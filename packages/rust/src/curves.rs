//! Loose-edge chaining and analytic-arc recovery, shared by the baked and the
//! instanced scene builders (Python port extension: `openskp/_curves.py`; the
//! C++ port has no equivalent).
//!
//! Loose edges - edges no face uses - are how SketchUp stores drawing
//! geometry (a facade elevation, a section outline). Edges bounding a face are
//! already represented by that face's mesh and are not emitted again.
//!
//! The run order follows the Python reference exactly, including the
//! iteration order of the CPython `set` it walks a curve's edges from (see
//! [`py_set_order`]), so a run comes out in the same direction there and here.

use std::collections::{HashMap, HashSet};

use crate::internal::{transform_point, GeometryBuilder};
use crate::model::{EntityId, Vec3};
use crate::scene::CurveArc;

/// Inches to metres.
pub const INCHES_TO_M: f64 = 0.0254;

/// One chained run of loose edges: its edges in walk order, the vertex ids
/// along it, and whether it returns to its first vertex (which is then NOT
/// repeated at the end).
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct LooseRun {
    pub edge_ids: Vec<EntityId>,
    pub vertex_ids: Vec<EntityId>,
    pub closed: bool,
}

/// Python's `round(x, 6)`: the double nearest the decimal rounding of `x`'s
/// exact value to 6 places, ties to even.
pub fn round6(x: f64) -> f64 {
    if !x.is_finite() {
        return x;
    }
    // Rust's fixed-precision formatting rounds the exact binary value, ties
    // to even - the same decimal string CPython's dtoa produces - and parsing
    // it back is correctly rounded, like CPython's strtod.
    format!("{x:.6}").parse().unwrap_or(x)
}

/// CPython's `hash(int)` for an `i64`.
fn py_int_hash(v: i64) -> i64 {
    const MODULUS: i128 = (1 << 61) - 1;
    let v = i128::from(v);
    let h = if v >= 0 {
        v % MODULUS
    } else {
        -((-v) % MODULUS)
    };
    let h = h as i64;
    if h == -1 {
        -2
    } else {
        h
    }
}

/// The iteration order of CPython's `set(keys)` for integer keys
/// (`Objects/setobject.c`: open addressing with 9 linear probes, then
/// perturbed probing; the table grows 4x once 3/5 full). Duplicates keep their
/// first occurrence.
pub fn py_set_order(keys: &[EntityId]) -> Vec<EntityId> {
    const LINEAR_PROBES: u64 = 9;
    const PERTURB_SHIFT: u32 = 5;

    fn insert_clean(table: &mut [Option<(EntityId, i64)>], key: EntityId, hash: i64) {
        let mask = table.len() as u64 - 1;
        let mut perturb = hash as u64;
        let mut i = (hash as u64) & mask;
        loop {
            if table[i as usize].is_none() {
                table[i as usize] = Some((key, hash));
                return;
            }
            if i + LINEAR_PROBES <= mask {
                for j in 1..=LINEAR_PROBES {
                    if table[(i + j) as usize].is_none() {
                        table[(i + j) as usize] = Some((key, hash));
                        return;
                    }
                }
            }
            perturb >>= PERTURB_SHIFT;
            i = (i.wrapping_mul(5).wrapping_add(1).wrapping_add(perturb)) & mask;
        }
    }

    let mut table: Vec<Option<(EntityId, i64)>> = vec![None; 8];
    let mut seen = HashSet::new();
    let mut used = 0usize;
    for &key in keys {
        if !seen.insert(key) {
            continue;
        }
        insert_clean(&mut table, key, py_int_hash(key));
        used += 1;
        // No deletions happen while building, so fill == used.
        let mask = table.len() - 1;
        if used * 5 >= mask * 3 {
            let minused = if used > 50000 { used * 2 } else { used * 4 };
            let mut newsize = 8usize;
            while newsize <= minused {
                newsize <<= 1;
            }
            let old = std::mem::replace(&mut table, vec![None; newsize]);
            for (k, h) in old.into_iter().flatten() {
                insert_clean(&mut table, k, h);
            }
        }
    }
    table.into_iter().flatten().map(|(k, _)| k).collect()
}

/// An insertion-ordered map (Python `dict`) from a small key to a value.
struct OrderedMap<K, V> {
    keys: Vec<K>,
    values: Vec<V>,
    index: HashMap<K, usize>,
}

impl<K: Copy + Eq + std::hash::Hash, V: Default> OrderedMap<K, V> {
    fn new() -> Self {
        Self {
            keys: Vec::new(),
            values: Vec::new(),
            index: HashMap::new(),
        }
    }

    fn entry(&mut self, k: K) -> &mut V {
        let i = *self.index.entry(k).or_insert_with(|| {
            self.keys.push(k);
            self.values.push(V::default());
            self.keys.len() - 1
        });
        &mut self.values[i]
    }

    fn get(&self, k: &K) -> Option<&V> {
        self.index.get(k).map(|&i| &self.values[i])
    }
}

/// The first key with the highest count, in insertion order (Python's
/// `max(counts, key=counts.get)`).
fn majority(counts: &OrderedMap<EntityId, usize>) -> Option<EntityId> {
    let mut best: Option<(EntityId, usize)> = None;
    for (k, &n) in counts.keys.iter().zip(&counts.values) {
        if best.is_none_or(|(_, b)| n > b) {
            best = Some((*k, n));
        }
    }
    best.map(|(k, _)| k)
}

/// The majority layer id (first wins a tie) among `ids`, ignoring absent and
/// zero ids; `None` when there is none.
pub(crate) fn majority_layer(ids: impl Iterator<Item = Option<EntityId>>) -> Option<EntityId> {
    let mut counts: OrderedMap<EntityId, usize> = OrderedMap::new();
    for id in ids.flatten().filter(|&id| id != 0) {
        *counts.entry(id) += 1;
    }
    majority(&counts)
}

/// Orders ONE curve's edges into runs. A SketchUp Curve is a path or a single
/// cycle, so there is only one way to continue; a branched group (which
/// SketchUp does not produce) is emitted as its longest walk and the rest
/// ordered again.
fn order_curve(b: &GeometryBuilder, eids: &[EntityId]) -> Vec<LooseRun> {
    let order = py_set_order(eids);
    let mut remaining: HashSet<EntityId> = eids.iter().copied().collect();
    let mut runs = Vec::new();
    let ends = |e: EntityId| {
        let pair = b.edges.get(&e).copied().unwrap_or_default();
        (pair.0.unwrap_or_default(), pair.1.unwrap_or_default())
    };
    while !remaining.is_empty() {
        let mut adj: OrderedMap<EntityId, Vec<(EntityId, EntityId)>> = OrderedMap::new();
        for &e in order.iter().filter(|e| remaining.contains(e)) {
            let (a, c) = ends(e);
            adj.entry(a).push((e, c));
            adj.entry(c).push((e, a));
        }
        // Start at a degree-1 vertex when there is one, so an open path comes
        // out end-to-end; a cycle has none, so any vertex will do.
        let start = adj
            .keys
            .iter()
            .zip(&adj.values)
            .find(|(_, v)| v.len() == 1)
            .map(|(k, _)| *k)
            .unwrap_or(adj.keys[0]);
        let (eid, mut nxt) = adj.get(&start).expect("start vertex")[0];
        remaining.remove(&eid);
        let mut chain_eids = vec![eid];
        let mut chain = vec![start, nxt];
        let mut closed = false;
        loop {
            let next = adj
                .get(&nxt)
                .and_then(|opts| opts.iter().find(|t| remaining.contains(&t.0)).copied());
            let Some((e2, v2)) = next else { break };
            remaining.remove(&e2);
            chain_eids.push(e2);
            if v2 == chain[0] {
                closed = true;
                break;
            }
            chain.push(v2);
            nxt = v2;
        }
        runs.push(LooseRun {
            edge_ids: chain_eids,
            vertex_ids: chain,
            closed,
        });
    }
    runs
}

/// Turns a definition's *loose* edges into polyline runs.
///
/// Grouping comes from the file (`edge_curves`, SketchUp's own `Edge#curve`),
/// not from vertex adjacency: an edge with no curve is its own two-point run,
/// and two curves touching at a vertex stay two runs. Ungrouped edges come
/// first (in edge order), then each curve in first-seen order.
pub fn chain_loose_edges(b: &GeometryBuilder) -> Vec<LooseRun> {
    let used: HashSet<EntityId> = b
        .faces
        .values()
        .flat_map(|f| f.loops.iter().flatten().map(|c| c.edge_id))
        .collect();

    let mut groups: OrderedMap<EntityId, Vec<EntityId>> = OrderedMap::new();
    let mut runs = Vec::new();
    for (&eid, pair) in &b.edges {
        if used.contains(&eid) {
            continue;
        }
        let (Some(a), Some(c)) = *pair else { continue };
        if a == c {
            continue;
        }
        match b.edge_curves.get(&eid).copied().filter(|&cid| cid != 0) {
            Some(cid) => groups.entry(cid).push(eid),
            None => runs.push(LooseRun {
                edge_ids: vec![eid],
                vertex_ids: vec![a, c],
                closed: false,
            }),
        }
    }
    for eids in &groups.values {
        runs.extend(order_curve(b, eids));
    }
    runs
}

/// CPython 3.12+'s `sum()` of floats (Neumaier-compensated), starting from
/// the int 0.
fn py_sum(values: &[f64]) -> f64 {
    let Some((&first, rest)) = values.split_first() else {
        return 0.0;
    };
    let mut f_result = first;
    let mut c = 0.0;
    for &x in rest {
        let t = f_result + x;
        if f_result.abs() >= x.abs() {
            c += (f_result - t) + x;
        } else {
            c += (x - t) + f_result;
        }
        f_result = t;
    }
    if c != 0.0 && c.is_finite() {
        f_result += c;
    }
    f_result
}

fn dist(p: &Vec3, q: &Vec3) -> f64 {
    let d = [p[0] - q[0], p[1] - q[1], p[2] - q[2]];
    (d[0] * d[0] + d[1] * d[1] + d[2] * d[2]).sqrt()
}

/// Recovers the analytic arc behind one loose-edge run, or `None` (the
/// answer for almost every run, and the safe one: the caller then keeps the
/// chords the file stores).
///
/// The frame (`arc_curves`) is used only if (1) the run is the whole curve,
/// (2) the frame is a circle (`y_axis == normal x x_axis`, not the affine
/// image of one), and (3) every point lies on that circle. `pts` are the
/// run's points as the caller produced them (metres, glTF Y-up, rounded to 6
/// places) after transforming by `matrix` (`None` = local space); the result
/// is in the same frame. A full turn comes back as two half-turn segments.
pub fn solve_run_arc(
    b: &GeometryBuilder,
    eids: &[EntityId],
    matrix: Option<&[f64]>,
    pts: &[Vec3],
    closed: bool,
) -> Option<CurveArc> {
    if b.arc_curves.is_empty() {
        return None;
    }
    let cid = *b.edge_curves.get(eids.first()?)?;
    let frame = b.arc_curves.get(&cid)?;
    if b.edge_curves.values().filter(|&&v| v == cid).count() != eids.len() {
        return None;
    }

    let xf_point = |p: Vec3| {
        let q = match matrix {
            Some(m) => transform_point(m, p),
            None => p,
        };
        [q[0] * INCHES_TO_M, q[2] * INCHES_TO_M, -q[1] * INCHES_TO_M]
    };
    // The linear part only (a direction), unfolded.
    let xf_vec = |v: Vec3| {
        let (x, y, z) = match matrix {
            Some(m) if m.len() >= 12 => (
                m[0] * v[0] + m[1] * v[1] + m[2] * v[2],
                m[3] * v[0] + m[4] * v[1] + m[5] * v[2],
                m[6] * v[0] + m[7] * v[1] + m[8] * v[2],
            ),
            _ => (v[0], v[1], v[2]),
        };
        [x * INCHES_TO_M, z * INCHES_TO_M, -y * INCHES_TO_M]
    };

    let c = xf_point(frame.center);
    let xa = xf_vec(frame.x_axis);
    let ya = xf_vec(frame.y_axis);
    let na = xf_vec(frame.normal);

    let r2 = py_sum(&[xa[0] * xa[0], xa[1] * xa[1], xa[2] * xa[2]]);
    let r = r2.powf(0.5);
    if r <= 0.0 {
        return None;
    }
    // `normal` is unit-length in the file while x_axis / y_axis carry the
    // radius; normalising keeps `y_axis == normal x x_axis` a vector equality.
    let nlen = py_sum(&[na[0] * na[0], na[1] * na[1], na[2] * na[2]]).powf(0.5);
    if nlen <= 0.0 {
        return None;
    }
    let nh = [na[0] / nlen, na[1] / nlen, na[2] / nlen];
    let cross = [
        nh[1] * xa[2] - nh[2] * xa[1],
        nh[2] * xa[0] - nh[0] * xa[2],
        nh[0] * xa[1] - nh[1] * xa[0],
    ];
    // Check 2 (relative: r is in model units).
    if dist(&cross, &ya) > 1e-6 * r {
        return None;
    }

    let inv_r = 1.0 / r;
    let xh = [xa[0] * inv_r, xa[1] * inv_r, xa[2] * inv_r];
    let yh = [ya[0] * inv_r, ya[1] * inv_r, ya[2] * inv_r];
    let theta = |p: &Vec3| {
        let (dx, dy, dz) = (p[0] - c[0], p[1] - c[1], p[2] - c[2]);
        (dx * yh[0] + dy * yh[1] + dz * yh[2]).atan2(dx * xh[0] + dy * xh[1] + dz * xh[2])
    };

    // The caller rounded pts to 6 decimals in metres, so the tolerance clears
    // that rounding first.
    let tol = 1e-5f64.max(1e-6 * r);
    if !pts.iter().all(|p| (dist(p, &c) - r).abs() <= tol) {
        return None;
    }

    let thetas: Vec<f64> = pts.iter().map(theta).collect();
    let mut pairs: Vec<(f64, f64)> = thetas.windows(2).map(|w| (w[0], w[1])).collect();
    // A closed run does not repeat its first vertex, so the closing step is
    // supplied from the flag.
    if closed && thetas.len() > 2 {
        pairs.push((thetas[thetas.len() - 1], thetas[0]));
    }
    // Unwrap into a monotone walk: each step is the shortest turn. The
    // quarter-turn bound carries a relative tolerance, because a 4-sided
    // circle sits exactly on it.
    let pi = std::f64::consts::PI;
    let step_limit = pi * 0.5 * (1.0 + 1e-9);
    let mut steps: Vec<f64> = Vec::new();
    let mut total = 0.0;
    for (a, b) in pairs {
        let mut d = b - a;
        while d > pi {
            d -= 2.0 * pi;
        }
        while d < -pi {
            d += 2.0 * pi;
        }
        if d.abs() > step_limit {
            return None; // not a walk along the circle
        }
        if !steps.is_empty() && (d > 0.0) != (steps[0] > 0.0) {
            return None; // direction reverses: not one arc
        }
        steps.push(d);
        total += d;
    }
    if steps.is_empty() || total == 0.0 {
        return None;
    }

    let at = |t: f64| {
        let (ct, st) = (t.cos(), t.sin());
        [
            c[0] + ct * xa[0] + st * ya[0],
            c[1] + ct * xa[1] + st * ya[1],
            c[2] + ct * xa[2] + st * ya[2],
        ]
    };
    let t0 = thetas[0];
    if total.abs() >= 2.0 * pi - 1e-6 * 1.0f64.max(total.abs()) {
        // A full turn: no single three-point arc states it, two half-turns do.
        return Some(CurveArc {
            points: vec![
                at(t0),
                at(t0 + total * 0.25),
                at(t0 + total * 0.5),
                at(t0 + total * 0.75),
            ],
            segments: vec![[0, 1, 2], [2, 3, 0]],
        });
    }
    Some(CurveArc {
        points: vec![at(t0), at(t0 + total * 0.5), at(t0 + total)],
        segments: vec![[0, 1, 2]],
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn set_order_matches_cpython() {
        // `list(set(xs))` under CPython 3.13.
        let cases: [(&[i64], &[i64]); 3] = [
            (&[35223, 149214, 16544], &[16544, 149214, 35223]),
            (
                &[66865, 30912, 129876, 199482, 117832],
                &[30912, 117832, 66865, 129876, 199482],
            ),
            (
                &[123797, 170812, 99514, 55039, 24605, 127889, 7432, 102187],
                &[7432, 102187, 127889, 123797, 99514, 170812, 24605, 55039],
            ),
        ];
        for (input, expected) in cases {
            assert_eq!(py_set_order(input), expected);
        }
    }

    #[test]
    fn round6_matches_python() {
        assert_eq!(round6(0.0078125), 0.007812);
        assert_eq!(round6(0.0234375), 0.023438);
        assert_eq!(round6(-0.0078125), -0.007812);
        assert_eq!(round6(-4e-7).to_bits(), (-0.0f64).to_bits());
        assert_eq!(round6(1.23456789), 1.234568);
    }
}
