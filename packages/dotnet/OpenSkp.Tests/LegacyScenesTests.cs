using System;
using System.IO;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>
    /// Scenes in a classic (pre-2021) file (openskp#445).
    ///
    /// gondola_v20.skp: SketchUp 2020's own save, one scene ("Cena1", capture flags 0x1FF) with a
    /// parallel-projection camera. sdk_saved_2019/2020.skp: built and saved through SketchUp's C API,
    /// which writes the scene class at a newer schema than the desktop application. Three scenes:
    /// "With camera" (eye 100, -200, 80 looking at 5, 5, 0), "No camera" (no saved camera, hides the
    /// layer "Furniture") and "Plain". SketchUp's own library reads the same scenes back.
    /// sdk_saved_sections_2019/2020.skp: the same model with two scenes, the first capturing everything
    /// a scene can (flags 0x1FF), so the second is only found if the first was read to its last byte.
    /// No private content.
    /// </summary>
    public class LegacyScenesTests
    {
        private static SkpModel Load(string name) => SkpFile.Open(
            Path.Combine(AppContext.BaseDirectory, "fixtures", name));

        [Fact]
        public void GondolaReadsItsSceneWithTheCamera()
        {
            var pages = Load("gondola_v20.skp").Pages;
            var p = Assert.Single(pages);
            Assert.Equal("Cena1", p.Name);
            Assert.Equal(2093.78994893, p.Eye!.Value.X, 6);
            Assert.Equal(412.425280402, p.Eye.Value.Y, 6);
            Assert.Equal(135.404197356, p.Eye.Value.Z, 6);
            Assert.Equal(1802.70152746, p.Target!.Value.X, 6);
            Assert.True(p.Parallel);
            Assert.Equal(180.650232984, p.OrthoHeight, 6);
            Assert.Empty(p.HiddenLayers);
        }

        [Theory]
        [InlineData("sdk_saved_2019.skp")]
        [InlineData("sdk_saved_2020.skp")]
        public void ScenesAreReadWithTheirCamerasAndHiddenLayers(string name)
        {
            var pages = Load(name).Pages.ToArray();
            Assert.Equal(new[] { "With camera", "No camera", "Plain" }, pages.Select(p => p.Name));
            Assert.Equal((100.0, -200.0, 80.0), pages[0].Eye);
            Assert.Equal((5.0, 5.0, 0.0), pages[0].Target);
            Assert.Equal(30.0, pages[0].Fov);
            Assert.False(pages[0].Parallel);
            Assert.Empty(pages[0].HiddenLayers);
            Assert.Null(pages[1].Eye);
            Assert.Null(pages[1].Target);
            Assert.Equal(new[] { "Furniture" }, pages[1].HiddenLayers);
            Assert.Null(pages[2].Eye);
            Assert.Empty(pages[2].HiddenLayers);
        }

        [Theory]
        [InlineData("sdk_saved_sections_2019.skp")]
        [InlineData("sdk_saved_sections_2020.skp")]
        public void ASceneCapturingEveryPropertyIsReadToItsLastByte(string name)
        {
            var pages = Load(name).Pages.ToArray();
            Assert.Equal(new[] { "Everything", "Plain" }, pages.Select(p => p.Name));
            Assert.Equal((100.0, -200.0, 80.0), pages[0].Eye);
            Assert.Equal(new[] { "Furniture" }, pages[0].HiddenLayers);
            Assert.Null(pages[1].Eye);
        }
    }
}
