# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — Python only

**Initial write support.** OpenSKP can now *create* new `.skp` files from
scratch, not just parse existing ones — a genuine, from-scratch binary
writer for the legacy MFC `CArchive` format (SketchUp 2013–2020), built by
inverting the existing reader's own decoding logic rather than wrapping
any SDK. Early-stage and Python-only for now; the other four language
ports do not yet have this capability. See
[`packages/python/src/openskp/create.py`](packages/python/src/openskp/create.py)
for the full scope notes.

### Added

- `openskp.create()` / `SkpBuilder` — build faces (planar, including
  concave polygons and non-manifold shared edges) directly from vertex
  coordinates, with automatic vertex/edge sharing wherever coordinates
  coincide exactly.
- Solid-color and image-textured materials (`add_material`,
  `add_texture_material` — PNG and JPEG, detected from the file's own
  magic bytes), assignable independently to a face's front and back side.
- Named layers (`add_layer`).
- Reusable component definitions with multiple independently-positioned
  instances (`add_component_definition`, `add_instance`), and groups
  (`add_group`, which place themselves automatically on close rather than
  needing a separate placement call).
- Nested definitions — a component definition can contain instances of
  another, already-built definition inside its own body
  (`ComponentDefinitionBuilder.add_instance`), the same assembly-of-parts
  nesting real SketchUp supports, to any depth. A nested placement can
  also be a *group* rather than a component instance
  (`ComponentDefinitionBuilder.add_group_instance`) — this format has no
  way to declare one definition's body inside another's, so the group's
  own geometry still has to be built with a normal
  `add_component_definition` first, then placed here.
- Explicit texture positioning (`add_face`'s `front_uv`/`back_uv`) —
  scale, rotate, shear, and offset a face's texture independently per
  side instead of the default planar projection, given 3 world-point/UV
  correspondences. Works on a face of any orientation, tilted or not.
- Per-face/per-edge hidden, soft, and smooth flags.
- Custom key/value attribute dictionaries (`attributes` on
  `add_component_definition`, `add_instance`, and `add_face`) — the same
  mechanism SketchUp's own "dynamic component" attributes use. Values may
  be `str`, `int`, or `float`; not yet supported on groups, since ground
  truth shows a group's own attribute pointer is always null unlike a
  component instance's.
- Circular faces (`add_circle` on `SkpBuilder`/`ComponentDefinitionBuilder`)
  — a genuine, editable-by-radius SketchUp arc/circle entity (`CArcCurve`),
  not `num_segments` disconnected straight edges that merely trace that
  shape. Every edge in the tessellation shares one real curve backref,
  confirmed via the SDK's own `SUEdgeGetCurve`/`SUCurveGetType` to resolve
  to a single, correctly-typed arc entity.
- Partial (open) arcs (`add_arc`) — the same genuine `CArcCurve` entity as
  `add_circle`, but a chain of edges with no face, swept between
  caller-given `start_angle`/`end_angle` (radians). Confirmed via the SDK
  that the written endpoint coordinates land exactly where the requested
  sweep says they should, not just that "some curve object" exists.
  Freeform polyline curves (`CCurve`, distinct from a true arc) are not
  yet exposed as public API.
- Every file now opens to the standard "Iso" view (parallel projection,
  looking at the origin) instead of the blank scaffold's own arbitrary
  default camera.
- No SketchUp SDK dependency at import, write, or any other runtime path.
  The bundled blank-document scaffold this module splices geometry into
  is disclosed plainly as SDK-authored boilerplate (Trimble's own
  built-in empty-document bytes, not anyone's creative work) in the
  module's own docstring — the writer logic itself (the entity encoding,
  the object-graph protocol, the tail-reference renumbering) is 100%
  independently reverse-engineered.

### Fixed

- Calling `add_face`/`add_instance` on the root builder while a component
  definition was still open (its `with` block not yet exited) silently
  produced a corrupted file instead of raising — found while testing
  group nesting, unrelated to it otherwise. Now raises immediately.

### Validation

Every capability above is verified against the real SketchUp SDK
(`SketchUpAPI.dll` used strictly as a local, offline validation oracle —
never a runtime dependency), not just against OpenSKP's own reader —
several silent-failure fields (drawbase padding, loop flags, attribute
container requirements) only show up as `SU_ERROR_MODEL_INVALID` in real
SketchUp despite parsing cleanly through this project's own code. A
combined "kitchen sink" test exercises every feature together in one
file (materials, layers, textures, definitions, instances, groups,
concave/non-manifold geometry) and is checked at scale (hundreds of
entities) as a regression guard.

### Explicitly out of scope for this first pass

- Declaring a group's geometry inline nested inside another definition's
  own body, the way `add_group` self-places at the root level - this
  format has no mechanism for one definition's declaration to live inside
  another's, so a nested group's geometry has to be built separately
  first (see `add_group_instance` above).
- Editing an existing arbitrary `.skp` file — a separately harder
  problem (real SketchUp re-serializes the whole document on save rather
  than appending), not attempted here.
- The other four language ports (TypeScript, .NET, Dart, C++) do not
  have write support yet.

## [1.0.0] — 2026-08-13

First stable release. All five language ports (Python, TypeScript, Dart,
C# / .NET, C++) now carry full feature parity: parsing (geometry,
materials, layers, dynamic properties, metadata) and native, dependency-light
export to GLB, JSON, Wavefront OBJ/MTL, STL, PLY, DXF (3DFACE and AutoCAD
Polyface Mesh), and IFC4 (BIM) — verified against real-world `.skp` fixtures
and, for DXF specifically, against real desktop AutoCAD rather than lenient
readers alone.

### Added

- **All 5 languages**: native Wavefront OBJ exporter (`to_obj`/`toOBJ`/
  `toObj`/`ToObj`, plus a file-writing counterpart in each language —
  Python's is `openskp.export.obj.export()`, the other four are
  `exportOBJ`/`exportObj`/`ExportObj`). InvariantCulture/classic-C-locale
  formatting is enforced everywhere to guarantee dot decimal separators
  regardless of the host OS locale.
- **All 5 languages**: native STL exporter, both ASCII (`.stl`) and
  little-endian binary (`_bin.stl`), with an optional `scale` multiplier
  (default 1.0 for metres, 1000.0 for mm, matching what slicers like Cura/
  PrusaSlicer/Bambu Studio expect). Verified byte-identical output (9,368,084
  bytes, 187,360 triangles) across languages on the same real fixture.
- **All 5 languages**: native PLY exporter (Polygon File Format), both ASCII
  and little-endian binary, carrying vertex positions, normals, UV
  coordinates, and RGBA vertex colors. C++'s binary writer uses
  architecture-independent bitwise shifts rather than relying on host
  endianness. Verified byte-identical output (9,316,015 bytes, 187,360
  triangles) across languages on the same real fixture.
- **All 5 languages**: rich Wavefront OBJ/MTL extension — a companion
  `.mtl` material-library writer (`to_mtl`/`toMTL`/`toMtl`/`ToMtl`) emitting
  `newmtl`/`Ka`/`Kd`/`Ks`/`Ns`/`d`/`illum` records plus `map_Kd` texture
  references, with `to_obj` gaining an optional `mtl_filename` parameter to
  link the two via `mtllib`. The `.obj` writer itself gained `vt` (UV) and
  `vn` (normal) records, upgrading it from a positions-only debug dump to a
  properly textured/shaded mesh format.
- **All 5 languages**: native 3D DXF exporter, targeting AutoCAD R2000
  (`AC1015`) compliance — full HEADER/CLASSES/TABLES/BLOCKS/OBJECTS
  scaffold, `LAYER` table entries with ACI color codes, and entity output in
  either `3DFACE` mode or AutoCAD Polyface Mesh mode (`POLYLINE` type 64 +
  `VERTEX` + `SEQEND`). Polyface is now the default mode (previously
  `3dface`), since it's the form AutoCAD itself generates for triangulated
  meshes. See Fixed below — the exporter's real-world compatibility with
  desktop AutoCAD required a second, much deeper pass beyond what made
  lenient DXF readers (`ezdxf`) accept it.
- **All 5 languages**: native IFC4 (BIM) exporter — ISO-10303-21 STEP ASCII
  output with a full `IfcProject → IfcSite → IfcBuilding → IfcBuildingStorey
  → IfcProduct` spatial hierarchy, `IfcTriangulatedFaceSet` tessellated
  geometry, best-effort element classification (walls/doors/windows/slabs/
  columns/beams/roofs, defaulting to `IfcBuildingElementProxy`), dynamic
  properties via `IfcPropertySet`/`IfcRelDefinesByProperties`, material
  colors via `IfcColourRgb`/`IfcSurfaceStyleRendering`, and layer
  preservation via `IfcPresentationLayerAssignment`. Validated with
  `IfcOpenShell` against two real-world files, including a 26.94 MB / 101,692-
  property export.
- **Web viewer**: the single "Export GLB" button is now an "Export Model"
  dropdown covering every format the library supports — JSON, IFC4, DXF
  (both Polyface Mesh and 3DFACE), PLY, STL, GLB, and OBJ. All formats share
  one `downloadFile()` helper (Blob + object URL, revoked after use), and
  exported filenames now derive from the uploaded `.skp`'s own name instead
  of a fixed stem.

- **Dart**: `toGlb(Scene)`/`exportGlb(Scene, path)` - binary glTF 2.0
  (GLB) export, matching Python's and C++'s `to_glb`/`export_glb` pair
  (same scope as the .NET entry below). A from-scratch writer with no new
  dependency - `dart:convert`'s built-in `jsonEncode` covers the JSON
  chunk directly, no custom serializer needed (simpler than .NET's port,
  which had to hand-roll one since `netstandard2.0` has no built-in JSON
  support). Same TEXCOORD_0 correction as .NET's port relative to the
  TypeScript reference. `exportGlb` doesn't create missing parent
  directories, matching C++/.NET's `export_glb`/`ExportGlb`. One
  Dart-specific wrinkle: `GlbPrimitive`'s fields are `List<double>`
  (64-bit) here, but the binary accessor data is float32 - min/max
  bounds are now read back from the already-written float32 buffer
  rather than computed from the raw doubles, so they match what's
  actually in the accessor. Verified against the real fixture: chunk
  headers, mesh/material counts, and decoded UV values all confirmed
  correct, byte-for-byte identical (after accounting for float32
  rounding) to the already cross-verified Python/TypeScript/C#/.NET
  output on the same file.
- **.NET**: `GlbExport.ToGlb(Scene)`/`GlbExport.ExportGlb(Scene, path)` -
  binary glTF 2.0 (GLB) export, matching Python's and C++'s
  `to_glb`/`export_glb` pair (TypeScript only has the bytes-returning
  variant). A from-scratch writer with no new dependency, matching how
  this project has stayed dependency-light everywhere except C++'s
  bundled TinyGLTF - `netstandard2.0` has no built-in JSON support, so
  this also adds a small internal `MiniJson` serializer (reflection-based,
  just enough to cover the object graphs this writer builds; not a
  general-purpose JSON library). Ported from the TypeScript reference
  implementation's `toGLB()`, with one correction: TS's own `toGLB()`
  still doesn't write `TEXCOORD_0` despite `GlbPrimitive.uvs` existing
  there too (tracked as a separate follow-up) - .NET's writer includes it
  from the start. `ExportGlb` doesn't create missing parent directories,
  matching C++'s `export_glb` (the other language with this same pair).
  Verified against a real fixture: magic/chunk headers, mesh/material
  counts, and decoded UV values all confirmed correct, with the UV values
  byte-for-byte identical to the already cross-verified Python/TypeScript/
  C#/Dart output on the same file.
- **Python**: `export.json_export.to_dict()`'s output gained a top-level
  `root` key with the model's implicit top-level definition (matching
  `SkpModel.root`) - previously only `definitions` (the numeric-ID-keyed
  component/group definitions) was included, so the JSON export silently
  omitted any geometry/instances placed directly in the model rather than
  inside a component.

- **TypeScript**: `Face` gained `uvProjected`/`uvProjectedBack` fields.
  The legacy MFC reader already decoded these bits internally
  (`front_projected`/`back_projected` on the `CFaceTextureCoords` record)
  but discarded them instead of exposing them, same gap Python closed in
  #61. A PROJECTED texture (e.g. the Add Location terrain drape) has UVs
  that run in the projection plane's frame, not the face frame - callers
  need to know this to render it correctly. VFF/modern files don't carry
  this flag at all, so it correctly defaults to `false` there, matching
  Python's precedent.
- **Dart**: `Face` gained `uvProjected`/`uvProjectedBack` fields, same fix
  and same rationale as the TypeScript entry above.
- **.NET**: `Face` gained `UvProjected`/`UvProjectedBack` properties, same
  fix and same rationale as the TypeScript/Dart entries above.
- **C++**: `Face` gained `uv_projected`/`uv_projected_back` fields, same
  rationale as the TypeScript/Dart/.NET entries above. See the Fixed
  section for why this needed more than exposing two bits.

- **Python**: `scene.GlbPrimitive` gained a `uvs` field with real per-vertex
  texture coordinates, computed from each source face's `uv_transform` (or
  the default face-plane projection when a face has none) — see
  `Face.uv_transform`'s docstring for the formula. Vertices are now split
  where two faces sharing a position disagree on UV, since indexed glTF
  meshes need position/normal/uv aligned per vertex. Fixes #62 for Python;
  other languages exposing the same `GlbPrimitive` shape are being ported
  separately. Faces with `uv_projected` set (terrain-drape textures) still
  use the face-plane formula, since the real projection-plane basis isn't
  captured anywhere in the parsed data yet — their UVs are approximate.
- **.NET**: `Scene.GlbPrimitive` gained a `Uvs` field, same fix as the
  Python entry above and the exact issue #62 was filed against (its
  `GlbPrimitive` snippet). Verified numerically identical to Python's
  output (to float precision) on the same real fixture.
- **TypeScript**: `GlbPrimitive` gained a `uvs` field, same fix as the
  Python/.NET entries above. Verified numerically identical to both on the
  same real fixture.
- **C++**: `GlbPrimitive` gained a `uvs` field, same fix as the other three
  ports. Unlike those, C++'s `to_glb`/`export_glb` write real `.glb` files
  directly from this struct, so this PR also wires a `TEXCOORD_0` accessor
  into the actual glTF output - exported files now carry real texture
  coordinates, not just the in-memory struct. C++ also uniquely models
  front/back materials as separate primitives; the back-side primitive
  (when present) uses `uv_transform_back` with the same face-plane basis
  as the front, per the documented recipe - this path has no cross-language
  reference to verify against, since no other port models back materials
  at all.
- **Dart**: `GlbPrimitive` gained a `uvs` field, same fix as the other
  ports. This closes out #62 across every language whose scene-baking
  layer exposes `GlbPrimitive` - Python, .NET, TypeScript, C++, and now
  Dart all compute numerically identical UV values (to float precision) on
  the same real fixture. Dart has no `.glb` file writer yet (tracked
  separately), so this reaches the `GlbPrimitive` data shape only, same as
  the Python/.NET/TypeScript entries above.
- **Python**: `export.glb.export()` - the actual `.glb` file writer, a
  separate legacy pipeline from `scene.py`'s `build_scene()` - now writes
  real per-vertex UV coordinates too, closing the same gap as above for
  Python's literal exported files (matching what C++'s PR already did for
  its own file writer). Faces are now grouped into one mesh per resolved
  color per definition, same as `scene.py`, since a single trimesh mesh
  can only carry one material - previously this pipeline put every face
  of a definition into one mesh with a flat per-face color array,
  regardless of how many distinct materials were mixed in (confirmed on a
  real fixture: 2 of 3 definitions mix colors). Verified numerically
  identical UV output to `scene.py`'s on three real files (both legacy
  MFC and modern VFF format).

- **C++**: public `to_glb(const Scene&)` and `export_glb(const Scene&, path)`
  APIs for in-memory and file-based binary glTF 2.0 export. The implementation
  uses privately bundled TinyGLTF 2.9.7, validates scene geometry and PBR data,
  and keeps TinyGLTF out of installed headers and consumer link interfaces.

### Fixed

- **All 5 languages**: the DXF exporter produced files that `ezdxf.readfile()`
  (and even `ezdxf`'s own `.audit()`) accepted without complaint, but real
  desktop AutoCAD rejected outright — first with "Invalid or incomplete DXF
  input", later with "Did not receive PlotStyleName" once the more obvious
  problems were fixed. `ezdxf` is too lenient to catch what AutoCAD enforces
  internally, and there's no public, comprehensive list of those rules, so
  this was tracked down using real desktop AutoCAD as the only reliable
  oracle, then cross-checked against what real `ezdxf` itself emits for
  equivalent structures. Five distinct root causes, byte-verified against a
  real `ezdxf`-generated file confirmed to open cleanly in AutoCAD:
  1. An incomplete HEADER/CLASSES/TABLES/BLOCKS/OBJECTS scaffold — a
     near-empty `CLASSES` table, a fabricated `VPORT` record, and a stripped
     `OBJECTS` dictionary tree missing `MATERIAL`/`MLINESTYLE`/
     `MLEADERSTYLE` records and a second `LAYOUT` record — all boilerplate
     AutoCAD requires but lenient readers never check.
  2. Every dynamically-generated `LAYER` record was missing group `370`
     (lineweight) and `390` (PlotStyleName handle); AutoCAD rejects the
     whole `TABLES` section without them once a drawing uses a Named plot
     style table. Also dropped group `420` (24-bit true color), an R2004+
     field real `ezdxf` never emits for R2000/AC1015 — ACI (`62`) is the
     only color mechanism R2000 supports.
  3. Polyface `POLYLINE`/`VERTEX`/`SEQEND` structure had three mismatches
     against real `ezdxf`'s own polyface output: face-record `VERTEX`
     entries were missing color (`62`) and dummy `0/0/0` coordinates while
     carrying a subclass marker they shouldn't have, and `SEQEND`'s owner
     (`330`) pointed at Model_Space instead of its parent `POLYLINE`'s own
     handle. This is exactly why `3dface` mode opened fine while `polyface`
     mode was rejected, despite sharing the same scaffold.
  4. TypeScript, Dart, C#, and C++ never substituted the `$HANDSEED`
     placeholder — every file these four exporters ever produced shipped
     the literal text `__HANDSEED__` instead of a real hex value.
  5. Unbounded layer names could exceed AutoCAD's documented limits; added a
     defensive 255-character cap with a collision-safe hash suffix on
     truncation.

  Dynamic handles now start at `0x691`, matching the reference file's own
  first entity handle, keeping every handle in an export globally unique.
  Both `3dface` and `polyface` output confirmed opening cleanly in real
  desktop AutoCAD after this fix (C++ mirrors the same verified structure
  but could not be locally compiled/run in the environment this was fixed
  in — CI is the correctness gate there).
- **Python**: `export.dxf.export()` didn't set `newline=''` when opening the
  output file on Windows, so `open()`'s universal-newline translation turned
  every `\r\n` the writer emitted into `\r\r\n` (double carriage returns),
  which CAD viewers rejected. Explicit 3DFACE output was affected before the
  fix above even applied.
- **TypeScript**: `toGLB()` never wrote a `TEXCOORD_0` accessor, despite
  `GlbPrimitive.uvs` existing on its data model since #65 - real UV data
  was computed but the actual exported `.glb` bytes never carried it.
  Found while using `toGLB()` as the structural reference for porting GLB
  export to .NET and Dart (both new writers included `TEXCOORD_0` from
  the start rather than reproducing this gap). Same shape as the gap
  already fixed for Python's `export/glb.py` (#68) and C++'s `glb.cpp`
  (#66). No test coverage existed for `toGLB()` before now, which is how
  this went unnoticed - added real coverage, including a real-fixture
  round-trip test that decodes every primitive's `TEXCOORD_0` back out of
  the binary chunk and confirms it matches the source `GlbPrimitive.uvs`
  exactly.
- **C++**: `Face.uv_transform`/`uv_transform_back` were never populated for
  *any* legacy MFC (SketchUp 2013–2020) file - every legacy face's UV
  silently fell back to the default (non-positioned) face-plane
  projection, even when the SketchUp author had explicitly positioned or
  photo-fitted a texture. Root cause: `CFace`'s attribute container (the
  MFC record that holds its `CFaceTextureCoords` mapping, alongside any
  other attribute dictionaries) was being read - correctly advancing the
  archive cursor - and then discarded, never linked back to the face.
  Found while investigating a smaller, originally-scoped task (exposing
  the already-decoded PROJECTED-texture bit); turned out the bit lived in
  a record that was never reachable at all. Fixed by capturing the
  attribute container's slot on `CFace`, capturing attribute-container
  children as they're read (previously discarded there too), and
  resolving both when building each face. Verified against a real fixture
  the count of faces with a real `uv_transform` now matches Python's
  independently-verified count exactly (32) - previously this would have
  been 0 for every legacy file, always.
- **Python**: `export.glb.export()` crashed on any file with a textured
  material - the metadata JSON sidecar it writes tried to embed each
  material's raw texture image bytes directly, which was never
  JSON-serializable, so `json.dump` raised `TypeError`. There was no
  existing test coverage for this function, which is how this went
  uncaught. Now stripped before serialization - the `.glb` file itself
  carries the actual texture data, a JSON metadata file was never the
  right place for it.
- **Python, TypeScript, Dart, C++**: `Material.color`'s alpha channel was
  silently discarded when parsing legacy MFC (SketchUp 2013–2020) files —
  each language read the material's real 4-byte RGBA record but only kept
  the first three bytes, always reporting a hardcoded alpha regardless of
  what the file actually stored. .NET already read the real byte correctly;
  the other four now match it. Verified empirically across 2,060 materials
  in 13 real production files that this byte is always 255 in practice, but
  reading the real value is more correct than assuming a constant. The VFF
  (2021+) material record has no alpha attribute at all in any language —
  that path correctly continues to default to 255, since there's no real
  data to read there.

### Changed

- **Python**: `SkpModel.definitions` no longer contains an entry keyed by
  the string `"ROOT"`. The implicit top-level model (the file's directly
  placed, non-componentized geometry and instances) now has its own
  dedicated `SkpModel.root` field, matching TypeScript/.NET/Dart/C++'s
  `root`/`Root` exactly. Previously `definitions` was typed
  `Dict[int, Definition]` but silently held one non-`int` key at
  runtime — any code iterating `model.definitions.values()` to sum
  geometry across the whole file (not just named components) needs to
  also include `model.root` now, the same way the other four languages'
  own callers already do. `len(model.definitions)` is now the count of
  real named component/group definitions only, one lower than before.

### Removed

- **Python**: `SkpModel.scene_hierarchy` and `SkpModel.mesh_index` —
  dead fields that `parse()` never populated (always empty). Leftover
  from an earlier design; the real, populated versions of both concepts
  live on the separate `Scene` class returned by `build_scene()`, which
  is the pattern this project uses consistently everywhere else (a plain
  `parse()` stays light; a scene bake is opt-in and heavier). Any code
  reading these two fields was always seeing an empty list/dict — this
  removal cannot change observed behavior for a correctly-written
  caller, only surface a clear `AttributeError` instead of silently
  succeeding with fake-empty data for one that assumed they were real.

## [0.3.0] — C++ only — 2026-08-07

### Added

- **C++ package** — new independent C++17 port covering both modern
  VFF/ZIP (2021+) and legacy MFC (SketchUp 2013–2020) containers, at
  parity with the other four languages: geometry, components, layers,
  materials/textures, styles, dynamic properties, image entities,
  `parse()`/`build_scene()` scene baking, and observability hooks
  (progress/log callbacks). Installable CMake package with static and
  shared library builds, cross-platform CI (Linux/macOS/Windows,
  GCC/Clang/MSVC), and a test suite cross-validated against the same
  real fixture files already used by the Python/TypeScript/.NET/Dart
  ports. GLB export was not yet included in this initial release — see
  `[Unreleased]` above. Contributed by
  [Thomas Loockx](https://github.com/thomasloockx). Closes #29.

## [0.3.1] — TypeScript only — 2026-07-30

### Fixed

- **TypeScript**: `packages/typescript/README.md` — the published npm page
  was still showing the pre-implementation placeholder README ("Under
  active development... coming soon", planned-features list, no working
  examples), unchanged since before the TypeScript port was actually
  written. Rewritten to describe the real, working package: accurate
  `parse()`/`buildScene()`/`toGLB()`/`toJSON()` quick start, observability
  options, and the known large-file memory limitation — each snippet
  checked against the actual exported types. Python, .NET, and Dart's
  READMEs were audited at the same time and found already accurate; only
  TypeScript needed this fix, which is why this release doesn't bump the
  other three languages' versions.

## [0.3.0] — 2026-07-29

All additions below are backwards-compatible (new defaulted dataclass
fields only; no existing field or behaviour removed) unless noted under
"Changed".

### Added

- **.NET package** — built from scratch: full VFF (2021+) parsing at
  parity with the other three languages (geometry, components, layers,
  materials/textures, styles, dynamic properties, image entities), plus
  full legacy MFC (SketchUp 2013–2020) support. Not yet released to
  NuGet.
- **Dart package** — built from scratch: same full VFF + legacy MFC
  parity as .NET. Not yet released to pub.dev.
- **All four languages**: opt-in scene baking — `build_scene()` /
  `buildScene()` / `BuildScene()` — resolves the *entire* placed
  instance tree to world-space, triangulates every face, and groups
  results into GLB-ready mesh primitives (`Scene`/`GlbPrimitive`).
  Deliberately kept separate from `parse()`/`Open()` (which stays light —
  raw per-definition geometry, no scene-graph resolution) since baking a
  file that reuses a handful of definitions across many instances can
  produce far more data than the file's raw geometry. TypeScript already
  had this; ported to Python, .NET, and Dart this round, each re-parsing
  independently rather than sharing a prior `parse()` call's data. .NET
  and Dart's triangulation uses a faithful port of
  [earcut](https://github.com/mapbox/earcut) (the same algorithm
  TypeScript already used) rather than a from-scratch alternative.
- **All four languages**: **memory fix for large real-world files.**
  Files with 100,000+ component definitions previously required
  materializing the *entire* file's TLV tree in memory before extraction
  could begin; peak memory now scales with the size of the single
  largest top-level record instead of the whole file, via a lazy,
  streaming top-level iterator
  (`iter_top_level_lazy`/`iterTopLevelLazy`/`IterTopLevelLazy`) built on
  a cheap flat-header pre-scan. No change to any tag's decoding logic —
  purely an orchestration change. Verified against real production files
  up to 620 MB. .NET additionally needed `ChunkedBuffer` (a
  multi-segment buffer) plus widening TLV offsets from `int` to `long`,
  since the CLR's array/`MemoryStream` types have a hard ~2.1 GB ceiling
  that a decompressed `model.dat` can exceed. See
  [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#memory-architecture) for
  the full explanation, and
  [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md#performance) for
  verified per-language numbers — including TypeScript's remaining,
  currently-open memory ceiling on very large files, documented honestly
  rather than glossed over.
- **All four languages**: **observability** — opt-in progress reporting
  and structured, location-carrying parse errors, silent by default.
  Python uses the standard `logging` module
  (`logging.getLogger("openskp")`); TypeScript/.NET/Dart use an explicit
  options object with `onProgress`/`onLog` callbacks
  (`IProgress<T>`-based in .NET). A new `SkpParseError`/`SkpParseException`
  in every language carries `stage`/`recordIndex`/`totalRecords`/`tag`/
  `definitionId`, with the original failure always preserved (`__cause__`
  / `.cause` / `InnerException`). Full reference:
  [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md).
- **TypeScript**: `model.root` — the implicit top-level definition
  (geometry/instances placed directly in the model, not inside any
  component/group) is now exposed on `parse()`'s result, matching .NET
  and Dart's `Root`/`root`. Previously dropped entirely from `parseSkp()`
  — the only way to reach it was the much heavier `buildScene()` call.
  Purely additive; `model.definitions` is unchanged.
- **Documentation**: [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md)
  (new) and [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md) (new) —
  detailed, cross-language, verified against actual source and real
  files rather than aspirational. `docs/ARCHITECTURE.md` and
  `docs/API_DESIGN.md` rewritten to match current reality (all four
  languages available, not "planned"). README rewritten: accurate
  per-language quick starts (the previous Python example referenced
  methods — `model.export_glb()`, `openskp.binary.VffReader` — that
  don't exist in the current package).

### Fixed

- **`examples/web-viewer`**: the web viewer called `parseSkp()` and read
  triangulated mesh data (`_glbPrimitives`/`meshIndex`/`_gltfMaterials`)
  directly off the result — the shape `parseSkp()` returned before the
  scene-baking split above. Every model would parse "successfully" but
  silently render zero meshes. Fixed to call the new `buildScene()`
  alongside `parseSkp()`.
- **Python**: `openskp.export.obj.export()` and
  `openskp.export.json_export`'s `scene_hierarchy` output both silently
  depended on `SkpModel.mesh_index`/`SkpModel.scene_hierarchy` — dataclass
  fields `parse()` never populates, always empty. `obj.export()` always
  wrote a near-empty `.obj` file; `json_export`'s `scene_hierarchy` key
  always serialized as `[]`, even though the rest of the JSON output was
  correct. Fixed: `obj.export()` now takes a built `Scene` (from
  `build_scene()`) and writes real geometry from its `glb_primitives`;
  `json_export.to_dict()`/`.export()` now accept an optional `scene=`
  parameter and use `Scene.scene_hierarchy`'s real, resolved instance tree
  when provided (`scene_hierarchy` is `None`, not a misleading `[]`, when
  omitted). This is a breaking signature change for `obj.export()`
  (previously `export(model, output_path)`, now
  `export(scene, output_path)`).

### Known limitations (not yet fixed)

- **Python**: `model.definitions` mixes real (integer-keyed) definitions
  with an implicit root entry under a `'ROOT'` **string key**, unlike
  TypeScript/.NET/Dart's separate `.root`/`.Root` property. Tracked as a
  follow-up; not changed yet since existing consumers may rely on the
  current shape.
- **TypeScript**: `parseSkp()`'s memory use scales significantly worse
  than the other three languages on very large files — see "memory fix"
  above. A 113 MB file needs 8–16 GB of Node heap; a 294 MB file fails
  even at 16 GB. Root-caused to V8's per-object overhead on millions of
  small geometry objects; a more compact internal representation is
  tracked as follow-up work.
- **GLB/OBJ/JSON export**: Python ships complete disk-writing exporters
  (`openskp.export.glb`/`obj`/`json_export`); TypeScript ships a complete
  in-memory GLB serializer (`toGLB()`) but no OBJ/JSON export yet; .NET
  and Dart expose the same triangulated scene data via `buildScene()` but
  a consumer needs to serialize it themselves.

- **Python**: `Material.id` and `SkpModel.materials_by_id` — expose the TLV
  material IDs that `Face.material_id` references, so callers can resolve a
  face's material (colour/transparency) from the public API. Previously the
  join existed only inside the internal exporter.
- **Python**: `Instance.material_id` — the material painted onto a component
  instance itself (SketchUp's "paint the component", the same `D007`/`D107`
  structure faces use). Faces with no material of their own inherit it;
  consumers can now resolve that inheritance like the official SDK does.
- **Python**: texture extraction — `Material.texture` (`Texture` dataclass:
  `filename`, tile `width`/`height` in inches, raw image `data` bytes,
  `save()` helper). Images are read from the material's folder inside the
  embedded ZIP, with a sibling fallback when the stored image name differs
  from `textureFilename`.
- **Python**: colourized materials — `Material.colorized` /
  `colorize_type`, and shared-texture resolution so a colourized copy
  (SketchUp's `[Name]1`, `type="2"`) resolves the image bytes it borrows
  from its source material's folder instead of returning `None`.
- **Python**: per-face texture mapping — `Face.uv_transform` /
  `uv_transform_back` (the 3×3 matrix a positioned / photo-fitted texture
  stores per face; SketchUp's texture pins). Includes the decoded recipe to
  turn it into UVs (plane basis from the normal, then
  `[x, y, 1] @ inv(M) / tile`), calibrated against SDK-exported ground
  truth to < 0.001 UV error, including projective (4-pin distorted)
  mappings.
- **Python**: `Face.back_material_id` — the material of a face's BACK side
  (the `AF0D` child of the face node). A face painted only on its back is
  common when the author paints the visible side of a downward-facing cap;
  without this field such faces looked unpainted.
- **Python**: `Edge.soft` / `smooth` / `hidden` — per-edge display flags
  decoded from the edge's `D307` byte, so viewers/exporters can hide facet
  lines of curved surfaces while keeping author-drawn coplanar edges.
- **Python**: styles — `SkpModel.styles` (`Style`: name, `front_color`,
  `back_color` RGB) parsed from `styles/*/style.xml` (signed-int32 ARGB
  items 4000/4001). Viewers need them to shade unpainted faces the way
  SketchUp does.
- **Python**: `Definition.always_faces_camera` — SketchUp's "always face
  camera" component behavior (2D people / tree cut-outs), decoded from the
  definition's behavior block (`581B` → sub-TLV `5D1B == 1`; its companion
  `5E1B` is "shadows face sun"). Consumers can now render such instances
  as billboards, like SketchUp does.
- **Python**: Image entities — a picture placed in the model as an object
  now parses: its placement wraps a standard instance node inside the
  image-specific `9013`/`401F` containers (previously opaque, so the image
  definition looked "never placed"), and `Definition.is_image` flags the
  single-quad definition backing it (TLV kind `8315 == 2`). Real-world
  case: photo cut-out statues/animals placed as images imported with no
  geometry at all.

### Fixed

- **Python**: entity names (materials, layers, definitions, instances,
  dynamic properties) now decode as **UTF-8** instead of ASCII-with-ignore.
  Dropping the non-ASCII bytes silently corrupted any accented name
  ("cópia" → "cpia", "Diseño" → "Diseo") and — critically — broke the
  material-name join between the TLV stream and the XML material files,
  leaving those materials unresolvable from geometry.

### Changed

- **Python** — ⚠️ **`Material.transparency` value change.** The `trans`
  attribute in `material.xml` is a *transparency* (0 = opaque, 1 = fully
  transparent), not an opacity, and only applies when `useTrans="1"`. The
  parser now exposes the resulting **opacity** as `1 - trans` (and `1.0`
  when `useTrans` is off). This corrects two prior behaviours — most
  materials previously read as 50% transparent (the parser default) and
  some as fully invisible (`trans="0"`) — but it also means
  `Material.transparency` returns **different numeric values for the same
  file** after this release: most materials move `0.5 → 1.0`, and genuinely
  translucent ones invert (e.g. SketchUp's "Translucent Glass Blue", 70%
  opacity, now reads `0.7` instead of `0.3`). **Audit any code that reads
  `Material.transparency` directly before upgrading.** Validated against
  SketchUp's own library materials.

## [0.2.0] — 2026-06-18

### Added

- SketchUp 2025 support
- Materials rendering support
- Older SKP version fixes

### Changed

- Package version bumps

## [0.1.0] — 2026-06-18

### Added

- **Python package** (`openskp`) — first public release
  - Parse SketchUp 2021+ (VFF format) binary files
  - Extract 3D geometry: vertices, edges, faces with full topology
  - Extract component definitions and instance hierarchy
  - Extract layers/tags with RGB colors
  - Extract materials with color and transparency
  - Extract dynamic component properties (key-value pairs)
  - Export to GLB (binary glTF 2.0) via `trimesh`
  - Export to Wavefront OBJ (text format)
  - Export full metadata to JSON
  - CLI entry point: `openskp model.skp`
- **TypeScript package** — type definitions and stubs (implementation coming)
- **Dart package** — placeholder (planned for future release)
- **Documentation**
  - Reverse-engineered binary format specification (`docs/BINARY_FORMAT.md`)
  - Architecture overview (`docs/ARCHITECTURE.md`)
  - Cross-platform API design (`docs/API_DESIGN.md`)
- **CI/CD**
  - GitHub Actions for Python (test matrix: 3.9–3.12 × Linux/Windows/macOS)
  - GitHub Actions for TypeScript
  - PyPI release workflow

[0.3.1]: https://github.com/iamahsanmehmood/openskp/compare/typescript-v0.3.0...typescript-v0.3.1
[0.3.0]: https://github.com/iamahsanmehmood/openskp/compare/python-v0.2.0...python-v0.3.0
[0.2.0]: https://github.com/iamahsanmehmood/openskp/compare/python-v0.1.0...python-v0.2.0
[0.1.0]: https://github.com/iamahsanmehmood/openskp/releases/tag/python-v0.1.0
