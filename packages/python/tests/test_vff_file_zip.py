"""Read prefixed ZIPs from disk without a file-sized compressed copy."""

import io
import zipfile

import pytest

from openskp import _core


@pytest.mark.parametrize("absolute_offsets", [False, True])
@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_prefixed_zip(tmp_path, absolute_offsets, compression):
    header = b"\xff\xfe\xff\x0e" + b"\0" * 600
    buffer = io.BytesIO(header if absolute_offsets else b"")
    if absolute_offsets:
        buffer.seek(0, 2)
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        archive.writestr("model.dat", b"")
        archive.writestr("meta/model_thumbnail.png", b"test-thumbnail")
    data = buffer.getvalue()
    path = tmp_path / "prefixed.skp"
    path.write_bytes(data if absolute_offsets else header + data)

    parsed = _core.full_parse(str(path))
    assert parsed["thumbnail_data"] == b"test-thumbnail"
    assert list(parsed["defs_dict"]) == ["ROOT"]


def test_zip_is_file_backed_and_closed_on_error(tmp_path, monkeypatch):
    path = tmp_path / "missing-model.skp"
    path.write_bytes(b"\xff\xfe\xff\x0e")
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("unused", b"unused")

    opened = []
    real_zip_file = zipfile.ZipFile

    def recording_zip_file(source, *args, **kwargs):
        assert source == str(path)
        archive = real_zip_file(source, *args, **kwargs)
        opened.append(archive)
        return archive

    monkeypatch.setattr(_core.zipfile, "ZipFile", recording_zip_file)
    with pytest.raises(KeyError, match="model.dat"):
        _core.full_parse(str(path))
    assert len(opened) == 1
    assert opened[0].fp is None
