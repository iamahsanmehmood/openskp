"""Loose-edge chaining and analytic-arc recovery, shared by scene.py's
world-space baking and instanced_scene.py's local-space instancing.

Extracted rather than duplicated: both build a definition's loose-edge
runs from the same (builder, matrix) shape, and the arc-recovery math
(circle-vs-ellipse check, monotone-sweep unwrap) has no scene.py-specific
state at all - see each function's own docstring for the reasoning behind
it, none of which changes between the baked and instanced paths.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from . import _core

INCHES_TO_M = 0.0254


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
