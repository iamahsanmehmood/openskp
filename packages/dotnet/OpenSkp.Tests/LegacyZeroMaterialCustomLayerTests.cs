using System;
using System.IO;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>
    /// Regression for SketchUp 2018 (file version 18) saves with no
    /// CMaterial records and a custom tag listed ahead of Layer0.
    ///
    /// Those files take ProbeLayerAnchorBases (the two-material bootstrap
    /// needs matCount &gt;= 2). The declared layer count is 1; Layer0
    /// follows after a 16-byte colour-layer extension. Missing that
    /// extension left the probe on padding: base probe: anchor resolved to.
    ///
    /// Fixture is a SketchUp 2018 layout sample (two construction lines, no
    /// faces). Identifiable strings were replaced with same-length ASCII so
    /// the MFC record sizes are unchanged.
    /// </summary>
    public class LegacyZeroMaterialCustomLayerTests
    {
        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        [Fact]
        public void ParsesV18CustomTagAheadOfLayer0()
        {
            var model = SkpFile.Open(FixturePath("zero_material_custom_layer_v18.skp"));

            Assert.Equal("{18.0.16975}", model.Version);
            Assert.Empty(model.Materials);
            Assert.True(model.Layers.Count >= 2);
            Assert.Contains(model.Layers, l => l.Name == "Layer0");
            Assert.Contains(model.Layers, l => l.Name == "Guide");
            Assert.Empty(model.Root.Faces);
            Assert.Equal(2, model.Root.ConstructionLines.Count);
        }
    }
}
