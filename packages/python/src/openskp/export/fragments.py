"""ThatOpen Fragments (``.frag``) export AND import - EXPERIMENTAL.

Converts an :class:`openskp.instanced_scene.InstancedScene` directly to
and from ThatOpen's own FlatBuffers-based ``.frag`` binary format
(https://github.com/ThatOpen/engine_fragment), the format their
``@thatopen/fragments``/``@thatopen/components`` viewer stack actually
loads and streams. Fragments is NOT IFC-specific - it is a generic,
publicly documented, instancing-native geometry+BIM format (deduplicated
"Shell" geometry referenced by many "Sample" placements) that ThatOpen's
own documentation explicitly invites third-party importers/exporters to
target directly. See ``openskp/_fragments_fb/index.fbs`` for the vendored
schema this module reads and writes against.

:func:`to_fragments`/:func:`export` (writing) came first; :func:`from_fragments`/
:func:`read` (reading, below the writing side in this file) came later, once
export was proven against the real runtime. Reading turns ``.frag`` into
OpenSKP's own 6th input format alongside ``.skp``: any file - one this
module wrote, one ThatOpen's real ``IfcImporter`` produced from an IFC
file, or anyone else's - becomes an :class:`InstancedScene` that rides
every OTHER export this project already has (GLB, OBJ, STL, PLY, DXF,
IFC4, JSON, and ``.skp`` itself via the writer) for free, the same
relationship every other reader here has to its own format.

Why this exists: the common integration path today is
``.skp -> IFC (text) -> web-ifc WASM -> .frag`` purely to get a SketchUp
model into a ThatOpen-based viewer. That round-trip through IFC's verbose
ASCII STEP text format is measured to cost tens of seconds on real files
for a step that is otherwise pure geometry repackaging. Going directly
from OpenSKP's own already-triangulated, already-deduplicated
:class:`InstancedScene` to Fragments' binary schema skips that entirely.

**Status - read before relying on this in production:**

- Confirmed working end-to-end against the REAL, unmodified
  ``@thatopen/fragments`` library: output loads, triangulates, and
  renders correctly, including genuine instance deduplication (one
  shared ``Shell`` referenced by many placements) and rotation.
- Non-uniform scale and mirrored (negative-determinant) instance
  transforms are SUPPORTED, not just tolerated: Fragments' ``Transform``
  struct has no scale field at all (confirmed by reading the real
  importer's own serialization code), so this module bakes each
  instance's scale/mirror into its OWN geometry variant before writing -
  see :func:`_decompose_trs` and :func:`_bake_primitive`. Instances that
  share the exact same (definition, scale) combination still dedupe
  against each other and share one baked ``Shell``, mirroring the real
  importer's own scale-hash caching behavior; only genuinely distinct
  scale factors get their own copy.
- The exported spatial structure reproduces the source scene's REAL
  component nesting (groups containing groups containing geometry,
  matching SketchUp's own outliner), not a flat list - see
  :func:`to_fragments`'s internal ``build_spatial_node`` recursion. A
  node gets a ``local_id`` only if it's one of the tracked
  geometry-bearing items; a pure organizational container just wraps its
  children. Confirmed against the real loader's own
  ``getSpatialStructure()`` on a real component-within-a-component
  ``.skp`` file.
- Definitions with more than 65,535 points per shell correctly switch to
  the wide (``BigShell``) index encoding; this is mechanical and has not
  been exercised against a real definition that large yet.

**Reading status:**

- Round-trips this module's own output exactly: world-space vertex
  positions across a real ``.skp``-derived scene match to the last bit
  (verified in ``tests/test_fragments.py``'s ``TestFromFragments``), not
  just object/vertex counts.
- Reads real, third-party ``.frag`` files too, not just its own encoder's
  output: a real ThatOpen-produced production file (a genuine IFC-derived
  building, not a synthetic fixture) reads cleanly, and re-exporting it
  through this same module and loading THAT back through the actual
  ``@thatopen/fragments`` runtime preserves real IFC GUIDs and category
  names - see this module's own dev history for the full verification, or
  ``tests/fixtures/thatopen_small_test.frag`` (MIT-licensed, from
  ThatOpen's own ``resources/frags/``) for the smaller committed fixture.
- What the format genuinely cannot give back, stated honestly rather than
  glossed over: no UVs anywhere in the schema (every reconstructed
  primitive gets an all-zero UV band), no stored vertex normals (rebuilt
  as flat per-face normals, correct for a hard-edged shell but not the
  original smooth-shading groups), and a purely organizational (non-
  geometry) node's own local transform was never serialized - only
  geometry-bearing items' full WORLD transforms survive. See
  :func:`from_fragments`'s own docstring for the complete list, including
  ``RepresentationClass.CIRCLE_EXTRUSION`` (round profiles), which isn't
  read yet - skipped with a warning, not silently misread as a shell.

See ``docs/IFC_PIPELINE_OPTIMIZATION_PLAN.md`` for the broader context
this was built to address, and ``CHECKLIST.md`` for this feature's
running status.

Example::

    from openskp import SkpFile
    from openskp.export import fragments

    # Writing
    skp = SkpFile.open("model.skp")
    scene = skp.build_instanced_scene()
    fragments.export(scene, "model.frag")

    # Reading - any real .frag file, from this module or anyone else's
    scene = fragments.read("model.frag")
"""

from __future__ import annotations

import json
import math
import pathlib
import warnings
import zlib
from array import array
from typing import TYPE_CHECKING, Any, Dict, List, Tuple, Union

if TYPE_CHECKING:
    from ..instanced_scene import InstancedScene, LocalPrimitive

# Fragments' Shell uses `ushort` point indices by default; a shell with
# more points than this must use the wide BigShell encoding (`uint`
# indices) instead - confirmed against the real importer's own
# `points.length > ushortMaxValue` check.
_USHORT_MAX = 65535

# Per-axis scale magnitudes within this of 1.0 are treated as exactly
# unit scale for cache-key rounding purposes - matches typical
# floating-point accumulation noise from matrix composition, not a
# meaningful tolerance for an actually-intended resize.
_SCALE_ROUND_NDIGITS = 6


def _import_fb():
    """Lazily import flatbuffers + the vendored generated bindings, so
    callers who never use fragments export don't need the dependency."""
    try:
        import flatbuffers  # noqa: F401
    except ImportError as e:
        raise ImportError(
            "openskp.export.fragments requires the 'flatbuffers' package - "
            "install it with `pip install openskp[fragments]`."
        ) from e

    from .._fragments_fb import Model, Meshes, Shell, ShellProfile, BigShellProfile
    from .._fragments_fb import Sample, Material, Transform, Representation
    from .._fragments_fb import SpatialStructure, RepresentationClass, ShellType
    from .._fragments_fb import RenderedFaces, Stroke, Attribute

    return flatbuffers, Model, Meshes, Shell, ShellProfile, BigShellProfile, \
        Sample, Material, Transform, Representation, SpatialStructure, \
        RepresentationClass, ShellType, RenderedFaces, Stroke, Attribute


def _import_fb_read():
    """Same vendored bindings as :func:`_import_fb`, plus the two
    fixed-size struct types (``FloatVector``/``DoubleVector``) only the
    READ side needs - the write side never instantiates them directly,
    it only ever calls their module-level ``Create*`` free functions."""
    base = _import_fb()
    from .._fragments_fb import FloatVector, DoubleVector
    return base + (FloatVector, DoubleVector)


def _mat4_mul(a: Tuple[float, ...], b: Tuple[float, ...]) -> Tuple[float, ...]:
    """Column-major 4x4 multiply, a*b - same convention as
    instanced_scene.py's own internal ``_mul4``."""
    out = [0.0] * 16
    for col in range(4):
        for row in range(4):
            s = 0.0
            for k in range(4):
                s += a[k * 4 + row] * b[col * 4 + k]
            out[col * 4 + row] = s
    return tuple(out)


_IDENTITY_MATRIX: Tuple[float, ...] = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)


def _collect_leaves(scene: "InstancedScene"):
    """Walk the instanced scene's tree, accumulating each node's GLOBAL
    (world) transform, and return every node worth tracking as its own
    item - "leaf" in the geometry sense (carries a mesh) OR a real, named
    organizational wrapper with no geometry of its own (e.g. a SketchUp
    group like "W-2" that only exists to hold several separately-meshed
    parts) - as ``(node, world_matrix)``. The node itself (not just its
    fields) is kept so the spatial-structure builder can later look up
    which item index a given tree node was assigned, by identity.

    Without the second case, a real, meaningfully-named wrapper has
    nowhere to attach its own Name/GUID at all: `build_spatial_node` only
    gives a node a `local_id` (and therefore a place for `getItemsData` to
    find a Name) when it's one of these tracked items, so a wrapper with
    no geometry of its own would show up in a viewer's tree only as its
    bare category ("wall"), never as "W-2" - exactly the gap this
    function closes. A wrapper with no real name (an anonymous SketchUp
    group nobody named, `name_is_generated=True`) is deliberately excluded
    - it stays a plain, id-less nesting level in the spatial structure,
    same as before, rather than becoming a noisy, meaninglessly-named item.
    """
    leaves: List[Tuple[object, Tuple[float, ...]]] = []
    root = scene.scene_hierarchy

    def walk(node, parent_matrix):
        world = _mat4_mul(parent_matrix, node.matrix)
        is_named_wrapper = node is not root and not node.name_is_generated
        if node.mesh_resource_id is not None or is_named_wrapper:
            leaves.append((node, world))
        for child in node.children:
            walk(child, world)

    walk(root, _IDENTITY_MATRIX)
    return leaves


def _decompose_trs(matrix16: Tuple[float, ...]):
    """Decompose a column-major 4x4 instance transform into
    ``(position, x_direction, y_direction, scale, mirrored)``.

    ``x_direction``/``y_direction`` are unit vectors - everything Fragments'
    ``Transform`` struct can hold. ``scale`` (``sx, sy, sz``, all positive
    magnitudes) and ``mirrored`` (whether the frame is left-handed) are the
    two things it CANNOT hold; the caller is expected to bake both into a
    per-instance geometry variant instead - see :func:`_bake_primitive`.

    Mirroring is resolved by re-deriving the matrix as
    ``M' = M @ diag(-1, 1, 1)`` (flip local X, then apply a proper
    right-handed ``M'``) whenever the original's determinant is negative -
    this is what lets a single consistent recipe (negate local X, keep
    magnitude scale, reverse triangle winding) correctly reproduce ANY
    mirrored placement, not just a specific known case.
    """
    m = matrix16
    x_axis = (m[0], m[1], m[2])
    y_axis = (m[4], m[5], m[6])
    z_axis = (m[8], m[9], m[10])
    pos = (m[12], m[13], m[14])

    sx = math.sqrt(sum(c * c for c in x_axis)) or 1.0
    sy = math.sqrt(sum(c * c for c in y_axis)) or 1.0
    sz = math.sqrt(sum(c * c for c in z_axis)) or 1.0

    det = (
        x_axis[0] * (y_axis[1] * z_axis[2] - y_axis[2] * z_axis[1])
        - x_axis[1] * (y_axis[0] * z_axis[2] - y_axis[2] * z_axis[0])
        + x_axis[2] * (y_axis[0] * z_axis[1] - y_axis[1] * z_axis[0])
    )
    mirrored = det < 0

    x_dir = tuple(c / sx for c in x_axis)
    if mirrored:
        x_dir = tuple(-c for c in x_dir)
    y_dir = tuple(c / sy for c in y_axis)

    return pos, x_dir, y_dir, (sx, sy, sz), mirrored


def _bake_primitive(prim: "LocalPrimitive", scale: Tuple[float, float, float], mirrored: bool):
    """Apply an instance's scale/mirror directly to a copy of its
    resource's LOCAL points and triangle winding, so the resulting
    geometry is correct when placed by a purely rigid (rotation +
    translation, no scale) Transform.

    Local X is negated first when ``mirrored`` (see :func:`_decompose_trs`
    for why that's always sufficient, not just for one specific case),
    then every axis is scaled by its magnitude. Reversing each triangle's
    winding after a mirror is what keeps face orientation/normals correct
    - a coordinate flip alone would turn the mesh inside-out.
    """
    sx, sy, sz = scale
    signed_sx = -sx if mirrored else sx

    positions = prim.positions
    n_verts = len(positions) // 3
    points = [
        (positions[i * 3] * signed_sx, positions[i * 3 + 1] * sy, positions[i * 3 + 2] * sz)
        for i in range(n_verts)
    ]
    triangles = [
        (prim.indices[i], prim.indices[i + 1], prim.indices[i + 2])
        for i in range(0, len(prim.indices), 3)
    ]
    if mirrored:
        triangles = [(a, c, b) for (a, b, c) in triangles]
    return points, triangles


def _scale_cache_key(mirrored: bool, scale: Tuple[float, float, float]) -> Tuple[float, float, float]:
    """Round a (mirrored, scale) pair to a stable cache key - the mirror
    flag folds into the X component's sign, since :func:`_bake_primitive`
    only ever negates X for a mirrored instance."""
    sx, sy, sz = scale
    return (
        round(-sx if mirrored else sx, _SCALE_ROUND_NDIGITS),
        round(sy, _SCALE_ROUND_NDIGITS),
        round(sz, _SCALE_ROUND_NDIGITS),
    )


def to_fragments(scene: "InstancedScene", *, raw: bool = False) -> bytes:
    """Build a ``.frag`` (FlatBuffers) buffer from an
    :class:`~openskp.instanced_scene.InstancedScene`.

    Args:
        scene: The instanced scene returned by
            :meth:`SkpFile.build_instanced_scene`.
        raw: If ``False`` (the default, matching ThatOpen's own
            ``IfcImporter`` convention), the output is deflate-compressed
            (zlib, RFC 1950 - the same wire format ``pako.deflate()``
            produces, which the real Fragments loader auto-detects). Pass
            ``True`` to get the uncompressed FlatBuffers bytes directly.

    Returns:
        The ``.frag`` file contents as bytes.

    Raises:
        ImportError: If the optional ``flatbuffers`` dependency isn't
            installed (``pip install openskp[fragments]``).
    """
    (flatbuffers, Model, Meshes, Shell, ShellProfile, BigShellProfile,
     Sample, Material, Transform, Representation, SpatialStructure,
     RepresentationClass, ShellType, RenderedFaces, Stroke, Attribute) = _import_fb()

    leaves = _collect_leaves(scene)
    resource_by_id = {r.id: r for r in scene.mesh_resources}

    builder = flatbuffers.Builder(1024 * 64)

    # ---- Shells/Representations/Materials, built lazily as leaves are
    # walked below: keyed by (resource, primitive, baked scale) so every
    # placement sharing the same definition AND the same scale/mirror
    # state dedupes onto one Shell - only a genuinely distinct scale
    # factor pays for its own geometry copy. ----
    shell_key_to_index: Dict[Tuple[str, int, float, float, float], int] = {}
    shell_offsets = []
    representation_bounds: List[Tuple[Tuple[float, float, float], Tuple[float, float, float]]] = []
    material_key_to_index: Dict[int, int] = {}
    material_rgba: List[Tuple[int, int, int, int]] = []

    def get_material_index(material_index: int) -> int:
        if material_index not in material_key_to_index:
            gltf_mat = (
                scene.gltf_materials[material_index]
                if 0 <= material_index < len(scene.gltf_materials) else {}
            )
            pbr = gltf_mat.get("pbrMetallicRoughness", {}) if isinstance(gltf_mat, dict) else {}
            base = pbr.get("baseColorFactor", [1.0, 1.0, 1.0, 1.0])
            rgba = tuple(int(round(c * 255)) for c in base[:3])
            alpha = int(round(base[3] * 255)) if len(base) > 3 else 255
            material_key_to_index[material_index] = len(material_rgba)
            material_rgba.append((rgba[0], rgba[1], rgba[2], alpha))
        return material_key_to_index[material_index]

    def get_or_bake_shell(resource_id: str, prim_idx: int, prim: "LocalPrimitive", scale, mirrored: bool) -> int:
        key = (resource_id, prim_idx) + _scale_cache_key(mirrored, scale)
        if key in shell_key_to_index:
            return shell_key_to_index[key]

        points, triangles = _bake_primitive(prim, scale, mirrored)
        is_big = len(points) > _USHORT_MAX

        profile_offsets = []
        for tri in triangles:
            if is_big:
                BigShellProfile.StartIndicesVector(builder, 3)
                for idx in reversed(tri):
                    builder.PrependUint32(idx)
                indices_vec = builder.EndVector()
                BigShellProfile.Start(builder)
                BigShellProfile.AddIndices(builder, indices_vec)
                profile_offsets.append(BigShellProfile.End(builder))
            else:
                ShellProfile.StartIndicesVector(builder, 3)
                for idx in reversed(tri):
                    builder.PrependUint16(idx)
                indices_vec = builder.EndVector()
                ShellProfile.Start(builder)
                ShellProfile.AddIndices(builder, indices_vec)
                profile_offsets.append(ShellProfile.End(builder))

        # profiles/big_profiles are BOTH required fields on Shell, but
        # only one of the two encodings is ever real for a given shell
        # (the other is just an empty vector).
        if is_big:
            Shell.StartBigProfilesVector(builder, len(profile_offsets))
            for off in reversed(profile_offsets):
                builder.PrependUOffsetTRelative(off)
            big_profiles_vec = builder.EndVector()
            Shell.StartProfilesVector(builder, 0)
            profiles_vec = builder.EndVector()
        else:
            Shell.StartProfilesVector(builder, len(profile_offsets))
            for off in reversed(profile_offsets):
                builder.PrependUOffsetTRelative(off)
            profiles_vec = builder.EndVector()
            Shell.StartBigProfilesVector(builder, 0)
            big_profiles_vec = builder.EndVector()

        Shell.StartHolesVector(builder, 0)
        holes_vec = builder.EndVector()
        Shell.StartBigHolesVector(builder, 0)
        big_holes_vec = builder.EndVector()

        Shell.StartPointsVector(builder, len(points))
        for p in reversed(points):
            builder.Prep(4, 12)
            builder.PrependFloat32(p[2])
            builder.PrependFloat32(p[1])
            builder.PrependFloat32(p[0])
        points_vec = builder.EndVector()

        face_ids = list(range(len(triangles)))
        Shell.StartProfilesFaceIdsVector(builder, len(face_ids))
        for fid in reversed(face_ids):
            builder.PrependUint16(fid)
        face_ids_vec = builder.EndVector()

        Shell.Start(builder)
        Shell.AddProfiles(builder, profiles_vec)
        Shell.AddBigProfiles(builder, big_profiles_vec)
        Shell.AddHoles(builder, holes_vec)
        Shell.AddBigHoles(builder, big_holes_vec)
        Shell.AddPoints(builder, points_vec)
        Shell.AddType(builder, ShellType.ShellType.BIG if is_big else ShellType.ShellType.NONE)
        Shell.AddProfilesFaceIds(builder, face_ids_vec)

        index = len(shell_offsets)
        shell_offsets.append(Shell.End(builder))

        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        zs = [p[2] for p in points]
        representation_bounds.append(((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))))

        shell_key_to_index[key] = index
        return index

    # ---- Model-level items + geometry samples: one item per leaf
    # placement, one sample per (leaf, primitive-of-its-resource) pair.
    # `meshes_items[k]` names which local_ids index the k-th sample
    # belongs to; `Sample.item` is simply k itself (confirmed against the
    # real importer's own output - see the module docstring). ----
    local_ids: List[int] = []
    categories: List[str] = []
    names: List[str] = []
    guids: List[str] = []
    # GUIDs of items whose `names` entry is a fallback this project
    # generated (no real name anywhere in the source file), not something
    # a person or plugin actually named - see
    # InstancedNode.name_is_generated. Carried in Model.metadata below,
    # same mechanism as layer_hidden, since the public Fragments schema
    # has no field for this either.
    generated_name_guids: List[str] = []
    sample_material: List[int] = []
    sample_representation: List[int] = []
    meshes_items: List[int] = []
    global_transform_data: List[Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float]]] = []
    # Which item index (local_ids' own index space) each tree node was
    # assigned - keyed by node identity, consumed by the spatial
    # structure builder below to place a leaf's local_id at its real
    # position in the tree instead of flattening everything under one root.
    item_index_by_node_id: Dict[int, int] = {}
    # Real-world SketchUp files can carry a non-unique per-instance GUID:
    # an engineer authors one instance (a framing plugin writes its own
    # identity into that instance's attribute dictionary), then duplicates
    # it 10-20 times via SketchUp's own native Copy/Move+Copy/Array tools
    # instead of re-running the plugin per placement. A plain SketchUp
    # entity duplication carries the source instance's attribute
    # dictionaries - and whatever GUID field a plugin wrote into one - to
    # every copy verbatim; each copy gets its own distinct transform but
    # not its own distinct identity (confirmed against a real production
    # file: 74 distinct GUID values each shared by exactly 17 different
    # physical instances, cross-checked with a second real file from the
    # same pipeline that has zero duplicates - openskp#290). A GUID that
    # doesn't uniquely identify its instance is exactly as broken as one
    # that's missing entirely for any GUID-keyed lookup (a viewer's
    # "select every member of this assembly" resolves a GUID back to a
    # scene item via a map that can only hold one value per key - 16 of
    # 17 colliding instances silently overwrite each other, and every
    # click on any of them resolves to whichever was registered last).
    # Reuses the exact same synthetic-fallback mechanism already used for
    # a genuinely missing GUID, just triggered on a second (or third...)
    # sighting of the same value instead of only on emptiness - the first
    # instance to claim a real GUID keeps it, every later instance sharing
    # that same value falls back to a synthetic one instead.
    seen_guids: set = set()

    for item_index, (node, world_matrix) in enumerate(leaves):
        resource_id = node.mesh_resource_id
        res = resource_by_id.get(resource_id) if resource_id is not None else None
        # A node with no mesh_resource_id at all is a real, named
        # organizational wrapper (see _collect_leaves) - tracked as an item
        # so it gets a Name/GUID, but with zero geometry samples of its
        # own. A node that DID declare a resource_id but it's missing from
        # resource_by_id is the original error case (a real geometry leaf
        # whose resource never got baked) - skip it as before, since
        # silently tracking it as a nameless, geometry-less item would
        # hide that bug rather than surface it.
        if resource_id is not None and res is None:
            continue
        local_ids.append(item_index)
        categories.append(node.layer or "Layer0")
        names.append(node.name or "")
        # Real SketchUp instance GUID when the source file carries one
        # (VFF/2021+) AND it hasn't already been claimed by an earlier
        # item in this same export (see seen_guids above); a stable
        # synthetic fallback otherwise (legacy-format files don't
        # currently expose a per-instance GUID, and a duplicate real GUID
        # is treated the same as a missing one). Either way, every tracked
        # item gets a non-empty, unique-within-this-export identifier -
        # consumers keyed on GUID<->local_id parity (e.g. a viewer's own
        # id-bridge, zipping Model.guids against Model.local_ids
        # index-for-index) silently get an empty map otherwise, since a
        # zero-length guids vector zips to nothing regardless of how many
        # real items exist - and a collided (non-unique) GUID breaks that
        # same lookup just as thoroughly as an empty one would.
        raw_guid = node.guid or ""
        item_guid = raw_guid if raw_guid and raw_guid not in seen_guids else f"openskp-{item_index}"
        seen_guids.add(item_guid)
        guids.append(item_guid)
        if node.name_is_generated:
            generated_name_guids.append(item_guid)
        item_index_by_node_id[id(node)] = item_index
        if res is not None:
            pos, x_dir, y_dir, scale, mirrored = _decompose_trs(world_matrix)
            for prim_idx, prim in enumerate(res.primitives):
                sample_material.append(get_material_index(prim.material_index))
                sample_representation.append(get_or_bake_shell(resource_id, prim_idx, prim, scale, mirrored))
                meshes_items.append(item_index)
                global_transform_data.append((pos, x_dir, y_dir))

    n_samples = len(sample_material)

    Meshes.StartShellsVector(builder, len(shell_offsets))
    for off in reversed(shell_offsets):
        builder.PrependUOffsetTRelative(off)
    shells_vec = builder.EndVector()

    Meshes.StartMaterialsVector(builder, len(material_rgba))
    for rgba in reversed(material_rgba):
        Material.CreateMaterial(
            builder, rgba[0], rgba[1], rgba[2], rgba[3],
            RenderedFaces.RenderedFaces.ONE, Stroke.Stroke.DEFAULT,
        )
    materials_vec = builder.EndVector()

    Meshes.StartRepresentationsVector(builder, len(representation_bounds))
    for i in reversed(range(len(representation_bounds))):
        bmin, bmax = representation_bounds[i]
        Representation.CreateRepresentation(
            builder, i, bmin[0], bmin[1], bmin[2], bmax[0], bmax[1], bmax[2],
            RepresentationClass.RepresentationClass.SHELL,
        )
    representations_vec = builder.EndVector()

    Meshes.StartSamplesVector(builder, n_samples)
    for i in reversed(range(n_samples)):
        Sample.CreateSample(builder, i, sample_material[i], sample_representation[i], 0)
    samples_vec = builder.EndVector()

    Meshes.StartMeshesItemsVector(builder, n_samples)
    for i in reversed(range(n_samples)):
        builder.PrependUint32(meshes_items[i])
    meshes_items_vec = builder.EndVector()

    Meshes.StartGlobalTransformsVector(builder, n_samples)
    for (pos, x_dir, y_dir) in reversed(global_transform_data):
        Transform.CreateTransform(
            builder, pos[0], pos[1], pos[2],
            x_dir[0], x_dir[1], x_dir[2], y_dir[0], y_dir[1], y_dir[2],
        )
    global_transforms_vec = builder.EndVector()

    # One shared identity local transform - no per-geometry sub-offset is
    # needed since every primitive's points are already in the resource's
    # own local space (now with scale/mirror already baked in) with no
    # additional internal transform.
    Meshes.StartLocalTransformsVector(builder, 1)
    Transform.CreateTransform(builder, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    local_transforms_vec = builder.EndVector()

    Meshes.StartCircleExtrusionsVector(builder, 0)
    circle_extrusions_vec = builder.EndVector()

    coordinates_struct = Transform.CreateTransform(builder, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0)

    Meshes.Start(builder)
    Meshes.AddCoordinates(builder, coordinates_struct)
    Meshes.AddMeshesItems(builder, meshes_items_vec)
    Meshes.AddSamples(builder, samples_vec)
    Meshes.AddRepresentations(builder, representations_vec)
    Meshes.AddMaterials(builder, materials_vec)
    Meshes.AddCircleExtrusions(builder, circle_extrusions_vec)
    Meshes.AddShells(builder, shells_vec)
    Meshes.AddLocalTransforms(builder, local_transforms_vec)
    Meshes.AddGlobalTransforms(builder, global_transforms_vec)
    meshes_off = Meshes.End(builder)

    cat_offsets = [builder.CreateString(c) for c in categories]
    Model.StartCategoriesVector(builder, len(categories))
    for off in reversed(cat_offsets):
        builder.PrependUOffsetTRelative(off)
    categories_vec = builder.EndVector()

    Model.StartLocalIdsVector(builder, len(local_ids))
    for lid in reversed(local_ids):
        builder.PrependUint32(lid)
    local_ids_vec = builder.EndVector()

    guid_str = builder.CreateString("00000000-0000-0000-0000-000000000000")

    # Per-item GUIDs. `guids[i]` is that item's identifier; `guids_items[i]`
    # is which local_id it belongs to - real consumers use both forms: some
    # (the library's own internal category/property indexing) walk
    # guids_items-paired-with-guids as a sparse map, but the more common
    # public entry point (a viewer's own id-bridge, matching the real
    # IfcImporter's own output shape) simply zips model.getGuids() against
    # model.getLocalIds() index-for-index - which only works when the two
    # vectors are the same length, in the same order, one entry per tracked
    # item. A zero-length guids vector (this export's previous behavior)
    # zips to an empty map regardless of how many real items exist, which
    # is silent and easy to miss: geometry still renders fine (nothing
    # about drawing a mesh needs a GUID), but every GUID-keyed interaction -
    # click-to-select's property/context-menu lookup, hide-by-id, anything
    # built on "resolve what was clicked" - has nothing to resolve against.
    guid_offsets = [builder.CreateString(g) for g in guids]
    Model.StartGuidsVector(builder, len(guids))
    for off in reversed(guid_offsets):
        builder.PrependUOffsetTRelative(off)
    guids_vec = builder.EndVector()

    Model.StartGuidsItemsVector(builder, len(local_ids))
    for lid in reversed(local_ids):
        builder.PrependUint32(lid)
    guids_items_vec = builder.EndVector()

    # One Attribute per tracked item (same order as local_ids/categories),
    # carrying the item's real display name - the SketchUp instance name,
    # or a third-party plugin's own name/label/code override when present
    # (see openskp.instanced_scene's attribute-dictionary handling) -
    # encoded as a `["Name", value, "STRING"]` JSON triple, the exact
    # convention the real IfcImporter uses for its own "Name" attribute
    # (confirmed against real ground-truth .frag output). This is what
    # lets a consumer show "Truss1"/"W1"-style real names instead of just
    # the shared layer/category string repeated at every tree node.
    attribute_offsets = []
    for name in names:
        data_offsets = []
        if name:
            entry = json.dumps(["Name", name, "STRING"])
            data_offsets.append(builder.CreateString(entry))
        Attribute.StartDataVector(builder, len(data_offsets))
        for off in reversed(data_offsets):
            builder.PrependUOffsetTRelative(off)
        data_vec = builder.EndVector()
        Attribute.Start(builder)
        Attribute.AddData(builder, data_vec)
        attribute_offsets.append(Attribute.End(builder))

    Model.StartAttributesVector(builder, len(attribute_offsets))
    for off in reversed(attribute_offsets):
        builder.PrependUOffsetTRelative(off)
    attributes_vec = builder.EndVector()

    # The source file's own per-layer visibility (openskp#275's VFF
    # layer-hidden fix) has no equivalent field anywhere in the Fragments
    # schema itself - visibility is a runtime/viewer concern there, not a
    # persisted one (confirmed by reading the real package: setVisible/
    # getVisible are live API calls, nothing in Model/Meshes/
    # SpatialStructure/Attribute represents it). `metadata` is the
    # schema's own general-purpose "JSON string for generic data about
    # the file" field - exactly the right place for a consuming viewer to
    # recover this and apply it on load (e.g. auto-hiding a "wall_
    # external_cladding_1" category that was off by default in
    # SketchUp/FrameBuilder, instead of requiring every viewer session to
    # manually re-discover and re-toggle it).
    metadata_off = builder.CreateString(json.dumps({
        "layer_hidden": scene.layer_hidden,
        "generated_name_guids": generated_name_guids,
    }))

    # Spatial structure: one SpatialStructure node per InstancedNode in
    # the ORIGINAL tree (not just leaves), so real component nesting -
    # groups containing groups containing geometry, matching SketchUp's
    # own outliner - comes through, not just a flat list. A node gets a
    # `local_id` only when it's one of the geometry-bearing leaves tracked
    # above (via `item_index_by_node_id`); a pure organizational
    # group/container simply wraps its children with no local_id of its
    # own - it was never one of this model's tracked "items" and doesn't
    # need to become one just to nest correctly. A node can legitimately
    # have BOTH its own geometry AND children (e.g. a component with its
    # own faces plus a nested sub-component) - it just gets both a
    # `local_id` and non-empty `children`.
    def build_spatial_node(node) -> int:
        child_offsets = [build_spatial_node(child) for child in node.children]

        SpatialStructure.StartChildrenVector(builder, len(child_offsets))
        for off in reversed(child_offsets):
            builder.PrependUOffsetTRelative(off)
        children_vec = builder.EndVector()

        category_off = builder.CreateString(node.layer) if node.layer else None
        item_index = item_index_by_node_id.get(id(node))

        SpatialStructure.Start(builder)
        if item_index is not None:
            SpatialStructure.AddLocalId(builder, item_index)
        if category_off is not None:
            SpatialStructure.AddCategory(builder, category_off)
        SpatialStructure.AddChildren(builder, children_vec)
        return SpatialStructure.End(builder)

    root_spatial = build_spatial_node(scene.scene_hierarchy)

    Model.Start(builder)
    Model.AddMeshes(builder, meshes_off)
    Model.AddLocalIds(builder, local_ids_vec)
    Model.AddCategories(builder, categories_vec)
    Model.AddAttributes(builder, attributes_vec)
    Model.AddGuids(builder, guids_vec)
    Model.AddGuidsItems(builder, guids_items_vec)
    Model.AddGuid(builder, guid_str)
    Model.AddMaxLocalId(builder, len(local_ids))
    Model.AddSpatialStructure(builder, root_spatial)
    Model.AddMetadata(builder, metadata_off)
    model_off = Model.End(builder)

    builder.Finish(model_off, file_identifier=b"0001")
    raw_bytes = bytes(builder.Output())

    return raw_bytes if raw else zlib.compress(raw_bytes)


def export(scene: "InstancedScene", output_path: Union[str, "pathlib.Path"], *, raw: bool = False) -> None:
    """Export an instanced scene to a ``.frag`` file.

    Args:
        scene: The instanced scene returned by
            :meth:`SkpFile.build_instanced_scene`.
        output_path: Destination path (``.frag``).
        raw: See :func:`to_fragments`.
    """
    path = pathlib.Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(to_fragments(scene, raw=raw))


# ============================================================================
# Reading - the mirror direction: a real ``.frag`` file (from OpenSKP's own
# exporter above, ThatOpen's real IfcImporter, or anyone else's) straight
# into an :class:`~openskp.instanced_scene.InstancedScene`, so it rides every
# other export OpenSKP already has (GLB, OBJ, STL, PLY, DXF, IFC4, JSON, and
# ``.skp`` itself via the writer) for free - the same relationship every
# other reader in this project has to its own format, just with ``.frag``
# as the 6th input format instead of ``.skp``.
#
# What the format genuinely cannot tell us back, stated honestly:
#
# - No UVs anywhere in the schema (``Shell`` is points + triangle indices
#   only) - every reconstructed primitive gets an all-zero UV band.
#   Materials are flat RGBA (``Material.R/G/B/A``); there is nothing
#   resembling a texture reference to read either.
# - No stored vertex normals - only points/triangles. Reconstructed here as
#   one flat per-face normal, duplicated across that face's 3 vertices;
#   correct for a hard-edged BREP-style shell, but any original smooth-
#   shading group is gone (Fragments never had anywhere to keep it).
# - A wrapper (organizational, non-geometry) ``SpatialStructure`` node's own
#   local transform was never serialized - only geometry-bearing items'
#   WORLD transforms survive, via ``Meshes.GlobalTransforms``. Reconstructed
#   wrapper nodes get an identity matrix; since every geometry leaf's own
#   matrix is set to its full world transform directly (not composed
#   through its ancestors), an identity-matrix ancestor chain still
#   composes to the exactly correct world placement - it just can't
#   recover what the ORIGINAL per-level split looked like before export.
# - ``RepresentationClass.CIRCLE_EXTRUSION`` (round profiles - rebar, pipes)
#   has no reader here yet, only ``SHELL``. A real IfcImporter-produced
#   file can contain these; such samples are skipped with a warning rather
#   than silently dropped or misread as shells - see
#   :data:`_UNSUPPORTED_REPRESENTATION_CLASSES`.
# ============================================================================

def _cross3(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> Tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _transform_to_matrix16(transform, FloatVector, DoubleVector) -> Tuple[float, ...]:
    """Reassemble a glTF-style column-major 4x4 matrix from a Fragments
    ``Transform`` struct (position + x/y direction unit vectors). The
    struct never stores a Z direction - reconstructed here as
    ``x_dir cross y_dir``, matching the same right-handed orthonormal
    frame :func:`_decompose_trs` produces on export (there, Z comes from
    the original matrix directly; here, X and Y are all that survived, so
    Z has to be re-derived, but for a genuine orthonormal frame the two
    are the same vector either way)."""
    pos = transform.Position(DoubleVector.DoubleVector())
    x_dir_s = transform.XDirection(FloatVector.FloatVector())
    y_dir_s = transform.YDirection(FloatVector.FloatVector())
    x_dir = (x_dir_s.X(), x_dir_s.Y(), x_dir_s.Z())
    y_dir = (y_dir_s.X(), y_dir_s.Y(), y_dir_s.Z())
    z_dir = _cross3(x_dir, y_dir)
    return (
        x_dir[0], x_dir[1], x_dir[2], 0.0,
        y_dir[0], y_dir[1], y_dir[2], 0.0,
        z_dir[0], z_dir[1], z_dir[2], 0.0,
        pos.X(), pos.Y(), pos.Z(), 1.0,
    )


def _extract_name_attribute(attribute) -> str:
    """Pull the ``["Name", value, "STRING"]`` entry out of an item's
    ``Attribute.Data`` strings, matching the exact convention
    :func:`to_fragments` (and the real ``IfcImporter``) writes - see that
    function's own comment on ``attribute_offsets``. Any other/unknown
    entry shape is ignored; a name-less item just gets ``""``, same as one
    that never had a name to begin with."""
    if attribute is None:
        return ""
    for i in range(attribute.DataLength()):
        raw = attribute.Data(i)
        try:
            triple = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(triple, list) and len(triple) >= 2 and triple[0] == "Name":
            return str(triple[1])
    return ""


def _compute_flat_normals(points: List[Tuple[float, float, float]], triangles: List[Tuple[int, int, int]]) -> List[Tuple[float, float, float]]:
    """One normal per vertex, flat-shaded: each triangle's own face normal,
    duplicated across its 3 vertices. See the module-level note on why
    this - not the original smooth-shading groups - is the most a ``Shell``
    (points + indices, nothing else) can ever give back."""
    normals: List[Tuple[float, float, float]] = [(0.0, 0.0, 1.0)] * len(points)
    for a, b, c in triangles:
        pa, pb, pc = points[a], points[b], points[c]
        u = (pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2])
        v = (pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2])
        n = _cross3(u, v)
        length = math.sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2])
        if length > 1e-12:
            n = (n[0] / length, n[1] / length, n[2] / length)
        normals[a] = n
        normals[b] = n
        normals[c] = n
    return normals


# RepresentationClass values this module can read geometry for today - see
# the module-level docstring note on CIRCLE_EXTRUSION.
_SUPPORTED_REPRESENTATION_CLASSES = frozenset({1})  # RepresentationClass.SHELL


def from_fragments(data: bytes) -> "InstancedScene":
    """Parse a real ``.frag`` file's bytes into an
    :class:`~openskp.instanced_scene.InstancedScene` - the mirror of
    :func:`to_fragments`. Accepts either the zlib-compressed wire format
    (the default :func:`to_fragments`/real loader convention) or raw,
    uncompressed FlatBuffers bytes; detected automatically the same way
    the real ``@thatopen/fragments`` loader does, by attempting zlib
    inflation first.

    Args:
        data: The ``.frag`` file's raw bytes.

    Returns:
        An :class:`InstancedScene` - pass it to any of
        ``openskp.export.instanced_glb``/``openskp.export.fragments`` (for
        a round-trip) exactly as you would one from
        :meth:`SkpFile.build_instanced_scene`.

    Raises:
        ImportError: If the optional ``flatbuffers`` dependency isn't
            installed (``pip install openskp[fragments]``).
    """
    from ..instanced_scene import InstancedScene, InstancedMeshResource, InstancedNode, LocalPrimitive

    (flatbuffers, Model, Meshes, Shell, ShellProfile, BigShellProfile,
     Sample, Material, Transform, Representation, SpatialStructure,
     RepresentationClass, ShellType, RenderedFaces, Stroke, Attribute,
     FloatVector, DoubleVector) = _import_fb_read()

    try:
        raw_bytes = zlib.decompress(data)
    except zlib.error:
        raw_bytes = data

    model = Model.Model.GetRootAsModel(raw_bytes, 0)
    meshes = model.Meshes()

    # ---- Materials (flat RGBA - no texture concept exists in this
    # format at all) ----
    gltf_materials: List[Dict[str, Any]] = []
    if meshes is not None:
        for i in range(meshes.MaterialsLength()):
            mat = meshes.Materials(i)
            gltf_materials.append({
                "pbrMetallicRoughness": {
                    "baseColorFactor": [mat.R() / 255.0, mat.G() / 255.0, mat.B() / 255.0, mat.A() / 255.0],
                    "metallicFactor": 0.0,
                    "roughnessFactor": 1.0,
                },
                "doubleSided": mat.RenderedFaces() != RenderedFaces.RenderedFaces.ONE,
            })
    if not gltf_materials:
        gltf_materials = [{"pbrMetallicRoughness": {"baseColorFactor": [0.8, 0.8, 0.8, 1.0], "metallicFactor": 0.0, "roughnessFactor": 1.0}}]

    # ---- Shells -> (points, triangles), decoded once per shell index,
    # reused by every sample referencing it. ----
    n_shells = meshes.ShellsLength() if meshes is not None else 0
    shell_geometry: List[Tuple[List[Tuple[float, float, float]], List[Tuple[int, int, int]]]] = [None] * n_shells  # type: ignore[list-item]

    def decode_shell(shell_idx: int):
        cached = shell_geometry[shell_idx]
        if cached is not None:
            return cached
        shell = meshes.Shells(shell_idx)
        n_points = shell.PointsLength()
        points = []
        for j in range(n_points):
            p = shell.Points(j)
            points.append((p.X(), p.Y(), p.Z()))

        is_big = shell.Type() == ShellType.ShellType.BIG
        triangles: List[Tuple[int, int, int]] = []
        if is_big:
            for j in range(shell.BigProfilesLength()):
                profile = shell.BigProfiles(j)
                if profile.IndicesLength() >= 3:
                    triangles.append((profile.Indices(0), profile.Indices(1), profile.Indices(2)))
        else:
            for j in range(shell.ProfilesLength()):
                profile = shell.Profiles(j)
                if profile.IndicesLength() >= 3:
                    triangles.append((profile.Indices(0), profile.Indices(1), profile.Indices(2)))

        result = (points, triangles)
        shell_geometry[shell_idx] = result
        return result

    # ---- Group samples by item (Meshes.MeshesItems[k] -> item index,
    # NOT Sample.Item() - see to_fragments's own comment on why the two
    # differ; Sample.Item() is just the sample's own position, always). ----
    n_samples = meshes.SamplesLength() if meshes is not None else 0
    samples_by_item: Dict[int, List[int]] = {}
    for k in range(n_samples):
        item_idx = meshes.MeshesItems(k)
        samples_by_item.setdefault(item_idx, []).append(k)

    # ---- Per-item metadata: name, guid, category, world transform.
    # local_ids[i]/categories[i]/attributes[i] are parallel, one entry per
    # tracked item, in item-index order (0..len(local_ids)-1 IS the item
    # index space - see to_fragments's own local_ids.append(item_index)). ----
    n_items = model.LocalIdsLength()
    local_id_to_item_index = {model.LocalIds(i): i for i in range(n_items)}

    guid_by_item: Dict[int, str] = {}
    n_guid_items = model.GuidsItemsLength()
    for i in range(n_guid_items):
        lid = model.GuidsItems(i)
        item_idx = local_id_to_item_index.get(lid)
        if item_idx is not None and i < model.GuidsLength():
            guid_by_item[item_idx] = model.Guids(i)

    metadata: Dict[str, Any] = {}
    metadata_raw = model.Metadata()
    if metadata_raw:
        try:
            metadata = json.loads(metadata_raw)
        except ValueError:
            metadata = {}
    layer_hidden: Dict[str, bool] = dict(metadata.get("layer_hidden", {}) or {})
    generated_name_guids = set(metadata.get("generated_name_guids", []) or [])

    material_key_to_gltf_index: Dict[int, int] = {i: i for i in range(len(gltf_materials))}

    mesh_resources: List[InstancedMeshResource] = []
    resource_by_signature: Dict[Tuple[Tuple[int, int], ...], str] = {}
    warned_unsupported = False

    def build_resource_for_item(item_idx: int, item_name: str) -> str:
        nonlocal warned_unsupported
        sample_indices = samples_by_item.get(item_idx, [])
        signature = []
        primitives: List[LocalPrimitive] = []
        for k in sample_indices:
            sample = meshes.Samples(k)
            rep_idx = sample.Representation()
            mat_idx = sample.Material()
            representation = meshes.Representations(rep_idx) if rep_idx < meshes.RepresentationsLength() else None
            rep_class = representation.RepresentationClass() if representation is not None else 1
            if rep_class not in _SUPPORTED_REPRESENTATION_CLASSES:
                if not warned_unsupported:
                    warnings.warn(
                        "openskp.export.fragments.from_fragments: skipping a "
                        f"sample with unsupported RepresentationClass={rep_class} "
                        "(only SHELL is read today - see module docstring).",
                        stacklevel=2,
                    )
                    warned_unsupported = True
                continue
            signature.append((rep_idx, mat_idx))
            points, triangles = decode_shell(rep_idx)
            normals = _compute_flat_normals(points, triangles)
            positions = array("f")
            normals_arr = array("f")
            for p in points:
                positions.extend(p)
            for n in normals:
                normals_arr.extend(n)
            uvs = array("f", [0.0] * (2 * len(points)))
            indices = array("I")
            for tri in triangles:
                indices.extend(tri)
            primitives.append(LocalPrimitive(
                positions=positions, normals=normals_arr, uvs=uvs, indices=indices,
                material_index=material_key_to_gltf_index.get(mat_idx, 0),
            ))

        sig_key = tuple(signature)
        if sig_key and sig_key in resource_by_signature:
            return resource_by_signature[sig_key]

        resource_id = f"frag-{item_idx}"
        mesh_resources.append(InstancedMeshResource(
            id=resource_id, definition_id=item_idx, definition_name=item_name or resource_id,
            variant_key="default", primitives=primitives,
        ))
        if sig_key:
            resource_by_signature[sig_key] = resource_id
        return resource_id

    # ---- Spatial structure -> InstancedNode tree. ----
    def build_node(spatial) -> InstancedNode:
        local_id = spatial.LocalId()
        category = spatial.Category() or ""
        if isinstance(category, bytes):
            category = category.decode("utf-8")

        name = ""
        guid = ""
        mesh_resource_id = None
        matrix = _IDENTITY_MATRIX
        name_is_generated = False

        if local_id is not None:
            item_idx = local_id_to_item_index.get(local_id)
            if item_idx is not None:
                attribute = model.Attributes(item_idx) if item_idx < model.AttributesLength() else None
                name = _extract_name_attribute(attribute)
                guid = guid_by_item.get(item_idx, "")
                name_is_generated = bool(guid) and guid in generated_name_guids
                if item_idx in samples_by_item:
                    mesh_resource_id = build_resource_for_item(item_idx, name or category)
                    transform = meshes.GlobalTransforms(samples_by_item[item_idx][0])
                    matrix = _transform_to_matrix16(transform, FloatVector, DoubleVector)

        children = [build_node(spatial.Children(i)) for i in range(spatial.ChildrenLength())]

        return InstancedNode(
            name=name, name_is_generated=name_is_generated, definition_name=category,
            layer=category, guid=guid, matrix=matrix,
            mesh_resource_id=mesh_resource_id, children=children,
        )

    root_spatial = model.SpatialStructure()
    if root_spatial is not None:
        scene_hierarchy = build_node(root_spatial)
    else:
        scene_hierarchy = InstancedNode(name="ROOT", definition_name="ROOT")

    return InstancedScene(
        bounds=None,
        scene_hierarchy=scene_hierarchy,
        mesh_resources=mesh_resources,
        gltf_materials=gltf_materials,
        textures=[],
        layer_hidden=layer_hidden,
    )


def read(path: Union[str, "pathlib.Path"]) -> "InstancedScene":
    """Read a ``.frag`` file from disk into an
    :class:`~openskp.instanced_scene.InstancedScene`. See
    :func:`from_fragments` for the full contract and known gaps.
    """
    return from_fragments(pathlib.Path(path).read_bytes())
