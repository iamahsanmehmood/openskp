using System;
using System.IO;
using System.Linq;
using Xunit;

namespace OpenSkp.Tests
{
    public class ScaleMaskTests
    {
        private static string CppFixturePath(string name)
        {
            string? dir = AppContext.BaseDirectory;
            while (dir != null)
            {
                string candidate = Path.Combine(dir, "packages", "cpp", "tests", "fixtures");
                if (Directory.Exists(candidate))
                {
                    return Path.Combine(candidate, name);
                }
                dir = Directory.GetParent(dir)?.FullName;
            }
            throw new DirectoryNotFoundException("Could not locate packages/cpp/tests/fixtures");
        }

        [Fact]
        public void NativeScaleMaskRoundTrip()
        {
            uint[] masks = { 0u, 1u, 2u, 4u, 8u, 16u, 32u, 64u, 127u, 0x12345678u };
            foreach (uint mask in masks)
            {
                var builder = SkpCreate.NewFile();
                builder.SetDocumentOptions(new DocumentOptions { NoScaleMask = mask });
                var def = builder.AddComponentDefinition(
                    "MaskProbe",
                    alwaysFacesCamera: true,
                    shadowsFaceSun: true,
                    noScaleMask: mask);
                using (def)
                {
                    def.AddFace(new (double, double, double)[] { (0, 0, 0), (10, 0, 0), (0, 10, 0) });
                }
                builder.AddInstance(def);
                var model = SkpFile.Parse(builder.ToBytes());
                Assert.Single(model.Definitions);
                var actual = model.Definitions.Values.First();
                Assert.Equal(mask, actual.NoScaleMask);
                Assert.Equal(mask, model.Root.NoScaleMask);
                Assert.True(actual.AlwaysFacesCamera);
                Assert.True(actual.ShadowsFaceSun);
                Assert.False(actual.IsGroup);
            }
        }

        [Fact]
        public void ReadsSdkScaleMaskFixtures()
        {
            foreach (string name in new[] { "behavior_mask_2017.skp", "behavior_mask_2021.skp" })
            {
                var model = SkpFile.Open(CppFixturePath(name));
                Assert.Single(model.Definitions);
                Assert.Equal(127u, model.Definitions.Values.First().NoScaleMask);
            }
        }

        [Fact]
        public void ReadsSdkRootScaleMaskFixtures()
        {
            foreach (string name in new[]
            {
                "root_behavior_mask_2013.skp",
                "root_behavior_mask_2017.skp",
                "root_behavior_mask_2021.skp",
            })
            {
                var model = SkpFile.Open(CppFixturePath(name));
                uint expected = name.Contains("2021") ? 127u : 2u;
                Assert.Equal(expected, model.Root.NoScaleMask);
                Assert.Single(model.Definitions);
                Assert.Equal(0u, model.Definitions.Values.First().NoScaleMask);
            }
        }

        [Fact]
        public void AppSavedFilesHaveNoScaleMask()
        {
            foreach (string name in new[]
            {
                "capilla_quiroz_v17.skp",
                "gondola_v20.skp",
                "zero_material_custom_layer_v18.skp",
            })
            {
                var model = SkpFile.Open(CppFixturePath(name));
                Assert.Equal(0u, model.Root.NoScaleMask);
                foreach (var defn in model.Definitions.Values)
                {
                    Assert.Equal(0u, defn.NoScaleMask);
                }
            }
        }

        [Fact]
        public void ScaleMaskSurvivesEditAndCodeGeneration()
        {
            var builder = SkpCreate.NewFile();
            builder.SetDocumentOptions(new DocumentOptions { NoScaleMask = 16u });
            var def = builder.AddComponentDefinition("Masked", noScaleMask: 64u);
            using (def)
            {
                def.AddFace(new (double, double, double)[] { (0, 0, 0), (10, 0, 0), (0, 10, 0) });
            }
            builder.AddInstance(def);

            string tmpPath = Path.Combine(Path.GetTempPath(), $"openskp_scale_mask_{Guid.NewGuid():N}.skp");
            try
            {
                builder.Save(tmpPath);
                var edited = SkpEdit.OpenExisting(tmpPath);
                var model = SkpFile.Parse(edited.Builder.ToBytes());
                Assert.Single(model.Definitions);
                Assert.Equal(64u, model.Definitions.Values.First().NoScaleMask);
                Assert.Equal(16u, model.Root.NoScaleMask);

                string code = Codegen.ToCSharpCode(model);
                Assert.Contains("noScaleMask: 64u", code);
                Assert.Contains("NoScaleMask = 16u", code);
            }
            finally
            {
                if (File.Exists(tmpPath))
                {
                    File.Delete(tmpPath);
                }
            }
        }
    }
}
