#include <cmath>
#include <gtest/gtest.h>
#include <map>
#include <set>
#include <string>
#include <tuple>
#include <vector>

#include <openskp/openskp.hpp>

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

}  // namespace
}  // namespace openskp
