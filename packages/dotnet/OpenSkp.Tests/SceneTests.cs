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
    /// Root instance count is cross-validated directly against Python's
    /// and TypeScript's SkpFile.build_scene()/buildScene() on this exact
    /// fixture. Mesh/GltfMaterials counts (21/21/13) instead match C++'s
    /// independently-verified reference for this file - the correct
    /// counts once faces with genuinely different front/back materials
    /// are split into two single-sided primitives each, rather than the
    /// pre-fix single-sided-only count (13/13/9). This fixture has 30
    /// such faces (confirmed by direct inspection), so the split isn't a
    /// rare edge case here.
    /// </summary>
    public class SceneTests
    {
        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        [Fact]
        public void BuildSceneMatchesPythonAndTypeScriptGroundTruth()
        {
            var scene = SkpFile.BuildScene(FixturePath("capilla_quiroz_v17.skp"));

            Assert.Equal(21, scene.GlbPrimitives.Count);
            Assert.Equal(21, scene.MeshIndex.Count);
            Assert.Equal(13, scene.GltfMaterials.Count);

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
                Assert.Equal(nVerts * 2, prim.Uvs.Length);
                Assert.All(prim.Uvs, uv => Assert.False(float.IsNaN(uv) || float.IsInfinity(uv)));
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
            Assert.Equal(21, scene.GlbPrimitives.Count);
        }

        [Fact]
        public void RendersBackFaceMaterialsCorrectly()
        {
            // This fixture has 30 faces (e.g. faces 133/152 in the
            // 'puerta' definition) whose front and back materials resolve
            // to genuinely different colors. Verified directly: front
            // material 29 is blue (2, 0, 237), back material 27 is light
            // blue (204, 235, 244).
            var scene = SkpFile.BuildScene(FixturePath("capilla_quiroz_v17.skp"));

            bool HasColor(int r, int g, int b) => scene.GltfMaterials.Any(m =>
            {
                var dict = (System.Collections.Generic.IDictionary<string, object>)m;
                var pbr = (System.Collections.Generic.IDictionary<string, object>)dict["pbrMetallicRoughness"];
                double[] c = (double[])pbr["baseColorFactor"];
                return (int)Math.Round(c[0] * 255) == r &&
                    (int)Math.Round(c[1] * 255) == g &&
                    (int)Math.Round(c[2] * 255) == b;
            });

            Assert.True(HasColor(2, 0, 237));
            Assert.True(HasColor(204, 235, 244));

            int doubleSidedCount = scene.GltfMaterials.Count(m =>
                ((System.Collections.Generic.IDictionary<string, object>)m).TryGetValue("doubleSided", out var v) && v is bool b && b);
            Assert.Equal(4, doubleSidedCount);
        }
    }
}
