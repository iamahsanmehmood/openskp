# OpenSKP

**The open-source SketchUp (`.skp`) file parser, writer, and converter — Dart / Flutter edition.**

Parse, write, and convert `.skp` files without SketchUp. No SDK. No license. Just code.

[![Pub Version](https://img.shields.io/pub/v/openskp.svg?logo=dart&logoColor=white)](https://pub.dev/packages/openskp)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/iamahsanmehmood/openskp/blob/main/LICENSE)

🏠 [openskp.com](https://openskp.com) · 🌐 [Try the Live Web Viewer](https://iamahsanmehmood.github.io/openskp/) · 📖 [Docs](https://iamahsanmehmood.github.io/openskp/docs/) · [Changelog](https://github.com/iamahsanmehmood/openskp/blob/main/CHANGELOG.md)

> [!IMPORTANT]
> This project was built by reverse engineering a proprietary binary format. It is not affiliated with or endorsed by Trimble Inc. or SketchUp.

---

## 🌟 What is OpenSKP?

OpenSKP is the first and only open-source, cross-platform parser for
SketchUp binary files — reverse-engineered from both the modern **VFF
container** (SketchUp 2021+) and the classic **MFC `CArchive`** container
(SketchUp 2013–2020). It gives Dart and Flutter developers full
programmatic access to geometry, materials, components, layers, and
metadata, with no SketchUp installation and no proprietary SDK required.
The same parser and export API also ship as first-class packages for
Python, TypeScript, .NET, and C++ — see the
[project README](https://github.com/iamahsanmehmood/openskp) for the full
cross-language picture.

This package can also *write* new `.skp` files from scratch, and edit
existing ones, validated feature-by-feature against the real SketchUp
SDK (see [Writing](#-writing) below).

---

## 🌟 Vision & Platform Coverage

Enable mobile (iOS/Android), desktop, and web developers to parse and build 3D SketchUp file viewer pipelines natively inside Flutter.

- **Mobile**: Flutter iOS & Android apps
- **Desktop**: Flutter Windows, macOS, and Linux
- **Web**: Compile client-side for browser-based parsers
- **Server**: Dart shelf backend parsing services

### 🌐 [Try the Live Web Viewer (Drag-and-Drop)](https://iamahsanmehmood.github.io/openskp/)

---

## ✨ Features

- **Zero Native Dependencies**: 100% pure Dart implementation.
- **Full-fidelity parsing**: vertices, edges, faces, normals, UV
  coordinates, nested component hierarchies, layers/tags, materials,
  textures, styles, and dynamic-component attributes.
- **Both SketchUp file generations**: modern VFF (2021+) and legacy MFC
  (2013–2020) containers, transparently, behind one `parse()` call.
- **Scene baking**: an opt-in `buildScene()` pass resolves the full placed
  scene graph to world-space, triangulated, export-ready geometry.
- **Native multi-format conversion**: glTF (GLB), Wavefront OBJ/MTL, STL,
  PLY, AutoCAD DXF (3DFACE and Polyface Mesh), IFC4 (BIM/ISO 10303-21
  STEP) — all written from scratch, no third-party CAD/BIM SDK involved.
  The DXF writer is verified against real desktop AutoCAD, not just
  lenient DXF readers.
- **Write support**: build new legacy-format `.skp` files from scratch:
  geometry (including true, editable circular/arc curves, freeform
  polylines, faces with holes cut out, and non-planar auto-triangulation),
  materials (solid + PNG/JPEG textures), layers, nested component
  definitions and groups, instance rotation/visibility, and custom
  attribute dictionaries — or load and extend an existing file with
  `openExisting()`. No SDK involved; every feature validated against the
  real SketchUp SDK. See [Writing](#-writing) below.

---

## 🚀 Installation

Add the library to your Dart or Flutter project:

```bash
# For Dart projects
dart pub add openskp

# For Flutter projects
flutter pub add openskp
```

---

## 💻 Quick Start

### 1. Parsing a SketchUp File
Open a `.skp` file, read the byte buffer, and load the data model:

```dart
import 'dart:io';
import 'package:openskp/openskp.dart';

void main() async {
  // Read SKP file bytes
  final file = File('my_model.skp');
  final bytes = await file.readAsBytes();

  // Load and parse SKP model
  final skpFile = SkpFile.fromBuffer(bytes);
  final model = skpFile.parse();

  print('SketchUp File Version: ${model.version}');

  // Inspect Layers
  print('Layers:');
  for (var layer in model.layers) {
    print('- ${layer.name} (RGB: ${layer.colorR}, ${layer.colorG}, ${layer.colorB})');
  }

  // Inspect Materials
  print('Materials:');
  for (var material in model.materials) {
    print('- ${material.name} (Opacity: ${material.transparency})');
  }

  // Walk component definitions and their geometry
  model.definitions.forEach((id, def) {
    print('Definition $id: ${def.name} - ${def.vertices.length} vertices, ${def.faces.length} faces');
  });

  // model.root holds whatever is placed directly in the model (not inside
  // any component/group), including root-level instances.
  print('Root-level instances: ${model.root.instances.length}');
}
```

### 2. Baking Scene Graph & GLB Export
Bake all placed instances into world-space, triangulated mesh primitives ready for 3D rendering or GLB export:

```dart
import 'dart:io';
import 'package:openskp/openskp.dart';

void main() async {
  final bytes = await File('my_model.skp').readAsBytes();
  final skpFile = SkpFile.fromBuffer(bytes);

  // Bake scene graph into world-space meshes
  final scene = skpFile.buildScene();

  print('Renderable primitives: ${scene.glbPrimitives.length}');
  for (var entry in scene.meshIndex.entries) {
    print('- Mesh ${entry.key}: ${entry.value.definitionName} [${entry.value.layer}]');
  }

  // Export to binary glTF 2.0 (GLB) bytes or file
  final glbBytes = toGlb(scene);
  await exportGlb(scene, 'my_model.glb');
  print('Exported GLB: ${glbBytes.length} bytes');

  // Export to Wavefront OBJ, plus a companion .mtl material library
  final objText = toObj(scene);
  final mtlText = toMtl(scene);
  exportObj(scene, 'my_model.obj'); // writes .obj + .mtl together

  // Export to STL (3D printing), ASCII or little-endian binary
  final stlBytes = toStlBinary(scene);
  exportStl(scene, 'my_model.stl', binary: true);

  // Export to PLY (Stanford Triangle Format), ASCII or little-endian binary
  final plyBytes = toPlyBinary(scene);
  exportPly(scene, 'my_model.ply', binary: true);

  // Export to 3D DXF (AutoCAD R2000 compliant, Polyface Mesh by default)
  final dxfText = toDxf(scene);
  exportDxf(scene, 'my_model.dxf');

  // Export to IFC4 / BIM (ISO 10303-21 STEP format)
  final ifcText = toIfc(scene);
  exportIfc(scene, 'my_model.ifc');
}
```

---

## ✏️ Writing

OpenSKP can also *create* new `.skp` files from scratch — a genuine,
from-scratch binary writer for the legacy MFC `CArchive` format (SketchUp
2013–2020), with no SketchUp SDK involved at any point. Ports the same
feature set as the Python package's writer, verified byte-identical to
Python's own output on the same input: geometry, materials (solid +
PNG/JPEG textures), layers (with color and default visibility),
component definitions with multiple instances, groups, nested
definitions and nested group instances, per-instance rotation and
visibility, explicit per-side texture positioning, custom key/value
attribute dictionaries, circular faces and partial arcs, freeform
polyline curves, faces with holes cut out, and non-planar
auto-triangulation. `openExisting()` loads an *existing* legacy-format
file and rebuilds it as a new builder, so more geometry can be added
before saving. See [`lib/src/create.dart`](lib/src/create.dart) for the
full scope notes.

```dart
import 'package:openskp/openskp.dart';

void main() {
  final builder = create();

  // Materials and layers
  final red = builder.addMaterial('Red', [255, 0, 0]);
  final roof = builder.addLayer('Roof', color: [180, 60, 40]);

  // All addComponentDefinition/addGroup calls must come before any
  // addInstance/addFace call - placing anything locks in the file's
  // internal slot numbering for everything after it
  final chair = builder.addComponentDefinition('Chair', (def) {
    def.addFace([(0.0, 0.0, 0.0), (20.0, 0.0, 0.0), (20.0, 20.0, 0.0), (0.0, 20.0, 0.0)]);
  });
  builder.addInstance(chair, translation: (50.0, 0.0, 0.0));
  builder.addInstance(chair, translation: (100.0, 0.0, 0.0), hidden: true);

  builder.addFace(
    [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (100.0, 100.0, 0.0), (0.0, 100.0, 0.0)],
    material: red, layer: roof,
  );

  builder.save('output.skp');
}
```

### Editing an existing file

```dart
final result = openExisting('building.skp');
for (final w in result.warnings) print('not fully reproduced: $w');

result.builder.addCircle((0.0, 0.0, 100.0), (0.0, 0.0, 1.0), 50.0);
result.builder.save('building_edited.skp');
```

`result.warnings` is the honest account of what couldn't be faithfully
reproduced from that specific source file. Every material/layer the
source had is reachable on `result.builder.materialsByName`/
`layersByName` without a separate lookup, and `result.definitions` maps
each replayed component definition's own name to its builder for placing
more instances of something the source already defined.

### Generating code from a file

`toDartCode()` takes the opposite approach: instead of a builder you keep
editing, it returns a **string of source code** — a re-runnable
transcript of `create()` calls that rebuilds an equivalent file when run:

```dart
final bytes = await File('building.skp').readAsBytes();
final model = SkpFile.fromBuffer(bytes).parse();
print(toDartCode(model));
```

Useful for handing a real model to an AI coding agent as editable
starting code, or for a diffable, reviewable text representation of a
`.skp` file. Shares the same fidelity scope as `openExisting()` above.

---

## 📐 API Data Model Reference

The public API is designed to mirror the Python reference implementation's
data model (the same shape the C# port also follows):

### `SkpModel`
- `String version` — The parsed SketchUp application version.
- `String? units` — Model unit-system string (e.g., `"Millimeter"`).
- `Map<int, Definition> definitions` — Component/group geometry definitions, keyed by their numeric TLV entity ID.
- `Definition root` — Whatever is placed directly in the model (not inside any component/group).
- `List<Layer> layers` — Layer names, colors, and `hidden` visibility flags.
- `List<Material> materials` — Material names, colors, transparency, and embedded textures.
- `Map<int, Material> materialsById` — Join table from a TLV material ID (`Face.materialId`) to its `Material`.
- `List<Style> styles` — Bundled rendering styles (default front/back face colors).

### `Scene` & GLB Export
- `Scene buildScene()` — Opt-in scene graph flattener; resolves nested instance transforms into world-space meshes.
- `List<GlbPrimitive> glbPrimitives` — Triangulated mesh primitives ready for GPU upload or GLB packaging.
- `Map<String, MeshMetadata> meshIndex` — Metadata map describing each baked mesh primitive, keyed by the matching `GlbPrimitive.geomName`.
- `Uint8List toGlb(Scene scene)` — Serializes a baked scene into binary glTF 2.0 (GLB) bytes.
- `Future<File> exportGlb(Scene scene, String path)` — Exports a baked scene directly to a `.glb` file on disk.

---

## 🏭 Used in Production

OpenSKP powers the SketchUp import pipeline for
[FrameSmart](https://frame-smart.com/) (a 3D collaboration platform with
nearly 200 active users) and [IngeTrazo](https://ingetrazo.com/) (a
SketchUp-alternative 3D modeler with a BIM → IFC bridge). Using OpenSKP in
your own project? [Open an issue](https://github.com/iamahsanmehmood/openskp/issues)
or a PR to get added here.

---

## 📄 License

This library is open-source software licensed under the **MIT License** — see the [LICENSE](https://github.com/iamahsanmehmood/openskp/blob/main/LICENSE) file for details.
