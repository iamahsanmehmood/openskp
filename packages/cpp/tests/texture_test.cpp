#include <algorithm>
#include <gtest/gtest.h>
#include <map>
#include <miniz.h>
#include <stdexcept>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

namespace openskp {
namespace {

ByteBuffer synthetic_skp(const std::map<std::string, ByteBuffer>& entries) {
  mz_zip_archive zip{};
  if (!mz_zip_writer_init_heap(&zip, 0, 0)) throw std::runtime_error("cannot create test ZIP");

  const auto add = [&](const std::string& name, const ByteBuffer& data) {
    const void* bytes = data.empty() ? nullptr : data.data();
    if (!mz_zip_writer_add_mem(&zip, name.c_str(), bytes, data.size(), MZ_NO_COMPRESSION)) {
      throw std::runtime_error("cannot add test ZIP entry");
    }
  };

  add("model.dat", {});
  for (const auto& [name, data] : entries) add(name, data);

  void* archive = nullptr;
  std::size_t archive_size = 0;
  if (!mz_zip_writer_finalize_heap_archive(&zip, &archive, &archive_size)) {
    mz_zip_writer_end(&zip);
    throw std::runtime_error("cannot finalize test ZIP");
  }
  mz_zip_writer_end(&zip);

  ByteBuffer result(32);
  result[0] = 0xff;
  result[1] = 0xfe;
  result[2] = 0xff;
  result[3] = 0x0e;
  const auto* begin = static_cast<const std::uint8_t*>(archive);
  result.insert(result.end(), begin, begin + archive_size);
  mz_free(archive);
  return result;
}

ByteBuffer textured_xml(const std::string& name, const std::string& filename) {
  return test::bytes(
      "<?xml version=\"1.0\"?><materialDocument "
      "xmlns:mat=\"http://sketchup.google.com/schemas/sketchup/1.0/material\">"
      "<mat:material name=\"" +
      name +
      "\" type=\"1\" colorRed=\"10\" colorGreen=\"20\" "
      "colorBlue=\"30\" trans=\"1\" hasTexture=\"1\">"
      "<mat:texture textureFilename=\"" +
      filename +
      "\" xScale=\"24\" yScale=\"12\"/>"
      "</mat:material></materialDocument>");
}

const Material& material(const SkpModel& model, const std::string& name) {
  const auto found = std::find_if(model.materials.begin(), model.materials.end(),
                                  [&](const Material& value) { return value.name == name; });
  if (found == model.materials.end()) throw std::runtime_error("test material not found");
  return *found;
}

TEST(Texture, ExtractsImageFilenameAndTileSize) {
  const ByteBuffer jpeg{0xff, 0xd8, 's', 'y', 'n', 't', 'h', 'e', 't', 'i', 'c'};
  const auto plain = test::bytes(
      "<materialDocument xmlns:mat=\"http://sketchup.google.com/schemas/sketchup/1.0/material\">"
      "<mat:material name=\"Plain\" type=\"0\" colorRed=\"1\" colorGreen=\"2\" "
      "colorBlue=\"3\"/></materialDocument>");
  auto model = parse_skp(synthetic_skp({
      {"materials/Wood/material.xml", textured_xml("Wood", "wood.jpg")},
      {"materials/Wood/wood.jpg", jpeg},
      {"materials/Plain/material.xml", plain},
  }));

  const auto& wood = material(model, "Wood");
  ASSERT_TRUE(wood.texture.has_value());
  EXPECT_EQ(wood.texture->filename, "wood.jpg");
  EXPECT_DOUBLE_EQ(wood.texture->width, 24);
  EXPECT_DOUBLE_EQ(wood.texture->height, 12);
  EXPECT_EQ(wood.texture->data, jpeg);
  EXPECT_FALSE(material(model, "Plain").texture.has_value());
}

TEST(Texture, FallsBackToFolderSiblingForMismatchedFilename) {
  const ByteBuffer jpeg{0xff, 0xd8, 's', 'i', 'b', 'l', 'i', 'n', 'g'};
  auto model = parse_skp(synthetic_skp({
      {"materials/Glass/material.xml", textured_xml("Glass", "glass_safety.jpg")},
      {"materials/Glass/glass_saftey.jpg", jpeg},
  }));

  const auto& glass = material(model, "Glass");
  ASSERT_TRUE(glass.texture.has_value());
  EXPECT_EQ(glass.texture->data, jpeg);
}

TEST(Texture, ResolvesColorizedCopySharingSourceImage) {
  const ByteBuffer png{0x89, 'P', 'N', 'G', 's', 'h', 'a', 'r', 'e', 'd'};
  const auto colorized = test::bytes(
      "<materialDocument xmlns:mat=\"http://sketchup.google.com/schemas/sketchup/1.0/material\">"
      "<mat:material name=\"[Fence]1\" type=\"2\" colorRed=\"27\" colorGreen=\"135\" "
      "colorBlue=\"59\" colorizeType=\"0\">"
      "<mat:texture textureFilename=\"fence.png\" xScale=\"2.75\" yScale=\"2.75\">"
      "<mat:images><mat:image path=\"materials/Fence/fence.png\"/></mat:images>"
      "</mat:texture></mat:material></materialDocument>");
  auto model = parse_skp(synthetic_skp({
      {"materials/Fence/material.xml", textured_xml("Fence", "fence.png")},
      {"materials/Fence/fence.png", png},
      {"materials/[Fence]1/material.xml", colorized},
  }));

  const auto& copy = material(model, "[Fence]1");
  ASSERT_TRUE(copy.texture.has_value());
  EXPECT_EQ(copy.texture->data, png);
  EXPECT_TRUE(copy.colorized);
  EXPECT_EQ(copy.colorize_type, 0);
  EXPECT_EQ(copy.color, (Color4{27, 135, 59, 255}));
  EXPECT_FALSE(material(model, "Fence").colorized);
}

TEST(Material, AppliesTransparencyOnlyWhenEnabled) {
  const auto enabled = test::bytes(
      "<materialDocument xmlns:mat=\"http://sketchup.google.com/schemas/sketchup/1.0/material\">"
      "<mat:material name=\"Enabled\" trans=\"0.27\" useTrans=\"1\"/></materialDocument>");
  const auto disabled = test::bytes(
      "<materialDocument xmlns:mat=\"http://sketchup.google.com/schemas/sketchup/1.0/material\">"
      "<mat:material name=\"Disabled\" trans=\"0.27\" useTrans=\"0\"/></materialDocument>");
  auto model = parse_skp(synthetic_skp({
      {"materials/Enabled/material.xml", enabled},
      {"materials/Disabled/material.xml", disabled},
  }));

  EXPECT_NEAR(material(model, "Enabled").transparency, 0.73, 1e-9);
  EXPECT_DOUBLE_EQ(material(model, "Disabled").transparency, 1.0);
}

}  // namespace
}  // namespace openskp
