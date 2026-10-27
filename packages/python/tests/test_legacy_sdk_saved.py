"""Files saved through SketchUp's C API as 2019 or 2020.

A model saved through the SketchUp library as SketchUp 2019 or 2020 has a null
entry in its layer list. The slot-base probe (``_probe_layer_anchor_bases``)
tried to subtract from that entry's missing slot and raised ``TypeError``, so
``parse()`` failed on any such file with a second layer.

Fixtures: ``sdk_saved_2019.skp`` and ``sdk_saved_2020.skp`` - one extra layer
("Furniture"), one face and three scenes, built and saved through the installed
SketchUp library. SketchUp reads back the same layers, face and scenes. No
private content.

``sdk_saved_sections_2019.skp`` and ``..._2020.skp`` are the same model with
two scenes, the first one capturing everything a scene can (capture flags
0x1FF: camera, rendering options and style, shadows, axes, hidden geometry and
objects, hidden layers, section planes).

The scenes in such a file are schema 15 records (SketchUp's desktop writes 12
and 13): the camera is only there when its flag is, there is no thumbnail, the
trailer is a byte shorter and the rendering options 3 bytes longer.
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


@pytest.mark.parametrize("name", ["sdk_saved_2019.skp", "sdk_saved_2020.skp"])
def test_the_scenes_are_read_with_their_cameras_and_hidden_layers(name):
    model = SkpFile.open(str(FIXTURES / name)).parse()
    with_camera, no_camera, plain = model.pages
    assert with_camera.name == "With camera"
    assert with_camera.eye == (100.0, -200.0, 80.0)
    assert with_camera.target == (5.0, 5.0, 0.0)
    assert with_camera.fov == 30.0
    assert with_camera.parallel is False
    assert with_camera.hidden_layers == []
    # a scene that doesn't save its camera keeps its name and its hidden layers
    assert no_camera.name == "No camera"
    assert no_camera.eye is None and no_camera.target is None
    assert no_camera.hidden_layers == ["Furniture"]
    assert plain.name == "Plain"
    assert plain.eye is None
    assert plain.hidden_layers == []


@pytest.mark.parametrize("name", ["sdk_saved_sections_2019.skp", "sdk_saved_sections_2020.skp"])
def test_a_scene_capturing_every_property_is_read_to_its_end(name):
    model = SkpFile.open(str(FIXTURES / name)).parse()
    everything, plain = model.pages
    assert everything.name == "Everything"
    assert everything.eye == (100.0, -200.0, 80.0)
    assert everything.target == (5.0, 5.0, 0.0)
    assert everything.hidden_layers == ["Furniture"]
    # the scene after it is only found if the first was read to its last byte
    assert plain.name == "Plain"
    assert plain.eye is None
