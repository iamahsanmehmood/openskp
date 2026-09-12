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
import pathlib
import re
from array import array
from typing import Dict, List, Optional

from openskp.export.ifc import to_ifc
from openskp.scene import GlbPrimitive, InstanceNode, MeshMetadata, Scene

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
