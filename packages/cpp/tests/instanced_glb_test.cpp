#include <algorithm>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <gtest/gtest.h>
#include <initializer_list>
#include <string>

#include <openskp/openskp.hpp>

#define TINYGLTF_NO_STB_IMAGE
#define TINYGLTF_NO_STB_IMAGE_WRITE
#include <tiny_gltf.h>

#include "test_helpers.hpp"

// Instanced GLB export (openskp#200, ported from TypeScript's
// toInstancedGLB()).

namespace openskp {
namespace {

// TinyGLTF is built with TINYGLTF_NO_STB_IMAGE, so it has no default pixel
// decoder and refuses to load a GLB with embedded images unless the caller
// supplies one. These tests only check structure (bufferView, mimeType,
// indices), never pixel data, so a no-op stand-in is enough.
bool skip_image_decode(tinygltf::Image*, const int, std::string*, std::string*, int, int,
                       const unsigned char*, int, void*) {
  return true;
}

tinygltf::Model load_glb(const ByteBuffer& bytes) {
  tinygltf::Model model;
  tinygltf::TinyGLTF loader;
  loader.SetImageLoader(skip_image_decode, nullptr);
  std::string error;
  std::string warning;
  EXPECT_TRUE(loader.LoadBinaryFromMemory(&model, &error, &warning, bytes.data(), bytes.size()))
      << error << warning;
  EXPECT_TRUE(error.empty()) << error;
  return model;
}

bool contains_bytes(const ByteBuffer& haystack, std::initializer_list<std::uint8_t> needle) {
  return std::search(haystack.begin(), haystack.end(), needle.begin(), needle.end()) !=
         haystack.end();
}

TEST(InstancedGlb, SerializesInstancedSceneWithSharedMesh) {
  const auto scene = SkpFile::open(test::fixture("gondola_v20.skp")).build_instanced_scene();
  const auto bytes = to_instanced_glb(scene);
  ASSERT_GE(bytes.size(), 12);
  EXPECT_EQ(std::string(bytes.begin(), bytes.begin() + 4), "glTF");

  const auto model = load_glb(bytes);
  EXPECT_EQ(model.meshes.size(), scene.mesh_resources.size());
  // gondola_v20.skp reuses components heavily: far fewer nodes-with-mesh
  // than instances would require if geometry were duplicated per
  // placement is the actual instancing evidence, checked below via file
  // size instead (structural node/mesh counts alone don't prove reuse as
  // clearly as a size comparison against the baked export).
}

TEST(InstancedGlb, ExportOmitsImagesByDefault) {
  const auto scene = SkpFile::open(test::fixture("capilla_quiroz_v17.skp")).build_instanced_scene();
  const auto bytes = to_instanced_glb(scene);

  const std::string text(bytes.begin(), bytes.end());
  EXPECT_EQ(text.find("\"images\""), std::string::npos);
  EXPECT_FALSE(contains_bytes(bytes, {0xff, 0xd8, 0xff}));

  const auto model = load_glb(bytes);
  EXPECT_TRUE(model.images.empty());
}

TEST(InstancedGlb, ExportEmbedsTexturesWhenAsked) {
  const auto scene = SkpFile::open(test::fixture("capilla_quiroz_v17.skp")).build_instanced_scene();
  const auto without_textures = to_instanced_glb(scene);
  const auto with_textures = to_instanced_glb(scene, InstancedGlbOptions{true});

  EXPECT_GT(with_textures.size(), without_textures.size());
  EXPECT_TRUE(contains_bytes(with_textures, {0xff, 0xd8, 0xff}));

  const auto model = load_glb(with_textures);
  ASSERT_EQ(model.images.size(), 3);
  for (const auto& image : model.images) {
    EXPECT_GE(image.bufferView, 0);
    ASSERT_GE(image.mimeType.size(), 6);
    EXPECT_EQ(image.mimeType.substr(0, 6), "image/");
  }

  bool found_textured_material = false;
  for (const auto& material : model.materials) {
    if (material.pbrMetallicRoughness.baseColorTexture.index >= 0) {
      found_textured_material = true;
    }
  }
  EXPECT_TRUE(found_textured_material);
}

TEST(InstancedGlb, IsSmallerThanTheBakedExportOnAFileWithRepeatedGeometry) {
  const auto baked_scene = SkpFile::open(test::fixture("gondola_v20.skp")).build_scene();
  const auto instanced_scene =
      SkpFile::open(test::fixture("gondola_v20.skp")).build_instanced_scene();

  const auto baked_bytes = to_glb(baked_scene);
  const auto instanced_bytes = to_instanced_glb(instanced_scene);

  EXPECT_LT(instanced_bytes.size(), baked_bytes.size());
}

TEST(InstancedGlb, ExportInstancedGlbFileRoundTrips) {
  const auto scene = SkpFile::open(test::fixture("capilla_quiroz_v17.skp")).build_instanced_scene();
  const auto output =
      std::filesystem::temp_directory_path() / "openskp-cpp-instanced-export-test.glb";
  export_instanced_glb(scene, output, InstancedGlbOptions{true});

  std::ifstream stream(output, std::ios::binary | std::ios::ate);
  ASSERT_TRUE(stream);
  const auto size = stream.tellg();
  ByteBuffer actual(static_cast<std::size_t>(size));
  stream.seekg(0);
  stream.read(reinterpret_cast<char*>(actual.data()), size);
  stream.close();

  EXPECT_TRUE(contains_bytes(actual, {0xff, 0xd8, 0xff}));
  std::filesystem::remove(output);
}

}  // namespace
}  // namespace openskp
