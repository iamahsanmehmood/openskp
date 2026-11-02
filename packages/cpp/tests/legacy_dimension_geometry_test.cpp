#include <algorithm>
#include <gtest/gtest.h>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

namespace openskp {
namespace {

const Point3 kA{1, 2, 3}, kB{4, 6, 3}, kC{11, 12, 13}, kD{14, 16, 13};

ByteBuffer u32(uint32_t n) {
  return {uint8_t(n), uint8_t(n >> 8), uint8_t(n >> 16), uint8_t(n >> 24)};
}

ByteBuffer ref(uint32_t slot, bool large = false) {
  if (large) return test::concat({{0xff, 0x7f}, u32(slot)});
  return {uint8_t(slot), uint8_t(slot >> 8)};
}

ByteBuffer point_bytes(Point3 p) { return test::f64s({p[0], p[1], p[2]}); }

ByteBuffer connection(Point3 p) { return test::concat({u32(1), u32(4), point_bytes(p)}); }

size_t locate(const ByteBuffer& bytes, const ByteBuffer& needle) {
  const auto it = std::search(bytes.begin(), bytes.end(), needle.begin(), needle.end());
  if (it == bytes.end()) throw std::runtime_error("dimension test record not found");
  return size_t(it - bytes.begin());
}

ByteBuffer fixture() {
  auto builder = create();
  builder->add_dimension(kA, kB, -2);
  builder->add_dimension(kC, kD, 3);
  // A successful parse must retain entities after the annotations, too.
  builder->add_construction_point({101, 102, 103});
  return builder->to_bytes();
}

uint32_t font_slot(const ByteBuffer& bytes) {
  // The second dimension shares the first one's font: a two-byte backref,
  // followed by five reserved bytes, then its first connection block.
  return read_u16(bytes, locate(bytes, connection(kC)) - 7);
}

ByteBuffer vertex(Point3 p, const ByteBuffer& tag) {
  // A CVertex object has an attribute-container ref, a v17+ PID mask, and xyz.
  return test::concat({tag, {0, 0, 0}, point_bytes(p)});
}

const ByteBuffer kVertexClass{0xff, 0xff, 0, 0, 7, 0, 'C', 'V', 'e', 'r', 't', 'e', 'x'};

void attach(ByteBuffer& bytes, Point3 original, const ByteBuffer& object) {
  const auto at = locate(bytes, connection(original));
  bytes[at] = 2;  // vertex connection, instead of a free point
  std::fill(bytes.begin() + at + 8, bytes.begin() + at + 32, 0);
  bytes.erase(bytes.begin() + at + 32, bytes.begin() + at + 34);  // null object ref
  bytes.insert(bytes.begin() + at + 32, object.begin(), object.end());
}

void expect_point(const std::optional<Vec3>& point, Point3 expected) {
  ASSERT_TRUE(point.has_value());
  for (int i = 0; i < 3; ++i) EXPECT_NEAR((*point)[i], expected[i], 1e-9);
}

void expect_tail(const SkpModel& model) {
  ASSERT_EQ(model.root().construction_points.size(), 1u);
  EXPECT_EQ(model.root().construction_points[0].position, (Vec3{101, 102, 103}));
}

TEST(LegacyDimensionGeometry, FreeEndpointsAndFrameSurviveParsing) {
  const auto model = SkpFile::from_buffer(fixture()).parse();
  ASSERT_EQ(model.root().dimensions.size(), 2u);
  const auto& first = model.root().dimensions[0];
  expect_point(first.a, kA);
  expect_point(first.b, kB);
  EXPECT_DOUBLE_EQ(first.offset, -2);
  expect_point(first.plane_x, {1, 0, 0});
  expect_point(first.normal, {0, 0, 1});
  EXPECT_FALSE(first.offset_vector);  // free/aligned mode: do not invent an axis
  expect_point(model.root().dimensions[1].a, kC);
  expect_point(model.root().dimensions[1].b, kD);
  expect_tail(model);
}

TEST(LegacyDimensionGeometry, InlineVerticesAndLaterBackrefsKeepArchiveAligned) {
  for (bool large : {false, true}) {
    SCOPED_TRACE(large);
    auto bytes = fixture();
    const auto font = font_slot(bytes);
    // Inline class declaration, new object of that class (short/big form),
    // then reuse those vertices in the next dimension (short/big backrefs).
    attach(bytes, kD, ref(font + 3, large));
    attach(bytes, kC, ref(font + 2, large));
    attach(bytes, kB, vertex(kB, ref((large ? 0x80000000u : 0x8000u) | (font + 1), large)));
    attach(bytes, kA, vertex(kA, kVertexClass));
    const auto model = SkpFile::from_buffer(bytes).parse();
    ASSERT_EQ(model.root().dimensions.size(), 2u);
    for (const auto& dim : model.root().dimensions) {
      expect_point(dim.a, kA);
      expect_point(dim.b, kB);
    }
    expect_tail(model);
  }
}

TEST(LegacyDimensionGeometry, ForwardVertexReferencesResolveAfterArchiveWalk) {
  auto bytes = fixture();
  const auto font = font_slot(bytes);
  // First dimension's B defines the vertex class and object. The second
  // dimension then allocates itself, followed by the vertex referenced by A.
  attach(bytes, kC, vertex(kC, ref(0x8000u | (font + 1))));
  attach(bytes, kB, vertex(kB, kVertexClass));
  attach(bytes, kA, ref(font + 4, true));
  const auto model = SkpFile::from_buffer(bytes).parse();
  ASSERT_EQ(model.root().dimensions.size(), 2u);
  expect_point(model.root().dimensions[0].a, kC);
  expect_point(model.root().dimensions[0].b, kB);
  expect_point(model.root().dimensions[1].a, kC);
  expect_tail(model);
}

TEST(LegacyDimensionGeometry, PlaneAxesPreserveSignedOffsetWithReversedEndpoints) {
  for (uint32_t axis : {1u, 2u, 3u}) {
    for (bool reversed : {false, true}) {
      SCOPED_TRACE(axis);
      SCOPED_TRACE(reversed);
      auto builder = create();
      builder->add_dimension(reversed ? kB : kA, reversed ? kA : kB, -2);
      auto bytes = builder->to_bytes();
      const auto at = locate(bytes, connection(reversed ? kA : kB)) + 32 + 2 + 10;
      // After the second connection's paths: normal, X axis, constrained axis.
      const auto frame = test::concat({test::f64s({0, -1, 0, 1, 0, 0}), u32(axis)});
      std::copy(frame.begin(), frame.end(), bytes.begin() + at);
      const auto model = SkpFile::from_buffer(bytes).parse();
      ASSERT_EQ(model.root().dimensions.size(), 1u);
      const auto& dim = model.root().dimensions[0];
      expect_point(dim.normal, {0, -1, 0});
      expect_point(dim.plane_x, {1, 0, 0});
      if (axis == 1)
        expect_point(dim.offset_vector, {0, 0, -2});
      else if (axis == 2)
        expect_point(dim.offset_vector, {2, 0, 0});
      else
        EXPECT_FALSE(dim.offset_vector);
    }
  }
}

TEST(LegacyDimensionGeometry, NestedPathsAreConsumedWithoutPublishingLocalCoordinates) {
  auto bytes = fixture();
  // Replace the first connection's empty first path with one unresolved
  // instance ref. Endpoint B and all following entities must still parse.
  const auto at = locate(bytes, connection(kA)) + 32 + 2 + 2;
  bytes[at] = 1;
  const auto path = ref(1234, true);
  bytes.insert(bytes.begin() + at + 4, path.begin(), path.end());
  const auto model = SkpFile::from_buffer(bytes).parse();
  ASSERT_EQ(model.root().dimensions.size(), 2u);
  EXPECT_FALSE(model.root().dimensions[0].a);
  expect_point(model.root().dimensions[0].b, kB);
  expect_point(model.root().dimensions[1].a, kC);
  expect_tail(model);
}

}  // namespace
}  // namespace openskp
