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

| Language | Registry | Released version | On `main`, not released yet |
|:---|:---|:---:|:---|
| 🐍 Python | [PyPI](https://pypi.org/project/openskp/) | [![PyPI](https://img.shields.io/pypi/v/openskp.svg?label=)](https://pypi.org/project/openskp/) | **Yes** — [`preview-python-v1.3.0`](https://github.com/iamahsanmehmood/openskp/releases/tag/preview-python-v1.3.0), GitHub-only, see [§3](#3-unreleased-on-main) |
| 📘 TypeScript | [npm](https://www.npmjs.com/package/openskp) | [![npm](https://img.shields.io/npm/v/openskp.svg?label=)](https://www.npmjs.com/package/openskp) | No |
| 🚀 .NET | [NuGet](https://www.nuget.org/packages/OpenSkp) | [![NuGet](https://img.shields.io/nuget/v/OpenSkp.svg?label=)](https://www.nuget.org/packages/OpenSkp) | No |
| 🎯 Dart | [pub.dev](https://pub.dev/packages/openskp) | [![Pub](https://img.shields.io/pub/v/openskp.svg?label=)](https://pub.dev/packages/openskp) | No |
| ⚙️ C++ | [GitHub Releases](https://github.com/iamahsanmehmood/openskp/releases?q=cpp-) | [![C++](https://img.shields.io/github/v/release/iamahsanmehmood/openskp?filter=cpp-v*&label=)](https://github.com/iamahsanmehmood/openskp/releases?q=cpp-) | **Yes** — direct SketchUp → Fragments export, see [§3](#3-unreleased-on-main) |

Each language releases independently — see
[CONTRIBUTING.md § Releasing](../CONTRIBUTING.md#releasing-maintainers) for
why version numbers across languages can legitimately diverge rather than
always moving in lockstep.

## 2. Feature matrix

Every feature OpenSKP has, across all 5 languages. ✅ = shipped and released.
🔶 = shipped on Python's `main` but not released; see [§3](#3-unreleased-on-main).

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
| Attribute dicts: full 9-value-type support (`Point3d`/`Vector3d`/`Length`/`Timestamp`/nested lists) | ✅ | ❌ str/int/float only | ❌ | ❌ | ❌ str only |
| Attribute dicts: multiple dictionaries per entity | ✅ | ❌ single dict only | ❌ | ❌ | ✅ |
| VFF pages/scenes + dimension parsing | ✅ | ✅ | ✅ | ✅ | ✅ |
| Legacy (pre-2021) pages/scenes reading | ✅ | ❌ VFF only | ❌ VFF only | ❌ VFF only | ❌ VFF only |
| Construction lines/points reading | ✅ | ❌ | ❌ | ❌ | ❌ |
| VFF per-layer-hidden flag reading | 🔶 on main | ❌ | ❌ | ❌ | ❌ |
| `mesh_index[...].properties` populated | ✅ | ✅ | ✅ | ✅ | ✅ |
| `model.layers` in file order (not alphabetical) | ✅ | ✅ | ✅ | ✅ | ❌ known gap |
| **Scene building & observability** | | | | | |
| Scene baking / triangulation (`buildScene()`) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Instancing-preserving scene output (`build_instanced_scene()`) | ✅ | ✅ | ✅ | ✅ | ✅ |
| earcut-based triangulation (replaces Shapely + concave-face correctness fix) | 🔶 on main | n/a, own triangulator | n/a | n/a | n/a |
| Large-file streaming fix ([#264](https://github.com/iamahsanmehmood/openskp/issues/264)) | 🔶 on main | not checked | not checked | not checked | not checked |
| Opt-in progress reporting + structured errors | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Writing** | | | | | |
| Writer base (`create()`): materials, layers, definitions, groups, faces, holes, auto-triangulate | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer: circular/arc curves + polylines | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer: instance/group rotation + visibility | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer: Image entities (`add_image`) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer: applied texture width + material opacity | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer: material/layer handle validation | ✅ | ✅ | ✅ | ✅ | ✅ |
| Writer: linear dimensions + leader text | ✅ | ❌ | ❌ | ❌ | ❌ |
| Writer: section planes | ✅ | ❌ | ❌ | ❌ | ❌ |
| Writer: construction lines/points | ✅ | ❌ | ❌ | ❌ | ❌ |
| Editor (`open_existing()`) | ✅ | ✅ | ✅ | ✅ | ✅ |
| SKP-to-code generator (`to_*_code()`) | ✅ | ✅ | ✅ | ✅ | ✅ |
| codegen round-trips `applied_width`/opacity | ❌ | ❌ | ❌ | ❌ | ❌ — gap in all 5 |
| **Export** | | | | | |
| GLB / OBJ+MTL / STL / PLY / DXF 3D / IFC4 / JSON | ✅ | ✅ | ✅ | ✅ | ✅ |
| IFC export: unit/axis fix, real names + layer visibility, plugin-attribute Psets, full-path classification | 🔶 on main | not checked for equivalent issues | not checked | not checked | 🔶 on main, GitHub-only |
| Direct SketchUp → Fragments (`.frag`) export | 🔶 on main, GitHub-only | 🔶 open [PR #276](https://github.com/iamahsanmehmood/openskp/pull/276), CI failing | ❌ not started | ❌ not started | 🔶 on main, GitHub-only |

## 3. Unreleased on main

What's implemented and merged, but not yet in a package release. Currently
Python-only:

| Item | Where | Tracking |
|:---|:---|:---|
| Direct SketchUp → Fragments (`.frag`) export | `openskp.export.fragments` | [Release](https://github.com/iamahsanmehmood/openskp/releases/tag/preview-python-v1.3.0) · [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md#fragments-export) |
| earcut-based triangulation (perf + concave-face correctness fix) | `_core.py` | [CHANGELOG.md § 1.3.0](../CHANGELOG.md) |
| 4 IFC export correctness fixes | `export/ifc.py` | [CHANGELOG.md § 1.3.0](../CHANGELOG.md) |
| VFF per-layer-hidden flag reading | `_core.py` | [CHANGELOG.md § 1.3.0](../CHANGELOG.md) |
| Legacy pages/scenes reading | `legacy.py` | [CHANGELOG.md § 1.3.0](../CHANGELOG.md) |
| Section planes writer | `create.py` | [CHANGELOG.md § 1.3.0](../CHANGELOG.md) |
| Construction lines/points read + write | `legacy.py`, `create.py` | [CHANGELOG.md § 1.3.0](../CHANGELOG.md) |
| Large-file parser fix | `_core.py` | [#264](https://github.com/iamahsanmehmood/openskp/issues/264) |

All of the above are on GitHub as the tagged
[`preview-python-v1.3.0`](https://github.com/iamahsanmehmood/openskp/releases/tag/preview-python-v1.3.0)
release (installable by pinning that tag directly — see the
[README](../README.md) or
[DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md#fragments-export)) — not on PyPI yet.
It folds into a proper PyPI `1.3.0` once the cross-language porting items in
[§2](#2-feature-matrix) and [issue #285](https://github.com/iamahsanmehmood/openskp/issues/285)
catch up. TypeScript, .NET, and Dart currently have nothing unreleased —
their `main` matches what's published.

C++ merged, not yet released:

| Item | Where | Tracking |
|:---|:---|:---|
| Direct SketchUp → Fragments (`.frag`) export | `openskp::to_fragments`/`export_fragments` (`fragments_export.cpp`) | [CHANGELOG.md § Unreleased](../CHANGELOG.md) |
| Multi-dictionary attribute extraction (`Instance::attribute_dictionaries` / `InstancedNode::attribute_dictionaries`) | `geometry.cpp`, `instanced_scene.cpp`, `scene.cpp` | [CHANGELOG.md § Unreleased](../CHANGELOG.md) |
| 4 IFC export correctness fixes (units/axis, real names + layer visibility, plugin-attribute Psets, opt-in full-path classification) | `ifc_export.cpp` | [CHANGELOG.md § Unreleased](../CHANGELOG.md) |
| `mesh_index[...].properties`/`.attribute_dictionaries` backfill in the baked (`build_scene`) path | `scene.cpp` | [CHANGELOG.md § Unreleased](../CHANGELOG.md) |

Known gap in this C++ work, stated honestly rather than glossed over:
attribute dictionary values decode as strings only (no `Point3d`/`Length`/
nested-list support, matching C++'s existing string-only property handling
elsewhere — see the feature matrix above).

## 4. Real SketchUp file version support

**Not every real old-format file parses today — stated plainly, not glossed
over.** A real-world version-compatibility sweep (14 `.skp` files, the same
project saved across its history from SketchUp version 3 up through 2025)
found:

**8 files parse, build, and export cleanly**: saves from **2014, 2015, 2016,
2017, 2018, 2020, 2021, and 2025** — consistent output (146 definitions / 131
mesh resources) across all of them.

**The remaining files hit 5 distinct error signatures**, investigated and
root-caused to varying depth (tracked in
[issue #284](https://github.com/iamahsanmehmood/openskp/issues/284)):

| SketchUp version | Error | Status |
|:---|:---|:---|
| V3 | `expected a string record` | Not investigated further |
| V4 | `texture object is not a dib` | Not investigated further |
| V6 | `definition list misaligned` | Not investigated further |
| V7, V8, 2013 | `class-ref to non-class slot N (CAttributeNamed)` | Root cause narrowed to inside one nested `CGroup`'s recursive content; exact byte-level origin not yet found. Two candidate fixes tested and disproven with evidence — not guessed at. |
| 2019 | `implausible def entity count` | Not investigated further |

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
