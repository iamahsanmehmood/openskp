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
  ((int, int, int), (int, int, int))? faceColors;
  bool colorByTag = false;
  Map<String, Map<String, Object?>> modelAttributeDictionaries = {};
  final Map<int, RawDefinition> defsDict = {};
  RawDefinition root = RawDefinition(guid: 'ROOT', name: 'ROOT_MODEL');
  int unreadRootEntities = 0;
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

    final layerColors = <String, (int, int, int)>{};
    final layerHidden = <String, bool>{};
    final materials = <String, RawMaterial>{};
    final materialsByFolder = <String, RawMaterial>{};
    final List<(String, RawMaterial)> rawMaterialEntries;
    final List<RawStyle> styles;
    final Uint8List modelDat;
    final String? units;
    try {
      (rawMaterialEntries, styles, modelDat, units) =
          _readVffZip(data, pkPos, materials.length, options);
    } on SkpParseException {
      rethrow;
    } on OutOfMemoryError {
      rethrow;
    } on StackOverflowError {
      rethrow;
    } catch (e) {
      throw SkpParseException('Invalid or damaged ZIP container: $e',
          stage: 'zip_extract', cause: e);
    }
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
    final layerFolderToName = <String, String>{};
    final materialIdToName = <int, String>{};
    final defsDictRaw = <int, RawDefinition>{};
    final rootBuilder = GeometryBuilder();
    final vertexPositions = <String, (double, double, double)>{};
    TlvNode? pageNode;
    var modelAttributeDicts = <String, Map<String, Object?>>{};

    for (final (index, total, el) in Tlv.iterTopLevelLazy(
        modelDat, 0, modelDat.length, Tlv.containerTags)) {
      try {
        Geometry.collectLayers(
            [el], layerIdToName, layerHidden, layerFolderToName);
        Geometry.collectMaterialIds([el], materialIdToName);
        Geometry.collectDefs([el], defsDictRaw);
        scanVertexPositions(el, vertexPositions);
        pageNode ??= findPageNode(el);
        if (el.tag == 'F601') {
          Geometry.extractGeometryFromNodes(el.children, rootBuilder);
          modelAttributeDicts =
              Geometry.entityAttributeDictionaries(el.children);
        }
      } on SkpParseException {
        rethrow;
      } on OutOfMemoryError {
        rethrow;
      } on StackOverflowError {
        rethrow;
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

    final realMaterialFolders = materialIdToName.values.toSet();
    for (final (folderName, mat) in rawMaterialEntries) {
      final exactTagName = layerFolderToName[folderName];
      if ((exactTagName != null || mat.name.startsWith('Layer_')) &&
          !realMaterialFolders.contains(folderName)) {
        final tagName = exactTagName ?? mat.name.substring(6);
        layerColors[tagName] = (mat.r, mat.g, mat.b);
        layerHidden.putIfAbsent(tagName, () => false);
      } else {
        materials[mat.name] = mat;
        if (folderName.isNotEmpty) {
          materialsByFolder[folderName] = mat;
        }
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

    List<RawPage> pages;
    try {
      pages = parsePages(pageNode);
    } on OutOfMemoryError {
      rethrow;
    } on StackOverflowError {
      rethrow;
    } catch (e) {
      emitLog(options, SkpLogLevel.debug, 'Failed to parse pages: $e');
      pages = [];
    }

    final dimensions = <RawDimension>[];
    try {
      assignVffAnnotations(
        modelDat,
        vertexPositions,
        rootBuilder,
        defsDictRaw,
        worldDimensions: dimensions,
      );
    } on OutOfMemoryError {
      rethrow;
    } on StackOverflowError {
      rethrow;
    } catch (e) {
      emitLog(options, SkpLogLevel.debug, 'Failed to parse annotations: $e');
      dimensions.clear();
    }

    ((int, int, int), (int, int, int))? faceColors;
    try {
      faceColors = _modelFaceColors(modelDat);
    } on OutOfMemoryError {
      rethrow;
    } on StackOverflowError {
      rethrow;
    } catch (e) {
      emitLog(options, SkpLogLevel.debug, 'Failed to parse model face colours: $e');
      faceColors = null;
    }

    bool colorByTag = false;
    try {
      colorByTag = _modelColorByTag(modelDat);
    } on OutOfMemoryError {
      rethrow;
    } on StackOverflowError {
      rethrow;
    } catch (e) {
      emitLog(options, SkpLogLevel.debug, 'Failed to parse model colorByTag: $e');
      colorByTag = false;
    }

    return RawParsed()
      ..version = version
      ..units = units
      ..layerColors.addAll(layerColors)
      ..layerHidden.addAll(layerHidden)
      ..layerIdToName.addAll(layerIdToName)
      ..pages.addAll(pages)
      ..dimensions.addAll(dimensions)
      ..materialIdToName.addAll(materialIdToName)
      ..materials.addAll(materials)
      ..materialsByFolder.addAll(materialsByFolder)
      ..styles.addAll(styles)
      ..faceColors = faceColors
      ..colorByTag = colorByTag
      ..modelAttributeDictionaries = modelAttributeDicts
      ..defsDict.addAll(defsDictRaw)
      ..root = RawDefinition(
        guid: 'ROOT',
        name: 'ROOT_MODEL',
        attributeDictionaries: modelAttributeDicts,
        builder: rootBuilder,
      );
  }

  static (
    List<(String, RawMaterial)>,
    List<RawStyle>,
    Uint8List,
    String?,
  ) _readVffZip(
    Uint8List data,
    int pkPos,
    int initialMaterialCount,
    ParseOptions? options,
  ) {
    final zip = Vff.openZip(data, pkPos);
    final rawMaterialEntries = <(String, RawMaterial)>[];

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
          rawMaterialEntries.add((folderName, mat));
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
        'Parsed $initialMaterialCount materials, ${styles.length} styles');

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

    final modelDatEntry = zip.findFile('model.dat');
    if (modelDatEntry == null) {
      throw SkpParseException('model.dat not found in ZIP container',
          stage: 'zip_extract');
    }
    Vff.validateEntrySize(modelDatEntry);
    final modelDat = modelDatEntry.content;
    return (rawMaterialEntries, styles, modelDat, units);
  }

  static const _faceFrontTag = [0x5C, 0x73, 0x04, 0x00, 0x00, 0x00];
  static const _faceBackTag = [0x5D, 0x73, 0x04, 0x00, 0x00, 0x00];

  static ((int, int, int), (int, int, int))? _modelFaceColors(
      Uint8List modelDat) {
    for (int at = 0; at + 20 <= modelDat.length; at++) {
      if (modelDat[at] == _faceFrontTag[0] &&
          modelDat[at + 1] == _faceFrontTag[1] &&
          modelDat[at + 2] == _faceFrontTag[2] &&
          modelDat[at + 3] == _faceFrontTag[3] &&
          modelDat[at + 4] == _faceFrontTag[4] &&
          modelDat[at + 5] == _faceFrontTag[5] &&
          modelDat[at + 10] == _faceBackTag[0] &&
          modelDat[at + 11] == _faceBackTag[1] &&
          modelDat[at + 12] == _faceBackTag[2] &&
          modelDat[at + 13] == _faceBackTag[3] &&
          modelDat[at + 14] == _faceBackTag[4] &&
          modelDat[at + 15] == _faceBackTag[5]) {
        return (
          (modelDat[at + 6], modelDat[at + 7], modelDat[at + 8]),
          (modelDat[at + 16], modelDat[at + 17], modelDat[at + 18]),
        );
      }
    }
    return null;
  }

  static bool _modelColorByTag(Uint8List modelDat) {
    for (int at = 0; at + 12 <= modelDat.length; at++) {
      if (modelDat[at] == 0xFB &&
          modelDat[at + 1] == 0x01 &&
          modelDat[at + 6] == 0x3C &&
          modelDat[at + 7] == 0x73) {
        final outerLen = Tlv.readU32(modelDat, at + 2);
        final innerLen = Tlv.readU32(modelDat, at + 8);
        if (outerLen >= 6 &&
            outerLen - 6 == innerLen &&
            at + 12 + innerLen <= modelDat.length) {
          int p = at + 12;
          final end = p + innerLen;
          while (p + 6 <= end) {
            final tag = Tlv.readU16(modelDat, p);
            final sz = Tlv.readU32(modelDat, p + 2);
            p += 6;
            if (sz > end - p) break;
            if (tag == 0x7347 && sz == 1 && modelDat[p] <= 1) {
              return modelDat[p] == 1;
            }
            p += sz;
          }
        }
      }
    }
    return false;
  }
}
