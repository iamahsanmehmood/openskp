"""DXF Unicode layer escaping, deduplication, 16-bit polyface limits, and mesh validation (#499)."""

from __future__ import annotations

from array import array
import math
import pathlib
import zlib

import pytest

from openskp.export.dxf import METRES_TO_INCHES, export as export_dxf, to_dxf
from openskp.scene import GlbPrimitive, Scene


def _prim(
    name: str | bytes,
    positions: list[float] | array,
    indices: list[int] | array,
    material_index: int = 0,
) -> GlbPrimitive:
    pos = positions if isinstance(positions, array) else array("f", positions)
    idx = indices if isinstance(indices, array) else array("I", indices)
    n = len(pos) // 3
    return GlbPrimitive(
        geom_name=name,  # type: ignore[arg-type]
        material_index=material_index,
        positions=pos,
        normals=array("f", [0.0, 0.0, 1.0] * n),
        uvs=array("f", [0.0, 0.0] * n),
        indices=idx,
    )


def _triangle(name: str | bytes) -> GlbPrimitive:
    return _prim(name, [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0], [0, 1, 2])


def _inspect(dxf: str) -> tuple[set[str], list[str]]:
    assert dxf.isascii(), "R2000 DXF contains raw non-ASCII bytes instead of Unicode escapes"
    assert dxf.endswith("\r\n")
    lines = dxf[:-2].split("\r\n")
    assert len(lines) % 2 == 0, "incomplete DXF code/value pair"

    layers: set[str] = set()
    mesh_layers: list[str] = []
    references: list[str] = []
    record = ""
    table_count = 0
    layer_table = False
    entity = False

    for k in range(0, len(lines), 2):
        group = int(lines[k].strip())
        value = lines[k + 1]
        if 60 <= group <= 79:
            iv = int(value)
            assert -32768 <= iv <= 32767, f"16-bit DXF field overflow: {iv}"
        if group == 0:
            record = value
            entity = False
        if group == 100 and value == "AcDbEntity":
            entity = True
        if record == "TABLE" and group == 2:
            layer_table = value == "LAYER"
        if record == "TABLE" and layer_table and group == 70:
            table_count = int(value)
        if record == "LAYER" and group == 2:
            assert value not in layers, f"duplicate LAYER {value}"
            layers.add(value)
            assert len(value) <= 2049, "encoded layer exceeds R2000 string limit"
            decoded: list[int] = []
            i = 0
            while i < len(value):
                if value[i] == "\\":
                    assert value[i : i + 3] == "\\U+" and i + 7 <= len(value), "bad Unicode escape"
                    decoded.append(int(value[i + 3 : i + 7], 16))
                    i += 7
                else:
                    assert 32 <= ord(value[i]) != 127, "control character in LAYER"
                    decoded.append(ord(value[i]))
                    i += 1
            assert 1 <= len(decoded) <= 255, "invalid logical layer name length"
            raw_utf16 = b"".join(u.to_bytes(2, "little") for u in decoded)
            raw_utf16.decode("utf-16-le")  # raises on split or unpaired surrogates
        if group == 8 and entity:
            references.append(value)
            if record in ("POLYLINE", "3DFACE"):
                mesh_layers.append(value)

    assert table_count == len(layers), "incorrect LAYER table count"
    for ref in references:
        assert ref in layers, f"entity refers to missing layer {ref}"
    return layers, mesh_layers


def _assert_bytes(dxf: str, expected_len: int, expected_crc: int) -> None:
    data = dxf.encode("ascii")
    assert len(data) == expected_len
    assert (zlib.crc32(data) & 0xFFFFFFFF) == expected_crc


def test_unicode_names_deduplication_and_reference_bytes() -> None:
    chinese = "mesh_27_ROOT__\u4e0a\u67dc(\u4fa7\u5305\u9876\u5e95)__\u9876\u677f_\u540a\u67dc"
    escaped = (
        "mesh_27_ROOT__\\U+4E0A\\U+67DC(\\U+4FA7\\U+5305\\U+9876\\U+5E95)"
        "__\\U+9876\\U+677F_\\U+540A\\U+67DC"
    )
    long_name = "\u67dc" * 260
    names = [
        chinese,
        "Caf\u00e9",
        "\u67dc\U0001f600",
        long_name + "A",
        long_name + "B",
        ("x" * 245) + "\U0001f600" + ("x" * 20),
        "A/B",
        "A:B",
        "walls",
        "WALLS",
        "0",
        "Defpoints",
        "",
        "\t ",
        "line\nbreak\tname",
        chinese,
        "y" * 300,
    ]
    scene = Scene(glb_primitives=[_triangle(n) for n in names])
    for mode in ("polyface", "3dface"):
        dxf = to_dxf(scene, scale=1.0, mode=mode)
        layers, mesh_layers = _inspect(dxf)
        assert escaped in layers
        assert "Caf\\U+00E9" in layers
        assert "\\U+67DC\\U+D83D\\U+DE00" in layers
        assert len(mesh_layers) == len(names)
        assert mesh_layers[0] == mesh_layers[15]
        unique = [m for idx, m in enumerate(mesh_layers) if idx != 15]
        lowered = {m.lower() for m in unique}
        assert len(lowered) == len(unique)

    _assert_bytes(to_dxf(scene, scale=1.0, mode="polyface"), 62908, 0x561316A9)
    _assert_bytes(to_dxf(scene, scale=1.0, mode="3dface"), 31833, 0x89DCD865)
    _assert_bytes(to_dxf(Scene(), scale=METRES_TO_INCHES, mode="polyface"), 16676, 0xFDEAA351)


def test_empty_and_unused_primitives_do_not_duplicate_layers() -> None:
    layers, _ = _inspect(to_dxf(Scene(), scale=1.0))
    assert layers == {"0", "Defpoints"}

    unused = _prim("Unused", [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0], [])
    layers2, _ = _inspect(to_dxf(Scene(glb_primitives=[unused]), scale=1.0))
    assert layers2 == {"0", "Defpoints"}

    back = _triangle("UnpaintedBack")
    back.unpainted_back = True
    layers3, mesh_layers3 = _inspect(
        to_dxf(Scene(glb_primitives=[_triangle("FrontOnly"), back]), scale=1.0)
    )
    assert layers3 == {"0", "Defpoints", "FrontOnly"}
    assert mesh_layers3 == ["FrontOnly"]


def test_meshes_past_polyface_16_bit_limits_fall_back_to_3dface() -> None:
    positions = array("f", [0.0] * (32768 * 3))
    positions[3] = 1.0
    positions[7] = 1.0
    large = _prim("Large mesh", positions, [0, 1, 32767])
    many = _prim(
        "Many faces",
        [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
        [0, 1, 2] * 32768,
    )
    scene = Scene(glb_primitives=[large, many, _triangle("Fits")])
    dxf = to_dxf(scene, scale=1.0, mode="polyface")
    _, mesh_layers = _inspect(dxf)
    assert len(mesh_layers) == 1 + 32768 + 1
    assert dxf.count("\r\nPOLYLINE\r\n") == 1
    _assert_bytes(dxf, 8_863_208, 0x1445729A)


def test_malformed_meshes_and_scales_are_rejected(tmp_path: pathlib.Path) -> None:
    for failure in range(5):
        bad = _triangle("Invalid mesh")
        if failure == 0:
            bad.positions.pop()
        elif failure == 1:
            bad.indices.pop()
        elif failure == 2:
            bad.indices[0] = 3
        elif failure == 3:
            bad.positions[0] = float("nan")
        scale = float("inf") if failure == 4 else 1.0
        scene = Scene(glb_primitives=[bad])
        for mode in ("polyface", "3dface"):
            with pytest.raises(ValueError):
                to_dxf(scene, scale=scale, mode=mode)

    target = tmp_path / "missing_dir" / "never_written.dxf"
    with pytest.raises(ValueError):
        export_dxf(Scene(glb_primitives=[_triangle("Ok")]), target, scale=math.nan)
    assert not target.exists()


def test_malformed_utf8_and_surrogate_names_keep_geometry_and_valid_text() -> None:
    cases: list[tuple[bytes, str]] = [
        (b"\x80", "_"),
        (b"\xff", "_"),
        (b"\xc0\xaf", "__"),
        (b"\xe0\x80\xaf", "_"),
        (b"\xf0\x80\x80\xaf", "_"),
        (b"\xc2", "_"),
        (b"\xe4\xb8", "_"),
        (b"\xf0\x9f\x98", "_"),
        (b"\xed\xa0\x80", "_"),
        (b"\xf4\x90\x80\x80", "_"),
        (b"\xe4ok", "_ok"),
        (b"\xe4\xb8ok", "_ok"),
        (b"\xe4\xe6\x9f\x9c", "_\\U+67DC"),
        (b"\x80\xf0\x9f\x98\x80", "_\\U+D83D\\U+DE00"),
    ]
    for mode in ("polyface", "3dface"):
        for raw_seq, replacement in cases:
            surrogate_escaped = (b"before" + raw_seq + b"after").decode("utf-8", "surrogateescape")
            scene = Scene(
                glb_primitives=[
                    _triangle(surrogate_escaped),
                    _triangle("unaffected"),
                    _triangle(surrogate_escaped),
                ]
            )
            dxf = to_dxf(scene, scale=1.0, mode=mode)
            _, mesh_layers = _inspect(dxf)
            expected = f"before{replacement}after"
            assert mesh_layers == [expected, "unaffected", expected]
            assert dxf == to_dxf(scene, scale=1.0, mode=mode)

        colliding = Scene(
            glb_primitives=[
                _triangle("bad_"),
                _triangle(b"bad\xff".decode("utf-8", "surrogateescape")),
                _triangle(b"bad\x80".decode("utf-8", "surrogateescape")),
                _triangle(b"bad\xff".decode("utf-8", "surrogateescape")),
            ]
        )
        _, mesh_layers = _inspect(to_dxf(colliding, scale=1.0, mode=mode))
        assert len(mesh_layers) == 4
        assert len(set(mesh_layers)) == 3
        assert mesh_layers[1] == mesh_layers[3]
