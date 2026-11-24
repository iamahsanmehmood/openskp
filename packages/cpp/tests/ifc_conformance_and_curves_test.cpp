#include <algorithm>
#include <cmath>
#include <filesystem>
#include <gtest/gtest.h>
#include <limits>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
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

  GlbPrimitive back_prim = scene.glb_primitives.front();
  back_prim.unpainted_back = true;
  scene.glb_primitives.push_back(back_prim);

  std::vector<std::pair<std::string, std::string>> seen_args;
  const std::string text =
      to_ifc(scene, METRES_TO_MM, "IFC4",
             [&](const std::string& geom_name, const std::string& layer_name) {
               seen_args.emplace_back(geom_name, layer_name);
               return std::make_pair(std::string("IFCWALL"), std::string("IfcWall"));
             });
  ASSERT_EQ(seen_args.size(), 1u);
  EXPECT_EQ(seen_args[0].first, zh_name);
  EXPECT_EQ(seen_args[0].second, zh_layer);
  EXPECT_EQ(count_occurrences(text, "=IFCTRIANGULATEDFACESET("), 1u);
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

TEST(IfcConformanceAndCurves, LargeCoordinateFormatsWithoutStackBufferOverflow) {
  Scene huge_scene;
  huge_scene.scene_hierarchy.name = "Root";
  CurveSetMetadata cs;
  cs.name = "Astronomical";
  cs.layer = "Layer0";
  cs.path = "ROOT";
  const double big = std::ldexp(1.0, 200);
  cs.points_m = {{big, 0.0, 0.0}, {0.0, 0.0, 0.0}};
  huge_scene.curve_sets.push_back(cs);

  const std::string text = to_ifc(huge_scene, 1.0);
  const std::string line = entity_line(text, "IFCCARTESIANPOINT((1");
  // "%.6f" of 1e60 has 61 integer digits + '.' + 6 fractional digits = 68 chars (> 64-byte stack
  // buf).
  const auto open_p = line.find("((");
  const auto comma = line.find(',', open_p);
  ASSERT_NE(open_p, std::string::npos);
  ASSERT_NE(comma, std::string::npos);
  const std::string x_str = line.substr(open_p + 2, comma - (open_p + 2));
  EXPECT_EQ(x_str.size(), 68u);
  EXPECT_EQ(x_str.substr(0, 7), "1606938");
  EXPECT_DOUBLE_EQ(std::stod(x_str), big);
  EXPECT_EQ(x_str.substr(x_str.size() - 7), ".000000");
}

TEST(IfcConformanceAndCurves, ControlCharactersEscapedAndNonFiniteRejected) {
  Scene ctrl_scene = make_mesh_scene("Wall\nOne\x7f", {0, 0, 0, 1, 0, 0, 0, 1, 0}, {0, 1, 2});
  ctrl_scene.mesh_index["Wall\nOne\x7f"].properties["note\tkey"] =
      std::string("line1\r\nline2\0\x1f\x7f", 15);

  const std::string text = to_ifc(ctrl_scene);
  EXPECT_NE(text.find("Wall\\X2\\000A\\X0\\One\\X2\\007F\\X0\\"), std::string::npos);
  EXPECT_NE(text.find("note\\X2\\0009\\X0\\key"), std::string::npos);
  EXPECT_NE(text.find("line1\\X2\\000D000A\\X0\\line2\\X2\\0000001F007F\\X0\\"), std::string::npos);

  std::istringstream ss(text);
  std::string line;
  bool in_data = false;
  while (std::getline(ss, line)) {
    if (!line.empty() && line.back() == '\r') line.pop_back();
    if (line == "DATA;") {
      in_data = true;
      continue;
    }
    if (in_data && line == "ENDSEC;") break;
    if (in_data) {
      ASSERT_FALSE(line.empty());
      EXPECT_EQ(line.front(), '#') << line;
      ASSERT_GE(line.size(), 2u);
      EXPECT_EQ(line.substr(line.size() - 2), ");") << line;
    }
  }

  const Scene good = make_mesh_scene("Wall", {0, 0, 0, 1, 0, 0, 0, 1, 0}, {0, 1, 2});
  EXPECT_THROW(to_ifc(good, std::numeric_limits<double>::quiet_NaN()), std::invalid_argument);
  EXPECT_THROW(to_ifc(good, std::numeric_limits<double>::infinity()), std::invalid_argument);

  const Scene nan_mesh = make_mesh_scene(
      "Wall", {std::numeric_limits<float>::quiet_NaN(), 0, 0, 1, 0, 0, 0, 1, 0}, {0, 1, 2});
  EXPECT_THROW(to_ifc(nan_mesh), std::invalid_argument);

  Scene inf_curve;
  inf_curve.scene_hierarchy.name = "Root";
  CurveSetMetadata bad_cs;
  bad_cs.name = "C";
  bad_cs.points_m = {{0.0, 0.0, 0.0}, {1e308, 0.0, 0.0}};
  inf_curve.curve_sets.push_back(bad_cs);
  EXPECT_THROW(to_ifc(inf_curve, 1000.0), std::invalid_argument);

  const auto tmp_dir =
      std::filesystem::temp_directory_path() / "openskp_ifc_nan_cpp_test" / "nested";
  const auto out_path = tmp_dir / "bad.ifc";
  std::filesystem::remove_all(tmp_dir.parent_path());
  EXPECT_THROW(export_ifc(nan_mesh, out_path), std::invalid_argument);
  EXPECT_FALSE(std::filesystem::exists(out_path));
  EXPECT_FALSE(std::filesystem::exists(tmp_dir));
}

}  // namespace
}  // namespace openskp
