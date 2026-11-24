"""Tests for native component and root scale-grip suppression masks (#519)."""

from __future__ import annotations

from pathlib import Path
import tempfile

from openskp import DocumentOptions, SkpFile, create, open_existing, to_python_code

CPP_FIXTURES = Path(__file__).resolve().parents[3] / "packages" / "cpp" / "tests" / "fixtures"


def _parse_builder(builder):
    with tempfile.NamedTemporaryFile(suffix=".skp", delete=False) as f:
        tmp_path = Path(f.name)
    try:
        builder.save(str(tmp_path))
        return SkpFile.open(str(tmp_path)).parse()
    finally:
        tmp_path.unlink(missing_ok=True)


def test_native_scale_mask_round_trip() -> None:
    for mask in (0, 1, 2, 4, 8, 16, 32, 64, 127, 0x12345678):
        builder = create()
        builder.set_document_options(DocumentOptions(no_scale_mask=mask))
        with builder.add_component_definition(
            "MaskProbe",
            always_faces_camera=True,
            shadows_face_sun=True,
            no_scale_mask=mask,
        ) as defn:
            defn.add_face([(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 10.0, 0.0)])
        builder.add_instance(defn)
        model = _parse_builder(builder)
        assert len(model.definitions) == 1
        actual = next(iter(model.definitions.values()))
        assert actual.no_scale_mask == mask
        assert model.root.no_scale_mask == mask
        assert actual.always_faces_camera is True
        assert actual.shadows_face_sun is True
        assert actual.is_group is False


def test_reads_sdk_scale_mask_fixtures() -> None:
    for name in ("behavior_mask_2017.skp", "behavior_mask_2021.skp"):
        model = SkpFile.open(str(CPP_FIXTURES / name)).parse()
        assert len(model.definitions) == 1
        actual = next(iter(model.definitions.values()))
        assert actual.no_scale_mask == 127


def test_reads_sdk_root_scale_mask_fixtures() -> None:
    for name in (
        "root_behavior_mask_2013.skp",
        "root_behavior_mask_2017.skp",
        "root_behavior_mask_2021.skp",
    ):
        model = SkpFile.open(str(CPP_FIXTURES / name)).parse()
        assert model.root.no_scale_mask == (127 if "2021" in name else 2)
        assert len(model.definitions) == 1
        actual = next(iter(model.definitions.values()))
        assert actual.no_scale_mask == 0


def test_app_saved_files_have_no_scale_mask() -> None:
    for name in (
        "capilla_quiroz_v17.skp",
        "gondola_v20.skp",
        "zero_material_custom_layer_v18.skp",
    ):
        model = SkpFile.open(str(CPP_FIXTURES / name)).parse()
        assert model.root.no_scale_mask == 0, name
        for defn in model.definitions.values():
            assert defn.no_scale_mask == 0, name


def test_scale_mask_survives_edit_and_code_generation() -> None:
    builder = create()
    builder.set_document_options(DocumentOptions(no_scale_mask=16))
    with builder.add_component_definition("Masked", no_scale_mask=64) as defn:
        defn.add_face([(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 10.0, 0.0)])
    builder.add_instance(defn)

    with tempfile.NamedTemporaryFile(suffix=".skp", delete=False) as f:
        tmp_path = Path(f.name)
    try:
        builder.save(str(tmp_path))
        edited_builder, _, _ = open_existing(str(tmp_path))
    finally:
        tmp_path.unlink(missing_ok=True)

    model = _parse_builder(edited_builder)
    assert len(model.definitions) == 1
    actual = next(iter(model.definitions.values()))
    assert actual.no_scale_mask == 64
    assert model.root.no_scale_mask == 16

    code = to_python_code(model)
    assert "no_scale_mask=64" in code
    assert "no_scale_mask=16" in code
