"""Wavefront OBJ text export.

Exports a baked :class:`~openskp.scene.Scene` (see
:func:`openskp.scene.build_scene` / :meth:`SkpFile.build_scene`) to a
simple ``.obj`` file: one ``o`` group per GLB primitive, with ``v``
(vertex) and ``f`` (face) records. No materials, normals, or texture
coordinates are written - this exporter is intended for quick debugging
and interchange with tools that accept minimal OBJ.
"""

from __future__ import annotations

import pathlib
from typing import IO, Union

from ..scene import Scene


def to_obj(scene: Scene) -> str:
    """Return Wavefront OBJ text representation for a baked scene.

    Args:
        scene: The result of :meth:`SkpFile.build_scene`.

    Returns:
        The formatted OBJ text string.
    """
    lines: list[str] = [
        "# OpenSKP OBJ Export",
        f"# Primitives: {len(scene.glb_primitives)}",
        "",
    ]
    vert_offset = 1  # OBJ indices are 1-based
    for prim in scene.glb_primitives:
        lines.append(f"o {prim.geom_name}")
        vert_count = len(prim.positions) // 3
        for i in range(vert_count):
            x = prim.positions[i * 3]
            y = prim.positions[i * 3 + 1]
            z = prim.positions[i * 3 + 2]
            lines.append(f"v {x:.6f} {y:.6f} {z:.6f}")

        tri_count = len(prim.indices) // 3
        for i in range(tri_count):
            i0 = prim.indices[i * 3] + vert_offset
            i1 = prim.indices[i * 3 + 1] + vert_offset
            i2 = prim.indices[i * 3 + 2] + vert_offset
            lines.append(f"f {i0} {i1} {i2}")

        vert_offset += vert_count
        lines.append("")

    return "\n".join(lines)


def _write_obj(scene: Scene, fp: IO[str]) -> None:
    """Write OBJ records for every baked primitive to an open text stream."""
    fp.write(to_obj(scene))


def export(scene: Scene, output_path: Union[str, pathlib.Path]) -> None:
    """Export a baked scene to Wavefront OBJ format.

    Coordinates are written exactly as `scene` provides them: metres,
    Y-up (glTF convention) - the same space this project's GLB output uses.

    Args:
        scene: The result of :meth:`SkpFile.build_scene` /
            :func:`openskp.scene.build_scene`.
        output_path: Destination file path (should end in ``.obj``).
    """
    out = pathlib.Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    with open(out, "w", encoding="utf-8") as fp:
        fp.write(to_obj(scene))


__all__ = ["to_obj", "export"]
