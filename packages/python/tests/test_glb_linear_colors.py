"""GLB exporters write linear baseColorFactor values while keeping scene gltf_materials sRGB (#650)."""

from __future__ import annotations

import json
import struct

import pytest

from openskp import create
from openskp.export import glb, instanced_glb


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _glb_materials_by_alpha_or_factor(path):
    data = path.read_bytes()
    (length,) = struct.unpack_from("<I", data, 12)
    return json.loads(data[20 : 20 + length])["materials"]


@pytest.mark.parametrize("exporter_mod", [glb, instanced_glb])
def test_glb_writes_linear_base_color_factor_and_keeps_scene_srgb(tmp_path, exporter_mod):
    doc = create()
    mats = [
        ("LayerRed", (255, 84, 84, 255)),
        ("White", (255, 255, 255, 255)),
        ("Black", (0, 0, 0, 255)),
        ("MidGrey", (188, 188, 188, 255)),
        ("DarkToe", (10, 10, 10, 128)),
    ]
    handles = [doc.add_material(name, rgba) for name, rgba in mats]
    for i, m in enumerate(handles):
        x = float(i * 10)
        doc.add_face([(x, 0.0, 0.0), (x + 5.0, 0.0, 0.0), (x + 5.0, 5.0, 0.0), (x, 5.0, 0.0)], material=m)

    skp_path = tmp_path / "colors.skp"
    doc.save(str(skp_path))

    from openskp import SkpFile

    skp = SkpFile.open(str(skp_path))
    skp.parse()

    # Scene gltf_materials remain sRGB before and after GLB export.
    baked_scene = skp.build_scene()
    inst_scene = skp.build_instanced_scene()
    for built in (baked_scene, inst_scene):
        by_name = {m.get("name"): m["pbrMetallicRoughness"]["baseColorFactor"] for m in built.gltf_materials}
        assert by_name["LayerRed"] == pytest.approx([1.0, 84 / 255, 84 / 255, 1.0])
        assert by_name["MidGrey"] == pytest.approx([188 / 255, 188 / 255, 188 / 255, 1.0])

    out_glb = tmp_path / "out.glb"
    exporter_mod.export(skp, str(out_glb))
    exported = _glb_materials_by_alpha_or_factor(out_glb)

    factors = [m["pbrMetallicRoughness"]["baseColorFactor"] for m in exported]

    def find_close(target_rgba):
        for f in factors:
            if all(abs(a - b) < 1e-4 for a, b in zip(f, target_rgba)):
                return f
        return None

    red = find_close([1.0, _srgb_to_linear(84 / 255), _srgb_to_linear(84 / 255), 1.0])
    assert red is not None
    assert red[0] == 1.0
    assert red[1] == pytest.approx(0.0887, abs=1e-4)
    assert red[2] == pytest.approx(0.0887, abs=1e-4)
    assert red[3] == 1.0

    white = find_close([1.0, 1.0, 1.0, 1.0])
    assert white is not None
    assert white == [1.0, 1.0, 1.0, 1.0]

    black = find_close([0.0, 0.0, 0.0, 1.0])
    assert black is not None
    assert black == [0.0, 0.0, 0.0, 1.0]

    mid = find_close([_srgb_to_linear(188 / 255)] * 3 + [1.0])
    assert mid is not None
    assert mid[0] == pytest.approx(0.503, abs=1e-3)
    assert mid[0] == pytest.approx(_srgb_to_linear(188 / 255), abs=1e-9)

    # Below 0.04045 uses c / 12.92 (~0.003035), not c ** 2.2 (~0.000804), and alpha stays unchanged.
    toe = find_close([(10 / 255) / 12.92] * 3 + [128 / 255])
    assert toe is not None
    assert toe[0] == pytest.approx((10 / 255) / 12.92, abs=1e-9)
    assert toe[3] == pytest.approx(128 / 255, abs=1e-6)


@pytest.mark.parametrize("exporter_mod", [glb, instanced_glb])
def test_plain_embedded_texture_factor_stays_unchanged(tmp_path, exporter_mod):
    import pathlib
    from openskp import SkpFile

    fixture = pathlib.Path(__file__).parent / "fixtures" / "capilla_quiroz_v17.skp"
    skp = SkpFile.open(str(fixture))
    skp.parse()
    scene = skp.build_scene()
    scene_textured_factors = sorted(
        tuple(m["pbrMetallicRoughness"]["baseColorFactor"])
        for m in scene.gltf_materials
        if "baseColorTexture" in m["pbrMetallicRoughness"]
    )

    out_glb = tmp_path / "textured.glb"
    exporter_mod.export(skp, str(out_glb), textures=True)
    exported = _glb_materials_by_alpha_or_factor(out_glb)
    exported_textured_factors = sorted(
        tuple(m["pbrMetallicRoughness"]["baseColorFactor"])
        for m in exported
        if "baseColorTexture" in m["pbrMetallicRoughness"]
    )
    assert exported_textured_factors == pytest.approx(scene_textured_factors)
