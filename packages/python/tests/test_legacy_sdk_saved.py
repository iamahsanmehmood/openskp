"""Files saved through SketchUp's C API as 2019 or 2020.

A model saved through the SketchUp library as SketchUp 2019 or 2020 has a null
entry in its layer list. The slot-base probe (``_probe_layer_anchor_bases``)
tried to subtract from that entry's missing slot and raised ``TypeError``, so
``parse()`` failed on any such file with a second layer.

Fixtures: ``sdk_saved_2019.skp`` and ``sdk_saved_2020.skp`` - one extra layer
("Furniture"), one face and three scenes, built and saved through the installed
SketchUp library. SketchUp reads back the same layers, face and scenes. No
private content.
"""
from __future__ import annotations

import pathlib

import pytest

from openskp import SkpFile

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("name", ["sdk_saved_2019.skp", "sdk_saved_2020.skp"])
def test_the_layers_and_geometry_are_read(name):
    model = SkpFile.open(str(FIXTURES / name)).parse()
    assert [layer.name for layer in model.layers] == ["Layer0", "Furniture"]
    assert len(model.root.faces) == 1
    assert len(model.root.edges) == 4
