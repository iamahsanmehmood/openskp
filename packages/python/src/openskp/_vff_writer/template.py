"""Template document model for the VFF writer.

The writer never splices at fixed byte offsets.  It parses the template's
``model.dat`` into a record tree, edits the sections it cares about (materials,
layers, definitions, the root entity container, the next-id record), and
re-encodes the tree, so any SketchUp-written VFF document with the standard
section set can serve as the template.

The record layouts are the ones documented from SketchUp-saved files and
confirmed against the minimal pairs in ``research/vff-writer/oracle/``; see
``research/vff-writer/notes.md``.
"""

from __future__ import annotations

import struct
import time
import uuid
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape as _xml_escape

from .archive import (
    ZipEntry,
    build_skp,
    entries_of,
    header_offset,
)
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
from ..create import Length, Point3d, SkpWriteError, Timestamp, Vector3d
from .uv import uv_attribute, uv_matrix_from_pins

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
T_CURRENT_LAYER = 0x3A9A
T_LAYER_FOLDERS = 0x3A9B

T_GROUPS = 0x138D
T_GROUP = 0x1D4C
T_MATERIAL_QUALITY = 0x32CD

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

T_CAMERA_MODEL = 0x01FA
T_RENDERING = 0x01FB
T_SHADOWS = 0x0204
T_AXES = 0x01FC
T_STYLES = 0x0206
T_PAGES_MODEL = 0x0207
T_CAMERA = 0x34BC
T_CAMERA_EYE = 0x34BD
T_CAMERA_TARGET = 0x34BE
T_CAMERA_UP = 0x34BF
T_CAMERA_PERSPECTIVE = 0x34C2
T_CAMERA_HEIGHT = 0x34C3
T_CAMERA_FOV = 0x34C4
T_PAGE_LIST = 0x6D60
T_PAGE_ITEMS = 0x6D61
T_PAGE_SELECTED = 0x6D62
T_PAGE = 0x7148
T_PAGE_BASE = 0x6F54
T_PAGE_NAME = 0x6F55
T_PAGE_DESCRIPTION = 0x6F56
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
T_DEFINITION_LIST = 0x1771
T_DEFINITION_SECTION = 0x1770

T_INSTANCE = 0x1964
T_INSTANCE_NAME = 0x1965
T_INSTANCE_TRANSFORM = 0x1966
T_INSTANCE_DEFINITION = 0x1967
T_INSTANCE_GUID = 0x1968
T_INSTANCE_HATCH = 0x1969

T_RELATIONSHIP_GROUP = 0x639F
T_RELATIONSHIP_LIST = 0x63A0

T_META = 0x0064
T_META_GUID = 0x0066
T_META_REVISION = 0x0074
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
        T_LAYERS,
        T_LAYER_SECTION,
        T_LAYER_LIST,
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

MODEL_REVISION = 17

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


def _entity_base(
    entity_id: Optional[int], attributes: Optional[Record] = None
) -> Record:
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
    if material is not None:
        children.append(record(T_FRONT_MATERIAL, varint(material)))
    if layer is not None:
        children.append(record(T_LAYER_REF, varint(layer)))
    if flags:
        children.append(record(T_FLAGS, u8(flags)))
    return Record(T_DRAWING, children=children)


def _material_record(
    entity_id: int, name: str, owner: int, quality: Optional[int] = None
) -> Record:
    children = [
        _entity_base(entity_id),
        record(T_MATERIAL_NAME, name.encode("utf-8")),
        record(T_MATERIAL_OWNER, u8(owner)),
        record(T_MATERIAL_EMPTY, b""),
    ]
    if quality is not None:
        children.append(record(T_MATERIAL_QUALITY, u32(quality)))
    return Record(T_MATERIAL, children=children)


def _attribute_value(value) -> Record:
    """One A438-wrapped attribute value, as SketchUp 2025 writes it.

    Tags confirmed from ``stage3/oracle/attributes.skp``: int32 A738, double
    A938 (Length AF38), bool AA38, time_t AC38, string AD38, array AE38
    (a sequence of A438 wrappers), Point3d B438, Vector3d B538 and null = an
    empty A438.
    """
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
        children.append(record(T_VALUE_LENGTH, f64s([float(value)])))
    elif isinstance(value, int):
        if not -(1 << 31) <= value < 1 << 31:
            raise SkpWriteError("attribute integers must fit a signed 32-bit value")
        children.append(record(T_VALUE_INT, struct.pack("<i", value)))
    elif isinstance(value, float):
        children.append(record(T_VALUE_DOUBLE, f64s([float(value)])))
    elif isinstance(value, str):
        children.append(record(T_VALUE_STRING, value.encode("utf-8")))
    elif isinstance(value, Point3d):
        children.append(record(T_VALUE_POINT3D, f64s([value.x, value.y, value.z])))
    elif isinstance(value, Vector3d):
        children.append(record(T_VALUE_VECTOR3D, f64s([value.x, value.y, value.z])))
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
    children: List[Record] = []
    if uv is not None:
        children.append(uv)
    for name, entries in attribute_dicts or ():
        children.append(_attribute_dictionary(name, entries))
    if not children:
        return None
    return Record(
        0x05DD,
        children=[
            Record(T_ATTR_LIST, children=[Record(T_ATTR_SET, children=children)])
        ],
    )


def _definition(container: Record, name: str, kind: int = 0) -> Record:
    behaviour = Record(
        T_DEFINITION_BEHAVIOUR,
        children=[
            record(0x1B59, u32(0)),
            record(0x1B5A, u32(0)),
            record(0x1B5B, u8(0)),
            record(0x1B5C, u8(0)),
            record(0x1B5D, u8(0)),
            record(0x1B5E, u8(0)),
        ],
    )
    return Record(
        T_DEFINITION,
        children=[
            container,
            record(T_DEFINITION_GUID, uuid.uuid4().bytes),
            record(T_DEFINITION_NAME, name.encode("utf-8")),
            record(T_DEFINITION_DESCRIPTION, b""),
            record(T_DEFINITION_SOURCE, b""),
            record(T_DEFINITION_FLAG, u8(0)),
            record(T_DEFINITION_KIND, u32(kind)),
            record(T_DEFINITION_TIMESTAMP, u32(int(time.time()))),
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

    A solid-colour material has ``type="0"`` and no texture block.  A
    texture material has ``type="1"``, the image's average colour in
    ``colorRed/Green/Blue``, ``hasTexture="1"`` and a ``<mat:texture>`` block
    naming the source path, the applied tile size in inches and the archive
    image (``./<basename>``).  ``layer_color`` marks the embedded material a
    tag owns (``Layer_<name>``); SketchUp writes ``pbrPromoState`` 0 and
    "belongs to another entity" for those.  An opacity below 1 is stored as
    ``useTrans="1"`` with ``trans = 1 - opacity`` (SketchUp's convention).
    """
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
        packed = _packed_average((red, green, blue))
        type_attr = "1"
        has_texture = "1"
        block = (
            f'    <mat:texture textureFilename="{_xml_escape(filename)}" '
            f'xScale="{_xml_number(width)}" yScale="{_xml_number(height)}" '
            f'avgColor="{packed}">\n'
            "      <mat:images>\n"
            f'        <mat:image id="1" path="./{_xml_escape(basename)}" '
            f'file_name="{_xml_escape(basename)}"/>\n'
            "      </mat:images>\n"
            "    </mat:texture>\n"
            + _pbr_block(width, height)
        )
    text = (
        '<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n'
        '<materialDocument xmlns="http://sketchup.google.com/schemas/sketchup/1.0/material" '
        'xmlns:mat="http://sketchup.google.com/schemas/sketchup/1.0/material">\n'
        f'  <mat:material name="{_xml_escape(name)}" type="{type_attr}" workflow="0" '
        f'colorRed="{red}" colorGreen="{green}" colorBlue="{blue}" '
        f'colorizeType="0" trans="{trans_attr}" useTrans="{use_trans}" '
        f'pbrPromoState="{promo}" hasTexture="{has_texture}">\n'
        + block
        + "  </mat:material>\n"
        "</materialDocument>\n"
    )
    return text.encode("utf-8")


def sanitize_folder(name: str) -> str:
    """Archive folder for a material/tag name, as SketchUp derives it."""
    out = []
    for char in name:
        if char in '*<>:|/\\':
            out.append("_")
        else:
            out.append(char)
    folder = "".join(out)
    return folder or "_"


def _material_folder_for(name: str, taken: Sequence[str]) -> str:
    base = sanitize_folder(name)
    if base not in taken:
        return base
    counter = 1
    while f"{base}{counter}" in taken:
        counter += 1
    return f"{base}{counter}"


# ── Template ─────────────────────────────────────────────────────────────


class Template:
    """A SketchUp-written VFF document parsed into an editable record tree."""

    def __init__(self, data: bytes) -> None:
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
        self.materials: Dict[str, int] = {}
        self.material_ids: Dict[int, str] = {}
        self.material_folders: List[str] = []
        self.texture_sizes: Dict[int, Tuple[float, float]] = {}
        self._read_layers()
        self._read_materials()
        self.default_layer_id = self.layers.get("Layer0") or (
            next(iter(self.layers.values())) if self.layers else None
        )
        self.font_id = self._read_font_id()
        self.style_id = self._read_style_id()
        self.scenes: List[dict] = []
        self.camera: Optional[dict] = None
        self.new_entries: List[ZipEntry] = []
        self._entry_names = set(self._by_name)

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

    def _read_style_id(self) -> Optional[int]:
        section = self.sections.get(T_STYLES)
        if section is None:
            return None
        try:
            nodes = parse_records(
                section.payload, {0x6978, 0x6979, 0x6B6C, T_ENTITY_BASE}
            )
        except ValueError:
            return None
        for wrapper in nodes:
            if wrapper.tag != 0x6978 or wrapper.children is None:
                continue
            for pool in wrapper.children:
                if pool.tag != 0x6979 or pool.children is None:
                    continue
                for style in pool.children:
                    if style.tag == 0x6B6C:
                        return _find_entity_id(style)
        return None

    # -- pages and camera -------------------------------------------------

    def add_scene(
        self,
        name: str,
        eye: Sequence[float],
        target: Sequence[float],
        up: Optional[Sequence[float]],
        *,
        perspective: bool,
        fov: float,
        ortho_height: float,
        hidden_layers: Sequence[int],
    ) -> None:
        if not name:
            raise SkpWriteError("a scene needs a name")
        if any(scene["name"] == name for scene in self.scenes):
            raise SkpWriteError(f"a scene named {name!r} already exists")
        if not 0 < fov < 180:
            raise SkpWriteError("scene field of view must be between 0 and 180 degrees")
        if ortho_height <= 0:
            raise SkpWriteError("scene ortho_height must be positive")
        for identifier in hidden_layers:
            if identifier not in self.layer_ids:
                raise SkpWriteError(f"unknown layer handle: {identifier!r}")
        eye = tuple(float(c) for c in eye)
        target = tuple(float(c) for c in target)
        direction = [target[i] - eye[i] for i in range(3)]
        if not any(abs(c) > 0 for c in direction):
            raise SkpWriteError("scene camera eye and target coincide")
        if up is None:
            corrected = self._default_up(eye, target)
        else:
            corrected = self._orthogonal_up(tuple(float(c) for c in up), direction)
        self.scenes.append({
            "name": name,
            "eye": eye,
            "target": target,
            "up": corrected,
            "perspective": bool(perspective),
            "fov": float(fov),
            "ortho_height": float(ortho_height),
            "hidden_layers": list(dict.fromkeys(hidden_layers)),
        })

    @staticmethod
    def _orthogonal_up(up: Sequence[float], direction: Sequence[float]):
        length = sum(c * c for c in direction) ** 0.5
        if length == 0:
            raise SkpWriteError("scene camera eye and target coincide")
        d = [c / length for c in direction]
        dot = sum(up[i] * d[i] for i in range(3))
        projected = [up[i] - dot * d[i] for i in range(3)]
        plen = sum(c * c for c in projected) ** 0.5
        if plen < 1e-9:
            raise SkpWriteError("scene up vector is zero or parallel to the view direction")
        return tuple(c / plen for c in projected)

    @classmethod
    def _default_up(cls, eye: Sequence[float], target: Sequence[float]):
        direction = [target[i] - eye[i] for i in range(3)]
        length = sum(c * c for c in direction) ** 0.5
        d = [c / length for c in direction]
        up = [0.0, 0.0, 1.0]
        if abs(d[2]) > 0.999:
            up = [0.0, 1.0, 0.0]
        return cls._orthogonal_up(up, direction)

    def set_camera(
        self,
        eye: Sequence[float],
        target: Sequence[float],
        up: Optional[Sequence[float]] = None,
        *,
        perspective: bool = True,
        fov: float = 35.0,
        ortho_height: float = 200.0,
    ) -> None:
        eye = tuple(float(c) for c in eye)
        target = tuple(float(c) for c in target)
        direction = [target[i] - eye[i] for i in range(3)]
        corrected = self._default_up(eye, target) if up is None else self._orthogonal_up(
            tuple(float(c) for c in up), direction
        )
        self.camera = {
            "eye": eye, "target": target, "up": corrected,
            "perspective": bool(perspective), "fov": float(fov),
            "ortho_height": float(ortho_height),
        }

    def _camera_template(self) -> Record:
        section = self.sections.get(T_CAMERA_MODEL)
        if section is None:
            raise SkpWriteError("the template has no camera section")
        camera = section.child(T_CAMERA)
        if camera is None and section.payload:
            try:
                nodes = parse_records(section.payload, {T_CAMERA})
            except ValueError:
                nodes = []
            camera = next((node for node in nodes if node.tag == T_CAMERA), None)
        if camera is None or camera.children is None:
            raise SkpWriteError("the template camera record is malformed")
        return camera

    def _camera_record(self, camera: dict) -> Record:
        template = self._camera_template()
        replacements = {
            T_CAMERA_EYE: f64s(camera["eye"]),
            T_CAMERA_TARGET: f64s(camera["target"]),
            T_CAMERA_UP: f64s(camera["up"]),
            T_CAMERA_PERSPECTIVE: u8(1 if camera["perspective"] else 0),
            T_CAMERA_FOV: f64s([camera["fov"]]),
            T_CAMERA_HEIGHT: f64s([
                camera["ortho_height"] if not camera["perspective"] else 1.0
            ]),
        }
        children: List[Record] = []
        for child in template.children or []:
            payload = replacements.get(child.tag)
            children.append(
                Record(child.tag, payload if payload is not None else child.payload)
            )
        return Record(T_CAMERA, children=children)

    def _build_pages(self) -> Optional[Record]:
        if not self.scenes:
            return None
        section = self.sections.get(T_PAGES_MODEL)
        if section is None:
            return None
        if section.children is None:
            try:
                section.children = parse_records(
                    section.payload, {T_PAGE_LIST, T_PAGE_ITEMS, T_PAGE, T_PAGE_BASE}
                )
            except ValueError:
                section.children = []
            section.payload = b""
        list_record = section.child(T_PAGE_LIST)
        if list_record is None:
            list_record = Record(T_PAGE_LIST, children=[Record(T_PAGE_ITEMS, children=[])])
            section.children.append(list_record)
        items = list_record.child(T_PAGE_ITEMS)
        if items is None:
            items = Record(T_PAGE_ITEMS, children=[])
            list_record.children.append(items)
        if items.children is None:
            items.children = []
        page_ids: List[int] = []
        for existing in items.children:
            base = existing.child(T_PAGE_BASE)
            identifier = _find_entity_id(base) if base is not None else None
            if identifier is not None:
                page_ids.append(identifier)
        style_payload = varint(self.style_id) if self.style_id is not None else b""
        for scene in self.scenes:
            page_id = self.alloc_id()
            hidden_payload = b"".join(
                bytes([len(varint(layer))]) + varint(layer)
                for layer in scene["hidden_layers"]
            )
            page = Record(
                T_PAGE,
                children=[
                    Record(
                        T_PAGE_BASE,
                        children=[
                            _entity_base(page_id),
                            record(T_PAGE_NAME, scene["name"].encode("utf-8")),
                        ],
                    ),
                    record(T_PAGE_PROPERTIES, u32(1 | 2 | 4 | 8 | 32)),
                    Record(
                        T_PAGE_CAMERA,
                        children=[self._camera_record(scene)],
                    ),
                    record(T_PAGE_STYLE, style_payload),
                    record(T_PAGE_RENDERING, self.sections[T_RENDERING].payload),
                    record(T_PAGE_SHADOWS, self.sections[T_SHADOWS].payload),
                    record(T_PAGE_AXES, self.sections[T_AXES].payload),
                    record(T_PAGE_HIDDEN_LAYERS, hidden_payload),
                    record(T_PAGE_ANIMATION, u8(1)),
                    record(T_PAGE_DISPLAY_NAME, scene["name"].encode("utf-8")),
                    record(T_PAGE_TRANSITION, f64s([-1.0])),
                    record(T_PAGE_DELAY, f64s([-1.0])),
                    record(T_PAGE_THUMBNAIL_FLAG, u8(1)),
                ],
            )
            items.children.append(page)
            page_ids.append(page_id)
        if page_ids:
            selected = list_record.child(T_PAGE_SELECTED)
            if selected is None:
                selected = record(T_PAGE_SELECTED, varint(page_ids[0]))
                list_record.children.append(selected)
            else:
                selected.payload = varint(page_ids[0])
        return section

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

    def _read_materials(self) -> None:
        for material in self._material_list().children or []:
            if material.tag != T_MATERIAL:
                continue
            identifier = _find_entity_id(material)
            name_node = material.child(T_MATERIAL_NAME)
            if identifier is None or name_node is None:
                continue
            name = name_node.payload.decode("utf-8", "replace")
            self.material_ids[identifier] = name
            self.material_folders.append(name)

    # -- adding materials / layers ---------------------------------------

    def add_material(
        self, name: str, rgb: Tuple[int, int, int], opacity: float = 1.0
    ) -> int:
        if name in self.materials:
            return self.materials[name]
        folder = _material_folder_for(name, self.material_folders)
        identifier = self.alloc_id()
        self._material_list().children.append(_material_record(identifier, folder, 0))
        self.material_ids[identifier] = name
        self.material_folders.append(folder)
        self.materials[name] = identifier
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
            raise ValueError("a texture's applied size must be positive")
        folder = _material_folder_for(name, self.material_folders)
        identifier = self.alloc_id()
        self._material_list().children.append(
            _material_record(identifier, folder, 0, quality=90)
        )
        self.material_ids[identifier] = name
        self.material_folders.append(folder)
        self.materials[name] = identifier
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
        if name in self.layers:
            return self.layers[name]
        colour = color if color is not None else _DEFAULT_LAYER_COLOR
        identifier = self.alloc_id()
        material_id = self.alloc_id()
        folder = _material_folder_for(f"Layer_{name}", self.material_folders)
        self.material_folders.append(folder)
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
        self._add_entry(
            f"materials/{folder}/material.xml",
            material_xml(f"Layer_{name}", colour, layer_color=True),
        )
        return identifier

    def add_definition(
        self, container: Record, name: str, container_id: int, kind: int = 0
    ) -> int:
        definition = _definition(container, name, kind=kind)
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
        guid.payload = uuid.uuid4().bytes

        revision = root.child(T_META_REVISION)
        if revision is not None:
            revision.payload = struct.pack("<H", MODEL_REVISION)

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
                        "<I", int(time.time()) & 0xFFFFFFFF
                    )
        return encode_records(records)

    def _patch_model_camera(self) -> None:
        camera = self.camera
        if camera is None:
            camera = {
                "eye": (100.0, -100.0, 100.0),
                "target": (0.0, 0.0, 0.0),
                "up": self._default_up((100.0, -100.0, 100.0), (0.0, 0.0, 0.0)),
                "perspective": False,
                "fov": 35.0,
                "ortho_height": 200.0,
            }
        section = self.sections.get(T_CAMERA_MODEL)
        if section is None:
            return
        section.children = [self._camera_record(camera)]
        section.payload = b""

    @property
    def root_container(self) -> Record:
        """The model's own ``0x1388`` entity container."""
        section = self.sections[T_ENTITIES]
        container = section.child(T_DEFINITION_CONTAINER)
        if container is None or container.children is None:
            raise ValueError("template model has no root entity container")
        return container

    # -- output -----------------------------------------------------------

    def build(self) -> bytes:
        self._build_pages()
        self._patch_model_camera()
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
        # Insertion order: a texture's image entry is written before its
        # material.xml, as SketchUp writes a material's folder.
        entries.extend(self.new_entries)
        entries.extend(others)
        entries.append(ZipEntry("model.dat", model_dat, 0x0006))
        header = bytearray(self.header)
        start = header.find(b"VFF\x08\x00\x01\x00")
        struct.pack_into("<H", header, start + 7, MODEL_REVISION)
        return build_skp(bytes(header), entries)


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
        self.container = container if container is not None else self._blank_container(
            container_id, _attribute_container(attributes)
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
        self._existing_order = self._read_order()

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

    def add_face(
        self,
        points: Sequence[Sequence[float]],
        *,
        material: Optional[int] = None,
        layer: Optional[int] = None,
        back_material: Optional[int] = None,
        hidden: bool = False,
        holes: Sequence[Sequence[Sequence[float]]] = (),
        front_uv: Optional[
            Sequence[Tuple[Sequence[float], Tuple[float, float]]]
        ] = None,
        back_uv: Optional[
            Sequence[Tuple[Sequence[float], Tuple[float, float]]]
        ] = None,
        attribute_dicts: Optional[
            Sequence[Tuple[str, Dict[str, object]]]
        ] = None,
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
        loops: List[Record] = []
        rings = [outer]
        for hole in holes:
            source = getattr(hole, "points", hole)
            ring = [tuple(float(c) for c in p) for p in source]
            if len(ring) < 3:
                raise ValueError("a hole needs at least three points")
            hole_normal = newell_normal(ring)
            if sum(a * b for a, b in zip(hole_normal, normal)) > 0:
                ring = list(reversed(ring))
            rings.append(ring)
        for ring in rings:
            uses: List[Record] = []
            for index in range(len(ring)):
                start_point = ring[index]
                end_point = ring[(index + 1) % len(ring)]
                start = self.add_vertex(start_point)
                end = self.add_vertex(end_point)
                if start == end:
                    raise ValueError("a face ring has a repeated adjacent point")
                edge_id, forward = self.add_edge(start, end, layer)
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
        uv_coords = None
        if front_uv is not None or back_uv is not None:
            front_matrix = None
            back_matrix = None
            if front_uv is not None:
                front_matrix = self._uv_placement(front_uv, material, normal)
            if back_uv is not None:
                back_matrix = self._uv_placement(
                    back_uv, back_material if back_material is not None else material,
                    normal,
                )
            uv_coords = uv_attribute(front_matrix, back_matrix)
        attributes = _attribute_container(attribute_dicts, uv=uv_coords)
        identifier = self.template.alloc_id()
        flags = 0x06 | (0x01 if hidden else 0)
        children: List[Record] = [
            _drawing(
                identifier,
                material=material,
                layer=layer,
                flags=flags,
                attributes=attributes,
            ),
            record(T_PLANE, f64s([*normal, plane_d])),
            Record(T_LOOPS, children=loops),
        ]
        if back_material is not None:
            children.append(record(T_BACK_MATERIAL, varint(back_material)))
        self._pool(T_FACES).children.append(Record(T_FACE, children=children))
        self.order.append(identifier)
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
        vector: Sequence[float],
        *,
        screen: bool = False,
        layer: Optional[int] = None,
    ) -> int:
        identifier = self.template.alloc_id()
        anchor = (float(point[0]), float(point[1]), float(point[2]))
        offset = (float(vector[0]), float(vector[1]), float(vector[2]))
        item = Record(
            T_TEXT,
            children=[
                _drawing(identifier, layer=layer),
                record(T_TEXT_STRING, text.encode("utf-8")),
                record(T_TEXT_EMPTY, b""),
                record(T_TEXT_ZERO_A, f64s([0.0])),
                record(T_TEXT_ZERO_B, f64s([0.0])),
                Record(
                    T_TEXT_ANCHOR, children=[self._connection(point=anchor)]
                ),
                record(T_TEXT_VECTOR, f64s(offset)),
                record(T_TEXT_ONE, u8(1)),
                record(T_TEXT_UP, f64s([0.0, 0.0, 1.0])),
                record(T_TEXT_ZERO, u8(0)),
                record(T_TEXT_FONT, varint(self._font_id())),
                record(T_TEXT_ARROW, u32(3)),
                record(T_TEXT_LEADER_FLAG_A, u32(1)),
                record(T_TEXT_LEADER_FLAG, u8(0 if screen else 1)),
                record(T_TEXT_LEADER_TYPE, u32(2)),
                record(T_TEXT_SCREEN, u32(1 if screen else 0)),
            ],
        )
        self._pool(T_TEXTS).children.append(item)
        self.order.append(identifier)
        return identifier

    @staticmethod
    def _perpendicular(direction: Sequence[float]) -> Tuple[float, float, float]:
        z = (0.0, 0.0, 1.0)
        cx = direction[1] * z[2] - direction[2] * z[1]
        cy = direction[2] * z[0] - direction[0] * z[2]
        cz = direction[0] * z[1] - direction[1] * z[0]
        length = (cx * cx + cy * cy + cz * cz) ** 0.5
        if length < 1e-9:
            return (1.0, 0.0, 0.0)
        return (cx / length, cy / length, cz / length)

    def add_dimension(
        self,
        start: Sequence[float],
        end: Sequence[float],
        offset: float = 10.0,
        offset_vector: Optional[Sequence[float]] = None,
        *,
        anchor: bool = False,
    ) -> int:
        p1 = tuple(float(c) for c in start)
        p2 = tuple(float(c) for c in end)
        if p1 == p2:
            raise SkpWriteError("dimension endpoints coincide")
        dx, dy, dz = (p2[i] - p1[i] for i in range(3))
        length = (dx * dx + dy * dy + dz * dz) ** 0.5
        direction = (dx / length, dy / length, dz / length)
        if offset_vector is None:
            perp = self._perpendicular(direction)
            ov = tuple(offset * c for c in perp)
        else:
            ov = tuple(float(c) for c in offset_vector)
            if all(abs(c) < 1e-12 for c in ov):
                raise SkpWriteError("dimension offset vector must be non-zero")
        nx = ov[1] * direction[2] - ov[2] * direction[1]
        ny = ov[2] * direction[0] - ov[0] * direction[2]
        nz = ov[0] * direction[1] - ov[1] * direction[0]
        nlen = (nx * nx + ny * ny + nz * nz) ** 0.5
        if nlen < 1e-9:
            raise SkpWriteError("dimension offset must not be parallel to the segment")
        normal = (nx / nlen, ny / nlen, nz / nlen)
        offset_scalar = -((ov[0] ** 2 + ov[1] ** 2 + ov[2] ** 2) ** 0.5)
        if anchor:
            v1 = self.vertices.get(p1)
            v2 = self.vertices.get(p2)
            if v1 is None or v2 is None:
                raise SkpWriteError(
                    "anchored dimension endpoints must be existing vertices"
                )
            connection_a = self._connection(vertex_id=v1)
            connection_b = self._connection(vertex_id=v2)
        else:
            connection_a = self._connection(point=p1)
            connection_b = self._connection(point=p2)
        identifier = self.template.alloc_id()
        item = Record(
            T_DIMENSION,
            children=[
                Record(
                    T_DIMENSION_HEAD,
                    children=[
                        _drawing(identifier),
                        record(T_DIMENSION_HEAD_EMPTY, b""),
                        record(T_DIMENSION_FONT, varint(self._font_id())),
                        record(T_DIMENSION_HEAD_FLAG, u8(0)),
                        record(T_DIMENSION_HEAD_ARROW, u32(3)),
                    ],
                ),
                Record(T_DIM_START, children=[connection_a]),
                Record(T_DIM_END, children=[connection_b]),
                record(T_DIM_NORMAL, f64s(normal)),
                record(T_DIM_XAXIS, f64s(direction)),
                record(T_DIM_ONE, u32(1)),
                record(T_DIM_ZERO, f64s([0.0])),
                record(T_DIM_OFFSET, f64s([offset_scalar])),
                record(T_DIM_ARROW, u32(3)),
            ],
        )
        self._pool(T_DIMENSIONS).children.append(item)
        self.order.append(identifier)
        return identifier

    def add_guide_line(
        self,
        point: Sequence[float],
        point2: Optional[Sequence[float]] = None,
        direction: Optional[Sequence[float]] = None,
    ) -> int:
        if (point2 is None) == (direction is None):
            raise SkpWriteError(
                "add_guide_line: pass exactly one of point2 or direction"
            )
        p = tuple(float(c) for c in point)
        if point2 is not None:
            p2 = tuple(float(c) for c in point2)
            dx, dy, dz = (p2[i] - p[i] for i in range(3))
            length = (dx * dx + dy * dy + dz * dz) ** 0.5
            if length == 0:
                raise SkpWriteError("guide-line points coincide")
            dirv = (dx / length, dy / length, dz / length)
            start_param, end_param = 0.0, length
        else:
            d = tuple(float(c) for c in direction)
            dlen = (d[0] ** 2 + d[1] ** 2 + d[2] ** 2) ** 0.5
            if dlen == 0:
                raise SkpWriteError("guide-line direction must be non-zero")
            dirv = (d[0] / dlen, d[1] / dlen, d[2] / dlen)
            start_param, end_param = -1e30, 1e30
        identifier = self.template.alloc_id()
        item = Record(
            T_GUIDE_LINE,
            children=[
                Record(T_ANNOTATION_BASE, children=[_drawing(identifier)]),
                record(
                    T_GUIDE_BODY,
                    f64s([*p, *dirv, start_param, end_param]),
                ),
                record(T_GUIDE_STIPPLE, struct.pack("<H", 0xFFFF)),
            ],
        )
        self._pool(T_GUIDE_LINES).children.append(item)
        self.order.append(identifier)
        return identifier

    def add_guide_point(self, position: Sequence[float]) -> int:
        p = tuple(float(c) for c in position)
        identifier = self.template.alloc_id()
        item = Record(
            T_GUIDE_POINT,
            children=[
                Record(T_ANNOTATION_BASE, children=[_drawing(identifier)]),
                record(T_GUIDE_POSITION, f64s(p)),
                record(T_GUIDE_ANCHOR, f64s([0.0, 0.0, 0.0])),
                record(T_GUIDE_TAIL, u8(0)),
            ],
        )
        self._pool(T_GUIDE_POINTS).children.append(item)
        self.order.append(identifier)
        self._extend_bounds(p)
        return identifier

    def add_section_plane(
        self,
        point: Sequence[float],
        normal: Sequence[float],
        *,
        name: str = "",
        symbol: str = "",
        active: bool = False,
        hidden: bool = False,
    ) -> int:
        p = tuple(float(c) for c in point)
        n = tuple(float(c) for c in normal)
        nlen = (n[0] ** 2 + n[1] ** 2 + n[2] ** 2) ** 0.5
        if nlen == 0:
            raise SkpWriteError("section-plane normal must be non-zero")
        a, b, c = n[0] / nlen, n[1] / nlen, n[2] / nlen
        d = -(a * p[0] + b * p[1] + c * p[2])
        identifier = self.template.alloc_id()
        flags = 0x06 | (0x01 if hidden else 0)
        item = Record(
            T_SECTION,
            children=[
                _drawing(identifier, flags=flags),
                record(T_SECTION_PLANE, f64s([a, b, c, d])),
                record(T_SECTION_NAME, name.encode("utf-8")),
                record(T_SECTION_SYMBOL, symbol.encode("utf-8")),
            ],
        )
        self._pool(T_SECTION_PLANES).children.append(item)
        self.order.append(identifier)
        if active:
            self.active_section = identifier
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
        """Create one curve over *points* (edges chained in point order)."""
        if len(points) < 2:
            raise ValueError("a curve needs at least two points")
        curve_id = self.template.alloc_id()
        edges: List[int] = []
        count = len(points) if closed else len(points) - 1
        for index in range(count):
            start = self.add_vertex(points[index % len(points)])
            end = self.add_vertex(points[(index + 1) % len(points)])
            if start == end:
                raise ValueError("a curve has a repeated adjacent point")
            edge_id, _forward = self.add_edge(
                start, end, layer, flags=flags, curve=curve_id
            )
            edges.append(edge_id)
        curve_record = Record(
            T_CURVE,
            children=[
                _entity_base(curve_id),
                record(T_CURVE_COUNT, u32(len(edges))),
                record(T_CURVE_FLAG, u8(0)),
                record(T_CURVE_FIRST, varint(edges[0])),
                record(T_CURVE_LAST, varint(edges[-1])),
            ],
        )
        if arc_frame is not None:
            self._pool(T_ARC_CURVES).children.append(
                Record(
                    T_ARC_CURVE,
                    children=[curve_record, record(T_ARC_FRAME, f64s(arc_frame))],
                )
            )
        else:
            self._pool(T_CURVES).children.append(curve_record)
        return curve_id, edges

    def add_model_attributes(
        self, attribute_dicts: Sequence[Tuple[str, Dict[str, object]]]
    ) -> None:
        """Append dictionaries to the model's own drawing element."""
        if not attribute_dicts:
            return
        drawing = next(
            (child for child in self.container.children or [] if child.tag == T_DRAWING),
            None,
        )
        if drawing is None:
            raise ValueError("the model container has no drawing element")
        entity_records = parse_records(
            drawing.payload, {T_ENTITY_BASE, 0x05DD, T_ATTR_LIST, T_ATTR_SET}
        )
        entity = next(
            (candidate for candidate in entity_records if candidate.tag == T_ENTITY_BASE),
            None,
        )
        if entity is None or entity.children is None:
            raise ValueError("the model drawing element is malformed")
        attribute = entity.child(0x05DD)
        new_dicts = [
            _attribute_dictionary(name, entries) for name, entries in attribute_dicts
        ]
        if attribute is None:
            attribute = _attribute_container(attribute_dicts)
            entity.children.append(attribute)
        else:
            attribute_set = attribute.child(T_ATTR_LIST)
            attribute_set = attribute_set.child(T_ATTR_SET) if attribute_set else None
            if attribute_set is None:
                raise ValueError("the model attribute set is malformed")
            attribute_set.children.extend(new_dicts)
        drawing.payload = encode_records(entity_records)

    def _uv_placement(self, pins, material, normal):
        size = self.template.texture_sizes.get(material) if material is not None else None
        if size is None:
            raise ValueError("front_uv/back_uv need a textured material handle")
        return uv_matrix_from_pins(pins, normal, size[0], size[1])

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
        attribute_dicts: Optional[
            Sequence[Tuple[str, Dict[str, object]]]
        ] = None,
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
                    attributes=_attribute_container(attribute_dicts),
                ),
                record(T_INSTANCE_NAME, name.encode("utf-8")),
                record(T_INSTANCE_TRANSFORM, f64s(matrix13)),
                record(T_INSTANCE_DEFINITION, varint(definition_container_id)),
                record(T_INSTANCE_GUID, uuid.uuid4().bytes),
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
        attribute_dicts: Optional[
            Sequence[Tuple[str, Dict[str, object]]]
        ] = None,
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
                    attributes=_attribute_container(attribute_dicts),
                ),
                record(T_INSTANCE_NAME, name.encode("utf-8")),
                record(T_INSTANCE_TRANSFORM, f64s(matrix13)),
                record(T_INSTANCE_DEFINITION, varint(definition_container_id)),
                record(T_INSTANCE_GUID, uuid.uuid4().bytes),
            ],
        )
        self._pool(T_GROUPS).children.append(Record(T_GROUP, children=[instance]))
        self.order.append(identifier)
        if definition_bounds is not None:
            for corner in _box_corners(definition_bounds):
                self._extend_bounds(_transform_point(corner, matrix13))
        return identifier

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
        merged_order = self._existing_order + [
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