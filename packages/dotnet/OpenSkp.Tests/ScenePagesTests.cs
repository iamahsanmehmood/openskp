using System;
using System.IO;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    public class ScenePagesTests
    {
        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        [Theory]
        [InlineData("gondola_v20.skp", new[] { "Cena1" })]
        [InlineData("sdk_saved_2020.skp", new[] { "With camera", "No camera", "Plain" })]
        [InlineData("scene_without_camera_2021.skp", new[] { "With camera", "No camera", "Plain" })]
        [InlineData("nesting_2017.skp", new string[0])]
        public void SceneAndInstancedScenePagesEqualParsedModelPages(string name, string[] expectedNames)
        {
            var path = FixturePath(name);
            var model = SkpFile.Open(path);
            var scene = SkpFile.BuildScene(path);
            var instanced = SkpFile.BuildInstancedScene(path);

            Assert.Equal(expectedNames, model.Pages.Select(p => p.Name).ToArray());
            Assert.Equal(model.Pages, scene.Pages);
            Assert.Equal(model.Pages, instanced.Pages);
        }

        [Fact]
        public void ScenePagesKeepNativeInchesAndHiddenLayers()
        {
            var path = FixturePath("scene_without_camera_2021.skp");
            foreach (var pages in new[] { SkpFile.BuildScene(path).Pages, SkpFile.BuildInstancedScene(path).Pages })
            {
                Assert.Equal((100.0, -200.0, 80.0), pages[0].Eye);
                Assert.Equal((5.0, 5.0, 0.0), pages[0].Target);
                Assert.Null(pages[1].Eye);
                Assert.Equal(new[] { "Furniture" }, pages[1].HiddenLayers);
            }
        }
    }
}
