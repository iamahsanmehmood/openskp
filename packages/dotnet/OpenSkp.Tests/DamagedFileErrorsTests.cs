using System;
using System.IO;
using OpenSkp;
using Xunit;

namespace OpenSkp.Tests
{
    public class DamagedFileErrorsTests
    {
        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        private static int FindZipOffset(byte[] data)
        {
            for (int i = 0; i + 4 <= data.Length; i++)
            {
                if (data[i] == (byte)'P' && data[i + 1] == (byte)'K' && data[i + 2] == 0x03 && data[i + 3] == 0x04)
                {
                    return i;
                }
            }
            return -1;
        }

        private static int FindSubsequence(byte[] haystack, byte[] needle)
        {
            for (int i = 0; i + needle.Length <= haystack.Length; i++)
            {
                bool match = true;
                for (int j = 0; j < needle.Length; j++)
                {
                    if (haystack[i + j] != needle[j])
                    {
                        match = false;
                        break;
                    }
                }
                if (match) return i;
            }
            return -1;
        }

        [Theory]
        [InlineData(0.1)]
        [InlineData(0.5)]
        public void TruncatedVffZipRaisesSkpParseException(double fraction)
        {
            var raw = File.ReadAllBytes(FixturePath("SU_File.skp"));
            int len = Math.Max(64, (int)(raw.Length * fraction));
            var truncated = new byte[len];
            Buffer.BlockCopy(raw, 0, truncated, 0, len);

            var parseEx = Assert.Throws<SkpParseException>(() => SkpFile.Parse(truncated));
            Assert.Equal("zip_extract", parseEx.Stage);

            var sceneEx = Assert.Throws<SkpParseException>(() => SkpFile.BuildScene(truncated));
            Assert.Equal("zip_extract", sceneEx.Stage);

            var instEx = Assert.Throws<SkpParseException>(() => SkpFile.BuildInstancedScene(truncated));
            Assert.Equal("zip_extract", instEx.Stage);
        }

        [Fact]
        public void CorruptedVffZipEntryRaisesSkpParseExceptionWithCause()
        {
            var raw = File.ReadAllBytes(FixturePath("SU_File.skp"));
            int pk = FindZipOffset(raw);
            Assert.True(pk > 0);

            int cut = pk + (raw.Length - pk) / 2;
            var damaged = new byte[cut];
            Buffer.BlockCopy(raw, 0, damaged, 0, cut);

            var ex = Assert.Throws<SkpParseException>(() => SkpFile.Parse(damaged));
            Assert.Equal("zip_extract", ex.Stage);
            Assert.NotNull(ex.InnerException);
        }

        [Theory]
        [InlineData(0.1)]
        [InlineData(0.5)]
        public void TruncatedClassicFileRaisesSkpParseExceptionWithCause(double fraction)
        {
            var raw = File.ReadAllBytes(FixturePath("capilla_quiroz_v17.skp"));
            int len = Math.Max(64, (int)(raw.Length * fraction));
            var truncated = new byte[len];
            Buffer.BlockCopy(raw, 0, truncated, 0, len);

            var parseEx = Assert.Throws<SkpParseException>(() => SkpFile.Parse(truncated));
            Assert.Equal("legacy_walk", parseEx.Stage);
            Assert.NotNull(parseEx.InnerException);

            var sceneEx = Assert.Throws<SkpParseException>(() => SkpFile.BuildScene(truncated));
            Assert.Equal("legacy_walk", sceneEx.Stage);
            Assert.NotNull(sceneEx.InnerException);

            var instEx = Assert.Throws<SkpParseException>(() => SkpFile.BuildInstancedScene(truncated));
            Assert.Equal("legacy_walk", instEx.Stage);
            Assert.NotNull(instEx.InnerException);
        }

        [Fact]
        public void MissingAndUnreadableFilesKeepIoExceptions()
        {
            var missing = Path.Combine(Path.GetTempPath(), $"openskp_missing_{Guid.NewGuid():N}.skp");
            Assert.Throws<FileNotFoundException>(() => SkpFile.Open(missing));
            Assert.Throws<FileNotFoundException>(() => SkpFile.BuildScene(missing));
            Assert.Throws<FileNotFoundException>(() => SkpFile.BuildInstancedScene(missing));

            var lockedPath = Path.Combine(Path.GetTempPath(), $"openskp_locked_{Guid.NewGuid():N}.skp");
            File.WriteAllBytes(lockedPath, File.ReadAllBytes(FixturePath("SU_File.skp")));
            try
            {
                using (var exclusive = new FileStream(lockedPath, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
                {
                    if (OperatingSystem.IsWindows())
                    {
                        Assert.ThrowsAny<IOException>(() => SkpFile.Open(lockedPath));
                    }
                }
            }
            finally
            {
                File.Delete(lockedPath);
            }
        }

        [Fact]
        public void ClassicFixtureCutAtEveryOffsetReturnsOrRaisesSkpParseException()
        {
            var raw = File.ReadAllBytes(FixturePath("legacy_v4_textured.skp"));
            for (int cut = 0; cut <= raw.Length; cut++)
            {
                var slice = new byte[cut];
                Buffer.BlockCopy(raw, 0, slice, 0, cut);
                try
                {
                    SkpFile.Parse(slice);
                }
                catch (SkpParseException)
                {
                }
            }
        }

        [Fact]
        public void PartialClassicRootReadIsNotHidden()
        {
            var builder = new SkpBuilder();
            builder.AddFace(new[] { (0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0) });
            builder.AddText("First", (0.0, 0.0, 0.0));
            builder.AddText("Broken", (5.0, 0.0, 0.0));
            var bytes = builder.ToBytes();

            var marker = new byte[] { 0x01, 0xFF, 0xFE, 0xFF, 0x06, (byte)'B', 0x00 };
            int at = FindSubsequence(bytes, marker);
            Assert.True(at >= 0);
            for (int i = 0; i < 4; i++) bytes[at + i] = 0x00;

            var model = SkpFile.Parse(bytes);
            Assert.Single(model.Root.Texts);
            Assert.True(model.UnreadRootEntities > 0);
        }
    }
}
