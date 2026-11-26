"""Tests for the ``openskp`` CLI (``info`` and ``convert`` subcommands, #570)."""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

import openskp
from openskp.cli import SUPPORTED_FORMATS
from openskp.cli import main as cli_main

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
CHAIR_AND_TABLE = REPO_ROOT / "examples" / "web-viewer" / "samples" / "chair_and_table.skp"
FIXTURES = pathlib.Path(__file__).parent / "fixtures"
SU_FILE_VFF = FIXTURES / "SU_File.skp"


@pytest.mark.parametrize("option", ["-h", "--help", "--version"])
def test_options_via_openskp_main(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    option: str,
) -> None:
    monkeypatch.setattr(sys, "argv", ["openskp", option])
    with pytest.raises(SystemExit) as exc_info:
        openskp.main()
    assert exc_info.value.code == 0
    out, err = capsys.readouterr()
    assert out.startswith("openskp " if option == "--version" else "usage: openskp")
    assert err == ""


def test_no_subcommand_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    rc = openskp.main([])
    assert rc == 0
    out, err = capsys.readouterr()
    assert out.startswith("usage: openskp")
    assert "info" in out
    assert "convert" in out
    assert err == ""


def test_info_chair_and_table(capsys: pytest.CaptureFixture[str]) -> None:
    rc = openskp.main(["info", str(CHAIR_AND_TABLE)])
    assert rc == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert "17.0.1" in out
    assert "Units:" in out and "unknown" in out
    assert "Definitions:" in out and "9" in out
    assert "Instances:" in out and "15" in out
    assert "Faces:" in out and "86" in out
    assert "Tags:" in out and "3" in out
    assert "Materials:" in out and "4" in out
    for tag in ("Layer0", "Chair", "Table"):
        assert tag in out
    for material in ("Walnut", "Oak", "Charcoal Linen", "Brass"):
        assert material in out


def test_info_vff_fixture_units_and_root_faces(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli_main(["info", str(SU_FILE_VFF)])
    assert rc == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert "25.0.575" in out
    assert "Millimeter" in out
    assert "Definitions:    0" in out
    assert "Instances:      0" in out
    assert "Faces:          32" in out
    assert "Tags:           1" in out
    assert "Materials:      0" in out
    assert "Layer0" in out
    assert "Material list:  (none)" in out


@pytest.mark.parametrize("ext", SUPPORTED_FORMATS)
def test_convert_all_supported_formats(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    ext: str,
) -> None:
    out_file = tmp_path / f"chair_and_table{ext}"
    rc = openskp.main(["convert", str(CHAIR_AND_TABLE), str(out_file)])
    assert rc == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert str(out_file) in out
    assert out_file.is_file()
    assert out_file.stat().st_size > 0

    if ext == ".json":
        payload = json.loads(out_file.read_text(encoding="utf-8"))
        assert payload["sketchup_version"] == "{17.0.1}"
        assert payload["total_definitions"] == 9
        assert payload["scene_hierarchy"] is not None
    elif ext == ".glb":
        assert out_file.read_bytes()[:4] == b"glTF"
    elif ext == ".obj":
        assert (tmp_path / "chair_and_table.mtl").is_file()


def test_convert_uppercase_extension(tmp_path: pathlib.Path) -> None:
    out_file = tmp_path / "model.STL"
    rc = cli_main(["convert", str(CHAIR_AND_TABLE), str(out_file)])
    assert rc == 0
    assert out_file.is_file()
    assert out_file.stat().st_size > 0


def test_info_missing_file_returns_error(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "does_not_exist.skp"
    rc = openskp.main(["info", str(missing)])
    assert rc == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.startswith("Error:")


def test_convert_missing_file_returns_error(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "does_not_exist.skp"
    out_file = tmp_path / "out.glb"
    rc = openskp.main(["convert", str(missing), str(out_file)])
    assert rc == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.startswith("Error:")
    assert not out_file.exists()


@pytest.mark.parametrize("filename", ["output.fbx", "output_no_ext"])
def test_convert_unsupported_extension_returns_error(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    filename: str,
) -> None:
    out_file = tmp_path / filename
    rc = openskp.main(["convert", str(CHAIR_AND_TABLE), str(out_file)])
    assert rc == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.startswith("Error:")
    for fmt in SUPPORTED_FORMATS:
        assert fmt in err
    assert not out_file.exists()


def test_info_corrupted_skp_returns_error(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    broken = tmp_path / "broken.skp"
    broken.write_bytes(b"not a valid sketchup file")
    rc = openskp.main(["info", str(broken)])
    assert rc == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.startswith("Error:")


def test_subprocess_version_and_help() -> None:
    for opt, prefix in (("--version", "openskp "), ("--help", "usage: openskp")):
        proc = subprocess.run(
            [sys.executable, "-m", "openskp.cli", opt],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0
        assert proc.stdout.startswith(prefix)
        assert proc.stderr == ""


def test_subprocess_info_and_convert(tmp_path: pathlib.Path) -> None:
    info_proc = subprocess.run(
        [sys.executable, "-m", "openskp.cli", "info", str(CHAIR_AND_TABLE)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert info_proc.returncode == 0
    assert "17.0.1" in info_proc.stdout
    assert "Walnut" in info_proc.stdout
    assert info_proc.stderr == ""

    out_json = tmp_path / "converted.json"
    conv_proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "openskp.cli",
            "convert",
            str(CHAIR_AND_TABLE),
            str(out_json),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert conv_proc.returncode == 0
    assert conv_proc.stderr == ""
    assert out_json.is_file()
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["total_definitions"] == 9
