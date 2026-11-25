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

        private static readonly (string Name, Func<Page, bool?> Getter)[] AllUseProps =
        {
            ("use_camera", p => p.UseCamera),
            ("use_rendering_options", p => p.UseRenderingOptions),
            ("use_shadow_info", p => p.UseShadowInfo),
            ("use_axes", p => p.UseAxes),
            ("use_hidden_layers", p => p.UseHiddenLayers),
            ("use_section_planes", p => p.UseSectionPlanes),
            ("use_hidden_geometry", p => p.UseHiddenGeometry),
            ("use_hidden_objects", p => p.UseHiddenObjects),
        };

        private static string LocalFixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

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

        private static Dictionary<string, Page> PagesByName(string path) =>
            SkpFile.Open(path).Pages.ToDictionary(p => p.Name);

        [Fact]
        public void ExposesCaptureFlagBitConstants()
        {
            Assert.Equal(0x001u, Page.PageUseCamera);
            Assert.Equal(0x002u, Page.PageUseRenderingOptions);
            Assert.Equal(0x004u, Page.PageUseShadowInfo);
            Assert.Equal(0x008u, Page.PageUseAxes);
            Assert.Equal(0x010u, Page.PageUseHidden);
            Assert.Equal(0x020u, Page.PageUseHiddenLayers);
            Assert.Equal(0x040u, Page.PageUseSectionPlanes);
            Assert.Equal(0x080u, Page.PageUseHiddenGeometry);
            Assert.Equal(0x100u, Page.PageUseHiddenObjects);
        }

        [Theory]
        [InlineData("scene_hidden_entities_2017.skp")]
        [InlineData("scene_hidden_entities_2019.skp")]
        [InlineData("scene_hidden_entities_2020.skp")]
        [InlineData("scene_hidden_entities_2024.skp")]
        public void EmptyHiddenListIsToldApart(string name)
        {
            var pages = PagesByName(CppFixturePath(name));
            Assert.Empty(pages["All"].HiddenEntityIds);
            Assert.Empty(pages["Not saved"].HiddenEntityIds);
            Assert.Equal(0x191u, pages["All"].CaptureFlags);
            Assert.Equal(true, pages["All"].UseHiddenObjects);
            Assert.Equal(true, pages["All"].UseHiddenGeometry);
            Assert.Equal(0x001u, pages["Not saved"].CaptureFlags);
            Assert.Equal(false, pages["Not saved"].UseHiddenObjects);
            Assert.Equal(false, pages["Not saved"].UseHiddenGeometry);
            Assert.Equal(true, pages["Not saved"].UseCamera);
        }

        [Theory]
        [InlineData("scene_hidden_entities_2017.skp")]
        [InlineData("scene_hidden_entities_2019.skp")]
        [InlineData("scene_hidden_entities_2020.skp")]
        [InlineData("scene_hidden_entities_2024.skp")]
        public void EverySceneThatHidesSomethingSavesIt(string name)
        {
            foreach (var page in PagesByName(CppFixturePath(name)).Values)
            {
                if (page.HiddenEntityIds.Count > 0)
                {
                    Assert.True(page.UseHiddenObjects == true || page.UseHiddenGeometry == true);
                }
            }
        }

        [Fact]
        public void ScenesThatSaveNoCamera()
        {
            var path = LocalFixturePath("scene_without_camera_2021.skp");
            var pages = PagesByName(path);
            Assert.Equal(0x001u, pages["With camera"].CaptureFlags);
            Assert.Equal(true, pages["With camera"].UseCamera);
            Assert.Equal(false, pages["With camera"].UseHiddenLayers);
            Assert.Equal(0x020u, pages["No camera"].CaptureFlags);
            Assert.Equal(false, pages["No camera"].UseCamera);
            Assert.Equal(true, pages["No camera"].UseHiddenLayers);
            Assert.Null(pages["No camera"].Eye);
            Assert.Equal(0x000u, pages["Plain"].CaptureFlags);
            Assert.Equal(false, pages["Plain"].UseCamera);
            Assert.Equal(false, pages["Plain"].UseHiddenLayers);

            Assert.Equal(new uint?[] { 0x001u, 0x020u, 0x000u }, SkpFile.BuildScene(path).Pages.Select(p => p.CaptureFlags).ToArray());
            Assert.Equal(new uint?[] { 0x001u, 0x020u, 0x000u }, SkpFile.BuildInstancedScene(path).Pages.Select(p => p.CaptureFlags).ToArray());
        }

        [Theory]
        [InlineData("sdk_saved_sections_2019.skp")]
        [InlineData("sdk_saved_sections_2020.skp")]
        public void AllOrNothing(string name)
        {
            var pages = PagesByName(LocalFixturePath(name));
            var everything = pages["Everything"];
            var plain = pages["Plain"];
            Assert.Equal(0x1FFu, everything.CaptureFlags);
            Assert.Equal(0x000u, plain.CaptureFlags);
            foreach (var (_, getter) in AllUseProps)
            {
                Assert.Equal(true, getter(everything));
                Assert.Equal(false, getter(plain));
            }
        }

        [Fact]
        public void ApplicationSavedScene()
        {
            var page = Assert.Single(PagesByName(LocalFixturePath("gondola_v20.skp")).Values);
            Assert.Equal(0x1FFu, page.CaptureFlags);
            Assert.Equal(true, page.UseHiddenGeometry);
        }

        [Fact]
        public void OlderCombinedBitCoversBoth()
        {
            var old = new Page { CaptureFlags = 0x011u };
            Assert.Equal(true, old.UseHiddenGeometry);
            Assert.Equal(true, old.UseHiddenObjects);

            var split = new Page { CaptureFlags = 0x111u };
            Assert.Equal(true, split.UseHiddenObjects);
            Assert.Equal(false, split.UseHiddenGeometry);
        }

        [Fact]
        public void NoFlagsMeansUnknown()
        {
            var page = new Page();
            Assert.Null(page.CaptureFlags);
            foreach (var (_, getter) in AllUseProps)
            {
                Assert.Null(getter(page));
            }
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

            var expected = new Dictionary<string, (uint Flags, HashSet<string> TrueProps)>
            {
                ["Camera"] = (0x001u, new HashSet<string> { "use_camera" }),
                ["RenderingOptions"] = (0x002u, new HashSet<string> { "use_rendering_options" }),
                ["ShadowInfo"] = (0x004u, new HashSet<string> { "use_shadow_info" }),
                ["Axes"] = (0x008u, new HashSet<string> { "use_axes" }),
                ["Hidden"] = (0x190u, new HashSet<string> { "use_hidden_geometry", "use_hidden_objects" }),
                ["HiddenGeometry"] = (0x090u, new HashSet<string> { "use_hidden_geometry" }),
                ["HiddenObjects"] = (0x110u, new HashSet<string> { "use_hidden_objects" }),
                ["HiddenLayers"] = (0x020u, new HashSet<string> { "use_hidden_layers" }),
                ["SectionPlanes"] = (0x040u, new HashSet<string> { "use_section_planes" }),
                ["ShadowsCamera"] = (0x005u, new HashSet<string> { "use_shadow_info", "use_camera" }),
                ["AxesCamera"] = (0x009u, new HashSet<string> { "use_axes", "use_camera" }),
            };
            foreach (var kv in expected)
            {
                var p = byName[kv.Key];
                Assert.Equal(kv.Value.Flags, p.CaptureFlags);
                foreach (var (propName, getter) in AllUseProps)
                {
                    Assert.Equal(kv.Value.TrueProps.Contains(propName), getter(p));
                }
            }

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
