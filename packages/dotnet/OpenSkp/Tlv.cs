using System;
using System.Collections.Generic;

namespace OpenSkp
{
    internal readonly struct TlvHeader : IEquatable<TlvHeader>
    {
        public readonly ulong Offset;
        public readonly ulong Size;

        public TlvHeader(ulong offset, ulong size)
        {
            Offset = offset;
            Size = size;
        }

        public bool Equals(TlvHeader other) => Offset == other.Offset && Size == other.Size;
        public override bool Equals(object? obj) => obj is TlvHeader other && Equals(other);
        public override int GetHashCode() => unchecked((Offset.GetHashCode() * 397) ^ Size.GetHashCode());
    }

    /// <summary>A single Tag-Length-Value node in the binary parse tree.
    /// Offset/Size are `long` since a decompressed model.dat can exceed
    /// 2 GB (see ChunkedBuffer) - the position within it then no longer
    /// fits in an Int32.</summary>
    internal sealed class TlvNode
    {
        public long Offset;
        public string Tag = "";
        public long Size;
        public List<TlvNode> Children = new List<TlvNode>();
        public byte[] Payload = Array.Empty<byte>();
    }

    /// <summary>Low-level TLV (Tag-Length-Value) binary parsing helpers.
    /// Ported from Python's _core.py; all multi-byte reads are explicitly
    /// little-endian regardless of host platform endianness.</summary>
    internal static class Tlv
    {
        /// <summary>VFF (2021+) model.dat binary structure uses Type-Length-Value (TLV) records.
        /// Most TLV tags carry raw leaf binary/string payloads, but container tags contain
        /// nested sub-TLV nodes (e.g. definitions, drawing elements, component instances).
        /// ContainerTags lists every tag hex ID that the TLV parser must recursively traverse.</summary>
        public static readonly HashSet<string> ContainerTags = new HashSet<string>
        {
            "F401", "F701", "D430", "D530", "C832",
            "7C15", "8813", "8913", "8A13", "8B13", "8C13", "8D13", "4C1D", "6419",
            "F901", "7017", "7117", "D007", "C409", "9411", "9511", "0F01",
            "384A", "B80B", "9713", "2C4C", "AC0D", "AE0D", "F601", "F801",
            "983A", "993A", "8C3C", "8D3C",
            // Image-entity placement: an Image placed in the model wraps a
            // standard 6419 instance node inside 9013 -> 401F. Without these
            // two containers, that inner instance stays buried in an opaque
            // payload and the image definition looks "never placed".
            "9013", "401F",
            // VFF guide-line / guide-point lists and entity wrappers.
            "9113", "9213", "6942", "6C42", "6842",
        };

        public static ushort ReadU16(byte[] data, int offset)
        {
            return (ushort)(data[offset] | (data[offset + 1] << 8));
        }

        public static uint ReadU32(byte[] data, int offset)
        {
            return (uint)(data[offset] | (data[offset + 1] << 8) | (data[offset + 2] << 16) | (data[offset + 3] << 24));
        }

        public static int ReadI32(byte[] data, int offset)
        {
            return unchecked((int)ReadU32(data, offset));
        }

        public static double ReadF64(byte[] data, int offset)
        {
            long bits = 0;
            for (int i = 0; i < 8; i++)
            {
                bits |= (long)data[offset + i] << (8 * i);
            }
            return BitConverter.Int64BitsToDouble(bits);
        }

        public static long ParseVarInt(byte[] data, int offset, int length)
        {
            long val = 0;
            for (int i = 0; i < length; i++)
            {
                val |= (long)data[offset + i] << (8 * i);
            }
            return val;
        }

        public static string ToHexUpper(byte[] data)
        {
            var chars = new char[data.Length * 2];
            for (int i = 0; i < data.Length; i++)
            {
                string h = data[i].ToString("X2");
                chars[i * 2] = h[0];
                chars[i * 2 + 1] = h[1];
            }
            return new string(chars);
        }

        private static string TagHex(byte[] data, int offset)
        {
            return data[offset].ToString("X2") + data[offset + 1].ToString("X2");
        }

        private static string TagHex(ChunkedBuffer data, long offset)
        {
            return data[offset].ToString("X2") + data[offset + 1].ToString("X2");
        }

        /// <summary>Deepest container nesting the parser follows. A real
        /// model.dat nests about a dozen levels (the deepest file checked
        /// reaches 11). A hostile file can nest tens of thousands, which
        /// aborts the whole process with a stack overflow that no catch can
        /// intercept, so deeper input throws a SkpParseException
        /// instead.</summary>
        public const int MaxDepth = 64;
        private const ulong TlvWrap = 1UL << 32;

        private sealed class HeaderResolver
        {
            private struct Run
            {
                public ulong Offset;
                public ulong Size;
                public ulong Count;
                public ulong Step => Size + 6;
                public ulong End => Offset + Count * (Size + 6);
            }

            private sealed class Chain
            {
                public readonly List<Run> Runs = new List<Run>();
                public readonly List<TlvHeader> Candidates = new List<TlvHeader>();
                public bool Indexed;

                public ulong End => Runs[Runs.Count - 1].End;

                public int RunAt(ulong p)
                {
                    int lo = 0;
                    int hi = Runs.Count;
                    while (lo < hi)
                    {
                        int mid = lo + ((hi - lo) >> 1);
                        if (p < Runs[mid].Offset) hi = mid;
                        else lo = mid + 1;
                    }
                    return lo - 1;
                }

                public bool Contains(ulong p)
                {
                    if (p < Runs[0].Offset || p >= End) return false;
                    var r = Runs[RunAt(p)];
                    return (p - r.Offset) % r.Step == 0;
                }

                public ulong Clip(ulong limit)
                {
                    ulong end = End;
                    if (limit >= end) return end;
                    var r = Runs[RunAt(limit)];
                    ulong step = r.Step;
                    return r.Offset + ((limit - r.Offset) / step) * step;
                }

                public void Append(ulong p, ulong size)
                {
                    int n = Runs.Count;
                    if (n > 0 && Runs[n - 1].Size == size && End == p)
                    {
                        var last = Runs[n - 1];
                        last.Count += 1;
                        Runs[n - 1] = last;
                    }
                    else
                    {
                        Runs.Add(new Run { Offset = p, Size = size, Count = 1 });
                    }
                }
            }

            private readonly struct View
            {
                public readonly Chain Chain;
                public readonly ulong Begin;
                public readonly ulong End;
                public View(Chain chain, ulong begin, ulong end)
                {
                    Chain = chain;
                    Begin = begin;
                    End = end;
                }
            }

            private sealed class Plan
            {
                public readonly List<View> Views;
                public readonly ulong Stop;
                public Plan(List<View> views, ulong stop)
                {
                    Views = views;
                    Stop = stop;
                }
            }

            private readonly Func<ulong, uint> _length;
            private readonly Func<ulong, bool> _container;
            private readonly ulong _limit;
            private int _attempts = 256;
            private readonly List<Chain> _chains = new List<Chain>();
            private readonly Dictionary<(ulong Start, ulong End, int Depth), Plan?> _decisions =
                new Dictionary<(ulong, ulong, int), Plan?>();

            public HeaderResolver(Func<ulong, uint> length, Func<ulong, bool> container, ulong end)
            {
                _length = length;
                _container = container;
                _limit = end;
            }

            private Chain? ChainAt(ulong p)
            {
                for (int i = _chains.Count - 1; i >= 0; i--)
                {
                    if (_chains[i].Contains(p)) return _chains[i];
                }
                return null;
            }

            private Plan Ordinary(ulong start, ulong end)
            {
                var views = new List<View>();
                ulong p = start;
                while (end - p >= 6)
                {
                    var cached = ChainAt(p);
                    if (cached != null)
                    {
                        ulong next = cached.Clip(end);
                        if (next > p) views.Add(new View(cached, p, next));
                        p = next;
                        if (p < cached.End) return new Plan(views, p);
                        continue;
                    }
                    var chain = new Chain();
                    ulong begin = p;
                    int probes = 0;
                    bool joined = false;
                    while (end - p >= 6)
                    {
                        if (probes != 0 && (probes & 1023) == 0 && ChainAt(p) != null)
                        {
                            joined = true;
                            break;
                        }
                        ulong size = _length(p);
                        if (size > end - p - 6) break;
                        chain.Append(p, size);
                        p += 6 + size;
                        probes++;
                    }
                    if (p > begin)
                    {
                        _chains.Add(chain);
                        views.Add(new View(chain, begin, p));
                    }
                    if (!joined) return new Plan(views, p);
                }
                return new Plan(views, p);
            }

            private void Index(Chain chain)
            {
                if (chain.Indexed) return;
                chain.Indexed = true;
                foreach (var r in chain.Runs)
                {
                    ulong step = r.Step;
                    for (ulong k = 0; k < r.Count; k++)
                    {
                        ulong p = r.Offset + k * step;
                        if ((_limit - p) - step < TlvWrap) return;
                        if (_container(p)) chain.Candidates.Add(new TlvHeader(p, r.Size));
                    }
                }
            }

            private static int LowerBound(List<TlvHeader> candidates, ulong begin)
            {
                int lo = 0;
                int hi = candidates.Count;
                while (lo < hi)
                {
                    int mid = lo + ((hi - lo) >> 1);
                    if (candidates[mid].Offset < begin) lo = mid + 1;
                    else hi = mid;
                }
                return lo;
            }

            private static Plan Replace(Plan original, TlvHeader h, ulong size, Plan suffix, ulong end)
            {
                var views = new List<View>();
                foreach (var v in original.Views)
                {
                    if (v.Begin >= h.Offset) break;
                    views.Add(new View(v.Chain, v.Begin, Math.Min(v.End, h.Offset)));
                    if (v.End >= h.Offset) break;
                }
                var extended = new Chain();
                extended.Append(h.Offset, size);
                views.Add(new View(extended, h.Offset, h.Offset + 6 + size));
                views.AddRange(suffix.Views);
                return new Plan(views, end);
            }

            private Plan? Solve(ulong start, ulong end, int depth)
            {
                var normal = Ordinary(start, end);
                if (normal.Stop == end) return normal;
                var key = (start, end, depth);
                if (_decisions.TryGetValue(key, out var cached)) return cached;
                foreach (var v in normal.Views)
                {
                    Index(v.Chain);
                    var candidates = v.Chain.Candidates;
                    for (int idx = LowerBound(candidates, v.Begin); idx < candidates.Count && candidates[idx].Offset < v.End; idx++)
                    {
                        var h = candidates[idx];
                        ulong available = end - h.Offset - 6;
                        if (available - h.Size < TlvWrap) break;
                        ulong n = h.Size + TlvWrap;
                        while (n <= available)
                        {
                            if (depth >= MaxDepth || _attempts == 0)
                            {
                                throw new SkpParseException(
                                    "TLV length recovery exceeded its depth or attempt limit",
                                    stage: "tlv_walk",
                                    offset: h.Offset <= (ulong)long.MaxValue ? (long?)h.Offset : null);
                            }
                            _attempts--;
                            ulong next = h.Offset + 6 + n;
                            var suffix = Solve(next, end, depth + 1);
                            if (suffix != null && Solve(h.Offset + 6, next, depth + 1) != null)
                            {
                                var result = Replace(normal, h, n, suffix, end);
                                _decisions[key] = result;
                                return result;
                            }
                            if (available - n < TlvWrap) break;
                            n += TlvWrap;
                        }
                    }
                }
                _decisions[key] = null;
                return null;
            }

            private static List<TlvHeader> Materialize(Plan plan)
            {
                var outHeaders = new List<TlvHeader>();
                foreach (var v in plan.Views)
                {
                    int i = v.Chain.RunAt(v.Begin);
                    while (i < v.Chain.Runs.Count && v.Chain.Runs[i].Offset < v.End)
                    {
                        var r = v.Chain.Runs[i++];
                        ulong step = r.Step;
                        ulong rEnd = r.End;
                        ulong stop = Math.Min(v.End, rEnd);
                        ulong k0 = v.Begin <= r.Offset ? 0UL : (v.Begin - r.Offset) / step;
                        ulong k1 = stop <= r.Offset ? 0UL : (stop - r.Offset) / step;
                        for (ulong k = k0; k < k1; k++)
                        {
                            outHeaders.Add(new TlvHeader(r.Offset + k * step, r.Size));
                        }
                    }
                }
                return outHeaders;
            }

            public List<TlvHeader> Headers(ulong start, ulong end)
            {
                var plan = Solve(start, end, 0);
                if (plan != null) return Materialize(plan);
                return Materialize(Ordinary(start, end));
            }
        }

        public static List<TlvHeader>? WrappedModelRecords(
            ulong end,
            Func<ulong, ushort> tag,
            Func<ulong, uint> length)
        {
            if (end < 6 || end - 6 < TlvWrap || tag(0) != 0x01f4 || length(0) != unchecked((uint)(end - 6)))
            {
                return null;
            }

            ulong storedRootEnd = 6 + (ulong)length(0);
            ulong storedParentEnd = storedRootEnd;
            bool contradictsStoredLengths = false;
            ulong pos = 6;

            ulong CheckedLength()
            {
                ulong size = length(pos);
                bool Crosses(ulong limit) => pos <= limit && limit - pos >= 6 && size > limit - pos - 6;
                if (Crosses(storedRootEnd) || Crosses(storedParentEnd))
                {
                    contradictsStoredLengths = true;
                }
                return size;
            }

            bool Has(ushort expected) => end - pos >= 6 && tag(pos) == expected;

            bool Advance()
            {
                if (end - pos < 6) return false;
                ulong size = CheckedLength();
                if (size > end - pos - 6) return false;
                pos += 6 + size;
                return true;
            }

            while (!Has(0x01f9))
            {
                if (!Advance()) return null;
            }
            ulong catalog = pos;
            ulong[] wrappers = new ulong[3];
            ushort[] path = { 0x01f9, 0x1770, 0x1771 };
            for (int i = 0; i < wrappers.Length; i++)
            {
                if (!Has(path[i])) return null;
                wrappers[i] = pos;
                ulong size = CheckedLength();
                if (pos <= storedParentEnd && storedParentEnd - pos >= 6 && size <= storedParentEnd - pos - 6)
                {
                    storedParentEnd = pos + 6 + size;
                }
                pos += 6;
                if (Has(0x0063) && !Advance()) return null;
            }
            ulong definitions = pos;
            while (Has(0x157c))
            {
                if (!Advance()) return null;
            }
            ulong entities = pos;
            if (definitions == entities || !Has(0x01f6)) return null;
            foreach (ulong offset in wrappers)
            {
                if (length(offset) != unchecked((uint)(entities - offset - 6))) return null;
            }
            storedParentEnd = storedRootEnd;
            while (pos < end)
            {
                if (!Advance()) return null;
            }
            if (!contradictsStoredLengths) return null;

            var records = new List<TlvHeader>();
            void Append(ulong begin, ulong stop)
            {
                for (ulong p = begin; p < stop;)
                {
                    ulong size = length(p);
                    records.Add(new TlvHeader(p, size));
                    p += 6 + size;
                }
            }
            Append(6, catalog);
            Append(definitions, entities);
            Append(entities, end);
            return records;
        }

        public static List<TlvHeader> TlvHeaders(
            ulong start,
            ulong end,
            Func<ulong, uint> length,
            Func<ulong, bool> container)
        {
            if (start > end) throw new ArgumentOutOfRangeException(nameof(end), "invalid TLV range");
            if (end - start >= TlvWrap)
            {
                return new HeaderResolver(length, container, end).Headers(start, end);
            }
            var outHeaders = new List<TlvHeader>();
            ulong p = start;
            while (end - p >= 6)
            {
                ulong n = length(p);
                if (n > end - p - 6) break;
                outHeaders.Add(new TlvHeader(p, n));
                p += 6 + n;
            }
            return outHeaders;
        }

        public static List<TlvHeader> TlvHeaders(
            ChunkedBuffer data,
            long start,
            long end,
            HashSet<string>? containerTags = null)
        {
            if (start < 0 || start > end || end > data.Length)
            {
                throw new ArgumentOutOfRangeException(nameof(end), "invalid TLV range");
            }
            containerTags ??= ContainerTags;
            return TlvHeaders(
                (ulong)start,
                (ulong)end,
                p => data.ReadU32((long)p + 2),
                p => containerTags.Contains(TagHex(data, (long)p)));
        }

        public static List<TlvNode> ParseRecursive(ChunkedBuffer data, long start, long end, HashSet<string>? containerTags = null, int depth = 0)
        {
            if (start < 0 || start > end || end > data.Length)
            {
                throw new ArgumentOutOfRangeException(nameof(end), "invalid TLV range");
            }
            containerTags ??= ContainerTags;
            var elements = new List<TlvNode>();
            foreach (var header in TlvHeaders(data, start, end, containerTags))
            {
                long pos = (long)header.Offset;
                long size = (long)header.Size;
                string tagHex = TagHex(data, pos);
                bool isContainer = containerTags.Contains(tagHex);
                List<TlvNode> children = new List<TlvNode>();
                if (isContainer && size > 0)
                {
                    if (depth >= MaxDepth)
                    {
                        throw new SkpParseException(
                            $"TLV records nest deeper than {MaxDepth} levels",
                            stage: "tlv_walk", tag: tagHex, offset: pos);
                    }
                    children = ParseRecursive(data, pos + 6, pos + 6 + size, containerTags, depth + 1);
                }
                byte[] payload = Array.Empty<byte>();
                if (children.Count == 0 && size > 0)
                {
                    payload = data.Slice(pos + 6, (int)size);
                }
                elements.Add(new TlvNode
                {
                    Offset = pos,
                    Tag = tagHex,
                    Size = size,
                    Children = children,
                    Payload = payload,
                });
            }
            return elements;
        }

        /// <summary>Walk a raw payload as a flat TLV sequence (no container-tag
        /// awareness); returns [(tag, body)] pairs.</summary>
        public static List<(string Tag, byte[] Body)> ParseFlat(byte[] payload)
        {
            var result = new List<(string, byte[])>();
            int pos = 0;
            while (pos <= payload.Length - 6)
            {
                string tag = TagHex(payload, pos);
                uint size = ReadU32(payload, pos + 2);
                if (pos + 6 + size > payload.Length) break;
                byte[] body = new byte[size];
                Array.Copy(payload, pos + 6, body, 0, (int)size);
                result.Add((tag, body));
                pos += 6 + (int)size;
            }
            return result;
        }

        public static byte[]? FindFlat(List<(string Tag, byte[] Body)> seq, string tag)
        {
            foreach (var (t, body) in seq)
            {
                if (t == tag) return body;
            }
            return null;
        }

        /// <summary>Scan [start, end) for direct-child (tag, offset, size)
        /// headers only, without recursing into any container - O(sibling
        /// count), not O(total node count). Used by IterTopLevelLazy to
        /// locate top-level records one at a time.</summary>
        private static List<(string Tag, long Offset, long Size)> FlatHeaders(ChunkedBuffer data, long start, long end, HashSet<string> containerTags)
        {
            if ((ulong)(end - start) >= TlvWrap)
            {
                var recovered = TlvHeaders(data, start, end, containerTags);
                var result = new List<(string, long, long)>(recovered.Count);
                foreach (var h in recovered)
                {
                    result.Add((TagHex(data, (long)h.Offset), (long)h.Offset, (long)h.Size));
                }
                return result;
            }
            var headers = new List<(string, long, long)>();
            long pos = start;
            while (pos <= end - 6)
            {
                string tagHex = TagHex(data, pos);
                uint size = data.ReadU32(pos + 2);
                if (pos + 6 + size > end) break;
                headers.Add((tagHex, pos, size));
                pos += 6 + size;
            }
            return headers;
        }

        /// <summary>Throw if a top-level record declares more bytes than the
        /// data holds. FlatHeaders stops quietly at such a record, so a
        /// model.dat cut short anywhere inside its single F401 record would
        /// parse as a model with no geometry and no error. Real files end
        /// exactly where their last top-level record does. Records nested
        /// inside it are deliberately left lenient: in real files some
        /// container payloads end with bytes that are not records, and a
        /// strict check there would reject valid models.</summary>
        private static void RequireCompleteTopLevel(
            ChunkedBuffer data,
            long start,
            long end,
            List<(string Tag, long Offset, long Size)> headers)
        {
            long pos = headers.Count == 0
                ? start
                : headers[headers.Count - 1].Offset + 6 + headers[headers.Count - 1].Size;
            if (end - pos >= 6)
            {
                uint size = data.ReadU32(pos + 2);
                throw new SkpParseException(
                    $"model.dat is truncated: the top-level record declares {size} bytes but only {end - pos - 6} remain",
                    stage: "tlv_walk", tag: TagHex(data, pos), offset: pos);
            }
        }

        private static List<(string Tag, long Offset, long Size)>? UnwrapDefinitionsContainer(
            ChunkedBuffer data,
            long offset,
            long size,
            HashSet<string> containerTags)
        {
            var level = FlatHeaders(data, offset + 6, offset + 6 + size, containerTags);
            foreach (string expected in new[] { "7017", "7117" })
            {
                int found = -1;
                for (int i = 0; i < level.Count; i++)
                {
                    if (level[i].Tag == expected)
                    {
                        found = i;
                        break;
                    }
                }
                if (found < 0) return null;
                var c = level[found];
                level = FlatHeaders(data, c.Offset + 6, c.Offset + 6 + c.Size, containerTags);
            }
            var defs = new List<(string, long, long)>();
            foreach (var h in level)
            {
                if (h.Tag == "7C15") defs.Add(h);
            }
            return defs.Count > 0 ? defs : null;
        }

        /// <summary>One top-level TLV record yielded by IterTopLevelLazy,
        /// with its position among the file's top-level siblings.</summary>
        public readonly struct TopLevelRecord
        {
            public readonly int Index;
            public readonly int Total;
            public readonly TlvNode Node;
            public TopLevelRecord(int index, int total, TlvNode node)
            {
                Index = index;
                Total = total;
                Node = node;
            }
        }

        /// <summary>Yield each top-level TLV record's fully-recursed node one
        /// at a time, transparently unwrapping a lone "F401" wrapper -
        /// without ever materializing more than one top-level subtree
        /// simultaneously. Total (the top-level sibling count) comes for free
        /// from the same cheap header scan that drives the loop, so callers
        /// can report "N of total" progress with no extra pass over the
        /// file. Each yielded node is safe to discard (drop all references)
        /// once the caller is done with it, before the next one is produced
        /// - that's what keeps peak memory bounded by the size of the single
        /// largest top-level record instead of the whole file (real
        /// production files can have 100k+ separate definitions).</summary>
        public static IEnumerable<TopLevelRecord> IterTopLevelLazy(ChunkedBuffer data, long start, long end, HashSet<string>? containerTags = null)
        {
            containerTags ??= ContainerTags;

            List<(string Tag, long Offset, long Size)> headers;
            List<TlvHeader>? wrapped = null;
            if (start == 0 && end == data.Length && containerTags.Contains("F401") && end >= 6 && (ulong)(end - 6) >= TlvWrap)
            {
                wrapped = WrappedModelRecords(
                    (ulong)end,
                    p => data.ReadU16((long)p),
                    p => data.ReadU32((long)p + 2));
            }
            if (wrapped != null)
            {
                headers = new List<(string, long, long)>(wrapped.Count);
                foreach (var h in wrapped)
                {
                    headers.Add((TagHex(data, (long)h.Offset), (long)h.Offset, (long)h.Size));
                }
            }
            else
            {
                headers = FlatHeaders(data, start, end, containerTags);
                RequireCompleteTopLevel(data, start, end, headers);
                if (headers.Count == 1 && headers[0].Tag == "F401")
                {
                    var (_, f401Offset, f401Size) = headers[0];
                    headers = FlatHeaders(data, f401Offset + 6, f401Offset + 6 + f401Size, containerTags);
                }
                if (containerTags.Contains("F901"))
                {
                    var expanded = new List<(string, long, long)>(headers.Count);
                    foreach (var h in headers)
                    {
                        if (h.Tag == "F901")
                        {
                            var defs = UnwrapDefinitionsContainer(data, h.Offset, h.Size, containerTags);
                            if (defs != null)
                            {
                                expanded.AddRange(defs);
                                continue;
                            }
                        }
                        expanded.Add(h);
                    }
                    headers = expanded;
                }
            }

            int total = headers.Count;
            int index = 0;
            foreach (var (tag, offset, size) in headers)
            {
                long recordEnd = offset + 6 + size;
                var nodes = ParseRecursive(data, offset, recordEnd, containerTags);
                if (nodes.Count > 0)
                {
                    yield return new TopLevelRecord(index, total, nodes[0]);
                }
                index++;
            }
        }
    }
}
