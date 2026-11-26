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
        public void ToIfc_DeclaresMillimetresAndConvertsGltfYUpToIfcZUp()
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

            string ifcText = IfcExport.ToIfc(scene);
            Assert.Contains("IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.)", ifcText);
            Assert.Contains("(2000.000000,-5000.000000,3000.000000)", ifcText);
            Assert.DoesNotContain("(2000.000000,3000.000000,5000.000000)", ifcText);
        }

        [Fact]
        public void ToIfc_UsesRealInstanceNameAndPassesRawTextToCustomClassifier()
        {
            var scene = new Scene
            {
                SceneHierarchy = new InstanceNode { Name = "Root", DefinitionName = "RootDef" },
                MeshIndex = new Dictionary<string, MeshMetadata>
                {
                    ["mesh_3_ROOT__W1_Layer0"] = new MeshMetadata
                    {
                        Name = "Wände 'A'",
                        Layer = "Außenwände '1'",
                        Properties = new Dictionary<string, string> { ["width"] = "10.0" },
                        AttributeDictionaries = new Dictionary<string, Dictionary<string, string>>
                        {
                            ["fbd-einfo"] = new Dictionary<string, string> { ["code"] = "aPf" },
                            ["empty_dict"] = new Dictionary<string, string>()
                        }
                    }
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
                },
                LayerHidden = new Dictionary<string, bool> { ["Außenwände '1'"] = true }
            };

            var seen = new List<(string Name, string Layer)>();
            string ifcText = IfcExport.ToIfc(scene, classifier: (name, layer) =>
            {
                seen.Add((name, layer));
                return ("IFCWALL", "IfcWall");
            });

            Assert.Equal(new[] { ("Wände 'A'", "Außenwände '1'") }, seen);
            Assert.Contains("''A''", ifcText);
            Assert.DoesNotContain("mesh_3_ROOT", ifcText);
            Assert.Contains("'Pset_CustomProperties'", ifcText);
            Assert.Contains("'Pset_fbd-einfo'", ifcText);
            Assert.DoesNotContain("Pset_empty_dict", ifcText);
            Assert.Contains(",.F.,.F.,.F.,());", ifcText);
        }

        [Fact]
        public void ToIfc_PreservesPaintedMaterialColorsDisplayNamesAndHiddenLayersEndToEnd()
        {
            const string path = "ROOT / Wall Frame / Stud 12";
            Assert.Equal("IFCBUILDINGELEMENTPROXY", IfcExport.ClassifyElement("Stud 12", "Layer0", path).StepType);
            Assert.Equal("IFCWALL", IfcExport.ClassifyElement("Stud 12", "Layer0", path, classifyUsingFullPath: true).StepType);

            var builder = SkpCreate.NewFile();
            int redMat = builder.AddMaterial("Crimson", (255, 51, 0));
            int hiddenWalls = builder.AddLayer("HiddenWalls", (200, 100, 100), hidden: true);
            ComponentDefinitionBuilder stud;
            using (stud = builder.AddComponentDefinition("Stud 12"))
            {
                stud.AddFace(
                    new (double, double, double)[] { (0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0) },
                    material: redMat);
            }
            ComponentDefinitionBuilder wallFrame;
            using (wallFrame = builder.AddComponentDefinition("Wall Frame"))
            {
                wallFrame.AddInstance(stud);
            }
            builder.AddInstance(wallFrame, layer: hiddenWalls);

            var scene = SkpFile.BuildScene(builder.ToBytes());
            Assert.True(scene.LayerHidden["HiddenWalls"]);
            Assert.False(scene.LayerHidden["Layer0"]);

            string ifcText = IfcExport.ToIfc(scene, classifyUsingFullPath: true);
            Assert.Contains("IFCWALL(", ifcText);
            Assert.Contains("'Stud 12'", ifcText);
            Assert.DoesNotContain("mesh_0_ROOT", ifcText);
            // Scene.cs stores baseColorFactor as double[]; verify non-grey IFCCOLOURRGB
            Assert.Contains("IFCCOLOURRGB($,1.0000,0.2000,0.0000);", ifcText);
            Assert.DoesNotContain("IFCCOLOURRGB($,0.8000,0.8000,0.8000);", ifcText);

            var untitledScene = SkpFile.BuildScene(Path.Combine(AppContext.BaseDirectory, "fixtures", "Untitled.skp"));
            string untitledIfc = IfcExport.ToIfc(untitledScene);
            Assert.Contains("'Pset_steelframer-dict'", untitledIfc);
        }

        [Fact]
        public void ToIfc_WritesHiddenPlacementTagAsHiddenEvenWhenDefinitionFacesAreWood()
        {
            var b = SkpCreate.NewFile();
            int wood = b.AddLayer("Wood", (160, 110, 60));
            int furnitureHidden = b.AddLayer("FurnitureHidden", (40, 40, 40), hidden: true);
            ComponentDefinitionBuilder woodPanel;
            using (woodPanel = b.AddComponentDefinition("WoodPanel"))
            {
                woodPanel.AddFace(
                    new (double, double, double)[] { (0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0) },
                    layer: wood);
            }
            b.AddInstance(woodPanel, name: "HiddenFurniture", layer: furnitureHidden);

            string ifcText = IfcExport.ToIfc(SkpFile.BuildScene(b.ToBytes()));
            var layerLines = ifcText.Split(new[] { "\r\n", "\n" }, StringSplitOptions.None)
                .Where(l => l.Contains("IFCPRESENTATIONLAYERWITHSTYLE("))
                .ToArray();
            var hiddenLines = layerLines.Where(l => l.Contains("'FurnitureHidden'")).ToArray();
            Assert.Single(hiddenLines);
            Assert.Contains(",.F.,", hiddenLines[0]);
            Assert.DoesNotContain(layerLines, l => l.Contains("'Wood'"));
        }

        [Fact]
        public void ToIfc_Ifc2x3SchemaEmitsBrepSurfaceModelTrimmedCurvesAndProxyCompositionType()
        {
            double s45 = Math.Sqrt(0.5);
            var scene = new Scene
            {
                SceneHierarchy = new InstanceNode { Name = "Root", DefinitionName = "RootDef" },
                MeshIndex = new Dictionary<string, MeshMetadata>
                {
                    ["ClosedCrate"] = new MeshMetadata { Name = "ClosedCrate", Path = "Root/ClosedCrate" },
                    ["OpenRoof"] = new MeshMetadata { Name = "Gable Roof", Path = "Root/OpenRoof" },
                    ["FrontDoor"] = new MeshMetadata { Name = "Front Door", Path = "Root/FrontDoor" },
                },
                GlbPrimitives = new List<GlbPrimitive>
                {
                    new GlbPrimitive
                    {
                        GeomName = "ClosedCrate",
                        MaterialIndex = 0,
                        Positions = new float[] { 0f, 0f, 0f, 1f, 0f, 0f, 0f, 1f, 0f, 0f, 0f, 1f },
                        Normals = new float[12],
                        Uvs = new float[8],
                        Indices = new uint[] { 0, 2, 1, 0, 1, 3, 1, 2, 3, 2, 0, 3 },
                    },
                    new GlbPrimitive
                    {
                        GeomName = "OpenRoof",
                        MaterialIndex = 0,
                        Positions = new float[] { 0f, 0f, 0f, 1f, 0f, 0f, 0f, 1f, 0f },
                        Normals = new float[9],
                        Uvs = new float[6],
                        Indices = new uint[] { 0, 1, 2 },
                    },
                    new GlbPrimitive
                    {
                        GeomName = "FrontDoor",
                        MaterialIndex = 0,
                        Positions = new float[] { 2f, 0f, 0f, 3f, 0f, 0f, 2f, 1f, 0f },
                        Normals = new float[9],
                        Uvs = new float[6],
                        Indices = new uint[] { 0, 1, 2 },
                    },
                },
                CurveSets = new List<CurveSetMetadata>
                {
                    new CurveSetMetadata
                    {
                        Name = "QuarterArc",
                        PointsM = new List<(double X, double Y, double Z)>
                        {
                            (1.0, 0.0, 0.0), (s45, 0.0, -s45), (0.0, 0.0, -1.0)
                        },
                        Closed = false,
                        Arc = new CurveArc
                        {
                            Points = new List<(double X, double Y, double Z)>
                            {
                                (1.0, 0.0, 0.0), (s45, 0.0, -s45), (0.0, 0.0, -1.0)
                            },
                            Segments = new List<(int Start, int Mid, int End)> { (0, 1, 2) },
                        },
                    },
                    new CurveSetMetadata
                    {
                        Name = "FullCircle",
                        PointsM = new List<(double X, double Y, double Z)>
                        {
                            (1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)
                        },
                        Closed = true,
                        Arc = new CurveArc
                        {
                            Points = new List<(double X, double Y, double Z)>
                            {
                                (1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)
                            },
                            Segments = new List<(int Start, int Mid, int End)> { (0, 1, 2), (2, 3, 0) },
                        },
                    },
                    new CurveSetMetadata
                    {
                        Name = "DegenerateArc",
                        PointsM = new List<(double X, double Y, double Z)>
                        {
                            (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)
                        },
                        Closed = false,
                        Arc = new CurveArc
                        {
                            Points = new List<(double X, double Y, double Z)>
                            {
                                (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)
                            },
                            Segments = new List<(int Start, int Mid, int End)> { (0, 1, 2) },
                        },
                    },
                },
            };

            string ifcText = IfcExport.ToIfc(scene, IfcExport.MetresToMm, "IFC2X3");
            Assert.Contains("FILE_SCHEMA(('IFC2X3'));", ifcText);
            Assert.DoesNotContain("IFCTRIANGULATEDFACESET(", ifcText);
            Assert.DoesNotContain("IFCCARTESIANPOINTLIST3D(", ifcText);
            Assert.DoesNotContain("IFCINDEXEDPOLYCURVE(", ifcText);
            Assert.DoesNotContain("IFCARCINDEX(", ifcText);

            Assert.Contains("IFCCLOSEDSHELL(", ifcText);
            Assert.Contains("IFCFACETEDBREP(", ifcText);
            Assert.Contains("'Body','Brep',", ifcText);
            Assert.Contains("IFCCONNECTEDFACESET(", ifcText);
            Assert.Contains("IFCFACEBASEDSURFACEMODEL(", ifcText);
            Assert.Contains("'Body','SurfaceModel',", ifcText);

            string[] lines = ifcText.Split(new[] { "\r\n", "\n" }, StringSplitOptions.None);
            Assert.Equal(2, lines.Count(l => l.Contains("=IFCCIRCLE(")));
            Assert.Equal(1, lines.Count(l => l.Contains("=IFCTRIMMEDCURVE(")));
            Assert.Contains("IFCPARAMETERVALUE(1.570796)", ifcText);
            Assert.Equal(1, lines.Count(l => l.Contains("=IFCPOLYLINE(")));

            string proxyLine = lines.Single(l => l.Contains("=IFCBUILDINGELEMENTPROXY("));
            Assert.EndsWith(",.ELEMENT.);", proxyLine);
            string roofLine = lines.Single(l => l.Contains("=IFCROOF("));
            Assert.EndsWith(",.NOTDEFINED.);", roofLine);
            string doorLine = lines.Single(l => l.Contains("=IFCDOOR("));
            string doorBody = doorLine.Substring(doorLine.IndexOf('(') + 1, doorLine.LastIndexOf(')') - doorLine.IndexOf('(') - 1);
            Assert.Equal(10, doorBody.Split(',').Length);

            int[] ids = lines
                .Where(l => l.StartsWith("#", StringComparison.Ordinal) && l.Contains("="))
                .Select(l => int.Parse(l.Substring(1, l.IndexOf('=') - 1)))
                .ToArray();
            Assert.Equal(Enumerable.Range(1, ids.Length).ToArray(), ids);
        }
    }
}
