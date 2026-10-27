"""The cross-revision checker must detect changes beyond entity counts."""

import importlib.util
import pathlib
import struct

import pytest

from openskp._core import parse_tlv_recursive
from openskp.model import Face, Instance

path = pathlib.Path(__file__).resolve().parents[3] / "examples/python/compare_parse.py"
spec = importlib.util.spec_from_file_location("compare_parse", path)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def test_equal_nan_bits_and_tlv_storage():
    nan = struct.unpack("!d", bytes.fromhex("7ff8000000000001"))[0]
    helper.compare([nan], [nan])
    data = b"\x01\x00\x02\x00\x00\x00ab"
    left = parse_tlv_recursive(data, 0, len(data))
    right = parse_tlv_recursive(data, 0, len(data))
    assert left[0].payload == b"ab"  # Only the left cache is populated.
    helper.compare(left, right)


@pytest.mark.parametrize(
    "left,right",
    [
        (0.0, -0.0),
        ([1], (1,)),
        ({1: 2, 3: 4}, {3: 4, 1: 2}),
        (Face(1, loops=[[(2, 1)]]), Face(1, loops=[[(2, -1)]])),
        (Instance(attribute_dictionaries={"a": {"b": 1}}),
         Instance(attribute_dictionaries={"a": {"b": 2}})),
    ],
)
def test_changed_field_is_rejected(left, right):
    with pytest.raises(AssertionError, match="model"):
        helper.compare(left, right)
