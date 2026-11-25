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

CAPTURE_FLAG_FILES = tuple(
    f"scene_capture_flags_{yr}.skp" for yr in range(2015, 2021)
)

SCENE_ORDER = [
    "Camera",
    "RenderingOptions",
    "ShadowInfo",
    "Axes",
    "Hidden",
    "HiddenGeometry",
    "HiddenObjects",
    "HiddenLayers",
    "SectionPlanes",
    "ShadowsCamera",
    "AxesCamera",
]

ALL_USE_PROPS = (
    "use_camera",
    "use_rendering_options",
    "use_shadow_info",
    "use_axes",
    "use_hidden_layers",
    "use_section_planes",
    "use_hidden_geometry",
    "use_hidden_objects",
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


@pytest.mark.parametrize("name", CAPTURE_FLAG_FILES)
def test_classic_2015_to_2020_one_flag_per_scene(name: str) -> None:
    """#661: shadows without axes, axes without shadows, and 2015/2016 schema 15."""
    model = SkpFile.open(CPP_FIXTURES / name).parse()
    assert [p.name for p in model.pages] == SCENE_ORDER
    pages = {p.name: p for p in model.pages}

    expected = {
        "Camera": (0x001, {"use_camera"}),
        "RenderingOptions": (0x002, {"use_rendering_options"}),
        "ShadowInfo": (0x004, {"use_shadow_info"}),
        "Axes": (0x008, {"use_axes"}),
        "Hidden": (0x190, {"use_hidden_geometry", "use_hidden_objects"}),
        "HiddenGeometry": (0x090, {"use_hidden_geometry"}),
        "HiddenObjects": (0x110, {"use_hidden_objects"}),
        "HiddenLayers": (0x020, {"use_hidden_layers"}),
        "SectionPlanes": (0x040, {"use_section_planes"}),
        "ShadowsCamera": (0x005, {"use_shadow_info", "use_camera"}),
        "AxesCamera": (0x009, {"use_axes", "use_camera"}),
    }
    for scene_name, (want_flags, true_props) in expected.items():
        p = pages[scene_name]
        assert p.capture_flags == want_flags, scene_name
        for prop in ALL_USE_PROPS:
            assert getattr(p, prop) is (prop in true_props), (scene_name, prop)

    # Cameras on the three scenes that save Camera Location (and absent on the rest)
    assert pages["Camera"].eye == (120.0, -240.0, 90.0)
    assert pages["Camera"].target == (5.0, 5.0, 0.0)
    assert pages["Camera"].up == pytest.approx(
        (-0.13407807210149128, 0.28564458839013357, 0.9489100271917137)
    )
    assert pages["Camera"].parallel is False
    assert pages["Camera"].fov == pytest.approx(42.0)

    assert pages["ShadowsCamera"].eye == (0.0, -300.0, 150.0)
    assert pages["ShadowsCamera"].target == (0.0, 0.0, 0.0)
    assert pages["ShadowsCamera"].up == pytest.approx(
        (0.0, 0.44721359549995804, 0.8944271909999161)
    )
    assert pages["ShadowsCamera"].parallel is True
    assert pages["ShadowsCamera"].ortho_height == pytest.approx(80.0)

    assert pages["AxesCamera"].eye == (-180.0, -180.0, 110.0)
    assert pages["AxesCamera"].target == (10.0, 10.0, 0.0)
    assert pages["AxesCamera"].up == pytest.approx(
        (0.2678945632789157, 0.2678945632789157, 0.9254539458726179)
    )
    assert pages["AxesCamera"].parallel is False
    assert pages["AxesCamera"].fov == pytest.approx(55.0)

    for scene_name in SCENE_ORDER:
        if scene_name not in ("Camera", "ShadowsCamera", "AxesCamera"):
            assert pages[scene_name].eye is None, scene_name
            assert pages[scene_name].target is None, scene_name
        if scene_name == "HiddenLayers":
            assert pages[scene_name].hidden_layers == ["HiddenTag"]
        else:
            assert pages[scene_name].hidden_layers == [], scene_name


@pytest.mark.parametrize("year", (2015, 2016, 2017, 2018, 2019, 2020))
def test_classic_2015_to_2020_hidden_entities(year: int) -> None:
    model = SkpFile.open(CPP_FIXTURES / f"scene_capture_flags_{year}.skp").parse()
    pages = {p.name: p for p in model.pages}
    if year < 2017:
        for p in model.pages:
            assert p.hidden_entity_ids == []
    else:
        crate_id = next(i.id for i in model.root.instances if i.name == "Crate")
        assert crate_id == 30
        assert any(e.persistent_id == 21 for e in model.root.edges.values())
        assert set(pages["Hidden"].hidden_entity_ids) == {21, 30}
        assert pages["HiddenGeometry"].hidden_entity_ids == [21]
        assert pages["HiddenObjects"].hidden_entity_ids == [30]
        for scene_name in SCENE_ORDER:
            if scene_name not in ("Hidden", "HiddenGeometry", "HiddenObjects"):
                assert pages[scene_name].hidden_entity_ids == []

