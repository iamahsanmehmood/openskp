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
        assert mat.color == (10, 20, 30)

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
        import struct as _struct
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
                        + t('581B', t('5D1B', b'\x01'))
                        + t('DC05', t('DE05', b'\x05'))))
        off = t('7C15', (t('7D15', b'\x22' * 16) + t('7E15', b'Silla')
                         + t('581B', t('5D1B', b'\x00'))
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
        assert by_name['Silla'].always_faces_camera is False


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


# Need pathlib for tmp_path fixture
import pathlib


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
        n_faces = sum(len(d.faces) for d in model.definitions.values())
        n_edges = sum(len(d.edges) for d in model.definitions.values())
        n_verts = sum(len(d.vertices) for d in model.definitions.values())
        assert n_faces == 181
        assert n_edges == 515
        assert n_verts == 335
        assert len(model.materials) == 16

    def test_bounding_box(self) -> None:
        model = self._model()
        lo = [float("inf")] * 3
        hi = [float("-inf")] * 3
        for d in model.definitions.values():
            for v in d.vertices.values():
                for i, c in enumerate((v.x, v.y, v.z)):
                    lo[i] = min(lo[i], c)
                    hi[i] = max(hi[i], c)
        # Inches, exact to 1e-2 (the store is f64; this pins units + Z-up).
        assert lo == pytest.approx([-326.719, 0.0, -23.419], abs=1e-2)
        assert hi == pytest.approx([784.443, 222.211, 145.976], abs=1e-2)

    def test_has_instances_and_geometry(self) -> None:
        model = self._model()
        # 3 definitions (root + 2 components), and the root places instances.
        assert len(model.definitions) == 3
        placed = sum(len(d.instances) for d in model.definitions.values())
        assert placed >= 2
        # Every face resolves a real ring of vertices.
        a_face = next(f for d in model.definitions.values()
                      for f in d.faces.values() if f.loops)
        assert len(a_face.loops[0]) >= 3
