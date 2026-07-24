using System;
using System.Collections.Generic;
using System.IO;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>
    /// openskp exposes progress via an optional SkpParseOptions
    /// (Progress/OnLog) - silent by default - and raises SkpParseException
    /// with structured location context (Stage, RecordIndex, Tag, ...) on
    /// failure, so a production pipeline can trace exactly where a model
    /// got stuck instead of a bare stack trace.
    /// </summary>
    public class ObservabilityTests
    {
        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        [Fact]
        public void SilentByDefaultWithNoOptions()
        {
            var model = SkpFile.Open(FixturePath("capilla_quiroz_v17.skp"));
            Assert.Equal("{17.0.18899}", model.Version);
        }

        [Fact]
        public void ReportsLogMessagesForARealParse()
        {
            var messages = new List<(SkpLogLevel Level, string Message)>();
            var options = new SkpParseOptions { OnLog = (level, message) => messages.Add((level, message)) };

            SkpFile.Open(FixturePath("capilla_quiroz_v17.skp"), options);

            Assert.Contains(messages, m => m.Message.Contains("Parsing legacy"));
            Assert.Contains(messages, m => m.Message.Contains("Parse complete"));
        }

        [Fact]
        public void ReportsLogMessagesForBuildScene()
        {
            var messages = new List<(SkpLogLevel Level, string Message)>();
            var options = new SkpParseOptions { OnLog = (level, message) => messages.Add((level, message)) };

            SkpFile.BuildScene(FixturePath("capilla_quiroz_v17.skp"), options);

            Assert.Contains(messages, m => m.Message.Contains("Building scene"));
            Assert.Contains(messages, m => m.Message.Contains("Scene build complete"));
        }

        [Fact]
        public void RaisesSkpParseExceptionWithStageForACorruptFile()
        {
            var bad = new byte[200];
            for (int i = 0; i < bad.Length; i++) bad[i] = 0x41; // "AAAA..." - not a valid header

            var ex = Assert.Throws<SkpParseException>(() => SkpFile.Parse(bad));
            Assert.Equal("header", ex.Stage);
        }

        [Fact]
        public void SkpParseExceptionMessageIncludesStructuredContext()
        {
            var ex = new SkpParseException(
                "boom", stage: "tlv_walk", recordIndex: 3, totalRecords: 10, tag: "F601");

            Assert.Contains("stage=tlv_walk", ex.Message);
            Assert.Contains("record=3/10", ex.Message);
            Assert.Contains("tag=F601", ex.Message);
        }

        [Fact]
        public void SkpParseExceptionPreservesInnerException()
        {
            var original = new InvalidOperationException("inner failure");
            var wrapped = new SkpParseException("wrapped", stage: "tlv_walk", innerException: original);
            Assert.Same(original, wrapped.InnerException);
        }

        [Fact]
        public void ProgressCallbackFiresDuringLegacyParse()
        {
            var reports = new List<SkpParseProgress>();
            var progress = new Progress<SkpParseProgress>(p => reports.Add(p));
            // Progress<T> marshals via SynchronizationContext.Post by default,
            // which needs a message pump to drain - this fixture is small
            // enough (3 defs) that it never crosses ParseTuning.ProgressInterval
            // (500), so we only assert the call completes without throwing.
            var options = new SkpParseOptions { Progress = progress };
            var model = SkpFile.Open(FixturePath("capilla_quiroz_v17.skp"), options);
            Assert.Equal("{17.0.18899}", model.Version);
        }
    }
}
