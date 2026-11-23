using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Xunit;

namespace OpenSkp.Tests
{
    public class FaceEdgeLayersTests
    {
        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        private static string ChairAndTablePath()
        {
            var dir = AppContext.BaseDirectory;
            while (dir != null)
            {
                var candidate = Path.Combine(dir, "examples", "web-viewer", "samples", "chair_and_table.skp");
                if (File.Exists(candidate)) return candidate;
                dir = Path.GetDirectoryName(dir);
            }
            throw new FileNotFoundException("Could not locate examples/web-viewer/samples/chair_and_table.skp");
        }

        private static byte[] TlvNodeBytes(string tag, params byte[][] children)
        {
            int len = children.Sum(c => c.Length);
            var buf = new byte[6 + len];
            buf[0] = Convert.ToByte(tag.Substring(0, 2), 16);
            buf[1] = Convert.ToByte(tag.Substring(2, 2), 16);
            BitConverter.GetBytes((uint)len).CopyTo(buf, 2);
            int off = 6;
            foreach (var c in children)
            {
                Buffer.BlockCopy(c, 0, buf, off, c.Length);
                off += c.Length;
            }
            return buf;
        }

        [Fact]
        public void ResolvesVffD207EdgeAndFaceLayerIdsToNames()
        {
            var edgeLayered = TlvNodeBytes("B80B",
                TlvNodeBytes("DE05", new byte[] { 10 }),
                TlvNodeBytes("D007", TlvNodeBytes("D207", new byte[] { 42 })));
            var edgeDefault = TlvNodeBytes("B80B",
                TlvNodeBytes("DE05", new byte[] { 11 }));
            var faceLayered = TlvNodeBytes("AC0D",
                TlvNodeBytes("DE05", new byte[] { 20 }),
                TlvNodeBytes("D007", TlvNodeBytes("D207", new byte[] { 42 })));
            var faceDefault = TlvNodeBytes("AC0D",
                TlvNodeBytes("DE05", new byte[] { 21 }));

            var all = edgeLayered.Concat(edgeDefault).Concat(faceLayered).Concat(faceDefault).ToArray();
            var chunked = ChunkedBuffer.FromArray(all);
            var nodes = Tlv.ParseRecursive(chunked, 0, chunked.Length);

            var builder = new GeometryBuilder();
            Geometry.ExtractGeometryFromNodes(nodes, builder);

            var layerIdToName = new Dictionary<long, string>
            {
                [1] = "Layer0",
                [42] = "Sky Dome",
            };
            var rootDef = SkpFile.BuildDefinition(
                0,
                new Geometry.RawDefinition { Guid = "ROOT", Name = "ROOT_MODEL", Builder = builder },
                layerIdToName);
            Assert.Equal("Sky Dome", rootDef.Edges[10].Layer);
            Assert.Equal("", rootDef.Edges[11].Layer);
            Assert.Equal("Sky Dome", rootDef.Faces[20].Layer);
            Assert.Equal("", rootDef.Faces[21].Layer);
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
        public void UsesMajorityFaceLayerInBuildSceneAndBuildInstancedSceneForChairAndTable()
        {
            var bytes = File.ReadAllBytes(ChairAndTablePath());
            var baked = SkpFile.BuildScene(bytes);
            var instanced = SkpFile.BuildInstancedScene(bytes);

            static HashSet<(int, int, int)> RgbSet(List<object> mats)
            {
                var s = new HashSet<(int, int, int)>();
                foreach (var m in mats.Cast<Dictionary<string, object>>())
                {
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

            static List<(string Name, string Layer)> CollectBakedLayers(InstanceNode n)
            {
                var list = new List<(string, string)> { (n.Name, n.Layer) };
                foreach (var c in n.Children) list.AddRange(CollectBakedLayers(c));
                return list;
            }

            static List<(string Name, string Layer)> CollectInstancedLayers(InstancedNode n)
            {
                var list = new List<(string, string)> { (n.Name, n.Layer) };
                foreach (var c in n.Children) list.AddRange(CollectInstancedLayers(c));
                return list;
            }

            Assert.Equal(CollectBakedLayers(baked.SceneHierarchy), CollectInstancedLayers(instanced.SceneHierarchy));
        }
    }
}
