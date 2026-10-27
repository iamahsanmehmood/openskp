using System;
using System.IO;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>
    /// <c>Definition.Arcs</c>: the analytic arc of every curve that is one (openskp#462).
    ///
    /// Fixtures: arcs_2017.skp, arcs_2020.skp (classic container) and arcs_2021.skp (VFF) -
    /// three arcs built through the installed SketchUp library:
    /// a loose arc (centre 0,0,0, radius 10, 0 to 90 degrees, 12 segments); a disc (a full
    /// circle bounding a face, centre 50,0,0, radius 5, 24 sides); and a half disc (an arc
    /// closed by its chord, bounding a face, centre 100,0,0, radius 8, 0 to 180 degrees, 12
    /// segments). SketchUp reads the same centres, radii and angles back from all three files.
    /// Untitled.skp is a real file whose 368 arcs (24-edge circles) agree with SketchUp's own
    /// values to 5e-6 inches. No private content.
    /// </summary>
    public class ArcCurveTests
    {
        public static readonly object[][] Names =
        {
            new object[] { "arcs_2017.skp" },
            new object[] { "arcs_2020.skp" },
            new object[] { "arcs_2021.skp" },
        };

        // centre x, radius, sweep in radians, edges, full circle
        private static readonly (double Cx, double Radius, double Sweep, int Edges, bool Full)[] Expected =
        {
            (0.0, 10.0, Math.PI / 2, 12, false),
            (50.0, 5.0, 2 * Math.PI, 24, true),
            (100.0, 8.0, Math.PI, 12, false),
        };

        private static SkpModel Load(string name) => SkpFile.Open(
            Path.Combine(AppContext.BaseDirectory, "fixtures", name));

        [Theory, MemberData(nameof(Names))]
        public void EveryArcIsReadWithItsCentreRadiusAndAngles(string name)
        {
            var arcs = Load(name).Root.Arcs.Values.OrderBy(a => a.Center.X).ToArray();
            Assert.Equal(3, arcs.Length);
            for (int i = 0; i < 3; i++)
            {
                var a = arcs[i];
                var e = Expected[i];
                Assert.Equal(e.Cx, a.Center.X, 9);
                Assert.Equal(0.0, a.Center.Y, 9);
                Assert.Equal(0.0, a.Center.Z, 9);
                Assert.Equal(1.0, a.Normal.Z, 9);
                Assert.Equal(e.Radius, a.Radius, 9);
                Assert.Equal(0.0, a.StartAngle, 9);
                double sweep = (a.EndAngle - a.StartAngle) % (2 * Math.PI);
                Assert.Equal(e.Sweep % (2 * Math.PI), sweep, 9);
                Assert.Equal(e.Full, a.IsFullCircle);
                Assert.Equal(e.Radius, a.XAxis.X, 9);
                Assert.Equal(e.Radius, a.YAxis.Y, 9);
            }
        }

        [Theory, MemberData(nameof(Names))]
        public void AnArcIsJoinedToItsEdgesByCurveId(string name)
        {
            var root = Load(name).Root;
            var perCurve = root.Edges.Values.Where(e => e.CurveId != null)
                .GroupBy(e => e.CurveId!.Value).ToDictionary(g => g.Key, g => g.Count());
            var ordered = root.Arcs.OrderBy(kv => kv.Value.Center.X).Select(kv => perCurve[kv.Key]).ToArray();
            Assert.Equal(Expected.Select(e => e.Edges).ToArray(), ordered);
        }

        [Theory]
        [InlineData("instance_scale_term_v6.skp")]
        [InlineData("instance_scale_term_2021.skp")]
        public void AModelWithoutArcsHasNone(string name)
        {
            var model = Load(name);
            Assert.Empty(model.Root.Arcs);
            Assert.All(model.Definitions.Values, d => Assert.Empty(d.Arcs));
        }

        [Fact]
        public void UntitledHas368ArcsEachTheCircleSketchUpReports()
        {
            var model = Load("Untitled.skp");
            int total = 0;
            foreach (var d in model.Definitions.Values.Append(model.Root))
            {
                var perCurve = d.Edges.Values.Where(e => e.CurveId != null)
                    .GroupBy(e => e.CurveId!.Value).ToDictionary(g => g.Key, g => g.Count());
                foreach (var kv in d.Arcs)
                {
                    total++;
                    Assert.Equal(24, perCurve[kv.Key]);
                    Assert.True(kv.Value.IsFullCircle);
                }
            }
            Assert.Equal(368, total);
        }
    }
}
