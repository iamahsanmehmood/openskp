"""Per-face texture placement for the VFF writer.

SketchUp stores a positioned texture as a projective 3x3 matrix in the face's
attribute block (``0x05dd > 0x36b1 > 0x36b2 > 0x2710 > 0x2711`` front /
``0x2712`` back -> ``0x2713 > 0x2715``).  This module turns the small,
caller-facing "pin" form (three model points and the tiles-space UV each one
lands on, the shape ``create.py``'s ``front_uv`` accepts) into the matrix
SketchUp 2025 writes, and wraps it in the record tree.

The matrix convention was derived from a SketchUp-saved pair
(``research/vff-writer/stage2/oracle/tex_uv.skp``): for a face with plane basis
``(xr, yr)`` and a material whose texture tile is ``tile_w x tile_h`` inches,
the stored matrix ``M`` has an inverse whose top rows map face-plane
coordinates ``(X, Y) = (p.xr, p.yr)`` to texture-tile coordinates multiplied
by the tile size:

    [X, Y, 1] @ inv(M) = [u * tile_w, v * tile_h, 1]

for tile-space UV ``(u, v)`` (the convention ``create.py``'s pins use: 1.0 is
one full repetition of the image).  Three pins determine that affine map
exactly; ``M`` is the inverse of the assembled map.  The no-op side uses the
identity matrix with its positioned flag clear.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from .tlv import Record, f64s, record, u32

__all__ = [
    "face_uv_basis",
    "invert3",
    "uv_matrix_from_pins",
    "uv_attribute",
    "IDENTITY_MATRIX",
]

IDENTITY_MATRIX: Tuple[float, ...] = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)

T_UV_ATTR = 0x05DD
T_UV_LIST = 0x36B1
T_UV_SET = 0x36B2
T_UV_COORDS = 0x2710
T_UV_EMPTY = 0x36B0
T_UV_FRONT = 0x2711
T_UV_BACK = 0x2712
T_UV_SIDE = 0x2713
T_UV_FLAGS = 0x2714
T_UV_MATRIX = 0x2715
T_UV_OFFSET = 0x2716
T_UV_PINS = 0x2717


def face_uv_basis(
    normal: Sequence[float],
) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    """The (xr, yr) plane basis SketchUp projects textures with.

    Matches ``openskp._face_groups.face_uv_basis`` (and the reader docs):
    ``xr = normalize(Z x n)`` and ``yr = n x xr``, except for a vertical
    normal, where SketchUp keeps the world axes and looks down with ``-X``.
    """
    nx, ny, nz = (float(c) for c in normal)
    cx, cy = -ny, nx
    length = (cx * cx + cy * cy) ** 0.5
    if length < 1e-3:
        xr = (1.0 if nz >= 0 else -1.0, 0.0, 0.0)
        yr = (0.0, 1.0, 0.0)
    else:
        xr = (cx / length, cy / length, 0.0)
        yr = (
            ny * xr[2] - nz * xr[1],
            nz * xr[0] - nx * xr[2],
            nx * xr[1] - ny * xr[0],
        )
    return xr, yr


def invert3(matrix: Sequence[float]) -> List[float]:
    """Inverse of a row-major 3x3; raises on a singular matrix."""
    a, b, c, d, e, f, g, h, i = (float(v) for v in matrix)
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-15:
        raise ValueError("texture placement is degenerate (singular matrix)")
    inv = [
        (e * i - f * h) / det,
        (c * h - b * i) / det,
        (b * f - c * e) / det,
        (f * g - d * i) / det,
        (a * i - c * g) / det,
        (c * d - a * f) / det,
        (d * h - e * g) / det,
        (b * g - a * h) / det,
        (a * e - b * d) / det,
    ]
    return [v + 0.0 for v in inv]


def _solve_affine(
    points: Sequence[Tuple[float, float]],
    values: Sequence[float],
) -> Tuple[float, float, float]:
    """Solve ``value_i = a*x_i + b*y_i + c`` for three non-collinear points."""
    if len(points) != 3 or len(values) != 3:
        raise ValueError("exactly three texture pins are supported")
    (x0, y0), (x1, y1), (x2, y2) = points
    det = x0 * (y1 - y2) - y0 * (x1 - x2) + (x1 * y2 - x2 * y1)
    if abs(det) < 1e-15:
        raise ValueError("texture pins must not be collinear")
    v0, v1, v2 = (float(v) for v in values)
    a = (v0 * (y1 - y2) - y0 * (v1 - v2) + (v1 * y2 - v2 * y1)) / det
    b = (x0 * (v1 - v2) - v0 * (x1 - x2) + (x1 * v2 - x2 * v1)) / det
    c = (
        x0 * (y1 * v2 - y2 * v1)
        - y0 * (x1 * v2 - x2 * v1)
        + v0 * (x1 * y2 - x2 * y1)
    ) / det
    return a + 0.0, b + 0.0, c + 0.0


def uv_matrix_from_pins(
    pins: Sequence[Tuple[Sequence[float], Tuple[float, float]]],
    normal: Sequence[float],
    tile_width: float,
    tile_height: float,
) -> List[float]:
    """The stored 9-double matrix for three model-point/UV pins (UV in tiles)."""
    if abs(tile_width) < 1e-12 or abs(tile_height) < 1e-12:
        raise ValueError("a textured material needs a non-zero applied size")
    xr, yr = face_uv_basis(normal)
    plane_points = []
    for point, _uv in pins:
        x = sum(float(point[i]) * xr[i] for i in range(3))
        y = sum(float(point[i]) * yr[i] for i in range(3))
        plane_points.append((x, y))
    # The observed map: face-plane (X, Y) -> (tile_w * u, tile_h * v).
    qx = [u * tile_width for _point, (u, _v) in pins]
    qy = [v * tile_height for _point, (_u, v) in pins]
    a, b, e = _solve_affine(plane_points, qx)
    c, d, f = _solve_affine(plane_points, qy)
    # Row-vector form: row 0 holds the X coefficients of (u, v), row 1 the Y
    # coefficients, row 2 the translation.
    inverse = [[a, c, 0.0], [b, d, 0.0], [e, f, 1.0]]
    return invert3([v for row in inverse for v in row])


def _side(matrix: Sequence[float], positioned: bool) -> Record:
    return Record(
        T_UV_SIDE,
        children=[
            record(T_UV_FLAGS, u32(1 if positioned else 0)),
            record(T_UV_MATRIX, f64s([float(v) for v in matrix])),
            record(T_UV_OFFSET, f64s([0.0, 0.0, 0.0])),
            record(T_UV_PINS, b""),
        ],
    )


def uv_attribute(
    front: Optional[Sequence[float]] = None,
    back: Optional[Sequence[float]] = None,
) -> Record:
    """The ``0x2710`` coordinates record holding a face's texture placement."""
    coords = Record(
        T_UV_COORDS,
        children=[
            record(T_UV_EMPTY, b""),
            Record(
                T_UV_FRONT,
                children=[_side(front if front is not None else IDENTITY_MATRIX,
                                front is not None)],
            ),
            Record(
                T_UV_BACK,
                children=[_side(back if back is not None else IDENTITY_MATRIX,
                                back is not None)],
            ),
        ],
    )
    return coords