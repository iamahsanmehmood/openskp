//! Wavefront OBJ/MTL export (`obj_export.hpp` / `obj_export.cpp`).

use std::collections::HashSet;
use std::fmt::Write as _;
use std::io;
use std::path::Path;

use crate::scene::Scene;

fn sanitize_material_name(name: &str) -> String {
    let clean: String = name
        .trim_matches([' ', '\t', '\n', '\r'])
        .bytes()
        .map(|b| {
            if b.is_ascii_alphanumeric() || b == b'.' || b == b'-' {
                b as char
            } else {
                '_'
            }
        })
        .collect();
    if clean.is_empty() {
        "default_material".into()
    } else {
        clean
    }
}

/// Unique, sanitized MTL names, one per material. Collisions gain
/// `_<1-based material index>` and, if needed, a further `_<n>`.
fn unique_material_names(scene: &Scene) -> Vec<String> {
    let bases: Vec<String> = scene
        .gltf_materials
        .iter()
        .enumerate()
        .map(|(i, m)| {
            let raw = if m.name.is_empty() {
                format!("Material_{i}")
            } else {
                m.name.clone()
            };
            sanitize_material_name(&raw)
        })
        .collect();
    let reserved: HashSet<&str> = bases.iter().map(String::as_str).collect();
    let mut used: HashSet<String> = HashSet::new();
    let mut names = Vec::with_capacity(bases.len());
    for (i, base) in bases.iter().enumerate() {
        let mut name = base.clone();
        if used.contains(&name) {
            let stem = format!("{name}_{}", i + 1);
            name = stem.clone();
            let mut suffix = 1usize;
            while used.contains(&name) || reserved.contains(name.as_str()) {
                name = format!("{stem}_{suffix}");
                suffix += 1;
            }
        }
        used.insert(name.clone());
        names.push(name);
    }
    names
}

/// Serializes a baked scene's materials as Wavefront MTL text. Names are
/// case-sensitive; collisions gain `_<1-based material index>` and, if
/// needed, an additional `_<n>` suffix.
pub fn to_mtl(scene: &Scene) -> String {
    let mut s = String::new();
    s.push_str("# OpenSKP MTL Material Library Export\n");
    let _ = write!(s, "# Materials: {}\n\n", scene.gltf_materials.len());
    let names = unique_material_names(scene);
    for (mat, name) in scene.gltf_materials.iter().zip(&names) {
        // The C++ narrows each channel to float before printing.
        let c = mat
            .pbr_metallic_roughness
            .base_color_factor
            .map(|v| v as f32 as f64);
        let _ = writeln!(s, "newmtl {name}");
        s.push_str("Ka 1.000000 1.000000 1.000000\n");
        let _ = writeln!(s, "Kd {:.6} {:.6} {:.6}", c[0], c[1], c[2]);
        s.push_str("Ks 0.200000 0.200000 0.200000\n");
        s.push_str("Ns 32.000000\n");
        let _ = writeln!(s, "d {:.6}", c[3]);
        s.push_str("illum 2\n");
        if !mat.texture_path.is_empty() {
            let tex = Path::new(&mat.texture_path)
                .file_name()
                .map(|f| f.to_string_lossy().into_owned());
            let _ = writeln!(s, "map_Kd {}", tex.unwrap_or_default());
        }
        s.push('\n');
    }
    s
}

/// Serializes a baked scene as Wavefront OBJ text. Material references use
/// the same unique names as [`to_mtl`]; `mtl_filename` (when non-empty) is
/// referenced with `mtllib`.
pub fn to_obj(scene: &Scene, mtl_filename: &str) -> String {
    let mut s = String::new();
    s.push_str("# OpenSKP OBJ Export\n");
    let _ = writeln!(s, "# Primitives: {}", scene.glb_primitives.len());
    if !mtl_filename.is_empty() {
        let _ = writeln!(s, "mtllib {mtl_filename}");
    }
    s.push('\n');

    let names = unique_material_names(scene);
    let mut vert_offset: u32 = 1;
    let mut uv_offset: u32 = 1;
    let mut norm_offset: u32 = 1;

    for prim in &scene.glb_primitives {
        let _ = writeln!(s, "o {}", prim.geom_name);
        let vert_count = prim.positions.len() / 3;
        for p in prim.positions.as_chunks::<3>().0 {
            let _ = writeln!(s, "v {:.6} {:.6} {:.6}", p[0], p[1], p[2]);
        }
        let uv_count = prim.uvs.len() / 2;
        for t in prim.uvs.as_chunks::<2>().0 {
            let _ = writeln!(s, "vt {:.6} {:.6}", t[0], t[1]);
        }
        let norm_count = prim.normals.len() / 3;
        for n in prim.normals.as_chunks::<3>().0 {
            let _ = writeln!(s, "vn {:.6} {:.6} {:.6}", n[0], n[1], n[2]);
        }
        if let Some(name) = names.get(prim.material_index) {
            let _ = writeln!(s, "usemtl {name}");
        }

        let has_uvs = uv_count == vert_count;
        let has_normals = norm_count == vert_count;
        for tri in prim.indices.as_chunks::<3>().0 {
            let tri = [tri[0], tri[1], tri[2]];
            let v = tri.map(|i| i.wrapping_add(vert_offset));
            let vt = tri.map(|i| i.wrapping_add(uv_offset));
            let vn = tri.map(|i| i.wrapping_add(norm_offset));
            let _ = match (has_uvs, has_normals) {
                (true, true) => writeln!(
                    s,
                    "f {}/{}/{} {}/{}/{} {}/{}/{}",
                    v[0], vt[0], vn[0], v[1], vt[1], vn[1], v[2], vt[2], vn[2]
                ),
                (true, false) => writeln!(
                    s,
                    "f {}/{} {}/{} {}/{}",
                    v[0], vt[0], v[1], vt[1], v[2], vt[2]
                ),
                (false, true) => writeln!(
                    s,
                    "f {}//{} {}//{} {}//{}",
                    v[0], vn[0], v[1], vn[1], v[2], vn[2]
                ),
                (false, false) => writeln!(s, "f {} {} {}", v[0], v[1], v[2]),
            };
        }

        vert_offset = vert_offset.wrapping_add(vert_count as u32);
        if has_uvs {
            uv_offset = uv_offset.wrapping_add(uv_count as u32);
        }
        if has_normals {
            norm_offset = norm_offset.wrapping_add(norm_count as u32);
        }
        s.push('\n');
    }
    s
}

/// Exports a baked scene to a Wavefront OBJ file at `path` and, when
/// `export_mtl` is set, a companion `<stem>.mtl` beside it. Missing parent
/// directories are created.
pub fn export_obj(scene: &Scene, path: impl AsRef<Path>, export_mtl: bool) -> io::Result<()> {
    let path = path.as_ref();
    let parent = path.parent().unwrap_or(Path::new(""));
    if !parent.as_os_str().is_empty() && !parent.exists() {
        std::fs::create_dir_all(parent)?;
    }
    let mtl_name = if export_mtl {
        format!(
            "{}.mtl",
            path.file_stem()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_default()
        )
    } else {
        String::new()
    };
    std::fs::write(path, to_obj(scene, &mtl_name)).map_err(|e| {
        io::Error::new(
            e.kind(),
            format!("Cannot open OBJ output file: {}: {e}", path.display()),
        )
    })?;
    if export_mtl {
        let mtl_path = parent.join(&mtl_name);
        std::fs::write(&mtl_path, to_mtl(scene)).map_err(|e| {
            io::Error::new(
                e.kind(),
                format!("Cannot open MTL output file: {}: {e}", mtl_path.display()),
            )
        })?;
    }
    Ok(())
}
