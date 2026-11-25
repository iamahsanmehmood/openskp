#include <algorithm>
#include <cstdint>
#include <gtest/gtest.h>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include <openskp/openskp.hpp>

#define TINYGLTF_NO_STB_IMAGE
#define TINYGLTF_NO_STB_IMAGE_WRITE
#include <tiny_gltf.h>

#include "face_groups.hpp"
#include "test_helpers.hpp"

namespace openskp {
namespace {

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

struct KeptCase {
  const char* fixture;
  const char* material_name;
  const char* mime;
  const char* format;
  const char* file;
  std::vector<std::uint8_t> signature;
};

const std::vector<KeptCase> kKeptCases{
    {"texture_tiff_2017.skp", "Tex_TIF", "image/tiff", "TIFF", "tex.tif", {0x49, 0x49, 0x2a, 0x00}},
    {"texture_tiff_2021.skp", "Tex_TIF", "image/tiff", "TIFF", "tex.tif", {0x49, 0x49, 0x2a, 0x00}},
    {"texture_tga_2021.skp", "Tex_TGA", "image/x-tga", "TGA", "tex.tga", {}},
    {"texture_gif_2021.skp",
     "Tex_GIF",
     "image/gif",
     "GIF",
     "tex.gif",
     {'G', 'I', 'F', '8', '9', 'a'}},
};

std::size_t count_textured_materials(const std::vector<GltfMaterial>& materials) {
  std::size_t count = 0;
  for (const auto& m : materials) {
    if (m.pbr_metallic_roughness.base_color_texture.has_value()) {
      EXPECT_EQ(*m.pbr_metallic_roughness.base_color_texture, 0u);
      ++count;
    }
  }
  return count;
}

TEST(OriginalTextures, KeepsNonGltfTexturesInOwnFormatWhenRequested) {
  for (bool instanced : {false, true}) {
    for (const auto& tc : kKeptCases) {
      const auto skp = SkpFile::open(test::fixture(tc.fixture));
      std::vector<std::string> warnings;
      ParseOptions opts;
      opts.original_textures = true;
      opts.log = [&](LogLevel lvl, std::string_view msg) {
        if (lvl == LogLevel::warning && msg.rfind("Texture of material", 0) == 0) {
          warnings.emplace_back(msg);
        }
      };

      std::vector<SceneTexture> textures;
      std::vector<GltfMaterial> materials;
      if (instanced) {
        auto s = skp.build_instanced_scene(opts);
        textures = std::move(s.textures);
        materials = std::move(s.gltf_materials);
      } else {
        auto s = skp.build_scene(opts);
        textures = std::move(s.textures);
        materials = std::move(s.gltf_materials);
      }

      ASSERT_EQ(textures.size(), 1u) << tc.fixture;
      EXPECT_EQ(textures[0].mime_type, tc.mime) << tc.fixture;
      if (!tc.signature.empty()) {
        ASSERT_GE(textures[0].data.size(), tc.signature.size());
        EXPECT_TRUE(std::equal(tc.signature.begin(), tc.signature.end(), textures[0].data.begin()));
      }

      const auto model = skp.parse();
      bool matched_stored = false;
      for (const auto& mat : model.materials) {
        if (mat.name == tc.material_name && mat.texture.has_value()) {
          EXPECT_EQ(textures[0].data, mat.texture->data);
          matched_stored = true;
        }
      }
      EXPECT_TRUE(matched_stored) << tc.fixture;
      EXPECT_EQ(count_textured_materials(materials), 1u) << tc.fixture;
      EXPECT_TRUE(warnings.empty()) << tc.fixture;
    }
  }
}

TEST(OriginalTextures, DropsNonGltfTexturesByDefaultAndWarnsNamingOption) {
  for (bool instanced : {false, true}) {
    for (const auto& tc : kKeptCases) {
      const auto skp = SkpFile::open(test::fixture(tc.fixture));
      std::vector<std::string> warnings;
      ParseOptions opts;
      opts.log = [&](LogLevel lvl, std::string_view msg) {
        if (lvl == LogLevel::warning && msg.rfind("Texture of material", 0) == 0) {
          warnings.emplace_back(msg);
        }
      };

      if (instanced) {
        const auto s = skp.build_instanced_scene(opts);
        EXPECT_TRUE(s.textures.empty()) << tc.fixture;
        EXPECT_EQ(count_textured_materials(s.gltf_materials), 0u) << tc.fixture;
      } else {
        const auto s = skp.build_scene(opts);
        EXPECT_TRUE(s.textures.empty()) << tc.fixture;
        EXPECT_EQ(count_textured_materials(s.gltf_materials), 0u) << tc.fixture;
      }

      const std::string expected =
          std::string("Texture of material '") + tc.material_name + "' (" + tc.file + ") is " +
          tc.format +
          ", which a scene cannot carry (only PNG and JPEG): the material is built without its "
          "texture (pass original_textures = true to keep it in its own format)";
      ASSERT_EQ(warnings.size(), 1u) << tc.fixture;
      EXPECT_EQ(warnings[0], expected);
    }
  }
}

TEST(OriginalTextures, LeavesPngAndJpegTexturesUnchanged) {
  for (bool instanced : {false, true}) {
    for (const char* fixture : {"texture_bmp_2021.skp", "capilla_quiroz_v17.skp"}) {
      const auto skp = SkpFile::open(test::fixture(fixture));
      ParseOptions kept_opts;
      kept_opts.original_textures = true;
      if (instanced) {
        const auto plain = skp.build_instanced_scene();
        const auto kept = skp.build_instanced_scene(kept_opts);
        ASSERT_FALSE(plain.textures.empty());
        ASSERT_EQ(kept.textures.size(), plain.textures.size());
        for (std::size_t i = 0; i < plain.textures.size(); ++i) {
          EXPECT_EQ(kept.textures[i].mime_type, plain.textures[i].mime_type);
          EXPECT_EQ(kept.textures[i].data, plain.textures[i].data);
        }
      } else {
        const auto plain = skp.build_scene();
        const auto kept = skp.build_scene(kept_opts);
        ASSERT_FALSE(plain.textures.empty());
        ASSERT_EQ(kept.textures.size(), plain.textures.size());
        for (std::size_t i = 0; i < plain.textures.size(); ++i) {
          EXPECT_EQ(kept.textures[i].mime_type, plain.textures[i].mime_type);
          EXPECT_EQ(kept.textures[i].data, plain.textures[i].data);
        }
      }
    }
  }
}

TEST(OriginalTextures, SniffsKnownSignaturesAndRejectsUnknownBytes) {
  ByteBuffer tga(60, 0);
  tga[2] = 2;
  const std::string footer = "TRUEVISION-XFILE.";
  std::copy(footer.begin(), footer.end(), tga.begin() + 42);

  EXPECT_EQ(original_image_mime({0x49, 0x49, 0x2a, 0x00, 0, 0, 0, 0}),
            std::optional<std::string>{"image/tiff"});
  EXPECT_EQ(original_image_mime({0x4d, 0x4d, 0x00, 0x2a, 0, 0, 0, 0}),
            std::optional<std::string>{"image/tiff"});
  EXPECT_EQ(original_image_mime({'G', 'I', 'F', '8', '9', 'a', 0, 0}),
            std::optional<std::string>{"image/gif"});
  EXPECT_EQ(original_image_mime({'B', 'M', 0, 0, 0, 0}), std::optional<std::string>{"image/bmp"});
  EXPECT_EQ(original_image_mime({'R', 'I', 'F', 'F', 0, 0, 0, 0, 'W', 'E', 'B', 'P'}),
            std::optional<std::string>{"image/webp"});
  EXPECT_EQ(original_image_mime(tga), std::optional<std::string>{"image/x-tga"});
  EXPECT_EQ(original_image_mime({'z', 'z', 'z', 'z', 'z', 'z', 'z', 'z'}), std::nullopt);
  EXPECT_EQ(original_image_mime({}), std::nullopt);
}

TEST(OriginalTextures, GlbExportOmitsLoneTiffAndWarnsOnce) {
  for (bool instanced : {false, true}) {
    const auto skp = SkpFile::open(test::fixture("texture_tiff_2021.skp"));
    ParseOptions parse_opts;
    parse_opts.original_textures = true;

    std::vector<std::string> warnings;
    auto log_cb = [&](LogLevel lvl, std::string_view msg) {
      if (lvl == LogLevel::warning) warnings.emplace_back(msg);
    };

    ByteBuffer glb;
    if (instanced) {
      const auto scene = skp.build_instanced_scene(parse_opts);
      glb = to_instanced_glb(scene, InstancedGlbOptions{true, log_cb});
    } else {
      const auto scene = skp.build_scene(parse_opts);
      glb = to_glb(scene, GlbOptions{true, log_cb});
    }

    const std::string text(glb.begin(), glb.end());
    EXPECT_EQ(text.find("\"images\""), std::string::npos);
    EXPECT_EQ(text.find("\"textures\""), std::string::npos);
    EXPECT_EQ(text.find("\"samplers\""), std::string::npos);

    const auto model = load_glb(glb);
    EXPECT_TRUE(model.images.empty());
    EXPECT_TRUE(model.textures.empty());
    EXPECT_TRUE(model.samplers.empty());
    for (const auto& mat : model.materials) {
      EXPECT_LT(mat.pbrMetallicRoughness.baseColorTexture.index, 0);
      EXPECT_NE(mat.alphaMode, "MASK");
    }
    ASSERT_EQ(warnings.size(), 1u);
    EXPECT_EQ(warnings[0],
              "Texture 'tex.tif' is TIFF, which glTF 2.0 cannot carry (only PNG and JPEG): leaving "
              "it out of the GLB");
  }
}

TEST(OriginalTextures, GlbExportDropsTiffKeepsColorRemapsPngIndexAndWarnsOnce) {
  for (bool instanced : {false, true}) {
    const auto skp = SkpFile::open(test::fixture("texture_tiff_and_png_2021.skp"));
    ParseOptions parse_opts;
    parse_opts.original_textures = true;

    std::vector<std::string> warnings;
    auto log_cb = [&](LogLevel lvl, std::string_view msg) {
      if (lvl == LogLevel::warning) warnings.emplace_back(msg);
    };

    ByteBuffer glb;
    if (instanced) {
      const auto scene = skp.build_instanced_scene(parse_opts);
      ASSERT_EQ(scene.textures.size(), 2u);
      EXPECT_EQ(scene.textures[0].mime_type, "image/tiff");
      EXPECT_EQ(scene.textures[1].mime_type, "image/png");
      glb = to_instanced_glb(scene, InstancedGlbOptions{true, log_cb});
    } else {
      const auto scene = skp.build_scene(parse_opts);
      ASSERT_EQ(scene.textures.size(), 2u);
      EXPECT_EQ(scene.textures[0].mime_type, "image/tiff");
      EXPECT_EQ(scene.textures[1].mime_type, "image/png");
      glb = to_glb(scene, GlbOptions{true, log_cb});
    }

    const auto model = load_glb(glb);
    ASSERT_EQ(model.images.size(), 1u);
    EXPECT_EQ(model.images[0].mimeType, "image/png");
    ASSERT_EQ(model.textures.size(), 1u);
    EXPECT_EQ(model.textures[0].source, 0);
    ASSERT_EQ(model.samplers.size(), 1u);

    const auto srgb_to_linear = [](double c) {
      return c <= 0.04045 ? c / 12.92 : std::pow((c + 0.055) / 1.055, 2.4);
    };
    const tinygltf::Material* tiff_mat = nullptr;
    const tinygltf::Material* png_mat = nullptr;
    for (const auto& mat : model.materials) {
      ASSERT_EQ(mat.pbrMetallicRoughness.baseColorFactor.size(), 4u);
      if (mat.pbrMetallicRoughness.baseColorFactor[0] > 0.5) tiff_mat = &mat;
      if (mat.pbrMetallicRoughness.baseColorFactor[2] > 0.8) png_mat = &mat;
    }
    ASSERT_NE(tiff_mat, nullptr);
    EXPECT_LT(tiff_mat->pbrMetallicRoughness.baseColorTexture.index, 0);
    EXPECT_EQ(tiff_mat->alphaMode, "OPAQUE");
    EXPECT_NEAR(tiff_mat->pbrMetallicRoughness.baseColorFactor[0], srgb_to_linear(200.0 / 255.0),
                1e-4);
    EXPECT_NEAR(tiff_mat->pbrMetallicRoughness.baseColorFactor[1], srgb_to_linear(60.0 / 255.0),
                1e-4);
    EXPECT_NEAR(tiff_mat->pbrMetallicRoughness.baseColorFactor[2], srgb_to_linear(40.0 / 255.0),
                1e-4);

    ASSERT_NE(png_mat, nullptr);
    EXPECT_EQ(png_mat->pbrMetallicRoughness.baseColorTexture.index, 0);
    EXPECT_EQ(png_mat->alphaMode, "MASK");

    ASSERT_EQ(warnings.size(), 1u);
    EXPECT_EQ(warnings[0],
              "Texture 'tex.tif' is TIFF, which glTF 2.0 cannot carry (only PNG and JPEG): leaving "
              "it out of the GLB");
  }
}

}  // namespace
}  // namespace openskp
