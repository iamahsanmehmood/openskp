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
                builder.AddText("Bad layer", (0, 0, 0), (1, 0, 0), new TextOptions { Layer = 999 }));
            Assert.Throws<SkpWriteException>(() =>
                builder.AddText("Bad position", (0, 0, 0), (1, 0, 0), new TextOptions { ScreenPosition = (-1.0, 0.5) }));
            builder.AddText("Valid", (0, 0, 0), (1, 0, 0));

            var model = SkpFile.Parse(builder.ToBytes());
            Assert.Empty(model.Root.Dimensions);
            var text = Assert.Single(model.Root.Texts);
            Assert.Equal("Valid", text.Text);
        }
    }
}
