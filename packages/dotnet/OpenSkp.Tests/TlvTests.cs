using System.Linq;
using OpenSkp;
using Xunit;

namespace OpenSkp.Tests
{
    /// <summary>Regression coverage for a real bug: Tlv.ParseRecursive's and
    /// the private FlatHeaders' (exercised here via the public
    /// IterTopLevelLazy) "is there room for one more 6-byte header" loop
    /// guard used `pos &lt; end - 6` instead of the correct `pos &lt;= end - 6`
    /// (equivalently `pos + 6 &lt;= end`) - a header occupying exactly the
    /// last 6 bytes of [start, end) is a real, valid record, not corrupt
    /// data, but the off-by-one silently dropped it with no error.
    /// FlatHeaders backs IterTopLevelLazy, the lazy scanner real
    /// 100k+-definition files depend on, so this could silently drop a
    /// trailing top-level definition, not just a nested child.</summary>
    public class TlvTests
    {
        private static byte[] Tlv(string tagHex, byte[] payload)
        {
            byte tag0 = (byte)System.Convert.ToInt32(tagHex.Substring(0, 2), 16);
            byte tag1 = (byte)System.Convert.ToInt32(tagHex.Substring(2, 2), 16);
            var size = System.BitConverter.GetBytes((uint)payload.Length);
            var result = new byte[6 + payload.Length];
            result[0] = tag0;
            result[1] = tag1;
            size.CopyTo(result, 2);
            payload.CopyTo(result, 6);
            return result;
        }

        [Fact]
        public void ParseRecursiveKeepsAHeaderThatExactlyFillsTheRange()
        {
            var data = Tlv("0300", System.Array.Empty<byte>());
            Assert.Equal(6, data.Length);
            var buffer = ChunkedBuffer.FromArray(data);

            var nodes = OpenSkp.Tlv.ParseRecursive(buffer, 0, buffer.Length);

            Assert.Single(nodes);
            Assert.Equal("0300", nodes[0].Tag);
        }

        [Fact]
        public void IterTopLevelLazyKeepsATopLevelRecordThatExactlyFillsTheRange()
        {
            var data = Tlv("0300", System.Array.Empty<byte>());
            Assert.Equal(6, data.Length);
            var buffer = ChunkedBuffer.FromArray(data);

            var items = OpenSkp.Tlv.IterTopLevelLazy(buffer, 0, buffer.Length).ToList();

            Assert.Single(items);
            Assert.Equal(0, items[0].Index);
            Assert.Equal(1, items[0].Total);
            Assert.Equal("0300", items[0].Node.Tag);
        }
    }
}
