//! STL export, ASCII and binary (`stl_export.hpp` / `stl_export.cpp`).

use std::fmt::Write as _;
use std::io;
use std::path::Path;

use crate::scene::{GlbPrimitive, Scene};

type V3 = [f32; 3];

fn calculate_normal(v0: V3, v1: V3, v2: V3) -> V3 {
    let e1 = [v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2]];
    let e2 = [v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2]];
    let n = [
        e1[1] * e2[2] - e1[2] * e2[1],
        e1[2] * e2[0] - e1[0] * e2[2],
        e1[0] * e2[1] - e1[1] * e2[0],
    ];
    let len = (n[0] * n[0] + n[1] * n[1] + n[2] * n[2]).sqrt();
    if len > 1e-12 {
        [n[0] / len, n[1] / len, n[2] / len]
    } else {
        [0.0; 3]
    }
}

/// Every triangle of every primitive as `(normal, [v0, v1, v2])`, scaled.
fn triangles(prim: &GlbPrimitive, scale: f32) -> impl Iterator<Item = (V3, [V3; 3])> + '_ {
    let vertex = move |i: u32| {
        let i = i as usize * 3;
        [
            prim.positions[i] * scale,
            prim.positions[i + 1] * scale,
            prim.positions[i + 2] * scale,
        ]
    };
    prim.indices.as_chunks::<3>().0.iter().map(move |t| {
        let v = [vertex(t[0]), vertex(t[1]), vertex(t[2])];
        (calculate_normal(v[0], v[1], v[2]), v)
    })
}

/// Serializes a baked scene as ASCII STL. `scale` multiplies every
/// coordinate (e.g. `1000.0` for millimetres).
pub fn to_stl_ascii(scene: &Scene, scale: f32) -> String {
    let mut s = String::from("solid OpenSKP_Model\n");
    for prim in &scene.glb_primitives {
        for (n, v) in triangles(prim, scale) {
            let _ = writeln!(s, "  facet normal {:.6} {:.6} {:.6}", n[0], n[1], n[2]);
            s.push_str("    outer loop\n");
            for p in v {
                let _ = writeln!(s, "      vertex {:.6} {:.6} {:.6}", p[0], p[1], p[2]);
            }
            s.push_str("    endloop\n");
            s.push_str("  endfacet\n");
        }
    }
    s.push_str("endsolid OpenSKP_Model\n");
    s
}

/// Serializes a baked scene as little-endian binary STL: an 80-byte header,
/// a `u32` triangle count, then 50 bytes per triangle.
pub fn to_stl_binary(scene: &Scene, scale: f32) -> Vec<u8> {
    let total: u32 = scene
        .glb_primitives
        .iter()
        .fold(0u32, |a, p| a.wrapping_add((p.indices.len() / 3) as u32));
    let mut data = Vec::with_capacity(84 + total as usize * 50);
    let header = b"# OpenSKP Binary STL Export";
    data.extend_from_slice(header);
    data.resize(80, 0);
    data.extend_from_slice(&total.to_le_bytes());
    for prim in &scene.glb_primitives {
        for (n, v) in triangles(prim, scale) {
            for f in n.iter().chain(v.iter().flatten()) {
                data.extend_from_slice(&f.to_le_bytes());
            }
            data.extend_from_slice(&0u16.to_le_bytes());
        }
    }
    data
}

/// Exports a baked scene to an STL file (binary or ASCII). Missing parent
/// directories are created.
pub fn export_stl(
    scene: &Scene,
    path: impl AsRef<Path>,
    binary: bool,
    scale: f32,
) -> io::Result<()> {
    let path = path.as_ref();
    if let Some(parent) = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty() && !p.exists())
    {
        std::fs::create_dir_all(parent)?;
    }
    let (bytes, kind) = if binary {
        (to_stl_binary(scene, scale), "binary")
    } else {
        (to_stl_ascii(scene, scale).into_bytes(), "ASCII")
    };
    std::fs::write(path, bytes).map_err(|e| {
        io::Error::new(
            e.kind(),
            format!(
                "Cannot open STL {kind} output file: {}: {e}",
                path.display()
            ),
        )
    })
}
