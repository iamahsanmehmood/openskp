#include <algorithm>
#include <cmath>
#include <regex>
#include <set>
#include <sstream>
#include <tuple>
#include <utility>

#include "curves.hpp"
#include "face_groups.hpp"
#include "internal.hpp"

namespace openskp {
namespace {

std::string safe(std::string s) {
  for (size_t p = 0; (p = s.find(" / ", p)) != std::string::npos;) s.replace(p, 3, "__");
  std::replace(s.begin(), s.end(), ' ', '_');
  if (s.size() > 80) s.resize(80);
  return s;
}

// Matches SketchUp's own auto-generated placeholder definition names
// ("Group#1", "Component#12"), which carry no more meaning than the
// internal index they'd otherwise fall back to - mirrors Python's
// _is_generic_definition_name() exactly (same pattern, duplicated rather
// than shared, exactly as openskp.scene.build_scene duplicates it from
// openskp.instanced_scene - see instanced_scene.cpp's identical helper).
bool is_generic_definition_name(const std::string& name) {
  static const std::regex kPattern(R"(^(?:Group|Component)\d*#\d+$)");
  return std::regex_match(name, kPattern);
}

// Scene nodes omit SketchUp's own dictionaries. dynamic_attributes is
// already on `properties`. SU_InstanceSet is the always-present
// Owner/Status boilerplate. Model-level Instance::attribute_dictionaries
// keeps both; this filter stays here, matching scene.py. Duplicated in
// instanced_scene.cpp rather than shared, same as the helper above.
bool omitted_from_scene(const std::string& dict_name) {
  return dict_name == "dynamic_attributes" || dict_name == "SU_InstanceSet";
}
}  // namespace

Scene build_scene_raw(RawParsed&& p, const ParseOptions& o) {
  const auto style_colors = unpainted_style_colors(p, o.unpainted_faces);
  Scene scene;
  scene.scene_hierarchy = {"ROOT", "ROOT_MODEL", "Layer0", {0, 0, 0}, {}, {}, {}};
  scene.layer_hidden = p.layer_hidden;
  scene.unread_root_entities = p.unread_root_entities;
  scene.pages = build_pages(p);
  emit_log(o, LogLevel::information,
           "Building scene: " + std::to_string(p.definitions.size()) + " definitions available");
  std::map<GroupKey, size_t> materials;
  size_t mesh_counter = 0, instance_counter = 0;
  std::set<EntityId> active;

  // Deferred mesh backfill: a mesh's own path is recorded verbatim as a
  // path_updates key by the exact instance that placed the definition
  // that mesh's own faces belong to (never an ancestor's), so a direct
  // O(1) lookup per mesh after the whole tree is built is enough - no
  // cascading from an ancestor down to its descendants' own meshes.
  // Needed because a mesh's own MeshMetadata is built once per
  // definition's own geometry (shared across every instance of that
  // definition), before the instance placing it - and so its real
  // per-instance name/properties/attribute dictionaries - is even known.
  // Matches openskp.scene.build_scene's identical path_updates mechanism.
  std::map<std::string, std::tuple<std::map<std::string, std::string>, std::string,
                                   std::map<std::string, std::map<std::string, std::string>>>>
      path_updates;

  // Textures deduplicated by full content (with a pointer-identity fast path
  // per face): the same image routinely backs several materials, while
  // equal-size PNGs/TIFFs share their 16-byte header and must stay distinct.
  auto texture_index_for = make_texture_index_for(scene.textures, o);
  std::map<std::string, std::size_t> curve_seq;
  std::map<std::optional<EntityId>, PreparedDefCurves> prepared_curves_cache;
  std::map<std::optional<EntityId>, FaceLayerSummary> face_layer_cache;
  auto get_face_layer_summary = [&](std::optional<EntityId> defid,
                                    const GeometryBuilder& b) -> const FaceLayerSummary& {
    auto it = face_layer_cache.find(defid);
    if (it != face_layer_cache.end()) return it->second;
    return face_layer_cache.emplace(defid, summarize_face_layers(p, b, o)).first->second;
  };

  std::map<EntityId, std::map<std::string, std::map<std::string, std::string>>>
      definition_dicts_cache;
  auto definition_dicts = [&](std::optional<EntityId> ref_idx)
      -> std::map<std::string, std::map<std::string, std::string>> {
    if (!ref_idx) return {};
    auto cached = definition_dicts_cache.find(*ref_idx);
    if (cached != definition_dicts_cache.end()) return cached->second;
    std::map<std::string, std::map<std::string, std::string>> result;
    auto d = p.definitions.find(*ref_idx);
    if (d != p.definitions.end()) {
      result = stringify_attr_dictionaries(d->second.parsed_dictionaries);
    }
    definition_dicts_cache.emplace(*ref_idx, result);
    return result;
  };

  std::function<std::vector<InstanceNode>(const GeometryBuilder&, const std::string&,
                                          std::optional<EntityId>, const std::vector<double>&,
                                          const std::string&, const std::string&,
                                          const std::shared_ptr<RawMaterial>&)>
      bake;
  bake = [&](const GeometryBuilder& b, const std::string& defname, std::optional<EntityId> defid,
             const std::vector<double>& matrix, const std::string& layer, const std::string& path,
             const std::shared_ptr<RawMaterial>& inherited_material) {
    const auto& summary = get_face_layer_summary(defid, b);
    const std::string mesh_layer =
        layer == "Layer0" ? summary.uniform_layer.value_or(layer) : layer;
    FaceColorKey fallback{};
    FaceColorKey fallback_back{};
    bool use_face_tag_colors = false;
    if (auto inherited_color = material_color(inherited_material)) {
      fallback = fallback_back = *inherited_color;
    } else if (style_colors) {
      std::tie(fallback, fallback_back) = *style_colors;
    } else {
      fallback = fallback_back = default_color(p, layer);
      use_face_tag_colors = !inherited_material;
    }
    auto groups =
        build_local_face_groups(p, b,
                                FaceGroupContext{texture_index_for, fallback, fallback_back,
                                                 inherited_material, use_face_tag_colors, o});
    for (auto& kv : groups) {
      auto& g = kv.second;
      if (g.tris.empty()) continue;
      auto geom = "mesh_" + std::to_string(mesh_counter++) + "_" + safe(path) + "_" + mesh_layer;
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
      meta.layer = mesh_layer;
      meta.path = path;
      meta.position_mm = {matrix.size() > 9 ? matrix[9] * 25.4 : 0,
                          matrix.size() > 10 ? matrix[10] * 25.4 : 0,
                          matrix.size() > 11 ? matrix[11] * 25.4 : 0};
      scene.mesh_index[geom] = meta;
      GlbPrimitive prim;
      prim.geom_name = geom;
      prim.unpainted_back = kv.first.unpainted_back;
      prim.back_triangles = g.back_tris;
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
      GroupKey mat_key = kv.first;
      mat_key.unpainted_back = false;
      auto mi = materials.find(mat_key);
      if (mi == materials.end()) {
        auto idx = scene.gltf_materials.size();
        GltfMaterial gm;
        gm.name = kv.first.material_name;
        gm.pbr_metallic_roughness.base_color_factor = {
            kv.first.color[0] / 255., kv.first.color[1] / 255., kv.first.color[2] / 255.,
            kv.first.color[3] / 255.};
        gm.pbr_metallic_roughness.base_color_texture = kv.first.texture_index;
        gm.double_sided = kv.first.double_sided;
        gm.colorized = kv.first.colorized && kv.first.texture_index.has_value();
        gm.colorize_type = gm.colorized ? kv.first.colorize_type : 0;
        scene.gltf_materials.push_back(gm);
        mi = materials.emplace(mat_key, idx).first;
      }
      prim.material_index = mi->second;
      scene.glb_primitives.push_back(std::move(prim));
    }

    if (o.include_curve_sets) {
      auto prep_it = prepared_curves_cache.find(defid);
      if (prep_it == prepared_curves_cache.end()) {
        prep_it = prepared_curves_cache.emplace(defid, prepare_def_curves(p, b, o)).first;
      }
      const auto& prepared = prep_it->second;
      if (!prepared.runs.empty()) {
        const auto fm = matrix.size() >= 12 ? fold_scale(matrix) : std::vector<double>{};
        const bool has_fm = fm.size() >= 12;
        const std::string comp_name =
            path == "ROOT"
                ? ""
                : path.substr(path.rfind(" / ") == std::string::npos ? 0 : path.rfind(" / ") + 3);
        for (const auto& run : prepared.runs) {
          std::vector<std::array<double, 3>> pts;
          pts.reserve(run.local_verts.size());
          if (!has_fm) {
            for (const auto& v : run.local_verts) {
              pts.push_back({round6(v[0] * kCurvesInchesToM), round6(v[2] * kCurvesInchesToM),
                             round6(-v[1] * kCurvesInchesToM)});
            }
          } else {
            for (const auto& v : run.local_verts) {
              const double px = fm[0] * v[0] + fm[1] * v[1] + fm[2] * v[2] + fm[9];
              const double py = fm[3] * v[0] + fm[4] * v[1] + fm[5] * v[2] + fm[10];
              const double pz = fm[6] * v[0] + fm[7] * v[1] + fm[8] * v[2] + fm[11];
              pts.push_back({round6(px * kCurvesInchesToM), round6(pz * kCurvesInchesToM),
                             round6(-py * kCurvesInchesToM)});
            }
          }
          const std::string run_layer = run.explicit_layer.value_or(mesh_layer);
          std::string cname;
          bool cgen = false;
          if (path == "ROOT") {
            const std::size_t seq = ++curve_seq[run_layer];
            cname = run_layer + "_" + std::to_string(seq);
            cgen = true;
          } else {
            cname = comp_name;
          }
          CurveSetMetadata cs;
          cs.name = std::move(cname);
          cs.definition_name = defname;
          cs.layer = run_layer;
          cs.path = path;
          cs.name_is_generated = cgen;
          cs.closed = run.closed;
          cs.arc = solve_run_arc(b, run.edge_ids, has_fm ? &fm : nullptr, pts, run.closed,
                                 &prepared.curve_edge_counts);
          cs.points_m = std::move(pts);
          scene.curve_sets.push_back(std::move(cs));
        }
      }
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
      auto child_material = find_material(p, i.material_id);
      if (!child_material) child_material = inherited_material;

      // def_name is looked up before building the node, since the
      // name-resolution fallback chain below needs it - same order as
      // openskp.instanced_scene / openskp.scene's own build_scene.
      std::string def_name;
      if (i.ref_idx) {
        auto dd = p.definitions.find(*i.ref_idx);
        if (dd != p.definitions.end()) def_name = dd->second.name;
      }
      const bool def_name_is_real = !def_name.empty() && !is_generic_definition_name(def_name);

      // Same fallback order as instanced_scene.cpp: attribute-dict
      // override ("name"/"label"/"code" on any dictionary except
      // dynamic_attributes and SU_InstanceSet, first dictionary and
      // first key found wins), then the instance's own name, then the
      // definition's own name if it's not itself an auto-generated
      // "Group#1"-style placeholder, then finally the internal index.
      std::optional<std::string> name_override;
      for (const auto& dict : i.attribute_dicts) {
        if (omitted_from_scene(dict.first)) continue;
        const auto& entries = dict.second;
        for (const char* key : {"name", "label", "code"}) {
          auto it = entries.find(key);
          if (it != entries.end()) {
            std::string s = it->second.to_string();
            if (!s.empty()) {
              name_override = std::move(s);
              break;
            }
          }
        }
        if (name_override) break;
      }

      const std::string inst_name =
          !i.name.empty()    ? i.name
          : def_name_is_real ? def_name
                             : ("Component_" + (i.ref_idx ? std::to_string(*i.ref_idx) : ""));
      const std::string display_name = name_override.value_or(inst_name);

      auto child_path = path + " / " + inst_name;
      auto mat = multiply_matrices(matrix, i.matrix);
      std::vector<InstanceNode> nested;
      if (i.ref_idx) {
        if (active.count(*i.ref_idx))
          throw SkpParseError("Recursive component definition", ParseStage::build_scene, {}, {}, {},
                              {}, *i.ref_idx);
        auto d = p.definitions.find(*i.ref_idx);
        if (d != p.definitions.end()) {
          active.insert(*i.ref_idx);
          nested = bake(d->second.builder, d->second.name, *i.ref_idx, mat, child_layer, child_path,
                        child_material);
          active.erase(*i.ref_idx);
        }
      }
      std::string node_layer = child_layer;
      if (child_layer == "Layer0" && nested.empty() && i.ref_idx) {
        auto d = p.definitions.find(*i.ref_idx);
        if (d != p.definitions.end()) {
          const auto& child_summary = get_face_layer_summary(*i.ref_idx, d->second.builder);
          if (child_summary.uniform_layer) node_layer = *child_summary.uniform_layer;
        }
      }
      std::map<std::string, std::map<std::string, std::string>> dicts;
      for (const auto& dict : i.attribute_dicts) {
        if (omitted_from_scene(dict.first)) continue;
        dicts.emplace(dict.first, stringify_attr_dict(dict.second));
      }
      InstanceNode node{display_name,
                        def_name,
                        node_layer,
                        {mat.size() > 9 ? mat[9] * 25.4 : 0, mat.size() > 10 ? mat[10] * 25.4 : 0,
                         mat.size() > 11 ? mat[11] * 25.4 : 0},
                        i.properties,
                        std::move(nested),
                        dicts,
                        definition_dicts(i.ref_idx)};
      path_updates[child_path] = {i.properties, display_name, dicts};
      children.push_back(std::move(node));
      if (++instance_counter % progress_interval == 0)
        emit_progress(o, ParseStage::build_scene, instance_counter, instance_counter);
    }
    return children;
  };
  std::vector<double> identity{1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1};
  scene.scene_hierarchy.children =
      bake(p.root.builder, "ROOT_MODEL", {}, identity, "Layer0", "ROOT", nullptr);

  // Apply the deferred backfill: each mesh's real per-instance name/
  // properties/attribute dictionaries, now that the whole tree (and so
  // every instance's own data) has been walked.
  for (auto& [geom_name, meta] : scene.mesh_index) {
    auto found = path_updates.find(meta.path);
    if (found != path_updates.end()) {
      std::tie(meta.properties, meta.name, meta.attribute_dictionaries) = found->second;
    }
  }

  emit_log(o, LogLevel::information,
           "Scene build complete: " + std::to_string(instance_counter) + " instances, " +
               std::to_string(scene.mesh_index.size()) + " meshes, " +
               std::to_string(scene.glb_primitives.size()) + " primitives");
  return scene;
}

std::vector<GlbPrimitive> Scene::surface_primitives() const {
  std::vector<GlbPrimitive> out;
  out.reserve(glb_primitives.size());
  for (const auto& prim : glb_primitives) {
    if (!prim.unpainted_back) out.push_back(prim);
  }
  return out;
}

}  // namespace openskp
