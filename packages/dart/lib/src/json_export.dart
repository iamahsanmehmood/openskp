import 'model.dart';
import 'scene.dart';

/// openskp's canonical JSON export schema, shared with the Python
/// (`to_dict`) and TypeScript (`toJSON`) ports. Every definition (root
/// included) carries full vertex/edge/face arrays and counts, plus its
/// raw (pre-bake) `instances` list; pass [scene] (the result of
/// `SkpFile.buildScene()`) to also include the resolved, world-space
/// `scene_hierarchy`/`mesh_index` - omit it for a lighter summary
/// covering just the raw model.
///
/// The raw per-definition `instances` list is intentionally flat, with
/// no `layer`/`properties`/`children` keys: those are declared on
/// [Instance] but never assigned during parsing here (same as Python's
/// and .NET's `Instance`; only C++ actually populates them - see item
/// 17), so encoding them would present known-dead data as meaningful.
/// The resolved, genuinely nested tree (with correct layer/properties/
/// attribute_dictionaries) is available via `scene_hierarchy` instead.
Map<String, dynamic> _instanceToJson(Instance inst) => {
      'name': inst.name,
      'ref_idx': inst.refIdx,
      'guid': inst.guid,
      'matrix': inst.matrix,
    };

Map<String, dynamic> _vertexToJson(Vertex v) => {'id': v.id, 'x': v.x, 'y': v.y, 'z': v.z};

Map<String, dynamic> _edgeToJson(Edge e) => {'id': e.id, 'v1_id': e.v1Id, 'v2_id': e.v2Id};

Map<String, dynamic> _faceToJson(Face f) => {
      'id': f.id,
      'loops': f.loops
          .map((loop) => loop.map((ce) => {'edge_id': ce.$1, 'orientation': ce.$2}).toList())
          .toList(),
      'normal': f.normal == null ? null : [f.normal!.$1, f.normal!.$2, f.normal!.$3],
    };

Map<String, dynamic> _definitionToJson(Definition defn) => {
      'id': defn.id,
      'guid': defn.guid,
      'name': defn.name,
      'vertex_count': defn.vertices.length,
      'edge_count': defn.edges.length,
      'face_count': defn.faces.length,
      'vertices': defn.vertices.values.map(_vertexToJson).toList(),
      'edges': defn.edges.values.map(_edgeToJson).toList(),
      'faces': defn.faces.values.map(_faceToJson).toList(),
      'instances': defn.instances.map(_instanceToJson).toList(),
    };

Map<String, dynamic> _attributeDictionariesToJson(
        Map<String, Map<String, String>> dicts) =>
    {
      for (final e in dicts.entries) e.key: Map<String, String>.from(e.value),
    };

Map<String, dynamic> _instanceNodeToJson(InstanceNode node) => {
      'name': node.name,
      'definition_name': node.definitionName,
      'layer': node.layer,
      'position_mm': [node.positionMm.$1, node.positionMm.$2, node.positionMm.$3],
      'properties': Map<String, String>.from(node.properties),
      'attribute_dictionaries':
          _attributeDictionariesToJson(node.attributeDictionaries),
      'children': node.children.map(_instanceNodeToJson).toList(),
    };

Map<String, dynamic> _meshMetadataToJson(MeshMetadata m) => {
      'name': m.name,
      'definition_name': m.definitionName,
      'layer': m.layer,
      'position_mm': [m.positionMm.$1, m.positionMm.$2, m.positionMm.$3],
      'properties': Map<String, String>.from(m.properties),
      'attribute_dictionaries':
          _attributeDictionariesToJson(m.attributeDictionaries),
      'path': m.path,
    };

/// Convert a parsed [SkpModel] (and optionally a baked [Scene]) to a
/// JSON-serializable [Map]. See this file's top-level docs for the
/// schema this matches exactly (Python's `to_dict`, TypeScript's
/// `toJSON`).
Map<String, dynamic> toJson(SkpModel model, [Scene? scene]) {
  final definitionsObj = <String, dynamic>{};
  for (final entry in model.definitions.entries) {
    definitionsObj[entry.key.toString()] = _definitionToJson(entry.value);
  }

  return {
    'format_version': '1.0',
    'sketchup_version': model.version,
    'units': model.units,
    'total_definitions': model.definitions.length,
    'total_layers': model.layers.length,
    'total_meshes': scene != null ? scene.meshIndex.length : 0,
    'root': _definitionToJson(model.root),
    'definitions': definitionsObj,
    'layers': model.layers
        .map((l) => {
              'name': l.name,
              'color': {'r': l.colorR, 'g': l.colorG, 'b': l.colorB},
              'hidden': l.hidden,
            })
        .toList(),
    'materials': model.materials
        .map((m) => {
              'name': m.name,
              'color': {'r': m.color.$1, 'g': m.color.$2, 'b': m.color.$3, 'a': m.color.$4},
              'transparency': m.transparency,
            })
        .toList(),
    'mesh_index': scene != null
        ? {for (final e in scene.meshIndex.entries) e.key: _meshMetadataToJson(e.value)}
        : <String, dynamic>{},
    'scene_hierarchy': scene != null ? _instanceNodeToJson(scene.sceneHierarchy) : null,
  };
}
