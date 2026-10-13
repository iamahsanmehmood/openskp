"""Wavefront OBJ and MTL text export.

Exports a baked :class:`~openskp.scene.Scene` (see
:func:`openskp.scene.build_scene` / :meth:`SkpFile.build_scene`) to
Wavefront ``.obj`` format and companion ``.mtl`` material library files.
"""

from __future__ import annotations

import pathlib
import re
from typing import IO, Union

from ..scene import Scene


def sanitize_material_name(name: str) -> str:
    """Sanitize material name for Wavefront MTL compliance."""
    clean = re.sub(r'[^\w\.-]', '_', name.strip())
    return clean or "default_material"


def _material_names(scene: Scene) -> list[str]:
    """Assign unique, case-sensitive MTL identifiers without stealing reserved names.

    A colliding name gains ``_<1-based material index>`` and, if needed,
    an additional ``_<n>`` suffix. Non-colliding names stay unchanged.
    """
    bases = [
        sanitize_material_name(mat.get("name") or f"Material_{i}")
        for i, mat in enumerate(scene.gltf_materials)
    ]
    reserved = set(bases)
    used: set[str] = set()
    result = []
    for i, base in enumerate(bases):
        name = base
        if name in used:
            stem = f"{base}_{i + 1}"
            name = stem
            suffix = 1
            while name in used or name in reserved:
                name = f"{stem}_{suffix}"
                suffix += 1
        used.add(name)
        result.append(name)
    return result


def to_mtl(scene: Scene) -> str:
    """Return Wavefront MTL material library text representation for a baked scene.

    Material names are case-sensitive; collisions use ``_<1-based material index>``
    followed by ``_<n>`` when that candidate is also reserved.

    Args:
        scene: The result of :meth:`SkpFile.build_scene`.

    Returns:
        The formatted MTL text string.
    """
    lines: list[str] = [
        "# OpenSKP MTL Material Library Export",
        f"# Materials: {len(scene.gltf_materials)}",
        "",
    ]

    names = _material_names(scene)
    for idx, mat in enumerate(scene.gltf_materials):
        mat_name = names[idx]

        pbr = mat.get("pbrMetallicRoughness", {})
        base_color = pbr.get("baseColorFactor", [0.8, 0.8, 0.8, 1.0])
        r = float(base_color[0]) if len(base_color) > 0 else 0.8
        g = float(base_color[1]) if len(base_color) > 1 else 0.8
        b = float(base_color[2]) if len(base_color) > 2 else 0.8
        a = float(base_color[3]) if len(base_color) > 3 else 1.0

        lines.extend([
            f"newmtl {mat_name}",
            "Ka 1.000000 1.000000 1.000000",
            f"Kd {r:.6f} {g:.6f} {b:.6f}",
            "Ks 0.200000 0.200000 0.200000",
            "Ns 32.000000",
            f"d {a:.6f}",
            "illum 2",
        ])

        texture_path = mat.get("texture_path")
        if texture_path:
            tex_name = pathlib.Path(texture_path).name
            lines.append(f"map_Kd {tex_name}")

        lines.append("")

    return "\n".join(lines)


def to_obj(scene: Scene, mtl_filename: str | None = None) -> str:
    """Return Wavefront OBJ text representation for a baked scene.

    Material names are case-sensitive; collisions use ``_<1-based material index>``
    followed by ``_<n>`` when that candidate is also reserved.

    Args:
        scene: The result of :meth:`SkpFile.build_scene`.
        mtl_filename: Optional companion `.mtl` filename to reference.

    Returns:
        The formatted OBJ text string.
    """
    lines: list[str] = [
        "# OpenSKP OBJ Export",
        f"# Primitives: {len(scene.glb_primitives)}",
    ]

    if mtl_filename:
        lines.append(f"mtllib {mtl_filename}")

    lines.append("")

    names = _material_names(scene)
    vert_offset = 1
    uv_offset = 1
    norm_offset = 1

    for prim in scene.glb_primitives:
        lines.append(f"o {prim.geom_name}")

        vert_count = len(prim.positions) // 3
        for i in range(vert_count):
            x = prim.positions[i * 3]
            y = prim.positions[i * 3 + 1]
            z = prim.positions[i * 3 + 2]
            lines.append(f"v {x:.6f} {y:.6f} {z:.6f}")

        uv_count = len(prim.uvs) // 2 if prim.uvs else 0
        for i in range(uv_count):
            u = prim.uvs[i * 2]
            v = prim.uvs[i * 2 + 1]
            lines.append(f"vt {u:.6f} {v:.6f}")

        norm_count = len(prim.normals) // 3 if prim.normals else 0
        for i in range(norm_count):
            nx = prim.normals[i * 3]
            ny = prim.normals[i * 3 + 1]
            nz = prim.normals[i * 3 + 2]
            lines.append(f"vn {nx:.6f} {ny:.6f} {nz:.6f}")

        mat_idx = prim.material_index
        if 0 <= mat_idx < len(scene.gltf_materials):
            lines.append(f"usemtl {names[mat_idx]}")

        tri_count = len(prim.indices) // 3
        has_uvs = uv_count == vert_count
        has_normals = norm_count == vert_count

        for i in range(tri_count):
            i0 = prim.indices[i * 3]
            i1 = prim.indices[i * 3 + 1]
            i2 = prim.indices[i * 3 + 2]

            v0 = i0 + vert_offset
            v1 = i1 + vert_offset
            v2 = i2 + vert_offset

            if has_uvs and has_normals:
                vt0 = i0 + uv_offset
                vt1 = i1 + uv_offset
                vt2 = i2 + uv_offset
                vn0 = i0 + norm_offset
                vn1 = i1 + norm_offset
                vn2 = i2 + norm_offset
                lines.append(f"f {v0}/{vt0}/{vn0} {v1}/{vt1}/{vn1} {v2}/{vt2}/{vn2}")
            elif has_uvs:
                vt0 = i0 + uv_offset
                vt1 = i1 + uv_offset
                vt2 = i2 + uv_offset
                lines.append(f"f {v0}/{vt0} {v1}/{vt1} {v2}/{vt2}")
            elif has_normals:
                vn0 = i0 + norm_offset
                vn1 = i1 + norm_offset
                vn2 = i2 + norm_offset
                lines.append(f"f {v0}//{vn0} {v1}//{vn1} {v2}//{vn2}")
            else:
                lines.append(f"f {v0} {v1} {v2}")

        vert_offset += vert_count
        if has_uvs:
            uv_offset += uv_count
        if has_normals:
            norm_offset += norm_count

        lines.append("")

    return "\n".join(lines)


def _write_obj(scene: Scene, fp: IO[str], mtl_filename: str | None = None) -> None:
    """Write OBJ records for every baked primitive to an open text stream."""
    fp.write(to_obj(scene, mtl_filename))


def export(
    scene: Scene,
    output_path: Union[str, pathlib.Path],
    export_mtl: bool = True
) -> None:
    """Export a baked scene to Wavefront OBJ format with optional MTL material library.

    Args:
        scene: The result of :meth:`SkpFile.build_scene`.
        output_path: Destination file path (should end in ``.obj``).
        export_mtl: Whether to export companion `.mtl` file alongside `.obj`.
    """
    out = pathlib.Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    mtl_name = f"{out.stem}.mtl" if export_mtl else None

    with open(out, "w", encoding="utf-8") as fp:
        fp.write(to_obj(scene, mtl_name))

    if export_mtl and mtl_name:
        mtl_path = out.parent / mtl_name
        with open(mtl_path, "w", encoding="utf-8") as fp:
            fp.write(to_mtl(scene))


__all__ = ["to_obj", "to_mtl", "export"]

