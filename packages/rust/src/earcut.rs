//! 2D ear-clipping with holes (`earcut.cpp`): `EarPoint` and `earcut_2d`.
//!
//! Every holeless loop is clipped directly; each hole is bridged into the
//! outer boundary from its right-most vertex to the nearest boundary vertex
//! on its right. When two or more holes genuinely overlap, bridging them
//! independently can produce a self-intersecting ring, so the outer boundary
//! is clipped alone and triangles whose centroid falls inside any hole are
//! dropped instead (openskp#285).

use crate::model::EntityId;

/// One projected 2D vertex, tagged with the source vertex id.
#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct EarPoint {
    pub x: f64,
    pub y: f64,
    pub id: EntityId,
}

fn area(p: &[EarPoint]) -> f64 {
    let mut a = 0.0;
    for i in 0..p.len() {
        let q = &p[(i + 1) % p.len()];
        a += p[i].x * q.y - q.x * p[i].y;
    }
    a * 0.5
}

fn cross(a: &EarPoint, b: &EarPoint, c: &EarPoint) -> f64 {
    (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)
}

fn inside(p: &EarPoint, a: &EarPoint, b: &EarPoint, c: &EarPoint) -> bool {
    let x = cross(a, b, p);
    let y = cross(b, c, p);
    let z = cross(c, a, p);
    x >= -1e-12 && y >= -1e-12 && z >= -1e-12
}

fn clip(mut p: Vec<EarPoint>) -> Vec<[EntityId; 3]> {
    let mut out = Vec::new();
    if area(&p) < 0.0 {
        p.reverse();
    }
    let mut guard = 0usize;
    while p.len() > 3 && {
        let g = guard;
        guard += 1;
        g < p.len() * p.len() * 4
    } {
        let n = p.len();
        let mut cut = false;
        for i in 0..n {
            let a = (i + n - 1) % n;
            let c = (i + 1) % n;
            if cross(&p[a], &p[i], &p[c]) <= 1e-12 {
                continue;
            }
            let hit = (0..n).any(|j| {
                j != a
                    && j != i
                    && j != c
                    && inside(&p[j], &p[a], &p[i], &p[c])
                    && p[j].id != p[a].id
                    && p[j].id != p[i].id
                    && p[j].id != p[c].id
            });
            if hit {
                continue;
            }
            out.push([p[a].id, p[i].id, p[c].id]);
            p.remove(i);
            cut = true;
            break;
        }
        if !cut {
            for i in 0..n {
                let a = (i + n - 1) % n;
                let c = (i + 1) % n;
                if cross(&p[a], &p[i], &p[c]).abs() < 1e-10 {
                    p.remove(i);
                    cut = true;
                    break;
                }
            }
            if !cut {
                break;
            }
        }
    }
    if p.len() == 3 && cross(&p[0], &p[1], &p[2]).abs() > 1e-12 {
        out.push([p[0].id, p[1].id, p[2].id]);
    }
    out
}

/// Ray-casting point-in-polygon (even-odd rule): true when `(x, y)` lies
/// strictly inside the closed ring.
fn point_in_polygon(x: f64, y: f64, polygon: &[EarPoint]) -> bool {
    let mut inside_result = false;
    let n = polygon.len();
    if n == 0 {
        return false;
    }
    let mut j = n - 1;
    for i in 0..n {
        let (xi, yi) = (polygon[i].x, polygon[i].y);
        let (xj, yj) = (polygon[j].x, polygon[j].y);
        let intersects = ((yi > y) != (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi);
        if intersects {
            inside_result = !inside_result;
        }
        j = i;
    }
    inside_result
}

fn any_vertex_inside(points: &[EarPoint], polygon: &[EarPoint]) -> bool {
    points.iter().any(|p| point_in_polygon(p.x, p.y, polygon))
}

/// True when at least one pair of hole loops (index 1+) shares real area,
/// detected by a vertex of one lying inside the other.
fn holes_overlap(loops: &[Vec<EarPoint>]) -> bool {
    for i in 1..loops.len() {
        for j in i + 1..loops.len() {
            if any_vertex_inside(&loops[i], &loops[j]) || any_vertex_inside(&loops[j], &loops[i]) {
                return true;
            }
        }
    }
    false
}

/// Fallback for overlapping holes: clip the outer boundary alone and drop
/// every triangle whose centroid lies inside any hole.
fn triangulate_by_filtering_holes(loops: &[Vec<EarPoint>]) -> Vec<[EntityId; 3]> {
    let outer_tris = clip(loops[0].clone());
    let mut by_id = std::collections::HashMap::new();
    for p in &loops[0] {
        by_id.insert(p.id, *p);
    }
    let mut result = Vec::new();
    for tri in outer_tris {
        let a = by_id[&tri[0]];
        let b = by_id[&tri[1]];
        let c = by_id[&tri[2]];
        let cx = (a.x + b.x + c.x) / 3.0;
        let cy = (a.y + b.y + c.y) / 3.0;
        let inside_any_hole = loops[1..].iter().any(|hole| point_in_polygon(cx, cy, hole));
        if !inside_any_hole {
            result.push(tri);
        }
    }
    result
}

/// Triangulates `loops[0]` (outer boundary) minus `loops[1..]` (holes),
/// returning vertex-id triples.
pub fn earcut_2d(mut loops: Vec<Vec<EarPoint>>) -> Vec<[EntityId; 3]> {
    if loops.is_empty() {
        return Vec::new();
    }
    if loops.len() > 2 && holes_overlap(&loops) {
        return triangulate_by_filtering_holes(&loops);
    }
    let mut poly = std::mem::take(&mut loops[0]);
    if area(&poly) < 0.0 {
        poly.reverse();
    }
    for hole in loops.iter_mut().skip(1) {
        let mut hole = std::mem::take(hole);
        if hole.is_empty() {
            continue;
        }
        // The C++ reads poly[0] here even when the outer loop is empty
        // (undefined behavior); there is nothing to bridge into.
        if poly.is_empty() {
            break;
        }
        if area(&hole) > 0.0 {
            hole.reverse();
        }
        let mut hi = 0;
        for i in 1..hole.len() {
            if hole[i].x > hole[hi].x || (hole[i].x == hole[hi].x && hole[i].y < hole[hi].y) {
                hi = i;
            }
        }
        let mut oi = 0;
        let mut best = 1e300;
        for (i, q) in poly.iter().enumerate() {
            let dx = q.x - hole[hi].x;
            if dx >= 0.0 {
                let d = dx * dx + (q.y - hole[hi].y) * (q.y - hole[hi].y);
                if d < best {
                    best = d;
                    oi = i;
                }
            }
        }
        let mut merged = Vec::with_capacity(poly.len() + hole.len() + 2);
        merged.extend_from_slice(&poly[..=oi]);
        merged.push(hole[hi]);
        for k in 1..hole.len() {
            merged.push(hole[(hi + k) % hole.len()]);
        }
        merged.push(hole[hi]);
        merged.push(poly[oi]);
        merged.extend_from_slice(&poly[oi + 1..]);
        poly = merged;
    }
    clip(poly)
}
