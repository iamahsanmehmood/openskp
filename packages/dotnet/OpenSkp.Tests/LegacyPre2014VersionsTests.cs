using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>
    /// Regression tests for the pre-2014 legacy layouts found while chasing
    /// openskp#284 - ports of the Python reader's fixes (#310 instance GUID
    /// gate, #385 CAttributeNamed trailer, #407 SketchUp 3 layout) to this
    /// reader (openskp#410).
    ///
    /// Fixtures are one small synthetic model (4 component definitions
    /// InnerFrame / Frame / Panel / Post; InnerFrame placed as a nested group
    /// inside Panel; 10 root instances; a custom "Roof" layer; Frame's face
    /// painted flat red) built with the OpenSKP writer and saved down through
    /// a real SketchUp version-downgrade export - to v3, v6, v7, and left at
    /// 2014. SketchUp's default template rides along as extra
    /// materials/layers. No private content.
    ///
    /// Each of these used to either throw or - worse - "succeed" with most of
    /// the model silently missing, so the tests assert parsed structure, not
    /// just the absence of an exception.
    /// </summary>
    public class LegacyPre2014VersionsTests
    {
        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        private static SkpModel Parse(string name) => SkpFile.Open(FixturePath(name));

        private static Dictionary<string, Definition> ByName(SkpModel m) =>
            m.Definitions.Values.ToDictionary(d => d.Name);

        private static List<string> InstanceNames(SkpModel m) =>
            m.Root.Instances.Select(i => i.Name).ToList();

        private static readonly string[] SiblingNames =
            { "", "P-1", "P-2", "P-3", "P-4", "P-5", "P-6", "P-7", "P-8", "P-9" };

        // ── #310: instance GUID gated on file version >= 14 ────────────

        [Fact]
        public void V7ParsesAndPlacesAllRootInstances()
        {
            // Was: class-ref to non-class slot, or silently truncated to 1.
            var model = Parse("legacy_v7_synthetic.skp");
            Assert.Equal("{7.0.1}", model.Version);
            Assert.Equal(SiblingNames, InstanceNames(model));
        }

        [Fact]
        public void V7AndV2014ParseToTheSameStructure()
        {
            var v7 = Parse("legacy_v7_synthetic.skp");
            var v14 = Parse("legacy_v2014_synthetic.skp");
            Assert.Equal(3, v7.Definitions.Count);
            Assert.Equal(3, v14.Definitions.Count);
            Assert.Equal(InstanceNames(v7), InstanceNames(v14));
            var d7 = ByName(v7);
            var d14 = ByName(v14);
            Assert.Equal(new[] { "Frame", "Panel", "Post" }, d7.Keys.OrderBy(k => k));
            Assert.Equal(new[] { "Frame", "Panel", "Post" }, d14.Keys.OrderBy(k => k));
            foreach (var kv in d7)
            {
                Assert.Equal(kv.Value.Faces.Count, d14[kv.Key].Faces.Count);
                Assert.Equal(
                    kv.Value.Instances.Select(i => i.Name),
                    d14[kv.Key].Instances.Select(i => i.Name));
            }
        }

        [Fact]
        public void V7KeepsTheNestedGroupInsidePanel()
        {
            var panel = ByName(Parse("legacy_v7_synthetic.skp"))["Panel"];
            Assert.Equal(new[] { "InnerFrame" }, panel.Instances.Select(i => i.Name));
        }

        [Fact]
        public void V2014StillReadsItsTrailingInstanceGuids()
        {
            var model = Parse("legacy_v2014_synthetic.skp");
            Assert.Equal("{14.0.1}", model.Version);
            Assert.Equal(10, model.Root.Instances.Count);
        }

        // ── #385: SketchUp 6 CAttributeNamed has no trailing field ─────

        [Fact]
        public void V6Parses()
        {
            // Was: back-ref to unwalked slot 256.
            var model = Parse("legacy_v6_synthetic.skp");
            Assert.Equal("{6.0.1}", model.Version);
            Assert.Equal(SiblingNames, InstanceNames(model));
            Assert.Equal(
                new[] { "Frame", "InnerFrame", "Panel", "Post" },
                ByName(model).Keys.OrderBy(k => k, StringComparer.Ordinal));
            Assert.Equal(new[] { "InnerFrame" }, ByName(model)["Panel"].Instances.Select(i => i.Name));
        }

        // ── #407: SketchUp 3 layout ────────────────────────────────────

        [Fact]
        public void V3ParsesEveryDefinitionWithItsGeometry()
        {
            // Was: expected a string record.
            var model = Parse("legacy_v3_synthetic.skp");
            Assert.Equal("{3.0.1}", model.Version);
            var defs = ByName(model);
            Assert.Equal(
                new[] { "Frame", "InnerFrame", "Panel", "Post" },
                defs.Keys.OrderBy(k => k, StringComparer.Ordinal));
            foreach (var d in defs.Values)
            {
                Assert.Single(d.Faces);
                Assert.Equal(4, d.Edges.Count);
                Assert.Equal(4, d.Vertices.Count);
            }
            var frame = defs["Frame"];
            Assert.Equal(new[] { 0.0, 20.0 }, frame.Vertices.Values.Select(v => v.X).Distinct().OrderBy(x => x));
            Assert.Equal(new[] { 0.0, 20.0 }, frame.Vertices.Values.Select(v => v.Y).Distinct().OrderBy(y => y));
        }

        [Fact]
        public void V3PlacesEveryRootInstance()
        {
            var model = Parse("legacy_v3_synthetic.skp");
            Assert.Equal(10, model.Root.Instances.Count);
            var expected = new[] { "Frame", "Panel", "Post", "Frame", "Panel", "Post", "Frame", "Panel", "Post", "Frame" };
            var actual = model.Root.Instances.Select(i => model.Definitions[i.RefIdx!.Value].Name);
            Assert.Equal(expected, actual);
        }

        [Fact]
        public void V3PlacesTheNestedGroupInsidePanel()
        {
            var model = Parse("legacy_v3_synthetic.skp");
            var panel = ByName(model)["Panel"];
            Assert.Equal(
                new[] { "InnerFrame" },
                panel.Instances.Select(i => model.Definitions[i.RefIdx!.Value].Name));
        }

        [Fact]
        public void V3FaceMaterialComesFromTheFacesOwnPointer()
        {
            // SketchUp 3's face opens with its FRONT MATERIAL pointer, not an
            // attribute container: Frame's face was painted red, the others not.
            var model = Parse("legacy_v3_synthetic.skp");
            var defs = ByName(model);
            var painted = defs["Frame"].Faces.Values.First();
            Assert.NotNull(painted.MaterialId);
            var red = model.MaterialsById[painted.MaterialId!.Value];
            Assert.Equal("Red", red.Name);
            Assert.Equal((255, 0, 0), (red.Color.R, red.Color.G, red.Color.B));
            foreach (var name in new[] { "Panel", "Post", "InnerFrame" })
            {
                Assert.Null(defs[name].Faces.Values.First().MaterialId);
            }
            foreach (var d in model.Definitions.Values)
            {
                foreach (var f in d.Faces.Values) Assert.False(f.Hidden);
            }
        }

        [Fact]
        public void V3KeepsTheCustomLayer()
        {
            var names = Parse("legacy_v3_synthetic.skp").Layers.Select(l => l.Name).ToList();
            Assert.Contains("Layer0", names);
            Assert.Contains("Roof", names);
        }

        [Fact]
        public void V3ReadsEmbeddedTemplateTexturesAsInlineJpegs()
        {
            var model = Parse("legacy_v3_synthetic.skp");
            Assert.Equal(35, model.Materials.Count);
            var textured = model.Materials.Where(m => m.Texture != null).ToList();
            Assert.Equal(10, textured.Count);
            foreach (var m in textured)
            {
                var data = m.Texture!.Data;
                Assert.NotNull(data);
                Assert.Equal(new byte[] { 0xFF, 0xD8, 0xFF }, data!.Take(3));
                Assert.Equal(new byte[] { 0xFF, 0xD9 }, data.Skip(data.Length - 2));
            }
        }

        [Fact]
        public void V3MatchesTheV6FileBuiltFromTheSameSource()
        {
            var v3 = Parse("legacy_v3_synthetic.skp");
            var v6 = Parse("legacy_v6_synthetic.skp");
            var d3 = ByName(v3);
            var d6 = ByName(v6);
            Assert.Equal(d3.Keys.OrderBy(k => k, StringComparer.Ordinal), d6.Keys.OrderBy(k => k, StringComparer.Ordinal));
            foreach (var kv in d3)
            {
                Assert.Equal(d6[kv.Key].Faces.Count, kv.Value.Faces.Count);
                Assert.Equal(d6[kv.Key].Edges.Count, kv.Value.Edges.Count);
                Assert.Equal(d6[kv.Key].Vertices.Count, kv.Value.Vertices.Count);
            }
            Assert.Equal(v6.Root.Instances.Count, v3.Root.Instances.Count);
            for (int i = 0; i < v3.Root.Instances.Count; i++)
            {
                var a = v3.Root.Instances[i];
                var b = v6.Root.Instances[i];
                Assert.Equal(v6.Definitions[b.RefIdx!.Value].Name, v3.Definitions[a.RefIdx!.Value].Name);
                Assert.Equal(b.Matrix.Count, a.Matrix.Count);
                for (int k = 0; k < a.Matrix.Count; k++) Assert.True(Math.Abs(a.Matrix[k] - b.Matrix[k]) < 1e-9);
            }
        }
    }
}
