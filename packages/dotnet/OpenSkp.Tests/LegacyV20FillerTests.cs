using System;
using System.Collections.Generic;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>
    /// The v20 filler probe, exercised on synthetic records.
    ///
    /// Ported from the TypeScript fix (openskp#192, following the review on
    /// openskp#155): the original implementation walked forward to the first
    /// non-zero byte and treated it as the count's low byte. That cannot
    /// represent a count which is an exact multiple of 256 - its low byte IS
    /// 0x00, so the scan walks straight into the count and misaligns every
    /// read after it. Probing whole u32s at 4-byte strides fixes that, since
    /// no individual byte is ever inspected.
    ///
    /// Layout (see LegacyBytes.FindCountAfterV20Filler):
    ///   &lt;ff fe ff&gt; &lt;u8 0&gt;   empty UTF-16 string
    ///   &lt;zero padding&gt;      length varies per call site, always pad % 4 == 1
    ///   &lt;u32 count&gt;
    /// </summary>
    public class LegacyV20FillerTests
    {
        /// <summary>Builds a filler record followed by `count`, then a class-record header.</summary>
        private static byte[] Filler(uint count, int pad)
        {
            var bytes = new List<byte> { 0xFF, 0xFE, 0xFF, 0x00 };
            for (int i = 0; i < pad; i++) bytes.Add(0x00);
            bytes.Add((byte)(count & 0xFF));
            bytes.Add((byte)((count >> 8) & 0xFF));
            bytes.Add((byte)((count >> 16) & 0xFF));
            bytes.Add((byte)((count >> 24) & 0xFF));
            bytes.Add(0xFF); bytes.Add(0xFF); bytes.Add(0x0B); bytes.Add(0x00); // next record
            return bytes.ToArray();
        }

        [Fact]
        public void ReadsCountsAndPaddingsSeenInRealV20Files()
        {
            // both padding lengths observed in gondola_v20.skp and a second v20 model
            var hit1 = LegacyBytes.FindCountAfterV20Filler(Filler(20, 9), 0, 1_000_000);
            Assert.Equal((20u, 17), hit1);

            var hit2 = LegacyBytes.FindCountAfterV20Filler(Filler(5425, 13), 0, 5_000_000);
            Assert.Equal((5425u, 21), hit2);
        }

        [Fact]
        public void ReadsACountThatIsAnExactMultipleOf256()
        {
            // the regression this test exists for: a 0x00 low byte is
            // indistinguishable from padding to a byte-at-a-time scan
            uint[] counts = { 256, 512, 1024, 65536, 16_777_216 / 16 };
            int[] pads = { 9, 13 };
            foreach (var count in counts)
            {
                foreach (var pad in pads)
                {
                    var hit = LegacyBytes.FindCountAfterV20Filler(Filler(count, pad), 0, 5_000_000);
                    Assert.True(hit.HasValue, $"count={count} pad={pad}");
                    Assert.Equal(count, hit.Value.Count);
                }
            }
        }

        [Fact]
        public void ReportsWhereToResumeReading()
        {
            var hit = LegacyBytes.FindCountAfterV20Filler(Filler(256, 13), 0, 5_000_000);
            Assert.True(hit.HasValue);
            // 4 (marker+len) + 13 (padding) + 4 (the count itself)
            Assert.Equal(21, hit.Value.Next);
        }

        [Fact]
        public void IgnoresANonEmptyStringRecord()
        {
            // a real string here is genuine data; moving the cursor past it
            // would corrupt the parse
            var bytes = new byte[] { 0xFF, 0xFE, 0xFF, 0x05, 0, 0, 0, 0, 0, 0, 0, 0, 20, 0, 0, 0 };
            Assert.Null(LegacyBytes.FindCountAfterV20Filler(bytes, 0, 1_000_000));
        }

        [Fact]
        public void ReturnsNullWhenThereIsNoMarkerAhead()
        {
            var bytes = new byte[32]; // all zeros, no ff fe ff
            Assert.Null(LegacyBytes.FindCountAfterV20Filler(bytes, 0, 1_000_000));
        }

        [Fact]
        public void RespectsTheCallersPlausibilityLimit()
        {
            // nrel's limit is 100_000: a value above it is not the count we want
            Assert.Null(LegacyBytes.FindCountAfterV20Filler(Filler(200_000, 13), 0, 100_000));
            var hit = LegacyBytes.FindCountAfterV20Filler(Filler(200_000, 13), 0, 5_000_000);
            Assert.True(hit.HasValue);
            Assert.Equal(200_000u, hit.Value.Count);
        }

        [Fact]
        public void DoesNotRunPastTheEndOfTheBuffer()
        {
            var bytes = new byte[] { 0xFF, 0xFE, 0xFF, 0x00, 0, 0 };
            Assert.Null(LegacyBytes.FindCountAfterV20Filler(bytes, 0, 1_000_000));
        }
    }
}
