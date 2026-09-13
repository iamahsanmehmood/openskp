"""Scene baking: flatten a parsed file's placed instances into a
world-space, triangulated 3D scene ready for rendering or GLB export.

This is deliberately a *separate*, opt-in step from :func:`SkpFile.parse`.
Baking walks the entire placed scene graph - so a file that reuses a
handful of definitions across many thousands of instances can produce far
more data here than the file's raw (un-instanced) geometry. Keeping it
separate means a plain ``SkpFile.open(path).parse()`` never pays for this
heavier computation, matching the same design used by the TypeScript,
C#, and Dart ports (``buildScene()`` / ``BuildScene()`` there).

Ported from the TypeScript reference implementation
(``packages/typescript/src/model.ts``'s ``buildSceneFromParsed``), reusing
this package's own proven ``_core.py`` primitives (``transform_point``,
``multiply_matrices``, ``triangulate_face_3d``) rather than duplicating
them.
"""

from __future__ import annotations

import logging
import math
import re
import time
from array import array
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import _core
from ._face_groups import FaceGroupContext, build_local_face_groups
from .errors import SkpParseError

logger = logging.getLogger("openskp.scene")

# Mirrors _core._PROGRESS_INTERVAL - counts placed instances (not
# definitions), since a handful of definitions can be instanced thousands
# of times and that's where scene-baking's own cost actually scales.
_PROGRESS_INTERVAL = 500

INCHES_TO_MM = 25.4
INCHES_TO_M = 0.0254

# See openskp.instanced_scene._is_generic_definition_name - same pattern,
# duplicated rather than cross-imported since instanced_scene.py already
# imports from this module and a name-resolution helper isn't worth a
# shared-utility module of its own for one regex.
_GENERIC_DEFINITION_NAME_RE = re.compile(r"^(?:Group|Component)\d*#\d+$")


def _is_generic_definition_name(name: str) -> bool:
    return bool(_GENERIC_DEFINITION_NAME_RE.match(name))


@dataclass
class InstanceNode:
    """One node in the baked, world-space instance tree."""

    name: str = ""
    # See openskp.instanced_scene.InstancedNode.name_is_generated - same
    # meaning: True when `name` is a fallback this project generated,
    # rather than a real name from the source file.
    name_is_generated: bool = False
    definition_name: str = ""
    layer: str = ""
    position_mm: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    properties: Dict[str, str] = field(default_factory=dict)
    # Every OTHER attribute dictionary this instance carries, keyed by the
    # dictionary's own name, values stringified the same way `properties`
    # already is - `properties` stays exactly SketchUp's own Dynamic
    # Components data (`dynamic_attributes`) for backward compatibility;
    # third-party plugins (BIM/steel-detailing tools, etc.) commonly
    # attach their own richer per-instance data under their own dictionary
    # name instead, which this project never surfaced before.
    attribute_dictionaries: Dict[str, Dict[str, str]] = field(default_factory=dict)
    children: List["InstanceNode"] = field(default_factory=list)
    # Same full-path string that scoping instance's own meshes (and every
    # nested descendant's meshes) carry as MeshMetadata.path - lets a
    # consumer (e.g. export/ifc.py's assembly grouping) correlate a flat
    # GlbPrimitive back to its owning tree node by exact string match,
    # without re-deriving SketchUp's own name-resolution/override rules.
    path: str = ""


@dataclass
class MeshMetadata:
    """Metadata for one baked mesh, keyed the same as its GlbPrimitive's
    ``geom_name`` in :attr:`Scene.glb_primitives`."""

    name: str = ""
    definition_name: str = ""
    layer: str = ""
    position_mm: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    properties: Dict[str, str] = field(default_factory=dict)
    # See InstanceNode.attribute_dictionaries.
    attribute_dictionaries: Dict[str, Dict[str, str]] = field(default_factory=dict)
    path: str = ""


@dataclass
class GlbPrimitive:
    """One triangulated, world-space mesh: all faces (or, for a face whose
    front/back colors genuinely differ, all *one side* of those faces)
    sharing a single resolved color from one flattened scene-graph
    position. Ready to hand straight to a GLB/glTF exporter or any other
    renderer.

    Attributes:
        positions: Flat [x, y, z, x, y, z, ...] vertex positions, in
            metres, Y-up.
        normals: Flat [x, y, z, ...] vertex normals, matching *positions*
            1:1.
        uvs: Flat [u, v, u, v, ...] texture coordinates, matching
            *positions* 1:1. Computed from each source face's
            ``uv_transform`` (or the default face-plane projection when a
            face has none) - see ``Face.uv_transform`` in model.py for the
            formula. A vertex shared by two faces that disagree on UV is
            split, since indexed glTF meshes need position/normal/uv
            aligned per vertex. Faces with ``uv_projected`` set (terrain
            drape textures) still use the face-plane formula here, since
            the real projection-plane basis isn't captured in the parsed
            data - their UVs will be approximate.
        indices: Triangle vertex indices into *positions*/*normals*/*uvs*
            (3 per triangle).
        material_index: Index into :attr:`Scene.gltf_materials` for this
            primitive's resolved color.
        geom_name: Matches the corresponding key in
            :attr:`Scene.mesh_index`.
    """

    positions: array
    normals: array
    uvs: array
    indices: array
    material_index: int
    geom_name: str


@dataclass
class SceneTexture:
    """One texture image referenced by :attr:`Scene.gltf_materials`.

    Attributes:
        data: The image file's raw bytes, exactly as stored in the .skp.
        mime_type: Sniffed from the bytes, not from ``filename`` -
            SketchUp records the authoring machine's path, whose
            extension can disagree with the content.
        filename: Best-effort original filename, for diagnostics only.
    """

    data: bytes
    mime_type: str
    filename: str = ""


@dataclass
class CurveSetMetadata:
    """Metadata for one run of *loose* edges - edges that no face uses.

    Loose edges are how SketchUp stores drawing/construction geometry: a
    facade elevation, a section outline, a guide. They carry no faces, so
    before this existed the whole class of curve-only models baked to an
    empty scene (no ``glb_primitives`` at all) and exported to an IFC with a
    spatial skeleton and zero elements.

    Edges belonging to a face's loop are deliberately NOT here: they are
    already represented by that face's mesh, and emitting them again would
    double every solid's geometry as line work.
    """

    name: str = ""
    definition_name: str = ""
    layer: str = ""
    path: str = ""
    # True when ``name`` was synthesised by this module rather than read from
    # the file (root-level loose geometry has no name to read - see the
    # loose-edge block in instantiate()). Consumers that use a name as
    # *evidence* about the author's intent - ifc_classify - must not treat a
    # name this module made up as if the author had written it. Same idea as
    # scene.InstanceNode.name_is_generated.
    name_is_generated: bool = False
    # World-space, metres, in **glTF's Y-up frame** (y = height, z = -depth) -
    # byte-for-byte the same convention as GlbPrimitive.positions, so a
    # consumer can mix the two without re-deriving an axis swap. A consumer
    # that wants Z-up (e.g. the IFC exporter) applies the same conversion it
    # already applies to mesh positions.
    points_m: List[Tuple[float, float, float]] = field(default_factory=list)
    # True when the run returns to its own first point (the common case for
    # a closed outline). IFC's IfcPolyline has no implicit closure, so a
    # closed run is emitted with its first point repeated at the end.
    closed: bool = False
    # Set when this run provably *is* one whole
    # circular arc, in which case the run is emitted as an analytic arc
    # rather than as the chords the file happens to tessellate it into.
    # ``{"points": [(x, y, z), ...], "segments": [(0, 1, 2), ...]}`` - the
    # points are in the same world-space glTF-Y-up metre frame as
    # ``points_m`` (so a consumer applies the same axis swap), and the
    # segments are 0-based index tuples into them, ready to become IFC4
    # ``IfcArcIndex``/``IfcLineIndex`` entries.
    #
    # Only ever set from a *validated* frame: the file says the Curve is a
    # CArcCurve, the frame's own axes agree it is a circle (not the affine
    # image of one - see legacy._read_arccurve), and every vertex the file
    # stores on this run lies on the resulting circle. Anything short of
    # that leaves this None and the chords are emitted unchanged, which is
    # exactly what the file contains. See _solve_run_arc.
    arc: Optional[Dict[str, Any]] = None
    # Same shape and meaning as MeshMetadata.attribute_dictionaries: the IFC
    # exporter writes each entry out as its own named property set, which is
    # how an inference result (e.g. ifc_classify's AI_Classification) travels
    # downstream with the geometry instead of dying in this process.
    attribute_dictionaries: Dict[str, Dict[str, str]] = field(default_factory=dict)


@dataclass
class Scene:
    """The result of baking a parsed file's placed instances into a flat,
    world-space 3D scene."""

    scene_hierarchy: InstanceNode = field(default_factory=InstanceNode)
    mesh_index: Dict[str, MeshMetadata] = field(default_factory=dict)
    glb_primitives: List[GlbPrimitive] = field(default_factory=list)
    # Loose-edge runs, one entry per chained polyline. Empty for a model
    # whose geometry is all faces (the overwhelmingly common case).
    curve_sets: List[CurveSetMetadata] = field(default_factory=list)
    gltf_materials: List[Dict[str, Any]] = field(default_factory=list)
    # Distinct texture images the placed materials use, deduplicated by
    # source bytes. Empty when nothing placed in the scene is textured.
    # GLB export only embeds these when explicitly asked (export(...,
    # textures=True)) - most callers just want geometry, and photographic
    # textures can multiply file size.
    textures: List[SceneTexture] = field(default_factory=list)
    # Each layer's own visibility checkbox in SketchUp (the parsed file's
    # own "layer_hidden", keyed by layer name) - independent of whether any
    # placed geometry on that layer ended up in glb_primitives. Consumers
    # that expose a layer list (e.g. the IFC exporter's IfcPresentationLayerWithStyle.LayerOn)
    # use this to match SketchUp's own on/off state instead of always
    # defaulting every layer to visible.
    layer_hidden: Dict[str, bool] = field(default_factory=dict)


def _order_curve(edges: Dict[Any, Any], eids: List[Any]) -> List[Tuple[List[Any], List[Any], bool]]:
    """Order ONE curve's edges into ``(edge_ids, vertex_keys, closed)`` runs.

    The grouping is not decided here - the caller took it from the file.
    All this does is walk the group in order, which is forced: a SketchUp
    Curve is a path or a single cycle, so every vertex's degree *within the
    group* is <= 2 (measured 0 violations across every model on hand), and
    there is only ever one way to continue.

    If a group is somehow neither (SketchUp does not produce a branched
    Curve, but this must not scramble one if it appears), the longest walk
    is emitted and the remainder is ordered again - an extra polyline is
    honest, a mis-ordered one is not.
    """
    remaining = set(eids)
    runs: List[Tuple[List[Any], List[Any], bool]] = []
    while remaining:
        adj: Dict[Any, List[Any]] = {}
        for e in remaining:
            a, b = edges[e][0], edges[e][1]
            adj.setdefault(a, []).append((e, b))
            adj.setdefault(b, []).append((e, a))

        # Start at a degree-1 vertex when there is one, so an open path
        # comes out end-to-end. A cycle has none, so any vertex will do.
        start = None
        for v in adj:
            if len(adj[v]) == 1:
                start = v
                break
        if start is None:
            start = next(iter(adj))

        eid, nxt = adj[start][0]
        remaining.discard(eid)
        chain_eids = [eid]
        chain = [start, nxt]
        closed = False
        while True:
            opts = [t for t in adj.get(nxt, ()) if t[0] in remaining]
            if not opts:
                break
            e2, v2 = opts[0]
            remaining.discard(e2)
            chain_eids.append(e2)
            if v2 == chain[0]:
                # Came back to where it started: a closed loop, and the
                # first point must NOT be appended again - IFC closes it by
                # repeating, which the caller does from this flag.
                closed = True
                break
            chain.append(v2)
            nxt = v2
        runs.append((chain_eids, chain, closed))
    return runs


def _chain_loose_edges(builder: Any) -> List[Tuple[List[Any], List[Any], bool]]:
    """Turn a definition's *loose* edges into polyline runs.

    Returns ``(edge_ids, vertex_keys, closed)`` triples - vertex keys, not
    coordinates, so this stays coordinate-agnostic and the caller does the
    transform. The edge ids ride along because they are the only handle on
    each edge's layer (``builder.edge_layers``).

    Loose = no face uses the edge. Edges that bound a face are already
    represented by that face's mesh; re-emitting them would draw every solid
    as line work on top of itself.

    **Grouping comes from the file, not from geometry.** Both parsers expose
    SketchUp's own ``Edge#curve`` - VFF's ``BB0B``, the classic format's
    ``CCurve`` pointer - as ``builder.edge_curves``. The two were verified to
    agree exactly on the same drawings saved in both formats:

        magnetar_Facade_Drawing   444 curves x 4 edges    (both parsers)
        sc_SourceCity_Facade      {1:4, 2:2, 4:2, 10:2}   (both parsers)
        gk_itjds_StableTowerBeams {1:5, 11:1}             (both parsers)
        gk_itjds_HyparHut         0 curves / 90649 edges  (both parsers)

    An edge with no curve entry is emitted as its own two-point run. That is
    what the file says (``edge.curve == nil``) and it is the honest answer:
    HyparHut carries a curve on 0 of its 90649 edges, so joining such edges
    by adjacency would be inventing curves the author never drew.

    (An earlier version of this function chained by vertex adjacency alone,
    with no curve ids to constrain it. That could silently join two curves
    the author drew separately, and it merged edges the file deliberately
    leaves ungrouped - SourceCity_Facade went from 180 genuinely loose edges
    to a different run count than the file's own grouping implies.)
    """
    edges = getattr(builder, "edges", None) or {}
    faces = getattr(builder, "faces", None) or {}
    used = set()
    for face in faces.values():
        for loop in (face.get("loops") or []):
            for co in loop:
                used.add(co[0])

    loose: List[Any] = []
    for eid, pair in edges.items():
        if eid in used:
            continue
        if pair[0] is None or pair[1] is None or pair[0] == pair[1]:
            continue
        loose.append(eid)

    edge_curves = getattr(builder, "edge_curves", None) or {}
    groups: Dict[Any, List[Any]] = {}
    runs: List[Tuple[List[Any], List[Any], bool]] = []
    for eid in loose:
        cid = edge_curves.get(eid)
        if cid:
            groups.setdefault(cid, []).append(eid)
        else:
            runs.append(([eid], [edges[eid][0], edges[eid][1]], False))
    for eids in groups.values():
        runs.extend(_order_curve(edges, eids))
    return runs


def _solve_run_arc(builder: Any, eids: List[Any], matrix: Any,
                   pts: List[Tuple[float, float, float]],
                   closed: bool = False) -> Optional[Dict[str, Any]]:
    """Recover the analytic arc behind one loose-edge run, or ``None``.

    ``None`` is the answer for almost every run, and it is the *honest*
    answer: the file only says a run is an arc when an ancestor Curve is a
    ``CArcCurve`` (legacy) - ``builder.arc_curves``. An ordinary
    ``CCurve`` (freehand, welded, polygon) is a polyline and stays one.

    The frame is used only if it survives three independent checks:

    1. **The run is the whole curve.** A curve that got split into more
       than one run has no single (start, sweep) to state, so nothing is
       emitted for it. Counted from ``edge_curves``, not assumed.
    2. **The frame is a circle.** ``_read_arccurve``'s layout allows an
       affine image of a circle (a SketchUp arc that was scaled
       non-uniformly), where the two parameterisation vectors differ in
       length and are not perpendicular - measured on ``gondola_v20.skp``
       and ``theater-2017.skp``. ``IfcArcIndex`` can only say "the circular
       arc through these three points", so an ellipse must not take this
       path. The test is ``|y_axis - normal x x_axis|``, which is exactly
       zero for every circular record in the corpus (17/17) and 50% of R
       for the elliptical ones.
    3. **Every vertex the file stores lies on that circle.** The arc is
       emitted from the frame, so a frame that disagrees with the geometry
       would silently replace the author's chords with a different curve.
       Radius and the angular span are both checked, against the run's own
       first and last vertex. This check is what refuses the two runs on
       ``theater-2017.skp``: their own frame claims a circle their stored
       vertices miss by 0.73mm - 73x the 0.01mm tolerance below, and not
       float noise (a real arc's vertices sit on its frame to ~1e-13).

    The emitted points are the frame's own - ``center + cos(t)*x_axis +
    sin(t)*y_axis`` - which for a tessellated arc is *more* accurate than
    the chords it replaces, not less: SketchUp stores the vertices at
    ``sin``/``cos`` of evenly spaced angles and the sine of a float is not
    a float that lies on the circle to the last bit.
    """
    edge_curves = getattr(builder, "edge_curves", None) or {}
    arc_curves = getattr(builder, "arc_curves", None) or {}
    if not arc_curves:
        return None
    cid = edge_curves.get(eids[0])
    if cid is None:
        return None
    frame = arc_curves.get(cid)
    if frame is None:
        return None
    if sum(1 for v in edge_curves.values() if v == cid) != len(eids):
        return None

    def _xf_point(p):
        q = _core.transform_point(p, matrix)
        return (q[0] * INCHES_TO_M, q[2] * INCHES_TO_M, -q[1] * INCHES_TO_M)

    def _xf_vec(v):
        # The linear part only - a direction, not a position. `matrix` is
        # the same 13-double row-major transform _core.transform_point
        # takes, so the 3x3 occupies [0:9].
        if not matrix or len(matrix) < 12:
            x, y, z = v
        else:
            x = matrix[0] * v[0] + matrix[1] * v[1] + matrix[2] * v[2]
            y = matrix[3] * v[0] + matrix[4] * v[1] + matrix[5] * v[2]
            z = matrix[6] * v[0] + matrix[7] * v[1] + matrix[8] * v[2]
        return (x * INCHES_TO_M, z * INCHES_TO_M, -y * INCHES_TO_M)

    # Everything below is in the same frame the caller produced pts in:
    # world space, metres, glTF Y-up. The frame's vectors get the identical
    # axis swap (and the same inch->metre factor) as the points, so the two
    # cannot drift apart.
    c = _xf_point(frame["center"])
    xa = _xf_vec(frame["x_axis"])
    ya = _xf_vec(frame["y_axis"])
    na = _xf_vec(frame["normal"])

    r2 = sum(t * t for t in xa)
    r = r2 ** 0.5
    if r <= 0.0:
        return None
    # `normal` is a unit vector in the file (it says which way the arc's
    # plane faces), while x_axis/y_axis are the parameterisation vectors and
    # carry the radius. The identity being tested is
    # `y_axis == normal x x_axis`, which only holds as a *vector* equality
    # when `normal` is unit-length - otherwise the cross comes out R times
    # too small and every circle fails. Normalising here is safe because the
    # whole comparison scales with r (both sides get the same factor from
    # x_axis/y_axis), so the test stays what it was in the source file.
    nlen = sum(t * t for t in na) ** 0.5
    if nlen <= 0.0:
        return None
    nh = tuple(t / nlen for t in na)
    cross = (nh[1] * xa[2] - nh[2] * xa[1],
             nh[2] * xa[0] - nh[0] * xa[2],
             nh[0] * xa[1] - nh[1] * xa[0])
    # Check 2. Relative, because r is in model units and can be metres or
    # inches depending on the file.
    if math.dist(cross, ya) > 1e-6 * r:
        return None

    inv_r = 1.0 / r
    xh = tuple(t * inv_r for t in xa)
    yh = tuple(t * inv_r for t in ya)

    def _theta(p):
        dx = p[0] - c[0]
        dy = p[1] - c[1]
        dz = p[2] - c[2]
        return math.atan2(dx * yh[0] + dy * yh[1] + dz * yh[2],
                          dx * xh[0] + dy * xh[1] + dz * xh[2])

    def _on_circle(p, tol):
        return abs(math.dist(p, c) - r) <= tol

    # The caller rounded pts to 6 decimals in metres, so the tolerance has
    # to clear that rounding before it can say anything about the geometry.
    # It still rejects what it has to: an elliptical frame is off by ~50%
    # of r, five orders of magnitude away.
    tol = max(1e-5, 1e-6 * r)
    # Check 3a: every stored vertex is on the circle.
    if not all(_on_circle(p, tol) for p in pts):
        return None

    thetas = [_theta(p) for p in pts]
    # A closed run's chain deliberately does NOT repeat its first vertex
    # ("the first point must NOT be appended again", _order_curve), so the
    # walk over the stored vertices alone sums to `2*pi - one step`: a full
    # circle of 24 edges would come out as a 345-degree arc with a visible
    # gap where its last segment should be. The closing step is therefore
    # supplied from the same flag the caller closes polylines from, in the
    # same shortest-turn sense as every other step.
    pairs = list(zip(thetas, thetas[1:]))
    if closed and len(thetas) > 2:
        pairs.append((thetas[-1], thetas[0]))
    # Unwrap into a monotone walk: each step is the shortest turn from the
    # previous vertex, which for a real tessellation is the tessellation
    # step (a fraction of a radian), never anything near pi.
    #
    # The quarter-turn bound carries a relative tolerance, because a
    # legitimate coarse tessellation sits exactly on it.
    # A SketchUp circle drawn with 4 sides stores its vertices a quarter turn
    # apart, and the four steps of a *closed* run do not all round the same
    # way: measured, they come out [pi/2, pi/2, pi/2, pi/2 + 2e-16] - the
    # closing step alone is over the bound, so the whole run was refused on
    # float noise and fell back to its chords. The bound is a statement about
    # evidence (vertices spaced wider than a quarter turn stop looking like a
    # tessellation of this arc), not a measurement, so it should not turn on
    # the last bit. Real arcs are nowhere near it: SketchUp's default 24-sided
    # circle steps 15 degrees.
    step_limit = math.pi * 0.5 * (1.0 + 1e-9)
    steps: List[float] = []
    total = 0.0
    for a, b in pairs:
        d = b - a
        while d > math.pi:
            d -= 2.0 * math.pi
        while d < -math.pi:
            d += 2.0 * math.pi
        if abs(d) > step_limit:
            return None          # not a walk along the circle
        if steps and (d > 0) != (steps[0] > 0):
            return None          # direction reverses: not one arc
        steps.append(d)
        total += d
    if not steps or total == 0.0:
        return None

    def _at(t):
        ct, st = math.cos(t), math.sin(t)
        return (c[0] + ct * xa[0] + st * ya[0],
                c[1] + ct * xa[1] + st * ya[1],
                c[2] + ct * xa[2] + st * ya[2])

    t0 = thetas[0]
    if abs(total) >= 2.0 * math.pi - 1e-6 * max(1.0, abs(total)):
        # A closed run: a full turn, which no single IfcArcIndex can state
        # (its three points would make the first and last coincide, so the
        # arc through them is not even determined). Two half-turns can, and
        # do. The test is on the measured sweep, not on the flag: a run
        # whose walk really is a full turn is a loop whatever it was
        # labelled, and this is the only branch that stays non-degenerate
        # for one.
        return {
            "points": [_at(t0), _at(t0 + total * 0.25),
                       _at(t0 + total * 0.5), _at(t0 + total * 0.75)],
            "segments": [(0, 1, 2), (2, 3, 0)],
        }
    return {
        "points": [_at(t0), _at(t0 + total * 0.5), _at(t0 + total)],
        "segments": [(0, 1, 2)],
    }


def _sniff_image_mime(data: bytes) -> Optional[str]:
    """Identify an image's MIME type from its magic bytes. Returns ``None``
    for anything glTF cannot carry (glTF only allows PNG and JPEG)."""
    if len(data) >= 3 and data[0] == 0xff and data[1] == 0xd8 and data[2] == 0xff:
        return "image/jpeg"
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return None


def build_scene(
    parsed: Dict[str, Any],
    name_override_keys: Sequence[str] = ("name", "label", "code"),
    include_curve_sets: bool = True,
) -> Scene:
    """Bake every instance actually placed in ``parsed`` (the output of
    :func:`openskp._core.full_parse` / ``full_parse_legacy``) into
    world-space, triangulated mesh data.

    Args:
        parsed: Output of ``_core.full_parse()``. Callers normally get
            this by calling :meth:`SkpFile.parse` first is *not* required -
            :meth:`SkpFile.build_scene` re-runs the raw parse independently,
            so a plain ``parse()`` call never carries this cost.
        name_override_keys: Attribute-dictionary key names (checked in
            order, first match wins) that identify an instance's real,
            plugin-assigned name - tried across every non-boilerplate
            dictionary the instance carries, whichever third-party plugin
            (FrameBuilder, TechSteel, or any other SketchUp extension that
            attaches its own attribute dictionary) wrote it, not a specific
            plugin's own dictionary name. The default covers the common
            convention; pass your own tuple to also recognize a plugin
            that instead uses e.g. ``"mark"`` or ``"partNumber"``.
        include_curve_sets: Bake loose edges (edges no face uses) into
            :attr:`Scene.curve_sets` as polyline runs. On by default, and
            the only reason to turn it off is cost. Measured on this
            project's own corpus, exporting the same file both ways:
            sc_SourceCity_Facade (solids + curves) 50,662,265 -> 53,564,893
            bytes (+5.7%); magnetar_Facade_Drawing (curves only) 1,570 ->
            568,177 - that second one being the point: on a curve-only
            model the curve sets *are* the model, and turning them off
            leaves a spatial skeleton with nothing in it.

    Returns:
        A populated :class:`Scene`.
    """
    t0 = time.monotonic()
    defs_dict = parsed["defs_dict"]
    layer_colors = parsed["layer_colors"]
    layer_id_to_name = parsed["layer_id_to_name"]
    material_id_to_name = parsed.get("material_id_to_name", {})
    materials = parsed["materials"]
    materials_by_folder = parsed.get("materials_by_folder", {})

    logger.info("Building scene: %d definitions available", len(defs_dict))

    instance_counter = [0]
    mesh_counter = [0]
    mesh_index: Dict[str, MeshMetadata] = {}
    glb_primitives: List[GlbPrimitive] = []
    curve_sets: List[CurveSetMetadata] = []
    # Per-layer sequence numbers for generated curve-set labels (see the
    # loose-edge block in instantiate()).
    curve_seq: Dict[str, int] = {}

    # Instance path -> (properties, name) updates, collected in O(1) per
    # instance and applied once after instantiation completes (see the
    # path-walk loop below), instead of scanning the entire mesh_index per
    # placed instance - an O(instances x meshes) substring scan that both
    # dominated build_scene on models with many placed instances and could
    # match the wrong meshes (a shallow instance's path is always a string
    # prefix of every deeper descendant's path too, so "in" matched far
    # more than intended - see openskp#240).
    path_updates: Dict[str, Tuple[Dict[str, str], str, Dict[str, Dict[str, str]]]] = {}

    # Textures deduplicated by bytes: the same image routinely backs
    # several materials, and re-embedding it per material would multiply
    # the export size for nothing.
    textures: List[SceneTexture] = []
    texture_index_by_key: Dict[str, int] = {}

    def texture_index_for(tex: Optional[Dict[str, Any]]) -> Optional[int]:
        if not tex:
            return None
        data = tex.get("data")
        if not data:
            return None
        mime_type = _sniff_image_mime(data)
        if mime_type is None:
            return None  # a format glTF cannot carry
        # length plus a short byte prefix is enough to tell real images
        # apart without hashing megabytes on every face
        key = f"{len(data)}:{data[:16].hex()}"
        hit = texture_index_by_key.get(key)
        if hit is not None:
            return hit
        idx = len(textures)
        textures.append(SceneTexture(data=data, mime_type=mime_type, filename=tex.get("filename", "")))
        texture_index_by_key[key] = idx
        return idx

    color_to_material_index: Dict[Tuple[Tuple[int, int, int], bool, Optional[int], float], int] = {}
    gltf_materials: List[Dict[str, Any]] = []

    # Definitions currently being instantiated on the active recursion
    # path (not "ever visited" - the same definition legitimately reused
    # by sibling instances is fine). Guards against a component that
    # directly or transitively instances itself, which would otherwise
    # recurse until the stack overflows.
    active_definitions: set = set()

    def get_layer_color(name: str) -> Tuple[int, int, int]:
        return layer_colors.get(name, (136, 136, 136))

    def get_material_index(
        color: Tuple[int, int, int],
        double_sided: bool,
        texture_index: Optional[int],
        transparency: float = 1.0,
    ) -> int:
        # The texture is part of the identity, not just the color: two
        # different images can average to the same RGB (real files do
        # this), and keying on color alone would merge them into one
        # material and lose one of the images.
        key = (color, double_sided, texture_index, transparency)
        if key in color_to_material_index:
            return color_to_material_index[key]
        idx = len(gltf_materials)
        r, g, b = color
        pbr: Dict[str, Any] = {
            "baseColorFactor": [r / 255, g / 255, b / 255, transparency],
            "metallicFactor": 0.0,
            "roughnessFactor": 0.8,
        }
        # baseColorFactor stays as the resolved color even with a texture
        # attached: glTF multiplies the two, and SketchUp's own colorized
        # materials rely on exactly that tint.
        if texture_index is not None:
            pbr["baseColorTexture"] = {"index": texture_index}
        mat_dict: Dict[str, Any] = {"pbrMetallicRoughness": pbr}
        if double_sided:
            mat_dict["doubleSided"] = True
        # glTF's default alphaMode is OPAQUE, which tells a conformant
        # renderer to ignore alpha entirely - both the material's own
        # opacity and any texture's alpha channel. Genuinely translucent
        # materials (glass, water) need BLEND so baseColorFactor's alpha
        # (and the texture's, if any) actually takes effect. A
        # textured-but-otherwise-opaque material gets MASK instead: many
        # SketchUp Warehouse assets (tree foliage, fences, signage) rely on
        # the image's own alpha channel to cut a shape out of an otherwise
        # flat quad, and without MASK a renderer would show the full
        # rectangle. MASK is a no-op for a texture with no real cutout - a
        # fully-opaque alpha channel (or none, as in JPEG) stays above the
        # cutoff everywhere - so this is safe to set unconditionally rather
        # than trying to detect which textures need it.
        if transparency < 1.0:
            mat_dict["alphaMode"] = "BLEND"
        elif texture_index is not None:
            mat_dict["alphaMode"] = "MASK"
        gltf_materials.append(mat_dict)
        color_to_material_index[key] = idx
        return idx

    def instantiate(
        def_id,
        current_matrix,
        parent_layer: str = "Layer0",
        path_name: str = "ROOT",
        inherited_color: Optional[Tuple[int, int, int]] = None,
    ) -> List[InstanceNode]:
        d = defs_dict.get(def_id)
        if d is None:
            return []
        builder = d["builder"]

        if builder.faces:
            # A definition's faces carry their own
            # layer (legacy drawbase). The inherited `parent_layer` is never
            # resolvable for legacy instances, so it silently degraded to
            # "Layer0" for every face - which starved export/ifc.py's
            # classifier (it types elements by name, then by layer) of the
            # one signal that was actually present in the file.
            _lc: Dict[Any, int] = {}
            for _f in builder.faces.values():
                _l = _f.get("layer")
                if _l:
                    _lc[_l] = _lc.get(_l, 0) + 1
            if _lc:
                _top_layer = max(_lc, key=_lc.get)
                parent_layer = layer_id_to_name.get(_top_layer, parent_layer)
            # Group faces sharing a resolved (color, double_sided, texture)
            # identity into one mesh each, in local space - shared with the
            # instanced builder (openskp#200) via _face_groups.py: a face
            # whose front/back resolve to the SAME color is emitted once,
            # with its glTF material marked doubleSided so it's visible from
            # either side without needing duplicate geometry; a face whose
            # front/back genuinely differ is emitted as TWO single-sided
            # triangle sets - one normal-wound using the front material, one
            # reverse-wound using the back material - so each side renders
            # its own correct color instead of the front material leaking
            # onto (or the back vanishing from) the far side.
            fallback_color = inherited_color if inherited_color is not None else get_layer_color(parent_layer)
            face_groups = build_local_face_groups(
                builder,
                FaceGroupContext(
                    material_id_to_name=material_id_to_name,
                    materials=materials,
                    materials_by_folder=materials_by_folder,
                    texture_index_for=texture_index_for,
                    fallback_color=fallback_color,
                    definition_id=def_id,
                ),
            )

            for (face_color, double_sided, tex_index, transparency), group in face_groups.items():
                local_faces = group["local_faces"]
                if not local_faces:
                    continue

                is_root = path_name == "ROOT"
                tx = 0.0 if is_root else (current_matrix[9] if len(current_matrix) > 9 else 0.0) * INCHES_TO_MM
                ty = 0.0 if is_root else (current_matrix[10] if len(current_matrix) > 10 else 0.0) * INCHES_TO_MM
                tz = 0.0 if is_root else (current_matrix[11] if len(current_matrix) > 11 else 0.0) * INCHES_TO_MM

                safe_path = path_name.replace(" / ", "__").replace(" ", "_")[:80]
                color_suffix = (
                    f"_{face_color[0]}_{face_color[1]}_{face_color[2]}_{'ds' if double_sided else 'ss'}"
                    if len(face_groups) > 1 else ""
                )
                geom_name = f"mesh_{mesh_counter[0]}_{safe_path}_{parent_layer}{color_suffix}"
                mesh_counter[0] += 1

                mesh_index[geom_name] = MeshMetadata(
                    name="ROOT" if is_root else (path_name.split(" / ")[-1] or ""),
                    definition_name=d.get("name") or "",
                    layer=parent_layer,
                    position_mm=(round(tx, 2), round(ty, 2), round(tz, 2)),
                    properties={},
                    path=path_name,
                )

                local_verts = group["local_verts"]
                local_uvs = group["local_uvs"]
                positions = array("f", [0.0]) * (len(local_verts) * 3)
                normals = array("f", [0.0]) * (len(local_verts) * 3)
                uvs = array("f", [0.0]) * (len(local_verts) * 2)
                vertex_normals_accum = group["normals_accum"]

                for i, v in enumerate(local_verts):
                    pt = _core.transform_point(v, current_matrix)
                    positions[i * 3] = pt[0] * INCHES_TO_M
                    positions[i * 3 + 1] = pt[2] * INCHES_TO_M
                    positions[i * 3 + 2] = -pt[1] * INCHES_TO_M

                    uvs[i * 2] = local_uvs[i][0]
                    uvs[i * 2 + 1] = local_uvs[i][1]

                    raw_n = vertex_normals_accum[i]
                    norm_len = (raw_n[0] ** 2 + raw_n[1] ** 2 + raw_n[2] ** 2) ** 0.5
                    if norm_len > 1e-6:
                        n = (raw_n[0] / norm_len, raw_n[1] / norm_len, raw_n[2] / norm_len)
                    else:
                        n = (0.0, 0.0, 1.0)

                    nx = current_matrix[0] * n[0] + current_matrix[1] * n[1] + current_matrix[2] * n[2]
                    ny = current_matrix[3] * n[0] + current_matrix[4] * n[1] + current_matrix[5] * n[2]
                    nz = current_matrix[6] * n[0] + current_matrix[7] * n[1] + current_matrix[8] * n[2]
                    length = (nx * nx + ny * ny + nz * nz) ** 0.5
                    if length > 1e-6:
                        normals[i * 3] = nx / length
                        normals[i * 3 + 1] = nz / length
                        normals[i * 3 + 2] = -ny / length
                    else:
                        normals[i * 3] = 0.0
                        normals[i * 3 + 1] = 1.0
                        normals[i * 3 + 2] = 0.0

                indices = array("I", [0]) * (len(local_faces) * 3)
                for i, tri in enumerate(local_faces):
                    indices[i * 3] = tri[0]
                    indices[i * 3 + 1] = tri[1]
                    indices[i * 3 + 2] = tri[2]

                material_index = get_material_index(face_color, double_sided, tex_index, transparency)
                glb_primitives.append(
                    GlbPrimitive(
                        positions=positions,
                        normals=normals,
                        uvs=uvs,
                        indices=indices,
                        material_index=material_index,
                        geom_name=geom_name,
                    )
                )

        # Loose edges -> world-space polyline runs.
        #
        # Deliberately OUTSIDE the `if builder.faces:` branch above: a
        # curve-only model has no faces at all, which is exactly the case
        # this exists for. Before it, such a definition contributed nothing
        # to the scene, so export/ifc.py got an empty primitive list and
        # emitted a spatial skeleton with zero elements (see
        # magnetar_Facade_Drawing.skp: 1776 edges -> 0 products).
        _edge_layers = getattr(builder, "edge_layers", None) or {}
        _runs = _chain_loose_edges(builder) if include_curve_sets else ()
        for _eids, _chain, _closed in _runs:
            pts = []
            _complete = True
            for _vk in _chain:
                _v = builder.vertices.get(_vk)
                if _v is None:
                    _complete = False
                    break
                _pt = _core.transform_point(_v, current_matrix)
                pts.append((round(_pt[0] * INCHES_TO_M, 6),
                            round(_pt[2] * INCHES_TO_M, 6),
                            round(-_pt[1] * INCHES_TO_M, 6)))
            # A dangling vertex key would silently shorten the run, turning a
            # parsing gap into a plausible-looking polyline. Drop the run
            # instead - a missing curve is honest, a truncated one is not.
            if not _complete or len(pts) < 2:
                continue
            # A run whose edges disagree on layer resolves to the majority,
            # same rule the face block above uses for the same reason.
            _lc: Dict[Any, int] = {}
            for _eid in _eids:
                _l = _edge_layers.get(_eid)
                if _l:
                    _lc[_l] = _lc.get(_l, 0) + 1
            run_layer = parent_layer
            if _lc:
                run_layer = layer_id_to_name.get(max(_lc, key=_lc.get), parent_layer)
            # Root-level loose geometry has no name in the file whatsoever -
            # an edge is just an edge, only components get named. Labelling
            # all 444 of them "ROOT" would be technically honest and useless,
            # so a root-level run is labelled with the one signal that does
            # exist (its layer) plus a sequence number to keep them distinct.
            # Anything inside a named component keeps its real name.
            if path_name == "ROOT":
                _seq = curve_seq.get(run_layer, 0) + 1
                curve_seq[run_layer] = _seq
                _cname = "%s_%d" % (run_layer, _seq)
                _cgen = True
            else:
                _cname = path_name.split(" / ")[-1] or ""
                _cgen = False
            curve_sets.append(CurveSetMetadata(
                name=_cname,
                name_is_generated=_cgen,
                definition_name=d.get("name") or "",
                layer=run_layer,
                path=path_name,
                points_m=pts,
                closed=_closed,
                arc=_solve_run_arc(builder, _eids,
                                   current_matrix, pts, _closed),
            ))

        child_instances_info: List[InstanceNode] = []
        for inst in builder.instances:
            ref_idx = inst["ref_idx"]
            inst_matrix = inst["matrix"]
            new_matrix = _core.multiply_matrices(current_matrix, inst_matrix)

            l_name = parent_layer
            inst_color = inherited_color
            # Legacy (pre-2021 MFC) instances carry a precomputed
            # "properties" dict (see legacy._extract_legacy_dynamic_
            # properties) - VFF instances don't set this key at all, so
            # this stays {} for them and gets overwritten below via the
            # D007/DC05 TLV walk instead.
            properties: Dict[str, str] = dict(inst.get("properties") or {})
            attribute_dicts: Dict[str, Dict[str, str]] = {}
            name_override: Optional[str] = None

            d007 = next((c for c in inst["children"] if c["tag"] == "D007"), None)
            if d007:
                d207 = next((c for c in d007["children"] if c["tag"] == "D207"), None)
                if d207 and d207["payload"]:
                    p = d207["payload"]
                    l_id = p[0] if len(p) == 1 else _core.parse_var_int(p, 0, len(p))
                    l_name = layer_id_to_name.get(l_id, parent_layer)

                d107 = next((c for c in d007["children"] if c["tag"] == "D107"), None)
                if d107:
                    inst_mat_id = _core.parse_var_int(d107["payload"], 0, len(d107["payload"]))
                    mat_name = material_id_to_name.get(inst_mat_id)
                    mat = materials.get(mat_name) or materials_by_folder.get(mat_name)
                    if mat:
                        c = mat["color"]
                        inst_color = (c["r"], c["g"], c["b"])

                try:
                    all_dicts = _core.extract_attribute_dictionaries(d007)
                    dynamic = all_dicts.get("dynamic_attributes", {})
                    properties = {k: _core._stringify_vff_attr_value(v) for k, v in dynamic.items()}
                    # Every other dictionary a third-party plugin (BIM
                    # workflow, steel-detailing tool, ...) attached to
                    # this specific instance - SU_InstanceSet is
                    # SketchUp's own always-present, always-empty
                    # Owner/Status boilerplate, not worth surfacing.
                    for dict_name, entries in all_dicts.items():
                        if dict_name in ("dynamic_attributes", "SU_InstanceSet"):
                            continue
                        attribute_dicts[dict_name] = {
                            k: _core._stringify_vff_attr_value(v) for k, v in entries.items()
                        }
                        if name_override is None:
                            for key in name_override_keys:
                                val = entries.get(key)
                                if val:
                                    name_override = str(val)
                                    break
                except Exception:
                    logger.debug(
                        "Failed to extract attribute dictionaries for instance %r (ref_idx=%r)",
                        inst.get("name"), ref_idx, exc_info=True,
                    )

            # inst_name (never overridden) is what keeps full_path_name -
            # and so path_updates' own keys - locally unique: a plugin's
            # "name"/"label" field (used for display_name below) is
            # frequently a shared type/catalog label ("Profile25" for
            # every instance of that profile), not a real per-instance
            # identifier, and using it here would collide different
            # instances' path_updates entries onto each other.
            def_name = (defs_dict.get(ref_idx) or {}).get("name") or ""
            # Same fallback order as openskp.instanced_scene: attribute-dict
            # override, then the instance's own name, then the definition's
            # own name if it's not itself an auto-generated "Group#1"-style
            # placeholder, then finally the internal index.
            inst_name = inst["name"] or (
                def_name if def_name and not _is_generic_definition_name(def_name) else ""
            ) or f"Component_{ref_idx}"
            display_name = name_override or inst_name
            name_is_generated = not (name_override or inst["name"] or (
                def_name and not _is_generic_definition_name(def_name)
            ))
            full_path_name = f"{path_name} / {inst_name}"
            instance_counter[0] += 1
            if instance_counter[0] % _PROGRESS_INTERVAL == 0:
                logger.debug("Processed %d placed instances", instance_counter[0])

            if ref_idx in active_definitions:
                raise SkpParseError(
                    "Recursive component definition",
                    stage="build_scene", definition_id=ref_idx,
                )
            active_definitions.add(ref_idx)
            child_nodes = instantiate(ref_idx, new_matrix, l_name, full_path_name, inst_color)
            active_definitions.discard(ref_idx)

            tx = new_matrix[9] * INCHES_TO_MM if len(new_matrix) > 9 else 0.0
            ty = new_matrix[10] * INCHES_TO_MM if len(new_matrix) > 10 else 0.0
            tz = new_matrix[11] * INCHES_TO_MM if len(new_matrix) > 11 else 0.0

            inst_info = InstanceNode(
                name=display_name,
                name_is_generated=name_is_generated,
                definition_name=def_name,
                layer=l_name,
                position_mm=(round(tx, 2), round(ty, 2), round(tz, 2)),
                properties=properties,
                attribute_dictionaries=attribute_dicts,
                children=child_nodes,
                path=full_path_name,
            )
            child_instances_info.append(inst_info)

            path_updates[full_path_name] = (properties, display_name, attribute_dicts)

        return child_instances_info

    identity_mat = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1.0]
    root_children = instantiate("ROOT", identity_mat)

    # Deferred mesh backfill: each mesh's own path was recorded verbatim as
    # a path_updates key by the exact instance that placed the definition
    # that mesh's own faces belong to (never an ancestor's), so a direct
    # O(1) lookup per mesh is enough - no cascading from an ancestor down
    # to its descendants' own meshes. Properties/name are per-instance
    # (each definition's own Dynamic Component attributes and placement
    # name), not inherited by nested sub-parts, matching how
    # scene_hierarchy already builds each InstanceNode from that same
    # instance's own `inst["name"]`/`properties` directly above - a mesh
    # ending up with some ancestor's name/properties instead of its own
    # was exactly this bug (openskp#240).
    for existing in mesh_index.values():
        if existing.path in path_updates:
            existing.properties, existing.name, existing.attribute_dictionaries = path_updates[existing.path]

    for geom_name, existing in mesh_index.items():
        if existing.path == "ROOT":
            # do NOT clobber `layer` here.
            #
            # This loop is meant to normalise the synthetic root *node*'s
            # placeholder fields (see scene_hierarchy below), but it iterates
            # real MESHES - specifically the ones whose path is exactly
            # "ROOT", i.e. loose geometry authored directly in the model root
            # rather than inside any group/component. `instantiate()` has
            # already resolved their layer from the definition's own faces
            # (legacy drawbase), and for such a mesh that layer is the ONLY
            # type signal that exists - there is no component name to fall
            # back on, because in SketchUp loose geometry has no name.
            # Hardcoding "Layer0" here threw that signal away and made every
            # root-level element invisible to export/ifc.py's classifier.
            # (Verified on gk_itjds_HyparHut.skp: 3 root meshes whose
            # geom_name had always carried the real layer `Table` while
            # MeshMetadata.layer read "Layer0".)
            existing.name = existing.name or "ROOT"
            existing.definition_name = existing.definition_name or "ROOT_MODEL"
            existing.layer = existing.layer or "Layer0"
            existing.position_mm = (0.0, 0.0, 0.0)
            existing.properties = existing.properties or {}
            existing.attribute_dictionaries = existing.attribute_dictionaries or {}

    scene_hierarchy = InstanceNode(
        name="ROOT",
        definition_name="ROOT_MODEL",
        layer="Layer0",
        position_mm=(0.0, 0.0, 0.0),
        properties={},
        children=root_children,
        path="ROOT",
    )

    logger.info(
        "Scene build complete: %d instances, %d meshes, %d primitives, "
        "%d curve sets (%.2fs)",
        instance_counter[0], len(mesh_index), len(glb_primitives),
        len(curve_sets), time.monotonic() - t0,
    )

    return Scene(
        scene_hierarchy=scene_hierarchy,
        mesh_index=mesh_index,
        glb_primitives=glb_primitives,
        curve_sets=curve_sets,
        gltf_materials=gltf_materials,
        textures=textures,
        layer_hidden=dict(parsed.get("layer_hidden") or {}),
    )
