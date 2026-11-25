#include "openskp/dxf_export.hpp"

#include <filesystem>
#include <gtest/gtest.h>

#include "openskp/create.hpp"
#include "openskp/openskp.hpp"

namespace openskp {
namespace {

TEST(DxfExport, SerializesSceneTo3DxfText) {
  Scene scene;
  GlbPrimitive prim;
  prim.geom_name = "Walls";
  prim.material_index = 0;
  prim.positions = {0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0};
  prim.normals = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.uvs = {0.0, 0.0, 1.0, 0.0, 0.0, 1.0};
  prim.indices = {0, 1, 2};
  scene.glb_primitives.push_back(prim);

  std::string dxf_text = to_dxf(scene);
  EXPECT_NE(dxf_text.find("$ACADVER"), std::string::npos);
  EXPECT_NE(dxf_text.find("AC1015"), std::string::npos);
  EXPECT_NE(dxf_text.find("POLYLINE"), std::string::npos);
  EXPECT_NE(dxf_text.find("AcDbPolyFaceMesh"), std::string::npos);
  EXPECT_NE(dxf_text.find("Walls"), std::string::npos);
  EXPECT_NE(dxf_text.find("EOF"), std::string::npos);

  std::string dxf_3d = to_dxf(scene, METRES_TO_INCHES, "3dface");
  EXPECT_NE(dxf_3d.find("3DFACE"), std::string::npos);
  EXPECT_NE(dxf_3d.find("AcDbFace"), std::string::npos);
}

TEST(DxfExport, EveryExportTakesAFileNameWithoutAFolder) {
  const auto skp = create();
  skp->add_face({{0, 0, 0}, {10, 0, 0}, {10, 10, 0}});
  const auto folder = std::filesystem::temp_directory_path() / "openskp_bare_file_names";
  std::filesystem::create_directories(folder);
  const auto previous = std::filesystem::current_path();
  std::filesystem::current_path(folder);
  EXPECT_NO_THROW({
    skp->save("bare.skp");
    const auto file = SkpFile::open("bare.skp");
    const auto scene = file.build_scene();
    export_dxf(scene, "bare.dxf");
    export_fragments(file.build_instanced_scene(), "bare.frag");
    export_glb(scene, "bare.glb");
    export_ifc(scene, "bare.ifc");
    export_obj(scene, "bare.obj");
    export_ply(scene, "bare.ply");
    export_stl(scene, "bare.stl");
  });
  for (const char* name :
       {"bare.dxf", "bare.frag", "bare.glb", "bare.ifc", "bare.obj", "bare.ply", "bare.stl"}) {
    EXPECT_TRUE(std::filesystem::exists(name)) << name;
  }
  std::filesystem::current_path(previous);
  std::filesystem::remove_all(folder);
}

}  // namespace
}  // namespace openskp
