//! The little JSON the Fragments exporter needs, kept private to this
//! module so it does not depend on the (separately ported) JSON exporter:
//!
//! * the write side reproduces `json_export.cpp`'s compact
//!   `to_json_string(value, 0)` for the only shapes `to_fragments` emits
//!   (strings, bools, arrays and objects of those);
//! * the read side is `fragments_export.cpp`'s `MinimalJsonParser`, ported
//!   as-is (including its leniency: numbers are skipped, trailing text is
//!   ignored), with two hardening changes: nesting is capped, and a
//!   `😀`-style surrogate pair decodes to one code point (the C++
//!   encodes each half separately, which is not valid UTF-8; a lone
//!   surrogate becomes U+FFFD).

use std::collections::BTreeMap;

/// Appends `s` as a JSON string literal, escaping exactly what the C++
/// `write_escaped_string` escapes.
pub(super) fn write_string(out: &mut String, s: &str) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
}

/// `["Name","<name>","STRING"]` - the IfcImporter's attribute convention.
pub(super) fn name_attribute(name: &str) -> String {
    let mut out = String::from("[\"Name\",");
    write_string(&mut out, name);
    out.push_str(",\"STRING\"]");
    out
}

/// `{"layer_hidden":{...},"generated_name_guids":[...]}`.
pub(super) fn metadata(
    layer_hidden: &BTreeMap<String, bool>,
    generated_name_guids: &[String],
) -> String {
    let mut out = String::from("{\"layer_hidden\":{");
    for (i, (layer, hidden)) in layer_hidden.iter().enumerate() {
        if i > 0 {
            out.push(',');
        }
        write_string(&mut out, layer);
        out.push(':');
        out.push_str(if *hidden { "true" } else { "false" });
    }
    out.push_str("},\"generated_name_guids\":[");
    for (i, guid) in generated_name_guids.iter().enumerate() {
        if i > 0 {
            out.push(',');
        }
        write_string(&mut out, guid);
    }
    out.push_str("]}");
    out
}

/// A parsed JSON value. Numbers are not kept (nothing here needs them) and
/// come back as `Null`, like the C++ parser.
#[derive(Debug, Clone, PartialEq)]
pub(super) enum Value {
    Null,
    Bool(bool),
    String(String),
    Array(Vec<Value>),
    /// Keyed like `std::map`; a repeated key keeps its FIRST value
    /// (`std::map::emplace`).
    Object(BTreeMap<String, Value>),
}

/// Any parse failure (the C++ throws; every caller catches and moves on).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct ParseError;

/// Deeper nesting than this is rejected instead of risking the stack.
const MAX_DEPTH: usize = 256;

/// Parses one JSON value from the start of `s`.
pub(super) fn parse(s: &[u8]) -> Result<Value, ParseError> {
    Parser { s, i: 0, depth: 0 }.parse_value()
}

struct Parser<'s> {
    s: &'s [u8],
    i: usize,
    depth: usize,
}

/// C `isspace` in the "C" locale.
fn is_space(c: u8) -> bool {
    matches!(c, b' ' | b'\t' | b'\n' | 0x0B | 0x0C | b'\r')
}

fn push_utf8(out: &mut Vec<u8>, code: u32) {
    let c = char::from_u32(code).unwrap_or(char::REPLACEMENT_CHARACTER);
    let mut tmp = [0u8; 4];
    out.extend_from_slice(c.encode_utf8(&mut tmp).as_bytes());
}

impl Parser<'_> {
    /// `s_[i]`, with `std::string`'s NUL past the end.
    fn at(&self, i: usize) -> u8 {
        self.s.get(i).copied().unwrap_or(0)
    }

    fn skip_ws(&mut self) {
        while self.i < self.s.len() && is_space(self.s[self.i]) {
            self.i += 1;
        }
    }

    fn starts_with(&self, word: &[u8]) -> bool {
        self.s
            .get(self.i..)
            .is_some_and(|rest| rest.starts_with(word))
    }

    fn parse_value(&mut self) -> Result<Value, ParseError> {
        self.skip_ws();
        if self.i >= self.s.len() {
            return Err(ParseError);
        }
        match self.s[self.i] {
            b'"' => return Ok(Value::String(self.parse_raw_string()?)),
            b'{' => return self.nested(Self::parse_object),
            b'[' => return self.nested(Self::parse_array),
            b't' if self.starts_with(b"true") => {
                self.i += 4;
                return Ok(Value::Bool(true));
            }
            b'f' if self.starts_with(b"false") => {
                self.i += 5;
                return Ok(Value::Bool(false));
            }
            b'n' if self.starts_with(b"null") => {
                self.i += 4;
                return Ok(Value::Null);
            }
            _ => {}
        }
        // A number - skipped over, never needed as a value.
        while self.i < self.s.len()
            && matches!(
                self.s[self.i],
                b'0'..=b'9' | b'-' | b'+' | b'.' | b'e' | b'E'
            )
        {
            self.i += 1;
        }
        Ok(Value::Null)
    }

    fn nested(
        &mut self,
        f: fn(&mut Self) -> Result<Value, ParseError>,
    ) -> Result<Value, ParseError> {
        if self.depth >= MAX_DEPTH {
            return Err(ParseError);
        }
        self.depth += 1;
        let v = f(self);
        self.depth -= 1;
        v
    }

    /// Up to 4 hex digits after `\u` (`std::stoul(substr(i + 1, 4), 16)`).
    fn hex4(&self, at: usize) -> Result<u32, ParseError> {
        let mut code = 0u32;
        let mut digits = 0;
        for k in 0..4 {
            match (self.at(at + k) as char).to_digit(16) {
                Some(d) => {
                    code = code * 16 + d;
                    digits += 1;
                }
                _ => break,
            }
        }
        if digits == 0 {
            Err(ParseError)
        } else {
            Ok(code)
        }
    }

    fn parse_raw_string(&mut self) -> Result<String, ParseError> {
        self.i += 1; // opening quote
        let mut out = Vec::new();
        while self.i < self.s.len() && self.s[self.i] != b'"' {
            let c = self.s[self.i];
            if c == b'\\' {
                self.i += 1;
                match self.at(self.i) {
                    b'"' => out.push(b'"'),
                    b'\\' => out.push(b'\\'),
                    b'/' => out.push(b'/'),
                    b'n' => out.push(b'\n'),
                    b'r' => out.push(b'\r'),
                    b't' => out.push(b'\t'),
                    b'b' => out.push(0x08),
                    b'f' => out.push(0x0C),
                    b'u' => {
                        let code = self.hex4(self.i + 1)?;
                        self.i += 4;
                        if (0xD800..0xDC00).contains(&code)
                            && self.at(self.i + 1) == b'\\'
                            && self.at(self.i + 2) == b'u'
                        {
                            if let Ok(low @ 0xDC00..=0xDFFF) = self.hex4(self.i + 3) {
                                self.i += 6;
                                push_utf8(
                                    &mut out,
                                    0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00),
                                );
                            } else {
                                push_utf8(&mut out, code);
                            }
                        } else {
                            push_utf8(&mut out, code);
                        }
                    }
                    _ => {}
                }
                self.i += 1;
            } else {
                out.push(c);
                self.i += 1;
            }
        }
        self.i += 1; // closing quote
        Ok(String::from_utf8_lossy(&out).into_owned())
    }

    fn parse_array(&mut self) -> Result<Value, ParseError> {
        let mut items = Vec::new();
        self.i += 1; // '['
        self.skip_ws();
        if self.i < self.s.len() && self.s[self.i] == b']' {
            self.i += 1;
            return Ok(Value::Array(items));
        }
        loop {
            items.push(self.parse_value()?);
            self.skip_ws();
            if self.i < self.s.len() && self.s[self.i] == b',' {
                self.i += 1;
                continue;
            }
            if self.i < self.s.len() && self.s[self.i] == b']' {
                self.i += 1;
                break;
            }
            return Err(ParseError);
        }
        Ok(Value::Array(items))
    }

    fn parse_object(&mut self) -> Result<Value, ParseError> {
        let mut obj = BTreeMap::new();
        self.i += 1; // '{'
        self.skip_ws();
        if self.i < self.s.len() && self.s[self.i] == b'}' {
            self.i += 1;
            return Ok(Value::Object(obj));
        }
        loop {
            self.skip_ws();
            let key = self.parse_raw_string()?;
            self.skip_ws();
            self.i += 1; // ':'
            let value = self.parse_value()?;
            obj.entry(key).or_insert(value);
            self.skip_ws();
            if self.i < self.s.len() && self.s[self.i] == b',' {
                self.i += 1;
                continue;
            }
            if self.i < self.s.len() && self.s[self.i] == b'}' {
                self.i += 1;
                break;
            }
            return Err(ParseError);
        }
        Ok(Value::Object(obj))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn writes_the_compact_cpp_format() {
        assert_eq!(name_attribute("W-2"), r#"["Name","W-2","STRING"]"#);
        assert_eq!(
            name_attribute("a\"b\\c\nd\u{1}é"),
            "[\"Name\",\"a\\\"b\\\\c\\nd\\u0001é\",\"STRING\"]"
        );
        let mut lh = BTreeMap::new();
        lh.insert("Framing".to_string(), false);
        lh.insert("Cladding".to_string(), true);
        assert_eq!(
            metadata(&lh, &["g1".to_string()]),
            r#"{"layer_hidden":{"Cladding":true,"Framing":false},"generated_name_guids":["g1"]}"#
        );
        assert_eq!(
            metadata(&BTreeMap::new(), &[]),
            r#"{"layer_hidden":{},"generated_name_guids":[]}"#
        );
    }

    #[test]
    fn parses_what_it_writes_and_real_world_escapes() {
        let v = parse(name_attribute("a\"b\\c\nd\u{1}é").as_bytes()).unwrap();
        assert_eq!(
            v,
            Value::Array(vec![
                Value::String("Name".into()),
                Value::String("a\"b\\c\nd\u{1}é".into()),
                Value::String("STRING".into()),
            ])
        );
        // Python's json.dumps escapes non-ASCII, emoji as a surrogate pair.
        let python_style = format!(
            "[\"Name\", \"Caf{b}u00e9 {b}ud83d{b}ude00\", \"STRING\"]",
            b = '\\'
        );
        let v = parse(python_style.as_bytes()).unwrap();
        let Value::Array(items) = v else { panic!() };
        assert_eq!(items[1], Value::String("Café 😀".into()));
        assert_eq!(
            parse(br#""\ud83d""#).unwrap(),
            Value::String("\u{FFFD}".into())
        );
    }

    #[test]
    fn is_as_lenient_as_the_cpp_parser() {
        assert_eq!(
            parse(b"[1, -2.5e3, true]").unwrap(),
            Value::Array(vec![Value::Null, Value::Null, Value::Bool(true)])
        );
        assert_eq!(
            parse(b"{\"a\":1} trailing").unwrap(),
            Value::Object([("a".to_string(), Value::Null)].into())
        );
        let Value::Object(o) = parse(br#"{"k":true,"k":false}"#).unwrap() else {
            panic!()
        };
        assert_eq!(o["k"], Value::Bool(true), "first duplicate key wins");
        assert!(parse(b"").is_err());
        assert!(parse(b"[1 2]").is_err());
        assert!(parse(br#""\uzz""#).is_err());
        assert!(
            parse(&vec![b'['; 10_000]).is_err(),
            "deep nesting is rejected, not a stack overflow"
        );
    }
}
