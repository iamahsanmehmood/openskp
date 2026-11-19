// A ZIP entry's declared uncompressed size is untrusted central-directory
// metadata - it can be set independently of what the compressed stream
// actually decompresses to, and even when genuine, DEFLATE can expand highly
// compressible data by three orders of magnitude. The reader validates every
// entry's size before extracting. These tests build real ZIP archives (the
// crate's own DEFLATE encoder stands in for miniz's writer) and exercise the
// guard end-to-end through full_parse().

mod common;

use common::{build_zip, wrap_vff, ZipEntry};
use openskp::internal::full_parse;
use openskp::ParseOptions;

fn skp_with_model(content: &[u8], level: u32) -> Vec<u8> {
    wrap_vff(
        &build_zip(&[ZipEntry {
            name: "model.dat",
            data: content,
            level,
        }]),
        16,
    )
}

#[test]
fn rejects_an_implausible_compression_ratio() {
    // 8 MB of zeros deflates to ~8 KB - a ratio well past what real (binary
    // geometry) model.dat entries show (~10x): tiny real payload, huge
    // claimed size.
    let zeros = vec![0u8; 8 * 1024 * 1024];
    let skp = skp_with_model(&zeros, 10);
    let err = full_parse(&skp, &ParseOptions::default())
        .expect_err("expected a decompression-bomb rejection");
    assert!(err.to_string().contains("compression ratio"), "{err}");
}

#[test]
fn rejects_a_declared_size_lie() {
    // The central directory claims 64 MB from 1 KB of stored data.
    let content = vec![7u8; 1024];
    let mut zip = build_zip(&[ZipEntry {
        name: "model.dat",
        data: &content,
        level: 0,
    }]);
    let cdh = zip
        .windows(4)
        .position(|w| w == [0x50, 0x4b, 0x01, 0x02])
        .unwrap();
    zip[cdh + 24..cdh + 28].copy_from_slice(&(64u32 * 1024 * 1024).to_le_bytes());
    // A stored entry whose sizes disagree is itself invalid to miniz unless
    // the method/time word is non-zero; mark it deflated so the size check is
    // what rejects it.
    zip[cdh + 10..cdh + 12].copy_from_slice(&8u16.to_le_bytes());
    let err = full_parse(&wrap_vff(&zip, 16), &ParseOptions::default())
        .expect_err("expected a rejection");
    assert!(err.to_string().contains("compression ratio"), "{err}");
}

#[test]
fn allows_a_realistic_compression_ratio() {
    // Pseudo-random content compresses poorly (ratio close to 1x),
    // comfortably under the safety threshold.
    let mut state = 42u64;
    let random: Vec<u8> = (0..64 * 1024)
        .map(|_| {
            state = state
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            (state >> 33) as u8
        })
        .collect();
    let skp = skp_with_model(&random, 10);
    full_parse(&skp, &ParseOptions::default()).expect("realistic ratio must parse");
}

#[test]
fn allows_a_tiny_entry_regardless_of_ratio() {
    // Below the 1 MB ratio-check threshold, even a high ratio is allowed
    // through - the absolute cost is bounded regardless.
    let zeros = vec![0u8; 1024];
    let skp = skp_with_model(&zeros, 10);
    full_parse(&skp, &ParseOptions::default()).expect("tiny entry must parse");
}
