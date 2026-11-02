#include <cstring>
#include <gtest/gtest.h>

#include <openskp/parser.hpp>

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

TEST(PagesDimensions, ConstrainedOffsetsPreservePlaneSideAndEndpointOrder) {
  auto point = [](std::uint16_t tag, Vec3 p) {
    return tlv(tag, tlv(0x5208, test::concat({tlv(0x5209, {1, 0, 0, 0}),
                                              tlv(0x520a, vec3(p[0], p[1], p[2]))})));
  };
  for (std::uint8_t axis : {1, 2}) {
    for (double distance : {-4.0, 4.0}) {
      for (bool reversed : {false, true}) {
        SCOPED_TRACE(axis);
        SCOPED_TRACE(distance);
        SCOPED_TRACE(reversed);
        const Vec3 a{1, 2, 3}, b{1, 2, 13};
        auto body =
            test::concat({point(0x5bcd, reversed ? b : a), point(0x5bce, reversed ? a : b),
                          tlv(0x5bcf, vec3(0, -1, 0)),  // actual plane normal
                          tlv(0x5bd0, vec3(1, 0, 0)),   // actual plane X axis
                          tlv(0x5bd1, {axis, 0, 0, 0}), tlv(0x5bd2, test::f64s({distance}))});
        RawParsed parsed;
        parsed.dimensions = parse_dimensions(tlv(0x5bcc, body), {}, {});
        const auto model = build_model(std::move(parsed));
        ASSERT_EQ(model.dimensions.size(), 1u);
        const auto& dim = model.dimensions[0];
        ASSERT_TRUE(dim.offset_vector);
        const Vec3 expected = axis == 1 ? Vec3{0, 0, distance} : Vec3{-distance, 0, 0};
        EXPECT_EQ(*dim.offset_vector, expected);
        EXPECT_EQ(*dim.a, reversed ? b : a);
        EXPECT_EQ(*dim.b, reversed ? a : b);
      }
    }
  }
}

TEST(PagesDimensions, UnsupportedOrMalformedConstraintKeepsScalarFallback) {
  auto point = [](std::uint16_t tag, Vec3 p) {
    return tlv(tag, tlv(0x5208, test::concat({tlv(0x5209, {1, 0, 0, 0}),
                                              tlv(0x520a, vec3(p[0], p[1], p[2]))})));
  };
  for (const ByteBuffer axis :
       {ByteBuffer{}, ByteBuffer{2}, ByteBuffer{0, 0, 0, 0}, ByteBuffer{3, 0, 0, 0}}) {
    auto body = test::concat({point(0x5bcd, {1, 2, 3}), point(0x5bce, {1, 2, 13}),
                              tlv(0x5bcf, vec3(0, -1, 0)), tlv(0x5bd0, vec3(1, 0, 0)),
                              tlv(0x5bd2, test::f64s({-4}))});
    if (!axis.empty()) {
      const auto tag = tlv(0x5bd1, axis);
      body.insert(body.end(), tag.begin(), tag.end());
    }
    const auto dims = parse_dimensions(tlv(0x5bcc, body), {}, {});
    ASSERT_EQ(dims.size(), 1u);
    EXPECT_FALSE(dims[0].offset_vector);
    EXPECT_EQ(dims[0].offset, -4);
  }
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

TEST(PagesDimensions, ComposeInstanceWorldNestsParentTimesLocal) {
  RawDefinition panel;
  RawDefinition cabinet;
  RawInstance panelInst;
  panelInst.persistent_id = "AA";
  panelInst.ref_idx = 2;
  panelInst.matrix = {1, 0, 0, 0, 1, 0, 0, 0, 1, 1.0, 2.0, 3.0, 1.0};
  cabinet.builder.instances.push_back(panelInst);

  RawDefinition root;
  RawInstance cabInst;
  cabInst.persistent_id = "BB";
  cabInst.ref_idx = 1;
  cabInst.matrix = {1, 0, 0, 0, 1, 0, 0, 0, 1, 10.0, 20.0, 30.0, 1.0};
  root.builder.instances.push_back(cabInst);

  std::map<EntityId, RawDefinition> defs;
  defs[1] = std::move(cabinet);
  defs[2] = std::move(panel);

  std::map<std::string, std::vector<double>> world;
  compose_instance_world(root, defs, world);
  ASSERT_EQ(world.count("BB"), 1u);
  ASSERT_EQ(world.count("AA"), 1u);
  EXPECT_EQ(transform_point(world["BB"], Vec3{0, 0, 0}), (Vec3{10.0, 20.0, 30.0}));
  EXPECT_EQ(transform_point(world["AA"], Vec3{0, 0, 0}), (Vec3{11.0, 22.0, 33.0}));
}

TEST(PagesDimensions, DimensionConnectedPointUsesComposedNestedWorld) {
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

  RawDefinition leaf;
  RawDefinition parentDef;
  RawInstance leafInst;
  leafInst.persistent_id = hex_upper(iid);
  leafInst.ref_idx = 2;
  leafInst.matrix = {1, 0, 0, 0, 1, 0, 0, 0, 1, 1.0, 2.0, 3.0, 1.0};
  parentDef.builder.instances.push_back(leafInst);
  RawDefinition root;
  RawInstance parentInst;
  parentInst.persistent_id = "PARENT";
  parentInst.ref_idx = 1;
  parentInst.matrix = {1, 0, 0, 0, 1, 0, 0, 0, 1, 10.0, 20.0, 30.0, 1.0};
  root.builder.instances.push_back(parentInst);
  std::map<EntityId, RawDefinition> defs;
  defs[1] = std::move(parentDef);
  defs[2] = std::move(leaf);

  std::map<std::string, std::vector<double>> world;
  compose_instance_world(root, defs, world);
  std::map<std::string, Vec3> id2pos{{hex_upper(vid), Vec3{0.0, 0.0, 0.0}}};
  auto dims = parse_dimensions(blob, id2pos, world);
  ASSERT_EQ(dims.size(), 1u);
  EXPECT_EQ(dims[0].a, (Vec3{11.0, 22.0, 33.0}));
}

TEST(PagesDimensions, DimensionConnectedPointUsesInnermostInstanceInPath) {
  // SketchUp stores 53FE as an instance path (outer → inner). A root-level
  // dimension on nested groups names the outer component first; the vertex
  // lives in the innermost definition and must use that placement's world xf.
  const ByteBuffer vid{0xaa, 0xbb, 0x01};
  const ByteBuffer outer_id{0x11, 0x01};
  const ByteBuffer inner_id{0x22, 0x02};

  ByteBuffer path;
  path.push_back(static_cast<std::uint8_t>(outer_id.size()));
  path.insert(path.end(), outer_id.begin(), outer_id.end());
  path.push_back(static_cast<std::uint8_t>(inner_id.size()));
  path.insert(path.end(), inner_id.begin(), inner_id.end());

  auto connected = [&](std::uint16_t wrap_tag) {
    auto ref_tlv = tlv(0x53fc, test::concat({tlv(0x53fd, vid), tlv(0x53fe, path)}));
    auto inner = test::concat({tlv(0x5209, ByteBuffer{2, 0, 0, 0}), tlv(0x520b, ref_tlv)});
    return tlv(wrap_tag, tlv(0x5208, inner));
  };
  auto free = [&](std::uint16_t wrap_tag) {
    auto inner =
        test::concat({tlv(0x5209, ByteBuffer{1, 0, 0, 0}), tlv(0x520a, vec3(0.0, 0.0, 0.0))});
    return tlv(wrap_tag, tlv(0x5208, inner));
  };
  auto blob =
      tlv(0x5bcc, test::concat({connected(0x5bcd), free(0x5bce), tlv(0x5bd2, test::f64s({0.0}))}));

  RawDefinition leaf;
  RawDefinition parentDef;
  RawInstance leafInst;
  leafInst.persistent_id = hex_upper(inner_id);
  leafInst.ref_idx = 2;
  leafInst.matrix = {1, 0, 0, 0, 1, 0, 0, 0, 1, 100.0, 0.0, 0.0, 1.0};
  parentDef.builder.instances.push_back(leafInst);
  RawDefinition root;
  RawInstance parentInst;
  parentInst.persistent_id = hex_upper(outer_id);
  parentInst.ref_idx = 1;
  parentInst.matrix = {1, 0, 0, 0, 1, 0, 0, 0, 1, 10.0, 0.0, 0.0, 1.0};
  root.builder.instances.push_back(parentInst);
  std::map<EntityId, RawDefinition> defs;
  defs[1] = std::move(parentDef);
  defs[2] = std::move(leaf);

  std::map<std::string, std::vector<double>> world;
  compose_instance_world(root, defs, world);
  std::map<std::string, Vec3> id2pos{{hex_upper(vid), Vec3{0.0, 0.0, 0.0}}};
  auto dims = parse_dimensions(blob, id2pos, world);
  ASSERT_EQ(dims.size(), 1u);
  EXPECT_EQ(dims[0].a, (Vec3{110.0, 0.0, 0.0}));
}

// ── scenes (pages) ──────────────────────────────────────────────────────

ByteBuffer page_record(const std::string& name, bool parallel, std::vector<int> hidden_ids = {},
                       ByteBuffer id = {}) {
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
      tlv(0x6f54, test::concat({id.empty() ? ByteBuffer{} : tlv(0x05dc, tlv(0x05de, id)),
                                tlv(0x6f55, test::bytes(name))})),
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
  EXPECT_FALSE(pages[0].selected);  // no 6D62: nothing selected
  EXPECT_FALSE(pages[1].selected);
}

// 6D62 (beside 6D61) names the scene selected when the model was saved by the
// entity id each page carries in 6F54 > DC05 > DE05.
TEST(PagesDimensions, ParsePagesSelected) {
  auto payload = tlv(
      0x6d60, test::concat({
                  tlv(0x6d61, test::concat({page_record("Plan", true, {}, ByteBuffer{0x38, 0x02}),
                                            page_record("3D", false, {}, ByteBuffer{0x39, 0x02})})),
                  tlv(0x6d62, ByteBuffer{0x39, 0x02}),
              }));
  TlvNode node;
  node.tag = "0702";
  node.payload = payload;
  auto pages = parse_pages(&node);
  ASSERT_EQ(pages.size(), 2u);
  EXPECT_FALSE(pages[0].selected);
  EXPECT_TRUE(pages[1].selected);
}

TEST(PagesDimensions, PagesAbsentIsEmpty) { EXPECT_TRUE(parse_pages(nullptr).empty()); }

TEST(PagesDimensions, FileWithNoPagesParsesWithEmptyPagesList) {
  auto model = SkpFile::open(test::fixture("SU_File.skp")).parse();
  EXPECT_TRUE(model.pages.empty());
}

// ── legacy scenes (pages) ────────────────────────────────────────────────
//
// CViewPage's full record also embeds a camera, an optional thumbnail, a
// font, and - for any flag combination beyond hidden-layers - an entire
// Style sub-object of undocumented size, so the legacy scanner only
// supports the narrow flag set it fully understands (see
// scan_pages_for_layers's own comment in legacy.cpp); everything else is
// silently skipped. Ground truth (the flags bitmask, the hidden-layers
// list shape) was captured from real v17-native SketchUp saves - ported
// byte-for-byte from Python's legacy._scan_pages / its own
// test_scan_pages_legacy_synthetic.

namespace {

ByteBuffer legacy_page_record(const std::string& name, std::uint32_t flags,
                              const std::vector<std::uint16_t>& hidden_ids = {}) {
  ByteBuffer out;
  auto push_marker = [&]() {
    out.push_back(255);
    out.push_back(254);
    out.push_back(255);
  };
  push_marker();
  out.push_back(static_cast<std::uint8_t>(name.size()));
  for (char c : name) {
    out.push_back(static_cast<std::uint8_t>(c));
    out.push_back(0);
  }
  push_marker();
  out.push_back(0);  // second name: always empty
  for (int i = 0; i < 4; ++i) out.push_back(static_cast<std::uint8_t>(flags >> (i * 8)));
  out.push_back(0);
  out.push_back(0);  // camera: null ref
  out.push_back(0);
  out.push_back(0);  // thumbnail dib: null ref
  constexpr std::uint32_t kHiddenLayersBit = 0x20;
  if (flags & kHiddenLayersBit) {
    for (auto hid : hidden_ids) {
      out.push_back(static_cast<std::uint8_t>(hid & 0xff));
      out.push_back(static_cast<std::uint8_t>((hid >> 8) & 0xff));
    }
    out.push_back(1);
    out.push_back(0);
    out.push_back(0);
    out.push_back(0);  // u32 marker (also stops the u16 list)
  }
  return out;
}

}  // namespace

TEST(PagesDimensions, ScanPagesLegacySynthetic) {
  auto data = test::concat({
      legacy_page_record("Baseline", 0xc00),
      legacy_page_record("WithHidden", 0xc20, {5, 6}),
      legacy_page_record("TooFull", 0xfff, {5}),
  });

  std::unordered_map<std::uint64_t, LegacySlotEntry> slots;
  std::unordered_map<std::string, std::uint64_t> class_slot;
  std::uint64_t next = 0;
  slots[5] = LegacySlotEntry{false, "CLayer", 0, {}};
  slots[6] = LegacySlotEntry{false, "CLayer", 0, {}};

  auto pages = scan_pages_for_layers(data, 0, LegacySlotTable{slots, class_slot, next});

  ASSERT_EQ(pages.size(), 2u);
  EXPECT_EQ(pages[0].name, "Baseline");
  EXPECT_EQ(pages[1].name, "WithHidden");
  EXPECT_TRUE(pages[0].hidden_layer_ids.empty());
  ASSERT_EQ(pages[1].hidden_layer_ids.size(), 2u);
  EXPECT_EQ(pages[1].hidden_layer_ids[0], 5);
  EXPECT_EQ(pages[1].hidden_layer_ids[1], 6);
}

TEST(PagesDimensions, ScanPagesLegacyIgnoresUnrelatedNoise) {
  // Random bytes containing the marker sequence but not shaped like a
  // real page record must never crash or fabricate a page.
  ByteBuffer noise{255, 254, 255, 5, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10};
  std::unordered_map<std::uint64_t, LegacySlotEntry> slots;
  std::unordered_map<std::string, std::uint64_t> class_slot;
  std::uint64_t next = 0;

  auto pages = scan_pages_for_layers(noise, 0, LegacySlotTable{slots, class_slot, next});

  EXPECT_TRUE(pages.empty());
}

TEST(PagesDimensions, SdkAnnotationTagsRetainOwnerAndVisibility) {
  for (const char* name : {"annotation_layers_legacy.skp", "annotation_layers_vff.skp"}) {
    SCOPED_TRACE(name);
    const auto model = SkpFile::open(test::fixture(name)).parse();
    const auto verify = [](const Definition& owner, bool nested) {
      const auto tagged = [](const auto& entities, const char* tag, std::size_t count, int hidden) {
        ASSERT_EQ(entities.size(), count);
        int actual_hidden = 0;
        for (const auto& entity : entities) {
          EXPECT_EQ(entity.layer, tag);
          actual_hidden += entity.hidden;
        }
        EXPECT_EQ(actual_hidden, hidden);
      };
      tagged(owner.dimensions, "尺寸标注", 2, 1);
      tagged(owner.construction_points, "参考元素", 2, 1);
      tagged(owner.construction_lines, "参考元素", 2, 1);
      tagged(owner.texts, "引注文字", nested ? 1 : 3, nested ? 0 : 1);
      for (const auto& text : owner.texts) {
        if (nested) {
          EXPECT_EQ(text.leader_type, 0);
          ASSERT_TRUE(text.screen_position);
          EXPECT_EQ(*text.screen_position, (std::array<double, 2>{0.23, 0.67}));
        } else {
          ASSERT_TRUE(text.point && text.label_point);
          for (int axis = 0; axis < 3; ++axis)
            EXPECT_DOUBLE_EQ((*text.label_point)[axis] - (*text.point)[axis], axis == 2 ? 10 : 0);
        }
      }
    };
    verify(model.root(), false);
    std::size_t annotated = 0;
    for (const auto& pair : model.definitions) {
      if (pair.second.texts.empty()) continue;
      verify(pair.second, true);
      ++annotated;
    }
    EXPECT_EQ(annotated, 1u);
    if (std::string(name) == "annotation_layers_vff.skp") {
      // The existing model-level world-space list remains available as before.
      ASSERT_EQ(model.dimensions.size(), 4u);
      for (const auto& dim : model.dimensions) EXPECT_EQ(dim.layer, "尺寸标注");
    }
  }
}

TEST(PagesDimensions, DrawingPropertiesComeFromDimensionNotItsConnections) {
  const auto free_point = [](double x, double y, double z) {
    return tlv(0x5208, test::concat({tlv(0x5209, {1, 0, 0, 0}), tlv(0x520a, vec3(x, y, z)),
                                     tlv(0x07d0, tlv(0x07d2, ByteBuffer{99}))}));
  };
  auto connection = free_point(0, 0, 0);
  auto body = test::concat({
      tlv(0x59d8,
          tlv(0x07d0, test::concat({tlv(0x07d2, ByteBuffer{12}), tlv(0x07d3, ByteBuffer{7})}))),
      tlv(0x5bcd, connection),
      tlv(0x5bce, free_point(10, 0, 0)),
      tlv(0x5bd2, test::f64s({3})),
  });
  const auto dims = parse_dimensions(tlv(0x5bcc, body), {}, {});
  ASSERT_EQ(dims.size(), 1u);
  EXPECT_EQ(dims[0].layer, "12");
  EXPECT_TRUE(dims[0].hidden);
}

}  // namespace openskp
