#include <algorithm>
#include <gtest/gtest.h>
#include <iostream>
#include <limits>
#include <locale>
#include <set>
#include <sstream>
#include <stdexcept>
#include <vector>

#include "openskp/dxf_export.hpp"

namespace {
static void check(bool ok, const char* message) {
  if (!ok) throw std::runtime_error(message);
}

static openskp::GlbPrimitive triangle(const std::string& name) {
  openskp::GlbPrimitive p;
  p.geom_name = name;
  p.positions = {0, 0, 0, 1, 0, 0, 0, 1, 0};
  p.indices = {0, 1, 2};
  return p;
}

struct Parsed {
  std::set<std::string> layers;
  std::vector<std::string> meshLayers;
};

static Parsed inspect(const std::string& dxf) {
  check(std::all_of(dxf.begin(), dxf.end(), [](unsigned char c) { return c < 128; }),
        "R2000 DXF contains raw non-ASCII bytes instead of Unicode escapes");
  std::istringstream in(dxf);
  std::string code, value, record;
  Parsed parsed;
  std::vector<std::string> references;
  std::size_t tableCount = 0;
  bool layerTable = false;
  bool entity = false;
  while (std::getline(in, code)) {
    check(bool(std::getline(in, value)), "incomplete DXF code/value pair");
    if (!value.empty() && value.back() == '\r') value.pop_back();
    const int group = std::stoi(code);
    if (group >= 60 && group <= 79)
      check(std::stol(value) >= -32768 && std::stol(value) <= 32767, "16-bit DXF field overflow");
    if (group == 0) {
      record = value;
      entity = false;
    }
    if (group == 100 && value == "AcDbEntity") entity = true;
    if (record == "TABLE" && group == 2) layerTable = value == "LAYER";
    if (record == "TABLE" && layerTable && group == 70) tableCount = std::stoul(value);
    if (record == "LAYER" && group == 2) {
      check(parsed.layers.insert(value).second, "duplicate LAYER name");
      std::u16string decoded;
      for (std::size_t i = 0; i < value.size();) {
        if (value[i] == '\\') {
          check(value.substr(i, 3) == "\\U+" && i + 7 <= value.size(), "bad Unicode escape");
          decoded.push_back(static_cast<char16_t>(std::stoul(value.substr(i + 3, 4), nullptr, 16)));
          i += 7;
        } else {
          check(value[i] >= 32 && value[i] != 127, "control character in LAYER");
          decoded.push_back(value[i++]);
        }
      }
      check(!decoded.empty() && decoded.size() <= 255, "invalid logical layer name length");
      for (std::size_t i = 0; i < decoded.size(); ++i) {
        if (decoded[i] >= 0xD800 && decoded[i] <= 0xDBFF) {
          check(++i < decoded.size() && decoded[i] >= 0xDC00 && decoded[i] <= 0xDFFF,
                "split Unicode surrogate pair");
        } else
          check(decoded[i] < 0xDC00 || decoded[i] > 0xDFFF, "unpaired low surrogate");
      }
    }
    if (group == 8 && entity) {
      references.push_back(value);
      if (record == "POLYLINE" || record == "3DFACE") parsed.meshLayers.push_back(value);
    }
  }
  check(tableCount == parsed.layers.size(), "incorrect LAYER table count");
  for (const auto& ref : references)
    check(parsed.layers.count(ref), "entity refers to missing layer");
  return parsed;
}

struct CommaNumbers : std::numpunct<char> {
  char do_decimal_point() const override { return ','; }

  char do_thousands_sep() const override { return '.'; }

  std::string do_grouping() const override { return "\3"; }
};

struct ScopedLocale {
  std::locale previous = std::locale();

  ScopedLocale() { std::locale::global(std::locale(previous, new CommaNumbers)); }

  ~ScopedLocale() { std::locale::global(previous); }
};

void verify_dxf_contract() {
  const std::string chinese =
      u8"mesh_27_ROOT__\u4e0a\u67dc(\u4fa7\u5305\u9876\u5e95)__\u9876\u677f_\u540a\u67dc";
  const std::string escaped =
      "mesh_27_ROOT__\\U+4E0A\\U+67DC(\\U+4FA7\\U+5305\\U+9876\\U+5E95)__\\U+9876\\U+677F_\\U+"
      "540A\\U+67DC";
  std::string longName;
  for (int i = 0; i < 260; ++i) longName += u8"\u67dc";
  openskp::Scene scene;
  for (const auto& name : std::vector<std::string>{
           chinese, u8"Caf\u00e9", u8"\u67dc\U0001f600", longName + "A", longName + "B",
           std::string(245, 'x') + u8"\U0001f600" + std::string(20, 'x'), "A/B", "A:B", "walls",
           "WALLS", "0", "Defpoints", "", "\t ", "line\nbreak\tname", chinese})
    scene.glb_primitives.push_back(triangle(name));
  for (const auto* mode : {"polyface", "3dface"}) {
    const auto dxf = openskp::to_dxf(scene, 1.0, mode);
    const auto parsed = inspect(dxf);
    check(parsed.layers.count(escaped), "Chinese layer name not preserved");
    check(parsed.layers.count("Caf\\U+00E9"), "accented layer name not preserved");
    check(parsed.layers.count("\\U+67DC\\U+D83D\\U+DE00"), "supplementary Unicode character lost");
    check(parsed.meshLayers.size() == scene.glb_primitives.size(), "missing geometry");
    check(parsed.meshLayers.front() == parsed.meshLayers.back(),
          "same source name got different layers");
    auto unique = parsed.meshLayers;
    unique.pop_back();
    for (auto& name : unique)
      for (char& c : name)
        if (c >= 'A' && c <= 'Z') c += 'a' - 'A';
    check(std::set<std::string>(unique.begin(), unique.end()).size() == unique.size(),
          "distinct names collided after sanitization/truncation");
    const auto classic = openskp::to_dxf(scene, 1234.5, mode);
    {
      ScopedLocale locale;
      check(openskp::to_dxf(scene, 1234.5, mode) == classic,
            "DXF output depends on the process locale");
    }
  }
  check(inspect(openskp::to_dxf({})).layers.size() == 2, "empty scene duplicates built-in layers");
  auto unused = triangle("Unused");
  unused.indices.clear();
  openskp::Scene empty;
  empty.glb_primitives.push_back(unused);
  check(inspect(openskp::to_dxf(empty)).layers.size() == 2,
        "empty primitive creates an unused layer");
  // Polyface counts and face indices are signed 16-bit fields.
  auto large = triangle("Large mesh");
  large.positions.resize(32768 * 3, 0);
  large.indices = {0, 1, 32767};
  scene.glb_primitives = {large};
  auto dxf = openskp::to_dxf(scene);
  check(inspect(dxf).meshLayers.size() == 1 && dxf.find("\r\n3DFACE\r\n") != std::string::npos,
        "large vertex count must fall back to 3DFACE");
  large = triangle("Many faces");
  for (int i = 1; i < 32768; ++i) large.indices.insert(large.indices.end(), {0, 1, 2});
  scene.glb_primitives = {large};
  check(inspect(openskp::to_dxf(scene)).meshLayers.size() == 32768,
        "large face count lost geometry");
  for (int failure = 0; failure < 5; ++failure) {
    auto bad = triangle("Invalid mesh");
    if (failure == 0) bad.positions.pop_back();
    if (failure == 1) bad.indices.pop_back();
    if (failure == 2) bad.indices[0] = 3;
    if (failure == 3) bad.positions[0] = std::numeric_limits<float>::quiet_NaN();
    scene.glb_primitives = {bad};
    for (const auto* mode : {"polyface", "3dface"}) {
      bool rejected = false;
      try {
        openskp::to_dxf(scene, failure == 4 ? std::numeric_limits<double>::infinity() : 1, mode);
      } catch (const std::invalid_argument&) {
        rejected = true;
      }
      check(rejected, "malformed geometry accepted");
    }
  }
  for (const auto& bad : {std::string("\xC0\xAF"), std::string("\xE4\xB8"),
                          std::string("\xED\xA0\x80"), std::string("\xF4\x90\x80\x80")}) {
    scene.glb_primitives = {triangle(bad)};
    bool rejected = false;
    try {
      openskp::to_dxf(scene);
    } catch (const std::invalid_argument&) {
      rejected = true;
    }
    check(rejected, "invalid UTF-8 accepted");
  }
}

}  // namespace

TEST(DxfExport, UnicodeNamesLocaleAndMalformedMeshes) { EXPECT_NO_THROW(verify_dxf_contract()); }
