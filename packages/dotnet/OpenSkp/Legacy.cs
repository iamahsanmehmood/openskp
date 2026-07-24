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
        public int NextSlot;
        public int WalkBase;
        public Dictionary<string, LegacyReader> Readers = new Dictionary<string, LegacyReader>();
        public int? CurrentLoop;
        public bool InEntityList;

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
            object? value = reader(this, r);
            Slots[slot] = new SlotEntry { Kind = "obj", Name = name, Value = value };
            return (slot, name, value);
        }

        private (int?, string?, object?) Backref(int slot, LR r)
        {
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
    internal sealed class RelationshipRec { }
    internal sealed class ConstructionLineRec { }
    internal sealed class ConstructionPointRec { public DrawBase Db = new DrawBase(); public double[] Pos = new double[3]; }
    internal sealed class SectionPlaneRec { }
    internal sealed class FontRec { }
    internal sealed class DimLinearRec { public DrawBase Db = new DrawBase(); public string Text = ""; }
    internal sealed class TextRec { public string Text = ""; }
    internal sealed class DefinitionRec
    {
        public string Name = "";
        public string Guid = "";
        public List<(int Slot, string? Name, object? Value)> Ents = new List<(int, string?, object?)>();
        public bool FacesCamera;
    }
    internal sealed class InstanceRec
    {
        public DrawBase Db = new DrawBase();
        public int? Def;
        public double[] Xf = new double[13];
        public string Name = "";
        public string Guid = "";
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
            var b = r.Raw(10);
            return new DrawBase
            {
                Mat = Tlv.ReadU16(b, 0),
                Hidden = b[2],
                Soft = b[5],
                Smooth = b[6],
                Layer = Tlv.ReadU16(b, 8),
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

        public static object ReadEdgeUse(Archive ar, LR r)
        {
            Preamble(ar, r);
            var (es, _, _) = ar.ReadObject(r, "CEdge");
            byte sense = r.U8();
            var (ps, _, _) = ar.ReadObject(r);
            if (ps != ar.CurrentLoop)
            {
                throw new LegacyParseError($"edge-use parent slot {ps} != current loop {ar.CurrentLoop} {r.Ctx()}");
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
            if (t == 0x12) return r.F64s(3);
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
            r.Utf16();
            r.U16();
            var rgba = r.Raw(4);
            r.Utf16();
            r.Raw(21);
            return new LayerRec { Name = name, Hidden = mid.Count > 0 ? mid[0] : 0, Rgba = rgba };
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
                r.Raw(ar.Ver >= 17 ? 2 : 1);
                var (s, _, dib) = ar.ReadObject(r, "CDib");
                if (!(dib is DibRec))
                {
                    throw new LegacyParseError($"texture object is not a dib {r.Ctx()}");
                }
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
                var avg = r.Raw(9);
                r.Utf16();
                var blob = r.Raw(8);
                double opacity = r.F64();
                byte useOp = r.U8();
                bool colorized = blob[4] != 0 || avg[3] == 0xFF;
                outRec.Rgba = avg.Take(4).ToArray();
                outRec.Opacity = opacity;
                outRec.UseOpacity = useOp;
                outRec.TexDib = s;
                outRec.TexW = w;
                outRec.TexH = h;
                outRec.TexFile = fname;
                outRec.Colorized = colorized;
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

        public static object ReadRelationship(Archive ar, LR r)
        {
            Preamble(ar, r);
            ar.ReadObject(r);
            ar.ReadObject(r);
            return new RelationshipRec();
        }

        public static object ReadConstructionLine(Archive ar, LR r)
        {
            Preamble(ar, r);
            Drawbase(ar, r);
            r.F64s(3);
            r.F64s(3);
            r.F64s(2);
            r.Raw(ar.Ver >= 17 ? 7 : 4);
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
            Drawbase(ar, r);
            double first = Tlv.ReadF64(r.Data, r.Pos);
            if (!(Math.Abs(first) <= 1.0001))
            {
                ar.ReadObject(r);
            }
            r.F64s(4);
            if (LegacyBytes.BytesEqual(r.Peek(3), 0, LegacyBytes.StrMarker))
            {
                r.Utf16();
                r.Utf16();
            }
            return new SectionPlaneRec();
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
            r.Raw(165);
            return new DimLinearRec { Db = db, Text = text };
        }

        public static object ReadText(Archive ar, LR r)
        {
            Preamble(ar, r);
            Drawbase(ar, r);
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
            return new TextRec { Text = text };
        }

        public static List<(int Slot, string? Name, object? Value)> ReadEntityList(Archive ar, LR r, long count, string owner)
        {
            var ents = new List<(int, string?, object?)>();
            while (ents.Count < count)
            {
                int p = r.Pos;
                bool prevFlag = ar.InEntityList;
                ar.InEntityList = true;
                int? s;
                string? n;
                object? v;
                try
                {
                    (s, n, v) = ar.ReadObject(r);
                }
                catch (LegacyParseError) when (owner == "root")
                {
                    ar.InEntityList = prevFlag;
                    r.Pos = p;
                    break;
                }
                ar.InEntityList = prevFlag;
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
            for (int i = 0; i < nlayers; i++)
            {
                ar.ReadObject(r, "CLayer");
            }
            uint decl = r.U16();
            if (decl == 0x7FFF)
            {
                decl = r.U32();
            }
            r.U32();
            uint count = r.U32();
            if (count > 5_000_000)
            {
                throw new LegacyParseError($"implausible def entity count {r.Ctx()}");
            }
            var ents = ReadEntityList(ar, r, count, "def");
            uint nrel = r.U32();
            if (nrel > 100000)
            {
                throw new LegacyParseError($"definition list misaligned {r.Ctx()}");
            }
            for (int i = 0; i < nrel; i++)
            {
                ar.ReadObject(r, "CRelationship");
            }
            r.U16();
            var guid = r.Raw(16);
            string name = r.Utf16();
            r.Utf16();
            r.Utf16();
            r.U32();

            int? tpos = null;
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
                if (ar.ClassSlot.TryGetValue("CThumbnail", out int thumbSlot))
                {
                    if (p + 2 <= r.Data.Length && Tlv.ReadU16(r.Data, p) == (0x8000 | thumbSlot))
                    {
                        tpos = p;
                        break;
                    }
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
            };
        }

        public static object ReadInstance(Archive ar, LR r)
        {
            Preamble(ar, r);
            var db = Drawbase(ar, r);
            var (ds, dn, _) = ar.ReadObject(r, "CComponentDefinition");
            if (dn != "CComponentDefinition")
            {
                throw new LegacyParseError($"instance definition ref is {dn} {r.Ctx()}");
            }
            var xf = r.F64s(13);
            string name = r.Utf16();
            var guid = r.Raw(16);
            return new InstanceRec { Db = db, Def = ds, Xf = xf, Name = name, Guid = LegacyBytes.ToHex(guid) };
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

        private static int?[] BuildCMaterialPattern()
        {
            var prefix = new int?[] { 0xFF, 0xFF, null, null, 0x09, 0x00 };
            var name = Encoding.ASCII.GetBytes("CMaterial").Select(b => (int?)b);
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

        private static WalkResult Walk(byte[] data)
        {
            int? ver = FindVersionMajor(data);
            if (ver == null)
            {
                throw new LegacyParseError("no version string in header");
            }
            var ar = new Archive(data, ver.Value);
            foreach (var kv in LegacyReaders.Readers) ar.Readers[kv.Key] = kv.Value;
            var r = ar.R;

            int matHdr = LegacyBytes.FindPattern(data, CMaterialPattern);
            if (matHdr < 0)
            {
                throw new LegacyParseError("no CMaterial class record found");
            }
            uint matCount = Tlv.ReadU32(data, matHdr - 4);
            if (matCount > 100000)
            {
                throw new LegacyParseError("implausible material count");
            }

            if (matCount < 2)
            {
                throw new LegacyParseError("single-material bootstrap not implemented for this file");
            }
            var boot = new Archive(data, ver.Value);
            foreach (var kv in LegacyReaders.Readers) boot.Readers[kv.Key] = kv.Value;
            boot.NextSlot = 1 << 20;
            boot.WalkBase = 1 << 20;
            boot.R.Pos = matHdr;
            boot.ReadObject(boot.R, "CMaterial");
            ushort bootTag = boot.R.PeekU16();
            if (bootTag == 0xFFFF || (bootTag & 0x8000) == 0)
            {
                throw new LegacyParseError("cannot bootstrap the slot base");
            }
            ar.NextSlot = bootTag & 0x7FFF;
            ar.WalkBase = ar.NextSlot;

            r.Pos = matHdr;
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
            var layers = new List<(int, object?)>();
            for (int i = 0; i < layerCount; i++)
            {
                var (s, _, v) = ar.ReadObject(r, "CLayer");
                layers.Add((s!.Value, v));
            }

            var (_, dn, _) = ar.ReadObject(r);
            if (dn != "CLayer")
            {
                throw new LegacyParseError($"definition-list anchor is {dn}, not a layer");
            }
            uint defCount = r.U32();
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
            public Dictionary<long, (double, double, double)> Vertices = new Dictionary<long, (double, double, double)>();
            public Dictionary<long, (long?, long?)> Edges = new Dictionary<long, (long?, long?)>();
            public Dictionary<long, int> EdgeFlags = new Dictionary<long, int>();
            public Dictionary<long, GeometryBuilderFace> Faces = new Dictionary<long, GeometryBuilderFace>();
            public List<GeometryBuilderInstance> Instances = new List<GeometryBuilderInstance>();
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
                            loop.Add((es.Value, u.Sense != 0 ? 1 : 0));
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
                        Children = new List<TlvNode>(),
                    });
                }
            }
        }

        /// <summary>Parse a classic MFC .skp into the shared raw-parse shape
        /// (Core.RawParsed), which Parser.cs converts to the public
        /// SkpModel exactly like the VFF path.</summary>
        public static Core.RawParsed FullParseLegacy(byte[] data)
        {
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

            WalkResult walkResult;
            try
            {
                walkResult = Walk(data);
            }
            catch (LegacyParseError e)
            {
                throw new InvalidOperationException($"legacy .skp parse failed: {e.Message}", e);
            }

            var ar = walkResult.Ar;
            var slots = ar.Slots;

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
            var layerIdToName = new Dictionary<long, string>();
            foreach (var (s, vObj) in walkResult.Layers)
            {
                var v = (LayerRec)vObj!;
                byte[] rgba = v.Rgba;
                layerColors[v.Name] = (rgba[0], rgba[1], rgba[2]);
                layerIdToName[s] = v.Name;
            }
            if (!layerColors.ContainsKey("Layer0"))
            {
                layerColors["Layer0"] = (136, 136, 136);
            }

            var defsDict = new Dictionary<long, Geometry.RawDefinition>();
            foreach (var kv in slots)
            {
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
                        Builder = ToGeometryBuilder(b),
                    };
                }
            }

            var rootBuilder = new LegacyBuilder();
            FillBuilder(rootBuilder, walkResult.Root, slots);

            return new Core.RawParsed
            {
                Version = version,
                LayerColors = layerColors,
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
