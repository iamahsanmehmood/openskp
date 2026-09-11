#include <algorithm>
#include <array>
#include <chrono>
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

constexpr std::array<double, 16> kIdentity{
    1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1,
};

InstancedMeshResource make_box_resource(const std::string& id) {
  InstancedMeshResource r;
  r.id = id;
  r.definition_id = 1;
  r.definition_name = "Box";
  r.variant_key = "1|255,255,255";
  LocalPrimitive prim;
  prim.positions = {0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0, 0, 0, 1, 1, 0, 1, 1, 1, 1, 0, 1, 1};
  prim.normals.assign(prim.positions.size(), 0.0f);
  prim.uvs.assign((prim.positions.size() / 3) * 2, 0.0f);
  prim.indices = {0, 1, 2, 0, 2, 3, 4, 6, 5, 4, 7, 6};
  prim.material_index = 0;
  r.primitives = {prim};
  return r;
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

TEST(InstancedGlb, ManyDistinctMeshResourcesStayNearLinearNotQuadratic) {
  // Regression test for openskp#305: make_model() reserved its shared
  // binary buffer to exactly its own new size on every append_values()
  // call (once per primitive per attribute array) instead of once
  // upfront for the true final size, forcing a full copy of everything
  // appended so far on every single call - turning what should be
  // amortized-O(1) appends into O(resource count squared). On a real
  // 65,000-resource file this was 713s under WASM (56.6x slower than
  // the ~12.6s after the fix) and 39s even natively for just 10,000
  // resources. 8,000 distinct tiny resources is calibrated to make the
  // quadratic behavior obvious (~9.4s, measured against the pre-fix
  // code) while staying comfortably fast when fixed (~0.3s) - a >10x
  // margin either way, so this isn't sensitive to normal CI slowness.
  InstancedScene scene;
  scene.gltf_materials = {{}};
  constexpr int kResourceCount = 8000;
  scene.mesh_resources.reserve(kResourceCount);
  InstancedNode root;
  root.name = "ROOT";
  root.matrix = kIdentity;
  root.children.reserve(kResourceCount);
  for (int i = 0; i < kResourceCount; ++i) {
    const auto id = "mesh_" + std::to_string(i);
    scene.mesh_resources.push_back(make_box_resource(id));
    InstancedNode leaf;
    leaf.name = "Box" + std::to_string(i);
    leaf.matrix = kIdentity;
    leaf.mesh_resource_id = id;
    root.children.push_back(std::move(leaf));
  }
  scene.scene_hierarchy = std::move(root);

  const auto start = std::chrono::steady_clock::now();
  const auto bytes = to_instanced_glb(scene);
  const auto elapsed_ms =
      std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();

  EXPECT_GT(bytes.size(), 0u);
  // Fixed: comfortably under a second. Quadratic-buggy: several seconds
  // at this size and climbing fast with scene size - 5s leaves ample
  // margin above real (fixed) runs without being able to hide a
  // reintroduced O(n^2).
  EXPECT_LT(elapsed_ms, 5000.0) << "took " << elapsed_ms << "ms - looks quadratic again";
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
