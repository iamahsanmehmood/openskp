using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text;
using Xunit;

namespace OpenSkp.Tests
{
    public class FaceEdgeLayersTests
    {
        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        private static string ChairAndTablePath() =>
            Path.GetFullPath(Path.Combine(
                AppContext.BaseDirectory, "..", "..", "..", "..", "..", "..",
                "examples", "web-viewer", "samples", "chair_and_table.skp"));

        private static byte[] TlvRec(string tag, params byte[][] parts)
        {
            int bodyLen = parts.Sum(p => p.Length);
            var buf = new byte[6 + bodyLen];
            buf[0] = Convert.ToByte(tag.Substring(0, 2), 16);
            buf[1] = Convert.ToByte(tag.Substring(2, 2), 16);
            BitConverter.GetBytes((uint)bodyLen).CopyTo(buf, 2);
            int off = 6;
            foreach (var p in parts)
            {
                Buffer.BlockCopy(p, 0, buf, off, p.Length);
                off += p.Length;
            }
            return buf;
        }

        private static byte[] BuildSyntheticVffSkp(byte[] modelDat)
        {
            byte[] refBytes = File.ReadAllBytes(FixturePath("arcs_2021.skp"));
            int pk = -1;
            for (int i = 0; i + 4 <= refBytes.Length; i++)
            {
                if (refBytes[i] == 0x50 && refBytes[i + 1] == 0x4B && refBytes[i + 2] == 0x03 && refBytes[i + 3] == 0x04)
                {
                    pk = i;
                    break;
                }
            }
            Assert.True(pk > 0);
            using var ms = new MemoryStream();
            ms.Write(refBytes, 0, pk);
            using (var zipStream = new MemoryStream())
            {
                using (var archive = new ZipArchive(zipStream, ZipArchiveMode.Create, leaveOpen: true))
                {
                    var entry = archive.CreateEntry("model.dat", CompressionLevel.Fastest);
                    using var es = entry.Open();
                    es.Write(modelDat, 0, modelDat.Length);
                }
                byte[] zipBytes = zipStream.ToArray();
                ms.Write(zipBytes, 0, zipBytes.Length);
            }
            return ms.ToArray();
        }

        private static List<(string Name, string Layer)> CollectBaked(InstanceNode node)
        {
            var list = new List<(string, string)> { (node.Name, node.Layer) };
            foreach (var c in node.Children) list.AddRange(CollectBaked(c));
            return list;
        }

        private static List<(string Name, string Layer)> CollectInstanced(InstancedNode node)
        {
            var list = new List<(string, string)> { (node.Name, node.Layer) };
            foreach (var c in node.Children) list.AddRange(CollectInstanced(c));
            return list;
        }

        [Fact]
        public void ResolvesVffD207EdgeAndFaceLayerIdsToLayerNames()
        {
            var layers = TlvRec(
                "993A",
                TlvRec("8C3C", TlvRec("DC05", new byte[] { 1 }), TlvRec("8D3C", Encoding.UTF8.GetBytes("Layer0"))),
                TlvRec("8C3C", TlvRec("DC05", new byte[] { 42 }), TlvRec("8D3C", Encoding.UTF8.GetBytes("Sky Dome"))));
            var edgeLayered = TlvRec(
                "B80B",
                TlvRec("DE05", new byte[] { 10 }),
                TlvRec("D007", TlvRec("D207", new byte[] { 42 })));
            var edgeDefault = TlvRec("B80B", TlvRec("DE05", new byte[] { 11 }));
            var faceLayered = TlvRec(
                "AC0D",
                TlvRec("DE05", new byte[] { 20 }),
                TlvRec("D007", TlvRec("D207", new byte[] { 42 })));
            var faceDefault = TlvRec("AC0D", TlvRec("DE05", new byte[] { 21 }));
            var root = TlvRec("F601", edgeLayered, edgeDefault, faceLayered, faceDefault);
            var modelDat = TlvRec("F401", layers, root);

            var model = SkpFile.Parse(BuildSyntheticVffSkp(modelDat));
            Assert.Equal("Sky Dome", model.Root.Edges[10].Layer);
            Assert.Equal("", model.Root.Edges[11].Layer);
            Assert.Equal("Sky Dome", model.Root.Faces[20].Layer);
            Assert.Equal("", model.Root.Faces[21].Layer);
        }

        [Fact]
        public void ReadsEdgeAndFaceLayersFromClassicFiles()
        {
            var gondola = SkpFile.Open(FixturePath("gondola_v20.skp"));
            int edgesOnLayer = 0;
            int facesOnLayer = 0;
            int facesDefault = 0;

            void Visit(Definition d)
            {
                foreach (var e in d.Edges.Values)
                {
                    if (e.Layer == "Gondulas Laterais") edgesOnLayer++;
                }
                foreach (var f in d.Faces.Values)
                {
                    if (f.Layer == "Gondulas Laterais") facesOnLayer++;
                    else if (f.Layer == "") facesDefault++;
                }
            }

            Visit(gondola.Root);
            foreach (var d in gondola.Definitions.Values) Visit(d);

            Assert.True(edgesOnLayer > 0);
            Assert.Equal(229, facesOnLayer);
            Assert.Equal(1658, facesDefault);

            var lineStyles = SkpFile.Open(FixturePath("sdk_saved_tag_line_styles_2020.skp"));
            Assert.Equal(
                new[] { "Tag 0 ", "Tag 1 Dash", "Tag 2 ", "Tag 3 Dash", "Tag 4 Dot", "Tag 5 Dash" },
                lineStyles.Root.Faces.Values.Select(f => f.Layer).ToArray());
        }

        [Fact]
        public void UsesFaceTagColorsAndUniformLeafLayersOnChairAndTable()
        {
            var opts = new SkpParseOptions { UnpaintedFaces = "tag" };
            var baked = SkpFile.BuildScene(ChairAndTablePath(), opts);
            var instanced = SkpFile.BuildInstancedScene(ChairAndTablePath(), opts);

            static HashSet<(int R, int G, int B)> RgbSet(List<object> mats)
            {
                var s = new HashSet<(int, int, int)>();
                foreach (var mObj in mats)
                {
                    var m = (Dictionary<string, object>)mObj;
                    var pbr = (Dictionary<string, object>)m["pbrMetallicRoughness"];
                    var bcf = (double[])pbr["baseColorFactor"];
                    s.Add(((int)Math.Round(bcf[0] * 255), (int)Math.Round(bcf[1] * 255), (int)Math.Round(bcf[2] * 255)));
                }
                return s;
            }

            var bakedColors = RgbSet(baked.GltfMaterials);
            var instancedColors = RgbSet(instanced.GltfMaterials);
            Assert.DoesNotContain((255, 84, 84), bakedColors);
            Assert.DoesNotContain((255, 84, 84), instancedColors);
            Assert.Contains((120, 150, 190), bakedColors);
            Assert.Contains((190, 150, 120), bakedColors);
            Assert.Equal(bakedColors, instancedColors);

            var bakedNodes = CollectBaked(baked.SceneHierarchy);
            Assert.Contains(("Chair", "Layer0"), bakedNodes);
            Assert.Contains(("Table", "Layer0"), bakedNodes);
            Assert.Contains(("Leg_Chair", "Chair"), bakedNodes);
            Assert.Contains(("Leg_Table", "Table"), bakedNodes);
            Assert.Equal(bakedNodes, CollectInstanced(instanced.SceneHierarchy));
        }

        [Fact]
        public void MixedTwentyUntaggedAndOneHiddenFaceDoNotHijackMeshesOrChildren()
        {
            var b = SkpCreate.NewFile();
            int hiddenTag = b.AddLayer("HiddenTag", (10, 20, 30), hidden: true);
            ComponentDefinitionBuilder childDef;
            using (childDef = b.AddComponentDefinition("Child"))
            {
                childDef.AddFace(new[] { (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0) });
            }
            ComponentDefinitionBuilder parentDef;
            using (parentDef = b.AddComponentDefinition("MixedParent"))
            {
                for (int k = 0; k < 20; k++)
                {
                    double x = k * 2.0;
                    parentDef.AddFace(new[] { (x, 0.0, 0.0), (x + 1.0, 0.0, 0.0), (x + 1.0, 1.0, 0.0), (x, 1.0, 0.0) });
                }
                parentDef.AddFace(
                    new[] { (0.0, 5.0, 0.0), (1.0, 5.0, 0.0), (1.0, 6.0, 0.0), (0.0, 6.0, 0.0) },
                    layer: hiddenTag);
                parentDef.AddInstance(childDef, name: "NestedChild", translation: (0, 10, 0));
            }
            b.AddInstance(parentDef, name: "MixedPlacement");

            var bytes = b.ToBytes();
            var baked = SkpFile.BuildScene(bytes);
            var instanced = SkpFile.BuildInstancedScene(bytes);

            Assert.All(baked.MeshIndex.Values, meta => Assert.Equal("Layer0", meta.Layer));
            var expectedNodes = new List<(string, string)>
            {
                ("ROOT", "Layer0"),
                ("MixedPlacement", "Layer0"),
                ("NestedChild", "Layer0"),
            };
            Assert.Equal(expectedNodes, CollectBaked(baked.SceneHierarchy));
            Assert.Equal(expectedNodes, CollectInstanced(instanced.SceneHierarchy));
        }

        [Fact]
        public void UniformFaceTagAppliesToOwnMeshesRootMeshesAndChildlessNodesOnly()
        {
            var b = SkpCreate.NewFile();
            int roof = b.AddLayer("Roof", (100, 120, 140));
            int leafTag = b.AddLayer("LeafTag", (40, 80, 120));
            ComponentDefinitionBuilder childDef;
            using (childDef = b.AddComponentDefinition("UntaggedChild"))
            {
                childDef.AddFace(new[] { (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0) });
            }
            ComponentDefinitionBuilder parentDef;
            using (parentDef = b.AddComponentDefinition("UniformParent"))
            {
                parentDef.AddFace(
                    new[] { (0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (2.0, 2.0, 0.0), (0.0, 2.0, 0.0) },
                    layer: roof);
                parentDef.AddInstance(childDef, name: "ChildInst", translation: (5, 0, 0));
            }
            ComponentDefinitionBuilder leafDef;
            using (leafDef = b.AddComponentDefinition("LeafDef"))
            {
                leafDef.AddFace(
                    new[] { (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0) },
                    layer: leafTag);
            }
            b.AddFace(
                new[] { (0.0, 20.0, 0.0), (2.0, 20.0, 0.0), (2.0, 22.0, 0.0), (0.0, 22.0, 0.0) },
                layer: roof);
            b.AddInstance(parentDef, name: "ParentInst");
            b.AddInstance(leafDef, name: "LeafInst", translation: (10, 0, 0));

            var bytes = b.ToBytes();
            var baked = SkpFile.BuildScene(bytes);
            var instanced = SkpFile.BuildInstancedScene(bytes);

            var layersByPath = baked.MeshIndex.Values
                .GroupBy(m => m.Path)
                .ToDictionary(g => g.Key, g => g.Select(m => m.Layer).Distinct().ToArray());
            Assert.Equal(new[] { "Roof" }, layersByPath["ROOT"]);
            Assert.Equal(new[] { "Roof" }, layersByPath["ROOT / ParentInst"]);
            Assert.Equal(new[] { "Layer0" }, layersByPath["ROOT / ParentInst / ChildInst"]);
            Assert.Equal(new[] { "LeafTag" }, layersByPath["ROOT / LeafInst"]);

            var expectedNodes = new List<(string, string)>
            {
                ("ROOT", "Layer0"),
                ("ParentInst", "Layer0"),
                ("ChildInst", "Layer0"),
                ("LeafInst", "LeafTag"),
            };
            Assert.Equal(expectedNodes, CollectBaked(baked.SceneHierarchy));
            Assert.Equal(expectedNodes, CollectInstanced(instanced.SceneHierarchy));
        }

        [Fact]
        public void KeepsOwnTagOrInheritedParentTagOverUniformFaceTag()
        {
            var b = SkpCreate.NewFile();
            int wood = b.AddLayer("Wood", (160, 110, 60));
            int furnitureHidden = b.AddLayer("FurnitureHidden", (40, 40, 40), hidden: true);
            int groupTag = b.AddLayer("GroupTag", (30, 140, 90));

            ComponentDefinitionBuilder woodPanel;
            using (woodPanel = b.AddComponentDefinition("WoodPanel"))
            {
                woodPanel.AddFace(
                    new[] { (0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (2.0, 2.0, 0.0), (0.0, 2.0, 0.0) },
                    layer: wood);
            }
            ComponentDefinitionBuilder parentGroup;
            using (parentGroup = b.AddComponentDefinition("ParentGroup"))
            {
                parentGroup.AddInstance(woodPanel, name: "UntaggedInGroup", translation: (5, 0, 0));
            }
            b.AddInstance(woodPanel, name: "HiddenFurniture", layer: furnitureHidden);
            b.AddInstance(parentGroup, name: "TaggedGroup", layer: groupTag, translation: (10, 0, 0));

            var bytes = b.ToBytes();
            var opts = new SkpParseOptions { UnpaintedFaces = "tag" };
            var baked = SkpFile.BuildScene(bytes, opts);
            var instanced = SkpFile.BuildInstancedScene(bytes, opts);

            var meshLayers = baked.MeshIndex.Values.ToDictionary(m => m.Path, m => m.Layer);
            Assert.Equal("FurnitureHidden", meshLayers["ROOT / HiddenFurniture"]);
            Assert.Equal("GroupTag", meshLayers["ROOT / TaggedGroup / UntaggedInGroup"]);

            var expectedNodes = new List<(string, string)>
            {
                ("ROOT", "Layer0"),
                ("HiddenFurniture", "FurnitureHidden"),
                ("TaggedGroup", "GroupTag"),
                ("UntaggedInGroup", "GroupTag"),
            };
            Assert.Equal(expectedNodes, CollectBaked(baked.SceneHierarchy));
            Assert.Equal(expectedNodes, CollectInstanced(instanced.SceneHierarchy));

            static bool HasWoodColor(List<object> mats)
            {
                foreach (var mObj in mats)
                {
                    var m = (Dictionary<string, object>)mObj;
                    var pbr = (Dictionary<string, object>)m["pbrMetallicRoughness"];
                    var bcf = (double[])pbr["baseColorFactor"];
                    if ((int)Math.Round(bcf[0] * 255) == 160 &&
                        (int)Math.Round(bcf[1] * 255) == 110 &&
                        (int)Math.Round(bcf[2] * 255) == 60)
                    {
                        return true;
                    }
                }
                return false;
            }
            Assert.True(HasWoodColor(baked.GltfMaterials));
            Assert.True(HasWoodColor(instanced.GltfMaterials));
        }
    }
}
