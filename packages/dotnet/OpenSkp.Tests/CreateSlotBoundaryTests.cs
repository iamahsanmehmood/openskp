using System;
using System.Collections.Generic;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>A slot of exactly 0x7FFF (32767) is unrepresentable in the
    /// short 2-byte form no matter which of the two short encodings would
    /// otherwise apply - it collides with a reserved marker value either
    /// way (confirmed by tracing Legacy.cs's actual read dispatch order,
    /// not just from the protocol table):
    ///
    /// * A plain back-ref of exactly 0x7FFF is byte-identical to the
    ///   big-tag escape marker itself - Archive.ReadObject checks
    ///   tag == 0x7FFF before it ever falls through to "plain back-ref",
    ///   so it would consume the next (unrelated) 4 bytes as a bogus slot.
    /// * A class-ref of exactly 0x7FFF sets 0x8000 | 0x7FFF == 0xFFFF,
    ///   which ReadObject checks for "new class declaration" before it
    ///   ever checks the class-ref high bit.
    ///
    /// Both desync every read after that point. This was a real,
    /// previously-shipped bug in the Python writer this project ports -
    /// see openskp/CHANGELOG.md's "Fixed" entry about slot 32767. These
    /// tests mirror packages/python/tests/test_create.py's own
    /// TestSlotBoundaryEncoding + TestLargeModelSlotBoundary classes.</summary>
    public class CreateSlotBoundaryTests
    {
        [Fact]
        public void BackrefBelowBoundaryUsesShortForm()
        {
            var writer = new ArchiveWriter(1, new Dictionary<string, int>());
            writer.Backref(0x7FFE);
            Assert.Equal(new byte[] { 0xFE, 0x7F }, writer.Buf.ToArray());
        }

        [Fact]
        public void BackrefAtBoundaryUsesEscapeForm()
        {
            var writer = new ArchiveWriter(1, new Dictionary<string, int>());
            writer.Backref(0x7FFF);
            var expected = new byte[] { 0xFF, 0x7F, 0xFF, 0x7F, 0x00, 0x00 };
            Assert.Equal(expected, writer.Buf.ToArray());
            // Specifically NOT the collision byte pattern a `<=` boundary
            // would have produced (identical to the escape marker alone).
            Assert.NotEqual(new byte[] { 0xFF, 0x7F }, writer.Buf.ToArray());
        }

        [Fact]
        public void NewOfKnownClassBelowBoundaryUsesShortForm()
        {
            var writer = new ArchiveWriter(1, new Dictionary<string, int> { ["Foo"] = 0x7FFE });
            writer.NewOfKnownClass("Foo");
            Assert.Equal(new byte[] { 0xFE, 0xFF }, writer.Buf.ToArray()); // 0x8000 | 0x7FFE = 0xFFFE
        }

        [Fact]
        public void NewOfKnownClassAtBoundaryUsesEscapeForm()
        {
            var writer = new ArchiveWriter(1, new Dictionary<string, int> { ["Foo"] = 0x7FFF });
            writer.NewOfKnownClass("Foo");
            var bytes = writer.Buf.ToArray();
            ushort tag = (ushort)(bytes[0] | (bytes[1] << 8));
            Assert.Equal(0x7FFF, tag);
            uint val = (uint)(bytes[2] | (bytes[3] << 8) | (bytes[4] << 16) | (bytes[5] << 24));
            Assert.Equal(0x80000000u | 0x7FFFu, val);
            // Specifically NOT 0xFFFF - the "new class declaration" marker.
            Assert.NotEqual((ushort)0xFFFF, tag);
        }

        [Fact]
        public void SlotBoundaryCrossingRoundTripsWithExactFaceCount()
        {
            // Real-scale reproduction: enough unique (non-shared-vertex)
            // triangles to push the file's total archive-slot count past
            // 32,767, the exact condition that corrupted large
            // flattened-geometry exports before this fix. Deliberately
            // uses unique vertices per triangle (~11 new slots each)
            // rather than a shared grid, to land the crossing at an
            // unpredictable, non-hand-picked slot number - the same
            // worst-case shape a CAD import's flattened mesh produces.
            const int n = 5000;
            var builder = SkpCreate.NewFile();
            for (int i = 0; i < n; i++)
            {
                double x = i * 10.0;
                builder.AddFace(new (double, double, double)[]
                {
                    (x, 0.0, 0.0), (x + 1.0, 0.0, 0.0), (x, 1.0, 0.0),
                });
            }

            var bytes = builder.ToBytes();
            var model = SkpFile.Parse(bytes);
            Assert.Equal(n, model.Root.Faces.Count);
        }
    }
}
