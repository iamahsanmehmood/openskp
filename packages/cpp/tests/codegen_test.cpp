// Tests for openskp::to_cpp_code (packages/cpp/src/codegen.cpp), the C++ port of
// packages/typescript/src/codegen.ts. Mirrors packages/python/tests/test_codegen.py's approach:
// build a small model with SkpBuilder, generate code for it, and assert on the TEXT of the
// generated code - the exact fragments a real Face/Instance/Layer/Material must produce.
//
// Deliberately text-based rather than compile-and-run: this project's own established convention
// (edit.cpp, create_test.cpp) avoids C++20 designated initializers in favor of default-construct
// + field-assign, which this suite checks for directly (`{.` must never appear in generated
// code) - that exact mistake, plus a `ComponentDefinitionBuilder&` dereferenced like a pointer and
// an MSVC ~16380-char single-string-literal limit, were all real bugs caught only by actually
// compiling generated output for a real file during development (see codegen.cpp's own
// cpp_string_literal_lines for the fix). Compiling the generated code back requires a compiler
// invocation whose flags/environment vary per OS (MSVC in particular needs a Developer Command
// Prompt's INCLUDE/LIB env vars just to find <string> - a plain subprocess call to cl.exe fails
// with "no include path set" even though CMake itself found and uses that same cl.exe), so it
// isn't automated here; it was verified manually against 4 real fixtures (gondola_v20.skp,
// SU_File.skp, Untitled.skp, capilla_quiroz_v17.skp) instead.

#include <gtest/gtest.h>

#include <openskp/codegen.hpp>
#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

namespace openskp {
namespace {

SkpModel roundtrip(const std::unique_ptr<SkpBuilder>& builder) {
  return SkpFile::from_buffer(builder->to_bytes()).parse();
}

TEST(Codegen, EmitsSolidMaterialLayerAndFaceWithFieldAssignment) {
  auto builder = create();
  int red = builder->add_material("Red", Color4{255, 0, 0, 255});
  LayerOptions lopts;
  lopts.hidden = true;
  builder->add_layer("Roof", lopts);
  FaceOptions fopts;
  fopts.material = red;
  builder->add_face({{0, 0, 0}, {10, 0, 0}, {10, 10, 0}, {0, 10, 0}}, fopts);
  SkpModel model = roundtrip(builder);

  std::string code = to_cpp_code(model);

  EXPECT_NE(code.find("add_material(\"Red\", Color4{255, 0, 0, 255})"), std::string::npos);
  // add_layer always has an implicit "Layer0" registered first (layer_opts0/layer0), so "Roof" -
  // the only layer this test explicitly created - comes out as layer_opts1/layer1.
  EXPECT_NE(code.find("LayerOptions layer_opts1;"), std::string::npos);
  EXPECT_NE(code.find("layer_opts1.hidden = true;"), std::string::npos);
  EXPECT_NE(code.find("add_layer(\"Roof\", layer_opts1)"), std::string::npos);
  EXPECT_NE(code.find("FaceOptions face_opts0;"), std::string::npos);
  EXPECT_NE(code.find("face_opts0.material = mat0;"), std::string::npos);
  // No UV on this face (no textured material), so auto_triangulate should be set.
  EXPECT_NE(code.find("face_opts0.auto_triangulate = true;"), std::string::npos);
  // The project's own convention (edit.cpp, create_test.cpp) is default-construct then
  // field-assign - never a C++20 designated initializer.
  EXPECT_EQ(code.find("{."), std::string::npos);
}

TEST(Codegen, ReconstructsFaceHoles) {
  auto builder = create();
  FaceOptions fopts;
  fopts.holes = {{{20, 20, 0}, {80, 20, 0}, {80, 80, 0}, {20, 80, 0}}};
  builder->add_face({{0, 0, 0}, {100, 0, 0}, {100, 100, 0}, {0, 100, 0}}, fopts);
  SkpModel model = roundtrip(builder);

  std::string code = to_cpp_code(model);

  auto holes_pos = code.find(".holes = {{");
  ASSERT_NE(holes_pos, std::string::npos);
  EXPECT_NE(code.find("{20, 20, 0}", holes_pos), std::string::npos);
  EXPECT_NE(code.find("{80, 80, 0}", holes_pos), std::string::npos);
}

TEST(Codegen, PreservesGenuinelyEmptyInstanceNameAndInstancePaint) {
  auto builder = create();
  int red = builder->add_material("Red", Color4{255, 0, 0, 255});
  auto& def = builder->add_component_definition("Widget");
  def.add_face({{0, 0, 0}, {10, 0, 0}, {10, 10, 0}, {0, 10, 0}});
  def.close();

  InstanceOptions named;
  named.name = "Widget";  // equal to the definition's own name - name line should be omitted
  builder->add_instance(def, named);

  InstanceOptions empty_named;
  empty_named.name = "";  // genuinely empty, not omitted - must stay empty, not fall back
  empty_named.material = red;
  builder->add_instance(def, empty_named);

  SkpModel model = roundtrip(builder);
  ASSERT_EQ(model.root().instances.size(), 2u);
  EXPECT_EQ(model.root().instances[0].name, "Widget");
  EXPECT_EQ(model.root().instances[1].name, "");
  EXPECT_TRUE(model.root().instances[1].material_id.has_value());

  std::string code = to_cpp_code(model);

  // The instance whose stored name already equals the definition's name gets no explicit
  // `.name =` line at all (add_instance's own default already reproduces it).
  auto first_instance_opts = code.find("inst_opts0.translation");
  auto second_instance_opts = code.find("inst_opts1.translation");
  ASSERT_NE(first_instance_opts, std::string::npos);
  ASSERT_NE(second_instance_opts, std::string::npos);
  std::string first_block =
      code.substr(first_instance_opts, second_instance_opts - first_instance_opts);
  EXPECT_EQ(first_block.find(".name ="), std::string::npos);

  EXPECT_NE(code.find("inst_opts1.name = \"\";"), std::string::npos);
  EXPECT_NE(code.find("inst_opts1.material = mat0;"), std::string::npos);
}

TEST(Codegen, UsesDotForDefinitionBuildersAndArrowForRootBuilder) {
  auto builder = create();
  auto& inner = builder->add_component_definition("Inner");
  inner.add_face({{0, 0, 0}, {10, 0, 0}, {10, 10, 0}, {0, 10, 0}});
  inner.close();

  auto& outer = builder->add_component_definition("Outer");
  outer.add_instance(inner, {});
  outer.close();

  builder->add_instance(outer, {});
  SkpModel model = roundtrip(builder);

  std::string code = to_cpp_code(model);

  // add_component_definition returns a ComponentDefinitionBuilder& (a reference, not a pointer) -
  // its own add_face/add_instance/close calls must use `.`, never `->`. `builder` itself is a
  // smart pointer (create() returns one), so its calls correctly keep `->`.
  EXPECT_NE(code.find("def0.add_face("), std::string::npos);
  EXPECT_NE(code.find("def1.add_instance(def0,"), std::string::npos);
  EXPECT_NE(code.find("def1.close();"), std::string::npos);
  EXPECT_NE(code.find("builder->add_instance(def1,"), std::string::npos);
  EXPECT_EQ(code.find("def0->"), std::string::npos);
  EXPECT_EQ(code.find("def1->"), std::string::npos);
  // add_instance takes the definition by reference, not by pointer - passing `*def0` (as if
  // dereferencing a pointer) doesn't compile against a ComponentDefinitionBuilder&.
  EXPECT_EQ(code.find("add_instance(*def"), std::string::npos);
}

class CodegenRealFixture : public ::testing::TestWithParam<const char*> {};

TEST_P(CodegenRealFixture, GeneratesPlausibleWellFormedCode) {
  SkpModel model = SkpFile::open(test::fixture(GetParam())).parse();
  std::string code = to_cpp_code(model);

  EXPECT_FALSE(code.empty());
  // Same two syntax mistakes as the synthetic tests above, re-checked against real, much larger
  // geometry where they actually first surfaced.
  EXPECT_EQ(code.find("{."), std::string::npos);
  EXPECT_EQ(code.find("add_instance(*def"), std::string::npos);

  // MSVC rejects a single string literal longer than ~16380 chars; cpp_string_literal_lines
  // chunks base64 texture data into <=16000-char literals, so no single line should come close
  // to that limit regardless of how large the source texture is.
  std::size_t line_start = 0;
  while (line_start < code.size()) {
    auto line_end = code.find('\n', line_start);
    if (line_end == std::string::npos) line_end = code.size();
    EXPECT_LT(line_end - line_start, 16200u);
    line_start = line_end + 1;
  }

  // If any texture material was emitted, its helper functions are forward-declared before
  // build() (whose body calls them) - their full definitions are only appended at the very end
  // of the file, once textured_mats is fully known.
  auto forward_decl = code.find("openskp_codegen_base64_decode(const std::string& s);");
  auto first_call = code.find("openskp_codegen_base64_decode(b64_");
  if (first_call != std::string::npos) {
    ASSERT_NE(forward_decl, std::string::npos);
    EXPECT_LT(forward_decl, first_call);
  }
}

INSTANTIATE_TEST_SUITE_P(RealFixtures, CodegenRealFixture,
                         ::testing::Values("gondola_v20.skp", "SU_File.skp", "Untitled.skp",
                                           "capilla_quiroz_v17.skp"));

}  // namespace
}  // namespace openskp
