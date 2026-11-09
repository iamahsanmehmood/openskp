//! Baked-scene GLB export (`glb.hpp` / `glb.cpp`).
//!
//! The C++ port serializes through TinyGLTF; this is a hand-written glTF
//! JSON + GLB container writer producing the same document (same
//! accessors, buffer views, materials and key layout) - see
//! `json_writer::Flavor::Gltf` for the JSON formatting rules it mirrors.

use std::fmt;
use std::io;
use std::path::Path;

use crate::json_export::JsonValue;
use crate::json_writer::{self, Flavor};
use crate::scene::{GltfMaterial, Scene, SceneTexture};

/// Options for [`to_glb`] / [`export_glb`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct GlbOptions {
    /// Embed the scene's texture images in the GLB and point each textured
    /// material's `baseColorTexture` at them. Off by default: photographic
    /// textures can multiply the file size, and the geometry alone is what
    /// most callers are after.
    pub textures: bool,
}

/// Why a GLB could not be produced (C++ `std::invalid_argument`,
/// `std::length_error` and `std::runtime_error`).
#[derive(Debug)]
pub enum GlbError {
    /// The scene's data is malformed (non-finite values, bad indices, ...).
    InvalidArgument(String),
    /// The scene exceeds one of GLB's 32-bit limits.
    Length(String),
    /// Writing the output file failed.
    Io(io::Error),
}

impl fmt::Display for GlbError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            GlbError::InvalidArgument(m) | GlbError::Length(m) => f.write_str(m),
            GlbError::Io(e) => write!(f, "{e}"),
        }
    }
}

impl std::error::Error for GlbError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            GlbError::Io(e) => Some(e),
            _ => None,
        }
    }
}

impl From<io::Error> for GlbError {
    fn from(e: io::Error) -> Self {
        GlbError::Io(e)
    }
}

/// Result alias for the GLB exporters.
pub type GlbResult<T> = Result<T, GlbError>;

pub(crate) const GLB_LIMIT: usize = u32::MAX as usize;
const MAX_MODEL_ENTRIES: usize = i32::MAX as usize;

const TARGET_ARRAY_BUFFER: i64 = 34962;
const TARGET_ELEMENT_ARRAY_BUFFER: i64 = 34963;
const COMPONENT_FLOAT: i64 = 5126;
const COMPONENT_UNSIGNED_INT: i64 = 5125;
const MODE_TRIANGLES: i64 = 4;
const WRAP_REPEAT: i64 = 10497;

fn invalid(msg: impl Into<String>) -> GlbError {
    GlbError::InvalidArgument(msg.into())
}

fn too_large() -> GlbError {
    GlbError::Length("scene geometry exceeds GLB's 32-bit binary-buffer limit".into())
}

pub(crate) fn check_finite(value: f64, field: &str) -> GlbResult<()> {
    if value.is_finite() {
        Ok(())
    } else {
        Err(invalid(format!("{field} must be finite")))
    }
}

fn check_unit_factor(value: f64, field: &str) -> GlbResult<()> {
    check_finite(value, field)?;
    if !(0.0..=1.0).contains(&value) {
        return Err(invalid(format!("{field} must be between 0 and 1")));
    }
    Ok(())
}

fn check_binary_growth(current: usize, count: usize, element_size: usize) -> GlbResult<()> {
    if count > GLB_LIMIT / element_size || current > GLB_LIMIT - count * element_size {
        return Err(too_large());
    }
    Ok(())
}

fn align4(n: usize) -> usize {
    (n + 3) & !3
}

/// Shared per-primitive geometry checks (both exporters).
pub(crate) fn validate_primitive(
    prefix: &str,
    positions: &[f32],
    normals: &[f32],
    uvs: &[f32],
    indices: &[u32],
    material_index: usize,
    material_count: usize,
) -> GlbResult<()> {
    if positions.is_empty() {
        return Err(invalid(format!("{prefix}positions must not be empty")));
    }
    if !positions.len().is_multiple_of(3) {
        return Err(invalid(format!(
            "{prefix}positions must contain complete vec3 values"
        )));
    }
    if normals.len() != positions.len() {
        return Err(invalid(format!("{prefix}normals must match positions")));
    }
    if uvs.len() != positions.len() / 3 * 2 {
        return Err(invalid(format!("{prefix}uvs must match positions")));
    }
    if !indices.len().is_multiple_of(3) {
        return Err(invalid(format!(
            "{prefix}indices must contain complete triangles"
        )));
    }
    if indices.is_empty() {
        return Err(invalid(format!("{prefix}indices must not be empty")));
    }
    if material_index >= material_count {
        return Err(invalid(format!("{prefix}references an invalid material")));
    }
    let vertex_count = positions.len() / 3;
    for &v in positions {
        check_finite(v.into(), &format!("{prefix}position"))?;
    }
    for &v in normals {
        check_finite(v.into(), &format!("{prefix}normal"))?;
    }
    for &v in uvs {
        check_finite(v.into(), &format!("{prefix}uv"))?;
    }
    if indices.iter().any(|&i| i as usize >= vertex_count) {
        return Err(invalid(format!("{prefix}index is out of range")));
    }
    Ok(())
}

fn validate_scene(scene: &Scene) -> GlbResult<()> {
    if scene.gltf_materials.len() > MAX_MODEL_ENTRIES
        || scene.glb_primitives.len() > MAX_MODEL_ENTRIES / 3
    {
        return Err(GlbError::Length(
            "scene has too many GLB model entries".into(),
        ));
    }
    for (mi, mat) in scene.gltf_materials.iter().enumerate() {
        let pbr = &mat.pbr_metallic_roughness;
        for (ch, &v) in pbr.base_color_factor.iter().enumerate() {
            check_unit_factor(v, &format!("material {mi} base_color_factor[{ch}]"))?;
        }
        check_unit_factor(
            pbr.metallic_factor,
            &format!("material {mi} metallic_factor"),
        )?;
        check_unit_factor(
            pbr.roughness_factor,
            &format!("material {mi} roughness_factor"),
        )?;
    }

    let mut estimated = 0usize;
    for (pi, p) in scene.glb_primitives.iter().enumerate() {
        let prefix = format!("primitive {pi} ");
        validate_primitive(
            &prefix,
            &p.positions,
            &p.normals,
            &p.uvs,
            &p.indices,
            p.material_index,
            scene.gltf_materials.len(),
        )?;
        for len in [
            p.positions.len(),
            p.normals.len(),
            p.uvs.len(),
            p.indices.len(),
        ] {
            estimated = align4(estimated);
            check_binary_growth(estimated, len, 4)?;
            estimated += len * 4;
        }
    }
    Ok(())
}

/// The glTF document under construction plus its single binary buffer -
/// the parts of TinyGLTF's `Model` these exporters use.
pub(crate) struct GltfBuilder {
    pub binary: Vec<u8>,
    pub buffer_views: Vec<JsonValue>,
    pub accessors: Vec<JsonValue>,
}

impl GltfBuilder {
    pub fn new() -> Self {
        Self {
            binary: Vec::new(),
            buffer_views: Vec::new(),
            accessors: Vec::new(),
        }
    }

    fn align(&mut self) -> GlbResult<()> {
        while !self.binary.len().is_multiple_of(4) {
            if self.binary.len() == GLB_LIMIT {
                return Err(too_large());
            }
            self.binary.push(0);
        }
        Ok(())
    }

    fn begin_append(&mut self, count: usize, element_size: usize) -> GlbResult<usize> {
        self.align()?;
        check_binary_growth(self.binary.len(), count, element_size)?;
        Ok(self.binary.len())
    }

    fn append_f32s(&mut self, values: &[f32]) -> GlbResult<usize> {
        let offset = self.begin_append(values.len(), 4)?;
        self.binary.reserve(values.len() * 4);
        for v in values {
            self.binary.extend_from_slice(&v.to_le_bytes());
        }
        Ok(offset)
    }

    fn append_u32s(&mut self, values: &[u32]) -> GlbResult<usize> {
        let offset = self.begin_append(values.len(), 4)?;
        self.binary.reserve(values.len() * 4);
        for v in values {
            self.binary.extend_from_slice(&v.to_le_bytes());
        }
        Ok(offset)
    }

    fn append_bytes(&mut self, values: &[u8]) -> GlbResult<usize> {
        let offset = self.begin_append(values.len(), 1)?;
        self.binary.extend_from_slice(values);
        Ok(offset)
    }

    /// TinyGLTF's `SerializeGltfBufferView`: `byteOffset` only when > 0,
    /// `target` only when it is a vertex/index target.
    fn add_view(&mut self, offset: usize, length: usize, target: Option<i64>) -> usize {
        let mut view = JsonValue::object();
        view.set("buffer", 0);
        view.set("byteLength", length);
        if offset > 0 {
            view.set("byteOffset", offset);
        }
        if let Some(t) = target {
            view.set("target", t);
        }
        self.buffer_views.push(view);
        self.buffer_views.len() - 1
    }

    fn add_accessor(
        &mut self,
        view: usize,
        count: usize,
        component_type: i64,
        ty: &str,
        bounds: Option<([f64; 3], [f64; 3])>,
    ) -> usize {
        let mut acc = JsonValue::object();
        acc.set("bufferView", view);
        acc.set("componentType", component_type);
        acc.set("count", count);
        if let Some((min, max)) = bounds {
            acc.set(
                "min",
                JsonValue::Array(min.iter().map(|&v| v.into()).collect()),
            );
            acc.set(
                "max",
                JsonValue::Array(max.iter().map(|&v| v.into()).collect()),
            );
        }
        acc.set("type", ty);
        self.accessors.push(acc);
        self.accessors.len() - 1
    }

    /// Writes one triangle primitive's four arrays and returns its glTF
    /// `primitive` object.
    pub fn add_primitive(
        &mut self,
        positions: &[f32],
        normals: &[f32],
        uvs: &[f32],
        indices: &[u32],
        material_index: usize,
    ) -> GlbResult<JsonValue> {
        let off = self.append_f32s(positions)?;
        let view = self.add_view(off, positions.len() * 4, Some(TARGET_ARRAY_BUFFER));
        let mut min = [
            positions[0] as f64,
            positions[1] as f64,
            positions[2] as f64,
        ];
        let mut max = min;
        for p in positions.as_chunks::<3>().0.iter().skip(1) {
            for axis in 0..3 {
                min[axis] = min[axis].min(p[axis] as f64);
                max[axis] = max[axis].max(p[axis] as f64);
            }
        }
        let position_acc = self.add_accessor(
            view,
            positions.len() / 3,
            COMPONENT_FLOAT,
            "VEC3",
            Some((min, max)),
        );

        let off = self.append_f32s(normals)?;
        let view = self.add_view(off, normals.len() * 4, Some(TARGET_ARRAY_BUFFER));
        let normal_acc = self.add_accessor(view, normals.len() / 3, COMPONENT_FLOAT, "VEC3", None);

        let off = self.append_f32s(uvs)?;
        let view = self.add_view(off, uvs.len() * 4, Some(TARGET_ARRAY_BUFFER));
        let uv_acc = self.add_accessor(view, uvs.len() / 2, COMPONENT_FLOAT, "VEC2", None);

        let off = self.append_u32s(indices)?;
        let view = self.add_view(off, indices.len() * 4, Some(TARGET_ELEMENT_ARRAY_BUFFER));
        let index_acc =
            self.add_accessor(view, indices.len(), COMPONENT_UNSIGNED_INT, "SCALAR", None);

        let mut attributes = JsonValue::object();
        attributes.set("POSITION", position_acc);
        attributes.set("NORMAL", normal_acc);
        attributes.set("TEXCOORD_0", uv_acc);
        let mut prim = JsonValue::object();
        prim.set("attributes", attributes);
        prim.set("indices", index_acc);
        prim.set("material", material_index);
        prim.set("mode", MODE_TRIANGLES);
        Ok(prim)
    }

    /// Embeds every texture image and returns `(samplers, images, textures)`.
    pub fn add_textures(
        &mut self,
        textures: &[SceneTexture],
    ) -> GlbResult<(JsonValue, JsonValue, JsonValue)> {
        let mut sampler = JsonValue::object();
        sampler.set("wrapS", WRAP_REPEAT);
        sampler.set("wrapT", WRAP_REPEAT);
        let samplers = JsonValue::Array(vec![sampler]);
        let mut images = JsonValue::array();
        let mut gltf_textures = JsonValue::array();
        for (i, tex) in textures.iter().enumerate() {
            let off = self.append_bytes(&tex.data)?;
            // An image view has no target: glTF's target enum only covers
            // vertex/index buffers.
            let view = self.add_view(off, tex.data.len(), None);
            let mut image = JsonValue::object();
            image.set("mimeType", &tex.mime_type);
            image.set("bufferView", view);
            images.push(image);
            let mut texture = JsonValue::object();
            texture.set("sampler", 0);
            texture.set("source", i);
            gltf_textures.push(texture);
        }
        Ok((samplers, images, gltf_textures))
    }
}

/// TinyGLTF's `SerializeGltfMaterial`, including its default-value
/// omissions (`OPAQUE` alpha mode, all-ones color, factors of 1).
pub(crate) fn material_json(source: &GltfMaterial, embed_textures: bool) -> JsonValue {
    let pbr = &source.pbr_metallic_roughness;
    let mut material = JsonValue::object();
    // glTF's default alphaMode is OPAQUE, which makes a renderer ignore
    // alpha entirely. Translucent materials need BLEND; textured opaque
    // ones get MASK so an image's own alpha can cut out foliage/fences (a
    // no-op for an image with no real cutout).
    if pbr.base_color_factor[3] < 1.0 {
        material.set("alphaMode", "BLEND");
    } else if pbr.base_color_texture.is_some() {
        material.set("alphaMode", "MASK");
    }
    if source.double_sided {
        material.set("doubleSided", true);
    }
    let mut pbr_json = JsonValue::object();
    let eps = 1e-12;
    if pbr
        .base_color_factor
        .iter()
        .any(|&c| (c - 1.0).abs() >= eps)
    {
        pbr_json.set(
            "baseColorFactor",
            JsonValue::Array(pbr.base_color_factor.iter().map(|&c| c.into()).collect()),
        );
    }
    if pbr.metallic_factor != 1.0 {
        pbr_json.set("metallicFactor", pbr.metallic_factor);
    }
    if pbr.roughness_factor != 1.0 {
        pbr_json.set("roughnessFactor", pbr.roughness_factor);
    }
    if let (true, Some(index)) = (embed_textures, pbr.base_color_texture) {
        let mut info = JsonValue::object();
        info.set("index", index);
        pbr_json.set("baseColorTexture", info);
    }
    if pbr_json.as_object().is_some_and(|o| !o.is_empty()) {
        material.set("pbrMetallicRoughness", pbr_json);
    }
    material
}

/// Wraps a glTF JSON document and its binary buffer in a GLB container
/// (TinyGLTF's `WriteBinaryGltfStream`): the JSON chunk is space-padded,
/// the BIN chunk (omitted when empty) zero-padded, both to 4 bytes.
pub(crate) fn write_glb(document: &JsonValue, binary: &[u8]) -> GlbResult<Vec<u8>> {
    let json = json_writer::write(document, Flavor::Gltf);
    let json_padded = align4(json.len());
    let bin_padded = align4(binary.len());
    let total = 12 + 8 + json_padded + if binary.is_empty() { 0 } else { 8 + bin_padded };
    if total > GLB_LIMIT {
        return Err(GlbError::Length(
            "serialized GLB exceeds its 32-bit file-size limit".into(),
        ));
    }
    let mut out = Vec::with_capacity(total);
    out.extend_from_slice(b"glTF");
    out.extend_from_slice(&2u32.to_le_bytes());
    out.extend_from_slice(&(total as u32).to_le_bytes());
    out.extend_from_slice(&(json_padded as u32).to_le_bytes());
    out.extend_from_slice(&0x4E4F_534Au32.to_le_bytes());
    out.extend_from_slice(json.as_bytes());
    out.resize(out.len() + (json_padded - json.len()), b' ');
    if !binary.is_empty() {
        out.extend_from_slice(&(bin_padded as u32).to_le_bytes());
        out.extend_from_slice(&0x004E_4942u32.to_le_bytes());
        out.extend_from_slice(binary);
        out.resize(out.len() + (bin_padded - binary.len()), 0);
    }
    Ok(out)
}

/// Assembles the top-level glTF object; empty arrays are omitted, as
/// TinyGLTF does.
#[allow(clippy::too_many_arguments)]
pub(crate) fn document(
    generator: &str,
    builder: GltfBuilder,
    buffer: Option<usize>,
    materials: Vec<JsonValue>,
    meshes: Vec<JsonValue>,
    nodes: Vec<JsonValue>,
    scene: JsonValue,
    textures: Option<(JsonValue, JsonValue, JsonValue)>,
) -> (JsonValue, Vec<u8>) {
    let mut doc = JsonValue::object();
    if !builder.accessors.is_empty() {
        doc.set("accessors", builder.accessors);
    }
    let mut asset = JsonValue::object();
    asset.set("generator", generator);
    asset.set("version", "2.0");
    doc.set("asset", asset);
    if !builder.buffer_views.is_empty() {
        doc.set("bufferViews", builder.buffer_views);
    }
    if let Some(len) = buffer {
        let mut b = JsonValue::object();
        b.set("byteLength", len);
        doc.set("buffers", vec![b]);
    }
    if let Some((samplers, images, gltf_textures)) = textures {
        doc.set("images", images);
        doc.set("samplers", samplers);
        doc.set("textures", gltf_textures);
    }
    if !materials.is_empty() {
        doc.set("materials", materials);
    }
    if !meshes.is_empty() {
        doc.set("meshes", meshes);
    }
    if !nodes.is_empty() {
        doc.set("nodes", nodes);
    }
    doc.set("scene", 0);
    doc.set("scenes", vec![scene]);
    (doc, builder.binary)
}

/// Serializes a baked scene as a binary glTF 2.0 (GLB) asset: one mesh
/// holding one primitive per [`crate::GlbPrimitive`], under a single node.
pub fn to_glb(scene: &Scene, options: &GlbOptions) -> GlbResult<Vec<u8>> {
    validate_scene(scene)?;
    let materials: Vec<JsonValue> = scene
        .gltf_materials
        .iter()
        .map(|m| material_json(m, options.textures))
        .collect();

    let mut gltf_scene = JsonValue::object();
    gltf_scene.set("name", "OpenSKP Scene");

    let mut builder = GltfBuilder::new();
    if scene.glb_primitives.is_empty() {
        let (doc, binary) = document(
            "OpenSKP",
            builder,
            None,
            materials,
            Vec::new(),
            Vec::new(),
            gltf_scene,
            None,
        );
        return write_glb(&doc, &binary);
    }

    let mut primitives = JsonValue::array();
    for p in &scene.glb_primitives {
        primitives.push(builder.add_primitive(
            &p.positions,
            &p.normals,
            &p.uvs,
            &p.indices,
            p.material_index,
        )?);
    }
    let textures = if options.textures && !scene.textures.is_empty() {
        Some(builder.add_textures(&scene.textures)?)
    } else {
        None
    };

    let mut mesh = JsonValue::object();
    mesh.set("name", "OpenSKP Mesh");
    mesh.set("primitives", primitives);
    let mut node = JsonValue::object();
    node.set("name", "OpenSKP Scene");
    node.set("mesh", 0);
    gltf_scene.set("nodes", vec![JsonValue::Int(0)]);

    let len = builder.binary.len();
    let (doc, binary) = document(
        "OpenSKP",
        builder,
        Some(len),
        materials,
        vec![mesh],
        vec![node],
        gltf_scene,
        textures,
    );
    write_glb(&doc, &binary)
}

/// Serializes a baked scene and writes the GLB bytes to `output_path`. The
/// parent directory is not created.
pub fn export_glb(
    scene: &Scene,
    output_path: impl AsRef<Path>,
    options: &GlbOptions,
) -> GlbResult<()> {
    let bytes = to_glb(scene, options)?;
    std::fs::write(output_path, bytes)?;
    Ok(())
}
