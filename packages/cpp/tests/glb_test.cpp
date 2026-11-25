#include <cmath>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <gtest/gtest.h>
#include <limits>
#include <string>

#include <openskp/instanced_glb.hpp>
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
  scene.gltf_materials.push_back({"Material_0", "", {{0.25, 0.5, 0.75, 1.0}, 0.1, 0.9}, true});
  scene.glb_primitives.push_back({
      {1.0F, 2.0F, 3.0F, -4.0F, 5.0F, 0.0F, 2.0F, -1.0F, 7.0F},
      {0.0F, 0.0F, 1.0F, 0.0F, 0.0F, 1.0F, 0.0F, 0.0F, 1.0F},
      {0.0F, 0.0F, 1.0F, 0.0F, 0.0F, 1.0F},
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
  ASSERT_TRUE(primitive.attributes.count("TEXCOORD_0"));

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

  const auto& uvs = model.accessors.at(primitive.attributes.at("TEXCOORD_0"));
  EXPECT_EQ(uvs.componentType, TINYGLTF_COMPONENT_TYPE_FLOAT);
  EXPECT_EQ(uvs.type, TINYGLTF_TYPE_VEC2);
  EXPECT_EQ(uvs.count, 3);
  EXPECT_FLOAT_EQ(buffer_value<float>(model, uvs, 2), 1.0F);

  const auto& indices = model.accessors.at(static_cast<std::size_t>(primitive.indices));
  EXPECT_EQ(indices.componentType, TINYGLTF_COMPONENT_TYPE_UNSIGNED_INT);
  EXPECT_EQ(indices.type, TINYGLTF_TYPE_SCALAR);
  EXPECT_EQ(indices.count, 3);
  EXPECT_EQ(buffer_value<std::uint32_t>(model, indices, 2), 2U);

  ASSERT_EQ(model.materials.size(), 1);
  const auto& pbr = model.materials[0].pbrMetallicRoughness;
  const auto srgb_to_linear = [](double c) {
    return c <= 0.04045 ? c / 12.92 : std::pow((c + 0.055) / 1.055, 2.4);
  };
  ASSERT_EQ(pbr.baseColorFactor.size(), 4U);
  EXPECT_NEAR(pbr.baseColorFactor[0], srgb_to_linear(0.25), 1e-12);
  EXPECT_NEAR(pbr.baseColorFactor[1], srgb_to_linear(0.5), 1e-12);
  EXPECT_NEAR(pbr.baseColorFactor[2], srgb_to_linear(0.75), 1e-12);
  EXPECT_DOUBLE_EQ(pbr.baseColorFactor[3], 1.0);
  EXPECT_DOUBLE_EQ(pbr.metallicFactor, 0.1);
  EXPECT_DOUBLE_EQ(pbr.roughnessFactor, 0.9);
  EXPECT_EQ(model.materials[0].alphaMode, "OPAQUE");
  EXPECT_TRUE(model.materials[0].doubleSided);
}

TEST(Glb, DeclaresBlendAlphaModeForTranslucentMaterials) {
  Scene scene;
  scene.gltf_materials.push_back({"Glass", "", {{0.16, 0.27, 0.39, 0.5}, 0.0, 0.8}, false});
  scene.glb_primitives.push_back({
      {0.0F, 0.0F, 0.0F, 1.0F, 0.0F, 0.0F, 0.0F, 1.0F, 0.0F},
      {0.0F, 0.0F, 1.0F, 0.0F, 0.0F, 1.0F, 0.0F, 0.0F, 1.0F},
      {0.0F, 0.0F, 1.0F, 0.0F, 0.0F, 1.0F},
      {0, 1, 2},
      0,
      "triangle",
  });

  const auto model = load_glb(to_glb(scene));
  ASSERT_EQ(model.materials.size(), 1);
  EXPECT_EQ(model.materials[0].alphaMode, "BLEND");
}

TEST(Glb, DeclaresMaskAlphaModeForTexturedOpaqueMaterials) {
  // glTF's default alphaMode is OPAQUE, which tells a conformant renderer
  // to ignore a texture's own alpha channel entirely. A textured material
  // whose baseColorFactor alpha is 1.0 (SketchUp Warehouse foliage/fence/
  // signage cutouts commonly look like this) needs MASK declared instead,
  // or the image's transparent regions render as a solid rectangle.
  Scene scene;
  GltfMaterial material{"Leaf", "", {{0.2, 0.5, 0.1, 1.0}, 0.0, 0.8}, false};
  material.pbr_metallic_roughness.base_color_texture = std::size_t{0};
  scene.gltf_materials.push_back(material);
  scene.textures.push_back(
      {ByteBuffer{0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a}, "image/png", "leaf.png"});
  scene.glb_primitives.push_back({
      {0.0F, 0.0F, 0.0F, 1.0F, 0.0F, 0.0F, 0.0F, 1.0F, 0.0F},
      {0.0F, 0.0F, 1.0F, 0.0F, 0.0F, 1.0F, 0.0F, 0.0F, 1.0F},
      {0.0F, 0.0F, 1.0F, 0.0F, 0.0F, 1.0F},
      {0, 1, 2},
      0,
      "triangle",
  });

  const auto model = load_glb(to_glb(scene));
  ASSERT_EQ(model.materials.size(), 1);
  EXPECT_EQ(model.materials[0].alphaMode, "MASK");
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
  scene.glb_primitives[0].uvs.pop_back();
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
  scene.glb_primitives[0].uvs[0] = std::numeric_limits<float>::quiet_NaN();
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
  stream.close();
  ASSERT_TRUE(stream);
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

TEST(Glb, ConvertsSrgbBaseColorFactorToLinearInBakedAndInstancedExports) {
  const auto srgb_to_linear = [](double c) {
    return c <= 0.04045 ? c / 12.92 : std::pow((c + 0.055) / 1.055, 2.4);
  };

  std::vector<GltfMaterial> mats{
      {"LayerRed", "", {{1.0, 84.0 / 255.0, 84.0 / 255.0, 1.0}, 0.0, 0.8}, false},
      {"White", "", {{1.0, 1.0, 1.0, 1.0}, 0.0, 0.8}, false},
      {"Black", "", {{0.0, 0.0, 0.0, 1.0}, 0.0, 0.8}, false},
      {"MidGrey", "", {{188.0 / 255.0, 188.0 / 255.0, 188.0 / 255.0, 1.0}, 0.0, 0.8}, false},
      {"DarkToe", "", {{10.0 / 255.0, 10.0 / 255.0, 10.0 / 255.0, 128.0 / 255.0}, 0.0, 0.8}, false},
  };

  Scene baked;
  baked.gltf_materials = mats;
  for (std::size_t i = 0; i < mats.size(); ++i) {
    baked.glb_primitives.push_back({
        {0.0F, 0.0F, 0.0F, 1.0F, 0.0F, 0.0F, 0.0F, 1.0F, 0.0F},
        {0.0F, 0.0F, 1.0F, 0.0F, 0.0F, 1.0F, 0.0F, 0.0F, 1.0F},
        {0.0F, 0.0F, 1.0F, 0.0F, 0.0F, 1.0F},
        {0, 1, 2},
        i,
        "tri_" + std::to_string(i),
    });
  }

  InstancedScene instanced;
  instanced.gltf_materials = mats;
  InstancedMeshResource res;
  res.id = "res_0";
  res.definition_name = "Def";
  for (std::size_t i = 0; i < mats.size(); ++i) {
    res.primitives.push_back({
        {0.0F, 0.0F, 0.0F, 1.0F, 0.0F, 0.0F, 0.0F, 1.0F, 0.0F},
        {0.0F, 0.0F, 1.0F, 0.0F, 0.0F, 1.0F, 0.0F, 0.0F, 1.0F},
        {0.0F, 0.0F, 1.0F, 0.0F, 0.0F, 1.0F},
        {0, 1, 2},
        i,
        false,
    });
  }
  instanced.mesh_resources.push_back(std::move(res));
  instanced.scene_hierarchy.name = "ROOT";
  instanced.scene_hierarchy.mesh_resource_id = "res_0";

  for (const auto& bytes : {to_glb(baked), to_instanced_glb(instanced)}) {
    const auto model = load_glb(bytes);
    ASSERT_EQ(model.materials.size(), 5U);

    const auto& red = model.materials[0].pbrMetallicRoughness.baseColorFactor;
    EXPECT_DOUBLE_EQ(red[0], 1.0);
    EXPECT_NEAR(red[1], 0.0887, 1e-4);
    EXPECT_NEAR(red[2], 0.0887, 1e-4);
    EXPECT_DOUBLE_EQ(red[3], 1.0);

    const auto& white = model.materials[1].pbrMetallicRoughness.baseColorFactor;
    EXPECT_EQ(white, (std::vector<double>{1.0, 1.0, 1.0, 1.0}));

    const auto& black = model.materials[2].pbrMetallicRoughness.baseColorFactor;
    EXPECT_EQ(black, (std::vector<double>{0.0, 0.0, 0.0, 1.0}));

    const auto& mid = model.materials[3].pbrMetallicRoughness.baseColorFactor;
    EXPECT_NEAR(mid[0], 0.503, 1e-3);
    EXPECT_NEAR(mid[0], srgb_to_linear(188.0 / 255.0), 1e-12);

    const auto& toe = model.materials[4].pbrMetallicRoughness.baseColorFactor;
    EXPECT_NEAR(toe[0], (10.0 / 255.0) / 12.92, 1e-12);
    EXPECT_NEAR(toe[3], 128.0 / 255.0, 1e-12);
  }

  // Scene gltf_materials remain sRGB.
  EXPECT_DOUBLE_EQ(baked.gltf_materials[0].pbr_metallic_roughness.base_color_factor[1],
                   84.0 / 255.0);
  EXPECT_DOUBLE_EQ(baked.gltf_materials[3].pbr_metallic_roughness.base_color_factor[0],
                   188.0 / 255.0);

  // Plain textured material with embedded image keeps its baseColorFactor unchanged.
  Scene tex_scene;
  GltfMaterial tex_mat{
      "PlainTex", "", {{138.0 / 255.0, 64.0 / 255.0, 36.0 / 255.0, 1.0}, 0.0, 0.8}, false};
  tex_mat.pbr_metallic_roughness.base_color_texture = std::size_t{0};
  tex_scene.gltf_materials.push_back(tex_mat);
  tex_scene.textures.push_back(
      {ByteBuffer{0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a}, "image/png", "tile.png"});
  tex_scene.glb_primitives.push_back(baked.glb_primitives[0]);
  tinygltf::TinyGLTF loader;
  loader.SetImageLoader([](tinygltf::Image*, const int, std::string*, std::string*, int, int,
                           const unsigned char*, int, void*) { return true; },
                        nullptr);
  tinygltf::Model tex_model;
  std::string err;
  std::string warn;
  const auto tex_bytes = to_glb(tex_scene, GlbOptions{true});
  ASSERT_TRUE(
      loader.LoadBinaryFromMemory(&tex_model, &err, &warn, tex_bytes.data(), tex_bytes.size()));
  const auto& tex_bcf = tex_model.materials[0].pbrMetallicRoughness.baseColorFactor;
  EXPECT_NEAR(tex_bcf[0], 138.0 / 255.0, 1e-12);
  EXPECT_NEAR(tex_bcf[1], 64.0 / 255.0, 1e-12);
  EXPECT_NEAR(tex_bcf[2], 36.0 / 255.0, 1e-12);
}

}  // namespace
}  // namespace openskp
