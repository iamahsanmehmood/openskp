"""Scene materials keep what SketchUp shows: inherited paint, names and colorization.

- An unpainted face inside a painted group/component renders with the group's material as a
  whole - texture and transparency included - not just that material's average colour. A group
  painted with a wood-floor texture or a translucent glass imported as a flat opaque colour before.
- Two differently named materials that share a colour stay two scene materials, each carrying its
  SketchUp name (glTF ``name``), instead of being merged into one anonymous material.
- A colorized texture (SketchUp's ``[Name]1`` copies) is flagged in the glTF material's
  ``extras.sketchup`` so a consumer can re-tint the shared image toward ``baseColorFactor``.

Fixture ``capilla_quiroz_v17.skp`` carries real textured and translucent library materials
("[Polished Concrete New]", "[Translucent Glass Blue]", ...). No committed fixture paints geometry with
a colorized texture or paints a group with a textured material, so those are covered on synthetic faces
and by flagging a fixture material as colorized after parsing.
"""
from __future__ import annotations

import pathlib
from types import SimpleNamespace

from openskp import _core, instanced_scene, scene
from openskp._face_groups import FaceGroupContext, build_local_face_groups

CAPILLA = pathlib.Path(__file__).parent / "fixtures" / "capilla_quiroz_v17.skp"

WOOD = {
    "name": "[Wood_ Floor]",
    "color": {"r": 155, "g": 105, "b": 61},
    "transparency": 1.0,
    "texture": {"data": b"wood", "x_scale": 60.0, "y_scale": 60.0},
}
GLASS = {"name": "Glass", "color": {"r": 229, "g": 230, "b": 230}, "transparency": 0.59}
GREY_A = {"name": "Grey A", "color": {"r": 126, "g": 127, "b": 127}, "transparency": 1.0}
GREY_B = {"name": "Grey B", "color": {"r": 126, "g": 127, "b": 127}, "transparency": 1.0}
MATERIALS = {m["name"]: m for m in (WOOD, GLASS, GREY_A, GREY_B)}
IDS = {1: "[Wood_ Floor]", 2: "Glass", 3: "Grey A", 4: "Grey B"}


def _square_builder(*face_materials):
    """One unit square per entry, each painted (front and back) with the given material id or None."""
    vertices, edges, faces = {}, {}, {}
    for i, mat_id in enumerate(face_materials):
        base = i * 4
        z = float(i)
        for k, (x, y) in enumerate(((0, 0), (1, 0), (1, 1), (0, 1))):
            vertices[base + k] = (float(x), float(y), z)
        loop = []
        for k in range(4):
            edges[base + k] = (base + k, base + (k + 1) % 4)
            loop.append((base + k, 1))
        faces[i] = {"loops": [loop], "normal": (0.0, 0.0, 1.0), "material_id": mat_id, "back_material_id": mat_id}
    return SimpleNamespace(vertices=vertices, edges=edges, faces=faces)


def _groups(builder, fallback_material=None, fallback_color=(136, 136, 136)):
    textures = []

    def texture_index_for(tex, material_name=None):
        if not tex:
            return None
        if tex not in textures:
            textures.append(tex)
        return textures.index(tex)

    ctx = FaceGroupContext(
        material_id_to_name=IDS,
        materials=MATERIALS,
        materials_by_folder={},
        texture_index_for=texture_index_for,
        fallback_color=fallback_color,
        definition_id="test",
        fallback_material=fallback_material,
    )
    return build_local_face_groups(builder, ctx)


def test_unpainted_face_inherits_the_painted_groups_texture_and_name():
    (key,) = _groups(_square_builder(None), fallback_material=WOOD, fallback_color=(155, 105, 61))
    assert key == ((155, 105, 61), True, 0, 1.0, "[Wood_ Floor]", False)


def test_unpainted_face_inherits_the_painted_groups_transparency():
    (key,) = _groups(_square_builder(None), fallback_material=GLASS, fallback_color=(229, 230, 230))
    assert key == ((229, 230, 230), True, None, 0.59, "Glass", False)


def test_a_face_painted_itself_keeps_its_own_material():
    (key,) = _groups(_square_builder(3), fallback_material=WOOD, fallback_color=(155, 105, 61))
    assert key == ((126, 127, 127), True, None, 1.0, "Grey A", False)


def test_nothing_painted_falls_back_to_the_layer_colour_without_a_name():
    (key,) = _groups(_square_builder(None))
    assert key == ((136, 136, 136), True, None, 1.0, None, False)


def test_differently_named_materials_with_one_colour_stay_separate():
    keys = set(_groups(_square_builder(3, 4)))
    assert keys == {
        ((126, 127, 127), True, None, 1.0, "Grey A", False),
        ((126, 127, 127), True, None, 1.0, "Grey B", False),
    }


def test_a_colorized_texture_is_flagged_on_its_group():
    colorized = dict(WOOD, colorized=True, colorize_type=1)
    MATERIALS["[Wood_ Floor]"] = colorized
    try:
        (group,) = _groups(_square_builder(1)).values()
    finally:
        MATERIALS["[Wood_ Floor]"] = WOOD
    assert (group["material_name"], group["colorized"], group["colorize_type"]) == ("[Wood_ Floor]", True, 1)


def _scenes(parsed):
    return (instanced_scene.build_instanced_scene(parsed), scene.build_scene(parsed))


def test_scene_materials_carry_their_sketchup_names():
    for built in _scenes(_core.full_parse(str(CAPILLA))):
        names = {m.get("name") for m in built.gltf_materials}
        assert {"[Polished Concrete New]", "[Translucent Glass Safety]", "[Translucent Glass Blue]"} <= names


def test_scene_materials_flag_a_colorized_texture():
    parsed = _core.full_parse(str(CAPILLA))
    parsed["materials"]["[Polished Concrete New]"].update(colorized=True, colorize_type=0)
    for built in _scenes(parsed):
        got = {m.get("name"): m for m in built.gltf_materials}
        assert got["[Polished Concrete New]"]["extras"] == {"sketchup": {"colorized": True, "colorize_type": 0}}
        assert "extras" not in got["[Translucent Glass Blue]"]


PNG_1X1 = bytes([
    0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,
    0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53,
    0xDE, 0x00, 0x00, 0x00, 0x0F, 0x49, 0x44, 0x41, 0x54, 0x78, 0x01, 0x01, 0x04, 0x00, 0xFB, 0xFF,
    0x00, 0xDC, 0x28, 0x1E, 0x03, 0x06, 0x01, 0x23, 0xE0, 0x4F, 0x0F, 0xCC, 0x00, 0x00, 0x00, 0x00,
    0x49, 0x45, 0x4E, 0x44, 0xAE, 0x42, 0x60, 0x82,
])


def test_glb_exports_keep_same_color_named_textured_and_unpainted_materials(tmp_path):
    import json
    import struct
    from openskp import SkpFile, create
    from openskp.export import glb, instanced_glb

    png_path = tmp_path / "brick.png"
    png_path.write_bytes(PNG_1X1)
    skp_path = tmp_path / "material_names.skp"

    b = create()
    coral_a = b.add_material("Coral A", (200, 80, 60))
    coral_b = b.add_material("Coral B", (200, 80, 60))
    brick_tex = b.add_texture_material("Brick Tex", str(png_path))
    b.add_face([(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)], material=coral_a)
    b.add_face([(15, 0, 0), (25, 0, 0), (25, 10, 0), (15, 10, 0)], material=coral_b)
    b.add_face([(30, 0, 0), (40, 0, 0), (40, 10, 0), (30, 10, 0)], material=brick_tex)
    b.add_face([(45, 0, 0), (55, 0, 0), (55, 10, 0), (45, 10, 0)])
    b.save(str(skp_path))

    skp = SkpFile.open(str(skp_path))
    skp.parse()
    for built in (skp.build_scene(), skp.build_instanced_scene()):
        assert len(built.gltf_materials) == 5
        named = [m for m in built.gltf_materials if "name" in m]
        unnamed = [m for m in built.gltf_materials if "name" not in m]
        assert {m["name"] for m in named} == {"Coral A", "Coral B", "Brick Tex"}
        assert len(unnamed) == 2

    for exporter, prefix in ((glb, "flat"), (instanced_glb, "instanced")):
        for embed_tex in (False, True):
            out_glb = tmp_path / f"{prefix}_{int(embed_tex)}.glb"
            exporter.export(skp, str(out_glb), textures=embed_tex)
            raw = out_glb.read_bytes()
            j_len, j_type = struct.unpack_from("<II", raw, 12)
            assert j_type == 0x4E4F534A
            doc = json.loads(raw[20 : 20 + j_len].decode("utf-8"))
            mats = doc["materials"]
            assert len(mats) == 5
            named_glb = {m["name"]: m for m in mats if "name" in m}
            unnamed_glb = [m for m in mats if "name" not in m]
            assert set(named_glb) == {"Coral A", "Coral B", "Brick Tex"}
            assert len(unnamed_glb) == 2
            if embed_tex:
                assert "baseColorTexture" in named_glb["Brick Tex"]["pbrMetallicRoughness"]
            else:
                assert "baseColorTexture" not in named_glb["Brick Tex"]["pbrMetallicRoughness"]

