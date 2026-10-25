"""Exact-layout geometry decoding agrees with the general TLV implementation."""

import random
import struct

import pytest

from openskp import _core


def tlv(tag, payload):
    return bytes.fromhex(tag) + struct.pack("<I", len(payload)) + payload


def identity(entity_id, width=4):
    return tlv("DC05", tlv("DE05", entity_id.to_bytes(width, "little")))


def drawbase(entity_id, flags=6, extra=b"", width=4):
    return tlv("D007", identity(entity_id, width) + tlv("D307", bytes([flags])) + extra)


def records(width=4):
    vertex = tlv(
        "C409", identity(17, width) + tlv("C509", struct.pack("<3d", -0.0, 2, 3))
    )
    edge = tlv(
        "B80B",
        drawbase(19, 31, width=width)
        + tlv("B90B", (17).to_bytes(width, "little"))
        + tlv("BA0B", (18).to_bytes(width, "little")),
    )
    coedges = b"".join(
        tlv(
            "A00F",
            tlv("A10F", i.to_bytes(width, "little")) + tlv("A20F", bytes([sense])),
        )
        for i, sense in ((19, 0), (20, 1), (21, 255))
    )
    face = tlv(
        "AC0D",
        drawbase(23, 7, width=width)
        + tlv("AD0D", struct.pack("<4d", 0, 0, 1, 9))
        + tlv("AE0D", tlv("9411", tlv("9511", coedges))),
    )
    return vertex, edge, face


def extract(data, fast):
    parser = _core._parse_tlv_geometry if fast else _core.parse_tlv_recursive
    nodes = parser(data, 0, len(data), _core.CONTAINER_TAGS)
    builder = _core._GeometryBuilder()
    _core._extract_geometry_nodes(nodes, builder)
    return nodes, builder


@pytest.mark.parametrize("width", range(1, 9))
def test_common_records_use_fast_nodes_and_preserve_values(width):
    data = b"".join(records(width))
    nodes, fast = extract(data, True)
    _, slow = extract(data, False)
    assert all(isinstance(n, _core._GeometryRecordNode) for n in nodes)
    assert vars(fast) == vars(slow)
    assert struct.pack("<d", fast.vertices[17][0]) == struct.pack("<d", -0.0)
    assert fast.edge_flags == {19: 31}
    assert fast.faces[23]["loops"] == [[(19, 1), (20, -1), (21, -1)]]
    assert fast.faces[23]["hidden"] is True


def test_generic_searches_and_repeated_extraction_remain_independent():
    data = tlv("7C15", b"".join(records()))  # No definition id of its own.
    fast, first = extract(data, True)
    slow, _ = extract(data, False)
    assert _core._entity_id(fast[0]) == _core._entity_id(slow[0]) == 17
    assert _core._find_tag(fast, "B90B").payload == struct.pack("<I", 17)
    second = _core._GeometryBuilder()
    _core._extract_geometry_nodes(fast, second)
    first.faces[23]["loops"][0].clear()
    assert len(second.faces[23]["loops"][0]) == 3


@pytest.mark.parametrize("width", range(1, 9))
def test_edge_integer_widths_are_independent_and_unsigned(width):
    entity_id = (1 << (8 * width)) - 1
    data = tlv(
        "B80B",
        drawbase(entity_id, width=width)
        + tlv("B90B", b"\xff")
        + tlv("BA0B", b"\xff" * 8),
    )
    nodes, fast = extract(data, True)
    _, slow = extract(data, False)
    assert isinstance(nodes[0], _core._GeometryRecordNode)
    assert vars(fast) == vars(slow)
    assert fast.edges[entity_id] == (255, (1 << 64) - 1)


@pytest.mark.parametrize("normal_size", [24, 32])
def test_mixed_coedge_widths_fall_back(normal_size):
    coedges = b"".join(
        tlv("A00F", tlv("A10F", (19).to_bytes(width, "little")) + tlv("A20F", b"\0"))
        for width in (1, 4, 8)
    )
    data = tlv(
        "AC0D",
        drawbase(23)
        + tlv("AD0D", struct.pack("<4d", 0, 0, 1, 9)[:normal_size])
        + tlv("AE0D", tlv("9411", tlv("9511", coedges))),
    )
    nodes, fast = extract(data, True)
    _, slow = extract(data, False)
    assert type(nodes[0]) is _core._TlvNode
    assert vars(fast) == vars(slow)


def test_retained_instance_subtrees_never_contain_fast_nodes():
    data = tlv("6419", b"".join(records()))
    nodes, builder = extract(data, True)
    assert builder.instances[0]["children"] is nodes[0].children
    assert all(type(n) is _core._TlvNode for n in nodes[0].children)
    assert all(type(n) is _core._TlvNode for n in builder.instances[0]["children"])


@pytest.mark.parametrize("tag", ["D107", "D207", "AF0D", "0702"])
def test_additional_fields_take_the_general_path(tag):
    _, edge, face = records()
    data = edge if tag == "BB0B" else face
    payload = data[6:] + tlv(tag, b"\x01")
    data = tlv(data[:2].hex(), payload)
    nodes, fast = extract(data, True)
    _, slow = extract(data, False)
    assert type(nodes[0]) is _core._TlvNode
    assert vars(fast) == vars(slow)


def painted_records(width, front=True, back=True, curve=True):
    _, edge, face = records()
    base_size = struct.unpack_from("<I", edge, 8)[0]
    base = edge[12 : 12 + base_size]
    extra = tlv("D107", ((1 << (8 * width)) - 1).to_bytes(width, "little"))
    # The common order is identity, material, flags. Layers/UVs stay generic.
    base = tlv("D007", base[:-7] + (extra if front else b"") + base[-7:])
    edge_body = base + edge[12 + base_size :]
    if curve:
        edge_body += tlv("BB0B", (73).to_bytes(width, "little"))
    face_base = face[12 : 12 + base_size]
    face_body = tlv("D007", face_base[:-7] + (extra if front else b"") + face_base[-7:])
    if back:
        face_body += tlv("AF0D", (0).to_bytes(width, "little"))
    face_body += face[12 + base_size :]
    return tlv("B80B", edge_body), tlv("AC0D", face_body)


@pytest.mark.parametrize("width", range(1, 9))
@pytest.mark.parametrize(
    "front,back,curve", [(True, True, True), (False, True, False), (True, False, False)]
)
def test_painted_faces_and_curve_edges_match_generic(width, front, back, curve):
    data = b"".join(painted_records(width, front, back, curve))
    nodes, fast = extract(data, True)
    _, slow = extract(data, False)
    assert all(isinstance(n, _core._GeometryRecordNode) for n in nodes)
    assert vars(fast) == vars(slow)
    assert fast.faces[23]["material_id"] == ((1 << (8 * width)) - 1 if front else None)
    assert fast.faces[23]["back_material_id"] == (0 if back else None)
    assert fast.edge_curves == ({19: 73} if curve else {})


def test_zero_curve_id_is_absent_and_unknown_extensions_fall_back():
    _, edge, _ = records()
    zero_curve = tlv("B80B", edge[6:] + tlv("BB0B", b"\0"))
    nodes, fast = extract(zero_curve, True)
    assert isinstance(nodes[0], _core._GeometryRecordNode)
    assert fast.edge_curves == {}
    for record in painted_records(4):
        data = tlv(record[:2].hex(), record[6:] + tlv("0702", b"\1"))
        nodes, fast = extract(data, True)
        _, slow = extract(data, False)
        assert type(nodes[0]) is _core._TlvNode
        assert vars(fast) == vars(slow)


def test_mutated_records_match_general_decoder_results_or_errors():
    rng = random.Random(20261025)
    samples = tuple(
        record
        for width in range(1, 9)
        for record in (*records(width), *painted_records(width))
    )
    for _ in range(2000):
        data = bytearray(rng.choice(samples))
        if rng.randrange(3) == 0:
            del data[rng.randrange(len(data)) :]
        else:
            data[rng.randrange(len(data))] ^= 1 << rng.randrange(8)
        outcomes = []
        for fast in (False, True):
            try:
                _, builder = extract(bytes(data), fast)
                outcomes.append(("ok", vars(builder)))
            except Exception as exc:  # noqa: BLE001 - compare malformed-input failures
                outcomes.append((type(exc), str(exc)))
        assert outcomes[0] == outcomes[1]
