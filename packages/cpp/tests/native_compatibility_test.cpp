#include <algorithm>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <gtest/gtest.h>
#include <limits>

#include <openskp/openskp.hpp>

namespace openskp {
namespace {

std::uint64_t uint_le(const ByteBuffer& bytes, std::size_t pos, int size) {
  std::uint64_t result = 0;
  for (int i = 0; i < size; ++i) result |= std::uint64_t(bytes.at(pos + i)) << (8 * i);
  return result;
}

std::size_t locate(const ByteBuffer& bytes, const ByteBuffer& needle) {
  const auto it = std::search(bytes.begin(), bytes.end(), needle.begin(), needle.end());
  if (it == bytes.end()) throw std::runtime_error("native field not found");
  return static_cast<std::size_t>(it - bytes.begin());
}

std::uint32_t native_option(const ByteBuffer& bytes, const std::string& name) {
  ByteBuffer key{0xff, 0xfe, 0xff, static_cast<std::uint8_t>(name.size())};
  for (char c : name) {
    key.push_back(c);
    key.push_back(0);
  }
  auto pos = locate(bytes, key) + key.size();
  EXPECT_EQ(bytes.at(pos++), 4);  // native int32 typed value
  return static_cast<std::uint32_t>(uint_le(bytes, pos, 4));
}

TEST(NativeCompatibility, FacePlaneAgreesWithVertices) {
  const std::vector<std::vector<Point3>> polygons{
      {{0, 0, 3}, {2, 0, 3}, {2, 2, 3}, {0, 2, 3}},
      {{0, 2, 3}, {2, 2, 3}, {2, 0, 3}, {0, 0, 3}},
      {{-7, 0, 0}, {-7, 2, 0}, {-7, 2, 2}, {-7, 0, 2}},
      {{10, 20, 35}, {12, 20, 37}, {12, 22, 39}, {10, 22, 37}}};
  for (const auto& polygon : polygons)
    for (bool nested : {false, true}) {
      auto builder = create();
      if (nested) {
        auto& def = builder->add_component_definition("offset");
        def.add_face(polygon);
        def.close();
        builder->add_instance(def);
      } else
        builder->add_face(polygon);
      const auto bytes = builder->to_bytes();
      const ByteBuffer marker{0xff, 0xff, 3, 0, 5, 0, 'C', 'F', 'a', 'c', 'e'};
      auto pos = locate(bytes, marker) + marker.size();
      ASSERT_EQ(uint_le(bytes, pos, 2), 0u);
      pos += 2;
      const auto mask = bytes.at(pos++);
      for (int bit = 0; bit < 8; ++bit)
        if (mask & (1 << bit)) ++pos;
      pos += 10;  // material and drawing flags
      double plane[4];
      for (double& value : plane) {
        auto bits = uint_le(bytes, pos, 8);
        pos += 8;
        std::memcpy(&value, &bits, sizeof(value));
      }
      // SDK GetPlane recomputes the plane, so a raw check is essential here.
      ASSERT_GT(std::abs(plane[3]), 1);
      for (const auto& p : polygon)
        EXPECT_NEAR(plane[0] * p[0] + plane[1] * p[1] + plane[2] * p[2] + plane[3], 0, 1e-9);
    }
}

TEST(NativeCompatibility, DegenerateFanDoesNotCorruptFollowingEntities) {
  const std::vector<Point3> polygon{{0, 0, 0}, {1, 0, 0}, {1, 1, 0}, {2, 2, 0}, {0, 1, 1}};
  FaceOptions options;
  options.auto_triangulate = true;
  for (bool nested : {false, true}) {
    auto builder = create();
    if (nested) {
      auto& def = builder->add_component_definition("fan");
      def.add_face(polygon, options);
      def.close();
      builder->add_instance(def);
    } else
      builder->add_face(polygon, options);
    builder->add_face({{10, 0, 0}, {11, 0, 0}, {10, 1, 0}});
    auto model = SkpFile::from_buffer(builder->to_bytes()).parse();
    std::size_t faces = model.root().faces.size();
    for (const auto& entry : model.definitions) faces += entry.second.faces.size();
    EXPECT_EQ(faces, 3u);
  }
}

TEST(NativeCompatibility, PersistentIdsAreDocumentWide) {
  auto builder = create();
  builder->add_material("paint", Color3{12, 34, 56});
  builder->add_layer("tag");
  auto& definition = builder->add_component_definition("part");
  definition.add_face({{0, 0, 0}, {1, 0, 0}, {0, 1, 0}});
  definition.close();
  builder->add_instance(definition);
  const auto bytes = builder->to_bytes();
  // Reserved IDs [0,18), material 18, layer 19/20, definition 21,
  // vertices/edges/face 22..28 and root instance 29. SDK-native header is uint64.
  EXPECT_EQ(uint_le(bytes, 1987, 8), 30u);
  const std::string name = "CComponentInstance";
  const auto pos = locate(bytes, ByteBuffer(name.begin(), name.end())) + name.size() + 7;
  EXPECT_EQ(bytes.at(pos), 1);
  EXPECT_EQ(bytes.at(pos + 1), 29);
}

TEST(NativeCompatibility, PersistentIdCounterDoesNotTruncateAt16Bits) {
  auto builder = create();
  for (int i = 0; i < 10000; ++i) {
    const double x = i * 2.0;
    builder->add_face({{x, 0, 0}, {x + 1, 0, 0}, {x, 1, 0}});
  }
  EXPECT_EQ(uint_le(builder->to_bytes(), 1987, 8), 70018u);
}

TEST(NativeCompatibility, WritesNativeUnitsAndBothStylesWithoutScaling) {
  for (int unit : {0, 1, 2, 3, 4, 5}) {
    auto builder = create();
    DocumentOptions options;
    options.length_unit = unit;
    options.length_precision = 2;
    options.style_items = {{4000, 0xff563412u}, {1006, 0}, {1007, 5}};
    builder->set_document_options(options);
    builder->add_face({{0, 0, 0}, {2, 0, 0}, {0, 3, 0}});
    const auto bytes = builder->to_bytes();
    EXPECT_EQ(native_option(bytes, "LengthUnit"), unit);
    EXPECT_EQ(native_option(bytes, "LengthFormat"), 0u);
    EXPECT_EQ(native_option(bytes, "LengthPrecision"), 2u);
    EXPECT_EQ(native_option(bytes, "AreaUnit"), unit);
    EXPECT_EQ(native_option(bytes, "VolumeUnit"), unit);
    EXPECT_EQ(uint_le(bytes, 3177, 4), 0xff563412u);  // current rendering background
    EXPECT_EQ(bytes.at(3206), 0);                     // profile edges disabled
    EXPECT_EQ(uint_le(bytes, 3207, 4), 5u);
    // Both saved and working styles carry the same item, independently of current options.
    const ByteBuffer item{0xa0, 0x0f, 0, 0, 1, 0, 0, 0, 4, 0, 0, 0, 0x12, 0x34, 0x56, 0xff};
    auto begin = bytes.begin();
    int matches = 0;
    while ((begin = std::search(begin, bytes.end(), item.begin(), item.end())) != bytes.end()) {
      ++matches;
      begin += item.size();
    }
    EXPECT_EQ(matches, 2);
    auto model = SkpFile::from_buffer(bytes).parse();
    bool unscaled = false;
    for (const auto& v : model.root().vertices)
      if (v.second.x == 2 && v.second.y == 0 && v.second.z == 0) unscaled = true;
    EXPECT_TRUE(unscaled);
  }
}

TEST(NativeCompatibility, RejectsInvalidDocumentOptions) {
  auto builder = create();
  DocumentOptions options;
  options.length_unit = 6;
  EXPECT_THROW(builder->set_document_options(options), SkpWriteError);
  options.length_unit = 0;
  options.length_precision = 7;
  EXPECT_THROW(builder->set_document_options(options), SkpWriteError);
  options.length_precision = 2;
  options.style_items[1000] = std::numeric_limits<double>::quiet_NaN();
  EXPECT_THROW(builder->set_document_options(options), SkpWriteError);
}

TEST(NativeCompatibility, ReadsTextureTypeModeAndPaintIndependently) {
  const auto path = std::filesystem::temp_directory_path() / "openskp_native_material_test.png";

  struct Cleanup {
    std::filesystem::path path;

    ~Cleanup() {
      std::error_code e;
      std::filesystem::remove(path, e);
    }
  } cleanup{path};

  // A valid generated 2x1 RGB PNG, usable with the independent SDK checker too.
  const ByteBuffer png = {0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0,    0,    0,    0x0d,
                          0x49, 0x48, 0x44, 0x52, 0,    0,    0,    2,    0,    0,    0,    1,
                          8,    2,    0,    0,    0,    0x7b, 0x40, 0xe8, 0xdd, 0,    0,    0,
                          0x0f, 0x49, 0x44, 0x41, 0x54, 0x78, 0x9c, 0x63, 0xf8, 0xcf, 0xc0, 0xc0,
                          0xf0, 0x9f, 1,    0,    7,    0xff, 1,    0xff, 1,    0x7f, 0x89, 0xa7,
                          0,    0,    0,    0,    0x49, 0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82};
  {
    std::ofstream f(path, std::ios::binary);
    f.write(reinterpret_cast<const char*>(png.data()), png.size());
  }
  auto builder = create();
  auto mat = builder->add_texture_material("paint", path);
  auto& group = builder->add_group(GroupOptions{});
  group.add_face({{0, 0, 0}, {1, 0, 0}, {0, 1, 0}});
  group.close();
  FaceOptions face;
  face.material = mat;
  builder->add_face({{2, 0, 0}, {3, 0, 0}, {2, 1, 0}}, face);
  const auto original = builder->to_bytes();
  const auto image_end = locate(original, png) + png.size();
  // Skip tile dimensions and the native CString (path remains short in this fixture).
  const auto str = image_end + 16;
  ASSERT_EQ(original.at(str), 0xff);
  ASSERT_EQ(original.at(str + 1), 0xfe);
  ASSERT_EQ(original.at(str + 2), 0xff);
  ASSERT_LT(original.at(str + 3), 0xff);
  const auto colors = str + 4 + 2 * original.at(str + 3);
  const auto type = colors + 9 + 4;  // two colors and empty CString
  EXPECT_EQ(uint_le(original, type, 4), 1u);
  EXPECT_EQ(uint_le(original, type + 4, 4), 0u);
  for (int kind : {1, 2})
    for (int mode : {0, 1}) {
      auto bytes = original;
      bytes.at(colors) = 17;
      bytes.at(colors + 1) = 18;
      bytes.at(colors + 2) = 19;
      bytes.at(colors + 3) = 255;
      bytes.at(colors + 5) = 72;
      bytes.at(colors + 6) = 83;
      bytes.at(colors + 7) = 94;
      bytes.at(type) = kind;
      bytes.at(type + 4) = mode;
      const auto model = SkpFile::from_buffer(bytes).parse();
      ASSERT_EQ(model.materials.size(), 1u);
      EXPECT_EQ(model.materials[0].color, (Color4{72, 83, 94, 255}));
      EXPECT_EQ(model.materials[0].colorized, kind == 2);
      EXPECT_EQ(model.materials[0].colorize_type, mode);
    }
}
}  // namespace
}  // namespace openskp
