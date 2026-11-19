//! Small geometry/linear-algebra helpers for the writer.
//!
//! Every formula keeps the exact operation order of the C++ writer: these values are written
//! straight into the file as f64s, so any reassociation would change output bytes.

use std::f64::consts::PI;

use super::{Matrix3x3, Point3, Rotation, SkpWriteError, UvPoint};

type Mat3 = [[f64; 3]; 3];

/// `std::min`/`std::max` semantics (`b < a ? b : a`), kept for identical NaN handling.
fn min_like_cpp(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

fn max_like_cpp(a: f64, b: f64) -> f64 {
    if a < b {
        b
    } else {
        a
    }
}

fn det3(m: &Mat3) -> f64 {
    m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
}

/// Cramer's rule.
fn solve3x3(a: &Mat3, b: &[f64; 3]) -> Result<[f64; 3], SkpWriteError> {
    let d = det3(a);
    if d.abs() < 1e-9 {
        return Err(SkpWriteError::new(
            "the 3 texture-positioning points map to collinear (u, v) coordinates - \
             cannot determine a texture mapping from them",
        ));
    }
    let mut cols = [0.0; 3];
    for (col, out) in cols.iter_mut().enumerate() {
        let mut ai = *a;
        for r in 0..3 {
            ai[r][col] = b[r];
        }
        *out = det3(&ai) / d;
    }
    Ok(cols)
}

pub(super) fn cross3(a: Point3, b: Point3) -> Point3 {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}

fn length3(v: Point3) -> f64 {
    (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]).sqrt()
}

pub(super) fn normalize3(v: Point3) -> Result<Point3, SkpWriteError> {
    let length = length3(v);
    if length < 1e-9 {
        return Err(SkpWriteError::new("cannot normalize a zero-length vector"));
    }
    Ok([v[0] / length, v[1] / length, v[2] / length])
}

/// Row-major 3x3 rotation by `angle` radians (right-hand rule) around `axis` (any length) -
/// Rodrigues' formula.
fn rotation_matrix3x3(axis: Point3, angle: f64) -> Result<Matrix3x3, SkpWriteError> {
    let length = length3(axis);
    if length < 1e-9 {
        return Err(SkpWriteError::new(
            "rotation axis must not be the zero vector",
        ));
    }
    let (x, y, z) = (axis[0] / length, axis[1] / length, axis[2] / length);
    let (c, s) = (angle.cos(), angle.sin());
    let t = 1.0 - c;
    Ok([
        t * x * x + c,
        t * x * y - s * z,
        t * x * z + s * y,
        t * x * y + s * z,
        t * y * y + c,
        t * y * z - s * x,
        t * x * z - s * y,
        t * y * z + s * x,
        t * z * z + c,
    ])
}

/// `matrix3x3` and `rotation` are two spellings of the same transform field, so at most one may
/// be given (neither means identity).
pub(super) fn resolve_matrix3x3(
    matrix3x3: Option<Matrix3x3>,
    rotation: Option<Rotation>,
) -> Result<Option<Matrix3x3>, SkpWriteError> {
    match (matrix3x3, rotation) {
        (Some(_), Some(_)) => Err(SkpWriteError::new(
            "pass at most one of matrix3x3/rotation - rotation is just a convenience for matrix3x3",
        )),
        (_, Some((axis, angle))) => rotation_matrix3x3(axis, angle).map(Some),
        (m, None) => Ok(m),
    }
}

/// Below this tilt sine a face counts as horizontal (`VERTICAL_TOLERANCE`).
const UV_VERTICAL_TOLERANCE: f64 = 1e-3;

/// Face-plane UV basis (xr, yr) from a unit face normal, as measured against the SDK.
///
/// U = normalize(Z x n), V = n x U. A horizontal face uses world (X, Y); a face looking DOWN
/// uses (-X, +Y) - the 180-degree turn of the upward basis, not the (X, -Y) mirror a first-edge
/// writer produced. Z x n is discontinuous at vertical, so a near-horizontal face snaps to those
/// world axes instead of flipping with float noise. The face's vertex order does not enter.
fn face_uv_basis(n: Point3) -> (Point3, Point3) {
    let (cx, cy) = (-n[1], n[0]);
    let clen = (cx * cx + cy * cy).sqrt();
    if clen < UV_VERTICAL_TOLERANCE {
        let xr = if n[2] >= 0.0 {
            [1.0, 0.0, 0.0]
        } else {
            [-1.0, 0.0, 0.0]
        };
        return (xr, [0.0, 1.0, 0.0]);
    }
    let xr = [cx / clen, cy / clen, 0.0];
    let yr = [
        n[1] * xr[2] - n[2] * xr[1],
        n[2] * xr[0] - n[0] * xr[2],
        n[0] * xr[1] - n[1] * xr[0],
    ];
    (xr, yr)
}

/// An arbitrary orthonormal in-plane basis (U, W) for a circle/arc plane: Gram-Schmidt whichever
/// of world +Z/+X is less parallel to `normal`, then W = normal x U.
pub(super) fn circle_basis(normal: Point3) -> Result<(Point3, Point3), SkpWriteError> {
    let seed = if normal[2].abs() < 0.9 {
        [0.0, 0.0, 1.0]
    } else {
        [1.0, 0.0, 0.0]
    };
    let dot = seed[0] * normal[0] + seed[1] * normal[1] + seed[2] * normal[2];
    let u_raw = [
        seed[0] - dot * normal[0],
        seed[1] - dot * normal[1],
        seed[2] - dot * normal[2],
    ];
    let u = normalize3(u_raw)?;
    let w = normalize3(cross3(normal, u))?;
    Ok((u, w))
}

fn point_on_circle(center: Point3, radius: f64, u: Point3, w: Point3, angle: f64) -> Point3 {
    let (c, s) = (angle.cos(), angle.sin());
    [
        center[0] + radius * (c * u[0] + s * w[0]),
        center[1] + radius * (c * u[1] + s * w[1]),
        center[2] + radius * (c * u[2] + s * w[2]),
    ]
}

pub(super) fn circle_points(
    center: Point3,
    radius: f64,
    num_segments: u32,
    u: Point3,
    w: Point3,
) -> Vec<Point3> {
    let n = f64::from(num_segments);
    (0..num_segments)
        .map(|i| point_on_circle(center, radius, u, w, 2.0 * PI * f64::from(i) / n))
        .collect()
}

/// The `num_segments + 1` points (both endpoints included) of a partial arc.
pub(super) fn arc_points(
    center: Point3,
    radius: f64,
    num_segments: u32,
    (u, w): (Point3, Point3),
    start_angle: f64,
    end_angle: f64,
) -> Vec<Point3> {
    let n = f64::from(num_segments);
    (0..=num_segments)
        .map(|i| {
            let angle = start_angle + (end_angle - start_angle) * f64::from(i) / n;
            point_on_circle(center, radius, u, w, angle)
        })
        .collect()
}

/// Fit the 3x3 UV-to-world affine matrix real SketchUp stores for a positioned texture, from
/// exactly 3 (world point, (u, v)) correspondences, in the face's own UV basis.
pub(super) fn uv_matrix_for_face(
    pairs: &[UvPoint],
    normal: Point3,
) -> Result<Matrix3x3, SkpWriteError> {
    if pairs.len() != 3 {
        return Err(SkpWriteError::new(
            "texture positioning needs exactly 3 (point, uv) pairs",
        ));
    }
    let (u_axis, w_axis) = face_uv_basis(normal);
    let mut a: Mat3 = [[0.0; 3]; 3];
    let mut bx = [0.0; 3];
    let mut by = [0.0; 3];
    for (i, (pt, uv)) in pairs.iter().enumerate() {
        a[i] = [uv[0], uv[1], 1.0];
        bx[i] = pt[0] * u_axis[0] + pt[1] * u_axis[1] + pt[2] * u_axis[2];
        by[i] = pt[0] * w_axis[0] + pt[1] * w_axis[1] + pt[2] * w_axis[2];
    }
    let [a0, c0, e0] = solve3x3(&a, &bx)?;
    let [b0, d0, f0] = solve3x3(&a, &by)?;
    Ok([a0, b0, 0.0, c0, d0, 0.0, e0, f0, 1.0])
}

/// The largest axis-aligned extent of `points`.
pub(super) fn polygon_span(points: &[Point3]) -> f64 {
    let mut span = 0.0;
    for axis in 0..3 {
        let mut mn = points[0][axis];
        let mut mx = mn;
        for p in points {
            mn = min_like_cpp(mn, p[axis]);
            mx = max_like_cpp(mx, p[axis]);
        }
        span = max_like_cpp(span, mx - mn);
    }
    span
}

/// The coplanarity tolerance for a polygon: scales with the face's own size.
pub(super) fn plane_tolerance(points: &[Point3]) -> f64 {
    max_like_cpp(polygon_span(points), 1.0) * 1e-6
}

/// A plane `nx*x + ny*y + nz*z = d` with a unit normal.
#[derive(Clone, Copy, Debug)]
pub(super) struct Plane {
    pub nx: f64,
    pub ny: f64,
    pub nz: f64,
    pub d: f64,
}

impl Plane {
    pub fn normal(&self) -> Point3 {
        [self.nx, self.ny, self.nz]
    }

    pub fn distance(&self, p: Point3) -> f64 {
        self.nx * p[0] + self.ny * p[1] + self.nz * p[2] - self.d
    }
}

/// Newell's method: sums a cross-product-like term over every edge rather than reading the
/// normal off the first 3 points - correct for concave polygons too, as long as the polygon is
/// planar and simple.
pub(super) fn newell_plane(points: &[Point3]) -> Result<Plane, SkpWriteError> {
    let n = points.len();
    let (mut nx, mut ny, mut nz) = (0.0, 0.0, 0.0);
    for i in 0..n {
        let p0 = points[i];
        let p1 = points[(i + 1) % n];
        nx += (p0[1] - p1[1]) * (p0[2] + p1[2]);
        ny += (p0[2] - p1[2]) * (p0[0] + p1[0]);
        nz += (p0[0] - p1[0]) * (p0[1] + p1[1]);
    }
    let length = (nx * nx + ny * ny + nz * nz).sqrt();
    if length < 1e-9 {
        return Err(SkpWriteError::new(
            "face points are collinear or degenerate; cannot compute a plane",
        ));
    }
    nx /= length;
    ny /= length;
    nz /= length;
    let (mut cx, mut cy, mut cz) = (0.0, 0.0, 0.0);
    for p in points {
        cx += p[0];
        cy += p[1];
        cz += p[2];
    }
    let count = n as f64;
    cx /= count;
    cy /= count;
    cz /= count;
    let d = nx * cx + ny * cy + nz * cz;
    Ok(Plane { nx, ny, nz, d })
}

/// The polygon's plane; every point must actually lie on it.
pub(super) fn plane_from_polygon(points: &[Point3]) -> Result<Plane, SkpWriteError> {
    let plane = newell_plane(points)?;
    let tol = plane_tolerance(points);
    if points.iter().any(|&p| plane.distance(p).abs() > tol) {
        return Err(SkpWriteError::new(
            "face points are not coplanar - openskp::create only supports planar faces \
             (pass auto_triangulate=true in FaceOptions to fan-split a non-planar polygon instead)",
        ));
    }
    Ok(plane)
}

/// Same fit/tolerance as [`plane_from_polygon`], but answers instead of failing on a non-planar
/// polygon (still fails on a degenerate one).
pub(super) fn is_coplanar(points: &[Point3]) -> Result<bool, SkpWriteError> {
    let plane = newell_plane(points)?;
    let tol = plane_tolerance(points);
    // `!any(> tol)` rather than `all(<= tol)`: a NaN distance counts as on-plane, as in C++.
    Ok(!points.iter().any(|&p| plane.distance(p).abs() > tol))
}
