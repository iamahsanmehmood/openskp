using System;
using System.IO;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>
    /// A SketchUp 2020 model with nothing in it (no entities, no component definitions) opens.
    ///
    /// Fixture legacy_empty_2020.skp: an empty model saved as a SketchUp 2020 file (SUModelVersion 13) by SketchUp's
    /// own library, the blank-template case. SketchUp 2020 writes an empty string record and zero padding where the
    /// definition count would be; with nothing in the model the only count behind it is the root entity count, which
    /// is 0, and the filler search skipped it, so the whole file was rejected with "implausible definition count".
    /// No private content.
    /// </summary>
    public class LegacyEmptyModel2020Tests
    {
        [Fact]
        public void AnEmpty2020ModelParsesToAnEmptyModel()
        {
            var model = SkpFile.Open(Path.Combine(AppContext.BaseDirectory, "fixtures", "legacy_empty_2020.skp"));
            Assert.Equal(new[] { "Layer0" }, model.Layers.Select(l => l.Name).ToArray());
            Assert.Empty(model.Definitions);
            Assert.Empty(model.Materials);
            Assert.Empty(model.Root.Faces);
            Assert.Empty(model.Root.Instances);
        }

        private static byte[] Filler(int zeros, byte[]? tail = null)
        {
            tail ??= new byte[] { 0x58, 0xE7, 0x37, 0x6A };
            return new byte[] { 0, 0, 0, 0xFF, 0xFE, 0xFF, 0 }.Concat(new byte[zeros]).Concat(tail).ToArray();
        }

        [Fact]
        public void ReturnsTheRootCountOffsetThirteenBytesOfPaddingBehindTheStringLengthByte()
        {
            Assert.Equal(3 + 4 + 13, LegacyBytes.FindEmptyRootAfterV20Filler(Filler(26), 0));
        }

        [Fact]
        public void ANonEmptyStringIsRealData()
        {
            var data = Filler(30);
            data[6] = 5;
            Assert.Null(LegacyBytes.FindEmptyRootAfterV20Filler(data, 0));
        }

        [Fact]
        public void ANonZeroByteInThePaddingOrTheCountIsNotThisLayout()
        {
            for (int at = 4; at < 4 + 13 + 4; at++)
            {
                var data = Filler(26);
                data[3 + at] = 1;
                Assert.True(LegacyBytes.FindEmptyRootAfterV20Filler(data, 0) == null, $"byte {at}");
            }
        }

        [Fact]
        public void NoFillerNearbyAndATruncatedFillerAreRejected()
        {
            Assert.Null(LegacyBytes.FindEmptyRootAfterV20Filler(new byte[64], 0));
            Assert.Null(LegacyBytes.FindEmptyRootAfterV20Filler(Filler(5, new byte[0]), 0));
        }
    }
}
