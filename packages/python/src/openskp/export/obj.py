"""Wavefront OBJ text export.

Produces a simple ``.obj`` file with ``v`` (vertex) and ``f`` (face)
records.  No materials, normals, or texture coordinates are written —
this exporter is intended for quick debugging and interchange with tools
that accept minimal OBJ.
"""

from __future__ import annotations

import pathlib
from typing import IO, Union

from ..model import SkpModel
from ..transforms import z_up_to_y_up, INCHES_TO_METRES


def _write_obj(model: SkpModel, fp: IO[str]) -> None:
    """Write OBJ records to an open text stream.

    Args:
        model: A fully parsed :class:`SkpModel`.
        fp: Writable text file handle.
    """
    fp.write("# OpenSKP OBJ Export\n")
    fp.write(f"# Definitions: {len(model.definitions)}\n\n")

    global_vert_offset: int = 1  # OBJ indices are 1-based

    for def_id, mesh_data in model.mesh_index.items():
        positions = mesh_data.get("positions", [])
        indices = mesh_data.get("indices", [])

        if not positions or not indices:
            continue

        defn = model.definitions.get(def_id)
        name = defn.name if defn and defn.name else f"definition_{def_id}"
        fp.write(f"o {name}\n")

        # Write vertices
        vert_count = len(positions) // 3
        for i in range(vert_count):
            x = positions[i * 3] * INCHES_TO_METRES
            y = positions[i * 3 + 1] * INCHES_TO_METRES
            z = positions[i * 3 + 2] * INCHES_TO_METRES
            ox, oy, oz = z_up_to_y_up(x, y, z)
            fp.write(f"v {ox:.6f} {oy:.6f} {oz:.6f}\n")

        # Write faces (triangle indices, adjusted for global offset)
        tri_count = len(indices) // 3
        for i in range(tri_count):
            i0 = indices[i * 3] + global_vert_offset
            i1 = indices[i * 3 + 1] + global_vert_offset
            i2 = indices[i * 3 + 2] + global_vert_offset
            fp.write(f"f {i0} {i1} {i2}\n")

        global_vert_offset += vert_count
        fp.write("\n")


def export(
    model: SkpModel,
    output_path: Union[str, pathlib.Path],
) -> None:
    """Export a parsed model to Wavefront OBJ format.

    Coordinates are converted from SketchUp's Z-up inches to Y-up metres.

    Args:
        model: A fully parsed :class:`SkpModel` with a populated
            :attr:`~SkpModel.mesh_index`.
        output_path: Destination file path (should end in ``.obj``).
    """
    out = pathlib.Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    with open(out, "w", encoding="utf-8") as fp:
        _write_obj(model, fp)
