"""Regression tests for SketchUp 3 files (openskp#284's "V3" signature,
``expected a string record``).

Fixture: legacy_v3_synthetic.skp - the same small synthetic model as
legacy_v6_synthetic.skp (4 component definitions - InnerFrame, Frame, Panel,
Post; InnerFrame also placed as a nested group inside Panel; 10 root
instances; a custom "Roof" layer; Frame's face painted with a flat red
material) built with ``openskp.create()`` and saved down to SketchUp 3 through
the installed SketchUp library (re-saved from legacy_v6_synthetic.skp at
SketchUp 3). It is small (8 KB) and carries no default-template materials or
images. No private content.

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

    def test_materials_are_all_read(self):
        model = SkpFile.open(str(V3)).parse()
        assert len(model.materials) == 24
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


TEXTURED = FIXTURES / "legacy_v3_textured.skp"


class TestSketchUp3SingleMaterial:
    """A V3 file with ONE material takes the slot-base probe path (the
    two-material bootstrap needs at least two), which used to read a layer
    count SketchUp 3 does not write and rejected every such file with
    "implausible layer count in base probe".

    Fixture: legacy_v3_textured.skp - one 20x20 face painted with a single
    material "Checker", an 8x8 PNG generated by this project (no third-party
    image), built with ``openskp.create()`` and saved down to SketchUp 3
    through the installed SketchUp library. 4 KB; it carries no default-template
    materials or images.
    """

    def test_parses(self):
        model = SkpFile.open(str(TEXTURED)).parse()
        assert model.version == "{3.0.1}"

    def test_the_single_material_and_its_inline_image_are_read(self):
        model = SkpFile.open(str(TEXTURED)).parse()
        assert [m.name for m in model.materials] == ["Checker"]
        data = model.materials[0].texture.data
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        assert data[-8:] == b"IEND\xaeB`\x82"  # ends exactly at its end chunk

    def test_the_face_is_painted_with_it(self):
        model = SkpFile.open(str(TEXTURED)).parse()
        faces = [f for d in [model.root, *model.definitions.values()] for f in d.faces.values()]
        assert len(faces) == 1
        assert model.materials[0].id == faces[0].material_id


CURVES_AND_TEXT = FIXTURES / "legacy_v3_curves_and_text.skp"
NO_MATERIALS = FIXTURES / "legacy_v3_no_materials.skp"


class TestSketchUp3CurvesTextAndGuides:
    """SketchUp 3 CCurve and CArcCurve open with two CEdge pointers (no
    CAttributeContainer preamble), CSkFont has no preamble and a 6-byte tail,
    and CConstructionLine stores only point + direction (#284).
    """

    def test_face_bounded_curves_and_arc_curves_parse_without_loop_desync(self):
        model = SkpFile.open(str(CURVES_AND_TEXT)).parse()
        assert model.version == "{3.0.1}"
        assert model.unread_root_entities == 0
        defs = _defs(model)
        assert set(defs) == {"CurvedPanel", "ArcDisk"}

        panel = defs["CurvedPanel"]
        assert (len(panel.faces), len(panel.edges), len(panel.vertices)) == (1, 6, 6)
        assert len(panel.arcs) == 0
        curved = [e.curve_id for e in panel.edges.values() if e.curve_id is not None]
        assert len(curved) == 4 and len(set(curved)) == 1
        panel_face = next(iter(panel.faces.values()))
        assert model.materials_by_id[panel_face.material_id].name == "Red"

        disk = defs["ArcDisk"]
        assert (len(disk.faces), len(disk.edges), len(disk.vertices)) == (1, 8, 8)
        assert len(disk.arcs) == 1
        arc = next(iter(disk.arcs.values()))
        assert arc.center == (10.0, 10.0, 0.0)
        assert abs(arc.radius - 8.0) < 1e-9
        assert arc.is_full_circle is True
        disk_face = next(iter(disk.faces.values()))
        assert model.materials_by_id[disk_face.material_id].name == "Blue"

        assert _names_of(model, model.root.instances) == ["CurvedPanel", "ArcDisk"]
        assert [i.matrix[9] for i in model.root.instances] == [0.0, 40.0]
        assert model.root.instances[0].material_id is None
        assert model.materials_by_id[model.root.instances[1].material_id].name == "Red"

    def test_loose_curves_font_text_and_infinite_guide_line_at_root(self):
        from openskp.model import loose_edge_runs

        model = SkpFile.open(str(CURVES_AND_TEXT)).parse()
        assert (len(model.root.faces), len(model.root.edges), len(model.root.vertices)) == (0, 9, 11)
        assert len(model.root.arcs) == 1
        arc = next(iter(model.root.arcs.values()))
        assert arc.center == (0.0, 40.0, 0.0)
        assert abs(arc.radius - 10.0) < 1e-9
        assert arc.is_full_circle is False
        assert [(len(r[0]), len(r[1]), r[2]) for r in loose_edge_runs(model.root)] == [
            (6, 7, False),
            (3, 4, False),
        ]

        assert len(model.root.texts) == 1
        txt = model.root.texts[0]
        assert txt.text == "Label A"
        assert txt.point == (15.0, 15.0, 0.0)
        assert txt.label_point == (20.0, 20.0, 10.0)
        assert txt.screen_position == (0.0, 0.0)

        assert len(model.root.construction_lines) == 1
        cl = model.root.construction_lines[0]
        assert cl.point == (0.0, 0.0, 0.0)
        assert cl.direction == (1.0, 0.0, 0.0)
        assert cl.start is None and cl.end is None


class TestSketchUp3ZeroMaterials:
    def test_parses_when_file_has_no_cmaterial_class(self):
        model = SkpFile.open(str(NO_MATERIALS)).parse()
        assert model.version == "{3.0.1}"
        assert model.unread_root_entities == 0
        assert model.materials == []
        assert "Layer0" in [layer.name for layer in model.layers]
        assert (len(model.root.faces), len(model.root.edges), len(model.root.vertices)) == (1, 4, 4)
