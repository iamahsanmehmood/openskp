"""Basic tests for the openskp package.

These tests validate the low-level parser utilities, data model
construction, and transform maths without requiring a real ``.skp`` file.
"""

from __future__ import annotations

import struct
from typing import List

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
        assert d["version"] == "unknown"
        assert d["definitions"] == {}
        assert d["layers"] == []
        assert d["materials"] == []
        assert d["scene_hierarchy"] == []


# ── SkpFile tests ────────────────────────────────────────────────────────


class TestSkpFile:
    """Tests for :class:`openskp.model.SkpFile`."""

    def test_open_missing_file(self) -> None:
        from openskp.model import SkpFile

        with pytest.raises(FileNotFoundError):
            SkpFile.open("/nonexistent/path/model.skp")

    def test_open_wrong_extension(self, tmp_path: pathlib.Path) -> None:
        import pathlib
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
        import struct as _struct
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
        assert model.materials[0].transparency == 0.27

    def test_use_trans_0_means_opaque(self, tmp_path: pathlib.Path) -> None:
        from openskp.model import SkpFile

        # trans="0" with useTrans="0" is a leftover default, NOT invisible.
        model = SkpFile.open(str(self._skp_with(
            tmp_path, self.XML % (b"0", b"0")))).parse()
        assert model.materials[0].transparency == 1.0


# Need pathlib for tmp_path fixture
import pathlib
