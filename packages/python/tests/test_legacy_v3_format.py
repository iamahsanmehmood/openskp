"""Regression tests for SketchUp 3 files (openskp#284's "V3" signature,
``expected a string record``).

Fixture: legacy_v3_synthetic.skp - the same small synthetic model as
legacy_v6_synthetic.skp (4 component definitions - InnerFrame, Frame, Panel,
Post; InnerFrame also placed as a nested group inside Panel; 10 root
instances; a custom "Roof" layer; Frame's face painted with a flat red
material) built with ``openskp.create()`` and saved down to SketchUp 3 through
a real SketchUp version-downgrade export. SketchUp's default template rides
along: ~35 materials, 10 of them with embedded JPEG textures, plus a
"group_label" layer that SketchUp itself added. No private content.

A V3 file differs from every later era in a dozen small ways (see the
``_FIRST_V4`` comment in legacy.py). Before this, the very first material
record failed the string check; getting past it took roughly ten separate
layout fixes, and parsing without an exception was NOT enough - an
intermediate state "succeeded" with one definition of four, a phantom layer
and no root instances. These tests therefore assert the parsed structure, and
cross-check it against the V6 fixture built from the same source.

The layout is calibrated on this one file: V3 attribute dictionaries don't
exist (SketchUp 3 can't store them), instance names are absent, and the
draw-block bytes (hidden/soft/smooth) are undecoded and reported unset.
"""
from __future__ import annotations

import pathlib

from openskp import SkpFile
from openskp.legacy import is_legacy

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
V3 = FIXTURES / "legacy_v3_synthetic.skp"
V6 = FIXTURES / "legacy_v6_synthetic.skp"


def _defs(model):
    return {d.name: d for d in model.definitions.values()}


def _names_of(model, instances):
    return [model.definitions[i.ref_idx].name for i in instances]


class TestSketchUp3Parse:
    def test_detected_as_legacy(self):
        assert is_legacy(V3.read_bytes()) is True

    def test_parses_and_reports_its_version(self):
        assert SkpFile.open(str(V3)).parse().version == "{3.0.1}"

    def test_every_definition_is_present_with_its_geometry(self):
        model = SkpFile.open(str(V3)).parse()
        defs = _defs(model)
        assert set(defs) == {"InnerFrame", "Frame", "Panel", "Post"}
        for d in defs.values():
            assert (len(d.faces), len(d.edges), len(d.vertices)) == (1, 4, 4)
        # Frame is a 20x20 square at the origin
        xs = sorted({v.x for v in defs["Frame"].vertices.values()})
        ys = sorted({v.y for v in defs["Frame"].vertices.values()})
        assert (xs, ys) == ([0.0, 20.0], [0.0, 20.0])

    def test_places_every_root_instance_with_its_transform(self):
        model = SkpFile.open(str(V3)).parse()
        insts = model.root.instances
        assert len(insts) == 10
        # i % 3: 0 -> Frame, 1 -> Panel, 2 -> Post; translated 40 inches per step
        expected = ["Frame", "Panel", "Post"] * 3 + ["Frame"]
        assert _names_of(model, insts) == expected
        for i, inst in enumerate(insts):
            # legacy transform: 3x3 rotation (0-8), translation (9-11), scale
            assert inst.matrix[9] == i * 40.0

    def test_nested_group_is_placed_inside_panel(self):
        model = SkpFile.open(str(V3)).parse()
        panel = _defs(model)["Panel"]
        assert _names_of(model, panel.instances) == ["InnerFrame"]

    def test_layers_and_face_layer_assignment(self):
        model = SkpFile.open(str(V3)).parse()
        names = [layer.name for layer in model.layers]
        assert "Layer0" in names and "Roof" in names
        defs = _defs(model)
        post_face = next(iter(defs["Post"].faces.values()))
        frame_face = next(iter(defs["Frame"].faces.values()))
        assert post_face.layer != frame_face.layer  # Post's face is on Roof

    def test_face_material_comes_from_the_faces_own_pointer(self):
        # SketchUp 3's face opens with its FRONT MATERIAL pointer, not an
        # attribute container: Frame's face was painted red, the others not.
        model = SkpFile.open(str(V3)).parse()
        by_id = {m.id: m for m in model.materials}
        defs = _defs(model)
        painted = next(iter(defs["Frame"].faces.values()))
        assert by_id[painted.material_id].name == "Red"
        assert tuple(by_id[painted.material_id].color[:3]) == (255, 0, 0)
        for name in ("Panel", "Post", "InnerFrame"):
            assert next(iter(defs[name].faces.values())).material_id is None

    def test_no_face_is_spuriously_hidden(self):
        model = SkpFile.open(str(V3)).parse()
        for d in model.definitions.values():
            for f in d.faces.values():
                assert f.hidden is False

    def test_embedded_template_textures_are_read_as_inline_images(self):
        model = SkpFile.open(str(V3)).parse()
        textured = [m for m in model.materials if m.texture is not None]
        assert len(textured) == 10
        for m in textured:
            data = m.texture.data
            assert data and data[:3] == b"\xff\xd8\xff"  # a real JPEG, not skipped bytes
            assert data[-2:] == b"\xff\xd9"  # ends exactly at its end-of-image marker

    def test_materials_are_all_read(self):
        model = SkpFile.open(str(V3)).parse()
        assert len(model.materials) == 35
        assert "Red" in {m.name for m in model.materials}


class TestSketchUp3MatchesSketchUp6:
    def test_same_source_model_gives_the_same_structure(self):
        v3 = SkpFile.open(str(V3)).parse()
        v6 = SkpFile.open(str(V6)).parse()
        d3, d6 = _defs(v3), _defs(v6)
        assert set(d3) == set(d6)
        for name in d3:
            assert len(d3[name].faces) == len(d6[name].faces)
            assert len(d3[name].edges) == len(d6[name].edges)
            assert len(d3[name].vertices) == len(d6[name].vertices)
        assert _names_of(v3, v3.root.instances) == _names_of(v6, v6.root.instances)
        for a, b in zip(v3.root.instances, v6.root.instances):
            assert all(abs(x - y) < 1e-9 for x, y in zip(a.matrix, b.matrix))
