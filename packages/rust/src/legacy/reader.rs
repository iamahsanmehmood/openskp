//! The bounds-checked cursor the legacy archive reads through (`legacy.cpp`'s
//! `struct R`).

use crate::internal::{read_f64, read_i32, read_u16, read_u32, Error, Result};
use crate::model::ParsedAttribute;

/// A read cursor over the whole file: every read is bounds-checked and fails
/// with `out_of_range` ("legacy archive truncated") past the end.
#[derive(Debug, Clone, Copy)]
pub(crate) struct R<'a> {
    pub d: &'a [u8],
    pub p: usize,
}

impl<'a> R<'a> {
    pub fn new(d: &'a [u8], p: usize) -> Self {
        R { d, p }
    }

    pub fn need(&self, n: usize) -> Result<()> {
        if self.p > self.d.len() || n > self.d.len() - self.p {
            return Err(Error::out_of_range("legacy archive truncated"));
        }
        Ok(())
    }

    pub fn u8(&mut self) -> Result<u8> {
        self.need(1)?;
        let v = self.d[self.p];
        self.p += 1;
        Ok(v)
    }

    pub fn u16(&mut self) -> Result<u16> {
        let v = read_u16(self.d, self.p)?;
        self.p += 2;
        Ok(v)
    }

    pub fn u32(&mut self) -> Result<u32> {
        let v = read_u32(self.d, self.p)?;
        self.p += 4;
        Ok(v)
    }

    pub fn f64(&mut self) -> Result<f64> {
        let v = read_f64(self.d, self.p)?;
        self.p += 8;
        Ok(v)
    }

    pub fn f64s(&mut self, n: usize) -> Result<Vec<f64>> {
        let mut v = Vec::with_capacity(n);
        for _ in 0..n {
            v.push(self.f64()?);
        }
        Ok(v)
    }

    /// Three doubles.
    pub fn vec3(&mut self) -> Result<[f64; 3]> {
        Ok([self.f64()?, self.f64()?, self.f64()?])
    }

    pub fn raw(&mut self, n: usize) -> Result<&'a [u8]> {
        self.need(n)?;
        let v = &self.d[self.p..self.p + n];
        self.p += n;
        Ok(v)
    }

    pub fn skip(&mut self, n: usize) -> Result<()> {
        self.raw(n).map(|_| ())
    }

    /// True when the string-record marker `FF FE FF` starts at the cursor.
    pub fn marker(&self) -> bool {
        self.p + 3 <= self.d.len()
            && self.d[self.p] == 255
            && self.d[self.p + 1] == 254
            && self.d[self.p + 2] == 255
    }

    pub fn peek_u16(&self) -> Result<u16> {
        self.need(2)?;
        read_u16(self.d, self.p)
    }

    /// One MFC `CString` record: `FF FE FF`, a u8 length (escaped to u16 /
    /// u32), then that many UTF-16LE code units.
    pub fn utf16(&mut self) -> Result<String> {
        if !self.marker() {
            return Err(Error::runtime("expected legacy string record"));
        }
        self.p += 3;
        let n = self.u8()?;
        let mut z = u32::from(n);
        if n == 255 {
            z = u32::from(self.u16()?);
            if z == 65535 {
                z = self.u32()?;
            }
        }
        let bytes = (z as usize)
            .checked_mul(2)
            .ok_or_else(|| Error::out_of_range("legacy archive truncated"))?;
        let units = self.raw(bytes)?;
        Ok(decode_utf16le(units))
    }

    /// One typed `CAttributeNamed` value (`Archive::typed`). Every SketchUp
    /// value type keeps its own kind here (the Python reader's
    /// `read_typed`): `Time` (9), `Length` (12), `Point3d` (17), `Vector3d`
    /// (18), nested `Array` (11).
    pub fn attr_value(&mut self, t: u8) -> Result<ParsedAttribute> {
        self.attr_value_depth(t, 0)
    }

    fn attr_value_depth(&mut self, t: u8, depth: usize) -> Result<ParsedAttribute> {
        const MAX_ATTR_DEPTH: usize = 32;
        if depth > MAX_ATTR_DEPTH {
            return Err(Error::runtime(
                "legacy attribute nesting exceeds maximum depth",
            ));
        }
        Ok(match t {
            0 => ParsedAttribute::Null,
            4 => ParsedAttribute::Integer(i64::from(read_i32(self.raw(4)?, 0)?)),
            6 => ParsedAttribute::Float(self.f64()?),
            7 => ParsedAttribute::Boolean(self.u8()? != 0),
            9 => ParsedAttribute::Time(i64::from(self.u32()?)), // time_t
            10 => ParsedAttribute::String(self.utf16()?),
            12 => ParsedAttribute::Length(self.f64()?), // Length (a double, inches)
            11 => {
                let mut n = self.u32()?;
                if n > 100000 {
                    return Err(Error::runtime("attr array too large"));
                }
                if (n as usize) > self.d.len().saturating_sub(self.p) {
                    return Err(Error::out_of_range("legacy archive truncated"));
                }
                let mut items = Vec::with_capacity((n as usize).min(1024));
                while n > 0 {
                    n -= 1;
                    let tag = self.u8()?;
                    items.push(self.attr_value_depth(tag, depth + 1)?);
                }
                ParsedAttribute::Array(items)
            }
            17 => ParsedAttribute::Point3d(self.vec3()?), // Geom::Point3d
            18 => ParsedAttribute::Vector3d(self.vec3()?), // Geom::Vector3d
            _ => return Err(Error::runtime("unknown legacy attribute type")),
        })
    }
}

/// UTF-16LE code units to UTF-8. The C++ converts unit by unit (no surrogate
/// pairing); a Rust `String` must be valid UTF-8, so surrogate pairs are
/// combined and a lone surrogate becomes U+FFFD. BMP text is identical.
pub(crate) fn decode_utf16le(units: &[u8]) -> String {
    char::decode_utf16(
        units
            .as_chunks::<2>()
            .0
            .iter()
            .map(|&c| u16::from_le_bytes(c)),
    )
    .map(|c| c.unwrap_or(char::REPLACEMENT_CHARACTER))
    .collect()
}

/// The major version from the `{N.` in the header's first 96 bytes (NULs
/// dropped), as the C++ `std::regex("\\{(\\d+)\\.")` + `std::stoi` read it:
/// `None` when there is no such text, `Some(Err)` when the digits overflow.
pub(crate) fn header_major(data: &[u8]) -> Option<std::result::Result<i32, ()>> {
    let ascii: Vec<u8> = data.iter().take(96).copied().filter(|&b| b != 0).collect();
    for (i, &c) in ascii.iter().enumerate() {
        if c != b'{' {
            continue;
        }
        let digits = ascii[i + 1..]
            .iter()
            .take_while(|b| b.is_ascii_digit())
            .count();
        if digits > 0 && ascii.get(i + 1 + digits) == Some(&b'.') {
            let text = std::str::from_utf8(&ascii[i + 1..i + 1 + digits]).unwrap_or("");
            return Some(text.parse::<i32>().map_err(|_| ()));
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn attr_value_recursion_and_prealloc_bomb_are_rejected() {
        // 1. Truncated array claiming 100,000 elements in 4 bytes must fail before allocating
        let truncated_array = 100_000u32.to_le_bytes();
        let mut r = R::new(&truncated_array, 0);
        assert!(r.attr_value(11).is_err());

        // 2. Deeply nested attribute arrays (100 levels of 1-element arrays, 5 bytes each)
        let levels = 100usize;
        let mut deep_buf = Vec::with_capacity(levels * 5 + 1);
        for _ in 0..levels {
            deep_buf.extend_from_slice(&1u32.to_le_bytes());
            deep_buf.push(11);
        }
        deep_buf.push(0);
        let mut r_deep = R::new(&deep_buf, 0);
        assert!(r_deep.attr_value(11).is_err());
    }
}
