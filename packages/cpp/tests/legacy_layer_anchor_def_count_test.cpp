#include <gtest/gtest.h>
#include <string>
#include <vector>

#include <openskp/openskp.hpp>

namespace openskp {
namespace {

// Regression (#534 / #285): after the last CLayer the reader sees `<anchor u16><def_count u32>`
// and then the first CComponentDefinition. skip_clayer_parent_ref consumed the anchor as a v18
// "parent" back-ref whenever both u16s at that position pointed at CLayer objects - which also
// happens when the definition count (11, 12, or 13) coincides with a tag's slot.

ByteBuffer build_model(int n_tags, int n_defs) {
  SkpBuilder builder;
  const int mat = builder.add_material("M", Color4{200, 100, 50, 255});
  std::vector<int> layers;
  layers.reserve(static_cast<std::size_t>(n_tags));
  for (int i = 0; i < n_tags; ++i) {
    layers.push_back(builder.add_layer("T" + std::to_string(i)));
  }
  std::vector<ComponentDefinitionBuilder*> defs;
  defs.reserve(static_cast<std::size_t>(n_defs));
  for (int i = 0; i < n_defs; ++i) {
    auto& comp = builder.add_component_definition("P" + std::to_string(i));
    FaceOptions opts;
    opts.material = mat;
    comp.add_face({{0.0, 0.0, 0.0}, {1.0 + i, 0.0, 0.0}, {1.0 + i, 1.0, 0.0}, {0.0, 1.0, 0.0}},
                  opts);
    comp.close();
    defs.push_back(&comp);
  }
  auto& container = builder.add_component_definition("Container");
  for (int i = 0; i < n_defs; ++i) {
    InstanceOptions opts;
    opts.name = "i" + std::to_string(i);
    opts.translation = {0.0, 2.0 * i, 0.0};
    opts.layer = layers[0];
    container.add_instance(*defs[static_cast<std::size_t>(i)], opts);
  }
  container.close();
  InstanceOptions root_opts;
  root_opts.name = "root";
  root_opts.layer = layers[0];
  builder.add_instance(container, root_opts);
  return builder.to_bytes();
}

TEST(LegacyLayerAnchorDefCount, DefinitionCountMatchingATagSlotRoundTrips) {
  for (int n_tags : {1, 2}) {
    for (int n_defs : {10, 11, 12, 13, 14}) {
      SCOPED_TRACE("n_tags=" + std::to_string(n_tags) + " n_defs=" + std::to_string(n_defs));
      const auto model = SkpFile::from_buffer(build_model(n_tags, n_defs)).parse();
      std::vector<std::string> expected_layers{"Layer0"};
      for (int i = 0; i < n_tags; ++i) {
        expected_layers.push_back("T" + std::to_string(i));
      }
      std::vector<std::string> actual_layers;
      for (const auto& layer : model.layers) {
        actual_layers.push_back(layer.name);
      }
      EXPECT_EQ(actual_layers, expected_layers);
      EXPECT_EQ(model.definitions.size(), static_cast<std::size_t>(n_defs + 1));
    }
  }
}

}  // namespace
}  // namespace openskp
