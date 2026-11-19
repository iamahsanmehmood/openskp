// The pure-Rust DEFLATE/zlib codec and ZIP reader that replace miniz. Real
// SketchUp-written streams are exercised by every fixture parse (each entry is
// CRC-checked); these cover the codec's own edge cases.

mod common;

use openskp::deflate::{deflate_raw, zlib_compress};
use openskp::inflate::{inflate_raw, uncompress, zlib_decompress, InflateError, UncompressError};
use openskp::zip::{crc32, ZipArchive};

fn pseudo_random(n: usize, seed: u64) -> Vec<u8> {
    let mut s = seed;
    (0..n)
        .map(|_| {
            s = s
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            (s >> 33) as u8
        })
        .collect()
}

fn samples() -> Vec<Vec<u8>> {
    let text: Vec<u8> = b"SketchUp model.dat "
        .iter()
        .copied()
        .cycle()
        .take(200_000)
        .collect();
    let mut mixed = pseudo_random(50_000, 7);
    mixed.extend(std::iter::repeat_n(0u8, 70_000));
    mixed.extend(text.iter().take(30_000));
    vec![
        Vec::new(),
        vec![42],
        b"hello hello hello hello".to_vec(),
        pseudo_random(100_000, 1),
        text,
        vec![0u8; 1 << 20],
        mixed,
    ]
}

#[test]
fn known_zlib_vector() {
    let hello = [
        0x78, 0x9c, 0xcb, 0x48, 0xcd, 0xc9, 0xc9, 0x07, 0x00, 0x06, 0x2c, 0x02, 0x15,
    ];
    assert_eq!(zlib_decompress(&hello).unwrap(), b"hello");
    assert_eq!(uncompress(&hello, 5).unwrap(), b"hello");
    assert_eq!(uncompress(&hello, 4).unwrap_err(), UncompressError::Buf);
}

#[test]
fn raw_round_trips_at_every_level() {
    for data in samples() {
        for level in [0, 1, 4, 6, 9, 10] {
            let packed = deflate_raw(&data, level);
            assert_eq!(
                inflate_raw(&packed, data.len()).unwrap(),
                data,
                "level {level}, {} bytes",
                data.len()
            );
            if !data.is_empty() {
                assert_eq!(
                    inflate_raw(&packed, data.len() - 1).unwrap_err(),
                    InflateError::OutputLimit
                );
            }
        }
    }
}

#[test]
fn zlib_round_trips() {
    for data in samples() {
        let packed = zlib_compress(&data, 6);
        assert_eq!(zlib_decompress(&packed).unwrap(), data);
    }
}

#[test]
fn zeros_reach_deflates_maximum_ratio() {
    let zeros = vec![0u8; 8 << 20];
    let packed = deflate_raw(&zeros, 10);
    assert!(
        zeros.len() / packed.len() > 1000,
        "ratio {}",
        zeros.len() / packed.len()
    );
}

#[test]
fn corrupt_streams_are_errors() {
    let data = pseudo_random(10_000, 3);
    let packed = zlib_compress(&data, 6);
    // Truncated.
    assert!(zlib_decompress(&packed[..packed.len() / 2]).is_err());
    assert_eq!(
        uncompress(&packed[..packed.len() / 2], 1 << 20).unwrap_err(),
        UncompressError::Data
    );
    // Adler-32 trailer.
    let mut bad = packed.clone();
    let n = bad.len();
    bad[n - 1] ^= 1;
    assert_eq!(
        zlib_decompress(&bad).unwrap_err(),
        InflateError::Adler32Mismatch
    );
    // Header: FDICT set / not a multiple of 31.
    assert_eq!(
        zlib_decompress(&[0x78, 0x9d, 0x03, 0x00]).unwrap_err(),
        InflateError::BadZlibHeader
    );
    // Block type 3.
    assert_eq!(
        inflate_raw(&[0x07], 10).unwrap_err(),
        InflateError::InvalidBlockType
    );
    // Stored block whose LEN is not ~NLEN.
    assert_eq!(
        inflate_raw(&[0x01, 0x05, 0x00, 0x00, 0x00], 10).unwrap_err(),
        InflateError::StoredLengthMismatch
    );
    // Fixed block: a match (len 3, distance 1) before any output.
    // bits: BFINAL=1, BTYPE=01, symbol 257 (0000001), distance code 0 (00000).
    assert_eq!(
        inflate_raw(&[0x03, 0x02, 0x00], 10).unwrap_err(),
        InflateError::DistanceTooFar
    );
}

#[test]
fn zip_reader_extracts_stored_and_deflated_entries() {
    let a = pseudo_random(5000, 9);
    let b: Vec<u8> = b"abcabcabc".iter().copied().cycle().take(40_000).collect();
    let zip = common::build_zip(&[
        common::ZipEntry {
            name: "dir/A.bin",
            data: &a,
            level: 0,
        },
        common::ZipEntry {
            name: "b.txt",
            data: &b,
            level: 9,
        },
    ]);
    let archive = ZipArchive::new(&zip).unwrap();
    assert_eq!(archive.len(), 2);
    // Lookups are ASCII case-insensitive, like miniz's sorted index.
    let ia = archive.locate(b"DIR/a.BIN").unwrap();
    let ib = archive.locate(b"b.txt").unwrap();
    assert!(archive.locate(b"missing").is_none());
    assert_eq!(archive.extract(ia).unwrap(), a);
    assert_eq!(archive.extract(ib).unwrap(), b);
    let st = archive.stat(ib).unwrap();
    assert_eq!(st.filename, b"b.txt");
    assert_eq!(st.crc32, crc32(&b));
    assert_eq!(st.uncomp_size, b.len() as u64);

    // A flipped payload byte fails the CRC check.
    let mut bad = zip.clone();
    let pos = bad
        .windows(4)
        .position(|w| w == [0x50, 0x4b, 0x03, 0x04])
        .unwrap()
        + 30
        + 9
        + 100;
    bad[pos] ^= 0xff;
    let archive = ZipArchive::new(&bad).unwrap();
    assert!(archive
        .extract(archive.locate(b"dir/A.bin").unwrap())
        .is_err());

    // Not an archive.
    assert!(ZipArchive::new(&[0u8; 100]).is_err());
}

#[test]
fn crc32_check_value() {
    assert_eq!(crc32(b"123456789"), 0xCBF4_3926);
    assert_eq!(crc32(b""), 0);
}
