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
    /// own fields: nothing for a tag without a line style, the style written
    /// in full the first time a tag uses it (a new-class record, then the
    /// object), a class-ref for a second style, and a back-ref for a style
    /// that an earlier tag already wrote. The style is an object in the walk
    /// like any other and takes a store-map slot.
    /// </summary>
    public class LegacyTagLineStylesTests
    {
        private static readonly Dictionary<string, string[]> Models = new Dictionary<string, string[]>
        {
            ["sdk_saved_tag_line_styles_2020.skp"] = new[]
            {
                "Tag 0 ", "Tag 1 Dash", "Tag 2 ", "Tag 3 Dash", "Tag 4 Dot", "Tag 5 Dash",
            },
            ["sdk_saved_tag_line_styles_2019.skp"] = new[]
            {
                "Tag 0 ", "Tag 1 Dash", "Tag 2 ", "Tag 3 Dash", "Tag 4 Dot", "Tag 5 Dash",
            },
            ["sdk_saved_tag_line_styles_materials_2020.skp"] = new[]
            {
                "Tag 0 Dash", "Tag 1 ", "Tag 2 Dot", "Tag 3 Dash", "Tag 4 Dot",
            },
        };

        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        public static IEnumerable<object[]> FixtureNames() =>
            Models.Keys.Select(name => new object[] { name });

        [Theory]
        [MemberData(nameof(FixtureNames))]
        public void ModelWhoseTagsHaveLineStylesOpens(string name)
        {
            var model = SkpFile.Open(FixturePath(name));
            var expectedLayers = new[] { "Layer0" }.Concat(Models[name]).ToArray();
            Assert.Equal(expectedLayers, model.Layers.Select(l => l.Name).ToArray());
            Assert.Equal(Models[name].Length, model.Root.Faces.Count);
        }

        [Theory]
        [MemberData(nameof(FixtureNames))]
        public void EachFaceStaysOnItsOwnTag(string name)
        {
            var walked = Legacy.Walk(File.ReadAllBytes(FixturePath(name)));
            var tagOfSlot = walked.Layers.ToDictionary(
                entry => entry.Slot,
                entry => ((LayerRec)entry.Value!).Name);
            var faceTags = walked.Root
                .Where(entry => entry.Name == "CFace")
                .Select(entry => tagOfSlot[((FaceRec)entry.Value!).Db.Layer])
                .ToArray();
            Assert.Equal(Models[name], faceTags);
        }
    }
}
