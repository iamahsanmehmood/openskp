# OpenSkp.Fragments

**Direct SketchUp (`.skp`) ↔ ThatOpen Fragments (`.frag`) export and import for .NET.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/iamahsanmehmood/openskp/blob/main/LICENSE)
[![NuGet](https://img.shields.io/nuget/v/OpenSkp.Fragments.svg?logo=nuget&logoColor=white)](https://www.nuget.org/packages/OpenSkp.Fragments)

Converts an [OpenSkp](https://www.nuget.org/packages/OpenSkp) `InstancedScene`
directly to ThatOpen's FlatBuffers-based `.frag` binary format
(https://github.com/ThatOpen/engine_fragment) — the format their
`@thatopen/fragments`/`@thatopen/components` viewer stack loads and streams —
and reads `.frag` `SHELL` geometry back into an `InstancedScene`, skipping the
`.skp → IFC (text) → web-ifc WASM → .frag` round trip.

Ships as its own package, separate from the core
[OpenSkp](https://www.nuget.org/packages/OpenSkp) package, because it needs
[Google.FlatBuffers](https://www.nuget.org/packages/Google.FlatBuffers) — the
core package has zero external dependencies, and this mirrors Python's opt-in
`pip install "openskp[fragments]"` extra instead of forcing every OpenSkp.NET
consumer to take a transitive dependency they don't need.

## Installation

```bash
dotnet add package OpenSkp.Fragments
```

## Usage

```csharp
using OpenSkp;
using OpenSkp.Fragments;

byte[] skpBytes = File.ReadAllBytes("model.skp");
var scene = SkpFile.BuildInstancedScene(skpBytes);

// Export (.skp -> .frag)
FragmentsExport.ExportFragments(scene, "model.frag");
byte[] fragBytes = FragmentsExport.ToFragments(scene); // in memory

// Import (.frag -> InstancedScene)
var roundTripped = FragmentsExport.ReadFragments("model.frag");
var fromBytes = FragmentsExport.FromFragments(fragBytes); // in memory
```

## Status — stated plainly

Supports both export (`ToFragments` / `ExportFragments`) and import
(`FromFragments` / `ReadFragments`), matching the Python, TypeScript, Dart
(`openskp_fragments`, on `main`), and C++ ports — see
[docs/LANGUAGE_PARITY.md § Fragments](https://github.com/iamahsanmehmood/openskp/blob/main/docs/LANGUAGE_PARITY.md#fragments-frag-support)
for the cross-language package and verification matrix. Verified on OpenSKP
round-trips (including multi-shell splits); not yet tested on external
`IfcImporter`-authored `.frag` files (`CIRCLE_EXTRUSION` samples are skipped
with a warning).

`InstancedNode`/`InstancedScene` in the core `OpenSkp` package carry real
per-instance source GUIDs (from the source file's own attribute dictionaries),
a generated-name flag, and the source file's real per-layer visibility state —
a duplicated real GUID (openskp#290 — SketchUp's Copy/Array tools can carry one
plugin-authored GUID to several distinct physical instances) gets the same
synthetic-fallback treatment as a missing one, so IDs stay unique either way.

Non-uniform scale and mirrored (negative-determinant) instance transforms
ARE fully supported: Fragments' `Transform` struct has no scale field at
all, so this module bakes each instance's scale/mirror into its own
geometry variant before writing. Instances sharing the exact same
(definition, scale) combination still dedupe onto one shared `Shell`.

See the [core OpenSkp README](https://www.nuget.org/packages/OpenSkp) for
the full cross-language picture, and
[openskp.export.fragments](https://github.com/iamahsanmehmood/openskp/blob/main/packages/python/src/openskp/export/fragments.py)
(the Python original this was ported from) for the complete rationale and
real-loader verification history.

## License

MIT — see [LICENSE](https://github.com/iamahsanmehmood/openskp/blob/main/LICENSE).
