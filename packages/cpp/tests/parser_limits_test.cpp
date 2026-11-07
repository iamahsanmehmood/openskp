#include <cstdint>
#include <cstring>
#include <gtest/gtest.h>
#include <miniz.h>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include <openskp/openskp.hpp>

#include "internal.hpp"
#include "test_helpers.hpp"

// Hostile-input limits. A model.dat that nests TLV containers tens of
// thousands of levels deep killed the process with a stack overflow (SIGSEGV
// on Linux, an uncatchable abort on Windows); a meta.dat wrapped 100,000
// times did the same; a truncated model.dat parsed as a model with no
// geometry and no error. All three are now a SkpParseError, or unknown units
// for the meta wrappers.

namespace openskp::test {
namespace {

// `levels` records of `tag`, each inside the previous, around `leaf`. Built in
// one pass: wrapping level by level would copy the payload each time and take
// quadratic time for the 100,000-level cases.
ByteBuffer nested(const char* tag, std::size_t levels, const ByteBuffer& leaf = {}) {
  const auto header = tlv(tag);
  ByteBuffer out;
  out.reserve(6 * levels + leaf.size());
  for (std::size_t i = 0; i < levels; ++i) {
    const auto size = static_cast<std::uint32_t>(leaf.size() + 6 * (levels - 1 - i));
    out.push_back(header[0]);
    out.push_back(header[1]);
    for (int b = 0; b < 4; ++b) out.push_back(static_cast<std::uint8_t>(size >> (8 * b)));
  }
  out.insert(out.end(), leaf.begin(), leaf.end());
  return out;
}

ByteBuffer build_skp(const ByteBuffer& model_dat, const ByteBuffer* meta_dat = nullptr) {
  mz_zip_archive zip{};
  if (!mz_zip_writer_init_heap(&zip, 0, 0)) throw std::runtime_error("zip writer init failed");
  const auto add = [&](const char* name, const ByteBuffer& content) {
    const void* data = content.empty() ? static_cast<const void*>("") : content.data();
    if (!mz_zip_writer_add_mem(&zip, name, data, content.size(), MZ_BEST_SPEED))
      throw std::runtime_error("zip writer add failed");
  };
  add("model.dat", model_dat);
  if (meta_dat) add("meta/meta.dat", *meta_dat);
  void* buf = nullptr;
  std::size_t size = 0;
  if (!mz_zip_writer_finalize_heap_archive(&zip, &buf, &size))
    throw std::runtime_error("zip writer finalize failed");
  ByteBuffer zip_bytes(static_cast<std::uint8_t*>(buf), static_cast<std::uint8_t*>(buf) + size);
  mz_free(buf);
  mz_zip_writer_end(&zip);
  ByteBuffer header(32, 0);
  header[0] = 0xFF;
  header[1] = 0xFE;
  header[2] = 0xFF;
  header[3] = 0x0E;
  return concat({header, zip_bytes});
}

ByteBuffer fixture_model_dat(const char* name) {
  const auto file = read_fixture(name);
  std::size_t zip_at = 0;
  while (zip_at + 4 <= file.size() && std::memcmp(file.data() + zip_at, "PK\3\4", 4) != 0) ++zip_at;
  mz_zip_archive zip{};
  if (!mz_zip_reader_init_mem(&zip, file.data() + zip_at, file.size() - zip_at, 0))
    throw std::runtime_error("fixture is not a zip");
  std::size_t size = 0;
  void* data = mz_zip_reader_extract_file_to_heap(&zip, "model.dat", &size, 0);
  if (!data) throw std::runtime_error("fixture has no model.dat");
  ByteBuffer out(static_cast<std::uint8_t*>(data), static_cast<std::uint8_t*>(data) + size);
  mz_free(data);
  mz_zip_reader_end(&zip);
  return out;
}

// Runs `body`, which must throw a SkpParseError, and returns it.
template <typename Body>
SkpParseError parse_error_from(Body&& body) {
  try {
    body();
  } catch (const SkpParseError& e) {
    return e;
  }
  ADD_FAILURE() << "expected a SkpParseError";
  return SkpParseError("none");
}

const ByteBuffer inches = tlv("6D00", ByteBuffer{'I', 'n', 'c', 'h', 'e', 's'});

TEST(TlvDepth, FollowsTheDeepestNestingTheLimitAllows) {
  const auto data = nested("F401", kMaxTlvDepth);
  EXPECT_EQ(parse_tlv_recursive(data, 0, data.size()).size(), 1u);
}

TEST(TlvDepth, RejectsDeepNestingWithAParseError) {
  for (std::size_t levels :
       {std::size_t(kMaxTlvDepth) + 2, std::size_t(5000), std::size_t(200000)}) {
    const auto data = nested("F401", levels);
    const auto e = parse_error_from([&] { parse_tlv_recursive(data, 0, data.size()); });
    EXPECT_EQ(e.stage(), ParseStage::tlv_walk) << levels;
    EXPECT_EQ(e.tag(), std::optional<std::string>("F401")) << levels;
    EXPECT_NE(std::string(e.what()).find("nest deeper"), std::string::npos) << e.what();
  }
}

TEST(TlvDepth, ParseSkpRejectsDeepNesting) {
  for (std::size_t levels :
       {std::size_t(kMaxTlvDepth) + 2, std::size_t(5000), std::size_t(200000)}) {
    const auto skp = build_skp(tlv("F401", nested("F401", levels)));
    const auto e = parse_error_from([&] { parse_skp(skp); });
    EXPECT_NE(std::string(e.what()).find("nest deeper"), std::string::npos) << e.what();
  }
}

TEST(TlvDepth, ParseSkpRejectsNestedAttributeDictionaries) {
  // An attribute dictionary payload (DC05) nests B536 containers, which are
  // walked by their own recursion rather than by parse_tlv_recursive.
  const auto dc05 = tlv("DC05", nested("B536", 100000));
  const auto model = tlv("F401", tlv("F601", tlv("8813", tlv("D007", dc05))));
  const auto skp = build_skp(model);
  const auto e = parse_error_from([&] { parse_skp(skp); });
  EXPECT_NE(std::string(e.what()).find("nest deeper"), std::string::npos) << e.what();
}

TEST(TlvDepth, ParseSkpAcceptsModerateNesting) {
  const auto skp = build_skp(tlv("F401", nested("F401", 40)));
  EXPECT_NO_THROW(parse_skp(skp));
}

TEST(MetaWrappers, FollowsAFewWrappers) {
  ByteBuffer meta = inches;
  for (int i = 0; i < 3; ++i) meta = tlv("6400", meta);
  EXPECT_EQ(read_meta_units(meta), std::optional<std::string>("Inches"));
}

TEST(MetaWrappers, DeepWrappersGiveUnknownUnitsWithoutRecursing) {
  for (std::size_t levels : {std::size_t(kMaxTlvDepth) + 5, std::size_t(100000)})
    EXPECT_EQ(read_meta_units(nested("6400", levels, inches)), std::nullopt) << levels;
}

TEST(MetaWrappers, DeepWrappersDoNotFailTheParse) {
  const auto meta = nested("6400", 100000, inches);
  const auto skp = build_skp(tlv("F401"), &meta);
  const auto model = parse_skp(skp);
  EXPECT_FALSE(model.units.has_value());
}

TEST(Truncation, RealFilesEndExactlyOnTheirTopLevelRecord) {
  for (const char* name : {"SU_File.skp", "Untitled.skp"}) {
    const auto model = fixture_model_dat(name);
    EXPECT_EQ(6u + read_u32(model, 2), model.size()) << name;
    EXPECT_NO_THROW(parse_skp(read_fixture(name))) << name;
  }
}

TEST(Truncation, ACutShortModelDatIsAnError) {
  const auto model = fixture_model_dat("SU_File.skp");
  for (int percent : {50, 99}) {
    const ByteBuffer cut(model.begin(),
                         model.begin() + static_cast<std::ptrdiff_t>(model.size() * percent / 100));
    const auto skp = build_skp(cut);
    const auto e = parse_error_from([&] { parse_skp(skp); });
    EXPECT_NE(std::string(e.what()).find("truncated"), std::string::npos) << e.what();
    EXPECT_EQ(e.stage(), ParseStage::tlv_walk);
    EXPECT_EQ(e.tag(), std::optional<std::string>("F401"));
    EXPECT_EQ(e.offset(), std::optional<std::uint64_t>(0));
  }
}

TEST(Truncation, TrailingBytesShorterThanAHeaderAreIgnored) {
  auto model = fixture_model_dat("SU_File.skp");
  model.insert(model.end(), {0, 0, 0});
  EXPECT_NO_THROW(parse_skp(build_skp(model)));
}

TEST(Truncation, AnEmptyModelDatStillParses) { EXPECT_NO_THROW(parse_skp(build_skp({}))); }

}  // namespace
}  // namespace openskp::test
