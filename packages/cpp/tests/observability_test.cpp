#include <algorithm>
#include <filesystem>
#include <gtest/gtest.h>
#include <string>
#include <utility>
#include <vector>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

using namespace openskp;

TEST(Errors, ContextAndCause) {
  auto cause = std::make_exception_ptr(std::logic_error("inner"));
  SkpParseError e("boom", ParseStage::tlv_walk, 3, 10, std::string("F601"), {}, {}, cause);
  EXPECT_NE(std::string(e.what()).find("stage=tlv_walk"), std::string::npos);
  EXPECT_NE(std::string(e.what()).find("record=3/10"), std::string::npos);
  EXPECT_EQ(e.cause(), cause);
}

TEST(Observability, CallbacksAreOptional) {
  EXPECT_THROW(SkpFile::from_buffer(ByteBuffer(20, 'A')).parse(), SkpParseError);
  EXPECT_NO_THROW(SkpFile::open(test::fixture("Untitled.skp")).parse());
}

TEST(Observability, ParseEmitsExpectedLogs) {
  std::vector<std::pair<LogLevel, std::string>> logs;
  ParseOptions options;
  options.log = [&](LogLevel level, std::string_view message) {
    logs.emplace_back(level, message);
  };

  SkpFile::open(test::fixture("capilla_quiroz_v17.skp")).parse(options);

  ASSERT_GE(logs.size(), 4u);
  EXPECT_EQ(logs.front().first, LogLevel::information);
  EXPECT_NE(logs.front().second.find("Parsing buffer"), std::string::npos);
  EXPECT_TRUE(std::any_of(logs.begin(), logs.end(), [](const auto& entry) {
    return entry.first == LogLevel::debug && entry.second.find("legacy MFC") != std::string::npos;
  }));
  EXPECT_TRUE(std::any_of(logs.begin(), logs.end(), [](const auto& entry) {
    return entry.second.find("Parse complete") != std::string::npos;
  }));
}

TEST(Observability, SceneBuildEmitsExpectedLogs) {
  std::vector<std::string> messages;
  ParseOptions options;
  options.log = [&](LogLevel, std::string_view message) { messages.emplace_back(message); };

  SkpFile::open(test::fixture("SU_File.skp")).build_scene(options);

  EXPECT_TRUE(std::any_of(messages.begin(), messages.end(), [](const auto& message) {
    return message.find("Building scene") != std::string::npos;
  }));
  EXPECT_TRUE(std::any_of(messages.begin(), messages.end(), [](const auto& message) {
    return message.find("Scene build complete") != std::string::npos;
  }));
}

TEST(Observability, ProgressIncludesLegacyDefinitions) {
  std::vector<ParseProgress> progress;
  ParseOptions options;
  options.progress = [&](const ParseProgress& value) { progress.push_back(value); };

  auto model = SkpFile::open(test::fixture("capilla_quiroz_v17.skp")).parse(options);

  ASSERT_FALSE(progress.empty());
  const auto found = std::find_if(progress.begin(), progress.end(), [](const auto& value) {
    return value.stage == ParseStage::legacy_defs;
  });
  ASSERT_NE(found, progress.end());
  EXPECT_EQ(found->current, model.definitions.size());
  EXPECT_EQ(found->total, model.definitions.size());
}

TEST(Errors, InvalidBufferReportsHeaderStage) {
  try {
    SkpFile::from_buffer(ByteBuffer(20, 'A')).parse();
    FAIL() << "Expected SkpParseError";
  } catch (const SkpParseError& error) {
    ASSERT_TRUE(error.stage().has_value());
    EXPECT_EQ(*error.stage(), ParseStage::header);
  }
}

TEST(ParserPaths, RejectsExistingFileWithWrongExtension) {
  const auto cmake_file = std::filesystem::path(OPENSKP_FIXTURES).parent_path() / "CMakeLists.txt";
  EXPECT_THROW(SkpFile::open(cmake_file), std::invalid_argument);
}
