"""Construct an empty classic-format (SketchUp 2017, v17) ``.skp`` document in code.

Every record of the empty MFC ``CArchive`` document is written field by field
through named builder methods and named constants — no bundled binary file, hex
blob, or compressed template is read or embedded. As each section is emitted,
the builder tracks the 1-based MFC store-map slots and byte offsets that
:mod:`openskp.create` splices into or patches when writing geometry, materials,
layers, component definitions, scenes, and document options.
"""
from __future__ import annotations

import dataclasses
import struct
import uuid
from typing import Dict, List, Optional, Sequence, Tuple

Point3 = Tuple[float, float, float]

# MFC CArchive UTF-16LE string prefix marker.
_UTF16_STRING_MARKER = b"\xff\xfe\xff"

# CAttributeNamed value-type tags used in OptionsManager providers and ModelProperties.
_ATTR_INT32 = 0x04
_ATTR_DOUBLE = 0x06
_ATTR_BOOL = 0x07

# CSkpStyle typed-property value tags.
_STYLE_BOOL = 1
_STYLE_UINT32 = 4
_STYLE_FLOAT32 = 6
_STYLE_FLOAT64 = 7

# File-header defaults matching the SketchUp 2017 blank document.
_FILE_MAGIC_TEXT = "SketchUp Model"
_VERSION_STRING_V17 = "{17.0.1}"
_DEFAULT_MODEL_GUID = "3093d39a-aa69-4e29-8d53-15cbfe3d94b0"
_DEFAULT_SAVE_TIMESTAMP_UNIX = 1786620315  # 2026-08-13T11:25:15Z (time_t)
_UTF16LE_CODEPAGE = 1200  # Windows UTF-16LE code page (0x04B0)
_INITIAL_NEXT_PERSISTENT_ID = 18

# Persistent entity IDs assigned in the empty document.
_SAVED_STYLE_PID = 1
_DIM_STYLE_FONT_PID = 2
_TEXT_STYLE_FONT_PID = 3
_LAYER0_PID = 4
_LAYER0_MATERIAL_PID = 5

# Saved and working CSkpStyle GUIDs in the empty document.
_DEFAULT_SAVED_STYLE_GUID = "94fee66b-2dce-4b79-ada7-4be56f9266a2"
_DEFAULT_WORKING_STYLE_GUID = "667aac7a-d15a-409d-9d21-24ffa8045e61"

# Default shadow / geo-location settings ("Boulder (CO)", "USA", summer solstice).
_DEFAULT_SHADOW_TIME_UNIX = 1782048600  # 2026-06-21T13:30:00Z
_DEFAULT_CITY = "Boulder (CO)"
_DEFAULT_COUNTRY = "USA"
_DEFAULT_LONGITUDE = -105.283
_DEFAULT_LATITUDE = 40.017
_DEFAULT_TZ_OFFSET = -7.0
_DEFAULT_SHADOW_LIGHT = 80
_DEFAULT_SHADOW_DARK = 20

# SketchUp 2017 CVersionMap schema table (alphabetical class name -> schema version).
_VERSION_MAP_V17: Tuple[Tuple[str, int], ...] = (
    ("CArcCurve", 3),
    ("CAttribute", 0),
    ("CAttributeContainer", 0),
    ("CAttributeNamed", 1),
    ("CBackgroundImage", 10),
    ("CCamera", 5),
    ("CComponent", 11),
    ("CComponentBehavior", 5),
    ("CComponentDefinition", 10),
    ("CComponentInstance", 5),
    ("CConstructionGeometry", 0),
    ("CConstructionLine", 1),
    ("CConstructionPoint", 0),
    ("CCurve", 4),
    ("CDefinitionList", 0),
    ("CDib", 3),
    ("CDimension", 1),
    ("CDimensionLinear", 6),
    ("CDimensionRadial", 2),
    ("CDimensionStyle", 4),
    ("CDrawingElement", 9),
    ("CEdge", 2),
    ("CEdgeUse", 1),
    ("CEntity", 5),
    ("CFace", 3),
    ("CFaceTextureCoords", 4),
    ("CFontManager", 0),
    ("CGroup", 1),
    ("CImage", 1),
    ("CLayer", 2),
    ("CLayerManager", 4),
    ("CLoop", 1),
    ("CMaterial", 12),
    ("CMaterialManager", 4),
    ("CPageList", 1),
    ("CPolyline3d", 0),
    ("CRelationship", 0),
    ("CRelationshipMap", 0),
    ("CRenderingOptions", 36),
    ("CSchemaFile", 1),
    ("CSchemaFilterFile", 0),
    ("CSchemaZipFile", 1),
    ("CSectionPlane", 2),
    ("CShadowInfo", 7),
    ("CSkFont", 1),
    ("CSketchCS", 0),
    ("CSketchUpModel", 26),
    ("CSketchUpPage", 1),
    ("CSkpStyle", 1),
    ("CSkpStyleManager", 2),
    ("CText", 9),
    ("CTextStyle", 5),
    ("CTexture", 6),
    ("CThumbnail", 1),
    ("CVertex", 0),
    ("CViewPage", 12),
    ("CWatermark", 1),
    ("CWatermarkManager", 2),
)
_VERSION_MAP_SENTINEL = "End-Of-Version-Map"


def _pack_abgr(r: int, g: int, b: int, a: int = 255) -> int:
    """Pack an ``(r, g, b, a)`` colour into a little-endian ``uint32`` word
    whose in-memory bytes are ``[r, g, b, a]`` (SketchUp's ``COLORREF``/ABGR word)."""
    return (r & 0xFF) | ((g & 0xFF) << 8) | ((b & 0xFF) << 16) | ((a & 0xFF) << 24)


def _encode_utf16_str(text: str) -> bytes:
    """Encode ``text`` with SketchUp's ``FF FE FF`` UTF-16LE string framing."""
    raw = text.encode("utf-16-le")
    code_units = len(raw) // 2
    if code_units < 0xFF:
        return _UTF16_STRING_MARKER + struct.pack("<B", code_units) + raw
    if code_units < 0xFFFF:
        return _UTF16_STRING_MARKER + b"\xff" + struct.pack("<H", code_units) + raw
    return _UTF16_STRING_MARKER + b"\xff\xff\xff" + struct.pack("<I", code_units) + raw


def pack_camera_body(
    *,
    eye: Point3,
    target: Point3,
    up: Point3,
    clip_near: float = 1.0,
    clip_far: float = 1000.0,
    perspective: bool = True,
    fov: float = 30.0,
    ortho_height: float = 133.0,
    window_params: Point3 = (0.0, 0.0, 0.0),
    aspect_ratio: float = 0.0,
    fov_is_height: bool = True,
    camera_flag: int = 0,
    description: str = "",
    image_width: float = 0.0,
    is_2d: bool = False,
    scale_2d: float = 1.0,
    center_2d: Tuple[float, float] = (0.0, 0.0),
) -> bytes:
    """Serialize the body of a ``CCamera`` (schema 5) object after its MFC tag.

    With an empty ``description`` string this body is 176 bytes.
    """
    out = bytearray()
    out += struct.pack("<3d", *eye)
    out += struct.pack("<3d", *target)
    out += struct.pack("<3d", *up)
    out += struct.pack("<2d", clip_near, clip_far)
    out += struct.pack("<B", 1 if perspective else 0)
    out += struct.pack("<2d", fov, ortho_height)
    out += struct.pack("<3d", *window_params)
    out += struct.pack("<d", aspect_ratio)
    out += struct.pack("<BB", 1 if fov_is_height else 0, camera_flag)
    out += _encode_utf16_str(description)
    out += struct.pack("<d", image_width)
    out += struct.pack("<B", 1 if is_2d else 0)
    out += struct.pack("<d", scale_2d)
    out += struct.pack("<2d", *center_2d)
    return bytes(out)


# Default 56-entry CSkpStyle property table: (key_id, value_kind, value).
# value_kind is None when the property carries 0 typed values (key 5001: watermark list).
_DEFAULT_STYLE_PROPERTIES: Tuple[Tuple[int, Optional[int], float], ...] = (
    (1000, _STYLE_UINT32, 1),                          # EdgeDisplayMode: show edges
    (1001, _STYLE_UINT32, 0),                          # EdgeType: standard strokes
    (1002, _STYLE_UINT32, 1),                          # EdgeColorMode: all same
    (1004, _STYLE_BOOL, 0),                            # ExtendLines: false
    (1005, _STYLE_UINT32, 8),                          # LineExtension: 8 px
    (1006, _STYLE_BOOL, 1),                            # DrawSilhouettes (Profiles): true
    (1007, _STYLE_UINT32, 3),                          # SilhouetteWidth: 3 px
    (1008, _STYLE_BOOL, 0),                            # DrawDepthQue: false
    (1009, _STYLE_UINT32, 4),                          # DepthQueWidth: 4 px
    (1010, _STYLE_BOOL, 0),                            # DrawLineEnds (Endpoints): false
    (1011, _STYLE_UINT32, 5),                          # LineEndWidth: 5 px
    (1012, _STYLE_BOOL, 0),                            # JitterEdges: false
    (1014, _STYLE_UINT32, _pack_abgr(0, 0, 0, 255)),   # ForegroundColor: black
    (1015, _STYLE_BOOL, 0),                            # DrawBackEdges: false
    (1016, _STYLE_BOOL, 1),                            # Edge halo / stroke flag: true
    (2001, _STYLE_UINT32, 2),                          # RenderMode: ShadedWithTextures
    (2002, _STYLE_UINT32, _pack_abgr(225, 225, 200, 255)),  # FaceFrontColor
    (2003, _STYLE_UINT32, _pack_abgr(128, 128, 200, 255)),  # FaceBackColor
    (2004, _STYLE_BOOL, 0),                            # ModelTransparency (X-ray): false
    (2005, _STYLE_BOOL, 1),                            # MaterialTransparency: true
    (2006, _STYLE_UINT32, 0),                          # TransparencySort: faster (0)
    (2007, _STYLE_BOOL, 1),                            # Texture: true
    (2008, _STYLE_FLOAT64, 0.65),                      # Transparency factor: 0.65
    (4000, _STYLE_UINT32, _pack_abgr(255, 255, 255, 255)),  # BackgroundColor: white
    (4001, _STYLE_UINT32, _pack_abgr(140, 165, 255, 255)),  # SkyColor
    (4002, _STYLE_BOOL, 0),                            # DrawHorizon (Sky): false
    (4003, _STYLE_UINT32, _pack_abgr(63, 147, 63, 255)),    # GroundColor
    (4004, _STYLE_BOOL, 0),                            # DrawGround: false
    (4005, _STYLE_UINT32, 50),                         # GroundTransparency: 50
    (4006, _STYLE_BOOL, 1),                            # DrawUnderground: true
    (4007, _STYLE_UINT32, _pack_abgr(0, 0, 0, 0)),     # HorizonColor: transparent black
    (5000, _STYLE_BOOL, 1),                            # DisplayWatermarks: true
    (5001, None, 0),                                   # Watermarks list: empty (count = 0)
    (7000, _STYLE_UINT32, _pack_abgr(255, 255, 0, 255)),    # HighlightColor: yellow
    (7001, _STYLE_UINT32, _pack_abgr(255, 0, 0, 255)),      # LockedColor: red
    (7002, _STYLE_UINT32, _pack_abgr(128, 128, 128, 255)),  # ConstructionColor: grey
    (7003, _STYLE_UINT32, _pack_abgr(102, 110, 141, 255)),  # SectionActiveColor
    (7004, _STYLE_UINT32, _pack_abgr(192, 192, 192, 255)),  # SectionInactiveColor
    (7005, _STYLE_UINT32, _pack_abgr(255, 0, 0, 255)),      # SectionDefaultCutColor
    (7008, _STYLE_BOOL, 1),                            # DisplaySketchAxes: true
    (7010, _STYLE_BOOL, 0),                            # DrawHidden: false
    (7011, _STYLE_BOOL, 0),                            # DisplayColorByLayer: false
    (7012, _STYLE_BOOL, 0),                            # HideConstructionGeometry: false
    (7013, _STYLE_UINT32, 3),                          # SectionDisplayMode: planes (1) | cuts (2)
    (7014, _STYLE_UINT32, 4),                          # SectionCutWidth: 4
    (7015, _STYLE_BOOL, 0),                            # SectionCutFilled: false
    (7016, _STYLE_UINT32, _pack_abgr(0, 0, 0, 255)),   # SectionDefaultFillColor: black
    (7017, _STYLE_BOOL, 0),                            # DrawHiddenGeometry: false
    (7018, _STYLE_BOOL, 0),                            # DrawHiddenObjects: false
    (8000, _STYLE_BOOL, 1),                            # Match Photo background image: true
    (8001, _STYLE_FLOAT64, 1.0),                       # Match Photo background opacity: 1.0
    (8002, _STYLE_BOOL, 1),                            # Match Photo foreground photo: true
    (8003, _STYLE_FLOAT64, 0.8),                       # Match Photo foreground opacity: 0.8
    (8100, _STYLE_BOOL, 0),                            # AmbientOcclusion: false
    (8102, _STYLE_FLOAT32, 10.0),                      # AmbientOcclusionDistance: 10.0
    (8103, _STYLE_FLOAT32, 0.0),                       # AmbientOcclusionIntensity: 0.0
)


@dataclasses.dataclass(frozen=True)
class BlankDocument:
    """Serialized empty v17 ``.skp`` document together with every named splice
    position, field offset, length, and MFC store-map slot :mod:`openskp.create`
    needs."""

    data: bytes
    model_guid_offset: int
    pid_counter_offset: int
    next_pid: int
    unit_option_offsets: Dict[str, int]
    attr_container_slot: int
    attribute_named_slot: int
    ccamera_slot: int
    camera_body_offset: int
    camera_body_len: int
    rendering_options_offset: int
    rendering_options_len: int
    rendering_option_fields: Tuple[Tuple[int, int, bool], ...]
    material_count_pos: int
    material_insert_pos: int
    material_base_slot: int
    layer_count_pos: int
    orig_layer_count: int
    layer_insert_pos: int
    layer_writer_base_slot: int
    active_layer_ref_pos: int
    def_count_pos: int
    orig_def_count: int
    root_count_pos: int
    orig_root_count: int
    tail_pos: int
    scaffold_next_slot: int
    scaffold_class_slot: Dict[str, int]
    shadow_info_tail_rel: int
    shadow_info_len: int
    page_list_tail_rel: int
    font_tail_refs: Tuple[int, ...]
    style_object_tail_rel: int
    style_tail_refs: Tuple[int, int]
    tail_ref_positions: Tuple[int, ...]
    saved_style_guid_tail_rel: int
    working_style_guid_tail_rel: int
    saved_style_items_tail_rel: int
    working_style_items_tail_rel: int
    style_item_count: int


class _BlankArchiveBuilder:
    """Sequential writer for the empty classic MFC ``CArchive`` document."""

    def __init__(self) -> None:
        self.buf = bytearray()
        self.next_slot = 1
        self.class_slot: Dict[str, int] = {}

    def _u8(self, value: int) -> None:
        self.buf.append(value & 0xFF)

    def _u16(self, value: int) -> None:
        self.buf += struct.pack("<H", value)

    def _u32(self, value: int) -> None:
        self.buf += struct.pack("<I", value)

    def _i32(self, value: int) -> None:
        self.buf += struct.pack("<i", value)

    def _u64(self, value: int) -> None:
        self.buf += struct.pack("<Q", value)

    def _f32(self, value: float) -> None:
        self.buf += struct.pack("<f", value)

    def _f64(self, value: float) -> None:
        self.buf += struct.pack("<d", value)

    def _rgba(self, r: int, g: int, b: int, a: int = 255) -> None:
        self.buf += bytes((r & 0xFF, g & 0xFF, b & 0xFF, a & 0xFF))

    def _uuid(self, canonical_guid: str) -> None:
        self.buf += uuid.UUID(canonical_guid).bytes_le

    def _str(self, text: str) -> None:
        self.buf += _encode_utf16_str(text)

    def _zeros(self, count: int) -> None:
        self.buf += bytes(count)

    def _alloc_slot(self) -> int:
        slot = self.next_slot
        self.next_slot += 1
        return slot

    def _null_ref(self) -> None:
        self._u16(0)

    def _backref(self, slot: int) -> None:
        self._u16(slot)

    def _new_object(self, class_name: str, schema: int) -> int:
        """Emit an MFC new-class declaration on first use or a short ``0x8000|slot``
        class-reference thereafter, and return the allocated object slot."""
        if class_name not in self.class_slot:
            self._u16(0xFFFF)
            self._u16(schema)
            self._u16(len(class_name))
            self.buf += class_name.encode("ascii")
            self.class_slot[class_name] = self._alloc_slot()
            return self._alloc_slot()
        cslot = self.class_slot[class_name]
        self._u16(0x8000 | cslot)
        return self._alloc_slot()

    def _encode_pid(self, pid: int) -> None:
        mask = 0
        payload = bytearray()
        for bit in range(8):
            byte_val = (pid >> (8 * bit)) & 0xFF
            if byte_val:
                mask |= 1 << bit
                payload.append(byte_val)
        self._u8(mask)
        self.buf += payload

    def _entity_preamble(self, pid: int = 0) -> None:
        """Write a ``CEntity`` preamble with a null ``CAttributeContainer`` ref."""
        self._null_ref()
        self._encode_pid(pid)

    def _write_attr_entry(self, key: str, tag: int, value: object) -> int:
        """Write one ``(key, type_tag, value)`` option/attribute entry and return
        the byte offset of ``value``."""
        self._str(key)
        self._u8(tag)
        val_offset = len(self.buf)
        if tag == _ATTR_BOOL:
            self._u8(1 if value else 0)
        elif tag == _ATTR_INT32:
            self._i32(int(value))  # type: ignore[arg-type]
        elif tag == _ATTR_DOUBLE:
            self._f64(float(value))  # type: ignore[arg-type]
        else:
            raise ValueError(f"unsupported attribute tag {tag:#x}")
        return val_offset

    # ── document sections ────────────────────────────────────────────────────

    def write_file_header(self) -> int:
        self._str(_FILE_MAGIC_TEXT)
        self._str(_VERSION_STRING_V17)
        model_guid_offset = len(self.buf)
        self._uuid(_DEFAULT_MODEL_GUID)
        self._str("")  # empty release/locale note string
        self._u32(_DEFAULT_SAVE_TIMESTAMP_UNIX)
        return model_guid_offset

    def write_version_map(self) -> None:
        self._new_object("CVersionMap", schema=0)
        for cls_name, schema_ver in _VERSION_MAP_V17:
            self._str(cls_name)
            self._u32(schema_ver)
        self._str(_VERSION_MAP_SENTINEL)

    def write_model_preamble(self) -> int:
        self._u32(0)                 # model header word 0
        self._u32(1)                 # model header format flag (1 in v17+)
        self._u32(_UTF16LE_CODEPAGE)
        self._u32(0)                 # preview thumbnail status (0 = none)
        pid_counter_offset = len(self.buf)
        self._u64(_INITIAL_NEXT_PERSISTENT_ID)
        self._null_ref()             # null model preview CDib pointer
        self._u32(1)                 # post-thumbnail model flag
        self._zeros(11)              # reserved model header bytes (all 0)
        self._str("")                # model description (SUModelGetDescription)
        self._u32(3)                 # options-manager format version
        return pid_counter_offset

    def write_options_providers(self) -> Dict[str, int]:
        self._u32(4)  # four named SUOptionsProvider tables follow

        # 1. NamedOptions (empty)
        self._str("NamedOptions")
        self._str("")

        # 2. PageOptions
        self._str("PageOptions")
        self._write_attr_entry("ShowTransition", _ATTR_BOOL, True)
        self._write_attr_entry("TransitionTime", _ATTR_DOUBLE, 1.5)
        self._str("")

        # 3. SlideshowOptions
        self._str("SlideshowOptions")
        self._write_attr_entry("LoopSlideshow", _ATTR_BOOL, True)
        self._write_attr_entry("SlideTime", _ATTR_DOUBLE, 1.0)
        self._str("")

        # 4. UnitsOptions
        self._str("UnitsOptions")
        unit_offsets: Dict[str, int] = {}
        unit_offsets["LengthPrecision"] = self._write_attr_entry("LengthPrecision", _ATTR_INT32, 4)
        unit_offsets["LengthFormat"] = self._write_attr_entry("LengthFormat", _ATTR_INT32, 1)  # 1 = Architectural
        unit_offsets["LengthUnit"] = self._write_attr_entry("LengthUnit", _ATTR_INT32, 0)      # 0 = Inches
        self._write_attr_entry("LengthSnapEnabled", _ATTR_BOOL, True)
        self._write_attr_entry("LengthSnapLength", _ATTR_DOUBLE, 0.0625)  # 1/16 inch
        self._write_attr_entry("AnglePrecision", _ATTR_INT32, 1)
        self._write_attr_entry("AngleSnapEnabled", _ATTR_BOOL, True)
        self._write_attr_entry("SnapAngle", _ATTR_DOUBLE, 15.0)
        self._write_attr_entry("SuppressUnitsDisplay", _ATTR_BOOL, False)
        self._write_attr_entry("ForceInchDisplay", _ATTR_BOOL, False)
        unit_offsets["AreaUnit"] = self._write_attr_entry("AreaUnit", _ATTR_INT32, 0)
        unit_offsets["VolumeUnit"] = self._write_attr_entry("VolumeUnit", _ATTR_INT32, 0)
        self._write_attr_entry("AreaPrecision", _ATTR_INT32, 2)
        self._write_attr_entry("VolumePrecision", _ATTR_INT32, 2)
        self._str("")
        return unit_offsets

    def write_model_properties(self) -> None:
        # CAttributeContainer holding one CAttributeNamed("ModelProperties")
        self._new_object("CAttributeContainer", schema=0)
        self._entity_preamble(pid=0)

        self._new_object("CAttributeNamed", schema=1)
        self._entity_preamble(pid=0)
        self._u32(0)  # CAttribute flags
        self._str("ModelProperties")
        self._write_attr_entry("IsClassified", _ATTR_BOOL, False)
        self._write_attr_entry("IsDynamic", _ATTR_BOOL, False)
        self._write_attr_entry("IsLive", _ATTR_BOOL, False)
        self._str("")  # end of CAttributeNamed entries

        self._null_ref()  # end of CAttributeContainer children
        self._zeros(6)    # three null u16 fields before model CCamera

    def write_model_camera(self) -> Tuple[int, int]:
        self._new_object("CCamera", schema=5)
        body_offset = len(self.buf)
        body = pack_camera_body(
            eye=(0.0, 0.0, 500.0),
            target=(0.0, 0.0, 0.0),
            up=(0.0, 1.0, 0.0),
            clip_near=1.0,
            clip_far=1000.0,
            perspective=True,
            fov=30.0,
            ortho_height=133.0,
        )
        self.buf += body
        return body_offset, len(body)

    def write_rendering_options(self) -> Tuple[int, int, Tuple[Tuple[int, int, bool], ...]]:
        self._entity_preamble(pid=0)  # CRenderingOptions CEntity preamble
        ro_start = len(self.buf)
        fields: List[Tuple[int, int, bool]] = []

        def ro_u8(val: int, style_item: Optional[int] = None) -> None:
            if style_item is not None:
                fields.append((style_item, len(self.buf), True))
            self._u8(val)

        def ro_u32(val: int, style_item: Optional[int] = None) -> None:
            if style_item is not None:
                fields.append((style_item, len(self.buf), False))
            self._u32(val)

        def ro_rgba(r: int, g: int, b: int, a: int = 255, style_item: Optional[int] = None) -> None:
            if style_item is not None:
                fields.append((style_item, len(self.buf), False))
            self._rgba(r, g, b, a)

        ro_u32(2, style_item=2001)                  # RenderMode: ShadedWithTextures
        ro_u8(0, style_item=2004)                   # ModelTransparency (X-ray)
        ro_u8(1, style_item=2005)                   # MaterialTransparency
        ro_u8(0, style_item=1012)                   # JitterEdges
        ro_u32(1, style_item=1000)                  # EdgeDisplayMode: show edges
        ro_rgba(0, 0, 0, 0, style_item=4000)        # BackgroundColor slot (unset until style sync)
        ro_rgba(0, 0, 0, 0, style_item=1014)        # ForegroundColor slot (unset until style sync)
        ro_rgba(255, 255, 0, 255, style_item=7000)  # HighlightColor
        ro_rgba(128, 128, 128, 255, style_item=7002)  # ConstructionColor
        ro_u8(0)                                    # DisplayShadows
        ro_u8(0)                                    # DisplayInstanceAxes
        ro_u8(0, style_item=7011)                   # DisplayColorByLayer (Color by Tag)
        ro_u8(1, style_item=2007)                   # Texture
        ro_u32(1, style_item=1002)                  # EdgeColorMode: all same
        ro_u8(0, style_item=1004)                   # ExtendLines
        ro_u32(8, style_item=1005)                  # LineExtension: 8 px
        ro_u8(1, style_item=1006)                   # DrawSilhouettes (Profiles)
        ro_u32(3, style_item=1007)                  # SilhouetteWidth: 3 px
        ro_u8(0, style_item=1008)                   # DrawDepthQue
        ro_u32(4, style_item=1009)                  # DepthQueWidth: 4 px
        ro_u8(0, style_item=1010)                   # DrawLineEnds (Endpoints)
        ro_u32(5, style_item=1011)                  # LineEndWidth: 5 px
        ro_u8(0)                                    # DrawProfilesOnly
        ro_u8(0, style_item=7010)                   # DrawHidden
        self._u32(0)                                # reserved rendering-options word (0)
        ro_rgba(225, 225, 200, 255, style_item=2002)  # FaceFrontColor
        ro_rgba(128, 128, 200, 255, style_item=2003)  # FaceBackColor
        self._f64(0.75)                             # InactiveFade
        self._f64(0.5)                              # InstanceFade
        ro_u8(0)                                    # InactiveHidden
        ro_u8(0)                                    # InstanceHidden
        ro_u8(0)                                    # DisplayFog
        ro_rgba(0, 0, 0, 0)                         # FogColor
        ro_u8(1)                                    # FogUseBkColor
        self._f64(-1.0)                             # FogStartDist (-1 = automatic)
        self._f64(-1.0)                             # FogEndDist (-1 = automatic)
        self._u32(1)                                # fog / stroke mode constant (1)
        ro_u32(0, style_item=1001)                  # EdgeType: standard strokes
        ro_u8(1, style_item=7008)                   # DisplaySketchAxes
        ro_u8(1)                                    # DisplayText
        ro_u8(1)                                    # DisplayDims
        ro_u8(0, style_item=7012)                   # HideConstructionGeometry
        ro_rgba(140, 165, 255, 255, style_item=4001)  # SkyColor
        ro_rgba(0, 0, 0, 0, style_item=4007)        # HorizonColor
        ro_rgba(63, 147, 63, 255, style_item=4003)  # GroundColor
        ro_u8(0, style_item=4002)                   # DrawHorizon
        ro_u8(0, style_item=4004)                   # DrawGround
        ro_u8(1, style_item=4006)                   # DrawUnderground
        ro_u32(50, style_item=4005)                 # GroundTransparency: 50
        ro_rgba(102, 110, 141, 255, style_item=7003)  # SectionActiveColor
        ro_rgba(192, 192, 192, 255, style_item=7004)  # SectionInactiveColor
        ro_rgba(255, 0, 0, 255, style_item=7005)    # SectionDefaultCutColor
        ro_u32(4, style_item=7014)                  # SectionCutWidth: 4
        ro_u32(3)                                   # SectionDisplayMode: planes (1) | cuts (2)
        ro_u32(0, style_item=2006)                  # TransparencySort: faster (0)
        ro_u8(1)                                    # edge halo flag (style item 1016)
        self._f64(0.34906585039886595)              # 20 deg in radians (crease / halo angle)
        ro_u8(1)                                    # ShowViewName: true
        ro_rgba(255, 0, 0, 255, style_item=7001)    # LockedColor
        ro_u8(1, style_item=5000)                   # DisplayWatermarks
        self._f64(0.65)                             # transparency factor (style item 2008)
        ro_u8(0, style_item=1015)                   # DrawBackEdges
        ro_u8(1)                                    # Match Photo background image (item 8000)
        self._f64(1.0)                              # Match Photo background opacity (item 8001)
        ro_u8(1)                                    # Match Photo foreground photo (item 8002)
        self._f64(0.8)                              # Match Photo foreground opacity (item 8003)

        ro_len = len(self.buf) - ro_start
        fields_sorted = tuple(sorted(fields, key=lambda item: item[0]))
        return ro_start, ro_len, fields_sorted

    def write_component_and_material_manager(self) -> Tuple[int, int, int]:
        self._u32(0)                 # post-rendering-options word 0
        self._u32(1)                 # post-rendering-options word 1
        self._entity_preamble(pid=0)  # CSketchUpModel CEntity preamble (null model attributes)
        self._zeros(3)               # CComponent base header pad (offsets 0..2)
        self._u8(1)                  # CComponent base flag 0
        self._u8(1)                  # CComponent base flag 1
        self._entity_preamble(pid=0)  # CMaterialManager CEntity preamble
        self._zeros(5)               # CMaterialManager header pad
        material_count_pos = len(self.buf)
        self._u32(0)                 # material_count = 0
        material_insert_pos = len(self.buf)
        material_base_slot = self.next_slot
        return material_count_pos, material_insert_pos, material_base_slot

    def write_layer_manager(self) -> Tuple[int, int, int, int, int, Dict[str, int]]:
        self._null_ref()             # active material ref (null = default unpainted)
        self._entity_preamble(pid=0)  # CLayerManager CEntity preamble
        layer_count_pos = len(self.buf)
        orig_layer_count = 1
        self._u32(orig_layer_count)

        # Default untagged layer "Layer0" (schema 3, layer pid=4, material pid=5)
        layer0_slot = self._new_object("CLayer", schema=3)
        middle_class_slot = {"CLayer": self.class_slot["CLayer"]}
        self._entity_preamble(pid=_LAYER0_PID)
        self._str("Layer0")
        self._u8(0)                  # hidden = False
        self._u16(0)                 # layer flags = 0
        self._encode_pid(_LAYER0_MATERIAL_PID)
        self._str("Layer_Layer0")
        self._u16(0x0100)            # embedded layer-colour material flag (256)
        self._rgba(255, 84, 84, 255)  # default Layer0 tag colour
        self._str("")                # empty texture path
        self._u32(0)                 # colorize mode = 0
        self._u32(0)                 # colorize flags = 0
        self._f64(0.5)               # default layer material alpha/transparency
        self._u8(0)                  # use_opacity = False
        self._u32(0)                 # layer trailer word = 0

        layer_insert_pos = len(self.buf)
        layer_writer_base_slot = self.next_slot
        active_layer_ref_pos = len(self.buf)
        self._backref(layer0_slot)   # active layer back-ref -> Layer0
        return (
            layer_count_pos,
            orig_layer_count,
            layer_insert_pos,
            layer_writer_base_slot,
            active_layer_ref_pos,
            middle_class_slot,
        )

    def write_definition_and_root_lists(self) -> Tuple[int, int, int, int, int, int]:
        def_count_pos = len(self.buf)
        orig_def_count = 0
        self._u32(orig_def_count)

        root_count_pos = len(self.buf)
        orig_root_count = 0
        self._u32(orig_root_count)

        tail_pos = len(self.buf)
        scaffold_next_slot = self.next_slot
        return (
            def_count_pos,
            orig_def_count,
            root_count_pos,
            orig_root_count,
            tail_pos,
            scaffold_next_slot,
        )

    def write_shadow_info(self, tail_pos: int) -> Tuple[int, int, int]:
        self._u32(0)                 # root CRelationship list count = 0
        self._u16(0)                 # root component closing word = 0
        self._entity_preamble(pid=0)  # CShadowInfo CEntity preamble

        shadow_start = len(self.buf)
        self._u32(_DEFAULT_SHADOW_TIME_UNIX)
        self._u8(0)                  # DaylightSavings = False
        self._str(_DEFAULT_CITY)
        self._str(_DEFAULT_COUNTRY)
        self._f64(_DEFAULT_LONGITUDE)
        self._f64(_DEFAULT_LATITUDE)
        self._f64(_DEFAULT_TZ_OFFSET)
        self._f64(0.0)               # NorthAngle (degrees / sin = 0.0)
        self._f64(1.0)               # North direction Y = cos(0) = 1.0
        self._f64(0.0)               # North direction Z = 0.0
        self._u8(0)                  # DisplayNorth = False
        self._u8(1)                  # DisplayOnAllFaces = True
        self._u8(1)                  # DisplayOnGroundPlane = True
        self._u8(0)                  # EdgesCastShadows = False
        self._u32(_DEFAULT_SHADOW_LIGHT)
        self._u32(_DEFAULT_SHADOW_DARK)
        shadow_len = len(self.buf) - shadow_start

        page_list_tail_rel = len(self.buf) - tail_pos
        self._u8(0)                  # UseSunForAllShading = False
        return shadow_start - tail_pos, shadow_len, page_list_tail_rel

    def write_page_list(self) -> None:
        self._entity_preamble(pid=0)  # CPageList CEntity preamble
        self._u32(0)                 # page_count = 0
        self._null_ref()             # selected_page_ref = null

    def write_model_axes(self) -> None:
        self._zeros(3)               # page-list closing bytes (0)
        self._entity_preamble(pid=0)  # CSketchCS CEntity preamble
        self._u8(1)                  # axis flag 0
        self._u8(1)                  # axis flag 1
        self._zeros(5)               # axis header pad
        self._f64(0.0)               # origin.x
        self._f64(0.0)               # origin.y
        self._f64(0.0)               # origin.z
        self._f64(1.0)               # x_axis.x
        self._f64(0.0)               # x_axis.y
        self._f64(0.0)               # x_axis.z
        self._f64(0.0)               # y_axis.x
        self._f64(1.0)               # y_axis.y
        self._f64(0.0)               # y_axis.z
        self._f64(0.0)               # z_axis.x
        self._f64(0.0)               # z_axis.y
        self._f64(1.0)               # z_axis.z
        self._zeros(68)              # empty post-axes model tables before CDimensionStyle

    def _write_skfont(self, face_name: str, point_size: int, pid: int) -> int:
        slot = self._new_object("CSkFont", schema=1)
        self._entity_preamble(pid=pid)
        self._str(face_name)
        self._u16(0)                 # bold/italic flags = 0
        self._u32(point_size)
        self._u8(0)                  # use_world_size = False
        self._f64(1.0)               # world_size = 1.0 inch
        return slot

    def write_dimension_style(self) -> int:
        self._entity_preamble(pid=0)  # CDimensionStyle CEntity preamble
        arial_slot = self._write_skfont("Arial", point_size=12, pid=_DIM_STYLE_FONT_PID)
        self._u8(0)                  # has_3d = False
        self._u8(1)                  # dimension text visibility / placement flag
        self._u32(5)                 # extension_line_offset = 5
        self._u32(10)                # extension_line_overshoot = 10
        self._u32(1)                 # line_weight = 1
        self._u32(3)                 # arrow_type = 3 (closed arrow)
        self._u32(10)                # arrow_size = 10
        self._u8(0)                  # highlight_non_associative = False
        self._rgba(255, 0, 0, 255)   # highlight_non_associative_color = red
        self._u8(1)                  # show_radial_prefix = True
        self._u8(0)                  # hide_out_of_plane = False
        self._f64(0.6)               # hide_out_of_plane_value = 0.6
        self._u8(0)                  # hide_small = False
        self._f64(10.0)              # hide_small_value = 10.0
        self._rgba(64, 64, 64, 255)  # dimension colour = dark grey
        self._u8(0)                  # use_text_color = False
        self._zeros(3)               # text alignment / flags = 0
        self._u8(1)                  # show_units = True
        self._zeros(3)               # dimension-style trailer pad
        return arial_slot

    def write_text_style_and_font_manager(
        self, tail_pos: int, arial_slot: int,
    ) -> Tuple[int, ...]:
        font_refs: List[int] = []
        self._entity_preamble(pid=0)  # CTextStyle CEntity preamble

        # Inline CSkFont("Tahoma"): its short class-ref (0x8000|11) shifts when slots grow.
        font_refs.append(len(self.buf) - tail_pos)
        tahoma_slot = self._write_skfont("Tahoma", point_size=12, pid=_TEXT_STYLE_FONT_PID)
        self._u32(2)                 # leader_type = 2 (pushpin)
        self._u32(1)                 # display_leader = 1
        self._u8(0)                  # text-style flag = 0
        self._u32(3)                 # arrow_type = 3 (closed arrow)
        self._u8(1)                  # leader visibility flag = 1
        self._rgba(0, 0, 0, 255)     # text colour = black
        self._rgba(0, 0, 0, 255)     # leader colour = black
        font_refs.append(len(self.buf) - tail_pos)
        self._backref(tahoma_slot)

        # CFontManager: [preamble][count = 2][Arial ref][Tahoma ref][closing u16 = 0]
        self._entity_preamble(pid=0)
        self._u32(2)
        font_refs.append(len(self.buf) - tail_pos)
        self._backref(arial_slot)
        font_refs.append(len(self.buf) - tail_pos)
        self._backref(tahoma_slot)
        self._u16(0)
        return tuple(font_refs)

    def _write_style_properties(
        self, properties: Sequence[Tuple[int, Optional[int], float]],
    ) -> int:
        self._u32(len(properties))
        items_start = len(self.buf)
        for key_id, kind, value in properties:
            self._u32(key_id)
            if kind is None:
                self._u32(0)
                continue
            self._u32(1)
            self._u32(kind)
            if kind == _STYLE_BOOL:
                self._u8(1 if value != 0.0 else 0)
            elif kind == _STYLE_UINT32:
                self._u32(int(value) & 0xFFFFFFFF)
            elif kind == _STYLE_FLOAT32:
                self._f32(float(value))
            elif kind == _STYLE_FLOAT64:
                self._f64(float(value))
            else:
                raise ValueError(f"unsupported style property kind {kind}")
        return items_start

    def _write_skp_style(
        self, *, name: str, description: str, guid: str, pid: int,
    ) -> Tuple[int, int, int]:
        slot = self._new_object("CSkpStyle", schema=2)
        self._entity_preamble(pid=pid)
        guid_offset = len(self.buf)
        self._uuid(guid)
        self._str("")                # source .style path
        self._u32(3)                 # style format version = 3
        self._str(name)
        self._str(description)
        items_offset = self._write_style_properties(_DEFAULT_STYLE_PROPERTIES)
        return slot, guid_offset, items_offset

    def write_style_manager(
        self, tail_pos: int,
    ) -> Tuple[int, Tuple[int, int], int, int, int, int, int]:
        self._entity_preamble(pid=0)  # CSkpStyleManager CEntity preamble
        self._u32(1)                 # saved style count = 1

        style_object_tail_rel = len(self.buf) - tail_pos
        saved_slot, saved_guid_off, saved_items_off = self._write_skp_style(
            name="Style",
            description="",
            guid=_DEFAULT_SAVED_STYLE_GUID,
            pid=_SAVED_STYLE_PID,
        )

        selected_ref_tail_rel = len(self.buf) - tail_pos
        self._backref(saved_slot)    # selected saved style back-ref

        working_ref_tail_rel = len(self.buf) - tail_pos
        _, working_guid_off, working_items_off = self._write_skp_style(
            name="Style",
            description="",
            guid=_DEFAULT_WORKING_STYLE_GUID,
            pid=0,
        )

        self._u8(0)                  # active_style_changed = False
        return (
            style_object_tail_rel,
            (selected_ref_tail_rel, working_ref_tail_rel),
            saved_guid_off - tail_pos,
            working_guid_off - tail_pos,
            saved_items_off - tail_pos,
            working_items_off - tail_pos,
            len(_DEFAULT_STYLE_PROPERTIES),
        )

    def write_watermark_manager_and_trailer(self) -> None:
        self._entity_preamble(pid=0)  # CWatermarkManager CEntity preamble
        self._u32(0)                 # watermark_count = 0
        self._u32(0)                 # watermark flags / selection = 0
        self._u8(1)                  # display_watermarks = True
        self._u32(0)                 # closing model trailer = 0


def build_blank_v17() -> BlankDocument:
    """Build the empty classic SketchUp 2017 (v17) document and return its
    serialized bytes alongside every splice offset and slot index."""
    b = _BlankArchiveBuilder()
    model_guid_offset = b.write_file_header()
    b.write_version_map()
    pid_counter_offset = b.write_model_preamble()
    unit_option_offsets = b.write_options_providers()
    b.write_model_properties()
    camera_body_offset, camera_body_len = b.write_model_camera()
    ro_offset, ro_len, ro_fields = b.write_rendering_options()
    mat_count_pos, mat_insert_pos, mat_base_slot = b.write_component_and_material_manager()
    (
        layer_count_pos,
        orig_layer_count,
        layer_insert_pos,
        layer_writer_base_slot,
        active_layer_ref_pos,
        scaffold_class_slot,
    ) = b.write_layer_manager()
    (
        def_count_pos,
        orig_def_count,
        root_count_pos,
        orig_root_count,
        tail_pos,
        scaffold_next_slot,
    ) = b.write_definition_and_root_lists()
    shadow_tail_rel, shadow_len, page_list_tail_rel = b.write_shadow_info(tail_pos)
    b.write_page_list()
    b.write_model_axes()
    arial_slot = b.write_dimension_style()
    font_tail_refs = b.write_text_style_and_font_manager(tail_pos, arial_slot)
    (
        style_object_tail_rel,
        style_tail_refs,
        saved_guid_tail_rel,
        working_guid_tail_rel,
        saved_items_tail_rel,
        working_items_tail_rel,
        style_item_count,
    ) = b.write_style_manager(tail_pos)
    b.write_watermark_manager_and_trailer()

    return BlankDocument(
        data=bytes(b.buf),
        model_guid_offset=model_guid_offset,
        pid_counter_offset=pid_counter_offset,
        next_pid=_INITIAL_NEXT_PERSISTENT_ID,
        unit_option_offsets=unit_option_offsets,
        attr_container_slot=b.class_slot["CAttributeContainer"],
        attribute_named_slot=b.class_slot["CAttributeNamed"],
        ccamera_slot=b.class_slot["CCamera"],
        camera_body_offset=camera_body_offset,
        camera_body_len=camera_body_len,
        rendering_options_offset=ro_offset,
        rendering_options_len=ro_len,
        rendering_option_fields=ro_fields,
        material_count_pos=mat_count_pos,
        material_insert_pos=mat_insert_pos,
        material_base_slot=mat_base_slot,
        layer_count_pos=layer_count_pos,
        orig_layer_count=orig_layer_count,
        layer_insert_pos=layer_insert_pos,
        layer_writer_base_slot=layer_writer_base_slot,
        active_layer_ref_pos=active_layer_ref_pos,
        def_count_pos=def_count_pos,
        orig_def_count=orig_def_count,
        root_count_pos=root_count_pos,
        orig_root_count=orig_root_count,
        tail_pos=tail_pos,
        scaffold_next_slot=scaffold_next_slot,
        scaffold_class_slot=scaffold_class_slot,
        shadow_info_tail_rel=shadow_tail_rel,
        shadow_info_len=shadow_len,
        page_list_tail_rel=page_list_tail_rel,
        font_tail_refs=font_tail_refs,
        style_object_tail_rel=style_object_tail_rel,
        style_tail_refs=style_tail_refs,
        tail_ref_positions=font_tail_refs + style_tail_refs,
        saved_style_guid_tail_rel=saved_guid_tail_rel,
        working_style_guid_tail_rel=working_guid_tail_rel,
        saved_style_items_tail_rel=saved_items_tail_rel,
        working_style_items_tail_rel=working_items_tail_rel,
        style_item_count=style_item_count,
    )
