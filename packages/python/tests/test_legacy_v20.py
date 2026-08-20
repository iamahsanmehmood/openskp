"""Real-file regression tests for SketchUp 2020 (v20) classic .skp files.

Fixture: fixtures/gondola_v20.skp - a retail gondola display authored in
SketchUp 2020 (v20.1.235, ~755 KB), shared via the TypeScript port's PR #155.

Before the v20 layout fixes, this file failed the legacy walk with an
"implausible definition count": v20 writes records the v17 layout does not
have, which left the reader a few bytes short and made it read garbage where
a count was expected. The only pre-existing legacy fixture
(capilla_quiroz_v17.skp) has a single layer and never exercised any of these
paths, so the divergence went unnoticed.

Every count below was read off this exact file after the fix and
sanity-checked for plausibility (bounding box in metres, definitions
carrying real geometry, instances actually placed in the scene) - a parse
that "succeeds" while silently dropping placements would still be a bug, so
the instance counts matter as much as the parse not throwing.
"""
from __future__ import annotations

import math
import pathlib

import pytest

from openskp import SkpFile
from openskp.legacy import is_legacy

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "gondola_v20.skp"


def _data() -> bytes:
    return FIXTURE.read_bytes()


class TestLegacyV20Detection:
    def test_is_detected_as_legacy(self):
        assert is_legacy(_data()) is True


class TestLegacyV20Parse:
    def test_parses_a_real_v20_file_that_previously_threw(self):
        skp = SkpFile.open(str(FIXTURE))
        model = skp.parse()

        assert model.version == "{20.1.235}"

        assert len(model.definitions) == 20
        assert len(model.materials) == 24

        # v20 interleaves a null object-ref after EACH layer record; the
        # count is the number of REAL layers. The old reader counted the
        # separators as items and dropped every layer after the first —
        # this fixture really does carry "Gondulas Laterais" (visible in
        # SketchUp), which the previous assertion enshrined as missing.
        # Nulls must still never reach model.layers.
        names = [layer.name for layer in model.layers]
        assert names == ["Layer0", "Gondulas Laterais"]
        for layer in model.layers:
            assert layer is not None
            assert isinstance(layer.name, str)

        # real geometry, not an empty shell
        faces = sum(len(d.faces) for d in model.definitions.values())
        edges = sum(len(d.edges) for d in model.definitions.values())
        vertices = sum(len(d.vertices) for d in model.definitions.values())
        assert faces == 1887
        assert edges == 9174
        assert vertices == 6543

    def test_places_every_root_instance(self):
        skp = SkpFile.open(str(FIXTURE))
        model = skp.parse()
        # 23 root-level placements: the definitions above are useless if the
        # instances that position them in the model are lost, which is
        # exactly what a subtly misaligned walk produces - a file that
        # parses into an almost-empty scene instead of throwing.
        assert len(model.root.instances) == 23

        scene = skp.build_scene()
        assert len(scene.scene_hierarchy.children) == 23
        assert len(scene.glb_primitives) == 201
        assert len(scene.mesh_index) == 201
        assert len(scene.gltf_materials) > 0

    def test_resolves_placed_instances_to_definitions_that_carry_geometry(self):
        # Guards the failure mode a zero entity count produces: the
        # definitions an instance points at come back empty, so the file
        # parses into a scene of correctly-positioned but invisible groups.
        # Counting definitions or instances alone does not catch it - the
        # two have to be checked together.
        skp = SkpFile.open(str(FIXTURE))
        model = skp.parse()

        referenced = set()
        for inst in model.root.instances:
            referenced.add(inst.ref_idx)
        for defn in model.definitions.values():
            for inst in defn.instances:
                referenced.add(inst.ref_idx)

        memo: dict[int, bool] = {}
        in_progress: set[int] = set()

        def carries_geometry(def_id: int) -> bool:
            if def_id in memo:
                return memo[def_id]
            if def_id in in_progress:
                return False  # reference cycle
            in_progress.add(def_id)
            defn = model.definitions.get(def_id)
            # a group whose own geometry lives in nested children still counts
            result = defn is not None and (
                len(defn.faces) > 0
                or any(carries_geometry(child.ref_idx) for child in defn.instances)
            )
            in_progress.discard(def_id)
            memo[def_id] = result
            return result

        empty = [def_id for def_id in referenced if not carries_geometry(def_id)]
        assert empty == []

    def test_bakes_geometry_at_a_plausible_real_world_scale(self):
        skp = SkpFile.open(str(FIXTURE))
        scene = skp.build_scene()
        mn = [math.inf, math.inf, math.inf]
        mx = [-math.inf, -math.inf, -math.inf]
        for prim in scene.glb_primitives:
            pos = prim.positions
            for i in range(0, len(pos), 3):
                for a in range(3):
                    v = pos[i + a]
                    if v < mn[a]:
                        mn[a] = v
                    if v > mx[a]:
                        mx[a] = v
        # a shop gondola display: metres, not the 1e3-off or degenerate box
        # a misaligned read produces
        assert mx[0] - mn[0] == pytest.approx(3.82, abs=0.1)
        assert mx[1] - mn[1] == pytest.approx(3.14, abs=0.1)
        assert mx[2] - mn[2] == pytest.approx(4.82, abs=0.1)

    def test_every_baked_primitive_has_valid_uv_coordinates(self):
        skp = SkpFile.open(str(FIXTURE))
        scene = skp.build_scene()
        assert len(scene.glb_primitives) > 0
        for prim in scene.glb_primitives:
            n_verts = len(prim.positions) // 3
            assert len(prim.uvs) == n_verts * 2
            for uv in prim.uvs:
                assert math.isfinite(uv)
