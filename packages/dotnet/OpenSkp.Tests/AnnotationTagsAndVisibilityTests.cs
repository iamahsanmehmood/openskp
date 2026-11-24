using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using OpenSkp;
using Xunit;

namespace OpenSkp.Tests
{
    public class AnnotationTagsAndVisibilityTests
    {
        private static byte[] TlvBytes(ushort tag, byte[] payload)
        {
            var result = new byte[6 + payload.Length];
            result[0] = (byte)(tag & 0xFF);
            result[1] = (byte)(tag >> 8);
            uint len = (uint)payload.Length;
            result[2] = (byte)(len & 0xFF);
            result[3] = (byte)((len >> 8) & 0xFF);
            result[4] = (byte)((len >> 16) & 0xFF);
            result[5] = (byte)((len >> 24) & 0xFF);
            Array.Copy(payload, 0, result, 6, payload.Length);
            return result;
        }

        private static byte[] Vec3Bytes(double x, double y, double z)
        {
            var result = new byte[24];
            Array.Copy(BitConverter.GetBytes(x), 0, result, 0, 8);
            Array.Copy(BitConverter.GetBytes(y), 0, result, 8, 8);
            Array.Copy(BitConverter.GetBytes(z), 0, result, 16, 8);
            return result;
        }

        private static byte[] Concat(params byte[][] parts)
        {
            int total = parts.Sum(p => p.Length);
            var result = new byte[total];
            int off = 0;
            foreach (var p in parts)
            {
                Array.Copy(p, 0, result, off, p.Length);
                off += p.Length;
            }
            return result;
        }

        [Fact]
        public void ComposeInstanceWorldNestsParentTimesLocal()
        {
            var panel = new GeometryBuilder();
            var cabinet = new GeometryBuilder();
            cabinet.Instances.Add(new GeometryBuilderInstance
            {
                PersistentId = "AA",
                RefIdx = 2,
                Matrix = new List<double> { 1, 0, 0, 0, 1, 0, 0, 0, 1, 1.0, 2.0, 3.0, 1.0 },
            });
            var root = new GeometryBuilder();
            root.Instances.Add(new GeometryBuilderInstance
            {
                PersistentId = "BB",
                RefIdx = 1,
                Matrix = new List<double> { 1, 0, 0, 0, 1, 0, 0, 0, 1, 10.0, 20.0, 30.0, 1.0 },
            });
            var defs = new Dictionary<long, Geometry.RawDefinition>
            {
                [1] = new Geometry.RawDefinition { Builder = cabinet },
                [2] = new Geometry.RawDefinition { Builder = panel },
            };
            var world = new Dictionary<string, List<double>?>();
            PagesDimensions.ComposeInstanceWorld(root, defs, world);
            Assert.Equal((10.0, 20.0, 30.0), Transforms.TransformPoint(world["BB"]!.ToArray(), (0.0, 0.0, 0.0)));
            Assert.Equal((11.0, 22.0, 33.0), Transforms.TransformPoint(world["AA"]!.ToArray(), (0.0, 0.0, 0.0)));
        }

        [Fact]
        public void DimensionConnectedPointUsesComposedNestedWorld()
        {
            var vid = new byte[] { 0xAA, 0xBB, 0x01 };
            var iid = new byte[] { 0xCC, 0xDD, 0x02 };
            var idLenPrefixed = Concat(new byte[] { (byte)iid.Length }, iid);
            var refTlv = TlvBytes(0x53FC, Concat(TlvBytes(0x53FD, vid), TlvBytes(0x53FE, idLenPrefixed)));
            var connA = TlvBytes(0x5BCD, TlvBytes(0x5208, Concat(TlvBytes(0x5209, BitConverter.GetBytes(2u)), TlvBytes(0x520B, refTlv))));
            var connB = TlvBytes(0x5BCE, TlvBytes(0x5208, Concat(TlvBytes(0x5209, BitConverter.GetBytes(1u)), TlvBytes(0x520A, Vec3Bytes(0, 0, 0)))));
            var blob = TlvBytes(0x5BCC, Concat(connA, connB, TlvBytes(0x5BD2, BitConverter.GetBytes(0.0))));

            var leaf = new GeometryBuilder();
            var parentDef = new GeometryBuilder();
            parentDef.Instances.Add(new GeometryBuilderInstance
            {
                PersistentId = "CCDD02",
                RefIdx = 2,
                Matrix = new List<double> { 1, 0, 0, 0, 1, 0, 0, 0, 1, 1.0, 2.0, 3.0, 1.0 },
            });
            var root = new GeometryBuilder();
            root.Instances.Add(new GeometryBuilderInstance
            {
                PersistentId = "PARENT",
                RefIdx = 1,
                Matrix = new List<double> { 1, 0, 0, 0, 1, 0, 0, 0, 1, 10.0, 20.0, 30.0, 1.0 },
            });
            var defs = new Dictionary<long, Geometry.RawDefinition>
            {
                [1] = new Geometry.RawDefinition { Builder = parentDef },
                [2] = new Geometry.RawDefinition { Builder = leaf },
            };
            var world = new Dictionary<string, List<double>?>();
            PagesDimensions.ComposeInstanceWorld(root, defs, world);
            var dims = PagesDimensions.ParseDimensions(
                ChunkedBuffer.FromArray(blob),
                new Dictionary<string, (double X, double Y, double Z)> { ["AABB01"] = (0.0, 0.0, 0.0) },
                world);
            var dim = Assert.Single(dims);
            Assert.Equal((11.0, 22.0, 33.0), dim.A);
        }

        [Fact]
        public void DimensionConnectedPointUsesInnermostInstanceInPath()
        {
            var vid = new byte[] { 0xAA, 0xBB, 0x01 };
            var outerId = new byte[] { 0x11, 0x01 };
            var innerId = new byte[] { 0x22, 0x02 };
            var instPath = Concat(new byte[] { (byte)outerId.Length }, outerId, new byte[] { (byte)innerId.Length }, innerId);
            var refTlv = TlvBytes(0x53FC, Concat(TlvBytes(0x53FD, vid), TlvBytes(0x53FE, instPath)));
            var connA = TlvBytes(0x5BCD, TlvBytes(0x5208, Concat(TlvBytes(0x5209, BitConverter.GetBytes(2u)), TlvBytes(0x520B, refTlv))));
            var connB = TlvBytes(0x5BCE, TlvBytes(0x5208, Concat(TlvBytes(0x5209, BitConverter.GetBytes(1u)), TlvBytes(0x520A, Vec3Bytes(0, 0, 0)))));
            var blob = TlvBytes(0x5BCC, Concat(connA, connB, TlvBytes(0x5BD2, BitConverter.GetBytes(0.0))));

            var leaf = new GeometryBuilder();
            var parentDef = new GeometryBuilder();
            parentDef.Instances.Add(new GeometryBuilderInstance
            {
                PersistentId = "2202",
                RefIdx = 2,
                Matrix = new List<double> { 1, 0, 0, 0, 1, 0, 0, 0, 1, 100.0, 0.0, 0.0, 1.0 },
            });
            var root = new GeometryBuilder();
            root.Instances.Add(new GeometryBuilderInstance
            {
                PersistentId = "1101",
                RefIdx = 1,
                Matrix = new List<double> { 1, 0, 0, 0, 1, 0, 0, 0, 1, 10.0, 0.0, 0.0, 1.0 },
            });
            var defs = new Dictionary<long, Geometry.RawDefinition>
            {
                [1] = new Geometry.RawDefinition { Builder = parentDef },
                [2] = new Geometry.RawDefinition { Builder = leaf },
            };
            var world = new Dictionary<string, List<double>?>();
            PagesDimensions.ComposeInstanceWorld(root, defs, world);
            var dims = PagesDimensions.ParseDimensions(
                ChunkedBuffer.FromArray(blob),
                new Dictionary<string, (double X, double Y, double Z)> { ["AABB01"] = (0.0, 0.0, 0.0) },
                world);
            var dim = Assert.Single(dims);
            Assert.Equal((110.0, 0.0, 0.0), dim.A);
        }

        [Theory]
        [InlineData("annotation_layers_legacy.skp")]
        [InlineData("annotation_layers_vff.skp")]
        public void SdkAnnotationTagsRetainOwnerAndVisibility(string name)
        {
            var model = SkpFile.Open(Path.Combine(AppContext.BaseDirectory, "fixtures", name));

            void Verify(Definition owner, bool nested)
            {
                Assert.Equal(2, owner.Dimensions.Count);
                Assert.All(owner.Dimensions, d => Assert.Equal("尺寸标注", d.Layer));
                Assert.Equal(1, owner.Dimensions.Count(d => d.Hidden));

                Assert.Equal(2, owner.ConstructionPoints.Count);
                Assert.Equal(2, owner.ConstructionLines.Count);

                Assert.Equal(nested ? 1 : 3, owner.Texts.Count);
                Assert.All(owner.Texts, t => Assert.Equal("引注文字", t.Layer));
                Assert.Equal(nested ? 0 : 1, owner.Texts.Count(t => t.Hidden));

                foreach (var txt in owner.Texts)
                {
                    Assert.Equal(txt.Text != "Dot", txt.DisplayLeader);
                    if (nested)
                    {
                        Assert.Equal(0, txt.LeaderType);
                        Assert.NotNull(txt.ScreenPosition);
                        Assert.Equal(0.23, txt.ScreenPosition.Value.X, 9);
                        Assert.Equal(0.67, txt.ScreenPosition.Value.Y, 9);
                    }
                    else
                    {
                        Assert.NotNull(txt.Point);
                        Assert.NotNull(txt.LabelPoint);
                        Assert.Equal(0.0, txt.LabelPoint.Value.X - txt.Point.Value.X, 9);
                        Assert.Equal(0.0, txt.LabelPoint.Value.Y - txt.Point.Value.Y, 9);
                        Assert.Equal(10.0, txt.LabelPoint.Value.Z - txt.Point.Value.Z, 9);
                    }
                }
            }

            Verify(model.Root, false);
            int annotated = 0;
            foreach (var defn in model.Definitions.Values)
            {
                if (defn.Texts.Count == 0) continue;
                Verify(defn, true);
                annotated += 1;
            }
            Assert.Equal(1, annotated);
            if (name == "annotation_layers_vff.skp")
            {
                Assert.Equal(4, model.Dimensions.Count);
                Assert.All(model.Dimensions, d => Assert.Equal("尺寸标注", d.Layer));
            }
        }

        [Fact]
        public void DrawingPropertiesComeFromDimensionNotItsConnections()
        {
            byte[] FreePoint(double x, double y, double z) => TlvBytes(
                0x5208,
                Concat(
                    TlvBytes(0x5209, BitConverter.GetBytes(1u)),
                    TlvBytes(0x520A, Vec3Bytes(x, y, z)),
                    TlvBytes(0x07D0, TlvBytes(0x07D2, new byte[] { 99 }))));

            var body = Concat(
                TlvBytes(0x59D8, TlvBytes(0x07D0, Concat(TlvBytes(0x07D2, new byte[] { 12 }), TlvBytes(0x07D3, new byte[] { 7 })))),
                TlvBytes(0x5BCD, FreePoint(0, 0, 0)),
                TlvBytes(0x5BCE, FreePoint(10, 0, 0)),
                TlvBytes(0x5BD2, BitConverter.GetBytes(3.0)));

            var dims = PagesDimensions.ParseDimensions(
                ChunkedBuffer.FromArray(TlvBytes(0x5BCC, body)),
                new Dictionary<string, (double X, double Y, double Z)>(),
                new Dictionary<string, List<double>?>());
            var dim = Assert.Single(dims);
            Assert.Equal("12", dim.Layer);
            Assert.True(dim.Hidden);
        }

        [Fact]
        public void AnnotationDrawingPropertiesRoundTrip()
        {
            var builder = SkpCreate.NewFile();
            int layer = builder.AddLayer("Annotations");
            ComponentDefinitionBuilder nested;
            using (nested = builder.AddComponentDefinition("TextOnly"))
            {
                nested.AddText(
                    "Screen label",
                    (0, 0, 0),
                    (0, 0, 0),
                    new TextOptions { Layer = layer, Hidden = true, ScreenPosition = (0.23, 0.67) });
            }
            builder.AddInstance(nested);
            builder.AddText("Root label", (2, 3, 4), (0, 0, 5), new TextOptions { Layer = layer, Hidden = true });
            builder.AddText("Default label", (1, 1, 1), (0, 0, 2));
            builder.AddDimension((0, 0, 0), (10, 0, 0), (0, 3, 0), new DimensionOptions { Layer = layer, Hidden = true });
            builder.AddDimension((0, 5, 0), (10, 5, 0), (0, 3, 0));

            var model = SkpFile.Parse(builder.ToBytes());
            Assert.Equal(2, model.Root.Dimensions.Count);
            Assert.Equal("Annotations", model.Root.Dimensions[0].Layer);
            Assert.True(model.Root.Dimensions[0].Hidden);
            Assert.Equal("", model.Root.Dimensions[1].Layer);
            Assert.False(model.Root.Dimensions[1].Hidden);

            Assert.Equal(2, model.Root.Texts.Count);
            Assert.Equal("Annotations", model.Root.Texts[0].Layer);
            Assert.True(model.Root.Texts[0].Hidden);
            Assert.Equal((2.0, 3.0, 4.0), model.Root.Texts[0].Point);
            Assert.Equal((2.0, 3.0, 9.0), model.Root.Texts[0].LabelPoint);
            Assert.Equal("", model.Root.Texts[1].Layer);
            Assert.False(model.Root.Texts[1].Hidden);

            var defn = model.Definitions.Values.Single(d => d.Name == "TextOnly");
            var screen = Assert.Single(defn.Texts);
            Assert.Equal("Annotations", screen.Layer);
            Assert.True(screen.Hidden);
            Assert.Equal(0, screen.LeaderType);
            Assert.Equal((0.23, 0.67), screen.ScreenPosition);
        }

        [Fact]
        public void LeaderlessModelTextRetainsBothPointsAndVisibility()
        {
            var builder = SkpCreate.NewFile();
            void AddToRoot()
            {
                foreach (int lt in new[] { 0, 1, 2 })
                {
                    foreach (bool disp in new[] { false, true })
                    {
                        builder.AddText("Model label", (5, 6, 7), (2, 3, 4), new TextOptions { LeaderType = lt, DisplayLeader = disp });
                    }
                }
                builder.AddText("Screen label", (0, 0, 0), (0, 0, 0), new TextOptions { ScreenPosition = (0.23, 0.67) });
            }

            ComponentDefinitionBuilder nested;
            using (nested = builder.AddComponentDefinition("Leader visibility"))
            {
                foreach (int lt in new[] { 0, 1, 2 })
                {
                    foreach (bool disp in new[] { false, true })
                    {
                        nested.AddText("Model label", (5, 6, 7), (2, 3, 4), new TextOptions { LeaderType = lt, DisplayLeader = disp });
                    }
                }
                nested.AddText("Screen label", (0, 0, 0), (0, 0, 0), new TextOptions { ScreenPosition = (0.23, 0.67) });
            }
            builder.AddInstance(nested);
            AddToRoot();

            var model = SkpFile.Parse(builder.ToBytes());
            void Verify(Definition owner)
            {
                Assert.Equal(7, owner.Texts.Count);
                for (int i = 0; i < 6; i++)
                {
                    var txt = owner.Texts[i];
                    Assert.Equal((5.0, 6.0, 7.0), txt.Point);
                    Assert.Equal((7.0, 9.0, 11.0), txt.LabelPoint);
                    Assert.NotEqual(0, txt.LeaderType);
                    Assert.Equal(i == 3 || i == 5, txt.DisplayLeader);
                }
                Assert.Equal(0, owner.Texts[6].LeaderType);
                Assert.Equal((0.23, 0.67), owner.Texts[6].ScreenPosition);
            }

            Verify(model.Root);
            var defn = Assert.Single(model.Definitions.Values);
            Verify(defn);
        }

        [Fact]
        public void InvalidAnnotationOptionsLeaveArchiveUsable()
        {
            var builder = SkpCreate.NewFile();
            Assert.Throws<SkpWriteException>(() =>
                builder.AddDimension((0, 0, 0), (10, 0, 0), (0, 3, 0), new DimensionOptions { Layer = 999 }));
            Assert.Throws<SkpWriteException>(() =>
                builder.AddDimension((0, 0, 0), (0, 0, 0), (0, 3, 0)));
            Assert.Throws<SkpWriteException>(() =>
                builder.AddText("Bad layer", (0, 0, 0), (1, 0, 0), new TextOptions { Layer = 999 }));
            Assert.Throws<SkpWriteException>(() =>
                builder.AddText("Bad position", (0, 0, 0), (1, 0, 0), new TextOptions { ScreenPosition = (-1.0, 0.5) }));
            Assert.Throws<SkpWriteException>(() =>
                builder.AddText(null!, (0, 0, 0), (1, 0, 0)));
            Assert.Throws<SkpWriteException>(() =>
                builder.AddText("\uD800", (0, 0, 0), (1, 0, 0)));
            Assert.Throws<SkpWriteException>(() =>
                builder.AddText("NaN point", (double.NaN, 0, 0), (1, 0, 0)));
            Assert.Throws<SkpWriteException>(() =>
                builder.AddText("Inf leader", (0, 0, 0), (double.PositiveInfinity, 0, 0)));

            int afterLayer = builder.AddLayer("AfterReject");
            ComponentDefinitionBuilder nested;
            using (nested = builder.AddComponentDefinition("DefAfterReject"))
            {
                Assert.Throws<SkpWriteException>(() =>
                    nested.AddText("\uD800", (0, 0, 0), (1, 0, 0)));
                nested.AddText("Nested valid", (0, 0, 0), (1, 0, 0), new TextOptions { Layer = afterLayer });
            }
            builder.AddInstance(nested);
            builder.AddText("Valid", (0, 0, 0), (1, 0, 0), new TextOptions { Layer = afterLayer });

            var model = SkpFile.Parse(builder.ToBytes());
            Assert.Empty(model.Root.Dimensions);
            var text = Assert.Single(model.Root.Texts);
            Assert.Equal("Valid", text.Text);
            Assert.Equal("AfterReject", text.Layer);
            var defn = Assert.Single(model.Definitions.Values);
            Assert.Equal("Nested valid", Assert.Single(defn.Texts).Text);
        }

        [Fact]
        public void VffDoublingChainComposesOnlyReferencedPathAndCapsVisits()
        {
            var chainPath = Path.Combine(AppContext.BaseDirectory, "fixtures", "vff_doubling_chain.skp");
            var chainModel = SkpFile.Open(chainPath);
            Assert.Equal(22, chainModel.Definitions.Count);
            Assert.Equal(2, chainModel.Root.Instances.Count);

            const int levels = 22;
            var defs = new Dictionary<long, Geometry.RawDefinition>
            {
                [0] = new Geometry.RawDefinition { Builder = new GeometryBuilder() }
            };
            for (int i = 1; i < levels; i++)
            {
                var b = new GeometryBuilder();
                for (int j = 0; j < 2; j++)
                {
                    double tx = j == 0 ? 0.0 : 10.0;
                    string pid = $"{i:X2}{j + 1:X2}";
                    b.Instances.Add(new GeometryBuilderInstance
                    {
                        PersistentId = pid,
                        RefIdx = i - 1,
                        Matrix = new List<double> { 1, 0, 0, 0, 1, 0, 0, 0, 1, tx, 0, 0, 1 },
                    });
                }
                defs[i] = new Geometry.RawDefinition { Builder = b };
            }

            static GeometryBuilder MakeRoot()
            {
                var rb = new GeometryBuilder();
                for (int j = 0; j < 2; j++)
                {
                    double tx = j == 0 ? 0.0 : 10.0;
                    string pid = $"{levels:X2}{j + 1:X2}";
                    rb.Instances.Add(new GeometryBuilderInstance
                    {
                        PersistentId = pid,
                        RefIdx = levels - 1,
                        Matrix = new List<double> { 1, 0, 0, 0, 1, 0, 0, 0, 1, tx, 0, 0, 1 },
                    });
                }
                return rb;
            }

            var pathBytes = new List<byte>();
            for (int i = levels; i >= 1; i--)
            {
                pathBytes.Add(2);
                pathBytes.Add((byte)i);
                pathBytes.Add(2);
            }

            byte[] vidZero = { 0xAA, 0xBB, 0x00 };
            byte[] vid = { 0xAA, 0xBB, 0x01 };
            byte[] vidB = { 0xAA, 0xBB, 0x02 };
            var id2pos = new Dictionary<string, (double X, double Y, double Z)>
            {
                ["AABB00"] = (0.0, 0.0, 0.0),
                ["AABB01"] = (4.0, 0.0, 0.0),
                ["AABB02"] = (5.0, 0.0, 0.0),
            };

            byte[] DimRecord(byte[] raw53fe)
            {
                var refTlv = TlvBytes(0x53FC, Concat(TlvBytes(0x53FD, vid), TlvBytes(0x53FE, raw53fe)));
                var connA = TlvBytes(0x5BCD, TlvBytes(0x5208, Concat(TlvBytes(0x5209, BitConverter.GetBytes(2u)), TlvBytes(0x520B, refTlv))));
                var connB = TlvBytes(0x5BCE, TlvBytes(0x5208, Concat(TlvBytes(0x5209, BitConverter.GetBytes(1u)), TlvBytes(0x520A, Vec3Bytes(0, 0, 0)))));
                return TlvBytes(0x5BCC, Concat(connA, connB, TlvBytes(0x5BD2, BitConverter.GetBytes(0.0))));
            }

            ChunkedBuffer DimBlob(byte[] raw53fe) => ChunkedBuffer.FromArray(DimRecord(raw53fe));

            var rb0 = MakeRoot();
            Assert.Equal(0L, PagesDimensions.AssignVffAnnotations(DimBlob(Array.Empty<byte>()), id2pos, rb0, defs, null, 5));
            Assert.Equal((4.0, 0.0, 0.0), rb0.Dimensions[0].A);

            var rbOk = MakeRoot();
            var worldOk = new List<RawDimension>();
            Assert.Equal(levels, PagesDimensions.AssignVffAnnotations(DimBlob(pathBytes.ToArray()), id2pos, rbOk, defs, worldOk, 64));
            Assert.Equal((224.0, 0.0, 0.0), rbOk.Dimensions[0].A);
            Assert.Equal((224.0, 0.0, 0.0), worldOk[0].A);

            var rbCap = MakeRoot();
            Assert.Equal(10L, PagesDimensions.AssignVffAnnotations(DimBlob(pathBytes.ToArray()), id2pos, rbCap, defs, null, 10));
            Assert.Equal((4.0, 0.0, 0.0), rbCap.Dimensions[0].A);

            var rbMiss = MakeRoot();
            byte[] bogus = { 4, 0xDE, 0xAD, 0xBE, 0xEF };
            Assert.Equal(0L, PagesDimensions.AssignVffAnnotations(DimBlob(bogus), id2pos, rbMiss, defs, null, 25));

            (GeometryBuilder Root, Dictionary<long, Geometry.RawDefinition> Defs) MakeAfterChainGraph()
            {
                var gDefs = new Dictionary<long, Geometry.RawDefinition>();
                foreach (var kv in defs)
                {
                    var nb = new GeometryBuilder();
                    nb.Instances.AddRange(kv.Value.Builder.Instances);
                    gDefs[kv.Key] = new Geometry.RawDefinition { Builder = nb };
                }
                gDefs[101] = new Geometry.RawDefinition { Builder = new GeometryBuilder() };
                var afterB = new GeometryBuilder();
                afterB.Instances.Add(new GeometryBuilderInstance
                {
                    PersistentId = "6501",
                    RefIdx = 101,
                    Matrix = new List<double> { 1, 0, 0, 0, 1, 0, 0, 0, 1, 3.0, 0, 0, 1 },
                });
                gDefs[100] = new Geometry.RawDefinition { Builder = afterB };
                var rb = MakeRoot();
                rb.Instances.Add(new GeometryBuilderInstance
                {
                    PersistentId = "6401",
                    RefIdx = 100,
                    Matrix = new List<double> { 1, 0, 0, 0, 1, 0, 0, 0, 1, 5.0, 0, 0, 1 },
                });
                return (rb, gDefs);
            }

            byte[] subPath = { 2, 0x65, 0x01 };
            var refA = TlvBytes(0x53FC, Concat(TlvBytes(0x53FD, vidZero), TlvBytes(0x53FE, subPath)));
            var refB = TlvBytes(0x53FC, Concat(TlvBytes(0x53FD, vidB), TlvBytes(0x53FE, subPath)));
            var afterDim = TlvBytes(
                0x5BCC,
                Concat(
                    TlvBytes(0x5BCD, TlvBytes(0x5208, Concat(TlvBytes(0x5209, BitConverter.GetBytes(2u)), TlvBytes(0x520B, refA)))),
                    TlvBytes(0x5BCE, TlvBytes(0x5208, Concat(TlvBytes(0x5209, BitConverter.GetBytes(2u)), TlvBytes(0x520B, refB)))),
                    TlvBytes(0x5BD2, BitConverter.GetBytes(0.0))));
            var def100Span = TlvBytes(
                0x01F9,
                TlvBytes(
                    0x1770,
                    TlvBytes(
                        0x1771,
                        TlvBytes(0x157C, Concat(TlvBytes(0x05DC, TlvBytes(0x05DE, new byte[] { 100 })), afterDim)))));

            var (rbAfter, defsAfter) = MakeAfterChainGraph();
            var worldAfter = new List<RawDimension>();
            long visitsAfter = PagesDimensions.AssignVffAnnotations(ChunkedBuffer.FromArray(def100Span), id2pos, rbAfter, defsAfter, worldAfter, 16);
            Assert.True(visitsAfter <= 10L);
            Assert.Equal((3.0, 0.0, 0.0), defsAfter[100].Builder.Dimensions[0].A);
            Assert.Equal((8.0, 0.0, 0.0), defsAfter[100].Builder.Dimensions[0].B);
            Assert.Equal((8.0, 0.0, 0.0), worldAfter[0].A);
            Assert.Equal((13.0, 0.0, 0.0), worldAfter[0].B);

            byte[] danglingMid = { 2, 10, 2, 4, 0xDE, 0xAD, 0xBE, 0xEF };
            var repeatedBytes = new List<byte>();
            for (int r = 0; r < 20; r++) repeatedBytes.AddRange(DimRecord(danglingMid));
            var rbRepeat = MakeRoot();
            Assert.True(PagesDimensions.AssignVffAnnotations(ChunkedBuffer.FromArray(repeatedBytes.ToArray()), id2pos, rbRepeat, defs, null, 16) <= 5L);

            var starveBytes = new List<byte>();
            for (int k = 0; k < 60; k++) starveBytes.AddRange(DimRecord(new byte[] { 2, 10, 2, 2, 0xEE, (byte)k }));
            starveBytes.AddRange(def100Span);
            var (rbStarve, defsStarve) = MakeAfterChainGraph();
            var worldStarve = new List<RawDimension>();
            PagesDimensions.AssignVffAnnotations(ChunkedBuffer.FromArray(starveBytes.ToArray()), id2pos, rbStarve, defsStarve, worldStarve, 16);
            Assert.Equal((8.0, 0.0, 0.0), worldStarve[^1].A);
            Assert.Equal((13.0, 0.0, 0.0), worldStarve[^1].B);
        }
    }
}
