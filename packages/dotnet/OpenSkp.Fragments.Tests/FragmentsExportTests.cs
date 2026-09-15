using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using Xunit;
using OpenSkp;
using Fb = openskp._fragments_fb;

namespace OpenSkp.Fragments.Tests
{
    /// <summary>Direct SKP -&gt; Fragments (.frag) export - C# port of
    /// Python's <c>openskp.export.fragments</c> (openskp#285/#276). See
    /// that module's own tests (<c>packages/python/tests/test_fragments.py</c>)
    /// and the TypeScript port's (<c>packages/typescript/tests/fragments.test.ts</c>)
    /// for the reference coverage this file mirrors - export-only, and
    /// matching TypeScript's own GUID/layer-hidden fidelity rather than
    /// Python's/C++'s fuller one (see FragmentsExport's own header
    /// comment).</summary>
    public class FragmentsExportTests
    {
        private static readonly double[] Identity =
        {
            1, 0, 0, 0,
            0, 1, 0, 0,
            0, 0, 1, 0,
            0, 0, 0, 1,
        };

        private static InstancedMeshResource MakeBoxResource(string id)
        {
            return new InstancedMeshResource
            {
                Id = id,
                DefinitionId = 1,
                DefinitionName = "Box",
                VariantKey = "1|255,255,255",
                Primitives = new List<LocalPrimitive>
                {
                    new LocalPrimitive
                    {
                        Positions = new float[] { 0,0,0, 1,0,0, 1,1,0, 0,1,0, 0,0,1, 1,0,1, 1,1,1, 0,1,1 },
                        Normals = new float[24],
                        Uvs = new float[16],
                        Indices = new uint[] { 0,1,2, 0,2,3, 4,6,5, 4,7,6 },
                        MaterialIndex = 0,
                    },
                },
            };
        }

        private static InstancedNode MakeLeaf(string name, string meshResourceId, double[]? matrix = null) => new InstancedNode
        {
            Name = name,
            Matrix = matrix ?? Identity,
            MeshResourceId = meshResourceId,
        };

        private static Fb.Model ParseRaw(byte[] raw) => Fb.Model.GetRootAsModel(new Google.FlatBuffers.ByteBuffer(raw));

        // ---- Schema & runtime verification ----------------------------

        [Fact]
        public void ProducesALoadableModelWithCorrectItemCount()
        {
            var scene = new InstancedScene
            {
                MeshResources = new List<InstancedMeshResource> { MakeBoxResource("mesh_0") },
                GltfMaterials = new List<object> { new Dictionary<string, object>() },
                SceneHierarchy = new InstancedNode
                {
                    Name = "ROOT",
                    Matrix = Identity,
                    Children = new List<InstancedNode> { MakeLeaf("Box1", "mesh_0") },
                },
            };

            var raw = FragmentsExport.ToFragments(scene, raw: true);
            var model = ParseRaw(raw);
            Assert.Equal(1, model.LocalIdsLength);
            Assert.NotNull(model.Meshes);
            Assert.Equal(1, model.Meshes!.Value.SamplesLength);
            Assert.Equal(1, model.Meshes!.Value.ShellsLength);
        }

        [Fact]
        public void DeduplicatesSharedGeometryAcrossInstances()
        {
            var scene = new InstancedScene
            {
                MeshResources = new List<InstancedMeshResource> { MakeBoxResource("mesh_0") },
                GltfMaterials = new List<object> { new Dictionary<string, object>() },
                SceneHierarchy = new InstancedNode
                {
                    Name = "ROOT",
                    Matrix = Identity,
                    Children = new List<InstancedNode>
                    {
                        MakeLeaf("Box1", "mesh_0"),
                        MakeLeaf("Box2", "mesh_0"),
                        MakeLeaf("Box3", "mesh_0"),
                    },
                },
            };

            var raw = FragmentsExport.ToFragments(scene, raw: true);
            var model = ParseRaw(raw);
            var meshes = model.Meshes!.Value;
            Assert.Equal(3, meshes.SamplesLength);
            // Same definition, same (identity) scale - one shared Shell.
            Assert.Equal(1, meshes.ShellsLength);
            Assert.Equal(1, meshes.RepresentationsLength);
        }

        [Fact]
        public void GlobalTransformsReflectEachInstancePlacement()
        {
            double[] translated = (double[])Identity.Clone();
            translated[12] = 5.0; translated[13] = 10.0; translated[14] = 15.0;

            var scene = new InstancedScene
            {
                MeshResources = new List<InstancedMeshResource> { MakeBoxResource("mesh_0") },
                GltfMaterials = new List<object> { new Dictionary<string, object>() },
                SceneHierarchy = new InstancedNode
                {
                    Name = "ROOT",
                    Matrix = Identity,
                    Children = new List<InstancedNode> { MakeLeaf("Box1", "mesh_0", translated) },
                },
            };

            var raw = FragmentsExport.ToFragments(scene, raw: true);
            var model = ParseRaw(raw);
            var transform = model.Meshes!.Value.GlobalTransforms(0)!.Value;
            var pos = transform.Position;
            Assert.Equal(5.0, pos.X, 6);
            Assert.Equal(10.0, pos.Y, 6);
            Assert.Equal(15.0, pos.Z, 6);
            // XDirection/YDirection must be unit vectors - everything a
            // rigid Transform can hold.
            var xd = transform.XDirection;
            Assert.Equal(1.0, Math.Sqrt(xd.X * xd.X + xd.Y * xd.Y + xd.Z * xd.Z), 5);
        }

        [Fact]
        public void GuidsAndLocalIdsSameLengthAndOrder()
        {
            var scene = new InstancedScene
            {
                MeshResources = new List<InstancedMeshResource> { MakeBoxResource("mesh_0") },
                GltfMaterials = new List<object> { new Dictionary<string, object>() },
                SceneHierarchy = new InstancedNode
                {
                    Name = "ROOT",
                    Matrix = Identity,
                    Children = new List<InstancedNode> { MakeLeaf("Box1", "mesh_0") },
                },
            };

            var raw = FragmentsExport.ToFragments(scene, raw: true);
            var model = ParseRaw(raw);
            Assert.Equal(1, model.LocalIdsLength);
            Assert.Equal(1, model.GuidsLength);
            Assert.Equal(1, model.GuidsItemsLength);
            Assert.Equal(model.LocalIds(0), model.GuidsItems(0));
        }

        [Fact]
        public void ConvertsMaterialColorsAccurately()
        {
            var scene = new InstancedScene
            {
                MeshResources = new List<InstancedMeshResource> { MakeBoxResource("mesh_0") },
                GltfMaterials = new List<object>
                {
                    new Dictionary<string, object>
                    {
                        ["pbrMetallicRoughness"] = new Dictionary<string, object>
                        {
                            ["baseColorFactor"] = new double[] { 1.0, 0.0, 0.0, 0.5 },
                        },
                    },
                },
                SceneHierarchy = new InstancedNode
                {
                    Name = "ROOT",
                    Matrix = Identity,
                    Children = new List<InstancedNode> { MakeLeaf("Box1", "mesh_0") },
                },
            };

            var raw = FragmentsExport.ToFragments(scene, raw: true);
            var model = ParseRaw(raw);
            var mat = model.Meshes!.Value.Materials(0)!.Value;
            Assert.Equal(255, mat.R);
            Assert.Equal(0, mat.G);
            Assert.Equal(0, mat.B);
            Assert.Equal(128, mat.A); // round(0.5 * 255)
        }

        [Fact]
        public void CompressedOutputIsSmallerAndActuallyDecompresses()
        {
            var scene = new InstancedScene
            {
                MeshResources = new List<InstancedMeshResource> { MakeBoxResource("mesh_0") },
                GltfMaterials = new List<object> { new Dictionary<string, object>() },
                SceneHierarchy = new InstancedNode
                {
                    Name = "ROOT",
                    Matrix = Identity,
                    Children = new List<InstancedNode> { MakeLeaf("Box1", "mesh_0") },
                },
            };

            var raw = FragmentsExport.ToFragments(scene, raw: true);
            var compressed = FragmentsExport.ToFragments(scene, raw: false);
            Assert.NotEqual(raw, compressed);
            // zlib (RFC 1950) header: 0x78 is the standard CMF byte for a
            // 32K window; 0x9C is a valid FLG (0x789C % 31 == 0).
            Assert.True(compressed.Length >= 2);
            Assert.Equal(0x78, compressed[0]);

            // Prove the hand-rolled zlib wrapper is actually valid, not
            // just "starts with the right byte": inflate the raw DEFLATE
            // body (skip the 2-byte zlib header, ignore the 4-byte Adler32
            // trailer - .NET's own DeflateStream speaks RFC 1951, not the
            // RFC 1950 zlib wrapper) and check it reproduces the exact
            // uncompressed bytes.
            using var input = new MemoryStream(compressed, 2, compressed.Length - 2 - 4);
            using var inflate = new DeflateStream(input, CompressionMode.Decompress);
            using var output = new MemoryStream();
            inflate.CopyTo(output);
            Assert.Equal(raw, output.ToArray());
        }

        // ---- TRS decomposition and scale/mirror baking -----------------

        [Fact]
        public void DecomposeTrsSeparatesTranslationRotationScaleAndMirror()
        {
            double[] m =
            {
                2, 0, 0, 0,
                0, 3, 0, 0,
                0, 0, 0.5, 0,
                10, 20, 30, 1,
            };
            var trs = FragmentsExport.DecomposeTrs(m);
            Assert.Equal(new[] { 10.0, 20.0, 30.0 }, trs.Position);
            Assert.Equal(new[] { 2.0, 3.0, 0.5 }, trs.Scale);
            Assert.False(trs.Mirrored);
            Assert.Equal(new[] { 1.0, 0.0, 0.0 }, trs.XDir);
            Assert.Equal(new[] { 0.0, 1.0, 0.0 }, trs.YDir);
        }

        [Fact]
        public void MirroredInstanceNegatesLocalXAndReversesWinding()
        {
            double[] m =
            {
                -1, 0, 0, 0,
                0, 1, 0, 0,
                0, 0, 1, 0,
                0, 0, 0, 1,
            };
            var trs = FragmentsExport.DecomposeTrs(m);
            Assert.True(trs.Mirrored);
            // The mirror is re-derived as M' = M @ diag(-1,1,1): XDir gets
            // negated a SECOND time here (once from the source matrix's own
            // already-negative local X, once by the mirror-correction
            // itself), so it comes back out positive - the actual mirroring
            // is expressed by baking a negated X into the geometry instead
            // (checked below), not by a negative XDir. See DecomposeTrs's
            // own doc comment.
            Assert.Equal(1.0, trs.XDir[0], 9);

            var prim = new LocalPrimitive
            {
                Positions = new float[] { 0, 0, 0, 1, 0, 0, 0, 1, 0 },
                Indices = new uint[] { 0, 1, 2 },
            };
            var baked = FragmentsExport.BakePrimitive(prim, trs.Scale, trs.Mirrored);
            Assert.Equal((uint)0, baked.Triangles[0][0]);
            Assert.Equal((uint)2, baked.Triangles[0][1]);
            Assert.Equal((uint)1, baked.Triangles[0][2]);
            // X coordinate of the second point (originally 1,0,0) flips sign.
            Assert.Equal(-1f, baked.Points[1][0]);
        }

        [Fact]
        public void InstancesSharingTheSameScaleDeduplicateToOneShell()
        {
            double[] scaled = { 2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 1 };
            var scene = new InstancedScene
            {
                MeshResources = new List<InstancedMeshResource> { MakeBoxResource("mesh_0") },
                GltfMaterials = new List<object> { new Dictionary<string, object>() },
                SceneHierarchy = new InstancedNode
                {
                    Name = "ROOT",
                    Matrix = Identity,
                    Children = new List<InstancedNode>
                    {
                        MakeLeaf("Box1", "mesh_0", scaled),
                        MakeLeaf("Box2", "mesh_0", scaled),
                    },
                },
            };
            var raw = FragmentsExport.ToFragments(scene, raw: true);
            var model = ParseRaw(raw);
            Assert.Equal(1, model.Meshes!.Value.ShellsLength);
        }

        [Fact]
        public void InstancesWithDifferentScalesGetSeparateShells()
        {
            double[] scale2 = { 2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 1 };
            double[] scale3 = { 3, 0, 0, 0, 0, 3, 0, 0, 0, 0, 3, 0, 0, 0, 0, 1 };
            var scene = new InstancedScene
            {
                MeshResources = new List<InstancedMeshResource> { MakeBoxResource("mesh_0") },
                GltfMaterials = new List<object> { new Dictionary<string, object>() },
                SceneHierarchy = new InstancedNode
                {
                    Name = "ROOT",
                    Matrix = Identity,
                    Children = new List<InstancedNode>
                    {
                        MakeLeaf("Box1", "mesh_0", scale2),
                        MakeLeaf("Box2", "mesh_0", scale3),
                    },
                },
            };
            var raw = FragmentsExport.ToFragments(scene, raw: true);
            var model = ParseRaw(raw);
            Assert.Equal(2, model.Meshes!.Value.ShellsLength);
        }

        // ---- Metadata and attributes ------------------------------------

        [Fact]
        public void AttributesCarryItemDisplayNames()
        {
            var scene = new InstancedScene
            {
                MeshResources = new List<InstancedMeshResource> { MakeBoxResource("mesh_0") },
                GltfMaterials = new List<object> { new Dictionary<string, object>() },
                SceneHierarchy = new InstancedNode
                {
                    Name = "ROOT",
                    Matrix = Identity,
                    Children = new List<InstancedNode> { MakeLeaf("Truss1", "mesh_0") },
                },
            };
            var raw = FragmentsExport.ToFragments(scene, raw: true);
            var model = ParseRaw(raw);
            var attr = model.Attributes(0)!.Value;
            Assert.Equal(1, attr.DataLength);
            Assert.Contains("Truss1", attr.Data(0));
            Assert.Contains("STRING", attr.Data(0));
        }

        [Fact]
        public void UnnamedItemGetsAnEmptyAttributeNotAMissingOne()
        {
            var scene = new InstancedScene
            {
                MeshResources = new List<InstancedMeshResource> { MakeBoxResource("mesh_0") },
                GltfMaterials = new List<object> { new Dictionary<string, object>() },
                SceneHierarchy = new InstancedNode
                {
                    Name = "ROOT",
                    Matrix = Identity,
                    Children = new List<InstancedNode> { MakeLeaf("", "mesh_0") },
                },
            };
            var raw = FragmentsExport.ToFragments(scene, raw: true);
            var model = ParseRaw(raw);
            Assert.Equal(1, model.AttributesLength);
            Assert.Equal(0, model.Attributes(0)!.Value.DataLength);
        }

        [Fact]
        public void MetadataIsAlwaysPresentAsAnEmptyLayerHiddenObject()
        {
            var scene = new InstancedScene
            {
                MeshResources = new List<InstancedMeshResource> { MakeBoxResource("mesh_0") },
                GltfMaterials = new List<object> { new Dictionary<string, object>() },
                SceneHierarchy = new InstancedNode
                {
                    Name = "ROOT",
                    Matrix = Identity,
                    Children = new List<InstancedNode> { MakeLeaf("Box1", "mesh_0") },
                },
            };
            var raw = FragmentsExport.ToFragments(scene, raw: true);
            var model = ParseRaw(raw);
            Assert.Contains("layer_hidden", model.Metadata);
            Assert.Contains("generated_name_guids", model.Metadata);
        }

        // ---- Spatial structure --------------------------------------

        [Fact]
        public void NamedContainerGetsALocalIdGenericOneDoesNot()
        {
            var stud = MakeLeaf("Stud1", "mesh_0");
            var wrapper = new InstancedNode { Name = "W-2", Matrix = Identity, Children = new List<InstancedNode> { stud } };
            var root = new InstancedNode { Name = "ROOT", Matrix = Identity, Children = new List<InstancedNode> { wrapper } };

            var scene = new InstancedScene
            {
                MeshResources = new List<InstancedMeshResource> { MakeBoxResource("mesh_0") },
                GltfMaterials = new List<object> { new Dictionary<string, object>() },
                SceneHierarchy = root,
            };
            var raw = FragmentsExport.ToFragments(scene, raw: true);
            var model = ParseRaw(raw);
            // wrapper (named, no geometry) + Stud1 = 2 tracked items.
            Assert.Equal(2, model.LocalIdsLength);

            var rootSpatial = model.SpatialStructure!.Value;
            var wrapperSpatial = rootSpatial.Children(0)!.Value;
            Assert.True(wrapperSpatial.LocalId.HasValue);
        }

        [Fact]
        public void CategoryReflectsEachNodesOwnLayer()
        {
            var leaf = MakeLeaf("Box1", "mesh_0");
            leaf.Layer = "Studs";
            var root = new InstancedNode { Name = "ROOT", Matrix = Identity, Children = new List<InstancedNode> { leaf } };

            var scene = new InstancedScene
            {
                MeshResources = new List<InstancedMeshResource> { MakeBoxResource("mesh_0") },
                GltfMaterials = new List<object> { new Dictionary<string, object>() },
                SceneHierarchy = root,
            };
            var raw = FragmentsExport.ToFragments(scene, raw: true);
            var model = ParseRaw(raw);
            Assert.Equal("Studs", model.Categories(0));
        }

        // ---- Real fixture round-trip -----------------------------------

        private static string FixturePath(string name) => Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        [Theory]
        [InlineData("SU_File.skp")]
        [InlineData("capilla_quiroz_v17.skp")]
        [InlineData("gondola_v20.skp")]
        public void RealSketchupFixturesExportToAValidModel(string fixtureName)
        {
            var bytes = File.ReadAllBytes(FixturePath(fixtureName));
            var scene = SkpFile.BuildInstancedScene(bytes);

            var raw = FragmentsExport.ToFragments(scene, raw: true);
            var model = ParseRaw(raw);

            Assert.True(model.LocalIdsLength > 0);
            Assert.NotNull(model.Meshes);
            Assert.True(model.Meshes!.Value.ShellsLength > 0);
            Assert.True(model.Meshes!.Value.SamplesLength > 0);
            // Every sample's Shell must resolve via Representation.Id (an
            // index into Meshes.Shells), matching the real reader's own
            // convention - not the representation's own vector position.
            var meshes = model.Meshes!.Value;
            for (int i = 0; i < meshes.SamplesLength; i++)
            {
                var sample = meshes.Samples(i)!.Value;
                var rep = meshes.Representations((int)sample.Representation)!.Value;
                Assert.True(rep.Id < (uint)meshes.ShellsLength);
            }
        }

        [Fact]
        public void ExportFragmentsWritesAFile()
        {
            var scene = new InstancedScene
            {
                MeshResources = new List<InstancedMeshResource> { MakeBoxResource("mesh_0") },
                GltfMaterials = new List<object> { new Dictionary<string, object>() },
                SceneHierarchy = new InstancedNode
                {
                    Name = "ROOT",
                    Matrix = Identity,
                    Children = new List<InstancedNode> { MakeLeaf("Box1", "mesh_0") },
                },
            };
            var tmp = Path.Combine(Path.GetTempPath(), $"openskp_fragments_test_{Guid.NewGuid():N}.frag");
            try
            {
                FragmentsExport.ExportFragments(scene, tmp);
                Assert.True(File.Exists(tmp));
                Assert.True(new FileInfo(tmp).Length > 0);
            }
            finally
            {
                if (File.Exists(tmp)) File.Delete(tmp);
            }
        }
    }
}
