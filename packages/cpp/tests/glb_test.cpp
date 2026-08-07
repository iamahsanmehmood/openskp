#include <cmath>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <gtest/gtest.h>
#include <limits>
#include <string>

#include <openskp/openskp.hpp>

#define TINYGLTF_IMPLEMENTATION
#define TINYGLTF_NO_STB_IMAGE
#define TINYGLTF_NO_STB_IMAGE_WRITE
#include <tiny_gltf.h>

#include "test_helpers.hpp"

namespace openskp {
namespace {

Scene triangle_scene() {
  Scene scene;
  scene.gltf_materials.push_back({{{0.25, 0.5, 0.75, 1.0}, 0.1, 0.9}});
  scene.glb_primitives.push_back({
      {1.0F, 2.0F, 3.0F, -4.0F, 5.0F, 0.0F, 2.0F, -1.0F, 7.0F},
      {0.0F, 0.0F, 1.0F, 0.0F, 0.0F, 1.0F, 0.0F, 0.0F, 1.0F},
      {0, 1, 2},
      0,
      "triangle",
  });
  return scene;
}

tinygltf::Model load_glb(const ByteBuffer& bytes) {
  tinygltf::Model model;
  tinygltf::TinyGLTF loader;
  std::string error;
  std::string warning;
  EXPECT_TRUE(loader.LoadBinaryFromMemory(&model, &error, &warning, bytes.data(), bytes.size()))
      << error << warning;
  EXPECT_TRUE(error.empty()) << error;
  return model;
}

template <typename T>
T buffer_value(const tinygltf::Model& model, const tinygltf::Accessor& accessor,
               std::size_t index) {
  const auto& view = model.bufferViews.at(static_cast<std::size_t>(accessor.bufferView));
  T value{};
  std::memcpy(&value,
              model.buffers.at(static_cast<std::size_t>(view.buffer)).data.data() +
                  view.byteOffset + accessor.byteOffset + index * sizeof(T),
              sizeof(T));
  return value;
}

TEST(Glb, SerializesSceneAndBinaryData) {
  const auto bytes = to_glb(triangle_scene());
  ASSERT_GE(bytes.size(), 12);
  EXPECT_EQ(std::string(bytes.begin(), bytes.begin() + 4), "glTF");

  const auto model = load_glb(bytes);
  EXPECT_EQ(model.defaultScene, 0);
  ASSERT_EQ(model.scenes.size(), 1);
  ASSERT_EQ(model.scenes[0].nodes, (std::vector<int>{0}));
  ASSERT_EQ(model.nodes.size(), 1);
  EXPECT_EQ(model.nodes[0].mesh, 0);
  ASSERT_EQ(model.meshes.size(), 1);
  ASSERT_EQ(model.meshes[0].primitives.size(), 1);

  const auto& primitive = model.meshes[0].primitives[0];
  EXPECT_EQ(primitive.mode, TINYGLTF_MODE_TRIANGLES);
  EXPECT_EQ(primitive.material, 0);
  ASSERT_TRUE(primitive.attributes.count("POSITION"));
  ASSERT_TRUE(primitive.attributes.count("NORMAL"));

  const auto& positions = model.accessors.at(primitive.attributes.at("POSITION"));
  EXPECT_EQ(positions.componentType, TINYGLTF_COMPONENT_TYPE_FLOAT);
  EXPECT_EQ(positions.type, TINYGLTF_TYPE_VEC3);
  EXPECT_EQ(positions.count, 3);
  EXPECT_EQ(positions.minValues, (std::vector<double>{-4.0, -1.0, 0.0}));
  EXPECT_EQ(positions.maxValues, (std::vector<double>{2.0, 5.0, 7.0}));
  EXPECT_FLOAT_EQ(buffer_value<float>(model, positions, 0), 1.0F);
  EXPECT_FLOAT_EQ(buffer_value<float>(model, positions, 4), 5.0F);

  const auto& normals = model.accessors.at(primitive.attributes.at("NORMAL"));
  EXPECT_EQ(normals.count, 3);
  EXPECT_FLOAT_EQ(buffer_value<float>(model, normals, 8), 1.0F);

  const auto& indices = model.accessors.at(static_cast<std::size_t>(primitive.indices));
  EXPECT_EQ(indices.componentType, TINYGLTF_COMPONENT_TYPE_UNSIGNED_INT);
  EXPECT_EQ(indices.type, TINYGLTF_TYPE_SCALAR);
  EXPECT_EQ(indices.count, 3);
  EXPECT_EQ(buffer_value<std::uint32_t>(model, indices, 2), 2U);

  ASSERT_EQ(model.materials.size(), 1);
  const auto& pbr = model.materials[0].pbrMetallicRoughness;
  EXPECT_EQ(pbr.baseColorFactor, (std::vector<double>{0.25, 0.5, 0.75, 1.0}));
  EXPECT_DOUBLE_EQ(pbr.metallicFactor, 0.1);
  EXPECT_DOUBLE_EQ(pbr.roughnessFactor, 0.9);
  EXPECT_EQ(model.materials[0].alphaMode, "OPAQUE");
}

TEST(Glb, SerializesAnEmptyScene) {
  const auto model = load_glb(to_glb({}));
  ASSERT_EQ(model.scenes.size(), 1);
  EXPECT_TRUE(model.scenes[0].nodes.empty());
  EXPECT_TRUE(model.meshes.empty());
  EXPECT_TRUE(model.buffers.empty());
}

TEST(Glb, RejectsMalformedGeometry) {
  auto scene = triangle_scene();
  scene.glb_primitives[0].positions.pop_back();
  EXPECT_THROW(to_glb(scene), std::invalid_argument);

  scene = triangle_scene();
  scene.glb_primitives[0].normals.pop_back();
  EXPECT_THROW(to_glb(scene), std::invalid_argument);

  scene = triangle_scene();
  scene.glb_primitives[0].indices.pop_back();
  EXPECT_THROW(to_glb(scene), std::invalid_argument);

  scene = triangle_scene();
  scene.glb_primitives[0].indices[2] = 3;
  EXPECT_THROW(to_glb(scene), std::invalid_argument);

  scene = triangle_scene();
  scene.glb_primitives[0].material_index = 1;
  EXPECT_THROW(to_glb(scene), std::invalid_argument);
}

TEST(Glb, RejectsNonFiniteAndInvalidPbrValues) {
  auto scene = triangle_scene();
  scene.glb_primitives[0].positions[0] = std::numeric_limits<float>::infinity();
  EXPECT_THROW(to_glb(scene), std::invalid_argument);

  scene = triangle_scene();
  scene.glb_primitives[0].normals[0] = std::numeric_limits<float>::quiet_NaN();
  EXPECT_THROW(to_glb(scene), std::invalid_argument);

  scene = triangle_scene();
  scene.gltf_materials[0].pbr_metallic_roughness.base_color_factor[0] = -0.1;
  EXPECT_THROW(to_glb(scene), std::invalid_argument);

  scene = triangle_scene();
  scene.gltf_materials[0].pbr_metallic_roughness.metallic_factor = 1.1;
  EXPECT_THROW(to_glb(scene), std::invalid_argument);

  scene = triangle_scene();
  scene.gltf_materials[0].pbr_metallic_roughness.roughness_factor =
      std::numeric_limits<double>::quiet_NaN();
  EXPECT_THROW(to_glb(scene), std::invalid_argument);
}

TEST(Glb, ExportsRealFixtureByteForByte) {
  const auto scene = SkpFile::open(test::fixture("SU_File.skp")).build_scene();
  const auto expected = to_glb(scene);
  const auto output = std::filesystem::temp_directory_path() / "openskp-cpp-export-test.bin";
  export_glb(scene, output);

  std::ifstream stream(output, std::ios::binary | std::ios::ate);
  ASSERT_TRUE(stream);
  const auto size = stream.tellg();
  ByteBuffer actual(static_cast<std::size_t>(size));
  stream.seekg(0);
  stream.read(reinterpret_cast<char*>(actual.data()), size);
  EXPECT_EQ(actual, expected);
  const auto model = load_glb(actual);
  EXPECT_EQ(model.meshes[0].primitives.size(), scene.glb_primitives.size());
  std::filesystem::remove(output);
}

TEST(Glb, ReportsFileFailuresWithoutCreatingDirectories) {
  const auto output =
      std::filesystem::temp_directory_path() / "openskp-missing-parent" / "asset.any-extension";
  EXPECT_THROW(export_glb(triangle_scene(), output), std::runtime_error);
  EXPECT_FALSE(std::filesystem::exists(output.parent_path()));
}

}  // namespace
}  // namespace openskp
