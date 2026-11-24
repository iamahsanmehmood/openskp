using System;
using System.Collections.Generic;

namespace OpenSkp
{
    /// <summary>VFF (2021+) scenes ("pages") and linear dimensions. Ported
    /// from Python's _core.py (PR #190) - see that module's
    /// _scan_vertex_positions / _scan_instance_transforms / _parse_dimensions
    /// / _find_page_node / _parse_pages for the byte-format details this
    /// file mirrors.</summary>
    internal sealed class RawPage
    {
        public string Name = "";
        public (double X, double Y, double Z)? Eye;
        public (double X, double Y, double Z)? Target;
        public (double X, double Y, double Z)? Up;
        public double Fov = 35.0;
        public bool Parallel;
        public double OrthoHeight;
        public List<long> HiddenLayerIds = new List<long>();
    }

    internal sealed class RawDimension
    {
        public (double X, double Y, double Z) A;
        public (double X, double Y, double Z) B;
        public double Offset;
        public (double X, double Y, double Z)? PlaneX;
        public (double X, double Y, double Z)? Normal;
        public (double X, double Y, double Z)? OffsetVector;
        public string Layer = "";
        public bool Hidden;
        public string Text = "";
    }

    internal static class PagesDimensions
    {
        // ── flat TLV, integer tags ──────────────────────────────────────
        // A second, deliberately separate flat-TLV reader from Tlv.ParseFlat/
        // FindFlat: those use the STRING byte-order tag convention the main
        // tree already relies on (e.g. "C409"), while the sub-records this
        // feature reads (5208, 520A, 53FC, 5BCD, ...) are most directly and
        // safely ported from Python's own _tlv_items/_tlv_find (which read
        // the tag as a little-endian uint16) by keeping the SAME integer
        // convention here, copying Python's numeric constants byte-for-byte
        // rather than hand-converting each one to the swapped string form.

        internal readonly struct FlatTlvItem
        {
            public readonly ushort Tag;
            public readonly byte[] Payload;
            public FlatTlvItem(ushort tag, byte[] payload) { Tag = tag; Payload = payload; }
        }

        internal static List<FlatTlvItem>? TlvItemsInt(byte[]? buf)
        {
            if (buf == null) return null;
            var items = new List<FlatTlvItem>();
            int off = 0;
            int n = buf.Length;
            while (off < n)
            {
                if (off + 6 > n) return null;
                ushort tag = (ushort)(buf[off] | (buf[off + 1] << 8));
                uint ln = Tlv.ReadU32(buf, off + 2);
                if (tag == 0 || off + 6 + ln > n) return null;
                var payload = new byte[ln];
                Array.Copy(buf, off + 6, payload, 0, (int)ln);
                items.Add(new FlatTlvItem(tag, payload));
                off += 6 + (int)ln;
            }
            return items;
        }

        internal static byte[]? TlvFindInt(List<FlatTlvItem>? items, ushort tag)
        {
            if (items == null) return null;
            foreach (var it in items)
            {
                if (it.Tag == tag) return it.Payload;
            }
            return null;
        }

        private static byte[] StripDe05(byte[] p)
        {
            if (p.Length >= 2 && p[0] == 0xDE && p[1] == 0x05)
            {
                uint idlen = Tlv.ReadU32(p, 2);
                var idb = new byte[idlen];
                Array.Copy(p, 6, idb, 0, (int)idlen);
                return idb;
            }
            return p;
        }

        /// <summary>Accumulate every vertex's persistent id (hex) -> (x, y, z)
        /// inches. A vertex is a "C409" record: "DC05" holds its persistent
        /// id (the "DE05" var-int payload), "C509" its 3xf64 position.
        /// Dimension connection points reference geometry by this id.
        /// Called once per top-level record - full_parse streams the TLV
        /// tree and never holds it whole.</summary>
        public static void ScanVertexPositions(TlvNode top, Dictionary<string, (double X, double Y, double Z)> id2pos)
        {
            void Walk(List<TlvNode> nodes)
            {
                foreach (var el in nodes)
                {
                    if (el.Tag == "C409")
                    {
                        var dc05 = Geometry.FindChildTag(el.Children, "DC05");
                        var c509 = Geometry.FindChildTag(el.Children, "C509");
                        if (dc05 != null && c509 != null && c509.Payload.Length == 24)
                        {
                            var idb = StripDe05(dc05.Payload);
                            id2pos[Tlv.ToHexUpper(idb)] = (
                                Tlv.ReadF64(c509.Payload, 0),
                                Tlv.ReadF64(c509.Payload, 8),
                                Tlv.ReadF64(c509.Payload, 16));
                        }
                    }
                    if (el.Children.Count > 0) Walk(el.Children);
                }
            }
            Walk(new List<TlvNode> { top });
        }

        /// <summary>Accumulate each instance's persistent id (hex) -> its
        /// WORLD transform (a 13-double matrix), walking the instance tree
        /// and composing parent x local at every "6419". Per top-level
        /// record, like ScanVertexPositions - an instance chain never
        /// crosses top-level records.
        ///
        /// A dimension connects to geometry INSIDE a placed component; its
        /// connection reference names the vertex AND the instance holding
        /// it. The vertex position is definition-local, so it must be
        /// lifted to world by the instance's transform for the dimension to
        /// land where the author drew it.</summary>
        public static void ScanInstanceTransforms(TlvNode top, Dictionary<string, List<double>?> world)
        {
            void Walk(List<TlvNode> nodes, List<double>? parent)
            {
                foreach (var el in nodes)
                {
                    if (el.Tag == "6419")
                    {
                        var d007 = Geometry.FindChildTag(el.Children, "D007");
                        var dc05 = d007 != null ? Geometry.FindChildTag(d007.Children, "DC05") : null;
                        string? iid = dc05 != null ? Tlv.ToHexUpper(StripDe05(dc05.Payload)) : null;

                        var m = Geometry.FindChildTag(el.Children, "6619");
                        List<double>? mat = null;
                        if (m != null && m.Payload.Length == 104)
                        {
                            mat = new List<double>(13);
                            for (int i = 0; i < 13; i++) mat.Add(Tlv.ReadF64(m.Payload, i * 8));
                        }
                        List<double>? here = mat != null ? Transforms.MultiplyMatrices(parent!, mat) : parent;
                        if (iid != null) world[iid] = here;
                        Walk(el.Children, here);
                    }
                    else if (el.Children.Count > 0)
                    {
                        Walk(el.Children, parent);
                    }
                }
            }
            Walk(new List<TlvNode> { top }, null);
        }

        /// <summary>Linear dimensions (SketchUp's Dimension tool).
        ///
        /// A dimension entity is a "5BCC" record (raw bytes cc 5b) holding:
        ///
        /// * 5BCD / 5BCE - the two connection points. Each wraps a 5208 whose
        ///   5209 is the connection TYPE (1 = a free explicit point in 520A,
        ///   already world space; 2 = connected to geometry, 520A is zero and
        ///   520B -> 53FC names the target: 53FD = the vertex by persistent
        ///   id, 53FE = a length-prefixed persistent id of the INSTANCE
        ///   holding it - the vertex position is definition-local, so it is
        ///   lifted to world by that instance's transform).
        /// * 5BCF - the dimension plane's NORMAL (SketchUp's SUDimensionLinearGetNormal
        ///   equals it); 5BD0 - its X axis. Kept in the PlaneX and Normal properties, whose
        ///   names say the opposite.
        /// * 5BD2 - the offset distance (inches): how far the dimension line
        ///   sits from the measured segment, along the in-plane
        ///   perpendicular.
        ///
        /// The measured value is auto-computed from the two points (no
        /// cached text on the samples seen), so callers format it
        /// themselves. Endpoints come out in WORLD space (inches). A
        /// connection point that cannot be resolved drops the whole
        /// dimension (fail-safe).</summary>
        private static List<string> LengthPrefixedIds(byte[] p)
        {
            var ids = new List<string>();
            int off = 0;
            while (off < p.Length)
            {
                int ln = p[off];
                if (ln == 0 || off + 1 + ln > p.Length) break;
                var sub = new byte[ln];
                Array.Copy(p, off + 1, sub, 0, ln);
                ids.Add(Tlv.ToHexUpper(sub));
                off += 1 + ln;
            }
            return ids;
        }

        private static (double X, double Y, double Z)? DimensionConnectionPoint(
            byte[]? blockPayload,
            Dictionary<string, (double X, double Y, double Z)> id2pos,
            Dictionary<string, List<double>?> instWorld)
        {
            if (blockPayload == null) return null;
            var blk = TlvFindInt(TlvItemsInt(blockPayload), 0x5208);
            if (blk == null) return null;
            var sub = TlvItemsInt(blk);
            var typB = TlvFindInt(sub, 0x5209);
            uint? typ = (typB != null && typB.Length == 4) ? Tlv.ReadU32(typB, 0) : (uint?)null;
            if (typ == 1)
            {
                var pos = TlvFindInt(sub, 0x520A);
                if (pos == null || pos.Length != 24) return null;
                return (Tlv.ReadF64(pos, 0), Tlv.ReadF64(pos, 8), Tlv.ReadF64(pos, 16));
            }
            var refB = TlvFindInt(sub, 0x520B);
            byte[]? f53fc = refB != null ? TlvFindInt(TlvItemsInt(refB), 0x53FC) : null;
            var fi = f53fc != null ? TlvItemsInt(f53fc) : null;
            var vid = TlvFindInt(fi, 0x53FD);
            var iid = TlvFindInt(fi, 0x53FE);
            if (vid == null) return null;
            if (!id2pos.TryGetValue(Tlv.ToHexUpper(vid), out var local)) return null;
            if (iid != null && iid.Length > 0)
            {
                var path = LengthPrefixedIds(iid);
                if (path.Count == 0) path.Add(Tlv.ToHexUpper(iid));
                for (int k = path.Count - 1; k >= 0; k--)
                {
                    if (instWorld.TryGetValue(path[k], out var w) && w != null && w.Count > 0)
                    {
                        return Transforms.TransformPoint(w.ToArray(), local);
                    }
                }
            }
            return local;
        }

        private static bool FlatTlvHasFields(ChunkedBuffer buf, long begin, long end, ushort first = 0x5BCD, ushort second = 0x5BCE)
        {
            bool hasFirst = false, hasSecond = false;
            long off = begin;
            while (off < end)
            {
                if (off + 6 > end) return false;
                ushort tag = (ushort)(buf[off] | (buf[off + 1] << 8));
                uint ln = buf.ReadU32(off + 2);
                if (tag == 0 || ln > end - off - 6) return false;
                if (tag == first) hasFirst = true;
                if (tag == second) hasSecond = true;
                off += 6 + ln;
            }
            return hasFirst && hasSecond;
        }

        private static RawDimension? ParseOne5Bcc(
            ChunkedBuffer modelDat,
            long j,
            Dictionary<string, (double X, double Y, double Z)> id2pos,
            Dictionary<string, List<double>?> instWorld)
        {
            long n = modelDat.Length;
            if (j + 6 > n) return null;
            uint ln = modelDat.ReadU32(j + 2);
            if (ln < 40 || j + 6 + ln > n) return null;
            if (!FlatTlvHasFields(modelDat, j + 6, j + 6 + ln, 0x5BCD, 0x5BCE)) return null;
            byte[] bodyBytes = modelDat.Slice(j + 6, (int)ln);
            var body = TlvItemsInt(bodyBytes);
            if (body == null || body.Count == 0) return null;
            bool has5Bcd = false, has5Bce = false;
            foreach (var it in body)
            {
                if (it.Tag == 0x5BCD) has5Bcd = true;
                if (it.Tag == 0x5BCE) has5Bce = true;
            }
            if (!has5Bcd || !has5Bce) return null;

            var a = DimensionConnectionPoint(TlvFindInt(body, 0x5BCD), id2pos, instWorld);
            var b = DimensionConnectionPoint(TlvFindInt(body, 0x5BCE), id2pos, instWorld);
            if (a == null || b == null) return null;

            string layer = "";
            bool hidden = false;
            var baseB = TlvFindInt(body, 0x59D8);
            if (baseB != null)
            {
                var drawB = TlvFindInt(TlvItemsInt(baseB), 0x07D0);
                if (drawB != null)
                {
                    var props = TlvItemsInt(drawB);
                    var layerB = TlvFindInt(props, 0x07D2);
                    if (layerB != null && layerB.Length > 0)
                    {
                        layer = Tlv.ParseVarInt(layerB, 0, layerB.Length).ToString();
                    }
                    var flagsB = TlvFindInt(props, 0x07D3);
                    if (flagsB != null && flagsB.Length > 0)
                    {
                        hidden = (flagsB[0] & 0x01) != 0;
                    }
                }
            }

            var xaxisB = TlvFindInt(body, 0x5BCF);
            var normalB = TlvFindInt(body, 0x5BD0);
            var axisB = TlvFindInt(body, 0x5BD1);
            var offB = TlvFindInt(body, 0x5BD2);

            var planeX = (xaxisB != null && xaxisB.Length == 24)
                ? (Tlv.ReadF64(xaxisB, 0), Tlv.ReadF64(xaxisB, 8), Tlv.ReadF64(xaxisB, 16))
                : ((double X, double Y, double Z)?)null;
            var normal = (normalB != null && normalB.Length == 24)
                ? (Tlv.ReadF64(normalB, 0), Tlv.ReadF64(normalB, 8), Tlv.ReadF64(normalB, 16))
                : ((double X, double Y, double Z)?)null;
            double offset = (offB != null && offB.Length == 8) ? Tlv.ReadF64(offB, 0) : 0.0;
            (double X, double Y, double Z)? offsetVector = null;
            if (axisB != null && axisB.Length == 4 && planeX.HasValue && normal.HasValue)
            {
                uint axis = Tlv.ReadU32(axisB, 0);
                var pNorm = planeX.Value;
                var pX = normal.Value;
                if (axis == 1)
                {
                    offsetVector = (
                        (pNorm.Y * pX.Z - pNorm.Z * pX.Y) * offset + 0.0,
                        (pNorm.Z * pX.X - pNorm.X * pX.Z) * offset + 0.0,
                        (pNorm.X * pX.Y - pNorm.Y * pX.X) * offset + 0.0);
                }
                else if (axis == 2)
                {
                    offsetVector = (
                        -pX.X * offset + 0.0,
                        -pX.Y * offset + 0.0,
                        -pX.Z * offset + 0.0);
                }
            }

            return new RawDimension
            {
                A = a.Value,
                B = b.Value,
                PlaneX = planeX,
                Normal = normal,
                Offset = offset,
                OffsetVector = offsetVector,
                Layer = layer,
                Hidden = hidden,
            };
        }

        private static TextEntity? ParseVffText(
            ChunkedBuffer modelDat,
            long j,
            Dictionary<string, (double X, double Y, double Z)> id2pos,
            Dictionary<string, List<double>?> transforms)
        {
            uint size = modelDat.ReadU32(j + 2);
            var fields = TlvItemsInt(modelDat.Slice(j + 6, (int)size));
            if (fields == null) return null;
            var valB = TlvFindInt(fields, 0x55F1);
            var connB = TlvFindInt(fields, 0x55F4);
            if (valB == null || connB == null) return null;
            string textStr = System.Text.Encoding.UTF8.GetString(valB);
            var point = DimensionConnectionPoint(connB, id2pos, transforms);
            var lpB = TlvFindInt(fields, 0x55F5);
            var labelPoint = (lpB != null && lpB.Length == 24)
                ? (Tlv.ReadF64(lpB, 0), Tlv.ReadF64(lpB, 8), Tlv.ReadF64(lpB, 16))
                : ((double X, double Y, double Z)?)null;
            var xB = TlvFindInt(fields, 0x55F2);
            var yB = TlvFindInt(fields, 0x55F3);
            var screenPos = (xB != null && yB != null && xB.Length == 8 && yB.Length == 8)
                ? (Tlv.ReadF64(xB, 0), Tlv.ReadF64(yB, 0))
                : ((double X, double Y)?)null;
            var ltB = TlvFindInt(fields, 0x55FD);
            int leaderType = (ltB != null && ltB.Length == 4) ? (int)Tlv.ReadU32(ltB, 0) : 2;
            var atB = TlvFindInt(fields, 0x55FA);
            int arrowType = (atB != null && atB.Length == 4) ? (int)Tlv.ReadU32(atB, 0) : 3;
            var dlB = TlvFindInt(fields, 0x55FC);
            bool displayLeader = (dlB != null && dlB.Length == 1) ? dlB[0] != 0 : true;
            string layer = "";
            bool hidden = false;
            var drawB = TlvFindInt(fields, 0x07D0);
            if (drawB != null)
            {
                var props = TlvItemsInt(drawB);
                var layerB = TlvFindInt(props, 0x07D2);
                if (layerB != null && layerB.Length > 0)
                {
                    layer = Tlv.ParseVarInt(layerB, 0, layerB.Length).ToString();
                }
                var flagsB = TlvFindInt(props, 0x07D3);
                if (flagsB != null && flagsB.Length > 0)
                {
                    hidden = (flagsB[0] & 0x01) != 0;
                }
            }
            return new TextEntity
            {
                Text = textStr,
                Hidden = hidden,
                Layer = layer,
                Point = point,
                LabelPoint = labelPoint,
                ScreenPosition = screenPos,
                LeaderType = leaderType,
                ArrowType = arrowType,
                DisplayLeader = displayLeader,
            };
        }

        private static List<(long Offset, long Size)> TlvHeadersRange(ChunkedBuffer md, long start, long end)
        {
            var list = new List<(long, long)>();
            for (long p = start; p + 6 <= end;)
            {
                long n = md.ReadU32(p + 2);
                if (n > end - p - 6) break;
                list.Add((p, n));
                p += 6 + n;
            }
            return list;
        }

        private static string TagHexAt(ChunkedBuffer md, long off)
        {
            return $"{md[off]:X2}{md[off + 1]:X2}";
        }

        private static readonly HashSet<string> DefIdContainers = new HashSet<string>
        {
            "8813", "D007", "8913", "8A13", "8B13", "8C13", "8D13"
        };

        private static long? EntityIdFromTlvSpan(ChunkedBuffer md, long rec, long recEnd, int depth = 0)
        {
            if (depth > 24 || rec + 6 > recEnd) return null;
            var kids = TlvHeadersRange(md, rec + 6, recEnd);
            foreach (var (kOff, kSz) in kids)
            {
                string t = TagHexAt(md, kOff);
                try
                {
                    if (t == "DE05" && kSz > 0 && kSz <= 8)
                    {
                        var b = md.Slice(kOff + 6, (int)kSz);
                        return Tlv.ParseVarInt(b, 0, b.Length);
                    }
                    if (t == "DC05" && kSz >= 6 && md[kOff + 6] == 0xDE && md[kOff + 7] == 0x05)
                    {
                        uint z = md.ReadU32(kOff + 8);
                        if (z > 0 && z <= 8 && z <= kSz - 6)
                        {
                            var b = md.Slice(kOff + 12, (int)z);
                            return Tlv.ParseVarInt(b, 0, b.Length);
                        }
                    }
                }
                catch
                {
                    continue;
                }
            }
            foreach (var (kOff, kSz) in kids)
            {
                string t = TagHexAt(md, kOff);
                if (!DefIdContainers.Contains(t)) continue;
                var found = EntityIdFromTlvSpan(md, kOff, kOff + 6 + kSz, depth + 1);
                if (found.HasValue) return found;
            }
            return null;
        }

        private static List<(long Begin, long End, long Id)> ScanDefinitionSpans(
            ChunkedBuffer md, Dictionary<long, Geometry.RawDefinition> defs)
        {
            var spans = new List<(long, long, long)>();
            if (md.Length < 6) return spans;
            var hs = TlvHeadersRange(md, 0, md.Length);
            if (hs.Count == 1 && md[hs[0].Offset] == 0xF4 && md[hs[0].Offset + 1] == 0x01)
            {
                hs = TlvHeadersRange(md, hs[0].Offset + 6, hs[0].Offset + 6 + hs[0].Size);
            }
            foreach (var (hOff, hSz) in hs)
            {
                if (TagHexAt(md, hOff) != "F901") continue;
                var level = TlvHeadersRange(md, hOff + 6, hOff + 6 + hSz);
                bool ok = true;
                foreach (string expected in new[] { "7017", "7117" })
                {
                    long mOff = -1, mSz = 0;
                    foreach (var (recOff, recSz) in level)
                    {
                        if (TagHexAt(md, recOff) == expected) { mOff = recOff; mSz = recSz; break; }
                    }
                    if (mOff < 0) { ok = false; break; }
                    level = TlvHeadersRange(md, mOff + 6, mOff + 6 + mSz);
                }
                if (!ok) continue;
                foreach (var (recOff, recSz) in level)
                {
                    if (TagHexAt(md, recOff) != "7C15") continue;
                    var id = EntityIdFromTlvSpan(md, recOff, recOff + 6 + recSz);
                    if (!id.HasValue || !defs.ContainsKey(id.Value)) continue;
                    spans.Add((recOff, recOff + 6 + recSz, id.Value));
                }
            }
            return spans;
        }

        private static long? OwningDefinition(List<(long Begin, long End, long Id)> spans, long off)
        {
            long? best = null;
            long bestLen = long.MaxValue;
            foreach (var (begin, end, id) in spans)
            {
                if (off < begin || off >= end) continue;
                long len = end - begin;
                if (len < bestLen)
                {
                    bestLen = len;
                    best = id;
                }
            }
            return best;
        }

        private static bool TlvContainsTag(ChunkedBuffer buf, long begin, long end, ushort tag, int depth = 0)
        {
            if (depth > 64) return false;
            long off = begin;
            while (off < end)
            {
                if (off + 6 > end) return false;
                ushort t = (ushort)(buf[off] | (buf[off + 1] << 8));
                uint ln = buf.ReadU32(off + 2);
                if (t == 0 || ln > end - off - 6) return false;
                off += 6 + ln;
            }
            for (off = begin; off < end;)
            {
                ushort t = (ushort)(buf[off] | (buf[off + 1] << 8));
                uint ln = buf.ReadU32(off + 2);
                if (t == tag || TlvContainsTag(buf, off + 6, off + 6 + ln, tag, depth + 1)) return true;
                off += 6 + ln;
            }
            return false;
        }

        public static void ComposeInstanceWorld(
            GeometryBuilder rootBuilder,
            Dictionary<long, Geometry.RawDefinition> definitions,
            Dictionary<string, List<double>?> world)
        {
            void Walk(GeometryBuilder builder, List<double>? parent, int depth)
            {
                if (depth > 64) return;
                foreach (var inst in builder.Instances)
                {
                    List<double>? here = (inst.Matrix != null && inst.Matrix.Count > 0)
                        ? Transforms.MultiplyMatrices(parent!, inst.Matrix)
                        : parent;
                    if (!string.IsNullOrEmpty(inst.PersistentId))
                    {
                        world[inst.PersistentId] = here;
                    }
                    if (!inst.RefIdx.HasValue) continue;
                    if (definitions.TryGetValue(inst.RefIdx.Value, out var childDef))
                    {
                        Walk(childDef.Builder, here, depth + 1);
                    }
                }
            }
            Walk(rootBuilder, null, 0);
        }

        public static void AssignVffAnnotations(
            ChunkedBuffer modelDat,
            Dictionary<string, (double X, double Y, double Z)> id2pos,
            Dictionary<string, List<double>?> modelWorld,
            GeometryBuilder rootBuilder,
            Dictionary<long, Geometry.RawDefinition> defsDict)
        {
            var spans = ScanDefinitionSpans(modelDat, defsDict);
            long? composedOwner = null;
            var localWorld = new Dictionary<string, List<double>?>();
            var noXf = new Dictionary<string, List<double>?>();
            long n = modelDat.Length;
            for (long j = 0; j + 6 <= n; j++)
            {
                byte b0 = modelDat[j];
                byte b1 = modelDat[j + 1];
                if (!((b0 == 0xCC && b1 == 0x5B) || (b0 == 0xF0 && b1 == 0x55))) continue;
                uint ln = modelDat.ReadU32(j + 2);
                if (ln < 40 || j + 6 + ln > n) continue;
                bool isText = b0 == 0xF0;
                if (!FlatTlvHasFields(modelDat, j + 6, j + 6 + ln, isText ? (ushort)0x55F1 : (ushort)0x5BCD, isText ? (ushort)0x55F4 : (ushort)0x5BCE))
                {
                    continue;
                }
                var owner = OwningDefinition(spans, j);
                var xf = modelWorld;
                if (owner.HasValue)
                {
                    if (TlvContainsTag(modelDat, j + 6, j + 6 + ln, 0x53FE))
                    {
                        if (composedOwner != owner.Value)
                        {
                            localWorld.Clear();
                            ComposeInstanceWorld(defsDict[owner.Value].Builder, defsDict, localWorld);
                            composedOwner = owner.Value;
                        }
                        xf = localWorld;
                    }
                    else
                    {
                        xf = noXf;
                    }
                }
                var targetBuilder = owner.HasValue ? defsDict[owner.Value].Builder : rootBuilder;
                if (isText)
                {
                    var txt = ParseVffText(modelDat, j, id2pos, xf);
                    if (txt != null) targetBuilder.Texts.Add(txt);
                    continue;
                }
                var dim = ParseOne5Bcc(modelDat, j, id2pos, xf);
                if (dim != null)
                {
                    targetBuilder.Dimensions.Add(new Dimension
                    {
                        Text = dim.Text,
                        Hidden = dim.Hidden,
                        Layer = dim.Layer,
                        A = dim.A,
                        B = dim.B,
                        Offset = dim.Offset,
                        PlaneX = dim.PlaneX,
                        Normal = dim.Normal,
                        OffsetVector = dim.OffsetVector,
                    });
                }
            }
        }

        public static List<RawDimension> ParseDimensions(
            ChunkedBuffer modelDat,
            Dictionary<string, (double X, double Y, double Z)> id2pos,
            Dictionary<string, List<double>?> instWorld)
        {
            var dims = new List<RawDimension>();
            var needle = new byte[] { 0xCC, 0x5B };
            long i = 0;
            while (true)
            {
                long j = modelDat.IndexOf(needle, i);
                if (j < 0) break;
                i = j + 1;
                var d = ParseOne5Bcc(modelDat, j, id2pos, instWorld);
                if (d != null) dims.Add(d);
            }
            return dims;
        }

        /// <summary>Return the "0702" scenes node inside top's subtree, or
        /// null. Called per top-level record; retaining the (small) 0702
        /// subtree is the only thing kept alive past the streaming loop.</summary>
        public static TlvNode? FindPageNode(TlvNode top)
        {
            return Geometry.FindChildTag(new List<TlvNode> { top }, "0702");
        }

        /// <summary>Scenes ("pages"). The 0702 node's payload nests
        /// 6D60 > 6D61 > one 7148 record per page:
        ///
        /// * 6F54 > 6F55 - page name (UTF-8)
        /// * 714A > 34BC - camera: 34BD eye, 34BE target, 34BF up (3xf64,
        ///   inches), 34C4 field of view (degrees), 34C2 u8 = PERSPECTIVE
        ///   flag (00 = parallel projection - calibrated against the
        ///   bundled scene thumbnails: parallel plans/elevations carry 00
        ///   and their 34C3 visible height matches the thumbnail framing
        ///   exactly, while perspective scenes carry 01 with a stale 34C3),
        ///   34C3 f64 = visible height when parallel (inches)
        /// * 7150 - layers hidden in this page: (u8 length, var-int layer
        ///   id) runs</summary>
        public static List<RawPage> ParsePages(TlvNode? node)
        {
            var pages = new List<RawPage>();
            if (node == null) return pages;

            static (double X, double Y, double Z)? Vec3(byte[]? p) =>
                (p != null && p.Length == 24) ? (Tlv.ReadF64(p, 0), Tlv.ReadF64(p, 8), Tlv.ReadF64(p, 16)) : ((double, double, double)?)null;

            var t60Items = TlvItemsInt(node.Payload);
            if (t60Items == null) return pages;
            foreach (var it60 in t60Items)
            {
                if (it60.Tag != 0x6D60) continue;
                var t61Items = TlvItemsInt(it60.Payload);
                if (t61Items == null) continue;
                foreach (var it61 in t61Items)
                {
                    if (it61.Tag != 0x6D61) continue;
                    var t48Items = TlvItemsInt(it61.Payload);
                    if (t48Items == null) continue;
                    foreach (var it48 in t48Items)
                    {
                        if (it48.Tag != 0x7148) continue;
                        var items = TlvItemsInt(it48.Payload);
                        if (items == null) continue;

                        var page = new RawPage();

                        var head = TlvItemsInt(TlvFindInt(items, 0x6F54));
                        var name = TlvFindInt(head, 0x6F55);
                        if (name != null && name.Length > 0)
                        {
                            page.Name = System.Text.Encoding.UTF8.GetString(name);
                        }

                        var camWrap = TlvItemsInt(TlvFindInt(items, 0x714A));
                        var cam = camWrap != null ? TlvItemsInt(TlvFindInt(camWrap, 0x34BC)) : null;
                        if (cam != null)
                        {
                            page.Eye = Vec3(TlvFindInt(cam, 0x34BD));
                            page.Target = Vec3(TlvFindInt(cam, 0x34BE));
                            page.Up = Vec3(TlvFindInt(cam, 0x34BF));
                            var fov = TlvFindInt(cam, 0x34C4);
                            if (fov != null && fov.Length == 8) page.Fov = Tlv.ReadF64(fov, 0);
                            var flag = TlvFindInt(cam, 0x34C2);
                            page.Parallel = flag != null && flag.Length > 0 && flag[0] == 0;
                            var height = TlvFindInt(cam, 0x34C3);
                            if (height != null && height.Length == 8) page.OrthoHeight = Tlv.ReadF64(height, 0);
                        }

                        var hidden = TlvFindInt(items, 0x7150);
                        int off = 0;
                        while (hidden != null && off + 1 <= hidden.Length)
                        {
                            int ln = hidden[off];
                            if (ln == 0 || off + 1 + ln > hidden.Length) break;
                            page.HiddenLayerIds.Add(Tlv.ParseVarInt(hidden, off + 1, ln));
                            off += 1 + ln;
                        }

                        // A scene that doesn't save its camera ("Camera Location" unticked)
                        // is still a scene: it keeps its name and hidden layers, with
                        // Eye/Target/Up null.
                        pages.Add(page);
                    }
                }
            }
            return pages;
        }
    }
}
