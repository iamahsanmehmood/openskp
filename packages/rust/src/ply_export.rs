//! PLY export, ASCII and binary (`ply_export.hpp` / `ply_export.cpp`).

use std::fmt::Write as _;
use std::io;
use std::path::Path;

use crate::scene::{GlbPrimitive, Scene};

/// The material's base color as RGBA bytes; light grey when the index is
/// out of range.
fn material_rgba(scene: &Scene, material_index: usize) -> [u8; 4] {
    match scene.gltf_materials.get(material_index) {
        Some(mat) => mat.pbr_metallic_roughness.base_color_factor.map(|c| {
            // std::max(0, std::min(255, x)), NaN handling included.
            let x = ((c as f32) * 255.0).round();
            let m = if x < 255.0 { x } else { 255.0 };
            (if 0.0 < m { m } else { 0.0 }) as u8
        }),
        None => [200, 200, 200, 255],
    }
}

fn header(format: &str, scene: &Scene) -> String {
    let vertices: usize = scene
        .glb_primitives
        .iter()
        .map(|p| p.positions.len() / 3)
        .sum();
    let faces: usize = scene
        .glb_primitives
        .iter()
        .map(|p| p.indices.len() / 3)
        .sum();
    let mut s = String::new();
    s.push_str("ply\n");
    let _ = writeln!(s, "format {format} 1.0");
    s.push_str("comment Created by OpenSKP\n");
    let _ = writeln!(s, "element vertex {vertices}");
    for prop in ["x", "y", "z", "nx", "ny", "nz", "u", "v"] {
        let _ = writeln!(s, "property float {prop}");
    }
    for prop in ["red", "green", "blue", "alpha"] {
        let _ = writeln!(s, "property uchar {prop}");
    }
    let _ = writeln!(s, "element face {faces}");
    s.push_str("property list uchar int vertex_indices\n");
    s.push_str("end_header\n");
    s
}

/// One vertex's eight float attributes; missing normals/UVs read as 0.
fn vertex_attrs(prim: &GlbPrimitive, i: usize) -> [f32; 8] {
    let n = |k: usize| prim.normals.get(k).copied().unwrap_or(0.0);
    let t = |k: usize| prim.uvs.get(k).copied().unwrap_or(0.0);
    [
        prim.positions[i * 3],
        prim.positions[i * 3 + 1],
        prim.positions[i * 3 + 2],
        n(i * 3),
        n(i * 3 + 1),
        n(i * 3 + 2),
        t(i * 2),
        t(i * 2 + 1),
    ]
}

/// Every face as global vertex indices, primitives concatenated.
fn faces(scene: &Scene) -> impl Iterator<Item = [u32; 3]> + '_ {
    let mut offset = 0usize;
    scene.glb_primitives.iter().flat_map(move |p| {
        let base = offset as u32;
        offset += p.positions.len() / 3;
        p.indices
            .as_chunks::<3>()
            .0
            .iter()
            .map(move |t| [t[0], t[1], t[2]].map(|i| i.wrapping_add(base)))
    })
}

/// Serializes a baked scene as ASCII PLY (per-vertex position, normal, UV
/// and material color; triangle faces).
pub fn to_ply_ascii(scene: &Scene) -> String {
    let mut s = header("ascii", scene);
    for prim in &scene.glb_primitives {
        let c = material_rgba(scene, prim.material_index);
        for i in 0..prim.positions.len() / 3 {
            let a = vertex_attrs(prim, i);
            let _ = writeln!(
                s,
                "{:.6} {:.6} {:.6} {:.6} {:.6} {:.6} {:.6} {:.6} {} {} {} {}",
                a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], c[0], c[1], c[2], c[3]
            );
        }
    }
    for f in faces(scene) {
        let _ = writeln!(s, "3 {} {} {}", f[0], f[1], f[2]);
    }
    s
}

/// Serializes a baked scene as little-endian binary PLY.
pub fn to_ply_binary(scene: &Scene) -> Vec<u8> {
    let mut data = header("binary_little_endian", scene).into_bytes();
    for prim in &scene.glb_primitives {
        let c = material_rgba(scene, prim.material_index);
        for i in 0..prim.positions.len() / 3 {
            for v in vertex_attrs(prim, i) {
                data.extend_from_slice(&v.to_le_bytes());
            }
            data.extend_from_slice(&c);
        }
    }
    for f in faces(scene) {
        data.push(3);
        for i in f {
            data.extend_from_slice(&(i as i32).to_le_bytes());
        }
    }
    data
}

/// Exports a baked scene to a PLY file (binary or ASCII). Missing parent
/// directories are created.
pub fn export_ply(scene: &Scene, path: impl AsRef<Path>, binary: bool) -> io::Result<()> {
    let path = path.as_ref();
    if let Some(parent) = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty() && !p.exists())
    {
        std::fs::create_dir_all(parent)?;
    }
    let (bytes, kind) = if binary {
        (to_ply_binary(scene), "binary")
    } else {
        (to_ply_ascii(scene).into_bytes(), "ASCII")
    };
    std::fs::write(path, bytes).map_err(|e| {
        io::Error::new(
            e.kind(),
            format!(
                "Cannot open PLY {kind} output file: {}: {e}",
                path.display()
            ),
        )
    })
}
