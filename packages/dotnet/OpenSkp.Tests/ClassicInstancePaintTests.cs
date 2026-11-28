using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
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

        private static readonly byte[] Png1x1 =
        {
            0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x48,
            0x44, 0x52, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x02, 0x00, 0x00,
            0x00, 0x90, 0x77, 0x53, 0xDE, 0x00, 0x00, 0x00, 0x0C, 0x49, 0x44, 0x41, 0x54, 0x78,
            0x9C, 0x63, 0x38, 0x91, 0x62, 0x04, 0x00, 0x03, 0x56, 0x01, 0x5F, 0xE8, 0x17, 0x84,
            0x52, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE, 0x42, 0x60, 0x82,
        };

        private static readonly byte[] Png2x1 =
        {
            0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x48,
            0x44, 0x52, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x01, 0x08, 0x02, 0x00, 0x00,
            0x00, 0x7B, 0x40, 0xE8, 0xDD, 0x00, 0x00, 0x00, 0x0F, 0x49, 0x44, 0x41, 0x54, 0x78,
            0x9C, 0x63, 0x30, 0x4A, 0x39, 0x61, 0x94, 0x72, 0x02, 0x00, 0x08, 0x6D, 0x02, 0xBD,
            0xEB, 0xAE, 0x9B, 0xE7, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE, 0x42,
            0x60, 0x82,
        };

        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        private static (double MinU, double MaxU, double MinV, double MaxV) UvBounds(float[] uvs)
        {
            float minU = uvs[0], maxU = uvs[0], minV = uvs[1], maxV = uvs[1];
            for (int i = 0; i + 1 < uvs.Length; i += 2)
            {
                if (uvs[i] < minU) minU = uvs[i];
                if (uvs[i] > maxU) maxU = uvs[i];
                if (uvs[i + 1] < minV) minV = uvs[i + 1];
                if (uvs[i + 1] > maxV) maxV = uvs[i + 1];
            }
            return (Math.Round(minU, 4), Math.Round(maxU, 4), Math.Round(minV, 4), Math.Round(maxV, 4));
        }

        [Fact]
        public void SdkClassicAndVffTexturedPaintedInstancesAndTags()
        {
            foreach (var fname in new[] { "instance_textured_paint_2020.skp", "instance_textured_paint_2021.skp" })
            {
                var bytes = File.ReadAllBytes(FixturePath(fname));
                var baked = SkpFile.BuildScene(bytes);
                var instanced = SkpFile.BuildInstancedScene(bytes);

                Assert.Equal(2, baked.Textures.Count);
                Assert.Equal(2, instanced.Textures.Count);
                Assert.Equal(10, baked.GlbPrimitives.Count);
                Assert.Equal(6, instanced.MeshResources.Count);

                var expectedTopLayers = new Dictionary<string, string>
                {
                    ["BrickPanel"] = "ExteriorTag",
                    ["WoodPanel"] = "InteriorTag",
                    ["PlainPanel"] = "Layer0",
                    ["BrickGroup"] = "ExteriorTag",
                    ["OuterInst"] = "ExteriorTag",
                };
                Assert.Equal(expectedTopLayers, baked.SceneHierarchy.Children.ToDictionary(n => n.Name, n => n.Layer));
                Assert.Equal(expectedTopLayers, instanced.SceneHierarchy.Children.ToDictionary(n => n.Name, n => n.Layer));

                var expectedNestedLayers = new Dictionary<string, string>
                {
                    ["ChildInherits"] = "ExteriorTag",
                    ["ChildOverrides"] = "InteriorTag",
                };
                Assert.Equal(
                    expectedNestedLayers,
                    baked.SceneHierarchy.Children.First(n => n.Name == "OuterInst").Children.ToDictionary(n => n.Name, n => n.Layer));
                Assert.Equal(
                    expectedNestedLayers,
                    instanced.SceneHierarchy.Children.First(n => n.Name == "OuterInst").Children.ToDictionary(n => n.Name, n => n.Layer));

                var byId = instanced.MeshResources.ToDictionary(r => r.Id);
                var top = instanced.SceneHierarchy.Children.ToDictionary(n => n.Name);
                var nested = top["OuterInst"].Children.ToDictionary(n => n.Name);

                int? TexIndexOf(LocalPrimitive p)
                {
                    var m = (Dictionary<string, object>)instanced.GltfMaterials[p.MaterialIndex];
                    var pbr = (Dictionary<string, object>)m["pbrMetallicRoughness"];
                    if (pbr.TryGetValue("baseColorTexture", out var texObj) && texObj is Dictionary<string, object> texDict)
                    {
                        return (int)texDict["index"];
                    }
                    return null;
                }

                var grpPrim = Assert.Single(byId[top["BrickGroup"].MeshResourceId!].Primitives);
                var inhPrim = Assert.Single(byId[nested["ChildInherits"].MeshResourceId!].Primitives);
                var ovrPrim = Assert.Single(byId[nested["ChildOverrides"].MeshResourceId!].Primitives);

                Assert.NotNull(TexIndexOf(grpPrim));
                Assert.NotNull(TexIndexOf(ovrPrim));
                Assert.NotEqual(TexIndexOf(grpPrim), TexIndexOf(ovrPrim));
                Assert.Equal((0.0, 1.0, 0.0, 1.0), UvBounds(grpPrim.Uvs));
                Assert.Equal((0.0, 1.0, 0.0, 1.0), UvBounds(inhPrim.Uvs));
                Assert.Equal((0.0, 2.5, 0.0, 2.5), UvBounds(ovrPrim.Uvs));
            }
        }

        [Fact]
        public void WriterUnnamedAndNamedTexturedMaterialsOnInstancesAndGroups()
        {
            string tempDir = Path.Combine(Path.GetTempPath(), "openskp-dotnet-559-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(tempDir);
            try
            {
                string tex1 = Path.Combine(tempDir, "a.png");
                string tex2 = Path.Combine(tempDir, "b.png");
                File.WriteAllBytes(tex1, Png1x1);
                File.WriteAllBytes(tex2, Png2x1);

                var rect = new (double, double, double)[] { (0, 0, 0), (10, 0, 0), (10, 20, 0), (0, 20, 0) };
                var builder = SkpCreate.NewFile();
                int namedMat = builder.AddTextureMaterial("Brick", tex1, appliedHeight: 20.0, appliedWidth: 10.0);
                int unnamedMat = builder.AddTextureMaterial("", tex2, appliedHeight: 8.0, appliedWidth: 4.0);
                int ext = builder.AddLayer("Ext");
                int inter = builder.AddLayer("Int");

                ComponentDefinitionBuilder panel;
                using (panel = builder.AddComponentDefinition("Panel"))
                {
                    panel.AddFace(rect);
                }
                using (var grp = builder.AddGroup("PaintedGroup", translation: (40, 0, 0), material: namedMat, layer: ext))
                {
                    grp.AddFace(rect);
                }
                builder.AddInstance(panel, name: "NamedInst", material: namedMat, layer: ext);
                builder.AddInstance(panel, name: "UnnamedInst", translation: (20, 0, 0), material: unnamedMat, layer: inter);

                var bytes = builder.ToBytes();
                var baked = SkpFile.BuildScene(bytes);
                var instanced = SkpFile.BuildInstancedScene(bytes);

                Assert.Equal(2, baked.Textures.Count);
                Assert.Equal(2, instanced.Textures.Count);
                var expectedLayers = new Dictionary<string, string>
                {
                    ["PaintedGroup"] = "Ext",
                    ["NamedInst"] = "Ext",
                    ["UnnamedInst"] = "Int",
                };
                Assert.Equal(expectedLayers, baked.SceneHierarchy.Children.ToDictionary(n => n.Name, n => n.Layer));
                Assert.Equal(expectedLayers, instanced.SceneHierarchy.Children.ToDictionary(n => n.Name, n => n.Layer));

                var byId = instanced.MeshResources.ToDictionary(r => r.Id);
                var nodes = instanced.SceneHierarchy.Children.ToDictionary(n => n.Name);
                Assert.NotEqual(nodes["NamedInst"].MeshResourceId, nodes["UnnamedInst"].MeshResourceId);
                Assert.Equal((0.0, 1.0, 0.0, 1.0), UvBounds(byId[nodes["NamedInst"].MeshResourceId!].Primitives[0].Uvs));
                Assert.Equal((0.0, 2.5, 0.0, 2.5), UvBounds(byId[nodes["UnnamedInst"].MeshResourceId!].Primitives[0].Uvs));
            }
            finally
            {
                try { Directory.Delete(tempDir, true); } catch { }
            }
        }

        [Fact]
        public void InstancedSceneIsolatesSameNameSameRgbInheritedMaterialVariants()
        {
            var raw = File.ReadAllBytes(FixturePath("instance_textured_paint_2021.skp"));
            int pkPos = 0;
            for (int i = 0; i + 4 <= raw.Length; i++)
            {
                if (raw[i] == 0x50 && raw[i + 1] == 0x4B && raw[i + 2] == 0x03 && raw[i + 3] == 0x04)
                {
                    pkPos = i;
                    break;
                }
            }

            using var outMs = new MemoryStream();
            outMs.Write(raw, 0, pkPos);
            using (var zipOutStream = new MemoryStream())
            {
                using (var zin = new ZipArchive(new MemoryStream(raw, pkPos, raw.Length - pkPos), ZipArchiveMode.Read))
                using (var zout = new ZipArchive(zipOutStream, ZipArchiveMode.Create, leaveOpen: true))
                {
                    foreach (var entry in zin.Entries)
                    {
                        byte[] data;
                        using (var es = entry.Open())
                        using (var ems = new MemoryStream())
                        {
                            es.CopyTo(ems);
                            data = ems.ToArray();
                        }
                        if (entry.FullName == "materials/BrickTex/material.xml" || entry.FullName == "materials/WoodTex/material.xml")
                        {
                            string xml = Encoding.UTF8.GetString(data)
                                .Replace("name=\"BrickTex\"", "name=\"Shared\"")
                                .Replace("name=\"WoodTex\"", "name=\"Shared\"");
                            xml = Regex.Replace(xml, @"colorRed=""\d+""", @"colorRed=""170""");
                            xml = Regex.Replace(xml, @"colorGreen=""\d+""", @"colorGreen=""58""");
                            xml = Regex.Replace(xml, @"colorBlue=""\d+""", @"colorBlue=""40""");
                            data = Encoding.UTF8.GetBytes(xml);
                        }
                        var outEntry = zout.CreateEntry(entry.FullName);
                        using var oes = outEntry.Open();
                        oes.Write(data, 0, data.Length);
                    }
                }
                var zipBytes = zipOutStream.ToArray();
                outMs.Write(zipBytes, 0, zipBytes.Length);
            }

            var instanced = SkpFile.BuildInstancedScene(outMs.ToArray());
            var byId = instanced.MeshResources.ToDictionary(r => r.Id);
            var top = instanced.SceneHierarchy.Children.ToDictionary(n => n.Name);
            var nested = top["OuterInst"].Children.ToDictionary(n => n.Name);

            Assert.NotEqual(top["BrickPanel"].MeshResourceId, top["WoodPanel"].MeshResourceId);
            Assert.NotEqual(nested["ChildInherits"].MeshResourceId, nested["ChildOverrides"].MeshResourceId);
            Assert.Equal((0.0, 1.0, 0.0, 1.0), UvBounds(byId[nested["ChildInherits"].MeshResourceId!].Primitives[0].Uvs));
            Assert.Equal((0.0, 2.5, 0.0, 2.5), UvBounds(byId[nested["ChildOverrides"].MeshResourceId!].Primitives[0].Uvs));
        }

        private static readonly byte[] PngBrick4x4 =
        {
            0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x48,
            0x44, 0x52, 0x00, 0x00, 0x00, 0x04, 0x00, 0x00, 0x00, 0x04, 0x08, 0x02, 0x00, 0x00,
            0x00, 0x26, 0x93, 0x09, 0x29, 0x00, 0x00, 0x00, 0x18, 0x49, 0x44, 0x41, 0x54, 0x78,
            0xDA, 0x63, 0xD8, 0xE0, 0x62, 0x00, 0x41, 0x97, 0xCE, 0x1E, 0x66, 0x80, 0x50, 0x10,
            0x3E, 0x03, 0x4E, 0x19, 0x00, 0x5A, 0xCA, 0x17, 0x39, 0xF4, 0x43, 0xAB, 0x79, 0x00,
            0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE, 0x42, 0x60, 0x82,
        };

        private static void WriteGroundTruthWriterSkp(string outSkpPath, string brickPngPath)
        {
            File.WriteAllBytes(brickPngPath, PngBrick4x4);

            var b = SkpCreate.NewFile();
            int plainRed = b.AddMaterial("PlainRed", (210, 50, 40, 255));
            int brickTex = b.AddTextureMaterial("BrickTex", brickPngPath, appliedHeight: 20.0, appliedWidth: 10.0, filename: "brick.png");
            int ownGold = b.AddMaterial("OwnGold", (230, 180, 30, 255));
            int tag = b.AddLayer("PaintedTag", color: (40, 120, 200));

            var fUnp = new (double, double, double)[] { (0, 0, 0), (10, 0, 0), (10, 20, 0), (0, 20, 0) };
            var fOwn = new (double, double, double)[] { (0, 30, 0), (10, 30, 0), (10, 50, 0), (0, 50, 0) };

            ComponentDefinitionBuilder panel;
            using (panel = b.AddComponentDefinition("PanelDef"))
            {
                panel.AddFace(fUnp);
                panel.AddFace(fOwn, material: ownGold, backMaterial: ownGold);
            }
            ComponentDefinitionBuilder outer;
            using (outer = b.AddComponentDefinition("OuterDef"))
            {
                outer.AddInstance(panel, name: "InnerUnpainted", translation: (5, 0, 0));
            }
            using (var g1 = b.AddGroup("PlainGroup", translation: (60, 0, 0), material: plainRed, layer: tag))
            {
                g1.AddFace(fUnp);
                g1.AddFace(fOwn, material: ownGold, backMaterial: ownGold);
            }
            using (var g2 = b.AddGroup("TexturedGroup", translation: (90, 0, 0), material: brickTex, layer: tag))
            {
                g2.AddFace(fUnp);
                g2.AddFace(fOwn, material: ownGold, backMaterial: ownGold);
            }

            b.AddInstance(panel, name: "PlainComp", translation: (0, 0, 0), material: plainRed, layer: tag);
            b.AddInstance(panel, name: "TexturedComp", translation: (30, 0, 0), material: brickTex, layer: tag);
            b.AddInstance(outer, name: "OuterTextured", translation: (120, 0, 0), material: brickTex, layer: tag);
            File.WriteAllBytes(outSkpPath, b.ToBytes());
        }

        [Fact]
        public void MatchesSketchUpGroundTruthAcross2017_2020_2021AndWriterFiles()
        {
            string tempDir = Path.Combine(Path.GetTempPath(), "openskp-gt-559-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(tempDir);
            try
            {
                string writerSkp = Path.Combine(tempDir, "gt_writer.skp");
                WriteGroundTruthWriterSkp(writerSkp, Path.Combine(tempDir, "brick.png"));

                var sources = new (string Label, string SkpPath)[]
                {
                    ("2017", FixturePath("sdk_scene_instance_paint_2017.skp")),
                    ("2020", FixturePath("sdk_scene_instance_paint_2020.skp")),
                    ("2021", FixturePath("sdk_scene_instance_paint_2021.skp")),
                    ("writer", writerSkp),
                };

                var expectedParse = new Dictionary<string, (bool IsGroup, string OwnLayer, string? OwnMat, bool HasTex)>
                {
                    ["PlainComp"] = (false, "PaintedTag", "PlainRed", false),
                    ["TexturedComp"] = (false, "PaintedTag", "BrickTex", true),
                    ["PlainGroup"] = (true, "PaintedTag", "PlainRed", false),
                    ["TexturedGroup"] = (true, "PaintedTag", "BrickTex", true),
                    ["OuterTextured"] = (false, "PaintedTag", "BrickTex", true),
                    ["OuterTextured / InnerUnpainted"] = (false, "", null, false),
                };
                var expectedNodeLayers = expectedParse.Keys.ToDictionary(k => k, _ => "PaintedTag");
                var plainParts = new List<(string Name, (int, int, int, int) Rgba, bool HasTex, (double, double, double, double) Uv)>
                {
                    ("OwnGold", (230, 180, 30, 255), false, (0.0, 10.0, 30.0, 50.0)),
                    ("PlainRed", (210, 50, 40, 255), false, (0.0, 10.0, 0.0, 20.0)),
                };

                foreach (var (label, skpPath) in sources)
                {
                    var brickRgba = label == "writer" ? (255, 255, 255, 255) : (184, 102, 84, 255);
                    var texturedParts = new List<(string Name, (int, int, int, int) Rgba, bool HasTex, (double, double, double, double) Uv)>
                    {
                        ("BrickTex", brickRgba, true, (0.0, 1.0, 0.0, 1.0)),
                        ("OwnGold", (230, 180, 30, 255), false, (0.0, 10.0, 30.0, 50.0)),
                    };
                    var expectedLeafParts = new Dictionary<string, List<(string Name, (int, int, int, int) Rgba, bool HasTex, (double, double, double, double) Uv)>>
                    {
                        ["PlainComp"] = plainParts,
                        ["PlainGroup"] = plainParts,
                        ["TexturedComp"] = texturedParts,
                        ["TexturedGroup"] = texturedParts,
                        ["OuterTextured / InnerUnpainted"] = texturedParts,
                    };
                    var model = SkpFile.Open(skpPath);
                    var bytes = File.ReadAllBytes(skpPath);
                    var parsedNodes = new Dictionary<string, (bool IsGroup, string OwnLayer, string? OwnMat, bool HasTex)>();
                    void WalkModel(Definition defn, string prefix)
                    {
                        foreach (var ins in defn.Instances)
                        {
                            var cdef = model.Definitions[ins.RefIdx!.Value];
                            string p = string.IsNullOrEmpty(prefix) ? ins.Name : $"{prefix} / {ins.Name}";
                            Material? mat = ins.MaterialId is long mid && model.MaterialsById.TryGetValue(mid, out var m) ? m : null;
                            parsedNodes[p] = (
                                cdef.IsGroup,
                                ins.Layer,
                                mat?.Name,
                                mat?.Texture?.Data != null && mat.Texture.Data.Length > 0
                            );
                            WalkModel(cdef, p);
                        }
                    }
                    WalkModel(model.Root, "");
                    Assert.True(expectedParse.Count == parsedNodes.Count && !expectedParse.Except(parsedNodes).Any(), label);

                    var baked = SkpFile.BuildScene(bytes);
                    var instanced = SkpFile.BuildInstancedScene(bytes);
                    Assert.Single(baked.Textures);
                    Assert.Single(instanced.Textures);
                    Assert.Equal(10, baked.GlbPrimitives.Count);
                    Assert.Equal(4, instanced.MeshResources.Count);

                    var bakedLayers = new Dictionary<string, string>();
                    void WalkBaked(InstanceNode node, string prefix)
                    {
                        foreach (var ch in node.Children)
                        {
                            string p = string.IsNullOrEmpty(prefix) ? ch.Name : $"{prefix} / {ch.Name}";
                            bakedLayers[p] = ch.Layer;
                            WalkBaked(ch, p);
                        }
                    }
                    WalkBaked(baked.SceneHierarchy, "");
                    Assert.Equal(expectedNodeLayers, bakedLayers);

                    var instLayers = new Dictionary<string, string>();
                    var instNodes = new Dictionary<string, InstancedNode>();
                    void WalkInst(InstancedNode node, string prefix)
                    {
                        foreach (var ch in node.Children)
                        {
                            string p = string.IsNullOrEmpty(prefix) ? ch.Name : $"{prefix} / {ch.Name}";
                            instLayers[p] = ch.Layer;
                            instNodes[p] = ch;
                            WalkInst(ch, p);
                        }
                    }
                    WalkInst(instanced.SceneHierarchy, "");
                    Assert.Equal(expectedNodeLayers, instLayers);

                    static (string Name, (int, int, int, int) Rgba, bool HasTex, (double, double, double, double) Uv) PartTuple(object matObj, float[] uvs)
                    {
                        var gm = (Dictionary<string, object>)matObj;
                        var pbr = (Dictionary<string, object>)gm["pbrMetallicRoughness"];
                        var bcf = (double[])pbr["baseColorFactor"];
                        var rgba = (
                            (int)Math.Round(bcf[0] * 255.0),
                            (int)Math.Round(bcf[1] * 255.0),
                            (int)Math.Round(bcf[2] * 255.0),
                            (int)Math.Round(bcf[3] * 255.0)
                        );
                        bool hasTex = pbr.TryGetValue("baseColorTexture", out var tObj)
                            && tObj is Dictionary<string, object> td
                            && td.TryGetValue("index", out var ti)
                            && (int)ti == 0;
                        Assert.True(gm.TryGetValue("doubleSided", out var ds) && ds is bool dsb && dsb);
                        string mName = gm.TryGetValue("name", out var nObj) && nObj is string ns ? ns : "";
                        return (mName, rgba, hasTex, UvBounds(uvs));
                    }

                    var bakedByPath = new Dictionary<string, List<(string Name, (int, int, int, int) Rgba, bool HasTex, (double, double, double, double) Uv)>>();
                    foreach (var p in baked.GlbPrimitives)
                    {
                        var meta = baked.MeshIndex[p.GeomName];
                        Assert.Equal("PaintedTag", meta.Layer);
                        string rel = meta.Path.StartsWith("ROOT / ", StringComparison.Ordinal) ? meta.Path.Substring(7) : meta.Path;
                        if (!bakedByPath.TryGetValue(rel, out var list))
                        {
                            list = new List<(string Name, (int, int, int, int) Rgba, bool HasTex, (double, double, double, double) Uv)>();
                            bakedByPath[rel] = list;
                        }
                        list.Add(PartTuple(baked.GltfMaterials[p.MaterialIndex], p.Uvs));
                    }
                    foreach (var kv in expectedLeafParts)
                    {
                        var got = bakedByPath[kv.Key].OrderBy(x => x.Name, StringComparer.Ordinal).ToList();
                        Assert.Equal(kv.Value, got);
                    }

                    Assert.Null(instNodes["OuterTextured"].MeshResourceId);
                    Assert.Equal(instNodes["TexturedComp"].MeshResourceId, instNodes["OuterTextured / InnerUnpainted"].MeshResourceId);
                    Assert.NotEqual(instNodes["PlainComp"].MeshResourceId, instNodes["TexturedComp"].MeshResourceId);

                    var byId = instanced.MeshResources.ToDictionary(r => r.Id);
                    foreach (var kv in expectedLeafParts)
                    {
                        var res = byId[instNodes[kv.Key].MeshResourceId!];
                        var got = res.Primitives
                            .Select(pr => PartTuple(instanced.GltfMaterials[pr.MaterialIndex], pr.Uvs))
                            .OrderBy(x => x.Name, StringComparer.Ordinal)
                            .ToList();
                        Assert.Equal(kv.Value, got);
                    }
                }
            }
            finally
            {
                try { Directory.Delete(tempDir, true); } catch { }
            }
        }
    }
}
