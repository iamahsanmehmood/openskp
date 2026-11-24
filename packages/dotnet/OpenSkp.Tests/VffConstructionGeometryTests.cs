using System;
using System.Collections.Generic;
using OpenSkp;
using Xunit;

namespace OpenSkp.Tests
{
    public class VffConstructionGeometryTests
    {
        private static byte[] Doubles(params double[] values)
        {
            var bytes = new byte[values.Length * 8];
            for (int i = 0; i < values.Length; i++)
            {
                var bits = BitConverter.GetBytes(values[i]);
                if (!BitConverter.IsLittleEndian) Array.Reverse(bits);
                Array.Copy(bits, 0, bytes, i * 8, 8);
            }
            return bytes;
        }

        private static TlvNode Node(string tag, params TlvNode[] children) =>
            new TlvNode { Tag = tag, Children = new List<TlvNode>(children) };

        private static TlvNode Leaf(string tag, params double[] values) =>
            new TlvNode { Tag = tag, Payload = Doubles(values) };

        [Fact]
        public void VffGuideContainersAreTraversed()
        {
            foreach (var tag in new[] { "9113", "9213", "6942", "6C42", "6842" })
            {
                Assert.Contains(tag, Tlv.ContainerTags);
            }
        }

        [Fact]
        public void ExtractGeometryReadsBoundedAndInfiniteGuideLinesAndPoint()
        {
            var root = new List<TlvNode>
            {
                Node("9113",
                    Node("6942", Leaf("6A42", 2, 3, 4, 0, 1, 0, -2, 7)),
                    Node("6942", Leaf("6A42", 0, 0, 4, 0, 0, 1, -1e30, 1e30))),
                Node("9213", Node("6C42",
                    Leaf("6D42", 8, 9, 10), Leaf("6E42", 0, 0, 0))),
            };
            var builder = new GeometryBuilder();
            Geometry.ExtractGeometryFromNodes(root, builder);

            Assert.Equal(2, builder.ConstructionLines.Count);
            Assert.Equal((2.0, 3.0, 4.0), builder.ConstructionLines[0].Point);
            Assert.Equal((0.0, 1.0, 0.0), builder.ConstructionLines[0].Direction);
            Assert.Equal((2.0, 1.0, 4.0), builder.ConstructionLines[0].Start);
            Assert.Equal((2.0, 10.0, 4.0), builder.ConstructionLines[0].End);
            Assert.Null(builder.ConstructionLines[1].Start);
            Assert.Null(builder.ConstructionLines[1].End);
            Assert.False(builder.ConstructionLines[0].Hidden);
            Assert.Equal("", builder.ConstructionLines[0].Layer);
            var cp = Assert.Single(builder.ConstructionPoints);
            Assert.Equal((8.0, 9.0, 10.0), cp.Position);
            Assert.False(cp.Hidden);
            Assert.Equal("", cp.Layer);
        }

        [Fact]
        public void ReadsVffGuideHiddenFlagAndLayer()
        {
            TlvNode Drawbase(byte flags, byte? layerId = null)
            {
                var children = new List<TlvNode>();
                if (layerId.HasValue)
                {
                    children.Add(new TlvNode { Tag = "D207", Payload = new[] { layerId.Value } });
                }
                children.Add(new TlvNode { Tag = "D307", Payload = new[] { flags } });
                return Node("6842", new TlvNode { Tag = "D007", Children = children });
            }

            var root = new List<TlvNode>
            {
                Node("9213",
                    Node("6C42", Drawbase(0x07), Leaf("6D42", 1, 2, 3)),
                    Node("6C42", Drawbase(0x06, 4), Leaf("6D42", 4, 5, 6))),
                Node("9113",
                    Node("6942", Drawbase(0x07, 3), Leaf("6A42", 0, 0, 0, 1, 0, 0, 0, 12))),
            };
            var builder = new GeometryBuilder();
            Geometry.ExtractGeometryFromNodes(root, builder);

            Assert.Equal(2, builder.ConstructionPoints.Count);
            Assert.True(builder.ConstructionPoints[0].Hidden);
            Assert.Equal("", builder.ConstructionPoints[0].Layer);
            Assert.False(builder.ConstructionPoints[1].Hidden);
            Assert.Equal("4", builder.ConstructionPoints[1].Layer);

            var cl = Assert.Single(builder.ConstructionLines);
            Assert.True(cl.Hidden);
            Assert.Equal("3", cl.Layer);
        }

        [Fact]
        public void IncompleteGuidePayloadsAreIgnored()
        {
            var root = new List<TlvNode>
            {
                Node("6942", Leaf("6A42", 1, 2, 3)),
                Node("6C42", Leaf("6D42", 1, 2)),
            };
            var builder = new GeometryBuilder();
            Geometry.ExtractGeometryFromNodes(root, builder);
            Assert.Empty(builder.ConstructionLines);
            Assert.Empty(builder.ConstructionPoints);
        }
    }
}
