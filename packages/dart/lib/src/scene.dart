import 'dart:math';

import 'core.dart';
import 'geometry.dart';
import 'tlv.dart';
import 'transforms.dart';
import 'triangulator.dart';

/// One node in the baked, world-space instance tree.
class InstanceNode {
  String name;
  String definitionName;
  String layer;
  (double, double, double) positionMm;
  Map<String, String> properties;
  List<InstanceNode> children;

  InstanceNode({
    this.name = '',
    this.definitionName = '',
    this.layer = '',
    this.positionMm = (0.0, 0.0, 0.0),
    Map<String, String>? properties,
    List<InstanceNode>? children,
  })  : properties = properties ?? {},
        children = children ?? [];
}

/// Metadata for one baked mesh, keyed the same as its GlbPrimitive's
/// geomName in Scene.meshIndex.
class MeshMetadata {
  String name;
  String definitionName;
  String layer;
  (double, double, double) positionMm;
  Map<String, String> properties;
  String path;

  MeshMetadata({
    this.name = '',
    this.definitionName = '',
    this.layer = '',
    this.positionMm = (0.0, 0.0, 0.0),
    Map<String, String>? properties,
    this.path = '',
  }) : properties = properties ?? {};
}

/// One triangulated, world-space mesh: all faces sharing a single resolved
/// color from one flattened scene-graph position. Ready to hand straight
/// to a GLB/glTF exporter or any other renderer.
class GlbPrimitive {
  /// Flat [x, y, z, x, y, z, ...] vertex positions, in metres, Y-up.
  final List<double> positions;

  /// Flat [x, y, z, ...] vertex normals, matching positions 1:1.
  final List<double> normals;

  /// Triangle vertex indices into positions/normals (3 per triangle).
  final List<int> indices;

  /// Index into Scene.gltfMaterials for this primitive's resolved color.
  final int materialIndex;

  /// Matches the corresponding key in Scene.meshIndex.
  final String geomName;

  GlbPrimitive({
    required this.positions,
    required this.normals,
    required this.indices,
    required this.materialIndex,
    required this.geomName,
  });
}

/// The result of baking a parsed file's placed instances into a flat,
/// world-space 3D scene.
class Scene {
  InstanceNode sceneHierarchy;
  Map<String, MeshMetadata> meshIndex;
  List<GlbPrimitive> glbPrimitives;
  List<Map<String, dynamic>> gltfMaterials;

  Scene({
    required this.sceneHierarchy,
    required this.meshIndex,
    required this.glbPrimitives,
    required this.gltfMaterials,
  });
}

class _FaceGroup {
  final (int, int, int) color;
  final List<(double, double, double)> localVerts = [];
  final List<List<int>> localFaces = [];
  final Map<int, int> localVMap = {};
  final List<(int, GeometryBuilderFace)> faceList = [];
  _FaceGroup(this.color);
}

const double _inchesToMm = 25.4;
const double _inchesToM = 0.0254;

/// Bakes every instance actually placed in a parsed model into world-space,
/// triangulated mesh data - SketchUp's own component/group nesting fully
/// resolved and flattened. See SkpFile.buildScene() for why this is a
/// separate, opt-in step from parse().
///
/// Ported from the TypeScript reference implementation
/// (model.ts's buildSceneFromParsed).
class SceneBuilder {
  static Scene build(RawParsed parsed) {
    final defsDict = parsed.defsDict;
    final layerColors = parsed.layerColors;
    final layerIdToName = parsed.layerIdToName;
    final materialIdToName = parsed.materialIdToName;
    final materials = parsed.materials;
    final materialsByFolder = parsed.materialsByFolder;

    int meshCounter = 0;
    final meshIndex = <String, MeshMetadata>{};
    final glbPrimitives = <GlbPrimitive>[];

    final colorToMaterialIndex = <(int, int, int), int>{};
    final gltfMaterials = <Map<String, dynamic>>[];

    (int, int, int) getLayerColor(String name) => layerColors[name] ?? (136, 136, 136);

    int getMaterialIndex((int, int, int) color) {
      final existing = colorToMaterialIndex[color];
      if (existing != null) return existing;
      final idx = gltfMaterials.length;
      final (r, g, b) = color;
      gltfMaterials.add({
        'pbrMetallicRoughness': {
          'baseColorFactor': [r / 255, g / 255, b / 255, 1.0],
          'metallicFactor': 0.0,
          'roughnessFactor': 0.8,
        },
      });
      colorToMaterialIndex[color] = idx;
      return idx;
    }

    List<int> reconstructLoopVertices(List<(int, int)> loop, Map<int, (int?, int?)> edges) {
      final loopVerts = <int>[];
      for (final (edgeId, orient) in loop) {
        final ends = edges[edgeId];
        if (ends != null) {
          final vStart = orient == 1 ? ends.$1 : ends.$2;
          if (vStart != null && (loopVerts.isEmpty || loopVerts.last != vStart)) {
            loopVerts.add(vStart);
          }
        }
      }
      if (loopVerts.length > 1 && loopVerts.first == loopVerts.last) {
        loopVerts.removeLast();
      }
      return loopVerts;
    }

    List<InstanceNode> instantiateBuilder(
      GeometryBuilder builder,
      String defName,
      List<double> currentMatrix,
      String parentLayer,
      String pathName,
      (int, int, int)? inheritedColor,
    ) {
      if (builder.faces.isNotEmpty) {
        final faceGroups = <(int, int, int), _FaceGroup>{};

        for (final faceEntry in builder.faces.entries) {
          final fId = faceEntry.key;
          final fData = faceEntry.value;
          (int, int, int)? faceColor = inheritedColor;
          final faceMatId = fData.materialId;
          if (faceMatId != null) {
            final matName = materialIdToName[faceMatId];
            if (matName != null) {
              final mat = materials[matName] ?? materialsByFolder[matName];
              if (mat != null) faceColor = (mat.r, mat.g, mat.b);
            }
          }
          final resolvedColor = faceColor ?? getLayerColor(parentLayer);

          final group = faceGroups.putIfAbsent(resolvedColor, () => _FaceGroup(resolvedColor));

          final loops = <List<int>>[];
          for (final loop in fData.loops) {
            final loopVerts = reconstructLoopVertices(loop, builder.edges);
            if (loopVerts.isNotEmpty) loops.add(loopVerts);
          }
          if (loops.isEmpty) continue;

          final triangles = Triangulator.triangulateFace3D(builder.vertices, loops, fData.normal);
          for (final tri in triangles) {
            final faceIndices = <int>[];
            for (final vId in tri) {
              if (builder.vertices.containsKey(vId)) {
                var idx = group.localVMap[vId];
                if (idx == null) {
                  group.localVerts.add(builder.vertices[vId]!);
                  idx = group.localVerts.length - 1;
                  group.localVMap[vId] = idx;
                }
                faceIndices.add(idx);
              }
            }
            if (faceIndices.length == 3) {
              group.localFaces.add(faceIndices);
            }
          }
          group.faceList.add((fId, fData));
        }

        final isRootPath = pathName == 'ROOT';
        final multiGroup = faceGroups.length > 1;

        for (final groupEntry in faceGroups.entries) {
          final color = groupEntry.key;
          final group = groupEntry.value;
          if (group.localFaces.isEmpty) continue;

          final tx = isRootPath ? 0.0 : (currentMatrix.length > 9 ? currentMatrix[9] : 0.0) * _inchesToMm;
          final ty = isRootPath ? 0.0 : (currentMatrix.length > 10 ? currentMatrix[10] : 0.0) * _inchesToMm;
          final tz = isRootPath ? 0.0 : (currentMatrix.length > 11 ? currentMatrix[11] : 0.0) * _inchesToMm;

          var safePath = pathName.replaceAll(' / ', '__').replaceAll(' ', '_');
          if (safePath.length > 80) safePath = safePath.substring(0, 80);
          final colorSuffix = multiGroup ? '_${color.$1}_${color.$2}_${color.$3}' : '';
          final geomName = 'mesh_${meshCounter}_${safePath}_$parentLayer$colorSuffix';
          meshCounter++;

          meshIndex[geomName] = MeshMetadata(
            name: isRootPath ? 'ROOT' : (pathName.split(' / ').lastOrNull ?? ''),
            definitionName: defName,
            layer: parentLayer,
            positionMm: (_round2(tx), _round2(ty), _round2(tz)),
            path: pathName,
          );

          final vertCount = group.localVerts.length;
          final positions = List<double>.filled(vertCount * 3, 0.0);
          final normals = List<double>.filled(vertCount * 3, 0.0);
          final vertexNormalsAccum = List.generate(vertCount, (_) => [0.0, 0.0, 0.0]);

          for (final (_, fData) in group.faceList) {
            final loops = <List<int>>[];
            for (final loop in fData.loops) {
              final loopVerts = reconstructLoopVertices(loop, builder.edges);
              if (loopVerts.isNotEmpty) loops.add(loopVerts);
            }
            if (loops.isEmpty) continue;
            final fn = fData.normal;
            for (final loop in loops) {
              for (final vId in loop) {
                final idx = group.localVMap[vId];
                if (idx != null) {
                  vertexNormalsAccum[idx][0] += fn.$1;
                  vertexNormalsAccum[idx][1] += fn.$2;
                  vertexNormalsAccum[idx][2] += fn.$3;
                }
              }
            }
          }

          for (int i = 0; i < vertCount; i++) {
            final v = group.localVerts[i];
            final pt = Transforms.transformPoint(currentMatrix, v);
            positions[i * 3] = pt.$1 * _inchesToM;
            positions[i * 3 + 1] = pt.$3 * _inchesToM;
            positions[i * 3 + 2] = -pt.$2 * _inchesToM;

            final raw = vertexNormalsAccum[i];
            final normLen = _len3(raw[0], raw[1], raw[2]);
            double nx0, ny0, nz0;
            if (normLen > 1e-6) {
              nx0 = raw[0] / normLen;
              ny0 = raw[1] / normLen;
              nz0 = raw[2] / normLen;
            } else {
              nx0 = 0;
              ny0 = 0;
              nz0 = 1;
            }

            final m0 = currentMatrix.length > 0 ? currentMatrix[0] : 1.0;
            final m1 = currentMatrix.length > 1 ? currentMatrix[1] : 0.0;
            final m2 = currentMatrix.length > 2 ? currentMatrix[2] : 0.0;
            final m3 = currentMatrix.length > 3 ? currentMatrix[3] : 0.0;
            final m4 = currentMatrix.length > 4 ? currentMatrix[4] : 1.0;
            final m5 = currentMatrix.length > 5 ? currentMatrix[5] : 0.0;
            final m6 = currentMatrix.length > 6 ? currentMatrix[6] : 0.0;
            final m7 = currentMatrix.length > 7 ? currentMatrix[7] : 0.0;
            final m8 = currentMatrix.length > 8 ? currentMatrix[8] : 1.0;

            final nx = m0 * nx0 + m1 * ny0 + m2 * nz0;
            final ny = m3 * nx0 + m4 * ny0 + m5 * nz0;
            final nz = m6 * nx0 + m7 * ny0 + m8 * nz0;
            final length = _len3(nx, ny, nz);
            if (length > 1e-6) {
              normals[i * 3] = nx / length;
              normals[i * 3 + 1] = nz / length;
              normals[i * 3 + 2] = -ny / length;
            } else {
              normals[i * 3] = 0;
              normals[i * 3 + 1] = 1;
              normals[i * 3 + 2] = 0;
            }
          }

          final indices = <int>[];
          for (final tri in group.localFaces) {
            indices.add(tri[0]);
            indices.add(tri[1]);
            indices.add(tri[2]);
          }

          final materialIndex = getMaterialIndex(color);
          glbPrimitives.add(GlbPrimitive(
            positions: positions,
            normals: normals,
            indices: indices,
            materialIndex: materialIndex,
            geomName: geomName,
          ));
        }
      }

      final childInstancesInfo = <InstanceNode>[];
      for (final inst in builder.instances) {
        final refIdx = inst.refIdx;
        final newMatrix = Transforms.multiplyMatrices(currentMatrix, inst.matrix);

        var lName = parentLayer;
        (int, int, int)? instColor = inheritedColor;
        final properties = <String, String>{};

        final d007 = inst.children.where((c) => c.tag == 'D007').firstOrNull;
        if (d007 != null) {
          final d207 = d007.children.where((c) => c.tag == 'D207').firstOrNull;
          if (d207 != null && d207.payload.isNotEmpty) {
            final p = d207.payload;
            final lId = p.length == 1 ? p[0] : Tlv.parseVarInt(p, 0, p.length);
            lName = layerIdToName[lId] ?? parentLayer;
          }
          final d107 = d007.children.where((c) => c.tag == 'D107').firstOrNull;
          if (d107 != null) {
            final instMatId = Tlv.parseVarInt(d107.payload, 0, d107.payload.length);
            final matName = materialIdToName[instMatId];
            if (matName != null) {
              final mat = materials[matName] ?? materialsByFolder[matName];
              if (mat != null) instColor = (mat.r, mat.g, mat.b);
            }
          }
          // Dynamic properties (attribute dictionaries under D007) are not
          // yet ported for Dart; left empty.
        }

        final instName = (inst.name != null && inst.name!.isNotEmpty) ? inst.name! : 'Component_$refIdx';
        final fullPathName = '$pathName / $instName';
        final childDef = refIdx != null ? defsDict[refIdx] : null;
        final childNodes = (refIdx != null && childDef != null)
            ? instantiateBuilder(childDef.builder, childDef.name ?? '', newMatrix, lName, fullPathName, instColor)
            : <InstanceNode>[];

        final itx = newMatrix.length > 9 ? newMatrix[9] * _inchesToMm : 0.0;
        final ity = newMatrix.length > 10 ? newMatrix[10] * _inchesToMm : 0.0;
        final itz = newMatrix.length > 11 ? newMatrix[11] * _inchesToMm : 0.0;

        final instInfo = InstanceNode(
          name: inst.name ?? '',
          definitionName: childDef?.name ?? '',
          layer: lName,
          positionMm: (_round2(itx), _round2(ity), _round2(itz)),
          properties: properties,
          children: childNodes,
        );
        childInstancesInfo.add(instInfo);

        var safeChildPath = fullPathName.replaceAll(' / ', '__').replaceAll(' ', '_');
        if (safeChildPath.length > 80) safeChildPath = safeChildPath.substring(0, 80);
        for (final entry in meshIndex.entries) {
          if (entry.key.contains(safeChildPath)) {
            entry.value.properties = properties;
            entry.value.name = inst.name ?? '';
          }
        }
      }

      return childInstancesInfo;
    }

    final identityMat = <double>[1.0, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1.0];
    final rootChildren = instantiateBuilder(parsed.root.builder, 'ROOT_MODEL', identityMat, 'Layer0', 'ROOT', null);

    for (final entry in meshIndex.entries) {
      final existing = entry.value;
      if (existing.path == 'ROOT') {
        existing.name = 'ROOT';
        existing.definitionName = 'ROOT_MODEL';
        existing.layer = 'Layer0';
        existing.positionMm = (0.0, 0.0, 0.0);
        existing.properties = {};
      }
    }

    final sceneHierarchy = InstanceNode(
      name: 'ROOT',
      definitionName: 'ROOT_MODEL',
      layer: 'Layer0',
      positionMm: (0.0, 0.0, 0.0),
      children: rootChildren,
    );

    return Scene(
      sceneHierarchy: sceneHierarchy,
      meshIndex: meshIndex,
      glbPrimitives: glbPrimitives,
      gltfMaterials: gltfMaterials,
    );
  }

  static double _round2(double v) => (v * 100).round() / 100;
  static double _len3(double x, double y, double z) => sqrt(x * x + y * y + z * z);
}

extension _FirstOrNullExt<T> on Iterable<T> {
  T? get firstOrNull => isEmpty ? null : first;
}

extension _LastOrNullExt<T> on List<T> {
  T? get lastOrNull => isEmpty ? null : last;
}
