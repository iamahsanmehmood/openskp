"""Regression tests for the PUBLIC typed-model loose-edge API added
alongside the FreeCAD addon's loose-edge/structural-framing import
support: Edge.layer, Edge.curve_id, Face.layer, and model.loose_edge_runs().

Before this, a definition's loose-edge grouping (SketchUp's own
Edge#curve) and per-edge/per-face layer were only reachable through
build_scene()'s curve_sets or build_instanced_scene()'s curve_resources -
both bake/triangulate. A consumer building real B-rep geometry (a CAD
kernel wire/edge, not a render mesh - which is exactly what the FreeCAD
addon needs) had no way to get the raw edge/vertex ids for a loose-edge
run at all when working directly with SkpFile.parse()'s typed model.

Cross-validated against build_instanced_scene()'s own curve_resources -
already tested (test_instanced_scene.py) against build_scene()'s
curve_sets on every fixture on hand - rather than asserting a number in
isolation: the same run count, in the same order, has to come out of
both the baked-mesh path and this new raw-typed-model path for the same
definition.
"""
import os

import pytest

from openskp import SkpFile
from openskp.model import loose_edge_runs

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

FIXTURES = [
    "SU_File.skp",
    "Untitled.skp",
    "capilla_quiroz_v17.skp",
    "gondola_v20.skp",
    "single_material_v17.skp",
]

INCHES_TO_M = 0.0254


def _to_world_frame(v):
    """Same axis swap + unit scale InstancedCurveResource's LocalCurve
    points use (SketchUp inches, Z-up -> metres, glTF Y-up), so the two
    are directly comparable without re-deriving the convention."""
    return (round(v.x * INCHES_TO_M, 6), round(v.z * INCHES_TO_M, 6), round(-v.y * INCHES_TO_M, 6))


@pytest.mark.parametrize("fixture_name", FIXTURES)
def test_matches_build_instanced_scenes_curve_resources(fixture_name):
    path = os.path.join(FIXTURES_DIR, fixture_name)

    typed = SkpFile.open(path)
    typed_model = typed.parse()

    instanced = SkpFile.open(path)
    instanced_scene = instanced.build_instanced_scene()

    definitions_by_id = dict(typed_model.definitions)
    definitions_by_id["ROOT"] = typed_model.root

    checked_any = False
    for resource in getattr(instanced_scene, "curve_resources", None) or []:
        definition = definitions_by_id.get(resource.definition_id)
        assert definition is not None, f"no matching Definition for {resource.definition_id!r}"

        runs = loose_edge_runs(definition)
        assert len(runs) == len(resource.curves), (
            f"{fixture_name} def={resource.definition_id!r}: "
            f"loose_edge_runs found {len(runs)}, curve_resources has {len(resource.curves)}"
        )

        for (edge_ids, vertex_ids, closed), local_curve in zip(runs, resource.curves):
            assert closed == local_curve.closed
            assert len(vertex_ids) == len(local_curve.points_m)
            for vid, expected_point in zip(vertex_ids, local_curve.points_m):
                vertex = definition.vertices[vid]
                got_point = _to_world_frame(vertex)
                for a, b in zip(got_point, expected_point):
                    assert abs(a - b) < 1e-5, (
                        f"{fixture_name} def={resource.definition_id!r}: "
                        f"point mismatch {got_point} vs {expected_point}"
                    )
        checked_any = True

    if not checked_any:
        # A fixture with no loose edges anywhere (e.g. SU_File.skp) is a
        # legitimate, honest result - confirmed separately below, not
        # silently skipped as if the test found nothing to check.
        for definition in list(typed_model.definitions.values()) + [typed_model.root]:
            assert loose_edge_runs(definition) == []


def test_edge_layer_and_curve_id_are_populated_when_the_file_has_them():
    """gondola_v20.skp is the known fixture with a real per-edge layer
    ('Gondulas Laterais' on definition 45's edges, from the mixed-mesh-
    and-loose-edge layer-fallback bug found and fixed earlier this
    session) - confirms Edge.layer/Edge.curve_id carry real data, not
    just None everywhere."""
    path = os.path.join(FIXTURES_DIR, "gondola_v20.skp")
    model = SkpFile.open(path).parse()

    seen_layer = False
    seen_curve_id = False
    for definition in model.definitions.values():
        for edge in definition.edges.values():
            if edge.layer is not None:
                seen_layer = True
            if edge.curve_id is not None:
                seen_curve_id = True
    assert seen_layer, "expected at least one edge with a real layer id in gondola_v20.skp"
    assert seen_curve_id, "expected at least one edge with a real curve_id in gondola_v20.skp"


def test_face_layer_is_populated_when_the_file_has_it():
    path = os.path.join(FIXTURES_DIR, "gondola_v20.skp")
    model = SkpFile.open(path).parse()

    seen = any(
        face.layer is not None
        for definition in model.definitions.values()
        for face in definition.faces.values()
    )
    assert seen, "expected at least one face with a real layer id in gondola_v20.skp"
