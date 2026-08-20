# OpenSKP

**The open-source SketchUp (`.skp`) file parser, writer, and converter — C# / .NET edition.**

Parse, write, and convert `.skp` files without SketchUp. No SDK. No license. Just code.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/iamahsanmehmood/openskp/blob/main/LICENSE)
[![NuGet](https://img.shields.io/nuget/v/OpenSkp.svg?logo=nuget&logoColor=white)](https://www.nuget.org/packages/OpenSkp)

🏠 [openskp.com](https://openskp.com) · 🌐 [Try the Live Web Viewer](https://iamahsanmehmood.github.io/openskp/) · 📖 [Docs](https://iamahsanmehmood.github.io/openskp/docs/) · [Changelog](https://github.com/iamahsanmehmood/openskp/blob/main/CHANGELOG.md)

> [!IMPORTANT]
> This project was built by reverse engineering a proprietary binary format. It is not affiliated with or endorsed by Trimble Inc. or SketchUp.

---

## 🌟 What is OpenSKP?

OpenSKP is the first and only open-source, cross-platform parser for
SketchUp binary files — reverse-engineered from both the modern **VFF
container** (SketchUp 2021+) and the classic **MFC `CArchive`** container
(SketchUp 2013–2020). It gives .NET developers (desktop, cloud, and
mobile) full programmatic access to geometry, materials, components,
layers, and metadata, with no SketchUp installation and no proprietary SDK
required. The same parser and export API also ship as first-class packages
for Python, TypeScript, Dart, and C++ — see the
[project README](https://github.com/iamahsanmehmood/openskp) for the full
cross-language picture.

This package can also *write* new `.skp` files from scratch, and edit
existing ones, validated feature-by-feature against the real SketchUp
SDK (see [Writing](#-writing) below).

## ✨ Features

- **Full-fidelity parsing** — vertices, edges, faces, normals, UV
  coordinates, nested component hierarchies, layers/tags, materials,
  textures, styles, and dynamic-component attributes.
- **Both SketchUp file generations** — modern VFF (2021+) and legacy MFC
  (2013–2020) containers, transparently, behind one `SkpFile.Open()` call.
- **Scene baking** — an opt-in `SkpFile.BuildScene()` pass resolves the
  full placed scene graph to world-space, triangulated, export-ready
  geometry.
- **Native multi-format conversion** — glTF (GLB), Wavefront OBJ/MTL, STL,
  PLY, AutoCAD DXF (3DFACE and Polyface Mesh), IFC4 (BIM/ISO 10303-21
  STEP), and JSON — all written from scratch, no third-party CAD/BIM SDK
  involved. The DXF writer is verified against real desktop AutoCAD, not
  just lenient DXF readers.
- **No practical file-size ceiling** — unlike the CLR's ~2.1 GB array/
  `MemoryStream` cap, OpenSKP's `ChunkedBuffer` and 64-bit TLV offsets
  remove that limit entirely: verified against a real 620 MB `.skp` file
  (153,586 definitions) with zero special configuration.
- **Structured observability** — opt-in progress reporting and
  location-carrying parse errors for debugging malformed or unusual files.
- **Write support** — build new legacy-format `.skp` files from scratch:
  geometry (including true, editable circular/arc curves, freeform
  polylines, faces with holes cut out, and non-planar auto-triangulation),
  materials (solid + PNG/JPEG textures), layers, nested component
  definitions and groups, instance rotation/visibility, and custom
  attribute dictionaries — or load and extend an existing file with
  `SkpEdit.OpenExisting()`. No SDK involved; every feature validated
  against the real SketchUp SDK. See [Writing](#-writing) below.

### 🌐 [Try the Live Web Viewer (Drag-and-Drop)](https://iamahsanmehmood.github.io/openskp/)

---

## 🚀 Installation

Install the package via the .NET CLI:

```bash
dotnet add package OpenSkp
```

Or via the NuGet Package Manager:

```powershell
Install-Package OpenSkp
```

---

## 💻 Quick Start

### 1. Parse an SKP File
Open and parse a SketchUp model to inspect its metadata:

```csharp
using System;
using OpenSkp;

class Program
{
    static void Main()
    {
        // Open and decode an SKP file
        SkpModel model = SkpFile.Open("house.skp");

        Console.WriteLine($"SketchUp File Version: {model.Version}");
        
        // Print Layer Names
        Console.WriteLine("Layers:");
        foreach (var layer in model.Layers)
        {
            Console.WriteLine($"- {layer.Name} (RGB: {layer.ColorR}, {layer.ColorG}, {layer.ColorB})");
        }

        // Print Materials List
        Console.WriteLine("Materials:");
        foreach (var material in model.Materials)
        {
            Console.WriteLine($"- {material.Name} (Transparency: {material.Transparency})");
        }

        // Walk component definitions and their geometry
        foreach (var kvp in model.Definitions)
        {
            Console.WriteLine($"Definition {kvp.Key}: {kvp.Value.Name} - {kvp.Value.Vertices.Count} vertices, {kvp.Value.Faces.Count} faces");
        }

        // model.Root holds whatever is placed directly in the model (not
        // inside any component/group), including root-level instances.
        Console.WriteLine($"Root-level instances: {model.Root.Instances.Count}");

        // Build scene graph into world-space meshes and export to DXF / IFC / GLB / OBJ / STL / PLY
        Scene scene = SkpFile.BuildScene("house.skp");
        DxfExport.ExportDxf(scene, "house.dxf");
        IfcExport.ExportIfc(scene, "house.ifc");
        GlbExport.ExportGlb(scene, "house.glb");
        ObjExport.ExportObj(scene, "house.obj");
        StlExport.ExportStl(scene, "house.stl");
        PlyExport.ExportPly(scene, "house.ply");
    }
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
auto-triangulation. `SkpEdit.OpenExisting()` loads an *existing*
legacy-format file and rebuilds it as a new builder, so more geometry can
be added before saving. See [`Create.cs`](Create.cs) for the full scope
notes.

```csharp
using OpenSkp;

var builder = SkpCreate.NewFile();

// Materials and layers
int red = builder.AddMaterial("Red", (255, 0, 0));
int roof = builder.AddLayer("Roof", color: (180, 60, 40));

// All AddComponentDefinition/AddGroup calls must come before any
// AddInstance/AddFace call - placing anything locks in the file's
// internal slot numbering for everything after it
var chair = builder.AddComponentDefinition("Chair");
using (chair)
{
    chair.AddFace(new (double, double, double)[] { (0, 0, 0), (20, 0, 0), (20, 20, 0), (0, 20, 0) });
}
builder.AddInstance(chair, translation: (50, 0, 0));
builder.AddInstance(chair, translation: (100, 0, 0), hidden: true);

builder.AddFace(
    new (double, double, double)[] { (0, 0, 0), (100, 0, 0), (100, 100, 0), (0, 100, 0) },
    material: red, layer: roof);

builder.Save("output.skp");
```

### Editing an existing file

```csharp
var result = SkpEdit.OpenExisting("building.skp");
foreach (var w in result.Warnings) Console.WriteLine($"not fully reproduced: {w}");

result.Builder.AddCircle((0, 0, 100), (0, 0, 1), 50);
result.Builder.Save("building_edited.skp");
```

`result.Warnings` is the honest account of what couldn't be faithfully
reproduced from that specific source file. Every material/layer the
source had is reachable on `result.Builder.MaterialsByName`/
`LayersByName` without a separate lookup, and `result.Definitions` maps
each replayed component definition's own name to its builder for placing
more instances of something the source already defined.

---

## ⚙️ Target Frameworks

OpenSKP targets **.NET Standard 2.0**, ensuring full compatibility with:
- **.NET 5 / 6 / 7 / 8 / 9** (Console, Web APIs, ASP.NET Core)
- **.NET Core 2.0+**
- **.NET Framework 4.6.1+** (WPF, WinForms)
- **Mono / Xamarin / .NET MAUI** (iOS, Android)

---

## 🏭 Used in Production

OpenSKP powers the SketchUp import pipeline for
[FrameSmart](https://frame-smart.com/) (a 3D collaboration platform with
nearly 200 active users) and [IngeTrazo](https://ingetrazo.com/) (a
SketchUp-alternative 3D modeler with a BIM → IFC bridge). Using OpenSKP in
your own project? [Open an issue](https://github.com/iamahsanmehmood/openskp/issues)
or a PR to get added here.

---

## 🖥️ Monorepo Package Ecosystem

OpenSKP is designed as a unified cross-platform monorepo:
* [Python Package](https://pypi.org/project/openskp/) (`openskp`)
* [TypeScript / JS Package](https://www.npmjs.com/package/openskp) (`openskp`)
* [.NET Package](https://www.nuget.org/packages/OpenSkp) (`OpenSkp`)
* [Dart Package](https://pub.dev/packages/openskp) (`openskp`)
* [C++ Package](https://github.com/iamahsanmehmood/openskp/releases?q=cpp-) (`OpenSkp`, source releases)

---

## 📄 License

This project is licensed under the **MIT License** — see the [LICENSE](https://github.com/iamahsanmehmood/openskp/blob/main/LICENSE) file for details.
