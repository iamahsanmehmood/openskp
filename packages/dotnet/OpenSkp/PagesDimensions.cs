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
        /// * 5BCF - the dimension plane's x-axis; 5BD0 - its normal.
        /// * 5BD2 - the offset distance (inches): how far the dimension line
        ///   sits from the measured segment, along the in-plane
        ///   perpendicular.
        ///
        /// The measured value is auto-computed from the two points (no
        /// cached text on the samples seen), so callers format it
        /// themselves. Endpoints come out in WORLD space (inches). A
        /// connection point that cannot be resolved drops the whole
        /// dimension (fail-safe).</summary>
        public static List<RawDimension> ParseDimensions(
            ChunkedBuffer modelDat,
            Dictionary<string, (double X, double Y, double Z)> id2pos,
            Dictionary<string, List<double>?> instWorld)
        {
            var dims = new List<RawDimension>();
            var needle = new byte[] { 0xCC, 0x5B };
            long i = 0;
            long n = modelDat.Length;

            (double X, double Y, double Z)? Point(byte[]? blockPayload)
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
                // type 2: resolve the geometry reference (vertex + instance).
                var refB = TlvFindInt(sub, 0x520B);
                byte[]? f53fc = refB != null ? TlvFindInt(TlvItemsInt(refB), 0x53FC) : null;
                var fi = f53fc != null ? TlvItemsInt(f53fc) : null;
                var vid = TlvFindInt(fi, 0x53FD);
                var iid = TlvFindInt(fi, 0x53FE);
                if (vid == null) return null;
                if (!id2pos.TryGetValue(Tlv.ToHexUpper(vid), out var local)) return null;
                if (iid != null && iid.Length >= 1 && iid[0] > 0 && 1 + iid[0] <= iid.Length)
                {
                    var idBytes = new byte[iid[0]];
                    Array.Copy(iid, 1, idBytes, 0, iid[0]);
                    if (instWorld.TryGetValue(Tlv.ToHexUpper(idBytes), out var w) && w != null)
                    {
                        return Transforms.TransformPoint(w.ToArray(), local);
                    }
                }
                return local; // model-root vertex - already world
            }

            while (true)
            {
                long j = modelDat.IndexOf(needle, i);
                if (j < 0) break;
                i = j + 1;
                if (j + 6 > n) continue;
                uint ln = modelDat.ReadU32(j + 2);
                if (ln < 40 || j + 6 + ln > n) continue;
                byte[] bodyBytes = modelDat.Slice(j + 6, (int)ln);
                var body = TlvItemsInt(bodyBytes);
                if (body == null) continue;
                bool has5Bcd = false, has5Bce = false;
                foreach (var it in body)
                {
                    if (it.Tag == 0x5BCD) has5Bcd = true;
                    if (it.Tag == 0x5BCE) has5Bce = true;
                }
                if (!has5Bcd || !has5Bce) continue;

                var a = Point(TlvFindInt(body, 0x5BCD));
                var b = Point(TlvFindInt(body, 0x5BCE));
                if (a == null || b == null) continue;

                var xaxisB = TlvFindInt(body, 0x5BCF);
                var normalB = TlvFindInt(body, 0x5BD0);
                var offB = TlvFindInt(body, 0x5BD2);

                dims.Add(new RawDimension
                {
                    A = a.Value,
                    B = b.Value,
                    PlaneX = (xaxisB != null && xaxisB.Length == 24)
                        ? (Tlv.ReadF64(xaxisB, 0), Tlv.ReadF64(xaxisB, 8), Tlv.ReadF64(xaxisB, 16))
                        : ((double, double, double)?)null,
                    Normal = (normalB != null && normalB.Length == 24)
                        ? (Tlv.ReadF64(normalB, 0), Tlv.ReadF64(normalB, 8), Tlv.ReadF64(normalB, 16))
                        : ((double, double, double)?)null,
                    Offset = (offB != null && offB.Length == 8) ? Tlv.ReadF64(offB, 0) : 0.0,
                });
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

                        if (page.Eye != null && page.Target != null) pages.Add(page);
                    }
                }
            }
            return pages;
        }
    }
}
