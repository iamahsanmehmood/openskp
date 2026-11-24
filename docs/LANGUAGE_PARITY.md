# Language Parity & Version Support

OpenSKP ships as five independent, native ports — Python, TypeScript, .NET,
Dart, and C++ — not bindings around a shared core. Each one reverse-engineers
and implements the format on its own, cross-validated against the others on
real files. Features don't always land in every language at the same time:
most new capability is researched and ground-truthed in Python first, then
ported once it's proven. This page is the single, current, honest picture of
where that stands — package versions, the full feature matrix, what's
unreleased, and which real SketchUp file versions actually parse today — kept
up to date as ports land, rather than re-derived from scratch each time it's
asked for.

For what's tracked as still outstanding, see
[issue #285](https://github.com/iamahsanmehmood/openskp/issues/285) (the
cross-language porting backlog) and [`ROADMAP.md`](../ROADMAP.md).

## 1. Package versions

As of the 2026-11-22 synchronized release, all 5 languages are on
**1.6.0** — but same version number does not mean identical capability.
Python carries a few things the other four do not (writing scenes,
per-edge and per-face layer ids, loose-edge/curve scene support, IFC curve
annotations, and since 1.6.0 painted-group materials in scenes, definition
and model attribute dictionaries, and textures kept in their own format) -
see [§2](#2-feature-matrix) and [§3](#3-whats-still-outstanding-after-160)
for exactly what differs. A sixth package, Rust (`packages/rust`), carries the
same version number but is not published yet, so it is not in the table
below. This is
a deliberate choice, not an oversight: that work is real, tested and ready,
and holding it back purely to keep every language byte-for-byte equal would
waste it for no real benefit. See
[CHANGELOG.md § 1.6.0](../CHANGELOG.md) for the full picture (and § 1.5.0 for
the release before it).

| Language | Registry | Released version | On `main`, not released yet |
|:---|:---|:---:|:---|
| 🐍 Python | [PyPI](https://pypi.org/project/openskp/) | [![PyPI](https://img.shields.io/pypi/v/openskp.svg?label=)](https://pypi.org/project/openskp/) | `face_front_color`/`face_back_color` ([#550](https://github.com/iamahsanmehmood/openskp/pull/550)); `py.typed` ([#580](https://github.com/iamahsanmehmood/openskp/pull/580)); `unread_root_entities` + warning ([#575](https://github.com/iamahsanmehmood/openskp/pull/575)); VFF `Layer_*` material exclusion ([#581](https://github.com/iamahsanmehmood/openskp/pull/581)) |
| 📘 TypeScript | [npm](https://www.npmjs.com/package/openskp) | [![npm](https://img.shields.io/npm/v/openskp.svg?label=)](https://www.npmjs.com/package/openskp) | `CCustomLineStyle` ([#579](https://github.com/iamahsanmehmood/openskp/pull/579)); `CLayer` parent-ref guard ([#577](https://github.com/iamahsanmehmood/openskp/pull/577)); `unreadRootEntities` + warning ([#575](https://github.com/iamahsanmehmood/openskp/pull/575)); VFF `Layer_*` material exclusion ([#581](https://github.com/iamahsanmehmood/openskp/pull/581)) |
| 🚀 .NET | [NuGet](https://www.nuget.org/packages/OpenSkp) | [![NuGet](https://img.shields.io/nuget/v/OpenSkp.svg?label=)](https://www.nuget.org/packages/OpenSkp) | `CCustomLineStyle` ([#579](https://github.com/iamahsanmehmood/openskp/pull/579)); `CLayer` parent-ref guard ([#577](https://github.com/iamahsanmehmood/openskp/pull/577)); `UnreadRootEntities` + warning ([#575](https://github.com/iamahsanmehmood/openskp/pull/575)); VFF `Layer_*` material exclusion ([#581](https://github.com/iamahsanmehmood/openskp/pull/581)) |
| 🎯 Dart | [pub.dev](https://pub.dev/packages/openskp) | [![Pub](https://img.shields.io/pub/v/openskp.svg?label=)](https://pub.dev/packages/openskp) | `CCustomLineStyle` ([#579](https://github.com/iamahsanmehmood/openskp/pull/579)); `CLayer` parent-ref guard ([#577](https://github.com/iamahsanmehmood/openskp/pull/577)); `unreadRootEntities` + warning ([#575](https://github.com/iamahsanmehmood/openskp/pull/575)); VFF `Layer_*` material exclusion ([#581](https://github.com/iamahsanmehmood/openskp/pull/581)) |
| ⚙️ C++ | [GitHub Releases](https://github.com/iamahsanmehmood/openskp/releases?q=cpp-) | [![C++](https://img.shields.io/github/v/release/iamahsanmehmood/openskp?filter=cpp-v*&label=)](https://github.com/iamahsanmehmood/openskp/releases?q=cpp-) | `CCustomLineStyle` ([#579](https://github.com/iamahsanmehmood/openskp/pull/579)); `CLayer` parent-ref guard ([#577](https://github.com/iamahsanmehmood/openskp/pull/577)); partial-read warning ([#575](https://github.com/iamahsanmehmood/openskp/pull/575)); VFF `Layer_*` material exclusion ([#581](https://github.com/iamahsanmehmood/openskp/pull/581)) |

Each language releases independently by pushing its own tag — see
[CONTRIBUTING.md § Releasing](../CONTRIBUTING.md#releasing-maintainers) for
the exact tag format per language (Dart is the one to double check: it's
the bare `vX.Y.Z`, not `dart-vX.Y.Z` — the latter builds fine but fails at
the pub.dev upload step).

## 2. Feature matrix

Every feature OpenSKP has, across all 5 languages. ✅ = shipped and
released. 🔶 = implemented on `main`, not published to a package registry
(or partial — see cell text). ❌ = not yet ported to that language; see
[§3](#3-whats-still-outstanding-after-160) for what's tracked there.

| Feature | Python | TypeScript | .NET | Dart | C++ |
|:---|:---:|:---:|:---:|:---:|:---:|
| **Reading** | | | | | |
| Parse VFF (2021+) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Parse classic MFC (SketchUp 4–2020; [§4](#4-real-sketchup-file-version-support)) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Geometry (vertices/edges/faces/UVs) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Component hierarchy / nested definitions | ✅ | ✅ | ✅ | ✅ | ✅ |
| Layers/tags (color + visibility) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Materials & textures | ✅ | ✅ | ✅ | ✅ | ✅ |
| Styles (`styles[].front_color`/`back_color` on 2021+ files) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Model rendering-options face colours (`face_front_color`/`face_back_color`, SketchUp 4–2025, [#550](https://github.com/iamahsanmehmood/openskp/pull/550)) | 🔶 on `main` | ❌ | ❌ | ❌ | ❌ |
| Scenes show unpainted faces in the model's front/back face colours (`unpainted_faces`, [#533](https://github.com/iamahsanmehmood/openskp/issues/533)) | 🔶 on `main` | ❌ | ❌ | ❌ | ❌ |
| Dynamic Component / entity attributes (instance & face dictionaries) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Definition- & model-level attribute dictionaries (Dynamic Components formulas/options, VFF + classic) | ✅ | ❌ | ❌ | ❌ | ❌ |
| Single attribute dictionary per entity | ✅ | ✅ | ✅ | ✅ | ✅ |
| Attribute dicts: full 9-value-type support, **legacy (pre-2021) format** (`Point3d`/`Vector3d`/`Length`/`Timestamp`/nested lists) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Attribute dicts: typed value decoding, **VFF (2021+) format** | ✅ 9/9 | ✅ 9/9 | ✅ 9/9 | ✅ 9/9 | ✅ 9/9 |
| Attribute dicts: multiple dictionaries per entity | ✅ | ✅ | ✅ | ✅ | ✅ |
| Attribute dicts surfaced in GLB/JSON metadata export (not just IFC Psets) | ✅ | n/a, not independently re-checked this cycle | n/a, not independently re-checked this cycle | n/a, not independently re-checked this cycle | ✅ |
| VFF pages/scenes + dimension parsing | ✅ | ✅ | ✅ | ✅ | ✅ |
| Legacy (pre-2021) pages/scenes reading | ✅ | ✅ | ✅ | ✅ | ✅ |
| Scene capture flags (`Page.capture_flags` / `use_*`, [#660](https://github.com/iamahsanmehmood/openskp/pull/660)) | 🔶 on `main` | 🔶 on `main` | 🔶 on `main` | 🔶 on `main` | 🔶 on `main` |
| Construction lines/points reading | ✅ legacy + VFF | ✅ legacy + VFF | ✅ legacy + VFF | ✅ legacy + VFF | ✅ legacy + VFF |
| VFF per-layer-hidden flag reading | ✅ | ✅ | ✅ | ✅ | ✅ |
| Per-edge/per-face layer, `Edge#curve` pointers, classic `CArcCurve` frames (legacy + VFF) | ✅ | ✅ | ✅ | ✅ | ✅ |
| `mesh_index[...].properties` populated | ✅ | ✅ | ✅ | ✅ | ✅ |
| `model.layers` in file order (not alphabetical) | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Scene building & observability** | | | | | |
| Scene baking / triangulation (`buildScene()`) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Instancing-preserving scene output (`build_instanced_scene()`) | ✅ | ✅ | ✅ | ✅ | ✅ |
| `build_instanced_scene()`: loose-edge curve support (structural framing/LGS) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Public typed model: `Edge.layer`/`Edge.curve_id`/`Face.layer`, `loose_edge_runs()` (raw ids, no baking - for a B-rep consumer building real wire edges) | ✅ | ✅ | ✅ | ✅ | ✅ |
| earcut-based triangulation (replaces Shapely + concave-face correctness fix) | ✅ | n/a, own triangulator | n/a | n/a | n/a |
| Large-file `F901 > 7017 > 7117` per-definition streaming ([#264](https://github.com/iamahsanmehmood/openskp/issues/264)) | ✅ ([#265](https://github.com/iamahsanmehmood/openskp/pull/265)) | ✅ | ❌ (`F401` only) | ❌ (`F401` only) | ✅ ([#300](https://github.com/iamahsanmehmood/openskp/pull/300)) |
| Opt-in progress reporting + structured errors | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Writing** | | | | | |
| Writer base (`create()`): materials, layers, definitions, groups, faces, holes, auto-triangulate | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer: VFF (2021+) core — template, integrity word, faces, solid materials, tags, definitions, instances, nested groups | 🔶 on `main` | ❌ | ❌ | ❌ | ❌ |
| Writer: circular/arc curves + polylines | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer: instance/group rotation + visibility | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer: Image entities (`add_image`) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer: applied texture width + material opacity | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer: material/layer handle validation | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer: linear dimensions + leader text | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer: section planes | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer: construction lines/points | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer: scenes (`add_scene`: camera + hidden layers) | ✅ | ❌ | ❌ | ❌ | ❌ |
| Editor (`open_existing()`) | ✅ | ✅ | ✅ | ✅ | ✅ |
| SKP-to-code generator (`to_*_code()`) | ✅ | ✅ | ✅ | ✅ | ✅ |
| codegen round-trips `applied_width`/opacity | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Export** | | | | | |
| GLB / OBJ+MTL / STL / PLY / DXF 3D / IFC4 / JSON | ✅ | ✅ | ✅ | ✅ | ✅ |
| IFC export: unit/axis fix, real names + layer visibility, plugin-attribute Psets, full-path classification | ✅ (also writes `IFCELEMENTASSEMBLY` and accepts a 3-arg classifier) | ✅ (2-arg classifier; no `IFCELEMENTASSEMBLY`) | ✅ (2-arg classifier; no `IFCELEMENTASSEMBLY`) | ✅ (2-arg classifier; no `IFCELEMENTASSEMBLY`) | ✅ (2-arg classifier; no `IFCELEMENTASSEMBLY`) |
| IFC export: loose-edge curve sets + analytic arcs as `IfcAnnotation` / `IfcIndexedPolyCurve`, IFC4 STEP conformance fixes | ✅ | ✅ | ✅ | ✅ | ✅ |
| Direct SketchUp → Fragments (`.frag`) export ([details](#fragments-frag-support)) | ✅ | ✅ | ✅ | 🔶 on `main`, not published | ✅ |
| **Import** | | | | | |
| Read a `.frag` file back (6th input format alongside `.skp`; [details](#fragments-frag-support)) | ✅ | ✅ | ✅ | 🔶 on `main`, not published | ✅ |

### Fragments (`.frag`) support

Both directions — exporting an `InstancedScene` to [ThatOpen's Fragments](https://github.com/ThatOpen/engine_fragment) (`.frag`) format and reading a `.frag` file's `SHELL` geometry back into an `InstancedScene` — are implemented across all five ports (plus the unpublished Rust port in `packages/rust`, `to_fragments` / `read_fragments`). Packaging, registry status, and test coverage differ by language:

| Language | Package & install | Export (`.skp` → `.frag`) | Read (`.frag` → `InstancedScene`) | Registry status | Verification scope |
|:---|:---|:---|:---|:---|:---|
| 🐍 Python | `pip install "openskp[fragments]"` (`openskp.export.fragments`) | `to_fragments()`, `export()` | `from_fragments()`, `read()` | ✅ On [PyPI](https://pypi.org/project/openskp/) (`1.6.0`) | Self round-trip + real `@thatopen/fragments` (`IfcImporter`) production `.frag` file (`CIRCLE_EXTRUSION` samples skipped with warning) |
| 📘 TypeScript | `npm install openskp` (included in `openskp`) | `toFragments()`, `skp.toFragments()` | `fromFragments()` | ✅ On [npm](https://www.npmjs.com/package/openskp) (`1.6.0`) | Self round-trip + real `@thatopen/fragments` (`SingleThreadedFragmentsModel`) load check on export; not tested on external `IfcImporter` `.frag` input |
| 🚀 .NET | `dotnet add package OpenSkp.Fragments` (`OpenSkp.Fragments`) | `FragmentsExport.ToFragments()`, `ExportFragments()` | `FragmentsExport.FromFragments()`, `ReadFragments()` | ✅ On [NuGet](https://www.nuget.org/packages/OpenSkp.Fragments) (`1.6.0`) | Self round-trip (including multi-shell splits); not tested on external `IfcImporter` `.frag` input |
| 🎯 Dart | Git / path dependency on `packages/dart_fragments` (`openskp_fragments`) | `toFragments()`, `exportFragments()` | `fromFragments()`, `readFragments()` | 🔶 On `main`, **not published to pub.dev** (`publish_to: none`) | Self round-trip (including multi-shell splits); not tested on external `IfcImporter` `.frag` input |
| ⚙️ C++ | Included in `OpenSkp::OpenSkp` (`<openskp/fragments_export.hpp>`) | `openskp::to_fragments()`, `export_fragments()` | `openskp::from_fragments()`, `read_fragments()` | ✅ [GitHub release](https://github.com/iamahsanmehmood/openskp/releases?q=cpp-) (`cpp-v1.6.0`) | Self round-trip (including multi-shell splits); not tested on external `IfcImporter` `.frag` input |

### Writer validation

Not every language's `.skp` writer (`create()` / `open_existing()`) is checked directly against SketchUp's C library — here is what each writer is actually verified against and what CI enforces on every writer change ([`.github/workflows/ci-writer-parity.yml`](../.github/workflows/ci-writer-parity.yml)):

| Language | What the writer is checked against | Automated cross-writer CI check | Measured byte differences |
|:---|:---|:---|:---|
| 🐍 **Python** | SketchUp's C library (`SketchUpAPI`, including `--resave` to modern VFF) during feature development + self round-trip test suite | Baseline for the Python-scaffold group in [`ci-writer-parity.yml`](../.github/workflows/ci-writer-parity.yml) | Preserves `blank_v17.skp`'s scaffold GUID at `0x34..0x43` |
| 📘 **TypeScript** | Python's writer output, byte-for-byte + self round-trip test suite | Asserted byte-identical to Python (ignoring per-definition/instance random UUIDs and `u32` definition Unix timestamps) in [`ci-writer-parity.yml`](../.github/workflows/ci-writer-parity.yml) | **0** non-GUID/timestamp bytes vs Python |
| 🚀 **.NET** | Python's writer output, byte-for-byte + self round-trip test suite | Asserted byte-identical to Python (ignoring random UUIDs and `u32` definition timestamps) in [`ci-writer-parity.yml`](../.github/workflows/ci-writer-parity.yml) | **0** non-GUID/timestamp bytes vs Python |
| 🎯 **Dart** | Python's writer output, byte-for-byte + self round-trip test suite | Asserted byte-identical to Python (ignoring random UUIDs and `u32` definition timestamps) in [`ci-writer-parity.yml`](../.github/workflows/ci-writer-parity.yml) | **0** non-GUID/timestamp bytes vs Python |
| ⚙️ **C++** | SketchUp's C library via [`packages/cpp/tools/check_native_skp.py`](../packages/cpp/tools/check_native_skp.py) (including `--resave` to modern VFF) + native binary invariants + self round-trip test suite | Baseline for the C++ group + diffed against Python in [`ci-writer-parity.yml`](../.github/workflows/ci-writer-parity.yml) | Randomizes the 16-byte file GUID at `0x34..0x43` (**0** other non-GUID/timestamp byte differences vs Python, TypeScript, .NET, and Dart) |
| 🦀 **Rust** *(unpublished)* | C++'s writer output, byte-for-byte (`tests/create_reference_test.rs`) + self round-trip test suite | Asserted byte-identical to C++ (ignoring random UUIDs and `u32` definition timestamps) in [`ci-writer-parity.yml`](../.github/workflows/ci-writer-parity.yml) and `ci-rust.yml` | **0** non-GUID/timestamp bytes vs C++ |

## 3. What's still outstanding after 1.6.0

For changes already merged to `main` since the 2026-11-22 **1.6.0** release, see
the **On `main`, not released yet** column in [§1](#1-package-versions) and the
`[Unreleased]` section of [CHANGELOG.md](../CHANGELOG.md). What remains open
across languages is cross-language porting work:

| Item | Python has it via | Needs porting to |
|:---|:---|:---|
| Writer: scenes (`add_scene`: camera + hidden layers) | `create.py`, `edit.py`, `codegen.py` | TypeScript, .NET, Dart, C++ |
| Material names on scene glTF materials (`1.6.0`; painted-group material inheritance ([#596](https://github.com/iamahsanmehmood/openskp/issues/596), [#634](https://github.com/iamahsanmehmood/openskp/issues/634)), skipped-texture warnings and `original_textures` ([#635](https://github.com/iamahsanmehmood/openskp/issues/635)), and colorized texture flags ([#657](https://github.com/iamahsanmehmood/openskp/issues/657)) are ported across all six ports) | `scene.py`, `instanced_scene.py` | TypeScript, .NET, Dart, C++, Rust — `gltf_materials` entries omit `name` |
| Definition- and model-level attribute dictionaries (Dynamic Components formulas/options), in VFF and classic files (1.6.0) | `_core.py`, `legacy.py`, `model.py` | TypeScript, .NET, Dart, C++ — not ported |
| Model rendering-options face colours (`SkpModel.face_front_color` / `face_back_color`, SketchUp 4–2025, [#550](https://github.com/iamahsanmehmood/openskp/pull/550), part of [#533](https://github.com/iamahsanmehmood/openskp/issues/533)) | `_core.py`, `legacy.py`, `model.py` (on `main`) | TypeScript, .NET, Dart, C++ |
| Scenes use those colours for unpainted faces (`unpainted_faces="style"`/`"tag"`, `GlbPrimitive`/`LocalPrimitive.unpainted_back`, `Scene.surface_primitives`; [#533](https://github.com/iamahsanmehmood/openskp/issues/533)) | `_face_groups.py`, `scene.py`, `instanced_scene.py`, `export/` (on `main`) | TypeScript, .NET, Dart, C++, Rust |
| Large-file `F901 > 7017 > 7117` per-definition streaming ([#264](https://github.com/iamahsanmehmood/openskp/issues/264)) | `_core.py` ([#265](https://github.com/iamahsanmehmood/openskp/pull/265)); also in TypeScript and C++ ([#300](https://github.com/iamahsanmehmood/openskp/pull/300)) | .NET (`Tlv.cs`) and Dart (`tlv.dart`) — currently unwrap `F401` only |

Tracked in [issue #285](https://github.com/iamahsanmehmood/openskp/issues/285) —
see the note at the top of [§1](#1-package-versions) for how cross-porting is
sequenced across releases.

## 4. Real SketchUp file version support

**Reading:** OpenSKP reads both the modern **VFF** container (SketchUp 2021+)
and the classic **MFC `CArchive`** container (SketchUp 4–2020; SketchUp
numbering jumps from `8` directly to `2013` / header `13`, with no `9`–`12`
releases). SketchUp 3 parses on synthetic SDK-downgraded fixtures only and
fails on the real V3 project in every reader. Every classic version SketchUp's
own C library saves (SketchUp 4 through 2020, plus 2021+) is read by Python
with matching rendering-options face colours ([#550](https://github.com/iamahsanmehmood/openskp/pull/550)).

**Writing (`create()` and `open_existing()`):** Every port's writer splices
entities into the same bundled **SketchUp 2017 (`{17.0.1}`)** classic MFC
`CArchive` blank-document scaffold (`blank_v17.skp`). Writers never emit VFF
(2021+) or any classic version other than SketchUp 2017; SketchUp 2017 and
every newer SketchUp release open that output natively. `open_existing()`
accepts any classic MFC (pre-2021) file `parse()` reads, replays it through
`create()`, and writes a new SketchUp 2017 (`{17.0.1}`) file (rejecting VFF
2021+ inputs). The unpublished Rust port (`packages/rust`) carries the same
classic version gates and the same `blank_v17.skp` scaffold.

### Reader support by SketchUp version

| SketchUp version (header) | Container | Python | TS / .NET / Dart / C++ | Synthetic & SDK-written files | Real files & open gaps |
|:---|:---|:---:|:---:|:---|:---|
| **3** (`{3.0.*}`) | Classic MFC | ⚠️ | ⚠️ | **All 5 (tests):** `legacy_v3_synthetic.skp`, `legacy_v3_textured.skp` (saved via SketchUp's C library; [#407](https://github.com/iamahsanmehmood/openskp/pull/407), [#411](https://github.com/iamahsanmehmood/openskp/pull/411)). **Python (tests):** `face_front_color`/`face_back_color` return `None` ([#550](https://github.com/iamahsanmehmood/openskp/pull/550)). Single-material V3 files and draw-block flags are unverified. | ❌ The real multi-version project's V3 save fails in **all 5** readers (`edge-use parent slot None != current loop`, [#284](https://github.com/iamahsanmehmood/openskp/issues/284)). |
| **4** (`{4.0.*}`) | Classic MFC | ✅ | ✅ | **All 5 (tests):** `legacy_v4_synthetic.skp`, `legacy_v4_textured.skp` (plus a 47-definition / 13-dimension SDK-written model checked across all 5 in [#440](https://github.com/iamahsanmehmood/openskp/pull/440), [#443](https://github.com/iamahsanmehmood/openskp/pull/443)). **Python (tests):** `face_colors_v4.skp` ([#550](https://github.com/iamahsanmehmood/openskp/pull/550); not in TS/.NET/Dart/C++ tests yet). (V4 stores no instance/group names.) | ✅ Real V4 cube sample and the real multi-version project's V4 save parse in all 5 readers with totals matching V6/2021/2025 ([#440](https://github.com/iamahsanmehmood/openskp/pull/440), [#465](https://github.com/iamahsanmehmood/openskp/pull/465)). |
| **5** (`{5.0.*}`) | Classic MFC | ✅ (SDK) | ⚠️ TS fails wild file; .NET / Dart / C++ ✅ (SDK) | **All 5 (tests):** `legacy_v5_synthetic.skp`, `legacy_v5_textured.skp` (plus the 47-definition SDK-written model in [#440](https://github.com/iamahsanmehmood/openskp/pull/440), [#443](https://github.com/iamahsanmehmood/openskp/pull/443)). **Python (tests):** `face_colors_v5.skp` ([#550](https://github.com/iamahsanmehmood/openskp/pull/550); not in TS/.NET/Dart/C++ tests yet). | ⚠️ The multi-version sweep's V5 save is a corrupted 0-byte file ([#440](https://github.com/iamahsanmehmood/openskp/pull/440)). A real wild V5 file (`{5.0.262}`, reported by boardfoot on [#284](https://github.com/iamahsanmehmood/openskp/issues/284)) fails in TypeScript (`back-ref to unwalked slot 20617` on an inline PNG) and has not been tested in the other four readers. |
| **6** (`{6.0.*}`) | Classic MFC | ✅ | ✅ | **All 5 (tests):** `legacy_v6_synthetic.skp`, `nesting_v6.skp`, `instance_scale_term_v6.skp` ([#385](https://github.com/iamahsanmehmood/openskp/pull/385), [#392](https://github.com/iamahsanmehmood/openskp/pull/392), [#411](https://github.com/iamahsanmehmood/openskp/pull/411)). **Python (tests):** `face_colors_v6.skp` ([#550](https://github.com/iamahsanmehmood/openskp/pull/550); not in TS/.NET/Dart/C++ tests yet). | ✅ Real multi-version project's V6 save parses in all 5 readers with identical totals ([#385](https://github.com/iamahsanmehmood/openskp/pull/385), [#465](https://github.com/iamahsanmehmood/openskp/pull/465)). |
| **7, 8, 2013** (`{7.0.*}`, `{8.0.*}`, `{13.0.*}`) | Classic MFC | ✅ | ✅ | **All 5 (tests):** `nesting_2013.skp`. **Python, TS, .NET, Dart (tests):** `legacy_v7_synthetic.skp` ([#310](https://github.com/iamahsanmehmood/openskp/pull/310), [#411](https://github.com/iamahsanmehmood/openskp/pull/411); C++ ported the same GUID gate in [#392](https://github.com/iamahsanmehmood/openskp/pull/392)). **Python:** SDK-written 7/8/2013 saves match SketchUp face colours ([#550](https://github.com/iamahsanmehmood/openskp/pull/550)). | ✅ Real multi-version project's V7, V8, and 2013 saves parse in all 5 readers with identical totals ([#310](https://github.com/iamahsanmehmood/openskp/pull/310), [#465](https://github.com/iamahsanmehmood/openskp/pull/465)). |
| **2014–2018** (`{14.0.*}`–`{18.*}`) | Classic MFC | ✅ | ✅ | **All 5 (tests):** `arcs_2017.skp`, `nesting_2017.skp`, `legacy_text_arrows_2017.skp`, `legacy_textured_colorize_2017.skp`, `single_material_v17.skp` ([#465](https://github.com/iamahsanmehmood/openskp/pull/465)). **Python, TS, .NET, Dart (tests):** `legacy_v2014_synthetic.skp` ([#310](https://github.com/iamahsanmehmood/openskp/pull/310), [#411](https://github.com/iamahsanmehmood/openskp/pull/411)). **Python (tests):** `legacy_attribute_dictionaries_{2017,2018}.skp`, `face_colors_{2016,2017,2018}.skp`, `texture_tiff_2017.skp` ([#550](https://github.com/iamahsanmehmood/openskp/pull/550); not in TS/.NET/Dart/C++ tests yet). *(Files with one or two tags and 11–13 component definitions — a file `openskp.create` can produce — trigger a `CLayer` parent back-ref count collision: fixed in Python in [#534](https://github.com/iamahsanmehmood/openskp/pull/534); TypeScript and the other 1.6.0 releases fail such files with `class-ref to non-class slot N (CLayer)`, fixed on `main` across TS, .NET, Dart, C++, and Rust in [#577](https://github.com/iamahsanmehmood/openskp/pull/577).)* | ✅ Real multi-version project's 2014–2018 saves plus committed app-saved fixtures `capilla_quiroz_v17.skp` (`{17.0.18899}`) and `zero_material_custom_layer_v18.skp` (`{18.0.16975}`) parse in all 5 (tests; [#465](https://github.com/iamahsanmehmood/openskp/pull/465)). |
| **2019–2020** (`{19.0.*}`, `{20.*}`) | Classic MFC | ✅ | ✅ | **All 5 (tests):** `legacy_v2019_synthetic.skp`, `legacy_v2020_textured.skp`, `legacy_empty_2020.skp`, `arcs_2020.skp`, `nesting_2020.skp`, `sdk_saved_{2019,2020}.skp`, `sdk_saved_sections_{2019,2020}.skp`, `sdk_saved_tag_line_styles_{2019,2020,materials_2020}.skp` ([#440](https://github.com/iamahsanmehmood/openskp/pull/440), [#443](https://github.com/iamahsanmehmood/openskp/pull/443), [#515](https://github.com/iamahsanmehmood/openskp/pull/515), [#579](https://github.com/iamahsanmehmood/openskp/pull/579)). **Python (tests):** `face_colors_{2019,2020}.skp` ([#550](https://github.com/iamahsanmehmood/openskp/pull/550); not in TS/.NET/Dart/C++ tests yet). *(Tags carrying a custom line style — `CCustomLineStyle`, [#514](https://github.com/iamahsanmehmood/openskp/issues/514) — landed in Python in [#515](https://github.com/iamahsanmehmood/openskp/pull/515); the 1.6.0 releases of TypeScript, .NET, Dart, and C++ fail such files with `no reader for class CCustomLineStyle`, fixed on `main` across TS, .NET, Dart, C++, and Rust in [#579](https://github.com/iamahsanmehmood/openskp/pull/579).)* | ✅ Real multi-version project's 2019 and 2020 saves plus committed app-saved fixture `gondola_v20.skp` (`{20.1.235}`) parse in all 5 (tests; [#440](https://github.com/iamahsanmehmood/openskp/pull/440), [#465](https://github.com/iamahsanmehmood/openskp/pull/465)). |
| **2021+** (`{21.*}`–`{26.*}`) | VFF (ZIP + TLV) | ✅ | ✅ | **All 5 (tests):** `arcs_2021.skp`, `nesting_2021.skp`, `scene_without_camera_2021.skp`, `instance_scale_term_2021.skp`. **Python (tests):** `face_colors_2021.skp` ([#550](https://github.com/iamahsanmehmood/openskp/pull/550)), `texture_{bmp,gif,tga,tiff}_2021.skp`, `definition_attribute_dictionaries_2026.skp` (`{26.2.243}`) (not in TS/.NET/Dart/C++ tests yet). | ✅ Real multi-version project's 2021 and 2025 saves plus committed app-saved fixtures `SU_File.skp` and `Untitled.skp` parse in all 5 (tests; [#465](https://github.com/iamahsanmehmood/openskp/pull/465)). |

### Note on the multi-version real-project sweep ([#284](https://github.com/iamahsanmehmood/openskp/issues/284))

The real-world sweep consists of 16 saves of one 5 MB, 146-definition textured
project across SketchUp 3 through 2025; its V5 save is a corrupted 0-byte file,
leaving **15 usable files**. **14 of those 15 usable saves** (V4, V6, V7, V8,
2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, and 2025) parse in all
five published readers with identical geometry and placement totals (32,982
vertices, 40,925 edges, 9,652 faces, and 150 instances; full scene-build and
export verification on that project was run in Python). Only **1 of the 15**
(**V3**) still fails across all five readers. If you hit an unparsed classic
file in the wild, please report it on
[issue #284](https://github.com/iamahsanmehmood/openskp/issues/284).

## Reporting a gap

If you hit a feature that behaves differently — or is missing — in one
language versus another, please
[open an issue](https://github.com/iamahsanmehmood/openskp/issues/new) with
the language, the feature, and (ideally) a real `.skp` file that reproduces
it. This page gets updated as gaps are found and closed.
