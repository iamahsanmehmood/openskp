"""Tests for code-built 2021+ VFF blank document (openskp._blank_document_vff)."""
from __future__ import annotations

import base64
import hashlib
import os
import pathlib
import re
import struct
import zlib

import pytest

from openskp import SkpFile
from openskp._blank_document_vff import build_blank_vff, build_plain_png
from openskp._vff_writer.archive import build_skp, read_zip, verify_integrity

TESTS_DIR = pathlib.Path(__file__).resolve().parent
FIXTURES = TESTS_DIR / "fixtures"
SRC_DIR = TESTS_DIR.parent / "src"
_SDK_DLL_PATH = os.environ.get(
    "OPENSKP_TEST_SKETCHUP_SDK_DLL",
    r"C:\Program Files\SketchUp\SketchUp 2025\SketchUp\SketchUpAPI.dll",
)

# Expected SHA-256 digests of every non-thumbnail entry of blank_vff.skp.
EXPECTED_HEADER_PREFIX_65B_SHA256 = "5e57871c4904fb0b7eb11a14975fa799f6b181ca57084c19e16a6843984c5692"
EXPECTED_HEADER_69B_TEMPLATE_ARCHIVE_SHA256 = "6354db951fda2da69af0eea71666823d865153c6969cb93e4316c6065568d880"
EXPECTED_META_DAT_SHA256 = "07bed0ef57d1138a48c4af94a053c636a395bf1836a855bacc7c847286449bff"
EXPECTED_LAYER0_MATERIAL_XML_SHA256 = "1e7f391e349e7bcee73541899d0f886535ed8db8d14508855904009c6ce82cf2"
EXPECTED_STYLE_XML_SHA256 = "c17600374310847c4369b2335465c88bbc17c95390b8eb2a27563c125f31fed3"
EXPECTED_MODEL_DAT_SHA256 = "e045c77085990b26630f534b4cce87720dd75b24a95f6beed7476aa485f754b4"


def test_build_blank_vff_entries_match_fixture_byte_for_byte():
    doc = build_blank_vff()
    fixture_bytes = (FIXTURES / "blank_vff.skp").read_bytes()
    fixture_entries = {e.name: e for e in read_zip(fixture_bytes, 69)}
    built_entries = {e.name: e for e in read_zip(doc.data, doc.zip_offset)}

    assert verify_integrity(doc.data)
    assert doc.zip_offset == 69
    assert doc.header_vff_offset == 56
    assert doc.integrity_word_offset == 65
    assert list(built_entries) == list(fixture_entries)

    # 1. 69-byte header: first 65 bytes identical, and 69 bytes identical when
    #    paired with the fixture archive's integrity word.
    assert hashlib.sha256(doc.header[:65]).hexdigest() == EXPECTED_HEADER_PREFIX_65B_SHA256
    assert doc.header[:65] == fixture_bytes[:65]
    repatched_header = build_skp(doc.header, list(fixture_entries.values()))[:69]
    assert hashlib.sha256(repatched_header).hexdigest() == EXPECTED_HEADER_69B_TEMPLATE_ARCHIVE_SHA256
    assert repatched_header == fixture_bytes[:69]

    # 2. meta/meta.dat (366 B)
    assert len(doc.meta_dat) == 366
    assert hashlib.sha256(doc.meta_dat).hexdigest() == EXPECTED_META_DAT_SHA256
    assert built_entries["meta/meta.dat"].data == fixture_entries["meta/meta.dat"].data

    # 3. materials/Layer_Layer0/material.xml (995 B)
    assert len(doc.layer0_material_xml) == 995
    assert hashlib.sha256(doc.layer0_material_xml).hexdigest() == EXPECTED_LAYER0_MATERIAL_XML_SHA256
    assert (
        built_entries["materials/Layer_Layer0/material.xml"].data
        == fixture_entries["materials/Layer_Layer0/material.xml"].data
    )

    # 4. Both style.xml entries (5,238 B each)
    assert len(doc.style_xml) == 5238
    assert hashlib.sha256(doc.style_xml).hexdigest() == EXPECTED_STYLE_XML_SHA256
    for style_entry in (
        "styles/[Simple Style]/style.xml",
        "styles/[Simple Style]_1/style.xml",
    ):
        assert built_entries[style_entry].data == fixture_entries[style_entry].data

    # 5. model.dat (5,282 B)
    assert len(doc.model_dat) == 5282
    assert hashlib.sha256(doc.model_dat).hexdigest() == EXPECTED_MODEL_DAT_SHA256
    assert built_entries["model.dat"].data == fixture_entries["model.dat"].data


def test_build_blank_vff_thumbnails_are_ours_and_valid_png():
    doc = build_blank_vff()
    fixture_bytes = (FIXTURES / "blank_vff.skp").read_bytes()
    fixture_entries = {e.name: e for e in read_zip(fixture_bytes, 69)}
    built_entries = {e.name: e for e in read_zip(doc.data, doc.zip_offset)}

    png = build_plain_png()
    assert doc.thumbnail_png == png

    for thumb_name in ("meta/model_thumbnail.png", "meta/preview_thumbnail.png"):
        built_thumb = built_entries[thumb_name].data
        fixture_thumb = fixture_entries[thumb_name].data
        assert built_thumb == png
        assert built_thumb != fixture_thumb
        assert len(built_thumb) < 128

        # Verify valid PNG framing + CRC + zlib IDAT decompression.
        assert built_thumb.startswith(b"\x89PNG\r\n\x1a\n")
        pos = 8
        chunks = []
        while pos < len(built_thumb):
            clen = struct.unpack_from(">I", built_thumb, pos)[0]
            ctype = built_thumb[pos + 4 : pos + 8]
            cbody = built_thumb[pos + 8 : pos + 8 + clen]
            ccrc = struct.unpack_from(">I", built_thumb, pos + 8 + clen)[0]
            assert (zlib.crc32(ctype + cbody) & 0xFFFFFFFF) == ccrc
            chunks.append((ctype, cbody))
            pos += 12 + clen
        assert [t for t, _ in chunks] == [b"IHDR", b"IDAT", b"IEND"]
        w, h, depth, colortype, comp, filt, interlace = struct.unpack(">IIBBBBB", chunks[0][1])
        assert (w, h, depth, colortype, comp, filt, interlace) == (1, 1, 8, 6, 0, 0, 0)
        raw_scanline = zlib.decompress(chunks[1][1])
        assert len(raw_scanline) == 5 and raw_scanline[0] == 0


def test_built_blank_vff_reads_back_in_vff_reader(tmp_path):
    doc = build_blank_vff()
    skp_path = tmp_path / "built_blank_vff.skp"
    skp_path.write_bytes(doc.data)

    model = SkpFile.open(str(skp_path)).parse()
    assert model.version == "{25.0.571}"
    assert model.units == "Inches"
    assert [lay.name for lay in model.layers] == ["Layer0"]
    assert model.layers[0].hidden is False
    assert (model.layers[0].color_r, model.layers[0].color_g, model.layers[0].color_b) == (255, 255, 255)
    assert len(model.materials) == 0
    assert len(model.definitions) == 0
    assert len(model.root.faces) == 0
    assert len(model.root.edges) == 0
    assert len(model.root.vertices) == 0
    assert len(model.root.instances) == 0
    assert len(model.pages) == 0
    assert model.unread_root_entities == 0
    assert model.face_front_color == (255, 255, 255)
    assert model.face_back_color == (162, 183, 198)
    assert model.color_by_tag is False
    assert len(model.styles) == 2
    assert {s.name for s in model.styles} == {"[Simple Style]"}
    assert set(model.attribute_dictionaries) == {
        "GeoReference",
        "temp",
        "GSU_ContributorsInfo",
    }

    # Verify structural IDs returned by BlankDocumentVff match what create_vff uses.
    assert doc.initial_next_id == 41203
    assert doc.default_layer_id == 13869
    assert doc.default_layer_material_id == 16049
    assert doc.default_font_id == 16047
    assert doc.default_style_id == 16050
    assert len(doc.line_style_ids) == 12


def _assert_no_skp_or_template_blobs(src_root: pathlib.Path) -> None:
    # 1. Zero .skp files anywhere under src_root, and no _scaffold directory.
    skp_files = sorted(p.relative_to(src_root).as_posix() for p in src_root.rglob("*.skp"))
    assert skp_files == [], f"Unexpected .skp file(s) under src/: {skp_files}"
    assert not (src_root / "openskp" / "_scaffold").exists()

    # 2. Neither blank_vff.skp's 5,282-byte model.dat (nor its thumbnails, nor
    #    blank_v17.skp) may appear in raw, hex, 0x.. byte-array, or base64 form
    #    anywhere under src_root.
    vff_bytes = (FIXTURES / "blank_vff.skp").read_bytes()
    vff_entries = {e.name: e.data for e in read_zip(vff_bytes, 69)}
    forbidden_blobs = {
        "blank_vff.skp": vff_bytes,
        "blank_vff model.dat": vff_entries["model.dat"],
        "blank_vff model_thumbnail.png": vff_entries["meta/model_thumbnail.png"],
        "blank_vff preview_thumbnail.png": vff_entries["meta/preview_thumbnail.png"],
        "blank_v17.skp": (FIXTURES / "blank_v17.skp").read_bytes(),
    }

    hex_tokens = {k: b[:64].hex() for k, b in forbidden_blobs.items()}
    b64_tokens = {
        k: base64.b64encode(b[:96]).decode("ascii")[:80]
        for k, b in forbidden_blobs.items()
    }
    hex_byte_runs = {
        k: [b[i : i + 64] for i in range(0, len(b) - 63, 64)]
        for k, b in forbidden_blobs.items()
    }

    for path in sorted(src_root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        raw = path.read_bytes()
        rel = path.relative_to(src_root).as_posix()
        for label, blob in forbidden_blobs.items():
            assert blob not in raw, f"{rel} embeds raw {label}"
        text = raw.decode("utf-8", errors="ignore")
        compact = re.sub(r"\s+", "", text)
        lower_compact = compact.lower()
        for label, htok in hex_tokens.items():
            assert htok not in lower_compact, f"{rel} embeds hex {label}"
        for label, btok in b64_tokens.items():
            assert btok not in compact, f"{rel} embeds base64 {label}"
        # Check against 0x.. byte-literal arrays (>= 64 consecutive template bytes).
        hex_literals = [int(m, 16) for m in re.findall(r"0x([0-9a-fA-F]{2})\b", text)]
        if len(hex_literals) >= 64:
            lit_bytes = bytes(hex_literals)
            for label, runs in hex_byte_runs.items():
                for run in runs:
                    assert run not in lit_bytes, f"{rel} embeds 0x.. byte array from {label}"


def test_no_skp_or_template_model_dat_bytes_under_src():
    _assert_no_skp_or_template_blobs(SRC_DIR)


def test_guard_detects_skp_and_model_dat_blobs(tmp_path):
    vff_bytes = (FIXTURES / "blank_vff.skp").read_bytes()
    model_dat = {e.name: e.data for e in read_zip(vff_bytes, 69)}["model.dat"]

    clean = tmp_path / "clean"
    (clean / "openskp").mkdir(parents=True)
    (clean / "openskp" / "ok.py").write_text("X = 1\n", encoding="utf-8")
    _assert_no_skp_or_template_blobs(clean)

    # 1. Stray .skp file
    case_skp = tmp_path / "case_skp"
    (case_skp / "openskp").mkdir(parents=True)
    (case_skp / "openskp" / "stray.skp").write_bytes(b"dummy")
    with pytest.raises(AssertionError, match=r"Unexpected \.skp"):
        _assert_no_skp_or_template_blobs(case_skp)

    # 2. Raw model.dat bytes
    case_raw = tmp_path / "case_raw"
    (case_raw / "openskp").mkdir(parents=True)
    (case_raw / "openskp" / "blob.bin").write_bytes(model_dat)
    with pytest.raises(AssertionError, match="embeds raw blank_vff model.dat"):
        _assert_no_skp_or_template_blobs(case_raw)

    # 3. Hex string of model.dat
    case_hex = tmp_path / "case_hex"
    (case_hex / "openskp").mkdir(parents=True)
    (case_hex / "openskp" / "hexblob.py").write_text(
        f'DATA = "{model_dat[:80].hex()}"\n', encoding="utf-8"
    )
    with pytest.raises(AssertionError, match="embeds hex blank_vff model.dat"):
        _assert_no_skp_or_template_blobs(case_hex)

    # 4. Base64 string of model.dat
    case_b64 = tmp_path / "case_b64"
    (case_b64 / "openskp").mkdir(parents=True)
    b64_str = base64.b64encode(model_dat[:120]).decode("ascii")
    (case_b64 / "openskp" / "b64blob.py").write_text(
        f'DATA = "{b64_str}"\n', encoding="utf-8"
    )
    with pytest.raises(AssertionError, match="embeds base64 blank_vff model.dat"):
        _assert_no_skp_or_template_blobs(case_b64)

    # 5. 0x.. byte-literal array of model.dat
    case_arr = tmp_path / "case_arr"
    (case_arr / "openskp").mkdir(parents=True)
    arr_str = ", ".join(f"0x{b:02X}" for b in model_dat[:80])
    (case_arr / "openskp" / "arrblob.py").write_text(
        f"DATA = [{arr_str}]\n", encoding="utf-8"
    )
    with pytest.raises(AssertionError, match=r"embeds 0x\.\. byte array from blank_vff model\.dat"):
        _assert_no_skp_or_template_blobs(case_arr)


@pytest.mark.skipif(not os.path.exists(_SDK_DLL_PATH), reason="SketchUp SDK not present on this machine")
def test_built_blank_vff_opens_cleanly_in_sketchup_sdk(tmp_path):
    import ctypes

    doc = build_blank_vff()
    skp_path = tmp_path / "sdk_blank_vff_check.skp"
    skp_path.write_bytes(doc.data)

    dll = ctypes.CDLL(_SDK_DLL_PATH)
    dll.SUInitialize.argtypes = []
    dll.SUTerminate.argtypes = []
    dll.SUModelCreateFromFileWithStatus.argtypes = [
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_int),
    ]
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
        status = ctypes.c_int(-1)
        assert (
            dll.SUModelCreateFromFileWithStatus(
                ctypes.byref(model), str(skp_path).encode("utf-8"), ctypes.byref(status)
            )
            == 0
        )
        assert status.value == 0
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
