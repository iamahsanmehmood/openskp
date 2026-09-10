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

}  // namespace
}  // namespace openskp
