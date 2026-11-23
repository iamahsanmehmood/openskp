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
    }
}
