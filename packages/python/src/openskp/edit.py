"""Load an existing legacy-format ``.skp`` file and rebuild it as a new,
independent :class:`~openskp.create.SkpBuilder`.

:mod:`openskp.create` only ever builds a brand-new file by splicing new
geometry into its own bundled blank scaffold (see that module's docstring)
- there is no way to append to or patch an arbitrary existing file's bytes
in place, because real SketchUp itself doesn't do that either: it fully
re-serializes the whole document on every save, so there is no stable
"original bytes + appended bytes" structure to target for a file this
project didn't create.

This module takes the other viable approach instead: fully parse the
existing file with this project's own reader (:mod:`openskp.legacy`,
already comprehensive), then *replay* everything it understood back
through the writer's own public API (materials, layers, every component
definition, every face/instance) to produce a brand-new file - not a
byte-patched copy of the original, but a freshly-built one with equivalent
content, to which the caller can add more geometry before saving.

**Scope and known fidelity gaps** (this reads long because every gap here
is a genuine, deliberately-scoped limitation, not an oversight - see each
module's own docstring for why):

* Only a **legacy-format** (SketchUp 2013-2020) source file is accepted -
  :mod:`openskp.create` never writes any other format, so a modern VFF
  (2021+) source can't be faithfully round-tripped through it.
* A face with more than one loop (a hole) is skipped - `write_face`
  (like real SketchUp's own simplest case) only supports a single
  boundary loop; a warning is returned identifying it.
* Per-edge ``hidden``/``soft``/``smooth`` flags are applied per-FACE, not
  per-edge (an "any edge in this boundary has the flag" approximation) -
  `add_face` can only set these uniformly for every edge it newly
  declares in one call, the same limitation any user of that API has.
* A positioned texture is replayed via 3 sample-point correspondences
  fitted to an affine map (see `add_face`'s own ``front_uv``/``back_uv``)
  - exact at those 3 points, but a genuinely projective (4-pin/distorted)
  source mapping won't interpolate identically between them. A *projected*
  (draped) texture has no equivalent at all and falls back to the default
  projection.
* A material's original texture tile size isn't preserved -
  `SkpBuilder.add_texture_material` has no scale parameter yet. A
  colorized (tinted) material variant is replayed as its plain source
  texture, losing the tint.
* Per-face material/layer painting: only a face's front/back *material*
  is replayed - this project's reader doesn't expose a per-face layer
  assignment at all (only instances carry an explicit layer).
* An instance's ``hidden`` flag isn't reproduced - `add_instance` has no
  visibility parameter yet.
* A layer's color and hidden state aren't reproduced - `add_layer` only
  takes a name.
* Every placed thing (originally a group or a component instance alike)
  is replayed as a plain component instance - structurally simpler, and
  visually identical, but no longer shows as a "Group" in SketchUp's
  Outliner afterward.
* Section planes, text entities, and dimensions aren't carried over at
  all - the writer has no support for any of these entity types.
* A circle/arc/polyline's original ``CArcCurve``/``CCurve`` grouping is
  lost - this project's reader doesn't preserve that grouping in its
  public :class:`~openskp.model.Face`/:class:`~openskp.model.Edge` model,
  so a round-tripped circle becomes an ordinary straight-edged face.
* Definition-level and face-level custom attributes aren't reproduced -
  the reader's public model doesn't expose either (only an instance's own
  ``properties`` are).
"""
from __future__ import annotations

import os
import pathlib
import tempfile
from typing import Dict, List, Optional, Sequence, Tuple

from . import legacy
from . import scene as _scene_mod
from .create import ComponentDefinitionBuilder, Point3, SkpBuilder, SkpWriteError, create
from .model import Definition, Face, Instance, SkpFile, SkpModel


def open_existing(path: "str | pathlib.Path") -> Tuple[SkpBuilder, List[str]]:
    """Parse ``path`` (a legacy-format ``.skp`` file) and rebuild it as a
    new :class:`SkpBuilder`, replaying materials, layers, every component
    definition, and all root-level geometry/instances.

    Returns ``(builder, warnings)`` - ``builder`` is ready for more
    ``add_face``/``add_circle``/`add_instance``/etc. calls before
    :meth:`SkpBuilder.save`; ``warnings`` lists anything from the source
    file that couldn't be faithfully reproduced (see this module's own
    docstring for the exact, deliberately-scoped gaps this draws from).

    Raises:
        SkpWriteError: if ``path`` isn't a legacy-format file.
    """
    path = str(path)
    with open(path, "rb") as f:
        head = f.read(0x200)
    if not legacy.is_legacy(head):
        raise SkpWriteError(
            f"{path!r} is not a legacy-format (SketchUp 2013-2020) .skp file - "
            "openskp.create only ever writes that format, so only a legacy-format "
            "source file can be rebuilt through it (see openskp.edit's module "
            "docstring for why an arbitrary existing file can't simply be patched)"
        )
    model = SkpFile.open(path).parse()
    warnings: List[str] = []
    builder = create()

    material_slots = _replay_materials(builder, model, warnings)
    layer_slots = {layer.name: builder.add_layer(layer.name) for layer in model.layers}
    for layer in model.layers:
        if layer.hidden or (layer.color_r, layer.color_g, layer.color_b) != (200, 200, 200):
            warnings.append(f"layer {layer.name!r}: color/hidden state not reproduced")

    def_builders: Dict[int, ComponentDefinitionBuilder] = {}
    for def_id in _definition_order(model):
        defn = model.definitions[def_id]
        context = f"definition {defn.name or def_id!r}"
        if not _definition_has_content(defn, def_builders):
            warnings.append(f"{context}: skipped (no replayable geometry)")
            continue
        with builder.add_component_definition(defn.name or f"Definition{def_id}") as db:
            _replay_body(db, defn, model, material_slots, layer_slots, warnings, context, def_builders)
        def_builders[def_id] = db

    _replay_body(builder, model.root, model, material_slots, layer_slots, warnings, "root", def_builders)

    return builder, warnings


def _replay_materials(builder: SkpBuilder, model: SkpModel, warnings: List[str]) -> Dict[int, int]:
    slots: Dict[int, int] = {}
    for mat in model.materials:
        if mat.texture is not None and mat.texture.data:
            suffix = pathlib.Path(mat.texture.filename or "texture").suffix or ".png"
            fd, tmp_path = tempfile.mkstemp(suffix=suffix)
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(mat.texture.data)
                slot = builder.add_texture_material(mat.name, tmp_path)
            finally:
                os.unlink(tmp_path)
            if mat.texture.width or mat.texture.height:
                warnings.append(f"material {mat.name!r}: original texture tile size not preserved")
            if mat.colorized:
                warnings.append(f"material {mat.name!r}: colorized tint not reproduced (base texture only)")
        else:
            if mat.texture is not None:
                warnings.append(f"material {mat.name!r}: texture image data missing - replayed as solid color")
            slot = builder.add_material(mat.name, mat.color)
        slots[id(mat)] = slot
    return slots


def _material_slot(material_id: Optional[int], model: SkpModel, slots: Dict[int, int]) -> Optional[int]:
    if material_id is None:
        return None
    mat = model.materials_by_id.get(material_id)
    if mat is None:
        return None
    return slots.get(id(mat))


def _definition_order(model: SkpModel) -> List[int]:
    """Topological order (dependencies before dependents) so a definition
    nesting instances of other definitions is only replayed after those
    are already built - the same ordering constraint
    `ComponentDefinitionBuilder.add_instance` documents."""
    visited: set = set()
    temp: set = set()
    order: List[int] = []

    def visit(def_id: int) -> None:
        if def_id in visited:
            return
        if def_id in temp:
            raise SkpWriteError(f"circular component-definition reference involving definition {def_id}")
        temp.add(def_id)
        defn = model.definitions.get(def_id)
        if defn is not None:
            for inst in defn.instances:
                if inst.ref_idx in model.definitions:
                    visit(inst.ref_idx)
        temp.discard(def_id)
        visited.add(def_id)
        order.append(def_id)

    for def_id in model.definitions:
        visit(def_id)
    return order


def _edge_map(defn: Definition) -> Dict[int, Tuple[int, int]]:
    return {eid: (e.v1_id, e.v2_id) for eid, e in defn.edges.items()}


def _definition_has_content(defn: Definition, def_builders: Dict[int, ComponentDefinitionBuilder]) -> bool:
    edges = _edge_map(defn)
    for face in defn.faces.values():
        if len(face.loops) != 1:
            continue
        if len(_scene_mod._reconstruct_loop_vertices(face.loops[0], edges)) >= 3:
            return True
    for inst in defn.instances:
        if inst.ref_idx in def_builders:
            return True
    return False


def _replay_body(
    target,
    defn: Definition,
    model: SkpModel,
    material_slots: Dict[int, int],
    layer_slots: Dict[str, int],
    warnings: List[str],
    context: str,
    def_builders: Dict[int, ComponentDefinitionBuilder],
) -> None:
    """Replay one definition's (or the root's) own faces and instances
    onto ``target`` - a :class:`SkpBuilder` for the root, or a
    :class:`ComponentDefinitionBuilder` for a nested definition; both
    expose the same ``add_face``/``add_instance`` shape this calls
    generically. ``def_builders`` resolves instance references - by the
    time any definition is opened (topological order, see
    `_definition_order`) every OTHER definition its own instances could
    reference is already in it."""
    edges = _edge_map(defn)
    for face in defn.faces.values():
        _replay_face(target, face, defn, edges, model, material_slots, warnings, context)
    for inst in defn.instances:
        _replay_instance(target, inst, def_builders, material_slots, layer_slots, model, warnings, context)


def _replay_face(
    target,
    face: Face,
    defn: Definition,
    edges: Dict[int, Tuple[int, int]],
    model: SkpModel,
    material_slots: Dict[int, int],
    warnings: List[str],
    context: str,
) -> None:
    if len(face.loops) != 1:
        warnings.append(f"{context}: face {face.id} has {len(face.loops)} loops (holes) - skipped")
        return
    vert_ids = _scene_mod._reconstruct_loop_vertices(face.loops[0], edges)
    if len(vert_ids) < 3:
        warnings.append(f"{context}: face {face.id} has fewer than 3 usable points - skipped")
        return
    points = [(defn.vertices[v].x, defn.vertices[v].y, defn.vertices[v].z) for v in vert_ids]

    loop_edges = [defn.edges[eid] for eid, _ in face.loops[0] if eid in defn.edges]
    hidden_edges = any(e.hidden for e in loop_edges)
    soft_edges = any(e.soft for e in loop_edges)
    smooth_edges = any(e.smooth for e in loop_edges)

    material = _material_slot(face.material_id, model, material_slots)
    back_material = _material_slot(face.back_material_id, model, material_slots)

    front_uv = _replay_uv(face.material_id, face.uv_transform, face.uv_projected, points, face.normal, model, warnings, context, "front")
    back_uv = _replay_uv(face.back_material_id, face.uv_transform_back, face.uv_projected_back, points, face.normal, model, warnings, context, "back")

    try:
        target.add_face(
            points,
            material=material, back_material=back_material,
            hidden=face.hidden, soft_edges=soft_edges, smooth_edges=smooth_edges, hidden_edges=hidden_edges,
            front_uv=front_uv, back_uv=back_uv,
        )
    except SkpWriteError as exc:
        warnings.append(f"{context}: face {face.id} skipped ({exc})")


def _replay_uv(
    material_id: Optional[int],
    uv_transform: Optional[Tuple[float, ...]],
    projected: bool,
    points: Sequence[Point3],
    normal: Optional[Tuple[float, float, float]],
    model: SkpModel,
    warnings: List[str],
    context: str,
    side: str,
) -> Optional[List[Tuple[Point3, Tuple[float, float]]]]:
    if uv_transform is None:
        return None
    if projected:
        warnings.append(f"{context}: {side} texture is projected/draped - falls back to default projection")
        return None
    if normal is None:
        return None
    mat = model.materials_by_id.get(material_id) if material_id is not None else None
    tile_w = (mat.texture.width if mat is not None and mat.texture is not None else 0.0) or 1.0
    tile_h = (mat.texture.height if mat is not None and mat.texture is not None else 0.0) or 1.0
    xr, yr = _scene_mod._face_uv_basis(normal)
    sample = points[:3]
    if len(sample) < 3:
        return None
    pairs = []
    for p in sample:
        u, v = _scene_mod._compute_face_uv(p, xr, yr, uv_transform, tile_w, tile_h)
        pairs.append((p, (u, v)))
    return pairs


def _replay_instance(
    target,
    inst: Instance,
    def_builders: Dict[int, ComponentDefinitionBuilder],
    material_slots: Dict[int, int],
    layer_slots: Dict[str, int],
    model: SkpModel,
    warnings: List[str],
    context: str,
) -> None:
    def_builder = def_builders.get(inst.ref_idx)
    if def_builder is None:
        warnings.append(f"{context}: instance {inst.name!r} references unavailable definition - skipped")
        return
    matrix3x3 = tuple(inst.matrix[0:9]) if len(inst.matrix) >= 9 else None
    translation = tuple(inst.matrix[9:12]) if len(inst.matrix) >= 12 else (0.0, 0.0, 0.0)
    material = _material_slot(inst.material_id, model, material_slots)
    layer = layer_slots.get(inst.layer) if inst.layer else None
    try:
        target.add_instance(
            def_builder, name=inst.name or None, translation=translation, matrix3x3=matrix3x3,
            material=material, layer=layer,
            attributes=inst.properties or None, attribute_dict_name="dynamic_attributes",
        )
    except SkpWriteError as exc:
        warnings.append(f"{context}: instance {inst.name!r} skipped ({exc})")
        return
    if inst.hidden:
        warnings.append(f"{context}: instance {inst.name!r} was hidden - hidden flag not reproduced")
