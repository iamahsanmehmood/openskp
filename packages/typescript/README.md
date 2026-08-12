# OpenSKP — TypeScript Package

Open-source SketchUp (`.skp`) binary file parser for Node.js and the
browser. Extract geometry, metadata, layers, and materials from SketchUp
files without requiring SketchUp itself. Zero native dependencies —
`fflate` handles ZIP extraction and a ported `earcut` handles
triangulation, so it runs anywhere JavaScript does.

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
```

## Exporting

```typescript
import { toGLB, toOBJ, toSTL, toPLY, toDXF, exportDXF, toIFC, exportIFC, toJSON } from 'openskp';

// Serialize a built scene straight to .glb bytes (in-memory, no disk I/O)
const glbBytes = toGLB(scene);

// Export to Wavefront OBJ string
const objText = toOBJ(scene);

// Export to STL ASCII or Binary string/buffer
const stlText = toSTL(scene);

// Export to PLY ASCII or Binary string/buffer
const plyText = toPLY(scene);

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

Memory use scales worse than the other three ports on very large files —
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
| `index.ts` | Public entry point — `SkpFile`, `parseSkp`, `buildScene`, `toGLB`, `toJSON` |

## Requirements

Node.js ≥ 16, or any modern browser. No native dependencies.

## Contributing

The Python package in `../python/` and this package are both full,
independent implementations at parity — neither is a stub for the other.
See [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## License

MIT — see [LICENSE](../../LICENSE)
