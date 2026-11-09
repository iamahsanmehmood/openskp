//! Bounds-checked little-endian readers, var-ints and the TLV walkers
//! (`tlv.cpp`).

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
    if end > d.len() || start > end {
        return Err(Error::out_of_range("invalid TLV range"));
    }
    let mut out = Vec::new();
    let mut p = start;
    while p <= end && end - p >= 6 {
        let (b0, b1) = (d[p], d[p + 1]);
        let size = u32_at(d, p + 2) as usize;
        if size > end - p - 6 {
            break;
        }
        let mut n = TlvNode {
            offset: p as u64,
            size: size as u64,
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
        p += 6 + size;
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
