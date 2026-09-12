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

| Language | Latest release | Status |
|:---|:---|:---|
| Python | [1.2.0 on PyPI](https://pypi.org/project/openskp/) · [1.3.0 (preview) on GitHub](https://github.com/iamahsanmehmood/openskp/releases/tag/preview-python-v1.3.2) (not on PyPI yet) | Ahead of the other 4 — see [Cross-language porting backlog](#cross-language-porting-backlog) below |
| TypeScript | [npm](https://www.npmjs.com/package/openskp) | At 1.2.0 parity, plus an unreleased writer memory fix — see [CHANGELOG.md](CHANGELOG.md) |
| .NET | [NuGet](https://www.nuget.org/packages/OpenSkp) | At 1.2.0 parity |
| Dart | [pub.dev](https://pub.dev/packages/openskp) | At 1.2.0 parity |
| C++ | [GitHub releases](https://github.com/iamahsanmehmood/openskp/releases?q=cpp-) · [1.3.0 (preview) on GitHub](https://github.com/iamahsanmehmood/openskp/releases/tag/preview-cpp-v1.3.1) | Closed most of the gap with Python this round — Fragments export, multi-dictionary attributes, 4 IFC export fixes, legacy pages/scenes reading, construction lines/points reading, and attribute-dictionary GLB/JSON metadata export all now shipped on a preview tag. Still behind on full 9-value-type attribute support and the writer's dimensions/section-planes/construction-geometry — see below |

Each language is released independently — see
[CONTRIBUTING.md § Releasing](CONTRIBUTING.md#releasing-maintainers) for why,
and why version numbers across languages can legitimately diverge rather
than always moving in lockstep.

### Why Python 1.3.0 isn't on PyPI yet

It's a real, fully tested release (538 tests passing, lint clean) — headlined
by direct SketchUp → ThatOpen Fragments (`.frag`) export, a new capability
that was Python-only when 1.3.0 first shipped and is now also in C++ (see
the table above). Rather than publish a PyPI version that's immediately out
of parity with the rest of the project, it's tagged on GitHub only,
installable by pinning the tag directly:

```bash
pip install "openskp[fragments] @ git+https://github.com/iamahsanmehmood/openskp.git@preview-python-v1.3.2#subdirectory=packages/python"
```

It folds into a proper PyPI `1.3.0` once the items below catch up. Likewise,
C++'s own preview tag (`preview-cpp-v1.3.1`) folds into a numbered
`cpp-v1.3.0` release once its own remaining gaps close — see
[CHANGELOG.md](CHANGELOG.md) and [docs/LANGUAGE_PARITY.md](docs/LANGUAGE_PARITY.md).

---

## Cross-language porting backlog

Tracked in full, with checkboxes, in
[issue #285](https://github.com/iamahsanmehmood/openskp/issues/285) — one
line per feature, not one issue per language per feature. Summary:

- Attribute dictionaries: full 9-value-type support — Python only today; C++
  decodes values as strings only (multiple dictionaries per entity itself is
  now also in C++, see below).
- Writer: linear dimensions + leader text, section planes, construction
  lines/points — Python only; TypeScript/.NET/Dart/C++ writers don't have
  these entity types yet.
- Legacy (pre-2021) pages/scenes reading — Python and C++ (GitHub-only
  preview tag); TypeScript/.NET/Dart only read pages/scenes from VFF
  (2021+) files.
- Direct SketchUp → Fragments (`.frag`) export — Python and C++ (both
  GitHub-only preview tags; C++ measured 5-9x faster end to end on real
  files). A community TypeScript port is open
  ([PR #276](https://github.com/iamahsanmehmood/openskp/pull/276)) but its
  required CI check is currently failing. .NET and Dart have no work
  started.
- VFF per-layer-hidden flag reading — Python and C++ (both GitHub-only).
- 4 IFC export correctness fixes (units/axis, real instance names + layer
  visibility, plugin-attribute property sets, full-path classification) —
  Python and C++ (both GitHub-only); not yet checked whether TypeScript/
  .NET/Dart's IFC exporters have equivalent issues.
- Attribute dictionaries surfaced in GLB/JSON metadata export, not just IFC
  Psets — Python and C++ (both GitHub-only); every attribute dictionary an
  instance carries was already resolved internally in both languages but
  never reached export until this fix. TypeScript/.NET/Dart don't have an
  `attribute_dictionaries` field at all yet.
- Construction lines/points reading — Python (all files) and C++ (legacy
  files only, GitHub-only); TypeScript/.NET/Dart have neither.
- earcut-based triangulation (replacing Shapely, with a real concave-face
  correctness fix) — Python only; other languages use their own
  triangulators, not independently checked for the same fidelity bug.
- Large-file parser fix ([#264](https://github.com/iamahsanmehmood/openskp/issues/264)) — Python only; each other language has its own independent parser, not assumed fixed.

Smaller, related items also in that same tracking issue: codegen not
round-tripping `applied_width`/opacity (affects all 5 languages), a .NET test
that soft-skips instead of reporting skipped, and an unexplained .NET/Python
triangle-count divergence on one real file. C++'s `model.layers` ordering and
`mesh_index[...].properties`/`.attribute_dictionaries` gap mentioned in
earlier versions of this page is now fixed — see
[docs/LANGUAGE_PARITY.md](docs/LANGUAGE_PARITY.md).

**Good first contribution?** Most of these have a real, SDK-verified Python
implementation to port from — the hard research is already done. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the workflow.

---

## Known bugs

### Legacy (pre-VFF) parser: some real old-format files fail to parse

Tracked in [issue #284](https://github.com/iamahsanmehmood/openskp/issues/284).
A real-world version-compatibility sweep (the same project saved across
SketchUp versions 3 through 2025) found 8 of 14 files parse and convert
cleanly; 6 fail, most sharing one root cause narrowed down to a slot-collision
bug inside the legacy MFC reader, not yet fixed. Stated here plainly rather
than glossed over — this is a real gap in the classic MFC `CArchive` support
some corners of the README describe as "full."

### Fragments export: no native visibility field

The public ThatOpen Fragments schema has no field for per-layer visibility at
all. OpenSKP carries it via a `Model.metadata` JSON sidecar — a convention
this project defined, not part of the public schema. Any other tool reading
the same `.frag` file needs to know to look there. Not really "fixable" (it's
a property of the format), but worth knowing before building against it.

---

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

---

## Reporting something

Bug reports and feature requests both go through
[GitHub Issues](https://github.com/iamahsanmehmood/openskp/issues/new/choose).
If you hit a language-parity gap not listed above, please file it — this page
and `docs/LANGUAGE_PARITY.md` are only as accurate as what's been reported.
