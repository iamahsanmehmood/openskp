#include <openskp/fragments_export.hpp>

#include <algorithm>
#include <array>
#include <cmath>
#include <fstream>
#include <functional>
#include <map>
#include <set>
#include <sstream>
#include <tuple>
#include <vector>

#include <flatbuffers/flatbuffers.h>
#include <miniz.h>

#include <openskp/_fragments_fb/index_generated.h>
#include <openskp/json_export.hpp>

namespace openskp {
namespace {

namespace fb = openskp::fragments_fb;

// Fragments' Shell uses `ushort` point indices by default; a shell with
// more points than this must use the wide BigShell encoding (`uint`
// indices) instead - confirmed against the real importer's own
// `points.length > ushortMaxValue` check.
constexpr std::size_t kUshortMax = 65535;

// Per-axis scale magnitudes within this of 1.0 are treated as exactly
// unit scale for cache-key rounding purposes - matches typical
// floating-point accumulation noise from matrix composition, not a
// meaningful tolerance for an actually-intended resize.
constexpr int kScaleRoundNdigits = 6;

using Mat4 = std::array<double, 16>;

constexpr Mat4 kIdentityMatrix{
    1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0,
};

// Column-major 4x4 multiply, a*b - same convention as instanced_scene.cpp's
// own internal mul4 (duplicated here rather than shared, matching this
// project's existing convention between closely-related-but-separate
// translation units).
Mat4 mat4_mul(const Mat4& a, const Mat4& b) {
  Mat4 out{};
  for (int col = 0; col < 4; ++col) {
    for (int row = 0; row < 4; ++row) {
      double s = 0.0;
      for (int k = 0; k < 4; ++k) s += a[static_cast<std::size_t>(k * 4 + row)] * b[static_cast<std::size_t>(col * 4 + k)];
      out[static_cast<std::size_t>(col * 4 + row)] = s;
    }
  }
  return out;
}

struct Leaf {
  const InstancedNode* node;
  Mat4 world;
};

// Walk the instanced scene's tree, accumulating each node's GLOBAL (world)
// transform, and return every node worth tracking as its own item - "leaf"
// in the geometry sense (carries a mesh) OR a real, named organizational
// wrapper with no geometry of its own (e.g. a SketchUp group like "W-2"
// that only exists to hold several separately-meshed parts). Mirrors
// openskp.export.fragments._collect_leaves exactly - see that function's
// own docstring for the full rationale.
void collect_leaves(const InstancedNode& node, const InstancedNode* root, const Mat4& parent_matrix,
                    std::vector<Leaf>& out) {
  const Mat4 world = mat4_mul(parent_matrix, node.matrix);
  const bool is_named_wrapper = (&node != root) && !node.name_is_generated;
  if (node.mesh_resource_id || is_named_wrapper) out.push_back(Leaf{&node, world});
  for (auto& child : node.children) collect_leaves(child, root, world, out);
}

struct Trs {
  std::array<double, 3> position;
  std::array<double, 3> x_dir;
  std::array<double, 3> y_dir;
  std::array<double, 3> scale;
  bool mirrored;
};

// Decompose a column-major 4x4 instance transform into
// (position, x_direction, y_direction, scale, mirrored) - a direct port of
// openskp.export.fragments._decompose_trs. See that function's own
// docstring for why mirroring is resolved this way.
Trs decompose_trs(const Mat4& m) {
  const std::array<double, 3> x_axis{m[0], m[1], m[2]};
  const std::array<double, 3> y_axis{m[4], m[5], m[6]};
  const std::array<double, 3> z_axis{m[8], m[9], m[10]};
  const std::array<double, 3> pos{m[12], m[13], m[14]};

  auto norm = [](const std::array<double, 3>& v) {
    double s = std::sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
    return s > 0.0 ? s : 1.0;
  };
  const double sx = norm(x_axis), sy = norm(y_axis), sz = norm(z_axis);

  const double det = x_axis[0] * (y_axis[1] * z_axis[2] - y_axis[2] * z_axis[1]) -
                     x_axis[1] * (y_axis[0] * z_axis[2] - y_axis[2] * z_axis[0]) +
                     x_axis[2] * (y_axis[0] * z_axis[1] - y_axis[1] * z_axis[0]);
  const bool mirrored = det < 0.0;

  std::array<double, 3> x_dir{x_axis[0] / sx, x_axis[1] / sx, x_axis[2] / sx};
  if (mirrored) {
    x_dir = {-x_dir[0], -x_dir[1], -x_dir[2]};
  }
  const std::array<double, 3> y_dir{y_axis[0] / sy, y_axis[1] / sy, y_axis[2] / sy};

  return Trs{pos, x_dir, y_dir, {sx, sy, sz}, mirrored};
}

struct BakedGeometry {
  std::vector<std::array<float, 3>> points;
  std::vector<std::array<std::uint32_t, 3>> triangles;
};

// Apply an instance's scale/mirror directly to a copy of its resource's
// LOCAL points and triangle winding, so the resulting geometry is correct
// when placed by a purely rigid Transform - a direct port of
// openskp.export.fragments._bake_primitive.
BakedGeometry bake_primitive(const LocalPrimitive& prim, const std::array<double, 3>& scale, bool mirrored) {
  const double sx = mirrored ? -scale[0] : scale[0];
  const double sy = scale[1], sz = scale[2];

  BakedGeometry out;
  const std::size_t n_verts = prim.positions.size() / 3;
  out.points.reserve(n_verts);
  for (std::size_t i = 0; i < n_verts; ++i) {
    out.points.push_back({
        static_cast<float>(prim.positions[i * 3] * sx),
        static_cast<float>(prim.positions[i * 3 + 1] * sy),
        static_cast<float>(prim.positions[i * 3 + 2] * sz),
    });
  }

  const std::size_t n_tris = prim.indices.size() / 3;
  out.triangles.reserve(n_tris);
  for (std::size_t i = 0; i < n_tris; ++i) {
    std::array<std::uint32_t, 3> tri{prim.indices[i * 3], prim.indices[i * 3 + 1], prim.indices[i * 3 + 2]};
    if (mirrored) std::swap(tri[1], tri[2]);
    out.triangles.push_back(tri);
  }
  return out;
}

// Round a (mirrored, scale) pair to a stable cache key - the mirror flag
// folds into the X component's sign, since bake_primitive only ever
// negates X for a mirrored instance. Matches
// openskp.export.fragments._scale_cache_key.
std::array<double, 3> scale_cache_key(bool mirrored, const std::array<double, 3>& scale) {
  const double mult = std::pow(10.0, kScaleRoundNdigits);
  auto rnd = [&](double v) { return std::round(v * mult) / mult; };
  return {rnd(mirrored ? -scale[0] : scale[0]), rnd(scale[1]), rnd(scale[2])};
}

}  // namespace

std::vector<std::uint8_t> to_fragments(const InstancedScene& scene, bool raw) {
  std::vector<Leaf> leaves;
  collect_leaves(scene.scene_hierarchy, &scene.scene_hierarchy, kIdentityMatrix, leaves);

  std::map<std::string, const InstancedMeshResource*> resource_by_id;
  for (auto& r : scene.mesh_resources) resource_by_id[r.id] = &r;

  ::flatbuffers::FlatBufferBuilder fbb(1024 * 64);

  // ---- Shells/Representations/Materials, built lazily as leaves are
  // walked below: keyed by (resource, primitive, baked scale) so every
  // placement sharing the same definition AND the same scale/mirror state
  // dedupes onto one Shell - only a genuinely distinct scale factor pays
  // for its own geometry copy. ----
  using ShellKey = std::tuple<std::string, int, double, double, double>;
  std::map<ShellKey, std::size_t> shell_key_to_index;
  std::vector<::flatbuffers::Offset<fb::Shell>> shell_offsets;
  std::vector<std::pair<std::array<float, 3>, std::array<float, 3>>> representation_bounds;
  std::map<int, std::size_t> material_key_to_index;
  std::vector<std::array<std::uint8_t, 4>> material_rgba;

  auto get_material_index = [&](int material_index) -> std::size_t {
    auto found = material_key_to_index.find(material_index);
    if (found != material_key_to_index.end()) return found->second;
    std::array<double, 4> base{1.0, 1.0, 1.0, 1.0};
    if (material_index >= 0 && static_cast<std::size_t>(material_index) < scene.gltf_materials.size()) {
      const auto& gm = scene.gltf_materials[static_cast<std::size_t>(material_index)];
      base = gm.pbr_metallic_roughness.base_color_factor;
    }
    std::array<std::uint8_t, 4> rgba{
        static_cast<std::uint8_t>(std::lround(base[0] * 255)),
        static_cast<std::uint8_t>(std::lround(base[1] * 255)),
        static_cast<std::uint8_t>(std::lround(base[2] * 255)),
        static_cast<std::uint8_t>(std::lround(base[3] * 255)),
    };
    const auto idx = material_rgba.size();
    material_rgba.push_back(rgba);
    material_key_to_index[material_index] = idx;
    return idx;
  };

  auto get_or_bake_shell = [&](const std::string& resource_id, int prim_idx, const LocalPrimitive& prim,
                               const std::array<double, 3>& scale, bool mirrored) -> std::size_t {
    const auto sk = scale_cache_key(mirrored, scale);
    const ShellKey key{resource_id, prim_idx, sk[0], sk[1], sk[2]};
    auto found = shell_key_to_index.find(key);
    if (found != shell_key_to_index.end()) return found->second;

    const auto baked = bake_primitive(prim, scale, mirrored);
    const bool is_big = baked.points.size() > kUshortMax;

    std::vector<::flatbuffers::Offset<fb::ShellProfile>> profile_offsets;
    std::vector<::flatbuffers::Offset<fb::BigShellProfile>> big_profile_offsets;
    for (auto& tri : baked.triangles) {
      if (is_big) {
        std::vector<std::uint32_t> idx{tri[0], tri[1], tri[2]};
        big_profile_offsets.push_back(fb::CreateBigShellProfileDirect(fbb, &idx));
      } else {
        std::vector<std::uint16_t> idx{static_cast<std::uint16_t>(tri[0]), static_cast<std::uint16_t>(tri[1]),
                                       static_cast<std::uint16_t>(tri[2])};
        profile_offsets.push_back(fb::CreateShellProfileDirect(fbb, &idx));
      }
    }

    std::vector<fb::FloatVector> points_struct;
    points_struct.reserve(baked.points.size());
    for (auto& p : baked.points) points_struct.emplace_back(p[0], p[1], p[2]);

    std::vector<std::uint16_t> face_ids(baked.triangles.size());
    for (std::size_t i = 0; i < face_ids.size(); ++i) face_ids[i] = static_cast<std::uint16_t>(i);

    std::vector<::flatbuffers::Offset<fb::ShellHole>> no_holes;
    std::vector<::flatbuffers::Offset<fb::BigShellHole>> no_big_holes;

    const auto shell_off = fb::CreateShellDirect(
        fbb, &profile_offsets, &no_holes, &points_struct, &big_profile_offsets, &no_big_holes,
        is_big ? fb::ShellType_BIG : fb::ShellType_NONE, &face_ids);

    const auto index = shell_offsets.size();
    shell_offsets.push_back(shell_off);

    std::array<float, 3> lo{std::numeric_limits<float>::infinity(), std::numeric_limits<float>::infinity(),
                            std::numeric_limits<float>::infinity()};
    std::array<float, 3> hi{-std::numeric_limits<float>::infinity(), -std::numeric_limits<float>::infinity(),
                            -std::numeric_limits<float>::infinity()};
    for (auto& p : baked.points) {
      for (int k = 0; k < 3; ++k) {
        if (p[static_cast<std::size_t>(k)] < lo[static_cast<std::size_t>(k)]) lo[static_cast<std::size_t>(k)] = p[static_cast<std::size_t>(k)];
        if (p[static_cast<std::size_t>(k)] > hi[static_cast<std::size_t>(k)]) hi[static_cast<std::size_t>(k)] = p[static_cast<std::size_t>(k)];
      }
    }
    representation_bounds.emplace_back(lo, hi);

    shell_key_to_index[key] = index;
    return index;
  };

  // ---- Model-level items + geometry samples ----
  std::vector<std::uint32_t> local_ids;
  std::vector<std::string> categories;
  std::vector<std::string> names;
  std::vector<std::string> guids;
  std::vector<std::string> generated_name_guids;
  std::vector<std::size_t> sample_material;
  std::vector<std::size_t> sample_representation;
  std::vector<std::uint32_t> meshes_items;
  std::vector<Trs> global_transform_data;
  std::map<const InstancedNode*, std::uint32_t> item_index_by_node;

  // Real-world SketchUp files can carry a non-unique per-instance GUID
  // (SketchUp's native Copy/Move+Copy/Array duplicates an instance's
  // attribute dictionaries - and whatever GUID a plugin wrote into one -
  // verbatim). See the identical, more fully-documented fix in Python's
  // export/fragments.py (openskp#290) for the full rationale; ported here
  // unchanged: the first instance to claim a real GUID keeps it, any later
  // instance sharing that value falls back to a synthetic one instead.
  std::set<std::string> seen_guids;

  for (std::size_t item_index = 0; item_index < leaves.size(); ++item_index) {
    const auto& leaf = leaves[item_index];
    const auto& node = *leaf.node;
    const InstancedMeshResource* res = nullptr;
    if (node.mesh_resource_id) {
      auto found = resource_by_id.find(*node.mesh_resource_id);
      if (found == resource_by_id.end()) continue;  // real error case: leaf declared a resource that never baked
      res = found->second;
    }

    local_ids.push_back(static_cast<std::uint32_t>(item_index));
    categories.push_back(node.layer.empty() ? "Layer0" : node.layer);
    names.push_back(node.name);

    const std::string raw_guid = node.guid;
    const std::string item_guid =
        (!raw_guid.empty() && !seen_guids.count(raw_guid)) ? raw_guid : ("openskp-" + std::to_string(item_index));
    seen_guids.insert(item_guid);
    guids.push_back(item_guid);
    if (node.name_is_generated) generated_name_guids.push_back(item_guid);
    item_index_by_node[&node] = static_cast<std::uint32_t>(item_index);

    if (res != nullptr) {
      const auto trs = decompose_trs(leaf.world);
      for (std::size_t prim_idx = 0; prim_idx < res->primitives.size(); ++prim_idx) {
        const auto& prim = res->primitives[prim_idx];
        sample_material.push_back(get_material_index(static_cast<int>(prim.material_index)));
        sample_representation.push_back(
            get_or_bake_shell(*node.mesh_resource_id, static_cast<int>(prim_idx), prim, trs.scale, trs.mirrored));
        meshes_items.push_back(static_cast<std::uint32_t>(item_index));
        global_transform_data.push_back(trs);
      }
    }
  }

  const std::size_t n_samples = sample_material.size();

  const auto shells_vec = fbb.CreateVector(shell_offsets);

  std::vector<fb::Material> material_structs;
  material_structs.reserve(material_rgba.size());
  for (auto& rgba : material_rgba) {
    material_structs.emplace_back(rgba[0], rgba[1], rgba[2], rgba[3], fb::RenderedFaces_ONE, fb::Stroke_DEFAULT);
  }
  const auto materials_vec = fbb.CreateVectorOfStructs(material_structs);

  std::vector<fb::Representation> representation_structs;
  representation_structs.reserve(representation_bounds.size());
  for (std::size_t i = 0; i < representation_bounds.size(); ++i) {
    const auto& [lo, hi] = representation_bounds[i];
    representation_structs.emplace_back(static_cast<std::uint32_t>(i), fb::BoundingBox(fb::FloatVector(lo[0], lo[1], lo[2]), fb::FloatVector(hi[0], hi[1], hi[2])),
                                        fb::RepresentationClass_SHELL);
  }
  const auto representations_vec = fbb.CreateVectorOfStructs(representation_structs);

  std::vector<fb::Sample> sample_structs;
  sample_structs.reserve(n_samples);
  for (std::size_t i = 0; i < n_samples; ++i) {
    sample_structs.emplace_back(static_cast<std::uint32_t>(i), static_cast<std::uint32_t>(sample_material[i]),
                                static_cast<std::uint32_t>(sample_representation[i]), 0u);
  }
  const auto samples_vec = fbb.CreateVectorOfStructs(sample_structs);

  const auto meshes_items_vec = fbb.CreateVector(meshes_items);

  std::vector<fb::Transform> global_transform_structs;
  global_transform_structs.reserve(global_transform_data.size());
  for (auto& trs : global_transform_data) {
    global_transform_structs.emplace_back(
        fb::DoubleVector(trs.position[0], trs.position[1], trs.position[2]),
        fb::FloatVector(static_cast<float>(trs.x_dir[0]), static_cast<float>(trs.x_dir[1]), static_cast<float>(trs.x_dir[2])),
        fb::FloatVector(static_cast<float>(trs.y_dir[0]), static_cast<float>(trs.y_dir[1]), static_cast<float>(trs.y_dir[2])));
  }
  const auto global_transforms_vec = fbb.CreateVectorOfStructs(global_transform_structs);

  // One shared identity local transform - no per-geometry sub-offset is
  // needed since every primitive's points are already in the resource's
  // own local space (now with scale/mirror already baked in).
  std::vector<fb::Transform> local_transform_structs{
      fb::Transform(fb::DoubleVector(0, 0, 0), fb::FloatVector(1, 0, 0), fb::FloatVector(0, 1, 0)),
  };
  const auto local_transforms_vec = fbb.CreateVectorOfStructs(local_transform_structs);

  std::vector<::flatbuffers::Offset<fb::CircleExtrusion>> no_circle_extrusions;
  const auto circle_extrusions_vec = fbb.CreateVector(no_circle_extrusions);

  const fb::Transform coordinates(fb::DoubleVector(0, 0, 0), fb::FloatVector(1, 0, 0), fb::FloatVector(0, 1, 0));

  const auto meshes_off =
      fb::CreateMeshes(fbb, &coordinates, meshes_items_vec, samples_vec, representations_vec, materials_vec,
                       circle_extrusions_vec, shells_vec, local_transforms_vec, global_transforms_vec);

  const auto categories_vec = fbb.CreateVectorOfStrings(categories);
  const auto local_ids_vec = fbb.CreateVector(local_ids);
  const auto guid_str = fbb.CreateString("00000000-0000-0000-0000-000000000000");
  const auto guids_vec = fbb.CreateVectorOfStrings(guids);
  const auto guids_items_vec = fbb.CreateVector(local_ids);

  // One Attribute per tracked item (same order as local_ids/categories),
  // carrying the item's real display name encoded as a `["Name", value,
  // "STRING"]` JSON triple - the exact convention the real IfcImporter
  // uses for its own "Name" attribute. Matches
  // openskp.export.fragments.to_fragments exactly.
  std::vector<::flatbuffers::Offset<fb::Attribute>> attribute_offsets;
  attribute_offsets.reserve(names.size());
  for (auto& name : names) {
    std::vector<::flatbuffers::Offset<::flatbuffers::String>> data_offsets;
    if (!name.empty()) {
      JsonValue::Array triple;
      triple.push_back(JsonValue("Name"));
      triple.push_back(JsonValue(name));
      triple.push_back(JsonValue("STRING"));
      data_offsets.push_back(fbb.CreateString(to_json_string(JsonValue(triple), 0)));
    }
    attribute_offsets.push_back(fb::CreateAttributeDirect(fbb, &data_offsets));
  }
  const auto attributes_vec = fbb.CreateVector(attribute_offsets);

  // The source file's own per-layer visibility has no equivalent field
  // anywhere in the Fragments schema itself - `metadata` is the schema's
  // own general-purpose "JSON string for generic data about the file"
  // field, same sidecar convention Python's exporter uses.
  JsonValue::Object metadata_obj;
  JsonValue::Object layer_hidden_obj;
  for (auto& [layer, hidden] : scene.layer_hidden) layer_hidden_obj.emplace_back(layer, JsonValue(hidden));
  metadata_obj.emplace_back("layer_hidden", JsonValue(layer_hidden_obj));
  JsonValue::Array generated_arr;
  for (auto& g : generated_name_guids) generated_arr.push_back(JsonValue(g));
  metadata_obj.emplace_back("generated_name_guids", JsonValue(generated_arr));
  const auto metadata_off = fbb.CreateString(to_json_string(JsonValue(metadata_obj), 0));

  // Spatial structure: one SpatialStructure node per InstancedNode in the
  // ORIGINAL tree (not just leaves), so real component nesting comes
  // through, not just a flat list. Matches
  // openskp.export.fragments.to_fragments's build_spatial_node exactly.
  std::function<::flatbuffers::Offset<fb::SpatialStructure>(const InstancedNode&)> build_spatial_node;
  build_spatial_node = [&](const InstancedNode& node) -> ::flatbuffers::Offset<fb::SpatialStructure> {
    std::vector<::flatbuffers::Offset<fb::SpatialStructure>> child_offsets;
    child_offsets.reserve(node.children.size());
    for (auto& child : node.children) child_offsets.push_back(build_spatial_node(child));
    const auto children_vec = fbb.CreateVector(child_offsets);

    ::flatbuffers::Offset<::flatbuffers::String> category_off = 0;
    if (!node.layer.empty()) category_off = fbb.CreateString(node.layer);

    ::flatbuffers::Optional<std::uint32_t> local_id = ::flatbuffers::nullopt;
    auto found = item_index_by_node.find(&node);
    if (found != item_index_by_node.end()) local_id = found->second;

    return fb::CreateSpatialStructure(fbb, local_id, category_off, children_vec);
  };
  const auto root_spatial = build_spatial_node(scene.scene_hierarchy);

  const auto model_off = fb::CreateModel(fbb, metadata_off, guids_vec, guids_items_vec,
                                         static_cast<std::uint32_t>(local_ids.size()), local_ids_vec, categories_vec,
                                         meshes_off, attributes_vec, /*relations*/ 0, /*relations_items*/ 0, guid_str,
                                         root_spatial);

  fbb.Finish(model_off, "0001");

  const std::uint8_t* buf = fbb.GetBufferPointer();
  const std::size_t size = fbb.GetSize();

  if (raw) {
    return std::vector<std::uint8_t>(buf, buf + size);
  }

  mz_ulong bound = mz_compressBound(static_cast<mz_ulong>(size));
  std::vector<std::uint8_t> compressed(bound);
  mz_ulong compressed_len = bound;
  const int rc = mz_compress2(compressed.data(), &compressed_len, buf, static_cast<mz_ulong>(size), MZ_DEFAULT_LEVEL);
  if (rc != MZ_OK) {
    throw std::runtime_error("Fragments export: zlib compression failed (miniz error " + std::to_string(rc) + ")");
  }
  compressed.resize(compressed_len);
  return compressed;
}

void export_fragments(const InstancedScene& scene, const std::filesystem::path& output_path, bool raw) {
  std::filesystem::create_directories(output_path.parent_path());
  const auto data = to_fragments(scene, raw);
  std::ofstream out(output_path, std::ios::binary);
  out.write(reinterpret_cast<const char*>(data.data()), static_cast<std::streamsize>(data.size()));
}

}  // namespace openskp
