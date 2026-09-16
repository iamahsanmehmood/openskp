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
    /// that module's own tests (<c>packages/python/tests/test_fragments.py</c>),
    /// the C++ port's (<c>packages/cpp/tests/fragments_export_test.cpp</c>),
    /// and the TypeScript port's (<c>packages/typescript/tests/fragments.test.ts</c>)
    /// for the reference coverage this file mirrors - export-only, matching
    /// Python's/C++'s full GUID/name-is-generated/layer-hidden fidelity
    /// (see FragmentsExport's own header comment).</summary>
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

        private static InstancedNode MakeLeaf(string name, string meshResourceId, double[]? matrix = null, string? guid = null) => new InstancedNode
        {
            Name = name,
            Matrix = matrix ?? Identity,
            MeshResourceId = meshResourceId,
            Guid = guid ?? "",
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
        public void ItemsWithoutASourceGuidGetAUniqueSyntheticOne()
        {
            var scene = new InstancedScene
            {
                MeshResources = new List<InstancedMeshResource> { MakeBoxResource("mesh_0") },
                GltfMaterials = new List<object> { new Dictionary<string, object>() },
                SceneHierarchy = new InstancedNode
                {
                    Name = "ROOT",
                    Matrix = Identity,
                    Children = new List<InstancedNode> { MakeLeaf("Box1", "mesh_0"), MakeLeaf("Box2", "mesh_0") },
                },
            };

            var raw = FragmentsExport.ToFragments(scene, raw: true);
            var model = ParseRaw(raw);
            var guids = new HashSet<string>();
            for (int i = 0; i < model.GuidsLength; i++)
            {
                var g = model.Guids(i);
                Assert.NotNull(g);
                Assert.NotEmpty(g!);
                guids.Add(g!);
            }
            Assert.Equal(model.GuidsLength, guids.Count);
        }

        [Fact]
        public void ARealSourceGuidIsPreservedExactly()
        {
            const string realGuid = "F160C36229782F47A9857FC88DD1F2CB";
            var scene = new InstancedScene
            {
                MeshResources = new List<InstancedMeshResource> { MakeBoxResource("mesh_0") },
                GltfMaterials = new List<object> { new Dictionary<string, object>() },
                SceneHierarchy = new InstancedNode
                {
                    Name = "ROOT",
                    Matrix = Identity,
                    Children = new List<InstancedNode> { MakeLeaf("Box", "mesh_0", guid: realGuid) },
                },
            };

            var raw = FragmentsExport.ToFragments(scene, raw: true);
            var model = ParseRaw(raw);
            Assert.Equal(1, model.GuidsLength);
            Assert.Equal(realGuid, model.Guids(0));
        }

        // openskp#290: SketchUp's own native Copy/Move+Copy/Array tools
        // carry an instance's attribute dictionaries - and whatever GUID a
        // framing plugin wrote into one - to every copy verbatim, so a real
        // file can have several DIFFERENT physical instances all sharing the
        // exact same non-empty InstancedNode.Guid. The first instance to
        // claim a real GUID keeps it; every later instance sharing that same
        // value must fall back to a synthetic one instead of silently
        // colliding. Mirrors Python's/C++'s own equivalent test exactly.
        [Fact]
        public void ADuplicatedSourceGuidDoesNotCollide()
        {
            const string duplicatedGuid = "F160C36229782F47A9857FC88DD1F2CB";
            var root = new InstancedNode { Name = "ROOT", Matrix = Identity, Children = new List<InstancedNode>() };
            for (int i = 0; i < 3; i++)
            {
                root.Children.Add(MakeLeaf($"Truss{i}", "mesh_0", guid: duplicatedGuid));
            }
            var scene = new InstancedScene
            {
                MeshResources = new List<InstancedMeshResource> { MakeBoxResource("mesh_0") },
                GltfMaterials = new List<object> { new Dictionary<string, object>() },
                SceneHierarchy = root,
            };

            var raw = FragmentsExport.ToFragments(scene, raw: true);
            var model = ParseRaw(raw);
            Assert.Equal(3, model.GuidsLength);

            var guids = new List<string>();
            int realGuidCount = 0;
            for (int i = 0; i < 3; i++)
            {
                var g = model.Guids(i)!;
                guids.Add(g);
                if (g == duplicatedGuid) realGuidCount++;
            }
            Assert.Equal(3, guids.Distinct().Count());
            Assert.Equal(1, realGuidCount);
        }

        [Fact]
        public void NamedWrapperWithNoGeometryGetsATrackedItem()
        {
            // A named organizational wrapper (NameIsGenerated = false, the
            // real-name case) with no geometry of its own must still become
            // a tracked item with its own local_id/Name/GUID; a generic,
            // auto-generated wrapper (NameIsGenerated = true) must not -
            // matches CollectLeaves' use of the real flag (not TypeScript's
            // "non-empty name" heuristic .NET briefly used before
            // InstancedNode gained NameIsGenerated).
            var child = MakeLeaf("Stud1", "mesh_0");
            var wrapper = new InstancedNode { Name = "W-2", NameIsGenerated = false, Matrix = Identity, Children = new List<InstancedNode> { child } };

            var genericChild = MakeLeaf("Stud2", "mesh_0");
            var genericWrapper = new InstancedNode { Name = "Component_5", NameIsGenerated = true, Matrix = Identity, Children = new List<InstancedNode> { genericChild } };

            var root = new InstancedNode { Name = "ROOT", Matrix = Identity, Children = new List<InstancedNode> { wrapper, genericWrapper } };
            var scene = new InstancedScene
            {
                MeshResources = new List<InstancedMeshResource> { MakeBoxResource("mesh_0") },
                GltfMaterials = new List<object> { new Dictionary<string, object>() },
                SceneHierarchy = root,
            };

            var raw = FragmentsExport.ToFragments(scene, raw: true);
            var model = ParseRaw(raw);
            // wrapper (W-2, named, no geometry) + Stud1 + Stud2 = 3 tracked
            // items; genericWrapper itself (NameIsGenerated) is NOT tracked.
            Assert.Equal(3, model.LocalIdsLength);

            bool foundW2 = false;
            for (int i = 0; i < model.AttributesLength; i++)
            {
                var attr = model.Attributes(i)!.Value;
                for (int j = 0; j < attr.DataLength; j++)
                {
                    if (attr.Data(j).Contains("W-2")) foundW2 = true;
                }
            }
            Assert.True(foundW2, "the named wrapper's own real name must reach the exported Attribute data");
        }

        [Fact]
        public void MetadataCarriesTheSourceFilesLayerHiddenState()
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
                LayerHidden = new Dictionary<string, bool> { ["Layer0"] = false, ["wall_cladding"] = true },
            };

            var raw = FragmentsExport.ToFragments(scene, raw: true);
            var model = ParseRaw(raw);
            Assert.Contains("\"wall_cladding\":true", model.Metadata);
            Assert.Contains("\"Layer0\":false", model.Metadata);
        }

        [Fact]
        public void GeneratedNameGuidsAreListedInMetadata()
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
                        new InstancedNode { Name = "Component_7", NameIsGenerated = true, Matrix = Identity, MeshResourceId = "mesh_0" },
                    },
                },
            };

            var raw = FragmentsExport.ToFragments(scene, raw: true);
            var model = ParseRaw(raw);
            Assert.Equal(1, model.GuidsLength);
            var guid = model.Guids(0)!;
            Assert.Contains(guid, model.Metadata);
            Assert.Contains("generated_name_guids", model.Metadata);
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
        public void MetadataPresentEvenWithNoHiddenLayers()
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

    /// <summary>A single shell (Fragments' term for one baked triangle
    /// mesh) has no representation for more than 65535 triangles:
    /// ProfilesFaceIds is a plain ushort array in the real schema
    /// (index.fbs), with no uint32 escape hatch the way POINTS get past
    /// 65535 via BigShellProfile. A real production model with one
    /// 222,000+-triangle primitive (a large flattened/dense mesh) hit this
    /// in practice on the Python port - see openskp#285/PR #355 for the
    /// full incident. Unlike Python (which threw at write time),
    /// GetOrBakeShell's old <c>(ushort)i</c> cast silently WRAPPED instead
    /// of throwing - a worse bug in one sense: it wrote wrong/colliding
    /// face ids instead of failing loudly. GetOrBakeShell now splits an
    /// oversized primitive's triangles into multiple shells instead, each
    /// within the ushort limit.</summary>
    public class FragmentsOversizedShellTests
    {
        private static readonly double[] Identity =
        {
            1, 0, 0, 0,
            0, 1, 0, 0,
            0, 0, 1, 0,
            0, 0, 0, 1,
        };

        private const int UshortMax = 65535;

        /// <summary>A flat cols x rows vertex grid (z=0 plane),
        /// triangulated as 2 triangles per quad cell - (cols-1)*(rows-1)*2
        /// triangles total, a plain synthetic stand-in for the kind of
        /// large flattened/dense mesh that triggers this in real
        /// models.</summary>
        private static LocalPrimitive GridPrimitive(int cols, int rows)
        {
            var positions = new float[cols * rows * 3];
            for (int j = 0; j < rows; j++)
            {
                for (int i = 0; i < cols; i++)
                {
                    int v = j * cols + i;
                    positions[v * 3] = i;
                    positions[v * 3 + 1] = j;
                    positions[v * 3 + 2] = 0;
                }
            }

            var triCells = (cols - 1) * (rows - 1);
            var indices = new uint[triCells * 6];
            int k = 0;
            for (int j = 0; j < rows - 1; j++)
            {
                for (int i = 0; i < cols - 1; i++)
                {
                    uint a = (uint)(j * cols + i);
                    uint b = a + 1;
                    uint c = a + (uint)cols;
                    uint d = c + 1;
                    indices[k++] = a; indices[k++] = b; indices[k++] = d;
                    indices[k++] = a; indices[k++] = d; indices[k++] = c;
                }
            }

            return new LocalPrimitive
            {
                Positions = positions,
                Normals = new float[cols * rows * 3],
                Uvs = new float[cols * rows * 2],
                Indices = indices,
                MaterialIndex = 0,
            };
        }

        [Fact]
        public void SplitsAnOversizedPrimitiveAcrossMultipleShellsWithoutLosingTriangles()
        {
            const int cols = 210, rows = 165;
            int expectedTriangles = (cols - 1) * (rows - 1) * 2;
            Assert.True(expectedTriangles > UshortMax); // sanity-check the fixture itself exceeds the limit under test

            var scene = new InstancedScene
            {
                MeshResources = new List<InstancedMeshResource>
                {
                    new InstancedMeshResource
                    {
                        Id = "mesh_big",
                        DefinitionId = 1,
                        DefinitionName = "BigMesh",
                        VariantKey = "1|255,255,255",
                        Primitives = new List<LocalPrimitive> { GridPrimitive(cols, rows) },
                    },
                },
                GltfMaterials = new List<object> { new Dictionary<string, object>() },
                SceneHierarchy = new InstancedNode
                {
                    Name = "ROOT",
                    Matrix = Identity,
                    Children = new List<InstancedNode>
                    {
                        new InstancedNode { Name = "Big", Matrix = Identity, MeshResourceId = "mesh_big", Guid = "" },
                    },
                },
            };

            // This is the exact shape of call that, before the fix, either
            // silently wrote wrapped-around (wrong) face ids via
            // `(ushort)i` truncation - the primary regression check is
            // that the result is now actually correct, not just that it
            // doesn't throw (unlike Python's own crash-shaped version of
            // this bug).
            var raw = FragmentsExport.ToFragments(scene, raw: true);
            var model = Fb.Model.GetRootAsModel(new Google.FlatBuffers.ByteBuffer(raw));
            var meshes = model.Meshes!.Value;

            Assert.True(meshes.ShellsLength > 1); // confirms the split actually happened, not a no-op
            Assert.Equal(meshes.ShellsLength, meshes.SamplesLength); // one sample per split shell, same item/material

            int totalTriangles = 0;
            for (int i = 0; i < meshes.ShellsLength; i++)
            {
                var shell = meshes.Shells(i)!.Value;
                Assert.True(shell.ProfilesFaceIdsLength <= UshortMax); // every sub-shell itself stays within the ushort limit
                Assert.Equal(shell.ProfilesLength, shell.ProfilesFaceIdsLength);
                // Every face id in a shell is a small, sequential, non-wrapped
                // 0..N-1 run - the exact thing the old (ushort)i truncation
                // could silently violate once a shell's own triangle count
                // (here, per sub-shell after the split, never more than
                // UshortMax) exceeded 65535.
                for (int j = 0; j < shell.ProfilesFaceIdsLength; j++)
                {
                    Assert.Equal((ushort)j, shell.ProfilesFaceIds(j));
                }
                totalTriangles += shell.ProfilesFaceIdsLength;
            }

            Assert.Equal(expectedTriangles, totalTriangles);
        }

        [Fact]
        public void APrimitiveWithinTheLimitStillProducesExactlyOneShell()
        {
            // Regression guard on the split path itself: a normal, non-huge
            // primitive must not be needlessly split into multiple shells.
            const int cols = 50, rows = 50; // 49*49*2 = 4802 triangles, comfortably under 65535

            var scene = new InstancedScene
            {
                MeshResources = new List<InstancedMeshResource>
                {
                    new InstancedMeshResource
                    {
                        Id = "mesh_small",
                        DefinitionId = 1,
                        DefinitionName = "SmallMesh",
                        VariantKey = "1|255,255,255",
                        Primitives = new List<LocalPrimitive> { GridPrimitive(cols, rows) },
                    },
                },
                GltfMaterials = new List<object> { new Dictionary<string, object>() },
                SceneHierarchy = new InstancedNode
                {
                    Name = "ROOT",
                    Matrix = Identity,
                    Children = new List<InstancedNode>
                    {
                        new InstancedNode { Name = "Small", Matrix = Identity, MeshResourceId = "mesh_small", Guid = "" },
                    },
                },
            };

            var raw = FragmentsExport.ToFragments(scene, raw: true);
            var model = Fb.Model.GetRootAsModel(new Google.FlatBuffers.ByteBuffer(raw));
            var meshes = model.Meshes!.Value;
            Assert.Equal(1, meshes.ShellsLength);
            Assert.Equal(1, meshes.SamplesLength);
        }
    }
}
