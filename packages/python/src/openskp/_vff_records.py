"""Decode common geometry layouts without their temporary TLV trees.

These are exact layout recognizers, not a second general parser. Additional
fields, materials, UVs, layers, holes, mixed coedge integer widths and malformed
lengths fall back to the existing tree decoder. Integer widths 1..8 are handled.
"""

import struct

_FLAGS = b"\xd3\x07\x01\0\0\0"
_POSITION = b"\xc5\x09\x18\0\0\0"
_ORIENTATION = b"\xa2\x0f\x01\0\0\0"
_HEADERS = struct.Struct("<HIHIHI").unpack_from
_HEADER = struct.Struct("<HI").unpack_from
_NORMAL = struct.Struct("<3d").unpack_from
_U32 = struct.Struct("<I").unpack_from
_IDS = {n: struct.pack("<HIHI", 0x05DC, 6 + n, 0x05DE, n) for n in range(1, 9)}
_DRAWBASES = {n: struct.pack("<HI", 0x07D0, 19 + n) + p for n, p in _IDS.items()}
_COEDGE_HEADERS = {n: struct.pack("<HIHI", 0x0FA0, 13 + n, 0x0FA1, n) for n in _IDS}
_COEDGES = {
    n: struct.Struct("<12s" + ("I" if n == 4 else f"{n}s") + "6sB").iter_unpack
    for n in _IDS
}


def _integer(data, start, width):
    if width == 4:
        return _U32(data, start)[0]
    return int.from_bytes(data[start : start + width], "little")


def decode_record(data, start, size, tag):
    """Return decoded fields for an exact common layout, otherwise None."""
    if tag == "C409":
        width = size - 42
        prefix = _IDS.get(width)
        if (
            prefix is None
            or data[start : start + 12] != prefix
            or data[start + 12 + width : start + 18 + width] != _POSITION
        ):
            return None
        entity_id = _integer(data, start + 12, width)
        return (
            entity_id,
            _NORMAL(data, start + 18 + width),
            data[start + 12 : start + 12 + width].hex(),
        )

    if tag not in ("B80B", "AC0D") or size < 40:
        return None
    width = _U32(data, start + 14)[0]
    prefix = _DRAWBASES.get(width)
    if (
        prefix is None
        or data[start : start + 18] != prefix
        or data[start + 18 + width : start + 24 + width] != _FLAGS
    ):
        return None
    end = start + size
    flags = data[start + 24 + width]
    following = start + 25 + width

    if tag == "B80B":
        if following + 6 > end:
            return None
        first_tag, first_width = _HEADER(data, following)
        if first_tag != 0x0BB9 or not 1 <= first_width <= 8:
            return None
        second = following + 6 + first_width
        if second + 6 > end:
            return None
        second_tag, second_width = _HEADER(data, second)
        if (
            second_tag != 0x0BBA
            or not 1 <= second_width <= 8
            or second + 6 + second_width != end
        ):
            return None
        return (
            _integer(data, start + 18, width),
            flags,
            _integer(data, following + 6, first_width),
            _integer(data, second + 6, second_width),
        )

    if size < 94:
        return None
    normal_header = data[following : following + 6]
    if normal_header == b"\xad\x0d\x20\0\0\0":
        loops_start = following + 38
    elif normal_header == b"\xad\x0d\x18\0\0\0":
        loops_start = following + 30
    else:
        return None
    coedges_start = loops_start + 18
    length = end - coedges_start
    if length < 20:
        return None
    edge_width = _U32(data, coedges_start + 8)[0]
    coedge_prefix = _COEDGE_HEADERS.get(edge_width)
    if coedge_prefix is None or length % (19 + edge_width):
        return None
    if _HEADERS(data, loops_start) != (
        0x0DAE,
        length + 12,
        0x1194,
        length + 6,
        0x1195,
        length,
    ):
        return None
    loop = []
    for head, edge_id, tail, orientation in _COEDGES[edge_width](
        memoryview(data)[coedges_start:end]
    ):
        if head != coedge_prefix or tail != _ORIENTATION:
            return None
        if edge_width != 4:
            edge_id = int.from_bytes(edge_id, "little")
        loop.append((edge_id, 1 if orientation == 0 else -1))
    return (
        _integer(data, start + 18, width),
        _NORMAL(data, following + 6),
        tuple(loop),
        bool(flags & 0x01),
    )
