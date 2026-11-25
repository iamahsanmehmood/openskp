#pragma once

#include <array>
#include <cmath>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <string>
#include <tuple>
#include <vector>

#include <openskp/scene.hpp>

#include "internal.hpp"

namespace openskp {

// Local-space face grouping, shared by the baked (scene.cpp) and instanced
// (instanced_scene.cpp) scene builders.
//
// Extracted from scene.cpp unchanged (openskp#200, mirroring TypeScript's
// face-groups.ts): a definition's faces are grouped by resolved (color,
// double_sided, texture) identity in DEFINITION-LOCAL space (inches,
// SketchUp Z-up) - exactly what the baked builder assembles just before
// applying an instance's world matrix, and exactly what the instanced
// builder keeps local and puts on the node instead. Keeping one
// implementation is what makes the two paths agree on triangulation, UV
// seams, normals and front/back handling by construction rather than by
// parallel maintenance.
//
// An unpainted face side inherits fallback_material (texture image, tile
// size and opacity included) from an enclosing painted component or group;
// when nothing up the instance path is painted, its color falls back to
// fallback_color / fallback_back_color (the model's face colors or the
// effective layer's color). Color includes a 4th (alpha/transparency)
// component here, unlike the 3-component RGB key in Python/TypeScript/.NET/Dart.

// Color key: (r, g, b, alpha-from-transparency). Richer than the other
// three ports' 3-component RGB key - an existing C++ characteristic,
// preserved rather than changed by this extraction.
using FaceColorKey = std::array<int, 4>;

struct GroupKey {
  FaceColorKey color;
  bool double_sided{};
  // The texture is part of the identity, not just the color: two
  // different images can average to the same RGB (real files do this),
  // and keying on color alone would merge them into one material and
  // lose one of the images.
  std::optional<std::size_t> texture_index;
  bool unpainted_back{};

  bool operator<(const GroupKey& other) const {
    return std::tie(color, double_sided, texture_index, unpainted_back) <
           std::tie(other.color, other.double_sided, other.texture_index, other.unpainted_back);
  }
};

// A vertex is keyed by (source vertex id, u, v): UVs are inherently
// per-face, so a vertex position shared by two faces that disagree on
// texture mapping must become two distinct output vertices (glTF requires
// position/normal/uv aligned per index).
using FaceVKey = std::tuple<EntityId, double, double>;

struct Group {
  std::vector<Vec3> verts;
  std::vector<std::array<float, 2>> uvs;
  std::vector<std::array<size_t, 3>> tris;
  std::vector<std::uint32_t> back_tris;
  std::map<FaceVKey, size_t> map;
  std::map<FaceVKey, Vec3> normals;
};

struct FaceLayerSummary {
  std::optional<std::string> uniform_layer;
  std::optional<std::string> majority_layer;
};

std::shared_ptr<RawMaterial> find_material(const RawParsed& parsed, std::optional<EntityId> id);
std::optional<FaceColorKey> material_color(const std::shared_ptr<RawMaterial>& material);
FaceColorKey default_color(const RawParsed& parsed, const std::string& layer);
FaceLayerSummary summarize_face_layers(const RawParsed& parsed, const GeometryBuilder& b,
                                       const ParseOptions& options = {});
std::optional<std::pair<FaceColorKey, FaceColorKey>> unpainted_style_colors(
    const RawParsed& parsed, const std::string& unpainted_faces);
std::optional<std::string> sniff_image_mime(const ByteBuffer& data);
std::optional<std::string> original_image_mime(const ByteBuffer& data);
void warn_skipped_texture(const ParseOptions& options,
                          std::set<std::pair<std::string, std::string>>& warned,
                          const std::shared_ptr<RawMaterial>& material);

std::function<std::optional<std::size_t>(const std::shared_ptr<RawMaterial>&)>
make_texture_index_for(std::vector<SceneTexture>& textures, const ParseOptions& options);

struct FilteredGltfTextures {
  std::vector<const SceneTexture*> textures;
  std::vector<std::optional<int>> remap;
};

FilteredGltfTextures filter_gltf_textures(
    const std::vector<SceneTexture>& raw_textures, bool embed,
    const std::function<void(LogLevel, std::string_view)>& log);

inline double srgb_to_linear(double c) {
  if (c <= 0.0) return 0.0;
  if (c >= 1.0) return 1.0;
  return c <= 0.04045 ? c / 12.92 : std::pow((c + 0.055) / 1.055, 2.4);
}

// Everything build_local_face_groups needs from its caller that isn't the
// builder itself.
struct FaceGroupContext {
  std::function<std::optional<std::size_t>(const std::shared_ptr<RawMaterial>&)> texture_index_for;
  // Color an unpainted face side falls back to (already resolved by the
  // caller: the instance's inherited paint color, or, when nothing is
  // inherited, the model's front face color or the layer's color).
  FaceColorKey fallback_color{};
  // Same as fallback_color for a face's back side where it differs (the
  // model's back face color). nullopt means fallback_color.
  std::optional<FaceColorKey> fallback_back_color;
  // Material painted on the enclosing group/component, inherited whole
  // (texture, tile size, transparency) by any unpainted face side.
  std::shared_ptr<RawMaterial> fallback_material;
};

// Group a definition's faces by resolved material identity, in local
// space.
//
// A face whose front/back resolve to the SAME color is emitted once with
// double_sided set; a face whose sides genuinely differ is emitted as two
// single-sided triangle sets (one normal-wound front, one reverse-wound
// back) so each side keeps its own color.
std::map<GroupKey, Group> build_local_face_groups(const RawParsed& parsed, const GeometryBuilder& b,
                                                  const FaceGroupContext& ctx);

}  // namespace openskp
