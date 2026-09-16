using System;
using System.IO;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>
    /// GeometryBuilder.SectionPlanes/Texts/Dimensions were already correctly
    /// read into LegacyBuilder by FillBuilder's own SectionPlaneRec/TextRec/
    /// DimRec dispatch, but ToGeometryBuilder - the conversion step that
    /// copies a LegacyBuilder into the shared GeometryBuilder representation
    /// the rest of the pipeline (Parser.cs, Model.cs) actually reads from -
    /// only ever copied Vertices/Edges/EdgeFlags/Faces/Instances, silently
    /// dropping these three fields entirely. Same "already-decoded-but-
    /// discarded" shape as this project's earlier layer/face/instance-hidden
    /// and attribute-container fixes, just one level deeper - and, like
    /// those, caught only because nothing in this project's own test suite
    /// (in ANY language, including Python) had ever exercised a real
    /// section-plane/text round-trip before (openskp#285).
    ///
    /// legacy_annotations.skp was generated with Python's own create() API
    /// (add_face + add_section_plane + add_text + add_dimension) and its
    /// values cross-checked against Python's own reader as ground truth -
    /// this project's own writer doesn't have add_section_plane/add_text/
    /// add_dimension yet (a separate, still-open openskp#285 item).
    /// </summary>
    public class SectionPlaneTextDimensionTests
    {
        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        [Fact]
        public void SectionPlaneRoundTripsThroughToGeometryBuilder()
        {
            var model = SkpFile.Open(FixturePath("legacy_annotations.skp"));

            var sp = Assert.Single(model.Root.SectionPlanes);
            Assert.Equal(0.0, sp.Plane[0], 6);
            Assert.Equal(0.0, sp.Plane[1], 6);
            Assert.Equal(1.0, sp.Plane[2], 6);
            Assert.Equal(-30.0, sp.Plane[3], 6);
            Assert.False(sp.Hidden);
        }

        [Fact]
        public void TextRoundTripsThroughToGeometryBuilder()
        {
            var model = SkpFile.Open(FixturePath("legacy_annotations.skp"));

            var text = Assert.Single(model.Root.Texts);
            Assert.Equal("Hello", text.Text);
            Assert.False(text.Hidden);
        }

        [Fact]
        public void DimensionRoundTripsThroughToGeometryBuilder()
        {
            var model = SkpFile.Open(FixturePath("legacy_annotations.skp"));

            Assert.Single(model.Root.Dimensions);
        }
    }
}
