import 'dart:convert';
import 'dart:math';

import 'model.dart';

/// Generates Dart source that rebuilds a parsed [SkpModel] from scratch
/// via `create()`/[SkpBuilder] - a faithful, human-readable, re-runnable
/// transcript of the model as writer API calls, not a serialized dump.
///
/// Handles: materials (solid and textured, including default-projection
/// and explicitly-pinned UVs), layers, component/group definitions (built
/// in dependency order), faces (front/back material, holes), instances
/// (transform, instance-level paint, instance-level name).
///
/// Found and fixed via diffing a real, large file (jeff.skp: 2713
/// definitions, 113643 faces) against its own regenerated output - the
/// TypeScript port this mirrors (`toTypeScriptCode`) found that an
/// earlier prototype silently dropped instance-level paint (95% of that
/// file's instances) and every instance's own name entirely, and never
/// emitted textured materials at all. Building this module surfaced the
/// same two bugs already living in `edit.dart`'s `openExisting` replay
/// (now fixed there too): an empty instance name being replaced by its
/// definition's name, and a textured material's applied height
/// corrupting ANY face that used it (not just default-projected ones).
///
/// Only reproduces geometry reachable by walking faces (`Definition.
/// faces`) - a real file's standalone/construction edges and curves that
/// don't bound any face are NOT reproduced (same limitation as the
/// TypeScript port - see its own doc for the concrete numbers this was
/// measured against). This does not affect materials, textures, instance
/// paint, or any face/surface geometry - only invisible
/// construction/reference lines.
///
/// Also not yet handled (matching this project's established disclosure
/// pattern for known gaps): colorized material tint, per-face
/// hidden/soft/smooth edge flags, section planes, text/dimension
/// entities. A model using any of these round-trips its
/// geometry/materials/instances correctly; those specific facts are
/// silently dropped.
///
/// A face a few millionths of an inch off its own fitted plane (common in
/// real files) is auto-triangulated rather than rejected, mirroring real
/// SketchUp's own tolerance.
String toDartCode(SkpModel model) {
  final lines = <String>[];
  void push(String s) => lines.add(s);

  double round(double n) {
    final r = double.parse(n.toStringAsFixed(4));
    return r == 0.0 ? 0.0 : r;
  }

  String pointStr((double, double, double) p) => '(${round(p.$1)}, ${round(p.$2)}, ${round(p.$3)})';
  // Matrix3x3 is a 9-tuple RECORD, not a List<double> - a record literal,
  // not a list literal.
  String matrix3x3Str(List<double> m9) => '(${m9.map(round).join(', ')})';

  Map<int, (int?, int?)> edgeMap(Definition defn) =>
      {for (final e in defn.edges.values) e.id: (e.v1Id, e.v2Id)};

  List<int> reconstructLoopVertices(List<(int, int)> loop, Map<int, (int?, int?)> edges) {
    final verts = <int>[];
    for (final (edgeId, orient) in loop) {
      final e = edges[edgeId];
      if (e == null) continue;
      final vStart = orient == 1 ? e.$1 : e.$2;
      if (vStart == null) continue;
      if (verts.isEmpty || verts.last != vStart) verts.add(vStart);
    }
    if (verts.length > 1 && verts.first == verts.last) verts.removeLast();
    return verts;
  }

  List<(double, double, double)>? loopPoints(
      List<(int, int)> loop, Map<int, (int?, int?)> edges, Definition defn) {
    final vertIds = reconstructLoopVertices(loop, edges);
    if (vertIds.length < 3) return null;
    final points = <(double, double, double)>[];
    for (final v in vertIds) {
      final vert = defn.vertices[v];
      if (vert != null) points.add((vert.x, vert.y, vert.z));
    }
    if (points.length < 3) return null;
    return points;
  }

  // frontUv/backUv need exactly 3 correspondences whose (u, v) values are
  // NOT collinear - real faces can have a "flat" vertex, which points
  // [0..2] alone isn't guaranteed to avoid.
  List<(double, double, double)>? nonCollinearTriple(List<(double, double, double)> points) {
    for (var i = 0; i < points.length; i++) {
      for (var j = i + 1; j < points.length; j++) {
        for (var k = j + 1; k < points.length; k++) {
          final a = points[i], b = points[j], c = points[k];
          final e1 = (b.$1 - a.$1, b.$2 - a.$2, b.$3 - a.$3);
          final e2 = (c.$1 - a.$1, c.$2 - a.$2, c.$3 - a.$3);
          final cx = e1.$2 * e2.$3 - e1.$3 * e2.$2;
          final cy = e1.$3 * e2.$1 - e1.$1 * e2.$3;
          final cz = e1.$1 * e2.$2 - e1.$2 * e2.$1;
          if (cx * cx + cy * cy + cz * cz > 1e-9) return [a, b, c];
        }
      }
    }
    return null;
  }

  ((double, double, double), (double, double, double)) faceUvBasis((double, double, double) n) {
    final cx = -n.$2, cy = n.$1;
    final clenSq = cx * cx + cy * cy;
    if (clenSq < 1e-18) {
      final yr = (0.0, n.$3 >= 0 ? 1.0 : -1.0, 0.0);
      return ((1.0, 0.0, 0.0), yr);
    }
    final clen = sqrt(clenSq);
    final xr = (cx / clen, cy / clen, 0.0);
    final yr = (
      n.$2 * xr.$3 - n.$3 * xr.$2,
      n.$3 * xr.$1 - n.$1 * xr.$3,
      n.$1 * xr.$2 - n.$2 * xr.$1,
    );
    return (xr, yr);
  }

  List<double> invert3x3(List<double> m) {
    final a = m[0], b = m[1], c = m[2];
    final d = m[3], e = m[4], f = m[5];
    final g = m[6], h = m[7], i = m[8];
    final det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g);
    final invDet = det.abs() < 1e-15 ? 0.0 : 1.0 / det;
    return [
      (e * i - f * h) * invDet, (c * h - b * i) * invDet, (b * f - c * e) * invDet,
      (f * g - d * i) * invDet, (a * i - c * g) * invDet, (c * d - a * f) * invDet,
      (d * h - e * g) * invDet, (b * g - a * h) * invDet, (a * e - b * d) * invDet,
    ];
  }

  (double, double) computeFaceUv(
    (double, double, double) p,
    (double, double, double) xr,
    (double, double, double) yr,
    List<double>? uvTransform,
    double tileW,
    double tileH,
  ) {
    final px = p.$1 * xr.$1 + p.$2 * xr.$2 + p.$3 * xr.$3;
    final py = p.$1 * yr.$1 + p.$2 * yr.$2 + p.$3 * yr.$3;
    if (uvTransform == null) return (px / tileW, py / tileH);
    final inv = invert3x3(uvTransform);
    final u = px * inv[0] + py * inv[3] + inv[6];
    final v = px * inv[1] + py * inv[4] + inv[7];
    var q = px * inv[2] + py * inv[5] + inv[8];
    if (q.abs() < 1e-12) q = 1.0;
    return (u / q / tileW, v / q / tileH);
  }

  final materialsById = model.materialsById;
  final matVar = <String, String>{};
  final texturedMats = <String>{};

  push("import 'package:openskp/openskp.dart';");
  push('');
  push('List<int> build() {');
  push('  final builder = create();');
  push('');
  push('  // --- Materials (${model.materials.length}) ---');
  for (var i = 0; i < model.materials.length; i++) {
    final mat = model.materials[i];
    final varName = 'mat$i';
    matVar[mat.name] = varName;
    final tex = mat.texture;
    if (tex != null && tex.data != null && tex.data!.isNotEmpty) {
      texturedMats.add(mat.name);
      final b64 = base64Encode(tex.data!);
      // appliedHeight: 1.0 - see addTextureMaterial's own note on why the
      // library default (an internal sentinel) corrupts ANY face using
      // this material. Every face using a textured material is written
      // below with explicit frontUv/backUv, never left to default
      // projection, so the original applied width/height never needs to
      // be reproduced.
      push('  final _texBytes$i = base64Decode(');
      push("    '$b64',");
      push('  );');
      push(
          "  final $varName = builder.addTextureMaterial(${_dartString(mat.name)}, _writeTempFile(_texBytes$i, ${_dartString(_extOf(tex.filename))}), appliedHeight: 1.0);");
    } else {
      final c = mat.color;
      push(
          "  final $varName = builder.addMaterial(${_dartString(mat.name)}, [${c.$1}, ${c.$2}, ${c.$3}, ${c.$4}]);");
    }
  }

  push('');
  push('  // --- Layers (${model.layers.length}) ---');
  for (var i = 0; i < model.layers.length; i++) {
    final layer = model.layers[i];
    final varName = 'layer$i';
    push(
        "  final $varName = builder.addLayer(${_dartString(layer.name)}, color: [${layer.colorR}, ${layer.colorG}, ${layer.colorB}], hidden: ${layer.hidden});");
  }

  String? uvTripleStr(
    List<(double, double, double)> points,
    (double, double, double)? normal,
    List<double>? uvTransform,
    double tileW,
    double tileH,
  ) {
    if (normal == null || points.length < 3) return null;
    final sample = nonCollinearTriple(points);
    if (sample == null) return null;
    final (xr, yr) = faceUvBasis(normal);
    final parts = sample.map((p) {
      final (u, v) = computeFaceUv(p, xr, yr, uvTransform, tileW, tileH);
      return '(${pointStr(p)}, (${round(u)}, ${round(v)}))';
    });
    return '[${parts.join(', ')}]';
  }

  (String, bool) materialOptsStr(Face face, List<(double, double, double)> points) {
    final parts = <String>[];
    var hasUv = false;
    final mid = face.materialId;
    if (mid != null) {
      final m = materialsById[mid];
      if (m != null) {
        parts.add('material: ${matVar[m.name]}');
        if (texturedMats.contains(m.name)) {
          final tw = m.texture!.width != 0.0 ? m.texture!.width : 1.0;
          final th = m.texture!.height != 0.0 ? m.texture!.height : 1.0;
          final triple = uvTripleStr(points, face.normal, face.uvTransform, tw, th);
          if (triple != null) {
            parts.add('frontUv: $triple');
            hasUv = true;
          }
        }
      }
    }
    final bmid = face.backMaterialId;
    if (bmid != null) {
      final m = materialsById[bmid];
      if (m != null) {
        parts.add('backMaterial: ${matVar[m.name]}');
        if (texturedMats.contains(m.name)) {
          final tw = m.texture!.width != 0.0 ? m.texture!.width : 1.0;
          final th = m.texture!.height != 0.0 ? m.texture!.height : 1.0;
          final triple = uvTripleStr(points, face.normal, face.uvTransformBack, tw, th);
          if (triple != null) {
            parts.add('backUv: $triple');
            hasUv = true;
          }
        }
      }
    }
    return (parts.join(', '), hasUv);
  }

  var facesSkippedDegenerate = 0;

  void emitFaces(Definition defn, String targetVar, String indent) {
    final edges = edgeMap(defn);
    for (final face in defn.faces.values) {
      if (face.loops.isEmpty) continue;
      final points = loopPoints(face.loops[0], edges, defn);
      if (points == null) {
        facesSkippedDegenerate++;
        continue;
      }
      final holes = <List<(double, double, double)>>[];
      for (var hi = 1; hi < face.loops.length; hi++) {
        final holePts = loopPoints(face.loops[hi], edges, defn);
        if (holePts != null) holes.add(holePts);
      }
      final (matStr, hasUv) = materialOptsStr(face, points);
      final pointsStr = points.map(pointStr).join(', ');
      final extra = <String>[];
      if (!hasUv) extra.add('autoTriangulate: true');
      if (holes.isNotEmpty) {
        final holesStr = holes.map((h) => '[${h.map(pointStr).join(', ')}]').join(', ');
        extra.add('holes: [$holesStr]');
      }
      final callParts = [if (matStr.isNotEmpty) matStr, ...extra];
      final callOpts = callParts.isNotEmpty ? ', ${callParts.join(', ')}' : '';
      push('$indent$targetVar.addFace([$pointsStr]$callOpts);');
    }
  }

  List<String> instanceOptsStr(Instance inst, String defName) {
    final parts = <String>[];
    final mid = inst.materialId;
    if (mid != null) {
      final m = materialsById[mid];
      if (m != null) parts.add('material: ${matVar[m.name]}');
    }
    // Explicit even when inst.name is empty: addInstance defaults an
    // OMITTED name to the definition's own name, so a source instance
    // with a genuinely empty name would otherwise come out with that
    // name baked in for real.
    if (inst.name != defName) parts.add('name: ${_dartString(inst.name)}');
    return parts;
  }

  final defVar = <int, String>{};
  var defCounter = 0;

  String? getOrBuildDef(int defId, Set<int> visiting) {
    final existing = defVar[defId];
    if (existing != null) return existing;
    if (visiting.contains(defId)) return null;
    visiting.add(defId);

    final defn = model.definitions[defId];
    if (defn == null || (defn.faces.isEmpty && defn.instances.isEmpty)) return null;

    for (final inst in defn.instances) {
      final refIdx = inst.refIdx;
      if (refIdx != null) getOrBuildDef(refIdx, visiting);
    }

    final varName = 'def${defCounter++}';
    final defName = defn.name.isNotEmpty ? defn.name : 'Def$defId';
    defVar[defId] = varName;

    push('');
    push('  // ${_dartComment(defn.name)} - ${defn.faces.length} faces, ${defn.instances.length} nested instances');
    push("  final $varName = builder.addComponentDefinition(${_dartString(defName)}, ($varName) {");
    emitFaces(defn, varName, '    ');
    for (final inst in defn.instances) {
      final refIdx = inst.refIdx;
      if (refIdx == null) continue;
      final childVar = defVar[refIdx];
      if (childVar == null) continue;
      final m9 = inst.matrix.length >= 9 ? inst.matrix.sublist(0, 9) : <double>[1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0];
      final t = inst.matrix.length >= 12
          ? (inst.matrix[9], inst.matrix[10], inst.matrix[11])
          : (0.0, 0.0, 0.0);
      final extra = instanceOptsStr(inst, defName);
      final opts = ['translation: ${pointStr(t)}', 'matrix3x3: ${matrix3x3Str(m9)}', ...extra];
      push('    $varName.addInstance($childVar, ${opts.join(', ')});');
    }
    push('  });');
    return varName;
  }

  for (final defId in model.definitions.keys.toList()) {
    getOrBuildDef(defId, <int>{});
  }

  push('');
  push('  // --- Root instances (${model.root.instances.length}) ---');
  for (final inst in model.root.instances) {
    final refIdx = inst.refIdx;
    if (refIdx == null) continue;
    final childVar = defVar[refIdx];
    if (childVar == null) continue;
    final childDefName = model.definitions[refIdx]?.name ?? '';
    final m9 = inst.matrix.length >= 9 ? inst.matrix.sublist(0, 9) : <double>[1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0];
    final t = inst.matrix.length >= 12
        ? (inst.matrix[9], inst.matrix[10], inst.matrix[11])
        : (0.0, 0.0, 0.0);
    final extra = instanceOptsStr(inst, childDefName);
    final opts = ['translation: ${pointStr(t)}', 'matrix3x3: ${matrix3x3Str(m9)}', ...extra];
    push('  builder.addInstance($childVar, ${opts.join(', ')});');
  }
  emitFaces(model.root, 'builder', '  ');

  push('');
  push('  return builder.toBytes();');
  push('}');
  if (texturedMats.isNotEmpty) {
    push('');
    push('String _writeTempFile(List<int> bytes, String suffix) {');
    push("  final dir = Directory.systemTemp.createTempSync('openskp_codegen_');");
    push("  final path = '\${dir.path}\${Platform.pathSeparator}texture\$suffix';");
    push('  File(path).writeAsBytesSync(bytes);');
    push('  return path;');
    push('}');
  }

  if (facesSkippedDegenerate > 0) {
    lines.insert(0,
        '// $facesSkippedDegenerate degenerate face(s) (fewer than 3 resolvable vertices) were skipped during generation.');
  }
  // dart:io/dart:convert only needed when at least one textured material
  // writes a temp file + base64-decodes its embedded bytes - inserted
  // after the whole body is built so texturedMats is fully populated.
  if (texturedMats.isNotEmpty) {
    lines.insert(1, "import 'dart:convert';");
    lines.insert(2, "import 'dart:io';");
  }

  return '${lines.join('\n')}\n';
}

String _extOf(String filename) {
  final base = filename.split(RegExp(r'[\\/]')).last;
  final dot = base.lastIndexOf('.');
  if (dot < 0 || dot == base.length - 1) return '.png';
  return base.substring(dot);
}

String _dartString(String s) {
  final escaped = s
      .replaceAll('\\', '\\\\')
      .replaceAll("'", "\\'")
      .replaceAll('\n', '\\n')
      .replaceAll('\r', '\\r')
      .replaceAll('\$', '\\\$');
  return "'$escaped'";
}

String _dartComment(String s) => s.replaceAll('*/', '* /');
