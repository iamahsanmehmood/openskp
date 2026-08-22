# OpenSKP

**The open-source SketchUp (`.skp`) file parser, writer, and converter — TypeScript / JavaScript edition.**

Parse, write, and convert `.skp` files without SketchUp. No SDK. No
license. Zero native dependencies — `fflate` handles ZIP extraction and a
ported `earcut` handles triangulation, so it runs anywhere JavaScript
does: Node.js or the browser.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![npm](https://img.shields.io/npm/v/openskp.svg?logo=npm&logoColor=white)](https://www.npmjs.com/package/openskp)
[![Node](https://img.shields.io/node/v/openskp.svg?logo=node.js&logoColor=white)](https://www.npmjs.com/package/openskp)

🏠 [openskp.com](https://openskp.com) · 🌐 [Try the Live Web Viewer](https://iamahsanmehmood.github.io/openskp/) · 📖 [Docs](https://iamahsanmehmood.github.io/openskp/docs/) · [Changelog](https://github.com/iamahsanmehmood/openskp/blob/main/CHANGELOG.md)

> [!IMPORTANT]
> This project was built by reverse engineering a proprietary binary format. It is not affiliated with or endorsed by Trimble Inc. or SketchUp.

## What is OpenSKP?

OpenSKP is the first and only open-source, cross-platform parser for
SketchUp binary files — reverse-engineered from both the modern **VFF
container** (SketchUp 2021+) and the classic **MFC `CArchive`** container
(SketchUp 2013–2020). It gives you full programmatic access to geometry,
materials, components, layers, and metadata, with no SketchUp installation
and no proprietary SDK required. The same parser and export API also ship
as first-class packages for Python, .NET, Dart, and C++ — see the
[project README](https://github.com/iamahsanmehmood/openskp) for the full
cross-language picture.

This package can also *write* new `.skp` files from scratch, and edit
existing ones, validated feature-by-feature against the real SketchUp
SDK (see [Writing](#writing) below).

## Features

- **Full-fidelity parsing** — vertices, edges, faces, normals, UV
  coordinates, nested component hierarchies, layers/tags, materials,
  textures, styles, and dynamic-component attributes.
- **Both SketchUp file generations** — modern VFF (2021+) and legacy MFC
  (2013–2020) containers, transparently, behind one `parseSkp()`/`.parse()`
  call.
- **Scene baking** — an opt-in `buildScene()` pass resolves the full placed
  scene graph to world-space, triangulated, export-ready geometry.
- **Instancing preserved** — `buildInstancedScene()` keeps SketchUp's own
  instancing instead of baking it out: unique geometry once, plus one
  transform per placement, so a component placed 1,000 times costs one copy
  of its buffers. Losslessly — no decimation or quantisation. See
  [Choosing an API](#choosing-an-api).
- **Native multi-format conversion** — glTF (GLB), Wavefront OBJ/MTL, STL,
  PLY, AutoCAD DXF (3DFACE and Polyface Mesh), IFC4 (BIM/ISO 10303-21 STEP),
  and JSON — all written from scratch, no third-party CAD/BIM SDK involved.
  The DXF writer is verified against real desktop AutoCAD, not just lenient
  DXF readers.
- **Runs anywhere JavaScript does** — zero native dependencies, works in
  Node.js and directly in the browser from a `File`/`Blob`.
- **Structured observability** — opt-in progress reporting and
  structured, location-carrying parse errors.
- **Write support** — build new legacy-format `.skp` files from scratch:
  geometry (including true, editable circular/arc curves, freeform
  polylines, faces with holes cut out, and non-planar auto-triangulation),
  materials (solid + PNG/JPEG textures), layers, nested component
  definitions and groups, instance rotation/visibility, and custom
  attribute dictionaries — or load and extend an existing file with
  `openExisting()`. No SDK involved; every feature validated against the
  real SketchUp SDK. See [Writing](#writing) below.

## Installation

```bash
npm install openskp
```

## Quick Start

```typescript
import { SkpFile } from 'openskp';

// Node.js
const skp = SkpFile.open('model.skp');
const model = skp.parse();

// Browser: parse from a File/Blob's ArrayBuffer
import { parseSkp } from 'openskp';
const buffer = await file.arrayBuffer();
const model2 = parseSkp(buffer);

console.log(model.version, model.layers.length, 'layers');

// Inspect definitions (component geometry) - model.definitions is a Map
for (const [id, defn] of model.definitions) {
  console.log(`${defn.name}: ${defn.faces.length} faces, ${defn.vertices.length} vertices`);
}

// model.root holds whatever is placed directly in the model, not inside
// any component/group
console.log(model.root.instances.length, 'root-level instances');

// Opt-in: full placed scene graph, triangulated, world-space, GLB-ready
const scene = skp.buildScene();
console.log(scene.glbPrimitives.length, 'renderable mesh primitives');

// Or keep the file's instancing instead of baking it out: unique geometry
// once, plus a transform per placement.
const instanced = skp.buildInstancedScene();
console.log(instanced.meshResources.length, 'unique meshes');
```

## Choosing an API

Three entry points, in increasing order of what they compute. Each one
re-parses the file independently, so you only pay for the one you call.

| Use | When |
|---|---|
| `parseSkp()` / `.parse()` | You want the file's raw contents — definitions, layers, materials, metadata — with no scene-graph instancing resolved. Fastest and lightest. |
| `buildScene()` / `.buildScene()` | You want a flat, world-space, triangulated scene to hand straight to a renderer or an exporter (`toGLB`, `toOBJ`, `toSTL`, `toIFC`, …). Every placement is baked into its own vertex buffers. |
| `buildInstancedScene()` / `.buildInstancedScene()` | You want the same geometry, but with the file's instancing preserved: each definition triangulated once and referenced by every placement. |

### The memory tradeoff

`buildScene()` bakes each placed instance into its own world-space buffers,
so its output scales with:

```
definition geometry x number of placed instances
```

`buildInstancedScene()` stores each distinct definition once and puts the
placement on the node, so it scales with:

```
unique geometry + instance transforms
```

For a model that reuses components heavily — furniture, facade panels,
fixtures, anything repeated — that is the difference between an output that
grows with the model's *placement count* and one that grows with its
*distinct content*. On a synthetic scene of one 24-face component repeated
1,000 times, the geometry buffers are 1,000x smaller (3,562 KB vs 3.6 KB)
and the exported GLB is 48x smaller.

The cost is that you must apply the node transforms yourself (or hand the
result to `toInstancedGLB()`, or to any glTF/three.js-style scene graph,
which do it natively). If you just want flat triangles, `buildScene()` is
still the simpler call.

**This is lossless instancing preservation, not mesh decimation.** No
vertices are removed, merged, quantised or approximated. The triangles are
exactly the ones `buildScene()` produces; they are stored once and
referenced N times instead of copied N times. The test suite asserts this
directly, by flattening the instanced result and comparing it against the
baked one on the repository's real `.skp` fixtures.

### Coordinate systems and units

SketchUp stores geometry in **inches on a Z-up** axis system. Both scene
builders convert to **metres on glTF's Y-up** axes, applying the same
`(x, y, z) -> (x, z, -y)` swap, so:

- `LocalPrimitive.positions` / `normals` are in metres, Y-up, and in
  **definition-local space** — no instance transform applied.
- `InstancedNode.matrix` is a 16-element **column-major glTF matrix**, in
  metres, Y-up, and is **relative to its parent**. Compose the chain by
  walking the tree, exactly as glTF does. The root node's matrix is the
  identity.
- `InstancedNode.positionMm` is the one exception: it is the node's
  **absolute** position in **millimetres on SketchUp's Z-up** axes, kept in
  that frame so it matches the baked path's `InstanceNode.positionMm`
  field for metadata comparisons.

Normals are left in local space deliberately. Transforming them is the
consumer's job (glTF's own rule: inverse-transpose of the node's upper-left
3x3), and deferring it is what keeps non-uniform and mirrored
(negative-determinant) scales correct without baking a per-placement copy of
the normal buffer.

### How material variants affect resource reuse

Two placements share a mesh resource when their *effective rendered
geometry* is identical — which is not the same as sharing a definition ID.
The same component renders differently depending on where it is placed, so
resource identity also accounts for:

- the **inherited instance material** (SketchUp's "paint the component"),
  which sets both the colour of unpainted faces and, through its texture's
  tile size, their UVs;
- **texture identity**, not just averaged colour — two different images can
  average to the same RGB;
- the **effective layer's fallback colour**, which is what an entirely
  unpainted face renders as.

So two instances of one definition painted with different materials produce
two resource *variants*, while two instances with the same effective context
share one. Front/back material resolution, per-face UV mapping and
double-sided-vs-split geometry all follow deterministically from the
definition plus those inputs. `InstancedMeshResource.variantKey` exposes the
resolved context if you need to see why a definition produced more than one
resource. Resource IDs (`mesh_0`, `mesh_1`, …) are stable and deterministic
for a given file.

### Exporting an instanced GLB

```typescript
import { SkpFile, toInstancedGLB } from 'openskp';
import * as fs from 'fs';

const instanced = SkpFile.open('model.skp').buildInstancedScene();

// Multiple glTF nodes reference the SAME mesh - the vertex and index
// buffers are written once, not once per placement.
fs.writeFileSync('model.glb', toInstancedGLB(instanced));

// Pass { textures: true } to embed the texture images, as with toGLB().
fs.writeFileSync('textured.glb', toInstancedGLB(instanced, { textures: true }));
```

A definition that resolves to several materials becomes one glTF mesh with
several primitives, which is glTF's normal representation — not several
nodes. `toGLB()` and `buildScene()` are untouched and still produce exactly
what they always have.

## Exporting

```typescript
import { toGLB, toInstancedGLB, toOBJ, toMTL, exportOBJ, toSTLAscii, toSTLBinary, exportSTL, toPLYAscii, toPLYBinary, exportPLY, toDXF, exportDXF, toIFC, exportIFC, toJSON } from 'openskp';

// Serialize a built scene straight to .glb bytes (in-memory, no disk I/O).
// Index buffers are written as UNSIGNED_SHORT when every index fits, which
// is the usual case - roughly halving the index data at no loss.
const glbBytes = toGLB(scene);

// Instancing-preserving GLB: one mesh, many nodes (see Choosing an API)
const instancedGlb = toInstancedGLB(instanced);

// Export to Wavefront OBJ string, plus a companion .mtl material library
const objText = toOBJ(scene, 'output.mtl');
const mtlText = toMTL(scene);

// Node.js only: writes both output.obj and output.mtl together
exportOBJ(scene, 'output.obj');

// Export to STL ASCII or Binary string/buffer
const stlText = toSTLAscii(scene);
const stlBytes = toSTLBinary(scene);

// Export to PLY ASCII or Binary string/buffer
const plyText = toPLYAscii(scene);
const plyBytes = toPLYBinary(scene);

// Export to AutoCAD 3D DXF text format (R2000 / AC1015 compliant)
const dxfText = toDXF(scene);

// Node.js only: export directly to a .dxf file
exportDXF(scene, 'output.dxf');

// Export to IFC4 / BIM (ISO 10303-21 STEP format string / file)
const ifcText = toIFC(scene);
exportIFC(scene, 'output.ifc');

// Full metadata as a JSON-compatible object
const meta = toJSON(model, scene);
```

## Writing

OpenSKP can also *create* new `.skp` files from scratch — a genuine,
from-scratch binary writer for the legacy MFC `CArchive` format (SketchUp
2013–2020), with no SketchUp SDK involved at any point. Ports the same
feature set as the Python package's writer, verified byte-identical to
Python's own output on the same input: geometry, materials (solid +
PNG/JPEG textures), layers (with color and default visibility),
component definitions with multiple instances, groups, nested
definitions and nested group instances, per-instance rotation and
visibility, explicit per-side texture positioning, custom key/value
attribute dictionaries, circular faces and partial arcs, freeform
polyline curves, faces with holes cut out, and non-planar
auto-triangulation. `openExisting()` loads an *existing* legacy-format
file and rebuilds it as a new builder, so more geometry can be added
before saving. See [`src/create.ts`](src/create.ts) for the full scope
notes.

```typescript
import { create } from 'openskp';

const builder = create();

// Materials and layers
const red = builder.addMaterial('Red', [255, 0, 0]);
const brick = builder.addTextureMaterial('Brick', brickPngBytes);
const roofLayer = builder.addLayer('Roof', { color: [180, 60, 40] });

// All addComponentDefinition/addGroup calls must come before any
// addInstance/addFace call - placing anything locks in the file's
// internal slot numbering for everything after it
const chair = builder.addComponentDefinition('Chair', (def) => {
  def.addFace([[0, 0, 0], [20, 0, 0], [20, 20, 0], [0, 20, 0]]);
});
builder.addInstance(chair, { translation: [50, 0, 0] });
builder.addInstance(chair, { translation: [100, 0, 0], hidden: true });

builder.addFace(
  [[0, 0, 0], [100, 0, 0], [100, 100, 0], [0, 100, 0]],
  { material: red, layer: roofLayer }
);

builder.save('output.skp');   // Node.js only; use builder.toBytes() in the browser
```

### Editing an existing file

```typescript
import { openExisting } from 'openskp';

const { builder, warnings, definitions } = openExisting('building.skp');
for (const w of warnings) console.log('not fully reproduced:', w);

builder.addCircle([0, 0, 100], [0, 0, 1], 50);
builder.save('building_edited.skp');
```

`warnings` is the honest account of what couldn't be faithfully
reproduced from that specific source file. Every material/layer the
source had is reachable on `builder.materialsByName`/`builder.layersByName`
without a separate lookup, and `definitions` maps each replayed component
definition's own name to its builder for placing more instances of
something the source already defined.

## Observability

`parse()`/`buildScene()` accept an optional options object for progress
reporting and structured errors — silent by default:

```typescript
const model = skp.parse({
  onProgress: (info) => console.log(`${info.stage}: ${info.current}/${info.total}`),
  onLog: (level, message) => console.log(`[${level}] ${message}`),
});
```

Parse failures throw `SkpParseError`, carrying `stage`, `recordIndex`,
`totalRecords`, `tag`, and `definitionId` context, with the original
error preserved as `.cause`. See
[docs/OBSERVABILITY.md](../../docs/OBSERVABILITY.md) for the full
cross-language reference.

## Known limitation: large files

Memory use scales worse than the other four ports on very large files —
a 113 MB file needs 8–16 GB of Node heap, and files beyond ~150-200 MB
may not parse in a browser tab at all (a typical tab's heap ceiling is
~4 GB). Root cause: V8's per-object overhead on millions of small
geometry objects. See
[docs/DEVELOPER_GUIDE.md](../../docs/DEVELOPER_GUIDE.md#performance) for
verified numbers before parsing very large files in this package.

## Package Structure

| Module | Purpose |
|---|---|
| `parser.ts` | TLV binary parser for SketchUp's internal format |
| `model.ts` | Interfaces for geometry, layers, materials, scenes |
| `legacy.ts` | Legacy MFC container support (SketchUp 2013–2020) |
| `vff.ts` | VFF/ZIP container handling (`fflate`-based) |
| `triangulator.ts` | Planar polygon triangulation (ported `earcut`) |
| `transforms.ts` | 3D matrix transforms and coordinate conversions |
| `observability.ts` | Progress/log callback types |
| `errors.ts` | `SkpParseError` and structured failure context |
| `face-groups.ts` | Local-space face grouping shared by both scene builders |
| `instanced.ts` | Instancing-preserving scene builder (`buildInstancedScene`) |
| `instanced-glb.ts` | GLB exporter that reuses one mesh across many nodes |
| `index.ts` | Public entry point — `SkpFile`, `parseSkp`, `buildScene`, `buildInstancedScene`, `toGLB`, `toInstancedGLB`, `toJSON` |

## Requirements

Node.js ≥ 16, or any modern browser. No native dependencies.

## Used in Production

OpenSKP powers the SketchUp import pipeline for
[FrameSmart](https://frame-smart.com/) (a 3D collaboration platform with
nearly 200 active users) and [IngeTrazo](https://ingetrazo.com/) (a
SketchUp-alternative 3D modeler with a BIM → IFC bridge). Using OpenSKP in
your own project? [Open an issue](https://github.com/iamahsanmehmood/openskp/issues)
or a PR to get added here.

## Contributing

The Python package in `../python/` and this package are both full,
independent implementations at parity — neither is a stub for the other.
See [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## License

MIT — see [LICENSE](../../LICENSE)
