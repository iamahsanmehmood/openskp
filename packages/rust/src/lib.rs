//! OpenSKP — pure Rust port of the OpenSKP SketchUp (.skp) toolkit.
//!
//! Ported from the C++ package at `openskp/packages/cpp`. No external dependencies.
//!
//! ```no_run
//! use openskp::{ParseOptions, SkpFile};
//!
//! let model = SkpFile::open("building.skp")?.parse(&ParseOptions::default())?;
//! println!("{} {} definitions", model.version, model.definitions.len());
//! # Ok::<(), Box<dyn std::error::Error>>(())
//! ```

pub mod codegen;
pub mod create;
pub mod dxf_export;
pub mod edit;
pub mod errors;
pub mod fragments;
pub mod glb;
pub mod ifc_export;
pub mod instanced_glb;
pub mod instanced_scene;
pub mod json_export;
pub mod model;
pub mod obj_export;
pub mod observability;
pub mod parser;
pub mod ply_export;
pub mod scene;
pub mod stl_export;

mod json_writer;
pub mod triangulator;

/// Raw DEFLATE / zlib compressor (no external crates).
pub mod deflate;
/// Raw DEFLATE / zlib decompressor (miniz `tinfl` / `mz_uncompress` replacement).
pub mod inflate;
/// In-memory ZIP reader (miniz `mz_zip_reader_*` replacement).
pub mod zip;
// Minimal FlatBuffers builder/reader (flatbuffers library replacement);
// documented by its own inner docs.
pub mod flatbuffers;

#[doc(hidden)]
pub mod curves;
#[doc(hidden)]
pub mod earcut;
#[doc(hidden)]
pub mod face_groups;
#[doc(hidden)]
pub mod instanced_scene_build;
#[doc(hidden)]
pub mod internal;
#[doc(hidden)]
pub mod legacy;
#[doc(hidden)]
pub mod scene_build;

pub use codegen::to_rust_code;
pub use dxf_export::{export_dxf, to_dxf, DxfError};
pub use edit::{open_existing, OpenExistingError, OpenExistingResult};
pub use errors::SkpParseError;
pub use fragments::{
    export_fragments, from_fragments, read_fragments, to_fragments, FragmentsError,
};
pub use glb::{export_glb, to_glb, GlbError, GlbOptions, GlbResult};
pub use ifc_export::{
    classify_element, export_ifc, generate_ifc_guid, to_ifc, IfcClassifier, METRES_TO_MM,
};
pub use instanced_glb::{export_instanced_glb, to_instanced_glb, InstancedGlbOptions};
pub use instanced_scene::{
    InstancedCurveResource, InstancedMeshResource, InstancedNode, InstancedScene, LocalCurve,
    LocalPrimitive, SceneBounds,
};
pub use json_export::{export_json, to_json, to_json_string, JsonKind, JsonValue};
pub use model::{
    loose_edge_runs, stringify_attr_dict, stringify_attr_dictionaries, ArcCurve, AttributeKind,
    ByteBuffer, CoEdge, Color3, Color4, ConstructionLine, ConstructionPoint, Definition, Dimension,
    Edge, EntityId, Face, Instance, Layer, LooseEdgeRun, Material, Page, ParsedAttrDict,
    ParsedAttrDictionaries, ParsedAttribute, SectionPlane, SkpModel, StringDictionaries, Style,
    StyleItem, StyleWatermark, TextEntity, Texture, Vec3, Vertex, ViewCamera, METRES_TO_INCHES,
};
pub use obj_export::{export_obj, to_mtl, to_obj};
pub use observability::{
    stage_name, LogLevel, ParseOptions, ParseProgress, ParseStage, PROGRESS_INTERVAL,
};
pub use parser::{build_instanced_scene, build_scene, parse_skp, SkpFile};
pub use ply_export::{export_ply, to_ply_ascii, to_ply_binary};
pub use scene::{
    CurveArc, CurveSetMetadata, GlbPrimitive, GltfMaterial, InstanceNode, MeshMetadata,
    PbrMetallicRoughness, Scene, SceneTexture,
};
pub use stl_export::{export_stl, to_stl_ascii, to_stl_binary};
pub use triangulator::{compute_face_normal, triangulate_face_3d};

pub use create::{
    create, ArcOptions, AttributeDict, AttributeValue, CircleOptions, ComponentDefinitionBuilder,
    DefinitionHandle, DefinitionOptions, DocumentOptions, FaceOptions, GroupInstanceOptions,
    GroupOptions, GuideOptions, ImageOptions, InstanceOptions, LayerOptions, PolylineOptions,
    SkpBuilder, SkpWriteError,
};
