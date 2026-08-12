# Cross-Platform API Design

This page is a quick side-by-side reference. For the full explanation of
*why* the API is shaped this way (the `parse()`/`buildScene()` split,
memory behavior, observability, legacy format support, and the places
where the five languages currently differ), see the
[Developer Guide](DEVELOPER_GUIDE.md).

All five packages are available today — Python and TypeScript have been
public longest; .NET, Dart, and C++ followed, built from scratch against the
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
const glbBytes = toGLB(scene);
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

## C++17

```cpp
#include <openskp/openskp.hpp>

auto skp = openskp::SkpFile::open("model.skp");
auto model = skp.parse();
auto scene = skp.build_scene();
auto glb_bytes = openskp::to_glb(scene);
openskp::export_glb(scene, "model.glb");

std::cout << model.version << " " << model.definitions.size() << '\n';
std::cout << scene.glb_primitives.size() << '\n';
```

## Common data model

All five languages produce equivalent structured output for the same file:

| Field | Type | Description |
|---|---|---|
| `version` | string | SketchUp file-format version, e.g. `"{25.0.575}"` |
| `definitions` | map | Component/group definitions with geometry, keyed by ID |
| `root` (TS/.NET/Dart/C++) or the `'ROOT'` entry in `definitions` (Python) | — | The implicit top-level definition — see the [Developer Guide](DEVELOPER_GUIDE.md#the-root-definition) |
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
| GLB (binary glTF 2.0) | `.glb` | All 5 languages (`glb.export` / `toGLB` / `GlbExport.ExportGlb` / `exportGlb` / `export_glb`) |
| Wavefront OBJ | `.obj` | All 5 languages (`obj.export` / `toOBJ` / `ObjExport.ExportObj` / `exportObj` / `export_obj`) |
| STL (3D Printing) | `.stl` | All 5 languages (`stl.export` / `toSTLAscii` / `StlExport.ExportStl` / `exportStl` / `export_stl`) |
| PLY (Stanford Mesh) | `.ply` | All 5 languages (`ply.export` / `toPLYAscii` / `PlyExport.ExportPly` / `exportPly` / `export_ply`) |
| DXF 3D (AutoCAD Polyface Mesh) | `.dxf` | All 5 languages (`dxf.export` / `toDXF` / `DxfExport.ExportDxf` / `exportDxf` / `export_dxf`) |
| IFC4 (BIM ISO STEP) | `.ifc` | All 5 languages (`ifc.export` / `toIFC` / `IfcExport.ExportIfc` / `exportIfc` / `export_ifc`) |
| Full metadata JSON | `.json` | All 5 languages (`json_export.export` / `toJSON` / `JsonExport.ExportJson` / `exportJson` / `export_json`) |
| Raw scene data | — | All 5 languages via `buildScene()` — build custom serializers directly from `Scene` / `GlbPrimitive` |
