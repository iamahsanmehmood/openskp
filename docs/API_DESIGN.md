# Cross-Platform API Design

## Python

```python
from openskp import SkpFile

# Parse
skp = SkpFile.open("model.skp")
model = skp.parse()

# Inspect
print(model.version)          # "{25.0.575}"
print(len(model.definitions)) # 47
print(len(model.layers))      # 14

for layer in model.layers:
    print(f"{layer.name}: rgb({layer.color_r}, {layer.color_g}, {layer.color_b})")

for def_id, defn in model.definitions.items():
    print(f"{defn.name}: {len(defn.vertices)} verts, {len(defn.faces)} faces")

# Export
from openskp.export import glb
glb.export(skp, "output.glb")
```

## TypeScript (Planned)

```typescript
import { parseSkp, toGLB } from 'openskp';

// Browser
const buffer = await fetch('model.skp').then(r => r.arrayBuffer());
const model = parseSkp(buffer);

console.log(model.version);
console.log(model.layers);
console.log(model.definitions.size);

// Export
const glbBuffer = toGLB(model);
```

## Dart (Planned)

```dart
import 'package:openskp/openskp.dart';

final file = File('model.skp');
final bytes = await file.readAsBytes();
final model = SkpFile.parse(bytes);

print(model.version);
print(model.layers.length);

final glb = model.toGlb();
await File('output.glb').writeAsBytes(glb);
```

## Common Data Model

All platforms produce equivalent structured output:

| Field | Type | Description |
|-------|------|-------------|
| `version` | string | SketchUp format version |
| `definitions` | map | Component definitions with geometry |
| `layers` | list | Layer names + RGB colors |
| `materials` | list | Material names + colors + transparency |
| `scene_hierarchy` | tree | Instance nesting with transforms |

## Export Formats

| Format | Extension | Use Case |
|--------|-----------|----------|
| GLB | `.glb` | 3D viewers (Three.js, Blender, etc.) |
| OBJ | `.obj` | Legacy 3D software import |
| JSON | `.json` | Metadata inspection, web UIs |
