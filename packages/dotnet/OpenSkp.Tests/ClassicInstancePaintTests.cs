using System;
using System.Collections.Generic;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>A component instance's own paint and tag in classic (pre-2021)
    /// files (#596).</summary>
    public class ClassicInstancePaintTests
    {
        private static readonly (double, double, double)[] Square =
        {
            (0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0),
        };

        private static byte[] PaintedModelBytes()
        {
            var builder = SkpCreate.NewFile();
            int red = builder.AddMaterial("Red", (200, 10, 10, 255));
            int blue = builder.AddMaterial("Blue", (10, 10, 200, 128));
            ComponentDefinitionBuilder leaf;
            using (leaf = builder.AddComponentDefinition("Leaf"))
            {
                leaf.AddFace(Square);
            }
            ComponentDefinitionBuilder outer;
            using (outer = builder.AddComponentDefinition("Outer"))
            {
                outer.AddInstance(leaf, name: "InheritsPaint");
                outer.AddInstance(leaf, name: "OwnPaint", material: blue, translation: (20, 0, 0));
            }
            builder.AddInstance(outer, name: "Painted", material: red);
            return builder.ToBytes();
        }

        [Fact]
        public void PaintIsAppliedAndInheritedByNestedComponents()
        {
            var bytes = PaintedModelBytes();
            var baked = SkpFile.BuildScene(bytes);
            var instanced = SkpFile.BuildInstancedScene(bytes);

            var bakedMatIndices = baked.GlbPrimitives.Select(p => p.MaterialIndex).ToList();
            var instancedMatIndices = instanced.MeshResources.SelectMany(r => r.Primitives).Select(p => p.MaterialIndex).ToList();

            foreach (var (gltfMats, indices) in new[]
            {
                (baked.GltfMaterials, bakedMatIndices),
                (instanced.GltfMaterials, instancedMatIndices),
            })
            {
                var used = indices.Select(i => (Dictionary<string, object>)gltfMats[i]).ToList();
                var summaries = used
                    .Select(m =>
                    {
                        var pbr = (Dictionary<string, object>)m["pbrMetallicRoughness"];
                        var bcf = (double[])pbr["baseColorFactor"];
                        bool doubleSided = m.TryGetValue("doubleSided", out var ds) && ds is bool b && b;
                        string alphaMode = m.TryGetValue("alphaMode", out var am) && am is string s ? s : "OPAQUE";
                        return (
                            Math.Round(bcf[0], 6),
                            Math.Round(bcf[1], 6),
                            Math.Round(bcf[2], 6),
                            Math.Round(bcf[3], 6),
                            doubleSided,
                            alphaMode
                        );
                    })
                    .ToHashSet();

                var expected = new HashSet<(double, double, double, double, bool, string)>
                {
                    (Math.Round(200.0 / 255.0, 6), Math.Round(10.0 / 255.0, 6), Math.Round(10.0 / 255.0, 6), 1.0, true, "OPAQUE"),
                    (Math.Round(10.0 / 255.0, 6), Math.Round(10.0 / 255.0, 6), Math.Round(200.0 / 255.0, 6), Math.Round(128.0 / 255.0, 6), true, "BLEND"),
                };
                Assert.Equal(expected, summaries);
            }
        }

        [Fact]
        public void EachPaintIsItsOwnInstancedMesh()
        {
            var scene = SkpFile.BuildInstancedScene(PaintedModelBytes());
            var nodes = scene.SceneHierarchy.Children[0].Children.ToDictionary(n => n.Name);
            Assert.NotNull(nodes["InheritsPaint"].MeshResourceId);
            Assert.NotNull(nodes["OwnPaint"].MeshResourceId);
            Assert.NotEqual(nodes["InheritsPaint"].MeshResourceId, nodes["OwnPaint"].MeshResourceId);
        }

        [Fact]
        public void TagIsTheInstancesOwn()
        {
            var builder = SkpCreate.NewFile();
            int walls = builder.AddLayer("Walls");
            ComponentDefinitionBuilder part;
            using (part = builder.AddComponentDefinition("Part"))
            {
                part.AddFace(Square);
            }
            builder.AddInstance(part, name: "Tagged", layer: walls);
            builder.AddInstance(part, name: "Untagged", translation: (20, 0, 0));

            var bytes = builder.ToBytes();
            var baked = SkpFile.BuildScene(bytes);
            var instanced = SkpFile.BuildInstancedScene(bytes);

            var expected = new Dictionary<string, string>
            {
                ["Tagged"] = "Walls",
                ["Untagged"] = "Layer0",
            };
            Assert.Equal(expected, baked.SceneHierarchy.Children.ToDictionary(n => n.Name, n => n.Layer));
            Assert.Equal(expected, instanced.SceneHierarchy.Children.ToDictionary(n => n.Name, n => n.Layer));
        }
    }
}
