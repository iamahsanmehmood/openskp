using System;
using System.IO;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>
    /// Real-file regression test for JsonExport.ToDict() - openskp's
    /// canonical JSON export schema, shared with the Python (to_dict) and
    /// TypeScript (toJSON) ports. Cross-checked directly against both on
    /// this exact fixture.
    /// </summary>
    public class JsonExportTests
    {
        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        [Fact]
        public void MatchesPythonAndTypeScriptGroundTruth()
        {
            var model = SkpFile.Open(FixturePath("capilla_quiroz_v17.skp"));
            var d = JsonExport.ToDict(model);

            Assert.Equal("1.0", d["format_version"]);
            Assert.Equal("{17.0.18899}", d["sketchup_version"]);
            Assert.Equal(2, d["total_definitions"]);
            Assert.Equal(1, d["total_layers"]);
            Assert.Equal(0, d["total_meshes"]);

            var root = (System.Collections.Generic.Dictionary<string, object?>)d["root"]!;
            Assert.Equal(251, root["vertex_count"]);
            Assert.Equal(390, root["edge_count"]);
            Assert.Equal(146, root["face_count"]);
            var rootInstances = (System.Collections.Generic.List<object>)root["instances"]!;
            Assert.Equal(3, rootInstances.Count);
            var firstInst = (System.Collections.Generic.Dictionary<string, object?>)rootInstances[0];
            Assert.Equal(new[] { "name", "ref_idx", "guid", "matrix" }.OrderBy(x => x), firstInst.Keys.OrderBy(x => x));

            var definitions = (System.Collections.Generic.Dictionary<string, object?>)d["definitions"]!;
            var puerta = definitions.Values
                .Cast<System.Collections.Generic.Dictionary<string, object?>>()
                .First(v => (string)v["name"]! == "puerta");
            Assert.Equal(40L, puerta["id"]);
            Assert.Equal(64, puerta["vertex_count"]);
            Assert.Equal(95, puerta["edge_count"]);
            Assert.Equal(24, puerta["face_count"]);
            Assert.Equal(95, ((System.Collections.Generic.List<object>)puerta["edges"]!).Count);
            Assert.Equal(24, ((System.Collections.Generic.List<object>)puerta["faces"]!).Count);

            var layers = (System.Collections.Generic.List<object>)d["layers"]!;
            var firstLayer = (System.Collections.Generic.Dictionary<string, object?>)layers[0];
            var layerColor = (System.Collections.Generic.Dictionary<string, object?>)firstLayer["color"]!;
            Assert.Equal(255, layerColor["r"]);
            Assert.Equal(84, layerColor["g"]);
            Assert.Equal(84, layerColor["b"]);

            Assert.Empty((System.Collections.Generic.Dictionary<string, object?>)d["mesh_index"]!);
            Assert.Null(d["scene_hierarchy"]);
        }

        [Fact]
        public void IncludesSceneHierarchyAndMeshIndexWhenSceneIsPassed()
        {
            var model = SkpFile.Open(FixturePath("capilla_quiroz_v17.skp"));
            var scene = SkpFile.BuildScene(FixturePath("capilla_quiroz_v17.skp"));
            var d = JsonExport.ToDict(model, scene);

            Assert.Equal(scene.MeshIndex.Count, d["total_meshes"]);
            var hierarchy = (System.Collections.Generic.Dictionary<string, object?>)d["scene_hierarchy"]!;
            Assert.Equal("ROOT", hierarchy["name"]);
            Assert.True(hierarchy.ContainsKey("definition_name"));
            Assert.True(hierarchy.ContainsKey("position_mm"));
            Assert.True(hierarchy.ContainsKey("attribute_dictionaries"));

            var meshIndex = (System.Collections.Generic.Dictionary<string, object?>)d["mesh_index"]!;
            var firstMesh = (System.Collections.Generic.Dictionary<string, object?>)meshIndex.Values.First()!;
            Assert.True(firstMesh.ContainsKey("definition_name"));
            Assert.True(firstMesh.ContainsKey("position_mm"));
            Assert.True(firstMesh.ContainsKey("attribute_dictionaries"));
        }

        private static System.Collections.Generic.Dictionary<string, object?>? FindNode(
            System.Collections.Generic.Dictionary<string, object?> node,
            string targetName)
        {
            if ((string?)node["name"] == targetName) return node;
            var children = (System.Collections.Generic.List<object>)node["children"]!;
            foreach (var childObj in children)
            {
                var found = FindNode((System.Collections.Generic.Dictionary<string, object?>)childObj, targetName);
                if (found != null) return found;
            }
            return null;
        }

        [Fact]
        public void IncludesAttributeDictionariesFromRealPluginData()
        {
            var path = FixturePath("Untitled.skp");
            var model = SkpFile.Open(path);
            var scene = SkpFile.BuildScene(path);
            var d = JsonExport.ToDict(model, scene);

            var hierarchy = (System.Collections.Generic.Dictionary<string, object?>)d["scene_hierarchy"]!;
            var w1 = FindNode(hierarchy, "W1");
            Assert.NotNull(w1);
            Assert.Equal(
                new[] { "name", "definition_name", "layer", "position_mm", "properties", "attribute_dictionaries", "children" },
                w1!.Keys.ToArray());
            Assert.Empty((System.Collections.Generic.IDictionary<string, object>)w1["properties"]!);

            var dicts = (System.Collections.Generic.Dictionary<string, object?>)w1["attribute_dictionaries"]!;
            var steelframer = (System.Collections.Generic.Dictionary<string, object?>)dicts["steelframer-dict"]!;
            Assert.Equal("SteelFramer::Engine::PanelGenerator", steelframer["generator"]);
            Assert.Equal("362S200-43", steelframer["profile"]);
            Assert.Equal("2700", steelframer["height"]);
            Assert.Equal("1", steelframer["auto_dimensions"]);

            var meshIndex = (System.Collections.Generic.Dictionary<string, object?>)d["mesh_index"]!;
            var meshWithSteel = meshIndex.Values
                .Cast<System.Collections.Generic.Dictionary<string, object?>>()
                .FirstOrDefault(m =>
                    ((System.Collections.Generic.Dictionary<string, object?>)m["attribute_dictionaries"]!)
                    .ContainsKey("steelframer-dict"));
            Assert.NotNull(meshWithSteel);
            Assert.Equal(
                new[] { "name", "definition_name", "layer", "position_mm", "properties", "attribute_dictionaries", "path" },
                meshWithSteel!.Keys.ToArray());
        }

        [Fact]
        public void SerializesAllAttributeValueTypesMatchingPythonShape()
        {
            var attrPath = Path.GetFullPath(Path.Combine(
                AppContext.BaseDirectory, "..", "..", "..", "..", "..",
                "python", "tests", "fixtures", "attribute_bool_time_2025.skp"));
            var model = SkpFile.Open(attrPath);
            var scene = SkpFile.BuildScene(attrPath);
            var d = JsonExport.ToDict(model, scene);
            var json = MiniJson.Serialize(d);

            using var doc = System.Text.Json.JsonDocument.Parse(json);
            var rootHierarchy = doc.RootElement.GetProperty("scene_hierarchy");
            var attrComp = rootHierarchy.GetProperty("children").EnumerateArray()
                .First(n => n.GetProperty("name").GetString() == "AttrComp");
            var dicts = attrComp.GetProperty("attribute_dictionaries");
            var stage3 = dicts.GetProperty("stage3");
            Assert.Equal("1,two,3.5,1", stage3.GetProperty("array").GetString());
            Assert.Equal("0", stage3.GetProperty("bool_false").GetString());
            Assert.Equal("1", stage3.GetProperty("bool_true").GetString());
            Assert.Equal("1.5", stage3.GetProperty("float").GetString());
            Assert.Equal("42", stage3.GetProperty("int").GetString());
            Assert.Equal("60", stage3.GetProperty("length").GetString());
            Assert.Equal("1,2,3,4", stage3.GetProperty("nested").GetString());
            Assert.Equal("", stage3.GetProperty("nothing").GetString());
            Assert.Equal("1,2,3", stage3.GetProperty("point").GetString());
            Assert.Equal("hello", stage3.GetProperty("string").GetString());
            Assert.Equal("1700000000", stage3.GetProperty("timestamp").GetString());
            Assert.Equal("4,5,6", stage3.GetProperty("vector").GetString());
            Assert.Equal("value", dicts.GetProperty("stage3_other").GetProperty("key").GetString());

            var attrMeshes = doc.RootElement.GetProperty("mesh_index").EnumerateObject()
                .Where(p => p.Value.GetProperty("name").GetString() == "AttrComp")
                .ToList();
            Assert.NotEmpty(attrMeshes);
            foreach (var m in attrMeshes)
            {
                Assert.Equal(
                    "1,2,3",
                    m.Value.GetProperty("attribute_dictionaries").GetProperty("stage3").GetProperty("point").GetString());
            }
        }

        [Fact]
        public void ProducesJsonSerializableOutput()
        {
            var model = SkpFile.Open(FixturePath("capilla_quiroz_v17.skp"));
            var scene = SkpFile.BuildScene(FixturePath("capilla_quiroz_v17.skp"));
            var d = JsonExport.ToDict(model, scene);
            var json = MiniJson.Serialize(d);
            Assert.False(string.IsNullOrEmpty(json));
        }
    }
}
