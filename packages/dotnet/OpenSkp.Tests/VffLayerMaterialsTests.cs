using System;
using System.IO;
using System.Linq;
using Xunit;

namespace OpenSkp.Tests
{
    /// <summary>
    /// Tag colour records (materials/Layer_*) in 2021+ files are excluded from
    /// SkpModel.Materials while keeping tag colours on SkpModel.Layers (#551).
    /// </summary>
    public class VffLayerMaterialsTests
    {
        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        [Fact]
        public void Arcs2021HasZeroMaterialsAndKeepsLayer0Colour()
        {
            var model = SkpFile.Open(FixturePath("arcs_2021.skp"));
            Assert.Empty(model.Materials);
            var layer = Assert.Single(model.Layers);
            Assert.Equal("Layer0", layer.Name);
            Assert.Equal((255, 84, 84), (layer.ColorR, layer.ColorG, layer.ColorB));
        }

        [Fact]
        public void TextureTga2021ListsOnlyRealMaterial()
        {
            var model = SkpFile.Open(FixturePath("texture_tga_2021.skp"));
            Assert.Equal(new[] { "Tex_TGA" }, model.Materials.Select(m => m.Name).ToArray());
            var layer = Assert.Single(model.Layers);
            Assert.Equal("Layer0", layer.Name);
            Assert.Equal((255, 84, 84), (layer.ColorR, layer.ColorG, layer.ColorB));
        }

        [Fact]
        public void RealMaterialNamedLayerPrefixIsPreservedAlongsideTagColour()
        {
            var model = SkpFile.Open(FixturePath("sdk_saved_layer_named_material_2021.skp"));
            var mat = Assert.Single(model.Materials);
            Assert.Equal("Layer_Foo", mat.Name);
            Assert.Equal((200, 10, 20, 255), mat.Color);

            var layers = model.Layers.ToDictionary(l => l.Name, l => (l.ColorR, l.ColorG, l.ColorB));
            Assert.Equal((30, 140, 250), layers["Foo"]);
            Assert.Equal((255, 84, 84), layers["Layer0"]);
        }
    }
}
