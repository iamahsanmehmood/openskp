//! Instance transform helpers (`transforms.cpp`). Matrices are the 13-value
//! form: 0-8 the row-major 3x3, 9-11 the translation, 12 the scale term.

use crate::model::Vec3;

pub fn transform_determinant(m: &[f64]) -> f64 {
    if m.len() < 9 {
        return 1.0;
    }
    m[0] * (m[4] * m[8] - m[5] * m[7]) - m[1] * (m[3] * m[8] - m[5] * m[6])
        + m[2] * (m[3] * m[7] - m[4] * m[6])
}

/// Folds a 13-value matrix's scale term (index 12) into its rotation and
/// translation, so index 12 is 1.
///
/// SketchUp can keep a uniform scale in the transformation's homogeneous term:
/// the 3x3 stays as it is, the translation is in the scaled units, and the 16th
/// value `w` is the divisor. The 13-value matrix stores `1/w` at index 12, so a
/// point transforms as `s * (R p + t)` with `s = m[12]`; folding gives the
/// equivalent `(s R) p + s t` with `s = 1`, which every consumer that reads
/// only indices 0-11 handles correctly (openskp#444). A matrix whose scale is
/// already 1 is returned unchanged.
pub fn fold_scale(m: &[f64]) -> Vec<f64> {
    if m.len() < 13 || m[12] == 1.0 {
        return m.to_vec();
    }
    let mut o = vec![0.0; 13];
    for i in 0..12 {
        o[i] = m[i] * m[12];
    }
    o[12] = 1.0;
    o
}

/// The first 12 values of `fold_scale(m)` without allocating.
#[inline]
fn folded12(m: &[f64]) -> [f64; 12] {
    let mut o = [0.0; 12];
    if m.len() >= 13 && m[12] != 1.0 {
        for i in 0..12 {
            o[i] = m[i] * m[12];
        }
    } else {
        o.copy_from_slice(&m[..12]);
    }
    o
}

pub fn transform_point(matrix: &[f64], p: Vec3) -> Vec3 {
    if matrix.len() < 12 {
        return p;
    }
    let m = folded12(matrix);
    [
        m[0] * p[0] + m[1] * p[1] + m[2] * p[2] + m[9],
        m[3] * p[0] + m[4] * p[1] + m[5] * p[2] + m[10],
        m[6] * p[0] + m[7] * p[1] + m[8] * p[2] + m[11],
    ]
}

/// Transforms a normal by the inverse transpose (the cofactor matrix, with the
/// determinant's sign so mirrors keep normals pointing outward).
pub fn transform_normal(m: &[f64], n: Vec3) -> Vec3 {
    if m.len() < 9 {
        return n;
    }
    let determinant = transform_determinant(m);
    if determinant == 0.0 {
        return [
            m[0] * n[0] + m[1] * n[1] + m[2] * n[2],
            m[3] * n[0] + m[4] * n[1] + m[5] * n[2],
            m[6] * n[0] + m[7] * n[1] + m[8] * n[2],
        ];
    }
    let sign = if determinant < 0.0 { -1.0 } else { 1.0 };
    [
        sign * ((m[4] * m[8] - m[5] * m[7]) * n[0]
            + (m[5] * m[6] - m[3] * m[8]) * n[1]
            + (m[3] * m[7] - m[4] * m[6]) * n[2]),
        sign * ((m[2] * m[7] - m[1] * m[8]) * n[0]
            + (m[0] * m[8] - m[2] * m[6]) * n[1]
            + (m[1] * m[6] - m[0] * m[7]) * n[2]),
        sign * ((m[1] * m[5] - m[2] * m[4]) * n[0]
            + (m[2] * m[3] - m[0] * m[5]) * n[1]
            + (m[0] * m[4] - m[1] * m[3]) * n[2]),
    ]
}

/// `a x b` (apply `b`, then `a`), with both scale terms folded in first so the
/// product's own scale term is 1.
pub fn multiply_matrices(a: &[f64], b: &[f64]) -> Vec<f64> {
    if a.is_empty() {
        return if b.len() == 13 {
            fold_scale(b)
        } else {
            b.to_vec()
        };
    }
    if b.is_empty() {
        return if a.len() == 13 {
            fold_scale(a)
        } else {
            a.to_vec()
        };
    }
    let mut p = vec![0.0; 13];
    let mut c = vec![0.0; 13];
    for i in 0..13 {
        p[i] = a.get(i).copied().unwrap_or(0.0);
        c[i] = b.get(i).copied().unwrap_or(0.0);
    }
    if a.len() < 13 {
        p[12] = 1.0;
    }
    if b.len() < 13 {
        c[12] = 1.0;
    }
    let p = fold_scale(&p);
    let c = fold_scale(&c);
    let mut o = vec![0.0; 13];
    for r in 0..3 {
        for q in 0..3 {
            o[r * 3 + q] = p[r * 3] * c[q] + p[r * 3 + 1] * c[3 + q] + p[r * 3 + 2] * c[6 + q];
        }
    }
    for r in 0..3 {
        o[9 + r] = p[r * 3] * c[9] + p[r * 3 + 1] * c[10] + p[r * 3 + 2] * c[11] + p[9 + r];
    }
    o[12] = 1.0; // both operands were folded (index 12 is 1)
    o
}
