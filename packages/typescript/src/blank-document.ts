/**
 * Construct an empty classic-format (SketchUp 2017, v17) `.skp` document in code.
 *
 * Every record of the empty MFC `CArchive` document is written field by field
 * through named builder methods and named constants — no bundled binary file,
 * hex blob, or compressed template is read or embedded. As each section is
 * emitted, the builder tracks the 1-based MFC store-map slots and byte offsets
 * that `create.ts` splices into or patches when writing geometry, materials,
 * layers, component definitions, scenes, and document options.
 */

export type BlankPoint3 = readonly [number, number, number];

export const SCAFFOLD_SHA256 = '809a1ab73a20a192ab13aaff197afb1c67d0e9352f6a353a9cd8030919f8a6c3';

// MFC CArchive UTF-16LE string prefix marker.
const UTF16_STRING_MARKER = [0xff, 0xfe, 0xff] as const;

// CAttributeNamed value-type tags used in OptionsManager providers and ModelProperties.
const ATTR_INT32 = 0x04;
const ATTR_DOUBLE = 0x06;
const ATTR_BOOL = 0x07;

// CSkpStyle typed-property value tags.
const STYLE_BOOL = 1;
const STYLE_UINT32 = 4;
const STYLE_FLOAT32 = 6;
const STYLE_FLOAT64 = 7;

// File-header defaults matching the SketchUp 2017 blank document.
const FILE_MAGIC_TEXT = 'SketchUp Model';
const VERSION_STRING_V17 = '{17.0.1}';
const DEFAULT_MODEL_GUID = '3093d39a-aa69-4e29-8d53-15cbfe3d94b0';
const DEFAULT_SAVE_TIMESTAMP_UNIX = 1786620315; // 2026-08-13T11:25:15Z (time_t)
const UTF16LE_CODEPAGE = 1200; // Windows UTF-16LE code page (0x04B0)
const INITIAL_NEXT_PERSISTENT_ID = 18;

// Persistent entity IDs assigned in the empty document.
const SAVED_STYLE_PID = 1;
const DIM_STYLE_FONT_PID = 2;
const TEXT_STYLE_FONT_PID = 3;
const LAYER0_PID = 4;
const LAYER0_MATERIAL_PID = 5;

// Saved and working CSkpStyle GUIDs in the empty document.
const DEFAULT_SAVED_STYLE_GUID = '94fee66b-2dce-4b79-ada7-4be56f9266a2';
const DEFAULT_WORKING_STYLE_GUID = '667aac7a-d15a-409d-9d21-24ffa8045e61';

// Default shadow / geo-location settings ("Boulder (CO)", "USA", summer solstice).
const DEFAULT_SHADOW_TIME_UNIX = 1782048600; // 2026-06-21T13:30:00Z
const DEFAULT_CITY = 'Boulder (CO)';
const DEFAULT_COUNTRY = 'USA';
const DEFAULT_LONGITUDE = -105.283;
const DEFAULT_LATITUDE = 40.017;
const DEFAULT_TZ_OFFSET = -7.0;
const DEFAULT_SHADOW_LIGHT = 80;
const DEFAULT_SHADOW_DARK = 20;

/** SketchUp 2017 `CVersionMap` schema table (alphabetical class name -> schema version). */
export const VERSION_MAP_V17: ReadonlyArray<readonly [string, number]> = [
  ['CArcCurve', 3],
  ['CAttribute', 0],
  ['CAttributeContainer', 0],
  ['CAttributeNamed', 1],
  ['CBackgroundImage', 10],
  ['CCamera', 5],
  ['CComponent', 11],
  ['CComponentBehavior', 5],
  ['CComponentDefinition', 10],
  ['CComponentInstance', 5],
  ['CConstructionGeometry', 0],
  ['CConstructionLine', 1],
  ['CConstructionPoint', 0],
  ['CCurve', 4],
  ['CDefinitionList', 0],
  ['CDib', 3],
  ['CDimension', 1],
  ['CDimensionLinear', 6],
  ['CDimensionRadial', 2],
  ['CDimensionStyle', 4],
  ['CDrawingElement', 9],
  ['CEdge', 2],
  ['CEdgeUse', 1],
  ['CEntity', 5],
  ['CFace', 3],
  ['CFaceTextureCoords', 4],
  ['CFontManager', 0],
  ['CGroup', 1],
  ['CImage', 1],
  ['CLayer', 2],
  ['CLayerManager', 4],
  ['CLoop', 1],
  ['CMaterial', 12],
  ['CMaterialManager', 4],
  ['CPageList', 1],
  ['CPolyline3d', 0],
  ['CRelationship', 0],
  ['CRelationshipMap', 0],
  ['CRenderingOptions', 36],
  ['CSchemaFile', 1],
  ['CSchemaFilterFile', 0],
  ['CSchemaZipFile', 1],
  ['CSectionPlane', 2],
  ['CShadowInfo', 7],
  ['CSkFont', 1],
  ['CSketchCS', 0],
  ['CSketchUpModel', 26],
  ['CSketchUpPage', 1],
  ['CSkpStyle', 1],
  ['CSkpStyleManager', 2],
  ['CText', 9],
  ['CTextStyle', 5],
  ['CTexture', 6],
  ['CThumbnail', 1],
  ['CVertex', 0],
  ['CViewPage', 12],
  ['CWatermark', 1],
  ['CWatermarkManager', 2],
];

const VERSION_MAP_SENTINEL = 'End-Of-Version-Map';

function packAbgr(r: number, g: number, b: number, a = 255): number {
  return ((r & 0xff) | ((g & 0xff) << 8) | ((b & 0xff) << 16) | ((a & 0xff) << 24)) >>> 0;
}

export function uuidToBytesLe(canonicalGuid: string): Uint8Array {
  const hex = canonicalGuid.replace(/-/g, '');
  const raw = new Uint8Array(16);
  for (let i = 0; i < 16; i++) {
    raw[i] = parseInt(hex.slice(2 * i, 2 * i + 2), 16);
  }
  const order = [3, 2, 1, 0, 5, 4, 7, 6, 8, 9, 10, 11, 12, 13, 14, 15];
  const out = new Uint8Array(16);
  for (let i = 0; i < 16; i++) {
    out[i] = raw[order[i]];
  }
  return out;
}

function encodeUtf16Str(text: string): Uint8Array {
  const codeUnits = text.length;
  let headerLen = 4;
  if (codeUnits >= 0xffff) {
    headerLen = 10;
  } else if (codeUnits >= 0xff) {
    headerLen = 6;
  }
  const out = new Uint8Array(headerLen + codeUnits * 2);
  out[0] = UTF16_STRING_MARKER[0];
  out[1] = UTF16_STRING_MARKER[1];
  out[2] = UTF16_STRING_MARKER[2];
  let pos = 3;
  if (codeUnits < 0xff) {
    out[pos++] = codeUnits;
  } else if (codeUnits < 0xffff) {
    out[pos++] = 0xff;
    out[pos++] = codeUnits & 0xff;
    out[pos++] = (codeUnits >>> 8) & 0xff;
  } else {
    out[pos++] = 0xff;
    out[pos++] = 0xff;
    out[pos++] = 0xff;
    out[pos++] = codeUnits & 0xff;
    out[pos++] = (codeUnits >>> 8) & 0xff;
    out[pos++] = (codeUnits >>> 16) & 0xff;
    out[pos++] = (codeUnits >>> 24) & 0xff;
  }
  for (let i = 0; i < codeUnits; i++) {
    const cu = text.charCodeAt(i);
    out[pos++] = cu & 0xff;
    out[pos++] = (cu >>> 8) & 0xff;
  }
  return out;
}

export interface PackCameraBodyOptions {
  eye: BlankPoint3;
  target: BlankPoint3;
  up: BlankPoint3;
  clipNear?: number;
  clipFar?: number;
  perspective?: boolean;
  fov?: number;
  orthoHeight?: number;
  windowParams?: BlankPoint3;
  aspectRatio?: number;
  fovIsHeight?: boolean;
  cameraFlag?: number;
  description?: string;
  imageWidth?: number;
  is2d?: boolean;
  scale2d?: number;
  center2d?: readonly [number, number];
}

/**
 * Serialize the body of a `CCamera` (schema 5) object after its MFC tag.
 * With an empty `description` string this body is 176 bytes.
 */
export function packCameraBody(opts: PackCameraBodyOptions): Uint8Array {
  const clipNear = opts.clipNear ?? 1.0;
  const clipFar = opts.clipFar ?? 1000.0;
  const perspective = opts.perspective ?? true;
  const fov = opts.fov ?? 30.0;
  const orthoHeight = opts.orthoHeight ?? 133.0;
  const windowParams = opts.windowParams ?? [0.0, 0.0, 0.0];
  const aspectRatio = opts.aspectRatio ?? 0.0;
  const fovIsHeight = opts.fovIsHeight ?? true;
  const cameraFlag = opts.cameraFlag ?? 0;
  const description = opts.description ?? '';
  const imageWidth = opts.imageWidth ?? 0.0;
  const is2d = opts.is2d ?? false;
  const scale2d = opts.scale2d ?? 1.0;
  const center2d = opts.center2d ?? [0.0, 0.0];

  const descBytes = encodeUtf16Str(description);
  const out = new Uint8Array(139 + descBytes.length + 33);
  const dv = new DataView(out.buffer);
  let p = 0;
  const putF64 = (v: number): void => {
    dv.setFloat64(p, v, true);
    p += 8;
  };
  putF64(opts.eye[0]);
  putF64(opts.eye[1]);
  putF64(opts.eye[2]);
  putF64(opts.target[0]);
  putF64(opts.target[1]);
  putF64(opts.target[2]);
  putF64(opts.up[0]);
  putF64(opts.up[1]);
  putF64(opts.up[2]);
  putF64(clipNear);
  putF64(clipFar);
  out[p++] = perspective ? 1 : 0;
  putF64(fov);
  putF64(orthoHeight);
  putF64(windowParams[0]);
  putF64(windowParams[1]);
  putF64(windowParams[2]);
  putF64(aspectRatio);
  out[p++] = fovIsHeight ? 1 : 0;
  out[p++] = cameraFlag & 0xff;
  out.set(descBytes, p);
  p += descBytes.length;
  putF64(imageWidth);
  out[p++] = is2d ? 1 : 0;
  putF64(scale2d);
  putF64(center2d[0]);
  putF64(center2d[1]);
  return out;
}

type StylePropertyDef = readonly [number, number | null, number];

// Default 56-entry CSkpStyle property table: [keyId, valueKind, value].
const DEFAULT_STYLE_PROPERTIES: ReadonlyArray<StylePropertyDef> = [
  [1000, STYLE_UINT32, 1],                         // EdgeDisplayMode: show edges
  [1001, STYLE_UINT32, 0],                         // EdgeType: standard strokes
  [1002, STYLE_UINT32, 1],                         // EdgeColorMode: all same
  [1004, STYLE_BOOL, 0],                           // ExtendLines: false
  [1005, STYLE_UINT32, 8],                         // LineExtension: 8 px
  [1006, STYLE_BOOL, 1],                           // DrawSilhouettes (Profiles): true
  [1007, STYLE_UINT32, 3],                         // SilhouetteWidth: 3 px
  [1008, STYLE_BOOL, 0],                           // DrawDepthQue: false
  [1009, STYLE_UINT32, 4],                         // DepthQueWidth: 4 px
  [1010, STYLE_BOOL, 0],                           // DrawLineEnds (Endpoints): false
  [1011, STYLE_UINT32, 5],                         // LineEndWidth: 5 px
  [1012, STYLE_BOOL, 0],                           // JitterEdges: false
  [1014, STYLE_UINT32, packAbgr(0, 0, 0, 255)],    // ForegroundColor: black
  [1015, STYLE_BOOL, 0],                           // DrawBackEdges: false
  [1016, STYLE_BOOL, 1],                           // Edge halo / stroke flag: true
  [2001, STYLE_UINT32, 2],                         // RenderMode: ShadedWithTextures
  [2002, STYLE_UINT32, packAbgr(225, 225, 200, 255)], // FaceFrontColor
  [2003, STYLE_UINT32, packAbgr(128, 128, 200, 255)], // FaceBackColor
  [2004, STYLE_BOOL, 0],                           // ModelTransparency (X-ray): false
  [2005, STYLE_BOOL, 1],                           // MaterialTransparency: true
  [2006, STYLE_UINT32, 0],                         // TransparencySort: faster (0)
  [2007, STYLE_BOOL, 1],                           // Texture: true
  [2008, STYLE_FLOAT64, 0.65],                     // Transparency factor: 0.65
  [4000, STYLE_UINT32, packAbgr(255, 255, 255, 255)], // BackgroundColor: white
  [4001, STYLE_UINT32, packAbgr(140, 165, 255, 255)], // SkyColor
  [4002, STYLE_BOOL, 0],                           // DrawHorizon (Sky): false
  [4003, STYLE_UINT32, packAbgr(63, 147, 63, 255)],   // GroundColor
  [4004, STYLE_BOOL, 0],                           // DrawGround: false
  [4005, STYLE_UINT32, 50],                        // GroundTransparency: 50
  [4006, STYLE_BOOL, 1],                           // DrawUnderground: true
  [4007, STYLE_UINT32, packAbgr(0, 0, 0, 0)],      // HorizonColor: transparent black
  [5000, STYLE_BOOL, 1],                           // DisplayWatermarks: true
  [5001, null, 0],                                 // Watermarks list: empty (count = 0)
  [7000, STYLE_UINT32, packAbgr(255, 255, 0, 255)],   // HighlightColor: yellow
  [7001, STYLE_UINT32, packAbgr(255, 0, 0, 255)],     // LockedColor: red
  [7002, STYLE_UINT32, packAbgr(128, 128, 128, 255)], // ConstructionColor: grey
  [7003, STYLE_UINT32, packAbgr(102, 110, 141, 255)], // SectionActiveColor
  [7004, STYLE_UINT32, packAbgr(192, 192, 192, 255)], // SectionInactiveColor
  [7005, STYLE_UINT32, packAbgr(255, 0, 0, 255)],     // SectionDefaultCutColor
  [7008, STYLE_BOOL, 1],                           // DisplaySketchAxes: true
  [7010, STYLE_BOOL, 0],                           // DrawHidden: false
  [7011, STYLE_BOOL, 0],                           // DisplayColorByLayer: false
  [7012, STYLE_BOOL, 0],                           // HideConstructionGeometry: false
  [7013, STYLE_UINT32, 3],                         // SectionDisplayMode: planes (1) | cuts (2)
  [7014, STYLE_UINT32, 4],                         // SectionCutWidth: 4
  [7015, STYLE_BOOL, 0],                           // SectionCutFilled: false
  [7016, STYLE_UINT32, packAbgr(0, 0, 0, 255)],    // SectionDefaultFillColor: black
  [7017, STYLE_BOOL, 0],                           // DrawHiddenGeometry: false
  [7018, STYLE_BOOL, 0],                           // DrawHiddenObjects: false
  [8000, STYLE_BOOL, 1],                           // Match Photo background image: true
  [8001, STYLE_FLOAT64, 1.0],                      // Match Photo background opacity: 1.0
  [8002, STYLE_BOOL, 1],                           // Match Photo foreground photo: true
  [8003, STYLE_FLOAT64, 0.8],                      // Match Photo foreground opacity: 0.8
  [8100, STYLE_BOOL, 0],                           // AmbientOcclusion: false
  [8102, STYLE_FLOAT32, 10.0],                     // AmbientOcclusionDistance: 10.0
  [8103, STYLE_FLOAT32, 0.0],                      // AmbientOcclusionIntensity: 0.0
];

/**
 * Serialized empty v17 `.skp` document together with every named splice
 * position, field offset, length, and MFC store-map slot `create.ts` needs.
 */
export interface BlankDocument {
  readonly data: Uint8Array;
  readonly modelGuidOffset: number;
  readonly pidCounterOffset: number;
  readonly nextPid: number;
  readonly noScaleMaskOffset: number;
  readonly unitOptionOffsets: Readonly<Record<string, number>>;
  readonly attrContainerSlot: number;
  readonly attributeNamedSlot: number;
  readonly ccameraSlot: number;
  readonly cameraBodyOffset: number;
  readonly cameraBodyLen: number;
  readonly renderingOptionsOffset: number;
  readonly renderingOptionsLen: number;
  readonly renderingOptionFields: ReadonlyArray<readonly [number, number, boolean]>;
  readonly materialCountPos: number;
  readonly materialInsertPos: number;
  readonly materialBaseSlot: number;
  readonly layerCountPos: number;
  readonly origLayerCount: number;
  readonly layerInsertPos: number;
  readonly layerWriterBaseSlot: number;
  readonly activeLayerRefPos: number;
  readonly defCountPos: number;
  readonly origDefCount: number;
  readonly rootCountPos: number;
  readonly origRootCount: number;
  readonly tailPos: number;
  readonly scaffoldNextSlot: number;
  readonly scaffoldClassSlot: Readonly<Record<string, number>>;
  readonly shadowInfoTailRel: number;
  readonly shadowInfoLen: number;
  readonly pageListTailRel: number;
  readonly fontTailRefs: readonly number[];
  readonly styleObjectTailRel: number;
  readonly styleTailRefs: readonly [number, number];
  readonly tailRefPositions: readonly number[];
  readonly savedStyleGuidTailRel: number;
  readonly workingStyleGuidTailRel: number;
  readonly savedStyleItemsTailRel: number;
  readonly workingStyleItemsTailRel: number;
  readonly styleItemCount: number;
}

class BlankArchiveBuilder {
  private buf = new Uint8Array(8192);
  private len = 0;
  private scratch = new DataView(new ArrayBuffer(8));
  nextSlot = 1;
  readonly classSlot: Record<string, number> = {};

  get position(): number {
    return this.len;
  }

  toUint8Array(): Uint8Array {
    return this.buf.slice(0, this.len);
  }

  private reserve(extra: number): void {
    const needed = this.len + extra;
    if (needed <= this.buf.length) return;
    let cap = this.buf.length * 2;
    while (cap < needed) cap *= 2;
    const next = new Uint8Array(cap);
    next.set(this.buf.subarray(0, this.len));
    this.buf = next;
  }

  private appendBytes(src: ArrayLike<number>): void {
    this.reserve(src.length);
    for (let i = 0; i < src.length; i++) {
      this.buf[this.len++] = src[i] & 0xff;
    }
  }

  private u8(value: number): void {
    this.reserve(1);
    this.buf[this.len++] = value & 0xff;
  }

  private u16(value: number): void {
    this.reserve(2);
    this.buf[this.len++] = value & 0xff;
    this.buf[this.len++] = (value >>> 8) & 0xff;
  }

  private u32(value: number): void {
    this.reserve(4);
    const v = value >>> 0;
    this.buf[this.len++] = v & 0xff;
    this.buf[this.len++] = (v >>> 8) & 0xff;
    this.buf[this.len++] = (v >>> 16) & 0xff;
    this.buf[this.len++] = (v >>> 24) & 0xff;
  }

  private i32(value: number): void {
    this.u32(value >>> 0);
  }

  private u64(value: number): void {
    this.u32(value >>> 0);
    this.u32(Math.floor(value / 0x100000000) >>> 0);
  }

  private f32(value: number): void {
    this.scratch.setFloat32(0, value, true);
    this.reserve(4);
    for (let i = 0; i < 4; i++) this.buf[this.len++] = this.scratch.getUint8(i);
  }

  private f64(value: number): void {
    this.scratch.setFloat64(0, value, true);
    this.reserve(8);
    for (let i = 0; i < 8; i++) this.buf[this.len++] = this.scratch.getUint8(i);
  }

  private rgba(r: number, g: number, b: number, a = 255): void {
    this.reserve(4);
    this.buf[this.len++] = r & 0xff;
    this.buf[this.len++] = g & 0xff;
    this.buf[this.len++] = b & 0xff;
    this.buf[this.len++] = a & 0xff;
  }

  private uuid(canonicalGuid: string): void {
    this.appendBytes(uuidToBytesLe(canonicalGuid));
  }

  private str(text: string): void {
    this.appendBytes(encodeUtf16Str(text));
  }

  private zeros(count: number): void {
    this.reserve(count);
    this.buf.fill(0, this.len, this.len + count);
    this.len += count;
  }

  private allocSlot(): number {
    const slot = this.nextSlot;
    this.nextSlot += 1;
    return slot;
  }

  private nullRef(): void {
    this.u16(0);
  }

  private backref(slot: number): void {
    this.u16(slot);
  }

  private newObject(className: string, schema: number): number {
    const existing = this.classSlot[className];
    if (existing === undefined) {
      this.u16(0xffff);
      this.u16(schema);
      this.u16(className.length);
      this.reserve(className.length);
      for (let i = 0; i < className.length; i++) {
        this.buf[this.len++] = className.charCodeAt(i) & 0x7f;
      }
      this.classSlot[className] = this.allocSlot();
      return this.allocSlot();
    }
    this.u16(0x8000 | existing);
    return this.allocSlot();
  }

  private encodePid(pid: number): void {
    let mask = 0;
    const payload: number[] = [];
    let rem = pid;
    for (let bit = 0; bit < 8; bit++) {
      const byteVal = rem & 0xff;
      rem = Math.floor(rem / 256);
      if (byteVal !== 0) {
        mask |= 1 << bit;
        payload.push(byteVal);
      }
    }
    this.u8(mask);
    this.appendBytes(payload);
  }

  private entityPreamble(pid = 0): void {
    this.nullRef();
    this.encodePid(pid);
  }

  private writeAttrEntry(key: string, tag: number, value: number | boolean): number {
    this.str(key);
    this.u8(tag);
    const valOffset = this.len;
    if (tag === ATTR_BOOL) {
      this.u8(value ? 1 : 0);
    } else if (tag === ATTR_INT32) {
      this.i32(Number(value));
    } else if (tag === ATTR_DOUBLE) {
      this.f64(Number(value));
    } else {
      throw new Error(`unsupported attribute tag 0x${tag.toString(16)}`);
    }
    return valOffset;
  }

  // ── document sections ────────────────────────────────────────────────────

  writeFileHeader(): number {
    this.str(FILE_MAGIC_TEXT);
    this.str(VERSION_STRING_V17);
    const modelGuidOffset = this.len;
    this.uuid(DEFAULT_MODEL_GUID);
    this.str(''); // empty release/locale note string
    this.u32(DEFAULT_SAVE_TIMESTAMP_UNIX);
    return modelGuidOffset;
  }

  writeVersionMap(): void {
    this.newObject('CVersionMap', 0);
    for (const [clsName, schemaVer] of VERSION_MAP_V17) {
      this.str(clsName);
      this.u32(schemaVer);
    }
    this.str(VERSION_MAP_SENTINEL);
  }

  writeModelPreamble(): [number, number] {
    this.u32(0); // model header word 0
    this.u32(1); // model header format flag (1 in v17+)
    this.u32(UTF16LE_CODEPAGE);
    this.u32(0); // preview thumbnail status (0 = none)
    const pidCounterOffset = this.len;
    this.u64(INITIAL_NEXT_PERSISTENT_ID);
    this.nullRef(); // null model preview CDib pointer
    this.u32(1); // post-thumbnail model flag
    this.zeros(7); // reserved pre-behavior model header bytes (all 0)
    const noScaleMaskOffset = this.len;
    this.u32(0); // root CComponentBehavior.no_scale_mask = 0
    this.str(''); // model description (SUModelGetDescription)
    this.u32(3); // options-manager format version
    return [pidCounterOffset, noScaleMaskOffset];
  }

  writeOptionsProviders(): Record<string, number> {
    this.u32(4); // four named SUOptionsProvider tables follow

    // 1. NamedOptions (empty)
    this.str('NamedOptions');
    this.str('');

    // 2. PageOptions
    this.str('PageOptions');
    this.writeAttrEntry('ShowTransition', ATTR_BOOL, true);
    this.writeAttrEntry('TransitionTime', ATTR_DOUBLE, 1.5);
    this.str('');

    // 3. SlideshowOptions
    this.str('SlideshowOptions');
    this.writeAttrEntry('LoopSlideshow', ATTR_BOOL, true);
    this.writeAttrEntry('SlideTime', ATTR_DOUBLE, 1.0);
    this.str('');

    // 4. UnitsOptions
    this.str('UnitsOptions');
    const unitOffsets: Record<string, number> = {};
    unitOffsets.LengthPrecision = this.writeAttrEntry('LengthPrecision', ATTR_INT32, 4);
    unitOffsets.LengthFormat = this.writeAttrEntry('LengthFormat', ATTR_INT32, 1); // 1 = Architectural
    unitOffsets.LengthUnit = this.writeAttrEntry('LengthUnit', ATTR_INT32, 0); // 0 = Inches
    this.writeAttrEntry('LengthSnapEnabled', ATTR_BOOL, true);
    this.writeAttrEntry('LengthSnapLength', ATTR_DOUBLE, 0.0625); // 1/16 inch
    this.writeAttrEntry('AnglePrecision', ATTR_INT32, 1);
    this.writeAttrEntry('AngleSnapEnabled', ATTR_BOOL, true);
    this.writeAttrEntry('SnapAngle', ATTR_DOUBLE, 15.0);
    this.writeAttrEntry('SuppressUnitsDisplay', ATTR_BOOL, false);
    this.writeAttrEntry('ForceInchDisplay', ATTR_BOOL, false);
    unitOffsets.AreaUnit = this.writeAttrEntry('AreaUnit', ATTR_INT32, 0);
    unitOffsets.VolumeUnit = this.writeAttrEntry('VolumeUnit', ATTR_INT32, 0);
    this.writeAttrEntry('AreaPrecision', ATTR_INT32, 2);
    this.writeAttrEntry('VolumePrecision', ATTR_INT32, 2);
    this.str('');
    return unitOffsets;
  }

  writeModelProperties(): void {
    // CAttributeContainer holding one CAttributeNamed("ModelProperties")
    this.newObject('CAttributeContainer', 0);
    this.entityPreamble(0);

    this.newObject('CAttributeNamed', 1);
    this.entityPreamble(0);
    this.u32(0); // CAttribute flags
    this.str('ModelProperties');
    this.writeAttrEntry('IsClassified', ATTR_BOOL, false);
    this.writeAttrEntry('IsDynamic', ATTR_BOOL, false);
    this.writeAttrEntry('IsLive', ATTR_BOOL, false);
    this.str(''); // end of CAttributeNamed entries

    this.nullRef(); // end of CAttributeContainer children
    this.zeros(6); // three null u16 fields before model CCamera
  }

  writeModelCamera(): [number, number] {
    this.newObject('CCamera', 5);
    const bodyOffset = this.len;
    const body = packCameraBody({
      eye: [0.0, 0.0, 500.0],
      target: [0.0, 0.0, 0.0],
      up: [0.0, 1.0, 0.0],
      clipNear: 1.0,
      clipFar: 1000.0,
      perspective: true,
      fov: 30.0,
      orthoHeight: 133.0,
    });
    this.appendBytes(body);
    return [bodyOffset, body.length];
  }

  writeRenderingOptions(): [number, number, Array<readonly [number, number, boolean]>] {
    this.entityPreamble(0); // CRenderingOptions CEntity preamble
    const roStart = this.len;
    const fields: Array<[number, number, boolean]> = [];

    const roU8 = (val: number, styleItem?: number): void => {
      if (styleItem !== undefined) fields.push([styleItem, this.len, true]);
      this.u8(val);
    };
    const roU32 = (val: number, styleItem?: number): void => {
      if (styleItem !== undefined) fields.push([styleItem, this.len, false]);
      this.u32(val);
    };
    const roRgba = (r: number, g: number, b: number, a: number, styleItem?: number): void => {
      if (styleItem !== undefined) fields.push([styleItem, this.len, false]);
      this.rgba(r, g, b, a);
    };

    roU32(2, 2001);                         // RenderMode: ShadedWithTextures
    roU8(0, 2004);                          // ModelTransparency (X-ray)
    roU8(1, 2005);                          // MaterialTransparency
    roU8(0, 1012);                          // JitterEdges
    roU32(1, 1000);                         // EdgeDisplayMode: show edges
    roRgba(0, 0, 0, 0, 4000);               // BackgroundColor slot (unset until style sync)
    roRgba(0, 0, 0, 0, 1014);               // ForegroundColor slot (unset until style sync)
    roRgba(255, 255, 0, 255, 7000);         // HighlightColor
    roRgba(128, 128, 128, 255, 7002);       // ConstructionColor
    roU8(0);                                // DisplayShadows
    roU8(0);                                // DisplayInstanceAxes
    roU8(0, 7011);                          // DisplayColorByLayer (Color by Tag)
    roU8(1, 2007);                          // Texture
    roU32(1, 1002);                         // EdgeColorMode: all same
    roU8(0, 1004);                          // ExtendLines
    roU32(8, 1005);                         // LineExtension: 8 px
    roU8(1, 1006);                          // DrawSilhouettes (Profiles)
    roU32(3, 1007);                         // SilhouetteWidth: 3 px
    roU8(0, 1008);                          // DrawDepthQue
    roU32(4, 1009);                         // DepthQueWidth: 4 px
    roU8(0, 1010);                          // DrawLineEnds (Endpoints)
    roU32(5, 1011);                         // LineEndWidth: 5 px
    roU8(0);                                // DrawProfilesOnly
    roU8(0, 7010);                          // DrawHidden
    this.u32(0);                            // reserved rendering-options word (0)
    roRgba(225, 225, 200, 255, 2002);       // FaceFrontColor
    roRgba(128, 128, 200, 255, 2003);       // FaceBackColor
    this.f64(0.75);                         // InactiveFade
    this.f64(0.5);                          // InstanceFade
    roU8(0);                                // InactiveHidden
    roU8(0);                                // InstanceHidden
    roU8(0);                                // DisplayFog
    roRgba(0, 0, 0, 0);                     // FogColor
    roU8(1);                                // FogUseBkColor
    this.f64(-1.0);                         // FogStartDist (-1 = automatic)
    this.f64(-1.0);                         // FogEndDist (-1 = automatic)
    this.u32(1);                            // fog / stroke mode constant (1)
    roU32(0, 1001);                         // EdgeType: standard strokes
    roU8(1, 7008);                          // DisplaySketchAxes
    roU8(1);                                // DisplayText
    roU8(1);                                // DisplayDims
    roU8(0, 7012);                          // HideConstructionGeometry
    roRgba(140, 165, 255, 255, 4001);       // SkyColor
    roRgba(0, 0, 0, 0, 4007);               // HorizonColor
    roRgba(63, 147, 63, 255, 4003);         // GroundColor
    roU8(0, 4002);                          // DrawHorizon
    roU8(0, 4004);                          // DrawGround
    roU8(1, 4006);                          // DrawUnderground
    roU32(50, 4005);                        // GroundTransparency: 50
    roRgba(102, 110, 141, 255, 7003);       // SectionActiveColor
    roRgba(192, 192, 192, 255, 7004);       // SectionInactiveColor
    roRgba(255, 0, 0, 255, 7005);           // SectionDefaultCutColor
    roU32(4, 7014);                         // SectionCutWidth: 4
    roU32(3);                               // SectionDisplayMode: planes (1) | cuts (2)
    roU32(0, 2006);                         // TransparencySort: faster (0)
    roU8(1);                                // edge halo flag (style item 1016)
    this.f64(0.34906585039886595);          // 20 deg in radians (crease / halo angle)
    roU8(1);                                // ShowViewName: true
    roRgba(255, 0, 0, 255, 7001);           // LockedColor
    roU8(1, 5000);                          // DisplayWatermarks
    this.f64(0.65);                         // transparency factor (style item 2008)
    roU8(0, 1015);                          // DrawBackEdges
    roU8(1);                                // Match Photo background image (item 8000)
    this.f64(1.0);                          // Match Photo background opacity (item 8001)
    roU8(1);                                // Match Photo foreground photo (item 8002)
    this.f64(0.8);                          // Match Photo foreground opacity (item 8003)

    const roLen = this.len - roStart;
    fields.sort((a, b) => a[0] - b[0]);
    return [roStart, roLen, fields];
  }

  writeComponentAndMaterialManager(): [number, number, number] {
    this.u32(0); // post-rendering-options word 0
    this.u32(1); // post-rendering-options word 1
    this.entityPreamble(0); // CSketchUpModel CEntity preamble (null model attributes)
    this.zeros(3); // CComponent base header pad (offsets 0..2)
    this.u8(1); // CComponent base flag 0
    this.u8(1); // CComponent base flag 1
    this.entityPreamble(0); // CMaterialManager CEntity preamble
    this.zeros(5); // CMaterialManager header pad
    const materialCountPos = this.len;
    this.u32(0); // material_count = 0
    const materialInsertPos = this.len;
    const materialBaseSlot = this.nextSlot;
    return [materialCountPos, materialInsertPos, materialBaseSlot];
  }

  writeLayerManager(): [number, number, number, number, number, Record<string, number>] {
    this.nullRef(); // active material ref (null = default unpainted)
    this.entityPreamble(0); // CLayerManager CEntity preamble
    const layerCountPos = this.len;
    const origLayerCount = 1;
    this.u32(origLayerCount);

    // Default untagged layer "Layer0" (schema 3, layer pid=4, material pid=5)
    const layer0Slot = this.newObject('CLayer', 3);
    const middleClassSlot: Record<string, number> = { CLayer: this.classSlot.CLayer };
    this.entityPreamble(LAYER0_PID);
    this.str('Layer0');
    this.u8(0); // hidden = False
    this.u16(0); // layer flags = 0
    this.encodePid(LAYER0_MATERIAL_PID);
    this.str('Layer_Layer0');
    this.u16(0x0100); // embedded layer-colour material flag (256)
    this.rgba(255, 84, 84, 255); // default Layer0 tag colour
    this.str(''); // empty texture path
    this.u32(0); // colorize mode = 0
    this.u32(0); // colorize flags = 0
    this.f64(0.5); // default layer material alpha/transparency
    this.u8(0); // use_opacity = False
    this.u32(0); // layer trailer word = 0

    const layerInsertPos = this.len;
    const layerWriterBaseSlot = this.nextSlot;
    const activeLayerRefPos = this.len;
    this.backref(layer0Slot); // active layer back-ref -> Layer0
    return [
      layerCountPos,
      origLayerCount,
      layerInsertPos,
      layerWriterBaseSlot,
      activeLayerRefPos,
      middleClassSlot,
    ];
  }

  writeDefinitionAndRootLists(): [number, number, number, number, number, number] {
    const defCountPos = this.len;
    const origDefCount = 0;
    this.u32(origDefCount);

    const rootCountPos = this.len;
    const origRootCount = 0;
    this.u32(origRootCount);

    const tailPos = this.len;
    const scaffoldNextSlot = this.nextSlot;
    return [defCountPos, origDefCount, rootCountPos, origRootCount, tailPos, scaffoldNextSlot];
  }

  writeShadowInfo(tailPos: number): [number, number, number] {
    this.u32(0); // root CRelationship list count = 0
    this.u16(0); // root component closing word = 0
    this.entityPreamble(0); // CShadowInfo CEntity preamble

    const shadowStart = this.len;
    this.u32(DEFAULT_SHADOW_TIME_UNIX);
    this.u8(0); // DaylightSavings = False
    this.str(DEFAULT_CITY);
    this.str(DEFAULT_COUNTRY);
    this.f64(DEFAULT_LONGITUDE);
    this.f64(DEFAULT_LATITUDE);
    this.f64(DEFAULT_TZ_OFFSET);
    this.f64(0.0); // NorthAngle (degrees / sin = 0.0)
    this.f64(1.0); // North direction Y = cos(0) = 1.0
    this.f64(0.0); // North direction Z = 0.0
    this.u8(0); // DisplayNorth = False
    this.u8(1); // DisplayOnAllFaces = True
    this.u8(1); // DisplayOnGroundPlane = True
    this.u8(0); // EdgesCastShadows = False
    this.u32(DEFAULT_SHADOW_LIGHT);
    this.u32(DEFAULT_SHADOW_DARK);
    const shadowLen = this.len - shadowStart;

    const pageListTailRel = this.len - tailPos;
    this.u8(0); // UseSunForAllShading = False
    return [shadowStart - tailPos, shadowLen, pageListTailRel];
  }

  writePageList(): void {
    this.entityPreamble(0); // CPageList CEntity preamble
    this.u32(0); // page_count = 0
    this.nullRef(); // selected_page_ref = null
  }

  writeModelAxes(): void {
    this.zeros(3); // page-list closing bytes (0)
    this.entityPreamble(0); // CSketchCS CEntity preamble
    this.u8(1); // axis flag 0
    this.u8(1); // axis flag 1
    this.zeros(5); // axis header pad
    this.f64(0.0); // origin.x
    this.f64(0.0); // origin.y
    this.f64(0.0); // origin.z
    this.f64(1.0); // x_axis.x
    this.f64(0.0); // x_axis.y
    this.f64(0.0); // x_axis.z
    this.f64(0.0); // y_axis.x
    this.f64(1.0); // y_axis.y
    this.f64(0.0); // y_axis.z
    this.f64(0.0); // z_axis.x
    this.f64(0.0); // z_axis.y
    this.f64(1.0); // z_axis.z
    this.zeros(68); // empty post-axes model tables before CDimensionStyle
  }

  private writeSkFont(faceName: string, pointSize: number, pid: number): number {
    const slot = this.newObject('CSkFont', 1);
    this.entityPreamble(pid);
    this.str(faceName);
    this.u16(0); // bold/italic flags = 0
    this.u32(pointSize);
    this.u8(0); // use_world_size = False
    this.f64(1.0); // world_size = 1.0 inch
    return slot;
  }

  writeDimensionStyle(): number {
    this.entityPreamble(0); // CDimensionStyle CEntity preamble
    const arialSlot = this.writeSkFont('Arial', 12, DIM_STYLE_FONT_PID);
    this.u8(0); // has_3d = False
    this.u8(1); // dimension text visibility / placement flag
    this.u32(5); // extension_line_offset = 5
    this.u32(10); // extension_line_overshoot = 10
    this.u32(1); // line_weight = 1
    this.u32(3); // arrow_type = 3 (closed arrow)
    this.u32(10); // arrow_size = 10
    this.u8(0); // highlight_non_associative = False
    this.rgba(255, 0, 0, 255); // highlight_non_associative_color = red
    this.u8(1); // show_radial_prefix = True
    this.u8(0); // hide_out_of_plane = False
    this.f64(0.6); // hide_out_of_plane_value = 0.6
    this.u8(0); // hide_small = False
    this.f64(10.0); // hide_small_value = 10.0
    this.rgba(64, 64, 64, 255); // dimension colour = dark grey
    this.u8(0); // use_text_color = False
    this.zeros(3); // text alignment / flags = 0
    this.u8(1); // show_units = True
    this.zeros(3); // dimension-style trailer pad
    return arialSlot;
  }

  writeTextStyleAndFontManager(tailPos: number, arialSlot: number): number[] {
    const fontRefs: number[] = [];
    this.entityPreamble(0); // CTextStyle CEntity preamble

    // Inline CSkFont("Tahoma"): its short class-ref (0x8000|11) shifts when slots grow.
    fontRefs.push(this.len - tailPos);
    const tahomaSlot = this.writeSkFont('Tahoma', 12, TEXT_STYLE_FONT_PID);
    this.u32(2); // leader_type = 2 (pushpin)
    this.u32(1); // display_leader = 1
    this.u8(0); // text-style flag = 0
    this.u32(3); // arrow_type = 3 (closed arrow)
    this.u8(1); // leader visibility flag = 1
    this.rgba(0, 0, 0, 255); // text colour = black
    this.rgba(0, 0, 0, 255); // leader colour = black
    fontRefs.push(this.len - tailPos);
    this.backref(tahomaSlot);

    // CFontManager: [preamble][count = 2][Arial ref][Tahoma ref][closing u16 = 0]
    this.entityPreamble(0);
    this.u32(2);
    fontRefs.push(this.len - tailPos);
    this.backref(arialSlot);
    fontRefs.push(this.len - tailPos);
    this.backref(tahomaSlot);
    this.u16(0);
    return fontRefs;
  }

  private writeStyleProperties(properties: ReadonlyArray<StylePropertyDef>): number {
    this.u32(properties.length);
    const itemsStart = this.len;
    for (const [keyId, kind, value] of properties) {
      this.u32(keyId);
      if (kind === null) {
        this.u32(0);
        continue;
      }
      this.u32(1);
      this.u32(kind);
      if (kind === STYLE_BOOL) {
        this.u8(value !== 0.0 ? 1 : 0);
      } else if (kind === STYLE_UINT32) {
        this.u32(value >>> 0);
      } else if (kind === STYLE_FLOAT32) {
        this.f32(value);
      } else if (kind === STYLE_FLOAT64) {
        this.f64(value);
      } else {
        throw new Error(`unsupported style property kind ${kind}`);
      }
    }
    return itemsStart;
  }

  private writeSkpStyle(
    name: string,
    description: string,
    guid: string,
    pid: number
  ): [number, number, number] {
    const slot = this.newObject('CSkpStyle', 2);
    this.entityPreamble(pid);
    const guidOffset = this.len;
    this.uuid(guid);
    this.str(''); // source .style path
    this.u32(3); // style format version = 3
    this.str(name);
    this.str(description);
    const itemsOffset = this.writeStyleProperties(DEFAULT_STYLE_PROPERTIES);
    return [slot, guidOffset, itemsOffset];
  }

  writeStyleManager(
    tailPos: number
  ): [number, [number, number], number, number, number, number, number] {
    this.entityPreamble(0); // CSkpStyleManager CEntity preamble
    this.u32(1); // saved style count = 1

    const styleObjectTailRel = this.len - tailPos;
    const [savedSlot, savedGuidOff, savedItemsOff] = this.writeSkpStyle(
      'Style',
      '',
      DEFAULT_SAVED_STYLE_GUID,
      SAVED_STYLE_PID
    );

    const selectedRefTailRel = this.len - tailPos;
    this.backref(savedSlot); // selected saved style back-ref

    const workingRefTailRel = this.len - tailPos;
    const [, workingGuidOff, workingItemsOff] = this.writeSkpStyle(
      'Style',
      '',
      DEFAULT_WORKING_STYLE_GUID,
      0
    );

    this.u8(0); // active_style_changed = False
    return [
      styleObjectTailRel,
      [selectedRefTailRel, workingRefTailRel],
      savedGuidOff - tailPos,
      workingGuidOff - tailPos,
      savedItemsOff - tailPos,
      workingItemsOff - tailPos,
      DEFAULT_STYLE_PROPERTIES.length,
    ];
  }

  writeWatermarkManagerAndTrailer(): void {
    this.entityPreamble(0); // CWatermarkManager CEntity preamble
    this.u32(0); // watermark_count = 0
    this.u32(0); // watermark flags / selection = 0
    this.u8(1); // display_watermarks = True
    this.u32(0); // closing model trailer = 0
  }
}

/**
 * Build the empty classic SketchUp 2017 (v17) document and return its
 * serialized bytes alongside every splice offset and slot index.
 */
export function buildBlankV17(): BlankDocument {
  const b = new BlankArchiveBuilder();
  const modelGuidOffset = b.writeFileHeader();
  b.writeVersionMap();
  const [pidCounterOffset, noScaleMaskOffset] = b.writeModelPreamble();
  const unitOptionOffsets = b.writeOptionsProviders();
  b.writeModelProperties();
  const [cameraBodyOffset, cameraBodyLen] = b.writeModelCamera();
  const [roOffset, roLen, roFields] = b.writeRenderingOptions();
  const [matCountPos, matInsertPos, matBaseSlot] = b.writeComponentAndMaterialManager();
  const [
    layerCountPos,
    origLayerCount,
    layerInsertPos,
    layerWriterBaseSlot,
    activeLayerRefPos,
    scaffoldClassSlot,
  ] = b.writeLayerManager();
  const [
    defCountPos,
    origDefCount,
    rootCountPos,
    origRootCount,
    tailPos,
    scaffoldNextSlot,
  ] = b.writeDefinitionAndRootLists();
  const [shadowTailRel, shadowLen, pageListTailRel] = b.writeShadowInfo(tailPos);
  b.writePageList();
  b.writeModelAxes();
  const arialSlot = b.writeDimensionStyle();
  const fontTailRefs = b.writeTextStyleAndFontManager(tailPos, arialSlot);
  const [
    styleObjectTailRel,
    styleTailRefs,
    savedGuidTailRel,
    workingGuidTailRel,
    savedItemsTailRel,
    workingItemsTailRel,
    styleItemCount,
  ] = b.writeStyleManager(tailPos);
  b.writeWatermarkManagerAndTrailer();

  return {
    data: b.toUint8Array(),
    modelGuidOffset,
    pidCounterOffset,
    nextPid: INITIAL_NEXT_PERSISTENT_ID,
    noScaleMaskOffset,
    unitOptionOffsets,
    attrContainerSlot: b.classSlot.CAttributeContainer,
    attributeNamedSlot: b.classSlot.CAttributeNamed,
    ccameraSlot: b.classSlot.CCamera,
    cameraBodyOffset,
    cameraBodyLen,
    renderingOptionsOffset: roOffset,
    renderingOptionsLen: roLen,
    renderingOptionFields: roFields,
    materialCountPos: matCountPos,
    materialInsertPos: matInsertPos,
    materialBaseSlot: matBaseSlot,
    layerCountPos,
    origLayerCount,
    layerInsertPos,
    layerWriterBaseSlot,
    activeLayerRefPos,
    defCountPos,
    origDefCount,
    rootCountPos,
    origRootCount,
    tailPos,
    scaffoldNextSlot,
    scaffoldClassSlot,
    shadowInfoTailRel: shadowTailRel,
    shadowInfoLen: shadowLen,
    pageListTailRel,
    fontTailRefs,
    styleObjectTailRel,
    styleTailRefs,
    tailRefPositions: [...fontTailRefs, ...styleTailRefs],
    savedStyleGuidTailRel: savedGuidTailRel,
    workingStyleGuidTailRel: workingGuidTailRel,
    savedStyleItemsTailRel: savedItemsTailRel,
    workingStyleItemsTailRel: workingItemsTailRel,
    styleItemCount,
  };
}
