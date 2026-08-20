# Changelog

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
