# Roadmap

What's shipped, what's next, and what's known-broken — kept current, not a
snapshot. If you're looking for something to contribute, this is where to
start: everything below either has an open issue to pick up or a clear
"why this hasn't happened yet."

For the day-to-day engineering log this page is distilled from, see the
project's [CHANGELOG.md](CHANGELOG.md) (what shipped, per version) and
[`docs/LANGUAGE_PARITY.md`](docs/LANGUAGE_PARITY.md) (what each of the 5
languages currently supports).

---

## Where each language stands

All 5 published languages released **1.6.0** together on 2026-11-22 — same
version number, not identical capability. See
[CHANGELOG.md § 1.6.0](CHANGELOG.md) for what shipped; the previous
synchronized release was 1.5.0 (2026-11-03). Python carries a few things the
other four do not (see the backlog below), rather than holding real, tested
work back purely for symmetry. A sixth package, Rust (`packages/rust`), is in
the repository with the same version number but is not on crates.io yet.

| Language | Latest release | Status |
|:---|:---|:---|
| Python | [1.6.0 on PyPI](https://pypi.org/project/openskp/) | Ahead of the other 4 on several items — see [Cross-language porting backlog](#cross-language-porting-backlog) below |
| TypeScript | [1.6.0 on npm](https://www.npmjs.com/package/openskp) | See [docs/LANGUAGE_PARITY.md](docs/LANGUAGE_PARITY.md) and the backlog below |
| .NET | [1.6.0 on NuGet](https://www.nuget.org/packages/OpenSkp) | See [docs/LANGUAGE_PARITY.md](docs/LANGUAGE_PARITY.md) and the backlog below |
| Dart | [1.6.0 on pub.dev](https://pub.dev/packages/openskp) | See [docs/LANGUAGE_PARITY.md](docs/LANGUAGE_PARITY.md) and the backlog below |
| C++ | [1.6.0 on GitHub releases](https://github.com/iamahsanmehmood/openskp/releases?q=cpp-) | Closest to Python — still missing scene writing (`add_scene`), loose-edge/curve support, per-edge/per-face layers, IFC curve annotations, definition/model attribute dictionaries, the 1.6.0 scene changes, and `face_front_color`/`face_back_color` (see [docs/LANGUAGE_PARITY.md](docs/LANGUAGE_PARITY.md)) |

Each language is released independently — see
[CONTRIBUTING.md § Releasing](CONTRIBUTING.md#releasing-maintainers) for
the tag format per language.

---

## Cross-language porting backlog

Tracked in full, with checkboxes, in
[issue #285](https://github.com/iamahsanmehmood/openskp/issues/285) — one
line per feature, not one issue per language per feature. What is currently
open across languages after 1.6.0 (see
[docs/LANGUAGE_PARITY.md](docs/LANGUAGE_PARITY.md) for the full, current
matrix):

- **Writer scenes (`add_scene`)** — camera + hidden-layer `CViewPage` scenes in
  new/edited classic files are Python-only today. *(Reading scenes works in all
  five languages for both VFF 2021+ and classic pre-2021 files.)*
- **Loose-edge/curve support in `build_instanced_scene()`**, plus the public
  typed model's `Edge.layer`/`Edge.curve_id`/`Face.layer`/`loose_edge_runs()`
  — Python only.
- **IFC export: loose-edge curve sets + analytic arcs** as
  `IfcAnnotation`/`IfcIndexedPolyCurve`, plus 4 IFC4 STEP-conformance fixes —
  Python only.
- **Per-edge/per-face layer, `Edge#curve` pointers, and classic `CArcCurve`
  frames** — Python only.
- **Python 1.6.0 scene & attribute additions** — painted-group/component
  material inheritance (textures + transparency) on inner faces, preserved
  material names, colorized-texture flag, `original_textures=True` (plus
  reporting skipped non-PNG/JPEG textures), and **definition- and model-level
  attribute dictionaries** (Dynamic Components formulas/options, in VFF and
  classic files) — Python only.
- **Current rendering-options face colours (`SkpModel.face_front_color` /
  `face_back_color`, [#550](https://github.com/iamahsanmehmood/openskp/pull/550))**
  — on `main` (unreleased) in Python only; scenes still colour unpainted faces
  with the tag colour ([#533](https://github.com/iamahsanmehmood/openskp/issues/533)).
- **Large-file `F901 > 7017 > 7117` per-definition streaming
  ([#264](https://github.com/iamahsanmehmood/openskp/issues/264))** — shipped
  in Python ([#265](https://github.com/iamahsanmehmood/openskp/pull/265)),
  TypeScript, and C++ ([#300](https://github.com/iamahsanmehmood/openskp/pull/300));
  `.NET` and `Dart` unwrap `F401` lazily but do not yet unwrap `F901 > 7017 > 7117`.
- **VFF `AA38` boolean attribute tag** — decoded in C++ (8/9 VFF value types);
  Python, TypeScript, .NET, and Dart decode the other 7 observed VFF types
  (`AD38` string, `A738` int32, `A938` float, `AF38` Length, `B438`/`B538`
  3D point/vector, `AE38` array).

**Good first contribution?** Most of these have a real, SDK-verified Python
implementation to port from — the hard research is already done. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the workflow.

---

## Known bugs

### Classic (pre-2021 MFC) parser: remaining version gaps

Tracked in [issue #284](https://github.com/iamahsanmehmood/openskp/issues/284).
Classic files from SketchUp 4 through 2020 parse across all five published
readers on synthetic, SDK-written, and real multi-version project saves (after
[#310](https://github.com/iamahsanmehmood/openskp/pull/310),
[#385](https://github.com/iamahsanmehmood/openskp/pull/385),
[#440](https://github.com/iamahsanmehmood/openskp/pull/440),
[#443](https://github.com/iamahsanmehmood/openskp/pull/443), and
[#465](https://github.com/iamahsanmehmood/openskp/pull/465)). The remaining
known gaps are the real SketchUp 3 project save (fails in every reader) and a
real SketchUp 5 file in the wild (fails in TypeScript; 2019–2020
`CCustomLineStyle` tag line styles [#514](https://github.com/iamahsanmehmood/openskp/issues/514)
are now ported on `main` across all ports via [#579](https://github.com/iamahsanmehmood/openskp/pull/579)) — see
[docs/LANGUAGE_PARITY.md § 4](docs/LANGUAGE_PARITY.md#4-real-sketchup-file-version-support)
for the single version-by-version table and evidence.

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

## Documentation

- [x] `CHANGELOG.md`'s `[Unreleased]` header, previously stale (everything
  under it had already shipped in 1.2.0) — corrected.
- [x] `docs/LANGUAGE_PARITY.md` — the feature matrix above, as a standalone
  reference.
- [x] This page.
- [x] `docs/DEVELOPER_GUIDE.md` — full Fragments export API reference.
- [x] `openskp.com` — 1.2.0/1.3.0 release notes added, Fragments export
  card added, the .NET-vs-Python 620MB mix-up corrected. This is a separate
  static site outside this repository, kept in sync manually going forward.
- [x] README.md, this page, `docs/LANGUAGE_PARITY.md`, and the docs site
  (`examples/web-viewer/docs/index.html`) swept for C++ staleness after this
  round's C++ work (Fragments export, multi-dict attributes, 4 IFC fixes,
  legacy pages/scenes, construction lines/points, attribute-dictionary
  GLB/JSON metadata export) — `openskp.com` still needs the same pass
  manually, since it isn't in this repository.
- [x] README.md, this page, `docs/LANGUAGE_PARITY.md`, and
  `docs/DEVELOPER_GUIDE.md` swept after TypeScript's Fragments export
  ([#276](https://github.com/iamahsanmehmood/openskp/pull/276), merged —
  was showing as an open, CI-failing PR) and the legacy pre-2014
  instance/group GUID fix
  ([#310](https://github.com/iamahsanmehmood/openskp/pull/310), fixed the
  V7/V8/2013 file-version-support gap) — `openskp.com` still needs its own
  manual pass for both, not done here.

---

## Reporting something

Bug reports and feature requests both go through
[GitHub Issues](https://github.com/iamahsanmehmood/openskp/issues/new/choose).
If you hit a language-parity gap not listed above, please file it — this page
and `docs/LANGUAGE_PARITY.md` are only as accurate as what's been reported.
