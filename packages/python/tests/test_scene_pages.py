"""Saved scenes (``pages``) on ``Scene`` and ``InstancedScene`` (openskp#593)."""

from __future__ import annotations

import pathlib

import pytest

from openskp import SkpFile
from openskp.export.fragments import from_fragments, to_fragments

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("name", "expected_names"),
    [
        ("gondola_v20.skp", ["Cena1"]),
        ("sdk_saved_2020.skp", ["With camera", "No camera", "Plain"]),
        ("scene_without_camera_2021.skp", ["With camera", "No camera", "Plain"]),
        ("nesting_2017.skp", []),
    ],
)
def test_scene_and_instanced_scene_pages_equal_parsed_model_pages(
    name: str,
    expected_names: list[str],
) -> None:
    skp = SkpFile.open(FIXTURES / name)
    model = skp.parse()
    scene = skp.build_scene()
    instanced = skp.build_instanced_scene()

    assert [p.name for p in model.pages] == expected_names
    assert scene.pages == model.pages
    assert instanced.pages == model.pages


def test_scene_pages_keep_native_inches_and_hidden_layers() -> None:
    skp = SkpFile.open(FIXTURES / "scene_without_camera_2021.skp")
    for pages in (skp.build_scene().pages, skp.build_instanced_scene().pages):
        assert pages[0].eye == (100.0, -200.0, 80.0)
        assert pages[0].target == (5.0, 5.0, 0.0)
        assert pages[1].eye is None
        assert pages[1].hidden_layers == ["Furniture"]


def test_fragments_round_trip_reports_empty_pages() -> None:
    instanced = SkpFile.open(FIXTURES / "scene_without_camera_2021.skp").build_instanced_scene()
    assert len(instanced.pages) == 3
    reloaded = from_fragments(to_fragments(instanced))
    assert reloaded.pages == []
