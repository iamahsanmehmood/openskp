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

        private static System.Collections.Generic.IDictionary<string, object> Pbr(object mat) =>
            (System.Collections.Generic.IDictionary<string, object>)
                ((System.Collections.Generic.IDictionary<string, object>)mat)["pbrMetallicRoughness"];

        [Fact]
        public void TranslucentMaterialGetsBlendAlpha()
        {
            // Round-tripped through the real writer and reader rather than
            // a hand-built fixture: AddMaterial's 4th (alpha) channel is
            // documented to carry SketchUp's own opacity mechanism, so
            // this exercises the exact path a real .skp file with a
            // translucent material takes. Before this fix, the legacy
            // binary reader dropped the color record's alpha byte entirely
            // (Legacy.cs hardcoded it to 255 downstream in Parser.cs), and
            // even where transparency WAS correctly parsed,
            // baseColorFactor's alpha was hardcoded to 1.0 with no
            // material ever declaring alphaMode - so a conformant renderer
            // showed every material fully opaque regardless of the source
            // file's actual transparency.
            var builder = SkpCreate.NewFile();
            int glass = builder.AddMaterial("Glass", (40, 70, 100, 128));
            builder.AddFace(new (double, double, double)[] { (0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0) }, material: glass);
            byte[] bytes = builder.ToBytes();

            string path = Path.Combine(Path.GetTempPath(), $"openskp_dotnet_glass_{Guid.NewGuid():N}.skp");
            File.WriteAllBytes(path, bytes);
            try
            {
                var scene = SkpFile.BuildScene(path);
                var mat = scene.GltfMaterials
                    .Select(m => (Mat: m, Pbr: Pbr(m)))
                    .First(x => (double)((double[])x.Pbr["baseColorFactor"])[3] != 1.0);
                double alpha = (double)((double[])mat.Pbr["baseColorFactor"])[3];
                Assert.InRange(alpha, 128 / 255.0 - 0.01, 128 / 255.0 + 0.01);
                Assert.Equal("BLEND", ((System.Collections.Generic.IDictionary<string, object>)mat.Mat)["alphaMode"]);
            }
            finally
            {
                File.Delete(path);
            }
        }

        [Fact]
        public void OpaqueMaterialStaysByteForByteUnchanged()
        {
            var builder = SkpCreate.NewFile();
            int red = builder.AddMaterial("Red", (255, 0, 0));
            builder.AddFace(new (double, double, double)[] { (0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0) }, material: red);
            byte[] bytes = builder.ToBytes();

            string path = Path.Combine(Path.GetTempPath(), $"openskp_dotnet_red_{Guid.NewGuid():N}.skp");
            File.WriteAllBytes(path, bytes);
            try
            {
                var scene = SkpFile.BuildScene(path);
                var mat = scene.GltfMaterials[0];
                var pbr = Pbr(mat);
                Assert.Equal(1.0, (double)((double[])pbr["baseColorFactor"])[3]);
                Assert.False(((System.Collections.Generic.IDictionary<string, object>)mat).ContainsKey("alphaMode"));
            }
            finally
            {
                File.Delete(path);
            }
        }

        [Fact]
        public void TexturedMaterialsGetMaskOrBlendNeverLeftOpaque()
        {
            // capilla_quiroz_v17.skp has four textured materials: two
            // ordinary opaque ones (MASK - a safe no-op, nothing in their
            // JPEGs to cut out) and two genuinely translucent
            // stained-glass-style materials at alpha 0.5 (BLEND, so that
            // opacity actually renders instead of being silently dropped
            // under glTF's OPAQUE default).
            var scene = SkpFile.BuildScene(FixturePath("capilla_quiroz_v17.skp"));
            var textured = scene.GltfMaterials
                .Select(m => (Mat: (System.Collections.Generic.IDictionary<string, object>)m, Pbr: Pbr(m)))
                .Where(x => x.Pbr.ContainsKey("baseColorTexture"))
                .ToList();
            Assert.Equal(4, textured.Count);

            var translucent = textured.Where(x => (string)x.Mat["alphaMode"] == "BLEND").ToList();
            var opaque = textured.Where(x => (string)x.Mat["alphaMode"] == "MASK").ToList();
            Assert.Equal(2, translucent.Count);
            Assert.Equal(2, opaque.Count);
            foreach (var x in translucent)
            {
                Assert.True((double)((double[])x.Pbr["baseColorFactor"])[3] < 1.0);
            }
            foreach (var x in opaque)
            {
                Assert.Equal(1.0, (double)((double[])x.Pbr["baseColorFactor"])[3]);
            }
        }
    }
}
