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
    /// prior PR). ConstructionLine/ConstructionPoint only smoke-test here -
    /// Legacy.cs's readers for those two currently discard the geometry
    /// they parse rather than exposing it on Model.Root at all (a separate,
    /// already-tracked openskp#285 item: "Writer + reader: construction
    /// lines/points"), so there is nothing yet to assert on beyond "the
    /// file still parses cleanly with these entities present".</summary>
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
        public void ConstructionLineAndPointDoNotCorruptTheFile()
        {
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
