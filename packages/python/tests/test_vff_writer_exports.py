"""Public exports for the VFF writer."""

from __future__ import annotations

import importlib


def test_public_vff_exports():
    import openskp

    assert "create_vff" in openskp.__all__
    assert "VffBuilder" in openskp.__all__
    assert "VffComponentDefinitionBuilder" in openskp.__all__
    assert callable(openskp.create) and callable(openskp.create_vff)
    assert isinstance(openskp.create(), openskp.SkpBuilder)
    assert isinstance(openskp.create_vff(), openskp.VffBuilder)

    module = importlib.import_module("openskp.create_vff")
    assert module.VffBuilder is openskp.VffBuilder
    assert isinstance(module.create(), module.VffBuilder)
    assert isinstance(module.create_vff(), module.VffBuilder)
    assert module.VffComponentDefinitionBuilder is not None
