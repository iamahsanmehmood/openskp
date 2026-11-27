using System.Collections.Generic;
using System.Linq;

namespace OpenSkp
{
    /// <summary>openskp's canonical JSON export schema, shared with the
    /// Python (<c>to_dict</c>) and TypeScript (<c>toJSON</c>) ports. Every
    /// definition (root included) carries full vertex/edge/face arrays and
    /// counts, plus its raw (pre-bake) <c>instances</c> list; pass a
    /// <see cref="Scene"/> (the result of
    /// <see cref="SkpFile.BuildScene(string, SkpParseOptions)"/>) to also
    /// include the resolved, world-space <c>scene_hierarchy</c>/
    /// <c>mesh_index</c> - omit it for a lighter summary covering just the
    /// raw model.
    ///
    /// The raw per-definition <c>instances</c> list is intentionally flat,
    /// with no <c>layer</c>/<c>properties</c>/<c>children</c> keys: those
    /// are declared on <see cref="Instance"/> but never assigned during
    /// parsing here (same as Python's and Dart's <c>Instance</c>; only C++
    /// actually populates them - see item 17), so encoding them would
    /// present known-dead data as meaningful. The resolved, genuinely
    /// nested tree (with correct layer/properties/attribute_dictionaries)
    /// is available via <c>scene_hierarchy</c> instead.</summary>
    public static class JsonExport
    {
        private static Dictionary<string, object?> InstanceToJson(Instance inst) => new Dictionary<string, object?>
        {
            ["name"] = inst.Name,
            ["ref_idx"] = inst.RefIdx,
            ["guid"] = inst.Guid,
            ["matrix"] = inst.Matrix.Cast<object>().ToList(),
        };

        private static Dictionary<string, object?> VertexToJson(Vertex v) => new Dictionary<string, object?>
        {
            ["id"] = v.Id,
            ["x"] = v.X,
            ["y"] = v.Y,
            ["z"] = v.Z,
        };

        private static Dictionary<string, object?> EdgeToJson(Edge e) => new Dictionary<string, object?>
        {
            ["id"] = e.Id,
            ["v1_id"] = e.V1Id,
            ["v2_id"] = e.V2Id,
        };

        private static Dictionary<string, object?> FaceToJson(Face f)
        {
            var loops = f.Loops.Select(loop => (object)loop.Select(ce => (object)new Dictionary<string, object?>
            {
                ["edge_id"] = ce.EdgeId,
                ["orientation"] = ce.Orientation,
            }).ToList()).ToList();

            object? normal = f.Normal.HasValue
                ? new List<object> { f.Normal.Value.Nx, f.Normal.Value.Ny, f.Normal.Value.Nz }
                : null;

            return new Dictionary<string, object?>
            {
                ["id"] = f.Id,
                ["loops"] = loops,
                ["normal"] = normal,
            };
        }

        private static Dictionary<string, object?> DefinitionToJson(Definition defn) => new Dictionary<string, object?>
        {
            ["id"] = defn.Id,
            ["guid"] = defn.Guid,
            ["name"] = defn.Name,
            ["vertex_count"] = defn.Vertices.Count,
            ["edge_count"] = defn.Edges.Count,
            ["face_count"] = defn.Faces.Count,
            ["vertices"] = defn.Vertices.Values.Select(v => (object)VertexToJson(v)).ToList(),
            ["edges"] = defn.Edges.Values.Select(e => (object)EdgeToJson(e)).ToList(),
            ["faces"] = defn.Faces.Values.Select(f => (object)FaceToJson(f)).ToList(),
            ["instances"] = defn.Instances.Select(i => (object)InstanceToJson(i)).ToList(),
        };

        private static Dictionary<string, object?> AttributeDictionariesToJson(
            Dictionary<string, Dictionary<string, string>>? dicts)
        {
            var result = new Dictionary<string, object?>();
            if (dicts == null) return result;
            foreach (var kv in dicts)
            {
                result[kv.Key] = kv.Value != null
                    ? kv.Value.ToDictionary(e => e.Key, e => (object?)e.Value)
                    : new Dictionary<string, object?>();
            }
            return result;
        }

        private static Dictionary<string, object?> InstanceNodeToJson(InstanceNode node) => new Dictionary<string, object?>
        {
            ["name"] = node.Name,
            ["definition_name"] = node.DefinitionName,
            ["layer"] = node.Layer,
            ["position_mm"] = new List<object> { node.PositionMm.X, node.PositionMm.Y, node.PositionMm.Z },
            ["properties"] = node.Properties.ToDictionary(kv => kv.Key, kv => (object)kv.Value),
            ["attribute_dictionaries"] = AttributeDictionariesToJson(node.AttributeDictionaries),
            ["children"] = node.Children.Select(c => (object)InstanceNodeToJson(c)).ToList(),
        };

        private static Dictionary<string, object?> MeshMetadataToJson(MeshMetadata m) => new Dictionary<string, object?>
        {
            ["name"] = m.Name,
            ["definition_name"] = m.DefinitionName,
            ["layer"] = m.Layer,
            ["position_mm"] = new List<object> { m.PositionMm.X, m.PositionMm.Y, m.PositionMm.Z },
            ["properties"] = m.Properties.ToDictionary(kv => kv.Key, kv => (object)kv.Value),
            ["attribute_dictionaries"] = AttributeDictionariesToJson(m.AttributeDictionaries),
            ["path"] = m.Path,
        };

        /// <summary>Convert a parsed <see cref="SkpModel"/> (and optionally a
        /// baked <see cref="Scene"/>) to a JSON-serializable dictionary.
        /// Pass the result to <see cref="MiniJson.Serialize"/> to get a JSON
        /// string.</summary>
        public static Dictionary<string, object?> ToDict(SkpModel model, Scene? scene = null)
        {
            var definitionsObj = new Dictionary<string, object?>();
            foreach (var kv in model.Definitions)
            {
                definitionsObj[kv.Key.ToString()] = DefinitionToJson(kv.Value);
            }

            var layersList = model.Layers.Select(l => (object)new Dictionary<string, object?>
            {
                ["name"] = l.Name,
                ["color"] = new Dictionary<string, object?> { ["r"] = l.ColorR, ["g"] = l.ColorG, ["b"] = l.ColorB },
                ["hidden"] = l.Hidden,
            }).ToList();

            var materialsList = model.Materials.Select(m => (object)new Dictionary<string, object?>
            {
                ["name"] = m.Name,
                ["color"] = new Dictionary<string, object?>
                {
                    ["r"] = m.Color.R,
                    ["g"] = m.Color.G,
                    ["b"] = m.Color.B,
                    ["a"] = m.Color.A,
                },
                ["transparency"] = m.Transparency,
            }).ToList();

            var meshIndexObj = new Dictionary<string, object?>();
            if (scene != null)
            {
                foreach (var kv in scene.MeshIndex)
                {
                    meshIndexObj[kv.Key] = MeshMetadataToJson(kv.Value);
                }
            }

            return new Dictionary<string, object?>
            {
                ["format_version"] = "1.0",
                ["sketchup_version"] = model.Version,
                ["units"] = model.Units,
                ["total_definitions"] = model.Definitions.Count,
                ["total_layers"] = model.Layers.Count,
                ["total_meshes"] = scene != null ? scene.MeshIndex.Count : 0,
                ["root"] = DefinitionToJson(model.Root),
                ["definitions"] = definitionsObj,
                ["layers"] = layersList,
                ["materials"] = materialsList,
                ["mesh_index"] = meshIndexObj,
                ["scene_hierarchy"] = scene != null ? InstanceNodeToJson(scene.SceneHierarchy) : null,
            };
        }
    }
}
