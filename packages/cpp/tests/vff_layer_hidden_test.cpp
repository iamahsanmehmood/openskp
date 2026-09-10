#include <gtest/gtest.h>

#include <openskp/openskp.hpp>

#include "internal.hpp"

// The VFF-format (2021+) counterpart of the legacy layer-hidden flag
// (see layer_hidden_test.cpp): each layer's 8C3C node (under a 993A
// layer-manager list) carries a single-byte 8E3C child sibling to the
// already-parsed DC05 (id) and 8D3C (name) - 1 = hidden, 0 = visible.
// Confirmed byte-for-byte against a real production file's own Tags
// panel: every layer shown with a hollow/hidden eye icon had 8E3C=01,
// every visible one had 8E3C=00, with no exceptions. Mirrors Python's
// TestVffLayerHidden.
//
// collect_layers() is called directly with a hand-built TlvNode tree
// (not a real binary stream - TlvNode's own fields are trivial to
// construct directly and this is the same level Python's C-extension
// tests operate at for TLV-shaped input), matching layer_hidden_test.cpp's
// existing pattern of testing one level below full SkpFile::open().

namespace openskp::test {
namespace {

TlvNode leaf(const std::string& tag, ByteBuffer payload) {
  TlvNode n;
  n.tag = tag;
  n.payload = std::move(payload);
  return n;
}

ByteBuffer str_payload(const std::string& s) { return ByteBuffer(s.begin(), s.end()); }

TEST(VffLayerHidden, HiddenAndVisibleLayersReadCorrectly) {
  TlvNode hidden_layer;
  hidden_layer.tag = "8C3C";
  hidden_layer.children = {
      leaf("DC05", {5}),
      leaf("8D3C", str_payload("wall_external_cladding_1")),
      leaf("8E3C", {1}),
  };

  TlvNode visible_layer;
  visible_layer.tag = "8C3C";
  visible_layer.children = {
      leaf("DC05", {6}),
      leaf("8D3C", str_payload("wall")),
      leaf("8E3C", {0}),
  };

  TlvNode manager;
  manager.tag = "993A";
  manager.children = {hidden_layer, visible_layer};

  std::vector<TlvNode> roots = {manager};
  std::map<EntityId, std::string> id_to_name;
  std::map<std::string, bool> hidden;
  collect_layers(roots, id_to_name, hidden);

  ASSERT_TRUE(hidden.count("wall_external_cladding_1"));
  EXPECT_TRUE(hidden.at("wall_external_cladding_1"));
  ASSERT_TRUE(hidden.count("wall"));
  EXPECT_FALSE(hidden.at("wall"));
}

TEST(VffLayerHidden, LayerWithNoNameGetsNoHiddenEntry) {
  // The hidden-flag branch is gated on the name (8D3C) node being
  // present, matching Python's find_child_tag(child['children'], '8E3C')
  // only being reached once the name lookup already succeeded.
  TlvNode nameless_layer;
  nameless_layer.tag = "8C3C";
  nameless_layer.children = {
      leaf("DC05", {7}),
      leaf("8E3C", {1}),
  };

  TlvNode manager;
  manager.tag = "993A";
  manager.children = {nameless_layer};

  std::vector<TlvNode> roots = {manager};
  std::map<EntityId, std::string> id_to_name;
  std::map<std::string, bool> hidden;
  collect_layers(roots, id_to_name, hidden);

  EXPECT_TRUE(hidden.empty());
}

TEST(VffLayerHidden, LayerWithNo8E3CTagLeavesHiddenMapUntouched) {
  // A layer manager entry with no 8E3C sibling at all (e.g. an older
  // file predating this bit) must not add a false entry - the caller's
  // pre-seeded "assume visible" default (core.cpp) stays authoritative.
  TlvNode layer_no_flag;
  layer_no_flag.tag = "8C3C";
  layer_no_flag.children = {
      leaf("DC05", {8}),
      leaf("8D3C", str_payload("no_flag_layer")),
  };

  TlvNode manager;
  manager.tag = "993A";
  manager.children = {layer_no_flag};

  std::vector<TlvNode> roots = {manager};
  std::map<EntityId, std::string> id_to_name;
  std::map<std::string, bool> hidden;
  collect_layers(roots, id_to_name, hidden);

  EXPECT_TRUE(hidden.empty());
}

}  // namespace
}  // namespace openskp::test
