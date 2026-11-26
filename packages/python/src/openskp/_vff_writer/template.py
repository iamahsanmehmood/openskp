"""Template document model for the VFF writer.

The writer never splices at fixed byte offsets.  It parses the template's
``model.dat`` into a record tree, edits the sections it cares about (materials,
layers, definitions, the root entity container, the next-id record), and
re-encodes the tree, so any SketchUp-written VFF document with the standard
section set can serve as the template.

The record layouts are the ones documented from SketchUp-saved files,
confirmed against minimal pairs saved by SketchUp itself.
"""

from __future__ import annotations

import math
import re
import struct
import time
import uuid
import xml.etree.ElementTree as ET
from typing import Dict, Iterable, KeysView, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape as _xml_escape

from .archive import (
    ZipEntry,
    build_skp,
    entries_of,
    header_offset,
)
from ..create import Length, Point3d, SkpWriteError, Timestamp, Vector3d
from .uv import uv_attribute, uv_matrix_from_pins
from .tlv import (
    Record,
    encode_records,
    f64s,
    id_list,
    newell_normal,
    parse_records,
    read_varint,
    record,
    u8,
    u32,
    unit_normal,
    varint,
)

__all__ = ["Template", "EntityEditor", "material_xml", "sanitize_folder"]

# ── Tags (16-bit values exactly as stored little-endian in the file) ───────

T_DOCUMENT = 0x01F4
T_NEXT_ID = 0x01F5
T_ENTITIES = 0x01F6
T_MATERIALS = 0x01F7
T_LAYERS = 0x01F8
T_DEFINITIONS = 0x01F9

T_ENTITY_BASE = 0x05DC
T_ENTITY_ID = 0x05DE
T_DRAWING = 0x07D0
T_FRONT_MATERIAL = 0x07D1
T_LAYER_REF = 0x07D2
T_FLAGS = 0x07D3

T_VERTICES = 0x1389
T_EDGES = 0x138A
T_FACES = 0x138B
T_COMPONENTS = 0x138C
T_CURVES = 0x1396
T_ARC_CURVES = 0x1397
T_TEXTS = 0x1398
T_DIMENSIONS = 0x1399
T_SECTION_PLANES = 0x1393
T_GUIDE_LINES = 0x1391
T_GUIDE_POINTS = 0x1392
T_ACTIVE_SECTION = 0x1394
T_ORDER = 0x138E
T_RELATIONSHIPS = 0x139B
T_FLAGS_CONTAINER = 0x139F
T_BOUNDS = 0x13A0
T_EMPTY = 0x139E

T_VERTEX = 0x09C4
T_POINT = 0x09C5
T_EDGE = 0x0BB8
T_EDGE_START = 0x0BB9
T_EDGE_END = 0x0BBA
T_EDGE_CURVE = 0x0BBB
T_FACE = 0x0DAC
T_PLANE = 0x0DAD
T_LOOPS = 0x0DAE
T_BACK_MATERIAL = 0x0DAF
T_LOOP = 0x1194
T_LOOP_EDGES = 0x1195
T_EDGE_USE = 0x0FA0
T_EDGE_USE_ID = 0x0FA1
T_EDGE_USE_FORWARD = 0x0FA2

T_MATERIAL = 0x32C8
T_MATERIAL_OWNER = 0x32CA
T_MATERIAL_EMPTY = 0x32CB
T_MATERIAL_NAME = 0x32CC
T_MATERIAL_LIST = 0x30D5
T_MATERIAL_SECTION = 0x30D4

T_LAYER = 0x3C8C
T_LAYER_NAME = 0x3C8D
T_LAYER_HIDDEN = 0x3C8E
T_LAYER_MATERIAL = 0x3C8F
T_LAYER_TAIL = 0x3C90
T_LAYER_LIST = 0x3A99
T_LAYER_SECTION = 0x3A98

T_GROUPS = 0x138D
T_GROUP = 0x1D4C

T_CURVE = 0x4A38
T_CURVE_COUNT = 0x4A39
T_CURVE_FLAG = 0x4A3A
T_CURVE_FIRST = 0x4A3B
T_CURVE_LAST = 0x4A3C
T_ARC_CURVE = 0x4C2C
T_ARC_FRAME = 0x4C2D

T_TEXT = 0x55F0
T_TEXT_STRING = 0x55F1
T_TEXT_EMPTY = 0x55FF
T_TEXT_ZERO_A = 0x55F2
T_TEXT_ZERO_B = 0x55F3
T_TEXT_ANCHOR = 0x55F4
T_TEXT_VECTOR = 0x55F5
T_TEXT_ONE = 0x55F6
T_TEXT_UP = 0x55F7
T_TEXT_ZERO = 0x55F8
T_TEXT_FONT = 0x55F9
T_TEXT_ARROW = 0x55FA
T_TEXT_LEADER_FLAG_A = 0x55FB
T_TEXT_LEADER_FLAG = 0x55FC
T_TEXT_LEADER_TYPE = 0x55FD
T_TEXT_SCREEN = 0x55FE

T_DIMENSION = 0x5BCC
T_DIMENSION_HEAD = 0x59D8
T_DIMENSION_HEAD_EMPTY = 0x59D9
T_DIMENSION_FONT = 0x59DA
T_DIMENSION_HEAD_FLAG = 0x59DB
T_DIMENSION_HEAD_ARROW = 0x59DC
T_DIM_START = 0x5BCD
T_DIM_END = 0x5BCE
T_DIM_NORMAL = 0x5BCF
T_DIM_XAXIS = 0x5BD0
T_DIM_ONE = 0x5BD1
T_DIM_OFFSET = 0x5BD2
T_DIM_ZERO = 0x5BD3
T_DIM_ARROW = 0x5BD4
T_CONNECTION = 0x5208
T_CONNECTION_TYPE = 0x5209
T_CONNECTION_POINT = 0x520A
T_CONNECTION_REF_A = 0x520B
T_CONNECTION_REF_B = 0x520C
T_CONNECTION_REF = 0x53FC
T_CONNECTION_VERTEX = 0x53FD
T_CONNECTION_PATH = 0x53FE

T_SECTION = 0x445C
T_SECTION_PLANE = 0x445D
T_SECTION_NAME = 0x445E
T_SECTION_SYMBOL = 0x445F

T_ANNOTATION_BASE = 0x4268
T_GUIDE_LINE = 0x4269
T_GUIDE_BODY = 0x426A
T_GUIDE_STIPPLE = 0x426B
T_GUIDE_POINT = 0x426C
T_GUIDE_POSITION = 0x426D
T_GUIDE_ANCHOR = 0x426E
T_GUIDE_TAIL = 0x426F

T_RENDERING = 0x01FB
T_SHADOWS = 0x0204
T_AXES = 0x01FC
T_STYLES = 0x0206
T_CAMERA_EYE = 0x34BD
T_CAMERA_TARGET = 0x34BE
T_CAMERA_UP = 0x34BF
T_CAMERA_PERSPECTIVE = 0x34C2
T_CAMERA_HEIGHT = 0x34C3
T_CAMERA_FOV = 0x34C4
T_PAGE_SELECTED = 0x6D62
T_PAGE_NAME = 0x6F55
T_PAGE_PROPERTIES = 0x7149
T_PAGE_CAMERA = 0x714A
T_PAGE_STYLE = 0x714C
T_PAGE_RENDERING = 0x714D
T_PAGE_SHADOWS = 0x714E
T_PAGE_AXES = 0x714F
T_PAGE_HIDDEN_LAYERS = 0x7150
T_PAGE_ANIMATION = 0x7152
T_PAGE_DISPLAY_NAME = 0x7153
T_PAGE_TRANSITION = 0x7154
T_PAGE_DELAY = 0x7155
T_PAGE_THUMBNAIL_FLAG = 0x7157
T_PAGE_FLAGS_EMPTY = 0x714B
T_PAGE_STYLE_FLAGS = 0x715B
T_PAGE_EMPTY_A = 0x7159
T_PAGE_EMPTY_B = 0x7151

T_ATTR_LIST = 0x36B1
T_ATTR_SET = 0x36B2
T_ATTR_DICT = 0x36B3
T_ATTR_EMPTY = 0x36B0
T_ATTR_NAME = 0x36B4
T_ATTR_ENTRIES = 0x36B5
T_ATTR_KEY = 0x36B6
T_ATTR_TRAILER = 0x36B7

T_VALUE = 0x38A4
T_VALUE_INT = 0x38A7
T_VALUE_DOUBLE = 0x38A9
T_VALUE_BOOL = 0x38AA
T_VALUE_TIME = 0x38AC
T_VALUE_STRING = 0x38AD
T_VALUE_ARRAY = 0x38AE
T_VALUE_LENGTH = 0x38AF
T_VALUE_POINT3D = 0x38B4
T_VALUE_VECTOR3D = 0x38B5


T_CAMERA_MODEL = 0x01FA
T_PAGES_MODEL = 0x0207
T_CAMERA = 0x34BC
T_PAGE_LIST = 0x6D60
T_PAGE_ITEMS = 0x6D61
T_PAGE = 0x7148
T_PAGE_BASE = 0x6F54


T_DEFINITION = 0x157C
T_DEFINITION_CONTAINER = 0x1388
T_DEFINITION_GUID = 0x157D
T_DEFINITION_NAME = 0x157E
T_DEFINITION_DESCRIPTION = 0x157F
T_DEFINITION_SOURCE = 0x1580
T_DEFINITION_TIMESTAMP = 0x1581
T_DEFINITION_FLAG = 0x1582
T_DEFINITION_KIND = 0x1583
T_DEFINITION_BEHAVIOUR = 0x1B58
T_DEFINITION_ALWAYS_FACE_CAMERA = 0x1B5D
T_DEFINITION_SHADOWS_FACE_SUN = 0x1B5E
T_DEFINITION_LIST = 0x1771
T_DEFINITION_SECTION = 0x1770

T_INSTANCE = 0x1964
T_INSTANCE_NAME = 0x1965
T_INSTANCE_TRANSFORM = 0x1966
T_INSTANCE_DEFINITION = 0x1967
T_INSTANCE_GUID = 0x1968

T_RELATIONSHIP_GROUP = 0x639F
T_RELATIONSHIP_LIST = 0x63A0

T_META = 0x0064
T_META_GUID = 0x0066
T_META_HISTORY = 0x0078
T_META_HISTORY_INNER = 0x00C8
T_META_HISTORY_ENTRY = 0x00C9
T_META_HISTORY_ITEM = 0x00CA
T_META_HISTORY_TIME = 0x00CC

#: Record tags the template reader walks into.  Everything else stays opaque
#: so unknown/future records survive an edit byte for byte.
MODEL_CONTAINERS = frozenset(
    {
        T_DOCUMENT,
        T_NEXT_ID,
        0x03E8,
        T_ENTITIES,
        T_MATERIALS,
        T_MATERIAL_SECTION,
        T_MATERIAL_LIST,
        T_MATERIAL,
        T_ENTITY_BASE,
        T_LAYERS,
        T_LAYER_SECTION,
        T_LAYER_LIST,
        T_LAYER,
        T_DEFINITIONS,
        T_DEFINITION_SECTION,
        T_DEFINITION_LIST,
        T_DEFINITION_CONTAINER,
        T_CAMERA_MODEL,
        T_CAMERA,
        T_PAGES_MODEL,
        T_PAGE_LIST,
        T_PAGE_ITEMS,
        T_PAGE,
        T_PAGE_BASE,
        T_VERTICES,
        T_EDGES,
        T_FACES,
        T_COMPONENTS,
        T_GROUPS,
        T_CURVES,
        T_ARC_CURVES,
        T_TEXTS,
        T_DIMENSIONS,
        T_SECTION_PLANES,
        T_GUIDE_LINES,
        T_GUIDE_POINTS,
        T_RELATIONSHIPS,
    }
)

META_CONTAINERS = frozenset(
    {
        T_META,
        T_META_HISTORY,
        T_META_HISTORY_INNER,
        T_META_HISTORY_ENTRY,
        T_META_HISTORY_ITEM,
    }
)


_POOL_TAGS = (
    T_VERTICES,
    T_CURVES,
    T_ARC_CURVES,
    T_EDGES,
    T_FACES,
    T_COMPONENTS,
    T_GROUPS,
    T_GUIDE_LINES,
    T_GUIDE_POINTS,
    T_SECTION_PLANES,
    T_TEXTS,
    T_DIMENSIONS,
)
_DEFAULT_LAYER_COLOR = (136, 136, 136)


# ── Small record constructors ────────────────────────────────────────────


def _entity_base(entity_id: Optional[int], attributes: Optional[Record] = None) -> Record:
    children: List[Record] = []
    if entity_id is not None:
        children.append(record(T_ENTITY_ID, varint(entity_id)))
    if attributes is not None:
        children.append(attributes)
    return Record(T_ENTITY_BASE, children=children)


def _drawing(
    entity_id: Optional[int],
    material: Optional[int] = None,
    layer: Optional[int] = None,
    flags: int = 0x06,
    attributes: Optional[Record] = None,
) -> Record:
    children = [_entity_base(entity_id, attributes)]
    if material:
        children.append(record(T_FRONT_MATERIAL, varint(material)))
    if layer:
        children.append(record(T_LAYER_REF, varint(layer)))
    if flags:
        children.append(record(T_FLAGS, u8(flags)))
    return Record(T_DRAWING, children=children)


def _material_record(entity_id: int, name: str, owner: int) -> Record:
    children = [
        _entity_base(entity_id),
        record(T_MATERIAL_NAME, name.encode("utf-8")),
        record(T_MATERIAL_OWNER, u8(owner)),
        record(T_MATERIAL_EMPTY, b""),
    ]
    return Record(T_MATERIAL, children=children)


def _definition(
    container: Record,
    name: str,
    guid: bytes,
    timestamp: int,
    kind: int = 0,
    always_faces_camera: bool = False,
    shadows_face_sun: bool = False,
) -> Record:
    behaviour = Record(
        T_DEFINITION_BEHAVIOUR,
        children=[
            record(0x1B59, u32(0)),
            record(0x1B5A, u32(0)),
            record(0x1B5B, u8(0)),
            record(0x1B5C, u8(0)),
            record(T_DEFINITION_ALWAYS_FACE_CAMERA, u8(1 if always_faces_camera else 0)),
            record(T_DEFINITION_SHADOWS_FACE_SUN, u8(1 if shadows_face_sun else 0)),
        ],
    )
    return Record(
        T_DEFINITION,
        children=[
            container,
            record(T_DEFINITION_GUID, guid),
            record(T_DEFINITION_NAME, name.encode("utf-8")),
            record(T_DEFINITION_DESCRIPTION, b""),
            record(T_DEFINITION_SOURCE, b""),
            record(T_DEFINITION_FLAG, u8(0)),
            record(T_DEFINITION_KIND, u32(kind)),
            record(T_DEFINITION_TIMESTAMP, u32(timestamp)),
            behaviour,
        ],
    )


def _empty_relationships() -> Record:
    return Record(
        T_RELATIONSHIPS,
        children=[Record(T_RELATIONSHIP_GROUP, children=[Record(T_RELATIONSHIP_LIST, b"")])],
    )


def _new_id_list_tag(ids: Iterable[int]) -> Record:
    return record(T_ORDER, id_list(ids))


def _decode_id_list(payload: bytes) -> List[int]:
    ids: List[int] = []
    pos = 0
    while pos < len(payload):
        width = payload[pos]
        if width < 1 or width > 8 or pos + 1 + width > len(payload):
            raise ValueError("malformed entity order list")
        ids.append(int.from_bytes(payload[pos + 1 : pos + 1 + width], "little"))
        pos += 1 + width
    return ids


def _identity_of(record_: Record) -> Optional[int]:
    """Persistent id carried by an entity-base record (0x05dc)."""
    if record_.tag != T_ENTITY_BASE:
        return None
    identifier = record_.child(T_ENTITY_ID)
    return None if identifier is None else read_varint(identifier.payload)


def _find_entity_id(record_: Record) -> Optional[int]:
    if record_.children is None:
        return None
    for child in record_.children:
        identifier = _identity_of(child)
        if identifier is not None:
            return identifier
    return None


# ── Attribute dictionaries ───────────────────────────────────────────────


def _attribute_value(value) -> Record:
    """One A438-wrapped attribute value, as SketchUp 2025 writes it."""
    children: List[Record] = []
    if value is None:
        pass
    elif isinstance(value, bool):
        children.append(record(T_VALUE_BOOL, u8(1 if value else 0)))
    elif isinstance(value, Timestamp):
        number = int(value)
        if not 0 <= number < 1 << 32:
            raise SkpWriteError("Timestamp must be in [0, 2**32)")
        children.append(record(T_VALUE_TIME, struct.pack("<I", number)))
    elif isinstance(value, Length):
        number = float(value)
        if not math.isfinite(number):
            raise SkpWriteError("attribute Length values must be finite")
        children.append(record(T_VALUE_LENGTH, f64s([number])))
    elif isinstance(value, int):
        if not -(1 << 31) <= value < 1 << 31:
            raise SkpWriteError("attribute integers must fit a signed 32-bit value")
        children.append(record(T_VALUE_INT, struct.pack("<i", value)))
    elif isinstance(value, float):
        number = float(value)
        if not math.isfinite(number):
            raise SkpWriteError("attribute Float values must be finite")
        children.append(record(T_VALUE_DOUBLE, f64s([number])))
    elif isinstance(value, str):
        children.append(record(T_VALUE_STRING, value.encode("utf-8")))
    elif isinstance(value, Point3d):
        coordinates = [float(value.x), float(value.y), float(value.z)]
        if not all(math.isfinite(number) for number in coordinates):
            raise SkpWriteError("attribute Point3d values must be finite")
        children.append(record(T_VALUE_POINT3D, f64s(coordinates)))
    elif isinstance(value, Vector3d):
        coordinates = [float(value.x), float(value.y), float(value.z)]
        if not all(math.isfinite(number) for number in coordinates):
            raise SkpWriteError("attribute Vector3d values must be finite")
        children.append(record(T_VALUE_VECTOR3D, f64s(coordinates)))
    elif isinstance(value, (list, tuple)):
        children.append(
            Record(
                T_VALUE_ARRAY,
                children=[_attribute_value(element) for element in value],
            )
        )
    else:
        raise SkpWriteError(
            f"unsupported attribute value type: {type(value).__name__}"
        )
    return Record(T_VALUE, children=children)


def _attribute_dictionary(name: str, entries) -> Record:
    children: List[Record] = [record(T_ATTR_EMPTY, b"")]
    children.append(record(T_ATTR_NAME, name.encode("utf-8")))
    pairs: List[Record] = []
    for key, value in entries.items():
        pairs.append(record(T_ATTR_KEY, str(key).encode("utf-8")))
        pairs.append(_attribute_value(value))
    children.append(Record(T_ATTR_ENTRIES, children=pairs))
    children.append(record(T_ATTR_TRAILER, u32(0)))
    return Record(T_ATTR_DICT, children=children)


def _attribute_container(
    attribute_dicts: Optional[Sequence[Tuple[str, Dict[str, object]]]] = None,
    uv: Optional[Record] = None,
) -> Optional[Record]:
    """The 0x05dd attribute block shared by dictionaries and texture placement."""
    if uv is None and not attribute_dicts:
        return None
    children: List[Record] = [
        _attribute_dictionary(name, entries)
        for name, entries in attribute_dicts or ()
    ]
    if uv is not None:
        children.append(uv)
    return Record(
        0x05DD,
        children=[
            Record(T_ATTR_LIST, children=[Record(T_ATTR_SET, children=children)])
        ],
    )


# ── Material XML ─────────────────────────────────────────────────────────

def _pbr_block(x_scale: float, y_scale: float) -> str:
    return (
        f'    <mat:pbrMR xScale="{_xml_number(x_scale)}" yScale="{_xml_number(y_scale)}">\n'
        "      <mat:enable_metalness>0</mat:enable_metalness>\n"
        "      <mat:enable_roughness>0</mat:enable_roughness>\n"
        "      <mat:enable_normal>0</mat:enable_normal>\n"
        "      <mat:enable_occlusion>0</mat:enable_occlusion>\n"
        "      <mat:metallicFactor>1</mat:metallicFactor>\n"
        "      <mat:roughnessFactor>1</mat:roughnessFactor>\n"
        "      <mat:normalMapStyle>1</mat:normalMapStyle>\n"
        "      <mat:normalScale>1</mat:normalScale>\n"
        "      <mat:occlusionStrength>1</mat:occlusionStrength>\n"
        "      <mat:baseColorFactor>4294967295</mat:baseColorFactor>\n"
        "    </mat:pbrMR>\n"
    )


def _xml_number(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _xml_attr(value: str) -> str:
    """Escape a value for a double- or single-quoted XML attribute."""
    return _xml_escape(value, {'"': "&quot;", "'": "&apos;"})


def _validate_xml_name(name: str, kind: str) -> None:
    """Reject characters XML 1.0 cannot hold, before they reach a file."""
    for char in name:
        codepoint = ord(char)
        if (
            (codepoint < 0x20 and char not in "\t\n\r")
            or 0xD800 <= codepoint <= 0xDFFF
            or codepoint in (0xFFFE, 0xFFFF)
        ):
            raise SkpWriteError(
                f"{kind} name contains a character XML cannot hold: {char!r}"
            )


def _packed_average(rgb: Tuple[int, int, int]) -> int:
    red, green, blue = rgb
    return (0xFF << 24) | (blue << 16) | (green << 8) | red


def material_xml(
    name: str,
    rgb: Tuple[int, int, int] = (255, 255, 255),
    *,
    layer_color: bool = False,
    opacity: float = 1.0,
    texture: Optional[Dict[str, object]] = None,
) -> bytes:
    """A ``material.xml`` as SketchUp 2025 writes it.

    A solid-colour material has ``type="0"``. ``layer_color`` marks the embedded material a tag owns
    (``Layer_<name>``); SketchUp writes ``pbrPromoState`` 0 and "belongs to
    another entity" for those.  An opacity below 1 is stored as
    ``useTrans="1"`` with ``trans = 1 - opacity`` (SketchUp's convention).
    """
    _validate_xml_name(name, "material")
    red, green, blue = (max(0, min(255, int(c))) for c in rgb)
    promo = "0" if layer_color else "2"
    opacity = min(max(float(opacity), 0.0), 1.0)
    if opacity >= 1.0:
        trans_attr, use_trans = "0.5", "0"
    else:
        trans_attr, use_trans = _xml_number(1.0 - opacity), "1"
    if texture is None:
        type_attr = "0"
        has_texture = "0"
        block = _pbr_block(1.0, 1.0)
    else:
        width = float(texture["width"])
        height = float(texture["height"])
        filename = str(texture["filename"])
        basename = str(texture["basename"])
        _validate_xml_name(filename, "texture file")
        _validate_xml_name(basename, "texture file")
        packed = _packed_average((red, green, blue))
        type_attr = "1"
        has_texture = "1"
        block = (
            f'    <mat:texture textureFilename="{_xml_attr(filename)}" '
            f'xScale="{_xml_number(width)}" yScale="{_xml_number(height)}" '
            f'avgColor="{packed}">\n'
            "      <mat:images>\n"
            f'        <mat:image id="1" path="./{_xml_attr(basename)}" '
            f'file_name="{_xml_attr(basename)}"/>\n'
            "      </mat:images>\n"
            "    </mat:texture>\n"
            + _pbr_block(width, height)
        )
    text = (
        '<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n'
        '<materialDocument xmlns="http://sketchup.google.com/schemas/sketchup/1.0/material" '
        'xmlns:mat="http://sketchup.google.com/schemas/sketchup/1.0/material">\n'
        f'  <mat:material name="{_xml_attr(name)}" type="{type_attr}" workflow="0" '
        f'colorRed="{red}" colorGreen="{green}" colorBlue="{blue}" '
        f'colorizeType="0" trans="{trans_attr}" useTrans="{use_trans}" '
        f'pbrPromoState="{promo}" hasTexture="{has_texture}">\n'
        + block
        + "  </mat:material>\n"
        "</materialDocument>\n"
    )
    return text.encode("utf-8")


_FOLDER_MAX_LENGTH = 80
_FOLDER_UNSAFE = set('*<>:|?/\\"\t\n\r')


def sanitize_folder(name: str) -> str:
    """SketchUp's archive folder for a material/tag name, before truncation."""
    folder = "".join("_" if char in _FOLDER_UNSAFE else char for char in name)
    return folder or "_"


def _truncate_folder(folder: str) -> str:
    """SketchUp's long-name truncation: repeated floor(2/3) while over 80."""
    while len(folder) > _FOLDER_MAX_LENGTH:
        folder = folder[: (2 * len(folder)) // 3]
        if len(folder) == _FOLDER_MAX_LENGTH:
            folder = folder[: (2 * len(folder)) // 3]
    return folder or "_"


def _material_folder_for(name: str, taken: KeysView[str] | set[str]) -> str:
    """The folder SketchUp would use for *name*, including ``_N`` collisions.

    ``taken`` holds lower-cased folders (Windows is case-insensitive).  On a
    collision SketchUp first strips a Windows drive prefix (``a:b`` -> ``b``)
    and then always starts its suffix at ``_1``.
    """
    base = _truncate_folder(sanitize_folder(name))
    if base.lower() not in taken:
        return base
    stripped = re.sub(r"^[A-Za-z]:", "", name)
    fallback = _truncate_folder(sanitize_folder(stripped))
    counter = 1
    while f"{fallback}_{counter}".lower() in taken:
        counter += 1
    return f"{fallback}_{counter}"


# ── Template ─────────────────────────────────────────────────────────────


class Template:
    """A SketchUp-written VFF document parsed into an editable record tree."""

    def __init__(self, data: bytes, options: Optional[dict] = None) -> None:
        self.options = dict(options or {})
        self._guid_counter = 0
        zip_offset = data.find(b"PK\x03\x04")
        if zip_offset < 0:
            raise ValueError("not a VFF file: no embedded ZIP archive")
        h_offset = header_offset(data)
        self.header = data[:zip_offset]
        self.entries: List[ZipEntry] = entries_of(data)
        self._by_name: Dict[str, ZipEntry] = {e.name: e for e in self.entries}
        if "model.dat" not in self._by_name:
            raise ValueError("template has no model.dat")
        model = self._by_name["model.dat"].data
        top = parse_records(model, MODEL_CONTAINERS)
        if len(top) != 1 or top[0].tag != T_DOCUMENT:
            raise ValueError("template model.dat is not a single 0x01f4 document")
        self.document = top[0]
        self.sections: Dict[int, Record] = {}
        for child in self.document.children or []:
            self.sections.setdefault(child.tag, child)
        for required in (T_NEXT_ID, T_ENTITIES, T_MATERIALS, T_LAYERS, T_DEFINITIONS):
            if required not in self.sections:
                raise ValueError(
                    f"template is missing the 0x{required:04x} section this writer edits"
                )
        self.header_offset = h_offset
        self.next_id = self._read_next_id()
        self.layers: Dict[str, int] = {}
        self.layer_ids: Dict[int, str] = {}
        self._layer_names: Dict[str, str] = {}
        self.materials: Dict[str, int] = {}
        self.material_ids: Dict[int, str] = {}
        self._material_names: Dict[str, str] = {}
        self.texture_sizes: Dict[int, Tuple[float, float]] = {}
        self.material_folders: List[str] = [
            entry.name.split("/")[1]
            for entry in self.entries
            if entry.name.startswith("materials/")
            and entry.name.endswith("/material.xml")
        ]
        self._material_folder_keys = {
            folder.lower() for folder in self.material_folders
        }
        self._read_layers()
        self._read_materials()
        self.default_layer_id = self.layers.get("Layer0") or (
            next(iter(self.layers.values())) if self.layers else None
        )
        self.font_id = self._read_font_id()
        self.definitions = self._read_definitions()
        self.new_entries: List[ZipEntry] = []
        self._entry_names = set(self._by_name)

    def guid_bytes(self) -> bytes:
        """Windows-order GUID bytes; random by default, reproducible when
        ``options['guid_seed']`` is set."""
        seed = self.options.get("guid_seed")
        if seed is None:
            return uuid.uuid4().bytes_le
        state = (int(seed) ^ (self._guid_counter * 0x9E3779B9)) & 0xFFFFFFFF
        self._guid_counter += 1
        out = bytearray()
        for _ in range(4):
            state ^= (state << 13) & 0xFFFFFFFF
            state ^= state >> 17
            state ^= (state << 5) & 0xFFFFFFFF
            state &= 0xFFFFFFFF
            out += struct.pack("<I", state)
        return bytes(out)

    def model_guid_bytes(self) -> bytes:
        supplied = self.options.get("model_guid")
        if supplied is not None:
            if isinstance(supplied, str):
                return bytes.fromhex(supplied.replace("-", ""))
            return bytes(supplied)
        return self.guid_bytes()

    def save_time(self) -> int:
        return int(self.options.get("save_time", int(time.time())))

    def definition_time(self) -> int:
        return int(self.options.get("definition_time", int(time.time())))


    def _read_definitions(self) -> List[dict]:
        """Existing definitions: name, container id, group flag and bounds."""
        section = self.sections.get(T_DEFINITIONS)
        if section is None:
            return []
        wrapper = section.child(T_DEFINITION_SECTION)
        listing = wrapper.child(T_DEFINITION_LIST) if wrapper is not None else None
        if listing is None or listing.children is None:
            return []
        definitions: List[dict] = []
        for definition in listing.children:
            if definition.tag != T_DEFINITION:
                continue
            try:
                parts = parse_records(
                    definition.payload,
                    {T_DEFINITION_CONTAINER, T_DRAWING, T_ENTITY_BASE},
                )
            except ValueError:
                continue
            container = next(
                (part for part in parts if part.tag == T_DEFINITION_CONTAINER), None
            )
            drawing = (
                container.child(T_DRAWING)
                if container is not None and container.children is not None
                else None
            )
            identifier = _find_entity_id(drawing) if drawing is not None else None
            name_node = next(
                (part for part in parts if part.tag == T_DEFINITION_NAME), None
            )
            kind_node = next(
                (part for part in parts if part.tag == T_DEFINITION_KIND), None
            )
            bounds_node = (
                container.child(T_BOUNDS)
                if container is not None and container.children is not None
                else None
            )
            bounds = None
            if bounds_node is not None and len(bounds_node.payload) == 48:
                bounds = list(struct.unpack("<6d", bounds_node.payload))
            definitions.append({
                "id": identifier,
                "name": name_node.payload.decode("utf-8", errors="replace")
                        if name_node is not None else "",
                "is_group": bool(
                    kind_node is not None
                    and int.from_bytes(kind_node.payload, "little") == 1
                ),
                "bounds": bounds,
            })
        return definitions


    # -- ids --------------------------------------------------------------

    def alloc_id(self) -> int:
        value = self.next_id
        self.next_id += 1
        return value

    def _read_next_id(self) -> int:
        section = self.sections[T_NEXT_ID]
        wrapper = section.child(0x03E8)
        if wrapper is None:
            raise ValueError("template has no next-id record")
        value = wrapper.child(0x03E9)
        if value is None:
            raise ValueError("template has an empty next-id record")
        return read_varint(value.payload)

    # -- existing materials / layers --------------------------------------

    def _material_list(self) -> Record:
        section = self.sections[T_MATERIALS]
        wrapper = section.child(T_MATERIAL_SECTION)
        if wrapper is None:
            raise ValueError("template materials section is malformed")
        entries = wrapper.child(T_MATERIAL_LIST)
        if entries is None:
            entries = Record(T_MATERIAL_LIST, children=[])
            wrapper.children.append(entries)
        if entries.children is None:
            entries.children = []
        return entries

    def _layer_list(self) -> Record:
        section = self.sections[T_LAYERS]
        wrapper = section.child(T_LAYER_SECTION)
        if wrapper is None:
            raise ValueError("template layers section is malformed")
        entries = wrapper.child(T_LAYER_LIST)
        if entries is None:
            entries = Record(T_LAYER_LIST, children=[])
            wrapper.children.append(entries)
        if entries.children is None:
            entries.children = []
        return entries

    def _definition_list(self) -> Record:
        section = self.sections[T_DEFINITIONS]
        wrapper = section.child(T_DEFINITION_SECTION)
        if wrapper is None:
            raise ValueError("template definitions section is malformed")
        entries = wrapper.child(T_DEFINITION_LIST)
        if entries is None:
            entries = Record(T_DEFINITION_LIST, children=[])
            wrapper.children.append(entries)
        if entries.children is None:
            entries.children = []
        return entries

    def _read_layers(self) -> None:
        for layer in self._layer_list().children or []:
            if layer.tag != T_LAYER:
                continue
            identifier = _find_entity_id(layer)
            name_node = layer.child(T_LAYER_NAME)
            if identifier is None or name_node is None:
                continue
            name = name_node.payload.decode("utf-8", "replace")
            self.layers.setdefault(name, identifier)
            self.layer_ids[identifier] = name
            self._layer_names.setdefault(name.lower(), name)

    def _material_xml_name(self, folder: str) -> str:
        """The display name SketchUp wrote inside a material folder."""
        entry = self._by_name.get(f"materials/{folder}/material.xml")
        if entry is None:
            return folder
        try:
            root = ET.fromstring(entry.data)
            namespace = "{http://sketchup.google.com/schemas/sketchup/1.0/material}"
            material = root.find(f".//{namespace}material")
            if material is not None and material.get("name"):
                return material.get("name")
        except (ET.ParseError, UnicodeDecodeError):
            pass
        return folder

    def _read_materials(self) -> None:
        for material in self._material_list().children or []:
            if material.tag != T_MATERIAL:
                continue
            identifier = _find_entity_id(material)
            folder_node = material.child(T_MATERIAL_NAME)
            if identifier is None or folder_node is None:
                continue
            # The record stores the archive folder; the real material name
            # lives in that folder's material.xml (a/b vs its a_b folder).
            folder = folder_node.payload.decode("utf-8", "replace")
            name = self._material_xml_name(folder)
            self.material_ids[identifier] = name
            self.materials[name] = identifier
            self._material_names.setdefault(name.lower(), name)

    # -- adding materials / layers ---------------------------------------

    def _unique_material_name(self, name: str) -> str:
        if name in self.materials:
            return name
        if name.lower() in self._material_names:
            base = name
            counter = 1
            while f"{base}{counter}".lower() in self._material_names:
                counter += 1
            return f"{base}{counter}"
        return name

    def add_material(
        self, name: str, rgb: Tuple[int, int, int], opacity: float = 1.0
    ) -> int:
        _validate_xml_name(name, "material")
        if name in self.materials:
            return self.materials[name]
        name = self._unique_material_name(name)
        folder = _material_folder_for(name, self._material_folder_keys)
        identifier = self.alloc_id()
        self._material_list().children.append(_material_record(identifier, folder, 0))
        self.material_ids[identifier] = name
        self.material_folders.append(folder)
        self._material_folder_keys.add(folder.lower())
        self.materials[name] = identifier
        self._material_names[name.lower()] = name
        self._add_entry(
            f"materials/{folder}/material.xml",
            material_xml(name, rgb, opacity=opacity),
        )
        return identifier


    def add_texture_material(
        self,
        name: str,
        image: bytes,
        image_name: str,
        width: float,
        height: float,
        opacity: float,
        average_rgb: Tuple[int, int, int],
        texture_filename: str,
    ) -> int:
        """Append an image-textured material and its two archive entries."""
        if name in self.materials:
            return self.materials[name]
        if width <= 0 or height <= 0:
            raise SkpWriteError("a texture's applied size must be positive")
        name = self._unique_material_name(name)
        folder = _material_folder_for(name, self._material_folder_keys)
        identifier = self.alloc_id()
        self._material_list().children.append(
            _material_record(identifier, folder, 0)
        )
        self.material_ids[identifier] = name
        self.material_folders.append(folder)
        self._material_folder_keys.add(folder.lower())
        self.materials[name] = identifier
        self._material_names[name.lower()] = name
        self.texture_sizes[identifier] = (float(width), float(height))
        self._add_entry(
            f"materials/{folder}/{image_name}",
            image,
            flag=0x0000,
        )
        self._add_entry(
            f"materials/{folder}/material.xml",
            material_xml(
                name,
                average_rgb,
                opacity=opacity,
                texture={
                    "width": float(width),
                    "height": float(height),
                    "filename": texture_filename,
                    "basename": image_name,
                },
            ),
        )
        return identifier

    def add_layer(
        self,
        name: str,
        color: Optional[Tuple[int, int, int]] = None,
        hidden: bool = False,
    ) -> int:
        _validate_xml_name(name, "tag")
        if name in self.layers:
            return self.layers[name]
        if name.lower() in self._layer_names:
            # SketchUp's layer manager is case-insensitive: "wood" after
            # "Wood" returns the same tag rather than creating a second one.
            return self.layers[self._layer_names[name.lower()]]
        colour = color if color is not None else _DEFAULT_LAYER_COLOR
        identifier = self.alloc_id()
        material_id = self.alloc_id()
        folder = _material_folder_for(f"Layer_{name}", self._material_folder_keys)
        self.material_folders.append(folder)
        self._material_folder_keys.add(folder.lower())
        layer = Record(
            T_LAYER,
            children=[
                _entity_base(identifier),
                record(T_LAYER_NAME, name.encode("utf-8")),
                record(T_LAYER_HIDDEN, u8(1 if hidden else 0)),
                Record(
                    T_LAYER_MATERIAL,
                    children=[_material_record(material_id, folder, 1)],
                ),
                record(T_LAYER_TAIL, u32(0)),
            ],
        )
        self._layer_list().children.append(layer)
        self.layers[name] = identifier
        self.layer_ids[identifier] = name
        self._layer_names[name.lower()] = name
        self._add_entry(
            f"materials/{folder}/material.xml",
            material_xml(f"Layer_{name}", colour, layer_color=True),
        )
        return identifier

    def add_definition(
        self,
        container: Record,
        name: str,
        container_id: int,
        kind: int = 0,
        *,
        guid: Optional[bytes] = None,
        always_faces_camera: bool = False,
        shadows_face_sun: bool = False,
    ) -> int:
        definition = _definition(
            container,
            name,
            self.guid_bytes() if guid is None else guid,
            self.definition_time(),
            kind=kind,
            always_faces_camera=always_faces_camera,
            shadows_face_sun=shadows_face_sun,
        )
        self._definition_list().children.append(definition)
        return container_id

    def _add_entry(self, name: str, data: bytes, flag: int = 0x0006) -> None:
        if name in self._entry_names:
            raise ValueError(f"archive entry already exists: {name}")
        self._entry_names.add(name)
        self.new_entries.append(ZipEntry(name=name, data=data, flag=flag))

    # -- meta.dat ---------------------------------------------------------

    def _meta_bytes(self) -> bytes:
        meta = self._by_name["meta/meta.dat"].data
        records = parse_records(meta, META_CONTAINERS)
        if len(records) != 1 or records[0].tag != T_META:
            raise ValueError("template meta.dat is not a single 0x0064 record")
        root = records[0]

        guid = root.child(T_META_GUID)
        if guid is None:
            raise ValueError("template meta.dat has no model GUID")
        guid.payload = self.model_guid_bytes()

        history = root.child(T_META_HISTORY)
        if history is not None:
            wrappers = history.child(T_META_HISTORY_INNER)
            entries = (
                wrappers.child(T_META_HISTORY_ENTRY) if wrappers is not None else None
            )
            items = entries.children_with(T_META_HISTORY_ITEM) if entries else []
            if items:
                stamp = items[-1].child(T_META_HISTORY_TIME)
                if stamp is not None:
                    stamp.payload = struct.pack(
                        "<I", self.save_time() & 0xFFFFFFFF
                    )
        return encode_records(records)


    @property
    def root_container(self) -> Record:
        """The model's own ``0x1388`` entity container."""
        section = self.sections[T_ENTITIES]
        container = section.child(T_DEFINITION_CONTAINER)
        if container is None or container.children is None:
            raise ValueError("template model has no root entity container")
        return container

    # -- output -----------------------------------------------------------

    def _read_font_id(self) -> Optional[int]:
        section = self.sections.get(0x01FD)
        if section is None:
            return None
        try:
            nodes = parse_records(
                section.payload, {0x4E20, 0x4E21, 0x5014, T_ENTITY_BASE}
            )
        except ValueError:
            return None
        for wrapper in nodes:
            if wrapper.tag != 0x4E20 or wrapper.children is None:
                continue
            for pool in wrapper.children:
                if pool.tag != 0x4E21 or pool.children is None:
                    continue
                for font in pool.children:
                    if font.tag == 0x5014:
                        identifier = _find_entity_id(font)
                        if identifier is not None:
                            return identifier
        return None










    def build(self) -> bytes:
        next_section = self.sections[T_NEXT_ID]
        next_section.children = [
            Record(0x03E8, children=[record(0x03E9, varint(self.next_id))])
        ]
        model_dat = self.document.encode()
        if len(model_dat) >= 1 << 32:
            raise ValueError("model.dat is too large to write")

        meta = self._meta_bytes()

        entries: List[ZipEntry] = []
        thumbnails: List[ZipEntry] = []
        materials: List[ZipEntry] = []
        others: List[ZipEntry] = []
        for entry in self.entries:
            if entry.name == "model.dat":
                continue
            if entry.name == "meta/meta.dat":
                continue
            if entry.name in ("meta/model_thumbnail.png", "meta/preview_thumbnail.png"):
                thumbnails.append(ZipEntry(entry.name, entry.data, 0x0000))
            elif entry.name.startswith("materials/"):
                materials.append(entry)
            elif entry.name.startswith("meta/"):
                others.append(entry)
            else:
                others.append(entry)

        # ``meta/meta.dat`` sits at index 2 in every file this integrity rule
        # has been checked against, so the writer keeps that slot even when a
        # template (an SDK save, for example) has no thumbnail entries.
        if len(thumbnails) < 2:
            existing = {e.name for e in thumbnails}
            for fallback in ("meta/model_thumbnail.png", "meta/preview_thumbnail.png"):
                if fallback not in existing:
                    thumbnails.append(ZipEntry(fallback, _TINY_PNG, 0x0000))
        thumbnails = [
            next(e for e in thumbnails if e.name == "meta/model_thumbnail.png"),
            next(e for e in thumbnails if e.name == "meta/preview_thumbnail.png"),
        ]

        entries.extend(thumbnails)
        entries.append(ZipEntry("meta/meta.dat", meta, 0x0006))
        entries.extend(materials)
        # New material entries follow the template's own material folders.
        entries.extend(self.new_entries)
        entries.extend(others)
        entries.append(ZipEntry("model.dat", model_dat, 0x0006))
        # The template's own header (including its model revision) is kept.
        return build_skp(self.header, entries)


#: Valid 1x1 transparent PNG, used only to fill the two thumbnail slots of a
#: template that has none (the slots keep meta.dat at index 2).
_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c626000000000ffff03000006000557bfabd400000000"
    "49454e44ae426082"
)


# ── Entity containers ───────────────────────────────────────────────────


class EntityEditor:
    """Build vertices/edges/faces/instances inside one ``0x1388`` container.

    ``container`` is an existing container parsed from the template (the model
    root); ``container_id`` makes a fresh container for a component
    definition.  New records are appended to the template's own pools when
    there are any, so template geometry and new geometry coexist.
    """

    def __init__(
        self,
        template: Template,
        *,
        container: Optional[Record] = None,
        container_id: Optional[int] = None,
        attributes: Optional[Sequence[Tuple[str, Dict[str, object]]]] = None,
    ) -> None:
        self.template = template
        self.fresh = container is None
        self.container = (
            container
            if container is not None
            else self._blank_container(container_id, _attribute_container(attributes))
        )
        self.container_id = container_id
        self.vertices: Dict[Tuple[float, float, float], int] = {}
        self.edges: Dict[Tuple[int, int], Tuple[int, int]] = {}
        self.order: List[int] = []
        self.bounds = None  # [minx, miny, minz, maxx, maxy, maxz]
        self.active_section: Optional[int] = None
        self._pools: Dict[int, Record] = {}
        for child in self.container.children or []:
            if child.tag in _POOL_TAGS and child.children is not None:
                self._pools[child.tag] = child
        self._existing_order_list = self._read_order()
        self._existing_order = set(self._existing_order_list)

    @staticmethod
    def _blank_container(
        container_id: Optional[int], attributes: Optional[Record] = None
    ) -> Record:
        children: List[Record] = [_drawing(container_id, attributes=attributes)]
        children.append(Record(T_EMPTY, children=[]))
        children.append(_empty_relationships())
        children.append(record(T_FLAGS_CONTAINER, u8(0)))
        return Record(T_DEFINITION_CONTAINER, children=children)

    def _read_order(self) -> List[int]:
        order = self.container.child(T_ORDER)
        if order is None or order.children is not None:
            return []
        try:
            return _decode_id_list(order.payload)
        except ValueError:
            return []

    def _pool(self, tag: int) -> Record:
        pool = self._pools.get(tag)
        if pool is None:
            pool = Record(tag, children=[])
            self._pools[tag] = pool
        return pool

    # -- bounds -----------------------------------------------------------

    def _extend_bounds(self, point: Sequence[float]) -> None:
        if self.bounds is None:
            self.bounds = [point[0], point[1], point[2], point[0], point[1], point[2]]
            return
        b = self.bounds
        b[0] = min(b[0], point[0])
        b[1] = min(b[1], point[1])
        b[2] = min(b[2], point[2])
        b[3] = max(b[3], point[0])
        b[4] = max(b[4], point[1])
        b[5] = max(b[5], point[2])

    # -- primitives -------------------------------------------------------

    def add_vertex(self, point: Sequence[float]) -> int:
        key = (float(point[0]), float(point[1]), float(point[2]))
        existing = self.vertices.get(key)
        if existing is not None:
            return existing
        identifier = self.template.alloc_id()
        vertex = Record(
            T_VERTEX,
            children=[_entity_base(identifier), record(T_POINT, f64s(key))],
        )
        self._pool(T_VERTICES).children.append(vertex)
        self.vertices[key] = identifier
        self._extend_bounds(key)
        return identifier

    def add_edge(
        self,
        start: int,
        end: int,
        layer: Optional[int],
        *,
        flags: int = 0x06,
        curve: Optional[int] = None,
    ) -> Tuple[int, bool]:
        """Return ``(edge id, forward)`` for the start-to-end direction."""
        key = (start, end) if start <= end else (end, start)
        existing = self.edges.get(key)
        if existing is not None:
            identifier, stored_start = existing
            return identifier, stored_start == start
        identifier = self.template.alloc_id()
        children: List[Record] = [
            _drawing(identifier, layer=layer, flags=flags),
            record(T_EDGE_START, varint(start)),
            record(T_EDGE_END, varint(end)),
        ]
        if curve is not None:
            children.append(record(T_EDGE_CURVE, varint(curve)))
        edge = Record(T_EDGE, children=children)
        self._pool(T_EDGES).children.append(edge)
        self.edges[key] = (identifier, start)
        self.order.append(identifier)
        return identifier, True

    def _face_attribute_block(
        self,
        attributes: Optional[Sequence[Tuple[str, Dict[str, object]]]],
        front_uv: Optional[Sequence[Tuple[Sequence[float], Tuple[float, float]]]],
        back_uv: Optional[Sequence[Tuple[Sequence[float], Tuple[float, float]]]],
        material: Optional[int],
        back_material: Optional[int],
        normal: Sequence[float],
    ) -> Optional[Record]:
        uv_coords = None
        if front_uv is not None or back_uv is not None:
            front_matrix = (
                None
                if front_uv is None
                else self._uv_placement(front_uv, material, normal)
            )
            back_matrix = (
                None
                if back_uv is None
                else self._uv_placement(back_uv, back_material, normal)
            )
            uv_coords = uv_attribute(front_matrix, back_matrix)
        return _attribute_container(attributes, uv=uv_coords)

    def validate_face_attributes(
        self,
        attributes: Optional[Sequence[Tuple[str, Dict[str, object]]]],
        front_uv: Optional[Sequence[Tuple[Sequence[float], Tuple[float, float]]]],
        back_uv: Optional[Sequence[Tuple[Sequence[float], Tuple[float, float]]]],
        material: Optional[int],
        back_material: Optional[int],
        normal: Sequence[float],
    ) -> None:
        """Raise before geometry is added if attrs/UV cannot be encoded."""
        self._face_attribute_block(
            attributes, front_uv, back_uv, material, back_material, normal
        )

    def add_face(
        self,
        points: Sequence[Sequence[float]],
        *,
        material: Optional[int] = None,
        layer: Optional[int] = None,
        back_material: Optional[int] = None,
        hidden: bool = False,
        holes: Sequence[Sequence[Sequence[float]]] = (),
        edge_flags: int = 0x06,
        front_uv: Optional[
            Sequence[Tuple[Sequence[float], Tuple[float, float]]]
        ] = None,
        back_uv: Optional[
            Sequence[Tuple[Sequence[float], Tuple[float, float]]]
        ] = None,
        attributes: Optional[Sequence[Tuple[str, Dict[str, object]]]] = None,
    ) -> int:
        outer = [tuple(float(c) for c in p) for p in points]
        if len(outer) < 3:
            raise ValueError("a face needs at least three points")
        normal = unit_normal(newell_normal(outer))
        plane_d = -(
            normal[0] * outer[0][0]
            + normal[1] * outer[0][1]
            + normal[2] * outer[0][2]
        )
        rings = [outer]
        for hole in holes:
            source = getattr(hole, "points", hole)
            ring = [tuple(float(c) for c in p) for p in source]
            if len(ring) < 3:
                raise ValueError("a hole needs at least three points")
            for index in range(len(ring)):
                if ring[index] == ring[(index + 1) % len(ring)]:
                    raise ValueError("a face ring has a repeated adjacent point")
            hole_normal = newell_normal(ring)
            if sum(a * b for a, b in zip(hole_normal, normal)) > 0:
                ring = list(reversed(ring))
            rings.append(ring)
        for index in range(len(outer)):
            if outer[index] == outer[(index + 1) % len(outer)]:
                raise ValueError("a face ring has a repeated adjacent point")
        attribute_block = self._face_attribute_block(
            attributes, front_uv, back_uv, material, back_material, normal
        )
        loops: List[Record] = []
        for ring in rings:
            uses: List[Record] = []
            for index in range(len(ring)):
                start_point = ring[index]
                end_point = ring[(index + 1) % len(ring)]
                start = self.add_vertex(start_point)
                end = self.add_vertex(end_point)
                edge_id, forward = self.add_edge(start, end, layer, flags=edge_flags)
                uses.append(
                    Record(
                        T_EDGE_USE,
                        children=[
                            record(T_EDGE_USE_ID, varint(edge_id)),
                            record(T_EDGE_USE_FORWARD, u8(0 if forward else 1)),
                        ],
                    )
                )
            loops.append(
                Record(T_LOOP, children=[Record(T_LOOP_EDGES, children=uses)])
            )
        identifier = self.template.alloc_id()
        flags = 0x06 | (0x01 if hidden else 0)
        children: List[Record] = [
            _drawing(
                identifier,
                material=material,
                layer=layer,
                flags=flags,
                attributes=attribute_block,
            ),
            record(T_PLANE, f64s([*normal, plane_d])),
            Record(T_LOOPS, children=loops),
        ]
        if back_material:
            children.append(record(T_BACK_MATERIAL, varint(back_material)))
        self._pool(T_FACES).children.append(Record(T_FACE, children=children))
        self.order.append(identifier)
        return identifier


    def _uv_placement(
        self,
        pins: Sequence[Tuple[Sequence[float], Tuple[float, float]]],
        material: Optional[int],
        normal: Sequence[float],
    ) -> List[float]:
        size = (
            self.template.texture_sizes.get(material)
            if material is not None
            else None
        )
        tile_width, tile_height = size if size is not None else (1.0, 1.0)
        try:
            return uv_matrix_from_pins(pins, normal, tile_width, tile_height)
        except ValueError as exc:
            raise SkpWriteError(str(exc)) from exc

    def add_instance(
        self,
        definition_container_id: int,
        matrix13: Sequence[float],
        *,
        definition_bounds: Optional[Sequence[float]] = None,
        name: str = "",
        material: Optional[int] = None,
        layer: Optional[int] = None,
        hidden: bool = False,
        attributes: Optional[Sequence[Tuple[str, Dict[str, object]]]] = None,
    ) -> int:
        identifier = self.template.alloc_id()
        flags = 0x06 | (0x01 if hidden else 0)
        instance = Record(
            T_INSTANCE,
            children=[
                _drawing(
                    identifier,
                    material=material,
                    layer=layer,
                    flags=flags,
                    attributes=_attribute_container(attributes),
                ),
                record(T_INSTANCE_NAME, name.encode("utf-8")),
                record(T_INSTANCE_TRANSFORM, f64s(matrix13)),
                record(T_INSTANCE_DEFINITION, varint(definition_container_id)),
                record(T_INSTANCE_GUID, self.template.guid_bytes()),
            ],
        )
        self._pool(T_COMPONENTS).children.append(instance)
        self.order.append(identifier)
        if definition_bounds is not None:
            for corner in _box_corners(definition_bounds):
                self._extend_bounds(_transform_point(corner, matrix13))
        return identifier

    def add_group(
        self,
        definition_container_id: int,
        matrix13: Sequence[float],
        *,
        definition_bounds: Optional[Sequence[float]] = None,
        name: str = "",
        material: Optional[int] = None,
        layer: Optional[int] = None,
        hidden: bool = False,
        attributes: Optional[Sequence[Tuple[str, Dict[str, object]]]] = None,
    ) -> int:
        """Place a group: a ``0x1d4c`` wrapper around an ordinary instance."""
        identifier = self.template.alloc_id()
        flags = 0x06 | (0x01 if hidden else 0)
        instance = Record(
            T_INSTANCE,
            children=[
                _drawing(
                    identifier,
                    material=material,
                    layer=layer,
                    flags=flags,
                    attributes=_attribute_container(attributes),
                ),
                record(T_INSTANCE_NAME, name.encode("utf-8")),
                record(T_INSTANCE_TRANSFORM, f64s(matrix13)),
                record(T_INSTANCE_DEFINITION, varint(definition_container_id)),
                record(T_INSTANCE_GUID, self.template.guid_bytes()),
            ],
        )
        self._pool(T_GROUPS).children.append(Record(T_GROUP, children=[instance]))
        self.order.append(identifier)
        if definition_bounds is not None:
            for corner in _box_corners(definition_bounds):
                self._extend_bounds(_transform_point(corner, matrix13))
        return identifier

    def _font_id(self) -> int:
        font = self.template.font_id
        if font is None:
            raise SkpWriteError("the template has no font record to reference")
        return font

    @staticmethod
    def _connection(
        point: Optional[Sequence[float]] = None,
        vertex_id: Optional[int] = None,
    ) -> Record:
        if vertex_id is None:
            if point is None:
                raise ValueError("a free connection needs a point")
            children = [
                record(T_CONNECTION_TYPE, u32(1)),
                record(T_CONNECTION_POINT, f64s([float(c) for c in point])),
                Record(
                    T_CONNECTION_REF_A,
                    children=[Record(T_CONNECTION_REF, children=[record(T_CONNECTION_PATH, b"")])],
                ),
                Record(
                    T_CONNECTION_REF_B,
                    children=[Record(T_CONNECTION_REF, children=[record(T_CONNECTION_PATH, b"")])],
                ),
            ]
        else:
            ref = Record(
                T_CONNECTION_REF,
                children=[
                    record(T_CONNECTION_VERTEX, varint(vertex_id)),
                    record(T_CONNECTION_PATH, b""),
                ],
            )
            children = [
                record(T_CONNECTION_TYPE, u32(2)),
                record(T_CONNECTION_POINT, f64s([0.0, 0.0, 0.0])),
                Record(T_CONNECTION_REF_A, children=[ref]),
                Record(
                    T_CONNECTION_REF_B,
                    children=[
                        Record(
                            T_CONNECTION_REF,
                            children=[record(T_CONNECTION_PATH, b"")],
                        )
                    ],
                ),
            ]
        return Record(T_CONNECTION, children=children)

    def add_text(
        self,
        text: str,
        point: Sequence[float],
        label_point: Sequence[float],
        *,
        leader_type: int = 2,
        arrow_type: int = 3,
        screen_position: Optional[Tuple[float, float]] = None,
        display_leader: bool = True,
        layer: Optional[int] = None,
        hidden: bool = False,
    ) -> int:
        identifier = self.template.alloc_id()
        anchor = tuple(float(c) for c in point)
        label = tuple(float(c) for c in label_point)
        sx, sy = screen_position if screen_position is not None else (0.0, 0.0)
        item = Record(
            T_TEXT,
            children=[
                _drawing(
                    identifier,
                    layer=layer,
                    flags=0x06 | (0x01 if hidden else 0),
                ),
                record(T_TEXT_STRING, text.encode("utf-8")),
                record(T_TEXT_EMPTY, b""),
                record(T_TEXT_ZERO_A, f64s([float(sx)])),
                record(T_TEXT_ZERO_B, f64s([float(sy)])),
                Record(
                    T_TEXT_ANCHOR, children=[self._connection(point=anchor)]
                ),
                record(T_TEXT_VECTOR, f64s(label)),
                record(T_TEXT_ONE, u8(1)),
                record(T_TEXT_UP, f64s([0.0, 0.0, 1.0])),
                record(T_TEXT_ZERO, u8(0)),
                record(T_TEXT_FONT, varint(self._font_id())),
                record(T_TEXT_ARROW, u32(arrow_type)),
                record(T_TEXT_LEADER_FLAG_A, u32(1)),
                record(T_TEXT_LEADER_FLAG, u8(1 if display_leader else 0)),
                record(T_TEXT_LEADER_TYPE, u32(leader_type)),
                record(T_TEXT_SCREEN, u32(1 if screen_position is not None else 0)),
            ],
        )
        self._pool(T_TEXTS).children.append(item)
        self.order.append(identifier)
        return identifier

    def add_dimension(
        self,
        p1: Sequence[float],
        p2: Sequence[float],
        normal: Sequence[float],
        xaxis: Sequence[float],
        offset: float,
        *,
        layer: Optional[int] = None,
        hidden: bool = False,
    ) -> int:
        identifier = self.template.alloc_id()
        item = Record(
            T_DIMENSION,
            children=[
                Record(
                    T_DIMENSION_HEAD,
                    children=[
                        _drawing(
                            identifier,
                            layer=layer,
                            flags=0x06 | (0x01 if hidden else 0),
                        ),
                        record(T_DIMENSION_HEAD_EMPTY, b""),
                        record(T_DIMENSION_FONT, varint(self._font_id())),
                        record(T_DIMENSION_HEAD_FLAG, u8(0)),
                        record(T_DIMENSION_HEAD_ARROW, u32(3)),
                    ],
                ),
                Record(T_DIM_START, children=[self._connection(point=p1)]),
                Record(T_DIM_END, children=[self._connection(point=p2)]),
                record(T_DIM_NORMAL, f64s([float(c) for c in normal])),
                record(T_DIM_XAXIS, f64s([float(c) for c in xaxis])),
                record(T_DIM_ONE, u32(1)),
                record(T_DIM_ZERO, f64s([0.0])),
                record(T_DIM_OFFSET, f64s([float(offset)])),
                record(T_DIM_ARROW, u32(3)),
            ],
        )
        self._pool(T_DIMENSIONS).children.append(item)
        self.order.append(identifier)
        return identifier




    def add_curve_edges(
        self,
        points: Sequence[Sequence[float]],
        *,
        closed: bool = False,
        arc_frame: Optional[Sequence[float]] = None,
        layer: Optional[int] = None,
        flags: int = 0x06,
    ) -> Tuple[int, List[int]]:
        """Create curves over the not-yet-existing edges of *points*.

        Edges that already exist are left out; the remaining new edges are
        split into contiguous runs (a run wrapping past a closed ring's end
        is one run) and only runs of at least two edges get a curve record.
        This matches create.py's post-review behaviour and keeps the curve
        records consistent with the edges that actually point at them.
        """
        if len(points) < 2:
            raise ValueError("a curve needs at least two points")
        coordinates = [tuple(float(c) for c in point) for point in points]
        count = len(coordinates) if closed else len(coordinates) - 1
        temp_ids: Dict[Tuple[float, float, float], int] = {}
        next_temp = -1

        def vertex_id(point: Tuple[float, float, float]) -> int:
            nonlocal next_temp
            existing = self.vertices.get(point)
            if existing is not None:
                return existing
            if point not in temp_ids:
                temp_ids[point] = next_temp
                next_temp -= 1
            return temp_ids[point]

        simulated = {frozenset(key) for key in self.edges}
        seg_new: List[bool] = []
        for index in range(count):
            start = coordinates[index % len(coordinates)]
            end = coordinates[(index + 1) % len(coordinates)]
            if start == end:
                raise ValueError("a curve has a repeated adjacent point")
            key = frozenset((vertex_id(start), vertex_id(end)))
            if key in simulated:
                seg_new.append(False)
            else:
                simulated.add(key)
                seg_new.append(True)

        runs: List[List[int]] = []
        current: List[int] = []
        for index, is_new in enumerate(seg_new):
            if is_new:
                current.append(index)
            elif current:
                runs.append(current)
                current = []
        if current:
            runs.append(current)
        if (
            closed
            and len(runs) >= 2
            and runs[0][0] == 0
            and runs[-1][-1] == count - 1
        ):
            runs = [runs[-1] + runs[0]] + runs[1:-1]
        runs = [run for run in runs if len(run) >= 2]

        curve_for_seg: Dict[int, int] = {}
        run_curves: List[Tuple[int, List[int], Optional[List[float]]]] = []
        for run in runs:
            curve_id = self.template.alloc_id()
            frame = arc_frame
            if frame is not None and not (
                len(run) == count and run[0] == 0
            ):
                center = frame[0:3]
                normal = frame[3:6]
                offset = frame[6]
                x_axis = frame[7:10]
                y_axis = frame[10:13]
                radius = frame[13]
                start_angle = frame[14]
                end_angle = frame[15]
                sweep = end_angle - start_angle
                first = run[0]
                a0 = start_angle + sweep * (first / count)
                a1 = start_angle + sweep * ((first + len(run)) / count)
                if closed and first + len(run) > count:
                    a0 -= 2.0 * math.pi
                    a1 -= 2.0 * math.pi
                frame = [
                    *center,
                    *normal,
                    offset,
                    *x_axis,
                    *y_axis,
                    radius,
                    a0,
                    a1,
                ]
            run_curves.append((curve_id, run, frame))
            for index in run:
                curve_for_seg[index] = curve_id

        edges: List[int] = []
        run_edges: Dict[int, List[int]] = {
            curve_id: [] for curve_id, _run, _frame in run_curves
        }
        for index in range(count):
            start = self.add_vertex(coordinates[index % len(coordinates)])
            end = self.add_vertex(coordinates[(index + 1) % len(coordinates)])
            curve_id = curve_for_seg.get(index)
            edge_id, _forward = self.add_edge(
                start, end, layer, flags=flags, curve=curve_id
            )
            edges.append(edge_id)
            if curve_id is not None:
                run_edges[curve_id].append(edge_id)

        first_curve = run_curves[0][0] if run_curves else 0
        for curve_id, _run, frame in run_curves:
            curve_edges = run_edges[curve_id]
            curve_record = Record(
                T_CURVE,
                children=[
                    _entity_base(curve_id),
                    record(T_CURVE_COUNT, u32(len(curve_edges))),
                    record(T_CURVE_FLAG, u8(0)),
                    record(T_CURVE_FIRST, varint(curve_edges[0])),
                    record(T_CURVE_LAST, varint(curve_edges[-1])),
                ],
            )
            if frame is not None:
                self._pool(T_ARC_CURVES).children.append(
                    Record(
                        T_ARC_CURVE,
                        children=[curve_record, record(T_ARC_FRAME, f64s(frame))],
                    )
                )
            else:
                self._pool(T_CURVES).children.append(curve_record)
        return first_curve, edges

    def add_model_attributes(
        self, attribute_dicts: Sequence[Tuple[str, Dict[str, object]]]
    ) -> None:
        """Add dictionaries to the model's existing attribute block.

        A dictionary whose name already exists **replaces** that dictionary
        in place; dictionaries the template already had under other names,
        including GeoReference/GSU_ContributorsInfo/temp, are kept.  A
        second call with different names therefore adds to the same block
        instead of writing a second one.
        """
        if not attribute_dicts:
            return
        drawing = next(
            (child for child in self.container.children or [] if child.tag == T_DRAWING),
            None,
        )
        if drawing is None:
            raise ValueError("the model container has no drawing element")
        entity_records = parse_records(
            drawing.payload,
            {T_ENTITY_BASE, 0x05DD, T_ATTR_LIST, T_ATTR_SET, T_ATTR_DICT},
        )
        entity = next(
            (candidate for candidate in entity_records if candidate.tag == T_ENTITY_BASE),
            None,
        )
        if entity is None:
            raise ValueError("the model drawing element is malformed")
        if entity.children is None:
            entity.children = []
        existing = entity.child(0x05DD)
        if existing is None:
            entity.children.append(_attribute_container(attribute_dicts))
            drawing.payload = encode_records(entity_records)
            return
        attribute_set = None
        for candidate in existing.children or []:
            if candidate.tag == T_ATTR_LIST:
                attribute_set = candidate.child(T_ATTR_SET)
                if attribute_set is not None:
                    break
        if attribute_set is None:
            attribute_set = Record(T_ATTR_SET, children=[])
            existing.children = [
                Record(T_ATTR_LIST, children=[attribute_set])
            ] + (existing.children or [])
        children = attribute_set.children or []
        for name, entries in attribute_dicts:
            replacement = _attribute_dictionary(name, entries)
            for index, current in enumerate(children):
                if current.tag != T_ATTR_DICT:
                    continue
                name_node = current.child(T_ATTR_NAME)
                if name_node is not None and name_node.payload == name.encode("utf-8"):
                    children[index] = replacement
                    break
            else:
                children.append(replacement)
        attribute_set.children = children
        drawing.payload = encode_records(entity_records)

    # -- finalize ---------------------------------------------------------

    def finalize(self) -> Record:
        children: List[Record] = []
        existing = self.container.children or []
        drawing = next((c for c in existing if c.tag == T_DRAWING), None)
        if drawing is None:
            drawing = _drawing(self.container_id)
        children.append(drawing)
        for tag in _POOL_TAGS:
            pool = self._pools.get(tag)
            if pool is not None:
                children.append(pool)
        ahead: List[Record] = []
        trailing: List[Record] = []
        existing_active = None
        for child in existing:
            if child.tag in _POOL_TAGS or child.tag in (
                T_DRAWING,
                T_ORDER,
                T_FLAGS_CONTAINER,
                T_BOUNDS,
                T_ACTIVE_SECTION,
            ):
                if child.tag == T_ACTIVE_SECTION:
                    existing_active = child
                continue
            (trailing if child.tag == T_RELATIONSHIPS else ahead).append(child)
        children.extend(ahead)
        merged_order = self._existing_order_list + [
            i for i in self.order if i not in self._existing_order
        ]
        if merged_order:
            children.append(_new_id_list_tag(merged_order))
        if self.active_section is not None:
            children.append(record(T_ACTIVE_SECTION, varint(self.active_section)))
        elif existing_active is not None:
            children.append(existing_active)
        children.extend(trailing)
        flags_record = self.container.child(T_FLAGS_CONTAINER)
        if self.fresh:
            children.append(record(T_FLAGS_CONTAINER, u8(1)))
        elif flags_record is not None:
            children.append(flags_record)
        old_bounds = self.container.child(T_BOUNDS)
        if self.fresh or old_bounds is not None:
            bounds = self.bounds
            if old_bounds is not None and len(old_bounds.payload) == 48:
                values = list(struct.unpack("<6d", old_bounds.payload))
                if bounds is None:
                    bounds = values
                else:
                    bounds = [
                        min(values[0], bounds[0]),
                        min(values[1], bounds[1]),
                        min(values[2], bounds[2]),
                        max(values[3], bounds[3]),
                        max(values[4], bounds[4]),
                        max(values[5], bounds[5]),
                    ]
            if bounds is None:
                bounds = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
            children.append(record(T_BOUNDS, f64s(bounds)))
        self.container.children = children
        return self.container


def _box_corners(bounds: Sequence[float]) -> List[Tuple[float, float, float]]:
    min_x, min_y, min_z, max_x, max_y, max_z = bounds
    return [
        (x, y, z)
        for x in (min_x, max_x)
        for y in (min_y, max_y)
        for z in (min_z, max_z)
    ]


def _transform_point(point: Sequence[float], matrix13: Sequence[float]) -> Tuple[float, float, float]:
    x, y, z = point
    return (
        matrix13[0] * x + matrix13[1] * y + matrix13[2] * z + matrix13[9],
        matrix13[3] * x + matrix13[4] * y + matrix13[5] * z + matrix13[10],
        matrix13[6] * x + matrix13[7] * y + matrix13[8] * z + matrix13[11],
    )
