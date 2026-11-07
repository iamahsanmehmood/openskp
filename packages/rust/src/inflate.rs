//! Pure-Rust DEFLATE (RFC 1951) and zlib (RFC 1950) decoder, replacing the
//! parts of miniz's `tinfl` / `mz_uncompress` the reader uses.
//!
//! Validation follows tinfl: a Huffman code must be complete unless it uses at
//! most one symbol, a length-repeat code may not start the code-length list,
//! a distance may not reach before the start of the output, and a stored
//! block's LEN must match ~NLEN. Output can be capped; exceeding the cap is an
//! error (tinfl's non-wrapping output buffer).

use std::fmt;

/// Why a stream failed to decode.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InflateError {
    /// The input ended before the stream did.
    Truncated,
    /// Block type 3.
    InvalidBlockType,
    /// A stored block's LEN is not the complement of NLEN.
    StoredLengthMismatch,
    /// Over-subscribed / incomplete Huffman code, or a bad repeat code.
    InvalidCodeLengths,
    /// A bit pattern or symbol that no valid code produces.
    InvalidSymbol,
    /// A back-reference before the start of the output.
    DistanceTooFar,
    /// The output would exceed the caller's cap.
    OutputLimit,
    /// Bad zlib CMF/FLG header (or a preset dictionary).
    BadZlibHeader,
    /// zlib Adler-32 trailer mismatch.
    Adler32Mismatch,
}

impl fmt::Display for InflateError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            InflateError::Truncated => "compressed stream is truncated",
            InflateError::InvalidBlockType => "invalid deflate block type",
            InflateError::StoredLengthMismatch => "stored block length mismatch",
            InflateError::InvalidCodeLengths => "invalid Huffman code lengths",
            InflateError::InvalidSymbol => "invalid Huffman symbol",
            InflateError::DistanceTooFar => "back-reference distance too far",
            InflateError::OutputLimit => "decompressed data exceeds the output buffer",
            InflateError::BadZlibHeader => "invalid zlib header",
            InflateError::Adler32Mismatch => "zlib Adler-32 checksum mismatch",
        })
    }
}

impl std::error::Error for InflateError {}

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

struct BitReader<'a> {
    data: &'a [u8],
    /// Next byte to load; may run past `data.len()` (virtual zero padding).
    pos: usize,
    buf: u64,
    cnt: u32,
}

impl<'a> BitReader<'a> {
    fn new(data: &'a [u8]) -> Self {
        Self {
            data,
            pos: 0,
            buf: 0,
            cnt: 0,
        }
    }

    #[inline]
    fn refill(&mut self) {
        if self.pos + 8 <= self.data.len() {
            let mut w = [0u8; 8];
            w.copy_from_slice(&self.data[self.pos..self.pos + 8]);
            self.buf |= u64::from_le_bytes(w) << self.cnt;
            let take = (63 - self.cnt) >> 3;
            self.pos += take as usize;
            self.cnt |= 56;
        } else {
            while self.cnt <= 56 {
                let b = self.data.get(self.pos).copied().unwrap_or(0);
                self.buf |= u64::from(b) << self.cnt;
                self.pos += 1;
                self.cnt += 8;
            }
        }
    }

    /// Bits consumed so far, counting from the start of `data`.
    #[inline]
    fn consumed(&self) -> usize {
        self.pos * 8 - self.cnt as usize
    }

    #[inline]
    fn check(&self) -> Result<(), InflateError> {
        if self.consumed() > self.data.len() * 8 {
            Err(InflateError::Truncated)
        } else {
            Ok(())
        }
    }

    #[inline]
    fn bits(&mut self, n: u32) -> Result<u32, InflateError> {
        if n == 0 {
            return Ok(0);
        }
        if self.cnt < n {
            self.refill();
        }
        let v = (self.buf & ((1u64 << n) - 1)) as u32;
        self.buf >>= n;
        self.cnt -= n;
        self.check()?;
        Ok(v)
    }

    fn align_byte(&mut self) {
        let drop = self.cnt % 8;
        self.buf >>= drop;
        self.cnt -= drop;
    }
}

/// Canonical Huffman decoding table indexed by `bits` (bit-reversed) code bits.
struct Huffman {
    /// `(symbol << 4) | length`; 0 = no code.
    table: Vec<u32>,
    bits: u32,
}

impl Huffman {
    fn new() -> Self {
        Self {
            table: Vec::new(),
            bits: 1,
        }
    }

    /// Builds the table, applying tinfl's rule: the code must be complete
    /// unless at most one symbol is used.
    fn build(&mut self, lengths: &[u8]) -> Result<(), InflateError> {
        let mut count = [0u32; 16];
        for &l in lengths {
            count[l as usize] += 1;
        }
        // tinfl's completeness test: the running total reaches 2^16 exactly
        // for a complete code.
        let mut used = 0u32;
        let mut total = 0u32;
        for &c in &count[1..=15] {
            used += c;
            total = (total + c) << 1;
        }
        if total != 65536 && used > 1 {
            return Err(InflateError::InvalidCodeLengths);
        }
        let max = (1..=15).rev().find(|&i| count[i] != 0).unwrap_or(1) as u32;
        self.bits = max;
        self.table.clear();
        self.table.resize(1usize << max, 0);
        // Canonical first code of each length (RFC 1951 3.2.2).
        let mut next = [0u32; 16];
        let mut code = 0u32;
        for len in 1..=15usize {
            let prev = if len == 1 { 0 } else { count[len - 1] };
            code = (code + prev) << 1;
            next[len] = code;
        }
        for (sym, &l) in lengths.iter().enumerate() {
            if l == 0 {
                continue;
            }
            let len = u32::from(l);
            let canonical = next[l as usize];
            next[l as usize] += 1;
            let rev = reverse_bits(canonical, len);
            let entry = ((sym as u32) << 4) | len;
            let step = 1usize << len;
            let mut i = rev as usize;
            while i < self.table.len() {
                self.table[i] = entry;
                i += step;
            }
        }
        Ok(())
    }

    #[inline]
    fn decode(&self, br: &mut BitReader) -> Result<u32, InflateError> {
        if br.cnt < 15 {
            br.refill();
        }
        let e = self.table[(br.buf & ((1u64 << self.bits) - 1)) as usize];
        let len = e & 15;
        if len == 0 {
            br.check()?;
            return Err(InflateError::InvalidSymbol);
        }
        br.buf >>= len;
        br.cnt -= len;
        br.check()?;
        Ok(e >> 4)
    }
}

#[inline]
fn reverse_bits(mut v: u32, n: u32) -> u32 {
    let mut r = 0;
    for _ in 0..n {
        r = (r << 1) | (v & 1);
        v >>= 1;
    }
    r
}

/// Decodes a raw DEFLATE stream from `br`, appending to `out`, which may not
/// grow past `limit` bytes.
fn inflate_stream(br: &mut BitReader, out: &mut Vec<u8>, limit: usize) -> Result<(), InflateError> {
    let mut lit = Huffman::new();
    let mut dist = Huffman::new();
    let mut cl = Huffman::new();
    let mut fixed_built = false;
    loop {
        let bfinal = br.bits(1)?;
        let btype = br.bits(2)?;
        match btype {
            0 => {
                br.align_byte();
                let len = br.bits(16)?;
                let nlen = br.bits(16)?;
                if len != (!nlen & 0xFFFF) {
                    return Err(InflateError::StoredLengthMismatch);
                }
                let mut remaining = len as usize;
                if out.len() + remaining > limit {
                    return Err(InflateError::OutputLimit);
                }
                while remaining > 0 && br.cnt >= 8 {
                    out.push((br.buf & 0xFF) as u8);
                    br.buf >>= 8;
                    br.cnt -= 8;
                    remaining -= 1;
                }
                br.check()?;
                if remaining > 0 {
                    // The bit buffer is empty here, so `pos` is exact. Drop the
                    // look-ahead bits refill() left above `cnt`: they belong to
                    // the bytes copied directly below.
                    br.buf = 0;
                    if br.pos + remaining > br.data.len() {
                        return Err(InflateError::Truncated);
                    }
                    out.extend_from_slice(&br.data[br.pos..br.pos + remaining]);
                    br.pos += remaining;
                }
            }
            1 | 2 => {
                if btype == 1 {
                    if !fixed_built {
                        let mut l = [0u8; 288];
                        l[..144].fill(8);
                        l[144..256].fill(9);
                        l[256..280].fill(7);
                        l[280..].fill(8);
                        lit.build(&l)?;
                        dist.build(&[5u8; 32])?;
                        fixed_built = true;
                    }
                } else {
                    fixed_built = false;
                    let hlit = br.bits(5)? as usize + 257;
                    let hdist = br.bits(5)? as usize + 1;
                    let hclen = br.bits(4)? as usize + 4;
                    let mut cl_lens = [0u8; 19];
                    for &idx in CL_ORDER.iter().take(hclen) {
                        cl_lens[idx] = br.bits(3)? as u8;
                    }
                    cl.build(&cl_lens)?;
                    let total = hlit + hdist;
                    let mut lens = [0u8; 288 + 32];
                    let mut n = 0usize;
                    while n < total {
                        let sym = cl.decode(br)?;
                        if sym < 16 {
                            lens[n] = sym as u8;
                            n += 1;
                            continue;
                        }
                        if sym == 16 && n == 0 {
                            return Err(InflateError::InvalidCodeLengths);
                        }
                        let (extra, base) = match sym {
                            16 => (2, 3),
                            17 => (3, 3),
                            _ => (7, 11),
                        };
                        let count = br.bits(extra)? as usize + base;
                        if n + count > total {
                            return Err(InflateError::InvalidCodeLengths);
                        }
                        let fill = if sym == 16 { lens[n - 1] } else { 0 };
                        lens[n..n + count].fill(fill);
                        n += count;
                    }
                    dist.build(&lens[hlit..total])?;
                    lit.build(&lens[..hlit])?;
                }
                loop {
                    let sym = lit.decode(br)?;
                    if sym < 256 {
                        if out.len() >= limit {
                            return Err(InflateError::OutputLimit);
                        }
                        out.push(sym as u8);
                        continue;
                    }
                    if sym == 256 {
                        break;
                    }
                    let li = (sym - 257) as usize;
                    if li >= 29 {
                        return Err(InflateError::InvalidSymbol);
                    }
                    let len = LEN_BASE[li] as usize + br.bits(u32::from(LEN_EXTRA[li]))? as usize;
                    let ds = dist.decode(br)? as usize;
                    if ds >= 30 {
                        return Err(InflateError::InvalidSymbol);
                    }
                    let d = DIST_BASE[ds] as usize + br.bits(u32::from(DIST_EXTRA[ds]))? as usize;
                    if d > out.len() {
                        return Err(InflateError::DistanceTooFar);
                    }
                    if out.len() + len > limit {
                        return Err(InflateError::OutputLimit);
                    }
                    let start = out.len() - d;
                    if d >= len {
                        out.extend_from_within(start..start + len);
                    } else if d == 1 {
                        let b = out[start];
                        out.resize(out.len() + len, b);
                    } else {
                        for i in 0..len {
                            let b = out[start + i];
                            out.push(b);
                        }
                    }
                }
            }
            _ => return Err(InflateError::InvalidBlockType),
        }
        if bfinal == 1 {
            return Ok(());
        }
    }
}

/// Decodes a raw DEFLATE stream (no zlib wrapper). Fails with
/// [`InflateError::OutputLimit`] when the output would exceed `limit` bytes.
pub fn inflate_raw(input: &[u8], limit: usize) -> Result<Vec<u8>, InflateError> {
    let mut out = Vec::with_capacity(limit.min(input.len().saturating_mul(4)).min(1 << 26));
    let mut br = BitReader::new(input);
    inflate_stream(&mut br, &mut out, limit)?;
    Ok(out)
}

/// Adler-32 checksum.
pub fn adler32(data: &[u8]) -> u32 {
    let (mut a, mut b) = (1u32, 0u32);
    for chunk in data.chunks(5552) {
        for &x in chunk {
            a += u32::from(x);
            b += a;
        }
        a %= 65521;
        b %= 65521;
    }
    (b << 16) | a
}

fn zlib_inner(input: &[u8], limit: usize) -> Result<Vec<u8>, InflateError> {
    if input.len() < 2 {
        return Err(InflateError::Truncated);
    }
    let (cmf, flg) = (input[0], input[1]);
    if !(u32::from(cmf) * 256 + u32::from(flg)).is_multiple_of(31) || flg & 32 != 0 || cmf & 15 != 8
    {
        return Err(InflateError::BadZlibHeader);
    }
    let mut out = Vec::with_capacity(limit.min(input.len().saturating_mul(4)).min(1 << 26));
    let mut br = BitReader::new(&input[2..]);
    inflate_stream(&mut br, &mut out, limit)?;
    br.align_byte();
    let mut stored = 0u32;
    for _ in 0..4 {
        stored = (stored << 8) | br.bits(8)?;
    }
    if stored != adler32(&out) {
        return Err(InflateError::Adler32Mismatch);
    }
    Ok(out)
}

/// Decodes a complete zlib stream (RFC 1950), verifying the Adler-32 trailer.
///
/// The output size is **not** limited: a small hostile stream can expand to
/// gigabytes. Use this only on input you trust (for example, data you just
/// compressed yourself); for untrusted input use [`zlib_decompress_capped`].
/// The library itself never calls this on file data.
pub fn zlib_decompress(input: &[u8]) -> Result<Vec<u8>, InflateError> {
    zlib_inner(input, usize::MAX)
}

/// Decodes a complete zlib stream (RFC 1950) up to `limit` uncompressed bytes,
/// verifying the Adler-32 trailer.
pub fn zlib_decompress_capped(input: &[u8], limit: usize) -> Result<Vec<u8>, InflateError> {
    zlib_inner(input, limit)
}

/// Result codes of [`uncompress`], mirroring `mz_uncompress`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UncompressError {
    /// `MZ_BUF_ERROR`: the destination capacity is too small.
    Buf,
    /// `MZ_DATA_ERROR`: the stream is malformed or truncated.
    Data,
}

impl fmt::Display for UncompressError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            UncompressError::Buf => f.write_str("buffer error (-5)"),
            UncompressError::Data => f.write_str("data error (-3)"),
        }
    }
}

impl std::error::Error for UncompressError {}

/// `mz_uncompress` equivalent: decodes a zlib stream into at most `dest_cap`
/// bytes. A too-small destination is [`UncompressError::Buf`] (callers grow
/// and retry); anything else that fails is [`UncompressError::Data`].
pub fn uncompress(src: &[u8], dest_cap: usize) -> Result<Vec<u8>, UncompressError> {
    match zlib_inner(src, dest_cap) {
        Ok(v) => Ok(v),
        Err(InflateError::OutputLimit) => Err(UncompressError::Buf),
        Err(_) => Err(UncompressError::Data),
    }
}
