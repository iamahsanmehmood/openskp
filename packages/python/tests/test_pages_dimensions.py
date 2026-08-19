"""VFF scenes (pages) and linear dimensions.

Dimensions are exercised against the repository's own ``Untitled.skp``
fixture (drawn in SketchUp 2025, it carries 13 linear dimensions); scenes
have no fixture yet, so their parser is exercised on a synthetic ``0702``
record byte-for-byte shaped like the real ones (the layout was decoded
from production survey files and calibrated against the scene thumbnails
SketchUp embeds in the ``.skp`` itself).
"""
import math
import struct
from pathlib import Path

from openskp import _core
from openskp.model import SkpFile

FIXTURES = Path(__file__).parent / "fixtures"


# ── helpers: build TLV runs in the flat (u16-LE tag, u32 len) form ────────

def tlv(tag: int, payload: bytes) -> bytes:
    return struct.pack('<HI', tag, len(payload)) + payload


def vec3(x: float, y: float, z: float) -> bytes:
    return struct.pack('<3d', x, y, z)


# ── linear dimensions ─────────────────────────────────────────────────────

def test_untitled_fixture_has_13_dimensions():
    model = SkpFile.open(str(FIXTURES / "Untitled.skp")).parse()
    assert len(model.dimensions) == 13
    for d in model.dimensions:
        assert d.a is not None and d.b is not None
        assert len(d.a) == 3 and len(d.b) == 3
        assert math.dist(d.a, d.b) > 0.0        # a real measured segment
        assert d.normal is not None and d.plane_x is not None


def test_dimension_free_points_synthetic():
    """A 5BCC record with two type-1 (free, world-space) connection points."""
    def point_block(wrap_tag: int, x: float, y: float, z: float) -> bytes:
        inner = (tlv(0x5209, struct.pack('<I', 1))     # type 1: explicit point
                 + tlv(0x520A, vec3(x, y, z)))
        return tlv(wrap_tag, tlv(0x5208, inner))

    body = (point_block(0x5BCD, 0.0, 0.0, 0.0)
            + point_block(0x5BCE, 100.0, 0.0, 0.0)
            + tlv(0x5BCF, vec3(1.0, 0.0, 0.0))         # plane x-axis
            + tlv(0x5BD0, vec3(0.0, 0.0, 1.0))         # plane normal
            + tlv(0x5BD2, struct.pack('<d', 15.5)))    # offset
    blob = b'\x00' * 8 + tlv(0x5BCC, body) + b'\x00' * 8

    dims = _core._parse_dimensions(blob, {}, {})
    assert len(dims) == 1
    d = dims[0]
    assert d['a'] == (0.0, 0.0, 0.0) and d['b'] == (100.0, 0.0, 0.0)
    assert d['offset'] == 15.5
    assert d['plane_x'] == (1.0, 0.0, 0.0)
    assert d['normal'] == (0.0, 0.0, 1.0)


def test_dimension_connected_point_resolves_through_instance():
    """A type-2 connection (vertex id + instance id): the vertex position is
    definition-local and must be lifted to world by the instance's
    transform. An unresolvable reference drops the dimension (fail-safe)."""
    vid = bytes.fromhex('aabb01')
    iid = bytes.fromhex('ccdd02')

    def connected(wrap_tag: int) -> bytes:
        ref = tlv(0x53FC, tlv(0x53FD, vid)
                  + tlv(0x53FE, bytes([len(iid)]) + iid))
        inner = (tlv(0x5209, struct.pack('<I', 2))
                 + tlv(0x520B, ref))
        return tlv(wrap_tag, tlv(0x5208, inner))

    def free(wrap_tag: int) -> bytes:
        inner = (tlv(0x5209, struct.pack('<I', 1))
                 + tlv(0x520A, vec3(0.0, 0.0, 0.0)))
        return tlv(wrap_tag, tlv(0x5208, inner))

    body = connected(0x5BCD) + free(0x5BCE) + tlv(0x5BD2, struct.pack('<d', 0.0))
    blob = tlv(0x5BCC, body)

    # Identity-ish transform that translates by (10, 20, 30).
    world = [1, 0, 0, 0, 1, 0, 0, 0, 1, 10.0, 20.0, 30.0, 1.0]
    dims = _core._parse_dimensions(
        blob, {vid.hex(): (1.0, 2.0, 3.0)}, {iid.hex(): world})
    assert len(dims) == 1
    assert dims[0]['a'] == (11.0, 22.0, 33.0)   # local + translation

    # Same record, but the vertex id is unknown: the dimension is dropped.
    assert _core._parse_dimensions(blob, {}, {}) == []


# ── scenes (pages) ────────────────────────────────────────────────────────

def _page_record(name: str, parallel: bool, hidden_ids=()) -> bytes:
    cam = (tlv(0x34BD, vec3(100.0, -200.0, 50.0))      # eye
           + tlv(0x34BE, vec3(0.0, 0.0, 0.0))          # target
           + tlv(0x34BF, vec3(0.0, 0.0, 1.0))          # up
           + tlv(0x34C4, struct.pack('<d', 35.0))      # fov
           + tlv(0x34C2, bytes([0 if parallel else 1]))
           + tlv(0x34C3, struct.pack('<d', 240.0)))    # ortho height
    hidden = b''.join(bytes([1]) + bytes([i]) for i in hidden_ids)
    body = (tlv(0x6F54, tlv(0x6F55, name.encode()))
            + tlv(0x714A, tlv(0x34BC, cam))
            + tlv(0x7150, hidden))
    return tlv(0x7148, body)


def test_parse_pages_synthetic():
    payload = tlv(0x6D60, tlv(0x6D61,
                              _page_record("Planta", True, hidden_ids=(2,))
                              + _page_record("Vista 3D", False)))
    pages = _core._parse_pages({'payload': payload})
    assert [p['name'] for p in pages] == ["Planta", "Vista 3D"]

    planta = pages[0]
    assert planta['parallel'] is True
    assert planta['ortho_height'] == 240.0
    assert planta['eye'] == (100.0, -200.0, 50.0)
    assert planta['up'] == (0.0, 0.0, 1.0)
    assert planta['hidden_layer_ids'] == [2]
    assert pages[1]['parallel'] is False
    assert pages[1]['fov'] == 35.0


def test_pages_absent_is_empty():
    assert _core._parse_pages(None) == []
    model = SkpFile.open(str(FIXTURES / "SU_File.skp")).parse()
    assert model.pages == []
