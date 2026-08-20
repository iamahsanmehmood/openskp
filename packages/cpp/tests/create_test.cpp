// Tests for openskp::create (packages/cpp/src/create.cpp), the C++ port of
// packages/python/src/openskp/create.py. Mirrors the feature-area coverage of
// packages/python/tests/test_create.py, adapted to this project's GoogleTest/round-trip-through-
// our-own-reader idiom (see legacy_single_material_test.cpp for precedent): most tests build a
// file with SkpBuilder, then parse the resulting bytes back with SkpFile::from_buffer(...).parse()
// and assert on the resulting SkpModel - the same "read your own output back with the
// already-trusted reader" validation strategy this project already uses on the read side.

#include <cmath>
#include <fstream>

#include <gtest/gtest.h>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

namespace openskp {
namespace {

// A local constant instead of <cmath>'s non-standard M_PI (MSVC only defines it with
// _USE_MATH_DEFINES set before <cmath> is first included, which isn't guaranteed here) - avoids
// that portability trap across this project's GCC/Clang/MSVC CI matrix.
constexpr double kPi = 3.14159265358979323846;

SkpModel round_trip(SkpBuilder& builder) {
  ByteBuffer bytes = builder.to_bytes();
  return SkpFile::from_buffer(bytes).parse();
}

// A syntactically-tiny PNG - just the magic bytes plus filler. write_textured_material embeds
// image bytes verbatim without decoding them as a real image, so this is sufficient to exercise
// the writer/reader's own byte plumbing without needing a real PNG encoder in the test.
ByteBuffer fake_png_bytes() {
  ByteBuffer data = {0x89, 'P', 'N', 'G', '\r', '\n', 0x1a, '\n'};
  data.insert(data.end(), 64, 0x42);
  return data;
}

// ---------------------------------------------------------------------------------------------
// Single face / basic geometry.
// ---------------------------------------------------------------------------------------------

TEST(Create, SingleFaceRoundTrips) {
  auto builder = create();
  builder->add_face({{0, 0, 0}, {100, 0, 0}, {100, 100, 0}, {0, 100, 0}});

  SkpModel model = round_trip(*builder);
  const Definition& root = model.root();
  ASSERT_EQ(root.faces.size(), 1u);
  EXPECT_EQ(root.vertices.size(), 4u);
  EXPECT_EQ(root.edges.size(), 4u);

  std::vector<Point3> expected = {{0, 0, 0}, {100, 0, 0}, {100, 100, 0}, {0, 100, 0}};
  for (const auto& p : expected) {
    bool found = false;
    for (const auto& [id, v] : root.vertices) {
      if (v.x == p[0] && v.y == p[1] && v.z == p[2]) found = true;
    }
    EXPECT_TRUE(found) << "expected vertex (" << p[0] << ", " << p[1] << ", " << p[2] << ") not found";
  }
}

TEST(Create, SharedVerticesAndEdgesAreDeduplicated) {
  auto builder = create();
  // Two triangles sharing an edge - the shared 2 vertices/1 edge should not be duplicated.
  builder->add_face({{0, 0, 0}, {10, 0, 0}, {10, 10, 0}});
  builder->add_face({{0, 0, 0}, {10, 10, 0}, {0, 10, 0}});

  SkpModel model = round_trip(*builder);
  const Definition& root = model.root();
  EXPECT_EQ(root.faces.size(), 2u);
  EXPECT_EQ(root.vertices.size(), 4u);  // not 6
  EXPECT_EQ(root.edges.size(), 5u);     // not 6
}

TEST(Create, FaceRequiresAtLeast3Points) {
  auto builder = create();
  EXPECT_THROW(builder->add_face({{0, 0, 0}, {1, 0, 0}}), SkpWriteError);
}

TEST(Create, NonCoplanarFaceRaisesWithoutAutoTriangulate) {
  auto builder = create();
  EXPECT_THROW(builder->add_face({{0, 0, 0}, {10, 0, 0}, {10, 10, 5}, {0, 10, 0}}), SkpWriteError);
}

// ---------------------------------------------------------------------------------------------
// Materials.
// ---------------------------------------------------------------------------------------------

TEST(Create, SolidMaterialRoundTrips) {
  auto builder = create();
  int red = builder->add_material("Red", Color3{255, 0, 0});
  FaceOptions opts;
  opts.material = red;
  builder->add_face({{0, 0, 0}, {10, 0, 0}, {10, 10, 0}, {0, 10, 0}}, opts);

  SkpModel model = round_trip(*builder);
  ASSERT_EQ(model.materials.size(), 1u);
  EXPECT_EQ(model.materials[0].name, "Red");
  EXPECT_EQ(model.materials[0].color[0], 255);
  EXPECT_EQ(model.materials[0].color[1], 0);
  EXPECT_EQ(model.materials[0].color[2], 0);

  ASSERT_EQ(model.root().faces.size(), 1u);
  const Face& f = model.root().faces.begin()->second;
  ASSERT_TRUE(f.material_id.has_value());
  const Material* mat = model.material_by_id(*f.material_id);
  ASSERT_NE(mat, nullptr);
  EXPECT_EQ(mat->name, "Red");
}

TEST(Create, DuplicateMaterialNameReturnsSameHandle) {
  auto builder = create();
  int a = builder->add_material("Blue", Color3{0, 0, 255});
  int b = builder->add_material("Blue", Color3{10, 10, 10});  // different rgba is ignored on repeat
  EXPECT_EQ(a, b);
  EXPECT_EQ(builder->materials_by_name.at("Blue"), a);
}

TEST(Create, TexturedMaterialRoundTrips) {
  auto tmp_path = std::filesystem::temp_directory_path() / "openskp_create_test_texture.png";
  {
    std::ofstream f(tmp_path, std::ios::binary);
    ByteBuffer png = fake_png_bytes();
    f.write(reinterpret_cast<const char*>(png.data()), static_cast<std::streamsize>(png.size()));
  }

  auto builder = create();
  int brick = builder->add_texture_material("Brick", tmp_path);
  FaceOptions opts;
  opts.material = brick;
  builder->add_face({{0, 0, 0}, {100, 0, 0}, {100, 100, 0}, {0, 100, 0}}, opts);

  SkpModel model = round_trip(*builder);
  std::filesystem::remove(tmp_path);

  ASSERT_EQ(model.materials.size(), 1u);
  const Material& mat = model.materials[0];
  EXPECT_EQ(mat.name, "Brick");
  ASSERT_TRUE(mat.texture.has_value());
  ASSERT_TRUE(mat.texture->data.has_value());
  EXPECT_EQ(*mat.texture->data, fake_png_bytes());
}

TEST(Create, MaterialsMustPrecedeGeometry) {
  auto builder = create();
  builder->add_face({{0, 0, 0}, {1, 0, 0}, {1, 1, 0}});
  EXPECT_THROW(builder->add_material("TooLate", Color3{1, 2, 3}), SkpWriteError);
}

// ---------------------------------------------------------------------------------------------
// Layers.
// ---------------------------------------------------------------------------------------------

TEST(Create, LayerRoundTrips) {
  auto builder = create();
  LayerOptions opts;
  opts.color = Color4{10, 20, 30, 255};
  opts.hidden = true;
  int roof = builder->add_layer("Roof", opts);
  FaceOptions fopts;
  fopts.layer = roof;
  builder->add_face({{0, 0, 0}, {1, 0, 0}, {1, 1, 0}}, fopts);

  SkpModel model = round_trip(*builder);
  // Layer0 (from the scaffold) + the new "Roof" layer.
  ASSERT_EQ(model.layers.size(), 2u);
  const Layer* roof_layer = nullptr;
  for (const auto& l : model.layers) {
    if (l.name == "Roof") roof_layer = &l;
  }
  ASSERT_NE(roof_layer, nullptr);
  EXPECT_TRUE(roof_layer->hidden);
  EXPECT_EQ(roof_layer->color[0], 10);
  EXPECT_EQ(roof_layer->color[1], 20);
  EXPECT_EQ(roof_layer->color[2], 30);
}

// ---------------------------------------------------------------------------------------------
// Component definitions + instances.
// ---------------------------------------------------------------------------------------------

TEST(Create, ComponentDefinitionAndMultipleInstances) {
  auto builder = create();
  auto& chair = builder->add_component_definition("Chair");
  chair.add_face({{0, 0, 0}, {20, 0, 0}, {20, 20, 0}, {0, 20, 0}});
  chair.close();

  InstanceOptions a;
  a.translation = {100, 0, 0};
  builder->add_instance(chair, a);
  InstanceOptions b;
  b.translation = {200, 0, 0};
  b.name = "Second Chair";
  builder->add_instance(chair, b);

  SkpModel model = round_trip(*builder);
  ASSERT_EQ(model.definitions.size(), 1u);
  const Definition& def = model.definitions.begin()->second;
  EXPECT_EQ(def.name, "Chair");
  EXPECT_EQ(def.faces.size(), 1u);

  ASSERT_EQ(model.root().instances.size(), 2u);
  bool found_translation_100 = false, found_named = false;
  for (const auto& inst : model.root().instances) {
    ASSERT_GE(inst.matrix.size(), 12u);
    if (std::abs(inst.matrix[9] - 100.0) < 1e-9) found_translation_100 = true;
    if (inst.name == "Second Chair") found_named = true;
  }
  EXPECT_TRUE(found_translation_100);
  EXPECT_TRUE(found_named);
}

TEST(Create, InstanceMustReferenceAClosedDefinitionFromTheSameBuilder) {
  auto builder = create();
  auto& chair = builder->add_component_definition("Chair");
  chair.add_face({{0, 0, 0}, {1, 0, 0}, {1, 1, 0}});
  // Not yet closed.
  EXPECT_THROW(builder->add_instance(chair), SkpWriteError);
  chair.close();

  auto other_builder = create();
  EXPECT_THROW(other_builder->add_instance(chair), SkpWriteError);
}

// ---------------------------------------------------------------------------------------------
// Groups + nesting.
// ---------------------------------------------------------------------------------------------

TEST(Create, GroupSelfPlaces) {
  auto builder = create();
  GroupOptions opts;
  opts.name = "Table";
  opts.translation = {50, 0, 0};
  auto& table = builder->add_group(opts);
  table.add_face({{0, 0, 0}, {30, 0, 0}, {30, 30, 0}, {0, 30, 0}});
  table.close();

  SkpModel model = round_trip(*builder);
  ASSERT_EQ(model.definitions.size(), 1u);
  ASSERT_EQ(model.root().instances.size(), 1u);
  const Instance& inst = model.root().instances[0];
  EXPECT_EQ(inst.name, "Table");
  ASSERT_GE(inst.matrix.size(), 12u);
  EXPECT_NEAR(inst.matrix[9], 50.0, 1e-9);
}

TEST(Create, NestedDefinitionInstanceAndNestedGroupInstance) {
  auto builder = create();
  auto& wheel = builder->add_component_definition("Wheel");
  wheel.add_face({{0, 0, 0}, {10, 0, 0}, {10, 10, 0}, {0, 10, 0}});
  wheel.close();

  auto& engine = builder->add_component_definition("Engine");
  engine.add_face({{0, 0, 0}, {30, 0, 0}, {30, 30, 0}, {0, 30, 0}});
  engine.close();

  auto& car = builder->add_component_definition("Car");
  car.add_face({{0, 0, 0}, {150, 0, 0}, {150, 60, 0}, {0, 60, 0}});
  InstanceOptions wheel_inst;
  wheel_inst.translation = {0, 0, 0};
  car.add_instance(wheel, wheel_inst);
  GroupInstanceOptions engine_group;
  engine_group.translation = {50, 0, 10};
  car.add_group_instance(engine, engine_group);
  car.close();

  builder->add_instance(car);

  SkpModel model = round_trip(*builder);
  ASSERT_EQ(model.definitions.size(), 3u);
  const Definition* car_def = nullptr;
  for (const auto& [id, defn] : model.definitions) {
    if (defn.name == "Car") car_def = &defn;
  }
  ASSERT_NE(car_def, nullptr);
  EXPECT_EQ(car_def->faces.size(), 1u);
  EXPECT_EQ(car_def->instances.size(), 2u);  // the wheel instance + the engine group instance
}

TEST(Create, DefinitionCannotNestAnInstanceOfItself) {
  auto builder = create();
  auto& a = builder->add_component_definition("A");
  a.add_face({{0, 0, 0}, {1, 0, 0}, {1, 1, 0}});
  EXPECT_THROW(a.add_instance(a), SkpWriteError);
}

// ---------------------------------------------------------------------------------------------
// Rotation + hidden.
// ---------------------------------------------------------------------------------------------

TEST(Create, RotationConvenienceProducesARotationMatrix) {
  auto builder = create();
  auto& part = builder->add_component_definition("Part");
  part.add_face({{0, 0, 0}, {10, 0, 0}, {10, 10, 0}, {0, 10, 0}});
  part.close();

  InstanceOptions opts;
  opts.rotation = Rotation{Point3{0, 0, 1}, kPi / 2.0};
  builder->add_instance(part, opts);

  SkpModel model = round_trip(*builder);
  ASSERT_EQ(model.root().instances.size(), 1u);
  const auto& m = model.root().instances[0].matrix;
  ASSERT_GE(m.size(), 9u);
  // Row-major 3x3 rotation by +90 degrees around Z: (1,0,0)->(0,1,0), i.e. m[0]=cos=0, m[1]=-sin=-1
  EXPECT_NEAR(m[0], 0.0, 1e-9);
  EXPECT_NEAR(m[1], -1.0, 1e-9);
  EXPECT_NEAR(m[3], 1.0, 1e-9);
  EXPECT_NEAR(m[4], 0.0, 1e-9);
}

TEST(Create, MatrixAndRotationAreMutuallyExclusive) {
  auto builder = create();
  auto& part = builder->add_component_definition("Part");
  part.add_face({{0, 0, 0}, {1, 0, 0}, {1, 1, 0}});
  part.close();

  InstanceOptions opts;
  opts.matrix3x3 = Matrix3x3{1, 0, 0, 0, 1, 0, 0, 0, 1};
  opts.rotation = Rotation{Point3{0, 0, 1}, 1.0};
  EXPECT_THROW(builder->add_instance(part, opts), SkpWriteError);
}

TEST(Create, HiddenFaceAndInstanceRoundTrip) {
  auto builder = create();
  FaceOptions fopts;
  fopts.hidden = true;
  builder->add_face({{0, 0, 0}, {1, 0, 0}, {1, 1, 0}}, fopts);

  auto& part = builder->add_component_definition("Part");
  part.add_face({{0, 0, 0}, {1, 0, 0}, {1, 1, 0}});
  part.close();
  InstanceOptions iopts;
  iopts.hidden = true;
  builder->add_instance(part, iopts);

  SkpModel model = round_trip(*builder);
  bool found_hidden_face = false;
  for (const auto& [id, f] : model.root().faces) {
    if (f.hidden) found_hidden_face = true;
  }
  EXPECT_TRUE(found_hidden_face);
  ASSERT_EQ(model.root().instances.size(), 1u);
  EXPECT_TRUE(model.root().instances[0].hidden);
}

// ---------------------------------------------------------------------------------------------
// Holes + auto_triangulate.
// ---------------------------------------------------------------------------------------------

TEST(Create, FaceWithHoleHasTwoLoops) {
  auto builder = create();
  std::vector<Point3> wall = {{0, 0, 0}, {200, 0, 0}, {200, 100, 0}, {0, 100, 0}};
  std::vector<Point3> window = {{80, 30, 0}, {120, 30, 0}, {120, 70, 0}, {80, 70, 0}};
  FaceOptions opts;
  opts.holes = {window};
  builder->add_face(wall, opts);

  SkpModel model = round_trip(*builder);
  ASSERT_EQ(model.root().faces.size(), 1u);
  const Face& f = model.root().faces.begin()->second;
  ASSERT_EQ(f.loops.size(), 2u);
  EXPECT_EQ(f.loops[0].size(), 4u);
  EXPECT_EQ(f.loops[1].size(), 4u);
  // 4 outer + 4 hole vertices/edges, all distinct (hole does not touch the boundary).
  EXPECT_EQ(model.root().vertices.size(), 8u);
  EXPECT_EQ(model.root().edges.size(), 8u);
}

TEST(Create, HoleNeedsAtLeast3Points) {
  auto builder = create();
  FaceOptions opts;
  opts.holes = {{{1, 1, 0}, {2, 2, 0}}};
  EXPECT_THROW(builder->add_face({{0, 0, 0}, {10, 0, 0}, {10, 10, 0}, {0, 10, 0}}, opts), SkpWriteError);
}

TEST(Create, AutoTriangulateSplitsANonPlanarQuadIntoTwoFaces) {
  auto builder = create();
  FaceOptions opts;
  opts.auto_triangulate = true;
  builder->add_face({{0, 0, 0}, {10, 0, 0}, {10, 10, 5}, {0, 10, 0}}, opts);

  SkpModel model = round_trip(*builder);
  EXPECT_EQ(model.root().faces.size(), 2u);
}

TEST(Create, AutoTriangulateLeavesAnAlreadyPlanarQuadAsOneFace) {
  auto builder = create();
  FaceOptions opts;
  opts.auto_triangulate = true;
  builder->add_face({{0, 0, 0}, {10, 0, 0}, {10, 10, 0}, {0, 10, 0}}, opts);

  SkpModel model = round_trip(*builder);
  EXPECT_EQ(model.root().faces.size(), 1u);
}

// ---------------------------------------------------------------------------------------------
// Circles, arcs, polylines.
// ---------------------------------------------------------------------------------------------

TEST(Create, CircleProducesAFaceWithNSegmentVerticesAndEdges) {
  auto builder = create();
  CircleOptions opts;
  opts.num_segments = 16;
  builder->add_circle({50, 50, 0}, {0, 0, 1}, 40.0, opts);

  SkpModel model = round_trip(*builder);
  ASSERT_EQ(model.root().faces.size(), 1u);
  EXPECT_EQ(model.root().vertices.size(), 16u);
  EXPECT_EQ(model.root().edges.size(), 16u);
}

TEST(Create, ArcProducesEdgesButNoFace) {
  auto builder = create();
  ArcOptions opts;
  opts.num_segments = 8;
  builder->add_arc({50, 50, 0}, {0, 0, 1}, 40.0, 0.0, kPi / 2.0, opts);

  SkpModel model = round_trip(*builder);
  EXPECT_EQ(model.root().faces.size(), 0u);
  EXPECT_EQ(model.root().vertices.size(), 9u);  // num_segments + 1 endpoints
  EXPECT_EQ(model.root().edges.size(), 8u);
}

TEST(Create, PolylineProducesEdgesButNoFace) {
  auto builder = create();
  builder->add_polyline({{0, 0, 0}, {10, 10, 0}, {20, 0, 0}, {30, 10, 0}});

  SkpModel model = round_trip(*builder);
  EXPECT_EQ(model.root().faces.size(), 0u);
  EXPECT_EQ(model.root().vertices.size(), 4u);
  EXPECT_EQ(model.root().edges.size(), 3u);
}

TEST(Create, ClosedPolylineConnectsLastPointToFirst) {
  auto builder = create();
  PolylineOptions opts;
  opts.closed = true;
  builder->add_polyline({{0, 0, 0}, {10, 0, 0}, {10, 10, 0}, {0, 10, 0}}, opts);

  SkpModel model = round_trip(*builder);
  EXPECT_EQ(model.root().vertices.size(), 4u);
  EXPECT_EQ(model.root().edges.size(), 4u);
}

// ---------------------------------------------------------------------------------------------
// Attributes.
// ---------------------------------------------------------------------------------------------

TEST(Create, InstanceAttributesRoundTrip) {
  auto builder = create();
  auto& part = builder->add_component_definition("Part");
  part.add_face({{0, 0, 0}, {1, 0, 0}, {1, 1, 0}});
  part.close();

  InstanceOptions opts;
  opts.attributes["count"] = std::int32_t{42};
  opts.attributes["label"] = std::string{"widget"};
  opts.attributes["weight"] = 3.5;
  builder->add_instance(part, opts);

  SkpModel model = round_trip(*builder);
  ASSERT_EQ(model.root().instances.size(), 1u);
  const auto& props = model.root().instances[0].properties;
  EXPECT_EQ(props.at("count"), "42");
  EXPECT_EQ(props.at("label"), "widget");
  ASSERT_EQ(props.count("weight"), 1u);
}

TEST(Create, FaceAndDefinitionAttributesDoNotThrow) {
  // The reader's public model doesn't expose face/definition-level attributes (see edit.hpp's
  // own documented gap), so this is a smoke test that writing them at least succeeds and
  // produces a file our own reader still parses without error - not a content assertion.
  auto builder = create();
  DefinitionOptions dopts;
  dopts.attributes["material_code"] = std::string{"ABC-123"};
  auto& part = builder->add_component_definition("Part", dopts);
  FaceOptions fopts;
  fopts.attributes["area_sqft"] = 12.5;
  part.add_face({{0, 0, 0}, {1, 0, 0}, {1, 1, 0}}, fopts);
  part.close();
  builder->add_instance(part);

  EXPECT_NO_THROW(round_trip(*builder));
}

// ---------------------------------------------------------------------------------------------
// Explicit texture positioning (front_uv/back_uv).
// ---------------------------------------------------------------------------------------------

TEST(Create, ExplicitFrontUvRoundTrips) {
  auto builder = create();
  int brick = builder->add_material("Brick", Color3{150, 100, 50});
  FaceOptions opts;
  opts.material = brick;
  opts.front_uv = UvCorrespondence{
      {Point3{0, 0, 0}, {0.0, 0.0}},
      {Point3{50, 0, 0}, {1.0, 0.0}},
      {Point3{0, 50, 0}, {0.0, 1.0}},
  };
  builder->add_face({{0, 0, 0}, {100, 0, 0}, {100, 100, 0}, {0, 100, 0}}, opts);

  SkpModel model = round_trip(*builder);
  ASSERT_EQ(model.root().faces.size(), 1u);
  const Face& f = model.root().faces.begin()->second;
  EXPECT_TRUE(f.uv_transform.has_value());
}

// ---------------------------------------------------------------------------------------------
// Exact archive-slot-32767 boundary (see create.hpp's module docstring and create.cpp's
// ArchiveWriter::backref/new_of_known_class comments for why this exact boundary is a real,
// previously-shipped bug class in the Python port this mirrors).
// ---------------------------------------------------------------------------------------------

TEST(Create, LargeModelCrossingTheSlotBoundaryRoundTrips) {
  // Deliberately uses unique (non-shared) vertices per triangle - the same worst-case shape a
  // flattened CAD import produces, landing the 0x7FFF crossing at a non-hand-picked slot.
  auto builder = create();
  constexpr int kTriangles = 5000;
  for (int i = 0; i < kTriangles; ++i) {
    double x = i * 10.0;
    builder->add_face({{x, 0.0, 0.0}, {x + 1.0, 0.0, 0.0}, {x, 1.0, 0.0}});
  }

  SkpModel model = round_trip(*builder);
  EXPECT_EQ(model.root().faces.size(), static_cast<std::size_t>(kTriangles));
}

}  // namespace
}  // namespace openskp
