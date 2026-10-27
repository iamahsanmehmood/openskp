using System;
using System.IO;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>
    /// <c>Definition.IsGroup</c>: whether SketchUp made a definition for a group
    /// or a component (openskp#446).
    ///
    /// Fixtures: nesting_&lt;version&gt;.skp - the group "Outer" holds an instance
    /// of the component "Shelf", which holds the group "Inner", a face at each
    /// level. Built and saved through the installed SketchUp library as
    /// SketchUp 6, 2013, 2017, 2020 and 2021 (the last is the VFF container,
    /// the rest the classic one). The library reports two group definitions and
    /// one component definition for each (SUModelGetNumGroupDefinitions /
    /// SUModelGetNumComponentDefinitions). No private content.
    /// </summary>
    public class DefinitionIsGroupTests
    {
        public static readonly object[][] Names =
        {
            new object[] { "nesting_v6.skp" },
            new object[] { "nesting_2013.skp" },
            new object[] { "nesting_2017.skp" },
            new object[] { "nesting_2020.skp" },
            new object[] { "nesting_2021.skp" },
        };

        private static SkpModel Load(string name) => SkpFile.Open(
            Path.Combine(AppContext.BaseDirectory, "fixtures", name));

        [Theory, MemberData(nameof(Names))]
        public void GroupsAndTheComponentAreToldApart(string name)
        {
            var all = Load(name).Definitions.Values.ToArray();
            Assert.Equal(2, all.Count(d => d.IsGroup));
            Assert.Equal(1, all.Count(d => !d.IsGroup));
        }

        [Theory, MemberData(nameof(Names))]
        public void TheFlagFollowsTheNesting(string name)
        {
            var model = Load(name);
            Definition Of(Instance i) => model.Definitions[i.RefIdx!.Value];

            var outer = Assert.Single(model.Root.Instances);
            Assert.Equal("Outer", outer.Name);
            Assert.True(Of(outer).IsGroup);

            var shelf = Assert.Single(Of(outer).Instances);
            Assert.Equal("Shelf", Of(shelf).Name);
            Assert.False(Of(shelf).IsGroup);

            var inner = Assert.Single(Of(shelf).Instances);
            Assert.Equal("Inner", inner.Name);
            Assert.True(Of(inner).IsGroup);
        }

        [Theory, MemberData(nameof(Names))]
        public void ADefinitionPlacedOnceIsNotTakenForAGroup(string name)
        {
            // "Shelf" is placed exactly once, which is what the old guess got wrong
            var model = Load(name);
            var placements = model.Definitions.Values.Append(model.Root)
                .SelectMany(d => d.Instances)
                .GroupBy(i => i.RefIdx!.Value)
                .ToDictionary(g => g.Key, g => g.Count());
            var shelf = model.Definitions.First(kv => kv.Value.Name == "Shelf");
            Assert.Equal(1, placements[shelf.Key]);
            Assert.False(shelf.Value.IsGroup);
        }
    }
}
