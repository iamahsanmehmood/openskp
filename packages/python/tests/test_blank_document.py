"""Tests for code-built classic v17 blank document (openskp._blank_document)."""
from __future__ import annotations

import hashlib
import importlib
import os
import pathlib

import pytest

from openskp import SkpFile, legacy
from openskp._blank_document import _VERSION_MAP_V17, build_blank_v17

create_mod = importlib.import_module("openskp.create")

TESTS_DIR = pathlib.Path(__file__).resolve().parent
FIXTURES = TESTS_DIR / "fixtures"
SRC_OPENSKP = TESTS_DIR.parent / "src" / "openskp"
_SDK_DLL_PATH = os.environ.get(
    "OPENSKP_TEST_SKETCHUP_SDK_DLL",
    r"C:\Program Files\SketchUp\SketchUp 2025\SketchUp\SketchUpAPI.dll",
)


def test_build_blank_v17_matches_fixture_byte_for_byte():
    doc = build_blank_v17()
    expected = (FIXTURES / "blank_v17.skp").read_bytes()
    assert len(doc.data) == 5796
    assert hashlib.sha256(doc.data).hexdigest() == create_mod._SCAFFOLD_SHA256
    assert doc.data == expected


def test_built_blank_document_reads_back_in_classic_reader(tmp_path):
    doc = build_blank_v17()
    skp_path = tmp_path / "built_blank_v17.skp"
    skp_path.write_bytes(doc.data)

    # 1. Verify header and CVersionMap class-schema table with legacy._R.
    r = legacy._R(doc.data, 0)
    assert r.utf16() == "SketchUp Model"
    assert r.utf16() == "{17.0.1}"
    r.raw(16)  # model GUID
    assert r.utf16() == ""
    r.u32()    # save timestamp
    assert r.u16() == 0xFFFF
    assert r.u16() == 0
    vmap_name_len = r.u16()
    assert r.raw(vmap_name_len) == b"CVersionMap"
    parsed_vmap = []
    while True:
        cls_name = r.utf16()
        if cls_name == "End-Of-Version-Map":
            break
        parsed_vmap.append((cls_name, r.u32()))
    assert tuple(parsed_vmap) == _VERSION_MAP_V17
    assert len(parsed_vmap) == 58

    # 2. Verify full classic reader parse (Layer0, zero entities/materials/defs/scenes, face colours).
    model = SkpFile.open(str(skp_path)).parse()
    assert model.version == "{17.0.1}"
    assert [lay.name for lay in model.layers] == ["Layer0"]
    assert model.layers[0].hidden is False
    assert (model.layers[0].color_r, model.layers[0].color_g, model.layers[0].color_b) == (255, 84, 84)
    assert len(model.materials) == 0
    assert len(model.definitions) == 0
    assert len(model.root.faces) == 0
    assert len(model.root.edges) == 0
    assert len(model.root.vertices) == 0
    assert len(model.root.instances) == 0
    assert len(model.pages) == 0
    assert model.unread_root_entities == 0
    assert model.face_front_color == (225, 225, 200)
    assert model.face_back_color == (128, 128, 200)
    assert model.color_by_tag is False
    assert model.attribute_dictionaries == {}

    # 3. Verify CSkpStyleManager in the tail holds exactly one saved style ("Style") + working copy.
    style_mgr_pos = doc.tail_pos + doc.style_object_tail_rel - 7
    sr = legacy._R(doc.data, style_mgr_pos)
    assert sr.u16() == 0  # null CAttributeContainer
    assert sr.u8() == 0   # pid_mask = 0
    assert sr.u32() == 1  # 1 saved style
    kind, _ = legacy._read_tag(sr)
    assert kind == "decl"
    assert sr.u16() == 2  # CSkpStyle schema 2
    sname_len = sr.u16()
    assert sr.raw(sname_len) == b"CSkpStyle"
    # Saved style name is "Style"
    saved_probe = legacy._R(doc.data, sr.pos)
    assert saved_probe.u16() == 0
    legacy._skip_pid(saved_probe)
    saved_probe.raw(16)
    assert saved_probe.utf16() == ""
    assert saved_probe.u32() == 3
    assert saved_probe.utf16() == "Style"
    assert saved_probe.utf16() == ""
    legacy._skip_style(sr, schema=2, has_pid=True)
    # Selected-style back-ref points to slot 15 (the saved CSkpStyle object)
    sel_kind, sel_slot = legacy._read_tag(sr)
    assert (sel_kind, sel_slot) == ("obj", 15)
    # Working style copy is a class-ref to slot 14 (CSkpStyle)
    work_kind, work_cslot = legacy._read_tag(sr)
    assert (work_kind, work_cslot) == ("class", 14)
    legacy._skip_style(sr, schema=2, has_pid=True)
    assert sr.u8() == 0   # active_style_changed == False


def test_no_classic_skp_files_under_src_openskp():
    found = sorted(p.relative_to(SRC_OPENSKP).as_posix() for p in SRC_OPENSKP.rglob("*.skp"))
    # _scaffold/blank_vff.skp is replaced in Phase 3; no classic .skp or any other
    # .skp file may exist anywhere under src/openskp/.
    assert "_scaffold/blank_v17.skp" not in found
    assert set(found) <= {"_scaffold/blank_vff.skp"}, (
        f"Unexpected .skp file(s) found under src/openskp/: {found}"
    )


@pytest.mark.skipif(not os.path.exists(_SDK_DLL_PATH), reason="SketchUp SDK not present on this machine")
def test_built_blank_document_opens_cleanly_in_sketchup_sdk(tmp_path):
    import ctypes

    doc = build_blank_v17()
    skp_path = tmp_path / "sdk_blank_check.skp"
    skp_path.write_bytes(doc.data)

    dll = ctypes.CDLL(_SDK_DLL_PATH)
    dll.SUInitialize.argtypes = []
    dll.SUTerminate.argtypes = []
    dll.SUModelCreateFromFile.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_char_p]
    dll.SUModelFixErrors.argtypes = [ctypes.c_void_p]
    dll.SUModelGetNumLayers.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
    dll.SUModelGetNumMaterials.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
    dll.SUModelGetNumComponentDefinitions.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
    dll.SUModelGetStyles.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    dll.SUStylesGetNumStyles.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
    dll.SUModelRelease.argtypes = [ctypes.POINTER(ctypes.c_void_p)]

    dll.SUInitialize()
    try:
        model = ctypes.c_void_p()
        assert dll.SUModelCreateFromFile(ctypes.byref(model), str(skp_path).encode("utf-8")) == 0
        assert dll.SUModelFixErrors(model) == 0
        nlayers = ctypes.c_size_t()
        nmats = ctypes.c_size_t()
        ndefs = ctypes.c_size_t()
        styles = ctypes.c_void_p()
        nstyles = ctypes.c_size_t()
        dll.SUModelGetNumLayers(model, ctypes.byref(nlayers))
        dll.SUModelGetNumMaterials(model, ctypes.byref(nmats))
        dll.SUModelGetNumComponentDefinitions(model, ctypes.byref(ndefs))
        dll.SUModelGetStyles(model, ctypes.byref(styles))
        dll.SUStylesGetNumStyles(styles, ctypes.byref(nstyles))
        assert nlayers.value == 1
        assert nmats.value == 0
        assert ndefs.value == 0
        assert nstyles.value == 1
        dll.SUModelRelease(ctypes.byref(model))
    finally:
        dll.SUTerminate()
