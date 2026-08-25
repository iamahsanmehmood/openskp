"""Instanced scene building (openskp#200, ported from TypeScript's
``buildInstancedScene()``/``toInstancedGLB()``).

The strongest correctness evidence available: run BOTH builders over the
repository's real .skp fixtures and require that flattening the instanced
result reproduces the baked builder's world-space triangles exactly. This
covers, on genuine files, everything a synthetic test would cover
piecewise - nested groups/components, instance-painted materials, layers,
front/back materials, textures, holes, mirrored transforms - because
whatever those files happen to contain has to come out the same either way.
"""

import json
import os
import struct

import pytest

from openskp import SkpFile
from openskp.export import glb, instanced_glb

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

# One modern VFF container plus two legacy MFC ones, so both parse paths
# feed the instanced builder here too.
FIXTURES = [
    "SU_File.skp",
    "Untitled.skp",
    "capilla_quiroz_v17.skp",
    "gondola_v20.skp",
    "single_material_v17.skp",
]

# Float32 round-off only, same tolerance and justification as the
# TypeScript reference: the baked path transforms in float64 then stores
# the world-space result as float32; the instanced path stores the
# local-space value as float32 and transforms afterwards. Both are
# single-rounding-step correct, but round at different moments, so a
# coordinate can land one float32 ulp apart between them.
_TOLERANCE = 1e-5


def _mul4(a, b):
    out = [0.0] * 16
    for col in range(4):
        for row in range(4):
            s = 0.0
            for k in range(4):
                s += a[k * 4 + row] * b[col * 4 + k]
            out[col * 4 + row] = s
    return tuple(out)


def _apply_matrix(m, p):
    x, y, z = p
    return (
        m[0] * x + m[4] * y + m[8] * z + m[12],
        m[1] * x + m[5] * y + m[9] * z + m[13],
        m[2] * x + m[6] * y + m[10] * z + m[14],
    )


_IDENTITY4 = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)


def _flatten_instanced(scene):
    """Walk the instanced tree, composing node transforms, and emit every
    triangle in world space - i.e. reconstruct what build_scene() bakes.
    Test-only: the whole point of the instanced output is to avoid
    materialising this."""
    by_id = {r.id: r for r in scene.mesh_resources}
    out = []

    def visit(node, parent_matrix):
        world = _mul4(parent_matrix, node.matrix)
        if node.mesh_resource_id is not None:
            res = by_id.get(node.mesh_resource_id)
            if res:
                for prim in res.primitives:
                    for i in range(0, len(prim.indices), 3):
                        tri = []
                        for k in range(3):
                            vi = prim.indices[i + k]
                            tri.append(_apply_matrix(world, (
                                prim.positions[vi * 3],
                                prim.positions[vi * 3 + 1],
                                prim.positions[vi * 3 + 2],
                            )))
                        out.append((tri[0], tri[1], tri[2], prim.material_index))
        for child in node.children:
            visit(child, world)

    visit(scene.scene_hierarchy, _IDENTITY4)
    return out


def _flatten_baked(scene):
    out = []
    for prim in scene.glb_primitives:
        for i in range(0, len(prim.indices), 3):
            tri = []
            for k in range(3):
                vi = prim.indices[i + k]
                tri.append((prim.positions[vi * 3], prim.positions[vi * 3 + 1], prim.positions[vi * 3 + 2]))
            out.append((tri[0], tri[1], tri[2], prim.material_index))
    return out


def _instanced_buffer_bytes(scene) -> int:
    total = 0
    for r in scene.mesh_resources:
        for p in r.primitives:
            total += (
                len(p.positions) * 4 + len(p.normals) * 4 + len(p.uvs) * 4 + len(p.indices) * 4
            )
    return total


def _baked_buffer_bytes(scene) -> int:
    total = 0
    for p in scene.glb_primitives:
        total += len(p.positions) * 4 + len(p.normals) * 4 + len(p.uvs) * 4 + len(p.indices) * 4
    return total


def _compare_triangles_in_order(actual, expected, actual_materials, expected_materials, tolerance=_TOLERANCE):
    """Compare two triangle sets IN ORDER, with a numeric tolerance and
    materials matched by CONTENT rather than index (both paths build the
    same glTF material table but allocate into it in their own encounter
    order). Both builders walk the same instance tree in the same order and
    group faces by the same rule, so the k-th triangle of one corresponds to
    the k-th triangle of the other - a set/nearest-neighbour comparison
    would happily pair a triangle with the wrong coincident twin instead."""
    worst_delta = 0.0
    first_mismatch = None
    material_mismatches = 0

    n = min(len(actual), len(expected))
    for i in range(n):
        a = actual[i]
        e = expected[i]

        a_mat = json.dumps(actual_materials[a[3]], sort_keys=True)
        e_mat = json.dumps(expected_materials[e[3]], sort_keys=True)
        if a_mat != e_mat:
            material_mismatches += 1
            if first_mismatch is None:
                first_mismatch = f"triangle {i}: material {a_mat} != {e_mat}"

        for pa, pe in zip(a[:3], e[:3]):
            for k in range(3):
                d = abs(pa[k] - pe[k])
                if d > worst_delta:
                    worst_delta = d
                if d > tolerance and first_mismatch is None:
                    first_mismatch = f"triangle {i}: coordinate delta {d:.3e} ({pa} vs {pe})"

    return worst_delta, first_mismatch, material_mismatches


def _walk_metadata_parity(baked_node, instanced_node):
    assert instanced_node.name == baked_node.name
    assert instanced_node.definition_name == baked_node.definition_name
    assert instanced_node.layer == baked_node.layer
    assert instanced_node.position_mm == baked_node.position_mm
    assert instanced_node.properties == baked_node.properties
    assert len(instanced_node.children) == len(baked_node.children)
    for b_child, i_child in zip(baked_node.children, instanced_node.children):
        _walk_metadata_parity(b_child, i_child)


@pytest.mark.parametrize("fixture_name", FIXTURES)
def test_reproduces_build_scenes_world_space_triangles(fixture_name):
    baked = SkpFile.open(os.path.join(FIXTURES_DIR, fixture_name))
    baked.parse()
    baked_scene = baked.build_scene()

    instanced = SkpFile.open(os.path.join(FIXTURES_DIR, fixture_name))
    instanced.parse()
    instanced_scene = instanced.build_instanced_scene()

    baked_tris = _flatten_baked(baked_scene)
    instanced_tris = _flatten_instanced(instanced_scene)

    assert len(instanced_tris) == len(baked_tris)

    worst_delta, first_mismatch, material_mismatches = _compare_triangles_in_order(
        instanced_tris, baked_tris, instanced_scene.gltf_materials, baked_scene.gltf_materials
    )

    assert first_mismatch is None, first_mismatch
    assert material_mismatches == 0
    assert worst_delta < _TOLERANCE


@pytest.mark.parametrize("fixture_name", FIXTURES)
def test_never_stores_more_geometry_than_the_baked_path(fixture_name):
    path = os.path.join(FIXTURES_DIR, fixture_name)
    baked = SkpFile.open(path)
    baked.parse()
    instanced = SkpFile.open(path)
    instanced.parse()

    baked_bytes = _baked_buffer_bytes(baked.build_scene())
    instanced_bytes = _instanced_buffer_bytes(instanced.build_instanced_scene())

    # Equal when nothing repeats; strictly smaller once anything does.
    assert instanced_bytes <= baked_bytes


@pytest.mark.parametrize("fixture_name", FIXTURES)
def test_resolves_the_same_layers_and_dynamic_properties_per_node(fixture_name):
    path = os.path.join(FIXTURES_DIR, fixture_name)
    baked = SkpFile.open(path)
    baked.parse()
    instanced = SkpFile.open(path)
    instanced.parse()

    baked_scene = baked.build_scene()
    instanced_scene = instanced.build_instanced_scene()

    # Walk both trees in lockstep: the instance walk order is identical, so
    # a divergence in metadata shows up as a mismatch here.
    _walk_metadata_parity(baked_scene.scene_hierarchy, instanced_scene.scene_hierarchy)


class TestInstancedGlbExport:
    FIXTURE = os.path.join(FIXTURES_DIR, "capilla_quiroz_v17.skp")

    def _built(self, tmp_path):
        skp = SkpFile.open(self.FIXTURE)
        skp.parse()
        return skp

    def test_omits_images_by_default(self, tmp_path):
        skp = self._built(tmp_path)
        out = instanced_glb.export(skp, str(tmp_path / "out.glb"))
        with open(out, "rb") as f:
            data = f.read()
        json_len = struct.unpack("<I", data[12:16])[0]
        gltf_json = json.loads(data[20:20 + json_len])
        assert "images" not in gltf_json
        assert b"\xff\xd8\xff" not in data

    def test_embeds_textures_when_asked(self, tmp_path):
        skp = self._built(tmp_path)
        out = instanced_glb.export(skp, str(tmp_path / "out_tex.glb"), textures=True)
        with open(out, "rb") as f:
            data = f.read()
        json_len = struct.unpack("<I", data[12:16])[0]
        gltf_json = json.loads(data[20:20 + json_len])
        assert "images" in gltf_json
        assert b"\xff\xd8\xff" in data

    def test_is_smaller_than_the_baked_export_on_a_file_with_repeated_geometry(self, tmp_path):
        # gondola_v20.skp reuses definitions heavily - the instanced export
        # should come out substantially smaller than the baked one.
        baked_skp = SkpFile.open(os.path.join(FIXTURES_DIR, "gondola_v20.skp"))
        baked_skp.parse()
        instanced_skp = SkpFile.open(os.path.join(FIXTURES_DIR, "gondola_v20.skp"))
        instanced_skp.parse()

        baked_out = glb.export(baked_skp, str(tmp_path / "baked.glb"))
        instanced_out = instanced_glb.export(instanced_skp, str(tmp_path / "instanced.glb"))

        assert os.path.getsize(instanced_out) < os.path.getsize(baked_out)

    def test_writes_a_metadata_sidecar(self, tmp_path):
        skp = self._built(tmp_path)
        out = instanced_glb.export(skp, str(tmp_path / "out.glb"))
        meta_path = str(tmp_path / "out_metadata.json")
        assert os.path.exists(meta_path)
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        assert meta["instanced"] is True
        assert meta["total_mesh_resources"] == 3
        assert meta["scene_hierarchy"]["name"] == "ROOT"
