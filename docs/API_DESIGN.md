# Cross-Platform API Design

This page is a quick side-by-side reference. For the full explanation of
*why* the API is shaped this way (the `parse()`/`buildScene()` split,
memory behavior, observability, legacy format support, and the places
where the five languages currently differ), see the
[Developer Guide](DEVELOPER_GUIDE.md).

All five published packages are available today (plus an unpublished Rust port
in `packages/rust`) — Python and TypeScript landed first, followed by .NET,
Dart, and C++, each implemented natively without shared bindings (see
[BINARY_FORMAT.md](BINARY_FORMAT.md) for the 2021+ VFF/TLV container and each
port's `legacy.*` / `create.*` docstrings for the pre-2021 classic MFC
container) and cross-validated against the other four ports on real files.

## Python

```python
from openskp import SkpFile

skp = SkpFile.open("model.skp")
model = skp.parse()

print(model.version)              # "{25.0.575}"
print(len(model.definitions))     # numeric-keyed only; the implicit top-level
print(len(model.layers))          # geometry lives separately in model.root

for layer in model.layers:
    print(f"{layer.name}: rgb({layer.color_r}, {layer.color_g}, {layer.color_b})")

for def_id, defn in model.definitions.items():
    print(f"{defn.name}: {len(defn.vertices)} verts, {len(defn.faces)} faces")

print(f"root: {len(model.root.vertices)} verts, {len(model.root.faces)} faces")

# Opt-in: full placed scene graph, triangulated, world-space
scene = skp.build_scene()
print(len(scene.glb_primitives), "GLB-ready mesh primitives")
```

**Writing:**

```python
from openskp import create

builder = create()
red = builder.add_material("Red", (255, 0, 0))
with builder.add_component_definition("Chair") as chair:
    chair.add_face([(0, 0, 0), (20, 0, 0), (20, 20, 0), (0, 20, 0)])
builder.add_instance(chair, translation=(50, 0, 0))
builder.add_face([(0, 0, 0), (100, 0, 0), (100, 100, 0), (0, 100, 0)], material=red)
builder.save("output.skp")
```

See [Write capabilities](DEVELOPER_GUIDE.md#write-capabilities) in the
Developer Guide for the full scope, limitations, and the naming
convention each language follows (`add_component_definition`'s scoping
in particular differs quite a bit per language — Python's `with` above,
TypeScript/Dart's callback form, .NET's `using` block, and C++'s
explicit `.close()`; see the examples below).

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

**Writing:**

```typescript
import { create } from 'openskp';

const builder = create();
const red = builder.addMaterial('Red', [255, 0, 0]);
const chair = builder.addComponentDefinition('Chair', (def) => {
  def.addFace([[0, 0, 0], [20, 0, 0], [20, 20, 0], [0, 20, 0]]);
});
builder.addInstance(chair, { translation: [50, 0, 0] });
builder.addFace([[0, 0, 0], [100, 0, 0], [100, 100, 0], [0, 100, 0]], { material: red });
builder.save('output.skp');   // Node.js; use builder.toBytes() in the browser
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

**Writing:**

```csharp
var builder = SkpCreate.NewFile();
int red = builder.AddMaterial("Red", (255, 0, 0));
var chair = builder.AddComponentDefinition("Chair");
using (chair)
{
    chair.AddFace(new (double, double, double)[] { (0, 0, 0), (20, 0, 0), (20, 20, 0), (0, 20, 0) });
}
builder.AddInstance(chair, translation: (50, 0, 0));
builder.AddFace(new (double, double, double)[] { (0, 0, 0), (100, 0, 0), (100, 100, 0), (0, 100, 0) }, material: red);
builder.Save("output.skp");
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

**Writing:**

```dart
final builder = create();
final red = builder.addMaterial('Red', [255, 0, 0]);
final chair = builder.addComponentDefinition('Chair', (def) {
  def.addFace([(0.0, 0.0, 0.0), (20.0, 0.0, 0.0), (20.0, 20.0, 0.0), (0.0, 20.0, 0.0)]);
});
builder.addInstance(chair, translation: (50.0, 0.0, 0.0));
builder.addFace([(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (100.0, 100.0, 0.0), (0.0, 100.0, 0.0)], material: red);
builder.save('output.skp');
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

**Writing:**

```cpp
using namespace openskp;

auto builder = create();
int red = builder->add_material("Red", Color3{255, 0, 0});
auto& chair = builder->add_component_definition("Chair");
chair.add_face({{0, 0, 0}, {20, 0, 0}, {20, 20, 0}, {0, 20, 0}});
chair.close();

InstanceOptions opts;
opts.translation = {50, 0, 0};
builder->add_instance(chair, opts);

FaceOptions face_opts;
face_opts.material = red;
builder->add_face({{0, 0, 0}, {100, 0, 0}, {100, 100, 0}, {0, 100, 0}}, face_opts);

builder->save("output.skp");
```

## Common data model

All five languages produce equivalent structured output for the same file:

| Field | Type | Description |
|---|---|---|
| `version` | string | SketchUp file-format version, e.g. `"{25.0.575}"` |
| `definitions` | map | Component/group definitions with geometry, keyed by ID |
| `root` (all five languages - `model.root()` is a method in C++, a plain field/property elsewhere) | — | The implicit top-level definition — see the [Developer Guide](DEVELOPER_GUIDE.md#the-root-definition) |
| `layers` | list | Layer names + RGB colors |
| `materials` | list | Material names, colors, transparency, optional embedded texture |
| `styles` | list | Named front/back face colors for unpainted faces; C++ also exposes the style description and every raw style.xml item (see [Style items](#style-items)) |
| `camera` (C++) | optional | The view the model was saved with (eye, target, up, field of view, parallel projection, visible height); SketchUp reopens the file at it whichever scene is selected. Scenes mark the one selected at save time (`Page::selected`) |

### Style items

Each `styles/*/style.xml` holds a style's display settings as `<sty:item id="N">`
elements, each wrapping one `<t:variant type="T">` value. The C++ `Style` exposes all
of them unparsed as `items` (item id → `{type, value}`, value trimmed, nested XML kept
verbatim), plus the `desc` attribute as `description`, so callers can map the settings
they understand. Other languages can follow the same shape.

Variant types seen in real files: 1 bool (`0`/`1`), 3/4 int32, 5 color in older files
(current SketchUp writes colors as type 4), 6 float, 7 double, 13 nested XML (watermark
list). Colors are signed int32 ABGR: R in the low byte, alpha in the high byte.

Item ids are SketchUp's own and undocumented. The ones below were confirmed against
SketchUp 2026 by giving every `Sketchup::RenderingOptions` key a distinct value,
updating the style, saving, and matching the saved items back:

| Ids | `RenderingOptions` key |
|---|---|
| 1000 / 1001 / 1002 | `EdgeDisplayMode` / `EdgeType` / `EdgeColorMode` |
| 1004 / 1005 | `ExtendLines` / `LineExtension` |
| 1006 / 1007 | `DrawSilhouettes` / `SilhouetteWidth` |
| 1008 / 1009 | `DrawDepthQue` / `DepthQueWidth` |
| 1010 / 1011 | `DrawLineEnds` / `LineEndWidth` |
| 1012 / 1014 / 1015 | `JitterEdges` / `ForegroundColor` / `DrawBackEdges` |
| 2001 | `RenderMode` |
| 2002 / 2003 | `FaceFrontColor` / `FaceBackColor` |
| 2004 / 2005 | `ModelTransparency` / `MaterialTransparency` |
| 2006 / 2007 | `TransparencySort` (0 Faster, 2 Nicer) / `Texture` |
| 4000 / 4001 / 4002 | `BackgroundColor` / `SkyColor` / `DrawHorizon` |
| 4003 / 4004 / 4005 | `GroundColor` / `DrawGround` / `GroundTransparency` |
| 4006 / 4007 | `DrawUnderground` / `HorizonColor` |
| 5000 | `DisplayWatermarks` |
| 7000 / 7001 / 7002 | `HighlightColor` / `LockedColor` / `ConstructionColor` |
| 7003 / 7004 | `SectionActiveColor` / `SectionInactiveColor` |
| 7005 / 7016 | `SectionDefaultCutColor` / `SectionDefaultFillColor` |
| 7008 / 7011 / 7012 | `DisplaySketchAxes` / `DisplayColorByLayer` / `HideConstructionGeometry` |
| 7010 | `DrawHidden` (pre-2020 key: hidden geometry or hidden objects) |
| 7013 | bit mask: bit 0 `DisplaySectionPlanes`, bit 1 `DisplaySectionCuts` |
| 7014 / 7015 | `SectionCutWidth` / `SectionCutFilled` |
| 7017 / 7018 | `DrawHiddenGeometry` / `DrawHiddenObjects` |
| 8100 / 8102 / 8103 | `AmbientOcclusion` / `AmbientOcclusionDistance` / `AmbientOcclusionIntensity` |
| 8105 / 8106 / 8107 | `AmbientOcclusionColorEnabled` / `AmbientOcclusionColor` / `AmbientOcclusionMultiplier` |

A model lists several styles; SketchUp stores the current one twice, in its own folder and
as a working copy `<folder>_1` that holds the settings the model displays (edits not yet
updated into the style included). model.dat's style catalog (record `0602 > 7869`) names
them: `7969` lists the styles (`6C6B` entries with a `DC05 > DE05` id and a `6F6B` name equal
to the style's folder), `7A69` holds the current style's id, `7B69` the working copy's
entry, and `7C69` is 1 when the current style was edited without updating it. The C++
`Style` carries its `folder` and the flags `active` (the current style in the list),
`working_copy`, and `modified` (on the active style).

Watermarks are item 5001, a nested `<wmlist>` of `<screenimage>` entries. The C++
`Style::watermarks` lists them in file order, each with its name, every other attribute
raw (entity-decoded), the image path as the style names it, its original file name, and
the image bytes. A style names its images relative to its own folder (`./2.jpg`); the
current style's working copy names them from the ZIP root (`watermarks/Watermark1.jpg`). The `<MODEL SPACE>` entry is a separator
between under- and overlays and is skipped. Attributes seen in SketchUp 2026:
`stretched` / `tiled` (1 when that display mode is on; neither means positioned),
`position` (3x3 grid numbered row by row, 0 top-left .. 4 center .. 8 bottom-right),
`scale` (0–1), `alphaScale` (the Blend slider, 0–1), `maintainAR` (stretched mode's
aspect lock), `background` (1 = drawn behind the model).

Items that are not `RenderingOptions` keys, matched against the Styles panel instead:
1016 edge dashes, 2008 X-ray opacity (0–1), 8001 / 8003 Match Photo background /
foreground opacity (0–1); 8000 / 8002 are by adjacency the background / foreground
photo shown flags.

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
| Full metadata JSON | `.json` | All 5 languages (`json_export.export` / `toJSON` / `JsonExport.ToDict` / `toJson` / `export_json`) — TypeScript/.NET/Dart return the object/dict only; see [Export capabilities](DEVELOPER_GUIDE.md#export-capabilities) for the file-writing gap |
| Raw scene data | — | All 5 languages via `buildScene()` — build custom serializers directly from `Scene` / `GlbPrimitive` |
