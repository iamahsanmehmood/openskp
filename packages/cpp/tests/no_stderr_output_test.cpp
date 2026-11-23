#include <array>
#include <cstdint>
#include <gtest/gtest.h>
#include <string>
#include <utility>
#include <vector>

#include <openskp/_fragments_fb/index_generated.h>
#include <openskp/fragments_export.hpp>
#include <openskp/openskp.hpp>

// Ensure openskp never writes to stdout or stderr when a warning is produced
// with no logging configured (#556).

namespace openskp {
namespace {

constexpr std::array<double, 16> kIdentity{
    1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1,
};

std::vector<std::uint8_t> make_circle_extrusion_frag() {
  InstancedMeshResource r;
  r.id = "mesh_0";
  r.definition_id = 1;
  r.definition_name = "Box";
  r.variant_key = "1|255,255,255";
  LocalPrimitive prim;
  prim.positions = {0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0};
  prim.normals.assign(12, 0.0f);
  prim.uvs.assign(8, 0.0f);
  prim.indices = {0, 1, 2, 0, 2, 3};
  prim.material_index = 0;
  r.primitives = {prim};

  InstancedNode leaf;
  leaf.name = "Box1";
  leaf.matrix = kIdentity;
  leaf.mesh_resource_id = "mesh_0";

  InstancedNode root;
  root.name = "ROOT";
  root.matrix = kIdentity;
  root.children = {leaf};

  InstancedScene scene;
  scene.mesh_resources = {r};
  scene.gltf_materials = {{}};
  scene.scene_hierarchy = root;

  auto raw = to_fragments(scene, /*raw=*/true);
  const auto* model = fragments_fb::GetModel(raw.data());
  const auto* rep = model->meshes()->representations()->Get(0);
  const auto offset =
      static_cast<std::size_t>(reinterpret_cast<const std::uint8_t*>(rep) - raw.data());
  raw[offset + 28] = static_cast<std::uint8_t>(fragments_fb::RepresentationClass_CIRCLE_EXTRUSION);
  return raw;
}

TEST(NoStderrOutput, FromFragmentsWritesNothingToStdoutOrStderrWithoutLogger) {
  const auto patched = make_circle_extrusion_frag();
  testing::internal::CaptureStdout();
  testing::internal::CaptureStderr();
  const auto back = from_fragments(patched);
  const auto out = testing::internal::GetCapturedStdout();
  const auto err = testing::internal::GetCapturedStderr();
  for (const auto& r : back.mesh_resources) {
    EXPECT_TRUE(r.primitives.empty());
  }
  EXPECT_EQ(out, "");
  EXPECT_EQ(err, "");
}

TEST(NoStderrOutput, FromFragmentsDeliversWarningsThroughParseOptions) {
  const auto patched = make_circle_extrusion_frag();
  std::vector<std::pair<LogLevel, std::string>> seen;
  ParseOptions opts;
  opts.log = [&](LogLevel level, std::string_view msg) {
    seen.emplace_back(level, std::string(msg));
  };
  const auto back = from_fragments(patched, opts);
  for (const auto& r : back.mesh_resources) {
    EXPECT_TRUE(r.primitives.empty());
  }
  bool matched = false;
  for (const auto& [level, msg] : seen) {
    if (level == LogLevel::warning && msg.find("RepresentationClass") != std::string::npos) {
      matched = true;
    }
  }
  EXPECT_TRUE(matched);
}

}  // namespace
}  // namespace openskp
