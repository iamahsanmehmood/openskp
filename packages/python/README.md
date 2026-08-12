# OpenSKP — Python Package

Open-source SketchUp (`.skp`) binary file parser. Extract geometry, metadata,
layers, and materials from SketchUp files without requiring SketchUp itself.

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

# Export to Wavefront OBJ - takes a built Scene
obj.export(scene, "output.obj")

# Export to STL (3D Printing ASCII/Binary)
stl.export(scene, "output.stl", binary=True)

# Export to PLY (Stanford 3D Triangle Mesh)
ply.export(scene, "output.ply", binary=True)

# Export to AutoCAD 3D DXF (AutoCAD R2000 compliant)
dxf.export(scene, "output.dxf")

# Export to IFC4 / BIM (ISO 10303-21 STEP ASCII format)
ifc.export(scene, "output.ifc")

# Export metadata as JSON - pass scene= to include the resolved hierarchy
meta = json_export.to_dict(model, scene=scene)
json_export.export(model, "output.json", scene=scene)
```

## Package Structure

| Module | Purpose |
|---|---|
| `openskp.parser` | TLV binary parser for SketchUp's internal format |
| `openskp.model` | Dataclasses for geometry, layers, materials |
| `openskp.vff` | VFF/ZIP container handling |
| `openskp.geometry` | Geometry extraction from parsed nodes |
| `openskp.triangulator` | 3D planar polygon triangulation |
| `openskp.materials` | Material and layer XML parsing |
| `openskp.metadata` | Dynamic properties and scene hierarchy |
| `openskp.transforms` | 3D matrix transforms and coordinate conversion |
| `openskp.export` | GLB, OBJ, STL, PLY, DXF, and JSON exporters |

## Requirements

- Python ≥ 3.9
- NumPy ≥ 1.20
- Trimesh ≥ 3.0
- Shapely ≥ 1.8

## License

MIT — see the [root repository](https://github.com/iamahsanmehmood/openskp) for
full documentation and multi-language packages.
