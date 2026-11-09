//! Local-space face grouping shared by the baked (`scene_build.rs`) and
//! instanced (`instanced_scene_build.rs`) scene builders (`face_groups.hpp` /
//! `face_groups.cpp`).
//!
//! A definition's faces are grouped by resolved (color, double_sided, texture)
//! identity in DEFINITION-LOCAL space (inches, SketchUp Z-up): exactly what
//! the baked builder assembles just before applying an instance's world
//! matrix, and exactly what the instanced builder keeps local and puts on the
//! node instead. One implementation keeps the two paths agreeing on
//! triangulation, UV seams, normals and front/back handling.
//!
//! An unpainted face falls back to the caller's `fallback_color` for color,
//! but its material (and so its texture tile size) comes from the face's OWN
//! `material_id` / `back_material_id` only. The color key carries a 4th
//! (alpha) component.

use std::collections::{BTreeMap, HashMap};

use crate::internal::{Error, GeometryBuilder, RawMaterial, RawParsed};
use crate::model::{ByteBuffer, CoEdge, EntityId, Vec3};
use crate::scene::SceneTexture;
use crate::triangulator::triangulate_with;

/// Color key: (r, g, b, alpha-from-transparency).
pub type FaceColorKey = [i32; 4];

/// A face group's identity. Ordered like the C++ `std::tie(color,
/// double_sided, texture_index)` (an absent texture sorts first).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Default)]
pub struct GroupKey {
    pub color: FaceColorKey,
    pub double_sided: bool,
    /// The texture is part of the identity, not just the color: two different
    /// images can average to the same RGB.
    pub texture_index: Option<usize>,
}

/// A vertex is keyed by (source vertex id, u, v): UVs are per-face, so a
/// position shared by two faces that disagree on texture mapping becomes two
/// output vertices. The doubles are stored as bits with `-0.0` folded into
/// `0.0`, matching the C++ `std::map`'s `<` equivalence.
pub type FaceVKey = (EntityId, u64, u64);

fn vkey(id: EntityId, u: f64, v: f64) -> FaceVKey {
    let bits = |x: f64| if x == 0.0 { 0u64 } else { x.to_bits() };
    (id, bits(u), bits(v))
}

/// One group's local-space mesh. `verts`, `uvs` and `normals` (accumulated,
/// unnormalised) are aligned by output vertex index.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Group {
    pub verts: Vec<Vec3>,
    pub uvs: Vec<[f32; 2]>,
    pub tris: Vec<[usize; 3]>,
    pub map: HashMap<FaceVKey, usize>,
    pub normals: Vec<Vec3>,
}

/// The material a face (or instance) material id names: by name first, then
/// by material folder.
pub fn find_material(parsed: &RawParsed, id: Option<EntityId>) -> Option<&RawMaterial> {
    let name = parsed.material_id_to_name.get(&id?)?;
    parsed
        .materials
        .get(name)
        .or_else(|| parsed.materials_by_folder.get(name))
        .map(|m| &**m)
}

/// A material's color key. Opacity combines the RGBA color record's alpha
/// byte and the XML material's transparency; the untouched one defaults to
/// fully opaque.
pub fn material_color(material: Option<&RawMaterial>) -> Option<FaceColorKey> {
    let material = material?;
    let combined =
        (f64::from(material.a.clamp(0, 255)) / 255.0) * material.transparency.clamp(0.0, 1.0);
    Some([
        material.r,
        material.g,
        material.b,
        (combined * 255.0).round() as i32,
    ])
}

/// The layer's color (or SketchUp's default grey), fully opaque.
pub fn default_color(parsed: &RawParsed, layer: &str) -> FaceColorKey {
    let color = parsed
        .layer_colors
        .get(layer)
        .copied()
        .unwrap_or([136, 136, 136]);
    [
        i32::from(color[0]),
        i32::from(color[1]),
        i32::from(color[2]),
        255,
    ]
}

/// An image's MIME type from its magic bytes; `None` for anything glTF cannot
/// carry (glTF only allows PNG and JPEG).
pub fn sniff_image_mime(data: &[u8]) -> Option<&'static str> {
    if data.len() >= 3 && data[0] == 0xff && data[1] == 0xd8 && data[2] == 0xff {
        return Some("image/jpeg");
    }
    if data.len() >= 8 && data[..8] == [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a] {
        return Some("image/png");
    }
    None
}

/// The scene's texture table, deduplicated by bytes (the builders'
/// `texture_index_for`): the same image routinely backs several materials.
#[derive(Debug, Clone, Default)]
pub struct TextureRegistry {
    pub textures: Vec<SceneTexture>,
    index_by_key: HashMap<String, usize>,
}

impl TextureRegistry {
    /// The texture index for a material's image, registering it on first use;
    /// `None` when the material has no image glTF can carry.
    pub fn index_for(&mut self, material: Option<&RawMaterial>) -> Option<usize> {
        let texture = material?.texture.as_ref()?;
        let data: &ByteBuffer = texture.data.as_ref()?;
        if data.is_empty() {
            return None;
        }
        let mime_type = sniff_image_mime(data)?;
        // Length plus a short byte prefix (C++ `std::hex`, unpadded) tells
        // real images apart without hashing megabytes on every face.
        let mut key = format!("{}:", data.len());
        for b in data.iter().take(16) {
            key.push_str(&format!("{b:x}"));
        }
        if let Some(&found) = self.index_by_key.get(&key) {
            return Some(found);
        }
        let idx = self.textures.len();
        self.textures.push(SceneTexture {
            data: data.clone(),
            mime_type: mime_type.to_owned(),
            filename: texture.filename.clone(),
        });
        self.index_by_key.insert(key, idx);
        Some(idx)
    }
}

/// What [`build_local_face_groups`] needs from its caller besides the builder.
pub struct FaceGroupContext<'a> {
    pub texture_index_for: &'a mut dyn FnMut(Option<&RawMaterial>) -> Option<usize>,
    /// Color an unpainted face falls back to (the instance's inherited paint
    /// color, or the effective layer's color when nothing is inherited).
    pub fallback_color: FaceColorKey,
}

fn tile_size(material: Option<&RawMaterial>) -> (f64, f64) {
    let (mut w, mut h) = (1.0, 1.0);
    if let Some(texture) = material.and_then(|m| m.texture.as_ref()) {
        if texture.x_scale > 1e-9 {
            w = texture.x_scale;
        }
        if texture.y_scale > 1e-9 {
            h = texture.y_scale;
        }
    }
    (w, h)
}

/// Inverse of a row-major 3x3 matrix (cofactor / adjugate); the identity when
/// singular.
fn invert_3x3(m: &[f64; 9]) -> [f64; 9] {
    let (a, b, c) = (m[0], m[1], m[2]);
    let (d, e, f) = (m[3], m[4], m[5]);
    let (g, h, i) = (m[6], m[7], m[8]);
    let det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g);
    if det.abs() < 1e-12 {
        return [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0];
    }
    let inv_det = 1.0 / det;
    [
        (e * i - f * h) * inv_det,
        (c * h - b * i) * inv_det,
        (b * f - c * e) * inv_det,
        (f * g - d * i) * inv_det,
        (a * i - c * g) * inv_det,
        (c * d - a * f) * inv_det,
        (d * h - e * g) * inv_det,
        (b * g - a * h) * inv_det,
        (a * e - b * d) * inv_det,
    ]
}

/// Tilt sine below which a face counts as horizontal and its UV basis snaps
/// to the world axes (SDK-measured).
const UV_VERTICAL_TOLERANCE: f64 = 1e-3;

/// Face-plane basis (xr, yr) for UV projection from a unit face normal:
/// U = normalize(Z x n), V = n x U; horizontal faces use (X, Y), downward
/// ones (-X, +Y).
fn face_uv_basis(n: &Vec3) -> (Vec3, Vec3) {
    let cx = -n[1];
    let cy = n[0];
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

/// UV of local-space point `p` (inches) on a face with plane basis
/// (`xr`, `yr`), the inverted per-face uv_transform (or `None` for the
/// default projection) and the material's tile size.
fn compute_face_uv(
    p: &Vec3,
    xr: &Vec3,
    yr: &Vec3,
    inv: Option<&[f64; 9]>,
    tile_w: f64,
    tile_h: f64,
) -> (f64, f64) {
    let px = p[0] * xr[0] + p[1] * xr[1] + p[2] * xr[2];
    let py = p[0] * yr[0] + p[1] * yr[1] + p[2] * yr[2];
    let Some(inv) = inv else {
        return (px / tile_w, py / tile_h);
    };
    let u = px * inv[0] + py * inv[3] + inv[6];
    let v = px * inv[1] + py * inv[4] + inv[7];
    let mut q = px * inv[2] + py * inv[5] + inv[8];
    if q.abs() < 1e-12 {
        q = 1.0;
    }
    ((u / q) / tile_w, (v / q) / tile_h)
}

fn loop_vertices(lp: &[CoEdge], b: &GeometryBuilder) -> Vec<EntityId> {
    let mut v: Vec<EntityId> = Vec::new();
    for c in lp {
        let Some(edge) = b.edges.get(&c.edge_id) else {
            continue;
        };
        let id = if c.orientation == 1 { edge.0 } else { edge.1 };
        if let Some(id) = id {
            if v.last() != Some(&id) {
                v.push(id);
            }
        }
    }
    if v.len() > 1 && v.first() == v.last() {
        v.pop();
    }
    v
}

/// Groups a definition's faces by resolved material identity, in local space.
///
/// A face whose front and back resolve to the SAME color is emitted once with
/// `double_sided` set; a face whose sides differ is emitted as two
/// single-sided triangle sets (one normal-wound front, one reverse-wound back)
/// so each side keeps its own color.
///
/// Fails (`OutOfRange`, the C++ `map::at`) when a triangle references a
/// vertex the builder does not have.
pub fn build_local_face_groups(
    parsed: &RawParsed,
    b: &GeometryBuilder,
    ctx: &mut FaceGroupContext<'_>,
) -> Result<BTreeMap<GroupKey, Group>, Error> {
    let mut groups: BTreeMap<GroupKey, Group> = BTreeMap::new();

    for f in b.faces.values() {
        let front_mat = find_material(parsed, f.material_id);
        let back_mat = find_material(parsed, f.back_material_id);
        let front = material_color(front_mat).unwrap_or(ctx.fallback_color);
        let back = material_color(back_mat).unwrap_or(ctx.fallback_color);

        let loops: Vec<Vec<EntityId>> = f
            .loops
            .iter()
            .map(|l| loop_vertices(l, b))
            .filter(|x| !x.is_empty())
            .collect();
        if loops.is_empty() {
            continue;
        }

        let triangles = triangulate_with(|id| b.vertices.get(&id).copied(), &loops, f.normal);

        let (xr, yr) = face_uv_basis(&f.normal);
        let mut add_side = |key: GroupKey,
                            reverse: bool,
                            uv_transform: &Option<[f64; 9]>,
                            tile_w: f64,
                            tile_h: f64|
         -> Result<(), Error> {
            let inv = uv_transform.as_ref().map(invert_3x3);
            let group = groups.entry(key).or_default();
            for &triangle in &triangles {
                let mut triangle = triangle;
                if reverse {
                    triangle.swap(1, 2);
                }
                let mut indices = [0usize; 3];
                for (slot, &id) in indices.iter_mut().zip(&triangle) {
                    let pos = *b.vertices.get(&id).ok_or_else(|| {
                        Error::out_of_range(format!("map::at: vertex {id} not found"))
                    })?;
                    let (u, v) = compute_face_uv(&pos, &xr, &yr, inv.as_ref(), tile_w, tile_h);
                    let index = *group.map.entry(vkey(id, u, v)).or_insert_with(|| {
                        group.verts.push(pos);
                        group.uvs.push([u as f32, v as f32]);
                        group.normals.push([0.0; 3]);
                        group.verts.len() - 1
                    });
                    *slot = index;
                    for (sum, &axis) in group.normals[index].iter_mut().zip(&f.normal) {
                        *sum += if reverse { -axis } else { axis };
                    }
                }
                group.tris.push(indices);
            }
            Ok(())
        };
        if front == back {
            let (tw, th) = tile_size(front_mat);
            let key = GroupKey {
                color: front,
                double_sided: true,
                texture_index: (ctx.texture_index_for)(front_mat),
            };
            add_side(key, false, &f.uv_transform, tw, th)?;
        } else {
            let (ftw, fth) = tile_size(front_mat);
            let key = GroupKey {
                color: front,
                double_sided: false,
                texture_index: (ctx.texture_index_for)(front_mat),
            };
            add_side(key, false, &f.uv_transform, ftw, fth)?;
            let (btw, bth) = tile_size(back_mat);
            let key = GroupKey {
                color: back,
                double_sided: false,
                texture_index: (ctx.texture_index_for)(back_mat),
            };
            add_side(key, true, &f.uv_transform_back, btw, bth)?;
        }
    }

    Ok(groups)
}
