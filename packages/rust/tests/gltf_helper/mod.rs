//! A minimal JSON parser and GLB reader standing in for the TinyGLTF loader
//! the C++ tests use. Parses into `openskp::JsonValue` (object key order
//! preserved).
#![allow(dead_code)]

use openskp::JsonValue;

struct Parser<'a> {
    s: &'a [u8],
    i: usize,
}

impl Parser<'_> {
    fn ws(&mut self) {
        while self.i < self.s.len() && matches!(self.s[self.i], b' ' | b'\t' | b'\n' | b'\r') {
            self.i += 1;
        }
    }

    fn eat(&mut self, c: u8) {
        self.ws();
        assert_eq!(
            self.s.get(self.i),
            Some(&c),
            "expected '{}' at {}",
            c as char,
            self.i
        );
        self.i += 1;
    }

    fn value(&mut self) -> JsonValue {
        self.ws();
        match self.s[self.i] {
            b'{' => {
                self.i += 1;
                let mut entries = Vec::new();
                self.ws();
                if self.s[self.i] == b'}' {
                    self.i += 1;
                    return JsonValue::Object(entries);
                }
                loop {
                    self.ws();
                    let key = self.string();
                    self.eat(b':');
                    let v = self.value();
                    entries.push((key, v));
                    self.ws();
                    let c = self.s[self.i];
                    self.i += 1;
                    match c {
                        b',' => continue,
                        b'}' => return JsonValue::Object(entries),
                        _ => panic!("bad object at {}", self.i),
                    }
                }
            }
            b'[' => {
                self.i += 1;
                let mut items = Vec::new();
                self.ws();
                if self.s[self.i] == b']' {
                    self.i += 1;
                    return JsonValue::Array(items);
                }
                loop {
                    items.push(self.value());
                    self.ws();
                    let c = self.s[self.i];
                    self.i += 1;
                    match c {
                        b',' => continue,
                        b']' => return JsonValue::Array(items),
                        _ => panic!("bad array at {}", self.i),
                    }
                }
            }
            b'"' => JsonValue::String(self.string()),
            b't' => self.lit("true", JsonValue::Bool(true)),
            b'f' => self.lit("false", JsonValue::Bool(false)),
            b'n' => self.lit("null", JsonValue::Null),
            _ => self.number(),
        }
    }

    fn lit(&mut self, word: &str, v: JsonValue) -> JsonValue {
        assert!(
            self.s[self.i..].starts_with(word.as_bytes()),
            "bad literal at {}",
            self.i
        );
        self.i += word.len();
        v
    }

    fn number(&mut self) -> JsonValue {
        let start = self.i;
        while self.i < self.s.len()
            && matches!(
                self.s[self.i],
                b'-' | b'+' | b'.' | b'e' | b'E' | b'0'..=b'9'
            )
        {
            self.i += 1;
        }
        let text = std::str::from_utf8(&self.s[start..self.i]).unwrap();
        if text.contains(['.', 'e', 'E']) {
            JsonValue::Double(text.parse().unwrap_or_else(|_| panic!("bad number {text}")))
        } else {
            JsonValue::Int(text.parse().unwrap_or_else(|_| panic!("bad number {text}")))
        }
    }

    fn string(&mut self) -> String {
        assert_eq!(self.s[self.i], b'"');
        self.i += 1;
        let mut out = Vec::new();
        loop {
            let c = self.s[self.i];
            self.i += 1;
            match c {
                b'"' => break,
                b'\\' => {
                    let e = self.s[self.i];
                    self.i += 1;
                    match e {
                        b'"' => out.push(b'"'),
                        b'\\' => out.push(b'\\'),
                        b'/' => out.push(b'/'),
                        b'b' => out.push(8),
                        b'f' => out.push(12),
                        b'n' => out.push(b'\n'),
                        b'r' => out.push(b'\r'),
                        b't' => out.push(b'\t'),
                        b'u' => {
                            let hex = std::str::from_utf8(&self.s[self.i..self.i + 4]).unwrap();
                            self.i += 4;
                            let cp = u32::from_str_radix(hex, 16).unwrap();
                            let ch = char::from_u32(cp).expect("BMP scalar");
                            let mut buf = [0; 4];
                            out.extend_from_slice(ch.encode_utf8(&mut buf).as_bytes());
                        }
                        _ => panic!("bad escape"),
                    }
                }
                _ => out.push(c),
            }
        }
        String::from_utf8(out).unwrap()
    }
}

/// Parses one JSON document.
pub fn parse_json(text: &str) -> JsonValue {
    let mut p = Parser {
        s: text.as_bytes(),
        i: 0,
    };
    let v = p.value();
    p.ws();
    assert_eq!(p.i, text.len(), "trailing data after JSON");
    v
}

/// A loaded GLB: its JSON document and BIN chunk.
pub struct Glb {
    pub json: JsonValue,
    pub json_text: String,
    pub bin: Vec<u8>,
}

fn u32_at(b: &[u8], at: usize) -> u32 {
    u32::from_le_bytes(b[at..at + 4].try_into().unwrap())
}

/// Validates the GLB container and splits it into JSON + BIN.
pub fn load_glb(bytes: &[u8]) -> Glb {
    assert!(bytes.len() >= 20);
    assert_eq!(&bytes[0..4], b"glTF");
    assert_eq!(u32_at(bytes, 4), 2);
    assert_eq!(u32_at(bytes, 8) as usize, bytes.len());
    let json_len = u32_at(bytes, 12) as usize;
    assert_eq!(json_len % 4, 0);
    assert_eq!(u32_at(bytes, 16), 0x4E4F534A);
    let json_text = std::str::from_utf8(&bytes[20..20 + json_len])
        .unwrap()
        .to_owned();
    let mut bin = Vec::new();
    let next = 20 + json_len;
    if next < bytes.len() {
        let bin_len = u32_at(bytes, next) as usize;
        assert_eq!(bin_len % 4, 0);
        assert_eq!(u32_at(bytes, next + 4), 0x004E4942);
        bin = bytes[next + 8..next + 8 + bin_len].to_vec();
        assert_eq!(next + 8 + bin_len, bytes.len());
    }
    let json = parse_json(&json_text);
    if let Some(len) = json
        .find("buffers")
        .and_then(|b| b.as_array())
        .and_then(|b| b.first())
    {
        let len = len.find("byteLength").unwrap().as_int().unwrap() as usize;
        assert!(len <= bin.len());
    }
    Glb {
        json,
        json_text,
        bin,
    }
}

impl Glb {
    /// A top-level array (empty when absent).
    pub fn list(&self, key: &str) -> &[JsonValue] {
        self.json
            .find(key)
            .and_then(|v| v.as_array())
            .unwrap_or(&[])
    }

    pub fn accessor(&self, index: i64) -> &JsonValue {
        &self.list("accessors")[index as usize]
    }

    /// Element `index` of an accessor, as raw 4-byte little-endian words.
    fn word(&self, accessor: &JsonValue, index: usize) -> [u8; 4] {
        let view = &self.list("bufferViews")[int(accessor, "bufferView") as usize];
        let offset = view.find("byteOffset").map_or(0, |v| v.as_int().unwrap()) as usize
            + accessor
                .find("byteOffset")
                .map_or(0, |v| v.as_int().unwrap()) as usize
            + index * 4;
        self.bin[offset..offset + 4].try_into().unwrap()
    }

    pub fn f32_at(&self, accessor: &JsonValue, index: usize) -> f32 {
        f32::from_le_bytes(self.word(accessor, index))
    }

    pub fn u32_at(&self, accessor: &JsonValue, index: usize) -> u32 {
        u32::from_le_bytes(self.word(accessor, index))
    }
}

/// An integer field of an object.
pub fn int(v: &JsonValue, key: &str) -> i64 {
    v.find(key)
        .and_then(JsonValue::as_int)
        .unwrap_or_else(|| panic!("missing int {key}"))
}

/// A string field of an object.
pub fn string<'a>(v: &'a JsonValue, key: &str) -> &'a str {
    v.find(key)
        .and_then(JsonValue::as_str)
        .unwrap_or_else(|| panic!("missing string {key}"))
}

/// A numeric array field (ints or doubles) as f64s.
pub fn numbers(v: &JsonValue, key: &str) -> Vec<f64> {
    v.find(key)
        .and_then(JsonValue::as_array)
        .unwrap_or_else(|| panic!("missing array {key}"))
        .iter()
        .map(|n| {
            n.as_double()
                .or_else(|| n.as_int().map(|i| i as f64))
                .unwrap()
        })
        .collect()
}

pub fn contains_bytes(haystack: &[u8], needle: &[u8]) -> bool {
    haystack.windows(needle.len()).any(|w| w == needle)
}
