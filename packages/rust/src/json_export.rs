//! openskp's canonical JSON export schema (`json_export.hpp` /
//! `json_export.cpp`), shared with the Python (`to_dict`), TypeScript
//! (`toJSON`), Dart (`toJson`) and .NET (`JsonExport.ToDict`) ports.

use std::collections::BTreeMap;
use std::io;
use std::path::Path;

use crate::json_writer::{self, Flavor};
use crate::model::{Definition, Edge, Face, Instance, SkpModel, StringDictionaries, Vertex};
use crate::scene::{InstanceNode, MeshMetadata, Scene};

/// A minimal, dynamically-typed JSON value tree. Objects are a vector of
/// pairs (not a map) to preserve insertion order, matching every other
/// port's object key ordering.
#[derive(Debug, Clone, PartialEq, Default)]
pub enum JsonValue {
    #[default]
    Null,
    Bool(bool),
    Int(i64),
    Double(f64),
    String(String),
    Array(Vec<JsonValue>),
    Object(Vec<(String, JsonValue)>),
}

/// The kind of value a [`JsonValue`] holds.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum JsonKind {
    Null,
    Bool,
    Int,
    Double,
    String,
    Array,
    Object,
}

impl JsonValue {
    /// An empty object.
    pub fn object() -> JsonValue {
        JsonValue::Object(Vec::new())
    }

    /// An empty array.
    pub fn array() -> JsonValue {
        JsonValue::Array(Vec::new())
    }

    /// Appends a key/value pair.
    ///
    /// # Panics
    /// When this value is not an object.
    pub fn set(&mut self, key: impl Into<String>, val: impl Into<JsonValue>) {
        match self {
            JsonValue::Object(entries) => entries.push((key.into(), val.into())),
            _ => panic!("JsonValue::set on a non-object"),
        }
    }

    /// Appends an element.
    ///
    /// # Panics
    /// When this value is not an array.
    pub fn push(&mut self, val: impl Into<JsonValue>) {
        match self {
            JsonValue::Array(items) => items.push(val.into()),
            _ => panic!("JsonValue::push on a non-array"),
        }
    }

    pub fn kind(&self) -> JsonKind {
        match self {
            JsonValue::Null => JsonKind::Null,
            JsonValue::Bool(_) => JsonKind::Bool,
            JsonValue::Int(_) => JsonKind::Int,
            JsonValue::Double(_) => JsonKind::Double,
            JsonValue::String(_) => JsonKind::String,
            JsonValue::Array(_) => JsonKind::Array,
            JsonValue::Object(_) => JsonKind::Object,
        }
    }

    pub fn is_null(&self) -> bool {
        matches!(self, JsonValue::Null)
    }

    pub fn is_object(&self) -> bool {
        matches!(self, JsonValue::Object(_))
    }

    pub fn is_array(&self) -> bool {
        matches!(self, JsonValue::Array(_))
    }

    pub fn as_object(&self) -> Option<&[(String, JsonValue)]> {
        match self {
            JsonValue::Object(entries) => Some(entries),
            _ => None,
        }
    }

    pub fn as_array(&self) -> Option<&[JsonValue]> {
        match self {
            JsonValue::Array(items) => Some(items),
            _ => None,
        }
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            JsonValue::String(s) => Some(s),
            _ => None,
        }
    }

    pub fn as_int(&self) -> Option<i64> {
        match self {
            JsonValue::Int(i) => Some(*i),
            _ => None,
        }
    }

    pub fn as_double(&self) -> Option<f64> {
        match self {
            JsonValue::Double(d) => Some(*d),
            _ => None,
        }
    }

    pub fn as_bool(&self) -> Option<bool> {
        match self {
            JsonValue::Bool(b) => Some(*b),
            _ => None,
        }
    }

    /// Looks up a key in an object value; `None` when absent or this value
    /// isn't an object.
    pub fn find(&self, key: &str) -> Option<&JsonValue> {
        self.as_object()?
            .iter()
            .find(|(k, _)| k == key)
            .map(|(_, v)| v)
    }
}

impl From<bool> for JsonValue {
    fn from(v: bool) -> Self {
        JsonValue::Bool(v)
    }
}

impl From<i64> for JsonValue {
    fn from(v: i64) -> Self {
        JsonValue::Int(v)
    }
}

impl From<i32> for JsonValue {
    fn from(v: i32) -> Self {
        JsonValue::Int(v.into())
    }
}

impl From<u32> for JsonValue {
    fn from(v: u32) -> Self {
        JsonValue::Int(v.into())
    }
}

impl From<u8> for JsonValue {
    fn from(v: u8) -> Self {
        JsonValue::Int(v.into())
    }
}

impl From<usize> for JsonValue {
    fn from(v: usize) -> Self {
        JsonValue::Int(v as i64)
    }
}

impl From<f64> for JsonValue {
    fn from(v: f64) -> Self {
        JsonValue::Double(v)
    }
}

impl From<String> for JsonValue {
    fn from(v: String) -> Self {
        JsonValue::String(v)
    }
}

impl From<&str> for JsonValue {
    fn from(v: &str) -> Self {
        JsonValue::String(v.to_owned())
    }
}

impl From<&String> for JsonValue {
    fn from(v: &String) -> Self {
        JsonValue::String(v.clone())
    }
}

impl From<Vec<JsonValue>> for JsonValue {
    fn from(v: Vec<JsonValue>) -> Self {
        JsonValue::Array(v)
    }
}

impl<T: Into<JsonValue>> From<Option<T>> for JsonValue {
    fn from(v: Option<T>) -> Self {
        v.map_or(JsonValue::Null, Into::into)
    }
}

/// Serializes a [`JsonValue`] tree to a JSON string. `indent <= 0` produces
/// compact output; `indent > 0` pretty-prints with that many spaces per
/// nesting level. Doubles are written as `%.17g` (exact round-trip).
pub fn to_json_string(value: &JsonValue, indent: i32) -> String {
    json_writer::write(value, Flavor::Canonical { indent })
}

fn vertex_to_json(v: &Vertex) -> JsonValue {
    let mut obj = JsonValue::object();
    obj.set("id", v.id);
    obj.set("x", v.x);
    obj.set("y", v.y);
    obj.set("z", v.z);
    obj
}

fn edge_to_json(e: &Edge) -> JsonValue {
    let mut obj = JsonValue::object();
    obj.set("id", e.id);
    obj.set("v1_id", e.v1_id);
    obj.set("v2_id", e.v2_id);
    obj
}

fn face_to_json(f: &Face) -> JsonValue {
    let mut obj = JsonValue::object();
    obj.set("id", f.id);
    let mut loops = JsonValue::array();
    for lp in &f.loops {
        let mut loop_json = JsonValue::array();
        for ce in lp {
            let mut ce_json = JsonValue::object();
            ce_json.set("edge_id", ce.edge_id);
            ce_json.set("orientation", ce.orientation);
            loop_json.push(ce_json);
        }
        loops.push(loop_json);
    }
    obj.set("loops", loops);
    obj.set(
        "normal",
        f.normal
            .map(|n| JsonValue::Array(n.iter().map(|&c| c.into()).collect())),
    );
    obj
}

fn instance_to_json(inst: &Instance) -> JsonValue {
    let mut obj = JsonValue::object();
    obj.set("name", &inst.name);
    obj.set("ref_idx", inst.ref_idx);
    obj.set("guid", &inst.guid);
    obj.set(
        "matrix",
        JsonValue::Array(inst.matrix.iter().map(|&m| m.into()).collect()),
    );
    obj
}

fn definition_to_json(defn: &Definition) -> JsonValue {
    let mut obj = JsonValue::object();
    obj.set("id", defn.id);
    obj.set("guid", &defn.guid);
    obj.set("name", &defn.name);
    obj.set("vertex_count", defn.vertices.len());
    obj.set("edge_count", defn.edges.len());
    obj.set("face_count", defn.faces.len());
    obj.set(
        "vertices",
        JsonValue::Array(defn.vertices.values().map(vertex_to_json).collect()),
    );
    obj.set(
        "edges",
        JsonValue::Array(defn.edges.values().map(edge_to_json).collect()),
    );
    obj.set(
        "faces",
        JsonValue::Array(defn.faces.values().map(face_to_json).collect()),
    );
    obj.set(
        "instances",
        JsonValue::Array(defn.instances.iter().map(instance_to_json).collect()),
    );
    obj
}

fn string_map_to_json(map: &BTreeMap<String, String>) -> JsonValue {
    JsonValue::Object(map.iter().map(|(k, v)| (k.clone(), v.into())).collect())
}

/// Every attribute dictionary an instance/mesh carries, keyed by the
/// dictionary's own name - where a third-party plugin's per-instance data
/// reaches JSON output.
pub(crate) fn attribute_dictionaries_to_json(dicts: &StringDictionaries) -> JsonValue {
    JsonValue::Object(
        dicts
            .iter()
            .map(|(name, entries)| (name.clone(), string_map_to_json(entries)))
            .collect(),
    )
}

fn position_to_json(p: &[f64; 3]) -> JsonValue {
    JsonValue::Array(p.iter().map(|&c| c.into()).collect())
}

fn instance_node_to_json(node: &InstanceNode) -> JsonValue {
    let mut obj = JsonValue::object();
    obj.set("name", &node.name);
    obj.set("definition_name", &node.definition_name);
    obj.set("layer", &node.layer);
    obj.set("position_mm", position_to_json(&node.position_mm));
    obj.set("properties", string_map_to_json(&node.properties));
    obj.set(
        "attribute_dictionaries",
        attribute_dictionaries_to_json(&node.attribute_dictionaries),
    );
    obj.set(
        "children",
        JsonValue::Array(node.children.iter().map(instance_node_to_json).collect()),
    );
    obj
}

fn mesh_metadata_to_json(m: &MeshMetadata) -> JsonValue {
    let mut obj = JsonValue::object();
    obj.set("name", &m.name);
    obj.set("definition_name", &m.definition_name);
    obj.set("layer", &m.layer);
    obj.set("position_mm", position_to_json(&m.position_mm));
    obj.set("properties", string_map_to_json(&m.properties));
    obj.set(
        "attribute_dictionaries",
        attribute_dictionaries_to_json(&m.attribute_dictionaries),
    );
    obj.set("path", &m.path);
    obj
}

/// Converts a parsed [`SkpModel`] (and optionally a baked [`Scene`]) to
/// openskp's canonical JSON export schema. Pass the result of
/// `build_scene()` as `scene` to also include the resolved, world-space
/// `scene_hierarchy`/`mesh_index`; pass `None` for a lighter summary
/// covering just the raw model (`scene_hierarchy` is then `null`).
///
/// The raw (pre-bake) per-definition `instances` list is intentionally flat,
/// with no layer/properties/children keys, so the schema stays identical
/// across languages; the resolved nested tree is `scene_hierarchy`.
pub fn to_json(model: &SkpModel, scene: Option<&Scene>) -> JsonValue {
    let definitions = JsonValue::Object(
        model
            .definitions
            .iter()
            .map(|(id, d)| (id.to_string(), definition_to_json(d)))
            .collect(),
    );

    let mut layers = JsonValue::array();
    for l in &model.layers {
        let mut layer_obj = JsonValue::object();
        layer_obj.set("name", &l.name);
        let mut color = JsonValue::object();
        color.set("r", l.color[0]);
        color.set("g", l.color[1]);
        color.set("b", l.color[2]);
        layer_obj.set("color", color);
        layer_obj.set("hidden", l.hidden);
        layers.push(layer_obj);
    }

    let mut materials = JsonValue::array();
    for m in &model.materials {
        let mut mat_obj = JsonValue::object();
        mat_obj.set("name", &m.name);
        let mut color = JsonValue::object();
        color.set("r", m.color[0]);
        color.set("g", m.color[1]);
        color.set("b", m.color[2]);
        color.set("a", m.color[3]);
        mat_obj.set("color", color);
        mat_obj.set("transparency", m.transparency);
        materials.push(mat_obj);
    }

    let mesh_index = JsonValue::Object(
        scene
            .map(|s| {
                s.mesh_index
                    .iter()
                    .map(|(name, meta)| (name.clone(), mesh_metadata_to_json(meta)))
                    .collect()
            })
            .unwrap_or_default(),
    );

    let mut result = JsonValue::object();
    result.set("format_version", "1.0");
    result.set("sketchup_version", &model.version);
    result.set("units", model.units.as_ref());
    result.set("total_definitions", model.definitions.len());
    result.set("total_layers", model.layers.len());
    result.set("total_meshes", scene.map_or(0, |s| s.mesh_index.len()));
    result.set("root", definition_to_json(model.root()));
    result.set("definitions", definitions);
    result.set("layers", layers);
    result.set("materials", materials);
    result.set("mesh_index", mesh_index);
    result.set(
        "scene_hierarchy",
        scene.map(|s| instance_node_to_json(&s.scene_hierarchy)),
    );
    result
}

/// Serializes [`to_json`]'s result and writes it to a file. The parent
/// directory must exist.
pub fn export_json(
    model: &SkpModel,
    output_path: impl AsRef<Path>,
    scene: Option<&Scene>,
    indent: i32,
) -> io::Result<()> {
    let path = output_path.as_ref();
    let text = to_json_string(&to_json(model, scene), indent);
    std::fs::write(path, text).map_err(|e| {
        io::Error::new(
            e.kind(),
            format!(
                "failed to open output path for JSON export: {}: {e}",
                path.display()
            ),
        )
    })
}
