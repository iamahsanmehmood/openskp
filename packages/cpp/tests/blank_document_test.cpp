#include "blank_document.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <gtest/gtest.h>
#include <string>
#include <vector>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

namespace openskp {
namespace {

std::string sha256_hex(const ByteBuffer& data) {
  static constexpr std::array<std::uint32_t, 64> kK = {
      0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u, 0x3956c25bu, 0x59f111f1u, 0x923f82a4u,
      0xab1c5ed5u, 0xd807aa98u, 0x12835b01u, 0x243185beu, 0x550c7dc3u, 0x72be5d74u, 0x80deb1feu,
      0x9bdc06a7u, 0xc19bf174u, 0xe49b69c1u, 0xefbe4786u, 0x0fc19dc6u, 0x240ca1ccu, 0x2de92c6fu,
      0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau, 0x983e5152u, 0xa831c66du, 0xb00327c8u, 0xbf597fc7u,
      0xc6e00bf3u, 0xd5a79147u, 0x06ca6351u, 0x14292967u, 0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu,
      0x53380d13u, 0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u, 0xa2bfe8a1u, 0xa81a664bu,
      0xc24b8b70u, 0xc76c51a3u, 0xd192e819u, 0xd6990624u, 0xf40e3585u, 0x106aa070u, 0x19a4c116u,
      0x1e376c08u, 0x2748774cu, 0x34b0bcb5u, 0x391c0cb3u, 0x4ed8aa4au, 0x5b9cca4fu, 0x682e6ff3u,
      0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u, 0x90befffau, 0xa4506cebu, 0xbef9a3f7u,
      0xc67178f2u,
  };
  auto rotr = [](std::uint32_t x, unsigned n) -> std::uint32_t {
    return (x >> n) | (x << (32u - n));
  };
  std::array<std::uint32_t, 8> h = {
      0x6a09e667u, 0xbb67ae85u, 0x3c6ef372u, 0xa54ff53au,
      0x510e527fu, 0x9b05688cu, 0x1f83d9abu, 0x5be0cd19u,
  };
  const std::uint64_t bit_len = static_cast<std::uint64_t>(data.size()) * 8u;
  ByteBuffer msg = data;
  msg.push_back(0x80u);
  while ((msg.size() % 64u) != 56u) msg.push_back(0u);
  for (int i = 7; i >= 0; --i) {
    msg.push_back(static_cast<std::uint8_t>((bit_len >> (i * 8)) & 0xFFu));
  }
  for (std::size_t off = 0; off < msg.size(); off += 64u) {
    std::array<std::uint32_t, 64> w{};
    for (int i = 0; i < 16; ++i) {
      w[static_cast<std::size_t>(i)] =
          (static_cast<std::uint32_t>(msg[off + static_cast<std::size_t>(i * 4)]) << 24u) |
          (static_cast<std::uint32_t>(msg[off + static_cast<std::size_t>(i * 4 + 1)]) << 16u) |
          (static_cast<std::uint32_t>(msg[off + static_cast<std::size_t>(i * 4 + 2)]) << 8u) |
          static_cast<std::uint32_t>(msg[off + static_cast<std::size_t>(i * 4 + 3)]);
    }
    for (int i = 16; i < 64; ++i) {
      const auto s0 = rotr(w[static_cast<std::size_t>(i - 15)], 7u) ^
                      rotr(w[static_cast<std::size_t>(i - 15)], 18u) ^
                      (w[static_cast<std::size_t>(i - 15)] >> 3u);
      const auto s1 = rotr(w[static_cast<std::size_t>(i - 2)], 17u) ^
                      rotr(w[static_cast<std::size_t>(i - 2)], 19u) ^
                      (w[static_cast<std::size_t>(i - 2)] >> 10u);
      w[static_cast<std::size_t>(i)] =
          w[static_cast<std::size_t>(i - 16)] + s0 + w[static_cast<std::size_t>(i - 7)] + s1;
    }
    auto [a, b, c, d, e, f, g, hh] = h;
    for (int i = 0; i < 64; ++i) {
      const auto s1 = rotr(e, 6u) ^ rotr(e, 11u) ^ rotr(e, 25u);
      const auto ch = (e & f) ^ ((~e) & g);
      const auto temp1 =
          hh + s1 + ch + kK[static_cast<std::size_t>(i)] + w[static_cast<std::size_t>(i)];
      const auto s0 = rotr(a, 2u) ^ rotr(a, 13u) ^ rotr(a, 22u);
      const auto maj = (a & b) ^ (a & c) ^ (b & c);
      const auto temp2 = s0 + maj;
      hh = g;
      g = f;
      f = e;
      e = d + temp1;
      d = c;
      c = b;
      b = a;
      a = temp1 + temp2;
    }
    h[0] += a;
    h[1] += b;
    h[2] += c;
    h[3] += d;
    h[4] += e;
    h[5] += f;
    h[6] += g;
    h[7] += hh;
  }
  std::string out;
  out.reserve(64);
  for (std::uint32_t word : h) {
    char buf[9];
    std::snprintf(buf, sizeof(buf), "%08x", word);
    out += buf;
  }
  return out;
}

std::string base64_encode(const ByteBuffer& data) {
  static constexpr char kTable[] =
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  std::string out;
  out.reserve(((data.size() + 2u) / 3u) * 4u);
  std::size_t i = 0;
  for (; i + 3u <= data.size(); i += 3u) {
    const std::uint32_t n = (static_cast<std::uint32_t>(data[i]) << 16u) |
                            (static_cast<std::uint32_t>(data[i + 1u]) << 8u) |
                            static_cast<std::uint32_t>(data[i + 2u]);
    out.push_back(kTable[(n >> 18u) & 63u]);
    out.push_back(kTable[(n >> 12u) & 63u]);
    out.push_back(kTable[(n >> 6u) & 63u]);
    out.push_back(kTable[n & 63u]);
  }
  const std::size_t rem = data.size() - i;
  if (rem == 1u) {
    const std::uint32_t n = static_cast<std::uint32_t>(data[i]) << 16u;
    out.push_back(kTable[(n >> 18u) & 63u]);
    out.push_back(kTable[(n >> 12u) & 63u]);
    out.push_back('=');
    out.push_back('=');
  } else if (rem == 2u) {
    const std::uint32_t n = (static_cast<std::uint32_t>(data[i]) << 16u) |
                            (static_cast<std::uint32_t>(data[i + 1u]) << 8u);
    out.push_back(kTable[(n >> 18u) & 63u]);
    out.push_back(kTable[(n >> 12u) & 63u]);
    out.push_back(kTable[(n >> 6u) & 63u]);
    out.push_back('=');
  }
  return out;
}

int hex_val(std::uint8_t c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

ByteBuffer extract_hex_byte_tokens(const ByteBuffer& raw) {
  ByteBuffer out;
  for (std::size_t i = 0; i + 3u < raw.size(); ++i) {
    if (raw[i] == '0' && (raw[i + 1u] == 'x' || raw[i + 1u] == 'X')) {
      const int hi = hex_val(raw[i + 2u]);
      const int lo = hex_val(raw[i + 3u]);
      const bool next_is_hex = (i + 4u < raw.size()) && hex_val(raw[i + 4u]) >= 0;
      if (hi >= 0 && lo >= 0 && !next_is_hex) {
        out.push_back(static_cast<std::uint8_t>((hi << 4) | lo));
        i += 3u;
      }
    }
  }
  return out;
}

bool contains_subsequence(const ByteBuffer& haystack, const ByteBuffer& needle) {
  if (needle.empty() || haystack.size() < needle.size()) return false;
  return std::search(haystack.begin(), haystack.end(), needle.begin(), needle.end()) !=
         haystack.end();
}

ByteBuffer read_file_bytes(const std::filesystem::path& p) {
  std::ifstream s(p, std::ios::binary | std::ios::ate);
  const auto sz = s.tellg();
  ByteBuffer buf(static_cast<std::size_t>(sz));
  s.seekg(0);
  s.read(reinterpret_cast<char*>(buf.data()), sz);
  return buf;
}

TEST(BlankDocument, MatchesFixtureByteForByte) {
  const auto doc = detail::build_blank_v17();
  const ByteBuffer expected = test::read_fixture("blank_v17.skp");

  EXPECT_EQ(doc.data.size(), 5796u);
  EXPECT_EQ(sha256_hex(doc.data), detail::kScaffoldSha256);
  EXPECT_EQ(doc.data, expected);

  EXPECT_EQ(doc.model_guid_offset, 52u);
  EXPECT_EQ(doc.pid_counter_offset, 1987u);
  EXPECT_EQ(doc.next_pid, 18u);
  EXPECT_EQ(doc.no_scale_mask_offset, 2008u);
  EXPECT_EQ(doc.tail_pos, 3515u);
  EXPECT_EQ(doc.scaffold_next_slot, 11);
  EXPECT_EQ(detail::kVersionMapV17.size(), 58u);
}

TEST(BlankDocument, ReadsBackInClassicReader) {
  const auto doc = detail::build_blank_v17();
  std::size_t pos = 0;
  auto read_utf16 = [&doc, &pos]() -> std::string {
    EXPECT_EQ(doc.data[pos], 0xFFu);
    EXPECT_EQ(doc.data[pos + 1u], 0xFEu);
    EXPECT_EQ(doc.data[pos + 2u], 0xFFu);
    const std::size_t n = doc.data[pos + 3u];
    pos += 4u;
    std::string out;
    out.reserve(n);
    for (std::size_t i = 0; i < n; ++i) {
      out.push_back(static_cast<char>(doc.data[pos + i * 2u]));
    }
    pos += n * 2u;
    return out;
  };

  EXPECT_EQ(read_utf16(), "SketchUp Model");
  EXPECT_EQ(read_utf16(), "{17.0.1}");
  pos += 16u;  // model GUID
  EXPECT_EQ(read_utf16(), "");
  pos += 4u;  // save timestamp
  const auto decl_tag = static_cast<std::uint16_t>(doc.data[pos] | (doc.data[pos + 1u] << 8u));
  const auto vmap_schema =
      static_cast<std::uint16_t>(doc.data[pos + 2u] | (doc.data[pos + 3u] << 8u));
  const auto vmap_name_len =
      static_cast<std::uint16_t>(doc.data[pos + 4u] | (doc.data[pos + 5u] << 8u));
  EXPECT_EQ(decl_tag, 0xFFFFu);
  EXPECT_EQ(vmap_schema, 0u);
  pos += 6u;
  EXPECT_EQ(std::string(doc.data.begin() + static_cast<std::ptrdiff_t>(pos),
                        doc.data.begin() + static_cast<std::ptrdiff_t>(pos + vmap_name_len)),
            "CVersionMap");
  pos += vmap_name_len;

  std::size_t vmap_count = 0;
  for (;;) {
    const std::string cls = read_utf16();
    if (cls == "End-Of-Version-Map") break;
    const std::uint32_t schema = static_cast<std::uint32_t>(doc.data[pos]) |
                                 (static_cast<std::uint32_t>(doc.data[pos + 1u]) << 8u) |
                                 (static_cast<std::uint32_t>(doc.data[pos + 2u]) << 16u) |
                                 (static_cast<std::uint32_t>(doc.data[pos + 3u]) << 24u);
    EXPECT_EQ(cls, detail::kVersionMapV17[vmap_count].first);
    EXPECT_EQ(schema, detail::kVersionMapV17[vmap_count].second);
    pos += 4u;
    ++vmap_count;
  }
  EXPECT_EQ(vmap_count, 58u);
  EXPECT_EQ(pos, 1971u);

  const SkpModel model = SkpFile::from_buffer(doc.data).parse();
  EXPECT_EQ(model.version, "{17.0.1}");
  ASSERT_EQ(model.layers.size(), 1u);
  EXPECT_EQ(model.layers[0].name, "Layer0");
  EXPECT_FALSE(model.layers[0].hidden);
  EXPECT_EQ(model.layers[0].color, (Color3{255, 84, 84}));
  EXPECT_TRUE(model.materials.empty());
  EXPECT_TRUE(model.definitions.empty());
  EXPECT_TRUE(model.root().faces.empty());
  EXPECT_TRUE(model.root().edges.empty());
  EXPECT_TRUE(model.root().vertices.empty());
  EXPECT_TRUE(model.root().instances.empty());
  EXPECT_TRUE(model.pages.empty());
  EXPECT_EQ(model.unread_root_entities, 0u);
  ASSERT_TRUE(model.face_front_color.has_value());
  EXPECT_EQ(*model.face_front_color, (Color3{225, 225, 200}));
  ASSERT_TRUE(model.face_back_color.has_value());
  EXPECT_EQ(*model.face_back_color, (Color3{128, 128, 200}));
  EXPECT_FALSE(model.color_by_tag);
  EXPECT_TRUE(model.attribute_dictionaries.empty());
}

TEST(BlankDocument, SourceTreeContainsNoSkpFilesOrTemplateBytes) {
  const ByteBuffer template_bytes = detail::build_blank_v17().data;
  std::string template_hex;
  template_hex.reserve(template_bytes.size() * 2u);
  for (std::uint8_t b : template_bytes) {
    char buf[3];
    std::snprintf(buf, sizeof(buf), "%02x", b);
    template_hex += buf;
  }
  const std::string template_b64 = base64_encode(template_bytes);

  const std::filesystem::path pkg_root(OPENSKP_PKG_ROOT);
  std::vector<std::filesystem::path> files;
  for (const char* sub : {"include", "src"}) {
    for (const auto& entry : std::filesystem::recursive_directory_iterator(pkg_root / sub)) {
      if (entry.is_regular_file()) files.push_back(entry.path());
    }
  }
  ASSERT_FALSE(files.empty());

  for (const auto& file : files) {
    std::string ext = file.extension().string();
    std::transform(ext.begin(), ext.end(), ext.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    EXPECT_NE(ext, ".skp") << "Unexpected .skp file under C++ source tree: " << file;

    const ByteBuffer raw = read_file_bytes(file);
    EXPECT_FALSE(contains_subsequence(raw, template_bytes))
        << "Raw template bytes found in " << file;

    std::string compact;
    compact.reserve(raw.size());
    for (std::uint8_t b : raw) {
      if (std::isalnum(b) || b == '+' || b == '/' || b == '=') {
        compact.push_back(static_cast<char>(b));
      }
    }
    EXPECT_EQ(compact.find(template_b64), std::string::npos) << file;
    std::string compact_lower = compact;
    std::transform(compact_lower.begin(), compact_lower.end(), compact_lower.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    EXPECT_EQ(compact_lower.find(template_hex), std::string::npos) << file;

    EXPECT_FALSE(contains_subsequence(extract_hex_byte_tokens(raw), template_bytes))
        << "Hex byte array of template found in " << file;
  }
}

}  // namespace
}  // namespace openskp
