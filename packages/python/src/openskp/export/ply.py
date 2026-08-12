"""PLY (Polygon File Format) export module for OpenSKP.

Exports a baked :class:`~openskp.scene.Scene` to PLY format (ASCII or Binary)
for 3D scanning, point clouds, and mesh processing.
"""

from __future__ import annotations

import pathlib
import struct
from typing import Union

from ..scene import Scene


def _get_material_rgba(scene: Scene, mat_idx: int) -> tuple[int, int, int, int]:
    """Extract 0-255 uchar RGBA color tuple for a primitive's material index."""
    if 0 <= mat_idx < len(scene.gltf_materials):
        mat = scene.gltf_materials[mat_idx]
        color = mat.get("baseColorFactor") if isinstance(mat, dict) else getattr(mat, "base_color_factor", None)
        if color and isinstance(color, (list, tuple)) and len(color) >= 4:
            r = max(0, min(255, int(round(color[0] * 255.0))))
            g = max(0, min(255, int(round(color[1] * 255.0))))
            b = max(0, min(255, int(round(color[2] * 255.0))))
            a = max(0, min(255, int(round(color[3] * 255.0))))
            return (r, g, b, a)
    return (200, 200, 200, 255)


def to_ply_ascii(scene: Scene) -> str:
    """Serialize a baked scene to ASCII PLY text format.

    Args:
        scene: The baked scene returned by :meth:`SkpFile.build_scene`.

    Returns:
        The formatted ASCII PLY text string.
    """
    total_vertices = sum(len(p.positions) // 3 for p in scene.glb_primitives)
    total_faces = sum(len(p.indices) // 3 for p in scene.glb_primitives)

    lines: list[str] = [
        "ply",
        "format ascii 1.0",
        "comment Created by OpenSKP",
        f"element vertex {total_vertices}",
        "property float x",
        "property float y",
        "property float z",
        "property float nx",
        "property float ny",
        "property float nz",
        "property float u",
        "property float v",
        "property uchar red",
        "property uchar green",
        "property uchar blue",
        "property uchar alpha",
        f"element face {total_faces}",
        "property list uchar int vertex_indices",
        "end_header",
    ]

    for prim in scene.glb_primitives:
        r, g, b, a = _get_material_rgba(scene, prim.material_index)
        vert_count = len(prim.positions) // 3
        for i in range(vert_count):
            px = prim.positions[i * 3]
            py = prim.positions[i * 3 + 1]
            pz = prim.positions[i * 3 + 2]

            nx = prim.normals[i * 3] if i * 3 < len(prim.normals) else 0.0
            ny = prim.normals[i * 3 + 1] if i * 3 + 1 < len(prim.normals) else 0.0
            nz = prim.normals[i * 3 + 2] if i * 3 + 2 < len(prim.normals) else 0.0

            u = prim.uvs[i * 2] if i * 2 < len(prim.uvs) else 0.0
            v = prim.uvs[i * 2 + 1] if i * 2 + 1 < len(prim.uvs) else 0.0

            lines.append(
                f"{px:.6f} {py:.6f} {pz:.6f} {nx:.6f} {ny:.6f} {nz:.6f} {u:.6f} {v:.6f} {r} {g} {b} {a}"
            )

    vert_offset = 0
    for prim in scene.glb_primitives:
        tri_count = len(prim.indices) // 3
        for i in range(tri_count):
            i0 = prim.indices[i * 3] + vert_offset
            i1 = prim.indices[i * 3 + 1] + vert_offset
            i2 = prim.indices[i * 3 + 2] + vert_offset
            lines.append(f"3 {i0} {i1} {i2}")

        vert_offset += len(prim.positions) // 3

    return "\n".join(lines) + "\n"


def to_ply_binary(scene: Scene) -> bytes:
    """Serialize a baked scene to Little-Endian Binary PLY format.

    Args:
        scene: The baked scene returned by :meth:`SkpFile.build_scene`.

    Returns:
        The packed Little-Endian Binary PLY byte array.
    """
    total_vertices = sum(len(p.positions) // 3 for p in scene.glb_primitives)
    total_faces = sum(len(p.indices) // 3 for p in scene.glb_primitives)

    header = (
        f"ply\n"
        f"format binary_little_endian 1.0\n"
        f"comment Created by OpenSKP\n"
        f"element vertex {total_vertices}\n"
        f"property float x\n"
        f"property float y\n"
        f"property float z\n"
        f"property float nx\n"
        f"property float ny\n"
        f"property float nz\n"
        f"property float u\n"
        f"property float v\n"
        f"property uchar red\n"
        f"property uchar green\n"
        f"property uchar blue\n"
        f"property uchar alpha\n"
        f"element face {total_faces}\n"
        f"property list uchar int vertex_indices\n"
        f"end_header\n"
    ).encode("ascii")

    chunks: list[bytes] = [header]

    for prim in scene.glb_primitives:
        r, g, b, a = _get_material_rgba(scene, prim.material_index)
        vert_count = len(prim.positions) // 3
        for i in range(vert_count):
            px = prim.positions[i * 3]
            py = prim.positions[i * 3 + 1]
            pz = prim.positions[i * 3 + 2]

            nx = prim.normals[i * 3] if i * 3 < len(prim.normals) else 0.0
            ny = prim.normals[i * 3 + 1] if i * 3 + 1 < len(prim.normals) else 0.0
            nz = prim.normals[i * 3 + 2] if i * 3 + 2 < len(prim.normals) else 0.0

            u = prim.uvs[i * 2] if i * 2 < len(prim.uvs) else 0.0
            v = prim.uvs[i * 2 + 1] if i * 2 + 1 < len(prim.uvs) else 0.0

            vert_bytes = struct.pack(
                "<8f4B", px, py, pz, nx, ny, nz, u, v, r, g, b, a
            )
            chunks.append(vert_bytes)

    vert_offset = 0
    for prim in scene.glb_primitives:
        tri_count = len(prim.indices) // 3
        for i in range(tri_count):
            i0 = prim.indices[i * 3] + vert_offset
            i1 = prim.indices[i * 3 + 1] + vert_offset
            i2 = prim.indices[i * 3 + 2] + vert_offset

            face_bytes = struct.pack("<B3i", 3, i0, i1, i2)
            chunks.append(face_bytes)

        vert_offset += len(prim.positions) // 3

    return b"".join(chunks)


def export(
    scene: Scene,
    output_path: Union[str, pathlib.Path],
    binary: bool = False,
) -> None:
    """Export a baked scene to a PLY file.

    Args:
        scene: The baked scene returned by :meth:`SkpFile.build_scene`.
        output_path: Destination file path (.ply).
        binary: If True, writes binary PLY. Otherwise writes ASCII PLY.
    """
    if scene is None:
        raise ValueError("scene cannot be None")

    out = pathlib.Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if binary:
        data = to_ply_binary(scene)
        with open(out, "wb") as fp:
            fp.write(data)
    else:
        text = to_ply_ascii(scene)
        with open(out, "w", encoding="utf-8") as fp:
            fp.write(text)


__all__ = ["to_ply_ascii", "to_ply_binary", "export"]
