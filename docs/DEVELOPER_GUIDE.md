# Developer Guide

This is the detailed, practical guide to building on OpenSKP: what each
language's API actually gives you, how memory and performance behave on
real files, how to plug in progress/error observability, and where the
four ports currently differ from each other. If you just want the pitch
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
- [The web viewer](#the-web-viewer)
- [Known cross-language differences](#known-cross-language-differences)
- [Troubleshooting](#troubleshooting)

---

## Installation

| Language | Install | Current version |
|---|---|---|
| Python | `pip install openskp` | 0.2.0 |
| TypeScript / JavaScript | `npm install openskp` | 0.2.0 |
| .NET / C# | `dotnet add package OpenSkp` | 0.3.0 |
| Dart / Flutter | `dart pub add openskp` | 0.3.0 |

All four are independent packages sharing one reverse-engineered format
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
resolved to world space, each face triangulated (via a ported
[earcut](https://github.com/mapbox/earcut) - the same ear-clipping
algorithm in all four languages) and grouped by resolved color into
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

## The data model

All four languages produce structurally equivalent output for the same
file — same counts, same coordinates, same topology, cross-validated
directly against each other on real fixtures (not just against each
language's own idea of what the format means).

| Concept | Python | TypeScript | .NET | Dart |
|---|---|---|---|---|
| Entry point | `SkpFile.open(path).parse()` | `SkpFile.open(path).parse()` | `SkpFile.Open(path)` | `SkpFile.open(path).parse()` |
| Top-level result | `SkpModel` | `SkpModel` | `SkpModel` | `SkpModel` |
| Definitions | `dict[int \| str, Definition]` | `Map<number, Definition>` | `Dictionary<long, Definition>` | `Map<int, Definition>` |
| Vertex | `Vertex(id, x, y, z)` | `{id, x, y, z}` | `Vertex{Id,X,Y,Z}` | `Vertex(id,x,y,z)` |
| Edge | `Edge(id, v1_id, v2_id, soft, smooth, hidden)` | `{id, v1Id, v2Id, soft, smooth, hidden}` | `Edge{...}` | `Edge(...)` |
| Face | `Face(id, loops, normal, material_id, back_material_id, uv_transform, uv_transform_back)` | same, camelCase | same, PascalCase | same, camelCase |
| Layer | `Layer(name, color_r, color_g, color_b)` | `{name, color: {r,g,b}}` | `Layer{Name,ColorR,...}` | `Layer(name, colorR, ...)` |
| Material | `Material(name, color, transparency, id, texture, colorized, colorize_type)` | same | same | same |
| Instance (unresolved placement) | `Instance(name, ref_idx, guid, matrix, material_id)` | same | same | same |

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

All four languages now parse **one top-level record at a time**:
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
only the orchestration loop changed, in all four languages, the same way.

### .NET's additional fix: no array-size ceiling

.NET has one constraint the other three don't: the CLR's array and
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

All four languages support opt-in progress reporting and structured error
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

See [OBSERVABILITY.md](OBSERVABILITY.md) for the full stage vocabulary,
error field reference, and design rationale.

## Error handling

Every language raises/throws a structured error type — never a bare
string — for failures anywhere in the parse or scene-build path:
`SkpParseError` (Python, TypeScript) / `SkpParseException` (.NET, Dart).
Full field reference in [OBSERVABILITY.md](OBSERVABILITY.md#error-fields).

Two other exceptions you may see that are *not* this type, and mean
something more basic:

- **File-not-found / wrong extension** — Python: `FileNotFoundError`
  /`ValueError`; TypeScript: n/a in the browser (you supply the buffer),
  Node's `SkpFile.open()` throws whatever `fs.readFileSync` throws; .NET:
  `FileNotFoundException`/`ArgumentException`; Dart:
  `FileSystemException`/`ArgumentError`. These happen before any actual
  parsing starts.

## Export capabilities

`buildScene()`'s result (`Scene`, `GlbPrimitive[]`, `gltfMaterials`) is
already exactly the data a GLB/glTF exporter needs — triangulated,
world-space, grouped by material. What differs is whether each language
ships the last step (serializing that data into an actual `.glb` binary
file) for you:

| Language | Scene data (`buildScene()`) | Binary `.glb` serializer |
|---|---|---|
| TypeScript | ✅ | ✅ `toGLB(scene)` in `index.ts` |
| Python | ✅ | ⚠️ not in the public API — see below |
| .NET | ✅ | ❌ not yet ported |
| Dart | ✅ | ❌ not yet ported |

TypeScript is the only port with a complete, public, in-memory-to-`.glb`-
bytes function today (`toGLB()`, used by the [web viewer](#the-web-viewer)'s
"Export GLB" button). Python has an older, *internal* helper
(`openskp._core.build_scene(parsed, output_dir, filename_stem)` — note the
different signature and module from the public `openskp.scene.build_scene()`
this guide describes) that writes GLB+JSON straight to disk via `trimesh`;
it predates this session's public `Scene` API, is not part of the
documented public surface, and shouldn't be relied on directly by new code.
.NET and Dart consumers who need a `.glb` file today need to serialize
`Scene`'s `GlbPrimitive`s themselves (the format is simple — see the glTF
2.0 spec, or read TypeScript's `toGLB()` for a reference implementation of
exactly this data shape).

Wavefront OBJ and JSON export are **not currently implemented** in any of
the four languages' public APIs, despite being mentioned in older project
notes — if you need OBJ output today, build it from `Scene`'s
already-triangulated primitives yourself (each `GlbPrimitive` is flat
position/normal/index arrays, which is most of the work an OBJ writer
needs anyway).

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

Honest list of places where the four ports currently do *not* behave
identically. None of these are bugs in the sense of "produces wrong data"
— each language's behavior is internally consistent and correct for what
it does — but code written against one language's shape will not port
directly to another's without adjustment.

### Root-level definition access

- **Python**: `model.definitions` is a single dict that includes a
  `'ROOT'` **string key** alongside the integer definition IDs. There is
  no separate `.root` attribute. Consumers must check
  `isinstance(key, int)` to distinguish real definitions from the root.
- **TypeScript, .NET, Dart**: `model.definitions`/`model.Definitions` is
  strictly numeric-keyed (no root entry mixed in); the root is a separate
  `model.root` / `model.Root` property with the same `Definition` shape.

(TypeScript used to drop root-level data from `parse()` entirely — fixed
this session to add `model.root`, matching .NET/Dart. Python's differing
shape predates this session and is tracked as a follow-up, not yet
changed, since Python's `.definitions` shape may have existing consumers
relying on the string-keyed `'ROOT'` entry.)

### GLB/OBJ/JSON export

Covered above under [Export capabilities](#export-capabilities) — only
TypeScript has a public, complete `.glb` binary serializer today.

### Progress/logging mechanism

Not a bug, but worth restating: Python's progress is DEBUG-level log
records through the standard `logging` module (no separate numeric
callback); TypeScript/.NET/Dart have an explicit `onProgress`/`Progress`
callback distinct from logging. See [OBSERVABILITY.md](OBSERVABILITY.md#per-language-mechanism)
for the full comparison and why.

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
