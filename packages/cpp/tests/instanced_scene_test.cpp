#include <algorithm>
#include <array>
#include <cmath>
#include <functional>
#include <gtest/gtest.h>
#include <map>
#include <string>
#include <vector>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

// Instanced scene building (openskp#200, ported from TypeScript's
// buildInstancedScene()/toInstancedGLB()).
//
// The strongest correctness evidence available: run BOTH builders over the
// repository's real .skp fixtures and require that flattening the
// instanced result reproduces the baked builder's world-space triangles
// exactly. This covers, on genuine files, everything a synthetic test
// would cover piecewise - nested groups/components, instance-painted
// materials, layers, front/back materials, textures, holes, mirrored
// transforms - because whatever those files happen to contain has to come
// out the same either way.

namespace openskp {
namespace {

// Float32 round-off only, same tolerance and justification as the
// TypeScript reference: the baked path transforms in float64 then stores
// the world-space result as float32; the instanced path stores the
// local-space value as float32 and transforms afterwards. Both are
// single-rounding-step correct, but round at different moments, so a
// coordinate can land one float32 ulp apart between them.
constexpr double kTolerance = 1e-5;

std::array<double, 16> mul4(const std::array<double, 16>& a, const std::array<double, 16>& b) {
  std::array<double, 16> out{};
  for (int col = 0; col < 4; ++col) {
    for (int row = 0; row < 4; ++row) {
      double s = 0;
      for (int k = 0; k < 4; ++k) s += a[k * 4 + row] * b[col * 4 + k];
      out[col * 4 + row] = s;
    }
  }
  return out;
}

std::array<double, 3> apply_matrix(const std::array<double, 16>& m, const std::array<float, 3>& p) {
  return {
      m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12],
      m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13],
      m[2] * p[0] + m[6] * p[1] + m[10] * p[2] + m[14],
  };
}

constexpr std::array<double, 16> kIdentity4{
    1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1,
};

struct FlatTri {
  std::array<double, 3> a, b, c;
  std::size_t material_index;
};

// Walk the instanced tree, composing node transforms, and emit every
// triangle in world space - i.e. reconstruct what build_scene() bakes.
// Test-only: the whole point of the instanced output is to avoid
// materialising this.
std::vector<FlatTri> flatten_instanced(const InstancedScene& scene) {
  std::map<std::string, const InstancedMeshResource*> by_id;
  for (auto& r : scene.mesh_resources) by_id[r.id] = &r;

  std::vector<FlatTri> out;
  std::function<void(const InstancedNode&, const std::array<double, 16>&)> visit;
  visit = [&](const InstancedNode& node, const std::array<double, 16>& parent) {
    const auto world = mul4(parent, node.matrix);
    if (node.mesh_resource_id) {
      auto found = by_id.find(*node.mesh_resource_id);
      if (found != by_id.end()) {
        for (auto& prim : found->second->primitives) {
          for (std::size_t i = 0; i + 2 < prim.indices.size(); i += 3) {
            std::array<std::array<double, 3>, 3> tri{};
            for (int k = 0; k < 3; ++k) {
              const auto vi = prim.indices[i + static_cast<std::size_t>(k)];
              tri[static_cast<std::size_t>(k)] = apply_matrix(
                  world,
                  {prim.positions[vi * 3], prim.positions[vi * 3 + 1], prim.positions[vi * 3 + 2]});
            }
            out.push_back(FlatTri{tri[0], tri[1], tri[2], prim.material_index});
          }
        }
      }
    }
    for (auto& child : node.children) visit(child, world);
  };
  visit(scene.scene_hierarchy, kIdentity4);
  return out;
}

std::vector<FlatTri> flatten_baked(const Scene& scene) {
  std::vector<FlatTri> out;
  for (auto& prim : scene.glb_primitives) {
    for (std::size_t i = 0; i + 2 < prim.indices.size(); i += 3) {
      std::array<std::array<double, 3>, 3> tri{};
      for (int k = 0; k < 3; ++k) {
        const auto vi = prim.indices[i + static_cast<std::size_t>(k)];
        tri[static_cast<std::size_t>(k)] = {prim.positions[vi * 3], prim.positions[vi * 3 + 1],
                                            prim.positions[vi * 3 + 2]};
      }
      out.push_back(FlatTri{tri[0], tri[1], tri[2], prim.material_index});
    }
  }
  return out;
}

std::size_t instanced_buffer_bytes(const InstancedScene& scene) {
  std::size_t total = 0;
  for (auto& r : scene.mesh_resources) {
    for (auto& p : r.primitives) {
      total +=
          p.positions.size() * 4 + p.normals.size() * 4 + p.uvs.size() * 4 + p.indices.size() * 4;
    }
  }
  return total;
}

std::size_t baked_buffer_bytes(const Scene& scene) {
  std::size_t total = 0;
  for (auto& p : scene.glb_primitives) {
    total +=
        p.positions.size() * 4 + p.normals.size() * 4 + p.uvs.size() * 4 + p.indices.size() * 4;
  }
  return total;
}

bool materials_equal(const GltfMaterial& a, const GltfMaterial& b) {
  const auto& pa = a.pbr_metallic_roughness;
  const auto& pb = b.pbr_metallic_roughness;
  if (pa.base_color_factor != pb.base_color_factor) return false;
  if (pa.metallic_factor != pb.metallic_factor) return false;
  if (pa.roughness_factor != pb.roughness_factor) return false;
  if (pa.base_color_texture.has_value() != pb.base_color_texture.has_value()) return false;
  if (a.double_sided != b.double_sided) return false;
  return true;
}

void walk_metadata_parity(const InstanceNode& baked, const InstancedNode& instanced) {
  EXPECT_EQ(instanced.name, baked.name);
  EXPECT_EQ(instanced.definition_name, baked.definition_name);
  EXPECT_EQ(instanced.layer, baked.layer);
  EXPECT_EQ(instanced.position_mm, baked.position_mm);
  EXPECT_EQ(instanced.properties, baked.properties);
  ASSERT_EQ(instanced.children.size(), baked.children.size());
  for (std::size_t k = 0; k < baked.children.size(); ++k) {
    walk_metadata_parity(baked.children[k], instanced.children[k]);
  }
}

const std::vector<std::string> kFixtures = {
    "SU_File.skp",
    "Untitled.skp",
    "capilla_quiroz_v17.skp",
    "gondola_v20.skp",
    "single_material_v17.skp",
};

class InstancedSceneParity : public ::testing::TestWithParam<std::string> {};

TEST_P(InstancedSceneParity, ReproducesBuildScenesWorldSpaceTriangles) {
  const auto path = test::fixture(GetParam().c_str());
  const auto baked = SkpFile::open(path).build_scene();
  const auto instanced = SkpFile::open(path).build_instanced_scene();

  const auto baked_tris = flatten_baked(baked);
  const auto instanced_tris = flatten_instanced(instanced);

  ASSERT_EQ(instanced_tris.size(), baked_tris.size());

  double worst_delta = 0;
  std::size_t material_mismatches = 0;
  for (std::size_t i = 0; i < baked_tris.size(); ++i) {
    const auto& a = instanced_tris[i];
    const auto& e = baked_tris[i];
    if (!materials_equal(instanced.gltf_materials[a.material_index],
                         baked.gltf_materials[e.material_index])) {
      ++material_mismatches;
    }
    for (const auto& pair : {std::pair{a.a, e.a}, std::pair{a.b, e.b}, std::pair{a.c, e.c}}) {
      for (int k = 0; k < 3; ++k) {
        worst_delta = std::max(worst_delta, std::abs(pair.first[static_cast<std::size_t>(k)] -
                                                     pair.second[static_cast<std::size_t>(k)]));
      }
    }
  }

  EXPECT_EQ(material_mismatches, 0u);
  EXPECT_LT(worst_delta, kTolerance);
}

TEST_P(InstancedSceneParity, NeverStoresMoreGeometryThanTheBakedPath) {
  const auto path = test::fixture(GetParam().c_str());
  const auto baked_bytes = baked_buffer_bytes(SkpFile::open(path).build_scene());
  const auto instanced_bytes = instanced_buffer_bytes(SkpFile::open(path).build_instanced_scene());
  // Equal when nothing repeats; strictly smaller once anything does.
  EXPECT_LE(instanced_bytes, baked_bytes);
}

TEST_P(InstancedSceneParity, ResolvesTheSameLayersAndDynamicPropertiesPerNode) {
  const auto path = test::fixture(GetParam().c_str());
  const auto baked = SkpFile::open(path).build_scene();
  const auto instanced = SkpFile::open(path).build_instanced_scene();
  walk_metadata_parity(baked.scene_hierarchy, instanced.scene_hierarchy);
}

INSTANTIATE_TEST_SUITE_P(RealFixtures, InstancedSceneParity, ::testing::ValuesIn(kFixtures));

}  // namespace
}  // namespace openskp
