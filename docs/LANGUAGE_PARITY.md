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

As of the 2026-09-18 synchronized release, all 5 languages are on
**1.3.0** — but same version number does not mean identical capability.
Python and C++ carry real, additional capability (Fragments reading,
loose-edge/curve support, IFC curve annotations, legacy pages/scenes
reading) that TypeScript, .NET, and Dart do not have yet - see
[§2](#2-feature-matrix) for exactly what differs. This was a deliberate
choice, not an oversight: that work was real, tested, and ready, and
holding it back purely to keep every language byte-for-byte equal would
have wasted it for no real benefit. See
[CHANGELOG.md § 1.3.0](../CHANGELOG.md) for the full picture.

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
released. ❌ = not yet ported to that language; see
[§3](#3-whats-still-outstanding-after-130) for what's tracked there.

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
| Legacy (pre-2021) pages/scenes reading | ✅ | ❌ VFF only | ❌ VFF only | ❌ VFF only | ✅ |
| Construction lines/points reading | ✅ legacy only | ✅ | ✅ | ✅ | ✅ legacy only |
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
| Direct SketchUp → Fragments (`.frag`) export | ✅ | ✅ — verified against the real `@thatopen/fragments` runtime | ✅ | ✅ | ✅ |
| **Import** | | | | | |
| Read a `.frag` file back (6th input format alongside `.skp`) | ✅ | ✅ | ✅ | ✅ | ✅ |

## 3. What's still outstanding after 1.3.0

Everything that was tracked here as "unreleased on main" as of the previous
update to this page is now released as part of the 2026-09-18 synchronized
1.3.0 (see [CHANGELOG.md § 1.3.0](../CHANGELOG.md)). What remains open is
genuine cross-language porting work, not a release-timing gap:

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
| `build_instanced_scene()` loose-edge curve support + public typed-model `Edge.layer`/`Edge.curve_id`/`Face.layer`/`loose_edge_runs()` | `_curves.py`, `instanced_scene.py`, `model.py` | TypeScript, .NET, Dart, C++ — none started |
| IFC export: loose-edge curve sets + analytic arcs as `IfcAnnotation`/`IfcIndexedPolyCurve`, plus the 4 IFC4 STEP-conformance fixes | `scene.py`, `export/ifc.py` | TypeScript, .NET, Dart, C++ — none started |
| Legacy (pre-2021) pages/scenes reading | `legacy.py` (also in C++, released) | TypeScript, .NET, Dart |
| Per-edge/per-face layer, `Edge#curve` pointers, classic `CArcCurve` frames | `legacy.py`/`_core.py` | TypeScript, .NET, Dart, C++ |
| Attribute dicts: full 9-value-type decoding for VFF (2021+) format (Python: 7/9, others: string-only) — not independently re-verified this cycle for TS/.NET/Dart/C++, carried over from the prior version of this page rather than asserted fresh | `_core.py` | TypeScript, .NET, Dart, C++ — status needs a fresh check |

All tracked in [issue #285](https://github.com/iamahsanmehmood/openskp/issues/285).
None of this blocked the 1.3.0 release — see the note at the top of
[§1](#1-package-versions) for why.

## 4. Real SketchUp file version support

**Not every real old-format file parses today — stated plainly, not glossed
over.** A real-world version-compatibility sweep (15 real `.skp` files —
16 saves of the same project across its history from SketchUp version 3 up
through 2025, one of which, V5, saved out corrupted/empty) found:

**11 files parse, build, and export cleanly**: saves from **2014, 2015,
2016, 2017, 2018, 2020, 2021, 2025, and — as of [#310](https://github.com/iamahsanmehmood/openskp/pull/310) — V7, V8, and
2013** — consistent output (146 definitions / 131 mesh resources) across all
of them.

**The remaining 4 files hit 4 distinct error signatures**, investigated and
root-caused to varying depth (tracked in
[issue #284](https://github.com/iamahsanmehmood/openskp/issues/284)):

| SketchUp version | Error | Status |
|:---|:---|:---|
| V3 | `expected a string record` | Not investigated further |
| V4 | `texture object is not a dib` | Not investigated further |
| V6 | `definition list misaligned` | Not investigated further |
| 2019 | `implausible def entity count` | Not investigated further |

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

This was only investigated against the **Python** legacy MFC reader. Whether
the other 4 languages' independently-implemented legacy readers hit the same
files the same way has not been checked — each has its own parser, so a fix
in one does not imply a fix in the others.

If you hit one of these versions (or a new one) in your own files, a real
repro `.skp` file dramatically speeds up narrowing this down further — see
[issue #284](https://github.com/iamahsanmehmood/openskp/issues/284).

## Reporting a gap

If you hit a feature that behaves differently — or is missing — in one
language versus another, please
[open an issue](https://github.com/iamahsanmehmood/openskp/issues/new) with
the language, the feature, and (ideally) a real `.skp` file that reproduces
it. This page gets updated as gaps are found and closed.
