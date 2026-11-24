#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <gtest/gtest.h>
#include <set>
#include <stdexcept>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

#include <openskp/openskp.hpp>

#define TINYGLTF_NO_STB_IMAGE
#define TINYGLTF_NO_STB_IMAGE_WRITE
#include <tiny_gltf.h>

#include "test_helpers.hpp"

namespace openskp {
namespace {

constexpr Color3 kFront{11, 122, 233};
constexpr Color3 kBack{201, 102, 3};

Color3 rgb_of(const std::vector<GltfMaterial>& mats, std::size_t idx) {
  const auto& f = mats.at(idx).pbr_metallic_roughness.base_color_factor;
  return {
      static_cast<std::uint8_t>(std::lround(f[0] * 255.0)),
      static_cast<std::uint8_t>(std::lround(f[1] * 255.0)),
      static_cast<std::uint8_t>(std::lround(f[2] * 255.0)),
  };
}

using Pt = std::array<double, 3>;
using Tri = std::array<Pt, 3>;

Pt round5(float x, float y, float z) {
  return {std::round(static_cast<double>(x) * 1e5) / 1e5,
          std::round(static_cast<double>(y) * 1e5) / 1e5,
          std::round(static_cast<double>(z) * 1e5) / 1e5};
}

std::vector<Tri> corners(const std::vector<float>& positions,
                         const std::vector<std::uint32_t>& indices) {
  std::vector<Pt> pts;
  pts.reserve(positions.size() / 3);
  for (std::size_t i = 0; i < positions.size(); i += 3) {
    pts.push_back(round5(positions[i], positions[i + 1], positions[i + 2]));
  }
  std::vector<Tri> out;
  out.reserve(indices.size() / 3);
  for (std::size_t t = 0; t < indices.size(); t += 3) {
    out.push_back({pts.at(indices[t]), pts.at(indices[t + 1]), pts.at(indices[t + 2])});
  }
  return out;
}

bool same_winding(const Tri& a, const Tri& b) {
  return b == a || b == Tri{a[1], a[2], a[0]} || b == Tri{a[2], a[0], a[1]};
}

std::size_t count_occurrences(const std::string& haystack, const std::string& needle) {
  std::size_t count = 0;
  std::size_t pos = 0;
  while ((pos = haystack.find(needle, pos)) != std::string::npos) {
    ++count;
    pos += needle.size();
  }
  return count;
}

SkpFile build_box_skp() {
  auto builder = create();
  std::vector<Point3> pts;
  for (double z : {0.0, 10.0}) {
    for (double y : {0.0, 10.0}) {
      for (double x : {0.0, 10.0}) {
        pts.push_back({x, y, z});
      }
    }
  }
  const std::array<std::array<std::size_t, 4>, 6> quads{{
      {0, 2, 3, 1},
      {4, 5, 7, 6},
      {0, 1, 5, 4},
      {1, 3, 7, 5},
      {3, 2, 6, 7},
      {2, 0, 4, 6},
  }};
  for (const auto& q : quads) {
    builder->add_face({pts[q[0]], pts[q[1]], pts[q[2]], pts[q[3]]});
  }
  return SkpFile::from_buffer(builder->to_bytes());
}

double linear_to_srgb(double c) {
  if (c <= 0.0) return 0.0;
  if (c >= 1.0) return 1.0;
  return c <= 0.0031308 ? c * 12.92 : 1.055 * std::pow(c, 1.0 / 2.4) - 0.055;
}

std::vector<std::pair<Color3, bool>> glb_materials(const ByteBuffer& glb_bytes) {
  tinygltf::TinyGLTF loader;
  tinygltf::Model model;
  std::string err;
  std::string warn;
  const bool ok = loader.LoadBinaryFromMemory(&model, &err, &warn, glb_bytes.data(),
                                              static_cast<unsigned int>(glb_bytes.size()));
  EXPECT_TRUE(ok) << err;
  std::vector<std::pair<Color3, bool>> out;
  for (const auto& m : model.materials) {
    const auto& f = m.pbrMetallicRoughness.baseColorFactor;
    Color3 rgb{
        static_cast<std::uint8_t>(std::lround(linear_to_srgb(f[0]) * 255.0)),
        static_cast<std::uint8_t>(std::lround(linear_to_srgb(f[1]) * 255.0)),
        static_cast<std::uint8_t>(std::lround(linear_to_srgb(f[2]) * 255.0)),
    };
    out.emplace_back(rgb, m.doubleSided);
  }
  std::sort(out.begin(), out.end());
  return out;
}

TEST(UnpaintedFaceColors, EachSideShowsModelsFaceColourAcrossVersions) {
  const char* versions[] = {"v4", "v5", "v6", "2016", "2017", "2018", "2019", "2020", "2021"};
  for (const char* v : versions) {
    SCOPED_TRACE(v);
    const std::string fname = std::string("face_colors_") + v + ".skp";
    const auto skp = SkpFile::open(test::fixture(fname.c_str()));
    const auto baked = skp.build_scene();
    const auto instanced = skp.build_instanced_scene();

    ASSERT_EQ(baked.glb_primitives.size(), 2u);
    auto baked_prims = baked.glb_primitives;
    std::sort(baked_prims.begin(), baked_prims.end(), [](const auto& a, const auto& b) {
      return static_cast<int>(a.unpainted_back) < static_cast<int>(b.unpainted_back);
    });
    EXPECT_FALSE(baked_prims[0].unpainted_back);
    EXPECT_TRUE(baked_prims[1].unpainted_back);
    EXPECT_EQ(rgb_of(baked.gltf_materials, baked_prims[0].material_index), kFront);
    EXPECT_EQ(rgb_of(baked.gltf_materials, baked_prims[1].material_index), kBack);
    EXPECT_FALSE(baked.gltf_materials.at(baked_prims[0].material_index).double_sided);
    EXPECT_FALSE(baked.gltf_materials.at(baked_prims[1].material_index).double_sided);

    const auto fronts = corners(baked_prims[0].positions, baked_prims[0].indices);
    const auto backs = corners(baked_prims[1].positions, baked_prims[1].indices);
    ASSERT_FALSE(fronts.empty());
    ASSERT_EQ(fronts.size(), backs.size());
    for (std::size_t i = 0; i < fronts.size(); ++i) {
      std::set<Pt> sa(fronts[i].begin(), fronts[i].end());
      std::set<Pt> sb(backs[i].begin(), backs[i].end());
      EXPECT_EQ(sa, sb);
      EXPECT_FALSE(same_winding(fronts[i], backs[i]));
    }

    std::vector<LocalPrimitive> inst_prims;
    for (const auto& r : instanced.mesh_resources) {
      inst_prims.insert(inst_prims.end(), r.primitives.begin(), r.primitives.end());
    }
    ASSERT_EQ(inst_prims.size(), 2u);
    std::sort(inst_prims.begin(), inst_prims.end(), [](const auto& a, const auto& b) {
      return static_cast<int>(a.unpainted_back) < static_cast<int>(b.unpainted_back);
    });
    EXPECT_FALSE(inst_prims[0].unpainted_back);
    EXPECT_TRUE(inst_prims[1].unpainted_back);
    EXPECT_EQ(rgb_of(instanced.gltf_materials, inst_prims[0].material_index), kFront);
    EXPECT_EQ(rgb_of(instanced.gltf_materials, inst_prims[1].material_index), kBack);
    EXPECT_FALSE(instanced.gltf_materials.at(inst_prims[0].material_index).double_sided);
    EXPECT_FALSE(instanced.gltf_materials.at(inst_prims[1].material_index).double_sided);
  }
}

TEST(UnpaintedFaceColors, TagOptionIsOldBehaviour) {
  ParseOptions opts;
  opts.unpainted_faces = "tag";
  for (const char* v : {"v4", "2017", "2021"}) {
    SCOPED_TRACE(v);
    const std::string fname = std::string("face_colors_") + v + ".skp";
    const auto skp = SkpFile::open(test::fixture(fname.c_str()));
    const auto layer = skp.parse().layers.at(0);
    const Color3 layer_rgb = layer.color;

    const auto baked = skp.build_scene(opts);
    ASSERT_EQ(baked.glb_primitives.size(), 1u);
    EXPECT_EQ(rgb_of(baked.gltf_materials, baked.glb_primitives[0].material_index), layer_rgb);
    EXPECT_TRUE(baked.gltf_materials.at(baked.glb_primitives[0].material_index).double_sided);
    EXPECT_FALSE(baked.glb_primitives[0].unpainted_back);

    const auto instanced = skp.build_instanced_scene(opts);
    ASSERT_EQ(instanced.mesh_resources.size(), 1u);
    ASSERT_EQ(instanced.mesh_resources[0].primitives.size(), 1u);
    const auto& prim = instanced.mesh_resources[0].primitives[0];
    EXPECT_EQ(rgb_of(instanced.gltf_materials, prim.material_index), layer_rgb);
    EXPECT_TRUE(instanced.gltf_materials.at(prim.material_index).double_sided);
    EXPECT_FALSE(prim.unpainted_back);
  }
}

TEST(UnpaintedFaceColors, FileWithoutFaceColoursUsesTagColour) {
  const auto skp = SkpFile::open(test::fixture("legacy_v3_synthetic.skp"));
  EXPECT_FALSE(skp.parse().face_front_color.has_value());

  ParseOptions tag_opts;
  tag_opts.unpainted_faces = "tag";
  const auto style_baked = skp.build_scene();
  const auto tag_baked = skp.build_scene(tag_opts);
  ASSERT_EQ(style_baked.glb_primitives.size(), tag_baked.glb_primitives.size());
  for (std::size_t i = 0; i < style_baked.glb_primitives.size(); ++i) {
    EXPECT_EQ(rgb_of(style_baked.gltf_materials, style_baked.glb_primitives[i].material_index),
              rgb_of(tag_baked.gltf_materials, tag_baked.glb_primitives[i].material_index));
    EXPECT_FALSE(style_baked.glb_primitives[i].unpainted_back);
  }

  const auto style_inst = skp.build_instanced_scene();
  const auto tag_inst = skp.build_instanced_scene(tag_opts);
  ASSERT_EQ(style_inst.mesh_resources.size(), tag_inst.mesh_resources.size());
  for (const auto& r : style_inst.mesh_resources) {
    for (const auto& p : r.primitives) {
      EXPECT_FALSE(p.unpainted_back);
    }
  }
}

TEST(UnpaintedFaceColors, UnknownOptionThrowsInvalidArgument) {
  const auto skp = SkpFile::open(test::fixture("face_colors_2017.skp"));
  ParseOptions bad_opts;
  bad_opts.unpainted_faces = "layer";
  EXPECT_THROW(skp.build_scene(bad_opts), std::invalid_argument);
  EXPECT_THROW(skp.build_instanced_scene(bad_opts), std::invalid_argument);
}

TEST(UnpaintedFaceColors, PaintWinsOverFaceColours) {
  auto builder = create();
  const int red = builder->add_material("Red", Color4{200, 10, 10, 255});
  const std::vector<Point3> square{{0, 0, 0}, {10, 0, 0}, {10, 10, 0}, {0, 10, 0}};
  auto& comp = builder->add_component_definition("Painted");
  comp.add_face({{0, 0, 9}, {10, 0, 9}, {10, 10, 9}, {0, 10, 9}});
  comp.close();

  FaceOptions front_red;
  front_red.material = red;
  builder->add_face(square, front_red);

  FaceOptions back_red;
  back_red.back_material = red;
  builder->add_face({{0, 0, 5}, {10, 0, 5}, {10, 10, 5}, {0, 10, 5}}, back_red);

  InstanceOptions painted_inst;
  painted_inst.material = red;
  builder->add_instance(comp, painted_inst);

  const auto skp = SkpFile::from_buffer(builder->to_bytes());
  const auto model = skp.parse();
  ASSERT_TRUE(model.face_front_color.has_value());
  ASSERT_TRUE(model.face_back_color.has_value());
  const auto front = *model.face_front_color;
  const auto back = *model.face_back_color;
  ASSERT_NE(front, back);

  using Side = std::tuple<Color3, bool, bool>;
  const auto baked = skp.build_scene();
  std::set<Side> baked_sides;
  for (const auto& p : baked.glb_primitives) {
    EXPECT_FALSE(p.unpainted_back);
    baked_sides.emplace(rgb_of(baked.gltf_materials, p.material_index),
                        baked.gltf_materials.at(p.material_index).double_sided, p.unpainted_back);
  }
  EXPECT_GT(baked_sides.count(Side{Color3{200, 10, 10}, true, false}), 0u);
  EXPECT_GT(baked_sides.count(Side{front, false, false}), 0u);
  EXPECT_GT(baked_sides.count(Side{back, false, false}), 0u);

  const auto instanced = skp.build_instanced_scene();
  std::set<Side> inst_sides;
  for (const auto& r : instanced.mesh_resources) {
    for (const auto& p : r.primitives) {
      EXPECT_FALSE(p.unpainted_back);
      inst_sides.emplace(rgb_of(instanced.gltf_materials, p.material_index),
                         instanced.gltf_materials.at(p.material_index).double_sided,
                         p.unpainted_back);
    }
  }
  EXPECT_GT(inst_sides.count(Side{Color3{200, 10, 10}, true, false}), 0u);
  EXPECT_GT(inst_sides.count(Side{front, false, false}), 0u);
  EXPECT_GT(inst_sides.count(Side{back, false, false}), 0u);
}

TEST(UnpaintedFaceColors, ComponentOnTwoTagsIsOneMeshResource) {
  auto builder = create();
  LayerOptions opts_a;
  opts_a.color = Color4{255, 0, 0, 255};
  const int tag_a = builder->add_layer("A", opts_a);
  LayerOptions opts_b;
  opts_b.color = Color4{0, 0, 255, 255};
  const int tag_b = builder->add_layer("B", opts_b);

  auto& part = builder->add_component_definition("Part");
  part.add_face({{0, 0, 0}, {1, 0, 0}, {1, 1, 0}, {0, 1, 0}});
  part.close();

  InstanceOptions inst_a;
  inst_a.layer = tag_a;
  builder->add_instance(part, inst_a);

  InstanceOptions inst_b;
  inst_b.layer = tag_b;
  inst_b.translation = {5, 0, 0};
  builder->add_instance(part, inst_b);

  const auto skp = SkpFile::from_buffer(builder->to_bytes());
  EXPECT_EQ(skp.build_instanced_scene().mesh_resources.size(), 1u);
}

TEST(UnpaintedFaceColors, SingleColourExportsAndFragmentsKeepOneCopyOfEachFace) {
  const auto skp = build_box_skp();
  ParseOptions tag_opts;
  tag_opts.unpainted_faces = "tag";
  const auto style = skp.build_scene();
  const auto tag = skp.build_scene(tag_opts);

  std::size_t glb_tris = 0;
  for (const auto& p : style.glb_primitives) glb_tris += p.indices.size() / 3;
  std::size_t surf_tris = 0;
  for (const auto& p : style.surface_primitives()) surf_tris += p.indices.size() / 3;
  EXPECT_EQ(glb_tris, 24u);
  EXPECT_EQ(surf_tris, 12u);

  EXPECT_EQ(to_stl_ascii(style), to_stl_ascii(tag));
  const auto stl_bin = to_stl_binary(style);
  ASSERT_GE(stl_bin.size(), 84u);
  std::uint32_t stl_count = 0;
  std::memcpy(&stl_count, stl_bin.data() + 80, 4);
  EXPECT_EQ(stl_count, 12u);

  EXPECT_EQ(count_occurrences(to_obj(style), "\nf "), 12u);
  EXPECT_EQ(count_occurrences(to_obj(tag), "\nf "), 12u);
  EXPECT_NE(to_ply_ascii(style).find("element face 12\n"), std::string::npos);

  const auto dxf_style = count_occurrences(to_dxf(style, METRES_TO_INCHES, "3dface"), "3DFACE");
  const auto dxf_tag = count_occurrences(to_dxf(tag, METRES_TO_INCHES, "3dface"), "3DFACE");
  EXPECT_GT(dxf_style, 0u);
  EXPECT_EQ(dxf_style, dxf_tag);

  EXPECT_EQ(count_occurrences(to_ifc(style), "IFCTRIANGULATEDFACESET"), 1u);
  EXPECT_EQ(count_occurrences(to_ifc(tag), "IFCTRIANGULATEDFACESET"), 1u);

  auto frag_tris = [](const InstancedScene& inst) {
    const auto round_trip = from_fragments(to_fragments(inst));
    std::size_t total = 0;
    for (const auto& r : round_trip.mesh_resources) {
      for (const auto& p : r.primitives) total += p.indices.size() / 3;
    }
    return total;
  };
  EXPECT_EQ(frag_tris(skp.build_instanced_scene()), 12u);
  EXPECT_EQ(frag_tris(skp.build_instanced_scene(tag_opts)), 12u);
}

TEST(UnpaintedFaceColors, GlbExportsShowBothSides) {
  const auto skp = SkpFile::open(test::fixture("face_colors_2017.skp"));
  const auto layer_rgb = skp.parse().layers.at(0).color;
  std::vector<std::pair<Color3, bool>> expected_style{{kFront, false}, {kBack, false}};
  std::sort(expected_style.begin(), expected_style.end());

  ParseOptions tag_opts;
  tag_opts.unpainted_faces = "tag";

  EXPECT_EQ(glb_materials(to_glb(skp.build_scene())), expected_style);
  EXPECT_EQ(glb_materials(to_glb(skp.build_scene(tag_opts))),
            (std::vector<std::pair<Color3, bool>>{{layer_rgb, true}}));

  EXPECT_EQ(glb_materials(to_instanced_glb(skp.build_instanced_scene())), expected_style);
  EXPECT_EQ(glb_materials(to_instanced_glb(skp.build_instanced_scene(tag_opts))),
            (std::vector<std::pair<Color3, bool>>{{layer_rgb, true}}));
}

}  // namespace
}  // namespace openskp
