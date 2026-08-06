#pragma once

#include <array>
#include <cstdint>
#include <deque>
#include <filesystem>
#include <map>
#include <optional>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#include <openskp/export.hpp>

namespace openskp {

using EntityId = std::int64_t;
using ByteBuffer = std::vector<std::uint8_t>;
using Vec3 = std::array<double, 3>;
using Color3 = std::array<std::uint8_t, 3>;
using Color4 = std::array<std::uint8_t, 4>;

struct Vertex {
  EntityId id{};
  double x{};
  double y{};
  double z{};
};

struct Edge {
  EntityId id{};
  EntityId v1_id{};
  EntityId v2_id{};
  bool soft{};
  bool smooth{};
  bool hidden{};
};

struct CoEdge {
  EntityId edge_id{};
  std::int64_t orientation{};
};

struct Face {
  EntityId id{};
  std::vector<std::vector<CoEdge>> loops;
  std::optional<Vec3> normal;
  std::optional<EntityId> material_id;
  std::optional<EntityId> back_material_id;
  std::optional<std::array<double, 9>> uv_transform;
  std::optional<std::array<double, 9>> uv_transform_back;
};

struct Layer {
  std::string name;
  Color3 color{200, 200, 200};
};

struct Texture {
  std::string filename;
  double width{};
  double height{};
  std::optional<ByteBuffer> data;
  OPENSKP_EXPORT void save(const std::filesystem::path& path) const;
};

struct Material {
  std::string name;
  Color4 color{200, 200, 200, 255};
  double transparency{1.0};
  std::optional<EntityId> id;
  std::optional<Texture> texture;
  bool colorized{};
  std::int32_t colorize_type{};
};

struct Style {
  std::string name;
  std::optional<Color3> front_color;
  std::optional<Color3> back_color;
};

struct Instance {
  std::string name;
  std::optional<EntityId> ref_idx;
  std::string guid;
  std::vector<double> matrix;
  std::string layer;
  std::map<std::string, std::string> properties;
  std::vector<Instance> children;
  std::optional<EntityId> material_id;
};

struct Definition {
  EntityId id{};
  std::string guid;
  std::string name;
  std::map<EntityId, Vertex> vertices;
  std::map<EntityId, Edge> edges;
  std::map<EntityId, Face> faces;
  std::vector<Instance> instances;
  bool always_faces_camera{};
  bool is_image{};
};

class OPENSKP_EXPORT SkpModel {
 public:
  std::string version{"unknown"};
  std::map<EntityId, Definition> definitions;
  std::vector<Layer> layers;
  std::deque<Material> materials;
  std::vector<Style> styles;

  Definition& root() noexcept;
  const Definition& root() const noexcept;

  Material* material_by_id(EntityId id) noexcept;
  const Material* material_by_id(EntityId id) const noexcept;

 private:
  Definition root_{0, "ROOT", "ROOT_MODEL"};
  std::unordered_map<EntityId, std::size_t> material_indices_;
  friend SkpModel build_model(struct RawParsed&&);
};
}  // namespace openskp
