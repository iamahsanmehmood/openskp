using System;
using System.IO;
using System.IO.Compression;
using System.Text;
using OpenSkp;
using Xunit;

namespace OpenSkp.Tests
{
    /// <summary>
    /// Hostile-input limits. A model.dat that nests TLV containers tens of
    /// thousands of levels deep aborted the whole process with a stack
    /// overflow (no catch can intercept it); a meta.dat wrapped 100,000
    /// times took 3.4 s and 5.9 GB; a truncated model.dat parsed as a model
    /// with no geometry and no error.
    /// </summary>
    public class ParserLimitsTests
    {
        private static byte[] Record(byte tag0, byte tag1, byte[] payload)
        {
            var result = new byte[6 + payload.Length];
            result[0] = tag0;
            result[1] = tag1;
            BitConverter.GetBytes((uint)payload.Length).CopyTo(result, 2);
            payload.CopyTo(result, 6);
            return result;
        }

        /// <summary>`levels` records of the tag, each inside the previous,
        /// around `leaf`. Built in one pass: wrapping level by level would
        /// copy the payload each time and take quadratic time for the
        /// 100,000-level cases.</summary>
        private static byte[] Nested(byte tag0, byte tag1, int levels, byte[]? leaf = null)
        {
            leaf ??= Array.Empty<byte>();
            var result = new byte[6 * levels + leaf.Length];
            for (int i = 0; i < levels; i++)
            {
                result[6 * i] = tag0;
                result[6 * i + 1] = tag1;
                BitConverter.GetBytes((uint)(leaf.Length + 6 * (levels - 1 - i))).CopyTo(result, 6 * i + 2);
            }
            leaf.CopyTo(result, 6 * levels);
            return result;
        }

        private static string WriteSkp(byte[] modelDat, byte[]? metaDat = null)
        {
            var header = new byte[32];
            header[0] = 0xFF; header[1] = 0xFE; header[2] = 0xFF; header[3] = 0x0E;
            using var zipBytes = new MemoryStream();
            using (var archive = new ZipArchive(zipBytes, ZipArchiveMode.Create, leaveOpen: true))
            {
                void Add(string name, byte[] content)
                {
                    using var s = archive.CreateEntry(name).Open();
                    s.Write(content, 0, content.Length);
                }
                Add("model.dat", modelDat);
                if (metaDat != null) Add("meta/meta.dat", metaDat);
            }
            var path = Path.Combine(Path.GetTempPath(), "openskp-limits-" + Guid.NewGuid().ToString("N") + ".skp");
            var all = new byte[header.Length + zipBytes.Length];
            header.CopyTo(all, 0);
            zipBytes.ToArray().CopyTo(all, header.Length);
            File.WriteAllBytes(path, all);
            return path;
        }

        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        private static byte[] FixtureModelDat(string name)
        {
            var data = File.ReadAllBytes(FixturePath(name));
            using var zip = Vff.OpenZip(data, Vff.FindZipOffset(data));
            using var stream = zip.GetEntry("model.dat")!.Open();
            using var copy = new MemoryStream();
            stream.CopyTo(copy);
            return copy.ToArray();
        }

        private static T WithSkp<T>(byte[] modelDat, byte[]? metaDat, Func<string, T> use)
        {
            var path = WriteSkp(modelDat, metaDat);
            try { return use(path); }
            finally { File.Delete(path); }
        }

        private static readonly byte[] Inches = Record(0x6D, 0x00, Encoding.UTF8.GetBytes("Inches"));

        [Fact]
        public void ParseRecursiveFollowsTheDeepestNestingTheLimitAllows()
        {
            var buffer = ChunkedBuffer.FromArray(Nested(0xF4, 0x01, Tlv.MaxDepth));
            Assert.Single(Tlv.ParseRecursive(buffer, 0, buffer.Length));
        }

        [Theory]
        [InlineData(Tlv.MaxDepth + 2)]
        [InlineData(5000)]
        [InlineData(200000)]
        public void ParseRecursiveRejectsDeepNestingWithAParseError(int levels)
        {
            var buffer = ChunkedBuffer.FromArray(Nested(0xF4, 0x01, levels));
            var ex = Assert.Throws<SkpParseException>(() => Tlv.ParseRecursive(buffer, 0, buffer.Length));
            Assert.Equal("tlv_walk", ex.Stage);
            Assert.Equal("F401", ex.Tag);
            Assert.Contains("nest deeper", ex.Message);
        }

        [Theory]
        [InlineData(Tlv.MaxDepth + 2)]
        [InlineData(5000)]
        [InlineData(200000)]
        public void OpenRejectsDeepNesting(int levels)
        {
            var model = Record(0xF4, 0x01, Nested(0xF4, 0x01, levels));
            var ex = Assert.Throws<SkpParseException>(() => WithSkp(model, null, p => SkpFile.Open(p)));
            Assert.Contains("nest deeper", ex.Message);
        }

        [Fact]
        public void OpenAcceptsModerateNesting()
        {
            var model = Record(0xF4, 0x01, Nested(0xF4, 0x01, 40));
            var parsed = WithSkp(model, null, p => SkpFile.Open(p));
            Assert.NotNull(parsed);
        }

        [Fact]
        public void ReadMetaUnitsFollowsAFewWrappers()
        {
            var meta = Inches;
            for (int i = 0; i < 3; i++) meta = Record(0x64, 0x00, meta);
            Assert.Equal("Inches", Vff.ReadMetaUnits(meta));
        }

        [Theory]
        [InlineData(Tlv.MaxDepth + 5)]
        [InlineData(100000)]
        public void ReadMetaUnitsGivesUnknownUnitsForDeepWrappersWithoutRecursing(int levels)
        {
            Assert.Null(Vff.ReadMetaUnits(Nested(0x64, 0x00, levels, Inches)));
        }

        [Fact]
        public void DeepMetaWrappersDoNotFailTheParse()
        {
            var meta = Nested(0x64, 0x00, 100000, Inches);
            var parsed = WithSkp(Record(0xF4, 0x01, Array.Empty<byte>()), meta, p => SkpFile.Open(p));
            Assert.Null(parsed.Units);
        }

        [Theory]
        [InlineData("SU_File.skp")]
        [InlineData("Untitled.skp")]
        public void RealFilesEndExactlyOnTheirTopLevelRecord(string fixture)
        {
            var model = FixtureModelDat(fixture);
            Assert.Equal(model.Length, 6 + (long)BitConverter.ToUInt32(model, 2));
            Assert.NotNull(SkpFile.Open(FixturePath(fixture)));
        }

        [Theory]
        [InlineData(50)]
        [InlineData(99)]
        public void ACutShortModelDatIsAnError(int percent)
        {
            var model = FixtureModelDat("SU_File.skp");
            var cut = new byte[model.Length * percent / 100];
            Array.Copy(model, cut, cut.Length);
            var ex = Assert.Throws<SkpParseException>(() => WithSkp(cut, null, p => SkpFile.Open(p)));
            Assert.Contains("truncated", ex.Message);
            Assert.Equal("tlv_walk", ex.Stage);
            Assert.Equal("F401", ex.Tag);
            Assert.Equal(0, ex.Offset);
        }

        [Fact]
        public void TrailingBytesShorterThanAHeaderAreIgnored()
        {
            var model = FixtureModelDat("SU_File.skp");
            var padded = new byte[model.Length + 3];
            model.CopyTo(padded, 0);
            Assert.NotNull(WithSkp(padded, null, p => SkpFile.Open(p)));
        }

        [Fact]
        public void AnEmptyModelDatStillParses()
        {
            Assert.NotNull(WithSkp(Array.Empty<byte>(), null, p => SkpFile.Open(p)));
        }
    }
}
