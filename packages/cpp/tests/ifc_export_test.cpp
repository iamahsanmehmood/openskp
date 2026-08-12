#include "openskp/ifc_export.hpp"

#include <gtest/gtest.h>

namespace openskp {
namespace {

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

}  // namespace
}  // namespace openskp
