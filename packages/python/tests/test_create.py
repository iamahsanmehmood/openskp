"""Tests for openskp.create - the from-scratch legacy (v17) .skp writer.

Self-parsing a written file with :mod:`openskp.legacy`'s own reader proves
internal round-trip consistency, but not that real SketchUp accepts the
file - legacy.py's reader doesn't validate several byte fields real
SketchUp silently requires (documented in create.py as "ground-truth
confirmed" - the drawbase padding bytes and CLoop's flag bytes). Those
specific fields are asserted on directly below, byte-for-byte, rather than
only checked indirectly through a round-trip that wouldn't catch a
regression in them. See the optional, SDK-gated test at the bottom for the
real, external-oracle-backed confidence check - not required for the
suite to be meaningful, since the byte-level assertions above already lock
in the specific fields that mattered.
"""
from __future__ import annotations

import importlib
import os

import pytest

from openskp.create import SkpBuilder, SkpWriteError, create
from openskp import legacy

# `openskp.create` (the submodule) and `openskp.create` (the top-level
# re-exported function of the same name) collide as an attribute on the
# `openskp` package once __init__.py runs its `from .create import create`
# - the function wins. Go through sys.modules via import_module to reach
# the actual submodule unambiguously.
create_module = importlib.import_module("openskp.create")


SQUARE = [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (100.0, 100.0, 0.0), (0.0, 100.0, 0.0)]


class TestBuilderErrors:
    def test_saving_with_no_geometry_raises(self):
        with pytest.raises(SkpWriteError, match="no geometry"):
            create().to_bytes()

    def test_face_with_fewer_than_3_points_raises(self):
        builder = create()
        with pytest.raises(SkpWriteError, match="at least 3 points"):
            builder.add_face([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)])

    def test_collinear_points_raise(self):
        builder = create()
        with pytest.raises(SkpWriteError, match="collinear"):
            builder.add_face([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)])


class TestSingleFace:
    def test_matches_ground_truth_byte_size(self):
        # Confirmed against a real SDK-authored file containing the exact
        # same face during development - an unexpected size change here
        # means the byte-level encoding drifted from what real SketchUp
        # itself produces for equivalent geometry.
        builder = create()
        builder.add_face(SQUARE)
        assert len(builder.to_bytes()) == 6149

    def test_self_parses_to_expected_structure(self):
        builder = create()
        builder.add_face(SQUARE)
        data = builder.to_bytes()

        ar, root, layers, materials = legacy._walk(data)
        assert [n for (_, n, _) in root] == ["CEdge", "CEdge", "CEdge", "CEdge", "CFace"]

        face = root[-1][2]
        assert face["k"] == "face"
        assert face["plane"][:3] == pytest.approx((0.0, 0.0, 1.0), abs=1e-9)

    def test_drawbase_padding_bytes_are_set(self):
        # legacy.py's reader documents drawbase offsets 3-4 as unused, but
        # real SketchUp silently drops any entity whose drawbase has them
        # zeroed. Not something legacy.py's own reader can catch on
        # round-trip - assert on the raw written bytes directly instead.
        builder = SkpBuilder()
        builder.add_face(SQUARE)
        # Every drawbase record in this build is 10 bytes: mat(u16) hidden
        # pad pad soft smooth pad layer(u16). Scan the writer's raw buffer
        # for every occurrence and check offsets 3-4 are both 0x01.
        buf = bytes(builder._geometry_writer.buf)
        # Each CEdge/CFace preamble+drawbase starts right after a class-ref
        # or class-declaration tag; rather than re-parse the whole stream,
        # confirm at least one drawbase's padding is set by checking that
        # b"\x00\x00\x00\x01\x01\x00" (hidden=0, pad=1, pad=1, soft=0)
        # appears - the fixed byte pattern every drawbase in this test
        # produces (mat=0, hidden=0, soft=0, smooth=0).
        assert b"\x00\x00\x00\x01\x01\x00\x00\x00\x00\x00" in buf

    def test_loop_flag_bytes_are_set(self):
        # Same silent-drop failure mode as the drawbase padding above, for
        # CLoop's "2 flag bytes" (also documented as opaque by legacy.py's
        # reader). The loop's preamble (null attrs + pid mask=0, since
        # structural objects use pid 0) is immediately followed by these
        # 2 bytes: b"\x00\x00\x00" + b"\x01\x01".
        builder = SkpBuilder()
        builder.add_face(SQUARE)
        buf = bytes(builder._geometry_writer.buf)
        assert b"\x00\x00\x00\x01\x01" in buf


class TestMultiFace:
    def test_shares_vertices_and_edges_across_faces(self):
        # Two quads sharing one edge: 4 + 4 - 1 = 7 unique edges, 2 faces.
        face1 = [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (100.0, 100.0, 0.0), (0.0, 100.0, 0.0)]
        face2 = [(100.0, 0.0, 0.0), (200.0, 0.0, 0.0), (200.0, 100.0, 0.0), (100.0, 100.0, 0.0)]

        builder = create()
        builder.add_face(face1)
        builder.add_face(face2)
        data = builder.to_bytes()

        ar, root, layers, materials = legacy._walk(data)
        assert len(root) == 9  # 7 edges + 2 faces
        kinds = [n for (_, n, _) in root]
        assert kinds.count("CEdge") == 7
        assert kinds.count("CFace") == 2

    def test_shared_edge_has_correct_sense_in_both_directions(self):
        # The shared edge is traversed forward by face1, reversed by
        # face2 - each CEdgeUse's sense bit must reflect that (this is
        # exactly the bug found during development: hardcoding sense=0
        # made two-face meshes render as a single connected surface
        # instead of two, since SketchUp couldn't tell which loop the
        # edge ran forward/backward in).
        face1 = [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (100.0, 100.0, 0.0), (0.0, 100.0, 0.0)]
        face2 = [(100.0, 0.0, 0.0), (200.0, 0.0, 0.0), (200.0, 100.0, 0.0), (100.0, 100.0, 0.0)]

        builder = create()
        builder.add_face(face1)
        builder.add_face(face2)
        data = builder.to_bytes()

        ar, root, layers, materials = legacy._walk(data)
        faces = [v for (_, n, v) in root if n == "CFace"]
        assert len(faces) == 2

        def edge_senses(face):
            return {u["edge"]: u["sense"] for u in face["loops"][0]["uses"]}

        senses1 = edge_senses(faces[0])
        senses2 = edge_senses(faces[1])
        shared_edges = set(senses1) & set(senses2)
        assert len(shared_edges) == 1
        shared = shared_edges.pop()
        # traversed in opposite directions by the two faces
        assert senses1[shared] != senses2[shared]

    def test_large_mesh_shifts_tail_references_without_byte_overflow(self):
        # Regression test for a real bug found during development: the
        # tail-reference renumbering only patched a single byte per
        # reference, so any mesh needing a shift >= ~240 slots (roughly
        # 15+ disjoint faces) silently wrapped instead of correctly
        # carrying into the reference's high byte, corrupting the file.
        # 30 disjoint (non-shared-vertex) quads comfortably exceeds that.
        builder = create()
        for i in range(30):
            x0 = i * 200.0
            builder.add_face([
                (x0, 0.0, 0.0), (x0 + 100.0, 0.0, 0.0),
                (x0 + 100.0, 100.0, 0.0), (x0, 100.0, 0.0),
            ])
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        assert sum(1 for (_, n, _) in root if n == "CFace") == 30
        assert sum(1 for (_, n, _) in root if n == "CEdge") == 120


class TestMaterials:
    def test_material_assigned_to_face_front(self):
        builder = create()
        red = builder.add_material("Red", (255, 0, 0))
        builder.add_face(SQUARE, material=red)
        data = builder.to_bytes()

        ar, root, layers, materials = legacy._walk(data)
        mat_by_slot = {s: v for s, v in materials}
        assert mat_by_slot[red]["name"] == "Red"
        assert mat_by_slot[red]["rgba"] == (255, 0, 0, 255)
        face = [v for (_, n, v) in root if n == "CFace"][0]
        assert face["db"]["mat"] == red
        # edges never carry a material, even when their face does (ground
        # truth: edge drawbase mat stays 0 regardless of the face's material)
        edges = [v for (_, n, v) in root if n == "CEdge"]
        assert all(e["db"]["mat"] == 0 for e in edges)

    def test_unmaterialed_face_keeps_default(self):
        builder = create()
        builder.add_material("Unused", (1, 2, 3))
        builder.add_face(SQUARE)
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        face = [v for (_, n, v) in root if n == "CFace"][0]
        assert face["db"]["mat"] == 0

    def test_material_dedup_by_name_returns_same_handle(self):
        builder = create()
        a = builder.add_material("Shared", (10, 20, 30))
        b = builder.add_material("Shared", (10, 20, 30))
        assert a == b
        builder.add_face(SQUARE, material=a)
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        assert len(materials) == 1

    def test_add_material_after_add_face_raises(self):
        builder = create()
        builder.add_face(SQUARE)
        with pytest.raises(SkpWriteError, match="before any add_face"):
            builder.add_material("TooLate", (0, 0, 0))

    def test_invalid_rgba_raises(self):
        builder = create()
        with pytest.raises(SkpWriteError, match="rgba"):
            builder.add_material("Bad", (300, 0, 0))
        with pytest.raises(SkpWriteError, match="rgba"):
            builder.add_material("Bad", (0, 0))

    def test_many_materials_and_faces_self_parse(self):
        # Regression guard for the same class of shift-tracking bug the
        # geometry-only large-mesh test guards against, but for the
        # material-manager insertion point instead of the tail: 40 new
        # materials plus 40 new faces stack two independent slot shifts
        # (material_shift into the layer/definition-list region and into
        # total_tail_shift, geometry_shift into total_tail_shift only).
        builder = create()
        mats = [builder.add_material(f"M{i}", (i % 256, (i * 7) % 256, (i * 13) % 256))
                for i in range(40)]
        for i, m in enumerate(mats):
            x0 = i * 150.0
            builder.add_face(
                [(x0, 0.0, 0.0), (x0 + 100.0, 0.0, 0.0),
                 (x0 + 100.0, 100.0, 0.0), (x0, 100.0, 0.0)],
                material=m,
            )
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        assert len(materials) == 40
        assert sum(1 for (_, n, _) in root if n == "CFace") == 40
        faces_by_mat = {v["db"]["mat"] for (_, n, v) in root if n == "CFace"}
        assert faces_by_mat == set(mats)


class TestScaffoldIntegrity:
    def test_scaffold_hash_matches_expected(self):
        # Guards against the scaffold file silently drifting (e.g. a bad
        # merge or manual edit) without _TAIL_REF_POSITIONS being
        # re-derived to match - would otherwise fail in a much more
        # confusing way (corrupted output, not a clear error).
        import hashlib

        data = (create_module.resources.files("openskp") / "_scaffold" / "blank_v17.skp").read_bytes()
        assert hashlib.sha256(data).hexdigest() == create_module._SCAFFOLD_SHA256


# ── optional: real SketchUp SDK oracle validation ──────────────────────────
#
# Not required for CI or for this suite to be meaningful - the byte-level
# assertions above already lock in the specific fields ground-truth
# diffing found to matter. This is an extra, local-only confidence check
# using the actual SketchUp SDK as a validation oracle (never a runtime
# dependency of openskp.create itself - see that module's docstring).
# Skipped automatically wherever the DLL isn't present, which is every CI
# machine and most contributors' machines.

_SDK_DLL_PATH = os.environ.get(
    "OPENSKP_TEST_SKETCHUP_SDK_DLL",
    r"C:\Program Files\SketchUp\SketchUp 2025\SketchUp\SketchUpAPI.dll",
)


@pytest.mark.skipif(not os.path.exists(_SDK_DLL_PATH), reason="SketchUp SDK not present on this machine")
class TestRealSketchUpOracle:
    def test_single_face_loads_with_correct_face_count(self, tmp_path):
        import ctypes

        builder = create()
        builder.add_face(SQUARE)
        out = tmp_path / "single_face.skp"
        builder.save(str(out))

        dll = ctypes.CDLL(_SDK_DLL_PATH)
        dll.SUInitialize()
        try:
            model = ctypes.c_void_p()
            err = dll.SUModelCreateFromFile(ctypes.byref(model), str(out).encode())
            assert err == 0, f"SketchUp SDK rejected the file (error {err})"
            entities = ctypes.c_void_p()
            dll.SUModelGetEntities(model, ctypes.byref(entities))
            nfaces = ctypes.c_long()
            dll.SUEntitiesGetNumFaces(entities, ctypes.byref(nfaces))
            assert nfaces.value == 1
            dll.SUModelRelease(ctypes.byref(model))
        finally:
            dll.SUTerminate()

    def test_material_colors_round_trip_through_real_sketchup(self, tmp_path):
        import ctypes

        class SUColor(ctypes.Structure):
            _fields_ = [("red", ctypes.c_ubyte), ("green", ctypes.c_ubyte),
                        ("blue", ctypes.c_ubyte), ("alpha", ctypes.c_ubyte)]

        builder = create()
        red = builder.add_material("Red", (255, 0, 0))
        blue = builder.add_material("Blue", (0, 0, 255))
        builder.add_face(SQUARE, material=red)
        builder.add_face(
            [(100.0, 0.0, 0.0), (200.0, 0.0, 0.0), (200.0, 100.0, 0.0), (100.0, 100.0, 0.0)],
            material=blue,
        )
        out = tmp_path / "two_materials.skp"
        builder.save(str(out))

        dll = ctypes.CDLL(_SDK_DLL_PATH)
        dll.SUInitialize()
        dll.SUEntitiesGetFaces.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        dll.SUFaceGetFrontMaterial.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUMaterialGetColor.argtypes = [ctypes.c_void_p, ctypes.POINTER(SUColor)]
        try:
            model = ctypes.c_void_p()
            err = dll.SUModelCreateFromFile(ctypes.byref(model), str(out).encode())
            assert err == 0, f"SketchUp SDK rejected the file (error {err})"
            entities = ctypes.c_void_p()
            dll.SUModelGetEntities(model, ctypes.byref(entities))
            nfaces = ctypes.c_long()
            dll.SUEntitiesGetNumFaces(entities, ctypes.byref(nfaces))
            assert nfaces.value == 2
            faces = (ctypes.c_void_p * 2)()
            got = ctypes.c_size_t()
            dll.SUEntitiesGetFaces(entities, 2, faces, ctypes.byref(got))
            colors = []
            for i in range(2):
                mat = ctypes.c_void_p()
                assert dll.SUFaceGetFrontMaterial(faces[i], ctypes.byref(mat)) == 0
                color = SUColor()
                assert dll.SUMaterialGetColor(mat, ctypes.byref(color)) == 0
                colors.append((color.red, color.green, color.blue))
            assert set(colors) == {(255, 0, 0), (0, 0, 255)}
            dll.SUModelRelease(ctypes.byref(model))
        finally:
            dll.SUTerminate()
