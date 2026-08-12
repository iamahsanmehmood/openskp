using System.Collections.Generic;
using OpenSkp;
using Xunit;

namespace OpenSkp.Tests
{
    public class StlExportTests
    {
        [Fact]
        public void ToStlAscii_SerializesSceneToStlText()
        {
            var scene = new Scene
            {
                SceneHierarchy = new InstanceNode { Name = "Root", DefinitionName = "RootDef" },
                GlbPrimitives = new List<GlbPrimitive>
                {
                    new GlbPrimitive
                    {
                        GeomName = "Box",
                        MaterialIndex = 0,
                        Positions = new float[] { 0f, 0f, 0f, 1f, 0f, 0f, 0f, 1f, 0f },
                        Normals = new float[] { 0f, 0f, 1f, 0f, 0f, 1f, 0f, 0f, 1f },
                        Uvs = new float[] { 0f, 0f, 1f, 0f, 0f, 1f },
                        Indices = new uint[] { 0, 1, 2 }
                    }
                }
            };

            string stlText = StlExport.ToStlAscii(scene);
            Assert.Contains("solid OpenSKP_Model", stlText);
            Assert.Contains("facet normal 0.000000 0.000000 1.000000", stlText);
            Assert.Contains("vertex 0.000000 0.000000 0.000000", stlText);
            Assert.Contains("endsolid OpenSKP_Model", stlText);
        }

        [Fact]
        public void ToStlBinary_SerializesSceneToBinaryStlData()
        {
            var scene = new Scene
            {
                SceneHierarchy = new InstanceNode { Name = "Root", DefinitionName = "RootDef" },
                GlbPrimitives = new List<GlbPrimitive>
                {
                    new GlbPrimitive
                    {
                        GeomName = "Box",
                        MaterialIndex = 0,
                        Positions = new float[] { 0f, 0f, 0f, 1f, 0f, 0f, 0f, 1f, 0f },
                        Normals = new float[] { 0f, 0f, 1f, 0f, 0f, 1f, 0f, 0f, 1f },
                        Uvs = new float[] { 0f, 0f, 1f, 0f, 0f, 1f },
                        Indices = new uint[] { 0, 1, 2 }
                    }
                }
            };

            byte[] data = StlExport.ToStlBinary(scene);
            Assert.Equal(80 + 4 + 50, data.Length);
        }
    }
}
