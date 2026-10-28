using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>
    /// Legacy (MFC) text labels anchored to an entity - port of the Python
    /// fix.
    ///
    /// 16 bytes after its font, a CText carries the connection block
    /// dimensions use, [u32 type][u32 4][point3d]: type 0 or 1 when the label
    /// is anchored to nothing (1 for a leader pointing at a free point stored
    /// inline), other types (2, 4, 5 and 7 seen) for a label anchored to an
    /// entity. As in a dimension, the block is followed by the anchored
    /// entity's ref and its instance paths, null and empty for types 0 and 1.
    /// MFC writes an object in full the first time it is referenced, so a
    /// path can hold a whole NEW instance: the component a label is anchored
    /// to, when the label comes first in its entity list. The reader skipped
    /// the block as bytes, so that instance never got its store-map slots and
    /// every later reference was off by them ("class-ref to non-class slot"):
    /// the auditorium (CR4) models of the BRAS database, whose loudspeaker
    /// labels are anchored to the loudspeakers, did not open, and two of its
    /// coupled-room (CR1) models lost root entities (one read 5 of its 318).
    /// The entity list's declared count holds such an instance, but SketchUp
    /// does not always list it again with a back-reference.
    /// </summary>
    public class LegacyAnchoredTextsTests
    {
        private const int Text = 1, FontClass = 2, Font = 3, Thing = 4; // pre-registered slots
        private const int FirstFree = 5;

        private static byte[] U16(int n) => new byte[] { (byte)n, (byte)(n >> 8) };

        private static byte[] U32(uint n) =>
            new byte[] { (byte)n, (byte)(n >> 8), (byte)(n >> 16), (byte)(n >> 24) };

        private static byte[] Zeros(int n) => new byte[n];

        private static byte[] F64s(params double[] xs) => xs.SelectMany(BitConverter.GetBytes).ToArray();

        private static byte[] Utf16(string text) =>
            Cat(new byte[] { 0xFF, 0xFE, 0xFF, (byte)text.Length }, Encoding.Unicode.GetBytes(text));

        private static byte[] Cat(params byte[][] parts) => parts.SelectMany(p => p).ToArray();

        private static readonly byte[] Tail = { (byte)'T', (byte)'A', (byte)'I', (byte)'L' };

        /// <summary>A SketchUp 2015 archive that knows CText, a font and a
        /// stand-in class for the anchored instance, whose objects are 3
        /// bytes long.</summary>
        private static Archive MakeArchive(byte[] data)
        {
            var ar = new Archive(data, 15);
            foreach (var kv in LegacyReaders.Readers) ar.Readers[kv.Key] = kv.Value;
            ar.Readers["CThing"] = (a, r) => r.Raw(3);
            ar.Slots[Text] = new SlotEntry { Kind = "class", Name = "CText" };
            ar.Slots[FontClass] = new SlotEntry { Kind = "class", Name = "CSkFont" };
            ar.Slots[Font] = new SlotEntry { Kind = "obj", Name = "CSkFont" };
            ar.Slots[Thing] = new SlotEntry { Kind = "class", Name = "CThing" };
            ar.ClassSlot["CText"] = Text;
            ar.ClassSlot["CSkFont"] = FontClass;
            ar.ClassSlot["CThing"] = Thing;
            ar.NextSlot = FirstFree;
            return ar;
        }

        /// <summary>A CText's bytes after its class tag. <paramref name="refs"/>
        /// is what follows the connection block: the anchored entity's ref and
        /// its instance paths.</summary>
        private static byte[] TextRecord(uint kind, double[]? point = null, byte[]? refs = null, double[]? label = null) =>
            Cat(
                Zeros(2),                       // no attribute container
                Zeros(8), Zeros(2),             // draw block, layer 0
                U16(Font),                      // the font, a back-reference
                Zeros(16),
                U32(kind), U32(4), F64s(point ?? new double[3]),
                refs ?? Zeros(12),
                F64s(label ?? new double[3]),
                F64s(-0.577, -0.577, -0.577),
                U32(2),
                new byte[] { 0x01, 0x00, 0x00, 0x00, 0x01, 0x00, 0x03, 0x00, 0x00, 0x00, 0x01 }, // arrow 3
                Utf16("LS1"),
                Zeros(5));

        private static byte[] NewThing() => Cat(U16(0x8000 | Thing), new byte[] { (byte)'O', (byte)'B', (byte)'J' });

        /// <summary>Anchored to entity 9 inside an instance written here in full.</summary>
        private static byte[] PathsWithNewInstance() => Cat(U16(9), Zeros(2), U32(1), NewThing(), U32(0));

        /// <summary>No relationships, then a GUID and the definition's name.</summary>
        private static byte[] DefinitionTail() => Cat(Zeros(4), Zeros(2), Zeros(16), Utf16("LS"));

        [Theory]
        [InlineData(2u, 0.0, 0.0, 0.0)]
        [InlineData(4u, 0.0, 0.0, 0.0)]
        [InlineData(5u, 12.5, -3.0, 40.0)]
        [InlineData(7u, 1.0, 2.0, 3.0)]
        public void AnAnchoredLabelReadsTheInstanceWrittenInItsPath(uint kind, double x, double y, double z)
        {
            var data = Cat(TextRecord(kind, new[] { x, y, z }, PathsWithNewInstance()), Tail);
            var ar = MakeArchive(data);
            var r = new LR(data, 0);
            var text = (TextRec)LegacyReaders.ReadText(ar, r);
            Assert.Equal("LS1", text.Text);
            Assert.Equal("obj", ar.Slots[FirstFree].Kind);
            Assert.Equal("CThing", ar.Slots[FirstFree].Name);
            Assert.Equal(Tail, data.Skip(r.Pos).ToArray());
        }

        [Fact]
        public void AFreeLabelReadsAsBefore()
        {
            var data = Cat(TextRecord(1, new[] { 1.0, 2.0, 3.0 }, null, new[] { 4.0, 5.0, 6.0 }), Tail);
            var r = new LR(data, 0);
            var text = (TextRec)LegacyReaders.ReadText(MakeArchive(data), r);
            Assert.Equal("LS1", text.Text);
            Assert.Equal(Tail, data.Skip(r.Pos).ToArray());
        }

        private static List<(int, string?)> Items(List<(int Slot, string? Name, object? Value)> ents) =>
            ents.Select(e => (e.Slot, e.Name)).ToList();

        [Fact]
        public void AnInstanceWrittenInALabelAndNeverListedAgainIsKept()
        {
            var data = Cat(U16(0x8000 | Text), TextRecord(2, null, PathsWithNewInstance()), DefinitionTail());
            var ar = MakeArchive(data);
            var r = new LR(data, 0);
            var ents = LegacyReaders.ReadEntityList(ar, r, 2, "def");
            Assert.Equal(new List<(int, string?)> { (FirstFree, "CText"), (FirstFree + 1, "CThing") }, Items(ents));
            Assert.Equal(DefinitionTail(), data.Skip(r.Pos).ToArray());
        }

        [Fact]
        public void AnInstanceWrittenInALabelAndListedAgainIsListedOnce()
        {
            var data = Cat(
                U16(0x8000 | Text), TextRecord(2, null, PathsWithNewInstance()),
                U16(FirstFree + 1),             // its back-reference
                DefinitionTail());
            var ar = MakeArchive(data);
            var r = new LR(data, 0);
            var ents = LegacyReaders.ReadEntityList(ar, r, 2, "def");
            Assert.Equal(new List<(int, string?)> { (FirstFree, "CText"), (FirstFree + 1, "CThing") }, Items(ents));
            Assert.Equal(DefinitionTail(), data.Skip(r.Pos).ToArray());
        }

        [Fact]
        public void OnlyTheOutermostInstanceOfAPathIsOneOfTheListsItems()
        {
            // A path runs from the outermost instance in; the inner ones live
            // in other definitions.
            var refs = Cat(U16(9), Zeros(2), U32(2), NewThing(), NewThing(), U32(0));
            var data = Cat(U16(0x8000 | Text), TextRecord(2, null, refs), DefinitionTail());
            var ar = MakeArchive(data);
            var r = new LR(data, 0);
            var ents = LegacyReaders.ReadEntityList(ar, r, 2, "def");
            Assert.Equal(new List<(int, string?)> { (FirstFree, "CText"), (FirstFree + 1, "CThing") }, Items(ents));
            Assert.Equal("CThing", ar.Slots[FirstFree + 2].Name); // read, not listed
            Assert.Equal(DefinitionTail(), data.Skip(r.Pos).ToArray());
        }
    }
}
