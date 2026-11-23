#include <algorithm>
#include <cmath>
#include <gtest/gtest.h>
#include <set>
#include <sstream>
#include <string>
#include <vector>

#include <openskp/openskp.hpp>

#include "openskp/ifc_export.hpp"
#include "test_helpers.hpp"

namespace openskp {
namespace {

std::string entity_line(const std::string& text, const std::string& token) {
  std::istringstream ss(text);
  std::string line;
  while (std::getline(ss, line)) {
    if (!line.empty() && line.back() == '\r') line.pop_back();
    if (line.find(token) != std::string::npos) return line;
  }
  ADD_FAILURE() << "No entity line containing " << token;
  return {};
}

std::vector<std::string> split_attributes(const std::string& line) {
  const auto first = line.find('(');
  const auto last = line.rfind(')');
  if (first == std::string::npos || last == std::string::npos || last <= first) return {};
  const std::string body = line.substr(first + 1, last - first - 1);
  std::vector<std::string> out;
  int depth = 0;
  std::string cur;
  for (char ch : body) {
    if (ch == '(') ++depth;
    if (ch == ')') --depth;
    if (ch == ',' && depth == 0) {
      out.push_back(cur);
      cur.clear();
    } else {
      cur.push_back(ch);
    }
  }
  out.push_back(cur);
  return out;
}

void assert_ids_are_dense(const std::string& text) {
  std::istringstream ss(text);
  std::string line;
  std::vector<int> ids;
  while (std::getline(ss, line)) {
    if (!line.empty() && line[0] == '#') {
      const auto eq = line.find('=');
      if (eq != std::string::npos) {
        ids.push_back(std::stoi(line.substr(1, eq - 1)));
      }
    }
  }
  ASSERT_FALSE(ids.empty());
  const std::set<int> unique(ids.begin(), ids.end());
  EXPECT_EQ(unique.size(), ids.size());
  EXPECT_EQ(*std::max_element(ids.begin(), ids.end()), static_cast<int>(ids.size()));
}

Scene make_mesh_scene(const std::string& name, const std::vector<float>& positions,
                      const std::vector<std::uint32_t>& indices) {
  Scene scene;
  scene.scene_hierarchy.name = "Root";
  GlbPrimitive prim;
  prim.geom_name = name;
  prim.material_index = 0;
  prim.positions = positions;
  prim.normals.assign(positions.size(), 0.0f);
  prim.uvs.assign((positions.size() / 3) * 2, 0.0f);
  prim.indices = indices;
  scene.glb_primitives.push_back(prim);

  MeshMetadata meta;
  meta.name = name;
  scene.mesh_index[name] = meta;

  GltfMaterial mat;
  mat.pbr_metallic_roughness.base_color_factor = {0.5, 0.5, 0.5, 1.0};
  scene.gltf_materials = {mat};
  return scene;
}

std::size_t count_occurrences(const std::string& text, const std::string& needle) {
  std::size_t count = 0;
  for (std::size_t pos = 0; (pos = text.find(needle, pos)) != std::string::npos;
       pos += needle.size()) {
    ++count;
  }
  return count;
}

TEST(IfcConformanceAndCurves, StepTextEscapingAndGuidLeadingBits) {
  const std::string guid = generate_ifc_guid();
  ASSERT_EQ(guid.size(), 22u);
  EXPECT_TRUE(guid[0] >= '0' && guid[0] <= '3');

  const std::string zh_name = "\xE5\x89\xAA\xE5\x8A\x9B\xE5\xA2\x99 JLQ-1";  // 剪力墙 JLQ-1
  const std::string zh_layer = "\xE9\x9A\x90\xE8\x97\x8F\xE5\xB1\x82";       // 隐藏层
  Scene scene = make_mesh_scene(zh_name, {0, 0, 0, 1, 0, 0, 0, 1, 0}, {0, 1, 2});
  scene.mesh_index[zh_name].layer = zh_layer;
  scene.mesh_index[zh_name].properties["\xE5\x90\x8D\xE7\xA7\xB0"] =
      "\xE6\x89\xBF\xE9\x87\x8D\xE5\xA2\x99";
  scene.mesh_index[zh_name].properties["emoji"] = "a\xF0\x9F\x98\x80\xE5\xA2\x99";
  scene.layer_hidden[zh_layer] = true;

  const std::string text = to_ifc(scene);
  EXPECT_EQ(text.find(zh_name), std::string::npos);
  EXPECT_NE(text.find("\\X2\\526A529B5899\\X0\\ JLQ-1"), std::string::npos);
  EXPECT_NE(text.find("\\X2\\540D79F0\\X0\\"), std::string::npos);
  EXPECT_NE(text.find("\\X2\\627F91CD5899\\X0\\"), std::string::npos);
  EXPECT_NE(text.find("a\\X4\\0001F60000005899\\X0\\"), std::string::npos);
  EXPECT_NE(entity_line(text, "IFCPRESENTATIONLAYERWITHSTYLE(").find(",.F.,.F.,.F.,())"),
            std::string::npos);
}

TEST(IfcConformanceAndCurves, EnumSpellingsAndPerEntityAttributeCounts) {
  const std::string door_text =
      to_ifc(make_mesh_scene("Front Door", {0, 0, 0, 1, 0, 0, 0, 1, 0}, {0, 1, 2}));
  EXPECT_EQ(door_text.find(".TRUE."), std::string::npos);
  EXPECT_EQ(door_text.find(".FALSE."), std::string::npos);
  EXPECT_EQ(door_text.find(".READWRITE."), std::string::npos);
  EXPECT_EQ(door_text.find(".STERADIANUNIT."), std::string::npos);
  EXPECT_NE(door_text.find(".NOCHANGE."), std::string::npos);
  EXPECT_NE(door_text.find(".SOLIDANGLEUNIT."), std::string::npos);
  EXPECT_NE(door_text.find(".STERADIAN."), std::string::npos);
  EXPECT_EQ(split_attributes(entity_line(door_text, "IFCDOOR(")).size(), 13u);

  const std::string wall_text =
      to_ifc(make_mesh_scene("Outer Wall", {0, 0, 0, 1, 0, 0, 0, 1, 0}, {0, 1, 2}));
  EXPECT_EQ(split_attributes(entity_line(wall_text, "IFCWALL(")).size(), 9u);

  const std::string door_2x3 =
      to_ifc(make_mesh_scene("Front Door", {0, 0, 0, 1, 0, 0, 0, 1, 0}, {0, 1, 2}), METRES_TO_MM,
             "IFC2X3");
  EXPECT_EQ(split_attributes(entity_line(door_2x3, "IFCDOOR(")).size(), 10u);
}

TEST(IfcConformanceAndCurves, ClosedFlagComputedFromWeldedManifoldTopology) {
  auto closed_flag = [](const std::string& txt) {
    return split_attributes(entity_line(txt, "IFCTRIANGULATEDFACESET("))[2];
  };
  EXPECT_EQ(closed_flag(to_ifc(make_mesh_scene("Open", {0, 0, 0, 1, 0, 0, 0, 1, 0}, {0, 1, 2}))),
            ".F.");

  const std::vector<float> per_face_tetra{
      0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1,
      0, 0, 0, 0, 1, 0, 0, 0, 1, 1, 0, 0, 0, 1, 0, 0, 0, 1,
  };
  EXPECT_EQ(closed_flag(to_ifc(make_mesh_scene("PerFaceTetra", per_face_tetra,
                                               {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11}))),
            ".T.");
}

TEST(IfcConformanceAndCurves, CurveSetsExportAsIfcAnnotationAndInlineIfcArcIndex) {
  Scene poly_scene;
  poly_scene.scene_hierarchy.name = "Root";
  CurveSetMetadata poly_cs;
  poly_cs.name = "ClosedOutline";
  poly_cs.layer = "Framing";
  poly_cs.path = "ROOT";
  poly_cs.points_m = {{0.0, 0.0, 0.0}, {1.0, 0.0, 0.0}, {0.0, 0.0, 1.0}};
  poly_cs.closed = true;
  poly_cs.attribute_dictionaries["frame-meta"]["mark"] = "B1";
  poly_scene.curve_sets.push_back(poly_cs);

  const std::string poly_text = to_ifc(poly_scene);
  assert_ids_are_dense(poly_text);
  EXPECT_NE(poly_text.find("IFCGEOMETRICREPRESENTATIONSUBCONTEXT('Annotation','Model'"),
            std::string::npos);
  EXPECT_NE(poly_text.find("IFCANNOTATION("), std::string::npos);
  EXPECT_NE(poly_text.find("'ClosedOutline'"), std::string::npos);
  EXPECT_NE(poly_text.find("'Pset_frame-meta'"), std::string::npos);
  EXPECT_EQ(count_occurrences(split_attributes(entity_line(poly_text, "IFCPOLYLINE("))[0], "#"),
            4u);

  Scene arc_scene;
  arc_scene.scene_hierarchy.name = "Root";
  CurveSetMetadata arc_cs;
  arc_cs.name = "ArcRun";
  arc_cs.layer = "Layer0";
  arc_cs.path = "ROOT";
  arc_cs.points_m = {{1.0, 0.0, 0.0}, {0.0, 0.0, -1.0}};
  arc_cs.closed = false;
  const double s2 = std::sqrt(0.5);
  arc_cs.arc = CurveArc{{{1.0, 0.0, 0.0}, {s2, 0.0, -s2}, {0.0, 0.0, -1.0}}, {{0, 1, 2}}};
  arc_scene.curve_sets.push_back(arc_cs);

  const std::string arc_text = to_ifc(arc_scene);
  assert_ids_are_dense(arc_text);
  EXPECT_NE(arc_text.find("IFCINDEXEDPOLYCURVE("), std::string::npos);
  EXPECT_NE(arc_text.find("IFCARCINDEX((1,2,3))"), std::string::npos);
  EXPECT_EQ(arc_text.find("=IFCARCINDEX("), std::string::npos);

  const std::string capilla_ifc =
      to_ifc(SkpFile::open(test::fixture("capilla_quiroz_v17.skp")).build_scene());
  assert_ids_are_dense(capilla_ifc);
  EXPECT_EQ(count_occurrences(capilla_ifc, "=IFCANNOTATION("), 20u);

  const std::string arcs_ifc = to_ifc(SkpFile::open(test::fixture("arcs_2021.skp")).build_scene());
  assert_ids_are_dense(arcs_ifc);
  EXPECT_EQ(count_occurrences(arcs_ifc, "=IFCINDEXEDPOLYCURVE("), 1u);
  EXPECT_NE(arcs_ifc.find("IFCARCINDEX((1,2,3))"), std::string::npos);
}

}  // namespace
}  // namespace openskp
