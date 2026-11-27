import 'dart:math';

import 'core.dart';
import 'curves.dart';
import 'errors.dart';
import 'face_groups.dart';
import 'geometry.dart';
import 'model.dart' show Page;
import 'observability.dart';
import 'parser.dart' show buildPagesFromRaw;
import 'scene.dart' show SceneTexture, createByteContentKeyer, createTextureIndexFor;
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

  /// Triangle offsets for separately emitted back sides.
  final List<int> backTriangles;

  final int materialIndex;

  /// The back copies of faces with no paint on either side; see
  /// [GlbPrimitive.unpaintedBack].
  final bool unpaintedBack;

  LocalPrimitive({
    required this.positions,
    required this.normals,
    required this.uvs,
    required this.indices,
    List<int>? backTriangles,
    required this.materialIndex,
    this.unpaintedBack = false,
  }) : backTriangles = backTriangles ?? const [];
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

/// One loose-edge run in definition-local space (metres, glTF Y-up).
class LocalCurve {
  final List<(double, double, double)> pointsM;
  final bool closed;
  final String layer;
  final CurveArc? arc;

  LocalCurve({
    required this.pointsM,
    required this.closed,
    required this.layer,
    this.arc,
  });
}

/// A definition's loose edges, ready to be referenced by any number of
/// [InstancedNode]s - the loose-edge counterpart of [InstancedMeshResource].
class InstancedCurveResource {
  final String id;
  final int? definitionId;
  final String definitionName;
  final List<LocalCurve> curves;

  InstancedCurveResource({
    required this.id,
    required this.definitionId,
    required this.definitionName,
    required this.curves,
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

  /// Whether [name] is a synthetic fallback (SketchUp's own internal
  /// index, e.g. "Component_5") rather than a real name from the source
  /// file - no attribute-dictionary name/label/code override, no explicit
  /// instance name, and no non-generic definition name were found. Lets a
  /// consumer (e.g. Fragments export) avoid presenting a placeholder as if
  /// it were real data. Mirrors Python's/C++'s own field of the same name.
  bool nameIsGenerated;

  String definitionName;
  String layer;

  /// This node's transform RELATIVE TO ITS PARENT, as a 16-element
  /// column-major glTF matrix (metres, Y-up) - directly usable as a glTF
  /// node "matrix". The root node's matrix is the identity.
  List<double> matrix;

  (double, double, double) positionMm;
  Map<String, String> properties;

  /// See scene.dart's InstanceNode.attributeDictionaries - every OTHER
  /// attribute dictionary this instance carries, keyed by the dictionary's
  /// own name (openskp#285).
  Map<String, Map<String, String>> attributeDictionaries;

  /// Real SketchUp instance GUID (VFF/2021+ files only - legacy pre-2021
  /// files carry no per-instance GUID here), or `''` when the source file
  /// has none. Mirrors Python's/C++'s own field; a consumer keying on this
  /// (e.g. Fragments export) is responsible for its own collision
  /// handling - see openskp#290's rationale for why a duplicated real GUID
  /// needs the same synthetic-fallback treatment as a missing one.
  String guid;

  String? meshResourceId;
  String? curveResourceId;
  List<InstancedNode> children;

  InstancedNode({
    this.name = '',
    this.nameIsGenerated = false,
    this.definitionName = '',
    this.layer = '',
    List<double>? matrix,
    this.positionMm = (0.0, 0.0, 0.0),
    Map<String, String>? properties,
    Map<String, Map<String, String>>? attributeDictionaries,
    this.guid = '',
    this.meshResourceId,
    this.curveResourceId,
    List<InstancedNode>? children,
  })  : matrix = matrix ?? identityGltf,
        properties = properties ?? {},
        attributeDictionaries = attributeDictionaries ?? {},
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
  List<InstancedCurveResource> curveResources;
  List<Map<String, dynamic>> gltfMaterials;

  /// Distinct texture images the placed materials use, deduplicated by
  /// source bytes - same as Scene.textures.
  List<SceneTexture> textures;

  /// The source file's own per-layer visibility (VFF/2021+ only - see
  /// RawParsed.layerHidden's own comment on why legacy files default every
  /// layer to visible), read straight from the raw parse. Mirrors
  /// Python's/C++'s own field of the same name.
  Map<String, bool> layerHidden;

  /// Number of top-level entities dropped when a classic (pre-2021) root
  /// entity walk stopped early (`0` for complete classic files, VFF files,
  /// and scenes read back from `.frag`).
  int unreadRootEntities;

  /// Saved scenes (SketchUp's "Scenes" tabs), identical to [SkpModel.pages].
  /// Camera [Page.eye], [Page.target], [Page.up] and [Page.orthoHeight] stay
  /// in SketchUp's native Z-up frame and inches (unlike [meshResources] and
  /// node matrices, which are Y-up metres). Empty on scenes read back from
  /// `.frag`.
  List<Page> pages;

  InstancedScene({
    this.bounds,
    required this.sceneHierarchy,
    required this.meshResources,
    List<InstancedCurveResource>? curveResources,
    required this.gltfMaterials,
    List<SceneTexture>? textures,
    Map<String, bool>? layerHidden,
    this.unreadRootEntities = 0,
    List<Page>? pages,
  })  : curveResources = curveResources ?? [],
        textures = textures ?? [],
        layerHidden = layerHidden ?? {},
        pages = pages ?? [];
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
    final styleColors = unpaintedStyleColors(parsed, options?.unpaintedFaces);

    final textures = <SceneTexture>[];
    final textureIndexFor = createTextureIndexFor(textures, options);

    final colorToMaterialIndex =
        <((int, int, int), bool, int?, double, bool, int, String?), int>{};
    final gltfMaterials = <Map<String, dynamic>>[];

    int getMaterialIndex(
      (int, int, int) color,
      bool doubleSided,
      int? textureIndex, [
      double transparency = 1.0,
      bool colorized = false,
      int colorizeType = 0,
      String? materialName,
    ]) {
      final effectiveColorized = colorized && textureIndex != null;
      final effectiveColorizeType = effectiveColorized ? colorizeType : 0;
      final normalizedName =
          (materialName != null && materialName.isNotEmpty) ? materialName : null;
      final key = (
        color,
        doubleSided,
        textureIndex,
        transparency,
        effectiveColorized,
        effectiveColorizeType,
        normalizedName,
      );
      final existing = colorToMaterialIndex[key];
      if (existing != null) return existing;
      final idx = gltfMaterials.length;
      final (r, g, b) = color;
      final pbr = <String, dynamic>{
        'baseColorFactor': [r / 255, g / 255, b / 255, transparency],
        'metallicFactor': 0.0,
        'roughnessFactor': 0.8,
      };
      if (textureIndex != null) {
        pbr['baseColorTexture'] = {'index': textureIndex};
      }
      final material = <String, dynamic>{'pbrMetallicRoughness': pbr};
      if (normalizedName != null) {
        material['name'] = normalizedName;
      }
      if (effectiveColorized) {
        material['extras'] = <String, dynamic>{
          'sketchup': <String, dynamic>{
            'colorized': true,
            'colorize_type': effectiveColorizeType,
          },
        };
      }
      if (doubleSided) material['doubleSided'] = true;
      // See scene.dart's getMaterialIndex for why: BLEND for genuinely
      // translucent materials, MASK (safe no-op on an opaque-alpha
      // texture) for textured ones, otherwise glTF's default OPAQUE.
      if (transparency < 1.0) {
        material['alphaMode'] = 'BLEND';
      } else if (textureIndex != null) {
        material['alphaMode'] = 'MASK';
      }
      gltfMaterials.add(material);
      colorToMaterialIndex[key] = idx;
      return idx;
    }

    RawMaterial? resolveMaterial(int? matId) =>
        _resolveMaterial(matId, materialIdToName, materials, materialsByFolder);

    final meshResources = <InstancedMeshResource>[];
    final resourceIdByKey = <String, String>{};
    final byteContentKey = createByteContentKeyer();
    final curveResources = <InstancedCurveResource>[];
    final curveResourceIdByKey = <String, String?>{};
    final preparedCurvesCache = <int, PreparedDefCurves>{};
    final includeCurveSets = options?.includeCurveSets ?? true;
    (int, int, int)? resolveFaceTagColor(int? layerId) {
      if (layerId != null && layerId != 0) {
        final fname = layerIdToName[layerId] ?? '';
        if (fname.isNotEmpty && fname != 'Layer0') {
          return getLayerColor(fname);
        }
      }
      return null;
    }

    final faceLayerCache = <int, (String?, bool)>{};
    (String?, bool) getFaceLayerSummary(int? defId, GeometryBuilder builder) {
      final key = defId ?? -1;
      final hit = faceLayerCache[key];
      if (hit != null || faceLayerCache.containsKey(key)) {
        return hit ?? (null, false);
      }
      final summary = Geometry.summarizeFaceLayers(builder, layerIdToName);
      faceLayerCache[key] = summary;
      return summary;
    }

    String? curveResourceForBuilder(
      GeometryBuilder builder,
      String defName,
      int? defId,
      String layer,
    ) {
      if (!includeCurveSets) return null;
      final effectiveLayer =
          layer == 'Layer0' ? (getFaceLayerSummary(defId, builder).$1 ?? layer) : layer;
      final key = '${defId ?? 'ROOT'}|$effectiveLayer';
      if (curveResourceIdByKey.containsKey(key)) {
        return curveResourceIdByKey[key];
      }

      final prepKey = defId ?? -1;
      var prepared = preparedCurvesCache[prepKey];
      if (prepared == null) {
        prepared = prepareDefinitionCurves(builder, layerIdToName);
        preparedCurvesCache[prepKey] = prepared;
      }
      if (prepared.runs.isEmpty) {
        curveResourceIdByKey[key] = null;
        return null;
      }

      final localCurves = <LocalCurve>[];
      for (final run in prepared.runs) {
        final pts = <(double, double, double)>[
          for (final v in run.localVerts)
            (
              round6(v.$1 * inchesToM),
              round6(v.$3 * inchesToM),
              round6(-v.$2 * inchesToM),
            ),
        ];
        final runLayer = run.explicitLayer ?? effectiveLayer;
        localCurves.add(LocalCurve(
          pointsM: pts,
          closed: run.closed,
          layer: runLayer,
          arc: solveRunArc(
            builder,
            run.edgeIds,
            null,
            pts,
            closed: run.closed,
            curveEdgeCounts: prepared.curveEdgeCounts,
          ),
        ));
      }
      if (localCurves.isEmpty) {
        curveResourceIdByKey[key] = null;
        return null;
      }

      final resourceId = 'curve_${curveResources.length}';
      curveResources.add(InstancedCurveResource(
        id: resourceId,
        definitionId: defId,
        definitionName: defName,
        curves: localCurves,
      ));
      curveResourceIdByKey[key] = resourceId;
      return resourceId;
    }

    // Identity of a mesh resource: (definition, effective fallback color,
    // inherited material name/opacity/colorize/texture+tile) - every input
    // that can change what buildLocalFaceGroups produces for this definition,
    // including inherited texture image content and tile scale on unpainted
    // faces.
    String? meshResourceForBuilder(
      GeometryBuilder builder,
      String defName,
      int? defId,
      (int, int, int)? inheritedColor,
      String layer, [
      RawMaterial? inheritedMaterial,
    ]) {
      if (builder.faces.isEmpty) return null;

      final (int, int, int) fallbackColor;
      final (int, int, int) fallbackBackColor;
      (int, int, int)? Function(int?)? faceTagColor;
      String colorPart;
      if (inheritedColor != null) {
        fallbackColor = fallbackBackColor = inheritedColor;
        colorPart = '${fallbackColor.$1},${fallbackColor.$2},${fallbackColor.$3}';
      } else if (styleColors != null) {
        (fallbackColor, fallbackBackColor) = styleColors;
        colorPart = '${fallbackColor.$1},${fallbackColor.$2},${fallbackColor.$3}';
      } else {
        final summary = getFaceLayerSummary(defId, builder);
        final colorLayer = summary.$1 ?? layer;
        fallbackColor = fallbackBackColor = getLayerColor(colorLayer);
        if (inheritedMaterial == null) {
          faceTagColor = resolveFaceTagColor;
        }
        colorPart = (summary.$2 || summary.$1 != null || inheritedMaterial != null)
            ? '${fallbackColor.$1},${fallbackColor.$2},${fallbackColor.$3}'
            : 'tags';
      }
      final backPart = fallbackBackColor != fallbackColor
          ? '/${fallbackBackColor.$1},${fallbackBackColor.$2},${fallbackBackColor.$3}'
          : '';
      String matPart = '';
      if (inheritedMaterial != null) {
        final tex = inheritedMaterial.texture;
        final String texPart;
        if (tex != null) {
          final data = tex.data;
          final dataKey = (data != null && data.isNotEmpty) ? '${byteContentKey(data)}' : '-';
          final tileW = tex.xScale > 1e-9 ? tex.xScale : 1.0;
          final tileH = tex.yScale > 1e-9 ? tex.yScale : 1.0;
          texPart = '$dataKey:${tileW}x$tileH';
        } else {
          texPart = '-';
        }
        matPart =
            '|${inheritedMaterial.name}:${resolveTransparency(inheritedMaterial)}:${inheritedMaterial.colorized ? 1 : 0}:${inheritedMaterial.colorizeType}:$texPart';
      }
      final key = '${defId ?? 'ROOT'}|$colorPart$matPart$backPart';
      final hit = resourceIdByKey[key];
      if (hit != null) return hit;

      final faceGroups = buildLocalFaceGroups(
        builder,
        FaceGroupContext(
          resolveMaterial: (matId) => resolveMaterial(matId) ?? inheritedMaterial,
          textureIndexFor: textureIndexFor,
          fallbackColor: fallbackColor,
          fallbackBackColor: fallbackBackColor,
          faceTagColor: faceTagColor,
          definitionId: defId,
        ),
      );

      final primitives = <LocalPrimitive>[];
      for (final groupEntry in faceGroups.entries) {
        final (
          color,
          doubleSided,
          texIndex,
          transparency,
          unpaintedBack,
          colorized,
          colorizeType,
          materialName,
        ) = groupEntry.key;
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
          backTriangles: List<int>.of(group.backTriangles),
          materialIndex: getMaterialIndex(
            color,
            doubleSided,
            texIndex,
            transparency,
            colorized,
            colorizeType,
            materialName,
          ),
          unpaintedBack: unpaintedBack,
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
    List<double> toGltfMatrix(List<double> raw) {
      // Fold the matrix's scale term (index 12, SketchUp's homogeneous 1/w)
      // into the rotation and translation first, so an instance scaled
      // through it is placed and sized correctly (openskp#444).
      final m = Transforms.foldScale(raw);
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
      (int, int, int)? inheritedColor, [
      RawMaterial? inheritedMaterial,
    ]) {
      final nodes = <InstancedNode>[];
      for (final inst in builder.instances) {
        final refIdx = inst.refIdx;
        final newMatrix = Transforms.multiplyMatrices(currentMatrix, inst.matrix);

        var lName = parentLayer;
        (int, int, int)? instColor = inheritedColor;
        RawMaterial? instMaterial = inheritedMaterial;
        var properties = Map<String, String>.from(inst.properties ?? {});
        Map<String, Map<String, Object?>>? instAttributeDicts = inst.attributeDicts;

        // A classic (pre-2021) instance has no D007: its own tag and paint
        // were read with it.
        if (inst.layerId != null) {
          lName = layerIdToName[inst.layerId!] ?? parentLayer;
        }
        final painted = resolveMaterial(inst.materialId);
        if (painted != null) {
          instColor = (painted.r, painted.g, painted.b);
          instMaterial = painted;
        }

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
              if (mat != null) {
                instColor = (mat.r, mat.g, mat.b);
                instMaterial = mat;
              }
            }
          }
          try {
            instAttributeDicts = Geometry.extractAttributeDictionaries(d007);
            final dynamicAttrs = instAttributeDicts['dynamic_attributes'];
            properties = <String, String>{
              if (dynamicAttrs != null)
                for (final e in dynamicAttrs.entries)
                  e.key: Geometry.stringifyVffAttrValue(e.value),
            };
          } catch (e) {
            properties = <String, String>{};
            instAttributeDicts = null;
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
            children = walkBuilder(
              childDef.builder,
              newMatrix,
              lName,
              instColor,
              instMaterial,
            );
          } finally {
            activeDefinitions.remove(refIdx);
          }
        } else {
          children = <InstancedNode>[];
        }

        final itx = newMatrix.length > 9 ? newMatrix[9] * _inchesToMm : 0.0;
        final ity = newMatrix.length > 10 ? newMatrix[10] * _inchesToMm : 0.0;
        final itz = newMatrix.length > 11 ? newMatrix[11] * _inchesToMm : 0.0;

        // Fallback order: an attribute-dict name/label/code override, then
        // the instance's own explicit name, then the definition's own
        // name IF it's not itself just SketchUp's auto-generated
        // "Group#1"/"Component#12" placeholder, then finally the internal
        // index - the only case with no real name anywhere in the source
        // file. Mirrors Python's/C++'s own instanced_scene name
        // resolution exactly, and scene.dart's identical resolution - see
        // instanced_fixture_parity_test.dart, which depends on both trees
        // resolving display names identically.
        final childDefName = childDef?.name ?? '';
        final defNameIsReal = childDefName.isNotEmpty && !Geometry.isGenericDefinitionName(childDefName);
        final nameOverride = Geometry.findNameOverride(instAttributeDicts);
        final instNameNonEmpty = inst.name != null && inst.name!.isNotEmpty;
        final fallbackName = instNameNonEmpty ? inst.name! : (defNameIsReal ? childDefName : 'Component_$refIdx');
        final displayName = nameOverride ?? fallbackName;
        final nameIsGenerated = nameOverride == null && !instNameNonEmpty && !defNameIsReal;

        // Every OTHER attribute dictionary this instance carries -
        // dynamic_attributes is already surfaced separately as properties
        // above, and SU_InstanceSet is SketchUp's own always-present,
        // always-empty Owner/Status boilerplate, not worth surfacing.
        // Mirrors scene.dart's InstanceNode (and Python's own
        // attribute_dictionaries) exactly (openskp#285).
        final attributeDictionaries = <String, Map<String, String>>{};
        if (instAttributeDicts != null) {
          for (final entry in instAttributeDicts.entries) {
            if (entry.key == 'dynamic_attributes' || entry.key == 'SU_InstanceSet') continue;
            attributeDictionaries[entry.key] = {
              for (final e in entry.value.entries) e.key: Geometry.stringifyVffAttrValue(e.value),
            };
          }
        }

        final nodeLayer =
            (lName == 'Layer0' && children.isEmpty && refIdx != null && childDef != null)
                ? (getFaceLayerSummary(refIdx, childDef.builder).$1 ?? lName)
                : lName;

        nodes.add(InstancedNode(
          name: displayName,
          nameIsGenerated: nameIsGenerated,
          definitionName: childDefName,
          layer: nodeLayer,
          matrix: toGltfMatrix(inst.matrix),
          positionMm: (_round2(itx), _round2(ity), _round2(itz)),
          properties: properties,
          attributeDictionaries: attributeDictionaries,
          guid: inst.refGuid ?? '',
          meshResourceId: (refIdx != null && childDef != null)
              ? meshResourceForBuilder(
                  childDef.builder,
                  childDef.name ?? '',
                  refIdx,
                  instColor,
                  lName,
                  instMaterial,
                )
              : null,
          curveResourceId: (refIdx != null && childDef != null)
              ? curveResourceForBuilder(
                  childDef.builder,
                  childDef.name ?? '',
                  refIdx,
                  lName,
                )
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
    final rootCurveResourceId = curveResourceForBuilder(parsed.root.builder, 'ROOT_MODEL', null, 'Layer0');

    final sceneHierarchy = InstancedNode(
      name: 'ROOT',
      definitionName: 'ROOT_MODEL',
      layer: 'Layer0',
      matrix: List<double>.from(identityGltf),
      positionMm: (0.0, 0.0, 0.0),
      meshResourceId: rootMeshResourceId,
      curveResourceId: rootCurveResourceId,
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
      curveResources: curveResources,
      gltfMaterials: gltfMaterials,
      textures: textures,
      layerHidden: Map<String, bool>.from(parsed.layerHidden),
      unreadRootEntities: parsed.unreadRootEntities,
      pages: buildPagesFromRaw(parsed),
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
