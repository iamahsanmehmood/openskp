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


def _entity_base(entity_id: Optional[int]) -> Record:
    children: List[Record] = []
    if entity_id is not None:
        children.append(record(T_ENTITY_ID, varint(entity_id)))
    return Record(T_ENTITY_BASE, children=children)


def _drawing(
    entity_id: Optional[int],
    material: Optional[int] = None,
    layer: Optional[int] = None,
    flags: int = 0x06,
) -> Record:
    children = [_entity_base(entity_id)]
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
            record(0x1B5D, u8(1 if always_faces_camera else 0)),
            record(0x1B5E, u8(1 if shadows_face_sun else 0)),
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




def material_xml(
    name: str,
    rgb: Tuple[int, int, int] = (255, 255, 255),
    *,
    layer_color: bool = False,
    opacity: float = 1.0,
) -> bytes:
    """A ``material.xml`` as SketchUp 2025 writes it.

    A solid-colour material has ``type="0"``. ``layer_color`` marks the embedded material a tag owns
    (``Layer_<name>``); SketchUp writes ``pbrPromoState`` 0 and "belongs to
    another entity" for those.  An opacity below 1 is stored as
    ``useTrans="1"`` with ``trans = 1 - opacity`` (SketchUp's convention).
    """
    red, green, blue = (max(0, min(255, int(c))) for c in rgb)
    promo = "0" if layer_color else "2"
    opacity = min(max(float(opacity), 0.0), 1.0)
    if opacity >= 1.0:
        trans_attr, use_trans = "0.5", "0"
    else:
        trans_attr, use_trans = _xml_number(1.0 - opacity), "1"
    type_attr = "0"
    has_texture = "0"
    block = _pbr_block(1.0, 1.0)
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
        self.materials: Dict[str, int] = {}
        self.material_ids: Dict[int, str] = {}
        self.material_folders: List[str] = []
        self._read_layers()
        self._read_materials()
        self.default_layer_id = self.layers.get("Layer0") or (
            next(iter(self.layers.values())) if self.layers else None
        )
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
            self.materials[name] = identifier
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
    ) -> None:
        self.template = template
        self.fresh = container is None
        self.container = container if container is not None else self._blank_container(
            container_id
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
    def _blank_container(container_id: Optional[int]) -> Record:
        children: List[Record] = [_drawing(container_id)]
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
        edge_flags: int = 0x06,
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
            ),
            record(T_PLANE, f64s([*normal, plane_d])),
            Record(T_LOOPS, children=loops),
        ]
        if back_material:
            children.append(record(T_BACK_MATERIAL, varint(back_material)))
        self._pool(T_FACES).children.append(Record(T_FACE, children=children))
        self.order.append(identifier)
        return identifier












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