# Language Parity

OpenSKP ships as five independent, native ports — Python, TypeScript, .NET,
Dart, and C++ — not bindings around a shared core. Each one reverse-engineers
and implements the format on its own, cross-validated against the others on
real files. Features don't always land in every language at the same time:
most new capability is researched and ground-truthed in Python first, then
ported once it's proven. This page is the current, honest picture of where
that stands — kept up to date as ports land, rather than re-derived from
scratch each time it's asked for.

For what's tracked as still outstanding, see
[issue #285](https://github.com/iamahsanmehmood/openskp/issues/285) (the
cross-language porting backlog) and [`ROADMAP.md`](../ROADMAP.md).

## Core parity — all 5 languages

Everything below is at full parity across Python, TypeScript, .NET, Dart, and
C++:

- Parsing both the modern VFF container (2021+) and the classic MFC
  `CArchive` container (2013–2020) — geometry, materials, layers, components,
  styles, dynamic component attributes.
- Native export to GLB, JSON, OBJ/MTL, STL (ASCII + binary), PLY (ASCII +
  binary), DXF (3DFACE + AutoCAD Polyface Mesh), and IFC4.
- The writer (`create()`/`SkpBuilder`) and editor (`open_existing()`): faces
  (including holes and non-planar auto-triangulation), solid/textured
  materials, layers, nested component definitions and groups, circular/arc
  curves, freeform polylines, per-instance rotation/visibility, single
  attribute dictionaries, Image entities, applied texture width/height,
  material opacity.
- Instancing-preserving scene output (`build_instanced_scene()`/
  `to_instanced_glb()`).
- The SKP-to-code generator (`to_*_code()`).
- VFF scene/page and dimension *parsing* (`model.pages`, `model.dimensions`).
- Material/layer handle validation at the writer's call site.

## Where the languages currently differ

| Feature | Python | TypeScript | .NET | Dart | C++ |
|---|:---:|:---:|:---:|:---:|:---:|
| Attribute dicts: full 9-value-type support (`None`/`bool`/`Point3d`/`Vector3d`/`Length`/`Timestamp`/nested lists) | ✅ | ❌ str/int/float only | ❌ | ❌ | ❌ |
| Attribute dicts: multiple dictionaries per entity (`attribute_dicts=[...]`) | ✅ | ❌ single dict only | ❌ | ❌ | ❌ |
| Writer: linear dimensions (`add_dimension`) + leader text (`add_text`) | ✅ | ❌ | ❌ | ❌ | ❌ |
| Writer: section planes (`add_section_plane`) | ✅ | ❌ | ❌ | ❌ | ❌ |
| Writer + reader: construction lines/points | ✅ | ❌ | ❌ | ❌ | ❌ |
| Legacy (pre-2021) pages/scenes reading | ✅ | ❌ VFF only | ❌ VFF only | ❌ VFF only | ❌ VFF only |
| Direct SketchUp → Fragments (.frag) export | ✅ | 🔶 open [PR #276](https://github.com/iamahsanmehmood/openskp/pull/276), CI failing | ❌ not started | ❌ not started | ❌ not started |
| IFC export: unit/axis fix, real instance names + layer visibility, plugin-attribute Psets, full-path classification | ✅ (4 fixes) | n/a — not checked for equivalent issues | n/a | n/a | n/a |
| VFF per-layer-hidden flag reading (`8E3C` tag) | ✅ | ❌ not confirmed ported | ❌ | ❌ | ❌ |
| earcut-based triangulation (Shapely replacement + concave-face correctness fix) | ✅ | n/a — own triangulator, not independently checked | n/a | n/a | n/a |
| Large-file parser fix ([#264](https://github.com/iamahsanmehmood/openskp/issues/264): container-unwrap streaming + O(V²) triangulation stall) | ✅ | not independently checked | not independently checked | not independently checked | not independently checked |
| `mesh_index[...].properties` populated | ✅ | ✅ | ✅ | ✅ | ❌ known gap |
| `model.layers` in file order (vs. alphabetical) | ✅ | ✅ | ✅ | ✅ | ❌ known gap (map-keyed) |
| codegen round-trips `applied_width`/opacity on regenerated textured materials | ❌ | ❌ | ❌ | ❌ | ❌ — affects all 5 equally |

## Known real-file gaps (not a language-parity issue — affects the format itself)

A 14-file real-world version-compatibility sweep (SketchUp versions 3 through
2025, same project) found 8/14 files parse and convert cleanly through the
legacy MFC reader; 6/14 fail with distinct errors, most sharing one root
cause (a slot-collision bug, not yet fixed — tracked in
[issue #284](https://github.com/iamahsanmehmood/openskp/issues/284)). This
was only investigated in Python; whether the other 4 languages' own legacy
readers hit the same files the same way has not been checked.

## Reporting a gap

If you hit a feature that behaves differently — or is missing — in one
language versus another, please
[open an issue](https://github.com/iamahsanmehmood/openskp/issues/new) with
the language, the feature, and (ideally) a real `.skp` file that reproduces
it. This page gets updated as gaps are found and closed.
