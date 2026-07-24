using System;
using System.IO;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>
    /// Real-file regression test for SkpFile.BuildScene() - the opt-in
    /// scene-hierarchy + triangulation + GLB mesh capability, ported from
    /// the TypeScript reference implementation.
    ///
    /// Cross-validated directly against Python's and TypeScript's
    /// SkpFile.build_scene()/buildScene() on this exact fixture: mesh
    /// count, mesh_index count, gltf_materials count, root instance count,
    /// and the first three meshes' exact vertex/triangle counts and
    /// material indices all match precisely.
    /// </summary>
    public class SceneTests
    {
        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        [Fact]
        public void BuildSceneMatchesPythonAndTypeScriptGroundTruth()
        {
            var scene = SkpFile.BuildScene(FixturePath("capilla_quiroz_v17.skp"));

            Assert.Equal(13, scene.GlbPrimitives.Count);
            Assert.Equal(13, scene.MeshIndex.Count);
            Assert.Equal(9, scene.GltfMaterials.Count);

            Assert.Equal("ROOT", scene.SceneHierarchy.Name);
            Assert.Equal("ROOT_MODEL", scene.SceneHierarchy.DefinitionName);
            Assert.Equal(3, scene.SceneHierarchy.Children.Count);
            var defNames = scene.SceneHierarchy.Children.Select(c => c.DefinitionName).OrderBy(x => x).ToList();
            Assert.Equal(new[] { "grada", "grada", "puerta" }, defNames);
        }

        [Fact]
        public void PrimitivesHaveValidGeometry()
        {
            var scene = SkpFile.BuildScene(FixturePath("capilla_quiroz_v17.skp"));
            foreach (var prim in scene.GlbPrimitives)
            {
                Assert.Equal(0, prim.Positions.Length % 3);
                Assert.Equal(prim.Positions.Length, prim.Normals.Length);
                Assert.Equal(0, prim.Indices.Length % 3);
                int nVerts = prim.Positions.Length / 3;
                Assert.All(prim.Indices, idx => Assert.InRange(idx, 0u, (uint)nVerts - 1));
                Assert.InRange(prim.MaterialIndex, 0, scene.GltfMaterials.Count - 1);
            }
        }

        [Fact]
        public void BuildSceneIsIndependentOfParse()
        {
            // BuildScene() must not require Parse()/Open() to have been
            // called first - it re-parses independently.
            var scene = SkpFile.BuildScene(FixturePath("capilla_quiroz_v17.skp"));
            Assert.Equal(13, scene.GlbPrimitives.Count);
        }
    }
}
