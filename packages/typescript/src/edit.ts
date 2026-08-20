/**
 * Load an existing legacy-format `.skp` file and rebuild it as a new,
 * independent `SkpBuilder`.
 *
 * Ported from Python's `openskp.edit` (`packages/python/src/openskp/edit.py`).
 * `create.ts` only ever builds a brand-new file by splicing new geometry
 * into its own bundled blank scaffold (see that module's docstring) -
 * there is no way to append to or patch an arbitrary existing file's bytes
 * in place, because real SketchUp itself doesn't do that either: it fully
 * re-serializes the whole document on every save, so there is no stable
 * "original bytes + appended bytes" structure to target for a file this
 * project didn't create.
 *
 * This module takes the other viable approach instead: fully parse the
 * existing file with this package's own reader (`legacy.ts`/`index.ts`,
 * already comprehensive), then *replay* everything it understood back
 * through the writer's own public API (materials, layers, every component
 * definition, every face/instance) to produce a brand-new file - not a
 * byte-patched copy of the original, but a freshly-built one with
 * equivalent content, to which the caller can add more geometry before
 * saving.
 *
 * **Adding more geometry after the fact.** The returned builder can take
 * more addFace/addCircle/addInstance/etc. calls, and every material/layer
 * the source had is already reachable via `builder.materialsByName`/
 * `builder.layersByName` (no separate lookup needed - `openExisting` also
 * returns a `definitions` map from each component definition's name to
 * its builder, for placing more instances of something the source already
 * defined). What the returned builder can no longer do is register a
 * genuinely NEW material, layer, or component definition/group -
 * `create.ts`'s own file-format ordering requirement (materials/layers/
 * definitions must all be finalized before any geometry is written) is
 * already satisfied by the time replay finishes writing the source's own
 * root-level geometry (which happens for any source file with root-level
 * content - in practice, almost always), so addMaterial/addLayer/
 * addComponentDefinition/addGroup all throw on the returned builder.
 * Build anything new into a separate `create()` call instead.
 *
 * **Scope and known fidelity gaps** (this reads long because every gap
 * here is a genuine, deliberately-scoped limitation, not an oversight -
 * see each module's own docstring for why, exactly mirroring Python's
 * own module docstring's disclosure):
 *
 * - Only a **legacy-format** (SketchUp 2013-2020) source file is
 *   accepted - `create.ts` never writes any other format, so a modern
 *   VFF (2021+) source can't be faithfully round-tripped through it.
 * - Per-edge hidden/soft/smooth flags are applied per-FACE, not per-edge
 *   (an "any edge in this boundary has the flag" approximation) -
 *   addFace can only set these uniformly for every edge it newly
 *   declares in one call, the same limitation any user of that API has.
 * - A positioned texture is replayed via 3 sample-point correspondences
 *   fitted to an affine map (see addFace's own frontUv/backUv) - exact
 *   at those 3 points, but a genuinely projective (4-pin/distorted)
 *   source mapping won't interpolate identically between them. A
 *   *projected* (draped) texture has no equivalent at all and falls back
 *   to the default projection.
 * - A material's original texture tile size isn't preserved -
 *   `addTextureMaterial` has no scale parameter yet. A colorized
 *   (tinted) material variant is replayed as its plain source texture,
 *   losing the tint.
 * - Per-face material/layer painting: only a face's front/back
 *   *material* is replayed - this package's reader doesn't expose a
 *   per-face layer assignment at all (only instances carry an explicit
 *   layer).
 * - Every placed thing (originally a group or a component instance
 *   alike) is replayed as a plain component instance - structurally
 *   simpler, and visually identical, but no longer shows as a "Group" in
 *   SketchUp's Outliner afterward.
 * - Section planes, text entities, and dimensions aren't carried over at
 *   all - the writer has no support for any of these entity types.
 * - A circle/arc/polyline's original CArcCurve/CCurve grouping is lost -
 *   this package's reader doesn't preserve that grouping in its public
 *   Face/Edge model, so a round-tripped circle becomes an ordinary
 *   straight-edged face.
 * - Definition-level and face-level custom attributes aren't
 *   reproduced - the reader's public model doesn't expose either (only
 *   an instance's own `properties` are).
 */
import { isLegacy } from './legacy';
import { reconstructLoopVertices } from './geometry';
import { faceUvBasis, computeFaceUv, SkpModel, Definition, Face, Instance } from './model';
import { parseSkp } from './index';
import {
  create,
  SkpBuilder,
  ComponentDefinitionBuilder,
  SkpWriteError,
  Point3,
  Matrix3x3,
  UvPair,
  AddFaceOptions,
  AddInstanceOptions,
} from './create';

// This package has no hard dependency on @types/node (it targets the
// browser too), so - like index.ts's own SkpFile.open - Node-only globals
// are declared `any` here rather than pulled in via @types/node.
declare const process: any;
declare const require: any;

/** The shared addFace/addInstance surface `SkpBuilder` and
 * `ComponentDefinitionBuilder` both expose - replay works generically
 * against either, matching Python's `_replay_body`'s own `target`
 * parameter (a builder for the root, a definition builder for a nested
 * definition). */
interface ReplayTarget {
  addFace(points: readonly Point3[], options?: AddFaceOptions): void;
  addInstance(definition: ComponentDefinitionBuilder, options?: AddInstanceOptions): void;
}

function readFileToArrayBuffer(path: string): ArrayBuffer {
  if (typeof process === 'undefined' || !process.versions || !process.versions.node) {
    throw new SkpWriteError(
      'a file-path source for openExisting is only supported in Node.js environments - pass an ArrayBuffer instead'
    );
  }
  const fs = require('fs');
  const buffer = fs.readFileSync(path);
  return buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength);
}

/**
 * Parse `source` (a legacy-format `.skp` file - either a filesystem path,
 * Node.js only, or an already-loaded `ArrayBuffer`, which works in the
 * browser too) and rebuild it as a new `SkpBuilder`, replaying materials,
 * layers, every component definition, and all root-level geometry/
 * instances.
 *
 * Returns `{ builder, warnings, definitions }`:
 *
 * - `builder` is ready for more addFace/addCircle/addInstance/etc. calls
 *   before `builder.toBytes()`/`builder.save()`. Every material and layer
 *   the source file had is already reachable via `builder.materialsByName`/
 *   `builder.layersByName` - reuse one as e.g. `addFace(points, {material:
 *   builder.materialsByName.get('Walnut')})`. A file-format ordering
 *   requirement this writer has always had (materials/layers/definitions
 *   must be finalized before any geometry is written) means a genuinely
 *   NEW material/layer/definition/group can no longer be added to
 *   `builder` at this point, since replaying the source's own root-level
 *   geometry already finalized all of those sections - build anything new
 *   into a SEPARATE `create()` builder instead.
 * - `warnings` lists anything from the source file that couldn't be
 *   faithfully reproduced (see this module's own docstring for the exact,
 *   deliberately-scoped gaps this draws from).
 * - `definitions` maps each replayed component definition's own name to
 *   its (already-closed) `ComponentDefinitionBuilder`, so the caller can
 *   place additional instances of something the source file already
 *   defined via `builder.addInstance(definitions.get('Wheel')!, {
 *   translation: ... })`. If two source definitions share a name, the
 *   later one wins - real SketchUp allows duplicate component names, this
 *   package's writer doesn't need them to be unique, only this
 *   convenience lookup does.
 *
 * @throws SkpWriteError if `source` isn't a legacy-format file.
 */
export function openExisting(
  source: string | ArrayBuffer
): { builder: SkpBuilder; warnings: string[]; definitions: Map<string, ComponentDefinitionBuilder> } {
  const buffer = typeof source === 'string' ? readFileToArrayBuffer(source) : source;
  const head = new Uint8Array(buffer, 0, Math.min(0x200, buffer.byteLength));
  if (!isLegacy(head)) {
    throw new SkpWriteError(
      `${typeof source === 'string' ? JSON.stringify(source) : 'the given buffer'} is not a legacy-format ` +
        '(SketchUp 2013-2020) .skp file - create.ts only ever writes that format, so only a legacy-format ' +
        "source file can be rebuilt through it (see edit.ts's own module docstring for why an arbitrary " +
        'existing file cannot simply be patched)'
    );
  }
  const model = parseSkp(buffer);
  const warnings: string[] = [];
  const builder = create();

  const materialSlots = replayMaterials(builder, model, warnings);
  const layerSlots = new Map<string, number>();
  for (const layer of model.layers) {
    layerSlots.set(layer.name, builder.addLayer(layer.name, { color: [layer.color.r, layer.color.g, layer.color.b], hidden: layer.hidden }));
  }

  const defBuilders = new Map<number, ComponentDefinitionBuilder>();
  for (const defId of definitionOrder(model)) {
    const defn = model.definitions.get(defId) as Definition;
    const context = `definition ${JSON.stringify(defn.name || String(defId))}`;
    if (!definitionHasContent(defn, defBuilders)) {
      warnings.push(`${context}: skipped (no replayable geometry)`);
      continue;
    }
    const db = builder.addComponentDefinition(defn.name || `Definition${defId}`, (d) => {
      replayBody(d, defn, model, materialSlots, layerSlots, warnings, context, defBuilders);
    });
    defBuilders.set(defId, db);
  }

  replayBody(builder, model.root, model, materialSlots, layerSlots, warnings, 'root', defBuilders);

  const definitionsByName = new Map<string, ComponentDefinitionBuilder>();
  for (const [defId, db] of defBuilders.entries()) {
    const name = (model.definitions.get(defId) as Definition).name;
    if (name) definitionsByName.set(name, db);
  }
  return { builder, warnings, definitions: definitionsByName };
}

function replayMaterials(builder: SkpBuilder, model: SkpModel, warnings: string[]): Map<object, number> {
  const slots = new Map<object, number>();
  for (const mat of model.materials) {
    let slot: number;
    if (mat.texture !== null && mat.texture.data !== null) {
      slot = builder.addTextureMaterial(mat.name, mat.texture.data, mat.texture.filename || 'texture');
      if (mat.texture.width || mat.texture.height) {
        warnings.push(`material ${JSON.stringify(mat.name)}: original texture tile size not preserved`);
      }
      if (mat.colorized) {
        warnings.push(`material ${JSON.stringify(mat.name)}: colorized tint not reproduced (base texture only)`);
      }
    } else {
      if (mat.texture !== null) {
        warnings.push(`material ${JSON.stringify(mat.name)}: texture image data missing - replayed as solid color`);
      }
      slot = builder.addMaterial(mat.name, [mat.color.r, mat.color.g, mat.color.b, mat.color.a]);
    }
    slots.set(mat, slot);
  }
  return slots;
}

function materialSlot(materialId: number | null, model: SkpModel, slots: Map<object, number>): number | undefined {
  if (materialId === null) return undefined;
  const mat = model.materialsById.get(materialId);
  if (!mat) return undefined;
  return slots.get(mat);
}

/** Topological order (dependencies before dependents) so a definition
 * nesting instances of other definitions is only replayed after those
 * are already built - the same ordering constraint
 * `ComponentDefinitionBuilder.addInstance` documents. */
function definitionOrder(model: SkpModel): number[] {
  const visited = new Set<number>();
  const temp = new Set<number>();
  const order: number[] = [];

  function visit(defId: number): void {
    if (visited.has(defId)) return;
    if (temp.has(defId)) {
      throw new SkpWriteError(`circular component-definition reference involving definition ${defId}`);
    }
    temp.add(defId);
    const defn = model.definitions.get(defId);
    if (defn) {
      for (const inst of defn.instances) {
        if (model.definitions.has(inst.refIdx)) visit(inst.refIdx);
      }
    }
    temp.delete(defId);
    visited.add(defId);
    order.push(defId);
  }

  for (const defId of model.definitions.keys()) visit(defId);
  return order;
}

function edgeMap(defn: Definition): Map<number, [number | null, number | null]> {
  const m = new Map<number, [number | null, number | null]>();
  for (const e of defn.edges) m.set(e.id, [e.v1Id, e.v2Id]);
  return m;
}

function edgeById(defn: Definition): Map<number, Definition['edges'][number]> {
  const m = new Map<number, Definition['edges'][number]>();
  for (const e of defn.edges) m.set(e.id, e);
  return m;
}

function definitionHasContent(defn: Definition, defBuilders: Map<number, ComponentDefinitionBuilder>): boolean {
  const edges = edgeMap(defn);
  for (const face of defn.faces) {
    if (face.loops.length === 0) continue;
    if (reconstructLoopVertices(face.loops[0], edges).length >= 3) return true;
  }
  for (const inst of defn.instances) {
    if (defBuilders.has(inst.refIdx)) return true;
  }
  return false;
}

/** Replay one definition's (or the root's) own faces and instances onto
 * `target` - a `SkpBuilder` for the root, or a `ComponentDefinitionBuilder`
 * for a nested definition; both expose the same addFace/addInstance shape
 * this calls generically. `defBuilders` resolves instance references - by
 * the time any definition is opened (topological order, see
 * `definitionOrder`) every OTHER definition its own instances could
 * reference is already in it. */
function replayBody(
  target: ReplayTarget,
  defn: Definition,
  model: SkpModel,
  materialSlots: Map<object, number>,
  layerSlots: Map<string, number>,
  warnings: string[],
  context: string,
  defBuilders: Map<number, ComponentDefinitionBuilder>
): void {
  const edges = edgeMap(defn);
  const edgesById = edgeById(defn);
  const vertexById = new Map(defn.vertices.map((v) => [v.id, [v.x, v.y, v.z] as Point3]));
  for (const face of defn.faces) {
    replayFace(target, face, edges, edgesById, vertexById, model, materialSlots, warnings, context);
  }
  for (const inst of defn.instances) {
    replayInstance(target, inst, defBuilders, materialSlots, layerSlots, model, warnings, context);
  }
}

function replayFace(
  target: ReplayTarget,
  face: Face,
  edges: Map<number, [number | null, number | null]>,
  edgesById: Map<number, Definition['edges'][number]>,
  vertexById: Map<number, Point3>,
  model: SkpModel,
  materialSlots: Map<object, number>,
  warnings: string[],
  context: string
): void {
  if (face.loops.length < 1) {
    warnings.push(`${context}: face ${face.id} has no loops - skipped`);
    return;
  }
  const vertIds = reconstructLoopVertices(face.loops[0], edges);
  if (vertIds.length < 3) {
    warnings.push(`${context}: face ${face.id} has fewer than 3 usable points - skipped`);
    return;
  }
  const points = vertIds.map((v) => vertexById.get(v) as Point3);

  const holes: Point3[][] = [];
  for (const holeLoop of face.loops.slice(1)) {
    const holeVertIds = reconstructLoopVertices(holeLoop, edges);
    if (holeVertIds.length < 3) {
      warnings.push(`${context}: face ${face.id} has a hole with fewer than 3 usable points - skipped`);
      return;
    }
    holes.push(holeVertIds.map((v) => vertexById.get(v) as Point3));
  }

  // Per-edge hidden/soft/smooth flags collapse to per-face here (an "any
  // edge in this boundary has the flag" approximation) - addFace can only
  // set these uniformly for every edge it newly declares in one call, the
  // same limitation any user of that API has (see this module's own
  // docstring's fidelity-gap list).
  const loopEdges = face.loops[0].map((ce) => edgesById.get(ce.edgeId)).filter((e): e is Definition['edges'][number] => e !== undefined);
  const hiddenEdges = loopEdges.some((e) => e.hidden);
  const softEdges = loopEdges.some((e) => e.soft);
  const smoothEdges = loopEdges.some((e) => e.smooth);

  const material = materialSlot(face.materialId, model, materialSlots);
  const backMaterial = materialSlot(face.backMaterialId, model, materialSlots);

  const frontUv = replayUv(face.materialId, face.uvTransform, face.uvProjected, points, face.normal, model, warnings, context, 'front');
  const backUv = replayUv(face.backMaterialId, face.uvTransformBack, face.uvProjectedBack, points, face.normal, model, warnings, context, 'back');

  try {
    target.addFace(points, {
      material,
      backMaterial,
      hidden: face.hidden,
      softEdges,
      smoothEdges,
      hiddenEdges,
      frontUv,
      backUv,
      holes,
    });
  } catch (exc) {
    if (exc instanceof SkpWriteError) {
      warnings.push(`${context}: face ${face.id} skipped (${exc.message})`);
    } else {
      throw exc;
    }
  }
}

function replayUv(
  materialId: number | null,
  uvTransform: number[] | null,
  projected: boolean,
  points: readonly Point3[],
  normal: readonly [number, number, number],
  model: SkpModel,
  warnings: string[],
  context: string,
  side: 'front' | 'back'
): UvPair[] | undefined {
  if (uvTransform === null) return undefined;
  if (projected) {
    warnings.push(`${context}: ${side} texture is projected/draped - falls back to default projection`);
    return undefined;
  }
  const mat = materialId !== null ? model.materialsById.get(materialId) : undefined;
  const tileW = (mat && mat.texture ? mat.texture.width : 0) || 1.0;
  const tileH = (mat && mat.texture ? mat.texture.height : 0) || 1.0;
  const { xr, yr } = faceUvBasis(normal as [number, number, number]);
  const sample = points.slice(0, 3);
  if (sample.length < 3) return undefined;
  return sample.map((p): UvPair => {
    const [u, v] = computeFaceUv(p, xr, yr, uvTransform, tileW, tileH);
    return [p, [u, v]];
  });
}

function replayInstance(
  target: ReplayTarget,
  inst: Instance,
  defBuilders: Map<number, ComponentDefinitionBuilder>,
  materialSlots: Map<object, number>,
  layerSlots: Map<string, number>,
  model: SkpModel,
  warnings: string[],
  context: string
): void {
  const defBuilder = defBuilders.get(inst.refIdx);
  if (!defBuilder) {
    warnings.push(`${context}: instance ${JSON.stringify(inst.name)} references unavailable definition - skipped`);
    return;
  }
  const matrix3x3 = inst.matrix.length >= 9 ? (inst.matrix.slice(0, 9) as Matrix3x3) : undefined;
  const translation: Point3 = inst.matrix.length >= 12 ? (inst.matrix.slice(9, 12) as Point3) : [0, 0, 0];
  const material = materialSlot(inst.materialId, model, materialSlots);
  const layer = inst.layer ? layerSlots.get(inst.layer) : undefined;
  try {
    target.addInstance(defBuilder, {
      name: inst.name || undefined,
      translation,
      matrix3x3,
      material,
      layer,
      hidden: inst.hidden,
      attributes: Object.keys(inst.properties).length > 0 ? inst.properties : undefined,
      attributeDictName: 'dynamic_attributes',
    });
  } catch (exc) {
    if (exc instanceof SkpWriteError) {
      warnings.push(`${context}: instance ${JSON.stringify(inst.name)} skipped (${exc.message})`);
    } else {
      throw exc;
    }
  }
}
