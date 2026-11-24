"""What colour the scene builders give a face side with no paint (openskp#533).

SketchUp shows the model's front face colour on the front and its back face
colour on the back. The ``face_colors_<version>.skp`` fixtures hold one
unpainted face and were written with SketchUp's own library after setting
those two colours to distinctive values (see test_model_face_colors.py).
"""
from __future__ import annotations

import json
import pathlib
import struct

import pytest

from openskp import SkpFile, create

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
FRONT, BACK = (11, 122, 233), (201, 102, 3)
VERSIONS = ("v4", "v5", "v6", "2016", "2017", "2018", "2019", "2020", "2021")


def _rgb(built, prim):
    factor = built.gltf_materials[prim.material_index]["pbrMetallicRoughness"]["baseColorFactor"]
    return tuple(round(c * 255) for c in factor[:3])


def _double_sided(built, prim):
    return bool(built.gltf_materials[prim.material_index].get("doubleSided"))


def _corners(prim):
    """Each triangle as its corner positions, in winding order."""
    points = [tuple(round(c, 5) for c in prim.positions[i:i + 3]) for i in range(0, len(prim.positions), 3)]
    return [tuple(points[i] for i in prim.indices[t:t + 3]) for t in range(0, len(prim.indices), 3)]


def _same_winding(a, b):
    return b in (a, a[1:] + a[:1], a[2:] + a[:2])


def _built(skp, **options):
    baked = skp.build_scene(**options)
    instanced = skp.build_instanced_scene(**options)
    return (
        (baked, baked.glb_primitives),
        (instanced, [p for r in instanced.mesh_resources for p in r.primitives]),
    )


def _save(builder, tmp_path, name="model.skp"):
    path = tmp_path / name
    path.write_bytes(builder.to_bytes())
    return SkpFile.open(str(path))


@pytest.mark.parametrize("version", VERSIONS)
def test_each_side_shows_the_models_face_colour(version):
    for built, prims in _built(SkpFile.open(str(FIXTURES / f"face_colors_{version}.skp"))):
        front, back = sorted(prims, key=lambda p: p.unpainted_back)
        assert (front.unpainted_back, back.unpainted_back) == (False, True)
        assert (_rgb(built, front), _rgb(built, back)) == (FRONT, BACK)
        assert not _double_sided(built, front) and not _double_sided(built, back)
        # the back is the same triangles, turned round
        fronts, backs = _corners(front), _corners(back)
        assert len(fronts) == len(backs) > 0
        for a, b in zip(fronts, backs):
            assert set(a) == set(b) and not _same_winding(a, b)


@pytest.mark.parametrize("version", ("v4", "2017", "2021"))
def test_tag_option_is_the_old_behaviour(version):
    skp = SkpFile.open(str(FIXTURES / f"face_colors_{version}.skp"))
    layer = skp.parse().layers[0]
    for built, prims in _built(skp, unpainted_faces="tag"):
        (prim,) = prims
        assert _rgb(built, prim) == (layer.color_r, layer.color_g, layer.color_b)
        assert _double_sided(built, prim) and not prim.unpainted_back


def test_a_file_without_face_colours_uses_the_tag_colour():
    skp = SkpFile.open(str(FIXTURES / "legacy_v3_synthetic.skp"))
    assert skp.parse().face_front_color is None
    for (style, style_prims), (tag, tag_prims) in zip(_built(skp), _built(skp, unpainted_faces="tag")):
        assert [_rgb(style, p) for p in style_prims] == [_rgb(tag, p) for p in tag_prims]
        assert not any(p.unpainted_back for p in style_prims)


def test_unknown_option_is_an_error():
    skp = SkpFile.open(str(FIXTURES / "face_colors_2017.skp"))
    for build in (skp.build_scene, skp.build_instanced_scene):
        with pytest.raises(ValueError, match="unpainted_faces"):
            build(unpainted_faces="layer")


def test_paint_wins_over_the_face_colours(tmp_path):
    builder = create()
    red = builder.add_material("Red", (200, 10, 10, 255))
    square = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)]
    with builder.add_component_definition("Painted") as component:
        component.add_face([(x, y, 9) for x, y, _ in square])
    builder.add_face(square, material=red)  # front painted, back not
    builder.add_face([(x, y, 5) for x, y, _ in square], back_material=red)  # the reverse
    builder.add_instance(component, material=red)  # both sides inherit the paint
    skp = _save(builder, tmp_path)
    model = skp.parse()
    front, back = model.face_front_color, model.face_back_color
    assert front is not None and front != back

    for built, prims in _built(skp):
        sides = sorted((_rgb(built, p), _double_sided(built, p), p.unpainted_back) for p in prims)
        red_rgb = (200, 10, 10)
        # unpainted_back is only for a face with no paint on either side
        assert (red_rgb, True, False) in sides  # the painted component
        assert (front, False, False) in sides and (back, False, False) in sides
        assert not any(p.unpainted_back for p in prims)


def test_a_component_on_two_tags_is_one_mesh(tmp_path):
    builder = create()
    tag_a = builder.add_layer("A", color=(255, 0, 0))
    tag_b = builder.add_layer("B", color=(0, 0, 255))
    with builder.add_component_definition("Part") as part:
        part.add_face([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    builder.add_instance(part, layer=tag_a)
    builder.add_instance(part, layer=tag_b, translation=(5, 0, 0))
    skp = _save(builder, tmp_path)
    assert len(skp.build_instanced_scene().mesh_resources) == 1


def _box(tmp_path):
    builder = create()
    p = [(x, y, z) for z in (0, 10) for y in (0, 10) for x in (0, 10)]
    for a, b, c, d in ((0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (1, 3, 7, 5), (3, 2, 6, 7), (2, 0, 4, 6)):
        builder.add_face([p[a], p[b], p[c], p[d]])
    return _save(builder, tmp_path)


def test_single_colour_exports_keep_one_copy_of_each_face(tmp_path):
    """A format with one colour per surface gets every unpainted face once,
    in the front colour, as before: two coincident copies would make a solid
    unprintable and double every BIM and CAD export."""
    from openskp.export.dxf import to_dxf
    from openskp.export.ifc import to_ifc
    from openskp.export.obj import to_obj
    from openskp.export.ply import to_ply_ascii
    from openskp.export.stl import to_stl_ascii, to_stl_binary

    skp = _box(tmp_path)
    style, tag = skp.build_scene(), skp.build_scene(unpainted_faces="tag")
    assert sum(len(p.indices) // 3 for p in style.glb_primitives) == 24
    assert sum(len(p.indices) // 3 for p in style.surface_primitives) == 12
    assert style.surface_primitives[0] is style.glb_primitives[0]

    assert to_stl_ascii(style) == to_stl_ascii(tag)
    assert struct.unpack_from("<I", to_stl_binary(style), 80) == (12,)
    assert to_obj(style).count("\nf ") == to_obj(tag).count("\nf ") == 12
    assert "element face 12\n" in to_ply_ascii(style)
    assert to_dxf(style, mode="3dface").count("3DFACE") == to_dxf(tag, mode="3dface").count("3DFACE") > 0
    assert to_ifc(style).count("IFCTRIANGULATEDFACESET") == to_ifc(tag).count("IFCTRIANGULATEDFACESET") == 1


def test_fragments_keep_one_copy_of_each_face(tmp_path):
    pytest.importorskip("flatbuffers")
    from openskp.export.fragments import from_fragments, to_fragments

    def triangles(scene):
        read = from_fragments(to_fragments(scene))
        return sum(len(p.indices) // 3 for r in read.mesh_resources for p in r.primitives)

    skp = _box(tmp_path)
    assert (
        triangles(skp.build_instanced_scene())
        == triangles(skp.build_instanced_scene(unpainted_faces="tag"))
        == 12
    )


def _glb_materials(path):
    data = path.read_bytes()
    (length,) = struct.unpack_from("<I", data, 12)
    materials = json.loads(data[20:20 + length])["materials"]
    return sorted(
        (tuple(round(c * 255) for c in m["pbrMetallicRoughness"]["baseColorFactor"][:3]), bool(m.get("doubleSided")))
        for m in materials
    )


@pytest.mark.parametrize("module", ("glb", "instanced_glb"))
def test_glb_exports_show_both_sides(module, tmp_path):
    import importlib

    export = importlib.import_module(f"openskp.export.{module}").export
    skp = SkpFile.open(str(FIXTURES / "face_colors_2017.skp"))
    layer = skp.parse().layers[0]
    export(skp, str(tmp_path / "style.glb"))
    assert _glb_materials(tmp_path / "style.glb") == sorted([(FRONT, False), (BACK, False)])
    export(skp, str(tmp_path / "tag.glb"), unpainted_faces="tag")
    assert _glb_materials(tmp_path / "tag.glb") == [((layer.color_r, layer.color_g, layer.color_b), True)]
