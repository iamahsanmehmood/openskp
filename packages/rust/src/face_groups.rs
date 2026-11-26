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
//! An unpainted face side inherits `fallback_material` (texture image, tile
//! size and opacity included) from an enclosing painted component or group;
//! when nothing up the instance path is painted, its color falls back to
//! `fallback_color` / `fallback_back_color`. The color key carries a 4th
//! (alpha) component.

use std::collections::{BTreeMap, HashMap};

use crate::errors::SkpParseError;
use crate::internal::{Error, GeometryBuilder, RawMaterial, RawParsed};
use crate::model::{ByteBuffer, CoEdge, EntityId, Vec3};
use crate::observability::ParseStage;
use crate::scene::SceneTexture;
use crate::triangulator::triangulate_with;

/// Color key: (r, g, b, alpha-from-transparency).
pub type FaceColorKey = [i32; 4];

/// A face group's identity. Ordered like the C++ `std::tie(color,
/// double_sided, texture_index, unpainted_back)` (an absent texture sorts first).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Default)]
pub struct GroupKey {
    pub color: FaceColorKey,
    pub double_sided: bool,
    /// The texture is part of the identity, not just the color: two different
    /// images can average to the same RGB.
    pub texture_index: Option<usize>,
    /// True for the reverse-wound back primitive of a face with no paint on
    /// either side when front and back fallback colours differ.
    pub unpainted_back: bool,
    pub colorized: bool,
    pub colorize_type: i32,
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
    pub back_triangles: Vec<u32>,
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

/// Front and back face colours from the model's rendering options (`"style"`),
/// or `None` when `"tag"` is requested or the file carries no face colours.
pub fn unpainted_style_colors(
    parsed: &RawParsed,
    unpainted_faces: &str,
) -> Result<Option<(FaceColorKey, FaceColorKey)>, SkpParseError> {
    match unpainted_faces {
        "tag" => Ok(None),
        "style" => {
            let Some((f, b)) = parsed.face_colors else {
                return Ok(None);
            };
            Ok(Some((
                [i32::from(f[0]), i32::from(f[1]), i32::from(f[2]), 255],
                [i32::from(b[0]), i32::from(b[1]), i32::from(b[2]), 255],
            )))
        }
        other => Err(SkpParseError::at_stage(
            format!("unpainted_faces must be 'style' or 'tag', got {other:?}"),
            ParseStage::BuildScene,
        )),
    }
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

pub(crate) fn describe_image_format(data: &[u8], filename: &str) -> String {
    if data.starts_with(b"II*\x00") || data.starts_with(b"MM\x00*") {
        return "TIFF".to_owned();
    }
    if data.starts_with(b"BM") {
        return "BMP".to_owned();
    }
    if data.starts_with(b"GIF87a") || data.starts_with(b"GIF89a") {
        return "GIF".to_owned();
    }
    if data.len() >= 12 && &data[..4] == b"RIFF" && &data[8..12] == b"WEBP" {
        return "WebP".to_owned();
    }
    if data.starts_with(b"8BPS") {
        return "PSD".to_owned();
    }
    if data.starts_with(b"DDS ") {
        return "DDS".to_owned();
    }
    if data.starts_with(b"\x00\x00\x00\x0cjP  ") {
        return "JPEG 2000".to_owned();
    }
    let tail = &data[data.len().saturating_sub(26)..];
    if tail.windows(16).any(|w| w == b"TRUEVISION-XFILE") {
        return "TGA".to_owned();
    }
    let base = filename.rsplit(['/', '\\']).next().unwrap_or("");
    if let Some((_, ext)) = base.rsplit_once('.') {
        if !ext.is_empty() && ext.len() <= 5 {
            return ext.to_ascii_uppercase();
        }
    }
    "an unrecognized format".to_owned()
}

/// Real MIME type of a known non-PNG/JPEG texture format from its byte
/// signature (or TGA footer), or `None` when unrecognized.
pub(crate) fn original_image_mime(data: &[u8]) -> Option<&'static str> {
    match describe_image_format(data, "").as_str() {
        "TIFF" => Some("image/tiff"),
        "GIF" => Some("image/gif"),
        "BMP" => Some("image/bmp"),
        "TGA" => Some("image/x-tga"),
        "WebP" => Some("image/webp"),
        "PSD" => Some("image/vnd.adobe.photoshop"),
        "DDS" => Some("image/vnd.ms-dds"),
        "JPEG 2000" => Some("image/jp2"),
        _ => None,
    }
}

/// The scene's texture table, deduplicated by full byte content (the builders'
/// `texture_index_for`): the same image routinely backs several materials,
/// while equal-size PNGs/TIFFs share their 16-byte header and must stay
/// distinct. Checks `(ptr, len)` identity first so repeated face lookups stay
/// O(1).
#[derive(Debug, Clone, Default)]
pub struct TextureRegistry {
    pub textures: Vec<SceneTexture>,
    index_by_ptr: HashMap<(usize, usize), usize>,
    buckets: HashMap<u64, Vec<usize>>,
    options: crate::observability::ParseOptions,
    warned: HashMap<(String, String), ()>,
}

impl TextureRegistry {
    /// Creates a registry configured with the scene build's [`ParseOptions`].
    pub fn from_options(options: &crate::observability::ParseOptions) -> Self {
        Self {
            options: options.clone(),
            ..Self::default()
        }
    }

    fn warn_skipped(&mut self, material: &RawMaterial, data: &[u8]) {
        let Some(texture) = material.texture.as_ref() else {
            return;
        };
        let fmt = describe_image_format(data, &texture.filename);
        let warn_key = (material.name.clone(), fmt.clone());
        if self.warned.insert(warn_key, ()).is_some() {
            return;
        }
        let base = texture.filename.rsplit(['/', '\\']).next().unwrap_or("");
        let keepable = original_image_mime(data).is_some();
        let label = if material.name.is_empty() {
            "(unnamed)"
        } else {
            &material.name
        };
        let suffix = if base.is_empty() {
            String::new()
        } else {
            format!(" ({base})")
        };
        let hint = if keepable {
            " (pass original_textures: true to keep it in its own format)"
        } else {
            ""
        };
        crate::observability::emit_log(
            &self.options,
            crate::observability::LogLevel::Warning,
            &format!(
                "Texture of material '{label}'{suffix} is {fmt}, which a scene cannot carry \
                 (only PNG and JPEG): the material is built without its texture{hint}"
            ),
        );
    }

    /// The texture index for a material's image, registering it on first use;
    /// `None` when the material has no image the scene can carry.
    pub fn index_for(&mut self, material: Option<&RawMaterial>) -> Option<usize> {
        let material = material?;
        let texture = material.texture.as_ref()?;
        let data: &ByteBuffer = texture.data.as_ref()?;
        if data.is_empty() {
            return None;
        }
        let ptr_key = (data.as_ptr() as usize, data.len());
        if let Some(&found) = self.index_by_ptr.get(&ptr_key) {
            return Some(found);
        }
        let mut mime_type = sniff_image_mime(data);
        if mime_type.is_none() && self.options.original_textures {
            mime_type = original_image_mime(data);
        }
        let Some(mime_type) = mime_type else {
            self.warn_skipped(material, data);
            return None;
        };
        let mut hash = 14_695_981_039_346_656_037u64;
        for &b in data {
            hash ^= u64::from(b);
            hash = hash.wrapping_mul(1_099_511_628_211);
        }
        if let Some(bucket) = self.buckets.get(&hash) {
            for &cand in bucket {
                if self.textures[cand].data == *data {
                    self.index_by_ptr.insert(ptr_key, cand);
                    return Some(cand);
                }
            }
        }
        let idx = self.textures.len();
        self.textures.push(SceneTexture {
            data: data.clone(),
            mime_type: mime_type.to_owned(),
            filename: texture.filename.clone(),
        });
        self.buckets.entry(hash).or_default().push(idx);
        self.index_by_ptr.insert(ptr_key, idx);
        Some(idx)
    }
}

/// What [`build_local_face_groups`] needs from its caller besides the builder.
pub struct FaceGroupContext<'a> {
    pub texture_index_for: &'a mut dyn FnMut(Option<&RawMaterial>) -> Option<usize>,
    /// Color an unpainted face front falls back to (the instance's inherited
    /// paint color, the model's front face color, or the effective layer's color).
    pub fallback_color: FaceColorKey,
    /// Color an unpainted face back falls back to (`fallback_color` when `None`).
    pub fallback_back_color: Option<FaceColorKey>,
    /// Material painted on the enclosing group/component, inherited whole
    /// (texture, tile size, transparency) by any unpainted face side.
    pub fallback_material: Option<&'a RawMaterial>,
    /// When `true`, an unpainted face side resolves its own tag's colour before
    /// falling back to `fallback_color` / `fallback_back_color` (#697).
    pub use_face_tag_colors: bool,
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
    let fallback_back = ctx.fallback_back_color.unwrap_or(ctx.fallback_color);

    for f in b.faces.values() {
        let front_mat = find_material(parsed, f.material_id).or(ctx.fallback_material);
        let back_mat = find_material(parsed, f.back_material_id).or(ctx.fallback_material);
        let face_fallback =
            if ctx.use_face_tag_colors && (front_mat.is_none() || back_mat.is_none()) {
                f.layer_id
                    .filter(|&id| id != 0)
                    .and_then(|id| parsed.layer_id_to_name.get(&id))
                    .filter(|name| !name.is_empty() && name.as_str() != "Layer0")
                    .map(|name| default_color(parsed, name))
            } else {
                None
            };
        let front = material_color(front_mat)
            .or(face_fallback)
            .unwrap_or(ctx.fallback_color);
        let back = material_color(back_mat)
            .or(face_fallback)
            .unwrap_or(fallback_back);

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
                if reverse {
                    let tri_idx = u32::try_from(group.tris.len()).map_err(|_| {
                        Error::out_of_range("face group exceeds u32 triangle index limit")
                    })?;
                    group.back_triangles.push(tri_idx);
                }
                group.tris.push(indices);
            }
            Ok(())
        };
        let front_raw_col = front_mat.is_some_and(|m| m.colorized);
        let back_raw_col = back_mat.is_some_and(|m| m.colorized);
        let front_raw_ctype = if front_raw_col {
            front_mat.map_or(0, |m| m.colorize_type)
        } else {
            0
        };
        let back_raw_ctype = if back_raw_col {
            back_mat.map_or(0, |m| m.colorize_type)
        } else {
            0
        };
        let same_colorization = if front_mat.is_some() && back_mat.is_some() {
            front_raw_col == back_raw_col && front_raw_ctype == back_raw_ctype
        } else {
            true
        };
        if front == back && same_colorization {
            let (tw, th) = tile_size(front_mat);
            let tex = (ctx.texture_index_for)(front_mat);
            let col = tex.is_some() && front_raw_col;
            let key = GroupKey {
                color: front,
                double_sided: true,
                texture_index: tex,
                unpainted_back: false,
                colorized: col,
                colorize_type: if col { front_raw_ctype } else { 0 },
            };
            add_side(key, false, &f.uv_transform, tw, th)?;
        } else {
            let (ftw, fth) = tile_size(front_mat);
            let ftex = (ctx.texture_index_for)(front_mat);
            let fcol = ftex.is_some() && front_raw_col;
            let key = GroupKey {
                color: front,
                double_sided: false,
                texture_index: ftex,
                unpainted_back: false,
                colorized: fcol,
                colorize_type: if fcol { front_raw_ctype } else { 0 },
            };
            add_side(key, false, &f.uv_transform, ftw, fth)?;
            let (btw, bth) = tile_size(back_mat);
            let btex = (ctx.texture_index_for)(back_mat);
            let bcol = btex.is_some() && back_raw_col;
            let key = GroupKey {
                color: back,
                double_sided: false,
                texture_index: btex,
                unpainted_back: front_mat.is_none() && back_mat.is_none(),
                colorized: bcol,
                colorize_type: if bcol { back_raw_ctype } else { 0 },
            };
            add_side(key, true, &f.uv_transform_back, btw, bth)?;
        }
    }

    Ok(groups)
}

#[cfg(test)]
mod tests {
    use super::original_image_mime;

    #[test]
    fn sniffs_known_signatures_and_rejects_unknown_bytes() {
        let mut tga = vec![0u8; 60];
        tga[2] = 2;
        tga[42..60].copy_from_slice(b"TRUEVISION-XFILE.\x00");
        assert_eq!(original_image_mime(b"II*\x00"), Some("image/tiff"));
        assert_eq!(original_image_mime(b"MM\x00*"), Some("image/tiff"));
        assert_eq!(original_image_mime(b"GIF89a"), Some("image/gif"));
        assert_eq!(original_image_mime(b"BM\x00\x00"), Some("image/bmp"));
        assert_eq!(
            original_image_mime(b"RIFF\x00\x00\x00\x00WEBP"),
            Some("image/webp")
        );
        assert_eq!(
            original_image_mime(b"8BPS\x00\x00\x00\x00"),
            Some("image/vnd.adobe.photoshop")
        );
        assert_eq!(
            original_image_mime(b"DDS \x00\x00\x00\x00"),
            Some("image/vnd.ms-dds")
        );
        assert_eq!(
            original_image_mime(b"\x00\x00\x00\x0cjP  "),
            Some("image/jp2")
        );
        assert_eq!(original_image_mime(&tga), Some("image/x-tga"));
        assert_eq!(original_image_mime(b"zzzzzzzzzzzzzzzz"), None);
        assert_eq!(original_image_mime(b""), None);
    }
}
