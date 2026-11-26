"""IFC4 conformance of the text this exporter writes.

Four things were wrong with its output, all invisible to a reader and all
caught by ifcopenshell's ``validate(express_rules=True)``:

* Non-ASCII text was written as raw UTF-8. ISO 10303-21 wants
  ``\\X2\\<UTF-16BE hex>\\X0\\``, and ifcopenshell 0.8.5 truncates at the
  character without a word rather than refusing the file - so the defect is
  silent on both ends unless something checks for it by hand.
* Enumeration literals used the IFC2x spellings: ``.TRUE.``/``.FALSE.`` for
  IfcBoolean, ``.READWRITE.`` for IfcChangeActionEnum, ``.STERADIANUNIT.``
  for IfcUnitEnum.
* Every product was written with one hardcoded attribute count, so any type
  whose real count differs from it came out short - or long - by the
  difference.
* ``Closed`` on IfcTriangulatedFaceSet was a hardcoded ``.TRUE.``: a claim
  about every mesh the exporter had ever written, which it never checked.

The tests are text assertions rather than ifcopenshell round-trips on purpose:
ifcopenshell is not a dependency of this package, and the point is to pin the
bytes this exporter produces, not to ask a particular reader's opinion of them.
"""
from __future__ import annotations

import codecs
import json
import math
import pathlib
import re
from array import array
from typing import Dict, List, Optional

import pytest

from openskp.export.ifc import export, to_ifc
from openskp.scene import CurveSetMetadata, GlbPrimitive, InstanceNode, MeshMetadata, Scene

# The attribute-count table the exporter itself consults. Loaded here from the
# file rather than imported, so the test fails if the emitter and the table
# ever disagree instead of moving together.
_ATTR_COUNTS = (pathlib.Path(__file__).parent.parent / "src" / "openskp"
                / "export" / "ifc_attr_counts.json")


def _mesh_scene(name: str, positions: List[float], indices: List[int],
                properties: Optional[Dict[str, str]] = None,
                attribute_dictionaries: Optional[Dict[str, Dict[str, str]]] = None,
                ) -> Scene:
    """An in-memory one-primitive scene, the same shape test_ifc.py builds."""
    prim = GlbPrimitive(
        positions=array("f", positions),
        normals=array("f", [0.0, 0.0, 1.0] * (len(positions) // 3)),
        uvs=array("f", [0.0, 0.0] * (len(positions) // 3)),
        indices=array("I", indices),
        material_index=0,
        geom_name=name,
    )
    return Scene(
        scene_hierarchy=InstanceNode(name="Root"),
        mesh_index={name: MeshMetadata(name=name,
                                       properties=properties or {},
                                       attribute_dictionaries=attribute_dictionaries or {})},
        glb_primitives=[prim],
        gltf_materials=[{"pbrMetallicRoughness":
                         {"baseColorFactor": [0.5, 0.5, 0.5, 1.0]}}],
    )


def _entity_line(text: str, token: str) -> str:
    """The first STEP entity line containing *token*."""
    for line in text.splitlines():
        if token in line:
            return line
    raise AssertionError(f"no entity line containing {token!r}")


def _split_attributes(line: str) -> List[str]:
    """The top-level comma-separated attributes of one STEP entity line.

    Splits on commas at parenthesis depth 0, so a nested list - the triangle
    index list of an IfcTriangulatedFaceSet, say - counts as one attribute
    rather than one per comma.
    """
    body = line[line.index("(") + 1: line.rindex(")")]
    out: List[str] = []
    depth = 0
    current = ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(current)
            current = ""
        else:
            current += ch
    out.append(current)
    return out


def _closed_flag(text: str) -> str:
    """The ``Closed`` attribute of the file's IfcTriangulatedFaceSet."""
    return _split_attributes(_entity_line(text, "IFCTRIANGULATEDFACESET("))[2]


def _attr_count_table() -> Dict[str, int]:
    return json.loads(_ATTR_COUNTS.read_text(encoding="utf-8"))["IFC4"]


class TestAttributeCounts:
    """Each product carries as many attributes as its own type declares."""

    def test_the_shipped_table_is_the_schema_it_claims(self):
        # Hard anchors. Without them a table regenerated wrong could agree
        # with an emitter that read the same wrong number, and both tests
        # would pass while the file stayed invalid.
        table = _attr_count_table()
        assert table["IfcDoor"] == 13
        assert table["IfcWall"] == 9

    def test_a_product_carries_every_attribute_its_type_declares(self):
        # IfcDoor and IfcWall differ (13 vs 9), which is the whole difficulty:
        # one hardcoded count cannot be right for both.
        table = _attr_count_table()
        for geom_name, type_name in (("Front Door", "IfcDoor"),
                                     ("Outer Wall", "IfcWall")):
            line = _entity_line(to_ifc(_mesh_scene(geom_name,
                                                   [0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
                                                    0.0, 1.0, 0.0], [0, 1, 2])),
                                type_name.upper() + "(")
            assert len(_split_attributes(line)) == table[type_name]


class TestEnumLiterals:
    """IFC4's own spellings, not IFC2x's."""

    def test_ifc2x_spellings_are_gone(self):
        text = to_ifc(_mesh_scene("Front Door",
                                  [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                                  [0, 1, 2]))
        assert ".TRUE." not in text
        assert ".FALSE." not in text
        assert ".READWRITE." not in text
        assert ".STERADIANUNIT." not in text

    def test_ifc4_spellings_are_used(self):
        text = to_ifc(_mesh_scene("Front Door",
                                  [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                                  [0, 1, 2]))
        assert ".T." in text                 # IfcBoolean true
        assert ".F." in text                 # IfcBoolean false
        assert ".SOLIDANGLEUNIT." in text    # IfcUnitEnum
        assert ".STERADIAN." in text         # the unit's own name
        assert ".NOCHANGE." in text          # IfcChangeActionEnum


class TestStepStringEscaping:
    """Non-ASCII text as ISO 10303-21 part 21 strings, not raw UTF-8."""

    def test_non_ascii_is_escaped_and_round_trips(self):
        name = "剪力墙 JLQ-1"
        text = to_ifc(_mesh_scene(name, [0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
                                         0.0, 1.0, 0.0], [0, 1, 2]))
        assert name not in text
        assert name.encode("utf-8") not in text.encode("utf-8")

        escaped = re.findall(r"\\X2\\[0-9A-F]+\\X0\\", text)
        assert escaped
        decoded = {codecs.decode(m[4:-4], "hex").decode("utf-16-be")
                   for m in escaped}
        assert "剪力墙" in decoded
        # ASCII runs inside the same string are not escaped, so the name is
        # not one big escape segment.
        assert "JLQ-1" in text

    def test_the_property_set_keys_are_escaped_too(self):
        # Strings do not only appear as names - a property key reaches the
        # file through write_pset, a separate path to the same encoder.
        text = to_ifc(_mesh_scene("Panel",
                                  [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                                  [0, 1, 2],
                                  properties={"名称": "承重墙"}))
        escaped = {codecs.decode(m[4:-4], "hex").decode("utf-16-be")
                   for m in re.findall(r"\\X2\\[0-9A-F]+\\X0\\", text)}
        assert "名称" in escaped
        assert "承重墙" in escaped

    def test_pure_ascii_is_left_verbatim(self):
        text = to_ifc(_mesh_scene("Outer Wall",
                                  [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                                  [0, 1, 2]))
        assert "Outer Wall" in text
        assert "\\X2\\" not in text

    def test_the_segment_is_utf16be_and_the_check_can_tell(self):
        # A reader that gets the endianness wrong produces different
        # characters and says nothing, which is the failure mode this class
        # exists for - so prove the assertion can distinguish the two readings
        # rather than passing under either.
        text = to_ifc(_mesh_scene("剪力墙",
                                  [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                                  [0, 1, 2]))
        match = re.search(r"\\X2\\([0-9A-F]+)\\X0\\", text)
        assert match is not None
        payload = match.group(1)
        assert payload == payload.upper()
        raw = codecs.decode(payload, "hex")
        assert raw.decode("utf-16-be") == "剪力墙"
        assert raw.decode("utf-16-le") != "剪力墙"

    def test_control_characters_and_del_are_escaped_without_splitting_lines(self):
        text = to_ifc(_mesh_scene(
            "Wall\nOne\x7f",
            [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
            [0, 1, 2],
            properties={"note\tkey": "line1\r\nline2\x00\x1f\x7f"},
        ))
        assert "Wall\\X2\\000A\\X0\\One\\X2\\007F\\X0\\" in text
        assert "note\\X2\\0009\\X0\\key" in text
        assert "line1\\X2\\000D000A\\X0\\line2\\X2\\0000001F007F\\X0\\" in text
        lines = text.split("\r\n")
        data_idx = lines.index("DATA;") + 1
        end_idx = lines.index("ENDSEC;", data_idx)
        for line in lines[data_idx:end_idx]:
            assert line.startswith("#") and line.endswith(");")
            assert all(0x20 <= ord(ch) <= 0x7E for ch in line)


class TestRealNumbersAndFiniteValidation:
    """STEP REAL numbers never use bare '1e-05' or non-finite values."""

    def test_small_and_large_coordinates_are_valid_step_reals(self):
        # 1e-8 m * 1000.0 = 1e-5 mm; Python str(round(...)) would emit '1e-05'.
        scene = _mesh_scene(
            "Tiny",
            [1e-8, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
            [0, 1, 2],
        )
        scene.curve_sets = [
            CurveSetMetadata(
                name="TinyCurve",
                points_m=[(1e-8, 0.0, 0.0), (1e17, 0.0, 0.0)],
            )
        ]
        text = to_ifc(scene)
        assert "1e-" not in text and "1e+" not in text
        assert "(0.00001,-0.0,0.0)" in text
        assert "(1.0E+20,-0.0,0.0)" in text

    def test_non_finite_scale_and_coordinates_raise_value_error(self, tmp_path):
        good = _mesh_scene("Wall", [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0], [0, 1, 2])
        for bad_scale in (math.nan, math.inf, -math.inf):
            with pytest.raises(ValueError, match="scale must be finite"):
                to_ifc(good, scale=bad_scale)

        nan_mesh = _mesh_scene("Wall", [math.nan, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0], [0, 1, 2])
        with pytest.raises(ValueError, match="coordinates must be finite"):
            to_ifc(nan_mesh)

        inf_curve = Scene(
            scene_hierarchy=InstanceNode(name="Root"),
            curve_sets=[CurveSetMetadata(name="C", points_m=[(0.0, 0.0, 0.0), (1e308, 0.0, 0.0)])],
        )
        with pytest.raises(ValueError, match="coordinates must be finite"):
            to_ifc(inf_curve, scale=1000.0)

        out_file = tmp_path / "nested" / "bad.ifc"
        with pytest.raises(ValueError, match="coordinates must be finite"):
            export(nan_mesh, out_file)
        assert not out_file.exists()
        assert not out_file.parent.exists()


class TestClosedFlag:
    """``Closed`` computed from the mesh, not asserted."""

    # A tetrahedron's four corners, each face listed separately.
    A = (0.0, 0.0, 0.0)
    B = (1.0, 0.0, 0.0)
    C = (0.0, 1.0, 0.0)
    D = (0.0, 0.0, 1.0)

    def test_an_open_mesh_is_not_closed(self):
        text = to_ifc(_mesh_scene("Open", [*self.A, *self.B, *self.C], [0, 1, 2]))
        assert _closed_flag(text) == ".F."

    def test_a_closed_tetra_is_closed(self):
        text = to_ifc(_mesh_scene("Tetra",
                                  [*self.A, *self.B, *self.C, *self.D],
                                  [0, 2, 1, 0, 1, 3, 1, 2, 3, 2, 0, 3]))
        assert _closed_flag(text) == ".T."

    def test_vertices_are_welded_by_position_not_by_index(self):
        # Same tetra, but one face is written with duplicate vertices, so the
        # index buffer alone reports two boundary edges. Only a weld by
        # position sees all six edges shared twice.
        text = to_ifc(_mesh_scene(
            "Welded",
            [*self.A, *self.B, *self.C, *self.D, *self.A, *self.B, *self.C,
             *self.D],
            [0, 1, 2,      # A B C
             4, 5, 7,      # A B D, via the duplicates
             0, 2, 3,      # A C D
             1, 2, 3]))    # B C D
        assert _closed_flag(text) == ".T."

    def test_per_face_vertices_do_not_make_a_closed_mesh_look_open(self):
        # What the exporter actually emits: every triangle with its own three
        # vertices, no sharing at all. Reading closure off the index buffer
        # reports False for *every* mesh this pipeline produces, so this case
        # is the one that decides whether the flag means anything.
        text = to_ifc(_mesh_scene(
            "PerFace",
            [*self.A, *self.B, *self.C,
             *self.A, *self.B, *self.D,
             *self.A, *self.C, *self.D,
             *self.B, *self.C, *self.D],
            [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]))
        assert _closed_flag(text) == ".T."

    def test_an_edge_used_three_times_is_not_closed(self):
        # A T-junction: no boundary edge, but three triangles meet along one
        # edge, so the surface is not a manifold. "Every edge used at least
        # twice" would call this closed; "exactly twice" is the real rule.
        text = to_ifc(_mesh_scene(
            "TJunction",
            [*self.A, *self.B, *self.C, *self.D, *self.C, *self.D,
             *self.A, *self.B],
            [0, 1, 2, 0, 1, 3, 0, 1, 4, 0, 1, 5]))
        assert _closed_flag(text) == ".F."


def _assert_dense_ids(text: str) -> None:
    ids = [
        int(line[1:line.index("=")])
        for line in text.splitlines()
        if line.startswith("#") and "=" in line
    ]
    assert ids == list(range(1, len(ids) + 1))


class TestIfc2x3Conformance:
    """schema='IFC2X3' writes IFC2X3-valid geometry, curves, and product attributes (#658)."""

    A = (0.0, 0.0, 0.0)
    B = (1.0, 0.0, 0.0)
    C = (0.0, 1.0, 0.0)
    D = (0.0, 0.0, 1.0)

    def test_closed_solid_uses_faceted_brep_and_open_mesh_uses_surface_model(self):
        closed_scene = _mesh_scene(
            "Tetra",
            [*self.A, *self.B, *self.C, *self.D],
            [0, 2, 1, 0, 1, 3, 1, 2, 3, 2, 0, 3],
        )
        closed_text = to_ifc(closed_scene, schema="IFC2X3")
        assert "FILE_SCHEMA(('IFC2X3'));" in closed_text
        assert "IFCTRIANGULATEDFACESET(" not in closed_text
        assert "IFCCARTESIANPOINTLIST3D(" not in closed_text
        assert closed_text.count("IFCPOLYLOOP(") == 4
        assert closed_text.count("IFCFACEOUTERBOUND(") == 4
        assert closed_text.count("IFCFACE(") == 4
        assert "IFCCLOSEDSHELL(" in closed_text
        assert "IFCFACETEDBREP(" in closed_text
        assert "'Body','Brep'," in closed_text
        _assert_dense_ids(closed_text)

        open_scene = _mesh_scene("Sheet", [*self.A, *self.B, *self.C], [0, 1, 2])
        open_text = to_ifc(open_scene, schema="IFC2X3")
        assert "IFCTRIANGULATEDFACESET(" not in open_text
        assert "IFCCARTESIANPOINTLIST3D(" not in open_text
        assert "IFCCONNECTEDFACESET(" in open_text
        assert "IFCFACEBASEDSURFACEMODEL(" in open_text
        assert "'Body','SurfaceModel'," in open_text
        _assert_dense_ids(open_text)

    def test_arcs_and_circles_use_trimmed_curve_circle_and_composite_curve(self):
        s45 = math.sqrt(0.5)
        open_arc = CurveSetMetadata(
            name="QuarterArc",
            points_m=[(1.0, 0.0, 0.0), (s45, 0.0, -s45), (0.0, 0.0, -1.0)],
            closed=False,
            arc={
                "points": [(1.0, 0.0, 0.0), (s45, 0.0, -s45), (0.0, 0.0, -1.0)],
                "segments": [(0, 1, 2)],
            },
        )
        closed_circle = CurveSetMetadata(
            name="FullCircle",
            points_m=[(1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)],
            closed=True,
            arc={
                "points": [(1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)],
                "segments": [(0, 1, 2), (2, 3, 0)],
            },
        )
        two_seg_arc = CurveSetMetadata(
            name="WaveArc",
            points_m=[
                (0.0, 0.0, 0.0), (1.0, 0.0, -1.0), (2.0, 0.0, 0.0),
                (3.0, 0.0, 1.0), (4.0, 0.0, 0.0),
            ],
            closed=False,
            arc={
                "points": [
                    (0.0, 0.0, 0.0), (1.0, 0.0, -1.0), (2.0, 0.0, 0.0),
                    (3.0, 0.0, 1.0), (4.0, 0.0, 0.0),
                ],
                "segments": [(0, 1, 2), (2, 3, 4)],
            },
        )
        collinear_arc = CurveSetMetadata(
            name="DegenerateArc",
            points_m=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)],
            closed=False,
            arc={
                "points": [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)],
                "segments": [(0, 1, 2)],
            },
        )

        scene = Scene(
            scene_hierarchy=InstanceNode(name="Root"),
            curve_sets=[open_arc, closed_circle, two_seg_arc, collinear_arc],
        )
        text = to_ifc(scene, schema="IFC2X3")
        assert "IFCINDEXEDPOLYCURVE(" not in text
        assert "IFCARCINDEX(" not in text
        assert "IFCCARTESIANPOINTLIST3D(" not in text
        assert text.count("IFCCIRCLE(") == 4
        assert text.count("IFCTRIMMEDCURVE(") == 3
        assert "IFCPARAMETERVALUE(1.570796)" in text
        assert text.count("IFCCOMPOSITECURVE(") == 1
        assert text.count("IFCCOMPOSITECURVESEGMENT(") == 2
        assert text.count("IFCPOLYLINE(") == 1
        _assert_dense_ids(text)

    def test_proxy_composition_type_roof_shape_type_and_door_attr_count(self):
        ifc2x3_counts = json.loads(_ATTR_COUNTS.read_text(encoding="utf-8"))["IFC2X3"]
        proxy_text = to_ifc(
            _mesh_scene("Mystery Box", [*self.A, *self.B, *self.C], [0, 1, 2]),
            schema="IFC2X3",
        )
        proxy_attrs = _split_attributes(_entity_line(proxy_text, "IFCBUILDINGELEMENTPROXY("))
        assert len(proxy_attrs) == ifc2x3_counts["IfcBuildingElementProxy"] == 9
        assert proxy_attrs[8] == ".ELEMENT."

        roof_text = to_ifc(
            _mesh_scene("Gable Roof", [*self.A, *self.B, *self.C], [0, 1, 2]),
            schema="IFC2X3",
        )
        roof_attrs = _split_attributes(_entity_line(roof_text, "IFCROOF("))
        assert len(roof_attrs) == ifc2x3_counts["IfcRoof"] == 9
        assert roof_attrs[8] == ".NOTDEFINED."

        door_text = to_ifc(
            _mesh_scene("Front Door", [*self.A, *self.B, *self.C], [0, 1, 2]),
            schema="IFC2X3",
        )
        door_attrs = _split_attributes(_entity_line(door_text, "IFCDOOR("))
        assert len(door_attrs) == ifc2x3_counts["IfcDoor"] == 10
        _assert_dense_ids(door_text)
