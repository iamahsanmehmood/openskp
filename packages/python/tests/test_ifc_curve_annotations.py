"""Loose-edge curve sets in the IFC, as IfcAnnotation products.

A drawing-style model - a facade elevation, a section outline - is *only*
loose edges. It has no faces, so the mesh loop contributes nothing and the
export used to be a spatial skeleton with no elements in it at all.

Three separate concerns are pinned here:

* The product. IfcAnnotation + IfcGeometricCurveSet is IFC4's shape for
  zero-thickness line work, and the layer assignment must carry the curve id
  or the layer the author drew on never reaches the file.
* The analytic arc. A run the scene proved is one whole circular arc is
  emitted as an IfcIndexedPolyCurve with IfcArcIndex segments; everything else
  stays a polyline of its own chords.
* The failure mode that hides all of the above: IfcArcIndex is a defined type,
  so a standalone ``#n=IFCARCINDEX(...)`` line is a syntax error - and
  ifcopenshell 0.8.5 answers a syntax error by truncating the file at that
  line without a word. Everything written after it, the IfcAnnotation
  included, silently disappears. Two different instruments are pointed at
  that: the segment is asserted to be written inline and to have no entity of
  its own, and ``test_a_reader_actually_parses_the_whole_file`` counts what an
  actual reader hands back, because a syntax the writer believes is wrong is
  only *wrong* if a reader chokes on it.
"""
from __future__ import annotations

import pathlib
import re
from array import array
from typing import Any, Dict, List, Optional

import pytest

from openskp.export.ifc import to_ifc
from openskp.model import SkpFile
from openskp.scene import (CurveSetMetadata, GlbPrimitive, InstanceNode,
                           MeshMetadata, Scene)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
CAPILLA = FIXTURES / "capilla_quiroz_v17.skp"

# One circular arc, as scene._solve_run_arc emits it: three points, the ends
# and the middle, in the same glTF-Y-up metre frame as CurveSetMetadata
# .points_m, and 0-based index tuples into them.
ARC = {"points": [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)],
       "segments": [(0, 1, 2)]}


def _curve_set(name: str = "Curve", layer: str = "Layer0", path: str = "ROOT",
               points: Optional[List[Any]] = None, closed: bool = False,
               arc: Optional[Dict[str, Any]] = None,
               attribute_dictionaries: Optional[Dict[str, Dict[str, str]]] = None,
               ) -> CurveSetMetadata:
    return CurveSetMetadata(
        name=name, layer=layer, path=path,
        # A two-point run by default: a run of fewer than two points is not a
        # curve and the exporter skips it (asserted separately).
        points_m=list(points) if points is not None
        else [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)],
        closed=closed, arc=arc,
        attribute_dictionaries=attribute_dictionaries or {})


def _curve_scene(*curve_sets: CurveSetMetadata) -> Scene:
    return Scene(scene_hierarchy=InstanceNode(name="Root"),
                 curve_sets=list(curve_sets))


def _mesh_scene(geom_name: str, path: str = "") -> Scene:
    prim = GlbPrimitive(
        positions=array("f", [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0]),
        normals=array("f", [0.0, 0.0, 1.0] * 3),
        uvs=array("f", [0.0, 0.0] * 3),
        indices=array("I", [0, 1, 2]),
        material_index=0,
        geom_name=geom_name,
    )
    return Scene(
        scene_hierarchy=InstanceNode(name="Root"),
        mesh_index={geom_name: MeshMetadata(name=geom_name, path=path)},
        glb_primitives=[prim],
        gltf_materials=[{"pbrMetallicRoughness":
                         {"baseColorFactor": [0.5, 0.5, 0.5, 1.0]}}],
    )


def _entity_line(text: str, token: str) -> str:
    for line in text.splitlines():
        if token in line:
            return line
    raise AssertionError(f"no entity line containing {token!r}")


def _split_attributes(line: str) -> List[str]:
    """Top-level comma-separated attributes of one STEP entity line."""
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


def _assert_ids_are_dense(text: str) -> int:
    """Every ``#n=`` line counted once, with no gap before the highest id.

    Entity ids are handed out contiguously, so ``len(ids) == max(ids)``
    catches an id that was allocated and then never emitted. That is the
    *writer's* own bookkeeping - it cannot see a reader that stops early, so
    it is a net under the writer, not a substitute for reading the file back.
    """
    ids = [int(m.group(1)) for m in re.finditer(r"^#(\d+)=", text, re.M)]
    assert ids, "no entity lines at all"
    assert len(set(ids)) == len(ids), "an entity id was used twice"
    assert len(ids) == max(ids), (
        f"{len(ids)} entity lines but the highest id is {max(ids)} - the file "
        f"is truncated, and everything past the cut is silently gone")
    return len(ids)


class TestPolylineCurveSets:
    """A run that is not a proven arc keeps the chords the file stores."""

    def test_a_run_becomes_a_polyline_of_its_points(self):
        text = to_ifc(_curve_scene(_curve_set(
            points=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)])))
        refs = _split_attributes(_entity_line(text, "IFCPOLYLINE("))[0]
        ids = re.findall(r"#(\d+)", refs)
        assert len(ids) == 3
        for point_id in ids:
            assert f"#{point_id}=IFCCARTESIANPOINT(" in text
        # glTF Y-up metres -> IFC Z-up millimetres, the same swap the mesh
        # branch applies to positions: y becomes -z, z becomes y.
        assert "IFCCARTESIANPOINT((0.0,-0.0,0.0))" in text
        assert "IFCCARTESIANPOINT((1000.0,-0.0,0.0))" in text
        assert "IFCCARTESIANPOINT((0.0,-1000.0,0.0))" in text

    def test_a_closed_run_repeats_its_first_point(self):
        # IfcPolyline has no implicit closing segment, so a closed run has to
        # repeat its first coordinate or the last gap renders open.
        text = to_ifc(_curve_scene(_curve_set(
            points=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)],
            closed=True)))
        refs = _split_attributes(_entity_line(text, "IFCPOLYLINE("))[0]
        ids = re.findall(r"#(\d+)", refs)
        assert len(ids) == 4
        coords = [re.search(r"IFCCARTESIANPOINT\(\((.*?)\)\)",
                            _entity_line(text, f"#{pid}=IFCCARTESIANPOINT(")
                            ).group(1)
                  for pid in ids]
        assert coords[0] == coords[-1]
        assert len(set(coords)) == 3


class TestAnalyticArcs:
    """A proven arc survives as an arc, not as its tessellation."""

    def test_an_arc_becomes_an_indexed_polycurve(self):
        text = to_ifc(_curve_scene(_curve_set(name="A1", points=[(0.0, 0.0, 0.0)] * 3,
                                              arc=ARC)))
        assert "IFCCARTESIANPOINTLIST3D(" in text
        assert "IFCINDEXEDPOLYCURVE(" in text
        assert "(IFCARCINDEX((1,2,3)))" in text
        # SelfIntersect, the third attribute.
        attrs = _split_attributes(_entity_line(text, "IFCINDEXEDPOLYCURVE("))
        assert attrs[2] == ".F."

    def test_the_segment_is_inline_and_has_no_entity_of_its_own(self):
        # IfcArcIndex is a defined type, not an entity, so it cannot take a
        # line: `#24=IFCARCINDEX((1,2,3));` is a syntax error, and the reader
        # truncates there without a word.
        text = to_ifc(_curve_scene(_curve_set(points=[(0.0, 0.0, 0.0)] * 3,
                                              arc=ARC)))
        assert not re.search(r"^#\d+=IFCARCINDEX", text, re.M)
        assert text.count("IFCARCINDEX") == 1

    def test_the_coord_list_is_1_based(self):
        # IfcArcIndex holds IfcPositiveInteger references into the point list,
        # which is 1-based - an off-by-one silently draws a different arc.
        text = to_ifc(_curve_scene(_curve_set(points=[(0.0, 0.0, 0.0)] * 3,
                                              arc=ARC)))
        points = _split_attributes(_entity_line(text, "IFCCARTESIANPOINTLIST3D("))[0]
        count = points.count("),(") + 1
        index = re.search(r"IFCARCINDEX\(\(([\d,]+)\)\)", text).group(1)
        assert [int(i) for i in index.split(",")] == [1, 2, count]
        assert count == len(ARC["points"])

    def test_a_two_segment_arc_shares_the_point_between_them(self):
        # A full turn is two half-turns meeting at the point they share, and
        # the second closes back on the first.
        arc = {"points": [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0),
                          (3.0, 0.0, 0.0)],
               "segments": [(0, 1, 2), (2, 3, 0)]}
        text = to_ifc(_curve_scene(_curve_set(points=[(0.0, 0.0, 0.0)] * 4,
                                              arc=arc)))
        assert "(IFCARCINDEX((1,2,3)),IFCARCINDEX((3,4,1)))" in text


class TestAnnotationProduct:
    """The curve set as a real IfcProduct, in its own representation context."""

    def test_a_curve_set_becomes_an_annotation(self):
        text = to_ifc(_curve_scene(_curve_set(name="Panel")))
        assert text.count("IFCANNOTATION(") == 1
        assert "IFCGEOMETRICCURVESET(" in text
        assert "'Annotation','GeometricCurveSet'" in text

    def test_the_sub_context_is_an_annotation_under_the_model_context(self):
        # Line work belongs in a sub-context, not on the Model context a body
        # belongs to, so a consumer can tell a solid from an annotation.
        text = to_ifc(_curve_scene(_curve_set()))
        attrs = _split_attributes(
            _entity_line(text, "IFCGEOMETRICREPRESENTATIONSUBCONTEXT("))
        assert attrs[0] == "'Annotation'"
        # Dimension, precision, world coordinate system and true north are
        # DERIVE FROM ParentContext in IFC4, so they are derived (`*`) rather
        # than re-stated.
        assert attrs[2:6] == ["*", "*", "*", "*"]

    def test_every_curve_set_gets_its_own_annotation(self):
        text = to_ifc(_curve_scene(_curve_set(name="A"), _curve_set(name="B"),
                                   _curve_set(name="C")))
        assert text.count("IFCANNOTATION(") == 3
        assert text.count("IFCGEOMETRICCURVESET(") == 3
        assert text.count("IFCPRODUCTDEFINITIONSHAPE(") >= 3

    def test_the_layer_assignment_carries_the_curve(self):
        # The assignment takes representation items, so the curve itself is
        # what carries the loose edge's layer across to the IFC.
        text = to_ifc(_curve_scene(_curve_set(layer="A-GLAZ-CWMG")))
        curve_id = re.search(r"^#(\d+)=IFCPOLYLINE", text, re.M).group(1)
        line = _entity_line(text, "IFCPRESENTATIONLAYERWITHSTYLE(")
        assert "'A-GLAZ-CWMG'" in line
        assert f"#{curve_id}" in _split_attributes(line)[2]

    def test_a_run_of_fewer_than_two_points_is_skipped(self):
        # A single point is not a curve, and an empty one came from nowhere.
        # Skipping is better than writing an IfcPolyline of one coordinate,
        # which no consumer can do anything with.
        for points in ([], [(1.0, 2.0, 3.0)]):
            text = to_ifc(_curve_scene(_curve_set(points=points)))
            assert "IFCANNOTATION(" not in text
            assert "IFCPOLYLINE(" not in text
            _assert_ids_are_dense(text)

    def test_attribute_dictionaries_become_property_sets(self):
        # This is how an inference result reaches a curve-only model: there
        # are no meshes to hang it on, so without this the basis for the
        # classification would not ship at all.
        text = to_ifc(_curve_scene(_curve_set(attribute_dictionaries={
            "AI_Classification": {"ifc_class": "IfcCurtainWall",
                                  "basis": "A-GLAZ-CWMG"}})))
        assert "'Pset_AI_Classification'" in text
        assert "IFCTEXT('IfcCurtainWall')" in text
        assert "IFCTEXT('A-GLAZ-CWMG')" in text


class TestNoSilentTruncation:
    """The tail of the file has to survive the reader."""

    def test_a_curve_only_export_is_dense(self):
        text = to_ifc(_curve_scene(_curve_set(points=[(0.0, 0.0, 0.0),
                                                      (1.0, 0.0, 0.0)])))
        _assert_ids_are_dense(text)

    def test_the_annotation_survives_an_arc(self):
        # The arc's segment is the thing that used to be unparsable, and the
        # annotation, its layer assignment and the containment relationship
        # all come after it - so they are what a truncation removes.
        text = to_ifc(_curve_scene(_curve_set(points=[(0.0, 0.0, 0.0)] * 3,
                                              arc=ARC)))
        _assert_ids_are_dense(text)
        assert "IFCANNOTATION(" in text
        assert "IFCPRESENTATIONLAYERWITHSTYLE(" in text
        assert "IFCRELCONTAINEDINSPATIALSTRUCTURE(" in text

    def test_a_reader_actually_parses_the_whole_file(self, tmp_path):
        # The density check above is the writer grading its own homework. This
        # is the reader: ifcopenshell 0.8.5 truncates silently on a syntax
        # error and its validate() still reports "0 problems", because the
        # entities it never read cannot be wrong. Counting what came back is
        # the only instrument that sees it.
        #
        # Optional on purpose: this package does not depend on ifcopenshell,
        # and CI may not have it. Skipping here is honest; asserting nothing
        # would not be.
        ifcopenshell = pytest.importorskip("ifcopenshell")
        path = tmp_path / "arc.ifc"
        path.write_text(
            to_ifc(_curve_scene(_curve_set(name="A1",
                                           points=[(0.0, 0.0, 0.0)] * 3,
                                           arc=ARC))),
            encoding="utf-8")
        model = ifcopenshell.open(str(path))
        # The arc itself, as a curve rather than a polyline of its chords.
        assert len(model.by_type("IfcIndexedPolyCurve")) == 1
        # The products and relationships that come *after* it in the file -
        # the ones a truncation at the segment would take with it.
        assert len(model.by_type("IfcAnnotation")) == 1
        assert len(model.by_type("IfcGeometricCurveSet")) == 1
        assert len(model.by_type("IfcPresentationLayerWithStyle")) == 1
        assert len(model.by_type("IfcRelContainedInSpatialStructure")) == 1

    def test_a_real_files_curve_sets_arrive_intact(self):
        # capilla's 20 loose-edge runs: 5 at the model root, 15 inside the
        # "puerta" component. None of them is a proven arc, so they stay
        # polylines - asserted, so this test cannot pass by finding arcs that
        # are not there.
        text = to_ifc(SkpFile.open(str(CAPILLA)).build_scene())
        _assert_ids_are_dense(text)
        assert text.count("IFCANNOTATION(") == 20
        assert text.count("IFCPOLYLINE(") == 20
        assert "IFCINDEXEDPOLYCURVE(" not in text


class TestClassifierArity:
    """A caller-supplied classifier is called with what it can accept."""

    def test_a_two_parameter_classifier_is_called_as_before(self):
        seen: List[Any] = []

        def classify(name: str, layer: str) -> Any:
            seen.append((name, layer))
            return "IFCCOLUMN", "IfcColumn"

        text = to_ifc(_mesh_scene("Stud 12"), classifier=classify)
        assert seen == [("Stud 12", "Layer0")]
        assert "IFCCOLUMN(" in text

    def test_a_three_parameter_classifier_receives_the_hierarchy_path(self):
        # The path is the same string used as the key in Scene.mesh_index and
        # on InstanceNode, so it is the handle a classifier needs to consult
        # the instance's definition name, node layer or attribute
        # dictionaries.
        seen: List[Any] = []

        def classify(name: str, layer: str, path: str) -> Any:
            seen.append((name, layer, path))
            return "IFCCOLUMN", "IfcColumn"

        to_ifc(_mesh_scene("Stud 12", path="ROOT / W1 / Stud 12"),
               classifier=classify)
        assert seen == [("Stud 12", "Layer0", "ROOT / W1 / Stud 12")]

    def test_a_type_error_inside_the_classifier_is_not_swallowed(self):
        # Arity is read off the signature rather than probed by calling and
        # catching TypeError: a classifier raising TypeError for its own
        # reasons would otherwise be silently re-run with fewer arguments and
        # its real error lost.
        def classify(name: str, layer: str) -> Any:
            raise TypeError("boom from inside the classifier")

        with pytest.raises(TypeError, match="boom from inside the classifier"):
            to_ifc(_mesh_scene("Stud 12"), classifier=classify)
