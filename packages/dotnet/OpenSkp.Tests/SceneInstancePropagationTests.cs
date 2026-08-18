using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>
    /// Regression tests for the deferred instance -> mesh (Properties, Name)
    /// back-fill in SceneBuilder.Build.
    ///
    /// The old implementation re-scanned the entire meshIndex with a string
    /// Contains per placed instance, i.e. O(instances x meshes). On real
    /// production files with a few hundred thousand instances and meshes
    /// that single loop took tens of minutes (measured: 323,856 instances x
    /// 321,683 meshes -> ~73 minutes in BuildScene, ~98% of the total
    /// conversion time). The replacement records one dictionary entry per
    /// instance during instantiation and applies updates once afterwards by
    /// walking each mesh's Path to its shallowest recorded ancestor.
    ///
    /// These tests build synthetic RawParsed models directly (internals are
    /// visible to the test assembly) so the semantics and the linear-time
    /// behaviour are pinned without needing a large .skp fixture.
    /// </summary>
    public class SceneInstancePropagationTests
    {
        private static GeometryBuilder TriangleBuilder(long vidBase)
        {
            var b = new GeometryBuilder();
            b.Vertices[vidBase + 0] = (0.0, 0.0, 0.0);
            b.Vertices[vidBase + 1] = (1.0, 0.0, 0.0);
            b.Vertices[vidBase + 2] = (0.0, 1.0, 0.0);
            b.Edges[vidBase + 0] = (vidBase + 0, vidBase + 1);
            b.Edges[vidBase + 1] = (vidBase + 1, vidBase + 2);
            b.Edges[vidBase + 2] = (vidBase + 2, vidBase + 0);
            b.Faces[vidBase] = new GeometryBuilderFace
            {
                Loops = new List<List<(long EdgeId, long Orientation)>>
                {
                    new List<(long, long)> { (vidBase + 0, 1), (vidBase + 1, 1), (vidBase + 2, 1) },
                },
                Normal = (0.0, 0.0, 1.0),
            };
            return b;
        }

        private static Core.RawParsed Parsed(params (long Id, GeometryBuilder Builder, string? Name)[] defs)
        {
            var parsed = new Core.RawParsed();
            foreach (var (id, builder, name) in defs)
            {
                parsed.DefsDict[id] = new Geometry.RawDefinition { Guid = "G" + id, Name = name, Builder = builder };
            }
            // Layer0 color is needed as the fallback face color.
            parsed.LayerColors["Layer0"] = (136, 136, 136);
            parsed.LayerIdToName[1] = "Layer0";
            return parsed;
        }

        [Fact]
        public void MeshBackfill_OutermostAncestorWins()
        {
            // ROOT (one direct face)
            //  |- A (def1: one face + child instance C -> def3, dynamic props)
            //  |    "- C (def3: one face, its own props)
            //  "- B (def2: one face, no props)
            var root = TriangleBuilder(100);
            var def1 = TriangleBuilder(200);
            var def2 = TriangleBuilder(300);
            var def3 = TriangleBuilder(400);

            def1.Instances.Add(new GeometryBuilderInstance
            {
                Name = "C",
                RefIdx = 3,
                Properties = new Dictionary<string, string> { ["height"] = "5" },
            });

            root.Instances.Add(new GeometryBuilderInstance
            {
                Name = "A",
                RefIdx = 1,
                Properties = new Dictionary<string, string> { ["width"] = "10" },
            });
            root.Instances.Add(new GeometryBuilderInstance { Name = "B", RefIdx = 2 });

            var parsed = Parsed(
                (1, def1, "def1"),
                (2, def2, "def2"),
                (3, def3, "def3"));
            parsed.Root = new Geometry.RawDefinition { Guid = "ROOT", Name = "ROOT_MODEL", Builder = root };

            var scene = SceneBuilder.Build(parsed);

            // ROOT's own mesh: untouched by instance updates, fixed up to ROOT.
            var rootMesh = Assert.Single(scene.MeshIndex.Values, m => m.Path == "ROOT");
            Assert.Equal("ROOT", rootMesh.Name);
            Assert.Empty(rootMesh.Properties);

            // Mesh directly under instance A: gets A's properties/name.
            var meshA = Assert.Single(scene.MeshIndex.Values, m => m.Path == "ROOT / A");
            Assert.Equal("A", meshA.Name);
            Assert.Equal("10", meshA.Properties["width"]);

            // Mesh under A's child C: the OUTERMOST ancestor (A) wins, exactly
            // like the old scan (A wrote last, after C).
            var meshAC = Assert.Single(scene.MeshIndex.Values, m => m.Path == "ROOT / A / C");
            Assert.Equal("A", meshAC.Name);
            Assert.Equal("10", meshAC.Properties["width"]);
            Assert.False(meshAC.Properties.ContainsKey("height"));

            // Mesh under B: B has no dynamic props; still records (empty, "B").
            var meshB = Assert.Single(scene.MeshIndex.Values, m => m.Path == "ROOT / B");
            Assert.Equal("B", meshB.Name);
            Assert.Empty(meshB.Properties);
        }

        [Fact]
        public void MeshBackfill_LargeInstanceCount_IsLinear()
        {
            // 100k placed instances of a one-triangle definition. The old
            // per-instance full-meshIndex scan is O(N^2): ~100k^2/2 string
            // Contains calls (~55+ s measured locally). The deferred back-fill
            // is O(N): this should finish in a couple of seconds, so a 30 s
            // ceiling separates the two regimes with a wide margin and stays
            // far from timing-flake territory for the fixed code.
            const int count = 100_000;
            var def = TriangleBuilder(1);
            var root = new GeometryBuilder();
            for (int i = 0; i < count; i++)
            {
                root.Instances.Add(new GeometryBuilderInstance { Name = "inst_" + i, RefIdx = 1 });
            }
            var parsed = Parsed((1, def, "def"));
            parsed.Root = new Geometry.RawDefinition { Guid = "ROOT", Name = "ROOT_MODEL", Builder = root };

            var sw = Stopwatch.StartNew();
            var scene = SceneBuilder.Build(parsed);
            sw.Stop();

            Assert.Equal(count, scene.MeshIndex.Count);
            Assert.Equal(count, scene.GlbPrimitives.Count);
            Assert.True(
                sw.Elapsed < TimeSpan.FromSeconds(30),
                "BuildScene took " + sw.Elapsed.TotalSeconds.ToString("F1") + "s for " + count + " instances - expected linear (deferred back-fill), not O(n^2).");
        }
    }
}
