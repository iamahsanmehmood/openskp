import 'dart:io';
import 'dart:math' as math;
import 'curves.dart';
import 'ifc_attr_counts.dart';
import 'scene.dart';

const double metresToInches = 39.37007874015748;
const double metresToMm = 1000.0;
const _ifcBase64 =
    '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_\$';

String generateIfcGuid() {
  final rand = math.Random();
  final bytes = List<int>.generate(16, (_) => rand.nextInt(256));
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // RFC 4122 variant
  var u = BigInt.zero;
  for (final b in bytes) {
    u = (u << 8) | BigInt.from(b);
  }
  final chars = List<String>.filled(22, '');
  final mask = BigInt.from(63);
  for (var i = 21; i >= 0; i--) {
    chars[i] = _ifcBase64[(u & mask).toInt()];
    u >>= 6;
  }
  return chars.join();
}

String stepText(String text) {
  final out = StringBuffer();
  final run = <int>[];
  var wide = false;

  void flush() {
    if (run.isEmpty) return;
    out.write(wide ? r'\X4\' : r'\X2\');
    final width = wide ? 8 : 4;
    for (final cp in run) {
      out.write(cp.toRadixString(16).toUpperCase().padLeft(width, '0'));
    }
    out.write(r'\X0\');
    run.clear();
  }

  for (final cp in text.runes) {
    if (cp >= 0x20 && cp <= 0x7e) {
      flush();
      if (cp == 0x27) {
        out.write("''");
      } else if (cp == 0x5c) {
        out.write(r'\\');
      } else {
        out.writeCharCode(cp);
      }
    } else {
      run.add(cp);
      if (cp > 0xffff) {
        wide = true;
      }
    }
  }
  flush();
  return out.toString();
}

final _asciiEdgeWs = RegExp(r'^[ \t\r\n]+|[ \t\r\n]+$');

String _trimAsciiWs(String? s) {
  if (s == null || s.isEmpty) return '';
  return s.replaceAll(_asciiEdgeWs, '');
}

String sanitizeName(String? name) {
  final clean = _trimAsciiWs(name);
  if (clean.isEmpty) return 'Unnamed';
  return stepText(clean);
}

List<String>? _classifyByKeyword(String name) {
  final l = name.toLowerCase();
  if (l.contains('wall')) return ['IFCWALL', 'IfcWall'];
  if (l.contains('door')) return ['IFCDOOR', 'IfcDoor'];
  if (l.contains('window')) return ['IFCWINDOW', 'IfcWindow'];
  if (l.contains('slab') || l.contains('floor')) return ['IFCSLAB', 'IfcSlab'];
  if (l.contains('column') || l.contains('pillar')) {
    return ['IFCCOLUMN', 'IfcColumn'];
  }
  if (l.contains('beam') || l.contains('joist')) return ['IFCBEAM', 'IfcBeam'];
  if (l.contains('roof')) return ['IFCROOF', 'IfcRoof'];
  return null;
}

/// Maps a geometry/component name to an IFC4 entity type and constructor.
///
/// Tries [geomName] first, then falls back to [layerName], then (when
/// [classifyUsingFullPath] is `true`) to [pathName], and finally to a
/// generic, untyped element if nothing matches.
List<String> classifyElement(
  String geomName, [
  String layerName = '',
  String pathName = '',
  bool classifyUsingFullPath = false,
]) {
  final byName = _classifyByKeyword(geomName);
  if (byName != null) return byName;
  if (layerName.isNotEmpty) {
    final byLayer = _classifyByKeyword(layerName);
    if (byLayer != null) return byLayer;
  }
  if (classifyUsingFullPath && pathName.isNotEmpty) {
    final byPath = _classifyByKeyword(pathName);
    if (byPath != null) return byPath;
  }
  return ['IFCBUILDINGELEMENTPROXY', 'IfcBuildingElementProxy'];
}

List<double> getPrimRgb(Scene scene, int primMatIdx) {
  double r = 0.8, g = 0.8, b = 0.8, a = 1.0;
  if (primMatIdx >= 0 && primMatIdx < scene.gltfMaterials.length) {
    final mat = scene.gltfMaterials[primMatIdx];
    if (mat['pbrMetallicRoughness'] is Map) {
      final pbr = mat['pbrMetallicRoughness'] as Map;
      if (pbr['baseColorFactor'] is List) {
        final vec = pbr['baseColorFactor'] as List;
        if (vec.length >= 3) {
          r = (vec[0] as num).toDouble().clamp(0.0, 1.0);
          g = (vec[1] as num).toDouble().clamp(0.0, 1.0);
          b = (vec[2] as num).toDouble().clamp(0.0, 1.0);
          if (vec.length >= 4) {
            a = (vec[3] as num).toDouble().clamp(0.0, 1.0);
          }
        }
      }
    }
  }
  return [r, g, b, a];
}

double _weldCoord(double v) {
  final r = round6(v);
  return r == 0.0 ? 0.0 : r;
}

bool _meshIsClosed(GlbPrimitive prim) {
  final vCount = prim.positions.length ~/ 3;
  final triCount = prim.indices.length ~/ 3;
  if (vCount == 0 || triCount == 0) return false;

  final weld = <(double, double, double), int>{};
  final remap = List<int>.filled(vCount, 0);
  for (var i = 0; i < vCount; i++) {
    final key = (
      _weldCoord(prim.positions[i * 3]),
      _weldCoord(prim.positions[i * 3 + 1]),
      _weldCoord(prim.positions[i * 3 + 2]),
    );
    remap[i] = weld.putIfAbsent(key, () => weld.length);
  }

  final edgeUse = <(int, int), int>{};
  for (var i = 0; i < triCount; i++) {
    final i0 = prim.indices[i * 3];
    final i1 = prim.indices[i * 3 + 1];
    final i2 = prim.indices[i * 3 + 2];
    if (i0 >= vCount || i1 >= vCount || i2 >= vCount) return false;
    final a = remap[i0];
    final b = remap[i1];
    final c = remap[i2];
    for (final (u, v) in [(a, b), (b, c), (c, a)]) {
      final ek = u < v ? (u, v) : (v, u);
      edgeUse[ek] = (edgeUse[ek] ?? 0) + 1;
    }
  }

  return edgeUse.isNotEmpty && edgeUse.values.every((n) => n == 2);
}

String _fmtReal6(double v) {
  if (!v.isFinite) {
    throw ArgumentError('IFC coordinates must be finite');
  }
  final s = v.toStringAsFixed(6);
  final eIdx = s.indexOf('e');
  if (eIdx == -1) return s;
  final mant = s.substring(0, eIdx);
  final exp = s.substring(eIdx + 1);
  return '${mant.contains('.') ? mant : '$mant.0'}E$exp';
}

String _ifcCoord(double x, double y, double z, double scale) {
  if (!x.isFinite || !y.isFinite || !z.isFinite) {
    throw ArgumentError('IFC coordinates must be finite');
  }
  final vx = _fmtReal6(x * scale);
  final vy = _fmtReal6(-z * scale);
  final vz = _fmtReal6(y * scale);
  return '($vx,$vy,$vz)';
}

const _ifc2x3RequiredNotDefinedAttr8 = <String>{
  'IFCROOF',
  'IFCFOOTING',
  'IFCPILE',
  'IFCRAMP',
  'IFCSTAIR',
};

typedef _SolvedArcSeg = ({
  (double, double, double) center,
  double radius,
  (double, double, double) axisZ,
  (double, double, double) refX,
  double sweep,
});

_SolvedArcSeg? _solveIfc2x3ArcSegment(
  (double, double, double) p0,
  (double, double, double) pm,
  (double, double, double) p1,
) {
  final ux = pm.$1 - p0.$1, uy = pm.$2 - p0.$2, uz = pm.$3 - p0.$3;
  final vx = p1.$1 - p0.$1, vy = p1.$2 - p0.$2, vz = p1.$3 - p0.$3;
  final wx = uy * vz - uz * vy;
  final wy = uz * vx - ux * vz;
  final wz = ux * vy - uy * vx;
  final u2 = ux * ux + uy * uy + uz * uz;
  final v2 = vx * vx + vy * vy + vz * vz;
  final w2 = wx * wx + wy * wy + wz * wz;
  if (u2 <= 0.0 || v2 <= 0.0 || w2 <= 1e-24 * math.max(1.0, u2 * v2)) {
    return null;
  }

  final vxwX = vy * wz - vz * wy;
  final vxwY = vz * wx - vx * wz;
  final vxwZ = vx * wy - vy * wx;
  final wxuX = wy * uz - wz * uy;
  final wxuY = wz * ux - wx * uz;
  final wxuZ = wx * uy - wy * ux;

  final inv2w2 = 0.5 / w2;
  final ox = (u2 * vxwX + v2 * wxuX) * inv2w2;
  final oy = (u2 * vxwY + v2 * wxuY) * inv2w2;
  final oz = (u2 * vxwZ + v2 * wxuZ) * inv2w2;
  final radius = math.sqrt(ox * ox + oy * oy + oz * oz);
  if (!radius.isFinite || radius <= 0.0) return null;

  final cx = p0.$1 + ox, cy = p0.$2 + oy, cz = p0.$3 + oz;
  final invR = 1.0 / radius;
  final xx = -ox * invR, xy = -oy * invR, xz = -oz * invR;
  final invW = 1.0 / math.sqrt(w2);
  final zx = wx * invW, zy = wy * invW, zz = wz * invW;
  final yx = zy * xz - zz * xy;
  final yy = zz * xx - zx * xz;
  final yz = zx * xy - zy * xx;

  final d1x = (p1.$1 - cx) * invR;
  final d1y = (p1.$2 - cy) * invR;
  final d1z = (p1.$3 - cz) * invR;
  var sweep = math.atan2(
    d1x * yx + d1y * yy + d1z * yz,
    d1x * xx + d1y * xy + d1z * xz,
  );
  if (sweep <= 0.0) sweep += 2.0 * math.pi;
  if (!sweep.isFinite || sweep <= 0.0 || sweep >= 2.0 * math.pi) return null;

  return (
    center: (cx, cy, cz),
    radius: radius,
    axisZ: (zx, zy, zz),
    refX: (xx, xy, xz),
    sweep: sweep,
  );
}

/**
 * Serialize a baked Scene into ISO-10303-21 STEP ASCII IFC4 format.
 */
String toIfc(
  Scene scene, {
  double scale = metresToMm,
  String schema = 'IFC4',
  List<String> Function(String geomName, String layerName)? classifier,
  bool classifyUsingFullPath = false,
}) {
  if (!scale.isFinite) {
    throw ArgumentError('IFC scale must be finite');
  }
  final List<String> Function(String, String, String) classify =
      classifier != null
          ? (name, layer, _) => classifier(name, layer)
          : (name, layer, path) =>
              classifyElement(name, layer, path, classifyUsingFullPath);
  final schemaStr = schema.isEmpty ? 'IFC4' : schema.toUpperCase();
  final isIfc2x3 = schemaStr.startsWith('IFC2X3');
  final nowIso = DateTime.now().toUtc().toIso8601String().split('.').first;
  final timestampEpoch = DateTime.now().toUtc().millisecondsSinceEpoch ~/ 1000;

  final lines = <String>[
    'ISO-10303-21;',
    'HEADER;',
    "FILE_DESCRIPTION(('ViewDefinition [CoordinationView]'),'2;1');",
    "FILE_NAME('model.ifc','$nowIso',('OpenSKP Author'),('OpenSKP Organization'),'OpenSKP IFC Exporter','OpenSKP','');",
    "FILE_SCHEMA(('$schemaStr'));",
    'ENDSEC;',
    'DATA;',
  ];

  var entityId = 1;
  int nextId() => entityId++;

  final personId = nextId();
  lines.add('#$personId=IFCPERSON(\$,\$,\'OpenSKP User\',\$,\$,\$,\$,\$);');

  final orgId = nextId();
  lines.add('#$orgId=IFCORGANIZATION(\$,\'OpenSKP\',\$,\$,\$);');

  final personOrgId = nextId();
  lines.add('#$personOrgId=IFCPERSONANDORGANIZATION(#$personId,#$orgId,\$);');

  final appId = nextId();
  lines.add(
      '#$appId=IFCAPPLICATION(#$orgId,\'0.3.1\',\'OpenSKP Exporter\',\'OpenSKP\');');

  final ownerHistId = nextId();
  lines.add(
      '#$ownerHistId=IFCOWNERHISTORY(#$personOrgId,#$appId,\$,.NOCHANGE.,\$,\$,\$,$timestampEpoch);');

  final lengthUnitId = nextId();
  lines.add('#$lengthUnitId=IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.);');

  final angleUnitId = nextId();
  lines.add('#$angleUnitId=IFCSIUNIT(*,.PLANEANGLEUNIT.,\$,.RADIAN.);');

  final solidUnitId = nextId();
  lines.add('#$solidUnitId=IFCSIUNIT(*,.SOLIDANGLEUNIT.,\$,.STERADIAN.);');

  final unitAssignId = nextId();
  lines.add(
      '#$unitAssignId=IFCUNITASSIGNMENT((#$lengthUnitId,#$angleUnitId,#$solidUnitId));');

  final ptZeroId = nextId();
  lines.add('#$ptZeroId=IFCCARTESIANPOINT((0.0,0.0,0.0));');

  final axisPlacementId = nextId();
  lines.add('#$axisPlacementId=IFCAXIS2PLACEMENT3D(#$ptZeroId,\$,\$);');

  final geomCtxId = nextId();
  lines.add(
      '#$geomCtxId=IFCGEOMETRICREPRESENTATIONCONTEXT(\$,\'Model\',3,1.0E-5,#$axisPlacementId,\$);');

  final annCtxId = nextId();
  lines.add(
      '#$annCtxId=IFCGEOMETRICREPRESENTATIONSUBCONTEXT(\'Annotation\',\'Model\',*,*,*,*,#$geomCtxId,\$,.MODEL_VIEW.,\$);');

  final projId = nextId();
  lines.add(
      '#$projId=IFCPROJECT(\'${generateIfcGuid()}\',#$ownerHistId,\'OpenSKP Project\',\$,\$,\$,\$,(#$geomCtxId),#$unitAssignId);');

  final sitePlacementId = nextId();
  lines.add('#$sitePlacementId=IFCLOCALPLACEMENT(\$,#$axisPlacementId);');

  final siteId = nextId();
  lines.add(
      '#$siteId=IFCSITE(\'${generateIfcGuid()}\',#$ownerHistId,\'Site\',\$,\$,#$sitePlacementId,\$,\$,.ELEMENT.,\$,\$,\$,\$,\$);');

  final bldgPlacementId = nextId();
  lines.add(
      '#$bldgPlacementId=IFCLOCALPLACEMENT(#$sitePlacementId,#$axisPlacementId);');

  final bldgId = nextId();
  lines.add(
      '#$bldgId=IFCBUILDING(\'${generateIfcGuid()}\',#$ownerHistId,\'Building\',\$,\$,#$bldgPlacementId,\$,\$,.ELEMENT.,\$,\$,\$);');

  final storeyPlacementId = nextId();
  lines.add(
      '#$storeyPlacementId=IFCLOCALPLACEMENT(#$bldgPlacementId,#$axisPlacementId);');

  final storeyId = nextId();
  lines.add(
      '#$storeyId=IFCBUILDINGSTOREY(\'${generateIfcGuid()}\',#$ownerHistId,\'Level 0\',\$,\$,#$storeyPlacementId,\$,\$,.ELEMENT.,0.0);');

  lines.add(
      '#${nextId()}=IFCRELAGGREGATES(\'${generateIfcGuid()}\',#$ownerHistId,\$,\$,#$projId,(#$siteId));');
  lines.add(
      '#${nextId()}=IFCRELAGGREGATES(\'${generateIfcGuid()}\',#$ownerHistId,\$,\$,#$siteId,(#$bldgId));');
  lines.add(
      '#${nextId()}=IFCRELAGGREGATES(\'${generateIfcGuid()}\',#$ownerHistId,\$,\$,#$bldgId,(#$storeyId));');

  final productIds = <int>[];
  final layerItems = <String, (String, List<int>)>{};
  final matStyleCache = <String, int>{};

  void addLayerItem(String rawLayer, int itemId) {
    final hasLayer = _trimAsciiWs(rawLayer).isNotEmpty;
    final raw = hasLayer ? rawLayer : 'Layer0';
    final key = hasLayer ? sanitizeName(rawLayer) : 'Layer0';
    layerItems.putIfAbsent(key, () => (raw, <int>[])).$2.add(itemId);
  }

  void writePset(int productId, String psetName, Map<String, String> props) {
    if (props.isEmpty) return;

    final propValIds = <int>[];
    for (final entry in props.entries) {
      final cleanK = sanitizeName(entry.key);
      final cleanV = sanitizeName(entry.value);
      final propId = nextId();
      lines.add(
          '#$propId=IFCPROPERTYSINGLEVALUE(\'$cleanK\',\$,IFCTEXT(\'$cleanV\'),\$);');
      propValIds.add(propId);
    }

    final psetId = nextId();
    final propRefs = propValIds.map((pid) => '#$pid').join(',');
    lines.add(
        '#$psetId=IFCPROPERTYSET(\'${generateIfcGuid()}\',#$ownerHistId,\'${sanitizeName(psetName)}\',\$,($propRefs));');
    lines.add(
        '#${nextId()}=IFCRELDEFINESBYPROPERTIES(\'${generateIfcGuid()}\',#$ownerHistId,\$,\$,(#$productId),#$psetId);');
  }

  for (final cs in scene.curveSets) {
    if (cs.pointsM.length < 2) continue;
    for (final p in cs.pointsM) {
      if (!p.$1.isFinite || !p.$2.isFinite || !p.$3.isFinite) {
        throw ArgumentError('IFC coordinates must be finite');
      }
    }

    int? curveId;
    final arc = cs.arc;
    if (arc != null &&
        arc.points.length >= 3 &&
        (!isIfc2x3 || arc.segments.isNotEmpty)) {
      if (!isIfc2x3) {
        final segs = arc.segments
            .map((s) => 'IFCARCINDEX((${s.$1 + 1},${s.$2 + 1},${s.$3 + 1}))')
            .join(',');
        final coords =
            arc.points.map((p) => _ifcCoord(p.$1, p.$2, p.$3, scale)).join(',');
        final ptListId = nextId();
        lines.add('#$ptListId=IFCCARTESIANPOINTLIST3D(($coords));');
        curveId = nextId();
        lines.add('#$curveId=IFCINDEXEDPOLYCURVE(#$ptListId,($segs),.F.);');
      } else {
        final arcPtsZup = <(double, double, double)>[];
        for (final p in arc.points) {
          if (!p.$1.isFinite || !p.$2.isFinite || !p.$3.isFinite) {
            throw ArgumentError('IFC coordinates must be finite');
          }
          final sx = p.$1 * scale, sy = -p.$3 * scale, sz = p.$2 * scale;
          if (!sx.isFinite || !sy.isFinite || !sz.isFinite) {
            throw ArgumentError('IFC coordinates must be finite');
          }
          arcPtsZup.add((sx, sy, sz));
        }

        final solvedSegs = <_SolvedArcSeg>[];
        var validArc = true;
        for (final s in arc.segments) {
          if (s.$1 < 0 ||
              s.$1 >= arcPtsZup.length ||
              s.$2 < 0 ||
              s.$2 >= arcPtsZup.length ||
              s.$3 < 0 ||
              s.$3 >= arcPtsZup.length) {
            validArc = false;
            break;
          }
          final solved = _solveIfc2x3ArcSegment(
            arcPtsZup[s.$1],
            arcPtsZup[s.$2],
            arcPtsZup[s.$3],
          );
          if (solved == null) {
            validArc = false;
            break;
          }
          solvedSegs.add(solved);
        }

        int emitCircle(_SolvedArcSeg seg) {
          final cId = nextId();
          lines.add(
              '#$cId=IFCCARTESIANPOINT((${_fmtReal6(seg.center.$1)},${_fmtReal6(seg.center.$2)},${_fmtReal6(seg.center.$3)}));');
          final zId = nextId();
          lines.add(
              '#$zId=IFCDIRECTION((${_fmtReal6(seg.axisZ.$1)},${_fmtReal6(seg.axisZ.$2)},${_fmtReal6(seg.axisZ.$3)}));');
          final xId = nextId();
          lines.add(
              '#$xId=IFCDIRECTION((${_fmtReal6(seg.refX.$1)},${_fmtReal6(seg.refX.$2)},${_fmtReal6(seg.refX.$3)}));');
          final plId = nextId();
          lines.add('#$plId=IFCAXIS2PLACEMENT3D(#$cId,#$zId,#$xId);');
          final circId = nextId();
          lines.add('#$circId=IFCCIRCLE(#$plId,${_fmtReal6(seg.radius)});');
          return circId;
        }

        if (validArc && solvedSegs.length == 1) {
          final circId = emitCircle(solvedSegs[0]);
          curveId = nextId();
          lines.add(
              '#$curveId=IFCTRIMMEDCURVE(#$circId,(IFCPARAMETERVALUE(0.0)),(IFCPARAMETERVALUE(${_fmtReal6(solvedSegs[0].sweep)})),.T.,.PARAMETER.);');
        } else if (validArc &&
            solvedSegs.length == 2 &&
            arc.segments[0] == (0, 1, 2) &&
            arc.segments[1] == (2, 3, 0) &&
            (solvedSegs[0].radius - solvedSegs[1].radius).abs() <=
                1e-3 * math.max(1.0, solvedSegs[0].radius)) {
          final dcx = solvedSegs[0].center.$1 - solvedSegs[1].center.$1;
          final dcy = solvedSegs[0].center.$2 - solvedSegs[1].center.$2;
          final dcz = solvedSegs[0].center.$3 - solvedSegs[1].center.$3;
          if (math.sqrt(dcx * dcx + dcy * dcy + dcz * dcz) <=
              1e-3 * math.max(1.0, solvedSegs[0].radius)) {
            curveId = emitCircle(solvedSegs[0]);
          }
        }
        if (validArc && curveId == null && solvedSegs.length >= 2) {
          final compSegIds = <int>[];
          for (final seg in solvedSegs) {
            final circId = emitCircle(seg);
            final trimId = nextId();
            lines.add(
                '#$trimId=IFCTRIMMEDCURVE(#$circId,(IFCPARAMETERVALUE(0.0)),(IFCPARAMETERVALUE(${_fmtReal6(seg.sweep)})),.T.,.PARAMETER.);');
            final csegId = nextId();
            lines.add(
                '#$csegId=IFCCOMPOSITECURVESEGMENT(.CONTINUOUS.,.T.,#$trimId);');
            compSegIds.add(csegId);
          }
          curveId = nextId();
          final csegRefs = compSegIds.map((sid) => '#$sid').join(',');
          lines.add('#$curveId=IFCCOMPOSITECURVE(($csegRefs),.F.);');
        }
      }
    }
    if (curveId == null) {
      final coords =
          cs.pointsM.map((p) => _ifcCoord(p.$1, p.$2, p.$3, scale)).toList();
      if (cs.closed) {
        coords.add(coords.first);
      }
      final ptIds = <int>[];
      for (final c in coords) {
        final ptId = nextId();
        lines.add('#$ptId=IFCCARTESIANPOINT($c);');
        ptIds.add(ptId);
      }
      curveId = nextId();
      final ptRefs = ptIds.map((pid) => '#$pid').join(',');
      lines.add('#$curveId=IFCPOLYLINE(($ptRefs));');
    }

    final curveSetId = nextId();
    lines.add('#$curveSetId=IFCGEOMETRICCURVESET((#$curveId));');

    addLayerItem(cs.layer, curveId);

    final annName = sanitizeName(cs.name.isEmpty ? 'Curve' : cs.name);
    final annRepId = nextId();
    lines.add(
        '#$annRepId=IFCSHAPEREPRESENTATION(#$annCtxId,\'Annotation\',\'GeometricCurveSet\',(#$curveSetId));');
    final annShapeId = nextId();
    lines.add('#$annShapeId=IFCPRODUCTDEFINITIONSHAPE(\$,\$,(#$annRepId));');
    final annPlacementId = nextId();
    lines.add(
        '#$annPlacementId=IFCLOCALPLACEMENT(#$storeyPlacementId,#$axisPlacementId);');
    final annId = nextId();
    lines.add(
        '#$annId=IFCANNOTATION(\'${generateIfcGuid()}\',#$ownerHistId,\'$annName\',\$,\$,#$annPlacementId,#$annShapeId);');

    for (final dictEntry in cs.attributeDictionaries.entries) {
      writePset(annId, 'Pset_${dictEntry.key}', dictEntry.value);
    }
    productIds.add(annId);
  }

  for (final prim in scene.surfacePrimitives) {
    final triCount = prim.indices.length ~/ 3;
    final vCount = prim.positions.length ~/ 3;
    if (triCount == 0 || vCount == 0) continue;

    final meta = scene.meshIndex[prim.geomName];
    final rawNameSrc =
        (meta != null && meta.name.isNotEmpty) ? meta.name : prim.geomName;
    final trimmedName = _trimAsciiWs(rawNameSrc);
    final rawName = trimmedName.isEmpty ? 'Unnamed' : trimmedName;
    final displayName = sanitizeName(rawName);
    final rawLayer = (meta != null && meta.layer.isNotEmpty) ? meta.layer : '';
    final trimmedLayer = _trimAsciiWs(rawLayer);
    final classifyLayer = trimmedLayer.isEmpty ? 'Layer0' : trimmedLayer;
    final pathName = meta?.path ?? '';
    final classification = classify(rawName, classifyLayer, pathName);
    final stepType = classification[0];

    final ptCoords = <String>[];
    for (var i = 0; i < vCount; i++) {
      ptCoords.add(_ifcCoord(
        prim.positions[i * 3],
        prim.positions[i * 3 + 1],
        prim.positions[i * 3 + 2],
        scale,
      ));
    }

    int faceSetId;
    String bodyRepType;
    if (!isIfc2x3) {
      final ptListId = nextId();
      lines.add('#$ptListId=IFCCARTESIANPOINTLIST3D((${ptCoords.join(',')}));');

      final faceIndices = <String>[];
      for (var i = 0; i < triCount; i++) {
        final idx0 = prim.indices[i * 3] + 1;
        final idx1 = prim.indices[i * 3 + 1] + 1;
        final idx2 = prim.indices[i * 3 + 2] + 1;
        faceIndices.add('($idx0,$idx1,$idx2)');
      }

      final closed = _meshIsClosed(prim) ? '.T.' : '.F.';
      faceSetId = nextId();
      lines.add(
          '#$faceSetId=IFCTRIANGULATEDFACESET(#$ptListId,\$,$closed,(${faceIndices.join(',')}),\$);');
      bodyRepType = 'Tessellation';
    } else {
      final coordToUid = <String, int>{};
      final uniqueCoords = <String>[];
      final vToUid = List<int>.filled(vCount, 0);
      for (var i = 0; i < vCount; i++) {
        final cStr = ptCoords[i];
        var uid = coordToUid[cStr];
        if (uid == null) {
          uid = uniqueCoords.length;
          coordToUid[cStr] = uid;
          uniqueCoords.add(cStr);
        }
        vToUid[i] = uid;
      }

      final validTris = <(int, int, int)>[];
      var outOfRange = false;
      for (var i = 0; i < triCount; i++) {
        final i0 = prim.indices[i * 3];
        final i1 = prim.indices[i * 3 + 1];
        final i2 = prim.indices[i * 3 + 2];
        if (i0 >= vCount || i1 >= vCount || i2 >= vCount) {
          outOfRange = true;
          continue;
        }
        final u0 = vToUid[i0], u1 = vToUid[i1], u2 = vToUid[i2];
        if (u0 == u1 || u1 == u2 || u2 == u0) continue;
        validTris.add((u0, u1, u2));
      }
      if (validTris.isEmpty) continue;

      final usedPtId = List<int>.filled(uniqueCoords.length, 0);
      for (final (u0, u1, u2) in validTris) {
        for (final uid in [u0, u1, u2]) {
          if (usedPtId[uid] == 0) {
            final ptId = nextId();
            lines.add('#$ptId=IFCCARTESIANPOINT(${uniqueCoords[uid]});');
            usedPtId[uid] = ptId;
          }
        }
      }

      final faceIds = <int>[];
      final halfEdges = <(int, int), int>{};
      for (final (u0, u1, u2) in validTris) {
        for (final he in [(u0, u1), (u1, u2), (u2, u0)]) {
          halfEdges[he] = (halfEdges[he] ?? 0) + 1;
        }
        final loopId = nextId();
        lines.add(
            '#$loopId=IFCPOLYLOOP((#${usedPtId[u0]},#${usedPtId[u1]},#${usedPtId[u2]}));');
        final boundId = nextId();
        lines.add('#$boundId=IFCFACEOUTERBOUND(#$loopId,.T.);');
        final faceId = nextId();
        lines.add('#$faceId=IFCFACE((#$boundId));');
        faceIds.add(faceId);
      }

      final isClosedSolid = !outOfRange &&
          validTris.length >= 4 &&
          halfEdges.entries.every(
              (e) => e.value == 1 && halfEdges[(e.key.$2, e.key.$1)] == 1);
      final faceRefs = faceIds.map((fid) => '#$fid').join(',');
      if (isClosedSolid) {
        final shellId = nextId();
        lines.add('#$shellId=IFCCLOSEDSHELL(($faceRefs));');
        faceSetId = nextId();
        lines.add('#$faceSetId=IFCFACETEDBREP(#$shellId);');
        bodyRepType = 'Brep';
      } else {
        final cfsId = nextId();
        lines.add('#$cfsId=IFCCONNECTEDFACESET(($faceRefs));');
        faceSetId = nextId();
        lines.add('#$faceSetId=IFCFACEBASEDSURFACEMODEL((#$cfsId));');
        bodyRepType = 'SurfaceModel';
      }
    }

    addLayerItem(rawLayer, faceSetId);

    final rgba = getPrimRgb(scene, prim.materialIndex);
    final r = rgba[0], g = rgba[1], b = rgba[2], a = rgba[3];
    final rgbaKey =
        '${r.toStringAsFixed(4)},${g.toStringAsFixed(4)},${b.toStringAsFixed(4)},${a.toStringAsFixed(4)}';
    int styleAssignId;

    if (!matStyleCache.containsKey(rgbaKey)) {
      final colId = nextId();
      lines.add(
          '#$colId=IFCCOLOURRGB(\$,${r.toStringAsFixed(4)},${g.toStringAsFixed(4)},${b.toStringAsFixed(4)});');

      final transparency = (1.0 - a).toStringAsFixed(4);
      final renderingId = nextId();
      lines.add(
          '#$renderingId=IFCSURFACESTYLERENDERING(#$colId,$transparency,\$,\$,\$,\$,\$,\$,.FLAT.);');

      final styleId = nextId();
      lines.add(
          '#$styleId=IFCSURFACESTYLE(\'${displayName}_Material\',.BOTH.,(#$renderingId));');

      styleAssignId = nextId();
      lines.add('#$styleAssignId=IFCPRESENTATIONSTYLEASSIGNMENT((#$styleId));');
      matStyleCache[rgbaKey] = styleAssignId;
    } else {
      styleAssignId = matStyleCache[rgbaKey]!;
    }

    final styledItemId = nextId();
    lines
        .add('#$styledItemId=IFCSTYLEDITEM(#$faceSetId,(#$styleAssignId),\$);');

    final shapeRepId = nextId();
    lines.add(
        '#$shapeRepId=IFCSHAPEREPRESENTATION(#$geomCtxId,\'Body\',\'$bodyRepType\',(#$faceSetId));');

    final prodShapeId = nextId();
    lines.add('#$prodShapeId=IFCPRODUCTDEFINITIONSHAPE(\$,\$,(#$shapeRepId));');

    final prodPlacementId = nextId();
    lines.add(
        '#$prodPlacementId=IFCLOCALPLACEMENT(#$storeyPlacementId,#$axisPlacementId);');

    final productId = nextId();
    final attrs = <String>[
      '\'${generateIfcGuid()}\'',
      '#$ownerHistId',
      '\'$displayName\'',
      '\$',
      '\$',
      '#$prodPlacementId',
      '#$prodShapeId',
      '\$',
    ];
    final declared = ifcAttrCount(schemaStr, stepType);
    final total = math.max(
        attrs.length, (declared != null && declared > 0) ? declared : 9);
    while (attrs.length < total) {
      attrs.add('\$');
    }
    if (isIfc2x3) {
      if (stepType == 'IFCBUILDINGELEMENTPROXY' && total == 9) {
        attrs[8] = '.ELEMENT.';
      } else if (_ifc2x3RequiredNotDefinedAttr8.contains(stepType) &&
          total >= 9) {
        attrs[8] = '.NOTDEFINED.';
      }
    } else if (stepType == 'IFCBUILDINGELEMENTPROXY' && total == 9) {
      attrs[8] = '.NOTDEFINED.';
    }
    lines.add('#$productId=$stepType(${attrs.join(',')});');
    productIds.add(productId);

    if (meta != null) {
      writePset(productId, 'Pset_CustomProperties', meta.properties);
      for (final dictEntry in meta.attributeDictionaries.entries) {
        writePset(productId, 'Pset_${dictEntry.key}', dictEntry.value);
      }
    }
  }

  final sortedLayerNames = layerItems.keys.toList()..sort();
  for (final lName in sortedLayerNames) {
    final entry = layerItems[lName]!;
    if (entry.$2.isNotEmpty) {
      final itemRefs = entry.$2.map((iid) => '#$iid').join(',');
      final layerOn = scene.layerHidden[entry.$1] == true ? '.F.' : '.T.';
      lines.add(
          '#${nextId()}=IFCPRESENTATIONLAYERWITHSTYLE(\'$lName\',\$,($itemRefs),\$,$layerOn,.F.,.F.,());');
    }
  }

  if (productIds.isNotEmpty) {
    final prodRefs = productIds.map((pid) => '#$pid').join(',');
    lines.add(
        '#${nextId()}=IFCRELCONTAINEDINSPATIALSTRUCTURE(\'${generateIfcGuid()}\',#$ownerHistId,\$,\$,($prodRefs),#$storeyId);');
  }

  lines.add('ENDSEC;');
  lines.add('END-ISO-10303-21;');
  return '${lines.join('\r\n')}\r\n';
}

void exportIfc(
  Scene scene,
  String outputPath, {
  double scale = metresToMm,
  String schema = 'IFC4',
  List<String> Function(String geomName, String layerName)? classifier,
  bool classifyUsingFullPath = false,
}) {
  final text = toIfc(
    scene,
    scale: scale,
    schema: schema,
    classifier: classifier,
    classifyUsingFullPath: classifyUsingFullPath,
  );
  final file = File(outputPath);
  file.parent.createSync(recursive: true);
  file.writeAsStringSync(text);
}
