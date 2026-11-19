//! Hand-written equivalents of the `std::regex` patterns core.cpp uses to pull
//! attributes out of material.xml / style.xml. Each function documents the
//! pattern it reproduces and follows ECMAScript leftmost-first backtracking
//! semantics (`\s` = space, \t, \n, \v, \f, \r; `\w` = [A-Za-z0-9_]).
//! Everything works on bytes, like the C++ `std::string`s.

use std::collections::BTreeMap;
use std::ops::Range;

use super::core::decode_xml_entities_bytes;
use super::text;

fn is_space(c: u8) -> bool {
    matches!(c, b' ' | b'\t' | b'\n' | 0x0B | 0x0C | b'\r')
}

fn is_word(c: u8) -> bool {
    c.is_ascii_alphanumeric() || c == b'_'
}

fn starts_with_ci(s: &[u8], at: usize, w: &[u8]) -> bool {
    at <= s.len() && s.len() - at >= w.len() && s[at..at + w.len()].eq_ignore_ascii_case(w)
}

/// End positions of `(?:[A-Za-z_][\w.-]*:)?NAME` (NAME case-insensitive) at
/// `i`, in the order the regex tries them: with the prefix, then without.
fn qname_ends(s: &[u8], i: usize, name: &[u8]) -> [Option<usize>; 2] {
    let mut with = None;
    if i < s.len() && (s[i].is_ascii_alphabetic() || s[i] == b'_') {
        let mut j = i + 1;
        while j < s.len() && (is_word(s[j]) || s[j] == b'.' || s[j] == b'-') {
            j += 1;
        }
        if j < s.len() && s[j] == b':' && starts_with_ci(s, j + 1, name) {
            with = Some(j + 1 + name.len());
        }
    }
    let without = starts_with_ci(s, i, name).then_some(i + name.len());
    [with, without]
}

/// `\b` after a name ending in a word character.
fn boundary_after(s: &[u8], e: usize) -> bool {
    e == s.len() || !is_word(s[e])
}

/// A match of an opening-tag pattern.
pub struct OpenTag {
    pub start: usize,
    pub attrs: Range<usize>,
    pub end: usize,
}

/// `<(?:P:)?NAME\b([^>]*)>` (icase) searched from `from`. With
/// `require_gt = false` the pattern is `<(?:P:)?NAME\b([^>]*)` (no closing
/// `>`), as material_xml's image lookup uses.
pub fn find_open_tag(s: &[u8], from: usize, name: &str, require_gt: bool) -> Option<OpenTag> {
    let name = name.as_bytes();
    for i in from..s.len() {
        if s[i] != b'<' {
            continue;
        }
        for e in qname_ends(s, i + 1, name).into_iter().flatten() {
            if !boundary_after(s, e) {
                continue;
            }
            let gt = s[e..].iter().position(|&c| c == b'>').map(|k| e + k);
            match (gt, require_gt) {
                (Some(g), true) => {
                    return Some(OpenTag {
                        start: i,
                        attrs: e..g,
                        end: g + 1,
                    })
                }
                (None, true) => return None,
                (g, false) => {
                    let g = g.unwrap_or(s.len());
                    return Some(OpenTag {
                        start: i,
                        attrs: e..g,
                        end: g,
                    });
                }
            }
        }
    }
    None
}

/// Earliest `</(?:P:)?NAME>` at or after `from`: (start, end).
fn find_close(s: &[u8], from: usize, name: &[u8]) -> Option<(usize, usize)> {
    let mut k = from;
    while k + 1 < s.len() {
        if s[k] == b'<' && s[k + 1] == b'/' {
            for e in qname_ends(s, k + 2, name).into_iter().flatten() {
                if e < s.len() && s[e] == b'>' {
                    return Some((k, e + 1));
                }
            }
        }
        k += 1;
    }
    None
}

/// A match of an element pattern.
pub struct Element {
    pub attrs: Range<usize>,
    pub body: Option<Range<usize>>,
    pub end: usize,
}

/// `<(?:P:)?NAME\b([^>]*)>([\s\S]*?)</(?:P:)?NAME>` (icase) from `from`.
pub fn find_element(s: &[u8], from: usize, name: &str) -> Option<Element> {
    let name = name.as_bytes();
    for i in from..s.len() {
        if s[i] != b'<' {
            continue;
        }
        for e in qname_ends(s, i + 1, name).into_iter().flatten() {
            if !boundary_after(s, e) {
                continue;
            }
            let g = s[e..].iter().position(|&c| c == b'>').map(|k| e + k)?;
            if let Some((k, ce)) = find_close(s, g + 1, name) {
                return Some(Element {
                    attrs: e..g,
                    body: Some(g + 1..k),
                    end: ce,
                });
            }
            return None;
        }
    }
    None
}

/// `<(?:P:)?screenimage\b([^>]*?)(?:/>|>([\s\S]*?)</(?:P:)?screenimage>)`
/// (icase) from `from`: a self-closing tag or one with a body.
pub fn find_screenimage(s: &[u8], from: usize) -> Option<Element> {
    let name = b"screenimage";
    for i in from..s.len() {
        if s[i] != b'<' {
            continue;
        }
        for e in qname_ends(s, i + 1, name).into_iter().flatten() {
            if !boundary_after(s, e) {
                continue;
            }
            // Lazy attribute text: stop at the first "/>" or ">".
            let mut j = e;
            while j < s.len() {
                if s[j] == b'/' && j + 1 < s.len() && s[j + 1] == b'>' {
                    return Some(Element {
                        attrs: e..j,
                        body: None,
                        end: j + 2,
                    });
                }
                if s[j] == b'>' {
                    if let Some((k, ce)) = find_close(s, j + 1, name) {
                        return Some(Element {
                            attrs: e..j,
                            body: Some(j + 1..k),
                            end: ce,
                        });
                    }
                    break;
                }
                j += 1;
            }
        }
    }
    None
}

/// `(?:^|\s)KEY\s*=\s*["']([^"']*)["']` (icase), entity-decoded; `""` when
/// absent (core.cpp's `attr`).
pub fn attr(s: &[u8], key: &str) -> String {
    let key = key.as_bytes();
    let rest = |k: usize| -> Option<Range<usize>> {
        if !starts_with_ci(s, k, key) {
            return None;
        }
        let mut j = k + key.len();
        while j < s.len() && is_space(s[j]) {
            j += 1;
        }
        if j >= s.len() || s[j] != b'=' {
            return None;
        }
        j += 1;
        while j < s.len() && is_space(s[j]) {
            j += 1;
        }
        if j >= s.len() || (s[j] != b'"' && s[j] != b'\'') {
            return None;
        }
        j += 1;
        let vs = j;
        while j < s.len() && s[j] != b'"' && s[j] != b'\'' {
            j += 1;
        }
        (j < s.len()).then_some(vs..j)
    };
    for i in 0..=s.len() {
        let starts = [
            (i == 0).then_some(0),
            (i < s.len() && is_space(s[i])).then_some(i + 1),
        ];
        for k in starts.into_iter().flatten() {
            if let Some(v) = rest(k) {
                return text(&decode_xml_entities_bytes(&s[v]));
            }
        }
    }
    String::new()
}

/// Every `name="value"` / `name='value'` attribute
/// (`([A-Za-z_][\w.:-]*)\s*=\s*(?:"([^"]*)"|'([^']*)')`), entity-decoded;
/// later duplicates win.
pub fn attributes(s: &[u8]) -> BTreeMap<String, String> {
    let mut out = BTreeMap::new();
    let at = |i: usize| -> Option<(Range<usize>, Range<usize>, usize)> {
        if !(s[i].is_ascii_alphabetic() || s[i] == b'_') {
            return None;
        }
        let mut j = i + 1;
        while j < s.len() && (is_word(s[j]) || matches!(s[j], b'.' | b':' | b'-')) {
            j += 1;
        }
        let name = i..j;
        while j < s.len() && is_space(s[j]) {
            j += 1;
        }
        if j >= s.len() || s[j] != b'=' {
            return None;
        }
        j += 1;
        while j < s.len() && is_space(s[j]) {
            j += 1;
        }
        if j >= s.len() || (s[j] != b'"' && s[j] != b'\'') {
            return None;
        }
        let q = s[j];
        let close = s[j + 1..].iter().position(|&c| c == q)? + j + 1;
        Some((name, j + 1..close, close + 1))
    };
    let mut i = 0;
    while i < s.len() {
        match at(i) {
            Some((name, value, end)) => {
                out.insert(text(&s[name]), text(&decode_xml_entities_bytes(&s[value])));
                i = end;
            }
            None => i += 1,
        }
    }
    out
}
