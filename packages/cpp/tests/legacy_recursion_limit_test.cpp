#include <algorithm>
#include <cstdint>
#include <gtest/gtest.h>
#include <stdexcept>
#include <string>
#include <vector>

#include <openskp/openskp.hpp>

// Classic (MFC archive) reader recursion limits (#557).

namespace openskp {
namespace {

constexpr std::size_t kMaxLegacyDepth = 64;

void add_u16(ByteBuffer& out, std::uint16_t x) {
  out.push_back(static_cast<std::uint8_t>(x & 0xFF));
  out.push_back(static_cast<std::uint8_t>((x >> 8) & 0xFF));
}

void add_u32(ByteBuffer& out, std::uint32_t x) {
  out.push_back(static_cast<std::uint8_t>(x & 0xFF));
  out.push_back(static_cast<std::uint8_t>((x >> 8) & 0xFF));
  out.push_back(static_cast<std::uint8_t>((x >> 16) & 0xFF));
  out.push_back(static_cast<std::uint8_t>((x >> 24) & 0xFF));
}

void add_wstr(ByteBuffer& out, const std::string& s) {
  out.push_back(0xFF);
  out.push_back(0xFE);
  out.push_back(0xFF);
  out.push_back(static_cast<std::uint8_t>(s.size()));
  for (unsigned char c : s) {
    out.push_back(c);
    out.push_back(0);
  }
}

void add_cdecl(ByteBuffer& out, const std::string& name, std::uint16_t schema = 0) {
  add_u16(out, 0xFFFF);
  add_u16(out, schema);
  add_u16(out, static_cast<std::uint16_t>(name.size()));
  out.insert(out.end(), name.begin(), name.end());
}

std::size_t find_ascii(const ByteBuffer& buf, const std::string& needle) {
  auto it = std::search(buf.begin(), buf.end(), needle.begin(), needle.end());
  if (it == buf.end()) throw std::runtime_error("needle not found: " + needle);
  return static_cast<std::size_t>(it - buf.begin());
}

ByteBuffer splice_at(const ByteBuffer& raw, std::size_t pos, std::size_t replace_len,
                     const ByteBuffer& mid) {
  ByteBuffer out;
  out.reserve(raw.size() - replace_len + mid.size());
  out.insert(out.end(), raw.begin(), raw.begin() + static_cast<std::ptrdiff_t>(pos));
  out.insert(out.end(), mid.begin(), mid.end());
  out.insert(out.end(), raw.begin() + static_cast<std::ptrdiff_t>(pos + replace_len), raw.end());
  return out;
}

ByteBuffer make_nested_object_skp(std::size_t total_depth, bool on_layer) {
  SkpBuilder builder;
  builder.add_construction_point({1.0, 2.0, 3.0});
  const ByteBuffer raw = builder.to_bytes();
  const std::string tag = on_layer ? "CLayer" : "CConstructionPoint";
  const std::uint16_t attr_cls_slot = on_layer ? 11 : 13;
  const std::size_t pos = find_ascii(raw, tag) + tag.size();
  if (total_depth <= 1) return raw;
  const std::size_t k = total_depth - 1;
  ByteBuffer chain;
  add_cdecl(chain, "CAttributeContainer");
  for (std::size_t i = 0; i < k - 1; ++i) {
    add_u16(chain, static_cast<std::uint16_t>(0x8000u | attr_cls_slot));
  }
  add_u16(chain, 0);
  for (std::size_t i = 0; i < k; ++i) {
    chain.push_back(0);
    add_u16(chain, 0);
  }
  return splice_at(raw, pos, 2, chain);
}

ByteBuffer make_nested_attr_array_skp(std::size_t array_depth) {
  SkpBuilder builder;
  builder.add_construction_point({1.0, 2.0, 3.0});
  const ByteBuffer raw = builder.to_bytes();
  const std::string tag = "CConstructionPoint";
  const std::size_t pos = find_ascii(raw, tag) + tag.size();
  ByteBuffer payload;
  add_cdecl(payload, "CAttributeContainer");
  add_u16(payload, 0);
  payload.push_back(0);
  add_cdecl(payload, "CAttributeNamed");
  add_u16(payload, 0);
  payload.push_back(0);
  add_u32(payload, 0);
  add_wstr(payload, "Dict");
  add_wstr(payload, "k");
  for (std::size_t i = 0; i < array_depth; ++i) {
    payload.push_back(0x0B);
    add_u32(payload, 1);
  }
  payload.push_back(0x04);
  add_u32(payload, 7);
  add_wstr(payload, "");
  add_u32(payload, 0);
  add_u16(payload, 0);
  return splice_at(raw, pos, 2, payload);
}

TEST(LegacyRecursionLimit, ObjectNestingWithinLimitParses) {
  for (bool on_layer : {false, true}) {
    for (std::size_t depth : {std::size_t{1}, std::size_t{10}, kMaxLegacyDepth}) {
      const auto model = SkpFile::from_buffer(make_nested_object_skp(depth, on_layer)).parse();
      EXPECT_EQ(model.unread_root_entities, 0u);
      EXPECT_EQ(model.root().construction_points.size(), 1u);
    }
  }
}

TEST(LegacyRecursionLimit, Boundary65ObjectNestingThrowsSkpParseError) {
  for (bool on_layer : {false, true}) {
    try {
      (void)SkpFile::from_buffer(make_nested_object_skp(kMaxLegacyDepth + 1, on_layer)).parse();
      FAIL() << "Expected SkpParseError for depth 65 (on_layer=" << on_layer << ")";
    } catch (const SkpParseError& e) {
      EXPECT_EQ(e.stage(), std::optional<ParseStage>{ParseStage::legacy_walk});
    }
  }
}

TEST(LegacyRecursionLimit, Deep5000ObjectNestingThrowsSkpParseErrorWithoutStackOverflow) {
  for (bool on_layer : {false, true}) {
    try {
      (void)SkpFile::from_buffer(make_nested_object_skp(5000, on_layer)).parse();
      FAIL() << "Expected SkpParseError for depth 5000 (on_layer=" << on_layer << ")";
    } catch (const SkpParseError& e) {
      EXPECT_EQ(e.stage(), std::optional<ParseStage>{ParseStage::legacy_walk});
    }
  }
}

TEST(LegacyRecursionLimit, AttrArrayNestingWithinLimitParses) {
  for (std::size_t depth : {std::size_t{1}, std::size_t{10}, kMaxLegacyDepth}) {
    const auto model = SkpFile::from_buffer(make_nested_attr_array_skp(depth)).parse();
    EXPECT_EQ(model.unread_root_entities, 0u);
    EXPECT_EQ(model.root().construction_points.size(), 1u);
  }
}

TEST(LegacyRecursionLimit, Boundary65AttrArrayNestingThrowsSkpParseError) {
  try {
    (void)SkpFile::from_buffer(make_nested_attr_array_skp(kMaxLegacyDepth + 1)).parse();
    FAIL() << "Expected SkpParseError for attr array depth 65";
  } catch (const SkpParseError& e) {
    EXPECT_EQ(e.stage(), std::optional<ParseStage>{ParseStage::legacy_walk});
  }
}

TEST(LegacyRecursionLimit, Deep5000AttrArrayNestingThrowsSkpParseErrorWithoutStackOverflow) {
  try {
    (void)SkpFile::from_buffer(make_nested_attr_array_skp(5000)).parse();
    FAIL() << "Expected SkpParseError for attr array depth 5000";
  } catch (const SkpParseError& e) {
    EXPECT_EQ(e.stage(), std::optional<ParseStage>{ParseStage::legacy_walk});
  }
}

}  // namespace
}  // namespace openskp
