import 'dart:io';
import 'dart:typed_data';

import 'core.dart';
import 'geometry.dart';
import 'legacy.dart';
import 'model.dart';

/// High-level entry point for opening and parsing .skp files.
///
/// ```dart
/// final model = SkpFile.open('house.skp').parse();
/// print(model.version);
/// for (final layer in model.layers) print(layer.name);
/// ```
class SkpFile {
  final String path;
  final Uint8List? _bytes;

  SkpFile._(this.path, this._bytes);

  /// Open a SketchUp file for parsing.
  factory SkpFile.open(String filepath) {
    final f = File(filepath);
    if (!f.existsSync()) {
      throw FileSystemException('File not found', filepath);
    }
    if (!filepath.toLowerCase().endsWith('.skp')) {
      throw ArgumentError('Expected a .skp file, got: $filepath');
    }
    return SkpFile._(filepath, null);
  }

  /// Parse directly from an in-memory buffer (no file I/O).
  factory SkpFile.fromBuffer(Uint8List bytes) {
    return SkpFile._('<memory>', bytes);
  }

  SkpModel parse() {
    final bytes = _bytes ?? File(path).readAsBytesSync();

    RawParsed parsed;
    try {
      parsed = Core.fullParse(bytes);
    } on LegacyParseError catch (e) {
      throw ArgumentError('legacy .skp parse failed: ${e.message}');
    }

    final model = SkpModel()..version = parsed.version;

    for (final entry in parsed.defsDict.entries) {
      model.definitions[entry.key] = _buildDefinition(entry.key, entry.value);
    }
    model.root = _buildDefinition(0, parsed.root);

    for (final entry in parsed.layerColors.entries) {
      final (r, g, b) = entry.value;
      model.layers.add(Layer(name: entry.key, colorR: r, colorG: g, colorB: b));
    }

    final matForData = <RawMaterial, Material>{};
    for (final rawMat in parsed.materials.values) {
      Texture? texture;
      final rawTex = rawMat.texture;
      if (rawTex != null) {
        texture = Texture(
            filename: rawTex.filename,
            width: rawTex.xScale,
            height: rawTex.yScale,
            data: rawTex.data);
      }
      final mat = Material(
        name: rawMat.name,
        color: (rawMat.r, rawMat.g, rawMat.b, 255),
        transparency: rawMat.transparency,
        texture: texture,
        colorized: rawMat.colorized,
        colorizeType: rawMat.colorizeType,
      );
      model.materials.add(mat);
      matForData[rawMat] = mat;
    }

    for (final entry in parsed.materialIdToName.entries) {
      final mId = entry.key;
      final mName = entry.value;
      final rawMat = parsed.materials[mName] ?? parsed.materialsByFolder[mName];
      if (rawMat == null) continue;
      final mat = matForData[rawMat];
      if (mat == null) continue;
      mat.id ??= mId;
      model.materialsById[mId] = mat;
    }

    for (final st in parsed.styles) {
      model.styles.add(Style(
          name: st.name, frontColor: st.frontColor, backColor: st.backColor));
    }

    return model;
  }

  static Definition _buildDefinition(int defId, RawDefinition d) {
    final defn = Definition(
      id: defId,
      guid: d.guid ?? '',
      name: d.name ?? '',
      alwaysFacesCamera: d.alwaysFacesCamera,
      isImage: d.isImage,
    );

    for (final entry in d.builder.vertices.entries) {
      final (x, y, z) = entry.value;
      defn.vertices[entry.key] = Vertex(id: entry.key, x: x, y: y, z: z);
    }

    for (final entry in d.builder.edges.entries) {
      final (v1, v2) = entry.value;
      final flags = d.builder.edgeFlags[entry.key] ?? 0;
      defn.edges[entry.key] = Edge(
        id: entry.key,
        v1Id: v1 ?? 0,
        v2Id: v2 ?? 0,
        soft: (flags & 0x08) != 0,
        smooth: (flags & 0x10) != 0,
        hidden: (flags & 0x01) != 0,
      );
    }

    for (final entry in d.builder.faces.entries) {
      final f = entry.value;
      defn.faces[entry.key] = Face(
        id: entry.key,
        loops: f.loops,
        normal: f.normal,
        materialId: f.materialId,
        backMaterialId: f.backMaterialId,
        uvTransform: f.uvTransform,
        uvTransformBack: f.uvTransformBack,
      );
    }

    for (final inst in d.builder.instances) {
      defn.instances.add(Instance(
        name: inst.name ?? '',
        refIdx: inst.refIdx,
        guid: inst.refGuid ?? '',
        matrix: inst.matrix,
        materialId: inst.materialId,
      ));
    }

    return defn;
  }
}
