"""Tests for >= 4 GiB VFF model.dat 32-bit container length wrap recovery (#520, #535)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import pytest

from openskp import _core
from openskp._core import parse_tlv_recursive, tlv_headers, tlv_headers_buffer, wrapped_model_records
from openskp.errors import SkpParseError

WRAP = 1 << 32
UINT32_MAX = 0xFFFFFFFF


@dataclass
class SparseRecords:
    lengths: Dict[int, int] = field(default_factory=dict)
    containers: Set[int] = field(default_factory=set)

    def scan(self, end: int) -> List[Tuple[int, int]]:
        return tlv_headers(
            0,
            end,
            lambda p: self.lengths.get(p, UINT32_MAX),
            lambda p: p in self.containers,
        )


def _reference_partition(
    d: SparseRecords, start: int, end: int
) -> Optional[List[Tuple[int, int]]]:
    ordinary: List[Tuple[int, int]] = []
    p = start
    while end - p >= 6:
        n = d.lengths.get(p, UINT32_MAX)
        if n > end - p - 6:
            break
        ordinary.append((p, n))
        p += 6 + n
    if p == end:
        return ordinary
    for i, (h_off, h_size) in enumerate(ordinary):
        if h_off not in d.containers:
            continue
        available = end - h_off - 6
        if available - h_size < WRAP:
            continue
        n = h_size + WRAP
        while n <= available:
            nxt = h_off + 6 + n
            suffix = _reference_partition(d, nxt, end)
            if suffix is not None and _reference_partition(d, h_off + 6, nxt) is not None:
                return ordinary[:i] + [(h_off, n)] + suffix
            if available - n < WRAP:
                break
            n += WRAP
    return None


def test_model_catalog_disambiguates_two_exact_partitions() -> None:
    entities = WRAP + 48
    end = entities + 6
    d = SparseRecords(
        lengths={
            0: 48,
            6: 36,
            12: 30,
            18: 24,
            24: UINT32_MAX,
            WRAP + 29: 13,
            entities: 0,
            54: UINT32_MAX - 5,
        },
        containers={0, 6, 12, 18},
    )
    ordinary = d.scan(end)
    assert len(ordinary) == 2
    assert ordinary[-1][0] == 54
    tags = {
        0: 0x01F4,
        6: 0x01F9,
        12: 0x1770,
        18: 0x1771,
        24: 0x157C,
        WRAP + 29: 0x157C,
        entities: 0x01F6,
    }

    def read():
        return wrapped_model_records(end, lambda p: tags[p], lambda p: d.lengths[p])

    records = read()
    assert records is not None
    assert [r[0] for r in records] == [24, WRAP + 29, entities]
    d.lengths[12] += 1
    assert read() is None
    d.lengths[12] -= 1
    tags[entities] = 0x157E
    assert read() is None


def test_small_catalog_inside_wrapped_model() -> None:
    entities = WRAP - 16
    end = entities + 70
    d = SparseRecords(
        lengths={
            0: 48,
            6: (entities - 12) & UINT32_MAX,
            12: (entities - 18) & UINT32_MAX,
            18: (entities - 24) & UINT32_MAX,
            24: (entities - 30) & UINT32_MAX,
            entities: 64,
            54: UINT32_MAX - 5,
        },
        containers={0, 6, 12, 18},
    )
    tags = {
        0: 0x01F4,
        6: 0x01F9,
        12: 0x1770,
        18: 0x1771,
        24: 0x157C,
        entities: 0x01F6,
    }
    ordinary = d.scan(end)
    assert len(ordinary) == 2
    assert ordinary[-1][0] == 54
    records = wrapped_model_records(end, lambda p: tags[p], lambda p: d.lengths[p])
    assert records is not None
    assert [r[0] for r in records] == [24, entities]


def test_preserves_complete_short_model_followed_by_top_level_records() -> None:
    end = WRAP + 54
    d = SparseRecords(
        lengths={0: 48, 6: 42, 12: 36, 18: 30, 24: 24, 54: UINT32_MAX - 5},
        containers={0, 6, 12, 18},
    )
    tags = {0: 0x01F4, 6: 0x01F9, 12: 0x1770, 18: 0x1771, 24: 0x157C, 54: 0x01F6}
    ordinary = d.scan(end)
    assert len(ordinary) == 2
    assert ordinary[-1][0] == 54
    records = wrapped_model_records(end, lambda p: tags[p], lambda p: d.lengths[p])
    assert records is None


def test_extra_top_level_definitions_do_not_extend_a_complete_catalog() -> None:
    entities = WRAP + 48
    end = entities + 6
    d = SparseRecords(
        lengths={
            0: 48,
            6: 36,
            12: 30,
            18: 24,
            24: 18,
            48: 0,
            54: UINT32_MAX - 11,
            entities: 0,
        },
        containers={0, 6, 12, 18},
    )
    tags = {
        0: 0x01F4,
        6: 0x01F9,
        12: 0x1770,
        18: 0x1771,
        24: 0x157C,
        48: 0x157C,
        54: 0x157C,
        entities: 0x01F6,
    }
    ordinary = d.scan(end)
    assert len(ordinary) == 3
    assert ordinary[1][0] == 54
    assert wrapped_model_records(end, lambda p: tags[p], lambda p: d.lengths[p]) is None


def test_nested_containers_and_following_sibling() -> None:
    d = SparseRecords()
    for p in range(0, 24, 6):
        d.lengths[p] = (WRAP + 30 - p - 6) & UINT32_MAX
        d.containers.add(p)
    d.lengths[24] = UINT32_MAX
    d.lengths[WRAP + 29] = 1
    for p in range(0, 24, 6):
        d.lengths[p] += 6
    d.lengths[WRAP + 36] = 0
    records = d.scan(WRAP + 42)
    assert len(records) == 2
    assert records[0][1] == WRAP + 30
    assert records[1][0] == WRAP + 36


def test_multiple_wraps() -> None:
    d = SparseRecords(
        lengths={0: 10, 6: UINT32_MAX, WRAP + 11: UINT32_MAX},
        containers={0},
    )
    records = d.scan(2 * WRAP + 16)
    assert len(records) == 1
    assert records[0][1] == 2 * WRAP + 10


def test_rejects_invalid_children_and_suffix_and_opaque_leaves() -> None:
    d = SparseRecords(lengths={0: 5, 6: UINT32_MAX}, containers={0})
    records = d.scan(WRAP + 11)
    assert len(records) == 1
    assert records[0][1] == WRAP + 5
    d.lengths[6] = UINT32_MAX - 1
    records = d.scan(WRAP + 11)
    assert records[0][1] == 5
    d.lengths[6] = UINT32_MAX
    records = d.scan(WRAP + 12)
    assert records[0][1] == 5
    d.containers.clear()
    records = d.scan(WRAP + 11)
    assert records[0][1] == 5


def test_keeps_an_exact_large_partition_and_nonzero_start() -> None:
    d = SparseRecords(lengths={0: UINT32_MAX, WRAP + 5: 0}, containers={0})
    records = d.scan(WRAP + 11)
    assert len(records) == 2
    assert records[0][1] == UINT32_MAX
    assert records[1][0] == WRAP + 5
    nested = tlv_headers(
        7,
        WRAP + 18,
        lambda p: 5 if p == 7 else UINT32_MAX,
        lambda p: p == 7,
    )
    assert nested == [(7, WRAP + 5)]


def test_zero_filled_payload_does_not_exhaust_recovery() -> None:
    padding_headers = 1000005
    for nested in (False, True):
        reads = 0
        leaf = 12 if nested else 6

        def length_fn(p: int) -> int:
            nonlocal reads
            reads += 1
            if p == 0:
                return leaf - 1
            if nested and p == 6:
                return 5
            if p == leaf:
                return UINT32_MAX
            if leaf + 5 <= p < leaf + 5 + 6 * padding_headers:
                return 0
            return UINT32_MAX

        records = tlv_headers(
            0,
            WRAP + leaf + 5,
            length_fn,
            lambda p: p == 0 or (nested and p == 6),
        )
        assert len(records) == 1
        assert records[0][1] == WRAP + leaf - 1
        assert reads < (2 if nested else 1) * padding_headers + 20


def test_reports_attempt_exhaustion_instead_of_truncation() -> None:
    reads = 0

    def length_fn(p: int) -> int:
        nonlocal reads
        reads += 1
        return 0 if p < 1800 else UINT32_MAX

    with pytest.raises(SkpParseError, match="recovery exceeded"):
        tlv_headers(0, WRAP + 1801, length_fn, lambda _: True)
    assert reads < 1000


def test_reuses_successful_and_failed_suffixes_across_parent_ends() -> None:
    for count in (10000, 100000):
        for trailing in range(6):
            for start in (0, 7):
                reads = 0

                def length_fn(p: int) -> int:
                    nonlocal reads
                    reads += 1
                    rel = p - start
                    if rel < 1536 or rel >= WRAP:
                        return 0
                    return UINT32_MAX

                records = tlv_headers(
                    start,
                    start + WRAP + 6 * count + trailing,
                    length_fn,
                    lambda p: p - start < 1536,
                )
                assert len(records) == count + (1 if trailing == 5 else 0)
                assert records[0][1] == 0
                assert records[256][1] == UINT32_MAX
                assert reads < 2 * count + 4096


def test_reuses_long_children_as_well_as_long_suffixes() -> None:
    count = 100000
    reads = 0

    def length_fn(p: int) -> int:
        nonlocal reads
        reads += 1
        if p < 6 * count + 1536 or p >= WRAP:
            return 0
        return UINT32_MAX

    records = tlv_headers(
        0,
        WRAP + 12 * count + 1542,
        length_fn,
        lambda p: p < 1536,
    )
    assert records
    assert records[0][1] == 0
    assert reads < 4 * count + 4096


def test_recovery_near_size_t_maximum() -> None:
    end = (1 << 64) - 1
    start = end - WRAP - 11
    records = tlv_headers(
        start,
        end,
        lambda p: 5 if p == start else UINT32_MAX,
        lambda p: p == start,
    )
    assert records == [(start, WRAP + 5)]


def test_recovery_depth_boundary_is_explicit() -> None:
    for depth in (64, 65):
        end = WRAP + 6 * depth + 5

        def scan():
            return tlv_headers(
                0,
                end,
                lambda p: ((end - p - 6) & UINT32_MAX)
                if (p < 6 * depth and p % 6 == 0)
                else UINT32_MAX,
                lambda p: p < 6 * depth and p % 6 == 0,
            )

        if depth == 64:
            records = scan()
            assert records == [(0, end - 6)]
        else:
            with pytest.raises(SkpParseError):
                scan()


def test_matches_uncached_reference_across_nested_and_corrupt_ranges() -> None:
    for depth in (1, 2, 3):
        for wraps in (1, 2, 3):
            for sibling in (False, True):
                for corruption in (-1, 0, 1):
                    d = SparseRecords()
                    p = 6 * depth
                    for _ in range(wraps):
                        d.lengths[p] = UINT32_MAX
                        p += WRAP + 5
                    d.lengths[p] = 7
                    inner_end = p + 13
                    for i in range(depth):
                        d.lengths[6 * i] = (inner_end - 6 * i - 6) & UINT32_MAX
                        d.containers.add(6 * i)
                    if sibling:
                        d.lengths[inner_end] = 3
                    d.lengths[p] = (7 + corruption) & UINT32_MAX
                    end = inner_end + (9 if sibling else 0)
                    expected = _reference_partition(d, 0, end)
                    if expected is None:
                        expected = []
                        q = 0
                        while end - q >= 6:
                            n = d.lengths.get(q, UINT32_MAX)
                            if n > end - q - 6:
                                break
                            expected.append((q, n))
                            q += 6 + n
                    actual = d.scan(end)
                    assert actual == expected


def test_cached_failure_does_not_hide_an_expandable_container() -> None:
    d = SparseRecords(lengths={0: 11, 6: 5, 12: UINT32_MAX}, containers={0, 6})
    records = d.scan(WRAP + 17)
    assert len(records) == 1
    assert records[0][1] == WRAP + 11


def test_preserves_exact_and_small_lenient_ranges() -> None:
    d = SparseRecords(lengths={0: 3, 9: 0}, containers={0})
    assert len(d.scan(15)) == 2
    assert len(d.scan(16)) == 2
    assert len(d.scan(8)) == 0
    assert d.scan(0) == []
    raw = bytes([0xF4, 0x01, 6, 0, 0, 0, 0xF6, 0x01, 0, 0, 0, 0])
    nodes = parse_tlv_recursive(raw, 0, len(raw))
    assert len(nodes) == 1
    assert len(nodes[0].children) == 1
    assert nodes[0].children[0].tag == "F601"
    with pytest.raises(ValueError):
        tlv_headers_buffer(raw, 0, len(raw) + 1)
    assert len(list(_core.iter_top_level_lazy(raw, 0, len(raw)))) == 1
