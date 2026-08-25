/// A pure Dart implementation of the OpenSKP parser: extracts geometry,
/// metadata, layers, and materials from SketchUp (.skp) binary files.
library openskp;

export 'src/model.dart';
export 'src/parser.dart' show SkpFile;
export 'src/scene.dart' show Scene, InstanceNode, MeshMetadata, GlbPrimitive;
export 'src/instanced_scene.dart'
    show InstancedScene, InstancedNode, InstancedMeshResource, LocalPrimitive, SceneBounds;
export 'src/glb.dart' show toGlb, exportGlb;
export 'src/instanced_glb.dart' show toInstancedGlb, exportInstancedGlb;
export 'src/json_export.dart' show toJson;
export 'src/obj_export.dart' show toObj, toMtl, exportObj;
export 'src/stl_export.dart' show toStlAscii, toStlBinary, exportStl;
export 'src/ply_export.dart' show toPlyAscii, toPlyBinary, exportPly;
export 'src/dxf_export.dart' show toDxf, exportDxf, metresToInches;
export 'src/ifc_export.dart' show toIfc, exportIfc, generateIfcGuid, classifyElement;
export 'src/errors.dart' show SkpParseException;
export 'src/observability.dart' show SkpLogLevel, ParseProgress, ParseOptions;
export 'src/create.dart'
    show
        create,
        SkpBuilder,
        ComponentDefinitionBuilder,
        GeometryHost,
        SkpWriteError,
        Point3,
        Matrix3x3,
        Rotation,
        CurveParams,
        rotationMatrix3x3;
export 'src/edit.dart' show openExisting, OpenExistingResult;
