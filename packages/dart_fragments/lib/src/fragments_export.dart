import 'dart:convert';
import 'dart:io';
import 'dart:math';
import 'dart:typed_data';

import 'package:archive/archive.dart';
import 'package:flat_buffers/flat_buffers.dart' as fb;
import 'package:openskp/openskp.dart';

import 'fragments_fb/fragments_generated.dart' as ffb;

/// Direct SKP <-> ThatOpen Fragments (.frag) conversion. A port of
/// Python's `openskp.export.fragments` (see that module's own docstring
/// for the full rationale and real-loader verification status this port
/// inherits) against the same vendored ThatOpen Fragments schema
/// (`fragments_fb/index.fbs`). Write side ([toFragments]/
/// [exportFragments]) matches TypeScript/.NET/C++/Python; read side
/// ([fromFragments]/[readFragments], openskp#285) brings Dart to parity
/// with Python/TypeScript/.NET - only C++ still lacks it as of this port
/// landing.
///
/// Built at full Python/C++/TypeScript/.NET GUID/name-is-generated/
/// layer-hidden fidelity from the start: InstancedNode/InstancedScene in
/// the core openskp package already carry real per-instance source
/// GUIDs, a generated-name flag, and the source file's layer-hidden
/// state (added alongside this port - see the core package's
/// Geometry.extractAttributeDictionaries).
///
/// Ships as its own package (rather than living in the core openskp
/// package) because it needs `package:flat_buffers` - the core package
/// has zero external dependencies today, and this mirrors Python's own
/// opt-in `pip install openskp[fragments]` extra instead of forcing every
/// openskp Dart consumer to take a transitive dependency they don't need.
///
/// See `lib/src/fragments_fb/index.fbs`'s own comment for why the Dart
/// bindings are generated from a locally-patched schema copy (flatc's
/// Dart backend doesn't support FlatBuffers' optional-scalar feature),
/// and [fromFragments]'s own note on the SpatialStructure.localId
/// consequence that patch has for reading (fixed via a hand-added
/// `localIdOrNull` getter in fragments_generated.dart).

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
  // Real, previously-undetected bug, found while adding the Dart Fragments
  // READER (openskp#285) - nothing before this read the materials vector
  // back, so it went unnoticed. package:flat_buffers's writeListOfStructs
  // writes struct elements FIRST, then endStructVector's putUint32(count)
  // LAST - and that final putUint32 call aligns its OWN 4-byte write by
  // inserting padding immediately before itself if needed, which (since
  // the elements are already fixed at their positions) lands the padding
  // BETWEEN the length prefix and element 0, not before the vector as a
  // whole. Every other struct type in this schema (FloatVector 12 bytes,
  // Transform 48, Representation 32, Sample 16) is already a multiple of
  // 4 regardless of count, so this never manifested until Material (6
  // bytes/element - NOT a multiple of 4): an ODD material count leaves 2
  // such bytes unaccounted for, which the standard `count + 4` read-side
  // offset (used by every language's generated reader, including this
  // one) doesn't know to skip. Confirmed directly: a 1-material scene
  // read back r=0/g=0/b=255/a=255/renderedFaces=-1 (a real crash) instead
  // of the correct r=255/g=255/b=255/a=255/renderedFaces=ONE; a
  // 2-material scene (12 bytes, already 4-aligned) read back correctly
  // with no fix at all. Pre-padding by exactly the shortfall before the
  // elements are written gives endStructVector's own alignment nothing
  // left to do, closing the gap. An EVEN material count needs no pad.
  if ((materialBuilders.length * 6) % 4 != 0) builder.pad(2);
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

// RepresentationClass values this module can read geometry for today -
// mirrors Python's own _SUPPORTED_REPRESENTATION_CLASSES and its note on
// why (only SHELL is written by any of this project's own exporters yet).
const Set<ffb.RepresentationClass> _supportedRepresentationClasses = {ffb.RepresentationClass.SHELL};

(double, double, double) _cross3((double, double, double) a, (double, double, double) b) => (
      a.$2 * b.$3 - a.$3 * b.$2,
      a.$3 * b.$1 - a.$1 * b.$3,
      a.$1 * b.$2 - a.$2 * b.$1,
    );

/// One normal per vertex, flat-shaded: each triangle's own face normal,
/// duplicated across its 3 vertices - the most a Shell (points + indices,
/// nothing else) can ever give back. Mirrors Python's `_compute_flat_normals`.
List<(double, double, double)> _computeFlatNormals(
    List<(double, double, double)> points, List<(int, int, int)> triangles) {
  final normals = List<(double, double, double)>.filled(points.length, (0.0, 0.0, 1.0));
  for (final (a, b, c) in triangles) {
    final pa = points[a], pb = points[b], pc = points[c];
    final u = (pb.$1 - pa.$1, pb.$2 - pa.$2, pb.$3 - pa.$3);
    final v = (pc.$1 - pa.$1, pc.$2 - pa.$2, pc.$3 - pa.$3);
    var n = _cross3(u, v);
    final length = sqrt(n.$1 * n.$1 + n.$2 * n.$2 + n.$3 * n.$3);
    if (length > 1e-12) n = (n.$1 / length, n.$2 / length, n.$3 / length);
    normals[a] = n;
    normals[b] = n;
    normals[c] = n;
  }
  return normals;
}

/// Reassemble a glTF-style column-major 4x4 matrix from a Fragments
/// `Transform` struct (position + x/y direction unit vectors). The struct
/// never stores a Z direction - reconstructed here as `xDir cross yDir`,
/// matching the same right-handed orthonormal frame export's TRS
/// decomposition produces. Mirrors Python's `_transform_to_matrix16`.
List<double> _transformToMatrix16(ffb.Transform transform) {
  final pos = transform.position;
  final xDirS = transform.xDirection;
  final yDirS = transform.yDirection;
  final xDir = (xDirS.x, xDirS.y, xDirS.z);
  final yDir = (yDirS.x, yDirS.y, yDirS.z);
  final zDir = _cross3(xDir, yDir);
  return [
    xDir.$1, xDir.$2, xDir.$3, 0.0,
    yDir.$1, yDir.$2, yDir.$3, 0.0,
    zDir.$1, zDir.$2, zDir.$3, 0.0,
    pos.x, pos.y, pos.z, 1.0,
  ];
}

/// Pull the `["Name", value, "STRING"]` entry out of an item's
/// `Attribute.data`, matching the exact convention [toFragments] (and the
/// real `IfcImporter`) writes. Mirrors Python's `_extract_name_attribute`.
String _extractNameAttribute(ffb.Attribute? attribute) {
  if (attribute == null) return '';
  for (final raw in attribute.data ?? const <String>[]) {
    try {
      final triple = jsonDecode(raw);
      if (triple is List && triple.length >= 2 && triple[0] == 'Name') {
        return triple[1]?.toString() ?? '';
      }
    } catch (_) {
      continue;
    }
  }
  return '';
}

/// Parses a real `.frag` file's bytes into an [InstancedScene] - the
/// mirror of [toFragments]. Accepts either the zlib-compressed wire
/// format (the default [toFragments]/real loader convention) or raw,
/// uncompressed FlatBuffers bytes; detected automatically the same way
/// the real `@thatopen/fragments` loader does, by attempting zlib
/// inflation first.
///
/// Known gaps, matching Python's own read side exactly (not
/// independently improved on here - see openskp#285): `positionMm`/
/// `properties`/`attributeDictionaries` on each node are left at their
/// defaults, since the Fragments format doesn't carry them separately
/// from the `Name` attribute this function does extract.
InstancedScene fromFragments(Uint8List data) {
  Uint8List rawBytes;
  try {
    rawBytes = const ZLibDecoder().decodeBytes(data);
  } catch (_) {
    rawBytes = data;
  }

  final model = ffb.Model(rawBytes);
  final meshes = model.meshes;

  // ---- Materials (flat RGBA - no texture concept exists in this format
  // at all). ----
  final gltfMaterials = <Map<String, dynamic>>[];
  if (meshes != null) {
    for (final mat in meshes.materials ?? const <ffb.Material>[]) {
      gltfMaterials.add({
        'pbrMetallicRoughness': {
          'baseColorFactor': [mat.r / 255.0, mat.g / 255.0, mat.b / 255.0, mat.a / 255.0],
          'metallicFactor': 0.0,
          'roughnessFactor': 1.0,
        },
        'doubleSided': mat.renderedFaces != ffb.RenderedFaces.ONE,
      });
    }
  }
  if (gltfMaterials.isEmpty) {
    gltfMaterials.add({
      'pbrMetallicRoughness': {
        'baseColorFactor': [0.8, 0.8, 0.8, 1.0],
        'metallicFactor': 0.0,
        'roughnessFactor': 1.0,
      },
    });
  }

  // ---- Shells -> (points, triangles), decoded once per shell index,
  // reused by every sample referencing it. ----
  final shellList = meshes?.shells ?? const <ffb.Shell>[];
  final shellGeometry = List<List<(double, double, double)>?>.filled(shellList.length, null);
  final shellTriangles = List<List<(int, int, int)>>.filled(shellList.length, const []);

  (List<(double, double, double)>, List<(int, int, int)>) decodeShell(int shellIdx) {
    final cached = shellGeometry[shellIdx];
    if (cached != null) return (cached, shellTriangles[shellIdx]);
    final shell = shellList[shellIdx];
    final points = (shell.points ?? const <ffb.FloatVector>[]).map((p) => (p.x.toDouble(), p.y.toDouble(), p.z.toDouble())).toList();

    final isBig = shell.type == ffb.ShellType.BIG;
    final triangles = <(int, int, int)>[];
    if (isBig) {
      for (final profile in shell.bigProfiles ?? const <ffb.BigShellProfile>[]) {
        final indices = profile.indices;
        if (indices != null && indices.length >= 3) triangles.add((indices[0], indices[1], indices[2]));
      }
    } else {
      for (final profile in shell.profiles ?? const <ffb.ShellProfile>[]) {
        final indices = profile.indices;
        if (indices != null && indices.length >= 3) triangles.add((indices[0], indices[1], indices[2]));
      }
    }

    shellGeometry[shellIdx] = points;
    shellTriangles[shellIdx] = triangles;
    return (points, triangles);
  }

  // ---- Group samples by item (Meshes.meshesItems[k] -> item index, NOT
  // Sample.item - see toFragments's own comment on why the two differ;
  // Sample.item is just the sample's own position, always). ----
  final sampleList = meshes?.samples ?? const <ffb.Sample>[];
  final meshesItems = meshes?.meshesItems ?? const <int>[];
  final samplesByItem = <int, List<int>>{};
  for (var k = 0; k < sampleList.length; k++) {
    final itemIdx = meshesItems[k];
    samplesByItem.putIfAbsent(itemIdx, () => []).add(k);
  }

  // ---- Per-item metadata: name, guid, category, world transform.
  // localIds[i] IS the item index space - see toFragments's own
  // localIds.add(itemIndex). ----
  final localIds = model.localIds ?? const <int>[];
  final localIdToItemIndex = <int, int>{for (var i = 0; i < localIds.length; i++) localIds[i]: i};

  final guidByItem = <int, String>{};
  final guidsItems = model.guidsItems ?? const <int>[];
  final guids = model.guids ?? const <String>[];
  for (var i = 0; i < guidsItems.length; i++) {
    final itemIdx = localIdToItemIndex[guidsItems[i]];
    if (itemIdx != null && i < guids.length) guidByItem[itemIdx] = guids[i];
  }

  var layerHidden = <String, bool>{};
  var generatedNameGuids = <String>{};
  final metadataRaw = model.metadata;
  if (metadataRaw != null && metadataRaw.isNotEmpty) {
    try {
      final metadata = jsonDecode(metadataRaw);
      if (metadata is Map) {
        final lh = metadata['layer_hidden'];
        if (lh is Map) {
          layerHidden = lh.map((k, v) => MapEntry(k.toString(), v == true));
        }
        final gn = metadata['generated_name_guids'];
        if (gn is List) {
          generatedNameGuids = gn.whereType<String>().toSet();
        }
      }
    } catch (_) {
      // leave layerHidden/generatedNameGuids empty
    }
  }

  final meshResources = <InstancedMeshResource>[];
  final resourceBySignature = <String, String>{};
  var warnedUnsupported = false;

  final representationList = meshes?.representations ?? const <ffb.Representation>[];
  final globalTransforms = meshes?.globalTransforms ?? const <ffb.Transform>[];

  String buildResourceForItem(int itemIdx, String itemName) {
    final sampleIndices = samplesByItem[itemIdx] ?? const <int>[];
    final signature = <(int, int)>[];
    final primitives = <LocalPrimitive>[];
    for (final k in sampleIndices) {
      final sample = sampleList[k];
      final repIdx = sample.representation;
      final matIdx = sample.material;
      final representation = repIdx < representationList.length ? representationList[repIdx] : null;
      final repClass = representation?.representationClass ?? ffb.RepresentationClass.SHELL;
      if (!_supportedRepresentationClasses.contains(repClass)) {
        if (!warnedUnsupported) {
          stderr.writeln(
              'openskp fromFragments: skipping a sample with unsupported RepresentationClass=$repClass (only SHELL is read today).');
          warnedUnsupported = true;
        }
        continue;
      }
      signature.add((repIdx, matIdx));
      // Representation.id is the index into Meshes.shells - NOT the
      // representation's own position in the representations list. See
      // Python's from_fragments's own comment on why this must follow
      // id, matching the real reader's own fetch-functions.ts.
      final shellIdx = representation!.id;
      if (shellIdx >= shellList.length) continue;
      final (points, triangles) = decodeShell(shellIdx);
      final normals = _computeFlatNormals(points, triangles);
      final positions = <double>[];
      final normalsFlat = <double>[];
      for (var i = 0; i < points.length; i++) {
        positions.addAll([points[i].$1, points[i].$2, points[i].$3]);
        normalsFlat.addAll([normals[i].$1, normals[i].$2, normals[i].$3]);
      }
      final uvs = List<double>.filled(points.length * 2, 0.0);
      final indicesFlat = <int>[];
      for (final tri in triangles) {
        indicesFlat.addAll([tri.$1, tri.$2, tri.$3]);
      }
      primitives.add(LocalPrimitive(
        positions: positions,
        normals: normalsFlat,
        uvs: uvs,
        indices: indicesFlat,
        materialIndex: matIdx < gltfMaterials.length ? matIdx : 0,
      ));
    }

    final sigKey = signature.map((s) => '${s.$1}:${s.$2}').join(',');
    if (sigKey.isNotEmpty && resourceBySignature.containsKey(sigKey)) {
      return resourceBySignature[sigKey]!;
    }

    final resourceId = 'frag-$itemIdx';
    meshResources.add(InstancedMeshResource(
      id: resourceId,
      definitionId: itemIdx,
      definitionName: itemName.isEmpty ? resourceId : itemName,
      variantKey: 'default',
      primitives: primitives,
    ));
    if (sigKey.isNotEmpty) resourceBySignature[sigKey] = resourceId;
    return resourceId;
  }

  final attributesList = model.attributes ?? const <ffb.Attribute>[];

  // ---- Spatial structure -> InstancedNode tree. ----
  InstancedNode buildNode(ffb.SpatialStructure spatial) {
    // See fragments_fb/index.fbs's own comment on why this reads
    // localIdOrNull (hand-added) rather than the generated localId -
    // absent and genuinely-0 are NOT the same thing here.
    final localId = spatial.localIdOrNull;
    final category = spatial.category ?? '';

    var name = '';
    var guid = '';
    String? meshResourceId;
    var matrix = _identityMatrix;
    var nameIsGenerated = false;

    if (localId != null) {
      final itemIdx = localIdToItemIndex[localId];
      if (itemIdx != null) {
        final attribute = itemIdx < attributesList.length ? attributesList[itemIdx] : null;
        name = _extractNameAttribute(attribute);
        guid = guidByItem[itemIdx] ?? '';
        nameIsGenerated = guid.isNotEmpty && generatedNameGuids.contains(guid);
        if (samplesByItem.containsKey(itemIdx)) {
          meshResourceId = buildResourceForItem(itemIdx, name.isEmpty ? category : name);
          final transform = globalTransforms[samplesByItem[itemIdx]![0]];
          matrix = _transformToMatrix16(transform);
        }
      }
    }

    final children = (spatial.children ?? const <ffb.SpatialStructure>[]).map(buildNode).toList();

    return InstancedNode(
      name: name,
      nameIsGenerated: nameIsGenerated,
      definitionName: category,
      layer: category,
      matrix: matrix,
      guid: guid,
      meshResourceId: meshResourceId,
      children: children,
    );
  }

  final rootSpatial = model.spatialStructure;
  final sceneHierarchy = rootSpatial != null ? buildNode(rootSpatial) : InstancedNode(name: 'ROOT', definitionName: 'ROOT');

  return InstancedScene(
    bounds: null,
    sceneHierarchy: sceneHierarchy,
    meshResources: meshResources,
    gltfMaterials: gltfMaterials,
    textures: [],
    layerHidden: layerHidden,
  );
}

/// Reads a `.frag` file from disk into an [InstancedScene]. See
/// [fromFragments] for the full contract and known gaps.
InstancedScene readFragments(String path) => fromFragments(File(path).readAsBytesSync());
