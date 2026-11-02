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
