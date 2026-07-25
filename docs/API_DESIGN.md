# Cross-Platform API Design

This page is a quick side-by-side reference. For the full explanation of
*why* the API is shaped this way (the `parse()`/`buildScene()` split,
memory behavior, observability, legacy format support, and the places
where the four languages currently differ), see the
[Developer Guide](DEVELOPER_GUIDE.md).

All four packages are available today — Python and TypeScript have been
public longest; .NET and Dart followed, built from scratch against the
same [binary format spec](BINARY_FORMAT.md) and cross-validated against
the other two on real files.

## Python

```python
from openskp import SkpFile

skp = SkpFile.open("model.skp")
model = skp.parse()

print(model.version)              # "{25.0.575}"
print(len(model.definitions))     # includes a 'ROOT' string key - see note below
print(len(model.layers))

for layer in model.layers:
    print(f"{layer.name}: rgb({layer.color_r}, {layer.color_g}, {layer.color_b})")

for def_id, defn in model.definitions.items():
    if not isinstance(def_id, int):
        continue  # 'ROOT' - see the Developer Guide's root-definition note
    print(f"{defn.name}: {len(defn.vertices)} verts, {len(defn.faces)} faces")

# Opt-in: full placed scene graph, triangulated, world-space
scene = skp.build_scene()
print(len(scene.glb_primitives), "GLB-ready mesh primitives")
```

## TypeScript / JavaScript

```typescript
import { SkpFile, parseSkp, buildScene, toGLB } from 'openskp';

// Node.js
const skp = SkpFile.open('model.skp');
const model = skp.parse();

// Browser (works identically - the package is isomorphic)
const buffer = await fetch('model.skp').then(r => r.arrayBuffer());
const model2 = parseSkp(buffer);

console.log(model.version);
console.log(model.layers);
console.log(model.definitions.size);
console.log(model.root.instances.length);  // top-level placements

// Opt-in: full placed scene graph, triangulated, world-space
const scene = skp.buildScene();
const glbBytes = toGLB(scene);   // only TS ships a binary .glb serializer today
```

## .NET / C#

```csharp
using OpenSkp;

SkpModel model = SkpFile.Open("model.skp");

Console.WriteLine(model.Version);
Console.WriteLine(model.Definitions.Count);
Console.WriteLine(model.Root.Instances.Count);   // top-level placements

foreach (var layer in model.Layers)
    Console.WriteLine($"{layer.Name}: rgb({layer.ColorR}, {layer.ColorG}, {layer.ColorB})");

// Opt-in: full placed scene graph, triangulated, world-space
Scene scene = SkpFile.BuildScene("model.skp");
Console.WriteLine(scene.GlbPrimitives.Count);
```

## Dart / Flutter

```dart
import 'package:openskp/openskp.dart';

final skp = SkpFile.open('model.skp');
final model = skp.parse();

print(model.version);
print(model.definitions.length);
print(model.root.instances.length);   // top-level placements

for (final layer in model.layers) {
  print('${layer.name}: rgb(${layer.colorR}, ${layer.colorG}, ${layer.colorB})');
}

// Opt-in: full placed scene graph, triangulated, world-space
final scene = skp.buildScene();
print('${scene.glbPrimitives.length} GLB-ready mesh primitives');
```

## Common data model

All four languages produce equivalent structured output for the same file:

| Field | Type | Description |
|---|---|---|
| `version` | string | SketchUp file-format version, e.g. `"{25.0.575}"` |
| `definitions` | map | Component/group definitions with geometry, keyed by ID |
| `root` (TS/.NET/Dart) or the `'ROOT'` entry in `definitions` (Python) | — | The implicit top-level definition — see the [Developer Guide](DEVELOPER_GUIDE.md#the-root-definition) |
| `layers` | list | Layer names + RGB colors |
| `materials` | list | Material names, colors, transparency, optional embedded texture |
| `styles` | list | Named front/back face colors for unpainted faces |

`buildScene()`'s result adds:

| Field | Type | Description |
|---|---|---|
| `sceneHierarchy` | tree | World-space instance nesting with resolved transforms |
| `meshIndex` | map | Metadata (name, layer, position, dynamic properties) per baked mesh |
| `glbPrimitives` | list | Triangulated positions/normals/indices, grouped by resolved color |
| `gltfMaterials` | list | glTF-format PBR material definitions referenced by primitive |

## Export formats

| Format | Extension | Ships in |
|---|---|---|
| GLB (binary glTF 2.0) | `.glb` | TypeScript (`toGLB()`) only — see [Export capabilities](DEVELOPER_GUIDE.md#export-capabilities) for the other three languages' current status |
| Raw scene data | — | All four, via `buildScene()`'s `Scene`/`GlbPrimitive` — build your own serializer (OBJ, custom JSON, etc.) from this |
