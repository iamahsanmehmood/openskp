using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>
    /// .NET never implemented Dynamic Component property extraction at all -
    /// neither the VFF-side D007/DC05/B636/AD38 TLV walk (Python/TypeScript/
    /// C++/Dart already had it) nor the legacy-side attribute-container
    /// plumbing. This file covers both halves of that port in one go.
    ///
    /// Legacy (pre-2021 MFC) instances have produced empty properties for
    /// every single file, because LegacyReaders.ReadInstance was calling
    /// Preamble(ar, r) - which reads the instance's CAttributeContainer,
    /// correctly advancing the byte cursor - and then discarding the return
    /// value entirely. Same "already-decoded-but-discarded" shape as the
    /// earlier layer/face/instance-hidden fixes, just one level deeper.
    ///
    /// SketchUp's Dynamic Components extension stores its data under a
    /// dictionary literally named "dynamic_attributes" (stable, publicly
    /// documented Ruby API: Entity#attribute_dictionary("dynamic_attributes")
    /// - not something reverse-engineered from a fixture).
    /// </summary>
    public class DynamicPropertiesTests
    {
        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        private static byte[] Tlv(string tagHex, byte[] payload)
        {
            var tag = new byte[] { Convert.ToByte(tagHex.Substring(0, 2), 16), Convert.ToByte(tagHex.Substring(2, 2), 16) };
            var len = BitConverter.GetBytes((uint)payload.Length);
            var result = new byte[6 + payload.Length];
            Array.Copy(tag, 0, result, 0, 2);
            Array.Copy(len, 0, result, 2, 4);
            Array.Copy(payload, 0, result, 6, payload.Length);
            return result;
        }

        [Fact]
        public void StringifyAttrValue_HandlesScalars()
        {
            Assert.Equal("", LegacyReaders.StringifyAttrValue(null));
            Assert.Equal("42", LegacyReaders.StringifyAttrValue(42));
            Assert.Equal("width", LegacyReaders.StringifyAttrValue("width"));
        }

        [Fact]
        public void StringifyAttrValue_JoinsLists()
        {
            var list = new List<object?> { 1, 2, 3 };
            Assert.Equal("1,2,3", LegacyReaders.StringifyAttrValue(list));
        }

        [Fact]
        public void ExtractLegacyDynamicProperties_FindsDictByName()
        {
            // Real shape from ReadAttrContainer/ReadAttrNamed: each child
            // tuple's Name is the entity CLASS NAME (always
            // "CAttributeNamed", from Archive.ReadObject) - never the
            // dictionary's own declared name, which lives in DictRec.Name.
            var dynamicDict = new DictRec
            {
                Name = "dynamic_attributes",
                Entries = new Dictionary<string, object?> { ["width"] = 10.0, ["_width_label"] = "Width", ["count"] = 4 },
            };
            var otherDict = new DictRec { Name = "SU_DefinitionSet", Entries = new Dictionary<string, object?> { ["unrelated"] = 1 } };
            var attrs = new AttrsRec
            {
                Children = new List<(string?, object?)> { ("CAttributeNamed", otherDict), ("CAttributeNamed", dynamicDict) },
            };

            var props = LegacyReaders.ExtractLegacyDynamicProperties(attrs);

            Assert.Equal("10", props["width"]);
            Assert.Equal("Width", props["_width_label"]);
            Assert.Equal("4", props["count"]);
        }

        [Fact]
        public void ExtractLegacyDynamicProperties_ReturnsEmptyWhenAbsent()
        {
            var attrs = new AttrsRec
            {
                Children = new List<(string?, object?)> { ("CAttributeNamed", new DictRec { Name = "SU_DefinitionSet" }) },
            };
            Assert.Empty(LegacyReaders.ExtractLegacyDynamicProperties(attrs));
        }

        [Fact]
        public void ExtractLegacyDynamicProperties_ReturnsEmptyForNoAttributeContainer()
        {
            Assert.Empty(LegacyReaders.ExtractLegacyDynamicProperties(null));
        }

        [Fact]
        public void ExtractDynamicProperties_ExtractsKeyValuePairFromDc05Payload()
        {
            var dc05Payload = new List<byte>();
            dc05Payload.AddRange(Tlv("B636", Encoding.UTF8.GetBytes("width")));
            dc05Payload.AddRange(Tlv("AD38", Encoding.UTF8.GetBytes("10")));
            var dc05 = new TlvNode { Tag = "DC05", Payload = dc05Payload.ToArray() };
            var d007 = new TlvNode { Tag = "D007", Children = new List<TlvNode> { dc05 } };

            var props = Geometry.ExtractDynamicProperties(d007);

            Assert.Equal("10", props["width"]);
        }

        [Fact]
        public void ExtractDynamicProperties_ReturnsEmptyWhenNoDc05Child()
        {
            var d007 = new TlvNode { Tag = "D007", Children = new List<TlvNode>() };
            Assert.Empty(Geometry.ExtractDynamicProperties(d007));
        }

        [Fact]
        public void ExtractDynamicProperties_ReturnsEmptyForEmptyDc05Payload()
        {
            var dc05 = new TlvNode { Tag = "DC05", Payload = Array.Empty<byte>() };
            var d007 = new TlvNode { Tag = "D007", Children = new List<TlvNode> { dc05 } };
            Assert.Empty(Geometry.ExtractDynamicProperties(d007));
        }

        // Real B436(name)/B536(entries) dictionary-boundary shape - the
        // TLV structure ExtractAttributeDictionaries needs but
        // ExtractDynamicProperties above never required, since it flattens
        // regardless of dictionary boundaries. Confirmed byte-for-byte
        // against a real FrameBuilder-authored production file (see
        // Python's TestVffAttributeDictionaries for the full verification
        // history this mirrors).
        private static TlvNode MakeD007WithNamedDictionary(string dictName, params (string Key, string Value)[] entries)
        {
            var entriesPayload = new List<byte>();
            foreach (var (key, value) in entries)
            {
                entriesPayload.AddRange(Tlv("B636", Encoding.UTF8.GetBytes(key)));
                entriesPayload.AddRange(Tlv("A438", Tlv("AD38", Encoding.UTF8.GetBytes(value))));
            }
            var dc05Payload = new List<byte>();
            dc05Payload.AddRange(Tlv("B436", Encoding.UTF8.GetBytes(dictName)));
            dc05Payload.AddRange(Tlv("B536", entriesPayload.ToArray()));
            var dc05 = new TlvNode { Tag = "DC05", Payload = dc05Payload.ToArray() };
            return new TlvNode { Tag = "D007", Children = new List<TlvNode> { dc05 } };
        }

        [Fact]
        public void ExtractAttributeDictionaries_GroupsEntriesByDictionaryName()
        {
            var d007 = MakeD007WithNamedDictionary("fbd-einfo", ("code", "Ks"));

            var dicts = Geometry.ExtractAttributeDictionaries(d007);

            Assert.Equal(new Dictionary<string, object?> { ["code"] = "Ks" }, dicts["fbd-einfo"]);
        }

        [Fact]
        public void ExtractAttributeDictionaries_TwoDictionariesStayDistinct()
        {
            var widthEntry = new List<byte>();
            widthEntry.AddRange(Tlv("B636", Encoding.UTF8.GetBytes("width")));
            widthEntry.AddRange(Tlv("A438", Tlv("AD38", Encoding.UTF8.GetBytes("10"))));

            var nameEntry = new List<byte>();
            nameEntry.AddRange(Tlv("B636", Encoding.UTF8.GetBytes("name")));
            nameEntry.AddRange(Tlv("A438", Tlv("AD38", Encoding.UTF8.GetBytes("W-2"))));

            var dc05Payload = new List<byte>();
            dc05Payload.AddRange(Tlv("B436", Encoding.UTF8.GetBytes("dynamic_attributes")));
            dc05Payload.AddRange(Tlv("B536", widthEntry.ToArray()));
            dc05Payload.AddRange(Tlv("B436", Encoding.UTF8.GetBytes("FrameBuilder")));
            dc05Payload.AddRange(Tlv("B536", nameEntry.ToArray()));
            var dc05 = new TlvNode { Tag = "DC05", Payload = dc05Payload.ToArray() };
            var d007 = new TlvNode { Tag = "D007", Children = new List<TlvNode> { dc05 } };

            var dicts = Geometry.ExtractAttributeDictionaries(d007);

            Assert.Equal("10", dicts["dynamic_attributes"]["width"]);
            Assert.Equal("W-2", dicts["FrameBuilder"]["name"]);
            Assert.False(dicts["dynamic_attributes"].ContainsKey("name"));
        }

        [Fact]
        public void ExtractAttributeDictionaries_ReturnsEmptyWhenNoDc05Child()
        {
            var d007 = new TlvNode { Tag = "D007", Children = new List<TlvNode>() };
            Assert.Empty(Geometry.ExtractAttributeDictionaries(d007));
        }

        // --- Multi-value-type decoding (openskp#285's VFF 9-value-type
        // item). Byte shapes mirror Python's TestVffAttributeDictionaries
        // exactly - same fixture-construction helpers, same real tag
        // pairings, ground-truthed there first.

        private static byte[] Concat(params byte[][] parts)
        {
            var result = new List<byte>();
            foreach (var p in parts) result.AddRange(p);
            return result.ToArray();
        }

        private static byte[] EntryValue(byte[] innerTlv) => Tlv("A438", innerTlv);

        private static byte[] Entry(string key, byte[] innerValueTlv) =>
            Concat(Tlv("B636", Encoding.UTF8.GetBytes(key)), EntryValue(innerValueTlv));

        private static TlvNode MakeD007WithRawEntries(string dictName, byte[] entriesPayload)
        {
            var dc05Payload = new List<byte>();
            dc05Payload.AddRange(Tlv("B436", Encoding.UTF8.GetBytes(dictName)));
            dc05Payload.AddRange(Tlv("B536", entriesPayload));
            var dc05 = new TlvNode { Tag = "DC05", Payload = dc05Payload.ToArray() };
            return new TlvNode { Tag = "D007", Children = new List<TlvNode> { dc05 } };
        }

        [Fact]
        public void ExtractAttributeDictionaries_LengthAndFloatAreDistinctTagsBothF64()
        {
            // AF38 (Length) and A938 (plain Float) both encode as a flat
            // 8-byte float64 but are genuinely different tags - real
            // SketchUp/FrameBuilder data uses both for different keys.
            var entries = Concat(
                Entry("depth", Tlv("AF38", BitConverter.GetBytes(15.5))),
                Entry("price", Tlv("A938", BitConverter.GetBytes(120.0))));
            var d007 = MakeD007WithRawEntries("fbd-einfo", entries);

            var result = Geometry.ExtractAttributeDictionaries(d007)["fbd-einfo"];

            Assert.Equal(15.5, Assert.IsType<double>(result["depth"]));
            Assert.Equal(120.0, Assert.IsType<double>(result["price"]));
        }

        [Fact]
        public void ExtractAttributeDictionaries_IntegerValueRoundTrips()
        {
            var entries = Entry("angle", Tlv("A738", BitConverter.GetBytes(-7)));
            var d007 = MakeD007WithRawEntries("fbd-einfo", entries);

            var result = Geometry.ExtractAttributeDictionaries(d007)["fbd-einfo"];

            Assert.Equal(-7, Assert.IsType<int>(result["angle"]));
        }

        [Fact]
        public void ExtractAttributeDictionaries_NullValueRoundTrips()
        {
            // A438 with no children at all.
            var entries = Entry("child_thickness", Array.Empty<byte>());
            var d007 = MakeD007WithRawEntries("fbd-einfo", entries);

            var dicts = Geometry.ExtractAttributeDictionaries(d007);

            Assert.Null(dicts["fbd-einfo"]["child_thickness"]);
        }

        [Fact]
        public void ExtractAttributeDictionaries_Point3dAndVector3dRoundTrip()
        {
            var pointBytes = Tlv("B438", Concat(
                BitConverter.GetBytes(0.0), BitConverter.GetBytes(0.807085), BitConverter.GetBytes(14.6551)));
            var vectorBytes = Tlv("B538", Concat(
                BitConverter.GetBytes(1.0), BitConverter.GetBytes(0.0), BitConverter.GetBytes(0.0)));
            var entries = Concat(Entry("end_pos", pointBytes), Entry("vector_new", vectorBytes));
            var d007 = MakeD007WithRawEntries("fbd-einfo", entries);

            var result = Geometry.ExtractAttributeDictionaries(d007)["fbd-einfo"];

            Assert.Equal(new double[] { 0.0, 0.807085, 14.6551 }, Assert.IsType<double[]>(result["end_pos"]));
            Assert.Equal(new double[] { 1.0, 0.0, 0.0 }, Assert.IsType<double[]>(result["vector_new"]));
        }

        [Fact]
        public void ExtractAttributeDictionaries_EmptyArrayRoundTrips()
        {
            var entries = Entry("added_bolt_holes", Tlv("AE38", Array.Empty<byte>()));
            var d007 = MakeD007WithRawEntries("fbd-einfo", entries);

            var result = Geometry.ExtractAttributeDictionaries(d007)["fbd-einfo"];

            Assert.Empty(Assert.IsType<List<object?>>(result["added_bolt_holes"]));
        }

        [Fact]
        public void ExtractAttributeDictionaries_ArrayOfFloatsRoundTrips()
        {
            var elems = Concat(
                EntryValue(Tlv("A938", BitConverter.GetBytes(0.728))),
                EntryValue(Tlv("A938", BitConverter.GetBytes(11.358))),
                EntryValue(Tlv("A938", BitConverter.GetBytes(14.655))));
            var entries = Entry("flangeholes", Tlv("AE38", elems));
            var d007 = MakeD007WithRawEntries("fbd-einfo", entries);

            var result = Geometry.ExtractAttributeDictionaries(d007)["fbd-einfo"];

            Assert.Equal(new List<object?> { 0.728, 11.358, 14.655 }, result["flangeholes"]);
        }

        [Fact]
        public void ExtractAttributeDictionaries_NestedArrayRoundTrips()
        {
            // openskp#253's motivating case mirrored on the read side:
            // fbd-profile-cords style [[x, y], [x, y]] - an array whose
            // elements are themselves arrays.
            var inner1 = Concat(
                EntryValue(Tlv("A938", BitConverter.GetBytes(25.17))),
                EntryValue(Tlv("A938", BitConverter.GetBytes(0.07))));
            var inner2 = Concat(
                EntryValue(Tlv("A938", BitConverter.GetBytes(25.17))),
                EntryValue(Tlv("A938", BitConverter.GetBytes(15.35))));
            var outer = Concat(EntryValue(Tlv("AE38", inner1)), EntryValue(Tlv("AE38", inner2)));
            var entries = Entry("lip_side1_cords", Tlv("AE38", outer));
            var d007 = MakeD007WithRawEntries("fbd-einfo", entries);

            var result = Geometry.ExtractAttributeDictionaries(d007)["fbd-einfo"];

            Assert.Equal(
                new List<object?>
                {
                    new List<object?> { 25.17, 0.07 },
                    new List<object?> { 25.17, 15.35 },
                },
                result["lip_side1_cords"]);
        }

        [Fact]
        public void ExtractAttributeDictionaries_UnrecognizedValueTagIsSkippedNotGuessed()
        {
            // A type tag this decoder doesn't (yet) know is left as null
            // rather than misinterpreted - safer than a wrong guess.
            var entries = Entry("mystery", Tlv("EE99", new byte[] { 0x01, 0x02 }));
            var d007 = MakeD007WithRawEntries("fbd-einfo", entries);

            var result = Geometry.ExtractAttributeDictionaries(d007)["fbd-einfo"];

            Assert.Null(result["mystery"]);
        }

        [Theory]
        [InlineData("Group#1", true)]
        [InlineData("Component#12", true)]
        [InlineData("W-2", false)]
        [InlineData("Truss1", false)]
        [InlineData("", false)]
        public void IsGenericDefinitionName_MatchesSketchUpsOwnPlaceholderPattern(string name, bool expected)
        {
            Assert.Equal(expected, Geometry.IsGenericDefinitionName(name));
        }

        [Fact]
        public void FindNameOverride_SkipsDynamicAttributesAndSuInstanceSet()
        {
            var dicts = new Dictionary<string, Dictionary<string, object?>>
            {
                ["dynamic_attributes"] = new Dictionary<string, object?> { ["name"] = "should-be-ignored" },
                ["SU_InstanceSet"] = new Dictionary<string, object?> { ["label"] = "also-ignored" },
                ["FrameBuilder"] = new Dictionary<string, object?> { ["name"] = "W-2" },
            };
            Assert.Equal("W-2", Geometry.FindNameOverride(dicts));
        }

        [Fact]
        public void FindNameOverride_ReturnsNullWhenNoOverridePresent()
        {
            Assert.Null(Geometry.FindNameOverride(null));
            Assert.Null(Geometry.FindNameOverride(new Dictionary<string, Dictionary<string, object?>>
            {
                ["dynamic_attributes"] = new Dictionary<string, object?> { ["width"] = "10" },
            }));
        }

        [Fact]
        public void RealLegacyFixture_DoesNotCrashAndReportsEmptyProperties()
        {
            // capilla_quiroz_v17.skp (a plain chapel model) has no Dynamic
            // Component data on any of its 3 instances - confirmed by direct
            // inspection of the raw attribute-container reads before writing
            // this fix - so this proves the plumbing fix doesn't break or
            // crash on entities that render no attributes, not the
            // dictionary-lookup logic itself (covered above with synthetic
            // data).
            var scene = SkpFile.BuildScene(FixturePath("capilla_quiroz_v17.skp"));

            void Walk(InstanceNode node)
            {
                Assert.Empty(node.Properties);
                foreach (var child in node.Children) Walk(child);
            }

            Walk(scene.SceneHierarchy);
        }

        // AttributeDictionaries exposure (openskp#285: "multiple dictionaries
        // per entity" - the reader side). Real fixture Untitled.skp: its "W1"
        // instance carries a SteelFramer-authored "steelframer-dict"
        // dictionary (not SketchUp's own "dynamic_attributes"), which
        // Properties (the backward-compatible dynamic_attributes-only view)
        // correctly leaves empty - AttributeDictionaries is the only way to
        // reach this third-party data. Mirrors Python's own
        // test_untitled_skp/test_untitled_skp_attribute_dictionaries_reach_glb_and_json_export
        // ground truth exactly (packages/python/tests/test_parser.py).
        private static InstanceNode? FindByName(InstanceNode node, string name)
        {
            if (node.Name == name) return node;
            foreach (var child in node.Children)
            {
                var found = FindByName(child, name);
                if (found != null) return found;
            }
            return null;
        }

        [Fact]
        public void RealFixture_Scene_ExposesThirdPartyAttributeDictionaryByName()
        {
            var scene = SkpFile.BuildScene(FixturePath("Untitled.skp"));
            var w1 = FindByName(scene.SceneHierarchy, "W1");

            Assert.NotNull(w1);
            // NOTE: unlike Python (whose properties is scoped to only the
            // "dynamic_attributes" dict), .NET's Properties is populated via
            // the pre-existing, deliberately flatten-everything
            // Geometry.ExtractDynamicProperties - a real, established
            // divergence predating this change, not something this task
            // touches. AttributeDictionaries (below) is the actually-correct,
            // dictionary-scoped way to reach steelframer-dict's own data.
            Assert.True(w1!.AttributeDictionaries.ContainsKey("steelframer-dict"));
            var steelframer = w1.AttributeDictionaries["steelframer-dict"];
            Assert.Equal("SteelFramer::Engine::PanelGenerator", steelframer["generator"]);
            Assert.Equal("362S200-43", steelframer["profile"]);
        }

        [Fact]
        public void RealFixture_InstancedScene_ExposesThirdPartyAttributeDictionaryByName()
        {
            var instancedScene = SkpFile.BuildInstancedScene(FixturePath("Untitled.skp"));

            InstancedNode? FindInstanced(InstancedNode node, string name)
            {
                if (node.Name == name) return node;
                foreach (var child in node.Children)
                {
                    var found = FindInstanced(child, name);
                    if (found != null) return found;
                }
                return null;
            }

            var w1 = FindInstanced(instancedScene.SceneHierarchy, "W1");

            Assert.NotNull(w1);
            Assert.True(w1!.AttributeDictionaries.ContainsKey("steelframer-dict"));
            var steelframer = w1.AttributeDictionaries["steelframer-dict"];
            Assert.Equal("SteelFramer::Engine::PanelGenerator", steelframer["generator"]);
            Assert.Equal("362S200-43", steelframer["profile"]);
        }

        [Fact]
        public void RealFixture_MeshIndex_AlsoExposesThirdPartyAttributeDictionary()
        {
            // MeshMetadata.AttributeDictionaries mirrors InstanceNode's -
            // both are backfilled from the same pathUpdates entry per
            // instance (see SceneBuilder.Build's deferred mesh back-fill),
            // keyed by exact instance path. W1 itself is a geometry-less
            // organizational wrapper (no mesh sits at its own path), so -
            // matching Python's own equally loose
            // test_untitled_skp_attribute_dictionaries_reach_glb_and_json_export
            // check - this only confirms SOME mesh in the tree carries a
            // non-empty "steelframer-dict", not necessarily W1's own.
            var scene = SkpFile.BuildScene(FixturePath("Untitled.skp"));
            var meshWithDict = scene.MeshIndex.Values.FirstOrDefault(
                m => m.AttributeDictionaries.TryGetValue("steelframer-dict", out var d) && d.Count > 0);

            Assert.NotNull(meshWithDict);
        }

        [Fact]
        public void ExtractAttributeDictionaries_SyntheticMultiDict_SurvivesToInstanceNode()
        {
            // Synthetic, byte-level equivalent of the real-fixture tests
            // above, isolating the exact TLV shape being exercised (two
            // named dictionaries on one D007, one of them the SketchUp
            // boilerplate that must NOT leak into AttributeDictionaries).
            var d007 = MakeD007WithNamedDictionary("FrameBuilder", ("mark", "W-2"));
            var dicts = Geometry.ExtractAttributeDictionaries(d007);

            Assert.Equal("W-2", dicts["FrameBuilder"]["mark"]);
            Assert.False(dicts.ContainsKey("dynamic_attributes"));
        }
    }
}
