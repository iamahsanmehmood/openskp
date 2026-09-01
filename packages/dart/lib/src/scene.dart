import 'dart:math';

import 'core.dart';
import 'errors.dart';
import 'face_groups.dart';
import 'geometry.dart';
import 'observability.dart';
import 'tlv.dart';
import 'transforms.dart';

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

  /// Flat [u, v, u, v, ...] texture coordinates, matching positions 1:1.
  /// Computed from each source face's uvTransform (or the default
  /// face-plane projection when a face has none) - see
  /// GeometryBuilderFace.uvTransform's usage for the formula. A vertex
  /// shared by two faces that disagree on UV is split, since indexed glTF
  /// meshes need position/normal/uv aligned per vertex. Faces with a
  /// PROJECTED texture (terrain-drape, e.g. Add Location) still use the
  /// face-plane formula here, since the real projection-plane basis isn't
  /// captured in the parsed data - their UVs will be approximate.
  final List<double> uvs;

  /// Triangle vertex indices into positions/normals/uvs (3 per triangle).
  final List<int> indices;

  /// Index into Scene.gltfMaterials for this primitive's resolved color.
  final int materialIndex;

  /// Matches the corresponding key in Scene.meshIndex.
  final String geomName;

  GlbPrimitive({
    required this.positions,
    required this.normals,
    required this.uvs,
    required this.indices,
    required this.materialIndex,
    required this.geomName,
  });
}

/// One texture image referenced by Scene.gltfMaterials.
class SceneTexture {
  /// The image file's raw bytes, exactly as stored in the .skp.
  final List<int> data;

  /// Sniffed from the bytes, not from [filename]: SketchUp records the
  /// authoring machine's path, whose extension can disagree with the
  /// content.
  final String mimeType;

  final String filename;

  SceneTexture({required this.data, required this.mimeType, this.filename = ''});
}

/// The result of baking a parsed file's placed instances into a flat,
/// world-space 3D scene.
class Scene {
  InstanceNode sceneHierarchy;
  Map<String, MeshMetadata> meshIndex;
  List<GlbPrimitive> glbPrimitives;
  List<Map<String, dynamic>> gltfMaterials;

  /// Distinct texture images the placed materials use, deduplicated by
  /// source bytes. Empty when nothing placed in the scene is textured.
  List<SceneTexture> textures;

  Scene({
    required this.sceneHierarchy,
    required this.meshIndex,
    required this.glbPrimitives,
    required this.gltfMaterials,
    List<SceneTexture>? textures,
  }) : textures = textures ?? [];
}

/// Identifies an image's MIME type from its magic bytes. Returns null for
/// anything glTF cannot carry (glTF only allows PNG and JPEG).
String? sniffImageMime(List<int> data) {
  if (data.length >= 3 && data[0] == 0xff && data[1] == 0xd8 && data[2] == 0xff) {
    return 'image/jpeg';
  }
  if (data.length >= 8 &&
      data[0] == 0x89 && data[1] == 0x50 && data[2] == 0x4e && data[3] == 0x47 &&
      data[4] == 0x0d && data[5] == 0x0a && data[6] == 0x1a && data[7] == 0x0a) {
    return 'image/png';
  }
  return null;
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
  static Scene build(RawParsed parsed, [ParseOptions? options]) {
    final sw = Stopwatch()..start();
    final defsDict = parsed.defsDict;
    final layerColors = parsed.layerColors;
    final layerIdToName = parsed.layerIdToName;
    final materialIdToName = parsed.materialIdToName;
    final materials = parsed.materials;
    final materialsByFolder = parsed.materialsByFolder;

    emitLog(options, SkpLogLevel.info, 'Building scene: ${defsDict.length} definitions available');
    var instanceCounter = 0;

    int meshCounter = 0;
    final meshIndex = <String, MeshMetadata>{};
    final glbPrimitives = <GlbPrimitive>[];

    // Instance path -> (properties, name) updates, recorded in O(1) per
    // instance and applied once after instantiation completes (see the
    // lookup pass below), instead of scanning the entire meshIndex per
    // placed instance - an O(instances x meshes) substring scan that both
    // dominated build_scene on models with many placed instances and
    // could match the wrong meshes (a shallow instance's path is always a
    // string prefix of every deeper descendant's path too, so `contains`
    // matched far more than intended - see openskp#240).
    final pathUpdates = <String, (Map<String, String>, String)>{};

    // Textures deduplicated by bytes: the same image routinely backs
    // several materials, and re-embedding it per material would multiply
    // the export size for nothing.
    final textures = <SceneTexture>[];
    final textureIndexByKey = <String, int>{};

    int? textureIndexFor(RawTexture? tex) {
      if (tex == null || tex.data == null || tex.data!.isEmpty) return null;
      final data = tex.data!;
      final mimeType = sniffImageMime(data);
      if (mimeType == null) return null; // a format glTF cannot carry
      // length plus a short byte prefix is enough to tell real images
      // apart without hashing megabytes on every face
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

    final colorToMaterialIndex = <((int, int, int), bool, int?, double), int>{};
    final gltfMaterials = <Map<String, dynamic>>[];

    // Definitions currently being instantiated on the active recursion path
    // (not "ever visited" - the same definition legitimately reused by
    // sibling instances is fine). Guards against a component that directly
    // or transitively instances itself, which would otherwise recurse until
    // the stack overflows.
    final activeDefinitions = <int>{};

    (int, int, int) getLayerColor(String name) => layerColors[name] ?? (136, 136, 136);

    int getMaterialIndex((int, int, int) color, bool doubleSided, int? textureIndex,
        [double transparency = 1.0]) {
      // The texture is part of the identity, not just the color: two
      // different images can average to the same RGB (real files do
      // this), and keying on color alone would merge them into one
      // material and lose one of the images.
      final key = (color, doubleSided, textureIndex, transparency);
      final existing = colorToMaterialIndex[key];
      if (existing != null) return existing;
      final idx = gltfMaterials.length;
      final (r, g, b) = color;
      final pbr = <String, dynamic>{
        'baseColorFactor': [r / 255, g / 255, b / 255, transparency],
        'metallicFactor': 0.0,
        'roughnessFactor': 0.8,
      };
      // baseColorFactor stays as the resolved color even with a texture
      // attached: glTF multiplies the two, and SketchUp's own colorized
      // materials rely on exactly that tint.
      if (textureIndex != null) {
        pbr['baseColorTexture'] = {'index': textureIndex};
      }
      final material = <String, dynamic>{'pbrMetallicRoughness': pbr};
      if (doubleSided) material['doubleSided'] = true;
      // glTF's default alphaMode is OPAQUE, which tells a conformant
      // renderer to ignore alpha entirely - both the material's own
      // opacity and any texture's alpha channel. Genuinely translucent
      // materials (glass, water) need BLEND so baseColorFactor's alpha
      // (and the texture's, if any) actually takes effect. A
      // textured-but-otherwise-opaque material gets MASK instead: many
      // SketchUp Warehouse assets (tree foliage, fences, signage) rely on
      // the image's own alpha channel to cut a shape out of an otherwise
      // flat quad, and without MASK a renderer would show the full
      // rectangle. MASK is a no-op for a texture with no real cutout - a
      // fully-opaque alpha channel (or none, as in JPEG) stays above the
      // cutoff everywhere - so this is safe to set unconditionally rather
      // than trying to detect which textures need it.
      if (transparency < 1.0) {
        material['alphaMode'] = 'BLEND';
      } else if (textureIndex != null) {
        material['alphaMode'] = 'MASK';
      }
      gltfMaterials.add(material);
      colorToMaterialIndex[key] = idx;
      return idx;
    }

    List<InstanceNode> instantiateBuilder(
      GeometryBuilder builder,
      String defName,
      int? defId,
      List<double> currentMatrix,
      String parentLayer,
      String pathName,
      (int, int, int)? inheritedColor,
    ) {
      if (builder.faces.isNotEmpty) {
        // Group faces sharing a resolved (color, doubleSided, texture)
        // identity into one mesh each, in local space - shared with the
        // instanced builder (openskp#200) via face_groups.dart: a face
        // whose front/back resolve to the SAME color is emitted once, with
        // its glTF material marked doubleSided so it's visible from either
        // side without needing duplicate geometry; a face whose
        // front/back genuinely differ is emitted as TWO single-sided
        // triangle sets - one normal-wound using the front material, one
        // reverse-wound using the back material - so each side renders
        // its own correct color instead of the front material leaking
        // onto (or the back vanishing from) the far side.
        final fallbackColor = inheritedColor ?? getLayerColor(parentLayer);
        final faceGroups = buildLocalFaceGroups(
          builder,
          FaceGroupContext(
            resolveMaterial: (matId) => _resolveMaterial(matId, materialIdToName, materials, materialsByFolder),
            textureIndexFor: textureIndexFor,
            fallbackColor: fallbackColor,
            definitionId: defId,
          ),
        );

        final isRootPath = pathName == 'ROOT';
        final multiGroup = faceGroups.length > 1;

        for (final groupEntry in faceGroups.entries) {
          final (_, _, texIndex, _) = groupEntry.key;
          final group = groupEntry.value;
          final color = group.color;
          if (group.localFaces.isEmpty) continue;

          final tx = isRootPath ? 0.0 : (currentMatrix.length > 9 ? currentMatrix[9] : 0.0) * _inchesToMm;
          final ty = isRootPath ? 0.0 : (currentMatrix.length > 10 ? currentMatrix[10] : 0.0) * _inchesToMm;
          final tz = isRootPath ? 0.0 : (currentMatrix.length > 11 ? currentMatrix[11] : 0.0) * _inchesToMm;

          var safePath = pathName.replaceAll(' / ', '__').replaceAll(' ', '_');
          if (safePath.length > 80) safePath = safePath.substring(0, 80);
          final colorSuffix = multiGroup
              ? '_${color.$1}_${color.$2}_${color.$3}_${group.doubleSided ? 'ds' : 'ss'}'
              : '';
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
          final uvs = List<double>.filled(vertCount * 2, 0.0);
          final vertexNormalsAccum = group.normalsAccum;

          for (int i = 0; i < vertCount; i++) {
            final v = group.localVerts[i];
            final pt = Transforms.transformPoint(currentMatrix, v);
            positions[i * 3] = pt.$1 * _inchesToM;
            positions[i * 3 + 1] = pt.$3 * _inchesToM;
            positions[i * 3 + 2] = -pt.$2 * _inchesToM;

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

          final materialIndex = getMaterialIndex(color, group.doubleSided, texIndex, group.transparency);
          glbPrimitives.add(GlbPrimitive(
            positions: positions,
            normals: normals,
            uvs: uvs,
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
        // Legacy (pre-2021 MFC) instances carry a precomputed `properties`
        // map (see legacy.dart's extractLegacyDynamicProperties) - VFF
        // instances don't set this, so this stays {} for them and gets
        // overwritten below via the D007/DC05 TLV walk instead.
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

        final instName = (inst.name != null && inst.name!.isNotEmpty) ? inst.name! : 'Component_$refIdx';
        final fullPathName = '$pathName / $instName';
        instanceCounter++;
        if (instanceCounter % progressInterval == 0) {
          emitProgress(options, 'build_scene', instanceCounter, instanceCounter);
          emitLog(options, SkpLogLevel.debug, 'Processed $instanceCounter placed instances');
        }
        final childDef = refIdx != null ? defsDict[refIdx] : null;
        List<InstanceNode> childNodes;
        if (refIdx != null && childDef != null) {
          if (!activeDefinitions.add(refIdx)) {
            throw SkpParseException(
              'Recursive component definition',
              stage: 'build_scene', definitionId: refIdx,
            );
          }
          try {
            childNodes = instantiateBuilder(
                childDef.builder, childDef.name ?? '', refIdx, newMatrix, lName, fullPathName, instColor);
          } finally {
            activeDefinitions.remove(refIdx);
          }
        } else {
          childNodes = <InstanceNode>[];
        }

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

        pathUpdates[fullPathName] = (properties, inst.name ?? '');
      }

      return childInstancesInfo;
    }

    final identityMat = <double>[1.0, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1.0];
    final rootChildren = instantiateBuilder(parsed.root.builder, 'ROOT_MODEL', null, identityMat, 'Layer0', 'ROOT', null);

    // Deferred mesh backfill: each mesh's own path was recorded verbatim
    // as a pathUpdates key by the exact instance that placed the
    // definition that mesh's own faces belong to (never an ancestor's),
    // so a direct O(1) lookup per mesh is enough - no cascading from an
    // ancestor down to its descendants' own meshes. Properties/name are
    // per-instance, not inherited by nested sub-parts, matching how the
    // instance tree above already builds each InstanceNode from that
    // same instance's own `inst.name`/`properties` directly.
    for (final entry in meshIndex.entries) {
      final update = pathUpdates[entry.value.path];
      if (update != null) {
        entry.value.properties = update.$1;
        entry.value.name = update.$2;
      }
    }

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

    emitLog(
      options, SkpLogLevel.info,
      'Scene build complete: $instanceCounter instances, ${meshIndex.length} meshes, '
      '${glbPrimitives.length} primitives (${(sw.elapsedMilliseconds / 1000).toStringAsFixed(2)}s)',
    );

    return Scene(
      sceneHierarchy: sceneHierarchy,
      meshIndex: meshIndex,
      glbPrimitives: glbPrimitives,
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

extension _LastOrNullExt<T> on List<T> {
  T? get lastOrNull => isEmpty ? null : last;
}
