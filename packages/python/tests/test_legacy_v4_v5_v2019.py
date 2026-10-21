"""Regression tests for SketchUp 4, 5 and 2019 files (openskp#284's remaining
signatures: "texture object is not a dib" on V4/V5, "implausible def entity
count" on 2019) and the silent failures found alongside them.

Fixtures - all small synthetic models, saved down through the installed
SketchUp library from the same sources as the existing legacy fixtures
(legacy_v6_synthetic.skp and the one-face textured model behind
legacy_v3_textured.skp). No private content; the only strings in them are
the model's own names and a neutral path:

* legacy_v4_synthetic.skp, legacy_v5_synthetic.skp, legacy_v2019_synthetic.skp
  - 4 definitions (InnerFrame, Frame, Panel, Post), 10 root instances, a
  "Roof" layer, 24 materials - at SketchUp 4, 5 and 2019.
* legacy_v4_textured.skp, legacy_v5_textured.skp - one painted face with an
  8x8 PNG texture, at SketchUp 4 and 5 (a bare inline image there, not a CDib
  object).
* legacy_v2020_textured.skp - the same model at SketchUp 2020: a model with NO
  component definitions, whose root entities used to be read as "definitions"
  and silently dropped.

Parsing without an exception is not enough (an early V3 attempt "succeeded"
with one definition of four), so every test asserts the parsed structure and
cross-checks it against the V6 file built from the same source.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from openskp import SkpFile, legacy
from openskp.legacy import _R, _Archive, _READERS, _read_dimlinear

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
V6 = FIXTURES / "legacy_v6_synthetic.skp"
V3_TEXTURED = FIXTURES / "legacy_v3_textured.skp"


def _parse(path):
    return SkpFile.open(str(path)).parse()


def _round(values, n=6):
    return tuple(round(float(v), n) for v in values)


def _canonical(model, *, instance_names=True):
    """Everything that matters about a model, keyed by names and coordinates
    (ids differ between versions)."""
    by_id = {m.id: m.name for m in model.materials}
    layers = {layer.name for layer in model.layers}
    defs = {}
    for d in model.definitions.values():
        faces = sorted(
            (by_id.get(f.material_id), by_id.get(f.back_material_id), bool(f.hidden))
            for f in d.faces.values())
        insts = sorted(
            (model.definitions[i.ref_idx].name, _round(i.matrix, 5),
             i.name if instance_names else "")
            for i in d.instances)
        defs[d.name] = {
            "vertices": sorted(_round((v.x, v.y, v.z)) for v in d.vertices.values()),
            "edges": len(d.edges),
            "faces": faces,
            "instances": insts,
        }
    root = sorted(
        (model.definitions[i.ref_idx].name, _round(i.matrix, 5),
         i.name if instance_names else "")
        for i in model.root.instances)
    return {"layers": layers, "materials": sorted(by_id.values()),
            "defs": defs, "root": root}


class TestSynthetic:
    @pytest.mark.parametrize("name,version", [
        ("legacy_v4_synthetic.skp", "{4.0.1}"),
        ("legacy_v5_synthetic.skp", "{5.0.1}"),
        ("legacy_v2019_synthetic.skp", "{19.0.1}"),
    ])
    def test_parses_and_reports_its_version(self, name, version):
        path = FIXTURES / name
        assert legacy.is_legacy(path.read_bytes())
        assert _parse(path).version == version

    @pytest.mark.parametrize("name,names", [
        ("legacy_v4_synthetic.skp", False),   # SketchUp 4 stores no instance names
        ("legacy_v5_synthetic.skp", True),
        ("legacy_v2019_synthetic.skp", True),
    ])
    def test_matches_the_v6_file_from_the_same_source(self, name, names):
        got = _canonical(_parse(FIXTURES / name), instance_names=names)
        want = _canonical(_parse(V6), instance_names=names)
        assert got == want

    @pytest.mark.parametrize("name", [
        "legacy_v4_synthetic.skp", "legacy_v5_synthetic.skp",
        "legacy_v2019_synthetic.skp"])
    def test_structure_is_complete_not_just_error_free(self, name):
        model = _parse(FIXTURES / name)
        defs = {d.name: d for d in model.definitions.values()}
        assert set(defs) == {"InnerFrame", "Frame", "Panel", "Post"}
        for d in defs.values():
            assert (len(d.faces), len(d.edges), len(d.vertices)) == (1, 4, 4)
        assert len(model.root.instances) == 10
        assert len(model.materials) == 24
        assert {"Layer0", "Roof"} <= {layer.name for layer in model.layers}
        by_id = {m.id: m for m in model.materials}
        painted = next(iter(defs["Frame"].faces.values()))
        assert by_id[painted.material_id].name == "Red"
        # nested group: InnerFrame is placed inside Panel
        assert [model.definitions[i.ref_idx].name for i in defs["Panel"].instances] \
            == ["InnerFrame"]

    def test_sketchup_4_has_no_instance_names_and_sketchup_5_does(self):
        v4 = _parse(FIXTURES / "legacy_v4_synthetic.skp")
        v5 = _parse(FIXTURES / "legacy_v5_synthetic.skp")
        v6 = _parse(V6)
        assert any(i.name for i in v6.root.instances)
        assert all(i.name == "" for i in v4.root.instances)
        assert [i.name for i in v5.root.instances] == [i.name for i in v6.root.instances]


class TestTextured:
    def test_sketchup_4_and_5_read_the_bare_inline_texture(self):
        reference = _parse(V3_TEXTURED)
        ref_tex = next(m.texture for m in reference.materials if m.texture)
        for name in ("legacy_v4_textured.skp", "legacy_v5_textured.skp"):
            model = _parse(FIXTURES / name)
            tex = next(m.texture for m in model.materials if m.texture)
            assert tex.data and tex.data == ref_tex.data      # the same PNG, byte for byte
            assert tex.data.startswith(b"\x89PNG")
            faces = sum(len(d.faces) for d in model.definitions.values()) \
                + len(model.root.faces)
            assert faces == 1

    def test_sketchup_2020_model_without_definitions_keeps_its_root_entities(self):
        # The root entity count was handed over as a "definition count", so the
        # face and its four edges were consumed as definitions and the model
        # came out empty - with no error.
        model = _parse(FIXTURES / "legacy_v2020_textured.skp")
        assert len(model.root.faces) == 1
        assert len(model.root.edges) == 4
        assert len(model.root.vertices) == 4
        assert any(m.texture and m.texture.data for m in model.materials)


# One real CDimensionLinear record per version, taken from the repository's own
# Untitled.skp re-saved at SketchUp 4, 5 and 6 (record bytes only - from the
# entity preamble to the end of the trailing block). All three describe the same
# dimension, so they must decode to the same endpoints and offset.
_DIM = {
    4: (
        "00000f0000010100001d00fffeff000100000004000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000010000000400000000000000000000007e"
        "210ac0556f56400000000000000000000000000000000000000000000000000000f0bf000000"
        "000000000000000000000000000000000000000080000000000000f0bf000000000000008001"
        "000000b95c2e97cbe512c00000000000000000"
    ),
    5: (
        "00000f000001010000001d00fffeff0001000000040000000000000000000000000000000000"
        "0000000000000000000000000000000000000000000001000000040000000000000000000000"
        "7e210ac0556f56400000000000000000000000000000000000000000000000000000f0bf0000"
        "00000000000000000000000000000000000000000080000000000000f0bf0000000000000080"
        "01000000b95c2e97cbe512c00000000000000000"
    ),
    6: (
        "00000f000001010000001d00fffeff00ffff0100070043536b466f6e740000fffeff06540061"
        "0068006f006d00610000000c0000000000000000000024400003000000010000000400000000"
        "0000000000000000000000000000000000000000000000000000000000000000000000010000"
        "000400000000000000000000007e210ac0556f56400000000000000000000000000000000000"
        "000000000000000000f0bf000000000000000000000000000000000000000000000080000000"
        "000000f0bf000000000000008001000000b95c2e97cbe512c0000000000000000003000000"
    ),
}


def _decode_dimension(ver):
    record = bytes.fromhex(_DIM[ver])
    tail = b"\xff\x7f\x00\x00\x00\x80"      # the next entity's big class-ref tag
    ar = _Archive(record + tail, ver)
    ar.readers.update(_READERS)
    ar.next_slot = ar.walk_base = 100
    r = _R(ar.data, 0)
    out = _read_dimlinear(ar, r)
    return out, r.pos, len(record)


class TestDimensionLayout:
    @pytest.mark.parametrize("ver", [4, 5, 6])
    def test_record_is_consumed_exactly(self, ver):
        _, consumed, size = _decode_dimension(ver)
        assert consumed == size

    @pytest.mark.parametrize("ver", [4, 5])
    def test_sketchup_4_and_5_decode_the_same_dimension_as_6(self, ver):
        got, _, _ = _decode_dimension(ver)
        want, _, _ = _decode_dimension(6)
        assert got["a"] == want["a"] == (0.0, 0.0, 0.0)
        assert got["b"] == want["b"]
        assert got["b"][1] == pytest.approx(89.7396087740835)
        assert got["offset"] == want["offset"] == pytest.approx(-4.724409448818897)


class TestFailureReporting:
    def test_the_deepest_failure_is_reported_not_the_last_candidate(self, monkeypatch):
        """Each slot-base candidate is tried in turn and all but the right one
        die immediately; the message must come from the one that got furthest."""
        calls = []

        def fake_walk_model(data, ver, start, mat_count, base):
            calls.append(base)
            offsets = {3: 0x100, 2: 0x9000, 1: 0x120}
            raise legacy.LegacyParseError(f"back-ref to unwalked slot 7 @{offsets[base]:#x}: ...")

        monkeypatch.setattr(legacy, "_walk_model", fake_walk_model)
        monkeypatch.setattr(legacy, "_probe_layer_anchor_bases",
                            lambda *a, **k: [3, 2, 1])
        data = (FIXTURES / "legacy_v4_textured.skp").read_bytes()   # one material
        with pytest.raises(legacy.LegacyParseError) as info:
            legacy._walk(data)
        assert calls == [3, 2, 1]
        assert re.search(r"@0x9000", str(info.value))
