"""Classic (MFC archive) reader recursion limits (#557)."""
from __future__ import annotations

import struct

import pytest

from openskp import SkpFile, create
from openskp.errors import SkpParseError

MAX_LEGACY_DEPTH = 64


def _u16(x: int) -> bytes:
    return struct.pack("<H", x)


def _u32(x: int) -> bytes:
    return struct.pack("<I", x)


def _wstr(s: str) -> bytes:
    b = s.encode("utf-16-le")
    return b"\xff\xfe\xff" + bytes([len(s)]) + b


def _cdecl(name: str, schema: int = 0) -> bytes:
    b = name.encode("ascii")
    return _u16(0xFFFF) + _u16(schema) + _u16(len(b)) + b


def make_nested_object_skp(total_depth: int, *, on_layer: bool = False) -> bytes:
    b = create()
    b.add_construction_point((1.0, 2.0, 3.0))
    raw = b.to_bytes()
    if on_layer:
        pos = raw.index(b"CLayer") + len(b"CLayer")
        attr_cls_slot = 11
    else:
        pos = raw.index(b"CConstructionPoint") + len(b"CConstructionPoint")
        attr_cls_slot = 13
    assert raw[pos : pos + 2] == b"\x00\x00"
    if total_depth <= 1:
        return raw
    k = total_depth - 1
    chain = bytearray(_cdecl("CAttributeContainer"))
    for _ in range(k - 1):
        chain += _u16(0x8000 | attr_cls_slot)
    chain += _u16(0)
    for _ in range(k):
        chain += b"\x00" + _u16(0)
    return raw[:pos] + bytes(chain) + raw[pos + 2 :]


def make_nested_attr_array_skp(array_depth: int) -> bytes:
    b = create()
    b.add_construction_point((1.0, 2.0, 3.0))
    raw = b.to_bytes()
    pos = raw.index(b"CConstructionPoint") + len(b"CConstructionPoint")
    assert raw[pos : pos + 2] == b"\x00\x00"
    payload = bytearray()
    payload += _cdecl("CAttributeContainer") + _u16(0) + b"\x00"
    payload += _cdecl("CAttributeNamed") + _u16(0) + b"\x00" + b"\x00" * 4 + _wstr("Dict")
    payload += _wstr("k")
    for _ in range(array_depth):
        payload += bytes([0x0B]) + _u32(1)
    payload += bytes([0x04]) + struct.pack("<i", 7)
    payload += _wstr("") + _u32(0) + _u16(0)
    return raw[:pos] + bytes(payload) + raw[pos + 2 :]


@pytest.mark.parametrize("on_layer", [False, True])
@pytest.mark.parametrize("depth", [1, 10, MAX_LEGACY_DEPTH])
def test_classic_object_nesting_within_limit_parses(tmp_path, depth, on_layer):
    path = tmp_path / "ok.skp"
    path.write_bytes(make_nested_object_skp(depth, on_layer=on_layer))
    model = SkpFile.open(path).parse()
    assert model.unread_root_entities == 0
    assert len(model.root.construction_points) == 1


@pytest.mark.parametrize("on_layer", [False, True])
@pytest.mark.parametrize("depth", [MAX_LEGACY_DEPTH + 1, 5000])
def test_classic_object_nesting_above_limit_raises_parse_error(tmp_path, depth, on_layer):
    path = tmp_path / "deep.skp"
    path.write_bytes(make_nested_object_skp(depth, on_layer=on_layer))
    with pytest.raises(SkpParseError, match="depth") as caught:
        SkpFile.open(path).parse()
    assert caught.value.stage == "legacy_walk"


@pytest.mark.parametrize("depth", [1, 10, MAX_LEGACY_DEPTH])
def test_classic_attr_array_nesting_within_limit_parses(tmp_path, depth):
    path = tmp_path / "ok_arr.skp"
    path.write_bytes(make_nested_attr_array_skp(depth))
    model = SkpFile.open(path).parse()
    assert model.unread_root_entities == 0
    assert len(model.root.construction_points) == 1


@pytest.mark.parametrize("depth", [MAX_LEGACY_DEPTH + 1, 5000])
def test_classic_attr_array_nesting_above_limit_raises_parse_error(tmp_path, depth):
    path = tmp_path / "deep_arr.skp"
    path.write_bytes(make_nested_attr_array_skp(depth))
    with pytest.raises(SkpParseError, match="depth") as caught:
        SkpFile.open(path).parse()
    assert caught.value.stage == "legacy_walk"
