using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Xunit;

namespace OpenSkp.Tests
{
    public class SceneCaptureFlagsTests
    {
        private static readonly string[] SceneOrder =
        {
            "Camera",
            "RenderingOptions",
            "ShadowInfo",
            "Axes",
            "Hidden",
            "HiddenGeometry",
            "HiddenObjects",
            "HiddenLayers",
            "SectionPlanes",
            "ShadowsCamera",
            "AxesCamera",
        };

        private static string CppFixturePath(string name)
        {
            var dir = new DirectoryInfo(AppContext.BaseDirectory);
            while (dir != null)
            {
                var candidate = Path.Combine(
                    dir.FullName, "packages", "cpp", "tests", "fixtures", name);
                if (File.Exists(candidate))
                {
                    return candidate;
                }
                dir = dir.Parent;
            }
            throw new FileNotFoundException($"Fixture not found: {name}");
        }

        [Theory]
        [InlineData(2015)]
        [InlineData(2016)]
        [InlineData(2017)]
        [InlineData(2018)]
        [InlineData(2019)]
        [InlineData(2020)]
        public void Classic2015To2020ReadsAllScenesWithCamerasAndHiddenLayers(int year)
        {
            var model = SkpFile.Open(CppFixturePath($"scene_capture_flags_{year}.skp"));
            Assert.Equal(SceneOrder, model.Pages.Select(p => p.Name).ToArray());
            var byName = model.Pages.ToDictionary(p => p.Name);

            var cam = byName["Camera"];
            Assert.Equal((120.0, -240.0, 90.0), cam.Eye);
            Assert.Equal((5.0, 5.0, 0.0), cam.Target);
            Assert.NotNull(cam.Up);
            Assert.Equal(-0.13407807210149128, cam.Up!.Value.X, 9);
            Assert.Equal(0.28564458839013357, cam.Up.Value.Y, 9);
            Assert.Equal(0.9489100271917137, cam.Up.Value.Z, 9);
            Assert.False(cam.Parallel);
            Assert.Equal(42.0, cam.Fov, 9);

            var shadowsCam = byName["ShadowsCamera"];
            Assert.Equal((0.0, -300.0, 150.0), shadowsCam.Eye);
            Assert.Equal((0.0, 0.0, 0.0), shadowsCam.Target);
            Assert.NotNull(shadowsCam.Up);
            Assert.Equal(0.0, shadowsCam.Up!.Value.X, 9);
            Assert.Equal(0.44721359549995804, shadowsCam.Up.Value.Y, 9);
            Assert.Equal(0.8944271909999161, shadowsCam.Up.Value.Z, 9);
            Assert.True(shadowsCam.Parallel);
            Assert.Equal(80.0, shadowsCam.OrthoHeight, 9);

            var axesCam = byName["AxesCamera"];
            Assert.Equal((-180.0, -180.0, 110.0), axesCam.Eye);
            Assert.Equal((10.0, 10.0, 0.0), axesCam.Target);
            Assert.NotNull(axesCam.Up);
            Assert.Equal(0.2678945632789157, axesCam.Up!.Value.X, 9);
            Assert.Equal(0.2678945632789157, axesCam.Up.Value.Y, 9);
            Assert.Equal(0.9254539458726179, axesCam.Up.Value.Z, 9);
            Assert.False(axesCam.Parallel);
            Assert.Equal(55.0, axesCam.Fov, 9);

            foreach (var name in SceneOrder)
            {
                var p = byName[name];
                if (name != "Camera" && name != "ShadowsCamera" && name != "AxesCamera")
                {
                    Assert.Null(p.Eye);
                    Assert.Null(p.Target);
                }
                if (name == "HiddenLayers")
                {
                    Assert.Equal(new[] { "HiddenTag" }, p.HiddenLayers);
                }
                else
                {
                    Assert.Empty(p.HiddenLayers);
                }
            }
        }

        [Theory]
        [InlineData(2015)]
        [InlineData(2016)]
        [InlineData(2017)]
        [InlineData(2018)]
        [InlineData(2019)]
        [InlineData(2020)]
        public void Classic2015To2020ResolvesHiddenEntities(int year)
        {
            var model = SkpFile.Open(CppFixturePath($"scene_capture_flags_{year}.skp"));
            var byName = model.Pages.ToDictionary(p => p.Name);

            if (year < 2017)
            {
                foreach (var p in model.Pages)
                {
                    Assert.Empty(p.HiddenEntityIds);
                }
            }
            else
            {
                var crate = Assert.Single(model.Root.Instances, i => i.Name == "Crate");
                Assert.Equal(30L, crate.Id);
                Assert.Contains(model.Root.Edges.Values, e => e.PersistentId == 21L);
                Assert.Equal(new HashSet<long> { 21L, 30L }, new HashSet<long>(byName["Hidden"].HiddenEntityIds));
                Assert.Equal(new[] { 21L }, byName["HiddenGeometry"].HiddenEntityIds);
                Assert.Equal(new[] { 30L }, byName["HiddenObjects"].HiddenEntityIds);
                foreach (var name in SceneOrder)
                {
                    if (name != "Hidden" && name != "HiddenGeometry" && name != "HiddenObjects")
                    {
                        Assert.Empty(byName[name].HiddenEntityIds);
                    }
                }
            }
        }
    }
}
