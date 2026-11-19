// The v20 filler probe, exercised on synthetic records.
//
// Ported from the TypeScript fix (openskp#192, following the review on
// openskp#155): the original implementation walked forward to the first
// non-zero byte and treated it as the count's low byte. That cannot represent
// a count which is an exact multiple of 256 - its low byte IS 0x00, so the
// scan walks straight into the count and misaligns every read after it.
// Probing whole u32s at 4-byte strides fixes that, since no individual byte is
// ever inspected.
//
// Layout (see find_count_after_v20_filler):
//   <ff fe ff> <u8 0>   empty UTF-16 string
//   <zero padding>      length varies per call site, always pad % 4 == 1
//   <u32 count>

use openskp::internal::find_count_after_v20_filler;

/// A filler record followed by `count`, then a class-record header.
fn filler(count: u32, pad: usize) -> Vec<u8> {
    let mut bytes = vec![0xff, 0xfe, 0xff, 0x00];
    bytes.extend(std::iter::repeat_n(0u8, pad));
    bytes.extend_from_slice(&count.to_le_bytes());
    bytes.extend_from_slice(&[0xff, 0xff, 0x0b, 0x00]); // whatever record comes next
    bytes
}

#[test]
fn reads_counts_and_paddings_seen_in_real_v20_files() {
    // both padding lengths observed in gondola_v20.skp and a second v20 model
    let hit1 = find_count_after_v20_filler(&filler(20, 9), 0, 1_000_000).expect("hit");
    assert_eq!(hit1.count, 20);
    assert_eq!(hit1.next, 17);

    let hit2 = find_count_after_v20_filler(&filler(5425, 13), 0, 5_000_000).expect("hit");
    assert_eq!(hit2.count, 5425);
    assert_eq!(hit2.next, 21);
}

#[test]
fn reads_a_count_that_is_an_exact_multiple_of_256() {
    // the regression this test exists for: a 0x00 low byte is
    // indistinguishable from padding to a byte-at-a-time scan
    for count in [256u32, 512, 1024, 65536, 16_777_216 / 16] {
        for pad in [9usize, 13] {
            let hit = find_count_after_v20_filler(&filler(count, pad), 0, 5_000_000);
            assert_eq!(hit.map(|h| h.count), Some(count), "count={count} pad={pad}");
        }
    }
}

#[test]
fn reports_where_to_resume_reading() {
    let hit = find_count_after_v20_filler(&filler(256, 13), 0, 5_000_000).expect("hit");
    // 4 (marker+len) + 13 (padding) + 4 (the count itself)
    assert_eq!(hit.next, 21);
}

#[test]
fn ignores_a_non_empty_string_record() {
    // a real string here is genuine data; moving the cursor past it would
    // corrupt the parse
    let bytes = [0xff, 0xfe, 0xff, 0x05, 0, 0, 0, 0, 0, 0, 0, 0, 20, 0, 0, 0];
    assert!(find_count_after_v20_filler(&bytes, 0, 1_000_000).is_none());
}

#[test]
fn returns_none_when_there_is_no_marker_ahead() {
    let bytes = [0u8; 32]; // all zeros, no ff fe ff
    assert!(find_count_after_v20_filler(&bytes, 0, 1_000_000).is_none());
}

#[test]
fn respects_the_callers_plausibility_limit() {
    // nrel's limit is 100_000: a value above it is not the count we want
    assert!(find_count_after_v20_filler(&filler(200_000, 13), 0, 100_000).is_none());
    let hit = find_count_after_v20_filler(&filler(200_000, 13), 0, 5_000_000).expect("hit");
    assert_eq!(hit.count, 200_000);
}

#[test]
fn does_not_run_past_the_end_of_the_buffer() {
    let bytes = [0xff, 0xfe, 0xff, 0x00, 0, 0];
    assert!(find_count_after_v20_filler(&bytes, 0, 1_000_000).is_none());
}
