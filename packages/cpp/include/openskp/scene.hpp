#pragma once

#include <array>
#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <vector>

#include <openskp/export.hpp>
#include <openskp/model.hpp>

namespace openskp {

struct InstanceNode {
  std::string name;
  std::string definition_name;
  std::string layer;
  std::array<double, 3> position_mm{};
  std::map<std::string, std::string> properties;
  std::vector<InstanceNode> children;
  // Every attribute dictionary other than SketchUp's own
  // dynamic_attributes (already on `properties`) and SU_InstanceSet
  // (always-present Owner/Status boilerplate). Values are stringified.
  // Model-level Instance::attribute_dictionaries keeps both, with
  // native types.
  std::map<std::string, std::map<std::string, std::string>> attribute_dictionaries;
};

struct MeshMetadata {
  std::string name;
  std::string definition_name;
  std::string layer;
  std::array<double, 3> position_mm{};
  std::map<std::string, std::string> properties;
  std::string path;
  // Same as InstanceNode::attribute_dictionaries above - backfilled from
  // the instance that placed this mesh's definition, matching `name`
  // and `properties` above (see build_scene_raw's deferred backfill).
  std::map<std::string, std::map<std::string, std::string>> attribute_dictionaries;
};

struct GlbPrimitive {
  std::vector<float> positions;
  std::vector<float> normals;
  // Flat [u, v, u, v, ...] texture coordinates, matching positions 1:1.
  // Computed from the source face's uv_transform (or the default
  // face-plane projection when a face has none): a face-plane basis from
  // the normal, inverting uv_transform when present, divided by the
  // material's texture tile size. A vertex shared by two faces that
  // disagree on UV is split, since indexed glTF meshes need
  // position/normal/uv aligned per vertex. Faces with a PROJECTED texture
  // (terrain-drape, e.g. Add Location) still use the face-plane formula
  // here, since the real projection-plane basis isn't captured in the
  // parsed data - their UVs will be approximate.
  std::vector<float> uvs;
  std::vector<std::uint32_t> indices;
  std::size_t material_index{};
  std::string geom_name;
  bool unpainted_back{};
};

struct PbrMetallicRoughness {
  std::array<double, 4> base_color_factor{1, 1, 1, 1};
  double metallic_factor{};
  double roughness_factor{0.8};
  // Index into Scene::textures, or nullopt for an untextured material.
  // baseColorFactor stays the resolved color even with a texture set:
  // glTF multiplies the two, and SketchUp's own colorized materials rely
  // on exactly that tint.
  std::optional<std::size_t> base_color_texture;
};

struct GltfMaterial {
  std::string name;
  std::string texture_path;
  PbrMetallicRoughness pbr_metallic_roughness;
  bool double_sided{};
};

/// One texture image referenced by Scene::gltf_materials.
struct SceneTexture {
  /// The image file's raw bytes, exactly as stored in the .skp.
  ByteBuffer data;
  /// Sniffed from the bytes, not from filename: SketchUp records the
  /// authoring machine's path, whose extension can disagree with the
  /// content.
  std::string mime_type;
  std::string filename;
};

/// An analytic arc recovered for a loose-edge run: points in the run's own
/// frame (metres, glTF Y-up) plus 0-based [start, mid, end] index triples into
/// them, ready for IFC4 IfcArcIndex.
struct CurveArc {
  std::vector<std::array<double, 3>> points;
  std::vector<std::array<std::size_t, 3>> segments;

  bool operator==(const CurveArc& other) const {
    return points == other.points && segments == other.segments;
  }

  bool operator!=(const CurveArc& other) const { return !(*this == other); }
};

/// One world-space polyline run of loose edges (edges no face uses).
struct CurveSetMetadata {
  std::string name;
  std::string definition_name;
  std::string layer;
  std::string path;
  bool name_is_generated{};
  std::vector<std::array<double, 3>> points_m;
  bool closed{};
  std::optional<CurveArc> arc;
  std::map<std::string, std::map<std::string, std::string>> attribute_dictionaries;
};

struct OPENSKP_EXPORT Scene {
  InstanceNode scene_hierarchy;
  std::map<std::string, MeshMetadata> mesh_index;
  std::vector<GlbPrimitive> glb_primitives;
  std::vector<GltfMaterial> gltf_materials;
  // Distinct texture images the placed materials use, deduplicated by
  // source bytes. Empty when nothing placed in the scene is textured.
  std::vector<SceneTexture> textures;
  // Each layer's own hidden/visible state, by layer name - threaded
  // through from the parser's existing per-layer hidden state. Legacy
  // (pre-2021) files carry a real per-layer flag; VFF (2021+) files are
  // not yet read here (open gap, see docs/LANGUAGE_PARITY.md), so every
  // VFF layer is absent from this map (treated as visible by consumers).
  std::map<std::string, bool> layer_hidden;
  // Number of top-level entities dropped when a classic (pre-2021) root
  // entity walk stopped early (0 for complete classic files and VFF files).
  std::size_t unread_root_entities{};
  // Saved scenes (SketchUp's "Scenes" tabs), identical to SkpModel::pages.
  // Camera eye/target/up and ortho_height stay in SketchUp's native Z-up
  // frame and inches (unlike glb_primitives, which are Y-up metres).
  std::vector<Page> pages;
  /// World-space polyline runs of loose edges (edges no face uses), when
  /// ParseOptions::include_curve_sets is true.
  std::vector<CurveSetMetadata> curve_sets;

  std::vector<GlbPrimitive> surface_primitives() const;
};
}  // namespace openskp
