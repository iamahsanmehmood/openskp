//! Bounds-checked little-endian readers, var-ints and the TLV walkers
//! (`tlv.cpp`).

use std::cell::RefCell;
use std::collections::BTreeMap;
use std::rc::Rc;

use super::{Error, Result, Tag, TlvNode};

fn require_range(d: &[u8], o: usize, n: usize) -> Result<()> {
    if o > d.len() || n > d.len() - o {
        return Err(Error::out_of_range("binary read exceeds buffer"));
    }
    Ok(())
}

pub fn read_u16(d: &[u8], o: usize) -> Result<u16> {
    require_range(d, o, 2)?;
    Ok(u16::from_le_bytes([d[o], d[o + 1]]))
}

pub fn read_u32(d: &[u8], o: usize) -> Result<u32> {
    require_range(d, o, 4)?;
    Ok(u32::from_le_bytes([d[o], d[o + 1], d[o + 2], d[o + 3]]))
}

pub fn read_i32(d: &[u8], o: usize) -> Result<i32> {
    Ok(read_u32(d, o)? as i32)
}

pub fn read_f64(d: &[u8], o: usize) -> Result<f64> {
    require_range(d, o, 8)?;
    let mut b = [0u8; 8];
    b.copy_from_slice(&d[o..o + 8]);
    Ok(f64::from_le_bytes(b))
}

/// Little-endian unsigned integer of `n` bytes (at most 8).
pub fn parse_varint(d: &[u8], o: usize, n: usize) -> Result<u64> {
    require_range(d, o, n)?;
    if n > 8 {
        return Err(Error::overflow("varint exceeds 64 bits"));
    }
    Ok(d[o..o + n]
        .iter()
        .enumerate()
        .fold(0u64, |v, (i, &b)| v | u64::from(b) << (8 * i)))
}

/// The tag of the two bytes at `o`, as upper-case hex in byte order.
pub fn tag_at(d: &[u8], o: usize) -> Result<Tag> {
    require_range(d, o, 2)?;
    Ok(Tag::from_bytes(d[o], d[o + 1]))
}

/// Infallible little-endian u32 for offsets already range-checked.
#[inline]
pub(crate) fn u32_at(d: &[u8], o: usize) -> u32 {
    u32::from_le_bytes([d[o], d[o + 1], d[o + 2], d[o + 3]])
}

// VFF (2021+) model.dat binary structure uses Type-Length-Value (TLV) records.
// Most TLV tags carry raw leaf binary/string payloads, but container tags
// contain nested sub-TLV nodes (definitions, drawing elements, component
// instances, ...). This lists every tag the TLV parser must recursively
// traverse.
fn is_container(b0: u8, b1: u8) -> bool {
    matches!(
        (b0, b1),
        (0xF4, 0x01)
            | (0xF7, 0x01)
            | (0xD4, 0x30)
            | (0xD5, 0x30)
            | (0xC8, 0x32)
            | (0x7C, 0x15)
            | (0x88, 0x13)
            | (0x89, 0x13)
            | (0x8A, 0x13)
            | (0x8B, 0x13)
            | (0x8C, 0x13)
            | (0x8D, 0x13)
            | (0x4C, 0x1D)
            | (0x64, 0x19)
            | (0xF9, 0x01)
            | (0x70, 0x17)
            | (0x71, 0x17)
            | (0xD0, 0x07)
            | (0xC4, 0x09)
            | (0x94, 0x11)
            | (0x95, 0x11)
            | (0x0F, 0x01)
            | (0x38, 0x4A)
            | (0xB8, 0x0B)
            | (0x97, 0x13)
            | (0x2C, 0x4C)
            | (0xAC, 0x0D)
            | (0xAE, 0x0D)
            | (0xF6, 0x01)
            | (0xF8, 0x01)
            | (0x98, 0x3A)
            | (0x99, 0x3A)
            | (0x8C, 0x3C)
            | (0x8D, 0x3C)
            | (0x90, 0x13)
            | (0x40, 0x1F)
            | (0x91, 0x13)
            | (0x92, 0x13)
            | (0x69, 0x42)
            | (0x6C, 0x42)
            | (0x68, 0x42)
    )
}

/// Maximum allowed nesting depth for recursive container TLV nodes.
pub const MAX_TLV_DEPTH: usize = 64;

const TLV_WRAP: u64 = 1u64 << 32;

/// Offset and payload size of a single TLV record header.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TlvHeader {
    pub offset: u64,
    pub size: u64,
}

#[derive(Clone, Copy)]
struct Run {
    offset: u64,
    size: u64,
    count: u64,
}

impl Run {
    fn step(self) -> u64 {
        self.size + 6
    }

    fn end(self) -> u64 {
        self.offset + self.count * self.step()
    }
}

#[derive(Default)]
struct Chain {
    runs: Vec<Run>,
    candidates: Vec<TlvHeader>,
    indexed: bool,
}

impl Chain {
    fn end(&self) -> u64 {
        self.runs.last().map_or(0, |r| r.end())
    }

    fn run_at(&self, p: u64) -> usize {
        self.runs.partition_point(|r| r.offset <= p) - 1
    }

    fn contains(&self, p: u64) -> bool {
        let Some(first) = self.runs.first() else {
            return false;
        };
        if p < first.offset || p >= self.end() {
            return false;
        }
        let r = self.runs[self.run_at(p)];
        (p - r.offset).is_multiple_of(r.step())
    }

    fn clip(&self, limit: u64) -> u64 {
        let end = self.end();
        if limit >= end {
            return end;
        }
        let r = self.runs[self.run_at(limit)];
        r.offset + (limit - r.offset) / r.step() * r.step()
    }

    fn append(&mut self, p: u64, size: u64) {
        let end = self.end();
        if let Some(last) = self.runs.last_mut() {
            if last.size == size && end == p {
                last.count += 1;
                return;
            }
        }
        self.runs.push(Run {
            offset: p,
            size,
            count: 1,
        });
    }
}

#[derive(Clone)]
struct View {
    chain: Rc<RefCell<Chain>>,
    begin: u64,
    end: u64,
}

#[derive(Clone)]
struct Plan {
    views: Vec<View>,
    stop: u64,
}

type PlanCache = BTreeMap<(u64, u64, usize), Option<Plan>>;

struct HeaderResolver<L, C> {
    length: L,
    container: C,
    limit: u64,
    attempts: usize,
    chains: Vec<Rc<RefCell<Chain>>>,
    decisions: PlanCache,
}

impl<L, C> HeaderResolver<L, C>
where
    L: FnMut(u64) -> u32,
    C: FnMut(u64) -> bool,
{
    fn new(length: L, container: C, end: u64) -> Self {
        Self {
            length,
            container,
            limit: end,
            attempts: 256,
            chains: Vec::new(),
            decisions: BTreeMap::new(),
        }
    }

    fn chain_at(chains: &[Rc<RefCell<Chain>>], p: u64) -> Option<Rc<RefCell<Chain>>> {
        chains
            .iter()
            .rev()
            .find(|c| c.borrow().contains(p))
            .cloned()
    }

    fn ordinary(&mut self, start: u64, end: u64) -> Plan {
        let mut views = Vec::new();
        let mut p = start;
        while end - p >= 6 {
            if let Some(chain) = Self::chain_at(&self.chains, p) {
                let (next, chain_end) = {
                    let borrowed = chain.borrow();
                    (borrowed.clip(end), borrowed.end())
                };
                if next > p {
                    views.push(View {
                        chain: Rc::clone(&chain),
                        begin: p,
                        end: next,
                    });
                }
                p = next;
                if p < chain_end {
                    return Plan { views, stop: p };
                }
                continue;
            }
            let mut chain = Chain::default();
            let begin = p;
            let mut probes = 0usize;
            let mut joined = false;
            while end - p >= 6 {
                if probes != 0 && (probes & 1023) == 0 && Self::chain_at(&self.chains, p).is_some()
                {
                    joined = true;
                    break;
                }
                let size = u64::from((self.length)(p));
                if size > end - p - 6 {
                    break;
                }
                chain.append(p, size);
                p += 6 + size;
                probes += 1;
            }
            if p > begin {
                let rc = Rc::new(RefCell::new(chain));
                self.chains.push(Rc::clone(&rc));
                views.push(View {
                    chain: rc,
                    begin,
                    end: p,
                });
            }
            if !joined {
                return Plan { views, stop: p };
            }
        }
        Plan { views, stop: p }
    }

    fn index(&mut self, chain: &mut Chain) {
        if chain.indexed {
            return;
        }
        chain.indexed = true;
        for r in &chain.runs {
            let step = r.step();
            for k in 0..r.count {
                let p = r.offset + k * step;
                if (self.limit - p) - step < TLV_WRAP {
                    return;
                }
                if (self.container)(p) {
                    chain.candidates.push(TlvHeader {
                        offset: p,
                        size: r.size,
                    });
                }
            }
        }
    }

    fn replace(original: &Plan, h: TlvHeader, size: u64, suffix: &Plan, end: u64) -> Plan {
        let mut views = Vec::new();
        for v in &original.views {
            if v.begin >= h.offset {
                break;
            }
            views.push(View {
                chain: Rc::clone(&v.chain),
                begin: v.begin,
                end: v.end.min(h.offset),
            });
            if v.end >= h.offset {
                break;
            }
        }
        let mut extended = Chain::default();
        extended.append(h.offset, size);
        views.push(View {
            chain: Rc::new(RefCell::new(extended)),
            begin: h.offset,
            end: h.offset + 6 + size,
        });
        views.extend(suffix.views.iter().cloned());
        Plan { views, stop: end }
    }

    fn solve(&mut self, start: u64, end: u64, depth: usize) -> Result<Option<Plan>> {
        let normal = self.ordinary(start, end);
        if normal.stop == end {
            return Ok(Some(normal));
        }
        let key = (start, end, depth);
        if let Some(cached) = self.decisions.get(&key) {
            return Ok(cached.clone());
        }
        for v in &normal.views {
            if !v.chain.borrow().indexed {
                self.index(&mut v.chain.borrow_mut());
            }
            let mut idx = v
                .chain
                .borrow()
                .candidates
                .partition_point(|h| h.offset < v.begin);
            loop {
                let h = {
                    let borrowed = v.chain.borrow();
                    match borrowed.candidates.get(idx) {
                        Some(&cand) if cand.offset < v.end => cand,
                        _ => break,
                    }
                };
                idx += 1;
                let available = end - h.offset - 6;
                if available - h.size < TLV_WRAP {
                    break;
                }
                let mut n = h.size + TLV_WRAP;
                while n <= available {
                    if depth >= MAX_TLV_DEPTH || self.attempts == 0 {
                        return Err(Error::depth_limit(
                            "TLV length recovery exceeded its depth or attempt limit",
                        ));
                    }
                    self.attempts -= 1;
                    let next = h.offset + 6 + n;
                    if let Some(suffix) = self.solve(next, end, depth + 1)? {
                        if self.solve(h.offset + 6, next, depth + 1)?.is_some() {
                            let result = Self::replace(&normal, h, n, &suffix, end);
                            self.decisions.insert(key, Some(result.clone()));
                            return Ok(Some(result));
                        }
                    }
                    if available - n < TLV_WRAP {
                        break;
                    }
                    n += TLV_WRAP;
                }
            }
        }
        self.decisions.insert(key, None);
        Ok(None)
    }

    fn materialize(plan: &Plan) -> Vec<TlvHeader> {
        let mut out = Vec::new();
        for v in &plan.views {
            let borrowed = v.chain.borrow();
            let mut i = borrowed.run_at(v.begin);
            while i < borrowed.runs.len() && borrowed.runs[i].offset < v.end {
                let r = borrowed.runs[i];
                i += 1;
                let step = r.step();
                let stop = v.end.min(r.end());
                let k0 = if v.begin <= r.offset {
                    0
                } else {
                    (v.begin - r.offset) / step
                };
                let k1 = if stop <= r.offset {
                    0
                } else {
                    (stop - r.offset) / step
                };
                for k in k0..k1 {
                    out.push(TlvHeader {
                        offset: r.offset + k * step,
                        size: r.size,
                    });
                }
            }
        }
        out
    }

    fn headers(&mut self, start: u64, end: u64) -> Result<Vec<TlvHeader>> {
        if let Some(plan) = self.solve(start, end, 0)? {
            return Ok(Self::materialize(&plan));
        }
        let fallback = self.ordinary(start, end);
        Ok(Self::materialize(&fallback))
    }
}

struct ModelScan<T, L> {
    end: u64,
    stored_root_end: u64,
    stored_parent_end: u64,
    contradicts_stored_lengths: bool,
    pos: u64,
    tag: T,
    length: L,
}

impl<T, L> ModelScan<T, L>
where
    T: FnMut(u64) -> u16,
    L: FnMut(u64) -> u32,
{
    fn crosses(pos: u64, size: u64, limit: u64) -> bool {
        pos <= limit && limit - pos >= 6 && size > limit - pos - 6
    }

    fn checked_length(&mut self) -> u64 {
        let size = u64::from((self.length)(self.pos));
        if Self::crosses(self.pos, size, self.stored_root_end)
            || Self::crosses(self.pos, size, self.stored_parent_end)
        {
            self.contradicts_stored_lengths = true;
        }
        size
    }

    fn has(&mut self, expected: u16) -> bool {
        self.end - self.pos >= 6 && (self.tag)(self.pos) == expected
    }

    fn advance(&mut self) -> bool {
        if self.end - self.pos < 6 {
            return false;
        }
        let size = self.checked_length();
        if size > self.end - self.pos - 6 {
            return false;
        }
        self.pos += 6 + size;
        true
    }

    fn append_range(&mut self, records: &mut Vec<TlvHeader>, begin: u64, stop: u64) {
        let mut p = begin;
        while p < stop {
            let size = u64::from((self.length)(p));
            records.push(TlvHeader { offset: p, size });
            p += 6 + size;
        }
    }
}

/// Disambiguates a `>= 4 GiB` `model.dat` whose outer `F401` 32-bit length
/// wrapped modulo `2^32`.
pub fn wrapped_model_records<T, L>(end: u64, mut tag: T, mut length: L) -> Option<Vec<TlvHeader>>
where
    T: FnMut(u64) -> u16,
    L: FnMut(u64) -> u32,
{
    if end < 6 || end - 6 < TLV_WRAP || tag(0) != 0x01f4 || length(0) != (end - 6) as u32 {
        return None;
    }
    let stored_root_end = 6 + u64::from(length(0));
    let mut scan = ModelScan {
        end,
        stored_root_end,
        stored_parent_end: stored_root_end,
        contradicts_stored_lengths: false,
        pos: 6,
        tag,
        length,
    };
    while !scan.has(0x01f9) {
        if !scan.advance() {
            return None;
        }
    }
    let catalog = scan.pos;
    let mut wrappers = [0u64; 3];
    for (slot, expected) in wrappers.iter_mut().zip([0x01f9u16, 0x1770, 0x1771]) {
        if !scan.has(expected) {
            return None;
        }
        *slot = scan.pos;
        let size = scan.checked_length();
        if scan.pos <= scan.stored_parent_end
            && scan.stored_parent_end - scan.pos >= 6
            && size <= scan.stored_parent_end - scan.pos - 6
        {
            scan.stored_parent_end = scan.pos + 6 + size;
        }
        scan.pos += 6;
        if scan.has(0x0063) && !scan.advance() {
            return None;
        }
    }
    let definitions = scan.pos;
    while scan.has(0x157c) {
        if !scan.advance() {
            return None;
        }
    }
    let entities = scan.pos;
    if definitions == entities || !scan.has(0x01f6) {
        return None;
    }
    for offset in wrappers {
        if (scan.length)(offset) != (entities - offset - 6) as u32 {
            return None;
        }
    }
    scan.stored_parent_end = scan.stored_root_end;
    while scan.pos < end {
        if !scan.advance() {
            return None;
        }
    }
    if !scan.contradicts_stored_lengths {
        return None;
    }
    let mut records = Vec::new();
    scan.append_range(&mut records, 6, catalog);
    scan.append_range(&mut records, definitions, entities);
    scan.append_range(&mut records, entities, end);
    Some(records)
}

/// Scans `[start, end)` for direct-child TLV headers, recovering 32-bit
/// container length wraps across `>= 4 GiB` ranges.
pub fn tlv_headers<L, C>(
    start: u64,
    end: u64,
    mut length: L,
    container: C,
) -> Result<Vec<TlvHeader>>
where
    L: FnMut(u64) -> u32,
    C: FnMut(u64) -> bool,
{
    if start > end {
        return Err(Error::out_of_range("invalid TLV range"));
    }
    if end - start >= TLV_WRAP {
        return HeaderResolver::new(length, container, end).headers(start, end);
    }
    let mut out = Vec::new();
    let mut p = start;
    while end - p >= 6 {
        let n = u64::from(length(p));
        if n > end - p - 6 {
            break;
        }
        out.push(TlvHeader { offset: p, size: n });
        p += 6 + n;
    }
    Ok(out)
}

/// Scans `d[start..end]` for direct-child TLV headers.
pub fn tlv_headers_slice(d: &[u8], start: usize, end: usize) -> Result<Vec<TlvHeader>> {
    if end > d.len() || start > end {
        return Err(Error::out_of_range("invalid TLV range"));
    }
    tlv_headers(
        start as u64,
        end as u64,
        |p| u32_at(d, p as usize + 2),
        |p| is_container(d[p as usize], d[p as usize + 1]),
    )
}

/// Parses `d[start..end]` into TLV nodes, recursing into container tags.
pub fn parse_tlv_recursive(d: &[u8], start: usize, end: usize) -> Result<Vec<TlvNode>> {
    parse_tlv_recursive_depth(d, start, end, 0)
}

fn parse_tlv_recursive_depth(
    d: &[u8],
    start: usize,
    end: usize,
    depth: usize,
) -> Result<Vec<TlvNode>> {
    if depth > MAX_TLV_DEPTH {
        return Err(Error::out_of_range(
            "TLV nesting depth exceeds maximum allowed limit (64)",
        ));
    }
    let headers = tlv_headers_slice(d, start, end)?;
    let mut out = Vec::with_capacity(headers.len());
    for header in headers {
        let p = header.offset as usize;
        let size = header.size as usize;
        let (b0, b1) = (d[p], d[p + 1]);
        let mut n = TlvNode {
            offset: header.offset,
            size: header.size,
            tag: Tag::from_bytes(b0, b1),
            ..Default::default()
        };
        if size != 0 && is_container(b0, b1) {
            n.children = parse_tlv_recursive_depth(d, p + 6, p + 6 + size, depth + 1)?;
        }
        if n.children.is_empty() && size != 0 {
            n.payload = d[p + 6..p + 6 + size].to_vec();
        }
        out.push(n);
    }
    Ok(out)
}

/// One flat level of TLV records over the whole buffer, stopping at the first
/// record that would run past the end. Payloads borrow from `d`.
pub fn parse_flat(d: &[u8]) -> Vec<(Tag, &[u8])> {
    let mut out = Vec::new();
    let mut p = 0usize;
    while d.len() - p.min(d.len()) >= 6 {
        let n = u32_at(d, p + 2) as usize;
        if n > d.len() - p - 6 {
            break;
        }
        out.push((Tag::from_bytes(d[p], d[p + 1]), &d[p + 6..p + 6 + n]));
        p += 6 + n;
    }
    out
}
