using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;

namespace OpenSkp
{
    /// <summary>
    /// Legacy (classic MFC) SketchUp .skp parser - SketchUp 2013-2020 era.
    ///
    /// Pre-2021 .skp files are not VFF/ZIP containers: after the same UTF-16
    /// header records, the body is one uncompressed MFC CArchive object
    /// stream with a single global 1-based store map. This module walks that
    /// stream and adapts the result to the same raw-parse shape the VFF path
    /// produces (Core.cs), so Parser.cs handles both eras transparently.
    ///
    /// Ported line-for-line from openskp/legacy.py (via the already-verified
    /// TypeScript port at packages/typescript/src/legacy.ts). See that
    /// module's docstring for the full list of format details that differ
    /// from the public 2017 format notes.
    /// </summary>
    internal sealed class LegacyParseError : Exception
    {
        public LegacyParseError(string message) : base(message) { }
    }

    internal static class LegacyBytes
    {
        public static readonly byte[] StrMarker = { 0xFF, 0xFE, 0xFF };

        public static bool BytesEqual(byte[] a, int aOff, byte[] b)
        {
            if (aOff + b.Length > a.Length) return false;
            for (int i = 0; i < b.Length; i++)
            {
                if (a[aOff + i] != b[i]) return false;
            }
            return true;
        }

        public static int FindBytes(byte[] data, byte[] needle, int start = 0, int end = -1)
        {
            if (end < 0) end = data.Length;
            int limit = end - needle.Length;
            for (int i = Math.Max(start, 0); i <= limit; i++)
            {
                bool ok = true;
                for (int j = 0; j < needle.Length; j++)
                {
                    if (data[i + j] != needle[j]) { ok = false; break; }
                }
                if (ok) return i;
            }
            return -1;
        }

        /// <summary>Search for a byte pattern where null entries are wildcards.</summary>
        public static int FindPattern(byte[] data, int?[] pattern, int start = 0, int end = -1)
        {
            if (end < 0) end = data.Length;
            int limit = end - pattern.Length;
            for (int i = Math.Max(start, 0); i <= limit; i++)
            {
                bool ok = true;
                for (int j = 0; j < pattern.Length; j++)
                {
                    int? want = pattern[j];
                    if (want.HasValue && data[i + j] != want.Value) { ok = false; break; }
                }
                if (ok) return i;
            }
            return -1;
        }

        /// <summary>True when the bytes at <paramref name="p"/> are an MFC class-ref
        /// to class <paramref name="slot"/>. Mirrors both encodings Archive.ReadObject
        /// decodes: the short 16-bit form (0x8000|slot) and, for slots past 0x7FFF,
        /// the big-tag escape (0x7FFF followed by a u32 of 0x80000000|slot).</summary>
        public static bool IsClassRef(byte[] data, int p, int slot)
        {
            if (slot <= 0x7FFF)
            {
                return p + 2 <= data.Length && Tlv.ReadU16(data, p) == (0x8000 | slot);
            }
            return p + 6 <= data.Length
                && Tlv.ReadU16(data, p) == 0x7FFF
                && Tlv.ReadU32(data, p + 2) == (0x80000000u | (uint)slot);
        }

        /// <summary>True when the u16 at <paramref name="at"/> can legally
        /// start an object read: a null, an escape, a class definition, a
        /// class-ref to a KNOWN class, or an object back-ref within the
        /// allocated range.</summary>
        public static bool PlausibleListTag(Archive ar, byte[] data, int at)
        {
            if (at + 2 > data.Length) return false;
            ushort t = Tlv.ReadU16(data, at);
            if (t == 0x0000 || t == 0x7FFF || t == 0xFFFF) return true;
            if ((t & 0x8000) != 0)
            {
                return ar.Slots.TryGetValue(t & 0x7FFF, out var ent) && ent.Kind == "class";
            }
            return t < ar.NextSlot;
        }

        public static int[] AsciiCodes(string s) => s.Select(c => (int)c).ToArray();

        public static bool MatchesAscii(byte[] data, int offset, string str)
        {
            if (offset + str.Length > data.Length) return false;
            for (int i = 0; i < str.Length; i++)
            {
                if (data[offset + i] != (byte)str[i]) return false;
            }
            return true;
        }

        public static string ToHex(byte[] data)
        {
            return Tlv.ToHexUpper(data);
        }

        /// <summary>
        /// SketchUp 2020 (v20) writes an extra, undocumented record ahead of
        /// some counts that v17 does not have, which leaves the reader a few
        /// bytes early and makes it read garbage as the count. The filler is
        /// an empty UTF-16 string record followed by zero padding:
        ///
        ///   &lt;ff fe ff&gt; &lt;u8 0&gt;        empty string
        ///   &lt;zero padding&gt;           runs up to the real count
        ///
        /// Rather than hard-code an offset (the number of bytes before the
        /// marker differs per call site), locate the marker in the short
        /// window ahead, then take the first non-zero u32 that follows the
        /// padding. Only the EMPTY-string form counts as filler: a real
        /// string here would mean genuine data, and moving the cursor past
        /// it would corrupt the parse.
        ///
        /// This only ever runs after a count came back implausible (or
        /// zero), so files that were already parsing (v17, and the VFF
        /// path) never reach it.
        ///
        /// <paramref name="countPos"/> is the offset the count was read FROM
        /// (i.e. r.Pos - 4). Returns the corrected count, or null when this
        /// is not the v20 layout.
        /// </summary>
        /// <summary>Widest zero padding seen between the v20 filler's empty
        /// string and the count that follows it (9 and 13 bytes occur in
        /// real files; the ceiling leaves room without letting the probe
        /// wander into unrelated records).</summary>
        private const int MaxV20FillerPad = 29;

        /// <summary>
        /// Locates the count that follows a v20 filler record, given the
        /// offset the bad count was read from. Pure byte logic, exposed for
        /// tests; see <see cref="RetryCountAfterV20Filler"/> for how it is
        /// used.
        /// </summary>
        public static (uint Count, int Next)? FindCountAfterV20Filler(byte[] data, int countPos, uint limit, Archive? ar = null)
        {
            int markerAt = -1;
            for (int i = countPos; i < countPos + 12 && i + 4 <= data.Length; i++)
            {
                if (data[i] == 0xFF && data[i + 1] == 0xFE && data[i + 2] == 0xFF)
                {
                    markerAt = i;
                    break;
                }
            }
            if (markerAt < 0) return null;
            if (data[markerAt + 3] != 0) return null; // non-empty string: real data

            // The count sits past a run of zero padding whose length varies
            // per call site (9 and 13 bytes both occur in real files), but
            // always lands at markerAt + 4 + pad with pad % 4 == 1. Step
            // through those candidate offsets and take the first plausible
            // u32 that is ALSO followed by a legitimate list tag (when an
            // Archive is available to check against) - a numerically
            // plausible count is not enough by itself once the filler can
            // also swallow the field ahead of it (see ReadDefinition's
            // decl-position retry), which raises the odds of a false match.
            //
            // Deliberately NOT "scan forward to the first non-zero byte": a
            // count that is an exact multiple of 256 has a 0x00 low byte,
            // which such a scan cannot tell apart from padding, so it would
            // skip into the count and misalign every later read. Probing
            // whole u32s at 4-byte strides never inspects an individual
            // byte, so those counts round-trip correctly.
            for (int pad = 1; pad <= MaxV20FillerPad; pad += 4)
            {
                int at = markerAt + 4 + pad;
                if (at + 4 > data.Length) break;
                uint count = Tlv.ReadU32(data, at);
                if (count == 0 || count > limit) continue;
                if (ar != null && !PlausibleListTag(ar, data, at + 4)) continue;
                return (count, at + 4);
            }
            return null;
        }

        public static uint? RetryCountAfterV20Filler(LR r, int countPos, uint limit, Archive? ar = null)
        {
            var hit = FindCountAfterV20Filler(r.Data, countPos, limit, ar);
            if (hit == null) return null;
            r.Pos = hit.Value.Next;
            return hit.Value.Count;
        }
    }

    /// <summary>Byte cursor, matching Python's _R / TS's R.</summary>
    internal sealed class LR
    {
        public byte[] Data;
        public int Pos;

        public LR(byte[] data, int pos = 0)
        {
            Data = data;
            Pos = pos;
        }

        public byte U8()
        {
            byte v = Data[Pos];
            Pos += 1;
            return v;
        }

        public ushort U16()
        {
            ushort v = Tlv.ReadU16(Data, Pos);
            Pos += 2;
            return v;
        }

        public uint U32()
        {
            uint v = Tlv.ReadU32(Data, Pos);
            Pos += 4;
            return v;
        }

        public int I32()
        {
            int v = Tlv.ReadI32(Data, Pos);
            Pos += 4;
            return v;
        }

        public double F64()
        {
            double v = Tlv.ReadF64(Data, Pos);
            Pos += 8;
            return v;
        }

        public double[] F64s(int n)
        {
            var outArr = new double[n];
            for (int i = 0; i < n; i++) outArr[i] = F64();
            return outArr;
        }

        public byte[] Raw(int n)
        {
            var v = new byte[n];
            Array.Copy(Data, Pos, v, 0, n);
            Pos += n;
            return v;
        }

        public byte[] Peek(int n)
        {
            int len = Math.Min(n, Data.Length - Pos);
            var v = new byte[len];
            Array.Copy(Data, Pos, v, 0, len);
            return v;
        }

        public ushort PeekU16() => Tlv.ReadU16(Data, Pos);

        public string Utf16()
        {
            if (!LegacyBytes.BytesEqual(Data, Pos, LegacyBytes.StrMarker))
            {
                throw new LegacyParseError($"expected a string record {Ctx()}");
            }
            Pos += 3;
            long n = U8();
            if (n == 0xFF)
            {
                n = U16();
                if (n == 0xFFFF)
                {
                    n = U32();
                }
            }
            var bytes = Raw((int)(2 * n));
            return Encoding.Unicode.GetString(bytes);
        }

        public string Ctx(int back = 16, int fwd = 32)
        {
            int p = Pos;
            int bstart = Math.Max(0, p - back);
            var before = new byte[p - bstart];
            Array.Copy(Data, bstart, before, 0, before.Length);
            int flen = Math.Min(fwd, Data.Length - p);
            var after = new byte[Math.Max(flen, 0)];
            if (flen > 0) Array.Copy(Data, p, after, 0, flen);
            return $"@0x{p:x}: ...{LegacyBytes.ToHex(before)} | {LegacyBytes.ToHex(after)}...";
        }
    }

    internal sealed class SlotEntry
    {
        public string Kind = ""; // "class" or "obj"
        public string? Name;
        public object? Value; // int? schema for "class"; reader result for "obj"
    }

    internal delegate object? LegacyReader(Archive ar, LR r);

    /// <summary>MFC CArchive store-map bookkeeping and object-graph walk,
    /// matching Python's _Archive / TS's Archive.</summary>
    internal sealed class Archive
    {
        public byte[] Data;
        public int Ver;
        public bool HasPid;
        public LR R;
        public Dictionary<int, SlotEntry> Slots = new Dictionary<int, SlotEntry>();
        public Dictionary<string, int> ClassSlot = new Dictionary<string, int>();
        public Dictionary<string, int> ClassSchema = new Dictionary<string, int>();
        public string? CurrentClass;
        public int NextSlot;
        public int WalkBase;
        public Dictionary<string, LegacyReader> Readers = new Dictionary<string, LegacyReader>();
        public int? CurrentLoop;
        public bool InEntityList;
        // Burned store-map indices (see ReadEdgeUse): the writer maps an
        // annotation's connection points into the store map WITHOUT writing
        // bytes, so file back-references beyond each burn run ahead of the
        // walker's numbering. Registrations always stay at WALKER indices -
        // no captured slot ever goes stale - and Backref translates file
        // references through the burn bands instead. Burns holds
        // (fileBandStart, width) per event; CumDelta their total;
        // AnnotWatermark the walker slot right after the last annotation
        // record - the only place a band can start.
        public List<(int Start, int Width)> Burns = new List<(int, int)>();
        public int CumDelta;
        public int? AnnotWatermark;
        public List<int> BurnStack = new List<int>();  // per-entity-list burned-item credits
        // Cached CConstructionLine trailer width, self-calibrated on the
        // first guide line of the file (see ReadConstructionLine).
        public int? ClineTail;

        public Archive(byte[] data, int ver)
        {
            Data = data;
            Ver = ver;
            HasPid = ver >= 17;
            R = new LR(data);
        }

        public int Alloc(SlotEntry entry)
        {
            int s = NextSlot;
            Slots[s] = entry;
            NextSlot += 1;
            return s;
        }

        public (int? Slot, string? Name, object? Value) ReadObject(LR r, string? expect = null)
        {
            ushort tag = r.U16();
            if (tag == 0)
            {
                return (null, null, null);
            }
            if (tag == 0x7FFF)
            {
                uint big = r.U32();
                if ((big & 0x80000000) != 0)
                {
                    return NewOfClass(r, (int)(big & 0x7FFFFFFF), expect);
                }
                return Backref((int)big, r);
            }
            if (tag == 0xFFFF)
            {
                ushort schema = r.U16();
                ushort namelen = r.U16();
                if (namelen > 40)
                {
                    throw new LegacyParseError($"implausible class name length {r.Ctx()}");
                }
                var nameBytes = r.Raw(namelen);
                string name = Encoding.ASCII.GetString(nameBytes);
                Alloc(new SlotEntry { Kind = "class", Name = name, Value = (int)schema });
                ClassSlot[name] = NextSlot - 1;
                ClassSchema[name] = schema;
                return NewObj(r, name);
            }
            if ((tag & 0x8000) != 0)
            {
                return NewOfClass(r, tag & 0x7FFF, expect);
            }
            return Backref(tag, r);
        }

        private (int, string, object?) NewOfClass(LR r, int cslot, string? expect)
        {
            if (!Slots.TryGetValue(cslot, out var ent))
            {
                if (expect == null)
                {
                    throw new LegacyParseError($"class-ref to unknown slot {cslot} {r.Ctx()}");
                }
                ent = new SlotEntry { Kind = "class", Name = expect, Value = null };
                Slots[cslot] = ent;
                ClassSlot[expect] = cslot;
            }
            if (ent.Kind != "class")
            {
                throw new LegacyParseError($"class-ref to non-class slot {cslot} ({ent.Name}) {r.Ctx()}");
            }
            return NewObj(r, ent.Name!);
        }

        private (int, string, object?) NewObj(LR r, string name)
        {
            InEntityList = false;
            int slot = Alloc(new SlotEntry { Kind = "obj", Name = name, Value = null });
            if (!Readers.TryGetValue(name, out var reader))
            {
                throw new LegacyParseError($"no reader for class {name} {r.Ctx()}");
            }
            string? prevClass = CurrentClass;
            CurrentClass = name;
            object? value;
            try
            {
                value = reader(this, r);
            }
            finally
            {
                CurrentClass = prevClass;
            }
            Slots[slot] = new SlotEntry { Kind = "obj", Name = name, Value = value };
            if (name == "CDimensionLinear" || name == "CText")
            {
                AnnotWatermark = NextSlot;
            }
            return (slot, name, value);
        }

        /// <summary>Map a FILE store-map index to the walker's numbering
        /// through the burn bands. Returns null when the reference points
        /// INTO a band (a phantom, never-serialized connection point).</summary>
        private int? TranslateRef(int slot)
        {
            int offset = 0;
            foreach (var (start, width) in Burns)
            {
                if (slot < start) break;
                if (slot < start + width) return null;
                offset += width;
            }
            return slot - offset;
        }

        private (int?, string?, object?) Backref(int slot, LR r)
        {
            if (Burns.Count > 0 && slot >= Burns[0].Start)
            {
                var walker = TranslateRef(slot);
                if (walker == null)
                {
                    // a phantom (burned) connection-point index - annotation
                    // metadata only; nothing was ever serialized for it
                    return (slot, "reserved", null);
                }
                slot = walker.Value;
            }
            if (!Slots.TryGetValue(slot, out var ent))
            {
                if (slot < WalkBase)
                {
                    return (slot, "premodel", null);
                }
                throw new LegacyParseError($"back-ref to unwalked slot {slot} {r.Ctx()}");
            }
            if (ent.Kind == "class")
            {
                throw new LegacyParseError($"back-ref to class slot {slot} {r.Ctx()}");
            }
            return (slot, ent.Name, ent.Value);
        }
    }

    // ── shared record blocks ─────────────────────────────────────────────

    internal sealed class DrawBase
    {
        public int Mat;
        public int Hidden;
        public int Soft;
        public int Smooth;
        public int Layer;
    }

    internal sealed class PreambleResult
    {
        public object? Attrs;
        public int Pid;
    }

    internal sealed class VertexRec { public double[] Xyz = new double[3]; }
    internal sealed class EdgeRec { public DrawBase Db = new DrawBase(); public int? Curve; public int? V1; public int? V2; }
    internal sealed class CurveRec { public uint N; }
    internal sealed class ArcCurveRec { }
    internal sealed class EdgeUseRec { public int? Edge; public int Sense; }
    internal sealed class LoopRec { public List<EdgeUseRec> Uses = new List<EdgeUseRec>(); }
    internal sealed class FaceRec
    {
        public DrawBase Db = new DrawBase();
        public double[] Plane = new double[4];
        public List<LoopRec> Loops = new List<LoopRec>();
        public int BackMat;
        public AttrsRec? Attrs;
    }
    internal sealed class AttrsRec { public List<(string? Name, object? Value)> Children = new List<(string?, object?)>(); }
    internal sealed class DictRec { public string Name = ""; public Dictionary<string, object?> Entries = new Dictionary<string, object?>(); }
    internal sealed class LayerRec { public string Name = ""; public int Hidden; public byte[] Rgba = new byte[4]; }
    internal sealed class TextureBlockRec
    {
        public byte[] Rgba = new byte[4];
        public double Opacity;
        public int UseOpacity;
        public int? TexDib;
        public double TexW;
        public double TexH;
        public string TexFile = "";
        public bool Colorized;
    }
    internal sealed class MaterialRec
    {
        public string Name = "";
        public byte[] Rgba = { 128, 128, 128, 255 };
        public double Opacity;
        public int UseOpacity;
        public int? TexDib;
        public double TexW;
        public double TexH;
        public string TexFile = "";
        public bool Colorized;
        public bool HasTexture;
    }
    internal sealed class DibRec { public uint Subtype; public byte[] Data = Array.Empty<byte>(); }
    internal sealed class FtcRec
    {
        public double[] Front = new double[9];
        public double[] Back = new double[9];
        public List<double[]> FrontPins = new List<double[]>();
        public List<double[]> BackPins = new List<double[]>();
        public bool FrontProjected;
        public bool BackProjected;
    }
    internal sealed class CameraRec { }
    internal sealed class ThumbnailRec { public int? Dib; }
    internal sealed class ImageRec { public DrawBase Db = new DrawBase(); public int? Def; public double[] Xform = new double[12]; public string Guid = ""; }
    internal sealed class RelationshipRec { }
    internal sealed class ConstructionLineRec { }
    internal sealed class ConstructionPointRec { public DrawBase Db = new DrawBase(); public double[] Pos = new double[3]; }
    internal sealed class SectionPlaneRec { public DrawBase Db = new DrawBase(); public double[] Plane = new double[4]; public string Name = ""; public string Label = ""; }
    internal sealed class FontRec { }
    internal sealed class DimLinearRec { public DrawBase Db = new DrawBase(); public string Text = ""; }
    internal sealed class TextRec { public DrawBase Db = new DrawBase(); public string Text = ""; }
    internal sealed class DefinitionRec
    {
        public string Name = "";
        public string Guid = "";
        public List<(int Slot, string? Name, object? Value)> Ents = new List<(int, string?, object?)>();
        public bool FacesCamera;
        public bool ShadowsFaceSun;
    }
    internal sealed class InstanceRec
    {
        public DrawBase Db = new DrawBase();
        public int? Def;
        public double[] Xf = new double[13];
        public string Name = "";
        public string Guid = "";
        public AttrsRec? Attrs;
    }

    internal static class LegacyReaders
    {
        public static PreambleResult Preamble(Archive ar, LR r)
        {
            var (_, _, attrs) = ar.ReadObject(r, "CAttributeContainer");
            int pid = 0;
            if (ar.HasPid)
            {
                byte mask = r.U8();
                for (int bit = 0; bit < 8; bit++)
                {
                    if ((mask & (1 << bit)) != 0)
                    {
                        pid |= r.U8() << (8 * bit);
                    }
                }
            }
            return new PreambleResult { Attrs = attrs, Pid = pid };
        }

        public static DrawBase Drawbase(Archive ar, LR r)
        {
            var b = r.Raw(8);
            // The layer field is normally a u16 id, but an entity can carry
            // the layer BY OBJECT instead (seen on real 2018 instances): a
            // full inline CLayer record on first use, an escaped back-ref
            // to it on later siblings. Layer ids never have the 0x8000 bit
            // and never equal 0x7FFF, so both object forms are unambiguous.
            // (A 2-byte back-ref would collide with the id space - not seen
            // in any file; by-object layers have only appeared in
            // >32k-object archives where refs escape anyway.)
            bool haveLayCls = ar.ClassSlot.TryGetValue("CLayer", out int layCls);
            ushort tag = r.PeekU16();
            int layer;
            if (haveLayCls && tag == (0x8000 | layCls))
            {
                ar.ReadObject(r, "CLayer");
                layer = 0;                   // by-object layer: keep the default id
            }
            else if (tag == 0x7FFF)
            {
                r.U16();
                uint big = r.U32();
                if ((big & 0x80000000) != 0)
                {
                    throw new LegacyParseError($"drawbase layer: unexpected class {r.Ctx()}");
                }
                layer = 0;                   // by-object layer (back-ref)
            }
            else
            {
                layer = r.U16();
            }
            return new DrawBase
            {
                Mat = Tlv.ReadU16(b, 0),
                Hidden = b[2],
                Soft = b[5],
                Smooth = b[6],
                Layer = layer,
            };
        }

        public static object ReadVertex(Archive ar, LR r)
        {
            Preamble(ar, r);
            return new VertexRec { Xyz = r.F64s(3) };
        }

        public static object ReadEdge(Archive ar, LR r)
        {
            Preamble(ar, r);
            var db = Drawbase(ar, r);
            var (s1, _, _) = ar.ReadObject(r, "CVertex");
            var (s2, _, _) = ar.ReadObject(r, "CVertex");
            var (cs, cn, _) = ar.ReadObject(r);
            if (cn != null && cn != "CCurve" && cn != "CArcCurve")
            {
                throw new LegacyParseError($"edge curve pointer resolved to {cn} {r.Ctx()}");
            }
            return new EdgeRec { Db = db, Curve = cs, V1 = s1, V2 = s2 };
        }

        public static object ReadCurve(Archive ar, LR r)
        {
            Preamble(ar, r);
            r.U8();
            uint n = r.U32();
            return new CurveRec { N = n };
        }

        public static object ReadArcCurve(Archive ar, LR r)
        {
            Preamble(ar, r);
            r.Raw(5);
            r.F64s(14);
            return new ArcCurveRec();
        }

        /// <summary>Record that the writer burned <paramref name="delta"/>
        /// store-map indices without serializing any bytes for them.
        ///
        /// SketchUp maps an annotation's connection-point objects into the
        /// MFC store map (CArchive::MapObject) when a dimension or leader
        /// text is attached to geometry - each mapping consumes an index,
        /// but nothing is written to the stream, so the file's later
        /// back-references run ahead of a byte-exact walk. The band starts
        /// right after the last annotation record (in FILE numbering);
        /// registrations never move - Backref translates file references
        /// through the recorded bands instead, so no slot value captured
        /// anywhere can go stale.</summary>
        private static void RegisterBurn(Archive ar, int delta)
        {
            ar.Burns.Add((ar.AnnotWatermark!.Value + ar.CumDelta, delta));
            ar.CumDelta += delta;
            ar.AnnotWatermark = null;
            // each burn event corresponds to ONE phantom top-level entity
            // that the entity list's declared count includes but the
            // stream never carries - credit it so the list doesn't run
            // past its real end
            if (ar.BurnStack.Count > 0)
            {
                ar.BurnStack[ar.BurnStack.Count - 1] += 1;
            }
        }

        public static object ReadEdgeUse(Archive ar, LR r)
        {
            Preamble(ar, r);
            var (es, _, _) = ar.ReadObject(r, "CEdge");
            byte sense = r.U8();
            // parent-loop back-ref: the alignment oracle. Read as a RAW
            // file index - after annotations the claimed index can sit
            // AHEAD of the walker's numbering (burned MapObject indices,
            // see RegisterBurn), which is a correction signal, not a
            // mis-parse.
            int p0 = r.Pos;
            ushort tag = r.U16();
            int? ps;
            if (tag == 0x7FFF)
            {
                uint big = r.U32();
                if ((big & 0x80000000) != 0)
                {
                    throw new LegacyParseError($"edge-use parent is a new object {r.Ctx()}");
                }
                ps = (int)big;
            }
            else if (tag == 0xFFFF || (tag & 0x8000) != 0)
            {
                throw new LegacyParseError($"edge-use parent is a new object {r.Ctx()}");
            }
            else
            {
                ps = tag != 0 ? (int?)tag : null;
            }
            int? expected = ar.CurrentLoop != null ? ar.CurrentLoop + ar.CumDelta : (int?)null;
            if (ps != expected)
            {
                int delta = (ps.HasValue && expected.HasValue) ? ps.Value - expected.Value : 0;
                if (delta > 0 && delta <= 4096 && ar.AnnotWatermark != null)
                {
                    RegisterBurn(ar, delta);
                }
                else
                {
                    r.Pos = p0;
                    throw new LegacyParseError($"edge-use parent slot {ps} != current loop {expected} {r.Ctx()}");
                }
            }
            return new EdgeUseRec { Edge = es, Sense = sense };
        }

        public static object ReadLoop(Archive ar, LR r)
        {
            int mySlot = ar.NextSlot - 1;
            var prev = ar.CurrentLoop;
            ar.CurrentLoop = mySlot;
            Preamble(ar, r);
            r.Raw(2);
            var uses = new List<EdgeUseRec>();
            while (true)
            {
                if (r.PeekU16() == 0)
                {
                    r.Pos += 2;
                    break;
                }
                var (_, _, v) = ar.ReadObject(r, "CEdgeUse");
                uses.Add((EdgeUseRec)v!);
            }
            ar.CurrentLoop = prev;
            return new LoopRec { Uses = uses };
        }

        public static object ReadFace(Archive ar, LR r)
        {
            var pre = Preamble(ar, r);
            var db = Drawbase(ar, r);
            var plane = r.F64s(4);
            uint nloops = r.U32();
            if (nloops > 10000)
            {
                throw new LegacyParseError($"implausible loop count {nloops} {r.Ctx()}");
            }
            var loops = new List<LoopRec>();
            for (int i = 0; i < nloops; i++)
            {
                var (_, _, v) = ar.ReadObject(r, "CLoop");
                loops.Add((LoopRec)v!);
            }
            ushort backMat = r.U16();
            return new FaceRec { Db = db, Plane = plane, Loops = loops, BackMat = backMat, Attrs = pre.Attrs as AttrsRec };
        }

        public static object ReadAttrContainer(Archive ar, LR r)
        {
            Preamble(ar, r);
            var children = new List<(string?, object?)>();
            while (true)
            {
                if (r.PeekU16() == 0)
                {
                    r.Pos += 2;
                    break;
                }
                var (_, n, v) = ar.ReadObject(r, "CAttributeNamed");
                children.Add((n, v));
            }
            return new AttrsRec { Children = children };
        }

        private static object? ReadTyped(LR r, int t)
        {
            if (t == 0x00) return null;
            if (t == 0x04) return r.I32();
            if (t == 0x06) return r.F64();
            if (t == 0x07) return r.U8();
            if (t == 0x09) return r.U32();
            if (t == 0x0a) return r.Utf16();
            if (t == 0x0c) return r.F64();           // Length (a double, inches)
            if (t == 0x0b)
            {
                uint n = r.U32();
                if (n > 100000)
                {
                    throw new LegacyParseError($"implausible attr array count {r.Ctx()}");
                }
                var arr = new List<object?>();
                for (int i = 0; i < n; i++) arr.Add(ReadTyped(r, r.U8()));
                return arr;
            }
            if (t == 0x11) return r.F64s(3);         // 3D point (Geom::Point3d)
            if (t == 0x12) return r.F64s(3);         // 3D vector (Geom::Vector3d)
            throw new LegacyParseError($"unknown attribute value type 0x{t:x} {r.Ctx()}");
        }

        public static object ReadAttrNamed(Archive ar, LR r)
        {
            Preamble(ar, r);
            r.Raw(4);
            string dictname = r.Utf16();
            var entries = new Dictionary<string, object?>();
            while (true)
            {
                string key = r.Utf16();
                if (key == "") break;
                entries[key] = ReadTyped(r, r.U8());
            }
            r.U32();
            return new DictRec { Name = dictname, Entries = entries };
        }

        public static object ReadLayer(Archive ar, LR r)
        {
            Preamble(ar, r);
            string name = r.Utf16();
            var mid = new List<byte>();
            while (mid.Count < 8 && !LegacyBytes.BytesEqual(r.Peek(3), 0, LegacyBytes.StrMarker))
            {
                mid.Add(r.Raw(1)[0]);
            }
            r.Utf16();                       // internal name ("Layer_<name>")
            ushort flags = r.U16();
            if ((flags & 0x00FF) != 0)
            {
                // Colour-by-layer with a TEXTURED material: instead of the
                // flat RGBA, the layer embeds the same texture block a
                // CMaterial carries (SketchUp Pro assigns full materials to
                // layers). Low byte of the flag word set = textured; a
                // plain colour layer has 0 there (its high byte carries an
                // unrelated flag, so the word as a whole is non-zero
                // either way).
                var tex = TextureBlock(ar, r);
                r.Raw(4);                    // trailing u32
                return new LayerRec { Name = name, Hidden = mid.Count > 0 ? mid[0] : 0, Rgba = tex.Rgba };
            }
            var rgba = r.Raw(4);
            r.Utf16();
            r.Raw(21);
            return new LayerRec { Name = name, Hidden = mid.Count > 0 ? mid[0] : 0, Rgba = rgba };
        }

        /// <summary>The textured-material payload: an embedded CDib plus
        /// applied size, source file name, average colour, and opacity.
        /// Shared verbatim between a CMaterial with a texture and a
        /// colour-by-layer CLayer that carries a textured material.</summary>
        public static TextureBlockRec TextureBlock(Archive ar, LR r)
        {
            r.Raw(ar.Ver >= 17 ? 2 : 1);        // texture flag pad
            var (s, _, dib) = ar.ReadObject(r, "CDib");
            if (!(dib is DibRec))
            {
                throw new LegacyParseError($"texture object is not a dib {r.Ctx()}");
            }
            // optional u32 between the dib and the 2 x f64 applied size
            int marker = LegacyBytes.FindBytes(r.Data, LegacyBytes.StrMarker, r.Pos, r.Pos + 28);
            if (marker - r.Pos == 20)
            {
                r.U32();
            }
            else if (marker - r.Pos != 16)
            {
                throw new LegacyParseError($"texture size block misaligned {r.Ctx()}");
            }
            double w = r.F64();
            double h = r.F64();
            string fname = r.Utf16();
            var avg = r.Raw(9);              // RGBA + 00 + RGBA (colour stored twice)
            r.Utf16();
            var blob = r.Raw(8);             // u32 + u32 colorized flag
            double opacity = r.F64();
            byte useOp = r.U8();
            // A colourized (re-tinted) texture stores the ORIGINAL image
            // plus the tint as the average colour; flagged by the second
            // blob u32 or by alpha 0xFF on the stored colour.
            bool colorized = blob[4] != 0 || avg[3] == 0xFF;
            return new TextureBlockRec
            {
                Rgba = avg.Take(4).ToArray(),
                Opacity = opacity,
                UseOpacity = useOp,
                TexDib = s,
                TexW = w,
                TexH = h,
                TexFile = fname,
                Colorized = colorized,
            };
        }

        public static object ReadMaterial(Archive ar, LR r)
        {
            Preamble(ar, r);
            string name = r.Utf16();
            ushort texflag = r.U16();
            var outRec = new MaterialRec { Name = name };
            if (texflag == 0)
            {
                var rgba = r.Raw(4);
                r.Utf16();
                r.Raw(8);
                double opacity = r.F64();
                byte useOp = r.U8();
                outRec.Rgba = rgba;
                outRec.Opacity = opacity;
                outRec.UseOpacity = useOp;
            }
            else
            {
                var tex = TextureBlock(ar, r);
                outRec.Rgba = tex.Rgba;
                outRec.Opacity = tex.Opacity;
                outRec.UseOpacity = tex.UseOpacity;
                outRec.TexDib = tex.TexDib;
                outRec.TexW = tex.TexW;
                outRec.TexH = tex.TexH;
                outRec.TexFile = tex.TexFile;
                outRec.Colorized = tex.Colorized;
                outRec.HasTexture = true;
            }
            return outRec;
        }

        public static object ReadDib(Archive ar, LR r)
        {
            uint subtype = r.U32();
            uint length = r.U32();
            if (length > r.Data.Length)
            {
                throw new LegacyParseError($"implausible dib length {length} {r.Ctx()}");
            }
            var data = r.Raw((int)length);
            return new DibRec { Subtype = subtype, Data = data };
        }

        public static object ReadFtc(Archive ar, LR r)
        {
            Preamble(ar, r);
            r.U32();
            var ks = r.F64s(24);
            uint frontPinsCount = r.U32();
            var frontPins = new List<double[]>();
            for (int i = 0; i < frontPinsCount; i++) frontPins.Add(r.F64s(4));
            uint backPinsCount = r.U32();
            var backPins = new List<double[]>();
            for (int i = 0; i < backPinsCount; i++) backPins.Add(r.F64s(4));
            uint fflags = r.U32();
            uint bflags = r.U32();
            return new FtcRec
            {
                Front = ks.Take(9).ToArray(),
                Back = ks.Skip(12).Take(9).ToArray(),
                FrontPins = frontPins,
                BackPins = backPins,
                FrontProjected = (fflags & 2) != 0,
                BackProjected = (bflags & 2) != 0,
            };
        }

        public static object ReadCamera(Archive ar, LR r)
        {
            r.Raw(137);
            r.U16();
            r.Utf16();
            r.Raw(33);
            return new CameraRec();
        }

        public static object ReadThumbnail(Archive ar, LR r)
        {
            Preamble(ar, r);
            ar.ReadObject(r, "CCamera");
            var (dibSlot, _, _) = ar.ReadObject(r, "CDib");
            return new ThumbnailRec { Dib = dibSlot };
        }

        /// <summary>CImage: an Image entity - instance-shaped: a back-ref
        /// to the (already walked) CComponentDefinition holding the
        /// image's face and texture, a 3x4 placement, a constant 1.0, the
        /// source path string (empty in every sample), and a 16-byte GUID.
        /// It appears as a normal entity-list item inside the definition
        /// that owns the image (typically a face-me/photo definition),
        /// whose own tail the ordinary definition reader then consumes.
        /// Calibrated byte-exact on two real files - an 80 MB v18 and a
        /// 661 MB v17 - both previously rejected outright with "no reader
        /// for class CImage".</summary>
        public static object ReadImage(Archive ar, LR r)
        {
            Preamble(ar, r);
            var db = Drawbase(ar, r);
            var (ds, _, _) = ar.ReadObject(r);          // the image's definition
            var xform = r.F64s(12);
            r.F64();                                     // constant 1.0
            r.Utf16();                                    // source path
            var guid = r.Raw(16);
            return new ImageRec { Db = db, Def = ds, Xform = xform, Guid = LegacyBytes.ToHex(guid) };
        }

        /// <summary>A reference-to-entity tag: dimension connection points
        /// and text leader attachments. Unlike ReadObject's back-ref path,
        /// this tolerates a slot the walk has not reached yet - SketchUp
        /// serializes a label/dimension BEFORE the entity it anchors to
        /// when both live in the same entity list, so the reference can
        /// legitimately point forward. Returns the slot number, or null
        /// for a null reference.</summary>
        private static int? EntityRef(Archive ar, LR r)
        {
            ushort tag = r.U16();
            if (tag == 0) return null;
            if (tag == 0x7FFF)
            {
                uint big = r.U32();
                if ((big & 0x80000000) != 0)
                {
                    throw new LegacyParseError($"entity ref is a new object {r.Ctx()}");
                }
                return (int)big;
            }
            if (tag == 0xFFFF || (tag & 0x8000) != 0)
            {
                throw new LegacyParseError($"entity ref is a new object {r.Ctx()}");
            }
            return tag;
        }

        public static object ReadRelationship(Archive ar, LR r)
        {
            // two object pointers (small maps: two u16 back-refs - which
            // read like the "u32" of the public notes; big maps escalate
            // them to big-tags). They bind an annotation to the entity it
            // labels, and the annotation side is routinely serialized
            // BEFORE the geometry side - so these can point forward, past
            // the walk cursor; EntityRef tolerates that where ReadObject's
            // back-ref path (rightly) does not.
            Preamble(ar, r);
            EntityRef(ar, r);
            EntityRef(ar, r);
            return new RelationshipRec();
        }

        /// <summary>True when the u16 at <paramref name="at"/> starts an
        /// object read in one of the UNAMBIGUOUS forms: null, escape, class
        /// definition, or a class-ref to a class already known. Plain
        /// object back-refs are excluded on purpose - any 2-byte junk below
        /// 0x8000 would qualify, which is exactly the ambiguity this check
        /// exists to avoid.</summary>
        private static bool StrictNextTag(Archive ar, byte[] data, int at, bool allowNull = true)
        {
            if (at + 2 > data.Length) return false;
            ushort t = Tlv.ReadU16(data, at);
            if (t == 0x0000) return allowNull;
            if (t == 0x7FFF || t == 0xFFFF) return true;
            if ((t & 0x8000) != 0)
            {
                return ar.Slots.TryGetValue(t & 0x7FFF, out var ent) && ent.Kind == "class";
            }
            return false;
        }

        public static object ReadConstructionLine(Archive ar, LR r)
        {
            Preamble(ar, r);
            Drawbase(ar, r);
            r.F64s(3);
            r.F64s(3);
            r.F64s(2);                       // line params (+-~4.4e29 = infinite)
            // The trailing block varies by the WRITING BUILD, not cleanly
            // by version: 7 bytes on the v17 calibration corpus, 4 on v16
            // and on a real v18, 0 on another real v17. Self-calibrate on
            // the first guide line of the file - the length that lands on
            // a legitimate next tag (strict forms only) - and cache it for
            // the rest of the file.
            int? k = ar.ClineTail;
            if (k == null)
            {
                int def = ar.Ver == 17 ? 7 : 4;
                var order = new List<int> { def };
                foreach (var c in new[] { 0, 4, 7 }) if (c != def) order.Add(c);
                // two passes: a zero tail full of padding can mimic a null
                // tag, so only accept a null-anchored candidate when no
                // candidate lands on a STRONG form (escape / known class /
                // class definition)
                foreach (var allowNull in new[] { false, true })
                {
                    foreach (var cand in order)
                    {
                        if (StrictNextTag(ar, r.Data, r.Pos + cand, allowNull))
                        {
                            k = cand;
                            break;
                        }
                    }
                    if (k != null) break;
                }
                if (k == null) k = def;
                ar.ClineTail = k;
            }
            r.Raw(k.Value);
            return new ConstructionLineRec();
        }

        public static object ReadConstructionPoint(Archive ar, LR r)
        {
            Preamble(ar, r);
            var db = Drawbase(ar, r);
            var pos = r.F64s(3);
            r.F64s(3);
            r.U8();
            return new ConstructionPointRec { Db = db, Pos = pos };
        }

        public static object ReadSectionPlane(Archive ar, LR r)
        {
            Preamble(ar, r);
            var db = Drawbase(ar, r);
            double first = Tlv.ReadF64(r.Data, r.Pos);
            if (!(Math.Abs(first) <= 1.0001))
            {
                ar.ReadObject(r);
            }
            var plane = r.F64s(4);
            string name = "";
            string label = "";
            if (LegacyBytes.BytesEqual(r.Peek(3), 0, LegacyBytes.StrMarker))
            {
                name = r.Utf16();
                label = r.Utf16();
            }
            return new SectionPlaneRec { Db = db, Plane = plane, Name = name, Label = label };
        }

        public static object ReadSkFont(Archive ar, LR r)
        {
            ar.ReadObject(r, "CAttributeContainer");
            if (ar.HasPid)
            {
                r.U8();
            }
            r.Utf16();
            r.Raw(15);
            return new FontRec();
        }

        public static object ReadDimLinear(Archive ar, LR r)
        {
            Preamble(ar, r);
            var db = Drawbase(ar, r);
            string text = r.Utf16();
            ar.ReadObject(r, "CSkFont");
            // The tail is NOT a fixed 165-byte blob: it embeds two object
            // references (the dimension's connection points into the
            // geometry). Each is a normal MFC tag - 2 bytes in small
            // files, but 6 bytes once the archive holds more than 0x7FFE
            // objects and the 0x7FFF big-tag escape kicks in - so a
            // fixed-size skip walks off the rails exactly on large models
            // (found on a real 17 MB SketchUp 2018 file whose dimension
            // sat past object #517k).
            r.Raw(37);
            EntityRef(ar, r);                // connection point 1 (may be null)
            r.Raw(42);
            EntityRef(ar, r);                // connection point 2 (may be null)
            r.Raw(82);
            return new DimLinearRec { Db = db, Text = text };
        }

        public static object ReadText(Archive ar, LR r)
        {
            Preamble(ar, r);
            var db = Drawbase(ar, r);
            ar.ReadObject(r, "CSkFont");
            int p = r.Pos;
            int idx;
            while (true)
            {
                idx = LegacyBytes.FindBytes(r.Data, LegacyBytes.StrMarker, p, r.Pos + 512);
                if (idx < 0)
                {
                    throw new LegacyParseError($"text delimiter not found {r.Ctx()}");
                }
                var blk = new byte[11];
                Array.Copy(r.Data, idx - 11, blk, 0, 11);
                if (blk[0] == 0x01 && blk[1] == 0x00 && blk[2] == 0x00 && blk[3] == 0x00
                    && blk[6] == 0x03 && blk[7] == 0x00 && blk[8] == 0x00 && blk[9] == 0x00
                    && blk[10] == 1)
                {
                    break;
                }
                p = idx + 3;
            }
            r.Raw(idx - r.Pos);
            string text = r.Utf16();
            r.Raw(5);
            // Optional leader-attachment refs follow the fixed tail (a
            // text label anchored to geometry stores the anchored entities
            // here; they can point FORWARD - see EntityRef). Only the
            // escaped 6-byte form is recognisable without risk: a 2-byte
            // back-ref here would be indistinguishable from the next list
            // item's tag, and every known sample either has no attachments
            // or lives in a >0x7FFE-object file where the escape is
            // mandatory anyway.
            while (r.Pos + 2 <= r.Data.Length && r.Data[r.Pos] == 0xFF && r.Data[r.Pos + 1] == 0x7F)
            {
                uint val = Tlv.ReadU32(r.Data, r.Pos + 2);
                if ((val & 0x80000000) != 0) break;   // new-object tag - the next entity
                r.Raw(6);
            }
            return new TextRec { Db = db, Text = text };
        }

        public static List<(int Slot, string? Name, object? Value)> ReadEntityList(Archive ar, LR r, long count, string owner)
        {
            ar.BurnStack.Add(0);
            try
            {
                return ReadEntityListInner(ar, r, count, owner);
            }
            finally
            {
                ar.BurnStack.RemoveAt(ar.BurnStack.Count - 1);
            }
        }

        private static List<(int Slot, string? Name, object? Value)> ReadEntityListInner(Archive ar, LR r, long count, string owner)
        {
            var ents = new List<(int, string?, object?)>();
            while (ents.Count < count)
            {
                int p = r.Pos;
                bool hasBurnCredit = owner == "def" && ar.BurnStack.Count > 0 && ar.BurnStack[ar.BurnStack.Count - 1] > 0;
                if (hasBurnCredit
                    && p + 25 <= r.Data.Length
                    && Tlv.ReadU32(r.Data, p) == 0
                    && LegacyBytes.BytesEqual(r.Data, p + 22, LegacyBytes.StrMarker))
                {
                    // burned MapObject indices (see RegisterBurn) mean the
                    // declared count includes phantom entities the stream
                    // never carries; the definition tail signature (nrel=0
                    // + pad + 16-byte GUID + name marker at +22) marks the
                    // list's REAL end
                    break;
                }
                bool prevFlag = ar.InEntityList;
                ar.InEntityList = true;
                int? s = null;
                string? n = null;
                object? v = null;
                bool failed = false;
                try
                {
                    (s, n, v) = ar.ReadObject(r);
                }
                catch (LegacyParseError)
                {
                    if (owner != "root" && !hasBurnCredit)
                    {
                        throw;
                    }
                    // owner == "root": over-declared root counts run into
                    // the document tail - stop.
                    // hasBurnCredit: this list had burned MapObject indices
                    // (see RegisterBurn): the phantom connection points
                    // were also counted as items, so the declared count
                    // overshoots the real records. Stop at the failed item
                    // - the definition tail that follows (nrel, GUID
                    // anchor, thumbnail scan) validates the cut.
                    failed = true;
                }
                finally
                {
                    ar.InEntityList = prevFlag;
                }
                if (failed)
                {
                    r.Pos = p;
                    break;
                }
                ents.Add((s!.Value, n, v));
            }
            return ents;
        }

        public static object ReadDefinition(Archive ar, LR r)
        {
            Preamble(ar, r);
            r.Raw(ar.Ver >= 17 ? 22 : 20);
            uint nlayers = r.U32();
            if (nlayers > 10000)
            {
                throw new LegacyParseError($"implausible def layer count {r.Ctx()}");
            }
            // like the model-level layer list, the count is REAL layers
            // (new records or back-refs); SketchUp 2020 interleaves null
            // separators between them
            int got = 0;
            while (got < nlayers)
            {
                if (r.PeekU16() == 0)
                {
                    r.Pos += 2;
                    continue;
                }
                ar.ReadObject(r, "CLayer");
                got++;
            }
            uint decl = r.U16();
            if (decl == 0x7FFF)
            {
                decl = r.U32();
            }
            // v20 can drop its undocumented filler right here, swallowing
            // the u32 field (and, behind a layer-separator null, even the
            // decl itself): if the empty-string marker sits in the next
            // few bytes, the real count is the first non-zero u32 after
            // its padding.
            uint? count = null;
            if (ar.Ver >= 20)
            {
                count = LegacyBytes.RetryCountAfterV20Filler(r, r.Pos, 5_000_000, ar);
            }
            if (count == null)
            {
                r.U32();
                count = r.U32();
            }
            // A zero count is as much a symptom of the v20 filler as an
            // implausibly large one: the reader lands on the leading zero
            // bytes of the filler instead of the count. A genuinely empty
            // definition reads zero with no filler ahead, and
            // RetryCountAfterV20Filler leaves those alone.
            if (count > 5_000_000 || count == 0)
            {
                var retry = LegacyBytes.RetryCountAfterV20Filler(r, r.Pos - 4, 5_000_000, ar);
                if (retry.HasValue) count = retry.Value;
            }
            if (count > 5_000_000)
            {
                throw new LegacyParseError($"implausible def entity count {r.Ctx()}");
            }
            var ents = ReadEntityList(ar, r, count.Value, "def");
            uint nrel = r.U32();
            if (nrel > 100000)
            {
                var retry = LegacyBytes.RetryCountAfterV20Filler(r, r.Pos - 4, 100_000, ar);
                if (retry.HasValue) nrel = retry.Value;
            }
            if (nrel > 100000)
            {
                throw new LegacyParseError($"definition list misaligned {r.Ctx()}");
            }
            for (int i = 0; i < nrel; i++)
            {
                ar.ReadObject(r, "CRelationship");
            }
            r.U16();
            // The GUID is followed immediately by the name string. Some
            // files (SketchUp 2020) carry two extra bytes ahead of the GUID,
            // which would shift this read and leave the cursor mid-record.
            // Anchor on the string marker that must follow the 16 GUID
            // bytes instead of trusting the fixed prefix width.
            if (!LegacyBytes.BytesEqual(r.Data, r.Pos + 16, LegacyBytes.StrMarker))
            {
                for (int skip = 1; skip <= 4; skip++)
                {
                    int at = r.Pos + skip;
                    if (LegacyBytes.BytesEqual(r.Data, at + 16, LegacyBytes.StrMarker))
                    {
                        r.Pos = at;
                        break;
                    }
                }
            }
            var guid = r.Raw(16);
            string name = r.Utf16();
            r.Utf16();
            r.Utf16();
            r.U32();

            int? tpos = null;
            bool haveThumbSlot = ar.ClassSlot.TryGetValue("CThumbnail", out int thumbSlot);
            for (int off = 0; off < 96; off++)
            {
                int p = r.Pos + off;
                if (p + 16 <= r.Data.Length
                    && r.Data[p] == 0xFF && r.Data[p + 1] == 0xFF
                    && r.Data[p + 4] == 0x0a && r.Data[p + 5] == 0x00
                    && LegacyBytes.MatchesAscii(r.Data, p + 6, "CThumbnail"))
                {
                    tpos = p;
                    break;
                }
                if (haveThumbSlot && LegacyBytes.IsClassRef(r.Data, p, thumbSlot))
                {
                    tpos = p;
                    break;
                }
            }
            if (tpos == null)
            {
                throw new LegacyParseError($"definition tail: thumbnail not found {r.Ctx()}");
            }
            var gap = r.Raw(tpos.Value - r.Pos);
            int behavior = gap.Length >= 9 ? gap[gap.Length - 9] : 0;
            ar.ReadObject(r, "CThumbnail");
            return new DefinitionRec
            {
                Name = name,
                Guid = LegacyBytes.ToHex(guid),
                Ents = ents,
                FacesCamera = (behavior & 1) != 0,
                ShadowsFaceSun = (behavior & 2) != 0,
            };
        }

        public static object ReadInstance(Archive ar, LR r)
        {
            string? cls = ar.CurrentClass;
            var pre = Preamble(ar, r);
            var db = Drawbase(ar, r);
            var (ds, dn, _) = ar.ReadObject(r, "CComponentDefinition");
            if (dn != "CComponentDefinition")
            {
                throw new LegacyParseError($"instance definition ref is {dn} {r.Ctx()}");
            }
            var xf = r.F64s(13);
            string name = r.Utf16();

            // The trailing instance GUID arrives with CComponentInstance schema 5 /
            // CGroup schema 1; SketchUp 2013 writes CComponentInstance schema 4,
            // whose record ends at the name (see openskp#38 / #40).
            int minSchema = cls == "CGroup" ? 1 : 5;
            int? schema = (cls != null && ar.ClassSchema.TryGetValue(cls, out int s)) ? s : (int?)null;
            byte[] guid = (schema == null || schema >= minSchema) ? r.Raw(16) : Array.Empty<byte>();

            return new InstanceRec { Db = db, Def = ds, Xf = xf, Name = name, Guid = LegacyBytes.ToHex(guid), Attrs = pre.Attrs as AttrsRec };
        }

        // SketchUp's Dynamic Components extension stores its data in an
        // attribute dictionary literally named "dynamic_attributes" - a
        // stable, publicly documented part of the SketchUp Ruby API
        // (Entity#attribute_dictionary("dynamic_attributes")).
        // ReadAttrContainer/ReadAttrNamed above already fully decode an
        // entity's CAttributeContainer into typed (dict-name, {key: value})
        // pairs for other purposes (CFaceTextureCoords lookup on faces) -
        // this just looks up that one dictionary by name, mirroring what the
        // VFF path's Geometry.ExtractDynamicProperties does for D007/DC05 TLV
        // data.
        private const string DynamicAttributesDictName = "dynamic_attributes";

        /// <summary>Render an already-typed legacy attribute value (number,
        /// string, list, or null) as a string, matching the string-valued
        /// Dictionary&lt;string, string&gt; contract the VFF path's
        /// Geometry.ExtractDynamicProperties produces.</summary>
        public static string StringifyAttrValue(object? value)
        {
            if (value == null) return "";
            if (value is System.Collections.IEnumerable list && !(value is string))
            {
                var parts = new List<string>();
                foreach (var v in list) parts.Add(StringifyAttrValue(v));
                return string.Join(",", parts);
            }
            return Convert.ToString(value, System.Globalization.CultureInfo.InvariantCulture) ?? "";
        }

        /// <summary>Extract Dynamic Component attribute key/value pairs from a
        /// legacy entity's already-parsed CAttributeContainer, or {} when the
        /// entity carries no attribute container or no dynamic_attributes
        /// dictionary.</summary>
        public static Dictionary<string, string> ExtractLegacyDynamicProperties(AttrsRec? attrs)
        {
            if (attrs == null) return new Dictionary<string, string>();
            // Each child tuple's Name is the entity CLASS NAME (always
            // 'CAttributeNamed', from Archive.ReadObject) - never the
            // dictionary's own declared name, which lives in DictRec.Name.
            foreach (var (_, value) in attrs.Children)
            {
                if (value is DictRec dict && dict.Name == DynamicAttributesDictName)
                {
                    var result = new Dictionary<string, string>();
                    foreach (var kv in dict.Entries) result[kv.Key] = StringifyAttrValue(kv.Value);
                    return result;
                }
            }
            return new Dictionary<string, string>();
        }

        public static readonly Dictionary<string, LegacyReader> Readers = new Dictionary<string, LegacyReader>
        {
            ["CVertex"] = ReadVertex,
            ["CEdge"] = ReadEdge,
            ["CCurve"] = ReadCurve,
            ["CArcCurve"] = ReadArcCurve,
            ["CEdgeUse"] = ReadEdgeUse,
            ["CLoop"] = ReadLoop,
            ["CFace"] = ReadFace,
            ["CLayer"] = ReadLayer,
            ["CMaterial"] = ReadMaterial,
            ["CDib"] = ReadDib,
            ["CAttributeContainer"] = ReadAttrContainer,
            ["CAttributeNamed"] = ReadAttrNamed,
            ["CCamera"] = ReadCamera,
            ["CThumbnail"] = ReadThumbnail,
            ["CRelationship"] = ReadRelationship,
            ["CComponentDefinition"] = ReadDefinition,
            ["CImage"] = ReadImage,
            ["CComponentInstance"] = ReadInstance,
            ["CGroup"] = ReadInstance,
            ["CFaceTextureCoords"] = ReadFtc,
            ["CConstructionLine"] = ReadConstructionLine,
            ["CConstructionPoint"] = ReadConstructionPoint,
            ["CSectionPlane"] = ReadSectionPlane,
            ["CSkFont"] = ReadSkFont,
            ["CDimensionLinear"] = ReadDimLinear,
            ["CText"] = ReadText,
        };
    }

    internal static class Legacy
    {
        /// <summary>True when data is a classic (pre-2021) MFC-container .skp.</summary>
        public static bool IsLegacy(byte[] data)
        {
            if (!(data.Length >= 4 && data[0] == 0xFF && data[1] == 0xFE && data[2] == 0xFF && data[3] == 0x0E))
            {
                return false;
            }
            int head100Len = Math.Min(0x100, data.Length);
            if (LegacyBytes.FindBytes(data, new byte[] { 0x50, 0x4B, 0x03, 0x04 }, 0, head100Len) >= 0)
            {
                return false;
            }
            int head200Len = Math.Min(0x200, data.Length);
            return LegacyBytes.FindBytes(data, Encoding.ASCII.GetBytes("CVersionMap"), 0, head200Len) >= 0;
        }

        private static readonly int?[] CMaterialPattern = BuildCMaterialPattern();
        private static readonly int?[] CLayerPattern = BuildCLayerPattern();

        private static int?[] BuildCMaterialPattern()
        {
            var prefix = new int?[] { 0xFF, 0xFF, null, null, 0x09, 0x00 };
            var name = Encoding.ASCII.GetBytes("CMaterial").Select(b => (int?)b);
            return prefix.Concat(name).ToArray();
        }

        private static int?[] BuildCLayerPattern()
        {
            var prefix = new int?[] { 0xFF, 0xFF, null, null, 0x06, 0x00 };
            var name = Encoding.ASCII.GetBytes("CLayer").Select(b => (int?)b);
            return prefix.Concat(name).ToArray();
        }

        private static int? FindVersionMajor(byte[] data)
        {
            int headLen = Math.Min(0x60, data.Length);
            var stripped = new List<byte>();
            for (int i = 0; i < headLen; i++)
            {
                if (data[i] != 0x00) stripped.Add(data[i]);
            }
            string text = Encoding.GetEncoding("ISO-8859-1").GetString(stripped.ToArray());
            var m = System.Text.RegularExpressions.Regex.Match(text, @"\{(\d+)\.");
            if (!m.Success) return null;
            return int.Parse(m.Groups[1].Value);
        }

        internal sealed class WalkResult
        {
            public Archive Ar = null!;
            public List<(int Slot, string? Name, object? Value)> Root = new List<(int, string?, object?)>();
            public List<(int Slot, object? Value)> Layers = new List<(int, object?)>();
            public List<(int Slot, object? Value)> Materials = new List<(int, object?)>();
        }

        /// <summary>Bootstrap the absolute slot base: parse material 1 with
        /// a throwaway archive; material 2's class-ref tag names
        /// CMaterial's true slot.</summary>
        private static int BootstrapTwoMaterials(byte[] data, int ver, int matHdr)
        {
            var boot = new Archive(data, ver);
            foreach (var kv in LegacyReaders.Readers) boot.Readers[kv.Key] = kv.Value;
            boot.NextSlot = 1 << 20;
            boot.WalkBase = 1 << 20;
            boot.R.Pos = matHdr;
            boot.ReadObject(boot.R, "CMaterial");
            ushort tag = boot.R.PeekU16();
            if (tag == 0xFFFF || (tag & 0x8000) == 0)
            {
                throw new LegacyParseError("cannot bootstrap the slot base");
            }
            return tag & 0x7FFF;
        }

        /// <summary>Slot-base candidates for files where the two-material
        /// trick is unavailable (0 or 1 materials).
        ///
        /// Parse the model prefix (materials, layer list) with a throwaway
        /// base; the object right after the layer list is the
        /// definition-list anchor - an ABSOLUTE back-ref to the active
        /// layer, an object we just allocated relatively. Each walked layer
        /// yields one candidate base; with a single layer (the common case)
        /// the answer is exact.</summary>
        /// <summary>Internal (not private) so Create.cs's SkpBuilder
        /// constructor can bootstrap the same slot base against the bundled
        /// blank scaffold - the writer-side mirror of this reader-side
        /// probe, same reasoning as the docs above.</summary>
        internal static List<int> ProbeLayerAnchorBases(byte[] data, int ver, int start, uint matCount)
        {
            var boot = new Archive(data, ver);
            foreach (var kv in LegacyReaders.Readers) boot.Readers[kv.Key] = kv.Value;
            const int b0 = 1 << 20;
            boot.NextSlot = b0;
            boot.WalkBase = b0;
            boot.R.Pos = start;
            for (int i = 0; i < matCount; i++)
            {
                boot.ReadObject(boot.R, "CMaterial");
            }
            boot.R.U32();
            if (ver >= 17)
            {
                boot.R.U8();
            }
            uint layerCount = boot.R.U32();
            if (layerCount < 1 || layerCount > 100000)
            {
                throw new LegacyParseError("implausible layer count in base probe");
            }
            var layerSlots = new List<int>();
            for (int i = 0; i < layerCount; i++)
            {
                var (s, _, _) = boot.ReadObject(boot.R, "CLayer");
                layerSlots.Add(s!.Value);
            }
            var (anchorSlot, anchorName, _) = boot.ReadObject(boot.R);
            if (anchorName != "premodel")
            {
                // under the throwaway base every absolute back-ref classifies as
                // premodel; anything else means the prefix did not parse
                throw new LegacyParseError($"base probe: anchor resolved to {anchorName}");
            }
            var result = new List<int>();
            foreach (var rel in layerSlots)
            {
                int candidate = anchorSlot!.Value - (rel - b0);
                if (candidate > 0 && candidate < b0) result.Add(candidate);
            }
            return result;
        }

        private static WalkResult Walk(byte[] data)
        {
            int? ver = FindVersionMajor(data);
            if (ver == null)
            {
                throw new LegacyParseError("no version string in header");
            }

            // anchor: the material manager (u32 count right before the first
            // CMaterial new-class record); zero-material files have no
            // CMaterial record anywhere, so fall back to the first CLayer
            // class record and start at the layer-list marker just before it
            int matHdr = LegacyBytes.FindPattern(data, CMaterialPattern);
            int start;
            uint matCount;
            if (matHdr >= 0)
            {
                start = matHdr;
                matCount = Tlv.ReadU32(data, matHdr - 4);
                if (matCount > 100000)
                {
                    throw new LegacyParseError("implausible material count");
                }
            }
            else
            {
                int layerHdr = LegacyBytes.FindPattern(data, CLayerPattern);
                if (layerHdr < 0)
                {
                    throw new LegacyParseError("no CMaterial or CLayer class record found");
                }
                matCount = 0;
                start = layerHdr - (ver >= 17 ? 9 : 8);
            }

            List<int> bases = matCount >= 2
                ? new List<int> { BootstrapTwoMaterials(data, ver.Value, start) }
                : ProbeLayerAnchorBases(data, ver.Value, start, matCount);

            LegacyParseError? lastExc = null;
            foreach (var b in bases)
            {
                try
                {
                    return WalkModel(data, ver.Value, start, matCount, b);
                }
                catch (LegacyParseError e)
                {
                    lastExc = e;
                }
            }
            if (lastExc != null) throw lastExc;
            throw new LegacyParseError("no viable slot base candidate");
        }

        private static WalkResult WalkModel(byte[] data, int ver, int start, uint matCount, int baseSlot)
        {
            var ar = new Archive(data, ver);
            foreach (var kv in LegacyReaders.Readers) ar.Readers[kv.Key] = kv.Value;
            ar.NextSlot = baseSlot;
            ar.WalkBase = baseSlot;
            var r = ar.R;

            r.Pos = start;
            var materials = new List<(int, object?)>();
            for (int i = 0; i < matCount; i++)
            {
                var (s, _, v) = ar.ReadObject(r, "CMaterial");
                materials.Add((s!.Value, v));
            }

            r.U32();
            if (ver >= 17)
            {
                r.U8();
            }
            uint layerCount = r.U32();
            if (layerCount > 100000)
            {
                throw new LegacyParseError("implausible layer count");
            }
            // layerCount counts REAL layers. SketchUp 2020 interleaves a
            // null object-ref after each layer record (a separator, not a
            // layer), so counting reads walks off mid-list on files with
            // several layers; count parsed layers instead, skip the
            // separators, and stop early if the next tag is a back-ref
            // (the definition-list anchor) - a v20 variant where the count
            // over-includes separators.
            var layers = new List<(int, object?)>();
            while (layers.Count < layerCount)
            {
                ushort tag = r.PeekU16();
                if (tag == 0)
                {
                    r.Pos += 2;
                    continue;
                }
                if (tag != 0xFFFF && (tag & 0x8000) == 0)
                {
                    break;
                }
                var (s, _, v) = ar.ReadObject(r, "CLayer");
                if (v == null) continue;
                layers.Add((s!.Value, v));
            }
            // trailing separators (and any layer records past the declared count)
            bool haveTrailingLayCls = ar.ClassSlot.TryGetValue("CLayer", out int trailingLayCls);
            while (true)
            {
                ushort tag = r.PeekU16();
                if (tag == 0)
                {
                    r.Pos += 2;
                    continue;
                }
                if (haveTrailingLayCls && tag == (0x8000 | trailingLayCls))
                {
                    var (s, _, v) = ar.ReadObject(r, "CLayer");
                    if (v != null) layers.Add((s!.Value, v));
                    continue;
                }
                break;
            }

            var (_, dn, _) = ar.ReadObject(r);
            if (dn != "CLayer")
            {
                throw new LegacyParseError($"definition-list anchor is {dn}, not a layer");
            }
            uint defCount = r.U32();
            if (defCount > 1_000_000)
            {
                var retry = LegacyBytes.RetryCountAfterV20Filler(r, r.Pos - 4, 1_000_000, ar);
                if (retry.HasValue) defCount = retry.Value;
            }
            if (defCount > 1_000_000)
            {
                throw new LegacyParseError("implausible definition count");
            }
            for (int i = 0; i < defCount; i++)
            {
                ar.ReadObject(r, "CComponentDefinition");
            }

            bool haveDefCls = ar.ClassSlot.TryGetValue("CComponentDefinition", out int defCls);
            while (true)
            {
                ushort tag = r.PeekU16();
                bool isDef = haveDefCls && tag == (0x8000 | defCls);
                if (!isDef && tag == 0xFFFF && LegacyBytes.MatchesAscii(r.Peek(26), 6, "CComponentDefinition"))
                {
                    isDef = true;
                }
                if (!isDef) break;
                ar.ReadObject(r);
            }

            uint rootCount = r.U32();
            if (rootCount > 5_000_000)
            {
                var retry = LegacyBytes.RetryCountAfterV20Filler(r, r.Pos - 4, 5_000_000, ar);
                if (retry.HasValue) rootCount = retry.Value;
            }
            if (rootCount > 5_000_000)
            {
                throw new LegacyParseError("implausible root entity count");
            }
            var root = LegacyReaders.ReadEntityList(ar, r, rootCount, "root");

            return new WalkResult { Ar = ar, Root = root, Layers = layers, Materials = materials };
        }

        // ── adapter to the shared raw-parse shape ─────────────────────────

        /// <summary>Mirror of Geometry.cs's GeometryBuilder, kept
        /// dependency-free from the VFF-specific TLV machinery.</summary>
        internal sealed class LegacyBuilder
        {
            public Dictionary<long, (double X, double Y, double Z)> Vertices = new Dictionary<long, (double, double, double)>();
            public Dictionary<long, (long? V1, long? V2)> Edges = new Dictionary<long, (long?, long?)>();
            public Dictionary<long, int> EdgeFlags = new Dictionary<long, int>();
            public Dictionary<long, GeometryBuilderFace> Faces = new Dictionary<long, GeometryBuilderFace>();
            public List<GeometryBuilderInstance> Instances = new List<GeometryBuilderInstance>();
            public List<SectionPlane> SectionPlanes = new List<SectionPlane>();
            public List<TextEntity> Texts = new List<TextEntity>();
            public List<Dimension> Dimensions = new List<Dimension>();
        }

        private static void AddEdge(LegacyBuilder builder, int slot, EdgeRec e, Dictionary<int, SlotEntry> slots)
        {
            if (builder.Edges.ContainsKey(slot)) return;
            foreach (var vs in new[] { e.V1, e.V2 })
            {
                if (vs == null) continue;
                if (slots.TryGetValue(vs.Value, out var ent) && ent.Value != null && !builder.Vertices.ContainsKey(vs.Value))
                {
                    var xyz = ((VertexRec)ent.Value).Xyz;
                    builder.Vertices[vs.Value] = (xyz[0], xyz[1], xyz[2]);
                }
            }
            builder.Edges[slot] = (e.V1, e.V2);
            var db = e.Db;
            int flags = (db.Soft != 0 ? 0x08 : 0) | (db.Smooth != 0 ? 0x10 : 0) | (db.Hidden != 0 ? 0x01 : 0);
            if (flags != 0)
            {
                builder.EdgeFlags[slot] = flags;
            }
        }

        private static void FillBuilder(LegacyBuilder builder, List<(int Slot, string? Name, object? Value)> ents, Dictionary<int, SlotEntry> slots)
        {
            foreach (var (s, _, v) in ents)
            {
                if (v == null) continue;
                if (v is EdgeRec edgeRec)
                {
                    AddEdge(builder, s, edgeRec, slots);
                }
                else if (v is FaceRec faceRec)
                {
                    var loops = new List<List<(long EdgeId, long Orientation)>>();
                    foreach (var lp in faceRec.Loops)
                    {
                        var loop = new List<(long, long)>();
                        foreach (var u in lp.Uses)
                        {
                            int? es = u.Edge;
                            if (es == null || !slots.TryGetValue(es.Value, out var ent) || ent.Value == null) continue;
                            AddEdge(builder, es.Value, (EdgeRec)ent.Value, slots);
                            // Normalize to the documented CoEdge contract (+1 = same
                            // direction as the edge, -1 = reversed) - u.Sense is the raw
                            // SketchUp bit (0 = forward, 1 = reversed).
                            loop.Add((es.Value, u.Sense != 0 ? -1 : 1));
                        }
                        loops.Add(loop);
                    }
                    var face = new GeometryBuilderFace
                    {
                        Loops = loops,
                        Normal = (faceRec.Plane[0], faceRec.Plane[1], faceRec.Plane[2]),
                        MaterialId = faceRec.Db.Mat != 0 ? faceRec.Db.Mat : (long?)null,
                        BackMaterialId = faceRec.BackMat != 0 ? faceRec.BackMat : (long?)null,
                        UvTransform = null,
                        UvTransformBack = null,
                        Hidden = faceRec.Db.Hidden != 0,
                    };
                    var attrs = faceRec.Attrs;
                    if (attrs != null)
                    {
                        foreach (var (_, cv) in attrs.Children)
                        {
                            if (cv is FtcRec ftc)
                            {
                                face.UvTransform = (double[])ftc.Front.Clone();
                                face.UvTransformBack = (double[])ftc.Back.Clone();
                                face.UvProjected = ftc.FrontProjected;
                                face.UvProjectedBack = ftc.BackProjected;
                            }
                        }
                    }
                    builder.Faces[s] = face;
                }
                else if (v is InstanceRec instRec)
                {
                    builder.Instances.Add(new GeometryBuilderInstance
                    {
                        Offset = 0,
                        Name = instRec.Name,
                        RefIdx = instRec.Def,
                        RefGuid = "",
                        Matrix = instRec.Xf.ToList(),
                        MaterialId = instRec.Db.Mat != 0 ? instRec.Db.Mat : (long?)null,
                        Hidden = instRec.Db.Hidden != 0,
                        LayerId = instRec.Db.Layer != 0 ? instRec.Db.Layer : (long?)null,
                        Children = new List<TlvNode>(),
                        Properties = LegacyReaders.ExtractLegacyDynamicProperties(instRec.Attrs),
                    });
                }
                else if (v is SectionPlaneRec spRec)
                {
                    builder.SectionPlanes.Add(new SectionPlane
                    {
                        Plane = spRec.Plane,
                        Name = spRec.Name,
                        Label = spRec.Label,
                        Hidden = spRec.Db.Hidden != 0
                    });
                }
                else if (v is TextRec trRec)
                {
                    builder.Texts.Add(new TextEntity
                    {
                        Text = trRec.Text,
                        Hidden = trRec.Db.Hidden != 0
                    });
                }
                else if (v is DimLinearRec dlRec)
                {
                    builder.Dimensions.Add(new Dimension
                    {
                        Text = dlRec.Text,
                        Hidden = dlRec.Db.Hidden != 0
                    });
                }
            }
        }

        /// <summary>Parse a classic MFC .skp into the shared raw-parse shape
        /// (Core.RawParsed), which Parser.cs converts to the public
        /// SkpModel exactly like the VFF path.</summary>
        public static Core.RawParsed FullParseLegacy(byte[] data, SkpParseOptions? options = null)
        {
            var sw = System.Diagnostics.Stopwatch.StartNew();
            Observability.Log(options, SkpLogLevel.Information, $"Parsing legacy buffer ({data.Length} bytes)");

            string version = "unknown";
            int second = LegacyBytes.FindBytes(data, LegacyBytes.StrMarker, 4);
            if (second > 0)
            {
                int start = second + 4;
                int len = Math.Min(100, data.Length - start);
                string text = Encoding.Unicode.GetString(data, start, len);
                int braceStart = text.IndexOf('{');
                int braceEnd = text.IndexOf('}');
                if (braceStart >= 0 && braceEnd >= 0)
                {
                    version = text.Substring(braceStart, braceEnd - braceStart + 1);
                }
            }
            Observability.Log(options, SkpLogLevel.Debug, $"Detected legacy version {version}");

            WalkResult walkResult;
            try
            {
                walkResult = Walk(data);
            }
            catch (LegacyParseError e)
            {
                throw new SkpParseException($"legacy .skp parse failed: {e.Message}", stage: "legacy_walk", innerException: e);
            }

            var ar = walkResult.Ar;
            var slots = ar.Slots;
            Observability.Log(
                options, SkpLogLevel.Debug,
                $"Legacy walk complete: {walkResult.Materials.Count} materials, {walkResult.Layers.Count} layers");

            var materialsMap = new Dictionary<string, Geometry.RawMaterial>();
            var materialIdToName = new Dictionary<long, string>();
            foreach (var (s, vObj) in walkResult.Materials)
            {
                var v = (MaterialRec)vObj!;
                byte[] rgba = v.Rgba;
                double trans = v.UseOpacity != 0 ? Math.Min(Math.Max(1.0 - v.Opacity, 0.0), 1.0) : 1.0;
                bool colorized = v.Colorized;
                Geometry.RawTexture? texture = null;
                if (v.HasTexture)
                {
                    byte[]? texData = null;
                    if (v.TexDib != null && slots.TryGetValue(v.TexDib.Value, out var dibEnt) && dibEnt.Value is DibRec dibRec)
                    {
                        texData = dibRec.Data;
                    }
                    bool isPng = texData != null && texData.Length >= 4
                        && texData[0] == 0x89 && texData[1] == 0x50 && texData[2] == 0x4E && texData[3] == 0x47;
                    string ext = isPng ? ".png" : ".jpg";
                    string fname = v.TexFile.Length > 0 ? v.TexFile : v.Name + ext;
                    texture = new Geometry.RawTexture { Filename = fname, XScale = v.TexW, YScale = v.TexH, Data = texData };
                }
                var matObj = new Geometry.RawMaterial
                {
                    Name = v.Name,
                    R = rgba[0],
                    G = rgba[1],
                    B = rgba[2],
                    A = rgba.Length > 3 ? rgba[3] : 255,
                    Transparency = trans,
                    Colorized = colorized,
                    // colourize type is not decoded in the legacy record;
                    // tint (1) is the correct rendering for the grey base
                    // textures observed.
                    ColorizeType = colorized ? 1 : 0,
                    Texture = texture,
                };
                materialsMap[v.Name] = matObj;
                materialIdToName[s] = v.Name;
            }

            var layerColors = new Dictionary<string, (int, int, int)>();
            var layerHidden = new Dictionary<string, bool>();
            var layerIdToName = new Dictionary<long, string>();
            foreach (var (s, vObj) in walkResult.Layers)
            {
                var v = (LayerRec)vObj!;
                byte[] rgba = v.Rgba;
                layerColors[v.Name] = (rgba[0], rgba[1], rgba[2]);
                layerHidden[v.Name] = v.Hidden != 0;
                layerIdToName[s] = v.Name;
            }
            if (!layerColors.ContainsKey("Layer0"))
            {
                layerColors["Layer0"] = (136, 136, 136);
            }
            if (!layerHidden.ContainsKey("Layer0"))
            {
                layerHidden["Layer0"] = false;
            }

            var defsDict = new Dictionary<long, Geometry.RawDefinition>();
            int processed = 0;
            long lastSlot = -1;
            try
            {
                foreach (var kv in slots)
                {
                    lastSlot = kv.Key;
                    if (kv.Value.Kind == "obj" && kv.Value.Name == "CComponentDefinition" && kv.Value.Value != null)
                    {
                        var d = (DefinitionRec)kv.Value.Value;
                        var b = new LegacyBuilder();
                        FillBuilder(b, d.Ents, slots);
                        defsDict[kv.Key] = new Geometry.RawDefinition
                        {
                            Guid = d.Guid,
                            Name = d.Name,
                            IsImage = false,
                            AlwaysFacesCamera = d.FacesCamera,
                            ShadowsFaceSun = d.ShadowsFaceSun,
                            Builder = ToGeometryBuilder(b),
                        };
                        processed++;
                        if (processed % ParseTuning.ProgressInterval == 0)
                        {
                            Observability.Progress(options, "legacy_defs", processed, processed);
                            Observability.Log(options, SkpLogLevel.Debug, $"Processed {processed} component definitions");
                        }
                    }
                }
            }
            catch (Exception e) when (!(e is SkpParseException))
            {
                throw new SkpParseException(
                    $"Failed while building component definitions: {e.Message}",
                    stage: "legacy_defs", definitionId: lastSlot, innerException: e);
            }

            var rootBuilder = new LegacyBuilder();
            FillBuilder(rootBuilder, walkResult.Root, slots);

            Observability.Log(
                options, SkpLogLevel.Information,
                $"Parse complete: {defsDict.Count} defs ({sw.Elapsed.TotalSeconds:F2}s)");

            return new Core.RawParsed
            {
                Version = version,
                // Legacy (pre-2021 MFC) files carry no meta/meta.dat
                // container - that's a VFF/ZIP-only construct - so there
                // is no known source for the model's unit-system string
                // here.
                Units = null,
                LayerColors = layerColors,
                LayerHidden = layerHidden,
                LayerIdToName = layerIdToName,
                MaterialIdToName = materialIdToName,
                Materials = materialsMap,
                MaterialsByFolder = new Dictionary<string, Geometry.RawMaterial>(),
                Styles = new List<Geometry.RawStyle>(),
                DefsDict = defsDict,
                Root = new Geometry.RawDefinition
                {
                    Guid = "ROOT",
                    Name = "ROOT_MODEL",
                    IsImage = false,
                    AlwaysFacesCamera = false,
                    Builder = ToGeometryBuilder(rootBuilder),
                },
            };
        }

        /// <summary>LegacyBuilder and GeometryBuilder hold identical shapes;
        /// copy across so the rest of the pipeline (Parser.cs) only needs to
        /// know about GeometryBuilder.</summary>
        private static GeometryBuilder ToGeometryBuilder(LegacyBuilder b)
        {
            var g = new GeometryBuilder();
            foreach (var kv in b.Vertices) g.Vertices[kv.Key] = kv.Value;
            foreach (var kv in b.Edges) g.Edges[kv.Key] = kv.Value;
            foreach (var kv in b.EdgeFlags) g.EdgeFlags[kv.Key] = kv.Value;
            foreach (var kv in b.Faces) g.Faces[kv.Key] = kv.Value;
            g.Instances = b.Instances;
            return g;
        }
    }
}
