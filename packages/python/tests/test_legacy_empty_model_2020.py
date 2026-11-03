"""A SketchUp 2020 model with nothing in it (no entities, no component definitions) opens.

Fixture ``legacy_empty_2020.skp``: an empty model saved as a SketchUp 2020 file (``SUModelVersion`` 13)
by SketchUp's own library, the blank-template case. SketchUp 2020 writes an empty string record and
zero padding where the definition count would be; with nothing in the model the only count behind it is
the root entity count, which is 0, and the filler search skipped it, so the whole file was rejected with
"implausible definition count". No private content.
"""
from __future__ import annotations

import pathlib

from openskp import SkpFile
from openskp.legacy import _v20_filler_before_empty_root

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "legacy_empty_2020.skp"


def test_an_empty_2020_model_parses_to_an_empty_model():
    model = SkpFile.open(str(FIXTURE)).parse()
    assert [layer.name for layer in model.layers] == ["Layer0"]
    assert len(model.definitions) == 0
    assert len(model.materials) == 0
    assert len(model.root.faces) == 0
    assert len(model.root.instances) == 0


def _filler(zeros: int, tail: bytes = b"\x58\xe7\x37\x6a") -> bytes:
    """The empty string record (ff fe ff 00), *zeros* zero bytes, then a non-zero record."""
    return b"\xff\xfe\xff\x00" + bytes(zeros) + tail


class TestFillerBeforeAnEmptyRoot:
    def test_returns_the_root_count_offset(self):
        # 3 bytes of the previous record, then the filler: the root count sits 13 bytes of padding
        # behind the string's length byte (17 bytes behind the marker, as in the real 2020 files)
        data = b"\x00\x00\x00" + _filler(26)
        assert _v20_filler_before_empty_root(data, 0) == 3 + 4 + 13

    def test_a_non_empty_string_is_real_data(self):
        data = b"\x00\x00\x00" + b"\xff\xfe\xff\x05" + bytes(30)
        assert _v20_filler_before_empty_root(data, 0) is None

    def test_a_non_zero_byte_in_the_padding_or_count_is_not_this_layout(self):
        for at in range(4, 4 + 13 + 4):          # every byte of padding and root count
            data = bytearray(b"\x00\x00\x00" + _filler(26))
            data[3 + at] = 1
            assert _v20_filler_before_empty_root(bytes(data), 0) is None, at

    def test_no_filler_nearby(self):
        assert _v20_filler_before_empty_root(bytes(64), 0) is None

    def test_truncated_after_the_filler(self):
        assert _v20_filler_before_empty_root(b"\x00\x00\x00" + _filler(5, tail=b""), 0) is None

