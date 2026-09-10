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
  bool uv_projected{};
  bool uv_projected_back{};
  bool hidden{};
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
  // The "dynamic_attributes" dictionary only (SketchUp's own Dynamic
  // Components data) - the same backward-compatible view Python's
  // extract_dynamic_properties() exposes as `properties`.
  std::map<std::string, std::string> properties;
  // Every OTHER attribute dictionary this instance carries, keyed by the
  // dictionary's own declared name (VFF tag B436) - a third-party plugin
  // (steel-detailing tool, etc.) commonly attaches its own richer
  // per-instance data under its own dictionary name instead of
  // dynamic_attributes. SU_InstanceSet (SketchUp's own always-present,
  // always-empty boilerplate) is excluded, same as dynamic_attributes.
  std::map<std::string, std::map<std::string, std::string>> attribute_dicts;
  bool hidden{};
};

struct GeometryBuilder {
  std::map<EntityId, Vec3> vertices;
  std::map<EntityId, std::pair<std::optional<EntityId>, std::optional<EntityId>>> edges;
  std::map<EntityId, int> edge_flags;
  std::map<EntityId, RawFace> faces;
  std::vector<RawInstance> instances;
  std::vector<SectionPlane> section_planes;
  std::vector<TextEntity> texts;
  std::vector<Dimension> dimensions;
};

struct RawDefinition {
  std::string guid;
  std::string name;
  bool always_faces_camera{};
  bool shadows_face_sun{};
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
  int a{255};
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

struct RawPage {
  std::string name;
  std::optional<Vec3> eye;
  std::optional<Vec3> target;
  std::optional<Vec3> up;
  double fov{35.0};
  bool parallel{};
  double ortho_height{};
  std::vector<EntityId> hidden_layer_ids;
};

struct RawDimension {
  Vec3 a{};
  Vec3 b{};
  double offset{};
  std::optional<Vec3> plane_x;
  std::optional<Vec3> normal;
  std::string text;
};

struct RawParsed {
  std::string version{"unknown"};
  // The model's unit-system string (e.g. "Millimeter"), read from
  // meta/meta.dat. Unset for legacy files or when the tag isn't found.
  std::optional<std::string> units;
  std::map<std::string, Color3> layer_colors;
  // Layer names in the order they were first encountered in the source
  // file (material.xml archive-entry order for VFF, slot-scan order for
  // legacy) - layer_colors above is a std::map (sorted by name), so this
  // is the only place file order survives; model.cpp's layer-building
  // loop iterates this instead of layer_colors directly. Mirrors
  // Python's plain dict for the same field, which preserves insertion
  // order natively.
  std::vector<std::string> layer_order;
  // Modern (VFF) files derive layer COLOR from Layer_<name>-prefixed
  // materials, which carry no visibility flag of their own - real
  // visibility comes from the model.dat layer manager's own 8E3C byte
  // (see geometry.cpp's collect_layers), read here into this same map.
  std::map<std::string, bool> layer_hidden;
  std::map<EntityId, std::string> layer_id_to_name;
  std::vector<RawPage> pages;
  std::vector<RawDimension> dimensions;
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
std::optional<std::string> read_meta_units(const ByteBuffer&);
std::string extract_version(const ByteBuffer&);
bool valid_header(const ByteBuffer&);
bool is_legacy(const ByteBuffer&);
bool legacy_instance_has_guid(const std::string&, std::optional<int>);

/// Result of `find_count_after_v20_filler`: the recovered count and the
/// offset just past it.
struct V20FillerHit {
  std::uint32_t count;
  std::size_t next;
};

std::optional<V20FillerHit> find_count_after_v20_filler(const ByteBuffer&, std::size_t,
                                                        std::uint32_t);
RawParsed full_parse(const ByteBuffer&, const ParseOptions&);
std::string decode_xml_entities(const std::string&);
RawParsed parse_legacy(const ByteBuffer&, const ParseOptions&);
void collect_geometry(const std::vector<TlvNode>&, GeometryBuilder&);
void collect_layers(const std::vector<TlvNode>&, std::map<EntityId, std::string>&,
                    std::map<std::string, bool>&);
void collect_material_ids(const std::vector<TlvNode>&, std::map<EntityId, std::string>&);
void collect_definitions(const std::vector<TlvNode>&, std::map<EntityId, RawDefinition>&);
SkpModel build_model(RawParsed&&, const ParseOptions& = {});
Scene build_scene_raw(RawParsed&&, const ParseOptions&);
InstancedScene build_instanced_scene_raw(RawParsed&&, const ParseOptions&);
std::array<double, 3> transform_point(const std::vector<double>&, const std::array<double, 3>&);
std::array<double, 3> transform_normal(const std::vector<double>&, const std::array<double, 3>&);
double transform_determinant(const std::vector<double>&);
std::vector<double> multiply_matrices(const std::vector<double>&, const std::vector<double>&);
void scan_vertex_positions(const TlvNode&, std::map<std::string, Vec3>&);
void scan_instance_transforms(const TlvNode&, std::map<std::string, std::vector<double>>&);
std::vector<RawDimension> parse_dimensions(const ByteBuffer&, const std::map<std::string, Vec3>&,
                                           const std::map<std::string, std::vector<double>>&);
const TlvNode* find_page_node(const TlvNode&);
std::vector<RawPage> parse_pages(const TlvNode*);

struct EarPoint {
  double x{};
  double y{};
  EntityId id{};
};

std::vector<std::array<EntityId, 3>> earcut_2d(std::vector<std::vector<EarPoint>> loops);
void emit_log(const ParseOptions&, LogLevel, const std::string&);
void emit_progress(const ParseOptions&, ParseStage, std::uint64_t, std::uint64_t);
}  // namespace openskp
