// Generator for every *.skp file in this directory.
//
// Runs fixed writer scenarios through the reference C++ writer (openskp/packages/cpp
// src/create.cpp) and dumps each result to <outdir>/<name>.skp. tests/create_reference_test.rs
// replays the same scenarios through the Rust port and asserts byte equality.
//
// To regenerate, from the repository root:
//
//   python packages/rust/tests/fixtures/create_reference/regenerate.py
//
// It builds this driver against a copy of packages/cpp/src/create.cpp patched to be reproducible
// - `random_uuid_bytes()` returns GUID n (a global counter `oracle_guid_counter`, reset per
// scenario) as bytes (n*16 + i) & 0xFF with the v4 version/variant bits applied, and
// `std::time(nullptr)` becomes 1700000000, which is exactly what
// `SkpBuilder::use_deterministic_ids(1700000000)` does on the Rust side - then runs it from the
// crate root (texture paths are relative and embedded verbatim in the files).
//
// The files are generated with the C++ writer of openskp v1.5.0. (Run against v1.4.0, this same
// driver reproduces the earlier files byte for byte, minus the 1.5.0-only scenarios at the end.)
//
// Up to 1.4.0, single_face, multi_face_shared_edge, materials_and_layers, holes,
// auto_triangulate, circle, arc, polyline and slot_boundary were also byte-identical to the Python
// writer's references (packages/dart/test/fixtures/create_reference), and attributes.skp differed
// from Python's only in key order. 1.5.0 changed the C++ writer's persistent ids, which Python
// does not share, so that no longer holds.
#include <cmath>
#include <cstdio>
#include <fstream>
#include <functional>
#include <iostream>
#include <string>
#include <vector>

#include <openskp/create.hpp>

extern std::uint32_t oracle_guid_counter;

using namespace openskp;

static std::string g_out;
static const char* kPng = "tests/fixtures/create_reference/fake.png";
static const char* kJpg = "tests/fixtures/create_reference/fake.jpg";
constexpr double kPi = 3.14159265358979323846;

static void run(const std::string& name, const std::function<void(SkpBuilder&)>& fn) {
  oracle_guid_counter = 0;
  auto b = create();
  try {
    fn(*b);
    ByteBuffer bytes = b->to_bytes();
    std::ofstream f(g_out + "/" + name + ".skp", std::ios::binary);
    f.write(reinterpret_cast<const char*>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
    std::cout << name << " " << bytes.size() << "\n";
  } catch (const std::exception& e) {
    std::cout << name << " ERROR " << e.what() << "\n";
  }
}

static std::vector<Point3> sq(double s = 100, double z = 0) {
  return {{0, 0, z}, {s, 0, z}, {s, s, z}, {0, s, z}};
}

int main(int argc, char** argv) {
  g_out = argc > 1 ? argv[1] : ".";

  run("single_face", [](SkpBuilder& b) { b.add_face(sq()); });

  run("multi_face_shared_edge", [](SkpBuilder& b) {
    b.add_face({{0, 0, 0}, {100, 0, 0}, {100, 100, 0}, {0, 100, 0}});
    b.add_face({{100, 0, 0}, {200, 0, 0}, {200, 100, 0}, {100, 100, 0}});
  });

  run("materials_and_layers", [](SkpBuilder& b) {
    int red = b.add_material("Red", Color3{255, 0, 0});
    int blue = b.add_material("Blue", Color4{0, 0, 255, 128});
    LayerOptions lo;
    lo.color = Color4{10, 20, 30, 255};
    int roof = b.add_layer("Roof", lo);
    LayerOptions lo2;
    lo2.hidden = true;
    int walls = b.add_layer("Walls", lo2);
    FaceOptions f1;
    f1.material = red;
    f1.layer = roof;
    b.add_face(sq(), f1);
    FaceOptions f2;
    f2.material = blue;
    f2.back_material = red;
    f2.layer = walls;
    b.add_face(sq(100, 10), f2);
  });

  run("holes", [](SkpBuilder& b) {
    FaceOptions o;
    o.holes = {{{80, 30, 0}, {120, 30, 0}, {120, 70, 0}, {80, 70, 0}}};
    b.add_face({{0, 0, 0}, {200, 0, 0}, {200, 100, 0}, {0, 100, 0}}, o);
  });

  run("auto_triangulate", [](SkpBuilder& b) {
    FaceOptions o;
    o.auto_triangulate = true;
    b.add_face({{0, 0, 0}, {10, 0, 0}, {10, 10, 0}, {0, 10, 5}}, o);
  });

  run("circle", [](SkpBuilder& b) {
    CircleOptions o;
    o.num_segments = 12;
    b.add_circle({50, 50, 0}, {0, 0, 1}, 40.0, o);
  });

  run("arc", [](SkpBuilder& b) {
    ArcOptions o;
    o.num_segments = 8;
    b.add_arc({50, 50, 0}, {0, 0, 1}, 40.0, 0.0, kPi / 2, o);
  });

  run("polyline", [](SkpBuilder& b) {
    PolylineOptions o;
    o.closed = true;
    b.add_polyline({{0, 0, 0}, {10, 10, 0}, {20, 0, 0}, {30, 10, 0}}, o);
  });

  run("attributes", [](SkpBuilder& b) {
    FaceOptions o;
    o.attributes["part_number"] = std::string{"A-100"};
    o.attributes["count"] = std::int32_t{7};
    o.attributes["weight"] = 3.5;
    b.add_face(sq(), o);
  });

  run("slot_boundary", [](SkpBuilder& b) {
    for (int i = 0; i < 3200; ++i) {
      double x = i * 10.0;
      b.add_face({{x, 0.0, 0.0}, {x + 1.0, 0.0, 0.0}, {x, 1.0, 0.0}});
    }
  });

  run("textures", [](SkpBuilder& b) {
    int t1 = b.add_texture_material("Brick", kPng);
    int t2 = b.add_texture_material("Tile", kPng, 48.0);
    int t3 = b.add_texture_material("Wood", kJpg, 16.0, 8.0);
    int t4 = b.add_texture_material("Voile", kPng, std::nullopt, std::nullopt, 0.5);
    int glass = b.add_material("Glass", Color4{200, 220, 255, 255}, 0.35);
    int dup = b.add_material("Brick", Color3{1, 2, 3});
    (void)dup;
    FaceOptions o1;
    o1.material = t1;
    b.add_face(sq(), o1);
    FaceOptions o2;
    o2.material = t2;
    o2.back_material = t3;
    b.add_face(sq(100, 10), o2);
    FaceOptions o3;
    o3.material = t4;
    o3.back_material = glass;
    b.add_face(sq(100, 20), o3);
  });

  run("uv_positioning", [](SkpBuilder& b) {
    int brick = b.add_material("Brick", Color3{150, 100, 50});
    int tex = b.add_texture_material("Tex", kPng, 10.0, 10.0);
    int tex2 = b.add_texture_material("Tex2", kPng);
    FaceOptions o;
    o.material = brick;
    o.front_uv = UvCorrespondence{{Point3{0, 0, 0}, {0.0, 0.0}},
                                  {Point3{50, 0, 0}, {1.0, 0.0}},
                                  {Point3{0, 50, 0}, {0.0, 1.0}}};
    b.add_face(sq(), o);
    FaceOptions o2;
    o2.material = tex;
    o2.back_material = tex;
    o2.front_uv = o.front_uv;
    o2.back_uv = UvCorrespondence{{Point3{0, 0, 5}, {0.0, 0.0}},
                                  {Point3{20, 0, 5}, {1.0, 0.0}},
                                  {Point3{0, 30, 5}, {0.0, 1.0}}};
    b.add_face({{100, 0, 5}, {100, 100, 5}, {0, 100, 5}, {0, 0, 5}}, o2);
    FaceOptions o3;
    o3.material = tex2;
    o3.front_uv = UvCorrespondence{{Point3{0, 0, 0}, {0.0, 0.0}},
                                   {Point3{2, 0, 0}, {1.0, 0.0}},
                                   {Point3{0, 2, 0}, {0.0, 1.0}}};
    b.add_face({{0, 2, -5}, {2, 2, -5}, {2, 0, -5}, {0, 0, -5}}, o3);
    constexpr double s = 70.71067811865476;
    FaceOptions o4;
    o4.material = tex2;
    o4.back_uv = UvCorrespondence{{Point3{0, 0, 0}, {0.0, 0.0}},
                                  {Point3{100, 0, 0}, {1.0, 0.0}},
                                  {Point3{0, s, s}, {0.0, 1.0}}};
    o4.attributes["k"] = std::string{"v"};
    b.add_face({{0, 0, 0}, {100, 0, 0}, {100, s, s}, {0, s, s}}, o4);
    // vertical face (tilt sine ~0 path is horizontal; this is the non-horizontal branch)
    FaceOptions o5;
    o5.material = tex2;
    o5.front_uv = UvCorrespondence{{Point3{0, 0, 0}, {0.0, 0.0}},
                                   {Point3{10, 0, 0}, {1.0, 0.0}},
                                   {Point3{0, 0, 10}, {0.0, 1.0}}};
    b.add_face({{0, 0, 0}, {10, 0, 0}, {10, 0, 10}, {0, 0, 10}}, o5);
  });

  run("components", [](SkpBuilder& b) {
    int red = b.add_material("Red", Color3{255, 0, 0});
    int lay = b.add_layer("Furniture");
    DefinitionOptions dopts;
    dopts.attributes["material_code"] = std::string{"ABC-123"};
    dopts.attributes["flag"] = AttributeBool{true};
    auto& chair = b.add_component_definition("Chair", dopts);
    FaceOptions fo;
    fo.attributes["area_sqft"] = 12.5;
    fo.material = red;
    chair.add_face({{0, 0, 0}, {20, 0, 0}, {20, 20, 0}, {0, 20, 0}}, fo);
    chair.add_face({{0, 0, 0}, {20, 0, 0}, {20, 0, 20}, {0, 0, 20}});
    chair.close();
    InstanceOptions a;
    a.translation = {100, 0, 0};
    b.add_instance(chair, a);
    InstanceOptions c;
    c.translation = {200, 0, 0};
    c.name = "Second Chair";
    c.rotation = Rotation{{0, 0, 1}, kPi / 2};
    c.material = red;
    c.layer = lay;
    c.hidden = true;
    c.attributes["visible"] = AttributeBool{true};
    c.attributes["hidden"] = AttributeBool{false};
    c.attributes["depth"] = AttributeLength{15.5};
    c.attributes["weight"] = 3.5;
    c.attributes["count"] = std::int32_t{-42};
    c.attributes["label"] = std::string{"widget \xC3\xA9\xF0\x9F\x98\x80"};
    b.add_instance(chair, c);
    InstanceOptions d;
    d.matrix3x3 = Matrix3x3{2, 0, 0, 0, 2, 0, 0, 0, 2};
    d.translation = {0, 300, 0};
    b.add_instance(chair, d);
    b.add_face(sq(10, -1));
  });

  run("groups", [](SkpBuilder& b) {
    int red = b.add_material("Red", Color3{255, 0, 0});
    int lay = b.add_layer("L1");
    GroupOptions g;
    g.name = "Table";
    g.translation = {50, 0, 0};
    g.attributes["sku"] = std::string{"TBL-1"};
    g.attributes["qty"] = std::int32_t{2};
    auto& table = b.add_group(g);
    table.add_face({{0, 0, 0}, {30, 0, 0}, {30, 30, 0}, {0, 30, 0}});
    table.close();
    GroupOptions g2;
    g2.rotation = Rotation{{1, 1, 0}, 0.3};
    g2.material = red;
    g2.layer = lay;
    g2.hidden = true;
    auto& g2b = b.add_group(g2);
    g2b.add_face({{0, 0, 0}, {5, 0, 0}, {5, 5, 0}});
    g2b.close();
    auto& chair = b.add_component_definition("Chair");
    chair.add_face(sq());
    chair.close();
    b.add_instance(chair);
  });

  run("groups_only", [](SkpBuilder& b) {
    auto& t = b.add_group();
    t.add_face(sq(10));
    t.close();
  });

  run("nested", [](SkpBuilder& b) {
    auto& wheel = b.add_component_definition("Wheel");
    wheel.add_face({{0, 0, 0}, {10, 0, 0}, {10, 10, 0}, {0, 10, 0}});
    wheel.close();
    auto& engine = b.add_component_definition("Engine");
    engine.add_face({{0, 0, 0}, {30, 0, 0}, {30, 30, 0}, {0, 30, 0}});
    engine.close();
    auto& car = b.add_component_definition("Car");
    car.add_face({{0, 0, 0}, {150, 0, 0}, {150, 60, 0}, {0, 60, 0}});
    InstanceOptions wi;
    wi.name = "LeafInstance";
    wi.attributes["n"] = std::int32_t{1};
    car.add_instance(wheel, wi);
    GroupInstanceOptions eg;
    eg.translation = {50, 0, 10};
    eg.attributes["part"] = std::string{"V6"};
    eg.rotation = Rotation{{0, 1, 0}, 1.0};
    car.add_group_instance(engine, eg);
    car.add_circle({0, 0, 5}, {0, 0, 1}, 3.0);
    car.add_arc({0, 0, 6}, {1, 0, 0}, 2.0, 0.0, kPi);
    car.add_polyline({{0, 0, 7}, {1, 0, 7}, {1, 1, 7}});
    car.add_construction_point({4.0, 5.0, 6.0});
    car.add_construction_line({1.0, 1.0, 1.0}, Point3{2.0, 2.0, 2.0});
    car.add_construction_line({1.0, 1.0, 1.0}, std::nullopt, Point3{0.0, 3.0, 0.0});
    car.close();
    b.add_instance(car);
    b.add_construction_point({1.0, 2.0, 3.0});
    b.add_construction_line({0.0, 0.0, 0.0}, Point3{12.0, 0.0, 0.0});
    b.add_construction_line({0.0, 0.0, 4.0}, std::nullopt, Point3{0.0, 0.0, 1.0});
  });

  run("image", [](SkpBuilder& b) {
    ImageOptions o;
    o.translation = {0, 0, 40};
    o.rotation = Rotation{{1, 0, 0}, kPi / 2};
    o.hidden = true;
    b.add_image(kPng, 48, 36, o);
    b.add_face(sq(10));
  });

  run("annotations", [](SkpBuilder& b) {
    b.add_face(sq(10));
    b.add_section_plane({10, 20, 30}, {0, 0, 1});
    b.add_section_plane({0, 0, 5}, {0, 3, 2});
    b.add_text("Hello", {5, 5, 5});
    b.add_dimension({0, 0, 0}, {10, 0, 0});
    b.add_dimension({0, 5, 0}, {10, 5, 0}, -3.5);
    b.add_text("Unicode \xC3\xA9\xF0\x9F\x98\x80", {1, 1, 1}, {1, 2, 3});
    b.add_construction_point({1, 2, 3});
    b.add_construction_line({0, 0, 0}, Point3{10, 0, 0});
    b.add_construction_line({0, 0, 0}, std::nullopt, Point3{0, 0, 1});
  });

  run("layers_extra", [](SkpBuilder& b) {
    b.add_material("M1", Color3{1, 2, 3});
    b.add_material("M2", Color4{4, 5, 6, 7});
    b.add_material("M3", Color3{8, 9, 10}, 0.25);
    LayerOptions lo;
    lo.extra_dictionaries["plugin"]["n"] = std::int32_t{7};
    lo.extra_dictionaries["plugin"]["s"] = std::string{"x"};
    lo.extra_dictionaries["another"]["len"] = AttributeLength{2.5};
    lo.color = Color4{1, 2, 3, 4};
    b.add_layer("Walls", lo);
    b.add_layer("Roof");
    b.add_layer("Walls");
    b.add_layer("Hidden", LayerOptions{std::nullopt, true, {}});
    b.add_face({{0, 0, 0}, {10, 0, 0}, {10, 10, 0}, {0, 10, 0}});
  });

  run("edge_flags", [](SkpBuilder& b) {
    FaceOptions o;
    o.hidden = true;
    o.soft_edges = true;
    o.smooth_edges = true;
    o.hidden_edges = true;
    b.add_face(sq(), o);
    FaceOptions o2;
    o2.soft_edges = true;
    b.add_face({{100, 0, 0}, {200, 0, 0}, {200, 100, 0}, {100, 100, 0}}, o2);
    ArcOptions ao;
    ao.hidden_edges = true;
    ao.soft_edges = true;
    ao.smooth_edges = true;
    ao.num_segments = 5;
    b.add_arc({0, 0, 50}, {0, 1, 1}, 10.0, 1.0, -2.5, ao);
    PolylineOptions po;
    po.smooth_edges = true;
    b.add_polyline({{0, 0, 60}, {5, 0, 60}, {5, 5, 61}}, po);
    CircleOptions co;
    co.num_segments = 7;
    co.hidden = true;
    co.attributes["r"] = 3.0;
    co.attribute_dict_name = "dims";
    co.front_uv = UvCorrespondence{{Point3{0, 0, 70}, {0.0, 0.0}},
                                   {Point3{1, 0, 70}, {1.0, 0.0}},
                                   {Point3{0, 1, 70}, {0.0, 1.0}}};
    b.add_circle({0, 0, 70}, {0, 0, 2}, 4.0, co);
    CircleOptions co2;
    co2.num_segments = 3;
    b.add_circle({0, 0, 80}, {1, 0, 0}, 4.0, co2);
  });

  run("large_triangulate", [](SkpBuilder& b) {
    FaceOptions o;
    o.auto_triangulate = true;
    o.attributes["tri"] = std::int32_t{1};
    b.add_face({{0, 0, 0}, {10, 0, 0}, {12, 5, 3}, {10, 10, 0}, {0, 10, 1}}, o);
    // shared vertex with the triangulated one
    b.add_face({{0, 0, 0}, {10, 0, 0}, {5, -5, 0}});
  });

  run("group_in_definition_flag", [](SkpBuilder& b) {
    auto& engine = b.add_component_definition("Engine");
    engine.add_face(sq());
    engine.close();
    auto& car = b.add_component_definition("Car");
    car.add_face(sq());
    GroupInstanceOptions g;
    g.translation = {50, 0, 10};
    car.add_group_instance(engine, g);
    car.close();
    b.add_instance(car);
    b.add_instance(engine);
  });

  run("many_slots_materials", [](SkpBuilder& b) {
    std::vector<int> mats;
    for (int i = 0; i < 40; ++i)
      mats.push_back(b.add_material("Mat" + std::to_string(i), Color3{static_cast<std::uint8_t>(i), 0, 0}));
    for (int i = 0; i < 10; ++i) b.add_layer("Layer" + std::to_string(i));
    auto& d = b.add_component_definition("D");
    d.add_face(sq());
    d.close();
    for (int i = 0; i < 5; ++i) {
      InstanceOptions o;
      o.translation = {i * 1.0, 0, 0};
      o.material = mats[static_cast<std::size_t>(i * 7)];
      b.add_instance(d, o);
    }
  });

  run("image_jpeg_with_material", [](SkpBuilder& b) {
    b.add_material("Red", Color3{255, 0, 0});
    ImageOptions o;
    o.matrix3x3 = Matrix3x3{1, 0, 0, 0, 0, -1, 0, 1, 0};
    b.add_image(kJpg, 10, 5, o);
  });

  // ---- openskp 1.5.0 writer features ----

  run("guide_options", [](SkpBuilder& b) {
    const int guides = b.add_layer("Guides");
    auto& d = b.add_component_definition("GuideDef");
    d.add_face(sq(5));
    d.add_construction_point({1, 1, 1}, GuideOptions{guides, true});
    d.add_construction_line({0, 0, 0}, Point3{5, 0, 0}, std::nullopt, GuideOptions{std::nullopt, true});
    d.close();
    b.add_instance(d);
    b.add_face(sq(10));
    b.add_construction_point({1, 2, 3}, GuideOptions{std::nullopt, true});
    b.add_construction_point({4, 5, 6}, GuideOptions{guides, false});
    b.add_construction_line({0, 0, 0}, Point3{10, 0, 0}, std::nullopt, GuideOptions{guides, true});
    b.add_construction_line({0, 0, 4}, std::nullopt, Point3{0, 0, 1});
  });

  run("document_options", [](SkpBuilder& b) {
    DocumentOptions o;
    o.length_unit = 2;
    o.length_precision = 1;
    o.style_items = {{4000, 0xff563412u}, {1006, 0}, {1007, 5}, {1000, 3}, {2004, 1}};
    b.set_document_options(o);
    b.add_face(sq(10));
  });

  run("dimension_vectors", [](SkpBuilder& b) {
    auto& d = b.add_component_definition("Dims");
    d.add_face(sq(5));
    d.add_dimension({0, 0, 0}, {5, 0, 0}, 2.5);
    d.add_dimension({0, 0, 0}, {5, 0, 0}, Point3{0, 0, 4});
    d.close();
    b.add_instance(d);
    b.add_face(sq(10));
    b.add_dimension({1, 2, 3}, {11, 2, 3}, Point3{0, 0, 4});
    b.add_dimension({11, 2, 3}, {1, 2, 3}, Point3{0, 0, -4});
    b.add_dimension({1, 2, 3}, {1, 2, 13}, Point3{-4, 0, 0});
    b.add_dimension({1, 2, 3}, {1, 2, 13}, Point3{0, 0, 0});
    b.add_dimension({1, 2, 3}, {11, 2, 3}, Point3{7, -4, 0});
  });

  return 0;
}
