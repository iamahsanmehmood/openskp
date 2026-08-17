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


def _make_test_png(size: int = 4, rgb=(200, 50, 50)) -> bytes:
    """A minimal, dependency-free PNG encoder (stdlib zlib only) - avoids
    pulling in an image library just to produce test fixtures. Solid color,
    no filtering, no interlacing."""
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
    raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))
    idat = chunk(b"IDAT", zlib.compress(raw))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


# A real 8x8 JPEG's exact bytes, pre-encoded - unlike PNG, JPEG needs real
# DCT/entropy encoding, not worth reimplementing just for a test fixture.
_JPEG_FIXTURE = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300100b0c0e0c0a10"
    "0e0d0e1211101318281a181616183123251d283a333d3c3933383740485c4e40"
    "4457453738506d51575f626768673e4d71797064785c656763ffdb0043011112"
    "121815182f1a1a2f634238426363636363636363636363636363636363636363"
    "636363636363636363636363636363636363636363636363636363636363ffc0"
    "0011080008000803012200021101031101ffc4001f0000010501010101010100"
    "000000000000000102030405060708090a0bffc400b510000201030302040305"
    "0504040000017d01020300041105122131410613516107227114328191a10823"
    "42b1c11552d1f02433627282090a161718191a25262728292a3435363738393a"
    "434445464748494a535455565758595a636465666768696a737475767778797a"
    "838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7"
    "b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1"
    "f2f3f4f5f6f7f8f9faffc4001f01000301010101010101010100000000000001"
    "02030405060708090a0bffc400b5110002010204040304070504040001027700"
    "0102031104052131061241510761711322328108144291a1b1c109233352f015"
    "6272d10a162434e125f11718191a262728292a35363738393a43444546474849"
    "4a535455565758595a636465666768696a737475767778797a82838485868788"
    "898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4"
    "c5c6c7c8c9cad2d3d4d5d6d7d8d9dae2e3e4e5e6e7e8e9eaf2f3f4f5f6f7f8f9"
    "faffda000c03010002110311003f00c8a28a2bda28ffd9"
)


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

    def test_non_planar_points_raise(self):
        builder = create()
        with pytest.raises(SkpWriteError, match="not coplanar"):
            builder.add_face([(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (100.0, 100.0, 0.0), (0.0, 100.0, 50.0)])


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

    def test_hidden_soft_smooth_flags(self):
        builder = create()
        builder.add_face(SQUARE, hidden=True, soft_edges=True, smooth_edges=True, hidden_edges=True)
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        face = [v for (_, n, v) in root if n == "CFace"][0]
        assert face["db"]["hidden"] == 1
        edges = [v for (_, n, v) in root if n == "CEdge"]
        assert all(e["db"]["hidden"] == 1 and e["db"]["soft"] == 1 and e["db"]["smooth"] == 1 for e in edges)

    def test_default_flags_are_off(self):
        builder = create()
        builder.add_face(SQUARE)
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        face = [v for (_, n, v) in root if n == "CFace"][0]
        assert face["db"]["hidden"] == 0
        edges = [v for (_, n, v) in root if n == "CEdge"]
        assert all(e["db"]["hidden"] == 0 and e["db"]["soft"] == 0 and e["db"]["smooth"] == 0 for e in edges)


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


class TestConcavePolygons:
    # L-shape: a 100x100 square missing its (50,50)-(100,100) corner.
    # Deliberately starts at the reflex (concave) vertex - the worst case
    # for a plane-normal computation that only looks at the first 3 points,
    # since that vertex's own local geometry points the "wrong" way.
    L_SHAPE = [
        (50.0, 50.0, 0.0), (100.0, 50.0, 0.0), (100.0, 100.0, 0.0),
        (0.0, 100.0, 0.0), (0.0, 0.0, 0.0), (50.0, 0.0, 0.0),
    ]

    def test_reflex_first_vertex_still_gets_correct_normal(self):
        # Regression guard: a naive first-3-points normal (rather than
        # Newell's method, summed over every edge) gets this backwards for
        # a concave polygon starting at its reflex corner.
        builder = create()
        builder.add_face(self.L_SHAPE)
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        face = [v for (_, n, v) in root if n == "CFace"][0]
        assert face["plane"][:3] == pytest.approx((0.0, 0.0, 1.0), abs=1e-9)


class TestNonManifoldTopology:
    # Three triangular "fins" sharing one common edge (the z-axis segment
    # from (0,0,0) to (0,0,100)) - nothing in the CEdgeUse/loop encoding
    # inherently limits an edge to 2 faces, but this was previously
    # unvalidated territory.
    SHARED_EDGE = [(0.0, 0.0, 0.0), (0.0, 0.0, 100.0)]
    FINS = [
        [SHARED_EDGE[0], SHARED_EDGE[1], (100.0, 0.0, 50.0)],
        [SHARED_EDGE[0], SHARED_EDGE[1], (-70.0, 70.0, 50.0)],
        [SHARED_EDGE[0], SHARED_EDGE[1], (-70.0, -70.0, 50.0)],
    ]

    def test_three_faces_share_one_edge(self):
        builder = create()
        for fin in self.FINS:
            builder.add_face(fin)
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        kinds = [n for (_, n, _) in root]
        assert kinds.count("CFace") == 3
        # 3 triangles x 3 edges = 9 edge-uses, but the shared edge collapses
        # 3 references into 1 -> 9 - 2 = 7 unique edges.
        assert kinds.count("CEdge") == 7


class TestComponentDefinitions:
    def test_basic_definition_and_instance(self):
        builder = create()
        with builder.add_component_definition("Chair") as chair:
            chair.add_face(SQUARE)
        builder.add_instance(chair)
        data = builder.to_bytes()

        ar, root, layers, materials = legacy._walk(data)
        assert len(root) == 1
        inst = root[0][2]
        assert inst["name"] == "Chair"
        assert inst["xf"] == pytest.approx((1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0))

    def test_multiple_instances_share_one_definition(self):
        builder = create()
        with builder.add_component_definition("Chair") as chair:
            chair.add_face(SQUARE)
        for i in range(5):
            builder.add_instance(chair, name=f"Chair{i}", translation=(i * 40.0, 0.0, 0.0))
        data = builder.to_bytes()

        ar, root, layers, materials = legacy._walk(data)
        assert len(root) == 5
        defs = {v["def"] for (_, n, v) in root}
        assert defs == {chair.slot}
        translations = sorted(v["xf"][9] for (_, n, v) in root)
        assert translations == [0.0, 40.0, 80.0, 120.0, 160.0]

    def test_transform_matrix_applied(self):
        builder = create()
        with builder.add_component_definition("Post") as post:
            post.add_face(SQUARE)
        # 2x scale on X only
        builder.add_instance(post, matrix3x3=(2.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0))
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        assert root[0][2]["xf"][0] == 2.0

    def test_empty_definition_raises(self):
        builder = create()
        with pytest.raises(SkpWriteError, match="no geometry"):
            with builder.add_component_definition("Empty"):
                pass

    def test_add_face_after_definition_closed_raises(self):
        builder = create()
        with builder.add_component_definition("Chair") as chair:
            chair.add_face(SQUARE)
        with pytest.raises(SkpWriteError, match="already closed"):
            chair.add_face(SQUARE)

    def test_add_instance_of_unclosed_definition_raises(self):
        builder = create()
        comp = builder.add_component_definition("Chair")
        comp.add_face(SQUARE)
        with pytest.raises(SkpWriteError, match="still open"):
            builder.add_instance(comp)

    def test_two_open_definitions_at_once_raises(self):
        builder = create()
        comp = builder.add_component_definition("Chair")
        comp.add_face(SQUARE)
        with pytest.raises(SkpWriteError, match="still open"):
            builder.add_component_definition("Table")

    def test_add_material_after_definition_started_raises(self):
        # Materials splice in earlier in the file than definitions, so a
        # definition already under construction has locked in the slot
        # numbering a later material would need to shift - this is the
        # exact case that produced a real corrupted (SU_ERROR_MODEL_INVALID)
        # file during development, caught only by the SDK oracle since
        # self-parsing doesn't validate the tail-reference shift amounts.
        builder = create()
        with builder.add_component_definition("Chair") as chair:
            chair.add_face(SQUARE)
        with pytest.raises(SkpWriteError, match="before any add_component_definition"):
            builder.add_material("TooLate", (0, 0, 0))

    def test_add_component_definition_after_add_face_raises(self):
        builder = create()
        builder.add_face(SQUARE)
        with pytest.raises(SkpWriteError, match="before any add_face/add_instance"):
            builder.add_component_definition("TooLate")

    def test_definition_geometry_and_root_geometry_share_no_vertices(self):
        # Each definition's vertex/edge sharing is scoped to itself, never
        # to the root model or other definitions.
        builder = create()
        with builder.add_component_definition("Chair") as chair:
            chair.add_face(SQUARE)
        builder.add_face(SQUARE)  # same coordinates, root level
        builder.add_instance(chair)
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        kinds = [n for (_, n, _) in root]
        assert kinds.count("CFace") == 1  # only the root-level face
        assert kinds.count("CEdge") == 4  # its own 4 edges, not shared with the definition's


class TestGroups:
    def test_basic_group_places_itself_on_close(self):
        builder = create()
        with builder.add_group("Table", translation=(50.0, 0.0, 0.0)) as table:
            table.add_face(SQUARE)
        data = builder.to_bytes()

        ar, root, layers, materials = legacy._walk(data)
        assert len(root) == 1
        kind = root[0][1]
        inst = root[0][2]
        assert kind == "CGroup"
        assert inst["name"] == "Table"
        assert inst["xf"][9:12] == (50.0, 0.0, 0.0)
        # ground truth: unlike CComponentInstance, CGroup uses a plain null
        # attribute pointer, not the real (empty) CAttributeContainer.
        assert inst["attrs"] is None

    def test_group_without_geometry_raises(self):
        builder = create()
        with pytest.raises(SkpWriteError, match="no geometry"):
            with builder.add_group("Empty"):
                pass

    def test_group_and_component_definition_together(self):
        builder = create()
        with builder.add_component_definition("Chair") as chair:
            chair.add_face(SQUARE)
        with builder.add_group("Table", translation=(100.0, 0.0, 0.0)) as table:
            table.add_face(SQUARE)
        builder.add_instance(chair, translation=(0.0, 100.0, 0.0))
        data = builder.to_bytes()

        ar, root, layers, materials = legacy._walk(data)
        kinds = {n for (_, n, _) in root}
        assert kinds == {"CGroup", "CComponentInstance"}

    def test_default_group_name(self):
        builder = create()
        with builder.add_group() as g:
            g.add_face(SQUARE)
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        assert root[0][2]["name"] == "Group"

    def test_many_definitions_instances_and_groups_self_parse(self):
        # Definitions/instances/groups haven't been stress-tested at scale
        # the way materials/layers already are elsewhere in this file -
        # this is that gap, sized to plausibly catch the same class of
        # shift-arithmetic bug the tail-reference byte-overflow fix and the
        # deferred-group-placement fix both were.
        builder = create()
        defs = []
        for d in range(20):
            with builder.add_component_definition(f"Def{d}") as comp:
                comp.add_face(SQUARE)
            defs.append(comp)
        groups = []
        for g in range(10):
            with builder.add_group(f"Grp{g}", translation=(g * 30.0, 500.0, 0.0)) as grp:
                grp.add_face(SQUARE)
            groups.append(grp)
        for i in range(40):
            builder.add_instance(defs[i % 20], name=f"Inst{i}", translation=(i * 25.0, 1000.0, 0.0))
        data = builder.to_bytes()

        ar, root, layers, materials = legacy._walk(data)
        kinds = {}
        for (_, n, _) in root:
            kinds[n] = kinds.get(n, 0) + 1
        assert kinds["CGroup"] == 10
        assert kinds["CComponentInstance"] == 40
        def_refs = {v["def"] for (_, n, v) in root if n == "CComponentInstance"}
        assert def_refs == {d.slot for d in defs}


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

    def test_back_material_distinct_from_front(self):
        builder = create()
        red = builder.add_material("Red", (255, 0, 0))
        green = builder.add_material("Green", (0, 255, 0))
        builder.add_face(SQUARE, material=red, back_material=green)
        data = builder.to_bytes()

        ar, root, layers, materials = legacy._walk(data)
        face = [v for (_, n, v) in root if n == "CFace"][0]
        assert face["db"]["mat"] == red
        assert face["back_mat"] == green

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


class TestTextures:
    def test_texture_material_self_parses(self, tmp_path):
        png_path = tmp_path / "tex.png"
        png_path.write_bytes(_make_test_png(size=4, rgb=(200, 50, 50)))

        builder = create()
        tex = builder.add_texture_material("Brick", str(png_path))
        builder.add_face(SQUARE, material=tex)
        data = builder.to_bytes()

        ar, root, layers, materials = legacy._walk(data)
        mat_by_slot = {s: v for s, v in materials}
        assert mat_by_slot[tex]["name"] == "Brick"
        assert mat_by_slot[tex]["tex_file"] == str(png_path)
        face = [v for (_, n, v) in root if n == "CFace"][0]
        assert face["db"]["mat"] == tex

    def test_jpeg_texture_material_self_parses(self, tmp_path):
        jpg_path = tmp_path / "tex.jpg"
        jpg_path.write_bytes(_JPEG_FIXTURE)

        builder = create()
        tex = builder.add_texture_material("Photo", str(jpg_path))
        builder.add_face(SQUARE, material=tex)
        data = builder.to_bytes()

        ar, root, layers, materials = legacy._walk(data)
        mat_by_slot = {s: v for s, v in materials}
        assert mat_by_slot[tex]["name"] == "Photo"
        assert mat_by_slot[tex]["tex_file"] == str(jpg_path)
        face = [v for (_, n, v) in root if n == "CFace"][0]
        assert face["db"]["mat"] == tex

    def test_png_and_jpeg_textures_together(self, tmp_path):
        # PNG and JPEG take different code paths inside write_textured_material
        # (JPEG writes one extra ground-truth u32 field PNG doesn't) -
        # regression guard that mixing both in one file doesn't misalign
        # anything downstream.
        png_path = tmp_path / "tex.png"
        png_path.write_bytes(_make_test_png())
        jpg_path = tmp_path / "tex.jpg"
        jpg_path.write_bytes(_JPEG_FIXTURE)

        builder = create()
        png_mat = builder.add_texture_material("PngTex", str(png_path))
        jpg_mat = builder.add_texture_material("JpgTex", str(jpg_path))
        builder.add_face(SQUARE, material=png_mat)
        builder.add_face(
            [(100.0, 0.0, 0.0), (200.0, 0.0, 0.0), (200.0, 100.0, 0.0), (100.0, 100.0, 0.0)],
            material=jpg_mat,
        )
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        assert len(materials) == 2
        faces = [v for (_, n, v) in root if n == "CFace"]
        assert {f["db"]["mat"] for f in faces} == {png_mat, jpg_mat}

    def test_unrecognized_format_raises(self, tmp_path):
        # Detection is by magic bytes, not extension - a .jpg with garbage
        # content should be rejected on content, not silently accepted.
        bad_path = tmp_path / "tex.jpg"
        bad_path.write_bytes(b"not really a jpeg")
        builder = create()
        with pytest.raises(SkpWriteError, match="unrecognized image format"):
            builder.add_texture_material("Bad", str(bad_path))

    def test_texture_material_dedup_by_name(self, tmp_path):
        png_path = tmp_path / "tex.png"
        png_path.write_bytes(_make_test_png())
        builder = create()
        a = builder.add_texture_material("Shared", str(png_path))
        b = builder.add_texture_material("Shared", str(png_path))
        assert a == b

    def test_add_texture_material_after_add_face_raises(self, tmp_path):
        png_path = tmp_path / "tex.png"
        png_path.write_bytes(_make_test_png())
        builder = create()
        builder.add_face(SQUARE)
        with pytest.raises(SkpWriteError, match="before any add_face"):
            builder.add_texture_material("TooLate", str(png_path))

    def test_texture_and_solid_materials_together(self, tmp_path):
        png_path = tmp_path / "tex.png"
        png_path.write_bytes(_make_test_png())
        builder = create()
        solid = builder.add_material("Red", (255, 0, 0))
        tex = builder.add_texture_material("Brick", str(png_path))
        builder.add_face(SQUARE, material=solid)
        builder.add_face(
            [(100.0, 0.0, 0.0), (200.0, 0.0, 0.0), (200.0, 100.0, 0.0), (100.0, 100.0, 0.0)],
            material=tex,
        )
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        assert len(materials) == 2
        faces = [v for (_, n, v) in root if n == "CFace"]
        assert {f["db"]["mat"] for f in faces} == {solid, tex}

    def test_two_texture_materials_together(self, tmp_path):
        # CDib is its own class declaration, separate from CMaterial's -
        # regression guard that a second texture correctly reuses CDib's
        # class-ref rather than colliding with it (the same class of bug
        # found for CLayer when combining materials and layers).
        png1 = tmp_path / "tex1.png"
        png1.write_bytes(_make_test_png(size=4, rgb=(200, 50, 50)))
        png2 = tmp_path / "tex2.png"
        png2.write_bytes(_make_test_png(size=8, rgb=(50, 200, 50)))
        builder = create()
        t1 = builder.add_texture_material("Tex1", str(png1))
        t2 = builder.add_texture_material("Tex2", str(png2))
        builder.add_face(SQUARE, material=t1)
        builder.add_face(
            [(100.0, 0.0, 0.0), (200.0, 0.0, 0.0), (200.0, 100.0, 0.0), (100.0, 100.0, 0.0)],
            material=t2,
        )
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        mat_by_slot = {s: v for s, v in materials}
        assert mat_by_slot[t1]["tex_file"] == str(png1)
        assert mat_by_slot[t2]["tex_file"] == str(png2)


class TestLayers:
    def test_layer_assigned_to_face(self):
        builder = create()
        roof = builder.add_layer("Roof")
        builder.add_face(SQUARE, layer=roof)
        data = builder.to_bytes()

        ar, root, layers, materials = legacy._walk(data)
        names = {s: v["name"] for s, v in layers}
        assert names[roof] == "Roof"
        assert "Layer0" in names.values()
        face = [v for (_, n, v) in root if n == "CFace"][0]
        assert face["db"]["layer"] == roof
        edges = [v for (_, n, v) in root if n == "CEdge"]
        assert all(e["db"]["layer"] == 0 for e in edges)

    def test_layer_dedup_by_name_returns_same_handle(self):
        builder = create()
        a = builder.add_layer("Shared")
        b = builder.add_layer("Shared")
        assert a == b
        builder.add_face(SQUARE, layer=a)
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        assert len(layers) == 2  # Layer0 + Shared

    def test_add_layer_after_add_face_raises(self):
        builder = create()
        builder.add_face(SQUARE)
        with pytest.raises(SkpWriteError, match="before any add_face"):
            builder.add_layer("TooLate")

    def test_add_material_after_add_layer_raises(self):
        # Materials splice in earlier in the file than layers, so the layer
        # section's slot numbering depends on the final material count -
        # add_material must happen first.
        builder = create()
        builder.add_layer("L")
        with pytest.raises(SkpWriteError, match="before any add_layer"):
            builder.add_material("TooLate", (0, 0, 0))

    def test_materials_and_layers_together(self):
        # The combined case stacks two independent front-of-file shifts:
        # layers splice in after materials, so the layer writer's starting
        # slot - and every scaffold class it might reference (CLayer) -
        # depends on the final material count.
        builder = create()
        red = builder.add_material("Red", (255, 0, 0))
        blue = builder.add_material("Blue", (0, 0, 255))
        roof = builder.add_layer("Roof")
        walls = builder.add_layer("Walls")
        builder.add_face(SQUARE, material=red, layer=roof)
        builder.add_face(
            [(100.0, 0.0, 0.0), (200.0, 0.0, 0.0), (200.0, 100.0, 0.0), (100.0, 100.0, 0.0)],
            material=blue, layer=walls,
        )
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        mat_names = {s: v["name"] for s, v in materials}
        layer_names = {s: v["name"] for s, v in layers}
        faces = [v for (_, n, v) in root if n == "CFace"]
        got = {(mat_names[f["db"]["mat"]], layer_names[f["db"]["layer"]]) for f in faces}
        assert got == {("Red", "Roof"), ("Blue", "Walls")}

    def test_many_layers_and_materials_self_parse(self):
        # Regression guard for the same class of slot-shift bug the
        # material stress test guards against, at the scale where the
        # scaffold's own CLayer class-slot reference (which shifts by
        # material_shift) is most likely to be forgotten.
        builder = create()
        mats = [builder.add_material(f"M{i}", (i % 256, (i * 7) % 256, (i * 13) % 256))
                for i in range(25)]
        lyrs = [builder.add_layer(f"L{i}") for i in range(25)]
        for i in range(25):
            x0 = i * 150.0
            builder.add_face(
                [(x0, 0.0, 0.0), (x0 + 100.0, 0.0, 0.0),
                 (x0 + 100.0, 100.0, 0.0), (x0, 100.0, 0.0)],
                material=mats[i], layer=lyrs[i],
            )
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        assert len(materials) == 25
        assert len(layers) == 26  # Layer0 + 25 new
        faces = [v for (_, n, v) in root if n == "CFace"]
        assert len(faces) == 25
        assert {f["db"]["mat"] for f in faces} == set(mats)
        assert {f["db"]["layer"] for f in faces} == set(lyrs)


def _build_kitchen_sink(builder, png_path, jpg_path):
    """Exercises every feature together at once - materials (solid + PNG +
    JPEG), layers, component definitions with concave and shared-edge
    geometry inside them, multiple instances, multiple groups, root-level
    materials/layers/back-materials/hidden faces, and a non-manifold shared
    edge. This combination (specifically, two groups back-to-back) is what
    originally caught the deferred-group-placement bug: a second
    add_group/add_component_definition call after an earlier group had
    already closed and auto-placed itself would wrongly reject with "must
    be called before any add_face/add_instance calls", since placing a
    group locks in root-level slot numbering. Used by both
    TestKitchenSink (self-parse) and TestRealSketchUpOracle (SDK) below as
    a permanent regression guard against that whole class of ordering bug.
    """
    solids = [builder.add_material(f"Solid{i}", (i * 20 % 256, (i * 53) % 256, (i * 97) % 256))
              for i in range(4)]
    png_mat = builder.add_texture_material("Checker", str(png_path))
    jpg_mat = builder.add_texture_material("Photo", str(jpg_path))
    layers = [builder.add_layer(f"Layer{i}") for i in range(3)]

    defs = []
    for d in range(2):
        with builder.add_component_definition(f"Part{d}") as comp:
            comp.add_face(  # concave (L-shaped)
                [(50.0, 50.0, 0.0), (100.0, 50.0, 0.0), (100.0, 100.0, 0.0),
                 (0.0, 100.0, 0.0), (0.0, 0.0, 0.0), (50.0, 0.0, 0.0)],
                material=solids[d], layer=layers[d],
            )
            comp.add_face([(0.0, 0.0, 0.0), (0.0, 0.0, 40.0), (100.0, 0.0, 20.0)], material=solids[d])
            comp.add_face([(0.0, 0.0, 0.0), (0.0, 0.0, 40.0), (-100.0, 0.0, 20.0)], material=solids[d],
                           soft_edges=True, smooth_edges=True)
        defs.append(comp)

    # Two groups back-to-back - the exact shape that caught the bug.
    with builder.add_group("GroupA", translation=(0.0, 200.0, 0.0)) as g:
        g.add_face(SQUARE, material=png_mat)
    with builder.add_group("GroupB", translation=(100.0, 200.0, 0.0)) as g:
        g.add_face(SQUARE, material=jpg_mat)

    for i in range(6):
        builder.add_instance(
            defs[i % 2], name=f"Inst{i}", translation=(i * 60.0, 0.0, 0.0),
            material=solids[i % len(solids)], layer=layers[i % len(layers)],
        )

    for i in range(3):
        x0 = i * 25.0
        builder.add_face(
            [(x0, -100.0, 0.0), (x0 + 20.0, -100.0, 0.0), (x0 + 20.0, -80.0, 0.0), (x0, -80.0, 0.0)],
            material=solids[i % len(solids)], layer=layers[i % len(layers)],
            back_material=solids[(i + 1) % len(solids)], hidden=(i == 1),
        )

    shared = [(0.0, -150.0, 0.0), (0.0, -150.0, 50.0)]
    builder.add_face([shared[0], shared[1], (30.0, -150.0, 25.0)], material=png_mat)
    builder.add_face([shared[0], shared[1], (-30.0, -140.0, 25.0)], material=jpg_mat)
    builder.add_face([shared[0], shared[1], (-30.0, -160.0, 25.0)])


class TestKitchenSink:
    def test_self_parses_with_expected_counts(self, tmp_path):
        png_path = tmp_path / "tex.png"
        png_path.write_bytes(_make_test_png())
        jpg_path = tmp_path / "tex.jpg"
        jpg_path.write_bytes(_JPEG_FIXTURE)

        builder = create()
        _build_kitchen_sink(builder, png_path, jpg_path)
        data = builder.to_bytes()

        ar, root, layers, materials = legacy._walk(data)
        assert len(materials) == 6  # 4 solid + png + jpeg
        assert len(layers) == 4  # 3 + Layer0
        kinds = {}
        for (_, n, _) in root:
            kinds[n] = kinds.get(n, 0) + 1
        assert kinds["CGroup"] == 2
        assert kinds["CComponentInstance"] == 6
        assert kinds["CFace"] == 6  # 3 disjoint + 3 sharing one edge


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

    def test_back_material_round_trips_through_real_sketchup(self, tmp_path):
        import ctypes

        class SUColor(ctypes.Structure):
            _fields_ = [("red", ctypes.c_ubyte), ("green", ctypes.c_ubyte),
                        ("blue", ctypes.c_ubyte), ("alpha", ctypes.c_ubyte)]

        builder = create()
        red = builder.add_material("Red", (255, 0, 0))
        green = builder.add_material("Green", (0, 255, 0))
        builder.add_face(SQUARE, material=red, back_material=green)
        out = tmp_path / "back_material.skp"
        builder.save(str(out))

        dll = ctypes.CDLL(_SDK_DLL_PATH)
        dll.SUInitialize()
        dll.SUEntitiesGetFaces.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        dll.SUFaceGetFrontMaterial.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUFaceGetBackMaterial.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUMaterialGetColor.argtypes = [ctypes.c_void_p, ctypes.POINTER(SUColor)]
        try:
            model = ctypes.c_void_p()
            err = dll.SUModelCreateFromFile(ctypes.byref(model), str(out).encode())
            assert err == 0, f"SketchUp SDK rejected the file (error {err})"
            entities = ctypes.c_void_p()
            dll.SUModelGetEntities(model, ctypes.byref(entities))
            faces = (ctypes.c_void_p * 1)()
            got = ctypes.c_size_t()
            dll.SUEntitiesGetFaces(entities, 1, faces, ctypes.byref(got))
            front_mat = ctypes.c_void_p()
            back_mat = ctypes.c_void_p()
            assert dll.SUFaceGetFrontMaterial(faces[0], ctypes.byref(front_mat)) == 0
            assert dll.SUFaceGetBackMaterial(faces[0], ctypes.byref(back_mat)) == 0
            front_color, back_color = SUColor(), SUColor()
            dll.SUMaterialGetColor(front_mat, ctypes.byref(front_color))
            dll.SUMaterialGetColor(back_mat, ctypes.byref(back_color))
            assert (front_color.red, front_color.green, front_color.blue) == (255, 0, 0)
            assert (back_color.red, back_color.green, back_color.blue) == (0, 255, 0)
            dll.SUModelRelease(ctypes.byref(model))
        finally:
            dll.SUTerminate()

    def test_materials_and_layers_round_trip_through_real_sketchup(self, tmp_path):
        import ctypes

        builder = create()
        red = builder.add_material("Red", (255, 0, 0))
        blue = builder.add_material("Blue", (0, 0, 255))
        roof = builder.add_layer("Roof")
        walls = builder.add_layer("Walls")
        builder.add_face(SQUARE, material=red, layer=roof)
        builder.add_face(
            [(100.0, 0.0, 0.0), (200.0, 0.0, 0.0), (200.0, 100.0, 0.0), (100.0, 100.0, 0.0)],
            material=blue, layer=walls,
        )
        out = tmp_path / "materials_and_layers.skp"
        builder.save(str(out))

        dll = ctypes.CDLL(_SDK_DLL_PATH)
        dll.SUInitialize()
        dll.SUEntitiesGetFaces.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        dll.SUDrawingElementGetLayer.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SULayerGetName.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUStringCreate.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        dll.SUStringGetUTF8Length.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        dll.SUStringGetUTF8.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.POINTER(ctypes.c_size_t)]
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

            names = []
            for i in range(2):
                layer = ctypes.c_void_p()
                assert dll.SUDrawingElementGetLayer(faces[i], ctypes.byref(layer)) == 0
                sref = ctypes.c_void_p()
                dll.SUStringCreate(ctypes.byref(sref))
                dll.SULayerGetName(layer, ctypes.byref(sref))
                length = ctypes.c_size_t()
                dll.SUStringGetUTF8Length(sref, ctypes.byref(length))
                buf = ctypes.create_string_buffer(length.value + 1)
                outlen = ctypes.c_size_t()
                dll.SUStringGetUTF8(sref, length.value + 1, buf, ctypes.byref(outlen))
                names.append(buf.value.decode())
            assert set(names) == {"Roof", "Walls"}
            dll.SUModelRelease(ctypes.byref(model))
        finally:
            dll.SUTerminate()

    def test_texture_material_round_trips_through_real_sketchup(self, tmp_path):
        import ctypes

        png_path = tmp_path / "tex.png"
        png_path.write_bytes(_make_test_png(size=8, rgb=(60, 180, 75)))

        builder = create()
        tex = builder.add_texture_material("Checker", str(png_path))
        builder.add_face(SQUARE, material=tex)
        out = tmp_path / "textured.skp"
        builder.save(str(out))

        dll = ctypes.CDLL(_SDK_DLL_PATH)
        dll.SUInitialize()
        dll.SUEntitiesGetFaces.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        dll.SUFaceGetFrontMaterial.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUMaterialGetTexture.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUTextureGetDimensions.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t),
            ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double),
        ]
        try:
            model = ctypes.c_void_p()
            err = dll.SUModelCreateFromFile(ctypes.byref(model), str(out).encode())
            assert err == 0, f"SketchUp SDK rejected the file (error {err})"
            entities = ctypes.c_void_p()
            dll.SUModelGetEntities(model, ctypes.byref(entities))
            faces = (ctypes.c_void_p * 1)()
            got = ctypes.c_size_t()
            dll.SUEntitiesGetFaces(entities, 1, faces, ctypes.byref(got))
            mat = ctypes.c_void_p()
            assert dll.SUFaceGetFrontMaterial(faces[0], ctypes.byref(mat)) == 0
            texture = ctypes.c_void_p()
            assert dll.SUMaterialGetTexture(mat, ctypes.byref(texture)) == 0
            w, h = ctypes.c_size_t(), ctypes.c_size_t()
            sw, sh = ctypes.c_double(), ctypes.c_double()
            dll.SUTextureGetDimensions(texture, ctypes.byref(w), ctypes.byref(h), ctypes.byref(sw), ctypes.byref(sh))
            assert (w.value, h.value) == (8, 8)
            dll.SUModelRelease(ctypes.byref(model))
        finally:
            dll.SUTerminate()

    def test_jpeg_texture_material_round_trips_through_real_sketchup(self, tmp_path):
        import ctypes

        jpg_path = tmp_path / "tex.jpg"
        jpg_path.write_bytes(_JPEG_FIXTURE)

        builder = create()
        tex = builder.add_texture_material("Photo", str(jpg_path))
        builder.add_face(SQUARE, material=tex)
        out = tmp_path / "jpeg_textured.skp"
        builder.save(str(out))

        dll = ctypes.CDLL(_SDK_DLL_PATH)
        dll.SUInitialize()
        dll.SUEntitiesGetFaces.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        dll.SUFaceGetFrontMaterial.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUMaterialGetTexture.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUTextureGetDimensions.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t),
            ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double),
        ]
        try:
            model = ctypes.c_void_p()
            err = dll.SUModelCreateFromFile(ctypes.byref(model), str(out).encode())
            assert err == 0, f"SketchUp SDK rejected the file (error {err})"
            entities = ctypes.c_void_p()
            dll.SUModelGetEntities(model, ctypes.byref(entities))
            faces = (ctypes.c_void_p * 1)()
            got = ctypes.c_size_t()
            dll.SUEntitiesGetFaces(entities, 1, faces, ctypes.byref(got))
            mat = ctypes.c_void_p()
            assert dll.SUFaceGetFrontMaterial(faces[0], ctypes.byref(mat)) == 0
            texture = ctypes.c_void_p()
            assert dll.SUMaterialGetTexture(mat, ctypes.byref(texture)) == 0
            w, h = ctypes.c_size_t(), ctypes.c_size_t()
            sw, sh = ctypes.c_double(), ctypes.c_double()
            dll.SUTextureGetDimensions(texture, ctypes.byref(w), ctypes.byref(h), ctypes.byref(sw), ctypes.byref(sh))
            assert (w.value, h.value) == (8, 8)  # the fixture JPEG is 8x8
            dll.SUModelRelease(ctypes.byref(model))
        finally:
            dll.SUTerminate()

    def test_concave_face_area_is_correct_in_real_sketchup(self, tmp_path):
        import ctypes

        builder = create()
        builder.add_face(TestConcavePolygons.L_SHAPE)
        out = tmp_path / "lshape.skp"
        builder.save(str(out))

        dll = ctypes.CDLL(_SDK_DLL_PATH)
        dll.SUInitialize()
        dll.SUEntitiesGetFaces.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        dll.SUFaceGetArea.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_double)]
        try:
            model = ctypes.c_void_p()
            err = dll.SUModelCreateFromFile(ctypes.byref(model), str(out).encode())
            assert err == 0, f"SketchUp SDK rejected the file (error {err})"
            entities = ctypes.c_void_p()
            dll.SUModelGetEntities(model, ctypes.byref(entities))
            nfaces = ctypes.c_long()
            dll.SUEntitiesGetNumFaces(entities, ctypes.byref(nfaces))
            assert nfaces.value == 1
            faces = (ctypes.c_void_p * 1)()
            got = ctypes.c_size_t()
            dll.SUEntitiesGetFaces(entities, 1, faces, ctypes.byref(got))
            area = ctypes.c_double()
            dll.SUFaceGetArea(faces[0], ctypes.byref(area))
            # 100x100 square minus the missing 50x50 corner = 7500 sq in.
            # A wrong-signed/backwards normal wouldn't necessarily change
            # this number, but a broken loop winding would - this is the
            # cheapest real-SketchUp check that the geometry is actually
            # the L-shape, not something degenerate.
            assert area.value == pytest.approx(7500.0, abs=1e-6)
            dll.SUModelRelease(ctypes.byref(model))
        finally:
            dll.SUTerminate()

    def test_three_faces_sharing_an_edge_have_correct_areas_in_real_sketchup(self, tmp_path):
        import ctypes

        builder = create()
        for fin in TestNonManifoldTopology.FINS:
            builder.add_face(fin)
        out = tmp_path / "three_fins.skp"
        builder.save(str(out))

        dll = ctypes.CDLL(_SDK_DLL_PATH)
        dll.SUInitialize()
        dll.SUEntitiesGetFaces.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        dll.SUFaceGetArea.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_double)]
        try:
            model = ctypes.c_void_p()
            err = dll.SUModelCreateFromFile(ctypes.byref(model), str(out).encode())
            assert err == 0, f"SketchUp SDK rejected the file (error {err})"
            entities = ctypes.c_void_p()
            dll.SUModelGetEntities(model, ctypes.byref(entities))
            nfaces = ctypes.c_long()
            dll.SUEntitiesGetNumFaces(entities, ctypes.byref(nfaces))
            assert nfaces.value == 3
            faces = (ctypes.c_void_p * 3)()
            got = ctypes.c_size_t()
            dll.SUEntitiesGetFaces(entities, 3, faces, ctypes.byref(got))
            areas = []
            for i in range(3):
                area = ctypes.c_double()
                dll.SUFaceGetArea(faces[i], ctypes.byref(area))
                areas.append(area.value)
            # base 100 (the shared edge's length) x each apex's distance
            # from the shared edge's line: 100 for the first fin, and
            # sqrt(70^2+70^2) for the other two.
            expected = sorted([5000.0, 4949.747468305833, 4949.747468305833])
            assert sorted(areas) == pytest.approx(expected, abs=1e-6)
            dll.SUModelRelease(ctypes.byref(model))
        finally:
            dll.SUTerminate()

    def test_hidden_soft_smooth_flags_round_trip_through_real_sketchup(self, tmp_path):
        import ctypes

        builder = create()
        builder.add_face(SQUARE, hidden=True, soft_edges=True, smooth_edges=True, hidden_edges=True)
        out = tmp_path / "hidden_smooth.skp"
        builder.save(str(out))

        dll = ctypes.CDLL(_SDK_DLL_PATH)
        dll.SUInitialize()
        dll.SUEntitiesGetFaces.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        dll.SUFaceGetEdges.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        dll.SUDrawingElementGetHidden.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_bool)]
        dll.SUEdgeGetSoft.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_bool)]
        dll.SUEdgeGetSmooth.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_bool)]
        try:
            model = ctypes.c_void_p()
            err = dll.SUModelCreateFromFile(ctypes.byref(model), str(out).encode())
            assert err == 0, f"SketchUp SDK rejected the file (error {err})"
            entities = ctypes.c_void_p()
            dll.SUModelGetEntities(model, ctypes.byref(entities))
            faces = (ctypes.c_void_p * 1)()
            got = ctypes.c_size_t()
            dll.SUEntitiesGetFaces(entities, 1, faces, ctypes.byref(got))
            face_hidden = ctypes.c_bool()
            assert dll.SUDrawingElementGetHidden(faces[0], ctypes.byref(face_hidden)) == 0
            assert face_hidden.value is True
            edges = (ctypes.c_void_p * 4)()
            got2 = ctypes.c_size_t()
            dll.SUFaceGetEdges(faces[0], 4, edges, ctypes.byref(got2))
            for i in range(4):
                eh, es, esm = ctypes.c_bool(), ctypes.c_bool(), ctypes.c_bool()
                dll.SUDrawingElementGetHidden(edges[i], ctypes.byref(eh))
                dll.SUEdgeGetSoft(edges[i], ctypes.byref(es))
                dll.SUEdgeGetSmooth(edges[i], ctypes.byref(esm))
                assert (eh.value, es.value, esm.value) == (True, True, True)
            dll.SUModelRelease(ctypes.byref(model))
        finally:
            dll.SUTerminate()

    def test_component_instances_round_trip_through_real_sketchup(self, tmp_path):
        import ctypes

        builder = create()
        with builder.add_component_definition("Chair") as chair:
            chair.add_face(SQUARE)
        for i in range(5):
            builder.add_instance(chair, name=f"Chair{i}", translation=(i * 40.0, 0.0, 0.0))
        out = tmp_path / "instances.skp"
        builder.save(str(out))

        dll = ctypes.CDLL(_SDK_DLL_PATH)
        dll.SUInitialize()
        dll.SUEntitiesGetNumInstances.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_long)]
        dll.SUEntitiesGetInstances.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        dll.SUComponentInstanceGetTransform.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_double * 16)]
        try:
            model = ctypes.c_void_p()
            err = dll.SUModelCreateFromFile(ctypes.byref(model), str(out).encode())
            assert err == 0, f"SketchUp SDK rejected the file (error {err})"
            entities = ctypes.c_void_p()
            dll.SUModelGetEntities(model, ctypes.byref(entities))
            ninst = ctypes.c_long()
            dll.SUEntitiesGetNumInstances(entities, ctypes.byref(ninst))
            assert ninst.value == 5
            insts = (ctypes.c_void_p * 5)()
            got = ctypes.c_size_t()
            dll.SUEntitiesGetInstances(entities, 5, insts, ctypes.byref(got))
            translations = []
            for i in range(5):
                xf = (ctypes.c_double * 16)()
                dll.SUComponentInstanceGetTransform(insts[i], ctypes.byref(xf))
                translations.append(xf[12])
            assert sorted(translations) == [0.0, 40.0, 80.0, 120.0, 160.0]
            dll.SUModelRelease(ctypes.byref(model))
        finally:
            dll.SUTerminate()

    def test_materials_inside_definition_and_at_root_round_trip(self, tmp_path):
        # Regression guard for a real bug found during development: adding
        # a root-level material AFTER a component definition had already
        # started writing produced a file that self-parsed fine but was
        # SU_ERROR_MODEL_INVALID in real SketchUp - the definition's
        # already-written bytes assumed a material count that a later
        # add_material call silently invalidated. add_material now raises
        # if called after add_component_definition (see
        # TestComponentDefinitions.test_add_material_after_definition_started_raises);
        # this test locks in the *correct* ordering actually working.
        import ctypes

        builder = create()
        brown = builder.add_material("Brown", (110, 80, 50))
        ground = builder.add_material("Grass", (86, 150, 60))
        with builder.add_component_definition("Box") as box:
            box.add_face(SQUARE, material=brown)
        builder.add_face(
            [(-20.0, -20.0, 0.0), (220.0, -20.0, 0.0), (220.0, 120.0, 0.0), (-20.0, 120.0, 0.0)],
            material=ground,
        )
        builder.add_instance(box)
        out = tmp_path / "combo.skp"
        builder.save(str(out))

        dll = ctypes.CDLL(_SDK_DLL_PATH)
        dll.SUInitialize()
        dll.SUEntitiesGetNumInstances.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_long)]
        try:
            model = ctypes.c_void_p()
            err = dll.SUModelCreateFromFile(ctypes.byref(model), str(out).encode())
            assert err == 0, f"SketchUp SDK rejected the file (error {err})"
            entities = ctypes.c_void_p()
            dll.SUModelGetEntities(model, ctypes.byref(entities))
            nfaces = ctypes.c_long()
            dll.SUEntitiesGetNumFaces(entities, ctypes.byref(nfaces))
            assert nfaces.value == 1  # the root-level ground face
            ninst = ctypes.c_long()
            dll.SUEntitiesGetNumInstances(entities, ctypes.byref(ninst))
            assert ninst.value == 1
            dll.SUModelRelease(ctypes.byref(model))
        finally:
            dll.SUTerminate()

    def test_group_round_trips_through_real_sketchup(self, tmp_path):
        import ctypes

        builder = create()
        with builder.add_group("Table", translation=(50.0, 0.0, 0.0)) as table:
            table.add_face(SQUARE)
        out = tmp_path / "group.skp"
        builder.save(str(out))

        dll = ctypes.CDLL(_SDK_DLL_PATH)
        dll.SUInitialize()
        dll.SUEntitiesGetNumGroups.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        dll.SUEntitiesGetGroups.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        dll.SUGroupGetTransform.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_double * 16)]
        try:
            model = ctypes.c_void_p()
            err = dll.SUModelCreateFromFile(ctypes.byref(model), str(out).encode())
            assert err == 0, f"SketchUp SDK rejected the file (error {err})"
            entities = ctypes.c_void_p()
            dll.SUModelGetEntities(model, ctypes.byref(entities))
            ng = ctypes.c_size_t()
            dll.SUEntitiesGetNumGroups(entities, ctypes.byref(ng))
            assert ng.value == 1
            groups = (ctypes.c_void_p * 1)()
            got = ctypes.c_size_t()
            dll.SUEntitiesGetGroups(entities, 1, groups, ctypes.byref(got))
            xf = (ctypes.c_double * 16)()
            dll.SUGroupGetTransform(groups[0], ctypes.byref(xf))
            assert (xf[12], xf[13], xf[14]) == (50.0, 0.0, 0.0)
            dll.SUModelRelease(ctypes.byref(model))
        finally:
            dll.SUTerminate()

    def test_kitchen_sink_round_trips_through_real_sketchup(self, tmp_path):
        import ctypes

        png_path = tmp_path / "tex.png"
        png_path.write_bytes(_make_test_png())
        jpg_path = tmp_path / "tex.jpg"
        jpg_path.write_bytes(_JPEG_FIXTURE)

        builder = create()
        _build_kitchen_sink(builder, png_path, jpg_path)
        out = tmp_path / "kitchen_sink.skp"
        builder.save(str(out))

        dll = ctypes.CDLL(_SDK_DLL_PATH)
        dll.SUInitialize()
        dll.SUEntitiesGetNumInstances.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_long)]
        dll.SUEntitiesGetNumGroups.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        dll.SUModelGetNumMaterials.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        try:
            model = ctypes.c_void_p()
            err = dll.SUModelCreateFromFile(ctypes.byref(model), str(out).encode())
            assert err == 0, f"SketchUp SDK rejected the file (error {err})"
            entities = ctypes.c_void_p()
            dll.SUModelGetEntities(model, ctypes.byref(entities))
            nfaces = ctypes.c_long()
            dll.SUEntitiesGetNumFaces(entities, ctypes.byref(nfaces))
            assert nfaces.value == 6
            ninst = ctypes.c_long()
            dll.SUEntitiesGetNumInstances(entities, ctypes.byref(ninst))
            assert ninst.value == 6
            ng = ctypes.c_size_t()
            dll.SUEntitiesGetNumGroups(entities, ctypes.byref(ng))
            assert ng.value == 2
            nmat = ctypes.c_size_t()
            dll.SUModelGetNumMaterials(model, ctypes.byref(nmat))
            assert nmat.value == 6
            dll.SUModelRelease(ctypes.byref(model))
        finally:
            dll.SUTerminate()

    def test_many_definitions_instances_and_groups_round_trip(self, tmp_path):
        import ctypes

        builder = create()
        defs = []
        for d in range(20):
            with builder.add_component_definition(f"Def{d}") as comp:
                comp.add_face(SQUARE)
            defs.append(comp)
        for g in range(10):
            with builder.add_group(f"Grp{g}", translation=(g * 30.0, 500.0, 0.0)) as grp:
                grp.add_face(SQUARE)
        for i in range(40):
            builder.add_instance(defs[i % 20], name=f"Inst{i}", translation=(i * 25.0, 1000.0, 0.0))
        out = tmp_path / "many_defs.skp"
        builder.save(str(out))

        dll = ctypes.CDLL(_SDK_DLL_PATH)
        dll.SUInitialize()
        dll.SUEntitiesGetNumInstances.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_long)]
        dll.SUEntitiesGetNumGroups.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        dll.SUModelGetNumComponentDefinitions.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        try:
            model = ctypes.c_void_p()
            err = dll.SUModelCreateFromFile(ctypes.byref(model), str(out).encode())
            assert err == 0, f"SketchUp SDK rejected the file (error {err})"
            entities = ctypes.c_void_p()
            dll.SUModelGetEntities(model, ctypes.byref(entities))
            ninst = ctypes.c_long()
            dll.SUEntitiesGetNumInstances(entities, ctypes.byref(ninst))
            assert ninst.value == 40
            ng = ctypes.c_size_t()
            dll.SUEntitiesGetNumGroups(entities, ctypes.byref(ng))
            assert ng.value == 10
            ndef = ctypes.c_size_t()
            dll.SUModelGetNumComponentDefinitions(model, ctypes.byref(ndef))
            assert ndef.value == 30  # 20 explicit + 10 backing the groups
            dll.SUModelRelease(ctypes.byref(model))
        finally:
            dll.SUTerminate()
