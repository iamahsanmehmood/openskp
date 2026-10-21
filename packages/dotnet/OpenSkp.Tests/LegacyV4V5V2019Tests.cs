using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>
    /// Regression tests for SketchUp 4, 5 and 2019 files - port of the Python
    /// reader's fixes (openskp#284, #440) to this reader.
    ///
    /// Fixtures are small synthetic models, saved down through the installed
    /// SketchUp library from the same sources as the existing legacy fixtures
    /// (legacy_v6_synthetic.skp and the one-face textured model behind
    /// legacy_v3_textured.skp); no private content:
    ///
    /// * legacy_v4/v5/v2019_synthetic.skp: 4 definitions (InnerFrame, Frame,
    ///   Panel, Post), 10 root instances, a "Roof" layer, 24 materials.
    /// * legacy_v4/v5_textured.skp: one painted face with an 8x8 PNG, stored
    ///   as a bare inline image before SketchUp 6 (not a CDib object).
    /// * legacy_v2020_textured.skp: the same model at SketchUp 2020 - a model
    ///   with NO component definitions, whose root entities used to be read
    ///   as "definitions" and silently dropped.
    ///
    /// Parsing without an exception is not enough (an early V3 attempt
    /// "succeeded" with one definition of four), so these assert parsed
    /// structure and cross-check it against the V6 file from the same source.
    /// </summary>
    public class LegacyV4V5V2019Tests
    {
        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        private static SkpModel Parse(string name) => SkpFile.Open(FixturePath(name));

        private static string R(IEnumerable<double> xs) =>
            string.Join(",", xs.Select(x => Math.Round(x, 5).ToString("R", System.Globalization.CultureInfo.InvariantCulture)));

        /// <summary>Everything that matters about a model, keyed by names and
        /// coordinates (ids differ between versions).</summary>
        private static string Canonical(SkpModel m, bool instanceNames)
        {
            var matName = m.Materials.Where(x => x.Id != null).ToDictionary(x => x.Id!.Value, x => x.Name);
            string DefName(long? idx) => idx != null && m.Definitions.TryGetValue(idx.Value, out var d) ? d.Name : "?";
            string Inst(Instance i) => $"{DefName(i.RefIdx)}|{R(i.Matrix)}|{(instanceNames ? i.Name : "")}";
            var sb = new System.Text.StringBuilder();
            sb.Append("layers:").Append(string.Join(",", m.Layers.Select(l => l.Name).OrderBy(n => n, StringComparer.Ordinal))).Append(';');
            sb.Append("materials:").Append(string.Join(",", m.Materials.Select(x => x.Name).OrderBy(n => n, StringComparer.Ordinal))).Append(';');
            foreach (var d in m.Definitions.Values.OrderBy(d => d.Name, StringComparer.Ordinal))
            {
                sb.Append("def ").Append(d.Name).Append(':');
                sb.Append("v=").Append(string.Join(";", d.Vertices.Values.Select(v => R(new[] { v.X, v.Y, v.Z })).OrderBy(s => s, StringComparer.Ordinal)));
                sb.Append(" e=").Append(d.Edges.Count);
                sb.Append(" f=").Append(string.Join(";", d.Faces.Values.Select(f =>
                    $"{(f.MaterialId != null && matName.TryGetValue(f.MaterialId.Value, out var n) ? n : "-")}/{f.Hidden}").OrderBy(s => s, StringComparer.Ordinal)));
                sb.Append(" i=").Append(string.Join(";", d.Instances.Select(Inst).OrderBy(s => s, StringComparer.Ordinal))).Append(';');
            }
            sb.Append("root:").Append(string.Join(";", m.Root.Instances.Select(Inst).OrderBy(s => s, StringComparer.Ordinal)));
            return sb.ToString();
        }

        public static IEnumerable<object[]> Versions => new[]
        {
            new object[] { "legacy_v4_synthetic.skp", "{4.0.1}" },
            new object[] { "legacy_v5_synthetic.skp", "{5.0.1}" },
            new object[] { "legacy_v2019_synthetic.skp", "{19.0.1}" },
        };

        // SketchUp 4 stores no instance names, so its comparison leaves them out.
        public static IEnumerable<object[]> WithNames => new[]
        {
            new object[] { "legacy_v4_synthetic.skp", false },
            new object[] { "legacy_v5_synthetic.skp", true },
            new object[] { "legacy_v2019_synthetic.skp", true },
        };

        [Theory]
        [MemberData(nameof(Versions))]
        public void ParsesAndReportsItsVersion(string file, string version)
        {
            Assert.Equal(version, Parse(file).Version);
        }

        [Theory]
        [MemberData(nameof(WithNames))]
        public void MatchesTheV6FileFromTheSameSource(string file, bool names)
        {
            Assert.Equal(Canonical(Parse("legacy_v6_synthetic.skp"), names), Canonical(Parse(file), names));
        }

        [Theory]
        [InlineData("legacy_v4_synthetic.skp")]
        [InlineData("legacy_v5_synthetic.skp")]
        [InlineData("legacy_v2019_synthetic.skp")]
        public void IsCompleteNotJustErrorFree(string file)
        {
            var m = Parse(file);
            var defs = m.Definitions.Values.ToDictionary(d => d.Name);
            Assert.Equal(new[] { "Frame", "InnerFrame", "Panel", "Post" }, defs.Keys.OrderBy(k => k, StringComparer.Ordinal));
            foreach (var d in defs.Values)
            {
                Assert.Equal((1, 4, 4), (d.Faces.Count, d.Edges.Count, d.Vertices.Count));
            }
            Assert.Equal(10, m.Root.Instances.Count);
            Assert.Equal(24, m.Materials.Count);
            var layers = m.Layers.Select(l => l.Name).ToHashSet();
            Assert.True(layers.Contains("Layer0") && layers.Contains("Roof"));
            Assert.Equal(new[] { "InnerFrame" },
                defs["Panel"].Instances.Select(i => m.Definitions[i.RefIdx!.Value].Name));
        }

        [Fact]
        public void SketchUp4HasNoInstanceNamesAndSketchUp5Does()
        {
            var v6 = Parse("legacy_v6_synthetic.skp");
            Assert.Contains(v6.Root.Instances, i => !string.IsNullOrEmpty(i.Name));
            Assert.All(Parse("legacy_v4_synthetic.skp").Root.Instances, i => Assert.Equal("", i.Name));
            Assert.Equal(v6.Root.Instances.Select(i => i.Name),
                Parse("legacy_v5_synthetic.skp").Root.Instances.Select(i => i.Name));
        }

        private static Texture? TextureOf(SkpModel m) =>
            m.Materials.FirstOrDefault(x => x.Texture?.Data != null)?.Texture;

        [Theory]
        [InlineData("legacy_v4_textured.skp")]
        [InlineData("legacy_v5_textured.skp")]
        public void SketchUp4And5ReadTheBareInlineTexture(string file)
        {
            // Was: texture object is not a dib.
            var reference = TextureOf(Parse("legacy_v3_textured.skp"))!;
            var m = Parse(file);
            var tex = TextureOf(m);
            Assert.NotNull(tex);
            Assert.Equal(reference.Data, tex!.Data);                      // the same PNG, byte for byte
            Assert.Equal(new byte[] { 0x89, 0x50, 0x4E, 0x47 }, tex.Data!.Take(4).ToArray());
            Assert.Single(m.Root.Faces);
        }

        [Fact]
        public void SketchUp2020ModelWithoutDefinitionsKeepsItsRootEntities()
        {
            // The root entity count was handed over as a "definition count", so
            // the face and its four edges were consumed as definitions and the
            // model came out empty - with no error.
            var m = Parse("legacy_v2020_textured.skp");
            Assert.Equal((1, 4, 4), (m.Root.Faces.Count, m.Root.Edges.Count, m.Root.Vertices.Count));
            Assert.NotNull(TextureOf(m));
        }

        // One real CDimensionLinear record per version, taken from the
        // repository's own Untitled.skp re-saved at SketchUp 4, 5 and 6 (record
        // bytes only - from the entity preamble to the end of the trailing
        // block). All three describe the same dimension, so each must be
        // consumed exactly and decode to the same text.
        private static readonly Dictionary<int, string> DimRecords = new Dictionary<int, string>
        {
            { 4,
                "00000f0000010100001d00fffeff00010000000400000000000000000000000000000000" +
                "000000000000000000000000000000000000000000000001000000040000000000000000" +
                "0000007e210ac0556f564000000000000000000000000000000000000000000000000000" +
                "00f0bf000000000000000000000000000000000000000000000080000000000000f0bf00" +
                "0000000000008001000000b95c2e97cbe512c00000000000000000" },
            { 5,
                "00000f000001010000001d00fffeff000100000004000000000000000000000000000000" +
                "000000000000000000000000000000000000000000000000010000000400000000000000" +
                "000000007e210ac0556f5640000000000000000000000000000000000000000000000000" +
                "0000f0bf000000000000000000000000000000000000000000000080000000000000f0bf" +
                "000000000000008001000000b95c2e97cbe512c00000000000000000" },
            { 6,
                "00000f000001010000001d00fffeff00ffff0100070043536b466f6e740000fffeff0654" +
                "00610068006f006d00610000000c00000000000000000000244000030000000100000004" +
                "000000000000000000000000000000000000000000000000000000000000000000000000" +
                "000000010000000400000000000000000000007e210ac0556f5640000000000000000000" +
                "0000000000000000000000000000000000f0bf0000000000000000000000000000000000" +
                "00000000000080000000000000f0bf000000000000008001000000b95c2e97cbe512c000" +
                "0000000000000003000000" },
        };

        private static (DimLinearRec Rec, int Consumed, int Size) ReadDimension(int ver)
        {
            var record = Convert.FromHexString(DimRecords[ver]);
            var tail = new byte[] { 0xFF, 0x7F, 0x00, 0x00, 0x00, 0x80 };   // the next entity's big class-ref tag
            var data = record.Concat(tail).ToArray();
            var ar = new Archive(data, ver);
            foreach (var kv in LegacyReaders.Readers) ar.Readers[kv.Key] = kv.Value;
            ar.NextSlot = ar.WalkBase = 100;
            var r = new LR(data, 0);
            var rec = (DimLinearRec)LegacyReaders.ReadDimLinear(ar, r);
            return (rec, r.Pos, record.Length);
        }

        [Theory]
        [InlineData(4)]
        [InlineData(5)]
        [InlineData(6)]
        public void DimensionRecordIsConsumedExactly(int ver)
        {
            var (_, consumed, size) = ReadDimension(ver);
            Assert.Equal(size, consumed);
        }

        [Theory]
        [InlineData(4)]
        [InlineData(5)]
        public void SketchUp4And5DecodeTheSameDimensionAs6(int ver)
        {
            Assert.Equal(ReadDimension(6).Rec.Text, ReadDimension(ver).Rec.Text);
        }
    }
}
