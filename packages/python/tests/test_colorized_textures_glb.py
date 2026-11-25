"""Colorized textures in Python GLB and instanced GLB exports (#285, step 1).

SketchUp bakes hue shift (``colorize_type == 0``) or monochrome tint (``colorize_type == 1``)
into the exported texture image while keeping the untinted image internally. ``Scene`` and
``InstancedScene`` continue to keep the untinted ``SceneTexture`` and sRGB ``baseColorFactor``
(with ``extras.sketchup.colorized``), while ``openskp.export.glb`` and
``openskp.export.instanced_glb`` bake colorized textures once per material when ``textures=True``
and Pillow is installed, setting ``baseColorFactor`` to ``[1.0, 1.0, 1.0, opacity]``.
When Pillow is not installed, GLB export stays as before (untinted PNG/JPEG with material factor)
and emits one warning on the ``openskp`` logger.
"""

from __future__ import annotations

import builtins
import ctypes
from ctypes import POINTER, byref, c_char_p, c_double, c_int, c_size_t, c_ubyte, c_void_p
import io
import json
import logging
import os
import pathlib
import struct
from typing import Dict, List, Tuple

import numpy as np
import pytest

import openskp
from openskp import SkpFile
from openskp.export import glb, instanced_glb

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
LEGACY_COLORIZE_FIXTURE = FIXTURES / "legacy_textured_colorize_2017.skp"
DEFAULT_SDK_DLL = r"C:\Program Files\SketchUp\SketchUp 2025\SketchUp\SketchUpAPI.dll"

# Ground-truth RGB pixels from SUTextureGetColorizedImageRep on legacy_textured_colorize_2017.skp
# (2x1 base PNG: left=[200, 40, 40], right=[40, 40, 200]).
SDK_COLORIZE_RGB_AND_OPACITY: Dict[str, Tuple[List[Tuple[int, int, int]], float]] = {
    "plain": ([(200, 40, 40), (40, 40, 200)], 1.0),
    "tint": ([(32, 237, 80), (32, 237, 80)], 1.0),
    "shift": ([(172, 253, 46), (253, 46, 172)], 1.0),
    "tint_half": ([(65, 73, 255), (65, 73, 255)], 0.5),
}


def _read_glb(path: pathlib.Path) -> Tuple[dict, bytes]:
    data = path.read_bytes()
    (json_len,) = struct.unpack_from("<I", data, 12)
    tree = json.loads(data[20 : 20 + json_len])
    bin_offset = 20 + json_len
    if bin_offset < len(data):
        (bin_len,) = struct.unpack_from("<I", data, bin_offset)
        bin_chunk = data[bin_offset + 8 : bin_offset + 8 + bin_len]
    else:
        bin_chunk = b""
    return tree, bin_chunk


def _extract_glb_image_bytes(tree: dict, bin_chunk: bytes, image_index: int) -> bytes:
    bv_index = tree["images"][image_index]["bufferView"]
    bv = tree["bufferViews"][bv_index]
    offset = bv.get("byteOffset", 0)
    return bin_chunk[offset : offset + bv["byteLength"]]


def _build_colorize_quads_skp(
    tmp_path: pathlib.Path, repeats_per_material: int = 1
) -> pathlib.Path:
    """Build a .skp with placed quads and component instances for ``plain``, ``tint``,
    ``shift``, and ``tint_half`` using the base PNG and parameters of
    ``legacy_textured_colorize_2017.skp``, without needing SketchUpAPI.dll."""
    orig_model = SkpFile.open(str(LEGACY_COLORIZE_FIXTURE)).parse()
    tex_png_path = tmp_path / "base_2x1.png"
    tex_png_path.write_bytes(orig_model.materials[0].texture.data)

    builder = openskp.create()
    default_tail = (
        bytes([255, 255, 255, 255, 0, 255, 255, 255, 255])
        + b"\xff\xfe\xff\x00"
        + struct.pack("<II", 1, 0)
    )
    handles: Dict[str, int] = {}
    for src_m in orig_model.materials:
        before_len = len(builder._material_writer.buf)
        opacity = src_m.transparency if src_m.transparency < 1.0 - 1e-6 else None
        handles[src_m.name] = builder.add_texture_material(
            src_m.name, str(tex_png_path), filename="base_2x1.png", opacity=opacity
        )
        mbuf = builder._material_writer.buf
        j = bytes(mbuf).index(default_tail, before_len)
        cr, cg, cb_v, ca = src_m.color
        replacement = (
            bytes([120, 40, 120, 255, 0, cr, cg, cb_v, ca])
            + b"\xff\xfe\xff\x00"
            + struct.pack("<II", 2 if src_m.colorized else 1, int(src_m.colorize_type or 0))
        )
        mbuf[j : j + len(default_tail)] = replacement

    with builder.add_component_definition("ColorizedTile") as comp:
        comp.add_face(
            [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)],
            material=handles["shift"],
        )

    for idx, mname in enumerate(("plain", "tint", "shift", "tint_half")):
        for r in range(repeats_per_material):
            x0 = idx * 2.0
            y0 = r * 2.0
            builder.add_face(
                [(x0, y0, 0.0), (x0 + 1.0, y0, 0.0), (x0 + 1.0, y0 + 1.0, 0.0), (x0, y0 + 1.0, 0.0)],
                material=handles[mname],
            )
    for r in range(repeats_per_material):
        builder.add_instance(comp, translation=(10.0, r * 2.0, 0.0))

    out_skp = tmp_path / "colorize_quads.skp"
    out_skp.write_bytes(builder.to_bytes())
    return out_skp


@pytest.mark.parametrize("exporter_mod", [glb, instanced_glb])
def test_colorized_glb_bakes_shift_tint_and_opacity_without_mutating_scene(tmp_path, exporter_mod):
    PIL_Image = pytest.importorskip("PIL.Image")

    skp_path = _build_colorize_quads_skp(tmp_path, repeats_per_material=3)
    skp = SkpFile.open(str(skp_path))
    skp.parse()

    scene_before = skp.build_scene()
    inst_before = skp.build_instanced_scene()
    expected_scene_mats = [json.loads(json.dumps(m)) for m in scene_before.gltf_materials]
    expected_inst_mats = [json.loads(json.dumps(m)) for m in inst_before.gltf_materials]
    assert len(scene_before.textures) == 1
    assert len(inst_before.textures) == 1
    orig_tex_bytes = scene_before.textures[0].data

    out_glb = tmp_path / "colorized.glb"
    exporter_mod.export(skp, str(out_glb), textures=True)

    # Scene and InstancedScene gltf_materials and textures remain unchanged.
    scene_after = skp.build_scene()
    inst_after = skp.build_instanced_scene()
    assert scene_after.gltf_materials == expected_scene_mats
    assert inst_after.gltf_materials == expected_inst_mats
    assert [t.data for t in scene_after.textures] == [orig_tex_bytes]
    assert [t.data for t in inst_after.textures] == [orig_tex_bytes]

    tree, bin_chunk = _read_glb(out_glb)
    textured_mats = [
        m for m in tree["materials"] if "baseColorTexture" in m.get("pbrMetallicRoughness", {})
    ]
    assert len(textured_mats) == 4

    decoded_by_pixels = {}
    for mat in textured_mats:
        pbr = mat["pbrMetallicRoughness"]
        factor = pbr.get("baseColorFactor", [1.0, 1.0, 1.0, 1.0])
        assert factor[:3] == pytest.approx([1.0, 1.0, 1.0], abs=1e-6)
        tex_idx = pbr["baseColorTexture"]["index"]
        img_idx = tree["textures"][tex_idx]["source"]
        raw = _extract_glb_image_bytes(tree, bin_chunk, img_idx)
        with PIL_Image.open(io.BytesIO(raw)) as im:
            rgb = np.asarray(im.convert("RGB"), dtype=np.uint8)
        pixels = [tuple(int(c) for c in rgb[0, 0]), tuple(int(c) for c in rgb[0, 1])]
        decoded_by_pixels[(tuple(pixels), round(float(factor[3]), 2))] = True

    for expected_pixels, expected_opacity in SDK_COLORIZE_RGB_AND_OPACITY.values():
        assert (tuple(expected_pixels), expected_opacity) in decoded_by_pixels


@pytest.mark.parametrize("exporter_mod", [glb, instanced_glb])
def test_colorized_textures_cached_once_per_material(tmp_path, monkeypatch, exporter_mod):
    PIL_Image = pytest.importorskip("PIL.Image")

    skp_path = _build_colorize_quads_skp(tmp_path, repeats_per_material=8)
    skp = SkpFile.open(str(skp_path))
    skp.parse()

    colorize_calls = []
    real_colorize = glb._colorize_image_u8

    def counting_colorize(rgba_u8, target_rgb, colorize_type, **kwargs):
        colorize_calls.append((target_rgb, colorize_type))
        return real_colorize(rgba_u8, target_rgb, colorize_type, **kwargs)

    open_calls = []
    real_open = PIL_Image.open

    def counting_open(*args, **kwargs):
        open_calls.append(1)
        return real_open(*args, **kwargs)

    monkeypatch.setattr(glb, "_colorize_image_u8", counting_colorize)
    monkeypatch.setattr(PIL_Image, "open", counting_open)

    out_glb = tmp_path / "cached.glb"
    exporter_mod.export(skp, str(out_glb), textures=True)

    # 3 distinct colorized variants (tint, shift, tint_half) over 1 shared base PNG:
    # decoded once, colorized once per variant despite 8+ repeated faces/instances each.
    assert len(open_calls) == 1
    assert len(colorize_calls) == 3
    tree, _ = _read_glb(out_glb)
    assert len(tree["images"]) == 4


def test_colorize_rgba_greyscale_alpha_row_blocks_and_achromatic_target():
    # Greyscale pixels in shift and tint modes, including black -> 0.01 lightness clamp.
    grey = np.array(
        [[[0, 0, 0, 0], [128, 128, 128, 64], [255, 255, 255, 255]]], dtype=np.uint8
    )
    shifted = glb._colorize_rgba_u8(grey, (200, 90, 20), 0)
    tinted = glb._colorize_rgba_u8(grey, (200, 90, 20), 1)
    assert shifted[..., 3].tolist() == [[0, 64, 255]]
    assert tinted[..., 3].tolist() == [[0, 64, 255]]
    # Black pixel clamps to nl >= 0.01 rather than staying [0, 0, 0].
    assert shifted[0, 0, 0] > 0

    # Chromatic image (avg_s > 0) colorized toward a pure grey target (tgt_s == 0) desaturates.
    vivid = np.array([[[240, 30, 80, 180], [20, 210, 90, 255]]], dtype=np.uint8)
    for ctype in (0, 1):
        desat = glb._colorize_rgba_u8(vivid, (140, 140, 140), ctype)
        assert np.array_equal(desat[..., 0], desat[..., 1])
        assert np.array_equal(desat[..., 1], desat[..., 2])
        assert desat[..., 3].tolist() == [[180, 255]]

    # Chromatic pixels whose whole-image average is grey (avg_s == 0) keep delta_s == 0
    # (within <= 1/255 of SUTextureGetColorizedImageRep's [[255, 24, 24], [24, 255, 255]]).
    grey_avg_chrom = np.array([[[255, 1, 1, 255], [1, 255, 255, 255]]], dtype=np.uint8)
    s0 = glb._colorize_rgba_u8(grey_avg_chrom, (140, 140, 140), 0)[..., :3].astype(int)
    s1 = glb._colorize_rgba_u8(grey_avg_chrom, (140, 140, 140), 1)[..., :3].astype(int)
    assert np.max(np.abs(s0 - np.array([[[255, 24, 24], [24, 255, 255]]]))) <= 1
    assert np.max(np.abs(s1 - np.array([[[255, 24, 24], [255, 24, 24]]]))) <= 1

    # Row-block chunking produces identical pixels regardless of block size.
    rng = np.random.default_rng(7)
    multi_row = rng.integers(0, 256, size=(37, 19, 4), dtype=np.uint8)
    for ctype in (0, 1):
        whole = glb._colorize_rgba_u8(multi_row, (200, 90, 20), ctype, max_block_pixels=100_000)
        chunked = glb._colorize_rgba_u8(multi_row, (200, 90, 20), ctype, max_block_pixels=40)
        assert np.array_equal(whole, chunked)


def _inject_exif_orientation_app1(jpeg_bytes: bytes, orientation: int = 6) -> bytes:
    assert jpeg_bytes[:2] == b"\xff\xd8"
    tiff = (
        b"II"
        + struct.pack("<HI", 42, 8)
        + struct.pack("<H", 1)
        + struct.pack("<HHII", 0x0112, 3, 1, orientation)
        + struct.pack("<I", 0)
    )
    payload = b"Exif\x00\x00" + tiff
    app1 = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    return jpeg_bytes[:2] + app1 + jpeg_bytes[2:]


@pytest.mark.parametrize("exporter_mod", [glb, instanced_glb])
def test_colorized_jpeg_stays_jpeg_and_exif_orientation_is_stripped(
    tmp_path, monkeypatch, exporter_mod
):
    PIL_Image = pytest.importorskip("PIL.Image")
    from PIL import ImageOps

    # Asymmetric 8x4 JPEG (top-left red, bottom-right blue) with EXIF Orientation=6 (Rotate 90 CW).
    arr = np.zeros((4, 8, 3), dtype=np.uint8)
    arr[:2, :4] = [255, 0, 0]
    arr[2:, 4:] = [0, 0, 255]
    raw_buf = io.BytesIO()
    PIL_Image.fromarray(arr, "RGB").save(raw_buf, format="JPEG", quality=95, subsampling=0)
    clean_jpeg = raw_buf.getvalue()
    exif_jpeg = _inject_exif_orientation_app1(clean_jpeg, orientation=6)
    with PIL_Image.open(io.BytesIO(exif_jpeg)) as check_im:
        assert check_im.size == (8, 4)
        assert check_im.getexif().get(274) == 6
        assert ImageOps.exif_transpose(check_im).size == (4, 8)

    jpg_path = tmp_path / "exif6.jpg"
    jpg_path.write_bytes(exif_jpeg)

    builder = openskp.create()
    default_tail = (
        bytes([255, 255, 255, 255, 0, 255, 255, 255, 255])
        + b"\xff\xfe\xff\x00"
        + struct.pack("<II", 1, 0)
    )
    specs = [
        ("PlainExif", False, 0, (255, 255, 255)),
        ("ShiftExif", True, 0, (200, 90, 20)),
        ("TintExif", True, 1, (30, 160, 60)),
    ]
    handles = {}
    for mname, is_col, ctype, trgb in specs:
        before_len = len(builder._material_writer.buf)
        handles[mname] = builder.add_texture_material(mname, str(jpg_path), filename="exif6.jpg")
        if is_col:
            mbuf = builder._material_writer.buf
            j = bytes(mbuf).index(default_tail, before_len)
            mbuf[j : j + len(default_tail)] = (
                bytes([255, 255, 255, 255, 0, trgb[0], trgb[1], trgb[2], 255])
                + b"\xff\xfe\xff\x00"
                + struct.pack("<II", 2, ctype)
            )
    for idx, (mname, _, _, _) in enumerate(specs):
        x0 = float(idx * 3)
        builder.add_face(
            [(x0, 0.0, 0.0), (x0 + 2.0, 0.0, 0.0), (x0 + 2.0, 1.0, 0.0), (x0, 1.0, 0.0)],
            material=handles[mname],
        )

    skp_path = tmp_path / "exif_colorize.skp"
    skp_path.write_bytes(builder.to_bytes())
    skp = SkpFile.open(str(skp_path))
    skp.parse()

    out_glb = tmp_path / "with_pil.glb"
    exporter_mod.export(skp, str(out_glb), textures=True)

    # Scene.textures retains the exact original bytes (including APP1).
    assert skp.build_scene().textures[0].data == exif_jpeg

    tree, bin_chunk = _read_glb(out_glb)
    assert len(tree["images"]) == 3
    extracted_blobs = []
    for img_i, img_meta in enumerate(tree["images"]):
        # Both plain and colorized JPEG sources stay image/jpeg in the GLB.
        assert img_meta["mimeType"] == "image/jpeg"
        blob = _extract_glb_image_bytes(tree, bin_chunk, img_i)
        extracted_blobs.append(blob)
        assert b"Exif\x00\x00" not in blob
        with PIL_Image.open(io.BytesIO(blob)) as gim:
            assert gim.size == (8, 4)
            assert gim.getexif().get(274) is None
            assert ImageOps.exif_transpose(gim).size == (8, 4)

    # Plain JPEG was stripped of APP1 without re-encoding (trimesh 4-byte pads bufferViews
    # with 0x00 or 0x20 depending on trimesh version; clean_jpeg ends with EOI 0xFFD9).
    assert any(b.rstrip(b"\x00 ") == clean_jpeg for b in extracted_blobs)

    # Without Pillow, raw JPEG fallback also strips APP1 without re-encoding.
    orig_import = builtins.__import__

    def block_pil(name, *args, **kwargs):
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError("Simulated missing Pillow")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", block_pil)
    nopil_glb = tmp_path / "no_pil.glb"
    exporter_mod.export(skp, str(nopil_glb), textures=True)
    nopil_tree, nopil_bin = _read_glb(nopil_glb)
    nopil_blob = _extract_glb_image_bytes(nopil_tree, nopil_bin, 0)
    assert nopil_blob.rstrip(b"\x00 ") == clean_jpeg
    assert b"Exif\x00\x00" not in nopil_blob


@pytest.mark.parametrize("exporter_mod", [glb, instanced_glb])
def test_without_pillow_stays_as_today_with_one_warning_and_silent_stderr(
    tmp_path, monkeypatch, caplog, capsys, exporter_mod
):
    skp_path = _build_colorize_quads_skp(tmp_path, repeats_per_material=4)
    skp = SkpFile.open(str(skp_path))
    skp.parse()
    orig_tex_bytes = skp.build_scene().textures[0].data

    orig_import = builtins.__import__

    def block_pil(name, *args, **kwargs):
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError("Simulated missing Pillow")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", block_pil)

    # 1. With no logging handler configured, NullHandler keeps stdout/stderr silent (#556).
    capsys.readouterr()
    silent_glb = tmp_path / "silent_no_pil.glb"
    exporter_mod.export(skp, str(silent_glb), textures=True)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""

    # 2. With logging enabled on 'openskp', exactly one warning is emitted per export() call,
    #    plain texture gets white factor, and colorized materials keep untinted image + factor.
    logged_glb = tmp_path / "logged_no_pil.glb"
    with caplog.at_level(logging.WARNING, logger="openskp"):
        caplog.clear()
        exporter_mod.export(skp, str(logged_glb), textures=True)

    pil_warnings = [r for r in caplog.records if "Pillow" in r.getMessage()]
    assert len(pil_warnings) == 1

    tree, bin_chunk = _read_glb(logged_glb)
    # Only the 1 untinted PNG bufferView is stored in the GLB binary chunk.
    assert len({img["bufferView"] for img in tree["images"]}) == 1
    assert _extract_glb_image_bytes(tree, bin_chunk, 0) == orig_tex_bytes

    factors = [
        m["pbrMetallicRoughness"].get("baseColorFactor", [1.0, 1.0, 1.0, 1.0])
        for m in tree["materials"]
        if "baseColorTexture" in m.get("pbrMetallicRoughness", {})
    ]
    expected_factors = [
        [1.0, 1.0, 1.0, 1.0],
        [30 / 255, 160 / 255, 60 / 255, 1.0],
        [200 / 255, 90 / 255, 20 / 255, 1.0],
        [10 / 255, 20 / 255, 230 / 255, 0.5],
    ]
    assert len(factors) == 4
    for exp in expected_factors:
        assert any(all(abs(a - b) < 1e-4 for a, b in zip(f, exp)) for f in factors)


class _SUColor(ctypes.Structure):
    _fields_ = [("red", c_ubyte), ("green", c_ubyte), ("blue", c_ubyte), ("alpha", c_ubyte)]


class _SUPoint3D(ctypes.Structure):
    _fields_ = [("x", c_double), ("y", c_double), ("z", c_double)]


@pytest.mark.skipif(
    not os.path.isfile(os.environ.get("SKETCHUP_SDK_DLL", DEFAULT_SDK_DLL)),
    reason="SketchUpAPI.dll not installed on this machine",
)
@pytest.mark.parametrize("exporter_mod", [glb, instanced_glb])
def test_pixel_comparison_against_sketchup_sdk_library(tmp_path, exporter_mod):
    """Compare exported GLB colorized pixels directly against SUTextureGetColorizedImageRep
    across shift, tint, greyscale, PNG with alpha, and JPEG."""
    PIL_Image = pytest.importorskip("PIL.Image")
    dll_path = os.environ.get("SKETCHUP_SDK_DLL", DEFAULT_SDK_DLL)
    su = ctypes.CDLL(dll_path)

    su.SUInitialize.argtypes = []
    su.SUTerminate.argtypes = []
    su.SUModelCreate.argtypes = [POINTER(c_void_p)]
    su.SUModelRelease.argtypes = [POINTER(c_void_p)]
    su.SUModelGetEntities.argtypes = [c_void_p, POINTER(c_void_p)]
    su.SUModelAddMaterials.argtypes = [c_void_p, c_size_t, POINTER(c_void_p)]
    su.SUModelSaveToFileWithVersion.argtypes = [c_void_p, c_char_p, c_int]
    su.SUEntitiesAddFaces.argtypes = [c_void_p, c_size_t, POINTER(c_void_p)]
    su.SULoopInputCreate.argtypes = [POINTER(c_void_p)]
    su.SULoopInputAddVertexIndex.argtypes = [c_void_p, c_size_t]
    su.SUFaceCreate.argtypes = [POINTER(c_void_p), POINTER(_SUPoint3D), POINTER(c_void_p)]
    su.SUFaceSetFrontMaterial.argtypes = [c_void_p, c_void_p]
    su.SUMaterialCreate.argtypes = [POINTER(c_void_p)]
    su.SUMaterialSetName.argtypes = [c_void_p, c_char_p]
    su.SUMaterialSetTexture.argtypes = [c_void_p, c_void_p]
    su.SUMaterialSetColor.argtypes = [c_void_p, POINTER(_SUColor)]
    su.SUMaterialSetType.argtypes = [c_void_p, c_int]
    su.SUMaterialSetColorizeType.argtypes = [c_void_p, c_int]
    su.SUTextureCreateFromFile.argtypes = [POINTER(c_void_p), c_char_p, c_double, c_double]
    su.SUTextureGetColorizedImageRep.argtypes = [c_void_p, POINTER(c_void_p)]
    su.SUImageRepCreate.argtypes = [POINTER(c_void_p)]
    su.SUImageRepRelease.argtypes = [POINTER(c_void_p)]
    su.SUImageRepSaveToFile.argtypes = [c_void_p, c_char_p]
    for fn in (
        su.SUModelCreate,
        su.SUModelRelease,
        su.SUModelGetEntities,
        su.SUModelAddMaterials,
        su.SUModelSaveToFileWithVersion,
        su.SUEntitiesAddFaces,
        su.SULoopInputCreate,
        su.SULoopInputAddVertexIndex,
        su.SUFaceCreate,
        su.SUFaceSetFrontMaterial,
        su.SUMaterialCreate,
        su.SUMaterialSetName,
        su.SUMaterialSetTexture,
        su.SUMaterialSetColor,
        su.SUMaterialSetType,
        su.SUMaterialSetColorizeType,
        su.SUTextureCreateFromFile,
        su.SUTextureGetColorizedImageRep,
        su.SUImageRepCreate,
        su.SUImageRepRelease,
        su.SUImageRepSaveToFile,
    ):
        fn.restype = c_int

    xs, ys = np.meshgrid(np.arange(16), np.arange(16))
    rgb_arr = np.stack(
        [
            ((xs * 17 + ys * 5) % 256).astype(np.uint8),
            ((ys * 15 + 30) % 256).astype(np.uint8),
            (((15 - xs) * 14 + ys * 7) % 256).astype(np.uint8),
        ],
        axis=-1,
    )
    rgb_png = tmp_path / "rgb.png"
    PIL_Image.fromarray(rgb_arr, "RGB").save(rgb_png, format="PNG")

    grey_arr = ((xs * 16 + ys) % 256).astype(np.uint8)
    grey_png = tmp_path / "grey.png"
    PIL_Image.fromarray(grey_arr, "L").save(grey_png, format="PNG")

    alpha_vals = np.where(xs < 4, 0, np.where(xs < 8, 64, np.where(xs < 12, 180, 255))).astype(
        np.uint8
    )
    rgba_arr = np.dstack([rgb_arr, alpha_vals])
    rgba_png = tmp_path / "rgba_alpha.png"
    PIL_Image.fromarray(rgba_arr, "RGBA").save(rgba_png, format="PNG")

    jpeg_path = tmp_path / "photo.jpg"
    PIL_Image.fromarray(rgb_arr, "RGB").save(jpeg_path, format="JPEG", quality=92)

    cases = [
        ("shift_rgb", rgb_png, 0, (200, 90, 20)),
        ("tint_rgb", rgb_png, 1, (30, 160, 60)),
        ("shift_greyscale", grey_png, 0, (25, 50, 210)),
        ("tint_greyscale", grey_png, 1, (200, 90, 20)),
        ("shift_png_alpha", rgba_png, 0, (70, 25, 65)),
        ("tint_png_alpha", rgba_png, 1, (140, 140, 140)),
        ("shift_jpeg", jpeg_path, 0, (235, 185, 200)),
        ("tint_jpeg", jpeg_path, 1, (30, 160, 60)),
    ]

    su.SUInitialize()
    try:
        model = c_void_p()
        assert su.SUModelCreate(byref(model)) == 0
        try:
            ents = c_void_p()
            assert su.SUModelGetEntities(model, byref(ents)) == 0
            sdk_oracle_by_case: Dict[str, np.ndarray] = {}

            for idx, (label, img_path, ctype, trgb) in enumerate(cases):
                mat = c_void_p()
                assert su.SUMaterialCreate(byref(mat)) == 0
                assert su.SUMaterialSetName(mat, label.encode("utf-8")) == 0
                tex = c_void_p()
                assert su.SUTextureCreateFromFile(byref(tex), str(img_path).encode("utf-8"), 1.0, 1.0) == 0
                assert su.SUMaterialSetTexture(mat, tex) == 0
                assert su.SUModelAddMaterials(model, 1, byref(mat)) == 0
                col = _SUColor(trgb[0], trgb[1], trgb[2], 255)
                assert su.SUMaterialSetColor(mat, byref(col)) == 0
                assert su.SUMaterialSetType(mat, 2) == 0
                # SUMaterialSetColor resets colorize_type to Tint (1), so set mode last.
                assert su.SUMaterialSetColorizeType(mat, ctype) == 0

                rep = c_void_p()
                assert su.SUImageRepCreate(byref(rep)) == 0
                assert su.SUTextureGetColorizedImageRep(tex, byref(rep)) == 0
                oracle_png = tmp_path / f"oracle_{label}.png"
                assert su.SUImageRepSaveToFile(rep, str(oracle_png).encode("utf-8")) == 0
                assert su.SUImageRepRelease(byref(rep)) == 0
                with PIL_Image.open(oracle_png) as oim:
                    sdk_oracle_by_case[label] = np.asarray(oim.convert("RGBA"), dtype=np.uint8)

                x0 = float(idx * 4)
                pts = (_SUPoint3D * 4)(
                    _SUPoint3D(x0, 0.0, 0.0),
                    _SUPoint3D(x0 + 2.0, 0.0, 0.0),
                    _SUPoint3D(x0 + 2.0, 2.0, 0.0),
                    _SUPoint3D(x0, 2.0, 0.0),
                )
                loop = c_void_p()
                assert su.SULoopInputCreate(byref(loop)) == 0
                for vi in range(4):
                    assert su.SULoopInputAddVertexIndex(loop, vi) == 0
                face = c_void_p()
                assert su.SUFaceCreate(byref(face), pts, byref(loop)) == 0
                assert su.SUFaceSetFrontMaterial(face, mat) == 0
                assert su.SUEntitiesAddFaces(ents, 1, byref(face)) == 0

            for su_ver_name, su_ver_code in (("2017", 10), ("2021", 14)):
                skp_out = tmp_path / f"sdk_colorize_{su_ver_name}.skp"
                assert su.SUModelSaveToFileWithVersion(model, str(skp_out).encode("utf-8"), su_ver_code) == 0

                skp = SkpFile.open(str(skp_out))
                skp.parse()
                glb_out = tmp_path / f"exported_{su_ver_name}_{exporter_mod.__name__.rsplit('.', 1)[-1]}.glb"
                exporter_mod.export(skp, str(glb_out), textures=True)

                tree, bin_chunk = _read_glb(glb_out)
                glb_images = []
                for img_i, img_meta in enumerate(tree["images"]):
                    raw = _extract_glb_image_bytes(tree, bin_chunk, img_i)
                    with PIL_Image.open(io.BytesIO(raw)) as gim:
                        glb_images.append(
                            (img_meta["mimeType"], np.asarray(gim.convert("RGBA"), dtype=np.uint8))
                        )

                assert len(glb_images) == len(cases)
                for label, img_path, ctype, trgb in cases:
                    oracle_rgba = sdk_oracle_by_case[label]
                    is_jpeg = label.endswith("_jpeg")
                    expected_mime = "image/jpeg" if is_jpeg else "image/png"
                    if is_jpeg:
                        with PIL_Image.open(img_path) as src_im:
                            src_rgba = np.asarray(src_im.convert("RGBA"), dtype=np.uint8)
                        pre_jpeg = glb._colorize_rgba_u8(src_rgba, trgb, ctype)
                        pre_diff = int(
                            np.max(np.abs(pre_jpeg[..., :3].astype(int) - oracle_rgba[..., :3].astype(int)))
                        )
                        assert pre_diff <= 1, f"{su_ver_name} {label}: pre-JPEG diff {pre_diff} > 1"

                    best_rgb_diff = min(
                        int(np.max(np.abs(g[..., :3].astype(int) - oracle_rgba[..., :3].astype(int))))
                        for mime, g in glb_images
                        if mime == expected_mime
                        and g.shape == oracle_rgba.shape
                        and np.array_equal(g[..., 3], oracle_rgba[..., 3])
                    )
                    limit = 22 if is_jpeg else 1
                    assert best_rgb_diff <= limit, (
                        f"{su_ver_name} {label}: max per-channel diff {best_rgb_diff} > {limit}"
                    )
        finally:
            su.SUModelRelease(byref(model))
    finally:
        su.SUTerminate()
