#include <algorithm>
#include <cmath>
#include <set>
#include <sstream>
#include <tuple>

#include "internal.hpp"

namespace openskp {
namespace {
using Key = std::array<int, 4>;

struct GroupKey {
  Key color;
  bool double_sided{};

  bool operator<(const GroupKey& other) const {
    return std::tie(color, double_sided) < std::tie(other.color, other.double_sided);
  }
};

struct Group {
  std::vector<Vec3> verts;
  std::vector<std::array<size_t, 3>> tris;
  std::map<EntityId, size_t> map;
  std::map<EntityId, Vec3> normals;
};

std::optional<Key> material_color(const RawParsed& parsed, std::optional<EntityId> id) {
  if (!id) return {};
  auto name = parsed.material_id_to_name.find(*id);
  if (name == parsed.material_id_to_name.end()) return {};
  std::shared_ptr<RawMaterial> material;
  auto direct = parsed.materials.find(name->second);
  if (direct != parsed.materials.end())
    material = direct->second;
  else {
    auto folder = parsed.materials_by_folder.find(name->second);
    if (folder != parsed.materials_by_folder.end()) material = folder->second;
  }
  if (!material) return {};
  return Key{material->r, material->g, material->b,
             static_cast<int>(std::lround(std::clamp(material->transparency, 0.0, 1.0) * 255.0))};
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
      const auto front = material_color(p, f.material_id).value_or(fallback);
      const auto back = material_color(p, f.back_material_id).value_or(fallback);
      std::vector<std::vector<EntityId>> loops;
      for (auto& l : f.loops) {
        auto x = loop_vertices(l, b);
        if (!x.empty()) loops.push_back(std::move(x));
      }
      if (loops.empty()) continue;
      std::map<EntityId, Vertex> vv;
      for (auto& x : b.vertices) vv[x.first] = {x.first, x.second[0], x.second[1], x.second[2]};
      const auto triangles = triangulate_face_3d(vv, loops, f.normal);
      const auto add_side = [&](const GroupKey& key, bool reverse) {
        auto& group = groups[key];
        for (auto triangle : triangles) {
          if (reverse) std::swap(triangle[1], triangle[2]);
          std::array<size_t, 3> indices{};
          for (int vertex = 0; vertex < 3; ++vertex) {
            auto id = triangle[vertex];
            auto it = group.map.find(id);
            if (it == group.map.end()) {
              it = group.map.emplace(id, group.verts.size()).first;
              group.verts.push_back(b.vertices.at(id));
            }
            indices[vertex] = it->second;
            auto& normal = group.normals[id];
            for (int axis = 0; axis < 3; ++axis)
              normal[axis] += reverse ? -f.normal[axis] : f.normal[axis];
          }
          group.tris.push_back(indices);
        }
      };
      if (front == back) {
        add_side({front, true}, false);
      } else {
        add_side({front, false}, false);
        add_side({back, false}, true);
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
        auto pt = transform_point(matrix, b.vertices.at(m.first));
        prim.positions.resize(g.verts.size() * 3);
        prim.normals.resize(g.verts.size() * 3);
        auto i = m.second;
        prim.positions[i * 3] = float(pt[0] * .0254);
        prim.positions[i * 3 + 1] = float(pt[2] * .0254);
        prim.positions[i * 3 + 2] = float(-pt[1] * .0254);
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
        }
      }
      auto child_color = inherited;
      if (auto color = material_color(p, i.material_id)) child_color = color;
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
