# Changelog

The releases 1.3.0 to 1.6.0 and the details of every release are in the
[project changelog](https://github.com/iamahsanmehmood/openskp/blob/main/CHANGELOG.md).

## Unreleased

- **Tag line style name on `Layer`:** `Layer.lineStyle` (`String`, `""` when
  unset) now exposes each tag's SketchUp line style name across 2021+ VFF
  (`913C` / `0802`) and classic 2019–2020 (`CCustomLineStyle`) files, matching
  `SULayerGetLineStyle` + `SULineStyleGetName` (#285).

- **Per-instance `InstancedNode.hidden` flag:** `buildInstancedScene()` now sets
  `InstancedNode.hidden` (`bool?`) on every placed component instance and group
  from its own SketchUp *Hide* flag across classic (pre-2021) and 2021+ VFF
  files (`false` on `ROOT`, `null` when unset or on scenes read back with
  `fromFragments`), matching TypeScript and Rust (#285, #527).

- **Classic scene narrow-scan fallback:** when `readPageList` gives up on a
  classic (pre-2021) `CViewPage` list with an unknown layout or capture-flags
  word, `parseLegacy` now falls back to `scanPages` to recover scene `name` and
  `hiddenLayers` for `0x0c00` / `0x0c20` scene records, matching Python, C++,
  and Rust (#285).

- **Definition- and model-level attribute dictionaries:** classic (pre-2021)
  `CComponentDefinition` records now populate `Definition.attributeDictionaries`
  with typed values; `SkpModel.attributeDictionaries` (shared on
  `model.root.attributeDictionaries`) reads model-wide dictionaries from both
  2021+ VFF (`F601`) and classic pre-2021 headers (excluding internal
  `ModelProperties`); and `buildScene()` populates
  `InstanceNode.definitionAttributeDictionaries` stringified (#285).
- **Attribute dictionaries in JSON export:** `toJson(model, scene)` now
  writes `attribute_dictionaries` alongside `properties` on every
  `scene_hierarchy` node and `mesh_index` entry (#285).
- **Face-anchored text points:** classic (SketchUp 3–2020) `CText` records
  anchored on a `CFace` (`connKind == 7`) and 2021+ VFF `0x55F0` texts with
  `0x5209 == 7` now reconstruct `TextEntity.point` in the target face's
  outer-loop basis and lift it through any component or group instance path,
  matching `SUTextGetPoint` (#559).
- **Classic text anchors and labels:** classic (pre-2021) `CText` vertex
  anchors (`connKind == 2`) only resolve when `connectionPaths` carries no
  `extra` reference or nested instance paths, `_textDelimiter` never borrows
  bytes from before `r.pos`, and unanchored `ctype == 2` fallback scans never
  read into `labelPoint`, keeping `texts` identical across all six ports
  (#559).
- **Material names on scenes and GLB:** `buildScene()` and
  `buildInstancedScene()` keep each SketchUp material's `name` on
  `gltfMaterials` (leaving unpainted face materials unnamed) and keep
  differently named materials of the same colour separate; `toGlb` /
  `exportGlb` and `toInstancedGlb` / `exportInstancedGlb` write `name` into
  glTF `materials` (#285).
- **Colorized textures in GLB:** `toGlb` / `exportGlb` and `toInstancedGlb` /
  `exportInstancedGlb` with `textures: true` bake colorized PNG textures out of
  the box (shift or tint within 1/255 of SketchUp's own result, once per
  colour, using the existing `archive` dependency). Pass an optional
  `SkpImageCodec` (`imageCodec:`) to also bake colorized JPEG textures at
  quality 95; without it, colorized JPEGs keep their tinted factor with one
  warning. JPEG EXIF `APP1` orientation blocks are stripped without
  re-encoding, and textures above 178,956,970 pixels keep their source bytes
  and tinted factor with one warning (#285).
- **SketchUp 3 files:** curves and arcs, painted component and group instances,
  text fonts, infinite guide lines and zero-material headers in SketchUp 3
  (`{3.0.x}`) files are read properly (#284).
- **Color by Tag:** `SkpModel.colorByTag` reads SketchUp's active *Color by Tag*
  (`DisplayColorByLayer`) style setting in SketchUp 4–2020 and 2021+ files
  (#697).
- **Textures and writer parity:** `addFace` and `addCircle` solve `frontUv` /
  `backUv` in SketchUp's normal-derived basis and scale pins by
  `(appliedWidth, appliedHeight)`; `openExisting()` and `toDartCode()` keep
  `appliedHeight` instead of resetting it to `1.0` (#696). Faces with both UV
  pins and attributes keep their attribute dictionaries in SketchUp, and curves
  drawn over existing edges split cleanly without dropping the curve (#693).
- **Changed results:** `buildScene()` and `buildInstancedScene()` project
  default face UVs in SketchUp's basis (changing UVs on nearly horizontal and
  downward textured faces to match SketchUp and Python, #696), keep front and
  back sides separate whenever they do not resolve to the same material even if
  their RGB colours match (#697), keep differently named materials of the same
  colour separate on scenes, GLB, and OBJ/MTL exports (#285), and with
  `ParseOptions(unpaintedFaces: 'tag')`
  colour each unpainted face side by its own `Face.layer` tag before falling
  back to the placement's tag (#697).

## 1.7.0

- **Privacy:** `openExisting()` and the code made by `toDartCode()` no longer
  write the path of a temporary file, with your user name, into the saved
  file. `addTextureMaterial` and `addImage` take an optional `filename`.
- **Colours as in SketchUp:** unpainted faces take the model's front and back
  face colours (`ParseOptions(unpaintedFaces: 'tag')` for the old ones), GLB
  files hold linear colours and textures that are no longer darkened, and
  colorized textures are marked.
- **More files open:** SketchUp 2019 and 2020 files whose tags carry a line
  style; scenes of 2015 to 2020 files are no longer lost. Damaged files throw
  `SkpParseError`.
- `Edge.layer`, `Face.layer`, `Definition.looseEdgeRuns()`, curve sets and arcs
  in scenes, saved scenes (`pages`) with the entities they hide and the
  properties they save, guides inside components, the line of a dimension, tags
  and visibility of texts and dimensions, several attribute dictionaries per
  entity, document options in the writer.
- STL files of painted models can be printed; IFC writes curves, names with any
  character and the visibility of tags; DXF refuses malformed meshes.
- **Changed results:** the IFC default scale is millimetres (it was inches),
  `Definition.guid` and `Instance.guid` are in the order SketchUp shows, tag
  colours are no longer listed as materials, and scene `properties` of 2021+
  files hold the dynamic attributes only.
- Classes that **implement** `Edge`, `Face`, `Definition` or `GeometryHost` need
  the new members.

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
