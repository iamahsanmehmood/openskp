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
#include <openskp/observability.hpp>

namespace openskp {

using EntityId = std::int64_t;
using ByteBuffer = std::vector<std::uint8_t>;
using Vec3 = std::array<double, 3>;
using Color3 = std::array<std::uint8_t, 3>;
using Color4 = std::array<std::uint8_t, 4>;

/// 3D point coordinate in raw SketchUp space.
struct Vertex {
  /// Unique TLV entity ID.
  EntityId id{};
  /// X coordinate in raw model space (Z-up, inches).
  double x{};
  /// Y coordinate in raw model space (Z-up, inches).
  double y{};
  /// Z coordinate in raw model space (Z-up, inches).
  double z{};
};

/// Straight line segment connecting two vertices.
struct Edge {
  /// Unique TLV entity ID.
  EntityId id{};
  /// Start vertex ID.
  EntityId v1_id{};
  /// End vertex ID.
  EntityId v2_id{};
  /// Soft edge flag (suppresses edge rendering across coplanar faces).
  bool soft{};
  /// Smooth edge flag (enables vertex normal interpolation).
  bool smooth{};
  /// Hidden edge flag (explicit "Hide" bit on this edge).
  bool hidden{};
};

/// Oriented edge reference within a face loop.
struct CoEdge {
  /// Target edge ID.
  EntityId edge_id{};
  /// Orientation relative to edge (+1 = same direction, -1 = reversed).
  std::int64_t orientation{};
};

/// Planar polygon bounded by outer and inner (hole) edge loops.
struct Face {
  /// Unique TLV entity ID.
  EntityId id{};
  /// Polygonal loops (loops[0] = outer boundary, loops[1..N] = holes).
  std::vector<std::vector<CoEdge>> loops;
  /// Unit surface normal vector [nx, ny, nz].
  std::optional<Vec3> normal;
  /// Front-face material ID (references SkpModel::materials).
  std::optional<EntityId> material_id;
  /// Back-face material ID (references SkpModel::materials).
  std::optional<EntityId> back_material_id;
  /// Front-face 3x3 row-major UV transform matrix.
  std::optional<std::array<double, 9>> uv_transform;
  /// Back-face 3x3 row-major UV transform matrix.
  std::optional<std::array<double, 9>> uv_transform_back;
  /// The texture is PROJECTED (e.g. the Add Location terrain drape): its
  /// UVs run in the projection plane's frame, not the face frame.
  bool uv_projected{};
  /// Same for the face's back side.
  bool uv_projected_back{};
  /// Whether the face is hidden (SketchUp's "Hide" on this specific face,
  /// not a layer/tag visibility toggle).
  bool hidden{};
};

/// Organization layer (tag) for model elements.
struct Layer {
  /// Layer name (e.g. "Layer0", "Walls").
  std::string name;
  /// Display color RGB [0..255].
  Color3 color{200, 200, 200};
  /// Whether the layer's visibility is switched off. Only populated for
  /// legacy (pre-2021 MFC) files, where the byte is read directly from the
  /// layer record - modern (VFF) files derive layers from
  /// Layer_<name>-prefixed materials, which carry no visibility data, so
  /// this is always false there.
  bool hidden{};
};

/// Embedded texture image data and metadata.
struct Texture {
  /// Original image filename inside the SKP ZIP archive.
  std::string filename;
  /// Horizontal scale factor.
  double width{};
  /// Vertical scale factor.
  double height{};
  /// Raw image file bytes (PNG/JPG/BMP).
  std::optional<ByteBuffer> data;
  /// Save texture image bytes to disk.
  OPENSKP_EXPORT void save(const std::filesystem::path& path) const;
};

/// Surface material properties (color, transparency, texture).
struct Material {
  /// Material name (e.g. "FrontColor", "Layer_Layer0").
  std::string name;
  /// Color channels RGBA [0..255].
  Color4 color{200, 200, 200, 255};
  /// Opacity factor [0.0 = fully transparent, 1.0 = fully opaque].
  double transparency{1.0};
  /// Unique TLV entity ID.
  std::optional<EntityId> id;
  /// Embedded texture reference.
  std::optional<Texture> texture;
  /// Whether material uses colorization.
  bool colorized{};
  /// Colorize mode enum identifier.
  std::int32_t colorize_type{};
};

/// Bundled rendering style settings.
struct Style {
  /// Style name.
  std::string name;
  /// Default front face color.
  std::optional<Color3> front_color;
  /// Default back face color.
  std::optional<Color3> back_color;
};

/// Component or group placement instance within a definition.
struct Instance {
  /// Instance name.
  std::string name;
  /// Reference index to the target definition.
  std::optional<EntityId> ref_idx;
  /// GUID string of target definition.
  std::string guid;
  /// 4x4 column-major affine transformation matrix (16 floats).
  std::vector<double> matrix;
  /// This instance's own explicit layer override, or "" when it has
  /// none. An instance without an explicit override inherits its
  /// *placement's* layer, which can only be resolved once the scene
  /// graph is flattened - see build_scene()'s InstanceNode::layer for
  /// that resolved value.
  std::string layer;
  /// Arbitrary key/value dynamic attributes attached directly to this
  /// instance (SketchUp's Dynamic Components).
  std::map<std::string, std::string> properties;
  /// Instance material override ID.
  std::optional<EntityId> material_id;
  /// Whether the instance itself is hidden (SketchUp's "Hide" on this
  /// specific component/group placement, not a layer/tag visibility
  /// toggle).
  bool hidden{};
};

/// Reusable geometry container (component definition or group).
struct Definition {
  /// Unique TLV entity ID.
  EntityId id{};
  /// GUID string identifier.
  std::string guid;
  /// Definition name.
  std::string name;
  /// Vertices in local definition space.
  std::map<EntityId, Vertex> vertices;
  /// Edges in local definition space.
  std::map<EntityId, Edge> edges;
  /// Faces in local definition space.
  std::map<EntityId, Face> faces;
  /// Child component/group instances placed inside this definition.
  std::vector<Instance> instances;
  /// Always faces camera behavior flag.
  bool always_faces_camera{};
  /// Shadows face sun behavior flag.
  bool shadows_face_sun{};
  /// Image entity flag.
  bool is_image{};
};

/// Top-level model container holding all definitions, layers, materials, and styles.
class OPENSKP_EXPORT SkpModel {
 public:
  /// SketchUp version string (e.g. "{25.0.575}").
  std::string version{"unknown"};
  /// The model's unit-system string (e.g. "Millimeter"), read from
  /// meta/meta.dat in modern (VFF) files. Unset for legacy (pre-2021 MFC)
  /// files, which carry no equivalent container, or when the tag isn't
  /// found.
  std::optional<std::string> units;
  /// Geometry definitions map keyed by numeric entity ID.
  std::map<EntityId, Definition> definitions;
  /// Model layers.
  std::vector<Layer> layers;
  /// Model materials sequence.
  std::deque<Material> materials;
  /// Model rendering styles.
  std::vector<Style> styles;

  /// Access the implicit root model definition.
  Definition& root() noexcept;
  /// Access the implicit root model definition (const).
  const Definition& root() const noexcept;

  /// Look up a material by its TLV entity ID.
  Material* material_by_id(EntityId id) noexcept;
  /// Look up a material by its TLV entity ID (const).
  const Material* material_by_id(EntityId id) const noexcept;

  /// Enumerate all materials keyed by their TLV entity ID.
  std::map<EntityId, Material*> materials_by_id();
  /// Enumerate all materials keyed by their TLV entity ID (const).
  std::map<EntityId, const Material*> materials_by_id() const;

 private:
  Definition root_{0, "ROOT", "ROOT_MODEL"};
  std::unordered_map<EntityId, std::size_t> material_indices_;
  friend SkpModel build_model(struct RawParsed&&, const ParseOptions&);
};
}  // namespace openskp
