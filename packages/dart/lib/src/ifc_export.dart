import 'dart:io';
import 'dart:math' as math;
import 'scene.dart';

const double metresToInches = 39.37007874015748;
const _ifcBase64 =
    '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_\$';

String generateIfcGuid() {
  final rand = math.Random();
  final buffer = StringBuffer();
  for (int i = 0; i < 22; i++) {
    buffer.write(_ifcBase64[rand.nextInt(64)]);
  }
  return buffer.toString();
}

String sanitizeName(String? name) {
  if (name == null || name.isEmpty) return 'Unnamed';
  final clean = name.replaceAll("'", "''").replaceAll('\\', '\\\\').trim();
  return clean.isEmpty ? 'Unnamed' : clean;
}

List<String>? _classifyByKeyword(String name) {
  final l = name.toLowerCase();
  if (l.contains('wall')) return ['IFCWALL', 'IfcWall'];
  if (l.contains('door')) return ['IFCDOOR', 'IfcDoor'];
  if (l.contains('window')) return ['IFCWINDOW', 'IfcWindow'];
  if (l.contains('slab') || l.contains('floor')) return ['IFCSLAB', 'IfcSlab'];
  if (l.contains('column') || l.contains('pillar'))
    return ['IFCCOLUMN', 'IfcColumn'];
  if (l.contains('beam') || l.contains('joist')) return ['IFCBEAM', 'IfcBeam'];
  if (l.contains('roof')) return ['IFCROOF', 'IfcRoof'];
  return null;
}

/// Maps a geometry/component name to an IFC4 entity type and constructor.
///
/// Tries [geomName] first, then falls back to [layerName] (many
/// SketchUp-for-BIM workflows organize by tag/layer - "Walls", "Doors" -
/// even when individual components are never renamed away from
/// SketchUp's own defaults like "Component#109415"), then falls back to
/// a generic, untyped element if neither matches.
List<String> classifyElement(String geomName, [String layerName = '']) {
  final byName = _classifyByKeyword(geomName);
  if (byName != null) return byName;
  if (layerName.isNotEmpty) {
    final byLayer = _classifyByKeyword(layerName);
    if (byLayer != null) return byLayer;
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

/**
 * Serialize a baked Scene into ISO-10303-21 STEP ASCII IFC4 format.
 */
String toIfc(
  Scene scene, {
  double scale = metresToInches,
  String schema = 'IFC4',
  List<String> Function(String geomName, String layerName)? classifier,
}) {
  final classify = classifier ?? classifyElement;
  final schemaStr = schema.toUpperCase();
  final nowIso = DateTime.now().toUtc().toIso8601String().split('.').first;
  final timestampEpoch = DateTime.now().toUtc().millisecondsSinceEpoch ~/ 1000;

  final lines = <String>[
    'ISO-10303-21;',
    'HEADER;',
    "FILE_DESCRIPTION(('ViewDefinition [CoordinationView]'),'2;1');",
    "FILE_NAME('model.ifc','$nowIso',('OpenSKP Author'),('OpenSKP Organization'),'OpenSKP IFC Exporter','OpenSKP','');",
    "FILE_SCHEMA(('$schemaStr'));",
    'ENDSEC;',
    'DATA;'
  ];

  int entityId = 1;
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
      '#$ownerHistId=IFCOWNERHISTORY(#$personOrgId,#$appId,\$,.READWRITE.,\$,\$,\$,$timestampEpoch);');

  final lengthUnitId = nextId();
  lines.add('#$lengthUnitId=IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.);');

  final angleUnitId = nextId();
  lines.add('#$angleUnitId=IFCSIUNIT(*,.PLANEANGLEUNIT.,\$,.RADIAN.);');

  final solidUnitId = nextId();
  lines.add('#$solidUnitId=IFCSIUNIT(*,.STERADIANUNIT.,\$,.STERADIAN.);');

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
  final layerItems = <String, List<int>>{};
  final matStyleCache = <String, int>{};

  for (final prim in scene.glbPrimitives) {
    final triCount = prim.indices.length ~/ 3;
    final vCount = prim.positions.length ~/ 3;
    if (triCount == 0 || vCount == 0) continue;

    final geomName = sanitizeName(prim.geomName);
    final meta = scene.meshIndex[prim.geomName];
    final layerName = sanitizeName(meta?.layer ?? 'Layer0');
    final classification = classify(geomName, layerName);
    final stepType = classification[0];

    final ptCoords = <String>[];
    for (int i = 0; i < vCount; i++) {
      final vx = (prim.positions[i * 3] * scale).toStringAsFixed(6);
      final vy = (prim.positions[i * 3 + 1] * scale).toStringAsFixed(6);
      final vz = (prim.positions[i * 3 + 2] * scale).toStringAsFixed(6);
      ptCoords.add('($vx,$vy,$vz)');
    }

    final ptListId = nextId();
    lines.add('#$ptListId=IFCCARTESIANPOINTLIST3D((${ptCoords.join(',')}));');

    final faceIndices = <String>[];
    for (int i = 0; i < triCount; i++) {
      final idx0 = prim.indices[i * 3] + 1;
      final idx1 = prim.indices[i * 3 + 1] + 1;
      final idx2 = prim.indices[i * 3 + 2] + 1;
      faceIndices.add('($idx0,$idx1,$idx2)');
    }

    final faceSetId = nextId();
    lines.add(
        '#$faceSetId=IFCTRIANGULATEDFACESET(#$ptListId,\$,.TRUE.,(${faceIndices.join(',')}),\$);');

    layerItems.putIfAbsent(layerName, () => []).add(faceSetId);

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
          '#$styleId=IFCSURFACESTYLE(\'${geomName}_Material\',.BOTH.,(#$renderingId));');

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
        '#$shapeRepId=IFCSHAPEREPRESENTATION(#$geomCtxId,\'Body\',\'Tessellation\',(#$faceSetId));');

    final prodShapeId = nextId();
    lines.add('#$prodShapeId=IFCPRODUCTDEFINITIONSHAPE(\$,\$,(#$shapeRepId));');

    final prodPlacementId = nextId();
    lines.add(
        '#$prodPlacementId=IFCLOCALPLACEMENT(#$storeyPlacementId,#$axisPlacementId);');

    final productId = nextId();
    final prodGuid = generateIfcGuid();
    if (stepType == 'IFCBUILDINGELEMENTPROXY') {
      lines.add(
          '#$productId=$stepType(\'$prodGuid\',#$ownerHistId,\'$geomName\',\$,\$,#$prodPlacementId,#$prodShapeId,\$,.NOTDEFINED.);');
    } else {
      lines.add(
          '#$productId=$stepType(\'$prodGuid\',#$ownerHistId,\'$geomName\',\$,\$,#$prodPlacementId,#$prodShapeId,\$,\$);');
    }
    productIds.add(productId);

    if (meta != null && meta.properties.isNotEmpty) {
      final propValIds = <int>[];
      for (final entry in meta.properties.entries) {
        final cleanK = sanitizeName(entry.key);
        final cleanV = sanitizeName(entry.value);
        final propId = nextId();
        lines.add(
            '#$propId=IFCPROPERTYSINGLEVALUE(\'$cleanK\',\$,IFCTEXT(\'$cleanV\'),\$);');
        propValIds.add(propId);
      }

      if (propValIds.isNotEmpty) {
        final psetId = nextId();
        final propRefs = propValIds.map((pid) => '#$pid').join(',');
        lines.add(
            '#$psetId=IFCPROPERTYSET(\'${generateIfcGuid()}\',#$ownerHistId,\'Pset_CustomProperties\',\$,($propRefs));');

        lines.add(
            '#${nextId()}=IFCRELDEFINESBYPROPERTIES(\'${generateIfcGuid()}\',#$ownerHistId,\$,\$,(#$productId),#$psetId);');
      }
    }
  }

  final sortedLayerNames = layerItems.keys.toList()..sort();
  for (final lName in sortedLayerNames) {
    final itemIds = layerItems[lName]!;
    if (itemIds.isNotEmpty) {
      final itemRefs = itemIds.map((iid) => '#$iid').join(',');
      lines.add(
          '#${nextId()}=IFCPRESENTATIONLAYERASSIGNMENT(\'$lName\',\$,($itemRefs),\$);');
    }
  }

  if (productIds.isNotEmpty) {
    final prodRefs = productIds.map((pid) => '#$pid').join(',');
    lines.add(
        '#${nextId()}=IFCRELCONTAINEDINSPATIALSTRUCTURE(\'${generateIfcGuid()}\',#$ownerHistId,\$,\$,($prodRefs),#$storeyId);');
  }

  lines.add('ENDSEC;');
  lines.add('END-ISO-10303-21;');
  return lines.join('\r\n') + '\r\n';
}

void exportIfc(
  Scene scene,
  String outputPath, {
  double scale = metresToInches,
  String schema = 'IFC4',
  List<String> Function(String geomName, String layerName)? classifier,
}) {
  final file = File(outputPath);
  file.parent.createSync(recursive: true);
  final text =
      toIfc(scene, scale: scale, schema: schema, classifier: classifier);
  file.writeAsStringSync(text);
}
