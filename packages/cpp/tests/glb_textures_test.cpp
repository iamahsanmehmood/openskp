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

// GLB texture embedding and the material-identity fix it depends on
// (openskp#193, ported from TypeScript).
//
// Before this, gltf_materials was keyed on (color, double_sided) alone, so
// two different textures that happened to average to the same RGB would
// silently collapse into one material and lose an image. Fixed by keying on
// (color, double_sided, texture_index) instead, at both the face-grouping
// and material-dedup layers.
//
// Fixture: capilla_quiroz_v17.skp, which carries 3 real, distinct JPEG
// textures - real coverage, not a synthetic mock.

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

const ByteBuffer kJpegMagic{0xff, 0xd8, 0xff};

TEST(GlbTextures, SceneDeduplicatesTexturesAndKeysMaterialsByThem) {
  const auto scene = SkpFile::open(test::fixture("capilla_quiroz_v17.skp")).build_scene();

  ASSERT_EQ(scene.textures.size(), 3);
  for (const auto& tex : scene.textures) {
    EXPECT_TRUE(tex.mime_type == "image/jpeg" || tex.mime_type == "image/png");
    EXPECT_FALSE(tex.data.empty());
  }

  std::size_t textured = 0;
  for (const auto& mat : scene.gltf_materials) {
    if (mat.pbr_metallic_roughness.base_color_texture.has_value()) {
      ++textured;
      EXPECT_LT(*mat.pbr_metallic_roughness.base_color_texture, scene.textures.size());
    }
  }
  EXPECT_EQ(textured, 4);
}

TEST(GlbTextures, ExportOmitsImagesByDefault) {
  const auto scene = SkpFile::open(test::fixture("capilla_quiroz_v17.skp")).build_scene();
  const auto bytes = to_glb(scene);

  const std::string text(bytes.begin(), bytes.end());
  EXPECT_EQ(text.find("\"images\""), std::string::npos);
  EXPECT_FALSE(contains_bytes(bytes, {0xff, 0xd8, 0xff}));

  const auto model = load_glb(bytes);
  EXPECT_TRUE(model.images.empty());
}

TEST(GlbTextures, ExportEmbedsTexturesWhenAsked) {
  const auto scene = SkpFile::open(test::fixture("capilla_quiroz_v17.skp")).build_scene();
  const auto without_textures = to_glb(scene);
  const auto with_textures = to_glb(scene, GlbOptions{true});

  EXPECT_GT(with_textures.size(), without_textures.size());
  EXPECT_TRUE(contains_bytes(with_textures, {0xff, 0xd8, 0xff}));

  const auto model = load_glb(with_textures);
  ASSERT_EQ(model.images.size(), 3);
  for (const auto& image : model.images) {
    EXPECT_GE(image.bufferView, 0);
    ASSERT_GE(image.mimeType.size(), 6);
    EXPECT_EQ(image.mimeType.substr(0, 6), "image/");
  }
  ASSERT_EQ(model.textures.size(), 3);
  for (const auto& texture : model.textures) {
    EXPECT_GE(texture.source, 0);
  }

  bool found_textured_material = false;
  for (const auto& material : model.materials) {
    if (material.pbrMetallicRoughness.baseColorTexture.index >= 0) {
      found_textured_material = true;
      EXPECT_LT(material.pbrMetallicRoughness.baseColorTexture.index,
                static_cast<int>(model.textures.size()));
    }
  }
  EXPECT_TRUE(found_textured_material);
}

TEST(GlbTextures, ExportGlbFileWritesEmbeddedTextures) {
  const auto scene = SkpFile::open(test::fixture("capilla_quiroz_v17.skp")).build_scene();
  const auto output = std::filesystem::temp_directory_path() / "openskp-cpp-textures-test.glb";
  export_glb(scene, output, GlbOptions{true});

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
