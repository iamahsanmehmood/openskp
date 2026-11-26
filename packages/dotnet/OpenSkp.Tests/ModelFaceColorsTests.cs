using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    public class ModelFaceColorsTests
    {
        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        private static readonly ((int R, int G, int B) Front, (int R, int G, int B) Back) Written =
            ((11, 122, 233), (201, 102, 3));

        private static readonly ((int R, int G, int B) Front, (int R, int G, int B) Back) AppDefault =
            ((255, 255, 255), (164, 178, 187));

        private static readonly ((int R, int G, int B) Front, (int R, int G, int B) Back) SdkDefault =
            ((225, 225, 200), (128, 128, 200));

        public static IEnumerable<object[]> Cases()
        {
            foreach (var v in new[] { "v4", "v5", "v6", "2016", "2017", "2018", "2019", "2020", "2021" })
            {
                yield return new object[] { $"face_colors_{v}.skp", Written.Front, Written.Back };
            }
            yield return new object[] { "capilla_quiroz_v17.skp", AppDefault.Front, AppDefault.Back };
            yield return new object[] { "gondola_v20.skp", AppDefault.Front, AppDefault.Back };
            yield return new object[] { "zero_material_custom_layer_v18.skp", AppDefault.Front, AppDefault.Back };
            yield return new object[] { "SU_File.skp", AppDefault.Front, AppDefault.Back };
            yield return new object[] { "nesting_v6.skp", SdkDefault.Front, SdkDefault.Back };
            yield return new object[] { "nesting_2013.skp", SdkDefault.Front, SdkDefault.Back };
            yield return new object[] { "nesting_2021.skp", SdkDefault.Front, SdkDefault.Back };
            yield return new object[] { "legacy_v4_synthetic.skp", SdkDefault.Front, SdkDefault.Back };
            yield return new object[] { "legacy_v5_synthetic.skp", SdkDefault.Front, SdkDefault.Back };
            yield return new object[] { "legacy_v2019_synthetic.skp", SdkDefault.Front, SdkDefault.Back };
        }

        [Theory]
        [MemberData(nameof(Cases))]
        public void FaceColorsAreWhatSketchUpReports(
            string name,
            (int R, int G, int B) expectedFront,
            (int R, int G, int B) expectedBack)
        {
            var model = SkpFile.Open(FixturePath(name));
            Assert.Equal(expectedFront, model.FaceFrontColor);
            Assert.Equal(expectedBack, model.FaceBackColor);
        }

        [Fact]
        public void SketchUp3GivesNull()
        {
            var model = SkpFile.Open(FixturePath("legacy_v3_synthetic.skp"));
            Assert.Null(model.FaceFrontColor);
            Assert.Null(model.FaceBackColor);
        }

        [Fact]
        public void ReadsCurrentColoursNotSavedStyle()
        {
            var model = SkpFile.Open(FixturePath("face_colors_2021.skp"));
            var stylePairs = model.Styles.Select(s => (s.FrontColor, s.BackColor)).ToHashSet();
            Assert.Contains((SdkDefault.Front, SdkDefault.Back), stylePairs);
            Assert.Contains((Written.Front, Written.Back), stylePairs);
            Assert.Equal(Written.Front, model.FaceFrontColor);
            Assert.Equal(Written.Back, model.FaceBackColor);
        }

        public static IEnumerable<object[]> ColorByTagCases()
        {
            yield return new object[] { "color_by_tag_v4.skp", true };
            yield return new object[] { "color_by_tag_v5.skp", true };
            yield return new object[] { "color_by_tag_v6.skp", true };
            yield return new object[] { "color_by_tag_2017.skp", true };
            yield return new object[] { "color_by_tag_2019.skp", true };
            yield return new object[] { "color_by_tag_2020.skp", true };
            yield return new object[] { "color_by_tag_2020_off.skp", false };
            yield return new object[] { "color_by_tag_2021.skp", true };
            yield return new object[] { "color_by_tag_2021_style.skp", true };
            yield return new object[] { "color_by_tag_2021_off.skp", false };
            yield return new object[] { "legacy_v3_synthetic.skp", false };
            foreach (var row in Cases())
            {
                yield return new object[] { (string)row[0], false };
            }
        }

        [Theory]
        [MemberData(nameof(ColorByTagCases))]
        public void ColorByTagMatchesSketchUp(string name, bool expected)
        {
            var model = SkpFile.Open(FixturePath(name));
            Assert.Equal(expected, model.ColorByTag);
        }

        [Fact]
        public void ColorByTagRoundTripsClassicWriter()
        {
            var quad = new[]
            {
                (0.0, 0.0, 0.0),
                (10.0, 0.0, 0.0),
                (10.0, 10.0, 0.0),
                (0.0, 10.0, 0.0),
            };
            foreach (var (setting, expected) in new (double?, bool)[] { (null, false), (0.0, false), (1.0, true) })
            {
                var builder = SkpCreate.NewFile();
                if (setting.HasValue)
                {
                    builder.SetDocumentOptions(new DocumentOptions
                    {
                        StyleItems = new Dictionary<int, double> { [7011] = setting.Value },
                    });
                }
                builder.AddFace(quad);
                var model = SkpFile.Parse(builder.ToBytes());
                Assert.Equal(expected, model.ColorByTag);
            }
        }

        [Theory]
        [InlineData("color_by_tag_2020.skp", "color_by_tag_2020_off.skp")]
        [InlineData("color_by_tag_2021.skp", "color_by_tag_2021_off.skp")]
        public void ColorByTagDoesNotChangeSceneOutput(string onName, string offName)
        {
            byte[] bytesOn = File.ReadAllBytes(FixturePath(onName));
            byte[] bytesOff = File.ReadAllBytes(FixturePath(offName));
            Assert.True(SkpFile.Parse(bytesOn).ColorByTag);
            Assert.False(SkpFile.Parse(bytesOff).ColorByTag);

            static List<(int, int, int)> ColorsOf(IReadOnlyList<object> mats) =>
                mats.Select(m =>
                {
                    var d = (IDictionary<string, object>)m;
                    var pbr = (IDictionary<string, object>)d["pbrMetallicRoughness"];
                    var f = (double[])pbr["baseColorFactor"];
                    return (
                        (int)Math.Round(f[0] * 255.0),
                        (int)Math.Round(f[1] * 255.0),
                        (int)Math.Round(f[2] * 255.0)
                    );
                }).ToList();

            var sceneOn = SkpFile.BuildScene(bytesOn);
            var sceneOff = SkpFile.BuildScene(bytesOff);
            Assert.Equal(ColorsOf(sceneOff.GltfMaterials), ColorsOf(sceneOn.GltfMaterials));

            var instOn = SkpFile.BuildInstancedScene(bytesOn);
            var instOff = SkpFile.BuildInstancedScene(bytesOff);
            Assert.Equal(ColorsOf(instOff.GltfMaterials), ColorsOf(instOn.GltfMaterials));
        }
    }
}
