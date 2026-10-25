"""An instance scaled through the matrix's scale term (openskp#444).

SketchUp can keep a uniform scale in the homogeneous term of an instance's
transformation instead of in the 3x3: the 3x3 stays as it is, the translation
is in the scaled units, and the 16th value ``w`` is the divisor. SketchUp's
own library reports a 100 in cube placed with ``w = 100`` and a translation of
2000 in as a 1 in cube at x = 20 in. The 13-value matrix stores ``1/w`` at
index 12, and every consumer used to ignore it, so such an instance came out
100x its size and 100x its distance from its parent's origin.

Fixtures: ``instance_scale_term_2021.skp`` (SketchUp 2021 format) and
``instance_scale_term_v6.skp`` (SketchUp 6 format) - one 100 in cube definition
placed twice, once through the 3x3 (scaling 0.01) and once through ``w = 100``
with a translation of 2000 in, built and saved through the installed SketchUp
library. SketchUp reports both cubes as 1 x 1 x 1 in, at x = 0 and x = 20 in.
No private content.
"""
from __future__ import annotations

import math
import pathlib
import random
import struct

import pytest

from openskp import SkpFile, _core
from openskp.export import stl

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
FILES = ["instance_scale_term_2021.skp", "instance_scale_term_v6.skp"]
INCH_MM = 25.4


def _apply(mat, p):
    return _core.transform_point(p, mat)


def _random_matrix(rng):
    """A 13-value matrix with a random rotation-ish block, translation and scale."""
    return [rng.uniform(-2, 2) for _ in range(9)] + [rng.uniform(-50, 50) for _ in range(3)] \
        + [rng.choice([1.0, 1.0, 0.01, 0.5, 2.0, 0.1])]


class TestMatrixArithmetic:
    def test_scale_term_scales_the_translated_point(self):
        # 3x3 identity, translation 2000, scale 0.01: (p + t) * s
        m = [1, 0, 0, 0, 1, 0, 0, 0, 1, 2000.0, 0, 0, 0.01]
        assert _apply(m, (100.0, 100.0, 100.0)) == pytest.approx((21.0, 1.0, 1.0))

    def test_a_scale_of_one_changes_nothing(self):
        m = [2, 0, 0, 0, 3, 0, 0, 0, 4, 5.0, 6.0, 7.0, 1.0]
        assert _apply(m, (1.0, 1.0, 1.0)) == (7.0, 9.0, 11.0)

    def test_scaling_through_the_3x3_and_through_the_term_agree(self):
        via_3x3 = [0.01, 0, 0, 0, 0.01, 0, 0, 0, 0.01, 0.0, 0.0, 0.0, 1.0]
        via_term = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0.0, 0.0, 0.0, 0.01]
        assert _apply(via_3x3, (50.0, 20.0, 10.0)) == pytest.approx(_apply(via_term, (50.0, 20.0, 10.0)))

    @pytest.mark.parametrize("seed", range(40))
    def test_composition_matches_applying_one_after_the_other(self, seed):
        rng = random.Random(seed)
        parent, child = _random_matrix(rng), _random_matrix(rng)
        p = (rng.uniform(-100, 100), rng.uniform(-100, 100), rng.uniform(-100, 100))
        composed = _core.multiply_matrices(parent, child)
        want = _apply(parent, _apply(child, p))
        assert _apply(composed, p) == pytest.approx(want, rel=1e-9, abs=1e-9)

    def test_a_composed_matrix_has_its_scale_folded_in(self):
        parent = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0.0, 0.0, 0.0, 0.01]
        child = [1, 0, 0, 0, 1, 0, 0, 0, 1, 2000.0, 0.0, 0.0, 1.0]
        out = _core.multiply_matrices(parent, child)
        assert out[12] == 1.0
        assert out[9] == pytest.approx(20.0)

    def test_the_parse_still_reports_the_raw_matrix(self):
        model = SkpFile.open(str(FIXTURES / FILES[0])).parse()
        rows = [list(i.matrix) for i in model.root.instances]
        assert any(abs(r[12] - 0.01) < 1e-12 and r[9] == pytest.approx(2000.0) for r in rows)


@pytest.mark.parametrize("name", FILES)
class TestRealFiles:
    def test_scene_places_both_cubes_where_sketchup_does(self, name):
        scene = SkpFile.open(str(FIXTURES / name)).build_scene()
        positions = sorted(n.position_mm for n in scene.scene_hierarchy.children)
        assert positions[0] == pytest.approx((0.0, 0.0, 0.0))
        assert positions[1] == pytest.approx((20 * INCH_MM, 0.0, 0.0))   # 508 mm, not 50,800

    def test_exported_geometry_is_two_unit_inch_cubes(self, name, tmp_path):
        scene = SkpFile.open(str(FIXTURES / name)).build_scene()
        out = tmp_path / "o.stl"
        stl.export(scene, str(out), binary=True)
        data = out.read_bytes()
        count = struct.unpack_from("<I", data, 80)[0]
        xs = []
        for i in range(count):
            for k in range(3):
                xs.append(struct.unpack_from("<3f", data, 84 + i * 50 + 12 + k * 12)[0])
        # binary STL is in metres here: the cubes span x = 0 .. 20 in + 1 in
        assert min(xs) == pytest.approx(0.0, abs=1e-6)
        assert max(xs) == pytest.approx(21 * INCH_MM / 1000.0, rel=1e-4)

    def test_instanced_scene_node_matrices(self, name):
        scene = SkpFile.open(str(FIXTURES / name)).build_instanced_scene()
        placed = list(scene.scene_hierarchy.children)
        assert len(placed) == 2
        for node in placed:
            # both cubes are scaled by 1/100 through the node matrix (glTF, metres)
            assert node.matrix[0] == pytest.approx(0.01)
            assert node.matrix[5] == pytest.approx(0.01)
            assert node.matrix[10] == pytest.approx(0.01)
        tx = sorted(n.matrix[12] for n in placed)
        assert tx[0] == pytest.approx(0.0)
        assert tx[1] == pytest.approx(20 * INCH_MM / 1000.0)             # 0.508 m, not 50.8
        assert all(math.isfinite(v) for n in placed for v in n.matrix)
