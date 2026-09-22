"""Regression for SketchUp 2018 (file version 18) saves with no CMaterial
records and a custom tag listed ahead of Layer0.

Those files take ``_probe_layer_anchor_bases`` (the two-material bootstrap
needs ``mat_count >= 2``). The declared ``layer_count`` is 1; Layer0
follows after a 16-byte colour-layer extension. Missing that extension
left the probe on padding: ``base probe: anchor resolved to``.

Fixture is a SketchUp 2018 layout sample (two construction lines, no
faces). Identifiable strings were replaced with same-length ASCII so the
MFC record sizes are unchanged.
"""
from __future__ import annotations

import pathlib

from openskp import SkpFile
from openskp.legacy import is_legacy

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "zero_material_custom_layer_v18.skp"


class TestLegacyZeroMaterialCustomLayer:
    def test_detects_the_fixture_as_legacy(self):
        assert is_legacy(FIXTURE.read_bytes()) is True

    def test_parses_v18_custom_tag_ahead_of_layer0(self):
        model = SkpFile.open(str(FIXTURE)).parse()
        assert model.version == "{18.0.16975}"
        assert len(model.materials) == 0
        names = [layer.name for layer in model.layers]
        assert "Layer0" in names
        assert "Guide" in names
        assert len(model.layers) >= 2
        assert model.root.faces == {}
        assert len(model.root.construction_lines) == 2
