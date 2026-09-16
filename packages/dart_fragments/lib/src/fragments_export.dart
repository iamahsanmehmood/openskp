import 'dart:convert';
import 'dart:io';
import 'dart:math';
import 'dart:typed_data';

import 'package:archive/archive.dart';
import 'package:flat_buffers/flat_buffers.dart' as fb;
import 'package:openskp/openskp.dart';

import 'fragments_fb/fragments_generated.dart' as ffb;

/// Direct SKP -> ThatOpen Fragments (.frag) export - EXPERIMENTAL. A port
/// of Python's `openskp.export.fragments.to_fragments` (see that module's
/// own docstring for the full rationale and real-loader verification
/// status this port inherits) against the same vendored ThatOpen
/// Fragments schema (`fragments_fb/index.fbs`).
///
/// EXPORT ONLY, matching the TypeScript/.NET ports - not Python's or
/// C++'s read side (neither of those two has it either, so this isn't a
/// gap unique to Dart). Built at full Python/C++/TypeScript/.NET GUID/
/// name-is-generated/layer-hidden fidelity from the start:
/// InstancedNode/InstancedScene in the core openskp package already carry
/// real per-instance source GUIDs, a generated-name flag, and the source
/// file's layer-hidden state (added alongside this port - see the core
/// package's Geometry.extractAttributeDictionaries).
///
/// Ships as its own package (rather than living in the core openskp
/// package) because it needs `package:flat_buffers` - the core package
/// has zero external dependencies today, and this mirrors Python's own
/// opt-in `pip install openskp[fragments]` extra instead of forcing every
/// openskp Dart consumer to take a transitive dependency they don't need.
///
/// See `lib/src/fragments_fb/index.fbs`'s own comment for why the Dart
/// bindings are generated from a locally-patched schema copy (flatc's
/// Dart backend doesn't support FlatBuffers' optional-scalar feature) -
/// wire-format-safe for this write-only module.

// Fragments' Shell uses `ushort` point indices by default; a shell with
// more points than this must use the wide BigShell encoding (`uint`
// indices) instead - confirmed against the real importer's own
// `points.length > ushortMaxValue` check.
const int _ushortMax = 65535;

// Per-axis scale magnitudes within this of 1.0 are treated as exactly
// unit scale for cache-key rounding purposes - matches typical
// floating-point accumulation noise from matrix composition, not a
// meaningful tolerance for an actually-intended resize. Matches Python's/
// C++'s/.NET's own value; TypeScript independently chose 4, a harmless
// difference since the rounding only affects shell-dedup cache keys,
// never the written geometry.
const int _scaleRoundNdigits = 6;

const List<double> _identityMatrix = [
  1.0, 0.0, 0.0, 0.0,
  0.0, 1.0, 0.0, 0.0,
  0.0, 0.0, 1.0, 0.0,
  0.0, 0.0, 0.0, 1.0,
];

// Column-major 4x4 multiply, a*b - same convention as
// instanced_scene.dart's own internal matrix composition.
List<double> _mat4Mul(List<double> a, List<double> b) {
  final out = List<double>.filled(16, 0.0);
  for (var col = 0; col < 4; col++) {
    for (var row = 0; row < 4; row++) {
      var s = 0.0;
      for (var k = 0; k < 4; k++) {
        s += a[k * 4 + row] * b[col * 4 + k];
      }
      out[col * 4 + row] = s;
    }
  }
  return out;
}

class _Leaf {
  final InstancedNode node;
  final List<double> world;
  _Leaf(this.node, this.world);
}

// Walk the instanced scene's tree, accumulating each node's GLOBAL
// (world) transform, and return every node worth tracking as its own
// item - a leaf carrying geometry, OR a real, named organizational
// wrapper with no geometry of its own (e.g. a SketchUp group like "W-2"
// that only exists to hold several separately-meshed parts). Mirrors
// Python's/C++'s/.NET's/TypeScript's own collect_leaves exactly, using
// the real nameIsGenerated flag.
void _collectLeaves(InstancedNode node, InstancedNode root, List<double> parentMatrix, List<_Leaf> out) {
  final world = _mat4Mul(parentMatrix, node.matrix);
  final isNamedWrapper = !identical(node, root) && !node.nameIsGenerated;
  if (node.meshResourceId != null || isNamedWrapper) {
    out.add(_Leaf(node, world));
  }
  for (final child in node.children) {
    _collectLeaves(child, root, world, out);
  }
}

/// Decompose a column-major 4x4 instance transform into
/// (position, xDir, yDir, scale, mirrored) - a direct port of Python's
/// `_decompose_trs`. See that function's own docstring for why mirroring
/// is resolved this way.
class Trs {
  final List<double> position;
  final List<double> xDir;
  final List<double> yDir;
  final List<double> scale;
  final bool mirrored;
  Trs(this.position, this.xDir, this.yDir, this.scale, this.mirrored);
}

Trs decomposeTrs(List<double> m) {
  final xAxis = [m[0], m[1], m[2]];
  final yAxis = [m[4], m[5], m[6]];
  final zAxis = [m[8], m[9], m[10]];
  final pos = [m[12], m[13], m[14]];

  double norm(List<double> v) {
    final s = sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
    return s > 0 ? s : 1.0;
  }

  final sx = norm(xAxis);
  final sy = norm(yAxis);
  final sz = norm(zAxis);

  final det = xAxis[0] * (yAxis[1] * zAxis[2] - yAxis[2] * zAxis[1]) -
      xAxis[1] * (yAxis[0] * zAxis[2] - yAxis[2] * zAxis[0]) +
      xAxis[2] * (yAxis[0] * zAxis[1] - yAxis[1] * zAxis[0]);
  final mirrored = det < 0.0;

  final xDir = [xAxis[0] / sx, xAxis[1] / sx, xAxis[2] / sx];
  if (mirrored) {
    xDir[0] = -xDir[0];
    xDir[1] = -xDir[1];
    xDir[2] = -xDir[2];
  }
  final yDir = [yAxis[0] / sy, yAxis[1] / sy, yAxis[2] / sy];

  return Trs(pos, xDir, yDir, [sx, sy, sz], mirrored);
}

class BakedGeometry {
  final List<List<double>> points;
  final List<List<int>> triangles;
  BakedGeometry(this.points, this.triangles);
}

/// Apply an instance's scale/mirror directly to a copy of its resource's
/// LOCAL points and triangle winding, so the resulting geometry is
/// correct when placed by a purely rigid (rotation + translation, no
/// scale) Transform - a direct port of Python's `_bake_primitive`.
BakedGeometry bakePrimitive(LocalPrimitive prim, List<double> scale, bool mirrored) {
  final sx = mirrored ? -scale[0] : scale[0];
  final sy = scale[1];
  final sz = scale[2];

  final positions = prim.positions;
  final nVerts = positions.length ~/ 3;
  final points = <List<double>>[];
  for (var i = 0; i < nVerts; i++) {
    points.add([
      positions[i * 3] * sx,
      positions[i * 3 + 1] * sy,
      positions[i * 3 + 2] * sz,
    ]);
  }

  final indices = prim.indices;
  final nTris = indices.length ~/ 3;
  final triangles = <List<int>>[];
  for (var i = 0; i < nTris; i++) {
    var tri = [indices[i * 3], indices[i * 3 + 1], indices[i * 3 + 2]];
    if (mirrored) {
      tri = [tri[0], tri[2], tri[1]];
    }
    triangles.add(tri);
  }
  return BakedGeometry(points, triangles);
}

/// Round a (mirrored, scale) triple to a stable cache key - the mirror
/// flag folds into the X component's sign, since [bakePrimitive] only
/// ever negates X for a mirrored instance. Matches Python's
/// `_scale_cache_key`.
String scaleCacheKey(bool mirrored, List<double> scale) {
  final mult = pow(10, _scaleRoundNdigits);
  double rnd(double v) => (v * mult).round() / mult;
  final sx = mirrored ? -scale[0] : scale[0];
  return '${rnd(sx)}_${rnd(scale[1])}_${rnd(scale[2])}';
}

(int, int, int, int) _extractBaseColor(Map<String, dynamic>? gltfMat) {
  if (gltfMat != null) {
    final pbr = gltfMat['pbrMetallicRoughness'];
    if (pbr is Map) {
      final bcf = pbr['baseColorFactor'];
      if (bcf is List && bcf.length >= 3) {
        int clampByte(num c) => (c * 255).round().clamp(0, 255);
        final a = bcf.length > 3 ? clampByte(bcf[3] as num) : 255;
        return (clampByte(bcf[0] as num), clampByte(bcf[1] as num), clampByte(bcf[2] as num), a);
      }
    }
  }
  return (255, 255, 255, 255);
}

String _nameAttributeJson(String name) => jsonEncode(['Name', name, 'STRING']);

/// Options for [toFragments].
class FragmentExportOptions {
  /// Model GUID / identifier. Default: "00000000-0000-0000-0000-000000000000".
  final String? modelId;

  /// If true, returns the raw uncompressed FlatBuffers buffer instead of
  /// zlib-deflated. Default: false.
  final bool raw;

  const FragmentExportOptions({this.modelId, this.raw = false});
}

/// Export an [InstancedScene] (from `InstancedSceneBuilder.build`)
/// directly to ThatOpen Fragments (.frag) binary format.
///
/// Uses generated FlatBuffers bindings from ThatOpen's own `index.fbs`
/// schema, with TRS matrix decomposition and scale/mirror geometry
/// baking for full visual parity with the real ThatOpen `IfcImporter`'s
/// own output shape.
Uint8List toFragments(InstancedScene scene, [FragmentExportOptions options = const FragmentExportOptions()]) {
  final leaves = <_Leaf>[];
  _collectLeaves(scene.sceneHierarchy, scene.sceneHierarchy, _identityMatrix, leaves);

  final resourceById = {for (final r in scene.meshResources) r.id: r};

  final builder = fb.Builder(deduplicateTables: false);

  // ---- Shells/Representations/Materials, built lazily as leaves are
  // walked below: keyed by (resource, primitive, baked scale) so every
  // placement sharing the same definition AND the same scale/mirror
  // state dedupes onto one Shell - only a genuinely distinct scale
  // factor pays for its own geometry copy. ----
  final shellKeyToIndex = <String, List<int>>{};
  final shellOffsets = <int>[];
  final representationBoundsMin = <List<double>>[];
  final representationBoundsMax = <List<double>>[];
  final materialKeyToIndex = <int, int>{};
  final materialRgba = <(int, int, int, int)>[];

  int getMaterialIndex(int materialIndex) {
    final cached = materialKeyToIndex[materialIndex];
    if (cached != null) return cached;
    Map<String, dynamic>? gltfMat;
    if (materialIndex >= 0 && materialIndex < scene.gltfMaterials.length) {
      gltfMat = scene.gltfMaterials[materialIndex];
    }
    final rgba = _extractBaseColor(gltfMat);
    final idx = materialRgba.length;
    materialRgba.add(rgba);
    materialKeyToIndex[materialIndex] = idx;
    return idx;
  }

  int bakeOneShell(List<List<double>> points, List<List<int>> triangles) {
    final isBig = points.length > _ushortMax;

    final profileOffsets = <int>[];
    final bigProfileOffsets = <int>[];
    for (final tri in triangles) {
      if (isBig) {
        final idxVecOffset = builder.writeListUint32(tri);
        final b = ffb.BigShellProfileBuilder(builder);
        b.begin();
        b.addIndicesOffset(idxVecOffset);
        bigProfileOffsets.add(b.finish());
      } else {
        final idxVecOffset = builder.writeListUint16(tri);
        final b = ffb.ShellProfileBuilder(builder);
        b.begin();
        b.addIndicesOffset(idxVecOffset);
        profileOffsets.add(b.finish());
      }
    }

    // profiles/bigProfiles are BOTH required fields on Shell, but only
    // one of the two encodings is ever real for a given shell (the other
    // is just an empty vector).
    final profilesVec = builder.writeList(isBig ? const [] : profileOffsets);
    final bigProfilesVec = builder.writeList(isBig ? bigProfileOffsets : const []);
    final holesVec = builder.writeList(const []);
    final bigHolesVec = builder.writeList(const []);

    final pointBuilders = [
      for (final p in points) ffb.FloatVectorObjectBuilder(x: p[0], y: p[1], z: p[2]),
    ];
    final pointsVec = builder.writeListOfStructs(pointBuilders);

    // Sequential per-shell profile ids (0..triangles.length-1, not tied to
    // any upstream SketchUp face identity - see the caller for how a
    // too-large triangle list is chunked before reaching here), so
    // re-numbering from 0 per shell is exactly consistent with the
    // single-shell behavior this is a straight extraction of.
    final faceIds = [for (var i = 0; i < triangles.length; i++) i];
    final faceIdsVec = builder.writeListUint16(faceIds);

    final shellB = ffb.ShellBuilder(builder);
    shellB.begin();
    shellB.addProfilesOffset(profilesVec);
    shellB.addBigProfilesOffset(bigProfilesVec);
    shellB.addHolesOffset(holesVec);
    shellB.addBigHolesOffset(bigHolesVec);
    shellB.addPointsOffset(pointsVec);
    shellB.addType(isBig ? ffb.ShellType.BIG : ffb.ShellType.NONE);
    shellB.addProfilesFaceIdsOffset(faceIdsVec);

    final index = shellOffsets.length;
    shellOffsets.add(shellB.finish());

    var minX = double.infinity, minY = double.infinity, minZ = double.infinity;
    var maxX = double.negativeInfinity, maxY = double.negativeInfinity, maxZ = double.negativeInfinity;
    for (final p in points) {
      if (p[0] < minX) minX = p[0];
      if (p[0] > maxX) maxX = p[0];
      if (p[1] < minY) minY = p[1];
      if (p[1] > maxY) maxY = p[1];
      if (p[2] < minZ) minZ = p[2];
      if (p[2] > maxZ) maxZ = p[2];
    }
    if (minX == double.infinity) {
      minX = minY = minZ = 0;
      maxX = maxY = maxZ = 0;
    }
    representationBoundsMin.add([minX, minY, minZ]);
    representationBoundsMax.add([maxX, maxY, maxZ]);

    return index;
  }

  List<int> getOrBakeShell(String resourceId, int primIdx, LocalPrimitive prim, List<double> scale, bool mirrored) {
    final sk = scaleCacheKey(mirrored, scale);
    final key = '$resourceId:$primIdx:$sk';
    final cached = shellKeyToIndex[key];
    if (cached != null) return cached;

    final baked = bakePrimitive(prim, scale, mirrored);

    // `profilesFaceIds` (written above) has no "big"/uint32 counterpart
    // anywhere in the real Fragments schema (index.fbs only declares
    // `profiles_face_ids: [ushort]` - unlike points, which DO get a
    // BigShellProfile/uint32-index escape hatch past 65535 of them). A
    // single shell genuinely cannot represent more than 65535 triangles no
    // matter how points are encoded - see Python's own fix (openskp#285/
    // PR #355) for the real production incident this was ported from.
    // Splitting into multiple shells, each within the ushort limit, is the
    // only way to represent this - the format has no cap on shell COUNT,
    // just per-shell triangle count. Each sub-shell duplicates the full
    // (shared) points array rather than remapping to a local subset:
    // simpler and lower-risk than a vertex-remapping pass, at the cost of
    // some extra file size in this rare oversized-mesh case.
    List<int> indices;
    if (baked.triangles.length > _ushortMax) {
      indices = [];
      for (var i = 0; i < baked.triangles.length; i += _ushortMax) {
        final end = (i + _ushortMax < baked.triangles.length) ? i + _ushortMax : baked.triangles.length;
        indices.add(bakeOneShell(baked.points, baked.triangles.sublist(i, end)));
      }
    } else {
      indices = [bakeOneShell(baked.points, baked.triangles)];
    }

    shellKeyToIndex[key] = indices;
    return indices;
  }

  // ---- Model-level items + geometry samples: one item per leaf
  // placement, one sample per (leaf, primitive-of-its-resource) pair. ----
  final localIds = <int>[];
  final categories = <String>[];
  final names = <String>[];
  final guids = <String>[];
  // GUIDs of items whose name is a fallback this project generated (no
  // real name anywhere in the source file), not something a person or
  // plugin actually named - see InstancedNode.nameIsGenerated. Carried in
  // Model.metadata below, same mechanism as layerHidden, since the public
  // Fragments schema has no field for this either.
  final generatedNameGuids = <String>[];
  final sampleMaterial = <int>[];
  final sampleRepresentation = <int>[];
  final meshesItems = <int>[];
  final globalTransformData = <Trs>[];
  final itemIndexByNode = <InstancedNode, int>{};

  // Real-world SketchUp files can carry a non-unique per-instance GUID:
  // an engineer authors one instance (a framing plugin writes its own
  // identity into that instance's attribute dictionary), then duplicates
  // it 10-20 times via SketchUp's own native Copy/Move+Copy/Array tools
  // instead of re-running the plugin per placement. A plain SketchUp
  // entity duplication carries the source instance's attribute
  // dictionaries - and whatever GUID field a plugin wrote into one - to
  // every copy verbatim; each copy gets its own distinct transform but
  // not its own distinct identity (openskp#290). The first instance to
  // claim a real GUID keeps it; every later instance sharing that same
  // value falls back to a synthetic one instead. Mirrors Python's/C++'s/
  // .NET's/TypeScript's own seenGuids handling exactly.
  final seenGuids = <String>{};

  for (var itemIndex = 0; itemIndex < leaves.length; itemIndex++) {
    final leaf = leaves[itemIndex];
    final node = leaf.node;
    InstancedMeshResource? res;
    if (node.meshResourceId != null) {
      res = resourceById[node.meshResourceId];
      if (res == null) {
        // Real error case: a leaf declared a resource that never got
        // baked - skip it rather than silently tracking a nameless,
        // geometry-less item, same as every other language port.
        continue;
      }
    }

    localIds.add(itemIndex);
    categories.add(node.layer.isEmpty ? 'Layer0' : node.layer);
    names.add(node.name);
    final rawGuid = node.guid;
    final itemGuid = (rawGuid.isNotEmpty && !seenGuids.contains(rawGuid)) ? rawGuid : 'openskp-$itemIndex';
    seenGuids.add(itemGuid);
    guids.add(itemGuid);
    if (node.nameIsGenerated) generatedNameGuids.add(itemGuid);
    itemIndexByNode[node] = itemIndex;

    if (res != null) {
      final trs = decomposeTrs(leaf.world);
      for (var primIdx = 0; primIdx < res.primitives.length; primIdx++) {
        final prim = res.primitives[primIdx];
        final materialIndex = getMaterialIndex(prim.materialIndex);
        // Normally exactly one shell; more than one only when the
        // primitive's own triangle count exceeded what a single shell can
        // represent (see getOrBakeShell) - each extra shell becomes its
        // own additional Sample of the same item/material/transform, the
        // same pattern this loop already uses for multiple primitives of
        // one item.
        for (final shellIndex in getOrBakeShell(node.meshResourceId!, primIdx, prim, trs.scale, trs.mirrored)) {
          sampleMaterial.add(materialIndex);
          sampleRepresentation.add(shellIndex);
          meshesItems.add(itemIndex);
          globalTransformData.add(trs);
        }
      }
    }
  }

  final nSamples = sampleMaterial.length;

  final shellsVec = builder.writeList(shellOffsets);

  final materialBuilders = [
    for (final rgba in materialRgba)
      ffb.MaterialObjectBuilder(
        r: rgba.$1, g: rgba.$2, b: rgba.$3, a: rgba.$4,
        renderedFaces: ffb.RenderedFaces.ONE, stroke: ffb.Stroke.DEFAULT,
      ),
  ];
  final materialsVec = builder.writeListOfStructs(materialBuilders);

  final representationBuilders = [
    for (var i = 0; i < representationBoundsMin.length; i++)
      ffb.RepresentationObjectBuilder(
        id: i,
        bbox: ffb.BoundingBoxObjectBuilder(
          min: ffb.FloatVectorObjectBuilder(
              x: representationBoundsMin[i][0], y: representationBoundsMin[i][1], z: representationBoundsMin[i][2]),
          max: ffb.FloatVectorObjectBuilder(
              x: representationBoundsMax[i][0], y: representationBoundsMax[i][1], z: representationBoundsMax[i][2]),
        ),
        representationClass: ffb.RepresentationClass.SHELL,
      ),
  ];
  final representationsVec = builder.writeListOfStructs(representationBuilders);

  final sampleBuilders = [
    for (var i = 0; i < nSamples; i++)
      ffb.SampleObjectBuilder(item: i, material: sampleMaterial[i], representation: sampleRepresentation[i], localTransform: 0),
  ];
  final samplesVec = builder.writeListOfStructs(sampleBuilders);

  final meshesItemsVec = builder.writeListUint32(meshesItems);

  final globalTransformBuilders = [
    for (final t in globalTransformData)
      ffb.TransformObjectBuilder(
        position: ffb.DoubleVectorObjectBuilder(x: t.position[0], y: t.position[1], z: t.position[2]),
        xDirection: ffb.FloatVectorObjectBuilder(x: t.xDir[0], y: t.xDir[1], z: t.xDir[2]),
        yDirection: ffb.FloatVectorObjectBuilder(x: t.yDir[0], y: t.yDir[1], z: t.yDir[2]),
      ),
  ];
  final globalTransformsVec = builder.writeListOfStructs(globalTransformBuilders);

  // One shared identity local transform - no per-geometry sub-offset is
  // needed since every primitive's points are already in the resource's
  // own local space (now with scale/mirror already baked in).
  final localTransformsVec = builder.writeListOfStructs([
    ffb.TransformObjectBuilder(
      position: ffb.DoubleVectorObjectBuilder(x: 0, y: 0, z: 0),
      xDirection: ffb.FloatVectorObjectBuilder(x: 1, y: 0, z: 0),
      yDirection: ffb.FloatVectorObjectBuilder(x: 0, y: 1, z: 0),
    ),
  ]);

  final circleExtrusionsVec = builder.writeList(const []);

  // Model-level coordinates: an identity Transform, same shared-identity
  // reasoning as localTransforms above.
  final coordinates = ffb.TransformObjectBuilder(
    position: ffb.DoubleVectorObjectBuilder(x: 0, y: 0, z: 0),
    xDirection: ffb.FloatVectorObjectBuilder(x: 1, y: 0, z: 0),
    yDirection: ffb.FloatVectorObjectBuilder(x: 0, y: 1, z: 0),
  ).finish(builder);

  final meshesB = ffb.MeshesBuilder(builder);
  meshesB.begin();
  meshesB.addCoordinates(coordinates);
  meshesB.addMeshesItemsOffset(meshesItemsVec);
  meshesB.addSamplesOffset(samplesVec);
  meshesB.addRepresentationsOffset(representationsVec);
  meshesB.addMaterialsOffset(materialsVec);
  meshesB.addCircleExtrusionsOffset(circleExtrusionsVec);
  meshesB.addShellsOffset(shellsVec);
  meshesB.addLocalTransformsOffset(localTransformsVec);
  meshesB.addGlobalTransformsOffset(globalTransformsVec);
  final meshesOffset = meshesB.finish();

  final catOffsets = [for (final c in categories) builder.writeString(c)];
  final categoriesVec = builder.writeList(catOffsets);

  final localIdsVec = builder.writeListUint32(localIds);

  final guidStr = builder.writeString(options.modelId ?? '00000000-0000-0000-0000-000000000000');

  // Per-item GUIDs: guids[i] is that item's identifier, guidsItems[i] is
  // which local_id it belongs to - a real consumer's own id-bridge zips
  // Model.getGuids() against Model.getLocalIds() index-for-index, so both
  // vectors must be the same length, in the same order, one entry per
  // tracked item.
  final guidOffsets = [for (final g in guids) builder.writeString(g)];
  final guidsVec = builder.writeList(guidOffsets);
  final guidsItemsVec = builder.writeListUint32(localIds);

  // One Attribute per tracked item (same order as local_ids/categories),
  // carrying the item's real display name encoded as a
  // `["Name", value, "STRING"]` JSON triple - the exact convention the
  // real IfcImporter uses for its own "Name" attribute (matches Python/
  // C++/.NET/TypeScript exactly).
  final attributeOffsets = <int>[];
  for (final name in names) {
    final dataOffsets = <int>[];
    if (name.isNotEmpty) {
      dataOffsets.add(builder.writeString(_nameAttributeJson(name)));
    }
    final dataVec = builder.writeList(dataOffsets);
    final attrB = ffb.AttributeBuilder(builder);
    attrB.begin();
    attrB.addDataOffset(dataVec);
    attributeOffsets.add(attrB.finish());
  }
  final attributesVec = builder.writeList(attributeOffsets);

  // The source file's own per-layer visibility has no equivalent field
  // anywhere in the Fragments schema itself - metadata is the schema's
  // own general-purpose "JSON string for generic data about the file"
  // field, exactly the right place for a consuming viewer to recover
  // this. Matches Python's/C++'s/.NET's/TypeScript's own metadata shape.
  final metadataOffset = builder.writeString(jsonEncode({
    'layer_hidden': scene.layerHidden,
    'generated_name_guids': generatedNameGuids,
  }));

  // Spatial structure: one SpatialStructure node per InstancedNode in the
  // ORIGINAL tree (not just leaves), so real component nesting comes
  // through, not just a flat list. Matches Python's/C++'s/.NET's/
  // TypeScript's own build_spatial_node/buildSpatialNode exactly.
  int buildSpatialNode(InstancedNode node) {
    final childOffsets = [for (final child in node.children) buildSpatialNode(child)];
    final childrenVec = builder.writeList(childOffsets);

    int? categoryOffset;
    if (node.layer.isNotEmpty) categoryOffset = builder.writeString(node.layer);

    final localId = itemIndexByNode[node];

    final b = ffb.SpatialStructureBuilder(builder);
    b.begin();
    if (localId != null) b.addLocalId(localId);
    if (categoryOffset != null) b.addCategoryOffset(categoryOffset);
    b.addChildrenOffset(childrenVec);
    return b.finish();
  }

  final rootSpatial = buildSpatialNode(scene.sceneHierarchy);

  final modelB = ffb.ModelBuilder(builder);
  modelB.begin();
  modelB.addMetadataOffset(metadataOffset);
  modelB.addGuidsOffset(guidsVec);
  modelB.addGuidsItemsOffset(guidsItemsVec);
  modelB.addMaxLocalId(localIds.length);
  modelB.addLocalIdsOffset(localIdsVec);
  modelB.addCategoriesOffset(categoriesVec);
  modelB.addMeshesOffset(meshesOffset);
  modelB.addAttributesOffset(attributesVec);
  modelB.addGuidOffset(guidStr);
  modelB.addSpatialStructureOffset(rootSpatial);
  final modelOffset = modelB.finish();

  builder.finish(modelOffset, '0001');
  final rawBytes = builder.buffer;

  return options.raw ? rawBytes : Uint8List.fromList(const ZLibEncoder().encode(rawBytes));
}

/// Exports an instanced scene to a `.frag` file.
void exportFragments(InstancedScene scene, String outputPath, [FragmentExportOptions options = const FragmentExportOptions()]) {
  final file = File(outputPath);
  file.parent.createSync(recursive: true);
  file.writeAsBytesSync(toFragments(scene, options));
}
