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

All 5 languages released **1.3.0** together on 2026-09-18 — same version
number, not identical capability. See
[CHANGELOG.md § 1.3.0](CHANGELOG.md) for what shipped and why Python/C++
carry a bit more than the others rather than holding real, tested work
back purely for symmetry.

| Language | Latest release | Status |
|:---|:---|:---|
| Python | [1.3.0 on PyPI](https://pypi.org/project/openskp/) | Ahead of the other 4 on a few items — see [Cross-language porting backlog](#cross-language-porting-backlog) below |
| TypeScript | [1.3.0 on npm](https://www.npmjs.com/package/openskp) | At parity except the backlog items below |
| .NET | [1.3.0 on NuGet](https://www.nuget.org/packages/OpenSkp) | At parity except the backlog items below |
| Dart | [1.3.0 on pub.dev](https://pub.dev/packages/openskp) | At parity except the backlog items below |
| C++ | [1.3.0 on GitHub releases](https://github.com/iamahsanmehmood/openskp/releases?q=cpp-) | Closest to Python — only missing Fragments reading, loose-edge/curve support, and IFC curve annotations |

Each language is released independently — see
[CONTRIBUTING.md § Releasing](CONTRIBUTING.md#releasing-maintainers) for
the tag format per language.

---

## Cross-language porting backlog

Tracked in full, with checkboxes, in
[issue #285](https://github.com/iamahsanmehmood/openskp/issues/285) — one
line per feature, not one issue per language per feature. What's actually
still open after 1.3.0 (most of what used to be listed here is now
shipped everywhere — see [docs/LANGUAGE_PARITY.md](docs/LANGUAGE_PARITY.md)
for the full, current matrix):

- ~~Reading a `.frag` file back~~ — **done in all 5 languages** (Python,
  TypeScript #362, .NET #363, Dart #364, C++ #365). The last item from
  the original 1.3.0 gap list; found and fixed two real, previously-
  undetected write-side bugs along the way (Dart's material-vector
  alignment padding, C++'s dangling-reference JSON parser) - see
  [CHANGELOG.md](CHANGELOG.md).
- Loose-edge/curve support in `build_instanced_scene()`, plus the public
  typed model's `Edge.layer`/`Edge.curve_id`/`Face.layer`/
  `loose_edge_runs()` — Python only.
- IFC export: loose-edge curve sets + analytic arcs as
  `IfcAnnotation`/`IfcIndexedPolyCurve`, plus 4 IFC4 STEP-conformance
  fixes — Python only.
- Legacy (pre-2021) pages/scenes reading — Python and C++; TypeScript/
  .NET/Dart only read pages/scenes from VFF (2021+) files.
- Per-edge/per-face layer, `Edge#curve` pointers, classic `CArcCurve`
  frames — Python only.
- Attribute dictionaries: full 9-value-type support for VFF (2021+) format
  — Python (7/9) only; not independently re-checked this round for the
  other 4 languages, so treat as needing a fresh look rather than a
  confirmed gap.

**Good first contribution?** Most of these have a real, SDK-verified Python
implementation to port from — the hard research is already done. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the workflow.

**Good first contribution?** Most of these have a real, SDK-verified Python
implementation to port from — the hard research is already done. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the workflow.

---

## Known bugs

### Legacy (pre-VFF) parser: some real old-format files fail to parse

Tracked in [issue #284](https://github.com/iamahsanmehmood/openskp/issues/284).
A real-world version-compatibility sweep (the same project saved across
SketchUp versions 3 through 2025, 15 usable files) found 11 of 15 files
parse and convert cleanly; 4 fail, each with its own distinct, still-open
error signature (V3, V4, V6, 2019) — see
[docs/LANGUAGE_PARITY.md § 4](docs/LANGUAGE_PARITY.md#4-real-sketchup-file-version-support)
for the full table. The three files that used to share one root cause (V7,
V8, 2013 — a `class-ref to non-class slot N (CAttributeNamed)`
slot-collision) are fixed as of
[#310](https://github.com/iamahsanmehmood/openskp/pull/310): the trailing
instance/group GUID read was gated on the class's own schema number, which
doesn't actually track GUID presence across versions — fixed by gating on
the file's real version instead. Stated here plainly rather than glossed
over — the remaining 4 are a real gap in the classic MFC `CArchive` support
some corners of the README describe as "full."

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
