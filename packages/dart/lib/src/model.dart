import 'dart:io';
import 'dart:typed_data';

class Vertex {
  final int id;
  final double x, y, z;
  Vertex({required this.id, required this.x, required this.y, required this.z});
}

class Edge {
  final int id;
  final int v1Id;
  final int v2Id;
  final bool soft;
  final bool smooth;
  final bool hidden;
  Edge({
    required this.id,
    required this.v1Id,
    required this.v2Id,
    this.soft = false,
    this.smooth = false,
    this.hidden = false,
  });
}

class Face {
  final int id;

  /// Ordered list of loops; each loop is a list of (edgeId, orientation)
  /// pairs where orientation is 1 for forward or -1 for reversed.
  final List<List<(int edgeId, int orientation)>> loops;

  final (double, double, double)? normal;
  final int? materialId;
  final int? backMaterialId;

  /// Per-face texture mapping for a positioned/photo-fitted texture
  /// (SketchUp's pins), or null when the default projection applies. A
  /// 9-element row-major 3x3 matrix mapping texture space to the face plane.
  final List<double>? uvTransform;
  final List<double>? uvTransformBack;

  Face({
    required this.id,
    this.loops = const [],
    this.normal,
    this.materialId,
    this.backMaterialId,
    this.uvTransform,
    this.uvTransformBack,
  });
}

class Layer {
  final String name;
  final int colorR, colorG, colorB;
  Layer(
      {required this.name,
      this.colorR = 200,
      this.colorG = 200,
      this.colorB = 200});
}

class Style {
  final String name;
  final (int, int, int)? frontColor;
  final (int, int, int)? backColor;
  Style({this.name = '', this.frontColor, this.backColor});
}

class Texture {
  final String filename;
  final double width;
  final double height;
  final Uint8List? data;
  Texture({this.filename = '', this.width = 0.0, this.height = 0.0, this.data});

  File save(String filepath) {
    final d = data;
    if (d == null) {
      throw StateError("Texture '$filename' has no image data");
    }
    return File(filepath)..writeAsBytesSync(d);
  }
}

class Material {
  final String name;
  final (int, int, int, int) color;
  final double transparency;

  /// Numeric material ID from the TLV stream - the value that
  /// [Face.materialId] references. Null when the file assigns the material
  /// no ID.
  int? id;

  final Texture? texture;
  final bool colorized;
  final int colorizeType;

  Material({
    required this.name,
    this.color = (200, 200, 200, 255),
    this.transparency = 1.0,
    this.id,
    this.texture,
    this.colorized = false,
    this.colorizeType = 0,
  });
}

class Instance {
  final String name;
  final int? refIdx;
  final String guid;

  /// 4x4 transform stored as a flat 16-element list in column-major order
  /// (empty when the entity carried none).
  final List<double> matrix;

  final String layer;
  final Map<String, String> properties;
  final List<Instance> children;
  final int? materialId;

  Instance({
    this.name = '',
    this.refIdx,
    this.guid = '',
    this.matrix = const [],
    this.layer = '',
    this.properties = const {},
    this.children = const [],
    this.materialId,
  });
}

class Definition {
  final int id;
  final String guid;
  final String name;
  final Map<int, Vertex> vertices = {};
  final Map<int, Edge> edges = {};
  final Map<int, Face> faces = {};
  final List<Instance> instances = [];
  final bool alwaysFacesCamera;
  final bool isImage;

  Definition({
    this.id = 0,
    this.guid = '',
    this.name = '',
    this.alwaysFacesCamera = false,
    this.isImage = false,
  });
}

/// Complete parsed representation of a SketchUp file, mirroring the shape
/// of Python's public SkpFile.parse() result.
class SkpModel {
  String version = 'unknown';

  /// Component/group definitions keyed by their numeric TLV entity ID. The
  /// implicit root definition (Python's "ROOT" dict entry, which has no
  /// numeric ID) is exposed separately via [root] instead of living in this
  /// map.
  final Map<int, Definition> definitions = {};

  /// The implicit top-level model definition: its instances are the
  /// entities placed directly in the model (not inside any
  /// component/group). Corresponds to Python's defsDict['ROOT'].
  Definition root = Definition(name: 'ROOT_MODEL');

  final List<Layer> layers = [];
  final List<Material> materials = [];

  /// Join table for Face.materialId / Instance.materialId: TLV material ID
  /// -> Material. Several IDs may alias the same Material instance.
  final Map<int, Material> materialsById = {};

  final List<Style> styles = [];
}
