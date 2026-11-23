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
| 🐍 Python | [PyPI](https://pypi.org/project/openskp/) | [![PyPI](https://img.shields.io/pypi/v/openskp.svg?label=)](https://pypi.org/project/openskp/) | No |
| 📘 TypeScript | [npm](https://www.npmjs.com/package/openskp) | [![npm](https://img.shields.io/npm/v/openskp.svg?label=)](https://www.npmjs.com/package/openskp) | No |
| 🚀 .NET | [NuGet](https://www.nuget.org/packages/OpenSkp) | [![NuGet](https://img.shields.io/nuget/v/OpenSkp.svg?label=)](https://www.nuget.org/packages/OpenSkp) | No |
| 🎯 Dart | [pub.dev](https://pub.dev/packages/openskp) | [![Pub](https://img.shields.io/pub/v/openskp.svg?label=)](https://pub.dev/packages/openskp) | No |
| ⚙️ C++ | [GitHub Releases](https://github.com/iamahsanmehmood/openskp/releases?q=cpp-) | [![C++](https://img.shields.io/github/v/release/iamahsanmehmood/openskp?filter=cpp-v*&label=)](https://github.com/iamahsanmehmood/openskp/releases?q=cpp-) | No |

Each language releases independently by pushing its own tag — see
[CONTRIBUTING.md § Releasing](../CONTRIBUTING.md#releasing-maintainers) for
the exact tag format per language (Dart is the one to double check: it's
the bare `vX.Y.Z`, not `dart-vX.Y.Z` — the latter builds fine but fails at
the pub.dev upload step).

## 2. Feature matrix

Every feature OpenSKP has, across all 5 languages. ✅ = shipped and
released. 🔶 = implemented on `main`, not published to a package registry.
❌ = not yet ported to that language; see
[§3](#3-whats-still-outstanding-after-160) for what's tracked there.

| Feature | Python | TypeScript | .NET | Dart | C++ |
|:---|:---:|:---:|:---:|:---:|:---:|
| **Reading** | | | | | |
| Parse VFF (2021+) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Parse legacy MFC (2013–2020) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Geometry (vertices/edges/faces/UVs) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Component hierarchy / nested definitions | ✅ | ✅ | ✅ | ✅ | ✅ |
| Layers/tags (color + visibility) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Materials & textures | ✅ | ✅ | ✅ | ✅ | ✅ |
| Styles (front/back unpainted face color) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Dynamic Component attributes | ✅ | ✅ | ✅ | ✅ | ✅ |
| Single attribute dictionary per entity | ✅ | ✅ | ✅ | ✅ | ✅ |
| Attribute dicts: full 9-value-type support, **legacy (pre-2021) format** (`Point3d`/`Vector3d`/`Length`/`Timestamp`/nested lists) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Attribute dicts: full 9-value-type support, **VFF (2021+) format** | ✅ 7/9 — no native `bool`/`time_t` tag observed yet | ❌ `AD38` string only (1/9) | ❌ string only (1/9) | ❌ string only (1/9) | ❌ string only (1/9) |
| Attribute dicts: multiple dictionaries per entity | ✅ | ✅ | ✅ | ✅ | ✅ |
| Attribute dicts surfaced in GLB/JSON metadata export (not just IFC Psets) | ✅ | n/a, not independently re-checked this cycle | n/a, not independently re-checked this cycle | n/a, not independently re-checked this cycle | ✅ |
| VFF pages/scenes + dimension parsing | ✅ | ✅ | ✅ | ✅ | ✅ |
| Legacy (pre-2021) pages/scenes reading | ✅ | ✅ | ✅ | ✅ | ✅ |
| Construction lines/points reading | ✅ legacy + VFF | ✅ legacy + VFF | ✅ legacy + VFF | ✅ legacy + VFF | ✅ legacy + VFF |
| VFF per-layer-hidden flag reading | ✅ | ✅ | ✅ | ✅ | ✅ |
| Per-edge/per-face layer, `Edge#curve` pointers, classic `CArcCurve` frames (legacy + VFF) | ✅ | ❌ | ❌ | ❌ | ❌ |
| `mesh_index[...].properties` populated | ✅ | ✅ | ✅ | ✅ | ✅ |
| `model.layers` in file order (not alphabetical) | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Scene building & observability** | | | | | |
| Scene baking / triangulation (`buildScene()`) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Instancing-preserving scene output (`build_instanced_scene()`) | ✅ | ✅ | ✅ | ✅ | ✅ |
| `build_instanced_scene()`: loose-edge curve support (structural framing/LGS) | ✅ | ❌ | ❌ | ❌ | ❌ |
| Public typed model: `Edge.layer`/`Edge.curve_id`/`Face.layer`, `loose_edge_runs()` (raw ids, no baking - for a B-rep consumer building real wire edges) | ✅ | ❌ | ❌ | ❌ | ❌ |
| earcut-based triangulation (replaces Shapely + concave-face correctness fix) | ✅ | n/a, own triangulator | n/a | n/a | n/a |
| Large-file streaming fix ([#264](https://github.com/iamahsanmehmood/openskp/issues/264)) | ✅ | not checked | not checked | not checked | not checked |
| Opt-in progress reporting + structured errors | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Writing** | | | | | |
| Writer base (`create()`): materials, layers, definitions, groups, faces, holes, auto-triangulate | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer: circular/arc curves + polylines | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer: instance/group rotation + visibility | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer: Image entities (`add_image`) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer: applied texture width + material opacity | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer: material/layer handle validation | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer: linear dimensions + leader text | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer: section planes | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer: construction lines/points | ✅ | ✅ | ✅ | ✅ | ✅ |
| Editor (`open_existing()`) | ✅ | ✅ | ✅ | ✅ | ✅ |
| SKP-to-code generator (`to_*_code()`) | ✅ | ✅ | ✅ | ✅ | ✅ |
| codegen round-trips `applied_width`/opacity | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Export** | | | | | |
| GLB / OBJ+MTL / STL / PLY / DXF 3D / IFC4 / JSON | ✅ | ✅ | ✅ | ✅ | ✅ |
| IFC export: unit/axis fix, real names + layer visibility, plugin-attribute Psets, full-path classification | ✅ | not checked for equivalent issues | not checked | not checked | ✅ |
| IFC export: loose-edge curve sets + analytic arcs as `IfcAnnotation` / `IfcIndexedPolyCurve`, IFC4 STEP conformance fixes | ✅ | ❌ | ❌ | ❌ | ❌ |
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
| 🐍 **Python** | SketchUp's C library (`SketchUpAPI`, including `--resave` to modern VFF) during feature development + self round-trip test suite | Baseline for the Python-scaffold group in [`ci-writer-parity.yml`](../.github/workflows/ci-writer-parity.yml) | Preserves `blank_v17.skp`'s scaffold GUID at `0x34..0x43`; starts entity persistent IDs at `19` (`data[1987] + 1`); writes opaque textured-material RGBA alpha `254` (`0xfe`) |
| 📘 **TypeScript** | Python's writer output, byte-for-byte + self round-trip test suite | Asserted byte-identical to Python (ignoring per-definition/instance random UUIDs and `u32` definition Unix timestamps) in [`ci-writer-parity.yml`](../.github/workflows/ci-writer-parity.yml) | **0** non-GUID/timestamp bytes vs Python |
| 🚀 **.NET** | Python's writer output, byte-for-byte + self round-trip test suite | Asserted byte-identical to Python (ignoring random UUIDs and `u32` definition timestamps) in [`ci-writer-parity.yml`](../.github/workflows/ci-writer-parity.yml) | **0** non-GUID/timestamp bytes vs Python |
| 🎯 **Dart** | Python's writer output, byte-for-byte + self round-trip test suite | Asserted byte-identical to Python (ignoring random UUIDs and `u32` definition timestamps) in [`ci-writer-parity.yml`](../.github/workflows/ci-writer-parity.yml) | **0** non-GUID/timestamp bytes vs Python |
| ⚙️ **C++** | SketchUp's C library via [`packages/cpp/tools/check_native_skp.py`](../packages/cpp/tools/check_native_skp.py) (including `--resave` to modern VFF) + native binary invariants + self round-trip test suite | Baseline for the C++ group + diffed against Python in [`ci-writer-parity.yml`](../.github/workflows/ci-writer-parity.yml) | Randomizes the 16-byte file GUID at `0x34..0x43`, starts entity persistent IDs at `18` (`kFirstNewPid = 18` vs Python's `19`), and writes opaque textured-material RGBA alpha `255` (`0xff`) vs Python's `254` (`0xfe`) |
| 🦀 **Rust** *(unpublished)* | C++'s writer output, byte-for-byte (`tests/create_reference_test.rs`) + self round-trip test suite | Asserted byte-identical to C++ (ignoring random UUIDs and `u32` definition timestamps) in [`ci-writer-parity.yml`](../.github/workflows/ci-writer-parity.yml) and `ci-rust.yml` | **0** non-GUID/timestamp bytes vs C++ |

## 3. What's still outstanding after 1.6.0

Nothing is unreleased on main: the 2026-11-22 synchronized 1.6.0 (see
[CHANGELOG.md § 1.6.0](../CHANGELOG.md)) released everything merged before it.
What remains open is genuine cross-language porting work, not a
release-timing gap:

**Closed**: reading a `.frag` file back (`from_fragments`/`read`) is now
done in all 5 languages (TypeScript #362, .NET #363, Dart #364, C++ #365)
- the last remaining item from the original 1.3.0 gap list. Along the
way, Dart's port found and fixed a real, previously-undetected write-side
bug (an odd material count silently corrupted the materials vector -
`flat_buffers` struct-vector alignment padding landing in the wrong
place), and C++'s port found and fixed an unrelated one of its own (a
dangling-reference bug in a hand-written JSON parser - see
`fragments_export.cpp`'s own comment on `MinimalJsonParser`). Neither
would have surfaced without a reader to actually exercise the write
path's own output.

| Item | Python has it via | Needs porting to |
|:---|:---|:---|
| `build_instanced_scene()` loose-edge curve support + public typed-model `Edge.layer`/`Face.layer`/`loose_edge_runs()` | `_curves.py`, `instanced_scene.py`, `model.py` | TypeScript, .NET, Dart, C++ — none started |
| IFC export: loose-edge curve sets + analytic arcs as `IfcAnnotation`/`IfcIndexedPolyCurve`, plus the 4 IFC4 STEP-conformance fixes | `scene.py`, `export/ifc.py` | TypeScript, .NET, Dart, C++ — none started |
| Per-edge/per-face layer | `legacy.py`/`_core.py` | TypeScript, .NET, Dart, C++ |
| Python scenes since 1.6.0: a painted group or component passes its whole material to the faces inside it, material names are kept, colorized textures are flagged, a texture a scene cannot carry is reported, and `original_textures=True` keeps it | `scene.py`, `instanced_scene.py`, `_face_groups.py` | TypeScript, .NET, Dart, C++ — not checked against these changes |
| A definition's and the model's own attribute dictionaries (Dynamic Components), in VFF and classic files (1.6.0) | `_core.py`, `legacy.py`, `model.py` | TypeScript, .NET, Dart, C++ — not checked |
| Attribute dicts: full 9-value-type decoding for VFF (2021+) format (Python: 7/9, others: string-only) — not independently re-verified this cycle for TS/.NET/Dart/C++, carried over from the prior version of this page rather than asserted fresh | `_core.py` | TypeScript, .NET, Dart, C++ — status needs a fresh check |

All tracked in [issue #285](https://github.com/iamahsanmehmood/openskp/issues/285).
None of this blocked the 1.3.0 release — see the note at the top of
[§1](#1-package-versions) for why.

## 4. Real SketchUp file version support

**Not every real old-format file parses today — stated plainly, not glossed
over.** A real-world version-compatibility sweep (15 real `.skp` files —
16 saves of the same project across its history from SketchUp version 3 up
through 2025, one of which, V5, saved out corrupted/empty) found:

**In the Python reader, 11 files parse, build, and export cleanly** (the sweep was run with Python; TypeScript, .NET, Dart and C++ read the same saves since [#442](https://github.com/iamahsanmehmood/openskp/issues/442) and give identical totals on every one of them - 32,982 vertices, 40,925 edges, 9,652 faces and 150 instances, the same as its 2021 and 2025 saves): saves from **2014, 2015,
2016, 2017, 2018, 2020, 2021, 2025, and — as of [#310](https://github.com/iamahsanmehmood/openskp/pull/310) — V7, V8, and
2013** — consistent output (146 definitions / 131 mesh resources) across all
of them.

**The remaining 4 files hit 4 distinct error signatures**, investigated and
root-caused to varying depth (tracked in
[issue #284](https://github.com/iamahsanmehmood/openskp/issues/284)):

| SketchUp version | Error | Status |
|:---|:---|:---|
| V3 | `expected a string record` | **Fixed in all 5 languages** ([#407](https://github.com/iamahsanmehmood/openskp/pull/407), ported in [#410](https://github.com/iamahsanmehmood/openskp/issues/410)) - calibrated on one real V3 file; single-material V3 files unverified |
| V4, V5 | `texture object is not a dib` | **Fixed in all 5 languages** (V4 and V5 differ from V6 in about a dozen small layout details - see the CHANGELOG): Python in [#440](https://github.com/iamahsanmehmood/openskp/pull/440), the other four in the follow-up port. The real V4 project parses identically to its V6 save in Python; the other four readers match V6 on SketchUp-written files (including a 47-definition, 13-dimension model) and on the real V4 sample, and, since [#442](https://github.com/iamahsanmehmood/openskp/issues/442), read the real project's V4 save with the same totals as its V6 save. The project's own V5 save is a 0-byte file |
| V6 | `definition list misaligned` | **Fixed in all 5 languages** ([#385](https://github.com/iamahsanmehmood/openskp/pull/385), ported in [#410](https://github.com/iamahsanmehmood/openskp/issues/410)) |
| 2019 | `implausible def entity count` | **Fixed in all 5 languages** (a separator null after the last layer of a definition shifted the entity count). The real 2019 save parses identically to its 2018 save in Python; the other four readers are checked on a SketchUp-written 2019 file and, since [#442](https://github.com/iamahsanmehmood/openskp/issues/442), read the real project's 2019 save with the same totals as its 2018 save |

~~V7, V8, 2013 — `class-ref to non-class slot N (CAttributeNamed)`~~ —
**fixed in [#310](https://github.com/iamahsanmehmood/openskp/pull/310)**.
Root cause: `_read_instance`'s trailing-GUID read for
`CComponentInstance`/`CGroup` was gated on the class's own reported
`schema` number (`schema >= 5` implies a GUID), which doesn't generalize —
a real V7 file's `CComponentInstance` reports schema 6, well above that
threshold, yet has no GUID at all. Forcing the read anyway silently ate
into the next sibling entity's own tag bytes, which either crashed deep in
a nested definition (the `CAttributeNamed` collision this row was
originally about) or — the more dangerous variant, caught only while
verifying the fix — got silently absorbed by the root entity list's own
over-declared-count tolerance, truncating a real 18-instance root scene
down to 1 with no error raised at all. Fixed by gating the GUID read on
the file's real version number (`ar.ver >= 14`) instead of schema;
verified byte-for-byte identical to the same files' 2014+ output.

The three fixes above (the V7/V8/2013 instance-GUID gate, the V6
attribute-dictionary trailer, and the V3 layout) were first made in the
**Python** legacy MFC reader. Each language has its own independently
implemented parser, so a fix in one does not carry over: they were ported to
the TypeScript, .NET, Dart and C++ readers separately (C++ already had the
first two), and each port is checked against the same synthetic V3/V6/V7/2014
fixtures.

If you hit one of these versions (or a new one) in your own files, a real
repro `.skp` file dramatically speeds up narrowing this down further — see
[issue #284](https://github.com/iamahsanmehmood/openskp/issues/284).

## Reporting a gap

If you hit a feature that behaves differently — or is missing — in one
language versus another, please
[open an issue](https://github.com/iamahsanmehmood/openskp/issues/new) with
the language, the feature, and (ideally) a real `.skp` file that reproduces
it. This page gets updated as gaps are found and closed.
