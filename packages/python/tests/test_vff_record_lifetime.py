"""Streaming must release the previous tree before decoding its successor."""

import struct
import weakref
import zipfile

from openskp import _core


def test_full_parse_does_not_overlap_discarded_top_level_trees(monkeypatch, tmp_path):
    class TrackedNode(_core._TlvNode):
        __slots__ = ("__weakref__",)

    references = []
    original = _core._parse_tlv_geometry

    def tracked_parser(data, start, end, container_tags):
        assert all(ref() is None for ref in references)
        # Empty root records keep this test independent of retained instances,
        # which deliberately retain their subtrees for raw export.
        node = original(data, start, end, container_tags)[0]
        tracked = TrackedNode(
            node.offset,
            node.tag,
            node.size,
            node.children,
            data,
            node.offset + 6,
            node.offset + 6 + node.size,
        )
        references.append(weakref.ref(tracked))
        return [tracked]

    monkeypatch.setattr(_core, "_parse_tlv_geometry", tracked_parser)
    root = bytes.fromhex("F601") + struct.pack("<I", 0)
    path = tmp_path / "two-roots.skp"
    path.write_bytes(b"\xff\xfe\xff\x0e")
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("model.dat", root * 2)
    parsed = _core.full_parse(str(path))
    assert len(references) == 2
    assert all(ref() is None for ref in references)
    assert parsed["defs_dict"]["ROOT"]["builder"].faces == {}
