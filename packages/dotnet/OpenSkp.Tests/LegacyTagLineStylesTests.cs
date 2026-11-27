using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Xunit;

namespace OpenSkp.Tests
{
    /// <summary>
    /// Tags with a line style in SketchUp 2019 and 2020 files (issue #514).
    ///
    /// Since SketchUp 2019 a tag can carry a line style ("Dash", "Dot", ...).
    /// A tag that has one points at a CCustomLineStyle object right after its
    /// own fields (both plain-colour and textured CLayer records): nothing for
    /// a tag without a line style, the style written in full the first time a
    /// tag uses it (a new-class record, then the object), a class-ref for a
    /// second style, and a back-ref for a style that an earlier tag already
    /// wrote. The style is an object in the walk like any other and takes a
    /// store-map slot.
    /// </summary>
    public class LegacyTagLineStylesTests
    {
        private sealed record ExpectedModel(
            bool FromCppFixtures,
            string[] Tags,
            string[] LineStyles,
            (string Name, (int R, int G, int B, int A) Color)[] Materials);

        private static readonly Dictionary<string, ExpectedModel> Models = new()
        {
            ["sdk_saved_tag_line_styles_2020.skp"] = new ExpectedModel(
                false,
                new[] { "Tag 0 ", "Tag 1 Dash", "Tag 2 ", "Tag 3 Dash", "Tag 4 Dot", "Tag 5 Dash" },
                new[] { "", "", "Dash", "", "Dash", "Dot", "Dash" },
                Array.Empty<(string, (int, int, int, int))>()),
            ["sdk_saved_tag_line_styles_2019.skp"] = new ExpectedModel(
                false,
                new[] { "Tag 0 ", "Tag 1 Dash", "Tag 2 ", "Tag 3 Dash", "Tag 4 Dot", "Tag 5 Dash" },
                new[] { "", "", "Dash", "", "Dash", "Dot", "Dash" },
                Array.Empty<(string, (int, int, int, int))>()),
            ["sdk_saved_tag_line_styles_materials_2020.skp"] = new ExpectedModel(
                false,
                new[] { "Tag 0 Dash", "Tag 1 ", "Tag 2 Dot", "Tag 3 Dash", "Tag 4 Dot" },
                new[] { "", "Dash", "", "Dot", "Dash", "Dot" },
                new[]
                {
                    ("Paint 0", (10, 20, 30, 255)),
                    ("Paint 1", (50, 20, 30, 255)),
                    ("Paint 2", (90, 20, 30, 255)),
                }),
            ["sdk_saved_textured_tag_line_styles_2019.skp"] = new ExpectedModel(
                true,
                new[] { "Tag 0 Tex Dash", "Tag 1 Tex ", "Tag 2 Tex Dot", "Tag 3 Tex Dash", "Tag 4 Plain Dot" },
                new[] { "", "Dash", "", "Dot", "Dash", "Dot" },
                Array.Empty<(string, (int, int, int, int))>()),
            ["sdk_saved_textured_tag_line_styles_2020.skp"] = new ExpectedModel(
                true,
                new[] { "Tag 0 Tex Dash", "Tag 1 Tex ", "Tag 2 Tex Dot", "Tag 3 Tex Dash", "Tag 4 Plain Dot" },
                new[] { "", "Dash", "", "Dot", "Dash", "Dot" },
                Array.Empty<(string, (int, int, int, int))>()),
        };

        private static string ResolveFixturePath(string name)
        {
            if (!Models[name].FromCppFixtures)
            {
                return Path.Combine(AppContext.BaseDirectory, "fixtures", name);
            }
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

        public static IEnumerable<object[]> FixtureNames() =>
            Models.Keys.Select(name => new object[] { name });

        [Theory]
        [MemberData(nameof(FixtureNames))]
        public void ModelWhoseTagsHaveLineStylesOpens(string name)
        {
            var expected = Models[name];
            var model = SkpFile.Open(ResolveFixturePath(name));
            var expectedLayers = new[] { "Layer0" }.Concat(expected.Tags).ToArray();
            Assert.Equal(expectedLayers, model.Layers.Select(l => l.Name).ToArray());
            Assert.Equal(expected.LineStyles, model.Layers.Select(l => l.LineStyle).ToArray());
            Assert.Equal(expected.Tags, model.Root.Faces.Values.Select(f => f.Layer).ToArray());
            Assert.Equal(
                expected.Materials,
                model.Materials.Select(m => (m.Name, m.Color)).ToArray());
        }

        [Theory]
        [MemberData(nameof(FixtureNames))]
        public void EachFaceStaysOnItsOwnTag(string name)
        {
            var expected = Models[name];
            var walked = Legacy.Walk(File.ReadAllBytes(ResolveFixturePath(name)));
            var tagOfSlot = walked.Layers.ToDictionary(
                entry => entry.Slot,
                entry => ((LayerRec)entry.Value!).Name);
            var faceTags = walked.Root
                .Where(entry => entry.Name == "CFace")
                .Select(entry => tagOfSlot[((FaceRec)entry.Value!).Db.Layer])
                .ToArray();
            Assert.Equal(expected.Tags, faceTags);
        }

        [Fact]
        public void Vff2025TagLineStylesMatchSketchUpSdk()
        {
            var path = Path.Combine(AppContext.BaseDirectory, "fixtures", "sdk_saved_tag_line_styles_2025.skp");
            var model = SkpFile.Open(path);
            var actual = model.Layers.Select(l => (l.Name, l.Hidden, l.LineStyle)).ToArray();
            var expected = new (string Name, bool Hidden, string LineStyle)[]
            {
                ("Layer0", false, ""),
                ("Tag 0 Plain", false, ""),
                ("Tag 1 Solid Basic", false, "Solid Basic"),
                ("Tag 2 Dash", false, "Dash"),
                ("Tag 3 Dot", false, "Dot"),
                ("Tag 4 Dash dot", false, "Dash dot"),
                ("Tag 5 Shared Dash", false, "Dash"),
                ("Tag 6 Hidden Long-dash double-dash", true, "Long-dash double-dash"),
                ("Tag 7 Custom Wide Dash", false, "Custom Wide Dash"),
            };
            Assert.Equal(expected, actual);
            Assert.Equal(
                model.Layers.Skip(1).Select(l => l.Name).ToArray(),
                model.Root.Faces.Values.Select(f => f.Layer).ToArray());
        }
    }
}
