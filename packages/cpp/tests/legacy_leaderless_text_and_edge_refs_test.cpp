#include <algorithm>
#include <fstream>
#include <gtest/gtest.h>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

namespace openskp {
namespace {

ByteBuffer read_fixture_bytes(const char* name) {
  const auto path = test::fixture(name);
  std::ifstream in(path, std::ios::binary);
  return ByteBuffer(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
}

TEST(LegacyLeaderlessTextAndEdgeRefs, LeaderlessTextBeforeSharedVertexEdgesMatchesSdk) {
  std::vector<std::pair<LogLevel, std::string>> logs;
  ParseOptions options;
  options.log = [&](LogLevel level, std::string_view message) {
    logs.emplace_back(level, std::string(message));
  };

  auto skp = SkpFile::open(test::fixture("sdk_saved_leaderless_text_edges_2020.skp"));
  const auto model = skp.parse(options);
  const auto scene = skp.build_scene(options);
  const auto instanced = skp.build_instanced_scene(options);

  ASSERT_EQ(model.definitions.size(), 1u);
  const auto& post = model.definitions.begin()->second;
  EXPECT_EQ(post.name, "Post");
  EXPECT_FALSE(post.is_group);
  EXPECT_EQ(model.root().faces.size(), 0u);
  EXPECT_EQ(model.root().edges.size(), 3u);
  EXPECT_EQ(model.root().instances.size(), 1u);
  ASSERT_EQ(model.root().texts.size(), 2u);
  EXPECT_EQ(model.root().texts[0].text, "Screen title");
  EXPECT_EQ(model.root().texts[1].text, "Callout");
  EXPECT_EQ(model.unread_root_entities, 0u);
  EXPECT_EQ(scene.surface_primitives().size(), 1u);
  EXPECT_EQ(instanced.mesh_resources.size(), 1u);
  EXPECT_TRUE(std::none_of(logs.begin(), logs.end(),
                           [](const auto& e) { return e.first == LogLevel::warning; }));
}

TEST(LegacyLeaderlessTextAndEdgeRefs, RootEdgeWithNonVertexRefReportsPartialRead) {
  auto raw = read_fixture_bytes("sdk_saved_leaderless_text_edges_2020.skp");
  ASSERT_GT(raw.size(), 5325u);
  ASSERT_EQ(raw[5323], 0x31);
  ASSERT_EQ(raw[5324], 0x00);
  raw[5323] = 0x2f;  // point 2nd root edge v1 at 1st root CEdge

  std::vector<std::pair<LogLevel, std::string>> logs;
  ParseOptions options;
  options.log = [&](LogLevel level, std::string_view message) {
    logs.emplace_back(level, std::string(message));
  };

  auto skp = SkpFile::from_buffer(std::move(raw));
  const auto model = skp.parse(options);
  const auto scene = skp.build_scene();
  const auto instanced = skp.build_instanced_scene();

  EXPECT_EQ(model.root().instances.size(), 1u);
  ASSERT_EQ(model.root().texts.size(), 2u);
  EXPECT_EQ(model.root().texts[0].text, "Screen title");
  EXPECT_EQ(model.root().texts[1].text, "Callout");
  EXPECT_EQ(model.root().edges.size(), 1u);
  EXPECT_EQ(model.unread_root_entities, 2u);
  EXPECT_EQ(scene.surface_primitives().size(), 1u);
  EXPECT_EQ(instanced.mesh_resources.size(), 1u);
  EXPECT_TRUE(std::any_of(logs.begin(), logs.end(), [](const auto& e) {
    return e.first == LogLevel::warning && e.second.find("2 unread") != std::string::npos;
  }));
}

TEST(LegacyLeaderlessTextAndEdgeRefs, DefinitionEdgeWithNonVertexRefThrowsSkpParseError) {
  auto raw = read_fixture_bytes("sdk_saved_leaderless_text_edges_2020.skp");
  ASSERT_GT(raw.size(), 4041u);
  ASSERT_EQ(raw[4039], 0x14);
  ASSERT_EQ(raw[4040], 0x00);
  raw[4039] = 0x11;  // point 2nd definition edge v1 at 1st definition CEdge

  auto skp = SkpFile::from_buffer(std::move(raw));
  EXPECT_THROW((void)skp.parse(), SkpParseError);
  EXPECT_THROW((void)skp.build_scene(), SkpParseError);
  EXPECT_THROW((void)skp.build_instanced_scene(), SkpParseError);
}

}  // namespace
}  // namespace openskp
