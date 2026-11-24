//! Tests for >= 4 GiB VFF `model.dat` 32-bit container length wrap recovery
//! (`wrapped_tlv_test.cpp`).

use std::collections::{BTreeMap, BTreeSet};

use openskp::internal::{
    parse_tlv_recursive, tlv_headers, tlv_headers_slice, wrapped_model_records, ErrorKind,
    TlvHeader,
};

const WRAP: u64 = 1u64 << 32;

#[derive(Default)]
struct SparseRecords {
    lengths: BTreeMap<u64, u32>,
    containers: BTreeSet<u64>,
}

impl SparseRecords {
    fn scan(&self, end: u64) -> Vec<TlvHeader> {
        tlv_headers(
            0,
            end,
            |p| self.lengths.get(&p).copied().unwrap_or(u32::MAX),
            |p| self.containers.contains(&p),
        )
        .unwrap()
    }
}

fn reference_partition(d: &SparseRecords, start: u64, end: u64) -> Option<Vec<TlvHeader>> {
    let mut ordinary = Vec::new();
    let mut p = start;
    while end - p >= 6 {
        let n = u64::from(d.lengths.get(&p).copied().unwrap_or(u32::MAX));
        if n > end - p - 6 {
            break;
        }
        ordinary.push(TlvHeader { offset: p, size: n });
        p += 6 + n;
    }
    if p == end {
        return Some(ordinary);
    }
    for i in 0..ordinary.len() {
        let h = ordinary[i];
        if !d.containers.contains(&h.offset) {
            continue;
        }
        let available = end - h.offset - 6;
        if available - h.size < WRAP {
            continue;
        }
        let mut n = h.size + WRAP;
        while n <= available {
            let next = h.offset + 6 + n;
            if let Some(suffix) = reference_partition(d, next, end) {
                if reference_partition(d, h.offset + 6, next).is_some() {
                    let mut out = ordinary[..=i].to_vec();
                    out[i].size = n;
                    out.extend(suffix);
                    return Some(out);
                }
            }
            if available - n < WRAP {
                break;
            }
            n += WRAP;
        }
    }
    None
}

#[test]
fn model_catalog_disambiguates_two_exact_partitions() {
    let entities = WRAP + 48;
    let end = entities + 6;
    let mut d = SparseRecords {
        lengths: BTreeMap::from([
            (0, 48),
            (6, 36),
            (12, 30),
            (18, 24),
            (24, u32::MAX),
            (WRAP + 29, 13),
            (entities, 0),
            (54, u32::MAX - 5),
        ]),
        containers: BTreeSet::from([0, 6, 12, 18]),
    };
    let ordinary = d.scan(end);
    assert_eq!(ordinary.len(), 2);
    assert_eq!(ordinary.last().unwrap().offset, 54);
    let mut tags = BTreeMap::from([
        (0, 0x01f4u16),
        (6, 0x01f9),
        (12, 0x1770),
        (18, 0x1771),
        (24, 0x157c),
        (WRAP + 29, 0x157c),
        (entities, 0x01f6),
    ]);
    let records = wrapped_model_records(end, |p| tags[&p], |p| d.lengths[&p]).unwrap();
    assert_eq!(records.len(), 3);
    assert_eq!(records[0].offset, 24);
    assert_eq!(records[1].offset, WRAP + 29);
    assert_eq!(records[2].offset, entities);
    *d.lengths.get_mut(&12).unwrap() += 1;
    assert!(wrapped_model_records(end, |p| tags[&p], |p| d.lengths[&p]).is_none());
    *d.lengths.get_mut(&12).unwrap() -= 1;
    tags.insert(entities, 0x157e);
    assert!(wrapped_model_records(end, |p| tags[&p], |p| d.lengths[&p]).is_none());
}

#[test]
fn small_catalog_inside_wrapped_model() {
    let entities = WRAP - 16;
    let end = entities + 70;
    let d = SparseRecords {
        lengths: BTreeMap::from([
            (0, 48),
            (6, (entities - 12) as u32),
            (12, (entities - 18) as u32),
            (18, (entities - 24) as u32),
            (24, (entities - 30) as u32),
            (entities, 64),
            (54, u32::MAX - 5),
        ]),
        containers: BTreeSet::from([0, 6, 12, 18]),
    };
    let tags = BTreeMap::from([
        (0, 0x01f4u16),
        (6, 0x01f9),
        (12, 0x1770),
        (18, 0x1771),
        (24, 0x157c),
        (entities, 0x01f6),
    ]);
    let ordinary = d.scan(end);
    assert_eq!(ordinary.len(), 2);
    assert_eq!(ordinary.last().unwrap().offset, 54);
    let records = wrapped_model_records(end, |p| tags[&p], |p| d.lengths[&p]).unwrap();
    assert_eq!(records.len(), 2);
    assert_eq!(records.first().unwrap().offset, 24);
    assert_eq!(records.last().unwrap().offset, entities);
}

#[test]
fn preserves_complete_short_model_followed_by_top_level_records() {
    let end = WRAP + 54;
    let d = SparseRecords {
        lengths: BTreeMap::from([
            (0, 48),
            (6, 42),
            (12, 36),
            (18, 30),
            (24, 24),
            (54, u32::MAX - 5),
        ]),
        containers: BTreeSet::from([0, 6, 12, 18]),
    };
    let tags = BTreeMap::from([
        (0, 0x01f4u16),
        (6, 0x01f9),
        (12, 0x1770),
        (18, 0x1771),
        (24, 0x157c),
        (54, 0x01f6),
    ]);
    let ordinary = d.scan(end);
    assert_eq!(ordinary.len(), 2);
    assert_eq!(ordinary.last().unwrap().offset, 54);
    assert!(wrapped_model_records(end, |p| tags[&p], |p| d.lengths[&p]).is_none());
}

#[test]
fn extra_top_level_definitions_do_not_extend_a_complete_catalog() {
    let entities = WRAP + 48;
    let end = entities + 6;
    let d = SparseRecords {
        lengths: BTreeMap::from([
            (0, 48),
            (6, 36),
            (12, 30),
            (18, 24),
            (24, 18),
            (48, 0),
            (54, u32::MAX - 11),
            (entities, 0),
        ]),
        containers: BTreeSet::from([0, 6, 12, 18]),
    };
    let tags = BTreeMap::from([
        (0, 0x01f4u16),
        (6, 0x01f9),
        (12, 0x1770),
        (18, 0x1771),
        (24, 0x157c),
        (48, 0x157c),
        (54, 0x157c),
        (entities, 0x01f6),
    ]);
    let ordinary = d.scan(end);
    assert_eq!(ordinary.len(), 3);
    assert_eq!(ordinary[1].offset, 54);
    assert!(wrapped_model_records(end, |p| tags[&p], |p| d.lengths[&p]).is_none());
}

#[test]
fn nested_containers_and_following_sibling() {
    let mut d = SparseRecords::default();
    let mut p = 0u64;
    while p < 24 {
        d.lengths.insert(p, (WRAP + 30 - p - 6) as u32);
        d.containers.insert(p);
        p += 6;
    }
    d.lengths.insert(24, u32::MAX);
    d.lengths.insert(WRAP + 29, 1);
    let mut p = 0u64;
    while p < 24 {
        *d.lengths.get_mut(&p).unwrap() += 6;
        p += 6;
    }
    d.lengths.insert(WRAP + 36, 0);
    let records = d.scan(WRAP + 42);
    assert_eq!(records.len(), 2);
    assert_eq!(records[0].size, WRAP + 30);
    assert_eq!(records[1].offset, WRAP + 36);
}

#[test]
fn multiple_wraps() {
    let d = SparseRecords {
        lengths: BTreeMap::from([(0, 10), (6, u32::MAX), (WRAP + 11, u32::MAX)]),
        containers: BTreeSet::from([0]),
    };
    let records = d.scan(2 * WRAP + 16);
    assert_eq!(records.len(), 1);
    assert_eq!(records[0].size, 2 * WRAP + 10);
}

#[test]
fn rejects_invalid_children_and_suffix_and_opaque_leaves() {
    let mut d = SparseRecords {
        lengths: BTreeMap::from([(0, 5), (6, u32::MAX)]),
        containers: BTreeSet::from([0]),
    };
    let mut records = d.scan(WRAP + 11);
    assert_eq!(records.len(), 1);
    assert_eq!(records[0].size, WRAP + 5);
    d.lengths.insert(6, u32::MAX - 1);
    records = d.scan(WRAP + 11);
    assert_eq!(records[0].size, 5);
    d.lengths.insert(6, u32::MAX);
    records = d.scan(WRAP + 12);
    assert_eq!(records[0].size, 5);
    d.containers.clear();
    records = d.scan(WRAP + 11);
    assert_eq!(records[0].size, 5);
}

#[test]
fn keeps_an_exact_large_partition_and_nonzero_start() {
    let d = SparseRecords {
        lengths: BTreeMap::from([(0, u32::MAX), (WRAP + 5, 0)]),
        containers: BTreeSet::from([0]),
    };
    let records = d.scan(WRAP + 11);
    assert_eq!(records.len(), 2);
    assert_eq!(records[0].size, u64::from(u32::MAX));
    assert_eq!(records[1].offset, WRAP + 5);
    let nested = tlv_headers(
        7,
        WRAP + 18,
        |p| if p == 7 { 5 } else { u32::MAX },
        |p| p == 7,
    )
    .unwrap();
    assert_eq!(
        nested,
        vec![TlvHeader {
            offset: 7,
            size: WRAP + 5,
        }]
    );
}

#[test]
fn zero_filled_payload_does_not_exhaust_recovery() {
    let padding_headers = 1_000_005u64;
    for nested in [false, true] {
        let mut reads = 0u64;
        let leaf = if nested { 12u64 } else { 6u64 };
        let records = tlv_headers(
            0,
            WRAP + leaf + 5,
            |p| {
                reads += 1;
                if p == 0 {
                    (leaf - 1) as u32
                } else if nested && p == 6 {
                    5
                } else if p == leaf {
                    u32::MAX
                } else if (leaf + 5..leaf + 5 + 6 * padding_headers).contains(&p) {
                    0
                } else {
                    u32::MAX
                }
            },
            |p| p == 0 || (nested && p == 6),
        )
        .unwrap();
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].size, WRAP + leaf - 1);
        let limit = (if nested { 2 } else { 1 }) * padding_headers + 20;
        assert!(reads < limit);
    }
}

#[test]
fn reports_attempt_exhaustion_instead_of_truncation() {
    let mut reads = 0u64;
    let err = tlv_headers(
        0,
        WRAP + 1801,
        |p| {
            reads += 1;
            if p < 1800 {
                0
            } else {
                u32::MAX
            }
        },
        |_| true,
    )
    .unwrap_err();
    assert_eq!(err.kind, ErrorKind::DepthLimit);
    assert!(err.message.contains("recovery exceeded"));
    assert!(reads < 1000);
}

#[test]
fn reuses_successful_and_failed_suffixes_across_parent_ends() {
    for count in [100_000u64, 1_000_000] {
        for trailing in 0..6u64 {
            for start in [0u64, 7] {
                let mut reads = 0u64;
                let records = tlv_headers(
                    start,
                    start + WRAP + 6 * count + trailing,
                    |p| {
                        reads += 1;
                        let rel = p - start;
                        if !(1536..WRAP).contains(&rel) {
                            0
                        } else {
                            u32::MAX
                        }
                    },
                    |p| p - start < 1536,
                )
                .unwrap();
                let expected = (count + u64::from(trailing == 5)) as usize;
                assert_eq!(records.len(), expected);
                assert_eq!(records[0].size, 0);
                assert_eq!(records[256].size, u64::from(u32::MAX));
                assert!(reads < 2 * count + 4096);
            }
        }
    }
}

#[test]
fn reuses_long_children_as_well_as_long_suffixes() {
    let count = 100_000u64;
    let mut reads = 0u64;
    let records = tlv_headers(
        0,
        WRAP + 12 * count + 1542,
        |p| {
            reads += 1;
            if !(6 * count + 1536..WRAP).contains(&p) {
                0
            } else {
                u32::MAX
            }
        },
        |p| p < 1536,
    )
    .unwrap();
    assert!(!records.is_empty());
    assert_eq!(records[0].size, 0);
    assert!(reads < 4 * count + 4096);
}

#[test]
fn recovery_near_size_t_maximum() {
    let end = u64::MAX;
    let start = end - WRAP - 11;
    let records = tlv_headers(
        start,
        end,
        |p| if p == start { 5 } else { u32::MAX },
        |p| p == start,
    )
    .unwrap();
    assert_eq!(
        records,
        vec![TlvHeader {
            offset: start,
            size: WRAP + 5,
        }]
    );
}

#[test]
fn recovery_depth_boundary_is_explicit() {
    for depth in [64u64, 65] {
        let end = WRAP + 6 * depth + 5;
        let result = tlv_headers(
            0,
            end,
            |p| {
                if p < 6 * depth && p.is_multiple_of(6) {
                    (end - p - 6) as u32
                } else {
                    u32::MAX
                }
            },
            |p| p < 6 * depth && p.is_multiple_of(6),
        );
        if depth == 64 {
            let records = result.unwrap();
            assert_eq!(
                records,
                vec![TlvHeader {
                    offset: 0,
                    size: end - 6,
                }]
            );
        } else {
            assert_eq!(result.unwrap_err().kind, ErrorKind::DepthLimit);
        }
    }
}

#[test]
fn matches_uncached_reference_across_nested_and_corrupt_ranges() {
    for depth in 1..=3u64 {
        for wraps in 1..=3u64 {
            for sibling in [false, true] {
                for corruption in [-1i32, 0, 1] {
                    let mut d = SparseRecords::default();
                    let mut p = 6 * depth;
                    for _ in 0..wraps {
                        d.lengths.insert(p, u32::MAX);
                        p += WRAP + 5;
                    }
                    d.lengths.insert(p, 7);
                    let inner_end = p + 13;
                    for i in 0..depth {
                        d.lengths.insert(6 * i, (inner_end - 6 * i - 6) as u32);
                        d.containers.insert(6 * i);
                    }
                    if sibling {
                        d.lengths.insert(inner_end, 3);
                    }
                    d.lengths.insert(p, (7 + corruption) as u32);
                    let end = inner_end + if sibling { 9 } else { 0 };
                    let expected = reference_partition(&d, 0, end).unwrap_or_else(|| {
                        let mut ordinary = Vec::new();
                        let mut q = 0u64;
                        while end - q >= 6 {
                            let n = u64::from(d.lengths.get(&q).copied().unwrap_or(u32::MAX));
                            if n > end - q - 6 {
                                break;
                            }
                            ordinary.push(TlvHeader { offset: q, size: n });
                            q += 6 + n;
                        }
                        ordinary
                    });
                    assert_eq!(d.scan(end), expected);
                }
            }
        }
    }
}

#[test]
fn cached_failure_does_not_hide_an_expandable_container() {
    let d = SparseRecords {
        lengths: BTreeMap::from([(0, 11), (6, 5), (12, u32::MAX)]),
        containers: BTreeSet::from([0, 6]),
    };
    let records = d.scan(WRAP + 17);
    assert_eq!(
        records,
        vec![TlvHeader {
            offset: 0,
            size: WRAP + 11,
        }]
    );
}

#[test]
fn preserves_exact_and_small_lenient_ranges() {
    let d = SparseRecords {
        lengths: BTreeMap::from([(0, 3), (9, 0)]),
        containers: BTreeSet::from([0]),
    };
    assert_eq!(d.scan(15).len(), 2);
    assert_eq!(d.scan(16).len(), 2);
    assert!(d.scan(8).is_empty());
    assert!(d.scan(0).is_empty());
    let bytes = [0xF4u8, 0x01, 6, 0, 0, 0, 0xF6, 0x01, 0, 0, 0, 0];
    let nodes = parse_tlv_recursive(&bytes, 0, bytes.len()).unwrap();
    assert_eq!(nodes.len(), 1);
    assert_eq!(nodes[0].children.len(), 1);
    assert_eq!(nodes[0].children[0].tag, "F601");
    assert_eq!(
        tlv_headers_slice(&bytes, 0, bytes.len() + 1)
            .unwrap_err()
            .kind,
        ErrorKind::OutOfRange
    );
}
