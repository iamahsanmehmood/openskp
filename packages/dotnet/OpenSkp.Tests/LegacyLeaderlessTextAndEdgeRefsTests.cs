using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    public class LegacyLeaderlessTextAndEdgeRefsTests
    {
        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        [Fact]
        public void LeaderlessTextBeforeSharedVertexEdgesMatchesSdk()
        {
            var path = FixturePath("sdk_saved_leaderless_text_edges_2020.skp");
            var logs = new List<(SkpLogLevel Level, string Message)>();
            var options = new SkpParseOptions
            {
                OnLog = (level, msg) => logs.Add((level, msg)),
            };

            var model = SkpFile.Open(path, options);
            var scene = SkpFile.BuildScene(path, options);
            var instanced = SkpFile.BuildInstancedScene(path, options);

            Assert.Single(model.Definitions);
            var post = model.Definitions.Values.Single();
            Assert.Equal("Post", post.Name);
            Assert.False(post.IsGroup);
            Assert.Empty(model.Root.Faces);
            Assert.Equal(3, model.Root.Edges.Count);
            Assert.Single(model.Root.Instances);
            Assert.Equal(new[] { "Screen title", "Callout" }, model.Root.Texts.Select(t => t.Text).ToArray());
            Assert.Equal(0, model.UnreadRootEntities);
            Assert.Single(scene.SurfacePrimitives);
            Assert.Single(instanced.MeshResources);
            Assert.DoesNotContain(logs, entry => entry.Level == SkpLogLevel.Warning);
        }

        [Fact]
        public void RootEdgeWithNonVertexRefReportsPartialRead()
        {
            var raw = File.ReadAllBytes(FixturePath("sdk_saved_leaderless_text_edges_2020.skp"));
            Assert.Equal(0x31, raw[5323]);
            Assert.Equal(0x00, raw[5324]);
            raw[5323] = 0x2F; // point 2nd root edge v1 at 1st root CEdge

            var logs = new List<(SkpLogLevel Level, string Message)>();
            var options = new SkpParseOptions
            {
                OnLog = (level, msg) => logs.Add((level, msg)),
            };

            var model = SkpFile.Parse(raw, options);
            var scene = SkpFile.BuildScene(raw);
            var instanced = SkpFile.BuildInstancedScene(raw);

            Assert.Single(model.Root.Instances);
            Assert.Equal(new[] { "Screen title", "Callout" }, model.Root.Texts.Select(t => t.Text).ToArray());
            Assert.Single(model.Root.Edges);
            Assert.Equal(2, model.UnreadRootEntities);
            Assert.Single(scene.SurfacePrimitives);
            Assert.Single(instanced.MeshResources);
            Assert.Contains(logs, entry => entry.Level == SkpLogLevel.Warning && entry.Message.Contains("2 unread"));
        }

        [Fact]
        public void DefinitionEdgeWithNonVertexRefThrowsSkpParseException()
        {
            var raw = File.ReadAllBytes(FixturePath("sdk_saved_leaderless_text_edges_2020.skp"));
            Assert.Equal(0x14, raw[4039]);
            Assert.Equal(0x00, raw[4040]);
            raw[4039] = 0x11; // point 2nd definition edge v1 at 1st definition CEdge

            Assert.Throws<SkpParseException>(() => SkpFile.Parse(raw));
            Assert.Throws<SkpParseException>(() => SkpFile.BuildScene(raw));
            Assert.Throws<SkpParseException>(() => SkpFile.BuildInstancedScene(raw));
        }
    }
}
