#!/usr/bin/env python3
"""
OpenSKP — Extract Metadata Example

Extracts all metadata from a SketchUp file including
layers, materials, component hierarchy, and dynamic properties.
"""

import json
from openskp import SkpFile
from openskp.export import json_export

# Parse the SKP file
skp = SkpFile.open("model.skp")
model = skp.parse()

# Get full metadata as a dictionary
metadata = json_export.to_dict(model)

# Save to JSON file
with open("metadata.json", "w") as f:
    json.dump(metadata, f, indent=2)

print(f"Metadata exported with {metadata['total_meshes']} meshes")

# Access specific metadata
print("\n--- Scene Hierarchy ---")
def print_hierarchy(node, indent=0):
    prefix = "  " * indent
    name = node.get("name", "unnamed")
    layer = node.get("layer", "")
    print(f"{prefix}• {name} [{layer}]")
    for child in node.get("children", []):
        print_hierarchy(child, indent + 1)

print_hierarchy(metadata["scene_hierarchy"])
