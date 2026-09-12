"""Regression tests for the per-element tables the parsers hand to scene.py.

scene.py can only bake what the parsers surface, and four of the values it
needs were read by the parsers and then dropped on the floor - present in the
file, absent from the model. Each matters downstream for its own reason:

* ``Builder.edge_layers`` - the edge's own layer (VFF ``D207``, the classic
  drawbase). A curve-only model has no faces at all, so this is the *only*
  layer signal it carries; scene.py resolves it through ``layer_id_to_name``
  and ``export/ifc.py`` types elements by the result.
* ``Builder.edge_curves`` - SketchUp's own ``Edge#curve`` (VFF ``BB0B``, the
  classic format's ``CCurve`` pointer). This is the file's own grouping of
  edges into Curves, so scene.py does not have to invent one by vertex
  adjacency.
* ``Builder.arc_curves`` - the 14 doubles of a classic ``CArcCurve``: the
  analytic frame behind an arc's tessellated chords.
* ``face['layer']`` - the classic walker parsed each face's drawbase and
  copied only material/hidden out of it, so every face arrived on whichever
  layer it happened to inherit.

Every count below was read off these exact files. A count on its own is not
enough to trust a table - a table can fill with plausible-looking garbage and
still count right - so each one is paired with a relation the file itself
forces: a unit-length normal, the arc identity ``y_axis == normal x x_axis``,
curve ids that all land inside the ids the file hands out, a layer that has to
be non-zero somewhere.

Two of the reads have no fixture at all: no file here carries a VFF ``D207``.
Those are driven on a single synthetic element record instead
(``TestVffElementRecords``), because a table read over a whole real file
cannot distinguish "nothing in this file is layered" from "the walker never
looks".
"""
from __future__ import annotations

import math
import pathlib
from typing import Any, Dict, List

import pytest

from openskp import _core

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
# Classic (pre-2021 MFC container) - _core.full_parse routes these to legacy.py.
CAPILLA = FIXTURES / "capilla_quiroz_v17.skp"   # v17, circles only, one layer
GONDOLA = FIXTURES / "gondola_v20.skp"          # v20, circles + ellipses, two layers
# VFF/ZIP container - the _core walker.
UNTITLED = FIXTURES / "Untitled.skp"            # v25


def _builders(path: pathlib.Path) -> List[Any]:
    """Every definition's geometry builder in *path*.

    The geometry hangs off ``parsed["defs_dict"][def_id]["builder"]``, not off
    the ``Definition`` object - ``Definition`` has no ``edges``/``faces``.
    """
    parsed = _core.full_parse(str(path))
    return [d["builder"] for d in parsed["defs_dict"].values()
            if d.get("builder") is not None]


def _table(path: pathlib.Path, attr: str) -> Dict[Any, Any]:
    """Merge one per-definition table across every definition in *path*."""
    merged: Dict[Any, Any] = {}
    for builder in _builders(path):
        merged.update(getattr(builder, attr, None) or {})
    return merged


def _face_layers(path: pathlib.Path) -> Dict[Any, int]:
    counts: Dict[Any, int] = {}
    for builder in _builders(path):
        for face in (getattr(builder, "faces", None) or {}).values():
            counts[face.get("layer")] = counts.get(face.get("layer"), 0) + 1
    return counts


def _norm(v: Any) -> float:
    return math.dist(v, (0.0, 0.0, 0.0))


def _cross(a: Any, b: Any) -> Any:
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _tlv(tag: str, payload: bytes = b"", children: Any = None) -> Dict[str, Any]:
    """One TLV node, the shape ``_core``'s walker consumes."""
    return {"tag": tag, "payload": payload, "children": list(children or [])}


def _d007(layer_id: int) -> Dict[str, Any]:
    return _tlv("D007", children=[_tlv("D207", bytes([layer_id]))])


def _edge_node(e_id: int, v1: int, v2: int, layer_id: Any = None,
               curve_id: Any = None) -> Dict[str, Any]:
    """A ``B80B`` element record: two vertex refs, and optionally D207/BB0B."""
    kids = [_tlv("DE05", bytes([e_id])),
            _tlv("B90B", bytes([v1])),
            _tlv("BA0B", bytes([v2]))]
    if layer_id is not None:
        kids.append(_d007(layer_id))
    if curve_id is not None:
        kids.append(_tlv("BB0B", bytes([curve_id])))
    return _tlv("B80B", children=kids)


def _face_node(f_id: int, layer_id: Any = None) -> Dict[str, Any]:
    """An ``AC0D`` element record, whose geometry is left empty on purpose."""
    kids = [_tlv("DE05", bytes([f_id]))]
    if layer_id is not None:
        kids.append(_d007(layer_id))
    return _tlv("AC0D", children=kids)


class TestEdgeLayers:
    """The edge's own layer - the only layer signal a curve-only model has."""

    def test_classic_edges_carry_their_layer(self):
        # gondola: 1102 of its 9186 edges sit on layer 43. The walker stores
        # only non-default ids, so 1102 is the number of *layered* edges, not
        # the number of edges - and every one of them is layer 43.
        layers = _table(GONDOLA, "edge_layers")
        assert len(layers) == 1102
        assert set(layers.values()) == {43}

    def test_a_single_layer_file_leaves_the_table_empty(self):
        # capilla puts every edge on its default layer, so no id is stored.
        # The distinction is the point: "no layered edges in this file" is a
        # fact, "this parser does not read edge layers" was the bug.
        for builder in _builders(CAPILLA):
            assert getattr(builder, "edge_layers", None) is not None
        assert _table(CAPILLA, "edge_layers") == {}


class TestFaceLayers:
    """Each face's own layer, which the classic walker used to drop."""

    def test_classic_faces_carry_their_own_layer(self):
        # 229 of gondola's 1887 faces are on layer 43; the other 1658 report
        # the file's default (0). Before this the drawbase's layer id was read
        # and discarded, so all 1887 looked like the default and
        # export/ifc.py's classifier never saw a layer name.
        assert _face_layers(GONDOLA) == {0: 1658, 43: 229}

    def test_default_layer_zero_is_read_not_dropped(self):
        # capilla is single-layer: the value is the file's own 0, not None.
        # Storing nothing would read back as None, indistinguishable from
        # "this face has no layer".
        assert _face_layers(CAPILLA) == {0: 181}


class TestCurveGrouping:
    """SketchUp's own Edge#curve, so runs need no chaining heuristic."""

    def test_classic_curve_pointers_and_their_ids(self):
        # capilla: 39 edges belong to 6 Curves. All six of those Curves are
        # arcs, so the ids the edges name must be exactly the ids the arc
        # table is keyed by - two independently decoded tables agreeing on one
        # id space. That agreement is the cross-check a bare count cannot give.
        curves = _table(CAPILLA, "edge_curves")
        assert len(curves) == 39
        assert all(isinstance(cid, int) and cid > 0 for cid in curves.values())
        ids = set(curves.values())
        assert len(ids) == 6
        assert ids == set(_table(CAPILLA, "arc_curves"))

    def test_the_two_curve_tables_are_independent(self):
        # gondola: 1480 edges spread over 219 Curves, of which only 30 are
        # arcs. Here the curve ids are *not* a subset of the arc table, which
        # is what exposes an implementation that conflated the two.
        curves = _table(GONDOLA, "edge_curves")
        assert len(curves) == 1480
        assert len(set(curves.values())) == 219
        arcs = set(_table(GONDOLA, "arc_curves"))
        assert len(arcs) == 30
        assert not set(curves.values()) <= arcs


class TestArcFrames:
    """The 14 doubles of a classic CArcCurve, decoded instead of discarded."""

    def test_frame_layout_is_the_files_own(self):
        # The order is center[3], normal[3], x_axis[3], start, end, y_axis[3].
        # Split the 14 doubles any other way and the file's own relations stop
        # holding, so it is these relations - not the field names - that pin
        # the layout down.
        frames = list(_table(CAPILLA, "arc_curves").values())
        assert len(frames) == 6
        for frame in frames:
            assert _norm(frame["normal"]) == pytest.approx(1.0, abs=1e-9)
            # y_axis == normal x x_axis, exactly, on a circle.
            assert math.dist(_cross(frame["normal"], frame["x_axis"]),
                             frame["y_axis"]) == pytest.approx(0.0, abs=1e-9)
            assert _norm(frame["x_axis"]) == pytest.approx(28.827831, abs=1e-5)
            assert _norm(frame["x_axis"]) == pytest.approx(
                _norm(frame["y_axis"]), abs=1e-5)
            assert frame["start_angle"] == pytest.approx(0.0, abs=1e-12)
        assert {round(f["end_angle"], 6) for f in frames} == {0.829263, 0.837889}

    def test_elliptical_frames_are_kept_not_dropped(self):
        # An arc that was scaled non-uniformly keeps its arc identity and
        # stores the affine image of a circle: |x_axis| != |y_axis| and the
        # two are no longer perpendicular. The parser must keep those - it is
        # scene.py's job to decide they cannot become an IfcArcIndex, not the
        # parser's job to throw them away. gondola has 16 of 30.
        frames = list(_table(GONDOLA, "arc_curves").values())
        assert len(frames) == 30
        assert all(_norm(f["normal"]) == pytest.approx(1.0, abs=1e-9)
                   for f in frames)
        elliptical = [f for f in frames
                      if abs(_norm(f["x_axis"]) - _norm(f["y_axis"])) > 1e-9]
        assert len(elliptical) == 16
        # Measured extremes, so a table that kept the frames but scrambled the
        # vectors inside them cannot pass.
        assert max(_norm(f["x_axis"]) for f in frames) == pytest.approx(
            106.849109, abs=1e-5)
        assert min(_norm(f["x_axis"]) for f in frames) == pytest.approx(
            1.291375, abs=1e-5)
        worst = max(math.dist(_cross(f["normal"], f["x_axis"]), f["y_axis"])
                    for f in frames)
        assert worst == pytest.approx(53.424554, abs=1e-5)


class TestVffParserSurfacing:
    """The VFF walker surfaces the same two tables, minus arcs."""

    def test_edge_curves_are_surfaced(self):
        # Untitled (v25): 8832 edges over 368 Curves, read from BB0B.
        curves = _table(UNTITLED, "edge_curves")
        assert len(curves) == 8832
        assert len(set(curves.values())) == 368

    def test_faces_carry_a_layer_key(self):
        # Every VFF face record carries D207. This file has none, so the value
        # is None - but the key has to be there, or a face on a real layer is
        # indistinguishable from a face whose record the walker skipped.
        for builder in _builders(UNTITLED):
            for face in (getattr(builder, "faces", None) or {}).values():
                assert "layer" in face
        assert _face_layers(UNTITLED) == {None: 1588}

    def test_no_arc_frames_are_decoded(self):
        # An honest limit, asserted rather than left implicit so it cannot be
        # mistaken for coverage: the VFF arc payload is not decoded here, and
        # no file in this corpus carries an ARC_CURVES section to decode. A
        # future implementation should delete this test, not work around it.
        assert _table(UNTITLED, "arc_curves") == {}


class TestVffElementRecords:
    """The VFF reads, driven one record at a time.

    No file in this corpus carries a VFF ``D207`` - Untitled's faces are all
    on the layer they inherited - so a table read over a whole real file
    cannot tell "this file has no layered element" from "the walker never
    looks". Handing the walker a record that does carry one is the only way
    that distinction becomes observable, and it is the difference between the
    bug and its absence.
    """

    def test_an_edge_record_yields_its_layer_and_its_curve(self):
        builder = _core._GeometryBuilder()
        _core._extract_geometry_from_nodes(
            [_edge_node(7, 1, 2, layer_id=43, curve_id=17)], builder)
        assert builder.edges == {7: (1, 2)}
        assert builder.edge_layers == {7: 43}
        assert builder.edge_curves == {7: 17}

    def test_an_edge_record_without_d207_stores_nothing(self):
        # Absent means the edge sits on the layer it inherited. Writing the
        # default id here would be this walker inventing a fact about the
        # file, and scene.py would then resolve it to a real layer name.
        builder = _core._GeometryBuilder()
        _core._extract_geometry_from_nodes([_edge_node(7, 1, 2)], builder)
        assert builder.edge_layers == {}
        assert builder.edge_curves == {}

    def test_a_face_record_yields_its_layer(self):
        builder = _core._GeometryBuilder()
        _core._extract_geometry_from_nodes([_face_node(9, layer_id=43)], builder)
        assert builder.faces[9]["layer"] == 43

    def test_a_face_record_without_d207_reports_none(self):
        builder = _core._GeometryBuilder()
        _core._extract_geometry_from_nodes([_face_node(9)], builder)
        assert builder.faces[9]["layer"] is None
