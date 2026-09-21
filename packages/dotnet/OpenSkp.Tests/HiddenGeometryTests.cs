using System.Collections.Generic;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>
    /// The readers have always decoded a face's and an instance's own hidden
    /// bit (Geometry's D307 base 0x06, +0x01 hidden) onto
    /// GeometryBuilderFace.Hidden and GeometryBuilderInstance.Hidden, but the
    /// scene builders never consulted either one - BuildLocalFaceGroups
    /// walked builder.Faces and both scene walkers walked builder.Instances
    /// unconditionally, so every exporter emitted geometry the file hides.
    /// SkpParseOptions.RespectVisibility now opts into leaving it out, off by
    /// default to match TypeScript's respectEdgeVisibility.
    ///
    /// Counting definitions or faces does not catch any of this, because the
    /// parse was always right - only the built scene is wrong, so these
    /// assert on the triangles each scene API emits, and assert both APIs
    /// agree.
    /// </summary>
    public class HiddenGeometryTests
    {
        private static readonly (double X, double Y, double Z)[] _lowerQuad =
        {
            (0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0),
        };

        private static readonly (double X, double Y, double Z)[] _upperQuad =
        {
            (0.0, 0.0, 5.0), (10.0, 0.0, 5.0), (10.0, 10.0, 5.0), (0.0, 10.0, 5.0),
        };

        private static SkpParseOptions Respecting() => new SkpParseOptions { RespectVisibility = true };

        private static int SceneTriangles(byte[] skp, SkpParseOptions options) =>
            SkpFile.BuildScene(skp, options)
                .GlbPrimitives.Sum(primitive => primitive.Indices.Length / 3);

        /// <summary>Counts per placed node, not per mesh resource: two instances of one
        /// definition share a single resource, so summing resources cannot tell a filtered
        /// placement from an unfiltered one.</summary>
        private static int InstancedTriangles(byte[] skp, SkpParseOptions options)
        {
            var scene = SkpFile.BuildInstancedScene(skp, options);
            var trianglesByResource = scene.MeshResources.ToDictionary(
                resource => resource.Id,
                resource => resource.Primitives.Sum(primitive => primitive.Indices.Length / 3));

            int count = 0;
            void Walk(InstancedNode node)
            {
                if (node.MeshResourceId is string meshResourceId &&
                    trianglesByResource.TryGetValue(meshResourceId, out var triangles))
                {
                    count += triangles;
                }

                foreach (var child in node.Children)
                {
                    Walk(child);
                }
            }

            Walk(scene.SceneHierarchy);
            return count;
        }

        private static int HierarchyNodes(byte[] skp, SkpParseOptions options)
        {
            int count = 0;
            void Walk(InstancedNode node)
            {
                count++;
                foreach (var child in node.Children)
                {
                    Walk(child);
                }
            }

            Walk(SkpFile.BuildInstancedScene(skp, options).SceneHierarchy);
            return count;
        }

        private static byte[] TwoFaces(bool upperHidden)
        {
            var builder = SkpCreate.NewFile();
            builder.AddFace(_lowerQuad, hidden: false);
            builder.AddFace(_upperQuad, hidden: upperHidden);
            return builder.ToBytes();
        }

        private static byte[] TwoGroups(bool secondHidden)
        {
            var builder = SkpCreate.NewFile();
            using (var visible = builder.AddGroup("visible", hidden: false))
            {
                visible.AddFace(_lowerQuad);
            }

            using (var other = builder.AddGroup("other", hidden: secondHidden))
            {
                other.AddFace(_upperQuad);
            }

            return builder.ToBytes();
        }

        private static byte[] TwoComponentInstances(bool secondHidden)
        {
            var builder = SkpCreate.NewFile();
            ComponentDefinitionBuilder definition;
            using (definition = builder.AddComponentDefinition("panel"))
            {
                definition.AddFace(_lowerQuad);
            }

            builder.AddInstance(definition, "visible", hidden: false);
            builder.AddInstance(definition, "other", translation: (0.0, 0.0, 5.0), hidden: secondHidden);
            return builder.ToBytes();
        }

        [Fact]
        public void Default_KeepsHiddenGeometry()
        {
            var plain = new SkpParseOptions();

            Assert.Equal(4, SceneTriangles(TwoFaces(upperHidden: true), plain));
            Assert.Equal(4, InstancedTriangles(TwoGroups(secondHidden: true), plain));
            Assert.Equal(4, InstancedTriangles(TwoComponentInstances(secondHidden: true), plain));
            Assert.Equal(4, SceneTriangles(TwoComponentInstances(secondHidden: true), plain));
        }

        [Fact]
        public void RespectVisibility_LeavesOutAHiddenFace()
        {
            var skp = TwoFaces(upperHidden: true);

            Assert.Equal(2, SceneTriangles(skp, Respecting()));
            Assert.Equal(2, InstancedTriangles(skp, Respecting()));
        }

        [Fact]
        public void RespectVisibility_KeepsVisibleFaces()
        {
            var skp = TwoFaces(upperHidden: false);

            Assert.Equal(4, SceneTriangles(skp, Respecting()));
            Assert.Equal(4, InstancedTriangles(skp, Respecting()));
        }

        [Fact]
        public void RespectVisibility_LeavesOutEverythingAHiddenGroupPlaces()
        {
            var skp = TwoGroups(secondHidden: true);

            Assert.Equal(2, SceneTriangles(skp, Respecting()));
            Assert.Equal(2, InstancedTriangles(skp, Respecting()));
        }

        [Fact]
        public void RespectVisibility_LeavesOutAHiddenComponentInstance()
        {
            var skp = TwoComponentInstances(secondHidden: true);

            Assert.Equal(2, SceneTriangles(skp, Respecting()));
            Assert.Equal(2, InstancedTriangles(skp, Respecting()));
        }

        [Fact]
        public void BothSceneApisAgreeOnWhatIsVisible()
        {
            foreach (var skp in new List<byte[]>
                     {
                         TwoFaces(upperHidden: true),
                         TwoGroups(secondHidden: true),
                         TwoComponentInstances(secondHidden: true),
                     })
            {
                Assert.Equal(SceneTriangles(skp, Respecting()), InstancedTriangles(skp, Respecting()));
            }
        }

        [Fact]
        public void RespectVisibility_TakesTheHiddenGroupOutOfTheHierarchyToo()
        {
            var skp = TwoGroups(secondHidden: true);

            Assert.Equal(HierarchyNodes(skp, new SkpParseOptions()) - 1, HierarchyNodes(skp, Respecting()));
        }
    }
}
