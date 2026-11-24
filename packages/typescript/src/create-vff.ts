/**
 * Native VFF (SketchUp 2021+) writer, ported from Python's
 * `openskp/create_vff.py` and `_vff_writer/`.
 *
 * The writer parses a genuine SketchUp-saved template (the bundled 2025
 * blank by default) into a record tree and extends it by tag; no template
 * byte offsets are used.  It covers faces, solid/textured materials, tags,
 * component definitions, groups and nested placements, arcs/circles/
 * polylines, attribute dictionaries, texts, dimensions, guides, section
 * planes, scenes and the model camera.  `openExistingVff` wraps the same
 * builder around an existing VFF source file.
 *
 * Format-documentation credit: skpwright's `docs/SKP_FORMAT.md` §16.15
 * (https://github.com/hew3d/skpwright, by Kurt Granroth, GPL-3.0-only)
 * describes the header integrity-word algorithm implemented here; the code
 * is openskp's own and was verified against genuine SketchUp 2025 saves.
 * No skpwright code is used or included.
 */
import { unzlibSync } from 'fflate';
import { blankVffTemplate } from './vff-scaffold';
import { concat, verifyIntegrity } from './vff-writer/core';
import { EntityEditor, Template } from './vff-writer/template';
import {
  Point3,
  VffAttributeDict,
  VffAttributeDicts,
  VffWriterOptions,
  resolveAttributeDicts,
} from './vff-writer/types';

export type { VffWriterOptions } from './vff-writer/types';

export class VffWriteError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'VffWriteError';
  }
}

export type VffTemplateSource = Uint8Array | ArrayBuffer;

export interface Rotation {
  axis: Point3;
  angleRadians: number;
}

export interface VffCurveHandle {
  points: Point3[];
  curveId: number;
  edgeIds: number[];
}

interface Placement {
  translation: Point3;
  matrix3x3?: number[];
  rotation?: Rotation;
  material?: number | null;
  layer?: number | null;
  hidden?: boolean;
  attributeDicts?: VffAttributeDicts;
}

function validatedRgba(rgba: number[], opacity?: number): [number, number, number, number] {
  if (rgba.length !== 3 && rgba.length !== 4) throw new VffWriteError('rgba must be 3 or 4 integers in 0-255');
  if (!rgba.every((c) => Number.isInteger(c) && c >= 0 && c <= 255)) throw new VffWriteError('rgba must be 3 or 4 integers in 0-255');
  const values: [number, number, number, number] = [rgba[0], rgba[1], rgba[2], rgba.length === 4 ? rgba[3] : 255];
  if (opacity !== undefined) {
    if (!(opacity >= 0 && opacity <= 1)) throw new VffWriteError('opacity must be between 0 and 1');
    values[3] = Math.round(opacity * 255);
  }
  return values;
}

function rotationMatrix(axis: Point3, angle: number): number[] {
  const length = Math.sqrt(axis[0] * axis[0] + axis[1] * axis[1] + axis[2] * axis[2]);
  if (length === 0) throw new VffWriteError('rotation axis must be non-zero');
  const [x, y, z] = [axis[0] / length, axis[1] / length, axis[2] / length];
  const cosine = Math.cos(angle);
  const sine = Math.sin(angle);
  const oneC = 1 - cosine;
  return [
    oneC * x * x + cosine, oneC * x * y - sine * z, oneC * x * z + sine * y,
    oneC * x * y + sine * z, oneC * y * y + cosine, oneC * y * z - sine * x,
    oneC * x * z - sine * y, oneC * y * z + sine * x, oneC * z * z + cosine,
  ];
}

function matrix13(translation: Point3, matrix?: number[], rotation?: Rotation): number[] {
  if (matrix && rotation) throw new VffWriteError('pass either matrix3x3 or rotation, not both');
  if (translation.length !== 3) throw new VffWriteError('translation must be three coordinates');
  let values: number[];
  if (matrix) {
    if (matrix.length !== 9) throw new VffWriteError('matrix3x3 must have nine values (row-major)');
    values = matrix.map(Number);
  } else if (rotation) {
    values = rotationMatrix(rotation.axis, rotation.angleRadians);
  } else {
    values = [1, 0, 0, 0, 1, 0, 0, 0, 1];
  }
  return [...values, translation[0], translation[1], translation[2], 1];
}

function imageExtension(image: Uint8Array): string {
  if (image.length >= 8 && [...image.subarray(0, 8)].every((v, i) => v === [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a][i])) return 'png';
  if (image.length >= 3 && image[0] === 0xff && image[1] === 0xd8 && image[2] === 0xff) return 'jpg';
  throw new VffWriteError("unrecognized image format - only PNG and JPEG textures are supported (detected from the file's own magic bytes, not any extension)");
}

/** Average RGB of a PNG (8-bit gray/RGB/RGBA), matching Python's Pillow path
 * for images at or below the 32x32 thumbnail size.  JPEG callers should pass
 * `averageColor` explicitly (documented parity choice). */
export function pngAverageColor(image: Uint8Array): [number, number, number] {
  if (imageExtension(image) !== 'png') return [128, 128, 128];
  const view = new DataView(image.buffer, image.byteOffset, image.byteLength);
  let pos = 8;
  let width = 0;
  let height = 0;
  let bitDepth = 0;
  let colorType = 0;
  const idat: Uint8Array[] = [];
  while (pos + 8 <= image.length) {
    const length = view.getUint32(pos);
    const type = new TextDecoder('ascii').decode(image.subarray(pos + 4, pos + 8));
    const body = image.subarray(pos + 8, pos + 8 + length);
    if (type === 'IHDR') {
      const header = new DataView(body.buffer, body.byteOffset, body.byteLength);
      width = header.getUint32(0);
      height = header.getUint32(4);
      bitDepth = body[8];
      colorType = body[9];
    } else if (type === 'IDAT') idat.push(body);
    else if (type === 'IEND') break;
    pos += 12 + length;
  }
  if (bitDepth !== 8 || ![0, 2, 4, 6].includes(colorType) || !width || !height) return [128, 128, 128];
  const channels = colorType === 0 ? 1 : colorType === 2 ? 3 : colorType === 4 ? 2 : 4;
  let raw: Uint8Array;
  try {
    raw = unzlibSync(concat(idat));
  } catch {
    return [128, 128, 128];
  }
  const stride = width * channels;
  const pixels = new Uint8Array(height * stride);
  let offset = 0;
  for (let y = 0; y < height; y++) {
    const filter = raw[offset++];
    const row = raw.subarray(offset, offset + stride);
    offset += stride;
    const out = pixels.subarray(y * stride, (y + 1) * stride);
    const prior = y > 0 ? pixels.subarray((y - 1) * stride, y * stride) : null;
    for (let x = 0; x < stride; x++) {
      const left = x >= channels ? out[x - channels] : 0;
      const up = prior ? prior[x] : 0;
      const upLeft = prior && x >= channels ? prior[x - channels] : 0;
      let value = row[x];
      if (filter === 1) value = (value + left) & 0xff;
      else if (filter === 2) value = (value + up) & 0xff;
      else if (filter === 3) value = (value + Math.floor((left + up) / 2)) & 0xff;
      else if (filter === 4) {
        const p = left + up - upLeft;
        const pa = Math.abs(p - left);
        const pb = Math.abs(p - up);
        const pc = Math.abs(p - upLeft);
        const pred = pa <= pb && pa <= pc ? left : pb <= pc ? up : upLeft;
        value = (value + pred) & 0xff;
      }
      out[x] = value;
    }
  }
  let r = 0;
  let g = 0;
  let b = 0;
  const count = width * height;
  for (let i = 0; i < count; i++) {
    const base = i * channels;
    if (colorType === 0 || colorType === 4) {
      r += pixels[base];
      g += pixels[base];
      b += pixels[base];
    } else {
      r += pixels[base];
      g += pixels[base + 1];
      b += pixels[base + 2];
    }
  }
  return [Math.floor(r / count), Math.floor(g / count), Math.floor(b / count)];
}

export class VffDefinitionHandle {
  readonly _builder: VffBuilder;
  readonly id: number;
  readonly name: string;
  readonly isGroup: boolean;
  readonly bounds: number[] | null;
  readonly _closed = true;

  constructor(builder: VffBuilder, id: number, name: string, isGroup = false, bounds: number[] | null = null) {
    this._builder = builder;
    this.id = id;
    this.name = name;
    this.isGroup = isGroup;
    this.bounds = bounds;
  }
}

export class VffComponentDefinitionBuilder {
  readonly _builder: VffBuilder;
  readonly name: string;
  readonly kind: number;
  readonly _editor: EntityEditor;
  readonly _containerId: number;
  readonly _owner: EntityEditor | null;
  readonly _placement: Placement | null;
  _closed = false;
  _instanceId: number | null = null;
  _instanceName = '';

  constructor(
    builder: VffBuilder,
    name: string,
    options: { kind?: number; owner?: EntityEditor | null; placement?: Placement | null; attributeDicts?: VffAttributeDicts } = {},
  ) {
    this._builder = builder;
    this.name = name;
    this.kind = options.kind ?? 0;
    this._containerId = builder._template.allocId();
    this._editor = new EntityEditor(builder._template, { containerId: this._containerId, attributes: options.attributeDicts });
    this._owner = options.owner ?? null;
    this._placement = options.placement ?? null;
  }

  get id(): number {
    if (!this._closed) throw new VffWriteError('definition is still open');
    return this._containerId;
  }

  get instanceId(): number | null {
    return this._instanceId;
  }

  get isGroup(): boolean {
    return this.kind === 1;
  }

  get bounds(): number[] | null {
    return this._editor.bounds;
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
      attributes?: VffAttributeDict;
      attributeDictName?: string;
      attributeDicts?: VffAttributeDicts;
    } = {},
  ): void {
    this._builder._validateMaterial(options.material ?? null);
    this._builder._validateMaterial(options.backMaterial ?? null);
    this._builder._validateLayer(options.layer ?? null);
    if (this._closed) throw new VffWriteError('this definition is already closed');
    this._editor.addFace(points, {
      material: options.material ?? null,
      layer: options.layer ?? null,
      backMaterial: options.backMaterial ?? null,
      hidden: options.hidden,
      holes: options.holes,
      frontUv: options.frontUv,
      backUv: options.backUv,
      attributeDicts: resolveAttributeDicts(options.attributes, options.attributeDictName ?? 'attributes', options.attributeDicts),
    });
  }

  addText(text: string, point: ArrayLike<number>, leader: ArrayLike<number> = [15, 15, 15], layer?: number | null): void {
    this._builder._validateLayer(layer ?? null);
    if (this._closed) throw new VffWriteError('this definition is already closed');
    this._editor.addText(text, point, leader, { screen: false, layer: layer ?? null });
  }

  addScreenText(text: string, point: ArrayLike<number>, vector: ArrayLike<number> = [30, 20, 0], layer?: number | null): void {
    this._builder._validateLayer(layer ?? null);
    if (this._closed) throw new VffWriteError('this definition is already closed');
    this._editor.addText(text, point, vector, { screen: true, layer: layer ?? null });
  }

  addDimension(start: ArrayLike<number>, end: ArrayLike<number>, offset = 10, offsetVector?: ArrayLike<number> | null, anchor = false): void {
    if (this._closed) throw new VffWriteError('this definition is already closed');
    this._editor.addDimension(start, end, offset, offsetVector, anchor);
  }

  addGuideLine(point: ArrayLike<number>, point2?: ArrayLike<number> | null, direction?: ArrayLike<number> | null): void {
    if (this._closed) throw new VffWriteError('this definition is already closed');
    this._editor.addGuideLine(point, point2, direction);
  }

  addGuidePoint(position: ArrayLike<number>): void {
    if (this._closed) throw new VffWriteError('this definition is already closed');
    this._editor.addGuidePoint(position);
  }

  addSectionPlane(point: ArrayLike<number>, normal: ArrayLike<number>, options: { name?: string; symbol?: string; active?: boolean; hidden?: boolean } = {}): number {
    if (this._closed) throw new VffWriteError('this definition is already closed');
    return this._editor.addSectionPlane(point, normal, options);
  }

  addCircle(center: ArrayLike<number>, normal: ArrayLike<number>, radius: number, options: { numSegments?: number; material?: number | null; layer?: number | null; backMaterial?: number | null; hidden?: boolean; frontUv?: Array<[ArrayLike<number>, [number, number]]>; backUv?: Array<[ArrayLike<number>, [number, number]]>; attributes?: VffAttributeDict; attributeDictName?: string; attributeDicts?: VffAttributeDicts; holes?: ArrayLike<number>[][] | Array<{ points: Point3[] }> } = {}): void {
    if (this._closed) throw new VffWriteError('this definition is already closed');
    this._builder._addCircleTo(this._editor, center, normal, radius, options);
  }

  addArc(center: ArrayLike<number>, normal: ArrayLike<number>, radius: number, startAngle: number, endAngle: number, options: { numSegments?: number; hiddenEdges?: boolean; softEdges?: boolean; smoothEdges?: boolean } = {}): void {
    if (this._closed) throw new VffWriteError('this definition is already closed');
    this._builder._addArcTo(this._editor, center, normal, radius, startAngle, endAngle, options);
  }

  addPolyline(points: ArrayLike<number>[], options: { closed?: boolean; hiddenEdges?: boolean; softEdges?: boolean; smoothEdges?: boolean } = {}): void {
    if (this._closed) throw new VffWriteError('this definition is already closed');
    this._builder._addPolylineTo(this._editor, points, options);
  }

  addInstance(definition: VffComponentDefinitionBuilder | VffDefinitionHandle, options: { name?: string; translation?: Point3; matrix3x3?: number[]; rotation?: Rotation; material?: number | null; layer?: number | null; hidden?: boolean; attributes?: VffAttributeDict; attributeDictName?: string; attributeDicts?: VffAttributeDicts } = {}): void {
    this._builder._checkPlacement(definition, options);
    if (this._closed) throw new VffWriteError('this definition is already closed');
    this._editor.addInstance(definition.id, matrix13(options.translation ?? [0, 0, 0], options.matrix3x3, options.rotation), {
      definitionBounds: definition.bounds,
      name: options.name ?? '',
      material: options.material ?? null,
      layer: options.layer ?? null,
      hidden: options.hidden,
      attributeDicts: resolveAttributeDicts(options.attributes, options.attributeDictName ?? 'attributes', options.attributeDicts),
    });
  }

  addGroupInstance(definition: VffComponentDefinitionBuilder | VffDefinitionHandle, options: { name?: string; translation?: Point3; matrix3x3?: number[]; rotation?: Rotation; material?: number | null; layer?: number | null; hidden?: boolean; attributes?: VffAttributeDict; attributeDictName?: string; attributeDicts?: VffAttributeDicts } = {}): void {
    this._builder._checkPlacement(definition, options);
    if (this._closed) throw new VffWriteError('this definition is already closed');
    this._editor.addGroup(definition.id, matrix13(options.translation ?? [0, 0, 0], options.matrix3x3, options.rotation), {
      definitionBounds: definition.bounds,
      name: options.name ?? '',
      material: options.material ?? null,
      layer: options.layer ?? null,
      hidden: options.hidden,
      attributeDicts: resolveAttributeDicts(options.attributes, options.attributeDictName ?? 'attributes', options.attributeDicts),
    });
  }

  addGroup(name?: string, options: { translation?: Point3; matrix3x3?: number[]; rotation?: Rotation; material?: number | null; layer?: number | null; hidden?: boolean; attributes?: VffAttributeDict; attributeDictName?: string; attributeDicts?: VffAttributeDicts } = {}): VffGroupBuilder {
    if (this._closed) throw new VffWriteError('this definition is already closed');
    this._builder._validateMaterial(options.material ?? null);
    this._builder._validateLayer(options.layer ?? null);
    return new VffGroupBuilder(this._builder, name ?? null, {
      owner: this._editor,
      placement: {
        translation: options.translation ?? [0, 0, 0],
        matrix3x3: options.matrix3x3,
        rotation: options.rotation,
        material: options.material ?? null,
        layer: options.layer ?? null,
        hidden: options.hidden,
        attributeDicts: resolveAttributeDicts(options.attributes, options.attributeDictName ?? 'attributes', options.attributeDicts),
      },
    });
  }

  close(): this {
    if (!this._closed) {
      const container = this._editor.finalize();
      this._builder._template.addDefinition(container, this.name, this._containerId, this.kind);
      this._closed = true;
      if (this._placement && this._owner) {
        this._instanceId = this._owner.addGroup(
          this._containerId,
          matrix13(this._placement.translation, this._placement.matrix3x3, this._placement.rotation),
          {
            definitionBounds: this.bounds,
            name: this._instanceName,
            material: this._placement.material ?? null,
            layer: this._placement.layer ?? null,
            hidden: this._placement.hidden,
            attributeDicts: this._placement.attributeDicts,
          },
        );
      }
    }
    return this;
  }
}

export class VffGroupBuilder extends VffComponentDefinitionBuilder {
  constructor(builder: VffBuilder, name: string | null, options: { owner: EntityEditor; placement: Placement }) {
    const definitionName = builder._uniqueDefinitionName(name ?? 'Group');
    super(builder, definitionName, { kind: 1, owner: options.owner, placement: options.placement });
    this._instanceName = name ?? '';
  }
}

export class VffBuilder {
  readonly _template: Template;
  readonly _root: EntityEditor;
  readonly materialsByName: { [name: string]: number };
  readonly layersByName: { [name: string]: number };
  definitionsByName: { [name: string]: VffDefinitionHandle } = {};
  definitionHandles: VffDefinitionHandle[] = [];
  private _definitionNames = new Set<string>();

  constructor(template?: VffTemplateSource, options: VffWriterOptions = {}) {
    let data: Uint8Array;
    if (template === undefined) data = blankVffTemplate();
    else if (template instanceof Uint8Array) data = template;
    else data = new Uint8Array(template);
    this._template = new Template(data, options);
    this._root = new EntityEditor(this._template, { container: this._template.rootContainer });
    this.materialsByName = { ...this._template.materials };
    this.layersByName = { ...this._template.layers };
    for (const existing of this._template.definitions) {
      if (existing.id === null) continue;
      const handle = new VffDefinitionHandle(this, existing.id, existing.name, existing.isGroup, existing.bounds);
      this.definitionHandles.push(handle);
      if (handle.name) this.definitionsByName[handle.name] = handle;
    }
  }

  addMaterial(name: string, rgba: number[], opacity?: number): number {
    if (name in this.materialsByName) return this.materialsByName[name];
    const values = validatedRgba(rgba, opacity);
    const id = this._template.addMaterial(name, [values[0], values[1], values[2]], values[3] / 255);
    this.materialsByName[name] = id;
    return id;
  }

  addTextureMaterial(name: string, image: Uint8Array, options: { appliedWidth?: number; appliedHeight?: number; opacity?: number; imageName?: string; averageColor?: [number, number, number] } = {}): number {
    if (name in this.materialsByName) return this.materialsByName[name];
    const extension = imageExtension(image);
    const entryName = options.imageName ?? `texture.${extension}`;
    const width = options.appliedWidth ?? 1;
    const height = options.appliedHeight ?? 1;
    if (width <= 0 || height <= 0) throw new VffWriteError("a texture's applied size must be positive");
    const opacity = options.opacity ?? 1;
    if (!(opacity >= 0 && opacity <= 1)) throw new VffWriteError('opacity must be between 0 and 1');
    const average = options.averageColor ?? pngAverageColor(image);
    const id = this._template.addTextureMaterial(name, image, entryName, width, height, opacity, average, entryName);
    this.materialsByName[name] = id;
    return id;
  }

  addLayer(name: string, color?: number[] | null, hidden = false): number {
    if (name in this.layersByName) return this.layersByName[name];
    const rgb = color ? validatedRgba(color) : null;
    const id = this._template.addLayer(name, rgb ? [rgb[0], rgb[1], rgb[2]] : null, hidden);
    this.layersByName[name] = id;
    return id;
  }

  _validateMaterial(material: number | null): void {
    if (material !== null && !this._template.materialIds.has(material)) throw new VffWriteError(`unknown material handle: ${material}`);
  }

  _validateLayer(layer: number | null): void {
    if (layer !== null && !this._template.layerIds.has(layer)) throw new VffWriteError(`unknown layer handle: ${layer}`);
  }

  _uniqueDefinitionName(base: string): string {
    let name = base;
    let counter = 1;
    while (this._definitionNames.has(name)) name = `${base}#${counter++}`;
    this._definitionNames.add(name);
    return name;
  }

  _checkDefinition(definition: VffComponentDefinitionBuilder | VffDefinitionHandle): void {
    if (!(definition instanceof VffComponentDefinitionBuilder) && !(definition instanceof VffDefinitionHandle)) throw new VffWriteError('this needs a component definition builder');
    if (definition._builder !== this) throw new VffWriteError('the definition belongs to a different builder');
    if (!definition._closed) throw new VffWriteError('close the definition before placing instances');
  }

  _checkPlacement(definition: VffComponentDefinitionBuilder | VffDefinitionHandle, options: { translation?: Point3; matrix3x3?: number[]; rotation?: Rotation; material?: number | null; layer?: number | null }): void {
    this._checkDefinition(definition);
    this._validateMaterial(options.material ?? null);
    this._validateLayer(options.layer ?? null);
    matrix13(options.translation ?? [0, 0, 0], options.matrix3x3, options.rotation);
  }

  addFace(points: ArrayLike<number>[], options: { material?: number | null; layer?: number | null; backMaterial?: number | null; hidden?: boolean; holes?: ArrayLike<number>[][] | Array<{ points: Point3[] }>; frontUv?: Array<[ArrayLike<number>, [number, number]]>; backUv?: Array<[ArrayLike<number>, [number, number]]>; attributes?: VffAttributeDict; attributeDictName?: string; attributeDicts?: VffAttributeDicts } = {}): void {
    this._validateMaterial(options.material ?? null);
    this._validateMaterial(options.backMaterial ?? null);
    this._validateLayer(options.layer ?? null);
    this._root.addFace(points, {
      material: options.material ?? null,
      layer: options.layer ?? null,
      backMaterial: options.backMaterial ?? null,
      hidden: options.hidden,
      holes: options.holes,
      frontUv: options.frontUv,
      backUv: options.backUv,
      attributeDicts: resolveAttributeDicts(options.attributes, options.attributeDictName ?? 'attributes', options.attributeDicts),
    });
  }

  addGroupInstance(definition: VffComponentDefinitionBuilder | VffDefinitionHandle, options: { name?: string; translation?: Point3; matrix3x3?: number[]; rotation?: Rotation; material?: number | null; layer?: number | null; hidden?: boolean; attributes?: VffAttributeDict; attributeDictName?: string; attributeDicts?: VffAttributeDicts } = {}): void {
    this._checkPlacement(definition, options);
    this._root.addGroup(definition.id, matrix13(options.translation ?? [0, 0, 0], options.matrix3x3, options.rotation), {
      definitionBounds: definition.bounds,
      name: options.name ?? '',
      material: options.material ?? null,
      layer: options.layer ?? null,
      hidden: options.hidden,
      attributeDicts: resolveAttributeDicts(options.attributes, options.attributeDictName ?? 'attributes', options.attributeDicts),
    });
  }

  addText(text: string, point: ArrayLike<number>, leader: ArrayLike<number> = [15, 15, 15], layer?: number | null): void {
    this._validateLayer(layer ?? null);
    this._root.addText(text, point, leader, { screen: false, layer: layer ?? null });
  }

  addScreenText(text: string, point: ArrayLike<number>, vector: ArrayLike<number> = [30, 20, 0], layer?: number | null): void {
    this._validateLayer(layer ?? null);
    this._root.addText(text, point, vector, { screen: true, layer: layer ?? null });
  }

  addDimension(start: ArrayLike<number>, end: ArrayLike<number>, offset = 10, offsetVector?: ArrayLike<number> | null, anchor = false): void {
    this._root.addDimension(start, end, offset, offsetVector, anchor);
  }

  addGuideLine(point: ArrayLike<number>, point2?: ArrayLike<number> | null, direction?: ArrayLike<number> | null): void {
    this._root.addGuideLine(point, point2, direction);
  }

  addGuidePoint(position: ArrayLike<number>): void {
    this._root.addGuidePoint(position);
  }

  addSectionPlane(point: ArrayLike<number>, normal: ArrayLike<number>, options: { name?: string; symbol?: string; active?: boolean; hidden?: boolean } = {}): number {
    return this._root.addSectionPlane(point, normal, options);
  }

  setActiveSectionPlane(identifier: number | null): void {
    this._root.activeSection = identifier;
  }

  _addCircleTo(editor: EntityEditor, center: ArrayLike<number>, normal: ArrayLike<number>, radius: number, options: { numSegments?: number; material?: number | null; layer?: number | null; backMaterial?: number | null; hidden?: boolean; frontUv?: Array<[ArrayLike<number>, [number, number]]>; backUv?: Array<[ArrayLike<number>, [number, number]]>; attributes?: VffAttributeDict; attributeDictName?: string; attributeDicts?: VffAttributeDicts; holes?: ArrayLike<number>[][] | Array<{ points: Point3[] }> } = {}): void {
    this._validateMaterial(options.material ?? null);
    this._validateMaterial(options.backMaterial ?? null);
    this._validateLayer(options.layer ?? null);
    const curve = this._writeCircleCurve(editor, center, normal, radius, options.numSegments ?? 24);
    editor.addFace(curve.points, {
      material: options.material ?? null,
      layer: options.layer ?? null,
      backMaterial: options.backMaterial ?? null,
      hidden: options.hidden,
      holes: options.holes,
      frontUv: options.frontUv,
      backUv: options.backUv,
      attributeDicts: resolveAttributeDicts(options.attributes, options.attributeDictName ?? 'attributes', options.attributeDicts),
    });
  }

  _writeCircleCurve(editor: EntityEditor, center: ArrayLike<number>, normal: ArrayLike<number>, radius: number, numSegments: number, hiddenEdges = false, softEdges = false, smoothEdges = false): VffCurveHandle {
    const curve = curvePoints(center, normal, radius, numSegments, undefined, undefined, !!this._template.options.portableTrig);
    const frame = arcFrame(curve.centre, curve.normal, radius, curve.u, curve.w, 0, 0);
    const [curveId, edges] = editor.addCurveEdges(curve.points, { closed: true, arcFrame: frame, flags: edgeFlags(hiddenEdges, softEdges, smoothEdges) });
    return { points: curve.points, curveId, edgeIds: edges };
  }

  _addArcTo(editor: EntityEditor, center: ArrayLike<number>, normal: ArrayLike<number>, radius: number, startAngle: number, endAngle: number, options: { numSegments?: number; hiddenEdges?: boolean; softEdges?: boolean; smoothEdges?: boolean } = {}): void {
    if (startAngle === endAngle) throw new VffWriteError('start_angle and end_angle must differ - use add_circle');
    const curve = curvePoints(center, normal, radius, options.numSegments ?? 24, startAngle, endAngle, !!this._template.options.portableTrig);
    const frame = arcFrame(curve.centre, curve.normal, radius, curve.u, curve.w, startAngle, endAngle);
    editor.addCurveEdges(curve.points, { arcFrame: frame, flags: edgeFlags(options.hiddenEdges ?? false, options.softEdges ?? false, options.smoothEdges ?? false) });
  }

  _addPolylineTo(editor: EntityEditor, points: ArrayLike<number>[], options: { closed?: boolean; hiddenEdges?: boolean; softEdges?: boolean; smoothEdges?: boolean } = {}): void {
    const coordinates = points.map((p) => [Number(p[0]), Number(p[1]), Number(p[2])] as Point3);
    if (coordinates.length < 2) throw new VffWriteError('a polyline needs at least 2 points');
    editor.addCurveEdges(coordinates, {
      closed: !!options.closed,
      flags: edgeFlags(options.hiddenEdges ?? false, options.softEdges ?? false, options.smoothEdges ?? false),
    });
  }

  addCircleCurve(center: ArrayLike<number>, normal: ArrayLike<number>, radius: number, options: { numSegments?: number; hiddenEdges?: boolean; softEdges?: boolean; smoothEdges?: boolean } = {}): VffCurveHandle {
    return this._writeCircleCurve(this._root, center, normal, radius, options.numSegments ?? 24, options.hiddenEdges, options.softEdges, options.smoothEdges);
  }

  addCircle(center: ArrayLike<number>, normal: ArrayLike<number>, radius: number, options: { numSegments?: number; material?: number | null; layer?: number | null; backMaterial?: number | null; hidden?: boolean; frontUv?: Array<[ArrayLike<number>, [number, number]]>; backUv?: Array<[ArrayLike<number>, [number, number]]>; attributes?: VffAttributeDict; attributeDictName?: string; attributeDicts?: VffAttributeDicts; holes?: ArrayLike<number>[][] | Array<{ points: Point3[] }> } = {}): void {
    this._addCircleTo(this._root, center, normal, radius, options);
  }

  addArc(center: ArrayLike<number>, normal: ArrayLike<number>, radius: number, startAngle: number, endAngle: number, options: { numSegments?: number; hiddenEdges?: boolean; softEdges?: boolean; smoothEdges?: boolean } = {}): void {
    this._addArcTo(this._root, center, normal, radius, startAngle, endAngle, options);
  }

  addPolyline(points: ArrayLike<number>[], options: { closed?: boolean; hiddenEdges?: boolean; softEdges?: boolean; smoothEdges?: boolean } = {}): void {
    this._addPolylineTo(this._root, points, options);
  }

  addComponentDefinition(name: string, options: { attributes?: VffAttributeDict; attributeDictName?: string; attributeDicts?: VffAttributeDicts } = {}): VffComponentDefinitionBuilder {
    return new VffComponentDefinitionBuilder(this, this._uniqueDefinitionName(name), {
      attributeDicts: resolveAttributeDicts(options.attributes, options.attributeDictName ?? 'attributes', options.attributeDicts),
    });
  }

  addGroup(name?: string, options: { translation?: Point3; matrix3x3?: number[]; rotation?: Rotation; material?: number | null; layer?: number | null; hidden?: boolean; attributes?: VffAttributeDict; attributeDictName?: string; attributeDicts?: VffAttributeDicts } = {}): VffGroupBuilder {
    this._validateMaterial(options.material ?? null);
    this._validateLayer(options.layer ?? null);
    return new VffGroupBuilder(this, name ?? null, {
      owner: this._root,
      placement: {
        translation: options.translation ?? [0, 0, 0],
        matrix3x3: options.matrix3x3,
        rotation: options.rotation,
        material: options.material ?? null,
        layer: options.layer ?? null,
        hidden: options.hidden,
        attributeDicts: resolveAttributeDicts(options.attributes, options.attributeDictName ?? 'attributes', options.attributeDicts),
      },
    });
  }

  addInstance(definition: VffComponentDefinitionBuilder | VffDefinitionHandle, options: { name?: string; translation?: Point3; matrix3x3?: number[]; rotation?: Rotation; material?: number | null; layer?: number | null; hidden?: boolean; attributes?: VffAttributeDict; attributeDictName?: string; attributeDicts?: VffAttributeDicts } = {}): void {
    this._checkPlacement(definition, options);
    this._root.addInstance(definition.id, matrix13(options.translation ?? [0, 0, 0], options.matrix3x3, options.rotation), {
      definitionBounds: definition.bounds,
      name: options.name ?? '',
      material: options.material ?? null,
      layer: options.layer ?? null,
      hidden: options.hidden,
      attributeDicts: resolveAttributeDicts(options.attributes, options.attributeDictName ?? 'attributes', options.attributeDicts),
    });
  }

  addScene(name: string, eye: ArrayLike<number>, target: ArrayLike<number>, options: { up?: ArrayLike<number> | null; perspective?: boolean; fov?: number; orthoHeight?: number; hiddenLayers?: number[] } = {}): void {
    for (const layer of options.hiddenLayers ?? []) this._validateLayer(layer);
    this._template.addScene(name, [Number(eye[0]), Number(eye[1]), Number(eye[2])], [Number(target[0]), Number(target[1]), Number(target[2])], options.up ? [Number(options.up[0]), Number(options.up[1]), Number(options.up[2])] : null, {
      perspective: options.perspective ?? true,
      fov: options.fov ?? 35,
      orthoHeight: options.orthoHeight ?? 200,
      hiddenLayers: options.hiddenLayers ?? [],
    });
  }

  setCamera(eye: ArrayLike<number>, target: ArrayLike<number>, options: { up?: ArrayLike<number> | null; perspective?: boolean; fov?: number; orthoHeight?: number } = {}): void {
    this._template.setCamera([Number(eye[0]), Number(eye[1]), Number(eye[2])], [Number(target[0]), Number(target[1]), Number(target[2])], options.up ? [Number(options.up[0]), Number(options.up[1]), Number(options.up[2])] : null, {
      perspective: options.perspective ?? true,
      fov: options.fov ?? 35,
      orthoHeight: options.orthoHeight ?? 200,
    });
  }

  setModelAttributes(options: { attributes?: VffAttributeDict; attributeDictName?: string; attributeDicts?: VffAttributeDicts } = {}): void {
    this._root.addModelAttributes(resolveAttributeDicts(options.attributes, options.attributeDictName ?? 'attributes', options.attributeDicts));
  }

  toBytes(): Uint8Array {
    this._root.finalize();
    return this._template.build();
  }
}

export function createVff(template?: VffTemplateSource, options: VffWriterOptions = {}): VffBuilder {
  return new VffBuilder(template, options);
}

/** Compatibility alias used by Python's module (`create`). */
export const create = createVff;

export function checkIntegrity(data: Uint8Array): boolean {
  return verifyIntegrity(data);
}

export function openExistingVff(data: Uint8Array): [VffBuilder, string[], { [name: string]: VffDefinitionHandle }] {
  if (data.length < 4 || !(data[0] === 0xff && data[1] === 0xfe && data[2] === 0xff && data[3] === 0x0e)) throw new VffWriteError('not a VFF .skp file');
  const start = data.indexOf(0x56);
  void start;
  const marker = 'VFF';
  const markerBytes = new TextEncoder().encode(marker);
  let header = -1;
  for (let i = 0; i + 7 < data.length; i++) {
    if (data[i] === markerBytes[0] && data[i + 1] === markerBytes[1] && data[i + 2] === markerBytes[2] && data[i + 3] === 8 && data[i + 4] === 0 && data[i + 5] === 1 && data[i + 6] === 0) {
      header = i;
      break;
    }
  }
  if (header < 0 || header + 9 > data.length) throw new VffWriteError('not a complete VFF .skp file');
  const revision = data[header + 7] | (data[header + 8] << 8);
  if (revision > 17) throw new VffWriteError(`VFF model revision ${revision} is newer than 17; only SketchUp 2025/revision 17 has acceptance evidence`);
  const builder = new VffBuilder(data);
  return [builder, [], { ...builder.definitionsByName }];
}

// Geometry helpers (ported from Python's create.py).
function normalize3(v: ArrayLike<number>): Point3 {
  const length = Math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
  if (length === 0) throw new VffWriteError('a curve normal must not be the zero vector');
  return [v[0] / length, v[1] / length, v[2] / length];
}

function cross(a: ArrayLike<number>, b: ArrayLike<number>): Point3 {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}

function circleBasis(normal: ArrayLike<number>): [Point3, Point3] {
  const seed: Point3 = Math.abs(normal[2]) < 0.9 ? [0, 0, 1] : [1, 0, 0];
  const dot = seed[0] * normal[0] + seed[1] * normal[1] + seed[2] * normal[2];
  const u = normalize3([seed[0] - dot * normal[0], seed[1] - dot * normal[1], seed[2] - dot * normal[2]]);
  return [u, normalize3(cross(normal, u))];
}

// The trigonometric kernels below are derived from fdlibm (Sun
// Microsystems); see the copyright notice in Python's create_vff.py.  The
// same sequence of IEEE double operations is used there, so opt-in
// "portableTrig" output is bit-identical across Python and TypeScript.
const INVPIO2 = 6.36619772367581382433e-1;
const PIO2_1 = 1.57079632673412561417;
const PIO2_1T = 6.07710050650619224932e-11;
const S1 = -1.66666666666666324348e-1;
const S2 = 8.33333333332248946124e-3;
const S3 = -1.98412698298579493134e-4;
const S4 = 2.75573137070700676789e-6;
const S5 = -2.50507602534068634195e-8;
const S6 = 1.58969099521155010221e-10;
const C1 = 4.16666666666666019037e-2;
const C2 = -1.38888888888741095749e-3;
const C3 = 2.48015872894767294178e-5;
const C4 = -2.75573143513906633035e-7;
const C5 = 2.0875723212981748279e-9;
const C6 = -1.13596475577881948265e-11;

function kernelSin(x: number, y: number): number {
  const z = x * x;
  const v = z * x;
  const r = S2 + z * (S3 + z * (S4 + z * (S5 + z * S6)));
  return x + v * (S1 + z * r) + y;
}

function kernelCos(x: number, y: number): number {
  const z = x * x;
  const r = z * (C1 + z * (C2 + z * (C3 + z * (C4 + z * (C5 + z * C6)))));
  if (Math.abs(x) < 0.3) return 1 - (0.5 * z - (z * r - x * y));
  const hz = 0.5 * z;
  const w = 1 - hz;
  return w + (((1 - w) - hz) + (z * r - x * y));
}

function remPio2(x: number): [number, number] {
  const n = Math.floor(x * INVPIO2 + 0.5);
  const r = x - n * PIO2_1;
  const w = n * PIO2_1T;
  return [n & 3, r - w];
}

export function portableSin(x: number): number {
  const [quadrant, y] = remPio2(x);
  if (quadrant === 0) return kernelSin(y, 0);
  if (quadrant === 1) return kernelCos(y, 0);
  if (quadrant === 2) return -kernelSin(y, 0);
  return -kernelCos(y, 0);
}

export function portableCos(x: number): number {
  const [quadrant, y] = remPio2(x);
  if (quadrant === 0) return kernelCos(y, 0);
  if (quadrant === 1) return -kernelSin(y, 0);
  if (quadrant === 2) return -kernelCos(y, 0);
  return kernelSin(y, 0);
}

function curvePoints(center: ArrayLike<number>, normal: ArrayLike<number>, radius: number, numSegments: number, startAngle?: number, endAngle?: number, portable = false): { centre: Point3; normal: Point3; u: Point3; w: Point3; points: Point3[] } {
  if (!(numSegments >= 3 && numSegments <= 255)) throw new VffWriteError('num_segments must be between 3 and 255');
  const centre: Point3 = [Number(center[0]), Number(center[1]), Number(center[2])];
  const unitNormal = normalize3(normal);
  const [u, w] = circleBasis(unitNormal);
  const points: Point3[] = [];
  const count = startAngle === undefined || endAngle === undefined ? numSegments : numSegments + 1;
  const sin = portable ? portableSin : Math.sin;
  const cos = portable ? portableCos : Math.cos;
  for (let i = 0; i < count; i++) {
    const angle = startAngle === undefined || endAngle === undefined ? (2 * Math.PI * i) / numSegments : startAngle + ((endAngle - startAngle) * i) / numSegments;
    const c = cos(angle);
    const s = sin(angle);
    points.push([
      centre[0] + radius * (c * u[0] + s * w[0]),
      centre[1] + radius * (c * u[1] + s * w[1]),
      centre[2] + radius * (c * u[2] + s * w[2]),
    ]);
  }
  return { centre, normal: unitNormal, u, w, points };
}

function arcFrame(center: Point3, normal: Point3, radius: number, u: Point3, w: Point3, startAngle: number, endAngle: number): number[] {
  const offset = -(normal[0] * center[0] + normal[1] * center[1] + normal[2] * center[2]);
  return [
    ...center, ...normal, offset,
    radius * u[0], radius * u[1], radius * u[2],
    radius * w[0], radius * w[1], radius * w[2],
    radius, startAngle, endAngle,
  ];
}

function edgeFlags(hidden: boolean, soft: boolean, smooth: boolean): number {
  return 0x06 | (hidden ? 0x01 : 0) | (soft ? 0x08 : 0) | (smooth ? 0x10 : 0);
}
