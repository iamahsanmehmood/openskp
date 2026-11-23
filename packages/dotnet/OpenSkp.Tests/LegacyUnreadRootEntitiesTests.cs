using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>The classic root reader keeps what it read before the first
    /// record it can't parse and drops the rest of the root.
    /// <see cref="SkpModel.UnreadRootEntities"/> says how many entities that
    /// dropped and <see cref="SkpParseOptions.OnLog"/> emits a
    /// <see cref="SkpLogLevel.Warning"/> when it is nonzero, so a partial read
    /// isn't silent.</summary>
    public class LegacyUnreadRootEntitiesTests
    {
        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

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

        [Fact]
        public void CompleteFilesReportNone()
        {
            foreach (var name in new[]
            {
                "Untitled.skp",
                "capilla_quiroz_v17.skp",
                "gondola_v20.skp",
                "legacy_text_arrows_2017.skp",
                "nesting_2017.skp",
                "SU_File.skp",
            })
            {
                var logs = new List<(SkpLogLevel Level, string Message)>();
                var options = new SkpParseOptions
                {
                    OnLog = (level, message) => logs.Add((level, message)),
                };
                var model = SkpFile.Open(FixturePath(name), options);
                Assert.Equal(0, model.UnreadRootEntities);
                Assert.DoesNotContain(logs, entry => entry.Level == SkpLogLevel.Warning);
            }
        }

        [Fact]
        public void AnUnreadableTextDropsTheRestOfTheRootAndWarns()
        {
            var builder = SkpCreate.NewFile();
            builder.AddFace(new (double, double, double)[]
            {
                (0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0),
            });
            builder.AddText("First", (0, 0, 0));
            builder.AddText("Broken", (5, 0, 0));
            builder.AddFace(new (double, double, double)[]
            {
                (20, 0, 0), (30, 0, 0), (30, 10, 0), (20, 10, 0),
            });

            var bytes = builder.ToBytes();
            var complete = SkpFile.Parse(bytes);
            Assert.Equal(2, complete.Root.Texts.Count);
            Assert.Equal(0, complete.UnreadRootEntities);
            Assert.Equal(0, SkpFile.BuildScene(bytes).UnreadRootEntities);
            Assert.Equal(0, SkpFile.BuildInstancedScene(bytes).UnreadRootEntities);

            // Break the second text's string marker so the reader can't find its text.
            var marker = new byte[] { 0x01, 0xFF, 0xFE, 0xFF, 0x06, (byte)'B', 0x00 };
            int at = FindSubsequence(bytes, marker);
            Assert.True(at >= 0);
            for (int i = 0; i < 4; i++) bytes[at + i] = 0x00;

            var logs = new List<(SkpLogLevel Level, string Message)>();
            var options = new SkpParseOptions
            {
                OnLog = (level, message) => logs.Add((level, message)),
            };
            var model = SkpFile.Parse(bytes, options);
            var text = Assert.Single(model.Root.Texts);
            Assert.Equal("First", text.Text);
            Assert.True(model.UnreadRootEntities > 0);
            Assert.Equal(model.UnreadRootEntities, SkpFile.BuildScene(bytes).UnreadRootEntities);
            Assert.Equal(model.UnreadRootEntities, SkpFile.BuildInstancedScene(bytes).UnreadRootEntities);
            Assert.Contains(
                logs,
                entry => entry.Level == SkpLogLevel.Warning
                    && entry.Message.Contains("stopped early")
                    && entry.Message.Contains($"{model.UnreadRootEntities} unread"));
        }
    }
}
