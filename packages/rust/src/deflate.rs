//! A small pure-Rust DEFLATE (RFC 1951) / zlib (RFC 1950) compressor:
//! hash-chain LZ77 with optional one-step lazy matching, and per block the
//! cheapest of stored / fixed-Huffman / dynamic-Huffman coding.
//!
//! Output is valid DEFLATE but not byte-identical to miniz's `tdefl`.

const WINDOW: usize = 32768;
const HASH_BITS: u32 = 15;
const MIN_MATCH: usize = 3;
const MAX_MATCH: usize = 258;
const BLOCK_TOKENS: usize = 65536;

const LEN_BASE: [u16; 29] = [
    3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131,
    163, 195, 227, 258,
];
const LEN_EXTRA: [u8; 29] = [
    0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0,
];
const DIST_BASE: [u16; 30] = [
    1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537,
    2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577,
];
const DIST_EXTRA: [u8; 30] = [
    0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13,
    13,
];
const CL_ORDER: [usize; 19] = [
    16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15,
];

#[derive(Clone, Copy)]
enum Token {
    Lit(u8),
    Match { len: u16, dist: u16 },
}

fn len_code(len: usize) -> usize {
    LEN_BASE
        .iter()
        .rposition(|&b| b as usize <= len)
        .unwrap_or(0)
}

fn dist_code(dist: usize) -> usize {
    DIST_BASE
        .iter()
        .rposition(|&b| b as usize <= dist)
        .unwrap_or(0)
}

struct BitWriter {
    out: Vec<u8>,
    buf: u64,
    cnt: u32,
}

impl BitWriter {
    fn new() -> Self {
        Self {
            out: Vec::new(),
            buf: 0,
            cnt: 0,
        }
    }

    fn put(&mut self, bits: u32, n: u32) {
        if n == 0 {
            return;
        }
        self.buf |= u64::from(bits) << self.cnt;
        self.cnt += n;
        while self.cnt >= 8 {
            self.out.push(self.buf as u8);
            self.buf >>= 8;
            self.cnt -= 8;
        }
    }

    fn align(&mut self) {
        if self.cnt > 0 {
            self.out.push(self.buf as u8);
            self.buf = 0;
            self.cnt = 0;
        }
    }
}

fn reverse_bits(mut v: u32, n: u32) -> u32 {
    let mut r = 0;
    for _ in 0..n {
        r = (r << 1) | (v & 1);
        v >>= 1;
    }
    r
}

/// Length-limited Huffman code lengths. The result is always a complete code
/// over at least two symbols (a lone used symbol gets a length-1 partner), so
/// every decoder accepts it.
fn code_lengths(freqs: &[u32], max_len: usize) -> Vec<u8> {
    let mut lens = vec![0u8; freqs.len()];
    let mut syms: Vec<usize> = (0..freqs.len()).filter(|&s| freqs[s] > 0).collect();
    match syms.len() {
        0 => {
            lens[0] = 1;
            lens[1] = 1;
            return lens;
        }
        1 => {
            let s = syms[0];
            lens[s] = 1;
            lens[if s == 0 { 1 } else { 0 }] = 1;
            return lens;
        }
        _ => {}
    }
    syms.sort_by_key(|&s| (freqs[s], s));
    // Two-queue Huffman construction over the sorted leaves.
    let n = syms.len();
    let mut weight: Vec<u64> = syms.iter().map(|&s| u64::from(freqs[s])).collect();
    let mut parent = vec![usize::MAX; 2 * n - 1];
    let (mut leaf, mut node) = (0usize, n);
    weight.resize(2 * n - 1, 0);
    for next in n..2 * n - 1 {
        let mut pick = || {
            if leaf < n && (node >= next || weight[leaf] <= weight[node]) {
                leaf += 1;
                leaf - 1
            } else {
                node += 1;
                node - 1
            }
        };
        let a = pick();
        let b = pick();
        weight[next] = weight[a] + weight[b];
        parent[a] = next;
        parent[b] = next;
    }
    let mut depth = vec![0usize; 2 * n - 1];
    for i in (0..2 * n - 2).rev() {
        depth[i] = depth[parent[i]] + 1;
    }
    let mut num = vec![0u32; 64];
    for d in depth.iter().take(n) {
        num[(*d).min(63)] += 1;
    }
    // Enforce the maximum length (miniz's huffman_enforce_max_code_size).
    for i in max_len + 1..64 {
        num[max_len] += num[i];
        num[i] = 0;
    }
    let mut total: u64 = (1..=max_len)
        .map(|i| u64::from(num[i]) << (max_len - i))
        .sum();
    while total != 1u64 << max_len {
        num[max_len] -= 1;
        for i in (1..max_len).rev() {
            if num[i] > 0 {
                num[i] -= 1;
                num[i + 1] += 2;
                break;
            }
        }
        total -= 1;
    }
    // Rarest symbols get the longest codes.
    let mut it = syms.iter();
    for len in (1..=max_len).rev() {
        for _ in 0..num[len] {
            if let Some(&s) = it.next() {
                lens[s] = len as u8;
            }
        }
    }
    lens
}

fn canonical_codes(lens: &[u8]) -> Vec<u32> {
    let mut count = [0u32; 16];
    for &l in lens {
        count[l as usize] += 1;
    }
    count[0] = 0;
    let mut next = [0u32; 16];
    let mut code = 0u32;
    for len in 1..16 {
        code = (code + count[len - 1]) << 1;
        next[len] = code;
    }
    lens.iter()
        .map(|&l| {
            if l == 0 {
                0
            } else {
                let c = next[l as usize];
                next[l as usize] += 1;
                reverse_bits(c, u32::from(l))
            }
        })
        .collect()
}

/// Run-length encodes code lengths into (symbol, extra bits value) pairs.
fn rle_lengths(lens: &[u8]) -> Vec<(u8, u8)> {
    let mut out = Vec::new();
    let mut i = 0;
    while i < lens.len() {
        let v = lens[i];
        let mut run = 1;
        while i + run < lens.len() && lens[i + run] == v {
            run += 1;
        }
        i += run;
        if v == 0 {
            while run >= 11 {
                let r = run.min(138);
                out.push((18, (r - 11) as u8));
                run -= r;
            }
            if run >= 3 {
                out.push((17, (run - 3) as u8));
                run = 0;
            }
            out.extend(std::iter::repeat_n((0, 0), run));
        } else {
            out.push((v, 0));
            run -= 1;
            while run >= 3 {
                let r = run.min(6);
                out.push((16, (r - 3) as u8));
                run -= r;
            }
            out.extend(std::iter::repeat_n((v, 0), run));
        }
    }
    out
}

fn fixed_lengths() -> (Vec<u8>, Vec<u8>) {
    let mut l = vec![8u8; 288];
    l[144..256].fill(9);
    l[256..280].fill(7);
    (l, vec![5u8; 30])
}

fn write_tokens(
    w: &mut BitWriter,
    tokens: &[Token],
    lit: &[u8],
    lit_codes: &[u32],
    dist: &[u8],
    dist_codes: &[u32],
) {
    for t in tokens {
        match *t {
            Token::Lit(b) => w.put(lit_codes[b as usize], u32::from(lit[b as usize])),
            Token::Match { len, dist: d } => {
                let lc = len_code(len as usize);
                w.put(lit_codes[257 + lc], u32::from(lit[257 + lc]));
                w.put(u32::from(len - LEN_BASE[lc]), u32::from(LEN_EXTRA[lc]));
                let dc = dist_code(d as usize);
                w.put(dist_codes[dc], u32::from(dist[dc]));
                w.put(u32::from(d - DIST_BASE[dc]), u32::from(DIST_EXTRA[dc]));
            }
        }
    }
    w.put(lit_codes[256], u32::from(lit[256]));
}

fn emit_block(w: &mut BitWriter, tokens: &[Token], raw: &[u8], last: bool) {
    let mut lf = vec![0u32; 286];
    let mut df = vec![0u32; 30];
    lf[256] = 1;
    let mut extra_bits = 0u64;
    for t in tokens {
        match *t {
            Token::Lit(b) => lf[b as usize] += 1,
            Token::Match { len, dist } => {
                let lc = len_code(len as usize);
                let dc = dist_code(dist as usize);
                lf[257 + lc] += 1;
                df[dc] += 1;
                extra_bits += u64::from(LEN_EXTRA[lc]) + u64::from(DIST_EXTRA[dc]);
            }
        }
    }
    let cost = |lens: &[u8], freqs: &[u32]| -> u64 {
        freqs
            .iter()
            .zip(lens)
            .map(|(&f, &l)| u64::from(f) * u64::from(l))
            .sum()
    };

    let lit = code_lengths(&lf, 15);
    let dist = code_lengths(&df, 15);
    let hlit = (257..=286)
        .rev()
        .find(|&n| lit[n - 1] != 0)
        .unwrap_or(257)
        .max(257);
    let hdist = (1..=30)
        .rev()
        .find(|&n| dist[n - 1] != 0)
        .unwrap_or(1)
        .max(1);
    let mut all = lit[..hlit].to_vec();
    all.extend_from_slice(&dist[..hdist]);
    let rle = rle_lengths(&all);
    let mut cf = vec![0u32; 19];
    for &(s, _) in &rle {
        cf[s as usize] += 1;
    }
    let cl = code_lengths(&cf, 7);
    let hclen = (4..=19)
        .rev()
        .find(|&n| cl[CL_ORDER[n - 1]] != 0)
        .unwrap_or(4)
        .max(4);
    let rle_extra: u64 = rle
        .iter()
        .map(|&(s, _)| match s {
            16 => 2,
            17 => 3,
            18 => 7,
            _ => 0,
        })
        .sum();
    let dynamic_cost = 3
        + 14
        + 3 * hclen as u64
        + cost(&cl, &cf)
        + rle_extra
        + cost(&lit, &lf)
        + cost(&dist, &df)
        + extra_bits;
    let (flit, fdist) = fixed_lengths();
    let fixed_cost = 3 + cost(&flit[..286], &lf) + cost(&fdist, &df) + extra_bits;
    let stored_cost = (raw.len() as u64 + 5 * (raw.len() as u64 / 65535 + 1)) * 8 + 8;

    if stored_cost < dynamic_cost.min(fixed_cost) {
        let chunks: Vec<&[u8]> = if raw.is_empty() {
            vec![raw]
        } else {
            raw.chunks(65535).collect()
        };
        let n = chunks.len();
        for (i, c) in chunks.into_iter().enumerate() {
            w.put(u32::from(last && i + 1 == n), 1);
            w.put(0, 2);
            w.align();
            let len = c.len() as u16;
            w.put(u32::from(len), 16);
            w.put(u32::from(!len), 16);
            w.out.extend_from_slice(c);
        }
        return;
    }
    w.put(u32::from(last), 1);
    if fixed_cost <= dynamic_cost {
        w.put(1, 2);
        let fl = canonical_codes(&flit);
        let fd = canonical_codes(&fdist);
        write_tokens(w, tokens, &flit, &fl, &fdist, &fd);
    } else {
        w.put(2, 2);
        w.put((hlit - 257) as u32, 5);
        w.put((hdist - 1) as u32, 5);
        w.put((hclen - 4) as u32, 4);
        for &i in CL_ORDER.iter().take(hclen) {
            w.put(u32::from(cl[i]), 3);
        }
        let cc = canonical_codes(&cl);
        for &(s, x) in &rle {
            w.put(cc[s as usize], u32::from(cl[s as usize]));
            match s {
                16 => w.put(u32::from(x), 2),
                17 => w.put(u32::from(x), 3),
                18 => w.put(u32::from(x), 7),
                _ => {}
            }
        }
        let lc = canonical_codes(&lit);
        let dc = canonical_codes(&dist);
        write_tokens(w, tokens, &lit, &lc, &dist, &dc);
    }
}

/// Compresses `data` into a raw DEFLATE stream. `level` 0 stores; 1-10 trade
/// speed for ratio (longer hash chains, lazy matching from 4).
pub fn deflate_raw(data: &[u8], level: u32) -> Vec<u8> {
    let mut w = BitWriter::new();
    if level == 0 || data.is_empty() {
        emit_stored_only(&mut w, data);
        return w.out;
    }
    let max_chain = [0usize, 4, 8, 16, 32, 64, 128, 256, 1024, 4096, 4096][level.min(10) as usize];
    let lazy = level >= 4;
    let mut head = vec![u32::MAX; 1 << HASH_BITS];
    let mut prev = vec![u32::MAX; WINDOW];
    let hash = |p: usize| -> usize {
        ((u32::from(data[p]) << 10 ^ u32::from(data[p + 1]) << 5 ^ u32::from(data[p + 2]))
            & ((1 << HASH_BITS) - 1)) as usize
    };
    let insert = |p: usize, head: &mut Vec<u32>, prev: &mut Vec<u32>| {
        if p + MIN_MATCH <= data.len() {
            let h = hash(p);
            prev[p % WINDOW] = head[h];
            head[h] = p as u32;
        }
    };
    let longest = |p: usize, head: &Vec<u32>, prev: &Vec<u32>| -> (usize, usize) {
        if p + MIN_MATCH > data.len() {
            return (0, 0);
        }
        let max = (data.len() - p).min(MAX_MATCH);
        let mut best = (0usize, 0usize);
        let mut cand = head[hash(p)];
        let mut chain = 0;
        while cand != u32::MAX && chain < max_chain {
            let c = cand as usize;
            if c >= p || p - c > WINDOW {
                break;
            }
            let mut l = 0;
            while l < max && data[c + l] == data[p + l] {
                l += 1;
            }
            if l > best.0 {
                best = (l, p - c);
                if l == max {
                    break;
                }
            }
            let next = prev[c % WINDOW];
            if next == u32::MAX || next as usize >= c {
                break;
            }
            cand = next;
            chain += 1;
        }
        if best.0 >= MIN_MATCH {
            best
        } else {
            (0, 0)
        }
    };

    let mut tokens = Vec::with_capacity(BLOCK_TOKENS);
    let mut block_start = 0usize;
    let mut p = 0usize;
    while p < data.len() {
        let (mut len, mut dist) = longest(p, &head, &prev);
        if len > 0 && lazy && len < MAX_MATCH {
            insert(p, &mut head, &mut prev);
            let (l2, d2) = longest(p + 1, &head, &prev);
            if l2 > len {
                tokens.push(Token::Lit(data[p]));
                p += 1;
                len = l2;
                dist = d2;
            } else {
                // Already inserted p; insert the rest of the match below.
                for q in p + 1..p + len {
                    insert(q, &mut head, &mut prev);
                }
                tokens.push(Token::Match {
                    len: len as u16,
                    dist: dist as u16,
                });
                p += len;
                flush_if_full(&mut w, &mut tokens, data, &mut block_start, p);
                continue;
            }
        }
        if len > 0 {
            for q in p..p + len {
                insert(q, &mut head, &mut prev);
            }
            tokens.push(Token::Match {
                len: len as u16,
                dist: dist as u16,
            });
            p += len;
        } else {
            insert(p, &mut head, &mut prev);
            tokens.push(Token::Lit(data[p]));
            p += 1;
        }
        flush_if_full(&mut w, &mut tokens, data, &mut block_start, p);
    }
    emit_block(&mut w, &tokens, &data[block_start..], true);
    w.align();
    w.out
}

fn flush_if_full(
    w: &mut BitWriter,
    tokens: &mut Vec<Token>,
    data: &[u8],
    block_start: &mut usize,
    p: usize,
) {
    if tokens.len() >= BLOCK_TOKENS {
        emit_block(w, tokens, &data[*block_start..p], false);
        tokens.clear();
        *block_start = p;
    }
}

fn emit_stored_only(w: &mut BitWriter, data: &[u8]) {
    let chunks: Vec<&[u8]> = if data.is_empty() {
        vec![data]
    } else {
        data.chunks(65535).collect()
    };
    let n = chunks.len();
    for (i, c) in chunks.into_iter().enumerate() {
        w.put(u32::from(i + 1 == n), 1);
        w.put(0, 2);
        w.align();
        let len = c.len() as u16;
        w.put(u32::from(len), 16);
        w.put(u32::from(!len), 16);
        w.out.extend_from_slice(c);
    }
}

/// Compresses `data` into a zlib stream (2-byte header, DEFLATE, Adler-32).
pub fn zlib_compress(data: &[u8], level: u32) -> Vec<u8> {
    let flevel = match level {
        0 | 1 => 0u8,
        2..=5 => 1,
        6 => 2,
        _ => 3,
    };
    let cmf = 0x78u8;
    let mut flg = flevel << 6;
    flg += (31 - ((u16::from(cmf) * 256 + u16::from(flg)) % 31) as u8) % 31;
    let mut out = vec![cmf, flg];
    out.extend_from_slice(&deflate_raw(data, level));
    out.extend_from_slice(&crate::inflate::adler32(data).to_be_bytes());
    out
}
