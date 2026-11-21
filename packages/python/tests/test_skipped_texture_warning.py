"""A texture the scene cannot carry is skipped with a warning, not silently.

A scene is glTF-shaped and glTF holds only PNG and JPEG. SketchUp keeps a texture in
the format it was imported in, except BMP, which it converts to PNG (checked with
SketchUp's own library on one model per format: TIFF, TGA and GIF stay as they are).
Before, a TIFF, TGA or GIF texture simply vanished from ``build_scene`` and
``build_instanced_scene``, and the material came out untextured with no hint why
(openskp#511, blender-openskp#17).

Fixtures, each one face with one textured material "Tex_<FORMAT>", saved by SketchUp's
library: ``texture_tiff_2017.skp`` (classic format), ``texture_tiff_2021.skp`` and
``texture_bmp_2021.skp``.
"""
from __future__ import annotations

import logging
import pathlib

import pytest

from openskp import SkpFile
from openskp.scene import _describe_image_format

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
BUILDERS = ("build_scene", "build_instanced_scene")


def _skipped_texture_warnings(caplog):
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING and record.getMessage().startswith("Texture of material")
    ]


@pytest.mark.parametrize("builder", BUILDERS)
@pytest.mark.parametrize("fixture", ["texture_tiff_2017.skp", "texture_tiff_2021.skp"])
def test_a_tiff_texture_is_skipped_with_a_warning_naming_the_material_and_format(caplog, fixture, builder):
    skp = SkpFile.open(str(FIXTURES / fixture))
    with caplog.at_level(logging.WARNING, logger="openskp"):
        scene = getattr(skp, builder)()

    assert scene.textures == []
    # the material is still built, only without its texture
    assert "Tex_TIF" in [material.get("name") for material in scene.gltf_materials]
    (message,) = _skipped_texture_warnings(caplog)  # once, though the face is seen from both sides
    assert "'Tex_TIF'" in message and "TIFF" in message and "tex.tif" in message
    assert "C:" not in message and "/" not in message, "the message must not carry the source path"


@pytest.mark.parametrize("builder", BUILDERS)
def test_a_bmp_texture_needs_no_warning_because_sketchup_stores_it_as_png(caplog, builder):
    skp = SkpFile.open(str(FIXTURES / "texture_bmp_2021.skp"))
    with caplog.at_level(logging.WARNING, logger="openskp"):
        scene = getattr(skp, builder)()

    assert [texture.mime_type for texture in scene.textures] == ["image/png"]
    assert _skipped_texture_warnings(caplog) == []


@pytest.mark.parametrize("builder", BUILDERS)
def test_png_and_jpeg_textures_give_no_warning(caplog, builder):
    # capilla_quiroz_v17.skp has PNG and JPEG textured materials
    skp = SkpFile.open(str(FIXTURES / "capilla_quiroz_v17.skp"))
    with caplog.at_level(logging.WARNING, logger="openskp"):
        scene = getattr(skp, builder)()

    assert scene.textures, "the fixture must have textures for this test to mean anything"
    assert _skipped_texture_warnings(caplog) == []


@pytest.mark.parametrize(
    ("data", "filename", "expected"),
    [
        (b"II*\x00" + b"\x08" * 12, "", "TIFF"),
        (b"MM\x00*" + b"\x00" * 12, "", "TIFF"),
        (b"GIF89a" + b"\x00" * 10, "", "GIF"),
        (b"GIF87a" + b"\x00" * 10, "", "GIF"),
        (b"BM" + b"\x00" * 14, "", "BMP"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "", "WebP"),
        (b"8BPS" + b"\x00" * 12, "", "PSD"),
        (b"DDS " + b"\x00" * 12, "", "DDS"),
        (b"\x00\x00\x00\x0cjP  " + b"\x00" * 8, "", "JPEG 2000"),
        # a TGA has no signature at the start, only a footer
        (b"\x00\x00\x02" + b"\x00" * 40 + b"TRUEVISION-XFILE.\x00", "", "TGA"),
        # no signature: the source file's extension, either kind of separator
        (b"zzzzzzzzzzzzzzzz", r"C:\textures\wall.PCX", "PCX"),
        (b"zzzzzzzzzzzzzzzz", "textures/wall.tga", "TGA"),
        # nothing to go on
        (b"zzzzzzzzzzzzzzzz", "", "an unrecognized format"),
        (b"zzzzzzzzzzzzzzzz", "no_extension", "an unrecognized format"),
        (b"zzzzzzzzzzzzzzzz", "wall.averylongextension", "an unrecognized format"),
    ],
)
def test_the_format_is_named_from_the_bytes_then_the_file_name(data, filename, expected):
    assert _describe_image_format(data, filename) == expected
