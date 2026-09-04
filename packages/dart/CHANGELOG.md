# Changelog

## 1.2.0

- **Groups can now carry attributes.** `addGroup`/`addGroupInstance` gained
  `attributes`/`attributeDictName` parameters, matching what `addInstance`
  already offered — a group only gets a real attribute container when one
  is actually given, instead of always writing a null pointer.
- `addTextureMaterial` gained `applied_width` support (alongside the
  existing `applied_height`), and `addMaterial`/`addTextureMaterial`
  gained `opacity`.
- Added `addImage` — writes a genuine SketchUp Image entity (File →
  Import → Image), distinct from a textured face.
- Ported VFF (2021+) scene ("pages") and linear dimension parsing from
  Python — surfaced as `SkpModel.pages`/`SkpModel.dimensions`.
- Fixed `Scene.meshIndex` entries getting the same, wrong `name` cascaded
  down from the outermost ancestor instance instead of each mesh's own
  correctly-nested name.
- Fixed a TLV header-scan off-by-one that could silently drop a record
  whose 6-byte header exactly filled the remaining buffer space.
- Fixed legacy Dynamic Component attribute lookup returning empty even
  when a file genuinely had Dynamic Component data.
- Fixed `CoEdge.orientation` being inverted (raw storage bit passed
  through instead of the documented `+1`/`-1` contract).
- Fixed an empty component/group definition name being fabricated into a
  placeholder (`"Def123"`) on `openExisting()`/`toDartCode()` replay.
- Fixed GLB export silently merging differently-textured materials that
  happened to average to the same flat color, and embedded texture images
  into exported GLB files for the first time.
- Fixed a SketchUp-2020 filler-recovery heuristic that could misdetect a
  value that was a multiple of 256.
- Writer now validates every `material`/`backMaterial`/`layer` argument
  against handles the same builder actually issued, raising immediately
  on a mismatch instead of silently accepting a stray value.

## 1.1.0

- **Write support** — `create()` returns an `SkpBuilder` that assembles a
  legacy MFC `CArchive`-format `.skp` file: geometry (`addFace`,
  `addCircle`, `addArc`, `addPolyline`, including holes and
  non-coplanar auto-triangulation), solid-color and PNG/JPEG-textured
  materials, named layers, reusable component definitions with multiple
  positioned instances, groups, and custom key/value attribute
  dictionaries — then `save(path)`/`toBytes()` writes it out. No
  SketchUp SDK involved at any point.
- **Edit support** — `openExisting(path)` loads an existing legacy-format
  file, replays everything this package's own reader understood back
  through the writer API, and returns a builder more geometry can still
  be added to before saving.
- Ports the same feature set already shipped in the Python package this
  release, verified byte-identical to Python's own output on the same
  input and validated against the real SketchUp SDK.

## 1.0.0

- Added native Wavefront OBJ exporter (`toObj`/`exportObj`) plus a companion
  MTL material-library writer (`toMtl`), linked via `mtllib` and an
  `exportMtl` flag on `exportObj`.
- Added native STL exporter (`toStlAscii`/`toStlBinary`/`exportStl`), ASCII
  and little-endian binary, with an optional unit-scale multiplier.
- Added native PLY exporter (`toPlyAscii`/`toPlyBinary`/`exportPly`), ASCII
  and little-endian binary, carrying positions, normals, UVs, and RGBA
  vertex colors.
- Added native 3D DXF exporter (`toDxf`/`exportDxf`) targeting AutoCAD R2000
  (`AC1015`) compliance, in both `3dface` and AutoCAD Polyface Mesh
  (default) modes — verified opening cleanly in real desktop AutoCAD, not
  just lenient DXF readers.
- Added native IFC4 (BIM) exporter (`toIfc`/`exportIfc`) with full spatial
  hierarchy, tessellated geometry, element classification, dynamic
  properties, material colors, and layer preservation — validated with
  IfcOpenShell.

## 0.3.0

- Added `buildScene()` scene-baking API (`Scene`, `MeshIndex`, `GlbPrimitive`) for world-space, triangulated mesh outputs.
- Added binary glTF 2.0 (GLB) export functions (`toGlb`, `exportGlb`, `toGlbBytes`).
- Added canonical JSON export (`toJson`) matching cross-platform schema.
- Added `meta/meta.dat` units parsing (`model.units`).
- Added face UV transforms (`uvTransform`, `uvTransformBack`) and back-face material handling.
- Added layer, face, and instance hidden visibility flags (`hidden`).
- Added ZIP decompression-bomb size cap and recursion/cycle guard in scene baking.
- Routed internal debug/warning logs through `ParseOptions.onLog` callback.

## 0.2.0

- SketchUp 2025 support.
- Materials rendering support.
- Older SKP version fixes.
- Version bump to 0.2.0.

## 0.1.1

- Add comprehensive documentation and package installation details.

## 0.1.0

- Initial release of the OpenSKP Dart package.
