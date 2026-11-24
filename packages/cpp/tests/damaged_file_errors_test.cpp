#include <algorithm>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <gtest/gtest.h>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

namespace openskp {
namespace {

ByteBuffer read_fixture_bytes(const char* name) {
  const auto path = test::fixture(name);
  std::ifstream file(path, std::ios::binary | std::ios::ate);
  if (!file) throw std::runtime_error("Cannot open fixture: " + path.string());
  const auto size = static_cast<std::size_t>(file.tellg());
  ByteBuffer data(size);
  file.seekg(0);
  file.read(reinterpret_cast<char*>(data.data()), static_cast<std::streamsize>(size));
  return data;
}

TEST(DamagedFileErrors, TruncatedVffZipRaisesSkpParseError) {
  const auto raw = read_fixture_bytes("SU_File.skp");
  for (double fraction : {0.1, 0.5}) {
    const auto cut = std::max<std::size_t>(64, static_cast<std::size_t>(raw.size() * fraction));
    ByteBuffer truncated(raw.begin(), raw.begin() + static_cast<std::ptrdiff_t>(cut));
    const auto skp = SkpFile::from_buffer(std::move(truncated));

    try {
      (void)skp.parse();
      FAIL() << "Expected SkpParseError from parse()";
    } catch (const SkpParseError& err) {
      ASSERT_TRUE(err.stage().has_value());
      EXPECT_EQ(*err.stage(), ParseStage::zip_extract);
    }

    EXPECT_THROW((void)skp.build_scene(), SkpParseError);
    EXPECT_THROW((void)skp.build_instanced_scene(), SkpParseError);
  }
}

TEST(DamagedFileErrors, TruncatedClassicFileRaisesSkpParseErrorWithCause) {
  const auto raw = read_fixture_bytes("capilla_quiroz_v17.skp");
  for (double fraction : {0.1, 0.5}) {
    const auto cut = std::max<std::size_t>(64, static_cast<std::size_t>(raw.size() * fraction));
    ByteBuffer truncated(raw.begin(), raw.begin() + static_cast<std::ptrdiff_t>(cut));
    const auto skp = SkpFile::from_buffer(std::move(truncated));

    try {
      (void)skp.parse();
      FAIL() << "Expected SkpParseError from parse()";
    } catch (const SkpParseError& err) {
      ASSERT_TRUE(err.stage().has_value());
      EXPECT_EQ(*err.stage(), ParseStage::legacy_walk);
      EXPECT_NE(err.cause(), nullptr);
    }

    EXPECT_THROW((void)skp.build_scene(), SkpParseError);
    EXPECT_THROW((void)skp.build_instanced_scene(), SkpParseError);
  }
}

TEST(DamagedFileErrors, MissingFileKeepsFilesystemError) {
  const auto missing = std::filesystem::temp_directory_path() / "openskp_does_not_exist_588.skp";
  std::error_code ec;
  std::filesystem::remove(missing, ec);
  EXPECT_THROW((void)SkpFile::open(missing), std::filesystem::filesystem_error);
}

TEST(DamagedFileErrors, ClassicFixtureCutAtEveryOffsetReturnsOrRaisesSkpParseError) {
  const auto raw = read_fixture_bytes("legacy_v4_textured.skp");
  for (std::size_t cut = 0; cut <= raw.size(); ++cut) {
    ByteBuffer slice(raw.begin(), raw.begin() + static_cast<std::ptrdiff_t>(cut));
    try {
      (void)SkpFile::from_buffer(std::move(slice)).parse();
    } catch (const SkpParseError&) {
    }
  }
}

TEST(DamagedFileErrors, PartialClassicRootReadIsNotHidden) {
  SkpBuilder builder;
  builder.add_face({{0, 0, 0}, {10, 0, 0}, {10, 10, 0}, {0, 10, 0}});
  builder.add_text("First", {0, 0, 0});
  builder.add_text("Broken", {5, 0, 0});
  auto bytes = builder.to_bytes();

  const ByteBuffer marker{0x01, 0xff, 0xfe, 0xff, 0x06, 'B', 0};
  auto at = std::search(bytes.begin(), bytes.end(), marker.begin(), marker.end());
  ASSERT_NE(at, bytes.end());
  std::fill(at, at + 4, std::uint8_t{0x00});

  const auto model = SkpFile::from_buffer(std::move(bytes)).parse();
  ASSERT_EQ(model.root().texts.size(), 1u);
  EXPECT_GT(model.unread_root_entities, 0u);
}

}  // namespace
}  // namespace openskp
