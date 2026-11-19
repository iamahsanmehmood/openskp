//! A tiny JSON serializer shared by the JSON export and the GLB writers.
//!
//! Two flavours, each byte-compatible with what the C++ port produced:
//!
//! - [`Flavor::Canonical`]: `json_export.cpp`'s hand-rolled writer - keys in
//!   insertion order, doubles as `%.17g`, optional pretty-printing.
//! - [`Flavor::Gltf`]: TinyGLTF's nlohmann::json `dump()` - compact, object
//!   keys sorted bytewise (nlohmann's default `std::map`), doubles as the
//!   shortest round-trip form with nlohmann's `.0` / exponent rules.

use crate::json_export::JsonValue;

/// Which serializer to imitate.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Flavor {
    /// `to_json_string()`; `indent <= 0` is compact.
    Canonical { indent: i32 },
    /// TinyGLTF / nlohmann compact dump.
    Gltf,
}

/// Serializes `value` in the given flavour.
pub(crate) fn write(value: &JsonValue, flavor: Flavor) -> String {
    let mut out = String::new();
    write_value(&mut out, value, flavor, 0);
    out
}

fn indent_of(flavor: Flavor) -> usize {
    match flavor {
        Flavor::Canonical { indent } if indent > 0 => indent as usize,
        _ => 0,
    }
}

fn newline_indent(out: &mut String, flavor: Flavor, depth: usize) {
    let indent = indent_of(flavor);
    if indent == 0 {
        return;
    }
    out.push('\n');
    out.extend(std::iter::repeat_n(' ', indent * depth));
}

fn write_value(out: &mut String, value: &JsonValue, flavor: Flavor, depth: usize) {
    match value {
        JsonValue::Null => out.push_str("null"),
        JsonValue::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        JsonValue::Int(i) => out.push_str(&i.to_string()),
        JsonValue::Double(d) => match flavor {
            Flavor::Canonical { .. } => out.push_str(&format_g17(*d)),
            Flavor::Gltf => out.push_str(&format_shortest(*d)),
        },
        JsonValue::String(s) => write_string(out, s, flavor),
        JsonValue::Array(items) => {
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                newline_indent(out, flavor, depth + 1);
                write_value(out, item, flavor, depth + 1);
            }
            if !items.is_empty() {
                newline_indent(out, flavor, depth);
            }
            out.push(']');
        }
        JsonValue::Object(entries) => {
            let mut ordered: Vec<&(String, JsonValue)> = entries.iter().collect();
            if flavor == Flavor::Gltf {
                ordered.sort_by(|a, b| a.0.as_bytes().cmp(b.0.as_bytes()));
            }
            out.push('{');
            for (i, (key, val)) in ordered.iter().map(|e| (&e.0, &e.1)).enumerate() {
                if i > 0 {
                    out.push(',');
                }
                newline_indent(out, flavor, depth + 1);
                write_string(out, key, flavor);
                out.push(':');
                if indent_of(flavor) > 0 {
                    out.push(' ');
                }
                write_value(out, val, flavor, depth + 1);
            }
            if !ordered.is_empty() {
                newline_indent(out, flavor, depth);
            }
            out.push('}');
        }
    }
}

fn write_string(out: &mut String, s: &str, flavor: Flavor) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            // nlohmann has short forms for these two; the canonical writer
            // falls through to \u00XX like every other control character.
            '\u{08}' if flavor == Flavor::Gltf => out.push_str("\\b"),
            '\u{0c}' if flavor == Flavor::Gltf => out.push_str("\\f"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
}

/// Splits a finite, non-zero `|v|` into its shortest round-trip decimal
/// digits and the exponent of the first digit (`d.ddd x 10^exp`).
fn shortest_digits(v: f64) -> (String, i32) {
    let s = format!("{:e}", v.abs());
    let (mantissa, exp) = s.split_once('e').expect("{:e} always has an exponent");
    (
        mantissa.replace('.', ""),
        exp.parse().expect("valid exponent"),
    )
}

/// nlohmann::json's double formatting (`dtoa_impl::format_buffer` with
/// min_exp -4 and max_exp 15).
pub(crate) fn format_shortest(v: f64) -> String {
    if v.is_nan() || v.is_infinite() {
        // nlohmann writes null for non-finite numbers.
        return "null".into();
    }
    let mut out = String::new();
    if v.is_sign_negative() {
        out.push('-');
    }
    if v == 0.0 {
        out.push_str("0.0");
        return out;
    }
    let (digits, exp) = shortest_digits(v);
    let k = digits.len() as i32;
    let n = exp + 1;
    if k <= n && n <= 15 {
        out.push_str(&digits);
        out.extend(std::iter::repeat_n('0', (n - k) as usize));
        out.push_str(".0");
    } else if 0 < n && n <= 15 {
        out.push_str(&digits[..n as usize]);
        out.push('.');
        out.push_str(&digits[n as usize..]);
    } else if -4 < n && n <= 0 {
        out.push_str("0.");
        out.extend(std::iter::repeat_n('0', (-n) as usize));
        out.push_str(&digits);
    } else {
        out.push_str(&digits[..1]);
        if k > 1 {
            out.push('.');
            out.push_str(&digits[1..]);
        }
        out.push('e');
        let e = n - 1;
        out.push(if e < 0 { '-' } else { '+' });
        let a = e.unsigned_abs();
        if a < 10 {
            out.push('0');
        }
        out.push_str(&a.to_string());
    }
    out
}

/// C's `printf("%.17g", v)`.
pub(crate) fn format_g17(v: f64) -> String {
    const P: i32 = 17;
    if v.is_nan() {
        return if v.is_sign_negative() {
            "-nan".into()
        } else {
            "nan".into()
        };
    }
    if v.is_infinite() {
        return if v < 0.0 { "-inf".into() } else { "inf".into() };
    }
    if v == 0.0 {
        return if v.is_sign_negative() {
            "-0".into()
        } else {
            "0".into()
        };
    }
    let e_form = format!("{:.*e}", (P - 1) as usize, v);
    let (mantissa, exp) = e_form.split_once('e').expect("{:e} always has an exponent");
    let x: i32 = exp.parse().expect("valid exponent");
    if (-4..P).contains(&x) {
        let fixed = format!("{:.*}", (P - 1 - x) as usize, v);
        strip_zeros(&fixed).to_string()
    } else {
        let mut out = strip_zeros(mantissa).to_string();
        out.push('e');
        out.push(if x < 0 { '-' } else { '+' });
        let a = x.unsigned_abs();
        if a < 10 {
            out.push('0');
        }
        out.push_str(&a.to_string());
        out
    }
}

fn strip_zeros(s: &str) -> &str {
    if s.contains('.') {
        s.trim_end_matches('0').trim_end_matches('.')
    } else {
        s
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shortest_matches_nlohmann() {
        assert_eq!(format_shortest(1.0), "1.0");
        assert_eq!(format_shortest(-4.0), "-4.0");
        assert_eq!(format_shortest(0.0), "0.0");
        assert_eq!(format_shortest(-0.0), "-0.0");
        assert_eq!(format_shortest(0.8), "0.8");
        assert_eq!(format_shortest(0.1f32 as f64), "0.10000000149011612");
        assert_eq!(format_shortest(123456.5), "123456.5");
        assert_eq!(format_shortest(0.001), "0.001");
        assert_eq!(format_shortest(0.0001), "0.0001");
        assert_eq!(format_shortest(0.00001), "1e-05");
        assert_eq!(format_shortest(1.5e-7), "1.5e-07");
        assert_eq!(format_shortest(1e14), "100000000000000.0");
        assert_eq!(format_shortest(1e15), "1e+15");
        assert_eq!(format_shortest(1e16), "1e+16");
        assert_eq!(format_shortest(1.25e100), "1.25e+100");
    }

    #[test]
    fn g17_matches_printf() {
        assert_eq!(format_g17(1.0), "1");
        assert_eq!(format_g17(0.1), "0.10000000000000001");
        assert_eq!(format_g17(-2.5), "-2.5");
        assert_eq!(format_g17(1e-5), "1.0000000000000001e-05");
        assert_eq!(format_g17(1e20), "1e+20");
        assert_eq!(format_g17(123456789.0), "123456789");
        assert_eq!(format_g17(0.0), "0");
        assert_eq!(format_g17(-0.0), "-0");
    }

    #[test]
    fn escapes_per_flavor() {
        let v = JsonValue::from("a\"\\\n\u{08}\u{1f}é");
        assert_eq!(
            write(&v, Flavor::Canonical { indent: 0 }),
            "\"a\\\"\\\\\\n\\u0008\\u001fé\""
        );
        assert_eq!(write(&v, Flavor::Gltf), "\"a\\\"\\\\\\n\\b\\u001fé\"");
    }

    #[test]
    fn pretty_and_sorted() {
        let mut o = JsonValue::object();
        o.set("b", 1);
        o.set("a", JsonValue::array());
        o.set("B", vec![JsonValue::from(true)]);
        assert_eq!(write(&o, Flavor::Gltf), r#"{"B":[true],"a":[],"b":1}"#);
        assert_eq!(
            write(&o, Flavor::Canonical { indent: 0 }),
            r#"{"b":1,"a":[],"B":[true]}"#
        );
        assert_eq!(
            write(&o, Flavor::Canonical { indent: 2 }),
            "{\n  \"b\": 1,\n  \"a\": [],\n  \"B\": [\n    true\n  ]\n}"
        );
    }
}
