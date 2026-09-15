# OpenSkp.Fragments

**Direct SketchUp (`.skp`) → ThatOpen Fragments (`.frag`) export — EXPERIMENTAL.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/iamahsanmehmood/openskp/blob/main/LICENSE)
[![NuGet](https://img.shields.io/nuget/v/OpenSkp.Fragments.svg?logo=nuget&logoColor=white)](https://www.nuget.org/packages/OpenSkp.Fragments)

Converts an [OpenSkp](https://www.nuget.org/packages/OpenSkp) `InstancedScene`
directly to ThatOpen's own FlatBuffers-based `.frag` binary format
(https://github.com/ThatOpen/engine_fragment) — the format their
`@thatopen/fragments`/`@thatopen/components` viewer stack actually loads and
streams. Skips the common `.skp → IFC (text) → web-ifc WASM → .frag` round
trip through IFC's verbose ASCII STEP format entirely.

Ships as its own package, separate from the core
[OpenSkp](https://www.nuget.org/packages/OpenSkp) package, because it needs
[Google.FlatBuffers](https://www.nuget.org/packages/Google.FlatBuffers) — the
core package has zero external dependencies, and this mirrors Python's own
opt-in `pip install openskp[fragments]` extra instead of forcing every
OpenSkp.NET consumer to take a transitive dependency they don't need.

## Usage

```csharp
using OpenSkp;
using OpenSkp.Fragments;

byte[] skpBytes = File.ReadAllBytes("model.skp");
var scene = SkpFile.BuildInstancedScene(skpBytes);

FragmentsExport.ExportFragments(scene, "model.frag");
// or, in memory:
byte[] fragBytes = FragmentsExport.ToFragments(scene);
```

## Status — stated plainly

Export only (no `.frag` → `InstancedScene` reader yet), matching the
[TypeScript port](https://github.com/iamahsanmehmood/openskp/tree/main/packages/typescript)'s
own scope, not Python's/C++'s fuller read+write one (TypeScript has no read
side either — not a gap unique to .NET). GUID/name-is-generated/layer-hidden
fidelity matches Python's/C++'s in full: `InstancedNode`/`InstancedScene` in
the core `OpenSkp` package carry real per-instance source GUIDs (from the
source file's own attribute dictionaries), a generated-name flag, and the
source file's real per-layer visibility state — a duplicated real GUID
(openskp#290 — SketchUp's own Copy/Array tools can carry one plugin-authored
GUID to several distinct physical instances) gets the same synthetic-
fallback treatment as a missing one, so IDs stay unique either way.

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
