"""Tests for canonical component/instance GUIDs and explicit writer UUIDs (#478)."""
from __future__ import annotations

import io
import pathlib
import struct
import zipfile

import pytest

from openskp import SkpFile, create
from openskp.create import SkpWriteError

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
SQUARE_PTS = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)]


def _tlv(tag_hex: str, payload: bytes) -> bytes:
    tag_bytes = bytes.fromhex(tag_hex)
    return tag_bytes + struct.pack("<I", len(payload)) + payload


def _parse_bytes(tmp_path: pathlib.Path, name: str, data: bytes):
    p = tmp_path / name
    p.write_bytes(data)
    return SkpFile.open(str(p)).parse()


def test_component_guid_matches_across_classic_and_vff_containers() -> None:
    for filename in ("nesting_2017.skp", "nesting_2020.skp", "nesting_2021.skp"):
        model = SkpFile.open(str(FIXTURES / filename)).parse()
        shelves = [d for d in model.definitions.values() if d.name == "Shelf"]
        assert len(shelves) == 1
        assert shelves[0].guid == "1614E9C58C0B40338881DDE62E6E2D80"
        references = 0
        for defn in [model.root, *model.definitions.values()]:
            for inst in defn.instances:
                if inst.ref_idx is None:
                    continue
                assert inst.ref_idx in model.definitions
                references += 1
        assert references > 0
        if filename == "nesting_2021.skp":
            assert len(model.root.instances) == 1
            assert model.root.instances[0].guid == "0BFF75653E97458E9B7238A6E9CF1A99"


def test_vff_definition_and_instance_guids_use_canonical_uuid_order(tmp_path: pathlib.Path) -> None:
    native = bytes([
        0xC5, 0xE9, 0x14, 0x16, 0x0B, 0x8C, 0x33, 0x40,
        0x88, 0x81, 0xDD, 0xE6, 0x2E, 0x6E, 0x2D, 0x80,
    ])
    canonical = "1614E9C58C0B40338881DDE62E6E2D80"
    model_dat = (
        _tlv("7C15", _tlv("DE05", b"\x01") + _tlv("7D15", native) + _tlv("7E15", b"Shelf"))
        + _tlv("F601", _tlv("6419", _tlv("6819", native)))
    )
    vff_prefix = (
        b"\xff\xfe\xff\x0e"
        + "SketchUp Model".encode("utf-16-le")
        + b"\xff\xfe\xff\x08"
        + "{21.0.1}".encode("utf-16-le")
    )
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("model.dat", model_dat)
    vff_path = tmp_path / "synthetic_vff.skp"
    vff_path.write_bytes(vff_prefix + zip_buf.getvalue())
    model = SkpFile.open(str(vff_path)).parse()
    assert len(model.definitions) == 1
    assert model.definitions[1].guid == canonical
    assert len(model.root.instances) == 1
    assert model.root.instances[0].guid == canonical
    assert model.root.instances[0].ref_idx is None


def test_vff_definition_dictionaries_preserve_aa38_booleans(tmp_path: pathlib.Path) -> None:
    def entry(key: str, val_node: bytes) -> bytes:
        return _tlv("B636", key.encode("utf-8")) + _tlv("A438", val_node)

    def named_dict(name: str, entries_payload: bytes) -> bytes:
        return _tlv("B436", name.encode("utf-8")) + _tlv("B536", entries_payload)

    values = (
        entry("count", _tlv("A738", struct.pack("<i", -7)))
        + entry("flag", _tlv("AA38", b"\x01"))
        + entry("disabled", _tlv("AA38", b"\x00"))
        + entry("nothing", b"")
    )
    own = _tlv("8813", _tlv("D007", _tlv("DC05", named_dict("dynamic_attributes", values))))
    inst = _tlv(
        "6419",
        _tlv("6719", b"\x01") + _tlv("D007", _tlv("DC05", named_dict("dynamic_attributes", values))),
    )
    model_dat = _tlv("7C15", _tlv("DE05", b"\x01") + _tlv("7E15", b"BoolDef") + own) + _tlv("F601", inst)
    vff_prefix = (
        b"\xff\xfe\xff\x0e"
        + "SketchUp Model".encode("utf-16-le")
        + b"\xff\xfe\xff\x08"
        + "{21.0.1}".encode("utf-16-le")
    )
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("model.dat", model_dat)
    vff_path = tmp_path / "synthetic_vff_bools.skp"
    vff_path.write_bytes(vff_prefix + zip_buf.getvalue())
    model = SkpFile.open(str(vff_path)).parse()
    attrs = model.definitions[1].attribute_dictionaries["dynamic_attributes"]
    assert attrs["flag"] is True
    assert attrs["disabled"] is False
    assert attrs["count"] == -7
    assert attrs["nothing"] is None
    assert model.root.instances[0].properties["flag"] == "1"
    assert model.root.instances[0].properties["disabled"] == "0"


def test_explicit_model_guid_uses_native_byte_order() -> None:
    builder = create()
    builder.add_face(SQUARE_PTS)
    native = bytes([
        0x33, 0x22, 0x11, 0x00, 0x55, 0x44, 0x77, 0x46,
        0x88, 0x99, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF,
    ])
    for guid in (
        "00112233-4455-4677-8899-aabbccddeeff",
        "00112233445546778899AABBCCDDEEFF",
    ):
        builder.set_guid(guid)
        data = builder.to_bytes()
        assert len(data) >= 68
        assert data[52:68] == native

    for bad in (
        "not-a-guid",
        "00112233445546778899AABBCCDDEEFZ",
        "00112233-4455-4677-8899a-abbccddeeff",
    ):
        with pytest.raises(SkpWriteError):
            builder.set_guid(bad)

    unchanged = builder.to_bytes()
    assert unchanged[52:68] == native

    builder.set_guid("")
    reset = builder.to_bytes()
    assert reset[52:68] != native
    assert reset[52:68] == builder.to_bytes()[52:68]
    assert (reset[52 + 7] & 0xF0) == 0x40
    assert (reset[52 + 8] & 0xC0) == 0x80


def test_definition_guid_preserved_in_archive_and_public_model(tmp_path: pathlib.Path) -> None:
    native = bytes([
        0x33, 0x22, 0x11, 0x00, 0x55, 0x44, 0x77, 0x46,
        0x88, 0x99, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF,
    ])
    for idx, guid in enumerate((
        "00112233-4455-4677-8899-aabbccddeeff",
        "00112233445546778899AABBCCDDEEFF",
    )):
        builder = create()
        with builder.add_component_definition("Identified", guid=guid) as defn:
            defn.add_face(SQUARE_PTS)
        builder.add_instance(defn)
        data = builder.to_bytes()
        assert native in data
        model = _parse_bytes(tmp_path, f"identified_{idx}.skp", data)
        assert len(model.definitions) == 1
        only_def = next(iter(model.definitions.values()))
        assert only_def.guid == "00112233445546778899AABBCCDDEEFF"
        assert len(model.root.instances) == 1
        ref_idx = model.root.instances[0].ref_idx
        assert ref_idx is not None
        assert model.definitions[ref_idx].guid == "00112233445546778899AABBCCDDEEFF"


def test_invalid_definition_guid_does_not_partially_add_definition(tmp_path: pathlib.Path) -> None:
    builder = create()
    with pytest.raises(SkpWriteError):
        builder.add_component_definition("Invalid", guid="not-a-guid")
    with builder.add_component_definition("Valid") as good:
        good.add_face(SQUARE_PTS)
    builder.add_instance(good)
    model = _parse_bytes(tmp_path, "valid.skp", builder.to_bytes())
    assert len(model.definitions) == 1
    assert next(iter(model.definitions.values())).name == "Valid"


def test_default_definition_guids_are_distinct_and_stable(tmp_path: pathlib.Path) -> None:
    builder = create()
    with builder.add_component_definition("Same name") as a:
        a.add_face(SQUARE_PTS)
    with builder.add_component_definition("Same name") as b:
        b.add_face(SQUARE_PTS)
    builder.add_instance(a)
    builder.add_instance(b)
    first = _parse_bytes(tmp_path, "first.skp", builder.to_bytes())
    second = _parse_bytes(tmp_path, "second.skp", builder.to_bytes())
    assert len(first.definitions) == 2
    defs = list(first.definitions.values())
    assert defs[0].guid != defs[1].guid
    for k, v in first.definitions.items():
        assert v.guid == second.definitions[k].guid
