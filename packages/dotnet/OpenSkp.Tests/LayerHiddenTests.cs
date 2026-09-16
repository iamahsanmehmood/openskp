using System.Collections.Generic;
using System.Text;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>
    /// VFF (2021+) layers derive their COLOR from Layer_&lt;name&gt;-prefixed
    /// materials, which carry no visibility flag of their own - real
    /// visibility lives on the model.dat layer manager's own 993A/8C3C node,
    /// as a single-byte 8E3C child sibling to the already-read DC05 (id) and
    /// 8D3C (name): 1 = hidden, 0 = visible. Before this fix, CollectLayers
    /// never read 8E3C at all, so every VFF layer's hidden state silently
    /// defaulted to visible regardless of the file's real Tags panel state.
    /// Mirrors Python's own collect_layers/TestVffLayerHidden exactly
    /// (openskp#285), byte shapes verified against a real production file's
    /// own Tags panel (FrameSmart pipeline report, 2026-09-08).
    /// </summary>
    public class LayerHiddenTests
    {
        private static TlvNode LayerNode(long id, string name, bool? hidden)
        {
            var children = new List<TlvNode>
            {
                new TlvNode { Tag = "DC05", Payload = new byte[] { (byte)id } },
                new TlvNode { Tag = "8D3C", Payload = Encoding.UTF8.GetBytes(name) },
            };
            if (hidden.HasValue)
            {
                children.Add(new TlvNode { Tag = "8E3C", Payload = new byte[] { (byte)(hidden.Value ? 1 : 0) } });
            }
            return new TlvNode { Tag = "8C3C", Children = children };
        }

        [Fact]
        public void CollectLayers_ReadsHiddenAndVisibleLayersCorrectly()
        {
            var root = new TlvNode
            {
                Tag = "993A",
                Children = new List<TlvNode>
                {
                    LayerNode(5, "wall_external_cladding_1", true),
                    LayerNode(6, "wall", false),
                },
            };

            var layerIdToName = new Dictionary<long, string>();
            var layerHidden = new Dictionary<string, bool>();
            Geometry.CollectLayers(new List<TlvNode> { root }, layerIdToName, layerHidden);

            Assert.Equal("wall_external_cladding_1", layerIdToName[5]);
            Assert.Equal("wall", layerIdToName[6]);
            Assert.True(layerHidden["wall_external_cladding_1"]);
            Assert.False(layerHidden["wall"]);
        }

        [Fact]
        public void CollectLayers_LeavesLayerHiddenUnsetWhenNo8E3CTag()
        {
            // A layer with no 8E3C child at all (e.g. an older file
            // predating the flag) leaves the dictionary untouched rather
            // than guessing a value - the caller's own "default to
            // visible" fallback (Core.FullParse) handles the gap.
            var root = new TlvNode
            {
                Tag = "993A",
                Children = new List<TlvNode> { LayerNode(1, "Layer0", null) },
            };

            var layerIdToName = new Dictionary<long, string>();
            var layerHidden = new Dictionary<string, bool>();
            Geometry.CollectLayers(new List<TlvNode> { root }, layerIdToName, layerHidden);

            Assert.Equal("Layer0", layerIdToName[1]);
            Assert.False(layerHidden.ContainsKey("Layer0"));
        }

        [Fact]
        public void CollectLayers_LayerHiddenParameterIsOptional()
        {
            // Backward-compatible default: existing callers that only care
            // about layerIdToName don't need to pass a layerHidden dict.
            var root = new TlvNode
            {
                Tag = "993A",
                Children = new List<TlvNode> { LayerNode(1, "Layer0", true) },
            };

            var layerIdToName = new Dictionary<long, string>();
            Geometry.CollectLayers(new List<TlvNode> { root }, layerIdToName);

            Assert.Equal("Layer0", layerIdToName[1]);
        }
    }
}
