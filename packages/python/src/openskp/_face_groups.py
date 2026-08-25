"""Local-space face grouping, shared by the baked (:mod:`openskp.scene`) and
instanced (:mod:`openskp.instanced_scene`) scene builders.

Extracted from :mod:`openskp.scene` unchanged (openskp#200, mirroring
TypeScript's ``face-groups.ts``): a definition's faces are grouped by
resolved (color, double_sided, texture) identity in DEFINITION-LOCAL space
(inches, SketchUp Z-up) - exactly what the baked builder assembles just
before applying an instance's world matrix, and exactly what the instanced
builder keeps local and puts on the node instead. Keeping one implementation
is what makes the two paths agree on triangulation, UV seams, normals and
front/back handling by construction rather than by parallel maintenance.

Faithful to the pre-existing baked behavior it was extracted from: an
unpainted face falls back to the caller-supplied ``fallback_color`` for
color, but its material (and therefore texture tile size) is resolved from
the face's OWN ``material_id``/``back_material_id`` only - an instance's
painted material is not consulted for texture purposes here. That is an
existing characteristic of this port (TypeScript's reference additionally
falls back to the inherited material itself for texture tile size on
unpainted faces), preserved rather than changed by this extraction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import _core
from .errors import SkpParseError


def _invert_3x3(m: Tuple[float, ...]) -> Tuple[float, ...]:
    """Inverse of a row-major 3x3 matrix, via the cofactor/adjugate method."""
    a, b, c, d, e, f, g, h, i = m
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-12:
        return (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    inv_det = 1.0 / det
    return (
        (e * i - f * h) * inv_det, (c * h - b * i) * inv_det, (b * f - c * e) * inv_det,
        (f * g - d * i) * inv_det, (a * i - c * g) * inv_det, (c * d - a * f) * inv_det,
        (d * h - e * g) * inv_det, (b * g - a * h) * inv_det, (a * e - b * d) * inv_det,
    )


def face_uv_basis(n: Tuple[float, float, float]) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    """Face-plane basis vectors (xr, yr) for UV projection, from a face
    normal. See ``Face.uv_transform`` in model.py for the recipe this
    implements."""
    nx, ny, nz = n
    # xr = normalize(Z x n) = normalize((-ny, nx, 0))
    cx, cy = -ny, nx
    clen = (cx * cx + cy * cy) ** 0.5
    if clen < 1e-9:
        xr = (1.0, 0.0, 0.0)
        yr = (0.0, 1.0 if nz >= 0 else -1.0, 0.0)
    else:
        xr = (cx / clen, cy / clen, 0.0)
        # yr = n x xr
        yr = (
            ny * xr[2] - nz * xr[1],
            nz * xr[0] - nx * xr[2],
            nx * xr[1] - ny * xr[0],
        )
    return xr, yr


def compute_face_uv(
    p: Tuple[float, float, float],
    xr: Tuple[float, float, float],
    yr: Tuple[float, float, float],
    uv_transform: Optional[Tuple[float, ...]],
    tile_w: float,
    tile_h: float,
) -> Tuple[float, float]:
    """UV of point *p* (inches, local/object space) on a face with the
    given plane basis, per-face ``uv_transform`` (or ``None`` for the
    default projection), and material tile size (inches)."""
    px = p[0] * xr[0] + p[1] * xr[1] + p[2] * xr[2]
    py = p[0] * yr[0] + p[1] * yr[1] + p[2] * yr[2]
    if uv_transform is None:
        return px / tile_w, py / tile_h
    inv = _invert_3x3(uv_transform)
    u = px * inv[0] + py * inv[3] + inv[6]
    v = px * inv[1] + py * inv[4] + inv[7]
    q = px * inv[2] + py * inv[5] + inv[8]
    if abs(q) < 1e-12:
        q = 1.0
    return (u / q) / tile_w, (v / q) / tile_h


def reconstruct_loop_vertices(loop, edges) -> List[int]:
    loop_verts: List[int] = []
    for edge_id, orient in loop:
        if edge_id in edges:
            v1, v2 = edges[edge_id]
            v_start = v1 if orient == 1 else v2
            if not loop_verts or loop_verts[-1] != v_start:
                loop_verts.append(v_start)
    if len(loop_verts) > 1 and loop_verts[0] == loop_verts[-1]:
        loop_verts = loop_verts[:-1]
    return loop_verts


def resolve_material(
    mat_id: Optional[int],
    material_id_to_name: Dict[int, str],
    materials: Dict[str, Any],
    materials_by_folder: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if mat_id is None:
        return None
    mat_name = material_id_to_name.get(mat_id)
    return materials.get(mat_name) or materials_by_folder.get(mat_name)


def resolve_color(mat: Optional[Dict[str, Any]]) -> Optional[Tuple[int, int, int]]:
    if mat is None:
        return None
    c = mat["color"]
    return (c["r"], c["g"], c["b"])


# Key: (color, double_sided, texture_index).
FaceGroupKey = Tuple[Tuple[int, int, int], bool, Optional[int]]


@dataclass
class FaceGroupContext:
    """Everything ``build_local_face_groups`` needs from its caller that
    isn't the builder itself."""

    material_id_to_name: Dict[int, str]
    materials: Dict[str, Any]
    materials_by_folder: Dict[str, Any]
    texture_index_for: Callable[[Optional[Dict[str, Any]]], Optional[int]]
    # Color an unpainted face falls back to (already resolved by the
    # caller: the instance's inherited paint color, or the effective
    # layer's color when nothing is inherited).
    fallback_color: Tuple[int, int, int]
    # Identifies the definition in a triangulation failure.
    definition_id: Any


def _add_face_side(
    face_groups: Dict[FaceGroupKey, Dict[str, Any]],
    builder: Any,
    triangles: List[List[int]],
    fn: Tuple[float, float, float],
    color: Tuple[int, int, int],
    double_sided: bool,
    reverse: bool,
    mat: Optional[Dict[str, Any]],
    uv_transform: Optional[Tuple[float, ...]],
    xr: Tuple[float, float, float],
    yr: Tuple[float, float, float],
    texture_index_for: Callable[[Optional[Dict[str, Any]]], Optional[int]],
) -> None:
    tex = mat.get("texture") if mat else None
    # faces are batched per emitted material, so the texture has to be
    # part of the key too - otherwise two differently-textured faces with
    # the same average color end up in one group with one image
    tex_index = texture_index_for(tex)
    key = (color, double_sided, tex_index)
    group = face_groups.get(key)
    if group is None:
        group = {
            "color": color,
            "double_sided": double_sided,
            "texture_index": tex_index,
            "local_verts": [],
            "local_uvs": [],
            "normals_accum": [],
            "local_faces": [],
            "local_v_map": {},
        }
        face_groups[key] = group

    tile_w = tex.get("x_scale") if tex else None
    tile_h = tex.get("y_scale") if tex else None
    tile_w = tile_w if tile_w and tile_w > 1e-9 else 1.0
    tile_h = tile_h if tile_h and tile_h > 1e-9 else 1.0

    side_normal = (-fn[0], -fn[1], -fn[2]) if reverse else fn

    # Vertices are deduped per (vId, uv) rather than just vId: UVs are
    # inherently per-face, so a vertex position shared by two faces that
    # disagree on texture mapping must become two distinct output
    # vertices (glTF requires position/normal/uv aligned per index).
    face_local_map: Dict[int, int] = {}
    for tri in triangles:
        tri_ids = list(tri)
        if reverse:
            tri_ids[1], tri_ids[2] = tri_ids[2], tri_ids[1]
        face_indices = []
        for v_id in tri_ids:
            if v_id not in builder.vertices:
                continue
            idx = face_local_map.get(v_id)
            if idx is None:
                p = builder.vertices[v_id]
                u, v = compute_face_uv(p, xr, yr, uv_transform, tile_w, tile_h)
                vkey = (v_id, u, v)
                idx = group["local_v_map"].get(vkey)
                if idx is None:
                    group["local_verts"].append(p)
                    group["local_uvs"].append((u, v))
                    group["normals_accum"].append([side_normal[0], side_normal[1], side_normal[2]])
                    idx = len(group["local_verts"]) - 1
                    group["local_v_map"][vkey] = idx
                else:
                    accum = group["normals_accum"][idx]
                    accum[0] += side_normal[0]
                    accum[1] += side_normal[1]
                    accum[2] += side_normal[2]
                face_local_map[v_id] = idx
            face_indices.append(idx)
        if len(face_indices) == 3:
            group["local_faces"].append(face_indices)


def build_local_face_groups(builder: Any, ctx: FaceGroupContext) -> Dict[FaceGroupKey, Dict[str, Any]]:
    """Group a definition's faces by resolved material identity, in local
    space.

    A face whose front/back resolve to the SAME color is emitted once with
    ``double_sided`` set; a face whose sides genuinely differ is emitted as
    two single-sided triangle sets (one normal-wound front, one
    reverse-wound back) so each side keeps its own color.
    """
    face_groups: Dict[FaceGroupKey, Dict[str, Any]] = {}

    for f_id, f_data in builder.faces.items():
        front_mat = resolve_material(
            f_data.get("material_id"), ctx.material_id_to_name, ctx.materials, ctx.materials_by_folder
        )
        back_mat = resolve_material(
            f_data.get("back_material_id"), ctx.material_id_to_name, ctx.materials, ctx.materials_by_folder
        )
        front_color = resolve_color(front_mat) or ctx.fallback_color
        back_color = resolve_color(back_mat) or ctx.fallback_color

        loops = []
        for loop in f_data["loops"]:
            loop_verts = reconstruct_loop_vertices(loop, builder.edges)
            if loop_verts:
                loops.append(loop_verts)
        if not loops:
            continue

        try:
            triangles = _core.triangulate_face_3d(builder.vertices, loops, f_data["normal"])
        except Exception as e:
            raise SkpParseError(
                f"Failed to triangulate face: {e}",
                stage="build_scene", definition_id=ctx.definition_id,
            ) from e

        fn = f_data["normal"]
        xr, yr = face_uv_basis(fn)

        if front_color == back_color:
            _add_face_side(face_groups, builder, triangles, fn, front_color, True, False, front_mat,
                           f_data.get("uv_transform"), xr, yr, ctx.texture_index_for)
        else:
            _add_face_side(face_groups, builder, triangles, fn, front_color, False, False, front_mat,
                           f_data.get("uv_transform"), xr, yr, ctx.texture_index_for)
            _add_face_side(face_groups, builder, triangles, fn, back_color, False, True, back_mat,
                           f_data.get("uv_transform_back"), xr, yr, ctx.texture_index_for)

    return face_groups
