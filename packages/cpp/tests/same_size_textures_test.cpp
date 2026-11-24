#include <algorithm>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <gtest/gtest.h>
#include <vector>

#include <openskp/openskp.hpp>

namespace openskp {
namespace {

const ByteBuffer kPngRed{
    0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44,
    0x52, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x02, 0x00, 0x00, 0x00, 0x90,
    0x77, 0x53, 0xde, 0x00, 0x00, 0x00, 0x0f, 0x49, 0x44, 0x41, 0x54, 0x78, 0x01, 0x01, 0x04,
    0x00, 0xfb, 0xff, 0x00, 0xdc, 0x28, 0x1e, 0x03, 0x06, 0x01, 0x23, 0xe0, 0x4f, 0x0f, 0xcc,
    0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82,
};

const ByteBuffer kPngBlue{
    0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44,
    0x52, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x02, 0x00, 0x00, 0x00, 0x90,
    0x77, 0x53, 0xde, 0x00, 0x00, 0x00, 0x0f, 0x49, 0x44, 0x41, 0x54, 0x78, 0x01, 0x01, 0x04,
    0x00, 0xfb, 0xff, 0x00, 0x1e, 0x5a, 0xdc, 0x01, 0xee, 0x01, 0x55, 0xda, 0x6c, 0x0b, 0x85,
    0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82,
};

void write_bytes(const std::filesystem::path& path, const ByteBuffer& data) {
  std::ofstream out(path, std::ios::binary);
  ASSERT_TRUE(out);
  out.write(reinterpret_cast<const char*>(data.data()), static_cast<std::streamsize>(data.size()));
}

std::vector<std::size_t> textured_indices(const std::vector<GltfMaterial>& materials) {
  std::vector<std::size_t> out;
  for (const auto& m : materials) {
    if (m.pbr_metallic_roughness.base_color_texture) {
      out.push_back(*m.pbr_metallic_roughness.base_color_texture);
    }
  }
  return out;
}

TEST(SameSizeTextures, TwoDifferentPngsOfEqualLengthGiveTwoTextures) {
  ASSERT_EQ(kPngRed.size(), kPngBlue.size());
  ASSERT_TRUE(std::equal(kPngRed.begin(), kPngRed.begin() + 16, kPngBlue.begin()));

  const auto dir = std::filesystem::temp_directory_path() / "openskp-cpp-645-distinct";
  std::filesystem::create_directories(dir);
  const auto path_a = dir / "a.png";
  const auto path_b = dir / "b.png";
  write_bytes(path_a, kPngRed);
  write_bytes(path_b, kPngBlue);

  auto b = create();
  const int mat_a = b->add_texture_material("MatA", path_a);
  const int mat_b = b->add_texture_material("MatB", path_b);
  FaceOptions fa;
  fa.material = mat_a;
  FaceOptions fb;
  fb.material = mat_b;
  b->add_face({{0, 0, 0}, {10, 0, 0}, {10, 10, 0}, {0, 10, 0}}, fa);
  b->add_face({{15, 0, 0}, {25, 0, 0}, {25, 10, 0}, {15, 10, 0}}, fa);
  b->add_face({{30, 0, 0}, {40, 0, 0}, {40, 10, 0}, {30, 10, 0}}, fb);

  const auto skp = SkpFile::from_buffer(b->to_bytes());
  for (bool instanced : {false, true}) {
    std::vector<SceneTexture> textures;
    std::vector<GltfMaterial> mats;
    if (instanced) {
      auto s = skp.build_instanced_scene();
      textures = std::move(s.textures);
      mats = std::move(s.gltf_materials);
    } else {
      auto s = skp.build_scene();
      textures = std::move(s.textures);
      mats = std::move(s.gltf_materials);
    }
    ASSERT_EQ(textures.size(), 2u);
    EXPECT_EQ(textures[0].data, kPngRed);
    EXPECT_EQ(textures[1].data, kPngBlue);
    auto indices = textured_indices(mats);
    std::sort(indices.begin(), indices.end());
    EXPECT_EQ(indices, (std::vector<std::size_t>{0, 1}));
  }

  std::error_code ec;
  std::filesystem::remove_all(dir, ec);
}

TEST(SameSizeTextures, IdenticalPngBytesInTwoMaterialsGiveOneTexture) {
  const auto dir = std::filesystem::temp_directory_path() / "openskp-cpp-645-dup";
  std::filesystem::create_directories(dir);
  const auto path_1 = dir / "one.png";
  const auto path_2 = dir / "two.png";
  write_bytes(path_1, kPngRed);
  write_bytes(path_2, kPngRed);

  auto b = create();
  const int mat_1 = b->add_texture_material("Mat1", path_1);
  const int mat_2 = b->add_texture_material("Mat2", path_2, std::nullopt, std::nullopt, 0.6);
  FaceOptions f1;
  f1.material = mat_1;
  FaceOptions f2;
  f2.material = mat_2;
  b->add_face({{0, 0, 0}, {10, 0, 0}, {10, 10, 0}, {0, 10, 0}}, f1);
  b->add_face({{20, 0, 0}, {30, 0, 0}, {30, 10, 0}, {20, 10, 0}}, f2);

  const auto skp = SkpFile::from_buffer(b->to_bytes());
  for (bool instanced : {false, true}) {
    std::vector<SceneTexture> textures;
    std::vector<GltfMaterial> mats;
    if (instanced) {
      auto s = skp.build_instanced_scene();
      textures = std::move(s.textures);
      mats = std::move(s.gltf_materials);
    } else {
      auto s = skp.build_scene();
      textures = std::move(s.textures);
      mats = std::move(s.gltf_materials);
    }
    ASSERT_EQ(textures.size(), 1u);
    EXPECT_EQ(textures[0].data, kPngRed);
    EXPECT_EQ(textured_indices(mats), (std::vector<std::size_t>{0, 0}));
  }

  std::error_code ec;
  std::filesystem::remove_all(dir, ec);
}

}  // namespace
}  // namespace openskp
