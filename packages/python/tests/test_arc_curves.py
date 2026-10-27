"""``Definition.arcs``: the analytic arc of every curve that is one (#462).

Fixtures: ``arcs_2017.skp``, ``arcs_2020.skp`` (classic container) and
``arcs_2021.skp`` (VFF) - three arcs built through the installed SketchUp
library:

* a loose arc: centre (0, 0, 0), radius 10, 0 to 90 degrees, 12 segments;
* a disc: a full circle bounding a face, centre (50, 0, 0), radius 5, 24 sides;
* a half disc: an arc closed by its chord, bounding a face, centre (100, 0, 0),
  radius 8, 0 to 180 degrees, 12 segments.

SketchUp reads the same centres, radii and angles back from all three files.
No private content.
"""
from __future__ import annotations

import math
import pathlib

import pytest

from openskp import SkpFile

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
NAMES = ["arcs_2017.skp", "arcs_2020.skp", "arcs_2021.skp"]

# (centre x, radius, sweep in radians, edges, is a full circle)
EXPECTED = [
    (0.0, 10.0, math.pi / 2, 12, False),
    (50.0, 5.0, 2 * math.pi, 24, True),
    (100.0, 8.0, math.pi, 12, False),
]


@pytest.mark.parametrize("name", NAMES)
def test_every_arc_is_read_with_its_centre_radius_and_angles(name):
    model = SkpFile.open(str(FIXTURES / name)).parse()
    arcs = sorted(model.root.arcs.values(), key=lambda a: a.center[0])
    assert len(arcs) == 3
    for arc, (cx, radius, sweep, _, full) in zip(arcs, EXPECTED):
        assert arc.center == pytest.approx((cx, 0.0, 0.0))
        assert arc.normal == pytest.approx((0.0, 0.0, 1.0))
        assert arc.radius == pytest.approx(radius)
        assert arc.start_angle == pytest.approx(0.0)
        assert (arc.end_angle - arc.start_angle) % (2 * math.pi) == pytest.approx(sweep % (2 * math.pi))
        assert arc.is_full_circle is full
        # the parameterisation: x_axis points at the start, y_axis = normal x x_axis
        assert arc.x_axis == pytest.approx((radius, 0.0, 0.0))
        assert arc.y_axis == pytest.approx((0.0, radius, 0.0))


@pytest.mark.parametrize("name", NAMES)
def test_an_arc_is_joined_to_its_edges_by_curve_id(name):
    # the disc and the half disc bound a face; they are arcs all the same
    model = SkpFile.open(str(FIXTURES / name)).parse()
    edges_per_curve: dict = {}
    for edge in model.root.edges.values():
        edges_per_curve[edge.curve_id] = edges_per_curve.get(edge.curve_id, 0) + 1
    by_centre = sorted(model.root.arcs.items(), key=lambda kv: kv[1].center[0])
    assert [edges_per_curve[curve_id] for curve_id, _ in by_centre] == [e[3] for e in EXPECTED]


def test_a_2021_loose_arc_reaches_the_scene_like_a_classic_one():
    # curve_sets[].arc used to be filled for classic files only
    for name in NAMES:
        scene = SkpFile.open(str(FIXTURES / name)).build_scene()
        (curve_set,) = scene.curve_sets
        assert curve_set.arc is not None, name
        assert len(curve_set.arc["points"]) == 3


@pytest.mark.parametrize("name", ["instance_scale_term_v6.skp", "instance_scale_term_2021.skp"])
def test_a_model_without_arcs_has_none(name):
    model = SkpFile.open(str(FIXTURES / name)).parse()
    assert all(not d.arcs for d in model.definitions.values())
    assert not model.root.arcs
