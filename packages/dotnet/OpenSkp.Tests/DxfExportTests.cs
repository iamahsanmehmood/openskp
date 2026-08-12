using System.Collections.Generic;
using OpenSkp;
using Xunit;

namespace OpenSkp.Tests
{
    public class DxfExportTests
    {
        [Fact]
        public void ToDxf_SerializesSceneTo3DxfText()
        {
            var scene = new Scene
            {
                SceneHierarchy = new InstanceNode { Name = "Root", DefinitionName = "RootDef" },
                GlbPrimitives = new List<GlbPrimitive>
                {
                    new GlbPrimitive
                    {
                        GeomName = "Walls",
                        MaterialIndex = 0,
                        Positions = new float[] { 0f, 0f, 0f, 1f, 0f, 0f, 0f, 1f, 0f },
                        Normals = new float[] { 0f, 0f, 1f, 0f, 0f, 1f, 0f, 0f, 1f },
                        Uvs = new float[] { 0f, 0f, 1f, 0f, 0f, 1f },
                        Indices = new uint[] { 0, 1, 2 }
                    }
                }
            };

            string dxfText = DxfExport.ToDxf(scene);
            Assert.Contains("$ACADVER", dxfText);
            Assert.Contains("AC1015", dxfText);
            Assert.Contains("POLYLINE", dxfText);
            Assert.Contains("AcDbPolyFaceMesh", dxfText);
            Assert.Contains("Walls", dxfText);
            Assert.Contains("EOF", dxfText);

            string dxf3d = DxfExport.ToDxf(scene, mode: "3dface");
            Assert.Contains("3DFACE", dxf3d);
            Assert.Contains("AcDbFace", dxf3d);
        }
    }
}
