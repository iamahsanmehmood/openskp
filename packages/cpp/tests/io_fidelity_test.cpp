#include <algorithm>
#include <gtest/gtest.h>
#include <limits>
#include <set>

#include <openskp/openskp.hpp>

namespace openskp {
namespace {
SkpModel read(SkpBuilder& builder) { return SkpFile::from_buffer(builder.to_bytes()).parse(); }

TEST(IoFidelity, NativeIdsAreSeparateFromArchiveSlotsAndRetainedOnWrite) {
  auto builder = create();
  DocumentOptions document;
  document.next_persistent_id = 10000;
  builder->set_document_options(document);
  builder->set_next_persistent_id("CMaterial", 41);
  builder->add_material("Paint", Color3{200, 100, 50});
  builder->set_next_persistent_id("CLayer", 42);
  builder->add_layer("Details");
  builder->set_next_persistent_id("CComponentDefinition", 43);
  auto& def = builder->add_component_definition("Part");
  builder->set_geometry_persistent_ids({{{0, 0, 0}, 44}, {{2, 0, 0}, 45}, {{0, 2, 0}, 46}},
                                       {{{{0, 0, 0}, {2, 0, 0}}, 47}});
  builder->set_next_persistent_id("CFace", 48);
  def.add_face({{0, 0, 0}, {2, 0, 0}, {0, 2, 0}});
  builder->set_next_persistent_id("CConstructionPoint", 49);
  def.add_construction_point({3, 4, 5});
  def.close();
  builder->set_geometry_persistent_ids({}, {});
  builder->set_next_persistent_id("CComponentInstance", 50);
  builder->add_instance(def);
  const auto model = read(*builder);
  ASSERT_EQ(model.definitions.size(), 1u);
  const auto& part = model.definitions.begin()->second;
  EXPECT_EQ(part.persistent_id, 43u);
  EXPECT_NE(part.id, part.persistent_id);
  std::set<std::uint64_t> vertices;
  for (const auto& item : part.vertices) vertices.insert(item.second.persistent_id);
  EXPECT_EQ(vertices, (std::set<std::uint64_t>{44, 45, 46}));
  bool edge = false;
  for (const auto& item : part.edges) edge |= item.second.persistent_id == 47;
  EXPECT_TRUE(edge);
  ASSERT_EQ(part.faces.size(), 1u);
  EXPECT_EQ(part.faces.begin()->second.persistent_id, 48u);
  ASSERT_EQ(part.construction_points.size(), 1u);
  EXPECT_EQ(part.construction_points[0].persistent_id, 49u);
  ASSERT_EQ(model.root().instances.size(), 1u);
  EXPECT_EQ(model.root().instances[0].id, 50);
  bool material = false, layer = false;
  for (const auto& item : model.materials)
    if (item.name == "Paint") material = item.persistent_id == 41;
  for (const auto& item : model.layers)
    if (item.name == "Details") layer = item.persistent_id == 42;
  EXPECT_TRUE(material);
  EXPECT_TRUE(layer);
  EXPECT_EQ(builder->to_bytes(), builder->to_bytes());
}

TEST(IoFidelity, RejectsDuplicateNativeIds) {
  auto builder = create();
  DocumentOptions document;
  document.next_persistent_id = 100;
  builder->set_document_options(document);
  builder->set_next_persistent_id("CConstructionPoint", 20);
  builder->add_construction_point({0, 0, 0});
  builder->set_next_persistent_id("CConstructionPoint", 20);
  EXPECT_THROW(builder->add_construction_point({1, 1, 1}), SkpWriteError);
}

TEST(IoFidelity, NamedDictionariesKeepTypesAndOwners) {
  auto builder = create();
  LayerOptions layer;
  layer.extra_dictionaries["TagData"]["enabled"] = AttributeBool{true};
  builder->add_layer("Details", layer);
  DefinitionOptions options;
  options.dictionaries["DefinitionData"]["height"] = AttributeLength{12.5};
  options.dictionaries["OtherData"]["count"] = std::int32_t{7};
  auto& part = builder->add_component_definition("Part", options);
  PolylineOptions edge;
  edge.dictionaries["EdgeData"]["index"] = std::int32_t{19};
  part.add_polyline({{0, 0, 0}, {2, 0, 0}}, edge);
  FaceOptions face;
  face.dictionaries["FaceData"]["enabled"] = AttributeBool{false};
  face.dictionaries["SecondData"]["name"] = std::string("value");
  part.add_face({{0, 0, 0}, {2, 0, 0}, {0, 2, 0}}, face);
  part.close();
  builder->add_instance(part);
  auto model = read(*builder);
  const auto& def = model.definitions.begin()->second;
  EXPECT_EQ(def.parsed_dictionaries.at("DefinitionData").at("height"),
            ParsedAttribute::from_length(12.5));
  EXPECT_EQ(def.parsed_dictionaries.at("OtherData").at("count"), ParsedAttribute::from_integer(7));
  const auto& f = def.faces.begin()->second;
  EXPECT_EQ(f.attribute_dictionaries.at("FaceData").at("enabled"),
            ParsedAttribute::from_boolean(false));
  EXPECT_EQ(f.attribute_dictionaries.at("SecondData").at("name").text, "value");
  int decorated = 0;
  for (const auto& e : def.edges)
    if (e.second.attribute_dictionaries.count("EdgeData")) {
      ++decorated;
      EXPECT_EQ(e.second.attribute_dictionaries.at("EdgeData").at("index"),
                ParsedAttribute::from_integer(19));
    }
  EXPECT_EQ(decorated, 1);
  for (const auto& l : model.layers)
    if (l.name == "Details")
      EXPECT_EQ(l.parsed_dictionaries.at("TagData").at("enabled"),
                ParsedAttribute::from_boolean(true));
}

TEST(IoFidelity, DefinitionBrowserMetadataAndSharedGroups) {
  auto builder = create();
  DefinitionOptions options;
  options.description = "Reusable part";
  options.path = "parts/part.skp";
  options.load_time = 1700000000;
  options.hidden = true;
  options.always_faces_camera = true;
  options.shadows_face_sun = true;
  options.thumbnail_camera = ThumbnailCamera{{8, 9, 10}, {1, 2, 3}, {0, 0, 1}, 42, false};
  auto& part = builder->add_component_definition("Part", options);
  part.add_face({{0, 0, 0}, {2, 0, 0}, {0, 2, 0}});
  part.close();
  builder->add_group_instance(part);
  GroupInstanceOptions placement;
  placement.translation = {10, 0, 0};
  placement.locked = true;
  placement.casts_shadows = false;
  placement.receives_shadows = false;
  builder->add_group_instance(part, placement);
  auto model = read(*builder);
  ASSERT_EQ(model.definitions.size(), 1u);
  const auto& def = model.definitions.begin()->second;
  EXPECT_EQ(def.description, options.description);
  EXPECT_EQ(def.path, options.path);
  EXPECT_EQ(def.load_time, options.load_time);
  EXPECT_TRUE(def.hidden);
  EXPECT_TRUE(def.is_group);
  EXPECT_TRUE(def.always_faces_camera);
  EXPECT_TRUE(def.shadows_face_sun);
  ASSERT_TRUE(def.thumbnail_camera);
  EXPECT_EQ(def.thumbnail_camera->eye, options.thumbnail_camera->eye);
  EXPECT_EQ(def.thumbnail_camera->fov_y_deg, 42);
  EXPECT_FALSE(def.thumbnail_camera->perspective);
  ASSERT_EQ(model.root().instances.size(), 2u);
  EXPECT_EQ(model.root().instances[0].ref_idx, model.root().instances[1].ref_idx);
  EXPECT_TRUE(model.root().instances[1].locked);
  EXPECT_FALSE(model.root().instances[1].casts_shadows);
  EXPECT_FALSE(model.root().instances[1].receives_shadows);
}

TEST(IoFidelity, DrawFlagsArcAxesAndHalfBoundedGuides) {
  auto builder = create();
  FaceOptions face;
  face.locked = true;
  face.casts_shadows = false;
  face.receives_shadows = false;
  builder->add_face({{0, 0, 0}, {2, 0, 0}, {0, 2, 0}}, face);
  ArcOptions arc;
  arc.xaxis = Point3{0, 1, 0};
  arc.locked = true;
  arc.casts_shadows = false;
  builder->add_arc({10, 10, 0}, {0, 0, 1}, 2, 0, 1.5, arc);
  builder->add_construction_line({0, 0, 4}, {2, 0, 0}, 3.0, 1e30);
  const auto model = read(*builder);
  const auto& f = model.root().faces.begin()->second;
  EXPECT_TRUE(f.locked);
  EXPECT_FALSE(f.casts_shadows);
  EXPECT_FALSE(f.receives_shadows);
  ASSERT_EQ(model.root().arcs.size(), 1u);
  const auto& a = model.root().arcs.begin()->second;
  EXPECT_NEAR(a.x_axis[0], 0, 1e-12);
  EXPECT_NEAR(a.x_axis[1], 2, 1e-12);
  EXPECT_NEAR(a.y_axis[0], -2, 1e-12);
  EXPECT_NEAR(a.y_axis[1], 0, 1e-12);
  ASSERT_EQ(model.root().construction_lines.size(), 1u);
  EXPECT_EQ(model.root().construction_lines[0].start, (Vec3{3, 0, 4}));
  EXPECT_FALSE(model.root().construction_lines[0].end);
}

TEST(IoFidelity, AffineArcAxesSurviveCircleAndArcRoundTrip) {
  for (const Point3 y : {Point3{0, 1.5, 0}, Point3{0.4, 1.5, 0}, Point3{0, -1, 0}}) {
    for (bool closed : {true, false}) {
      auto builder = create();
      auto& def = builder->add_component_definition("Affine arc");
      if (closed) {
        CircleOptions options;
        options.num_segments = 12;
        options.xaxis = Point3{1, 0, 0};
        options.yaxis = y;
        def.add_circle({10, 20, 0}, {0, 0, 1}, 5, options);
      } else {
        ArcOptions options;
        options.num_segments = 12;
        options.xaxis = Point3{1, 0, 0};
        options.yaxis = y;
        def.add_arc({10, 20, 0}, {0, 0, 1}, 5, 0, 3.141592653589793, options);
      }
      def.close();
      builder->add_instance(def);
      const auto model = read(*builder);
      ASSERT_EQ(model.definitions.size(), 1u);
      const auto& part = model.definitions.begin()->second;
      ASSERT_EQ(part.arcs.size(), 1u);
      const auto& arc = part.arcs.begin()->second;
      EXPECT_EQ(arc.x_axis, (Vec3{5, 0, 0}));
      for (int j = 0; j < 3; ++j) EXPECT_NEAR(arc.y_axis[j], 5*y[j], 1e-12);
      // Sampling must use the same affine basis as the serialized metadata.
      for (const auto& [id, v] : part.vertices) {
        const double sine = (v.y - 20) / (5*y[1]);
        const double cosine = (v.x - 10 - 5*y[0]*sine) / 5;
        EXPECT_NEAR(cosine*cosine + sine*sine, 1, 1e-10);
        if (!closed) EXPECT_GE(sine, -1e-10);
      }
    }
  }
}

TEST(IoFidelity, RejectsInvalidAffineArcAxesBeforeWriting) {
  for (Point3 y : {Point3{0, 0, 0}, Point3{1, 0, 0}, Point3{0, 1, 1},
                  Point3{0, std::numeric_limits<double>::infinity(), 0}}) {
    auto builder = create();
    builder->add_face({{20, 0, 0}, {22, 0, 0}, {20, 2, 0}});
    const auto before = builder->to_bytes();
    ArcOptions arc;
    arc.xaxis = Point3{1, 0, 0};
    arc.yaxis = y;
    EXPECT_THROW(builder->add_arc({0,0,0}, {0,0,1}, 5, 0, 1, arc), SkpWriteError);
    CircleOptions circle;
    circle.xaxis = arc.xaxis;
    circle.yaxis = y;
    EXPECT_THROW(builder->add_circle({0,0,0}, {0,0,1}, 5, circle), SkpWriteError);
    EXPECT_EQ(builder->to_bytes(), before);
  }
}

TEST(IoFidelity, ClosedArcKeepsExplicitVerticesAndSharesFaceBoundary) {
  auto builder = create();
  DocumentOptions document;
  document.next_persistent_id = 100;
  builder->set_document_options(document);
  // Deliberately rounded coordinates from an edited circular boundary.
  const std::vector<Point3> points{{1, 0, 0}, {-0.5, 0.866025, 0}, {-0.5, -0.866025, 0}};
  builder->set_geometry_persistent_ids({{points[0], 21}, {points[1], 22}, {points[2], 23}}, {});
  ArcOptions arc;
  arc.num_segments = 3;
  arc.vertices = points;
  arc.vertices.push_back(points.front());
  builder->add_arc({0, 0, 0}, {0, 0, 1}, 1, 0, 6.283185307179586, arc);
  builder->add_face(points);
  const auto model = read(*builder);
  EXPECT_EQ(model.root().vertices.size(), 3u);
  EXPECT_EQ(model.root().edges.size(), 3u);
  ASSERT_EQ(model.root().faces.size(), 1u);
  std::set<EntityId> boundary;
  for (const auto& edge : model.root().faces.begin()->second.loops.front())
    boundary.insert(edge.edge_id);
  EXPECT_EQ(boundary.size(), model.root().edges.size());
  for (const auto& [id, vertex] : model.root().vertices) {
    ASSERT_GE(vertex.persistent_id, 21u);
    ASSERT_LE(vertex.persistent_id, 23u);
    EXPECT_EQ((Point3{vertex.x, vertex.y, vertex.z}), points[vertex.persistent_id - 21]);
  }
}

TEST(IoFidelity, ValidatesIdentityReservationAndGuideInputsBeforeWriting) {
  auto builder = create();
  EXPECT_THROW(builder->set_next_persistent_id("CFace", 1), SkpWriteError);
  DocumentOptions document;
  document.next_persistent_id = std::numeric_limits<std::uint64_t>::max();
  EXPECT_THROW(builder->set_document_options(document), SkpWriteError);
  document.next_persistent_id = 100;
  builder->set_document_options(document);
  EXPECT_THROW(builder->set_next_persistent_id("CFace", 100), SkpWriteError);
  EXPECT_THROW(builder->set_next_persistent_id("Typo", 12), SkpWriteError);
  EXPECT_THROW(builder->add_construction_line({0, 0, 0}, {0, 0, 0}, 0, 1), SkpWriteError);
  EXPECT_THROW(builder->add_construction_line({0, 0, 0}, {1, 0, 0}, 0,
                                              std::numeric_limits<double>::quiet_NaN()),
               SkpWriteError);
  builder->add_construction_point({1, 2, 3});
  EXPECT_EQ(read(*builder).root().construction_points.size(), 1u);
}

TEST(IoFidelity, NonPlanarFallbackKeepsFaceDrawFlags) {
  auto builder = create();
  FaceOptions options;
  options.auto_triangulate = true;
  options.locked = true;
  options.casts_shadows = false;
  options.receives_shadows = false;
  builder->add_face({{0, 0, 0}, {2, 0, 0}, {2, 2, 0.2}, {0, 2, 0}}, options);
  const auto model = read(*builder);
  ASSERT_EQ(model.root().faces.size(), 2u);
  for (const auto& [id, face] : model.root().faces) {
    EXPECT_TRUE(face.locked);
    EXPECT_FALSE(face.casts_shadows);
    EXPECT_FALSE(face.receives_shadows);
  }
}

TEST(IoFidelity, DuplicateDefaultLayerRecordsExposeAllNativeIdentities) {
  auto builder = create();
  DocumentOptions document;
  document.next_persistent_id = 100;
  builder->set_document_options(document);
  builder->set_next_persistent_id("CLayer", 31);
  builder->add_layer("Layer0");
  builder->add_construction_point({1, 2, 3});
  const auto model = read(*builder);
  const auto layer = std::find_if(model.layers.begin(), model.layers.end(),
                                  [](const Layer& value) { return value.name == "Layer0"; });
  ASSERT_NE(layer, model.layers.end());
  EXPECT_EQ(layer->persistent_id_aliases, (std::vector<std::uint64_t>{31, 103}));
}

}  // namespace
}  // namespace openskp
