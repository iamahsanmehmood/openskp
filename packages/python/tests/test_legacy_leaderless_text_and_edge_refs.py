"""Leaderless classic CText records and non-vertex CEdge references (#592)."""

from __future__ import annotations

import logging
import pathlib
import tempfile

import pytest

from openskp import SkpFile, SkpParseError

FIXTURE = (
    pathlib.Path(__file__).parent
    / "fixtures"
    / "sdk_saved_leaderless_text_edges_2020.skp"
)


def _parse_bytes(data: bytes) -> tuple:
    with tempfile.NamedTemporaryFile(suffix=".skp", delete=False) as tmp:
        tmp.write(data)
        tmp_path = pathlib.Path(tmp.name)
    try:
        skp = SkpFile.open(str(tmp_path))
        return skp.parse(), skp.build_scene(), skp.build_instanced_scene()
    finally:
        tmp_path.unlink(missing_ok=True)


def test_leaderless_text_before_shared_vertex_edges_matches_sdk(caplog):
    skp = SkpFile.open(str(FIXTURE))
    with caplog.at_level(logging.WARNING, logger="openskp"):
        model = skp.parse()
        scene = skp.build_scene()
        instanced = skp.build_instanced_scene()

    assert len(model.definitions) == 1
    post = next(iter(model.definitions.values()))
    assert post.name == "Post"
    assert post.is_group is False
    assert len(model.root.faces) == 0
    assert len(model.root.edges) == 3
    assert len(model.root.instances) == 1
    assert [t.text for t in model.root.texts] == ["Screen title", "Callout"]
    assert model.unread_root_entities == 0
    assert len(scene.surface_primitives) == 1
    assert len(instanced.mesh_resources) == 1
    assert caplog.records == []


def test_root_edge_with_non_vertex_ref_reports_partial_read(caplog):
    raw = bytearray(FIXTURE.read_bytes())
    assert raw[5323:5325] == b"\x31\x00"  # 2nd root edge v1 -> CVertex slot 49
    raw[5323] = 0x2F  # CEdge slot 47

    with caplog.at_level(logging.WARNING, logger="openskp"):
        model, scene, instanced = _parse_bytes(bytes(raw))

    assert len(model.root.instances) == 1
    assert [t.text for t in model.root.texts] == ["Screen title", "Callout"]
    assert len(model.root.edges) == 1
    assert model.unread_root_entities == 2
    assert len(scene.surface_primitives) == 1
    assert len(instanced.mesh_resources) == 1
    assert any("2 unread" in r.getMessage() for r in caplog.records)


def test_definition_edge_with_non_vertex_ref_raises_parse_error():
    raw = bytearray(FIXTURE.read_bytes())
    assert raw[4039:4041] == b"\x14\x00"  # 2nd definition edge v1 -> CVertex slot 20
    raw[4039] = 0x11  # CEdge slot 17

    with tempfile.NamedTemporaryFile(suffix=".skp", delete=False) as tmp:
        tmp.write(bytes(raw))
        tmp_path = pathlib.Path(tmp.name)
    try:
        skp = SkpFile.open(str(tmp_path))
        with pytest.raises(SkpParseError):
            skp.parse()
        with pytest.raises(SkpParseError):
            skp.build_scene()
        with pytest.raises(SkpParseError):
            skp.build_instanced_scene()
    finally:
        tmp_path.unlink(missing_ok=True)
