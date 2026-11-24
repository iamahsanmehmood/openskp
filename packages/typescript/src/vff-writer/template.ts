/**
 * Template document model for the VFF writer, ported from Python's
 * `openskp/_vff_writer/template.py`.  The writer parses `model.dat` into a
 * record tree, edits the sections it owns and re-encodes the tree; no
 * template byte offsets are used.
 */
import {
  Record,
  ZipEntry,
  buildSkp,
  concat,
  decodeIdList,
  encodeRecords,
  f64s,
  i32,
  idList,
  makeZipEntry,
  newellNormal,
  parseRecords,
  readF64s,
  readVarint,
  record,
  u8,
  u16,
  u32,
  unitNormal,
  varint,
  headerOffset,
  entriesOf,
  zipOffset,
} from './core';
import { uvAttribute, uvMatrixFromPins } from './uv';
import {
  Point3,
  VffAttributeDict,
  VffAttributeDicts,
  VffAttributeValue,
  VffLength,
  VffPoint3d,
  VffTimestamp,
  VffVector3d,
  VffWriterOptions,
} from './types';

// ── Tags ───────────────────────────────────────────────────────────────
export const T_DOCUMENT = 0x01f4;
export const T_NEXT_ID = 0x01f5;
export const T_ENTITIES = 0x01f6;
export const T_MATERIALS = 0x01f7;
export const T_LAYERS = 0x01f8;
export const T_DEFINITIONS = 0x01f9;

export const T_ENTITY_BASE = 0x05dc;
export const T_ENTITY_ID = 0x05de;
export const T_DRAWING = 0x07d0;
export const T_FRONT_MATERIAL = 0x07d1;
export const T_LAYER_REF = 0x07d2;
export const T_FLAGS = 0x07d3;

export const T_VERTICES = 0x1389;
export const T_EDGES = 0x138a;
export const T_FACES = 0x138b;
export const T_COMPONENTS = 0x138c;
export const T_CURVES = 0x1396;
export const T_ARC_CURVES = 0x1397;
export const T_TEXTS = 0x1398;
export const T_DIMENSIONS = 0x1399;
export const T_SECTION_PLANES = 0x1393;
export const T_GUIDE_LINES = 0x1391;
export const T_GUIDE_POINTS = 0x1392;
export const T_ACTIVE_SECTION = 0x1394;
export const T_ORDER = 0x138e;
export const T_RELATIONSHIPS = 0x139b;
export const T_FLAGS_CONTAINER = 0x139f;
export const T_BOUNDS = 0x13a0;
export const T_EMPTY = 0x139e;

export const T_VERTEX = 0x09c4;
export const T_POINT = 0x09c5;
export const T_EDGE = 0x0bb8;
export const T_EDGE_START = 0x0bb9;
export const T_EDGE_END = 0x0bba;
export const T_EDGE_CURVE = 0x0bbb;
export const T_FACE = 0x0dac;
export const T_PLANE = 0x0dad;
export const T_LOOPS = 0x0dae;
export const T_BACK_MATERIAL = 0x0daf;
export const T_LOOP = 0x1194;
export const T_LOOP_EDGES = 0x1195;
export const T_EDGE_USE = 0x0fa0;
export const T_EDGE_USE_ID = 0x0fa1;
export const T_EDGE_USE_FORWARD = 0x0fa2;

export const T_MATERIAL = 0x32c8;
export const T_MATERIAL_OWNER = 0x32ca;
export const T_MATERIAL_EMPTY = 0x32cb;
export const T_MATERIAL_NAME = 0x32cc;
export const T_MATERIAL_LIST = 0x30d5;
export const T_MATERIAL_SECTION = 0x30d4;
export const T_MATERIAL_QUALITY = 0x32cd;

export const T_LAYER = 0x3c8c;
export const T_LAYER_NAME = 0x3c8d;
export const T_LAYER_HIDDEN = 0x3c8e;
export const T_LAYER_MATERIAL = 0x3c8f;
export const T_LAYER_TAIL = 0x3c90;
export const T_LAYER_LIST = 0x3a99;
export const T_LAYER_SECTION = 0x3a98;

export const T_GROUPS = 0x138d;
export const T_GROUP = 0x1d4c;

export const T_CURVE = 0x4a38;
export const T_CURVE_COUNT = 0x4a39;
export const T_CURVE_FLAG = 0x4a3a;
export const T_CURVE_FIRST = 0x4a3b;
export const T_CURVE_LAST = 0x4a3c;
export const T_ARC_CURVE = 0x4c2c;
export const T_ARC_FRAME = 0x4c2d;

export const T_TEXT = 0x55f0;
export const T_TEXT_STRING = 0x55f1;
export const T_TEXT_EMPTY = 0x55ff;
export const T_TEXT_ZERO_A = 0x55f2;
export const T_TEXT_ZERO_B = 0x55f3;
export const T_TEXT_ANCHOR = 0x55f4;
export const T_TEXT_VECTOR = 0x55f5;
export const T_TEXT_ONE = 0x55f6;
export const T_TEXT_UP = 0x55f7;
export const T_TEXT_ZERO = 0x55f8;
export const T_TEXT_FONT = 0x55f9;
export const T_TEXT_ARROW = 0x55fa;
export const T_TEXT_LEADER_FLAG_A = 0x55fb;
export const T_TEXT_LEADER_FLAG = 0x55fc;
export const T_TEXT_LEADER_TYPE = 0x55fd;
export const T_TEXT_SCREEN = 0x55fe;

export const T_DIMENSION = 0x5bcc;
export const T_DIMENSION_HEAD = 0x59d8;
export const T_DIMENSION_HEAD_EMPTY = 0x59d9;
export const T_DIMENSION_FONT = 0x59da;
export const T_DIMENSION_HEAD_FLAG = 0x59db;
export const T_DIMENSION_HEAD_ARROW = 0x59dc;
export const T_DIM_START = 0x5bcd;
export const T_DIM_END = 0x5bce;
export const T_DIM_NORMAL = 0x5bcf;
export const T_DIM_XAXIS = 0x5bd0;
export const T_DIM_ONE = 0x5bd1;
export const T_DIM_OFFSET = 0x5bd2;
export const T_DIM_ZERO = 0x5bd3;
export const T_DIM_ARROW = 0x5bd4;
export const T_CONNECTION = 0x5208;
export const T_CONNECTION_TYPE = 0x5209;
export const T_CONNECTION_POINT = 0x520a;
export const T_CONNECTION_REF_A = 0x520b;
export const T_CONNECTION_REF_B = 0x520c;
export const T_CONNECTION_REF = 0x53fc;
export const T_CONNECTION_VERTEX = 0x53fd;
export const T_CONNECTION_PATH = 0x53fe;

export const T_SECTION = 0x445c;
export const T_SECTION_PLANE = 0x445d;
export const T_SECTION_NAME = 0x445e;
export const T_SECTION_SYMBOL = 0x445f;

export const T_ANNOTATION_BASE = 0x4268;
export const T_GUIDE_LINE = 0x4269;
export const T_GUIDE_BODY = 0x426a;
export const T_GUIDE_STIPPLE = 0x426b;
export const T_GUIDE_POINT = 0x426c;
export const T_GUIDE_POSITION = 0x426d;
export const T_GUIDE_ANCHOR = 0x426e;
export const T_GUIDE_TAIL = 0x426f;

export const T_CAMERA_MODEL = 0x01fa;
export const T_RENDERING = 0x01fb;
export const T_SHADOWS = 0x0204;
export const T_AXES = 0x01fc;
export const T_STYLES = 0x0206;
export const T_PAGES_MODEL = 0x0207;
export const T_CAMERA = 0x34bc;
export const T_CAMERA_EYE = 0x34bd;
export const T_CAMERA_TARGET = 0x34be;
export const T_CAMERA_UP = 0x34bf;
export const T_CAMERA_PERSPECTIVE = 0x34c2;
export const T_CAMERA_HEIGHT = 0x34c3;
export const T_CAMERA_FOV = 0x34c4;
export const T_PAGE_LIST = 0x6d60;
export const T_PAGE_ITEMS = 0x6d61;
export const T_PAGE_SELECTED = 0x6d62;
export const T_PAGE = 0x7148;
export const T_PAGE_BASE = 0x6f54;
export const T_PAGE_NAME = 0x6f55;
export const T_PAGE_DESCRIPTION = 0x6f56;
export const T_PAGE_PROPERTIES = 0x7149;
export const T_PAGE_CAMERA = 0x714a;
export const T_PAGE_STYLE = 0x714c;
export const T_PAGE_RENDERING = 0x714d;
export const T_PAGE_SHADOWS = 0x714e;
export const T_PAGE_AXES = 0x714f;
export const T_PAGE_HIDDEN_LAYERS = 0x7150;
export const T_PAGE_ANIMATION = 0x7152;
export const T_PAGE_DISPLAY_NAME = 0x7153;
export const T_PAGE_TRANSITION = 0x7154;
export const T_PAGE_DELAY = 0x7155;
export const T_PAGE_THUMBNAIL_FLAG = 0x7157;

export const T_ATTR_LIST = 0x36b1;
export const T_ATTR_SET = 0x36b2;
export const T_ATTR_DICT = 0x36b3;
export const T_ATTR_EMPTY = 0x36b0;
export const T_ATTR_NAME = 0x36b4;
export const T_ATTR_ENTRIES = 0x36b5;
export const T_ATTR_KEY = 0x36b6;
export const T_ATTR_TRAILER = 0x36b7;

export const T_VALUE = 0x38a4;
export const T_VALUE_INT = 0x38a7;
export const T_VALUE_DOUBLE = 0x38a9;
export const T_VALUE_BOOL = 0x38aa;
export const T_VALUE_TIME = 0x38ac;
export const T_VALUE_STRING = 0x38ad;
export const T_VALUE_ARRAY = 0x38ae;
export const T_VALUE_LENGTH = 0x38af;
export const T_VALUE_POINT3D = 0x38b4;
export const T_VALUE_VECTOR3D = 0x38b5;

export const T_DEFINITION = 0x157c;
export const T_DEFINITION_CONTAINER = 0x1388;
export const T_DEFINITION_GUID = 0x157d;
export const T_DEFINITION_NAME = 0x157e;
export const T_DEFINITION_DESCRIPTION = 0x157f;
export const T_DEFINITION_SOURCE = 0x1580;
export const T_DEFINITION_TIMESTAMP = 0x1581;
export const T_DEFINITION_FLAG = 0x1582;
export const T_DEFINITION_KIND = 0x1583;
export const T_DEFINITION_BEHAVIOUR = 0x1b58;
export const T_DEFINITION_LIST = 0x1771;
export const T_DEFINITION_SECTION = 0x1770;

export const T_INSTANCE = 0x1964;
export const T_INSTANCE_NAME = 0x1965;
export const T_INSTANCE_TRANSFORM = 0x1966;
export const T_INSTANCE_DEFINITION = 0x1967;
export const T_INSTANCE_GUID = 0x1968;

export const T_RELATIONSHIP_GROUP = 0x639f;
export const T_RELATIONSHIP_LIST = 0x63a0;

export const T_META = 0x0064;
export const T_META_GUID = 0x0066;
export const T_META_REVISION = 0x0074;
export const T_META_HISTORY = 0x0078;
export const T_META_HISTORY_INNER = 0x00c8;
export const T_META_HISTORY_ENTRY = 0x00c9;
export const T_META_HISTORY_ITEM = 0x00ca;
export const T_META_HISTORY_TIME = 0x00cc;

export const MODEL_CONTAINERS = new Set<number>([
  T_DOCUMENT, T_NEXT_ID, 0x03e8, T_ENTITIES, T_MATERIALS, T_MATERIAL_SECTION,
  T_MATERIAL_LIST, T_MATERIAL, T_ENTITY_BASE, T_LAYERS, T_LAYER_SECTION,
  T_LAYER_LIST, T_LAYER, T_DEFINITIONS, T_DEFINITION_SECTION, T_DEFINITION_LIST,
  T_DEFINITION_CONTAINER, T_CAMERA_MODEL, T_CAMERA, T_PAGES_MODEL, T_PAGE_LIST,
  T_PAGE_ITEMS, T_PAGE, T_PAGE_BASE, T_VERTICES, T_EDGES, T_FACES, T_COMPONENTS,
  T_GROUPS, T_CURVES, T_ARC_CURVES, T_TEXTS, T_DIMENSIONS, T_SECTION_PLANES,
  T_GUIDE_LINES, T_GUIDE_POINTS, T_RELATIONSHIPS,
]);

export const META_CONTAINERS = new Set<number>([
  T_META, T_META_HISTORY, T_META_HISTORY_INNER, T_META_HISTORY_ENTRY, T_META_HISTORY_ITEM,
]);

export const MODEL_REVISION = 17;

const POOL_TAGS = [
  T_VERTICES, T_CURVES, T_ARC_CURVES, T_EDGES, T_FACES, T_COMPONENTS, T_GROUPS,
  T_GUIDE_LINES, T_GUIDE_POINTS, T_SECTION_PLANES, T_TEXTS, T_DIMENSIONS,
];
const DEFAULT_LAYER_COLOR: [number, number, number] = [136, 136, 136];

const TINY_PNG = Uint8Array.from(
  '89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c626000000000ffff03000006000557bfabd40000000049454e44ae426082'.match(/../g)!.map((v) => parseInt(v, 16)),
);

export function entityBase(id: number | null, attributes?: Record | null): Record {
  const children: Record[] = [];
  if (id !== null) children.push(record(T_ENTITY_ID, varint(id)));
  if (attributes) children.push(attributes);
  return new Record(T_ENTITY_BASE, undefined, children);
}

export function drawing(
  id: number | null,
  material?: number | null,
  layer?: number | null,
  flags = 0x06,
  attributes?: Record | null,
): Record {
  const children: Record[] = [entityBase(id, attributes)];
  if (material !== undefined && material !== null) children.push(record(T_FRONT_MATERIAL, varint(material)));
  if (layer !== undefined && layer !== null) children.push(record(T_LAYER_REF, varint(layer)));
  if (flags) children.push(record(T_FLAGS, u8(flags)));
  return new Record(T_DRAWING, undefined, children);
}

function materialRecord(id: number, name: string, owner: number, quality?: number): Record {
  const children: Record[] = [
    entityBase(id),
    record(T_MATERIAL_NAME, new TextEncoder().encode(name)),
    record(T_MATERIAL_OWNER, u8(owner)),
    record(T_MATERIAL_EMPTY, new Uint8Array(0)),
  ];
  if (quality !== undefined) children.push(record(T_MATERIAL_QUALITY, u32(quality)));
  return new Record(T_MATERIAL, undefined, children);
}

function attributeValue(value: VffAttributeValue): Record {
  const children: Record[] = [];
  if (value === null) {
    // empty
  } else if (typeof value === 'boolean') {
    children.push(record(T_VALUE_BOOL, u8(value ? 1 : 0)));
  } else if (value instanceof VffTimestamp) {
    const number = Math.trunc(value.value);
    if (!Number.isInteger(number) || number < 0 || number >= 2 ** 32) throw new Error('Timestamp must be in [0, 2**32)');
    children.push(record(T_VALUE_TIME, u32(number)));
  } else if (value instanceof VffLength) {
    children.push(record(T_VALUE_LENGTH, f64s([value.value])));
  } else if (typeof value === 'number') {
    if (Number.isInteger(value)) {
      if (value < -(2 ** 31) || value >= 2 ** 31) throw new Error('attribute integers must fit a signed 32-bit value');
      children.push(record(T_VALUE_INT, i32(value)));
    } else {
      children.push(record(T_VALUE_DOUBLE, f64s([value])));
    }
  } else if (typeof value === 'string') {
    children.push(record(T_VALUE_STRING, new TextEncoder().encode(value)));
  } else if (value instanceof VffPoint3d) {
    children.push(record(T_VALUE_POINT3D, f64s([value.x, value.y, value.z])));
  } else if (value instanceof VffVector3d) {
    children.push(record(T_VALUE_VECTOR3D, f64s([value.x, value.y, value.z])));
  } else if (Array.isArray(value)) {
    children.push(new Record(T_VALUE_ARRAY, undefined, value.map(attributeValue)));
  } else {
    throw new Error('unsupported attribute value type');
  }
  return new Record(T_VALUE, undefined, children);
}

function attributeDictionary(name: string, entries: VffAttributeDict): Record {
  const children: Record[] = [
    record(T_ATTR_EMPTY, new Uint8Array(0)),
    record(T_ATTR_NAME, new TextEncoder().encode(name)),
  ];
  const pairs: Record[] = [];
  for (const [key, value] of Object.entries(entries)) {
    pairs.push(record(T_ATTR_KEY, new TextEncoder().encode(String(key))));
    pairs.push(attributeValue(value));
  }
  children.push(new Record(T_ATTR_ENTRIES, undefined, pairs));
  children.push(record(T_ATTR_TRAILER, u32(0)));
  return new Record(T_ATTR_DICT, undefined, children);
}

function attributeContainer(dicts?: VffAttributeDicts, uv?: Record | null): Record | null {
  const children: Record[] = [];
  if (uv) children.push(uv);
  for (const [name, entries] of dicts ?? []) children.push(attributeDictionary(name, entries));
  if (children.length === 0) return null;
  return new Record(0x05dd, undefined, [new Record(T_ATTR_LIST, undefined, [new Record(T_ATTR_SET, undefined, children)])]);
}

function definitionRecord(container: Record, name: string, guid: Uint8Array, timestamp: number, kind = 0): Record {
  const behaviour = new Record(T_DEFINITION_BEHAVIOUR, undefined, [
    record(0x1b59, u32(0)), record(0x1b5a, u32(0)), record(0x1b5b, u8(0)),
    record(0x1b5c, u8(0)), record(0x1b5d, u8(0)), record(0x1b5e, u8(0)),
  ]);
  return new Record(T_DEFINITION, undefined, [
    container,
    record(T_DEFINITION_GUID, guid),
    record(T_DEFINITION_NAME, new TextEncoder().encode(name)),
    record(T_DEFINITION_DESCRIPTION, new Uint8Array(0)),
    record(T_DEFINITION_SOURCE, new Uint8Array(0)),
    record(T_DEFINITION_FLAG, u8(0)),
    record(T_DEFINITION_KIND, u32(kind)),
    record(T_DEFINITION_TIMESTAMP, u32(timestamp)),
    behaviour,
  ]);
}

function emptyRelationships(): Record {
  return new Record(T_RELATIONSHIPS, undefined, [
    new Record(T_RELATIONSHIP_GROUP, undefined, [record(T_RELATIONSHIP_LIST, new Uint8Array(0))]),
  ]);
}

function newIdListTag(ids: number[]): Record {
  return record(T_ORDER, idList(ids));
}

function identityOf(node: Record): number | null {
  if (node.tag !== T_ENTITY_BASE) return null;
  const idNode = node.child(T_ENTITY_ID);
  return idNode ? readVarint(idNode.payload) : null;
}

export function findEntityId(node: Record | null): number | null {
  if (!node || !node.children) return null;
  for (const child of node.children) {
    const id = identityOf(child);
    if (id !== null) return id;
  }
  return null;
}

function xmlNumber(value: number): string {
  return value.toFixed(6).replace(/0+$/, '').replace(/\.$/, '') || '0';
}

function xmlEscape(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function packedAverage(rgb: [number, number, number]): number {
  return ((0xff << 24) | (rgb[2] << 16) | (rgb[1] << 8) | rgb[0]) >>> 0;
}

function pbrBlock(xScale: number, yScale: number): string {
  return `    <mat:pbrMR xScale="${xmlNumber(xScale)}" yScale="${xmlNumber(yScale)}">
      <mat:enable_metalness>0</mat:enable_metalness>
      <mat:enable_roughness>0</mat:enable_roughness>
      <mat:enable_normal>0</mat:enable_normal>
      <mat:enable_occlusion>0</mat:enable_occlusion>
      <mat:metallicFactor>1</mat:metallicFactor>
      <mat:roughnessFactor>1</mat:roughnessFactor>
      <mat:normalMapStyle>1</mat:normalMapStyle>
      <mat:normalScale>1</mat:normalScale>
      <mat:occlusionStrength>1</mat:occlusionStrength>
      <mat:baseColorFactor>4294967295</mat:baseColorFactor>
    </mat:pbrMR>
`;
}

export interface VffTextureInfo {
  width: number;
  height: number;
  filename: string;
  basename: string;
}

export function materialXml(
  name: string,
  rgb: [number, number, number] = [255, 255, 255],
  options: { layerColor?: boolean; opacity?: number; texture?: VffTextureInfo } = {},
): Uint8Array {
  const [red, green, blue] = rgb.map((c) => Math.max(0, Math.min(255, Math.trunc(c))));
  const promo = options.layerColor ? '0' : '2';
  const opacity = Math.min(Math.max(options.opacity ?? 1, 0), 1);
  const transAttr = opacity >= 1 ? '0.5' : xmlNumber(1 - opacity);
  const useTrans = opacity >= 1 ? '0' : '1';
  let typeAttr = '0';
  let hasTexture = '0';
  let block = pbrBlock(1, 1);
  if (options.texture) {
    const { width, height, filename, basename } = options.texture;
    const packed = packedAverage([red, green, blue]);
    typeAttr = '1';
    hasTexture = '1';
    block =
      `    <mat:texture textureFilename="${xmlEscape(filename)}" xScale="${xmlNumber(width)}" yScale="${xmlNumber(height)}" avgColor="${packed}">\n` +
      '      <mat:images>\n' +
      `        <mat:image id="1" path="./${xmlEscape(basename)}" file_name="${xmlEscape(basename)}"/>\n` +
      '      </mat:images>\n' +
      '    </mat:texture>\n' +
      pbrBlock(width, height);
  }
  const text =
    '<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n' +
    '<materialDocument xmlns="http://sketchup.google.com/schemas/sketchup/1.0/material" xmlns:mat="http://sketchup.google.com/schemas/sketchup/1.0/material">\n' +
    `  <mat:material name="${xmlEscape(name)}" type="${typeAttr}" workflow="0" colorRed="${red}" colorGreen="${green}" colorBlue="${blue}" colorizeType="0" trans="${transAttr}" useTrans="${useTrans}" pbrPromoState="${promo}" hasTexture="${hasTexture}">\n` +
    block +
    '  </mat:material>\n' +
    '</materialDocument>\n';
  return new TextEncoder().encode(text);
}

export function sanitizeFolder(name: string): string {
  const out = Array.from(name, (char) => ('*<>:|/\\'.includes(char) ? '_' : char)).join('');
  return out || '_';
}

function materialFolderFor(name: string, taken: string[]): string {
  const base = sanitizeFolder(name);
  if (!taken.includes(base)) return base;
  let counter = 1;
  while (taken.includes(`${base}${counter}`)) counter++;
  return `${base}${counter}`;
}

export interface VffScene {
  name: string;
  eye: Point3;
  target: Point3;
  up: Point3;
  perspective: boolean;
  fov: number;
  orthoHeight: number;
  hiddenLayers: number[];
}

export interface VffExistingDefinition {
  id: number | null;
  name: string;
  isGroup: boolean;
  bounds: number[] | null;
}

export class Template {
  readonly options: VffWriterOptions;
  readonly header: Uint8Array;
  readonly entries: ZipEntry[];
  readonly byName: Map<string, ZipEntry>;
  readonly document: Record;
  readonly sections: Map<number, Record>;
  nextId: number;
  layers: { [name: string]: number } = {};
  layerIds: Map<number, string> = new Map();
  materials: { [name: string]: number } = {};
  materialIds: Map<number, string> = new Map();
  materialFolders: string[] = [];
  textureSizes: Map<number, [number, number]> = new Map();
  defaultLayerId: number | null;
  fontId: number | null;
  styleId: number | null;
  definitions: VffExistingDefinition[] = [];
  scenes: VffScene[] = [];
  camera: VffScene | null = null;
  newEntries: ZipEntry[] = [];
  entryNames: Set<string>;
  private guidCounter = 0;

  constructor(data: Uint8Array, options: VffWriterOptions = {}) {
    this.options = { ...options };
    const zip = zipOffset(data);
    this.header = data.subarray(0, zip);
    this.entries = entriesOf(data);
    this.byName = new Map(this.entries.map((entry) => [entry.name, entry]));
    this.entryNames = new Set(this.byName.keys());
    const model = this.byName.get('model.dat');
    if (!model) throw new Error('template has no model.dat');
    const top = parseRecords(model.data, MODEL_CONTAINERS);
    if (top.length !== 1 || top[0].tag !== T_DOCUMENT) throw new Error('template model.dat is not a single 0x01f4 document');
    this.document = top[0];
    this.sections = new Map();
    for (const child of this.document.children ?? []) if (!this.sections.has(child.tag)) this.sections.set(child.tag, child);
    for (const required of [T_NEXT_ID, T_ENTITIES, T_MATERIALS, T_LAYERS, T_DEFINITIONS]) {
      if (!this.sections.has(required)) throw new Error(`template is missing the 0x${required.toString(16)} section this writer edits`);
    }
    this.nextId = this.readNextId();
    this.readLayers();
    this.readMaterials();
    this.defaultLayerId = this.layers.Layer0 ?? [...this.layerIds.keys()][0] ?? null;
    this.fontId = this.readFontId();
    this.styleId = this.readStyleId();
    this.definitions = this.readDefinitions();
  }

  section(tag: number): Record {
    const section = this.sections.get(tag);
    if (!section) throw new Error(`template has no 0x${tag.toString(16)} section`);
    return section;
  }

  readNextId(): number {
    const wrapper = this.section(T_NEXT_ID).child(0x03e8);
    const value = wrapper?.child(0x03e9);
    if (!value) throw new Error('template has an empty next-id record');
    return readVarint(value.payload);
  }

  allocId(): number {
    return this.nextId++;
  }

  guidBytes(): Uint8Array {
    const seed = this.options.guidSeed;
    if (seed === undefined) {
      const out = new Uint8Array(16);
      const cryptoObj = (globalThis as { crypto?: { getRandomValues?: (array: Uint8Array) => Uint8Array } }).crypto;
      if (cryptoObj?.getRandomValues) cryptoObj.getRandomValues(out);
      else for (let i = 0; i < 16; i++) out[i] = Math.floor(Math.random() * 256);
      return out;
    }
    let state = (seed ^ Math.imul(this.guidCounter, 0x9e3779b9)) >>> 0;
    this.guidCounter++;
    const out = new Uint8Array(16);
    const view = new DataView(out.buffer);
    for (let i = 0; i < 4; i++) {
      state ^= (state << 13) >>> 0;
      state ^= state >>> 17;
      state ^= (state << 5) >>> 0;
      state >>>= 0;
      view.setUint32(i * 4, state, true);
    }
    return out;
  }

  modelGuidBytes(): Uint8Array {
    const supplied = this.options.modelGuid;
    if (supplied !== undefined) {
      if (typeof supplied === 'string') {
        const hex = supplied.replace(/-/g, '');
        return Uint8Array.from(hex.match(/../g)!.map((v) => parseInt(v, 16)));
      }
      return supplied;
    }
    return this.guidBytes();
  }

  saveTime(): number {
    return this.options.saveTime ?? Math.floor(Date.now() / 1000);
  }

  definitionTime(): number {
    return this.options.definitionTime ?? Math.floor(Date.now() / 1000);
  }

  readFontId(): number | null {
    const section = this.sections.get(0x01fd);
    if (!section) return null;
    let nodes: Record[];
    try {
      nodes = parseRecords(section.payload, [0x4e20, 0x4e21, 0x5014, T_ENTITY_BASE]);
    } catch {
      return null;
    }
    for (const wrapper of nodes) {
      if (wrapper.tag !== 0x4e20 || !wrapper.children) continue;
      for (const pool of wrapper.children) {
        if (pool.tag !== 0x4e21 || !pool.children) continue;
        for (const font of pool.children) if (font.tag === 0x5014) return findEntityId(font);
      }
    }
    return null;
  }

  readDefinitions(): VffExistingDefinition[] {
    const section = this.sections.get(T_DEFINITIONS);
    const wrapper = section?.child(T_DEFINITION_SECTION);
    const listing = wrapper?.child(T_DEFINITION_LIST);
    if (!listing?.children) return [];
    const definitions: VffExistingDefinition[] = [];
    for (const definition of listing.children) {
      if (definition.tag !== T_DEFINITION) continue;
      let parts: Record[];
      try {
        parts = parseRecords(definition.payload, [T_DEFINITION_CONTAINER, T_DRAWING, T_ENTITY_BASE]);
      } catch {
        continue;
      }
      const container = parts.find((part) => part.tag === T_DEFINITION_CONTAINER);
      const drawingNode = container?.child(T_DRAWING) ?? null;
      const id = findEntityId(drawingNode);
      const nameNode = parts.find((part) => part.tag === T_DEFINITION_NAME);
      const kindNode = parts.find((part) => part.tag === T_DEFINITION_KIND);
      const boundsNode = container?.child(T_BOUNDS) ?? null;
      definitions.push({
        id,
        name: nameNode ? new TextDecoder().decode(nameNode.payload) : '',
        isGroup: !!kindNode && readVarint(kindNode.payload) === 1,
        bounds: boundsNode && boundsNode.payload.length === 48 ? readF64s(boundsNode.payload) : null,
      });
    }
    return definitions;
  }

  readStyleId(): number | null {
    const section = this.sections.get(T_STYLES);
    if (!section) return null;
    let nodes: Record[];
    try {
      nodes = parseRecords(section.payload, [0x6978, 0x6979, 0x6b6c, T_ENTITY_BASE]);
    } catch {
      return null;
    }
    for (const wrapper of nodes) {
      if (wrapper.tag !== 0x6978 || !wrapper.children) continue;
      for (const pool of wrapper.children) {
        if (pool.tag !== 0x6979 || !pool.children) continue;
        for (const style of pool.children) if (style.tag === 0x6b6c) return findEntityId(style);
      }
    }
    return null;
  }

  materialList(): Record {
    const wrapper = this.section(T_MATERIALS).child(T_MATERIAL_SECTION);
    if (!wrapper) throw new Error('template materials section is malformed');
    let entries = wrapper.child(T_MATERIAL_LIST);
    if (!entries) {
      entries = new Record(T_MATERIAL_LIST, undefined, []);
      wrapper.children!.push(entries);
    }
    if (!entries.children) entries.children = [];
    return entries;
  }

  layerList(): Record {
    const wrapper = this.section(T_LAYERS).child(T_LAYER_SECTION);
    if (!wrapper) throw new Error('template layers section is malformed');
    let entries = wrapper.child(T_LAYER_LIST);
    if (!entries) {
      entries = new Record(T_LAYER_LIST, undefined, []);
      wrapper.children!.push(entries);
    }
    if (!entries.children) entries.children = [];
    return entries;
  }

  definitionList(): Record {
    const wrapper = this.section(T_DEFINITIONS).child(T_DEFINITION_SECTION);
    if (!wrapper) throw new Error('template definitions section is malformed');
    let entries = wrapper.child(T_DEFINITION_LIST);
    if (!entries) {
      entries = new Record(T_DEFINITION_LIST, undefined, []);
      wrapper.children!.push(entries);
    }
    if (!entries.children) entries.children = [];
    return entries;
  }

  readLayers(): void {
    for (const layer of this.layerList().children ?? []) {
      if (layer.tag !== T_LAYER) continue;
      const id = findEntityId(layer);
      const nameNode = layer.child(T_LAYER_NAME);
      if (id === null || !nameNode) continue;
      const name = new TextDecoder().decode(nameNode.payload);
      if (!(name in this.layers)) this.layers[name] = id;
      this.layerIds.set(id, name);
    }
  }

  readMaterials(): void {
    for (const material of this.materialList().children ?? []) {
      if (material.tag !== T_MATERIAL) continue;
      const id = findEntityId(material);
      const nameNode = material.child(T_MATERIAL_NAME);
      if (id === null || !nameNode) continue;
      const name = new TextDecoder().decode(nameNode.payload);
      this.materialIds.set(id, name);
      this.materials[name] = id;
      this.materialFolders.push(name);
    }
  }

  addMaterial(name: string, rgb: [number, number, number], opacity = 1): number {
    if (name in this.materials) return this.materials[name];
    const folder = materialFolderFor(name, this.materialFolders);
    const id = this.allocId();
    this.materialList().children!.push(materialRecord(id, folder, 0));
    this.materialIds.set(id, name);
    this.materialFolders.push(folder);
    this.materials[name] = id;
    this.addEntry(`materials/${folder}/material.xml`, materialXml(name, rgb, { opacity }));
    return id;
  }

  addTextureMaterial(
    name: string,
    image: Uint8Array,
    imageName: string,
    width: number,
    height: number,
    opacity: number,
    averageRgb: [number, number, number],
    textureFilename: string,
  ): number {
    if (name in this.materials) return this.materials[name];
    if (width <= 0 || height <= 0) throw new Error("a texture's applied size must be positive");
    const folder = materialFolderFor(name, this.materialFolders);
    const id = this.allocId();
    this.materialList().children!.push(materialRecord(id, folder, 0, 90));
    this.materialIds.set(id, name);
    this.materialFolders.push(folder);
    this.materials[name] = id;
    this.textureSizes.set(id, [width, height]);
    this.addEntry(`materials/${folder}/${imageName}`, image, 0x0000);
    this.addEntry(
      `materials/${folder}/material.xml`,
      materialXml(name, averageRgb, {
        opacity,
        texture: { width, height, filename: textureFilename, basename: imageName },
      }),
    );
    return id;
  }

  addLayer(name: string, color: [number, number, number] | null = null, hidden = false): number {
    if (name in this.layers) return this.layers[name];
    const rgb = color ?? DEFAULT_LAYER_COLOR;
    const id = this.allocId();
    const materialId = this.allocId();
    const folder = materialFolderFor(`Layer_${name}`, this.materialFolders);
    this.materialFolders.push(folder);
    const layer = new Record(T_LAYER, undefined, [
      entityBase(id),
      record(T_LAYER_NAME, new TextEncoder().encode(name)),
      record(T_LAYER_HIDDEN, u8(hidden ? 1 : 0)),
      new Record(T_LAYER_MATERIAL, undefined, [materialRecord(materialId, folder, 1)]),
      record(T_LAYER_TAIL, u32(0)),
    ]);
    this.layerList().children!.push(layer);
    this.layers[name] = id;
    this.layerIds.set(id, name);
    this.addEntry(`materials/${folder}/material.xml`, materialXml(`Layer_${name}`, rgb, { layerColor: true }));
    return id;
  }

  addDefinition(container: Record, name: string, _containerId: number, kind = 0): number {
    const definition = definitionRecord(container, name, this.guidBytes(), this.definitionTime(), kind);
    this.definitionList().children!.push(definition);
    return _containerId;
  }

  addEntry(name: string, data: Uint8Array, flag = 0x0006): void {
    if (this.entryNames.has(name)) throw new Error(`archive entry already exists: ${name}`);
    this.entryNames.add(name);
    this.newEntries.push(makeZipEntry(name, data, flag));
  }

  addScene(
    name: string,
    eye: Point3,
    target: Point3,
    up: Point3 | null,
    options: { perspective: boolean; fov: number; orthoHeight: number; hiddenLayers: number[] },
  ): void {
    if (!name) throw new Error('a scene needs a name');
    if (this.scenes.some((scene) => scene.name === name)) throw new Error(`a scene named ${name} already exists`);
    if (!(options.fov > 0 && options.fov < 180)) throw new Error('scene field of view must be between 0 and 180 degrees');
    if (options.orthoHeight <= 0) throw new Error('scene ortho_height must be positive');
    for (const layer of options.hiddenLayers) if (!this.layerIds.has(layer)) throw new Error(`unknown layer handle: ${layer}`);
    const direction: Point3 = [target[0] - eye[0], target[1] - eye[1], target[2] - eye[2]];
    if (direction.every((value) => Math.abs(value) === 0)) throw new Error('scene camera eye and target coincide');
    const corrected = up ? orthogonalUp(up, direction) : defaultUp(eye, target);
    const hidden = [...new Set(options.hiddenLayers)];
    this.scenes.push({
      name,
      eye: [...eye] as Point3,
      target: [...target] as Point3,
      up: corrected,
      perspective: options.perspective,
      fov: options.fov,
      orthoHeight: options.orthoHeight,
      hiddenLayers: hidden,
    });
  }

  setCamera(
    eye: Point3,
    target: Point3,
    up: Point3 | null,
    options: { perspective: boolean; fov: number; orthoHeight: number },
  ): void {
    const direction: Point3 = [target[0] - eye[0], target[1] - eye[1], target[2] - eye[2]];
    const corrected = up ? orthogonalUp(up, direction) : defaultUp(eye, target);
    this.camera = {
      name: '',
      eye: [...eye] as Point3,
      target: [...target] as Point3,
      up: corrected,
      perspective: options.perspective,
      fov: options.fov,
      orthoHeight: options.orthoHeight,
      hiddenLayers: [],
    };
  }

  cameraTemplate(): Record {
    const section = this.sections.get(T_CAMERA_MODEL);
    if (!section) throw new Error('the template has no camera section');
    let camera = section.child(T_CAMERA);
    if (!camera && section.payload.length) {
      try {
        camera = parseRecords(section.payload, [T_CAMERA]).find((node) => node.tag === T_CAMERA) ?? null;
      } catch {
        camera = null;
      }
    }
    if (!camera?.children) throw new Error('the template camera record is malformed');
    return camera;
  }

  cameraRecord(scene: VffScene): Record {
    const template = this.cameraTemplate();
    const replacements = new Map<number, Uint8Array>([
      [T_CAMERA_EYE, f64s(scene.eye)],
      [T_CAMERA_TARGET, f64s(scene.target)],
      [T_CAMERA_UP, f64s(scene.up)],
      [T_CAMERA_PERSPECTIVE, u8(scene.perspective ? 1 : 0)],
      [T_CAMERA_FOV, f64s([scene.fov])],
      [T_CAMERA_HEIGHT, f64s([scene.perspective ? 1 : scene.orthoHeight])],
    ]);
    return new Record(T_CAMERA, undefined, (template.children ?? []).map((child) => new Record(child.tag, replacements.get(child.tag) ?? child.payload)));
  }

  buildPages(): void {
    if (this.scenes.length === 0) return;
    const section = this.sections.get(T_PAGES_MODEL);
    if (!section) return;
    if (!section.children) {
      try {
        section.children = parseRecords(section.payload, [T_PAGE_LIST, T_PAGE_ITEMS, T_PAGE, T_PAGE_BASE]);
      } catch {
        section.children = [];
      }
      section.payload = new Uint8Array(0);
    }
    let list = section.child(T_PAGE_LIST);
    if (!list) {
      list = new Record(T_PAGE_LIST, undefined, [new Record(T_PAGE_ITEMS, undefined, [])]);
      section.children.push(list);
    }
    let items = list.child(T_PAGE_ITEMS);
    if (!items) {
      items = new Record(T_PAGE_ITEMS, undefined, []);
      list.children!.push(items);
    }
    if (!items.children) items.children = [];
    const pageIds: number[] = [];
    for (const existing of items.children) {
      const base = existing.child(T_PAGE_BASE);
      const id = findEntityId(base);
      if (id !== null) pageIds.push(id);
    }
    const stylePayload = this.styleId !== null ? varint(this.styleId) : new Uint8Array(0);
    for (const scene of this.scenes) {
      const pageId = this.allocId();
      const hiddenPayload = concat(scene.hiddenLayers.map((layer) => {
        const encoded = varint(layer);
        return concat([u8(encoded.length), encoded]);
      }));
      const page = new Record(T_PAGE, undefined, [
        new Record(T_PAGE_BASE, undefined, [entityBase(pageId), record(T_PAGE_NAME, new TextEncoder().encode(scene.name))]),
        record(T_PAGE_PROPERTIES, u32(1 | 2 | 4 | 8 | 32)),
        new Record(T_PAGE_CAMERA, undefined, [this.cameraRecord(scene)]),
        record(T_PAGE_STYLE, stylePayload),
        record(T_PAGE_RENDERING, this.section(T_RENDERING).payload),
        record(T_PAGE_SHADOWS, this.section(T_SHADOWS).payload),
        record(T_PAGE_AXES, this.section(T_AXES).payload),
        record(T_PAGE_HIDDEN_LAYERS, hiddenPayload),
        record(T_PAGE_ANIMATION, u8(1)),
        record(T_PAGE_DISPLAY_NAME, new TextEncoder().encode(scene.name)),
        record(T_PAGE_TRANSITION, f64s([-1])),
        record(T_PAGE_DELAY, f64s([-1])),
        record(T_PAGE_THUMBNAIL_FLAG, u8(1)),
      ]);
      items.children.push(page);
      pageIds.push(pageId);
    }
    if (pageIds.length) {
      let selected = list.child(T_PAGE_SELECTED);
      if (!selected) {
        selected = record(T_PAGE_SELECTED, varint(pageIds[0]));
        list.children!.push(selected);
      } else {
        selected.payload = varint(pageIds[0]);
      }
    }
  }

  patchModelCamera(): void {
    const camera = this.camera ?? {
      name: '',
      eye: [100, -100, 100] as Point3,
      target: [0, 0, 0] as Point3,
      up: defaultUp([100, -100, 100], [0, 0, 0]),
      perspective: false,
      fov: 35,
      orthoHeight: 200,
      hiddenLayers: [],
    };
    const section = this.sections.get(T_CAMERA_MODEL);
    if (!section) return;
    section.children = [this.cameraRecord(camera)];
    section.payload = new Uint8Array(0);
  }

  metaBytes(): Uint8Array {
    const meta = this.byName.get('meta/meta.dat');
    if (!meta) throw new Error('template has no meta/meta.dat');
    const records = parseRecords(meta.data, META_CONTAINERS);
    if (records.length !== 1 || records[0].tag !== T_META) throw new Error('template meta.dat is not a single 0x0064 record');
    const root = records[0];
    const guid = root.child(T_META_GUID);
    if (!guid) throw new Error('template meta.dat has no model GUID');
    guid.payload = this.modelGuidBytes();
    const revision = root.child(T_META_REVISION);
    if (revision) revision.payload = u16(MODEL_REVISION);
    const history = root.child(T_META_HISTORY);
    const wrappers = history?.child(T_META_HISTORY_INNER);
    const entries = wrappers?.child(T_META_HISTORY_ENTRY);
    const items = entries?.childrenWith(T_META_HISTORY_ITEM) ?? [];
    if (items.length) {
      const stamp = items[items.length - 1].child(T_META_HISTORY_TIME);
      if (stamp) stamp.payload = u32(this.saveTime());
    }
    return encodeRecords(records);
  }

  get rootContainer(): Record {
    const container = this.section(T_ENTITIES).child(T_DEFINITION_CONTAINER);
    if (!container?.children) throw new Error('template model has no root entity container');
    return container;
  }

  build(): Uint8Array {
    this.buildPages();
    this.patchModelCamera();
    this.metaBytes(); // validate early, same error surface as Python
    const nextSection = this.section(T_NEXT_ID);
    nextSection.children = [new Record(0x03e8, undefined, [record(0x03e9, varint(this.nextId))])];
    const modelDat = this.document.encode();
    const meta = this.metaBytes();
    const thumbnails: ZipEntry[] = [];
    const materials: ZipEntry[] = [];
    const others: ZipEntry[] = [];
    for (const entry of this.entries) {
      if (entry.name === 'model.dat' || entry.name === 'meta/meta.dat') continue;
      if (entry.name === 'meta/model_thumbnail.png' || entry.name === 'meta/preview_thumbnail.png') {
        thumbnails.push(makeZipEntry(entry.name, entry.data, 0x0000));
      } else if (entry.name.startsWith('materials/')) {
        materials.push(entry);
      } else {
        others.push(entry);
      }
    }
    if (thumbnails.length < 2) {
      const existing = new Set(thumbnails.map((entry) => entry.name));
      for (const fallback of ['meta/model_thumbnail.png', 'meta/preview_thumbnail.png']) {
        if (!existing.has(fallback)) thumbnails.push(makeZipEntry(fallback, TINY_PNG, 0x0000));
      }
    }
    const orderedThumbs = [
      thumbnails.find((entry) => entry.name === 'meta/model_thumbnail.png')!,
      thumbnails.find((entry) => entry.name === 'meta/preview_thumbnail.png')!,
    ];
    const entries: ZipEntry[] = [
      ...orderedThumbs,
      makeZipEntry('meta/meta.dat', meta, 0x0006),
      ...materials,
      ...this.newEntries,
      ...others,
      makeZipEntry('model.dat', modelDat, 0x0006),
    ];
    const header = new Uint8Array(this.header);
    const start = headerOffset(header);
    new DataView(header.buffer).setUint16(start + 7, MODEL_REVISION, true);
    return buildSkp(header, entries);
  }
}

function orthogonalUp(up: ArrayLike<number>, direction: ArrayLike<number>): Point3 {
  const length = Math.sqrt(direction[0] * direction[0] + direction[1] * direction[1] + direction[2] * direction[2]);
  if (length === 0) throw new Error('scene camera eye and target coincide');
  const d = [direction[0] / length, direction[1] / length, direction[2] / length];
  const dot = up[0] * d[0] + up[1] * d[1] + up[2] * d[2];
  const projected = [up[0] - dot * d[0], up[1] - dot * d[1], up[2] - dot * d[2]];
  const plen = Math.sqrt(projected[0] * projected[0] + projected[1] * projected[1] + projected[2] * projected[2]);
  if (plen < 1e-9) throw new Error('scene up vector is zero or parallel to the view direction');
  return [projected[0] / plen, projected[1] / plen, projected[2] / plen];
}

function defaultUp(eye: Point3, target: Point3): Point3 {
  const direction: Point3 = [target[0] - eye[0], target[1] - eye[1], target[2] - eye[2]];
  const length = Math.sqrt(direction[0] * direction[0] + direction[1] * direction[1] + direction[2] * direction[2]);
  const d = [direction[0] / length, direction[1] / length, direction[2] / length];
  const seed: Point3 = Math.abs(d[2]) > 0.999 ? [0, 1, 0] : [0, 0, 1];
  return orthogonalUp(seed, direction);
}

// ── Entity editor ──────────────────────────────────────────────────────

export interface VffCurveHandle {
  points: Point3[];
  curveId: number;
  edgeIds: number[];
}

export class EntityEditor {
  readonly template: Template;
  readonly fresh: boolean;
  container: Record;
  readonly containerId: number | null;
  vertices = new Map<string, number>();
  edges = new Map<string, [number, number]>();
  order: number[] = [];
  bounds: number[] | null = null;
  activeSection: number | null = null;
  private pools = new Map<number, Record>();
  private existingOrder: number[];

  constructor(
    template: Template,
    options: { container?: Record; containerId?: number | null; attributes?: VffAttributeDicts },
  ) {
    this.template = template;
    this.fresh = options.container === undefined;
    this.container = options.container ?? blankContainer(options.containerId ?? null, attributeContainer(options.attributes));
    this.containerId = options.containerId ?? null;
    if (options.container) {
      for (const child of options.container.children ?? []) if (POOL_TAGS.includes(child.tag) && child.children) this.pools.set(child.tag, child);
    }
    this.existingOrder = this.readOrder();
  }

  readOrder(): number[] {
    const order = this.container.child(T_ORDER);
    if (!order || order.children) return [];
    try {
      return decodeIdList(order.payload);
    } catch {
      return [];
    }
  }

  pool(tag: number): Record {
    let pool = this.pools.get(tag);
    if (!pool) {
      pool = new Record(tag, undefined, []);
      this.pools.set(tag, pool);
    }
    return pool;
  }

  extendBounds(point: ArrayLike<number>): void {
    if (!this.bounds) this.bounds = [point[0], point[1], point[2], point[0], point[1], point[2]];
    else {
      const b = this.bounds;
      b[0] = Math.min(b[0], point[0]);
      b[1] = Math.min(b[1], point[1]);
      b[2] = Math.min(b[2], point[2]);
      b[3] = Math.max(b[3], point[0]);
      b[4] = Math.max(b[4], point[1]);
      b[5] = Math.max(b[5], point[2]);
    }
  }

  addVertex(point: ArrayLike<number>): number {
    const key = `${point[0]},${point[1]},${point[2]}`;
    const existing = this.vertices.get(key);
    if (existing !== undefined) return existing;
    const id = this.template.allocId();
    this.pool(T_VERTICES).children!.push(new Record(T_VERTEX, undefined, [entityBase(id), record(T_POINT, f64s(point))]));
    this.vertices.set(key, id);
    this.extendBounds(point);
    return id;
  }

  addEdge(start: number, end: number, layer: number | null, flags = 0x06, curve: number | null = null): [number, boolean] {
    const key = start <= end ? `${start},${end}` : `${end},${start}`;
    const existing = this.edges.get(key);
    if (existing) return [existing[0], existing[1] === start];
    const id = this.template.allocId();
    const children: Record[] = [
      drawing(id, null, layer, flags),
      record(T_EDGE_START, varint(start)),
      record(T_EDGE_END, varint(end)),
    ];
    if (curve !== null) children.push(record(T_EDGE_CURVE, varint(curve)));
    this.pool(T_EDGES).children!.push(new Record(T_EDGE, undefined, children));
    this.edges.set(key, [id, start]);
    this.order.push(id);
    return [id, true];
  }

  addFace(
    points: ArrayLike<number>[],
    options: {
      material?: number | null;
      layer?: number | null;
      backMaterial?: number | null;
      hidden?: boolean;
      holes?: ArrayLike<number>[][] | Array<{ points: Point3[] }>;
      frontUv?: Array<[ArrayLike<number>, [number, number]]>;
      backUv?: Array<[ArrayLike<number>, [number, number]]>;
      attributeDicts?: VffAttributeDicts;
    } = {},
  ): number {
    const outer = points.map((p) => [Number(p[0]), Number(p[1]), Number(p[2])] as Point3);
    if (outer.length < 3) throw new Error('a face needs at least three points');
    const normal = unitNormal(newellNormal(outer));
    const planeD = -(normal[0] * outer[0][0] + normal[1] * outer[0][1] + normal[2] * outer[0][2]);
    const rings: Point3[][] = [outer];
    for (const hole of options.holes ?? []) {
      const source = (hole as { points?: Point3[] }).points ?? (hole as Point3[]);
      let ring = source.map((p) => [Number(p[0]), Number(p[1]), Number(p[2])] as Point3);
      if (ring.length < 3) throw new Error('a hole needs at least three points');
      const holeNormal = newellNormal(ring);
      if (holeNormal[0] * normal[0] + holeNormal[1] * normal[1] + holeNormal[2] * normal[2] > 0) ring = ring.reverse();
      rings.push(ring);
    }
    const loops: Record[] = [];
    for (const ring of rings) {
      const uses: Record[] = [];
      for (let index = 0; index < ring.length; index++) {
        const startPoint = ring[index];
        const endPoint = ring[(index + 1) % ring.length];
        const start = this.addVertex(startPoint);
        const end = this.addVertex(endPoint);
        if (start === end) throw new Error('a face ring has a repeated adjacent point');
        const [edgeId, forward] = this.addEdge(start, end, options.layer ?? null);
        uses.push(new Record(T_EDGE_USE, undefined, [
          record(T_EDGE_USE_ID, varint(edgeId)),
          record(T_EDGE_USE_FORWARD, u8(forward ? 0 : 1)),
        ]));
      }
      loops.push(new Record(T_LOOP, undefined, [new Record(T_LOOP_EDGES, undefined, uses)]));
    }
    let uvCoords: Record | null = null;
    if (options.frontUv || options.backUv) {
      const front = options.frontUv ? this.uvPlacement(options.frontUv, options.material ?? null, normal) : null;
      const back = options.backUv ? this.uvPlacement(options.backUv, options.backMaterial ?? options.material ?? null, normal) : null;
      uvCoords = uvAttribute(front ?? undefined, back ?? undefined);
    }
    const attributes = attributeContainer(options.attributeDicts, uvCoords);
    const id = this.template.allocId();
    const flags = 0x06 | (options.hidden ? 0x01 : 0);
    const children: Record[] = [
      drawing(id, options.material ?? null, options.layer ?? null, flags, attributes),
      record(T_PLANE, f64s([...normal, planeD])),
      new Record(T_LOOPS, undefined, loops),
    ];
    if (options.backMaterial !== undefined && options.backMaterial !== null) children.push(record(T_BACK_MATERIAL, varint(options.backMaterial)));
    this.pool(T_FACES).children!.push(new Record(T_FACE, undefined, children));
    this.order.push(id);
    return id;
  }

  private uvPlacement(pins: Array<[ArrayLike<number>, [number, number]]>, material: number | null, normal: Point3): number[] {
    const size = material !== null ? this.template.textureSizes.get(material) : undefined;
    if (!size) throw new Error('front_uv/back_uv need a textured material handle');
    return uvMatrixFromPins(pins, normal, size[0], size[1]);
  }

  fontId(): number {
    if (this.template.fontId === null) throw new Error('the template has no font record to reference');
    return this.template.fontId;
  }

  connection(point?: ArrayLike<number>, vertexId?: number): Record {
    if (vertexId === undefined) {
      if (!point) throw new Error('a free connection needs a point');
      return new Record(T_CONNECTION, undefined, [
        record(T_CONNECTION_TYPE, u32(1)),
        record(T_CONNECTION_POINT, f64s(point)),
        new Record(T_CONNECTION_REF_A, undefined, [new Record(T_CONNECTION_REF, undefined, [record(T_CONNECTION_PATH, new Uint8Array(0))])]),
        new Record(T_CONNECTION_REF_B, undefined, [new Record(T_CONNECTION_REF, undefined, [record(T_CONNECTION_PATH, new Uint8Array(0))])]),
      ]);
    }
    const ref = new Record(T_CONNECTION_REF, undefined, [record(T_CONNECTION_VERTEX, varint(vertexId)), record(T_CONNECTION_PATH, new Uint8Array(0))]);
    return new Record(T_CONNECTION, undefined, [
      record(T_CONNECTION_TYPE, u32(2)),
      record(T_CONNECTION_POINT, f64s([0, 0, 0])),
      new Record(T_CONNECTION_REF_A, undefined, [ref]),
      new Record(T_CONNECTION_REF_B, undefined, [new Record(T_CONNECTION_REF, undefined, [record(T_CONNECTION_PATH, new Uint8Array(0))])]),
    ]);
  }

  addText(text: string, point: ArrayLike<number>, vector: ArrayLike<number>, options: { screen?: boolean; layer?: number | null } = {}): number {
    const id = this.template.allocId();
    const item = new Record(T_TEXT, undefined, [
      drawing(id, null, options.layer ?? null),
      record(T_TEXT_STRING, new TextEncoder().encode(text)),
      record(T_TEXT_EMPTY, new Uint8Array(0)),
      record(T_TEXT_ZERO_A, f64s([0])),
      record(T_TEXT_ZERO_B, f64s([0])),
      new Record(T_TEXT_ANCHOR, undefined, [this.connection(point)]),
      record(T_TEXT_VECTOR, f64s(vector)),
      record(T_TEXT_ONE, u8(1)),
      record(T_TEXT_UP, f64s([0, 0, 1])),
      record(T_TEXT_ZERO, u8(0)),
      record(T_TEXT_FONT, varint(this.fontId())),
      record(T_TEXT_ARROW, u32(3)),
      record(T_TEXT_LEADER_FLAG_A, u32(1)),
      record(T_TEXT_LEADER_FLAG, u8(options.screen ? 0 : 1)),
      record(T_TEXT_LEADER_TYPE, u32(2)),
      record(T_TEXT_SCREEN, u32(options.screen ? 1 : 0)),
    ]);
    this.pool(T_TEXTS).children!.push(item);
    this.order.push(id);
    return id;
  }

  addDimension(start: ArrayLike<number>, end: ArrayLike<number>, offset = 10, offsetVector?: ArrayLike<number> | null, anchor = false): number {
    const p1: Point3 = [Number(start[0]), Number(start[1]), Number(start[2])];
    const p2: Point3 = [Number(end[0]), Number(end[1]), Number(end[2])];
    if (p1.every((v, i) => v === p2[i])) throw new Error('dimension endpoints coincide');
    const delta = [p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2]];
    const length = Math.sqrt(delta[0] * delta[0] + delta[1] * delta[1] + delta[2] * delta[2]);
    const direction: Point3 = [delta[0] / length, delta[1] / length, delta[2] / length];
    let ov: Point3;
    if (!offsetVector) {
      const perp = perpendicular(direction);
      ov = [offset * perp[0], offset * perp[1], offset * perp[2]];
    } else {
      ov = [Number(offsetVector[0]), Number(offsetVector[1]), Number(offsetVector[2])];
      if (ov.every((c) => Math.abs(c) < 1e-12)) throw new Error('dimension offset vector must be non-zero');
    }
    const n = [
      ov[1] * direction[2] - ov[2] * direction[1],
      ov[2] * direction[0] - ov[0] * direction[2],
      ov[0] * direction[1] - ov[1] * direction[0],
    ];
    const nlen = Math.sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2]);
    if (nlen < 1e-9) throw new Error('dimension offset must not be parallel to the segment');
    const normal: Point3 = [n[0] / nlen, n[1] / nlen, n[2] / nlen];
    const offsetScalar = -Math.sqrt(ov[0] * ov[0] + ov[1] * ov[1] + ov[2] * ov[2]);
    let first: Record;
    let second: Record;
    if (anchor) {
      const v1 = this.vertices.get(`${p1[0]},${p1[1]},${p1[2]}`);
      const v2 = this.vertices.get(`${p2[0]},${p2[1]},${p2[2]}`);
      if (v1 === undefined || v2 === undefined) throw new Error('anchored dimension endpoints must be existing vertices');
      first = this.connection(undefined, v1);
      second = this.connection(undefined, v2);
    } else {
      first = this.connection(p1);
      second = this.connection(p2);
    }
    const id = this.template.allocId();
    const item = new Record(T_DIMENSION, undefined, [
      new Record(T_DIMENSION_HEAD, undefined, [
        drawing(id),
        record(T_DIMENSION_HEAD_EMPTY, new Uint8Array(0)),
        record(T_DIMENSION_FONT, varint(this.fontId())),
        record(T_DIMENSION_HEAD_FLAG, u8(0)),
        record(T_DIMENSION_HEAD_ARROW, u32(3)),
      ]),
      new Record(T_DIM_START, undefined, [first]),
      new Record(T_DIM_END, undefined, [second]),
      record(T_DIM_NORMAL, f64s(normal)),
      record(T_DIM_XAXIS, f64s(direction)),
      record(T_DIM_ONE, u32(1)),
      record(T_DIM_ZERO, f64s([0])),
      record(T_DIM_OFFSET, f64s([offsetScalar])),
      record(T_DIM_ARROW, u32(3)),
    ]);
    this.pool(T_DIMENSIONS).children!.push(item);
    this.order.push(id);
    return id;
  }

  addGuideLine(point: ArrayLike<number>, point2?: ArrayLike<number> | null, direction?: ArrayLike<number> | null): number {
    if ((point2 === undefined || point2 === null) === (direction === undefined || direction === null)) throw new Error('add_guide_line: pass exactly one of point2 or direction');
    const p: Point3 = [Number(point[0]), Number(point[1]), Number(point[2])];
    let dirv: Point3;
    let startParam: number;
    let endParam: number;
    if (point2) {
      const p2: Point3 = [Number(point2[0]), Number(point2[1]), Number(point2[2])];
      const delta = [p2[0] - p[0], p2[1] - p[1], p2[2] - p[2]];
      const length = Math.sqrt(delta[0] * delta[0] + delta[1] * delta[1] + delta[2] * delta[2]);
      if (length === 0) throw new Error('guide-line points coincide');
      dirv = [delta[0] / length, delta[1] / length, delta[2] / length];
      startParam = 0;
      endParam = length;
    } else {
      const d = [Number(direction![0]), Number(direction![1]), Number(direction![2])];
      const length = Math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2]);
      if (length === 0) throw new Error('guide-line direction must be non-zero');
      dirv = [d[0] / length, d[1] / length, d[2] / length];
      startParam = -1e30;
      endParam = 1e30;
    }
    const id = this.template.allocId();
    const item = new Record(T_GUIDE_LINE, undefined, [
      new Record(T_ANNOTATION_BASE, undefined, [drawing(id)]),
      record(T_GUIDE_BODY, f64s([...p, ...dirv, startParam, endParam])),
      record(T_GUIDE_STIPPLE, u16(0xffff)),
    ]);
    this.pool(T_GUIDE_LINES).children!.push(item);
    this.order.push(id);
    return id;
  }

  addGuidePoint(position: ArrayLike<number>): number {
    const p: Point3 = [Number(position[0]), Number(position[1]), Number(position[2])];
    const id = this.template.allocId();
    this.pool(T_GUIDE_POINTS).children!.push(new Record(T_GUIDE_POINT, undefined, [
      new Record(T_ANNOTATION_BASE, undefined, [drawing(id)]),
      record(T_GUIDE_POSITION, f64s(p)),
      record(T_GUIDE_ANCHOR, f64s([0, 0, 0])),
      record(T_GUIDE_TAIL, u8(0)),
    ]));
    this.order.push(id);
    this.extendBounds(p);
    return id;
  }

  addSectionPlane(point: ArrayLike<number>, normal: ArrayLike<number>, options: { name?: string; symbol?: string; active?: boolean; hidden?: boolean } = {}): number {
    const p: Point3 = [Number(point[0]), Number(point[1]), Number(point[2])];
    const n = [Number(normal[0]), Number(normal[1]), Number(normal[2])];
    const length = Math.sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2]);
    if (length === 0) throw new Error('section-plane normal must be non-zero');
    const a = n[0] / length;
    const b = n[1] / length;
    const c = n[2] / length;
    const d = -(a * p[0] + b * p[1] + c * p[2]);
    const id = this.template.allocId();
    const flags = 0x06 | (options.hidden ? 0x01 : 0);
    this.pool(T_SECTION_PLANES).children!.push(new Record(T_SECTION, undefined, [
      drawing(id, null, null, flags),
      record(T_SECTION_PLANE, f64s([a, b, c, d])),
      record(T_SECTION_NAME, new TextEncoder().encode(options.name ?? '')),
      record(T_SECTION_SYMBOL, new TextEncoder().encode(options.symbol ?? '')),
    ]));
    this.order.push(id);
    if (options.active) this.activeSection = id;
    return id;
  }

  addCurveEdges(points: ArrayLike<number>[], options: { closed?: boolean; arcFrame?: number[] | null; layer?: number | null; flags?: number } = {}): [number, number[]] {
    if (points.length < 2) throw new Error('a curve needs at least two points');
    const curveId = this.template.allocId();
    const edges: number[] = [];
    const count = options.closed ? points.length : points.length - 1;
    for (let index = 0; index < count; index++) {
      const start = this.addVertex(points[index % points.length]);
      const end = this.addVertex(points[(index + 1) % points.length]);
      if (start === end) throw new Error('a curve has a repeated adjacent point');
      const [edgeId] = this.addEdge(start, end, options.layer ?? null, options.flags ?? 0x06, curveId);
      edges.push(edgeId);
    }
    const curveRecord = new Record(T_CURVE, undefined, [
      entityBase(curveId),
      record(T_CURVE_COUNT, u32(edges.length)),
      record(T_CURVE_FLAG, u8(0)),
      record(T_CURVE_FIRST, varint(edges[0])),
      record(T_CURVE_LAST, varint(edges[edges.length - 1])),
    ]);
    if (options.arcFrame) this.pool(T_ARC_CURVES).children!.push(new Record(T_ARC_CURVE, undefined, [curveRecord, record(T_ARC_FRAME, f64s(options.arcFrame))]));
    else this.pool(T_CURVES).children!.push(curveRecord);
    return [curveId, edges];
  }

  addModelAttributes(dicts: VffAttributeDicts): void {
    if (dicts.length === 0) return;
    const drawingNode = (this.container.children ?? []).find((child) => child.tag === T_DRAWING);
    if (!drawingNode) throw new Error('the model container has no drawing element');
    const entityRecords = parseRecords(drawingNode.payload, [T_ENTITY_BASE, 0x05dd, T_ATTR_LIST, T_ATTR_SET]);
    const entity = entityRecords.find((candidate) => candidate.tag === T_ENTITY_BASE);
    if (!entity?.children) throw new Error('the model drawing element is malformed');
    let attribute = entity.child(0x05dd);
    if (!attribute) {
      attribute = attributeContainer(dicts);
      if (attribute) entity.children.push(attribute);
    } else {
      const list = attribute.child(T_ATTR_LIST);
      const set = list?.child(T_ATTR_SET);
      if (!set?.children) throw new Error('the model attribute set is malformed');
      for (const [name, entries] of dicts) set.children.push(attributeDictionary(name, entries));
    }
    drawingNode.payload = encodeRecords(entityRecords);
  }

  addInstance(definitionContainerId: number, matrix13: number[], options: { definitionBounds?: number[] | null; name?: string; material?: number | null; layer?: number | null; hidden?: boolean; attributeDicts?: VffAttributeDicts } = {}): number {
    const id = this.template.allocId();
    const flags = 0x06 | (options.hidden ? 0x01 : 0);
    this.pool(T_COMPONENTS).children!.push(new Record(T_INSTANCE, undefined, [
      drawing(id, options.material ?? null, options.layer ?? null, flags, attributeContainer(options.attributeDicts)),
      record(T_INSTANCE_NAME, new TextEncoder().encode(options.name ?? '')),
      record(T_INSTANCE_TRANSFORM, f64s(matrix13)),
      record(T_INSTANCE_DEFINITION, varint(definitionContainerId)),
      record(T_INSTANCE_GUID, this.template.guidBytes()),
    ]));
    this.order.push(id);
    if (options.definitionBounds) for (const corner of boxCorners(options.definitionBounds)) this.extendBounds(transformPoint(corner, matrix13));
    return id;
  }

  addGroup(definitionContainerId: number, matrix13: number[], options: { definitionBounds?: number[] | null; name?: string; material?: number | null; layer?: number | null; hidden?: boolean; attributeDicts?: VffAttributeDicts } = {}): number {
    const id = this.template.allocId();
    const flags = 0x06 | (options.hidden ? 0x01 : 0);
    this.pool(T_GROUPS).children!.push(new Record(T_GROUP, undefined, [new Record(T_INSTANCE, undefined, [
      drawing(id, options.material ?? null, options.layer ?? null, flags, attributeContainer(options.attributeDicts)),
      record(T_INSTANCE_NAME, new TextEncoder().encode(options.name ?? '')),
      record(T_INSTANCE_TRANSFORM, f64s(matrix13)),
      record(T_INSTANCE_DEFINITION, varint(definitionContainerId)),
      record(T_INSTANCE_GUID, this.template.guidBytes()),
    ])]));
    this.order.push(id);
    if (options.definitionBounds) for (const corner of boxCorners(options.definitionBounds)) this.extendBounds(transformPoint(corner, matrix13));
    return id;
  }

  finalize(): Record {
    const existing = this.container.children ?? [];
    let drawingNode = existing.find((child) => child.tag === T_DRAWING);
    if (!drawingNode) drawingNode = drawing(this.containerId);
    const children: Record[] = [drawingNode];
    for (const tag of POOL_TAGS) {
      const pool = this.pools.get(tag);
      if (pool) children.push(pool);
    }
    const ahead: Record[] = [];
    const trailing: Record[] = [];
    let existingActive: Record | null = null;
    for (const child of existing) {
      if (POOL_TAGS.includes(child.tag) || [T_DRAWING, T_ORDER, T_FLAGS_CONTAINER, T_BOUNDS, T_ACTIVE_SECTION].includes(child.tag)) {
        if (child.tag === T_ACTIVE_SECTION) existingActive = child;
        continue;
      }
      (child.tag === T_RELATIONSHIPS ? trailing : ahead).push(child);
    }
    children.push(...ahead);
    const merged = [...this.existingOrder, ...this.order.filter((id) => !this.existingOrder.includes(id))];
    if (merged.length) children.push(newIdListTag(merged));
    if (this.activeSection !== null) children.push(record(T_ACTIVE_SECTION, varint(this.activeSection)));
    else if (existingActive) children.push(existingActive);
    children.push(...trailing);
    const flagsRecord = this.container.child(T_FLAGS_CONTAINER);
    if (this.fresh) children.push(record(T_FLAGS_CONTAINER, u8(1)));
    else if (flagsRecord) children.push(flagsRecord);
    const oldBounds = this.container.child(T_BOUNDS);
    if (this.fresh || oldBounds) {
      let bounds = this.bounds;
      if (oldBounds && oldBounds.payload.length === 48) {
        const values = readF64s(oldBounds.payload);
        if (!bounds) bounds = values;
        else bounds = [
          Math.min(values[0], bounds[0]), Math.min(values[1], bounds[1]), Math.min(values[2], bounds[2]),
          Math.max(values[3], bounds[3]), Math.max(values[4], bounds[4]), Math.max(values[5], bounds[5]),
        ];
      }
      children.push(record(T_BOUNDS, f64s(bounds ?? [0, 0, 0, 0, 0, 0])));
    }
    this.container.children = children;
    return this.container;
  }
}

function blankContainer(containerId: number | null, attributes: Record | null): Record {
  return new Record(T_DEFINITION_CONTAINER, undefined, [
    drawing(containerId, null, null, 0x06, attributes),
    new Record(T_EMPTY, undefined, []),
    emptyRelationships(),
    record(T_FLAGS_CONTAINER, u8(0)),
  ]);
}

function perpendicular(direction: ArrayLike<number>): Point3 {
  const z: Point3 = [0, 0, 1];
  const cx = direction[1] * z[2] - direction[2] * z[1];
  const cy = direction[2] * z[0] - direction[0] * z[2];
  const cz = direction[0] * z[1] - direction[1] * z[0];
  const length = Math.sqrt(cx * cx + cy * cy + cz * cz);
  if (length < 1e-9) return [1, 0, 0];
  return [cx / length, cy / length, cz / length];
}

function boxCorners(bounds: number[]): Point3[] {
  const [minX, minY, minZ, maxX, maxY, maxZ] = bounds;
  const corners: Point3[] = [];
  for (const x of [minX, maxX]) for (const y of [minY, maxY]) for (const z of [minZ, maxZ]) corners.push([x, y, z]);
  return corners;
}

function transformPoint(point: ArrayLike<number>, matrix: number[]): Point3 {
  const [x, y, z] = [point[0], point[1], point[2]];
  return [
    matrix[0] * x + matrix[1] * y + matrix[2] * z + matrix[9],
    matrix[3] * x + matrix[4] * y + matrix[5] * z + matrix[10],
    matrix[6] * x + matrix[7] * y + matrix[8] * z + matrix[11],
  ];
}
