# openskp_fragments

**Direct SketchUp (`.skp`) → ThatOpen Fragments (`.frag`) export — EXPERIMENTAL.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/iamahsanmehmood/openskp/blob/main/LICENSE)

Converts an [openskp](https://pub.dev/packages/openskp) `InstancedScene`
directly to ThatOpen's own FlatBuffers-based `.frag` binary format
(https://github.com/ThatOpen/engine_fragment) — the format their
`@thatopen/fragments`/`@thatopen/components` viewer stack actually loads and
streams. Skips the common `.skp → IFC (text) → web-ifc WASM → .frag` round
trip through IFC's verbose ASCII STEP format entirely.

Ships as its own package, separate from the core
[openskp](https://pub.dev/packages/openskp) package, because it needs
[`package:flat_buffers`](https://pub.dev/packages/flat_buffers) — the core
package has zero external dependencies, and this mirrors Python's own opt-in
`pip install openskp[fragments]` extra instead of forcing every openskp Dart
consumer to take a transitive dependency they don't need.

## Usage

```dart
import 'package:openskp/openskp.dart';
import 'package:openskp_fragments/openskp_fragments.dart';

final scene = SkpFile.open('model.skp').buildInstancedScene();

exportFragments(scene, 'model.frag');
// or, in memory:
final fragBytes = toFragments(scene);
```

## Status — stated plainly

Export only (no `.frag` → `InstancedScene` reader yet), matching the
[TypeScript](https://github.com/iamahsanmehmood/openskp/tree/main/packages/typescript)
and [.NET](https://github.com/iamahsanmehmood/openskp/tree/main/packages/dotnet/OpenSkp.Fragments)
ports' own scope, not Python's/C++'s fuller read+write one (neither
TypeScript nor .NET has the read side either — not a gap unique to Dart).
Built at full Python/C++/TypeScript/.NET GUID/name-is-generated/layer-hidden
fidelity from the start: `InstancedNode`/`InstancedScene` in the core
`openskp` package carry real per-instance source GUIDs (from the source
file's own attribute dictionaries), a generated-name flag, and the source
file's real per-layer visibility state — a duplicated real GUID (openskp#290
— SketchUp's own Copy/Array tools can carry one plugin-authored GUID to
several distinct physical instances) gets the same synthetic-fallback
treatment as a missing one, so IDs stay unique either way.

Non-uniform scale and mirrored (negative-determinant) instance transforms
ARE fully supported: Fragments' `Transform` struct has no scale field at
all, so this module bakes each instance's scale/mirror into its own
geometry variant before writing. Instances sharing the exact same
(definition, scale) combination still dedupe onto one shared `Shell`.

**A note on the vendored FlatBuffers schema**: `lib/src/fragments_fb/index.fbs`
is the real, canonical schema (vendored the same way every other language
port does). `flatc`'s Dart code generator doesn't support FlatBuffers'
optional-scalar feature yet, so the actual generated bindings
(`fragments_generated.dart`) are produced from `index_dart_gen.fbs` — a
copy with exactly one field's declared default changed to make code
generation possible. This is wire-format-safe for this write-only module;
see that file's own comment for the full explanation.

See the [core openskp README](https://pub.dev/packages/openskp) for the
full cross-language picture, and
[openskp.export.fragments](https://github.com/iamahsanmehmood/openskp/blob/main/packages/python/src/openskp/export/fragments.py)
(the Python original this was ported from) for the complete rationale and
real-loader verification history.

## License

MIT — see [LICENSE](https://github.com/iamahsanmehmood/openskp/blob/main/LICENSE).
