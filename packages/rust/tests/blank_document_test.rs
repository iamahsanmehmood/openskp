mod common;

use std::path::Path;

use common::fixture;
use openskp::create::{build_blank_v17, SCAFFOLD_SHA256};
use openskp::{ParseOptions, SkpFile};

fn sha256_hex(data: &[u8]) -> String {
    const K: [u32; 64] = [
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
        0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
        0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
        0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
        0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
        0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
        0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
        0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
        0xc67178f2,
    ];
    let mut h: [u32; 8] = [
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab,
        0x5be0cd19,
    ];
    let bit_len = (data.len() as u64) * 8;
    let mut msg = data.to_vec();
    msg.push(0x80);
    while (msg.len() % 64) != 56 {
        msg.push(0);
    }
    msg.extend_from_slice(&bit_len.to_be_bytes());
    for off in (0..msg.len()).step_by(64) {
        let chunk = &msg[off..off + 64];
        let mut w = [0u32; 64];
        for (i, word) in w.iter_mut().take(16).enumerate() {
            *word = u32::from_be_bytes([
                chunk[i * 4],
                chunk[i * 4 + 1],
                chunk[i * 4 + 2],
                chunk[i * 4 + 3],
            ]);
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16]
                .wrapping_add(s0)
                .wrapping_add(w[i - 7])
                .wrapping_add(s1);
        }
        let [mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut hh] = h;
        for i in 0..64 {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let ch = (e & f) ^ ((!e) & g);
            let temp1 = hh
                .wrapping_add(s1)
                .wrapping_add(ch)
                .wrapping_add(K[i])
                .wrapping_add(w[i]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let temp2 = s0.wrapping_add(maj);
            hh = g;
            g = f;
            f = e;
            e = d.wrapping_add(temp1);
            d = c;
            c = b;
            b = a;
            a = temp1.wrapping_add(temp2);
        }
        for (dst, add) in h.iter_mut().zip([a, b, c, d, e, f, g, hh]) {
            *dst = dst.wrapping_add(add);
        }
    }
    let mut out = String::with_capacity(64);
    for word in h {
        out.push_str(&format!("{word:08x}"));
    }
    out
}

fn base64_encode(data: &[u8]) -> String {
    const TABLE: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(data.len().div_ceil(3) * 4);
    let mut i = 0;
    while i + 3 <= data.len() {
        let n = ((data[i] as u32) << 16) | ((data[i + 1] as u32) << 8) | (data[i + 2] as u32);
        out.push(TABLE[((n >> 18) & 63) as usize] as char);
        out.push(TABLE[((n >> 12) & 63) as usize] as char);
        out.push(TABLE[((n >> 6) & 63) as usize] as char);
        out.push(TABLE[(n & 63) as usize] as char);
        i += 3;
    }
    let rem = data.len() - i;
    if rem == 1 {
        let n = (data[i] as u32) << 16;
        out.push(TABLE[((n >> 18) & 63) as usize] as char);
        out.push(TABLE[((n >> 12) & 63) as usize] as char);
        out.push('=');
        out.push('=');
    } else if rem == 2 {
        let n = ((data[i] as u32) << 16) | ((data[i + 1] as u32) << 8);
        out.push(TABLE[((n >> 18) & 63) as usize] as char);
        out.push(TABLE[((n >> 12) & 63) as usize] as char);
        out.push(TABLE[((n >> 6) & 63) as usize] as char);
        out.push('=');
    }
    out
}

fn hex_val(b: u8) -> Option<u8> {
    match b {
        b'0'..=b'9' => Some(b - b'0'),
        b'a'..=b'f' => Some(b - b'a' + 10),
        b'A'..=b'F' => Some(b - b'A' + 10),
        _ => None,
    }
}

fn extract_hex_byte_tokens(raw: &[u8]) -> Vec<u8> {
    let mut out = Vec::new();
    let mut i = 0;
    while i + 3 < raw.len() {
        if raw[i] == b'0' && (raw[i + 1] == b'x' || raw[i + 1] == b'X') {
            if let (Some(hi), Some(lo)) = (hex_val(raw[i + 2]), hex_val(raw[i + 3])) {
                let next_is_hex = i + 4 < raw.len() && hex_val(raw[i + 4]).is_some();
                if !next_is_hex {
                    out.push((hi << 4) | lo);
                    i += 4;
                    continue;
                }
            }
        }
        i += 1;
    }
    out
}

fn contains_subsequence(haystack: &[u8], needle: &[u8]) -> bool {
    !needle.is_empty() && haystack.windows(needle.len()).any(|w| w == needle)
}

fn collect_files(dir: &Path, out: &mut Vec<std::path::PathBuf>) {
    for entry in std::fs::read_dir(dir).unwrap() {
        let entry = entry.unwrap();
        let path = entry.path();
        if path.is_dir() {
            collect_files(&path, out);
        } else {
            out.push(path);
        }
    }
}

#[test]
fn build_blank_v17_matches_fixture_byte_for_byte() {
    let doc = build_blank_v17();
    let expected = std::fs::read(fixture("blank_v17.skp")).unwrap();
    assert_eq!(doc.data.len(), 5796);
    assert_eq!(sha256_hex(&doc.data), SCAFFOLD_SHA256);
    assert_eq!(doc.data, expected);

    assert_eq!(doc.model_guid_offset, 52);
    assert_eq!(doc.pid_counter_offset, 1987);
    assert_eq!(doc.next_pid, 18);
    assert_eq!(doc.no_scale_mask_offset, 2008);
    assert_eq!(doc.tail_pos, 3515);
    assert_eq!(doc.scaffold_next_slot, 11);
}

#[test]
fn built_blank_document_reads_back_in_classic_reader() {
    let doc = build_blank_v17();
    let mut pos = 0usize;
    let read_utf16 = |data: &[u8], pos: &mut usize| -> String {
        assert_eq!(&data[*pos..*pos + 3], &[0xFF, 0xFE, 0xFF]);
        let n = data[*pos + 3] as usize;
        *pos += 4;
        let mut units = Vec::with_capacity(n);
        for i in 0..n {
            units.push(u16::from_le_bytes([
                data[*pos + i * 2],
                data[*pos + i * 2 + 1],
            ]));
        }
        *pos += n * 2;
        String::from_utf16(&units).unwrap()
    };

    assert_eq!(read_utf16(&doc.data, &mut pos), "SketchUp Model");
    assert_eq!(read_utf16(&doc.data, &mut pos), "{17.0.1}");
    pos += 16; // model GUID
    assert_eq!(read_utf16(&doc.data, &mut pos), "");
    pos += 4; // save timestamp
    assert_eq!(
        u16::from_le_bytes([doc.data[pos], doc.data[pos + 1]]),
        0xFFFF
    );
    assert_eq!(
        u16::from_le_bytes([doc.data[pos + 2], doc.data[pos + 3]]),
        0
    );
    let vmap_name_len = u16::from_le_bytes([doc.data[pos + 4], doc.data[pos + 5]]) as usize;
    pos += 6;
    assert_eq!(&doc.data[pos..pos + vmap_name_len], b"CVersionMap");
    pos += vmap_name_len;

    let mut vmap_count = 0usize;
    loop {
        let cls = read_utf16(&doc.data, &mut pos);
        if cls == "End-Of-Version-Map" {
            break;
        }
        pos += 4;
        vmap_count += 1;
    }
    assert_eq!(vmap_count, 58);
    assert_eq!(pos, 1971);

    let model = SkpFile::from_buffer(doc.data)
        .parse(&ParseOptions::default())
        .unwrap();
    assert_eq!(model.version, "{17.0.1}");
    assert_eq!(model.layers.len(), 1);
    assert_eq!(model.layers[0].name, "Layer0");
    assert!(!model.layers[0].hidden);
    assert_eq!(model.layers[0].color, [255, 84, 84]);
    assert!(model.materials.is_empty());
    assert!(model.definitions.is_empty());
    assert!(model.root().faces.is_empty());
    assert!(model.root().edges.is_empty());
    assert!(model.root().vertices.is_empty());
    assert!(model.root().instances.is_empty());
    assert!(model.pages.is_empty());
    assert_eq!(model.unread_root_entities, 0);
    assert_eq!(model.face_front_color, Some([225, 225, 200]));
    assert_eq!(model.face_back_color, Some([128, 128, 200]));
    assert!(!model.color_by_tag);
    assert!(model.attribute_dictionaries.is_empty());
}

#[test]
fn src_tree_contains_no_skp_files_or_template_bytes() {
    let template_bytes = build_blank_v17().data;
    let template_hex: String = template_bytes.iter().map(|b| format!("{b:02x}")).collect();
    let template_b64 = base64_encode(&template_bytes);

    let src_dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
    let mut files = Vec::new();
    collect_files(&src_dir, &mut files);
    assert!(!files.is_empty());

    for file in files {
        let ext = file
            .extension()
            .and_then(|e| e.to_str())
            .unwrap_or("")
            .to_ascii_lowercase();
        assert_ne!(
            ext,
            "skp",
            "Unexpected .skp file under src/: {}",
            file.display()
        );

        let raw = std::fs::read(&file).unwrap();
        assert!(
            !contains_subsequence(&raw, &template_bytes),
            "Raw template bytes found in {}",
            file.display()
        );

        let mut compact = String::with_capacity(raw.len());
        for &b in &raw {
            if b.is_ascii_alphanumeric() || b == b'+' || b == b'/' || b == b'=' {
                compact.push(b as char);
            }
        }
        assert!(
            !compact.contains(&template_b64),
            "Base64 template bytes found in {}",
            file.display()
        );
        assert!(
            !compact.to_ascii_lowercase().contains(&template_hex),
            "Hex template bytes found in {}",
            file.display()
        );
        assert!(
            !contains_subsequence(&extract_hex_byte_tokens(&raw), &template_bytes),
            "Hex byte array of template found in {}",
            file.display()
        );
    }
}
