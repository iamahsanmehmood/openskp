using System.Collections.Generic;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    public class ClassicInstanceAttrsTests
    {
        private static readonly (double X, double Y, double Z)[] WallRect =
        {
            (0.0, 0.0, 0.0),
            (20.0, 0.0, 0.0),
            (20.0, 10.0, 0.0),
            (0.0, 10.0, 0.0),
        };

        private static byte[] BuildFixtureBytes()
        {
            var b = SkpCreate.NewFile();
            ComponentDefinitionBuilder wall;
            using (wall = b.AddComponentDefinition("Wall"))
            {
                wall.AddFace(WallRect);
            }
            b.AddInstance(
                wall,
                name: "W1",
                attributes: new Dictionary<string, object>
                {
                    ["name"] = "CustomOverride",
                    ["sku"] = "B42",
                });
            b.AddInstance(
                wall,
                name: "W2",
                translation: (30.0, 0.0, 0.0),
                attributes: new Dictionary<string, object>
                {
                    ["label"] = "TS-9",
                    ["count"] = 3,
                },
                attributeDictName: "TechSteel");
            b.AddInstance(
                wall,
                name: "W3",
                translation: (60.0, 0.0, 0.0),
                attributes: new Dictionary<string, object>
                {
                    ["name"] = "IgnoredDynamic",
                    ["lenx"] = 20,
                },
                attributeDictName: "dynamic_attributes");
            return b.ToBytes();
        }

        [Fact]
        public void ClassicInstanceAttributesAndNamesMatchAcrossSceneTypes()
        {
            var data = BuildFixtureBytes();
            var baked = SkpFile.BuildScene(data);
            var instanced = SkpFile.BuildInstancedScene(data);

            var bakedNodes = baked.SceneHierarchy.Children;
            Assert.Equal(new[] { "CustomOverride", "TS-9", "W3" }, bakedNodes.Select(n => n.Name));
            Assert.Equal(new[] { false, false, false }, bakedNodes.Select(n => n.NameIsGenerated));
            Assert.Equal("CustomOverride", bakedNodes[0].AttributeDictionaries["attributes"]["name"]);
            Assert.Equal("B42", bakedNodes[0].AttributeDictionaries["attributes"]["sku"]);
            Assert.Empty(bakedNodes[0].Properties);
            Assert.Equal("TS-9", bakedNodes[1].AttributeDictionaries["TechSteel"]["label"]);
            Assert.Equal("3", bakedNodes[1].AttributeDictionaries["TechSteel"]["count"]);
            Assert.Empty(bakedNodes[1].Properties);
            Assert.Empty(bakedNodes[2].AttributeDictionaries);
            Assert.Equal("IgnoredDynamic", bakedNodes[2].Properties["name"]);
            Assert.Equal("20", bakedNodes[2].Properties["lenx"]);

            var instNodes = instanced.SceneHierarchy.Children;
            Assert.Equal(new[] { "CustomOverride", "TS-9", "W3" }, instNodes.Select(n => n.Name));
            Assert.Equal(new[] { false, false, false }, instNodes.Select(n => n.NameIsGenerated));
            Assert.Equal("CustomOverride", instNodes[0].AttributeDictionaries["attributes"]["name"]);
            Assert.Equal("B42", instNodes[0].AttributeDictionaries["attributes"]["sku"]);
            Assert.Empty(instNodes[0].Properties);
            Assert.Equal("TS-9", instNodes[1].AttributeDictionaries["TechSteel"]["label"]);
            Assert.Equal("3", instNodes[1].AttributeDictionaries["TechSteel"]["count"]);
            Assert.Empty(instNodes[1].Properties);
            Assert.Empty(instNodes[2].AttributeDictionaries);
            Assert.Equal("IgnoredDynamic", instNodes[2].Properties["name"]);
            Assert.Equal("20", instNodes[2].Properties["lenx"]);

            var byName = baked.SurfacePrimitives.ToDictionary(
                p => baked.MeshIndex[p.GeomName].Name,
                p => baked.MeshIndex[p.GeomName]);
            Assert.Equal(new HashSet<string> { "CustomOverride", "TS-9", "W3" }, byName.Keys.ToHashSet());
            Assert.Equal("B42", byName["CustomOverride"].AttributeDictionaries["attributes"]["sku"]);
            Assert.Equal("3", byName["TS-9"].AttributeDictionaries["TechSteel"]["count"]);
            Assert.Empty(byName["W3"].AttributeDictionaries);
            Assert.Equal("20", byName["W3"].Properties["lenx"]);
        }
    }
}
