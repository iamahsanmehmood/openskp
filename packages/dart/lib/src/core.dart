import 'dart:typed_data';

import 'errors.dart';
import 'geometry.dart';
import 'legacy.dart';
import 'observability.dart';
import 'pages_dimensions.dart';
import 'tlv.dart';
import 'vff.dart';

/// Raw parse result shared by both container eras (VFF and legacy MFC),
/// mirroring Python's _core.full_parse() / legacy.full_parse_legacy() dict
/// shape. Parser.dart converts this into the public SkpModel.
class RawParsed {
  String version = 'unknown';

  /// The model's unit-system string (e.g. "Millimeter"), read from
  /// meta/meta.dat. Null for legacy files or when the tag isn't found.
  String? units;
  final Map<String, (int, int, int)> layerColors = {};
  // Modern (VFF) files derive layers from Layer_<name>-prefixed materials,
  // which carry no visibility flag of their own - unlike legacy MFC files,
  // there is currently no known tag exposing a VFF layer's hidden state,
  // so every VFF layer defaults to visible.
  final Map<String, bool> layerHidden = {};
  final Map<int, String> layerIdToName = {};
  final List<RawPage> pages = [];
  final List<RawDimension> dimensions = [];
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
  static RawParsed fullParse(Uint8List data, [ParseOptions? options]) {
    final sw = Stopwatch()..start();
    emitLog(options, SkpLogLevel.info, 'Parsing buffer (${data.length} bytes)');

    final headerLen = data.length < 512 ? data.length : 512;
    final header = Uint8List.sublistView(data, 0, headerLen);

    if (!Vff.hasValidHeader(header)) {
      throw SkpParseException('Not a valid SketchUp file (bad header magic)',
          stage: 'header');
    }

    if (Legacy.isLegacy(data)) {
      emitLog(options, SkpLogLevel.debug,
          'Detected legacy MFC container; routing to legacy walker');
      return Legacy.fullParseLegacy(data, options);
    }

    final version = Vff.extractVersion(header);
    emitLog(options, SkpLogLevel.debug,
        'Detected version $version (VFF/ZIP container)');

    final pkPos = Vff.findZipOffset(data);
    if (pkPos < 0) {
      throw SkpParseException('No ZIP container found', stage: 'zip_extract');
    }

    final zip = Vff.openZip(data, pkPos);

    final layerColors = <String, (int, int, int)>{};
    final layerHidden = <String, bool>{};
    final materials = <String, RawMaterial>{};
    final materialsByFolder = <String, RawMaterial>{};

    for (final entry in zip.files) {
      final name = entry.name;
      if (name.endsWith('material.xml') && name.startsWith('materials/')) {
        Vff.validateEntrySize(entry);
        RawMaterial? mat;
        try {
          mat = Geometry.parseMaterialXml(zip, name, entry.content, options);
        } catch (e) {
          mat = null;
          emitLog(options, SkpLogLevel.debug,
              'Failed to parse material.xml $name: $e');
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
            layerHidden[mat.name.substring(6)] = false;
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
      Vff.validateEntrySize(entry);
      final style = Geometry.parseStyleXml(entry.content, name, options);
      if (style != null) {
        styles.add(style);
      }
    }

    emitLog(options, SkpLogLevel.debug,
        'Parsed ${materials.length} materials, ${styles.length} styles');

    final modelDatEntry = zip.findFile('model.dat');
    if (modelDatEntry == null) {
      throw SkpParseException('model.dat not found in ZIP container',
          stage: 'zip_extract');
    }
    Vff.validateEntrySize(modelDatEntry);
    final modelDat = modelDatEntry.content;
    emitLog(
        options, SkpLogLevel.debug, 'Read model.dat: ${modelDat.length} bytes');

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
    final vertexPositions = <String, (double, double, double)>{};
    final instanceWorld = <String, List<double>>{};
    TlvNode? pageNode;

    for (final (index, total, el) in Tlv.iterTopLevelLazy(
        modelDat, 0, modelDat.length, Tlv.containerTags)) {
      try {
        Geometry.collectLayers([el], layerIdToName);
        Geometry.collectMaterialIds([el], materialIdToName);
        Geometry.collectDefs([el], defsDictRaw);
        scanVertexPositions(el, vertexPositions);
        scanInstanceTransforms(el, instanceWorld);
        pageNode ??= findPageNode(el);
        if (el.tag == 'F601') {
          Geometry.extractGeometryFromNodes(el.children, rootBuilder);
        }
      } catch (e) {
        throw SkpParseException(
          'Failed while processing top-level record: $e',
          stage: 'tlv_walk',
          recordIndex: index,
          totalRecords: total,
          tag: el.tag,
          cause: e,
        );
      }
      // `el` (and its whole subtree) is now unreferenced and eligible for
      // garbage collection before the next top-level record is built.
      if (index % progressInterval == 0 || index == total - 1) {
        emitProgress(options, 'tlv_walk', index + 1, total);
        emitLog(options, SkpLogLevel.debug,
            'Processed ${index + 1}/$total top-level records');
      }
    }

    emitLog(
      options,
      SkpLogLevel.info,
      'Parse complete: ${defsDictRaw.length} defs (${(sw.elapsedMilliseconds / 1000).toStringAsFixed(2)}s)',
    );

    // Units (meta/meta.dat) - VFF-only; legacy files carry no equivalent
    // container.
    String? units;
    final metaDatEntry = zip.findFile('meta/meta.dat');
    if (metaDatEntry != null) {
      try {
        Vff.validateEntrySize(metaDatEntry);
        units = Vff.readMetaUnits(metaDatEntry.content);
      } catch (_) {
        units = null;
      }
    }

    if (!layerIdToName.containsKey(1)) {
      layerIdToName[1] = 'Layer0';
    }
    if (!layerColors.containsKey('Layer0')) {
      layerColors['Layer0'] = (136, 136, 136);
    }
    if (!layerHidden.containsKey('Layer0')) {
      layerHidden['Layer0'] = false;
    }

    return RawParsed()
      ..version = version
      ..units = units
      ..layerColors.addAll(layerColors)
      ..layerHidden.addAll(layerHidden)
      ..layerIdToName.addAll(layerIdToName)
      ..pages.addAll(parsePages(pageNode))
      ..dimensions
          .addAll(parseDimensions(modelDat, vertexPositions, instanceWorld))
      ..materialIdToName.addAll(materialIdToName)
      ..materials.addAll(materials)
      ..materialsByFolder.addAll(materialsByFolder)
      ..styles.addAll(styles)
      ..defsDict.addAll(defsDictRaw)
      ..root =
          RawDefinition(guid: 'ROOT', name: 'ROOT_MODEL', builder: rootBuilder);
  }
}
