"""Textured and colorized materials in a classic file read as SketchUp reads them.

Fixture ``legacy_textured_colorize_2017.skp``: four materials with the same 2x1 texture, made and
saved as a SketchUp 2017 file by SketchUp's own library: ``plain`` (type 1, textured), ``tint`` and
``shift`` (type 2, colorized, colorize mode tint 1 and shift 0) and ``tint_half`` (a tint at opacity
0.5). Before, every classic textured material read as colorized and the mode was always tint
(96 of 117 textured materials of the differential test files disagreed with SketchUp's own
``SUMaterialGetType``). No private content.
"""
from __future__ import annotations

import pathlib

from openskp import SkpFile

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "legacy_textured_colorize_2017.skp"


def test_texture_type_colorize_mode_and_paint_colour():
    model = SkpFile.open(str(FIXTURE)).parse()
    got = {
        m.name: (tuple(m.color[:3]), m.colorized, m.colorize_type, round(m.transparency, 2))
        for m in model.materials
    }
    assert got == {
        "plain": ((120, 40, 120), False, 0, 1.0),
        "tint": ((30, 160, 60), True, 1, 1.0),
        "shift": ((200, 90, 20), True, 0, 1.0),
        "tint_half": ((10, 20, 230), True, 1, 0.5),
    }


def test_colour_alpha_is_the_material_colours_not_the_image_averages(tmp_path):
    """blender-openskp#9: a SketchUp 2017 textured material stores two colours side by side and the
    first one's alpha byte can be 0 (as in the box the report attached). Taking the colour and its alpha
    from the first made every textured material fully transparent in the Blender add-on. Here the first
    colour of ``plain`` (120, 40, 120) gets alpha 0, as in that file."""
    data = FIXTURE.read_bytes()
    pair = bytes.fromhex("782878ff00782878ff")      # image average, 00, material colour (alpha 255)
    assert data.count(pair) == 1
    patched = tmp_path / "alpha0.skp"
    patched.write_bytes(data.replace(pair, bytes.fromhex("7828780000782878ff")))
    plain = {m.name: m for m in SkpFile.open(str(patched)).parse().materials}["plain"]
    assert tuple(plain.color) == (120, 40, 120, 255)
