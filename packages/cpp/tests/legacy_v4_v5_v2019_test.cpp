#include <algorithm>
#include <cmath>
#include <gtest/gtest.h>
#include <iomanip>
#include <set>
#include <sstream>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

namespace openskp {
namespace {

// Port of the Python reader's fixes for SketchUp 4, 5 and 2019 files
// (openskp#284, #440) to C++.
//
// Fixtures are small synthetic models, saved down through the installed
// SketchUp library from the same sources as the existing legacy fixtures
// (legacy_v6_synthetic.skp and the one-face textured model behind
// legacy_v3_textured.skp); no private content:
//
//  * legacy_v4/v5/v2019_synthetic.skp: 4 definitions (InnerFrame, Frame,
//    Panel, Post), 10 root instances, a "Roof" layer, 24 materials.
//  * legacy_v4/v5_textured.skp: one painted face with an 8x8 PNG, stored as a
//    bare inline image before SketchUp 6 (not a CDib object).
//  * legacy_v2020_textured.skp: the same model at SketchUp 2020 - a model with
//    NO component definitions, whose root entities used to be read as
//    "definitions" and silently dropped.
//
// Parsing without an exception is not enough (an early V3 attempt "succeeded"
// with one definition of four), so these assert parsed structure and
// cross-check it against the V6 file from the same source.

SkpModel parse(const char* name) { return SkpFile::open(test::fixture(name)).parse(); }

std::string num(double x) {
  std::ostringstream os;
  os << std::fixed << std::setprecision(5) << (x == 0 ? 0.0 : x);
  return os.str();
}

std::string matrix_text(const std::vector<double>& m) {
  std::string out;
  for (double x : m) out += num(x) + ",";
  return out;
}

// Everything that matters about a model, keyed by names and coordinates (ids
// differ between versions).
std::string canonical(const SkpModel& m, bool instance_names) {
  std::map<EntityId, std::string> mat_name;
  for (const auto& x : m.materials)
    if (x.id) mat_name[*x.id] = x.name;
  auto def_name = [&](const std::optional<EntityId>& idx) -> std::string {
    if (!idx) return "?";
    auto it = m.definitions.find(*idx);
    return it == m.definitions.end() ? "?" : it->second.name;
  };
  auto inst_text = [&](const Instance& i) {
    return def_name(i.ref_idx) + "|" + matrix_text(i.matrix) + "|" +
           (instance_names ? i.name : std::string());
  };
  auto joined = [](std::vector<std::string> v) {
    std::sort(v.begin(), v.end());
    std::string out;
    for (const auto& s : v) out += s + ";";
    return out;
  };

  std::string out = "layers:";
  {
    std::vector<std::string> names;
    for (const auto& l : m.layers) names.push_back(l.name);
    out += joined(names) + "materials:";
    names.clear();
    for (const auto& x : m.materials) names.push_back(x.name);
    out += joined(names);
  }

  std::vector<const Definition*> defs;
  for (const auto& [id, d] : m.definitions) defs.push_back(&d);
  std::sort(defs.begin(), defs.end(),
            [](const Definition* a, const Definition* b) { return a->name < b->name; });
  for (const auto* d : defs) {
    std::vector<std::string> verts, faces, insts;
    for (const auto& [id, v] : d->vertices)
      verts.push_back(num(v.x) + "," + num(v.y) + "," + num(v.z));
    for (const auto& [id, f] : d->faces) {
      std::string mat = "-";
      if (f.material_id && mat_name.count(*f.material_id)) mat = mat_name[*f.material_id];
      faces.push_back(mat + "/" + (f.hidden ? "1" : "0"));
    }
    for (const auto& i : d->instances) insts.push_back(inst_text(i));
    out += "def " + d->name + ":v=" + joined(verts) + " e=" + std::to_string(d->edges.size()) +
           " f=" + joined(faces) + " i=" + joined(insts) + "|";
  }
  std::vector<std::string> root;
  for (const auto& i : m.root().instances) root.push_back(inst_text(i));
  out += "root:" + joined(root);
  return out;
}

struct SyntheticCase {
  const char* file;
  const char* version;
  bool instance_names;  // SketchUp 4 stores none
};

const SyntheticCase kSynthetic[] = {
    {"legacy_v4_synthetic.skp", "{4.0.1}", false},
    {"legacy_v5_synthetic.skp", "{5.0.1}", true},
    {"legacy_v2019_synthetic.skp", "{19.0.1}", true},
};

TEST(LegacyV4V5V2019, ParseAndReportTheirVersion) {
  for (const auto& c : kSynthetic) {
    EXPECT_EQ(parse(c.file).version, c.version) << c.file;
  }
}

TEST(LegacyV4V5V2019, MatchTheV6FileFromTheSameSource) {
  for (const auto& c : kSynthetic) {
    EXPECT_EQ(canonical(parse(c.file), c.instance_names),
              canonical(parse("legacy_v6_synthetic.skp"), c.instance_names))
        << c.file;
  }
}

TEST(LegacyV4V5V2019, AreCompleteNotJustErrorFree) {
  for (const auto& c : kSynthetic) {
    auto model = parse(c.file);
    std::map<std::string, const Definition*> defs;
    for (const auto& [id, d] : model.definitions) defs[d.name] = &d;
    std::set<std::string> names;
    for (const auto& [name, d] : defs) {
      names.insert(name);
      EXPECT_EQ(d->faces.size(), 1u) << c.file << " " << name;
      EXPECT_EQ(d->edges.size(), 4u) << c.file << " " << name;
      EXPECT_EQ(d->vertices.size(), 4u) << c.file << " " << name;
    }
    EXPECT_EQ(names, (std::set<std::string>{"InnerFrame", "Frame", "Panel", "Post"})) << c.file;
    EXPECT_EQ(model.root().instances.size(), 10u) << c.file;
    EXPECT_EQ(model.materials.size(), 24u) << c.file;
    std::set<std::string> layers;
    for (const auto& l : model.layers) layers.insert(l.name);
    EXPECT_TRUE(layers.count("Layer0") && layers.count("Roof")) << c.file;
    const auto& panel = defs.at("Panel")->instances;
    ASSERT_EQ(panel.size(), 1u) << c.file;
    EXPECT_EQ(model.definitions.at(*panel[0].ref_idx).name, "InnerFrame") << c.file;
  }
}

TEST(LegacyV4V5V2019, SketchUp4HasNoInstanceNamesAndSketchUp5Does) {
  auto v6 = parse("legacy_v6_synthetic.skp");
  auto v4 = parse("legacy_v4_synthetic.skp");
  auto v5 = parse("legacy_v5_synthetic.skp");
  EXPECT_TRUE(std::any_of(v6.root().instances.begin(), v6.root().instances.end(),
                          [](const Instance& i) { return !i.name.empty(); }));
  for (const auto& i : v4.root().instances) EXPECT_EQ(i.name, "");
  ASSERT_EQ(v5.root().instances.size(), v6.root().instances.size());
  for (size_t k = 0; k < v5.root().instances.size(); ++k)
    EXPECT_EQ(v5.root().instances[k].name, v6.root().instances[k].name) << k;
}

const Texture* texture_of(const SkpModel& m) {
  for (const auto& x : m.materials)
    if (x.texture && x.texture->data) return &*x.texture;
  return nullptr;
}

TEST(LegacyV4V5V2019, SketchUp4And5ReadTheBareInlineTexture) {
  // Was: texture object is not a dib.
  auto reference = parse("legacy_v3_textured.skp");
  const Texture* ref_tex = texture_of(reference);
  ASSERT_NE(ref_tex, nullptr);
  for (const char* file : {"legacy_v4_textured.skp", "legacy_v5_textured.skp"}) {
    auto model = parse(file);
    const Texture* tex = texture_of(model);
    ASSERT_NE(tex, nullptr) << file;
    EXPECT_EQ(*tex->data, *ref_tex->data) << file;  // the same PNG, byte for byte
    ASSERT_GE(tex->data->size(), 4u);
    EXPECT_EQ((*tex->data)[0], 0x89) << file;
    EXPECT_EQ((*tex->data)[1], 'P') << file;
    EXPECT_EQ(model.root().faces.size(), 1u) << file;
  }
}

TEST(LegacyV4V5V2019, SketchUp2020ModelWithoutDefinitionsKeepsItsRootEntities) {
  // The root entity count was handed over as a "definition count", so the face
  // and its four edges were consumed as definitions and the model came out
  // empty - with no error.
  auto model = parse("legacy_v2020_textured.skp");
  EXPECT_EQ(model.root().faces.size(), 1u);
  EXPECT_EQ(model.root().edges.size(), 4u);
  EXPECT_EQ(model.root().vertices.size(), 4u);
  EXPECT_NE(texture_of(model), nullptr);
}

}  // namespace
}  // namespace openskp
