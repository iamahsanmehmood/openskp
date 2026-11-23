using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using OpenSkp;
using Xunit;

namespace OpenSkp.Tests
{
    public class LazyDefinitionsTests
    {
        private static byte[] MakeTlv(string tagHex, byte[] payload)
        {
            var outBytes = new byte[6 + payload.Length];
            outBytes[0] = Convert.ToByte(tagHex.Substring(0, 2), 16);
            outBytes[1] = Convert.ToByte(tagHex.Substring(2, 2), 16);
            uint len = (uint)payload.Length;
            outBytes[2] = (byte)(len & 0xFF);
            outBytes[3] = (byte)((len >> 8) & 0xFF);
            outBytes[4] = (byte)((len >> 16) & 0xFF);
            outBytes[5] = (byte)((len >> 24) & 0xFF);
            Buffer.BlockCopy(payload, 0, outBytes, 6, payload.Length);
            return outBytes;
        }

        private static byte[] Concat(params byte[][] parts)
        {
            int total = parts.Sum(p => p.Length);
            var res = new byte[total];
            int offset = 0;
            foreach (var p in parts)
            {
                Buffer.BlockCopy(p, 0, res, offset, p.Length);
                offset += p.Length;
            }
            return res;
        }

        private static byte[] DefinitionRecord(byte seed, string name)
        {
            var guid = new byte[16];
            for (int i = 0; i < 16; i++) guid[i] = seed;
            return MakeTlv("7C15", Concat(MakeTlv("7D15", guid), MakeTlv("7E15", Encoding.UTF8.GetBytes(name))));
        }

        private static readonly byte[] LayersRecord =
            MakeTlv("993A", MakeTlv("8C3C", MakeTlv("8D3C", Encoding.UTF8.GetBytes("Layer0"))));

        private static byte[] ModelBytes(params byte[][] defs)
        {
            return Concat(
                LayersRecord,
                MakeTlv("F901", MakeTlv("7017", MakeTlv("7117", Concat(defs)))),
                MakeTlv("0802", new byte[] { 1, 2, 3 })
            );
        }

        private static List<Tlv.TopLevelRecord> Collect(byte[] data)
        {
            var buf = ChunkedBuffer.FromArray(data);
            return Tlv.IterTopLevelLazy(buf, 0, buf.Length).ToList();
        }

        private static List<TlvNode> DefinitionsFromFullParse(byte[] data)
        {
            var buf = ChunkedBuffer.FromArray(data);
            var found = new List<TlvNode>();
            void Walk(List<TlvNode> nodes)
            {
                foreach (var n in nodes)
                {
                    if (n.Tag == "7C15") found.Add(n);
                    else Walk(n.Children);
                }
            }
            Walk(Tlv.ParseRecursive(buf, 0, buf.Length));
            return found;
        }

        private static void AssertNodesEqual(TlvNode a, TlvNode b)
        {
            Assert.Equal(a.Offset, b.Offset);
            Assert.Equal(a.Tag, b.Tag);
            Assert.Equal(a.Size, b.Size);
            Assert.Equal(a.Payload, b.Payload);
            Assert.Equal(a.Children.Count, b.Children.Count);
            for (int i = 0; i < a.Children.Count; i++)
            {
                AssertNodesEqual(a.Children[i], b.Children[i]);
            }
        }

        [Fact]
        public void YieldsEach7C15AsItsOwnRecordInFileOrder()
        {
            var data = ModelBytes(DefinitionRecord(1, "Porta"), DefinitionRecord(2, "Gaveta"), DefinitionRecord(3, "Prateleira"));
            var records = Collect(data);

            Assert.Equal(new[] { "993A", "7C15", "7C15", "7C15", "0802" }, records.Select(r => r.Node.Tag));
            Assert.Equal(new[] { 0, 1, 2, 3, 4 }, records.Select(r => r.Index));
            Assert.All(records, r => Assert.Equal(5, r.Total));
        }

        [Fact]
        public void NeverYieldsPassThroughWrappers()
        {
            var records = Collect(ModelBytes(DefinitionRecord(1, "A"), DefinitionRecord(2, "B")));
            var tags = new HashSet<string>();
            void Walk(TlvNode n)
            {
                tags.Add(n.Tag);
                foreach (var c in n.Children) Walk(c);
            }
            foreach (var r in records) Walk(r.Node);

            Assert.DoesNotContain("F901", tags);
            Assert.DoesNotContain("7017", tags);
            Assert.DoesNotContain("7117", tags);
        }

        [Fact]
        public void StreamedDefinitionsMatchFullParse()
        {
            var data = ModelBytes(DefinitionRecord(1, "Porta"), DefinitionRecord(2, "Gaveta"), DefinitionRecord(3, "Prateleira"));
            var streamed = Collect(data).Select(r => r.Node).Where(n => n.Tag == "7C15").ToList();
            var full = DefinitionsFromFullParse(data);

            Assert.Equal(full.Count, streamed.Count);
            for (int i = 0; i < full.Count; i++)
            {
                AssertNodesEqual(full[i], streamed[i]);
            }
        }

        [Fact]
        public void LeavesEveryOtherContainerWhole()
        {
            var first = Collect(ModelBytes(DefinitionRecord(1, "A"))).First();
            Assert.Equal("993A", first.Node.Tag);
            Assert.Equal(new[] { "8C3C" }, first.Node.Children.Select(c => c.Tag));
            Assert.Equal(new[] { "8D3C" }, first.Node.Children[0].Children.Select(c => c.Tag));
        }

        [Fact]
        public void UnwrapsLoneF401BeforeStreamingDefinitions()
        {
            var inner = ModelBytes(DefinitionRecord(1, "A"), DefinitionRecord(2, "B"));
            var records = Collect(MakeTlv("F401", inner));

            Assert.Equal(new[] { "993A", "7C15", "7C15", "0802" }, records.Select(r => r.Node.Tag));
        }

        [Fact]
        public void EmptyDefinitionsChainYieldsNothingForIt()
        {
            var records = Collect(ModelBytes());
            Assert.Equal(new[] { "993A", "0802" }, records.Select(r => r.Node.Tag));
        }

        [Fact]
        public void RejectsDeeplyNestedPassThroughWrappers()
        {
            byte[] payload = Array.Empty<byte>();
            for (int i = 0; i < 100; i++)
            {
                payload = MakeTlv("F901", payload);
            }
            var ex = Assert.Throws<SkpParseException>(() => Collect(MakeTlv("F401", payload)));
            Assert.Equal("tlv_walk", ex.Stage);
        }
    }
}
