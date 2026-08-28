// Tests for openskp::open_existing (packages/cpp/src/edit.cpp), the C++ port of
// packages/python/src/openskp/edit.py. Mirrors packages/python/tests/test_edit.py's approach:
// build a file with SkpBuilder, save it, reopen it with open_existing(), and check the rebuilt
// builder's own re-parsed output preserves the original's structure - plus an end-to-end pass
// against a real SketchUp-SDK-authored fixture (capilla_quiroz_v17.skp) already used elsewhere in
// this test suite.

#include <gtest/gtest.h>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

namespace openskp {
namespace {

std::filesystem::path temp_skp(const char* stem) {
  return std::filesystem::temp_directory_path() /
         (std::string("openskp_edit_test_") + stem + ".skp");
}

// A syntactically-tiny PNG - just the magic bytes plus filler, matching create_test.cpp's own
// fake_png_bytes() (not shared across translation units, so duplicated here).
ByteBuffer fake_png_bytes() {
  ByteBuffer data = {0x89, 'P', 'N', 'G', '\r', '\n', 0x1a, '\n'};
  data.insert(data.end(), 64, 0x42);
  return data;
}

TEST(Edit, RejectsAModernNonLegacyFile) {
  EXPECT_THROW(open_existing(test::fixture("Untitled.skp")), SkpWriteError);
}

TEST(Edit, RejectsAMissingFile) {
  EXPECT_THROW(open_existing(test::fixture("does_not_exist_at_all.skp")), SkpWriteError);
}

TEST(Edit, RoundTripsAFreshlyBuiltFile) {
  auto path = temp_skp("roundtrip");
  {
    auto builder = create();
    int red = builder->add_material("Red", Color3{255, 0, 0});
    int roof = builder->add_layer("Roof");

    auto& chair = builder->add_component_definition("Chair");
    chair.add_face({{0, 0, 0}, {20, 0, 0}, {20, 20, 0}, {0, 20, 0}});
    chair.close();
    InstanceOptions iopts;
    iopts.translation = {200, 0, 0};
    builder->add_instance(chair, iopts);

    FaceOptions opts;
    opts.material = red;
    opts.layer = roof;
    builder->add_face({{0, 0, 0}, {100, 0, 0}, {100, 100, 0}, {0, 100, 0}}, opts);

    builder->save(path);
  }

  OpenExistingResult result = open_existing(path);
  ASSERT_NE(result.builder, nullptr);
  EXPECT_EQ(result.builder->materials_by_name.count("Red"), 1u);
  EXPECT_EQ(result.builder->layers_by_name.count("Roof"), 1u);
  ASSERT_EQ(result.definitions.count("Chair"), 1u);

  // The rebuilt file should carry the same face/material/layer/instance/definition counts as the
  // original when parsed back with our own reader.
  ByteBuffer rebuilt_bytes = result.builder->to_bytes();
  SkpModel rebuilt = SkpFile::from_buffer(rebuilt_bytes).parse();
  EXPECT_EQ(rebuilt.materials.size(), 1u);
  EXPECT_EQ(rebuilt.layers.size(), 2u);  // Layer0 + Roof
  EXPECT_EQ(rebuilt.definitions.size(), 1u);
  EXPECT_EQ(rebuilt.root().faces.size(), 1u);
  EXPECT_EQ(rebuilt.root().instances.size(), 1u);

  std::filesystem::remove(path);
}

TEST(Edit, DefaultProjectedTextureGetsAnExplicitUvPinNotLeftCorrupted) {
  // Found via cross-language analysis (2026-08-28): replay_materials wrote every rebuilt texture
  // material with the library's (until now) corrupted default applied height, and replay_uv only
  // replayed a face's UV when it already had an explicit uv_transform - leaving a DEFAULT-projected
  // textured face's material (and thus its real SketchUp rendering) corrupted end to end. Both are
  // fixed; this checks the rebuilt face actually gets a pin (not just that some texture data
  // round-tripped) and that the material's applied height isn't the corrupted sentinel.
  auto png_path = std::filesystem::temp_directory_path() / "openskp_edit_test_uv_texture.png";
  {
    std::ofstream f(png_path, std::ios::binary);
    ByteBuffer png = fake_png_bytes();
    f.write(reinterpret_cast<const char*>(png.data()), static_cast<std::streamsize>(png.size()));
  }
  auto path = temp_skp("default_projected_texture");
  {
    auto builder = create();
    int brick = builder->add_texture_material("Brick", png_path);
    FaceOptions opts;
    opts.material = brick;
    builder->add_face({{0, 0, 0}, {100, 0, 0}, {100, 100, 0}, {0, 100, 0}}, opts);  // no front_uv
    builder->save(path);
  }
  std::filesystem::remove(png_path);

  OpenExistingResult result = open_existing(path);
  ASSERT_NE(result.builder, nullptr);
  ByteBuffer rebuilt_bytes = result.builder->to_bytes();
  SkpModel rebuilt = SkpFile::from_buffer(rebuilt_bytes).parse();
  std::filesystem::remove(path);

  ASSERT_EQ(rebuilt.materials.size(), 1u);
  ASSERT_TRUE(rebuilt.materials[0].texture.has_value());
  EXPECT_DOUBLE_EQ(rebuilt.materials[0].texture->width, 1.0);
  EXPECT_DOUBLE_EQ(rebuilt.materials[0].texture->height, 1.0);
  ASSERT_EQ(rebuilt.root().faces.size(), 1u);
  EXPECT_TRUE(rebuilt.root().faces.begin()->second.uv_transform.has_value());
}

TEST(Edit, GenuinelyEmptyInstanceNameIsPreservedNotReplaced) {
  // Found via cross-language analysis (2026-08-28): add_instance's own name.value_or(definition
  // name) fallback and replay_instance's `if (!inst.name.empty()) options.name = inst.name;` both
  // silently replaced a genuinely empty instance name with its definition's name - a real
  // difference, not cosmetic (a later rename of the definition would no longer show through).
  auto path = temp_skp("empty_instance_name");
  {
    auto builder = create();
    auto& box = builder->add_component_definition("Box");
    box.add_face({{0, 0, 0}, {10, 0, 0}, {10, 10, 0}, {0, 10, 0}});
    box.close();
    InstanceOptions iopts;
    iopts.name = "";
    builder->add_instance(box, iopts);
    builder->save(path);
  }

  SkpModel source = SkpFile::open(path).parse();
  ASSERT_EQ(source.root().instances.size(), 1u);
  EXPECT_EQ(source.root().instances[0].name, "");

  OpenExistingResult result = open_existing(path);
  ASSERT_NE(result.builder, nullptr);
  ByteBuffer rebuilt_bytes = result.builder->to_bytes();
  SkpModel rebuilt = SkpFile::from_buffer(rebuilt_bytes).parse();
  std::filesystem::remove(path);

  ASSERT_EQ(rebuilt.root().instances.size(), 1u);
  EXPECT_EQ(rebuilt.root().instances[0].name, "");
}

TEST(Edit, GenuinelyEmptyDefinitionNameIsPreservedNotReplaced) {
  // Found via cross-language analysis (2026-08-28), same bug class as the empty instance name
  // case above: `defn.name.empty() ? ("Definition" + std::to_string(def_id)) : defn.name` silently
  // replaced a genuinely empty definition name with a fabricated one. SketchUp Groups are
  // internally just unnamed component definitions (unlike Components, which SketchUp
  // auto-names), so an empty name is common in real files.
  auto path = temp_skp("empty_definition_name");
  {
    auto builder = create();
    auto& box = builder->add_component_definition("");
    box.add_face({{0, 0, 0}, {10, 0, 0}, {10, 10, 0}, {0, 10, 0}});
    box.close();
    builder->add_instance(box, {});
    builder->save(path);
  }

  SkpModel source = SkpFile::open(path).parse();
  ASSERT_EQ(source.definitions.size(), 1u);
  EXPECT_EQ(source.definitions.begin()->second.name, "");

  OpenExistingResult result = open_existing(path);
  ASSERT_NE(result.builder, nullptr);
  ByteBuffer rebuilt_bytes = result.builder->to_bytes();
  SkpModel rebuilt = SkpFile::from_buffer(rebuilt_bytes).parse();
  std::filesystem::remove(path);

  ASSERT_EQ(rebuilt.definitions.size(), 1u);
  EXPECT_EQ(rebuilt.definitions.begin()->second.name, "");
}

TEST(Edit, ReturnedBuilderCanAddMoreGeometryAndCannotRegisterNewSections) {
  auto path = temp_skp("addmore");
  {
    auto builder = create();
    builder->add_face({{0, 0, 0}, {10, 0, 0}, {10, 10, 0}, {0, 10, 0}});
    builder->save(path);
  }

  OpenExistingResult result = open_existing(path);
  // More geometry is fine.
  EXPECT_NO_THROW(result.builder->add_face({{50, 50, 0}, {60, 50, 0}, {60, 60, 0}, {50, 60, 0}}));
  // Registering a genuinely new material/layer/definition is not - the file format's ordering
  // requirement (materials/layers/definitions before any geometry) was already satisfied by
  // replaying the source's own root-level geometry.
  EXPECT_THROW(result.builder->add_material("New", Color3{1, 2, 3}), SkpWriteError);
  EXPECT_THROW(result.builder->add_layer("New"), SkpWriteError);

  ByteBuffer bytes = result.builder->to_bytes();
  SkpModel model = SkpFile::from_buffer(bytes).parse();
  EXPECT_EQ(model.root().faces.size(), 2u);

  std::filesystem::remove(path);
}

TEST(Edit, DefinitionsMapAllowsPlacingMoreInstancesOfAnExistingDefinition) {
  auto path = temp_skp("moreinstances");
  {
    auto builder = create();
    auto& wheel = builder->add_component_definition("Wheel");
    wheel.add_face({{0, 0, 0}, {10, 0, 0}, {10, 10, 0}, {0, 10, 0}});
    wheel.close();
    builder->add_instance(wheel);
    builder->save(path);
  }

  OpenExistingResult result = open_existing(path);
  ASSERT_EQ(result.definitions.count("Wheel"), 1u);
  InstanceOptions opts;
  opts.translation = {500, 0, 0};
  EXPECT_NO_THROW(result.builder->add_instance(*result.definitions.at("Wheel"), opts));

  ByteBuffer bytes = result.builder->to_bytes();
  SkpModel model = SkpFile::from_buffer(bytes).parse();
  EXPECT_EQ(model.root().instances.size(), 2u);

  std::filesystem::remove(path);
}

TEST(Edit, EmptyBlankScaffoldHasNoGeometryToReplayButStillOpens) {
  // blank_v17.skp has 0 materials/layers-beyond-Layer0/definitions/root entities - exercises the
  // "nothing to replay" path (no crash, an empty-but-valid builder) distinctly from every other
  // test here, which all replay at least one face.
  OpenExistingResult result = open_existing(test::fixture("blank_v17.skp"));
  ASSERT_NE(result.builder, nullptr);
  EXPECT_TRUE(result.definitions.empty());
  // Can't call to_bytes() yet - matches create()'s own "no geometry added" guard - but adding a
  // face and saving should work.
  EXPECT_THROW(result.builder->to_bytes(), SkpWriteError);
  EXPECT_NO_THROW(result.builder->add_face({{0, 0, 0}, {1, 0, 0}, {1, 1, 0}}));
  EXPECT_NO_THROW(result.builder->to_bytes());
}

// ---------------------------------------------------------------------------------------------
// End-to-end against a real SketchUp-SDK-authored fixture.
// ---------------------------------------------------------------------------------------------

TEST(Edit, RealFixtureRoundTripsWithConsistentTopLevelCounts) {
  SkpModel original = SkpFile::open(test::fixture("capilla_quiroz_v17.skp")).parse();

  OpenExistingResult result = open_existing(test::fixture("capilla_quiroz_v17.skp"));
  ASSERT_NE(result.builder, nullptr);

  ByteBuffer rebuilt_bytes = result.builder->to_bytes();
  SkpModel rebuilt = SkpFile::from_buffer(rebuilt_bytes).parse();

  // Materials and layers are replayed 1:1 (every source material/layer becomes exactly one
  // writer call - see edit.hpp's own docstring for what's replayed vs approximated).
  EXPECT_EQ(rebuilt.materials.size(), original.materials.size());
  EXPECT_EQ(rebuilt.layers.size(), original.layers.size());

  // Every placed thing (group or instance alike) is replayed as a plain component instance
  // (documented fidelity gap), so root instance count should be at least as large as the
  // original's (never fewer, since nothing is silently dropped without a warning) - definitions
  // with no replayable geometry are skipped (and warned about), so definition count can be lower
  // but never higher.
  EXPECT_LE(rebuilt.definitions.size(), original.definitions.size());
}

}  // namespace
}  // namespace openskp
