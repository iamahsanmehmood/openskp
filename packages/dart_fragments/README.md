# openskp_fragments

**Direct SketchUp (`.skp`) ↔ ThatOpen Fragments (`.frag`) export and import for Dart (on `main`, not published to pub.dev).**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/iamahsanmehmood/openskp/blob/main/LICENSE)

Converts an [openskp](https://pub.dev/packages/openskp) `InstancedScene`
directly to ThatOpen's FlatBuffers-based `.frag` binary format
(https://github.com/ThatOpen/engine_fragment) — the format their
`@thatopen/fragments`/`@thatopen/components` viewer stack loads and streams —
and reads `.frag` `SHELL` geometry back into an `InstancedScene`, skipping the
`.skp → IFC (text) → web-ifc WASM → .frag` round trip.

Ships as its own package, separate from the core
[openskp](https://pub.dev/packages/openskp) package, because it needs
[`package:flat_buffers`](https://pub.dev/packages/flat_buffers) — the core
package depends only on `archive`, `crypto`, `meta`, and `xml` (pure Dart, no
native dependencies), and keeping Fragments separate mirrors Python's opt-in
`pip install "openskp[fragments]"` extra instead of pulling `flat_buffers` into
every `openskp` Dart consumer.

> **Registry status:** `openskp_fragments` is **on `main`, not published to
> pub.dev** (`publish_to: none`). Depend on it via a Git or local path
> dependency (note its monorepo `path: ../dart` dependency on `openskp`).

## Usage

```dart
import 'package:openskp/openskp.dart';
import 'package:openskp_fragments/openskp_fragments.dart';

final scene = SkpFile.open('model.skp').buildInstancedScene();

// Export (.skp -> .frag)
exportFragments(scene, 'model.frag');
final fragBytes = toFragments(scene); // in memory

// Import (.frag -> InstancedScene)
final roundTripped = readFragments('model.frag');
final fromBytes = fromFragments(fragBytes); // in memory
```

## Status — stated plainly

Supports both export (`toFragments` / `exportFragments`) and import
(`fromFragments` / `readFragments`), matching the Python, TypeScript, .NET
(`OpenSkp.Fragments`), and C++ ports — see
[docs/LANGUAGE_PARITY.md § Fragments](https://github.com/iamahsanmehmood/openskp/blob/main/docs/LANGUAGE_PARITY.md#fragments-frag-support)
for the cross-language package and verification matrix. Verified on OpenSKP
round-trips (including multi-shell splits and odd material counts); not yet
tested on external `IfcImporter`-authored `.frag` files (`CIRCLE_EXTRUSION`
samples are skipped with a warning).

`InstancedNode`/`InstancedScene` in the core `openskp` package carry real
per-instance source GUIDs (from the source file's own attribute dictionaries),
a generated-name flag, and the source file's per-layer visibility state — a
duplicated real GUID (openskp#290 — SketchUp's Copy/Array tools can carry one
plugin-authored GUID to several physical instances) gets the same synthetic
fallback as a missing one, so IDs stay unique.

Non-uniform scale and mirrored (negative-determinant) instance transforms
are supported: Fragments' `Transform` struct has no scale field, so this module
bakes each instance's scale/mirror into its own geometry variant before
writing. Instances sharing the exact same `(definition, scale)` combination
still dedupe onto one shared `Shell`.

**A note on the vendored FlatBuffers schema**: `lib/src/fragments_fb/index.fbs`
is the canonical schema (vendored the same way every other language port does).
`flatc`'s Dart code generator doesn't support FlatBuffers' optional-scalar
feature yet, so the generated bindings (`fragments_generated.dart`) come from
`index_dart_gen.fbs` (with one optional scalar given an explicit default and a
`localIdOrNull` helper for reading `SpatialStructure.localId`); see that file's
own comment for the full explanation.

See the [core openskp README](https://pub.dev/packages/openskp) for the
full cross-language picture, and
[openskp.export.fragments](https://github.com/iamahsanmehmood/openskp/blob/main/packages/python/src/openskp/export/fragments.py)
(the Python original this was ported from) for the complete rationale and
real-loader verification history.

## License

MIT — see [LICENSE](https://github.com/iamahsanmehmood/openskp/blob/main/LICENSE).
