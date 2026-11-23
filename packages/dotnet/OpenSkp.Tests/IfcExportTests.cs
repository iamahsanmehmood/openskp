using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using OpenSkp;
using Xunit;

namespace OpenSkp.Tests
{
    public class IfcExportTests
    {
        [Fact]
        public void GenerateIfcGuid_ReturnsValid22CharString()
        {
            string guid = IfcExport.GenerateIfcGuid();
            Assert.NotNull(guid);
            Assert.Equal(22, guid.Length);
        }

        [Fact]
        public void ClassifyElement_ClassifiesNamesToIfcTypes()
        {
            Assert.Equal("IFCWALL", IfcExport.ClassifyElement("Main Wall").StepType);
            Assert.Equal("IFCDOOR", IfcExport.ClassifyElement("Front Door").StepType);
            Assert.Equal("IFCWINDOW", IfcExport.ClassifyElement("Office Window").StepType);
            Assert.Equal("IFCSLAB", IfcExport.ClassifyElement("Concrete Slab").StepType);
            Assert.Equal("IFCBEAM", IfcExport.ClassifyElement("Steel Beam").StepType);
        }

        [Fact]
        public void ClassifyElement_FallsBackToLayerNameWhenComponentNameHasNoKeyword()
        {
            // SketchUp default names carry no signal, but a BIM-style
            // layer/tag often does (openskp#238).
            Assert.Equal("IFCWALL", IfcExport.ClassifyElement("Component#109415", "Walls").StepType);
            Assert.Equal("IFCDOOR", IfcExport.ClassifyElement("Group#3", "Doors").StepType);
        }

        [Fact]
        public void ClassifyElement_PrefersComponentNameOverLayerName()
        {
            Assert.Equal("IFCDOOR", IfcExport.ClassifyElement("Interior Door", "Walls").StepType);
        }

        [Fact]
        public void ClassifyElement_FallsBackToGenericProxyWhenNeitherMatches()
        {
            Assert.Equal("IFCBUILDINGELEMENTPROXY", IfcExport.ClassifyElement("Component#109415", "Layer0").StepType);
            Assert.Equal("IFCBUILDINGELEMENTPROXY", IfcExport.ClassifyElement("Component#109415").StepType);
        }

        [Fact]
        public void ToIfc_UsesLayerNameFallbackForUnnamedComponents()
        {
            var scene = new Scene
            {
                SceneHierarchy = new InstanceNode { Name = "Root", DefinitionName = "RootDef" },
                MeshIndex = new Dictionary<string, MeshMetadata>
                {
                    ["Component#109415"] = new MeshMetadata
                    {
                        Name = "Component#109415",
                        Layer = "Walls"
                    }
                },
                GlbPrimitives = new List<GlbPrimitive>
                {
                    new GlbPrimitive
                    {
                        GeomName = "Component#109415",
                        MaterialIndex = 0,
                        Positions = new float[] { 0f, 0f, 0f, 1f, 0f, 0f, 0f, 1f, 0f },
                        Normals = new float[] { 0f, 0f, 1f, 0f, 0f, 1f, 0f, 0f, 1f },
                        Uvs = new float[] { 0f, 0f, 1f, 0f, 0f, 1f },
                        Indices = new uint[] { 0, 1, 2 }
                    }
                }
            };

            string ifcText = IfcExport.ToIfc(scene);
            Assert.Contains("IFCWALL(", ifcText);
            Assert.DoesNotContain("IFCBUILDINGELEMENTPROXY", ifcText);
        }

        [Fact]
        public void ToIfc_AcceptsCustomClassifierOverride()
        {
            var scene = new Scene
            {
                SceneHierarchy = new InstanceNode { Name = "Root", DefinitionName = "RootDef" },
                MeshIndex = new Dictionary<string, MeshMetadata>
                {
                    ["Outer Wall"] = new MeshMetadata { Name = "Outer Wall" }
                },
                GlbPrimitives = new List<GlbPrimitive>
                {
                    new GlbPrimitive
                    {
                        GeomName = "Outer Wall",
                        MaterialIndex = 0,
                        Positions = new float[] { 0f, 0f, 0f, 1f, 0f, 0f, 0f, 1f, 0f },
                        Normals = new float[] { 0f, 0f, 1f, 0f, 0f, 1f, 0f, 0f, 1f },
                        Uvs = new float[] { 0f, 0f, 1f, 0f, 0f, 1f },
                        Indices = new uint[] { 0, 1, 2 }
                    }
                }
            };

            string ifcText = IfcExport.ToIfc(scene, IfcExport.MetresToInches, "IFC4", (geomName, layerName) => ("IFCCOLUMN", "IfcColumn"));
            Assert.DoesNotContain("IFCWALL(", ifcText);
            Assert.Contains("IFCCOLUMN(", ifcText);
        }

        [Fact]
        public void ToIfc_SerializesSceneToIfc4StepText()
        {
            var scene = new Scene
            {
                SceneHierarchy = new InstanceNode { Name = "Root", DefinitionName = "RootDef" },
                MeshIndex = new Dictionary<string, MeshMetadata>
                {
                    ["Outer Wall"] = new MeshMetadata
                    {
                        Name = "Outer Wall",
                        DefinitionName = "WallDef",
                        Properties = new Dictionary<string, string> { ["Thickness"] = "200mm" }
                    }
                },
                GlbPrimitives = new List<GlbPrimitive>
                {
                    new GlbPrimitive
                    {
                        GeomName = "Outer Wall",
                        MaterialIndex = 0,
                        Positions = new float[] { 0f, 0f, 0f, 1f, 0f, 0f, 0f, 1f, 0f },
                        Normals = new float[] { 0f, 0f, 1f, 0f, 0f, 1f, 0f, 0f, 1f },
                        Uvs = new float[] { 0f, 0f, 1f, 0f, 0f, 1f },
                        Indices = new uint[] { 0, 1, 2 }
                    }
                }
            };

            string ifcText = IfcExport.ToIfc(scene);
            Assert.Contains("ISO-10303-21;", ifcText);
            Assert.Contains("HEADER;", ifcText);
            Assert.Contains("FILE_SCHEMA(('IFC4'));", ifcText);
            Assert.Contains("IFCPROJECT", ifcText);
            Assert.Contains("IFCSITE", ifcText);
            Assert.Contains("IFCBUILDING", ifcText);
            Assert.Contains("IFCBUILDINGSTOREY", ifcText);
            Assert.Contains("IFCWALL", ifcText);
            Assert.Contains("IFCTRIANGULATEDFACESET", ifcText);
            Assert.Contains("IFCCARTESIANPOINTLIST3D", ifcText);
            Assert.Contains("IFCPROPERTYSET", ifcText);
            Assert.Contains("ENDSEC;", ifcText);
        }

        [Fact]
        public void ToIfc_DeclaresMillimetresAndScalesToMatch()
        {
            var scene = new Scene
            {
                SceneHierarchy = new InstanceNode { Name = "Root", DefinitionName = "RootDef" },
                MeshIndex = new Dictionary<string, MeshMetadata>
                {
                    ["Test Triangle"] = new MeshMetadata { Name = "Test Triangle" }
                },
                GlbPrimitives = new List<GlbPrimitive>
                {
                    new GlbPrimitive
                    {
                        GeomName = "Test Triangle",
                        MaterialIndex = 0,
                        Positions = new float[] { 0f, 0f, 0f, 1f, 0f, 0f, 0f, 0f, 1f },
                        Normals = new float[] { 0f, 0f, 1f, 0f, 0f, 1f, 0f, 0f, 1f },
                        Uvs = new float[] { 0f, 0f, 1f, 0f, 0f, 1f },
                        Indices = new uint[] { 0, 1, 2 }
                    }
                }
            };

            string ifcText = IfcExport.ToIfc(scene);
            Assert.Contains("IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.)", ifcText);
            Assert.Contains("(1000.000000,-0.000000,0.000000)", ifcText);
        }

        [Fact]
        public void ToIfc_ConvertsGltfYUpToIfcZUp()
        {
            var scene = new Scene
            {
                SceneHierarchy = new InstanceNode { Name = "Root", DefinitionName = "RootDef" },
                MeshIndex = new Dictionary<string, MeshMetadata>
                {
                    ["Test Triangle"] = new MeshMetadata { Name = "Test Triangle" }
                },
                GlbPrimitives = new List<GlbPrimitive>
                {
                    new GlbPrimitive
                    {
                        GeomName = "Test Triangle",
                        MaterialIndex = 0,
                        Positions = new float[] { 0f, 0f, 0f, 2f, 3f, 5f, 0f, 1f, 0f },
                        Normals = new float[] { 0f, 0f, 1f, 0f, 0f, 1f, 0f, 0f, 1f },
                        Uvs = new float[] { 0f, 0f, 1f, 0f, 0f, 1f },
                        Indices = new uint[] { 0, 1, 2 }
                    }
                }
            };

            string ifcText = IfcExport.ToIfc(scene, scale: 1.0);
            Assert.Contains("(2.000000,-5.000000,3.000000)", ifcText);
            Assert.DoesNotContain("(2.000000,3.000000,5.000000)", ifcText);
        }

        [Fact]
        public void ToIfc_UsesRealInstanceNameNotInternalKey()
        {
            var scene = new Scene
            {
                SceneHierarchy = new InstanceNode { Name = "Root", DefinitionName = "RootDef" },
                MeshIndex = new Dictionary<string, MeshMetadata>
                {
                    ["mesh_3_ROOT__W1_Layer0"] = new MeshMetadata { Name = "W1", Layer = "Layer0" }
                },
                GlbPrimitives = new List<GlbPrimitive>
                {
                    new GlbPrimitive
                    {
                        GeomName = "mesh_3_ROOT__W1_Layer0",
                        MaterialIndex = 0,
                        Positions = new float[] { 0f, 0f, 0f, 1f, 0f, 0f, 0f, 1f, 0f },
                        Normals = new float[] { 0f, 0f, 1f, 0f, 0f, 1f, 0f, 0f, 1f },
                        Uvs = new float[] { 0f, 0f, 1f, 0f, 0f, 1f },
                        Indices = new uint[] { 0, 1, 2 }
                    }
                }
            };

            string ifcText = IfcExport.ToIfc(scene);
            Assert.Contains("'W1'", ifcText);
            Assert.DoesNotContain("mesh_3_ROOT", ifcText);
        }

        [Fact]
        public void ToIfc_WritesAPropertySetPerExtraAttributeDictionaryAndSkipsEmpty()
        {
            var scene = new Scene
            {
                SceneHierarchy = new InstanceNode { Name = "Root", DefinitionName = "RootDef" },
                MeshIndex = new Dictionary<string, MeshMetadata>
                {
                    ["mesh_0_ROOT__Profile25_Layer0"] = new MeshMetadata
                    {
                        Name = "Profile25",
                        Properties = new Dictionary<string, string> { ["width"] = "10.0" },
                        AttributeDictionaries = new Dictionary<string, Dictionary<string, string>>
                        {
                            ["fbd-einfo"] = new Dictionary<string, string>
                            {
                                ["code"] = "aPf",
                                ["angle"] = "90"
                            },
                            ["empty_dict"] = new Dictionary<string, string>()
                        }
                    }
                },
                GlbPrimitives = new List<GlbPrimitive>
                {
                    new GlbPrimitive
                    {
                        GeomName = "mesh_0_ROOT__Profile25_Layer0",
                        MaterialIndex = 0,
                        Positions = new float[] { 0f, 0f, 0f, 1f, 0f, 0f, 0f, 1f, 0f },
                        Normals = new float[] { 0f, 0f, 1f, 0f, 0f, 1f, 0f, 0f, 1f },
                        Uvs = new float[] { 0f, 0f, 1f, 0f, 0f, 1f },
                        Indices = new uint[] { 0, 1, 2 }
                    }
                }
            };

            string ifcText = IfcExport.ToIfc(scene);
            Assert.Contains("'Pset_CustomProperties'", ifcText);
            Assert.Contains("'Pset_fbd-einfo'", ifcText);
            Assert.Contains("'width'", ifcText);
            Assert.Contains("'code'", ifcText);
            Assert.Contains("'aPf'", ifcText);
            Assert.Contains("'angle'", ifcText);
            Assert.Contains("'90'", ifcText);
            Assert.DoesNotContain("Pset_empty_dict", ifcText);
        }

        [Fact]
        public void ToIfc_LayerOnReflectsSceneLayerHidden()
        {
            var scene = new Scene
            {
                SceneHierarchy = new InstanceNode { Name = "Root", DefinitionName = "RootDef" },
                MeshIndex = new Dictionary<string, MeshMetadata>
                {
                    ["Outer Wall"] = new MeshMetadata { Name = "Outer Wall", Layer = "Hidden Layer" },
                    ["Front Door"] = new MeshMetadata { Name = "Front Door", Layer = "Visible Layer" }
                },
                GlbPrimitives = new List<GlbPrimitive>
                {
                    new GlbPrimitive
                    {
                        GeomName = "Outer Wall",
                        MaterialIndex = 0,
                        Positions = new float[] { 0f, 0f, 0f, 1f, 0f, 0f, 0f, 1f, 0f },
                        Normals = new float[] { 0f, 0f, 1f, 0f, 0f, 1f, 0f, 0f, 1f },
                        Uvs = new float[] { 0f, 0f, 1f, 0f, 0f, 1f },
                        Indices = new uint[] { 0, 1, 2 }
                    },
                    new GlbPrimitive
                    {
                        GeomName = "Front Door",
                        MaterialIndex = 0,
                        Positions = new float[] { 2f, 0f, 0f, 3f, 0f, 0f, 2f, 1f, 0f },
                        Normals = new float[] { 0f, 0f, 1f, 0f, 0f, 1f, 0f, 0f, 1f },
                        Uvs = new float[] { 0f, 0f, 1f, 0f, 0f, 1f },
                        Indices = new uint[] { 0, 1, 2 }
                    }
                },
                LayerHidden = new Dictionary<string, bool>
                {
                    ["Hidden Layer"] = true,
                    ["Visible Layer"] = false
                }
            };

            string ifcText = IfcExport.ToIfc(scene);
            Assert.Contains("IFCPRESENTATIONLAYERWITHSTYLE", ifcText);
            Assert.DoesNotContain("IFCPRESENTATIONLAYERASSIGNMENT(", ifcText);

            var lines = ifcText.Split(new[] { "\r\n", "\n" }, StringSplitOptions.None);
            string hiddenLine = lines.First(l => l.Contains("'Hidden Layer'"));
            string visibleLine = lines.First(l => l.Contains("'Visible Layer'"));
            Assert.Contains(",.F.,.F.,.F.,())", hiddenLine);
            Assert.Contains(",.T.,.F.,.F.,())", visibleLine);
        }

        [Fact]
        public void ClassifyElement_And_ToIfc_FullPathOnlyWhenOptedIn()
        {
            const string path = "ROOT / Wall Frame / Stud 12";
            Assert.Equal("IFCBUILDINGELEMENTPROXY", IfcExport.ClassifyElement("Stud 12", "Layer0", path).StepType);
            Assert.Equal("IFCWALL", IfcExport.ClassifyElement("Stud 12", "Layer0", path, classifyUsingFullPath: true).StepType);
            Assert.Equal(
                "IFCDOOR",
                IfcExport.ClassifyElement("Front Door", "Layer0", "ROOT / Wall Frame / Front Door", classifyUsingFullPath: true).StepType);

            var scene = new Scene
            {
                SceneHierarchy = new InstanceNode { Name = "Root", DefinitionName = "RootDef" },
                MeshIndex = new Dictionary<string, MeshMetadata>
                {
                    ["mesh_0_ROOT__Wall_Frame__Stud_12_Layer0"] = new MeshMetadata
                    {
                        Name = "Stud 12",
                        Layer = "Layer0",
                        Path = path
                    }
                },
                GlbPrimitives = new List<GlbPrimitive>
                {
                    new GlbPrimitive
                    {
                        GeomName = "mesh_0_ROOT__Wall_Frame__Stud_12_Layer0",
                        MaterialIndex = 0,
                        Positions = new float[] { 0f, 0f, 0f, 1f, 0f, 0f, 0f, 1f, 0f },
                        Normals = new float[] { 0f, 0f, 1f, 0f, 0f, 1f, 0f, 0f, 1f },
                        Uvs = new float[] { 0f, 0f, 1f, 0f, 0f, 1f },
                        Indices = new uint[] { 0, 1, 2 }
                    }
                }
            };

            string defaultText = IfcExport.ToIfc(scene);
            Assert.Contains("IFCBUILDINGELEMENTPROXY", defaultText);
            Assert.DoesNotContain("IFCWALL(", defaultText);

            string optedInText = IfcExport.ToIfc(scene, IfcExport.MetresToMm, "IFC4", null, classifyUsingFullPath: true);
            Assert.Contains("IFCWALL(", optedInText);
            Assert.Contains("'Stud 12'", optedInText);
        }

        [Fact]
        public void ToIfc_PreservesDisplayNamesHiddenLayersAndPluginDictsEndToEnd()
        {
            var builder = SkpCreate.NewFile();
            int hiddenWalls = builder.AddLayer("HiddenWalls", (200, 100, 100), hidden: true);
            var stud = builder.AddComponentDefinition("Stud 12");
            stud.AddFace(new (double, double, double)[] { (0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0) });
            stud.Dispose();
            var wallFrame = builder.AddComponentDefinition("Wall Frame");
            wallFrame.AddInstance(stud);
            wallFrame.Dispose();
            builder.AddInstance(wallFrame, layer: hiddenWalls);

            string tmpSkp = Path.Combine(Path.GetTempPath(), $"openskp_ifc_e2e_{Guid.NewGuid():N}.skp");
            File.WriteAllBytes(tmpSkp, builder.ToBytes());
            try
            {
                var scene = SkpFile.BuildScene(tmpSkp);
                Assert.True(scene.LayerHidden["HiddenWalls"]);
                string ifcText = IfcExport.ToIfc(scene, classifyUsingFullPath: true);
                Assert.Contains("IFCWALL(", ifcText);
                Assert.Contains("'Stud 12'", ifcText);
                Assert.DoesNotContain("mesh_0_ROOT", ifcText);
                string hiddenLine = ifcText.Split(new[] { "\r\n", "\n" }, StringSplitOptions.None)
                    .First(l => l.Contains("'HiddenWalls'"));
                Assert.Contains(",.F.,.F.,.F.,())", hiddenLine);
            }
            finally
            {
                File.Delete(tmpSkp);
            }

            var untitledScene = SkpFile.BuildScene(Path.Combine("fixtures", "Untitled.skp"));
            string untitledIfc = IfcExport.ToIfc(untitledScene);
            Assert.Contains("'Pset_steelframer-dict'", untitledIfc);
        }
    }
}
