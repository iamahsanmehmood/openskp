"""Unit tests for the VFF container primitives.

Covers the independent building blocks the VFF writer is assembled from:
the record-tree encoding (:mod:`openskp._vff_writer.tlv`) and the embedded
ZIP archive (:mod:`openskp._vff_writer.archive`).
"""

from __future__ import annotations

from pathlib import Path


from openskp._vff_writer.archive import (
    ZipEntry,
    build_zip,
    read_zip,
)
from openskp._vff_writer.tlv import Record, encode_records, parse_records, record

FIXTURES = Path(__file__).parent / "fixtures"
WRITER_FIXTURES = FIXTURES / "vff_writer"


def test_tlv_records_round_trip():
    tree = Record(
        0x01F4,
        children=[
            record(0x01F5, b"abc"),
            Record(0x01F6, children=[record(0x05DE, b"\x01")]),
        ],
    )
    payload = encode_records([tree])
    parsed = parse_records(payload, {0x01F4, 0x01F6})
    assert len(parsed) == 1
    assert parsed[0].tag == 0x01F4
    assert parsed[0].children[0].tag == 0x01F5
    assert parsed[0].children[0].payload == b"abc"


def test_zip_round_trip_and_crc():
    entries = [
        ZipEntry("a.txt", b"hello", 0x0006),
        ZipEntry("b.bin", b"\x00\x01\x02", 0x0000),
    ]
    data = build_zip(entries)
    parsed = read_zip(data, 0)
    assert [entry.name for entry in parsed] == ["a.txt", "b.bin"]
    assert [entry.data for entry in parsed] == [b"hello", b"\x00\x01\x02"]
