"""Keep instance metadata without retaining the decompressed model buffer."""

import struct
import tracemalloc

from openskp import _core


def tlv(tag, payload):
    return struct.pack("<HI", tag, len(payload)) + payload


def test_instance_children_release_source_buffer():
    opaque = tlv(0x1234, b"opaque")
    # 6419 instance -> D007 drawing attributes -> unknown leaf.
    data = tlv(0x1964, tlv(0x07D0, opaque))
    nodes = _core.parse_tlv_recursive(data, 0, len(data))
    builder = _core._GeometryBuilder()
    _core._extract_geometry_nodes(nodes, builder)
    children = builder.instances[0]["children"]
    assert children is nodes[0].children
    leaf = children[0].children[0]
    assert leaf._data is None
    assert leaf.payload == b"opaque"
    assert leaf.tag == "3412"
    assert leaf.offset == 12
    assert leaf.size == 6
    assert leaf.children == []


def test_empty_container_and_repeated_detach_preserve_fields():
    data = tlv(0x07D0, b"not a TLV") + tlv(0x1234, b"")
    nodes = _core.parse_tlv_recursive(data, 0, len(data))
    _core._detach_tlv_payloads(nodes)
    _core._detach_tlv_payloads(nodes)
    assert [node.payload for node in nodes] == [b"not a TLV", b""]
    assert all(node._data is None for node in nodes)


def test_false_dimension_marker_does_not_copy_advertised_payload():
    data = tlv(0x5BCC, b"\0" * (8 * 1024 * 1024))
    tracemalloc.start()
    try:
        assert _core._parse_dimensions(data, {}, {}) == []
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 64 * 1024
