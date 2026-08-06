#pragma once

#include <array>
#include <cstdint>
#include <map>
#include <string>
#include <vector>

#include <openskp/export.hpp>

namespace openskp {

struct InstanceNode {
  std::string name;
  std::string definition_name;
  std::string layer;
  std::array<double, 3> position_mm{};
  std::map<std::string, std::string> properties;
  std::vector<InstanceNode> children;
};

struct MeshMetadata {
  std::string name;
  std::string definition_name;
  std::string layer;
  std::array<double, 3> position_mm{};
  std::map<std::string, std::string> properties;
  std::string path;
};

struct GlbPrimitive {
  std::vector<float> positions;
  std::vector<float> normals;
  std::vector<std::uint32_t> indices;
  std::size_t material_index{};
  std::string geom_name;
};

struct PbrMetallicRoughness {
  std::array<double, 4> base_color_factor{1, 1, 1, 1};
  double metallic_factor{};
  double roughness_factor{0.8};
};

struct GltfMaterial {
  PbrMetallicRoughness pbr_metallic_roughness;
};

struct OPENSKP_EXPORT Scene {
  InstanceNode scene_hierarchy;
  std::map<std::string, MeshMetadata> mesh_index;
  std::vector<GlbPrimitive> glb_primitives;
  std::vector<GltfMaterial> gltf_materials;
};
}  // namespace openskp
