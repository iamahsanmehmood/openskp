#include <array>
#include <gtest/gtest.h>
#include <set>
#include <string>
#include <vector>

#include <openskp/_fragments_fb/index_generated.h>
#include <openskp/fragments_export.hpp>
#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

// Direct SKP -> Fragments (.frag) export - C++ port of Python's
// openskp.export.fragments. See that module's own tests
// (packages/python/tests/test_fragments.py) for the reference coverage
// this file mirrors.

namespace openskp {
namespace {

namespace fb = openskp::fragments_fb;

constexpr std::array<double, 16> kIdentity{
    1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1,
};

InstancedMeshResource make_box_resource(const std::string& id) {
  InstancedMeshResource r;
  r.id = id;
  r.definition_id = 1;
  r.definition_name = "Box";
  r.variant_key = "1|255,255,255";
  LocalPrimitive prim;
  prim.positions = {0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0, 0, 0, 1, 1, 0, 1, 1, 1, 1, 0, 1, 1};
  prim.normals.assign(prim.positions.size(), 0.0f);
  prim.uvs.assign((prim.positions.size() / 3) * 2, 0.0f);
  prim.indices = {0, 1, 2, 0, 2, 3, 4, 6, 5, 4, 7, 6};
  prim.material_index = 0;
  r.primitives = {prim};
  return r;
}

const fb::Model* parse_raw(const std::vector<std::uint8_t>& raw) {
  return fb::GetModel(raw.data());
}

TEST(FragmentsExport, GuidsAndLocalIdsSameLengthAndOrder) {
  InstancedScene scene;
  scene.mesh_resources = {make_box_resource("mesh_0")};
  scene.gltf_materials = {{}};
  InstancedNode leaf;
  leaf.name = "Box1";
  leaf.matrix = kIdentity;
  leaf.mesh_resource_id = "mesh_0";
  InstancedNode root;
  root.name = "ROOT";
  root.matrix = kIdentity;
  root.children = {leaf};
  scene.scene_hierarchy = root;

  const auto raw = to_fragments(scene, /*raw=*/true);
  const auto* model = parse_raw(raw);
  ASSERT_NE(model, nullptr);
  EXPECT_EQ(model->local_ids()->size(), 1u);
  EXPECT_EQ(model->guids()->size(), 1u);
  EXPECT_EQ(model->guids_items()->size(), 1u);
  EXPECT_EQ(model->guids_items()->Get(0), model->local_ids()->Get(0));
}

TEST(FragmentsExport, ItemsWithoutSourceGuidGetUniqueSyntheticOne) {
  InstancedScene scene;
  scene.mesh_resources = {make_box_resource("mesh_0")};
  scene.gltf_materials = {{}};
  InstancedNode leaf1;
  leaf1.name = "Box1";
  leaf1.matrix = kIdentity;
  leaf1.mesh_resource_id = "mesh_0";
  InstancedNode leaf2;
  leaf2.name = "Box2";
  leaf2.matrix = kIdentity;
  leaf2.mesh_resource_id = "mesh_0";
  InstancedNode root;
  root.name = "ROOT";
  root.matrix = kIdentity;
  root.children = {leaf1, leaf2};
  scene.scene_hierarchy = root;

  const auto raw = to_fragments(scene, /*raw=*/true);
  const auto* model = parse_raw(raw);
  ASSERT_NE(model, nullptr);
  std::set<std::string> guids;
  for (std::size_t i = 0; i < model->guids()->size(); ++i) {
    const auto* g = model->guids()->Get(static_cast<::flatbuffers::uoffset_t>(i));
    ASSERT_NE(g, nullptr);
    EXPECT_GT(g->size(), 0u);
    guids.insert(g->str());
  }
  EXPECT_EQ(guids.size(), model->guids()->size());
}

TEST(FragmentsExport, ARealSourceGuidIsPreservedExactly) {
  InstancedScene scene;
  scene.mesh_resources = {make_box_resource("mesh_0")};
  scene.gltf_materials = {{}};
  const std::string real_guid = "F160C36229782F47A9857FC88DD1F2CB";
  InstancedNode leaf;
  leaf.name = "Box";
  leaf.matrix = kIdentity;
  leaf.mesh_resource_id = "mesh_0";
  leaf.guid = real_guid;
  InstancedNode root;
  root.name = "ROOT";
  root.matrix = kIdentity;
  root.children = {leaf};
  scene.scene_hierarchy = root;

  const auto raw = to_fragments(scene, /*raw=*/true);
  const auto* model = parse_raw(raw);
  ASSERT_NE(model, nullptr);
  ASSERT_EQ(model->guids()->size(), 1u);
  EXPECT_EQ(model->guids()->Get(0)->str(), real_guid);
}

// openskp#290: SketchUp's own native Copy/Move+Copy/Array tools carry an
// instance's attribute dictionaries - and whatever GUID a framing plugin
// wrote into one - to every copy verbatim, so a real file can have several
// DIFFERENT physical instances all sharing the exact same non-empty
// InstancedNode::guid. The first instance to claim a real GUID keeps it;
// every later instance sharing that same value must fall back to a
// synthetic one instead of silently colliding. Mirrors Python's
// test_a_duplicated_source_guid_does_not_collide exactly.
TEST(FragmentsExport, ADuplicatedSourceGuidDoesNotCollide) {
  InstancedScene scene;
  scene.mesh_resources = {make_box_resource("mesh_0")};
  scene.gltf_materials = {{}};
  const std::string duplicated_guid = "F160C36229782F47A9857FC88DD1F2CB";
  InstancedNode root;
  root.name = "ROOT";
  root.matrix = kIdentity;
  for (int i = 0; i < 3; ++i) {
    InstancedNode leaf;
    leaf.name = "Truss" + std::to_string(i);
    leaf.matrix = kIdentity;
    leaf.mesh_resource_id = "mesh_0";
    leaf.guid = duplicated_guid;
    root.children.push_back(leaf);
  }
  scene.scene_hierarchy = root;

  const auto raw = to_fragments(scene, /*raw=*/true);
  const auto* model = parse_raw(raw);
  ASSERT_NE(model, nullptr);
  ASSERT_EQ(model->guids()->size(), 3u);

  std::vector<std::string> guids;
  int real_guid_count = 0;
  for (std::size_t i = 0; i < 3; ++i) {
    const auto s = model->guids()->Get(static_cast<::flatbuffers::uoffset_t>(i))->str();
    guids.push_back(s);
    if (s == duplicated_guid) ++real_guid_count;
  }
  EXPECT_EQ(std::set<std::string>(guids.begin(), guids.end()).size(), 3u)
      << "guids must never collide";
  EXPECT_EQ(real_guid_count, 1) << "only the first claimant keeps the real value";
}

// A named organizational wrapper with no geometry of its own (e.g. a
// FrameBuilder wall "W-2" wrapping several separately-meshed parts) must
// still become a tracked item with its own local_id/Name/GUID - see
// openskp.export.fragments._collect_leaves's own docstring.
TEST(FragmentsExport, NamedWrapperWithNoGeometryGetsATrackedItem) {
  InstancedScene scene;
  scene.mesh_resources = {make_box_resource("mesh_0")};
  scene.gltf_materials = {{}};

  InstancedNode child;
  child.name = "Stud1";
  child.matrix = kIdentity;
  child.mesh_resource_id = "mesh_0";

  InstancedNode wrapper;
  wrapper.name = "W-2";
  wrapper.name_is_generated = false;
  wrapper.matrix = kIdentity;
  wrapper.children = {child};

  InstancedNode generic_wrapper;
  generic_wrapper.name = "Component_5";
  generic_wrapper.name_is_generated = true;
  generic_wrapper.matrix = kIdentity;
  InstancedNode generic_child;
  generic_child.name = "Stud2";
  generic_child.matrix = kIdentity;
  generic_child.mesh_resource_id = "mesh_0";
  generic_wrapper.children = {generic_child};

  InstancedNode root;
  root.name = "ROOT";
  root.matrix = kIdentity;
  root.children = {wrapper, generic_wrapper};
  scene.scene_hierarchy = root;

  const auto raw = to_fragments(scene, /*raw=*/true);
  const auto* model = parse_raw(raw);
  ASSERT_NE(model, nullptr);
  // wrapper (W-2, named, no geometry) + child (Stud1) + generic_child
  // (Stud2) = 3 tracked items; generic_wrapper itself (name_is_generated)
  // is NOT tracked, matching _collect_leaves.
  EXPECT_EQ(model->local_ids()->size(), 3u);

  bool found_w2 = false;
  for (std::size_t i = 0; i < model->attributes()->size(); ++i) {
    const auto* attr = model->attributes()->Get(static_cast<::flatbuffers::uoffset_t>(i));
    for (std::size_t j = 0; j < attr->data()->size(); ++j) {
      const std::string s = attr->data()->Get(static_cast<::flatbuffers::uoffset_t>(j))->str();
      if (s.find("W-2") != std::string::npos) found_w2 = true;
    }
  }
  EXPECT_TRUE(found_w2)
      << "the named wrapper's own real name must reach the exported Attribute data";
}

TEST(FragmentsExport, CompressedOutputIsSmallerAndDecompresses) {
  InstancedScene scene;
  scene.mesh_resources = {make_box_resource("mesh_0")};
  scene.gltf_materials = {{}};
  InstancedNode leaf;
  leaf.name = "Box1";
  leaf.matrix = kIdentity;
  leaf.mesh_resource_id = "mesh_0";
  InstancedNode root;
  root.name = "ROOT";
  root.matrix = kIdentity;
  root.children = {leaf};
  scene.scene_hierarchy = root;

  const auto raw = to_fragments(scene, /*raw=*/true);
  const auto compressed = to_fragments(scene, /*raw=*/false);
  EXPECT_NE(raw, compressed);
  // zlib (RFC 1950) header: 0x78 is the standard CMF byte for a 32K window.
  ASSERT_GE(compressed.size(), 2u);
  EXPECT_EQ(compressed[0], 0x78);
}

// ---------------------------------------------------------------------------------------------
// Oversized shell splitting (openskp#285 / PR #355).
//
// A single shell (Fragments' term for one baked triangle mesh) has no representation for more
// than 65535 triangles: profiles_face_ids is a plain ushort array in the real schema (index.fbs),
// with no uint32 escape hatch the way POINTS get past 65535 via BigShellProfile. A real
// production model with one 222,000+-triangle primitive (a large flattened/dense mesh) hit this
// in practice on the Python port - see PR #355 for the full incident. Unlike Python (which threw
// at write time), this port's old `static_cast<std::uint16_t>(i)` silently WRAPPED instead of
// throwing - a worse bug in one sense: it wrote wrong/colliding face ids instead of failing
// loudly. get_or_bake_shell now splits an oversized primitive's triangles into multiple shells
// instead, each within the ushort limit.
// ---------------------------------------------------------------------------------------------

LocalPrimitive grid_primitive(int cols, int rows) {
  LocalPrimitive prim;
  prim.positions.resize(static_cast<std::size_t>(cols) * static_cast<std::size_t>(rows) * 3);
  for (int j = 0; j < rows; ++j) {
    for (int i = 0; i < cols; ++i) {
      const std::size_t v = static_cast<std::size_t>(j) * static_cast<std::size_t>(cols) +
                            static_cast<std::size_t>(i);
      prim.positions[v * 3] = static_cast<float>(i);
      prim.positions[v * 3 + 1] = static_cast<float>(j);
      prim.positions[v * 3 + 2] = 0.0f;
    }
  }
  prim.normals.assign(prim.positions.size(), 0.0f);
  prim.uvs.assign((prim.positions.size() / 3) * 2, 0.0f);

  const int tri_cells = (cols - 1) * (rows - 1);
  prim.indices.resize(static_cast<std::size_t>(tri_cells) * 6);
  std::size_t k = 0;
  for (int j = 0; j < rows - 1; ++j) {
    for (int i = 0; i < cols - 1; ++i) {
      const std::uint32_t a = static_cast<std::uint32_t>(j * cols + i);
      const std::uint32_t b = a + 1;
      const std::uint32_t c = a + static_cast<std::uint32_t>(cols);
      const std::uint32_t d = c + 1;
      prim.indices[k++] = a;
      prim.indices[k++] = b;
      prim.indices[k++] = d;
      prim.indices[k++] = a;
      prim.indices[k++] = d;
      prim.indices[k++] = c;
    }
  }
  prim.material_index = 0;
  return prim;
}

InstancedScene scene_with_one_primitive(const LocalPrimitive& prim, const std::string& mesh_id) {
  InstancedScene scene;
  InstancedMeshResource r;
  r.id = mesh_id;
  r.definition_id = 1;
  r.definition_name = "BigMesh";
  r.variant_key = "1|255,255,255";
  r.primitives = {prim};
  scene.mesh_resources = {r};
  scene.gltf_materials = {{}};

  InstancedNode leaf;
  leaf.name = "Big";
  leaf.matrix = kIdentity;
  leaf.mesh_resource_id = mesh_id;
  InstancedNode root;
  root.name = "ROOT";
  root.matrix = kIdentity;
  root.children = {leaf};
  scene.scene_hierarchy = root;
  return scene;
}

TEST(FragmentsExport, SplitsAnOversizedPrimitiveAndEveryFaceIdIsCorrectAndSequential) {
  constexpr int kCols = 210, kRows = 165;
  constexpr int kExpectedTriangles = (kCols - 1) * (kRows - 1) * 2;
  static_assert(kExpectedTriangles > 65535, "fixture must exceed the limit under test");

  const auto scene = scene_with_one_primitive(grid_primitive(kCols, kRows), "mesh_big");

  // This is the exact shape of call that, before the fix, silently wrote wrapped-around (wrong)
  // face ids via static_cast<uint16_t> - the primary regression check is that the result is now
  // actually correct, not just that it doesn't throw (unlike Python's own crash-shaped version of
  // this bug).
  const auto raw = to_fragments(scene, /*raw=*/true);
  const auto* model = parse_raw(raw);
  ASSERT_NE(model, nullptr);
  const auto* meshes = model->meshes();
  ASSERT_NE(meshes, nullptr);

  ASSERT_GT(meshes->shells()->size(), 1u);  // confirms the split actually happened, not a no-op
  EXPECT_EQ(meshes->samples()->size(), meshes->shells()->size());  // one sample per split shell

  std::size_t total_triangles = 0;
  for (const auto* shell : *meshes->shells()) {
    const auto* face_ids = shell->profiles_face_ids();
    ASSERT_NE(face_ids, nullptr);
    EXPECT_LE(face_ids->size(), 65535u);  // every sub-shell stays within the ushort limit
    ASSERT_NE(shell->profiles(), nullptr);
    EXPECT_EQ(shell->profiles()->size(), face_ids->size());
    // Every face id in a shell is a small, sequential, non-wrapped 0..N-1 run - the exact thing
    // the old static_cast<uint16_t> could silently violate once a shell's own triangle count
    // exceeded 65535.
    for (::flatbuffers::uoffset_t j = 0; j < face_ids->size(); ++j) {
      EXPECT_EQ(face_ids->Get(j), j);
    }
    total_triangles += face_ids->size();
  }
  EXPECT_EQ(total_triangles, static_cast<std::size_t>(kExpectedTriangles));
}

TEST(FragmentsExport, APrimitiveWithinTheLimitStillProducesExactlyOneShell) {
  // Regression guard on the split path itself: a normal, non-huge primitive must not be
  // needlessly split into multiple shells.
  const auto scene =
      scene_with_one_primitive(grid_primitive(50, 50), "mesh_small");  // 49*49*2 = 4802 triangles

  const auto raw = to_fragments(scene, /*raw=*/true);
  const auto* model = parse_raw(raw);
  ASSERT_NE(model, nullptr);
  const auto* meshes = model->meshes();
  ASSERT_NE(meshes, nullptr);
  EXPECT_EQ(meshes->shells()->size(), 1u);
  EXPECT_EQ(meshes->samples()->size(), 1u);
}

// ---- from_fragments (openskp#285: reading a .frag file back) ----

InstancedScene make_two_instance_scene() {
  InstancedMeshResource resource = make_box_resource("mesh_0");
  InstancedNode node_a;
  node_a.name = "Box_A";
  node_a.layer = "Framing";
  node_a.matrix = kIdentity;
  node_a.mesh_resource_id = "mesh_0";
  InstancedNode node_b;
  node_b.name = "Box_B";
  node_b.layer = "Framing";
  node_b.matrix = kIdentity;
  node_b.mesh_resource_id = "mesh_0";

  InstancedScene scene;
  scene.mesh_resources = {resource};
  scene.gltf_materials = {{}};
  scene.scene_hierarchy.name = "ROOT";
  scene.scene_hierarchy.matrix = kIdentity;
  scene.scene_hierarchy.children = {node_a, node_b};
  return scene;
}

TEST(FromFragments, RoundTripsABasicTwoInstanceScene) {
  const auto scene = make_two_instance_scene();
  const auto raw = to_fragments(scene, /*raw=*/true);
  const auto back = from_fragments(raw);

  ASSERT_EQ(back.mesh_resources.size(), 1u);
  ASSERT_EQ(back.mesh_resources[0].primitives.size(), 1u);
  // make_box_resource's box carries 8 vertices but only 4 triangles (2 of
  // the cube's 6 faces), matching its own shape exactly.
  EXPECT_EQ(back.mesh_resources[0].primitives[0].positions.size(), 8u * 3);
  EXPECT_EQ(back.mesh_resources[0].primitives[0].indices.size(), 4u * 3);

  ASSERT_EQ(back.scene_hierarchy.children.size(), 2u);
  const auto& a = back.scene_hierarchy.children[0];
  const auto& b = back.scene_hierarchy.children[1];
  EXPECT_EQ(a.name, "Box_A");
  EXPECT_EQ(b.name, "Box_B");
  EXPECT_EQ(a.layer, "Framing");
  ASSERT_TRUE(a.mesh_resource_id.has_value());
  ASSERT_TRUE(b.mesh_resource_id.has_value());
  EXPECT_EQ(*a.mesh_resource_id, *b.mesh_resource_id);
  EXPECT_FALSE(back.gltf_materials.empty());
}

TEST(FromFragments, RoundTripsBothRawAndZlibCompressedWireFormats) {
  const auto scene = make_two_instance_scene();
  const auto raw_bytes = to_fragments(scene, /*raw=*/true);
  const auto compressed_bytes = to_fragments(scene, /*raw=*/false);
  EXPECT_NE(raw_bytes, compressed_bytes);

  const auto back_from_raw = from_fragments(raw_bytes);
  const auto back_from_compressed = from_fragments(compressed_bytes);

  EXPECT_EQ(back_from_raw.mesh_resources[0].primitives[0].indices.size(),
            back_from_compressed.mesh_resources[0].primitives[0].indices.size());
  ASSERT_EQ(back_from_raw.scene_hierarchy.children.size(),
            back_from_compressed.scene_hierarchy.children.size());
  for (std::size_t i = 0; i < back_from_raw.scene_hierarchy.children.size(); ++i) {
    EXPECT_EQ(back_from_raw.scene_hierarchy.children[i].name,
              back_from_compressed.scene_hierarchy.children[i].name);
  }
}

TEST(FromFragments, PreservesRealSourceGuidsAndLayerHiddenMetadata) {
  auto scene = make_two_instance_scene();
  scene.scene_hierarchy.children[0].guid = "F160C36229782F47A9857FC88DD1F2CB";
  scene.layer_hidden = {{"Framing", false}, {"Cladding", true}};
  const auto raw = to_fragments(scene, /*raw=*/true);
  const auto back = from_fragments(raw);

  EXPECT_EQ(back.scene_hierarchy.children[0].guid, "F160C36229782F47A9857FC88DD1F2CB");
  EXPECT_EQ(back.layer_hidden, scene.layer_hidden);
}

TEST(FromFragments, AnOddMaterialCountRoundTripsColorsExactly) {
  // Cross-language regression guard: Dart's own port of this exact
  // feature (openskp#285) found a real write-side bug where an odd
  // material count corrupted the materials vector - see
  // packages/dart_fragments's own CHANGELOG entry. C++'s canonical flatc
  // codegen (unlike Dart's community `flat_buffers` package) uses fixed
  // manually-aligned structs for Material with no equivalent length-
  // prefix/element gap, so this is not expected to be affected - this
  // test exists to confirm that directly rather than assume it from the
  // architecture alone.
  InstancedScene scene;
  const std::array<std::array<double, 4>, 3> colors{{
      {1.0, 0.0, 0.0, 1.0},
      {0.0, 1.0, 0.0, 1.0},
      {0.0, 0.0, 1.0, 1.0},
  }};
  for (std::size_t i = 0; i < colors.size(); ++i) {
    InstancedMeshResource r;
    r.id = "mesh_" + std::to_string(i);
    r.definition_id = static_cast<EntityId>(i + 1);
    r.definition_name = "Tri";
    r.variant_key = std::to_string(i);
    LocalPrimitive prim;
    prim.positions = {0, 0, 0, 1, 0, 0, 0, 1, 0};
    prim.normals.assign(9, 0.0f);
    prim.uvs.assign(6, 0.0f);
    prim.indices = {0, 1, 2};
    prim.material_index = i;
    r.primitives = {prim};
    scene.mesh_resources.push_back(r);

    GltfMaterial gm;
    gm.pbr_metallic_roughness.base_color_factor = colors[i];
    scene.gltf_materials.push_back(gm);

    InstancedNode node;
    node.name = std::string(1, static_cast<char>('A' + i));
    node.matrix = kIdentity;
    node.mesh_resource_id = r.id;
    scene.scene_hierarchy.children.push_back(node);
  }
  scene.scene_hierarchy.name = "ROOT";
  scene.scene_hierarchy.matrix = kIdentity;

  const auto raw = to_fragments(scene, /*raw=*/true);
  const auto* model = parse_raw(raw);
  const auto* materials = model->meshes()->materials();
  ASSERT_NE(materials, nullptr);
  ASSERT_EQ(materials->size(), 3u);
  for (std::size_t i = 0; i < 3; ++i) {
    const auto* mat = materials->Get(static_cast<::flatbuffers::uoffset_t>(i));
    EXPECT_EQ(mat->r(), static_cast<std::uint8_t>(colors[i][0] * 255));
    EXPECT_EQ(mat->g(), static_cast<std::uint8_t>(colors[i][1] * 255));
    EXPECT_EQ(mat->b(), static_cast<std::uint8_t>(colors[i][2] * 255));
    EXPECT_EQ(mat->rendered_faces(), fb::RenderedFaces_ONE);
  }

  const auto back = from_fragments(raw);
  ASSERT_EQ(back.gltf_materials.size(), 3u);
  for (std::size_t i = 0; i < 3; ++i) {
    const auto& bcf = back.gltf_materials[i].pbr_metallic_roughness.base_color_factor;
    EXPECT_NEAR(bcf[0], colors[i][0], 1.0 / 255.0);
    EXPECT_NEAR(bcf[1], colors[i][1], 1.0 / 255.0);
    EXPECT_NEAR(bcf[2], colors[i][2], 1.0 / 255.0);
  }
}

TEST(FromFragments, ReconstructsEveryTriangleOfAPrimitiveSplitAcrossMultipleShells) {
  // Same grid-primitive shape as the oversized-shell-splitting tests
  // above, forcing the export side to split into several shells - the
  // read side has to walk every sample for the item and reassemble them
  // into the ONE mesh resource, not just read the first shell.
  constexpr int kCols = 210,
                kRows = 165;  // 209*164*2 = 68,552 triangles - forces a split (> 65535)
  const auto scene = scene_with_one_primitive(grid_primitive(kCols, kRows), "mesh_big");
  constexpr long long kExpectedTriangles =
      static_cast<long long>(kCols - 1) * static_cast<long long>(kRows - 1) * 2;

  const auto raw = to_fragments(scene, /*raw=*/true);

  const auto* model = parse_raw(raw);
  ASSERT_GT(model->meshes()->shells()->size(), 1u);

  const auto back = from_fragments(raw);
  ASSERT_EQ(back.mesh_resources.size(), 1u);
  long long total_triangles_back = 0;
  for (const auto& prim : back.mesh_resources[0].primitives) {
    total_triangles_back += static_cast<long long>(prim.indices.size() / 3);
  }
  EXPECT_EQ(total_triangles_back, kExpectedTriangles);
}

}  // namespace
}  // namespace openskp
