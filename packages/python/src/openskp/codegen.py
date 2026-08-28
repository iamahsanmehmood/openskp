"""Generate Python source that rebuilds a parsed :class:`~openskp.model.
SkpModel` from scratch via :func:`openskp.create.create` - a faithful,
human-readable, re-runnable transcript of the model as writer API calls,
not a serialized dump.

Handles: materials (solid and textured, including default-projection and
explicitly-pinned UVs), layers, component/group definitions (built in
dependency order), faces (front/back material, holes), instances
(transform, instance-level paint, instance-level name).

Found and fixed via diffing a real, large file (jeff.skp: 2713
definitions, 113643 faces) against its own regenerated output - the
TypeScript port this module mirrors (``toTypeScriptCode``) found that an
earlier prototype silently dropped instance-level paint (95% of that
file's instances) and every instance's own name entirely, and never
emitted textured materials at all. Building this module surfaced the same
two bugs already living in :mod:`openskp.edit`'s ``open_existing`` replay
(now fixed there too): an empty instance name being replaced by its
definition's name, and a textured material's applied height corrupting
ANY face that used it (not just default-projected ones).

Only reproduces geometry reachable by walking faces (``Definition.faces``)
- a real file's standalone/construction edges and curves that don't bound
any face are NOT reproduced (same limitation as the TypeScript port - see
its own docstring for the concrete numbers this was measured against).
This does not affect materials, textures, instance paint, or any
face/surface geometry - only invisible construction/reference lines.

Also not yet handled (matching this project's established disclosure
pattern for known gaps): colorized material tint, per-face
hidden/soft/smooth edge flags, section planes, text/dimension entities.
A model using any of these round-trips its geometry/materials/instances
correctly; those specific facts are silently dropped.

A face a few millionths of an inch off its own fitted plane (common in
real files - floating-point noise, not a modeling error) is
auto-triangulated rather than rejected, mirroring real SketchUp's own
tolerance - matches the input's face count unless triangulation was
actually needed, in which case one input face becomes 2+ (visually
identical, more triangles internally).
"""
from __future__ import annotations

import base64
from typing import Dict, List, Optional, Sequence, Set, Tuple

from ._face_groups import compute_face_uv, face_uv_basis, reconstruct_loop_vertices
from .model import Definition, Face, Instance, Material, SkpModel


def _round(n: float) -> float:
    r = round(n, 4)
    return 0.0 if r == 0.0 else r


def _point_str(p: Sequence[float]) -> str:
    return f"({_round(p[0])}, {_round(p[1])}, {_round(p[2])})"


def _matrix3x3_str(m9: Sequence[float]) -> str:
    return f"({', '.join(str(_round(v)) for v in m9)})"


def _edge_map(defn: Definition) -> Dict[int, Tuple[int, int]]:
    return {eid: (e.v1_id, e.v2_id) for eid, e in defn.edges.items()}


def _loop_points(
    loop: Sequence[Tuple[int, int]], edges: Dict[int, Tuple[int, int]], defn: Definition,
) -> Optional[List[Tuple[float, float, float]]]:
    vert_ids = reconstruct_loop_vertices(loop, edges)
    if len(vert_ids) < 3:
        return None
    points = [defn.vertices[v] for v in vert_ids if v in defn.vertices]
    if len(points) < 3:
        return None
    return [(v.x, v.y, v.z) for v in points]


def _non_collinear_triple(
    points: Sequence[Tuple[float, float, float]],
) -> Optional[Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float]]]:
    """front_uv/back_uv need exactly 3 correspondences whose (u, v) values
    are NOT collinear (an affine fit is impossible otherwise) - real faces
    can have a "flat" vertex (three consecutive vertices genuinely
    collinear in 3D), which points[:3] alone isn't guaranteed to avoid.
    Search for the first non-collinear triple instead."""
    n = len(points)
    for i in range(n):
        for j in range(i + 1, n):
            for k in range(j + 1, n):
                ax, ay, az = points[i]
                bx, by, bz = points[j]
                cx, cy, cz = points[k]
                e1 = (bx - ax, by - ay, bz - az)
                e2 = (cx - ax, cy - ay, cz - az)
                cross = (
                    e1[1] * e2[2] - e1[2] * e2[1],
                    e1[2] * e2[0] - e1[0] * e2[2],
                    e1[0] * e2[1] - e1[1] * e2[0],
                )
                if cross[0] ** 2 + cross[1] ** 2 + cross[2] ** 2 > 1e-9:
                    return points[i], points[j], points[k]
    return None


def to_python_code(model: SkpModel) -> str:
    """Generate Python source that, when its ``build()`` function is
    called, rebuilds ``model`` from scratch via ``openskp.create``. See
    this module's own docstring for exactly what is and isn't reproduced.
    """
    lines: List[str] = []

    def push(s: str) -> None:
        lines.append(s)

    materials_by_id: Dict[int, Material] = {m.id: m for m in model.materials if m.id is not None}

    # --- materials ---
    mat_var: Dict[str, str] = {}
    textured_mats: Set[str] = set()
    push("import base64")
    push("import os")
    push("import tempfile")
    push("")
    push("from openskp import create")
    push("")
    push("")
    push("def build():")
    push("    builder = create()")
    push("")
    push(f"    # --- Materials ({len(model.materials)}) ---")
    for i, mat in enumerate(model.materials):
        var_name = f"mat{i}"
        mat_var[mat.name] = var_name
        if mat.texture is not None and mat.texture.data:
            textured_mats.add(mat.name)
            b64 = base64.b64encode(mat.texture.data).decode("ascii")
            suffix = "".join(ch for ch in (mat.texture.filename or "").rsplit(".", 1)[-1:] if ch.isalnum())
            suffix = f".{suffix}" if suffix else ".png"
            # appliedHeight=1.0 - see add_texture_material's own note on
            # why the library default (an internal sentinel) corrupts ANY
            # face using this material. Every face using a textured
            # material is written below with explicit front_uv/back_uv,
            # never left to default projection, so the original applied
            # width/height never needs to be reproduced.
            push(f"    _tex_fd, _tex_path = tempfile.mkstemp(suffix={suffix!r})")
            push("    with os.fdopen(_tex_fd, 'wb') as _f:")
            push(f"        _f.write(base64.b64decode({b64!r}))")
            push("    try:")
            push(
                f"        {var_name} = builder.add_texture_material({mat.name!r}, _tex_path, applied_height=1.0)"
            )
            push("    finally:")
            push("        os.unlink(_tex_path)")
        else:
            rgba = (mat.color[0], mat.color[1], mat.color[2], mat.color[3])
            push(f"    {var_name} = builder.add_material({mat.name!r}, {rgba!r})")

    # --- layers ---
    push("")
    push(f"    # --- Layers ({len(model.layers)}) ---")
    for i, layer in enumerate(model.layers):
        var_name = f"layer{i}"
        color = (layer.color_r, layer.color_g, layer.color_b)
        push(f"    {var_name} = builder.add_layer({layer.name!r}, color={color!r}, hidden={layer.hidden!r})")

    def uv_triple_str(
        points: Sequence[Tuple[float, float, float]],
        normal: Optional[Tuple[float, float, float]],
        uv_transform: Optional[Tuple[float, ...]],
        tile_w: float,
        tile_h: float,
    ) -> Optional[str]:
        if normal is None or len(points) < 3:
            return None
        triple = _non_collinear_triple(points)
        if triple is None:
            return None
        xr, yr = face_uv_basis(normal)
        parts = []
        for p in triple:
            u, v = compute_face_uv(p, xr, yr, uv_transform, tile_w, tile_h)
            parts.append(f"({_point_str(p)}, ({_round(u)}, {_round(v)}))")
        return f"[{', '.join(parts)}]"

    def material_opts_str(face: Face, points: Sequence[Tuple[float, float, float]]) -> Tuple[str, bool]:
        parts: List[str] = []
        has_uv = False
        if face.material_id is not None:
            m = materials_by_id.get(face.material_id)
            if m is not None:
                parts.append(f"material={mat_var[m.name]}")
                if m.name in textured_mats:
                    triple = uv_triple_str(points, face.normal, face.uv_transform, m.texture.width or 1.0, m.texture.height or 1.0)
                    if triple:
                        parts.append(f"front_uv={triple}")
                        has_uv = True
        if face.back_material_id is not None:
            m = materials_by_id.get(face.back_material_id)
            if m is not None:
                parts.append(f"back_material={mat_var[m.name]}")
                if m.name in textured_mats:
                    triple = uv_triple_str(points, face.normal, face.uv_transform_back, m.texture.width or 1.0, m.texture.height or 1.0)
                    if triple:
                        parts.append(f"back_uv={triple}")
                        has_uv = True
        return ", ".join(parts), has_uv

    faces_skipped_degenerate = 0

    def emit_faces(defn: Definition, target_var: str, indent: str) -> None:
        nonlocal faces_skipped_degenerate
        edges = _edge_map(defn)
        for face in defn.faces.values():
            if not face.loops:
                continue
            points = _loop_points(face.loops[0], edges, defn)
            if points is None:
                faces_skipped_degenerate += 1
                continue
            # Independent cut-out loops (SketchUp's own "hole in a wall"
            # shape) - loops[0] is always the outer boundary, any further
            # loop is a hole. A hole that itself fails to reconstruct is
            # dropped rather than dropping the whole face.
            holes: List[List[Tuple[float, float, float]]] = []
            for hole_loop in face.loops[1:]:
                hole_points = _loop_points(hole_loop, edges, defn)
                if hole_points:
                    holes.append(hole_points)
            opts_str, has_uv = material_opts_str(face, points)
            points_str = ", ".join(_point_str(p) for p in points)
            extra: List[str] = []
            # auto_triangulate=True - mirrors real SketchUp's own tolerance
            # for a not-quite-flat polygon; incompatible with front_uv/
            # back_uv, so only added when this face has neither. Harmless
            # alongside holes - the writer takes the direct (non-
            # triangulated) path whenever holes are present either way.
            if not has_uv:
                extra.append("auto_triangulate=True")
            if holes:
                holes_str = ", ".join("[" + ", ".join(_point_str(p) for p in h) + "]" for h in holes)
                extra.append(f"holes=[{holes_str}]")
            call_opts = ", ".join(p for p in [opts_str, *extra] if p)
            push(f"{indent}{target_var}.add_face([{points_str}]{', ' + call_opts if call_opts else ''})")

    def instance_opts_str(inst: Instance, def_name: str) -> List[str]:
        parts: List[str] = []
        if inst.material_id is not None:
            m = materials_by_id.get(inst.material_id)
            if m is not None:
                parts.append(f"material={mat_var[m.name]}")
        # Explicit even when inst.name is empty: add_instance defaults an
        # OMITTED name (None) to the definition's own name, so a source
        # instance with a genuinely empty name (SketchUp shows the
        # definition's name in the Outliner as a UI-level fallback,
        # without actually storing it on the instance) would otherwise
        # come out with that name baked in for real.
        if inst.name != def_name:
            parts.append(f"name={inst.name!r}")
        return parts

    # --- definitions, built in dependency order (children before parents) ---
    def_var: Dict[int, str] = {}
    def_counter = 0

    def get_or_build_def(def_id: int, visiting: Set[int]) -> Optional[str]:
        nonlocal def_counter
        existing = def_var.get(def_id)
        if existing:
            return existing
        if def_id in visiting:
            return None  # self/mutually-referencing definition
        visiting.add(def_id)

        defn = model.definitions.get(def_id)
        if defn is None or (not defn.faces and not defn.instances):
            return None

        for inst in defn.instances:
            get_or_build_def(inst.ref_idx, visiting)

        var_name = f"def{def_counter}"
        def_counter += 1
        def_name = defn.name or f"Def{def_id}"
        def_var[def_id] = var_name

        push("")
        push(f"    # {defn.name!r} - {len(defn.faces)} faces, {len(defn.instances)} nested instances")
        push(f"    with builder.add_component_definition({def_name!r}) as {var_name}:")
        before = len(lines)
        emit_faces(defn, var_name, "        ")
        for inst in defn.instances:
            child_var = def_var.get(inst.ref_idx)
            if not child_var:
                continue
            m9 = inst.matrix[0:9]
            t = inst.matrix[9:12]
            extra = instance_opts_str(inst, def_name)
            opts = ", ".join([f"translation={_point_str(t)}", f"matrix3x3={_matrix3x3_str(m9)}", *extra])
            push(f"        {var_name}.add_instance({child_var}, {opts})")
        if len(lines) == before:
            push("        pass")
        return var_name

    for def_id in list(model.definitions.keys()):
        get_or_build_def(def_id, set())

    # --- root ---
    push("")
    push(f"    # --- Root instances ({len(model.root.instances)}) ---")
    for inst in model.root.instances:
        child_var = def_var.get(inst.ref_idx)
        if not child_var:
            continue
        child_def_name = model.definitions[inst.ref_idx].name if inst.ref_idx in model.definitions else ""
        m9 = inst.matrix[0:9]
        t = inst.matrix[9:12]
        extra = instance_opts_str(inst, child_def_name)
        opts = ", ".join([f"translation={_point_str(t)}", f"matrix3x3={_matrix3x3_str(m9)}", *extra])
        push(f"    builder.add_instance({child_var}, {opts})")
    emit_faces(model.root, "builder", "    ")

    push("")
    push("    return builder.to_bytes()")

    if faces_skipped_degenerate > 0:
        lines.insert(
            0,
            f"# {faces_skipped_degenerate} degenerate face(s) (fewer than 3 resolvable vertices) were skipped during generation.",
        )

    return "\n".join(lines) + "\n"
