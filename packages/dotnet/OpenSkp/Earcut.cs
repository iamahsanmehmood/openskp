using System;
using System.Collections.Generic;

namespace OpenSkp
{
    /// <summary>Ear-clipping polygon triangulation with hole support, ported
    /// from Mapbox's "earcut" (https://github.com/mapbox/earcut,
    /// ISC License, Copyright (c) 2024 Mapbox) - the same library the
    /// TypeScript port already depends on for TriangulateFace3D. Ported here
    /// (rather than referenced as a package) to keep the .NET build
    /// dependency-free, matching the rest of this port's design.
    ///
    /// Correctly handles concave polygons and polygons with holes, unlike
    /// the plane-fan fallback this class replaces in Triangulator.cs.</summary>
    internal static class Earcut
    {
        private sealed class Node
        {
            public int I;
            public double X, Y;
            public Node? Prev;
            public Node? Next;
            public double Z;
            public Node? PrevZ;
            public Node? NextZ;
            public bool Steiner;

            public Node(int i, double x, double y)
            {
                I = i;
                X = x;
                Y = y;
            }
        }

        /// <summary>Triangulates a (possibly holed) 2D polygon given as a
        /// flat [x0,y0,x1,y1,...] coordinate array, with holeIndices giving
        /// the starting *vertex* (not coordinate) index of each hole ring.
        /// Returns a flat list of vertex indices, 3 per triangle.</summary>
        public static List<int> Triangulate(double[] data, int[]? holeIndices, int dim = 2)
        {
            bool hasHoles = holeIndices != null && holeIndices.Length > 0;
            int outerLen = hasHoles ? holeIndices![0] * dim : data.Length;
            Node? outerNode = LinkedList(data, 0, outerLen, dim, true);
            var triangles = new List<int>();

            if (outerNode == null || outerNode.Next == outerNode.Prev) return triangles;

            double minX = 0, minY = 0, invSize = 0;

            if (hasHoles) outerNode = EliminateHoles(data, holeIndices!, outerNode, dim);

            if (data.Length > 80 * dim)
            {
                minX = data[0];
                minY = data[1];
                double maxX = minX, maxY = minY;

                for (int i = dim; i < outerLen; i += dim)
                {
                    double x = data[i], y = data[i + 1];
                    if (x < minX) minX = x;
                    if (y < minY) minY = y;
                    if (x > maxX) maxX = x;
                    if (y > maxY) maxY = y;
                }

                invSize = Math.Max(maxX - minX, maxY - minY);
                invSize = invSize != 0 ? 32767 / invSize : 0;
            }

            EarcutLinked(outerNode, triangles, dim, minX, minY, invSize, 0);

            return triangles;
        }

        private static Node? LinkedList(double[] data, int start, int end, int dim, bool clockwise)
        {
            Node? last = null;

            if (clockwise == (SignedArea(data, start, end, dim) > 0))
            {
                for (int i = start; i < end; i += dim) last = InsertNode(i / dim, data[i], data[i + 1], last);
            }
            else
            {
                for (int i = end - dim; i >= start; i -= dim) last = InsertNode(i / dim, data[i], data[i + 1], last);
            }

            if (last != null && Equals(last, last.Next!))
            {
                RemoveNode(last);
                last = last.Next;
            }

            return last;
        }

        private static Node? FilterPoints(Node? start, Node? end = null)
        {
            if (start == null) return start;
            if (end == null) end = start;

            Node p = start;
            bool again;
            do
            {
                again = false;

                if (!p.Steiner && (Equals(p, p.Next!) || Area(p.Prev!, p, p.Next!) == 0))
                {
                    RemoveNode(p);
                    p = end = p.Prev!;
                    if (p == p.Next) break;
                    again = true;
                }
                else
                {
                    p = p.Next!;
                }
            } while (again || p != end);

            return end;
        }

        private static void EarcutLinked(Node? ear, List<int> triangles, int dim, double minX, double minY, double invSize, int pass)
        {
            if (ear == null) return;

            if (pass == 0 && invSize != 0) IndexCurve(ear, minX, minY, invSize);

            Node? stop = ear;

            while (ear!.Prev != ear.Next)
            {
                Node prev = ear.Prev!;
                Node next = ear.Next!;

                if (invSize != 0 ? IsEarHashed(ear, minX, minY, invSize) : IsEar(ear))
                {
                    triangles.Add(prev.I);
                    triangles.Add(ear.I);
                    triangles.Add(next.I);

                    RemoveNode(ear);

                    ear = next.Next;
                    stop = next.Next;

                    continue;
                }

                ear = next;

                if (ear == stop)
                {
                    if (pass == 0)
                    {
                        EarcutLinked(FilterPoints(ear), triangles, dim, minX, minY, invSize, 1);
                    }
                    else if (pass == 1)
                    {
                        ear = CureLocalIntersections(FilterPoints(ear)!, triangles);
                        EarcutLinked(ear, triangles, dim, minX, minY, invSize, 2);
                    }
                    else if (pass == 2)
                    {
                        SplitEarcut(ear!, triangles, dim, minX, minY, invSize);
                    }

                    break;
                }
            }
        }

        private static bool IsEar(Node ear)
        {
            Node a = ear.Prev!, b = ear, c = ear.Next!;

            if (Area(a, b, c) >= 0) return false;

            double ax = a.X, bx = b.X, cx = c.X, ay = a.Y, by = b.Y, cy = c.Y;

            double x0 = Math.Min(ax, Math.Min(bx, cx));
            double y0 = Math.Min(ay, Math.Min(by, cy));
            double x1 = Math.Max(ax, Math.Max(bx, cx));
            double y1 = Math.Max(ay, Math.Max(by, cy));

            Node? p = c.Next;
            while (p != a)
            {
                if (p!.X >= x0 && p.X <= x1 && p.Y >= y0 && p.Y <= y1 &&
                    PointInTriangleExceptFirst(ax, ay, bx, by, cx, cy, p.X, p.Y) &&
                    Area(p.Prev!, p, p.Next!) >= 0) return false;
                p = p.Next;
            }

            return true;
        }

        private static bool IsEarHashed(Node ear, double minX, double minY, double invSize)
        {
            Node a = ear.Prev!, b = ear, c = ear.Next!;

            if (Area(a, b, c) >= 0) return false;

            double ax = a.X, bx = b.X, cx = c.X, ay = a.Y, by = b.Y, cy = c.Y;

            double x0 = Math.Min(ax, Math.Min(bx, cx));
            double y0 = Math.Min(ay, Math.Min(by, cy));
            double x1 = Math.Max(ax, Math.Max(bx, cx));
            double y1 = Math.Max(ay, Math.Max(by, cy));

            double minZ = ZOrder(x0, y0, minX, minY, invSize);
            double maxZ = ZOrder(x1, y1, minX, minY, invSize);

            Node? p = ear.PrevZ;
            Node? n = ear.NextZ;

            while (p != null && p.Z >= minZ && n != null && n.Z <= maxZ)
            {
                if (p.X >= x0 && p.X <= x1 && p.Y >= y0 && p.Y <= y1 && p != a && p != c &&
                    PointInTriangleExceptFirst(ax, ay, bx, by, cx, cy, p.X, p.Y) && Area(p.Prev!, p, p.Next!) >= 0) return false;
                p = p.PrevZ;

                if (n.X >= x0 && n.X <= x1 && n.Y >= y0 && n.Y <= y1 && n != a && n != c &&
                    PointInTriangleExceptFirst(ax, ay, bx, by, cx, cy, n.X, n.Y) && Area(n.Prev!, n, n.Next!) >= 0) return false;
                n = n.NextZ;
            }

            while (p != null && p.Z >= minZ)
            {
                if (p.X >= x0 && p.X <= x1 && p.Y >= y0 && p.Y <= y1 && p != a && p != c &&
                    PointInTriangleExceptFirst(ax, ay, bx, by, cx, cy, p.X, p.Y) && Area(p.Prev!, p, p.Next!) >= 0) return false;
                p = p.PrevZ;
            }

            while (n != null && n.Z <= maxZ)
            {
                if (n.X >= x0 && n.X <= x1 && n.Y >= y0 && n.Y <= y1 && n != a && n != c &&
                    PointInTriangleExceptFirst(ax, ay, bx, by, cx, cy, n.X, n.Y) && Area(n.Prev!, n, n.Next!) >= 0) return false;
                n = n.NextZ;
            }

            return true;
        }

        private static Node? CureLocalIntersections(Node start, List<int> triangles)
        {
            Node p = start;
            do
            {
                Node a = p.Prev!, b = p.Next!.Next!;

                if (!Equals(a, b) && Intersects(a, p, p.Next!, b) && LocallyInside(a, b) && LocallyInside(b, a))
                {
                    triangles.Add(a.I);
                    triangles.Add(p.I);
                    triangles.Add(b.I);

                    RemoveNode(p);
                    RemoveNode(p.Next!);

                    p = start = b;
                }
                p = p.Next!;
            } while (p != start);

            return FilterPoints(p);
        }

        private static void SplitEarcut(Node start, List<int> triangles, int dim, double minX, double minY, double invSize)
        {
            Node a = start;
            do
            {
                Node? b = a.Next!.Next;
                while (b != a.Prev)
                {
                    if (a.I != b!.I && IsValidDiagonal(a, b))
                    {
                        Node c = SplitPolygon(a, b);

                        Node? aFiltered = FilterPoints(a, a.Next);
                        Node? cFiltered = FilterPoints(c, c.Next);

                        EarcutLinked(aFiltered, triangles, dim, minX, minY, invSize, 0);
                        EarcutLinked(cFiltered, triangles, dim, minX, minY, invSize, 0);
                        return;
                    }
                    b = b.Next;
                }
                a = a.Next!;
            } while (a != start);
        }

        private static Node EliminateHoles(double[] data, int[] holeIndices, Node outerNode, int dim)
        {
            var queue = new List<Node>();

            for (int i = 0, len = holeIndices.Length; i < len; i++)
            {
                int start = holeIndices[i] * dim;
                int end = i < len - 1 ? holeIndices[i + 1] * dim : data.Length;
                Node? list = LinkedList(data, start, end, dim, false);
                if (list == list!.Next) list.Steiner = true;
                queue.Add(GetLeftmost(list));
            }

            queue.Sort(CompareXYSlope);

            for (int i = 0; i < queue.Count; i++)
            {
                outerNode = EliminateHole(queue[i], outerNode);
            }

            return outerNode;
        }

        private static int CompareXYSlope(Node a, Node b)
        {
            double result = a.X - b.X;
            if (result == 0)
            {
                result = a.Y - b.Y;
                if (result == 0)
                {
                    double aSlope = (a.Next!.Y - a.Y) / (a.Next.X - a.X);
                    double bSlope = (b.Next!.Y - b.Y) / (b.Next.X - b.X);
                    result = aSlope - bSlope;
                }
            }
            return result < 0 ? -1 : result > 0 ? 1 : 0;
        }

        private static Node EliminateHole(Node hole, Node outerNode)
        {
            Node? bridge = FindHoleBridge(hole, outerNode);
            if (bridge == null)
            {
                return outerNode;
            }

            Node bridgeReverse = SplitPolygon(bridge, hole);

            FilterPoints(bridgeReverse, bridgeReverse.Next);
            return FilterPoints(bridge, bridge.Next)!;
        }

        private static Node? FindHoleBridge(Node hole, Node outerNode)
        {
            Node p = outerNode;
            double hx = hole.X;
            double hy = hole.Y;
            double qx = double.NegativeInfinity;
            Node? m = null;

            if (Equals(hole, p)) return p;
            do
            {
                if (Equals(hole, p.Next!)) return p.Next;
                else if (hy <= p.Y && hy >= p.Next!.Y && p.Next.Y != p.Y)
                {
                    double x = p.X + (hy - p.Y) * (p.Next.X - p.X) / (p.Next.Y - p.Y);
                    if (x <= hx && x > qx)
                    {
                        qx = x;
                        m = p.X < p.Next.X ? p : p.Next;
                        if (x == hx) return m;
                    }
                }
                p = p.Next!;
            } while (p != outerNode);

            if (m == null) return null;

            Node stop = m;
            double mx = m.X;
            double my = m.Y;
            double tanMin = double.PositiveInfinity;

            p = m;

            do
            {
                if (hx >= p.X && p.X >= mx && hx != p.X &&
                    PointInTriangle(hy < my ? hx : qx, hy, mx, my, hy < my ? qx : hx, hy, p.X, p.Y))
                {
                    double tan = Math.Abs(hy - p.Y) / (hx - p.X);

                    if (LocallyInside(p, hole) &&
                        (tan < tanMin || (tan == tanMin && (p.X > m.X || (p.X == m.X && SectorContainsSector(m, p))))))
                    {
                        m = p;
                        tanMin = tan;
                    }
                }

                p = p.Next!;
            } while (p != stop);

            return m;
        }

        private static bool SectorContainsSector(Node m, Node p)
        {
            return Area(m.Prev!, m, p.Prev!) < 0 && Area(p.Next!, m, m.Next!) < 0;
        }

        private static void IndexCurve(Node start, double minX, double minY, double invSize)
        {
            Node p = start;
            do
            {
                if (p.Z == 0) p.Z = ZOrder(p.X, p.Y, minX, minY, invSize);
                p.PrevZ = p.Prev;
                p.NextZ = p.Next;
                p = p.Next!;
            } while (p != start);

            p.PrevZ!.NextZ = null;
            p.PrevZ = null;

            SortLinked(p);
        }

        private static Node SortLinked(Node list)
        {
            int numMerges;
            int inSize = 1;
            Node? listNode = list;

            do
            {
                Node? p = listNode;
                Node? e;
                listNode = null;
                Node? tail = null;
                numMerges = 0;

                while (p != null)
                {
                    numMerges++;
                    Node? q = p;
                    int pSize = 0;
                    for (int i = 0; i < inSize; i++)
                    {
                        pSize++;
                        q = q.NextZ;
                        if (q == null) break;
                    }
                    int qSize = inSize;

                    while (pSize > 0 || (qSize > 0 && q != null))
                    {
                        if (pSize != 0 && (qSize == 0 || q == null || p!.Z <= q.Z))
                        {
                            e = p;
                            p = p!.NextZ;
                            pSize--;
                        }
                        else
                        {
                            e = q;
                            q = q!.NextZ;
                            qSize--;
                        }

                        if (tail != null) tail.NextZ = e;
                        else listNode = e;

                        e!.PrevZ = tail;
                        tail = e;
                    }

                    p = q;
                }

                tail!.NextZ = null;
                inSize *= 2;
            } while (numMerges > 1);

            return listNode!;
        }

        private static double ZOrder(double x, double y, double minX, double minY, double invSize)
        {
            int xi = (int)((x - minX) * invSize);
            int yi = (int)((y - minY) * invSize);

            xi = (xi | (xi << 8)) & 0x00FF00FF;
            xi = (xi | (xi << 4)) & 0x0F0F0F0F;
            xi = (xi | (xi << 2)) & 0x33333333;
            xi = (xi | (xi << 1)) & 0x55555555;

            yi = (yi | (yi << 8)) & 0x00FF00FF;
            yi = (yi | (yi << 4)) & 0x0F0F0F0F;
            yi = (yi | (yi << 2)) & 0x33333333;
            yi = (yi | (yi << 1)) & 0x55555555;

            return xi | (yi << 1);
        }

        private static Node GetLeftmost(Node start)
        {
            Node p = start, leftmost = start;
            do
            {
                if (p.X < leftmost.X || (p.X == leftmost.X && p.Y < leftmost.Y)) leftmost = p;
                p = p.Next!;
            } while (p != start);

            return leftmost;
        }

        private static bool PointInTriangle(double ax, double ay, double bx, double by, double cx, double cy, double px, double py)
        {
            return (cx - px) * (ay - py) >= (ax - px) * (cy - py) &&
                   (ax - px) * (by - py) >= (bx - px) * (ay - py) &&
                   (bx - px) * (cy - py) >= (cx - px) * (by - py);
        }

        private static bool PointInTriangleExceptFirst(double ax, double ay, double bx, double by, double cx, double cy, double px, double py)
        {
            return !(ax == px && ay == py) && PointInTriangle(ax, ay, bx, by, cx, cy, px, py);
        }

        private static bool IsValidDiagonal(Node a, Node b)
        {
            return a.Next!.I != b.I && a.Prev!.I != b.I && !IntersectsPolygon(a, b) &&
                   ((LocallyInside(a, b) && LocallyInside(b, a) && MiddleInside(a, b) &&
                    (Area(a.Prev, a, b.Prev!) != 0 || Area(a, b.Prev!, b) != 0)) ||
                    (Equals(a, b) && Area(a.Prev, a, a.Next) > 0 && Area(b.Prev!, b, b.Next!) > 0));
        }

        private static double Area(Node p, Node q, Node r)
        {
            return (q.Y - p.Y) * (r.X - q.X) - (q.X - p.X) * (r.Y - q.Y);
        }

        private static bool Equals(Node p1, Node p2)
        {
            return p1.X == p2.X && p1.Y == p2.Y;
        }

        private static bool Intersects(Node p1, Node q1, Node p2, Node q2)
        {
            int o1 = Sign(Area(p1, q1, p2));
            int o2 = Sign(Area(p1, q1, q2));
            int o3 = Sign(Area(p2, q2, p1));
            int o4 = Sign(Area(p2, q2, q1));

            if (o1 != o2 && o3 != o4) return true;

            if (o1 == 0 && OnSegment(p1, p2, q1)) return true;
            if (o2 == 0 && OnSegment(p1, q2, q1)) return true;
            if (o3 == 0 && OnSegment(p2, p1, q2)) return true;
            if (o4 == 0 && OnSegment(p2, q1, q2)) return true;

            return false;
        }

        private static bool OnSegment(Node p, Node q, Node r)
        {
            return q.X <= Math.Max(p.X, r.X) && q.X >= Math.Min(p.X, r.X) && q.Y <= Math.Max(p.Y, r.Y) && q.Y >= Math.Min(p.Y, r.Y);
        }

        private static int Sign(double num)
        {
            return num > 0 ? 1 : num < 0 ? -1 : 0;
        }

        private static bool IntersectsPolygon(Node a, Node b)
        {
            Node p = a;
            do
            {
                if (p.I != a.I && p.Next!.I != a.I && p.I != b.I && p.Next.I != b.I &&
                    Intersects(p, p.Next, a, b)) return true;
                p = p.Next!;
            } while (p != a);

            return false;
        }

        private static bool LocallyInside(Node a, Node b)
        {
            return Area(a.Prev!, a, a.Next!) < 0
                ? Area(a, b, a.Next!) >= 0 && Area(a, a.Prev!, b) >= 0
                : Area(a, b, a.Prev!) < 0 || Area(a, a.Next!, b) < 0;
        }

        private static bool MiddleInside(Node a, Node b)
        {
            Node p = a;
            bool inside = false;
            double px = (a.X + b.X) / 2;
            double py = (a.Y + b.Y) / 2;
            do
            {
                if (((p.Y > py) != (p.Next!.Y > py)) && p.Next.Y != p.Y &&
                    (px < (p.Next.X - p.X) * (py - p.Y) / (p.Next.Y - p.Y) + p.X))
                    inside = !inside;
                p = p.Next!;
            } while (p != a);

            return inside;
        }

        private static Node SplitPolygon(Node a, Node b)
        {
            var a2 = new Node(a.I, a.X, a.Y);
            var b2 = new Node(b.I, b.X, b.Y);
            Node an = a.Next!;
            Node bp = b.Prev!;

            a.Next = b;
            b.Prev = a;

            a2.Next = an;
            an.Prev = a2;

            b2.Next = a2;
            a2.Prev = b2;

            bp.Next = b2;
            b2.Prev = bp;

            return b2;
        }

        private static Node InsertNode(int i, double x, double y, Node? last)
        {
            var p = new Node(i, x, y);

            if (last == null)
            {
                p.Prev = p;
                p.Next = p;
            }
            else
            {
                p.Next = last.Next;
                p.Prev = last;
                last.Next!.Prev = p;
                last.Next = p;
            }
            return p;
        }

        private static void RemoveNode(Node p)
        {
            p.Next!.Prev = p.Prev;
            p.Prev!.Next = p.Next;

            if (p.PrevZ != null) p.PrevZ.NextZ = p.NextZ;
            if (p.NextZ != null) p.NextZ.PrevZ = p.PrevZ;
        }

        private static double SignedArea(double[] data, int start, int end, int dim)
        {
            double sum = 0;
            for (int i = start, j = end - dim; i < end; i += dim)
            {
                sum += (data[j] - data[i]) * (data[i + 1] + data[j + 1]);
                j = i;
            }
            return sum;
        }
    }
}
