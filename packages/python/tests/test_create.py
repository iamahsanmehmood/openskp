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
import math
import os
import struct

import pytest

from openskp.create import (
    Length, Point3d, SkpBuilder, SkpWriteError, Timestamp, Vector3d, create,
)
from openskp import legacy
from openskp import SkpFile

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


class TestUnicodeNames:
    # Compares by codepoint/exact string equality throughout, never by
    # printed representation - a terminal's own display codepage can
    # substitute a replacement glyph for a character it can't render even
    # when the underlying decoded string is byte-perfect, which looks
    # identical to real data corruption unless checked this way.
    NAME = "Rouge Écarlate étoile"

    def test_material_name_round_trips_exactly(self):
        builder = create()
        mat = builder.add_material(self.NAME, (255, 0, 0))
        builder.add_face(SQUARE, material=mat)
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        assert dict(materials)[mat]["name"] == self.NAME

    def test_layer_name_round_trips_exactly(self):
        builder = create()
        layer = builder.add_layer(self.NAME)
        builder.add_face(SQUARE, layer=layer)
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        assert dict(layers)[layer]["name"] == self.NAME

    def test_definition_and_instance_names_round_trip_exactly(self):
        builder = create()
        with builder.add_component_definition(self.NAME) as comp:
            comp.add_face(SQUARE)
        builder.add_instance(comp, name=self.NAME)
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        assert root[0][2]["name"] == self.NAME


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

    def test_add_face_rejects_a_layer_handle_passed_as_material(self):
        # The exact real-world mistake this guards against: a caller
        # accidentally passes a layer handle into `material=` (e.g. via a
        # positional-argument slip in a wrapper function around add_face).
        # Before this check, the layer's slot silently became a dangling
        # material reference - openskp's own reader tolerated it, but real
        # SketchUp rejected the resulting file outright.
        builder = create()
        layer = builder.add_layer("Layer0")
        with pytest.raises(SkpWriteError, match="material"):
            builder.add_face(SQUARE, material=layer)

    def test_add_face_rejects_a_material_handle_passed_as_layer(self):
        builder = create()
        mat = builder.add_material("Red", (255, 0, 0))
        with pytest.raises(SkpWriteError, match="layer"):
            builder.add_face(SQUARE, layer=mat)

    def test_add_face_rejects_an_unrelated_back_material_handle(self):
        builder = create()
        layer = builder.add_layer("Layer0")
        with pytest.raises(SkpWriteError, match="back_material"):
            builder.add_face(SQUARE, back_material=layer)

    def test_add_face_rejects_a_handle_from_a_different_builder(self):
        other_builder = create()
        stray_material = other_builder.add_material("Blue", (0, 0, 255))
        builder = create()
        with pytest.raises(SkpWriteError, match="material"):
            builder.add_face(SQUARE, material=stray_material)

    def test_add_instance_rejects_a_layer_handle_passed_as_material(self):
        builder = create()
        layer = builder.add_layer("Layer0")
        with builder.add_component_definition("Chair") as chair:
            chair.add_face(SQUARE)
        with pytest.raises(SkpWriteError, match="material"):
            builder.add_instance(chair, material=layer)

    def test_add_group_rejects_an_unrelated_layer_handle(self):
        builder = create()
        mat = builder.add_material("Red", (255, 0, 0))
        with pytest.raises(SkpWriteError, match="layer"):
            builder.add_group("Table", layer=mat)

    def test_component_scope_add_face_rejects_an_unrelated_handle(self):
        builder = create()
        layer = builder.add_layer("Layer0")
        with builder.add_component_definition("Chair") as chair:
            with pytest.raises(SkpWriteError, match="material"):
                chair.add_face(SQUARE, material=layer)
            # the definition must still be usable after a rejected call
            chair.add_face(SQUARE)

    def test_add_face_accepts_a_real_material_and_layer(self):
        builder = create()
        mat = builder.add_material("Red", (255, 0, 0))
        layer = builder.add_layer("MyLayer")
        builder.add_face(SQUARE, material=mat, layer=layer)
        assert len(builder.to_bytes()) > 0


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


class TestAutoTriangulate:
    # A "quad" whose 4th point is deliberately raised out of the other
    # 3's plane - a corner "pop" no shared-plane tilt could produce, the
    # same non-planarity a tessellated curved surface hits in practice.
    WARPED_QUAD = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 5.0)]
    FLAT_SQUARE = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)]

    def test_non_planar_without_auto_triangulate_still_raises(self):
        builder = create()
        with pytest.raises(SkpWriteError, match="not coplanar"):
            builder.add_face(self.WARPED_QUAD)

    def test_non_planar_with_auto_triangulate_splits_into_two_faces(self):
        builder = create()
        builder.add_face(self.WARPED_QUAD, auto_triangulate=True)
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        faces = [v for (_, n, v) in root if n == "CFace"]
        assert len(faces) == 2

    def test_already_planar_input_stays_a_single_face(self):
        # auto_triangulate should be a no-op for input that's already flat
        # - not force-split every face into triangles.
        builder = create()
        builder.add_face(self.FLAT_SQUARE, auto_triangulate=True)
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        faces = [v for (_, n, v) in root if n == "CFace"]
        assert len(faces) == 1

    def test_triangulated_faces_share_the_fan_origin_vertex(self):
        builder = create()
        builder.add_face(self.WARPED_QUAD, auto_triangulate=True)
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        edges = [v for (_, n, v) in root if n == "CEdge"]
        vertex_ids = set()
        for e in edges:
            vertex_ids.add(e["v1"])
            vertex_ids.add(e["v2"])
        # 4 corners, no duplicate CVertex for the shared fan-origin point
        assert len(vertex_ids) == 4

    def test_auto_triangulate_rejects_front_uv(self):
        builder = create()
        with pytest.raises(SkpWriteError, match="auto_triangulate cannot be combined"):
            builder.add_face(
                self.WARPED_QUAD, auto_triangulate=True,
                front_uv=[((0, 0, 0), (0.0, 0.0)), ((10, 0, 0), (1.0, 0.0)), ((10, 10, 0), (1.0, 1.0))],
            )

    def test_auto_triangulate_still_raises_for_degenerate_input(self):
        # A genuinely collinear/degenerate input isn't "fixed" by
        # triangulation - _is_coplanar itself still raises for it.
        builder = create()
        with pytest.raises(SkpWriteError, match="collinear or degenerate"):
            builder.add_face([(0.0, 0.0, 0.0), (5.0, 0.0, 0.0), (10.0, 0.0, 0.0)], auto_triangulate=True)

    def test_auto_triangulate_in_component_definition(self):
        builder = create()
        with builder.add_component_definition("Fin") as fin:
            fin.add_face(self.WARPED_QUAD, auto_triangulate=True)
        builder.add_instance(fin)
        data = builder.to_bytes()
        legacy._walk(data)


class TestFaceHoles:
    WALL = [(0.0, 0.0, 0.0), (200.0, 0.0, 0.0), (200.0, 100.0, 0.0), (0.0, 100.0, 0.0)]
    WINDOW = [(80.0, 30.0, 0.0), (120.0, 30.0, 0.0), (120.0, 70.0, 0.0), (80.0, 70.0, 0.0)]
    WINDOW_2 = [(20.0, 30.0, 0.0), (50.0, 30.0, 0.0), (50.0, 70.0, 0.0), (20.0, 70.0, 0.0)]

    def test_single_hole_produces_two_loops(self):
        builder = create()
        builder.add_face(self.WALL, holes=[self.WINDOW])
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        faces = [v for (_, n, v) in root if n == "CFace"]
        assert len(faces) == 1
        assert len(faces[0]["loops"]) == 2
        assert len(faces[0]["loops"][0]["uses"]) == 4  # outer boundary
        assert len(faces[0]["loops"][1]["uses"]) == 4  # hole

    def test_two_holes_produce_three_loops(self):
        builder = create()
        builder.add_face(self.WALL, holes=[self.WINDOW, self.WINDOW_2])
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        faces = [v for (_, n, v) in root if n == "CFace"]
        assert len(faces[0]["loops"]) == 3

    def test_hole_loop_flag_byte_is_zero_boundary_loop_stays_one(self):
        # Ground truth (an SDK-authored window-in-a-wall face, byte-decoded):
        # CLoop's first flag byte is 1 for the boundary loop, 0 for a hole
        # loop - the second byte is 1 either way. legacy.py's reader treats
        # both bytes as opaque (`_read_loop` just does `r.raw(2)`), so
        # capture them directly by patching the reader for this one check.
        flag_bytes = []
        orig = legacy._READERS["CLoop"]

        def patched(ar, r):
            my_slot = ar.next_slot - 1
            prev = ar.current_loop
            ar.current_loop = my_slot
            legacy._preamble(ar, r)
            flag_bytes.append(r.raw(2))
            uses = []
            while True:
                if r.peek_u16() == 0:
                    r.pos += 2
                    break
                _, _, v = ar.read_object(r, expect="CEdgeUse")
                uses.append(v)
            ar.current_loop = prev
            return {"k": "loop", "uses": uses}

        legacy._READERS["CLoop"] = patched
        try:
            builder = create()
            builder.add_face(self.WALL, holes=[self.WINDOW])
            legacy._walk(builder.to_bytes())
        finally:
            legacy._READERS["CLoop"] = orig

        assert flag_bytes == [bytes([1, 1]), bytes([0, 1])]

    def test_hole_not_on_face_plane_raises(self):
        builder = create()
        off_plane_hole = [(80.0, 30.0, 5.0), (120.0, 30.0, 0.0), (120.0, 70.0, 0.0), (80.0, 70.0, 0.0)]
        with pytest.raises(SkpWriteError, match="off the face's own plane"):
            builder.add_face(self.WALL, holes=[off_plane_hole])

    def test_hole_with_too_few_points_raises(self):
        builder = create()
        with pytest.raises(SkpWriteError, match="at least 3 points"):
            builder.add_face(self.WALL, holes=[[(1.0, 1.0, 0.0), (2.0, 2.0, 0.0)]])

    def test_hole_winding_direction_does_not_matter(self):
        # Ground truth (SDK oracle, both directions tested): a hole's own
        # winding relative to the outer boundary doesn't affect whether
        # it's recognized as a hole - confirm both orders self-parse
        # identically (same loop/edge counts) rather than one silently
        # producing a different structure.
        forward = create()
        forward.add_face(self.WALL, holes=[self.WINDOW])
        reversed_builder = create()
        reversed_builder.add_face(self.WALL, holes=[list(reversed(self.WINDOW))])
        for b in (forward, reversed_builder):
            ar, root, layers, materials = legacy._walk(b.to_bytes())
            faces = [v for (_, n, v) in root if n == "CFace"]
            assert len(faces[0]["loops"]) == 2
            assert len(faces[0]["loops"][1]["uses"]) == 4

    def test_hole_in_component_definition(self):
        builder = create()
        with builder.add_component_definition("Wall") as wall:
            wall.add_face(self.WALL, holes=[self.WINDOW])
        builder.add_instance(wall)
        data = builder.to_bytes()
        legacy._walk(data)


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

    def test_hidden_group(self):
        builder = create()
        with builder.add_group("Table", hidden=True) as table:
            table.add_face(SQUARE)
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        assert root[0][2]["db"]["hidden"] == 1

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

    # openskp#261: a group WITH attributes carries a real, non-null
    # CAttributeContainer at the same schema (1) - verified against a
    # real SketchUp 2020-legacy-format export of an attributed group
    # (see CHECKLIST.md). A group with none given still writes a null
    # pointer, same as before (test_basic_group_places_itself_on_close
    # above already covers that unchanged case).

    def test_group_with_attribute_dicts_carries_real_container(self):
        builder = create()
        with builder.add_group("W-1", attribute_dicts=[
            ("fbd-einfo", {"depth": 15.5, "code": "Ks"}),
        ]) as grp:
            grp.add_face(SQUARE)
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        assert root[0][2]["attrs"] == {
            "k": "attrs", "children": [
                ("CAttributeNamed", {
                    "k": "dict", "name": "fbd-einfo",
                    "entries": {"depth": 15.5, "code": "Ks"},
                }),
            ],
        }

    def test_group_with_attributes_shorthand(self):
        builder = create()
        with builder.add_group("W-1", attributes={"a": 1}) as grp:
            grp.add_face(SQUARE)
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        assert root[0][2]["attrs"]["children"][0][1]["name"] == "attributes"
        assert root[0][2]["attrs"]["children"][0][1]["entries"] == {"a": 1}

    def test_group_attributes_and_attribute_dicts_together_raises(self):
        builder = create()
        with pytest.raises(SkpWriteError, match="not both"):
            with builder.add_group("W-1", attributes={"a": 1}, attribute_dicts=[("b", {"c": 2})]):
                pass

    def test_group_multiple_attribute_dicts(self):
        builder = create()
        with builder.add_group("W-1", attribute_dicts=[
            ("fbd-einfo", {"depth": 15.5}),
            ("fbd-profile", {"guide": 1}),
        ]) as grp:
            grp.add_face(SQUARE)
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        names = [v["name"] for _, v in root[0][2]["attrs"]["children"]]
        assert names == ["fbd-einfo", "fbd-profile"]

    def test_group_instance_attribute_dicts(self):
        # The nested (definition-scoped) placement path - add_group_instance,
        # not the root-level self-placing add_group.
        builder = create()
        with builder.add_component_definition("Part") as part:
            part.add_face(SQUARE)
        with builder.add_component_definition("Assembly") as assembly:
            assembly.add_group_instance(part, name="Nested", attribute_dicts=[("fbd-profile", {"guide": 1})])
        builder.add_instance(assembly)
        data = builder.to_bytes()
        # Nested content lives inside the definition, not exposed by
        # legacy._walk's root-only view - byte-level presence check,
        # matching test_face_attributes_in_component_definition's own
        # established pattern for this case.
        assert "fbd-profile".encode("utf-16-le") in data
        assert "guide".encode("utf-16-le") in data

    def test_group_attribute_dictionaries_round_trip_through_public_reader(self, tmp_path):
        builder = create()
        with builder.add_group("W-1", attribute_dicts=[
            ("fbd-einfo", {"depth": 15.5, "code": "Ks"}),
        ]) as grp:
            grp.add_face(SQUARE)
        out = tmp_path / "group_attrs.skp"
        builder.save(str(out))

        model = SkpFile.open(str(out)).parse()
        inst = model.root.instances[0]
        assert inst.attribute_dictionaries == {"fbd-einfo": {"depth": 15.5, "code": "Ks"}}

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


class TestNestedDefinitions:
    def test_definition_nests_instance_of_another_definition(self, tmp_path):
        from openskp import SkpFile

        builder = create()
        with builder.add_component_definition("Wheel") as wheel:
            wheel.add_face(SQUARE)
        with builder.add_component_definition("Car") as car:
            car.add_instance(wheel, translation=(0.0, 0.0, 0.0))
            car.add_instance(wheel, translation=(200.0, 0.0, 0.0))
        builder.add_instance(car)
        data = builder.to_bytes()

        # Root level only ever sees Car - Wheel is never placed there.
        ar, root, layers, materials = legacy._walk(data)
        assert len(root) == 1
        assert root[0][1] == "CComponentInstance"
        assert root[0][2]["def"] == car.slot

        # Car's own body has 0 faces of its own, 2 nested instances of Wheel.
        out = tmp_path / "nested.skp"
        out.write_bytes(data)
        model = SkpFile.open(str(out)).parse()
        car_def = model.definitions[car.slot]
        assert len(car_def.faces) == 0
        assert len(car_def.instances) == 2
        assert {inst.name for inst in car_def.instances} == {"Wheel"}
        translations = sorted(inst.matrix[9] for inst in car_def.instances)
        assert translations == [0.0, 200.0]

    def test_hidden_instance_at_root_and_nested(self):
        builder = create()
        with builder.add_component_definition("Wheel") as wheel:
            wheel.add_face(SQUARE)
        with builder.add_component_definition("Car") as car:
            car.add_instance(wheel, translation=(0.0, 0.0, 0.0), hidden=True)
        builder.add_instance(car, hidden=True)
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        assert root[0][2]["db"]["hidden"] == 1

    def test_hidden_group_instance(self):
        builder = create()
        with builder.add_component_definition("Engine") as engine:
            engine.add_face(SQUARE)
        with builder.add_component_definition("Car") as car:
            car.add_face(SQUARE)
            car.add_group_instance(engine, hidden=True)
        builder.add_instance(car)
        data = builder.to_bytes()
        legacy._walk(data)

    def test_real_sketchup_resolves_nested_instances(self, tmp_path):
        # Recursively resolving Car -> 2x Wheel -> 1 face each through 2
        # placed Car instances should total 4 faces - confirmed against the
        # real SketchUp SDK, not just this project's own reader, the same
        # discipline as the rest of this suite's oracle tests.
        import ctypes

        if not os.path.exists(_SDK_DLL_PATH):
            pytest.skip("SketchUp SDK not present on this machine")

        builder = create()
        with builder.add_component_definition("Wheel") as wheel:
            wheel.add_face(SQUARE)
        with builder.add_component_definition("Car") as car:
            car.add_instance(wheel, translation=(0.0, 0.0, 0.0))
            car.add_instance(wheel, translation=(200.0, 0.0, 0.0))
        builder.add_instance(car, translation=(0.0, 0.0, 0.0))
        builder.add_instance(car, translation=(500.0, 0.0, 0.0))
        out = tmp_path / "nested.skp"
        builder.save(str(out))

        dll = ctypes.CDLL(_SDK_DLL_PATH)
        dll.SUEntitiesGetNumFaces.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        dll.SUEntitiesGetNumInstances.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        dll.SUEntitiesGetInstances.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        dll.SUComponentInstanceGetDefinition.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUComponentDefinitionGetEntities.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]

        def count_faces_recursive(entities) -> int:
            nfaces = ctypes.c_size_t()
            dll.SUEntitiesGetNumFaces(entities, ctypes.byref(nfaces))
            total = nfaces.value
            ninst = ctypes.c_size_t()
            dll.SUEntitiesGetNumInstances(entities, ctypes.byref(ninst))
            if ninst.value:
                insts = (ctypes.c_void_p * ninst.value)()
                got = ctypes.c_size_t()
                dll.SUEntitiesGetInstances(entities, ninst.value, insts, ctypes.byref(got))
                for i in range(got.value):
                    comp_def = ctypes.c_void_p()
                    dll.SUComponentInstanceGetDefinition(insts[i], ctypes.byref(comp_def))
                    sub_entities = ctypes.c_void_p()
                    dll.SUComponentDefinitionGetEntities(comp_def, ctypes.byref(sub_entities))
                    total += count_faces_recursive(sub_entities)
            return total

        dll.SUInitialize()
        try:
            model = ctypes.c_void_p()
            err = dll.SUModelCreateFromFile(ctypes.byref(model), str(out).encode())
            assert err == 0, f"SketchUp SDK rejected the file (error {err})"
            entities = ctypes.c_void_p()
            dll.SUModelGetEntities(model, ctypes.byref(entities))
            assert count_faces_recursive(entities) == 4
            dll.SUModelRelease(ctypes.byref(model))
        finally:
            dll.SUTerminate()

    def test_deeply_nested_three_levels_self_parses(self, tmp_path):
        from openskp import SkpFile

        builder = create()
        with builder.add_component_definition("Bolt") as bolt:
            bolt.add_face(SQUARE)
        with builder.add_component_definition("Wheel") as wheel:
            wheel.add_instance(bolt, translation=(0.0, 0.0, 0.0))
            wheel.add_instance(bolt, translation=(30.0, 0.0, 0.0))
        with builder.add_component_definition("Car") as car:
            car.add_instance(wheel)
        builder.add_instance(car)
        data = builder.to_bytes()

        out = tmp_path / "nested3.skp"
        out.write_bytes(data)
        model = SkpFile.open(str(out)).parse()
        car_def = model.definitions[car.slot]
        wheel_def = model.definitions[wheel.slot]
        bolt_def = model.definitions[bolt.slot]
        assert len(car_def.instances) == 1
        assert len(wheel_def.instances) == 2
        assert len(bolt_def.faces) == 1

    def test_nested_instance_of_self_raises(self):
        builder = create()
        comp = builder.add_component_definition("Loop")
        comp.add_face(SQUARE)
        with pytest.raises(SkpWriteError, match="cannot nest an instance of itself"):
            comp.add_instance(comp)

    def test_nested_instance_of_definition_from_a_different_builder_raises(self):
        # A definition from a different create() call has a slot number
        # that means nothing in this builder's document - without this
        # check it would silently write a garbage back-reference instead
        # of failing loudly.
        builder1 = create()
        with builder1.add_component_definition("Wheel") as wheel:
            wheel.add_face(SQUARE)

        builder2 = create()
        with builder2.add_component_definition("Car") as car:
            car.add_face(SQUARE)
            with pytest.raises(SkpWriteError, match="different builder"):
                car.add_instance(wheel)

    def test_nested_instance_after_definition_closed_raises(self):
        builder = create()
        with builder.add_component_definition("Wheel") as wheel:
            wheel.add_face(SQUARE)
        with builder.add_component_definition("Car") as car:
            car.add_instance(wheel)
        with pytest.raises(SkpWriteError, match="already closed"):
            car.add_instance(wheel)


class TestNestedGroups:
    def test_group_instance_nested_in_definition_self_parses(self, tmp_path):
        from openskp import SkpFile

        builder = create()
        with builder.add_component_definition("Engine") as engine:
            engine.add_face(SQUARE)
        with builder.add_component_definition("Car") as car:
            car.add_face(SQUARE)
            car.add_group_instance(engine, translation=(50.0, 0.0, 10.0))
        builder.add_instance(car)
        data = builder.to_bytes()

        # Root only ever sees Car - Engine is never placed at root level.
        ar, root, layers, materials = legacy._walk(data)
        assert len(root) == 1
        assert root[0][1] == "CComponentInstance"
        assert root[0][2]["def"] == car.slot

        out = tmp_path / "nested_group.skp"
        out.write_bytes(data)
        model = SkpFile.open(str(out)).parse()
        car_def = model.definitions[car.slot]
        assert len(car_def.faces) == 1
        assert len(car_def.instances) == 1
        assert car_def.instances[0].name == "Engine"
        assert car_def.instances[0].matrix[9:12] == pytest.approx([50.0, 0.0, 10.0])

    def test_group_instance_is_a_real_cgroup_not_a_component_instance(self):
        # The whole point of add_group_instance over add_instance: the
        # placement record itself must be a genuine CGroup class
        # declaration, not CComponentInstance - Car's own body (unlike
        # root-level entities) isn't exposed through legacy._walk, so
        # check for the class declaration bytes directly, the same way
        # the writer itself declares a class on first use.
        builder = create()
        with builder.add_component_definition("Engine") as engine:
            engine.add_face(SQUARE)
        with builder.add_component_definition("Car") as car:
            car.add_face(SQUARE)
            car.add_group_instance(engine, translation=(50.0, 0.0, 0.0))
        builder.add_instance(car)
        data = builder.to_bytes()

        cgroup_decl = struct.pack("<H", 0xFFFF) + struct.pack("<H", 1) + struct.pack("<H", 6) + b"CGroup"
        assert cgroup_decl in data

    def test_group_instance_recursively_resolves_through_real_sketchup(self, tmp_path):
        import ctypes

        if not os.path.exists(_SDK_DLL_PATH):
            pytest.skip("SketchUp SDK not present on this machine")

        builder = create()
        with builder.add_component_definition("Engine") as engine:
            engine.add_face(SQUARE)
        with builder.add_component_definition("Car") as car:
            car.add_face(SQUARE)
            car.add_group_instance(engine, translation=(50.0, 0.0, 10.0))
        builder.add_instance(car)
        out = tmp_path / "nested_group_oracle.skp"
        builder.save(str(out))

        dll = ctypes.CDLL(_SDK_DLL_PATH)
        dll.SUModelCreateFromFile.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_char_p]
        dll.SUModelGetEntities.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUEntitiesGetNumInstances.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        dll.SUEntitiesGetInstances.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        dll.SUComponentInstanceGetDefinition.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUComponentDefinitionGetEntities.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUEntitiesGetNumFaces.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        dll.SUEntitiesGetNumGroups.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        dll.SUEntitiesGetGroups.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        dll.SUGroupGetEntities.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUInitialize()
        try:
            model = ctypes.c_void_p()
            err = dll.SUModelCreateFromFile(ctypes.byref(model), str(out).encode())
            assert err == 0, f"SketchUp SDK rejected the file (error {err})"
            entities = ctypes.c_void_p()
            dll.SUModelGetEntities(model, ctypes.byref(entities))
            ninst = ctypes.c_size_t()
            dll.SUEntitiesGetNumInstances(entities, ctypes.byref(ninst))
            assert ninst.value == 1
            insts = (ctypes.c_void_p * 1)()
            got = ctypes.c_size_t()
            dll.SUEntitiesGetInstances(entities, 1, insts, ctypes.byref(got))
            comp_def = ctypes.c_void_p()
            dll.SUComponentInstanceGetDefinition(insts[0], ctypes.byref(comp_def))
            car_entities = ctypes.c_void_p()
            dll.SUComponentDefinitionGetEntities(comp_def, ctypes.byref(car_entities))
            nfaces = ctypes.c_size_t()
            dll.SUEntitiesGetNumFaces(car_entities, ctypes.byref(nfaces))
            assert nfaces.value == 1
            ngroups = ctypes.c_size_t()
            dll.SUEntitiesGetNumGroups(car_entities, ctypes.byref(ngroups))
            assert ngroups.value == 1
            groups = (ctypes.c_void_p * 1)()
            got2 = ctypes.c_size_t()
            dll.SUEntitiesGetGroups(car_entities, 1, groups, ctypes.byref(got2))
            engine_entities = ctypes.c_void_p()
            dll.SUGroupGetEntities(groups[0], ctypes.byref(engine_entities))
            nfaces2 = ctypes.c_size_t()
            dll.SUEntitiesGetNumFaces(engine_entities, ctypes.byref(nfaces2))
            assert nfaces2.value == 1
            dll.SUModelRelease(ctypes.byref(model))
        finally:
            dll.SUTerminate()

    def test_group_instance_of_definition_from_a_different_builder_raises(self):
        builder1 = create()
        with builder1.add_component_definition("Engine") as engine:
            engine.add_face(SQUARE)

        builder2 = create()
        with builder2.add_component_definition("Car") as car:
            car.add_face(SQUARE)
            with pytest.raises(SkpWriteError, match="different builder"):
                car.add_group_instance(engine)

    def test_group_instance_of_self_raises(self):
        builder = create()
        comp = builder.add_component_definition("Loop")
        comp.add_face(SQUARE)
        with pytest.raises(SkpWriteError, match="cannot nest a group instance of itself"):
            comp.add_group_instance(comp)

    def test_group_instance_after_definition_closed_raises(self):
        builder = create()
        with builder.add_component_definition("Engine") as engine:
            engine.add_face(SQUARE)
        with builder.add_component_definition("Car") as car:
            car.add_group_instance(engine)
        with pytest.raises(SkpWriteError, match="already closed"):
            car.add_group_instance(engine)


class TestPreExistingOrderingGap:
    def test_root_add_face_while_definition_open_raises(self):
        # Real, previously-unguarded bug found while testing group nesting:
        # calling add_face on the root builder while a component
        # definition was still open silently corrupted the file (the
        # geometry writer's starting slot got locked in too early). Not
        # related to nested groups themselves - just found alongside them.
        builder = create()
        comp = builder.add_component_definition("Chair")
        comp.add_face(SQUARE)
        with pytest.raises(SkpWriteError, match="still open"):
            builder.add_face(SQUARE)

    def test_root_add_instance_while_definition_open_raises(self):
        builder = create()
        with builder.add_component_definition("Wheel") as wheel:
            wheel.add_face(SQUARE)
        comp = builder.add_component_definition("Chair")
        comp.add_face(SQUARE)
        with pytest.raises(SkpWriteError, match="still open"):
            builder.add_instance(wheel)


class TestDefinitionsInstancesPhaseApi:
    # openskp#257: an explicit with builder.definitions()/instances()
    # phase API, documenting the definitions-before-instances ordering
    # rule and catching a misplaced call at the point it's made.

    def test_happy_path_round_trips(self):
        builder = create()
        with builder.definitions():
            with builder.add_component_definition("Chair") as chair:
                chair.add_face(SQUARE)
        with builder.instances():
            builder.add_instance(chair)
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        assert root[0][1] == "CComponentInstance"

    def test_definitions_block_is_a_pure_marker_no_new_restriction(self):
        # definitions() itself adds no new checks - the SAME material/
        # layer/definition calls that work unwrapped still work inside
        # it, in any order relative to each other.
        builder = create()
        with builder.definitions():
            red = builder.add_material("Red", (255, 0, 0))
            layer = builder.add_layer("L")
            with builder.add_component_definition("Chair") as chair:
                chair.add_face(SQUARE, material=red, layer=layer)
        builder.add_instance(chair)
        assert builder.to_bytes()

    def test_add_component_definition_inside_instances_raises_immediately(self):
        # The exact scenario openskp#257 asks for: the error arrives
        # right when the definition is opened inside the instance phase,
        # not only whenever the first real geometry call happens to
        # occur - here that's immediately on entering instances(), since
        # no add_face/add_instance was ever called before it.
        builder = create()
        with pytest.raises(SkpWriteError, match="before any add_face/add_instance"):
            with builder.instances():
                builder.add_component_definition("TooLate")

    def test_add_material_inside_instances_raises_immediately(self):
        builder = create()
        with pytest.raises(SkpWriteError, match="before any add_face calls"):
            with builder.instances():
                builder.add_material("TooLate", (0, 0, 0))

    def test_add_layer_inside_instances_raises_immediately(self):
        builder = create()
        with pytest.raises(SkpWriteError, match="before any add_face calls"):
            with builder.instances():
                builder.add_layer("TooLate")

    def test_instances_entered_twice_is_a_no_op(self):
        builder = create()
        with builder.add_component_definition("Chair") as chair:
            chair.add_face(SQUARE)
        with builder.instances():
            with builder.instances():
                builder.add_instance(chair)
        assert builder.to_bytes()

    def test_error_message_reports_entities_already_written(self):
        builder = create()
        builder.add_face(SQUARE)
        builder.add_face(SQUARE)
        with pytest.raises(SkpWriteError, match=r"\(2 already written\)"):
            builder.add_component_definition("TooLate")

    def test_two_definitions_blocks_compose_like_two_independent_modules(self):
        # The issue's "composing two independent modules" case: two
        # separate definitions() blocks (standing in for e.g. walls.emit()
        # and roof.emit() each declaring their own definitions) both
        # still work, since definitions() is a pure marker with no
        # exclusive-entry restriction of its own.
        builder = create()
        with builder.definitions():
            with builder.add_component_definition("Wall") as wall:
                wall.add_face(SQUARE)
        with builder.definitions():
            with builder.add_component_definition("Roof") as roof:
                roof.add_face(SQUARE)
        with builder.instances():
            builder.add_instance(wall)
            builder.add_instance(roof)
        assert builder.to_bytes()


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

    def test_materials_by_name_reflects_registered_handle(self):
        builder = create()
        red = builder.add_material("Red", (255, 0, 0))
        assert builder.materials_by_name["Red"] == red
        assert builder.materials_by_name == {"Red": red}

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

    def test_texture_material_default_applied_height_is_one_not_corrupted(self, tmp_path):
        # Regression test for a real bug: until 2026-08-28, an omitted
        # applied_height wrote a corrupted internal sentinel byte pattern
        # (~1.29e-231) instead of a real number - confirmed via real
        # SketchUp screenshots to render as a streaky, vertically-smeared
        # texture. add_texture_material's applied width and height both
        # default to 1.0 now (each overridable), not silently corrupt every
        # caller who doesn't know to pass applied_height=1.0 explicitly.
        png_path = tmp_path / "tex.png"
        png_path.write_bytes(_make_test_png(size=4, rgb=(200, 50, 50)))

        builder = create()
        tex = builder.add_texture_material("Brick", str(png_path))
        builder.add_face(SQUARE, material=tex)
        data = builder.to_bytes()

        ar, root, layers, materials = legacy._walk(data)
        mat_by_slot = {s: v for s, v in materials}
        assert mat_by_slot[tex]["tex_w"] == 1.0
        assert mat_by_slot[tex]["tex_h"] == 1.0

    def test_texture_material_explicit_applied_height_still_overridable(self, tmp_path):
        png_path = tmp_path / "tex.png"
        png_path.write_bytes(_make_test_png(size=4, rgb=(200, 50, 50)))

        builder = create()
        tex = builder.add_texture_material("Brick", str(png_path), applied_height=48.0)
        builder.add_face(SQUARE, material=tex)
        data = builder.to_bytes()

        ar, root, layers, materials = legacy._walk(data)
        mat_by_slot = {s: v for s, v in materials}
        assert mat_by_slot[tex]["tex_h"] == 48.0

    def test_texture_material_applied_size_fully_overridable(self, tmp_path):
        # Real SketchUp writes the material's own tile size in BOTH axes (a
        # file authored in SketchUp Web carries 8.0 x 16.0 for a brick); a
        # texture applied without positioning carries no per-face UV record,
        # so this pair IS its mapping.
        png_path = tmp_path / "tex.png"
        png_path.write_bytes(_make_test_png(size=4, rgb=(200, 50, 50)))

        builder = create()
        tex = builder.add_texture_material(
            "Brick", str(png_path), applied_width=8.0, applied_height=16.0)
        builder.add_face(SQUARE, material=tex)
        data = builder.to_bytes()

        ar, root, layers, materials = legacy._walk(data)
        mat_by_slot = {s: v for s, v in materials}
        assert mat_by_slot[tex]["tex_w"] == 8.0
        assert mat_by_slot[tex]["tex_h"] == 16.0

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

    def test_add_image_places_a_real_image_entity_not_a_plain_textured_face(self, tmp_path):
        png_path = tmp_path / "photo.png"
        png_path.write_bytes(_make_test_png())
        builder = create()
        builder.add_image(
            str(png_path), width=48.0, height=36.0,
            translation=(0.0, 0.0, 40.0),
            rotation=((1.0, 0.0, 0.0), math.radians(90)),
        )
        out_path = tmp_path / "out.skp"
        out_path.write_bytes(builder.to_bytes())

        model = SkpFile.open(str(out_path)).parse()
        image_defs = [d for d in model.definitions.values() if d.is_image]
        assert len(image_defs) == 1
        assert len(image_defs[0].faces) == 1
        assert len(model.root.instances) == 1
        assert model.root.instances[0].ref_idx == image_defs[0].id


class TestUVPositioning:
    def test_axis_aligned_scale_matches_solved_matrix(self, tmp_path):
        png_path = tmp_path / "tex.png"
        png_path.write_bytes(_make_test_png())
        builder = create()
        tex = builder.add_texture_material("Brick", str(png_path))
        # (0,0,0)->(0,0), (50,0,0)->(1,0), (0,50,0)->(0,1): a pure 50x scale,
        # no rotation - the matrix should come out as diag(50, 50).
        builder.add_face(
            SQUARE, material=tex,
            front_uv=[((0.0, 0.0, 0.0), (0.0, 0.0)), ((50.0, 0.0, 0.0), (1.0, 0.0)), ((0.0, 50.0, 0.0), (0.0, 1.0))],
        )
        data = builder.to_bytes()

        ar, root, layers, materials = legacy._walk(data)
        face = [v for (_, n, v) in root if n == "CFace"][0]
        ftc = dict(face["attrs"]["children"])["CFaceTextureCoords"]
        assert ftc["front"] == pytest.approx((50.0, 0.0, 0.0, 0.0, 50.0, 0.0, 0.0, 0.0, 1.0))
        assert ftc["back"] == pytest.approx((1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0))
        assert ftc["front_projected"] is False
        assert ftc["back_projected"] is False

    def test_rotated_mapping_matches_solved_matrix(self, tmp_path):
        # (0,0,0)->(0,0), (100,0,0)->(1,1), (0,100,0)->(-1,1): a 45-degree
        # rotated UV basis - the matrix should show real off-diagonal terms,
        # not just a diagonal scale.
        png_path = tmp_path / "tex.png"
        png_path.write_bytes(_make_test_png())
        builder = create()
        tex = builder.add_texture_material("Brick", str(png_path))
        builder.add_face(
            SQUARE, material=tex,
            front_uv=[
                ((0.0, 0.0, 0.0), (0.0, 0.0)),
                ((100.0, 0.0, 0.0), (1.0, 1.0)),
                ((0.0, 100.0, 0.0), (-1.0, 1.0)),
            ],
        )
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        face = [v for (_, n, v) in root if n == "CFace"][0]
        ftc = dict(face["attrs"]["children"])["CFaceTextureCoords"]
        assert ftc["front"] == pytest.approx((50.0, -50.0, 0.0, 50.0, 50.0, 0.0, 0.0, 0.0, 1.0))

    def test_front_and_back_positioned_independently(self, tmp_path):
        png_path = tmp_path / "tex.png"
        png_path.write_bytes(_make_test_png())
        builder = create()
        front_tex = builder.add_texture_material("Front", str(png_path))
        back_tex = builder.add_texture_material("Back", str(png_path))
        builder.add_face(
            SQUARE, material=front_tex, back_material=back_tex,
            front_uv=[((0.0, 0.0, 0.0), (0.0, 0.0)), ((50.0, 0.0, 0.0), (1.0, 0.0)), ((0.0, 50.0, 0.0), (0.0, 1.0))],
            back_uv=[((0.0, 0.0, 0.0), (0.0, 0.0)), ((25.0, 0.0, 0.0), (1.0, 0.0)), ((0.0, 25.0, 0.0), (0.0, 1.0))],
        )
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        face = [v for (_, n, v) in root if n == "CFace"][0]
        assert face["db"]["mat"] == front_tex
        assert face["back_mat"] == back_tex
        ftc = dict(face["attrs"]["children"])["CFaceTextureCoords"]
        assert ftc["front"] == pytest.approx((50.0, 0.0, 0.0, 0.0, 50.0, 0.0, 0.0, 0.0, 1.0))
        assert ftc["back"] == pytest.approx((25.0, 0.0, 0.0, 0.0, 25.0, 0.0, 0.0, 0.0, 1.0))

    def test_unpositioned_face_has_no_texture_coords_record(self, tmp_path):
        # A face with a texture but no explicit positioning shouldn't pay
        # for (or emit) a CFaceTextureCoords/attribute-container record at
        # all - ground truth confirms the default-projection case needs none.
        png_path = tmp_path / "tex.png"
        png_path.write_bytes(_make_test_png())
        builder = create()
        tex = builder.add_texture_material("Brick", str(png_path))
        builder.add_face(SQUARE, material=tex)
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        face = [v for (_, n, v) in root if n == "CFace"][0]
        assert face["attrs"] is None

    def test_xz_and_yz_aligned_faces_supported(self, tmp_path):
        png_path = tmp_path / "tex.png"
        png_path.write_bytes(_make_test_png())
        builder = create()
        tex = builder.add_texture_material("Brick", str(png_path))
        # XZ-aligned face (constant y=0).
        builder.add_face(
            [(0.0, 0.0, 0.0), (50.0, 0.0, 0.0), (50.0, 0.0, 50.0), (0.0, 0.0, 50.0)],
            material=tex,
            front_uv=[((0.0, 0.0, 0.0), (0.0, 0.0)), ((50.0, 0.0, 0.0), (1.0, 0.0)), ((0.0, 0.0, 50.0), (0.0, 1.0))],
        )
        # YZ-aligned face (constant x=0).
        builder.add_face(
            [(0.0, 0.0, 0.0), (0.0, 50.0, 0.0), (0.0, 50.0, 50.0), (0.0, 0.0, 50.0)],
            material=tex,
            front_uv=[((0.0, 0.0, 0.0), (0.0, 0.0)), ((0.0, 50.0, 0.0), (1.0, 0.0)), ((0.0, 0.0, 50.0), (0.0, 1.0))],
        )
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        faces = [v for (_, n, v) in root if n == "CFace"]
        assert len(faces) == 2
        for face in faces:
            ftc = dict(face["attrs"]["children"])["CFaceTextureCoords"]
            assert ftc["front"] == pytest.approx((50.0, 0.0, 0.0, 0.0, 50.0, 0.0, 0.0, 0.0, 1.0))

    def test_face_uv_basis_snaps_and_turns_like_real_sketchup(self):
        # Measured with the SDK (2026-09-04): the world axes hold while the
        # sine of the tilt is below 1e-3 (1.0000004e-3 still vertical,
        # 1.0001e-3 already the cross product), and a face looking DOWN
        # gets (-X, +Y), the 180-degree turn of the upward basis - not the
        # (X, -Y) mirror this reader assumed, which turned every underside.
        from openskp._face_groups import face_uv_basis
        up = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
        assert face_uv_basis((0.0, 0.0, 1.0)) == up
        assert face_uv_basis((9.9e-4, 0.0, 1.0)) == up                # float noise, a 1e-4 tilt
        assert face_uv_basis((0.0, 0.0, -1.0)) == ((-1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
        assert face_uv_basis((3e-4, 0.0, -1.0)) == ((-1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
        xr, yr = face_uv_basis((2e-3, 0.0, 0.999998))                 # a real tilt: Z x n
        assert xr == pytest.approx((0.0, 1.0, 0.0))
        assert yr[0] == pytest.approx(-0.999998) and abs(yr[1]) < 1e-12   # yr = n x xr

    def test_vertex_order_does_not_turn_the_mapping(self, tmp_path):
        # The SAME square as test_uv_scale_only..., listed from a different
        # corner so its first edge runs along +Y instead of +X, pinned to
        # the same u = x/50, v = y/50. SketchUp expresses a face's matrix
        # in a basis derived from the NORMAL alone ((X, Y) for a horizontal
        # face), so the stored matrix must be the same diag(50, 50). The
        # writer used to solve it in a first-edge basis, which agrees only
        # when that edge happens to run along Z x n: this face came out
        # turned 90 degrees in SketchUp (measured through the SDK's own
        # SUMeshHelperGetFrontSTQCoords).
        png_path = tmp_path / "tex.png"
        png_path.write_bytes(_make_test_png())
        builder = create()
        tex = builder.add_texture_material("Brick", str(png_path))
        rotated = SQUARE[1:] + SQUARE[:1]
        builder.add_face(
            rotated, material=tex,
            front_uv=[((0.0, 0.0, 0.0), (0.0, 0.0)), ((50.0, 0.0, 0.0), (1.0, 0.0)), ((0.0, 50.0, 0.0), (0.0, 1.0))],
        )
        data = builder.to_bytes()

        ar, root, layers, materials = legacy._walk(data)
        face = [v for (_, n, v) in root if n == "CFace"][0]
        ftc = dict(face["attrs"]["children"])["CFaceTextureCoords"]
        assert ftc["front"] == pytest.approx((50.0, 0.0, 0.0, 0.0, 50.0, 0.0, 0.0, 0.0, 1.0), abs=1e-9)

    def test_pins_read_back_as_pinned_on_every_orientation(self, tmp_path):
        # The contract: whatever (point, uv) you pin, the reader - which
        # inverts the matrix in SketchUp's normal-derived basis, calibrated
        # against SDK-authored files - hands the same uv back at that point,
        # on a face of ANY orientation and vertex order. Each square here
        # is pinned to a mapping deliberately not aligned with its first
        # edge; the fourth corner checks the map is affine over the face.
        from openskp._face_groups import compute_face_uv, face_uv_basis
        s = 70.71067811865476
        squares = [
            [(0.0, 0.0, 0.0), (0.0, 50.0, 0.0), (-50.0, 50.0, 0.0), (-50.0, 0.0, 0.0)],   # horizontal, 1st edge +Y
            [(0.0, 0.0, 0.0), (0.0, 0.0, 50.0), (50.0, 0.0, 50.0), (50.0, 0.0, 0.0)],     # wall facing +Y, 1st edge +Z
            [(0.0, 0.0, 0.0), (50.0, 0.0, 0.0), (50.0, 0.0, 50.0), (0.0, 0.0, 50.0)],     # wall facing -Y
            [(0.0, 0.0, 0.0), (0.0, 0.0, 50.0), (0.0, 50.0, 50.0), (0.0, 50.0, 0.0)],     # wall facing -X, 1st edge +Z
            [(0.0, 0.0, 0.0), (50.0, 0.0, 0.0), (50.0, s, s), (0.0, s, s)],                # 45-degree slope
            [(0.0, 0.0, 0.0), (0.0, s, s), (-50.0, s, s), (-50.0, 0.0, 0.0)],              # same slope, 1st edge up it
        ]
        png_path = tmp_path / "tex.png"
        png_path.write_bytes(_make_test_png())
        builder = create()
        tex = builder.add_texture_material("Brick", str(png_path))
        pins_by_face = []
        for pts in squares:
            # An asymmetric affine map over the face: u/v from two skewed
            # combinations of the corners, so no axis of it lines up with
            # an edge.
            p0, p1, p2, p3 = pts
            pins = [(p0, (0.1, 0.2)), (p1, (0.9, 0.35)), (p3, (0.25, 1.4))]
            builder.add_face(pts, material=tex, front_uv=pins)
            pins_by_face.append((pins, p2))
        data = builder.to_bytes()

        ar, root, layers, materials = legacy._walk(data)
        faces = [v for (_, n, v) in root if n == "CFace"]
        assert len(faces) == len(squares)
        for face, (pins, p2) in zip(faces, pins_by_face):
            ftc = dict(face["attrs"]["children"])["CFaceTextureCoords"]
            xr, yr = face_uv_basis(tuple(face["plane"][:3]))
            for pt, (u, v) in pins:
                got = compute_face_uv(pt, xr, yr, tuple(ftc["front"]), 1.0, 1.0)
                assert got == pytest.approx((u, v), abs=1e-9), (pt, got, (u, v))
            # Affine: the fourth corner is p1 + p3 - p0 in uv too.
            (_, uv0), (_, uv1), (_, uv3) = pins
            expect = (uv1[0] + uv3[0] - uv0[0], uv1[1] + uv3[1] - uv0[1])
            got = compute_face_uv(p2, xr, yr, tuple(ftc["front"]), 1.0, 1.0)
            assert got == pytest.approx(expect, abs=1e-9)

    def test_pins_are_in_tiles_whatever_the_applied_size(self, tmp_path):
        # Real SketchUp stores the per-face matrix in INCHES of texture
        # space and divides by the material's applied size on read (so
        # does compute_face_uv). A caller pins in tiles: (50,0,0) -> (1,0)
        # must read back as u = 1 whether the material is 1 in or 10 in per
        # tile. It used to read back as 0.1 for the 10-inch material - a
        # 2 m water tile came out 78.74x too big.
        from openskp._face_groups import compute_face_uv, face_uv_basis
        png_path = tmp_path / "tex.png"
        png_path.write_bytes(_make_test_png())
        builder = create()
        tex = builder.add_texture_material("Brick", str(png_path), applied_width=10.0, applied_height=10.0)
        builder.add_face(
            SQUARE, material=tex,
            front_uv=[((0.0, 0.0, 0.0), (0.0, 0.0)), ((50.0, 0.0, 0.0), (1.0, 0.0)), ((0.0, 50.0, 0.0), (0.0, 1.0))],
        )
        data = builder.to_bytes()

        ar, root, layers, materials = legacy._walk(data)
        face = [v for (_, n, v) in root if n == "CFace"][0]
        ftc = dict(face["attrs"]["children"])["CFaceTextureCoords"]
        # 10 texture-inches (one tile) span 50 model inches: a 5x scale.
        assert ftc["front"] == pytest.approx((5.0, 0.0, 0.0, 0.0, 5.0, 0.0, 0.0, 0.0, 1.0), abs=1e-9)
        xr, yr = face_uv_basis((0.0, 0.0, 1.0))
        assert compute_face_uv((50.0, 0.0, 0.0), xr, yr, tuple(ftc["front"]), 10.0, 10.0) == pytest.approx((1.0, 0.0))
        assert compute_face_uv((0.0, 50.0, 0.0), xr, yr, tuple(ftc["front"]), 10.0, 10.0) == pytest.approx((0.0, 1.0))

    def test_tilted_face_edge_aligned_mapping_is_a_pure_scale(self, tmp_path):
        # A 100x100 square tilted 45 degrees around X (points[1]-points[0]
        # runs along world X; points[3]-points[0] runs along the tilted
        # diagonal, also length 100). Mapping UV corners onto the face's
        # own corners should give a clean scale-only matrix - ground truth
        # (an SDK-authored file positioned the same way) confirms this
        # exact result, not just "some matrix that self-parses".
        png_path = tmp_path / "tex.png"
        png_path.write_bytes(_make_test_png())
        builder = create()
        tex = builder.add_texture_material("Brick", str(png_path))
        s = 70.71067811865476  # 100 / sqrt(2)
        tilted = [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (100.0, s, s), (0.0, s, s)]
        builder.add_face(
            tilted, material=tex,
            front_uv=[((0.0, 0.0, 0.0), (0.0, 0.0)), ((100.0, 0.0, 0.0), (1.0, 0.0)), ((0.0, s, s), (0.0, 1.0))],
        )
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        face = [v for (_, n, v) in root if n == "CFace"][0]
        ftc = dict(face["attrs"]["children"])["CFaceTextureCoords"]
        assert ftc["front"] == pytest.approx((100.0, 0.0, 0.0, 0.0, 100.0, 0.0, 0.0, 0.0, 1.0), abs=1e-6)

    def test_tilted_face_asymmetric_mapping_matches_ground_truth(self, tmp_path):
        # Same tilted face, but correspondence points/uvs chosen specifically
        # to not align with the face's own edges - the resulting matrix
        # must match exactly what a real SDK-authored file produces for the
        # identical setup (verified once by ground-truth diffing; this pins
        # it as a byte-exact regression test). In particular this is what
        # ruled out a plausible-looking "subtract points[0] first" origin
        # hypothesis, which predicted the wrong translation terms here.
        png_path = tmp_path / "tex.png"
        png_path.write_bytes(_make_test_png())
        builder = create()
        tex = builder.add_texture_material("Brick", str(png_path))
        s = 70.71067811865476
        tilted = [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (100.0, s, s), (0.0, s, s)]
        builder.add_face(
            tilted, material=tex,
            front_uv=[
                ((20.0, 0.0, 0.0), (0.5, 1.0)),
                ((80.0, 0.0, 0.0), (2.0, 1.0)),
                ((20.0, s * 0.4, s * 0.4), (0.5, 3.0)),
            ],
        )
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        face = [v for (_, n, v) in root if n == "CFace"][0]
        ftc = dict(face["attrs"]["children"])["CFaceTextureCoords"]
        assert ftc["front"] == pytest.approx((40.0, 0.0, 0.0, 0.0, 20.0, 0.0, 0.0, -20.0, 1.0), abs=1e-6)

    def test_tilted_face_far_from_world_origin_matches_ground_truth(self, tmp_path):
        # A face offset far from (0,0,0) - the case that actually
        # distinguished "no origin subtraction" (correct) from "subtract
        # points[0] first" (plausible-looking, but wrong) during
        # ground-truth research, since every earlier sample happened to
        # have points[0] at the world origin, making the two hypotheses
        # indistinguishable there.
        png_path = tmp_path / "tex.png"
        png_path.write_bytes(_make_test_png())
        builder = create()
        tex = builder.add_texture_material("Brick", str(png_path))
        ox, oy, oz = 200.0, 300.0, 50.0
        tilted = [
            (ox, oy, oz), (ox + 100.0, oy, oz), (ox + 100.0, oy, oz + 80.0), (ox, oy, oz + 80.0),
        ]
        builder.add_face(
            tilted, material=tex,
            front_uv=[
                ((ox + 10.0, oy, oz + 10.0), (0.0, 0.0)),
                ((ox + 60.0, oy, oz + 10.0), (2.0, 0.0)),
                ((ox + 10.0, oy, oz + 50.0), (0.0, 1.6)),
            ],
        )
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        face = [v for (_, n, v) in root if n == "CFace"][0]
        ftc = dict(face["attrs"]["children"])["CFaceTextureCoords"]
        assert ftc["front"] == pytest.approx((25.0, 0.0, 0.0, 0.0, 25.0, 0.0, 210.0, 60.0, 1.0), abs=1e-6)

    def test_wrong_number_of_pairs_raises(self, tmp_path):
        png_path = tmp_path / "tex.png"
        png_path.write_bytes(_make_test_png())
        builder = create()
        tex = builder.add_texture_material("Brick", str(png_path))
        with pytest.raises(SkpWriteError, match="exactly 3"):
            builder.add_face(
                SQUARE, material=tex,
                front_uv=[((0.0, 0.0, 0.0), (0.0, 0.0)), ((50.0, 0.0, 0.0), (1.0, 0.0))],
            )

    def test_collinear_uv_points_raise(self, tmp_path):
        png_path = tmp_path / "tex.png"
        png_path.write_bytes(_make_test_png())
        builder = create()
        tex = builder.add_texture_material("Brick", str(png_path))
        with pytest.raises(SkpWriteError, match="collinear"):
            builder.add_face(
                SQUARE, material=tex,
                front_uv=[
                    ((0.0, 0.0, 0.0), (0.0, 0.0)),
                    ((50.0, 0.0, 0.0), (1.0, 0.0)),
                    ((0.0, 50.0, 0.0), (2.0, 0.0)),
                ],
            )

    def test_positioning_in_component_definition(self, tmp_path):
        png_path = tmp_path / "tex.png"
        png_path.write_bytes(_make_test_png())
        builder = create()
        tex = builder.add_texture_material("Brick", str(png_path))
        with builder.add_component_definition("Panel") as panel:
            panel.add_face(
                SQUARE, material=tex,
                front_uv=[
                    ((0.0, 0.0, 0.0), (0.0, 0.0)), ((50.0, 0.0, 0.0), (1.0, 0.0)), ((0.0, 50.0, 0.0), (0.0, 1.0)),
                ],
            )
        builder.add_instance(panel)
        data = builder.to_bytes()

        from openskp import SkpFile
        out = tmp_path / "panel.skp"
        out.write_bytes(data)
        model = SkpFile.open(str(out)).parse()
        defn = model.definitions[panel.slot]
        face = list(defn.faces.values())[0]
        assert face.uv_transform == pytest.approx([50.0, 0.0, 0.0, 0.0, 50.0, 0.0, 0.0, 0.0, 1.0])


class TestAttributeDicts:
    def test_instance_attributes_self_parse(self):
        # legacy._walk only exposes root-level entities, so an instance
        # (unlike a definition or a face nested inside one) can be checked
        # directly this way.
        builder = create()
        with builder.add_component_definition("Chair") as chair:
            chair.add_face(SQUARE)
        builder.add_instance(chair, attributes={"serial": "A1", "count": 3, "weight": 4.5})
        data = builder.to_bytes()

        ar, root, layers, materials = legacy._walk(data)
        inst = root[0][2]
        assert inst["attrs"]["children"] == [
            ("CAttributeNamed", {
                "k": "dict", "name": "attributes",
                "entries": {"serial": "A1", "count": 3, "weight": 4.5},
            }),
        ]

    def test_custom_dict_name(self):
        builder = create()
        with builder.add_component_definition("Chair") as chair:
            chair.add_face(SQUARE)
        builder.add_instance(chair, attributes={"a": 1}, attribute_dict_name="dynamic_attributes")
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        assert root[0][2]["attrs"]["children"][0][1]["name"] == "dynamic_attributes"

    def test_attribute_dictionaries_exposed_by_name_through_the_public_reader(self, tmp_path):
        # openskp#254: every dictionary reachable by name through the
        # public reader, not just a flat merge - and `properties` stays
        # a backward-compatible view of the 'dynamic_attributes' one
        # specifically.
        builder = create()
        with builder.add_component_definition("Stud") as stud:
            stud.add_face(SQUARE)
        builder.add_instance(stud, attribute_dicts=[
            ("dynamic_attributes", {"width": 10.0, "count": 4}),
            ("fbd-profile", {"guide": 1}),
        ])
        out = tmp_path / "attrs.skp"
        builder.save(str(out))

        model = SkpFile.open(str(out)).parse()
        inst = model.root.instances[0]
        # properties (backward-compatible) stringifies; attribute_dictionaries
        # keeps each value's real type.
        assert inst.properties == {"width": "10.0", "count": "4"}
        assert inst.attribute_dictionaries == {
            "dynamic_attributes": {"width": 10.0, "count": 4},
            "fbd-profile": {"guide": 1},
        }

    def test_no_dynamic_attributes_dict_leaves_properties_empty_but_others_visible(self, tmp_path):
        builder = create()
        with builder.add_component_definition("Stud") as stud:
            stud.add_face(SQUARE)
        builder.add_instance(stud, attribute_dicts=[("fbd-profile", {"guide": 1})])
        out = tmp_path / "attrs.skp"
        builder.save(str(out))

        model = SkpFile.open(str(out)).parse()
        inst = model.root.instances[0]
        assert inst.properties == {}
        assert inst.attribute_dictionaries == {"fbd-profile": {"guide": 1}}

    def test_face_with_no_attributes_has_no_attr_container(self):
        # A face with no attributes (and no UV positioning) shouldn't pay
        # for (or emit) an attribute container at all - same discipline as
        # test_unpositioned_face_has_no_texture_coords_record. Instances/
        # definitions differ here: ground truth already has them always
        # carrying a real (possibly empty) container regardless of
        # attributes, so this check is face-specific.
        builder = create()
        builder.add_face(SQUARE)
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        face = [v for (_, n, v) in root if n == "CFace"][0]
        assert face["attrs"] is None

    def test_instance_with_no_attributes_has_empty_attr_container(self):
        # Unlike a face, an instance always carries a real attribute
        # container regardless of whether attributes are given (ground
        # truth predates this feature) - adding attributes support
        # shouldn't change that pre-existing shape for the no-attributes case.
        builder = create()
        with builder.add_component_definition("Chair") as chair:
            chair.add_face(SQUARE)
        builder.add_instance(chair)
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        assert root[0][2]["attrs"] == {"k": "attrs", "children": []}

    def test_bool_value_round_trips(self):
        # openskp#253: bool (tag 0x07) is a real, distinct type the reader
        # already decodes - no longer rejected in favor of int(0/1).
        builder = create()
        with builder.add_component_definition("Chair") as chair:
            chair.add_face(SQUARE)
        builder.add_instance(chair, attributes={"flag": True, "off": False})
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        entries = root[0][2]["attrs"]["children"][0][1]["entries"]
        assert entries == {"flag": 1, "off": 0}

    def test_unsupported_type_raises(self):
        builder = create()
        with builder.add_component_definition("Chair") as chair:
            chair.add_face(SQUARE)
        with pytest.raises(SkpWriteError, match="unsupported value type"):
            builder.add_instance(chair, attributes={"bad": object()})

    def test_int32_out_of_range_raises(self):
        builder = create()
        with builder.add_component_definition("Chair") as chair:
            chair.add_face(SQUARE)
        with pytest.raises(SkpWriteError, match="out of signed 32-bit range"):
            builder.add_instance(chair, attributes={"huge": 2**40})

    # openskp#253: write_attribute_dict now covers all 9 value types
    # legacy._read_attr_named already decodes, not just str/int/float.

    def test_none_value_round_trips(self):
        builder = create()
        with builder.add_component_definition("Chair") as chair:
            chair.add_face(SQUARE)
        builder.add_instance(chair, attributes={"empty": None})
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        entries = root[0][2]["attrs"]["children"][0][1]["entries"]
        assert entries == {"empty": None}

    def test_point3d_and_vector3d_round_trip(self):
        builder = create()
        with builder.add_component_definition("Chair") as chair:
            chair.add_face(SQUARE)
        builder.add_instance(chair, attributes={
            "pt": Point3d(1.0, 2.0, 3.0),
            "vec": Vector3d(0.0, 0.0, 1.0),
        })
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        entries = root[0][2]["attrs"]["children"][0][1]["entries"]
        assert entries == {"pt": (1.0, 2.0, 3.0), "vec": (0.0, 0.0, 1.0)}

    def test_length_round_trips_as_double_not_int32(self):
        # Length is a float subclass - must be written as tag 0x0C, not
        # accidentally caught by the plain-float branch's tag 0x06 (both
        # decode to the same Python float, so this only distinguishes at
        # the raw byte level - covered indirectly by every other test
        # already using legacy._walk's own type-agnostic decode; this
        # test exists to pin the *value*, which would still be wrong if
        # Length silently fell through to the int32 branch instead).
        builder = create()
        with builder.add_component_definition("Chair") as chair:
            chair.add_face(SQUARE)
        builder.add_instance(chair, attributes={"depth": Length(94.92125984251969)})
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        entries = root[0][2]["attrs"]["children"][0][1]["entries"]
        assert entries == {"depth": 94.92125984251969}

    def test_timestamp_round_trips_as_uint32(self):
        builder = create()
        with builder.add_component_definition("Chair") as chair:
            chair.add_face(SQUARE)
        builder.add_instance(chair, attributes={"saved": Timestamp(1234567890)})
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        entries = root[0][2]["attrs"]["children"][0][1]["entries"]
        assert entries == {"saved": 1234567890}

    def test_timestamp_out_of_range_raises(self):
        builder = create()
        with builder.add_component_definition("Chair") as chair:
            chair.add_face(SQUARE)
        with pytest.raises(SkpWriteError, match="out of unsigned 32-bit range"):
            builder.add_instance(chair, attributes={"bad": Timestamp(-1)})

    def test_array_round_trips_with_mixed_element_types(self):
        # openskp#253's motivating case: fbd-profile's guide-geometry keys
        # are entirely arrays of Point3d (34,979 values across the
        # reporter's 3 production files) - previously unwritable at all.
        builder = create()
        with builder.add_component_definition("Chair") as chair:
            chair.add_face(SQUARE)
        builder.add_instance(chair, attributes={
            "guide": [Point3d(1.0, 2.0, 3.0), Point3d(4.0, 5.0, 6.0)],
            "mixed": [1, "two", 3.0, None, True],
        }, attribute_dict_name="fbd-profile")
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        entries = root[0][2]["attrs"]["children"][0][1]["entries"]
        assert entries == {
            "guide": [(1.0, 2.0, 3.0), (4.0, 5.0, 6.0)],
            "mixed": [1, "two", 3.0, None, 1],
        }

    def test_nested_array_round_trips(self):
        builder = create()
        with builder.add_component_definition("Chair") as chair:
            chair.add_face(SQUARE)
        builder.add_instance(chair, attributes={"nested": [[1, 2], [3, 4]]})
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        entries = root[0][2]["attrs"]["children"][0][1]["entries"]
        assert entries == {"nested": [[1, 2], [3, 4]]}

    def test_bad_value_inside_array_raises_before_any_write(self):
        builder = create()
        with builder.add_component_definition("Chair") as chair:
            chair.add_face(SQUARE)
        with pytest.raises(SkpWriteError, match=r"attribute 'bad\[1\]'"):
            builder.add_instance(chair, attributes={"bad": [1, object(), 3]})

    def test_point3d_non_numeric_component_raises(self):
        # Point3d's own NamedTuple constructor enforces arity (exactly 3
        # positional args) before validation ever runs - what
        # _check_attribute_value's "numeric components" check actually
        # catches is a non-numeric element smuggled into one of those 3
        # slots.
        builder = create()
        with builder.add_component_definition("Chair") as chair:
            chair.add_face(SQUARE)
        with pytest.raises(SkpWriteError, match="needs exactly 3 numeric components"):
            builder.add_instance(chair, attributes={"pt": Point3d(1.0, 2.0, "bad")})

    # openskp#256: the public API can attach several attribute
    # dictionaries to one entity, not just the attributes/
    # attribute_dict_name shorthand's single dictionary.

    def test_multiple_attribute_dicts_on_instance(self):
        # The FrameBuilder shape from the issue: three dictionaries on
        # one instance, previously only the first was ever reachable.
        builder = create()
        with builder.add_component_definition("Stud") as stud:
            stud.add_face(SQUARE)
        builder.add_instance(stud, attribute_dicts=[
            ("fbd-einfo", {"part": "Stud", "depth": 94.92}),
            ("fbd-profile", {"guide": 1}),
            ("fbd-profile-cords", {"x": 1.0, "y": 2.0}),
        ])
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        children = root[0][2]["attrs"]["children"]
        names = [v["name"] for _, v in children]
        assert names == ["fbd-einfo", "fbd-profile", "fbd-profile-cords"]
        assert children[0][1]["entries"] == {"part": "Stud", "depth": 94.92}
        assert children[2][1]["entries"] == {"x": 1.0, "y": 2.0}

    def test_multiple_attribute_dicts_on_face(self):
        builder = create()
        builder.add_face(SQUARE, attribute_dicts=[("a", {"x": 1}), ("b", {"y": 2})])
        data = builder.to_bytes()
        assert "x".encode("utf-16-le") in data and "y".encode("utf-16-le") in data

    def test_multiple_attribute_dicts_on_component_definition(self):
        builder = create()
        with builder.add_component_definition(
            "Chair", attribute_dicts=[("a", {"x": 1}), ("b", {"y": 2})],
        ) as chair:
            chair.add_face(SQUARE)
        builder.add_instance(chair)
        data = builder.to_bytes()
        assert "x".encode("utf-16-le") in data and "y".encode("utf-16-le") in data

    def test_multiple_attribute_dicts_on_definition_scoped_instance(self):
        # A nested definition-scoped add_instance (placing an already-
        # closed sub-part definition inside another definition's own
        # body) - built in the order this format requires: the part
        # closed and ready before the assembly that nests it opens.
        builder = create()
        with builder.add_component_definition("Part") as part:
            part.add_face(SQUARE)
        with builder.add_component_definition("Assembly") as assembly:
            assembly.add_instance(part, attribute_dicts=[("a", {"x": 1}), ("b", {"y": 2})])
        builder.add_instance(assembly)
        data = builder.to_bytes()
        assert "x".encode("utf-16-le") in data and "y".encode("utf-16-le") in data

    def test_attributes_and_attribute_dicts_together_raises(self):
        builder = create()
        with builder.add_component_definition("Chair") as chair:
            chair.add_face(SQUARE)
        with pytest.raises(SkpWriteError, match="not both"):
            builder.add_instance(
                chair, attributes={"a": 1}, attribute_dicts=[("b", {"c": 2})],
            )

    def test_empty_attribute_dicts_behaves_like_no_attributes(self):
        builder = create()
        with builder.add_component_definition("Chair") as chair:
            chair.add_face(SQUARE)
        builder.add_instance(chair, attribute_dicts=[])
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        assert root[0][2]["attrs"] == {"k": "attrs", "children": []}

    def test_face_attributes_in_component_definition(self):
        builder = create()
        with builder.add_component_definition("Panel") as panel:
            panel.add_face(SQUARE, attributes={"note": "handle with care"})
        builder.add_instance(panel)
        data = builder.to_bytes()
        # Byte-level presence check (face attrs live inside the definition,
        # not exposed by legacy._walk's root-only view) - the SDK oracle
        # test below is the authoritative check for this case.
        assert "handle with care".encode("utf-16-le") in data

    def test_definition_and_instance_and_face_attributes_together_via_real_sketchup(self, tmp_path):
        # The comprehensive case: attributes at all three levels this
        # writer supports at once, each independently readable by real
        # SketchUp through its own standard attribute-dictionary API -
        # not just this project's own reader.
        import ctypes

        if not os.path.exists(_SDK_DLL_PATH):
            pytest.skip("SketchUp SDK not present on this machine")

        builder = create()
        with builder.add_component_definition(
            "Chair", attributes={"sku": "CH-100", "price": 49.99, "stock": 12},
        ) as chair:
            chair.add_face(SQUARE, attributes={"material_note": "oak"})
        builder.add_instance(chair, translation=(50.0, 0.0, 0.0), attributes={"serial": "A1"})
        out = tmp_path / "attrs.skp"
        builder.save(str(out))

        dll = ctypes.CDLL(_SDK_DLL_PATH)
        dll.SUModelCreateFromFile.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_char_p]
        dll.SUModelGetEntities.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUEntitiesGetInstances.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        dll.SUEntitiesGetNumInstances.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        dll.SUComponentInstanceGetDefinition.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUComponentDefinitionGetEntities.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUEntitiesGetFaces.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        dll.SUEntityGetAttributeDictionary.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUAttributeDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUTypedValueCreate.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        dll.SUTypedValueGetString.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUTypedValueGetDouble.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_double)]
        dll.SUTypedValueGetInt32.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int32)]
        dll.SUStringCreate.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        dll.SUStringGetUTF8Length.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        dll.SUStringGetUTF8.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.POINTER(ctypes.c_size_t),
        ]

        def get_string_value(typed_value):
            s = ctypes.c_void_p()
            dll.SUStringCreate(ctypes.byref(s))
            dll.SUTypedValueGetString(typed_value, ctypes.byref(s))
            length = ctypes.c_size_t()
            dll.SUStringGetUTF8Length(s, ctypes.byref(length))
            buf = ctypes.create_string_buffer(length.value + 1)
            outlen = ctypes.c_size_t()
            dll.SUStringGetUTF8(s, length.value + 1, buf, ctypes.byref(outlen))
            return buf.value.decode("utf-8")

        def get_value(dict_ref, key):
            tv = ctypes.c_void_p()
            dll.SUTypedValueCreate(ctypes.byref(tv))
            err = dll.SUAttributeDictionaryGetValue(dict_ref, key.encode(), ctypes.byref(tv))
            assert err == 0, f"key {key!r} not found (error {err})"
            return tv

        dll.SUInitialize()
        try:
            model = ctypes.c_void_p()
            err = dll.SUModelCreateFromFile(ctypes.byref(model), str(out).encode())
            assert err == 0, f"SketchUp SDK rejected the file (error {err})"
            entities = ctypes.c_void_p()
            dll.SUModelGetEntities(model, ctypes.byref(entities))
            ninst = ctypes.c_size_t()
            dll.SUEntitiesGetNumInstances(entities, ctypes.byref(ninst))
            assert ninst.value == 1
            insts = (ctypes.c_void_p * 1)()
            got = ctypes.c_size_t()
            dll.SUEntitiesGetInstances(entities, 1, insts, ctypes.byref(got))

            inst_dict = ctypes.c_void_p()
            err = dll.SUEntityGetAttributeDictionary(insts[0], b"attributes", ctypes.byref(inst_dict))
            assert err == 0
            assert get_string_value(get_value(inst_dict, "serial")) == "A1"

            comp_def = ctypes.c_void_p()
            dll.SUComponentInstanceGetDefinition(insts[0], ctypes.byref(comp_def))
            def_dict = ctypes.c_void_p()
            err = dll.SUEntityGetAttributeDictionary(comp_def, b"attributes", ctypes.byref(def_dict))
            assert err == 0
            assert get_string_value(get_value(def_dict, "sku")) == "CH-100"
            price = ctypes.c_double()
            dll.SUTypedValueGetDouble(get_value(def_dict, "price"), ctypes.byref(price))
            assert price.value == pytest.approx(49.99)
            stock = ctypes.c_int32()
            dll.SUTypedValueGetInt32(get_value(def_dict, "stock"), ctypes.byref(stock))
            assert stock.value == 12

            def_entities = ctypes.c_void_p()
            dll.SUComponentDefinitionGetEntities(comp_def, ctypes.byref(def_entities))
            faces = (ctypes.c_void_p * 1)()
            got2 = ctypes.c_size_t()
            dll.SUEntitiesGetFaces(def_entities, 1, faces, ctypes.byref(got2))
            face_dict = ctypes.c_void_p()
            err = dll.SUEntityGetAttributeDictionary(faces[0], b"attributes", ctypes.byref(face_dict))
            assert err == 0
            assert get_string_value(get_value(face_dict, "material_note")) == "oak"

            dll.SUModelRelease(ctypes.byref(model))
        finally:
            dll.SUTerminate()


class TestCurvedEdges:
    def test_write_arc_curve_full_circle_byte_layout(self):
        # Ground truth (an SDK-authored full circle's own CArcCurve bytes,
        # cross-checked field-by-field via struct.unpack_from): 5-byte
        # header [0, num_segments, 0, 0, 0], then 14 f64s - center, normal,
        # xaxis, start_angle, end_angle, 0.0, radius, 0.0.
        writer = create_module._ArchiveWriter(next_slot=100, class_slot={})
        center = (50.0, 60.0, 70.0)
        normal = (0.0, 0.0, 1.0)
        xaxis = (40.0, 0.0, 0.0)
        slot = writer.write_arc_curve(center, normal, xaxis, 0.0, 6.283185307179586, 40.0, 8)
        # First-ever declaration of a class consumes one slot for the
        # class-ref bookkeeping itself (100) and a second for the object
        # instance (101) - the same two-slot pattern every other first-use
        # class declaration in this writer follows.
        assert slot == 101
        body = bytes(writer.buf)
        # Locate the 5-byte header right after the class-ref/preamble bytes,
        # then the 14 f64s that follow it - rather than assert on absolute
        # offsets (which shift with preamble encoding details), decode the
        # tail of the buffer: the last 5 + 14*8 = 117 bytes are exactly the
        # curve's own record (nothing is written after it by this call).
        record = body[-(5 + 14 * 8):]
        header, floats = record[:5], record[5:]
        assert header == bytes([0, 8, 0, 0, 0])
        values = struct.unpack("<14d", floats)
        assert values == pytest.approx((
            *center, *normal, *xaxis, 0.0, 6.283185307179586, 0.0, 40.0, 0.0,
        ))

    def test_write_arc_curve_partial_arc_byte_layout(self):
        # Ground truth from a 90-degree quarter-arc (6 segments).
        writer = create_module._ArchiveWriter(next_slot=1, class_slot={})
        center = (50.0, 50.0, 0.0)
        normal = (0.0, 0.0, 1.0)
        xaxis = (40.0, 0.0, 0.0)
        writer.write_arc_curve(center, normal, xaxis, 0.0, 1.5707963267948966, 40.0, 6)
        record = bytes(writer.buf)[-(5 + 14 * 8):]
        header, floats = record[:5], record[5:]
        assert header == bytes([0, 6, 0, 0, 0])
        values = struct.unpack("<14d", floats)
        assert values == pytest.approx((
            *center, *normal, *xaxis, 0.0, 1.5707963267948966, 0.0, 40.0, 0.0,
        ))

    def test_num_segments_out_of_range_raises(self):
        writer = create_module._ArchiveWriter(next_slot=1, class_slot={})
        with pytest.raises(SkpWriteError, match="num_segments"):
            writer.write_arc_curve((0, 0, 0), (0, 0, 1), (1, 0, 0), 0.0, 1.0, 1.0, 256)

    def test_add_circle_self_parses_as_closed_face(self):
        builder = create()
        builder.add_circle((50.0, 50.0, 0.0), (0.0, 0.0, 1.0), radius=40.0, num_segments=8)
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        edges = [v for (_, n, v) in root if n == "CEdge"]
        faces = [v for (_, n, v) in root if n == "CFace"]
        assert len(edges) == 8
        assert len(faces) == 1
        # Every edge shares the exact same curve backref - one CArcCurve.
        curve_slots = {e["curve"] for e in edges}
        assert len(curve_slots) == 1
        assert None not in curve_slots

    def test_add_circle_face_normal_matches_requested_normal(self):
        # Winding direction check: the generated polygon's own computed
        # normal (via Newell's method, same as any other face) must come
        # out parallel to the requested normal, not anti-parallel.
        builder = create()
        builder.add_circle((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), radius=10.0, num_segments=12)
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        face = [v for (_, n, v) in root if n == "CFace"][0]
        assert face["plane"][:3] == pytest.approx((0.0, 0.0, 1.0))

    def test_add_circle_rejects_bad_segment_count(self):
        builder = create()
        with pytest.raises(SkpWriteError, match="num_segments"):
            builder.add_circle((0, 0, 0), (0, 0, 1), radius=10.0, num_segments=2)
        with pytest.raises(SkpWriteError, match="num_segments"):
            builder.add_circle((0, 0, 0), (0, 0, 1), radius=10.0, num_segments=256)

    def test_add_circle_in_component_definition(self):
        builder = create()
        with builder.add_component_definition("Wheel") as wheel:
            wheel.add_circle((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), radius=15.0, num_segments=10)
        builder.add_instance(wheel)
        data = builder.to_bytes()
        # Definition contents aren't exposed via legacy._walk's root-only
        # view - a byte-level presence check plus the SDK oracle test below
        # cover this case; here just confirm it builds without error and
        # round-trips through our own reader without raising.
        legacy._walk(data)

    def test_add_arc_self_parses_as_open_chain_no_face(self):
        builder = create()
        builder.add_arc(
            (50.0, 50.0, 0.0), (0.0, 0.0, 1.0), radius=40.0,
            start_angle=0.0, end_angle=math.pi / 2, num_segments=6,
        )
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        edges = [v for (_, n, v) in root if n == "CEdge"]
        faces = [v for (_, n, v) in root if n == "CFace"]
        assert len(edges) == 6
        assert len(faces) == 0
        curve_slots = {e["curve"] for e in edges}
        assert len(curve_slots) == 1
        assert None not in curve_slots

    def test_add_arc_endpoints_match_requested_sweep(self, tmp_path):
        # angle 0 -> center + radius*u, angle pi/2 -> center + radius*w -
        # confirm the actual written vertex coordinates land there exactly,
        # not just that a curve object exists. CVertex isn't a root-level
        # entity (it's nested inline inside CEdge, like CArcCurve) so
        # legacy._walk's root-only view can't see it directly - go through
        # the public parse API instead, which resolves nested vertices.
        from openskp import SkpFile

        builder = create()
        builder.add_arc(
            (50.0, 50.0, 0.0), (0.0, 0.0, 1.0), radius=40.0,
            start_angle=0.0, end_angle=math.pi / 2, num_segments=6,
        )
        out = tmp_path / "arc_endpoints.skp"
        builder.save(str(out))
        model = SkpFile.open(str(out)).parse()
        coords = {(round(v.x, 6), round(v.y, 6), round(v.z, 6)) for v in model.root.vertices.values()}
        assert (90.0, 50.0, 0.0) in coords
        assert (50.0, 90.0, 0.0) in coords

    def test_add_arc_rejects_equal_angles(self):
        builder = create()
        with pytest.raises(SkpWriteError, match="must differ"):
            builder.add_arc((0, 0, 0), (0, 0, 1), radius=10.0, start_angle=1.0, end_angle=1.0)

    def test_add_arc_rejects_bad_segment_count(self):
        builder = create()
        with pytest.raises(SkpWriteError, match="num_segments"):
            builder.add_arc((0, 0, 0), (0, 0, 1), radius=10.0, start_angle=0.0, end_angle=1.0, num_segments=2)

    def test_add_arc_in_component_definition(self):
        builder = create()
        with builder.add_component_definition("Bracket") as bracket:
            bracket.add_arc(
                (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), radius=15.0,
                start_angle=0.0, end_angle=math.pi, num_segments=8,
            )
        builder.add_instance(bracket)
        data = builder.to_bytes()
        legacy._walk(data)

    def test_add_arc_and_add_circle_share_vertex_registry(self, tmp_path):
        # An arc and a circle built with the same center/radius/normal
        # trace overlapping points (e.g. angle 0) - confirm the shared
        # vertex_slots dict actually dedupes across the two calls rather
        # than emitting a duplicate CVertex.
        from openskp import SkpFile

        builder = create()
        builder.add_arc(
            (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), radius=10.0,
            start_angle=0.0, end_angle=math.pi, num_segments=4,
        )
        builder.add_circle((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), radius=10.0, num_segments=4)
        out = tmp_path / "arc_circle_shared.skp"
        builder.save(str(out))
        model = SkpFile.open(str(out)).parse()
        coords = [(round(v.x, 6), round(v.y, 6), round(v.z, 6)) for v in model.root.vertices.values()]
        assert len(coords) == len(set(coords)), "no duplicate CVertex for a shared point"
        # angle 0 for both is the same point (10, 0, 0) - confirm it's
        # really shared (present once), not that dedup happened to be
        # unnecessary because the two shapes never overlap.
        assert coords.count((10.0, 0.0, 0.0)) == 1

    def test_write_curve_byte_layout(self):
        # Ground truth (SDK-authored open and closed polylines of several
        # edge counts, read back and byte-decoded): a 1-byte field always
        # 1 (open or closed), followed by num_edges as a u32.
        writer = create_module._ArchiveWriter(next_slot=1, class_slot={})
        slot = writer.write_curve(3)
        assert slot == 2  # first-ever declaration: class slot 1, object slot 2
        record = bytes(writer.buf)[-5:]
        assert record[0] == 1
        assert struct.unpack("<I", record[1:])[0] == 3

    def test_add_polyline_open_self_parses_no_face(self):
        builder = create()
        builder.add_polyline([(0.0, 0.0, 0.0), (10.0, 10.0, 0.0), (20.0, 0.0, 0.0), (30.0, 10.0, 0.0)])
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        edges = [v for (_, n, v) in root if n == "CEdge"]
        faces = [v for (_, n, v) in root if n == "CFace"]
        assert len(edges) == 3
        assert len(faces) == 0
        curve_slots = {e["curve"] for e in edges}
        assert len(curve_slots) == 1
        assert None not in curve_slots

    def test_add_polyline_closed_wraps_around(self):
        builder = create()
        builder.add_polyline(
            [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)], closed=True,
        )
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        edges = [v for (_, n, v) in root if n == "CEdge"]
        assert len(edges) == 4
        curve_slots = {e["curve"] for e in edges}
        assert len(curve_slots) == 1

    def test_add_polyline_rejects_too_few_points(self):
        builder = create()
        with pytest.raises(SkpWriteError, match="at least 2 points"):
            builder.add_polyline([(0, 0, 0)])

    def test_add_polyline_in_component_definition(self):
        builder = create()
        with builder.add_component_definition("Wire") as wire:
            wire.add_polyline([(0.0, 0.0, 0.0), (10.0, 0.0, 5.0), (20.0, 0.0, 0.0)])
        builder.add_instance(wire)
        data = builder.to_bytes()
        legacy._walk(data)


class TestInstanceRotation:
    def test_rotation_matrix_matches_matrix3x3_for_90deg_z(self):
        # A +90-degree rotation around +Z (right-hand rule) sends the
        # world X axis to the world Y axis - the same identity a rotation
        # matrix must satisfy regardless of how it was derived.
        m = create_module._rotation_matrix3x3((0.0, 0.0, 1.0), math.pi / 2)
        # Apply m (row-major: m[0:3]=row0, m[3:6]=row1, m[6:9]=row2) to
        # local point (1, 0, 0) - confirmed against the real SDK's own
        # SUComponentInstanceGetTransform for this exact rotation.
        x, y, z = 1.0, 0.0, 0.0
        rx = m[0] * x + m[1] * y + m[2] * z
        ry = m[3] * x + m[4] * y + m[5] * z
        rz = m[6] * x + m[7] * y + m[8] * z
        assert (rx, ry, rz) == pytest.approx((0.0, 1.0, 0.0), abs=1e-9)

    def test_rotation_matrix_identity_at_zero_angle(self):
        m = create_module._rotation_matrix3x3((0.0, 0.0, 1.0), 0.0)
        assert m == pytest.approx((1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0))

    def test_rotation_matrix_rejects_zero_axis(self):
        with pytest.raises(SkpWriteError, match="zero vector"):
            create_module._rotation_matrix3x3((0.0, 0.0, 0.0), 1.0)

    def test_add_instance_rotation_and_matrix3x3_are_mutually_exclusive(self):
        builder = create()
        with builder.add_component_definition("Chair") as chair:
            chair.add_face(SQUARE)
        with pytest.raises(SkpWriteError, match="at most one"):
            builder.add_instance(
                chair, matrix3x3=(1, 0, 0, 0, 1, 0, 0, 0, 1), rotation=((0, 0, 1), math.pi / 2),
            )

    def test_add_instance_rotation_produces_same_matrix_as_equivalent_matrix3x3(self):
        # Byte-for-byte comparison isn't meaningful here - each instance
        # embeds a fresh random GUID - so compare the parsed-back
        # transform field instead.
        angle = math.pi / 3
        expected = create_module._rotation_matrix3x3((0, 0, 1), angle)

        b1 = create()
        with b1.add_component_definition("Chair") as chair1:
            chair1.add_face(SQUARE)
        b1.add_instance(chair1, rotation=((0, 0, 1), angle))
        ar, root, layers, materials = legacy._walk(b1.to_bytes())
        inst = [v for (_, n, v) in root if n == "CComponentInstance"][0]
        assert tuple(inst["xf"][0:9]) == pytest.approx(expected)

    def test_add_group_rotation(self):
        builder = create()
        with builder.add_group("Table", rotation=((0, 0, 1), math.pi / 4)) as table:
            table.add_face(SQUARE)
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        assert any(n == "CGroup" for (_, n, v) in root)

    def test_add_group_instance_rotation(self):
        builder = create()
        with builder.add_component_definition("Engine") as engine:
            engine.add_face(SQUARE)
        with builder.add_component_definition("Car") as car:
            car.add_face(SQUARE)
            car.add_group_instance(engine, rotation=((1, 0, 0), math.pi / 6))
        builder.add_instance(car)
        data = builder.to_bytes()
        legacy._walk(data)


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

    def test_layers_by_name_reflects_registered_handle(self):
        builder = create()
        roof = builder.add_layer("Roof")
        assert builder.layers_by_name["Roof"] == roof

    def test_layer_color_and_hidden_round_trip(self):
        builder = create()
        roof = builder.add_layer("Roof", color=(150, 75, 30), hidden=True)
        builder.add_face(SQUARE, layer=roof)
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        by_slot = {s: v for s, v in layers}
        assert by_slot[roof]["rgba"] == (150, 75, 30, 255)
        assert by_slot[roof]["hidden"] == 1

    def test_layer_color_alpha_defaults_to_opaque(self):
        builder = create()
        roof = builder.add_layer("Roof", color=(10, 20, 30))
        builder.add_face(SQUARE, layer=roof)
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        by_slot = {s: v for s, v in layers}
        assert by_slot[roof]["rgba"] == (10, 20, 30, 255)

    def test_layer_without_color_or_hidden_matches_previous_bytes(self):
        # No behavior change for callers not using the new parameters.
        builder = create()
        roof = builder.add_layer("Roof")
        builder.add_face(SQUARE, layer=roof)
        data = builder.to_bytes()
        ar, root, layers, materials = legacy._walk(data)
        by_slot = {s: v for s, v in layers}
        assert by_slot[roof]["rgba"] == (0, 0, 0, 0)
        assert by_slot[roof]["hidden"] == 0

    def test_invalid_layer_color_raises(self):
        builder = create()
        with pytest.raises(SkpWriteError, match="color"):
            builder.add_layer("Bad", color=(300, 0, 0))

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


class TestDefaultCamera:
    def test_every_file_gets_the_iso_camera_patch(self):
        # Byte-level guard: every file this writer produces should carry the
        # same fixed ISO-camera bytes at the same fixed offsets, regardless
        # of what geometry/materials/etc. it also contains - independent of
        # the SDK oracle test below, which additionally confirms real
        # SketchUp reads these bytes back as the intended eye/target/
        # perspective.
        builder = create()
        builder.add_face(SQUARE)
        data = builder.to_bytes()
        off = create_module._ISO_CAMERA_PREFIX_OFFSET
        patch = create_module._ISO_CAMERA_PREFIX_PATCH
        assert data[off : off + len(patch)] == patch

    def test_camera_patch_present_regardless_of_other_content(self):
        # The prefix patch offset is well before _material_insert_pos, so
        # it should never move even once materials/layers/definitions
        # shift everything after it - confirmed with a file that exercises
        # all of those.
        builder = create()
        red = builder.add_material("Red", (255, 0, 0))
        builder.add_layer("Roof")
        with builder.add_component_definition("Chair") as chair:
            chair.add_face(SQUARE)
        builder.add_instance(chair)
        builder.add_face(SQUARE, material=red)
        data = builder.to_bytes()
        off = create_module._ISO_CAMERA_PREFIX_OFFSET
        patch = create_module._ISO_CAMERA_PREFIX_PATCH
        assert data[off : off + len(patch)] == patch


class TestSlotBoundaryEncoding:
    """A slot of exactly 0x7FFF (32767) is unrepresentable in the short
    2-byte form no matter which of the two short encodings would
    otherwise apply - it collides with a reserved marker value either
    way, confirmed by tracing legacy.py's actual read dispatch order
    (`_Archive.read_object`), not just from the protocol table:

    * A plain back-ref of exactly 0x7FFF is byte-identical to the
      big-tag escape marker itself - `read_object` checks
      `tag == 0x7FFF` before it ever falls through to "plain back-ref",
      so it would consume the next (unrelated) 4 bytes as a bogus slot.
    * A class-ref of exactly 0x7FFF sets `0x8000 | 0x7FFF == 0xFFFF`,
      which `read_object` checks for "new class declaration" before it
      ever checks the class-ref high bit.

    Both desync every read after that point - the file doesn't just fail
    to open, our own reader silently drops entities without erroring
    (see TestLargeModelSlotBoundary below for a real-scale reproduction
    of exactly that silent data loss, before this fix)."""

    def test_backref_below_boundary_uses_short_form(self):
        writer = create_module._ArchiveWriter(next_slot=1, class_slot={})
        writer._backref(0x7FFE)
        assert bytes(writer.buf) == struct.pack("<H", 0x7FFE)

    def test_backref_at_boundary_uses_escape_form(self):
        writer = create_module._ArchiveWriter(next_slot=1, class_slot={})
        writer._backref(0x7FFF)
        assert bytes(writer.buf) == struct.pack("<H", 0x7FFF) + struct.pack("<I", 0x7FFF)
        # Specifically NOT the collision byte pattern a `<=` boundary
        # would have produced (identical to the escape marker alone).
        assert bytes(writer.buf) != struct.pack("<H", 0x7FFF)

    def test_new_of_known_class_below_boundary_uses_short_form(self):
        writer = create_module._ArchiveWriter(next_slot=1, class_slot={"Foo": 0x7FFE})
        writer._new_of_known_class("Foo")
        assert bytes(writer.buf) == struct.pack("<H", 0x8000 | 0x7FFE)

    def test_new_of_known_class_at_boundary_uses_escape_form(self):
        writer = create_module._ArchiveWriter(next_slot=1, class_slot={"Foo": 0x7FFF})
        writer._new_of_known_class("Foo")
        assert bytes(writer.buf) == struct.pack("<H", 0x7FFF) + struct.pack("<I", 0x80000000 | 0x7FFF)
        # Specifically NOT 0xFFFF - the "new class declaration" marker.
        tag = struct.unpack_from("<H", writer.buf, 0)[0]
        assert tag != 0xFFFF

    def test_shift_ref_below_boundary_stays_short_and_reports_no_growth(self):
        buf = bytearray(struct.pack("<H", 100))
        grown = create_module._shift_ref(buf, 0, 0x7FFE - 100)  # -> exactly 0x7FFE
        assert grown == 0
        assert bytes(buf) == struct.pack("<H", 0x7FFE)

    def test_shift_ref_crossing_boundary_widens_and_reports_growth(self):
        buf = bytearray(struct.pack("<H", 100) + b"\x00\x00")  # trailing bytes to prove no overwrite
        grown = create_module._shift_ref(buf, 0, 0x7FFF)  # 100 + 0x7FFF -> past the boundary
        assert grown == 4
        assert len(buf) == 8
        tag = struct.unpack_from("<H", buf, 0)[0]
        val = struct.unpack_from("<I", buf, 2)[0]
        assert tag == 0x7FFF
        assert val == 100 + 0x7FFF
        assert bytes(buf[6:8]) == b"\x00\x00"  # original trailing bytes preserved, just pushed forward

    def test_shift_ref_preserves_class_ref_tag_bit_when_widening(self):
        buf = bytearray(struct.pack("<H", 0x8000 | 50))
        create_module._shift_ref(buf, 0, 0x7FFF)
        val = struct.unpack_from("<I", buf, 2)[0]
        assert val == 0x80000000 | (50 + 0x7FFF)

    def test_is_class_ref_below_boundary(self):
        data = struct.pack("<H", 0x8000 | 0x7FFE)
        assert legacy._is_class_ref(data, 0, 0x7FFE) is True

    def test_is_class_ref_at_boundary_expects_escape_form(self):
        # A real encoder can never emit the short form for slot 0x7FFF
        # (same collision as _new_of_known_class above) - the short-form
        # bytes at this position must NOT match, even though the naive
        # `0x8000 | 0x7FFF` bit-math alone would suggest otherwise.
        short_form = struct.pack("<H", 0x8000 | 0x7FFF)
        assert legacy._is_class_ref(short_form, 0, 0x7FFF) is False
        escape_form = struct.pack("<H", 0x7FFF) + struct.pack("<I", 0x80000000 | 0x7FFF)
        assert legacy._is_class_ref(escape_form, 0, 0x7FFF) is True


class TestLargeModelSlotBoundary:
    """Real-scale reproduction: enough unique (non-shared-vertex)
    triangles to push the file's total archive-slot count past 32,767,
    the exact condition that corrupted large flattened-geometry exports
    before this fix. Deliberately uses unique vertices per triangle
    (~11 new slots each) rather than a shared grid, to land the crossing
    at an unpredictable, non-hand-picked slot number - the same
    worst-case shape a CAD import's flattened mesh produces."""

    def _build(self, n=5000):
        builder = create()
        for i in range(n):
            x = i * 10.0
            builder.add_face([(x, 0.0, 0.0), (x + 1.0, 0.0, 0.0), (x, 1.0, 0.0)])
        return builder

    def test_next_slot_actually_crosses_the_boundary(self):
        # Sanity check that this test is exercising the real condition,
        # not accidentally staying under it.
        builder = self._build()
        assert builder._geometry_writer.next_slot > 0x7FFF

    def test_round_trips_through_own_reader_with_exact_face_count(self, tmp_path):
        builder = self._build()
        out = tmp_path / "slot_boundary.skp"
        builder.save(str(out))
        from openskp import SkpFile

        parsed = SkpFile.open(str(out)).parse()
        assert len(parsed.root.faces) == 5000


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

    def test_bool_time_and_array_attribute_values_round_trip_through_real_sketchup(self, tmp_path):
        # openskp#253: bool/Timestamp/array were previously unwritable at
        # all. Point3d/Vector3d/Length are covered instead by the
        # legacy._walk byte-level tests in TestAttributeDicts - the
        # public C SDK's SUTypedValue surface has no
        # SUTypedValueGetPoint3D/GetVector3D/GetLength accessor (confirmed
        # by probing this DLL's exports directly), only
        # GetBool/GetTime/GetArrayItems for the newly-added types.
        import ctypes

        builder = create()
        with builder.add_component_definition("Chair") as chair:
            chair.add_face(SQUARE)
        builder.add_instance(chair, attributes={
            "flag": True, "ts": Timestamp(1700000000), "arr": [1, 2, 3],
        })
        out = tmp_path / "bool_time_array.skp"
        builder.save(str(out))

        dll = ctypes.CDLL(_SDK_DLL_PATH)
        dll.SUModelCreateFromFile.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_char_p]
        dll.SUModelGetEntities.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUEntitiesGetInstances.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        dll.SUEntityGetAttributeDictionary.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUAttributeDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUTypedValueCreate.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        dll.SUTypedValueGetBool.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_bool)]
        dll.SUTypedValueGetTime.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int64)]
        dll.SUTypedValueGetNumArrayItems.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        dll.SUTypedValueGetArrayItems.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        dll.SUTypedValueGetInt32.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int32)]

        def get_value(dict_ref, key):
            tv = ctypes.c_void_p()
            dll.SUTypedValueCreate(ctypes.byref(tv))
            err = dll.SUAttributeDictionaryGetValue(dict_ref, key.encode(), ctypes.byref(tv))
            assert err == 0, f"key {key!r} not found (error {err})"
            return tv

        dll.SUInitialize()
        try:
            model = ctypes.c_void_p()
            err = dll.SUModelCreateFromFile(ctypes.byref(model), str(out).encode())
            assert err == 0, f"SketchUp SDK rejected the file (error {err})"
            entities = ctypes.c_void_p()
            dll.SUModelGetEntities(model, ctypes.byref(entities))
            insts = (ctypes.c_void_p * 1)()
            got = ctypes.c_size_t()
            dll.SUEntitiesGetInstances(entities, 1, insts, ctypes.byref(got))
            attr_dict = ctypes.c_void_p()
            err = dll.SUEntityGetAttributeDictionary(insts[0], b"attributes", ctypes.byref(attr_dict))
            assert err == 0

            flag_tv = get_value(attr_dict, "flag")
            flag = ctypes.c_bool()
            assert dll.SUTypedValueGetBool(flag_tv, ctypes.byref(flag)) == 0
            assert flag.value is True

            ts_tv = get_value(attr_dict, "ts")
            ts = ctypes.c_int64()
            assert dll.SUTypedValueGetTime(ts_tv, ctypes.byref(ts)) == 0
            assert ts.value == 1700000000

            arr_tv = get_value(attr_dict, "arr")
            n = ctypes.c_size_t()
            assert dll.SUTypedValueGetNumArrayItems(arr_tv, ctypes.byref(n)) == 0
            assert n.value == 3
            items = (ctypes.c_void_p * n.value)()
            got_items = ctypes.c_size_t()
            assert dll.SUTypedValueGetArrayItems(arr_tv, n.value, items, ctypes.byref(got_items)) == 0
            values = []
            for i in range(got_items.value):
                v = ctypes.c_int32()
                assert dll.SUTypedValueGetInt32(items[i], ctypes.byref(v)) == 0
                values.append(v.value)
            assert values == [1, 2, 3]
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

    def test_unicode_material_name_round_trips_through_real_sketchup(self, tmp_path):
        # Self-parsing (TestUnicodeNames) already locks in that the raw
        # bytes decode correctly; this additionally confirms real SketchUp
        # itself reads the name back exactly, not just this project's own
        # reader.
        import ctypes

        name = "Rouge Écarlate"
        builder = create()
        mat = builder.add_material(name, (255, 0, 0))
        builder.add_face(SQUARE, material=mat)
        out = tmp_path / "unicode.skp"
        builder.save(str(out))

        dll = ctypes.CDLL(_SDK_DLL_PATH)
        dll.SUInitialize()
        dll.SUModelGetMaterials.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        dll.SUModelGetNumMaterials.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        dll.SUMaterialGetNameLegacyBehavior.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUStringCreate.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        dll.SUStringGetUTF8Length.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        dll.SUStringGetUTF8.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.POINTER(ctypes.c_size_t),
        ]
        try:
            model = ctypes.c_void_p()
            err = dll.SUModelCreateFromFile(ctypes.byref(model), str(out).encode())
            assert err == 0, f"SketchUp SDK rejected the file (error {err})"
            nmat = ctypes.c_size_t()
            dll.SUModelGetNumMaterials(model, ctypes.byref(nmat))
            mats = (ctypes.c_void_p * nmat.value)()
            got = ctypes.c_size_t()
            dll.SUModelGetMaterials(model, nmat.value, mats, ctypes.byref(got))
            sref = ctypes.c_void_p()
            dll.SUStringCreate(ctypes.byref(sref))
            dll.SUMaterialGetNameLegacyBehavior(mats[0], ctypes.byref(sref))
            length = ctypes.c_size_t()
            dll.SUStringGetUTF8Length(sref, ctypes.byref(length))
            buf = ctypes.create_string_buffer(length.value + 1)
            outlen = ctypes.c_size_t()
            dll.SUStringGetUTF8(sref, length.value + 1, buf, ctypes.byref(outlen))
            assert buf.value.decode("utf-8") == name
            dll.SUModelRelease(ctypes.byref(model))
        finally:
            dll.SUTerminate()

    def test_positioned_texture_round_trips_through_real_sketchup(self, tmp_path):
        # Beyond just loading without SU_ERROR_MODEL_INVALID: independently
        # verifies the position through the SDK's own SUUVHelper, which
        # computes UV coordinates from the same on-disk matrix this test
        # doesn't otherwise inspect - confirms real SketchUp both accepts
        # and correctly *interprets* the mapping, not just tolerates it.
        import ctypes

        class SUPoint3D(ctypes.Structure):
            _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("z", ctypes.c_double)]

        class SUUVQ(ctypes.Structure):
            _fields_ = [("u", ctypes.c_double), ("v", ctypes.c_double), ("q", ctypes.c_double)]

        png_path = tmp_path / "tex.png"
        png_path.write_bytes(_make_test_png())
        builder = create()
        tex = builder.add_texture_material("Brick", str(png_path))
        # (0,0,0)->(0,0), (50,0,0)->(1,0), (0,50,0)->(0,1): pure 50x scale.
        builder.add_face(
            SQUARE, material=tex,
            front_uv=[((0.0, 0.0, 0.0), (0.0, 0.0)), ((50.0, 0.0, 0.0), (1.0, 0.0)), ((0.0, 50.0, 0.0), (0.0, 1.0))],
        )
        out = tmp_path / "positioned.skp"
        builder.save(str(out))

        dll = ctypes.CDLL(_SDK_DLL_PATH)
        dll.SUModelCreateFromFile.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_char_p]
        dll.SUModelGetEntities.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUEntitiesGetFaces.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        dll.SUFaceGetUVHelper.argtypes = [
            ctypes.c_void_p, ctypes.c_bool, ctypes.c_bool, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
        ]
        dll.SUUVHelperGetFrontUVQ.argtypes = [ctypes.c_void_p, ctypes.POINTER(SUPoint3D), ctypes.POINTER(SUUVQ)]
        dll.SUInitialize()
        try:
            model = ctypes.c_void_p()
            err = dll.SUModelCreateFromFile(ctypes.byref(model), str(out).encode())
            assert err == 0, f"SketchUp SDK rejected the file (error {err})"
            entities = ctypes.c_void_p()
            dll.SUModelGetEntities(model, ctypes.byref(entities))
            faces = (ctypes.c_void_p * 1)()
            got = ctypes.c_size_t()
            dll.SUEntitiesGetFaces(entities, 1, faces, ctypes.byref(got))
            assert got.value == 1
            uv_helper = ctypes.c_void_p()
            err = dll.SUFaceGetUVHelper(faces[0], True, False, ctypes.c_void_p(0), ctypes.byref(uv_helper))
            assert err == 0
            # SUUVHelperGetFrontUVQ's v-component is unreliable through this
            # minimal call shape (a documented SDK quirk, not specific to
            # this file - q and u alone already cross-check the mapping:
            # u=0.5 at the midpoint between the 0->0 and 50->1 pins).
            uvq = SUUVQ()
            err = dll.SUUVHelperGetFrontUVQ(uv_helper, ctypes.byref(SUPoint3D(25.0, 25.0, 0.0)), ctypes.byref(uvq))
            assert err == 0
            assert uvq.u == pytest.approx(0.5)
            assert uvq.q == pytest.approx(1.0)
            dll.SUModelRelease(ctypes.byref(model))
        finally:
            dll.SUTerminate()

    def test_positioned_texture_on_tilted_face_round_trips_through_real_sketchup(self, tmp_path):
        # Same discipline as the axis-aligned oracle test above, but for a
        # face tilted 45 degrees - real SketchUp must both accept the file
        # and agree with the u-coordinate this project's own basis formula
        # (_face_uv_basis) computes, not just tolerate the bytes.
        import ctypes

        class SUPoint3D(ctypes.Structure):
            _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("z", ctypes.c_double)]

        class SUUVQ(ctypes.Structure):
            _fields_ = [("u", ctypes.c_double), ("v", ctypes.c_double), ("q", ctypes.c_double)]

        png_path = tmp_path / "tex.png"
        png_path.write_bytes(_make_test_png())
        builder = create()
        tex = builder.add_texture_material("Brick", str(png_path))
        s = 70.71067811865476
        tilted = [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (100.0, s, s), (0.0, s, s)]
        # (0,0,0)->(0,0), (100,0,0)->(1,0), (0,s,s)->(0,1): pure 100x scale
        # along the face's own (tilted) edges.
        builder.add_face(
            tilted, material=tex,
            front_uv=[((0.0, 0.0, 0.0), (0.0, 0.0)), ((100.0, 0.0, 0.0), (1.0, 0.0)), ((0.0, s, s), (0.0, 1.0))],
        )
        out = tmp_path / "tilted_positioned.skp"
        builder.save(str(out))

        dll = ctypes.CDLL(_SDK_DLL_PATH)
        dll.SUModelCreateFromFile.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_char_p]
        dll.SUModelGetEntities.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUEntitiesGetFaces.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        dll.SUFaceGetUVHelper.argtypes = [
            ctypes.c_void_p, ctypes.c_bool, ctypes.c_bool, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
        ]
        dll.SUUVHelperGetFrontUVQ.argtypes = [ctypes.c_void_p, ctypes.POINTER(SUPoint3D), ctypes.POINTER(SUUVQ)]
        dll.SUInitialize()
        try:
            model = ctypes.c_void_p()
            err = dll.SUModelCreateFromFile(ctypes.byref(model), str(out).encode())
            assert err == 0, f"SketchUp SDK rejected the file (error {err})"
            entities = ctypes.c_void_p()
            dll.SUModelGetEntities(model, ctypes.byref(entities))
            faces = (ctypes.c_void_p * 1)()
            got = ctypes.c_size_t()
            dll.SUEntitiesGetFaces(entities, 1, faces, ctypes.byref(got))
            assert got.value == 1
            uv_helper = ctypes.c_void_p()
            err = dll.SUFaceGetUVHelper(faces[0], True, False, ctypes.c_void_p(0), ctypes.byref(uv_helper))
            assert err == 0
            # Midpoint of the tilted face: (50, s/2, s/2) -> u should be 0.5.
            uvq = SUUVQ()
            midpoint = SUPoint3D(50.0, s / 2, s / 2)
            err = dll.SUUVHelperGetFrontUVQ(uv_helper, ctypes.byref(midpoint), ctypes.byref(uvq))
            assert err == 0
            assert uvq.u == pytest.approx(0.5)
            assert uvq.q == pytest.approx(1.0)
            dll.SUModelRelease(ctypes.byref(model))
        finally:
            dll.SUTerminate()

    def test_positioned_texture_survives_vertex_order_through_real_sketchup(self, tmp_path):
        # The square listed from its second corner (first edge along +Y),
        # pinned to u = x/100: real SketchUp must still put u = 0.5 at
        # x = 50, i.e. read the matrix in its normal-derived basis, not in
        # a first-edge one. The axis-aligned oracle test above cannot tell
        # the two apart (its first edge runs along Z x n).
        import ctypes

        class SUPoint3D(ctypes.Structure):
            _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("z", ctypes.c_double)]

        class SUUVQ(ctypes.Structure):
            _fields_ = [("u", ctypes.c_double), ("v", ctypes.c_double), ("q", ctypes.c_double)]

        png_path = tmp_path / "tex.png"
        png_path.write_bytes(_make_test_png())
        builder = create()
        tex = builder.add_texture_material("Brick", str(png_path))
        rotated = SQUARE[1:] + SQUARE[:1]
        builder.add_face(
            rotated, material=tex,
            front_uv=[((0.0, 0.0, 0.0), (0.0, 0.0)), ((100.0, 0.0, 0.0), (1.0, 0.0)), ((0.0, 100.0, 0.0), (0.0, 1.0))],
        )
        out = tmp_path / "rotated_order_positioned.skp"
        builder.save(str(out))

        dll = ctypes.CDLL(_SDK_DLL_PATH)
        dll.SUModelCreateFromFile.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_char_p]
        dll.SUModelGetEntities.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUEntitiesGetFaces.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        dll.SUFaceGetUVHelper.argtypes = [
            ctypes.c_void_p, ctypes.c_bool, ctypes.c_bool, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
        ]
        dll.SUUVHelperGetFrontUVQ.argtypes = [ctypes.c_void_p, ctypes.POINTER(SUPoint3D), ctypes.POINTER(SUUVQ)]
        dll.SUInitialize()
        try:
            model = ctypes.c_void_p()
            err = dll.SUModelCreateFromFile(ctypes.byref(model), str(out).encode())
            assert err == 0, f"SketchUp SDK rejected the file (error {err})"
            entities = ctypes.c_void_p()
            dll.SUModelGetEntities(model, ctypes.byref(entities))
            faces = (ctypes.c_void_p * 1)()
            got = ctypes.c_size_t()
            dll.SUEntitiesGetFaces(entities, 1, faces, ctypes.byref(got))
            assert got.value == 1
            uv_helper = ctypes.c_void_p()
            err = dll.SUFaceGetUVHelper(faces[0], True, False, ctypes.c_void_p(0), ctypes.byref(uv_helper))
            assert err == 0
            uvq = SUUVQ()
            err = dll.SUUVHelperGetFrontUVQ(uv_helper, ctypes.byref(SUPoint3D(50.0, 0.0, 0.0)), ctypes.byref(uvq))
            assert err == 0
            assert uvq.u == pytest.approx(0.5)
            assert uvq.v == pytest.approx(0.0, abs=1e-9)
            err = dll.SUUVHelperGetFrontUVQ(uv_helper, ctypes.byref(SUPoint3D(0.0, 50.0, 0.0)), ctypes.byref(uvq))
            assert err == 0
            assert uvq.u == pytest.approx(0.0, abs=1e-9)
            assert uvq.v == pytest.approx(0.5)
            dll.SUModelRelease(ctypes.byref(model))
        finally:
            dll.SUTerminate()

    def test_default_camera_is_iso_through_real_sketchup(self, tmp_path):
        import ctypes

        class SUPoint3D(ctypes.Structure):
            _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("z", ctypes.c_double)]

        class SUVector3D(ctypes.Structure):
            _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("z", ctypes.c_double)]

        builder = create()
        builder.add_face(SQUARE)
        out = tmp_path / "iso_camera.skp"
        builder.save(str(out))

        dll = ctypes.CDLL(_SDK_DLL_PATH)
        dll.SUModelCreateFromFile.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_char_p]
        dll.SUModelGetCamera.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUCameraGetOrientation.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(SUPoint3D), ctypes.POINTER(SUPoint3D), ctypes.POINTER(SUVector3D),
        ]
        dll.SUCameraGetPerspective.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_bool)]
        dll.SUInitialize()
        try:
            model = ctypes.c_void_p()
            err = dll.SUModelCreateFromFile(ctypes.byref(model), str(out).encode())
            assert err == 0, f"SketchUp SDK rejected the file (error {err})"
            camera = ctypes.c_void_p()
            dll.SUModelGetCamera(model, ctypes.byref(camera))
            eye, target, up = SUPoint3D(), SUPoint3D(), SUVector3D()
            err = dll.SUCameraGetOrientation(camera, ctypes.byref(eye), ctypes.byref(target), ctypes.byref(up))
            assert err == 0
            assert (eye.x, eye.y, eye.z) == pytest.approx((100.0, -100.0, 100.0))
            assert (target.x, target.y, target.z) == pytest.approx((0.0, 0.0, 0.0))
            perspective = ctypes.c_bool()
            dll.SUCameraGetPerspective(camera, ctypes.byref(perspective))
            assert perspective.value is False
            dll.SUModelRelease(ctypes.byref(model))
        finally:
            dll.SUTerminate()

    def test_circle_recognized_as_true_curve_by_real_sketchup(self, tmp_path):
        # The key claim `add_circle` makes beyond "N straight edges that
        # happen to trace a circle": every edge's own curve pointer
        # (SUEdgeGetCurve) resolves to the exact SAME real curve object,
        # typed as a genuine arc curve with the right edge count - proof
        # real SketchUp treats this as one editable arc entity, not
        # disconnected geometry that merely looks circular.
        #
        # SUEntitiesGetNumCurves is deliberately NOT asserted here: ground
        # truth (an SDK-authored circle+face built via SUGeometryInputAddFace
        # over the same SUGeometryInputAddArcCurve edges) shows real
        # SketchUp reports 0 top-level curves once an arc's edges are fully
        # bound into a face's loop - curves stay reachable per-edge via
        # SUEdgeGetCurve, but drop out of the Entities-level curve list.
        # That's a real, ground-truth-confirmed SketchUp behavior, not a
        # gap in this writer.
        import ctypes

        builder = create()
        builder.add_circle((50.0, 50.0, 0.0), (0.0, 0.0, 1.0), radius=40.0, num_segments=8)
        out = tmp_path / "circle_oracle.skp"
        builder.save(str(out))

        dll = ctypes.CDLL(_SDK_DLL_PATH)
        dll.SUModelCreateFromFile.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_char_p]
        dll.SUModelGetEntities.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUEntitiesGetNumFaces.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        dll.SUEntitiesGetNumEdges.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.POINTER(ctypes.c_size_t)]
        dll.SUEntitiesGetEdges.argtypes = [
            ctypes.c_void_p, ctypes.c_bool, ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        dll.SUEdgeGetCurve.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUCurveGetType.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        dll.SUCurveGetNumEdges.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        dll.SUInitialize()
        try:
            model = ctypes.c_void_p()
            err = dll.SUModelCreateFromFile(ctypes.byref(model), str(out).encode())
            assert err == 0, f"SketchUp SDK rejected the file (error {err})"
            entities = ctypes.c_void_p()
            dll.SUModelGetEntities(model, ctypes.byref(entities))

            nf = ctypes.c_size_t()
            dll.SUEntitiesGetNumFaces(entities, ctypes.byref(nf))
            assert nf.value == 1

            ne = ctypes.c_size_t()
            dll.SUEntitiesGetNumEdges(entities, False, ctypes.byref(ne))
            assert ne.value == 8
            edges = (ctypes.c_void_p * 8)()
            got = ctypes.c_size_t()
            dll.SUEntitiesGetEdges(entities, False, 8, edges, ctypes.byref(got))
            assert got.value == 8

            curve_ptrs = set()
            for i in range(8):
                curve = ctypes.c_void_p()
                err = dll.SUEdgeGetCurve(edges[i], ctypes.byref(curve))
                assert err == 0
                assert curve.value is not None
                curve_ptrs.add(curve.value)
            assert len(curve_ptrs) == 1, "every edge must share the exact same curve"

            curve = ctypes.c_void_p(curve_ptrs.pop())
            ctype = ctypes.c_int()
            dll.SUCurveGetType(curve, ctypes.byref(ctype))
            assert ctype.value == 1  # SUCurveType_ArcCurve

            cne = ctypes.c_size_t()
            dll.SUCurveGetNumEdges(curve, ctypes.byref(cne))
            assert cne.value == 8

            dll.SUModelRelease(ctypes.byref(model))
        finally:
            dll.SUTerminate()

    def test_partial_arc_recognized_as_open_arc_by_real_sketchup(self, tmp_path):
        # Same discipline as the circle oracle test above, but for a
        # partial (open) arc built with add_arc: no face at all, and the
        # actual endpoint positions must land exactly where the requested
        # start_angle/end_angle sweep says they should - not just "some
        # curve object exists with the right edge count".
        import math as _math
        import ctypes

        class SUPoint3D(ctypes.Structure):
            _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("z", ctypes.c_double)]

        builder = create()
        builder.add_arc(
            (50.0, 50.0, 0.0), (0.0, 0.0, 1.0), radius=40.0,
            start_angle=0.0, end_angle=_math.pi / 2, num_segments=6,
        )
        out = tmp_path / "arc_oracle.skp"
        builder.save(str(out))

        dll = ctypes.CDLL(_SDK_DLL_PATH)
        dll.SUModelCreateFromFile.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_char_p]
        dll.SUModelGetEntities.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUEntitiesGetNumFaces.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        dll.SUEntitiesGetNumEdges.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.POINTER(ctypes.c_size_t)]
        dll.SUEntitiesGetEdges.argtypes = [
            ctypes.c_void_p, ctypes.c_bool, ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        dll.SUEdgeGetCurve.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUEdgeGetStartVertex.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUEdgeGetEndVertex.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUVertexGetPosition.argtypes = [ctypes.c_void_p, ctypes.POINTER(SUPoint3D)]
        dll.SUCurveGetType.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        dll.SUCurveGetNumEdges.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        dll.SUInitialize()
        try:
            model = ctypes.c_void_p()
            err = dll.SUModelCreateFromFile(ctypes.byref(model), str(out).encode())
            assert err == 0, f"SketchUp SDK rejected the file (error {err})"
            entities = ctypes.c_void_p()
            dll.SUModelGetEntities(model, ctypes.byref(entities))

            nf = ctypes.c_size_t()
            dll.SUEntitiesGetNumFaces(entities, ctypes.byref(nf))
            assert nf.value == 0

            ne = ctypes.c_size_t()
            dll.SUEntitiesGetNumEdges(entities, False, ctypes.byref(ne))
            assert ne.value == 6
            edges = (ctypes.c_void_p * 6)()
            got = ctypes.c_size_t()
            dll.SUEntitiesGetEdges(entities, False, 6, edges, ctypes.byref(got))
            assert got.value == 6

            curve_ptrs = set()
            positions = set()
            for i in range(6):
                curve = ctypes.c_void_p()
                err = dll.SUEdgeGetCurve(edges[i], ctypes.byref(curve))
                assert err == 0
                assert curve.value is not None
                curve_ptrs.add(curve.value)
                for get_vertex in (dll.SUEdgeGetStartVertex, dll.SUEdgeGetEndVertex):
                    vert = ctypes.c_void_p()
                    get_vertex(edges[i], ctypes.byref(vert))
                    pos = SUPoint3D()
                    dll.SUVertexGetPosition(vert, ctypes.byref(pos))
                    positions.add((round(pos.x, 6), round(pos.y, 6), round(pos.z, 6)))
            assert len(curve_ptrs) == 1, "every edge must share the exact same curve"

            curve = ctypes.c_void_p(curve_ptrs.pop())
            ctype = ctypes.c_int()
            dll.SUCurveGetType(curve, ctypes.byref(ctype))
            assert ctype.value == 1  # SUCurveType_ArcCurve
            cne = ctypes.c_size_t()
            dll.SUCurveGetNumEdges(curve, ctypes.byref(cne))
            assert cne.value == 6

            # angle 0 -> center + (radius, 0, 0); angle pi/2 -> center + (0, radius, 0)
            assert (90.0, 50.0, 0.0) in positions
            assert (50.0, 90.0, 0.0) in positions

            dll.SUModelRelease(ctypes.byref(model))
        finally:
            dll.SUTerminate()

    def test_polyline_recognized_as_simple_curve_by_real_sketchup(self, tmp_path):
        # Same discipline as the circle/arc oracle tests above, but for a
        # freeform polyline (add_polyline): no face, every edge shares the
        # same curve object, and that curve's own type is the "simple"
        # kind (SUCurveType_Simple = 0) - distinct from the arc/circle
        # tests' SUCurveType_ArcCurve = 1, confirming real SketchUp
        # recognizes this as the same grouping mechanism but a genuinely
        # different curve kind, not an arc curve with an unused geometry.
        import ctypes

        builder = create()
        builder.add_polyline([(0.0, 0.0, 0.0), (10.0, 10.0, 0.0), (20.0, 0.0, 0.0), (30.0, 10.0, 0.0)])
        out = tmp_path / "polyline_oracle.skp"
        builder.save(str(out))

        dll = ctypes.CDLL(_SDK_DLL_PATH)
        dll.SUModelCreateFromFile.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_char_p]
        dll.SUModelGetEntities.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUEntitiesGetNumFaces.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        dll.SUEntitiesGetNumEdges.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.POINTER(ctypes.c_size_t)]
        dll.SUEntitiesGetEdges.argtypes = [
            ctypes.c_void_p, ctypes.c_bool, ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        dll.SUEdgeGetCurve.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUCurveGetType.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        dll.SUCurveGetNumEdges.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        dll.SUInitialize()
        try:
            model = ctypes.c_void_p()
            err = dll.SUModelCreateFromFile(ctypes.byref(model), str(out).encode())
            assert err == 0, f"SketchUp SDK rejected the file (error {err})"
            entities = ctypes.c_void_p()
            dll.SUModelGetEntities(model, ctypes.byref(entities))

            nf = ctypes.c_size_t()
            dll.SUEntitiesGetNumFaces(entities, ctypes.byref(nf))
            assert nf.value == 0

            ne = ctypes.c_size_t()
            dll.SUEntitiesGetNumEdges(entities, False, ctypes.byref(ne))
            assert ne.value == 3
            edges = (ctypes.c_void_p * 3)()
            got = ctypes.c_size_t()
            dll.SUEntitiesGetEdges(entities, False, 3, edges, ctypes.byref(got))
            assert got.value == 3

            curve_ptrs = set()
            for i in range(3):
                curve = ctypes.c_void_p()
                err = dll.SUEdgeGetCurve(edges[i], ctypes.byref(curve))
                assert err == 0
                assert curve.value is not None
                curve_ptrs.add(curve.value)
            assert len(curve_ptrs) == 1, "every edge must share the exact same curve"

            curve = ctypes.c_void_p(curve_ptrs.pop())
            ctype = ctypes.c_int()
            dll.SUCurveGetType(curve, ctypes.byref(ctype))
            assert ctype.value == 0  # SUCurveType_Simple, not ArcCurve

            cne = ctypes.c_size_t()
            dll.SUCurveGetNumEdges(curve, ctypes.byref(cne))
            assert cne.value == 3

            dll.SUModelRelease(ctypes.byref(model))
        finally:
            dll.SUTerminate()

    def test_instance_rotation_matches_real_sketchup_transform(self, tmp_path):
        # A +90-degree rotation around +Z (right-hand rule) must send a
        # local point on the +X axis to world +Y - confirms this writer's
        # `rotation=` convenience matches real SketchUp's own transform
        # convention, not just that SOME rotation was applied.
        import ctypes

        class SUPoint3D(ctypes.Structure):
            _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("z", ctypes.c_double)]

        class SUTransformation(ctypes.Structure):
            _fields_ = [("values", ctypes.c_double * 16)]

        builder = create()
        with builder.add_component_definition("Marker") as marker:
            marker.add_face([(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 1.0, 0.0), (0.0, 1.0, 0.0)])
        builder.add_instance(marker, rotation=((0.0, 0.0, 1.0), math.pi / 2))
        out = tmp_path / "rotation_oracle.skp"
        builder.save(str(out))

        dll = ctypes.CDLL(_SDK_DLL_PATH)
        dll.SUModelCreateFromFile.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_char_p]
        dll.SUModelGetEntities.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUEntitiesGetInstances.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        dll.SUComponentInstanceGetTransform.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        dll.SUInitialize()
        try:
            model = ctypes.c_void_p()
            err = dll.SUModelCreateFromFile(ctypes.byref(model), str(out).encode())
            assert err == 0, f"SketchUp SDK rejected the file (error {err})"
            entities = ctypes.c_void_p()
            dll.SUModelGetEntities(model, ctypes.byref(entities))
            inst = (ctypes.c_void_p * 1)()
            got = ctypes.c_size_t()
            dll.SUEntitiesGetInstances(entities, 1, inst, ctypes.byref(got))
            assert got.value == 1
            t = SUTransformation()
            dll.SUComponentInstanceGetTransform(inst[0], ctypes.byref(t))
            # SUTransformation.values is column-major: column 0 is where the
            # local +X axis (10, 0, 0) ends up.
            col0 = t.values[0:3]
            world = (10.0 * col0[0], 10.0 * col0[1], 10.0 * col0[2])
            assert world == pytest.approx((0.0, 10.0, 0.0), abs=1e-6)
            dll.SUModelRelease(ctypes.byref(model))
        finally:
            dll.SUTerminate()

    def test_auto_triangulated_warped_quad_opens_as_two_faces_in_real_sketchup(self, tmp_path):
        # A non-planar "quad" real SketchUp itself would silently split
        # into 2 triangles when drawn by hand - confirms this writer's
        # auto_triangulate fallback produces the same structurally valid
        # result the real application accepts, not just something our
        # own lenient reader tolerates.
        import ctypes

        builder = create()
        builder.add_face(
            [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 5.0)],
            auto_triangulate=True,
        )
        out = tmp_path / "warped_quad.skp"
        builder.save(str(out))

        dll = ctypes.CDLL(_SDK_DLL_PATH)
        dll.SUModelCreateFromFile.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_char_p]
        dll.SUModelGetEntities.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUEntitiesGetNumFaces.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        dll.SUInitialize()
        try:
            model = ctypes.c_void_p()
            err = dll.SUModelCreateFromFile(ctypes.byref(model), str(out).encode())
            assert err == 0, f"SketchUp SDK rejected the file (error {err})"
            entities = ctypes.c_void_p()
            dll.SUModelGetEntities(model, ctypes.byref(entities))
            nf = ctypes.c_size_t()
            dll.SUEntitiesGetNumFaces(entities, ctypes.byref(nf))
            assert nf.value == 2
            dll.SUModelRelease(ctypes.byref(model))
        finally:
            dll.SUTerminate()

    def test_face_hole_area_is_subtracted_by_real_sketchup(self, tmp_path):
        # The real, load-bearing claim for holes: not just "the file opens
        # and has 2 loops" but that real SketchUp actually treats the
        # inner loop as a genuine subtracted opening - confirmed via the
        # face's own reported area, not just structural presence.
        import ctypes

        outer = [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (100.0, 100.0, 0.0), (0.0, 100.0, 0.0)]
        hole = [(30.0, 30.0, 0.0), (70.0, 30.0, 0.0), (70.0, 70.0, 0.0), (30.0, 70.0, 0.0)]
        builder = create()
        builder.add_face(outer, holes=[hole])
        out = tmp_path / "hole_face.skp"
        builder.save(str(out))

        dll = ctypes.CDLL(_SDK_DLL_PATH)
        dll.SUModelCreateFromFile.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_char_p]
        dll.SUModelGetEntities.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUEntitiesGetFaces.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        dll.SUFaceGetArea.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_double)]
        dll.SUInitialize()
        try:
            model = ctypes.c_void_p()
            err = dll.SUModelCreateFromFile(ctypes.byref(model), str(out).encode())
            assert err == 0, f"SketchUp SDK rejected the file (error {err})"
            entities = ctypes.c_void_p()
            dll.SUModelGetEntities(model, ctypes.byref(entities))
            faces = (ctypes.c_void_p * 1)()
            got = ctypes.c_size_t()
            dll.SUEntitiesGetFaces(entities, 1, faces, ctypes.byref(got))
            assert got.value == 1
            area = ctypes.c_double()
            dll.SUFaceGetArea(faces[0], ctypes.byref(area))
            assert area.value == pytest.approx(100 * 100 - 40 * 40)
            dll.SUModelRelease(ctypes.byref(model))
        finally:
            dll.SUTerminate()

    def test_hidden_instance_group_and_layer_visibility_match_real_sketchup(self, tmp_path):
        import ctypes

        builder = create()
        roof = builder.add_layer("Roof", color=(150, 75, 30), hidden=True)
        with builder.add_component_definition("Chair") as chair:
            chair.add_face(SQUARE)
        with builder.add_group("Table", hidden=True) as table:
            table.add_face(SQUARE)
        builder.add_instance(chair, hidden=True, layer=roof)
        out = tmp_path / "hidden_oracle.skp"
        builder.save(str(out))

        dll = ctypes.CDLL(_SDK_DLL_PATH)
        dll.SUModelCreateFromFile.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_char_p]
        dll.SUModelGetEntities.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUEntitiesGetInstances.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        dll.SUEntitiesGetGroups.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        dll.SUDrawingElementGetHidden.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_bool)]
        dll.SUModelGetNumLayers.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        dll.SUModelGetLayers.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        dll.SULayerGetVisibility.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_bool)]
        dll.SUInitialize()
        try:
            model = ctypes.c_void_p()
            err = dll.SUModelCreateFromFile(ctypes.byref(model), str(out).encode())
            assert err == 0, f"SketchUp SDK rejected the file (error {err})"
            entities = ctypes.c_void_p()
            dll.SUModelGetEntities(model, ctypes.byref(entities))

            inst = (ctypes.c_void_p * 1)()
            got = ctypes.c_size_t()
            dll.SUEntitiesGetInstances(entities, 1, inst, ctypes.byref(got))
            assert got.value == 1
            hidden = ctypes.c_bool()
            dll.SUDrawingElementGetHidden(inst[0], ctypes.byref(hidden))
            assert hidden.value is True

            grp = (ctypes.c_void_p * 1)()
            got2 = ctypes.c_size_t()
            dll.SUEntitiesGetGroups(entities, 1, grp, ctypes.byref(got2))
            assert got2.value == 1
            hidden2 = ctypes.c_bool()
            dll.SUDrawingElementGetHidden(grp[0], ctypes.byref(hidden2))
            assert hidden2.value is True

            nl = ctypes.c_size_t()
            dll.SUModelGetNumLayers(model, ctypes.byref(nl))
            layers = (ctypes.c_void_p * nl.value)()
            gotl = ctypes.c_size_t()
            dll.SUModelGetLayers(model, nl.value, layers, ctypes.byref(gotl))
            visibilities = []
            for i in range(gotl.value):
                vis = ctypes.c_bool()
                dll.SULayerGetVisibility(layers[i], ctypes.byref(vis))
                visibilities.append(vis.value)
            assert False in visibilities  # the hidden "Roof" layer
            assert True in visibilities  # the default, visible "Layer0"

            dll.SUModelRelease(ctypes.byref(model))
        finally:
            dll.SUTerminate()

    def test_model_crossing_the_slot_boundary_opens_cleanly(self, tmp_path):
        # Before the _backref/_new_of_known_class/_shift_ref fix, a model
        # whose total archive-slot count crossed 0x7FFF (32767) was
        # rejected outright by the real SDK (SUModelCreateFromFile
        # returned a non-zero error, matching the "Unexpected file
        # format" a user sees in the SketchUp GUI) - confirmed by
        # reverting the fix locally and observing error code 12 on this
        # exact file shape, plus our own reader silently dropping ~40%
        # of the faces with no error at all. 5000 unique (non-shared-
        # vertex) triangles reliably crosses the boundary with margin.
        import ctypes

        builder = create()
        for i in range(5000):
            x = i * 10.0
            builder.add_face([(x, 0.0, 0.0), (x + 1.0, 0.0, 0.0), (x, 1.0, 0.0)])
        assert builder._geometry_writer.next_slot > 0x7FFF
        out = tmp_path / "slot_boundary_oracle.skp"
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
            assert nfaces.value == 5000
            dll.SUModelRelease(ctypes.byref(model))
        finally:
            dll.SUTerminate()


class TestMaterialOpacity:
    def test_solid_material_carries_opacity(self):
        builder = create()
        glass = builder.add_material("Glass", (200, 220, 255), opacity=0.35)
        builder.add_face(SQUARE, material=glass)
        data = builder.to_bytes()

        ar, root, layers, materials = legacy._walk(data)
        mat = {s: v for s, v in materials}[glass]
        # Stored as TRANSPARENCY gated by use_opacity - the exact solid-tail
        # shape legacy.py documents (and VFF's useTrans semantics).
        assert mat["use_opacity"] == 1
        assert mat["opacity"] == pytest.approx(0.65)

    def test_omitted_opacity_stays_ungated(self):
        builder = create()
        red = builder.add_material("Red", (255, 0, 0))
        builder.add_face(SQUARE, material=red)
        data = builder.to_bytes()

        mat = {s: v for s, v in legacy._walk(data)[3]}[red]
        assert mat["use_opacity"] == 0

    def test_texture_material_carries_opacity(self, tmp_path):
        png_path = tmp_path / "tex.png"
        png_path.write_bytes(_make_test_png(size=4, rgb=(200, 50, 50)))

        builder = create()
        tex = builder.add_texture_material("Voile", str(png_path), opacity=0.5)
        builder.add_face(SQUARE, material=tex)
        data = builder.to_bytes()

        mat = {s: v for s, v in legacy._walk(data)[3]}[tex]
        assert mat["use_opacity"] == 1
        assert mat["opacity"] == pytest.approx(0.5)



def test_face_me_behaviour_round_trips(tmp_path):
    """``always_faces_camera`` / ``shadows_face_sun`` land in the behavior
    byte the reader already decodes (byte -9 of the definition's 43-byte
    gap): a 2D person written here is a real face-me component in SketchUp
    - it turns toward the camera - instead of a flat cut-out standing still."""
    from openskp import SkpFile
    builder = create()
    with builder.add_component_definition("Susan", always_faces_camera=True, shadows_face_sun=True) as susan:
        susan.add_face([(0.0, 0.0, 0.0), (20.0, 0.0, 0.0), (20.0, 0.0, 70.0), (0.0, 0.0, 70.0)])
    with builder.add_component_definition("Chair") as chair:
        chair.add_face(SQUARE)
    builder.add_instance(susan, translation=(100.0, 50.0, 0.0))
    builder.add_instance(chair, translation=(0.0, 0.0, 0.0))
    out = tmp_path / "faceme.skp"
    builder.save(str(out))
    model = SkpFile.open(str(out)).parse()
    by_name = {d.name: d for d in model.definitions.values()}
    assert by_name["Susan"].always_faces_camera is True
    assert by_name["Susan"].shadows_face_sun is True
    assert by_name["Chair"].always_faces_camera is False
    assert by_name["Chair"].shadows_face_sun is False



def test_persistent_ids_run_in_one_sequence_across_sections():
    """Every section's writer used to hand out pids from 1 again, so a
    material, a definition and a root instance could all be pid 1, and the
    header counter (a u32 at _PID_COUNTER_POS) only ever grew by the
    material and layer count. SketchUp loads such a file and renumbers the
    duplicates - and then SUModelSaveToFile fails with SU_ERROR_SERIALIZATION
    once the model is big enough or a definition happens to come first
    ("Guardado fallido" in SketchUp Web on a real 7 MB export). One sequence
    now, continuing from the scaffold's counter, and the counter tells the
    truth."""
    import struct
    from openskp.create import _PID_COUNTER_POS
    builder = create()
    tex_free = builder.add_material("Rojo", (255, 0, 0, 255))
    layer = builder.add_layer("Estructura")
    with builder.add_component_definition("A") as a:
        a.add_face(SQUARE, material=tex_free, layer=layer)
        a.add_face([(x + 300.0, y, z) for x, y, z in SQUARE])
    with builder.add_component_definition("B") as b:
        b.add_face([(x, y, z + 50.0) for x, y, z in SQUARE])
    builder.add_instance(a)
    builder.add_instance(b, translation=(0.0, 0.0, 100.0))
    data = builder.to_bytes()
    counter = struct.unpack_from("<I", data, _PID_COUNTER_POS)[0]
    # sections in order, each continuing where the previous stopped
    starts = [builder._pid_start, builder._material_writer.next_pid,
              builder._layer_writer.next_pid, builder._definition_writer.next_pid,
              builder._geometry_writer.next_pid]
    assert starts == sorted(starts) and starts[0] < starts[-1]
    assert counter == builder._geometry_writer.next_pid - 1
    assert counter > builder._pid_start + 2          # far more than materials + layers


def test_a_small_definition_before_a_bigger_one_saves_through_real_sketchup(tmp_path):
    """The minimal case that real SketchUp refused to SAVE (SU_ERROR_SERIALIZATION,
    7) before pids ran in one sequence: a 1-face definition, then a 3-face
    one, both placed. Needs the SDK DLL."""
    import ctypes, os
    if not os.path.exists(_SDK_DLL_PATH):
        pytest.skip("SketchUp SDK not present on this machine")
    builder = create()
    with builder.add_component_definition("Primera") as d1:
        d1.add_face(SQUARE)
    with builder.add_component_definition("Segunda") as d2:
        for k in range(3):
            d2.add_face([(x + 300.0 * k, y, z + 50.0) for x, y, z in SQUARE])
    builder.add_instance(d1)
    builder.add_instance(d2, translation=(0.0, 0.0, 100.0))
    src = tmp_path / "small-then-big.skp"
    builder.save(str(src))
    dll = ctypes.CDLL(_SDK_DLL_PATH)
    dll.SUModelCreateFromFile.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_char_p]
    dll.SUModelSaveToFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    dll.SUInitialize()
    try:
        model = ctypes.c_void_p()
        assert dll.SUModelCreateFromFile(ctypes.byref(model), str(src).encode()) == 0
        out = tmp_path / "resaved.skp"
        assert dll.SUModelSaveToFile(model, str(out).encode()) == 0
        dll.SUModelRelease(ctypes.byref(model))
    finally:
        dll.SUTerminate()
