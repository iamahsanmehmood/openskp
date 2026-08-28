using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text;

namespace OpenSkp
{
    /// <summary>Generates C# source that rebuilds a parsed <see cref="SkpModel"/>
    /// from scratch via <see cref="SkpCreate.NewFile"/> - a faithful,
    /// human-readable, re-runnable transcript of the model as writer API
    /// calls, not a serialized dump.
    ///
    /// Handles: materials (solid and textured, including default-projection
    /// and explicitly-pinned UVs), layers, component/group definitions
    /// (built in dependency order), faces (front/back material, holes),
    /// instances (transform, instance-level paint, instance-level name).
    ///
    /// Found and fixed via diffing a real, large file (jeff.skp: 2713
    /// definitions, 113643 faces) against its own regenerated output - the
    /// TypeScript port this mirrors (toTypeScriptCode) found that an
    /// earlier prototype silently dropped instance-level paint (95% of
    /// that file's instances) and every instance's own name entirely, and
    /// never emitted textured materials at all. Building this module
    /// reused Edit.cs's own already-fixed replay helpers (ReplayUv,
    /// NonCollinearTriple) directly, so both share the exact same, already
    /// real-fixture-tested UV/hole logic.
    ///
    /// Only reproduces geometry reachable by walking faces (Definition.
    /// Faces) - a real file's standalone/construction edges and curves
    /// that don't bound any face are NOT reproduced (same limitation as
    /// the TypeScript port - see its own doc for the concrete numbers this
    /// was measured against). This does not affect materials, textures,
    /// instance paint, or any face/surface geometry - only invisible
    /// construction/reference lines.
    ///
    /// Also not yet handled (matching this project's established
    /// disclosure pattern for known gaps): colorized material tint,
    /// per-face hidden/soft/smooth edge flags, section planes, text/
    /// dimension entities. A model using any of these round-trips its
    /// geometry/materials/instances correctly; those specific facts are
    /// silently dropped.
    ///
    /// A face a few millionths of an inch off its own fitted plane
    /// (common in real files) is auto-triangulated rather than rejected,
    /// mirroring real SketchUp's own tolerance.</summary>
    public static class Codegen
    {
        public static string ToCSharpCode(SkpModel model)
        {
            var lines = new List<string>();
            void Push(string s) => lines.Add(s);

            string Round(double n)
            {
                double r = Math.Round(n, 4);
                if (r == 0.0) r = 0.0;
                return r.ToString("0.0###############", CultureInfo.InvariantCulture);
            }
            string PointStr((double X, double Y, double Z) p) => $"({Round(p.X)}, {Round(p.Y)}, {Round(p.Z)})";
            string Matrix3x3Str(IReadOnlyList<double> m9) => $"new double[] {{ {string.Join(", ", m9.Select(Round))} }}";

            var materialsById = model.MaterialsById;
            var matVar = new Dictionary<string, string>();
            var texturedMats = new HashSet<string>();

            Push("using System;");
            Push("using OpenSkp;");
            Push("");
            Push("public static class GeneratedModel");
            Push("{");
            Push("    public static byte[] Build()");
            Push("    {");
            Push("        var builder = SkpCreate.NewFile();");
            Push("");
            Push($"        // --- Materials ({model.Materials.Count}) ---");
            for (int i = 0; i < model.Materials.Count; i++)
            {
                var mat = model.Materials[i];
                string varName = $"mat{i}";
                matVar[mat.Name] = varName;
                if (mat.Texture != null && mat.Texture.Data != null && mat.Texture.Data.Length > 0)
                {
                    texturedMats.Add(mat.Name);
                    string b64 = Convert.ToBase64String(mat.Texture.Data);
                    string ext = System.IO.Path.GetExtension(mat.Texture.Filename ?? "");
                    if (string.IsNullOrEmpty(ext)) ext = ".png";
                    // appliedHeight: 1.0 - see AddTextureMaterial's own note
                    // on why the library default (an internal sentinel)
                    // corrupts ANY face using this material. Every face
                    // using a textured material is written below with
                    // explicit frontUv/backUv, never left to default
                    // projection, so the original applied width/height
                    // never needs to be reproduced.
                    Push($"        var _texPath{i} = System.IO.Path.Combine(System.IO.Path.GetTempPath(), Guid.NewGuid() + \"{ext}\");");
                    Push($"        System.IO.File.WriteAllBytes(_texPath{i}, Convert.FromBase64String(\"{b64}\"));");
                    Push("        int " + varName + $" = builder.AddTextureMaterial({CsString(mat.Name)}, _texPath{i}, appliedHeight: 1.0);");
                    Push($"        System.IO.File.Delete(_texPath{i});");
                }
                else
                {
                    var c = mat.Color;
                    Push($"        int {varName} = builder.AddMaterial({CsString(mat.Name)}, ({c.R}, {c.G}, {c.B}, {c.A}));");
                }
            }

            Push("");
            Push($"        // --- Layers ({model.Layers.Count}) ---");
            var layerVar = new Dictionary<string, string>();
            for (int i = 0; i < model.Layers.Count; i++)
            {
                var layer = model.Layers[i];
                string varName = $"layer{i}";
                layerVar[layer.Name] = varName;
                Push(
                    $"        int {varName} = builder.AddLayer({CsString(layer.Name)}, color: ({layer.ColorR}, {layer.ColorG}, {layer.ColorB}, 255), hidden: {(layer.Hidden ? "true" : "false")});"
                );
            }

            string? UvTripleStr(
                IReadOnlyList<(double X, double Y, double Z)> points,
                (double Nx, double Ny, double Nz)? normal,
                double[]? uvTransform, double tileW, double tileH)
            {
                if (normal == null || points.Count < 3) return null;
                var sample = NonCollinearTripleLocal(points);
                if (sample == null) return null;
                var (xr, yr) = FaceGroups.FaceUvBasis((normal.Value.Nx, normal.Value.Ny, normal.Value.Nz));
                var parts = sample.Select(p =>
                {
                    var (u, v) = FaceGroups.ComputeFaceUv(p, xr, yr, uvTransform, tileW, tileH);
                    return $"new UvCorrespondence({PointStr(p)}, ({Round(u)}, {Round(v)}))";
                });
                return $"new[] {{ {string.Join(", ", parts)} }}";
            }

            (string Str, bool HasUv) MaterialOptsStr(Face face, IReadOnlyList<(double X, double Y, double Z)> points)
            {
                var parts = new List<string>();
                bool hasUv = false;
                if (face.MaterialId.HasValue && materialsById.TryGetValue(face.MaterialId.Value, out var fm))
                {
                    parts.Add($"material: {matVar[fm.Name]}");
                    if (texturedMats.Contains(fm.Name))
                    {
                        var triple = UvTripleStr(points, face.Normal, face.UvTransform, fm.Texture!.Width > 1e-9 ? fm.Texture.Width : 1.0, fm.Texture.Height > 1e-9 ? fm.Texture.Height : 1.0);
                        if (triple != null) { parts.Add($"frontUv: {triple}"); hasUv = true; }
                    }
                }
                if (face.BackMaterialId.HasValue && materialsById.TryGetValue(face.BackMaterialId.Value, out var bm))
                {
                    parts.Add($"backMaterial: {matVar[bm.Name]}");
                    if (texturedMats.Contains(bm.Name))
                    {
                        var triple = UvTripleStr(points, face.Normal, face.UvTransformBack, bm.Texture!.Width > 1e-9 ? bm.Texture.Width : 1.0, bm.Texture.Height > 1e-9 ? bm.Texture.Height : 1.0);
                        if (triple != null) { parts.Add($"backUv: {triple}"); hasUv = true; }
                    }
                }
                return (string.Join(", ", parts), hasUv);
            }

            int facesSkippedDegenerate = 0;

            void EmitFaces(Definition defn, string targetVar, string indent)
            {
                var edges = defn.Edges.ToDictionary(kv => kv.Key, kv => ((long?)kv.Value.V1Id, (long?)kv.Value.V2Id));
                foreach (var face in defn.Faces.Values)
                {
                    if (face.Loops.Count == 0) continue;
                    var vertIds = FaceGroups.ReconstructLoopVertices(face.Loops[0], edges);
                    if (vertIds.Count < 3) { facesSkippedDegenerate++; continue; }
                    var points = vertIds.Where(v => defn.Vertices.ContainsKey(v)).Select(v => defn.Vertices[v])
                        .Select(v => (v.X, v.Y, v.Z)).ToList();
                    if (points.Count < 3) { facesSkippedDegenerate++; continue; }

                    var holes = new List<List<(double X, double Y, double Z)>>();
                    for (int hi = 1; hi < face.Loops.Count; hi++)
                    {
                        var holeVertIds = FaceGroups.ReconstructLoopVertices(face.Loops[hi], edges);
                        if (holeVertIds.Count < 3) continue;
                        var holePts = holeVertIds.Where(v => defn.Vertices.ContainsKey(v)).Select(v => defn.Vertices[v])
                            .Select(v => (v.X, v.Y, v.Z)).ToList();
                        if (holePts.Count >= 3) holes.Add(holePts);
                    }

                    var (matStr, hasUv) = MaterialOptsStr(face, points);
                    string pointsStr = string.Join(", ", points.Select(PointStr));
                    var extra = new List<string>();
                    if (!hasUv) extra.Add("autoTriangulate: true");
                    if (holes.Count > 0)
                    {
                        string holesStr = string.Join(", ", holes.Select(h => $"new (double,double,double)[] {{ {string.Join(", ", h.Select(PointStr))} }}"));
                        extra.Add($"holes: new[] {{ {holesStr} }}");
                    }
                    var callParts = new List<string>();
                    if (!string.IsNullOrEmpty(matStr)) callParts.Add(matStr);
                    callParts.AddRange(extra);
                    string callOpts = callParts.Count > 0 ? ", " + string.Join(", ", callParts) : "";
                    Push($"{indent}{targetVar}.AddFace(new (double,double,double)[] {{ {pointsStr} }}{callOpts});");
                }
            }

            List<string> InstanceOptsStr(Instance inst, string defName)
            {
                var parts = new List<string>();
                if (inst.MaterialId.HasValue && materialsById.TryGetValue(inst.MaterialId.Value, out var im))
                {
                    parts.Add($"material: {matVar[im.Name]}");
                }
                // Explicit even when inst.Name is empty: AddInstance
                // defaults an OMITTED name to the definition's own name
                // (name ?? definition.Name), so a source instance with a
                // genuinely empty name would otherwise come out with that
                // name baked in for real.
                if (inst.Name != defName) parts.Add($"name: {CsString(inst.Name)}");
                return parts;
            }

            var defVar = new Dictionary<long, string>();
            int defCounter = 0;

            string? GetOrBuildDef(long defId, HashSet<long> visiting)
            {
                if (defVar.TryGetValue(defId, out var existing)) return existing;
                if (visiting.Contains(defId)) return null;
                visiting.Add(defId);

                if (!model.Definitions.TryGetValue(defId, out var defn)) return null;
                if (defn.Faces.Count == 0 && defn.Instances.Count == 0) return null;

                foreach (var inst in defn.Instances)
                {
                    if (inst.RefIdx.HasValue) GetOrBuildDef(inst.RefIdx.Value, visiting);
                }

                string varName = $"def{defCounter++}";
                string defName = !string.IsNullOrEmpty(defn.Name) ? defn.Name : $"Def{defId}";
                defVar[defId] = varName;

                Push("");
                Push($"        // {CsComment(defn.Name)} - {defn.Faces.Count} faces, {defn.Instances.Count} nested instances");
                Push($"        var {varName} = builder.AddComponentDefinition({CsString(defName)});");
                Push($"        using ({varName})");
                Push("        {");
                EmitFaces(defn, varName, "            ");
                foreach (var inst in defn.Instances)
                {
                    if (!inst.RefIdx.HasValue) continue;
                    if (!defVar.TryGetValue(inst.RefIdx.Value, out var childVar)) continue;
                    var m9 = inst.Matrix.Count >= 9 ? inst.Matrix.Take(9).ToList() : new List<double> { 1, 0, 0, 0, 1, 0, 0, 0, 1 };
                    var t = inst.Matrix.Count >= 12 ? (inst.Matrix[9], inst.Matrix[10], inst.Matrix[11]) : (0.0, 0.0, 0.0);
                    var extra = InstanceOptsStr(inst, defName);
                    var opts = new List<string> { $"translation: {PointStr(t)}", $"matrix3x3: {Matrix3x3Str(m9)}" };
                    opts.AddRange(extra);
                    Push($"            {varName}.AddInstance({childVar}, {string.Join(", ", opts)});");
                }
                Push("        }");
                return varName;
            }

            foreach (var defId in model.Definitions.Keys.ToList())
            {
                GetOrBuildDef(defId, new HashSet<long>());
            }

            Push("");
            Push($"        // --- Root instances ({model.Root.Instances.Count}) ---");
            foreach (var inst in model.Root.Instances)
            {
                if (!inst.RefIdx.HasValue) continue;
                if (!defVar.TryGetValue(inst.RefIdx.Value, out var childVar)) continue;
                string childDefName = model.Definitions.TryGetValue(inst.RefIdx.Value, out var cd) ? cd.Name : "";
                var m9 = inst.Matrix.Count >= 9 ? inst.Matrix.Take(9).ToList() : new List<double> { 1, 0, 0, 0, 1, 0, 0, 0, 1 };
                var t = inst.Matrix.Count >= 12 ? (inst.Matrix[9], inst.Matrix[10], inst.Matrix[11]) : (0.0, 0.0, 0.0);
                var extra = InstanceOptsStr(inst, childDefName);
                var opts = new List<string> { $"translation: {PointStr(t)}", $"matrix3x3: {Matrix3x3Str(m9)}" };
                opts.AddRange(extra);
                Push($"        builder.AddInstance({childVar}, {string.Join(", ", opts)});");
            }
            EmitFaces(model.Root, "builder", "        ");

            Push("");
            Push("        return builder.ToBytes();");
            Push("    }");
            Push("}");

            if (facesSkippedDegenerate > 0)
            {
                lines.Insert(0, $"// {facesSkippedDegenerate} degenerate face(s) (fewer than 3 resolvable vertices) were skipped during generation.");
            }

            return string.Join("\n", lines) + "\n";
        }

        /// front_uv/back_uv need exactly 3 correspondences whose (u, v)
        /// values are NOT collinear - the same search Edit.cs's own
        /// NonCollinearTriple does, duplicated here (private to that
        /// file) rather than made internal purely to avoid widening that
        /// file's own surface for a helper this is the only other user of.
        private static (double X, double Y, double Z)[]? NonCollinearTripleLocal(
            IReadOnlyList<(double X, double Y, double Z)> points)
        {
            for (int i = 0; i < points.Count; i++)
            {
                for (int j = i + 1; j < points.Count; j++)
                {
                    for (int k = j + 1; k < points.Count; k++)
                    {
                        var a = points[i]; var b = points[j]; var c = points[k];
                        var e1 = (X: b.X - a.X, Y: b.Y - a.Y, Z: b.Z - a.Z);
                        var e2 = (X: c.X - a.X, Y: c.Y - a.Y, Z: c.Z - a.Z);
                        double cx = e1.Y * e2.Z - e1.Z * e2.Y;
                        double cy = e1.Z * e2.X - e1.X * e2.Z;
                        double cz = e1.X * e2.Y - e1.Y * e2.X;
                        if (cx * cx + cy * cy + cz * cz > 1e-9)
                        {
                            return new[] { a, b, c };
                        }
                    }
                }
            }
            return null;
        }

        private static string CsString(string s)
        {
            var sb = new StringBuilder();
            sb.Append('"');
            foreach (char c in s)
            {
                switch (c)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    default: sb.Append(c); break;
                }
            }
            sb.Append('"');
            return sb.ToString();
        }

        private static string CsComment(string s) => s.Replace("*/", "* /");
    }
}
