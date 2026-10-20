"""The TLV parser's fast paths must behave exactly like the plain code they
replaced (openskp#431).

``full_parse`` on a large VFF model spends nearly all of its time building and
walking the TLV tree, so that path reads node fields as attributes, decodes the
record header with one ``struct`` call, and shares one string per tag. None of
that may change what comes out. Each test here pins one of those substitutions
against a direct restatement of the original behaviour, on random input.
"""
from __future__ import annotations

import random
import struct

import pytest

from openskp import _core

CONTAINERS = {"AAAA", "BBBB", "CCCC"}
LEAF_TAGS = ["D007", "DC05", "DE05", "C509", "B90B", "0702"]


def _reference_parse(data, start, end, container_tags):
    """The original ``parse_tlv_recursive``, as plain dicts."""
    pos = start
    out = []
    while pos <= end - 6:
        tag = "%02X%02X" % (data[pos], data[pos + 1])
        size = struct.unpack_from("<I", data, pos + 2)[0]
        if pos + 6 + size > end:
            break
        children = []
        if tag in container_tags and size > 0:
            children = _reference_parse(data, pos + 6, pos + 6 + size, container_tags)
        out.append({
            "offset": pos, "tag": tag, "size": size, "children": children,
            "payload": b"" if children else data[pos + 6:pos + 6 + size],
        })
        pos += 6 + size
    return out


def _as_dict(node):
    return {
        "offset": node.offset, "tag": node.tag, "size": node.size,
        "children": [_as_dict(c) for c in node.children],
        "payload": node.payload,
    }


def _record(tag_hex, payload):
    return bytes.fromhex(tag_hex) + struct.pack("<I", len(payload)) + payload


def _random_buffer(rng, depth=0):
    """Random run of TLV records; containers nest, some lengths lie."""
    parts = []
    for _ in range(rng.randint(0, 6)):
        if depth < 4 and rng.random() < 0.4:
            tag = rng.choice(sorted(CONTAINERS))
            body = _random_buffer(rng, depth + 1)
        else:
            tag = rng.choice(LEAF_TAGS + ["FFFF", "0000", "1234"])
            body = bytes(rng.randrange(256) for _ in range(rng.randint(0, 9)))
        rec = _record(tag, body)
        if rng.random() < 0.05:  # length claims more bytes than exist
            rec = rec[:2] + struct.pack("<I", len(body) + rng.randint(1, 50)) + rec[6:]
        parts.append(rec)
    buf = b"".join(parts)
    if rng.random() < 0.1:  # trailing junk shorter than a header
        buf += bytes(rng.randrange(256) for _ in range(rng.randint(1, 5)))
    return buf


def test_tag_table_matches_the_byte_pair_spelling():
    table = _core._tag_strings()
    assert len(table) == 0x10000
    for value in range(0x10000):
        assert table[value] == "%02X%02X" % (value & 0xFF, value >> 8)


@pytest.mark.parametrize("seed", range(40))
def test_parse_tlv_recursive_matches_the_original(seed):
    rng = random.Random(seed)
    data = _random_buffer(rng)
    got = [_as_dict(n) for n in _core.parse_tlv_recursive(data, 0, len(data), CONTAINERS)]
    assert got == _reference_parse(data, 0, len(data), CONTAINERS)


@pytest.mark.parametrize("seed", range(10))
def test_flat_headers_match_the_original(seed):
    rng = random.Random(1000 + seed)
    data = _random_buffer(rng)
    expected = []
    pos = 0
    while pos <= len(data) - 6:
        tag = "%02X%02X" % (data[pos], data[pos + 1])
        size = struct.unpack_from("<I", data, pos + 2)[0]
        if pos + 6 + size > len(data):
            break
        expected.append((tag, pos, size))
        pos += 6 + size
    assert _core._flat_headers(data, 0, len(data)) == expected


def _reference_var_int(data, offset, length):
    value = 0
    for i in range(length):
        value |= data[offset + i] << (8 * i)
    return value


def test_parse_var_int_matches_the_byte_loop_including_errors():
    rng = random.Random(7)
    for _ in range(20000):
        data = bytes(rng.randrange(256) for _ in range(rng.randint(0, 12)))
        if rng.random() < 0.3:
            data = bytearray(data)
        offset = rng.randint(-14, 14)
        length = rng.randint(-5, 16)
        try:
            want = ("ok", _reference_var_int(data, offset, length))
        except Exception as exc:  # noqa: BLE001 - comparing failure types
            want = ("err", type(exc))
        try:
            got = ("ok", _core.parse_var_int(data, offset, length))
        except Exception as exc:  # noqa: BLE001
            got = ("err", type(exc))
        assert got == want, (data, offset, length)


def _dict_tree(rng, counter, depth=0):
    nodes = []
    for _ in range(rng.randint(0, 4)):
        counter[0] += 1
        tag = rng.choice(["DE05", "DC05", "C509", "B90B", "XXXX", "6419", "D007"])
        kids = _dict_tree(rng, counter, depth + 1) if depth < 4 and rng.random() < 0.5 else []
        payload = b"" if kids else bytes(rng.randrange(256) for _ in range(rng.randint(0, 8)))
        if tag == "DC05" and not kids and rng.random() < 0.5:
            payload = b"\xde\x05" + struct.pack("<I", 2) + b"\x07\x01" + payload
        nodes.append({"offset": counter[0], "tag": tag, "size": len(payload),
                      "children": kids, "payload": payload})
    return nodes


def _ids(node):
    return None if node is None else node["offset"] if isinstance(node, dict) else node.offset


@pytest.mark.parametrize("seed", range(60))
def test_tree_helpers_agree_on_dict_and_node_trees(seed):
    rng = random.Random(seed)
    dict_nodes = _dict_tree(rng, [0])
    tlv_nodes = [_core._node_from_dict(n) for n in dict_nodes]

    for target in ("DE05", "DC05", "C509", "B90B", "XXXX", "NONE"):
        assert _ids(_core.find_child_tag(dict_nodes, target)) == \
            _ids(_core.find_child_tag(tlv_nodes, target))

        a, b = [], []
        _core.find_all_nodes_rec(dict_nodes, target, a)
        _core.find_all_nodes_rec(tlv_nodes, target, b)
        assert [_ids(n) for n in a] == [_ids(n) for n in b]

    for d, t in zip(dict_nodes, tlv_nodes):
        assert _core.extract_entity_id(d) == _core.extract_entity_id(t)
