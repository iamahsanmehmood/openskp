using System;
using System.Collections.Generic;
using System.Linq;
using Xunit;

namespace OpenSkp.Tests
{
    public class WrappedTlvTests
    {
        private const ulong Wrap = 1UL << 32;

        private sealed class SparseRecords
        {
            public readonly Dictionary<ulong, uint> Lengths = new Dictionary<ulong, uint>();
            public readonly HashSet<ulong> Containers = new HashSet<ulong>();

            public List<TlvHeader> Scan(ulong end)
            {
                return Tlv.TlvHeaders(
                    0,
                    end,
                    p => Lengths.TryGetValue(p, out uint v) ? v : uint.MaxValue,
                    p => Containers.Contains(p));
            }
        }

        private static List<TlvHeader>? ReferencePartition(SparseRecords d, ulong start, ulong end)
        {
            var ordinary = new List<TlvHeader>();
            ulong p = start;
            while (end - p >= 6)
            {
                ulong n = d.Lengths.TryGetValue(p, out uint v) ? v : uint.MaxValue;
                if (n > end - p - 6) break;
                ordinary.Add(new TlvHeader(p, n));
                p += 6 + n;
            }
            if (p == end) return ordinary;
            for (int i = 0; i < ordinary.Count; i++)
            {
                var h = ordinary[i];
                if (!d.Containers.Contains(h.Offset)) continue;
                ulong available = end - h.Offset - 6;
                if (available - h.Size < Wrap) continue;
                ulong n = h.Size + Wrap;
                while (n <= available)
                {
                    ulong next = h.Offset + 6 + n;
                    var suffix = ReferencePartition(d, next, end);
                    if (suffix != null && ReferencePartition(d, h.Offset + 6, next) != null)
                    {
                        var result = ordinary.Take(i).ToList();
                        result.Add(new TlvHeader(h.Offset, n));
                        result.AddRange(suffix);
                        return result;
                    }
                    if (available - n < Wrap) break;
                    n += Wrap;
                }
            }
            return null;
        }

        [Fact]
        public void ModelCatalogDisambiguatesTwoExactPartitions()
        {
            ulong entities = Wrap + 48;
            ulong end = entities + 6;
            var d = new SparseRecords();
            d.Lengths[0] = 48;
            d.Lengths[6] = 36;
            d.Lengths[12] = 30;
            d.Lengths[18] = 24;
            d.Lengths[24] = uint.MaxValue;
            d.Lengths[Wrap + 29] = 13;
            d.Lengths[entities] = 0;
            d.Lengths[54] = uint.MaxValue - 5;
            d.Containers.UnionWith(new ulong[] { 0, 6, 12, 18 });

            var ordinary = d.Scan(end);
            Assert.Equal(2, ordinary.Count);
            Assert.Equal(54UL, ordinary[ordinary.Count - 1].Offset);

            var tags = new Dictionary<ulong, ushort>
            {
                [0] = 0x01f4,
                [6] = 0x01f9,
                [12] = 0x1770,
                [18] = 0x1771,
                [24] = 0x157c,
                [Wrap + 29] = 0x157c,
                [entities] = 0x01f6,
            };

            List<TlvHeader>? Read() => Tlv.WrappedModelRecords(end, p => tags[p], p => d.Lengths[p]);

            var records = Read();
            Assert.NotNull(records);
            Assert.Equal(new ulong[] { 24, Wrap + 29, entities }, records!.Select(r => r.Offset).ToArray());

            d.Lengths[12]++;
            Assert.Null(Read());
            d.Lengths[12]--;
            tags[entities] = 0x157e;
            Assert.Null(Read());
        }

        [Fact]
        public void SmallCatalogInsideWrappedModel()
        {
            ulong entities = Wrap - 16;
            ulong end = entities + 70;
            var d = new SparseRecords();
            d.Lengths[0] = 48;
            d.Lengths[6] = unchecked((uint)(entities - 12));
            d.Lengths[12] = unchecked((uint)(entities - 18));
            d.Lengths[18] = unchecked((uint)(entities - 24));
            d.Lengths[24] = unchecked((uint)(entities - 30));
            d.Lengths[entities] = 64;
            d.Lengths[54] = uint.MaxValue - 5;
            d.Containers.UnionWith(new ulong[] { 0, 6, 12, 18 });

            var tags = new Dictionary<ulong, ushort>
            {
                [0] = 0x01f4,
                [6] = 0x01f9,
                [12] = 0x1770,
                [18] = 0x1771,
                [24] = 0x157c,
                [entities] = 0x01f6,
            };

            var ordinary = d.Scan(end);
            Assert.Equal(2, ordinary.Count);
            Assert.Equal(54UL, ordinary[1].Offset);

            var records = Tlv.WrappedModelRecords(end, p => tags[p], p => d.Lengths[p]);
            Assert.NotNull(records);
            Assert.Equal(new ulong[] { 24, entities }, records!.Select(r => r.Offset).ToArray());
        }

        [Fact]
        public void PreservesCompleteShortModelFollowedByTopLevelRecords()
        {
            ulong end = Wrap + 54;
            var d = new SparseRecords();
            d.Lengths[0] = 48;
            d.Lengths[6] = 42;
            d.Lengths[12] = 36;
            d.Lengths[18] = 30;
            d.Lengths[24] = 24;
            d.Lengths[54] = uint.MaxValue - 5;
            d.Containers.UnionWith(new ulong[] { 0, 6, 12, 18 });

            var tags = new Dictionary<ulong, ushort>
            {
                [0] = 0x01f4,
                [6] = 0x01f9,
                [12] = 0x1770,
                [18] = 0x1771,
                [24] = 0x157c,
                [54] = 0x01f6,
            };

            var ordinary = d.Scan(end);
            Assert.Equal(2, ordinary.Count);
            Assert.Equal(54UL, ordinary[1].Offset);
            Assert.Null(Tlv.WrappedModelRecords(end, p => tags[p], p => d.Lengths[p]));
        }

        [Fact]
        public void ExtraTopLevelDefinitionsDoNotExtendACompleteCatalog()
        {
            ulong entities = Wrap + 48;
            ulong end = entities + 6;
            var d = new SparseRecords();
            d.Lengths[0] = 48;
            d.Lengths[6] = 36;
            d.Lengths[12] = 30;
            d.Lengths[18] = 24;
            d.Lengths[24] = 18;
            d.Lengths[48] = 0;
            d.Lengths[54] = uint.MaxValue - 11;
            d.Lengths[entities] = 0;
            d.Containers.UnionWith(new ulong[] { 0, 6, 12, 18 });

            var tags = new Dictionary<ulong, ushort>
            {
                [0] = 0x01f4,
                [6] = 0x01f9,
                [12] = 0x1770,
                [18] = 0x1771,
                [24] = 0x157c,
                [48] = 0x157c,
                [54] = 0x157c,
                [entities] = 0x01f6,
            };

            var ordinary = d.Scan(end);
            Assert.Equal(3, ordinary.Count);
            Assert.Equal(54UL, ordinary[1].Offset);
            Assert.Null(Tlv.WrappedModelRecords(end, p => tags[p], p => d.Lengths[p]));
        }

        [Fact]
        public void NestedContainersAndFollowingSibling()
        {
            var d = new SparseRecords();
            for (ulong p = 0; p < 24; p += 6)
            {
                d.Lengths[p] = unchecked((uint)(Wrap + 30 - p - 6));
                d.Containers.Add(p);
            }
            d.Lengths[24] = uint.MaxValue;
            d.Lengths[Wrap + 29] = 1;
            for (ulong p = 0; p < 24; p += 6) d.Lengths[p] += 6;
            d.Lengths[Wrap + 36] = 0;

            var records = d.Scan(Wrap + 42);
            Assert.Equal(2, records.Count);
            Assert.Equal(Wrap + 30, records[0].Size);
            Assert.Equal(Wrap + 36, records[1].Offset);
        }

        [Fact]
        public void MultipleWraps()
        {
            var d = new SparseRecords();
            d.Lengths[0] = 10;
            d.Lengths[6] = uint.MaxValue;
            d.Lengths[Wrap + 11] = uint.MaxValue;
            d.Containers.Add(0);

            var records = d.Scan(2 * Wrap + 16);
            Assert.Single(records);
            Assert.Equal(2 * Wrap + 10, records[0].Size);
        }

        [Fact]
        public void RejectsInvalidChildrenAndSuffixAndOpaqueLeaves()
        {
            var d = new SparseRecords();
            d.Lengths[0] = 5;
            d.Lengths[6] = uint.MaxValue;
            d.Containers.Add(0);

            var records = d.Scan(Wrap + 11);
            Assert.Single(records);
            Assert.Equal(Wrap + 5, records[0].Size);

            d.Lengths[6] = uint.MaxValue - 1;
            records = d.Scan(Wrap + 11);
            Assert.Equal(5UL, records[0].Size);

            d.Lengths[6] = uint.MaxValue;
            records = d.Scan(Wrap + 12);
            Assert.Equal(5UL, records[0].Size);

            d.Containers.Clear();
            records = d.Scan(Wrap + 11);
            Assert.Equal(5UL, records[0].Size);
        }

        [Fact]
        public void KeepsAnExactLargePartitionAndNonzeroStart()
        {
            var d = new SparseRecords();
            d.Lengths[0] = uint.MaxValue;
            d.Lengths[Wrap + 5] = 0;
            d.Containers.Add(0);

            var records = d.Scan(Wrap + 11);
            Assert.Equal(2, records.Count);
            Assert.Equal((ulong)uint.MaxValue, records[0].Size);
            Assert.Equal(Wrap + 5, records[1].Offset);

            var nested = Tlv.TlvHeaders(
                7,
                Wrap + 18,
                p => p == 7 ? 5u : uint.MaxValue,
                p => p == 7);
            Assert.Single(nested);
            Assert.Equal(7UL, nested[0].Offset);
            Assert.Equal(Wrap + 5, nested[0].Size);
        }

        [Fact]
        public void ZeroFilledPayloadDoesNotExhaustRecovery()
        {
            const ulong paddingHeaders = 1000005;
            foreach (bool nested in new[] { false, true })
            {
                ulong reads = 0;
                ulong leaf = nested ? 12UL : 6UL;
                var records = Tlv.TlvHeaders(
                    0,
                    Wrap + leaf + 5,
                    p =>
                    {
                        reads++;
                        if (p == 0) return (uint)(leaf - 1);
                        if (nested && p == 6) return 5;
                        if (p == leaf) return uint.MaxValue;
                        if (p >= leaf + 5 && p < leaf + 5 + 6 * paddingHeaders) return 0;
                        return uint.MaxValue;
                    },
                    p => p == 0 || (nested && p == 6));
                Assert.Single(records);
                Assert.Equal(Wrap + leaf - 1, records[0].Size);
                Assert.True(reads < (nested ? 2UL : 1UL) * paddingHeaders + 20);
            }
        }

        [Fact]
        public void ReportsAttemptExhaustionInsteadOfTruncation()
        {
            ulong reads = 0;
            var ex = Assert.Throws<SkpParseException>(() =>
                Tlv.TlvHeaders(
                    0,
                    Wrap + 1801,
                    p =>
                    {
                        reads++;
                        return p < 1800 ? 0u : uint.MaxValue;
                    },
                    _ => true));
            Assert.Contains("recovery exceeded", ex.Message);
            Assert.True(reads < 1000);
        }

        [Fact]
        public void ReusesSuccessfulAndFailedSuffixesAcrossParentEnds()
        {
            foreach (ulong count in new[] { 100000UL, 1000000UL })
            {
                for (ulong trailing = 0; trailing < 6; trailing++)
                {
                    foreach (ulong start in new[] { 0UL, 7UL })
                    {
                        ulong reads = 0;
                        var records = Tlv.TlvHeaders(
                            start,
                            start + Wrap + 6 * count + trailing,
                            p =>
                            {
                                reads++;
                                ulong rel = p - start;
                                if (rel < 1536 || rel >= Wrap) return 0u;
                                return uint.MaxValue;
                            },
                            p => p - start < 1536);
                        Assert.Equal((int)(count + (trailing == 5 ? 1UL : 0UL)), records.Count);
                        Assert.Equal(0UL, records[0].Size);
                        Assert.Equal((ulong)uint.MaxValue, records[256].Size);
                        Assert.True(reads < 2 * count + 4096);
                    }
                }
            }
        }

        [Fact]
        public void ReusesLongChildrenAsWellAsLongSuffixes()
        {
            const ulong count = 100000;
            ulong reads = 0;
            var records = Tlv.TlvHeaders(
                0,
                Wrap + 12 * count + 1542,
                p =>
                {
                    reads++;
                    if (p < 6 * count + 1536 || p >= Wrap) return 0u;
                    return uint.MaxValue;
                },
                p => p < 1536);
            Assert.NotEmpty(records);
            Assert.Equal(0UL, records[0].Size);
            Assert.True(reads < 4 * count + 4096);
        }

        [Fact]
        public void RecoveryNearSizeTMaximum()
        {
            ulong end = ulong.MaxValue;
            ulong start = end - Wrap - 11;
            var records = Tlv.TlvHeaders(
                start,
                end,
                p => p == start ? 5u : uint.MaxValue,
                p => p == start);
            Assert.Single(records);
            Assert.Equal(start, records[0].Offset);
            Assert.Equal(Wrap + 5, records[0].Size);
        }

        [Fact]
        public void RecoveryDepthBoundaryIsExplicit()
        {
            foreach (ulong depth in new[] { 64UL, 65UL })
            {
                ulong end = Wrap + 6 * depth + 5;
                List<TlvHeader> Scan() => Tlv.TlvHeaders(
                    0,
                    end,
                    p => (p < 6 * depth && p % 6 == 0) ? unchecked((uint)(end - p - 6)) : uint.MaxValue,
                    p => p < 6 * depth && p % 6 == 0);
                if (depth == 64)
                {
                    var records = Scan();
                    Assert.Single(records);
                    Assert.Equal(end - 6, records[0].Size);
                }
                else
                {
                    Assert.Throws<SkpParseException>((Func<List<TlvHeader>>)Scan);
                }
            }
        }

        [Fact]
        public void MatchesUncachedReferenceAcrossNestedAndCorruptRanges()
        {
            for (ulong depth = 1; depth <= 3; depth++)
            {
                for (ulong wraps = 1; wraps <= 3; wraps++)
                {
                    foreach (bool sibling in new[] { false, true })
                    {
                        foreach (int corruption in new[] { -1, 0, 1 })
                        {
                            var d = new SparseRecords();
                            ulong p = 6 * depth;
                            for (ulong i = 0; i < wraps; i++)
                            {
                                d.Lengths[p] = uint.MaxValue;
                                p += Wrap + 5;
                            }
                            d.Lengths[p] = 7;
                            ulong innerEnd = p + 13;
                            for (ulong i = 0; i < depth; i++)
                            {
                                d.Lengths[6 * i] = unchecked((uint)(innerEnd - 6 * i - 6));
                                d.Containers.Add(6 * i);
                            }
                            if (sibling) d.Lengths[innerEnd] = 3;
                            d.Lengths[p] = (uint)(7 + corruption);
                            ulong end = innerEnd + (sibling ? 9UL : 0UL);
                            var expected = ReferencePartition(d, 0, end);
                            if (expected == null)
                            {
                                expected = new List<TlvHeader>();
                                for (ulong q = 0; end - q >= 6;)
                                {
                                    ulong n = d.Lengths.TryGetValue(q, out uint v) ? v : uint.MaxValue;
                                    if (n > end - q - 6) break;
                                    expected.Add(new TlvHeader(q, n));
                                    q += 6 + n;
                                }
                            }
                            var actual = d.Scan(end);
                            Assert.Equal(expected, actual);
                        }
                    }
                }
            }
        }

        [Fact]
        public void CachedFailureDoesNotHideAnExpandableContainer()
        {
            var d = new SparseRecords();
            d.Lengths[0] = 11;
            d.Lengths[6] = 5;
            d.Lengths[12] = uint.MaxValue;
            d.Containers.UnionWith(new ulong[] { 0, 6 });

            var records = d.Scan(Wrap + 17);
            Assert.Single(records);
            Assert.Equal(Wrap + 11, records[0].Size);
        }

        [Fact]
        public void PreservesExactAndSmallLenientRanges()
        {
            var d = new SparseRecords();
            d.Lengths[0] = 3;
            d.Lengths[9] = 0;
            d.Containers.Add(0);

            Assert.Equal(2, d.Scan(15).Count);
            Assert.Equal(2, d.Scan(16).Count);
            Assert.Empty(d.Scan(8));
            Assert.Empty(d.Scan(0));

            byte[] bytes = { 0xF4, 0x01, 6, 0, 0, 0, 0xF6, 0x01, 0, 0, 0, 0 };
            var buf = ChunkedBuffer.FromArray(bytes);
            var nodes = Tlv.ParseRecursive(buf, 0, buf.Length);
            Assert.Single(nodes);
            Assert.Single(nodes[0].Children);
            Assert.Equal("F601", nodes[0].Children[0].Tag);
            Assert.Throws<ArgumentOutOfRangeException>(() => Tlv.TlvHeaders(buf, 0, buf.Length + 1));
            Assert.Single(Tlv.IterTopLevelLazy(buf, 0, buf.Length).ToList());
        }
    }
}
