#include <algorithm>
#include <filesystem>
#include <gtest/gtest.h>
#include <map>
#include <string>
#include <vector>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

namespace openskp {
namespace {

std::filesystem::path python_fixture(const char* name) {
  return std::filesystem::path(OPENSKP_FIXTURES) / "../../../python/tests/fixtures" / name;
}

ParsedAttrDict expected_typed_attrs() {
  return {
      {"v_padded", ParsedAttribute::from_string("  leading and trailing  ")},
      {"v_tab_nl",
       ParsedAttribute::from_string("\t<render>\n  <exposure value=\"1.5\"/>\n</render>\n")},
      {"v_crlf", ParsedAttribute::from_string("line1\r\nline2\r\n")},
      {"v_empty", ParsedAttribute::from_string("")},
      {"v_spaces", ParsedAttribute::from_string("   ")},
      {"  padded_key  ", ParsedAttribute::from_string("val_padded_key")},
      {"\ttab_nl_key\n", ParsedAttribute::from_string("val_tab_nl_key")},
      {"crlf_key\r\n", ParsedAttribute::from_string("val_crlf_key")},
  };
}

std::map<std::string, std::string> expected_string_attrs() {
  return {
      {"v_padded", "  leading and trailing  "},
      {"v_tab_nl", "\t<render>\n  <exposure value=\"1.5\"/>\n</render>\n"},
      {"v_crlf", "line1\r\nline2\r\n"},
      {"v_empty", ""},
      {"v_spaces", "   "},
      {"  padded_key  ", "val_padded_key"},
      {"\ttab_nl_key\n", "val_tab_nl_key"},
      {"crlf_key\r\n", "val_crlf_key"},
  };
}

TEST(AttributeStringWhitespace, ValuesKeysAndDictionaryNamesMatchSketchUp) {
  const auto expected_typed = expected_typed_attrs();
  const auto expected_str = expected_string_attrs();
  const ParsedAttrDict probe_typed{{"probe", ParsedAttribute::from_string("ok")}};
  const std::map<std::string, std::string> probe_str{{"probe", "ok"}};
  const std::vector<std::string> special_names{"  padded_dict  ", "\ttab_dict\n", "   ", ""};

  for (const char* fname :
       {"attribute_string_whitespace_2021.skp", "attribute_string_whitespace_2017.skp"}) {
    SCOPED_TRACE(fname);
    const auto path = python_fixture(fname);
    const auto model = SkpFile::open(path).parse();

    for (const auto* owner_dicts :
         {&model.attribute_dictionaries, &model.root().parsed_dictionaries}) {
      EXPECT_EQ(owner_dicts->at("whitespace_attrs"), expected_typed);
      EXPECT_EQ(owner_dicts->at("dynamic_attributes"), expected_typed);
      for (const auto& dname : special_names) {
        ASSERT_GT(owner_dicts->count(dname), 0u);
        EXPECT_EQ(owner_dicts->at(dname), probe_typed);
      }
    }

    const Definition* carrier_def = nullptr;
    for (const auto& [id, def] : model.definitions) {
      (void)id;
      if (def.name == "AttrCarrierDef") carrier_def = &def;
    }
    ASSERT_NE(carrier_def, nullptr);
    EXPECT_EQ(carrier_def->parsed_dictionaries.at("whitespace_attrs"), expected_typed);
    EXPECT_EQ(carrier_def->parsed_dictionaries.at("dynamic_attributes"), expected_typed);
    for (const auto& dname : special_names) {
      ASSERT_GT(carrier_def->parsed_dictionaries.count(dname), 0u);
      EXPECT_EQ(carrier_def->parsed_dictionaries.at(dname), probe_typed);
    }

    const auto scene = SkpFile::open(path).build_scene();
    const InstanceNode* carrier_node = nullptr;
    for (const auto& child : scene.scene_hierarchy.children) {
      if (child.definition_name == "AttrCarrierDef") carrier_node = &child;
    }
    ASSERT_NE(carrier_node, nullptr);
    EXPECT_EQ(carrier_node->properties, expected_str);
    EXPECT_EQ(carrier_node->attribute_dictionaries.at("whitespace_attrs"), expected_str);
    for (const auto& dname : special_names) {
      ASSERT_GT(carrier_node->attribute_dictionaries.count(dname), 0u);
      EXPECT_EQ(carrier_node->attribute_dictionaries.at(dname), probe_str);
    }
    EXPECT_EQ(carrier_node->definition_attribute_dictionaries.at("whitespace_attrs"), expected_str);
    EXPECT_EQ(carrier_node->definition_attribute_dictionaries.at("dynamic_attributes"),
              expected_str);
  }
}

TEST(AttributeStringWhitespace, EntityNamesWithWhitespaceMatchSketchUp) {
  for (const std::string version : {"2021", "2017"}) {
    SCOPED_TRACE(version);
    const std::string fname = "attribute_string_whitespace_" + version + ".skp";
    const auto model = SkpFile::open(python_fixture(fname.c_str())).parse();

    std::vector<std::string> expected_mats{
        "  Padded Mat  ",
        "\tTabbed\nMat\n",
        version == "2021" ? "CR\nLF Mat\n" : "CR\r\nLF Mat\r\n",
        "   ",
    };
    std::sort(expected_mats.begin(), expected_mats.end());
    std::vector<std::string> actual_mats;
    for (const auto& m : model.materials) actual_mats.push_back(m.name);
    std::sort(actual_mats.begin(), actual_mats.end());
    EXPECT_EQ(actual_mats, expected_mats);

    std::vector<std::string> expected_layers{
        "Layer0", "  Padded Tag  ", "\tTabbed\nTag\n", "CR\r\nLF Tag\r\n", "   ",
    };
    std::sort(expected_layers.begin(), expected_layers.end());
    std::vector<std::string> actual_layers;
    for (const auto& l : model.layers) actual_layers.push_back(l.name);
    std::sort(actual_layers.begin(), actual_layers.end());
    EXPECT_EQ(actual_layers, expected_layers);

    std::vector<std::string> expected_defs{
        "AttrCarrierDef", "  Padded Def  ", "\tTabbed\nDef\n", "CR\r\nLF Def\r\n", "   ",
    };
    std::sort(expected_defs.begin(), expected_defs.end());
    std::vector<std::string> actual_defs;
    for (const auto& [id, d] : model.definitions) {
      (void)id;
      actual_defs.push_back(d.name);
    }
    std::sort(actual_defs.begin(), actual_defs.end());
    EXPECT_EQ(actual_defs, expected_defs);

    std::vector<std::string> expected_insts{
        "AttrCarrierInst", "Padded Inst", "Tabbed\nInst", "CR\r\nLF Inst", "",
    };
    std::sort(expected_insts.begin(), expected_insts.end());
    std::vector<std::string> actual_insts;
    for (const auto& i : model.root().instances) actual_insts.push_back(i.name);
    std::sort(actual_insts.begin(), actual_insts.end());
    EXPECT_EQ(actual_insts, expected_insts);
  }
}

TEST(AttributeStringWhitespace, RawVffEmptyKeysUntrimmedInstanceNameAndEmptyMaterialName) {
  const auto path = python_fixture("attribute_string_whitespace_raw_vff.skp");
  const auto model = SkpFile::open(path).parse();

  const ParsedAttrDict expected_raw_keys{
      {"", ParsedAttribute::from_string("empty_key_val")},
      {"   ", ParsedAttribute::from_string("spaces_key_val")},
      {"  k  ", ParsedAttribute::from_string("  v\n")},
  };
  EXPECT_EQ(model.attribute_dictionaries.at("raw_keys"), expected_raw_keys);
  ASSERT_EQ(model.materials.size(), 1u);
  EXPECT_EQ(model.materials[0].name, "");
  ASSERT_EQ(model.root().instances.size(), 1u);
  EXPECT_EQ(model.root().instances[0].name, "  Raw Padded Inst\n");

  const auto scene = SkpFile::open(path).build_scene();
  ASSERT_EQ(scene.scene_hierarchy.children.size(), 1u);
  const auto& node = scene.scene_hierarchy.children[0];
  EXPECT_EQ(node.name, "  Raw Padded Inst\n");
  const std::map<std::string, std::string> expected_inst_keys{
      {"", "inst_empty_k"},
      {"   ", "inst_spaces_k"},
  };
  EXPECT_EQ(node.attribute_dictionaries.at("raw_keys"), expected_inst_keys);
}

}  // namespace
}  // namespace openskp
