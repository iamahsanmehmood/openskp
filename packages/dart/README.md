# OpenSKP — Pure Dart SketchUp Binary Parser

OpenSKP is a pure Dart library designed to parse SketchUp (`.skp`) binary files directly in Dart and Flutter applications without requiring any native Trimble SketchUp SDK dependencies.

[![Pub Version](https://img.shields.io/pub/v/openskp.svg?logo=dart&logoColor=white)](https://pub.dev/packages/openskp)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/iamahsanmehmood/openskp/blob/main/LICENSE)

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
- **TLV Binary Decoding**: Decodes SketchUp's internal Tag-Length-Value records.
- **VFF ZIP Extractors**: Decompresses and validates VFF file containers to extract `model.dat` and material XML assets.
- **Geometry & Hierarchy**: Recovers vertices, edges, face loops, component definitions, and nested instance transformations.
- **Steelframer properties**: Reads custom dynamic component attribute values.

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

---

## 📐 API Data Model Reference

The public API is designed to mirror the Python reference implementation's
data model (the same shape the C# port also follows):

### `SkpModel`
- `String version` — The parsed SketchUp application version.
- `Map<int, Definition> definitions` — Component/group geometry definitions, keyed by their numeric TLV entity ID.
- `Definition root` — Whatever is placed directly in the model (not inside any component/group).
- `List<Layer> layers` — Layer names and color configurations.
- `List<Material> materials` — Material names, color channels, and transparency values.
- `Map<int, Material> materialsById` — Join table from a TLV material ID (`Face.materialId`) to its `Material`.
- `List<Style> styles` — Bundled rendering styles (default front/back face colors).

---

## 📄 License

This library is open-source software licensed under the **MIT License** — see the [LICENSE](https://github.com/iamahsanmehmood/openskp/blob/main/LICENSE) file for details.
