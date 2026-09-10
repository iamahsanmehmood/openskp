#include "openskp/ifc_export.hpp"

#include <gtest/gtest.h>
#include <sstream>
#include <string>

namespace openskp {
namespace {

Scene create_mock_scene() {
  GlbPrimitive prim1;
  prim1.positions = {0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0};
  prim1.normals = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim1.uvs = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim1.indices = {0, 1, 2};
  prim1.material_index = 0;
  prim1.geom_name = "Outer Wall";

  GlbPrimitive prim2;
  prim2.positions = {2.0, 0.0, 0.0, 3.0, 0.0, 0.0, 2.0, 1.0, 0.0};
  prim2.normals = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim2.uvs = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim2.indices = {0, 1, 2};
  prim2.material_index = 1;
  prim2.geom_name = "Front Door";

  Scene scene;
  scene.scene_hierarchy.name = "Root";
  scene.glb_primitives = {prim1, prim2};

  GltfMaterial mat1;
  mat1.pbr_metallic_roughness.base_color_factor = {0.8, 0.2, 0.2, 1.0};
  GltfMaterial mat2;
  mat2.pbr_metallic_roughness.base_color_factor = {0.2, 0.8, 0.2, 0.9};
  scene.gltf_materials = {mat1, mat2};

  MeshMetadata wall;
  wall.name = "Outer Wall";
  wall.properties = {{"Thickness", "200mm"}, {"LoadBearing", "True"}};
  scene.mesh_index["Outer Wall"] = wall;

  MeshMetadata door;
  door.name = "Front Door";
  door.properties = {{"Material", "Wood"}};
  scene.mesh_index["Front Door"] = door;

  return scene;
}

TEST(IfcExport, GeneratesValid22CharGuid) {
  std::string guid = generate_ifc_guid();
  EXPECT_EQ(guid.length(), 22u);
}

TEST(IfcExport, ClassifiesElementNames) {
  EXPECT_EQ(classify_element("Main Wall").first, "IFCWALL");
  EXPECT_EQ(classify_element("Front Door").first, "IFCDOOR");
  EXPECT_EQ(classify_element("Office Window").first, "IFCWINDOW");
  EXPECT_EQ(classify_element("Concrete Slab").first, "IFCSLAB");
  EXPECT_EQ(classify_element("Steel Beam").first, "IFCBEAM");
}

TEST(IfcExport, ClassifyElementFallsBackToLayerNameWhenComponentNameHasNoKeyword) {
  // SketchUp default names carry no signal, but a BIM-style layer/tag
  // often does (openskp#238).
  EXPECT_EQ(classify_element("Component#109415", "Walls").first, "IFCWALL");
  EXPECT_EQ(classify_element("Group#3", "Doors").first, "IFCDOOR");
}

TEST(IfcExport, ClassifyElementPrefersComponentNameOverLayerName) {
  EXPECT_EQ(classify_element("Interior Door", "Walls").first, "IFCDOOR");
}

TEST(IfcExport, ClassifyElementFallsBackToGenericProxyWhenNeitherMatches) {
  EXPECT_EQ(classify_element("Component#109415", "Layer0").first, "IFCBUILDINGELEMENTPROXY");
  EXPECT_EQ(classify_element("Component#109415").first, "IFCBUILDINGELEMENTPROXY");
}

TEST(IfcExport, ToIfcUsesLayerNameFallbackForUnnamedComponents) {
  Scene scene;
  GlbPrimitive prim;
  prim.geom_name = "Component#109415";
  prim.material_index = 0;
  prim.positions = {0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0};
  prim.normals = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.uvs = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.indices = {0, 1, 2};
  scene.glb_primitives.push_back(prim);

  MeshMetadata meta;
  meta.name = "Component#109415";
  meta.layer = "Walls";
  scene.mesh_index["Component#109415"] = meta;

  std::string ifc_text = to_ifc(scene);
  EXPECT_NE(ifc_text.find("IFCWALL("), std::string::npos);
  EXPECT_EQ(ifc_text.find("IFCBUILDINGELEMENTPROXY"), std::string::npos);
}

TEST(IfcExport, ToIfcAcceptsCustomClassifierOverride) {
  Scene scene;
  GlbPrimitive prim;
  prim.geom_name = "Outer Wall";
  prim.material_index = 0;
  prim.positions = {0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0};
  prim.normals = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.uvs = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.indices = {0, 1, 2};
  scene.glb_primitives.push_back(prim);

  MeshMetadata meta;
  meta.name = "Outer Wall";
  scene.mesh_index["Outer Wall"] = meta;

  IfcClassifier always_column = [](const std::string&, const std::string&) {
    return std::pair<std::string, std::string>{"IFCCOLUMN", "IfcColumn"};
  };
  std::string ifc_text = to_ifc(scene, METRES_TO_INCHES, "IFC4", always_column);
  EXPECT_EQ(ifc_text.find("IFCWALL("), std::string::npos);
  EXPECT_NE(ifc_text.find("IFCCOLUMN("), std::string::npos);
}

TEST(IfcExport, SerializesSceneToIfc4StepText) {
  Scene scene;
  GlbPrimitive prim;
  prim.geom_name = "Outer Wall";
  prim.material_index = 0;
  prim.positions = {0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0};
  prim.normals = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.uvs = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.indices = {0, 1, 2};
  scene.glb_primitives.push_back(prim);

  MeshMetadata meta;
  meta.name = "Outer Wall";
  meta.properties["Thickness"] = "200mm";
  scene.mesh_index["Outer Wall"] = meta;

  std::string ifc_text = to_ifc(scene);
  EXPECT_NE(ifc_text.find("ISO-10303-21;"), std::string::npos);
  EXPECT_NE(ifc_text.find("HEADER;"), std::string::npos);
  EXPECT_NE(ifc_text.find("FILE_SCHEMA(('IFC4'));"), std::string::npos);
  EXPECT_NE(ifc_text.find("IFCPROJECT"), std::string::npos);
  EXPECT_NE(ifc_text.find("IFCSITE"), std::string::npos);
  EXPECT_NE(ifc_text.find("IFCBUILDING"), std::string::npos);
  EXPECT_NE(ifc_text.find("IFCBUILDINGSTOREY"), std::string::npos);
  EXPECT_NE(ifc_text.find("IFCWALL"), std::string::npos);
  EXPECT_NE(ifc_text.find("IFCTRIANGULATEDFACESET"), std::string::npos);
  EXPECT_NE(ifc_text.find("IFCCARTESIANPOINTLIST3D"), std::string::npos);
  EXPECT_NE(ifc_text.find("IFCPROPERTYSET"), std::string::npos);
  EXPECT_NE(ifc_text.find("ENDSEC;"), std::string::npos);
}

// The exporter always declares millimetres - the default scale has to
// actually produce millimetre-scaled values, or every coordinate reads
// back ~25.4x too small in any IFC consumer that respects the unit
// declaration (this exact bug, caught 2026-09-07 comparing against a
// real SketchUp IFC export of the same file).
TEST(IfcExport, ToIfcDeclaresMillimetresAndScalesToMatch) {
  Scene scene;
  scene.scene_hierarchy.name = "Root";
  GlbPrimitive prim;
  prim.positions = {0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0};
  prim.normals = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.uvs = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.indices = {0, 1, 2};
  prim.material_index = 0;
  prim.geom_name = "Test Triangle";
  scene.glb_primitives = {prim};
  MeshMetadata meta;
  meta.name = "Test Triangle";
  scene.mesh_index["Test Triangle"] = meta;
  GltfMaterial mat;
  mat.pbr_metallic_roughness.base_color_factor = {0.5, 0.5, 0.5, 1.0};
  scene.gltf_materials = {mat};

  std::string ifc_text = to_ifc(scene);

  EXPECT_NE(ifc_text.find("IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.)"), std::string::npos);
  // vertex 2 is glTF (1.0, 0.0, 0.0) = 1 metre along X, which maps
  // straight through to IFC X - millimetres means it must come out as
  // 1000.0, not ~39.37 (metres-to-inches, the old default).
  EXPECT_NE(ifc_text.find("(1000.000000,-0.000000,0.000000)"), std::string::npos);
}

// scene.glb_primitives positions are baked in glTF's Y-up convention
// (glTF.y = SketchUp Z/height, glTF.z = -SketchUp Y/depth) for GLB
// export - IFC (like SketchUp itself) is Z-up, so to_ifc must convert
// back rather than pass positions through raw, or the exported building
// comes out rotated ~90 degrees and mirrored (this exact bug, caught
// 2026-09-07 comparing against a real SketchUp IFC export of the same
// file).
TEST(IfcExport, ToIfcConvertsGltfYUpToIfcZUp) {
  Scene scene;
  scene.scene_hierarchy.name = "Root";
  GlbPrimitive prim;
  prim.positions = {0.0, 0.0, 0.0, 2.0, 3.0, 5.0, 0.0, 1.0, 0.0};
  prim.normals = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.uvs = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.indices = {0, 1, 2};
  prim.material_index = 0;
  prim.geom_name = "Test Triangle";
  scene.glb_primitives = {prim};
  MeshMetadata meta;
  meta.name = "Test Triangle";
  scene.mesh_index["Test Triangle"] = meta;
  GltfMaterial mat;
  mat.pbr_metallic_roughness.base_color_factor = {0.5, 0.5, 0.5, 1.0};
  scene.gltf_materials = {mat};

  std::string ifc_text = to_ifc(scene, /*scale=*/1.0);

  // glTF (2.0, 3.0, 5.0) is SketchUp (x=2.0, y=-5.0, z=3.0) - IFC/
  // SketchUp Z-up means that vertex must appear as (2.0,-5.0,3.0), not
  // the raw glTF-order (2.0,3.0,5.0).
  EXPECT_NE(ifc_text.find("(2.000000,-5.000000,3.000000)"), std::string::npos);
  EXPECT_EQ(ifc_text.find("(2.000000,3.000000,5.000000)"), std::string::npos);
}

// prim.geom_name is an internal lookup key (mesh index + hierarchy path +
// layer, e.g. "mesh_3_ROOT__W1_Layer0") - never a name a user should
// see. The IFC element's Name must come from MeshMetadata.name (the
// actual SketchUp instance name) instead (this exact bug, caught
// 2026-09-07 comparing against real IFC exports of the same file).
TEST(IfcExport, ToIfcUsesRealInstanceNameNotInternalKey) {
  Scene scene;
  scene.scene_hierarchy.name = "Root";
  GlbPrimitive prim;
  prim.positions = {0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0};
  prim.normals = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.uvs = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.indices = {0, 1, 2};
  prim.material_index = 0;
  prim.geom_name = "mesh_3_ROOT__W1_Layer0";
  scene.glb_primitives = {prim};
  MeshMetadata meta;
  meta.name = "W1";
  meta.layer = "Layer0";
  scene.mesh_index["mesh_3_ROOT__W1_Layer0"] = meta;
  GltfMaterial mat;
  mat.pbr_metallic_roughness.base_color_factor = {0.5, 0.5, 0.5, 1.0};
  scene.gltf_materials = {mat};

  std::string ifc_text = to_ifc(scene);

  EXPECT_NE(ifc_text.find("'W1'"), std::string::npos);
  EXPECT_EQ(ifc_text.find("mesh_3_ROOT"), std::string::npos);
}

// Only IFCPRESENTATIONLAYERWITHSTYLE (not the plain
// IFCPRESENTATIONLAYERASSIGNMENT this exporter used to write) carries a
// layer's visibility - LayerOn must match the source file's own
// hidden/visible state per layer, not just default to visible for
// everything.
TEST(IfcExport, ToIfcLayerOnReflectsSceneLayerHidden) {
  Scene scene = create_mock_scene();
  scene.mesh_index["Outer Wall"].layer = "Hidden Layer";
  scene.mesh_index["Front Door"].layer = "Visible Layer";
  scene.layer_hidden = {{"Hidden Layer", true}, {"Visible Layer", false}};

  std::string ifc_text = to_ifc(scene);

  EXPECT_NE(ifc_text.find("IFCPRESENTATIONLAYERWITHSTYLE"), std::string::npos);
  EXPECT_EQ(ifc_text.find("IFCPRESENTATIONLAYERASSIGNMENT("), std::string::npos);
  EXPECT_NE(ifc_text.find("'Hidden Layer',$,(#"), std::string::npos);

  std::istringstream lines(ifc_text);
  std::string line, hidden_line, visible_line;
  while (std::getline(lines, line)) {
    if (line.find("'Hidden Layer'") != std::string::npos) hidden_line = line;
    if (line.find("'Visible Layer'") != std::string::npos) visible_line = line;
  }
  EXPECT_NE(hidden_line.find(",.F.,.F.,.F.,())"), std::string::npos);
  EXPECT_NE(visible_line.find(",.T.,.F.,.F.,())"), std::string::npos);
}

// A third-party plugin's attribute dictionary (e.g. the steel-detailing
// "fbd-einfo" seen on the Keith Street file) must reach the exported IFC
// as its own named property set - separate from Pset_CustomProperties
// (which stays SketchUp's own Dynamic Components data), since a second
// source of properties commonly reuses key names like "name".
TEST(IfcExport, ToIfcWritesAPropertySetPerExtraAttributeDictionary) {
  Scene scene;
  scene.scene_hierarchy.name = "Root";
  GlbPrimitive prim;
  prim.positions = {0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0};
  prim.normals = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.uvs = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.indices = {0, 1, 2};
  prim.material_index = 0;
  prim.geom_name = "mesh_0_ROOT__Profile25_Layer0";
  scene.glb_primitives = {prim};

  MeshMetadata meta;
  meta.name = "Profile25";
  meta.properties = {{"width", "10.0"}};
  meta.attribute_dictionaries = {{"fbd-einfo", {{"code", "aPf"}, {"angle", "90"}}}};
  scene.mesh_index["mesh_0_ROOT__Profile25_Layer0"] = meta;
  GltfMaterial mat;
  mat.pbr_metallic_roughness.base_color_factor = {0.5, 0.5, 0.5, 1.0};
  scene.gltf_materials = {mat};

  std::string ifc_text = to_ifc(scene);

  EXPECT_NE(ifc_text.find("'Pset_CustomProperties'"), std::string::npos);
  EXPECT_NE(ifc_text.find("'Pset_fbd-einfo'"), std::string::npos);
  EXPECT_NE(ifc_text.find("'width'"), std::string::npos);
  EXPECT_NE(ifc_text.find("'code'"), std::string::npos);
  EXPECT_NE(ifc_text.find("'aPf'"), std::string::npos);
  EXPECT_NE(ifc_text.find("'angle'"), std::string::npos);
  EXPECT_NE(ifc_text.find("'90'"), std::string::npos);
}

TEST(IfcExport, ToIfcSkipsEmptyAttributeDictionaries) {
  Scene scene;
  scene.scene_hierarchy.name = "Root";
  GlbPrimitive prim;
  prim.positions = {0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0};
  prim.normals = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.uvs = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.indices = {0, 1, 2};
  prim.material_index = 0;
  prim.geom_name = "Test Triangle";
  scene.glb_primitives = {prim};

  MeshMetadata meta;
  meta.name = "Test Triangle";
  meta.attribute_dictionaries = {{"empty_dict", {}}};
  scene.mesh_index["Test Triangle"] = meta;
  GltfMaterial mat;
  mat.pbr_metallic_roughness.base_color_factor = {0.5, 0.5, 0.5, 1.0};
  scene.gltf_materials = {mat};

  std::string ifc_text = to_ifc(scene);

  EXPECT_EQ(ifc_text.find("Pset_empty_dict"), std::string::npos);
}

// #272's naming fix stopped classify_element() from matching keywords
// against the internal mangled path string (a bug: it also corrupted the
// displayed Name). That must stay untyped by default, and only match
// when classify_using_full_path is set - the broader, noisier fallback
// callers can opt into.
TEST(IfcExport, ClassifyElementFullPathOnlyWhenOptedIn) {
  const std::string path = "ROOT / Wall Frame / Stud 12";
  EXPECT_EQ(classify_element("Stud 12", "Layer0", path).first, "IFCBUILDINGELEMENTPROXY");
  EXPECT_EQ(classify_element("Stud 12", "Layer0", path, /*classify_using_full_path=*/true).first,
            "IFCWALL");
}

// The path fallback only kicks in once name and layer both miss - it
// must never override a real, specific match.
TEST(IfcExport, ClassifyElementFullPathStillPrefersNameAndLayer) {
  const std::string path = "ROOT / Wall Frame / Front Door";
  auto result = classify_element("Front Door", "Layer0", path, /*classify_using_full_path=*/true);
  EXPECT_EQ(result.first, "IFCDOOR");
}

TEST(IfcExport, ToIfcClassifyUsingFullPathOptIn) {
  Scene scene;
  scene.scene_hierarchy.name = "Root";
  GlbPrimitive prim;
  prim.positions = {0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0};
  prim.normals = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.uvs = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.indices = {0, 1, 2};
  prim.material_index = 0;
  prim.geom_name = "mesh_0_ROOT__Wall_Frame__Stud_12_Layer0";
  scene.glb_primitives = {prim};

  MeshMetadata meta;
  meta.name = "Stud 12";
  meta.layer = "Layer0";
  meta.path = "ROOT / Wall Frame / Stud 12";
  scene.mesh_index["mesh_0_ROOT__Wall_Frame__Stud_12_Layer0"] = meta;
  scene.gltf_materials = {GltfMaterial{}};

  std::string default_text = to_ifc(scene);
  EXPECT_NE(default_text.find("IFCBUILDINGELEMENTPROXY"), std::string::npos);
  EXPECT_EQ(default_text.find("IFCWALL("), std::string::npos);

  std::string opted_in_text =
      to_ifc(scene, METRES_TO_MM, "IFC4", nullptr, /*classify_using_full_path=*/true);
  EXPECT_NE(opted_in_text.find("IFCWALL("), std::string::npos);
  // the real, clean name is still what's shown - opting into the broader
  // classification fallback doesn't reintroduce the old mangled-name-
  // as-Name bug.
  EXPECT_NE(opted_in_text.find("'Stud 12'"), std::string::npos);
}

}  // namespace
}  // namespace openskp
