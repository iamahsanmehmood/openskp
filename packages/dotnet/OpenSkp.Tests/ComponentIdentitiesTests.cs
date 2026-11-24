using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using Xunit;

namespace OpenSkp.Tests
{
    public class ComponentIdentitiesTests
    {
        private static readonly (double, double, double)[] SquarePts =
        {
            (0.0, 0.0, 0.0),
            (10.0, 0.0, 0.0),
            (10.0, 10.0, 0.0),
            (0.0, 10.0, 0.0),
        };

        private static string FixturePath(string name)
        {
            var baseDir = AppContext.BaseDirectory;
            var dir = new DirectoryInfo(baseDir);
            while (dir != null && !File.Exists(Path.Combine(dir.FullName, "OpenSkp.Tests.csproj")))
            {
                dir = dir.Parent;
            }
            return Path.Combine(dir!.FullName, "fixtures", name);
        }

        private static byte[] TlvBytes(string tagHex, params byte[][] payloads)
        {
            var payload = Concat(payloads);
            var outBuf = new byte[6 + payload.Length];
            outBuf[0] = Convert.ToByte(tagHex.Substring(0, 2), 16);
            outBuf[1] = Convert.ToByte(tagHex.Substring(2, 2), 16);
            uint len = (uint)payload.Length;
            outBuf[2] = (byte)(len & 0xFF);
            outBuf[3] = (byte)((len >> 8) & 0xFF);
            outBuf[4] = (byte)((len >> 16) & 0xFF);
            outBuf[5] = (byte)((len >> 24) & 0xFF);
            Array.Copy(payload, 0, outBuf, 6, payload.Length);
            return outBuf;
        }

        private static byte[] Concat(params byte[][] parts)
        {
            int total = parts.Sum(p => p.Length);
            var outBuf = new byte[total];
            int pos = 0;
            foreach (var p in parts)
            {
                Array.Copy(p, 0, outBuf, pos, p.Length);
                pos += p.Length;
            }
            return outBuf;
        }

        private static byte[] EntryValue(byte[] inner) => TlvBytes("A438", inner);

        private static byte[] Entry(string key, byte[] inner) =>
            Concat(TlvBytes("B636", Encoding.UTF8.GetBytes(key)), EntryValue(inner));

        private static byte[] NamedDict(string name, byte[] entries) =>
            Concat(TlvBytes("B436", Encoding.UTF8.GetBytes(name)), TlvBytes("B536", entries));

        private static bool ContainsSubarray(byte[] haystack, byte[] needle)
        {
            for (int i = 0; i <= haystack.Length - needle.Length; i++)
            {
                bool match = true;
                for (int j = 0; j < needle.Length; j++)
                {
                    if (haystack[i + j] != needle[j])
                    {
                        match = false;
                        break;
                    }
                }
                if (match) return true;
            }
            return false;
        }

        [Fact]
        public void ComponentGuidMatchesAcrossClassicAndVffContainers()
        {
            foreach (var filename in new[] { "nesting_2017.skp", "nesting_2020.skp", "nesting_2021.skp" })
            {
                var model = SkpFile.Open(FixturePath(filename));
                var shelves = model.Definitions.Values.Where(d => d.Name == "Shelf").ToList();
                Assert.Single(shelves);
                Assert.Equal("1614E9C58C0B40338881DDE62E6E2D80", shelves[0].Guid);

                int references = 0;
                foreach (var defn in new[] { model.Root }.Concat(model.Definitions.Values))
                {
                    foreach (var inst in defn.Instances)
                    {
                        if (!inst.RefIdx.HasValue) continue;
                        Assert.True(model.Definitions.ContainsKey(inst.RefIdx.Value));
                        references++;
                    }
                }
                Assert.True(references > 0);
                if (filename == "nesting_2021.skp")
                {
                    Assert.Single(model.Root.Instances);
                    Assert.Equal("0BFF75653E97458E9B7238A6E9CF1A99", model.Root.Instances[0].Guid);
                }
            }
        }

        [Fact]
        public void VffDefinitionAndInstanceGuidsUseCanonicalUuidOrder()
        {
            var native = new byte[]
            {
                0xC5, 0xE9, 0x14, 0x16, 0x0B, 0x8C, 0x33, 0x40,
                0x88, 0x81, 0xDD, 0xE6, 0x2E, 0x6E, 0x2D, 0x80,
            };
            const string canonical = "1614E9C58C0B40338881DDE62E6E2D80";
            var defBytes = TlvBytes(
                "7C15",
                TlvBytes("DE05", new byte[] { 1 }),
                TlvBytes("7D15", native),
                TlvBytes("7E15", Encoding.UTF8.GetBytes("Shelf")));
            var defsDict = new Dictionary<long, Geometry.RawDefinition>();
            var defBuf = ChunkedBuffer.FromArray(defBytes);
            Geometry.CollectDefs(Tlv.ParseRecursive(defBuf, 0, defBuf.Length, Tlv.ContainerTags), defsDict);
            Assert.Equal(canonical, defsDict[1].Guid);

            var instBytes = TlvBytes("6419", TlvBytes("6819", native));
            var builder = new GeometryBuilder();
            var instBuf = ChunkedBuffer.FromArray(instBytes);
            Geometry.ExtractGeometryFromNodes(
                Tlv.ParseRecursive(instBuf, 0, instBuf.Length, Tlv.ContainerTags),
                builder);
            Assert.Single(builder.Instances);
            Assert.Equal(canonical, builder.Instances[0].RefGuid);
            Assert.Null(builder.Instances[0].RefIdx);
        }

        [Fact]
        public void DefinitionDictionariesKeepNativeTypesWithoutChildAttributes()
        {
            var values = Concat(
                Entry("count", TlvBytes("A738", BitConverter.GetBytes(-7))),
                Entry("nothing", Array.Empty<byte>()),
                Entry(
                    "values",
                    TlvBytes(
                        "AE38",
                        EntryValue(TlvBytes("A938", BitConverter.GetBytes(2.5))),
                        EntryValue(TlvBytes("AD38", Encoding.UTF8.GetBytes("abc"))))));
            var own = TlvBytes("8813", TlvBytes("D007", TlvBytes("DC05", NamedDict("Plugin", values))));
            var child = TlvBytes(
                "6419",
                TlvBytes("6719", new byte[] { 2 }),
                TlvBytes("D007", TlvBytes("DC05", NamedDict("Plugin", Entry("count", TlvBytes("A738", BitConverter.GetBytes(99)))))));
            var rawBytes = Concat(
                TlvBytes("7C15", TlvBytes("DE05", new byte[] { 1 }), own, child),
                TlvBytes("7C15", TlvBytes("DE05", new byte[] { 2 }), child));

            var defsDict = new Dictionary<long, Geometry.RawDefinition>();
            var buf = ChunkedBuffer.FromArray(rawBytes);
            Geometry.CollectDefs(Tlv.ParseRecursive(buf, 0, buf.Length, Tlv.ContainerTags), defsDict);
            Assert.Equal(2, defsDict.Count);

            var plugin = defsDict[1].AttributeDictionaries["Plugin"];
            Assert.Equal(-7, plugin["count"]);
            Assert.Null(plugin["nothing"]);
            var arr = Assert.IsType<List<object?>>(plugin["values"]);
            Assert.Equal(2, arr.Count);
            Assert.Equal(2.5, arr[0]);
            Assert.Equal("abc", arr[1]);
            Assert.Empty(defsDict[2].AttributeDictionaries);
            Assert.Equal(99, defsDict[1].Builder.Instances[0].AttributeDicts!["Plugin"]["count"]);
        }

        [Fact]
        public void ExplicitModelGuidUsesNativeByteOrder()
        {
            var builder = SkpCreate.NewFile();
            builder.AddFace(SquarePts);
            var native = new byte[]
            {
                0x33, 0x22, 0x11, 0x00, 0x55, 0x44, 0x77, 0x46,
                0x88, 0x99, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF,
            };
            foreach (var guid in new[]
            {
                "00112233-4455-4677-8899-aabbccddeeff",
                "00112233445546778899AABBCCDDEEFF",
            })
            {
                builder.SetGuid(guid);
                var data = builder.ToBytes();
                Assert.True(data.Length >= 68);
                Assert.Equal(native, data.Skip(52).Take(16).ToArray());
            }
            Assert.Throws<SkpWriteException>(() => builder.SetGuid("not-a-guid"));
            Assert.Throws<SkpWriteException>(() => builder.SetGuid("00112233445546778899AABBCCDDEEFZ"));
            Assert.Throws<SkpWriteException>(() => builder.SetGuid("00112233-4455-4677-8899a-abbccddeeff"));
            Assert.Equal(native, builder.ToBytes().Skip(52).Take(16).ToArray());

            builder.SetGuid("");
            var reset = builder.ToBytes();
            Assert.NotEqual(native, reset.Skip(52).Take(16).ToArray());
            Assert.Equal(reset.Skip(52).Take(16).ToArray(), builder.ToBytes().Skip(52).Take(16).ToArray());
            Assert.Equal(0x40, reset[52 + 7] & 0xF0);
            Assert.Equal(0x80, reset[52 + 8] & 0xC0);
        }

        [Fact]
        public void DefinitionGuidPreservedInArchiveAndPublicModel()
        {
            var native = new byte[]
            {
                0x33, 0x22, 0x11, 0x00, 0x55, 0x44, 0x77, 0x46,
                0x88, 0x99, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF,
            };
            foreach (var guid in new[]
            {
                "00112233-4455-4677-8899-aabbccddeeff",
                "00112233445546778899AABBCCDDEEFF",
            })
            {
                var builder = SkpCreate.NewFile();
                ComponentDefinitionBuilder def;
                using (def = builder.AddComponentDefinition("Identified", guid: guid))
                {
                    def.AddFace(SquarePts);
                }
                builder.AddInstance(def);
                var data = builder.ToBytes();
                Assert.True(ContainsSubarray(data, native));
                var model = SkpFile.Parse(data);
                Assert.Single(model.Definitions);
                Assert.Equal("00112233445546778899AABBCCDDEEFF", model.Definitions.Values.First().Guid);
                Assert.Single(model.Root.Instances);
                Assert.NotNull(model.Root.Instances[0].RefIdx);
                Assert.Equal(
                    "00112233445546778899AABBCCDDEEFF",
                    model.Definitions[model.Root.Instances[0].RefIdx!.Value].Guid);
            }
        }

        [Fact]
        public void InvalidDefinitionGuidDoesNotPartiallyAddDefinition()
        {
            var builder = SkpCreate.NewFile();
            Assert.Throws<SkpWriteException>(() => builder.AddComponentDefinition("Invalid", guid: "not-a-guid"));
            ComponentDefinitionBuilder good;
            using (good = builder.AddComponentDefinition("Valid"))
            {
                good.AddFace(SquarePts);
            }
            builder.AddInstance(good);
            var model = SkpFile.Parse(builder.ToBytes());
            Assert.Single(model.Definitions);
            Assert.Equal("Valid", model.Definitions.Values.First().Name);
        }

        [Fact]
        public void DefaultDefinitionGuidsAreDistinctAndStable()
        {
            var builder = SkpCreate.NewFile();
            ComponentDefinitionBuilder a, b;
            using (a = builder.AddComponentDefinition("Same name"))
            {
                a.AddFace(SquarePts);
            }
            using (b = builder.AddComponentDefinition("Same name"))
            {
                b.AddFace(SquarePts);
            }
            builder.AddInstance(a);
            builder.AddInstance(b);
            var first = SkpFile.Parse(builder.ToBytes());
            var second = SkpFile.Parse(builder.ToBytes());
            Assert.Equal(2, first.Definitions.Count);
            var defs = first.Definitions.Values.ToList();
            Assert.NotEqual(defs[0].Guid, defs[1].Guid);
            foreach (var kv in first.Definitions)
            {
                Assert.Equal(kv.Value.Guid, second.Definitions[kv.Key].Guid);
            }
        }
    }
}
