using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Xunit;

namespace OpenSkp.Tests
{
    public class LooseEdgeCurvesTests
    {
        private const double RadiusIn = 40.0;
        private const double InchesToM = 0.0254;
        private static readonly double[] Identity = { 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1 };

        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        private static ArcFrame MakeFrame(double radius = RadiusIn, double sweep = Math.PI / 2) =>
            new ArcFrame
            {
                Center = (0, 0, 0),
                Normal = (0, 0, 1),
                XAxis = (radius, 0, 0),
                YAxis = (0, radius, 0),
                StartAngle = 0,
                EndAngle = sweep,
            };

        private static (double X, double Y, double Z) ArcPoint(double t, double radius = RadiusIn) =>
            (radius * Math.Cos(t) * InchesToM, 0.0, -radius * Math.Sin(t) * InchesToM);

        [Fact]
        public void ChainLooseEdges_RespectsCurvesAndExcludesFaceEdges()
        {
            var b1 = new GeometryBuilder();
            b1.Edges[1] = (10, 20);
            b1.Edges[2] = (20, 30);
            b1.EdgeCurves[1] = 100;
            b1.EdgeCurves[2] = 101;
            Assert.Equal(new[] { 2, 2 }, Curves.ChainLooseEdges(b1).Select(r => r.VertexIds.Count).OrderBy(n => n));

            var openB = new GeometryBuilder();
            openB.Edges[1] = (10, 20);
            openB.Edges[2] = (20, 30);
            openB.Edges[3] = (30, 40);
            openB.EdgeCurves[2] = 50;
            openB.EdgeCurves[3] = 50;
            openB.Faces[9] = new GeometryBuilderFace
            {
                Loops = new List<List<(long EdgeId, long Orientation)>>
                {
                    new List<(long EdgeId, long Orientation)> { (1, 1) },
                },
            };
            var openRuns = Curves.ChainLooseEdges(openB);
            Assert.Single(openRuns);
            Assert.Equal(new long[] { 2, 3 }, openRuns[0].EdgeIds);
            Assert.Equal(new long[] { 20, 30, 40 }, openRuns[0].VertexIds);
            Assert.False(openRuns[0].Closed);

            var closedB = new GeometryBuilder();
            closedB.Edges[1] = (10, 20);
            closedB.Edges[2] = (20, 30);
            closedB.Edges[3] = (30, 10);
            closedB.EdgeCurves[1] = 77;
            closedB.EdgeCurves[2] = 77;
            closedB.EdgeCurves[3] = 77;
            var closedRuns = Curves.ChainLooseEdges(closedB);
            Assert.Single(closedRuns);
            Assert.True(closedRuns[0].Closed);
            Assert.Equal(3, closedRuns[0].VertexIds.Count);
        }

        [Fact]
        public void SolveRunArc_AcceptsCircularArcsAndRejectsInvalidOnes()
        {
            var b = new GeometryBuilder();
            b.EdgeCurves[1] = 17;
            b.ArcCurves[17] = MakeFrame();
            var pts = new[] { 0.0, Math.PI / 8, Math.PI / 4, 3 * Math.PI / 8, Math.PI / 2 }
                .Select(t => ArcPoint(t))
                .ToList();
            var arc = Curves.SolveRunArc(b, new long[] { 1 }, Identity, pts, false);
            Assert.NotNull(arc);
            Assert.Equal(new[] { (0, 1, 2) }, arc!.Segments);
            Assert.Equal(3, arc.Points.Count);

            var b4 = new GeometryBuilder();
            for (long i = 1; i <= 4; i++)
            {
                b4.EdgeCurves[i] = 17;
            }
            b4.ArcCurves[17] = MakeFrame(RadiusIn, 2 * Math.PI);
            var pts4 = Enumerable.Range(0, 4).Select(i => ArcPoint(2 * Math.PI * i / 4.0)).ToList();
            var arc4 = Curves.SolveRunArc(b4, new long[] { 1, 2, 3, 4 }, Identity, pts4, true);
            Assert.NotNull(arc4);
            Assert.Equal(new[] { (0, 1, 2), (2, 3, 0) }, arc4!.Segments);

            b.EdgeCurves[2] = 17;
            Assert.Null(Curves.SolveRunArc(b, new long[] { 1 }, Identity, pts.Take(2).ToList(), false));

            var bEllipse = new GeometryBuilder();
            bEllipse.EdgeCurves[1] = 17;
            var ef = MakeFrame();
            ef.YAxis = (0, RadiusIn / 2.0, 0);
            bEllipse.ArcCurves[17] = ef;
            Assert.Null(Curves.SolveRunArc(bEllipse, new long[] { 1 }, Identity, pts.Take(2).ToList(), false));

            var bOff = new GeometryBuilder();
            bOff.EdgeCurves[1] = 17;
            bOff.ArcCurves[17] = MakeFrame();
            var badPts = new List<(double X, double Y, double Z)>
            {
                pts[0],
                (pts[1].X, pts[1].Y + 0.01, pts[1].Z),
            };
            Assert.Null(Curves.SolveRunArc(bOff, new long[] { 1 }, Identity, badPts, false));
        }

        [Fact]
        public void BuildScene_BakesLooseEdgeCurveSetsOnRealFixtures()
        {
            var capillaPath = FixturePath("capilla_quiroz_v17.skp");
            var sc = SkpFile.BuildScene(capillaPath);
            Assert.Equal(20, sc.CurveSets.Count);
            Assert.All(sc.CurveSets, cs =>
            {
                Assert.Equal(2, cs.PointsM.Count);
                Assert.False(cs.Closed);
                Assert.Null(cs.Arc);
            });

            var rootRuns = sc.CurveSets.Where(cs => cs.Path == "ROOT").ToList();
            Assert.Equal(5, rootRuns.Count);
            Assert.Equal(
                Enumerable.Range(1, 5).Select(i => $"Layer0_{i}").OrderBy(s => s),
                rootRuns.Select(cs => cs.Name).OrderBy(s => s));
            Assert.All(rootRuns, cs => Assert.True(cs.NameIsGenerated));

            var innerRuns = sc.CurveSets.Where(cs => cs.Path == "ROOT / puerta").ToList();
            Assert.Equal(15, innerRuns.Count);
            Assert.Equal(new[] { "puerta" }, innerRuns.Select(cs => cs.Name).Distinct());
            Assert.All(innerRuns, cs => Assert.False(cs.NameIsGenerated));

            var scOff = SkpFile.BuildScene(capillaPath, new SkpParseOptions { IncludeCurveSets = false });
            Assert.Empty(scOff.CurveSets);
            Assert.Equal(sc.GlbPrimitives.Count, scOff.GlbPrimitives.Count);

            var gondolaSc = SkpFile.BuildScene(FixturePath("gondola_v20.skp"));
            Assert.Equal(
                new[] { "Gondulas Laterais", "Layer0" },
                gondolaSc.CurveSets.Select(cs => cs.Layer).Distinct().OrderBy(s => s));
            Assert.Equal(57232, gondolaSc.CurveSets.Count(cs => cs.Layer == "Gondulas Laterais"));
        }

        [Theory]
        [InlineData("SU_File.skp")]
        [InlineData("Untitled.skp")]
        [InlineData("capilla_quiroz_v17.skp")]
        [InlineData("gondola_v20.skp")]
        [InlineData("single_material_v17.skp")]
        public void LooseEdgeRuns_MatchesInstancedSceneCurveResources(string fixtureName)
        {
            var bytes = File.ReadAllBytes(FixturePath(fixtureName));
            var model = SkpFile.Parse(bytes);
            var instScene = SkpFile.BuildInstancedScene(bytes);

            bool checkedAny = false;
            foreach (var res in instScene.CurveResources)
            {
                var def = res.DefinitionId.HasValue ? model.Definitions[res.DefinitionId.Value] : model.Root;
                var runs = SkpModel.LooseEdgeRuns(def);
                Assert.Equal(res.Curves.Count, runs.Count);
                for (int i = 0; i < runs.Count; i++)
                {
                    Assert.Equal(res.Curves[i].Closed, runs[i].Closed);
                    Assert.Equal(res.Curves[i].PointsM.Count, runs[i].VertexIds.Count);
                    for (int k = 0; k < runs[i].VertexIds.Count; k++)
                    {
                        var v = def.Vertices[runs[i].VertexIds[k]];
                        double gx = Math.Round(v.X * InchesToM, 6);
                        double gy = Math.Round(v.Z * InchesToM, 6);
                        double gz = Math.Round(-v.Y * InchesToM, 6);
                        Assert.True(Math.Abs(gx - res.Curves[i].PointsM[k].X) < 1e-5);
                        Assert.True(Math.Abs(gy - res.Curves[i].PointsM[k].Y) < 1e-5);
                        Assert.True(Math.Abs(gz - res.Curves[i].PointsM[k].Z) < 1e-5);
                    }
                }
                checkedAny = true;
            }

            if (!checkedAny)
            {
                foreach (var def in model.Definitions.Values.Append(model.Root))
                {
                    Assert.Empty(def.LooseEdgeRuns());
                }
            }
        }
    }
}
