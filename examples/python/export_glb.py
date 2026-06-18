#!/usr/bin/env python3
"""
OpenSKP — Export to GLB Example

Converts a SketchUp file to GLB (binary glTF 2.0) format
for use in Three.js, Blender, or any 3D viewer.
"""

from openskp import SkpFile
from openskp.export import glb

# Parse the SKP file
skp = SkpFile.open("model.skp")
model = skp.parse()

# Export to GLB
glb.export(model, "output.glb")
print(f"GLB exported successfully!")

# Export to GLB with custom options
glb.export(model, "output_yup.glb", coordinate_system="y-up", units="mm")
