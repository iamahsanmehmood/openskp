using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    public class DefinitionAndModelAttributeDictionariesTests
    {
        private static string LocalFixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        private static string PythonFixturePath(string name) =>
            Path.GetFullPath(Path.Combine(
                AppContext.BaseDirectory, "..", "..", "..", "..", "..",
                "python", "tests", "fixtures", name));

        [Fact]
        public void VffDefinitionAndModelKeepTheirOwnDictionariesAndSceneStringifiesThem()
        {
            var path = PythonFixturePath("definition_attribute_dictionaries_2026.skp");
            var model = SkpFile.Open(path);

            var box = model.Definitions.Values.Single(d => d.Name == "dc_fixture_box");
            var boxDynamic = box.AttributeDictionaries["dynamic_attributes"];
            Assert.Equal("Box", boxDynamic["_name"]);
            Assert.Equal("Shelf!Width/2", boxDynamic["_lenx_formula"]);
            Assert.Equal("LenX", boxDynamic["_lenx_label"]);
            Assert.Equal("CENTIMETERS", boxDynamic["_lengthunits"]);
            Assert.Equal("2", boxDynamic["_copies_formula"]);
            Assert.Equal(1.0, boxDynamic["_hasbehaviors"]);
            Assert.Equal(2.0, boxDynamic["copies"]);
            Assert.Equal(10.0, boxDynamic["lenx"]);
            Assert.Equal(8, boxDynamic.Count);
            Assert.Equal(
                "definition-level custom dictionary",
                box.AttributeDictionaries["fixture_custom"]["note"]);

            var modelDynamic = model.AttributeDictionaries["dynamic_attributes"];
            Assert.Equal("Shelf", modelDynamic["_name"]);
            Assert.Equal(25.0, modelDynamic["width"]);
            Assert.Equal("LIST", modelDynamic["_width_access"]);
            Assert.Equal("&Narrow=20&Wide=25&", modelDynamic["_width_options"]);
            Assert.Equal("Shelf width", modelDynamic["_width_formlabel"]);
            Assert.Equal(model.AttributeDictionaries.Keys, model.Root.AttributeDictionaries.Keys);
            Assert.Equal(
                model.AttributeDictionaries["dynamic_attributes"],
                model.Root.AttributeDictionaries["dynamic_attributes"]);

            var scene = SkpFile.BuildScene(path);
            Assert.Empty(scene.SceneHierarchy.DefinitionAttributeDictionaries);
            var node = Assert.Single(scene.SceneHierarchy.Children);
            Assert.Equal("dc_fixture_box", node.DefinitionName);
            Assert.Equal(
                new Dictionary<string, string>
                {
                    ["_hasbehaviors"] = "1",
                    ["_lenx_formula"] = "Shelf!Width/2",
                    ["lenx"] = "12.5",
                },
                node.Properties);
            Assert.Equal(
                new Dictionary<string, string>
                {
                    ["_name"] = "Box",
                    ["_lenx_formula"] = "Shelf!Width/2",
                    ["_lenx_label"] = "LenX",
                    ["_lengthunits"] = "CENTIMETERS",
                    ["_copies_formula"] = "2",
                    ["_hasbehaviors"] = "1",
                    ["copies"] = "2",
                    ["lenx"] = "10",
                },
                node.DefinitionAttributeDictionaries["dynamic_attributes"]);
            Assert.Equal(
                new Dictionary<string, string>
                {
                    ["note"] = "definition-level custom dictionary",
                },
                node.DefinitionAttributeDictionaries["fixture_custom"]);
        }

        [Theory]
        [InlineData("2017")]
        [InlineData("2018")]
        public void ClassicDefinitionAndModelKeepTheirOwnDictionariesAndExcludeModelProperties(string version)
        {
            var path = PythonFixturePath($"legacy_attribute_dictionaries_{version}.skp");
            var model = SkpFile.Open(path);

            var box = model.Definitions.Values.Single(d => d.Name == "dc_fixture_box");
            Assert.Equal(
                new Dictionary<string, object?>
                {
                    ["_name"] = "Box",
                    ["_lenx_formula"] = "Shelf!Width/2",
                    ["_lengthunits"] = "CENTIMETERS",
                    ["_copies_formula"] = "2",
                    ["lenx"] = 10.0,
                },
                box.AttributeDictionaries["dynamic_attributes"]);
            Assert.Equal(
                new Dictionary<string, object?>
                {
                    ["note"] = "definition-level custom dictionary",
                },
                box.AttributeDictionaries["fixture_custom"]);

            Assert.False(model.AttributeDictionaries.ContainsKey("ModelProperties"));
            Assert.Equal(
                new Dictionary<string, object?>
                {
                    ["_name"] = "Shelf",
                    ["width"] = 25.0,
                    ["_width_access"] = "LIST",
                    ["_width_options"] = "&Narrow=20&Wide=25&",
                    ["_width_formlabel"] = "Shelf width",
                },
                model.AttributeDictionaries["dynamic_attributes"]);
            Assert.Equal(model.AttributeDictionaries.Keys, model.Root.AttributeDictionaries.Keys);
            Assert.Equal(
                model.AttributeDictionaries["dynamic_attributes"],
                model.Root.AttributeDictionaries["dynamic_attributes"]);

            var scene = SkpFile.BuildScene(path);
            Assert.Empty(scene.SceneHierarchy.DefinitionAttributeDictionaries);
            var node = Assert.Single(scene.SceneHierarchy.Children);
            Assert.Equal("dc_fixture_box", node.DefinitionName);
            Assert.Equal(new Dictionary<string, string> { ["lenx"] = "12.5" }, node.Properties);
            Assert.Equal(
                new Dictionary<string, string>
                {
                    ["_name"] = "Box",
                    ["_lenx_formula"] = "Shelf!Width/2",
                    ["_lengthunits"] = "CENTIMETERS",
                    ["_copies_formula"] = "2",
                    ["lenx"] = "10",
                },
                node.DefinitionAttributeDictionaries["dynamic_attributes"]);
            Assert.Equal(
                new Dictionary<string, string>
                {
                    ["note"] = "definition-level custom dictionary",
                },
                node.DefinitionAttributeDictionaries["fixture_custom"]);
        }

        [Fact]
        public void FilesWithoutDictionariesStayClean()
        {
            var vffModel = SkpFile.Open(LocalFixturePath("nesting_2021.skp"));
            foreach (var def in vffModel.Definitions.Values)
            {
                Assert.False(def.AttributeDictionaries.ContainsKey("dynamic_attributes"));
            }

            var classicModel = SkpFile.Open(LocalFixturePath("legacy_v2019_synthetic.skp"));
            Assert.False(classicModel.AttributeDictionaries.ContainsKey("ModelProperties"));
            Assert.False(classicModel.AttributeDictionaries.ContainsKey("dynamic_attributes"));
            foreach (var def in classicModel.Definitions.Values)
            {
                Assert.False(def.AttributeDictionaries.ContainsKey("dynamic_attributes"));
            }
        }
    }
}
