# Roadmap

What's shipped, what's next, and what's known-broken — kept current, not a
snapshot. If you're looking for something to contribute, this is where to
start: everything below either has an open issue to pick up or a clear
"why this hasn't happened yet."

For the day-to-day engineering log this page is distilled from, see the
project's [CHANGELOG.md](CHANGELOG.md) (what shipped, per version) and
[`docs/LANGUAGE_PARITY.md`](docs/LANGUAGE_PARITY.md) (what each language
currently supports).

---

## Where each language stands

All 5 published languages ship **1.7.0**, and a sixth package, Rust
(`packages/rust`), carries **1.7.0** in the repository (not on crates.io yet).
See [CHANGELOG.md](CHANGELOG.md) for what shipped and
[`docs/LANGUAGE_PARITY.md`](docs/LANGUAGE_PARITY.md) for the full six-language
matrix.

| Language | Latest release | Status |
|:---|:---|:---|
| Python | [1.7.0 on PyPI](https://pypi.org/project/openskp/) | Reference implementation — ahead on scene writing (`add_scene`), scene material names, `IFCELEMENTASSEMBLY` / 3-arg IFC classifier, and GLB sidecar metadata |
| TypeScript | [1.7.0 on npm](https://www.npmjs.com/package/openskp) | Full 1.7.0 reader, scene, writer, and exporter parity except the items in [Cross-language porting backlog](#cross-language-porting-backlog) |
| .NET | [1.7.0 on NuGet](https://www.nuget.org/packages/OpenSkp) | Full 1.7.0 reader, scene, writer, and exporter parity except the items in [Cross-language porting backlog](#cross-language-porting-backlog) |
| Dart | [1.7.0 on pub.dev](https://pub.dev/packages/openskp) | Full 1.7.0 reader, scene, writer, and exporter parity (`openskp_fragments` remains git/path-only) — see [Cross-language porting backlog](#cross-language-porting-backlog) |
| C++ | [1.7.0 on GitHub releases](https://github.com/iamahsanmehmood/openskp/releases?q=cpp-) | Adds `Definition::no_scale_mask` and `Layer::line_style`; missing `add_scene`, scene material names, classic model attribute dictionaries, and `InstanceNode::definition_attribute_dictionaries` |
| Rust | `1.7.0` in `packages/rust` *(not on crates.io)* | Matches C++ and Python on VFF + classic definition/model dictionaries, `InstanceNode::definition_attribute_dictionaries`, `no_scale_mask`, and `InstancedNode::hidden`; missing `add_scene`, scene material names, and STL painted back-side deduplication ([#601](https://github.com/iamahsanmehmood/openskp/issues/601)) |

Each published language is released independently — see
[CONTRIBUTING.md § Releasing](CONTRIBUTING.md#releasing-maintainers) for
the tag format per language.

---

## Cross-language porting backlog

Tracked in full, with checkboxes, in
[issue #285](https://github.com/iamahsanmehmood/openskp/issues/285) — one
line per feature, not one issue per language per feature. What remains open
across languages after **1.7.0** (see
[`docs/LANGUAGE_PARITY.md`](docs/LANGUAGE_PARITY.md) for the full matrix):

- **Writer scenes (`add_scene`)** — camera + hidden-layer `CViewPage` scenes in
  new classic files are Python-only (`create.py`). *(Reading scenes — including
  cameras, `hidden_layers`, `hidden_entity_ids`, and `capture_flags` — works in
  all six languages across VFF 2021+ and classic pre-2021 files.)*
- **Material names on scene `gltf_materials`** — Python preserves SketchUp
  material names on `gltf_materials` and keeps differently named materials of
  the same colour separate; TypeScript, .NET, Dart, C++, and Rust omit `name`
  on scene materials built from `.skp`.
- **Remaining definition- and model-level attribute dictionaries** — Python and
  Rust read VFF + classic definition and model attribute dictionaries and
  expose `InstanceNode.definition_attribute_dictionaries`; C++ lacks classic
  model dictionaries and `InstanceNode::definition_attribute_dictionaries`;
  TypeScript, .NET, and Dart read VFF definition dictionaries only.
- **Attribute dictionaries in JSON metadata export** — Python, C++, and Rust
  serialize `attribute_dictionaries` on `scene_hierarchy` and `mesh_index` in
  `to_json()`; TypeScript, .NET, and Dart emit `properties` only.
- **IFC `IFCELEMENTASSEMBLY` wrapper grouping and 3-arg custom `classifier`** —
  Python-only; TypeScript, .NET, Dart, C++, and Rust accept a 2-arg custom
  `classifier` and do not emit `IFCELEMENTASSEMBLY`.
- **Smaller cross-port items** — classic `CViewPage` narrow-scan fallback in
  TypeScript, .NET, and Dart; `Definition.no_scale_mask` in Python, TypeScript,
  .NET, and Dart (present in C++ and Rust); `InstancedNode.hidden` in Python,
  .NET, Dart, and C++ (present in TypeScript and Rust); `Layer.line_style` in
  Python, TypeScript, .NET, Dart, and Rust (exposed in C++); STL painted
  back-side deduplication ([#601](https://github.com/iamahsanmehmood/openskp/issues/601))
  in Rust; randomizing the writer scaffold file GUID at `0x34..0x43` in Python,
  TypeScript, .NET, and Dart.
- **Colorized textures in GLB files and scenes (all six languages)** — SketchUp
  bakes a colorized material's hue-shift or greyscale tint into the image;
  OpenSKP currently keeps the untinted base image and multiplies by
  `baseColorFactor` (tracked on
  [issue #285](https://github.com/iamahsanmehmood/openskp/issues/285)).

**Good first contribution?** Most of these have a real, SDK-verified Python or
C++ implementation to port from — the hard research is already done. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the workflow.

---

## Known bugs

### Classic (pre-2021 MFC) parser: remaining version gaps

Tracked in [issue #284](https://github.com/iamahsanmehmood/openskp/issues/284).
Classic files from SketchUp 4 through 2020 parse across all six readers on
synthetic, SDK-written, and real multi-version project saves. The real SketchUp 3
project save reads in all six readers on `main` since #705 (not released yet);
the remaining known gap is a real SketchUp 5 file in the wild (fails in TypeScript) — see
[docs/LANGUAGE_PARITY.md § 4](docs/LANGUAGE_PARITY.md#4-real-sketchup-file-version-support)
for the version-by-version table and evidence.

### Fragments export: no native visibility field

The public ThatOpen Fragments schema has no field for per-layer visibility at
all. OpenSKP carries it via a `Model.metadata` JSON sidecar — a convention
this project defined, not part of the public schema. Any other tool reading
the same `.frag` file needs to know to look there. Not really "fixable" (it's
a property of the format), but worth knowing before building against it.

---

## Future ideas (not scheduled)

Bigger, not-yet-committed ideas worth remembering. Nothing here has an owner or a
timeline — they're parked, not planned.

- **Semantic diff between two `.skp` files.** Since every language already parses a
  file into a structured model, a tool comparing two versions and reporting real
  changes ("Component X moved 3ft", "Material Y changed color", "2 groups added") is
  far more useful than git's own byte-level diff on a binary file, and needs no new
  format — just a comparison pass over two `parse()` outputs. The real design problem:
  SketchUp's internal entity numbering isn't stable across saves (seen repeatedly this
  project), so matching "the same" component/instance between two versions has to go
  by name/definition-content, not raw internal IDs, or the diff reports false changes
  on every save. Scoped deliberately as diff-only, not merge — true 3-way merge of
  concurrent geometric edits is a much harder, different problem this project isn't
  taking on.

---

## Reporting something

Bug reports and feature requests both go through
[GitHub Issues](https://github.com/iamahsanmehmood/openskp/issues/new/choose).
If you hit a language-parity gap not listed above, please file it — this page
and `docs/LANGUAGE_PARITY.md` are only as accurate as what's been reported.
