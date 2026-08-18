"""Tests for openskp.edit - loading an existing legacy .skp file and
rebuilding it as a new SkpBuilder (see that module's own docstring for the
exact scope and known fidelity gaps this suite exercises).
"""
from __future__ import annotations

import pathlib

import pytest

from openskp import SkpFile, edit
from openskp.create import SkpWriteError, create

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
SQUARE = [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (100.0, 100.0, 0.0), (0.0, 100.0, 0.0)]


def _make_test_png(size: int = 4, rgb=(200, 50, 50)) -> bytes:
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


class TestOpenExisting:
    def test_rejects_vff_source(self):
        with pytest.raises(SkpWriteError, match="not a legacy-format"):
            edit.open_existing(FIXTURES / "SU_File.skp")

    def test_rejects_nonexistent_file(self):
        with pytest.raises(FileNotFoundError):
            edit.open_existing(FIXTURES / "does_not_exist.skp")

    def test_round_trips_simple_file(self, tmp_path):
        builder = create()
        red = builder.add_material("Red", (255, 0, 0))
        roof = builder.add_layer("Roof")
        with builder.add_component_definition("Chair") as chair:
            chair.add_face([(0, 0, 0), (20, 0, 0), (20, 20, 0), (0, 20, 0)], material=red)
        builder.add_instance(chair, translation=(0, 0, 0))
        builder.add_instance(chair, translation=(50, 0, 0))
        builder.add_face(SQUARE, material=red, layer=roof)
        src = tmp_path / "source.skp"
        builder.save(str(src))

        new_builder, warnings, definitions = edit.open_existing(str(src))
        data = new_builder.to_bytes()
        out = tmp_path / "rebuilt.skp"
        out.write_bytes(data)
        rebuilt = SkpFile.open(str(out)).parse()

        assert len(rebuilt.root.faces) == 1
        assert len(rebuilt.root.instances) == 2
        assert len(rebuilt.definitions) == 1
        chair_defn = next(iter(rebuilt.definitions.values()))
        assert chair_defn.name == "Chair"
        assert len(chair_defn.faces) == 1
        assert [m.name for m in rebuilt.materials] == ["Red"]
        assert "Roof" in [layer.name for layer in rebuilt.layers]

    def test_preserves_instance_translation(self, tmp_path):
        builder = create()
        with builder.add_component_definition("Post") as post:
            post.add_face([(0, 0, 0), (5, 0, 0), (5, 5, 0), (0, 5, 0)])
        builder.add_instance(post, translation=(37.5, -12.25, 8.0))
        src = tmp_path / "source.skp"
        builder.save(str(src))

        new_builder, warnings, definitions = edit.open_existing(str(src))
        out = tmp_path / "rebuilt.skp"
        out.write_bytes(new_builder.to_bytes())
        rebuilt = SkpFile.open(str(out)).parse()

        inst = rebuilt.root.instances[0]
        assert (inst.matrix[9], inst.matrix[10], inst.matrix[11]) == pytest.approx((37.5, -12.25, 8.0))

    def test_nested_definition_dependency_order(self, tmp_path):
        # Car nests 2 instances of Wheel - Wheel must be fully built and
        # closed before Car opens (write order matters for this format).
        builder = create()
        with builder.add_component_definition("Wheel") as wheel:
            wheel.add_face([(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)])
        with builder.add_component_definition("Car") as car:
            car.add_face([(0, 0, 0), (100, 0, 0), (100, 50, 0), (0, 50, 0)])
            car.add_instance(wheel, translation=(10, 10, 0))
            car.add_instance(wheel, translation=(80, 10, 0))
        builder.add_instance(car)
        src = tmp_path / "source.skp"
        builder.save(str(src))

        new_builder, warnings, definitions = edit.open_existing(str(src))
        out = tmp_path / "rebuilt.skp"
        out.write_bytes(new_builder.to_bytes())
        rebuilt = SkpFile.open(str(out)).parse()

        by_name = {d.name: d for d in rebuilt.definitions.values()}
        assert set(by_name) == {"Wheel", "Car"}
        assert len(by_name["Car"].instances) == 2

    def test_materials_and_layers_reusable_after_replay(self, tmp_path):
        # The practical gap independent testing surfaced: after
        # open_existing(), a caller needs a way to reuse the source
        # file's own materials/layers on new geometry without reaching
        # into a private attribute.
        builder = create()
        red = builder.add_material("Red", (255, 0, 0))
        roof = builder.add_layer("Roof")
        builder.add_face(SQUARE, material=red, layer=roof)
        src = tmp_path / "source.skp"
        builder.save(str(src))

        new_builder, warnings, definitions = edit.open_existing(str(src))
        assert "Red" in new_builder.materials_by_name
        assert "Roof" in new_builder.layers_by_name
        # The reused handle actually works on new geometry, not just present.
        new_builder.add_face(
            [(300, 0, 0), (310, 0, 0), (310, 10, 0), (300, 10, 0)],
            material=new_builder.materials_by_name["Red"],
            layer=new_builder.layers_by_name["Roof"],
        )
        out = tmp_path / "rebuilt.skp"
        out.write_bytes(new_builder.to_bytes())
        rebuilt = SkpFile.open(str(out)).parse()
        assert len(rebuilt.materials) == 1  # still just "Red" - reused, not duplicated
        assert len(rebuilt.root.faces) == 2

    def test_definitions_returned_by_name(self, tmp_path):
        builder = create()
        with builder.add_component_definition("Wheel") as wheel:
            wheel.add_face([(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)])
        builder.add_instance(wheel, translation=(0, 0, 0))
        src = tmp_path / "source.skp"
        builder.save(str(src))

        new_builder, warnings, definitions = edit.open_existing(str(src))
        assert "Wheel" in definitions
        # The returned definition is directly usable for a NEW placement.
        new_builder.add_instance(definitions["Wheel"], translation=(100, 0, 0))
        out = tmp_path / "rebuilt.skp"
        out.write_bytes(new_builder.to_bytes())
        rebuilt = SkpFile.open(str(out)).parse()
        assert len(rebuilt.definitions) == 1  # still just one Wheel definition
        assert len(rebuilt.root.instances) == 2  # but now placed twice

    def test_new_material_layer_definition_group_all_rejected_after_replay(self, tmp_path):
        # Documented, tested constraint (not a bug): replaying a source
        # file's own root-level geometry already finalizes the writer's
        # materials/layers/definitions sections, per the same file-format
        # ordering requirement every create() builder has always had.
        builder = create()
        builder.add_face(SQUARE)
        src = tmp_path / "source.skp"
        builder.save(str(src))

        new_builder, warnings, definitions = edit.open_existing(str(src))
        with pytest.raises(SkpWriteError, match="before any add_face"):
            new_builder.add_material("Chrome", (180, 180, 185))
        with pytest.raises(SkpWriteError, match="before any add_face"):
            new_builder.add_layer("Extra")
        with pytest.raises(SkpWriteError, match="before any add_face/add_instance"):
            new_builder.add_component_definition("New")
        with pytest.raises(SkpWriteError, match="before any add_face/add_instance"):
            new_builder.add_group("NewGroup")

    def test_positioned_texture_round_trips(self, tmp_path):
        png_path = tmp_path / "tex.png"
        png_path.write_bytes(_make_test_png())
        builder = create()
        brick = builder.add_texture_material("Brick", str(png_path))
        builder.add_face(
            SQUARE, material=brick,
            front_uv=[((0.0, 0.0, 0.0), (0.0, 0.0)), ((50.0, 0.0, 0.0), (1.0, 0.0)), ((0.0, 50.0, 0.0), (0.0, 1.0))],
        )
        src = tmp_path / "source.skp"
        builder.save(str(src))

        new_builder, warnings, definitions = edit.open_existing(str(src))
        out = tmp_path / "rebuilt.skp"
        out.write_bytes(new_builder.to_bytes())
        rebuilt = SkpFile.open(str(out)).parse()

        face = next(iter(rebuilt.root.faces.values()))
        assert face.uv_transform is not None
        assert rebuilt.materials[0].texture is not None
        assert rebuilt.materials[0].texture.data == _make_test_png()

    def test_face_with_hole_round_trips(self, tmp_path):
        # add_face's holes= support (added alongside this module) means a
        # multi-loop face is now faithfully replayed, not skipped.
        wall = [(0.0, 0.0, 0.0), (200.0, 0.0, 0.0), (200.0, 100.0, 0.0), (0.0, 100.0, 0.0)]
        window = [(80.0, 30.0, 0.0), (120.0, 30.0, 0.0), (120.0, 70.0, 0.0), (80.0, 70.0, 0.0)]
        builder = create()
        builder.add_face(wall, holes=[window])
        src = tmp_path / "source.skp"
        builder.save(str(src))

        new_builder, warnings, definitions = edit.open_existing(str(src))
        assert not any("hole" in w for w in warnings)
        out = tmp_path / "rebuilt.skp"
        out.write_bytes(new_builder.to_bytes())
        rebuilt = SkpFile.open(str(out)).parse()
        face = next(iter(rebuilt.root.faces.values()))
        assert len(face.loops) == 2

    def test_face_with_invalid_hole_is_skipped_not_corrupting(self, tmp_path):
        # A hole loop that isn't on the face's own plane is something
        # write_face itself rejects (upfront-validated, before writing
        # any bytes) - simulate the reader seeing one anyway by
        # monkeypatching a parsed definition's faces, confirming the
        # skip-this-face path doesn't corrupt geometry written afterward.
        from openskp.model import Edge, Face, Vertex

        builder = create()
        builder.add_face(SQUARE)
        builder.add_face([(200, 0, 0), (300, 0, 0), (300, 100, 0), (200, 100, 0)])
        src = tmp_path / "source.skp"
        builder.save(str(src))

        model = SkpFile.open(str(src)).parse()
        first_id = next(iter(model.root.faces))
        original = model.root.faces[first_id]

        # Fabricate an off-plane "hole" loop using fresh vertex/edge ids.
        off_plane_pts = [(20.0, 20.0, 5.0), (40.0, 20.0, 5.0), (40.0, 40.0, 5.0), (20.0, 40.0, 5.0)]
        base_vid = max(model.root.vertices) + 1
        base_eid = max(model.root.edges) + 1
        hole_loop = []
        for i, p in enumerate(off_plane_pts):
            vid = base_vid + i
            model.root.vertices[vid] = Vertex(id=vid, x=p[0], y=p[1], z=p[2])
        for i in range(4):
            eid = base_eid + i
            v1 = base_vid + i
            v2 = base_vid + (i + 1) % 4
            model.root.edges[eid] = Edge(id=eid, v1_id=v1, v2_id=v2)
            hole_loop.append((eid, 1))

        model.root.faces[first_id] = Face(id=original.id, loops=[original.loops[0], hole_loop])

        new_builder = create()
        warnings = []
        material_slots = edit._replay_materials(new_builder, model, warnings)
        layer_slots = {}
        edit._replay_body(new_builder, model.root, model, material_slots, layer_slots, warnings, "root", {})
        assert any("skipped" in w for w in warnings)
        data = new_builder.to_bytes()
        # self-parses cleanly and the second (valid) face survived
        out = tmp_path / "rebuilt.skp"
        out.write_bytes(data)
        rebuilt = SkpFile.open(str(out)).parse()
        assert len(rebuilt.root.faces) == 1

    def test_empty_source_can_still_be_saved_after_adding_geometry(self, tmp_path):
        new_builder, warnings, definitions = edit.open_existing(FIXTURES / "blank_v17.skp")
        with pytest.raises(SkpWriteError, match="no geometry"):
            new_builder.to_bytes()
        new_builder.add_face(SQUARE)
        data = new_builder.to_bytes()
        assert len(data) > 0


class TestRealWorldFixtures:
    """Round-trip real, non-writer-authored files - the true stress test
    for this module, since every other test above only exercises content
    this project's own writer already produces (a much narrower subset of
    what real SketchUp files contain)."""

    @pytest.mark.parametrize("fixture_name", ["capilla_quiroz_v17.skp", "gondola_v20.skp"])
    def test_round_trips_without_crashing(self, fixture_name, tmp_path):
        path = FIXTURES / fixture_name
        if not path.exists():
            pytest.skip(f"fixture {fixture_name} not present")
        new_builder, warnings, definitions = edit.open_existing(str(path))
        data = new_builder.to_bytes()
        out = tmp_path / "rebuilt.skp"
        out.write_bytes(data)
        # self-parses without raising - the authoritative "is this
        # structurally valid" check for a file this large/real
        SkpFile.open(str(out)).parse()

    def test_capilla_preserves_almost_all_geometry(self, tmp_path):
        path = FIXTURES / "capilla_quiroz_v17.skp"
        if not path.exists():
            pytest.skip("fixture not present")
        orig = SkpFile.open(str(path)).parse()
        new_builder, warnings, definitions = edit.open_existing(str(path))
        out = tmp_path / "rebuilt.skp"
        out.write_bytes(new_builder.to_bytes())
        rebuilt = SkpFile.open(str(out)).parse()

        orig_total = sum(len(d.faces) for d in orig.definitions.values()) + len(orig.root.faces)
        rebuilt_total = sum(len(d.faces) for d in rebuilt.definitions.values()) + len(rebuilt.root.faces)
        # At most a handful of faces (e.g. a degenerate UV correspondence)
        # are expected to be skipped, never a large fraction - a big drop
        # would indicate silent corruption, not a legitimately-scoped gap.
        assert rebuilt_total >= orig_total - 5
        assert len(rebuilt.root.instances) == len(orig.root.instances)
        assert len(rebuilt.definitions) == len(orig.definitions)


class TestRealSketchUpOracle:
    _SDK_DLL_PATH = pathlib.Path(
        __import__("os").environ.get(
            "OPENSKP_TEST_SKETCHUP_SDK_DLL",
            r"C:\Program Files\SketchUp\SketchUp 2025\SketchUp\SketchUpAPI.dll",
        )
    )

    def test_rebuilt_real_world_file_opens_in_real_sketchup(self, tmp_path):
        import ctypes

        if not self._SDK_DLL_PATH.exists():
            pytest.skip("SketchUp SDK not present on this machine")
        path = FIXTURES / "capilla_quiroz_v17.skp"
        if not path.exists():
            pytest.skip("fixture not present")

        orig = SkpFile.open(str(path)).parse()
        new_builder, warnings, definitions = edit.open_existing(str(path))
        out = tmp_path / "rebuilt.skp"
        out.write_bytes(new_builder.to_bytes())

        dll = ctypes.CDLL(str(self._SDK_DLL_PATH))
        dll.SUModelCreateFromFile.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_char_p]
        dll.SUModelGetEntities.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.SUEntitiesGetNumFaces.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        dll.SUEntitiesGetNumInstances.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        dll.SUInitialize()
        try:
            model = ctypes.c_void_p()
            err = dll.SUModelCreateFromFile(ctypes.byref(model), str(out).encode())
            assert err == 0, f"SketchUp SDK rejected the rebuilt file (error {err})"
            entities = ctypes.c_void_p()
            dll.SUModelGetEntities(model, ctypes.byref(entities))
            nf = ctypes.c_size_t()
            dll.SUEntitiesGetNumFaces(entities, ctypes.byref(nf))
            ni = ctypes.c_size_t()
            dll.SUEntitiesGetNumInstances(entities, ctypes.byref(ni))
            assert nf.value >= len(orig.root.faces) - 5
            assert ni.value == len(orig.root.instances)
            dll.SUModelRelease(ctypes.byref(model))
        finally:
            dll.SUTerminate()
