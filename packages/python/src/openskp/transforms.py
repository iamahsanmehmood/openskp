"""3-D matrix and point transforms used during SKP parsing.

SketchUp stores instance placement as 4×4 affine transformation matrices
in column-major order.  This module provides helpers to multiply those
matrices, apply them to points, and convert between coordinate systems
(SketchUp uses inches / Z-up; many renderers expect metres / Y-up).

All matrices are stored as flat 16-element lists in **column-major** order
matching the layout found in the binary file.
"""

from __future__ import annotations

from typing import List, Tuple

# ── Constants ─────────────────────────────────────────────────────────────

INCHES_TO_METRES: float = 0.0254
"""Conversion factor from SketchUp inches to SI metres."""

IDENTITY_MATRIX: List[float] = [
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
]
"""4×4 identity matrix in column-major order."""


# ── Matrix helpers ────────────────────────────────────────────────────────


def multiply_matrices(a: List[float], b: List[float]) -> List[float]:
    """Multiply two 4×4 column-major matrices.

    Args:
        a: First matrix (16 floats, column-major).
        b: Second matrix (16 floats, column-major).

    Returns:
        The product ``a × b`` as a 16-element column-major list.
    """
    result: List[float] = [0.0] * 16
    for row in range(4):
        for col in range(4):
            s = 0.0
            for k in range(4):
                # column-major indexing: element (row, col) at index col*4+row
                s += a[k * 4 + row] * b[col * 4 + k]
            result[col * 4 + row] = s
    return result


def transform_point(
    matrix: List[float],
    x: float,
    y: float,
    z: float,
) -> Tuple[float, float, float]:
    """Apply a 4×4 affine transform to a 3-D point.

    Args:
        matrix: 16-element column-major transformation matrix.
        x: X coordinate.
        y: Y coordinate.
        z: Z coordinate.

    Returns:
        The transformed ``(x', y', z')`` tuple.
    """
    # M is column-major: column c starts at index c*4
    tx = matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12]
    ty = matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13]
    tz = matrix[2] * x + matrix[6] * y + matrix[10] * z + matrix[14]
    return (tx, ty, tz)


def z_up_to_y_up(x: float, y: float, z: float) -> Tuple[float, float, float]:
    """Convert from SketchUp's Z-up to a Y-up coordinate system.

    The mapping is ``(x, y, z) → (x, z, -y)``.

    Args:
        x: X coordinate.
        y: Y coordinate (originally "depth" in SketchUp).
        z: Z coordinate (originally "up" in SketchUp).

    Returns:
        Re-oriented ``(x', y', z')`` tuple.
    """
    return (x, z, -y)


def inches_to_metres(x: float, y: float, z: float) -> Tuple[float, float, float]:
    """Convert a point from inches to metres.

    Args:
        x: X in inches.
        y: Y in inches.
        z: Z in inches.

    Returns:
        ``(x, y, z)`` in metres.
    """
    return (x * INCHES_TO_METRES, y * INCHES_TO_METRES, z * INCHES_TO_METRES)


def decompose_translation(matrix: List[float]) -> Tuple[float, float, float]:
    """Extract the translation component from a 4×4 column-major matrix.

    Args:
        matrix: 16-element column-major transformation matrix.

    Returns:
        ``(tx, ty, tz)`` translation vector.
    """
    return (matrix[12], matrix[13], matrix[14])


def is_identity(matrix: List[float], tol: float = 1e-9) -> bool:
    """Check whether a 4×4 matrix is (approximately) the identity.

    Args:
        matrix: 16-element column-major transformation matrix.
        tol: Absolute tolerance for floating-point comparison.

    Returns:
        ``True`` if every element matches the identity within *tol*.
    """
    for i in range(16):
        expected = 1.0 if (i % 5 == 0) else 0.0
        if abs(matrix[i] - expected) > tol:
            return False
    return True
