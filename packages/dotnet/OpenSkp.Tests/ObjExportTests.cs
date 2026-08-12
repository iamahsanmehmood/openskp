using System.Collections.Generic;
using OpenSkp;
using Xunit;

namespace OpenSkp.Tests
{
    public class ObjExportTests
    {
        [Fact]
        public void ToObj_SerializesSceneToObjText()
        {
            var scene = new Scene
            {
                SceneHierarchy = new InstanceNode { Name = "Root", DefinitionName = "RootDef" },
                GlbPrimitives = new List<GlbPrimitive>
                {
                    new GlbPrimitive
                    {
                        GeomName = "Cube",
                        MaterialIndex = 0,
                        Positions = new float[] { 0f, 0f, 0f, 1f, 0f, 0f, 0f, 1f, 0f },
                        Normals = new float[] { 0f, 0f, 1f, 0f, 0f, 1f, 0f, 0f, 1f },
                        Uvs = new float[] { 0f, 0f, 1f, 0f, 0f, 1f },
                        Indices = new uint[] { 0, 1, 2 }
                    }
                }
            };

            string objText = ObjExport.ToObj(scene);
            Assert.Contains("# OpenSKP OBJ Export", objText);
            Assert.Contains("o Cube", objText);
            Assert.Contains("v 0.000000 0.000000 0.000000", objText);
            Assert.Contains("f 1 2 3", objText);
        }
    }
}
