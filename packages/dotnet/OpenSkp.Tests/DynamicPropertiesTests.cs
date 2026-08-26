using System;
using System.Collections.Generic;
using System.IO;
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
    }
}
