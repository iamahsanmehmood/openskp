using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    public class AttributeStringWhitespaceTests
    {
        private static string PythonFixturePath(string name) =>
            Path.GetFullPath(Path.Combine(
                AppContext.BaseDirectory, "..", "..", "..", "..", "..",
                "python", "tests", "fixtures", name));

        private static Dictionary<string, object?> ExpectedTypedAttrs() =>
            new Dictionary<string, object?>
            {
                ["v_padded"] = "  leading and trailing  ",
                ["v_tab_nl"] = "\t<render>\n  <exposure value=\"1.5\"/>\n</render>\n",
                ["v_crlf"] = "line1\r\nline2\r\n",
                ["v_empty"] = "",
                ["v_spaces"] = "   ",
                ["  padded_key  "] = "val_padded_key",
                ["\ttab_nl_key\n"] = "val_tab_nl_key",
                ["crlf_key\r\n"] = "val_crlf_key",
            };

        private static Dictionary<string, string> ExpectedStringAttrs() =>
            new Dictionary<string, string>
            {
                ["v_padded"] = "  leading and trailing  ",
                ["v_tab_nl"] = "\t<render>\n  <exposure value=\"1.5\"/>\n</render>\n",
                ["v_crlf"] = "line1\r\nline2\r\n",
                ["v_empty"] = "",
                ["v_spaces"] = "   ",
                ["  padded_key  "] = "val_padded_key",
                ["\ttab_nl_key\n"] = "val_tab_nl_key",
                ["crlf_key\r\n"] = "val_crlf_key",
            };

        private static readonly string[] SpecialDictNames =
        {
            "  padded_dict  ",
            "\ttab_dict\n",
            "   ",
            "",
        };

        [Theory]
        [InlineData("2021")]
        [InlineData("2017")]
        public void AttributeValuesKeysAndDictionaryNamesMatchSketchUp(string version)
        {
            var path = PythonFixturePath($"attribute_string_whitespace_{version}.skp");
            var model = SkpFile.Open(path);
            var expectedTyped = ExpectedTypedAttrs();
            var expectedStr = ExpectedStringAttrs();
            var expectedProbeTyped = new Dictionary<string, object?> { ["probe"] = "ok" };
            var expectedProbeStr = new Dictionary<string, string> { ["probe"] = "ok" };

            foreach (var ownerDicts in new[] { model.AttributeDictionaries, model.Root.AttributeDictionaries })
            {
                Assert.Equal(expectedTyped, ownerDicts["whitespace_attrs"]);
                Assert.Equal(expectedTyped, ownerDicts["dynamic_attributes"]);
                foreach (var dname in SpecialDictNames)
                {
                    Assert.Equal(expectedProbeTyped, ownerDicts[dname]);
                }
            }

            var carrierDef = model.Definitions.Values.Single(d => d.Name == "AttrCarrierDef");
            Assert.Equal(expectedTyped, carrierDef.AttributeDictionaries["whitespace_attrs"]);
            Assert.Equal(expectedTyped, carrierDef.AttributeDictionaries["dynamic_attributes"]);
            foreach (var dname in SpecialDictNames)
            {
                Assert.Equal(expectedProbeTyped, carrierDef.AttributeDictionaries[dname]);
            }

            var scene = SkpFile.BuildScene(path);
            var node = scene.SceneHierarchy.Children.Single(n => n.DefinitionName == "AttrCarrierDef");
            Assert.Equal(expectedStr, node.Properties);
            Assert.Equal(expectedStr, node.AttributeDictionaries["whitespace_attrs"]);
            foreach (var dname in SpecialDictNames)
            {
                Assert.Equal(expectedProbeStr, node.AttributeDictionaries[dname]);
            }
            Assert.Equal(expectedStr, node.DefinitionAttributeDictionaries["whitespace_attrs"]);
            Assert.Equal(expectedStr, node.DefinitionAttributeDictionaries["dynamic_attributes"]);
        }

        [Theory]
        [InlineData("2021")]
        [InlineData("2017")]
        public void EntityNamesWithWhitespaceMatchSketchUp(string version)
        {
            var path = PythonFixturePath($"attribute_string_whitespace_{version}.skp");
            var model = SkpFile.Open(path);

            var expectedMaterials = new[]
            {
                "  Padded Mat  ",
                "\tTabbed\nMat\n",
                version == "2021" ? "CR\nLF Mat\n" : "CR\r\nLF Mat\r\n",
                "   ",
            }.OrderBy(s => s, StringComparer.Ordinal).ToArray();
            Assert.Equal(expectedMaterials, model.Materials.Select(m => m.Name).OrderBy(s => s, StringComparer.Ordinal).ToArray());

            var expectedLayers = new[]
            {
                "Layer0",
                "  Padded Tag  ",
                "\tTabbed\nTag\n",
                "CR\r\nLF Tag\r\n",
                "   ",
            }.OrderBy(s => s, StringComparer.Ordinal).ToArray();
            Assert.Equal(expectedLayers, model.Layers.Select(l => l.Name).OrderBy(s => s, StringComparer.Ordinal).ToArray());

            var expectedDefs = new[]
            {
                "AttrCarrierDef",
                "  Padded Def  ",
                "\tTabbed\nDef\n",
                "CR\r\nLF Def\r\n",
                "   ",
            }.OrderBy(s => s, StringComparer.Ordinal).ToArray();
            Assert.Equal(expectedDefs, model.Definitions.Values.Select(d => d.Name).OrderBy(s => s, StringComparer.Ordinal).ToArray());

            var expectedInstNames = new[]
            {
                "AttrCarrierInst",
                "Padded Inst",
                "Tabbed\nInst",
                "CR\r\nLF Inst",
                "",
            }.OrderBy(s => s, StringComparer.Ordinal).ToArray();
            Assert.Equal(expectedInstNames, model.Root.Instances.Select(i => i.Name).OrderBy(s => s, StringComparer.Ordinal).ToArray());
        }

        [Fact]
        public void RawVffEmptyKeysUntrimmedInstanceNameAndEmptyMaterialNameMatchSketchUp()
        {
            var path = PythonFixturePath("attribute_string_whitespace_raw_vff.skp");
            var model = SkpFile.Open(path);

            Assert.Equal(
                new Dictionary<string, object?>
                {
                    [""] = "empty_key_val",
                    ["   "] = "spaces_key_val",
                    ["  k  "] = "  v\n",
                },
                model.AttributeDictionaries["raw_keys"]);
            Assert.Equal(new[] { "" }, model.Materials.Select(m => m.Name).ToArray());
            var inst = Assert.Single(model.Root.Instances);
            Assert.Equal("  Raw Padded Inst\n", inst.Name);

            var scene = SkpFile.BuildScene(path);
            var node = Assert.Single(scene.SceneHierarchy.Children);
            Assert.Equal("  Raw Padded Inst\n", node.Name);
            Assert.Equal(
                new Dictionary<string, string>
                {
                    [""] = "inst_empty_k",
                    ["   "] = "inst_spaces_k",
                },
                node.AttributeDictionaries["raw_keys"]);
        }
    }
}
