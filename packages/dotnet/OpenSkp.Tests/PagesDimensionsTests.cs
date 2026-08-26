using System;
using System.Collections.Generic;
using OpenSkp;
using Xunit;

namespace OpenSkp.Tests
{
    /// <summary>VFF scenes ("pages") and linear dimensions - ported from
    /// Python's test_pages_dimensions.py (PR #190).
    ///
    /// Dimensions are exercised against the repository's own Untitled.skp
    /// fixture (drawn in SketchUp 2025, it carries 13 linear dimensions);
    /// scenes have no fixture yet, so their parser is exercised on a
    /// synthetic "0702" record byte-for-byte shaped like the real ones (the
    /// layout was decoded from production survey files and calibrated
    /// against the scene thumbnails SketchUp embeds in the .skp itself).</summary>
    public class PagesDimensionsTests
    {
        // ── helpers: build TLV runs in the flat (u16-LE tag, u32 len) form ──

        private static byte[] Tlv(ushort tag, byte[] payload)
        {
            var result = new byte[6 + payload.Length];
            result[0] = (byte)(tag & 0xFF);
            result[1] = (byte)(tag >> 8);
            uint len = (uint)payload.Length;
            result[2] = (byte)(len & 0xFF);
            result[3] = (byte)((len >> 8) & 0xFF);
            result[4] = (byte)((len >> 16) & 0xFF);
            result[5] = (byte)((len >> 24) & 0xFF);
            Array.Copy(payload, 0, result, 6, payload.Length);
            return result;
        }

        private static byte[] Vec3(double x, double y, double z)
        {
            var result = new byte[24];
            Array.Copy(BitConverter.GetBytes(x), 0, result, 0, 8);
            Array.Copy(BitConverter.GetBytes(y), 0, result, 8, 8);
            Array.Copy(BitConverter.GetBytes(z), 0, result, 16, 8);
            return result;
        }

        private static byte[] Concat(params byte[][] parts)
        {
            int total = 0;
            foreach (var p in parts) total += p.Length;
            var result = new byte[total];
            int off = 0;
            foreach (var p in parts) { Array.Copy(p, 0, result, off, p.Length); off += p.Length; }
            return result;
        }

        private static string Hex(byte[] b) => BitConverter.ToString(b).Replace("-", "");

        // ── linear dimensions ────────────────────────────────────────────

        [Fact]
        public void UntitledFixtureHas13Dimensions()
        {
            var model = SkpFile.Open(System.IO.Path.Combine("fixtures", "Untitled.skp"));
            Assert.Equal(13, model.Dimensions.Count);
            foreach (var d in model.Dimensions)
            {
                Assert.NotNull(d.A);
                Assert.NotNull(d.B);
                var dx = d.A!.Value.X - d.B!.Value.X;
                var dy = d.A.Value.Y - d.B.Value.Y;
                var dz = d.A.Value.Z - d.B.Value.Z;
                Assert.True(Math.Sqrt(dx * dx + dy * dy + dz * dz) > 0.0); // a real measured segment
                Assert.NotNull(d.Normal);
                Assert.NotNull(d.PlaneX);
            }
        }

        [Fact]
        public void DimensionFreePointsSynthetic()
        {
            // A 5BCC record with two type-1 (free, world-space) connection points.
            byte[] PointBlock(ushort wrapTag, double x, double y, double z)
            {
                var inner = Concat(Tlv(0x5209, BitConverter.GetBytes((uint)1)), Tlv(0x520A, Vec3(x, y, z)));
                return Tlv(wrapTag, Tlv(0x5208, inner));
            }

            var body = Concat(
                PointBlock(0x5BCD, 0.0, 0.0, 0.0),
                PointBlock(0x5BCE, 100.0, 0.0, 0.0),
                Tlv(0x5BCF, Vec3(1.0, 0.0, 0.0)),   // plane x-axis
                Tlv(0x5BD0, Vec3(0.0, 0.0, 1.0)),   // plane normal
                Tlv(0x5BD2, BitConverter.GetBytes(15.5))); // offset
            var blob = Concat(new byte[8], Tlv(0x5BCC, body), new byte[8]);

            var modelDat = ChunkedBuffer.FromArray(blob);
            var dims = PagesDimensions.ParseDimensions(modelDat,
                new Dictionary<string, (double X, double Y, double Z)>(),
                new Dictionary<string, List<double>?>());

            Assert.Single(dims);
            var d = dims[0];
            Assert.Equal((0.0, 0.0, 0.0), d.A);
            Assert.Equal((100.0, 0.0, 0.0), d.B);
            Assert.Equal(15.5, d.Offset);
            Assert.Equal((1.0, 0.0, 0.0), d.PlaneX);
            Assert.Equal((0.0, 0.0, 1.0), d.Normal);
        }

        [Fact]
        public void DimensionConnectedPointResolvesThroughInstance()
        {
            // A type-2 connection (vertex id + instance id): the vertex position
            // is definition-local and must be lifted to world by the instance's
            // transform. An unresolvable reference drops the dimension (fail-safe).
            byte[] vid = { 0xAA, 0xBB, 0x01 };
            byte[] iid = { 0xCC, 0xDD, 0x02 };

            byte[] Connected(ushort wrapTag)
            {
                var idLenPrefixed = Concat(new byte[] { (byte)iid.Length }, iid);
                var refTlv = Tlv(0x53FC, Concat(Tlv(0x53FD, vid), Tlv(0x53FE, idLenPrefixed)));
                var inner = Concat(Tlv(0x5209, BitConverter.GetBytes((uint)2)), Tlv(0x520B, refTlv));
                return Tlv(wrapTag, Tlv(0x5208, inner));
            }

            byte[] Free(ushort wrapTag)
            {
                var inner = Concat(Tlv(0x5209, BitConverter.GetBytes((uint)1)), Tlv(0x520A, Vec3(0.0, 0.0, 0.0)));
                return Tlv(wrapTag, Tlv(0x5208, inner));
            }

            var body = Concat(Connected(0x5BCD), Free(0x5BCE), Tlv(0x5BD2, BitConverter.GetBytes(0.0)));
            var blob = Tlv(0x5BCC, body);

            // Identity-ish transform that translates by (10, 20, 30).
            var world = new List<double> { 1, 0, 0, 0, 1, 0, 0, 0, 1, 10.0, 20.0, 30.0, 1.0 };
            var id2pos = new Dictionary<string, (double X, double Y, double Z)> { [Hex(vid)] = (1.0, 2.0, 3.0) };
            var instWorld = new Dictionary<string, List<double>?> { [Hex(iid)] = world };

            var modelDat = ChunkedBuffer.FromArray(blob);
            var dims = PagesDimensions.ParseDimensions(modelDat, id2pos, instWorld);
            Assert.Single(dims);
            Assert.Equal((11.0, 22.0, 33.0), dims[0].A); // local + translation

            // Same record, but the vertex id is unknown: the dimension is dropped.
            var emptyDims = PagesDimensions.ParseDimensions(modelDat,
                new Dictionary<string, (double X, double Y, double Z)>(),
                new Dictionary<string, List<double>?>());
            Assert.Empty(emptyDims);
        }

        // ── scenes (pages) ───────────────────────────────────────────────

        private static byte[] PageRecord(string name, bool parallel, int[]? hiddenIds = null)
        {
            hiddenIds ??= Array.Empty<int>();
            var cam = Concat(
                Tlv(0x34BD, Vec3(100.0, -200.0, 50.0)), // eye
                Tlv(0x34BE, Vec3(0.0, 0.0, 0.0)),       // target
                Tlv(0x34BF, Vec3(0.0, 0.0, 1.0)),       // up
                Tlv(0x34C4, BitConverter.GetBytes(35.0)),                    // fov
                Tlv(0x34C2, new byte[] { (byte)(parallel ? 0 : 1) }),
                Tlv(0x34C3, BitConverter.GetBytes(240.0)));                  // ortho height
            var hiddenParts = new List<byte[]>();
            foreach (var i in hiddenIds) hiddenParts.Add(new byte[] { 1, (byte)i });
            var hidden = Concat(hiddenParts.ToArray());
            var body = Concat(
                Tlv(0x6F54, Tlv(0x6F55, System.Text.Encoding.UTF8.GetBytes(name))),
                Tlv(0x714A, Tlv(0x34BC, cam)),
                Tlv(0x7150, hidden));
            return Tlv(0x7148, body);
        }

        [Fact]
        public void ParsePagesSynthetic()
        {
            var payload = Tlv(0x6D60, Tlv(0x6D61, Concat(
                PageRecord("Planta", true, new[] { 2 }),
                PageRecord("Vista 3D", false))));
            var node = new TlvNode { Tag = "0702", Payload = payload };
            var pages = PagesDimensions.ParsePages(node);

            Assert.Equal(new[] { "Planta", "Vista 3D" }, pages.ConvertAll(p => p.Name));
            var planta = pages[0];
            Assert.True(planta.Parallel);
            Assert.Equal(240.0, planta.OrthoHeight);
            Assert.Equal((100.0, -200.0, 50.0), planta.Eye);
            Assert.Equal((0.0, 0.0, 1.0), planta.Up);
            Assert.Equal(new List<long> { 2 }, planta.HiddenLayerIds);
            Assert.False(pages[1].Parallel);
            Assert.Equal(35.0, pages[1].Fov);
        }

        [Fact]
        public void PagesAbsentIsEmpty()
        {
            Assert.Empty(PagesDimensions.ParsePages(null));
            var model = SkpFile.Open(System.IO.Path.Combine("fixtures", "SU_File.skp"));
            Assert.Empty(model.Pages);
        }
    }
}
