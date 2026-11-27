#include <filesystem>
#include <gtest/gtest.h>
#include <map>
#include <string>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

namespace openskp {
namespace {

std::filesystem::path python_fixture(const char* name) {
  return std::filesystem::path(OPENSKP_FIXTURES) / "../../../python/tests/fixtures" / name;
}

const Definition* find_definition(const SkpModel& model, const std::string& name) {
  const Definition* match = nullptr;
  for (const auto& [id, def] : model.definitions) {
    (void)id;
    if (def.name == name) {
      EXPECT_EQ(match, nullptr);
      match = &def;
    }
  }
  return match;
}

TEST(DefinitionAndModelAttributeDictionaries, VffDefinitionModelAndSceneNodes) {
  const auto path = python_fixture("definition_attribute_dictionaries_2026.skp");
  const auto model = SkpFile::open(path).parse();

  const Definition* box = find_definition(model, "dc_fixture_box");
  ASSERT_NE(box, nullptr);
  const auto& box_dynamic = box->parsed_dictionaries.at("dynamic_attributes");
  EXPECT_EQ(box_dynamic.size(), 8u);
  EXPECT_EQ(box_dynamic.at("_name"), ParsedAttribute::from_string("Box"));
  EXPECT_EQ(box_dynamic.at("_lenx_formula"), ParsedAttribute::from_string("Shelf!Width/2"));
  EXPECT_EQ(box_dynamic.at("_lenx_label"), ParsedAttribute::from_string("LenX"));
  EXPECT_EQ(box_dynamic.at("_lengthunits"), ParsedAttribute::from_string("CENTIMETERS"));
  EXPECT_EQ(box_dynamic.at("_copies_formula"), ParsedAttribute::from_string("2"));
  EXPECT_EQ(box_dynamic.at("_hasbehaviors"), ParsedAttribute::from_float(1.0));
  EXPECT_EQ(box_dynamic.at("copies"), ParsedAttribute::from_float(2.0));
  EXPECT_EQ(box_dynamic.at("lenx"), ParsedAttribute::from_float(10.0));
  EXPECT_EQ(box->parsed_dictionaries.at("fixture_custom").at("note"),
            ParsedAttribute::from_string("definition-level custom dictionary"));

  const auto& model_dynamic = model.attribute_dictionaries.at("dynamic_attributes");
  EXPECT_EQ(model_dynamic.at("_name"), ParsedAttribute::from_string("Shelf"));
  EXPECT_EQ(model_dynamic.at("width"), ParsedAttribute::from_float(25.0));
  EXPECT_EQ(model_dynamic.at("_width_access"), ParsedAttribute::from_string("LIST"));
  EXPECT_EQ(model_dynamic.at("_width_options"),
            ParsedAttribute::from_string("&Narrow=20&Wide=25&"));
  EXPECT_EQ(model_dynamic.at("_width_formlabel"), ParsedAttribute::from_string("Shelf width"));
  EXPECT_EQ(model.root().parsed_dictionaries, model.attribute_dictionaries);

  const auto scene = SkpFile::open(path).build_scene();
  EXPECT_TRUE(scene.scene_hierarchy.definition_attribute_dictionaries.empty());
  ASSERT_EQ(scene.scene_hierarchy.children.size(), 1u);
  const auto& node = scene.scene_hierarchy.children[0];
  EXPECT_EQ(node.definition_name, "dc_fixture_box");
  const std::map<std::string, std::string> expected_props{
      {"_hasbehaviors", "1"},
      {"_lenx_formula", "Shelf!Width/2"},
      {"lenx", "12.5"},
  };
  EXPECT_EQ(node.properties, expected_props);

  const std::map<std::string, std::string> expected_def_dynamic{
      {"_copies_formula", "2"},
      {"_hasbehaviors", "1"},
      {"_lengthunits", "CENTIMETERS"},
      {"_lenx_formula", "Shelf!Width/2"},
      {"_lenx_label", "LenX"},
      {"_name", "Box"},
      {"copies", "2"},
      {"lenx", "10"},
  };
  const std::map<std::string, std::string> expected_def_custom{
      {"note", "definition-level custom dictionary"},
  };
  EXPECT_EQ(node.definition_attribute_dictionaries.at("dynamic_attributes"), expected_def_dynamic);
  EXPECT_EQ(node.definition_attribute_dictionaries.at("fixture_custom"), expected_def_custom);
}

TEST(DefinitionAndModelAttributeDictionaries, Classic2017And2018DefinitionModelAndSceneNodes) {
  for (const char* fname :
       {"legacy_attribute_dictionaries_2017.skp", "legacy_attribute_dictionaries_2018.skp"}) {
    SCOPED_TRACE(fname);
    const auto path = python_fixture(fname);
    const auto model = SkpFile::open(path).parse();

    const Definition* box = find_definition(model, "dc_fixture_box");
    ASSERT_NE(box, nullptr);
    const ParsedAttrDict expected_box_dynamic{
        {"_copies_formula", ParsedAttribute::from_string("2")},
        {"_lengthunits", ParsedAttribute::from_string("CENTIMETERS")},
        {"_lenx_formula", ParsedAttribute::from_string("Shelf!Width/2")},
        {"_name", ParsedAttribute::from_string("Box")},
        {"lenx", ParsedAttribute::from_float(10.0)},
    };
    const ParsedAttrDict expected_box_custom{
        {"note", ParsedAttribute::from_string("definition-level custom dictionary")},
    };
    EXPECT_EQ(box->parsed_dictionaries.at("dynamic_attributes"), expected_box_dynamic);
    EXPECT_EQ(box->parsed_dictionaries.at("fixture_custom"), expected_box_custom);

    EXPECT_EQ(model.attribute_dictionaries.count("ModelProperties"), 0u);
    const ParsedAttrDict expected_model_dynamic{
        {"_name", ParsedAttribute::from_string("Shelf")},
        {"_width_access", ParsedAttribute::from_string("LIST")},
        {"_width_formlabel", ParsedAttribute::from_string("Shelf width")},
        {"_width_options", ParsedAttribute::from_string("&Narrow=20&Wide=25&")},
        {"width", ParsedAttribute::from_float(25.0)},
    };
    EXPECT_EQ(model.attribute_dictionaries.at("dynamic_attributes"), expected_model_dynamic);
    EXPECT_EQ(model.root().parsed_dictionaries, model.attribute_dictionaries);

    const auto scene = SkpFile::open(path).build_scene();
    EXPECT_TRUE(scene.scene_hierarchy.definition_attribute_dictionaries.empty());
    ASSERT_EQ(scene.scene_hierarchy.children.size(), 1u);
    const auto& node = scene.scene_hierarchy.children[0];
    EXPECT_EQ(node.definition_name, "dc_fixture_box");
    const std::map<std::string, std::string> expected_props{{"lenx", "12.5"}};
    EXPECT_EQ(node.properties, expected_props);
    const std::map<std::string, std::string> expected_def_dynamic{
        {"_copies_formula", "2"},
        {"_lengthunits", "CENTIMETERS"},
        {"_lenx_formula", "Shelf!Width/2"},
        {"_name", "Box"},
        {"lenx", "10"},
    };
    const std::map<std::string, std::string> expected_def_custom{
        {"note", "definition-level custom dictionary"},
    };
    EXPECT_EQ(node.definition_attribute_dictionaries.at("dynamic_attributes"),
              expected_def_dynamic);
    EXPECT_EQ(node.definition_attribute_dictionaries.at("fixture_custom"), expected_def_custom);
  }
}

TEST(DefinitionAndModelAttributeDictionaries, FilesWithoutDictionariesStayClean) {
  const auto vff_model = SkpFile::open(test::fixture("nesting_2021.skp")).parse();
  for (const auto& [id, def] : vff_model.definitions) {
    (void)id;
    EXPECT_EQ(def.parsed_dictionaries.count("dynamic_attributes"), 0u);
  }

  const auto classic_model = SkpFile::open(test::fixture("legacy_v2019_synthetic.skp")).parse();
  EXPECT_EQ(classic_model.attribute_dictionaries.count("ModelProperties"), 0u);
  EXPECT_EQ(classic_model.attribute_dictionaries.count("dynamic_attributes"), 0u);
  for (const auto& [id, def] : classic_model.definitions) {
    (void)id;
    EXPECT_EQ(def.parsed_dictionaries.count("dynamic_attributes"), 0u);
  }
}

}  // namespace
}  // namespace openskp
