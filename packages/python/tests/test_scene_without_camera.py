"""A scene that doesn't save its camera (openskp#447).

SketchUp lets a scene leave "Camera Location" unticked: such a scene saves no
camera and still carries its name and the layers it hides. The 2021+ reader
used to drop it - and, for a scene with no camera record at all, raised.

Fixture: ``scene_without_camera_2021.skp`` - one layer ("Furniture"), one face
and three scenes built through SketchUp's own library: "With camera" (eye
100, -200, 80 looking at 5, 5, 0), "No camera" (``SUSceneSetUseCamera`` off,
hides Furniture) and "Plain" (no camera, hides nothing). SketchUp reports the
same three scenes. No private content.
"""
from __future__ import annotations

import pathlib

from openskp import SkpFile

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "scene_without_camera_2021.skp"


def test_every_scene_is_read():
    model = SkpFile.open(str(FIXTURE)).parse()
    assert [p.name for p in model.pages] == ["With camera", "No camera", "Plain"]


def test_a_saved_camera_is_still_read():
    page = SkpFile.open(str(FIXTURE)).parse().pages[0]
    assert page.eye == (100.0, -200.0, 80.0)
    assert page.target == (5.0, 5.0, 0.0)


def test_a_scene_without_a_camera_keeps_its_hidden_layers():
    no_camera, plain = SkpFile.open(str(FIXTURE)).parse().pages[1:]
    assert no_camera.eye is None and no_camera.target is None and no_camera.up is None
    assert no_camera.hidden_layers == ["Furniture"]
    assert plain.eye is None and plain.hidden_layers == []
