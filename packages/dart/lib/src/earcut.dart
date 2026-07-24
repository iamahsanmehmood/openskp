/// Ear-clipping polygon triangulation with hole support, ported from
/// Mapbox's "earcut" (https://github.com/mapbox/earcut, ISC License,
/// Copyright (c) 2024 Mapbox) - the same library the TypeScript port
/// already depends on for triangulateFace3D. Ported here (rather than
/// pulled in as a package) to keep this port dependency-free, matching the
/// rest of this package's design.
///
/// Correctly handles concave polygons and polygons with holes, unlike the
/// plane-fan fallback this file replaces in triangulator.dart.
library earcut;

class _Node {
  int i;
  double x, y;
  _Node? prev;
  _Node? next;
  double z = 0;
  _Node? prevZ;
  _Node? nextZ;
  bool steiner = false;

  _Node(this.i, this.x, this.y);
}

class Earcut {
  /// Triangulates a (possibly holed) 2D polygon given as a flat
  /// [x0,y0,x1,y1,...] coordinate array, with holeIndices giving the
  /// starting *vertex* (not coordinate) index of each hole ring. Returns a
  /// flat list of vertex indices, 3 per triangle.
  static List<int> triangulate(List<double> data, List<int>? holeIndices, [int dim = 2]) {
    final hasHoles = holeIndices != null && holeIndices.isNotEmpty;
    final outerLen = hasHoles ? holeIndices[0] * dim : data.length;
    _Node? outerNode = _linkedList(data, 0, outerLen, dim, true);
    final triangles = <int>[];

    if (outerNode == null || outerNode.next == outerNode.prev) return triangles;

    double minX = 0, minY = 0, invSize = 0;

    if (hasHoles) outerNode = _eliminateHoles(data, holeIndices, outerNode, dim);

    if (data.length > 80 * dim) {
      minX = data[0];
      minY = data[1];
      double maxX = minX, maxY = minY;

      for (int i = dim; i < outerLen; i += dim) {
        final x = data[i], y = data[i + 1];
        if (x < minX) minX = x;
        if (y < minY) minY = y;
        if (x > maxX) maxX = x;
        if (y > maxY) maxY = y;
      }

      invSize = (maxX - minX) > (maxY - minY) ? (maxX - minX) : (maxY - minY);
      invSize = invSize != 0 ? 32767 / invSize : 0;
    }

    _earcutLinked(outerNode, triangles, dim, minX, minY, invSize, 0);

    return triangles;
  }

  static _Node? _linkedList(List<double> data, int start, int end, int dim, bool clockwise) {
    _Node? last;

    if (clockwise == (_signedArea(data, start, end, dim) > 0)) {
      for (int i = start; i < end; i += dim) {
        last = _insertNode(i ~/ dim, data[i], data[i + 1], last);
      }
    } else {
      for (int i = end - dim; i >= start; i -= dim) {
        last = _insertNode(i ~/ dim, data[i], data[i + 1], last);
      }
    }

    if (last != null && _equals(last, last.next!)) {
      _removeNode(last);
      last = last.next;
    }

    return last;
  }

  static _Node? _filterPoints(_Node? start, [_Node? end]) {
    if (start == null) return start;
    end ??= start;

    var p = start;
    bool again;
    do {
      again = false;

      if (!p.steiner && (_equals(p, p.next!) || _area(p.prev!, p, p.next!) == 0)) {
        _removeNode(p);
        p = end = p.prev!;
        if (p == p.next) break;
        again = true;
      } else {
        p = p.next!;
      }
    } while (again || p != end);

    return end;
  }

  static void _earcutLinked(_Node? ear, List<int> triangles, int dim, double minX, double minY, double invSize, int pass) {
    if (ear == null) return;

    if (pass == 0 && invSize != 0) _indexCurve(ear, minX, minY, invSize);

    _Node? stop = ear;

    while (ear!.prev != ear.next) {
      final prev = ear.prev!;
      final next = ear.next!;

      if (invSize != 0 ? _isEarHashed(ear, minX, minY, invSize) : _isEar(ear)) {
        triangles.add(prev.i);
        triangles.add(ear.i);
        triangles.add(next.i);

        _removeNode(ear);

        ear = next.next;
        stop = next.next;

        continue;
      }

      ear = next;

      if (ear == stop) {
        if (pass == 0) {
          _earcutLinked(_filterPoints(ear), triangles, dim, minX, minY, invSize, 1);
        } else if (pass == 1) {
          ear = _cureLocalIntersections(_filterPoints(ear)!, triangles);
          _earcutLinked(ear, triangles, dim, minX, minY, invSize, 2);
        } else if (pass == 2) {
          _splitEarcut(ear, triangles, dim, minX, minY, invSize);
        }

        break;
      }
    }
  }

  static bool _isEar(_Node ear) {
    final a = ear.prev!, b = ear, c = ear.next!;

    if (_area(a, b, c) >= 0) return false;

    final ax = a.x, bx = b.x, cx = c.x, ay = a.y, by = b.y, cy = c.y;

    final x0 = _min3(ax, bx, cx), y0 = _min3(ay, by, cy);
    final x1 = _max3(ax, bx, cx), y1 = _max3(ay, by, cy);

    var p = c.next;
    while (p != a) {
      if (p!.x >= x0 && p.x <= x1 && p.y >= y0 && p.y <= y1 &&
          _pointInTriangleExceptFirst(ax, ay, bx, by, cx, cy, p.x, p.y) &&
          _area(p.prev!, p, p.next!) >= 0) {
        return false;
      }
      p = p.next;
    }

    return true;
  }

  static bool _isEarHashed(_Node ear, double minX, double minY, double invSize) {
    final a = ear.prev!, b = ear, c = ear.next!;

    if (_area(a, b, c) >= 0) return false;

    final ax = a.x, bx = b.x, cx = c.x, ay = a.y, by = b.y, cy = c.y;

    final x0 = _min3(ax, bx, cx), y0 = _min3(ay, by, cy);
    final x1 = _max3(ax, bx, cx), y1 = _max3(ay, by, cy);

    final minZ = _zOrder(x0, y0, minX, minY, invSize);
    final maxZ = _zOrder(x1, y1, minX, minY, invSize);

    var p = ear.prevZ;
    var n = ear.nextZ;

    while (p != null && p.z >= minZ && n != null && n.z <= maxZ) {
      if (p.x >= x0 && p.x <= x1 && p.y >= y0 && p.y <= y1 && p != a && p != c &&
          _pointInTriangleExceptFirst(ax, ay, bx, by, cx, cy, p.x, p.y) && _area(p.prev!, p, p.next!) >= 0) {
        return false;
      }
      p = p.prevZ;

      if (n.x >= x0 && n.x <= x1 && n.y >= y0 && n.y <= y1 && n != a && n != c &&
          _pointInTriangleExceptFirst(ax, ay, bx, by, cx, cy, n.x, n.y) && _area(n.prev!, n, n.next!) >= 0) {
        return false;
      }
      n = n.nextZ;
    }

    while (p != null && p.z >= minZ) {
      if (p.x >= x0 && p.x <= x1 && p.y >= y0 && p.y <= y1 && p != a && p != c &&
          _pointInTriangleExceptFirst(ax, ay, bx, by, cx, cy, p.x, p.y) && _area(p.prev!, p, p.next!) >= 0) {
        return false;
      }
      p = p.prevZ;
    }

    while (n != null && n.z <= maxZ) {
      if (n.x >= x0 && n.x <= x1 && n.y >= y0 && n.y <= y1 && n != a && n != c &&
          _pointInTriangleExceptFirst(ax, ay, bx, by, cx, cy, n.x, n.y) && _area(n.prev!, n, n.next!) >= 0) {
        return false;
      }
      n = n.nextZ;
    }

    return true;
  }

  static _Node? _cureLocalIntersections(_Node start, List<int> triangles) {
    var p = start;
    do {
      final a = p.prev!, b = p.next!.next!;

      if (!_equals(a, b) && _intersects(a, p, p.next!, b) && _locallyInside(a, b) && _locallyInside(b, a)) {
        triangles.add(a.i);
        triangles.add(p.i);
        triangles.add(b.i);

        _removeNode(p);
        _removeNode(p.next!);

        p = start = b;
      }
      p = p.next!;
    } while (p != start);

    return _filterPoints(p);
  }

  static void _splitEarcut(_Node start, List<int> triangles, int dim, double minX, double minY, double invSize) {
    var a = start;
    do {
      _Node? b = a.next!.next;
      while (b != a.prev) {
        if (a.i != b!.i && _isValidDiagonal(a, b)) {
          final c = _splitPolygon(a, b);

          final aFiltered = _filterPoints(a, a.next);
          final cFiltered = _filterPoints(c, c.next);

          _earcutLinked(aFiltered, triangles, dim, minX, minY, invSize, 0);
          _earcutLinked(cFiltered, triangles, dim, minX, minY, invSize, 0);
          return;
        }
        b = b.next;
      }
      a = a.next!;
    } while (a != start);
  }

  static _Node _eliminateHoles(List<double> data, List<int> holeIndices, _Node outerNode, int dim) {
    final queue = <_Node>[];

    for (int i = 0, len = holeIndices.length; i < len; i++) {
      final start = holeIndices[i] * dim;
      final end = i < len - 1 ? holeIndices[i + 1] * dim : data.length;
      final list = _linkedList(data, start, end, dim, false)!;
      if (list == list.next) list.steiner = true;
      queue.add(_getLeftmost(list));
    }

    queue.sort(_compareXYSlope);

    var result = outerNode;
    for (int i = 0; i < queue.length; i++) {
      result = _eliminateHole(queue[i], result);
    }

    return result;
  }

  static int _compareXYSlope(_Node a, _Node b) {
    var result = a.x - b.x;
    if (result == 0) {
      result = a.y - b.y;
      if (result == 0) {
        final aSlope = (a.next!.y - a.y) / (a.next!.x - a.x);
        final bSlope = (b.next!.y - b.y) / (b.next!.x - b.x);
        result = aSlope - bSlope;
      }
    }
    return result < 0
        ? -1
        : result > 0
            ? 1
            : 0;
  }

  static _Node _eliminateHole(_Node hole, _Node outerNode) {
    final bridge = _findHoleBridge(hole, outerNode);
    if (bridge == null) {
      return outerNode;
    }

    final bridgeReverse = _splitPolygon(bridge, hole);

    _filterPoints(bridgeReverse, bridgeReverse.next);
    return _filterPoints(bridge, bridge.next)!;
  }

  static _Node? _findHoleBridge(_Node hole, _Node outerNode) {
    var p = outerNode;
    final hx = hole.x;
    final hy = hole.y;
    double qx = double.negativeInfinity;
    _Node? m;

    if (_equals(hole, p)) return p;
    do {
      if (_equals(hole, p.next!)) return p.next;
      if (hy <= p.y && hy >= p.next!.y && p.next!.y != p.y) {
        final x = p.x + (hy - p.y) * (p.next!.x - p.x) / (p.next!.y - p.y);
        if (x <= hx && x > qx) {
          qx = x;
          m = p.x < p.next!.x ? p : p.next;
          if (x == hx) return m;
        }
      }
      p = p.next!;
    } while (p != outerNode);

    if (m == null) return null;

    final stop = m;
    final mx = m.x;
    final my = m.y;
    double tanMin = double.infinity;

    p = m;

    do {
      if (hx >= p.x && p.x >= mx && hx != p.x &&
          _pointInTriangle(hy < my ? hx : qx, hy, mx, my, hy < my ? qx : hx, hy, p.x, p.y)) {
        final tan = (hy - p.y).abs() / (hx - p.x);

        if (_locallyInside(p, hole) &&
            (tan < tanMin || (tan == tanMin && (p.x > m!.x || (p.x == m.x && _sectorContainsSector(m, p)))))) {
          m = p;
          tanMin = tan;
        }
      }

      p = p.next!;
    } while (p != stop);

    return m;
  }

  static bool _sectorContainsSector(_Node m, _Node p) {
    return _area(m.prev!, m, p.prev!) < 0 && _area(p.next!, m, m.next!) < 0;
  }

  static void _indexCurve(_Node start, double minX, double minY, double invSize) {
    var p = start;
    do {
      if (p.z == 0) p.z = _zOrder(p.x, p.y, minX, minY, invSize);
      p.prevZ = p.prev;
      p.nextZ = p.next;
      p = p.next!;
    } while (p != start);

    p.prevZ!.nextZ = null;
    p.prevZ = null;

    _sortLinked(p);
  }

  static _Node _sortLinked(_Node list) {
    int numMerges;
    int inSize = 1;
    _Node? listNode = list;

    do {
      _Node? p = listNode;
      _Node? e;
      listNode = null;
      _Node? tail;
      numMerges = 0;

      while (p != null) {
        numMerges++;
        _Node? q = p;
        int pSize = 0;
        for (int i = 0; i < inSize; i++) {
          pSize++;
          q = q!.nextZ;
          if (q == null) break;
        }
        int qSize = inSize;

        while (pSize > 0 || (qSize > 0 && q != null)) {
          if (pSize != 0 && (qSize == 0 || q == null || p!.z <= q.z)) {
            e = p;
            p = p!.nextZ;
            pSize--;
          } else {
            e = q;
            q = q!.nextZ;
            qSize--;
          }

          if (tail != null) {
            tail.nextZ = e;
          } else {
            listNode = e;
          }

          e!.prevZ = tail;
          tail = e;
        }

        p = q;
      }

      tail!.nextZ = null;
      inSize *= 2;
    } while (numMerges > 1);

    return listNode!;
  }

  static double _zOrder(double x, double y, double minX, double minY, double invSize) {
    int xi = ((x - minX) * invSize).toInt();
    int yi = ((y - minY) * invSize).toInt();

    xi = (xi | (xi << 8)) & 0x00FF00FF;
    xi = (xi | (xi << 4)) & 0x0F0F0F0F;
    xi = (xi | (xi << 2)) & 0x33333333;
    xi = (xi | (xi << 1)) & 0x55555555;

    yi = (yi | (yi << 8)) & 0x00FF00FF;
    yi = (yi | (yi << 4)) & 0x0F0F0F0F;
    yi = (yi | (yi << 2)) & 0x33333333;
    yi = (yi | (yi << 1)) & 0x55555555;

    return (xi | (yi << 1)).toDouble();
  }

  static _Node _getLeftmost(_Node start) {
    var p = start, leftmost = start;
    do {
      if (p.x < leftmost.x || (p.x == leftmost.x && p.y < leftmost.y)) leftmost = p;
      p = p.next!;
    } while (p != start);

    return leftmost;
  }

  static bool _pointInTriangle(double ax, double ay, double bx, double by, double cx, double cy, double px, double py) {
    return (cx - px) * (ay - py) >= (ax - px) * (cy - py) &&
        (ax - px) * (by - py) >= (bx - px) * (ay - py) &&
        (bx - px) * (cy - py) >= (cx - px) * (by - py);
  }

  static bool _pointInTriangleExceptFirst(
      double ax, double ay, double bx, double by, double cx, double cy, double px, double py) {
    return !(ax == px && ay == py) && _pointInTriangle(ax, ay, bx, by, cx, cy, px, py);
  }

  static bool _isValidDiagonal(_Node a, _Node b) {
    return a.next!.i != b.i &&
        a.prev!.i != b.i &&
        !_intersectsPolygon(a, b) &&
        ((_locallyInside(a, b) &&
                _locallyInside(b, a) &&
                _middleInside(a, b) &&
                (_area(a.prev!, a, b.prev!) != 0 || _area(a, b.prev!, b) != 0)) ||
            (_equals(a, b) && _area(a.prev!, a, a.next!) > 0 && _area(b.prev!, b, b.next!) > 0));
  }

  static double _area(_Node p, _Node q, _Node r) {
    return (q.y - p.y) * (r.x - q.x) - (q.x - p.x) * (r.y - q.y);
  }

  static bool _equals(_Node p1, _Node p2) {
    return p1.x == p2.x && p1.y == p2.y;
  }

  static bool _intersects(_Node p1, _Node q1, _Node p2, _Node q2) {
    final o1 = _sign(_area(p1, q1, p2));
    final o2 = _sign(_area(p1, q1, q2));
    final o3 = _sign(_area(p2, q2, p1));
    final o4 = _sign(_area(p2, q2, q1));

    if (o1 != o2 && o3 != o4) return true;

    if (o1 == 0 && _onSegment(p1, p2, q1)) return true;
    if (o2 == 0 && _onSegment(p1, q2, q1)) return true;
    if (o3 == 0 && _onSegment(p2, p1, q2)) return true;
    if (o4 == 0 && _onSegment(p2, q1, q2)) return true;

    return false;
  }

  static bool _onSegment(_Node p, _Node q, _Node r) {
    return q.x <= _max2(p.x, r.x) && q.x >= _min2(p.x, r.x) && q.y <= _max2(p.y, r.y) && q.y >= _min2(p.y, r.y);
  }

  static int _sign(double num) => num > 0 ? 1 : (num < 0 ? -1 : 0);

  static bool _intersectsPolygon(_Node a, _Node b) {
    var p = a;
    do {
      if (p.i != a.i && p.next!.i != a.i && p.i != b.i && p.next!.i != b.i && _intersects(p, p.next!, a, b)) {
        return true;
      }
      p = p.next!;
    } while (p != a);

    return false;
  }

  static bool _locallyInside(_Node a, _Node b) {
    return _area(a.prev!, a, a.next!) < 0
        ? _area(a, b, a.next!) >= 0 && _area(a, a.prev!, b) >= 0
        : _area(a, b, a.prev!) < 0 || _area(a, a.next!, b) < 0;
  }

  static bool _middleInside(_Node a, _Node b) {
    var p = a;
    bool inside = false;
    final px = (a.x + b.x) / 2;
    final py = (a.y + b.y) / 2;
    do {
      if (((p.y > py) != (p.next!.y > py)) &&
          p.next!.y != p.y &&
          (px < (p.next!.x - p.x) * (py - p.y) / (p.next!.y - p.y) + p.x)) {
        inside = !inside;
      }
      p = p.next!;
    } while (p != a);

    return inside;
  }

  static _Node _splitPolygon(_Node a, _Node b) {
    final a2 = _Node(a.i, a.x, a.y);
    final b2 = _Node(b.i, b.x, b.y);
    final an = a.next!;
    final bp = b.prev!;

    a.next = b;
    b.prev = a;

    a2.next = an;
    an.prev = a2;

    b2.next = a2;
    a2.prev = b2;

    bp.next = b2;
    b2.prev = bp;

    return b2;
  }

  static _Node _insertNode(int i, double x, double y, _Node? last) {
    final p = _Node(i, x, y);

    if (last == null) {
      p.prev = p;
      p.next = p;
    } else {
      p.next = last.next;
      p.prev = last;
      last.next!.prev = p;
      last.next = p;
    }
    return p;
  }

  static void _removeNode(_Node p) {
    p.next!.prev = p.prev;
    p.prev!.next = p.next;

    if (p.prevZ != null) p.prevZ!.nextZ = p.nextZ;
    if (p.nextZ != null) p.nextZ!.prevZ = p.prevZ;
  }

  static double _signedArea(List<double> data, int start, int end, int dim) {
    double sum = 0;
    for (int i = start, j = end - dim; i < end; i += dim) {
      sum += (data[j] - data[i]) * (data[i + 1] + data[j + 1]);
      j = i;
    }
    return sum;
  }

  static double _min2(double a, double b) => a < b ? a : b;
  static double _max2(double a, double b) => a > b ? a : b;
  static double _min3(double a, double b, double c) => _min2(_min2(a, b), c);
  static double _max3(double a, double b, double c) => _max2(_max2(a, b), c);
}
