import 'dart:io';
import 'dart:math' as math;
import 'scene.dart';

// 1 metre = 39.37007874015748 inches (SketchUp native unit)
const double metresToInches = 39.37007874015748;

String sanitizeLayerName(String? name) {
  if (name == null || name.isEmpty) return '0';
  final illegal = RegExp(r'[\<\>\/\\\"~\:;\?\*\=`\|]');
  final clean = name.replaceAll(illegal, '_').trim();
  return clean.isEmpty ? '0' : clean;
}

int rgbToAci(int r, int g, int b) {
  const standardAci = [
    [255, 0, 0, 1],
    [255, 255, 0, 2],
    [0, 255, 0, 3],
    [0, 255, 255, 4],
    [0, 0, 255, 5],
    [255, 0, 255, 6],
    [255, 255, 255, 7],
    [128, 128, 128, 8],
    [192, 192, 192, 9],
  ];
  int bestAci = 7;
  double minDist = double.infinity;
  for (final entry in standardAci) {
    final sr = entry[0];
    final sg = entry[1];
    final sb = entry[2];
    final aci = entry[3];
    final dist = (r - sr) * (r - sr) + (g - sg) * (g - sg) + (b - sb) * (b - sb);
    if (dist < minDist) {
      minDist = dist.toDouble();
      bestAci = aci;
    }
  }
  return bestAci;
}

List<int> getPrimRgb(Scene scene, GlbPrimitive prim) {
  int r = 200;
  int g = 200;
  int b = 200;
  final matIdx = prim.materialIndex;
  if (matIdx < scene.gltfMaterials.length) {
    final mat = scene.gltfMaterials[matIdx];
    final pbr = mat['pbrMetallicRoughness'];
    if (pbr is Map && pbr['baseColorFactor'] is List) {
      final colorVec = pbr['baseColorFactor'] as List;
      if (colorVec.length >= 3) {
        r = ((colorVec[0] as num).toDouble() * 255.0).round().clamp(0, 255);
        g = ((colorVec[1] as num).toDouble() * 255.0).round().clamp(0, 255);
        b = ((colorVec[2] as num).toDouble() * 255.0).round().clamp(0, 255);
      }
    }
  }
  return [math.max(0, math.min(255, r)), math.max(0, math.min(255, g)), math.max(0, math.min(255, b))];
}

/**
 * Serialize a baked Scene into AutoCAD R2000 (AC1015) 3D ASCII DXF format.
 * Uses the AutoCAD 100% compliant template scaffold with Windows CRLF (\r\n) line endings.
 */
String toDxf(
  Scene scene, {
  double scale = metresToInches,
  String mode = 'polyface',
}) {
  final layerColors = <String, List<int>>{};
  for (final prim in scene.glbPrimitives) {
    final layerName = sanitizeLayerName(prim.geomName);
    if (!layerColors.containsKey(layerName)) {
      layerColors[layerName] = getPrimRgb(scene, prim);
    }
  }

  if (layerColors.isEmpty) {
    layerColors['0'] = [200, 200, 200];
  }

  final sortedLayers = layerColors.keys.toList()..sort();

  int handleId = 0x100;
  String nextHandle() {
    final h = handleId.toRadixString(16).toUpperCase();
    handleId++;
    return h;
  }

  final layerHandles = <String, String>{};
  for (final lName in sortedLayers) {
    layerHandles[lName] = nextHandle();
  }

  final lines = <String>[
    '  0', 'SECTION', '  2', 'HEADER',
    '  9', '\$ACADVER', '  1', 'AC1015',
    '  9', '\$ACADMAINTVER', ' 70', '6',
    '  9', '\$DWGCODEPAGE', '  3', 'ANSI_1252',
    '  9', '\$INSBASE', ' 10', '0.0', ' 20', '0.0', ' 30', '0.0',
    '  9', '\$EXTMIN', ' 10', '1e+20', ' 20', '1e+20', ' 30', '1e+20',
    '  9', '\$EXTMAX', ' 10', '-1e+20', ' 20', '-1e+20', ' 30', '-1e+20',
    '  9', '\$LIMMIN', ' 10', '0.0', ' 20', '0.0',
    '  9', '\$LIMMAX', ' 10', '420.0', ' 20', '297.0',
    '  9', '\$ORTHOMODE', ' 70', '0',
    '  9', '\$REGENMODE', ' 70', '1',
    '  9', '\$FILLMODE', ' 70', '1',
    '  9', '\$QTEXTMODE', ' 70', '0',
    '  9', '\$MIRRTEXT', ' 70', '1',
    '  9', '\$LTSCALE', ' 40', '1.0',
    '  9', '\$ATTMODE', ' 70', '1',
    '  9', '\$TEXTSIZE', ' 40', '2.5',
    '  9', '\$TRACEWID', ' 40', '1.0',
    '  9', '\$TEXTSTYLE', '  7', 'Standard',
    '  9', '\$CLAYER', '  8', '0',
    '  9', '\$CELTYPE', '  6', 'ByLayer',
    '  9', '\$CECOLOR', ' 62', '256',
    '  9', '\$CELTSCALE', ' 40', '1.0',
    '  9', '\$DISPSILH', ' 70', '0',
    '  9', '\$HANDSEED', '  5', '__HANDSEED__',
    '  9', '\$INSUNITS', ' 70', '1',
    '  0', 'ENDSEC',
    '  0', 'SECTION', '  2', 'CLASSES',
    '  0', 'CLASS', '  1', 'ACDBDICTIONARYWDFLT', '  2', 'AcDbDictionaryWithDefault', '  3', 'ObjectDBX Classes', ' 90', '0', ' 91', '0', '280', '0', '281', '0',
    '  0', 'ENDSEC',
    '  0', 'SECTION', '  2', 'TABLES',
    '  0', 'TABLE', '  2', 'VPORT', '  5', '1F', '100', 'AcDbSymbolTable', ' 70', '0', '  0', 'ENDTAB',
    '  0', 'TABLE', '  2', 'LTYPE', '  5', '20', '100', 'AcDbSymbolTable', ' 70', '1',
    '  0', 'LTYPE', '  5', '21', '100', 'AcDbSymbolTableRecord', '100', 'AcDbLinetypeTableRecord', '  2', 'BYBLOCK', ' 70', '0', '  3', '', ' 72', '65', ' 73', '0', ' 40', '0.0',
    '  0', 'LTYPE', '  5', '22', '100', 'AcDbSymbolTableRecord', '100', 'AcDbLinetypeTableRecord', '  2', 'BYLAYER', ' 70', '0', '  3', '', ' 72', '65', ' 73', '0', ' 40', '0.0',
    '  0', 'LTYPE', '  5', '23', '100', 'AcDbSymbolTableRecord', '100', 'AcDbLinetypeTableRecord', '  2', 'CONTINUOUS', ' 70', '0', '  3', 'Solid line', ' 72', '65', ' 73', '0', ' 40', '0.0',
    '  0', 'ENDTAB',
    '  0', 'TABLE', '  2', 'LAYER', '  5', '4', '100', 'AcDbSymbolTable', ' 70', (sortedLayers.length + 1).toString(),
    '  0', 'LAYER', '  5', '27', '330', '4', '100', 'AcDbSymbolTableRecord', '100', 'AcDbLayerTableRecord', '  2', '0', ' 70', '0', ' 62', '7', '  6', 'Continuous',
    '  0', 'LAYER', '  5', '28', '330', '4', '100', 'AcDbSymbolTableRecord', '100', 'AcDbLayerTableRecord', '  2', 'Defpoints', ' 70', '0', ' 62', '7', '  6', 'Continuous'
  ];

  for (final lName in sortedLayers) {
    final rgb = layerColors[lName]!;
    final lr = rgb[0];
    final lg = rgb[1];
    final lb = rgb[2];
    final aci = rgbToAci(lr, lg, lb);
    final trueColor = (lr << 16) | (lg << 8) | lb;
    lines.addAll([
      '  0', 'LAYER', '  5', layerHandles[lName]!, '330', '4', '100', 'AcDbSymbolTableRecord', '100', 'AcDbLayerTableRecord',
      '  2', lName, ' 70', '0', ' 62', aci.toString(), '420', trueColor.toString(), '  6', 'Continuous'
    ]);
  }

  lines.addAll([
    '  0', 'ENDTAB',
    '  0', 'TABLE', '  2', 'STYLE', '  5', '25', '100', 'AcDbSymbolTable', ' 70', '0', '  0', 'ENDTAB',
    '  0', 'TABLE', '  2', 'VIEW', '  5', '26', '100', 'AcDbSymbolTable', ' 70', '0', '  0', 'ENDTAB',
    '  0', 'TABLE', '  2', 'UCS', '  5', '27', '100', 'AcDbSymbolTable', ' 70', '0', '  0', 'ENDTAB',
    '  0', 'TABLE', '  2', 'APPID', '  5', '28', '100', 'AcDbSymbolTable', ' 70', '1',
    '  0', 'APPID', '  5', '29', '100', 'AcDbSymbolTableRecord', '100', 'AcDbRegAppTableRecord', '  2', 'ACAD', ' 70', '0',
    '  0', 'ENDTAB',
    '  0', 'TABLE', '  2', 'DIMSTYLE', '  5', '2A', '100', 'AcDbSymbolTable', ' 70', '0', '  0', 'ENDTAB',
    '  0', 'TABLE', '  2', 'BLOCK_RECORD', '  5', '2B', '100', 'AcDbSymbolTable', ' 70', '2',
    '  0', 'BLOCK_RECORD', '  5', '17', '330', '2B', '100', 'AcDbSymbolTableRecord', '100', 'AcDbBlockTableRecord', '  2', '*Model_Space',
    '  0', 'BLOCK_RECORD', '  5', '1B', '330', '2B', '100', 'AcDbSymbolTableRecord', '100', 'AcDbBlockTableRecord', '  2', '*Paper_Space',
    '  0', 'ENDTAB', '  0', 'ENDSEC',
    '  0', 'SECTION', '  2', 'BLOCKS',
    '  0', 'BLOCK', '  5', '18', '330', '17', '100', 'AcDbEntity', '  8', '0', '100', 'AcDbBlockBegin', '  2', '*Model_Space', ' 70', '0', ' 10', '0.0', ' 20', '0.0', ' 30', '0.0', '  3', '*Model_Space', '  1', '',
    '  0', 'ENDBLK', '  5', '19', '330', '17', '100', 'AcDbEntity', '  8', '0', '100', 'AcDbBlockEnd',
    '  0', 'BLOCK', '  5', '1C', '330', '1B', '100', 'AcDbEntity', '  8', '0', '100', 'AcDbBlockBegin', '  2', '*Paper_Space', ' 70', '0', ' 10', '0.0', ' 20', '0.0', ' 30', '0.0', '  3', '*Paper_Space', '  1', '',
    '  0', 'ENDBLK', '  5', '1D', '330', '1B', '100', 'AcDbEntity', '  8', '0', '100', 'AcDbBlockEnd',
    '  0', 'ENDSEC',
    '  0', 'SECTION', '  2', 'ENTITIES'
  ]);

  for (final prim in scene.glbPrimitives) {
    final layerName = sanitizeLayerName(prim.geomName);
    final triCount = prim.indices.length ~/ 3;
    if (triCount == 0) continue;

    final rgb = getPrimRgb(scene, prim);
    final aci = rgbToAci(rgb[0], rgb[1], rgb[2]);

    if (mode == 'polyface') {
      final vCount = prim.positions.length ~/ 3;
      lines.addAll([
        '  0', 'POLYLINE', '  5', nextHandle(), '330', '17', '100', 'AcDbEntity', '  8', layerName,
        ' 62', aci.toString(), '100', 'AcDbPolyFaceMesh', ' 66', '1',
        ' 10', '0.0', ' 20', '0.0', ' 30', '0.0',
        ' 70', '64', ' 71', vCount.toString(), ' 72', triCount.toString()
      ]);
      for (int i = 0; i < vCount; i++) {
        final vx = (prim.positions[i * 3] * scale).toStringAsFixed(6);
        final vy = (prim.positions[i * 3 + 1] * scale).toStringAsFixed(6);
        final vz = (prim.positions[i * 3 + 2] * scale).toStringAsFixed(6);
        lines.addAll([
          '  0', 'VERTEX', '  5', nextHandle(), '330', '17', '100', 'AcDbEntity', '  8', layerName,
          '100', 'AcDbVertex', '100', 'AcDbPolyFaceMeshVertex',
          ' 10', vx, ' 20', vy, ' 30', vz, ' 70', '192'
        ]);
      }
      for (int i = 0; i < triCount; i++) {
        final idx0 = prim.indices[i * 3] + 1;
        final idx1 = prim.indices[i * 3 + 1] + 1;
        final idx2 = prim.indices[i * 3 + 2] + 1;
        lines.addAll([
          '  0', 'VERTEX', '  5', nextHandle(), '330', '17', '100', 'AcDbEntity', '  8', layerName,
          '100', 'AcDbVertex', '100', 'AcDbFaceRecord', ' 70', '128',
          ' 71', idx0.toString(), ' 72', idx1.toString(), ' 73', idx2.toString(), ' 74', '0'
        ]);
      }
      lines.addAll(['  0', 'SEQEND', '  5', nextHandle(), '330', '17', '100', 'AcDbEntity', '  8', layerName]);
    } else {
      for (int i = 0; i < triCount; i++) {
        final i0 = prim.indices[i * 3];
        final i1 = prim.indices[i * 3 + 1];
        final i2 = prim.indices[i * 3 + 2];

        final v0x = (prim.positions[i0 * 3] * scale).toStringAsFixed(6);
        final v0y = (prim.positions[i0 * 3 + 1] * scale).toStringAsFixed(6);
        final v0z = (prim.positions[i0 * 3 + 2] * scale).toStringAsFixed(6);

        final v1x = (prim.positions[i1 * 3] * scale).toStringAsFixed(6);
        final v1y = (prim.positions[i1 * 3 + 1] * scale).toStringAsFixed(6);
        final v1z = (prim.positions[i1 * 3 + 2] * scale).toStringAsFixed(6);

        final v2x = (prim.positions[i2 * 3] * scale).toStringAsFixed(6);
        final v2y = (prim.positions[i2 * 3 + 1] * scale).toStringAsFixed(6);
        final v2z = (prim.positions[i2 * 3 + 2] * scale).toStringAsFixed(6);

        lines.addAll([
          '  0', '3DFACE', '  5', nextHandle(), '330', '17', '100', 'AcDbEntity', '  8', layerName,
          ' 62', aci.toString(), '100', 'AcDbFace',
          ' 10', v0x, ' 20', v0y, ' 30', v0z,
          ' 11', v1x, ' 21', v1y, ' 31', v1z,
          ' 12', v2x, ' 22', v2y, ' 32', v2z,
          ' 13', v2x, ' 23', v2y, ' 33', v2z
        ]);
      }
    }
  }

  lines.addAll([
    '  0', 'ENDSEC',
    '  0', 'SECTION', '  2', 'OBJECTS',
    '  0', 'DICTIONARY', '  5', 'A', '330', '0', '100', 'AcDbDictionary', '281', '1',
    '  3', 'ACAD_COLOR', '350', 'B',
    '  3', 'ACAD_GROUP', '350', 'C',
    '  3', 'ACAD_LAYOUT', '350', 'D',
    '  3', 'ACAD_MATERIAL', '350', 'E',
    '  3', 'ACAD_MLEADERSTYLE', '350', 'F',
    '  3', 'ACAD_MLINESTYLE', '350', '10',
    '  3', 'ACAD_PLOTSETTINGS', '350', '11',
    '  3', 'ACAD_PLOTSTYLENAME', '350', '12',
    '  3', 'ACAD_SCALELIST', '350', '14',
    '  3', 'ACAD_TABLESTYLE', '350', '15',
    '  3', 'ACAD_VISUALSTYLE', '350', '16',
    '  0', 'DICTIONARY', '  5', 'B', '330', 'A', '100', 'AcDbDictionary', '281', '1',
    '  0', 'DICTIONARY', '  5', 'C', '330', 'A', '100', 'AcDbDictionary', '281', '1',
    '  0', 'DICTIONARY', '  5', 'D', '330', 'A', '100', 'AcDbDictionary', '281', '1', '  3', 'Model', '350', '1A', '  3', 'Layout1', '350', '1E',
    '  0', 'DICTIONARY', '  5', 'E', '330', 'A', '100', 'AcDbDictionary', '281', '1',
    '  0', 'DICTIONARY', '  5', 'F', '330', 'A', '100', 'AcDbDictionary', '281', '1',
    '  0', 'DICTIONARY', '  5', '10', '330', 'A', '100', 'AcDbDictionary', '281', '1',
    '  0', 'DICTIONARY', '  5', '11', '330', 'A', '100', 'AcDbDictionary', '281', '1',
    '  0', 'ACDBDICTIONARYWDFLT', '  5', '12', '330', 'A', '100', 'AcDbDictionary', '281', '1', '  3', 'Normal', '350', '13', '100', 'AcDbDictionaryWithDefault', '340', '13',
    '  0', 'ACDBPLACEHOLDER', '  5', '13', '330', '12',
    '  0', 'DICTIONARY', '  5', '14', '330', 'A', '100', 'AcDbDictionary', '281', '1',
    '  0', 'DICTIONARY', '  5', '15', '330', 'A', '100', 'AcDbDictionary', '281', '1',
    '  0', 'DICTIONARY', '  5', '16', '330', 'A', '100', 'AcDbDictionary', '281', '1',
    '  0', 'LAYOUT', '  5', '1A', '330', 'D', '100', 'AcDbPlotSettings', '  1', '', '  4', 'A3', '  6', '', ' 40', '7.5', ' 41', '20.0', ' 42', '7.5', ' 43', '20.0', ' 44', '420.0', ' 45', '297.0', ' 46', '0.0', ' 47', '0.0', ' 48', '0.0', ' 49', '0.0', '140', '0.0', '141', '0.0', '142', '1.0', '143', '1.0', ' 70', '1024', ' 72', '1', ' 73', '0', ' 74', '5', '  7', '', ' 75', '16', ' 76', '0', ' 77', '2', ' 78', '300', '147', '1.0', '148', '0.0', '149', '0.0', '100', 'AcDbLayout', '  1', 'Model', ' 70', '1', ' 71', '0', ' 10', '0.0', ' 20', '0.0', ' 11', '420.0', ' 21', '297.0', ' 12', '0.0', ' 22', '0.0', ' 32', '0.0', ' 14', '1e+20', ' 24', '1e+20', ' 34', '1e+20', ' 15', '-1e+20', ' 25', '-1e+20', ' 35', '-1e+20', '146', '0.0', ' 13', '0.0', ' 23', '0.0', ' 33', '0.0', ' 16', '1.0', ' 26', '0.0', ' 36', '0.0', ' 17', '0.0', ' 27', '1.0', ' 76', '1', '330', '17',
    '  0', 'LAYOUT', '  5', '1E', '330', 'D', '100', 'AcDbPlotSettings', '  1', '', '  4', 'A3', '  6', '', ' 40', '7.5', ' 41', '20.0', ' 42', '7.5', ' 43', '20.0', ' 44', '420.0', ' 45', '297.0', ' 46', '0.0', ' 47', '0.0', ' 48', '0.0', ' 49', '0.0', '140', '0.0', '141', '0.0', '142', '1.0', '143', '1.0', ' 70', '0', ' 72', '1', ' 73', '0', ' 74', '5', '  7', '', ' 75', '16', ' 76', '0', ' 77', '2', ' 78', '300', '147', '1.0', '148', '0.0', '149', '0.0', '100', 'AcDbLayout', '  1', 'Layout1', ' 70', '1', ' 71', '1', ' 10', '0.0', ' 20', '0.0', ' 11', '420.0', ' 21', '297.0', ' 12', '0.0', ' 22', '0.0', ' 32', '0.0', ' 14', '1e+20', ' 24', '1e+20', ' 34', '1e+20', ' 15', '-1e+20', ' 25', '-1e+20', ' 35', '-1e+20', '146', '0.0', ' 13', '0.0', ' 23', '0.0', ' 33', '0.0', ' 16', '1.0', ' 26', '0.0', ' 36', '0.0', ' 17', '0.0', ' 27', '1.0', ' 76', '1', '330', '1B',
    '  0', 'ENDSEC',
    '  0', 'EOF'
  ]);

  // Enforce Windows CRLF (\r\n) line endings!
  final text = lines.join('\r\n') + '\r\n';
  return text.replaceAll('__HANDSEED__', (handleId + 0x10).toRadixString(16).toUpperCase());
}

void exportDxf(
  Scene scene,
  String outputPath, {
  double scale = metresToInches,
  String mode = 'polyface',
}) {
  final file = File(outputPath);
  file.parent.createSync(recursive: true);
  final text = toDxf(scene, scale: scale, mode: mode);
  file.writeAsStringSync(text);
}
