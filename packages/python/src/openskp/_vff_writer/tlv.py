"""Minimal record-tree primitives for the SketchUp VFF ``model.dat`` format.

A VFF ``model.dat`` is a single record tree: every record is a 16-bit tag, a
32-bit payload length, and the payload; a container's payload is exactly a
sequence of child records.  Tags are stored little-endian, so the two bytes
``f4 01`` are the tag written here as ``0x01f4``.

Nothing in this module is specific to any one template: a record is kept as
opaque bytes unless its tag is one of the container tags the caller asks to
walk into, and an existing tree re-encodes byte-for-byte when it is not
edited at all.

The format facts used here (record framing, variable-width ids, the meaning
of the tags) were checked against files written by SketchUp 2025 itself; see
``research/vff-writer/notes.md``.
"""

from __future__ import annotations

import struct
from typing import Iterable, List, Optional, Sequence

__all__ = [
    "Record",
    "parse_records",
    "encode_records",
    "record",
    "u8",
    "u32",
    "f64s",
    "varint",
    "read_varint",
    "id_list",
    "newell_normal",
    "unit_normal",
]

_HEADER = struct.Struct("<HI")


class Record:
    """One TLV record.

    A record is either a leaf (``payload`` holds its bytes) or a container
    (``children`` holds its sub-records, and the payload is re-derived when
    the tree is encoded).  Exactly one of the two is set.
    """

    __slots__ = ("tag", "payload", "children")

    def __init__(
        self,
        tag: int,
        payload: bytes = b"",
        children: Optional[List["Record"]] = None,
    ) -> None:
        if not 0 <= tag <= 0xFFFF:
            raise ValueError(f"record tag out of range: {tag!r}")
        self.tag = tag
        if children is not None:
            self.payload = b""
            self.children = children
        else:
            self.payload = bytes(payload)
            self.children = None

    @property
    def is_container(self) -> bool:
        return self.children is not None

    def value(self) -> bytes:
        """Return the encoded payload (children encoded first, when any)."""
        if self.children is None:
            return self.payload
        return b"".join(child.encode() for child in self.children)

    def encode(self) -> bytes:
        body = self.value()
        return _HEADER.pack(self.tag, len(body)) + body

    def child(self, tag: int) -> Optional["Record"]:
        """First direct child with *tag*, or ``None``."""
        if self.children is None:
            return None
        for candidate in self.children:
            if candidate.tag == tag:
                return candidate
        return None

    def children_with(self, tag: int) -> List["Record"]:
        if self.children is None:
            return []
        return [child for child in self.children if child.tag == tag]

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        kind = "container" if self.is_container else "leaf"
        return f"Record(0x{self.tag:04x}, {kind}, {len(self.value())} bytes)"


def _try_parse(data: bytes, start: int, end: int, containers, depth: int):
    """Parse ``[start, end)`` as a record sequence, or return ``None``.

    A payload that does not tile exactly into records is left as opaque leaf
    bytes by the caller, which keeps unknown and future record layouts intact.
    """
    if depth > 64:
        return None
    records: List[Record] = []
    pos = start
    while pos < end:
        if end - pos < _HEADER.size:
            return None
        tag, size = _HEADER.unpack_from(data, pos)
        body_start = pos + _HEADER.size
        body_end = body_start + size
        if body_end > end:
            return None
        children = None
        if size and tag in containers:
            children = _try_parse(data, body_start, body_end, containers, depth + 1)
        if children is None:
            records.append(Record(tag, data[body_start:body_end]))
        else:
            records.append(Record(tag, children=children))
        pos = body_end
    return records


def parse_records(
    data: bytes,
    containers: Iterable[int] = (),
    *,
    strict: bool = True,
) -> List[Record]:
    """Parse *data* as a sequence of VFF records.

    ``containers`` names the tags whose payload should be walked into; every
    other tag is kept as opaque bytes.  With ``strict`` the whole buffer has
    to decode as records (the usual case for a top-level ``model.dat`` or a
    container payload); a container whose payload does not decode exactly is
    kept as a leaf instead of raising, which is what lets an unknown newer
    layout survive a round trip.
    """
    container_set = frozenset(containers)
    records = _try_parse(data, 0, len(data), container_set, 0)
    if records is None:
        if strict:
            raise ValueError("data does not decode as VFF records")
        return [Record(0, data)]
    return records


def encode_records(records: Sequence[Record]) -> bytes:
    return b"".join(r.encode() for r in records)


def record(tag: int, payload: bytes = b"") -> Record:
    """A leaf record."""
    return Record(tag, bytes(payload))


def u8(value: int) -> bytes:
    return bytes([value & 0xFF])


def u32(value: int) -> bytes:
    return struct.pack("<I", value & 0xFFFFFFFF)


def f64s(values: Sequence[float]) -> bytes:
    return struct.pack("<%dd" % len(values), *values)


def varint(value: int) -> bytes:
    """Little-endian id/integer, exactly as wide as the value needs (1-8)."""
    if value < 0:
        raise ValueError("ids are unsigned")
    width = max(1, (value.bit_length() + 7) // 8)
    return value.to_bytes(width, "little")


def read_varint(payload: bytes) -> int:
    return int.from_bytes(payload, "little")


def id_list(ids: Iterable[int]) -> bytes:
    """Encode the ``<width:u8> <id>`` list layout of a ``0x138e`` record."""
    return b"".join(bytes([len(v)]) + v for v in (varint(i) for i in ids))


def newell_normal(points: Sequence[Sequence[float]]) -> List[float]:
    """Polygon normal from the vertex order (Newell's method)."""
    nx = ny = nz = 0.0
    count = len(points)
    for index in range(count):
        x1, y1, z1 = points[index]
        x2, y2, z2 = points[(index + 1) % count]
        nx += (y1 - y2) * (z1 + z2)
        ny += (z1 - z2) * (x1 + x2)
        nz += (x1 - x2) * (y1 + y2)
    return [nx, ny, nz]


def unit_normal(normal: Sequence[float]) -> List[float]:
    length = (normal[0] ** 2 + normal[1] ** 2 + normal[2] ** 2) ** 0.5
    if length == 0:
        raise ValueError("degenerate polygon: zero-area normal")
    return [normal[0] / length, normal[1] / length, normal[2] / length]