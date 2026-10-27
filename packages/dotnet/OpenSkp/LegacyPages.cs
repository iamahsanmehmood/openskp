using System;
using System.Collections.Generic;
using System.Text;

namespace OpenSkp
{
    // ── full scene records ───────────────────────────────────────────────────
    //
    // Ground truth, all read byte-exactly: the gondola_v20.skp fixture (SketchUp 2020, one scene,
    // flags 0x1FF); SketchUp's own 3D Warehouse model "Skill Builder: Creating Section Animations
    // with Scenes" in its SketchUp 2017 format (eight scenes, 0x7F) and SketchUp 2015 saves (nine
    // scenes, 0x7F) - not redistributable, so not committed; files written by openskp.create, which
    // SketchUp Free opens with working scenes; and files built and saved through SketchUp's C API as
    // 2017 to 2020 (schema 15), whose names, cameras and hidden layers SketchUp's own library reads
    // back identically.
    //
    // The scene list sits in the document tail as a u32 count right before the first record (which
    // declares the CViewPage class), then one record per scene, then a ref to the selected scene. A
    // record is: the class tag | a null attribute ref | (schema 13+, SketchUp 2016+, only) a pid |
    // name | description | u32 capture flags | the camera (CCamera, or null) | the thumbnail (CDib,
    // or null) | (schema 13) a byte | then one section per capture flag that is set:
    //
    //   0x02 style       the rendering options (length by file version, see RenderingOptionsLen)
    //                    + a CSkpStyle ref or object
    //   0x04 shadows     2 bytes (schema 13+: 3) + the shadow info
    //   0x08 axes        (schema 13+: a flag byte +) 110 bytes
    //   0x10 hidden      a byte, u32 n, n entity refs
    //   0x20 layers      u32 n, n refs to the layers (tags) the scene hides
    //   0x40 sections    u32 n, n section-plane refs
    //
    // | 22 bytes (transition and delay time, flags) | u32 n + an n-byte PNG.
    //
    // Schema 15 (a file saved through the C API as 2017 to 2020) differs: flag 0x01 is the camera,
    // which is only there when the flag is set, with no CDib tag, no byte after it and no
    // thumbnail; the rendering options are 3 bytes longer; the style options have two more value
    // types (3: 2 bytes, 5: 4 bytes); and the trailer is 21 bytes with no PNG after it.
    //
    // A record this reader can't fully read, or flags outside 0x1FF, make ReadPageList give up (it
    // returns null). Python then falls back to a narrow scan of the file tail that finds scene names
    // and hidden layers only; that fallback is not ported, so these readers return no scenes there.
    internal static class LegacyPages
    {
        private const uint KnownFlags = 0x1FF;
        private const int AxesLen = 110;
        private const int TrailerLen = 22;
        private const int ShadowInfoFixedLen = 60; // after the city and country strings

        // The rendering options' length by SketchUp major version, measured on the model-level copy
        // of the same block in every fixture from 2014 to 2020 (and in the 2020 scene); other
        // versions aren't known, so give up there.
        private static readonly Dictionary<int, int> RenderingOptionsLen = new Dictionary<int, int>
        {
            [14] = 206,
            [15] = 206,
            [16] = 206,
            [17] = 206,
            [18] = 211,
            [19] = 212,
            [20] = 213,
        };

        // CSkpStyle option values: type tag -> byte size
        private static readonly Dictionary<uint, int> StyleValueSizes = new Dictionary<uint, int>
        {
            [1] = 1,
            [3] = 2,
            [4] = 4,
            [5] = 4,
            [6] = 4,
            [7] = 8,
        };

        private static readonly byte[] ClassName = Encoding.ASCII.GetBytes("CViewPage");

        /// <summary>The first CViewPage declaration at or after <paramref name="from"/>:
        /// <c>ff ff &lt;schema 12|13|15&gt; 00 09 00 CViewPage</c>.</summary>
        private static int FindDecl(byte[] data, int from)
        {
            for (int i = from; i + 6 + ClassName.Length <= data.Length; i++)
            {
                if (data[i] != 0xFF || data[i + 1] != 0xFF) continue;
                byte schema = data[i + 2];
                if (schema != 0x0C && schema != 0x0D && schema != 0x0F) continue;
                if (data[i + 3] != 0x00 || data[i + 4] != 0x09 || data[i + 5] != 0x00) continue;
                bool ok = true;
                for (int j = 0; j < ClassName.Length; j++)
                {
                    if (data[i + 6 + j] != ClassName[j]) { ok = false; break; }
                }
                if (ok) return i;
            }
            return -1;
        }

        private enum TagKind { Null, Decl, Class, Obj }

        /// <summary>One archive tag, with the 0x7FFF escape to 32 bits resolved.</summary>
        private static (TagKind Kind, uint Slot) ReadTag(LR r)
        {
            uint v = r.U16();
            if (v == 0) return (TagKind.Null, 0);
            if (v == 0xFFFF) return (TagKind.Decl, 0);
            if (v == 0x7FFF)
            {
                uint w = r.U32();
                return (w & 0x80000000u) != 0 ? (TagKind.Class, w & 0x7FFFFFFFu) : (TagKind.Obj, w);
            }
            return (v & 0x8000) != 0 ? (TagKind.Class, v & 0x7FFFu) : (TagKind.Obj, v);
        }

        private static int Popcount(int mask)
        {
            int n = 0;
            for (int bit = 0; bit < 8; bit++) if ((mask & (1 << bit)) != 0) n++;
            return n;
        }

        private static void SkipPid(LR r) => r.Raw(Popcount(r.U8()));

        /// <summary>The classes the scene records declare or refer to: the schema each was declared
        /// with and, once seen, the slot its class-refs use - every class-ref to one class must
        /// carry the same slot, which catches a record read from the wrong offset early.</summary>
        private sealed class Classes
        {
            // CCamera and CDib are declared in the document prefix; the scene records only refer to
            // them, at the schemas every v6+ file uses.
            private readonly Dictionary<string, int> _schema = new Dictionary<string, int> { ["CCamera"] = 5, ["CDib"] = 3 };
            private readonly Dictionary<string, uint> _slot = new Dictionary<string, uint>();

            /// <summary>After a Decl or Class tag: the declaration's header (if any); returns the
            /// schema the object was written with.</summary>
            public int Object(LR r, TagKind kind, uint slot, string name)
            {
                if (kind == TagKind.Decl)
                {
                    int schema = r.U16();
                    int n = r.U16();
                    string got = Encoding.ASCII.GetString(r.Raw(n));
                    if (n != name.Length || got != name)
                    {
                        throw new LegacyParseError($"expected a {name} declaration {r.Ctx()}");
                    }
                    _schema[name] = schema;
                }
                else if (_slot.TryGetValue(name, out var have))
                {
                    if (have != slot) throw new LegacyParseError($"{name} class-ref to another slot {r.Ctx()}");
                }
                else
                {
                    _slot[name] = slot;
                }
                if (!_schema.TryGetValue(name, out var s))
                {
                    throw new LegacyParseError($"{name} object of an unknown schema {r.Ctx()}");
                }
                return s;
            }
        }

        private static void ReadCamera(LR r, RawPage page)
        {
            var eye = r.F64s(3);
            var target = r.F64s(3);
            var up = r.F64s(3);
            r.F64s(2); // constants 1.0, 1000.0
            byte perspective = r.U8();
            double fov = r.F64();
            double orthoHeight = r.F64();
            r.Raw(137 - 105);
            r.U16();
            r.Utf16();
            r.Raw(33);
            page.Eye = (eye[0], eye[1], eye[2]);
            page.Target = (target[0], target[1], target[2]);
            page.Up = (up[0], up[1], up[2]);
            page.Fov = fov;
            page.Parallel = perspective == 0;
            page.OrthoHeight = orthoHeight;
        }

        /// <summary>A CSkpStyle object's body: no attributes, a pid (schema 2+), GUID, a string,
        /// u32, name, description, then its options - each a u32 key and a u32 count of (u32 type,
        /// value) pairs.</summary>
        private static void SkipStyle(LR r, int schema)
        {
            if (r.U16() != 0) throw new LegacyParseError($"style with attributes {r.Ctx()}");
            if (schema >= 2) SkipPid(r);
            r.Raw(16);
            r.Utf16();
            r.U32();
            r.Utf16();
            r.Utf16();
            uint n = r.U32();
            for (uint i = 0; i < n; i++)
            {
                r.U32();
                uint m = r.U32();
                for (uint j = 0; j < m; j++)
                {
                    if (!StyleValueSizes.TryGetValue(r.U32(), out var size))
                    {
                        throw new LegacyParseError($"unknown style value type {r.Ctx()}");
                    }
                    r.Raw(size);
                }
            }
        }

        private static List<long> ReadRefs(LR r, uint n)
        {
            if (n > (uint)((r.Data.Length - r.Pos) / 2))
            {
                throw new LegacyParseError($"implausible ref count {n} {r.Ctx()}");
            }
            var refs = new List<long>();
            for (uint i = 0; i < n; i++)
            {
                var (kind, v) = ReadTag(r);
                if (kind != TagKind.Obj) throw new LegacyParseError($"expected a back-ref {r.Ctx()}");
                refs.Add(v);
            }
            return refs;
        }

        private static RawPage ReadRecord(Archive ar, LR r, Classes classes)
        {
            var (kind, slot) = ReadTag(r);
            if (kind != TagKind.Decl && kind != TagKind.Class)
            {
                throw new LegacyParseError($"expected a CViewPage record {r.Ctx()}");
            }
            int schema = classes.Object(r, kind, slot, "CViewPage");
            if (r.U16() != 0) throw new LegacyParseError($"scene with attributes {r.Ctx()}");
            if (schema >= 13) SkipPid(r);
            var page = new RawPage { Name = r.Utf16() };
            r.Utf16(); // description
            uint flags = r.U32();
            if ((flags & ~KnownFlags) != 0)
            {
                throw new LegacyParseError($"scene capture flags 0x{flags:x} {r.Ctx()}");
            }
            if (schema >= 15)
            {
                // saved through SketchUp's C API: the camera is there when its flag is, and there
                // is no thumbnail
                if ((flags & 0x01) != 0)
                {
                    (kind, slot) = ReadTag(r);
                    if (kind != TagKind.Decl && kind != TagKind.Class)
                    {
                        throw new LegacyParseError($"expected a camera {r.Ctx()}");
                    }
                    classes.Object(r, kind, slot, "CCamera");
                    ReadCamera(r, page);
                }
            }
            else
            {
                (kind, slot) = ReadTag(r);
                if (kind == TagKind.Decl || kind == TagKind.Class)
                {
                    classes.Object(r, kind, slot, "CCamera");
                    ReadCamera(r, page);
                }
                (kind, slot) = ReadTag(r);
                if (kind == TagKind.Decl || kind == TagKind.Class)
                {
                    classes.Object(r, kind, slot, "CDib");
                    LegacyReaders.ReadDib(ar, r);
                }
                if (schema >= 13) r.U8();
            }
            if ((flags & 0x02) != 0)
            {
                if (!RenderingOptionsLen.TryGetValue(ar.Ver, out var baseLen))
                {
                    throw new LegacyParseError($"rendering options of a v{ar.Ver} scene");
                }
                r.Raw(baseLen + (schema >= 15 ? 3 : 0));
                (kind, slot) = ReadTag(r);
                if (kind == TagKind.Decl || kind == TagKind.Class)
                {
                    SkipStyle(r, classes.Object(r, kind, slot, "CSkpStyle"));
                }
            }
            if ((flags & 0x04) != 0)
            {
                r.Raw(schema >= 13 ? 3 : 2);
                r.U32(); // time
                r.U8();
                r.Utf16(); // city
                r.Utf16(); // country
                r.Raw(ShadowInfoFixedLen);
            }
            if ((flags & 0x08) != 0) r.Raw(AxesLen + (schema >= 13 ? 1 : 0));
            if ((flags & 0x10) != 0)
            {
                r.U8();
                ReadRefs(r, r.U32());
            }
            if ((flags & 0x20) != 0) page.HiddenLayerIds = ReadRefs(r, r.U32());
            if ((flags & 0x40) != 0) ReadRefs(r, r.U32());
            if (schema >= 15)
            {
                r.Raw(TrailerLen - 1);
                return page;
            }
            r.Raw(TrailerLen);
            uint png = r.U32();
            if (png > (uint)(r.Data.Length - r.Pos))
            {
                throw new LegacyParseError($"implausible thumbnail length {png} {r.Ctx()}");
            }
            r.Raw((int)png);
            return page;
        }

        /// <summary>Every scene, with its camera and hidden layers, read as a sequence from the
        /// scene list in the file tail. Null when there is a scene list this reader can't fully
        /// read; an empty list when there is none.</summary>
        public static List<RawPage>? ReadPageList(byte[] data, Archive ar)
        {
            int start = FindDecl(data, ar.R.Pos);
            if (start < 0)
            {
                // a scene list in a schema this reader doesn't know
                return LegacyBytes.FindBytes(data, ClassName, ar.R.Pos, data.Length) >= 0 ? null : new List<RawPage>();
            }
            try
            {
                uint count = BitConverter.ToUInt32(data, start - 4);
                if (count < 1 || count > 10000) throw new LegacyParseError($"implausible scene count {count}");
                var r = new LR(data, start);
                var classes = new Classes();
                var pages = new List<RawPage>();
                for (uint i = 0; i < count; i++) pages.Add(ReadRecord(ar, r, classes));
                // the list ends with a ref to the selected scene (or a null ref)
                var (kind, _) = ReadTag(r);
                if (kind != TagKind.Obj && kind != TagKind.Null)
                {
                    throw new LegacyParseError($"no selected-scene ref after the scenes {r.Ctx()}");
                }
                foreach (var page in pages)
                {
                    page.HiddenLayerIds = page.HiddenLayerIds.FindAll(id =>
                        ar.Slots.TryGetValue((int)id, out var ent) && ent.Kind == "obj" && ent.Name == "CLayer");
                }
                return pages;
            }
            catch (Exception e) when (e is LegacyParseError || e is ArgumentException || e is IndexOutOfRangeException)
            {
                return null;
            }
        }
    }
}
