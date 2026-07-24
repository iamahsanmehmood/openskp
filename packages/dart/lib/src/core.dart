import 'dart:typed_data';

import 'geometry.dart';
import 'legacy.dart';
import 'tlv.dart';
import 'vff.dart';

/// Raw parse result shared by both container eras (VFF and legacy MFC),
/// mirroring Python's _core.full_parse() / legacy.full_parse_legacy() dict
/// shape. Parser.dart converts this into the public SkpModel.
class RawParsed {
  String version = 'unknown';
  final Map<String, (int, int, int)> layerColors = {};
  final Map<int, String> layerIdToName = {};
  final Map<int, String> materialIdToName = {};
  final Map<String, RawMaterial> materials = {};
  final Map<String, RawMaterial> materialsByFolder = {};
  final List<RawStyle> styles = [];
  final Map<int, RawDefinition> defsDict = {};
  RawDefinition root = RawDefinition(guid: 'ROOT', name: 'ROOT_MODEL');
}

/// Orchestrates the full parsing pipeline for both container eras,
/// producing a shape-identical RawParsed regardless of which path ran.
/// Mirrors Python's _core.full_parse() / legacy.full_parse_legacy().
class Core {
  static RawParsed fullParse(Uint8List data) {
    final headerLen = data.length < 512 ? data.length : 512;
    final header = Uint8List.sublistView(data, 0, headerLen);

    if (!Vff.hasValidHeader(header)) {
      throw ArgumentError('Not a valid SketchUp file');
    }

    if (Legacy.isLegacy(data)) {
      return Legacy.fullParseLegacy(data);
    }

    final version = Vff.extractVersion(header);

    final pkPos = Vff.findZipOffset(data);
    if (pkPos < 0) {
      throw ArgumentError('No ZIP container found');
    }

    final zip = Vff.openZip(data, pkPos);

    final layerColors = <String, (int, int, int)>{};
    final materials = <String, RawMaterial>{};
    final materialsByFolder = <String, RawMaterial>{};

    for (final entry in zip.files) {
      final name = entry.name;
      if (name.endsWith('material.xml') && name.startsWith('materials/')) {
        RawMaterial? mat;
        try {
          mat = Geometry.parseMaterialXml(zip, name, entry.content);
        } catch (_) {
          mat = null;
        }
        if (mat != null) {
          final parts = name.split('/');
          final folderName = parts.length > 1 ? parts[1] : '';
          materials[mat.name] = mat;
          if (folderName.isNotEmpty) {
            materialsByFolder[folderName] = mat;
          }
          if (mat.name.startsWith('Layer_')) {
            layerColors[mat.name.substring(6)] = (mat.r, mat.g, mat.b);
          }
        }
      }
    }

    final styles = <RawStyle>[];
    for (final entry in zip.files) {
      final name = entry.name;
      if (!(name.startsWith('styles/') && name.endsWith('style.xml'))) {
        continue;
      }
      final style = Geometry.parseStyleXml(entry.content);
      if (style != null) {
        styles.add(style);
      }
    }

    final modelDatEntry = zip.findFile('model.dat');
    if (modelDatEntry == null) {
      throw ArgumentError('model.dat not found in ZIP container');
    }
    final modelDat = modelDatEntry.content;

    // Walk the TLV tree one top-level record at a time (instead of building
    // the whole file's tree at once) so peak memory is bounded by the
    // single largest definition/layer-manager/material-manager/root block,
    // not by the file's total node count. Real production files can have
    // 100k+ separate component definitions; materializing all of them
    // simultaneously is what actually exhausts memory on large files - not
    // the (comparatively modest, ~1x) cost of decompressing model.dat
    // itself.
    final layerIdToName = <int, String>{};
    final materialIdToName = <int, String>{};
    final defsDictRaw = <int, RawDefinition>{};
    final rootBuilder = GeometryBuilder();

    for (final el in Tlv.iterTopLevelLazy(modelDat, 0, modelDat.length, Tlv.containerTags)) {
      Geometry.collectLayers([el], layerIdToName);
      Geometry.collectMaterialIds([el], materialIdToName);
      Geometry.collectDefs([el], defsDictRaw);
      if (el.tag == 'F601') {
        Geometry.extractGeometryFromNodes(el.children, rootBuilder);
      }
      // `el` (and its whole subtree) is now unreferenced and eligible for
      // garbage collection before the next top-level record is built.
    }

    if (!layerIdToName.containsKey(1)) {
      layerIdToName[1] = 'Layer0';
    }
    if (!layerColors.containsKey('Layer0')) {
      layerColors['Layer0'] = (136, 136, 136);
    }

    return RawParsed()
      ..version = version
      ..layerColors.addAll(layerColors)
      ..layerIdToName.addAll(layerIdToName)
      ..materialIdToName.addAll(materialIdToName)
      ..materials.addAll(materials)
      ..materialsByFolder.addAll(materialsByFolder)
      ..styles.addAll(styles)
      ..defsDict.addAll(defsDictRaw)
      ..root =
          RawDefinition(guid: 'ROOT', name: 'ROOT_MODEL', builder: rootBuilder);
  }
}
