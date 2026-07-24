import 'dart:convert';
import 'dart:typed_data';

import 'package:archive/archive.dart';
import 'package:xml/xml.dart';

import 'tlv.dart';

class GeometryBuilderFace {
  List<List<(int edgeId, int orientation)>> loops = [];
  (double, double, double) normal = (0.0, 0.0, 1.0);
  int? materialId;
  int? backMaterialId;
  List<double>? uvTransform;
  List<double>? uvTransformBack;
}

class GeometryBuilderInstance {
  int offset = 0;
  String? refGuid;
  int? refIdx;
  String? name;
  List<double> matrix = [];
  int? materialId;
  List<TlvNode> children = const [];
}

/// Accumulates the raw geometry extracted for one component definition (or
/// the implicit ROOT definition). Mirrors Python's _GeometryBuilder.
class GeometryBuilder {
  final Map<int, (double, double, double)> vertices = {};
  final Map<int, (int?, int?)> edges = {};
  final Map<int, int> edgeFlags = {};
  final Map<int, GeometryBuilderFace> faces = {};
  final List<GeometryBuilderInstance> instances = [];
}

class RawTexture {
  String filename;
  double xScale;
  double yScale;
  Uint8List? data;
  RawTexture(
      {required this.filename,
      required this.xScale,
      required this.yScale,
      this.data});
}

class RawMaterial {
  String name;
  int r, g, b;
  double transparency;
  bool colorized;
  int colorizeType;
  RawTexture? texture;
  RawMaterial({
    required this.name,
    this.r = 128,
    this.g = 128,
    this.b = 128,
    this.transparency = 1.0,
    this.colorized = false,
    this.colorizeType = 0,
    this.texture,
  });
}

class RawStyle {
  String name;
  (int, int, int)? frontColor;
  (int, int, int)? backColor;
  RawStyle({required this.name, this.frontColor, this.backColor});
}

class RawDefinition {
  String? guid;
  String? name;
  bool alwaysFacesCamera;
  bool isImage;
  GeometryBuilder builder;
  RawDefinition({
    this.guid,
    this.name,
    this.alwaysFacesCamera = false,
    this.isImage = false,
    GeometryBuilder? builder,
  }) : builder = builder ?? GeometryBuilder();
}

class Geometry {
  static TlvNode? findChildTag(List<TlvNode> nodes, String target) {
    for (final n in nodes) {
      if (n.tag == target) return n;
      final res = findChildTag(n.children, target);
      if (res != null) return res;
    }
    return null;
  }

  static void findAllNodesRec(
      List<TlvNode> nodes, String targetTag, List<TlvNode> results) {
    for (final n in nodes) {
      if (n.tag == targetTag) results.add(n);
      findAllNodesRec(n.children, targetTag, results);
    }
  }

  /// Mirrors Python's extract_entity_id exactly: only DE05 and a
  /// DE05-prefixed DC05 resolve an ID here (unlike collectLayers /
  /// collectMaterialIds, which also fall back to a bare var-int read when
  /// the DC05 payload lacks the DE05 marker).
  static int? extractEntityId(TlvNode node) {
    for (final child in node.children) {
      if (child.tag == 'DE05') {
        return Tlv.parseVarInt(child.payload, 0, child.payload.length);
      }
      if (child.tag == 'DC05') {
        final payload = child.payload;
        if (payload.length >= 2 && payload[0] == 0xDE && payload[1] == 0x05) {
          final de05Len = Tlv.readU32(payload, 2);
          return Tlv.parseVarInt(payload, 6, de05Len);
        }
      }
    }
    for (final child in node.children) {
      final res = extractEntityId(child);
      if (res != null) return res;
    }
    return null;
  }

  /// Entity-ID resolution used by collectLayers / collectMaterialIds: falls
  /// back to a raw var-int read of the whole DC05 payload when it doesn't
  /// start with the DE05 marker.
  static int _parseIdFromDc05(Uint8List payload) {
    if (payload.length >= 2 && payload[0] == 0xDE && payload[1] == 0x05) {
      final de05Len = Tlv.readU32(payload, 2);
      return Tlv.parseVarInt(payload, 6, de05Len);
    }
    return Tlv.parseVarInt(payload, 0, payload.length);
  }

  static (List<double>?, List<double>?) extractUvTransforms(
      Uint8List dc05Payload) {
    final dd05 = Tlv.findFlat(Tlv.parseFlat(dc05Payload), 'DD05');
    if (dd05 == null) return (null, null);
    final b136 = Tlv.findFlat(Tlv.parseFlat(dd05), 'B136');
    if (b136 == null) return (null, null);
    final b236 = Tlv.findFlat(Tlv.parseFlat(b136), 'B236');
    if (b236 == null) return (null, null);
    final t1027 = Tlv.findFlat(Tlv.parseFlat(b236), '1027');
    if (t1027 == null) return (null, null);

    final sides = Tlv.parseFlat(t1027);
    final front = _extractUvSide(sides, '1127');
    final back = _extractUvSide(sides, '1227');
    return (front, back);
  }

  static List<double>? _extractUvSide(
      List<(String, Uint8List)> sides, String sideTag) {
    final side = Tlv.findFlat(sides, sideTag);
    if (side == null) return null;
    final t1327 = Tlv.findFlat(Tlv.parseFlat(side), '1327');
    if (t1327 == null) return null;
    final t1527 = Tlv.findFlat(Tlv.parseFlat(t1327), '1527');
    if (t1527 == null || t1527.length != 72) return null;
    return [for (int i = 0; i < 9; i++) Tlv.readF64(t1527, i * 8)];
  }

  static void extractGeometryFromNodes(
      List<TlvNode> elements, GeometryBuilder builder) {
    for (final el in elements) {
      final tag = el.tag;

      if (tag == 'C409') {
        final vId = extractEntityId(el);
        final c509 = findChildTag(el.children, 'C509');
        if (vId != null && c509 != null && c509.payload.length >= 24) {
          final x = Tlv.readF64(c509.payload, 0);
          final y = Tlv.readF64(c509.payload, 8);
          final z = Tlv.readF64(c509.payload, 16);
          builder.vertices[vId] = (x, y, z);
        }
      } else if (tag == 'B80B') {
        final eId = extractEntityId(el);
        if (eId != null) {
          final v1Node = findChildTag(el.children, 'B90B');
          final v2Node = findChildTag(el.children, 'BA0B');
          final v1 = v1Node != null
              ? Tlv.parseVarInt(v1Node.payload, 0, v1Node.payload.length)
              : null;
          final v2 = v2Node != null
              ? Tlv.parseVarInt(v2Node.payload, 0, v2Node.payload.length)
              : null;
          builder.edges[eId] = (v1, v2);

          final d007 = el.children.where((c) => c.tag == 'D007').firstOrNull;
          if (d007 != null) {
            final d307 =
                d007.children.where((c) => c.tag == 'D307').firstOrNull;
            if (d307 != null && d307.payload.isNotEmpty) {
              builder.edgeFlags[eId] = d307.payload[0];
            }
          }
        }
      } else if (tag == 'AC0D') {
        final fId = extractEntityId(el);
        if (fId != null) {
          var normal = (0.0, 0.0, 1.0);
          final ad0d = findChildTag(el.children, 'AD0D');
          if (ad0d != null && ad0d.payload.length >= 24) {
            normal = (
              Tlv.readF64(ad0d.payload, 0),
              Tlv.readF64(ad0d.payload, 8),
              Tlv.readF64(ad0d.payload, 16),
            );
          }

          final ae0d = findChildTag(el.children, 'AE0D');
          final loops = <List<(int, int)>>[];
          if (ae0d != null) {
            final loopNodes = <TlvNode>[];
            findAllNodesRec(ae0d.children, '9411', loopNodes);
            for (final ln in loopNodes) {
              final coEdges = <(int, int)>[];
              final coNodes = <TlvNode>[];
              findAllNodesRec(ln.children, 'A00F', coNodes);
              for (final cn in coNodes) {
                final payload = cn.payload;
                int? edgeId;
                int? orient;
                int subPos = 0;
                while (subPos < payload.length - 6) {
                  final b0 = payload[subPos];
                  final b1 = payload[subPos + 1];
                  final subSize = Tlv.readU32(payload, subPos + 2);
                  if (subPos + 6 + subSize <= payload.length) {
                    final val = Tlv.parseVarInt(payload, subPos + 6, subSize);
                    if (b0 == 0xA1 && b1 == 0x0F) {
                      edgeId = val;
                    } else if (b0 == 0xA2 && b1 == 0x0F) {
                      orient = val;
                    }
                  }
                  subPos += 6 + subSize;
                }
                if (edgeId != null && orient != null) {
                  coEdges.add((edgeId, orient));
                }
              }
              if (coEdges.isNotEmpty) loops.add(coEdges);
            }
          }

          int? faceMatId;
          List<double>? uvFront;
          List<double>? uvBack;
          final d007 = el.children.where((c) => c.tag == 'D007').firstOrNull;
          if (d007 != null) {
            final d107 =
                d007.children.where((c) => c.tag == 'D107').firstOrNull;
            if (d107 != null) {
              faceMatId = Tlv.parseVarInt(d107.payload, 0, d107.payload.length);
            }
            final dc05 =
                d007.children.where((c) => c.tag == 'DC05').firstOrNull;
            if (dc05 != null) {
              final (f, b) = extractUvTransforms(dc05.payload);
              uvFront = f;
              uvBack = b;
            }
          }

          int? backMatId;
          final af0d = el.children.where((c) => c.tag == 'AF0D').firstOrNull;
          if (af0d != null && af0d.payload.isNotEmpty) {
            backMatId = Tlv.parseVarInt(af0d.payload, 0, af0d.payload.length);
          }

          builder.faces[fId] = GeometryBuilderFace()
            ..loops = loops
            ..normal = normal
            ..materialId = faceMatId
            ..backMaterialId = backMatId
            ..uvTransform = uvFront
            ..uvTransformBack = uvBack;
        }
      } else if (tag == '6419') {
        final nodesToSearch = el.children.isNotEmpty ? el.children : [el];
        String? guid;
        int? defIdx;
        String? name;
        final matrix = <double>[];

        final guidNode = findChildTag(nodesToSearch, '6819');
        if (guidNode != null && guidNode.payload.length == 16) {
          guid = Tlv.toHexUpper(guidNode.payload);
        }
        final defIdxNode = findChildTag(nodesToSearch, '6719');
        if (defIdxNode != null) {
          defIdx =
              Tlv.parseVarInt(defIdxNode.payload, 0, defIdxNode.payload.length);
        }
        final nameNode = findChildTag(nodesToSearch, '6519');
        if (nameNode != null) {
          name = Tlv.decodeUtf8(nameNode.payload);
        }
        final matNode = findChildTag(nodesToSearch, '6619');
        if (matNode != null && matNode.payload.length >= 104) {
          for (int idx = 0; idx < 13; idx++) {
            matrix.add(Tlv.readF64(matNode.payload, idx * 8));
          }
        }

        int? instMatId;
        final instD007 = el.children.where((c) => c.tag == 'D007').firstOrNull;
        if (instD007 != null) {
          final d107 =
              instD007.children.where((c) => c.tag == 'D107').firstOrNull;
          if (d107 != null) {
            instMatId = Tlv.parseVarInt(d107.payload, 0, d107.payload.length);
          }
        }

        builder.instances.add(GeometryBuilderInstance()
          ..offset = el.offset
          ..refGuid = guid
          ..refIdx = defIdx
          ..name = name
          ..matrix = matrix
          ..materialId = instMatId
          ..children = el.children);
      } else if (el.children.isNotEmpty) {
        extractGeometryFromNodes(el.children, builder);
      }
    }
  }

  // ── Layer / material ID lookups (used by Core.fullParse) ────────────────

  static void collectLayers(
      List<TlvNode> nodes, Map<int, String> layerIdToName) {
    for (final el in nodes) {
      if (el.tag == '993A') {
        for (final child in el.children) {
          if (child.tag == '8C3C') {
            final dc05 = findChildTag(child.children, 'DC05');
            final nameNode = findChildTag(child.children, '8D3C');
            if (dc05 != null && nameNode != null) {
              final lId = _parseIdFromDc05(dc05.payload);
              final lName = Tlv.decodeUtf8(nameNode.payload);
              layerIdToName[lId] = lName;
            }
          }
        }
      }
      collectLayers(el.children, layerIdToName);
    }
  }

  static void collectMaterialIds(
      List<TlvNode> nodes, Map<int, String> materialIdToName) {
    for (final el in nodes) {
      if (el.tag == 'C832') {
        final dc05 = findChildTag(el.children, 'DC05');
        final nameNode = findChildTag(el.children, 'CC32');
        if (dc05 != null && nameNode != null) {
          final mId = _parseIdFromDc05(dc05.payload);
          final mName = Tlv.decodeUtf8(nameNode.payload);
          materialIdToName[mId] = mName;
        }
      }
      collectMaterialIds(el.children, materialIdToName);
    }
  }

  static void collectDefs(
      List<TlvNode> nodes, Map<int, RawDefinition> defsDict) {
    for (final el in nodes) {
      if (el.tag == '7C15') {
        String? guid;
        String? name;
        bool facesCamera = false;
        bool isImage = false;
        for (final child in el.children) {
          if (child.tag == '7D15' && child.payload.length == 16) {
            guid = Tlv.toHexUpper(child.payload);
          } else if (child.tag == '7E15') {
            name = Tlv.decodeUtf8(child.payload);
          } else if (child.tag == '581B') {
            int pos = 0;
            final pl = child.payload;
            while (pos <= pl.length - 6) {
              final subTag =
                  Tlv.toHexUpper(Uint8List.sublistView(pl, pos, pos + 2));
              final subSize = Tlv.readU32(pl, pos + 2);
              if (pos + 6 + subSize > pl.length) break;
              if (subTag == '5D1B' && subSize >= 1) {
                facesCamera = Tlv.parseVarInt(pl, pos + 6, subSize) == 1;
              }
              pos += 6 + subSize;
            }
          } else if (child.tag == '8315' && child.payload.isNotEmpty) {
            isImage =
                Tlv.parseVarInt(child.payload, 0, child.payload.length) == 2;
          }
        }
        final entId = extractEntityId(el);
        final builder = GeometryBuilder();
        extractGeometryFromNodes(el.children, builder);
        if (entId != null) {
          defsDict[entId] = RawDefinition(
            guid: guid,
            name: name,
            alwaysFacesCamera: facesCamera,
            isImage: isImage,
            builder: builder,
          );
        }
      }
      collectDefs(el.children, defsDict);
    }
  }

  // ── material.xml / style.xml parsing ─────────────────────────────────────

  static const _matNs =
      'http://sketchup.google.com/schemas/sketchup/1.0/material';
  static const _styleNs =
      'http://sketchup.google.com/schemas/sketchup/1.0/style';
  static const _typesNs = 'http://sketchup.google.com/schemas/1.0/types';

  /// Parse one materials/<folder>/material.xml entry. [xmlName] is the
  /// archive path (e.g. "materials/Wood/material.xml"), used to resolve
  /// sibling texture image files.
  static RawMaterial? parseMaterialXml(
      Archive zip, String xmlName, Uint8List xmlData) {
    XmlDocument doc;
    try {
      doc = XmlDocument.parse(utf8.decode(xmlData));
    } catch (_) {
      return null;
    }

    final matElem =
        doc.findAllElements('material', namespaceUri: _matNs).firstOrNull;
    if (matElem == null) return null;

    final matName = matElem.getAttribute('name') ?? 'unknown';
    final r = int.tryParse(matElem.getAttribute('colorRed') ?? '') ?? 128;
    final g = int.tryParse(matElem.getAttribute('colorGreen') ?? '') ?? 128;
    final b = int.tryParse(matElem.getAttribute('colorBlue') ?? '') ?? 128;

    double trans;
    if (matElem.getAttribute('useTrans') == '1') {
      final raw = double.tryParse(matElem.getAttribute('trans') ?? '') ?? 0.0;
      trans = (1.0 - raw).clamp(0.0, 1.0);
    } else {
      trans = 1.0;
    }

    final colorized = matElem.getAttribute('type') == '2';
    final colorizeType =
        int.tryParse(matElem.getAttribute('colorizeType') ?? '') ?? 0;

    final mat = RawMaterial(
      name: matName,
      r: r,
      g: g,
      b: b,
      transparency: trans,
      colorized: colorized,
      colorizeType: colorizeType,
    );

    mat.texture = _extractTexture(zip, xmlName, matElem);
    return mat;
  }

  static RawTexture? _extractTexture(
      Archive zip, String xmlName, XmlElement matElem) {
    final texElem = matElem.getElement('texture', namespaceUri: _matNs);
    if (texElem == null) return null;

    var filename = texElem.getAttribute('textureFilename') ?? '';
    final xScale = double.tryParse(texElem.getAttribute('xScale') ?? '') ?? 0.0;
    final yScale = double.tryParse(texElem.getAttribute('yScale') ?? '') ?? 0.0;

    final lastSlash = xmlName.lastIndexOf('/');
    final folder = lastSlash >= 0 ? xmlName.substring(0, lastSlash) : '';

    Uint8List? data;
    final entryNames = zip.files.map((f) => f.name).toSet();

    final candidate = filename.isNotEmpty ? '$folder/$filename' : null;
    if (candidate != null && entryNames.contains(candidate)) {
      data = zip.findFile(candidate)?.content;
    } else {
      for (final entry in zip.files) {
        if (entry.name.startsWith('$folder/') &&
            entry.name != xmlName &&
            !entry.name.toLowerCase().endsWith('.xml')) {
          data = entry.content;
          if (filename.isEmpty) {
            final s = entry.name.lastIndexOf('/');
            filename = s >= 0 ? entry.name.substring(s + 1) : entry.name;
          }
          break;
        }
      }
    }

    if (data == null) {
      final imgElem = texElem
          .getElement('images', namespaceUri: _matNs)
          ?.getElement('image', namespaceUri: _matNs);
      var imgPath = imgElem?.getAttribute('path') ?? '';
      imgPath = _lstripChars(imgPath, './');
      for (final cand in [imgPath, '$folder/$imgPath']) {
        if (cand.isNotEmpty && entryNames.contains(cand)) {
          data = zip.findFile(cand)?.content;
          if (filename.isEmpty) {
            final s = cand.lastIndexOf('/');
            filename = s >= 0 ? cand.substring(s + 1) : cand;
          }
          break;
        }
      }
    }

    return RawTexture(
        filename: filename, xScale: xScale, yScale: yScale, data: data);
  }

  /// Matches Python's str.lstrip(chars): repeatedly strips any leading
  /// character found in [chars], NOT the literal prefix "./".
  static String _lstripChars(String s, String chars) {
    int i = 0;
    while (i < s.length && chars.contains(s[i])) i++;
    return s.substring(i);
  }

  static RawStyle? parseStyleXml(Uint8List xmlData) {
    XmlDocument doc;
    try {
      doc = XmlDocument.parse(utf8.decode(xmlData));
    } catch (_) {
      return null;
    }

    final styleEl = doc.rootElement.getElement('style', namespaceUri: _styleNs);
    if (styleEl == null) return null;

    final colors = <String, (int, int, int)>{};
    for (final item in styleEl.findElements('item', namespaceUri: _styleNs)) {
      final iid = item.getAttribute('id');
      final variant = item.getElement('variant', namespaceUri: _typesNs);
      if ((iid == '4000' || iid == '4001') &&
          variant != null &&
          variant.innerText.isNotEmpty) {
        final signed = int.tryParse(variant.innerText);
        if (signed != null) {
          final v = signed & 0xFFFFFFFF;
          colors[iid!] = ((v >> 16) & 255, (v >> 8) & 255, v & 255);
        }
      }
    }

    return RawStyle(
      name: styleEl.getAttribute('name') ?? '',
      frontColor: colors['4000'],
      backColor: colors['4001'],
    );
  }
}

extension _FirstOrNull<T> on Iterable<T> {
  T? get firstOrNull => isEmpty ? null : first;
}
