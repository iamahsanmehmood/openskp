"""Legacy CDimensionLinear endpoints, inline/forward vertices, plane offsets, and writer offset_vector (#481)."""
from __future__ import annotations

import math
import struct
import pytest

from openskp import SkpFile, SkpWriteError, create

KA = (1.0, 2.0, 3.0)
KB = (4.0, 6.0, 3.0)
KC = (11.0, 12.0, 13.0)
KD = (14.0, 16.0, 13.0)

VERTEX_CLASS = b"\xff\xff\x00\x00\x07\x00CVertex"


def _u32(n: int) -> bytes:
    return struct.pack("<I", n)


def _ref(slot: int, large: bool = False) -> bytes:
    if large:
        return b"\xff\x7f" + _u32(slot)
    return struct.pack("<H", slot)


def _point_bytes(p: tuple[float, float, float]) -> bytes:
    return struct.pack("<3d", p[0], p[1], p[2])


def _connection(p: tuple[float, float, float]) -> bytes:
    return _u32(1) + _u32(4) + _point_bytes(p)


def _locate(buf: bytes | bytearray, needle: bytes) -> int:
    at = buf.find(needle)
    if at < 0:
        raise AssertionError("dimension test record not found")
    return at


def _fixture() -> bytearray:
    builder = create()
    builder.add_dimension(KA, KB, -2.0)
    builder.add_dimension(KC, KD, 3.0)
    builder.add_construction_point((101.0, 102.0, 103.0))
    return bytearray(builder.to_bytes())


def _font_slot(buf: bytes | bytearray) -> int:
    return struct.unpack_from("<H", buf, _locate(buf, _connection(KC)) - 7)[0]


def _vertex(p: tuple[float, float, float], tag: bytes) -> bytes:
    return tag + b"\x00\x00\x00" + _point_bytes(p)


def _attach(buf: bytearray, original: tuple[float, float, float], obj: bytes) -> None:
    at = _locate(buf, _connection(original))
    buf[at] = 2
    buf[at + 8 : at + 32] = bytes(24)
    buf[at + 32 : at + 34] = obj


def _parse_bytes(tmp_path, buf: bytes | bytearray, name: str):
    p = tmp_path / name
    p.write_bytes(bytes(buf))
    return SkpFile.open(str(p)).parse()


def _expect_tail(model) -> None:
    assert len(model.root.construction_points) == 1
    assert model.root.construction_points[0].position == (101.0, 102.0, 103.0)


def test_free_endpoints_and_frame_survive_parsing(tmp_path):
    model = _parse_bytes(tmp_path, _fixture(), "free.skp")
    assert len(model.root.dimensions) == 2
    first = model.root.dimensions[0]
    assert first.a == pytest.approx(KA, abs=1e-9)
    assert first.b == pytest.approx(KB, abs=1e-9)
    assert first.offset == -2.0
    assert first.plane_x == pytest.approx((1.0, 0.0, 0.0), abs=1e-9)
    assert first.normal == pytest.approx((0.0, 0.0, 1.0), abs=1e-9)
    assert first.offset_vector is None
    assert model.root.dimensions[1].a == pytest.approx(KC, abs=1e-9)
    assert model.root.dimensions[1].b == pytest.approx(KD, abs=1e-9)
    _expect_tail(model)


@pytest.mark.parametrize("large", [False, True])
def test_inline_vertices_and_later_backrefs_keep_archive_aligned(tmp_path, large: bool):
    buf = _fixture()
    font = _font_slot(buf)
    _attach(buf, KD, _ref(font + 3, large))
    _attach(buf, KC, _ref(font + 2, large))
    _attach(buf, KB, _vertex(KB, _ref((0x80000000 if large else 0x8000) | (font + 1), large)))
    _attach(buf, KA, _vertex(KA, VERTEX_CLASS))
    model = _parse_bytes(tmp_path, buf, f"inline_{int(large)}.skp")
    assert len(model.root.dimensions) == 2
    for dim in model.root.dimensions:
        assert dim.a == pytest.approx(KA, abs=1e-9)
        assert dim.b == pytest.approx(KB, abs=1e-9)
    _expect_tail(model)


def test_forward_vertex_references_resolve_after_archive_walk(tmp_path):
    buf = _fixture()
    font = _font_slot(buf)
    _attach(buf, KC, _vertex(KC, _ref(0x8000 | (font + 1))))
    _attach(buf, KB, _vertex(KB, VERTEX_CLASS))
    _attach(buf, KA, _ref(font + 4, True))
    model = _parse_bytes(tmp_path, buf, "forward.skp")
    assert len(model.root.dimensions) == 2
    assert model.root.dimensions[0].a == pytest.approx(KC, abs=1e-9)
    assert model.root.dimensions[0].b == pytest.approx(KB, abs=1e-9)
    assert model.root.dimensions[1].a == pytest.approx(KC, abs=1e-9)
    _expect_tail(model)


@pytest.mark.parametrize("axis", [1, 2, 3])
@pytest.mark.parametrize("reversed_pts", [False, True])
def test_plane_axes_preserve_signed_offset_with_reversed_endpoints(tmp_path, axis: int, reversed_pts: bool):
    builder = create()
    p1, p2 = (KB, KA) if reversed_pts else (KA, KB)
    builder.add_dimension(p1, p2, -2.0)
    buf = bytearray(builder.to_bytes())
    at = _locate(buf, _connection(p2)) + 32 + 2 + 10
    frame = struct.pack("<6dI", 0.0, -1.0, 0.0, 1.0, 0.0, 0.0, axis)
    buf[at : at + len(frame)] = frame
    model = _parse_bytes(tmp_path, buf, f"axis_{axis}_{int(reversed_pts)}.skp")
    assert len(model.root.dimensions) == 1
    dim = model.root.dimensions[0]
    assert dim.normal == pytest.approx((0.0, -1.0, 0.0), abs=1e-9)
    assert dim.plane_x == pytest.approx((1.0, 0.0, 0.0), abs=1e-9)
    if axis == 1:
        assert dim.offset_vector == pytest.approx((0.0, 0.0, -2.0), abs=1e-9)
    elif axis == 2:
        assert dim.offset_vector == pytest.approx((2.0, 0.0, 0.0), abs=1e-9)
    else:
        assert dim.offset_vector is None


def test_nested_paths_are_consumed_without_publishing_local_coordinates(tmp_path):
    buf = _fixture()
    at = _locate(buf, _connection(KA)) + 32 + 2 + 2
    buf[at] = 1
    buf[at + 4 : at + 4] = _ref(1234, True)
    model = _parse_bytes(tmp_path, buf, "nested_path.skp")
    assert len(model.root.dimensions) == 2
    assert model.root.dimensions[0].a is None
    assert model.root.dimensions[0].b == pytest.approx(KB, abs=1e-9)
    assert model.root.dimensions[1].a == pytest.approx(KC, abs=1e-9)
    _expect_tail(model)


def test_vector_offsets_survive_root_and_nested_round_trips(tmp_path):
    samples = [
        ((1.0, 2.0, 3.0), (11.0, 2.0, 3.0), (0.0, 0.0, 4.0), (0.0, 0.0, 4.0)),
        ((11.0, 2.0, 3.0), (1.0, 2.0, 3.0), (0.0, 0.0, -4.0), (0.0, 0.0, -4.0)),
        ((1.0, 2.0, 3.0), (1.0, 2.0, 13.0), (-4.0, 0.0, 0.0), (-4.0, 0.0, 0.0)),
        ((1.0, 2.0, 3.0), (4.0, 6.0, 8.0), (-4.0, 3.0, 0.0), (-4.0, 3.0, 0.0)),
        ((1.0, 2.0, 3.0), (11.0, 2.0, 3.0), (7.0, -4.0, 0.0), (0.0, -4.0, 0.0)),
        ((1.0, 2.0, 3.0), (1.0, 2.0, 13.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
        ((1.0, 2.0, 3.0), (11.0, 2.0, 3.0), (7.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
        ((1.0, 2.0, 3.0), (4.0, 6.0, 8.0), (3.0, 4.0, 5.0), (0.0, 0.0, 0.0)),
        ((1.0, 2.0, 3.0), (11.0, 2.0, 3.0), (0.0, 0.0, 1e-12), (0.0, 0.0, 1e-12)),
    ]
    builder = create()
    with builder.add_component_definition("Dimensions") as defn:
        for a, b, off, _ in samples:
            defn.add_dimension(a, b, off)
    builder.add_instance(defn)
    for a, b, off, _ in samples:
        builder.add_dimension(a, b, offset_vector=off)

    model = _parse_bytes(tmp_path, builder.to_bytes(), "vector_offsets.skp")
    assert len(model.root.dimensions) == len(samples)
    assert len(model.definitions) == 1
    def_dims = next(iter(model.definitions.values())).dimensions
    assert len(def_dims) == len(samples)
    for dims in (model.root.dimensions, def_dims):
        for dim, (a, b, _, expected) in zip(dims, samples):
            assert dim.a == pytest.approx(a, abs=1e-9)
            assert dim.b == pytest.approx(b, abs=1e-9)
            assert dim.offset_vector == pytest.approx(expected, abs=1e-9)


def test_invalid_vector_dimension_does_not_corrupt_following_records(tmp_path):
    builder = create()
    with pytest.raises(SkpWriteError):
        builder.add_dimension(KA, KA)
    with pytest.raises(SkpWriteError):
        builder.add_dimension(KA, KB, 5.0, offset_vector=(0.0, 0.0, 2.0))
    with pytest.raises(SkpWriteError):
        builder.add_dimension(KA, KB, 10.0, offset_vector=(0.0, 0.0, 2.0))
    builder.add_layer("AfterRejected")
    with builder.add_component_definition("Dimensions") as defn:
        for bad in (math.inf, math.nan):
            with pytest.raises(SkpWriteError):
                defn.add_dimension(KA, KB, (bad, 0.0, 0.0))
        with pytest.raises(SkpWriteError):
            defn.add_dimension(KA, KA, (0.0, 0.0, 1.0))
        with pytest.raises(SkpWriteError):
            defn.add_dimension(KA, KB, 5.0, offset_vector=(0.0, 0.0, 2.0))
        defn.add_dimension(KA, KB, (0.0, 0.0, 2.0))
    builder.add_instance(defn)
    for bad in (math.inf, math.nan):
        with pytest.raises(SkpWriteError):
            builder.add_dimension((bad, 0.0, 0.0), KB, (0.0, 0.0, 1.0))
    for bad_vec in ((1.0, 2.0), (1.0, 2.0, 3.0, 4.0), ()):
        with pytest.raises(SkpWriteError):
            builder.add_dimension(KA, KB, bad_vec)  # type: ignore[arg-type]
        with pytest.raises(SkpWriteError):
            builder.add_dimension(KA, KB, offset_vector=bad_vec)  # type: ignore[arg-type]
        with pytest.raises(SkpWriteError):
            builder.add_dimension(bad_vec, KB, 5.0)  # type: ignore[arg-type]
    builder.add_dimension(KA, KB, (0.0, 0.0, 3.0))

    model = _parse_bytes(tmp_path, builder.to_bytes(), "valid_after_invalid.skp")
    assert any(lay.name == "AfterRejected" for lay in model.layers)
    assert len(model.root.dimensions) == 1
    assert len(model.definitions) == 1
    def_dims = next(iter(model.definitions.values())).dimensions
    assert len(def_dims) == 1
    assert model.root.dimensions[0].offset_vector == pytest.approx((0.0, 0.0, 3.0), abs=1e-9)
    assert def_dims[0].offset_vector == pytest.approx((0.0, 0.0, 2.0), abs=1e-9)


def test_scalar_offset_accepts_decimal_and_float_like_numbers(tmp_path):
    from decimal import Decimal

    class FloatLike:
        def __init__(self, v: float) -> None:
            self._v = v

        def __float__(self) -> float:
            return self._v

    builder = create()
    builder.add_dimension((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), Decimal("3.5"))
    builder.add_dimension((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), FloatLike(4.25))
    try:
        import numpy as np  # type: ignore[import-not-found]
    except ImportError:
        np = None
    if np is not None:
        builder.add_dimension((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), np.float32(6.0))

    model = _parse_bytes(tmp_path, builder.to_bytes(), "decimal_offsets.skp")
    expected = [3.5, 4.25] + ([6.0] if np is not None else [])
    assert [d.offset for d in model.root.dimensions] == pytest.approx(expected, abs=1e-6)
