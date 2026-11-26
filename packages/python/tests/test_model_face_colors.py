"""The model's face colours: what SketchUp shows on the front and the back side
of a face with no paint on that side (openskp#533).

They come from the model's current rendering options, not from a saved style:
in a 2021+ file the items 0x735C / 0x735D of model.dat, in a classic file two
RGBA values at a distance after the model's camera that depends on the
version (``legacy._FACE_COLORS_AFTER_CAMERA``).

Fixtures ``face_colors_<version>.skp``: one face, written with SketchUp's own
library (SketchUp 2025 SDK) after setting the rendering options FaceFrontColor
to (11, 122, 233) and FaceBackColor to (201, 102, 3), saved in one version per
distinct classic layout (4, 5, 6, 2016, 2017, 2018, 2019, 2020) and as 2021.
SketchUp reads those colours back from each. No private content.

The other cases are the colours SketchUp's own library reports for existing
fixtures, two of them saved by the SketchUp application (capilla_quiroz_v17,
gondola_v20) with the default style of SketchUp 2017+, the others with the
default of the library.
"""
from __future__ import annotations

import pathlib

import pytest

from openskp import DocumentOptions, SkpFile, create

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

WRITTEN = ((11, 122, 233), (201, 102, 3))
APP_DEFAULT = ((255, 255, 255), (164, 178, 187))   # SketchUp 2017+ default style
SDK_DEFAULT = ((225, 225, 200), (128, 128, 200))   # SketchUp's library, new model

CASES = {
    **{f"face_colors_{v}.skp": WRITTEN
       for v in ("v4", "v5", "v6", "2016", "2017", "2018", "2019", "2020", "2021")},
    "capilla_quiroz_v17.skp": APP_DEFAULT,
    "gondola_v20.skp": APP_DEFAULT,
    "zero_material_custom_layer_v18.skp": APP_DEFAULT,
    "SU_File.skp": APP_DEFAULT,
    "nesting_v6.skp": SDK_DEFAULT,
    "nesting_2013.skp": SDK_DEFAULT,
    "nesting_2021.skp": SDK_DEFAULT,
    "legacy_v4_synthetic.skp": SDK_DEFAULT,
    "legacy_v5_synthetic.skp": SDK_DEFAULT,
    "legacy_v2019_synthetic.skp": SDK_DEFAULT,
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_face_colors_are_what_sketchup_reports(name):
    model = SkpFile.open(FIXTURES / name).parse()
    assert (model.face_front_color, model.face_back_color) == CASES[name]


def test_sketchup_3_gives_none():
    # SketchUp 3 keeps its camera in a fixed block of the thumbnail; where the
    # rendering options sit after it is not measured, so nothing is read.
    model = SkpFile.open(FIXTURES / "legacy_v3_synthetic.skp").parse()
    assert model.face_front_color is None and model.face_back_color is None


def test_the_current_colours_not_a_saved_style():
    # The 2021 fixture keeps the library's default style next to the one the
    # changed rendering options make; the model's face colours are the latter.
    model = SkpFile.open(FIXTURES / "face_colors_2021.skp").parse()
    assert {(s.front_color, s.back_color) for s in model.styles} >= {SDK_DEFAULT, WRITTEN}
    assert (model.face_front_color, model.face_back_color) == WRITTEN


COLOR_BY_TAG_CASES = {
    "color_by_tag_v4.skp": True,
    "color_by_tag_v5.skp": True,
    "color_by_tag_v6.skp": True,
    "color_by_tag_2017.skp": True,
    "color_by_tag_2019.skp": True,
    "color_by_tag_2020.skp": True,
    "color_by_tag_2020_off.skp": False,
    "color_by_tag_2021.skp": True,
    "color_by_tag_2021_style.skp": True,
    "color_by_tag_2021_off.skp": False,
    "legacy_v3_synthetic.skp": False,
    **{name: False for name in CASES},
}


@pytest.mark.parametrize("name", sorted(COLOR_BY_TAG_CASES))
def test_color_by_tag_matches_sketchup(name):
    model = SkpFile.open(FIXTURES / name).parse()
    assert model.color_by_tag is COLOR_BY_TAG_CASES[name]


def test_color_by_tag_roundtrips_classic_writer(tmp_path):
    quad = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)]
    for label, opts, expected in [
        ("default", None, False),
        ("off", DocumentOptions(style_items={7011: 0}), False),
        ("on", DocumentOptions(style_items={7011: 1}), True),
    ]:
        out = tmp_path / f"writer_{label}.skp"
        builder = create()
        if opts is not None:
            builder.set_document_options(opts)
        builder.add_face(quad)
        builder.save(str(out))
        assert SkpFile.open(out).parse().color_by_tag is expected


@pytest.mark.parametrize(
    ("on_name", "off_name"),
    [
        ("color_by_tag_2020.skp", "color_by_tag_2020_off.skp"),
        ("color_by_tag_2021.skp", "color_by_tag_2021_off.skp"),
    ],
)
def test_color_by_tag_does_not_change_scene_output(on_name, off_name):
    skp_on = SkpFile.open(FIXTURES / on_name)
    skp_off = SkpFile.open(FIXTURES / off_name)
    assert skp_on.parse().color_by_tag is True
    assert skp_off.parse().color_by_tag is False

    scene_on = skp_on.build_scene()
    scene_off = skp_off.build_scene()
    assert scene_on.gltf_materials == scene_off.gltf_materials

    inst_on = skp_on.build_instanced_scene()
    inst_off = skp_off.build_instanced_scene()
    assert inst_on.gltf_materials == inst_off.gltf_materials

