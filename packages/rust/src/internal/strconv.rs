//! C library string-to-number conversions with the C++ `std::stoi` /
//! `std::stoll` / `std::stod` semantics the reader relies on: leading
//! whitespace is skipped, the longest valid prefix is converted, trailing text
//! is ignored, and "no conversion" or "out of range" is `None` (where the C++
//! would throw).

fn is_c_space(c: u8) -> bool {
    matches!(c, b' ' | b'\t' | b'\n' | 0x0B | 0x0C | b'\r')
}

/// Optional sign and decimal digits after leading whitespace; `None` when no
/// digit follows. Returns (negative, digits).
fn integer_prefix(s: &[u8]) -> Option<(bool, &[u8])> {
    let mut i = 0;
    while i < s.len() && is_c_space(s[i]) {
        i += 1;
    }
    let mut neg = false;
    if i < s.len() && (s[i] == b'+' || s[i] == b'-') {
        neg = s[i] == b'-';
        i += 1;
    }
    let start = i;
    while i < s.len() && s[i].is_ascii_digit() {
        i += 1;
    }
    if i == start {
        None
    } else {
        Some((neg, &s[start..i]))
    }
}

fn to_i128(neg: bool, digits: &[u8]) -> Option<i128> {
    let mut v: i128 = 0;
    for &d in digits {
        v = v.checked_mul(10)?.checked_add(i128::from(d - b'0'))?;
        if v > i128::from(u64::MAX) * 2 {
            return None;
        }
    }
    Some(if neg { -v } else { v })
}

/// `std::stoll`.
pub fn c_stoll(s: &str) -> Option<i64> {
    let (neg, digits) = integer_prefix(s.as_bytes())?;
    i64::try_from(to_i128(neg, digits)?).ok()
}

/// `std::stoi`.
pub fn c_stoi(s: &str) -> Option<i32> {
    let (neg, digits) = integer_prefix(s.as_bytes())?;
    i32::try_from(to_i128(neg, digits)?).ok()
}

/// `std::stoul` on a digit string in `base` (10 or 16), with a 64-bit
/// `unsigned long`.
pub fn c_stoul_digits(digits: &[u8], base: u32) -> Option<u64> {
    let mut v: u64 = 0;
    if digits.is_empty() {
        return None;
    }
    for &d in digits {
        let x = (d as char).to_digit(base)?;
        v = v.checked_mul(u64::from(base))?.checked_add(u64::from(x))?;
    }
    Some(v)
}

/// `std::stod`: decimal, hexadecimal (`0x1.8p3`), `inf`/`infinity` and `nan`
/// prefixes; overflow and underflow (a non-zero value that rounds to zero or
/// to a subnormal) are range errors.
pub fn c_stod(s: &str) -> Option<f64> {
    let b = s.as_bytes();
    let mut i = 0;
    while i < b.len() && is_c_space(b[i]) {
        i += 1;
    }
    let mut neg = false;
    if i < b.len() && (b[i] == b'+' || b[i] == b'-') {
        neg = b[i] == b'-';
        i += 1;
    }
    let rest = &b[i..];
    let starts_ci = |p: &[u8], w: &[u8]| p.len() >= w.len() && p[..w.len()].eq_ignore_ascii_case(w);
    let sign = if neg { -1.0 } else { 1.0 };
    if starts_ci(rest, b"inf") {
        return Some(sign * f64::INFINITY);
    }
    if starts_ci(rest, b"nan") {
        return Some(if neg { -f64::NAN } else { f64::NAN });
    }
    if starts_ci(rest, b"0x") {
        if let Some(v) = hex_float(&rest[2..]) {
            return range_checked(sign * v.0, v.1);
        }
        // "0x" with no hex digits converts the "0".
        return Some(sign * 0.0);
    }
    // Decimal: digits [. digits] [e [sign] digits], at least one mantissa digit.
    let mut j = 0;
    let mut nonzero = false;
    let mut mant_digits = 0;
    while j < rest.len() && rest[j].is_ascii_digit() {
        nonzero |= rest[j] != b'0';
        j += 1;
        mant_digits += 1;
    }
    if j < rest.len() && rest[j] == b'.' {
        j += 1;
        while j < rest.len() && rest[j].is_ascii_digit() {
            nonzero |= rest[j] != b'0';
            j += 1;
            mant_digits += 1;
        }
    }
    if mant_digits == 0 {
        return None;
    }
    if j < rest.len() && (rest[j] == b'e' || rest[j] == b'E') {
        let mut k = j + 1;
        if k < rest.len() && (rest[k] == b'+' || rest[k] == b'-') {
            k += 1;
        }
        if k < rest.len() && rest[k].is_ascii_digit() {
            while k < rest.len() && rest[k].is_ascii_digit() {
                k += 1;
            }
            j = k;
        }
    }
    let text = std::str::from_utf8(&rest[..j]).ok()?;
    let v: f64 = text.parse().ok()?;
    range_checked(sign * v, nonzero)
}

fn range_checked(v: f64, nonzero: bool) -> Option<f64> {
    if v.is_infinite() || (nonzero && (v == 0.0 || v.is_subnormal())) {
        None
    } else {
        Some(v)
    }
}

/// Hex-float body after "0x": (value, any non-zero digit).
fn hex_float(b: &[u8]) -> Option<(f64, bool)> {
    let mut mant: u64 = 0;
    let mut exp: i64 = 0;
    let mut digits = 0;
    let mut nonzero = false;
    let mut j = 0;
    let push = |d: u32, frac: bool, mant: &mut u64, exp: &mut i64| {
        if *mant >> 60 == 0 {
            *mant = (*mant << 4) | u64::from(d);
            if frac {
                *exp -= 4;
            }
        } else if !frac {
            *exp += 4;
        }
    };
    while j < b.len() && b[j].is_ascii_hexdigit() {
        let d = (b[j] as char).to_digit(16).unwrap_or(0);
        nonzero |= d != 0;
        push(d, false, &mut mant, &mut exp);
        j += 1;
        digits += 1;
    }
    if j < b.len() && b[j] == b'.' {
        j += 1;
        while j < b.len() && b[j].is_ascii_hexdigit() {
            let d = (b[j] as char).to_digit(16).unwrap_or(0);
            nonzero |= d != 0;
            push(d, true, &mut mant, &mut exp);
            j += 1;
            digits += 1;
        }
    }
    if digits == 0 {
        return None;
    }
    if j < b.len() && (b[j] == b'p' || b[j] == b'P') {
        let mut k = j + 1;
        let mut eneg = false;
        if k < b.len() && (b[k] == b'+' || b[k] == b'-') {
            eneg = b[k] == b'-';
            k += 1;
        }
        if k < b.len() && b[k].is_ascii_digit() {
            let mut e: i64 = 0;
            while k < b.len() && b[k].is_ascii_digit() {
                e = (e * 10 + i64::from(b[k] - b'0')).min(1 << 20);
                k += 1;
            }
            exp += if eneg { -e } else { e };
        }
    }
    let v = (mant as f64) * 2f64.powi(exp.clamp(-1 << 20, 1 << 20) as i32);
    Some((v, nonzero))
}
