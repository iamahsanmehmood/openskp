# Developer Guide

This is the detailed, practical guide to building on OpenSKP: what each
language's API actually gives you, how memory and performance behave on
real files, how to plug in progress/error observability, and where the
five ports currently differ from each other. If you just want the pitch
and a five-line example, see the [README](../README.md). If you want the
raw binary format itself, see [BINARY_FORMAT.md](BINARY_FORMAT.md). If you
want the observability feature in full depth, see
[OBSERVABILITY.md](OBSERVABILITY.md).

Every claim in this guide — every number, every code sample, every "this
works"/"this doesn't yet" — was checked against the actual current source
and, where practical, run against real `.skp` files while writing this
document. Where a language's behavior is genuinely different from the
others, that's stated plainly rather than smoothed over.

## Contents

- [Installation](#installation)
- [Two entry points: parse() and buildScene()](#two-entry-points-parse-and-buildscene)
- [The data model](#the-data-model)
- [Legacy format support (SketchUp 2013–2020)](#legacy-format-support-sketchup-20132020)
- [Memory and performance](#performance)
- [Observability: progress and errors](#observability)
- [Error handling](#error-handling)
- [Export capabilities](#export-capabilities)
- [Write capabilities](#write-capabilities)
- [The web viewer](#the-web-viewer)
- [Known cross-language differences](#known-cross-language-differences)
- [Troubleshooting](#troubleshooting)

---

## Installation

| Language | Install | Current version |
|---|---|---|
| Python | `pip install openskp` | [![PyPI](https://img.shields.io/pypi/v/openskp.svg?label=)](https://pypi.org/project/openskp/) |
| TypeScript / JavaScript | `npm install openskp` | [![npm](https://img.shields.io/npm/v/openskp.svg?label=)](https://www.npmjs.com/package/openskp) |
| .NET / C# | `dotnet add package OpenSkp` | [![NuGet](https://img.shields.io/nuget/v/OpenSkp.svg?label=)](https://www.nuget.org/packages/OpenSkp) |
| Dart / Flutter | `dart pub add openskp` | [![Pub](https://img.shields.io/pub/v/openskp.svg?label=)](https://pub.dev/packages/openskp) |
| C++17 / CMake | build/install `packages/cpp`, then `find_package(OpenSkp CONFIG REQUIRED)` | [![C++](https://img.shields.io/github/v/release/iamahsanmehmood/openskp?filter=cpp-v*&label=)](https://github.com/iamahsanmehmood/openskp/releases?q=cpp-) |

All five are independent packages sharing one reverse-engineered format
specification, not bindings around a shared native core — each is a
from-scratch, idiomatic implementation in its own language, cross-validated
against the others on the same real files.

## Two entry points: `parse()` and `buildScene()`

Every language exposes the same two-tier API, and the split exists for the
same reason everywhere: **memory**.

```
SkpFile.open(path)
  ├── .parse()       → SkpModel   (fast, light: raw per-definition geometry)
  └── .buildScene()  → Scene      (opt-in, heavier: full placed scene graph,
                                    triangulated, world-space, GLB-ready)
```

**`parse()`** reads each component/group definition's geometry exactly
once — vertices, edges, faces, and the *un-resolved* instance placements
(which definition, what transform) — with no scene-graph walking and no
triangulation. This is what you want for metadata inspection, custom
geometry processing, or anything that doesn't need a renderable mesh.

**`buildScene()`** walks the *entire placed scene graph*: every instance
of every component, nested arbitrarily deep, each with its transform
resolved to world space, each face triangulated (via Ear Clipping - `earcut` in
TypeScript, Dart, .NET, and C++; Delaunay triangulation in Python) and grouped by resolved color into
GLB-ready mesh primitives. For a file that reuses a handful of definitions
GLB-ready mesh primitives. For a file that reuses a handful of definitions
across many thousands of placements (a park bench repeated 400 times, say),
this can produce **far more data** than the file's raw, un-instanced
geometry — that's the whole reason it's a separate, opt-in call rather than
something `parse()` always pays for.

**They're independent, not layered.** Calling `buildScene()` does not
require calling `parse()` first, and it does not reuse a prior `parse()`
call's data — it re-runs the underlying parse on its own. Calling both on
the same buffer/file means parsing the raw TLV data twice; this is a
deliberate trade of a bit of extra CPU time for guaranteeing that a plain
`parse()` call's memory footprint never includes scene-baking's cost.

```python
# Python
model = SkpFile.open("model.skp").parse()          # light
scene = SkpFile.open("model.skp").build_scene()     # opt-in, heavier
```
```typescript
// TypeScript
const model = SkpFile.open("model.skp").parse();
const scene = SkpFile.open("model.skp").buildScene();
```
```csharp
// .NET
var model = SkpFile.Open("model.skp");
var scene = SkpFile.BuildScene("model.skp");
```
```dart
// Dart
final model = SkpFile.open("model.skp").parse();
final scene = SkpFile.open("model.skp").buildScene();
```
```cpp
// C++
auto skp = openskp::SkpFile::open("model.skp");
auto model = skp.parse();
auto scene = skp.build_scene();
auto glb = openskp::to_glb(scene);
openskp::export_glb(scene, "model.glb");
```

## The data model

All five languages produce structurally equivalent output for the same
file — same counts, same coordinates, same topology, cross-validated
directly against each other on real fixtures (not just against each
language's own idea of what the format means).

| Concept | Python | TypeScript | .NET | Dart | C++ |
|---|---|---|---|---|---|
| Entry point | `.open().parse()` | `.open().parse()` | `SkpFile.Open()` | `.open().parse()` | `SkpFile::open().parse()` |
| Top-level result | `SkpModel` | `SkpModel` | `SkpModel` | `SkpModel` | `SkpModel` |
| Definitions | `dict` | `Map` | `Dictionary` | `Map` | `std::map` |
| Vertex | dataclass | object | class | class | struct |
| Edge / Face | dataclass | object | class | class | struct |
| Layer / Material | dataclass | object | class | class | struct |
| Instance | dataclass | object | class | class | struct |

Coordinates are always **inches, Z-up** (SketchUp's native units) in the
`parse()` result. `buildScene()`'s output converts to **meters, Y-up**
(glTF convention) — see [BINARY_FORMAT.md §4](BINARY_FORMAT.md#4-coordinate-system)
for the exact conversion.

### The root definition

Every `.skp` file has an *implicit* top-level "definition" — geometry drawn
directly in the model (not inside any component/group) and the top-level
placed instances. How each language exposes it is currently **not
uniform** — see [Known cross-language differences](#known-cross-language-differences)
below for the full, honest breakdown; don't assume the shape from one
language's docs applies to another's.

## Legacy format support (SketchUp 2013–2020)

SketchUp 2021 switched `.skp`'s container from a classic MFC `CArchive`
object-graph serialization (versions 8 through 2020, internally versions
13–20) to the VFF/ZIP container the rest of this guide describes. OpenSKP
reads **both**, transparently — `SkpFile.open()`/`.parse()` auto-detects
which era a file uses (by header bytes) and routes to the matching walker.
There is no separate API to call for old files; the same code path handles
both, and the resulting `SkpModel`/`Scene` shape is identical either way.

The legacy walker was reverse-engineered independently of the public
"2017 format notes" — several details (edge/loop record ordering, entity
preamble structure, per-version byte-count differences between v16 and
v17+) were established by clean-room analysis and cross-validated against
the *same models re-saved as VFF*, matching face/edge counts, surface area,
and bounding boxes exactly. See the extensive docstring at the top of each
language's `legacy.py`/`legacy.ts`/`Legacy.cs`/`legacy.dart` for the full
list of documented deviations from the public spec, if you're working on
the parser itself rather than just consuming it.

Legacy files quietly cost more CPU per byte than modern VFF files (the MFC
object-graph format requires resolving a shared, order-dependent slot
table rather than a self-describing TLV tree), but the same lazy,
streaming architecture applies — see [Performance](#performance).

## Performance

### The memory architecture

Real production `.skp` files can have well over 100,000 separate component
definitions. The naive approach — parse the entire file into one in-memory
tree, then walk it — means peak memory scales with the *whole file's* node
count, which is what made large files crash outright before this was fixed.

All five languages now parse **one top-level record at a time**:
`iter_top_level_lazy()` / `iterTopLevelLazy()` / `IterTopLevelLazy()` do a
cheap flat header scan (O(sibling count), not O(total node count)) to find
each top-level definition/layer-manager/material-manager/root block, fully
build *only that one record's* subtree, hand it to the caller, and let it
be garbage-collected before the next one is built. Peak memory during the
walk is bounded by the size of the **single largest** top-level record, not
the file's total size — this is also what makes the [progress
reporting](#observability) free: the same header scan that drives the loop
gives you the total record count for "N of total" with no extra pass over
the file.

**This fixed the crash on large files uniformly** — the underlying
per-tag extraction logic (every tag's decoding, every field) was untouched;
only the orchestration loop changed, in all five languages, the same way.

### .NET's additional fix: no array-size ceiling

.NET has one constraint the other four don't: the CLR's array and
`MemoryStream` types are capped at roughly 2.1 GB regardless of GC
settings, and a decompressed `model.dat` can exceed that on real files (a
compression ratio of ~10x on this binary format is common, so a 300 MB
`.skp` file can decompress to several GB). This needed a genuine
architecture change, not just a tuning flag: `ChunkedBuffer`, a
multi-segment byte buffer, plus widening every TLV offset from `int` to
`long` throughout the parser. As a result, **.NET has no practical
file-size ceiling today** — verified against a 620 MB real file (153,586
definitions) with zero special configuration.

### Verified numbers (real files, this session)

| Language | File | Size | Definitions | Config needed | Time |
|---|---|---|---|---|---|
| .NET | `Sunner - Iron Ore Windows _ side skp.skp` | 620 MB | 153,586 | none | ~230–270s parse, ~17s scene build |
| Python | `The Suite on 49th.skp` | 294 MB | 336,254 | none | ~400s |
| Dart | `The Suite on 49th.skp` | 294 MB | 336,253 | `DART_VM_OPTIONS="--old_gen_heap_size=4096"` | ~82s |
| TypeScript | `IronTech_IFC-04.skp` | 18.5 MB | 1,264 | none | ~4s |
| TypeScript | `Barzona IFC01.skp` | 26.8 MB | 2,554 | none | ~8s |
| TypeScript | `McDonalds_chnages.skp` | 113 MB | 132,879 | `node --max-old-space-size=16384` | ~34s |
| TypeScript | `The Suite on 49th.skp` | 294 MB | 336,254 | — | **fails even at 16 GB heap** |

Python and .NET need no configuration regardless of file size in the files
tested. Dart and TypeScript run on V8/the Dart VM's own heap, which
defaults to a few GB — for files past roughly 50–100 MB (thousands to tens
of thousands of definitions), raise it:

```bash
# Dart
DART_VM_OPTIONS="--old_gen_heap_size=4096" dart run your_script.dart

# Node.js
node --max-old-space-size=8192 your-script.js
```

**TypeScript's ceiling is a real, currently open limitation**, not just a
"needs a bigger flag" story: a 113 MB file needed somewhere between 8 GB
and 16 GB of heap, and a 294 MB file failed even at 16 GB. This is very
likely V8's per-object memory overhead on the millions of individual small
`{id, x, y, z}`-shaped objects a large file's vertices/edges/faces become
(the lazy top-level iteration above bounds the *walk's* peak memory, but
doesn't change the size of the *final* `SkpModel` result sitting in
memory afterward — and V8 objects cost meaningfully more per unit of data
than Python tuples, .NET structs, or Dart's typed collections). Investigating
a more compact internal representation is tracked as follow-up work, not
yet done. **Practical guidance today:** TypeScript is solid for files up to
the tens-of-MB / low-hundreds-of-thousands-of-definitions range; for larger
files, prefer Python or .NET, or process on the server side rather than in
a browser tab (which has its own, usually much lower, heap ceiling
regardless of Node flags).

## Observability

All five languages support opt-in progress reporting and structured error
context — silent by default, never printing or logging unless you ask.
This is substantial enough to have [its own document](OBSERVABILITY.md);
the short version:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
logging.getLogger("openskp").setLevel(logging.DEBUG)
model = SkpFile.open("model.skp").parse()   # now logs progress/stages
```
```typescript
const model = SkpFile.open("model.skp").parse({
  onProgress: (info) => console.log(`${info.stage}: ${info.current}/${info.total}`),
  onLog: (level, message) => console.log(`[${level}] ${message}`),
});
```
```csharp
var options = new SkpParseOptions {
    Progress = new Progress<SkpParseProgress>(p => Console.WriteLine($"{p.Stage}: {p.Current}/{p.Total}")),
    OnLog = (level, msg) => Console.WriteLine($"[{level}] {msg}"),
};
var model = SkpFile.Open("model.skp", options);
```
```dart
final model = SkpFile.open("model.skp").parse(ParseOptions(
  onProgress: (info) => print('${info.stage}: ${info.current}/${info.total}'),
  onLog: (level, message) => print('[$level] $message'),
));
```
```cpp
openskp::ParseOptions options;
options.progress = [](const openskp::ParseProgress& p) { /* update UI */ };
options.log = [](openskp::LogLevel level, std::string_view message) { /* log */ };
auto model = openskp::SkpFile::open("model.skp").parse(options);
```

See [OBSERVABILITY.md](OBSERVABILITY.md) for the full stage vocabulary,
error field reference, and design rationale.

## Error handling

Every language raises/throws a structured error type — never a bare
string — for failures anywhere in the parse or scene-build path:
`SkpParseError` (Python, TypeScript, C++) / `SkpParseException` (.NET, Dart).
Full field reference in [OBSERVABILITY.md](OBSERVABILITY.md#error-fields).

Two other exceptions you may see that are *not* this type, and mean
something more basic:

- **File-not-found / wrong extension** — Python: `FileNotFoundError`
  /`ValueError`; TypeScript: n/a in the browser (you supply the buffer),
  Node's `SkpFile.open()` throws whatever `fs.readFileSync` throws; .NET:
  `FileNotFoundException`/`ArgumentException`; Dart:
  `FileSystemException`/`ArgumentError`; C++: `std::filesystem::filesystem_error`
  / `std::invalid_argument`. These happen before any actual
  parsing starts.
## Export capabilities

`buildScene()`'s result (`Scene`, `GlbPrimitive[]`, `gltfMaterials`) is
already exactly the data a GLB/glTF exporter needs — triangulated,
world-space, grouped by material. What differs is whether each language
ships file-writing exporters on top of that data:

| Language | Scene data (`buildScene()`) | GLB | OBJ | STL | PLY | DXF 3D (AutoCAD R2000) | IFC4 (BIM) | JSON metadata |
|---|---|---|---|---|---|---|---|---|
| Python | ✅ | ✅ `openskp.export.glb` | ✅ `openskp.export.obj` | ✅ `openskp.export.stl` | ✅ `openskp.export.ply` | ✅ `openskp.export.dxf` | ✅ `openskp.export.ifc` | ✅ `openskp.export.json_export` |
| TypeScript | ✅ | ✅ `toGLB(scene)` | ✅ `toOBJ(scene)` / `exportOBJ` | ✅ `toSTLAscii` / `exportSTL` | ✅ `toPLYAscii` / `exportPLY` | ✅ `toDXF(scene)` / `exportDXF` | ✅ `toIFC(scene)` / `exportIFC` | ✅ `toJSON(model, scene?)` |
| .NET | ✅ | ✅ `GlbExport.ExportGlb` | ✅ `ObjExport.ExportObj` | ✅ `StlExport.ExportStl` | ✅ `PlyExport.ExportPly` | ✅ `DxfExport.ToDxf` / `ExportDxf` | ✅ `IfcExport.ToIfc` / `ExportIfc` | ✅ `JsonExport.ExportJson` |
| Dart | ✅ | ✅ `exportGlb` | ✅ `exportObj` | ✅ `exportStl` | ✅ `exportPly` | ✅ `toDxf` / `exportDxf` | ✅ `toIfc` / `exportIfc` | ✅ `exportJson` |
| C++ | ✅ | ✅ `export_glb` | ✅ `export_obj` | ✅ `export_stl` | ✅ `export_ply` | ✅ `to_dxf` / `export_dxf` | ✅ `to_ifc` / `export_ifc` | ✅ `export_json` |

All five languages provide built-in file-writing and in-memory exporters for GLB, OBJ, STL, PLY, DXF 3D, IFC4 (BIM), and JSON metadata. Below is the Python export example:

```python
from openskp import SkpFile
from openskp.export import glb, obj, stl, ply, dxf, ifc, json_export

skp = SkpFile.open("model.skp")
model = skp.parse()
scene = skp.build_scene()

glb.export(skp, "output.glb")               # takes the SkpFile, writes .glb + .json via trimesh
obj.export(scene, "output.obj")              # takes a built Scene, writes vertices/faces only
stl.export(scene, "output.stl", binary=True) # writes 3D printing STL (ASCII/binary)
ply.export(scene, "output.ply", binary=True) # writes Stanford PLY mesh
dxf.export(scene, "output.dxf")              # writes AutoCAD R2000 3D Polyface Mesh DXF
ifc.export(scene, "output.ifc")              # writes ISO 10303-21 STEP IFC4 BIM model
json_export.export(model, "output.json", scene=scene)  # scene= populates scene_hierarchy
```

Notes on each:

- **`glb.export(skp_file, output_path, ...)`** requires `skp_file.parse()`
  to have been called first. Internally it calls the older
  `openskp._core.build_scene(parsed, output_dir, filename_stem)` helper
  (trimesh-based, writes GLB+JSON straight to disk) — a different function
  from the public `openskp.scene.build_scene()` this guide otherwise
  describes, but it is real, working, public API reachable via
  `openskp.export.glb`.
- **`obj.export(scene, output_path)`** takes a built `Scene` (not the raw
  model) and writes one `o` group per `GlbPrimitive` with `v`/`f` records
  only — no materials, normals, or UVs.
- **`json_export.to_dict(model, scene=None)` / `.export(...)`** serialize
  definitions/layers/materials from `model` always; `scene_hierarchy` is
  `None` unless a built `Scene` is passed via `scene=`, in which case it's
  the real, resolved, world-space instance tree.

TypeScript's `toGLB(scene)` provides complete, public, in-memory-to-`.glb`
bytes only (no file-write variant). C++, .NET, and Dart all provide both:
in-memory bytes (`to_glb`/`ToGlb`/`toGlb`) and direct file output
(`export_glb`/`ExportGlb`/`exportGlb`). All three of the newer writers
(C++, .NET, Dart) are from-scratch implementations with no new
dependency — .NET added a small internal JSON serializer since
`netstandard2.0` has none built in; C++'s writer uses a private, pinned
TinyGLTF dependency that does not appear in installed consumer
interfaces; Dart's uses `dart:convert`'s built-in JSON support directly.
None of the five GLB writers include OBJ export except Python (`openskp.export.obj`).
However, because every language's `buildScene()` returns the same `Scene` shape (`GlbPrimitive[]` with triangulated `positions` and `indices`), generating a Wavefront `.obj` file in any language requires only a short loop over `scene.glbPrimitives`:

```csharp
// .NET OBJ export snippet
using var writer = new StreamWriter("output.obj");
int vertexOffset = 1;
foreach (var prim in scene.GlbPrimitives) {
    for (int i = 0; i < prim.Positions.Count; i += 3)
        writer.WriteLine($"v {prim.Positions[i]} {prim.Positions[i+1]} {prim.Positions[i+2]}");
    for (int i = 0; i < prim.Indices.Count; i += 3)
        writer.WriteLine($"f {prim.Indices[i] + vertexOffset} {prim.Indices[i+1] + vertexOffset} {prim.Indices[i+2] + vertexOffset}");
    vertexOffset += prim.Positions.Count / 3;
}
```

```typescript
// TypeScript OBJ export snippet
let objText = "";
let vertexOffset = 1;
for (const prim of scene.glbPrimitives) {
  for (let i = 0; i < prim.positions.length; i += 3) {
    objText += `v ${prim.positions[i]} ${prim.positions[i+1]} ${prim.positions[i+2]}\n`;
  }
  for (let i = 0; i < prim.indices.length; i += 3) {
    objText += `f ${prim.indices[i] + vertexOffset} ${prim.indices[i+1] + vertexOffset} ${prim.indices[i+2] + vertexOffset}\n`;
  }
  vertexOffset += prim.positions.length / 3;
}
```

```dart
// Dart OBJ export snippet
final buffer = StringBuffer();
var vertexOffset = 1;
for (final prim in scene.glbPrimitives) {
  for (var i = 0; i < prim.positions.length; i += 3) {
    buffer.writeln('v ${prim.positions[i]} ${prim.positions[i + 1]} ${prim.positions[i + 2]}');
  }
  for (var i = 0; i < prim.indices.length; i += 3) {
    buffer.writeln('f ${prim.indices[i] + vertexOffset} ${prim.indices[i + 1] + vertexOffset} ${prim.indices[i + 2] + vertexOffset}');
  }
  vertexOffset += prim.positions.length ~/ 3;
}
await File('output.obj').writeAsString(buffer.toString());
```

```cpp
// C++ OBJ export snippet
std::ofstream out("output.obj");
std::uint32_t vertex_offset = 1;
for (const auto& prim : scene.glb_primitives) {
    for (std::size_t i = 0; i < prim.positions.size(); i += 3) {
        out << "v " << prim.positions[i] << " " << prim.positions[i+1] << " " << prim.positions[i+2] << "\n";
    }
    for (std::size_t i = 0; i < prim.indices.size(); i += 3) {
        out << "f " << (prim.indices[i] + vertex_offset) << " "
            << (prim.indices[i+1] + vertex_offset) << " "
            << (prim.indices[i+2] + vertex_offset) << "\n";
    }
    vertex_offset += static_cast<std::uint32_t>(prim.positions.size() / 3);
}
```

## Write capabilities

Everything above this section is about reading `.skp` files. As of this
writing, one language — Python — can also go the other direction: create a
new `.skp` file from nothing.

| Language | Write new `.skp` files |
|---|---|
| Python | 🧪 `openskp.create()` — early-stage, legacy-format (2013–2020) only |
| TypeScript | ❌ not yet |
| .NET | ❌ not yet |
| Dart | ❌ not yet |
| C++ | ❌ not yet |

`openskp.create()` returns an `SkpBuilder` that assembles a legacy MFC
`CArchive`-format `.skp` file byte-for-byte — geometry, materials
(solid-color and PNG/JPEG-textured), named layers, reusable component
definitions with multiple positioned instances, and groups — then
`.save(path)` writes it to disk. No SketchUp SDK is involved at import,
build, or save time; the writer works by inverting this project's own
reader logic (the same class-ref/back-ref object-graph protocol and entity
encodings documented in [BINARY_FORMAT.md](BINARY_FORMAT.md)), against a
small bundled blank-document scaffold it splices new entities into.

```python
from openskp import create

builder = create()
red = builder.add_material("Red", (255, 0, 0))
with builder.add_component_definition("Chair") as chair:
    chair.add_face([(0, 0, 0), (20, 0, 0), (20, 20, 0), (0, 20, 0)])
builder.add_instance(chair, translation=(50, 0, 0))
builder.add_face([(0, 0, 0), (100, 0, 0), (100, 100, 0), (0, 100, 0)], material=red)
builder.save("output.skp")
```

One ordering rule falls out of how the format's internal slot numbering
works: every `add_component_definition`/`add_group` call must happen
before any `add_face`/`add_instance` call on the builder itself — placing
root-level geometry locks in the numbering for everything that comes
after it. `ComponentDefinitionBuilder` (the object yielded by the `with`
block) is exported alongside `SkpBuilder` from the top-level `openskp`
package.

A definition can also nest instances of another, already-closed
definition inside its own body, to any depth (an assembly containing its
own sub-parts) - `ComponentDefinitionBuilder.add_instance` has the same
signature as `SkpBuilder.add_instance`:

```python
with builder.add_component_definition("Wheel") as wheel:
    wheel.add_face([(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)])
with builder.add_component_definition("Car") as car:
    car.add_instance(wheel, translation=(0, 0, 0))
    car.add_instance(wheel, translation=(100, 0, 0))
builder.add_instance(car)
```

A nested placement can also be a *group* rather than a component
instance (`add_group_instance`, same signature as `add_instance` again).
Unlike root-level `add_group`, a nested group can't be declared inline -
this format has no way to embed one definition's declaration inside
another's, so its geometry still needs a normal `add_component_definition`
first:

```python
with builder.add_component_definition("Engine") as engine:
    engine.add_face([(0, 0, 0), (30, 0, 0), (30, 30, 0), (0, 30, 0)])
with builder.add_component_definition("Car") as car:
    car.add_face([(0, 0, 0), (150, 0, 0), (150, 60, 0), (0, 60, 0)])
    car.add_group_instance(engine, translation=(50, 0, 10))
builder.add_instance(car)
```

`add_instance`/`add_group`/`add_group_instance` all also accept
`rotation=(axis, angle_radians)` as a convenience alternative to
hand-deriving a `matrix3x3` rotation matrix yourself - pass at most one
of the two:

```python
import math
builder.add_instance(wheel, translation=(0, 0, 0), rotation=((0, 0, 1), math.radians(90)))
```

A face's texture can also be explicitly positioned (scaled, rotated,
sheared, offset - independently per side) instead of the default planar
projection, given 3 world-point/UV correspondences - on a face of any
orientation, tilted or not:

```python
brick = builder.add_texture_material("Brick", "brick.png")
builder.add_face(
    [(0, 0, 0), (100, 0, 0), (100, 100, 0), (0, 100, 0)],
    material=brick,
    front_uv=[
        ((0, 0, 0), (0.0, 0.0)),
        ((50, 0, 0), (1.0, 0.0)),
        ((0, 50, 0), (0.0, 1.0)),
    ],
)
```

The in-plane 2D basis this uses for a tilted face - the face's own first
edge direction as one axis, the plane normal crossed with that as the
other - was found by comparing an SDK-authored file's own computed
matrix against several candidate formulas, then confirmed exactly (all
6 matrix values matching) against a correspondence deliberately chosen
not to align with the face's own edges.

`add_face` only stores true planar faces (all it can represent), so
non-coplanar points raise by default - pass `auto_triangulate=True` to
fan-triangulate instead, the same silent fallback real SketchUp's own UI
applies to a not-quite-flat quad you draw by hand:

```python
warped_quad = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 5)]
builder.add_face(warped_quad, auto_triangulate=True)  # -> 2 triangular faces
```

A face can also have one or more holes cut out of it - `holes=` takes
a list of independent closed polygons, each on the same plane as the
face itself; winding direction doesn't matter:

```python
wall = [(0, 0, 0), (200, 0, 0), (200, 100, 0), (0, 100, 0)]
window = [(80, 30, 0), (120, 30, 0), (120, 70, 0), (80, 70, 0)]
builder.add_face(wall, holes=[window])
```

Ground-truth-derived from an SDK-authored window-in-a-wall face: a hole
is a real additional loop in the same `CFace` record - structurally
identical to the boundary loop, differing only in one flag byte.
Confirmed against the real SDK that the hole's area is genuinely
subtracted (`SUFaceGetArea`), not just structurally present.

Component definitions, instances, and faces can also carry custom
key/value metadata - the same mechanism SketchUp's own "dynamic
component" attributes use - via each of their `attributes` parameters
(values may be `str`, `int`, or `float`):

```python
with builder.add_component_definition("Chair", attributes={"sku": "CH-100", "price": 49.99}) as chair:
    chair.add_face([(0, 0, 0), (20, 0, 0), (20, 20, 0), (0, 20, 0)])
builder.add_instance(chair, attributes={"serial": "A1"})
```

Not yet supported on groups - ground truth shows a group's own attribute
pointer is always null, unlike a component instance's (real) one.

A circular face (`add_circle`) is a genuine, editable-by-radius
SketchUp arc/circle entity (`CArcCurve`), not `num_segments`
disconnected straight edges that merely trace that shape - every edge in
the tessellation shares one real curve backref, the same object graph
real SketchUp's own Circle tool produces:

```python
builder.add_circle((50, 50, 0), normal=(0, 0, 1), radius=40, num_segments=24)
```

Confirmed against the real SDK: every edge's `SUEdgeGetCurve` resolves to
the exact same curve object, typed as a genuine arc (`SUCurveGetType`)
with the requested edge count.

A partial (open) arc (`add_arc`) is the same underlying `CArcCurve`
entity, but a chain of edges with no face - given `start_angle`/
`end_angle` (radians), swept from an arbitrary but fixed reference
direction in the arc's own plane:

```python
import math
builder.add_arc((50, 50, 0), normal=(0, 0, 1), radius=40, start_angle=0, end_angle=math.pi / 2)
```

Confirmed against the real SDK that the written endpoint coordinates
land exactly where the requested sweep says they should - not just that
some curve object exists with the right edge count.

A freeform polyline (`add_polyline`) groups an arbitrary chain of
straight edges into one genuine `CCurve` entity - distinct from
`CArcCurve`: no geometric frame of its own, just a type tag and an edge
count, the same grouping real SketchUp's own Freehand tool produces.
`closed=True` also connects the last point back to the first:

```python
builder.add_polyline([(0, 0, 0), (10, 10, 0), (20, 0, 0), (30, 10, 0)])
```

Confirmed against the real SDK that every edge shares the same curve
object, typed as `SUCurveType_Simple` (distinct from the arc/circle
tests' `SUCurveType_ArcCurve`), with the correct edge count.

Explicitly out of scope for this first pass: declaring a group's
geometry inline nested inside another definition (as opposed to placing
an already-built one via `add_group_instance`), and attributes on
groups. See [`openskp/create.py`](../packages/python/src/openskp/create.py)
for the full, current scope notes, and the [Python package README](../packages/python/README.md#writing-early-stage)
for a longer worked example.

### Editing an existing file

`openskp.create` only ever builds a brand-new file from its own blank
scaffold - real SketchUp never patches a file in place either (it fully
re-serializes the whole document on every save), so there's no stable
byte region to append to for an arbitrary existing file the way there is
for that blank scaffold. `openskp.open_existing()` takes the other
viable approach: fully parse the existing file with this project's own
reader, then replay everything it understood - materials, layers, every
component definition, all root-level geometry and instances - back
through the writer's own API, producing a brand-new file with equivalent
content that more geometry can still be added to before saving:

```python
from openskp import open_existing

builder, warnings, definitions = open_existing("building.skp")
for w in warnings:
    print("not fully reproduced:", w)

builder.add_circle((0, 0, 100), (0, 0, 1), radius=50)
builder.save("building_edited.skp")
```

Only a legacy-format (SketchUp 2013-2020) source file is accepted, for
the same reason `openskp.create` only ever writes that format. The
returned `warnings` list is the honest account of what couldn't be
faithfully reproduced for that specific file - per-edge flags collapsed
to a per-face approximation, a projected/distorted texture falling back
to the default projection, a material's texture scale or colorized tint,
an instance's hidden flag, a layer's color, section planes/text/
dimensions (no writer support at all), and an original circle/arc's
curve grouping (this project's reader doesn't preserve it, so it
round-trips as a plain straight-edged face) - see
[`openskp/edit.py`](../packages/python/src/openskp/edit.py)'s own
module docstring for the complete, itemized list and the reasoning
behind each one. Round-trip-validated against real, non-writer-authored
architectural models, not just files this project's own writer produced.

Every material/layer the source had is already reachable on the
returned `builder` without a separate lookup - `builder.materials_by_name
["Walnut"]`/`builder.layers_by_name["Roof"]` - and `definitions` maps
each replayed component definition's own name to its builder, for
placing more instances of something the source already defined:

```python
builder.add_face(points, material=builder.materials_by_name["Walnut"])
builder.add_instance(definitions["Wheel"], translation=(50, 0, 0))
```

What the returned `builder` can no longer do is register a genuinely
NEW material, layer, or component definition/group - by the time
replay finishes writing the source's own root-level geometry, this
writer's usual file-format ordering requirement (materials/layers/
definitions must be finalized before any geometry) is already
satisfied, so `add_material`/`add_layer`/`add_component_definition`/
`add_group` all raise on this particular builder. Build anything new
into a separate `create()` call instead.

## The web viewer

[`examples/web-viewer/`](../examples/web-viewer/) is a full drag-and-drop
3D viewer built on the TypeScript package and Three.js — deployed live at
the link in the [README](../README.md). It calls both `parseSkp()` (for
version/layers/materials metadata) and `buildScene()` (for the actual
renderable meshes) on the same buffer, and uses Three.js's own
`GLTFExporter` for the "Export GLB" button rather than this package's
`toGLB()`.

To run it locally:

```bash
cd packages/typescript
npm run build && npm run copy-dist
cd ../../examples/web-viewer
python serve.py    # serves the directory at http://localhost:8000
```

It's deployed automatically by `.github/workflows/deploy-pages.yml` on
every push to `main` that touches `packages/typescript/**` or
`examples/web-viewer/**` — the workflow runs exactly the two build steps
above, then publishes `examples/web-viewer/` to GitHub Pages.

## Known cross-language differences

Honest list of places where the five ports currently do *not* behave
identically. None of these are bugs in the sense of "produces wrong data"
— each language's behavior is internally consistent and correct for what
it does — but code written against one language's shape will not port
directly to another's without adjustment.

### Root-level definition access

Resolved as of this session — all five languages now agree.
`model.definitions`/`model.Definitions` is strictly numeric-keyed (no
root entry mixed in) in every language; the implicit top-level
definition is always a separate `model.root` / `model.Root` /
`model.root()` property with the same `Definition` shape. (Python
previously mixed a string `'ROOT'` key into `model.definitions`, requiring
consumers to `isinstance(key, int)`-check every key — fixed to match the
other four; TypeScript previously dropped root-level data from `parse()`
entirely — also fixed, earlier in the same session.)

### GLB/OBJ/STL/PLY/DXF/IFC/JSON export

Covered above under [Export capabilities](#export-capabilities) — GLB, Wavefront OBJ, STL (3D Printing), PLY (Stanford Mesh), DXF 3D (AutoCAD R2000 compliant), IFC4 (BIM ISO STEP), and JSON metadata export are natively supported in all five languages. All ports provide both in-memory string/buffer formatting (`to_ifc`/`toIFC`/`toIfc`/`ToIfc`) and direct file output functions (`export_ifc`/`exportIFC`/`exportIfc`/`ExportIfc`).

### Progress/logging mechanism

Not a bug, but worth restating: Python's progress is DEBUG-level log
records through the standard `logging` module (`logging.getLogger("openskp")`),
preserving standard Python library conventions without requiring explicit callback
delegates; TypeScript/.NET/Dart/C++ have an explicit progress callback delegate
distinct from logging. See [OBSERVABILITY.md](OBSERVABILITY.md#per-language-mechanism)
for the full comparison and rationale.

### .NET static `SkpFile` API shape

The .NET port exposes `SkpFile` as a static class with factory methods
(`SkpFile.Parse`, `SkpFile.BuildScene`, `SkpFile.Open`), rather than requiring an
instantiated file handle object before invoking `.Parse()`. This is a deliberate
C# idiom choice that matches standard .NET framework library designs (e.g. `System.IO.File`).

### Write support — Python only

Covered above under [Write capabilities](#write-capabilities): Python is
currently the only port that can create new `.skp` files (`openskp.create()`).
This is a genuine capability gap, not a shape difference — TypeScript,
.NET, Dart, and C++ remain read/export-only until a writer is ported to
each. Contributions bringing the other four ports to parity are welcome.

### C++ `materials_by_id()` helper

`SkpModel::materials_by_id()` returns a `std::map<EntityId, Material*>` (or `const Material*`),
providing an enumerable map of materials keyed by numeric TLV entity ID. This matches the
enumerable dictionary/map properties exposed by Python (`model.materials_by_id`), TypeScript
(`model.materialsById`), Dart (`model.materialsById`), and .NET (`model.MaterialsById`).

## Troubleshooting

**"Not a valid SketchUp file (bad header magic)"** — the file doesn't
start with `FF FE FF 0E`. Either it's not a `.skp` file, or it's been
corrupted/truncated. Check `error.stage === 'header'`
(TypeScript)/`e.Stage == "header"` (.NET)/etc.

**Parsing a large file runs out of memory** — see
[Memory and performance](#performance) above. Try the light `parse()`
before `buildScene()` if you don't need the baked scene; raise your
runtime's heap limit (Node/Dart) if you do.

**A file parses but geometry looks empty/wrong** — check
`model.layers`/`model.materials` populated correctly first (confirms the
ZIP/XML side worked); if those are fine but geometry is missing, the file
may use a TLV tag combination not yet handled — please
[open an issue](https://github.com/iamahsanmehmood/openskp/issues) with
the file if you're able to share it (or a minimal reproduction).

**`buildScene()` is slow / produces more data than expected** — this is
expected for files that reuse a small number of definitions across many
placements; see [the two-entry-point explanation](#two-entry-points-parse-and-buildscene)
above. If you only need per-definition geometry (not resolved world-space
instances), use `parse()` instead.
