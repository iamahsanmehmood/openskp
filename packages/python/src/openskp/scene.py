"""Scene baking: flatten a parsed file's placed instances into a
world-space, triangulated 3D scene ready for rendering or GLB export.

This is deliberately a *separate*, opt-in step from :func:`SkpFile.parse`.
Baking walks the entire placed scene graph - so a file that reuses a
handful of definitions across many thousands of instances can produce far
more data here than the file's raw (un-instanced) geometry. Keeping it
separate means a plain ``SkpFile.open(path).parse()`` never pays for this
heavier computation, matching the same design used by the TypeScript,
C#, and Dart ports (``buildScene()`` / ``BuildScene()`` there).

Ported from the TypeScript reference implementation
(``packages/typescript/src/model.ts``'s ``buildSceneFromParsed``), reusing
this package's own proven ``_core.py`` primitives (``transform_point``,
``multiply_matrices``, ``triangulate_face_3d``) rather than duplicating
them.
"""

from __future__ import annotations

import logging
import re
import time
from array import array
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import _core
from ._curves import (
    _chain_loose_edges as _chain_loose_edges,
    _prepare_definition_curves,
    _solve_run_arc,
)
from ._face_groups import (
    FaceGroupContext,
    build_local_face_groups,
    resolve_color,
    resolve_material,
    unpainted_style_colors,
)
from .errors import SkpParseError
from .model import Page, _convert_pages

logger = logging.getLogger("openskp.scene")

# Mirrors _core._PROGRESS_INTERVAL - counts placed instances (not
# definitions), since a handful of definitions can be instanced thousands
# of times and that's where scene-baking's own cost actually scales.
_PROGRESS_INTERVAL = 500

INCHES_TO_MM = 25.4
INCHES_TO_M = 0.0254

# See openskp.instanced_scene._is_generic_definition_name - same pattern,
# duplicated rather than cross-imported since instanced_scene.py already
# imports from this module and a name-resolution helper isn't worth a
# shared-utility module of its own for one regex.
_GENERIC_DEFINITION_NAME_RE = re.compile(r"^(?:Group|Component)\d*#\d+$")


def _is_generic_definition_name(name: str) -> bool:
    return bool(_GENERIC_DEFINITION_NAME_RE.match(name))


@dataclass
class InstanceNode:
    """One node in the baked, world-space instance tree."""

    name: str = ""
    # See openskp.instanced_scene.InstancedNode.name_is_generated - same
    # meaning: True when `name` is a fallback this project generated,
    # rather than a real name from the source file.
    name_is_generated: bool = False
    definition_name: str = ""
    layer: str = ""
    position_mm: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    properties: Dict[str, str] = field(default_factory=dict)
    # Every OTHER attribute dictionary this instance carries, keyed by the
    # dictionary's own name, values stringified the same way `properties`
    # already is - `properties` stays exactly SketchUp's own Dynamic
    # Components data (`dynamic_attributes`) for backward compatibility;
    # third-party plugins (BIM/steel-detailing tools, etc.) commonly
    # attach their own richer per-instance data under their own dictionary
    # name instead, which this project never surfaced before.
    attribute_dictionaries: Dict[str, Dict[str, str]] = field(default_factory=dict)
    # The attribute dictionaries of the DEFINITION this instance places,
    # every one by name (``dynamic_attributes`` included), values
    # stringified like `properties`. SketchUp's Dynamic Components keep a
    # component's attributes, formulas and options on the definition and
    # only the current values on each instance, so a reader needs both.
    # Shared by every placement of the same definition. Empty for classic
    # (pre-2021) files.
    definition_attribute_dictionaries: Dict[str, Dict[str, str]] = field(default_factory=dict)
    children: List["InstanceNode"] = field(default_factory=list)
    # Same full-path string that scoping instance's own meshes (and every
    # nested descendant's meshes) carry as MeshMetadata.path - lets a
    # consumer (e.g. export/ifc.py's assembly grouping) correlate a flat
    # GlbPrimitive back to its owning tree node by exact string match,
    # without re-deriving SketchUp's own name-resolution/override rules.
    path: str = ""


@dataclass
class MeshMetadata:
    """Metadata for one baked mesh, keyed the same as its GlbPrimitive's
    ``geom_name`` in :attr:`Scene.glb_primitives`."""

    name: str = ""
    definition_name: str = ""
    layer: str = ""
    position_mm: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    properties: Dict[str, str] = field(default_factory=dict)
    # See InstanceNode.attribute_dictionaries.
    attribute_dictionaries: Dict[str, Dict[str, str]] = field(default_factory=dict)
    path: str = ""


@dataclass
class GlbPrimitive:
    """One triangulated, world-space mesh: all faces (or, for a face whose
    front/back colors genuinely differ, all *one side* of those faces)
    sharing a single resolved color from one flattened scene-graph
    position. Ready to hand straight to a GLB/glTF exporter or any other
    renderer.

    Attributes:
        positions: Flat [x, y, z, x, y, z, ...] vertex positions, in
            metres, Y-up.
        normals: Flat [x, y, z, ...] vertex normals, matching *positions*
            1:1.
        uvs: Flat [u, v, u, v, ...] texture coordinates, matching
            *positions* 1:1. Computed from each source face's
            ``uv_transform`` (or the default face-plane projection when a
            face has none) - see ``Face.uv_transform`` in model.py for the
            formula. A vertex shared by two faces that disagree on UV is
            split, since indexed glTF meshes need position/normal/uv
            aligned per vertex. Faces with ``uv_projected`` set (terrain
            drape textures) still use the face-plane formula here, since
            the real projection-plane basis isn't captured in the parsed
            data - their UVs will be approximate.
        indices: Triangle vertex indices into *positions*/*normals*/*uvs*
            (3 per triangle).
        back_triangles: Triangle offsets for separately emitted back
            sides. A primitive can mix front and back triangles when they
            share a material. Their front counterparts are still present.
        material_index: Index into :attr:`Scene.gltf_materials` for this
            primitive's resolved color.
        geom_name: Matches the corresponding key in
            :attr:`Scene.mesh_index`.
        unpainted_back: ``True`` for the back copies of faces with no
            paint on either side (the front copies are in another
            primitive). They exist only to show the model's back face
            colour from behind; see :attr:`Scene.surface_primitives`.
    """

    positions: array
    normals: array
    uvs: array
    indices: array
    back_triangles: array = field(default_factory=lambda: array("I"), kw_only=True)
    material_index: int
    geom_name: str
    unpainted_back: bool = False


@dataclass
class SceneTexture:
    """One texture image referenced by :attr:`Scene.gltf_materials`.

    Attributes:
        data: The image file's raw bytes, exactly as stored in the .skp.
        mime_type: Sniffed from the bytes, not from ``filename`` -
            SketchUp records the authoring machine's path, whose
            extension can disagree with the content. ``image/png`` or
            ``image/jpeg``, the two formats glTF holds, unless the scene
            was built with ``original_textures=True``: then also the real
            type of a TIFF, GIF, BMP, TGA, WebP, PSD, DDS or JPEG 2000
            texture (``image/tiff``, ...), kept as SketchUp stores it.
        filename: Best-effort original filename, for diagnostics only.
    """

    data: bytes
    mime_type: str
    filename: str = ""


@dataclass
class CurveSetMetadata:
    """Metadata for one run of *loose* edges - edges that no face uses.

    Loose edges are how SketchUp stores drawing/construction geometry: a
    facade elevation, a section outline, a guide. They carry no faces, so
    before this existed the whole class of curve-only models baked to an
    empty scene (no ``glb_primitives`` at all) and exported to an IFC with a
    spatial skeleton and zero elements.

    Edges belonging to a face's loop are deliberately NOT here: they are
    already represented by that face's mesh, and emitting them again would
    double every solid's geometry as line work.
    """

    name: str = ""
    definition_name: str = ""
    layer: str = ""
    path: str = ""
    # True when ``name`` was synthesised by this module rather than read from
    # the file (root-level loose geometry has no name to read - see the
    # loose-edge block in instantiate()). Consumers that use a name as
    # *evidence* about the author's intent - ifc_classify - must not treat a
    # name this module made up as if the author had written it. Same idea as
    # scene.InstanceNode.name_is_generated.
    name_is_generated: bool = False
    # World-space, metres, in **glTF's Y-up frame** (y = height, z = -depth) -
    # byte-for-byte the same convention as GlbPrimitive.positions, so a
    # consumer can mix the two without re-deriving an axis swap. A consumer
    # that wants Z-up (e.g. the IFC exporter) applies the same conversion it
    # already applies to mesh positions.
    points_m: List[Tuple[float, float, float]] = field(default_factory=list)
    # True when the run returns to its own first point (the common case for
    # a closed outline). IFC's IfcPolyline has no implicit closure, so a
    # closed run is emitted with its first point repeated at the end.
    closed: bool = False
    # Set when this run provably *is* one whole
    # circular arc, in which case the run is emitted as an analytic arc
    # rather than as the chords the file happens to tessellate it into.
    # ``{"points": [(x, y, z), ...], "segments": [(0, 1, 2), ...]}`` - the
    # points are in the same world-space glTF-Y-up metre frame as
    # ``points_m`` (so a consumer applies the same axis swap), and the
    # segments are 0-based index tuples into them, ready to become IFC4
    # ``IfcArcIndex``/``IfcLineIndex`` entries.
    #
    # Only ever set from a *validated* frame: the file says the Curve is a
    # CArcCurve, the frame's own axes agree it is a circle (not the affine
    # image of one - see legacy._read_arccurve), and every vertex the file
    # stores on this run lies on the resulting circle. Anything short of
    # that leaves this None and the chords are emitted unchanged, which is
    # exactly what the file contains. See _solve_run_arc.
    arc: Optional[Dict[str, Any]] = None
    # Same shape and meaning as MeshMetadata.attribute_dictionaries: the IFC
    # exporter writes each entry out as its own named property set, which is
    # how an inference result (e.g. ifc_classify's AI_Classification) travels
    # downstream with the geometry instead of dying in this process.
    attribute_dictionaries: Dict[str, Dict[str, str]] = field(default_factory=dict)


@dataclass
class Scene:
    """The result of baking a parsed file's placed instances into a flat,
    world-space 3D scene."""

    scene_hierarchy: InstanceNode = field(default_factory=InstanceNode)
    mesh_index: Dict[str, MeshMetadata] = field(default_factory=dict)
    glb_primitives: List[GlbPrimitive] = field(default_factory=list)
    # Loose-edge runs, one entry per chained polyline. Empty for a model
    # whose geometry is all faces (the overwhelmingly common case).
    curve_sets: List[CurveSetMetadata] = field(default_factory=list)
    gltf_materials: List[Dict[str, Any]] = field(default_factory=list)
    # Distinct texture images the placed materials use, deduplicated by
    # source bytes. Empty when nothing placed in the scene is textured.
    # GLB export only embeds these when explicitly asked (export(...,
    # textures=True)) - most callers just want geometry, and photographic
    # textures can multiply file size.
    textures: List[SceneTexture] = field(default_factory=list)
    # Each layer's own visibility checkbox in SketchUp (the parsed file's
    # own "layer_hidden", keyed by layer name) - independent of whether any
    # placed geometry on that layer ended up in glb_primitives. Consumers
    # that expose a layer list (e.g. the IFC exporter's IfcPresentationLayerWithStyle.LayerOn)
    # use this to match SketchUp's own on/off state instead of always
    # defaulting every layer to visible.
    layer_hidden: Dict[str, bool] = field(default_factory=dict)
    unread_root_entities: int = 0
    # Saved scenes (SketchUp's "Scenes" tabs), identical to SkpModel.pages.
    # Camera eye/target/up and ortho_height stay in SketchUp's native Z-up
    # frame and inches (unlike glb_primitives, which are Y-up metres).
    pages: List[Page] = field(default_factory=list)

    @property
    def surface_primitives(self) -> List[GlbPrimitive]:
        """:attr:`glb_primitives` without the back copies of faces that have
        no paint on either side: what to use when the two sides of a surface
        aren't shown separately (printing, CAD and BIM formats, counting or
        measuring triangles). Every face is still there once."""
        return [p for p in self.glb_primitives if not p.unpainted_back]


def _sniff_image_mime(data: bytes) -> Optional[str]:
    """Identify an image's MIME type from its magic bytes. Returns ``None``
    for anything glTF cannot carry (glTF only allows PNG and JPEG)."""
    if len(data) >= 3 and data[0] == 0xff and data[1] == 0xd8 and data[2] == 0xff:
        return "image/jpeg"
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return None


def _describe_image_format(data: bytes, filename: str = "") -> str:
    """A short name for an image's format, for a message about a texture the
    scene cannot carry: from the bytes' own signature, else from the source
    file name's extension (TGA has no signature at the start), else a plain
    "unrecognized format"."""
    head = data[:16]
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "TIFF"
    if head[:2] == b"BM":
        return "BMP"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "GIF"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "WebP"
    if head[:4] == b"8BPS":
        return "PSD"
    if head[:4] == b"DDS ":
        return "DDS"
    if head[:8] == b"\x00\x00\x00\x0cjP  ":
        return "JPEG 2000"
    if b"TRUEVISION-XFILE" in data[-26:]:
        return "TGA"
    ext = filename.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[-1] if "." in filename else ""
    return ext.upper() if 0 < len(ext) <= 5 else "an unrecognized format"


# The real MIME type of an image format a scene cannot carry as glTF but a consumer
# may well read (Blender reads all of these), by the name _describe_image_format gives it.
_ORIGINAL_IMAGE_MIME = {
    "TIFF": "image/tiff",
    "GIF": "image/gif",
    "BMP": "image/bmp",
    "TGA": "image/x-tga",
    "WebP": "image/webp",
    "PSD": "image/vnd.adobe.photoshop",
    "DDS": "image/vnd.ms-dds",
    "JPEG 2000": "image/jp2",
}


def _original_image_mime(data: bytes) -> Optional[str]:
    """The MIME type of an image that is not PNG or JPEG, from its signature
    (or, for a TGA, its footer), or ``None`` when the format is not one of
    the known ones. A format named only by the source file's extension is not
    trusted: the bytes could be anything."""
    name = _describe_image_format(data)
    return _ORIGINAL_IMAGE_MIME.get(name)


def _warn_skipped_texture(
    log: logging.Logger,
    warned: set,
    material_name: Optional[str],
    tex: Dict[str, Any],
    data: bytes,
) -> None:
    """Say, once per material and format, that a texture the scene cannot
    carry (anything but PNG and JPEG) was left out, so the material is built
    without it. Before, the texture just vanished."""
    filename = tex.get("filename") or ""
    fmt = _describe_image_format(data, filename)
    if (material_name, fmt) in warned:
        return
    warned.add((material_name, fmt))
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    keepable = _original_image_mime(data) is not None
    log.warning(
        "Texture of material %r%s is %s, which a scene cannot carry (only PNG and JPEG): "
        "the material is built without its texture%s",
        material_name or "(unnamed)",
        f" ({base})" if base else "",
        fmt,
        " (pass original_textures=True to keep it in its own format)" if keepable else "",
    )


def build_scene(
    parsed: Dict[str, Any],
    name_override_keys: Sequence[str] = ("name", "label", "code"),
    include_curve_sets: bool = True,
    original_textures: bool = False,
    unpainted_faces: str = "style",
) -> Scene:
    """Bake every instance actually placed in ``parsed`` (the output of
    :func:`openskp._core.full_parse` / ``full_parse_legacy``) into
    world-space, triangulated mesh data.

    Args:
        parsed: Output of ``_core.full_parse()``. Callers normally get
            this by calling :meth:`SkpFile.parse` first is *not* required -
            :meth:`SkpFile.build_scene` re-runs the raw parse independently,
            so a plain ``parse()`` call never carries this cost.
        name_override_keys: Attribute-dictionary key names (checked in
            order, first match wins) that identify an instance's real,
            plugin-assigned name - tried across every non-boilerplate
            dictionary the instance carries, whichever third-party plugin
            (FrameBuilder, TechSteel, or any other SketchUp extension that
            attaches its own attribute dictionary) wrote it, not a specific
            plugin's own dictionary name. The default covers the common
            convention; pass your own tuple to also recognize a plugin
            that instead uses e.g. ``"mark"`` or ``"partNumber"``.
        include_curve_sets: Bake loose edges (edges no face uses) into
            :attr:`Scene.curve_sets` as polyline runs. On by default, and
            the only reason to turn it off is cost. Measured on this
            project's own corpus, exporting the same file both ways:
            sc_SourceCity_Facade (solids + curves) 50,662,265 -> 53,564,893
            bytes (+5.7%); magnetar_Facade_Drawing (curves only) 1,570 ->
            568,177 - that second one being the point: on a curve-only
            model the curve sets *are* the model, and turning them off
            leaves a spatial skeleton with nothing in it.
        original_textures: See :meth:`SkpFile.build_scene`.
        unpainted_faces: See :meth:`SkpFile.build_scene`.

    Returns:
        A populated :class:`Scene`.
    """
    t0 = time.monotonic()
    defs_dict = parsed["defs_dict"]
    layer_colors = parsed["layer_colors"]
    layer_id_to_name = parsed["layer_id_to_name"]
    material_id_to_name = parsed.get("material_id_to_name", {})
    materials = parsed["materials"]
    materials_by_folder = parsed.get("materials_by_folder", {})

    logger.info("Building scene: %d definitions available", len(defs_dict))

    # Each definition's own dictionaries, stringified once and shared by
    # every InstanceNode that places it (see
    # InstanceNode.definition_attribute_dictionaries).
    definition_dicts_cache: Dict[Any, Dict[str, Dict[str, str]]] = {}

    def _definition_dicts(ref_idx) -> Dict[str, Dict[str, str]]:
        cached = definition_dicts_cache.get(ref_idx)
        if cached is None:
            raw = (defs_dict.get(ref_idx) or {}).get("attribute_dictionaries") or {}
            cached = {
                name: {k: _core._stringify_vff_attr_value(v) for k, v in entries.items()}
                for name, entries in raw.items()
            }
            definition_dicts_cache[ref_idx] = cached
        return cached

    instance_counter = [0]
    mesh_counter = [0]
    mesh_index: Dict[str, MeshMetadata] = {}
    glb_primitives: List[GlbPrimitive] = []
    curve_sets: List[CurveSetMetadata] = []
    # Per-layer sequence numbers for generated curve-set labels (see the
    # loose-edge block in instantiate()).
    curve_seq: Dict[str, int] = {}
    prepared_curves_cache: Dict[Any, Any] = {}

    # Instance path -> (properties, name) updates, collected in O(1) per
    # instance and applied once after instantiation completes (see the
    # path-walk loop below), instead of scanning the entire mesh_index per
    # placed instance - an O(instances x meshes) substring scan that both
    # dominated build_scene on models with many placed instances and could
    # match the wrong meshes (a shallow instance's path is always a string
    # prefix of every deeper descendant's path too, so "in" matched far
    # more than intended - see openskp#240).
    path_updates: Dict[str, Tuple[Dict[str, str], str, Dict[str, Dict[str, str]]]] = {}

    # Textures deduplicated by full content: the same image routinely backs
    # several materials, while equal-size PNGs/TIFFs share their 16-byte
    # header and must stay distinct. Look up each bytes object by identity
    # first so per-face calls stay O(1) after the first sight.
    textures: List[SceneTexture] = []
    texture_index_by_id: Dict[int, int] = {}
    texture_index_by_bytes: Dict[bytes, int] = {}
    kept_data_refs: List[Any] = []
    skipped_textures: set = set()

    def texture_index_for(tex: Optional[Dict[str, Any]], material_name: Optional[str] = None) -> Optional[int]:
        if not tex:
            return None
        data = tex.get("data")
        if not data:
            return None
        hit = texture_index_by_id.get(id(data))
        if hit is not None:
            return hit
        mime_type = _sniff_image_mime(data)
        if mime_type is None and original_textures:
            mime_type = _original_image_mime(data)
        if mime_type is None:
            # a format glTF cannot carry: leave it out, but say so
            _warn_skipped_texture(logger, skipped_textures, material_name, tex, data)
            return None
        data_bytes = data if isinstance(data, bytes) else bytes(data)
        idx = texture_index_by_bytes.get(data_bytes)
        if idx is None:
            idx = len(textures)
            textures.append(SceneTexture(data=data_bytes, mime_type=mime_type, filename=tex.get("filename", "")))
            texture_index_by_bytes[data_bytes] = idx
        kept_data_refs.append(data)
        texture_index_by_id[id(data)] = idx
        return idx

    color_to_material_index: Dict[Tuple[Any, ...], int] = {}
    gltf_materials: List[Dict[str, Any]] = []

    # Definitions currently being instantiated on the active recursion
    # path (not "ever visited" - the same definition legitimately reused
    # by sibling instances is fine). Guards against a component that
    # directly or transitively instances itself, which would otherwise
    # recurse until the stack overflows.
    active_definitions: set = set()

    def get_layer_color(name: str) -> Tuple[int, int, int]:
        return layer_colors.get(name, (136, 136, 136))

    style_colors = unpainted_style_colors(parsed, unpainted_faces)

    def get_material_index(
        color: Tuple[int, int, int],
        double_sided: bool,
        texture_index: Optional[int],
        transparency: float = 1.0,
        material_name: Optional[str] = None,
        colorized: bool = False,
        colorize_type: int = 0,
    ) -> int:
        # The texture is part of the identity, not just the color: two
        # different images can average to the same RGB (real files do
        # this), and keying on color alone would merge them into one
        # material and lose one of the images. The name is too, so two
        # differently named SketchUp materials stay two materials.
        effective_colorized = bool(colorized) and texture_index is not None
        effective_colorize_type = int(colorize_type) if effective_colorized else 0
        key = (
            color,
            double_sided,
            texture_index,
            transparency,
            material_name,
            effective_colorized,
            effective_colorize_type,
        )
        if key in color_to_material_index:
            return color_to_material_index[key]
        idx = len(gltf_materials)
        r, g, b = color
        pbr: Dict[str, Any] = {
            "baseColorFactor": [r / 255, g / 255, b / 255, transparency],
            "metallicFactor": 0.0,
            "roughnessFactor": 0.8,
        }
        # baseColorFactor stays as the resolved color even with a texture
        # attached: glTF multiplies the two, and SketchUp's own colorized
        # materials rely on exactly that tint.
        if texture_index is not None:
            pbr["baseColorTexture"] = {"index": texture_index}
        mat_dict: Dict[str, Any] = {"pbrMetallicRoughness": pbr}
        if material_name:
            mat_dict["name"] = material_name
        if effective_colorized:
            # See openskp.instanced_scene's get_material_index.
            mat_dict["extras"] = {
                "sketchup": {"colorized": True, "colorize_type": effective_colorize_type}
            }
        if double_sided:
            mat_dict["doubleSided"] = True
        # glTF's default alphaMode is OPAQUE, which tells a conformant
        # renderer to ignore alpha entirely - both the material's own
        # opacity and any texture's alpha channel. Genuinely translucent
        # materials (glass, water) need BLEND so baseColorFactor's alpha
        # (and the texture's, if any) actually takes effect. A
        # textured-but-otherwise-opaque material gets MASK instead: many
        # SketchUp Warehouse assets (tree foliage, fences, signage) rely on
        # the image's own alpha channel to cut a shape out of an otherwise
        # flat quad, and without MASK a renderer would show the full
        # rectangle. MASK is a no-op for a texture with no real cutout - a
        # fully-opaque alpha channel (or none, as in JPEG) stays above the
        # cutoff everywhere - so this is safe to set unconditionally rather
        # than trying to detect which textures need it.
        if transparency < 1.0:
            mat_dict["alphaMode"] = "BLEND"
        elif texture_index is not None:
            mat_dict["alphaMode"] = "MASK"
        gltf_materials.append(mat_dict)
        color_to_material_index[key] = idx
        return idx

    face_layer_summary_cache: Dict[Any, Tuple[Optional[str], Optional[str]]] = {}

    def _face_layer_summary(def_id: Any) -> Tuple[Optional[str], Optional[str]]:
        hit = face_layer_summary_cache.get(def_id)
        if hit is not None or def_id in face_layer_summary_cache:
            return hit  # type: ignore[return-value]
        d = defs_dict.get(def_id)
        if d is None or not d["builder"].faces:
            res: Tuple[Optional[str], Optional[str]] = (None, None)
            face_layer_summary_cache[def_id] = res
            return res
        counts: Dict[Any, int] = {}
        all_same = True
        common_name: Optional[str] = None
        for f in d["builder"].faces.values():
            lid = f.get("layer")
            fname = layer_id_to_name.get(lid, "") if lid else ""
            if fname == "Layer0":
                fname = ""
            if fname:
                counts[lid] = counts.get(lid, 0) + 1
                if common_name is None:
                    common_name = fname
                elif fname != common_name:
                    all_same = False
            else:
                all_same = False
        uniform_layer = common_name if all_same else None
        majority_layer = (
            layer_id_to_name.get(max(counts, key=counts.get)) if counts else None
        )
        res = (uniform_layer, majority_layer)
        face_layer_summary_cache[def_id] = res
        return res

    def instantiate(
        def_id,
        current_matrix,
        parent_layer: str = "Layer0",
        path_name: str = "ROOT",
        inherited_color: Optional[Tuple[int, int, int]] = None,
        inherited_material: Optional[Dict[str, Any]] = None,
    ) -> List[InstanceNode]:
        d = defs_dict.get(def_id)
        if d is None:
            return []
        builder = d["builder"]
        uniform_layer, majority_layer = _face_layer_summary(def_id)
        mesh_layer = (
            (uniform_layer if uniform_layer is not None else parent_layer)
            if parent_layer == "Layer0"
            else parent_layer
        )

        if builder.faces:
            # An untagged (Layer0) placement's own mesh takes its faces' tag
            # only when every face of the definition has the same tag (other
            # than Layer0); tagged placements keep their own or inherited tag,
            # and child instances never inherit a face's tag. For
            # unpainted_faces="tag" and files without style face colours, the
            # majority face tag still supplies the unpainted fallback colour.
            if inherited_color is not None:
                fallback_color = fallback_back_color = inherited_color
            elif style_colors is not None:
                fallback_color, fallback_back_color = style_colors
            else:
                color_layer = majority_layer if majority_layer is not None else parent_layer
                fallback_color = fallback_back_color = get_layer_color(color_layer)
            face_groups = build_local_face_groups(
                builder,
                FaceGroupContext(
                    material_id_to_name=material_id_to_name,
                    materials=materials,
                    materials_by_folder=materials_by_folder,
                    texture_index_for=texture_index_for,
                    fallback_color=fallback_color,
                    definition_id=def_id,
                    fallback_material=inherited_material,
                    fallback_back_color=fallback_back_color,
                ),
            )

            for (
                face_color, double_sided, tex_index, transparency, material_name, unpainted_back
            ), group in face_groups.items():
                local_faces = group["local_faces"]
                if not local_faces:
                    continue

                is_root = path_name == "ROOT"
                tx = 0.0 if is_root else (current_matrix[9] if len(current_matrix) > 9 else 0.0) * INCHES_TO_MM
                ty = 0.0 if is_root else (current_matrix[10] if len(current_matrix) > 10 else 0.0) * INCHES_TO_MM
                tz = 0.0 if is_root else (current_matrix[11] if len(current_matrix) > 11 else 0.0) * INCHES_TO_MM

                safe_path = path_name.replace(" / ", "__").replace(" ", "_")[:80]
                color_suffix = (
                    f"_{face_color[0]}_{face_color[1]}_{face_color[2]}_{'ds' if double_sided else 'ss'}"
                    if len(face_groups) > 1 else ""
                )
                geom_name = f"mesh_{mesh_counter[0]}_{safe_path}_{mesh_layer}{color_suffix}"
                mesh_counter[0] += 1

                mesh_index[geom_name] = MeshMetadata(
                    name="ROOT" if is_root else (path_name.split(" / ")[-1] or ""),
                    definition_name=d.get("name") or "",
                    layer=mesh_layer,
                    position_mm=(round(tx, 2), round(ty, 2), round(tz, 2)),
                    properties={},
                    path=path_name,
                )

                local_verts = group["local_verts"]
                local_uvs = group["local_uvs"]
                positions = array("f", [0.0]) * (len(local_verts) * 3)
                normals = array("f", [0.0]) * (len(local_verts) * 3)
                uvs = array("f", [0.0]) * (len(local_verts) * 2)
                vertex_normals_accum = group["normals_accum"]

                for i, v in enumerate(local_verts):
                    pt = _core.transform_point(v, current_matrix)
                    positions[i * 3] = pt[0] * INCHES_TO_M
                    positions[i * 3 + 1] = pt[2] * INCHES_TO_M
                    positions[i * 3 + 2] = -pt[1] * INCHES_TO_M

                    uvs[i * 2] = local_uvs[i][0]
                    uvs[i * 2 + 1] = local_uvs[i][1]

                    raw_n = vertex_normals_accum[i]
                    norm_len = (raw_n[0] ** 2 + raw_n[1] ** 2 + raw_n[2] ** 2) ** 0.5
                    if norm_len > 1e-6:
                        n = (raw_n[0] / norm_len, raw_n[1] / norm_len, raw_n[2] / norm_len)
                    else:
                        n = (0.0, 0.0, 1.0)

                    nx = current_matrix[0] * n[0] + current_matrix[1] * n[1] + current_matrix[2] * n[2]
                    ny = current_matrix[3] * n[0] + current_matrix[4] * n[1] + current_matrix[5] * n[2]
                    nz = current_matrix[6] * n[0] + current_matrix[7] * n[1] + current_matrix[8] * n[2]
                    length = (nx * nx + ny * ny + nz * nz) ** 0.5
                    if length > 1e-6:
                        normals[i * 3] = nx / length
                        normals[i * 3 + 1] = nz / length
                        normals[i * 3 + 2] = -ny / length
                    else:
                        normals[i * 3] = 0.0
                        normals[i * 3 + 1] = 1.0
                        normals[i * 3 + 2] = 0.0

                indices = array("I", [0]) * (len(local_faces) * 3)
                for i, tri in enumerate(local_faces):
                    indices[i * 3] = tri[0]
                    indices[i * 3 + 1] = tri[1]
                    indices[i * 3 + 2] = tri[2]

                material_index = get_material_index(
                    face_color, double_sided, tex_index, transparency,
                    material_name, group["colorized"], group["colorize_type"],
                )
                glb_primitives.append(
                    GlbPrimitive(
                        positions=positions,
                        normals=normals,
                        uvs=uvs,
                        indices=indices,
                        back_triangles=group["back_triangles"],
                        material_index=material_index,
                        geom_name=geom_name,
                        unpainted_back=unpainted_back,
                    )
                )

        # Loose edges -> world-space polyline runs.
        #
        # Deliberately OUTSIDE the `if builder.faces:` branch above: a
        # curve-only model has no faces at all, which is exactly the case
        # this exists for. Before it, such a definition contributed nothing
        # to the scene, so export/ifc.py got an empty primitive list and
        # emitted a spatial skeleton with zero elements (see
        # magnetar_Facade_Drawing.skp: 1776 edges -> 0 products).
        if include_curve_sets:
            _cached_curves = prepared_curves_cache.get(def_id)
            if _cached_curves is None:
                _cached_curves = _prepare_definition_curves(builder, layer_id_to_name)
                prepared_curves_cache[def_id] = _cached_curves
            _prepared_runs, _curve_counts = _cached_curves
            if _prepared_runs:
                _fm = _core._fold_scale(current_matrix) if (current_matrix and len(current_matrix) >= 12) else None
                _def_name_str = d.get("name") or ""
                _cname_component = "" if path_name == "ROOT" else (path_name.split(" / ")[-1] or "")
                for _eids, _local_verts, _closed, _explicit_layer in _prepared_runs:
                    if _fm is None:
                        pts = [
                            (round(_v[0] * INCHES_TO_M, 6),
                             round(_v[2] * INCHES_TO_M, 6),
                             round(-_v[1] * INCHES_TO_M, 6))
                            for _v in _local_verts
                        ]
                    else:
                        _m0, _m1, _m2, _m3, _m4, _m5, _m6, _m7, _m8, _m9, _m10, _m11 = _fm[:12]
                        pts = [
                            (round((_m0 * _v[0] + _m1 * _v[1] + _m2 * _v[2] + _m9) * INCHES_TO_M, 6),
                             round((_m6 * _v[0] + _m7 * _v[1] + _m8 * _v[2] + _m11) * INCHES_TO_M, 6),
                             round(-(_m3 * _v[0] + _m4 * _v[1] + _m5 * _v[2] + _m10) * INCHES_TO_M, 6))
                            for _v in _local_verts
                        ]
                    run_layer = _explicit_layer if _explicit_layer is not None else mesh_layer
                    # Root-level loose geometry has no name in the file whatsoever -
                    # an edge is just an edge, only components get named. Labelling
                    # all 444 of them "ROOT" would be technically honest and useless,
                    # so a root-level run is labelled with the one signal that does
                    # exist (its layer) plus a sequence number to keep them distinct.
                    # Anything inside a named component keeps its real name.
                    if path_name == "ROOT":
                        _seq = curve_seq.get(run_layer, 0) + 1
                        curve_seq[run_layer] = _seq
                        _cname = "%s_%d" % (run_layer, _seq)
                        _cgen = True
                    else:
                        _cname = _cname_component
                        _cgen = False
                    curve_sets.append(CurveSetMetadata(
                        name=_cname,
                        name_is_generated=_cgen,
                        definition_name=_def_name_str,
                        layer=run_layer,
                        path=path_name,
                        points_m=pts,
                        closed=_closed,
                        arc=_solve_run_arc(builder, _eids,
                                           _fm, pts, _closed, _curve_counts),
                    ))

        child_instances_info: List[InstanceNode] = []
        for inst in builder.instances:
            ref_idx = inst["ref_idx"]
            inst_matrix = inst["matrix"]
            new_matrix = _core.multiply_matrices(current_matrix, inst_matrix)

            l_name = parent_layer
            inst_color = inherited_color
            inst_material = inherited_material
            # Legacy (pre-2021 MFC) instances carry a precomputed
            # "properties" dict (see legacy._extract_legacy_dynamic_
            # properties) - VFF instances don't set this key at all, so
            # this stays {} for them and gets overwritten below via the
            # D007/DC05 TLV walk instead.
            properties: Dict[str, str] = dict(inst.get("properties") or {})
            raw_attr_dicts = inst.get("attribute_dictionaries")
            attribute_dicts: Dict[str, Dict[str, str]] = {}
            name_override: Optional[str] = None

            # A classic (pre-2021) instance has no D007: its own tag and paint
            # were read with it.
            l_name = layer_id_to_name.get(inst.get("layer_id"), l_name)
            painted = resolve_material(
                inst.get("material_id"), material_id_to_name, materials, materials_by_folder
            )
            if painted:
                inst_color = resolve_color(painted)
                inst_material = painted

            d007 = next((c for c in inst["children"] if c["tag"] == "D007"), None)
            if d007:
                d207 = next((c for c in d007["children"] if c["tag"] == "D207"), None)
                if d207 and d207["payload"]:
                    p = d207["payload"]
                    l_id = p[0] if len(p) == 1 else _core.parse_var_int(p, 0, len(p))
                    l_name = layer_id_to_name.get(l_id, parent_layer)

                d107 = next((c for c in d007["children"] if c["tag"] == "D107"), None)
                if d107:
                    inst_mat_id = _core.parse_var_int(d107["payload"], 0, len(d107["payload"]))
                    mat_name = material_id_to_name.get(inst_mat_id)
                    mat = materials.get(mat_name) or materials_by_folder.get(mat_name)
                    if mat:
                        c = mat["color"]
                        inst_color = (c["r"], c["g"], c["b"])
                        inst_material = mat

                try:
                    raw_attr_dicts = _core.extract_attribute_dictionaries(d007)
                    dynamic = raw_attr_dicts.get("dynamic_attributes", {})
                    properties = {k: _core._stringify_vff_attr_value(v) for k, v in dynamic.items()}
                except Exception:
                    raw_attr_dicts = None
                    logger.debug(
                        "Failed to extract attribute dictionaries for instance %r (ref_idx=%r)",
                        inst.get("name"), ref_idx, exc_info=True,
                    )

            # Every other dictionary a third-party plugin (BIM workflow,
            # steel-detailing tool, ...) attached to this specific instance -
            # SU_InstanceSet is SketchUp's own always-present, always-empty
            # Owner/Status boilerplate, not worth surfacing.
            if raw_attr_dicts:
                for dict_name, entries in raw_attr_dicts.items():
                    if dict_name in ("dynamic_attributes", "SU_InstanceSet"):
                        continue
                    attribute_dicts[dict_name] = {
                        k: _core._stringify_vff_attr_value(v) for k, v in entries.items()
                    }
                    if name_override is None:
                        for key in name_override_keys:
                            if key in entries and entries[key] is not None:
                                s = _core._stringify_vff_attr_value(entries[key])
                                if s:
                                    name_override = s
                                    break

            # inst_name (never overridden) is what keeps full_path_name -
            # and so path_updates' own keys - locally unique: a plugin's
            # "name"/"label" field (used for display_name below) is
            # frequently a shared type/catalog label ("Profile25" for
            # every instance of that profile), not a real per-instance
            # identifier, and using it here would collide different
            # instances' path_updates entries onto each other.
            def_name = (defs_dict.get(ref_idx) or {}).get("name") or ""
            # Same fallback order as openskp.instanced_scene: attribute-dict
            # override, then the instance's own name, then the definition's
            # own name if it's not itself an auto-generated "Group#1"-style
            # placeholder, then finally the internal index.
            inst_name = inst["name"] or (
                def_name if def_name and not _is_generic_definition_name(def_name) else ""
            ) or f"Component_{ref_idx}"
            display_name = name_override or inst_name
            name_is_generated = not (name_override or inst["name"] or (
                def_name and not _is_generic_definition_name(def_name)
            ))
            full_path_name = f"{path_name} / {inst_name}"
            instance_counter[0] += 1
            if instance_counter[0] % _PROGRESS_INTERVAL == 0:
                logger.debug("Processed %d placed instances", instance_counter[0])

            if ref_idx in active_definitions:
                raise SkpParseError(
                    "Recursive component definition",
                    stage="build_scene", definition_id=ref_idx,
                )
            active_definitions.add(ref_idx)
            child_nodes = instantiate(ref_idx, new_matrix, l_name, full_path_name, inst_color, inst_material)
            active_definitions.discard(ref_idx)

            node_layer = l_name
            if l_name == "Layer0" and not child_nodes and ref_idx in defs_dict:
                child_uniform, _ = _face_layer_summary(ref_idx)
                if child_uniform is not None:
                    node_layer = child_uniform

            tx = new_matrix[9] * INCHES_TO_MM if len(new_matrix) > 9 else 0.0
            ty = new_matrix[10] * INCHES_TO_MM if len(new_matrix) > 10 else 0.0
            tz = new_matrix[11] * INCHES_TO_MM if len(new_matrix) > 11 else 0.0

            inst_info = InstanceNode(
                name=display_name,
                name_is_generated=name_is_generated,
                definition_name=def_name,
                layer=node_layer,
                position_mm=(round(tx, 2), round(ty, 2), round(tz, 2)),
                properties=properties,
                attribute_dictionaries=attribute_dicts,
                definition_attribute_dictionaries=_definition_dicts(ref_idx),
                children=child_nodes,
                path=full_path_name,
            )
            child_instances_info.append(inst_info)

            path_updates[full_path_name] = (properties, display_name, attribute_dicts)

        return child_instances_info

    identity_mat = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1.0]
    root_children = instantiate("ROOT", identity_mat)

    # Deferred mesh backfill: each mesh's own path was recorded verbatim as
    # a path_updates key by the exact instance that placed the definition
    # that mesh's own faces belong to (never an ancestor's), so a direct
    # O(1) lookup per mesh is enough - no cascading from an ancestor down
    # to its descendants' own meshes. Properties/name are per-instance
    # (each definition's own Dynamic Component attributes and placement
    # name), not inherited by nested sub-parts, matching how
    # scene_hierarchy already builds each InstanceNode from that same
    # instance's own `inst["name"]`/`properties` directly above - a mesh
    # ending up with some ancestor's name/properties instead of its own
    # was exactly this bug (openskp#240).
    for existing in mesh_index.values():
        if existing.path in path_updates:
            existing.properties, existing.name, existing.attribute_dictionaries = path_updates[existing.path]

    for geom_name, existing in mesh_index.items():
        if existing.path == "ROOT":
            # do NOT clobber `layer` here.
            #
            # This loop is meant to normalise the synthetic root *node*'s
            # placeholder fields (see scene_hierarchy below), but it iterates
            # real MESHES - specifically the ones whose path is exactly
            # "ROOT", i.e. loose geometry authored directly in the model root
            # rather than inside any group/component. `instantiate()` has
            # already resolved their layer from the definition's own faces
            # (legacy drawbase), and for such a mesh that layer is the ONLY
            # type signal that exists - there is no component name to fall
            # back on, because in SketchUp loose geometry has no name.
            # Hardcoding "Layer0" here threw that signal away and made every
            # root-level element invisible to export/ifc.py's classifier.
            # (Verified on gk_itjds_HyparHut.skp: 3 root meshes whose
            # geom_name had always carried the real layer `Table` while
            # MeshMetadata.layer read "Layer0".)
            existing.name = existing.name or "ROOT"
            existing.definition_name = existing.definition_name or "ROOT_MODEL"
            existing.layer = existing.layer or "Layer0"
            existing.position_mm = (0.0, 0.0, 0.0)
            existing.properties = existing.properties or {}
            existing.attribute_dictionaries = existing.attribute_dictionaries or {}

    scene_hierarchy = InstanceNode(
        name="ROOT",
        definition_name="ROOT_MODEL",
        layer="Layer0",
        position_mm=(0.0, 0.0, 0.0),
        properties={},
        children=root_children,
        path="ROOT",
    )

    logger.info(
        "Scene build complete: %d instances, %d meshes, %d primitives, "
        "%d curve sets (%.2fs)",
        instance_counter[0], len(mesh_index), len(glb_primitives),
        len(curve_sets), time.monotonic() - t0,
    )

    return Scene(
        scene_hierarchy=scene_hierarchy,
        mesh_index=mesh_index,
        glb_primitives=glb_primitives,
        curve_sets=curve_sets,
        gltf_materials=gltf_materials,
        textures=textures,
        layer_hidden=dict(parsed.get("layer_hidden") or {}),
        unread_root_entities=int(parsed.get("unread_root_entities", 0)),
        pages=_convert_pages(parsed),
    )
