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

        [Fact]
        public void NestedDefinitionConstructionRoundTrips()
        {
            // Construction line is last in the nested definition on purpose:
            // its nrel=0 tail used to calibrate ClineTail to 7 on v17,
            // desyncing the archive walk and corrupting root-level guides.
            var builder = SkpCreate.NewFile();
            ComponentDefinitionBuilder nested;
            using (nested = builder.AddComponentDefinition("GuideGroup"))
            {
                nested.AddFace(new (double, double, double)[]
                {
                    (0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0),
                });
                nested.AddConstructionPoint((4, 5, 6));
                nested.AddConstructionLine((1, 1, 1), point2: (2, 2, 2));
            }
            builder.AddInstance(nested);
            builder.AddConstructionPoint((1, 2, 3));
            builder.AddConstructionLine((0, 0, 0), point2: (12, 0, 0));
            builder.AddConstructionLine((0, 0, 4), direction: (0, 0, 1));

            var model = SkpFile.Parse(builder.ToBytes());
            var rootCp = Assert.Single(model.Root.ConstructionPoints);
            Assert.Equal((1.0, 2.0, 3.0), rootCp.Position);
            Assert.Equal(2, model.Root.ConstructionLines.Count);
            Assert.Equal((12.0, 0.0, 0.0), model.Root.ConstructionLines[0].End!.Value);
            Assert.Null(model.Root.ConstructionLines[1].Start);
            Assert.Null(model.Root.ConstructionLines[1].End);
            Assert.Equal((0.0, 0.0, 4.0), model.Root.ConstructionLines[1].Point);

            var group = Assert.Single(model.Definitions.Values, d => d.Name == "GuideGroup");
            var defCp = Assert.Single(group.ConstructionPoints);
            Assert.Equal((4.0, 5.0, 6.0), defCp.Position);
            var defCl = Assert.Single(group.ConstructionLines);
            Assert.NotNull(defCl.Start);
            Assert.NotNull(defCl.End);
            Assert.Equal(1.0, defCl.Start!.Value.X, 6);
            Assert.Equal(2.0, defCl.End!.Value.X, 6);
        }

        [Fact]
        public void ConstructionGuidesRejectNonFiniteAndLeaveAddLayerUsable()
        {
            var builder = SkpCreate.NewFile();
            foreach (var bad in new (double, double, double)[]
            {
                (double.NaN, 0, 0),
                (double.PositiveInfinity, 0, 0),
                (double.NegativeInfinity, 1, 2),
            })
            {
                Assert.Throws<SkpWriteException>(() => builder.AddConstructionPoint(bad));
                Assert.Throws<SkpWriteException>(() => builder.AddConstructionLine(bad, point2: (1, 0, 0)));
                Assert.Throws<SkpWriteException>(() => builder.AddConstructionLine((0, 0, 0), point2: bad));
                Assert.Throws<SkpWriteException>(() => builder.AddConstructionLine((0, 0, 0), direction: bad));
            }
            Assert.Throws<SkpWriteException>(() => builder.AddConstructionLine((0, 0, 0), point2: (0, 0, 0)));

            int tag = builder.AddLayer("GuidesAfterReject");
            ComponentDefinitionBuilder nested;
            using (nested = builder.AddComponentDefinition("NestedGuides"))
            {
                Assert.Throws<SkpWriteException>(() => nested.AddConstructionPoint((double.NaN, 0, 0)));
                Assert.Throws<SkpWriteException>(() => nested.AddConstructionLine((0, 0, 0), direction: (0, double.PositiveInfinity, 0)));
                nested.AddFace(new (double, double, double)[] { (0, 0, 0), (5, 0, 0), (5, 5, 0), (0, 5, 0) }, layer: tag);
            }
            builder.AddInstance(nested, layer: tag);
            var model = SkpFile.Parse(builder.ToBytes());
            Assert.Contains(model.Layers, l => l.Name == "GuidesAfterReject");
        }

        [Fact]
        public void GuideOptionsRoundTripHiddenFlagAndLayer()
        {
            var builder = SkpCreate.NewFile();
            int guides = builder.AddLayer("Guides");
            ComponentDefinitionBuilder nested;
            using (nested = builder.AddComponentDefinition("NestedGuides"))
            {
                nested.AddConstructionPoint((1, 1, 1), layer: guides, hidden: true);
                nested.AddConstructionLine((0, 0, 0), point2: (5, 0, 0), hidden: true);
            }
            builder.AddInstance(nested);
            builder.AddConstructionPoint((1, 2, 3), hidden: true);
            builder.AddConstructionPoint((4, 5, 6), layer: guides, hidden: false);
            builder.AddConstructionLine((0, 0, 0), point2: (10, 0, 0), layer: guides, hidden: true);
            builder.AddConstructionLine((0, 0, 4), direction: (0, 0, 1));

            var model = SkpFile.Parse(builder.ToBytes());
            Assert.Equal(2, model.Root.ConstructionPoints.Count);
            Assert.True(model.Root.ConstructionPoints[0].Hidden);
            Assert.Equal("", model.Root.ConstructionPoints[0].Layer);
            Assert.False(model.Root.ConstructionPoints[1].Hidden);
            Assert.Equal("Guides", model.Root.ConstructionPoints[1].Layer);

            Assert.Equal(2, model.Root.ConstructionLines.Count);
            Assert.True(model.Root.ConstructionLines[0].Hidden);
            Assert.Equal("Guides", model.Root.ConstructionLines[0].Layer);
            Assert.False(model.Root.ConstructionLines[1].Hidden);
            Assert.Equal("", model.Root.ConstructionLines[1].Layer);

            var defn = Assert.Single(model.Definitions.Values, d => d.Name == "NestedGuides");
            Assert.Equal("Guides", defn.ConstructionPoints[0].Layer);
            Assert.True(defn.ConstructionPoints[0].Hidden);
            Assert.Equal("", defn.ConstructionLines[0].Layer);
            Assert.True(defn.ConstructionLines[0].Hidden);
        }

        [Fact]
        public void GuideOptionsRejectAForeignLayerHandle()
        {
            var builder = SkpCreate.NewFile();
            builder.AddFace(new (double, double, double)[]
            {
                (0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0),
            });
            Assert.Throws<SkpWriteException>(() => builder.AddConstructionPoint((1, 2, 3), layer: 42));
            Assert.Throws<SkpWriteException>(() => builder.AddConstructionLine((0, 0, 0), point2: (10, 0, 0), layer: 42));
        }
    }
}
