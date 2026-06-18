"""3-D planar polygon triangulation.

This module takes a 3-D polygon (co-planar points) and produces a list of
triangle indices suitable for rendering or mesh export.  The polygon is
projected onto a best-fit 2-D plane, triangulated using Shapely, and the
resulting indices map back to the original vertex list.

Dependencies:
    * **NumPy** — for projection maths.
    * **Shapely** — for robust 2-D polygon triangulation
      (``shapely.ops.triangulate``).
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np


def _project_to_2d(
    points: List[Tuple[float, float, float]],
    normal: Tuple[float, float, float],
) -> List[Tuple[float, float]]:
    """Project 3-D co-planar points onto a 2-D plane.

    The projection axes are chosen by crossing *normal* with a non-parallel
    cardinal axis.

    Args:
        points: Ordered polygon vertices in 3-D.
        normal: Unit normal of the polygon plane.

    Returns:
        Corresponding 2-D ``(u, v)`` coordinates.
    """
    n = np.array(normal, dtype=np.float64)

    # Choose a reference axis that isn't parallel to the normal
    if abs(n[0]) < 0.9:
        ref = np.array([1.0, 0.0, 0.0])
    else:
        ref = np.array([0.0, 1.0, 0.0])

    u_axis = np.cross(n, ref)
    u_len = np.linalg.norm(u_axis)
    if u_len < 1e-12:
        u_axis = np.array([1.0, 0.0, 0.0])
    else:
        u_axis /= u_len

    v_axis = np.cross(n, u_axis)
    v_len = np.linalg.norm(v_axis)
    if v_len > 1e-12:
        v_axis /= v_len

    origin = np.array(points[0], dtype=np.float64)
    result: List[Tuple[float, float]] = []
    for pt in points:
        d = np.array(pt, dtype=np.float64) - origin
        result.append((float(np.dot(d, u_axis)), float(np.dot(d, v_axis))))

    return result


def triangulate_face_3d(
    points: List[Tuple[float, float, float]],
    normal: Tuple[float, float, float],
) -> List[int]:
    """Triangulate a 3-D planar polygon and return triangle indices.

    The polygon is projected to 2-D via :func:`_project_to_2d`, then
    triangulated using Shapely.  If Shapely fails (e.g. self-intersecting
    polygon), a simple fan triangulation is used as a fallback.

    Args:
        points: Ordered polygon vertices — at least 3 points.
        normal: Outward unit normal of the polygon's plane.

    Returns:
        A flat list of vertex indices.  Every consecutive triplet
        ``(i0, i1, i2)`` defines one triangle referencing positions
        in *points*.  Returns an empty list for degenerate input.
    """
    n = len(points)
    if n < 3:
        return []

    # Trivial case
    if n == 3:
        return [0, 1, 2]

    pts_2d = _project_to_2d(points, normal)

    try:
        from shapely.geometry import Polygon as ShapelyPolygon
        from shapely.ops import triangulate as delaunay_triangulate

        poly = ShapelyPolygon(pts_2d)
        if not poly.is_valid:
            poly = poly.buffer(0)

        triangles = delaunay_triangulate(poly)
        indices: List[int] = []

        for tri in triangles:
            # Only keep triangles whose centroid lies inside the polygon
            if not poly.contains(tri.centroid):
                continue
            coords = list(tri.exterior.coords)[:-1]  # drop closing coord
            tri_idx: List[int] = []
            for cx, cy in coords:
                # Find nearest original vertex
                best = 0
                best_dist = float("inf")
                for j, (px, py) in enumerate(pts_2d):
                    d = (cx - px) ** 2 + (cy - py) ** 2
                    if d < best_dist:
                        best_dist = d
                        best = j
                tri_idx.append(best)
            if len(tri_idx) == 3 and len(set(tri_idx)) == 3:
                indices.extend(tri_idx)

        if indices:
            return indices

    except Exception:
        pass

    # Fallback: simple fan triangulation from vertex 0
    return _fan_triangulate(n)


def _fan_triangulate(n: int) -> List[int]:
    """Produce a simple fan triangulation for an *n*-gon.

    All triangles share vertex ``0``.  This is only geometrically
    correct for convex polygons.

    Args:
        n: Number of polygon vertices (≥ 3).

    Returns:
        Flat list of triangle indices.
    """
    indices: List[int] = []
    for i in range(1, n - 1):
        indices.extend([0, i, i + 1])
    return indices
