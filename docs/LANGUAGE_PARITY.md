# Language Parity & Version Support

OpenSKP ships as five published native ports — Python, TypeScript, .NET, Dart,
and C++ — plus a sixth native Rust port (`packages/rust`) in the repository,
not bindings around a shared core. Each one reverse-engineers and implements
the format on its own, cross-validated against the others on real files.
Features don't always land in every language at the same time: most new
capability is researched and ground-truthed in Python or C++ first, then ported
once it's proven. This page is the single, current picture of where that
stands — package versions, the full feature matrix, what remains open across
ports, and which real SketchUp file versions parse today.

For what's tracked as still outstanding, see
[issue #285](https://github.com/iamahsanmehmood/openskp/issues/285) (the
cross-language porting backlog) and [`ROADMAP.md`](../ROADMAP.md).

## 1. Package versions

All 5 published languages ship **1.7.0**, and the sixth package, Rust
(`packages/rust`), carries **1.7.0** on `main` (depend on it by git or path; it
is not published to crates.io yet). Same version number does not mean
byte-for-byte identical capability — see [§2](#2-feature-matrix) and
[§3](#3-whats-still-outstanding-after-170) for the exact differences, and
[CHANGELOG.md](../CHANGELOG.md) for what shipped in each release.

| Language | Registry | Released version |
|:---|:---|:---:|
| 🐍 Python | [PyPI](https://pypi.org/project/openskp/) | [![PyPI](https://img.shields.io/pypi/v/openskp.svg?label=)](https://pypi.org/project/openskp/) |
| 📘 TypeScript | [npm](https://www.npmjs.com/package/openskp) | [![npm](https://img.shields.io/npm/v/openskp.svg?label=)](https://www.npmjs.com/package/openskp) |
| 🚀 .NET | [NuGet](https://www.nuget.org/packages/OpenSkp) | [![NuGet](https://img.shields.io/nuget/v/OpenSkp.svg?label=)](https://www.nuget.org/packages/OpenSkp) |
| 🎯 Dart | [pub.dev](https://pub.dev/packages/openskp) | [![Pub](https://img.shields.io/pub/v/openskp.svg?label=)](https://pub.dev/packages/openskp) |
| ⚙️ C++ | [GitHub Releases](https://github.com/iamahsanmehmood/openskp/releases?q=cpp-) | [![C++](https://img.shields.io/github/v/release/iamahsanmehmood/openskp?filter=cpp-v*&label=)](https://github.com/iamahsanmehmood/openskp/releases?q=cpp-) |
| 🦀 Rust | `packages/rust` (git / path; not on crates.io) | `1.7.0` *(on `main`)* |

Each published language releases independently by pushing its own tag — see
[CONTRIBUTING.md § Releasing](../CONTRIBUTING.md#releasing-maintainers) for
the exact tag format per language (Dart uses the bare `vX.Y.Z` tag rather than
`dart-vX.Y.Z`).

## 2. Feature matrix

Every feature OpenSKP has, across all 6 languages. ✅ = shipped in **1.7.0**
(for Rust, implemented on `main` in `packages/rust` — not on crates.io yet).
🔶 = partial, or in a separate unpublished package — see cell text. ❌ = not
yet in that language; see [§3](#3-whats-still-outstanding-after-170).

| Feature | Python | TypeScript | .NET | Dart | C++ | Rust *(git)* |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|
| **Reading** | | | | | | |
| Parse VFF (2021+), including wrapped 4 GiB `model.dat` recovery | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Parse classic MFC (SketchUp 4–2020; [§4](#4-real-sketchup-file-version-support)), 64-level recursion cap, `unread_root_entities` + warning | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Geometry (vertices/edges/faces/UVs) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Component hierarchy / nested definitions / canonical UUID GUIDs | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Component scale-grip suppression mask (`Definition.no_scale_mask`, VFF + classic) | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ |
| Layers/tags (color, visibility in classic + VFF `8E3C`, line style `line_style` / `lineStyle` / `LineStyle`, file order, VFF `Layer_*` exclusion) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Materials & textures (solid, textured, colorized flag/type, PNG/JPEG/BMP/GIF/TGA/TIFF) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Styles (`styles[].front_color`/`back_color` on 2021+ files) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Model rendering-options face colours (`face_front_color`/`face_back_color`, SketchUp 4–2025, [#533](https://github.com/iamahsanmehmood/openskp/issues/533)) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Model rendering-options "Color by Tag" setting (`color_by_tag` / `colorByTag` / `ColorByTag`, SketchUp 4–2025, [#697](https://github.com/iamahsanmehmood/openskp/issues/697)) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Scenes show unpainted faces in the model's front/back face colours (`unpainted_faces`, `surface_primitives`, [#533](https://github.com/iamahsanmehmood/openskp/issues/533)) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Dynamic Component / entity attributes (instance & face dictionaries; single & multiple dictionaries) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Definition- & model-level attribute dictionaries (Dynamic Components formulas/options, VFF + classic) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Attribute dicts: full 9-value-type support, **legacy (pre-2021) format** (`Point3d`/`Vector3d`/`Length`/`Timestamp`/nested lists) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Attribute dicts: typed value decoding, **VFF (2021+) format** (`AD38`/`AA38`/`AF38`/`A938`/`A738`/`AC38`/`B438`/`B538`/`AE38`) | ✅ 9/9 | ✅ 9/9 | ✅ 9/9 | ✅ 9/9 | ✅ 9/9 | ✅ 9/9 |
| Attribute dicts surfaced in GLB/JSON metadata export (not just IFC Psets) | ✅ (GLB sidecar + JSON) | ✅ (JSON) | ✅ (JSON) | ✅ (JSON) | ✅ (JSON) | ✅ (JSON) |
| VFF pages/scenes + dimension & text parsing (`offset_vector`, leaders/screen text, `layer`/`hidden`, VFF component ownership) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| VFF 2021+ section planes (plane, name, label, hidden) | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ |
| Legacy (pre-2021) pages/scenes reading (`CViewPage`, `hidden_entity_ids` + `persistent_id`) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Scene capture flags (`Page.capture_flags` / `use_*`, [#660](https://github.com/iamahsanmehmood/openskp/pull/660)) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Construction lines/points reading (`layer` + `hidden`, legacy + VFF) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Per-edge/per-face layer, `Edge#curve` pointers, `ArcCurve` frames (legacy + VFF) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `mesh_index[...].properties` & `attribute_dictionaries` populated (VFF + classic) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Scene building & observability** | | | | | | |
| Scene baking / triangulation (`build_scene()`, `Scene.pages`, `Scene.unread_root_entities`) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Instancing-preserving scene output (`build_instanced_scene()`, `InstancedNode.hidden`, `InstancedScene.pages`, `unread_root_entities`) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Scenes: painted-instance material inheritance, colorized texture flag, skipped-texture warning & `original_textures` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Scenes: SketchUp material `name` preserved on `gltf_materials` and GLB `materials` ([#285](https://github.com/iamahsanmehmood/openskp/issues/285)) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Scenes: `InstanceNode.definition_attribute_dictionaries` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Loose-edge curve support in scenes (`curve_sets`, `curve_resources`, `include_curve_sets`) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Public typed model: `Edge.layer`/`Edge.curve_id`/`Face.layer`, `loose_edge_runs()` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Earcut-based triangulation (concave-quad diagonal check + overlapping-hole fallback) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Large-file `F901 > 7017 > 7117` per-definition streaming ([#264](https://github.com/iamahsanmehmood/openskp/issues/264)) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Opt-in progress reporting, structured parse errors, no unsolicited stderr/console output | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Writing** | | | | | | |
| Writer base (`create()`): materials, layers, definitions (`guid`), groups, faces, holes, auto-triangulate, `set_document_options`, `attribute_dicts`, long strings | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer: circular/arc curves + polylines | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer: instance/group rotation + visibility | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer: Image entities (`add_image`) + optional stored texture `filename` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer: applied texture width + material opacity | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer: material/layer handle validation | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer: linear dimensions (`offset_vector`, `layer`, `hidden`) + leader/screen text | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer: section planes | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer: construction lines/points (root + component definitions, `layer` + `hidden`) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer for 2021+ files (`create_vff()`: faces, materials, UVs, tags, components, groups, curves, attributes, texts/dimensions, guides, section planes, scenes/camera) | 🔶 on `main`, not released yet | ❌ | ❌ | ❌ | ❌ | ❌ |
| Writer: scenes (`add_scene`: camera + hidden layers) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Editor (`open_existing()`) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| SKP-to-code generator (`to_*_code()`), round-tripping `applied_width`/opacity/`filename` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Export** | | | | | | |
| GLB (flat & instanced, linear `baseColorFactor`) / OBJ+MTL / STL / PLY / DXF 3D / IFC4 / JSON | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| GLB colorized texture baking (hue shift & tint, JPEG EXIF `APP1` strip; [#285](https://github.com/iamahsanmehmood/openskp/issues/285)) | ✅ *(Pillow)* | ✅ *(PNG built-in; JPEG via `imageCodec`)* | ✅ *(PNG built-in; JPEG via `ImageCodec`)* | ✅ *(PNG built-in; JPEG via `imageCodec`)* | ✅ *(PNG & JPEG built-in)* | ✅ *(PNG built-in; JPEG via `ImageCodec`)* |
| Command-line tool (`openskp info` / `openskp convert`, [#570](https://github.com/iamahsanmehmood/openskp/issues/570)) | ✅ *(on `main`)* | ❌ | ❌ | ❌ | ❌ | ❌ |
| IFC export: mm unit/axis fix, real names + layer visibility, plugin-attribute Psets, full-path classification | ✅ *(+ `IFCELEMENTASSEMBLY`, 3-arg classifier)* | ✅ *(2-arg classifier; no `IFCELEMENTASSEMBLY`)* | ✅ *(2-arg classifier; no `IFCELEMENTASSEMBLY`)* | ✅ *(2-arg classifier; no `IFCELEMENTASSEMBLY`)* | ✅ *(2-arg classifier; no `IFCELEMENTASSEMBLY`)* | ✅ *(2-arg classifier; no `IFCELEMENTASSEMBLY`)* |
| IFC export: loose-edge curve sets + analytic arcs as `IfcAnnotation` / `IfcIndexedPolyCurve`, IFC4 STEP conformance | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Direct SketchUp → Fragments (`.frag`) export ([details](#fragments-frag-support)) | ✅ | ✅ | ✅ | 🔶 on `main`, not on pub.dev | ✅ | ✅ |
| **Import** | | | | | | |
| Read a `.frag` file back (6th input format alongside `.skp`; [details](#fragments-frag-support)) | ✅ | ✅ | ✅ | 🔶 on `main`, not on pub.dev | ✅ | ✅ |

### Fragments (`.frag`) support

Both directions — exporting an `InstancedScene` to [ThatOpen's Fragments](https://github.com/ThatOpen/engine_fragment) (`.frag`) format and reading a `.frag` file's `SHELL` geometry back into an `InstancedScene` — are implemented across all six ports. Packaging, registry status, and test coverage differ by language:

| Language | Package & install | Export (`.skp` → `.frag`) | Read (`.frag` → `InstancedScene`) | Registry status | Verification scope |
|:---|:---|:---|:---|:---|:---|
| 🐍 Python | `pip install "openskp[fragments]"` (`openskp.export.fragments`) | `to_fragments()`, `export()` | `from_fragments()`, `read()` | ✅ On [PyPI](https://pypi.org/project/openskp/) (`1.7.0`) | Self round-trip + real `@thatopen/fragments` (`IfcImporter`) production `.frag` file (`CIRCLE_EXTRUSION` samples skipped with warning) |
| 📘 TypeScript | `npm install openskp` (included in `openskp`) | `toFragments()`, `skp.toFragments()` | `fromFragments()` | ✅ On [npm](https://www.npmjs.com/package/openskp) (`1.7.0`) | Self round-trip + real `@thatopen/fragments` (`SingleThreadedFragmentsModel`) load check on export; not tested on external `IfcImporter` `.frag` input |
| 🚀 .NET | `dotnet add package OpenSkp.Fragments` (`OpenSkp.Fragments`) | `FragmentsExport.ToFragments()`, `ExportFragments()` | `FragmentsExport.FromFragments()`, `ReadFragments()` | ✅ On [NuGet](https://www.nuget.org/packages/OpenSkp.Fragments) (`1.7.0`) | Self round-trip (including multi-shell splits); not tested on external `IfcImporter` `.frag` input |
| 🎯 Dart | Git / path dependency on `packages/dart_fragments` (`openskp_fragments`) | `toFragments()`, `exportFragments()` | `fromFragments()`, `readFragments()` | 🔶 On `main`, **not published to pub.dev** (`publish_to: none`) | Self round-trip (including multi-shell splits); not tested on external `IfcImporter` `.frag` input |
| ⚙️ C++ | Included in `OpenSkp::OpenSkp` (`<openskp/fragments_export.hpp>`) | `openskp::to_fragments()`, `export_fragments()` | `openskp::from_fragments()`, `read_fragments()` | ✅ [GitHub release](https://github.com/iamahsanmehmood/openskp/releases?q=cpp-) (`cpp-v1.7.0`) | Self round-trip (including multi-shell splits); not tested on external `IfcImporter` `.frag` input |
| 🦀 Rust | Git / path dependency on `packages/rust` (`openskp`) | `to_fragments()`, `export_fragments()` | `from_fragments()`, `read_fragments()` | 🔶 On `main` (`1.7.0`), **not on crates.io** | Self round-trip (including multi-shell splits); not tested on external `IfcImporter` `.frag` input |

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

## 3. What's still outstanding after 1.7.0

What remains open across languages after **1.7.0** is tracked in
[issue #285](https://github.com/iamahsanmehmood/openskp/issues/285):

| Item | Present in | Needs porting to |
|:---|:---|:---|
| Writer for 2021+ VFF files (`create_vff()`: faces, solid/textured materials, UVs, tags, components, groups, curves, attribute dictionaries, texts/dimensions, guides, section planes, scenes/camera) | Python (`create_vff.py`, on `main`) | TypeScript, .NET, Dart, C++, Rust *(plus VFF `add_image`, vertex-attached dimensions, and VFF `open_existing()` in Python)* |
| Writer: scenes (`add_scene`: camera + hidden layers) | Python (`create.py`) | TypeScript, .NET, Dart, C++, Rust |
| Command-line tool (`openskp info` / `openskp convert`, [#570](https://github.com/iamahsanmehmood/openskp/issues/570)) | Python (`cli.py`, on `main`) | TypeScript, .NET, Dart, C++, Rust |
| IFC `IFCELEMENTASSEMBLY` wrapper grouping and 3-arg custom `classifier(name, layer, path)` | Python (`export/ifc.py`) | TypeScript, .NET, Dart, C++, Rust — 2-arg custom `classifier` and no `IFCELEMENTASSEMBLY` |
| Component scale-grip suppression mask (`Definition.no_scale_mask`, read + written + replayed + codegen) | C++ (`model.hpp`, `create.hpp`), Rust (`model.rs`, `create/mod.rs`) | Python, TypeScript, .NET, Dart |
| Writer randomizes scaffold file GUID at `0x34..0x43` | C++ (`create.cpp`), Rust (`create/mod.rs`) | Python, TypeScript, .NET, Dart — currently preserve `blank_v17.skp`'s GUID |

## 4. Real SketchUp file version support

**Reading:** OpenSKP reads both the modern **VFF** container (SketchUp 2021+)
and the classic **MFC `CArchive`** container (SketchUp 4–2020; SketchUp
numbering jumps from `8` directly to `2013` / header `13`, with no `9`–`12`
releases). SketchUp 3 parses on synthetic SDK-written fixtures and, on `main` since
#705 (not released yet), on the real V3 project in every reader. Every classic version SketchUp's
own C library saves (SketchUp 4 through 2020, plus 2021+) is read across all
six ports with matching rendering-options face colours
([#533](https://github.com/iamahsanmehmood/openskp/issues/533),
[#550](https://github.com/iamahsanmehmood/openskp/pull/550)).

**Writing (`create()` and `open_existing()`):** Every port's writer splices
entities into the same bundled **SketchUp 2017 (`{17.0.1}`)** classic MFC
`CArchive` blank-document scaffold (`blank_v17.skp`). These writers never
emit VFF (2021+) or any classic version other than SketchUp 2017 (Python's
separate `create_vff()`, on `main`, writes 2021+ files); SketchUp 2017 and
every newer SketchUp release open that output natively. `open_existing()`
accepts any classic MFC (pre-2021) file `parse()` reads, replays it through
`create()`, and writes a new SketchUp 2017 (`{17.0.1}`) file (rejecting VFF
2021+ inputs).

### Reader support by SketchUp version

| SketchUp version (header) | Container | Python | TS / .NET / Dart / C++ / Rust | Synthetic & SDK-written files | Real files & open gaps |
|:---|:---|:---:|:---:|:---|:---|
| **3** (`{3.0.*}`) | Classic MFC | ✅ (`main`) | ✅ (`main`) | **All 6 (tests):** `legacy_v3_synthetic.skp`, `legacy_v3_textured.skp` (`face_front_color`/`face_back_color` unset), and since #705 `legacy_v3_curves_and_text.skp` (curves, arcs, a painted placement, a text and a guide line) and `legacy_v3_no_materials.skp`. | ✅ On `main` since #705 (not released yet): the real multi-version project's V3 save reads in every reader and matches its V6 save (definitions, faces, curves, texts; [#284](https://github.com/iamahsanmehmood/openskp/issues/284)). |
| **4** (`{4.0.*}`) | Classic MFC | ✅ | ✅ | **All 6 (tests):** `legacy_v4_synthetic.skp`, `legacy_v4_textured.skp`, `face_colors_v4.skp` (plus a 47-definition / 13-dimension SDK-written model checked across readers). (V4 stores no instance/group names.) | ✅ Real V4 cube sample and the real multi-version project's V4 save parse with totals matching V6/2021/2025. |
| **5** (`{5.0.*}`) | Classic MFC | ✅ (SDK) | ⚠️ TS fails wild file; .NET / Dart / C++ / Rust ✅ (SDK) | **All 6 (tests):** `legacy_v5_synthetic.skp`, `legacy_v5_textured.skp`, `face_colors_v5.skp` (plus the 47-definition SDK-written model). | ⚠️ The multi-version sweep's V5 save is a corrupted 0-byte file. A real wild V5 file (`{5.0.262}`, reported on [#284](https://github.com/iamahsanmehmood/openskp/issues/284)) fails in TypeScript (`back-ref to unwalked slot 20617` on an inline PNG) and has not been tested in the other readers. |
| **6** (`{6.0.*}`) | Classic MFC | ✅ | ✅ | **All 6 (tests):** `legacy_v6_synthetic.skp`, `nesting_v6.skp`, `instance_scale_term_v6.skp`, `face_colors_v6.skp`. | ✅ Real multi-version project's V6 save parses in all published readers with identical totals. |
| **7, 8, 2013** (`{7.0.*}`, `{8.0.*}`, `{13.0.*}`) | Classic MFC | ✅ | ✅ | **All 6 (tests):** `nesting_2013.skp`; **Python, TS, .NET, Dart (tests):** `legacy_v7_synthetic.skp` (same `ver >= 14` instance-GUID gate in C++ and Rust). | ✅ Real multi-version project's V7, V8, and 2013 saves parse in all published readers with identical totals. |
| **2014–2018** (`{14.0.*}`–`{18.*}`) | Classic MFC | ✅ | ✅ | **All 6 (tests):** `arcs_2017.skp`, `nesting_2017.skp`, `legacy_text_arrows_2017.skp`, `legacy_textured_colorize_2017.skp`, `single_material_v17.skp`, `face_colors_{2016,2017,2018}.skp`, `texture_tiff_2017.skp`, `scene_capture_flags_{2015,2016,2017,2018}.skp`, `scene_hidden_entities_2017.skp`, `legacy_attribute_dictionaries_{2017,2018}.skp`. | ✅ Real multi-version project's 2014–2018 saves plus committed app-saved fixtures `capilla_quiroz_v17.skp` (`{17.0.18899}`) and `zero_material_custom_layer_v18.skp` (`{18.0.16975}`) parse in all 6. |
| **2019–2020** (`{19.0.*}`, `{20.*}`) | Classic MFC | ✅ | ✅ | **All 6 (tests):** `legacy_v2019_synthetic.skp`, `legacy_v2020_textured.skp`, `legacy_empty_2020.skp`, `arcs_2020.skp`, `nesting_2020.skp`, `sdk_saved_{2019,2020}.skp`, `sdk_saved_sections_{2019,2020}.skp`, `sdk_saved_tag_line_styles_{2019,2020,materials_2020}.skp`, `sdk_saved_textured_tag_line_styles_{2019,2020}.skp`, `face_colors_{2019,2020}.skp`, `scene_capture_flags_{2019,2020}.skp`, `scene_hidden_entities_{2019,2020}.skp`. | ✅ Real multi-version project's 2019 and 2020 saves plus committed app-saved fixture `gondola_v20.skp` (`{20.1.235}`) parse in all 6. |
| **2021+** (`{21.*}`–`{26.*}`) | VFF (ZIP + TLV) | ✅ | ✅ | **All 6 (tests):** `arcs_2021.skp`, `nesting_2021.skp`, `scene_without_camera_2021.skp`, `instance_scale_term_2021.skp`, `face_colors_2021.skp`, `texture_{bmp,gif,tga,tiff}_2021.skp`, `scene_hidden_entities_2024.skp`, `definition_attribute_dictionaries_2026.skp` (`{26.2.243}`). | ✅ Real multi-version project's 2021 and 2025 saves plus committed app-saved fixtures `SU_File.skp` and `Untitled.skp` parse in all 6. |

### Note on the multi-version real-project sweep ([#284](https://github.com/iamahsanmehmood/openskp/issues/284))

The real-world sweep consists of 16 saves of one 5 MB, 146-definition textured
project across SketchUp 3 through 2025; its V5 save is a corrupted 0-byte file,
leaving **15 usable files**. **14 of those 15 usable saves** (V4, V6, V7, V8,
2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, and 2025) parse in all
five published readers with identical geometry and placement totals (32,982
vertices, 40,925 edges, 9,652 faces, and 150 instances; full scene-build and
export verification on that project was run in Python). The 15th, **V3**,
reads in all six readers on `main` since #705 (not released yet), with the same totals. If you hit an unparsed classic file in
the wild, please report it on
[issue #284](https://github.com/iamahsanmehmood/openskp/issues/284).

## Reporting a gap

If you hit a feature that behaves differently — or is missing — in one
language versus another, please
[open an issue](https://github.com/iamahsanmehmood/openskp/issues/new) with
the language, the feature, and (ideally) a real `.skp` file that reproduces
it. This page gets updated as gaps are found and closed.
