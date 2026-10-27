import 'dart:io';
import 'dart:math' as math;
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

  /// SketchUp's own `Edge#curve` grouping: the id of the Curve this edge
  /// belongs to, or null when it belongs to none. Edges sharing a [curveId]
  /// were drawn as one continuous multi-segment line or arc; if the curve is
  /// an arc, [Definition.arcs] has it under the same id.
  final int? curveId;
  Edge({
    required this.id,
    required this.v1Id,
    required this.v2Id,
    this.soft = false,
    this.smooth = false,
    this.hidden = false,
    this.curveId,
  });
}

/// The analytic arc behind a curve: SketchUp's own `ArcCurve`.
///
/// A curve SketchUp drew as an arc or a circle is a run of straight edges
/// ([Edge.curveId]) and also this exact description. The arc is the set of
/// points `center + cos(t) * xAxis + sin(t) * yAxis` for `t` from [startAngle]
/// to [endAngle]. For a circle [xAxis] and [yAxis] are perpendicular and both
/// of length [radius]; an arc scaled non-uniformly keeps its arc identity and
/// stores an affine image of a circle, where they are not (then [radius] is
/// the length of [xAxis] only). Arcs and circles that bound a face are arcs
/// too. Lengths are in the model's units (inches), in the definition's local
/// space, like [Vertex.x].
class ArcCurve {
  final (double, double, double) center;

  /// Unit normal of the arc's plane.
  final (double, double, double) normal;

  /// Vector from the centre to the arc's start (length = radius).
  final (double, double, double) xAxis;

  /// `normal x xAxis`.
  final (double, double, double) yAxis;

  /// Start parameter in radians.
  final double startAngle;

  /// End parameter in radians. A full circle has `endAngle == startAngle` in
  /// a classic (pre-2021) file and `startAngle + 2 pi` in a 2021+ file;
  /// [isFullCircle] covers both.
  final double endAngle;
  ArcCurve({
    required this.center,
    required this.normal,
    required this.xAxis,
    required this.yAxis,
    required this.startAngle,
    required this.endAngle,
  });

  double get radius =>
      math.sqrt(xAxis.$1 * xAxis.$1 + xAxis.$2 * xAxis.$2 + xAxis.$3 * xAxis.$3);

  bool get isFullCircle {
    final twoPi = 2 * math.pi;
    final sweep = (endAngle - startAngle) % twoPi;
    return math.min(sweep, twoPi - sweep) < 1e-9;
  }
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

class SectionPlane {
  final List<double> plane;
  final String name;
  final String label;
  final bool hidden;

  const SectionPlane({
    this.plane = const [0.0, 0.0, 1.0, 0.0],
    this.name = '',
    this.label = '',
    this.hidden = false,
  });
}

class TextEntity {
  final String text;
  final bool hidden;

  const TextEntity({
    this.text = '',
    this.hidden = false,
  });
}

/// A linear dimension (SketchUp's Dimension tool).
///
/// The legacy (pre-2021) reader recovers only [text]/[hidden]. The VFF
/// reader (2021+) recovers the full geometry - see [SkpModel.dimensions]
/// for the model-level, world-space list.
class Dimension {
  /// The displayed text. Empty when the dimension shows its auto-computed
  /// measured value (the caller formats `|b - a|`).
  final String text;
  final bool hidden;

  /// First measured point (x, y, z) in inches (world space), or null when
  /// only the text was recovered.
  final (double, double, double)? a;

  /// Second measured point.
  final (double, double, double)? b;

  /// Offset distance (inches) - how far the dimension line sits from the
  /// a-b segment, along the in-plane perpendicular.
  final double offset;

  /// The dimension plane's x-axis, or null.
  final (double, double, double)? planeX;

  /// The dimension plane's normal, or null.
  final (double, double, double)? normal;

  const Dimension({
    this.text = '',
    this.hidden = false,
    this.a,
    this.b,
    this.offset = 0.0,
    this.planeX,
    this.normal,
  });
}

/// A construction/guide line (SketchUp's Construction Line tool). Read from
/// both legacy (pre-2021) and VFF (2021+) files.
///
/// Stored internally (and here, unchanged) as a point + normalized
/// direction + two signed distance parameters along that direction
/// marking where the visible segment starts/ends - the same shape
/// `Sketchup::ConstructionLine`'s own `start`/`end`/`direction`
/// properties expose. A parameter magnitude of `1e30` means unbounded
/// in that direction (SketchUp draws this as an infinite guide line
/// through [point]) - [start]/[end] come back null in that case,
/// matching the real API returning `nil`.
class ConstructionLine {
  /// A point on the line, in inches (world space) - matches the bounded
  /// case's own [start], or the anchor point given for an infinite line.
  final (double, double, double) point;

  /// The line's normalized direction vector.
  final (double, double, double) direction;

  /// The bounded segment's start point, or null if unbounded in this
  /// direction.
  final (double, double, double)? start;

  /// The bounded segment's end point, or null if unbounded.
  final (double, double, double)? end;

  const ConstructionLine({
    this.point = (0.0, 0.0, 0.0),
    this.direction = (1.0, 0.0, 0.0),
    this.start,
    this.end,
  });
}

/// A construction/guide point (SketchUp's Construction Point tool).
/// Both legacy and VFF (2021+) files are supported.
class ConstructionPoint {
  /// The point's position, in inches (world space).
  final (double, double, double) position;

  const ConstructionPoint({this.position = (0.0, 0.0, 0.0)});
}

/// A saved scene (SketchUp's "Scenes" tabs; "pages" in the SDK).
class Page {
  /// Scene name as shown on its tab.
  final String name;

  /// Camera position (x, y, z) in inches, or null.
  final (double, double, double)? eye;

  /// Point the camera looks at, in inches.
  final (double, double, double)? target;

  /// Camera up vector.
  final (double, double, double)? up;

  /// Field of view in degrees (SketchUp default 35).
  final double fov;

  /// True when the scene uses parallel (orthographic) projection; [fov]
  /// still holds the stored perspective angle.
  final bool parallel;

  /// Visible height in inches when [parallel].
  final double orthoHeight;

  /// Names of the layers this scene hides.
  final List<String> hiddenLayers;

  const Page({
    this.name = '',
    this.eye,
    this.target,
    this.up,
    this.fov = 35.0,
    this.parallel = false,
    this.orthoHeight = 0.0,
    this.hiddenLayers = const [],
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
  final List<SectionPlane> sectionPlanes = [];
  final List<TextEntity> texts = [];
  final List<Dimension> dimensions = [];
  final List<ConstructionLine> constructionLines = [];
  final List<ConstructionPoint> constructionPoints = [];

  /// The analytic arc of every curve that is one, keyed by curve id - the
  /// [Edge.curveId] of the edges that form it. A curve that is not an arc (a
  /// freehand or polygon curve) has no entry.
  final Map<int, ArcCurve> arcs = {};
  final bool alwaysFacesCamera;
  final bool shadowsFaceSun;
  final bool isImage;

  /// True when SketchUp made this definition for a group rather than a
  /// component (what its own API reports as `SUComponentType_Group`). Groups
  /// and component instances are both [Instance] placements of a
  /// [Definition]; this is the only thing that tells them apart. It comes from
  /// the file itself - the definition kind in a 2021+ file, a flag in the
  /// definition record in an older one - not from how often the definition is
  /// placed.
  final bool isGroup;

  Definition({
    this.id = 0,
    this.guid = '',
    this.name = '',
    this.alwaysFacesCamera = false,
    this.shadowsFaceSun = false,
    this.isImage = false,
    this.isGroup = false,
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

  /// The file's saved scenes (VFF files; classic pre-2021 files import
  /// with none).
  final List<Page> pages = [];

  /// Model-level linear dimensions with world-space endpoints (VFF
  /// files). Legacy files surface text-only dimensions per definition
  /// instead ([Definition.dimensions]).
  final List<Dimension> dimensions = [];

  final List<Material> materials = [];

  /// Join table for Face.materialId / Instance.materialId: TLV material ID
  /// -> Material. Several IDs may alias the same Material instance.
  final Map<int, Material> materialsById = {};

  final List<Style> styles = [];
}
