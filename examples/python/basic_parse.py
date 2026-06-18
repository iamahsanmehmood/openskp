#!/usr/bin/env python3
"""
OpenSKP — Basic Parse Example

Demonstrates how to open and parse a SketchUp file,
access its structure, and print summary information.
"""

from openskp import SkpFile

# Open and parse the SKP file
skp = SkpFile.open("model.skp")
model = skp.parse()

# Print model information
print(f"SketchUp Version: {model.version}")
print(f"Definitions: {len(model.definitions)}")
print(f"Layers: {len(model.layers)}")
print(f"Materials: {len(model.materials)}")

# List all layers
print("\n--- Layers ---")
for layer in model.layers:
    print(f"  {layer.name} (color: rgb({layer.color_r}, {layer.color_g}, {layer.color_b}))")

# List all component definitions
print("\n--- Component Definitions ---")
for def_id, defn in model.definitions.items():
    print(f"  [{def_id}] {defn.name}: {len(defn.faces)} faces, {len(defn.vertices)} vertices")
