#include <algorithm>
#include <cmath>
#include <gtest/gtest.h>
#include <map>
#include <set>
#include <tuple>
#include <utility>
#include <vector>

#include <openskp/create.hpp>
#include <openskp/ifc_export.hpp>
#include <openskp/openskp.hpp>

#include "internal.hpp"
#include "test_helpers.hpp"

// Edge::layer - edges carry the same drawing-element layer reference faces
// and instances do (VFF D207 under D007, the legacy drawbase layer id), and
// it can differ from the layer of the faces an edge bounds: a sky dome's
// rim edges sit on "Sky Dome" while other boundaries stay on Layer0.
// build_model() resolves the layer id to its name like it does for faces.

namespace openskp::test {
namespace {

TEST(EdgeLayer, ResolvesTheEdgeLayerIdToItsName) {
  RawParsed parsed;
  parsed.layer_order = {"Layer0", "Sky Dome"};
  parsed.layer_colors["Layer0"] = {136, 136, 136};
  parsed.layer_colors["Sky Dome"] = {0, 0, 255};
  parsed.layer_id_to_name[1] = "Layer0";
  parsed.layer_id_to_name[42] = "Sky Dome";
  parsed.root.builder.vertices[1] = {0, 0, 0};
  parsed.root.builder.vertices[2] = {1, 0, 0};
  parsed.root.builder.vertices[3] = {0, 1, 0};
  parsed.root.builder.edges[10] = {EntityId(1), EntityId(2)};
  parsed.root.builder.edges[11] = {EntityId(2), EntityId(3)};
  parsed.root.builder.edges[12] = {EntityId(3), EntityId(1)};
  parsed.root.builder.edge_layers[10] = "42";
  parsed.root.builder.edge_layers[11] = "1";

  auto model = build_model(std::move(parsed));

  const auto& edges = model.root().edges;
  ASSERT_EQ(edges.size(), 3U);
  EXPECT_EQ(edges.at(10).layer, "Sky Dome");
  EXPECT_EQ(edges.at(11).layer, "Layer0");
  // An edge without a layer reference stays on the default layer.
  EXPECT_EQ(edges.at(12).layer, "");
}

// gondola_v20.skp (SketchUp 2020, classic format) draws its side panels on the
// "Gondulas Laterais" layer, edges included.
TEST(EdgeLayer, ReadsEdgeLayersFromAClassicFile) {
  const auto model = SkpFile::open(fixture("gondola_v20.skp")).parse();
  std::size_t on_layer = 0;
  const auto count = [&](const Definition& d) {
    for (const auto& [id, edge] : d.edges)
      if (edge.layer == "Gondulas Laterais") ++on_layer;
  };
  count(model.root());
  for (const auto& [id, d] : model.definitions) count(d);
  EXPECT_GT(on_layer, 0U);
}

TEST(EdgeLayer, UsesFaceTagColorsAndUniformLeafLayersOnChairAndTable) {
  const auto chair_and_table = std::filesystem::path(OPENSKP_FIXTURES)
                                   .parent_path()
                                   .parent_path()
                                   .parent_path()
                                   .parent_path() /
                               "examples" / "web-viewer" / "samples" / "chair_and_table.skp";
  const auto skp = SkpFile::open(chair_and_table);
  ParseOptions opts{};
  opts.unpainted_faces = "tag";
  const auto baked = skp.build_scene(opts);
  const auto instanced = skp.build_instanced_scene(opts);

  const auto rgb_set = [](const std::vector<GltfMaterial>& mats) {
    std::set<std::tuple<int, int, int>> s;
    for (const auto& m : mats) {
      const auto& bcf = m.pbr_metallic_roughness.base_color_factor;
      s.emplace(static_cast<int>(std::lround(bcf[0] * 255.0)),
                static_cast<int>(std::lround(bcf[1] * 255.0)),
                static_cast<int>(std::lround(bcf[2] * 255.0)));
    }
    return s;
  };

  const auto baked_colors = rgb_set(baked.gltf_materials);
  const auto instanced_colors = rgb_set(instanced.gltf_materials);

  EXPECT_EQ(baked_colors.count({255, 84, 84}), 0U);
  EXPECT_EQ(instanced_colors.count({255, 84, 84}), 0U);
  EXPECT_EQ(baked_colors.count({120, 150, 190}), 1U);
  EXPECT_EQ(baked_colors.count({190, 150, 120}), 1U);
  EXPECT_EQ(instanced_colors, baked_colors);

  std::vector<std::pair<std::string, std::string>> baked_layers;
  std::function<void(const InstanceNode&)> collect_baked = [&](const InstanceNode& n) {
    baked_layers.emplace_back(n.name, n.layer);
    for (const auto& c : n.children) collect_baked(c);
  };
  std::vector<std::pair<std::string, std::string>> instanced_layers;
  std::function<void(const InstancedNode&)> collect_instanced = [&](const InstancedNode& n) {
    instanced_layers.emplace_back(n.name, n.layer);
    for (const auto& c : n.children) collect_instanced(c);
  };
  collect_baked(baked.scene_hierarchy);
  collect_instanced(instanced.scene_hierarchy);
  EXPECT_EQ(instanced_layers, baked_layers);
  EXPECT_NE(std::find(baked_layers.begin(), baked_layers.end(),
                      std::make_pair(std::string("Chair"), std::string("Layer0"))),
            baked_layers.end());
  EXPECT_NE(std::find(baked_layers.begin(), baked_layers.end(),
                      std::make_pair(std::string("Leg_Chair"), std::string("Chair"))),
            baked_layers.end());
}

TEST(EdgeLayer, MixedTwentyUntaggedAndOneHiddenFaceDoNotHijackMeshesOrChildren) {
  auto b = create();
  LayerOptions hidden_opts{};
  hidden_opts.color = {10, 20, 30, 255};
  hidden_opts.hidden = true;
  const int hidden_tag = b->add_layer("HiddenTag", hidden_opts);

  auto& child_def = b->add_component_definition("Child");
  child_def.add_face({{0, 0, 0}, {1, 0, 0}, {1, 1, 0}, {0, 1, 0}});
  child_def.close();

  auto& parent_def = b->add_component_definition("MixedParent");
  for (int k = 0; k < 20; ++k) {
    const double x = k * 2.0;
    parent_def.add_face({{x, 0, 0}, {x + 1, 0, 0}, {x + 1, 1, 0}, {x, 1, 0}});
  }
  FaceOptions fopts{};
  fopts.layer = hidden_tag;
  parent_def.add_face({{0, 5, 0}, {1, 5, 0}, {1, 6, 0}, {0, 6, 0}}, fopts);
  InstanceOptions child_iopts{};
  child_iopts.name = "NestedChild";
  child_iopts.translation = {0, 10, 0};
  parent_def.add_instance(child_def, child_iopts);
  parent_def.close();

  InstanceOptions parent_iopts{};
  parent_iopts.name = "MixedPlacement";
  b->add_instance(parent_def, parent_iopts);

  const auto skp = SkpFile::from_buffer(b->to_bytes());
  const auto baked = skp.build_scene();
  const auto instanced = skp.build_instanced_scene();

  for (const auto& kv : baked.mesh_index) {
    EXPECT_EQ(kv.second.layer, "Layer0");
  }

  std::vector<std::pair<std::string, std::string>> baked_layers;
  std::function<void(const InstanceNode&)> collect_baked = [&](const InstanceNode& n) {
    baked_layers.emplace_back(n.name, n.layer);
    for (const auto& c : n.children) collect_baked(c);
  };
  std::vector<std::pair<std::string, std::string>> instanced_layers;
  std::function<void(const InstancedNode&)> collect_instanced = [&](const InstancedNode& n) {
    instanced_layers.emplace_back(n.name, n.layer);
    for (const auto& c : n.children) collect_instanced(c);
  };
  collect_baked(baked.scene_hierarchy);
  collect_instanced(instanced.scene_hierarchy);
  const std::vector<std::pair<std::string, std::string>> expected = {
      {"ROOT", "Layer0"},
      {"MixedPlacement", "Layer0"},
      {"NestedChild", "Layer0"},
  };
  EXPECT_EQ(baked_layers, expected);
  EXPECT_EQ(instanced_layers, expected);
}

TEST(EdgeLayer, UniformFaceTagAppliesToOwnMeshesRootMeshesAndChildlessNodesOnly) {
  auto b = create();
  LayerOptions roof_opts{};
  roof_opts.color = {100, 120, 140, 255};
  const int roof = b->add_layer("Roof", roof_opts);
  LayerOptions leaf_opts{};
  leaf_opts.color = {40, 80, 120, 255};
  const int leaf_tag = b->add_layer("LeafTag", leaf_opts);

  auto& child_def = b->add_component_definition("UntaggedChild");
  child_def.add_face({{0, 0, 0}, {1, 0, 0}, {1, 1, 0}, {0, 1, 0}});
  child_def.close();

  auto& parent_def = b->add_component_definition("UniformParent");
  FaceOptions roof_face{};
  roof_face.layer = roof;
  parent_def.add_face({{0, 0, 0}, {2, 0, 0}, {2, 2, 0}, {0, 2, 0}}, roof_face);
  InstanceOptions child_inst{};
  child_inst.name = "ChildInst";
  child_inst.translation = {5, 0, 0};
  parent_def.add_instance(child_def, child_inst);
  parent_def.close();

  auto& leaf_def = b->add_component_definition("LeafDef");
  FaceOptions leaf_face{};
  leaf_face.layer = leaf_tag;
  leaf_def.add_face({{0, 0, 0}, {1, 0, 0}, {1, 1, 0}, {0, 1, 0}}, leaf_face);
  leaf_def.close();

  b->add_face({{0, 20, 0}, {2, 20, 0}, {2, 22, 0}, {0, 22, 0}}, roof_face);
  InstanceOptions p_inst{};
  p_inst.name = "ParentInst";
  b->add_instance(parent_def, p_inst);
  InstanceOptions l_inst{};
  l_inst.name = "LeafInst";
  l_inst.translation = {10, 0, 0};
  b->add_instance(leaf_def, l_inst);

  const auto skp = SkpFile::from_buffer(b->to_bytes());
  const auto baked = skp.build_scene();
  const auto instanced = skp.build_instanced_scene();

  std::map<std::string, std::set<std::string>> layers_by_path;
  for (const auto& kv : baked.mesh_index) {
    layers_by_path[kv.second.path].insert(kv.second.layer);
  }
  EXPECT_EQ(layers_by_path["ROOT"], std::set<std::string>{"Roof"});
  EXPECT_EQ(layers_by_path["ROOT / ParentInst"], std::set<std::string>{"Roof"});
  EXPECT_EQ(layers_by_path["ROOT / ParentInst / ChildInst"], std::set<std::string>{"Layer0"});
  EXPECT_EQ(layers_by_path["ROOT / LeafInst"], std::set<std::string>{"LeafTag"});

  std::vector<std::pair<std::string, std::string>> baked_layers;
  std::function<void(const InstanceNode&)> collect_baked = [&](const InstanceNode& n) {
    baked_layers.emplace_back(n.name, n.layer);
    for (const auto& c : n.children) collect_baked(c);
  };
  std::vector<std::pair<std::string, std::string>> instanced_layers;
  std::function<void(const InstancedNode&)> collect_instanced = [&](const InstancedNode& n) {
    instanced_layers.emplace_back(n.name, n.layer);
    for (const auto& c : n.children) collect_instanced(c);
  };
  collect_baked(baked.scene_hierarchy);
  collect_instanced(instanced.scene_hierarchy);
  const std::vector<std::pair<std::string, std::string>> expected = {
      {"ROOT", "Layer0"},
      {"ParentInst", "Layer0"},
      {"ChildInst", "Layer0"},
      {"LeafInst", "LeafTag"},
  };
  EXPECT_EQ(baked_layers, expected);
  EXPECT_EQ(instanced_layers, expected);
}

TEST(EdgeLayer, KeepsOwnTagOrInheritedParentTagOverUniformFaceTag) {
  auto b = create();
  LayerOptions wood_opts{};
  wood_opts.color = {160, 110, 60, 255};
  const int wood = b->add_layer("Wood", wood_opts);

  LayerOptions hidden_opts{};
  hidden_opts.color = {40, 40, 40, 255};
  hidden_opts.hidden = true;
  const int furniture_hidden = b->add_layer("FurnitureHidden", hidden_opts);

  LayerOptions group_opts{};
  group_opts.color = {30, 140, 90, 255};
  const int group_tag = b->add_layer("GroupTag", group_opts);

  auto& wood_panel = b->add_component_definition("WoodPanel");
  FaceOptions wood_face{};
  wood_face.layer = wood;
  wood_panel.add_face({{0, 0, 0}, {2, 0, 0}, {2, 2, 0}, {0, 2, 0}}, wood_face);
  wood_panel.close();

  auto& parent_group = b->add_component_definition("ParentGroup");
  InstanceOptions untagged_in_group{};
  untagged_in_group.name = "UntaggedInGroup";
  untagged_in_group.translation = {5, 0, 0};
  parent_group.add_instance(wood_panel, untagged_in_group);
  parent_group.close();

  InstanceOptions hf_inst{};
  hf_inst.name = "HiddenFurniture";
  hf_inst.layer = furniture_hidden;
  b->add_instance(wood_panel, hf_inst);

  InstanceOptions tg_inst{};
  tg_inst.name = "TaggedGroup";
  tg_inst.layer = group_tag;
  tg_inst.translation = {10, 0, 0};
  b->add_instance(parent_group, tg_inst);

  const auto skp = SkpFile::from_buffer(b->to_bytes());
  ParseOptions opts{};
  opts.unpainted_faces = "tag";
  const auto baked = skp.build_scene(opts);
  const auto instanced = skp.build_instanced_scene(opts);

  std::map<std::string, std::string> mesh_layers;
  for (const auto& kv : baked.mesh_index) {
    mesh_layers[kv.second.path] = kv.second.layer;
  }
  EXPECT_EQ(mesh_layers["ROOT / HiddenFurniture"], "FurnitureHidden");
  EXPECT_EQ(mesh_layers["ROOT / TaggedGroup / UntaggedInGroup"], "GroupTag");

  std::vector<std::pair<std::string, std::string>> baked_layers;
  std::function<void(const InstanceNode&)> collect_baked = [&](const InstanceNode& n) {
    baked_layers.emplace_back(n.name, n.layer);
    for (const auto& c : n.children) collect_baked(c);
  };
  std::vector<std::pair<std::string, std::string>> instanced_layers;
  std::function<void(const InstancedNode&)> collect_instanced = [&](const InstancedNode& n) {
    instanced_layers.emplace_back(n.name, n.layer);
    for (const auto& c : n.children) collect_instanced(c);
  };
  collect_baked(baked.scene_hierarchy);
  collect_instanced(instanced.scene_hierarchy);
  const std::vector<std::pair<std::string, std::string>> expected = {
      {"ROOT", "Layer0"},
      {"HiddenFurniture", "FurnitureHidden"},
      {"TaggedGroup", "GroupTag"},
      {"UntaggedInGroup", "GroupTag"},
  };
  EXPECT_EQ(baked_layers, expected);
  EXPECT_EQ(instanced_layers, expected);

  const auto ifc = to_ifc(baked);
  EXPECT_NE(ifc.find("IFCPRESENTATIONLAYERWITHSTYLE('FurnitureHidden'"), std::string::npos);
  EXPECT_NE(ifc.find(",.F.,"), std::string::npos);
  EXPECT_EQ(ifc.find("IFCPRESENTATIONLAYERWITHSTYLE('Wood'"), std::string::npos);
}

}  // namespace
}  // namespace openskp::test
