# Draft replacement for issue #284

## Title

Legacy (pre-VFF) parser: version-compatibility status across SketchUp 3–2020 (V4, V6–2020 fixed across all ports; real V3 and wild V5 still open)

## Body

### Summary

A real-world version-compatibility sweep (16 saves of a 5 MB, 146-definition textured project across SketchUp 3 through 2025; the V5 save is a corrupted 0-byte file, leaving 15 usable files) originally failed on 6 pre-2021 saves.

Following #310, #385, #392, #407, #411, #440, #443, and #465 (plus #515/#579 for tag line styles and #534/#577 for `CLayer` parent back-ref collisions), **14 of the 15 usable saves** (V4, V6, V7, V8, 2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, and 2025) now parse across all five published readers (Python, TypeScript, .NET, Dart, and C++, plus the unpublished Rust port) with identical geometry and placement totals (32,982 vertices, 40,925 edges, 9,652 faces, and 150 instances; full scene-build and export verification on that project was run in Python). Every classic version SketchUp's C library saves (SketchUp 4 through 2020) also parses in Python with matching rendering-options face colours (#550).

See [`docs/LANGUAGE_PARITY.md` §4](https://github.com/iamahsanmehmood/openskp/blob/main/docs/LANGUAGE_PARITY.md#4-real-sketchup-file-version-support) for the full per-version, per-language table.

### Failure signatures & status

| Version(s) | Original signature | Status |
|:--|:--|:--|
| **V3** (synthetic SDK-downgraded) | `expected a string record` | **Fixed** (#407 in Python; #411 across TS, .NET, Dart, C++) |
| **V3** (real multi-version project) | `edge-use parent slot None != current loop` | **Open** (all readers) |
| **V4, V5** (SDK-written & real V4) | `texture object is not a dib` (+ silent empty parse on real V4 cube) | **Fixed** (#440 in Python; #443 + #465 across TS, .NET, Dart, C++) |
| **V5** (real wild file `{5.0.262}`, reported by @boardfoot) | `back-ref to unwalked slot 20617` (on an inline PNG) | **Open** (confirmed failing in TypeScript; untested in Python, .NET, Dart, C++) |
| **V6** | `definition list misaligned` / `back-ref to unwalked slot N` | **Fixed** (#385 in Python; #392 in C++; #411 across TS, .NET, Dart) |
| **V7, V8, 2013** | `class-ref to non-class slot N (CAttributeNamed)` | **Fixed** (#310 in Python; #392 in C++; #411 across TS, .NET, Dart) |
| **2014–2020** (real project in non-Python ports) | `text delimiter not found` (`CText` arrow types != 3) & `expected a string record` (2-byte `CSkFont` pid) | **Fixed** (#465 across TS, .NET, Dart, C++, closing #442) |
| **2019** (and 2020 zero-definition filler) | `implausible def entity count` / silent root-entity drop | **Fixed** (#440 in Python; #443 + #465 across TS, .NET, Dart, C++) |

*(Related classic-format fixes tracked separately and now on `main` across all ports: 2019–2020 `CCustomLineStyle` tag line styles in #515 / #579, and the 1–2 tag + 11–13 definition `CLayer` parent back-ref collision in #534 / #577.)*

---

### What was fixed

- **V7 / V8 / 2013 (#310, #392, #411):** `_read_instance` gated its 16-byte GUID read on `schema >= 5`, but a real V7 `CComponentInstance` reports schema 6 (and `CGroup` reports schema 1 everywhere) with no GUID present. Gating on `ar.ver >= 14` fixed both the `CAttributeNamed` slot collision and silent root-list truncation.
- **V6 (#385, #392, #411):** `_read_attr_named` unconditionally read a trailing `u32` added in SketchUp 7, consuming 4 bytes from the next sibling entity in V6 files that carry attribute dictionaries. Gated on `ar.ver >= 7`.
- **V3 synthetic layout (#407, #411):** Gated ~12 SketchUp-3-specific layout differences on `ar.ver < 4` (no attribute-container pointer on vertex/loop/edge-use/material/layer; 5-byte draw block; shorter layer tail and 13-byte definition base block; inline texture image without `CDib`; fixed 129-byte thumbnail; no layer count; no instance names; leading face pointer holds front material). Verified on SDK-downgraded synthetic V3 fixtures (`legacy_v3_synthetic.skp`, `legacy_v3_textured.skp`).
- **V4, V5 (SDK-written), and 2019/2020 (#440, #443):** Handled pre-V6 inline texture bytes (no `CDib` slot before V6), 7-byte V4 draw block, 19-byte V4 definition header, 10-byte camera tail, no trailing thumbnail image pointer, no instance/group names before V5, pre-V6 linear-dimension layout, the 2019 post-layer separator null before a definition's entity count, and 2020's zero-definition filler (which previously swallowed root entities). Also fixed a real V4 cube sample that had silently parsed as empty.
- **Real multi-version project in TypeScript, .NET, Dart, and C++ (#465, closing #442):** Relaxed `CText` delimiter matching from arrow type `== 3` to `0..8` and skipped `popcount(mask)` PID bytes on `CSkFont` (matching Python), bringing all four ports to identical geometry/instance counts on V4 and V6–2020 saves of the real project (`legacy_text_arrows_2017.skp`).

---

### Still open

1. **Real SketchUp 3 project (`{3.0.*}`):**
   - **Symptom:** `edge-use parent slot None != current loop` in **all five** readers (#440, #465).
   - **Notes:** Distinct from the synthetic SDK-downgraded V3 layout fixed in #407; single-material V3 slot-base probing and V3 draw-block flags also remain unverified.
2. **Real wild SketchUp 5 file (`{5.0.262}`):**
   - **Symptom:** Reported by @boardfoot on #284 — fails in **TypeScript** with `back-ref to unwalked slot 20617` on an inline PNG texture; not yet tested against Python, .NET, Dart, or C++.
   - **Notes:** The multi-version sweep's own V5 save was a 0-byte file, so V5 coverage in tests (`legacy_v5_synthetic.skp`, `legacy_v5_textured.skp`, `face_colors_v5.skp`) currently comes solely from files written by SketchUp's C library.
