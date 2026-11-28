"""Tests for ``openskp.provenance_report`` and ``openskp info --provenance`` (#731)."""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
import zipfile

import pytest

import openskp
from openskp import (
    DerivedPathSummary,
    ProvenanceItem,
    ProvenanceReport,
    ProvenanceSource,
    SkpParseError,
    provenance_report,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
PROV_2021 = FIXTURES / "provenance_traces_2021.skp"
PROV_2017 = FIXTURES / "provenance_traces_2017.skp"

TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
    b"\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.mark.parametrize(
    ("fixture_path", "expected_format", "expected_version_prefix"),
    [
        (PROV_2021, "vff", "25."),
        (PROV_2017, "classic", "17.0."),
    ],
)
def test_sdk_provenance_fixtures_complete(
    fixture_path: pathlib.Path,
    expected_format: str,
    expected_version_prefix: str,
) -> None:
    report = provenance_report(fixture_path)
    assert isinstance(report, ProvenanceReport)
    assert report.format == expected_format
    assert report.sketchup_version.startswith(expected_version_prefix)
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        report.file_guid,
    )
    assert report.model_name == "Example Provenance Model"
    assert (
        report.model_description
        == "Contact someone@example.com or visit https://example.com/model-spec for details."
    )

    # Every stored item carries kind, value, non-empty source record/entity_type, and derived=False
    for item in report.items:
        assert isinstance(item, ProvenanceItem)
        assert item.derived is False
        assert isinstance(item.source, ProvenanceSource)
        assert item.source.record
        assert item.source.entity_type

    # Save and definition load times
    file_save_times = report.items_of_kind("save_time")
    assert len(file_save_times) >= 1
    assert all(isinstance(it.value, int) and it.value > 1_700_000_000 for it in file_save_times)
    def_load_times = report.items_of_kind("definition_load_time")
    assert {it.source.entity_name for it in def_load_times} == {
        "WindowUnit",
        "site_plan_image",
    }

    # Definition GUIDs (22-character SketchUp/IFC Base64 GUIDs)
    def_guids = {it.source.entity_name: it.value for it in report.definition_guids}
    assert set(def_guids) == {"WindowUnit", "site_plan_image"}
    for guid_val in def_guids.values():
        assert len(guid_val) == 22

    # Contributor record (GSU_ContributorsInfo)
    assert len(report.contributor_records) == 1
    contrib = report.contributor_records[0]
    assert contrib.source.entity_type == "model"
    assert "GSU_ContributorsInfo" in contrib.source.record
    assert contrib.value == {
        "LastModifiedByKey": "u-example-0001",
        "NicknamesKey": ["example-contributor"],
        "UserIdsKey": ["u-example-0001"],
        "VersionKey": 1000,
    }

    # Stored paths: texture images, component definition from disk, imported image
    tex_map = {it.source.entity_name: it.value for it in report.texture_paths}
    assert tex_map == {
        "BrickWall": r"C:\Users\example-user\Documents\Textures\brick.png",
        "StuccoWall": r"\\example-server\share\materials\stucco.png",
        "LegacyOak": r"D:\Documents and Settings\example-winxp\My Documents\oak.png",
    }
    def_paths = {it.source.entity_name: it.value for it in report.definition_paths}
    assert def_paths == {"WindowUnit": "components/ext_window.skp"}
    img_paths = [it.value for it in report.image_paths]
    assert img_paths == ["/Users/example-mac/Pictures/site_plan.png"]

    # Derived path summary (explicitly labeled derived=True)
    d = report.derived
    assert isinstance(d, DerivedPathSummary)
    assert d.user_folders == ("example-user", "example-winxp", "example-mac")
    assert d.distinct_user_folder_count == 3
    assert d.drive_letters == ("C:", "D:")
    assert d.distinct_drive_letter_count == 2
    assert d.network_shares == (r"\\example-server\share",)
    assert d.distinct_network_share_count == 1
    assert d.operating_systems == ("Windows", "macOS")
    assert d.distinct_operating_system_count == 2
    assert len(d.items) > 0
    assert all(it.derived is True for it in d.items)

    # Geo-location (ShadowInfo + GeoReference)
    geo_by_kind = {it.value["kind"]: it for it in report.geolocation}
    assert set(geo_by_kind) == {"ShadowInfo", "GeoReference"}
    sh = geo_by_kind["ShadowInfo"].value
    assert sh["city"] == "San Francisco"
    assert sh["country"] == "ExampleLand"
    assert sh["latitude"] == pytest.approx(37.7749)
    assert sh["longitude"] == pytest.approx(-122.4194)
    assert sh["is_georeferenced"] is True
    georef_entries = geo_by_kind["GeoReference"].value["entries"]
    assert georef_entries["Latitude"] == pytest.approx(37.7749)
    assert georef_entries["Longitude"] == pytest.approx(-122.4194)
    assert bool(georef_entries["UsesGeoReferencing"]) is True

    # 3D Warehouse dictionary
    assert len(report.warehouse_dictionaries) == 1
    wh = report.warehouse_dictionaries[0]
    assert wh.source.entity_type == "definition"
    assert wh.source.entity_name == "WindowUnit"
    assert wh.value == {
        "name": "3DWarehouse",
        "entry_count": 2,
        "entries": {
            "ModelID": "00000000-0000-0000-0000-000000000001",
            "SourceURL": "https://example.org/warehouse/window-unit",
        },
    }

    # Plugin dictionaries (excluding SketchUp's internal TempShadowInfo / SU_DefinitionSet / ModelProperties)
    plugs = {
        (it.source.entity_type, it.source.entity_name, it.value["name"]): it.value[
            "entry_count"
        ]
        for it in report.plugin_dictionaries
    }
    assert plugs == {
        ("model", "", "ExampleRenderPlugin"): 3,
        ("instance", "NorthWindow", "ExampleBimPlugin"): 2,
    }

    # Email addresses & web addresses inside stored strings (excluding XML schema URIs)
    assert sorted(it.value for it in report.email_addresses) == [
        "qa-team@example.org",
        "someone@example.com",
        "support@example.net",
    ]
    assert sorted(it.value for it in report.web_addresses) == [
        "https://example.com/model-spec",
        "https://example.org/warehouse/window-unit",
        "https://www.example.com/bim/w01",
    ]

    # Non-guessing human-readable phrasing
    text = report.format_text()
    assert "3 different user folders appear in stored paths:" in text
    assert "Derived from stored paths (derived):" in text
    for forbidden_phrase in ("created on", "written by", "authored by"):
        assert forbidden_phrase not in text.lower()


@pytest.mark.parametrize("fixture_path", [PROV_2021, PROV_2017])
def test_provenance_fixtures_contain_only_synthetic_traces(
    fixture_path: pathlib.Path,
) -> None:
    """Raw bytes (ASCII, UTF-16LE, UTF-16BE) and ZIP entries contain only synthetic example-* traces."""
    raw = fixture_path.read_bytes()
    blobs: list[tuple[str, bytes]] = [("raw", raw)]
    if b"PK\x03\x04" in raw[:4096]:
        with zipfile.ZipFile(fixture_path, "r") as zf:
            for name in zf.namelist():
                blobs.append((name, zf.read(name)))

    forbidden = ("devs", "admin", ".gemini", "prov_probe", "prov_fixture_stage", "lanes")
    for label, blob in blobs:
        low_ascii = blob.decode("latin-1").lower()
        low_u16le = blob.decode("utf-16-le", errors="ignore").lower()
        low_u16be = blob.decode("utf-16-be", errors="ignore").lower()
        for tok in forbidden:
            assert tok not in low_ascii, f"Forbidden token {tok!r} in {fixture_path.name}:{label}"
            assert tok not in low_u16le, f"Forbidden UTF-16LE token {tok!r} in {fixture_path.name}:{label}"
            assert tok not in low_u16be, f"Forbidden UTF-16BE token {tok!r} in {fixture_path.name}:{label}"


def test_writer_annotations_linux_paths_and_retina_filenames(
    tmp_path: pathlib.Path,
) -> None:
    """Text annotations carry emails/URLs, Linux /home/<user>/ is derived, and @2x.png is not an email."""
    vff_path = tmp_path / "synthetic_vff.skp"
    b_vff = openskp.create_vff()
    mat = b_vff.add_texture_material(
        "RetinaTex",
        TINY_PNG,
        applied_width=10.0,
        applied_height=10.0,
        filename="/home/example-linux/textures/tile@2x.png",
    )
    b_vff.add_face([(0, 0, 0), (10, 0, 0), (10, 10, 0)], material=mat)
    b_vff.add_text("Spec at www.example.org/docs or ops@example.org", (1, 2, 3), (4, 5, 6))
    b_vff.add_section_plane((0, 0, 0), (0, 0, 1), name="Cut info@example.com", symbol="S1")
    b_vff.set_model_attributes(
        attribute_dicts=[
            ("CustomTool", {"ref": "tile@2x.png", "site": "http://example.net/tool."})
        ]
    )
    b_vff.save(vff_path)

    rep = provenance_report(vff_path)
    assert rep.derived.user_folders == ("example-linux",)
    assert rep.derived.distinct_user_folder_count == 1
    assert "Linux" in rep.derived.operating_systems
    assert any(
        it.kind == "derived_operating_system"
        and it.value == "Linux"
        and it.source.entity_name == "RetinaTex"
        for it in rep.derived.items
    )
    assert "1 user folder appears in stored paths:" in rep.format_text()
    # tile@2x.png is an image filename, never misreported as an email address
    assert sorted(it.value for it in rep.email_addresses) == [
        "info@example.com",
        "ops@example.org",
    ]
    assert sorted(it.value for it in rep.web_addresses) == [
        "http://example.net/tool",
        "www.example.org/docs",
    ]

    # Classic blank writer has no template style path, so only Linux is implied
    png_file = tmp_path / "tile@2x.png"
    png_file.write_bytes(TINY_PNG)
    classic_path = tmp_path / "synthetic_classic.skp"
    b_cls = openskp.create()
    mat_c = b_cls.add_texture_material(
        "RetinaTexClassic",
        str(png_file),
        applied_width=10.0,
        applied_height=10.0,
        filename="/home/example-linux/textures/tile@2x.png",
    )
    b_cls.add_face([(0, 0, 0), (10, 0, 0), (10, 10, 0)], material=mat_c)
    b_cls.save(str(classic_path))
    rep_c = provenance_report(classic_path)
    assert rep_c.derived.user_folders == ("example-linux",)
    assert rep_c.derived.operating_systems == ("Linux",)
    assert rep_c.derived.distinct_operating_system_count == 1
    assert len(rep_c.email_addresses) == 0


def test_vff_multi_save_history_and_classic_real_fixture() -> None:
    vff_multi = provenance_report(FIXTURES / "vff_face_text_anchors_2025.skp")
    assert [it.value for it in vff_multi.save_clients] == ["Pre SU 2021", "SketchUp"]
    assert len(vff_multi.items_of_kind("save_time")) == 2

    gondola = provenance_report(FIXTURES / "gondola_v20.skp")
    assert gondola.format == "classic"
    assert gondola.sketchup_version == "20.1.235"
    assert gondola.derived.drive_letters == ("B:", "C:")
    assert len(gondola.warehouse_dictionaries) == 1
    assert len(gondola.contributor_records) == 3


def test_cli_info_provenance_text_and_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc_txt = openskp.main(["info", "--provenance", str(PROV_2021)])
    assert rc_txt == 0
    out_txt, err_txt = capsys.readouterr()
    assert err_txt == ""
    assert "3 different user folders appear in stored paths:" in out_txt
    assert "Example Provenance Model" in out_txt
    assert "GSU_ContributorsInfo" in out_txt

    rc_json = openskp.main(["info", "--provenance", "--json", str(PROV_2017)])
    assert rc_json == 0
    out_json, err_json = capsys.readouterr()
    assert err_json == ""
    payload = json.loads(out_json)
    assert payload == provenance_report(PROV_2017).to_dict()
    assert payload["derived"]["derived"] is True
    assert payload["derived"]["distinct_user_folder_count"] == 3


def test_cli_provenance_subprocess_and_errors(tmp_path: pathlib.Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "openskp.cli", "info", "--provenance", "--json", str(PROV_2021)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert proc.stderr == ""
    parsed = json.loads(proc.stdout)
    assert parsed["format"] == "vff"
    assert parsed["derived"]["user_folders"] == [
        "example-user",
        "example-winxp",
        "example-mac",
    ]

    bad = tmp_path / "invalid.skp"
    bad.write_bytes(b"not a sketchup file")
    with pytest.raises(SkpParseError):
        provenance_report(bad)

    bad_proc = subprocess.run(
        [sys.executable, "-m", "openskp.cli", "info", "--provenance", str(bad)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad_proc.returncode == 1
    assert bad_proc.stderr.startswith("Error:")


def test_provenance_runs_on_every_python_fixture() -> None:
    skp_fixtures = sorted(FIXTURES.rglob("*.skp"))
    assert len(skp_fixtures) >= 85
    for skp_file in skp_fixtures:
        rep = provenance_report(skp_file)
        assert rep.format in ("vff", "classic")
        assert rep.sketchup_version
