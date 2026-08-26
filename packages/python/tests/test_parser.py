"""Basic tests for the openskp package.

These tests validate the low-level parser utilities, data model
construction, and transform maths without requiring a real ``.skp`` file.
"""

from __future__ import annotations

import pathlib
import struct

import pytest


# ── Parser tests ─────────────────────────────────────────────────────────


class TestReadU32:
    """Tests for :func:`openskp.parser.read_u32`."""

    def test_zero(self) -> None:
        from openskp.parser import read_u32

        data = struct.pack('<I', 0)
        assert read_u32(data, 0) == 0

    def test_known_value(self) -> None:
        from openskp.parser import read_u32

        data = struct.pack('<I', 305419896)  # 0x12345678
        assert read_u32(data, 0) == 305419896

    def test_offset(self) -> None:
        from openskp.parser import read_u32

        data = b'\x00\x00' + struct.pack('<I', 42)
        assert read_u32(data, 2) == 42


class TestReadF64:
    """Tests for :func:`openskp.parser.read_f64`."""

    def test_pi(self) -> None:
        from openskp.parser import read_f64

        import math
        data = struct.pack('<d', math.pi)
        assert abs(read_f64(data, 0) - math.pi) < 1e-15


class TestParseVarInt:
    """Tests for :func:`openskp.parser.parse_var_int`."""

    def test_single_byte(self) -> None:
        from openskp.parser import parse_var_int

        assert parse_var_int(bytes([0x42]), 0, 1) == 0x42

    def test_two_bytes(self) -> None:
        from openskp.parser import parse_var_int

        data = bytes([0x01, 0x02])
        assert parse_var_int(data, 0, 2) == 0x0201

    def test_four_bytes(self) -> None:
        from openskp.parser import parse_var_int

        data = bytes([0x78, 0x56, 0x34, 0x12])
        assert parse_var_int(data, 0, 4) == 0x12345678


class TestParseTlvRecursive:
    """Tests for :func:`openskp.parser.parse_tlv_recursive`."""

    def _make_tlv(self, tag_hex: str, payload: bytes) -> bytes:
        """Build a single TLV element."""
        tag = bytes.fromhex(tag_hex)
        length = struct.pack('<I', len(payload))
        return tag + length + payload

    def test_single_leaf(self) -> None:
        from openskp.parser import parse_tlv_recursive

        data = self._make_tlv("0100", b'\xAA\xBB')
        nodes = parse_tlv_recursive(data, 0, len(data))
        assert len(nodes) == 1
        assert nodes[0].tag == "0100"
        assert nodes[0].payload == b'\xAA\xBB'
        assert nodes[0].children == []

    def test_two_elements(self) -> None:
        from openskp.parser import parse_tlv_recursive

        data = self._make_tlv("0100", b'\x01') + self._make_tlv("0200", b'\x02\x03')
        nodes = parse_tlv_recursive(data, 0, len(data))
        assert len(nodes) == 2
        assert nodes[0].tag == "0100"
        assert nodes[1].tag == "0200"

    def test_empty_payload(self) -> None:
        from openskp.parser import parse_tlv_recursive

        # Need a second element so buffer > 6 bytes (the while guard is `pos < end - 6`)
        data = self._make_tlv("0300", b'') + self._make_tlv("0100", b'\x01')
        nodes = parse_tlv_recursive(data, 0, len(data))
        assert len(nodes) == 2
        assert nodes[0].size == 0
        assert nodes[0].payload == b''


# ── Data model tests ─────────────────────────────────────────────────────


class TestDataModel:
    """Tests for :mod:`openskp.model` dataclasses."""

    def test_vertex_creation(self) -> None:
        from openskp.model import Vertex

        v = Vertex(id=0, x=1.0, y=2.0, z=3.0)
        assert v.x == 1.0

    def test_edge_creation(self) -> None:
        from openskp.model import Edge

        e = Edge(id=0, v1_id=1, v2_id=2)
        assert e.v1_id == 1

    def test_face_defaults(self) -> None:
        from openskp.model import Face

        f = Face(id=0)
        assert f.loops == []
        assert f.normal is None

    def test_layer_defaults(self) -> None:
        from openskp.model import Layer

        layer = Layer(name="Test")
        assert layer.color_r == 200

    def test_material_defaults(self) -> None:
        from openskp.model import Material

        mat = Material(name="Wood")
        assert mat.transparency == 1.0
        assert mat.color == (200, 200, 200, 255)

    def test_instance_identity_matrix(self) -> None:
        from openskp.model import Instance

        inst = Instance()
        assert len(inst.matrix) == 16
        assert inst.matrix[0] == 1.0
        assert inst.matrix[5] == 1.0

    def test_instance_has_no_children_field(self) -> None:
        # Item 17: Instance.children was declared but never assigned
        # during parsing anywhere (confirmed the same in Dart/.NET/C++ -
        # a definition's placed instances are always a flat list at
        # parse time; nesting only exists in the resolved scene graph),
        # so it was removed outright rather than left as a permanently-
        # empty field.
        from openskp.model import Instance
        import dataclasses

        field_names = {f.name for f in dataclasses.fields(Instance)}
        assert "children" not in field_names
        assert "layer" in field_names
        assert "properties" in field_names

    def test_skp_model_defaults(self) -> None:
        from openskp.model import SkpModel

        model = SkpModel()
        assert model.version == "unknown"
        assert model.definitions == {}
        assert model.layers == []


# ── Transform tests ──────────────────────────────────────────────────────


class TestTransforms:
    """Tests for :mod:`openskp.transforms`."""

    def test_identity_transform(self) -> None:
        from openskp.transforms import transform_point, IDENTITY_MATRIX

        x, y, z = transform_point(IDENTITY_MATRIX, 1.0, 2.0, 3.0)
        assert (x, y, z) == (1.0, 2.0, 3.0)

    def test_translation(self) -> None:
        from openskp.transforms import transform_point

        matrix = [
            1, 0, 0, 0,
            0, 1, 0, 0,
            0, 0, 1, 0,
            10, 20, 30, 1,
        ]
        x, y, z = transform_point(matrix, 0, 0, 0)
        assert (x, y, z) == (10.0, 20.0, 30.0)

    def test_multiply_identity(self) -> None:
        from openskp.transforms import multiply_matrices, IDENTITY_MATRIX

        result = multiply_matrices(IDENTITY_MATRIX, IDENTITY_MATRIX)
        for i in range(16):
            expected = 1.0 if i % 5 == 0 else 0.0
            assert abs(result[i] - expected) < 1e-12

    def test_z_up_to_y_up(self) -> None:
        from openskp.transforms import z_up_to_y_up

        x, y, z = z_up_to_y_up(1.0, 2.0, 3.0)
        assert x == 1.0
        assert y == 3.0
        assert z == -2.0

    def test_is_identity_true(self) -> None:
        from openskp.transforms import is_identity, IDENTITY_MATRIX

        assert is_identity(IDENTITY_MATRIX) is True

    def test_is_identity_false(self) -> None:
        from openskp.transforms import is_identity

        matrix = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 5, 0, 0, 1]
        assert is_identity(matrix) is False


# ── Triangulator tests ───────────────────────────────────────────────────


class TestTriangulator:
    """Tests for :mod:`openskp.triangulator`."""

    def test_triangle_passthrough(self) -> None:
        from openskp.triangulator import triangulate_face_3d

        pts = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
        indices = triangulate_face_3d(pts, (0, 0, 1))
        assert indices == [0, 1, 2]

    def test_degenerate_input(self) -> None:
        from openskp.triangulator import triangulate_face_3d

        assert triangulate_face_3d([], (0, 0, 1)) == []
        assert triangulate_face_3d([(0, 0, 0)], (0, 0, 1)) == []
        assert triangulate_face_3d([(0, 0, 0), (1, 0, 0)], (0, 0, 1)) == []

    def test_quad(self) -> None:
        from openskp.triangulator import triangulate_face_3d

        pts = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
        indices = triangulate_face_3d(pts, (0, 0, 1))
        assert len(indices) == 6  # 2 triangles × 3 indices


# ── VFF tests ─────────────────────────────────────────────────────────────


class TestVff:
    """Tests for :mod:`openskp.vff`."""

    def test_validate_header_valid(self) -> None:
        from openskp.vff import validate_header

        data = b"\xFF\xFE\xFF\x0E" + b"\x00" * 100
        assert validate_header(data) is True

    def test_validate_header_invalid(self) -> None:
        from openskp.vff import validate_header

        assert validate_header(b"\x00\x00\x00\x00") is False

    def test_validate_header_too_short(self) -> None:
        from openskp.vff import validate_header

        assert validate_header(b"\xFF\xFE") is False


class TestZipEntrySizeCap:
    """A ZIP entry's declared uncompressed size (ZipInfo.file_size) is
    untrusted central-directory metadata - it can be set independently of
    what the compressed stream actually decompresses to, or (even when
    genuine) DEFLATE can still expand highly compressible data by three
    orders of magnitude. ``zipfile.ZipFile.read()`` decompresses up to
    that declared size with no ceiling of its own, so
    ``_core._validate_zip_entry_size`` is called before every ``zf.read()``
    in the real full_parse() pipeline (not :mod:`openskp.vff`, whose own
    ``extract_skp_contents`` turned out to be dead code - nothing in the
    real parsing path imports it - so the fix lives where ZIP entries are
    actually read).
    """

    @staticmethod
    def _make_zip(name: str, content: bytes, compress: bool = True):
        import io
        import zipfile

        buf = io.BytesIO()
        method = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
        with zipfile.ZipFile(buf, "w", method) as zf:
            zf.writestr(name, content)
        buf.seek(0)
        return zipfile.ZipFile(buf, "r")

    def test_rejects_an_implausible_compression_ratio(self) -> None:
        from openskp._core import _validate_zip_entry_size
        from openskp.errors import SkpParseError

        # 8 MB of zeros deflates to a few hundred bytes - a ratio well past
        # what real (binary geometry) model.dat entries show (~10x), the
        # shape of a declared-size decompression bomb: tiny real payload,
        # huge claimed size.
        zf = self._make_zip("payload.dat", b"\x00" * (8 * 1024 * 1024))
        info = zf.getinfo("payload.dat")
        assert info.compress_size > 0
        assert info.file_size / info.compress_size > 1000

        with pytest.raises(SkpParseError, match="compression ratio"):
            _validate_zip_entry_size(zf, "payload.dat")

    def test_allows_a_realistic_compression_ratio(self) -> None:
        import os

        from openskp._core import _validate_zip_entry_size

        # Pseudo-random content compresses poorly (ratio close to 1x),
        # comfortably under the safety threshold.
        zf = self._make_zip("payload.dat", os.urandom(64 * 1024))
        _validate_zip_entry_size(zf, "payload.dat")  # must not raise

    def test_allows_a_tiny_entry_regardless_of_ratio(self) -> None:
        from openskp._core import _validate_zip_entry_size

        # Below the 1 MB ratio-check threshold, even a high ratio is
        # allowed through - the absolute cost is bounded regardless.
        zf = self._make_zip("payload.dat", b"\x00" * 1024)
        _validate_zip_entry_size(zf, "payload.dat")  # must not raise

    def test_allows_an_empty_entry(self) -> None:
        from openskp._core import _validate_zip_entry_size

        zf = self._make_zip("payload.dat", b"", compress=False)
        _validate_zip_entry_size(zf, "payload.dat")  # must not raise


# ── Materials tests ──────────────────────────────────────────────────────


class TestMaterials:
    """Tests for :mod:`openskp.materials`."""

    def test_parse_color_hex(self) -> None:
        from openskp.materials import _parse_color_string

        assert _parse_color_string("#FF0000") == (255, 0, 0, 255)
        assert _parse_color_string("#00FF00FF") == (0, 255, 0, 255)

    def test_parse_color_csv(self) -> None:
        from openskp.materials import _parse_color_string

        assert _parse_color_string("128,64,32") == (128, 64, 32, 255)
        assert _parse_color_string("128,64,32,200") == (128, 64, 32, 200)

    def test_parse_empty_materials(self) -> None:
        from openskp.materials import parse_materials

        assert parse_materials({}) == []


# ── JSON export tests ────────────────────────────────────────────────────


class TestJsonExport:
    """Tests for :mod:`openskp.export.json_export`."""

    def test_empty_model(self) -> None:
        from openskp.model import SkpModel
        from openskp.export.json_export import to_dict

        model = SkpModel()
        d = to_dict(model)
        assert d["format_version"] == "1.0"
        assert d["sketchup_version"] == "unknown"
        assert d["units"] is None
        assert d["total_definitions"] == 0
        assert d["total_layers"] == 0
        assert d["total_meshes"] == 0
        assert d["root"] == {
            "id": 0, "guid": "", "name": "", "vertex_count": 0,
            "edge_count": 0, "face_count": 0, "vertices": [], "edges": [],
            "faces": [], "instances": [],
        }
        assert d["definitions"] == {}
        assert d["layers"] == []
        assert d["materials"] == []
        assert d["mesh_index"] == {}
        assert d["scene_hierarchy"] is None

    def test_root_is_included_alongside_definitions(self) -> None:
        from openskp.model import Definition, SkpModel
        from openskp.export.json_export import to_dict

        model = SkpModel()
        model.root = Definition(id=0, guid="ROOT", name="ROOT_MODEL")
        d = to_dict(model)
        # root is its own top-level key, not folded into "definitions" -
        # matching SkpModel.root being a separate field, not a
        # definitions[...] entry (see TestDataModel's root tests).
        assert d["root"]["guid"] == "ROOT"
        assert d["root"]["name"] == "ROOT_MODEL"
        assert "ROOT" not in d["definitions"]

    def test_layer_hidden_is_included(self) -> None:
        from openskp.model import Layer, SkpModel
        from openskp.export.json_export import to_dict

        model = SkpModel()
        model.layers.append(Layer(name="Furniture", hidden=True))
        d = to_dict(model)
        assert d["layers"][0]["hidden"] is True

    def test_layer_and_material_color_are_nested_objects(self) -> None:
        # Matches TypeScript's convention (this schema's canonical shape)
        # rather than Python's old flat color_r/color_g/color_b keys for
        # layers or a positional [r, g, b, a] list for materials.
        from openskp.model import Layer, Material, SkpModel
        from openskp.export.json_export import to_dict

        model = SkpModel()
        model.layers.append(Layer(name="Furniture", color_r=10, color_g=20, color_b=30))
        model.materials.append(Material(name="Brick", color=(1, 2, 3, 4)))
        d = to_dict(model)
        assert d["layers"][0]["color"] == {"r": 10, "g": 20, "b": 30}
        assert d["materials"][0]["color"] == {"r": 1, "g": 2, "b": 3, "a": 4}

    def test_definition_includes_full_edges_and_faces(self) -> None:
        # Regression: this used to only include vertex/edge/face *counts*
        # and a flat vertices array - TypeScript's toJSON always included
        # full edges/faces arrays too, so switching ports meant a
        # genuinely different (not just differently-named) shape.
        from openskp.model import Definition, Edge, Face, SkpModel, Vertex

        from openskp.export.json_export import to_dict

        model = SkpModel()
        model.root = Definition(id=0, guid="ROOT", name="ROOT_MODEL")
        model.root.vertices[1] = Vertex(id=1, x=1.0, y=2.0, z=3.0)
        model.root.vertices[2] = Vertex(id=2, x=4.0, y=5.0, z=6.0)
        model.root.edges[10] = Edge(id=10, v1_id=1, v2_id=2)
        model.root.faces[100] = Face(
            id=100, loops=[[(10, 1)]], normal=(0.0, 0.0, 1.0), material_id=5,
        )
        d = to_dict(model)
        assert d["root"]["edges"] == [{"id": 10, "v1_id": 1, "v2_id": 2}]
        assert d["root"]["faces"] == [
            {"id": 100, "loops": [[{"edge_id": 10, "orientation": 1}]], "normal": [0.0, 0.0, 1.0]}
        ]

    def test_instance_does_not_include_dead_layer_properties_children_fields(self) -> None:
        # Instance.layer/Instance.properties/Instance.children are all
        # declared but never assigned during parsing (always "" / {} / []
        # defaults) - see item 17. Encoding them here would present
        # known-dead data as if it were meaningful. The resolved,
        # genuinely nested tree with correct layer/properties is
        # available via scene_hierarchy instead.
        from openskp.model import Definition, Instance, SkpModel
        from openskp.export.json_export import to_dict

        model = SkpModel()
        model.root = Definition(id=0, guid="ROOT", name="ROOT_MODEL")
        model.root.instances.append(Instance(name="child", ref_idx=1))
        d = to_dict(model)
        inst = d["root"]["instances"][0]
        assert "layer" not in inst
        assert "properties" not in inst
        assert "children" not in inst
        assert inst["name"] == "child"
        assert inst["ref_idx"] == 1

    def test_real_file_matches_ground_truth(self) -> None:
        # Cross-checked directly against the TypeScript port's toJSON()
        # on this same fixture - the schema is meant to match exactly.
        import pathlib as _pathlib

        import pytest as _pytest
        from openskp.model import SkpFile
        from openskp.export.json_export import to_dict

        fixture = _pathlib.Path(__file__).parent / "fixtures" / "capilla_quiroz_v17.skp"
        if not fixture.exists():
            _pytest.skip("legacy fixture not present")

        skp = SkpFile.open(str(fixture))
        model = skp.parse()
        d = to_dict(model)

        assert d["total_definitions"] == 2
        assert d["total_layers"] == 1
        assert d["root"]["vertex_count"] == 251
        assert d["root"]["edge_count"] == 390
        assert d["root"]["face_count"] == 146
        assert len(d["root"]["instances"]) == 3

        puerta = next(v for v in d["definitions"].values() if v["name"] == "puerta")
        assert puerta["id"] == 40
        assert puerta["vertex_count"] == 64
        assert puerta["edge_count"] == 95
        assert puerta["face_count"] == 24
        assert len(puerta["edges"]) == 95
        assert len(puerta["faces"]) == 24
        assert set(d["root"]["instances"][0].keys()) == {
            "name", "ref_idx", "guid", "matrix",
        }


# ── SkpFile tests ────────────────────────────────────────────────────────


class TestSkpFile:
    """Tests for :class:`openskp.model.SkpFile`."""

    def test_open_missing_file(self) -> None:
        from openskp.model import SkpFile

        with pytest.raises(FileNotFoundError):
            SkpFile.open("/nonexistent/path/model.skp")

    def test_open_wrong_extension(self, tmp_path: pathlib.Path) -> None:
        from openskp.model import SkpFile

        fake = tmp_path / "test.txt"
        fake.write_text("hello")
        with pytest.raises(ValueError, match="Expected a .skp file"):
            SkpFile.open(str(fake))


# ── Transparency semantics tests ─────────────────────────────────────────


class TestUseTrans:
    """'trans' in material.xml only applies when useTrans="1"."""

    def _skp_with(self, tmp_path, mat_xml: bytes):
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("model.dat", b"")
            zf.writestr("materials/M/material.xml", mat_xml)
        path = tmp_path / "m.skp"
        path.write_bytes(b"\xFF\xFE\xFF\x0E" + b"\x00" * 28 + buf.getvalue())
        return path

    XML = b"""<?xml version="1.0"?>
<materialDocument xmlns="http://sketchup.google.com/schemas/sketchup/1.0/material"
                  xmlns:mat="http://sketchup.google.com/schemas/sketchup/1.0/material">
  <mat:material name="M" colorRed="1" colorGreen="2" colorBlue="3"
                trans="%s" useTrans="%s"/>
</materialDocument>
"""

    def test_use_trans_1_applies(self, tmp_path: pathlib.Path) -> None:
        from openskp.model import SkpFile

        model = SkpFile.open(str(self._skp_with(
            tmp_path, self.XML % (b"0.27", b"1")))).parse()
        # trans stores a TRANSPARENCY; the model exposes the resulting
        # opacity, so trans="0.27" reads back as 0.73.
        assert abs(model.materials[0].transparency - 0.73) < 1e-9

    def test_use_trans_0_means_opaque(self, tmp_path: pathlib.Path) -> None:
        from openskp.model import SkpFile

        # trans="0" with useTrans="0" is a leftover default, NOT invisible.
        model = SkpFile.open(str(self._skp_with(
            tmp_path, self.XML % (b"0", b"0")))).parse()
        assert model.materials[0].transparency == 1.0


# ── UTF-8 entity name tests ──────────────────────────────────────────────


class TestUtf8EntityNames:
    """Entity names must decode as UTF-8, not ASCII-with-ignore.

    SketchUp stores names UTF-8 encoded. Decoding them as ASCII and
    *dropping* the non-ASCII bytes silently corrupts any accented name
    ("cópia" → "cpia", "Diseño" → "Diseo") — and, worse, breaks the
    material-name join between the TLV stream and the XML material files,
    leaving those materials unresolvable.
    """

    @staticmethod
    def _tlv(tag_hex: str, payload: bytes) -> bytes:
        return bytes.fromhex(tag_hex) + struct.pack('<I', len(payload)) + payload

    def test_instance_name_keeps_accents(self) -> None:
        from openskp import _core

        name = "Diseño de árbol".encode("utf-8")
        node = self._tlv('6419', self._tlv('6519', name)
                         + self._tlv('6719', b'\x05'))
        elements = _core.parse_tlv_recursive(
            node + self._tlv('0100', b'\x00'), 0, len(node) + 7)
        builder = _core._GeometryBuilder()
        _core._extract_geometry_from_nodes(elements, builder)

        assert builder.instances[0]['name'] == "Diseño de árbol"


class TestMaterialIdJoin:
    """Tests for the ``Face.material_id`` → :class:`Material` join that
    :meth:`SkpFile.parse` exposes (``Material.id`` +
    ``SkpModel.materials_by_id``).

    ``full_parse`` is stubbed out, so no real ``.skp`` file is needed.
    """

    @staticmethod
    def _parse_with(monkeypatch, tmp_path: pathlib.Path, parsed: dict):
        import openskp._core as _core
        from openskp.model import SkpFile

        monkeypatch.setattr(_core, "full_parse", lambda path: parsed)
        fake = tmp_path / "model.skp"
        fake.write_bytes(b"")
        return SkpFile.open(str(fake)).parse()

    def test_material_id_defaults_to_none(self) -> None:
        from openskp.model import Material

        assert Material(name="Wood").id is None

    def test_face_material_resolves_through_materials_by_id(
        self, monkeypatch, tmp_path: pathlib.Path
    ) -> None:
        parsed = {
            "version": "test",
            "defs_dict": {},
            "layer_colors": {},
            "materials": {
                "Wood": {"name": "Wood",
                         "color": {"r": 10, "g": 20, "b": 30},
                         "transparency": 1.0},
            },
            "materials_by_folder": {},
            "material_id_to_name": {29491: "Wood"},
        }
        model = self._parse_with(monkeypatch, tmp_path, parsed)

        assert model.materials[0].id == 29491
        mat = model.materials_by_id[29491]
        assert mat is model.materials[0]
        assert mat.color == (10, 20, 30, 255)

    def test_folder_alias_resolves_to_same_material(
        self, monkeypatch, tmp_path: pathlib.Path
    ) -> None:
        # The TLV name may match the ZIP folder rather than the XML name —
        # the same name-then-folder fallback the internal exporter uses.
        wood = {"name": "Wood", "color": {"r": 1, "g": 2, "b": 3},
                "transparency": 1.0}
        parsed = {
            "version": "test",
            "defs_dict": {},
            "layer_colors": {},
            "materials": {"Wood": wood},
            "materials_by_folder": {"m0": wood},
            "material_id_to_name": {7: "Wood", 8: "m0"},
        }
        model = self._parse_with(monkeypatch, tmp_path, parsed)

        assert len(model.materials) == 1
        assert model.materials_by_id[7] is model.materials_by_id[8]
        # The first ID seen sticks as the Material's own id; both resolve.
        assert model.materials[0].id in (7, 8)

    def test_unresolvable_id_is_skipped(
        self, monkeypatch, tmp_path: pathlib.Path
    ) -> None:
        parsed = {
            "version": "test",
            "defs_dict": {},
            "layer_colors": {},
            "materials": {},
            "materials_by_folder": {},
            "material_id_to_name": {99: "Ghost"},
        }
        model = self._parse_with(monkeypatch, tmp_path, parsed)

        assert model.materials_by_id == {}


class TestLayerHidden:
    """``Layer.hidden`` - the on/off visibility bit. Already correctly
    extracted from legacy MFC files (``legacy._read_layer``) but previously
    discarded before reaching the public model; now wired through
    ``layer_hidden`` alongside the existing ``layer_colors``/
    ``layer_id_to_name`` dicts. VFF files carry no known visibility tag, so
    they always default to ``False`` (documented on ``Layer.hidden``).

    ``full_parse`` is stubbed out (matching ``TestMaterialIdJoin``'s
    pattern above), since hand-crafting a real hidden-layer legacy file
    isn't practical and the only real fixture available has just one,
    non-hidden layer.
    """

    @staticmethod
    def _parse_with(monkeypatch, tmp_path: pathlib.Path, parsed: dict):
        import openskp._core as _core
        from openskp.model import SkpFile

        monkeypatch.setattr(_core, "full_parse", lambda path: parsed)
        fake = tmp_path / "model.skp"
        fake.write_bytes(b"")
        return SkpFile.open(str(fake)).parse()

    def test_hidden_layer_is_reported_hidden(self, monkeypatch, tmp_path: pathlib.Path) -> None:
        parsed = {
            "version": "test",
            "defs_dict": {},
            "layer_colors": {"Layer0": (136, 136, 136), "Furniture": (200, 50, 50)},
            "layer_hidden": {"Layer0": False, "Furniture": True},
            "materials": {},
            "materials_by_folder": {},
            "material_id_to_name": {},
        }
        model = self._parse_with(monkeypatch, tmp_path, parsed)

        by_name = {layer.name: layer for layer in model.layers}
        assert by_name["Layer0"].hidden is False
        assert by_name["Furniture"].hidden is True

    def test_missing_layer_hidden_dict_defaults_to_visible(
        self, monkeypatch, tmp_path: pathlib.Path
    ) -> None:
        # No layer_hidden key at all (e.g. an older cached parse result) -
        # must default to visible, not raise.
        parsed = {
            "version": "test",
            "defs_dict": {},
            "layer_colors": {"Layer0": (136, 136, 136)},
            "materials": {},
            "materials_by_folder": {},
            "material_id_to_name": {},
        }
        model = self._parse_with(monkeypatch, tmp_path, parsed)

        assert model.layers[0].hidden is False

    def test_real_legacy_fixture_layer_is_not_hidden(self) -> None:
        # Real-fixture sanity check: the only layer in this file is a
        # plain, visible Layer0 - confirms the field is actually populated
        # (not silently dropped) end-to-end through the real legacy parser,
        # even though it can't exercise the True branch.
        import pytest as _pytest
        fixture = pathlib.Path(__file__).parent / "fixtures" / "capilla_quiroz_v17.skp"
        if not fixture.exists():
            _pytest.skip("legacy fixture not present")
        from openskp.model import SkpFile

        model = SkpFile.open(str(fixture)).parse()
        assert len(model.layers) == 1
        assert model.layers[0].hidden is False


class TestFaceInstanceHidden:
    """``Face.hidden`` / ``Instance.hidden`` - the same per-element "Hide"
    bit edges already exposed. Both the legacy MFC drawbase record
    (``_drawbase``'s ``'hidden'`` key) and the VFF/modern D007->D307
    display-flags record (confirmed present on every single face and
    instance in a real VFF fixture - 1588/1588 faces, 46/46 instances in
    Untitled.skp) already carried this bit; it was just discarded when
    building the final ``Face``/``Instance`` objects.

    ``full_parse`` is stubbed for the VFF-shaped case (constructing a
    synthetic ``_GeometryBuilder``); the legacy real-fixture case is
    checked separately below since it can't exercise the True branch.
    """

    @staticmethod
    def _parse_with(monkeypatch, tmp_path: pathlib.Path, parsed: dict):
        import openskp._core as _core
        from openskp.model import SkpFile

        monkeypatch.setattr(_core, "full_parse", lambda path: parsed)
        fake = tmp_path / "model.skp"
        fake.write_bytes(b"")
        return SkpFile.open(str(fake)).parse()

    def test_hidden_face_and_instance_are_reported_hidden(
        self, monkeypatch, tmp_path: pathlib.Path
    ) -> None:
        from openskp._core import _GeometryBuilder

        builder = _GeometryBuilder()
        builder.faces[1] = {"loops": [], "normal": (0.0, 0.0, 1.0), "hidden": True}
        builder.faces[2] = {"loops": [], "normal": (0.0, 0.0, 1.0), "hidden": False}
        builder.instances.append({
            "offset": 0, "ref_guid": "", "ref_idx": None, "name": "hidden_one",
            "matrix": [], "material_id": None, "hidden": True, "children": [],
        })
        builder.instances.append({
            "offset": 0, "ref_guid": "", "ref_idx": None, "name": "visible_one",
            "matrix": [], "material_id": None, "hidden": False, "children": [],
        })
        parsed = {
            "version": "test",
            "defs_dict": {"ROOT": {"guid": "ROOT", "name": "ROOT_MODEL", "builder": builder}},
            "layer_colors": {},
            "materials": {},
            "materials_by_folder": {},
            "material_id_to_name": {},
        }
        model = self._parse_with(monkeypatch, tmp_path, parsed)

        assert model.root.faces[1].hidden is True
        assert model.root.faces[2].hidden is False
        by_name = {i.name: i for i in model.root.instances}
        assert by_name["hidden_one"].hidden is True
        assert by_name["visible_one"].hidden is False

    def test_missing_hidden_key_defaults_to_visible(
        self, monkeypatch, tmp_path: pathlib.Path
    ) -> None:
        from openskp._core import _GeometryBuilder

        builder = _GeometryBuilder()
        builder.faces[1] = {"loops": [], "normal": (0.0, 0.0, 1.0)}
        builder.instances.append({
            "offset": 0, "ref_guid": "", "ref_idx": None, "name": "n",
            "matrix": [], "material_id": None, "children": [],
        })
        parsed = {
            "version": "test",
            "defs_dict": {"ROOT": {"guid": "ROOT", "name": "ROOT_MODEL", "builder": builder}},
            "layer_colors": {},
            "materials": {},
            "materials_by_folder": {},
            "material_id_to_name": {},
        }
        model = self._parse_with(monkeypatch, tmp_path, parsed)

        assert model.root.faces[1].hidden is False
        assert model.root.instances[0].hidden is False

    def test_real_legacy_fixture_faces_and_instances_are_not_hidden(self) -> None:
        # Real-fixture sanity check: nothing in this file is hidden, but
        # this confirms the field is actually populated end-to-end through
        # the real legacy parser (not silently dropped), even though it
        # can't exercise the True branch.
        import pytest as _pytest
        fixture = pathlib.Path(__file__).parent / "fixtures" / "capilla_quiroz_v17.skp"
        if not fixture.exists():
            _pytest.skip("legacy fixture not present")
        from openskp.model import SkpFile

        model = SkpFile.open(str(fixture)).parse()
        assert len(model.definitions) > 0
        for defn in model.definitions.values():
            for face in defn.faces.values():
                assert face.hidden is False
        for inst in model.root.instances:
            assert inst.hidden is False


class TestMetaUnits:
    """``SkpModel.units`` - the model's unit-system string, read from
    ``meta/meta.dat`` in VFF files. Never opened by any parser before this
    (zero references to the filename anywhere in the codebase). Confirmed
    plaintext payload in a real fixture (Untitled.skp): ``meta.dat`` uses
    the same low-level TLV framing as ``model.dat`` (2-byte tag + 4-byte
    little-endian length + payload), one flat record list wrapped in a
    single outer record (tag 0x6400); tag 0x6D00 carries the units string
    as plain text, alongside sibling tags for the SketchUp version, save
    path, and thumbnail references that no parser surfaces either.
    """

    @staticmethod
    def _tlv(tag: bytes, payload: bytes) -> bytes:
        return tag + struct.pack('<I', len(payload)) + payload

    def test_extracts_units_from_real_fixture_bytes(self) -> None:
        # The exact 388-byte meta/meta.dat payload from a real VFF fixture
        # (Untitled.skp, SketchUp 25.0.575) - byte-for-byte, not
        # hand-crafted. Confirms _read_meta_units against genuine data,
        # not just a minimal synthetic record.
        from openskp._core import _read_meta_units

        real_meta_dat = (
            b'd\x00~\x01\x00\x00u\x00\x08\x00\x00\x0025.0.575v\x00\x02\x00\x00\x00\x18\x00'
            b'w\x00\x02\x00\x00\x00\x02\x00s\x00\x02\x00\x00\x00\x01\x00t\x00\x02\x00\x00'
            b'\x00\x11\x00f\x00\x10\x00\x00\x00\xdc\xd4u*8=rG\x83\x02/\xa2\x9c\xda2$'
            b'g\x00.\x00\x00\x00(#(\x00\x00\x00)#\x04\x00\x00\x00\x04\x00\x00\x00'
            b'*#\x18\x00\x00\x00meta/model_thumbnail.png'
            b'h\x000\x00\x00\x00(#*\x00\x00\x00)#\x04\x00\x00\x00\x04\x00\x00\x00'
            b'*#\x1a\x00\x00\x00meta/preview_thumbnail.png'
            b'i\x00\x01\x00\x00\x00\x01j\x00\x00\x00\x00\x00k\x00\x00\x00\x00\x00'
            b'l\x00\x00\x00\x00\x00n\x00\x00\x00\x00\x00q\x00\x01\x00\x00\x00\x00'
            b'y\x00\x01\x00\x00\x00\x00r\x00\x01\x00\x00\x00\x00'
            b'm\x00\n\x00\x00\x00Millimeter'
            b'p\x00\x01\x00\x00\x00\x01'
            b"o\x00'\x00\x00\x00E:/Devs/TEst/Skp Test/ref2/Untitled.skp"
            b'x\x00R\x00\x00\x00\xc8\x00L\x00\x00\x00\xc9\x00F\x00\x00\x00\xca\x00'
            b'@\x00\x00\x00\xcb\x00"\x00\x00\x00SketchUp Client (Windows) 25.0.575'
            b'\xcc\x00\x04\x00\x00\x00#\xc52j'
            b'\xcd\x00\x08\x00\x00\x00\xecD=\xc9\xb4\xdb\x98w'
        )
        assert _read_meta_units(real_meta_dat) == 'Millimeter'

    def test_extracts_units_from_minimal_synthetic_record(self) -> None:
        from openskp._core import _read_meta_units

        inner = self._tlv(b'\x6D\x00', b'Inches')
        outer = self._tlv(b'\x64\x00', inner)
        assert _read_meta_units(outer) == 'Inches'

    def test_returns_none_when_units_tag_absent(self) -> None:
        from openskp._core import _read_meta_units

        inner = self._tlv(b'\x75\x00', b'25.0.575')  # version tag, not units
        outer = self._tlv(b'\x64\x00', inner)
        assert _read_meta_units(outer) is None

    def test_returns_none_for_empty_or_truncated_bytes(self) -> None:
        from openskp._core import _read_meta_units

        assert _read_meta_units(b'') is None
        assert _read_meta_units(b'\x01\x02\x03') is None

    def test_model_units_wired_from_parsed_dict(self, monkeypatch, tmp_path: pathlib.Path) -> None:
        import openskp._core as _core
        from openskp.model import SkpFile

        parsed = {
            "version": "test",
            "defs_dict": {},
            "layer_colors": {},
            "materials": {},
            "materials_by_folder": {},
            "material_id_to_name": {},
            "units": "Millimeter",
        }
        monkeypatch.setattr(_core, "full_parse", lambda path: parsed)
        fake = tmp_path / "model.skp"
        fake.write_bytes(b"")
        model = SkpFile.open(str(fake)).parse()

        assert model.units == "Millimeter"

    def test_model_units_defaults_to_none_when_absent(self, monkeypatch, tmp_path: pathlib.Path) -> None:
        import openskp._core as _core
        from openskp.model import SkpFile

        parsed = {
            "version": "test",
            "defs_dict": {},
            "layer_colors": {},
            "materials": {},
            "materials_by_folder": {},
            "material_id_to_name": {},
        }
        monkeypatch.setattr(_core, "full_parse", lambda path: parsed)
        fake = tmp_path / "model.skp"
        fake.write_bytes(b"")
        model = SkpFile.open(str(fake)).parse()

        assert model.units is None

    def test_real_legacy_fixture_has_no_units(self) -> None:
        # Legacy (pre-2021 MFC) files carry no meta/meta.dat container -
        # confirms this returns None cleanly rather than raising.
        import pytest as _pytest
        fixture = pathlib.Path(__file__).parent / "fixtures" / "capilla_quiroz_v17.skp"
        if not fixture.exists():
            _pytest.skip("legacy fixture not present")
        from openskp.model import SkpFile

        model = SkpFile.open(str(fixture)).parse()
        assert model.units is None


# ── Instance material tests ──────────────────────────────────────────────


class TestInstanceMaterial:
    """Tests for instance-level materials (``Instance.material_id``)."""

    @staticmethod
    def _tlv(tag_hex: str, payload: bytes) -> bytes:
        return bytes.fromhex(tag_hex) + struct.pack('<I', len(payload)) + payload

    def test_instance_material_defaults_to_none(self) -> None:
        from openskp.model import Instance

        assert Instance().material_id is None

    def test_extractor_reads_instance_d007_material(self) -> None:
        # A 6419 instance node carrying D007/D107 — the same material
        # structure faces use ("paint the component" in SketchUp).
        from openskp import _core

        d107 = self._tlv('D107', bytes([0x33, 0x73]))          # id 0x7333
        d007 = self._tlv('D007', d107)
        ref = self._tlv('6719', bytes([0x05]))                  # ref_idx 5
        matrix = self._tlv('6619', struct.pack('<13d', *([1.0] * 13)))
        node = self._tlv('6419', ref + matrix + d007)

        elements = _core.parse_tlv_recursive(
            node + self._tlv('0100', b'\x00'), 0, len(node) + 7)
        builder = _core._GeometryBuilder()
        _core._extract_geometry_from_nodes(elements, builder)

        assert len(builder.instances) == 1
        inst = builder.instances[0]
        assert inst['ref_idx'] == 5
        assert inst['material_id'] == 0x7333

    def test_instance_without_material_stays_none(self) -> None:
        from openskp import _core

        ref = self._tlv('6719', bytes([0x05]))
        node = self._tlv('6419', ref)
        elements = _core.parse_tlv_recursive(
            node + self._tlv('0100', b'\x00'), 0, len(node) + 7)
        builder = _core._GeometryBuilder()
        _core._extract_geometry_from_nodes(elements, builder)

        assert builder.instances[0]['material_id'] is None


# ── Style tests ──────────────────────────────────────────────────────────


class TestStyles:
    """Face colors from styles/*/style.xml (items 4000 front / 4001 back)."""

    def test_style_colors_via_synthetic_skp(self, tmp_path: pathlib.Path) -> None:
        import io
        import zipfile
        from openskp.model import SkpFile

        style_xml = b"""<?xml version="1.0"?>
<styleDocument xmlns="http://sketchup.google.com/schemas/sketchup/1.0/style"
               xmlns:sty="http://sketchup.google.com/schemas/sketchup/1.0/style">
  <sty:style xmlns:t="http://sketchup.google.com/schemas/1.0/types" name="Verde">
    <sty:item id="4000"><t:variant type="4">-3552052</t:variant></sty:item>
    <sty:item id="4001"><t:variant type="4">-3093050</t:variant></sty:item>
  </sty:style>
</styleDocument>
"""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("model.dat", b"")
            zf.writestr("styles/Verde/style.xml", style_xml)
        path = tmp_path / "s.skp"
        path.write_bytes(b"\xFF\xFE\xFF\x0E" + b"\x00" * 28 + buf.getvalue())

        model = SkpFile.open(str(path)).parse()
        assert len(model.styles) == 1
        st = model.styles[0]
        assert st.name == "Verde"
        # -3552052 -> 0xFFC9CCCC-ish ARGB: decode matches int32 & 0xFFFFFF
        v = (-3552052) & 0xFFFFFFFF
        assert st.front_color == ((v >> 16) & 255, (v >> 8) & 255, v & 255)
        v2 = (-3093050) & 0xFFFFFFFF
        assert st.back_color == ((v2 >> 16) & 255, (v2 >> 8) & 255, v2 & 255)


class TestXmlEntityExpansion:
    """material.xml/style.xml come from inside the .skp's ZIP container -
    fully attacker-controlled input. Stdlib xml.etree.ElementTree expands
    internal general entities with no limit, making a "billion laughs" DoS
    payload trivial to embed. _core.py and materials.py both switched to
    defusedxml.ElementTree, which rejects any entity declaration outright
    (not just recursive/exponential ones - confirmed directly: even a
    single non-recursive `<!ENTITY x "hi">` is blocked, so this doesn't
    require actually triggering an expansion to prove the fix works)."""

    # A single, non-recursive entity declaration is enough to trigger
    # defusedxml's EntitiesForbidden - safe to construct directly (no
    # actual expansion ever happens, unlike a real billion-laughs payload).
    _ENTITY_BOMB = b"""<?xml version="1.0"?>
<!DOCTYPE r [ <!ENTITY x "hi"> ]>
<r>&x;</r>
"""

    def test_parse_material_xml_rejects_entity_declarations(self) -> None:
        from openskp.materials import _parse_material_xml

        assert _parse_material_xml(self._ENTITY_BOMB) is None

    def test_full_parse_skips_malicious_material_and_style_xml(
        self, tmp_path: pathlib.Path
    ) -> None:
        import io
        import zipfile
        from openskp.model import SkpFile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("model.dat", b"")
            zf.writestr("materials/Evil/material.xml", self._ENTITY_BOMB)
            zf.writestr("styles/Evil/style.xml", self._ENTITY_BOMB)
        path = tmp_path / "evil.skp"
        path.write_bytes(b"\xFF\xFE\xFF\x0E" + b"\x00" * 28 + buf.getvalue())

        # Must not hang, crash, or leak the exception - the malicious
        # entries are silently skipped, exactly like a malformed
        # (non-well-formed) material.xml/style.xml already was.
        model = SkpFile.open(str(path)).parse()
        assert model.materials == []
        assert model.styles == []


# ── Per-face UV transform tests ──────────────────────────────────────────


class TestFaceUvTransform:
    """Tests for positioned-texture mapping extraction (``Face.uv_transform``)."""

    ROT90 = (0.0, 1.0, 0.0, -1.0, 0.0, 0.0, 96.0, -96.0, 1.0)

    @staticmethod
    def _tlv(tag_hex: str, payload: bytes) -> bytes:
        return bytes.fromhex(tag_hex) + struct.pack('<I', len(payload)) + payload

    def _dc05(self, front=None, back=None) -> bytes:
        t = self._tlv
        def side(tag, mat):
            m1527 = t('1527', struct.pack('<9d', *mat))
            return t(tag, t('1327', t('1427', b'\x01') + m1527))
        inner = b''
        if front is not None:
            inner += side('1127', front)
        if back is not None:
            inner += side('1227', back)
        t1027 = t('1027', inner)
        return (t('DE05', b'\x2A')
                + t('DD05', t('B136', t('B236', t1027))))

    def test_extracts_front_matrix(self) -> None:
        from openskp._core import _extract_uv_transforms

        front, back = _extract_uv_transforms(self._dc05(front=self.ROT90))
        assert front == pytest.approx(self.ROT90)
        assert back is None

    def test_extracts_both_sides(self) -> None:
        from openskp._core import _extract_uv_transforms

        other = tuple(v * 2 for v in self.ROT90)
        front, back = _extract_uv_transforms(
            self._dc05(front=self.ROT90, back=other))
        assert front == pytest.approx(self.ROT90)
        assert back == pytest.approx(other)

    def test_untouched_texture_has_no_transform(self) -> None:
        from openskp._core import _extract_uv_transforms
        from openskp.model import Face

        t = self._tlv
        plain = t('DE05', b'\x2A')      # entity id only, no DD05 block
        assert _extract_uv_transforms(plain) == (None, None)
        assert Face(id=0).uv_transform is None
        assert Face(id=0).uv_transform_back is None

    def test_recipe_reproduces_known_uvs(self) -> None:
        # Ground truth from a controlled SketchUp file: a 1x1 m square on the
        # ground with the texture rotated 90 deg (48x48 in tile). The stored
        # matrix maps texture->plane; UV = [x, y, 1] @ inv(M) / tile.
        import numpy as np

        m = np.array(self.ROT90).reshape(3, 3)
        minv = np.linalg.inv(m)
        tile = 48.0
        for (x, y), (u_t, v_t) in [
            ((82.64, 0.0), (2.0, 0.2784)),
            ((122.01, 0.0), (2.0, -0.5418)),
            ((82.64, 39.37), (2.8202, 0.2784)),
        ]:
            uvq = np.array([x, y, 1.0]) @ minv
            u = uvq[0] / uvq[2] / tile
            v = uvq[1] / uvq[2] / tile
            assert u == pytest.approx(u_t, abs=2e-3)
            assert v == pytest.approx(v_t, abs=2e-3)


# ── Back material tests ──────────────────────────────────────────────────


class TestBackMaterial:
    """The AF0D child of a face node is the material of its BACK side."""

    @staticmethod
    def _tlv(tag_hex: str, payload: bytes) -> bytes:
        return bytes.fromhex(tag_hex) + struct.pack('<I', len(payload)) + payload

    def test_back_material_extracted(self) -> None:
        from openskp import _core

        t = self._tlv
        node = t('AC0D', (t('DC05', t('DE05', b'\x2A'))
                          + t('AF0D', bytes([0x85, 0x8B, 0x06]))))
        elements = _core.parse_tlv_recursive(
            node + t('0100', b'\x00'), 0, len(node) + 7)
        builder = _core._GeometryBuilder()
        _core._extract_geometry_from_nodes(elements, builder)

        assert 0x2A in builder.faces
        f = builder.faces[0x2A]
        assert f['material_id'] is None          # front unpainted
        assert f['back_material_id'] == 0x68B85  # back painted

    def test_face_defaults(self) -> None:
        from openskp.model import Face

        assert Face(id=0).back_material_id is None


# ── Face-camera behavior tests ───────────────────────────────────────────


class TestFaceCameraBehavior:
    """Component behavior flag 5D1B inside the definition's 581B block marks
    SketchUp's "always face camera" (2D people / tree cut-outs)."""

    @staticmethod
    def _tlv(tag_hex: str, payload: bytes) -> bytes:
        return bytes.fromhex(tag_hex) + struct.pack('<I', len(payload)) + payload

    def _def_node(self, flags: bytes) -> bytes:
        t = self._tlv
        return t('7C15', t('7E15', b'Susan') + t('581B', flags))

    def test_flag_set(self) -> None:
        from openskp import _core

        t = self._tlv
        flags = t('5B1B', b'\x00') + t('5D1B', b'\x01') + t('5E1B', b'\x01')
        node = self._def_node(flags)
        elements = _core.parse_tlv_recursive(
            node + t('0100', b'\x00'), 0, len(node) + 7)
        # run the def-collection path via full parse plumbing: emulate by
        # scanning like collect_defs does — simplest is a synthetic file, but
        # the flag logic is inline; exercise it through a minimal .skp below.
        assert elements[0]['tag'] == '7C15'

    def test_flag_via_synthetic_skp(self, tmp_path: pathlib.Path) -> None:
        import io
        import zipfile
        from openskp.model import SkpFile

        t = self._tlv
        on = t('7C15', (t('7D15', b'\x11' * 16) + t('7E15', b'Susan')
                        + t('581B', t('5D1B', b'\x01') + t('5E1B', b'\x01'))
                        + t('DC05', t('DE05', b'\x05'))))
        off = t('7C15', (t('7D15', b'\x22' * 16) + t('7E15', b'Silla')
                         + t('581B', t('5D1B', b'\x00') + t('5E1B', b'\x00'))
                         + t('DC05', t('DE05', b'\x06'))))
        model_dat = t('F901', t('7017', t('7117', on + off)))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('model.dat', model_dat)
        path = tmp_path / 's.skp'
        path.write_bytes(b'\xFF\xFE\xFF\x0E' + b'\x00' * 28 + buf.getvalue())

        model = SkpFile.open(str(path)).parse()
        by_name = {d.name: d for d in model.definitions.values()}
        assert by_name['Susan'].always_faces_camera is True
        assert by_name['Susan'].shadows_face_sun is True
        assert by_name['Silla'].always_faces_camera is False
        assert by_name['Silla'].shadows_face_sun is False


class TestSectionPlaneTextDimension:
    """Definition metadata for section planes, text entities, and dimensions."""

    def test_default_lists_empty(self) -> None:
        from openskp.model import Definition, SectionPlane, TextEntity, Dimension
        d = Definition()
        assert d.section_planes == []
        assert d.texts == []
        assert d.dimensions == []

        sp = SectionPlane(plane=[0.0, 0.0, 1.0, 0.0], name="Cut 1", label="A")
        assert sp.name == "Cut 1"
        assert sp.hidden is False

        txt = TextEntity(text="Note")
        assert txt.text == "Note"

        dim = Dimension(text="100mm")
        assert dim.text == "100mm"


class TestObjExporter:
    """Wavefront OBJ and MTL text exporter."""

    def test_to_obj_exports_primitives(self) -> None:
        from array import array
        from openskp.scene import Scene, GlbPrimitive
        from openskp.export.obj import to_obj, to_mtl

        prim = GlbPrimitive(
            geom_name="Box",
            material_index=0,
            positions=array("f", [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0]),
            normals=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            uvs=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            indices=array("I", [0, 1, 2]),
        )
        scene = Scene(
            glb_primitives=[prim],
            gltf_materials=[{"name": "Red_Material", "pbrMetallicRoughness": {"baseColorFactor": [1.0, 0.0, 0.0, 1.0]}}]
        )
        obj_text = to_obj(scene, "materials.mtl")
        assert "# OpenSKP OBJ Export" in obj_text
        assert "mtllib materials.mtl" in obj_text
        assert "o Box" in obj_text
        assert "v 0.000000 0.000000 0.000000" in obj_text
        assert "vt 0.000000 0.000000" in obj_text
        assert "vn 0.000000 0.000000 1.000000" in obj_text
        assert "usemtl Red_Material" in obj_text
        assert "f 1/1/1 2/2/2 3/3/3" in obj_text

        mtl_text = to_mtl(scene)
        assert "# OpenSKP MTL Material Library Export" in mtl_text
        assert "newmtl Red_Material" in mtl_text
        assert "Kd 1.000000 0.000000 0.000000" in mtl_text


class TestStlExporter:
    """STL exporter tests (ASCII & Binary)."""

    def test_to_stl_ascii(self) -> None:
        from array import array
        from openskp.scene import Scene, GlbPrimitive
        from openskp.export.stl import to_stl_ascii

        prim = GlbPrimitive(
            geom_name="Box",
            material_index=0,
            positions=array("f", [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0]),
            normals=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            uvs=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            indices=array("I", [0, 1, 2]),
        )
        scene = Scene(glb_primitives=[prim])
        stl_text = to_stl_ascii(scene, scale=1.0)
        assert "solid OpenSKP_Model" in stl_text
        assert "facet normal 0.000000 0.000000 1.000000" in stl_text
        assert "vertex 0.000000 0.000000 0.000000" in stl_text
        assert "endsolid OpenSKP_Model" in stl_text

    def test_to_stl_binary(self) -> None:
        from array import array
        from openskp.scene import Scene, GlbPrimitive
        from openskp.export.stl import to_stl_binary

        prim = GlbPrimitive(
            geom_name="Box",
            material_index=0,
            positions=array("f", [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0]),
            normals=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            uvs=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            indices=array("I", [0, 1, 2]),
        )
        scene = Scene(glb_primitives=[prim])
        data = to_stl_binary(scene, scale=1.0)
        assert len(data) == 80 + 4 + 50  # Header + uint32 count + 1 triangle
        assert data.startswith(b"# OpenSKP Binary STL Export")


class TestPlyExporter:
    """PLY exporter tests (ASCII & Binary)."""

    def test_to_ply_ascii(self) -> None:
        from array import array
        from openskp.scene import Scene, GlbPrimitive
        from openskp.export.ply import to_ply_ascii

        prim = GlbPrimitive(
            geom_name="Box",
            material_index=0,
            positions=array("f", [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0]),
            normals=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            uvs=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            indices=array("I", [0, 1, 2]),
        )
        scene = Scene(glb_primitives=[prim])
        ply_text = to_ply_ascii(scene)
        assert "format ascii 1.0" in ply_text
        assert "element vertex 3" in ply_text
        assert "element face 1" in ply_text
        assert "3 0 1 2" in ply_text

    def test_to_ply_binary(self) -> None:
        from array import array
        from openskp.scene import Scene, GlbPrimitive
        from openskp.export.ply import to_ply_binary

        prim = GlbPrimitive(
            geom_name="Box",
            material_index=0,
            positions=array("f", [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0]),
            normals=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            uvs=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            indices=array("I", [0, 1, 2]),
        )
        scene = Scene(glb_primitives=[prim])
        data = to_ply_binary(scene)
        assert b"format binary_little_endian 1.0" in data
        assert b"element vertex 3" in data
        assert b"element face 1" in data


class TestDxfExporter:
    """DXF 3D exporter tests."""

    def test_to_dxf(self) -> None:
        from array import array
        from openskp.scene import Scene, GlbPrimitive
        from openskp.export.dxf import to_dxf

        prim = GlbPrimitive(
            geom_name="Walls",
            material_index=0,
            positions=array("f", [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0]),
            normals=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            uvs=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            indices=array("I", [0, 1, 2]),
        )
        scene = Scene(glb_primitives=[prim])
        dxf_text = to_dxf(scene)
        assert "$ACADVER" in dxf_text
        assert "AC1015" in dxf_text
        assert "POLYLINE" in dxf_text or "3DFACE" in dxf_text
        assert "Walls" in dxf_text
        assert "EOF" in dxf_text

        dxf_poly = to_dxf(scene, mode="polyface")
        assert "POLYLINE" in dxf_poly or "AcDbPolyFaceMesh" in dxf_poly

        dxf_3d = to_dxf(scene, mode="3dface")
        assert "3DFACE" in dxf_3d
        assert "AcDbFace" in dxf_3d


# ── Image entity tests ───────────────────────────────────────────────────


class TestImageEntities:
    """Image entities: a picture placed in the model wraps a standard 6419
    instance inside ``9013 → 401F``, and its backing definition carries
    kind ``8315 == 2``."""

    @staticmethod
    def _tlv(tag_hex: str, payload: bytes) -> bytes:
        return bytes.fromhex(tag_hex) + struct.pack('<I', len(payload)) + payload

    def test_image_placement_instance_is_extracted(self) -> None:
        from openskp import _core

        t = self._tlv
        inner_6419 = t('6419', t('6719', b'\x07'))       # ref_idx 7
        node = t('9013', t('401F', inner_6419))
        elements = _core.parse_tlv_recursive(
            node + t('0100', b'\x00'), 0, len(node) + 7)
        builder = _core._GeometryBuilder()
        _core._extract_geometry_from_nodes(elements, builder)

        assert len(builder.instances) == 1
        assert builder.instances[0]['ref_idx'] == 7

    def test_definition_kind_2_marks_is_image(self, tmp_path: pathlib.Path,
                                              monkeypatch) -> None:
        from openskp.model import Definition, SkpFile
        import openskp._core as _core

        class _B:
            vertices = {}
            edges = {}
            faces = {}
            instances = []

        parsed = {
            "version": "test",
            "defs_dict": {
                1: {"guid": "", "name": "imagen#1", "is_image": True,
                    "builder": _B()},
                2: {"guid": "", "name": "Grupo", "is_image": False,
                    "builder": _B()},
            },
            "layer_colors": {},
            "materials": {},
            "materials_by_folder": {},
            "material_id_to_name": {},
        }
        monkeypatch.setattr(_core, "full_parse", lambda path: parsed)
        fake = tmp_path / "m.skp"
        fake.write_bytes(b"")
        model = SkpFile.open(str(fake)).parse()

        assert model.definitions[1].is_image is True
        assert model.definitions[2].is_image is False
        assert Definition().is_image is False


# ── Texture extraction tests ─────────────────────────────────────────────


_MATERIAL_XML_TEXTURED = b"""<?xml version="1.0" encoding="UTF-8"?>
<materialDocument xmlns="http://sketchup.google.com/schemas/sketchup/1.0/material"
                  xmlns:mat="http://sketchup.google.com/schemas/sketchup/1.0/material">
  <mat:material name="%s" type="1" colorRed="10" colorGreen="20"
                colorBlue="30" trans="1" hasTexture="1">
    <mat:texture textureFilename="%s" xScale="24" yScale="12"/>
  </mat:material>
</materialDocument>
"""

_MATERIAL_XML_PLAIN = b"""<?xml version="1.0" encoding="UTF-8"?>
<materialDocument xmlns="http://sketchup.google.com/schemas/sketchup/1.0/material"
                  xmlns:mat="http://sketchup.google.com/schemas/sketchup/1.0/material">
  <mat:material name="Plain" type="0" colorRed="1" colorGreen="2"
                colorBlue="3" trans="1" hasTexture="0"/>
</materialDocument>
"""


def _write_synthetic_skp(tmp_path: "pathlib.Path",
                         entries: dict) -> "pathlib.Path":
    """Build a minimal ``.skp``: the UTF-16 header marker followed by an
    embedded ZIP with ``model.dat`` plus *entries*."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("model.dat", b"")
        for name, data in entries.items():
            zf.writestr(name, data)
    path = tmp_path / "synthetic.skp"
    path.write_bytes(b"\xFF\xFE\xFF\x0E" + b"\x00" * 28 + buf.getvalue())
    return path


class TestTextureExtraction:
    """Tests for material texture extraction (``Material.texture``)."""

    def test_texture_dataclass_save(self, tmp_path: "pathlib.Path") -> None:
        from openskp.model import Texture

        tex = Texture(filename="wood.jpg", width=24.0, height=12.0,
                      data=b"\xff\xd8fakejpeg")
        out = tex.save(tmp_path / "out.jpg")
        assert out.read_bytes() == b"\xff\xd8fakejpeg"

    def test_texture_save_without_data_raises(self) -> None:
        from openskp.model import Texture

        with pytest.raises(ValueError, match="no image data"):
            Texture(filename="missing.jpg").save("/tmp/never-written.jpg")

    def test_textured_material_roundtrip(self, tmp_path: "pathlib.Path") -> None:
        from openskp.model import SkpFile

        jpeg = b"\xff\xd8syntheticjpegbytes"
        skp = _write_synthetic_skp(tmp_path, {
            "materials/Wood/material.xml":
                _MATERIAL_XML_TEXTURED % (b"Wood", b"wood.jpg"),
            "materials/Wood/wood.jpg": jpeg,
            "materials/Plain/material.xml": _MATERIAL_XML_PLAIN,
        })
        model = SkpFile.open(str(skp)).parse()

        by_name = {m.name: m for m in model.materials}
        wood = by_name["Wood"]
        assert wood.texture is not None
        assert wood.texture.filename == "wood.jpg"
        assert wood.texture.width == 24.0    # inches, from xScale
        assert wood.texture.height == 12.0
        assert wood.texture.data == jpeg
        assert by_name["Plain"].texture is None

    def test_image_name_mismatch_falls_back_to_sibling(
        self, tmp_path: "pathlib.Path"
    ) -> None:
        # Observed in real files: the XML says "..._Safety.jpg" while the
        # stored image is "..._Saftey.jpg" — the folder sibling must win.
        from openskp.model import SkpFile

        jpeg = b"\xff\xd8siblingbytes"
        skp = _write_synthetic_skp(tmp_path, {
            "materials/Glass/material.xml":
                _MATERIAL_XML_TEXTURED % (b"Glass", b"glass_safety.jpg"),
            "materials/Glass/glass_saftey.jpg": jpeg,
        })
        model = SkpFile.open(str(skp)).parse()

        glass = {m.name: m for m in model.materials}["Glass"]
        assert glass.texture is not None
        assert glass.texture.data == jpeg

    def test_colorized_copy_shares_source_image(
        self, tmp_path: "pathlib.Path"
    ) -> None:
        # A colourized material ("[Name]1", type="2") stores no image of
        # its own — its <mat:image path> points into the SOURCE material's
        # folder. The shared bytes must be resolved, and the colorized
        # flag exposed so viewers can re-tint.
        from openskp.model import SkpFile

        png = b"\x89PNGsharedchainlink"
        colorized_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<materialDocument xmlns="http://sketchup.google.com/schemas/sketchup/1.0/material"
                  xmlns:mat="http://sketchup.google.com/schemas/sketchup/1.0/material">
  <mat:material name="[Fence]1" type="2" colorRed="27" colorGreen="135"
                colorBlue="59" colorizeType="0" trans="1" hasTexture="1">
    <mat:texture textureFilename="fence.png" xScale="2.75" yScale="2.75">
      <mat:images>
        <mat:image id="1" path="materials/Fence/fence.png"
                   file_name="fence.png"/>
      </mat:images>
    </mat:texture>
  </mat:material>
</materialDocument>
"""
        skp = _write_synthetic_skp(tmp_path, {
            "materials/Fence/material.xml":
                _MATERIAL_XML_TEXTURED % (b"Fence", b"fence.png"),
            "materials/Fence/fence.png": png,
            "materials/[Fence]1/material.xml": colorized_xml,
        })
        model = SkpFile.open(str(skp)).parse()

        by_name = {m.name: m for m in model.materials}
        copy = by_name["[Fence]1"]
        assert copy.texture is not None
        assert copy.texture.data == png       # borrowed from materials/Fence/
        assert copy.colorized is True
        assert copy.colorize_type == 0
        assert copy.color[:3] == (27, 135, 59)
        base = by_name["Fence"]
        assert base.colorized is False


# ── Legacy (classic MFC) container tests ─────────────────────────────────


class TestLegacyDetection:
    """Tests for :func:`openskp.legacy.is_legacy`."""

    def test_classic_header_detected(self) -> None:
        from openskp.legacy import is_legacy

        data = (b"\xFF\xFE\xFF\x0E" + "SketchUp Model".encode("utf-16-le")
                + b"\xFF\xFE\xFF\x0C" + "{16.0.19912}".encode("utf-16-le")
                + b"\x00" * 0x20 + b"\xFF\xFF\x00\x00\x0B\x00CVersionMap")
        assert is_legacy(data) is True

    def test_vff_zip_not_legacy(self) -> None:
        from openskp.legacy import is_legacy

        data = (b"\xFF\xFE\xFF\x0E" + "SketchUp Model".encode("utf-16-le")
                + b"\xFF\xFE\xFF\x08" + "{26.2.0}".encode("utf-16-le")
                + b"VFF\x08" + b"PK\x03\x04" + b"\x00" * 64)
        assert is_legacy(data) is False

    def test_random_bytes_not_legacy(self) -> None:
        from openskp.legacy import is_legacy

        assert is_legacy(b"\x00" * 64) is False


class TestLegacyStrings:
    """Tests for the MFC string records (:class:`openskp.legacy._R`)."""

    def test_short_string(self) -> None:
        from openskp.legacy import _R

        r = _R(b"\xFF\xFE\xFF\x04" + "Casa".encode("utf-16-le"))
        assert r.utf16() == "Casa"

    def test_empty_string(self) -> None:
        from openskp.legacy import _R

        r = _R(b"\xFF\xFE\xFF\x00")
        assert r.utf16() == ""

    def test_escalated_length(self) -> None:
        from openskp.legacy import _R
        import struct as _s

        text = "x" * 300
        r = _R(b"\xFF\xFE\xFF\xFF" + _s.pack("<H", 300)
               + text.encode("utf-16-le"))
        assert r.utf16() == text

    def test_not_a_string_raises(self) -> None:
        import pytest as _pytest
        from openskp.legacy import _R, LegacyParseError

        with _pytest.raises(LegacyParseError):
            _R(b"\x00\x00\x00\x00").utf16()


# ── Legacy real-file regression (binary fixture) ─────────────────────────


def _assert_connected_normalized_loops(definitions) -> None:
    """Every face loop's coedges must (1) carry a normalized +1/-1
    orientation (the public contract - see ``Face.loops`` docs) and (2)
    actually connect head-to-tail: the vertex the coedge ENDS at
    (accounting for its own orientation) must be where the next coedge in
    the loop STARTS. A loop that "parses" without this holding describes
    disconnected or misordered geometry - counting coedges or checking
    their type alone does not catch a flipped orientation bit, since a
    face with reversed coedges still has the same edge count and loop
    length, just the wrong winding."""
    for d in definitions:
        for face in d.faces.values():
            for loop in face.loops:
                assert loop, "empty loop"
                n = len(loop)
                for i in range(n):
                    edge_id, orientation = loop[i]
                    next_edge_id, next_orientation = loop[(i + 1) % n]
                    assert orientation in (1, -1)
                    edge = d.edges[edge_id]
                    next_edge = d.edges[next_edge_id]
                    end = edge.v2_id if orientation == 1 else edge.v1_id
                    next_start = (next_edge.v1_id if next_orientation == 1
                                  else next_edge.v2_id)
                    assert end == next_start


class TestLegacyRealFile:
    """Decode a real classic (v17 MFC) ``.skp`` end to end and assert the
    geometry against known ground truth.

    Fixture: ``fixtures/capilla_quiroz_v17.skp`` — a small chapel authored in
    SketchUp 2017 (v17.0.18899, ~212 KB), contributed by Marco Sumari
    (IngeTrazo). The expected counts were cross-validated against the same
    model re-saved as VFF by SketchUp Web: exact face/edge counts, total
    surface area and bounding box match between the two formats.
    """

    import pathlib as _pathlib

    FIXTURE = _pathlib.Path(__file__).parent / "fixtures" / "capilla_quiroz_v17.skp"

    def _model(self):
        import pytest as _pytest
        if not self.FIXTURE.exists():
            _pytest.skip("legacy fixture not present")
        from openskp.model import SkpFile
        return SkpFile.open(str(self.FIXTURE)).parse()

    def test_version_and_counts(self) -> None:
        model = self._model()
        assert model.version == "{17.0.18899}"
        # root carries its own top-level geometry alongside the named
        # component definitions - both must be counted for the full totals.
        all_defs = list(model.definitions.values()) + [model.root]
        n_faces = sum(len(d.faces) for d in all_defs)
        n_edges = sum(len(d.edges) for d in all_defs)
        n_verts = sum(len(d.vertices) for d in all_defs)
        assert n_faces == 181
        assert n_edges == 515
        assert n_verts == 335
        assert len(model.materials) == 16

    def test_bounding_box(self) -> None:
        model = self._model()
        lo = [float("inf")] * 3
        hi = [float("-inf")] * 3
        for d in list(model.definitions.values()) + [model.root]:
            for v in d.vertices.values():
                for i, c in enumerate((v.x, v.y, v.z)):
                    lo[i] = min(lo[i], c)
                    hi[i] = max(hi[i], c)
        # Inches, exact to 1e-2 (the store is f64; this pins units + Z-up).
        assert lo == pytest.approx([-326.719, 0.0, -23.419], abs=1e-2)
        assert hi == pytest.approx([784.443, 222.211, 145.976], abs=1e-2)

    def test_has_instances_and_geometry(self) -> None:
        model = self._model()
        # 2 named component definitions; the implicit root (which places
        # the instances) is its own separate field, not a 3rd entry here.
        assert len(model.definitions) == 2
        all_defs = list(model.definitions.values()) + [model.root]
        placed = sum(len(d.instances) for d in all_defs)
        assert placed >= 2
        # Every face resolves a real ring of vertices.
        a_face = next(f for d in all_defs
                      for f in d.faces.values() if f.loops)
        assert len(a_face.loops[0]) >= 3

    def test_root_is_a_separate_field_not_a_definitions_entry(self) -> None:
        model = self._model()
        assert model.root.name == "ROOT_MODEL"
        assert model.root.guid == "ROOT"
        assert len(model.root.instances) >= 2
        # The implicit root must never leak into the definitions map under
        # its internal "ROOT" sentinel key - it has its own field.
        assert "ROOT" not in model.definitions
        assert all(isinstance(k, int) for k in model.definitions)

    def test_coedge_orientations_are_normalized_and_connected(self) -> None:
        model = self._model()
        _assert_connected_normalized_loops(
            list(model.definitions.values()) + [model.root])


class TestModernRealFile:
    """Decode real modern (VFF/2021+) ``.skp`` files end to end and assert
    against known ground truth.

    Python's ``tests/fixtures/`` previously had only the legacy MFC
    fixture - ``Untitled.skp``/``SU_File.skp`` (which every other language
    pins exact values against in its own integration test) didn't exist in
    Python's tree at all, meaning the VFF/modern-format path had zero
    real-file coverage here specifically. Both fixtures are the exact same
    files already used by the TypeScript/Dart/C++/.NET ports (confirmed
    byte-identical via checksum before copying), and every assertion below
    was cross-checked directly against a real Python parse of these files
    before being written (not copied blind from the other ports' tests).
    """

    FIXTURE_UNTITLED = pathlib.Path(__file__).parent / "fixtures" / "Untitled.skp"
    FIXTURE_SU_FILE = pathlib.Path(__file__).parent / "fixtures" / "SU_File.skp"

    def _model(self, fixture: pathlib.Path):
        if not fixture.exists():
            pytest.skip("modern fixture not present")
        from openskp.model import SkpFile
        return SkpFile.open(str(fixture))

    def test_untitled_skp_matches_ground_truth(self) -> None:
        skp = self._model(self.FIXTURE_UNTITLED)
        model = skp.parse()

        # 1. Version and units
        assert model.version == "{25.0.575}"
        assert model.units == "Millimeter"

        # 2. Layers
        assert len(model.layers) == 14
        expected_layers = {
            "Layer0", "BottomPlate", "TopPlate", "Stud", "Nog", "KingStud",
            "HeaderJackStud", "HeaderPlate1", "HeaderPlate2", "SillPlate1",
            "VerticalHeaderStud", "generic_frame", "dimension", "Hat Sections",
        }
        assert expected_layers <= {layer.name for layer in model.layers}
        layer0 = next(layer for layer in model.layers if layer.name == "Layer0")
        assert (layer0.color_r, layer0.color_g, layer0.color_b) == (136, 136, 136)
        # VFF files carry no known layer-visibility tag - always False here.
        assert all(not layer.hidden for layer in model.layers)

        # 3. Materials
        assert len(model.materials) == 15
        mat_layer0 = next(m for m in model.materials if m.name == "Layer_Layer0")
        # Real data: none of this fixture's materials have useTrans="1" set,
        # so all correctly read fully opaque.
        assert mat_layer0.transparency == 1.0
        assert mat_layer0.id is None
        assert mat_layer0.texture is None
        assert mat_layer0.colorized is False
        assert mat_layer0.colorize_type == 0

        # Real join: TLV material ID 26180 resolves to the default "*"
        # material, and the resolved object is the SAME instance held in
        # model.materials (not a copy) - the join shares identity.
        joined = model.materials_by_id.get(26180)
        assert joined is not None
        assert joined.name == "*"
        assert joined in model.materials
        assert joined.id == 26180

        # 3b. Instance layer/properties (item 17): previously always ""
        # / {} - declared but never assigned. Now genuinely populated
        # from each instance's own D207 (layer override)/DC05 (dynamic
        # properties) TLV children, matching C++'s existing behavior.
        battens = [i for i in model.root.instances if i.name == "BattenHatSection_1"]
        assert battens
        assert battens[0].layer == "Hat Sections"
        w1 = [i for i in model.root.instances if i.name == "W1"]
        assert w1
        assert w1[0].properties["generator"] == "SteelFramer::Engine::PanelGenerator"
        assert w1[0].properties["profile"] == "362S200-43"

        # 4. Definitions
        assert len(model.definitions) == 46
        def66 = model.definitions[66]
        assert def66.name == "Group200#2"
        assert len(def66.guid) == 32  # GUID as hex string
        assert def66.always_faces_camera is False
        assert def66.is_image is False
        assert isinstance(def66.instances, list)

        # 5. Vertices/edges/faces in Definition 66
        assert len(def66.vertices) == 136
        first_vertex = next(iter(def66.vertices.values()))
        assert isinstance(first_vertex.x, float)
        assert isinstance(first_vertex.y, float)
        assert isinstance(first_vertex.z, float)

        assert len(def66.edges) == 158
        first_edge = next(iter(def66.edges.values()))
        assert first_edge.soft is False
        assert first_edge.smooth is False
        assert first_edge.hidden is False

        assert len(def66.faces) == 26
        first_face = next(iter(def66.faces.values()))
        assert len(first_face.loops) > 0
        edge_id, orientation = first_face.loops[0][0]
        assert isinstance(edge_id, int)
        assert isinstance(orientation, int)
        assert first_face.normal is not None
        assert len(first_face.normal) == 3
        assert first_face.back_material_id is None
        assert first_face.uv_transform is None
        assert first_face.uv_transform_back is None
        # Real data: every face/instance in this fixture is visible - D307's
        # flag byte reads the plain baseline (0x06) throughout.
        assert all(not f.hidden for f in def66.faces.values())

        # 6. Styles - this fixture bundles two style.xml files (the second
        # is SketchUp's "_1" duplicate-naming convention), both named
        # "[Construction Documentation Style]" with the same front/back
        # colors.
        assert len(model.styles) == 2
        assert model.styles[0].name == "[Construction Documentation Style]"
        assert model.styles[0].front_color == (255, 255, 255)
        assert model.styles[0].back_color == (208, 209, 189)

        # NOTE: build_scene()/mesh_index is deliberately NOT asserted here.
        # Adding this fixture surfaced a real, pre-existing bug: triangulating
        # one specific face in definition 20686 raises shapely/GEOS
        # `TopologyException: side location conflict` on some platform/
        # Python-version combinations (fails on all 4 Windows CI jobs, plus
        # some macOS/Linux ones - a GEOS numerical-robustness issue, not a
        # test-writing mistake). See the newly-filed follow-up item for the
        # actual fix; this test intentionally stays scoped to parse(), which
        # exercises the full TLV/XML decode path without touching
        # triangulation at all.

    def test_su_file_skp_matches_ground_truth(self) -> None:
        skp = self._model(self.FIXTURE_SU_FILE)
        model = skp.parse()

        assert model.version == "{25.0.575}"

        assert len(model.layers) == 1
        assert model.layers[0].name == "Layer0"

        assert len(model.materials) == 1
        assert model.materials[0].name == "Layer_Layer0"

        # Only ROOT holds geometry in this fixture, so the numeric
        # definitions map (which excludes ROOT) is empty.
        assert len(model.definitions) == 0

        scene = skp.build_scene()
        assert scene.scene_hierarchy.name == "ROOT"
        assert scene.scene_hierarchy.definition_name == "ROOT_MODEL"

        assert len(scene.mesh_index) == 1
        first_mesh = next(iter(scene.mesh_index.values()))
        assert first_mesh.definition_name == "ROOT_MODEL"

    def test_coedge_orientations_are_normalized_and_connected(self) -> None:
        model = self._model(self.FIXTURE_UNTITLED).parse()
        _assert_connected_normalized_loops(
            list(model.definitions.values()) + [model.root])


class TestLegacyDynamicProperties:
    """Legacy (pre-2021 MFC) instances now carry their already-parsed
    ``CAttributeContainer`` through to ``build_scene()``, instead of it
    being read (advancing the byte cursor correctly) and then silently
    discarded. ``_read_instance`` was calling ``_preamble(ar, r)`` and
    dropping its return value - the exact same "already-decoded-but-
    discarded" shape as the Tier 2 layer/face/instance-hidden fixes, just
    one level deeper (a whole sub-object tree instead of a single byte).

    SketchUp's Dynamic Components extension stores its data under a
    dictionary literally named ``"dynamic_attributes"`` (stable, publicly
    documented Ruby API: ``Entity#attribute_dictionary("dynamic_attributes")``
    - not something reverse-engineered from a fixture). The real legacy
    fixture available in this repo (``capilla_quiroz_v17.skp``, a plain
    chapel model) has no Dynamic Component data at all - confirmed by
    direct inspection: none of its 3 instances carry an attribute
    container of any kind - so the dictionary-lookup logic itself can only
    be verified with synthetic data here; the real fixture instead proves
    the plumbing fix doesn't break or crash on entities that render no
    attributes.
    """

    def test_stringify_scalar_values(self) -> None:
        from openskp.legacy import _stringify_attr_value
        assert _stringify_attr_value(None) == ""
        assert _stringify_attr_value(42) == "42"
        assert _stringify_attr_value(3.5) == "3.5"
        assert _stringify_attr_value("width") == "width"

    def test_stringify_list_and_tuple_values(self) -> None:
        from openskp.legacy import _stringify_attr_value
        assert _stringify_attr_value([1, 2, 3]) == "1,2,3"
        assert _stringify_attr_value((1.0, 2.0, 3.0)) == "1.0,2.0,3.0"

    def test_extracts_dynamic_attributes_dict_by_name(self) -> None:
        from openskp.legacy import _extract_legacy_dynamic_properties
        attrs = {
            "k": "attrs",
            "children": [
                ("SU_DefinitionSet", {"k": "dict", "name": "SU_DefinitionSet", "entries": {"unrelated": 1}}),
                ("dynamic_attributes", {
                    "k": "dict", "name": "dynamic_attributes",
                    "entries": {"width": 10.0, "_width_label": "Width", "count": 4},
                }),
            ],
        }
        props = _extract_legacy_dynamic_properties(attrs)
        assert props == {"width": "10.0", "_width_label": "Width", "count": "4"}

    def test_returns_empty_dict_when_no_dynamic_attributes_dict(self) -> None:
        from openskp.legacy import _extract_legacy_dynamic_properties
        attrs = {"k": "attrs", "children": [
            ("SU_DefinitionSet", {"k": "dict", "name": "SU_DefinitionSet", "entries": {"a": 1}}),
        ]}
        assert _extract_legacy_dynamic_properties(attrs) == {}

    def test_returns_empty_dict_for_no_attribute_container(self) -> None:
        from openskp.legacy import _extract_legacy_dynamic_properties
        assert _extract_legacy_dynamic_properties(None) == {}

    import pathlib as _pathlib

    FIXTURE = _pathlib.Path(__file__).parent / "fixtures" / "capilla_quiroz_v17.skp"

    def test_real_legacy_fixture_wires_through_without_crashing(self) -> None:
        if not self.FIXTURE.exists():
            pytest.skip("legacy fixture not present")
        from openskp.model import SkpFile
        scene = SkpFile.open(str(self.FIXTURE)).build_scene()

        def walk(node):
            assert isinstance(node.properties, dict)
            # This fixture carries no Dynamic Component data on any
            # instance (verified by direct inspection of the raw
            # CAttributeContainer reads) - properties stay empty, but the
            # plumbing must not raise or silently drop the instance.
            assert node.properties == {}
            for child in node.children:
                walk(child)

        walk(scene.scene_hierarchy)


# ── Scene baking (opt-in build_scene(), separate from parse()) ──────────


class TestBuildScene:
    """``SkpFile.build_scene()`` bakes every placed instance into a
    triangulated, world-space scene - a separate, opt-in step from
    :meth:`parse` (see module docstring in ``scene.py``).

    Root instance count is cross-validated directly against the
    TypeScript port's ``SkpFile.buildScene()`` on this exact fixture.
    Mesh/gltf_materials counts (21/21/13) instead match C++'s
    independently-verified reference for this file
    (``parser_test.cpp``'s ``ModernSceneMatchesReference``/legacy
    equivalent) - the correct counts once faces with genuinely different
    front/back materials are split into two single-sided primitives each,
    rather than TypeScript's still-unported single-sided-only count
    (13/13/9) at the time this comment was written. This fixture has 30
    such faces (confirmed by direct inspection), so the split isn't a rare
    edge case here.
    """

    import pathlib as _pathlib

    FIXTURE = _pathlib.Path(__file__).parent / "fixtures" / "capilla_quiroz_v17.skp"

    def _scene(self):
        import pytest as _pytest
        if not self.FIXTURE.exists():
            _pytest.skip("legacy fixture not present")
        from openskp.model import SkpFile
        return SkpFile.open(str(self.FIXTURE)).build_scene()

    def test_scene_matches_typescript_ground_truth(self) -> None:
        scene = self._scene()

        assert len(scene.glb_primitives) == 21
        assert len(scene.mesh_index) == 21
        assert len(scene.gltf_materials) == 13

        assert scene.scene_hierarchy.name == "ROOT"
        assert scene.scene_hierarchy.definition_name == "ROOT_MODEL"
        assert len(scene.scene_hierarchy.children) == 3
        assert sorted(c.definition_name for c in scene.scene_hierarchy.children) == [
            "grada", "grada", "puerta",
        ]

    def test_primitives_have_valid_geometry(self) -> None:
        scene = self._scene()
        for prim in scene.glb_primitives:
            assert len(prim.positions) % 3 == 0
            assert len(prim.normals) == len(prim.positions)
            assert len(prim.indices) % 3 == 0
            n_verts = len(prim.positions) // 3
            assert all(0 <= idx < n_verts for idx in prim.indices)
            assert 0 <= prim.material_index < len(scene.gltf_materials)

    def test_back_face_materials_render_correctly(self) -> None:
        """Regression for item 14: faces with genuinely different front/
        back materials must produce two correctly-colored, correctly-
        wound single-sided primitives (not one primitive using only the
        front material, and not a hidden/invisible back side). Faces
        133/152 in this fixture's ``puerta`` definition are a real,
        concrete example: front material 29 (a blue, (2, 0, 237)), back
        material 27 (a light blue, (204, 235, 244)) - confirmed by direct
        inspection before writing this test."""
        scene = self._scene()

        def has_color(rgb) -> bool:
            r, g, b = rgb
            return any(
                m["pbrMetallicRoughness"]["baseColorFactor"][:3]
                == [r / 255, g / 255, b / 255]
                for m in scene.gltf_materials
            )

        assert has_color((2, 0, 237))
        assert has_color((204, 235, 244))

        # Faces whose front/back colors coincide (or have no back material
        # at all) are emitted once with doubleSided=True instead of being
        # split - confirmed count from direct inspection of this fixture.
        double_sided = [m for m in scene.gltf_materials if m.get("doubleSided")]
        assert len(double_sided) == 4

    def test_independent_of_parse(self) -> None:
        """build_scene() must not require parse() to have been called
        first - it re-parses independently."""
        from openskp.model import SkpFile
        skp = SkpFile.open(str(self.FIXTURE))
        scene = skp.build_scene()
        assert len(scene.glb_primitives) == 21


class TestBuildSceneRecursionGuard:
    """A component definition that (directly or transitively) instances
    itself must raise, not recurse until the stack overflows. Real .skp
    files can't easily be hand-crafted to exercise this, so these tests
    build a synthetic ``defs_dict`` directly using the same
    ``_GeometryBuilder`` shape ``_core.py`` produces.
    """

    @staticmethod
    def _instance(ref_idx, name="child"):
        return {
            "offset": 0, "ref_guid": "", "ref_idx": ref_idx, "name": name,
            "matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1.0],
            "material_id": None, "children": [],
        }

    @staticmethod
    def _parsed(defs_dict):
        return {
            "defs_dict": defs_dict,
            "layer_colors": {},
            "layer_id_to_name": {},
            "material_id_to_name": {},
            "materials": {},
            "materials_by_folder": {},
        }

    def test_self_referencing_definition_raises(self) -> None:
        from openskp._core import _GeometryBuilder
        from openskp.errors import SkpParseError
        from openskp.scene import build_scene

        builder = _GeometryBuilder()
        builder.instances.append(self._instance(1))
        root_builder = _GeometryBuilder()
        root_builder.instances.append(self._instance(1))
        defs_dict = {
            1: {"guid": "g1", "name": "self_ref", "builder": builder},
            "ROOT": {"guid": "ROOT", "name": "ROOT_MODEL", "builder": root_builder},
        }

        with pytest.raises(SkpParseError, match="Recursive component definition"):
            build_scene(self._parsed(defs_dict))

    def test_indirect_cycle_raises(self) -> None:
        from openskp._core import _GeometryBuilder
        from openskp.errors import SkpParseError
        from openskp.scene import build_scene

        builder_a = _GeometryBuilder()
        builder_a.instances.append(self._instance(2))
        builder_b = _GeometryBuilder()
        builder_b.instances.append(self._instance(1))
        root_builder = _GeometryBuilder()
        root_builder.instances.append(self._instance(1))
        defs_dict = {
            1: {"guid": "g1", "name": "a", "builder": builder_a},
            2: {"guid": "g2", "name": "b", "builder": builder_b},
            "ROOT": {"guid": "ROOT", "name": "ROOT_MODEL", "builder": root_builder},
        }

        with pytest.raises(SkpParseError, match="Recursive component definition"):
            build_scene(self._parsed(defs_dict))

    def test_legitimate_sibling_reuse_does_not_raise(self) -> None:
        """The same definition instanced twice as siblings (not nested
        inside itself) is normal and must not trip the guard."""
        from openskp._core import _GeometryBuilder
        from openskp.scene import build_scene

        shared = _GeometryBuilder()
        root_builder = _GeometryBuilder()
        root_builder.instances.append(self._instance(1, "child_a"))
        root_builder.instances.append(self._instance(1, "child_b"))
        defs_dict = {
            1: {"guid": "g1", "name": "shared", "builder": shared},
            "ROOT": {"guid": "ROOT", "name": "ROOT_MODEL", "builder": root_builder},
        }

        scene = build_scene(self._parsed(defs_dict))
        assert len(scene.scene_hierarchy.children) == 2


class TestGlbExport:
    """``export.glb.export()`` bakes via ``scene.build_scene()`` (the same
    step ``SkpFile.build_scene()`` exposes directly) and hands the
    resulting primitives to trimesh purely for GLB binary serialization -
    so its counts now track ``TestBuildScene``'s exactly (scaled to
    millimetres instead of metres), and its UV/material/JSON-metadata
    handling still needs its own coverage since the trimesh serialization
    step is a separate concern from the baking step.

    Before this was tested, ``export()`` crashed on any file with a
    textured material (the metadata JSON writer tried to serialize the raw
    texture image bytes), and no mesh actually carried real UV coordinates
    at all - both fixed together, verified here.
    """

    import pathlib as _pathlib

    FIXTURE = _pathlib.Path(__file__).parent / "fixtures" / "capilla_quiroz_v17.skp"

    def _export(self, tmp_path):
        import pytest as _pytest
        if not self.FIXTURE.exists():
            _pytest.skip("legacy fixture not present")
        from openskp.export import glb as glb_export
        from openskp.model import SkpFile

        skp = SkpFile.open(str(self.FIXTURE))
        skp.parse()
        out_path = tmp_path / "capilla_quiroz_v17.glb"
        return skp, glb_export.export(skp, str(out_path))

    def test_export_writes_a_glb_with_real_uv_per_mesh(self, tmp_path) -> None:
        import trimesh

        skp, glb_path = self._export(tmp_path)
        loaded = trimesh.load(glb_path, file_type="glb")

        # NOTE: no longer asserting len(loaded.geometry) ==
        # len(scene.build_scene().glb_primitives) here - that was only ever
        # a coincidence of the two pipelines' prior implementation details,
        # not a documented invariant (this class's own docstring already
        # says export() uses a wholly separate trimesh engine). It stopped
        # holding once build_scene() started correctly splitting faces with
        # genuinely different front/back materials into two primitives
        # each - the trimesh pipeline doesn't do that split, so the counts
        # now legitimately diverge on this fixture (30 such faces).

        for name, geom in loaded.geometry.items():
            assert geom.visual.kind == "texture", f"{name} has no UV/material"
            uv = geom.visual.uv
            assert uv is not None and len(uv) == len(geom.vertices)
            assert not any(v != v or v in (float("inf"), float("-inf")) for row in uv for v in row)

    def test_metadata_json_is_written_and_json_safe(self, tmp_path) -> None:
        import json

        _skp, glb_path = self._export(tmp_path)
        meta_path = str(glb_path).replace(".glb", "_metadata.json")
        with open(meta_path, encoding="utf-8") as f:
            metadata = json.load(f)

        # 21, not 13: matches TestBuildScene's ground truth now that
        # export() bakes via scene.build_scene() - faces with genuinely
        # different front/back materials split into two meshes each (30
        # such faces on this fixture).
        assert metadata["total_meshes"] == 21
        # Regression: materials carry texture data as raw bytes internally,
        # which isn't JSON-serializable - export() must strip it before
        # writing, not embed it (or crash).
        for mat in metadata["materials"]:
            tex = mat.get("texture")
            if tex is not None:
                assert "data" not in tex

        first_child = metadata["scene_hierarchy"]["children"][0]
        assert "definition_name" in first_child
        assert "position_mm" in first_child

    def test_export_rejects_unsupported_coordinate_system(self, tmp_path) -> None:
        # The underlying conversion is hardcoded to y-up/mm - passing
        # anything else must raise, not silently produce y-up/mm output
        # while claiming to have honored the request.
        import pytest as _pytest
        if not self.FIXTURE.exists():
            _pytest.skip("legacy fixture not present")
        from openskp.export import glb as glb_export
        from openskp.model import SkpFile

        skp = SkpFile.open(str(self.FIXTURE))
        skp.parse()
        with pytest.raises(NotImplementedError, match="coordinate_system"):
            glb_export.export(skp, str(tmp_path / "out.glb"), coordinate_system="z-up")

    def test_export_rejects_unsupported_units(self, tmp_path) -> None:
        import pytest as _pytest
        if not self.FIXTURE.exists():
            _pytest.skip("legacy fixture not present")
        from openskp.export import glb as glb_export
        from openskp.model import SkpFile

        skp = SkpFile.open(str(self.FIXTURE))
        skp.parse()
        with pytest.raises(NotImplementedError, match="units"):
            glb_export.export(skp, str(tmp_path / "out.glb"), units="inches")

    def test_scene_deduplicates_textures_and_keys_materials_by_them(self, tmp_path) -> None:
        # openskp#193 (ported from TypeScript): color-to-material dedup used
        # to key on (color, double_sided) alone, so two different textures
        # averaging to the same color would silently collapse into one
        # material and lose an image. Real fixture, not a mock: 3 distinct
        # JPEGs, correctly deduplicated and correctly referenced.
        from openskp import scene as scene_mod
        from openskp.model import SkpFile

        skp = SkpFile.open(str(self.FIXTURE))
        skp.parse()
        scene_obj = scene_mod.build_scene(skp._parsed)

        assert len(scene_obj.textures) == 3
        for tex in scene_obj.textures:
            assert tex.mime_type in ("image/jpeg", "image/png")
            assert len(tex.data) > 0

        textured_materials = [
            m for m in scene_obj.gltf_materials if "baseColorTexture" in m["pbrMetallicRoughness"]
        ]
        assert len(textured_materials) == 4
        # every referenced index is a real, in-range texture
        for mat in textured_materials:
            idx = mat["pbrMetallicRoughness"]["baseColorTexture"]["index"]
            assert 0 <= idx < len(scene_obj.textures)

    def test_export_omits_images_by_default(self, tmp_path) -> None:
        skp, glb_path = self._export(tmp_path)
        with open(glb_path, "rb") as f:
            data = f.read()
        assert b'"images"' not in data
        assert b"\xff\xd8\xff" not in data  # no JPEG magic bytes embedded

    def test_export_embeds_textures_when_asked(self, tmp_path) -> None:
        from openskp.export import glb as glb_export
        from openskp.model import SkpFile

        skp = SkpFile.open(str(self.FIXTURE))
        skp.parse()
        out_path = tmp_path / "with_textures.glb"
        glb_export.export(skp, str(out_path), textures=True)

        with open(out_path, "rb") as f:
            data = f.read()
        assert b'"images"' in data
        assert b"\xff\xd8\xff" in data  # real JPEG bytes, not a reference

        import trimesh
        loaded = trimesh.load(out_path, file_type="glb")
        # the embedded images must actually be usable as textures, not just
        # bytes sitting in the file
        found_texture = False
        for geom in loaded.geometry.values():
            mat = getattr(geom.visual, "material", None)
            if mat is not None and getattr(mat, "baseColorTexture", None) is not None:
                found_texture = True
                break
        assert found_texture

    def test_export_rejects_self_referencing_definition(self, tmp_path) -> None:
        # export() now bakes via scene.build_scene() instead of its own
        # separate trimesh pass, so the recursion guard covered by
        # TestBuildSceneRecursionGuard must reach this path too - it
        # didn't before this was wired together.
        from openskp._core import _GeometryBuilder
        from openskp.errors import SkpParseError
        from openskp.export import glb as glb_export

        instance = {
            "offset": 0, "ref_guid": "", "ref_idx": 1, "name": "child",
            "matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1.0],
            "material_id": None, "children": [],
        }
        builder = _GeometryBuilder()
        builder.instances.append(instance)
        root_builder = _GeometryBuilder()
        root_builder.instances.append(instance)
        defs_dict = {
            1: {"guid": "g1", "name": "self_ref", "builder": builder},
            "ROOT": {"guid": "ROOT", "name": "ROOT_MODEL", "builder": root_builder},
        }

        class _FakeSkpFile:
            _parsed = {
                "defs_dict": defs_dict, "layer_colors": {}, "layer_id_to_name": {},
                "material_id_to_name": {}, "materials": {}, "materials_by_folder": {},
                "version": "test", "thumbnail_data": None,
            }

        with pytest.raises(SkpParseError, match="Recursive component definition"):
            glb_export.export(_FakeSkpFile(), str(tmp_path / "out.glb"))

    def test_export_marks_back_face_materials_double_sided(self, tmp_path) -> None:
        # The metadata JSON's material list should carry the same
        # doubleSided/back-face fix TestBuildScene covers - end to end
        # through the real trimesh-serialized .glb, not just the
        # intermediate Scene object.
        import json
        import struct

        skp, glb_path = self._export(tmp_path)
        with open(glb_path, "rb") as f:
            data = f.read()
        chunk_len = struct.unpack("<I", data[12:16])[0]
        materials = json.loads(data[20:20 + chunk_len])["materials"]

        assert len(materials) == 13
        double_sided = [m for m in materials if m.get("doubleSided")]
        assert len(double_sided) == 4


# ── Observability: progress logging + structured error context ──────────


class TestObservability:
    """openskp exposes progress via the stdlib ``logging`` module (silent
    by default, matching ``requests``/``urllib3``), and raises
    :class:`SkpParseError` with structured location context (stage,
    record_index, tag, ...) on failure - so a production pipeline can
    trace exactly where a model got stuck instead of a bare traceback."""

    import pathlib as _pathlib

    FIXTURE = _pathlib.Path(__file__).parent / "fixtures" / "capilla_quiroz_v17.skp"

    def test_silent_by_default(self, caplog) -> None:
        """With no explicit logging configuration, openskp's INFO/DEBUG
        progress logs must not reach any handler - the library never
        calls ``basicConfig()`` or installs its own handler/level."""
        if not self.FIXTURE.exists():
            pytest.skip("legacy fixture not present")
        from openskp.model import SkpFile
        SkpFile.open(str(self.FIXTURE)).parse()
        assert caplog.records == []

    def test_progress_logs_at_debug(self, caplog) -> None:
        if not self.FIXTURE.exists():
            pytest.skip("legacy fixture not present")
        from openskp.model import SkpFile
        with caplog.at_level("DEBUG", logger="openskp.legacy"):
            SkpFile.open(str(self.FIXTURE)).parse()
        messages = [r.message for r in caplog.records]
        assert any("Parsing legacy" in m for m in messages)
        assert any("Parse complete" in m for m in messages)

    def test_scene_build_logs(self, caplog) -> None:
        if not self.FIXTURE.exists():
            pytest.skip("legacy fixture not present")
        from openskp.model import SkpFile
        with caplog.at_level("INFO", logger="openskp.scene"):
            SkpFile.open(str(self.FIXTURE)).build_scene()
        messages = [r.message for r in caplog.records]
        assert any("Building scene" in m for m in messages)
        assert any("Scene build complete" in m for m in messages)

    def test_bad_header_raises_parse_error_with_stage(self, tmp_path) -> None:
        from openskp import SkpParseError
        from openskp.model import SkpFile

        bad_file = tmp_path / "not_a_skp.skp"
        bad_file.write_bytes(b"not a real skp file" * 10)

        with pytest.raises(SkpParseError) as exc_info:
            SkpFile.open(str(bad_file)).parse()
        assert exc_info.value.stage == "header"

    def test_parse_error_str_includes_context(self) -> None:
        from openskp import SkpParseError

        err = SkpParseError(
            "boom", stage="tlv_walk", record_index=3, total_records=10,
            tag="F601",
        )
        text = str(err)
        assert "stage=tlv_walk" in text
        assert "record=3/10" in text
        assert "tag=F601" in text

    def test_parse_error_preserves_cause(self) -> None:
        from openskp import SkpParseError

        original = ValueError("inner failure")
        try:
            try:
                raise original
            except ValueError as e:
                raise SkpParseError("wrapped", stage="tlv_walk") from e
        except SkpParseError as wrapped:
            assert wrapped.__cause__ is original


class TestLegacyClassRef:
    """Both MFC class-ref encodings the definition-tail scanner must match."""

    def test_short_form(self) -> None:
        from openskp.legacy import _is_class_ref

        data = struct.pack("<H", 0x8000 | 278)
        assert _is_class_ref(data, 0, 278)
        assert not _is_class_ref(data, 0, 279)

    def test_big_tag_escape(self) -> None:
        from openskp.legacy import _is_class_ref

        # slot 65712 does not fit in 0x8000|slot: MFC writes 0x7FFF + u32
        data = struct.pack("<HI", 0x7FFF, 0x80000000 | 65712)
        assert _is_class_ref(data, 0, 65712)
        assert not _is_class_ref(data, 0, 65713)

    def test_big_slot_never_matches_short_form(self) -> None:
        from openskp.legacy import _is_class_ref

        # the pre-fix scanner compared a u16 read against 0x8000|65712,
        # which cannot fit in 16 bits — the truncated value must not match
        data = struct.pack("<H", (0x8000 | 65712) & 0xFFFF)
        assert not _is_class_ref(data, 0, 65712)

    def test_truncated_data(self) -> None:
        from openskp.legacy import _is_class_ref

        assert not _is_class_ref(b"\xff\x7f\xb0", 0, 65712)


class TestTriangulateFace3dRobustness:
    """Robustness tests for _core.triangulate_face_3d on complex/invalid face geometry."""

    def test_triangulate_face_3d_handles_invalid_polygon_without_raising(self) -> None:
        from openskp._core import triangulate_face_3d

        # Self-intersecting bow-tie shape projected to 3D
        vertices_3d = {
            1: (0.0, 0.0, 0.0),
            2: (10.0, 10.0, 0.0),
            3: (0.0, 10.0, 0.0),
            4: (10.0, 0.0, 0.0),
        }
        loops = [[1, 2, 3, 4]]
        normal = (0.0, 0.0, 1.0)

        # Must not raise TopologyException or GEOS error
        triangles = triangulate_face_3d(vertices_3d, loops, normal)
        assert isinstance(triangles, list)
        assert len(triangles) > 0

    def test_triangulate_face_3d_handles_insufficient_points(self) -> None:
        from openskp._core import triangulate_face_3d

        vertices_3d = {1: (0.0, 0.0, 0.0), 2: (1.0, 1.0, 0.0)}
        loops = [[1, 2]]
        normal = (0.0, 0.0, 1.0)

        triangles = triangulate_face_3d(vertices_3d, loops, normal)
        assert triangles == []
