using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>
    /// Real-file regression test for SketchUp 2020 (v20) classic .skp files.
    ///
    /// Fixture: fixtures/gondola_v20.skp - a retail gondola display authored
    /// in SketchUp 2020 (v20.1.235, ~755 KB), shared via the TypeScript
    /// port's PR #155.
    ///
    /// Before the v20 layout fixes, this file threw an "implausible
    /// definition count" from the legacy walk: v20 writes records the v17
    /// layout does not have, which left the reader a few bytes short and
    /// made it read garbage where a count was expected. The existing v17
    /// fixture (capilla_quiroz_v17.skp) has only one layer and never
    /// exercised any of these paths, so the divergence went unnoticed.
    ///
    /// Every count below was read off this exact file after the fix and
    /// sanity-checked for plausibility (bounding box in metres, definitions
    /// carrying real geometry, instances actually placed in the scene) - a
    /// parse that "succeeds" while silently dropping placements would still
    /// be a bug, so the instance counts matter as much as the parse not
    /// throwing.
    /// </summary>
    public class LegacyV20Tests
    {
        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        [Fact]
        public void ParsesRealV20FileThatPreviouslyThrew()
        {
            var model = SkpFile.Open(FixturePath("gondola_v20.skp"));

            Assert.Equal("{20.1.235}", model.Version);

            // Units - legacy files carry no meta/meta.dat, same as v17.
            Assert.Null(model.Units);

            Assert.Equal(20, model.Definitions.Count);
            Assert.Equal(24, model.Materials.Count);

            // v20 interleaves a null object-ref after EACH layer record;
            // the count is the number of REAL layers. The old reader
            // counted the separators as items and dropped every layer
            // after the first - this fixture really does carry "Gondulas
            // Laterais" (visible in SketchUp), which the previous
            // assertion enshrined as missing. Nulls must still never reach
            // model.Layers.
            var names = model.Layers.Select(l => l.Name).ToList();
            Assert.Equal(new[] { "Layer0", "Gondulas Laterais" }, names);

            // real geometry, not an empty shell
            int faces = model.Definitions.Values.Sum(d => d.Faces.Count);
            int edges = model.Definitions.Values.Sum(d => d.Edges.Count);
            int vertices = model.Definitions.Values.Sum(d => d.Vertices.Count);
            Assert.Equal(1887, faces);
            Assert.Equal(9174, edges);
            Assert.Equal(6543, vertices);
        }

        [Fact]
        public void PlacesEveryRootInstance()
        {
            var model = SkpFile.Open(FixturePath("gondola_v20.skp"));
            // 23 root-level placements: the definitions above are useless if
            // the instances that position them in the model are lost, which
            // is exactly what a subtly misaligned walk produces - a file
            // that parses into an almost-empty scene instead of throwing.
            Assert.Equal(23, model.Root.Instances.Count);

            var scene = SkpFile.BuildScene(FixturePath("gondola_v20.skp"));
            Assert.Equal(23, scene.SceneHierarchy.Children.Count);
            Assert.Equal(201, scene.GlbPrimitives.Count);
            Assert.Equal(201, scene.MeshIndex.Count);
            Assert.NotEmpty(scene.GltfMaterials);
        }

        [Fact]
        public void ResolvesPlacedInstancesToDefinitionsThatCarryGeometry()
        {
            // Guards the failure mode a zero entity count produces: the
            // definitions an instance points at come back empty, so the
            // file parses into a scene of correctly-positioned but invisible
            // groups. Counting definitions or instances alone does not
            // catch it - the two have to be checked together.
            var model = SkpFile.Open(FixturePath("gondola_v20.skp"));

            var referenced = new HashSet<long>();
            foreach (var inst in model.Root.Instances)
            {
                if (inst.RefIdx.HasValue) referenced.Add(inst.RefIdx.Value);
            }
            foreach (var def in model.Definitions.Values)
            {
                foreach (var inst in def.Instances)
                {
                    if (inst.RefIdx.HasValue) referenced.Add(inst.RefIdx.Value);
                }
            }

            var memo = new Dictionary<long, bool>();
            var inProgress = new HashSet<long>();
            bool CarriesGeometry(long defId)
            {
                if (memo.TryGetValue(defId, out var cached)) return cached;
                if (inProgress.Contains(defId)) return false; // reference cycle
                inProgress.Add(defId);
                bool result = model.Definitions.TryGetValue(defId, out var def)
                    && (def!.Faces.Count > 0
                        || def.Instances.Any(child => child.RefIdx.HasValue && CarriesGeometry(child.RefIdx.Value)));
                inProgress.Remove(defId);
                memo[defId] = result;
                return result;
            }

            var empty = referenced.Where(id => !CarriesGeometry(id)).ToList();
            Assert.Empty(empty);
        }

        [Fact]
        public void BakesGeometryAtAPlausibleRealWorldScale()
        {
            var scene = SkpFile.BuildScene(FixturePath("gondola_v20.skp"));
            var min = new float[] { float.PositiveInfinity, float.PositiveInfinity, float.PositiveInfinity };
            var max = new float[] { float.NegativeInfinity, float.NegativeInfinity, float.NegativeInfinity };
            foreach (var prim in scene.GlbPrimitives)
            {
                var pos = prim.Positions;
                for (int i = 0; i < pos.Length; i += 3)
                {
                    for (int a = 0; a < 3; a++)
                    {
                        float v = pos[i + a];
                        if (v < min[a]) min[a] = v;
                        if (v > max[a]) max[a] = v;
                    }
                }
            }
            // a shop gondola display: metres, not the 1e3-off or degenerate
            // box a misaligned read produces
            Assert.True(Math.Abs((max[0] - min[0]) - 3.82) < 0.1);
            Assert.True(Math.Abs((max[1] - min[1]) - 3.14) < 0.1);
            Assert.True(Math.Abs((max[2] - min[2]) - 4.82) < 0.1);
        }

        [Fact]
        public void GivesEveryBakedPrimitiveValidUvCoordinates()
        {
            var scene = SkpFile.BuildScene(FixturePath("gondola_v20.skp"));
            Assert.NotEmpty(scene.GlbPrimitives);
            foreach (var prim in scene.GlbPrimitives)
            {
                int nVerts = prim.Positions.Length / 3;
                Assert.Equal(nVerts * 2, prim.Uvs.Length);
                foreach (var uv in prim.Uvs)
                {
                    Assert.True(!float.IsNaN(uv) && !float.IsInfinity(uv));
                }
            }
        }
    }
}
