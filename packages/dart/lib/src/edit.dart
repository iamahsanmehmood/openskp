/// Load an existing legacy-format `.skp` file and rebuild it as a new,
/// independent [SkpBuilder].
///
/// [create] only ever builds a brand-new file by splicing new geometry into
/// its own bundled blank scaffold - there is no way to append to or patch
/// an arbitrary existing file's bytes in place, because real SketchUp
/// itself doesn't do that either: it fully re-serializes the whole
/// document on every save, so there is no stable "original bytes +
/// appended bytes" structure to target for a file this project didn't
/// create.
///
/// This module takes the other viable approach instead: fully parse the
/// existing file with this package's own reader ([legacy], already
/// comprehensive), then *replay* everything it understood back through the
/// writer's own public API (materials, layers, every component definition,
/// every face/instance) to produce a brand-new file - not a byte-patched
/// copy of the original, but a freshly-built one with equivalent content,
/// to which the caller can add more geometry before saving. Ported
/// field-for-field from `openskp.edit` (Python).
///
/// **Adding more geometry after the fact.** The returned builder can take
/// more `addFace`/`addCircle`/`addInstance`/etc. calls, and every material/
/// layer the source had is already reachable via
/// `builder.materialsByName`/`builder.layersByName` - [OpenExistingResult]
/// also carries a `definitions` map from each component definition's name
/// to its builder, for placing more instances of something the source
/// already defined. What the returned builder can no longer do is register
/// a genuinely NEW material, layer, or component definition/group -
/// [create]'s own file-format ordering requirement (materials/layers/
/// definitions must all be finalized before any geometry is written) is
/// already satisfied by the time replay finishes writing the source's own
/// root-level geometry, so `addMaterial`/`addLayer`/
/// `addComponentDefinition`/`addGroup` all throw on the returned builder.
/// Build anything new into a separate [create] call instead.
///
/// **Scope and known fidelity gaps** (every gap here is a deliberately-
/// scoped limitation, not an oversight - see [create]'s own doc comment
/// for why):
///
/// * Only a **legacy-format** (SketchUp 2013-2020) source file is accepted
///   - [create] never writes any other format, so a modern VFF (2021+)
///   source can't be faithfully round-tripped through it.
/// * Per-edge `hidden`/`soft`/`smooth` flags are applied per-FACE, not
///   per-edge (an "any edge in this boundary has the flag" approximation).
/// * A positioned texture is replayed via 3 sample-point correspondences
///   fitted to an affine map - exact at those 3 points, but a genuinely
///   projective (4-pin/distorted) source mapping won't interpolate
///   identically between them. A *projected* (draped) texture has no
///   equivalent at all and falls back to the default projection.
/// * A material's original texture tile size isn't preserved. A colorized
///   (tinted) material variant is replayed as its plain source texture,
///   losing the tint.
/// * Per-face material/layer painting: only a face's front/back *material*
///   is replayed - this package's reader doesn't expose a per-face layer
///   assignment at all.
/// * Every placed thing (originally a group or a component instance alike)
///   is replayed as a plain component instance - structurally simpler, and
///   visually identical, but no longer shows as a "Group" in SketchUp's
///   Outliner afterward.
/// * Section planes, text entities, and dimensions aren't carried over at
///   all - the writer has no support for any of these entity types.
/// * A circle/arc/polyline's original `CArcCurve`/`CCurve` grouping is
///   lost - this package's reader doesn't preserve that grouping in its
///   public [Face]/[Edge] model, so a round-tripped circle becomes an
///   ordinary straight-edged face.
/// * Definition-level and face-level custom attributes aren't reproduced -
///   the reader's public model doesn't expose either (only an instance's
///   own `properties` are).
library;

import 'dart:io';
import 'dart:math';

import 'create.dart';
import 'legacy.dart';
import 'model.dart';
import 'parser.dart';

/// Result of [openExisting]: the rebuilt [builder], any [warnings] about
/// content that couldn't be faithfully reproduced (see this library's own
/// doc comment for the exact, deliberately-scoped gaps this draws from),
/// and a [definitions] lookup from each replayed component definition's
/// own name to its (already-closed) builder.
class OpenExistingResult {
  final SkpBuilder builder;
  final List<String> warnings;
  final Map<String, ComponentDefinitionBuilder> definitions;
  OpenExistingResult(this.builder, this.warnings, this.definitions);
}

/// Parse [path] (a legacy-format `.skp` file) and rebuild it as a new
/// [SkpBuilder], replaying materials, layers, every component definition,
/// and all root-level geometry/instances. See this library's own doc
/// comment for the full scope and fidelity gaps.
///
/// Throws [SkpWriteError] if [path] isn't a legacy-format file.
OpenExistingResult openExisting(String path) {
  final raf = File(path).openSync();
  final headLen = min(raf.lengthSync(), 0x200);
  final head = raf.readSync(headLen);
  raf.closeSync();
  if (!Legacy.isLegacy(head)) {
    throw SkpWriteError(
      "'$path' is not a legacy-format (SketchUp 2013-2020) .skp file - "
      'openskp.create only ever writes that format, so only a legacy-format '
      'source file can be rebuilt through it (see this library\'s own doc '
      "comment for why an arbitrary existing file can't simply be patched)",
    );
  }
  final model = SkpFile.open(path).parse();
  final warnings = <String>[];
  final builder = create();

  final materialSlots = _replayMaterials(builder, model, warnings);
  final layerSlots = <String, int>{
    for (final layer in model.layers)
      layer.name: builder.addLayer(
        layer.name,
        color: [layer.colorR, layer.colorG, layer.colorB],
        hidden: layer.hidden,
      ),
  };

  final defBuilders = <int, ComponentDefinitionBuilder>{};
  for (final defId in _definitionOrder(model)) {
    final defn = model.definitions[defId]!;
    final context = "definition '${defn.name.isNotEmpty ? defn.name : defId}'";
    if (!_definitionHasContent(defn, defBuilders)) {
      warnings.add('$context: skipped (no replayable geometry)');
      continue;
    }
    final defName = defn.name.isNotEmpty ? defn.name : 'Definition$defId';
    final db = builder.addComponentDefinition(defName, (b) {
      _replayBody(b, defn, model, materialSlots, layerSlots, warnings, context, defBuilders);
    });
    defBuilders[defId] = db;
  }

  _replayBody(builder, model.root, model, materialSlots, layerSlots, warnings, 'root', defBuilders);

  final definitionsByName = <String, ComponentDefinitionBuilder>{};
  for (final entry in defBuilders.entries) {
    final name = model.definitions[entry.key]!.name;
    if (name.isNotEmpty) definitionsByName[name] = entry.value;
  }
  return OpenExistingResult(builder, warnings, definitionsByName);
}

Map<Material, int> _replayMaterials(SkpBuilder builder, SkpModel model, List<String> warnings) {
  final slots = <Material, int>{};
  for (final mat in model.materials) {
    int slot;
    final tex = mat.texture;
    final texData = tex?.data;
    if (tex != null && texData != null && texData.isNotEmpty) {
      var suffix = _extensionOf(tex.filename.isNotEmpty ? tex.filename : 'texture');
      if (suffix.isEmpty) suffix = '.png';
      final tmpDir = Directory.systemTemp.createTempSync('openskp_edit_');
      final tmpFile = File('${tmpDir.path}${Platform.pathSeparator}texture$suffix');
      tmpFile.writeAsBytesSync(texData);
      try {
        slot = builder.addTextureMaterial(mat.name, tmpFile.path);
      } finally {
        tmpDir.deleteSync(recursive: true);
      }
      if (tex.width != 0.0 || tex.height != 0.0) {
        warnings.add("material '${mat.name}': original texture tile size not preserved");
      }
      if (mat.colorized) {
        warnings.add("material '${mat.name}': colorized tint not reproduced (base texture only)");
      }
    } else {
      if (tex != null) {
        warnings.add("material '${mat.name}': texture image data missing - replayed as solid color");
      }
      slot = builder.addMaterial(mat.name, [mat.color.$1, mat.color.$2, mat.color.$3, mat.color.$4]);
    }
    slots[mat] = slot;
  }
  return slots;
}

String _extensionOf(String filename) {
  final base = filename.split(RegExp(r'[\\/]')).last;
  final dot = base.lastIndexOf('.');
  if (dot <= 0) return '';
  return base.substring(dot);
}

int? _materialSlot(int? materialId, SkpModel model, Map<Material, int> slots) {
  if (materialId == null) return null;
  final mat = model.materialsById[materialId];
  if (mat == null) return null;
  return slots[mat];
}

/// Topological order (dependencies before dependents) so a definition
/// nesting instances of other definitions is only replayed after those are
/// already built - the same ordering constraint
/// [ComponentDefinitionBuilder.addInstance] documents.
List<int> _definitionOrder(SkpModel model) {
  final visited = <int>{};
  final temp = <int>{};
  final order = <int>[];

  void visit(int defId) {
    if (visited.contains(defId)) return;
    if (temp.contains(defId)) {
      throw SkpWriteError('circular component-definition reference involving definition $defId');
    }
    temp.add(defId);
    final defn = model.definitions[defId];
    if (defn != null) {
      for (final inst in defn.instances) {
        final refIdx = inst.refIdx;
        if (refIdx != null && model.definitions.containsKey(refIdx)) {
          visit(refIdx);
        }
      }
    }
    temp.remove(defId);
    visited.add(defId);
    order.add(defId);
  }

  for (final defId in model.definitions.keys) {
    visit(defId);
  }
  return order;
}

Map<int, (int, int)> _edgeMap(Definition defn) {
  return {for (final e in defn.edges.entries) e.key: (e.value.v1Id, e.value.v2Id)};
}

bool _definitionHasContent(Definition defn, Map<int, ComponentDefinitionBuilder> defBuilders) {
  final edges = _edgeMap(defn);
  for (final face in defn.faces.values) {
    if (face.loops.isEmpty) continue;
    if (_reconstructLoopVertices(face.loops[0], edges).length >= 3) return true;
  }
  for (final inst in defn.instances) {
    if (inst.refIdx != null && defBuilders.containsKey(inst.refIdx)) return true;
  }
  return false;
}

/// Replay one definition's (or the root's) own faces and instances onto
/// [target] - an [SkpBuilder] for the root, or a [ComponentDefinitionBuilder]
/// for a nested definition; both satisfy [GeometryHost] the same shape this
/// calls generically. [defBuilders] resolves instance references - by the
/// time any definition is opened (topological order, see
/// [_definitionOrder]) every OTHER definition its own instances could
/// reference is already in it.
void _replayBody(
  GeometryHost target,
  Definition defn,
  SkpModel model,
  Map<Material, int> materialSlots,
  Map<String, int> layerSlots,
  List<String> warnings,
  String context,
  Map<int, ComponentDefinitionBuilder> defBuilders,
) {
  final edges = _edgeMap(defn);
  for (final face in defn.faces.values) {
    _replayFace(target, face, defn, edges, model, materialSlots, warnings, context);
  }
  for (final inst in defn.instances) {
    _replayInstance(target, inst, defBuilders, materialSlots, layerSlots, model, warnings, context);
  }
}

Point3 _vertexPoint(Definition defn, int id) {
  final v = defn.vertices[id]!;
  return (v.x, v.y, v.z);
}

void _replayFace(
  GeometryHost target,
  Face face,
  Definition defn,
  Map<int, (int, int)> edges,
  SkpModel model,
  Map<Material, int> materialSlots,
  List<String> warnings,
  String context,
) {
  if (face.loops.isEmpty) {
    warnings.add('$context: face ${face.id} has no loops - skipped');
    return;
  }
  final vertIds = _reconstructLoopVertices(face.loops[0], edges);
  if (vertIds.length < 3) {
    warnings.add('$context: face ${face.id} has fewer than 3 usable points - skipped');
    return;
  }
  final points = [for (final v in vertIds) _vertexPoint(defn, v)];

  final holes = <List<Point3>>[];
  for (var li = 1; li < face.loops.length; li++) {
    final holeVertIds = _reconstructLoopVertices(face.loops[li], edges);
    if (holeVertIds.length < 3) {
      warnings.add('$context: face ${face.id} has a hole with fewer than 3 usable points - skipped');
      return;
    }
    holes.add([for (final v in holeVertIds) _vertexPoint(defn, v)]);
  }

  final loopEdges = [
    for (final (eid, _) in face.loops[0])
      if (defn.edges.containsKey(eid)) defn.edges[eid]!,
  ];
  final hiddenEdges = loopEdges.any((e) => e.hidden);
  final softEdges = loopEdges.any((e) => e.soft);
  final smoothEdges = loopEdges.any((e) => e.smooth);

  final material = _materialSlot(face.materialId, model, materialSlots);
  final backMaterial = _materialSlot(face.backMaterialId, model, materialSlots);

  final frontUv = _replayUv(
    face.materialId, face.uvTransform, face.uvProjected, points, face.normal, model, warnings, context, 'front',
  );
  final backUv = _replayUv(
    face.backMaterialId, face.uvTransformBack, face.uvProjectedBack, points, face.normal, model, warnings, context, 'back',
  );

  try {
    target.addFace(
      points,
      material: material,
      layer: null,
      backMaterial: backMaterial,
      hidden: face.hidden,
      softEdges: softEdges,
      smoothEdges: smoothEdges,
      hiddenEdges: hiddenEdges,
      frontUv: frontUv,
      backUv: backUv,
      attributes: null,
      attributeDictName: 'attributes',
      autoTriangulate: false,
      holes: holes,
    );
  } on SkpWriteError catch (exc) {
    warnings.add('$context: face ${face.id} skipped ($exc)');
  }
}

double _tileDimension(double raw) => raw != 0.0 ? raw : 1.0;

List<(Point3, (double, double))>? _replayUv(
  int? materialId,
  List<double>? uvTransform,
  bool projected,
  List<Point3> points,
  (double, double, double)? normal,
  SkpModel model,
  List<String> warnings,
  String context,
  String side,
) {
  if (uvTransform == null) return null;
  if (projected) {
    warnings.add('$context: $side texture is projected/draped - falls back to default projection');
    return null;
  }
  if (normal == null) return null;
  final mat = materialId != null ? model.materialsById[materialId] : null;
  final tileW = _tileDimension(mat?.texture?.width ?? 0.0);
  final tileH = _tileDimension(mat?.texture?.height ?? 0.0);
  final (xr, yr) = _faceUvBasis(normal);
  final sample = points.length > 3 ? points.sublist(0, 3) : points;
  if (sample.length < 3) return null;
  final pairs = <(Point3, (double, double))>[];
  for (final p in sample) {
    final uv = _computeFaceUv(p, xr, yr, uvTransform, tileW, tileH);
    pairs.add((p, uv));
  }
  return pairs;
}

void _replayInstance(
  GeometryHost target,
  Instance inst,
  Map<int, ComponentDefinitionBuilder> defBuilders,
  Map<Material, int> materialSlots,
  Map<String, int> layerSlots,
  SkpModel model,
  List<String> warnings,
  String context,
) {
  final defBuilder = inst.refIdx != null ? defBuilders[inst.refIdx] : null;
  if (defBuilder == null) {
    warnings.add("$context: instance '${inst.name}' references unavailable definition - skipped");
    return;
  }
  Matrix3x3? matrix3x3;
  var translation = (0.0, 0.0, 0.0);
  if (inst.matrix.length >= 9) {
    matrix3x3 = (
      inst.matrix[0], inst.matrix[1], inst.matrix[2], //
      inst.matrix[3], inst.matrix[4], inst.matrix[5], //
      inst.matrix[6], inst.matrix[7], inst.matrix[8],
    );
  }
  if (inst.matrix.length >= 12) {
    translation = (inst.matrix[9], inst.matrix[10], inst.matrix[11]);
  }
  final material = _materialSlot(inst.materialId, model, materialSlots);
  final layer = inst.layer.isNotEmpty ? layerSlots[inst.layer] : null;
  try {
    target.addInstance(
      defBuilder,
      name: inst.name.isNotEmpty ? inst.name : null,
      translation: translation,
      matrix3x3: matrix3x3,
      rotation: null,
      material: material,
      layer: layer,
      attributes: inst.properties.isNotEmpty ? Map<String, Object>.from(inst.properties) : null,
      attributeDictName: 'dynamic_attributes',
      hidden: inst.hidden,
    );
  } on SkpWriteError catch (exc) {
    warnings.add("$context: instance '${inst.name}' skipped ($exc)");
  }
}

// ── UV-basis helpers for replaying an already-stored uvTransform ──────────
//
// Distinct from create.dart's own `_faceUvBasis` (which derives a basis
// from a face's first edge, for ENCODING a caller-chosen positioning): this
// is the READ-side convention (normal-only, Z-cross based) the parser
// itself used to interpret a face's stored uvTransform, duplicated here
// (matching scene.dart's private helpers of the same name/shape, and
// Python's edit.py, which imports these from openskp.scene directly) so
// replay recovers the same sample UV coordinates the original file encoded.

List<double> _invert3x3(List<double> m) {
  final a = m[0], b = m[1], c = m[2];
  final d = m[3], e = m[4], f = m[5];
  final g = m[6], h = m[7], i = m[8];
  final det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g);
  if (det.abs() < 1e-12) {
    return [1, 0, 0, 0, 1, 0, 0, 0, 1];
  }
  final invDet = 1 / det;
  return [
    (e * i - f * h) * invDet, (c * h - b * i) * invDet, (b * f - c * e) * invDet,
    (f * g - d * i) * invDet, (a * i - c * g) * invDet, (c * d - a * f) * invDet,
    (d * h - e * g) * invDet, (b * g - a * h) * invDet, (a * e - b * d) * invDet,
  ];
}

((double, double, double), (double, double, double)) _faceUvBasis((double, double, double) n) {
  final (nx, ny, nz) = n;
  final cx = -ny, cy = nx;
  final clen = sqrt(cx * cx + cy * cy);
  if (clen < 1e-9) {
    return ((1.0, 0.0, 0.0), (0.0, nz >= 0 ? 1.0 : -1.0, 0.0));
  }
  final xr = (cx / clen, cy / clen, 0.0);
  final yr = (ny * xr.$3 - nz * xr.$2, nz * xr.$1 - nx * xr.$3, nx * xr.$2 - ny * xr.$1);
  return (xr, yr);
}

(double, double) _computeFaceUv(
  (double, double, double) p,
  (double, double, double) xr,
  (double, double, double) yr,
  List<double>? uvTransform,
  double tileW,
  double tileH,
) {
  final px = p.$1 * xr.$1 + p.$2 * xr.$2 + p.$3 * xr.$3;
  final py = p.$1 * yr.$1 + p.$2 * yr.$2 + p.$3 * yr.$3;
  if (uvTransform == null) {
    return (px / tileW, py / tileH);
  }
  final inv = _invert3x3(uvTransform);
  final u = px * inv[0] + py * inv[3] + inv[6];
  final v = px * inv[1] + py * inv[4] + inv[7];
  var q = px * inv[2] + py * inv[5] + inv[8];
  if (q.abs() < 1e-12) q = 1.0;
  return (u / q / tileW, v / q / tileH);
}

/// Same reconstruction [SceneBuilder] uses internally (see scene.dart) -
/// duplicated here (rather than exposed from there) since it operates on
/// the exact same [Face.loops]/[Definition.edges] shapes and this is the
/// only other place in the package that needs it.
List<int> _reconstructLoopVertices(List<(int, int)> loop, Map<int, (int, int)> edges) {
  final loopVerts = <int>[];
  for (final (edgeId, orient) in loop) {
    final ends = edges[edgeId];
    if (ends != null) {
      final vStart = orient == 1 ? ends.$1 : ends.$2;
      if (loopVerts.isEmpty || loopVerts.last != vStart) {
        loopVerts.add(vStart);
      }
    }
  }
  if (loopVerts.length > 1 && loopVerts.first == loopVerts.last) {
    loopVerts.removeLast();
  }
  return loopVerts;
}
