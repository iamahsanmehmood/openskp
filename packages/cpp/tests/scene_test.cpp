#include <gtest/gtest.h>

#include "internal.hpp"

namespace openskp {
namespace {

RawParsed triangle_with_materials(bool distinct_back) {
  RawParsed parsed;
  parsed.root.builder.vertices = {{1, {0, 0, 0}}, {2, {1, 0, 0}}, {3, {0, 1, 0}}};
  parsed.root.builder.edges = {{10, {1, 2}}, {11, {2, 3}}, {12, {3, 1}}};
  RawFace face;
  face.loops = {{{10, 1}, {11, 1}, {12, 1}}};
  face.normal = {0, 0, 1};
  face.material_id = 100;
  face.back_material_id = distinct_back ? std::optional<EntityId>{200} : face.material_id;
  parsed.root.builder.faces.emplace(20, std::move(face));

  auto front = std::make_shared<RawMaterial>();
  front->name = "front";
  front->r = 10;
  front->g = 20;
  front->b = 30;
  front->transparency = 0.5;
  parsed.materials.emplace(front->name, front);
  parsed.material_id_to_name.emplace(100, front->name);

  auto back = std::make_shared<RawMaterial>();
  back->name = "back";
  back->r = 40;
  back->g = 50;
  back->b = 60;
  back->transparency = 0.25;
  parsed.materials.emplace(back->name, back);
  parsed.material_id_to_name.emplace(200, back->name);
  return parsed;
}

double winding_normal_dot(const GlbPrimitive& primitive) {
  const auto first = primitive.indices[0] * 3;
  const auto second = primitive.indices[1] * 3;
  const auto third = primitive.indices[2] * 3;
  Vec3 a{primitive.positions[second] - primitive.positions[first],
         primitive.positions[second + 1] - primitive.positions[first + 1],
         primitive.positions[second + 2] - primitive.positions[first + 2]};
  Vec3 b{primitive.positions[third] - primitive.positions[first],
         primitive.positions[third + 1] - primitive.positions[first + 1],
         primitive.positions[third + 2] - primitive.positions[first + 2]};
  Vec3 cross{a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]};
  return cross[0] * primitive.normals[first] + cross[1] * primitive.normals[first + 1] +
         cross[2] * primitive.normals[first + 2];
}

TEST(Scene, UsesOneDoubleSidedPrimitiveWhenFaceMaterialsMatch) {
  auto scene = build_scene_raw(triangle_with_materials(false), {});
  ASSERT_EQ(scene.glb_primitives.size(), 1);
  ASSERT_EQ(scene.gltf_materials.size(), 1);
  EXPECT_TRUE(scene.gltf_materials[0].double_sided);
  EXPECT_DOUBLE_EQ(scene.gltf_materials[0].pbr_metallic_roughness.base_color_factor[3],
                   128.0 / 255.0);
}

TEST(Scene, PreservesDistinctFrontAndBackFaceMaterials) {
  auto scene = build_scene_raw(triangle_with_materials(true), {});
  ASSERT_EQ(scene.glb_primitives.size(), 2);
  ASSERT_EQ(scene.gltf_materials.size(), 2);

  bool found_front = false;
  bool found_back = false;
  for (const auto& primitive : scene.glb_primitives) {
    const auto& material = scene.gltf_materials.at(primitive.material_index);
    EXPECT_FALSE(material.double_sided);
    ASSERT_GE(primitive.normals.size(), 2);
    if (material.pbr_metallic_roughness.base_color_factor[0] == 10.0 / 255.0) {
      found_front = true;
      EXPECT_GT(primitive.normals[1], 0);
      EXPECT_DOUBLE_EQ(material.pbr_metallic_roughness.base_color_factor[3], 128.0 / 255.0);
    } else if (material.pbr_metallic_roughness.base_color_factor[0] == 40.0 / 255.0) {
      found_back = true;
      EXPECT_LT(primitive.normals[1], 0);
      EXPECT_DOUBLE_EQ(material.pbr_metallic_roughness.base_color_factor[3], 64.0 / 255.0);
    }
  }
  EXPECT_TRUE(found_front);
  EXPECT_TRUE(found_back);
}

// A material's overall opacity can come from either of two independent
// SketchUp mechanisms: the plain RGBA color record's alpha byte
// (RawMaterial::a), or the newer XML material definition's own
// trans/useTrans attribute (RawMaterial::transparency, already exercised by
// UsesOneDoubleSidedPrimitiveWhenFaceMaterialsMatch above). Before this fix,
// material_color() only read transparency - a's default (255, fully
// opaque) meant a translucent material written via the raw color byte (as
// SkpBuilder::add_material's 4-channel overload does) lost its alpha
// entirely once baked into the scene.
RawParsed triangle_with_raw_alpha(int alpha) {
  RawParsed parsed;
  parsed.root.builder.vertices = {{1, {0, 0, 0}}, {2, {1, 0, 0}}, {3, {0, 1, 0}}};
  parsed.root.builder.edges = {{10, {1, 2}}, {11, {2, 3}}, {12, {3, 1}}};
  RawFace face;
  face.loops = {{{10, 1}, {11, 1}, {12, 1}}};
  face.normal = {0, 0, 1};
  face.material_id = 100;
  face.back_material_id = face.material_id;
  parsed.root.builder.faces.emplace(20, std::move(face));

  auto mat = std::make_shared<RawMaterial>();
  mat->name = "glass";
  mat->r = 40;
  mat->g = 70;
  mat->b = 100;
  mat->a = alpha;
  // transparency left at its default (1.0) - this material's opacity comes
  // entirely from the raw color alpha byte, not the XML mechanism.
  parsed.materials.emplace(mat->name, mat);
  parsed.material_id_to_name.emplace(100, mat->name);
  return parsed;
}

TEST(Scene, PropagatesRawColorAlphaByteIntoBaseColorFactor) {
  auto scene = build_scene_raw(triangle_with_raw_alpha(128), {});
  ASSERT_EQ(scene.gltf_materials.size(), 1);
  EXPECT_NEAR(scene.gltf_materials[0].pbr_metallic_roughness.base_color_factor[3], 128.0 / 255.0,
              1.0 / 255.0);
}

TEST(Scene, LeavesFullyOpaqueRawAlphaByteForByteUnchanged) {
  auto scene = build_scene_raw(triangle_with_raw_alpha(255), {});
  ASSERT_EQ(scene.gltf_materials.size(), 1);
  EXPECT_DOUBLE_EQ(scene.gltf_materials[0].pbr_metallic_roughness.base_color_factor[3], 1.0);
}

TEST(Scene, KeepsWindingAndNormalsAlignedForMirroredInstances) {
  auto parsed = triangle_with_materials(true);
  RawDefinition definition;
  definition.name = "mirrored triangle";
  definition.builder = std::move(parsed.root.builder);
  parsed.definitions.emplace(1, std::move(definition));
  RawInstance instance;
  instance.ref_idx = 1;
  instance.matrix = {-2, 0, 0, 0, 3, 0, 0, 0, 4, 0, 0, 0, 1};
  parsed.root.builder.instances.push_back(std::move(instance));

  auto scene = build_scene_raw(std::move(parsed), {});
  ASSERT_EQ(scene.glb_primitives.size(), 2);
  for (const auto& primitive : scene.glb_primitives) EXPECT_GT(winding_normal_dot(primitive), 0);
}

}  // namespace
}  // namespace openskp
