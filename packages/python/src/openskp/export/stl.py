"""STL (Standard Triangle Language) export module for OpenSKP.

Exports a baked :class:`~openskp.scene.Scene` to STL format (ASCII or Binary)
for 3D printing and CAD interchange.
"""

from __future__ import annotations

import math
import pathlib
import struct
from typing import Union

from ..scene import Scene


def _calculate_normal(
    v0: tuple[float, float, float],
    v1: tuple[float, float, float],
    v2: tuple[float, float, float],
) -> tuple[float, float, float]:
    """Calculate normalized normal vector from 3 vertices."""
    e1x, e1y, e1z = v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2]
    e2x, e2y, e2z = v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2]

    nx = e1y * e2z - e1z * e2y
    ny = e1z * e2x - e1x * e2z
    nz = e1x * e2y - e1y * e2x

    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length > 1e-12:
        return (nx / length, ny / length, nz / length)
    return (0.0, 0.0, 0.0)


def to_stl_ascii(scene: Scene, scale: float = 1.0) -> str:
    """Serialize a baked scene to ASCII STL text format.

    Args:
        scene: The baked scene returned by :meth:`SkpFile.build_scene`.
        scale: Scale factor for vertex coordinates (e.g. 1.0 for metres,
            1000.0 to convert metres to millimetres for 3D slicers).

    Returns:
        The formatted ASCII STL text string.
    """
    lines: list[str] = ["solid OpenSKP_Model"]

    for prim in scene.glb_primitives:
        tri_count = len(prim.indices) // 3
        for i in range(tri_count):
            i0 = prim.indices[i * 3]
            i1 = prim.indices[i * 3 + 1]
            i2 = prim.indices[i * 3 + 2]

            v0 = (
                prim.positions[i0 * 3] * scale,
                prim.positions[i0 * 3 + 1] * scale,
                prim.positions[i0 * 3 + 2] * scale,
            )
            v1 = (
                prim.positions[i1 * 3] * scale,
                prim.positions[i1 * 3 + 1] * scale,
                prim.positions[i1 * 3 + 2] * scale,
            )
            v2 = (
                prim.positions[i2 * 3] * scale,
                prim.positions[i2 * 3 + 1] * scale,
                prim.positions[i2 * 3 + 2] * scale,
            )

            nx, ny, nz = _calculate_normal(v0, v1, v2)

            lines.append(f"  facet normal {nx:.6f} {ny:.6f} {nz:.6f}")
            lines.append("    outer loop")
            lines.append(f"      vertex {v0[0]:.6f} {v0[1]:.6f} {v0[2]:.6f}")
            lines.append(f"      vertex {v1[0]:.6f} {v1[1]:.6f} {v1[2]:.6f}")
            lines.append(f"      vertex {v2[0]:.6f} {v2[1]:.6f} {v2[2]:.6f}")
            lines.append("    endloop")
            lines.append("  endfacet")

    lines.append("endsolid OpenSKP_Model\n")
    return "\n".join(lines)


def to_stl_binary(scene: Scene, scale: float = 1.0) -> bytes:
    """Serialize a baked scene to Binary STL format.

    Args:
        scene: The baked scene returned by :meth:`SkpFile.build_scene`.
        scale: Scale factor for vertex coordinates (e.g. 1.0 for metres,
            1000.0 to convert metres to millimetres for 3D slicers).

    Returns:
        The packed Little-Endian Binary STL byte array.
    """
    total_triangles = sum(len(p.indices) // 3 for p in scene.glb_primitives)

    # 80-byte header
    header = b"# OpenSKP Binary STL Export"
    header = header.ljust(80, b"\x00")

    chunks: list[bytes] = [header, struct.pack("<I", total_triangles)]

    for prim in scene.glb_primitives:
        tri_count = len(prim.indices) // 3
        for i in range(tri_count):
            i0 = prim.indices[i * 3]
            i1 = prim.indices[i * 3 + 1]
            i2 = prim.indices[i * 3 + 2]

            v0 = (
                prim.positions[i0 * 3] * scale,
                prim.positions[i0 * 3 + 1] * scale,
                prim.positions[i0 * 3 + 2] * scale,
            )
            v1 = (
                prim.positions[i1 * 3] * scale,
                prim.positions[i1 * 3 + 1] * scale,
                prim.positions[i1 * 3 + 2] * scale,
            )
            v2 = (
                prim.positions[i2 * 3] * scale,
                prim.positions[i2 * 3 + 1] * scale,
                prim.positions[i2 * 3 + 2] * scale,
            )

            nx, ny, nz = _calculate_normal(v0, v1, v2)

            # Pack: 3 floats normal + 3x3 floats vertices + 1 uint16 attribute count
            tri_data = struct.pack(
                "<12fH",
                nx,
                ny,
                nz,
                v0[0],
                v0[1],
                v0[2],
                v1[0],
                v1[1],
                v1[2],
                v2[0],
                v2[1],
                v2[2],
                0,
            )
            chunks.append(tri_data)

    return b"".join(chunks)


def export(
    scene: Scene,
    output_path: Union[str, pathlib.Path],
    binary: bool = False,
    scale: float = 1.0,
) -> None:
    """Export a baked scene to an STL file.

    Args:
        scene: The baked scene returned by :meth:`SkpFile.build_scene`.
        output_path: Destination file path (.stl).
        binary: If True, writes binary STL. Otherwise writes ASCII STL.
        scale: Scale factor for vertex coordinates (e.g. 1000.0 for mm).
    """
    if scene is None:
        raise ValueError("scene cannot be None")

    out = pathlib.Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if binary:
        data = to_stl_binary(scene, scale=scale)
        with open(out, "wb") as fp:
            fp.write(data)
    else:
        text = to_stl_ascii(scene, scale=scale)
        with open(out, "w", encoding="utf-8") as fp:
            fp.write(text)


__all__ = ["to_stl_ascii", "to_stl_binary", "export"]
