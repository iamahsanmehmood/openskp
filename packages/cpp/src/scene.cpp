#include <algorithm>
#include <cmath>
#include <set>
#include <sstream>

#include "internal.hpp"

namespace openskp {
namespace {
using Key = std::array<int, 3>;

struct Group {
  Key color;
  std::vector<Vec3> verts;
  std::vector<std::array<size_t, 3>> tris;
  std::map<EntityId, size_t> map;
  std::map<EntityId, Vec3> normals;
};

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
  std::map<Key, size_t> materials;
  size_t mesh_counter = 0, instance_counter = 0;
  std::set<EntityId> active;
  std::function<std::vector<InstanceNode>(
      const GeometryBuilder&, const std::string&, std::optional<EntityId>,
      const std::vector<double>&, const std::string&, const std::string&, std::optional<Key>)>
      bake;
  bake = [&](const GeometryBuilder& b, const std::string& defname, std::optional<EntityId> defid,
             const std::vector<double>& matrix, const std::string& layer, const std::string& path,
             std::optional<Key> inherited) {
    std::map<Key, Group> groups;
    for (auto& fv : b.faces) {
      auto& f = fv.second;
      auto color = inherited;
      if (f.material_id) {
        auto mn = p.material_id_to_name.find(*f.material_id);
        if (mn != p.material_id_to_name.end()) {
          auto mi = p.materials.find(mn->second);
          if (mi == p.materials.end()) {
            auto mf = p.materials_by_folder.find(mn->second);
            if (mf != p.materials_by_folder.end())
              color = Key{mf->second->r, mf->second->g, mf->second->b};
          } else
            color = Key{mi->second->r, mi->second->g, mi->second->b};
        }
      }
      if (!color) {
        auto c = p.layer_colors.find(layer);
        auto x = c == p.layer_colors.end() ? Color3{136, 136, 136} : c->second;
        color = Key{x[0], x[1], x[2]};
      }
      auto& g = groups[*color];
      g.color = *color;
      std::vector<std::vector<EntityId>> loops;
      for (auto& l : f.loops) {
        auto x = loop_vertices(l, b);
        if (!x.empty()) loops.push_back(std::move(x));
      }
      if (loops.empty()) continue;
      std::map<EntityId, Vertex> vv;
      for (auto& x : b.vertices) vv[x.first] = {x.first, x.second[0], x.second[1], x.second[2]};
      for (auto& t : triangulate_face_3d(vv, loops, f.normal)) {
        std::array<size_t, 3> tri{};
        for (int j = 0; j < 3; ++j) {
          auto id = t[j];
          auto it = g.map.find(id);
          if (it == g.map.end()) {
            it = g.map.emplace(id, g.verts.size()).first;
            g.verts.push_back(b.vertices.at(id));
          }
          tri[j] = it->second;
          auto& nn = g.normals[id];
          for (int q = 0; q < 3; ++q) nn[q] += f.normal[q];
        }
        g.tris.push_back(tri);
      }
    }
    for (auto& kv : groups) {
      auto& g = kv.second;
      if (g.tris.empty()) continue;
      auto geom = "mesh_" + std::to_string(mesh_counter++) + "_" + safe(path) + "_" + layer;
      if (groups.size() > 1)
        geom += "_" + std::to_string(kv.first[0]) + "_" + std::to_string(kv.first[1]) + "_" +
                std::to_string(kv.first[2]);
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
      for (auto& t : g.tris)
        for (auto x : t) prim.indices.push_back(static_cast<uint32_t>(x));
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
        Vec3 w{(matrix.size() > 0 ? matrix[0] : 1) * n[0] +
                   (matrix.size() > 1 ? matrix[1] : 0) * n[1] +
                   (matrix.size() > 2 ? matrix[2] : 0) * n[2],
               (matrix.size() > 3 ? matrix[3] : 0) * n[0] +
                   (matrix.size() > 4 ? matrix[4] : 1) * n[1] +
                   (matrix.size() > 5 ? matrix[5] : 0) * n[2],
               (matrix.size() > 6 ? matrix[6] : 0) * n[0] +
                   (matrix.size() > 7 ? matrix[7] : 0) * n[1] +
                   (matrix.size() > 8 ? matrix[8] : 1) * n[2]};
        l = std::sqrt(w[0] * w[0] + w[1] * w[1] + w[2] * w[2]);
        prim.normals[i * 3] = float(w[0] / l);
        prim.normals[i * 3 + 1] = float(w[2] / l);
        prim.normals[i * 3 + 2] = float(-w[1] / l);
      }
      auto mi = materials.find(kv.first);
      if (mi == materials.end()) {
        auto idx = scene.gltf_materials.size();
        GltfMaterial gm;
        gm.pbr_metallic_roughness.base_color_factor = {kv.first[0] / 255., kv.first[1] / 255.,
                                                       kv.first[2] / 255., 1};
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
      if (i.material_id) {
        auto mn = p.material_id_to_name.find(*i.material_id);
        if (mn != p.material_id_to_name.end()) {
          auto mi = p.materials.find(mn->second);
          if (mi != p.materials.end())
            child_color = Key{mi->second->r, mi->second->g, mi->second->b};
          else {
            auto mf = p.materials_by_folder.find(mn->second);
            if (mf != p.materials_by_folder.end())
              child_color = Key{mf->second->r, mf->second->g, mf->second->b};
          }
        }
      }
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
