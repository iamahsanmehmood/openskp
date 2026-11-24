"""Scene texture deduplication uses full byte content rather than a 16-byte prefix (#645)."""
from __future__ import annotations

import struct
import zlib

import pytest

from openskp import SkpFile, create


def _uncompressed_png(size: int, rgb: tuple[int, int, int]) -> bytes:
    """Valid RGB PNG stored with zlib level=0 so every image of *size* has the
    exact same byte length regardless of pixel values."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
    raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))
    idat = chunk(b"IDAT", zlib.compress(raw, level=0))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


@pytest.mark.parametrize("builder_name", ["build_scene", "build_instanced_scene"])
def test_two_different_pngs_of_equal_byte_length_stay_two_textures(tmp_path, builder_name):
    png_a = _uncompressed_png(8, (220, 40, 30))
    png_b = _uncompressed_png(8, (30, 90, 220))
    assert len(png_a) == len(png_b)
    assert png_a[:16] == png_b[:16]
    assert png_a != png_b

    path_a = tmp_path / "a.png"
    path_b = tmp_path / "b.png"
    path_a.write_bytes(png_a)
    path_b.write_bytes(png_b)

    b = create()
    mat_a = b.add_texture_material("MatA", str(path_a))
    mat_b = b.add_texture_material("MatB", str(path_b))
    b.add_face([(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)], material=mat_a)
    b.add_face([(15.0, 0.0, 0.0), (25.0, 0.0, 0.0), (25.0, 10.0, 0.0), (15.0, 10.0, 0.0)], material=mat_a)
    b.add_face([(30.0, 0.0, 0.0), (40.0, 0.0, 0.0), (40.0, 10.0, 0.0), (30.0, 10.0, 0.0)], material=mat_b)

    skp_path = tmp_path / "two_pngs.skp"
    b.save(str(skp_path))

    scene = getattr(SkpFile.open(str(skp_path)), builder_name)()
    assert [t.data for t in scene.textures] == [png_a, png_b]
    textured_indices = sorted(
        m["pbrMetallicRoughness"]["baseColorTexture"]["index"]
        for m in scene.gltf_materials
        if "baseColorTexture" in m["pbrMetallicRoughness"]
    )
    assert textured_indices == [0, 1]


@pytest.mark.parametrize("builder_name", ["build_scene", "build_instanced_scene"])
def test_identical_png_bytes_in_two_materials_still_deduplicate_to_one_texture(tmp_path, builder_name):
    png = _uncompressed_png(8, (220, 40, 30))
    path_1 = tmp_path / "one.png"
    path_2 = tmp_path / "two.png"
    path_1.write_bytes(png)
    path_2.write_bytes(bytes(png))

    b = create()
    mat_1 = b.add_texture_material("Mat1", str(path_1))
    mat_2 = b.add_texture_material("Mat2", str(path_2), opacity=0.6)
    b.add_face([(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)], material=mat_1)
    b.add_face([(20.0, 0.0, 0.0), (30.0, 0.0, 0.0), (30.0, 10.0, 0.0), (20.0, 10.0, 0.0)], material=mat_2)

    skp_path = tmp_path / "same_png.skp"
    b.save(str(skp_path))

    scene = getattr(SkpFile.open(str(skp_path)), builder_name)()
    assert len(scene.textures) == 1
    assert scene.textures[0].data == png
    textured_indices = [
        m["pbrMetallicRoughness"]["baseColorTexture"]["index"]
        for m in scene.gltf_materials
        if "baseColorTexture" in m["pbrMetallicRoughness"]
    ]
    assert textured_indices == [0, 0]
