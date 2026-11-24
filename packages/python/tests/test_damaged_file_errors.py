"""Damaged containers and truncated files raise ``SkpParseError`` (#588).

Verifies that:
- Truncated/corrupted 2021+ VFF ZIP files and truncated classic MFC files raise
  :class:`openskp.SkpParseError` (with ``stage`` and ``__cause__`` attached when
  wrapping a lower-level library error) across ``parse()``, ``build_scene()``,
  and ``build_instanced_scene()``.
- Missing or unreadable files (``FileNotFoundError``, ``PermissionError``) stay
  what they are instead of being wrapped into ``SkpParseError``.
- Cutting a classic fixture containing layers at every single byte offset either
  returns or raises ``SkpParseError`` without hanging at EOF.
- Partial classic root reads (``unread_root_entities``) are preserved.
"""

from __future__ import annotations

import builtins
import io
import os
import pathlib
import sys
import zipfile

import pytest

from openskp import SkpFile, SkpParseError, _core, create, legacy

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("fraction", [0.1, 0.5])
def test_truncated_vff_zip_raises_parse_error(tmp_path: pathlib.Path, fraction: float) -> None:
    raw = (FIXTURES / "SU_File.skp").read_bytes()
    truncated = tmp_path / f"trunc_vff_{fraction}.skp"
    truncated.write_bytes(raw[: max(1, int(len(raw) * fraction))])

    skp = SkpFile.open(truncated)
    for fn in (skp.parse, skp.build_scene, skp.build_instanced_scene):
        with pytest.raises(SkpParseError) as exc_info:
            fn()
        assert exc_info.value.stage == "zip_extract"
        assert exc_info.value.__cause__ is not None


def test_vff_zip_missing_model_dat_raises_parse_error(tmp_path: pathlib.Path) -> None:
    raw = (FIXTURES / "SU_File.skp").read_bytes()
    pk = raw.find(b"PK\x03\x04")
    assert pk > 0

    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("meta/other.txt", b"placeholder")

    missing = tmp_path / "missing_model_dat.skp"
    missing.write_bytes(raw[:pk] + zbuf.getvalue())

    skp = SkpFile.open(missing)
    for fn in (skp.parse, skp.build_scene, skp.build_instanced_scene):
        with pytest.raises(SkpParseError) as exc_info:
            fn()
        assert exc_info.value.stage == "zip_extract"
        assert exc_info.value.__cause__ is not None


def test_corrupted_vff_deflate_stream_raises_parse_error(tmp_path: pathlib.Path) -> None:
    raw = bytearray((FIXTURES / "SU_File.skp").read_bytes())
    pk = bytes(raw).find(b"PK\x03\x04")
    assert pk > 0
    mid = pk + (len(raw) - pk) // 2
    for i in range(min(32, len(raw) - mid)):
        raw[mid + i] ^= 0xFF

    corrupted = tmp_path / "corrupt_deflate.skp"
    corrupted.write_bytes(raw)

    skp = SkpFile.open(corrupted)
    for fn in (skp.parse, skp.build_scene, skp.build_instanced_scene):
        with pytest.raises(SkpParseError) as exc_info:
            fn()
        assert exc_info.value.stage in ("zip_extract", "tlv_walk")
        assert exc_info.value.__cause__ is not None


@pytest.mark.parametrize("fraction", [0.1, 0.5])
def test_truncated_classic_file_raises_parse_error(tmp_path: pathlib.Path, fraction: float) -> None:
    raw = (FIXTURES / "capilla_quiroz_v17.skp").read_bytes()
    truncated = tmp_path / f"trunc_classic_{fraction}.skp"
    truncated.write_bytes(raw[: max(32, int(len(raw) * fraction))])

    skp = SkpFile.open(truncated)
    for fn in (skp.parse, skp.build_scene, skp.build_instanced_scene):
        with pytest.raises(SkpParseError) as exc_info:
            fn()
        assert exc_info.value.stage == "legacy_walk"
        assert exc_info.value.__cause__ is not None


def test_missing_and_unreadable_files_keep_os_exceptions(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "does_not_exist.skp"
    with pytest.raises(FileNotFoundError):
        SkpFile.open(missing)
    with pytest.raises(FileNotFoundError):
        _core.full_parse(str(missing))
    with pytest.raises(FileNotFoundError):
        legacy.full_parse_legacy(str(missing))

    # If a file disappears after SkpFile.open() validated its existence,
    # parse/build_scene/build_instanced_scene must still raise FileNotFoundError.
    vanishing = tmp_path / "vanishing.skp"
    vanishing.write_bytes((FIXTURES / "SU_File.skp").read_bytes())
    skp = SkpFile.open(vanishing)
    vanishing.unlink()
    for fn in (skp.parse, skp.build_scene, skp.build_instanced_scene):
        with pytest.raises(FileNotFoundError):
            fn()

    # Unreadable file raises PermissionError (simulated deterministically across
    # OSes via open() / ZipFile, and also tested via chmod on POSIX non-root).
    unreadable_vff = tmp_path / "unreadable_vff.skp"
    unreadable_vff.write_bytes((FIXTURES / "SU_File.skp").read_bytes())
    real_open = builtins.open

    def deny_target(file, *args, **kwargs):
        if os.fspath(file) == str(unreadable_vff):
            raise PermissionError(13, "Permission denied", str(unreadable_vff))
        return real_open(file, *args, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(builtins, "open", deny_target)
        skp_denied = SkpFile.open(unreadable_vff)
        for fn in (skp_denied.parse, skp_denied.build_scene, skp_denied.build_instanced_scene):
            with pytest.raises(PermissionError):
                fn()

    # Also verify PermissionError inside _read_vff_zip (zipfile.ZipFile) is not wrapped.
    def deny_zip(*args, **kwargs):
        raise PermissionError(13, "Permission denied", str(unreadable_vff))

    with monkeypatch.context() as m:
        m.setattr(_core.zipfile, "ZipFile", deny_zip)
        skp_zip_denied = SkpFile.open(unreadable_vff)
        for fn in (
            skp_zip_denied.parse,
            skp_zip_denied.build_scene,
            skp_zip_denied.build_instanced_scene,
        ):
            with pytest.raises(PermissionError):
                fn()

    if sys.platform != "win32" and hasattr(os, "geteuid") and os.geteuid() != 0:
        chmod_file = tmp_path / "chmod_denied.skp"
        chmod_file.write_bytes((FIXTURES / "SU_File.skp").read_bytes())
        chmod_file.chmod(0)
        try:
            skp_chmod = SkpFile.open(chmod_file)
            with pytest.raises(PermissionError):
                skp_chmod.parse()
        finally:
            chmod_file.chmod(0o600)


def test_classic_fixture_cut_at_every_offset_returns_or_raises_parse_error(
    tmp_path: pathlib.Path,
) -> None:
    raw = (FIXTURES / "legacy_v4_textured.skp").read_bytes()
    assert len(raw) == 4423
    probe = tmp_path / "cut_probe.skp"
    probe.write_bytes(raw)
    skp = SkpFile.open(probe)

    for cut in range(len(raw), -1, -1):
        os.truncate(probe, cut)
        try:
            skp.parse()
        except SkpParseError:
            pass


def test_partial_classic_root_read_is_not_hidden(tmp_path: pathlib.Path) -> None:
    builder = create()
    builder.add_face([(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)])
    builder.add_text("First", (0, 0, 0))
    builder.add_text("Broken", (5, 0, 0))
    raw = bytearray(builder.to_bytes())

    marker = b"\x01\xff\xfe\xff\x06B\x00"
    at = raw.find(marker)
    assert at >= 0
    raw[at : at + 4] = b"\x00\x00\x00\x00"

    partial_path = tmp_path / "partial_classic.skp"
    partial_path.write_bytes(raw)

    model = SkpFile.open(partial_path).parse()
    assert len(model.root.texts) == 1
    assert model.unread_root_entities > 0
