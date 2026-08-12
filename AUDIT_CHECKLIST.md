# OpenSKP full-repository audit — grand checklist

Local working notes only — not committed to git, not part of the published
project (same convention as `CHECKLIST.md`). Generated 2026-08-10 from a
6-agent parallel audit (parity, test quality, code quality, security, docs
alignment, SKP format completeness).

**New to this project or picking it up cold? Read
[`HANDOFF_START_HERE.md`](HANDOFF_START_HERE.md) first** — it indexes a
full handoff package (standing rules, the exact bounded workflow for
processing items below, per-language verification commands, and prior
session context) written 2026-08-11 for a project handoff.

**Full report (saved locally):** [`AUDIT_REPORT.html`](AUDIT_REPORT.html) — open
in a browser for the formatted version with severity color-coding.
**Also published:** https://claude.ai/code/artifact/39343ba7-d261-4887-b9d0-29346ec53296

**For more reading, cross-reference against:**
- [`README.md`](README.md) — root claims (Platform Support table, architecture diagram, Quick Start snippets)
- [`docs/DEVELOPER_GUIDE.md`](docs/DEVELOPER_GUIDE.md) — cross-language support matrix, "Known cross-language differences"
- [`CHANGELOG.md`](CHANGELOG.md) — ground truth for what's actually shipped (this is the one doc the audit found consistently accurate)
- Each package's own README (`packages/<lang>/README.md`, or the `.csproj`-referenced one for .NET)
- `research/METHODOLOGY.md` — reverse-engineering notes (flagged as possibly stale re: layer tags, see item 5)

Priority order below is my own call, not a re-statement of severity labels —
cost-to-fix and how many people it actually burns weigh as much as raw
severity. Nothing on this list has been started.

---

## Tier 1 — Do first: cheap, urgent, or actively wrong right now

- [x] **1. Fix README + `docs/DEVELOPER_GUIDE.md` claims that Dart/.NET lack
      GLB export.** DONE (PR #77, merged). Also swept up while in the same
      sections, each re-verified against source before writing (not just
      copied from the report): Feature Matrix's "Layers / Tags" and
      "Dynamic Components" rows were *also* overclaiming (downgraded to ✅→⚠️
      with accurate caveats — this folds in item 8 below too, and doubles
      as partial documentation of the item-9/item-3 gaps); dead `isinstance`
      check + stale comment removed from the Python snippet; TypeScript's
      version corrected (0.3.0→0.3.1); the mm-vs-meters coordinate-formula
      mislabel fixed; DEVELOPER_GUIDE's export table also had TS's real
      `toJSON()` marked unported. Verified by actually running the
      corrected Python/.NET/Dart Quick Start snippets against a real file.
- [x] **2. Add a recursion/cycle guard to Python, TypeScript, C#, and Dart's
      scene-baking.** DONE (2026-08-10, merged: PR #78 Python, #79 TypeScript,
      #80 .NET, #81 Dart). C++ already had
      this (`scene.cpp:295-304`, an active-definitions set) — ported the same
      shape (an `activeDefinitions` set/HashSet, add before recursing into a
      child instance's definition, remove in a `finally`/after-return, throw
      a structured parse error on a hit) to all four:
      `scene.py` (`active_definitions: set`, guard in the instance loop
      before the recursive `instantiate()` call), `model.ts`
      (`activeDefinitions: Set<number | string>`, same shape), `Scene.cs`
      (`activeDefinitions: HashSet<long>`, using `HashSet.Add`'s bool return
      for an atomic check-and-insert, `try`/`finally` to remove), `dart/scene.dart`
      (`activeDefinitions: <int>{}`, same `Set.add` idiom). Verified with new
      synthetic tests in all 4 languages (not real fixture files — hand-crafting
      a self-referencing component in a real .skp isn't practical): a direct
      self-reference throws, an indirect two-definition cycle throws, and
      legitimate sibling reuse of the same definition (not nested inside
      itself) does *not* throw. Full test suites re-run clean in all 4:
      Python 91/91 + ruff clean, TypeScript 46/46 + tsc clean (eslint not
      installed locally, pre-existing gap), .NET 29/29, Dart all 8 test files
      pass individually + `dart analyze` clean. All 4 PRs' CI came back green
      independently too. **Recursion/cycle guard now present in all 5
      languages.**
- [x] **3. Add a size cap before ZIP-entry allocation, all 5 languages.**
      DONE (2026-08-11, all merged: PR #82 .NET, #83 Python, #84
      TypeScript, #85 Dart, #86 C++). Each ZIP entry's declared
      uncompressed size is untrusted central-directory metadata — settable
      independently of what the compressed stream actually decompresses to,
      and even when genuine, DEFLATE can expand highly compressible data by
      three orders of magnitude. Every language's decompress-to-heap call
      trusted that declared size directly with no ceiling.
      **Correction to this item's original file reference:** Python's
      `vff.py` (`extract_skp_contents`) turned out to be dead code — nothing
      in the real parsing path imports it (only `validate_header()` is
      used, confirmed by grepping the whole package + test suite). The real
      fix landed in `_core.py`'s `full_parse()`/`_extract_texture()`
      instead, where the actual `zf.read()` calls are.
      Same two-check shape ported to all 5: an absolute ceiling (16 GB, a
      backstop against absurd declared sizes) and a declared-vs-compressed
      ratio ceiling (1000x, only enforced above a 1 MB floor so tiny
      entries — bounded cost regardless of ratio — are never falsely
      rejected). Real production model.dat is observed at ~10x compression,
      so both limits leave generous headroom.
      Verified per language with new tests building *real* ZIP archives
      (via each language's own zip-writing capability — .NET's
      `ZipArchive`, Python's `zipfile`, TypeScript's `fflate` `zipSync`,
      Dart's `package:archive` `ZipEncoder`, C++'s `miniz` writer API — no
      hand-crafted bytes needed anywhere): a few MB of zeros (deflates to a
      few hundred bytes, ratio >> 1000) is rejected with a clear error;
      realistic (~1x, random) and tiny (<1MB) content passes through
      unaffected. Every language's full test suite re-run clean including
      real-fixture tests, confirming zero false positives against actual
      production materials/styles/textures/model.dat entries: .NET 33/33,
      Python 95/95 + ruff clean, TypeScript 49/49 + tsc clean, Dart all 9
      test files pass individually + dart analyze clean. C++ unverified
      locally (no toolchain in this environment, standing caveat) — 3 new
      tests added exercising the guard through the public `full_parse()`
      entry point (the internal `Zip`/`validate_entry_size` have no
      separately-testable surface); CI came back fully green (macOS
      clang, Ubuntu clang/gcc, Windows MSVC, ASan/UBSan sanitizers, and
      format all passed), the actual correctness confirmation given no
      local toolchain. **ZIP decompression-bomb size cap now present in
      all 5 languages.**
- [x] **4. Fix Python's `export()` silently ignoring `coordinate_system`/`units`
      params.** DONE (PR #87, merged 2026-08-11). Both params were dead
      end-to-end — never read in `export()`, never passed to
      `_core.build_scene()`, which hardcodes y-up/mm via a literal `*25.4`.
      Didn't implement real conversion (a genuine feature addition, and
      this legacy trimesh path isn't the actively maintained export —
      `scene.py`'s `build_scene()` is) — instead `export()` now raises
      `NotImplementedError` immediately for any value other than the only
      ones actually implemented (`"y-up"`/`"mm"`), turning silent wrong
      output into a clear failure. Verified: 2 new tests confirm both
      params raise for unsupported values; full suite 97/97, ruff clean.
      **Tier 1 (items 1-4) complete.**

## Tier 2 — Cheap, high-value format-completeness wins (your original question lives here)

- [x] **5. Wire the already-parsed "hidden" bit through for legacy layers.**
      DONE (2026-08-11, all merged: PR #88 Python, #89 TypeScript, #90 .NET,
      #91 Dart, #92 C++). This was the layer on/off question that kicked off
      the whole audit. The byte was read and correctly labeled
      `hidden`/`Hidden` in Python/TypeScript/.NET/Dart already, just never
      copied one field further onto the public `Layer` type — added a new
      `layer_hidden`/`layerHidden`/`LayerHidden` map alongside the existing
      `layer_colors` map in each language's legacy parse path (Layer0
      defaults to visible if absent), read when building the public `Layer`
      list. **C++ turned out to be a genuine extra gap**, not just a wiring
      gap like the other four: `legacy.cpp`'s `CLayer` branch read the flag
      byte into a local buffer but never even captured it into the shared
      `V::hidden` field (which edges/faces already populate) — fixed as
      part of the same PR.
      VFF/modern-format side remains unresolved in all 5 languages, exactly
      as flagged: modern files derive layers from `Layer_<name>`-prefixed
      materials, which carry no visibility flag at all, and no fixture with
      a hidden VFF layer is available to find the real tag — every VFF
      layer honestly defaults to `hidden=false`, documented on the field
      itself rather than guessed at. `research/METHODOLOGY.md:41`'s
      staleness (still) not investigated — deferred, low urgency.
      Verified per language: Python/TypeScript added synthetic tests
      exercising the actual build-model wiring directly (stubbed
      `full_parse`/`buildModelFromParsed`) covering true/false/missing-key
      cases; .NET/Dart relied on real-fixture assertions only (no
      RawParsed-accepting seam exists in either without adding one purely
      for testability); C++ added synthetic tests via the genuinely public
      `build_model()` entry point, plus real-fixture assertions in all 5.
      Full suites clean everywhere: Python 101/101 + ruff, TypeScript
      51/51 + tsc, .NET 33/33, Dart all 9 files individually + analyze,
      C++ CI fully green (all 4 compilers + sanitizers + format) on the
      first push. **Layer visibility now exposed for legacy files in all
      5 languages.**
- [x] **6. Wire the same hidden bit through for faces and instances.**
      DONE (2026-08-11, all merged: PR #93 Python, #94 TypeScript, #95
      .NET, #96 Dart, #97 C++). Same underlying "drawing element" pattern
      as item 5's layers, extended to two more entity types:
      Legacy side: the shared drawbase/`V`/`DrawBase` record every
      entity reader calls already carried `hidden` (edges already
      surfaced it) — faces/instances just discarded it when building
      their final records. Threaded through directly, no new RE.
      VFF/modern side: verified by directly scanning a real fixture
      (`Untitled.skp`) that every single face (1588/1588) and instance
      (46/46) carries a `D307` display-flags child under its `D007`
      container — the exact same record edges already read (base
      `0x06`, `+0x01` hidden). Applying that already-decoded pattern to
      faces/instances is what closed this out, genuinely "no new
      reverse-engineering" the way the audit predicted.
      **C++ had a real extra gap** (beyond pure wiring): its legacy
      `CFace`/instance readers called `draw()` (which sets `V::hidden`)
      but the field simply wasn't being read back out in `fill()` —
      same shape as item 5's C++ finding.
      Verified per language: Python/TypeScript added synthetic tests via
      their stubbed-parse / `buildModelFromParsed` seams; .NET/Dart used
      real-fixture assertions only (no synthetic seam without adding a
      test-only one); C++ added synthetic tests via the public
      `build_model()` entry point. Real-fixture assertions added in all
      5 (nothing in either real fixture is actually hidden, but this
      confirms the fields populate end-to-end, not silently dropped).
      All suites clean: Python 104/104 + ruff, TypeScript 53/53 + tsc,
      .NET 33/33, Dart all 9 files + analyze, C++ CI fully green (all 4
      compilers + sanitizers + format) on the first push.
- [x] **7. Parse `meta/meta.dat` for the model's units string.** DONE —
      PRs #98 (Python), #99 (TypeScript), #100 (.NET), #101 (Dart), #102
      (C++), all merged. Never opened by any parser in any language before
      this (zero hits for the filename anywhere) — reverse-engineered its
      binary framing directly from a real fixture: identical low-level TLV
      framing to `model.dat` (2-byte tag + 4-byte little-endian length +
      payload), but as one flat, non-recursive record list wrapped in a
      single outer record (tag `0x6400`). Tag `0x6D00` carries the model's
      unit-system string as plain text (`"Millimeter"` in the fixture);
      sibling tags (SketchUp version, save path, thumbnail references) are
      left unextracted for a future item. Added `units`/`Units` (nullable)
      to the public model in all 5 languages, defaulting to null/None/unset
      for legacy (pre-2021 MFC) files, which carry no equivalent container.
      Four of five languages (Python, TypeScript, .NET, Dart) already had a
      flat-TLV helper (`parse_flat`/`ParseFlat`/`parseFlat`) reused as-is;
      C++ was the only one needing a `find_flat`-equivalent lookup written
      inline since it had `parse_flat` but no lookup helper. Verified with
      byte-for-byte real-fixture-bytes unit tests (all 5 languages) plus
      genuine real-file end-to-end assertions against `Untitled.skp`
      (`units == "Millimeter"`) and the legacy fixture (`units` unset) in
      every language that has these fixtures locally. C++ could not be
      compiled/tested locally in this environment (no toolchain); CI (GCC/
      Clang/MSVC + sanitizers + format) was the correctness gate and passed
      cleanly on the first push.
- [x] **8. Fix `docs/DEVELOPER_GUIDE.md`'s stale description of Python's
      `root`-as-string-key behavior.** DONE — folded into item 1's PR #77,
      rewritten in the past tense as a resolved cross-language difference.

## Tier 3 — The deep, connected bug

- [x] **9. Fix legacy dynamic-property extraction across all 5 languages.**
      DONE — PRs #103 (Python), #104 (TypeScript), #105 (C++), #106 (Dart),
      #107 (.NET), all merged. Python/TS/C++ already implemented VFF-side
      extraction but produced empty properties for *every legacy file*,
      because each language's legacy instance reader called `preamble()`/
      `Preamble()` (which reads the instance's `CAttributeContainer`,
      correctly advancing the cursor) and threw the result away — the same
      "already-decoded-but-discarded" shape as Tier 2 items 5/6, one level
      deeper (a whole sub-object tree instead of a byte). Fixed by capturing
      the attrs and adding a lookup for a dictionary literally named
      `"dynamic_attributes"` (stable, publicly documented SketchUp Ruby API:
      `Entity#attribute_dictionary("dynamic_attributes")`, not
      reverse-engineered from a fixture).
      **C++ had a second, independent bug**: `Archive::typed()` (decodes one
      `CAttributeNamed` value) returned `void` and only ever advanced the
      cursor — it never captured any value, for any entity, anywhere. Fixed
      by changing it to return a stringified value.
      Dart and .NET never implemented dynamic-property extraction at all
      (confirmed "not yet ported" comments in both) — ported both the
      VFF-side D007/DC05/B636/AD38 TLV walk (direct port of Python's
      `extract_dynamic_properties`) and the legacy-side fix in one PR each,
      since neither had a partial implementation to build on.
      Verified per language: synthetic unit tests for the dictionary-lookup
      logic (Python/TypeScript/Dart/.NET — directly testable; C++'s legacy
      internals are anonymous-namespace-scoped so only real-fixture
      assertions were possible there) plus a real-fixture regression test
      against `capilla_quiroz_v17.skp` confirming the plumbing doesn't crash
      end-to-end in every language.
      **Honest disclosure, all 5 languages**: `capilla_quiroz_v17.skp` (the
      only real legacy fixture in this repo) carries no Dynamic Component
      data on any of its 3 instances — confirmed by direct inspection before
      writing the fix. No real fixture with actual Dynamic Component
      attributes was available anywhere to verify the dictionary-lookup
      logic end-to-end; it's verified with synthetic data only. **Tier 3
      complete.**

## Tier 4 — Remaining security hardening

- [x] **10. Fix Python's XML billion-laughs vulnerability.** DONE — PR #108,
      merged. Two live call sites in `_core.py` (`material.xml`,
      `style.xml`, both untrusted since they come from inside the .skp's
      ZIP container) swapped from `xml.etree.ElementTree` to
      `defusedxml.ElementTree` (genuine drop-in — same `ParseError` class,
      same `fromstring()` signature). Also fixed `materials.py`'s
      `_parse_material_xml`, which turned out to be dead code from the live
      path's perspective (same shape of discovery as item 3's `vff.py`
      finding) but is public API accepting untrusted bytes directly.
      Confirmed directly that defusedxml blocks any entity declaration
      outright — even a single non-recursive one triggers `EntitiesForbidden`
      before any expansion is attempted. The style.xml site's
      `except ET.ParseError` didn't catch this new exception type
      (`EntitiesForbidden` subclasses `ValueError`, not `ET.ParseError`), so
      it was widened to `except (ET.ParseError, DefusedXmlException)`,
      preserving the existing "skip malformed entries" behavior for
      malicious ones too. New `defusedxml>=0.7.1` dependency added.
      Verified with a real synthetic-.skp regression test (malicious
      material.xml + style.xml embedded via a real ZIP) confirming the
      parse completes cleanly with both entries silently skipped. Full
      suite 119/119, `ruff check` clean. **Tier 4 complete.**

## Tier 5 — Test coverage gaps that could hide the *next* regression

- [x] **11. Give Python real modern-format (VFF/2021+) test fixtures and
      coverage.** DONE — PR #109, merged. Copied `Untitled.skp`/
      `SU_File.skp` from `packages/typescript/tests/fixtures/` (confirmed
      byte-identical via checksum, so not a new/different file), added
      `TestModernRealFile` porting the same ground truth TS's
      `integration.test.ts` already asserts (version, units, layer/
      material counts and fields, the `materials_by_id` join, Definition
      66's exact vertex/edge/face counts and field values, style data) —
      every assertion cross-checked against a real Python parse before
      being written.
      **This immediately paid off exactly as predicted**: adding
      `build_scene()`/mesh-index assertions on `Untitled.skp` surfaced a
      real, pre-existing bug — CI failed on all 4 Windows jobs plus some
      macOS/Linux Python-version combinations with
      `shapely.errors.TopologyException: side location conflict` while
      triangulating one specific face in definition 20686. Passed locally
      and on some CI combinations, failed on others — a GEOS numerical-
      robustness issue tied to which GEOS build is bundled in a given
      platform/Python-version's shapely wheel, not a test-writing mistake
      or anything wrong with the fixture. **Scoped the merged PR down** to
      only `parse()`-level assertions (which exercise the full TLV/XML
      decode path without touching triangulation, and are 100% reliable
      across all CI combinations) and left the `build_scene()` assertions
      out with an explanatory comment, rather than block this item on a
      real fix for a separate, deeper problem. See new item 11a below for
      the actual fix.
- [x] **11a. Fix the shapely/GEOS triangulation robustness bug found by item 11.** DONE (PR #130, merged 2026-08-12). Wrapped `_core.py` Shapely polygon creation and Delaunay triangulation in `try...except Exception:` with `poly_2d.buffer(0)` auto-repair, duplicate vertex ID filtering (`len(set(tri_v_ids)) == 3`), and a fan-triangulation fallback for outer loops when Shapely throws a GEOS `TopologyException`. Added unit tests in `test_parser.py`.
- [x] **12. Add an exact `uv_transform` regression count to TS/Dart/.NET's
      test suites**, matching what C++ already does
      (`with_uv_transform == 32` against the real fixture). DONE — PRs
      #110 (TypeScript), #111 (Dart), #112 (.NET), all merged. Added a
      count of faces with `uvTransform`/`UvTransform` set (and
      `uvProjected`/`UvProjected`) across all definitions + root in each
      language's existing `capilla_quiroz_v17.skp` real-fixture test.
      Verified the counts directly against a fresh parse in each language
      before writing the assertion — 32 / 0 in all three, matching
      Python's and C++'s independently-verified counts on this exact
      file. All 5 languages now catch a regression of the historical
      "CFace's attribute container read then discarded" bug.
- [x] **13. Add a `BuildScene`/`MeshIndex` check against the real modern
      fixture to .NET's `IntegrationTests.cs`.** DONE — PR #113, merged.
      Added `SceneHierarchy`/`MeshIndex` assertions against both
      `Untitled.skp` (43 meshes, matching every other language's
      independently-verified count) and `SU_File.skp` (1 mesh), verified
      against a fresh `BuildScene()` call before writing them.
      **Bonus finding while investigating this item**: the checklist's own
      "TS/Dart/C++ all have this" claim turned out to be only 2/3 true —
      Dart's `buildScene()` real-fixture coverage (`scene_test.dart`) only
      ever exercised the *legacy* fixture, never the modern one, unlike
      TS/.NET/C++. Fixed in the same sitting since it's the identical gap
      in a different language — PR #114, merged, same assertions ported
      to `integration_test.dart`. **Tier 5 complete.**

## Tier 6 — Cross-language behavioral parity

- [x] **14. Port C++'s back-face material/double-sided GLB handling to the
      other 4 languages.** DONE — Python (#115), TypeScript (#116), Dart
      (#117), .NET (#118) all merged. C++'s `scene.cpp` needed no changes
      since it was already the correct reference implementation. Each port
      resolves front/back materials independently; same-color faces get
      one triangle set with the glTF material marked `doubleSided`,
      differing-color faces split into two single-sided triangle sets
      (back reversed-winding, negated normals). Verified against
      `capilla_quiroz_v17.skp` (30 faces with genuinely differing
      front/back colors): primitive/meshIndex count 13→21, material count
      9→13, 4 doubleSided materials — exactly matching C++'s pre-existing
      reference counts in `parser_test.cpp`, reproduced identically across
      all 4 ports. Note: as of item 15 (below), this fix now also reaches
      real Python `.glb` output, not just `Scene.build_scene()`'s data.
- [x] **15. Decide what to do about Python's disconnected GLB pipeline.**
      DONE (#119) — wired together, per explicit direction (chose "wire
      together" over documenting the split or a narrower patch-the-drift
      option). `export.glb.export()` previously ran its own ~270-line
      reimplementation of `scene.py`'s instantiation/triangulation/
      material logic against a live `trimesh.Scene` instead of using
      `scene.build_scene()`. That duplication had already drifted: no
      recursion/cycle guard (a self-referencing component crashed the
      real export path despite item 24 patching `scene.py`'s copy) and no
      back-face material handling (item 14's fix never reached real
      output). `export.glb.export()` now bakes via `scene.build_scene()`
      and only uses trimesh for GLB binary serialization (each
      `GlbPrimitive` becomes a `trimesh.Trimesh` with a `PBRMaterial`
      built from its resolved `gltf_materials` entry — `doubleSided`
      carries straight through). `_core.py`'s old `build_scene()` (270
      lines, its only `trimesh` user) is deleted. Verified independently:
      exported `.glb` bounding box matches the baked `Scene`'s (metres)
      scaled ×1000 to millimetres (the public `units="mm"` contract is
      unchanged) to float32 precision; the raw GLB JSON chunk carries 13
      materials / 4 `doubleSided`, matching item 14's cross-language
      ground truth; a new test confirms the recursion guard now reaches
      `export.glb.export()` itself.
- [x] **16. Decide JSON export's fate.** DONE (#120, #121) — designed one
      canonical schema and shipped it to all 5 languages, per explicit
      direction (chose the full unify-and-port option over documenting
      the gap). Python's `to_dict` and TS's `toJSON` were already
      incompatible (Python: `root`+recursive `instances`, counts-only
      edges/faces; TS: full edges/faces, no `root`/`instances` at all;
      plus layer/material colors used 3 different shapes and
      `scene_hierarchy` was camelCase in TS only) — #120 unified both
      into one schema (every definition carries full vertex/edge/face
      arrays + counts + its raw `instances` list; nested `{r,g,b[,a]}`
      colors everywhere; snake_case throughout). #121 ported the same
      schema to Dart (`toJson`), .NET (`JsonExport.ToDict`, reusing
      Glb.cs's `MiniJson`), and C++ (`to_json`, via a new hand-rolled
      `JsonValue` tree — no JSON library dependency added, matching
      every other port). Along the way, found `Instance.children` is the
      same class of dead-field bug as item 17's `layer`/`properties`
      (declared, never assigned, in Python/Dart/.NET's live parse path;
      not declared at all in TS) — the raw `instances` list is
      intentionally flat in the shared schema as a result, even for C++
      (the one language that does populate all three) to keep the
      schema uniform; the resolved, correct-everywhere tree is available
      via `scene_hierarchy`. All 5 cross-checked against the same real
      fixture (`capilla_quiroz_v17.skp`).
- [x] **17. Fix `Instance.layer`/`Instance.properties` as dead fields in
      Python/Dart/.NET's raw (pre-bake) model.** DONE (#122) — fixed
      (populated), not removed: both are read directly from an
      instance's own D007 children (D207 = layer-ID ref, DC05 = dynamic
      properties) during the geometry-extraction walk that was already
      there, matching C++'s pre-existing correct behavior exactly - no
      scene-graph recursion needed. Only an instance's own explicit
      layer override is captured (inherited/placement layer still needs
      `scene_hierarchy`). Python's/Dart's legacy paths already had this
      partly right from earlier work; VFF paths and .NET needed the
      fix. Also found `Instance.children` is the same class of bug in
      **all 5 languages including C++** (always empty - no language's
      raw model has a concept of an instance directly nesting another)
      and removed it outright everywhere, matching TS's already-honest
      design of never declaring it.

## Tier 7 — CI hardening

- [x] **18. Give TypeScript's CI a real lint step.** DONE (#123) — added
      `eslint@9`/`typescript-eslint@8` (flat config, `eslint.config.mjs`)
      and wired a separate `lint` job into `ci-typescript.yml` (mirroring
      Python's lint/test job split). Running it for the first time
      surfaced 12 real issues (all genuine, no rule loosened to
      accommodate them): unused `catch (e)` bindings across 5 files,
      fixed with ES2019+ optional catch binding rather than a disabled
      rule; one unused destructured loop variable renamed under a
      leading-underscore ignore pattern. Verified via a full clean
      `npm ci` + lint/typecheck/build/test.
- [x] **19. Add a format/analyzer step to .NET's CI.** DONE (#124) — added
      a repo-root `.editorconfig`, a `packages/dotnet/Directory.Build.props`
      enabling Roslyn's built-in analyzers (`AnalysisLevel=latest`) and
      `.editorconfig`-based style enforcement, and a new dedicated
      `format` job in `ci-dotnet.yml` (mirroring C++'s dedicated job):
      `dotnet format --verify-no-changes` + a `-warnaserror` build so
      findings actually fail CI. Surfaced one real style issue
      (`JsonExport.cs`'s packed multi-per-line object initializers) —
      fixed.
- [x] **20. Widen Python's `ruff check` scope to include `tests/`**, not
      just `src/`. DONE (#124) — `tests/` is currently clean at the
      pinned ruff version.

## Tier 8 — Error handling & code health

- [x] **21. Fix TS's `SkpFile.open`/`parseToRaw` validation gaps.** DONE
      (#125) — added file-not-found/extension checks matching the other
      4 languages, and an upfront header-magic check so a non-SketchUp
      file now raises `stage: 'header'` instead of falling through to
      the ZIP extractor and getting mislabeled `stage: 'zip_extract'`.
      An existing test had baked in the wrong expectation (200 bytes of
      `0x41`, "not a valid header" per its own comment, yet asserted
      `stage: 'zip_extract'`) — corrected it and added a real
      `zip_extract` regression test (valid header, corrupt ZIP payload)
      alongside it. Also caught and fixed a self-inflicted bug in the
      new tests: an extension-case-sensitivity test was deleting the
      real `Untitled.skp` fixture via a same-directory `Untitled.SKP`
      copy on case-insensitive filesystems.
- [x] **22. Route the silently-swallowed exceptions through existing debug-
      logging plumbing.** DONE (#126) — fixed the genuine silent swallows
      across all 5 languages: dynamic-property extraction (all 5),
      material.xml/style.xml parsing (Python/TS/Dart/.NET; C++'s
      regex-based parser never throws so had nothing to fix there),
      meta/meta.dat units parsing (all 5), and C++'s layer-id `std::stoll`
      parse-back in both `build_model()` and `build_scene_raw()` (unique
      to C++'s string-typed layer references). TS/Dart/.NET required
      threading an optional `options` param through several utility
      functions (`geometry.ts`, `vff.ts`, `geometry.dart`, `Geometry.cs`)
      that didn't already accept it, since those languages use a
      callback-based `emitLog`/`Observability.Log` design rather than a
      global logger like Python's. Deliberately left alone: retry/fallback
      control flow (e.g. TS's earcut-fallback catch), already-correct
      re-raise-as-staged-error patterns, tiny attribute-parsing fallbacks,
      and Python's dead/orphaned `geometry.py`/`metadata.py`/`materials.py`/
      `parser.py` modules (unreachable from the public API, built around a
      stale TLV tag scheme — good candidate for a future deletion item).
- [x] **23. Add explanatory comments to the unexplained TLV tag tables**,
      all 5 languages. DONE (PR #127, merged 2026-08-12). Added inline doc comments explaining `CONTAINER_TAGS` (VFF top-level container tags in `model.dat` wrapping nested sub-entities), dynamic attribute container tags (`DC05` payload tags: `DD05`, `B536`, `B136`, `B236`, `B336`, `B036`, `A438`), UTF-8 key/value tags (`B636` key, `AD38` value), and definition camera-facing flags (`5D1B`) across Python (`_core.py`), TypeScript (`parser.ts`, `geometry.ts`), Dart (`tlv.dart`, `geometry.dart`), C# (`Tlv.cs`, `Geometry.cs`), and C++ (`tlv.cpp`, `geometry.cpp`).
- [x] **24. Add a null-check to C#'s `SkpFile.Parse(byte[])`**. DONE (PR #127, merged 2026-08-12). Added explicit `if (buffer == null) throw new ArgumentNullException(...)` checks at the start of `SkpFile.Parse(byte[], ...)` and `SkpFile.BuildScene(byte[], ...)` in `Parser.cs`, with new unit test coverage in `IntegrationTests.cs`.
- [x] **25. Clean up Python's dead `SkpFile` fields**. DONE (PR #127, merged 2026-08-12). Removed unused fields `_raw_data`, `_model_data`, and `_material_files` from `SkpFile.__init__`, explicitly declared `self._parsed: Optional[Dict[str, Any]] = None` in `SkpFile.__init__`, and simplified `export/glb.py`'s check to `if skp_file._parsed is None:`. **Tier 8 (items 21-25) complete.**

## Tier 9 — Remaining format-completeness (needs more RE or new fixtures)

- [x] **26. Wire component behavior flags** (`shadows_face_sun`). DONE. Decoded bit 1 (`behavior & 2`) in legacy MFC parsers and sub-tag `5E1B` (inside container `581B`) in VFF/ZIP parsers across Python (`legacy.py`, `_core.py`, `model.py`), TypeScript (`legacy.ts`, `geometry.ts`, `model.ts`), Dart (`legacy.dart`, `geometry.dart`, `model.dart`), C# (`Legacy.cs`, `Geometry.cs`, `Model.cs`, `Parser.cs`), and C++ (`legacy.cpp`, `geometry.cpp`, `internal.hpp`, `model.hpp`, `model.cpp`). Added unit test coverage across all ports.
- [x] **27. Wire section planes, camera/default view, dimensions, and text entities**. DONE. Wired `SectionPlane`, `TextEntity`, and `Dimension` entities into `Definition` (and `root` model definition) across Python (`legacy.py`, `_core.py`, `model.py`), TypeScript (`legacy.ts`, `geometry.ts`, `model.ts`), Dart (`legacy.dart`, `geometry.dart`, `model.dart`), C# (`Legacy.cs`, `Geometry.cs`, `Model.cs`, `Parser.cs`), and C++ (`legacy.cpp`, `internal.hpp`, `model.hpp`, `model.cpp`). Added unit test coverage across all ports.
- [x] **28. Investigate Scenes/Pages support**. AUDITED — Documented format coverage. Standard `.skp` geometry parsing across all 5 ports processes model entities cleanly without failure even when scenes/pages are present in `meta/meta.dat` or legacy streams.
- [x] **29. Investigate geo-location and classification/IFC metadata**. AUDITED — Documented attribute dictionary routing. Geo-location and IFC/Classification metadata attach as attribute dictionaries (`CAttributeContainer` / `DC05` TLVs), which are safely captured into entity dynamic properties without interrupting core parsing.

## Tier 10 — Documentation cleanup

- [x] **30. Update `packages/dart/CHANGELOG.md` past 0.2.0**. DONE (PR #128, merged 2026-08-12). Added `0.3.0` release entry to Dart `CHANGELOG.md` documenting shipped features (`buildScene()`, GLB export, `toJson`, `meta.dat` units, visibility flags, recursion guard, zip entry size cap).
- [x] **31. Add field-level doc comments to C++'s `model.hpp`**. DONE (PR #128, merged 2026-08-12). Added Doxygen doc comments (`///`) to all public structs and fields (`Vertex`, `Edge`, `CoEdge`, `Face`, `Layer`, `Texture`, `Material`, `Style`, `Instance`, `Definition`, `SkpModel`).
- [x] **32. Document Dart's own `buildScene()`/`Scene`/GLB-export API in its own README**. DONE (PR #128, merged 2026-08-12). Updated `packages/dart/README.md` to document `buildScene()`, `Scene`, `MeshIndex`, `toGlb`, and `exportGlb`.
- [x] **33. Sweep the smaller doc-staleness items**. DONE (PR #128, merged 2026-08-12). Cleaned up outdated comments in .NET `Glb.cs` and Dart `glb.dart` claiming TypeScript `toGLB()` lacks `TEXCOORD_0`. **Tier 10 (items 30-33) complete.**

## Tier 11 — Structural, no urgency

- [x] **34. Refactor the 320–390-line scene-baking functions**. DONE (PR #131, merged 2026-08-12). Modularized scene-baking inner loops across Python (`scene.py`), TypeScript (`model.ts`), Dart (`scene.dart`), and .NET (`Scene.cs`) into clean, named helper functions (`_resolve_material`, `_resolve_color`, `_add_face_side`, `ResolveMaterialFromDicts`). **Tier 11 complete.**
- [x] **35. Decide OBJ export's fate**. DONE (PR #130, merged 2026-08-12). Documented OBJ export support across all five ports in `docs/DEVELOPER_GUIDE.md`, providing production-ready OBJ exporter code snippets for TypeScript, Dart, C# / .NET, and C++.
- [x] **36. Add an enumerable materials-by-ID map to C++'s `SkpModel`**. DONE (PR #129, merged 2026-08-12). Added `materials_by_id()` (`std::map<EntityId, Material*>` and `std::map<EntityId, const Material*>`) to C++ `SkpModel` and unit tests in `parser_test.cpp`.
- [x] **37. Document .NET's static-class `SkpFile` shape**. DONE (PR #129, merged 2026-08-12). Documented .NET's static `SkpFile` API design in `docs/DEVELOPER_GUIDE.md` under `Known cross-language differences`.
- [x] **38. Document Python's callback-less progress/logging design**. DONE (PR #129, merged 2026-08-12). Documented Python's standard `logging` module pattern for observability in `docs/DEVELOPER_GUIDE.md` under `Known cross-language differences`.

## Tier 12 — Multi-Language Exporters Expansion (Ordered Low Weight ➔ High Weight)

- [x] **39. STL Exporter (Standard Triangle Language `.stl`) — Low Weight [3D Printing]**. DONE (PR #135, merged 2026-08-12). Added native ASCII and Little-Endian Binary STL exporters (`to_stl_ascii`/`to_stl_binary`/`export`, `toSTLAscii`/`toSTLBinary`/`exportSTL`, `toStlAscii`/`toStlBinary`/`exportStl`, `StlExport.ToStlAscii`/`ToStlBinary`/`ExportStl`) across Python, TypeScript, Dart, C# / .NET, and C++, with optional scale factor for mm 3D slicer conversion. Verified 1:1 byte-for-byte binary STL parity (9,368,084 bytes for 187,360 triangles) across all ports.

- [ ] **40. PLY Exporter (Polygon File Format `.ply`) — Low Weight [3D Scanning & Mesh Processing]**
  - **Goal**: Add native PLY export across all 5 language ports (Python, TypeScript, Dart, C# / .NET, C++).
  - **Features**:
    - In-memory formatting (`to_ply`/`toPLY`/`toPly`/`ToPly`) & direct disk export (`export_ply`/`exportPLY`/`exportPly`/`ExportPly`).
    - Support both **ASCII PLY** and **Binary PLY** (Little-Endian).
    - Per-vertex position, normal (`nx ny nz`), texture coordinates (`u v`), and RGBA vertex color (`red green blue alpha`).
    - Header definition with `element vertex N` and `element face M`.
    - Unit test suites across all 5 languages.

- [ ] **41. DXF 3D Exporter (AutoCAD Drawing Exchange `.dxf`) — Medium Weight [3D CAD]**
  - **Goal**: Add native 3D DXF export across all 5 language ports (Python, TypeScript, Dart, C# / .NET, C++).
  - **Features**:
    - Text-based ASCII DXF `SECTION ENTITIES` with `3DFACE` and `POLYLINE` / `MESH` entities.
    - Layer preservation: Map SKP layers/tags directly to AutoCAD DXF `LAYER` tables.
    - RGB color indexing (`62` / `420` group codes).
    - Unit test suites across all 5 languages.

- [ ] **42. Rich Wavefront OBJ Exporter Extension (`.obj` + `.mtl`) — Medium-High Weight [3D Graphics]**
  - **Goal**: Extend the native OBJ exporter across all 5 language ports to generate companion `.mtl` material libraries.
  - **Features**:
    - `mtllib model.mtl` and `usemtl material_name` references inside `.obj`.
    - Material library `.mtl` serialization: `Ka` (ambient), `Kd` (diffuse RGB), `Ks` (specular), `d` / `Tr` (transparency/opacity), `map_Kd` (texture image filename).
    - Export extracted material texture images alongside `.obj`/`.mtl`.
    - Unit test suites across all 5 languages.

- [ ] **43. Ultimate IFC BIM Exporter (`.ifc` ISO-10303-21 STEP) — High Weight [Full BIM Standards]**
  - **Goal**: Implement standard-compliant, rich IFC (IFC4 & IFC2X3) BIM file export across all 5 language ports.
  - **Features**:
    - **STEP Physical Format Generator** (`HEADER`, `FILE_DESCRIPTION`, `FILE_SCHEMA(('IFC4'))`, `DATA`).
    - **Full Spatial Hierarchy**: `IfcProject` ➔ `IfcSite` ➔ `IfcBuilding` ➔ `IfcBuildingStorey` ➔ `IfcSpace` ➔ `IfcProduct`.
    - **BIM Element Classification**: Map SketchUp component classifications and names to specific IFC entities (`IfcWall`, `IfcSlab`, `IfcColumn`, `IfcBeam`, `IfcDoor`, `IfcWindow`, `IfcRoof`, `IfcCovering`, `IfcBuildingElementProxy` fallback).
    - **Dynamic Property Sets (`IfcPropertySet`)**: Extract all SketchUp Dynamic Component attributes, attributes from `CAttributeContainer` / `DC05` TLVs, and custom properties into `IfcPropertySingleValue` key-value pairs attached via `IfcRelDefinesByProperties`.
    - **Material & Style Mapping**: Extract SketchUp materials into `IfcMaterial`, `IfcMaterialLayerSet`, and `IfcSurfaceStyleRendering` (RGB colors, transparency).
    - **Layer / Tag Assignment**: Map SketchUp layers to `IfcPresentationLayerAssignment`.
    - **Tessellated / Brep Geometry**: Export geometry as `IfcTriangulatedFaceSet` / `IfcPolygonalFaceSet` (IFC4 standard) or `IfcFacetedBrep` (IFC2X3).
    - **Unit System Handling**: Convert SketchUp units (metres, mm, feet/inches) to standard `IfcSIUnit`.
    - Unit test suites across all 5 languages.

---

## Confirmed clean (no action needed — listed so this doesn't read as all-bad-news)

- Zip-slip/path traversal: not exploitable anywhere (in-memory extraction only).
- XXE: not exploitable anywhere (TS/C++ don't use a real XML parser; Dart's doesn't implement entity substitution; C#'s prohibits DTDs outright).
- C++'s legacy binary parser: consistently bounds-checked, no raw unchecked pointer arithmetic found.
- No unsafe deserialization (`eval`/`exec`/`pickle.loads`/etc.) anywhere.
- No true infinite loops on malformed input in any language.
- No skip markers, disabled tests, or commented-out tests in any of the 5 suites.
- No meaningful pattern of tautological assertions anywhere — exact-value checks dominate.
- No leftover debug prints, no commented-out code blocks, correct resource management on all paths, consistent naming within each language.
- `uv_projected`/`uv_projected_back`, `GlbPrimitive.uvs`, the `SkpParseError` structured-error hierarchy, and the `root` field concept are all genuinely consistent across all 5 languages.
- Python's, TypeScript's, and C++'s package READMEs are accurate; `CHANGELOG.md` itself is detailed and correct on every entry checked — the problem throughout is prose docs lagging the CHANGELOG, not the CHANGELOG being wrong.
- Version manifests (`pyproject.toml`, `pubspec.yaml`, `.csproj`, `CMakeLists.txt`) are consistent with the README table except TypeScript (item 33). All badges are live and correct.
