#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <gtest/gtest.h>
#include <map>
#include <random>
#include <set>
#include <string>
#include <string_view>
#include <tuple>
#include <utility>
#include <vector>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

#include <openskp/instanced_glb.hpp>
#include <openskp/openskp.hpp>

#define TINYGLTF_NO_STB_IMAGE
#define TINYGLTF_NO_STB_IMAGE_WRITE
#include <tiny_gltf.h>

#include "glb_textures.hpp"
#include "test_helpers.hpp"

namespace openskp {
namespace {

bool skip_image_decode(tinygltf::Image*, const int, std::string*, std::string*, int, int,
                       const unsigned char*, int, void*) {
  return true;
}

tinygltf::Model load_glb_model(const ByteBuffer& bytes) {
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

ByteBuffer extract_glb_image_bytes(const tinygltf::Model& model, std::size_t image_index) {
  const auto& img = model.images.at(image_index);
  const auto& bv = model.bufferViews.at(static_cast<std::size_t>(img.bufferView));
  const auto& buf = model.buffers.at(static_cast<std::size_t>(bv.buffer)).data;
  return ByteBuffer(buf.begin() + static_cast<std::ptrdiff_t>(bv.byteOffset),
                    buf.begin() + static_cast<std::ptrdiff_t>(bv.byteOffset + bv.byteLength));
}

struct TempTestDir {
  std::filesystem::path path;

  explicit TempTestDir(const std::string& prefix) {
    path = std::filesystem::temp_directory_path() /
           (prefix + "-" + std::to_string(std::random_device{}()));
    std::filesystem::create_directories(path);
  }

  ~TempTestDir() {
    std::error_code ec;
    std::filesystem::remove_all(path, ec);
  }
};

void write_file_bytes(const std::filesystem::path& p, const ByteBuffer& data) {
  std::ofstream out(p, std::ios::binary | std::ios::trunc);
  ASSERT_TRUE(out);
  out.write(reinterpret_cast<const char*>(data.data()), static_cast<std::streamsize>(data.size()));
  ASSERT_TRUE(out);
}

ByteBuffer build_colorize_quads_skp(const std::filesystem::path& tmp_dir,
                                    int repeats_per_material = 1) {
  const auto orig_model = SkpFile::open(test::fixture("legacy_textured_colorize_2017.skp")).parse();
  const auto tex_png_path = tmp_dir / "base_2x1.png";
  write_file_bytes(tex_png_path, orig_model.materials.at(0).texture->data.value());

  auto builder = create();
  std::map<std::string, int> handles;
  for (const auto& src_m : orig_model.materials) {
    TextureMaterialOptions opts;
    opts.color = {src_m.color[0], src_m.color[1], src_m.color[2]};
    opts.transparency = src_m.transparency;
    opts.colorized = src_m.colorized;
    opts.colorize_type = src_m.colorize_type;
    opts.filename = "base_2x1.png";
    handles[src_m.name] = builder->add_texture_material(src_m.name, tex_png_path, opts);
  }

  auto& comp = builder->add_component_definition("ColorizedTile");
  FaceOptions comp_face;
  comp_face.material = handles.at("shift");
  comp.add_face({{0.0, 0.0, 0.0}, {1.0, 0.0, 0.0}, {1.0, 1.0, 0.0}, {0.0, 1.0, 0.0}}, comp_face);
  comp.close();

  const std::array<const char*, 4> names{"plain", "tint", "shift", "tint_half"};
  for (std::size_t idx = 0; idx < names.size(); ++idx) {
    for (int r = 0; r < repeats_per_material; ++r) {
      const double x0 = static_cast<double>(idx) * 2.0;
      const double y0 = static_cast<double>(r) * 2.0;
      FaceOptions fopts;
      fopts.material = handles.at(names[idx]);
      builder->add_face(
          {{x0, y0, 0.0}, {x0 + 1.0, y0, 0.0}, {x0 + 1.0, y0 + 1.0, 0.0}, {x0, y0 + 1.0, 0.0}},
          fopts);
    }
  }
  for (int r = 0; r < repeats_per_material; ++r) {
    InstanceOptions iopts;
    iopts.translation = {10.0, static_cast<double>(r) * 2.0, 0.0};
    builder->add_instance(comp, iopts);
  }
  return builder->to_bytes();
}

ByteBuffer inject_exif_orientation_app1(const ByteBuffer& jpeg_bytes,
                                        std::uint16_t orientation = 6) {
  ByteBuffer tiff{
      'I',
      'I',
      42,
      0,
      8,
      0,
      0,
      0,  // little-endian TIFF header
      1,
      0,  // 1 IFD entry
      0x12,
      0x01,
      3,
      0,
      1,
      0,
      0,
      0,  // Tag 0x0112 (Orientation), SHORT, count 1
      static_cast<std::uint8_t>(orientation & 0xFFu),
      static_cast<std::uint8_t>((orientation >> 8) & 0xFFu),
      0,
      0,
      0,
      0,
      0,
      0,  // next IFD = 0
  };
  ByteBuffer payload{'E', 'x', 'i', 'f', 0, 0};
  payload.insert(payload.end(), tiff.begin(), tiff.end());
  const std::uint16_t seg_len = static_cast<std::uint16_t>(payload.size() + 2);

  ByteBuffer out;
  out.reserve(jpeg_bytes.size() + payload.size() + 4);
  out.push_back(jpeg_bytes[0]);
  out.push_back(jpeg_bytes[1]);
  out.push_back(0xFF);
  out.push_back(0xE1);
  out.push_back(static_cast<std::uint8_t>((seg_len >> 8) & 0xFFu));
  out.push_back(static_cast<std::uint8_t>(seg_len & 0xFFu));
  out.insert(out.end(), payload.begin(), payload.end());
  out.insert(out.end(), jpeg_bytes.begin() + 2, jpeg_bytes.end());
  return out;
}

bool contains_subsequence(const ByteBuffer& haystack, const ByteBuffer& needle) {
  return std::search(haystack.begin(), haystack.end(), needle.begin(), needle.end()) !=
         haystack.end();
}

// Parse JPEG SOF0 (0xFFC0) sampling factors to verify 4:4:4 (0x11, 0x11, 0x11).
std::vector<std::uint8_t> parse_jpeg_sof0_sampling_factors(const ByteBuffer& jpeg) {
  if (jpeg.size() < 4 || jpeg[0] != 0xFF || jpeg[1] != 0xD8) return {};
  std::size_t pos = 2;
  while (pos < jpeg.size()) {
    if (jpeg[pos] != 0xFF) break;
    while (pos < jpeg.size() && jpeg[pos] == 0xFF) ++pos;
    if (pos >= jpeg.size()) break;
    const std::uint8_t marker = jpeg[pos++];
    if (marker == 0x00 || marker == 0xD9 || marker == 0xDA) break;
    if (marker == 0x01 || (marker >= 0xD0 && marker <= 0xD8)) continue;
    if (pos + 2 > jpeg.size()) break;
    const std::size_t seg_len =
        (static_cast<std::size_t>(jpeg[pos]) << 8) | static_cast<std::size_t>(jpeg[pos + 1]);
    if (seg_len < 2 || pos + seg_len > jpeg.size()) break;
    if (marker == 0xC0) {
      if (seg_len < 8) return {};
      const std::size_t num_comp = jpeg[pos + 7];
      if (seg_len < 8 + num_comp * 3) return {};
      std::vector<std::uint8_t> factors;
      for (std::size_t c = 0; c < num_comp; ++c) {
        factors.push_back(jpeg[pos + 8 + c * 3 + 1]);
      }
      return factors;
    }
    pos += seg_len;
  }
  return {};
}

TEST(ColorizedTexturesGlb, BakesShiftTintAndOpacityWithoutMutatingScene) {
  TempTestDir tmp("openskp-colorize-quads");
  const ByteBuffer skp_bytes = build_colorize_quads_skp(tmp.path, /*repeats_per_material=*/3);
  const auto skp = SkpFile::from_buffer(skp_bytes);

  const Scene scene = skp.build_scene();
  const InstancedScene inst = skp.build_instanced_scene();
  ASSERT_EQ(scene.textures.size(), 1u);
  ASSERT_EQ(inst.textures.size(), 1u);
  const auto orig_scene_mats = scene.gltf_materials;
  const auto orig_inst_mats = inst.gltf_materials;
  const ByteBuffer orig_tex = scene.textures[0].data;

  // Ground-truth RGB pixels from SUTextureGetColorizedImageRep on
  // legacy_textured_colorize_2017.skp (2x1 base PNG: [200,40,40], [40,40,200]).
  using TwoPixelsAndAlpha = std::tuple<Color3, Color3, int>;
  const std::set<TwoPixelsAndAlpha> expected{
      {Color3{200, 40, 40}, Color3{40, 40, 200}, 100},
      {Color3{32, 237, 80}, Color3{32, 237, 80}, 100},
      {Color3{172, 253, 46}, Color3{253, 46, 172}, 100},
      {Color3{65, 73, 255}, Color3{65, 73, 255}, 50},
  };

  for (bool instanced : {false, true}) {
    const ByteBuffer glb_bytes = instanced ? to_instanced_glb(inst, InstancedGlbOptions{true})
                                           : to_glb(scene, GlbOptions{true});

    // Scene and InstancedScene gltf_materials and textures remain unchanged.
    ASSERT_EQ(scene.gltf_materials.size(), orig_scene_mats.size());
    for (std::size_t i = 0; i < scene.gltf_materials.size(); ++i) {
      EXPECT_EQ(scene.gltf_materials[i].pbr_metallic_roughness.base_color_factor,
                orig_scene_mats[i].pbr_metallic_roughness.base_color_factor);
      EXPECT_EQ(scene.gltf_materials[i].colorized, orig_scene_mats[i].colorized);
      EXPECT_EQ(scene.gltf_materials[i].colorize_type, orig_scene_mats[i].colorize_type);
    }
    ASSERT_EQ(inst.gltf_materials.size(), orig_inst_mats.size());
    for (std::size_t i = 0; i < inst.gltf_materials.size(); ++i) {
      EXPECT_EQ(inst.gltf_materials[i].pbr_metallic_roughness.base_color_factor,
                orig_inst_mats[i].pbr_metallic_roughness.base_color_factor);
    }
    EXPECT_EQ(scene.textures[0].data, orig_tex);
    EXPECT_EQ(inst.textures[0].data, orig_tex);

    const auto model = load_glb_model(glb_bytes);
    ASSERT_EQ(model.images.size(), 4u);
    std::set<TwoPixelsAndAlpha> actual;
    std::size_t textured_count = 0;
    for (const auto& mat : model.materials) {
      const int tex_idx = mat.pbrMetallicRoughness.baseColorTexture.index;
      if (tex_idx < 0) continue;
      ++textured_count;
      ASSERT_EQ(mat.pbrMetallicRoughness.baseColorFactor.size(), 4u);
      EXPECT_NEAR(mat.pbrMetallicRoughness.baseColorFactor[0], 1.0, 1e-6);
      EXPECT_NEAR(mat.pbrMetallicRoughness.baseColorFactor[1], 1.0, 1e-6);
      EXPECT_NEAR(mat.pbrMetallicRoughness.baseColorFactor[2], 1.0, 1e-6);
      const int img_idx = model.textures.at(static_cast<std::size_t>(tex_idx)).source;
      ASSERT_GE(img_idx, 0);
      EXPECT_EQ(model.images.at(static_cast<std::size_t>(img_idx)).mimeType, "image/png");
      const ByteBuffer raw = extract_glb_image_bytes(model, static_cast<std::size_t>(img_idx));
      const auto decoded = decode_texture_rgba(raw);
      ASSERT_TRUE(decoded.has_value());
      ASSERT_EQ(decoded->width, 2);
      ASSERT_EQ(decoded->height, 1);
      const unsigned char* px = decoded->pixels.get();
      const Color3 left{px[0], px[1], px[2]};
      const Color3 right{px[4], px[5], px[6]};
      const int opacity_pct =
          static_cast<int>(std::lround(mat.pbrMetallicRoughness.baseColorFactor[3] * 100.0));
      actual.emplace(left, right, opacity_pct);
    }
    EXPECT_EQ(textured_count, 4u);
    EXPECT_EQ(actual, expected);
  }
}

TEST(ColorizedTexturesGlb, CachesDecodedAndBakedImagesOncePerMaterial) {
  TempTestDir tmp("openskp-colorize-cache");
  const ByteBuffer skp_bytes = build_colorize_quads_skp(tmp.path, /*repeats_per_material=*/8);
  const auto skp = SkpFile::from_buffer(skp_bytes);

  for (bool instanced : {false, true}) {
    GltfTextureBakeStats stats{};
    if (instanced) {
      const auto s = skp.build_instanced_scene();
      const auto resolved = resolve_gltf_textures(s.textures, s.gltf_materials, true, {}, &stats);
      EXPECT_EQ(resolved.images.size(), 4u);
      const auto model = load_glb_model(to_instanced_glb(s, InstancedGlbOptions{true}));
      EXPECT_EQ(model.images.size(), 4u);
    } else {
      const auto s = skp.build_scene();
      const auto resolved = resolve_gltf_textures(s.textures, s.gltf_materials, true, {}, &stats);
      EXPECT_EQ(resolved.images.size(), 4u);
      const auto model = load_glb_model(to_glb(s, GlbOptions{true}));
      EXPECT_EQ(model.images.size(), 4u);
    }
    // 1 shared base PNG decoded once; 3 colorized variants (tint, shift, tint_half)
    // baked once per unique color despite 8+ repeated faces/instances each.
    EXPECT_EQ(stats.decode_calls, 1u);
    EXPECT_EQ(stats.colorize_calls, 3u);
  }
}

TEST(ColorizedTexturesGlb, GreyscaleAlphaRowBlocksAndAchromaticTargetsMatchRules) {
  // Greyscale pixels in shift and tint modes, including black -> 0.01 lightness clamp.
  const std::array<std::uint8_t, 12> grey{
      0, 0, 0, 0, 128, 128, 128, 64, 255, 255, 255, 255,
  };
  const ByteBuffer shifted = colorize_image_u8(grey.data(), 3, 1, Color3{200, 90, 20}, 0, true);
  const ByteBuffer tinted = colorize_image_u8(grey.data(), 3, 1, Color3{200, 90, 20}, 1, true);
  ASSERT_EQ(shifted.size(), 12u);
  ASSERT_EQ(tinted.size(), 12u);
  EXPECT_EQ(shifted[3], 0);
  EXPECT_EQ(shifted[7], 64);
  EXPECT_EQ(shifted[11], 255);
  EXPECT_EQ(tinted[3], 0);
  EXPECT_EQ(tinted[7], 64);
  EXPECT_EQ(tinted[11], 255);
  EXPECT_GT(shifted[0], 0);

  // Chromatic image (avg_s > 0) colorized toward a pure grey target (tgt_s == 0) desaturates.
  const std::array<std::uint8_t, 8> vivid{240, 30, 80, 180, 20, 210, 90, 255};
  for (int ctype : {0, 1}) {
    const ByteBuffer desat =
        colorize_image_u8(vivid.data(), 2, 1, Color3{140, 140, 140}, ctype, true);
    ASSERT_EQ(desat.size(), 8u);
    EXPECT_EQ(desat[0], desat[1]);
    EXPECT_EQ(desat[1], desat[2]);
    EXPECT_EQ(desat[3], 180);
    EXPECT_EQ(desat[4], desat[5]);
    EXPECT_EQ(desat[5], desat[6]);
    EXPECT_EQ(desat[7], 255);
  }

  // Chromatic pixels whose whole-image average is grey (avg_s == 0) keep delta_s == 0
  // (within <= 1/255 of SUTextureGetColorizedImageRep's [[255,24,24],[24,255,255]]).
  const std::array<std::uint8_t, 8> grey_avg_chrom{255, 1, 1, 255, 1, 255, 255, 255};
  const ByteBuffer s0 =
      colorize_image_u8(grey_avg_chrom.data(), 2, 1, Color3{140, 140, 140}, 0, false);
  const ByteBuffer s1 =
      colorize_image_u8(grey_avg_chrom.data(), 2, 1, Color3{140, 140, 140}, 1, false);
  const std::array<int, 6> exp0{255, 24, 24, 24, 255, 255};
  const std::array<int, 6> exp1{255, 24, 24, 255, 24, 24};
  for (std::size_t i = 0; i < 6; ++i) {
    EXPECT_LE(std::abs(static_cast<int>(s0[i]) - exp0[i]), 1);
    EXPECT_LE(std::abs(static_cast<int>(s1[i]) - exp1[i]), 1);
  }

  // Row-block chunking produces byte-identical pixels regardless of block size.
  std::mt19937 rng(7);
  ByteBuffer multi_row(37u * 19u * 4u);
  for (auto& b : multi_row) b = static_cast<std::uint8_t>(rng() & 0xFFu);
  for (int ctype : {0, 1}) {
    const ByteBuffer whole =
        colorize_image_u8(multi_row.data(), 19, 37, Color3{200, 90, 20}, ctype, true, 100000);
    const ByteBuffer chunked =
        colorize_image_u8(multi_row.data(), 19, 37, Color3{200, 90, 20}, ctype, true, 40);
    EXPECT_EQ(whole, chunked);
  }
}

TEST(ColorizedTexturesGlb, ColorizedJpegStaysJpeg444AndExifOrientationIsStripped) {
  TempTestDir tmp("openskp-colorize-jpeg-exif");

  // Asymmetric 8x4 RGB JPEG (top-left red, bottom-right blue) with EXIF Orientation=6.
  ByteBuffer rgb(8u * 4u * 3u, 0);
  for (int y = 0; y < 2; ++y) {
    for (int x = 0; x < 4; ++x) {
      rgb[static_cast<std::size_t>((y * 8 + x) * 3 + 0)] = 255;
    }
  }
  for (int y = 2; y < 4; ++y) {
    for (int x = 4; x < 8; ++x) {
      rgb[static_cast<std::size_t>((y * 8 + x) * 3 + 2)] = 255;
    }
  }
  const ByteBuffer clean_jpeg = encode_jpeg_444(rgb.data(), 8, 4, 3, 95);
  const ByteBuffer exif_jpeg = inject_exif_orientation_app1(clean_jpeg, 6);
  const ByteBuffer exif_sig{'E', 'x', 'i', 'f', 0, 0};
  ASSERT_TRUE(contains_subsequence(exif_jpeg, exif_sig));
  EXPECT_EQ(strip_jpeg_exif_app1(exif_jpeg), clean_jpeg);

  const auto jpg_path = tmp.path / "exif6.jpg";
  write_file_bytes(jpg_path, exif_jpeg);

  auto builder = create();

  struct Spec {
    const char* name;
    bool colorized;
    int ctype;
    Color3 rgb;
  };

  const std::array<Spec, 3> specs{{
      {"PlainExif", false, 0, {255, 255, 255}},
      {"ShiftExif", true, 0, {200, 90, 20}},
      {"TintExif", true, 1, {30, 160, 60}},
  }};
  std::map<std::string, int> handles;
  for (const auto& sp : specs) {
    TextureMaterialOptions opts;
    opts.color = sp.rgb;
    opts.colorized = sp.colorized;
    opts.colorize_type = sp.ctype;
    opts.filename = "exif6.jpg";
    handles[sp.name] = builder->add_texture_material(sp.name, jpg_path, opts);
  }
  for (std::size_t idx = 0; idx < specs.size(); ++idx) {
    const double x0 = static_cast<double>(idx) * 3.0;
    FaceOptions fopts;
    fopts.material = handles.at(specs[idx].name);
    builder->add_face({{x0, 0.0, 0.0}, {x0 + 2.0, 0.0, 0.0}, {x0 + 2.0, 1.0, 0.0}, {x0, 1.0, 0.0}},
                      fopts);
  }

  const auto skp = SkpFile::from_buffer(builder->to_bytes());
  const Scene scene = skp.build_scene();
  const InstancedScene inst = skp.build_instanced_scene();
  ASSERT_EQ(scene.textures.size(), 1u);
  EXPECT_EQ(scene.textures[0].data, exif_jpeg);

  for (bool instanced : {false, true}) {
    const ByteBuffer glb_bytes = instanced ? to_instanced_glb(inst, InstancedGlbOptions{true})
                                           : to_glb(scene, GlbOptions{true});
    const auto model = load_glb_model(glb_bytes);
    ASSERT_EQ(model.images.size(), 3u);
    bool matched_lossless_strip = false;
    std::size_t verified_444 = 0;
    for (std::size_t i = 0; i < model.images.size(); ++i) {
      EXPECT_EQ(model.images[i].mimeType, "image/jpeg");
      const ByteBuffer blob = extract_glb_image_bytes(model, i);
      EXPECT_FALSE(contains_subsequence(blob, exif_sig));
      const auto decoded = decode_texture_rgba(blob);
      ASSERT_TRUE(decoded.has_value());
      EXPECT_EQ(decoded->width, 8);
      EXPECT_EQ(decoded->height, 4);
      if (blob == clean_jpeg) {
        matched_lossless_strip = true;
      } else {
        const auto sampling = parse_jpeg_sof0_sampling_factors(blob);
        EXPECT_EQ(sampling, (std::vector<std::uint8_t>{0x11, 0x11, 0x11}));
        ++verified_444;
      }
    }
    EXPECT_TRUE(matched_lossless_strip);
    EXPECT_EQ(verified_444, 2u);
  }
}

TEST(ColorizedTexturesGlb, CorruptColorizedTextureWarnsOnceAndStaysSilentOnStderr) {
  Scene baked;
  GltfMaterial mat{"CorruptCol", "",   {{200.0 / 255.0, 90.0 / 255.0, 20.0 / 255.0, 1.0}, 0.0, 0.8},
                   false,        true, 0};
  mat.pbr_metallic_roughness.base_color_texture = std::size_t{0};
  baked.gltf_materials.push_back(mat);
  baked.textures.push_back(
      {ByteBuffer{0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a}, "image/png", "truncated.png"});
  baked.glb_primitives.push_back({
      {0.0f, 0.0f, 0.0f, 1.0f, 0.0f, 0.0f, 0.0f, 1.0f, 0.0f},
      {0.0f, 0.0f, 1.0f, 0.0f, 0.0f, 1.0f, 0.0f, 0.0f, 1.0f},
      {0.0f, 0.0f, 1.0f, 0.0f, 0.0f, 1.0f},
      {0, 1, 2},
      0,
      "tri",
  });

  testing::internal::CaptureStdout();
  testing::internal::CaptureStderr();
  const ByteBuffer silent_glb = to_glb(baked, GlbOptions{true});
  const std::string out_str = testing::internal::GetCapturedStdout();
  const std::string err_str = testing::internal::GetCapturedStderr();
  EXPECT_TRUE(out_str.empty());
  EXPECT_TRUE(err_str.empty());
  EXPECT_FALSE(silent_glb.empty());

  std::vector<std::string> warnings;
  GlbOptions logged_opts;
  logged_opts.textures = true;
  logged_opts.log = [&](LogLevel lvl, std::string_view msg) {
    if (lvl == LogLevel::warning) warnings.emplace_back(msg);
  };
  const auto model = load_glb_model(to_glb(baked, logged_opts));
  EXPECT_EQ(warnings.size(), 1u);
  EXPECT_TRUE(model.images.empty());
  ASSERT_EQ(model.materials.size(), 1u);
  EXPECT_EQ(model.materials[0].pbrMetallicRoughness.baseColorTexture.index, -1);
}

#ifdef _WIN32
struct SuColor {
  unsigned char red;
  unsigned char green;
  unsigned char blue;
  unsigned char alpha;
};

struct SuPoint3D {
  double x;
  double y;
  double z;
};

TEST(ColorizedTexturesGlb, PixelComparisonAgainstSketchUpSdkLibrary) {
  std::filesystem::path dll_path =
      R"(C:\Program Files\SketchUp\SketchUp 2025\SketchUp\SketchUpAPI.dll)";
  if (const char* env_dll = std::getenv("SKETCHUP_SDK_DLL")) {
    dll_path = env_dll;
  }
  if (!std::filesystem::is_regular_file(dll_path)) {
    GTEST_SKIP() << "SketchUpAPI.dll not installed on this machine";
  }

  HMODULE su = LoadLibraryExW(dll_path.wstring().c_str(), nullptr,
                              LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
  if (!su) {
    GTEST_SKIP() << "Failed to load SketchUpAPI.dll";
  }

  struct DllGuard {
    HMODULE handle;

    ~DllGuard() {
      if (handle) FreeLibrary(handle);
    }
  } guard{su};

  using FnVoid = void (*)();
  using FnPtr = int (*)(void**);
  using FnGetEnts = int (*)(void*, void**);
  using FnAddItems = int (*)(void*, std::size_t, void**);
  using FnSaveVer = int (*)(void*, const char*, int);
  using FnAddVert = int (*)(void*, std::size_t);
  using FnFaceCreate = int (*)(void**, const SuPoint3D*, void**);
  using FnSetPtr = int (*)(void*, void*);
  using FnSetStr = int (*)(void*, const char*);
  using FnSetColor = int (*)(void*, const SuColor*);
  using FnSetInt = int (*)(void*, int);
  using FnTexCreate = int (*)(void**, const char*, double, double);

  auto load_fn = [&](const char* name) {
    return reinterpret_cast<void*>(GetProcAddress(su, name));
  };
  const auto su_init = reinterpret_cast<FnVoid>(load_fn("SUInitialize"));
  const auto su_term = reinterpret_cast<FnVoid>(load_fn("SUTerminate"));
  const auto su_model_create = reinterpret_cast<FnPtr>(load_fn("SUModelCreate"));
  const auto su_model_release = reinterpret_cast<FnPtr>(load_fn("SUModelRelease"));
  const auto su_model_get_ents = reinterpret_cast<FnGetEnts>(load_fn("SUModelGetEntities"));
  const auto su_model_add_mats = reinterpret_cast<FnAddItems>(load_fn("SUModelAddMaterials"));
  const auto su_model_save_ver =
      reinterpret_cast<FnSaveVer>(load_fn("SUModelSaveToFileWithVersion"));
  const auto su_ents_add_faces = reinterpret_cast<FnAddItems>(load_fn("SUEntitiesAddFaces"));
  const auto su_loop_create = reinterpret_cast<FnPtr>(load_fn("SULoopInputCreate"));
  const auto su_loop_add_vi = reinterpret_cast<FnAddVert>(load_fn("SULoopInputAddVertexIndex"));
  const auto su_face_create = reinterpret_cast<FnFaceCreate>(load_fn("SUFaceCreate"));
  const auto su_face_set_front = reinterpret_cast<FnSetPtr>(load_fn("SUFaceSetFrontMaterial"));
  const auto su_mat_create = reinterpret_cast<FnPtr>(load_fn("SUMaterialCreate"));
  const auto su_mat_set_name = reinterpret_cast<FnSetStr>(load_fn("SUMaterialSetName"));
  const auto su_mat_set_tex = reinterpret_cast<FnSetPtr>(load_fn("SUMaterialSetTexture"));
  const auto su_mat_set_col = reinterpret_cast<FnSetColor>(load_fn("SUMaterialSetColor"));
  const auto su_mat_set_type = reinterpret_cast<FnSetInt>(load_fn("SUMaterialSetType"));
  const auto su_mat_set_ctype = reinterpret_cast<FnSetInt>(load_fn("SUMaterialSetColorizeType"));
  const auto su_tex_create = reinterpret_cast<FnTexCreate>(load_fn("SUTextureCreateFromFile"));
  const auto su_tex_get_img_rep = reinterpret_cast<FnGetEnts>(load_fn("SUTextureGetImageRep"));
  const auto su_tex_get_col_rep =
      reinterpret_cast<FnGetEnts>(load_fn("SUTextureGetColorizedImageRep"));
  const auto su_rep_create = reinterpret_cast<FnPtr>(load_fn("SUImageRepCreate"));
  const auto su_rep_release = reinterpret_cast<FnPtr>(load_fn("SUImageRepRelease"));
  const auto su_rep_save = reinterpret_cast<FnSetStr>(load_fn("SUImageRepSaveToFile"));
  ASSERT_NE(su_init, nullptr);
  ASSERT_NE(su_term, nullptr);
  ASSERT_NE(su_tex_get_img_rep, nullptr);
  ASSERT_NE(su_tex_get_col_rep, nullptr);

  TempTestDir tmp("openskp-sdk-colorize");
  ByteBuffer rgb_arr(16u * 16u * 3u);
  ByteBuffer grey_rgb_arr(16u * 16u * 3u);
  ByteBuffer rgba_arr(16u * 16u * 4u);
  for (int y = 0; y < 16; ++y) {
    for (int x = 0; x < 16; ++x) {
      const auto r = static_cast<std::uint8_t>((x * 17 + y * 5) % 256);
      const auto g = static_cast<std::uint8_t>((y * 15 + 30) % 256);
      const auto b = static_cast<std::uint8_t>(((15 - x) * 14 + y * 7) % 256);
      const std::size_t idx3 = static_cast<std::size_t>((y * 16 + x) * 3);
      rgb_arr[idx3 + 0] = r;
      rgb_arr[idx3 + 1] = g;
      rgb_arr[idx3 + 2] = b;

      const auto gv = static_cast<std::uint8_t>((x * 16 + y) % 256);
      grey_rgb_arr[idx3 + 0] = gv;
      grey_rgb_arr[idx3 + 1] = gv;
      grey_rgb_arr[idx3 + 2] = gv;

      const std::size_t idx4 = static_cast<std::size_t>((y * 16 + x) * 4);
      rgba_arr[idx4 + 0] = r;
      rgba_arr[idx4 + 1] = g;
      rgba_arr[idx4 + 2] = b;
      rgba_arr[idx4 + 3] = (x < 4) ? 0 : (x < 8) ? 64 : (x < 12) ? 180 : 255;
    }
  }

  const auto rgb_png = tmp.path / "rgb.png";
  const auto grey_png = tmp.path / "grey.png";
  const auto rgba_png = tmp.path / "rgba_alpha.png";
  const auto jpeg_path = tmp.path / "photo.jpg";
  write_file_bytes(rgb_png, encode_png_u8(rgb_arr.data(), 16, 16, 3));
  write_file_bytes(grey_png, encode_png_u8(grey_rgb_arr.data(), 16, 16, 3));
  write_file_bytes(rgba_png, encode_png_u8(rgba_arr.data(), 16, 16, 4));
  const ByteBuffer src_jpeg_bytes = encode_jpeg_444(rgb_arr.data(), 16, 16, 3, 92);
  write_file_bytes(jpeg_path, src_jpeg_bytes);

  struct SdkCase {
    const char* label;
    std::filesystem::path img_path;
    int ctype;
    Color3 trgb;
    bool is_jpeg;
  };

  const std::array<SdkCase, 8> cases{{
      {"shift_rgb", rgb_png, 0, {200, 90, 20}, false},
      {"tint_rgb", rgb_png, 1, {30, 160, 60}, false},
      {"shift_greyscale", grey_png, 0, {25, 50, 210}, false},
      {"tint_greyscale", grey_png, 1, {200, 90, 20}, false},
      {"shift_png_alpha", rgba_png, 0, {70, 25, 65}, false},
      {"tint_png_alpha", rgba_png, 1, {140, 140, 140}, false},
      {"shift_jpeg", jpeg_path, 0, {235, 185, 200}, true},
      {"tint_jpeg", jpeg_path, 1, {30, 160, 60}, true},
  }};

  su_init();
  void* model = nullptr;
  ASSERT_EQ(su_model_create(&model), 0);

  void* ents = nullptr;
  ASSERT_EQ(su_model_get_ents(model, &ents), 0);
  std::map<std::string, ByteBuffer> untinted_rgba_by_case;
  std::map<std::string, ByteBuffer> oracle_rgba_by_case;

  auto read_saved_rep_rgba = [&](void* rep_handle, const std::filesystem::path& png_out) {
    const std::string u8 = png_out.string();
    EXPECT_EQ(su_rep_save(rep_handle, u8.c_str()), 0);
    std::ifstream f(png_out, std::ios::binary | std::ios::ate);
    EXPECT_TRUE(f);
    const auto sz = static_cast<std::size_t>(f.tellg());
    ByteBuffer buf(sz);
    f.seekg(0);
    f.read(reinterpret_cast<char*>(buf.data()), static_cast<std::streamsize>(sz));
    const auto dec = decode_texture_rgba(buf);
    EXPECT_TRUE(dec.has_value());
    return ByteBuffer(dec->pixels.get(), dec->pixels.get() + 16u * 16u * 4u);
  };

  for (std::size_t idx = 0; idx < cases.size(); ++idx) {
    const auto& c = cases[idx];
    void* mat = nullptr;
    ASSERT_EQ(su_mat_create(&mat), 0);
    ASSERT_EQ(su_mat_set_name(mat, c.label), 0);
    void* tex = nullptr;
    const std::string path_u8 = c.img_path.string();
    ASSERT_EQ(su_tex_create(&tex, path_u8.c_str(), 1.0, 1.0), 0);
    ASSERT_EQ(su_mat_set_tex(mat, tex), 0);
    ASSERT_EQ(su_model_add_mats(model, 1, &mat), 0);
    const SuColor col{static_cast<unsigned char>(c.trgb[0]), static_cast<unsigned char>(c.trgb[1]),
                      static_cast<unsigned char>(c.trgb[2]), 255};
    ASSERT_EQ(su_mat_set_col(mat, &col), 0);
    ASSERT_EQ(su_mat_set_type(mat, 2), 0);
    ASSERT_EQ(su_mat_set_ctype(mat, c.ctype), 0);

    void* base_rep = nullptr;
    ASSERT_EQ(su_rep_create(&base_rep), 0);
    ASSERT_EQ(su_tex_get_img_rep(tex, &base_rep), 0);
    untinted_rgba_by_case[c.label] =
        read_saved_rep_rgba(base_rep, tmp.path / (std::string("base_") + c.label + ".png"));
    ASSERT_EQ(su_rep_release(&base_rep), 0);

    void* rep = nullptr;
    ASSERT_EQ(su_rep_create(&rep), 0);
    ASSERT_EQ(su_tex_get_col_rep(tex, &rep), 0);
    oracle_rgba_by_case[c.label] =
        read_saved_rep_rgba(rep, tmp.path / (std::string("oracle_") + c.label + ".png"));
    ASSERT_EQ(su_rep_release(&rep), 0);

    const double x0 = static_cast<double>(idx) * 4.0;
    const std::array<SuPoint3D, 4> pts{{
        {x0, 0.0, 0.0},
        {x0 + 2.0, 0.0, 0.0},
        {x0 + 2.0, 2.0, 0.0},
        {x0, 2.0, 0.0},
    }};
    void* loop = nullptr;
    ASSERT_EQ(su_loop_create(&loop), 0);
    for (std::size_t vi = 0; vi < 4; ++vi) ASSERT_EQ(su_loop_add_vi(loop, vi), 0);
    void* face = nullptr;
    ASSERT_EQ(su_face_create(&face, pts.data(), &loop), 0);
    ASSERT_EQ(su_face_set_front(face, mat), 0);
    ASSERT_EQ(su_ents_add_faces(ents, 1, &face), 0);
  }

  for (const auto& [ver_name, ver_code] :
       std::array<std::pair<const char*, int>, 2>{{{"2017", 10}, {"2021", 14}}}) {
    const auto skp_out = tmp.path / (std::string("sdk_colorize_") + ver_name + ".skp");
    const std::string skp_u8 = skp_out.string();
    ASSERT_EQ(su_model_save_ver(model, skp_u8.c_str(), ver_code), 0);

    const auto skp = SkpFile::open(skp_out);
    for (bool instanced : {false, true}) {
      const ByteBuffer glb_bytes =
          instanced ? to_instanced_glb(skp.build_instanced_scene(), InstancedGlbOptions{true})
                    : to_glb(skp.build_scene(), GlbOptions{true});
      const auto glb_model = load_glb_model(glb_bytes);
      ASSERT_EQ(glb_model.images.size(), cases.size());

      std::vector<std::pair<std::string, ByteBuffer>> decoded_glb_images;
      for (std::size_t i = 0; i < glb_model.images.size(); ++i) {
        const ByteBuffer raw = extract_glb_image_bytes(glb_model, i);
        const auto dec = decode_texture_rgba(raw);
        ASSERT_TRUE(dec.has_value());
        ASSERT_EQ(dec->width, 16);
        ASSERT_EQ(dec->height, 16);
        decoded_glb_images.emplace_back(
            glb_model.images[i].mimeType,
            ByteBuffer(dec->pixels.get(), dec->pixels.get() + 16u * 16u * 4u));
      }

      const auto src_jpeg_dec = decode_texture_rgba(src_jpeg_bytes);
      ASSERT_TRUE(src_jpeg_dec.has_value());

      for (const auto& c : cases) {
        const ByteBuffer& oracle = oracle_rgba_by_case.at(c.label);
        if (c.is_jpeg) {
          // 1. Same decoded pixels (from SUTextureGetImageRep) -> colorize_image_u8 matches
          //    SUTextureGetColorizedImageRep within <= 1/255.
          const ByteBuffer& sdk_untinted = untinted_rgba_by_case.at(c.label);
          const ByteBuffer pre_same_dec =
              colorize_image_u8(sdk_untinted.data(), 16, 16, c.trgb, c.ctype, true);
          int max_same_dec = 0;
          // 2. stb_image IDCT vs SketchUp's libjpeg-turbo IDCT differs by 1-2/255 on JPEG
          //    blocks before colorize (<= 4/255 after HLS shift/tint).
          const ByteBuffer pre_stbi_dec =
              colorize_image_u8(src_jpeg_dec->pixels.get(), 16, 16, c.trgb, c.ctype, true);
          int max_stbi_dec = 0;
          for (std::size_t p = 0; p < 16u * 16u; ++p) {
            for (std::size_t ch = 0; ch < 3; ++ch) {
              max_same_dec =
                  std::max(max_same_dec, std::abs(static_cast<int>(pre_same_dec[p * 4 + ch]) -
                                                  static_cast<int>(oracle[p * 4 + ch])));
              max_stbi_dec =
                  std::max(max_stbi_dec, std::abs(static_cast<int>(pre_stbi_dec[p * 4 + ch]) -
                                                  static_cast<int>(oracle[p * 4 + ch])));
            }
          }
          EXPECT_LE(max_same_dec, 1) << ver_name << " " << c.label;
          EXPECT_LE(max_stbi_dec, 4) << ver_name << " " << c.label;
        }

        const std::string expected_mime = c.is_jpeg ? "image/jpeg" : "image/png";
        int best_diff = 999;
        for (const auto& [mime, px] : decoded_glb_images) {
          if (mime != expected_mime) continue;
          bool alpha_match = true;
          int max_rgb_diff = 0;
          for (std::size_t p = 0; p < 16u * 16u; ++p) {
            if (px[p * 4 + 3] != oracle[p * 4 + 3]) {
              alpha_match = false;
              break;
            }
            for (std::size_t ch = 0; ch < 3; ++ch) {
              max_rgb_diff = std::max(max_rgb_diff, std::abs(static_cast<int>(px[p * 4 + ch]) -
                                                             static_cast<int>(oracle[p * 4 + ch])));
            }
          }
          if (alpha_match) best_diff = std::min(best_diff, max_rgb_diff);
        }
        const int limit = c.is_jpeg ? 22 : 1;
        EXPECT_LE(best_diff, limit) << ver_name << " " << c.label;
      }
    }
  }

  ASSERT_EQ(su_model_release(&model), 0);
  su_term();
}
#endif

}  // namespace
}  // namespace openskp
