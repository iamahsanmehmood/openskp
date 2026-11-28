"""Construct an empty VFF-format (SketchUp 2021+) ``.skp`` document in code.

Every component of the empty VFF container — the 69-byte file header,
``meta/meta.dat``, ``materials/Layer_Layer0/material.xml``, the saved and
working ``style.xml`` entries, OpenSKP's own minimal placeholder thumbnail PNG,
``model.dat``'s 27 top-level TLV sections, and the deterministic ZIP archive —
is written field by field through named builder methods, named TLV tag
constants, and named ``(name, type, value)`` setting tables. No bundled binary
file, rendered SketchUp thumbnail, raw XML template string, or hex/base64 blob
is read or embedded.
"""
from __future__ import annotations

import dataclasses
import struct
import uuid
import zlib
from typing import List, Sequence, Tuple
from xml.sax.saxutils import escape as _xml_escape

from ._vff_writer.archive import ZipEntry, build_skp
from ._vff_writer.tlv import Record, encode_records, f64s, record, u8, u32, varint

Point3 = Tuple[float, float, float]
RGBA = Tuple[int, int, int, int]

# ── VFF header framing constants ─────────────────────────────────────────

_UTF16_STRING_MARKER = b"\xff\xfe\xff"
_VFF_TRAILER_MAGIC = b"VFF"
_VFF_TRAILER_PAYLOAD_BYTES = 8  # u16 container_version + u16 model_revision + u32 integrity_word
_VFF_CONTAINER_VERSION = 1

_FILE_MAGIC_TEXT = "SketchUp Model"
_DEFAULT_VERSION_STRING = "{25.0.571}"
_DEFAULT_APP_VERSION = "25.0.571"
_DEFAULT_RELEASE_YEAR_CODE = 24
_DEFAULT_RELEASE_CHANNEL = 2
_DEFAULT_MODEL_REVISION = 17

# ── Default identifiers, names, and timestamps ───────────────────────────

_DEFAULT_MODEL_GUID = "ccc9e7ac-a198-46ae-b942-b609057c057e"
_DEFAULT_SAVED_PATH = "C:/Temp/vff/blank.skp"
_DEFAULT_CLIENT_APP = "SketchUp Client (Windows) 25.0.571"
_DEFAULT_SAVE_TIMESTAMP_UNIX = 1795500289  # 2026-11-24T06:04:49Z (time_t)
_DEFAULT_HISTORY_SESSION_TOKEN = 0x778B43B4C931B848
_DEFAULT_UNITS_NAME = "Inches"

_INITIAL_NEXT_PERSISTENT_ID = 41203  # 0xA0F3

_DEFAULT_LAYER_NAME = "Layer0"
_DEFAULT_LAYER_PID = 13869           # 0x362D
_DEFAULT_LAYER_MATERIAL_PID = 16049  # 0x3EB1
_DEFAULT_LAYER0_COLOR_RGB: Tuple[int, int, int] = (255, 255, 255)
_DEFAULT_NEW_LAYER_COLOR_RGB: Tuple[int, int, int] = (136, 136, 136)

_DEFAULT_FONT_NAME = "Tahoma"
_DEFAULT_FONT_PID = 16047            # 0x3EAF
_DEFAULT_FONT_POINT_SIZE = 12
_DEFAULT_FONT_WORLD_SIZE = 10.0

_DEFAULT_STYLE_NAME = "[Simple Style]"
_DEFAULT_STYLE_DESCRIPTION = "[Default face colors. Blue sky and gray background color.]"
_DEFAULT_STYLE_PID = 16050           # 0x3EB2
_DEFAULT_STYLE_GUID = "89cb0288-1dbb-432d-a638-fcd642799541"

_MODEL_THUMBNAIL_ENTRY = "meta/model_thumbnail.png"
_PREVIEW_THUMBNAIL_ENTRY = "meta/preview_thumbnail.png"
_META_DAT_ENTRY = "meta/meta.dat"
_MODEL_DAT_ENTRY = "model.dat"
_META_IMAGE_FORMAT_PNG = 4

# Default camera parameters in the empty 2025 VFF document (inches, degrees).
_DEFAULT_CAMERA_EYE: Point3 = (
    189.18162001962057,
    -240.1043324491603,
    74.01764083499498,
)
_DEFAULT_CAMERA_TARGET: Point3 = (
    -332.7888510540576,
    565.0165468333703,
    -38.71823019085756,
)
_DEFAULT_CAMERA_UP: Point3 = (
    -0.06347835297529261,
    0.09791310082684807,
    0.9931684264967428,
)
_DEFAULT_CAMERA_CLIP_NEAR = 1.0
_DEFAULT_CAMERA_CLIP_FAR = 1000.0
_DEFAULT_CAMERA_PERSPECTIVE = True
_DEFAULT_CAMERA_FOV = 35.0
_DEFAULT_CAMERA_ORTHO_HEIGHT = 133.0
_DEFAULT_CAMERA_ASPECT_RATIO = 0.0
_DEFAULT_CAMERA_FOV_IS_HEIGHT = True
_DEFAULT_CAMERA_IMAGE_WIDTH = 0.0
_DEFAULT_CAMERA_IS_2D = False
_DEFAULT_CAMERA_SCALE_2D = 1.0
_DEFAULT_CAMERA_CENTER_2D: Tuple[float, float] = (0.0, 0.0)

# Default shadow / geo-location settings ("Boulder (CO)", "USA").
_DEFAULT_SHADOW_TIME_UNIX = 1794144600  # 2026-11-08T13:30:00Z
_DEFAULT_CITY = "Boulder (CO)"
_DEFAULT_COUNTRY = "USA"
_DEFAULT_LONGITUDE = -105.242139
_DEFAULT_LATITUDE = 40.018309
_DEFAULT_TZ_OFFSET = -7.0
_DEFAULT_NORTH_VECTOR: Point3 = (0.0, 1.0, 0.0)
_DEFAULT_SHADOW_LIGHT = 80
_DEFAULT_SHADOW_DARK = 45

# ── TLV Tags (16-bit little-endian integers; file order = low byte first) ─

# meta/meta.dat tags
T_META = 0x0064
T_META_GUID = 0x0066
T_META_MODEL_THUMBNAIL = 0x0067
T_META_PREVIEW_THUMBNAIL = 0x0068
T_META_HAS_THUMBNAIL = 0x0069
T_META_MODEL_NAME = 0x006A
T_META_MODEL_DESC = 0x006B
T_META_AUTHOR = 0x006C
T_META_UNITS = 0x006D
T_META_SOURCE = 0x006E
T_META_SAVED_PATH = 0x006F
T_META_SHOW_UNITS = 0x0070
T_META_GEOREFERENCED = 0x0071
T_META_DYNAMIC_FLAG = 0x0072
T_META_CONTAINER_VER = 0x0073
T_META_MODEL_REVISION = 0x0074
T_META_APP_VERSION = 0x0075
T_META_RELEASE_YEAR = 0x0076
T_META_RELEASE_CHANNEL = 0x0077
T_META_HISTORY = 0x0078
T_META_CLASSIFIED = 0x0079
T_META_HISTORY_INNER = 0x00C8
T_META_HISTORY_ENTRY = 0x00C9
T_META_HISTORY_ITEM = 0x00CA
T_META_HISTORY_APP = 0x00CB
T_META_HISTORY_TIME = 0x00CC
T_META_HISTORY_TOKEN = 0x00CD
T_META_FILE_REF = 0x2328
T_META_FILE_FORMAT = 0x2329
T_META_FILE_PATH = 0x232A

# model.dat top-level & shared structural tags
T_DOC_ZERO_WORD = 0x0063
T_DOCUMENT = 0x01F4
T_NEXT_ID = 0x01F5
T_ENTITIES = 0x01F6
T_MATERIALS = 0x01F7
T_LAYERS = 0x01F8
T_DEFINITIONS = 0x01F9
T_CAMERA_MODEL = 0x01FA
T_RENDERING = 0x01FB
T_AXES = 0x01FC
T_FONTS = 0x01FD
T_TEXT_STYLE = 0x01FE
T_DIMENSION_STYLE = 0x01FF
T_OPTIONS_MODEL = 0x0200
T_PREVIEW_DIB = 0x0201
T_CLASSIFICATIONS = 0x0203
T_SHADOWS = 0x0204
T_WATERMARKS = 0x0205
T_STYLES = 0x0206
T_PAGES_MODEL = 0x0207
T_LINE_STYLES = 0x0208
T_MODEL_PROPS = 0x0209
T_META_PATH = 0x020A
T_MODEL_FLAG_BYTE = 0x020C
T_MODEL_FEATURE_MASK = 0x020D
T_MODEL_BEHAVIOUR = 0x020E
T_MODEL_TRAILER = 0x020F
T_OVERLAYS = 0x0210

T_NEXT_ID_WRAP = 0x03E8
T_NEXT_ID_VAL = 0x03E9

T_ENTITY_BASE = 0x05DC
T_ATTR_BLOCK = 0x05DD
T_ENTITY_ID = 0x05DE
T_DRAWING = 0x07D0
T_FLAGS = 0x07D3

# Root entities / definitions / relationships / behaviour
T_DEFINITION_CONTAINER = 0x1388
T_RELATIONSHIPS = 0x139B
T_EMPTY = 0x139E
T_FLAGS_CONTAINER = 0x139F
T_DEFINITION_SECTION = 0x1770
T_DEFINITION_LIST = 0x1771
T_DEFINITION_BEHAVIOUR = 0x1B58
T_BEHAVIOUR_SNAP_TO = 0x1B59
T_BEHAVIOUR_NO_SCALE = 0x1B5A
T_BEHAVIOUR_CUTS_OPENING = 0x1B5B
T_BEHAVIOUR_IS_2D = 0x1B5C
T_DEFINITION_ALWAYS_FACE_CAMERA = 0x1B5D
T_DEFINITION_SHADOWS_FACE_SUN = 0x1B5E

# Classifications
T_SCHEMA_MGR = 0x2CEC
T_SCHEMA_LIST = 0x2CED
T_SCHEMA_FLAGS = 0x2CEE

# Materials & Layers
T_MATERIAL_SECTION = 0x30D4
T_MATERIAL_LIST = 0x30D5
T_MATERIAL = 0x32C8
T_MATERIAL_OWNER = 0x32CA
T_MATERIAL_EMPTY = 0x32CB
T_MATERIAL_NAME = 0x32CC

T_LAYER_SECTION = 0x3A98
T_LAYER_LIST = 0x3A99
T_LAYER_ACTIVE = 0x3A9A
T_LAYER_FOLDERS = 0x3A9B
T_LAYER = 0x3C8C
T_LAYER_NAME = 0x3C8D
T_LAYER_HIDDEN = 0x3C8E
T_LAYER_MATERIAL = 0x3C8F
T_LAYER_TAIL = 0x3C90
T_LAYER_FOLDER_ROOT = 0x3E80
T_LAYER_FOLDER_NAME = 0x3E81
T_LAYER_FOLDER_VIS = 0x3E82
T_LAYER_FOLDER_SUBS = 0x3E83
T_LAYER_FOLDER_ITEMS = 0x3E84
T_LAYER_FOLDER_TAIL = 0x3E85

# Attribute dictionaries & OptionsManager
T_ATTR_EMPTY = 0x36B0
T_ATTR_LIST = 0x36B1
T_ATTR_SET = 0x36B2
T_ATTR_DICT = 0x36B3
T_ATTR_NAME = 0x36B4
T_ATTR_ENTRIES = 0x36B5
T_ATTR_KEY = 0x36B6
T_ATTR_TRAILER = 0x36B7

T_VALUE = 0x38A4
T_VALUE_INT = 0x38A7
T_VALUE_DOUBLE = 0x38A9
T_VALUE_BOOL = 0x38AA
T_VALUE_STRING = 0x38AD

T_OPTIONS_MGR = 0x61A8
T_OPTIONS_PROVIDERS = 0x61A9
T_OPTIONS_PROVIDER = 0x61AA
T_OPTIONS_PROV_NAME = 0x61AB
T_OPTIONS_PROV_ENTRIES = 0x61AC
T_OPTIONS_KEY = 0x61AD

# Camera (0x34BC)
T_CAMERA = 0x34BC
T_CAMERA_EYE = 0x34BD
T_CAMERA_TARGET = 0x34BE
T_CAMERA_UP = 0x34BF
T_CAMERA_CLIP_NEAR = 0x34C0
T_CAMERA_CLIP_FAR = 0x34C1
T_CAMERA_PERSPECTIVE = 0x34C2
T_CAMERA_HEIGHT = 0x34C3
T_CAMERA_FOV = 0x34C4
T_CAMERA_ASPECT_RATIO = 0x34C5
T_CAMERA_FOV_IS_HEIGHT = 0x34C6
T_CAMERA_WINDOW_FLAG = 0x34C7
T_CAMERA_DESCRIPTION = 0x34C8
T_CAMERA_IMAGE_WIDTH = 0x34C9
T_CAMERA_IS_2D = 0x34CA
T_CAMERA_SCALE_2D = 0x34CB
T_CAMERA_CENTER_2D_X = 0x34CC
T_CAMERA_CENTER_2D_Y = 0x34CD
T_CAMERA_TAIL_FLAG = 0x34CE

# RenderingOptions (0x733C)
T_RO_CONTAINER = 0x733C
T_RO_RENDER_MODE = 0x733D
T_RO_MODEL_TRANSPARENCY = 0x733E
T_RO_MATERIAL_TRANSPARENCY = 0x733F
T_RO_TEXTURE = 0x7340
T_RO_EDGE_DISPLAY_MODE = 0x7341
T_RO_EDGE_TYPE = 0x7342
T_RO_DISPLAY_SKETCH_AXES = 0x7343
T_RO_DISPLAY_TEXT = 0x7344
T_RO_DISPLAY_DIMS = 0x7345
T_RO_HIDE_CONSTRUCTION = 0x7346
T_RO_DISPLAY_COLOR_BY_LAYER = 0x7347
T_RO_EDGE_COLOR_MODE = 0x7348
T_RO_DISPLAY_INSTANCE_AXES = 0x7349
T_RO_JITTER_EDGES = 0x734B
T_RO_EDGE_HALO_FLAG = 0x734C
T_RO_EXTEND_LINES = 0x734D
T_RO_LINE_EXTENSION = 0x734E
T_RO_DRAW_SILHOUETTES = 0x734F
T_RO_SILHOUETTE_WIDTH = 0x7350
T_RO_DRAW_DEPTH_QUE = 0x7351
T_RO_DEPTH_QUE_WIDTH = 0x7352
T_RO_DRAW_LINE_ENDS = 0x7353
T_RO_LINE_END_WIDTH = 0x7354
T_RO_DRAW_PROFILES_ONLY = 0x7355
T_RO_DRAW_BACK_EDGES = 0x7356
T_RO_BACKGROUND_COLOR = 0x7357
T_RO_FOREGROUND_COLOR = 0x7358
T_RO_HIGHLIGHT_COLOR = 0x7359
T_RO_LOCKED_COLOR = 0x735A
T_RO_CONSTRUCTION_COLOR = 0x735B
T_RO_FACE_FRONT_COLOR = 0x735C
T_RO_FACE_BACK_COLOR = 0x735D
T_RO_DISPLAY_WATERMARKS = 0x735E
T_RO_DISPLAY_FOG = 0x735F
T_RO_FOG_COLOR = 0x7360
T_RO_FOG_USE_BK_COLOR = 0x7361
T_RO_FOG_START_DIST = 0x7362
T_RO_FOG_END_DIST = 0x7363
T_RO_STROKE_MODE = 0x7364
T_RO_SKY_COLOR = 0x7365
T_RO_HORIZON_COLOR = 0x7366
T_RO_GROUND_COLOR = 0x7367
T_RO_DRAW_HORIZON = 0x7368
T_RO_DRAW_GROUND = 0x7369
T_RO_DRAW_UNDERGROUND = 0x736A
T_RO_GROUND_TRANSPARENCY = 0x736B
T_RO_INACTIVE_FADE = 0x736C
T_RO_INSTANCE_FADE = 0x736D
T_RO_INACTIVE_HIDDEN = 0x736E
T_RO_INSTANCE_HIDDEN = 0x736F
T_RO_SECTION_ACTIVE_COLOR = 0x7370
T_RO_SECTION_INACTIVE_COLOR = 0x7371
T_RO_SECTION_DEFAULT_CUT_COLOR = 0x7372
T_RO_SECTION_DEFAULT_FILL_COLOR = 0x7373
T_RO_SECTION_CUT_WIDTH = 0x7374
T_RO_SECTION_DISPLAY_MODE = 0x7375
T_RO_SECTION_CUT_FILLED = 0x7376
T_RO_TRANSPARENCY_SORT = 0x7377
T_RO_TRANSPARENCY_FACTOR = 0x7378
T_RO_SECTION_CUT_DRAW_EDGES = 0x7379
T_RO_CREASE_ANGLE_RAD = 0x737A
T_RO_SHOW_VIEW_NAME = 0x737B
T_RO_MATCH_PHOTO_BG_ENABLE = 0x737C
T_RO_MATCH_PHOTO_BG_OPACITY = 0x737D
T_RO_MATCH_PHOTO_FG_ENABLE = 0x737E
T_RO_MATCH_PHOTO_FG_OPACITY = 0x737F
T_RO_DRAW_HIDDEN_GEOMETRY = 0x7380
T_RO_DRAW_HIDDEN_OBJECTS = 0x7381
T_RO_AO_AUTO_DISTANCE = 0x7382
T_RO_AMBIENT_OCCLUSION = 0x7383
T_RO_AO_DISTANCE = 0x7385
T_RO_AO_INTENSITY = 0x7386

# ShadowInfo (0x6590)
T_SHADOW_CONTAINER = 0x6590
T_SHADOW_TIME = 0x6591
T_SHADOW_DAYLIGHT_SAVINGS = 0x6592
T_SHADOW_CITY = 0x6593
T_SHADOW_COUNTRY = 0x6594
T_SHADOW_LONGITUDE = 0x6595
T_SHADOW_LATITUDE = 0x6596
T_SHADOW_TZ_OFFSET = 0x6597
T_SHADOW_NORTH_VEC = 0x6598
T_SHADOW_DISPLAY_SHADOWS = 0x6599
T_SHADOW_DISPLAY_NORTH = 0x659A
T_SHADOW_ON_ALL_FACES = 0x659B
T_SHADOW_ON_GROUND = 0x659C
T_SHADOW_EDGES_CAST = 0x659D
T_SHADOW_LIGHT = 0x659E
T_SHADOW_DARK = 0x659F
T_SHADOW_SUN_SHADING = 0x65A0

# Axes (0x4650)
T_AXES_CONTAINER = 0x4650
T_AXES_ORIGIN = 0x4651
T_AXES_X = 0x4652
T_AXES_Y = 0x4653
T_AXES_Z = 0x4654

# LineStyles (0x4074)
T_LINESTYLE_MGR = 0x4074
T_LINESTYLE_LIST = 0x4075
T_LINESTYLE = 0x4076
T_LINESTYLE_NAME = 0x4077
T_LINESTYLE_PATTERN = 0x4078
T_LINESTYLE_WIDTH_SCALE = 0x4079
T_LINESTYLE_LENGTH_SCALE = 0x407A
T_LINESTYLE_TYPE = 0x407B
T_LINESTYLE_USER_FLAG = 0x407C

# Fonts (0x4E20), TextStyle (0x57E4), DimensionStyle (0x5FB4)
T_FONT_MGR = 0x4E20
T_FONT_LIST = 0x4E21
T_FONT = 0x5014
T_FONT_NAME = 0x5015
T_FONT_ITALIC = 0x5016
T_FONT_BOLD = 0x5017
T_FONT_POINT_SIZE = 0x5018
T_FONT_USE_WORLD_SIZE = 0x5019
T_FONT_WORLD_SIZE = 0x501A

T_TEXT_STYLE_CONTAINER = 0x57E4
T_TEXT_STYLE_FONT_A = 0x57E5
T_TEXT_STYLE_FONT_B = 0x57E6
T_TEXT_STYLE_ARROW = 0x57E7
T_TEXT_STYLE_ALIGN = 0x57E8
T_TEXT_STYLE_FLAG = 0x57E9
T_TEXT_STYLE_LEADER_TYPE = 0x57EA
T_TEXT_STYLE_DISPLAY_LEADER = 0x57EB
T_TEXT_STYLE_COLOR = 0x57EC
T_TEXT_STYLE_LEADER_COLOR = 0x57ED

T_DIM_STYLE_CONTAINER = 0x5FB4
T_DIM_STYLE_FONT = 0x5FB5
T_DIM_STYLE_HAS_3D = 0x5FB6
T_DIM_STYLE_SHOW_UNITS = 0x5FB7
T_DIM_STYLE_EXT_OFFSET = 0x5FB8
T_DIM_STYLE_EXT_OVERSHOOT = 0x5FB9
T_DIM_STYLE_LINE_WEIGHT = 0x5FBA
T_DIM_STYLE_ARROW_TYPE = 0x5FBB
T_DIM_STYLE_ARROW_SIZE = 0x5FBC
T_DIM_STYLE_HL_NONASSOC = 0x5FBD
T_DIM_STYLE_HL_COLOR = 0x5FBE
T_DIM_STYLE_RADIAL_PREFIX = 0x5FBF
T_DIM_STYLE_HIDE_OUT_OF_PLANE = 0x5FC0
T_DIM_STYLE_HIDE_OOP_VAL = 0x5FC1
T_DIM_STYLE_HIDE_SMALL = 0x5FC2
T_DIM_STYLE_HIDE_SMALL_VAL = 0x5FC3
T_DIM_STYLE_COLOR = 0x5FC4
T_DIM_STYLE_TEXT_COLOR = 0x5FC5
T_DIM_STYLE_ALIGN_MODE = 0x5FC6

# Relationships, Styles, Overlays, Pages
T_RELATIONSHIP_GROUP = 0x639F
T_RELATIONSHIP_LIST = 0x63A0

T_STYLE_MGR = 0x6978
T_STYLE_LIST = 0x6979
T_STYLE_SELECTED = 0x697A
T_STYLE_WORKING = 0x697B
T_STYLE_CHANGED = 0x697C
T_STYLE = 0x6B6C
T_STYLE_GUID = 0x6B6D
T_STYLE_PATH = 0x6B6E
T_STYLE_NAME = 0x6B6F
T_STYLE_DESC = 0x6B70

T_PAGE_LIST = 0x6D60
T_PAGE_ITEMS = 0x6D61

T_OVERLAY_MGR = 0x7918
T_OVERLAY_LIST = 0x7919


# ── Primitive encoders ───────────────────────────────────────────────────


def _encode_utf16_str(text: str) -> bytes:
    """Encode ``text`` with SketchUp's ``FF FE FF`` UTF-16LE header framing."""
    raw = text.encode("utf-16-le")
    code_units = len(raw) // 2
    if code_units < 0xFF:
        return _UTF16_STRING_MARKER + struct.pack("<B", code_units) + raw
    if code_units < 0xFFFF:
        return _UTF16_STRING_MARKER + b"\xff" + struct.pack("<H", code_units) + raw
    return _UTF16_STRING_MARKER + b"\xff\xff\xff" + struct.pack("<I", code_units) + raw


def _pack_rgba_u32(r: int, g: int, b: int, a: int = 255) -> int:
    """Pack ``(r, g, b, a)`` into an unsigned 32-bit ABGR word ``0xAABBGGRR``."""
    return (r & 0xFF) | ((g & 0xFF) << 8) | ((b & 0xFF) << 16) | ((a & 0xFF) << 24)


def _pack_rgba_i32(r: int, g: int, b: int, a: int = 255) -> int:
    """Pack ``(r, g, b, a)`` into the signed 32-bit ABGR integer ``style.xml`` stores."""
    word = _pack_rgba_u32(r, g, b, a)
    return word - (1 << 32) if word >= (1 << 31) else word


def _pack_rgba_bytes(rgba: RGBA) -> bytes:
    return struct.pack("<I", _pack_rgba_u32(*rgba))


def _f32(value: float) -> bytes:
    return struct.pack("<f", float(value))


def _u16(value: int) -> bytes:
    return struct.pack("<H", value & 0xFFFF)


def _u64(value: int) -> bytes:
    return struct.pack("<Q", value & 0xFFFFFFFFFFFFFFFF)


def _xml_attr(value: str) -> str:
    return _xml_escape(value, {'"': "&quot;", "'": "&apos;"})


def _sanitize_archive_folder(name: str) -> str:
    unsafe = set('*<>:|?/\\"\t\n\r')
    cleaned = "".join("_" if ch in unsafe else ch for ch in name)
    return cleaned or "_"


def build_plain_png(
    width: int = 1,
    height: int = 1,
    rgba: RGBA = (255, 255, 255, 0),
) -> bytes:
    """Build OpenSKP's own minimal valid RGBA PNG image in code via ``struct`` and ``zlib``.

    This image is generated from scratch by OpenSKP (1x1 transparent pixel by
    default) — zero bytes come from any SketchUp-rendered thumbnail.
    """
    def _png_chunk(chunk_type: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(chunk_type + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + chunk_type + body + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    scanline = b"\x00" + bytes(c & 0xFF for c in rgba) * width
    idat = zlib.compress(scanline * height, 9)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


# ── Named setting tables ─────────────────────────────────────────────────

# Value-type tags for named setting tables.
_VAL_BOOL = "bool"
_VAL_INT32 = "i32"
_VAL_UINT32 = "u32"
_VAL_FLOAT32 = "f32"
_VAL_FLOAT64 = "f64"
_VAL_STRING = "str"
_VAL_RGBA = "rgba"

# 1. ModelProperties attribute dictionary (0x0209): (name, type, value)
_DEFAULT_MODEL_PROPERTIES: Tuple[Tuple[str, str, object], ...] = (
    ("Description", _VAL_STRING, ""),
    ("IsClassified", _VAL_BOOL, False),
    ("IsDynamic", _VAL_BOOL, False),
    ("IsLive", _VAL_BOOL, False),
    ("Name", _VAL_STRING, ""),
)

# 2. SUOptionsManager providers (0x0200): (provider_name, ((key, type, value), ...))
_DEFAULT_OPTIONS_PROVIDERS: Tuple[Tuple[str, Tuple[Tuple[str, str, object], ...]], ...] = (
    (
        "UnitsOptions",
        (
            ("LengthPrecision", _VAL_INT32, 4),
            ("LengthFormat", _VAL_INT32, 1),          # 1 = Architectural
            ("LengthUnit", _VAL_INT32, 0),            # 0 = Inches
            ("LengthSnapEnabled", _VAL_BOOL, False),
            ("LengthSnapLength", _VAL_FLOAT64, 0.0625),
            ("AnglePrecision", _VAL_INT32, 1),
            ("AngleSnapEnabled", _VAL_BOOL, True),
            ("SnapAngle", _VAL_FLOAT64, 15.0),
            ("SuppressUnitsDisplay", _VAL_BOOL, False),
            ("ForceInchDisplay", _VAL_BOOL, False),
            ("AreaUnit", _VAL_INT32, 0),
            ("VolumeUnit", _VAL_INT32, 0),
            ("AreaPrecision", _VAL_INT32, 2),
            ("VolumePrecision", _VAL_INT32, 2),
        ),
    ),
    (
        "PageOptions",
        (
            ("ShowTransition", _VAL_BOOL, True),
            ("TransitionTime", _VAL_FLOAT64, 2.0),
        ),
    ),
    (
        "SlideshowOptions",
        (
            ("LoopSlideshow", _VAL_BOOL, True),
            ("SlideTime", _VAL_FLOAT64, 1.0),
        ),
    ),
    ("NamedOptions", ()),
    ("PrintOptions", ()),
)

# 3. CRenderingOptions (0x01FB > 0x733C): (tag, name, type, value) in exact record order.
_DEFAULT_RENDERING_OPTIONS: Tuple[Tuple[int, str, str, object], ...] = (
    (T_RO_RENDER_MODE, "RenderMode", _VAL_UINT32, 2),
    (T_RO_MODEL_TRANSPARENCY, "ModelTransparency", _VAL_BOOL, False),
    (T_RO_MATERIAL_TRANSPARENCY, "MaterialTransparency", _VAL_BOOL, True),
    (T_RO_TEXTURE, "Texture", _VAL_BOOL, True),
    (T_RO_EDGE_DISPLAY_MODE, "EdgeDisplayMode", _VAL_UINT32, 1),
    (T_RO_EDGE_TYPE, "EdgeType", _VAL_UINT32, 0),
    (T_RO_DISPLAY_SKETCH_AXES, "DisplaySketchAxes", _VAL_BOOL, True),
    (T_RO_DISPLAY_TEXT, "DisplayText", _VAL_BOOL, True),
    (T_RO_DISPLAY_DIMS, "DisplayDims", _VAL_BOOL, True),
    (T_RO_HIDE_CONSTRUCTION, "HideConstructionGeometry", _VAL_BOOL, False),
    (T_RO_DISPLAY_COLOR_BY_LAYER, "DisplayColorByLayer", _VAL_BOOL, False),
    (T_RO_EDGE_COLOR_MODE, "EdgeColorMode", _VAL_UINT32, 1),
    (T_RO_DISPLAY_INSTANCE_AXES, "DisplayInstanceAxes", _VAL_BOOL, False),
    (T_RO_DRAW_HIDDEN_GEOMETRY, "DrawHiddenGeometry", _VAL_BOOL, False),
    (T_RO_DRAW_HIDDEN_OBJECTS, "DrawHiddenObjects", _VAL_BOOL, False),
    (T_RO_JITTER_EDGES, "JitterEdges", _VAL_BOOL, False),
    (T_RO_EDGE_HALO_FLAG, "EdgeHaloFlag", _VAL_BOOL, True),
    (T_RO_EXTEND_LINES, "ExtendLines", _VAL_BOOL, False),
    (T_RO_LINE_EXTENSION, "LineExtension", _VAL_UINT32, 3),
    (T_RO_DRAW_SILHOUETTES, "DrawSilhouettes", _VAL_BOOL, True),
    (T_RO_SILHOUETTE_WIDTH, "SilhouetteWidth", _VAL_UINT32, 2),
    (T_RO_DRAW_DEPTH_QUE, "DrawDepthQue", _VAL_BOOL, False),
    (T_RO_DEPTH_QUE_WIDTH, "DepthQueWidth", _VAL_UINT32, 4),
    (T_RO_DRAW_LINE_ENDS, "DrawLineEnds", _VAL_BOOL, False),
    (T_RO_LINE_END_WIDTH, "LineEndWidth", _VAL_UINT32, 9),
    (T_RO_DRAW_PROFILES_ONLY, "DrawProfilesOnly", _VAL_BOOL, False),
    (T_RO_DRAW_BACK_EDGES, "DrawBackEdges", _VAL_BOOL, False),
    (T_RO_BACKGROUND_COLOR, "BackgroundColor", _VAL_RGBA, (191, 191, 198, 255)),
    (T_RO_FOREGROUND_COLOR, "ForegroundColor", _VAL_RGBA, (0, 0, 0, 255)),
    (T_RO_HIGHLIGHT_COLOR, "HighlightColor", _VAL_RGBA, (0, 1, 255, 255)),
    (T_RO_LOCKED_COLOR, "LockedColor", _VAL_RGBA, (255, 0, 0, 255)),
    (T_RO_CONSTRUCTION_COLOR, "ConstructionColor", _VAL_RGBA, (0, 0, 0, 255)),
    (T_RO_FACE_FRONT_COLOR, "FaceFrontColor", _VAL_RGBA, (255, 255, 255, 255)),
    (T_RO_FACE_BACK_COLOR, "FaceBackColor", _VAL_RGBA, (162, 183, 198, 255)),
    (T_RO_DISPLAY_WATERMARKS, "DisplayWatermarks", _VAL_BOOL, True),
    (T_RO_DISPLAY_FOG, "DisplayFog", _VAL_BOOL, False),
    (T_RO_FOG_COLOR, "FogColor", _VAL_RGBA, (0, 0, 0, 0)),
    (T_RO_FOG_USE_BK_COLOR, "FogUseBkColor", _VAL_BOOL, True),
    (T_RO_FOG_START_DIST, "FogStartDist", _VAL_FLOAT64, -1.0),
    (T_RO_FOG_END_DIST, "FogEndDist", _VAL_FLOAT64, -1.0),
    (T_RO_STROKE_MODE, "StrokeMode", _VAL_UINT32, 1),
    (T_RO_SKY_COLOR, "SkyColor", _VAL_RGBA, (0, 154, 217, 255)),
    (T_RO_HORIZON_COLOR, "HorizonColor", _VAL_RGBA, (0, 0, 0, 0)),
    (T_RO_GROUND_COLOR, "GroundColor", _VAL_RGBA, (151, 170, 136, 255)),
    (T_RO_DRAW_HORIZON, "DrawHorizon", _VAL_BOOL, True),
    (T_RO_DRAW_GROUND, "DrawGround", _VAL_BOOL, False),
    (T_RO_DRAW_UNDERGROUND, "DrawUnderground", _VAL_BOOL, True),
    (T_RO_GROUND_TRANSPARENCY, "GroundTransparency", _VAL_UINT32, 50),
    (T_RO_INACTIVE_FADE, "InactiveFade", _VAL_FLOAT64, 0.25),
    (T_RO_INSTANCE_FADE, "InstanceFade", _VAL_FLOAT64, 0.5),
    (T_RO_INACTIVE_HIDDEN, "InactiveHidden", _VAL_BOOL, False),
    (T_RO_INSTANCE_HIDDEN, "InstanceHidden", _VAL_BOOL, False),
    (T_RO_SECTION_ACTIVE_COLOR, "SectionActiveColor", _VAL_RGBA, (255, 135, 0, 255)),
    (T_RO_SECTION_INACTIVE_COLOR, "SectionInactiveColor", _VAL_RGBA, (112, 105, 97, 255)),
    (T_RO_SECTION_DEFAULT_CUT_COLOR, "SectionDefaultCutColor", _VAL_RGBA, (0, 0, 0, 255)),
    (T_RO_SECTION_DEFAULT_FILL_COLOR, "SectionDefaultFillColor", _VAL_RGBA, (63, 63, 63, 255)),
    (T_RO_SECTION_CUT_WIDTH, "SectionCutWidth", _VAL_UINT32, 3),
    (T_RO_SECTION_DISPLAY_MODE, "SectionDisplayMode", _VAL_UINT32, 2),
    (T_RO_SECTION_CUT_FILLED, "SectionCutFilled", _VAL_BOOL, True),
    (T_RO_TRANSPARENCY_SORT, "TransparencySort", _VAL_UINT32, 0),
    (T_RO_TRANSPARENCY_FACTOR, "TransparencyFactor", _VAL_FLOAT64, 0.65),
    (T_RO_SECTION_CUT_DRAW_EDGES, "SectionCutDrawEdges", _VAL_BOOL, True),
    (T_RO_CREASE_ANGLE_RAD, "CreaseAngleRad", _VAL_FLOAT64, 0.34906585039886595),
    (T_RO_SHOW_VIEW_NAME, "ShowViewName", _VAL_BOOL, True),
    (T_RO_MATCH_PHOTO_BG_ENABLE, "MatchPhotoBackgroundEnable", _VAL_BOOL, True),
    (T_RO_MATCH_PHOTO_BG_OPACITY, "MatchPhotoBackgroundOpacity", _VAL_FLOAT64, 1.0),
    (T_RO_MATCH_PHOTO_FG_ENABLE, "MatchPhotoForegroundEnable", _VAL_BOOL, True),
    (T_RO_MATCH_PHOTO_FG_OPACITY, "MatchPhotoForegroundOpacity", _VAL_FLOAT64, 0.8),
    (T_RO_AO_AUTO_DISTANCE, "AmbientOcclusionAutoDistanceOff", _VAL_BOOL, False),
    (T_RO_AMBIENT_OCCLUSION, "AmbientOcclusion", _VAL_BOOL, False),
    (T_RO_AO_DISTANCE, "AmbientOcclusionDistance", _VAL_FLOAT32, 10.0),
    (T_RO_AO_INTENSITY, "AmbientOcclusionIntensity", _VAL_FLOAT32, 0.0),
)

# 4. Built-in CLineStyle table (0x0208 > 0x4074 > 0x4075): (entity_id, name, stipple_pattern)
_DEFAULT_LINE_STYLES: Tuple[Tuple[int, str, str], ...] = (
    (13870, "Solid Basic", "16.0"),
    (13871, "Short dash", "6.0, -6.0"),
    (13872, "Dash", "12.0, -6.0"),
    (13873, "Dot", "1.0, -6.0"),
    (13874, "Dash dot", "12.0, -6.0, 1.0, -6.0"),
    (13875, "Dash double-dot", "12.0, -6.0, 1.0, -6.0, 1.0, -6.0"),
    (13876, "Dash triple-dot", "12.0, -6.0, 1.0, -6.0, 1.0, -6.0, 1.0, -6.0"),
    (13877, "Double-dash dot", "12.0, -6.0, 12.0, -6.0, 1.0, -6.0"),
    (13878, "Double-dash double-dot", "12.0, -6.0, 12.0, -6.0, 1.0, -6.0, 1.0, -6.0"),
    (13879, "Double-dash triple-dot", "12.0, -6.0, 12.0, -6.0, 1.0, -6.0, 1.0, -6.0, 1.0, -6.0"),
    (13880, "Long-dash dash", "36.0, -10.0, 12.0, -10.0"),
    (13881, "Long-dash double-dash", "36.0, -10.0, 12.0, -10.0, 12.0, -10.0"),
)

# 5. Root model attribute dictionaries in 0x01F6 > 0x1388 > 0x07D0 > 0x05DC > 0x05DD:
#    (dict_name, trailer_u32, ((key, type, value), ...))
_DEFAULT_ROOT_ATTRIBUTE_DICTS: Tuple[Tuple[str, int, Tuple[Tuple[str, str, object], ...]], ...] = (
    (
        "GeoReference",
        0,
        (
            ("GeoReferenceNorthAngle", _VAL_FLOAT64, 0.1557307335588798),
            ("Latitude", _VAL_FLOAT64, _DEFAULT_LATITUDE),
            ("LocationSource", _VAL_STRING, "Manual"),
            ("Longitude", _VAL_FLOAT64, _DEFAULT_LONGITUDE),
            ("ModelTranslationX", _VAL_FLOAT64, -18871519.616960514),
            ("ModelTranslationY", _VAL_FLOAT64, -174402260.58333763),
            ("UsesGeoReferencing", _VAL_BOOL, False),
        ),
    ),
    (
        "temp",
        0,
        (("temp", _VAL_INT32, 0),),
    ),
    (
        "GSU_ContributorsInfo",
        1,
        (
            ("LastModifiedByKey", _VAL_STRING, ""),
            ("VersionKey", _VAL_INT32, 1000),
        ),
    ),
)

# 6. material.xml named setting tables: (name, type, value)
_MATERIAL_NAMESPACE = "http://sketchup.google.com/schemas/sketchup/1.0/material"

_DEFAULT_LAYER0_MATERIAL_SETTINGS: Tuple[Tuple[str, str, object], ...] = (
    ("type", _VAL_INT32, 0),
    ("workflow", _VAL_INT32, 0),
    ("colorRed", _VAL_INT32, _DEFAULT_LAYER0_COLOR_RGB[0]),
    ("colorGreen", _VAL_INT32, _DEFAULT_LAYER0_COLOR_RGB[1]),
    ("colorBlue", _VAL_INT32, _DEFAULT_LAYER0_COLOR_RGB[2]),
    ("colorizeType", _VAL_INT32, 0),
    ("trans", _VAL_FLOAT64, 1.0),
    ("useTrans", _VAL_BOOL, False),
    ("pbrPromoState", _VAL_INT32, 0),
    ("hasTexture", _VAL_BOOL, False),
)

_DEFAULT_MATERIAL_PBR_ATTRS: Tuple[Tuple[str, str, object], ...] = (
    ("xScale", _VAL_FLOAT64, 1.0),
    ("yScale", _VAL_FLOAT64, 1.0),
)

_DEFAULT_MATERIAL_PBR_SETTINGS: Tuple[Tuple[str, str, object], ...] = (
    ("enable_metalness", _VAL_BOOL, False),
    ("enable_roughness", _VAL_BOOL, False),
    ("enable_normal", _VAL_BOOL, False),
    ("enable_occlusion", _VAL_BOOL, False),
    ("metallicFactor", _VAL_FLOAT64, 1.0),
    ("roughnessFactor", _VAL_FLOAT64, 1.0),
    ("normalMapStyle", _VAL_INT32, 1),
    ("normalScale", _VAL_FLOAT64, 1.0),
    ("occlusionStrength", _VAL_FLOAT64, 1.0),
    ("baseColorFactor", _VAL_UINT32, _pack_rgba_u32(255, 255, 255, 255)),
)

# 7. style.xml named setting table: (item_id, name, variant_type, value)
_STYLE_NAMESPACE = "http://sketchup.google.com/schemas/sketchup/1.0/style"
_TYPES_NAMESPACE = "http://sketchup.google.com/schemas/1.0/types"
_WMLIST_NAMESPACE = "http://sketchup.google.com/schemas/sketchup/1.0/wmlist"

_STYLE_VAR_BOOL = 1
_STYLE_VAR_INT32 = 4
_STYLE_VAR_FLOAT32 = 6
_STYLE_VAR_FLOAT64 = 7
_STYLE_VAR_WMLIST = 13

_DEFAULT_STYLE_XML_SETTINGS: Tuple[Tuple[int, str, int, object], ...] = (
    (1000, "EdgeDisplayMode", _STYLE_VAR_INT32, 1),
    (1001, "EdgeType", _STYLE_VAR_INT32, 0),
    (1002, "EdgeColorMode", _STYLE_VAR_INT32, 1),
    (1004, "ExtendLines", _STYLE_VAR_BOOL, False),
    (1005, "LineExtension", _STYLE_VAR_INT32, 3),
    (1006, "DrawSilhouettes", _STYLE_VAR_BOOL, True),
    (1007, "SilhouetteWidth", _STYLE_VAR_INT32, 2),
    (1008, "DrawDepthQue", _STYLE_VAR_BOOL, False),
    (1009, "DepthQueWidth", _STYLE_VAR_INT32, 4),
    (1010, "DrawLineEnds", _STYLE_VAR_BOOL, False),
    (1011, "LineEndWidth", _STYLE_VAR_INT32, 9),
    (1012, "JitterEdges", _STYLE_VAR_BOOL, False),
    (1014, "ForegroundColor", _STYLE_VAR_INT32, (0, 0, 0, 255)),
    (1015, "DrawBackEdges", _STYLE_VAR_BOOL, False),
    (1016, "EdgeHaloFlag", _STYLE_VAR_BOOL, True),
    (2001, "RenderMode", _STYLE_VAR_INT32, 2),
    (2002, "FaceFrontColor", _STYLE_VAR_INT32, (255, 255, 255, 255)),
    (2003, "FaceBackColor", _STYLE_VAR_INT32, (162, 183, 198, 255)),
    (2004, "ModelTransparency", _STYLE_VAR_BOOL, False),
    (2005, "MaterialTransparency", _STYLE_VAR_BOOL, True),
    (2006, "TransparencySort", _STYLE_VAR_INT32, 0),
    (2007, "Texture", _STYLE_VAR_BOOL, True),
    (2008, "TransparencyFactor", _STYLE_VAR_FLOAT64, 0.65),
    (4000, "BackgroundColor", _STYLE_VAR_INT32, (191, 191, 198, 255)),
    (4001, "SkyColor", _STYLE_VAR_INT32, (0, 154, 217, 255)),
    (4002, "DrawHorizon", _STYLE_VAR_BOOL, True),
    (4003, "GroundColor", _STYLE_VAR_INT32, (151, 170, 136, 255)),
    (4004, "DrawGround", _STYLE_VAR_BOOL, False),
    (4005, "GroundTransparency", _STYLE_VAR_INT32, 50),
    (4006, "DrawUnderground", _STYLE_VAR_BOOL, True),
    (4007, "HorizonColor", _STYLE_VAR_INT32, (0, 0, 0, 0)),
    (5000, "DisplayWatermarks", _STYLE_VAR_BOOL, True),
    (5001, "WatermarksList", _STYLE_VAR_WMLIST, 0),
    (7000, "HighlightColor", _STYLE_VAR_INT32, (0, 1, 255, 255)),
    (7001, "LockedColor", _STYLE_VAR_INT32, (255, 0, 0, 255)),
    (7002, "ConstructionColor", _STYLE_VAR_INT32, (0, 0, 0, 255)),
    (7003, "SectionActiveColor", _STYLE_VAR_INT32, (255, 135, 0, 255)),
    (7004, "SectionInactiveColor", _STYLE_VAR_INT32, (112, 105, 97, 255)),
    (7005, "SectionDefaultCutColor", _STYLE_VAR_INT32, (0, 0, 0, 255)),
    (7008, "DisplaySketchAxes", _STYLE_VAR_BOOL, True),
    (7010, "DrawHidden", _STYLE_VAR_BOOL, False),
    (7011, "DisplayColorByLayer", _STYLE_VAR_BOOL, False),
    (7012, "HideConstructionGeometry", _STYLE_VAR_BOOL, False),
    (7013, "SectionDisplayMode", _STYLE_VAR_INT32, 2),
    (7014, "SectionCutWidth", _STYLE_VAR_INT32, 3),
    (7015, "SectionCutFilled", _STYLE_VAR_BOOL, True),
    (7016, "SectionDefaultFillColor", _STYLE_VAR_INT32, (0, 0, 0, 255)),
    (7017, "DrawHiddenGeometry", _STYLE_VAR_BOOL, False),
    (7018, "DrawHiddenObjects", _STYLE_VAR_BOOL, False),
    (8000, "MatchPhotoBackgroundEnable", _STYLE_VAR_BOOL, True),
    (8001, "MatchPhotoBackgroundOpacity", _STYLE_VAR_FLOAT64, 1.0),
    (8002, "MatchPhotoForegroundEnable", _STYLE_VAR_BOOL, True),
    (8003, "MatchPhotoForegroundOpacity", _STYLE_VAR_FLOAT64, 0.8),
    (8100, "AmbientOcclusion", _STYLE_VAR_BOOL, False),
    (8101, "AmbientOcclusionAutoDistance", _STYLE_VAR_BOOL, True),
    (8102, "AmbientOcclusionDistance", _STYLE_VAR_FLOAT32, 10.0),
    (8103, "AmbientOcclusionIntensity", _STYLE_VAR_FLOAT32, 0.0),
)


# ── XML table serializers ────────────────────────────────────────────────


def _format_xml_setting_value(value_type: str, value: object) -> str:
    if value_type == _VAL_BOOL:
        return "1" if bool(value) else "0"
    if value_type in (_VAL_INT32, _VAL_UINT32):
        return str(int(value))  # type: ignore[arg-type]
    if value_type in (_VAL_FLOAT32, _VAL_FLOAT64):
        text = f"{float(value):.6f}".rstrip("0").rstrip(".")  # type: ignore[arg-type]
        return text or "0"
    if value_type == _VAL_STRING:
        return _xml_attr(str(value))
    raise ValueError(f"unknown XML setting type: {value_type!r}")


def serialize_material_xml_from_table(
    material_name: str,
    material_settings: Sequence[Tuple[str, str, object]] = _DEFAULT_LAYER0_MATERIAL_SETTINGS,
    pbr_attrs: Sequence[Tuple[str, str, object]] = _DEFAULT_MATERIAL_PBR_ATTRS,
    pbr_settings: Sequence[Tuple[str, str, object]] = _DEFAULT_MATERIAL_PBR_SETTINGS,
) -> bytes:
    """Serialize ``material.xml`` from named ``(name, type, value)`` setting tables."""
    mat_attr_parts = [f'name="{_xml_attr(material_name)}"']
    for name, vtype, val in material_settings:
        mat_attr_parts.append(f'{name}="{_format_xml_setting_value(vtype, val)}"')

    pbr_attr_parts = [
        f'{name}="{_format_xml_setting_value(vtype, val)}"'
        for name, vtype, val in pbr_attrs
    ]
    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="no" ?>',
        f'<materialDocument xmlns="{_MATERIAL_NAMESPACE}" xmlns:mat="{_MATERIAL_NAMESPACE}">',
        f'  <mat:material {" ".join(mat_attr_parts)}>',
        f'    <mat:pbrMR {" ".join(pbr_attr_parts)}>',
    ]
    for name, vtype, val in pbr_settings:
        lines.append(f"      <mat:{name}>{_format_xml_setting_value(vtype, val)}</mat:{name}>")
    lines.extend(
        [
            "    </mat:pbrMR>",
            "  </mat:material>",
            "</materialDocument>",
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def serialize_style_xml_from_table(
    style_name: str = _DEFAULT_STYLE_NAME,
    style_description: str = _DEFAULT_STYLE_DESCRIPTION,
    settings: Sequence[Tuple[int, str, int, object]] = _DEFAULT_STYLE_XML_SETTINGS,
) -> bytes:
    """Serialize ``style.xml`` from a named ``(id, name, variant_type, value)`` table."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="no" ?>',
        f'<styleDocument xmlns="{_STYLE_NAMESPACE}" xmlns:sty="{_STYLE_NAMESPACE}">',
        (
            f'  <sty:style xmlns:t="{_TYPES_NAMESPACE}" '
            f'name="{_xml_attr(style_name)}" desc="{_xml_attr(style_description)}">'
        ),
    ]
    for item_id, _setting_name, var_type, value in settings:
        lines.append(f'    <sty:item id="{item_id}">')
        if var_type == _STYLE_VAR_WMLIST:
            lines.append(f'      <t:variant type="{var_type}" xmlns:n0="{_WMLIST_NAMESPACE}">')
            lines.append(f'        <n0:wmlist count="{int(value)}"/>')  # type: ignore[arg-type]
            lines.append("      </t:variant>")
        else:
            if var_type == _STYLE_VAR_BOOL:
                formatted = "1" if bool(value) else "0"
            elif var_type == _STYLE_VAR_INT32:
                if isinstance(value, tuple):
                    formatted = str(_pack_rgba_i32(*value))
                else:
                    formatted = str(int(value))  # type: ignore[arg-type]
            elif var_type == _STYLE_VAR_FLOAT32:
                fval = float(value)  # type: ignore[arg-type]
                formatted = f"{fval:.9g}"
            elif var_type == _STYLE_VAR_FLOAT64:
                # IEEE-754 DBL_DECIMAL_DIG (17 significant digits), matching SketchUp's %.17g.
                formatted = f"{float(value):.17g}"  # type: ignore[arg-type]
            else:
                raise ValueError(f"unknown style.xml variant type: {var_type!r}")
            lines.append(f'      <t:variant type="{var_type}">{formatted}</t:variant>')
        lines.append("    </sty:item>")
    lines.extend(["  </sty:style>", "</styleDocument>", ""])
    return "\n".join(lines).encode("utf-8")


# ── Output dataclass ─────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class BlankDocumentVff:
    """Serialized empty 2021+ VFF ``.skp`` document together with every ID,
    offset, archive path, and default value :mod:`openskp.create_vff` uses."""

    data: bytes
    header: bytes
    header_vff_offset: int
    integrity_word_offset: int
    zip_offset: int
    version_string: str
    model_revision: int
    initial_next_id: int
    default_layer_name: str
    default_layer_id: int
    default_layer_material_id: int
    default_layer_material_folder: str
    default_new_layer_rgb: Tuple[int, int, int]
    default_font_name: str
    default_font_id: int
    default_style_name: str
    default_style_id: int
    saved_style_folder: str
    working_style_folder: str
    line_style_ids: Tuple[Tuple[str, int], ...]
    default_camera_eye: Point3
    default_camera_target: Point3
    default_camera_up: Point3
    default_camera_perspective: bool
    default_camera_fov: float
    default_camera_ortho_height: float
    meta_dat: bytes
    layer0_material_xml: bytes
    style_xml: bytes
    thumbnail_png: bytes
    model_dat: bytes
    entries: Tuple[ZipEntry, ...]


# ── Builder implementation ───────────────────────────────────────────────


class _BlankDocumentVffWriter:
    """Internal builder emitting each part of the empty VFF document in order."""

    def _encode_typed_value(self, value_type: str, value: object) -> Record:
        if value_type == _VAL_BOOL:
            child = record(T_VALUE_BOOL, u8(1 if bool(value) else 0))
        elif value_type == _VAL_INT32:
            child = record(T_VALUE_INT, struct.pack("<i", int(value)))  # type: ignore[arg-type]
        elif value_type == _VAL_FLOAT64:
            child = record(T_VALUE_DOUBLE, f64s([float(value)]))  # type: ignore[arg-type]
        elif value_type == _VAL_STRING:
            child = record(T_VALUE_STRING, str(value).encode("utf-8"))
        else:
            raise ValueError(f"unsupported typed value kind: {value_type!r}")
        return Record(T_VALUE, children=[child])

    def _encode_attr_dict(
        self,
        name: str,
        entries: Sequence[Tuple[str, str, object]],
        trailer: int = 0,
    ) -> Record:
        pair_records: List[Record] = []
        for key, vtype, val in entries:
            pair_records.append(record(T_ATTR_KEY, key.encode("utf-8")))
            pair_records.append(self._encode_typed_value(vtype, val))
        return Record(
            T_ATTR_DICT,
            children=[
                record(T_ATTR_EMPTY, b""),
                record(T_ATTR_NAME, name.encode("utf-8")),
                Record(T_ATTR_ENTRIES, children=pair_records),
                record(T_ATTR_TRAILER, u32(trailer)),
            ],
        )

    def _entity_id_record(self, entity_id: int) -> Record:
        return Record(T_ENTITY_BASE, children=[record(T_ENTITY_ID, varint(entity_id))])

    # ── 1. 69-byte file header ───────────────────────────────────────────

    def _write_header(
        self,
        version_string: str = _DEFAULT_VERSION_STRING,
        model_revision: int = _DEFAULT_MODEL_REVISION,
        integrity_word_value: int = 0,
    ) -> Tuple[bytes, int, int]:
        prefix = _encode_utf16_str(_FILE_MAGIC_TEXT) + _encode_utf16_str(version_string)
        vff_offset = len(prefix)
        integrity_offset = vff_offset + 9
        trailer = _VFF_TRAILER_MAGIC + struct.pack(
            "<HHHI",
            _VFF_TRAILER_PAYLOAD_BYTES,
            _VFF_CONTAINER_VERSION,
            model_revision,
            integrity_word_value & 0xFFFFFFFF,
        )
        return prefix + trailer, vff_offset, integrity_offset

    # ── 2. meta/meta.dat (366 bytes) ─────────────────────────────────────

    def _thumbnail_ref_record(self, outer_tag: int, archive_path: str) -> Record:
        return Record(
            outer_tag,
            children=[
                Record(
                    T_META_FILE_REF,
                    children=[
                        record(T_META_FILE_FORMAT, u32(_META_IMAGE_FORMAT_PNG)),
                        record(T_META_FILE_PATH, archive_path.encode("utf-8")),
                    ],
                )
            ],
        )

    def _write_meta_dat(self) -> bytes:
        history_item = Record(
            T_META_HISTORY_ITEM,
            children=[
                record(T_META_HISTORY_APP, _DEFAULT_CLIENT_APP.encode("utf-8")),
                record(T_META_HISTORY_TIME, u32(_DEFAULT_SAVE_TIMESTAMP_UNIX)),
                record(T_META_HISTORY_TOKEN, _u64(_DEFAULT_HISTORY_SESSION_TOKEN)),
            ],
        )
        root = Record(
            T_META,
            children=[
                record(T_META_APP_VERSION, _DEFAULT_APP_VERSION.encode("utf-8")),
                record(T_META_RELEASE_YEAR, _u16(_DEFAULT_RELEASE_YEAR_CODE)),
                record(T_META_RELEASE_CHANNEL, _u16(_DEFAULT_RELEASE_CHANNEL)),
                record(T_META_CONTAINER_VER, _u16(_VFF_CONTAINER_VERSION)),
                record(T_META_MODEL_REVISION, _u16(_DEFAULT_MODEL_REVISION)),
                record(T_META_GUID, uuid.UUID(_DEFAULT_MODEL_GUID).bytes_le),
                self._thumbnail_ref_record(T_META_MODEL_THUMBNAIL, _MODEL_THUMBNAIL_ENTRY),
                self._thumbnail_ref_record(T_META_PREVIEW_THUMBNAIL, _PREVIEW_THUMBNAIL_ENTRY),
                record(T_META_HAS_THUMBNAIL, u8(1)),
                record(T_META_MODEL_NAME, b""),
                record(T_META_MODEL_DESC, b""),
                record(T_META_AUTHOR, b""),
                record(T_META_SOURCE, b""),
                record(T_META_GEOREFERENCED, u8(0)),
                record(T_META_CLASSIFIED, u8(0)),
                record(T_META_DYNAMIC_FLAG, u8(0)),
                record(T_META_UNITS, _DEFAULT_UNITS_NAME.encode("utf-8")),
                record(T_META_SHOW_UNITS, u8(1)),
                record(T_META_SAVED_PATH, _DEFAULT_SAVED_PATH.encode("utf-8")),
                Record(
                    T_META_HISTORY,
                    children=[
                        Record(
                            T_META_HISTORY_INNER,
                            children=[Record(T_META_HISTORY_ENTRY, children=[history_item])],
                        )
                    ],
                ),
            ],
        )
        return encode_records([root])

    # ── 3. XML entries & placeholder thumbnail ───────────────────────────

    def _write_layer0_material_xml(self, layer_material_name: str) -> bytes:
        return serialize_material_xml_from_table(layer_material_name)

    def _write_style_xml(self) -> bytes:
        return serialize_style_xml_from_table(
            _DEFAULT_STYLE_NAME,
            _DEFAULT_STYLE_DESCRIPTION,
            _DEFAULT_STYLE_XML_SETTINGS,
        )

    def _write_thumbnail_png(self) -> bytes:
        return build_plain_png()

    # ── 4. model.dat sections (5,282 bytes across 27 top-level records) ──

    def _records_preamble_and_meta_path(self) -> List[Record]:
        return [
            record(T_DOC_ZERO_WORD, u32(0)),
            record(T_META_PATH, _META_DAT_ENTRY.encode("utf-8")),
        ]

    def _record_model_properties(self) -> Record:
        prop_dict = self._encode_attr_dict("ModelProperties", _DEFAULT_MODEL_PROPERTIES, trailer=0)
        return Record(
            T_MODEL_PROPS,
            children=[Record(T_ATTR_LIST, children=[Record(T_ATTR_SET, children=[prop_dict])])],
        )

    def _record_options_manager(self) -> Record:
        provider_records: List[Record] = []
        for prov_name, prov_entries in _DEFAULT_OPTIONS_PROVIDERS:
            entry_children: List[Record] = []
            for key, vtype, val in prov_entries:
                entry_children.append(record(T_OPTIONS_KEY, key.encode("utf-8")))
                entry_children.append(self._encode_typed_value(vtype, val))
            provider_records.append(
                Record(
                    T_OPTIONS_PROVIDER,
                    children=[
                        record(T_OPTIONS_PROV_NAME, prov_name.encode("utf-8")),
                        Record(T_OPTIONS_PROV_ENTRIES, children=entry_children),
                    ],
                )
            )
        return Record(
            T_OPTIONS_MODEL,
            children=[
                Record(
                    T_OPTIONS_MGR,
                    children=[Record(T_OPTIONS_PROVIDERS, children=provider_records)],
                )
            ],
        )

    def _record_next_id(self) -> Record:
        return Record(
            T_NEXT_ID,
            children=[
                Record(
                    T_NEXT_ID_WRAP,
                    children=[record(T_NEXT_ID_VAL, varint(_INITIAL_NEXT_PERSISTENT_ID))],
                )
            ],
        )

    def _record_model_camera(self) -> Record:
        cam = Record(
            T_CAMERA,
            children=[
                record(T_CAMERA_EYE, f64s(_DEFAULT_CAMERA_EYE)),
                record(T_CAMERA_TARGET, f64s(_DEFAULT_CAMERA_TARGET)),
                record(T_CAMERA_UP, f64s(_DEFAULT_CAMERA_UP)),
                record(T_CAMERA_CLIP_NEAR, f64s([_DEFAULT_CAMERA_CLIP_NEAR])),
                record(T_CAMERA_CLIP_FAR, f64s([_DEFAULT_CAMERA_CLIP_FAR])),
                record(T_CAMERA_PERSPECTIVE, u8(1 if _DEFAULT_CAMERA_PERSPECTIVE else 0)),
                record(T_CAMERA_FOV, f64s([_DEFAULT_CAMERA_FOV])),
                record(T_CAMERA_HEIGHT, f64s([_DEFAULT_CAMERA_ORTHO_HEIGHT])),
                record(T_CAMERA_ASPECT_RATIO, f64s([_DEFAULT_CAMERA_ASPECT_RATIO])),
                record(T_CAMERA_FOV_IS_HEIGHT, u8(1 if _DEFAULT_CAMERA_FOV_IS_HEIGHT else 0)),
                record(T_CAMERA_WINDOW_FLAG, u8(0)),
                record(T_CAMERA_DESCRIPTION, b""),
                record(T_CAMERA_IMAGE_WIDTH, f64s([_DEFAULT_CAMERA_IMAGE_WIDTH])),
                record(T_CAMERA_IS_2D, u8(1 if _DEFAULT_CAMERA_IS_2D else 0)),
                record(T_CAMERA_SCALE_2D, f64s([_DEFAULT_CAMERA_SCALE_2D])),
                record(T_CAMERA_CENTER_2D_X, f64s([_DEFAULT_CAMERA_CENTER_2D[0]])),
                record(T_CAMERA_CENTER_2D_Y, f64s([_DEFAULT_CAMERA_CENTER_2D[1]])),
                record(T_CAMERA_TAIL_FLAG, u8(0)),
            ],
        )
        return Record(T_CAMERA_MODEL, children=[cam])

    def _record_rendering_options(self) -> Record:
        children: List[Record] = []
        for tag, _name, vtype, val in _DEFAULT_RENDERING_OPTIONS:
            if vtype == _VAL_BOOL:
                payload = u8(1 if bool(val) else 0)
            elif vtype == _VAL_UINT32:
                payload = u32(int(val))  # type: ignore[arg-type]
            elif vtype == _VAL_FLOAT32:
                payload = _f32(float(val))  # type: ignore[arg-type]
            elif vtype == _VAL_FLOAT64:
                payload = f64s([float(val)])  # type: ignore[arg-type]
            elif vtype == _VAL_RGBA:
                payload = _pack_rgba_bytes(val)  # type: ignore[arg-type]
            else:
                raise ValueError(f"unsupported rendering option type: {vtype!r}")
            children.append(record(tag, payload))
        return Record(T_RENDERING, children=[Record(T_RO_CONTAINER, children=children)])

    def _record_shadow_info(self) -> Record:
        shadow = Record(
            T_SHADOW_CONTAINER,
            children=[
                record(T_SHADOW_TIME, u32(_DEFAULT_SHADOW_TIME_UNIX)),
                record(T_SHADOW_DAYLIGHT_SAVINGS, u8(0)),
                record(T_SHADOW_CITY, _DEFAULT_CITY.encode("utf-8")),
                record(T_SHADOW_COUNTRY, _DEFAULT_COUNTRY.encode("utf-8")),
                record(T_SHADOW_LONGITUDE, f64s([_DEFAULT_LONGITUDE])),
                record(T_SHADOW_LATITUDE, f64s([_DEFAULT_LATITUDE])),
                record(T_SHADOW_TZ_OFFSET, f64s([_DEFAULT_TZ_OFFSET])),
                record(T_SHADOW_NORTH_VEC, f64s(_DEFAULT_NORTH_VECTOR)),
                record(T_SHADOW_DISPLAY_SHADOWS, u8(0)),
                record(T_SHADOW_DISPLAY_NORTH, u8(0)),
                record(T_SHADOW_ON_ALL_FACES, u8(1)),
                record(T_SHADOW_ON_GROUND, u8(1)),
                record(T_SHADOW_EDGES_CAST, u8(0)),
                record(T_SHADOW_LIGHT, u32(_DEFAULT_SHADOW_LIGHT)),
                record(T_SHADOW_DARK, u32(_DEFAULT_SHADOW_DARK)),
                record(T_SHADOW_SUN_SHADING, u8(0)),
            ],
        )
        return Record(T_SHADOWS, children=[shadow])

    def _record_axes(self) -> Record:
        axes = Record(
            T_AXES_CONTAINER,
            children=[
                Record(T_DRAWING, children=[record(T_FLAGS, u8(0x06))]),
                record(T_AXES_ORIGIN, f64s((0.0, 0.0, 0.0))),
                record(T_AXES_X, f64s((1.0, 0.0, 0.0))),
                record(T_AXES_Y, f64s((0.0, 1.0, 0.0))),
                record(T_AXES_Z, f64s((0.0, 0.0, 1.0))),
            ],
        )
        return Record(T_AXES, children=[axes])

    def _record_line_styles(self) -> Record:
        items: List[Record] = []
        for ls_id, ls_name, ls_pattern in _DEFAULT_LINE_STYLES:
            items.append(
                Record(
                    T_LINESTYLE,
                    children=[
                        self._entity_id_record(ls_id),
                        record(T_LINESTYLE_NAME, ls_name.encode("utf-8")),
                        record(T_LINESTYLE_PATTERN, ls_pattern.encode("utf-8")),
                        record(T_LINESTYLE_LENGTH_SCALE, f64s([1.0])),
                        record(T_LINESTYLE_WIDTH_SCALE, f64s([1.0])),
                        record(T_LINESTYLE_TYPE, u32(0)),
                        record(T_LINESTYLE_USER_FLAG, u8(0)),
                    ],
                )
            )
        return Record(
            T_LINE_STYLES,
            children=[
                Record(
                    T_LINESTYLE_MGR,
                    children=[Record(T_LINESTYLE_LIST, children=items)],
                )
            ],
        )

    def _record_materials_section(self) -> Record:
        return Record(
            T_MATERIALS,
            children=[Record(T_MATERIAL_SECTION, children=[Record(T_MATERIAL_LIST, children=[])])],
        )

    def _record_layers_section(self, layer_material_folder: str) -> Record:
        layer0_mat = Record(
            T_MATERIAL,
            children=[
                self._entity_id_record(_DEFAULT_LAYER_MATERIAL_PID),
                record(T_MATERIAL_NAME, layer_material_folder.encode("utf-8")),
                record(T_MATERIAL_OWNER, u8(1)),
                record(T_MATERIAL_EMPTY, b""),
            ],
        )
        layer0 = Record(
            T_LAYER,
            children=[
                self._entity_id_record(_DEFAULT_LAYER_PID),
                record(T_LAYER_NAME, _DEFAULT_LAYER_NAME.encode("utf-8")),
                record(T_LAYER_HIDDEN, u8(0)),
                Record(T_LAYER_MATERIAL, children=[layer0_mat]),
                record(T_LAYER_TAIL, u32(0)),
            ],
        )
        folder_root = Record(
            T_LAYER_FOLDER_ROOT,
            children=[
                record(T_LAYER_FOLDER_NAME, b""),
                record(T_LAYER_FOLDER_SUBS, b""),
                record(T_LAYER_FOLDER_ITEMS, b""),
                record(T_LAYER_FOLDER_VIS, u8(0)),
                record(T_LAYER_FOLDER_TAIL, u8(0)),
            ],
        )
        return Record(
            T_LAYERS,
            children=[
                Record(
                    T_LAYER_SECTION,
                    children=[
                        Record(T_LAYER_LIST, children=[layer0]),
                        record(T_LAYER_ACTIVE, varint(_DEFAULT_LAYER_PID)),
                        Record(T_LAYER_FOLDERS, children=[folder_root]),
                    ],
                )
            ],
        )

    def _record_fonts_section(self) -> Record:
        font = Record(
            T_FONT,
            children=[
                self._entity_id_record(_DEFAULT_FONT_PID),
                record(T_FONT_NAME, _DEFAULT_FONT_NAME.encode("utf-8")),
                record(T_FONT_ITALIC, u8(0)),
                record(T_FONT_BOLD, u8(0)),
                record(T_FONT_POINT_SIZE, u32(_DEFAULT_FONT_POINT_SIZE)),
                record(T_FONT_USE_WORLD_SIZE, u8(0)),
                record(T_FONT_WORLD_SIZE, f64s([_DEFAULT_FONT_WORLD_SIZE])),
            ],
        )
        return Record(
            T_FONTS,
            children=[Record(T_FONT_MGR, children=[Record(T_FONT_LIST, children=[font])])],
        )

    def _record_text_style(self) -> Record:
        tstyle = Record(
            T_TEXT_STYLE_CONTAINER,
            children=[
                record(T_TEXT_STYLE_FONT_A, varint(_DEFAULT_FONT_PID)),
                record(T_TEXT_STYLE_FONT_B, varint(_DEFAULT_FONT_PID)),
                record(T_TEXT_STYLE_ARROW, u32(3)),
                record(T_TEXT_STYLE_ALIGN, u32(1)),
                record(T_TEXT_STYLE_FLAG, u8(0)),
                record(T_TEXT_STYLE_LEADER_TYPE, u32(2)),
                record(T_TEXT_STYLE_DISPLAY_LEADER, u8(1)),
                record(T_TEXT_STYLE_COLOR, _pack_rgba_bytes((63, 63, 63, 255))),
                record(T_TEXT_STYLE_LEADER_COLOR, _pack_rgba_bytes((63, 63, 63, 255))),
            ],
        )
        return Record(T_TEXT_STYLE, children=[tstyle])

    def _record_dimension_style(self) -> Record:
        dstyle = Record(
            T_DIM_STYLE_CONTAINER,
            children=[
                record(T_DIM_STYLE_FONT, varint(_DEFAULT_FONT_PID)),
                record(T_DIM_STYLE_HAS_3D, u8(0)),
                record(T_DIM_STYLE_SHOW_UNITS, u8(1)),
                record(T_DIM_STYLE_EXT_OFFSET, u32(5)),
                record(T_DIM_STYLE_EXT_OVERSHOOT, u32(10)),
                record(T_DIM_STYLE_LINE_WEIGHT, u32(1)),
                record(T_DIM_STYLE_ARROW_TYPE, u32(3)),
                record(T_DIM_STYLE_ARROW_SIZE, u32(10)),
                record(T_DIM_STYLE_HL_NONASSOC, u8(0)),
                record(T_DIM_STYLE_HL_COLOR, _pack_rgba_bytes((255, 0, 0, 255))),
                record(T_DIM_STYLE_RADIAL_PREFIX, u8(1)),
                record(T_DIM_STYLE_HIDE_OUT_OF_PLANE, u8(0)),
                record(T_DIM_STYLE_HIDE_OOP_VAL, f64s([0.6])),
                record(T_DIM_STYLE_HIDE_SMALL, u8(0)),
                record(T_DIM_STYLE_HIDE_SMALL_VAL, f64s([10.0])),
                record(T_DIM_STYLE_COLOR, _pack_rgba_bytes((63, 63, 63, 255))),
                record(T_DIM_STYLE_TEXT_COLOR, _pack_rgba_bytes((0, 0, 0, 255))),
                record(T_DIM_STYLE_ALIGN_MODE, u32(3)),
            ],
        )
        return Record(T_DIMENSION_STYLE, children=[dstyle])

    def _records_classifications_and_definitions(self) -> List[Record]:
        preview_dib = record(T_PREVIEW_DIB, b"")
        classifications = Record(
            T_CLASSIFICATIONS,
            children=[
                Record(
                    T_SCHEMA_MGR,
                    children=[
                        record(T_SCHEMA_LIST, b""),
                        record(T_SCHEMA_FLAGS, u32(0)),
                    ],
                )
            ],
        )
        definitions = Record(
            T_DEFINITIONS,
            children=[
                record(T_DOC_ZERO_WORD, u32(0)),
                Record(
                    T_DEFINITION_SECTION,
                    children=[
                        record(T_DOC_ZERO_WORD, u32(0)),
                        Record(
                            T_DEFINITION_LIST,
                            children=[record(T_DOC_ZERO_WORD, u32(0))],
                        ),
                    ],
                ),
            ],
        )
        return [preview_dib, classifications, definitions]

    def _record_root_entities(self) -> Record:
        dict_records = [
            self._encode_attr_dict(dname, dentries, trailer=dtrailer)
            for dname, dtrailer, dentries in _DEFAULT_ROOT_ATTRIBUTE_DICTS
        ]
        attr_block = Record(
            T_ATTR_BLOCK,
            children=[Record(T_ATTR_LIST, children=[Record(T_ATTR_SET, children=dict_records)])],
        )
        drawing = Record(
            T_DRAWING,
            children=[
                Record(T_ENTITY_BASE, children=[attr_block]),
                record(T_FLAGS, u8(0x06)),
            ],
        )
        relationships = Record(
            T_RELATIONSHIPS,
            children=[
                Record(
                    T_RELATIONSHIP_GROUP,
                    children=[record(T_RELATIONSHIP_LIST, b"")],
                )
            ],
        )
        container = Record(
            T_DEFINITION_CONTAINER,
            children=[
                drawing,
                record(T_EMPTY, b""),
                relationships,
                record(T_FLAGS_CONTAINER, u8(0)),
            ],
        )
        return Record(T_ENTITIES, children=[container])

    def _records_styles_section(
        self,
        saved_style_folder: str,
        working_style_folder: str,
    ) -> List[Record]:
        style_guid_bytes = uuid.UUID(_DEFAULT_STYLE_GUID).bytes_le
        saved_style = Record(
            T_STYLE,
            children=[
                self._entity_id_record(_DEFAULT_STYLE_PID),
                record(T_STYLE_GUID, style_guid_bytes),
                record(T_STYLE_PATH, b""),
                record(T_STYLE_NAME, saved_style_folder.encode("utf-8")),
                record(T_STYLE_DESC, b""),
            ],
        )
        working_style = Record(
            T_STYLE,
            children=[
                record(T_STYLE_GUID, style_guid_bytes),
                record(T_STYLE_PATH, b""),
                record(T_STYLE_NAME, working_style_folder.encode("utf-8")),
            ],
        )
        styles_mgr = Record(
            T_STYLES,
            children=[
                Record(
                    T_STYLE_MGR,
                    children=[
                        Record(T_STYLE_LIST, children=[saved_style]),
                        record(T_STYLE_SELECTED, varint(_DEFAULT_STYLE_PID)),
                        Record(T_STYLE_WORKING, children=[working_style]),
                        record(T_STYLE_CHANGED, u8(0)),
                    ],
                )
            ],
        )
        return [
            record(T_MODEL_FLAG_BYTE, u8(1)),
            record(T_MODEL_FEATURE_MASK, u32(1)),
            styles_mgr,
        ]

    def _records_overlays_pages_watermarks(self) -> List[Record]:
        overlays = Record(
            T_OVERLAYS,
            children=[Record(T_OVERLAY_MGR, children=[record(T_OVERLAY_LIST, b"")])],
        )
        pages = Record(
            T_PAGES_MODEL,
            children=[Record(T_PAGE_LIST, children=[record(T_PAGE_ITEMS, b"")])],
        )
        watermarks = record(T_WATERMARKS, b"")
        return [overlays, pages, watermarks]

    def _records_root_behaviour_and_trailer(self) -> List[Record]:
        behaviour = Record(
            T_MODEL_BEHAVIOUR,
            children=[
                Record(
                    T_DEFINITION_BEHAVIOUR,
                    children=[
                        record(T_BEHAVIOUR_SNAP_TO, u32(0)),
                        record(T_BEHAVIOUR_NO_SCALE, u32(0)),
                        record(T_BEHAVIOUR_CUTS_OPENING, u8(0)),
                        record(T_BEHAVIOUR_IS_2D, u8(0)),
                        record(T_DEFINITION_ALWAYS_FACE_CAMERA, u8(0)),
                        record(T_DEFINITION_SHADOWS_FACE_SUN, u8(0)),
                    ],
                )
            ],
        )
        return [behaviour, record(T_MODEL_TRAILER, u32(0))]

    def _write_model_dat(
        self,
        layer_material_folder: str,
        saved_style_folder: str,
        working_style_folder: str,
    ) -> bytes:
        children: List[Record] = []
        children.extend(self._records_preamble_and_meta_path())
        children.append(self._record_model_properties())
        children.append(self._record_options_manager())
        children.append(self._record_next_id())
        children.append(self._record_model_camera())
        children.append(self._record_rendering_options())
        children.append(self._record_shadow_info())
        children.append(self._record_axes())
        children.append(self._record_line_styles())
        children.append(self._record_materials_section())
        children.append(self._record_layers_section(layer_material_folder))
        children.append(self._record_fonts_section())
        children.append(self._record_text_style())
        children.append(self._record_dimension_style())
        children.extend(self._records_classifications_and_definitions())
        children.append(self._record_root_entities())
        children.extend(self._records_styles_section(saved_style_folder, working_style_folder))
        children.extend(self._records_overlays_pages_watermarks())
        children.extend(self._records_root_behaviour_and_trailer())
        return Record(T_DOCUMENT, children=children).encode()

    # ── 5. Deterministic archive assembly ────────────────────────────────

    def build(self) -> BlankDocumentVff:
        layer_material_name = f"Layer_{_DEFAULT_LAYER_NAME}"
        layer_material_folder = _sanitize_archive_folder(layer_material_name)
        saved_style_folder = _sanitize_archive_folder(_DEFAULT_STYLE_NAME)
        working_style_folder = f"{saved_style_folder}_1"

        meta_dat = self._write_meta_dat()
        layer0_xml = self._write_layer0_material_xml(layer_material_name)
        style_xml = self._write_style_xml()
        thumb_png = self._write_thumbnail_png()
        model_dat = self._write_model_dat(
            layer_material_folder, saved_style_folder, working_style_folder
        )

        entries: Tuple[ZipEntry, ...] = (
            ZipEntry(_MODEL_THUMBNAIL_ENTRY, thumb_png, flag=0x0000),
            ZipEntry(_PREVIEW_THUMBNAIL_ENTRY, thumb_png, flag=0x0000),
            ZipEntry(_META_DAT_ENTRY, meta_dat, flag=0x0006),
            ZipEntry(f"materials/{layer_material_folder}/material.xml", layer0_xml, flag=0x0006),
            ZipEntry(f"styles/{saved_style_folder}/style.xml", style_xml, flag=0x0006),
            ZipEntry(f"styles/{working_style_folder}/style.xml", style_xml, flag=0x0006),
            ZipEntry(_MODEL_DAT_ENTRY, model_dat, flag=0x0006),
        )

        raw_header, vff_offset, integrity_offset = self._write_header()
        full_skp = build_skp(raw_header, entries)
        zip_offset = len(raw_header)
        final_header = full_skp[:zip_offset]

        return BlankDocumentVff(
            data=full_skp,
            header=final_header,
            header_vff_offset=vff_offset,
            integrity_word_offset=integrity_offset,
            zip_offset=zip_offset,
            version_string=_DEFAULT_VERSION_STRING,
            model_revision=_DEFAULT_MODEL_REVISION,
            initial_next_id=_INITIAL_NEXT_PERSISTENT_ID,
            default_layer_name=_DEFAULT_LAYER_NAME,
            default_layer_id=_DEFAULT_LAYER_PID,
            default_layer_material_id=_DEFAULT_LAYER_MATERIAL_PID,
            default_layer_material_folder=layer_material_folder,
            default_new_layer_rgb=_DEFAULT_NEW_LAYER_COLOR_RGB,
            default_font_name=_DEFAULT_FONT_NAME,
            default_font_id=_DEFAULT_FONT_PID,
            default_style_name=_DEFAULT_STYLE_NAME,
            default_style_id=_DEFAULT_STYLE_PID,
            saved_style_folder=saved_style_folder,
            working_style_folder=working_style_folder,
            line_style_ids=tuple((name, pid) for pid, name, _pat in _DEFAULT_LINE_STYLES),
            default_camera_eye=_DEFAULT_CAMERA_EYE,
            default_camera_target=_DEFAULT_CAMERA_TARGET,
            default_camera_up=_DEFAULT_CAMERA_UP,
            default_camera_perspective=_DEFAULT_CAMERA_PERSPECTIVE,
            default_camera_fov=_DEFAULT_CAMERA_FOV,
            default_camera_ortho_height=_DEFAULT_CAMERA_ORTHO_HEIGHT,
            meta_dat=meta_dat,
            layer0_material_xml=layer0_xml,
            style_xml=style_xml,
            thumbnail_png=thumb_png,
            model_dat=model_dat,
            entries=entries,
        )


def build_blank_vff() -> BlankDocumentVff:
    """Construct the empty 2021+ VFF ``.skp`` document and return its bytes and metadata."""
    return _BlankDocumentVffWriter().build()
