#include <cstring>
#include <gtest/gtest.h>

#include "internal.hpp"
#include "test_helpers.hpp"

// VFF scenes ("pages") and linear dimensions - ported from Python's
// test_pages_dimensions.py (PR #190).
//
// Dimensions are exercised against the repository's own Untitled.skp
// fixture (drawn in SketchUp 2025, it carries 13 linear dimensions); scenes
// have no fixture yet, so their parser is exercised on a synthetic "0702"
// record byte-for-byte shaped like the real ones (the layout was decoded
// from production survey files and calibrated against the scene thumbnails
// SketchUp embeds in the .skp itself).

namespace openskp {
namespace {

// ── helpers: build TLV runs in the flat (u16-LE tag, u32 len) form ────────
// Deliberately NOT test_helpers.hpp's tlv() - that one takes a hex-digit
// STRING and writes it in the main tree's byte-order convention (tag[0..1]
// as the first byte), whereas this feature's sub-records use Python's own
// _tlv_items/_tlv_find INTEGER convention (a true little-endian uint16).
// Passing e.g. "5208" through the string helper would write the byte-order
// bytes for tag 0x0852, not 0x5208 - so this local helper takes the
// numeric tag directly and packs it little-endian, matching Python's own
// test helper (`def tlv(tag: int, payload: bytes)`) exactly.

ByteBuffer tlv(std::uint16_t tag, ByteBuffer payload = {}) {
  ByteBuffer result{static_cast<std::uint8_t>(tag & 0xff),
                    static_cast<std::uint8_t>((tag >> 8) & 0xff)};
  const auto size = static_cast<std::uint32_t>(payload.size());
  for (int i = 0; i < 4; ++i) result.push_back(static_cast<std::uint8_t>(size >> (i * 8)));
  result.insert(result.end(), payload.begin(), payload.end());
  return result;
}

ByteBuffer vec3(double x, double y, double z) { return test::f64s({x, y, z}); }

std::string hex_upper(const ByteBuffer& p) {
  static char h[] = "0123456789ABCDEF";
  std::string s;
  for (auto b : p) {
    s += h[b >> 4];
    s += h[b & 15];
  }
  return s;
}

}  // namespace

// ── linear dimensions ────────────────────────────────────────────────────

TEST(PagesDimensions, UntitledFixtureHas13Dimensions) {
  auto model = SkpFile::open(test::fixture("Untitled.skp")).parse();
  EXPECT_EQ(model.dimensions.size(), 13u);
  for (auto& d : model.dimensions) {
    ASSERT_TRUE(d.a.has_value());
    ASSERT_TRUE(d.b.has_value());
    double dx = (*d.a)[0] - (*d.b)[0];
    double dy = (*d.a)[1] - (*d.b)[1];
    double dz = (*d.a)[2] - (*d.b)[2];
    EXPECT_GT(dx * dx + dy * dy + dz * dz, 0.0);  // a real measured segment
    EXPECT_TRUE(d.normal.has_value());
    EXPECT_TRUE(d.plane_x.has_value());
  }
}

TEST(PagesDimensions, DimensionFreePointsSynthetic) {
  // A 5BCC record with two type-1 (free, world-space) connection points.
  auto point_block = [](std::uint16_t wrap_tag, double x, double y, double z) {
    auto inner = test::concat({tlv(0x5209, ByteBuffer{1, 0, 0, 0}), tlv(0x520a, vec3(x, y, z))});
    return tlv(wrap_tag, tlv(0x5208, inner));
  };

  auto body = test::concat({
      point_block(0x5bcd, 0.0, 0.0, 0.0), point_block(0x5bce, 100.0, 0.0, 0.0),
      tlv(0x5bcf, vec3(1.0, 0.0, 0.0)),  // plane x-axis
      tlv(0x5bd0, vec3(0.0, 0.0, 1.0)),  // plane normal
      tlv(0x5bd2, test::f64s({15.5})),   // offset
  });
  auto blob = test::concat({ByteBuffer(8, 0), tlv(0x5bcc, body), ByteBuffer(8, 0)});

  auto dims = parse_dimensions(blob, {}, {});
  ASSERT_EQ(dims.size(), 1u);
  auto& d = dims[0];
  EXPECT_EQ(d.a, (Vec3{0.0, 0.0, 0.0}));
  EXPECT_EQ(d.b, (Vec3{100.0, 0.0, 0.0}));
  EXPECT_EQ(d.offset, 15.5);
  ASSERT_TRUE(d.plane_x.has_value());
  EXPECT_EQ(*d.plane_x, (Vec3{1.0, 0.0, 0.0}));
  ASSERT_TRUE(d.normal.has_value());
  EXPECT_EQ(*d.normal, (Vec3{0.0, 0.0, 1.0}));
}

TEST(PagesDimensions, DimensionConnectedPointResolvesThroughInstance) {
  // A type-2 connection (vertex id + instance id): the vertex position is
  // definition-local and must be lifted to world by the instance's
  // transform. An unresolvable reference drops the dimension (fail-safe).
  const ByteBuffer vid{0xaa, 0xbb, 0x01};
  const ByteBuffer iid{0xcc, 0xdd, 0x02};

  auto connected = [&](std::uint16_t wrap_tag) {
    ByteBuffer id_len_prefixed{static_cast<std::uint8_t>(iid.size())};
    id_len_prefixed.insert(id_len_prefixed.end(), iid.begin(), iid.end());
    auto ref_tlv = tlv(0x53fc, test::concat({tlv(0x53fd, vid), tlv(0x53fe, id_len_prefixed)}));
    auto inner = test::concat({tlv(0x5209, ByteBuffer{2, 0, 0, 0}), tlv(0x520b, ref_tlv)});
    return tlv(wrap_tag, tlv(0x5208, inner));
  };

  auto free = [&](std::uint16_t wrap_tag) {
    auto inner =
        test::concat({tlv(0x5209, ByteBuffer{1, 0, 0, 0}), tlv(0x520a, vec3(0.0, 0.0, 0.0))});
    return tlv(wrap_tag, tlv(0x5208, inner));
  };

  auto body = test::concat({connected(0x5bcd), free(0x5bce), tlv(0x5bd2, test::f64s({0.0}))});
  auto blob = tlv(0x5bcc, body);

  // Identity-ish transform that translates by (10, 20, 30).
  std::vector<double> world{1, 0, 0, 0, 1, 0, 0, 0, 1, 10.0, 20.0, 30.0, 1.0};
  std::map<std::string, Vec3> id2pos{{hex_upper(vid), Vec3{1.0, 2.0, 3.0}}};
  std::map<std::string, std::vector<double>> inst_world{{hex_upper(iid), world}};

  auto dims = parse_dimensions(blob, id2pos, inst_world);
  ASSERT_EQ(dims.size(), 1u);
  EXPECT_EQ(dims[0].a, (Vec3{11.0, 22.0, 33.0}));  // local + translation

  // Same record, but the vertex id is unknown: the dimension is dropped.
  auto empty_dims = parse_dimensions(blob, {}, {});
  EXPECT_TRUE(empty_dims.empty());
}

// ── scenes (pages) ──────────────────────────────────────────────────────

ByteBuffer page_record(const std::string& name, bool parallel, std::vector<int> hidden_ids = {}) {
  auto cam = test::concat({
      tlv(0x34bd, vec3(100.0, -200.0, 50.0)),  // eye
      tlv(0x34be, vec3(0.0, 0.0, 0.0)),        // target
      tlv(0x34bf, vec3(0.0, 0.0, 1.0)),        // up
      tlv(0x34c4, test::f64s({35.0})),         // fov
      tlv(0x34c2, ByteBuffer{static_cast<std::uint8_t>(parallel ? 0 : 1)}),
      tlv(0x34c3, test::f64s({240.0})),  // ortho height
  });
  ByteBuffer hidden;
  for (auto id : hidden_ids) {
    hidden.push_back(1);
    hidden.push_back(static_cast<std::uint8_t>(id));
  }
  auto body = test::concat({
      tlv(0x6f54, tlv(0x6f55, test::bytes(name))),
      tlv(0x714a, tlv(0x34bc, cam)),
      tlv(0x7150, hidden),
  });
  return tlv(0x7148, body);
}

TEST(PagesDimensions, ParsePagesSynthetic) {
  auto payload = tlv(0x6d60, tlv(0x6d61, test::concat({page_record("Planta", true, {2}),
                                                       page_record("Vista 3D", false)})));
  TlvNode node;
  node.tag = "0702";
  node.payload = payload;
  auto pages = parse_pages(&node);

  ASSERT_EQ(pages.size(), 2u);
  EXPECT_EQ(pages[0].name, "Planta");
  EXPECT_EQ(pages[1].name, "Vista 3D");
  auto& planta = pages[0];
  EXPECT_TRUE(planta.parallel);
  EXPECT_EQ(planta.ortho_height, 240.0);
  ASSERT_TRUE(planta.eye.has_value());
  EXPECT_EQ(*planta.eye, (Vec3{100.0, -200.0, 50.0}));
  ASSERT_TRUE(planta.up.has_value());
  EXPECT_EQ(*planta.up, (Vec3{0.0, 0.0, 1.0}));
  ASSERT_EQ(planta.hidden_layer_ids.size(), 1u);
  EXPECT_EQ(planta.hidden_layer_ids[0], 2);
  EXPECT_FALSE(pages[1].parallel);
  EXPECT_EQ(pages[1].fov, 35.0);
}

TEST(PagesDimensions, PagesAbsentIsEmpty) { EXPECT_TRUE(parse_pages(nullptr).empty()); }

TEST(PagesDimensions, FileWithNoPagesParsesWithEmptyPagesList) {
  auto model = SkpFile::open(test::fixture("SU_File.skp")).parse();
  EXPECT_TRUE(model.pages.empty());
}
}  // namespace openskp
