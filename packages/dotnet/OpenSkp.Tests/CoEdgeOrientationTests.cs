using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>
    /// Regression coverage for the CoEdge orientation contract (+1 = same
    /// direction as the edge's own V1Id->V2Id, -1 = reversed). Both the
    /// legacy (pre-2021 MFC) and modern (VFF) readers used to leak
    /// SketchUp's raw storage bit (0 = forward, 1 = reversed) straight
    /// through instead of normalizing it - counting coedges or checking
    /// their type does not catch this, since a face with every coedge
    /// reversed still has the same edge count and loop length, just the
    /// wrong winding. The only thing that catches it is checking that
    /// consecutive coedges in a loop actually connect head-to-tail.
    /// </summary>
    public class CoEdgeOrientationTests
    {
        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        private static void AssertConnectedNormalizedLoops(IEnumerable<Definition> definitions)
        {
            foreach (var def in definitions)
            {
                foreach (var face in def.Faces.Values)
                {
                    foreach (var loop in face.Loops)
                    {
                        Assert.True(loop.Count > 0, "empty loop");
                        int n = loop.Count;
                        for (int i = 0; i < n; i++)
                        {
                            var (edgeId, orientation) = loop[i];
                            var (nextEdgeId, nextOrientation) = loop[(i + 1) % n];
                            Assert.True(orientation == 1 || orientation == -1);
                            Assert.True(def.Edges.TryGetValue(edgeId, out var edge));
                            Assert.True(def.Edges.TryGetValue(nextEdgeId, out var nextEdge));
                            long end = orientation == 1 ? edge!.V2Id : edge!.V1Id;
                            long nextStart = nextOrientation == 1 ? nextEdge!.V1Id : nextEdge!.V2Id;
                            Assert.Equal(nextStart, end);
                        }
                    }
                }
            }
        }

        [Fact]
        public void LegacyMfcReaderCapillaQuirozV17()
        {
            var model = SkpFile.Open(FixturePath("capilla_quiroz_v17.skp"));
            AssertConnectedNormalizedLoops(model.Definitions.Values.Append(model.Root));
        }

        [Fact]
        public void ModernVffReaderUntitled()
        {
            var model = SkpFile.Open(FixturePath("Untitled.skp"));
            AssertConnectedNormalizedLoops(model.Definitions.Values.Append(model.Root));
        }
    }
}
