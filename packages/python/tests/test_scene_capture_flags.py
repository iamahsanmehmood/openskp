"""Which properties a scene saves: ``Page.capture_flags``.

An empty ``hidden_entity_ids`` means two different things: a scene that
saves its hidden objects and hides none ("All" below: picking it shows
everything again), and a scene that does not save hidden objects at all
("Not saved": picking it leaves them as they are). The flags tell them
apart. 2021+ files store them as tag ``7149`` of the scene's ``7148``
record; classic files as the u32 after the scene's description.
"""

from pathlib import Path

import pytest

from openskp import SkpFile
from openskp.model import Page

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CPP_FIXTURES = Path(__file__).resolve().parents[2] / "cpp" / "tests" / "fixtures"

HIDDEN_ENTITIES = (
    "scene_hidden_entities_2017.skp",
    "scene_hidden_entities_2019.skp",
    "scene_hidden_entities_2020.skp",
    "scene_hidden_entities_2024.skp",
)


def _pages(path: Path) -> dict:
    return {p.name: p for p in SkpFile.open(path).parse().pages}


@pytest.mark.parametrize("name", HIDDEN_ENTITIES)
def test_an_empty_hidden_list_is_told_apart(name: str) -> None:
    pages = _pages(CPP_FIXTURES / name)
    assert pages["All"].hidden_entity_ids == []
    assert pages["Not saved"].hidden_entity_ids == []
    # Camera + hidden geometry + hidden objects + the older combined bit.
    assert pages["All"].capture_flags == 0x191
    assert pages["All"].use_hidden_objects is True
    assert pages["All"].use_hidden_geometry is True
    assert pages["Not saved"].capture_flags == 0x001
    assert pages["Not saved"].use_hidden_objects is False
    assert pages["Not saved"].use_hidden_geometry is False
    assert pages["Not saved"].use_camera is True


@pytest.mark.parametrize("name", HIDDEN_ENTITIES)
def test_every_scene_that_hides_something_saves_it(name: str) -> None:
    for page in _pages(CPP_FIXTURES / name).values():
        if page.hidden_entity_ids:
            assert page.use_hidden_objects or page.use_hidden_geometry


def test_scenes_that_save_no_camera() -> None:
    pages = _pages(FIXTURES / "scene_without_camera_2021.skp")
    assert pages["With camera"].capture_flags == 0x001
    assert pages["With camera"].use_camera is True
    assert pages["With camera"].use_hidden_layers is False
    # "Camera Location" unticked, "Visible Tags" ticked: it hides Furniture.
    assert pages["No camera"].capture_flags == 0x020
    assert pages["No camera"].use_camera is False
    assert pages["No camera"].use_hidden_layers is True
    assert pages["No camera"].eye is None
    assert pages["Plain"].capture_flags == 0x000
    assert pages["Plain"].use_camera is False
    assert pages["Plain"].use_hidden_layers is False


@pytest.mark.parametrize(
    "name", ("sdk_saved_sections_2019.skp", "sdk_saved_sections_2020.skp"))
def test_all_or_nothing(name: str) -> None:
    pages = _pages(FIXTURES / name)
    everything, plain = pages["Everything"], pages["Plain"]
    assert everything.capture_flags == 0x1FF
    assert plain.capture_flags == 0x000
    for prop in ("use_camera", "use_rendering_options", "use_shadow_info",
                 "use_axes", "use_hidden_layers", "use_section_planes",
                 "use_hidden_geometry", "use_hidden_objects"):
        assert getattr(everything, prop) is True, prop
        assert getattr(plain, prop) is False, prop


def test_an_application_saved_scene() -> None:
    [page] = _pages(FIXTURES / "gondola_v20.skp").values()
    assert page.capture_flags == 0x1FF
    assert page.use_hidden_geometry is True


def test_the_older_combined_bit_covers_both() -> None:
    # A file from before SketchUp 2020.1 sets only 0x010 for both kinds.
    old = Page(capture_flags=0x011)
    assert old.use_hidden_geometry is True
    assert old.use_hidden_objects is True
    # Once either new bit is set, they decide on their own.
    split = Page(capture_flags=0x111)
    assert split.use_hidden_objects is True
    assert split.use_hidden_geometry is False


def test_no_flags_means_unknown() -> None:
    page = Page()
    assert page.capture_flags is None
    assert page.use_camera is None
    assert page.use_hidden_objects is None
