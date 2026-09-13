"""Baking loose edges into the scene, and the analytic arc behind one.

Loose edges - edges no face uses - are how SketchUp stores drawing geometry: a
facade elevation, a section outline. A model made only of them has no faces at
all, so before this the scene came out with no ``glb_primitives`` and the IFC
export was a spatial skeleton with nothing in it.

Two mechanisms are pinned down here, and they fail for different reasons:

* ``_chain_loose_edges`` groups edges by the file's own Curve, never by vertex
  adjacency. Two Curves that touch at a vertex are two Curves; joining them
  invents a curve the author did not draw.
* ``_solve_run_arc`` returns a frame only when the file proves the run is one
  whole circular arc, and ``None`` otherwise. ``None`` is the safe answer -
  the caller then emits the chords the file actually stores - so every test
  that expects an arc is paired with one that expects ``None`` for a case that
  looks similar.
"""
from __future__ import annotations

import math
import pathlib
from typing import Any, Dict, Optional, Tuple

import pytest

from openskp import _core, scene
from openskp.model import SkpFile
from openskp.scene import INCHES_TO_M

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
CAPILLA = FIXTURES / "capilla_quiroz_v17.skp"   # classic, 20 loose-edge runs
GONDOLA = FIXTURES / "gondola_v20.skp"          # classic, layered edges
UNTITLED = FIXTURES / "Untitled.skp"            # VFF/ZIP, the _core walker

# arc.skp's own frame: |x_axis| == |y_axis| == 40.0 inches, sweeping 0..pi/2.
RADIUS_IN = 40.0
# The identity transform, in _core.transform_point's 13-double layout: the 3x3
# occupies [0:9] and the translation [9:12]. (The 12th slot is not read.)
IDENTITY = [1.0, 0.0, 0.0,
            0.0, 1.0, 0.0,
            0.0, 0.0, 1.0,
            0.0, 0.0, 0.0,
            1.0]


class _FakeBuilder:
    """A duck-typed stand-in for a parser's geometry builder.

    Both functions under test read only ``edges``, ``faces``, ``edge_curves``
    and ``arc_curves`` off the builder, so the frame can be driven directly
    instead of being dug out of a binary fixture.
    """

    def __init__(self, edges: Optional[Dict[Any, Any]] = None,
                 faces: Optional[Dict[Any, Any]] = None,
                 edge_curves: Optional[Dict[Any, Any]] = None,
                 arc_curves: Optional[Dict[Any, Any]] = None) -> None:
        self.edges = edges or {}
        self.faces = faces or {}
        self.edge_curves = edge_curves or {}
        self.arc_curves = arc_curves or {}


def _frame(radius: float = RADIUS_IN, sweep: float = math.pi / 2) -> Dict[str, Any]:
    """A circular frame in the layout ``legacy._read_arccurve`` produces."""
    return {
        "center": (0.0, 0.0, 0.0),
        "normal": (0.0, 0.0, 1.0),
        "x_axis": (radius, 0.0, 0.0),
        "y_axis": (0.0, radius, 0.0),
        "start_angle": 0.0,
        "end_angle": sweep,
    }


def _arc_point(t: float, radius: float = RADIUS_IN) -> Tuple[float, float, float]:
    """Where *t* lands, in the caller's world-space glTF-Y-up metre frame.

    The frame parametrises ``center + cos(t)*x_axis + sin(t)*y_axis`` in the
    file's own inches; the caller's points are that, axis-swapped and scaled.
    """
    return (radius * math.cos(t) * INCHES_TO_M,
            0.0,
            -radius * math.sin(t) * INCHES_TO_M)


def _real_loose_builder(path: pathlib.Path, definition: str) -> _FakeBuilder:
    """One definition's *actual* parser tables, with its faces removed.

    Dropping the faces is the only edit, and it is what makes the definition's
    own curved edges count as loose. The edges, the curve ids and the arc
    frames are exactly what the parser read out of the file.
    """
    for d in _core.full_parse(str(path))["defs_dict"].values():
        if d.get("name") == definition and d.get("builder") is not None:
            b = d["builder"]
            return _FakeBuilder(
                edges=dict(b.edges),
                faces={},
                edge_curves=dict(getattr(b, "edge_curves", None) or {}),
                arc_curves=dict(getattr(b, "arc_curves", None) or {}))
    raise AssertionError(f"no definition named {definition!r} in {path.name}")


def _closed_run(n: int) -> Optional[Dict[str, Any]]:
    """Solve a closed run of *n* vertices spread evenly over a full turn."""
    builder = _FakeBuilder(
        edge_curves={i: 17 for i in range(1, n + 1)},
        arc_curves={17: _frame(sweep=2.0 * math.pi)},
    )
    pts = [_arc_point(2.0 * math.pi * i / n) for i in range(n)]
    return scene._solve_run_arc(builder, list(range(1, n + 1)), IDENTITY, pts, True)


class TestChainLooseEdges:
    """Grouping comes from the file, not from vertex adjacency."""

    def test_two_curves_touching_at_a_vertex_stay_two_runs(self):
        # edges 1=(a,b) and 2=(b,c) share vertex b, so by adjacency they are
        # one polyline. The file says two Curves, and joining them would
        # invent a curve the author did not draw.
        builder = _FakeBuilder(edges={1: ("a", "b"), 2: ("b", "c")},
                               edge_curves={1: 10, 2: 11})
        runs = scene._chain_loose_edges(builder)
        assert sorted(len(chain) for _, chain, _ in runs) == [2, 2]

    def test_an_edge_with_no_curve_is_its_own_run(self):
        # ``edge.curve == nil``. HyparHut carries a curve on 0 of its 90649
        # edges, so chaining such edges by adjacency would invent an
        # 90649-edge curve that is not in the file.
        builder = _FakeBuilder(edges={1: ("a", "b"), 2: ("b", "c")})
        runs = scene._chain_loose_edges(builder)
        assert runs == [([1], ["a", "b"], False), ([2], ["b", "c"], False)]

    def test_edges_used_by_a_face_are_not_loose(self):
        # They are already represented by that face's mesh; emitting them
        # again would draw every solid as line work on top of itself.
        builder = _FakeBuilder(edges={1: ("a", "b"), 2: ("b", "c")},
                               faces={9: {"loops": [[(1, True)]]}})
        assert scene._chain_loose_edges(builder) == [([2], ["b", "c"], False)]

    def test_a_curve_is_ordered_end_to_end(self):
        # A Curve is a path, so its edges must come out as one run in order.
        # The grouping is the file's; only the order is derived here.
        builder = _FakeBuilder(edges={1: ("a", "b"), 2: ("b", "c"), 3: ("c", "d")},
                               edge_curves={1: 10, 2: 10, 3: 10})
        assert scene._chain_loose_edges(builder) == [
            ([1, 2, 3], ["a", "b", "c", "d"], False)]

    def test_a_closed_curve_does_not_repeat_its_first_vertex(self):
        # The flag carries the closure, not a repeated point: IFC closes a
        # polyline by repeating the first coordinate, and the exporter does
        # that from this flag. A repeated vertex here would close it twice.
        builder = _FakeBuilder(edges={1: ("a", "b"), 2: ("b", "c"), 3: ("c", "a")},
                               edge_curves={1: 10, 2: 10, 3: 10})
        runs = scene._chain_loose_edges(builder)
        assert len(runs) == 1
        _, chain, closed = runs[0]
        assert closed is True
        assert chain == ["a", "b", "c"]


class TestTheParsersOwnCurvesDriveTheChaining:
    """The join between the parser tables and the chaining, on real output.

    No file in this corpus has a *loose* edge that belongs to a Curve: in
    capilla and gondola every curved edge also bounds a face, so its curve id
    never reaches the chaining. That leaves the two mechanisms tested apart -
    the parser's table here, the grouping there - and the place they meet
    unguarded. These two tests are that place: the tables come straight from
    the parser, and the only thing done to them is dropping the faces.
    """

    def test_a_classic_definitions_curves_become_runs(self):
        # gondola, "Laura": 995 edges, 929 of them in 154 Curves. The 66 edges
        # with no curve stay two-point runs of their own and every Curve
        # becomes one run, so the run count is forced by the file's own
        # grouping rather than by any adjacency rule.
        builder = _real_loose_builder(GONDOLA, "Laura")
        runs = scene._chain_loose_edges(builder)
        curves = set(builder.edge_curves.values())
        unchained = len(builder.edges) - len(builder.edge_curves)
        assert (unchained, len(curves)) == (66, 154)
        assert len(runs) == unchained + len(curves)
        # 21 of the 154 Curves carry one edge each, so 133 runs are longer
        # than a bare pair - i.e. the grouping did happen.
        assert sum(1 for _, c, _ in runs if len(c) > 2) == 133
        assert max(len(c) for _, c, _ in runs) == 42
        assert sum(1 for _, _, closed in runs if closed) == 1

    def test_a_vff_definitions_curves_become_runs(self):
        # Untitled, "Group240#2": 442 edges, 336 in 14 Curves, read through
        # BB0B instead of the classic CCurve pointer. Same relation - which is
        # the cross-parser agreement the whole path rests on.
        builder = _real_loose_builder(UNTITLED, "Group240#2")
        runs = scene._chain_loose_edges(builder)
        curves = set(builder.edge_curves.values())
        unchained = len(builder.edges) - len(builder.edge_curves)
        assert (unchained, len(curves)) == (106, 14)
        assert len(runs) == unchained + len(curves)
        assert sum(1 for _, c, _ in runs if len(c) > 2) == 14
        assert max(len(c) for _, c, _ in runs) == 24
        assert sum(1 for _, _, closed in runs if closed) == 14


class TestSolveRunArcAccepts:
    """Runs the file proves are one whole circular arc."""

    def test_whole_curve_on_a_circular_frame(self):
        builder = _FakeBuilder(edge_curves={1: 17}, arc_curves={17: _frame()})
        pts = [_arc_point(t) for t in (0.0, math.pi / 8, math.pi / 4,
                                       3 * math.pi / 8, math.pi / 2)]
        arc = scene._solve_run_arc(builder, [1], IDENTITY, pts, False)
        assert arc is not None
        assert arc["segments"] == [(0, 1, 2)]
        assert len(arc["points"]) == 3
        # The points are the frame's own - the ends and the sweep's midpoint -
        # not the vertices the file happened to store at pi/8 intervals.
        assert arc["points"][0] == pytest.approx(_arc_point(0.0))
        assert arc["points"][1] == pytest.approx(_arc_point(math.pi / 4))
        assert arc["points"][2] == pytest.approx(_arc_point(math.pi / 2))

    def test_a_full_turn_becomes_two_half_turns(self):
        # A closed run sums to a full turn, and one IfcArcIndex cannot state
        # that: its three points would make the first and last coincide, so
        # the arc through them is not determined. Two half-turns can.
        arc = _closed_run(24)
        assert arc is not None
        assert arc["segments"] == [(0, 1, 2), (2, 3, 0)]
        assert len(arc["points"]) == 4

    def test_a_coarse_circle_is_not_refused_on_a_float_boundary(self):
        # SketchUp's Circle tool takes a segment count, so a 4-sided circle is
        # authorable. Its vertices sit exactly a quarter turn apart, and the
        # four steps of a closed run do not all round the same way - the
        # closing one comes out ~2e-16 over a bare ``pi/2`` bound, which used
        # to refuse the whole run on float noise.
        arc = _closed_run(4)
        assert arc is not None
        assert arc["segments"] == [(0, 1, 2), (2, 3, 0)]


class TestSolveRunArcRejects:
    """Runs the file does not prove - the chords are kept for these."""

    def test_a_split_curve_has_no_single_arc(self):
        # Two edges carry the same curve id but the run under test holds one
        # of them: the curve was split, and a split curve has no single
        # (start, sweep) to state.
        builder = _FakeBuilder(edge_curves={1: 17, 2: 17},
                               arc_curves={17: _frame()})
        pts = [_arc_point(0.0), _arc_point(math.pi / 8)]
        assert scene._solve_run_arc(builder, [1], IDENTITY, pts, False) is None

    def test_an_elliptical_frame_is_refused(self):
        # |y_axis| != |x_axis| is the affine image of a circle - a SketchUp
        # arc that was scaled non-uniformly. IfcArcIndex can only say "the
        # circular arc through these three points", so an ellipse must not
        # take this path even though its vertices do sit on a circle.
        builder = _FakeBuilder(edge_curves={1: 17},
                               arc_curves={17: _frame()})
        builder.arc_curves[17]["y_axis"] = (0.0, RADIUS_IN / 2.0, 0.0)
        pts = [_arc_point(0.0), _arc_point(math.pi / 8)]
        assert scene._solve_run_arc(builder, [1], IDENTITY, pts, False) is None

    def test_a_vertex_off_the_circle_is_refused(self):
        # The arc is emitted from the frame, so a frame that disagrees with
        # the geometry would silently replace the author's chords with a
        # different curve.
        builder = _FakeBuilder(edge_curves={1: 17}, arc_curves={17: _frame()})
        pts = [_arc_point(0.0), _arc_point(math.pi / 8)]
        # 10 mm off, along the arc plane's normal.
        pts[1] = (pts[1][0], pts[1][1] + 0.01, pts[1][2])
        assert scene._solve_run_arc(builder, [1], IDENTITY, pts, False) is None

    def test_rounding_noise_is_not_mistaken_for_a_disagreement(self):
        # The caller rounds its points to 6 decimals in metres, so the
        # tolerance has to clear that rounding - otherwise every arc in every
        # file would be refused for being a micrometre off its own frame.
        builder = _FakeBuilder(edge_curves={1: 17}, arc_curves={17: _frame()})
        pts = [_arc_point(0.0), _arc_point(math.pi / 8)]
        pts[1] = (pts[1][0], pts[1][1] + 1e-6, pts[1][2])
        assert scene._solve_run_arc(builder, [1], IDENTITY, pts, False) is not None

    def test_the_tolerance_is_relative_on_a_large_arc(self):
        # The tolerance is ``max(1e-5, 1e-6 * r)``, and the two terms belong
        # to different jobs: the absolute floor clears the caller's rounding,
        # the relative term keeps a big arc from being refused over a
        # float-sized deviation. Every arc in this corpus is under 2 m, where
        # the floor hides the second term - so it is pinned here on a ~2.5 km
        # radius, where 1e-6 * r is about 2.5 mm: a vertex 1 mm off the circle
        # is still that circle, and one 10 mm off is not.
        radius = 100000.0           # inches, about 2.54 km
        builder = _FakeBuilder(edge_curves={1: 17},
                               arc_curves={17: _frame(radius=radius)})
        start = _arc_point(0.0, radius)
        for off, accepted in ((0.001, True), (0.010, False)):
            # Offset in the plane, along the radius: an offset along the
            # plane's normal barely moves the distance to the centre at all
            # (it changes it by off^2 / 2r), so it would test nothing here.
            pts = [(start[0] + off, start[1], start[2]),
                   _arc_point(math.pi / 8, radius)]
            arc = scene._solve_run_arc(builder, [1], IDENTITY, pts, False)
            assert (arc is not None) is accepted, f"{off} m off the circle"

    def test_an_edge_outside_the_arc_table_is_refused(self):
        builder = _FakeBuilder(edge_curves={1: 17}, arc_curves={17: _frame()})
        pts = [_arc_point(0.0), _arc_point(math.pi / 8)]
        assert scene._solve_run_arc(builder, [2], IDENTITY, pts, False) is None

    def test_a_plain_curve_is_not_an_arc(self):
        # A freehand, welded or polygon Curve is a polyline and stays one.
        builder = _FakeBuilder(edge_curves={1: 17})
        pts = [_arc_point(0.0), _arc_point(math.pi / 8)]
        assert scene._solve_run_arc(builder, [1], IDENTITY, pts, False) is None


class TestSceneCurveSets:
    """End to end: a real file's loose edges arrive in the scene."""

    def test_a_classic_file_bakes_its_loose_edges(self):
        sc = SkpFile.open(str(CAPILLA)).build_scene()
        assert len(sc.curve_sets) == 20
        assert all(len(cs.points_m) == 2 for cs in sc.curve_sets)
        assert all(cs.closed is False and cs.arc is None for cs in sc.curve_sets)

    def test_root_runs_are_named_from_layer_and_sequence(self):
        # Loose geometry at the model root has no name in the file at all -
        # an edge is just an edge, only components get named. Labelling all of
        # them "ROOT" would be honest and useless, so a root run is named from
        # the one signal that does exist, and flagged as this module's own
        # invention so a consumer cannot read it as the author's.
        sc = SkpFile.open(str(CAPILLA)).build_scene()
        root = [cs for cs in sc.curve_sets if cs.path == "ROOT"]
        assert len(root) == 5
        assert sorted(cs.name for cs in root) == [f"Layer0_{i}"
                                                  for i in range(1, 6)]
        assert all(cs.name_is_generated for cs in root)

    def test_runs_inside_a_component_keep_the_real_name(self):
        sc = SkpFile.open(str(CAPILLA)).build_scene()
        inner = [cs for cs in sc.curve_sets if cs.path == "ROOT / puerta"]
        assert len(inner) == 15
        assert {cs.name for cs in inner} == {"puerta"}
        assert {cs.definition_name for cs in inner} == {"puerta"}
        assert not any(cs.name_is_generated for cs in inner)

    def test_an_edge_layer_reaches_the_curve_set(self):
        # gondola's layer 43 resolves to "Gondulas Laterais" and 1102 of its
        # edges sit on it. This is the whole point of surfacing edge_layers:
        # without it every run falls back to the layer it inherited, and a
        # curve-only model - which has no faces to carry a layer either - has
        # no type signal at all.
        sc = SkpFile.open(str(GONDOLA)).build_scene()
        assert {cs.layer for cs in sc.curve_sets} == {"Gondulas Laterais",
                                                      "Layer0"}
        assert sum(1 for cs in sc.curve_sets
                   if cs.layer == "Gondulas Laterais") == 57232

    def test_curve_sets_are_a_cost_lever_only(self):
        # On a model that has both solids and curves, turning the curve sets
        # off must not change the meshes - they are extra line work, not the
        # content. On a curve-only model they are the content, and the flag
        # would be a way to export an empty file.
        on = SkpFile.open(str(CAPILLA)).build_scene()
        off = SkpFile.open(str(CAPILLA)).build_scene(include_curve_sets=False)
        assert on.curve_sets
        assert off.curve_sets == []
        assert len(on.glb_primitives) == len(off.glb_primitives)
        assert list(on.mesh_index) == list(off.mesh_index)
