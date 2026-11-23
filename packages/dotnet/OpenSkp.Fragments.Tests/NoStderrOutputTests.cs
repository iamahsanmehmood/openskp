using System;
using System.Collections.Generic;
using System.IO;
using OpenSkp;
using OpenSkp.Fragments;
using Xunit;
using Fb = openskp._fragments_fb;

namespace OpenSkp.Fragments.Tests
{
    public class NoStderrOutputTests
    {
        private static readonly double[] Identity =
        {
            1, 0, 0, 0,
            0, 1, 0, 0,
            0, 0, 1, 0,
            0, 0, 0, 1,
        };

        private static byte[] MakeCircleExtrusionFrag()
        {
            var resource = new InstancedMeshResource
            {
                Id = "mesh_0",
                DefinitionId = 1,
                DefinitionName = "Box",
                VariantKey = "1|255,255,255",
                Primitives = new List<LocalPrimitive>
                {
                    new LocalPrimitive
                    {
                        Positions = new float[]
                        {
                            1.25f, 2.5f, 3.75f,
                            4.5f, 2.5f, 3.75f,
                            4.5f, 5.25f, 6.75f,
                        },
                        Normals = new float[9],
                        Uvs = new float[6],
                        Indices = new uint[] { 0, 1, 2 },
                        MaterialIndex = 0,
                    },
                },
            };
            var scene = new InstancedScene
            {
                MeshResources = new List<InstancedMeshResource> { resource },
                GltfMaterials = new List<object> { new Dictionary<string, object>() },
                SceneHierarchy = new InstancedNode
                {
                    Name = "ROOT",
                    Matrix = Identity,
                    Children = new List<InstancedNode>
                    {
                        new InstancedNode
                        {
                            Name = "Box1",
                            Matrix = Identity,
                            MeshResourceId = "mesh_0",
                            Guid = "",
                        },
                    },
                },
            };
            var raw = FragmentsExport.ToFragments(scene, raw: true);
            var bboxNeedle = new byte[24];
            float[] floats = { 1.25f, 2.5f, 3.75f, 4.5f, 5.25f, 6.75f };
            Buffer.BlockCopy(floats, 0, bboxNeedle, 0, 24);

            int foundAt = -1;
            for (int i = 0; i + bboxNeedle.Length + 1 <= raw.Length; i++)
            {
                bool match = true;
                for (int j = 0; j < bboxNeedle.Length; j++)
                {
                    if (raw[i + j] != bboxNeedle[j])
                    {
                        match = false;
                        break;
                    }
                }
                if (match)
                {
                    foundAt = i;
                    break;
                }
            }
            Assert.True(foundAt >= 0, "Expected unique Representation bbox in raw buffer");
            var patched = (byte[])raw.Clone();
            patched[foundAt + 24] = (byte)Fb.RepresentationClass.CIRCLE_EXTRUSION;
            return patched;
        }

        [Fact]
        public void FromFragmentsWritesNothingToStdoutOrStderrWhenNoLoggerConfigured()
        {
            var patched = MakeCircleExtrusionFrag();
            var origOut = Console.Out;
            var origErr = Console.Error;
            using var outWriter = new StringWriter();
            using var errWriter = new StringWriter();
            Console.SetOut(outWriter);
            Console.SetError(errWriter);
            try
            {
                var back = FragmentsExport.FromFragments(patched);
                Assert.All(back.MeshResources, r => Assert.Empty(r.Primitives));
            }
            finally
            {
                Console.SetOut(origOut);
                Console.SetError(origErr);
            }

            Assert.Equal("", outWriter.ToString());
            Assert.Equal("", errWriter.ToString());
        }

        [Fact]
        public void FromFragmentsDeliversWarningsThroughParseOptions()
        {
            var patched = MakeCircleExtrusionFrag();
            var seen = new List<(SkpLogLevel Level, string Message)>();
            var options = new SkpParseOptions
            {
                OnLog = (level, msg) => seen.Add((level, msg)),
            };
            var back = FragmentsExport.FromFragments(patched, options);
            Assert.All(back.MeshResources, r => Assert.Empty(r.Primitives));
            Assert.Contains(seen, e => e.Level == SkpLogLevel.Warning && e.Message.Contains("RepresentationClass"));
        }
    }
}
