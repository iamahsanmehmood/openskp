"""Regression tests for hosts without ``mapbox_earcut`` (FreeCAD's bundled
Python, reported on freecad-openskp#3).

``mapbox_earcut`` is a compiled dependency, and ``_core`` used to import it
at the top of the module - so ``import openskp`` itself raised
``ModuleNotFoundError`` in an interpreter that couldn't install it, even for
callers that only parse and never triangulate. It is now optional: parsing
and everything else works, and the one place that needs it (face
triangulation for the scene/mesh path) falls back to fan triangulation with a
one-time warning.
"""
from __future__ import annotations

import logging
import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

import openskp
from openskp import _core

# A U-shaped (concave) outline, counter-clockwise, in the z=0 plane: a 6x4
# block with a 2x2 notch cut from the top, area 20. No single vertex sees the
# whole outline, so a fan over it overlaps itself (absolute areas sum to 28).
U_VERTICES = {
    1: (0.0, 0.0, 0.0),
    2: (6.0, 0.0, 0.0),
    3: (6.0, 4.0, 0.0),
    4: (4.0, 4.0, 0.0),
    5: (4.0, 2.0, 0.0),
    6: (2.0, 2.0, 0.0),
    7: (2.0, 4.0, 0.0),
    8: (0.0, 4.0, 0.0),
}
U_LOOP = [1, 2, 3, 4, 5, 6, 7, 8]
U_AREA = 20.0


def _abs_area_sum(triangles):
    total = 0.0
    for tri in triangles:
        (x1, y1, _), (x2, y2, _), (x3, y3, _) = (U_VERTICES[v] for v in tri)
        total += abs((x2 - x1) * (y3 - y1) - (x3 - x1) * (y2 - y1)) / 2
    return total


def test_import_and_parse_work_without_mapbox_earcut(tmp_path):
    # A real import failure, not a patched attribute: block the module in a
    # fresh interpreter and exercise the same entry points the FreeCAD
    # importer uses.
    src = pathlib.Path(openskp.__file__).resolve().parent.parent
    script = textwrap.dedent(
        f"""
        import sys
        sys.modules["mapbox_earcut"] = None  # makes `import mapbox_earcut` raise ImportError
        import openskp
        from openskp import create, SkpFile

        b = create()
        b.add_face([(0, 0, 0), (6, 0, 0), (6, 4, 0), (4, 4, 0), (4, 2, 0), (2, 2, 0), (2, 4, 0), (0, 4, 0)])
        path = {str(tmp_path / "concave.skp")!r}
        b.save(path)

        model = SkpFile.open(path).parse()
        assert len(model.root.faces) >= 1
        assert openskp._core.mapbox_earcut is None

        # The mesh path still works too, via the fan fallback.
        scene = SkpFile.open(path).build_scene()
        assert scene.glb_primitives
        print("OK")
        """
    )
    env = dict(os.environ, PYTHONPATH=str(src))
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_missing_earcut_falls_back_to_fan_and_warns_once(monkeypatch, caplog):
    monkeypatch.setattr(_core, "mapbox_earcut", None)
    monkeypatch.setattr(_core, "_earcut_warned", False)

    with caplog.at_level(logging.WARNING, logger="openskp"):
        first = _core.triangulate_face_3d(U_VERTICES, [U_LOOP], (0, 0, 1))
        second = _core.triangulate_face_3d(U_VERTICES, [U_LOOP], (0, 0, 1))

    # A fan over an 8-sided outline: n - 2 triangles, all rooted at vertex 1.
    assert first == [[1, i, i + 1] for i in range(2, 8)]
    assert _abs_area_sum(first) != U_AREA  # the fallback really is the lossy one
    assert second == first
    warnings = [r for r in caplog.records if "mapbox_earcut is not installed" in r.getMessage()]
    assert len(warnings) == 1


def test_installed_earcut_still_triangulates_the_concave_outline_correctly():
    # With the dependency present the result must be a true triangulation of
    # the U, not the overlapping fan.
    pytest.importorskip("mapbox_earcut")
    triangles = _core.triangulate_face_3d(U_VERTICES, [U_LOOP], (0, 0, 1))
    assert len(triangles) == 6
    assert _abs_area_sum(triangles) == U_AREA
