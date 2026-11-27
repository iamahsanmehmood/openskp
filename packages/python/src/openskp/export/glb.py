"""GLB/glTF 2.0 export for parsed SketchUp models.

Builds on :func:`openskp.scene.build_scene` - the same cross-language-parity
``Scene``/``GlbPrimitive`` baking step used by the TypeScript, Dart, C#, and
C++ ports' ``buildScene()``/``BuildScene()`` - then hands the resulting
primitives to trimesh purely for GLB binary serialization.

This module used to run its own, independently-implemented scene-baking
pass (duplicating ``scene.py``'s instantiation/triangulation/material logic
against a live ``trimesh.Scene`` instead of ``Scene``/``GlbPrimitive``
objects). That duplication had already drifted out of sync with the shared
implementation in two concrete ways: it had no recursion/cycle guard for
self-referencing component definitions (a real crash-on-malicious-input bug
that ``scene.py`` was patched against separately), and it never resolved
``back_material_id``/``uv_transform_back``, so faces painted with different
front/back materials rendered with the front color on both sides. Building
on ``scene.build_scene()`` directly means every fix made there - present and
future - reaches real ``.glb`` output automatically, with no separate
pipeline left to fall out of sync.

Example::

    from openskp import SkpFile
    from openskp.export import glb

    skp = SkpFile.open("model.skp")
    model = skp.parse()
    glb.export(skp, "output.glb")
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# glTF's meter convention is what scene.build_scene()'s GlbPrimitive
# positions are already expressed in (matching every other language's
# BuildScene()/buildScene()) - this module's own public contract has always
# been millimetres, though, so the conversion below undoes that scaling
# rather than changing what callers of export() actually receive.
_M_TO_MM = 1000.0


def _srgb_to_linear(c: float) -> float:
    """Convert one sRGB component in ``[0, 1]`` to linear light per IEC 61966-2-1."""
    if c <= 0.0:
        return 0.0
    if c >= 1.0:
        return 1.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _strip_jpeg_exif_app1(data: bytes) -> bytes:
    """Drop JPEG ``APP1`` (``0xFFE1``, Exif / XMP orientation) segments before ``SOS``
    without re-encoding. SketchUp ignores EXIF orientation when rendering textures
    while storing the original JPEG bytes in ``.skp``, whereas browsers apply it."""
    b = data if isinstance(data, bytes) else bytes(data)
    n = len(b)
    if n < 4 or b[0] != 0xFF or b[1] != 0xD8:
        return b

    drop_ranges: List[Tuple[int, int]] = []
    pos = 2
    while pos < n:
        if b[pos] != 0xFF:
            break
        marker_start = pos
        while pos < n and b[pos] == 0xFF:
            pos += 1
        if pos >= n:
            break
        marker = b[pos]
        pos += 1
        if marker in (0x00, 0xD9):
            break
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:
            continue
        if pos + 2 > n:
            break
        seg_len = (b[pos] << 8) | b[pos + 1]
        if seg_len < 2 or pos + seg_len > n:
            break
        seg_end = pos + seg_len
        if marker == 0xDA:  # Start of Scan: compressed entropy stream follows
            break
        if marker == 0xE1:  # APP1 (Exif / XMP)
            drop_ranges.append((marker_start, seg_end))
        pos = seg_end

    if not drop_ranges:
        return b

    chunks: List[bytes] = []
    cursor = 0
    for start, end in drop_ranges:
        if start > cursor:
            chunks.append(b[cursor:start])
        cursor = end
    if cursor < n:
        chunks.append(b[cursor:])
    return b"".join(chunks)


class _RawGltfImage:
    """Minimal encoded-image wrapper accepted by ``trimesh.exchange.gltf`` and
    ``PBRMaterial.__hash__`` without requiring Pillow."""

    __slots__ = ("_data", "format")

    def __init__(self, data: bytes, mime_type: str, *, strip_exif: bool = True) -> None:
        raw = data if isinstance(data, bytes) else bytes(data)
        if mime_type == "image/jpeg" and strip_exif:
            raw = _strip_jpeg_exif_app1(raw)
        self._data = raw
        self.format = "JPEG" if mime_type == "image/jpeg" else "PNG"

    def save(self, fp, format=None) -> None:  # noqa: A002
        fp.write(self._data)

    def tobytes(self) -> bytes:
        return self._data

    def __array__(self, dtype=None, copy=None):
        import numpy as np

        arr = np.frombuffer(self._data, dtype=np.uint8)
        if dtype is not None and arr.dtype != dtype:
            return arr.astype(dtype)
        return arr.copy() if copy else arr


_MAX_COLORIZE_BLOCK_PIXELS = 262_144


def _colorize_image_u8(
    rgba_u8,
    target_rgb: Tuple[int, int, int],
    colorize_type: int,
    *,
    keep_alpha: bool = True,
    max_block_pixels: int = _MAX_COLORIZE_BLOCK_PIXELS,
):
    """Apply SketchUp's HLS colorize rules (``colorize_type`` 0 = shift, 1 = tint)
    in ``float32`` row blocks of at most *max_block_pixels*, computing the whole-image
    channel averages first so memory stays bounded on 4K+ textures while matching
    ``SUTextureGetColorizedImageRep`` within ``1/255``."""
    import colorsys
    import numpy as np

    arr = np.asarray(rgba_u8, dtype=np.uint8)
    out_channels = 4 if keep_alpha else 3
    if arr.size == 0:
        return arr[..., :out_channels].copy()

    h_px, w_px = arr.shape[:2]
    n_px = h_px * w_px
    rgb_u8 = arr[..., :3]
    # SUTextureGetAverageColor truncates each channel's mean over all pixels to uint8.
    avg_r = (int(rgb_u8[..., 0].sum(dtype=np.uint64)) // n_px) / 255.0
    avg_g = (int(rgb_u8[..., 1].sum(dtype=np.uint64)) // n_px) / 255.0
    avg_b = (int(rgb_u8[..., 2].sum(dtype=np.uint64)) // n_px) / 255.0
    avg_h, avg_l, avg_s = colorsys.rgb_to_hls(avg_r, avg_g, avg_b)
    tgt_h, tgt_l, tgt_s = colorsys.rgb_to_hls(
        target_rgb[0] / 255.0, target_rgb[1] / 255.0, target_rgb[2] / 255.0
    )

    f32 = np.float32
    is_shift = int(colorize_type or 0) == 0
    delta_h = f32(tgt_h - avg_h)
    delta_l = f32(tgt_l - avg_l)
    # SUMaterialGetColorizeDeltas sets delta_s = -1.0 when desaturating a chromatic
    # texture toward a greyscale target (tgt_s == 0 and avg_s > 0).
    delta_s = f32(-1.0 if (tgt_s == 0.0 and avg_s > 0.0) else (tgt_s - avg_s))
    tgt_h_f = f32(tgt_h)
    one_sixth = f32(1.0 / 6.0)
    one_third = f32(1.0 / 3.0)
    half = f32(0.5)
    one = f32(1.0)
    two = f32(2.0)
    four = f32(4.0)
    six = f32(6.0)
    min_l = f32(0.01)
    undef_h = f32(-1.0 / 360.0)
    eps = f32(1e-6)
    scale_255 = f32(255.0)
    zero = f32(0.0)

    out = np.empty((h_px, w_px, out_channels), dtype=np.uint8)
    block_rows = min(h_px, max(1, int(max_block_pixels) // max(1, w_px)))
    bshape = (block_rows, w_px)

    r_buf = np.empty(bshape, dtype=f32)
    g_buf = np.empty(bshape, dtype=f32)
    b_buf = np.empty(bshape, dtype=f32)
    cmax = np.empty(bshape, dtype=f32)
    cmin = np.empty(bshape, dtype=f32)
    rng = np.empty(bshape, dtype=f32)
    light = np.empty(bshape, dtype=f32)
    sat = np.empty(bshape, dtype=f32)
    nh = np.empty(bshape, dtype=f32)
    m1 = np.empty(bshape, dtype=f32)
    m2 = np.empty(bshape, dtype=f32)
    tmp1 = np.empty(bshape, dtype=f32)
    tmp2 = np.empty(bshape, dtype=f32)
    cond = np.empty(bshape, dtype=bool)
    nz = np.empty(bshape, dtype=bool)

    for y0 in range(0, h_px, block_rows):
        y1 = min(h_px, y0 + block_rows)
        nrows = y1 - y0
        if nrows == block_rows:
            r, g, b = r_buf, g_buf, b_buf
            cm, cn, rg, lt, st, nh_v, m1_v, m2_v, t1, t2, cd, nz_v = (
                cmax, cmin, rng, light, sat, nh, m1, m2, tmp1, tmp2, cond, nz
            )
        else:
            r, g, b = r_buf[:nrows], g_buf[:nrows], b_buf[:nrows]
            cm, cn, rg, lt, st, nh_v, m1_v, m2_v, t1, t2, cd, nz_v = (
                cmax[:nrows], cmin[:nrows], rng[:nrows], light[:nrows], sat[:nrows],
                nh[:nrows], m1[:nrows], m2[:nrows], tmp1[:nrows], tmp2[:nrows],
                cond[:nrows], nz[:nrows],
            )

        sub = arr[y0:y1]
        np.divide(sub[..., 0], scale_255, out=r, casting="unsafe")
        np.divide(sub[..., 1], scale_255, out=g, casting="unsafe")
        np.divide(sub[..., 2], scale_255, out=b, casting="unsafe")

        np.maximum(r, g, out=cm)
        np.maximum(cm, b, out=cm)
        np.minimum(r, g, out=cn)
        np.minimum(cn, b, out=cn)
        np.subtract(cm, cn, out=rg)

        np.add(cm, cn, out=lt)
        np.multiply(lt, half, out=lt)

        np.greater(rg, eps, out=nz_v)
        np.add(cm, cn, out=t1)
        np.subtract(two, t1, out=t2)
        np.less_equal(lt, half, out=cd)
        np.copyto(t1, t2, where=~cd)
        np.copyto(t1, one, where=~nz_v)
        np.divide(rg, t1, out=st)
        st[~nz_v] = zero

        if is_shift:
            # SketchUp's RGB_to_HLS assigns H = -1 deg (-1/360 turn) to achromatic pixels.
            np.copyto(t1, rg)
            t1[~nz_v] = one
            np.subtract(r, g, out=nh_v)
            np.divide(nh_v, t1, out=nh_v)
            np.add(nh_v, four, out=nh_v)

            np.subtract(b, r, out=t2)
            np.divide(t2, t1, out=t2)
            np.add(t2, two, out=t2)
            np.equal(g, cm, out=cd)
            np.copyto(nh_v, t2, where=cd)

            np.subtract(g, b, out=t2)
            np.divide(t2, t1, out=t2)
            np.equal(r, cm, out=cd)
            np.copyto(nh_v, t2, where=cd)

            np.multiply(nh_v, one_sixth, out=nh_v)
            np.floor(nh_v, out=t2)
            np.subtract(nh_v, t2, out=nh_v)
            nh_v[~nz_v] = undef_h
            np.add(nh_v, delta_h, out=nh_v)
            np.floor(nh_v, out=t2)
            np.subtract(nh_v, t2, out=nh_v)
        else:
            nh_v.fill(tgt_h_f)

        np.add(lt, delta_l, out=lt)
        np.clip(lt, min_l, one, out=lt)
        np.add(st, delta_s, out=st)
        np.clip(st, zero, one, out=st)

        # Use nl + ns * (1 - nl) for nl > 0.5 so 1 - nl is exact in float32 (Sterbenz's lemma).
        np.multiply(lt, st, out=t1)
        np.add(lt, t1, out=m2_v)
        np.subtract(one, lt, out=t2)
        np.multiply(st, t2, out=t2)
        np.add(lt, t2, out=t2)
        np.greater(lt, half, out=cd)
        np.copyto(m2_v, t2, where=cd)

        np.multiply(lt, two, out=m1_v)
        np.subtract(m1_v, m2_v, out=m1_v)
        np.subtract(m2_v, m1_v, out=rg)  # span = m2 - m1
        np.less_equal(st, eps, out=nz_v)  # achromatic mask

        for ch_idx, h_off in ((0, one_third), (1, zero), (2, -one_third)):
            if h_off != zero:
                np.add(nh_v, h_off, out=t1)
                np.floor(t1, out=t2)
                np.subtract(t1, t2, out=t1)
            else:
                np.copyto(t1, nh_v)
            np.multiply(t1, six, out=t1)
            np.subtract(t1, two, out=t1)
            np.abs(t1, out=t1)
            np.subtract(two, t1, out=t1)
            np.clip(t1, zero, one, out=t1)
            np.equal(t1, one, out=cd)
            np.multiply(t1, rg, out=t1)
            np.add(t1, m1_v, out=t1)
            np.copyto(t1, m2_v, where=cd)
            np.copyto(t1, lt, where=nz_v)
            np.multiply(t1, scale_255, out=t1)
            np.clip(t1, zero, scale_255, out=t1)
            out[y0:y1, :, ch_idx] = t1

        if keep_alpha:
            out[y0:y1, :, 3] = sub[..., 3]

    return out


def _colorize_rgba_u8(
    rgba_u8,
    target_rgb: Tuple[int, int, int],
    colorize_type: int,
    *,
    max_block_pixels: int = _MAX_COLORIZE_BLOCK_PIXELS,
):
    """Apply SketchUp's HLS colorize rules (``colorize_type`` 0 = shift, 1 = tint)
    to an ``(H, W, 4)`` ``uint8`` RGBA array, matching ``SUTextureGetColorizedImageRep``."""
    return _colorize_image_u8(
        rgba_u8, target_rgb, colorize_type, keep_alpha=True, max_block_pixels=max_block_pixels
    )


def _encode_baked_pil(pil_image_mod, pixels_u8, output_mime: str) -> bytes:
    import io

    buf = io.BytesIO()
    if output_mime == "image/jpeg":
        pil_image_mod.fromarray(pixels_u8, "RGB").save(
            buf, format="JPEG", quality=95, subsampling=0
        )
    elif pixels_u8.shape[2] == 3:
        pil_image_mod.fromarray(pixels_u8, "RGB").save(buf, format="PNG")
    else:
        pil_image_mod.fromarray(pixels_u8, "RGBA").save(buf, format="PNG")
    return buf.getvalue()


class _GltfMaterialCache:
    """Per-export cache building one ``PBRMaterial`` per scene material index and
    baking colorized textures once per unique ``(texture, colorize_type, target_rgb)``."""

    def __init__(
        self,
        materials: List[Dict[str, Any]],
        scene_textures: List[Any],
        embed_textures: bool,
        log: logging.Logger = logger,
    ) -> None:
        self._materials = materials
        self._scene_textures = scene_textures
        self._embed_textures = embed_textures
        self._log = log
        self._pbr_by_mat_idx: Dict[int, Any] = {}
        self._rgba_by_tex_idx: Dict[int, Any] = {}
        self._image_by_key: Dict[Tuple[Any, ...], Optional[_RawGltfImage]] = {}
        self._pil_checked = False
        self._pil_image: Any = None
        self._warned_no_pillow = False

    def _get_pil_image(self):
        if not self._pil_checked:
            self._pil_checked = True
            try:
                from PIL import Image

                self._pil_image = Image
            except ImportError:
                self._pil_image = None
        if self._pil_image is None and not self._warned_no_pillow:
            self._warned_no_pillow = True
            self._log.warning(
                "Pillow is not installed; colorized textures in GLB export stay untinted "
                "with their material factor (install pillow to bake colorized textures)."
            )
        return self._pil_image

    def _decode_tex_rgba(self, tex_idx: int):
        if tex_idx in self._rgba_by_tex_idx:
            return self._rgba_by_tex_idx[tex_idx]
        pil_image = self._get_pil_image()
        if pil_image is None:
            return None
        import io
        import numpy as np

        tex = self._scene_textures[tex_idx]
        try:
            with pil_image.open(io.BytesIO(tex.data)) as im:
                rgba = np.asarray(im.convert("RGBA"), dtype=np.uint8)
        except Exception as exc:
            self._log.warning(
                "Failed to decode texture %d (%r) for GLB export: %s",
                tex_idx,
                getattr(tex, "filename", ""),
                exc,
            )
            rgba = None
        self._rgba_by_tex_idx[tex_idx] = rgba
        return rgba

    def _resolve_texture(
        self,
        tex_idx: int,
        is_colorized: bool,
        colorize_type: int,
        target_rgb: Tuple[int, int, int],
    ) -> Tuple[Optional[_RawGltfImage], bool]:
        tex = self._scene_textures[tex_idx]
        is_gltf_native = tex.mime_type in ("image/png", "image/jpeg")

        if not is_colorized:
            key: Tuple[Any, ...] = (tex_idx, False)
            if key in self._image_by_key:
                return self._image_by_key[key], False
            if is_gltf_native:
                img: Optional[_RawGltfImage] = _RawGltfImage(tex.data, tex.mime_type)
            else:
                rgba = self._decode_tex_rgba(tex_idx)
                if rgba is None:
                    img = None
                else:
                    import numpy as np

                    has_alpha = bool(np.any(rgba[..., 3] < 255))
                    pixels = rgba if has_alpha else np.ascontiguousarray(rgba[..., :3])
                    png_bytes = _encode_baked_pil(self._pil_image, pixels, "image/png")
                    img = _RawGltfImage(png_bytes, "image/png", strip_exif=False)
            self._image_by_key[key] = img
            return img, False

        pil_image = self._get_pil_image()
        if pil_image is None:
            key = (tex_idx, False)
            if key not in self._image_by_key:
                self._image_by_key[key] = (
                    _RawGltfImage(tex.data, tex.mime_type) if is_gltf_native else None
                )
            return self._image_by_key[key], False

        ckey: Tuple[Any, ...] = (tex_idx, True, colorize_type, target_rgb)
        if ckey in self._image_by_key:
            cached = self._image_by_key[ckey]
            return cached, cached is not None

        rgba = self._decode_tex_rgba(tex_idx)
        if rgba is None:
            self._image_by_key[ckey] = None
            return None, False

        import numpy as np

        output_mime = "image/jpeg" if tex.mime_type == "image/jpeg" else "image/png"
        keep_alpha = output_mime == "image/png" and bool(np.any(rgba[..., 3] < 255))
        baked = _colorize_image_u8(rgba, target_rgb, colorize_type, keep_alpha=keep_alpha)
        encoded = _encode_baked_pil(pil_image, baked, output_mime)
        img = _RawGltfImage(encoded, output_mime, strip_exif=False)
        self._image_by_key[ckey] = img
        return img, True

    def get(self, material_index: int):
        cached = self._pbr_by_mat_idx.get(material_index)
        if cached is not None:
            return cached

        import numpy as np
        import trimesh

        mat = self._materials[material_index]
        pbr = mat["pbrMetallicRoughness"]
        raw_factor = pbr["baseColorFactor"]
        sketchup_extras = mat.get("extras", {}).get("sketchup", {})
        is_colorized = bool(sketchup_extras.get("colorized"))
        colorize_type = int(sketchup_extras.get("colorize_type", 0) or 0)

        base_color_texture = None
        colorize_baked = False
        tex_ref = pbr.get("baseColorTexture") if self._embed_textures else None
        if tex_ref is not None:
            target_rgb = (
                int(max(0, min(255, round(raw_factor[0] * 255.0)))),
                int(max(0, min(255, round(raw_factor[1] * 255.0)))),
                int(max(0, min(255, round(raw_factor[2] * 255.0)))),
            )
            base_color_texture, colorize_baked = self._resolve_texture(
                tex_ref["index"], is_colorized, colorize_type, target_rgb
            )

        if base_color_texture is None:
            base_color_factor = [
                _srgb_to_linear(raw_factor[0]),
                _srgb_to_linear(raw_factor[1]),
                _srgb_to_linear(raw_factor[2]),
                raw_factor[3],
            ]
        elif is_colorized and not colorize_baked:
            base_color_factor = list(raw_factor)
        else:
            base_color_factor = [1.0, 1.0, 1.0, raw_factor[3]]

        mat_name = mat.get("name")
        pbr_material = trimesh.visual.material.PBRMaterial(
            name=mat_name if isinstance(mat_name, str) and mat_name else None,
            baseColorTexture=base_color_texture,
            metallicFactor=pbr["metallicFactor"],
            roughnessFactor=pbr["roughnessFactor"],
            doubleSided=mat.get("doubleSided", False),
        )
        # PBRMaterial's baseColorFactor setter calls trimesh.visual.color.to_rgba()
        # with dtype=uint8, which quantizes linear floats onto a 1/255 grid (for
        # example sRGB 84 -> linear 0.08866 -> 23/255 = 0.09020, and sRGB <= 5 -> 0).
        # Storing float64 directly lets trimesh's glTF serializer (color.to_float)
        # write the exact IEEE-754 linear values.
        if hasattr(pbr_material, "_data"):
            pbr_material._data["baseColorFactor"] = np.asarray(
                base_color_factor, dtype=np.float64
            )
        else:
            pbr_material.baseColorFactor = base_color_factor

        self._pbr_by_mat_idx[material_index] = pbr_material
        return pbr_material


def _primitive_to_trimesh(
    prim,
    materials: List[Dict[str, Any]],
    scene_textures: List[Any],
    embed_textures: bool,
    material_cache: Optional[_GltfMaterialCache] = None,
):
    import trimesh

    n_verts = len(prim.positions) // 3
    vertices = [
        (
            prim.positions[i * 3] * _M_TO_MM,
            prim.positions[i * 3 + 1] * _M_TO_MM,
            prim.positions[i * 3 + 2] * _M_TO_MM,
        )
        for i in range(n_verts)
    ]
    normals = [
        (prim.normals[i * 3], prim.normals[i * 3 + 1], prim.normals[i * 3 + 2])
        for i in range(n_verts)
    ]
    uvs = [(prim.uvs[i * 2], prim.uvs[i * 2 + 1]) for i in range(n_verts)]
    faces = [
        (prim.indices[i * 3], prim.indices[i * 3 + 1], prim.indices[i * 3 + 2])
        for i in range(len(prim.indices) // 3)
    ]

    # process=False: trimesh's default post-construction cleanup merges/
    # drops vertices by position alone, which would misalign the UV/normal
    # arrays below (built 1:1 against `vertices`) and silently undo the
    # UV-based vertex splitting scene.build_scene() already did.
    mesh = trimesh.Trimesh(
        vertices=vertices, faces=faces, vertex_normals=normals, process=False,
    )

    if material_cache is None:
        material_cache = _GltfMaterialCache(materials, scene_textures, embed_textures)
    pbr_material = material_cache.get(prim.material_index)
    mesh.visual = trimesh.visual.TextureVisuals(uv=uvs, material=pbr_material)
    return mesh


def _instance_node_to_dict(node) -> Dict[str, Any]:
    return {
        "name": node.name,
        "definition_name": node.definition_name,
        "layer": node.layer,
        "position_mm": list(node.position_mm),
        "properties": dict(node.properties),
        # Every OTHER attribute dictionary this instance carries (e.g. a
        # third-party plugin's own named dictionary) - already correctly
        # resolved by build_scene(), but previously never reached this
        # metadata JSON at all, silently hiding it from any consumer that
        # reads mesh_index/scene_hierarchy instead of the derived .ifc.
        "attribute_dictionaries": {k: dict(v) for k, v in node.attribute_dictionaries.items()},
        "children": [_instance_node_to_dict(c) for c in node.children],
    }


def _json_safe_material(mat: Dict[str, Any]) -> Dict[str, Any]:
    # Raw texture image bytes aren't JSON-serializable and don't belong in
    # a text metadata sidecar anyway - the .glb file itself carries the
    # actual texture data.
    safe = dict(mat)
    tex = safe.get("texture")
    if tex is not None:
        safe_tex = dict(tex)
        safe_tex.pop("data", None)
        safe["texture"] = safe_tex
    return safe


def export(
    skp_file,
    output_path: str,
    *,
    coordinate_system: str = "y-up",
    units: str = "mm",
    textures: bool = False,
    unpainted_faces: str = "style",
) -> str:
    """Export a parsed SkpFile to GLB (binary glTF 2.0) format.

    Bakes the scene via :func:`openskp.scene.build_scene` (the same
    baking step :meth:`SkpFile.build_scene` exposes directly), then
    serializes the resulting primitives to a GLB file using trimesh.

    Args:
        skp_file: An :class:`~openskp.model.SkpFile` instance that has
            already been parsed (i.e., ``skp_file.parse()`` has been called).
        output_path: Filesystem path for the output ``.glb`` file.
        coordinate_system: Target coordinate system. Currently only
            ``"y-up"`` (glTF standard) is supported.
        units: Target unit system. Currently only ``"mm"`` is supported.
        textures: Embed the scene's texture images in the GLB, so each
            textured material's ``baseColorTexture`` points at real image
            data instead of just a resolved color. Off by default:
            photographic textures can multiply the file size, and the
            geometry alone is what most callers are after. Uses Pillow
            (``pip install pillow``) when installed to bake SketchUp
            colorized textures and re-encode non-PNG/JPEG textures;
            without Pillow, plain PNG/JPEG textures still embed and
            colorized textures stay untinted with their material factor
            (logging one warning).
        unpainted_faces: ``"style"`` (the default) shows faces with no paint
            in the model's front and back face colours, as SketchUp does;
            ``"tag"`` gives them the colour of their tag. See
            :meth:`openskp.SkpFile.build_scene`.

    Returns:
        Absolute path to the written GLB file.

    Raises:
        RuntimeError: If *skp_file* has not been parsed yet.
        NotImplementedError: If *coordinate_system* or *units* request
            anything other than the only values currently implemented
            (``"y-up"`` / ``"mm"``). The underlying conversion (glTF
            y-up, millimetres) is hardcoded in the scene builder, so a
            caller requesting e.g. ``units="inches"`` would otherwise
            silently get millimetre output with no indication anything
            was ignored.
    """
    import trimesh

    from .. import scene as _scene_mod

    if skp_file._parsed is None:
        raise RuntimeError(
            "SkpFile must be parsed before exporting. Call skp_file.parse() first."
        )

    if coordinate_system != "y-up":
        raise NotImplementedError(
            f"coordinate_system={coordinate_system!r} is not implemented; "
            "only 'y-up' (glTF standard) is currently supported."
        )
    if units != "mm":
        raise NotImplementedError(
            f"units={units!r} is not implemented; only 'mm' is currently supported."
        )

    parsed = skp_file._parsed
    # Embedding textures decodes them with Pillow, which reads a TIFF, TGA or GIF
    # texture as well as a PNG or JPEG one, so keep those in the scene (it would
    # otherwise drop them) and let the GLB carry them re-encoded.
    scene_obj = _scene_mod.build_scene(
        parsed, original_textures=textures, unpainted_faces=unpainted_faces
    )

    trimesh_scene = trimesh.Scene()
    material_cache = _GltfMaterialCache(
        scene_obj.gltf_materials, scene_obj.textures, textures, log=logger
    )
    for prim in scene_obj.glb_primitives:
        mesh = _primitive_to_trimesh(
            prim, scene_obj.gltf_materials, scene_obj.textures, textures, material_cache
        )
        trimesh_scene.add_geometry(mesh, geom_name=prim.geom_name)

    output_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(output_dir, exist_ok=True)
    trimesh_scene.export(output_path, file_type="glb")

    stem = os.path.splitext(os.path.basename(output_path))[0]
    defs_dict = parsed["defs_dict"]
    layer_colors = parsed["layer_colors"]
    layers_list = [
        {"name": name, "color": {"r": c[0], "g": c[1], "b": c[2]}}
        for name, c in layer_colors.items()
    ]

    metadata = {
        "format_version": "1.0",
        "source_file": stem,
        "model_source": "sketchup",
        "sketchup_version": parsed["version"],
        "total_definitions": len(defs_dict) - 1,
        "total_meshes": len(scene_obj.mesh_index),
        "total_layers": len(layers_list),
        "layers": layers_list,
        "layer_hidden": dict(scene_obj.layer_hidden),
        "materials": [_json_safe_material(m) for m in parsed["materials"].values()],
        "mesh_index": {
            name: {
                "name": m.name,
                "definition_name": m.definition_name,
                "layer": m.layer,
                "position_mm": list(m.position_mm),
                "properties": dict(m.properties),
                "attribute_dictionaries": {k: dict(v) for k, v in m.attribute_dictionaries.items()},
                "path": m.path,
            }
            for name, m in scene_obj.mesh_index.items()
        },
        "scene_hierarchy": _instance_node_to_dict(scene_obj.scene_hierarchy),
    }

    json_path = os.path.join(output_dir, f"{stem}_metadata.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    if parsed.get("thumbnail_data"):
        thumb_path = os.path.join(output_dir, f"{stem}_thumbnail.png")
        with open(thumb_path, "wb") as f:
            f.write(parsed["thumbnail_data"])

    return os.path.abspath(output_path)
