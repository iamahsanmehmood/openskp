# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
