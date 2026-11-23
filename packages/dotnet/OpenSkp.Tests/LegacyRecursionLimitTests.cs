using System;
using System.Collections.Generic;
using System.Text;
using OpenSkp;
using Xunit;

namespace OpenSkp.Tests
{
    public class LegacyRecursionLimitTests
    {
        private const int MaxLegacyDepth = 64;

        private static void AddU16(List<byte> outBytes, int x)
        {
            outBytes.Add((byte)(x & 0xFF));
            outBytes.Add((byte)((x >> 8) & 0xFF));
        }

        private static void AddU32(List<byte> outBytes, uint x)
        {
            outBytes.Add((byte)(x & 0xFF));
            outBytes.Add((byte)((x >> 8) & 0xFF));
            outBytes.Add((byte)((x >> 16) & 0xFF));
            outBytes.Add((byte)((x >> 24) & 0xFF));
        }

        private static void AddWStr(List<byte> outBytes, string s)
        {
            outBytes.Add(0xFF);
            outBytes.Add(0xFE);
            outBytes.Add(0xFF);
            outBytes.Add((byte)s.Length);
            outBytes.AddRange(Encoding.Unicode.GetBytes(s));
        }

        private static void AddClassDecl(List<byte> outBytes, string name, int schema = 0)
        {
            AddU16(outBytes, 0xFFFF);
            AddU16(outBytes, schema);
            AddU16(outBytes, name.Length);
            outBytes.AddRange(Encoding.ASCII.GetBytes(name));
        }

        private static int FindAscii(byte[] data, string needle)
        {
            var nb = Encoding.ASCII.GetBytes(needle);
            for (int i = 0; i + nb.Length <= data.Length; i++)
            {
                bool match = true;
                for (int j = 0; j < nb.Length; j++)
                {
                    if (data[i + j] != nb[j])
                    {
                        match = false;
                        break;
                    }
                }
                if (match) return i;
            }
            throw new InvalidOperationException($"Needle {needle} not found");
        }

        private static byte[] Splice(byte[] raw, int pos, int replaceLen, List<byte> mid)
        {
            var res = new byte[raw.Length - replaceLen + mid.Count];
            Buffer.BlockCopy(raw, 0, res, 0, pos);
            mid.CopyTo(res, pos);
            Buffer.BlockCopy(raw, pos + replaceLen, res, pos + mid.Count, raw.Length - (pos + replaceLen));
            return res;
        }

        private static byte[] MakeNestedObjectSkp(int totalDepth, bool onLayer)
        {
            var builder = SkpCreate.NewFile();
            builder.AddConstructionPoint((1.0, 2.0, 3.0));
            var raw = builder.ToBytes();
            string tag = onLayer ? "CLayer" : "CConstructionPoint";
            int attrClsSlot = onLayer ? 11 : 13;
            int pos = FindAscii(raw, tag) + tag.Length;
            if (totalDepth <= 1) return raw;
            int k = totalDepth - 1;
            var chain = new List<byte>();
            AddClassDecl(chain, "CAttributeContainer");
            for (int i = 0; i < k - 1; i++) AddU16(chain, 0x8000 | attrClsSlot);
            AddU16(chain, 0);
            for (int i = 0; i < k; i++)
            {
                chain.Add(0);
                AddU16(chain, 0);
            }
            return Splice(raw, pos, 2, chain);
        }

        private static byte[] MakeNestedAttrArraySkp(int arrayDepth)
        {
            var builder = SkpCreate.NewFile();
            builder.AddConstructionPoint((1.0, 2.0, 3.0));
            var raw = builder.ToBytes();
            int pos = FindAscii(raw, "CConstructionPoint") + "CConstructionPoint".Length;
            var payload = new List<byte>();
            AddClassDecl(payload, "CAttributeContainer");
            AddU16(payload, 0);
            payload.Add(0);
            AddClassDecl(payload, "CAttributeNamed");
            AddU16(payload, 0);
            payload.Add(0);
            AddU32(payload, 0);
            AddWStr(payload, "Dict");
            AddWStr(payload, "k");
            for (int i = 0; i < arrayDepth; i++)
            {
                payload.Add(0x0B);
                AddU32(payload, 1);
            }
            payload.Add(0x04);
            AddU32(payload, 7);
            AddWStr(payload, "");
            AddU32(payload, 0);
            AddU16(payload, 0);
            return Splice(raw, pos, 2, payload);
        }

        [Theory]
        [InlineData(1, false)]
        [InlineData(10, false)]
        [InlineData(MaxLegacyDepth, false)]
        [InlineData(1, true)]
        [InlineData(10, true)]
        [InlineData(MaxLegacyDepth, true)]
        public void ClassicObjectNestingWithinLimitParses(int depth, bool onLayer)
        {
            var model = SkpFile.Parse(MakeNestedObjectSkp(depth, onLayer));
            Assert.Equal(0, model.UnreadRootEntities);
            Assert.Single(model.Root.ConstructionPoints);
        }

        [Theory]
        [InlineData(MaxLegacyDepth + 1, false)]
        [InlineData(5000, false)]
        [InlineData(MaxLegacyDepth + 1, true)]
        [InlineData(5000, true)]
        public void ClassicObjectNestingAboveLimitThrowsSkpParseException(int depth, bool onLayer)
        {
            var ex = Assert.Throws<SkpParseException>(() => SkpFile.Parse(MakeNestedObjectSkp(depth, onLayer)));
            Assert.Equal("legacy_walk", ex.Stage);
            Assert.Contains("depth", ex.Message, StringComparison.OrdinalIgnoreCase);
        }

        [Theory]
        [InlineData(1)]
        [InlineData(10)]
        [InlineData(MaxLegacyDepth)]
        public void ClassicAttrArrayNestingWithinLimitParses(int depth)
        {
            var model = SkpFile.Parse(MakeNestedAttrArraySkp(depth));
            Assert.Equal(0, model.UnreadRootEntities);
            Assert.Single(model.Root.ConstructionPoints);
        }

        [Theory]
        [InlineData(MaxLegacyDepth + 1)]
        [InlineData(5000)]
        public void ClassicAttrArrayNestingAboveLimitThrowsSkpParseException(int depth)
        {
            var ex = Assert.Throws<SkpParseException>(() => SkpFile.Parse(MakeNestedAttrArraySkp(depth)));
            Assert.Equal("legacy_walk", ex.Stage);
            Assert.Contains("depth", ex.Message, StringComparison.OrdinalIgnoreCase);
        }
    }
}
