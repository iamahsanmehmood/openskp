# OpenSKP

**The open-source SketchUp (`.skp`) file parser, writer, and converter — Python edition.**

Parse, write, and convert `.skp` files without SketchUp. No SDK. No license. Just code.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![PyPI](https://img.shields.io/pypi/v/openskp.svg?logo=pypi&logoColor=white&label=pypi)](https://pypi.org/project/openskp/)
[![Python Versions](https://img.shields.io/pypi/pyversions/openskp.svg?logo=python&logoColor=white)](https://pypi.org/project/openskp/)

🏠 [openskp.com](https://openskp.com) · 🌐 [Try the Live Web Viewer](https://iamahsanmehmood.github.io/openskp/) · 📖 [Docs](https://iamahsanmehmood.github.io/openskp/docs/) · [Changelog](https://github.com/iamahsanmehmood/openskp/blob/main/CHANGELOG.md)

> [!IMPORTANT]
> This project was built by reverse engineering a proprietary binary format. It is not affiliated with or endorsed by Trimble Inc. or SketchUp.

## What is OpenSKP?

OpenSKP is the first and only open-source, cross-platform parser for SketchUp
binary files — reverse-engineered from both the modern **VFF container**
(SketchUp 2021+) and the classic **MFC `CArchive`** container (SketchUp
2013–2020). It gives you full programmatic access to geometry, materials,
components, layers, and metadata, with no SketchUp installation and no
proprietary SDK required. The same parser and export API also ship as
first-class packages for TypeScript, .NET, Dart, and C++ — see the
[project README](https://github.com/iamahsanmehmood/openskp) for the full
cross-language picture.

This Python package can also *write* new `.skp` files from scratch, and
edit existing ones, validated feature-by-feature against the real
SketchUp SDK (see [Writing](#writing) below) — the same writer is also
available in TypeScript, .NET, Dart, and C++.

## Features

- **Full-fidelity parsing** — vertices, edges, faces, normals, UV
  coordinates, nested component hierarchies, layers/tags, materials,
  textures, styles, and dynamic-component attributes.
- **Both SketchUp file generations** — modern VFF (2021+) and legacy MFC
  (2013–2020) containers, transparently, behind one `parse()` call.
- **Scene baking** — an opt-in `build_scene()` pass resolves the full placed
  scene graph to world-space, triangulated, export-ready geometry.
- **Native multi-format conversion** — glTF (GLB), Wavefront OBJ/MTL, STL,
  PLY, AutoCAD DXF (3DFACE and Polyface Mesh), IFC4 (BIM/ISO 10303-21 STEP),
  and JSON — all written from scratch, no third-party CAD/BIM SDK involved.
  The DXF writer is verified against real desktop AutoCAD, not just lenient
  DXF readers.
- **Streaming / low-memory parsing** — peak memory is bounded by the
  largest single definition, not the whole file.
- **Structured observability** — opt-in progress reporting and
  location-carrying parse errors for debugging malformed or unusual files.
- **Write support** — build new legacy-format `.skp` files from scratch:
  geometry (including true, editable circular/arc curves, freeform
  polylines, faces with holes cut out, and non-planar auto-triangulation),
  materials (solid + PNG/JPEG textures), layers, nested component
  definitions and groups, instance rotation/visibility, and custom
  attribute dictionaries — or load and extend an existing file with
  `open_existing()`. No SDK involved; every feature validated against
  the real SketchUp SDK. Also available in TypeScript, .NET, Dart, and
  C++. See [Writing](#writing) below.

## Installation

```bash
pip install openskp
```

Or install from source:

```bash
git clone https://github.com/iamahsanmehmood/openskp.git
cd openskp/packages/python
pip install -e .
```

## Quick Start

```python
from openskp import SkpFile

# Parse an SKP file
skp = SkpFile.open("model.skp")
model = skp.parse()

# Inspect layers
for layer in model.layers:
    print(f"{layer.name}: rgb({layer.color_r}, {layer.color_g}, {layer.color_b})")

# Inspect definitions (component geometry)
for defn in model.definitions.values():
    print(f"{defn.name}: {len(defn.faces)} faces, {len(defn.vertices)} vertices")

# Inspect resolved, world-space scene hierarchy (opt-in, heavier)
scene = skp.build_scene()
for inst in scene.scene_hierarchy.children:
    print(f"  {inst.name} [{inst.layer}] @ {inst.position_mm}")
```

## Exporting

```python
from openskp.export import glb, obj, stl, ply, dxf, ifc, json_export

# Export to GLB (glTF 2.0 binary) - takes the SkpFile itself
glb.export(skp, "output.glb")

# Export to Wavefront OBJ (+ companion .mtl) - takes a built Scene
obj.export(scene, "output.obj")

# Export to STL (3D Printing ASCII/Binary)
stl.export(scene, "output.stl", binary=True)

# Export to PLY (Stanford 3D Triangle Mesh)
ply.export(scene, "output.ply", binary=True)

# Export to AutoCAD 3D DXF (AutoCAD R2000 compliant, Polyface Mesh by default)
dxf.export(scene, "output.dxf")

# Export to IFC4 / BIM (ISO 10303-21 STEP ASCII format)
ifc.export(scene, "output.ifc")

# Export metadata as JSON - pass scene= to include the resolved hierarchy
meta = json_export.to_dict(model, scene=scene)
json_export.export(model, "output.json", scene=scene)
```

## Writing

OpenSKP can also *create* new `.skp` files from scratch — a genuine,
from-scratch binary writer for the legacy MFC `CArchive` format (SketchUp
2013–2020), with no SketchUp SDK involved at any point. Feature-complete
for common modeling needs and validated feature-by-feature against the
real SketchUp SDK: geometry, materials
(solid + PNG/JPEG textures), layers (with color and default visibility),
component definitions with multiple instances, groups, nested
definitions and nested group instances (an assembly containing instances
or groups of its own sub-parts, to any depth), per-instance rotation and
visibility, explicit per-side texture positioning (on a face of any
orientation), custom key/value attribute dictionaries on
definitions/instances/faces (the same mechanism SketchUp's own "dynamic
component" attributes use), and circular faces and partial arcs (genuine,
editable-by-radius arc/circle entities, not disconnected straight edges
that merely trace that shape), and freeform polyline curves (an arbitrary
chain of edges grouped into one genuine `CCurve` entity) are all
supported, along with faces with one or more holes cut out (a window
opening in a wall, say) and an `add_face(..., auto_triangulate=True)`
fallback for non-planar input. `openskp.open_existing()` can also load an
*existing* legacy-format file and rebuild it as a new builder, so more
geometry can be added to it before saving (see
[Editing an existing file](#editing-an-existing-file) below). See
[`openskp/create.py`](src/openskp/create.py) for the full scope notes.

The same writer is also available in TypeScript, .NET, Dart, and C++ —
see each package's own README, or
[Write capabilities](https://github.com/iamahsanmehmood/openskp/blob/main/docs/DEVELOPER_GUIDE.md#write-capabilities)
in the Developer Guide for the naming convention each one follows.

```python
from openskp import create

builder = create()

# Materials and layers
red = builder.add_material("Red", (255, 0, 0))
brick = builder.add_texture_material("Brick", "brick.png")
roof_layer = builder.add_layer("Roof", color=(180, 60, 40))

# All add_component_definition/add_group calls must come before any
# add_instance/add_face call - placing anything locks in the file's
# slot numbering for what comes after.
with builder.add_component_definition("Chair") as chair:
    chair.add_face(
        [(0, 0, 0), (20, 0, 0), (20, 20, 0), (0, 20, 0)],
        material=brick,
        # Explicit texture positioning: 3 (world point, uv) pairs pin the
        # texture's scale/rotation/offset instead of the default projection.
        front_uv=[((0, 0, 0), (0.0, 0.0)), ((10, 0, 0), (1.0, 0.0)), ((0, 10, 0), (0.0, 1.0))],
    )

# A one-off group (placed automatically when its `with` block exits)
with builder.add_group("Table", translation=(100, 0, 0)) as table:
    table.add_face([(0, 0, 0), (60, 0, 0), (60, 40, 0), (0, 40, 0)])

# Now place instances of the reusable component
builder.add_instance(chair, translation=(0, 0, 0))
# rotation=(axis, angle_radians) is a shortcut for a hand-derived matrix3x3
import math
builder.add_instance(chair, translation=(50, 0, 0), rotation=((0, 0, 1), math.radians(180)))
# hidden=True hides this specific placement (its contents still exist)
builder.add_instance(chair, translation=(100, 0, 0), hidden=True)

# Root-level geometry
builder.add_face(
    [(0, 0, 0), (200, 0, 0), (200, 150, 0), (0, 150, 0)],
    material=red, layer=roof_layer,
)

# A true, editable circular face - not disconnected straight edges
builder.add_circle((100, 75, 0), normal=(0, 0, 1), radius=30, num_segments=24)

# A partial (open) arc - same real curve entity, but edges only, no face
builder.add_arc((100, 75, 0), normal=(0, 0, 1), radius=30, start_angle=0, end_angle=math.pi / 2)

# A freeform polyline - an arbitrary edge chain grouped into one curve
builder.add_polyline([(0, 0, 0), (10, 10, 0), (20, 0, 0), (30, 10, 0)])

# A non-planar "quad" - auto_triangulate fans it into 2 real triangular
# faces instead of raising, the same thing SketchUp's own UI does
warped_quad = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 5)]
builder.add_face(warped_quad, auto_triangulate=True)

# A face with a hole cut out - a window opening in a wall
wall = [(0, 0, 0), (200, 0, 0), (200, 100, 0), (0, 100, 0)]
window = [(80, 30, 0), (120, 30, 0), (120, 70, 0), (80, 70, 0)]
builder.add_face(wall, holes=[window])

builder.save("output.skp")
```

### Editing an existing file

`create()` only ever starts from a blank scaffold. To load an *existing*
legacy-format `.skp` file and add to it, use `open_existing()` instead —
it fully parses the file with OpenSKP's own reader and replays
everything it understood (materials, layers, every component definition,
all root-level geometry/instances) back through the writer's own API,
producing a brand-new file with equivalent content:

```python
from openskp import open_existing

builder, warnings, definitions = open_existing("building.skp")
for w in warnings:
    print("not fully reproduced:", w)

# Every material/layer the source had is already reusable, no separate lookup:
builder.add_circle((0, 0, 100), (0, 0, 1), radius=50, material=builder.materials_by_name.get("Roofing"))
# definitions maps each replayed component's own name to its builder:
builder.add_instance(definitions["Window"], translation=(0, 300, 0))
builder.save("building_edited.skp")
```

`warnings` lists anything the source file had that couldn't be
faithfully reproduced (a projected/draped texture falling back to
default projection, a colorized material's tint, and several others) —
see [`openskp/edit.py`](src/openskp/edit.py) for the complete, itemized
scope.

### Generating code from a file

`to_python_code()` takes the opposite approach: instead of a builder you
keep editing, it returns a **string of source code** — a re-runnable
transcript of `create()` calls that rebuilds an equivalent file when run:

```python
from openskp import SkpFile, to_python_code

model = SkpFile.open("building.skp").parse()
print(to_python_code(model))
```

Useful for handing a real model to an AI coding agent as editable
starting code, or for a diffable, reviewable text representation of a
`.skp` file. Shares the same fidelity scope as `open_existing()` above.

## Package Structure

| Module | Purpose |
|---|---|
| `openskp.parser` | TLV binary parser for SketchUp's internal format |
| `openskp.model` | Dataclasses for geometry, layers, materials |
| `openskp.vff` | VFF/ZIP container handling |
| `openskp.triangulator` | 3D planar polygon triangulation |
| `openskp.materials` | Material and layer XML parsing |
| `openskp.metadata` | Dynamic properties and scene hierarchy |
| `openskp.transforms` | 3D matrix transforms and coordinate conversion |
| `openskp.export` | GLB, OBJ/MTL, STL, PLY, DXF, IFC4, and JSON exporters |
| `openskp.create` | Writer — build new `.skp` files from scratch |
| `openskp.edit` | Load an existing legacy `.skp` file and rebuild it as a new writer |

## Requirements

- Python ≥ 3.9
- NumPy ≥ 1.20
- Trimesh ≥ 3.0
- Shapely ≥ 1.8

## Used in Production

OpenSKP powers the SketchUp import pipeline for
[FrameSmart](https://frame-smart.com/) (a 3D collaboration platform with
nearly 200 active users) and [IngeTrazo](https://ingetrazo.com/) (a
SketchUp-alternative 3D modeler with a BIM → IFC bridge). Using OpenSKP in
your own project? [Open an issue](https://github.com/iamahsanmehmood/openskp/issues)
or a PR to get added here.

## License

MIT — see the [root repository](https://github.com/iamahsanmehmood/openskp) for
full documentation and multi-language packages.
