#include <algorithm>
#include <cmath>
#include <set>
#include <sstream>
#include <tuple>
#include <utility>

#include "internal.hpp"

namespace openskp {
namespace {
using Key = std::array<int, 4>;

struct GroupKey {
  Key color;
  bool double_sided{};
  // The texture is part of the identity, not just the color: two
  // different images can average to the same RGB (real files do this),
  // and keying on color alone would merge them into one material and
  // lose one of the images.
  std::optional<std::size_t> texture_index;

  bool operator<(const GroupKey& other) const {
    return std::tie(color, double_sided, texture_index) <
           std::tie(other.color, other.double_sided, other.texture_index);
  }
};

// A vertex is keyed by (source vertex id, u, v): UVs are inherently
// per-face, so a vertex position shared by two faces that disagree on
// texture mapping must become two distinct output vertices (glTF requires
// position/normal/uv aligned per index).
using VKey = std::tuple<EntityId, double, double>;

struct Group {
  std::vector<Vec3> verts;
  std::vector<std::array<float, 2>> uvs;
  std::vector<std::array<size_t, 3>> tris;
  std::map<VKey, size_t> map;
  std::map<VKey, Vec3> normals;
};

std::shared_ptr<RawMaterial> find_material(const RawParsed& parsed, std::optional<EntityId> id) {
  if (!id) return nullptr;
  auto name = parsed.material_id_to_name.find(*id);
  if (name == parsed.material_id_to_name.end()) return nullptr;
  auto direct = parsed.materials.find(name->second);
  if (direct != parsed.materials.end()) return direct->second;
  auto folder = parsed.materials_by_folder.find(name->second);
  if (folder != parsed.materials_by_folder.end()) return folder->second;
  return nullptr;
}

std::optional<Key> material_color(const std::shared_ptr<RawMaterial>& material) {
  if (!material) return {};
  return Key{material->r, material->g, material->b,
             static_cast<int>(std::lround(std::clamp(material->transparency, 0.0, 1.0) * 255.0))};
}

// Identifies an image's MIME type from its magic bytes. Returns nullopt
// for anything glTF cannot carry (glTF only allows PNG and JPEG).
std::optional<std::string> sniff_image_mime(const ByteBuffer& data) {
  if (data.size() >= 3 && data[0] == 0xff && data[1] == 0xd8 && data[2] == 0xff) {
    return "image/jpeg";
  }
  if (data.size() >= 8 && data[0] == 0x89 && data[1] == 0x50 && data[2] == 0x4e &&
      data[3] == 0x47 && data[4] == 0x0d && data[5] == 0x0a && data[6] == 0x1a &&
      data[7] == 0x0a) {
    return "image/png";
  }
  return std::nullopt;
}

std::pair<double, double> tile_size(const std::shared_ptr<RawMaterial>& material) {
  double w = 1.0, h = 1.0;
  if (material && material->texture) {
    if (material->texture->x_scale > 1e-9) w = material->texture->x_scale;
    if (material->texture->y_scale > 1e-9) h = material->texture->y_scale;
  }
  return {w, h};
}

// Inverse of a row-major 3x3 matrix, via the cofactor/adjugate method.
std::array<double, 9> invert_3x3(const std::array<double, 9>& m) {
  double a = m[0], b = m[1], c = m[2];
  double d = m[3], e = m[4], f = m[5];
  double g = m[6], h = m[7], i = m[8];
  double det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g);
  if (std::abs(det) < 1e-12) return {1, 0, 0, 0, 1, 0, 0, 0, 1};
  double inv_det = 1.0 / det;
  return {
      (e * i - f * h) * inv_det, (c * h - b * i) * inv_det, (b * f - c * e) * inv_det,
      (f * g - d * i) * inv_det, (a * i - c * g) * inv_det, (c * d - a * f) * inv_det,
      (d * h - e * g) * inv_det, (b * g - a * h) * inv_det, (a * e - b * d) * inv_det,
  };
}

// Face-plane basis vectors (xr, yr) for UV projection, from a face normal.
std::pair<Vec3, Vec3> face_uv_basis(const Vec3& n) {
  double cx = -n[1], cy = n[0];
  double clen = std::sqrt(cx * cx + cy * cy);
  if (clen < 1e-9) {
    return {Vec3{1, 0, 0}, Vec3{0, n[2] >= 0 ? 1.0 : -1.0, 0}};
  }
  Vec3 xr{cx / clen, cy / clen, 0};
  Vec3 yr{n[1] * xr[2] - n[2] * xr[1], n[2] * xr[0] - n[0] * xr[2], n[0] * xr[1] - n[1] * xr[0]};
  return {xr, yr};
}

// UV of point p (inches, local/object space) on a face with the given
// plane basis, per-face uv_transform (or nullopt for the default
// projection), and material tile size (inches).
std::pair<double, double> compute_face_uv(const Vec3& p, const Vec3& xr, const Vec3& yr,
                                          const std::optional<std::array<double, 9>>& uv_transform,
                                          double tile_w, double tile_h) {
  double px = p[0] * xr[0] + p[1] * xr[1] + p[2] * xr[2];
  double py = p[0] * yr[0] + p[1] * yr[1] + p[2] * yr[2];
  if (!uv_transform) return {px / tile_w, py / tile_h};
  auto inv = invert_3x3(*uv_transform);
  double u = px * inv[0] + py * inv[3] + inv[6];
  double v = px * inv[1] + py * inv[4] + inv[7];
  double q = px * inv[2] + py * inv[5] + inv[8];
  if (std::abs(q) < 1e-12) q = 1.0;
  return {(u / q) / tile_w, (v / q) / tile_h};
}

Key default_color(const RawParsed& parsed, const std::string& layer) {
  auto found = parsed.layer_colors.find(layer);
  auto color = found == parsed.layer_colors.end() ? Color3{136, 136, 136} : found->second;
  return {color[0], color[1], color[2], 255};
}

std::vector<EntityId> loop_vertices(const std::vector<CoEdge>& loop, const GeometryBuilder& b) {
  std::vector<EntityId> v;
  for (auto& c : loop) {
    auto i = b.edges.find(c.edge_id);
    if (i == b.edges.end()) continue;
    auto id = c.orientation == 1 ? i->second.first : i->second.second;
    if (id && (v.empty() || v.back() != *id)) v.push_back(*id);
  }
  if (v.size() > 1 && v.front() == v.back()) v.pop_back();
  return v;
}

std::string safe(std::string s) {
  for (size_t p = 0; (p = s.find(" / ", p)) != std::string::npos;) s.replace(p, 3, "__");
  std::replace(s.begin(), s.end(), ' ', '_');
  if (s.size() > 80) s.resize(80);
  return s;
}
}  // namespace

Scene build_scene_raw(RawParsed&& p, const ParseOptions& o) {
  Scene scene;
  scene.scene_hierarchy = {"ROOT", "ROOT_MODEL", "Layer0", {0, 0, 0}, {}, {}};
  emit_log(o, LogLevel::information,
           "Building scene: " + std::to_string(p.definitions.size()) + " definitions available");
  std::map<GroupKey, size_t> materials;
  size_t mesh_counter = 0, instance_counter = 0;
  std::set<EntityId> active;

  // Textures deduplicated by bytes: the same image routinely backs
  // several materials, and re-embedding it per material would multiply
  // the export size for nothing.
  std::map<std::string, std::size_t> texture_index_by_key;
  auto texture_index_for =
      [&](const std::shared_ptr<RawMaterial>& mat) -> std::optional<std::size_t> {
    if (!mat || !mat->texture || !mat->texture->data || mat->texture->data->empty()) {
      return std::nullopt;
    }
    const auto& data = *mat->texture->data;
    auto mime_type = sniff_image_mime(data);
    if (!mime_type) return std::nullopt;  // a format glTF cannot carry
    // length plus a short byte prefix is enough to tell real images apart
    // without hashing megabytes on every face
    std::ostringstream key_stream;
    key_stream << data.size() << ':';
    for (std::size_t i = 0; i < data.size() && i < 16; ++i) {
      key_stream << std::hex << static_cast<int>(data[i]);
    }
    const auto key = key_stream.str();
    auto found = texture_index_by_key.find(key);
    if (found != texture_index_by_key.end()) return found->second;
    const auto idx = scene.textures.size();
    scene.textures.push_back(SceneTexture{data, *mime_type, mat->texture->filename});
    texture_index_by_key.emplace(key, idx);
    return idx;
  };
  std::function<std::vector<InstanceNode>(
      const GeometryBuilder&, const std::string&, std::optional<EntityId>,
      const std::vector<double>&, const std::string&, const std::string&, std::optional<Key>)>
      bake;
  bake = [&](const GeometryBuilder& b, const std::string& defname, std::optional<EntityId> defid,
             const std::vector<double>& matrix, const std::string& layer, const std::string& path,
             std::optional<Key> inherited) {
    std::map<GroupKey, Group> groups;
    for (auto& fv : b.faces) {
      auto& f = fv.second;
      const auto fallback = inherited.value_or(default_color(p, layer));
      const auto front_mat = find_material(p, f.material_id);
      const auto back_mat = find_material(p, f.back_material_id);
      const auto front = material_color(front_mat).value_or(fallback);
      const auto back = material_color(back_mat).value_or(fallback);
      std::vector<std::vector<EntityId>> loops;
      for (auto& l : f.loops) {
        auto x = loop_vertices(l, b);
        if (!x.empty()) loops.push_back(std::move(x));
      }
      if (loops.empty()) continue;
      std::map<EntityId, Vertex> vv;
      for (auto& x : b.vertices) vv[x.first] = {x.first, x.second[0], x.second[1], x.second[2]};
      const auto triangles = triangulate_face_3d(vv, loops, f.normal);
      // Not a structured binding: those can't be captured by the nested
      // add_side lambda below in C++17 (only from C++20 onward).
      const auto uv_basis = face_uv_basis(f.normal);
      const Vec3& xr = uv_basis.first;
      const Vec3& yr = uv_basis.second;
      const auto add_side = [&](const GroupKey& key, bool reverse,
                                const std::optional<std::array<double, 9>>& uv_transform,
                                double tile_w, double tile_h) {
        auto& group = groups[key];
        for (auto triangle : triangles) {
          if (reverse) std::swap(triangle[1], triangle[2]);
          std::array<size_t, 3> indices{};
          for (int vertex = 0; vertex < 3; ++vertex) {
            auto id = triangle[vertex];
            const auto& pos = b.vertices.at(id);
            const auto [u, v] = compute_face_uv(pos, xr, yr, uv_transform, tile_w, tile_h);
            const VKey vkey{id, u, v};
            auto it = group.map.find(vkey);
            if (it == group.map.end()) {
              it = group.map.emplace(vkey, group.verts.size()).first;
              group.verts.push_back(pos);
              group.uvs.push_back({float(u), float(v)});
            }
            indices[vertex] = it->second;
            auto& normal = group.normals[vkey];
            for (int axis = 0; axis < 3; ++axis)
              normal[axis] += reverse ? -f.normal[axis] : f.normal[axis];
          }
          group.tris.push_back(indices);
        }
      };
      if (front == back) {
        const auto [tw, th] = tile_size(front_mat);
        add_side({front, true, texture_index_for(front_mat)}, false, f.uv_transform, tw, th);
      } else {
        const auto [ftw, fth] = tile_size(front_mat);
        add_side({front, false, texture_index_for(front_mat)}, false, f.uv_transform, ftw, fth);
        const auto [btw, bth] = tile_size(back_mat);
        add_side({back, false, texture_index_for(back_mat)}, true, f.uv_transform_back, btw, bth);
      }
    }
    for (auto& kv : groups) {
      auto& g = kv.second;
      if (g.tris.empty()) continue;
      auto geom = "mesh_" + std::to_string(mesh_counter++) + "_" + safe(path) + "_" + layer;
      if (groups.size() > 1)
        geom += "_" + std::to_string(kv.first.color[0]) + "_" + std::to_string(kv.first.color[1]) +
                "_" + std::to_string(kv.first.color[2]) + "_" + std::to_string(kv.first.color[3]) +
                (kv.first.double_sided ? "_ds" : "_ss");
      MeshMetadata meta;
      meta.name =
          path == "ROOT"
              ? "ROOT"
              : path.substr(path.rfind(" / ") == std::string::npos ? 0 : path.rfind(" / ") + 3);
      meta.definition_name = defname;
      meta.layer = layer;
      meta.path = path;
      meta.position_mm = {matrix.size() > 9 ? matrix[9] * 25.4 : 0,
                          matrix.size() > 10 ? matrix[10] * 25.4 : 0,
                          matrix.size() > 11 ? matrix[11] * 25.4 : 0};
      scene.mesh_index[geom] = meta;
      GlbPrimitive prim;
      prim.geom_name = geom;
      const auto mirrored = transform_determinant(matrix) < 0.0;
      for (auto& triangle : g.tris) {
        prim.indices.push_back(static_cast<uint32_t>(triangle[0]));
        prim.indices.push_back(static_cast<uint32_t>(triangle[mirrored ? 2 : 1]));
        prim.indices.push_back(static_cast<uint32_t>(triangle[mirrored ? 1 : 2]));
      }
      for (auto& m : g.map) {
        auto pt = transform_point(matrix, b.vertices.at(std::get<0>(m.first)));
        prim.positions.resize(g.verts.size() * 3);
        prim.normals.resize(g.verts.size() * 3);
        prim.uvs.resize(g.verts.size() * 2);
        auto i = m.second;
        prim.positions[i * 3] = float(pt[0] * .0254);
        prim.positions[i * 3 + 1] = float(pt[2] * .0254);
        prim.positions[i * 3 + 2] = float(-pt[1] * .0254);
        prim.uvs[i * 2] = g.uvs[i][0];
        prim.uvs[i * 2 + 1] = g.uvs[i][1];
        auto n = g.normals[m.first];
        double l = std::sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2]);
        if (l < 1e-6)
          n = {0, 0, 1};
        else
          for (auto& x : n) x /= l;
        auto w = transform_normal(matrix, n);
        l = std::sqrt(w[0] * w[0] + w[1] * w[1] + w[2] * w[2]);
        if (l < 1e-6) {
          w = n;
          l = 1.0;
        }
        prim.normals[i * 3] = float(w[0] / l);
        prim.normals[i * 3 + 1] = float(w[2] / l);
        prim.normals[i * 3 + 2] = float(-w[1] / l);
      }
      auto mi = materials.find(kv.first);
      if (mi == materials.end()) {
        auto idx = scene.gltf_materials.size();
        GltfMaterial gm;
        gm.pbr_metallic_roughness.base_color_factor = {
            kv.first.color[0] / 255., kv.first.color[1] / 255., kv.first.color[2] / 255.,
            kv.first.color[3] / 255.};
        gm.pbr_metallic_roughness.base_color_texture = kv.first.texture_index;
        gm.double_sided = kv.first.double_sided;
        scene.gltf_materials.push_back(gm);
        mi = materials.emplace(kv.first, idx).first;
      }
      prim.material_index = mi->second;
      scene.glb_primitives.push_back(std::move(prim));
    }
    std::vector<InstanceNode> children;
    for (auto& i : b.instances) {
      std::string child_layer = layer;
      if (!i.layer.empty()) {
        try {
          auto li = p.layer_id_to_name.find(std::stoll(i.layer));
          if (li != p.layer_id_to_name.end()) child_layer = li->second;
        } catch (...) {
          child_layer = i.layer;
          emit_log(o, LogLevel::debug, "Failed to resolve layer id '" + i.layer + "' to a name");
        }
      }
      auto child_color = inherited;
      if (auto color = material_color(find_material(p, i.material_id))) child_color = color;
      auto nm =
          i.name.empty() ? "Component_" + (i.ref_idx ? std::to_string(*i.ref_idx) : "") : i.name;
      auto child_path = path + " / " + nm;
      auto mat = multiply_matrices(matrix, i.matrix);
      std::vector<InstanceNode> nested;
      std::string child_def;
      if (i.ref_idx) {
        if (active.count(*i.ref_idx))
          throw SkpParseError("Recursive component definition", ParseStage::build_scene, {}, {}, {},
                              {}, *i.ref_idx);
        auto d = p.definitions.find(*i.ref_idx);
        if (d != p.definitions.end()) {
          active.insert(*i.ref_idx);
          child_def = d->second.name;
          nested = bake(d->second.builder, d->second.name, *i.ref_idx, mat, child_layer, child_path,
                        child_color);
          active.erase(*i.ref_idx);
        }
      }
      InstanceNode node{i.name,
                        child_def,
                        child_layer,
                        {mat.size() > 9 ? mat[9] * 25.4 : 0, mat.size() > 10 ? mat[10] * 25.4 : 0,
                         mat.size() > 11 ? mat[11] * 25.4 : 0},
                        i.properties,
                        std::move(nested)};
      children.push_back(std::move(node));
      if (++instance_counter % progress_interval == 0)
        emit_progress(o, ParseStage::build_scene, instance_counter, instance_counter);
    }
    return children;
  };
  std::vector<double> identity{1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1};
  scene.scene_hierarchy.children =
      bake(p.root.builder, "ROOT_MODEL", {}, identity, "Layer0", "ROOT", {});
  emit_log(o, LogLevel::information,
           "Scene build complete: " + std::to_string(instance_counter) + " instances, " +
               std::to_string(scene.mesh_index.size()) + " meshes, " +
               std::to_string(scene.glb_primitives.size()) + " primitives");
  return scene;
}
}  // namespace openskp
