# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

All additions below are backwards-compatible (new defaulted dataclass
fields only; no existing field or behaviour removed) unless noted under
"Changed".

### Added

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
