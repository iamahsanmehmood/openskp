import 'dart:math' as math;
import 'dart:typed_data';

import 'geometry.dart';
import 'model.dart';
import 'transforms.dart';

const double inchesToM = 0.0254;

/// One chained run of loose edges: its edges in walk order, the vertex ids
/// along it, and whether it returns to its first vertex (which is then NOT
/// repeated at the end).
class LooseEdgeRun {
  final List<int> edgeIds;
  final List<int> vertexIds;
  final bool closed;

  const LooseEdgeRun({
    required this.edgeIds,
    required this.vertexIds,
    required this.closed,
  });

  @override
  bool operator ==(Object other) {
    if (identical(this, other)) return true;
    if (other is! LooseEdgeRun || closed != other.closed) return false;
    if (edgeIds.length != other.edgeIds.length || vertexIds.length != other.vertexIds.length) {
      return false;
    }
    for (var i = 0; i < edgeIds.length; i++) {
      if (edgeIds[i] != other.edgeIds[i]) return false;
    }
    for (var i = 0; i < vertexIds.length; i++) {
      if (vertexIds[i] != other.vertexIds[i]) return false;
    }
    return true;
  }

  @override
  int get hashCode => Object.hash(Object.hashAll(edgeIds), Object.hashAll(vertexIds), closed);

  @override
  String toString() =>
      'LooseEdgeRun(edgeIds: $edgeIds, vertexIds: $vertexIds, closed: $closed)';
}

/// An analytic arc recovered for a loose-edge run: points in the run's own
/// frame (metres, glTF Y-up) plus 0-based `(start, mid, end)` index triples
/// into them, ready for IFC4 `IfcArcIndex`.
class CurveArc {
  final List<(double, double, double)> points;
  final List<(int, int, int)> segments;

  const CurveArc({
    required this.points,
    required this.segments,
  });
}

class PreparedCurveRun {
  final List<int> edgeIds;
  final List<(double, double, double)> localVerts;
  final bool closed;
  final String? explicitLayer;

  const PreparedCurveRun({
    required this.edgeIds,
    required this.localVerts,
    required this.closed,
    this.explicitLayer,
  });
}

class PreparedDefCurves {
  final List<PreparedCurveRun> runs;
  final Map<int, int>? curveEdgeCounts;

  const PreparedDefCurves({
    this.runs = const [],
    this.curveEdgeCounts,
  });
}

final ByteData _round6Bd = ByteData(8);
final BigInt _big15625 = BigInt.from(15625);

/// Round [x] to 6 decimal places, matching Python's `round(x, 6)` (exact IEEE-754 binary64 ties-to-even).
double round6(double x) {
  if (!x.isFinite || x.abs() >= 8589934592.0 || x == 0.0) return x;
  final absScaled = (x * 1e6).abs();
  if (absScaled < 4.5e12) {
    final fl = absScaled.floorToDouble();
    final frac = absScaled - fl;
    if ((frac - 0.5).abs() > 1e-3) {
      final res = (frac > 0.5 ? fl + 1.0 : fl) / 1e6;
      return x.isNegative ? -res : res;
    }
  }
  _round6Bd.setFloat64(0, x.abs(), Endian.little);
  final lo32 = _round6Bd.getUint32(0, Endian.little);
  final hi32 = _round6Bd.getUint32(4, Endian.little);
  final rawExp = (hi32 >> 20) & 0x7ff;
  if (rawExp == 0) return x.isNegative ? -0.0 : 0.0;
  final mantHi = (1 << 20) | (hi32 & 0xfffff);
  final mant = (BigInt.from(mantHi) << 32) | BigInt.from(lo32);
  final shift = 1069 - rawExp;
  final num = mant * _big15625;
  var k = num >> shift;
  final rem = num & ((BigInt.one << shift) - BigInt.one);
  final half = BigInt.one << (shift - 1);
  if (rem > half || (rem == half && k.isOdd)) {
    k += BigInt.one;
  }
  final res = k.toDouble() / 1e6;
  return x.isNegative ? -res : res;
}

/// Order ONE curve's edges into [LooseEdgeRun]s.
List<LooseEdgeRun> orderCurveEdges(
  (int, int)? Function(int eid) endsOf,
  Iterable<int> eids,
) {
  final remaining = Set<int>.from(eids);
  final runs = <LooseEdgeRun>[];

  while (remaining.isNotEmpty) {
    final adj = <int, List<(int, int)>>{};
    for (final e in remaining) {
      final ends = endsOf(e);
      if (ends == null) continue;
      final (a, b) = ends;
      (adj[a] ??= <(int, int)>[]).add((e, b));
      (adj[b] ??= <(int, int)>[]).add((e, a));
    }
    if (adj.isEmpty) break;

    int? start;
    for (final entry in adj.entries) {
      if (entry.value.length == 1) {
        start = entry.key;
        break;
      }
    }
    start ??= adj.keys.first;

    final first = adj[start]![0];
    final eid = first.$1;
    var nxt = first.$2;
    remaining.remove(eid);
    final chainEids = <int>[eid];
    final chain = <int>[start, nxt];
    var closed = false;

    while (true) {
      final neighbors = adj[nxt];
      (int, int)? nextOpt;
      if (neighbors != null) {
        for (final t in neighbors) {
          if (remaining.contains(t.$1)) {
            nextOpt = t;
            break;
          }
        }
      }
      if (nextOpt == null) break;
      final (e2, v2) = nextOpt;
      remaining.remove(e2);
      chainEids.add(e2);
      if (v2 == chain[0]) {
        closed = true;
        break;
      }
      chain.add(v2);
      nxt = v2;
    }

    runs.add(LooseEdgeRun(edgeIds: chainEids, vertexIds: chain, closed: closed));
  }

  return runs;
}

/// Turn a definition builder's *loose* edges (edges no face uses) into ordered
/// polyline runs, grouped strictly by [GeometryBuilder.edgeCurves].
List<LooseEdgeRun> chainLooseEdges(GeometryBuilder builder) {
  if (builder.edges.isEmpty) return const [];

  final used = <int>{};
  for (final face in builder.faces.values) {
    for (final loop in face.loops) {
      for (final (edgeId, _) in loop) {
        used.add(edgeId);
      }
    }
  }

  final groups = <int, List<int>>{};
  final runs = <LooseEdgeRun>[];
  for (final entry in builder.edges.entries) {
    final eid = entry.key;
    if (used.contains(eid)) continue;
    final (v1, v2) = entry.value;
    if (v1 == null || v2 == null || v1 == v2) continue;
    final cid = builder.edgeCurves[eid];
    if (cid != null && cid != 0) {
      (groups[cid] ??= <int>[]).add(eid);
    } else {
      runs.add(LooseEdgeRun(edgeIds: <int>[eid], vertexIds: <int>[v1, v2], closed: false));
    }
  }

  (int, int)? endsOf(int eid) {
    final pair = builder.edges[eid];
    if (pair == null || pair.$1 == null || pair.$2 == null) return null;
    return (pair.$1!, pair.$2!);
  }

  for (final eids in groups.values) {
    runs.addAll(orderCurveEdges(endsOf, eids));
  }
  return runs;
}

/// Turn a public [Definition]'s loose edges (edges no face uses) into ordered
/// polyline runs, grouped strictly by [Edge.curveId].
List<LooseEdgeRun> looseEdgeRuns(Definition def) {
  final used = <int>{};
  for (final face in def.faces.values) {
    for (final loop in face.loops) {
      for (final (edgeId, _) in loop) {
        used.add(edgeId);
      }
    }
  }

  final groups = <int, List<int>>{};
  final runs = <LooseEdgeRun>[];
  for (final entry in def.edges.entries) {
    final eid = entry.key;
    final edge = entry.value;
    if (used.contains(eid)) continue;
    if (edge.v1Id == edge.v2Id) continue;
    final cid = edge.curveId;
    if (cid != null && cid != 0) {
      (groups[cid] ??= <int>[]).add(eid);
    } else {
      runs.add(LooseEdgeRun(
        edgeIds: <int>[eid],
        vertexIds: <int>[edge.v1Id, edge.v2Id],
        closed: false,
      ));
    }
  }

  (int, int)? endsOf(int eid) {
    final e = def.edges[eid];
    if (e == null) return null;
    return (e.v1Id, e.v2Id);
  }

  for (final eids in groups.values) {
    runs.addAll(orderCurveEdges(endsOf, eids));
  }
  return runs;
}

Map<int, int> indexEdgeCurves(GeometryBuilder builder) {
  final counts = <int, int>{};
  for (final cid in builder.edgeCurves.values) {
    if (cid != 0) {
      counts[cid] = (counts[cid] ?? 0) + 1;
    }
  }
  return counts;
}

String? _resolveExplicitRunLayer(
  GeometryBuilder builder,
  Map<int, String> layerIdToName,
  List<int> edgeIds,
) {
  if (builder.edgeLayers.isEmpty) return null;
  final counts = <int, int>{};
  for (final eid in edgeIds) {
    final lid = builder.edgeLayers[eid];
    if (lid != null && lid != 0) {
      counts[lid] = (counts[lid] ?? 0) + 1;
    }
  }
  if (counts.isEmpty) return null;
  var bestLid = 0;
  var bestCount = -1;
  for (final entry in counts.entries) {
    if (entry.value > bestCount) {
      bestLid = entry.key;
      bestCount = entry.value;
    }
  }
  return layerIdToName[bestLid];
}

/// Resolve the majority layer of a loose-edge run's edges, or [fallback].
String resolveRunLayer(
  GeometryBuilder builder,
  Map<int, String> layerIdToName,
  List<int> edgeIds,
  String fallback,
) =>
    _resolveExplicitRunLayer(builder, layerIdToName, edgeIds) ?? fallback;

PreparedDefCurves prepareDefinitionCurves(
  GeometryBuilder builder,
  Map<int, String> layerIdToName,
) {
  final rawRuns = chainLooseEdges(builder);
  if (rawRuns.isEmpty) return const PreparedDefCurves();

  final runs = <PreparedCurveRun>[];
  for (final run in rawRuns) {
    final localVerts = <(double, double, double)>[];
    var complete = true;
    for (final vk in run.vertexIds) {
      final v = builder.vertices[vk];
      if (v == null) {
        complete = false;
        break;
      }
      localVerts.add(v);
    }
    if (!complete || localVerts.length < 2) continue;
    runs.add(PreparedCurveRun(
      edgeIds: run.edgeIds,
      localVerts: localVerts,
      closed: run.closed,
      explicitLayer: _resolveExplicitRunLayer(builder, layerIdToName, run.edgeIds),
    ));
  }
  if (runs.isEmpty) return const PreparedDefCurves();
  return PreparedDefCurves(
    runs: runs,
    curveEdgeCounts: builder.arcCurves.isNotEmpty ? indexEdgeCurves(builder) : null,
  );
}

double _dist3((double, double, double) p, (double, double, double) q) {
  final dx = p.$1 - q.$1;
  final dy = p.$2 - q.$2;
  final dz = p.$3 - q.$3;
  return math.sqrt(dx * dx + dy * dy + dz * dz);
}

/// Recover the analytic circular arc behind one loose-edge run, or `null` when
/// the run is not one whole circular `ArcCurve` whose stored vertices sit on
/// its frame.
CurveArc? solveRunArc(
  GeometryBuilder builder,
  List<int> eids,
  List<double>? matrix,
  List<(double, double, double)> pts, {
  bool closed = false,
  Map<int, int>? curveEdgeCounts,
}) {
  if (builder.arcCurves.isEmpty || eids.isEmpty) return null;
  final cid = builder.edgeCurves[eids.first];
  if (cid == null) return null;
  final frame = builder.arcCurves[cid];
  if (frame == null) return null;

  var curveEdgeCount = 0;
  if (curveEdgeCounts != null) {
    curveEdgeCount = curveEdgeCounts[cid] ?? 0;
  } else {
    for (final v in builder.edgeCurves.values) {
      if (v == cid) curveEdgeCount++;
    }
  }
  if (curveEdgeCount != eids.length) return null;

  final fm = (matrix != null && matrix.length >= 12) ? Transforms.foldScale(matrix) : null;
  var mirrored = false;
  if (fm != null) {
    final det = fm[0] * (fm[4] * fm[8] - fm[5] * fm[7]) -
        fm[1] * (fm[3] * fm[8] - fm[5] * fm[6]) +
        fm[2] * (fm[3] * fm[7] - fm[4] * fm[6]);
    mirrored = det < 0.0;
  }

  (double, double, double) xfPoint((double, double, double) p) {
    if (fm == null) {
      return (p.$1 * inchesToM, p.$3 * inchesToM, -p.$2 * inchesToM);
    }
    final qx = fm[0] * p.$1 + fm[1] * p.$2 + fm[2] * p.$3 + fm[9];
    final qy = fm[3] * p.$1 + fm[4] * p.$2 + fm[5] * p.$3 + fm[10];
    final qz = fm[6] * p.$1 + fm[7] * p.$2 + fm[8] * p.$3 + fm[11];
    return (qx * inchesToM, qz * inchesToM, -qy * inchesToM);
  }

  (double, double, double) xfVec((double, double, double) v) {
    double x, y, z;
    if (fm == null) {
      (x, y, z) = v;
    } else {
      x = fm[0] * v.$1 + fm[1] * v.$2 + fm[2] * v.$3;
      y = fm[3] * v.$1 + fm[4] * v.$2 + fm[5] * v.$3;
      z = fm[6] * v.$1 + fm[7] * v.$2 + fm[8] * v.$3;
    }
    return (x * inchesToM, z * inchesToM, -y * inchesToM);
  }

  final c = xfPoint(frame.center);
  final xa = xfVec(frame.xAxis);
  final ya = xfVec(frame.yAxis);
  final na = xfVec(frame.normal);

  final r = math.sqrt(xa.$1 * xa.$1 + xa.$2 * xa.$2 + xa.$3 * xa.$3);
  if (r <= 0) return null;

  final nlen = math.sqrt(na.$1 * na.$1 + na.$2 * na.$2 + na.$3 * na.$3);
  if (nlen <= 0) return null;
  final nSign = mirrored ? -1.0 : 1.0;
  final nh = (nSign * na.$1 / nlen, nSign * na.$2 / nlen, nSign * na.$3 / nlen);
  final cross = (
    nh.$2 * xa.$3 - nh.$3 * xa.$2,
    nh.$3 * xa.$1 - nh.$1 * xa.$3,
    nh.$1 * xa.$2 - nh.$2 * xa.$1,
  );
  if (_dist3(cross, ya) > 1e-6 * r) return null;

  final invR = 1.0 / r;
  final xh = (xa.$1 * invR, xa.$2 * invR, xa.$3 * invR);
  final yh = (ya.$1 * invR, ya.$2 * invR, ya.$3 * invR);

  final tol = math.max(1e-5, 1e-6 * r);
  for (final p in pts) {
    if ((_dist3(p, c) - r).abs() > tol) return null;
  }

  double theta((double, double, double) p) {
    final dx = p.$1 - c.$1;
    final dy = p.$2 - c.$2;
    final dz = p.$3 - c.$3;
    return math.atan2(
      dx * yh.$1 + dy * yh.$2 + dz * yh.$3,
      dx * xh.$1 + dy * xh.$2 + dz * xh.$3,
    );
  }

  final thetas = pts.map(theta).toList();
  final pairs = <(double, double)>[];
  for (var i = 0; i + 1 < thetas.length; i++) {
    pairs.add((thetas[i], thetas[i + 1]));
  }
  if (closed && thetas.length > 2) {
    pairs.add((thetas.last, thetas.first));
  }

  final stepLimit = math.pi * 0.5 * (1.0 + 1e-9);
  final steps = <double>[];
  var total = 0.0;
  for (final (a, b) in pairs) {
    var d = b - a;
    while (d > math.pi) {
      d -= 2.0 * math.pi;
    }
    while (d < -math.pi) {
      d += 2.0 * math.pi;
    }
    if (d.abs() > stepLimit) return null;
    if (steps.isNotEmpty && (d > 0) != (steps[0] > 0)) return null;
    steps.add(d);
    total += d;
  }
  if (steps.isEmpty || total == 0.0) return null;

  (double, double, double) at(double t) {
    final ct = math.cos(t);
    final st = math.sin(t);
    return (
      c.$1 + ct * xa.$1 + st * ya.$1,
      c.$2 + ct * xa.$2 + st * ya.$2,
      c.$3 + ct * xa.$3 + st * ya.$3,
    );
  }

  final t0 = thetas[0];
  if (total.abs() >= 2.0 * math.pi - 1e-6 * math.max(1.0, total.abs())) {
    return CurveArc(
      points: [
        at(t0),
        at(t0 + total * 0.25),
        at(t0 + total * 0.5),
        at(t0 + total * 0.75),
      ],
      segments: const [(0, 1, 2), (2, 3, 0)],
    );
  }
  return CurveArc(
    points: [at(t0), at(t0 + total * 0.5), at(t0 + total)],
    segments: const [(0, 1, 2)],
  );
}
