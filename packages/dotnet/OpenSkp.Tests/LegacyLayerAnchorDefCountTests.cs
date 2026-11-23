using System.Collections.Generic;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>Regression (#534 / #285): after the last CLayer the reader
    /// sees &lt;anchor u16&gt;&lt;def_count u32&gt; and then the first
    /// CComponentDefinition. SkipClayerParentRef consumed the anchor as a v18
    /// "parent" back-ref whenever both u16s at that position pointed at CLayer
    /// objects - which also happens when the definition count (11, 12, or 13)
    /// coincides with a tag's slot.</summary>
    public class LegacyLayerAnchorDefCountTests
    {
        private static byte[] Build(int nTags, int nDefs)
        {
            var builder = SkpCreate.NewFile();
            int mat = builder.AddMaterial("M", (200, 100, 50, 255));
            var layers = new List<int>();
            for (int i = 0; i < nTags; i++)
            {
                layers.Add(builder.AddLayer($"T{i}"));
            }
            var defs = new List<ComponentDefinitionBuilder>();
            for (int i = 0; i < nDefs; i++)
            {
                ComponentDefinitionBuilder comp;
                using (comp = builder.AddComponentDefinition($"P{i}"))
                {
                    comp.AddFace(
                        new (double, double, double)[]
                        {
                            (0, 0, 0), (1 + i, 0, 0), (1 + i, 1, 0), (0, 1, 0),
                        },
                        material: mat);
                }
                defs.Add(comp);
            }
            ComponentDefinitionBuilder container;
            using (container = builder.AddComponentDefinition("Container"))
            {
                for (int i = 0; i < defs.Count; i++)
                {
                    container.AddInstance(
                        defs[i],
                        name: $"i{i}",
                        translation: (0, 2 * i, 0),
                        layer: layers[0]);
                }
            }
            builder.AddInstance(container, name: "root", layer: layers[0]);
            return builder.ToBytes();
        }

        [Theory]
        [InlineData(1, 10)]
        [InlineData(1, 11)]
        [InlineData(1, 12)]
        [InlineData(1, 13)]
        [InlineData(1, 14)]
        [InlineData(2, 10)]
        [InlineData(2, 11)]
        [InlineData(2, 12)]
        [InlineData(2, 13)]
        [InlineData(2, 14)]
        public void DefinitionCountMatchingATagSlotRoundTrips(int nTags, int nDefs)
        {
            var model = SkpFile.Parse(Build(nTags, nDefs));
            var expectedLayers = new List<string> { "Layer0" };
            for (int i = 0; i < nTags; i++) expectedLayers.Add($"T{i}");
            Assert.Equal(expectedLayers, model.Layers.Select(l => l.Name).ToList());
            Assert.Equal(nDefs + 1, model.Definitions.Count);
        }
    }
}
