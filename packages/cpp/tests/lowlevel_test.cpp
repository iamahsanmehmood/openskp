#include <gtest/gtest.h>

#include "internal.hpp"
#include "test_helpers.hpp"
using namespace openskp;

TEST(Tlv, CheckedReadsAndVarints) {
  ByteBuffer b{0x34, 0x12, 0x78, 0x56, 0x34, 0x12};
  EXPECT_EQ(read_u16(b, 0), 0x1234);
  EXPECT_EQ(read_u32(b, 2), 0x12345678u);
  EXPECT_EQ(parse_varint(b, 0, 2), 0x1234u);
  EXPECT_THROW(read_u32(b, 4), std::out_of_range);
  EXPECT_THROW(parse_varint(b, 0, 9), std::out_of_range);
}

TEST(Tlv, ReadsFloat64LittleEndian) {
  const ByteBuffer data{0, 0, 0, 0, 0, 0, 0xf0, 0x3f};
  EXPECT_DOUBLE_EQ(read_f64(data, 0), 1.0);
  EXPECT_THROW(read_f64(data, 1), std::out_of_range);
}

TEST(Tlv, RecursiveContainers) {
  ByteBuffer b{0xF4, 0x01, 0x07, 0, 0, 0, 0xAA, 0x00, 0x01, 0, 0, 0, 0x42};
  auto n = parse_tlv_recursive(b, 0, b.size());
  ASSERT_EQ(n.size(), 1);
  ASSERT_EQ(n[0].children.size(), 1);
  EXPECT_EQ(n[0].children[0].payload, ByteBuffer({0x42}));
}

TEST(Vff, HeaderAndVersion) {
  auto d = ByteBuffer{0xff, 0xfe, 0xff, 0x0e, 0xff, 0xfe, 0xff, 0};
  std::string v = "{25.0.575}";
  for (char c : v) {
    d.push_back(c);
    d.push_back(0);
  }
  EXPECT_TRUE(valid_header(d));
  EXPECT_EQ(extract_version(d), v);
  EXPECT_FALSE(valid_header({0xff, 0xff, 0xff, 0x0e}));
}

TEST(Legacy, DetectsClassicContainer) {
  EXPECT_TRUE(is_legacy(test::read_fixture("capilla_quiroz_v17.skp")));
  EXPECT_FALSE(is_legacy(test::read_fixture("Untitled.skp")));
}

TEST(Transforms, Composition) {
  std::vector<double> a{1, 0, 0, 0, 1, 0, 0, 0, 1, 2, 3, 4, 1};
  std::vector<double> b{1, 0, 0, 0, 1, 0, 0, 0, 1, 5, 6, 7, 1};
  auto m = multiply_matrices(a, b);
  auto p = transform_point(m, {1, 1, 1});
  EXPECT_EQ(p, (std::array<double, 3>{8, 10, 12}));
}

TEST(Transforms, TranslatesPoint) {
  const std::vector<double> matrix{1, 0, 0, 0, 1, 0, 0, 0, 1, 5, 10, -2, 1};
  EXPECT_EQ(transform_point(matrix, {1, 2, 3}), (std::array<double, 3>{6, 12, 1}));
}
