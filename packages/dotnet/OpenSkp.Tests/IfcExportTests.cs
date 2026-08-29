using System.Collections.Generic;
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
    }
}
