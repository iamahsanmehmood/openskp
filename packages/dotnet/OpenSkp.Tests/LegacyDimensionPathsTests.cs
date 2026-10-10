using System;
using System.Collections.Generic;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>
    /// Legacy (MFC) linear dimensions anchored inside groups - port of the
    /// Python fix (openskp#384) to this reader (openskp#412).
    ///
    /// Each connection ref of a CDimensionLinear is followed by an entity ref
    /// and two lists of entity refs - the instance paths of the anchored
    /// entity. On loose geometry they are a null ref and two empty lists (the
    /// zeros a fixed 42/82-byte layout used to skip); anchored inside nested
    /// components they carry a ref per component, and a fixed-size read slid
    /// off the record, silently cutting the root entity list short. The first
    /// time an instance is referenced MFC writes it in full, so a path can
    /// also hold a whole NEW object.
    ///
    /// Synthetic bytes only: the real file that exposed this (a SketchUp 2017
    /// cabinet, 21 of 96 root instances lost) is private and is not
    /// committed.
    /// </summary>
    public class LegacyDimensionPathsTests
    {
        /// <summary>A 6-byte big reference (0x7FFF escape) to an existing object.</summary>
        private static byte[] Big(uint slot) =>
            new byte[] { 0xFF, 0x7F, (byte)slot, (byte)(slot >> 8), (byte)(slot >> 16), (byte)(slot >> 24) };

        private static byte[] U32(uint n) =>
            new byte[] { (byte)n, (byte)(n >> 8), (byte)(n >> 16), (byte)(n >> 24) };

        private static byte[] Cat(params byte[][] parts) => parts.SelectMany(p => p).ToArray();

        private static readonly byte[] Tail = { (byte)'T', (byte)'A', (byte)'I', (byte)'L' };

        private static int? NeverNew(LR r) => throw new InvalidOperationException("unexpected new object");

        [Fact]
        public void LooseGeometryPathsAreEmptyAndTenBytes()
        {
            var data = Cat(new byte[] { 0, 0 }, U32(0), U32(0), Tail);
            var r = new LR(data, 0);
            var (extra, first, second) = LegacyReaders.ConnectionPaths(NeverNew, r);
            Assert.Null(extra);
            Assert.Empty(first);
            Assert.Empty(second);
            Assert.Equal(10, r.Pos); // the old fixed layout's zeros
        }

        [Fact]
        public void PathsInsideNestedComponentsAreReadRefByRef()
        {
            var data = Cat(
                Big(0x0F69CA), U32(2), Big(0x166157), Big(0x165BBC),
                U32(2), Big(0x166157), Big(0x165BBD), Tail);
            var r = new LR(data, 0);
            var (extra, first, second) = LegacyReaders.ConnectionPaths(NeverNew, r);
            Assert.Equal(0x0F69CA, extra);
            Assert.Equal(new int?[] { 0x166157, 0x165BBC }, first);
            Assert.Equal(new int?[] { 0x166157, 0x165BBD }, second);
            Assert.Equal(Tail, data.Skip(r.Pos));
        }

        [Fact]
        public void APathCanHoldAWholeNewObject()
        {
            // MFC serializes an object in full the first time it is referenced.
            var newTag = new byte[] { 0xFF, 0x7F, 0x67, 0x9F, 0x00, 0x80 };
            var data = Cat(new byte[] { 0, 0 }, U32(1), newTag, new byte[] { (byte)'O', (byte)'B', (byte)'J' }, U32(0), Tail);
            var r = new LR(data, 0);
            var (extra, first, second) = LegacyReaders.ConnectionPaths(rr =>
            {
                Assert.Equal(newTag, data.Skip(rr.Pos).Take(6));
                rr.Pos += 6 + 3; // the object's own bytes
                return 4242;
            }, r);
            Assert.Null(extra);
            Assert.Equal(new int?[] { 4242 }, first);
            Assert.Empty(second);
            Assert.Equal(Tail, data.Skip(r.Pos));
        }

        [Fact]
        public void ImplausiblePathLengthIsRejected()
        {
            var data = Cat(new byte[] { 0, 0 }, U32(100000));
            Assert.Throws<LegacyParseError>(() => LegacyReaders.ConnectionPaths(NeverNew, new LR(data, 0)));
        }
    }
}
