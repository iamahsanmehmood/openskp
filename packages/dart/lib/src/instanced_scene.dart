import 'dart:math';

import 'core.dart';
import 'errors.dart';
import 'face_groups.dart';
import 'geometry.dart';
import 'observability.dart';
import 'scene.dart' show SceneTexture, sniffImageMime;
import 'tlv.dart';
import 'transforms.dart';

/// One reusable, DEFINITION-LOCAL triangulated mesh: the instanced
/// counterpart of GlbPrimitive, minus the world transform.
///
/// Positions and normals stay in the definition's own local frame (metres,
/// glTF Y-up - already converted, same as GlbPrimitive), so N placements
/// of the same definition share this one buffer set instead of getting N
/// transformed copies of it. Normal transformation is deferred to the
/// consumer/renderer's node transform (glTF's own inverse-transpose rule),
/// which is what keeps mirrored/non-uniform-scale placements correct
/// without a per-instance normal copy.
class LocalPrimitive {
  final List<double> positions;
  final List<double> normals;
  final List<double> uvs;
  final List<int> indices;
  final int materialIndex;

  LocalPrimitive({
    required this.positions,
    required this.normals,
    required this.uvs,
    required this.indices,
    required this.materialIndex,
  });
}

/// A definition's geometry, resolved for one specific rendering context and
/// ready to be referenced by any number of InstancedNodes.
///
/// One SketchUp definition can yield MORE than one resource: the same
/// component painted with two different colors renders differently and
/// therefore needs a separate variant - see [variantKey].
class InstancedMeshResource {
  final String id;
  final int? definitionId;
  final String definitionName;
  final String variantKey;
  final List<LocalPrimitive> primitives;

  InstancedMeshResource({
    required this.id,
    required this.definitionId,
    required this.definitionName,
    required this.variantKey,
    required this.primitives,
  });
}

const List<double> identityGltf = [
  1, 0, 0, 0,
  0, 1, 0, 0,
  0, 0, 1, 0,
  0, 0, 0, 1,
];

/// One placed node in the instanced scene graph.
///
/// Carries the transform that places its [meshResourceId] (and its whole
/// subtree) into the scene, instead of that transform having been baked
/// into vertex data.
class InstancedNode {
  String name;
  String definitionName;
  String layer;

  /// This node's transform RELATIVE TO ITS PARENT, as a 16-element
  /// column-major glTF matrix (metres, Y-up) - directly usable as a glTF
  /// node "matrix". The root node's matrix is the identity.
  List<double> matrix;

  (double, double, double) positionMm;
  Map<String, String> properties;
  String? meshResourceId;
  List<InstancedNode> children;

  InstancedNode({
    this.name = '',
    this.definitionName = '',
    this.layer = '',
    List<double>? matrix,
    this.positionMm = (0.0, 0.0, 0.0),
    Map<String, String>? properties,
    this.meshResourceId,
    List<InstancedNode>? children,
  })  : matrix = matrix ?? identityGltf,
        properties = properties ?? {},
        children = children ?? [];
}

/// Axis-aligned bounds of the scene as PLACED, metres and Y-up.
class SceneBounds {
  final (double, double, double) min;
  final (double, double, double) max;
  final (double, double, double) size;
  final (double, double, double) center;

  SceneBounds({required this.min, required this.max, required this.size, required this.center});
}

/// The result of [InstancedSceneBuilder.build].
class InstancedScene {
  SceneBounds? bounds;
  InstancedNode sceneHierarchy;
  List<InstancedMeshResource> meshResources;
  List<Map<String, dynamic>> gltfMaterials;

  /// Distinct texture images the placed materials use, deduplicated by
  /// source bytes - same as Scene.textures.
  List<SceneTexture> textures;

  InstancedScene({
    this.bounds,
    required this.sceneHierarchy,
    required this.meshResources,
    required this.gltfMaterials,
    List<SceneTexture>? textures,
  }) : textures = textures ?? [];
}

const double _inchesToMm = 25.4;
const double _inchesToM = 0.0254;

/// Builds the placed scene graph with SketchUp's component/group
/// instancing PRESERVED rather than baked out.
///
/// Where SceneBuilder emits one world-space vertex buffer per placement,
/// this triangulates each distinct definition (in its own rendering
/// context) ONCE, in local space, and refers to it from every placement.
/// Scene size therefore scales with unique geometry + instance transforms
/// instead of definition geometry x placement count - the same value
/// proposition for a furniture layout or a structural grid with many
/// repeated components as the TypeScript reference
/// (buildInstancedScene()/toInstancedGLB() in
/// packages/typescript/src/{instanced,instanced-glb}.ts, openskp#200).
///
/// Lossless: no decimation, quantisation or geometry approximation of any
/// kind. The triangles are the same triangles SceneBuilder produces - via
/// the SAME extracted buildLocalFaceGroups - just stored once and
/// referenced N times instead of baked into N world-space copies.
class InstancedSceneBuilder {
  static InstancedScene build(RawParsed parsed, [ParseOptions? options]) {
    final sw = Stopwatch()..start();
    final defsDict = parsed.defsDict;
    final layerColors = parsed.layerColors;
    final layerIdToName = parsed.layerIdToName;
    final materialIdToName = parsed.materialIdToName;
    final materials = parsed.materials;
    final materialsByFolder = parsed.materialsByFolder;

    emitLog(options, SkpLogLevel.info, 'Building instanced scene: ${defsDict.length} definitions available');
    var instanceCounter = 0;
    final activeDefinitions = <int>{};

    (int, int, int) getLayerColor(String name) => layerColors[name] ?? (136, 136, 136);

    final textures = <SceneTexture>[];
    final textureIndexByKey = <String, int>{};

    int? textureIndexFor(RawTexture? tex) {
      if (tex == null || tex.data == null || tex.data!.isEmpty) return null;
      final data = tex.data!;
      final mimeType = sniffImageMime(data);
      if (mimeType == null) return null;
      final headLen = data.length < 16 ? data.length : 16;
      final head = data.sublist(0, headLen).join(',');
      final key = '${data.length}:$head';
      final hit = textureIndexByKey[key];
      if (hit != null) return hit;
      final idx = textures.length;
      textures.add(SceneTexture(data: data, mimeType: mimeType, filename: tex.filename));
      textureIndexByKey[key] = idx;
      return idx;
    }

    final colorToMaterialIndex = <((int, int, int), bool, int?), int>{};
    final gltfMaterials = <Map<String, dynamic>>[];

    int getMaterialIndex((int, int, int) color, bool doubleSided, int? textureIndex) {
      final key = (color, doubleSided, textureIndex);
      final existing = colorToMaterialIndex[key];
      if (existing != null) return existing;
      final idx = gltfMaterials.length;
      final (r, g, b) = color;
      final pbr = <String, dynamic>{
        'baseColorFactor': [r / 255, g / 255, b / 255, 1.0],
        'metallicFactor': 0.0,
        'roughnessFactor': 0.8,
      };
      if (textureIndex != null) {
        pbr['baseColorTexture'] = {'index': textureIndex};
      }
      final material = <String, dynamic>{'pbrMetallicRoughness': pbr};
      if (doubleSided) material['doubleSided'] = true;
      gltfMaterials.add(material);
      colorToMaterialIndex[key] = idx;
      return idx;
    }

    RawMaterial? resolveMaterial(int? matId) =>
        _resolveMaterial(matId, materialIdToName, materials, materialsByFolder);

    final meshResources = <InstancedMeshResource>[];
    final resourceIdByKey = <String, String>{};

    // Identity of a mesh resource: (definition, effective fallback color) -
    // the ONLY inputs that can change what buildLocalFaceGroups produces
    // for this definition, since (faithfully to the baked path this was
    // extracted from - see face_groups.dart's own docs) it resolves each
    // face's material from the face's OWN material id only, never from an
    // instance's painted material. Caching on the definition id alone
    // would still be wrong: the same definition renders a different
    // fallback color depending on the layer/paint context it's placed in,
    // and merging those would silently repaint geometry.
    String? meshResourceForBuilder(
      GeometryBuilder builder,
      String defName,
      int? defId,
      (int, int, int)? inheritedColor,
      String layer,
    ) {
      if (builder.faces.isEmpty) return null;

      final fallbackColor = inheritedColor ?? getLayerColor(layer);
      final key = '${defId ?? 'ROOT'}|${fallbackColor.$1},${fallbackColor.$2},${fallbackColor.$3}';
      final hit = resourceIdByKey[key];
      if (hit != null) return hit;

      final faceGroups = buildLocalFaceGroups(
        builder,
        FaceGroupContext(
          resolveMaterial: resolveMaterial,
          textureIndexFor: textureIndexFor,
          fallbackColor: fallbackColor,
          definitionId: defId,
        ),
      );

      final primitives = <LocalPrimitive>[];
      for (final groupEntry in faceGroups.entries) {
        final (color, doubleSided, texIndex) = groupEntry.key;
        final group = groupEntry.value;
        if (group.localFaces.isEmpty) continue;

        final vertCount = group.localVerts.length;
        final positions = List<double>.filled(vertCount * 3, 0.0);
        final normals = List<double>.filled(vertCount * 3, 0.0);
        final uvs = List<double>.filled(vertCount * 2, 0.0);
        final vertexNormalsAccum = group.normalsAccum;

        for (int i = 0; i < vertCount; i++) {
          final v = group.localVerts[i];
          // Local space, so no instance matrix is applied - only the
          // inches->metres scale and SketchUp Z-up -> glTF Y-up axis
          // swap, the same fixed conventions the baked path applies.
          positions[i * 3] = v.$1 * _inchesToM;
          positions[i * 3 + 1] = v.$3 * _inchesToM;
          positions[i * 3 + 2] = -v.$2 * _inchesToM;

          uvs[i * 2] = group.localUvs[i].$1;
          uvs[i * 2 + 1] = group.localUvs[i].$2;

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
          // Same axis swap as positions. No instance-matrix normal
          // transform here: that belongs to the node, and deferring it is
          // precisely what keeps mirrored/non-uniform scales correct per
          // placement.
          normals[i * 3] = nx0;
          normals[i * 3 + 1] = nz0;
          normals[i * 3 + 2] = -ny0;
        }

        final indices = <int>[];
        for (final tri in group.localFaces) {
          indices.add(tri[0]);
          indices.add(tri[1]);
          indices.add(tri[2]);
        }

        primitives.add(LocalPrimitive(
          positions: positions,
          normals: normals,
          uvs: uvs,
          indices: indices,
          materialIndex: getMaterialIndex(color, doubleSided, texIndex),
        ));
      }

      if (primitives.isEmpty) return null;

      final resourceId = 'mesh_${meshResources.length}';
      meshResources.add(InstancedMeshResource(
        id: resourceId,
        definitionId: defId,
        definitionName: defName,
        variantKey: key,
        primitives: primitives,
      ));
      resourceIdByKey[key] = resourceId;
      return resourceId;
    }

    /// Convert one instance's 13-element SketchUp matrix (inches, Z-up)
    /// into a 16-element column-major glTF matrix (metres, Y-up).
    ///
    /// The axis change is the similarity transform C * M * C^-1 with
    /// C: (x, y, z) -> (x, z, -y), so it composes correctly through
    /// nesting: converting each level and multiplying gives the same
    /// result as converting the fully-composed SketchUp matrix.
    /// Translation is scaled to metres; the rotation/scale block is
    /// unitless and is not.
    List<double> toGltfMatrix(List<double> m) {
      final a = m[0], b = m[1], c = m[2];
      final d = m[3], e = m[4], f = m[5];
      final g = m[6], h = m[7], i = m[8];
      final tx = m.length > 9 ? m[9] : 0.0;
      final ty = m.length > 10 ? m[10] : 0.0;
      final tz = m.length > 11 ? m[11] : 0.0;

      final r00 = a, r01 = c, r02 = -b;
      final r10 = g, r11 = i, r12 = -h;
      final r20 = -d, r21 = -f, r22 = e;

      return [
        r00, r10, r20, 0,
        r01, r11, r21, 0,
        r02, r12, r22, 0,
        tx * _inchesToM, tz * _inchesToM, -ty * _inchesToM, 1,
      ];
    }

    /// Walk a definition's placed instances, emitting one node each.
    /// currentMatrix is the accumulated SketchUp-space matrix and is used
    /// ONLY to report each node's absolute positionMm (matching the baked
    /// path's metadata); the geometry itself never sees it.
    List<InstancedNode> walkBuilder(
      GeometryBuilder builder,
      List<double> currentMatrix,
      String parentLayer,
      (int, int, int)? inheritedColor,
    ) {
      final nodes = <InstancedNode>[];
      for (final inst in builder.instances) {
        final refIdx = inst.refIdx;
        final newMatrix = Transforms.multiplyMatrices(currentMatrix, inst.matrix);

        var lName = parentLayer;
        (int, int, int)? instColor = inheritedColor;
        var properties = Map<String, String>.from(inst.properties ?? {});

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
          try {
            properties = Geometry.extractDynamicProperties(d007);
          } catch (e) {
            emitLog(
              options, SkpLogLevel.debug,
              'Failed to extract dynamic properties for instance ${inst.name} (refIdx=$refIdx): $e',
            );
          }
        }

        instanceCounter++;
        if (instanceCounter % progressInterval == 0) {
          emitProgress(options, 'build_scene', instanceCounter, instanceCounter);
          emitLog(options, SkpLogLevel.debug, 'Processed $instanceCounter placed instances');
        }

        final childDef = refIdx != null ? defsDict[refIdx] : null;
        List<InstancedNode> children;
        if (refIdx != null && childDef != null) {
          if (!activeDefinitions.add(refIdx)) {
            throw SkpParseException(
              'Recursive component definition',
              stage: 'build_scene', definitionId: refIdx,
            );
          }
          try {
            children = walkBuilder(childDef.builder, newMatrix, lName, instColor);
          } finally {
            activeDefinitions.remove(refIdx);
          }
        } else {
          children = <InstancedNode>[];
        }

        final itx = newMatrix.length > 9 ? newMatrix[9] * _inchesToMm : 0.0;
        final ity = newMatrix.length > 10 ? newMatrix[10] * _inchesToMm : 0.0;
        final itz = newMatrix.length > 11 ? newMatrix[11] * _inchesToMm : 0.0;

        nodes.add(InstancedNode(
          name: inst.name ?? '',
          definitionName: childDef?.name ?? '',
          layer: lName,
          matrix: toGltfMatrix(inst.matrix),
          positionMm: (_round2(itx), _round2(ity), _round2(itz)),
          properties: properties,
          meshResourceId: (refIdx != null && childDef != null)
              ? meshResourceForBuilder(childDef.builder, childDef.name ?? '', refIdx, instColor, lName)
              : null,
          children: children,
        ));
      }
      return nodes;
    }

    final identityMat = <double>[1.0, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1.0];
    final rootChildren = walkBuilder(parsed.root.builder, identityMat, 'Layer0', null);

    // Loose geometry drawn straight into the model (not inside any
    // component/group) is kept, as the baked path keeps it: it becomes
    // the root node's own mesh resource.
    final rootMeshResourceId = meshResourceForBuilder(parsed.root.builder, 'ROOT_MODEL', null, null, 'Layer0');

    final sceneHierarchy = InstancedNode(
      name: 'ROOT',
      definitionName: 'ROOT_MODEL',
      layer: 'Layer0',
      matrix: List<double>.from(identityGltf),
      positionMm: (0.0, 0.0, 0.0),
      meshResourceId: rootMeshResourceId,
      children: rootChildren,
    );

    // Bounds of the scene AS PLACED: walk the tree, transform each
    // resource's local corners by the accumulated node matrix. Only the 8
    // corners of each resource's local box are transformed rather than
    // every vertex - an affine transform maps a box's corners to the
    // corners of the transformed box, so the result is exact for the
    // axis-aligned bounds, at a fraction of the cost.
    final resourceById = {for (final r in meshResources) r.id: r};
    final localBoxCache = <String, (List<double>, List<double>)?>{};

    (List<double>, List<double>)? localBox(String resourceId) {
      if (localBoxCache.containsKey(resourceId)) return localBoxCache[resourceId];
      (List<double>, List<double>)? box;
      final res = resourceById[resourceId];
      if (res != null) {
        final lo = [double.infinity, double.infinity, double.infinity];
        final hi = [double.negativeInfinity, double.negativeInfinity, double.negativeInfinity];
        for (final prim in res.primitives) {
          for (var i = 0; i < prim.positions.length; i += 3) {
            for (var k = 0; k < 3; k++) {
              final v = prim.positions[i + k];
              if (v < lo[k]) lo[k] = v;
              if (v > hi[k]) hi[k] = v;
            }
          }
        }
        if (lo[0] != double.infinity) box = (lo, hi);
      }
      localBoxCache[resourceId] = box;
      return box;
    }

    List<double> mul4(List<double> a, List<double> b) {
      final out = List<double>.filled(16, 0.0);
      for (var col = 0; col < 4; col++) {
        for (var row = 0; row < 4; row++) {
          var s = 0.0;
          for (var k = 0; k < 4; k++) s += a[k * 4 + row] * b[col * 4 + k];
          out[col * 4 + row] = s;
        }
      }
      return out;
    }

    final bMin = [double.infinity, double.infinity, double.infinity];
    final bMax = [double.negativeInfinity, double.negativeInfinity, double.negativeInfinity];

    void accumulate(InstancedNode node, List<double> parent) {
      final world = mul4(parent, node.matrix);
      final resId = node.meshResourceId;
      if (resId != null) {
        final box = localBox(resId);
        if (box != null) {
          final (lo, hi) = box;
          for (var c = 0; c < 8; c++) {
            final x = (c & 1) != 0 ? hi[0] : lo[0];
            final y = (c & 2) != 0 ? hi[1] : lo[1];
            final z = (c & 4) != 0 ? hi[2] : lo[2];
            final wx = world[0] * x + world[4] * y + world[8] * z + world[12];
            final wy = world[1] * x + world[5] * y + world[9] * z + world[13];
            final wz = world[2] * x + world[6] * y + world[10] * z + world[14];
            if (wx < bMin[0]) bMin[0] = wx;
            if (wy < bMin[1]) bMin[1] = wy;
            if (wz < bMin[2]) bMin[2] = wz;
            if (wx > bMax[0]) bMax[0] = wx;
            if (wy > bMax[1]) bMax[1] = wy;
            if (wz > bMax[2]) bMax[2] = wz;
          }
        }
      }
      for (final child in node.children) accumulate(child, world);
    }
    accumulate(sceneHierarchy, List<double>.from(identityGltf));

    SceneBounds? bounds;
    if (bMin[0] != double.infinity) {
      bounds = SceneBounds(
        min: (bMin[0], bMin[1], bMin[2]),
        max: (bMax[0], bMax[1], bMax[2]),
        size: (bMax[0] - bMin[0], bMax[1] - bMin[1], bMax[2] - bMin[2]),
        center: ((bMin[0] + bMax[0]) / 2, (bMin[1] + bMax[1]) / 2, (bMin[2] + bMax[2]) / 2),
      );
    }

    emitLog(
      options, SkpLogLevel.info,
      'Instanced scene build complete: $instanceCounter instances, ${meshResources.length} mesh resources '
      '(${(sw.elapsedMilliseconds / 1000).toStringAsFixed(2)}s)',
    );

    return InstancedScene(
      bounds: bounds,
      sceneHierarchy: sceneHierarchy,
      meshResources: meshResources,
      gltfMaterials: gltfMaterials,
      textures: textures,
    );
  }

  static double _round2(double v) => (v * 100).round() / 100;
  static double _len3(double x, double y, double z) => sqrt(x * x + y * y + z * z);

  static RawMaterial? _resolveMaterial(
    int? matId,
    Map<int, String> materialIdToName,
    Map<String, RawMaterial> materials,
    Map<String, RawMaterial> materialsByFolder,
  ) {
    if (matId == null) return null;
    final matName = materialIdToName[matId];
    if (matName == null) return null;
    return materials[matName] ?? materialsByFolder[matName];
  }
}

extension _FirstOrNullExt<T> on Iterable<T> {
  T? get firstOrNull => isEmpty ? null : first;
}
