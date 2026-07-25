# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
- **GLB export**: only TypeScript ships a complete binary `.glb`
  serializer (`toGLB()`) today. Python, .NET, and Dart all expose the
  same triangulated scene data via `buildScene()`, but a consumer needs
  to serialize it to `.glb` bytes themselves. OBJ and JSON export are not
  implemented in any language's current public API.

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

[0.2.0]: https://github.com/iamahsanmehmood/openskp/compare/python-v0.1.0...python-v0.2.0
[0.1.0]: https://github.com/iamahsanmehmood/openskp/releases/tag/python-v0.1.0
