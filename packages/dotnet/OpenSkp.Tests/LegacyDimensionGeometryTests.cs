using System;
using System.Collections.Generic;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    public class LegacyDimensionGeometryTests
    {
        private static readonly (double X, double Y, double Z) KA = (1, 2, 3);
        private static readonly (double X, double Y, double Z) KB = (4, 6, 3);
        private static readonly (double X, double Y, double Z) KC = (11, 12, 13);
        private static readonly (double X, double Y, double Z) KD = (14, 16, 13);

        private static readonly byte[] VertexClass =
        {
            0xFF, 0xFF, 0, 0, 7, 0, (byte)'C', (byte)'V', (byte)'e', (byte)'r', (byte)'t', (byte)'e', (byte)'x'
        };

        private static byte[] U32(uint n) => BitConverter.GetBytes(n);

        private static byte[] Concat(params byte[][] parts)
        {
            int len = 0;
            foreach (var p in parts) len += p.Length;
            var res = new byte[len];
            int pos = 0;
            foreach (var p in parts)
            {
                Buffer.BlockCopy(p, 0, res, pos, p.Length);
                pos += p.Length;
            }
            return res;
        }

        private static byte[] F64s(params double[] vals)
        {
            var res = new byte[vals.Length * 8];
            for (int i = 0; i < vals.Length; i++)
                Buffer.BlockCopy(BitConverter.GetBytes(vals[i]), 0, res, i * 8, 8);
            return res;
        }

        private static byte[] Ref(uint slot, bool large = false) =>
            large
                ? Concat(new byte[] { 0xFF, 0x7F }, U32(slot))
                : new byte[] { (byte)(slot & 0xFF), (byte)((slot >> 8) & 0xFF) };

        private static byte[] PointBytes((double X, double Y, double Z) p) =>
            F64s(p.X, p.Y, p.Z);

        private static byte[] Connection((double X, double Y, double Z) p) =>
            Concat(U32(1), U32(4), PointBytes(p));

        private static int Locate(List<byte> bytes, byte[] needle)
        {
            for (int i = 0; i + needle.Length <= bytes.Count; i++)
            {
                bool match = true;
                for (int j = 0; j < needle.Length; j++)
                {
                    if (bytes[i + j] != needle[j])
                    {
                        match = false;
                        break;
                    }
                }
                if (match) return i;
            }
            throw new InvalidOperationException("dimension test record not found");
        }

        private static List<byte> Fixture()
        {
            var builder = SkpCreate.NewFile();
            builder.AddDimension(KA, KB, -2.0);
            builder.AddDimension(KC, KD, 3.0);
            builder.AddConstructionPoint((101, 102, 103));
            return builder.ToBytes().ToList();
        }

        private static uint FontSlot(List<byte> bytes)
        {
            int at = Locate(bytes, Connection(KC)) - 7;
            return (uint)(bytes[at] | (bytes[at + 1] << 8));
        }

        private static byte[] Vertex((double X, double Y, double Z) p, byte[] tag) =>
            Concat(tag, new byte[] { 0, 0, 0 }, PointBytes(p));

        private static void Attach(List<byte> bytes, (double X, double Y, double Z) original, byte[] obj)
        {
            int at = Locate(bytes, Connection(original));
            bytes[at] = 2;
            for (int i = at + 8; i < at + 32; i++) bytes[i] = 0;
            bytes.RemoveRange(at + 32, 2);
            bytes.InsertRange(at + 32, obj);
        }

        private static void ExpectPoint((double X, double Y, double Z)? actual, (double X, double Y, double Z) expected)
        {
            Assert.True(actual.HasValue);
            Assert.Equal(expected.X, actual!.Value.X, 9);
            Assert.Equal(expected.Y, actual.Value.Y, 9);
            Assert.Equal(expected.Z, actual.Value.Z, 9);
        }

        private static void ExpectTail(SkpModel model)
        {
            var cp = Assert.Single(model.Root.ConstructionPoints);
            Assert.Equal((101.0, 102.0, 103.0), cp.Position);
        }

        [Fact]
        public void FreeEndpointsAndFrameSurviveParsing()
        {
            var model = SkpFile.Parse(Fixture().ToArray());
            Assert.Equal(2, model.Root.Dimensions.Count);
            var first = model.Root.Dimensions[0];
            ExpectPoint(first.A, KA);
            ExpectPoint(first.B, KB);
            Assert.Equal(-2.0, first.Offset);
            ExpectPoint(first.PlaneX, (1, 0, 0));
            ExpectPoint(first.Normal, (0, 0, 1));
            Assert.Null(first.OffsetVector);
            ExpectPoint(model.Root.Dimensions[1].A, KC);
            ExpectPoint(model.Root.Dimensions[1].B, KD);
            ExpectTail(model);
        }

        [Theory]
        [InlineData(false)]
        [InlineData(true)]
        public void InlineVerticesAndLaterBackrefsKeepArchiveAligned(bool large)
        {
            var bytes = Fixture();
            uint font = FontSlot(bytes);
            Attach(bytes, KD, Ref(font + 3, large));
            Attach(bytes, KC, Ref(font + 2, large));
            Attach(bytes, KB, Vertex(KB, Ref((large ? 0x80000000u : 0x8000u) | (font + 1), large)));
            Attach(bytes, KA, Vertex(KA, VertexClass));
            var model = SkpFile.Parse(bytes.ToArray());
            Assert.Equal(2, model.Root.Dimensions.Count);
            foreach (var dim in model.Root.Dimensions)
            {
                ExpectPoint(dim.A, KA);
                ExpectPoint(dim.B, KB);
            }
            ExpectTail(model);
        }

        [Fact]
        public void ForwardVertexReferencesResolveAfterArchiveWalk()
        {
            var bytes = Fixture();
            uint font = FontSlot(bytes);
            Attach(bytes, KC, Vertex(KC, Ref(0x8000u | (font + 1))));
            Attach(bytes, KB, Vertex(KB, VertexClass));
            Attach(bytes, KA, Ref(font + 4, true));
            var model = SkpFile.Parse(bytes.ToArray());
            Assert.Equal(2, model.Root.Dimensions.Count);
            ExpectPoint(model.Root.Dimensions[0].A, KC);
            ExpectPoint(model.Root.Dimensions[0].B, KB);
            ExpectPoint(model.Root.Dimensions[1].A, KC);
            ExpectTail(model);
        }

        [Theory]
        [InlineData(1u, false)]
        [InlineData(1u, true)]
        [InlineData(2u, false)]
        [InlineData(2u, true)]
        [InlineData(3u, false)]
        [InlineData(3u, true)]
        public void PlaneAxesPreserveSignedOffsetWithReversedEndpoints(uint axis, bool reversed)
        {
            var builder = SkpCreate.NewFile();
            var p1 = reversed ? KB : KA;
            var p2 = reversed ? KA : KB;
            builder.AddDimension(p1, p2, -2.0);
            var bytes = builder.ToBytes().ToList();
            int at = Locate(bytes, Connection(p2)) + 32 + 2 + 10;
            var frame = Concat(F64s(0, -1, 0, 1, 0, 0), U32(axis));
            for (int i = 0; i < frame.Length; i++) bytes[at + i] = frame[i];
            var model = SkpFile.Parse(bytes.ToArray());
            var dim = Assert.Single(model.Root.Dimensions);
            ExpectPoint(dim.Normal, (0, -1, 0));
            ExpectPoint(dim.PlaneX, (1, 0, 0));
            if (axis == 1) ExpectPoint(dim.OffsetVector, (0, 0, -2));
            else if (axis == 2) ExpectPoint(dim.OffsetVector, (2, 0, 0));
            else Assert.Null(dim.OffsetVector);
        }

        [Fact]
        public void NestedPathsAreConsumedWithoutPublishingLocalCoordinates()
        {
            var bytes = Fixture();
            int at = Locate(bytes, Connection(KA)) + 32 + 2 + 2;
            bytes[at] = 1;
            bytes.InsertRange(at + 4, Ref(1234, true));
            var model = SkpFile.Parse(bytes.ToArray());
            Assert.Equal(2, model.Root.Dimensions.Count);
            Assert.Null(model.Root.Dimensions[0].A);
            ExpectPoint(model.Root.Dimensions[0].B, KB);
            ExpectPoint(model.Root.Dimensions[1].A, KC);
            ExpectTail(model);
        }

        [Fact]
        public void VectorOffsetsSurviveRootAndNestedRoundTrips()
        {
            var samples = new ((double X, double Y, double Z) A, (double X, double Y, double Z) B, (double X, double Y, double Z) Offset, (double X, double Y, double Z) Expected)[]
            {
                ((1, 2, 3), (11, 2, 3), (0, 0, 4), (0, 0, 4)),
                ((11, 2, 3), (1, 2, 3), (0, 0, -4), (0, 0, -4)),
                ((1, 2, 3), (1, 2, 13), (-4, 0, 0), (-4, 0, 0)),
                ((1, 2, 3), (4, 6, 8), (-4, 3, 0), (-4, 3, 0)),
                ((1, 2, 3), (11, 2, 3), (7, -4, 0), (0, -4, 0)),
                ((1, 2, 3), (1, 2, 13), (0, 0, 0), (0, 0, 0)),
                ((1, 2, 3), (11, 2, 3), (7, 0, 0), (0, 0, 0)),
                ((1, 2, 3), (4, 6, 8), (3, 4, 5), (0, 0, 0)),
                ((1, 2, 3), (11, 2, 3), (0, 0, 1e-12), (0, 0, 1e-12)),
            };
            var builder = SkpCreate.NewFile();
            ComponentDefinitionBuilder defn;
            using (defn = builder.AddComponentDefinition("Dimensions"))
            {
                foreach (var s in samples) defn.AddDimension(s.A, s.B, s.Offset);
            }
            builder.AddInstance(defn);
            foreach (var s in samples) builder.AddDimension(s.A, s.B, s.Offset);

            var model = SkpFile.Parse(builder.ToBytes());
            Assert.Equal(samples.Length, model.Root.Dimensions.Count);
            var defDims = Assert.Single(model.Definitions.Values).Dimensions;
            Assert.Equal(samples.Length, defDims.Count);
            foreach (var dims in new[] { model.Root.Dimensions, defDims })
            {
                for (int i = 0; i < samples.Length; i++)
                {
                    ExpectPoint(dims[i].A, samples[i].A);
                    ExpectPoint(dims[i].B, samples[i].B);
                    ExpectPoint(dims[i].OffsetVector, samples[i].Expected);
                }
            }
        }

        [Fact]
        public void InvalidVectorDimensionDoesNotCorruptFollowingRecords()
        {
            var builder = SkpCreate.NewFile();
            ComponentDefinitionBuilder defn;
            using (defn = builder.AddComponentDefinition("Dimensions"))
            {
                foreach (double bad in new[] { double.PositiveInfinity, double.NaN })
                {
                    Assert.Throws<SkpWriteException>(() => defn.AddDimension(KA, KB, (bad, 0, 0)));
                    Assert.Throws<SkpWriteException>(() => builder.AddDimension((bad, 0, 0), KB, (0, 0, 1)));
                }
                Assert.Throws<SkpWriteException>(() => defn.AddDimension(KA, KA, (0, 0, 1)));
                defn.AddDimension(KA, KB, (0, 0, 2));
            }
            builder.AddInstance(defn);
            foreach (double bad in new[] { double.PositiveInfinity, double.NaN })
            {
                Assert.Throws<SkpWriteException>(() => builder.AddDimension((bad, 0, 0), KB, (0, 0, 1)));
            }
            builder.AddDimension(KA, KB, (0, 0, 3));

            var model = SkpFile.Parse(builder.ToBytes());
            ExpectPoint(Assert.Single(model.Root.Dimensions).OffsetVector, (0, 0, 3));
            ExpectPoint(Assert.Single(Assert.Single(model.Definitions.Values).Dimensions).OffsetVector, (0, 0, 2));
        }
    }
}
