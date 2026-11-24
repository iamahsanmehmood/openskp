using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Xunit;

namespace OpenSkp.Tests
{
    public class SceneHiddenEntitiesTests
    {
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

        private static HashSet<long> HiddenSet(Page page) =>
            new HashSet<long>(page.HiddenEntityIds);

        private static Dictionary<string, long> PlacementIds(SkpModel model)
        {
            var ids = new Dictionary<string, long>();
            void Add(Definition defn)
            {
                foreach (var inst in defn.Instances)
                {
                    if (inst.Id.HasValue)
                    {
                        ids[inst.Name] = inst.Id.Value;
                    }
                }
            }
            Add(model.Root);
            foreach (var defn in model.Definitions.Values)
            {
                Add(defn);
            }
            return ids;
        }

        [Theory]
        [InlineData("scene_hidden_entities_2017.skp")]
        [InlineData("scene_hidden_entities_2019.skp")]
        [InlineData("scene_hidden_entities_2020.skp")]
        [InlineData("scene_hidden_entities_2024.skp")]
        public void EachSceneListsWhatItHides(string name)
        {
            var model = SkpFile.Open(CppFixturePath(name));
            Assert.Equal(6, model.Pages.Count);
            Assert.Equal("All", model.Pages[0].Name);
            Assert.Empty(model.Pages[0].HiddenEntityIds);
            Assert.Equal("No B, no crate", model.Pages[1].Name);
            Assert.Equal(new HashSet<long> { 71L, 73L }, HiddenSet(model.Pages[1]));
            Assert.Equal("No A, no crate", model.Pages[2].Name);
            Assert.Equal(new HashSet<long> { 70L, 73L }, HiddenSet(model.Pages[2]));
            Assert.Equal("Edge", model.Pages[4].Name);
            Assert.Equal(new HashSet<long> { 17L }, HiddenSet(model.Pages[4]));
            Assert.Equal("Not saved", model.Pages[5].Name);
            Assert.Empty(model.Pages[5].HiddenEntityIds);
        }

        [Theory]
        [InlineData("scene_hidden_entities_2017.skp")]
        [InlineData("scene_hidden_entities_2019.skp")]
        [InlineData("scene_hidden_entities_2020.skp")]
        [InlineData("scene_hidden_entities_2024.skp")]
        public void IdsResolveThroughPlacements(string name)
        {
            var model = SkpFile.Open(CppFixturePath(name));
            Assert.Equal(6, model.Pages.Count);
            var ids = PlacementIds(model);
            Assert.True(ids.ContainsKey("Part A"));
            Assert.True(ids.ContainsKey("Part B"));
            Assert.True(ids.ContainsKey("Crate"));
            Assert.Equal(
                new HashSet<long> { ids["Part B"], ids["Crate"] },
                HiddenSet(model.Pages[1]));
            Assert.Equal(
                new HashSet<long> { ids["Part A"], ids["Crate"] },
                HiddenSet(model.Pages[2]));
        }

        [Theory]
        [InlineData("scene_hidden_entities_2017.skp")]
        [InlineData("scene_hidden_entities_2019.skp")]
        [InlineData("scene_hidden_entities_2020.skp")]
        [InlineData("scene_hidden_entities_2024.skp")]
        public void HiddenEdgeIsBoxEdge(string name)
        {
            var model = SkpFile.Open(CppFixturePath(name));
            Assert.Contains(model.Root.Edges.Values, e => e.PersistentId == 17L);
            Assert.Contains(model.Root.Faces.Values, f => f.PersistentId > 0L);
        }

        [Theory]
        [InlineData("scene_hidden_entities_2020.skp")]
        [InlineData("scene_hidden_entities_2024.skp")]
        public void A2020OrNewerFileKeepsNestedPlacement(string name)
        {
            var model = SkpFile.Open(CppFixturePath(name));
            Assert.Equal(6, model.Pages.Count);
            Assert.Equal("No C", model.Pages[3].Name);
            Assert.Equal(new HashSet<long> { 100L }, HiddenSet(model.Pages[3]));
            Assert.Equal(100L, PlacementIds(model)["Part C"]);
        }

        [Fact]
        public void A2017FileKeepsNoneLikeSketchUp()
        {
            var model = SkpFile.Open(CppFixturePath("scene_hidden_entities_2017.skp"));
            Assert.Equal(6, model.Pages.Count);
            Assert.Empty(model.Pages[3].HiddenEntityIds);
        }

        [Fact]
        public void GondolaSceneListsTwelveHiddenEdges()
        {
            var model = SkpFile.Open(CppFixturePath("gondola_v20.skp"));
            Assert.Single(model.Pages);
            Assert.Equal(12, model.Pages[0].HiddenEntityIds.Count);
        }
    }
}
