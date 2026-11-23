#include <algorithm>
#include <gtest/gtest.h>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

namespace openskp {
namespace {

// The classic root reader keeps what it read before the first record it can't parse and drops
// the rest of the root. SkpModel::unread_root_entities says how many entities that dropped, so a
// partial read isn't silent.

TEST(LegacyUnreadRootEntities, CompleteFilesReportNone) {
  for (const char* name : {"legacy_annotations.skp", "capilla_quiroz_v17.skp", "gondola_v20.skp",
                           "legacy_text_arrows_2017.skp", "nesting_2017.skp", "SU_File.skp"}) {
    std::vector<std::pair<LogLevel, std::string>> logs;
    ParseOptions options;
    options.log = [&](LogLevel level, std::string_view message) {
      logs.emplace_back(level, message);
    };
    EXPECT_EQ(SkpFile::open(test::fixture(name)).parse(options).unread_root_entities, 0u) << name;
    EXPECT_TRUE(std::none_of(logs.begin(), logs.end(), [](const auto& entry) {
      return entry.first == LogLevel::warning;
    })) << name;
  }
}

TEST(LegacyUnreadRootEntities, AnUnreadableTextDropsTheRestOfTheRoot) {
  SkpBuilder builder;
  builder.add_face({{0, 0, 0}, {10, 0, 0}, {10, 10, 0}, {0, 10, 0}});
  builder.add_text("First", {0, 0, 0});
  builder.add_text("Broken", {5, 0, 0});
  builder.add_face({{20, 0, 0}, {30, 0, 0}, {30, 10, 0}, {20, 10, 0}});
  auto bytes = builder.to_bytes();
  const auto complete_skp = SkpFile::from_buffer(bytes);
  const auto complete = complete_skp.parse();
  ASSERT_EQ(complete.root().texts.size(), 2u);
  ASSERT_EQ(complete.unread_root_entities, 0u);
  EXPECT_EQ(complete_skp.build_scene().unread_root_entities, 0u);
  const auto complete_instanced = complete_skp.build_instanced_scene();
  EXPECT_EQ(complete_instanced.unread_root_entities, 0u);
  EXPECT_EQ(from_fragments(to_fragments(complete_instanced)).unread_root_entities, 0u);

  // Break the second text's string marker so the reader can't find its text.
  const ByteBuffer marker{0x01, 0xff, 0xfe, 0xff, 0x06, 'B', 0};
  auto at = std::search(bytes.begin(), bytes.end(), marker.begin(), marker.end());
  ASSERT_NE(at, bytes.end());
  std::fill(at, at + 4, std::uint8_t{0x00});

  std::vector<std::pair<LogLevel, std::string>> logs;
  ParseOptions options;
  options.log = [&](LogLevel level, std::string_view message) {
    logs.emplace_back(level, message);
  };
  const auto broken_skp = SkpFile::from_buffer(std::move(bytes));
  const auto model = broken_skp.parse(options);
  ASSERT_EQ(model.root().texts.size(), 1u);
  EXPECT_EQ(model.root().texts[0].text, "First");
  EXPECT_GT(model.unread_root_entities, 0u);
  EXPECT_EQ(broken_skp.build_scene().unread_root_entities, model.unread_root_entities);
  EXPECT_EQ(broken_skp.build_instanced_scene().unread_root_entities, model.unread_root_entities);
  const auto unread_token = std::to_string(model.unread_root_entities) + " unread";
  EXPECT_TRUE(std::any_of(logs.begin(), logs.end(), [&](const auto& entry) {
    return entry.first == LogLevel::warning &&
           entry.second.find("stopped early") != std::string::npos &&
           entry.second.find(unread_token) != std::string::npos;
  }));
}

}  // namespace
}  // namespace openskp
