"""``original_textures=True`` keeps a texture a scene cannot carry as glTF in its own format.

SketchUp stores a TIFF, TGA or GIF texture as the file it was imported from (BMP it
converts to PNG), and a scene holds only PNG and JPEG unless asked otherwise, so such a
texture was left out with a warning (openskp#511). With ``original_textures=True`` it
stays in ``scene.textures`` with its real MIME type, for the consumers that read it
(Blender does), and the library's own GLB exporters, which decode textures through
Pillow, write it re-encoded as PNG.

Fixtures (one face, one textured material "Tex_<FORMAT>", saved by SketchUp's library):
``texture_tiff_2017.skp``, ``texture_tiff_2021.skp``, ``texture_tga_2021.skp``,
``texture_gif_2021.skp``, ``texture_bmp_2021.skp``.
"""
from __future__ import annotations

import json
import logging
import pathlib
import struct

import pytest

from openskp import SkpFile
from openskp.scene import _original_image_mime

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
BUILDERS = ("build_scene", "build_instanced_scene")
# fixture -> (material name, MIME type kept, signature of the file's bytes)
KEPT = {
    "texture_tiff_2017.skp": ("Tex_TIF", "image/tiff", b"II*\x00"),
    "texture_tiff_2021.skp": ("Tex_TIF", "image/tiff", b"II*\x00"),
    "texture_tga_2021.skp": ("Tex_TGA", "image/x-tga", None),
    "texture_gif_2021.skp": ("Tex_GIF", "image/gif", b"GIF89a"),
}


def _material(scene, name):
    return next(m for m in scene.gltf_materials if m.get("name") == name)


@pytest.mark.parametrize("builder", BUILDERS)
@pytest.mark.parametrize("fixture", sorted(KEPT))
def test_the_texture_is_kept_in_its_own_format_with_its_real_mime_type(caplog, fixture, builder):
    name, mime, signature = KEPT[fixture]
    skp = SkpFile.open(str(FIXTURES / fixture))
    with caplog.at_level(logging.WARNING, logger="openskp"):
        scene = getattr(skp, builder)(original_textures=True)

    (texture,) = scene.textures
    assert texture.mime_type == mime
    if signature is not None:
        assert texture.data.startswith(signature)
    # exactly the bytes SketchUp stored, the same ones parse() returns with the material
    (stored,) = [m.texture.data for m in skp.parse().materials if m.name == name]
    assert texture.data == stored
    assert _material(scene, name)["pbrMetallicRoughness"]["baseColorTexture"] == {"index": 0}
    assert not [r for r in caplog.records if r.getMessage().startswith("Texture of material")]


@pytest.mark.parametrize("builder", BUILDERS)
@pytest.mark.parametrize("fixture", sorted(KEPT))
def test_by_default_nothing_changes_but_the_warning_names_the_option(caplog, fixture, builder):
    name, _, _ = KEPT[fixture]
    skp = SkpFile.open(str(FIXTURES / fixture))
    with caplog.at_level(logging.WARNING, logger="openskp"):
        scene = getattr(skp, builder)()

    assert scene.textures == []
    assert "baseColorTexture" not in _material(scene, name)["pbrMetallicRoughness"]
    (message,) = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Texture of material")]
    assert "original_textures=True" in message


@pytest.mark.parametrize("builder", BUILDERS)
@pytest.mark.parametrize("fixture", ["texture_bmp_2021.skp", "capilla_quiroz_v17.skp"])
def test_png_jpeg_and_converted_bmp_textures_are_the_same_either_way(fixture, builder):
    skp = SkpFile.open(str(FIXTURES / fixture))
    plain = getattr(skp, builder)()
    kept = getattr(skp, builder)(original_textures=True)

    assert plain.textures, "the fixture must have textures for this test to mean anything"
    assert [(t.mime_type, t.data) for t in kept.textures] == [(t.mime_type, t.data) for t in plain.textures]
    assert kept.gltf_materials == plain.gltf_materials
    assert {t.mime_type for t in plain.textures} <= {"image/png", "image/jpeg"}


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"II*\x00" + b"\x08" * 12, "image/tiff"),
        (b"MM\x00*" + b"\x00" * 12, "image/tiff"),
        (b"GIF89a" + b"\x00" * 10, "image/gif"),
        (b"BM" + b"\x00" * 14, "image/bmp"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"),
        (b"\x00\x00\x02" + b"\x00" * 40 + b"TRUEVISION-XFILE.\x00", "image/x-tga"),
        # not a format worth guessing: the bytes themselves must say what it is
        (b"zzzzzzzzzzzzzzzz", None),
        (b"", None),
    ],
)
def test_original_image_mime(data, expected):
    assert _original_image_mime(data) == expected


def _glb_json(path):
    data = pathlib.Path(path).read_bytes()
    (length,) = struct.unpack_from("<I", data, 12)
    return json.loads(data[20:20 + length])


@pytest.mark.parametrize("exporter", ["glb", "instanced_glb"])
@pytest.mark.parametrize("fixture", ["texture_tiff_2021.skp", "texture_tga_2021.skp", "texture_gif_2021.skp"])
def test_a_glb_with_textures_carries_the_texture_as_png(tmp_path, fixture, exporter):
    pytest.importorskip("trimesh")
    pytest.importorskip("PIL")
    import importlib

    module = importlib.import_module(f"openskp.export.{exporter}")
    skp = SkpFile.open(str(FIXTURES / fixture))
    skp.parse()

    with_textures = tmp_path / "with.glb"
    module.export(skp, str(with_textures), textures=True)
    document = _glb_json(with_textures)
    assert [image["mimeType"] for image in document["images"]] == ["image/png"]
    assert len(document["textures"]) == 1

    without_textures = tmp_path / "without.glb"
    module.export(skp, str(without_textures), textures=False)
    assert "images" not in _glb_json(without_textures)
