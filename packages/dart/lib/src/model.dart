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

  /// The texture is PROJECTED (e.g. the Add Location terrain drape): its
  /// UVs run in the projection plane's frame, not the face frame.
  final bool uvProjected;

  /// Same for the face's back side.
  final bool uvProjectedBack;

  /// Whether the face is hidden (SketchUp's "Hide" on this specific face,
  /// not a layer/tag visibility toggle).
  final bool hidden;

  Face({
    required this.id,
    this.loops = const [],
    this.normal,
    this.materialId,
    this.backMaterialId,
    this.uvTransform,
    this.uvTransformBack,
    this.uvProjected = false,
    this.uvProjectedBack = false,
    this.hidden = false,
  });
}

class Layer {
  final String name;
  final int colorR, colorG, colorB;

  /// Whether the layer's visibility is switched off. Only populated for
  /// legacy (pre-2021 MFC) files, where the byte is read directly from the
  /// layer record - modern (VFF) files derive layers from
  /// Layer_<name>-prefixed materials, which carry no visibility data, so
  /// this is always false there.
  final bool hidden;

  Layer(
      {required this.name,
      this.colorR = 200,
      this.colorG = 200,
      this.colorB = 200,
      this.hidden = false});
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

  /// This instance's own explicit layer override, or "" when it has
  /// none. An instance without an explicit override inherits its
  /// *placement's* layer, which can only be resolved once the scene
  /// graph is flattened - see SkpFile.buildScene()'s InstanceNode.layer
  /// for that resolved value.
  final String layer;

  /// Arbitrary key/value dynamic attributes attached directly to this
  /// instance (SketchUp's Dynamic Components).
  final Map<String, String> properties;
  final int? materialId;

  /// Whether the instance itself is hidden (SketchUp's "Hide" on this
  /// specific component/group placement, not a layer/tag visibility
  /// toggle).
  final bool hidden;

  Instance({
    this.name = '',
    this.refIdx,
    this.guid = '',
    this.matrix = const [],
    this.layer = '',
    this.properties = const {},
    this.materialId,
    this.hidden = false,
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
  final bool shadowsFaceSun;
  final bool isImage;

  Definition({
    this.id = 0,
    this.guid = '',
    this.name = '',
    this.alwaysFacesCamera = false,
    this.shadowsFaceSun = false,
    this.isImage = false,
  });
}

/// Complete parsed representation of a SketchUp file, mirroring the shape
/// of Python's public SkpFile.parse() result.
class SkpModel {
  String version = 'unknown';

  /// The model's unit-system string (e.g. "Millimeter"), read from
  /// meta/meta.dat in modern (VFF) files. Null for legacy (pre-2021 MFC)
  /// files, which carry no equivalent container, or when the tag isn't
  /// found.
  String? units;

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
