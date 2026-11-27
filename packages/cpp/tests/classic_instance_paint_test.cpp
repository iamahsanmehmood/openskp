#include <algorithm>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <gtest/gtest.h>
#include <map>
#include <random>
#include <set>
#include <string>
#include <tuple>
#include <vector>

#include <openskp/openskp.hpp>

#include "internal.hpp"
#include "test_helpers.hpp"

namespace openskp {
namespace {

const std::vector<Point3> kSquare{{0, 0, 0}, {10, 0, 0}, {10, 10, 0}, {0, 10, 0}};

double round6(double value) { return std::round(value * 1e6) / 1e6; }

SkpFile painted_model() {
  auto builder = create();
  int red = builder->add_material("Red", Color4{200, 10, 10, 255});
  int blue = builder->add_material("Blue", Color4{10, 10, 200, 128});

  auto& leaf = builder->add_component_definition("Leaf");
  leaf.add_face(kSquare);
  leaf.close();

  auto& outer = builder->add_component_definition("Outer");
  InstanceOptions inherits_opts;
  inherits_opts.name = "InheritsPaint";
  outer.add_instance(leaf, inherits_opts);
  InstanceOptions own_opts;
  own_opts.name = "OwnPaint";
  own_opts.material = blue;
  own_opts.translation = {20, 0, 0};
  outer.add_instance(leaf, own_opts);
  outer.close();

  InstanceOptions painted_opts;
  painted_opts.name = "Painted";
  painted_opts.material = red;
  builder->add_instance(outer, painted_opts);

  return SkpFile::from_buffer(builder->to_bytes());
}

using MaterialSummary = std::tuple<double, double, double, double, bool>;

MaterialSummary summarize_material(const GltfMaterial& mat) {
  const auto& bcf = mat.pbr_metallic_roughness.base_color_factor;
  return {round6(bcf[0]), round6(bcf[1]), round6(bcf[2]), round6(bcf[3]), mat.double_sided};
}

TEST(ClassicInstancePaint, PaintIsAppliedAndInheritedByNestedComponents) {
  const auto skp = painted_model();
  const auto baked = skp.build_scene();
  const auto instanced = skp.build_instanced_scene();

  const std::set<MaterialSummary> expected{
      {round6(200.0 / 255.0), round6(10.0 / 255.0), round6(10.0 / 255.0), 1.0, true},
      {round6(10.0 / 255.0), round6(10.0 / 255.0), round6(200.0 / 255.0), round6(128.0 / 255.0),
       true},
  };

  std::set<MaterialSummary> baked_mats;
  for (const auto& prim : baked.glb_primitives) {
    baked_mats.insert(summarize_material(baked.gltf_materials.at(prim.material_index)));
  }
  EXPECT_EQ(baked_mats, expected);

  std::set<MaterialSummary> instanced_mats;
  for (const auto& res : instanced.mesh_resources) {
    for (const auto& prim : res.primitives) {
      instanced_mats.insert(summarize_material(instanced.gltf_materials.at(prim.material_index)));
    }
  }
  EXPECT_EQ(instanced_mats, expected);
}

TEST(ClassicInstancePaint, EachPaintIsItsOwnInstancedMesh) {
  const auto scene = painted_model().build_instanced_scene();
  ASSERT_EQ(scene.scene_hierarchy.children.size(), 1u);
  std::map<std::string, const InstancedNode*> nodes;
  for (const auto& child : scene.scene_hierarchy.children[0].children) {
    nodes[child.name] = &child;
  }
  ASSERT_TRUE(nodes.count("InheritsPaint"));
  ASSERT_TRUE(nodes.count("OwnPaint"));
  ASSERT_TRUE(nodes["InheritsPaint"]->mesh_resource_id.has_value());
  ASSERT_TRUE(nodes["OwnPaint"]->mesh_resource_id.has_value());
  EXPECT_NE(nodes["InheritsPaint"]->mesh_resource_id, nodes["OwnPaint"]->mesh_resource_id);
}

TEST(ClassicInstancePaint, TagIsTheInstancesOwn) {
  auto builder = create();
  int walls = builder->add_layer("Walls");
  auto& part = builder->add_component_definition("Part");
  part.add_face(kSquare);
  part.close();

  InstanceOptions tagged_opts;
  tagged_opts.name = "Tagged";
  tagged_opts.layer = walls;
  builder->add_instance(part, tagged_opts);

  InstanceOptions untagged_opts;
  untagged_opts.name = "Untagged";
  untagged_opts.translation = {20, 0, 0};
  builder->add_instance(part, untagged_opts);

  const auto skp = SkpFile::from_buffer(builder->to_bytes());
  const auto baked = skp.build_scene();
  const auto instanced = skp.build_instanced_scene();

  const std::map<std::string, std::string> expected{{"Tagged", "Walls"}, {"Untagged", "Layer0"}};

  std::map<std::string, std::string> baked_layers;
  for (const auto& node : baked.scene_hierarchy.children) {
    baked_layers[node.name] = node.layer;
  }
  EXPECT_EQ(baked_layers, expected);

  std::map<std::string, std::string> instanced_layers;
  for (const auto& node : instanced.scene_hierarchy.children) {
    instanced_layers[node.name] = node.layer;
  }
  EXPECT_EQ(instanced_layers, expected);
}

const ByteBuffer kPng1x1{
    0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d, 0x49, 0x48,
    0x44, 0x52, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x02, 0x00, 0x00,
    0x00, 0x90, 0x77, 0x53, 0xde, 0x00, 0x00, 0x00, 0x0c, 0x49, 0x44, 0x41, 0x54, 0x78,
    0x9c, 0x63, 0x38, 0x91, 0x62, 0x04, 0x00, 0x03, 0x56, 0x01, 0x5f, 0xe8, 0x17, 0x84,
    0x52, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82,
};

const ByteBuffer kPng2x1{
    0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44,
    0x52, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x01, 0x08, 0x02, 0x00, 0x00, 0x00, 0x7b,
    0x40, 0xe8, 0xdd, 0x00, 0x00, 0x00, 0x0f, 0x49, 0x44, 0x41, 0x54, 0x78, 0x9c, 0x63, 0x30,
    0x4a, 0x39, 0x61, 0x94, 0x72, 0x02, 0x00, 0x08, 0x6d, 0x02, 0xbd, 0xeb, 0xae, 0x9b, 0xe7,
    0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82,
};

std::tuple<float, float, float, float> uv_bounds4(const std::vector<float>& uvs) {
  float min_u = uvs.at(0), max_u = uvs.at(0), min_v = uvs.at(1), max_v = uvs.at(1);
  for (std::size_t i = 0; i + 1 < uvs.size(); i += 2) {
    min_u = std::min(min_u, uvs[i]);
    max_u = std::max(max_u, uvs[i]);
    min_v = std::min(min_v, uvs[i + 1]);
    max_v = std::max(max_v, uvs[i + 1]);
  }
  return {min_u, max_u, min_v, max_v};
}

TEST(ClassicInstancePaint, SdkClassicAndVffTexturedPaintedInstancesAndTags) {
  for (const char* fname :
       {"instance_textured_paint_2020.skp", "instance_textured_paint_2021.skp"}) {
    SCOPED_TRACE(fname);
    const auto skp = SkpFile::open(test::fixture(fname));
    const auto baked = skp.build_scene();
    const auto instanced = skp.build_instanced_scene();

    EXPECT_EQ(baked.textures.size(), 2u);
    EXPECT_EQ(instanced.textures.size(), 2u);
    EXPECT_EQ(baked.glb_primitives.size(), 10u);
    EXPECT_EQ(instanced.mesh_resources.size(), 6u);

    const std::map<std::string, std::string> expected_top{
        {"BrickPanel", "ExteriorTag"}, {"WoodPanel", "InteriorTag"}, {"PlainPanel", "Layer0"},
        {"BrickGroup", "ExteriorTag"}, {"OuterInst", "ExteriorTag"},
    };
    std::map<std::string, std::string> baked_top, inst_top;
    const InstanceNode* baked_outer = nullptr;
    const InstancedNode* inst_outer = nullptr;
    for (const auto& n : baked.scene_hierarchy.children) {
      baked_top[n.name] = n.layer;
      if (n.name == "OuterInst") baked_outer = &n;
    }
    for (const auto& n : instanced.scene_hierarchy.children) {
      inst_top[n.name] = n.layer;
      if (n.name == "OuterInst") inst_outer = &n;
    }
    EXPECT_EQ(baked_top, expected_top);
    EXPECT_EQ(inst_top, expected_top);
    ASSERT_NE(baked_outer, nullptr);
    ASSERT_NE(inst_outer, nullptr);

    const std::map<std::string, std::string> expected_nested{
        {"ChildInherits", "ExteriorTag"},
        {"ChildOverrides", "InteriorTag"},
    };
    std::map<std::string, std::string> baked_nested, inst_nested;
    std::map<std::string, const InstancedNode*> nested_by_name;
    for (const auto& n : baked_outer->children) baked_nested[n.name] = n.layer;
    for (const auto& n : inst_outer->children) {
      inst_nested[n.name] = n.layer;
      nested_by_name[n.name] = &n;
    }
    EXPECT_EQ(baked_nested, expected_nested);
    EXPECT_EQ(inst_nested, expected_nested);

    std::map<std::string, const InstancedMeshResource*> by_id;
    for (const auto& r : instanced.mesh_resources) by_id[r.id] = &r;
    std::map<std::string, const InstancedNode*> top_by_name;
    for (const auto& n : instanced.scene_hierarchy.children) top_by_name[n.name] = &n;

    const auto& grp_prim =
        by_id.at(*top_by_name.at("BrickGroup")->mesh_resource_id)->primitives.at(0);
    const auto& inh_prim =
        by_id.at(*nested_by_name.at("ChildInherits")->mesh_resource_id)->primitives.at(0);
    const auto& ovr_prim =
        by_id.at(*nested_by_name.at("ChildOverrides")->mesh_resource_id)->primitives.at(0);

    const auto grp_tex = instanced.gltf_materials.at(grp_prim.material_index)
                             .pbr_metallic_roughness.base_color_texture;
    const auto ovr_tex = instanced.gltf_materials.at(ovr_prim.material_index)
                             .pbr_metallic_roughness.base_color_texture;
    EXPECT_TRUE(grp_tex.has_value());
    EXPECT_TRUE(ovr_tex.has_value());
    EXPECT_NE(grp_tex, ovr_tex);

    const auto [gu0, gu1, gv0, gv1] = uv_bounds4(grp_prim.uvs);
    const auto [iu0, iu1, iv0, iv1] = uv_bounds4(inh_prim.uvs);
    const auto [ou0, ou1, ov0, ov1] = uv_bounds4(ovr_prim.uvs);
    EXPECT_NEAR(gu0, 0.0f, 1e-4f);
    EXPECT_NEAR(gu1, 1.0f, 1e-4f);
    EXPECT_NEAR(gv1, 1.0f, 1e-4f);
    EXPECT_NEAR(iu1, 1.0f, 1e-4f);
    EXPECT_NEAR(iv1, 1.0f, 1e-4f);
    EXPECT_NEAR(ou0, 0.0f, 1e-4f);
    EXPECT_NEAR(ou1, 2.5f, 1e-4f);
    EXPECT_NEAR(ov1, 2.5f, 1e-4f);
  }
}

TEST(ClassicInstancePaint, WriterUnnamedAndNamedTexturedMaterialsOnInstancesAndGroups) {
  const auto dir = std::filesystem::temp_directory_path() /
                   ("openskp-cpp-559-" + std::to_string(std::random_device{}()));
  std::filesystem::create_directories(dir);

  struct Cleanup {
    std::filesystem::path d;

    ~Cleanup() {
      std::error_code ec;
      std::filesystem::remove_all(d, ec);
    }
  } cleanup{dir};

  const auto tex1 = dir / "a.png";
  const auto tex2 = dir / "b.png";
  {
    std::ofstream f1(tex1, std::ios::binary);
    f1.write(reinterpret_cast<const char*>(kPng1x1.data()),
             static_cast<std::streamsize>(kPng1x1.size()));
    std::ofstream f2(tex2, std::ios::binary);
    f2.write(reinterpret_cast<const char*>(kPng2x1.data()),
             static_cast<std::streamsize>(kPng2x1.size()));
  }

  const std::vector<Point3> rect{{0, 0, 0}, {10, 0, 0}, {10, 20, 0}, {0, 20, 0}};
  auto builder = create();
  const int named_mat = builder->add_texture_material("Brick", tex1, 20.0, 10.0);
  const int unnamed_mat = builder->add_texture_material("", tex2, 8.0, 4.0);
  const int ext = builder->add_layer("Ext");
  const int inter = builder->add_layer("Int");

  auto& panel = builder->add_component_definition("Panel");
  panel.add_face(rect);
  panel.close();

  GroupOptions grp_opts;
  grp_opts.name = "PaintedGroup";
  grp_opts.translation = {40, 0, 0};
  grp_opts.material = named_mat;
  grp_opts.layer = ext;
  auto& grp = builder->add_group(grp_opts);
  grp.add_face(rect);
  grp.close();

  InstanceOptions named_opts;
  named_opts.name = "NamedInst";
  named_opts.material = named_mat;
  named_opts.layer = ext;
  builder->add_instance(panel, named_opts);

  InstanceOptions unnamed_opts;
  unnamed_opts.name = "UnnamedInst";
  unnamed_opts.translation = {20, 0, 0};
  unnamed_opts.material = unnamed_mat;
  unnamed_opts.layer = inter;
  builder->add_instance(panel, unnamed_opts);

  const auto skp = SkpFile::from_buffer(builder->to_bytes());
  const auto baked = skp.build_scene();
  const auto instanced = skp.build_instanced_scene();

  EXPECT_EQ(baked.textures.size(), 2u);
  EXPECT_EQ(instanced.textures.size(), 2u);
  const std::map<std::string, std::string> expected_layers{
      {"PaintedGroup", "Ext"}, {"NamedInst", "Ext"}, {"UnnamedInst", "Int"}};
  std::map<std::string, std::string> baked_layers, inst_layers;
  std::map<std::string, const InstancedNode*> nodes;
  for (const auto& n : baked.scene_hierarchy.children) baked_layers[n.name] = n.layer;
  for (const auto& n : instanced.scene_hierarchy.children) {
    inst_layers[n.name] = n.layer;
    nodes[n.name] = &n;
  }
  EXPECT_EQ(baked_layers, expected_layers);
  EXPECT_EQ(inst_layers, expected_layers);

  std::map<std::string, const InstancedMeshResource*> by_id;
  for (const auto& r : instanced.mesh_resources) by_id[r.id] = &r;
  EXPECT_NE(nodes.at("NamedInst")->mesh_resource_id, nodes.at("UnnamedInst")->mesh_resource_id);
  const auto [nu0, nu1, nv0, nv1] =
      uv_bounds4(by_id.at(*nodes.at("NamedInst")->mesh_resource_id)->primitives.at(0).uvs);
  const auto [uu0, uu1, uv0, uv1] =
      uv_bounds4(by_id.at(*nodes.at("UnnamedInst")->mesh_resource_id)->primitives.at(0).uvs);
  EXPECT_NEAR(nu1, 1.0f, 1e-4f);
  EXPECT_NEAR(nv1, 1.0f, 1e-4f);
  EXPECT_NEAR(uu1, 2.5f, 1e-4f);
  EXPECT_NEAR(uv1, 2.5f, 1e-4f);
}

TEST(ClassicInstancePaint, InstancedSceneIsolatesSameNameSameRgbInheritedMaterialVariants) {
  ByteBuffer raw;
  {
    std::ifstream in(test::fixture("instance_textured_paint_2021.skp"), std::ios::binary);
    ASSERT_TRUE(in);
    raw.assign(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
  }
  ParseOptions opts;
  RawParsed parsed = full_parse(raw, opts);
  auto brick = parsed.materials_by_folder.at("BrickTex");
  auto wood = parsed.materials_by_folder.at("WoodTex");
  parsed.materials.erase("BrickTex");
  parsed.materials.erase("WoodTex");
  brick->name = "Shared";
  wood->name = "Shared";
  brick->r = wood->r = 170;
  brick->g = wood->g = 58;
  brick->b = wood->b = 40;
  parsed.materials["Shared"] = wood;

  const auto instanced = build_instanced_scene_raw(std::move(parsed), opts);
  std::map<std::string, const InstancedMeshResource*> by_id;
  for (const auto& r : instanced.mesh_resources) by_id[r.id] = &r;
  std::map<std::string, const InstancedNode*> top, nested;
  for (const auto& n : instanced.scene_hierarchy.children) top[n.name] = &n;
  for (const auto& n : top.at("OuterInst")->children) nested[n.name] = &n;

  EXPECT_NE(top.at("BrickPanel")->mesh_resource_id, top.at("WoodPanel")->mesh_resource_id);
  EXPECT_NE(nested.at("ChildInherits")->mesh_resource_id,
            nested.at("ChildOverrides")->mesh_resource_id);
  const auto [iu0, iu1, iv0, iv1] =
      uv_bounds4(by_id.at(*nested.at("ChildInherits")->mesh_resource_id)->primitives.at(0).uvs);
  const auto [ou0, ou1, ov0, ov1] =
      uv_bounds4(by_id.at(*nested.at("ChildOverrides")->mesh_resource_id)->primitives.at(0).uvs);
  EXPECT_NEAR(iu1, 1.0f, 1e-4f);
  EXPECT_NEAR(iv1, 1.0f, 1e-4f);
  EXPECT_NEAR(ou1, 2.5f, 1e-4f);
  EXPECT_NEAR(ov1, 2.5f, 1e-4f);
}

}  // namespace
}  // namespace openskp
