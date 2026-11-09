"""Hostile-input limits: deep nesting, deep meta wrappers and truncated files.

A file whose model.dat nests TLV containers tens of thousands of levels deep
used to exhaust the stack (or raise RecursionError); a truncated model.dat
used to parse as a model with no geometry and no error. Both now raise
SkpParseError, and a meta.dat wrapped thousands of times no longer recurses.
"""

import io
import pathlib
import struct
import zipfile

import pytest

from openskp import _core
from openskp.errors import SkpParseError
from openskp.parser import MAX_TLV_DEPTH, parse_tlv_recursive

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
HEADER = b"\xff\xfe\xff\x0e" + b"\0" * 600


def record(tag: bytes, payload: bytes) -> bytes:
    return tag + struct.pack("<I", len(payload)) + payload


def nested(tag: bytes, levels: int, leaf: bytes = b"") -> bytes:
    """`levels` records of `tag`, each inside the previous, around `leaf`.

    Built in one pass: wrapping level by level would copy the payload each
    time and take quadratic time for the 100,000-level cases.
    """
    parts = [tag + struct.pack("<I", len(leaf) + 6 * (levels - 1 - i)) for i in range(levels)]
    return b"".join(parts) + leaf


def write_skp(path, model_dat: bytes, meta_dat: bytes = None) -> str:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("model.dat", model_dat)
        if meta_dat is not None:
            archive.writestr("meta/meta.dat", meta_dat)
    path.write_bytes(HEADER + buffer.getvalue())
    return str(path)


def model_dat_of(fixture: str) -> bytes:
    with zipfile.ZipFile(FIXTURES / fixture) as archive:
        return archive.read("model.dat")


class TestNestingDepth:
    def test_public_parser_follows_real_depths(self):
        data = nested(b"\xf4\x01", MAX_TLV_DEPTH)
        assert parse_tlv_recursive(data, 0, len(data))

    @pytest.mark.parametrize("levels", [MAX_TLV_DEPTH + 2, 5000, 200000])
    def test_public_parser_rejects_deep_nesting(self, levels):
        data = nested(b"\xf4\x01", levels)
        with pytest.raises(SkpParseError, match="nest deeper") as caught:
            parse_tlv_recursive(data, 0, len(data))
        assert caught.value.stage == "tlv_walk"
        assert caught.value.tag == "F401"

    @pytest.mark.parametrize("levels", [MAX_TLV_DEPTH + 2, 5000, 200000])
    def test_full_parse_rejects_deep_nesting(self, tmp_path, levels):
        # The single top-level F401 is unwrapped; its child is the chain.
        model = record(b"\xf4\x01", nested(b"\xf4\x01", levels))
        with pytest.raises(SkpParseError, match="nest deeper"):
            _core.full_parse(write_skp(tmp_path / "deep.skp", model))

    def test_full_parse_accepts_moderate_nesting(self, tmp_path):
        model = record(b"\xf4\x01", nested(b"\xf4\x01", 40))
        parsed = _core.full_parse(write_skp(tmp_path / "ok.skp", model))
        assert list(parsed["defs_dict"]) == ["ROOT"]


class TestMetaWrappers:
    def wrapped(self, units=b"Inches"):
        inner = record(b"\x6d\x00", units)
        for _ in range(3):
            inner = record(b"\x64\x00", inner)
        return inner

    def test_follows_a_few_wrappers(self):
        assert _core._read_meta_units(self.wrapped()) == "Inches"

    @pytest.mark.parametrize("levels", [_core._MAX_TLV_DEPTH + 5, 100000])
    def test_deep_wrappers_give_unknown_units_without_recursing(self, levels):
        meta = nested(b"\x64\x00", levels, record(b"\x6d\x00", b"Inches"))
        assert _core._read_meta_units(meta) is None

    def test_deep_wrappers_do_not_fail_the_parse(self, tmp_path):
        meta = nested(b"\x64\x00", 100000, record(b"\x6d\x00", b"Inches"))
        model = record(b"\xf4\x01", b"")
        parsed = _core.full_parse(write_skp(tmp_path / "meta.skp", model, meta))
        assert parsed["units"] is None


class TestTruncation:
    @pytest.mark.parametrize("fixture", ["SU_File.skp", "Untitled.skp"])
    def test_real_files_end_exactly_on_their_top_level_record(self, tmp_path, fixture):
        model = model_dat_of(fixture)
        assert 6 + struct.unpack_from("<I", model, 2)[0] == len(model)
        assert _core.full_parse(str(FIXTURES / fixture))

    @pytest.mark.parametrize("keep", [0.5, 0.99])
    def test_cut_short_model_dat_is_an_error(self, tmp_path, keep):
        model = model_dat_of("SU_File.skp")
        cut = model[: int(len(model) * keep)]
        with pytest.raises(SkpParseError, match="truncated") as caught:
            _core.full_parse(write_skp(tmp_path / "cut.skp", cut))
        assert caught.value.stage == "tlv_walk"
        assert caught.value.tag == "F401"
        assert caught.value.offset == 0

    def test_trailing_bytes_shorter_than_a_header_are_ignored(self, tmp_path):
        model = model_dat_of("SU_File.skp") + b"\0\0\0"
        assert _core.full_parse(write_skp(tmp_path / "pad.skp", model))

    def test_empty_model_dat_still_parses(self, tmp_path):
        parsed = _core.full_parse(write_skp(tmp_path / "empty.skp", b""))
        assert list(parsed["defs_dict"]) == ["ROOT"]
