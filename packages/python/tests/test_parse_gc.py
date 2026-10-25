"""SkpFile.parse pauses automatic cyclic GC and restores the caller's state."""
import gc

import pytest

from openskp.model import SkpFile


@pytest.mark.parametrize("initially_enabled", [False, True])
@pytest.mark.parametrize("failure", [None, ValueError, KeyboardInterrupt])
def test_parse_pauses_cyclic_gc_and_restores_state(monkeypatch, tmp_path,
                                                   initially_enabled, failure):
    original_state = gc.isenabled()
    sentinel = object()
    seen = []

    def pipeline(self):
        seen.append(gc.isenabled())
        if failure:
            raise failure("interrupted parse")
        return sentinel

    monkeypatch.setattr(SkpFile, "_parse_pipeline", pipeline)
    skp = SkpFile(path=tmp_path / "sample.skp")
    try:
        (gc.enable if initially_enabled else gc.disable)()
        if failure:
            with pytest.raises(failure):
                skp.parse()
        else:
            assert skp.parse() is sentinel
        assert seen == [False]
        assert gc.isenabled() is initially_enabled
    finally:
        (gc.enable if original_state else gc.disable)()
