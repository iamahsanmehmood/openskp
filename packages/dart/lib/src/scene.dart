import 'dart:math';

import 'core.dart';
import 'curves.dart';
import 'errors.dart';
import 'face_groups.dart';
import 'geometry.dart';
import 'model.dart' show Page;
import 'observability.dart';
import 'parser.dart' show buildPagesFromRaw;
import 'tlv.dart';
import 'transforms.dart';

/// One node in the baked, world-space instance tree.
class InstanceNode {
  String name;

  /// Whether [name] is a synthetic fallback (SketchUp's own internal
  /// index, e.g. "Component_5") rather than a real name from the source
  /// file - see InstancedNode.nameIsGenerated (instanced_scene.dart) for
  /// the full fallback-order rationale; both resolve a node's display
  /// name identically.
  bool nameIsGenerated;

  String definitionName;
  String layer;
  (double, double, double) positionMm;
  Map<String, String> properties;

  /// Every OTHER attribute dictionary this instance carries, keyed by the
  /// dictionary's own name, values stringified the same way [properties]
  /// already is - [properties] stays exactly SketchUp's own Dynamic
  /// Components data ("dynamic_attributes") for backward compatibility;
  /// third-party plugins (BIM/steel-detailing tools, etc.) commonly attach
  /// their own richer per-instance data under their own dictionary name
  /// instead, which this project never surfaced before (openskp#285).
  Map<String, Map<String, String>> attributeDictionaries;

  /// Real SketchUp instance GUID (VFF/2021+ files only), or `''` when the
  /// source file has none - see InstancedNode.guid (instanced_scene.dart).
  String guid;

  List<InstanceNode> children;

  InstanceNode({
    this.name = '',
    this.nameIsGenerated = false,
    this.definitionName = '',
    this.layer = '',
    this.positionMm = (0.0, 0.0, 0.0),
    Map<String, String>? properties,
    Map<String, Map<String, String>>? attributeDictionaries,
    this.guid = '',
    List<InstanceNode>? children,
  })  : properties = properties ?? {},
        attributeDictionaries = attributeDictionaries ?? {},
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

  /// See InstanceNode.attributeDictionaries.
  Map<String, Map<String, String>> attributeDictionaries;

  String path;

  MeshMetadata({
    this.name = '',
    this.definitionName = '',
    this.layer = '',
    this.positionMm = (0.0, 0.0, 0.0),
    Map<String, String>? properties,
    Map<String, Map<String, String>>? attributeDictionaries,
    this.path = '',
  })  : properties = properties ?? {},
        attributeDictionaries = attributeDictionaries ?? {};
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

  /// Triangle offsets for separately emitted back sides. A primitive can mix
  /// front and back triangles when they share a material; their front
  /// counterparts are still present.
  final List<int> backTriangles;

  /// Index into Scene.gltfMaterials for this primitive's resolved color.
  final int materialIndex;

  /// Matches the corresponding key in Scene.meshIndex.
  final String geomName;

  /// `true` for the back copies of faces with no paint on either side; see
  /// [Scene.surfacePrimitives].
  final bool unpaintedBack;

  GlbPrimitive({
    required this.positions,
    required this.normals,
    required this.uvs,
    required this.indices,
    List<int>? backTriangles,
    required this.materialIndex,
    required this.geomName,
    this.unpaintedBack = false,
  }) : backTriangles = backTriangles ?? const [];
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

/// Metadata for one run of *loose* edges (edges no face uses) in world-space
/// metres, glTF Y-up.
class CurveSetMetadata {
  String name;
  bool nameIsGenerated;
  String definitionName;
  String layer;
  String path;
  List<(double, double, double)> pointsM;
  bool closed;
  CurveArc? arc;
  Map<String, Map<String, String>> attributeDictionaries;

  CurveSetMetadata({
    this.name = '',
    this.nameIsGenerated = false,
    this.definitionName = '',
    this.layer = '',
    this.path = '',
    List<(double, double, double)>? pointsM,
    this.closed = false,
    this.arc,
    Map<String, Map<String, String>>? attributeDictionaries,
  })  : pointsM = pointsM ?? [],
        attributeDictionaries = attributeDictionaries ?? {};
}

/// The result of baking a parsed file's placed instances into a flat,
/// world-space 3D scene.
class Scene {
  InstanceNode sceneHierarchy;
  Map<String, MeshMetadata> meshIndex;
  List<GlbPrimitive> glbPrimitives;
  List<CurveSetMetadata> curveSets;
  List<Map<String, dynamic>> gltfMaterials;

  /// Distinct texture images the placed materials use, deduplicated by
  /// source bytes. Empty when nothing placed in the scene is textured.
  List<SceneTexture> textures;

  /// Number of top-level entities dropped when a classic (pre-2021) root
  /// entity walk stopped early (`0` for complete classic files and VFF files).
  int unreadRootEntities;

  /// Saved scenes (SketchUp's "Scenes" tabs), identical to [SkpModel.pages].
  /// Camera [Page.eye], [Page.target], [Page.up] and [Page.orthoHeight] stay
  /// in SketchUp's native Z-up frame and inches (unlike [glbPrimitives], which
  /// are Y-up metres).
  List<Page> pages;

  /// Each layer's hidden/visible state by layer name, read from the parsed
  /// file (`true` when hidden, `false` when visible).
  Map<String, bool> layerHidden;

  /// [glbPrimitives] without the back copies of faces that have no paint on
  /// either side, for formats and callers that emit one surface per face.
  List<GlbPrimitive> get surfacePrimitives =>
      [for (final p in glbPrimitives) if (!p.unpaintedBack) p];

  Scene({
    required this.sceneHierarchy,
    required this.meshIndex,
    required this.glbPrimitives,
    List<CurveSetMetadata>? curveSets,
    required this.gltfMaterials,
    List<SceneTexture>? textures,
    this.unreadRootEntities = 0,
    List<Page>? pages,
    Map<String, bool>? layerHidden,
  })  : curveSets = curveSets ?? [],
        textures = textures ?? [],
        pages = pages ?? [],
        layerHidden = layerHidden ?? {};
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

const Map<String, String> _originalImageMime = {
  'TIFF': 'image/tiff',
  'GIF': 'image/gif',
  'BMP': 'image/bmp',
  'TGA': 'image/x-tga',
  'WebP': 'image/webp',
  'PSD': 'image/vnd.adobe.photoshop',
  'DDS': 'image/vnd.ms-dds',
  'JPEG 2000': 'image/jp2',
};

bool _asciiAt(List<int> data, int offset, String text) {
  if (offset + text.length > data.length) return false;
  for (var i = 0; i < text.length; i++) {
    if (data[offset + i] != text.codeUnitAt(i)) return false;
  }
  return true;
}

String describeImageFormat(List<int> data, [String filename = '']) {
  if (data.length >= 4 &&
      ((data[0] == 0x49 && data[1] == 0x49 && data[2] == 0x2a && data[3] == 0x00) ||
          (data[0] == 0x4d && data[1] == 0x4d && data[2] == 0x00 && data[3] == 0x2a))) {
    return 'TIFF';
  }
  if (_asciiAt(data, 0, 'BM')) return 'BMP';
  if (_asciiAt(data, 0, 'GIF87a') || _asciiAt(data, 0, 'GIF89a')) return 'GIF';
  if (_asciiAt(data, 0, 'RIFF') && _asciiAt(data, 8, 'WEBP')) return 'WebP';
  if (_asciiAt(data, 0, '8BPS')) return 'PSD';
  if (_asciiAt(data, 0, 'DDS ')) return 'DDS';
  if (data.length >= 8 &&
      data[0] == 0x00 &&
      data[1] == 0x00 &&
      data[2] == 0x00 &&
      data[3] == 0x0c &&
      _asciiAt(data, 4, 'jP  ')) {
    return 'JPEG 2000';
  }
  final tailStart = data.length > 26 ? data.length - 26 : 0;
  for (var i = tailStart; i + 16 <= data.length; i++) {
    if (_asciiAt(data, i, 'TRUEVISION-XFILE')) return 'TGA';
  }
  final baseName = filename.replaceAll('\\', '/').split('/').last;
  final dot = baseName.lastIndexOf('.');
  final ext = dot >= 0 ? baseName.substring(dot + 1) : '';
  return ext.isNotEmpty && ext.length <= 5 ? ext.toUpperCase() : 'an unrecognized format';
}

String? originalImageMime(List<int> data) => _originalImageMime[describeImageFormat(data)];

void warnSkippedTexture(
  ParseOptions? options,
  Set<(String, String)> warned,
  String? materialName,
  RawTexture tex,
  List<int> data,
) {
  final filename = tex.filename;
  final fmt = describeImageFormat(data, filename);
  if (!warned.add((materialName ?? '', fmt))) return;
  final baseName = filename.replaceAll('\\', '/').split('/').last;
  final keepable = originalImageMime(data) != null;
  final label = (materialName != null && materialName.isNotEmpty)
      ? "'$materialName'"
      : "'(unnamed)'";
  final suffix = baseName.isNotEmpty ? ' ($baseName)' : '';
  final hint = keepable ? ' (pass originalTextures: true to keep it in its own format)' : '';
  emitLog(
    options,
    SkpLogLevel.warning,
    'Texture of material $label$suffix is $fmt, which a scene cannot carry (only PNG and JPEG): '
    'the material is built without its texture$hint',
  );
}

int _fnv1a32(List<int> data) {
  var h = 0x811c9dc5;
  for (var i = 0; i < data.length; i++) {
    h ^= data[i] & 0xff;
    h = (h * 0x01000193) & 0xffffffff;
  }
  return h;
}

bool _bytesEqual(List<int> a, List<int> b) {
  if (identical(a, b)) return true;
  if (a.length != b.length) return false;
  for (var i = 0; i < a.length; i++) {
    if (a[i] != b[i]) return false;
  }
  return true;
}

int? Function(RawTexture?, [String?]) createTextureIndexFor(
  List<SceneTexture> textures,
  ParseOptions? options,
) {
  final indexByIdentity = Map<List<int>, int>.identity();
  final buckets = <int, List<int>>{};
  final skippedTextures = <(String, String)>{};

  return (RawTexture? tex, [String? materialName]) {
    if (tex == null || tex.data == null || tex.data!.isEmpty) return null;
    final data = tex.data!;
    final cached = indexByIdentity[data];
    if (cached != null) return cached;

    var mimeType = sniffImageMime(data);
    if (mimeType == null && (options?.originalTextures ?? false)) {
      mimeType = originalImageMime(data);
    }
    if (mimeType == null) {
      warnSkippedTexture(options, skippedTextures, materialName, tex, data);
      return null;
    }

    final hash = _fnv1a32(data);
    final bucket = buckets[hash];
    if (bucket != null) {
      for (final cand in bucket) {
        if (_bytesEqual(textures[cand].data, data)) {
          indexByIdentity[data] = cand;
          return cand;
        }
      }
    }

    final idx = textures.length;
    textures.add(SceneTexture(data: data, mimeType: mimeType, filename: tex.filename));
    (bucket ?? (buckets[hash] = <int>[])).add(idx);
    indexByIdentity[data] = idx;
    return idx;
  };
}

double _srgbToLinear(double c) {
  if (c <= 0.0) return 0.0;
  if (c >= 1.0) return 1.0;
  return c <= 0.04045 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4).toDouble();
}

Map<String, dynamic> _linearizePbr(Map<String, dynamic> pbr, {bool stripTexture = false}) {
  final newPbr = Map<String, dynamic>.from(pbr);
  if (stripTexture) newPbr.remove('baseColorTexture');
  final bcf = newPbr['baseColorFactor'];
  if (bcf is List && bcf.length >= 3) {
    newPbr['baseColorFactor'] = <double>[
      _srgbToLinear((bcf[0] as num).toDouble()),
      _srgbToLinear((bcf[1] as num).toDouble()),
      _srgbToLinear((bcf[2] as num).toDouble()),
      bcf.length > 3 ? (bcf[3] as num).toDouble() : 1.0,
    ];
  }
  return newPbr;
}

(List<SceneTexture>, List<Map<String, dynamic>>) filterGltfTextures(
  List<SceneTexture> rawTextures,
  List<Map<String, dynamic>> rawMaterials,
  bool embed,
  void Function(SkpLogLevel level, String message)? onLog,
) {
  if (!embed || rawTextures.isEmpty) {
    final stripped = rawMaterials.map((m) {
      final pbr = m['pbrMetallicRoughness'] as Map<String, dynamic>?;
      if (pbr == null) return m;
      final newPbr = _linearizePbr(pbr, stripTexture: pbr.containsKey('baseColorTexture'));
      return Map<String, dynamic>.from(m)..['pbrMetallicRoughness'] = newPbr;
    }).toList();
    return (<SceneTexture>[], stripped);
  }

  final kept = <SceneTexture>[];
  final remap = List<int?>.filled(rawTextures.length, null);
  final warned = <(String, String)>{};

  for (var i = 0; i < rawTextures.length; i++) {
    final tex = rawTextures[i];
    if (tex.mimeType == 'image/png' || tex.mimeType == 'image/jpeg') {
      remap[i] = kept.length;
      kept.add(tex);
    } else {
      remap[i] = null;
      final baseName = tex.filename.replaceAll('\\', '/').split('/').last;
      final fmt = describeImageFormat(tex.data, tex.filename);
      if (warned.add((baseName, fmt))) {
        final label = baseName.isNotEmpty ? " '$baseName'" : '';
        onLog?.call(
          SkpLogLevel.warning,
          'Texture$label is $fmt, which glTF 2.0 cannot carry (only PNG and JPEG): leaving it out of the GLB',
        );
      }
    }
  }

  final materials = rawMaterials.map((m) {
    final pbr = m['pbrMetallicRoughness'] as Map<String, dynamic>?;
    if (pbr == null) return m;
    final texRef = pbr['baseColorTexture'] as Map<String, dynamic>?;
    final texIdx = texRef?['index'];
    if (texRef == null || texIdx is! int) {
      return Map<String, dynamic>.from(m)..['pbrMetallicRoughness'] = _linearizePbr(pbr);
    }
    final mapped = texIdx >= 0 && texIdx < remap.length ? remap[texIdx] : null;
    if (mapped == null) {
      final newPbr = _linearizePbr(pbr, stripTexture: true);
      final newMat = Map<String, dynamic>.from(m)..['pbrMetallicRoughness'] = newPbr;
      if (newMat['alphaMode'] == 'MASK') newMat.remove('alphaMode');
      return newMat;
    }
    if (mapped == texIdx) return m;
    final newTexRef = Map<String, dynamic>.from(texRef)..['index'] = mapped;
    final newPbr = Map<String, dynamic>.from(pbr)..['baseColorTexture'] = newTexRef;
    return Map<String, dynamic>.from(m)..['pbrMetallicRoughness'] = newPbr;
  }).toList();

  return (kept, materials);
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
    final pathUpdates = <String, (Map<String, String>, String, Map<String, Map<String, String>>)>{};

    // Textures deduplicated by full content (with an object-identity fast
    // path per face): the same image routinely backs several materials,
    // while equal-size PNGs/TIFFs share their 16-byte header and must stay
    // distinct.
    final textures = <SceneTexture>[];
    final textureIndexFor = createTextureIndexFor(textures, options);

    final colorToMaterialIndex =
        <((int, int, int), bool, int?, double, bool, int), int>{};
    final gltfMaterials = <Map<String, dynamic>>[];
    final curveSets = <CurveSetMetadata>[];
    final curveSeq = <String, int>{};
    final includeCurveSets = options?.includeCurveSets ?? true;
    final preparedCurvesCache = <int, PreparedDefCurves>{};

    // Definitions currently being instantiated on the active recursion path
    // (not "ever visited" - the same definition legitimately reused by
    // sibling instances is fine). Guards against a component that directly
    // or transitively instances itself, which would otherwise recurse until
    // the stack overflows.
    final activeDefinitions = <int>{};

    (int, int, int) getLayerColor(String name) => layerColors[name] ?? (136, 136, 136);
    final styleColors = unpaintedStyleColors(parsed, options?.unpaintedFaces);

    int getMaterialIndex(
      (int, int, int) color,
      bool doubleSided,
      int? textureIndex, [
      double transparency = 1.0,
      bool colorized = false,
      int colorizeType = 0,
    ]) {
      // The texture is part of the identity, not just the color: two
      // different images can average to the same RGB (real files do
      // this), and keying on color alone would merge them into one
      // material and lose one of the images.
      final effectiveColorized = colorized && textureIndex != null;
      final effectiveColorizeType = effectiveColorized ? colorizeType : 0;
      final key = (
        color,
        doubleSided,
        textureIndex,
        transparency,
        effectiveColorized,
        effectiveColorizeType,
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
      // baseColorFactor stays as the resolved color even with a texture
      // attached: glTF multiplies the two, and SketchUp's own colorized
      // materials rely on exactly that tint.
      if (textureIndex != null) {
        pbr['baseColorTexture'] = {'index': textureIndex};
      }
      final material = <String, dynamic>{'pbrMetallicRoughness': pbr};
      if (effectiveColorized) {
        material['extras'] = <String, dynamic>{
          'sketchup': <String, dynamic>{
            'colorized': true,
            'colorize_type': effectiveColorizeType,
          },
        };
      }
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

    final faceLayerCache = <int, (String?, String?)>{};
    (String?, String?) getFaceLayerSummary(int? defId, GeometryBuilder builder) {
      final key = defId ?? -1;
      final hit = faceLayerCache[key];
      if (hit != null || faceLayerCache.containsKey(key)) {
        return hit ?? (null, null);
      }
      final summary = Geometry.summarizeFaceLayers(builder, layerIdToName);
      faceLayerCache[key] = summary;
      return summary;
    }

    List<InstanceNode> instantiateBuilder(
      GeometryBuilder builder,
      String defName,
      int? defId,
      List<double> currentMatrix,
      String parentLayer,
      String pathName,
      (int, int, int)? inheritedColor, [
      RawMaterial? inheritedMaterial,
    ]) {
      final summary = getFaceLayerSummary(defId, builder);
      final meshLayer = parentLayer == 'Layer0' ? (summary.$1 ?? parentLayer) : parentLayer;
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
        final (int, int, int) fallbackColor;
        final (int, int, int) fallbackBackColor;
        if (inheritedColor != null) {
          fallbackColor = fallbackBackColor = inheritedColor;
        } else if (styleColors != null) {
          (fallbackColor, fallbackBackColor) = styleColors;
        } else {
          final colorLayer = summary.$2 ?? parentLayer;
          fallbackColor = fallbackBackColor = getLayerColor(colorLayer);
        }
        final faceGroups = buildLocalFaceGroups(
          builder,
          FaceGroupContext(
            resolveMaterial: (matId) =>
                _resolveMaterial(matId, materialIdToName, materials, materialsByFolder) ??
                inheritedMaterial,
            textureIndexFor: textureIndexFor,
            fallbackColor: fallbackColor,
            fallbackBackColor: fallbackBackColor,
            definitionId: defId,
          ),
        );

        final isRootPath = pathName == 'ROOT';
        final multiGroup = faceGroups.length > 1;

        for (final groupEntry in faceGroups.entries) {
          final (_, _, texIndex, _, unpaintedBack, _, _) = groupEntry.key;
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
          final geomName = 'mesh_${meshCounter}_${safePath}_$meshLayer$colorSuffix';
          meshCounter++;

          meshIndex[geomName] = MeshMetadata(
            name: isRootPath ? 'ROOT' : (pathName.split(' / ').lastOrNull ?? ''),
            definitionName: defName,
            layer: meshLayer,
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

          final materialIndex = getMaterialIndex(
            color,
            group.doubleSided,
            texIndex,
            group.transparency,
            group.colorized,
            group.colorizeType,
          );
          glbPrimitives.add(GlbPrimitive(
            positions: positions,
            normals: normals,
            uvs: uvs,
            indices: indices,
            backTriangles: List<int>.of(group.backTriangles),
            materialIndex: materialIndex,
            geomName: geomName,
            unpaintedBack: unpaintedBack,
          ));
        }
      }

      if (includeCurveSets) {
        final prepKey = defId ?? -1;
        var prepared = preparedCurvesCache[prepKey];
        if (prepared == null) {
          prepared = prepareDefinitionCurves(builder, layerIdToName);
          preparedCurvesCache[prepKey] = prepared;
        }
        if (prepared.runs.isNotEmpty) {
          final fm = currentMatrix.length >= 12 ? Transforms.foldScale(currentMatrix) : null;
          final compName = pathName == 'ROOT' ? '' : (pathName.split(' / ').lastOrNull ?? '');
          for (final run in prepared.runs) {
            final pts = <(double, double, double)>[];
            if (fm == null) {
              for (final v in run.localVerts) {
                pts.add((
                  round6(v.$1 * _inchesToM),
                  round6(v.$3 * _inchesToM),
                  round6(-v.$2 * _inchesToM),
                ));
              }
            } else {
              for (final v in run.localVerts) {
                final px = fm[0] * v.$1 + fm[1] * v.$2 + fm[2] * v.$3 + fm[9];
                final py = fm[3] * v.$1 + fm[4] * v.$2 + fm[5] * v.$3 + fm[10];
                final pz = fm[6] * v.$1 + fm[7] * v.$2 + fm[8] * v.$3 + fm[11];
                pts.add((
                  round6(px * _inchesToM),
                  round6(pz * _inchesToM),
                  round6(-py * _inchesToM),
                ));
              }
            }

            final runLayer = run.explicitLayer ?? meshLayer;
            String cname;
            bool cgen;
            if (pathName == 'ROOT') {
              final seq = (curveSeq[runLayer] ?? 0) + 1;
              curveSeq[runLayer] = seq;
              cname = '${runLayer}_$seq';
              cgen = true;
            } else {
              cname = compName;
              cgen = false;
            }

            curveSets.add(CurveSetMetadata(
              name: cname,
              nameIsGenerated: cgen,
              definitionName: defName,
              layer: runLayer,
              path: pathName,
              pointsM: pts,
              closed: run.closed,
              arc: solveRunArc(
                builder,
                run.edgeIds,
                fm,
                pts,
                closed: run.closed,
                curveEdgeCounts: prepared.curveEdgeCounts,
              ),
              attributeDictionaries: <String, Map<String, String>>{},
            ));
          }
        }
      }

      final childInstancesInfo = <InstanceNode>[];
      for (final inst in builder.instances) {
        final refIdx = inst.refIdx;
        final newMatrix = Transforms.multiplyMatrices(currentMatrix, inst.matrix);

        var lName = parentLayer;
        (int, int, int)? instColor = inheritedColor;
        RawMaterial? instMaterial = inheritedMaterial;
        // Legacy (pre-2021 MFC) instances carry a precomputed `properties`
        // map (see legacy.dart's extractLegacyDynamicProperties) - VFF
        // instances don't set this, so this stays {} for them and gets
        // overwritten below via the D007/DC05 TLV walk instead.
        var properties = Map<String, String>.from(inst.properties ?? {});
        Map<String, Map<String, Object?>>? instAttributeDicts = inst.attributeDicts;

        // A classic (pre-2021) instance has no D007: its own tag and paint
        // were read with it.
        if (inst.layerId != null) {
          lName = layerIdToName[inst.layerId!] ?? parentLayer;
        }
        final painted =
            _resolveMaterial(inst.materialId, materialIdToName, materials, materialsByFolder);
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

        final childDef = refIdx != null ? defsDict[refIdx] : null;
        // Fallback order: an attribute-dict name/label/code override, then
        // the instance's own explicit name, then the definition's own
        // name IF it's not itself just SketchUp's auto-generated
        // "Group#1"/"Component#12" placeholder, then finally the internal
        // index. Mirrors instanced_scene.dart's identical resolution (and
        // Python's/C++'s own scene.py/instanced_scene.py) - see
        // instanced_fixture_parity_test.dart, which depends on both trees
        // resolving display names identically.
        final childDefName = childDef?.name ?? '';
        final defNameIsReal = childDefName.isNotEmpty && !Geometry.isGenericDefinitionName(childDefName);
        final nameOverride = Geometry.findNameOverride(instAttributeDicts);
        final instNameNonEmpty = inst.name != null && inst.name!.isNotEmpty;
        final instName = instNameNonEmpty ? inst.name! : (defNameIsReal ? childDefName : 'Component_$refIdx');
        final displayName = nameOverride ?? instName;
        final nameIsGenerated = nameOverride == null && !instNameNonEmpty && !defNameIsReal;

        final fullPathName = '$pathName / $instName';
        instanceCounter++;
        if (instanceCounter % progressInterval == 0) {
          emitProgress(options, 'build_scene', instanceCounter, instanceCounter);
          emitLog(options, SkpLogLevel.debug, 'Processed $instanceCounter placed instances');
        }
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
              childDef.builder,
              childDef.name ?? '',
              refIdx,
              newMatrix,
              lName,
              fullPathName,
              instColor,
              instMaterial,
            );
          } finally {
            activeDefinitions.remove(refIdx);
          }
        } else {
          childNodes = <InstanceNode>[];
        }

        final itx = newMatrix.length > 9 ? newMatrix[9] * _inchesToMm : 0.0;
        final ity = newMatrix.length > 10 ? newMatrix[10] * _inchesToMm : 0.0;
        final itz = newMatrix.length > 11 ? newMatrix[11] * _inchesToMm : 0.0;

        // Every OTHER attribute dictionary this instance carries -
        // dynamic_attributes is already surfaced separately as properties
        // above, and SU_InstanceSet is SketchUp's own always-present,
        // always-empty Owner/Status boilerplate, not worth surfacing.
        // Mirrors Python's own attribute_dictionaries construction in
        // scene.py exactly (openskp#285).
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
            (lName == 'Layer0' && childNodes.isEmpty && refIdx != null && childDef != null)
                ? (getFaceLayerSummary(refIdx, childDef.builder).$1 ?? lName)
                : lName;

        final instInfo = InstanceNode(
          name: displayName,
          nameIsGenerated: nameIsGenerated,
          definitionName: childDefName,
          layer: nodeLayer,
          positionMm: (_round2(itx), _round2(ity), _round2(itz)),
          properties: properties,
          attributeDictionaries: attributeDictionaries,
          guid: inst.refGuid ?? '',
          children: childNodes,
        );
        childInstancesInfo.add(instInfo);

        pathUpdates[fullPathName] = (properties, displayName, attributeDictionaries);
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
        entry.value.attributeDictionaries = update.$3;
      }
    }

    for (final entry in meshIndex.entries) {
      final existing = entry.value;
      if (existing.path == 'ROOT') {
        if (existing.name.isEmpty) existing.name = 'ROOT';
        if (existing.definitionName.isEmpty) existing.definitionName = 'ROOT_MODEL';
        if (existing.layer.isEmpty) existing.layer = 'Layer0';
        existing.positionMm = (0.0, 0.0, 0.0);
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
      curveSets: curveSets,
      gltfMaterials: gltfMaterials,
      textures: textures,
      unreadRootEntities: parsed.unreadRootEntities,
      pages: buildPagesFromRaw(parsed),
      layerHidden: Map<String, bool>.from(parsed.layerHidden),
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
