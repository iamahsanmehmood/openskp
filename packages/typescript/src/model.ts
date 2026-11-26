import { foldScale, transformPoint, multiplyMatrices } from './transforms';
import { extractAttributeDictionaries, isGenericDefinitionName, findNameOverride, stringifyVffAttrValue, summarizeFaceLayers, FaceLayerSummary, ParsedDefinition, VffAttrValue } from './geometry';
import {
  buildLocalFaceGroups,
  createTextureIndexFor,
  unpaintedStyleColors,
  type UnpaintedFacesMode,
} from './face-groups';
import { SkpParseError } from './errors';
import { ParseOptions, PROGRESS_INTERVAL, emitLog, emitProgress } from './observability';
import { RawPage, RawDimension } from './pages-dimensions';
import {
  CurveArc,
  LooseEdgeRun,
  PreparedDefCurves,
  INCHES_TO_M,
  orderCurveEdges,
  prepareDefinitionCurves,
  round6,
  solveRunArc,
} from './curves';

export type { CurveArc, LooseEdgeRun };

export interface SkpModel {
  version: string;
  definitions: Map<number, Definition>;
  /** The implicit top-level model definition: its `instances` are the
   * entities placed directly in the model (not inside any component/
   * group), and its `vertices`/`edges`/`faces` are geometry drawn directly
   * at the top level. Corresponds to .NET/Dart's `Root`/`root`. */
  root: Definition;
  layers: Layer[];
  /** The file's saved scenes, from VFF files and from classic pre-2021 files. */
  pages: Page[];
  /** Model-level linear dimensions with world-space endpoints (VFF
   * files). Legacy files surface text-only dimensions per definition
   * instead (`Definition.dimensions`). */
  dimensions: Dimension[];
  materials: Material[];
  materialsById: Map<number, Material>;
  styles: Style[];
  /** The colour SketchUp shows on the front side of a face with no paint on
   * that side, `[r, g, b]` 0-255: the model's current rendering options, which
   * is what SketchUp displays even when the style was changed and not saved
   * (so it can differ from every entry in {@link styles}). Read from 2021+
   * files and from classic files of SketchUp 4 to 2020; `null` when the file
   * does not give it (SketchUp 3). */
  faceFrontColor: [number, number, number] | null;
  /** The same for the back side of a face. */
  faceBackColor: [number, number, number] | null;
  /** Whether SketchUp's "Color by Tag" (`DisplayColorByLayer`) style setting is
   * enabled in the model's active rendering options. Read from 2021+ files and
   * from classic files of SketchUp 4 to 2020; `false` when off or when the
   * file does not give it (SketchUp 3). */
  colorByTag: boolean;
  /** The model's unit-system string (e.g. "Millimeter"), read from
   * meta/meta.dat in modern (VFF) files. null for legacy (pre-2021 MFC)
   * files, which carry no equivalent container, or when the tag isn't
   * found. */
  units: string | null;
  /** How many top-level entities of a classic (pre-2021) file the reader
   * couldn't read. It keeps what it read before the first root record it
   * can't parse and drops that record and everything after it, so a nonzero
   * count means the model lost geometry, components, groups, text or
   * dimensions. Phantom entries the root count includes but the file never
   * writes are not counted. Always 0 for VFF files. */
  unreadRootEntities: number;
}

export interface SectionPlane {
  plane: [number, number, number, number];
  name: string;
  label: string;
  hidden: boolean;
}

export interface TextEntity {
  text: string;
  hidden: boolean;
  /** Explicit tag name, or empty string for the default tag (Layer0). */
  layer?: string;
  /** Anchor and label positions in owner-local inches; null when unresolved. */
  point?: [number, number, number] | null;
  labelPoint?: [number, number, number] | null;
  /** Normalized screen fractions (used when leaderType is 0). */
  screenPosition?: [number, number] | null;
  leaderType?: number;
  arrowType?: number;
  displayLeader?: boolean;
}

/**
 * A linear dimension (SketchUp's Dimension tool).
 *
 * Both VFF (2021+) and legacy (pre-2021) readers expose owner-local geometry
 * and drawing properties on `root.dimensions` and `Definition.dimensions`;
 * `SkpModel.dimensions` also retains the world-space VFF compatibility list.
 */
export interface Dimension {
  /** The displayed text. Empty when the dimension shows its auto-computed
   * measured value (the caller formats `|b - a|`). */
  text: string;
  hidden: boolean;
  /** First measured point [x, y, z] in inches, or null when unresolved. */
  a: [number, number, number] | null;
  /** Second measured point. */
  b: [number, number, number] | null;
  /** Offset distance (inches) - how far the dimension line sits from the
   * a-b segment, along the in-plane perpendicular. */
  offset: number;
  /** Historical name: for a VFF dimension this is tag 5BCF, which is the plane NORMAL (SketchUp's SUDimensionLinearGetNormal equals it); for a legacy dimension this is the plane's X axis, or null. */
  planeX: [number, number, number] | null;
  /** Historical name: for a VFF dimension this is tag 5BD0, the plane's X axis; for a legacy dimension this is the plane normal, or null. */
  normal: [number, number, number] | null;
  /** Explicit offset vector (inches) for legacy or VFF dimensions constrained to a plane axis, preserving the side of the line independently of endpoint ordering, or null. */
  offsetVector?: [number, number, number] | null;
  /** Explicit tag name, or empty string for the default tag (Layer0). */
  layer?: string;
}

/**
 * A construction/guide line (SketchUp's Construction Line tool). Read from
 * both legacy (pre-2021) and VFF (2021+) files.
 *
 * Stored internally (and here, unchanged) as a point + normalized
 * direction + two signed distance parameters along that direction marking
 * where the visible segment starts/ends - the same shape
 * `Sketchup::ConstructionLine`'s own `start`/`end`/`direction` properties
 * expose. A parameter magnitude of `1e30` means unbounded in that
 * direction (SketchUp draws this as an infinite guide line through
 * `point`) - `start`/`end` come back null in that case, matching the real
 * API returning `nil`.
 */
export interface ConstructionLine {
  /** A point on the line, in inches (world space) - matches the bounded
   * case's own `start`, or the anchor point given for an infinite line. */
  point: [number, number, number];
  /** The line's normalized direction vector. */
  direction: [number, number, number];
  /** The bounded segment's start point, or null if unbounded in this
   * direction. */
  start: [number, number, number] | null;
  /** The bounded segment's end point, or null if unbounded. */
  end: [number, number, number] | null;
  /** Name of the layer (tag) the guide is assigned to, or "" for the
   * default layer (Layer0). */
  layer?: string;
  /** Whether the guide itself is hidden. */
  hidden?: boolean;
}

/** A construction/guide point (SketchUp's Construction Point tool).
 * Legacy and VFF (2021+) files are both supported. */
export interface ConstructionPoint {
  /** The point's position, in inches (world space). */
  position: [number, number, number];
  /** Name of the layer (tag) the guide is assigned to, or "" for the
   * default layer (Layer0). */
  layer?: string;
  /** Whether the guide itself is hidden. */
  hidden?: boolean;
}

/** Bits of {@link Page.captureFlags}, named after SketchUp's public Ruby API
 * (`Sketchup::Page#use_camera?` and so on). */
export const PAGE_USE_CAMERA = 0x001;
export const PAGE_USE_RENDERING_OPTIONS = 0x002;
export const PAGE_USE_SHADOW_INFO = 0x004;
export const PAGE_USE_AXES = 0x008;
export const PAGE_USE_HIDDEN = 0x010; // before SketchUp 2020.1: geometry + objects
export const PAGE_USE_HIDDEN_LAYERS = 0x020;
export const PAGE_USE_SECTION_PLANES = 0x040;
export const PAGE_USE_HIDDEN_GEOMETRY = 0x080;
export const PAGE_USE_HIDDEN_OBJECTS = 0x100;

/** Which properties a scene saves ("Properties to save" in SketchUp's Scenes
 * panel). `null` when the file doesn't say. */
export interface PageCaptureFlags {
  /** The u32 the file stores, a combination of the `PAGE_USE_*` bits. */
  captureFlags: number | null;
  /** The scene saves its camera ("Camera Location"), `0x001`. */
  useCamera: boolean | null;
  /** The scene saves its style ("Style and Fog"), `0x002`. */
  useRenderingOptions: boolean | null;
  /** The scene saves its shadows ("Shadow Settings"), `0x004`. */
  useShadowInfo: boolean | null;
  /** The scene saves the axes ("Axes Location"), `0x008`. */
  useAxes: boolean | null;
  /** The scene saves which layers it hides ("Visible Tags"), `0x020`. */
  useHiddenLayers: boolean | null;
  /** The scene saves the active section ("Active Section Planes"), `0x040`. */
  useSectionPlanes: boolean | null;
  /** The scene saves which edges and faces it hides ("Hidden Geometry"),
   * `0x080`, or `0x010` before SketchUp 2020.1. */
  useHiddenGeometry: boolean | null;
  /** The scene saves which groups and components it hides ("Hidden Objects"),
   * `0x100`, or `0x010` before SketchUp 2020.1. */
  useHiddenObjects: boolean | null;
}

/** The `use...` properties of a scene's {@link PageCaptureFlags.captureFlags}. */
export function pageCaptureFlags(captureFlags: number | null): PageCaptureFlags {
  const flag = (bit: number): boolean | null =>
    captureFlags === null ? null : (captureFlags & bit) !== 0;
  const split =
    captureFlags !== null &&
    (captureFlags & (PAGE_USE_HIDDEN_GEOMETRY | PAGE_USE_HIDDEN_OBJECTS)) !== 0;
  return {
    captureFlags,
    useCamera: flag(PAGE_USE_CAMERA),
    useRenderingOptions: flag(PAGE_USE_RENDERING_OPTIONS),
    useShadowInfo: flag(PAGE_USE_SHADOW_INFO),
    useAxes: flag(PAGE_USE_AXES),
    useHiddenLayers: flag(PAGE_USE_HIDDEN_LAYERS),
    useSectionPlanes: flag(PAGE_USE_SECTION_PLANES),
    useHiddenGeometry: flag(split ? PAGE_USE_HIDDEN_GEOMETRY : PAGE_USE_HIDDEN),
    useHiddenObjects: flag(split ? PAGE_USE_HIDDEN_OBJECTS : PAGE_USE_HIDDEN),
  };
}

/** A saved scene (SketchUp's "Scenes" tabs; "pages" in the SDK). */
export interface Page extends Partial<PageCaptureFlags> {
  /** Scene name as shown on its tab. */
  name: string;
  /** Camera position [x, y, z] in inches, or null. */
  eye: [number, number, number] | null;
  /** Point the camera looks at, in inches. */
  target: [number, number, number] | null;
  /** Camera up vector. */
  up: [number, number, number] | null;
  /** Field of view in degrees (SketchUp default 35). */
  fov: number;
  /** True when the scene uses parallel (orthographic) projection; `fov`
   * still holds the stored perspective angle. */
  parallel: boolean;
  /** Visible height in inches when `parallel`. */
  orthoHeight: number;
  /** Names of the layers this scene hides. */
  hiddenLayers: string[];
  /** Persistent ids of the entities this scene hides (groups, component
   * instances, and edges or faces hidden as geometry), in file order. */
  hiddenEntityIds?: number[];
}

export interface Definition {
  id: number;
  guid: string;
  name: string;
  vertices: Vertex[];
  edges: Edge[];
  faces: Face[];
  instances: Instance[];
  sectionPlanes: SectionPlane[];
  texts: TextEntity[];
  dimensions: Dimension[];
  constructionLines: ConstructionLine[];
  constructionPoints: ConstructionPoint[];
  /** The analytic arc of every curve that is one, keyed by curve id - the
   * {@link Edge.curveId} of the edges that form it. A curve that is not an
   * arc (a freehand or polygon curve) has no entry. */
  arcs: Map<number, ArcCurve>;
  isImage: boolean;
  /** True when SketchUp made this definition for a group rather than a
   * component (what its own API reports as `SUComponentType_Group`). Groups
   * and component instances are both `Instance` placements of a `Definition`;
   * this is the only thing that tells them apart. It comes from the file
   * itself - the definition kind in a 2021+ file, a flag in the definition
   * record in an older one - not from how often the definition is placed. */
  isGroup: boolean;
  alwaysFacesCamera: boolean;
  shadowsFaceSun: boolean;
  /** Native definition attributes from VFF files, preserving booleans, null and arrays. */
  attributeDictionaries?: Record<string, Record<string, VffAttrValue>>;
}

/**
 * Groups a definition's LOOSE edges (edges no face uses) into ordered
 * polyline runs, using the file's own `Edge#curve` (`Edge.curveId`) grouping.
 *
 * Returns `{ edgeIds, vertexIds, closed }` runs in the same order as
 * {@link buildInstancedScene}'s `curveResources`, so B-rep consumers working
 * directly with {@link parseSkp}'s typed model can resolve raw vertex/edge IDs.
 */
export function looseEdgeRuns(definition: Definition): LooseEdgeRun[] {
  const used = new Set<number>();
  for (const face of definition.faces) {
    for (const loop of face.loops) {
      for (const co of loop) {
        used.add(co.edgeId);
      }
    }
  }

  const edgesById = new Map<number, [number, number]>();
  const groups = new Map<number, number[]>();
  const runs: LooseEdgeRun[] = [];
  for (const e of definition.edges) {
    if (used.has(e.id) || e.v1Id === e.v2Id) continue;
    if (e.curveId) {
      edgesById.set(e.id, [e.v1Id, e.v2Id]);
      let grp = groups.get(e.curveId);
      if (!grp) {
        grp = [];
        groups.set(e.curveId, grp);
      }
      grp.push(e.id);
    } else {
      runs.push({ edgeIds: [e.id], vertexIds: [e.v1Id, e.v2Id], closed: false });
    }
  }

  const endsOf = (eid: number) => edgesById.get(eid);
  for (const eids of groups.values()) {
    runs.push(...orderCurveEdges(endsOf, eids));
  }
  return runs;
}

export interface Vertex {
  id: number;
  x: number;
  y: number;
  z: number;
}

export interface Edge {
  id: number;
  v1Id: number;
  v2Id: number;
  soft: boolean;
  smooth: boolean;
  hidden: boolean;
  /** SketchUp's own `Edge#curve` grouping: the id of the Curve this edge
   * belongs to, or null when it belongs to none. Edges sharing a `curveId`
   * were drawn as one continuous multi-segment line or arc; if the curve is
   * an arc, {@link Definition.arcs} has it under the same id. */
  curveId?: number | null;
  /** Native persistent ID; 0 when absent. */
  persistentId?: number;
  /** Name of the layer (tag) this edge is assigned to, or `""` when the edge
   * carries no explicit layer reference. */
  layer?: string;
}

/**
 * The analytic arc behind a curve: SketchUp's own `ArcCurve`.
 *
 * A curve SketchUp drew as an arc or a circle is a run of straight edges
 * ({@link Edge.curveId}) and also this exact description. The arc is the set
 * of points `center + cos(t) * xAxis + sin(t) * yAxis` for `t` from
 * `startAngle` to `endAngle`. For a circle `xAxis` and `yAxis` are
 * perpendicular and both of length `radius`; an arc scaled non-uniformly keeps
 * its arc identity and stores an affine image of a circle, where they are not
 * (then `radius` is the length of `xAxis` only). Arcs and circles that bound a
 * face are arcs too. Lengths are in the model's units (inches), in the
 * definition's local space, like {@link Vertex.x}.
 */
export interface ArcCurve {
  center: [number, number, number];
  /** Unit normal of the arc's plane. */
  normal: [number, number, number];
  /** Vector from the centre to the arc's start (length = radius). */
  xAxis: [number, number, number];
  /** `normal x xAxis`. */
  yAxis: [number, number, number];
  /** Start parameter in radians. */
  startAngle: number;
  /** End parameter in radians. A full circle has `endAngle === startAngle`
   * in a classic (pre-2021) file and `startAngle + 2 pi` in a 2021+ file;
   * {@link ArcCurve.isFullCircle} covers both. */
  endAngle: number;
  /** Length of `xAxis`. */
  radius: number;
  isFullCircle: boolean;
}

export interface Face {
  id: number;
  loops: CoEdge[][];
  normal: [number, number, number];
  /** Material of the face's FRONT side, or null. */
  materialId: number | null;
  /** Material of the face's BACK side, or null. */
  backMaterialId: number | null;
  /**
   * Per-face texture mapping for a positioned / photo-fitted texture
   * (SketchUp's pins), or null when the texture is untouched (default
   * projection applies). A 9-element array: a 3x3 row-major matrix mapping
   * texture space -> face plane. To compute the UV of a point p (inches):
   *
   * 1. Plane basis from the face normal n: xr = normalize(Z x n),
   *    yr = n x xr (for a vertical n: xr = X, yr = +-Y by the sign of n.Z).
   * 2. uvq = [p.xr, p.yr, 1] @ inv(M) (row-vector convention).
   * 3. u = uvq[0]/uvq[2] / tileW, v = uvq[1]/uvq[2] / tileH with the
   *    material texture's tile size in inches.
   *
   * When the texture is untouched (null), the default is
   * u = (p.xr)/tileW, v = (p.yr)/tileH. Distorted (4-pin) mappings are
   * projective: uvq[2] != 1.
   */
  uvTransform: number[] | null;
  /** Same for the face's back side, or null. */
  uvTransformBack: number[] | null;
  /** The texture is PROJECTED (e.g. the Add Location terrain drape): its
   * UVs run in the projection plane's frame, not the face frame. */
  uvProjected: boolean;
  /** Same for the face's back side. */
  uvProjectedBack: boolean;
  /** Whether the face is hidden (SketchUp's "Hide" on this specific face,
   * not a layer/tag visibility toggle). */
  hidden: boolean;
  /** Native persistent ID; 0 when absent. */
  persistentId?: number;
  /** Name of the layer (tag) this face is assigned to, or `""` when the face
   * carries no explicit layer reference (default layer). */
  layer?: string;
}

/**
 * Whether SketchUp itself would DRAW this edge.
 *
 * SketchUp hides three kinds of edge: `hidden` (explicitly hidden), and
 * `soft`/`smooth` (the smoothing flags that make a faceted surface read as
 * curved). The last two are why a rounded model carries far more edges
 * than it appears to: every curve is triangles stitched together by edges
 * that exist to define the shape and are never shown.
 *
 * The flags are parsed and exposed on {@link Edge}, but nothing in this
 * library acts on them - an edge-consuming consumer (a wireframe or
 * hidden-line renderer built on {@link parseSkp} output) has to make the
 * call itself, and `edge.soft || edge.smooth || edge.hidden` is not
 * obvious as "SketchUp does not draw this" unless you already know the
 * format. Hence this helper.
 *
 * Measured across this repository's fixtures, 27.3% of edges are
 * non-drawable on aggregate - but that ranges from 0.2% on a mostly-flat
 * model to 66.1% on a curved-surface one, so the saving is concentrated
 * exactly where geometry is heaviest.
 *
 * ```ts
 * const model = parseSkp(buffer);
 * const visible = model.root.edges.filter(isDrawableEdge);
 * ```
 */
export function isDrawableEdge(edge: Pick<Edge, 'soft' | 'smooth' | 'hidden'>): boolean {
  return !edge.soft && !edge.smooth && !edge.hidden;
}

export interface CoEdge {
  edgeId: number;
  orientation: number;
}

/** A placed instance (component or group) inside a Definition's own instance list. */
export interface Instance {
  id?: number | null;
  name: string;
  refIdx: number;
  guid: string;
  matrix: number[];
  /**
   * Material painted onto the instance itself (SketchUp's "paint the
   * component"), or null. Faces inside the placed definition whose own
   * Face.materialId is null inherit this material - consumers must resolve
   * that inheritance themselves, like the official SDK does on export.
   */
  materialId: number | null;
  /** Whether the instance itself is hidden (SketchUp's "Hide" on this
   * specific component/group placement, not a layer/tag visibility
   * toggle). */
  hidden: boolean;
  /** This instance's own explicit layer override, or `''` when it has
   * none. An instance without an explicit override inherits its
   * *placement's* layer, which can only be resolved once the scene graph
   * is flattened - see `buildScene`'s `InstanceNode.layer` for that
   * resolved value. Populated for legacy (pre-2021 MFC) files, where the
   * layer id is read directly off the instance's drawbase record and
   * resolved to a name here; always `''` for modern (VFF) files, which
   * this reader doesn't currently resolve a per-instance layer id for. */
  layer: string;
  /** Arbitrary key/value dynamic attributes attached directly to this
   * instance (SketchUp's Dynamic Components), or `{}`. Populated for
   * legacy (pre-2021 MFC) files (see legacy.ts's
   * extractLegacyDynamicProperties); always `{}` for modern (VFF) files,
   * whose per-instance properties are only resolved lazily during scene
   * baking (see buildScene's InstanceNode.properties) rather than at
   * parse time. */
  properties: Record<string, string>;
}

export interface Layer {
  name: string;
  color: { r: number; g: number; b: number };
  /** Whether the layer's visibility is switched off. Only populated for
   * legacy (pre-2021 MFC) files, where the byte is read directly from the
   * layer record - modern (VFF) files derive layers from
   * `Layer_<name>`-prefixed materials, which carry no visibility data, so
   * this is always `false` there. */
  hidden: boolean;
}

/** A material's texture image, extracted from the SKP container. */
export interface Texture {
  filename: string;
  width: number;
  height: number;
  data: Uint8Array | null;
}

/** A rendering style bundled in the file (SketchUp's Styles browser). */
export interface Style {
  name: string;
  frontColor: [number, number, number] | null;
  backColor: [number, number, number] | null;
}

export interface Material {
  name: string;
  color: { r: number; g: number; b: number; a: number };
  transparency: number;
  id: number | null;
  texture: Texture | null;
  colorized: boolean;
  colorizeType: number;
}

export interface InstanceNode {
  name: string;
  /** Whether `name` is a synthetic fallback (SketchUp's own internal
   * index, e.g. "Component_5") rather than a real name from the source
   * file - see InstancedNode.nameIsGenerated (instanced.ts) for the full
   * fallback-order rationale; both resolve a node's display name
   * identically. */
  nameIsGenerated: boolean;
  definitionName: string;
  layer: string;
  positionMm: [number, number, number];
  properties: Record<string, string>;
  /** Every OTHER attribute dictionary this instance carries, keyed by the
   * dictionary's own name, values stringified the same way `properties`
   * already is - `properties` stays exactly SketchUp's own Dynamic
   * Components data (`dynamic_attributes`) for backward compatibility;
   * third-party plugins (BIM/steel-detailing tools, etc.) commonly attach
   * their own richer per-instance data under their own dictionary name
   * instead, which this project never surfaced before (openskp#285). */
  attributeDictionaries: Record<string, Record<string, string>>;
  /** Real SketchUp instance GUID (VFF/2021+ files only), or `''` when the
   * source file has none - see InstancedNode.guid (instanced.ts). */
  guid: string;
  children: InstanceNode[];
}

export interface MeshMetadata {
  name: string;
  definitionName: string;
  layer: string;
  positionMm: [number, number, number];
  properties: Record<string, string>;
  /** See InstanceNode.attributeDictionaries. */
  attributeDictionaries: Record<string, Record<string, string>>;
  path: string;
}

/** One triangulated, world-space mesh: all faces (or, for a face whose
 * front/back colors genuinely differ, all *one side* of those faces)
 * sharing a single resolved color from one flattened scene-graph position.
 * Ready to hand straight to a GLB/glTF exporter or any other renderer. */
export interface GlbPrimitive {
  /** Flat [x, y, z, x, y, z, ...] vertex positions, in metres, Y-up. */
  positions: Float32Array;
  /** Flat [x, y, z, ...] vertex normals, matching `positions` 1:1. */
  normals: Float32Array;
  /** Flat [u, v, u, v, ...] texture coordinates, matching `positions` 1:1.
   * Computed from each source face's `uvTransform` (or the default
   * face-plane projection when a face has none) - see `Face.uvTransform`'s
   * docs for the formula. A vertex shared by two faces that disagree on UV
   * is split, since indexed glTF meshes need position/normal/uv aligned
   * per vertex. Faces with a PROJECTED texture (terrain-drape textures,
   * e.g. Add Location) still use the face-plane formula here, since the
   * real projection-plane basis isn't captured in the parsed data - their
   * UVs will be approximate. */
  uvs: Float32Array;
  /** Triangle vertex indices into `positions`/`normals`/`uvs` (3 per
   * triangle). */
  indices: Uint32Array;
  /** Triangle offsets for separately emitted back sides. A primitive can mix
   * front and back triangles when they share a material; their front
   * counterparts are still present. */
  backTriangles?: Uint32Array;
  /** Index into `gltfMaterials` for this primitive's resolved color. */
  materialIndex: number;
  /** Matches the corresponding key in `SkpScene.meshIndex`. */
  geomName: string;
  /** `true` for the back copies of faces with no paint on either side (the
   * front copies sit in another primitive). They exist only to show the
   * model's back face colour from behind; see {@link SkpScene.surfacePrimitives}. */
  unpaintedBack?: boolean;
}

/**
 * The result of baking a parsed file's placed instances into a flat,
 * world-space 3D scene: every instance's geometry triangulated and
 * transformed into its final position, ready for rendering or GLB export.
 *
 * This is deliberately a *separate*, opt-in step from {@link SkpModel} -
 * for a file with many repeated instances, baking the scene can produce far
 * more data than the file's raw (per-definition, un-instanced) geometry, so
 * callers who only need the raw model data never pay for it.
 */
/**
 * Axis-aligned bounds of a scene's geometry, in the same frame as the
 * vertex data it summarises: metres, glTF Y-up.
 */
export interface SceneBounds {
  min: [number, number, number];
  max: [number, number, number];
  /** `max - min` per axis. The model's overall size, which is what a
   * catalogue listing or a fit-to-view camera actually wants. */
  size: [number, number, number];
  /** Midpoint of `min` and `max`. */
  center: [number, number, number];
}

/** Metadata for one run of *loose* edges (edges no face uses), in world-space
 * metres in glTF's Y-up frame. */
export interface CurveSetMetadata {
  name: string;
  definitionName: string;
  layer: string;
  path: string;
  nameIsGenerated: boolean;
  pointsM: [number, number, number][];
  closed: boolean;
  arc: CurveArc | null;
  attributeDictionaries: Record<string, Record<string, string>>;
}

export interface SkpScene {
  /** The root of the world-space instance tree. */
  sceneHierarchy: InstanceNode;
  /** Metadata for every baked mesh, keyed the same as `glbPrimitives`'
   * `geomName`. */
  meshIndex: Record<string, MeshMetadata>;
  /** The actual triangulated mesh data, one entry per unique
   * (definition, resolved color) combination actually placed in the scene. */
  glbPrimitives: GlbPrimitive[];
  /** Loose-edge runs, one entry per chained polyline. Always populated by
   * {@link buildScene} (empty when all geometry is face-bounded or
   * `includeCurveSets` is `false`); optional so hand-constructed scene
   * literals for exporters do not need to supply it. */
  curveSets?: CurveSetMetadata[];
  /** {@link glbPrimitives} without the back copies of faces that have no paint
   * on either side: what single-colour surface exporters (STL, OBJ, PLY, DXF,
   * IFC) and triangle-counting callers use so every face appears once. Always
   * populated by {@link buildScene}; optional so hand-constructed scene
   * literals for exporters do not need to supply it. */
  surfacePrimitives?: GlbPrimitive[];
  /** glTF PBR material definitions referenced by `GlbPrimitive.materialIndex`.
   * A material whose source had a texture image carries a `baseColorTexture`
   * whose `index` points into {@link SkpScene.textures}. */
  gltfMaterials: unknown[];
  /** Axis-aligned bounds over every baked primitive, metres and Y-up, or
   * `null` when the scene has no geometry. Computed during the bake, so
   * reading it costs nothing extra - every consumer previously had to walk
   * the position buffers itself to get the model's size. */
  bounds: SceneBounds | null;
  /** The distinct texture images the placed materials use, deduplicated by
   * source bytes. Empty when nothing placed in the scene is textured.
   *
   * Kept out of `gltfMaterials` so a caller can decide whether to pay for
   * them: {@link toGLB} embeds these only when asked, since a model with a
   * handful of photographic textures is several times larger with them than
   * without. */
  textures: SceneTexture[];
  /** Number of top-level entities dropped when a classic (pre-2021) root
   * entity walk stopped early (`0` for complete classic files and VFF files).
   * Always populated by {@link buildScene}; optional so hand-constructed scene
   * literals for exporters do not need to supply it. */
  unreadRootEntities?: number;
  /** Saved scenes (SketchUp's "Scenes" tabs), identical to {@link SkpModel.pages}.
   * Camera `eye`, `target`, `up` and `orthoHeight` stay in SketchUp's native
   * Z-up frame and inches (unlike `glbPrimitives`, which are Y-up metres).
   * Always populated by {@link buildScene}; optional so hand-constructed scene
   * literals for exporters do not need to supply it. */
  pages?: Page[];
  /** Each layer's hidden/visible state by layer name, read from the parsed
   * file (`true` when hidden, `false` when visible). Always populated by
   * {@link buildScene}; optional so hand-constructed scene literals for
   * exporters do not need to supply it. */
  layerHidden?: Record<string, boolean>;
}

/** Options shared by {@link buildScene} and {@link buildInstancedScene}. */
export interface SceneOptions {
  /**
   * Bake loose edges (edges no face uses) into {@link SkpScene.curveSets} /
   * {@link InstancedScene.curveResources}. Defaults to `true`; pass `false`
   * to skip loose-edge chaining and arc solving.
   */
  includeCurveSets?: boolean;
  /**
   * Skip geometry SketchUp itself would not draw: faces carrying SketchUp's
   * "Hide" flag and non-drawable loose edges (`hidden`, `soft`, or `smooth`,
   * matching {@link isDrawableEdge}) in {@link SkpScene.curveSets} and
   * {@link InstancedScene.curveResources}. Face-bounding edges are already
   * represented by face triangles and are not emitted as curves regardless.
   *
   * Off by default: what SketchUp draws is a display policy, not a parsing
   * fact, and some consumers legitimately want every face and loose edge.
   */
  respectEdgeVisibility?: boolean;
  /**
   * Emit every face once, double-sided with its FRONT material, instead of
   * adding a reversed back-side copy when the face's two sides resolve to
   * different colours.
   *
   * For consumers that treat a face as one unoriented surface - modelers,
   * CAD and editing tools that rebuild topology from the mesh - where the
   * coincident reversed copy reads as a second face: it makes the shared
   * edges non-manifold and z-fights with the front. Back-side paint is lost.
   *
   * Off by default: a viewer wants each side's own colour.
   */
  frontSideOnly?: boolean;
  /**
   * Colour of a face side with no paint on it or on anything that contains it.
   * `'style'` (the default) is what SketchUp shows: the model's front face
   * colour on the front and its back face colour on the back
   * ({@link SkpModel.faceFrontColor} / {@link SkpModel.faceBackColor}). Where
   * the two differ, such a face is emitted as two single-sided sets and the
   * back one is marked `unpaintedBack`. `'tag'` gives both sides the colour of
   * the face's tag, in one double-sided set, as openskp did up to 1.6.0. A
   * file whose face colours cannot be read (SketchUp 3) always uses the tag
   * colour.
   */
  unpaintedFaces?: UnpaintedFacesMode;
  /**
   * Keep a texture that is not PNG or JPEG in {@link SkpScene.textures} and
   * {@link InstancedScene.textures} in its own format (`image/tiff`,
   * `image/gif`, `image/bmp`, `image/x-tga`, `image/webp`,
   * `image/vnd.adobe.photoshop`, `image/vnd.ms-dds`, `image/jp2`), instead of
   * leaving it out with a warning. Off by default because glTF carries only
   * PNG and JPEG.
   */
  originalTextures?: boolean;
}

export type { UnpaintedFacesMode };

/** What the single-colour exporters write. Computed from `glbPrimitives`, so a
 * scene whose primitives a caller replaced or filtered exports those. */
export function getSurfacePrimitives(scene: SkpScene): GlbPrimitive[] {
  return scene.glbPrimitives.filter((p) => !p.unpaintedBack);
}

/**
 * Image type from the file's magic bytes. glTF only carries PNG and JPEG, so
 * anything else (TIFF from older SketchUp, say) reports null and the material
 * keeps its flat colour instead of embedding an image no viewer would read.
 */
export function sniffImageMime(data: Uint8Array): 'image/png' | 'image/jpeg' | null {
  if (data.length >= 3 && data[0] === 0xff && data[1] === 0xd8 && data[2] === 0xff) {
    return 'image/jpeg';
  }
  if (
    data.length >= 8 &&
    data[0] === 0x89 && data[1] === 0x50 && data[2] === 0x4e && data[3] === 0x47 &&
    data[4] === 0x0d && data[5] === 0x0a && data[6] === 0x1a && data[7] === 0x0a
  ) {
    return 'image/png';
  }
  return null;
}

/** One texture image referenced by {@link SkpScene.gltfMaterials}. */
export interface SceneTexture {
  /** The image file's raw bytes, exactly as they were stored in the .skp. */
  data: Uint8Array;
  /** Sniffed from the bytes, not from `filename`: SketchUp records the
   * authoring machine's path, whose extension can disagree with the content.
   * `image/png` or `image/jpeg` unless built with `originalTextures: true`,
   * which also keeps `image/tiff`, `image/gif`, `image/bmp`, `image/x-tga`,
   * `image/webp`, `image/vnd.adobe.photoshop`, `image/vnd.ms-dds`, and
   * `image/jp2`. */
  mimeType: string;
  /** The material's texture path as recorded in the file. Informational: it
   * is usually an absolute path on the machine that authored the model. */
  filename: string;
}

/** Raw parsed data, source-agnostic (populated by either the VFF/ZIP path
 * in index.ts or the legacy MFC walker in legacy.ts), that
 * {@link buildModelFromParsed} turns into the final public
 * {@link SkpModel} - including scene-hierarchy resolution and GLB
 * primitive building, which both formats share. */
export interface ParsedRawData {
  version: string;
  /** The model's unit-system string (e.g. "Millimeter"), read from
   * meta/meta.dat. null for legacy files or when the tag isn't found. */
  units: string | null;
  layerColors: Map<string, [number, number, number]>;
  layerHidden: Map<string, boolean>;
  layerIdToName: Map<number, string>;
  pages: RawPage[];
  dimensions: RawDimension[];
  materialIdToName: Map<number, string>;
  materialsMap: Map<string, Material>;
  materialsByFolder: Map<string, Material>;
  styles: Style[];
  faceColors?: [[number, number, number], [number, number, number]] | null;
  colorByTag?: boolean;
  defsDict: Map<number | string, ParsedDefinition>;
  unreadRootEntities?: number;
}

export function buildModelFromParsed(parsed: ParsedRawData): SkpModel {
  const {
    version,
    units,
    layerColors,
    layerHidden,
    layerIdToName,
    dimensions,
    materialIdToName,
    materialsMap,
    materialsByFolder,
    styles,
    defsDict,
  } = parsed;

  // Join the TLV material IDs (what Face.materialId references) onto the
  // parsed materials, so callers can resolve face -> material.
  // materialsMap/materialsByFolder may share the same Material object
  // reference for an alias, so setting `.id` here is visible through both.
  const materialsById = new Map<number, Material>();
  for (const [mId, mName] of materialIdToName.entries()) {
    const mat = materialsMap.get(mName) || materialsByFolder.get(mName);
    if (!mat) continue;
    if (mat.id === null) {
      mat.id = mId;
    }
    materialsById.set(mId, mat);
  }

  const finalLayersList: Layer[] = Array.from(layerColors.entries()).map(([name, c]) => ({
    name,
    color: { r: c[0], g: c[1], b: c[2] },
    hidden: layerHidden.get(name) ?? false,
  }));

  const finalMaterialsList: Material[] = Array.from(materialsMap.values());

  // Convert pages (saved scenes) - hidden layer ids resolve to names;
  // unknown ids (stale refs) are dropped.
  const finalPagesList: Page[] = buildPagesFromRaw(parsed);

  // Convert model-level linear dimensions (VFF; world space).
  const finalDimensionsList: Dimension[] = dimensions.map((dm) => ({
    a: dm.a,
    b: dm.b,
    offset: dm.offset,
    planeX: dm.planeX,
    normal: dm.normal,
    offsetVector: dm.offsetVector ?? null,
    text: dm.text,
    hidden: dm.hidden ?? false,
    layer: resolveAnnotationLayer(dm.layer, undefined, layerIdToName),
  }));

  const finalDefinitions = new Map<number, Definition>();
  let rootDefinition: Definition | null = null;
  for (const [id, d] of defsDict.entries()) {
    const defn = buildDefinition(typeof id === 'number' ? id : 0, d, layerIdToName);
    if (typeof id === 'number') {
      finalDefinitions.set(id, defn);
    } else {
      rootDefinition = defn;
    }
  }

  return {
    version,
    definitions: finalDefinitions,
    // The implicit top-level model definition: its instances are the
    // entities placed directly in the model (not inside any component/
    // group). Kept out of `definitions` (which is numeric-ID-only, one
    // entry per real component/group definition) and exposed here instead,
    // matching the .NET and Dart ports' `Root`/`root` field.
    root: rootDefinition ?? { id: 0, guid: 'ROOT', name: 'ROOT_MODEL', vertices: [], edges: [], faces: [], instances: [], sectionPlanes: [], texts: [], dimensions: [], constructionLines: [], constructionPoints: [], arcs: new Map(), isImage: false, isGroup: false, alwaysFacesCamera: false, shadowsFaceSun: false, attributeDictionaries: {} },
    layers: finalLayersList,
    pages: finalPagesList,
    dimensions: finalDimensionsList,
    materials: finalMaterialsList,
    materialsById,
    styles,
    faceFrontColor: parsed.faceColors?.[0] ?? null,
    faceBackColor: parsed.faceColors?.[1] ?? null,
    colorByTag: Boolean(parsed.colorByTag),
    units,
    unreadRootEntities: parsed.unreadRootEntities ?? 0,
  };
}

function resolveAnnotationLayer(
  rawLayer?: string,
  rawLayerId?: number | null,
  layerIdToName?: Map<number, string>
): string {
  if (rawLayerId != null && rawLayerId !== 0) {
    return layerIdToName?.get(rawLayerId) ?? String(rawLayerId);
  }
  if (!rawLayer) return '';
  const num = Number(rawLayer);
  if (Number.isInteger(num) && String(num) === rawLayer) {
    if (num === 0) return '';
    return layerIdToName?.get(num) ?? rawLayer;
  }
  return rawLayer;
}

function buildDefinition(id: number, d: ParsedDefinition, layerIdToName?: Map<number, string>): Definition {
  const isLegacy = d.builder.edgePids !== undefined;
  const vertices: Vertex[] = Array.from(d.builder.vertices.entries()).map(([vId, [x, y, z]]) => ({
    id: vId,
    x,
    y,
    z,
  }));
  const edges: Edge[] = Array.from(d.builder.edges.entries()).map(([eId, [v1, v2]]) => {
    const flags = d.builder.edgeFlags.get(eId) ?? 0;
    const edgeLayerId = d.builder.edgeLayers?.get(eId);
    return {
      id: eId,
      v1Id: v1 ?? 0,
      v2Id: v2 ?? 0,
      soft: (flags & 0x08) !== 0,
      smooth: (flags & 0x10) !== 0,
      hidden: (flags & 0x01) !== 0,
      curveId: d.builder.edgeCurves?.get(eId) ?? null,
      persistentId: isLegacy ? (d.builder.edgePids?.get(eId) ?? 0) : eId,
      layer: edgeLayerId != null ? (layerIdToName?.get(edgeLayerId) ?? String(edgeLayerId)) : '',
    };
  });
  const arcs = new Map<number, ArcCurve>();
  for (const [curveId, f] of d.builder.arcCurves ?? []) {
    const radius = Math.hypot(f.xAxis[0], f.xAxis[1], f.xAxis[2]);
    const sweep = (((f.endAngle - f.startAngle) % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
    arcs.set(curveId, {
      center: [...f.center] as [number, number, number],
      normal: [...f.normal] as [number, number, number],
      xAxis: [...f.xAxis] as [number, number, number],
      yAxis: [...f.yAxis] as [number, number, number],
      startAngle: f.startAngle,
      endAngle: f.endAngle,
      radius,
      isFullCircle: Math.min(sweep, 2 * Math.PI - sweep) < 1e-9,
    });
  }
  const faces: Face[] = Array.from(d.builder.faces.entries()).map(([fId, fData]) => ({
    id: fId,
    loops: fData.loops,
    normal: fData.normal,
    materialId: fData.materialId ?? null,
    backMaterialId: fData.backMaterialId ?? null,
    uvTransform: fData.uvTransform ?? null,
    uvTransformBack: fData.uvTransformBack ?? null,
    uvProjected: fData.uvProjected ?? false,
    uvProjectedBack: fData.uvProjectedBack ?? false,
    hidden: fData.hidden ?? false,
    persistentId: isLegacy ? (fData.persistentId ?? 0) : fId,
    layer: fData.layerId != null ? (layerIdToName?.get(fData.layerId) ?? String(fData.layerId)) : '',
  }));
  const instances: Instance[] = d.builder.instances.map((inst) => ({
    id: inst.id ?? null,
    name: inst.name,
    refIdx: inst.refIdx,
    guid: inst.refGuid,
    matrix: inst.matrix,
    materialId: inst.materialId,
    hidden: inst.hidden ?? false,
    layer: (inst.layerId != null ? layerIdToName?.get(inst.layerId) : undefined) ?? '',
    properties: inst.properties ?? {},
  }));

  const sectionPlanes: SectionPlane[] = (d.builder.sectionPlanes || []).map((sp) => ({
    plane: sp.plane || [0, 0, 1, 0],
    name: sp.name || '',
    label: sp.label || '',
    hidden: sp.hidden || false,
  }));
  const texts: TextEntity[] = (d.builder.texts || []).map((txt) => ({
    text: txt.text || '',
    hidden: txt.hidden || false,
    layer: resolveAnnotationLayer(txt.layer, txt.layerId, layerIdToName),
    point: txt.point ?? null,
    labelPoint: txt.labelPoint ?? null,
    screenPosition: txt.screenPosition ?? null,
    leaderType: txt.leaderType ?? 2,
    arrowType: txt.arrowType ?? 3,
    displayLeader: txt.displayLeader ?? true,
  }));
  const dimensions: Dimension[] = (d.builder.dimensions || []).map((dim) => ({
    text: dim.text || '',
    hidden: dim.hidden || false,
    a: dim.a ?? null,
    b: dim.b ?? null,
    offset: dim.offset ?? 0,
    planeX: dim.planeX ?? null,
    normal: dim.normal ?? null,
    offsetVector: dim.offsetVector ?? null,
    layer: resolveAnnotationLayer(dim.layer, dim.layerId, layerIdToName),
  }));
  const constructionLines: ConstructionLine[] = (d.builder.constructionLines || []).map((cl) => ({
    point: cl.point,
    direction: cl.direction,
    start: cl.start,
    end: cl.end,
    layer: (cl.layerId != null ? layerIdToName?.get(cl.layerId) : undefined) ?? '',
    hidden: cl.hidden ?? false,
  }));
  const constructionPoints: ConstructionPoint[] = (d.builder.constructionPoints || []).map((cp) => ({
    position: cp.position,
    layer: (cp.layerId != null ? layerIdToName?.get(cp.layerId) : undefined) ?? '',
    hidden: cp.hidden ?? false,
  }));

  return {
    id,
    guid: d.guid,
    name: d.name,
    vertices,
    edges,
    faces,
    instances,
    sectionPlanes,
    texts,
    dimensions,
    constructionLines,
    constructionPoints,
    arcs,
    isImage: d.isImage,
    isGroup: d.isGroup || false,
    alwaysFacesCamera: d.alwaysFacesCamera,
    shadowsFaceSun: d.shadowsFaceSun || false,
    attributeDictionaries: d.attributeDictionaries ?? {},
  };
}

/** Inverse of a row-major 3x3 matrix, via the cofactor/adjugate method. */
function invertMatrix3x3(m: number[]): number[] {
  const [a, b, c, d, e, f, g, h, i] = m;
  const det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g);
  if (Math.abs(det) < 1e-12) {
    return [1, 0, 0, 0, 1, 0, 0, 0, 1];
  }
  const invDet = 1 / det;
  return [
    (e * i - f * h) * invDet, (c * h - b * i) * invDet, (b * f - c * e) * invDet,
    (f * g - d * i) * invDet, (a * i - c * g) * invDet, (c * d - a * f) * invDet,
    (d * h - e * g) * invDet, (b * g - a * h) * invDet, (a * e - b * d) * invDet,
  ];
}

/** Below this `|Z x n|` length, SketchUp treats a face as horizontal
 * (measured against SketchUp 2025's SDK: 9.9e-4 snaps, 1.0e-3 turns). */
const UV_VERTICAL_TOLERANCE = 1e-3;

/** Face-plane basis vectors (xr, yr) for UV projection, from a face
 * normal. See `Face.uvTransform`'s docs for the recipe this implements.
 * Exported for create.ts, edit.ts and codegen.ts, which need the identical
 * basis a face's uvTransform is defined against. */
export function faceUvBasis(n: [number, number, number]): { xr: [number, number, number]; yr: [number, number, number] } {
  const [nx, ny, nz] = n;
  const cx = -ny;
  const cy = nx;
  const clen = Math.sqrt(cx * cx + cy * cy);
  let xr: [number, number, number];
  let yr: [number, number, number];
  if (clen < UV_VERTICAL_TOLERANCE) {
    xr = nz >= 0 ? [1, 0, 0] : [-1, 0, 0];
    yr = [0, 1, 0];
  } else {
    xr = [cx / clen, cy / clen, 0];
    yr = [ny * xr[2] - nz * xr[1], nz * xr[0] - nx * xr[2], nx * xr[1] - ny * xr[0]];
  }
  return { xr, yr };
}

/** UV of point p (inches, local/object space) on a face with the given
 * plane basis, per-face uvTransform (or null for the default projection),
 * and material tile size (inches). Exported for edit.ts's own UV replay. */
export function computeFaceUv(
  p: [number, number, number],
  xr: [number, number, number],
  yr: [number, number, number],
  uvTransform: number[] | null | undefined,
  tileW: number,
  tileH: number
): [number, number] {
  const px = p[0] * xr[0] + p[1] * xr[1] + p[2] * xr[2];
  const py = p[0] * yr[0] + p[1] * yr[1] + p[2] * yr[2];
  if (!uvTransform) {
    return [px / tileW, py / tileH];
  }
  const inv = invertMatrix3x3(uvTransform);
  const u = px * inv[0] + py * inv[3] + inv[6];
  const v = px * inv[1] + py * inv[4] + inv[7];
  let q = px * inv[2] + py * inv[5] + inv[8];
  if (Math.abs(q) < 1e-12) q = 1;
  return [u / q / tileW, v / q / tileH];
}

/**
 * Bake every instance actually placed in the model into world-space,
 * triangulated mesh data - SketchUp's component/group nesting fully
 * resolved and flattened, ready for a GLB export or any other renderer.
 *
 * This walks the *entire* placed scene graph, so for a file that reuses a
 * handful of definitions across many thousands of instances, the output
 * here can be far larger than the file's raw (un-instanced) geometry -
 * that's why it's a separate, opt-in step from {@link buildModelFromParsed}
 * rather than something every parse() pays for.
 */
export function buildSceneFromParsed(
  parsed: ParsedRawData,
  options?: ParseOptions & SceneOptions
): SkpScene {
  const t0 = Date.now();
  const {
    layerColors,
    layerHidden,
    layerIdToName,
    materialIdToName,
    materialsMap,
    materialsByFolder,
    defsDict,
  } = parsed;

  emitLog(options, 'info', `Building scene: ${defsDict.size} definitions available`);
  const instanceCounter = { count: 0 };

  // Instantiate scene hierarchy and gather mesh metadata & GLB primitives
  const meshCounter = { count: 0 };
  const meshIndex: Record<string, MeshMetadata> = {};
  const glbPrimitives: any[] = [];
  const curveSets: CurveSetMetadata[] = [];
  const curveSeq = new Map<string, number>();
  const includeCurveSets = options?.includeCurveSets !== false;
  const respectEdgeVis = options?.respectEdgeVisibility === true;
  const preparedCurvesCache = new Map<number | string, PreparedDefCurves>();

  // Instance path -> (properties, name) updates, recorded in O(1) per
  // instance and applied once after instantiation completes (see the
  // lookup pass below), instead of scanning the entire meshIndex per
  // placed instance - an O(instances x meshes) substring scan that both
  // dominated build_scene on models with many placed instances and could
  // match the wrong meshes (a shallow instance's path is always a string
  // prefix of every deeper descendant's path too, so `includes()` matched
  // far more than intended - see openskp#240).
  const pathUpdates = new Map<
    string,
    { properties: Record<string, string>; name: string; attributeDictionaries: Record<string, Record<string, string>> }
  >();

  const getLayerColor = (name: string) => {
    const c = layerColors.get(name) || [136, 136, 136];
    return { r: c[0], g: c[1], b: c[2] };
  };

  const styleColors = unpaintedStyleColors(parsed.faceColors, options?.unpaintedFaces);

  // Textures are deduplicated by full content (with an object-identity fast
  // path per face): the same image routinely backs several materials, while
  // equal-size PNGs/TIFFs share their 16-byte header and must stay distinct.
  const textures: SceneTexture[] = [];
  const textureIndexFor = createTextureIndexFor(textures, options);

  const colorToMaterialIndex = new Map<string, number>();
  const gltfMaterials: any[] = [];

  // Accumulated while positions are written, so bounds cost no extra pass
  // over the vertex data.
  const boundsMin: [number, number, number] = [Infinity, Infinity, Infinity];
  const boundsMax: [number, number, number] = [-Infinity, -Infinity, -Infinity];

  // Definitions currently being instantiated on the active recursion path
  // (not "ever visited" - the same definition legitimately reused by
  // sibling instances is fine). Guards against a component that directly
  // or transitively instances itself, which would otherwise recurse until
  // the stack overflows.
  const activeDefinitions = new Set<number | string>();

  function getMaterialIndex(
    color: { r: number; g: number; b: number },
    doubleSided: boolean,
    textureIndex: number | null,
    transparency: number = 1.0,
    colorized: boolean = false,
    colorizeType: number = 0
  ) {
    // The texture is part of the identity, not just the colour: two different
    // images can average to the same RGB (real files do this - two fabrics
    // both resolving to 141,141,141), and keying on colour alone would merge
    // them into one material and lose one of the images.
    const effectiveColorized = colorized && textureIndex !== null;
    const effectiveColorizeType = effectiveColorized ? colorizeType : 0;
    const key = `${color.r},${color.g},${color.b},${doubleSided},${textureIndex ?? -1},${transparency},${effectiveColorized ? 1 : 0},${effectiveColorizeType}`;
    if (colorToMaterialIndex.has(key)) {
      return colorToMaterialIndex.get(key)!;
    }
    const idx = gltfMaterials.length;
    const pbr: any = {
      baseColorFactor: [color.r / 255, color.g / 255, color.b / 255, transparency],
      metallicFactor: 0.0,
      roughnessFactor: 0.8,
    };
    // baseColorFactor stays as the resolved colour even with a texture
    // attached: glTF multiplies the two, and SketchUp's own colorized
    // materials rely on exactly that tint. Overwriting it with white would
    // also drop the colour every existing consumer of this exporter reads.
    if (textureIndex !== null) {
      pbr.baseColorTexture = { index: textureIndex };
    }
    const material: any = { pbrMetallicRoughness: pbr };
    if (effectiveColorized) {
      material.extras = {
        sketchup: { colorized: true, colorize_type: effectiveColorizeType },
      };
    }
    if (doubleSided) material.doubleSided = true;
    // glTF's default alphaMode is OPAQUE, which tells a conformant renderer
    // to ignore alpha entirely - both the material's own opacity and any
    // texture's alpha channel. Genuinely translucent materials (glass,
    // water) need BLEND so baseColorFactor's alpha (and the texture's, if
    // any) actually takes effect. A textured-but-otherwise-opaque material
    // gets MASK instead: many SketchUp Warehouse assets (tree foliage,
    // fences, signage) rely on the image's own alpha channel to cut a
    // shape out of an otherwise flat quad, and without MASK a renderer
    // would show the full rectangle. MASK is a no-op for a texture with no
    // real cutout - a fully-opaque alpha channel (or none, as in JPEG)
    // stays above the cutoff everywhere - so this is safe to set
    // unconditionally rather than trying to detect which textures need it.
    if (transparency < 1.0) {
      material.alphaMode = 'BLEND';
    } else if (textureIndex !== null) {
      material.alphaMode = 'MASK';
    }
    gltfMaterials.push(material);
    colorToMaterialIndex.set(key, idx);
    return idx;
  }

  const resolveFaceTagColor = (layerId: number | null | undefined) => {
    const fname = layerId ? (layerIdToName.get(layerId) ?? '') : '';
    return fname && fname !== 'Layer0' ? getLayerColor(fname) : null;
  };

  const faceLayerCache = new Map<number | string, FaceLayerSummary>();
  function getFaceLayerSummary(defId: number | string): FaceLayerSummary {
    let hit = faceLayerCache.get(defId);
    if (hit !== undefined) return hit;
    const d = defsDict.get(defId);
    hit = d ? summarizeFaceLayers(d.builder, layerIdToName) : { uniformLayer: null, hasUntaggedFace: false };
    faceLayerCache.set(defId, hit);
    return hit;
  }

  function instantiate(
    defId: number | string,
    currentMatrix: number[],
    parentLayer: string = 'Layer0',
    pathName: string = 'ROOT',
    inheritedMaterial?: Material
  ): InstanceNode[] {
    const d = defsDict.get(defId);
    if (!d) return [];

    const builder = d.builder;
    const summary = getFaceLayerSummary(defId);
    const meshLayer =
      parentLayer === 'Layer0' ? (summary.uniformLayer ?? parentLayer) : parentLayer;

    if (builder.faces.size > 0) {
      // Group faces sharing a resolved (color, doubleSided) pair into one
      // mesh each - same grouping the C++ reference uses (the only port
      // that already had this before this port): a face whose front/back
      // resolve to the SAME color is emitted once, with its glTF material
      // marked doubleSided so it's visible from either side without
      // needing duplicate geometry; a face whose front/back genuinely
      // differ is emitted as TWO single-sided triangle sets - one
      // normal-wound using the front material, one reverse-wound using
      // the back material - so each side renders its own correct color
      // instead of the front material leaking onto (or the back
      // vanishing from) the far side.
      //
      // The grouping itself lives in face-groups.ts and is shared verbatim
      // with buildInstancedSceneFromParsed: it is entirely local-space
      // work, and the ONLY difference between the two paths is what
      // happens next - here each group's vertices get `currentMatrix`
      // applied and become a world-space primitive, while the instanced
      // path leaves them local and puts the transform on the node.
      const [fallbackColor, fallbackBackColor] =
        styleColors ??
        (() => {
          const c = getLayerColor(parentLayer);
          return [c, c] as const;
        })();
      const faceGroups = buildLocalFaceGroups(builder, {
        resolveMaterial: (matId) =>
          resolveMaterialFromMaps(matId, materialIdToName, materialsMap, materialsByFolder),
        textureIndexFor,
        inheritedMaterial,
        fallbackLayerColor: fallbackColor,
        fallbackBackColor,
        faceTagColor: !inheritedMaterial && !styleColors ? resolveFaceTagColor : undefined,
        definitionId: defId,
        respectVisibility: options?.respectEdgeVisibility,
        frontSideOnly: options?.frontSideOnly,
      });

      for (const group of faceGroups.values()) {
        if (group.localFaces.length === 0) continue;

        const isRoot = pathName === 'ROOT';
        const tx = isRoot ? 0 : (currentMatrix[9] ?? 0) * 25.4;
        const ty = isRoot ? 0 : (currentMatrix[10] ?? 0) * 25.4;
        const tz = isRoot ? 0 : (currentMatrix[11] ?? 0) * 25.4;

        let safePath = pathName.replace(/ \/ /g, '__').replace(/ /g, '_');
        if (safePath.length > 80) safePath = safePath.slice(0, 80);

        const colorSuffix =
          faceGroups.size > 1
            ? `_${group.color.r}_${group.color.g}_${group.color.b}_${group.doubleSided ? 'ds' : 'ss'}`
            : '';
        const geomName = `mesh_${meshCounter.count}_${safePath}_${meshLayer}${colorSuffix}`;
        meshCounter.count++;

        meshIndex[geomName] = {
          name: isRoot ? 'ROOT' : pathName.split(' / ').pop() || '',
          definitionName: d.name || '',
          layer: meshLayer,
          positionMm: [Math.round(tx * 100) / 100, Math.round(ty * 100) / 100, Math.round(tz * 100) / 100],
          properties: {},
          attributeDictionaries: {},
          path: pathName,
        };

        const scale = 0.0254;
        const positions = new Float32Array(group.localVerts.length * 3);
        const normals = new Float32Array(group.localVerts.length * 3);
        const uvs = new Float32Array(group.localVerts.length * 2);
        const vertexNormalsAccum = group.normalsAccum;

        for (let i = 0; i < group.localVerts.length; i++) {
          const v = group.localVerts[i];
          const pt = transformPoint(currentMatrix, v);
          positions[i * 3] = pt[0] * scale;
          positions[i * 3 + 1] = pt[2] * scale;
          positions[i * 3 + 2] = -pt[1] * scale;

          // Read back out of the Float32Array rather than using the float64
          // values above: bounds must describe the vertex data as STORED, so
          // that a consumer sweeping `positions` itself gets the identical
          // answer rather than one off by a float32 ulp.
          const wx = positions[i * 3];
          const wy = positions[i * 3 + 1];
          const wz = positions[i * 3 + 2];

          if (wx < boundsMin[0]) boundsMin[0] = wx;
          if (wy < boundsMin[1]) boundsMin[1] = wy;
          if (wz < boundsMin[2]) boundsMin[2] = wz;
          if (wx > boundsMax[0]) boundsMax[0] = wx;
          if (wy > boundsMax[1]) boundsMax[1] = wy;
          if (wz > boundsMax[2]) boundsMax[2] = wz;

          uvs[i * 2] = group.localUvs[i][0];
          uvs[i * 2 + 1] = group.localUvs[i][1];

          const rawNorm = vertexNormalsAccum[i];
          const normLen = Math.sqrt(rawNorm[0] ** 2 + rawNorm[1] ** 2 + rawNorm[2] ** 2);
          const n = normLen > 1e-6 ? [rawNorm[0] / normLen, rawNorm[1] / normLen, rawNorm[2] / normLen] : [0, 0, 1];

          const nx = currentMatrix[0] * n[0] + currentMatrix[1] * n[1] + currentMatrix[2] * n[2];
          const ny = currentMatrix[3] * n[0] + currentMatrix[4] * n[1] + currentMatrix[5] * n[2];
          const nz = currentMatrix[6] * n[0] + currentMatrix[7] * n[1] + currentMatrix[8] * n[2];

          const l = Math.sqrt(nx * nx + ny * ny + nz * nz);
          if (l > 1e-6) {
            normals[i * 3] = nx / l;
            normals[i * 3 + 1] = nz / l;
            normals[i * 3 + 2] = -ny / l;
          } else {
            normals[i * 3] = 0;
            normals[i * 3 + 1] = 1;
            normals[i * 3 + 2] = 0;
          }
        }

        const indices = new Uint32Array(group.localFaces.length * 3);
        for (let i = 0; i < group.localFaces.length; i++) {
          indices[i * 3] = group.localFaces[i][0];
          indices[i * 3 + 1] = group.localFaces[i][1];
          indices[i * 3 + 2] = group.localFaces[i][2];
        }

        const materialIndex = getMaterialIndex(
          group.color,
          group.doubleSided,
          group.textureIndex,
          group.transparency,
          group.colorized,
          group.colorizeType
        );

        glbPrimitives.push({
          positions,
          normals,
          uvs,
          indices,
          backTriangles: new Uint32Array(group.backTriangles),
          materialIndex,
          geomName,
          unpaintedBack: group.unpaintedBack,
        });
      }
    }

    if (includeCurveSets) {
      let prepared = preparedCurvesCache.get(defId);
      if (prepared === undefined) {
        prepared = prepareDefinitionCurves(builder, layerIdToName, respectEdgeVis);
        preparedCurvesCache.set(defId, prepared);
      }
      if (prepared.runs.length > 0) {
        const fm = currentMatrix && currentMatrix.length >= 12 ? foldScale(currentMatrix) : null;
        const defNameStr = d.name || '';
        const compName = pathName === 'ROOT' ? '' : pathName.split(' / ').pop() || '';
        for (const run of prepared.runs) {
          const pts: [number, number, number][] = new Array(run.localVerts.length);
          if (!fm) {
            for (let i = 0; i < run.localVerts.length; i++) {
              const v = run.localVerts[i];
              pts[i] = [
                round6(v[0] * INCHES_TO_M),
                round6(v[2] * INCHES_TO_M),
                round6(-v[1] * INCHES_TO_M),
              ];
            }
          } else if (fm.length === 16) {
            for (let i = 0; i < run.localVerts.length; i++) {
              const v = run.localVerts[i];
              const px = fm[0] * v[0] + fm[4] * v[1] + fm[8] * v[2] + fm[12];
              const py = fm[1] * v[0] + fm[5] * v[1] + fm[9] * v[2] + fm[13];
              const pz = fm[2] * v[0] + fm[6] * v[1] + fm[10] * v[2] + fm[14];
              pts[i] = [
                round6(px * INCHES_TO_M),
                round6(pz * INCHES_TO_M),
                round6(-py * INCHES_TO_M),
              ];
            }
          } else {
            for (let i = 0; i < run.localVerts.length; i++) {
              const v = run.localVerts[i];
              const px = fm[0] * v[0] + fm[1] * v[1] + fm[2] * v[2] + fm[9];
              const py = fm[3] * v[0] + fm[4] * v[1] + fm[5] * v[2] + fm[10];
              const pz = fm[6] * v[0] + fm[7] * v[1] + fm[8] * v[2] + fm[11];
              pts[i] = [
                round6(px * INCHES_TO_M),
                round6(pz * INCHES_TO_M),
                round6(-py * INCHES_TO_M),
              ];
            }
          }

          const runLayer = run.explicitLayer ?? meshLayer;
          let cname: string;
          let cgen: boolean;
          if (pathName === 'ROOT') {
            const seq = (curveSeq.get(runLayer) ?? 0) + 1;
            curveSeq.set(runLayer, seq);
            cname = `${runLayer}_${seq}`;
            cgen = true;
          } else {
            cname = compName;
            cgen = false;
          }

          curveSets.push({
            name: cname,
            nameIsGenerated: cgen,
            definitionName: defNameStr,
            layer: runLayer,
            path: pathName,
            pointsM: pts,
            closed: run.closed,
            arc: solveRunArc(builder, run.edgeIds, fm, pts, run.closed, prepared.curveEdgeCounts),
            attributeDictionaries: {},
          });
        }
      }
    }

    const childInstancesInfo: InstanceNode[] = [];

    for (const inst of builder.instances) {
      const refIdx = inst.refIdx;
      const instMatrix = inst.matrix;
      const newMatrix = multiplyMatrices(currentMatrix, instMatrix);

      let lName = parentLayer;
      let instMaterial = inheritedMaterial;
      // Legacy (pre-2021 MFC) instances carry a precomputed `properties`
      // record (see legacy.ts's extractLegacyDynamicProperties) - VFF
      // instances don't set this, so this stays {} for them and gets
      // overwritten below via the D007/DC05 TLV walk instead.
      let properties: Record<string, string> = { ...(inst.properties || {}) };

      // Layer and instance-material resolution use the fields already
      // extracted onto the builder instance (same source data for VFF -
      // D007/D207/D107 - read once in geometry.ts; legacy files populate
      // the same fields directly since they have no TLV children).
      if (inst.layerId !== null && inst.layerId !== undefined) {
        lName = layerIdToName.get(inst.layerId) || parentLayer;
      }

      if (inst.materialId !== null && inst.materialId !== undefined) {
        const matName = materialIdToName.get(inst.materialId);
        if (matName) {
          const mat = materialsMap.get(matName) || materialsByFolder.get(matName);
          if (mat) {
            instMaterial = mat;
          }
        }
      }

      // D007/DC05 TLV walk (VFF only); inst.children is always empty for
      // legacy instances, so this is a no-op there and the precomputed
      // `properties` seeded above survives unchanged.
      const d007 = inst.children.find((c) => c.tag === 'D007');
      let attributeDicts: Record<string, Record<string, VffAttrValue>> | null = inst.attributeDicts ?? null;
      if (d007) {
        try {
          attributeDicts = extractAttributeDictionaries(d007, options);
          const dynamic = attributeDicts['dynamic_attributes'];
          properties = {};
          if (dynamic) {
            for (const key of Object.keys(dynamic)) {
              properties[key] = stringifyVffAttrValue(dynamic[key]);
            }
          }
        } catch (e) {
          properties = {};
          attributeDicts = null;
          emitLog(
            options, 'debug',
            `Failed to extract dynamic properties for instance ${inst.name ?? ''} (refIdx=${refIdx}): ${(e as Error).message}`
          );
        }
      }

      // Fallback order: an attribute-dict name/label/code override, then
      // the instance's own explicit name, then the definition's own name
      // IF it's not itself just SketchUp's auto-generated
      // "Group#1"/"Component#12" placeholder, then finally the internal
      // index. Mirrors instanced.ts's identical resolution (and Python's/
      // C++'s own scene.py/instanced_scene.py) - see
      // instanced-fixture-parity.test.ts, which depends on both trees
      // resolving display names identically.
      const childDefName = defsDict.get(refIdx)?.name || '';
      const defNameIsReal = !!childDefName && !isGenericDefinitionName(childDefName);
      const nameOverride = findNameOverride(attributeDicts);
      const instNameNonEmpty = !!inst.name;
      const instName = instNameNonEmpty
        ? inst.name
        : defNameIsReal
          ? childDefName
          : `Component_${refIdx}`;
      const displayName = nameOverride ?? instName;
      const nameIsGenerated = nameOverride === null && !instNameNonEmpty && !defNameIsReal;

      const fullPathName = `${pathName} / ${instName}`;
      instanceCounter.count++;
      if (instanceCounter.count % PROGRESS_INTERVAL === 0) {
        emitProgress(options, 'build_scene', instanceCounter.count, instanceCounter.count);
        emitLog(options, 'debug', `Processed ${instanceCounter.count} placed instances`);
      }
      if (activeDefinitions.has(refIdx)) {
        throw new SkpParseError('Recursive component definition', {
          stage: 'build_scene',
          definitionId: refIdx,
        });
      }
      activeDefinitions.add(refIdx);
      const childNodes = instantiate(refIdx, newMatrix, lName, fullPathName, instMaterial);
      activeDefinitions.delete(refIdx);

      const tx = (newMatrix[9] ?? 0) * 25.4;
      const ty = (newMatrix[10] ?? 0) * 25.4;
      const tz = (newMatrix[11] ?? 0) * 25.4;

      // Every OTHER attribute dictionary this instance carries -
      // dynamic_attributes is already surfaced separately as `properties`
      // above, and SU_InstanceSet is SketchUp's own always-present,
      // always-empty Owner/Status boilerplate, not worth surfacing.
      // Mirrors Python's own attribute_dictionaries construction in
      // scene.py exactly (openskp#285).
      const attributeDictionaries: Record<string, Record<string, string>> = {};
      if (attributeDicts) {
        for (const dictName of Object.keys(attributeDicts)) {
          if (dictName === 'dynamic_attributes' || dictName === 'SU_InstanceSet') continue;
          const entries = attributeDicts[dictName];
          const stringified: Record<string, string> = {};
          for (const key of Object.keys(entries)) stringified[key] = stringifyVffAttrValue(entries[key]);
          attributeDictionaries[dictName] = stringified;
        }
      }

      const nodeLayer =
        lName === 'Layer0' && childNodes.length === 0 && defsDict.has(refIdx)
          ? (getFaceLayerSummary(refIdx).uniformLayer ?? lName)
          : lName;

      const instInfo: InstanceNode = {
        name: displayName,
        nameIsGenerated,
        definitionName: childDefName,
        layer: nodeLayer,
        positionMm: [
          Math.round(tx * 100) / 100,
          Math.round(ty * 100) / 100,
          Math.round(tz * 100) / 100,
        ],
        properties: properties,
        attributeDictionaries,
        guid: inst.refGuid || '',
        children: childNodes,
      };
      childInstancesInfo.push(instInfo);

      pathUpdates.set(fullPathName, { properties, name: displayName, attributeDictionaries });
    }

    return childInstancesInfo;
  }

  const identityMat = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1.0];
  const rootChildren = instantiate('ROOT', identityMat);

  // Deferred mesh backfill: each mesh's own path was recorded verbatim as
  // a pathUpdates key by the exact instance that placed the definition
  // that mesh's own faces belong to (never an ancestor's), so a direct
  // O(1) lookup per mesh is enough - no cascading from an ancestor down
  // to its descendants' own meshes. Properties/name are per-instance,
  // not inherited by nested sub-parts, matching how sceneHierarchy
  // already builds each InstanceNode from that same instance's own
  // `inst.name`/`properties` directly above.
  for (const geomName of Object.keys(meshIndex)) {
    const existing = meshIndex[geomName];
    const update = existing ? pathUpdates.get(existing.path) : undefined;
    if (existing && update) {
      existing.properties = update.properties;
      existing.name = update.name;
      existing.attributeDictionaries = update.attributeDictionaries;
    }
  }

  // Fill in missing root meshes without clobbering their resolved layer
  for (const geomName of Object.keys(meshIndex)) {
    const existing = meshIndex[geomName];
    if (existing && existing.path === 'ROOT') {
      existing.name = existing.name || 'ROOT';
      existing.definitionName = existing.definitionName || 'ROOT_MODEL';
      existing.layer = existing.layer || 'Layer0';
      existing.positionMm = [0, 0, 0];
      existing.properties = existing.properties || {};
      existing.attributeDictionaries = existing.attributeDictionaries || {};
    }
  }

  const sceneHierarchy: InstanceNode = {
    name: 'ROOT',
    nameIsGenerated: false,
    definitionName: 'ROOT_MODEL',
    layer: 'Layer0',
    positionMm: [0, 0, 0],
    properties: {},
    attributeDictionaries: {},
    guid: '',
    children: rootChildren,
  };

  emitLog(
    options,
    'info',
    `Scene build complete: ${instanceCounter.count} instances, ${Object.keys(meshIndex).length} meshes, ` +
      `${glbPrimitives.length} primitives (${((Date.now() - t0) / 1000).toFixed(2)}s)`
  );

  const bounds: SceneBounds | null = Number.isFinite(boundsMin[0])
    ? {
        min: [boundsMin[0], boundsMin[1], boundsMin[2]],
        max: [boundsMax[0], boundsMax[1], boundsMax[2]],
        size: [
          boundsMax[0] - boundsMin[0],
          boundsMax[1] - boundsMin[1],
          boundsMax[2] - boundsMin[2],
        ],
        center: [
          (boundsMin[0] + boundsMax[0]) / 2,
          (boundsMin[1] + boundsMax[1]) / 2,
          (boundsMin[2] + boundsMax[2]) / 2,
        ],
      }
    : null;

  return {
    sceneHierarchy,
    meshIndex,
    glbPrimitives,
    curveSets,
    surfacePrimitives: glbPrimitives.filter((p) => !p.unpaintedBack),
    gltfMaterials,
    textures,
    bounds,
    unreadRootEntities: parsed.unreadRootEntities ?? 0,
    pages: buildPagesFromRaw(parsed),
    layerHidden: layerHidden ? Object.fromEntries(layerHidden) : {},
  };
}

export function buildPagesFromRaw(parsed: ParsedRawData): Page[] {
  return (parsed.pages ?? []).map((pg) => ({
    name: pg.name,
    eye: pg.eye,
    target: pg.target,
    up: pg.up,
    fov: pg.fov,
    parallel: pg.parallel,
    orthoHeight: pg.orthoHeight,
    hiddenLayers: (pg.hiddenLayerIds ?? [])
      .map((id) => parsed.layerIdToName?.get(id))
      .filter((name): name is string => name !== undefined),
    hiddenEntityIds: [...(pg.hiddenEntityIds ?? [])],
    ...pageCaptureFlags(pg.captureFlags ?? null),
  }));
}

export function resolveMaterialFromMaps(
  matId: number | null | undefined,
  materialIdToName: Map<number, string>,
  materialsMap: Map<string, Material>,
  materialsByFolder: Map<string, Material>
): Material | undefined {
  if (matId === undefined || matId === null) return undefined;
  const matName = materialIdToName.get(matId);
  if (!matName) return undefined;
  return materialsMap.get(matName) || materialsByFolder.get(matName);
}
