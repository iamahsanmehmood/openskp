"""Write a CSV bill of materials for the placed components in a SketchUp model.

Run with a .skp path, or with no arguments to use the bundled chair and table.
Sizes are world-axis bounding boxes including nested geometry. A definition
placed at different sizes still has one row, with distinct sizes separated by
semicolons. Material names describe instance paint, including inherited paint.
"""

import argparse
from collections import Counter, defaultdict
import csv
from pathlib import Path
import sys

import numpy as np
from openskp import SkpFile


SAMPLE = Path(__file__).resolve().parents[1] / "web-viewer/samples/chair_and_table.skp"
# SketchUp stores vertex coordinates in inches regardless of its display unit.
UNITS = {
    "inch": ("in", 1), "inches": ("in", 1), "foot": ("ft", 1 / 12),
    "feet": ("ft", 1 / 12), "yard": ("yd", 1 / 36),
    "millimeter": ("mm", 25.4), "centimeter": ("cm", 2.54), "meter": ("m", 0.0254),
}


def write_bill(model, output):
    name = (model.units or "inch").strip().lower()
    unit_scale = UNITS.get(name) or UNITS.get(name.removesuffix("s"))
    if unit_scale is None:
        raise ValueError(f"Unsupported model length unit: {model.units!r}")
    unit, factor = unit_scale
    counts = Counter()
    sizes = defaultdict(set)
    paints = defaultdict(set)

    def visit(instance, parent, paint, ancestors):
        key = instance.ref_idx
        if key in ancestors:
            raise ValueError(f"Cyclic component reference: {key}")
        definition = model.definitions[key]
        # Instance.matrix has 9 linear terms, 3 translations and a scalar.
        m = instance.matrix
        placement = np.eye(4)
        placement[:3, :3] = np.reshape(m[:9], (3, 3))
        placement[:3, 3] = m[9:12]
        placement[:3] *= m[12]
        world = parent @ placement
        if instance.material_id is not None:
            paint = instance.material_id

        counts[key] += 1
        if paint in model.materials_by_id:
            paints[key].add(model.materials_by_id[paint].name)

        bounds = []
        if definition.vertices:
            xyz = np.array([(v.x, v.y, v.z) for v in definition.vertices.values()])
            placed = xyz @ world[:3, :3].T + world[:3, 3]
            bounds.extend((placed.min(axis=0), placed.max(axis=0)))
        for child in definition.instances:
            bounds.extend(visit(child, world, paint, ancestors | {key}))
        if not bounds:
            return []
        low = np.min(bounds, axis=0)
        high = np.max(bounds, axis=0)
        sizes[key].add(" x ".join(f"{v:.6g}" for v in (high - low) * factor))
        return [low, high]

    for instance in model.root.instances:
        visit(instance, np.eye(4), None, set())

    writer = csv.writer(output)
    writer.writerow(("component", "quantity", f"size_x_y_z ({unit})", "instance_materials"))
    for key in sorted(counts, key=lambda k: (model.definitions[k].name, k)):
        writer.writerow((model.definitions[key].name, counts[key],
                         "; ".join(sorted(sizes[key])), "; ".join(sorted(paints[key]))))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", nargs="?", type=Path, default=SAMPLE)
    args = parser.parse_args()
    write_bill(SkpFile.open(str(args.model)).parse(), sys.stdout)
