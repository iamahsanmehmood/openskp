using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>Covers the writer side of openskp#285's "Writer: section
    /// planes" item - AddSectionPlane/AddDimension/AddText/
    /// AddConstructionLine/AddConstructionPoint on SkpBuilder, ported
    /// byte-for-byte from create.py's own methods of the same name (see
    /// that file's docstrings for the real-SketchUp ground truth these
    /// record layouts were harvested from).
    ///
    /// SectionPlane/Text/Dimension round-trip fully since Legacy.cs's own
    /// readers for them already expose real data on Model.Root (fixed in a
    /// prior PR). ConstructionLine/ConstructionPoint now round-trip fully
    /// too - Legacy.cs's readers for those two used to parse the geometry
    /// and then discard it rather than exposing it on Model.Root at all,
    /// fixed alongside adding this writer (openskp#285's "Writer + reader:
    /// construction lines/points").</summary>
    public class WriterAnnotationsTests
    {
        [Fact]
        public void SectionPlaneTextDimensionRoundTrip()
        {
            var builder = SkpCreate.NewFile();
            builder.AddFace(new (double, double, double)[]
            {
                (0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0),
            });
            builder.AddSectionPlane((10, 20, 30), (0, 0, 1));
            builder.AddText("Hello", (5, 5, 5));
            builder.AddDimension((0, 0, 0), (10, 0, 0));

            var model = SkpFile.Parse(builder.ToBytes());

            var sp = Assert.Single(model.Root.SectionPlanes);
            Assert.Equal(0.0, sp.Plane[0], 6);
            Assert.Equal(0.0, sp.Plane[1], 6);
            Assert.Equal(1.0, sp.Plane[2], 6);
            Assert.Equal(-30.0, sp.Plane[3], 6);
            Assert.False(sp.Hidden);

            var text = Assert.Single(model.Root.Texts);
            Assert.Equal("Hello", text.Text);
            Assert.False(text.Hidden);

            Assert.Single(model.Root.Dimensions);
        }

        [Fact]
        public void SectionPlaneNormalizesANonUnitNormal()
        {
            var builder = SkpCreate.NewFile();
            builder.AddFace(new (double, double, double)[]
            {
                (0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0),
            });
            builder.AddSectionPlane((0, 0, 5), (0, 0, 2)); // non-unit normal

            var model = SkpFile.Parse(builder.ToBytes());
            var sp = Assert.Single(model.Root.SectionPlanes);
            Assert.Equal(0.0, sp.Plane[0], 6);
            Assert.Equal(0.0, sp.Plane[1], 6);
            Assert.Equal(1.0, sp.Plane[2], 6);
            Assert.Equal(-5.0, sp.Plane[3], 6);
        }

        [Fact]
        public void TwoDimensionsShareOneEmbeddedFont()
        {
            // AddDimension/AddText only ever embed the CSkFont payload
            // inline on the FIRST call and back-ref it afterwards -
            // exercise that shared-state path across a call pair without
            // asserting on font bytes directly (there's no reader exposure
            // for font data to assert against - the point of this test is
            // that a second dimension after the first doesn't corrupt the
            // archive's slot numbering).
            var builder = SkpCreate.NewFile();
            builder.AddFace(new (double, double, double)[]
            {
                (0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0),
            });
            builder.AddDimension((0, 0, 0), (10, 0, 0));
            builder.AddDimension((0, 5, 0), (10, 5, 0));
            builder.AddText("First", (1, 1, 1));

            var model = SkpFile.Parse(builder.ToBytes());
            Assert.Equal(2, model.Root.Dimensions.Count);
            Assert.Single(model.Root.Texts);
        }

        [Fact]
        public void ConstructionLineAndPointRoundTrip()
        {
            // Legacy.cs's ReadConstructionLine used to parse point/direction/
            // start/end into locals and then discard all of them (the same
            // shape as the SectionPlane/Text/Dimension bugs fixed in a prior
            // PR, just not yet ported for these two entities) - fixed
            // alongside adding this writer, matching C++'s own reader, which
            // already exposed this correctly.
            var builder = SkpCreate.NewFile();
            builder.AddFace(new (double, double, double)[]
            {
                (0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0),
            });
            builder.AddConstructionPoint((1, 2, 3));
            builder.AddConstructionLine((0, 0, 0), point2: (10, 0, 0));
            builder.AddConstructionLine((0, 0, 0), direction: (0, 0, 1));
            builder.AddSectionPlane((10, 20, 30), (0, 0, 1)); // still readable afterwards

            var model = SkpFile.Parse(builder.ToBytes());

            var cp = Assert.Single(model.Root.ConstructionPoints);
            Assert.Equal((1.0, 2.0, 3.0), cp.Position);

            Assert.Equal(2, model.Root.ConstructionLines.Count);
            var bounded = model.Root.ConstructionLines[0];
            Assert.Equal((0.0, 0.0, 0.0), bounded.Point);
            Assert.Equal((1.0, 0.0, 0.0), bounded.Direction);
            Assert.NotNull(bounded.Start);
            Assert.NotNull(bounded.End);
            Assert.Equal((0.0, 0.0, 0.0), bounded.Start!.Value);
            Assert.Equal((10.0, 0.0, 0.0), bounded.End!.Value);

            var unbounded = model.Root.ConstructionLines[1];
            Assert.Equal((0.0, 0.0, 1.0), unbounded.Direction);
            Assert.Null(unbounded.Start);
            Assert.Null(unbounded.End);

            var sp = Assert.Single(model.Root.SectionPlanes);
            Assert.Equal(-30.0, sp.Plane[3], 6);
        }

        [Fact]
        public void AddSectionPlaneRejectsAZeroNormal()
        {
            var builder = SkpCreate.NewFile();
            builder.AddFace(new (double, double, double)[]
            {
                (0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0),
            });
            Assert.Throws<SkpWriteException>(() => builder.AddSectionPlane((0, 0, 0), (0, 0, 0)));
        }

        [Fact]
        public void AddConstructionLineRequiresExactlyOneOfPoint2OrDirection()
        {
            var builder = SkpCreate.NewFile();
            builder.AddFace(new (double, double, double)[]
            {
                (0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0),
            });
            Assert.Throws<SkpWriteException>(() => builder.AddConstructionLine((0, 0, 0)));
            Assert.Throws<SkpWriteException>(() =>
                builder.AddConstructionLine((0, 0, 0), point2: (1, 0, 0), direction: (0, 1, 0)));
        }
    }
}
