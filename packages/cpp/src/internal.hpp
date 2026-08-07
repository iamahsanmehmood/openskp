#pragma once

#include <chrono>
#include <cstring>
#include <limits>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <string>
#include <unordered_map>
#include <vector>

#include <openskp/openskp.hpp>

namespace openskp {
struct TlvNode {
  std::uint64_t offset{};
  std::uint64_t size{};
  std::string tag;
  std::vector<TlvNode> children;
  ByteBuffer payload;
};

struct RawFace {
  std::vector<std::vector<CoEdge>> loops;
  Vec3 normal{0, 0, 1};
  std::optional<EntityId> material_id;
  std::optional<EntityId> back_material_id;
  std::optional<std::array<double, 9>> uv_transform;
  std::optional<std::array<double, 9>> uv_transform_back;
};

struct RawInstance {
  std::uint64_t offset{};
  std::string ref_guid;
  std::string name;
  std::optional<EntityId> ref_idx;
  std::optional<EntityId> material_id;
  std::vector<double> matrix;
  std::vector<TlvNode> children;
  std::string layer;
  std::map<std::string, std::string> properties;
};

struct GeometryBuilder {
  std::map<EntityId, Vec3> vertices;
  std::map<EntityId, std::pair<std::optional<EntityId>, std::optional<EntityId>>> edges;
  std::map<EntityId, int> edge_flags;
  std::map<EntityId, RawFace> faces;
  std::vector<RawInstance> instances;
};

struct RawDefinition {
  std::string guid;
  std::string name;
  bool always_faces_camera{};
  bool is_image{};
  GeometryBuilder builder;
};

struct RawTexture {
  std::string filename;
  double x_scale{};
  double y_scale{};
  std::optional<ByteBuffer> data;
};

struct RawMaterial {
  std::string name;
  int r{128};
  int g{128};
  int b{128};
  double transparency{1};
  bool colorized{};
  int colorize_type{};
  std::optional<RawTexture> texture;
};

struct RawStyle {
  std::string name;
  std::optional<Color3> front_color;
  std::optional<Color3> back_color;
};

struct RawParsed {
  std::string version{"unknown"};
  std::map<std::string, Color3> layer_colors;
  std::map<EntityId, std::string> layer_id_to_name;
  std::map<EntityId, std::string> material_id_to_name;
  std::map<std::string, std::shared_ptr<RawMaterial>> materials;
  std::map<std::string, std::shared_ptr<RawMaterial>> materials_by_folder;
  std::vector<RawStyle> styles;
  std::map<EntityId, RawDefinition> definitions;
  RawDefinition root{"ROOT", "ROOT_MODEL"};
};

std::uint16_t read_u16(const ByteBuffer&, std::size_t);
std::uint32_t read_u32(const ByteBuffer&, std::size_t);
std::int32_t read_i32(const ByteBuffer&, std::size_t);
double read_f64(const ByteBuffer&, std::size_t);
std::uint64_t parse_varint(const ByteBuffer&, std::size_t, std::size_t);
std::vector<TlvNode> parse_tlv_recursive(const ByteBuffer&, std::size_t, std::size_t);
std::vector<std::pair<std::string, ByteBuffer>> parse_flat(const ByteBuffer&);
std::string extract_version(const ByteBuffer&);
bool valid_header(const ByteBuffer&);
bool is_legacy(const ByteBuffer&);
RawParsed full_parse(const ByteBuffer&, const ParseOptions&);
RawParsed parse_legacy(const ByteBuffer&, const ParseOptions&);
void collect_geometry(const std::vector<TlvNode>&, GeometryBuilder&);
void collect_layers(const std::vector<TlvNode>&, std::map<EntityId, std::string>&);
void collect_material_ids(const std::vector<TlvNode>&, std::map<EntityId, std::string>&);
void collect_definitions(const std::vector<TlvNode>&, std::map<EntityId, RawDefinition>&);
SkpModel build_model(RawParsed&&);
Scene build_scene_raw(RawParsed&&, const ParseOptions&);
std::array<double, 3> transform_point(const std::vector<double>&, const std::array<double, 3>&);
std::array<double, 3> transform_normal(const std::vector<double>&, const std::array<double, 3>&);
double transform_determinant(const std::vector<double>&);
std::vector<double> multiply_matrices(const std::vector<double>&, const std::vector<double>&);

struct EarPoint {
  double x{};
  double y{};
  EntityId id{};
};

std::vector<std::array<EntityId, 3>> earcut_2d(std::vector<std::vector<EarPoint>> loops);
void emit_log(const ParseOptions&, LogLevel, const std::string&);
void emit_progress(const ParseOptions&, ParseStage, std::uint64_t, std::uint64_t);
}  // namespace openskp
