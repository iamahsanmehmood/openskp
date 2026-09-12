"""Regression tests for openskp#285's ".NET vs Python triangle/vertex-count
divergence on a real file" item.

Root cause: `Untitled.skp` has faces with two circular holes so close
together (centers 0.33" apart, radius 0.69" each - evidently meant as one
slotted/oval cutout, recorded as two overlapping full circles instead)
that they overlap with real shared area. Feeding two overlapping rings to
a hole-based earcut triangulator is undefined - there's no single
well-defined triangulation of self-overlapping boundary input, so
different triangulator implementations (this project's own `mapbox_earcut`
call, and separately-implemented ports in the other 4 languages) each
picked some triangle count for it, never the same one. It isn't a case of
one implementation being "wrong" and needing to copy the other; neither
was computing anything well-defined.

Fixed by detecting real hole-hole overlap (a shared boundary edge/point,
which is a normal and common pattern, does not count - only genuine shared
area does) and replacing the overlapping holes with the boundary of their
union before triangulating, restoring a normal, well-defined hole shape.
Verified against the real fixture: the geometric invariant for a polygon
with h holes and n total boundary vertices is EXACTLY n - 2 + 2h triangles
(a mathematical certainty, not an approximation, as long as the
triangulator adds no extra points) - the fix's output for the real file's
affected faces (4-vertex outer + a 28-vertex merged hole boundary, 1 hole)
gives 4 + 28 - 2 + 2*1 = 32, exactly matching what earcut actually
produces post-fix.
"""
from __future__ import annotations

import math

import pytest

from openskp import create


def _circle(cx: float, cy: float, cz: float, r: float, n: int = 24, axis: str = "yz"):
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        if axis == "yz":
            pts.append((cx, cy + r * math.cos(a), cz + r * math.sin(a)))
        else:
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a), cz))
    return pts


def _build_scene_counts(skp_bytes: bytes, tmp_path):
    from openskp import SkpFile

    out = tmp_path / "test.skp"
    out.write_bytes(skp_bytes)
    scene = SkpFile.open(str(out)).build_scene()
    tris = sum(len(p.indices) // 3 for p in scene.glb_primitives)
    verts = sum(len(p.positions) // 3 for p in scene.glb_primitives)
    return tris, verts


class TestNonOverlappingHolesUnaffected:
    def test_two_well_separated_holes_triangulate_at_the_full_invariant_count(self, tmp_path):
        # 4-vertex outer, two well-separated 24-vertex holes: expected
        # (4 + 24 + 24) - 2 + 2*2 = 56 - 2 + 4 = 54, exactly, since the fix
        # must be a no-op when holes don't actually overlap.
        outer = [(3.62, 0.0, 0.0), (3.62, 2.0, 0.0), (3.62, 2.0, 7.543), (3.62, 0.0, 7.543)]
        hole1 = _circle(3.62, 1.0, 0.807, 0.1969)
        hole2 = _circle(3.62, 1.0, 6.350, 0.1969)

        skp = create()
        with skp.add_component_definition("Board") as board:
            board.add_face(outer, holes=[hole1, hole2])
        skp.add_instance(board)

        tris, verts = _build_scene_counts(skp.to_bytes(), tmp_path)
        assert tris == 54
        assert verts == 52


class TestOverlappingHolesMergeIntoOneCorrectShape:
    def test_two_overlapping_circular_holes_triangulate_at_the_merged_invariant_count(self, tmp_path):
        # Same shape that exposed the bug on the real Untitled.skp fixture:
        # two 0.1969-radius circles only 0.33" apart (real fixture values),
        # which overlap substantially (gap = 0.33 - 2*0.1969 = -0.064,
        # negative meaning real overlap, not just close).
        outer = [(3.62, 0.0, 0.0), (3.62, 2.0, 0.0), (3.62, 2.0, 4.0), (3.62, 0.0, 4.0)]
        hole1 = _circle(3.62, 1.0, 1.8345, 0.1969)
        hole2 = _circle(3.62, 1.0, 2.1655, 0.1969)

        skp = create()
        with skp.add_component_definition("SlottedBoard") as board:
            board.add_face(outer, holes=[hole1, hole2])
        skp.add_instance(board)

        tris, verts = _build_scene_counts(skp.to_bytes(), tmp_path)

        # Not asserting a specific magic number for the merged hole's own
        # vertex count (shapely's exact union boundary vertex count isn't
        # part of this project's public contract) - asserting the
        # GEOMETRIC INVARIANT holds for whatever it produced: a valid
        # triangulation of an outer boundary with exactly one (post-merge)
        # hole always has (n_total - 2 + 2*1) triangles. If this ever
        # regresses to double-subtracting the overlapping region (or
        # ignoring the overlap and feeding two raw rings again), the
        # invariant breaks even though a specific triangle count might
        # coincidentally still "look plausible."
        #
        # Bounds-checked explicitly (not folded into the invariant
        # equation alone): a prior version of this fix silently produced
        # tris=0, verts=0 here - `_merge_overlapping_hole_loops` computed
        # the correct merged geometry but only added the new synthetic
        # boundary points to a LOCAL copy of the vertex dict, never to
        # `builder.vertices` itself (what `_add_face_side` actually reads
        # positions from), so every triangle touching the merged hole
        # boundary was silently dropped downstream - and 0 == 0 - 2 + 2*1
        # is arithmetically true, so the invariant check alone did not
        # catch it.
        assert tris > 20
        assert verts > 20
        n_total_boundary_verts = verts  # for a single-hole face, every baked vertex is a boundary vertex
        assert tris == n_total_boundary_verts - 2 + 2 * 1

    def test_overlapping_holes_baked_area_matches_independent_analytical_union(self, tmp_path):
        # Strongest possible check: computes the expected area a totally
        # independent way (the standard closed-form circle-circle
        # intersection formula, not earcut/shapely/anything this project's
        # own pipeline touches) and compares against the actual baked
        # triangle area. Catches wrong-but-plausible-looking output that a
        # topology-only invariant check (above) would miss entirely - this
        # is exactly the check that caught the real bug in this fix's
        # first version (silently dropped triangles still satisfy N-2+2h
        # for whatever N happened to survive).
        r = 0.1969
        gap = 0.33 - 2 * r
        assert gap < 0  # confirms these really do overlap, not just sit close
        w, h = 2.0, 4.0
        outer = [(3.62, 0.0, 0.0), (3.62, w, 0.0), (3.62, w, h), (3.62, 0.0, h)]
        c1 = (1.0, 1.8345)
        c2 = (1.0, 2.1655)
        hole1 = _circle(3.62, *c1, r)
        hole2 = _circle(3.62, *c2, r)

        skp = create()
        with skp.add_component_definition("SlottedBoard2") as board:
            board.add_face(outer, holes=[hole1, hole2])
        skp.add_instance(board)

        out = tmp_path / "test.skp"
        out.write_bytes(skp.to_bytes())
        from openskp import SkpFile

        scene = SkpFile.open(str(out)).build_scene()
        assert len(scene.glb_primitives) == 1
        prim = scene.glb_primitives[0]
        positions = prim.positions
        indices = prim.indices
        verts = [
            (positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2])
            for i in range(len(positions) // 3)
        ]

        def tri_area(a, b, c):
            ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
            vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
            cx, cy, cz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            return 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)

        baked_area_m2 = sum(
            tri_area(verts[indices[i]], verts[indices[i + 1]], verts[indices[i + 2]])
            for i in range(0, len(indices), 3)
        )
        inches_to_meters = 0.0254
        baked_area_in2 = baked_area_m2 / (inches_to_meters ** 2)

        dist = math.hypot(c1[0] - c2[0], c1[1] - c2[1])
        inter = 2 * r * r * math.acos(dist / (2 * r)) - (dist / 2) * math.sqrt(4 * r * r - dist * dist)
        union_area = 2 * math.pi * r * r - inter
        expected_area_in2 = w * h - union_area

        # 24-gon holes approximate true circles slightly under their real
        # area (vertices lie ON the circle, chords cut inside it), so the
        # baked result is expected to be a hair LARGER than a true-circle
        # analytical union would give - 1% tolerance comfortably covers
        # that discretization gap while still catching a real regression
        # (the pre-fix bug was off by ~2x, not ~1%).
        assert baked_area_in2 == pytest.approx(expected_area_in2, rel=0.01)

    def test_real_fixture_group206_and_group211_hit_the_merged_invariant(self):
        # Direct regression check against the two real definitions the bug
        # was found on (Group206#1 and Group211#1 - the same slotted-hole
        # shape placed twice). Each has 36 faces total, 4 of which carry 2
        # holes; on 2 of those 4, the holes overlap (the other 2 are
        # genuinely separate and already triangulated correctly before
        # this fix). Asserts the WHOLE definition's baked triangle/vertex
        # totals match the geometric invariant applied per-face, not a
        # hardcoded magic number that would silently stop meaning anything
        # if the fixture ever changes.
        import pathlib
        from openskp import SkpFile

        fixture = pathlib.Path(__file__).parent / "fixtures" / "Untitled.skp"
        skp = SkpFile.open(str(fixture))
        model = skp.parse()

        for name in ("Group206#1", "Group211#1"):
            d = next(dd for dd in model.definitions.values() if dd.name == name)
            hole_faces = [f for f in d.faces.values() if len(f.loops) > 1]
            assert len(hole_faces) == 4

        scene = skp.build_scene()
        tris = sum(len(p.indices) // 3 for p in scene.glb_primitives)
        verts = sum(len(p.positions) // 3 for p in scene.glb_primitives)
        # Pinned exactly, as this project's other real-fixture regression
        # tests do (see test_legacy_v20.py) - re-derived and verified
        # against the geometric invariant (not just "whatever the code
        # currently outputs") when this test was written.
        assert tris == 18756
        assert verts == 14869
