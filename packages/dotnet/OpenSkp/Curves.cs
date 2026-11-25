using System;
using System.Collections.Generic;

namespace OpenSkp
{
    /// <summary>One chained run of loose edges: its edges in walk order, the
    /// vertex ids along it, and whether it returns to its first vertex (which
    /// is then NOT repeated at the end).</summary>
    public sealed class LooseEdgeRun
    {
        /// <summary>Ordered entity IDs of the edges forming this run.</summary>
        public List<long> EdgeIds { get; set; } = new List<long>();
        /// <summary>Ordered vertex IDs visited along this run (the closing vertex is not repeated when <see cref="Closed"/> is <c>true</c>).</summary>
        public List<long> VertexIds { get; set; } = new List<long>();
        /// <summary>Whether the run's last edge reconnects to its starting vertex.</summary>
        public bool Closed { get; set; }
    }

    /// <summary>An analytic arc recovered for a loose-edge run: points in the
    /// run's own frame (metres, glTF Y-up) plus 0-based (Start, Mid, End)
    /// index triples into them, ready for IFC4 IfcArcIndex.</summary>
    public sealed class CurveArc
    {
        /// <summary>Sampled 3D points (start, midpoints, and end) in metres on glTF's Y-up axes.</summary>
        public List<(double X, double Y, double Z)> Points { get; set; } = new List<(double X, double Y, double Z)>();
        /// <summary>Zero-based <c>(Start, Mid, End)</c> point-index triples describing each circular arc segment.</summary>
        public List<(int Start, int Mid, int End)> Segments { get; set; } = new List<(int Start, int Mid, int End)>();
    }

    internal sealed class PreparedCurveRun
    {
        public List<long> EdgeIds { get; set; } = new List<long>();
        public List<(double X, double Y, double Z)> LocalVerts { get; set; } = new List<(double X, double Y, double Z)>();
        public bool Closed { get; set; }
        public string? ExplicitLayer { get; set; }
    }

    internal sealed class PreparedDefCurves
    {
        public List<PreparedCurveRun> Runs { get; set; } = new List<PreparedCurveRun>();
        public Dictionary<long, int>? CurveEdgeCounts { get; set; }
    }

    internal static class Curves
    {
        public const double InchesToM = 0.0254;

        public static double Round6(double x)
        {
            if (double.IsNaN(x) || double.IsInfinity(x) || Math.Abs(x) >= 8589934592.0 || x == 0.0)
            {
                return x;
            }
            double absScaled = Math.Abs(x * 1e6);
            if (absScaled < 4.5e12)
            {
                double fl = Math.Floor(absScaled);
                double frac = absScaled - fl;
                if (Math.Abs(frac - 0.5) > 1e-3)
                {
                    double fastRes = (frac > 0.5 ? fl + 1.0 : fl) / 1e6;
                    return BitConverter.DoubleToInt64Bits(x) < 0 ? -fastRes : fastRes;
                }
            }

            ulong bits = (ulong)BitConverter.DoubleToInt64Bits(Math.Abs(x));
            int rawExp = (int)((bits >> 52) & 0x7FFUL);
            if (rawExp == 0)
            {
                return BitConverter.DoubleToInt64Bits(x) < 0 ? -0.0 : 0.0;
            }
            ulong mant = (1UL << 52) | (bits & 0xFFFFFFFFFFFFFUL);
            int shift = 1069 - rawExp;
            if (shift >= 68)
            {
                return BitConverter.DoubleToInt64Bits(x) < 0 ? -0.0 : 0.0;
            }

            ulong lo1 = (mant & 0xFFFFFFFFUL) * 15625UL;
            ulong hi1 = (mant >> 32) * 15625UL;
            ulong lo = unchecked(lo1 + (hi1 << 32));
            ulong hi = (hi1 >> 32) + (lo < lo1 ? 1UL : 0UL);

            ulong k;
            ulong halfBit;
            bool restNonZero;
            if (shift >= 64)
            {
                int s = shift - 64;
                k = hi >> s;
                if (s == 0)
                {
                    halfBit = (lo >> 63) & 1UL;
                    restNonZero = (lo & 0x7FFFFFFFFFFFFFFFUL) != 0UL;
                }
                else
                {
                    halfBit = (hi >> (s - 1)) & 1UL;
                    restNonZero = ((hi & ((1UL << (s - 1)) - 1UL)) != 0UL) || (lo != 0UL);
                }
            }
            else
            {
                k = (hi << (64 - shift)) | (lo >> shift);
                halfBit = (lo >> (shift - 1)) & 1UL;
                restNonZero = (lo & ((1UL << (shift - 1)) - 1UL)) != 0UL;
            }
            if (halfBit == 1UL && (restNonZero || (k & 1UL) == 1UL))
            {
                k++;
            }
            double res = (double)k / 1e6;
            return BitConverter.DoubleToInt64Bits(x) < 0 ? -res : res;
        }

        public static List<LooseEdgeRun> OrderCurveEdges(
            IReadOnlyDictionary<long, (long V1, long V2)> edges,
            IReadOnlyList<long> eids)
        {
            var remaining = new HashSet<long>(eids);
            var remainingOrder = new List<long>(eids);
            var runs = new List<LooseEdgeRun>();

            while (remaining.Count > 0)
            {
                var adj = new Dictionary<long, List<(long EdgeId, long Neighbor)>>();
                var adjOrder = new List<long>();
                foreach (var e in remainingOrder)
                {
                    if (!remaining.Contains(e) || !edges.TryGetValue(e, out var ends))
                    {
                        continue;
                    }
                    if (!adj.TryGetValue(ends.V1, out var listA))
                    {
                        listA = new List<(long, long)>();
                        adj[ends.V1] = listA;
                        adjOrder.Add(ends.V1);
                    }
                    listA.Add((e, ends.V2));

                    if (!adj.TryGetValue(ends.V2, out var listB))
                    {
                        listB = new List<(long, long)>();
                        adj[ends.V2] = listB;
                        adjOrder.Add(ends.V2);
                    }
                    listB.Add((e, ends.V1));
                }
                if (adjOrder.Count == 0)
                {
                    break;
                }

                long start = adjOrder[0];
                foreach (var v in adjOrder)
                {
                    if (adj[v].Count == 1)
                    {
                        start = v;
                        break;
                    }
                }

                var first = adj[start][0];
                long eid = first.EdgeId;
                long nxt = first.Neighbor;
                remaining.Remove(eid);
                var chainEids = new List<long> { eid };
                var chain = new List<long> { start, nxt };
                bool closed = false;

                while (true)
                {
                    (long EdgeId, long Neighbor)? nextOpt = null;
                    if (adj.TryGetValue(nxt, out var neighbors))
                    {
                        foreach (var item in neighbors)
                        {
                            if (remaining.Contains(item.EdgeId))
                            {
                                nextOpt = item;
                                break;
                            }
                        }
                    }
                    if (!nextOpt.HasValue)
                    {
                        break;
                    }

                    var (e2, v2) = nextOpt.Value;
                    remaining.Remove(e2);
                    chainEids.Add(e2);
                    if (v2 == chain[0])
                    {
                        closed = true;
                        break;
                    }
                    chain.Add(v2);
                    nxt = v2;
                }

                runs.Add(new LooseEdgeRun
                {
                    EdgeIds = chainEids,
                    VertexIds = chain,
                    Closed = closed,
                });
            }

            return runs;
        }

        public static List<LooseEdgeRun> ChainLooseEdges(GeometryBuilder builder)
        {
            if (builder.Edges.Count == 0)
            {
                return new List<LooseEdgeRun>();
            }

            var used = new HashSet<long>();
            foreach (var face in builder.Faces.Values)
            {
                foreach (var loop in face.Loops)
                {
                    foreach (var co in loop)
                    {
                        used.Add(co.EdgeId);
                    }
                }
            }

            var nonNullEdges = new Dictionary<long, (long V1, long V2)>();
            var groupOrder = new List<long>();
            var groups = new Dictionary<long, List<long>>();
            var runs = new List<LooseEdgeRun>();

            foreach (var kv in builder.Edges)
            {
                long eid = kv.Key;
                var (v1, v2) = kv.Value;
                if (used.Contains(eid) || !v1.HasValue || !v2.HasValue || v1.Value == v2.Value)
                {
                    continue;
                }
                nonNullEdges[eid] = (v1.Value, v2.Value);

                if (builder.EdgeCurves.TryGetValue(eid, out var cid) && cid != 0)
                {
                    if (!groups.TryGetValue(cid, out var grp))
                    {
                        grp = new List<long>();
                        groups[cid] = grp;
                        groupOrder.Add(cid);
                    }
                    grp.Add(eid);
                }
                else
                {
                    runs.Add(new LooseEdgeRun
                    {
                        EdgeIds = new List<long> { eid },
                        VertexIds = new List<long> { v1.Value, v2.Value },
                        Closed = false,
                    });
                }
            }

            foreach (var cid in groupOrder)
            {
                runs.AddRange(OrderCurveEdges(nonNullEdges, groups[cid]));
            }

            return runs;
        }

        public static Dictionary<long, int> IndexEdgeCurves(GeometryBuilder builder)
        {
            var counts = new Dictionary<long, int>();
            foreach (var cid in builder.EdgeCurves.Values)
            {
                if (cid != 0)
                {
                    counts[cid] = (counts.TryGetValue(cid, out var c) ? c : 0) + 1;
                }
            }
            return counts;
        }

        private static string? ResolveExplicitRunLayer(
            GeometryBuilder builder,
            Dictionary<long, string> layerIdToName,
            IReadOnlyList<long> edgeIds)
        {
            if (builder.EdgeLayers.Count == 0)
            {
                return null;
            }
            var counts = new Dictionary<long, int>();
            var order = new List<long>();
            foreach (var eid in edgeIds)
            {
                if (builder.EdgeLayers.TryGetValue(eid, out var lid) && lid != 0)
                {
                    if (counts.TryGetValue(lid, out var c))
                    {
                        counts[lid] = c + 1;
                    }
                    else
                    {
                        counts[lid] = 1;
                        order.Add(lid);
                    }
                }
            }
            if (order.Count == 0)
            {
                return null;
            }
            long bestLid = order[0];
            int bestCount = counts[bestLid];
            foreach (var lid in order)
            {
                if (counts[lid] > bestCount)
                {
                    bestLid = lid;
                    bestCount = counts[lid];
                }
            }
            return layerIdToName.TryGetValue(bestLid, out var name) ? name : null;
        }

        public static string ResolveRunLayer(
            GeometryBuilder builder,
            Dictionary<long, string> layerIdToName,
            IReadOnlyList<long> edgeIds,
            string fallback) =>
            ResolveExplicitRunLayer(builder, layerIdToName, edgeIds) ?? fallback;

        public static PreparedDefCurves PrepareDefinitionCurves(
            GeometryBuilder builder,
            Dictionary<long, string> layerIdToName)
        {
            var rawRuns = ChainLooseEdges(builder);
            if (rawRuns.Count == 0)
            {
                return new PreparedDefCurves();
            }

            var runs = new List<PreparedCurveRun>(rawRuns.Count);
            foreach (var run in rawRuns)
            {
                var localVerts = new List<(double X, double Y, double Z)>(run.VertexIds.Count);
                bool complete = true;
                foreach (var vk in run.VertexIds)
                {
                    if (!builder.Vertices.TryGetValue(vk, out var v))
                    {
                        complete = false;
                        break;
                    }
                    localVerts.Add(v);
                }
                if (!complete || localVerts.Count < 2)
                {
                    continue;
                }
                runs.Add(new PreparedCurveRun
                {
                    EdgeIds = run.EdgeIds,
                    LocalVerts = localVerts,
                    Closed = run.Closed,
                    ExplicitLayer = ResolveExplicitRunLayer(builder, layerIdToName, run.EdgeIds),
                });
            }

            if (runs.Count == 0)
            {
                return new PreparedDefCurves();
            }

            return new PreparedDefCurves
            {
                Runs = runs,
                CurveEdgeCounts = builder.ArcCurves.Count > 0 ? IndexEdgeCurves(builder) : null,
            };
        }

        private static double Dist3((double X, double Y, double Z) p, (double X, double Y, double Z) q)
        {
            double dx = p.X - q.X;
            double dy = p.Y - q.Y;
            double dz = p.Z - q.Z;
            return Math.Sqrt(dx * dx + dy * dy + dz * dz);
        }

        public static CurveArc? SolveRunArc(
            GeometryBuilder builder,
            IReadOnlyList<long> eids,
            IReadOnlyList<double>? matrix,
            IReadOnlyList<(double X, double Y, double Z)> pts,
            bool closed = false,
            IReadOnlyDictionary<long, int>? curveEdgeCounts = null)
        {
            if (builder.ArcCurves.Count == 0 || eids.Count == 0)
            {
                return null;
            }
            if (!builder.EdgeCurves.TryGetValue(eids[0], out var cid))
            {
                return null;
            }
            if (!builder.ArcCurves.TryGetValue(cid, out var frame))
            {
                return null;
            }

            int curveEdgeCount = 0;
            if (curveEdgeCounts != null)
            {
                curveEdgeCounts.TryGetValue(cid, out curveEdgeCount);
            }
            else
            {
                foreach (var v in builder.EdgeCurves.Values)
                {
                    if (v == cid)
                    {
                        curveEdgeCount++;
                    }
                }
            }
            if (curveEdgeCount != eids.Count)
            {
                return null;
            }

            double[]? fm = null;
            bool mirrored = false;
            if (matrix != null && matrix.Count >= 12)
            {
                var arr = matrix as double[];
                if (arr == null)
                {
                    arr = new double[matrix.Count];
                    for (int i = 0; i < matrix.Count; i++)
                    {
                        arr[i] = matrix[i];
                    }
                }
                fm = Transforms.FoldScale(arr);
                double det = fm[0] * (fm[4] * fm[8] - fm[5] * fm[7])
                           - fm[1] * (fm[3] * fm[8] - fm[5] * fm[6])
                           + fm[2] * (fm[3] * fm[7] - fm[4] * fm[6]);
                mirrored = det < 0.0;
            }

            (double X, double Y, double Z) XfPoint((double X, double Y, double Z) p)
            {
                if (fm == null)
                {
                    return (p.X * InchesToM, p.Z * InchesToM, -p.Y * InchesToM);
                }
                double qx = fm[0] * p.X + fm[1] * p.Y + fm[2] * p.Z + fm[9];
                double qy = fm[3] * p.X + fm[4] * p.Y + fm[5] * p.Z + fm[10];
                double qz = fm[6] * p.X + fm[7] * p.Y + fm[8] * p.Z + fm[11];
                return (qx * InchesToM, qz * InchesToM, -qy * InchesToM);
            }

            (double X, double Y, double Z) XfVec((double X, double Y, double Z) v)
            {
                double x, y, z;
                if (fm == null)
                {
                    (x, y, z) = v;
                }
                else
                {
                    x = fm[0] * v.X + fm[1] * v.Y + fm[2] * v.Z;
                    y = fm[3] * v.X + fm[4] * v.Y + fm[5] * v.Z;
                    z = fm[6] * v.X + fm[7] * v.Y + fm[8] * v.Z;
                }
                return (x * InchesToM, z * InchesToM, -y * InchesToM);
            }

            var c = XfPoint(frame.Center);
            var xa = XfVec(frame.XAxis);
            var ya = XfVec(frame.YAxis);
            var na = XfVec(frame.Normal);

            double r = Math.Sqrt(xa.X * xa.X + xa.Y * xa.Y + xa.Z * xa.Z);
            if (r <= 0.0)
            {
                return null;
            }

            double nlen = Math.Sqrt(na.X * na.X + na.Y * na.Y + na.Z * na.Z);
            if (nlen <= 0.0)
            {
                return null;
            }
            double nSign = mirrored ? -1.0 : 1.0;
            var nh = (X: nSign * na.X / nlen, Y: nSign * na.Y / nlen, Z: nSign * na.Z / nlen);
            var cross = (
                X: nh.Y * xa.Z - nh.Z * xa.Y,
                Y: nh.Z * xa.X - nh.X * xa.Z,
                Z: nh.X * xa.Y - nh.Y * xa.X);
            if (Dist3(cross, ya) > 1e-6 * r)
            {
                return null;
            }

            double invR = 1.0 / r;
            var xh = (X: xa.X * invR, Y: xa.Y * invR, Z: xa.Z * invR);
            var yh = (X: ya.X * invR, Y: ya.Y * invR, Z: ya.Z * invR);

            double tol = Math.Max(1e-5, 1e-6 * r);
            foreach (var p in pts)
            {
                if (Math.Abs(Dist3(p, c) - r) > tol)
                {
                    return null;
                }
            }

            double Theta((double X, double Y, double Z) p)
            {
                double dx = p.X - c.X;
                double dy = p.Y - c.Y;
                double dz = p.Z - c.Z;
                return Math.Atan2(
                    dx * yh.X + dy * yh.Y + dz * yh.Z,
                    dx * xh.X + dy * xh.Y + dz * xh.Z);
            }

            var thetas = new List<double>(pts.Count);
            foreach (var p in pts)
            {
                thetas.Add(Theta(p));
            }

            var pairs = new List<(double A, double B)>();
            for (int i = 0; i + 1 < thetas.Count; i++)
            {
                pairs.Add((thetas[i], thetas[i + 1]));
            }
            if (closed && thetas.Count > 2)
            {
                pairs.Add((thetas[thetas.Count - 1], thetas[0]));
            }

            double stepLimit = Math.PI * 0.5 * (1.0 + 1e-9);
            var steps = new List<double>();
            double total = 0.0;
            foreach (var (a, b) in pairs)
            {
                double d = b - a;
                while (d > Math.PI)
                {
                    d -= 2.0 * Math.PI;
                }
                while (d < -Math.PI)
                {
                    d += 2.0 * Math.PI;
                }
                if (Math.Abs(d) > stepLimit)
                {
                    return null;
                }
                if (steps.Count > 0 && (d > 0.0) != (steps[0] > 0.0))
                {
                    return null;
                }
                steps.Add(d);
                total += d;
            }
            if (steps.Count == 0 || total == 0.0)
            {
                return null;
            }

            (double X, double Y, double Z) At(double t)
            {
                double ct = Math.Cos(t);
                double st = Math.Sin(t);
                return (
                    c.X + ct * xa.X + st * ya.X,
                    c.Y + ct * xa.Y + st * ya.Y,
                    c.Z + ct * xa.Z + st * ya.Z);
            }

            double t0 = thetas[0];
            if (Math.Abs(total) >= 2.0 * Math.PI - 1e-6 * Math.Max(1.0, Math.Abs(total)))
            {
                return new CurveArc
                {
                    Points = new List<(double X, double Y, double Z)>
                    {
                        At(t0),
                        At(t0 + total * 0.25),
                        At(t0 + total * 0.5),
                        At(t0 + total * 0.75),
                    },
                    Segments = new List<(int Start, int Mid, int End)>
                    {
                        (0, 1, 2),
                        (2, 3, 0),
                    },
                };
            }

            return new CurveArc
            {
                Points = new List<(double X, double Y, double Z)>
                {
                    At(t0),
                    At(t0 + total * 0.5),
                    At(t0 + total),
                },
                Segments = new List<(int Start, int Mid, int End)>
                {
                    (0, 1, 2),
                },
            };
        }
    }
}
