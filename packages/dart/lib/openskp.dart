/// A pure Dart implementation of the OpenSKP parser: extracts geometry,
/// metadata, layers, and materials from SketchUp (.skp) binary files.
library openskp;

export 'src/model.dart';
export 'src/parser.dart' show SkpFile;
export 'src/scene.dart' show Scene, InstanceNode, MeshMetadata, GlbPrimitive;
export 'src/glb.dart' show toGlb, exportGlb;
export 'src/json_export.dart' show toJson;
export 'src/obj_export.dart' show toObj, exportObj;
export 'src/errors.dart' show SkpParseException;
export 'src/observability.dart' show SkpLogLevel, ParseProgress, ParseOptions;
