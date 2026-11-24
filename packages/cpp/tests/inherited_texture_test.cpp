#include <algorithm>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <gtest/gtest.h>
#include <string>
#include <utility>
#include <vector>

#include <openskp/openskp.hpp>

#define TINYGLTF_NO_STB_IMAGE
#define TINYGLTF_NO_STB_IMAGE_WRITE
#include <tiny_gltf.h>

namespace openskp {
namespace {

// Minimal valid 1x1 and 2x1 RGB PNGs (distinct byte lengths so texture dedup
// treats them as two distinct images).
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

void write_bytes(const std::filesystem::path& path, const ByteBuffer& data) {
  std::ofstream out(path, std::ios::binary);
  ASSERT_TRUE(out);
  out.write(reinterpret_cast<const char*>(data.data()), static_cast<std::streamsize>(data.size()));
}

struct TempTextureFiles {
  std::filesystem::path dir;
  std::filesystem::path brick;
  std::filesystem::path tile;

  TempTextureFiles() {
    dir = std::filesystem::temp_directory_path() / "openskp-cpp-inherited-tex-test";
    std::filesystem::create_directories(dir);
    brick = dir / "brick.png";
    tile = dir / "tile.png";
    write_bytes(brick, kPng1x1);
    write_bytes(tile, kPng2x1);
  }

  ~TempTextureFiles() {
    std::error_code ec;
    std::filesystem::remove_all(dir, ec);
  }
};

struct UvBounds {
  float min_u;
  float max_u;
  float min_v;
  float max_v;
};

UvBounds uv_bounds(const std::vector<float>& uvs) {
  UvBounds b{uvs.at(0), uvs.at(0), uvs.at(1), uvs.at(1)};
  for (std::size_t i = 0; i + 1 < uvs.size(); i += 2) {
    b.min_u = std::min(b.min_u, uvs[i]);
    b.max_u = std::max(b.max_u, uvs[i]);
    b.min_v = std::min(b.min_v, uvs[i + 1]);
    b.max_v = std::max(b.max_v, uvs[i + 1]);
  }
  return b;
}

SkpFile build_inherited_texture_model(const TempTextureFiles& files) {
  auto builder = create();
  const int brick = builder->add_texture_material("Brick", files.brick, 6.0, 12.0);
  const int tile = builder->add_texture_material("Tile", files.tile, 3.0, 4.0);
  const int accent = builder->add_material("Accent", Color3{10, 20, 30});

  auto& panel = builder->add_component_definition("Panel");
  panel.add_face({{0, 0, 0}, {24, 0, 0}, {24, 12, 0}, {0, 12, 0}});
  panel.close();

  auto& wrapper = builder->add_component_definition("Wrapper");
  wrapper.add_instance(panel);
  wrapper.close();

  auto& mixed = builder->add_component_definition("MixedPanel");
  mixed.add_face({{0, 0, 0}, {24, 0, 0}, {24, 12, 0}, {0, 12, 0}});
  FaceOptions accent_front;
  accent_front.material = accent;
  mixed.add_face({{0, 20, 0}, {10, 20, 0}, {10, 30, 0}, {0, 30, 0}}, accent_front);
  mixed.close();

  InstanceOptions brick_opts;
  brick_opts.name = "BrickPanel";
  brick_opts.material = brick;
  builder->add_instance(panel, brick_opts);

  InstanceOptions tile_opts;
  tile_opts.name = "TilePanel";
  tile_opts.translation = {30, 0, 0};
  tile_opts.material = tile;
  builder->add_instance(panel, tile_opts);

  InstanceOptions nested_opts;
  nested_opts.name = "NestedBrick";
  nested_opts.translation = {60, 0, 0};
  nested_opts.material = brick;
  builder->add_instance(wrapper, nested_opts);

  InstanceOptions mixed_opts;
  mixed_opts.name = "MixedBrick";
  mixed_opts.translation = {90, 0, 0};
  mixed_opts.material = brick;
  builder->add_instance(mixed, mixed_opts);

  return SkpFile::from_buffer(builder->to_bytes());
}

TEST(InheritedTextures, BakedAndInstancedScenesInheritTextureUvsAndAlphaMode) {
  TempTextureFiles files;
  const auto skp = build_inherited_texture_model(files);
  const auto scene = skp.build_scene();
  const auto instanced = skp.build_instanced_scene();

  ASSERT_EQ(scene.textures.size(), 2u);
  EXPECT_EQ(scene.textures[0].mime_type, "image/png");
  EXPECT_EQ(scene.textures[0].data, kPng1x1);
  EXPECT_EQ(scene.textures[1].mime_type, "image/png");
  EXPECT_EQ(scene.textures[1].data, kPng2x1);

  ASSERT_EQ(scene.glb_primitives.size(), 6u);
  for (const auto& prim : scene.glb_primitives) {
    EXPECT_FALSE(prim.unpainted_back);
    const auto& mat = scene.gltf_materials.at(prim.material_index);
    const auto uvs = uv_bounds(prim.uvs);
    if (prim.geom_name.find("BrickPanel") != std::string::npos ||
        prim.geom_name.find("NestedBrick") != std::string::npos) {
      EXPECT_EQ(mat.pbr_metallic_roughness.base_color_texture, std::optional<std::size_t>{0});
      EXPECT_NEAR(uvs.min_u, 0.0f, 1e-5f);
      EXPECT_NEAR(uvs.max_u, 2.0f, 1e-5f);
      EXPECT_NEAR(uvs.min_v, 0.0f, 1e-5f);
      EXPECT_NEAR(uvs.max_v, 2.0f, 1e-5f);
    } else if (prim.geom_name.find("TilePanel") != std::string::npos) {
      EXPECT_EQ(mat.pbr_metallic_roughness.base_color_texture, std::optional<std::size_t>{1});
      EXPECT_NEAR(uvs.min_u, 0.0f, 1e-5f);
      EXPECT_NEAR(uvs.max_u, 6.0f, 1e-5f);
      EXPECT_NEAR(uvs.min_v, 0.0f, 1e-5f);
      EXPECT_NEAR(uvs.max_v, 4.0f, 1e-5f);
    } else if (mat.double_sided) {
      // Unpainted face inside MixedPanel inherits Brick on both sides.
      EXPECT_EQ(mat.pbr_metallic_roughness.base_color_texture, std::optional<std::size_t>{0});
      EXPECT_NEAR(uvs.max_u, 2.0f, 1e-5f);
      EXPECT_NEAR(uvs.max_v, 2.0f, 1e-5f);
    } else if (!mat.pbr_metallic_roughness.base_color_texture.has_value()) {
      // Explicitly painted front keeps Accent's 1-inch default UV scale.
      EXPECT_NEAR(uvs.min_u, 0.0f, 1e-5f);
      EXPECT_NEAR(uvs.max_u, 10.0f, 1e-5f);
      EXPECT_NEAR(uvs.min_v, 20.0f, 1e-5f);
      EXPECT_NEAR(uvs.max_v, 30.0f, 1e-5f);
    } else {
      // Unpainted back of Accent-painted face inherits Brick (12x6 tile).
      EXPECT_EQ(mat.pbr_metallic_roughness.base_color_texture, std::optional<std::size_t>{0});
      EXPECT_NEAR(uvs.min_u, 0.0f, 1e-5f);
      EXPECT_NEAR(uvs.max_u, 10.0f / 12.0f, 1e-5f);
      EXPECT_NEAR(uvs.min_v, 20.0f / 6.0f, 1e-5f);
      EXPECT_NEAR(uvs.max_v, 30.0f / 6.0f, 1e-5f);
    }
  }

  ASSERT_EQ(instanced.textures.size(), 2u);
  ASSERT_EQ(instanced.mesh_resources.size(), 3u);
  ASSERT_EQ(instanced.scene_hierarchy.children.size(), 4u);
  // BrickPanel and NestedBrick -> Panel share the same Brick-painted resource,
  // while TilePanel gets its own resource despite sharing white base color.
  const auto& brick_res_id = instanced.scene_hierarchy.children[0].mesh_resource_id;
  const auto& tile_res_id = instanced.scene_hierarchy.children[1].mesh_resource_id;
  ASSERT_EQ(instanced.scene_hierarchy.children[2].children.size(), 1u);
  const auto& nested_res_id = instanced.scene_hierarchy.children[2].children[0].mesh_resource_id;
  ASSERT_TRUE(brick_res_id.has_value());
  ASSERT_TRUE(tile_res_id.has_value());
  EXPECT_EQ(nested_res_id, brick_res_id);
  EXPECT_NE(tile_res_id, brick_res_id);

  const auto& brick_prim = instanced.mesh_resources[0].primitives.at(0);
  const auto brick_uvs = uv_bounds(brick_prim.uvs);
  EXPECT_NEAR(brick_uvs.max_u, 2.0f, 1e-5f);
  EXPECT_NEAR(brick_uvs.max_v, 2.0f, 1e-5f);
  EXPECT_EQ(instanced.gltf_materials.at(brick_prim.material_index)
                .pbr_metallic_roughness.base_color_texture,
            std::optional<std::size_t>{0});

  const auto& tile_prim = instanced.mesh_resources[1].primitives.at(0);
  const auto tile_uvs = uv_bounds(tile_prim.uvs);
  EXPECT_NEAR(tile_uvs.max_u, 6.0f, 1e-5f);
  EXPECT_NEAR(tile_uvs.max_v, 4.0f, 1e-5f);
  EXPECT_EQ(instanced.gltf_materials.at(tile_prim.material_index)
                .pbr_metallic_roughness.base_color_texture,
            std::optional<std::size_t>{1});

  // Both GLB exporters emit MASK alphaMode on inherited textured materials and
  // OPAQUE on solid opaque materials, matching Python.
  for (const auto& glb_bytes :
       {to_glb(scene, GlbOptions{true}), to_instanced_glb(instanced, InstancedGlbOptions{true})}) {
    const auto glb = load_glb(glb_bytes);
    EXPECT_EQ(glb.images.size(), 2u);
    EXPECT_EQ(glb.textures.size(), 2u);
    std::size_t mask_count = 0;
    std::size_t opaque_count = 0;
    for (const auto& m : glb.materials) {
      if (m.pbrMetallicRoughness.baseColorTexture.index >= 0) {
        EXPECT_EQ(m.alphaMode, "MASK");
        ++mask_count;
      } else {
        EXPECT_EQ(m.alphaMode, "OPAQUE");
        ++opaque_count;
      }
    }
    EXPECT_GE(mask_count, 2u);
    EXPECT_EQ(opaque_count, 1u);
  }
}

TEST(InheritedTextures, InstancedSceneSeparatesMaterialsSharingImageAndColorByTileSize) {
  TempTextureFiles files;
  auto builder = create();
  const int small_tile = builder->add_texture_material("SmallBrick", files.brick, 6.0, 12.0);
  const int large_tile = builder->add_texture_material("LargeBrick", files.brick, 12.0, 24.0);

  auto& panel = builder->add_component_definition("Panel");
  panel.add_face({{0, 0, 0}, {24, 0, 0}, {24, 12, 0}, {0, 12, 0}});
  panel.close();

  InstanceOptions first;
  first.material = small_tile;
  builder->add_instance(panel, first);

  InstanceOptions second;
  second.translation = {30, 0, 0};
  second.material = large_tile;
  builder->add_instance(panel, second);

  const auto instanced = SkpFile::from_buffer(builder->to_bytes()).build_instanced_scene();
  EXPECT_EQ(instanced.textures.size(), 1u);
  ASSERT_EQ(instanced.mesh_resources.size(), 2u);
  EXPECT_NEAR(uv_bounds(instanced.mesh_resources[0].primitives.at(0).uvs).max_u, 2.0f, 1e-5f);
  EXPECT_NEAR(uv_bounds(instanced.mesh_resources[1].primitives.at(0).uvs).max_u, 1.0f, 1e-5f);
}

}  // namespace
}  // namespace openskp
