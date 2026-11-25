#include "openskp/stl_export.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>
#include <gtest/gtest.h>
#include <map>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

#include "openskp/create.hpp"
#include "openskp/instanced_scene.hpp"
#include "openskp/model.hpp"
#include "openskp/parser.hpp"
#include "openskp/ply_export.hpp"

namespace openskp {
namespace {

using Tri = std::array<std::array<double, 3>, 3>;

const std::vector<std::vector<Point3>> kBoxFaces = {
    {{0, 0, 0}, {0, 10, 0}, {10, 10, 0}, {10, 0, 0}},
    {{0, 0, 10}, {10, 0, 10}, {10, 10, 10}, {0, 10, 10}},
    {{0, 0, 0}, {10, 0, 0}, {10, 0, 10}, {0, 0, 10}},
    {{10, 0, 0}, {10, 10, 0}, {10, 10, 10}, {10, 0, 10}},
    {{10, 10, 0}, {0, 10, 0}, {0, 10, 10}, {10, 10, 10}},
    {{0, 10, 0}, {0, 0, 0}, {0, 0, 10}, {0, 10, 10}},
};

ByteBuffer build_box_bytes(const std::string& paint) {
  auto builder = create();
  const int red = builder->add_material("Red", Color3{255, 0, 0});
  const int blue = builder->add_material("Blue", Color3{0, 0, 255});
  for (std::size_t i = 0; i < kBoxFaces.size(); ++i) {
    FaceOptions opts;
    if (paint == "one_face") {
      if (i == 0) opts.material = red;
    } else {
      if (paint == "front" || paint == "both" || paint == "mixed") opts.material = red;
      if (paint == "back" || paint == "both" || paint == "mixed") opts.back_material = blue;
      if (paint == "mixed" && (i % 2 == 1)) std::swap(opts.material, opts.back_material);
    }
    builder->add_face(kBoxFaces[i], opts);
  }
  return builder->to_bytes();
}

std::vector<Tri> parse_ascii_stl(const std::string& text) {
  std::istringstream ss(text);
  std::string line;
  std::vector<std::array<double, 3>> verts;
  while (std::getline(ss, line)) {
    std::istringstream ls(line);
    std::string kw;
    ls >> kw;
    if (kw == "vertex") {
      double x = 0.0, y = 0.0, z = 0.0;
      ls >> x >> y >> z;
      verts.push_back({x, y, z});
    }
  }
  std::vector<Tri> tris;
  for (std::size_t i = 0; i + 2 < verts.size(); i += 3) {
    tris.push_back({verts[i], verts[i + 1], verts[i + 2]});
  }
  return tris;
}

std::vector<Tri> parse_binary_stl(const std::vector<std::uint8_t>& data) {
  std::uint32_t count = 0;
  std::memcpy(&count, data.data() + 80, 4);
  EXPECT_EQ(data.size(), 84U + static_cast<std::size_t>(count) * 50U);
  std::vector<Tri> tris;
  tris.reserve(count);
  for (std::uint32_t i = 0; i < count; ++i) {
    const std::size_t base = 84U + static_cast<std::size_t>(i) * 50U + 12U;
    Tri t{};
    for (int v = 0; v < 3; ++v) {
      for (int c = 0; c < 3; ++c) {
        float f = 0.0f;
        std::memcpy(&f, data.data() + base + (v * 3 + c) * 4, 4);
        t[v][c] = static_cast<double>(f);
      }
    }
    tris.push_back(t);
  }
  return tris;
}

void assert_closed_10in_box(const std::vector<Tri>& tris) {
  ASSERT_EQ(tris.size(), 12U);
  using VKey = std::array<long long, 3>;
  auto vkey = [](const std::array<double, 3>& p) -> VKey {
    return {std::llround(p[0] * 1e6), std::llround(p[1] * 1e6), std::llround(p[2] * 1e6)};
  };
  std::map<std::pair<VKey, VKey>, int> edge_counts;
  double vol_m3 = 0.0;
  for (const auto& [v0, v1, v2] : tris) {
    const double cx = v1[1] * v2[2] - v1[2] * v2[1];
    const double cy = v1[2] * v2[0] - v1[0] * v2[2];
    const double cz = v1[0] * v2[1] - v1[1] * v2[0];
    vol_m3 += (v0[0] * cx + v0[1] * cy + v0[2] * cz) / 6.0;

    std::array<VKey, 3> keys = {vkey(v0), vkey(v1), vkey(v2)};
    for (int e = 0; e < 3; ++e) {
      auto a = keys[e];
      auto b = keys[(e + 1) % 3];
      if (b < a) std::swap(a, b);
      edge_counts[{a, b}]++;
    }
  }
  const double vol_cm3 = vol_m3 * 1e6;
  EXPECT_NEAR(std::round(vol_cm3 * 10.0) / 10.0, 16387.1, 1e-9);
  EXPECT_NEAR(vol_cm3, 16387.064, 0.05);
  EXPECT_EQ(edge_counts.size(), 18U);
  for (const auto& [edge, cnt] : edge_counts) {
    EXPECT_EQ(cnt, 2);
  }
}

TEST(StlExport, SerializesSceneToStlAsciiText) {
  Scene scene;
  GlbPrimitive prim;
  prim.geom_name = "Box";
  prim.material_index = 0;
  prim.positions = {0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0};
  prim.normals = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.uvs = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.indices = {0, 1, 2};
  scene.glb_primitives.push_back(prim);

  std::string stl_text = to_stl_ascii(scene, 1.0f);
  EXPECT_NE(stl_text.find("solid OpenSKP_Model"), std::string::npos);
  EXPECT_NE(stl_text.find("facet normal 0.000000 0.000000 1.000000"), std::string::npos);
  EXPECT_NE(stl_text.find("vertex 0.000000 0.000000 0.000000"), std::string::npos);
  EXPECT_NE(stl_text.find("endsolid OpenSKP_Model"), std::string::npos);
}

TEST(StlExport, SerializesSceneToStlBinaryData) {
  Scene scene;
  GlbPrimitive prim;
  prim.geom_name = "Box";
  prim.material_index = 0;
  prim.positions = {0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0};
  prim.normals = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.uvs = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.indices = {0, 1, 2};
  scene.glb_primitives.push_back(prim);

  std::vector<std::uint8_t> data = to_stl_binary(scene, 1.0f);
  EXPECT_EQ(data.size(), 80 + 4 + 50);  // Header + uint32 count + 1 triangle
}

TEST(StlExport, OmitsPaintedBackTrianglesAcrossAllBoxPaintModes) {
  for (const std::string paint : {"unpainted", "one_face", "front", "back", "both", "mixed"}) {
    SCOPED_TRACE(paint);
    const auto bytes = build_box_bytes(paint);
    const auto scene = SkpFile::from_buffer(bytes).build_scene();
    assert_closed_10in_box(parse_ascii_stl(to_stl_ascii(scene)));
    assert_closed_10in_box(parse_binary_stl(to_stl_binary(scene)));
  }
}

TEST(StlExport, KeepsBothPaintedSidesInColorExportsAndRecordsInstancedBackTriangles) {
  const auto bytes = build_box_bytes("both");
  const auto skp = SkpFile::from_buffer(bytes);
  const auto baked = skp.build_scene();
  const auto instanced = skp.build_instanced_scene();

  std::size_t total_tris = 0;
  std::size_t baked_back_tris = 0;
  for (const auto& p : baked.glb_primitives) {
    total_tris += p.indices.size() / 3;
    baked_back_tris += p.back_triangles.size();
  }
  EXPECT_EQ(total_tris, 24U);
  EXPECT_EQ(baked_back_tris, 12U);

  std::size_t inst_back_tris = 0;
  for (const auto& r : instanced.mesh_resources) {
    for (const auto& p : r.primitives) {
      inst_back_tris += p.back_triangles.size();
    }
  }
  EXPECT_EQ(inst_back_tris, 12U);

  const std::string ply = to_ply_ascii(baked);
  EXPECT_NE(ply.find("element face 24"), std::string::npos);
  EXPECT_NE(ply.find("255 0 0 255"), std::string::npos);
  EXPECT_NE(ply.find("0 0 255 255"), std::string::npos);
}

}  // namespace
}  // namespace openskp
