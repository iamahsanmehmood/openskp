using System;
using System.IO;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>
    /// A legacy dimension whose connection point is a vertex written in full (#474).
    ///
    /// Fixture legacy_dimension_inline_vertex.skp: two linear dimensions and a construction point after them,
    /// written by openskp.create (the C++ writer), then patched into the layout of the report: the first
    /// dimension's connection points are a CVertex object written in full, class declaration included, and the
    /// second dimension's refer back to it. MFC writes an object in full the first time anything references it,
    /// and a dimension comes before the geometry it is attached to in its entity list. The readers took the first
    /// such connection for the end of the list (an "entity ref is a new object" error that the root list treats as
    /// its end), so nothing after the dimension was placed: a SketchUp 2016 file kept all 15 definitions and none
    /// of its 148 root entities. Synthetic; no private content.
    /// </summary>
    public class LegacyDimensionInlineVertexTests
    {
        [Fact]
        public void TheEntityListContinuesAfterADimensionWithAnInlineVertex()
        {
            var model = SkpFile.Open(Path.Combine(AppContext.BaseDirectory, "fixtures", "legacy_dimension_inline_vertex.skp"));
            Assert.Equal(2, model.Root.Dimensions.Count);
            Assert.Equal(new[] { (101.0, 102.0, 103.0) }, model.Root.ConstructionPoints.Select(p => p.Position));
        }
    }
}
