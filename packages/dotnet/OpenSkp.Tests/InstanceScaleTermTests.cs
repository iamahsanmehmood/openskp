using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>
    /// An instance scaled through the matrix's scale term (openskp#444).
    ///
    /// SketchUp can keep a uniform scale in the homogeneous term of an
    /// instance's transformation instead of in the 3x3: the 3x3 stays as it
    /// is, the translation is in the scaled units, and the 16th value
    /// <c>w</c> is the divisor. SketchUp's own library reports a 100 in cube
    /// placed with <c>w = 100</c> and a translation of 2000 in as a 1 in cube
    /// at x = 20 in. The 13-value matrix stores <c>1/w</c> at index 12, and
    /// every consumer used to ignore it, so such an instance came out 100x
    /// its size and 100x its distance from its parent.
    ///
    /// Fixtures: instance_scale_term_2021.skp and instance_scale_term_v6.skp -
    /// one 100 in cube definition placed twice, once through the 3x3 (scaling
    /// 0.01) and once through w = 100 with a translation of 2000 in, built
    /// and saved through the installed SketchUp library. No private content.
    /// </summary>
    public class InstanceScaleTermTests
    {
        private const double InchMm = 25.4;

        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        public static IEnumerable<object[]> Files => new[]
        {
            new object[] { "instance_scale_term_2021.skp" },
            new object[] { "instance_scale_term_v6.skp" },
        };

        [Fact]
        public void ScaleTermScalesTheTranslatedPoint()
        {
            var m = new double[] { 1, 0, 0, 0, 1, 0, 0, 0, 1, 2000, 0, 0, 0.01 };
            var p = Transforms.TransformPoint(m, (100, 100, 100));
            Assert.Equal(21.0, p.X, 9);
            Assert.Equal(1.0, p.Y, 9);
            Assert.Equal(1.0, p.Z, 9);
        }

        [Fact]
        public void AScaleOfOneChangesNothing()
        {
            var m = new double[] { 2, 0, 0, 0, 3, 0, 0, 0, 4, 5, 6, 7, 1 };
            Assert.Equal((7.0, 9.0, 11.0), Transforms.TransformPoint(m, (1, 1, 1)));
            Assert.Same(m, Transforms.FoldScale(m));
        }

        [Fact]
        public void ScalingThroughThe3x3AndThroughTheTermAgree()
        {
            var via3x3 = new double[] { 0.01, 0, 0, 0, 0.01, 0, 0, 0, 0.01, 0, 0, 0, 1 };
            var viaTerm = new double[] { 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0.01 };
            var a = Transforms.TransformPoint(via3x3, (50, 20, 10));
            var b = Transforms.TransformPoint(viaTerm, (50, 20, 10));
            Assert.Equal(a.X, b.X, 9);
            Assert.Equal(a.Y, b.Y, 9);
            Assert.Equal(a.Z, b.Z, 9);
        }

        [Fact]
        public void CompositionMatchesApplyingOneTransformAfterTheOther()
        {
            var rng = new Random(12345);
            double[] scales = { 1.0, 1.0, 0.01, 0.5, 2.0, 0.1 };
            double[] RandomMatrix()
            {
                var m = new double[13];
                for (int i = 0; i < 9; i++) m[i] = rng.NextDouble() * 4 - 2;
                for (int i = 9; i < 12; i++) m[i] = rng.NextDouble() * 100 - 50;
                m[12] = scales[rng.Next(scales.Length)];
                return m;
            }
            for (int k = 0; k < 40; k++)
            {
                var parent = RandomMatrix();
                var child = RandomMatrix();
                var p = (rng.NextDouble() * 200 - 100, rng.NextDouble() * 200 - 100, rng.NextDouble() * 200 - 100);
                var composed = Transforms.MultiplyMatrices(new List<double>(parent), new List<double>(child)).ToArray();
                var want = Transforms.TransformPoint(parent, Transforms.TransformPoint(child, p));
                var got = Transforms.TransformPoint(composed, p);
                Assert.Equal(want.X, got.X, 6);
                Assert.Equal(want.Y, got.Y, 6);
                Assert.Equal(want.Z, got.Z, 6);
            }
        }

        [Fact]
        public void AComposedMatrixHasItsScaleFoldedIn()
        {
            var parent = new List<double> { 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0.01 };
            var child = new List<double> { 1, 0, 0, 0, 1, 0, 0, 0, 1, 2000, 0, 0, 1 };
            var o = Transforms.MultiplyMatrices(parent, child);
            Assert.Equal(1.0, o[12]);
            Assert.Equal(20.0, o[9], 9);
        }

        [Theory]
        [MemberData(nameof(Files))]
        public void TheParseStillReportsTheRawMatrix(string name)
        {
            var model = SkpFile.Open(FixturePath(name));
            Assert.Contains(model.Root.Instances, i =>
                Math.Abs(i.Matrix[12] - 0.01) < 1e-12 && Math.Abs(i.Matrix[9] - 2000.0) < 1e-9);
        }

        [Theory]
        [MemberData(nameof(Files))]
        public void TheSceneBothCubesWhereSketchUpDoes(string name)
        {
            var scene = SkpFile.BuildScene(FixturePath(name));
            var xs = scene.SceneHierarchy.Children.Select(n => n.PositionMm.X).OrderBy(x => x).ToList();
            Assert.Equal(0.0, xs[0], 6);
            Assert.Equal(20 * InchMm, xs[1], 6);                     // 508 mm, not 50,800
        }

        [Theory]
        [MemberData(nameof(Files))]
        public void TheInstancedSceneNodeMatricesCarryTheScaleAndTheTranslation(string name)
        {
            var scene = SkpFile.BuildInstancedScene(FixturePath(name));
            var placed = scene.SceneHierarchy.Children;
            Assert.Equal(2, placed.Count);
            foreach (var n in placed)
            {
                Assert.Equal(0.01, n.Matrix[0], 9);
                Assert.Equal(0.01, n.Matrix[5], 9);
                Assert.Equal(0.01, n.Matrix[10], 9);
            }
            var tx = placed.Select(n => n.Matrix[12]).OrderBy(x => x).ToList();
            Assert.Equal(0.0, tx[0], 9);
            Assert.Equal(20 * InchMm / 1000.0, tx[1], 9);            // 0.508 m, not 50.8
        }
    }
}
