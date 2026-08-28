using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;

namespace OpenSkp
{
    /// <summary>
    /// Load an existing legacy-format .skp file and rebuild it as a new,
    /// independent <see cref="SkpBuilder"/>.
    ///
    /// <see cref="Create"/>-produced files only ever get built by splicing
    /// new geometry into the bundled blank scaffold (see Create.cs's own
    /// header comment) - there is no way to append to or patch an arbitrary
    /// existing file's bytes in place, because real SketchUp itself doesn't
    /// do that either: it fully re-serializes the whole document on every
    /// save, so there is no stable "original bytes + appended bytes"
    /// structure to target for a file this project didn't create.
    ///
    /// This file takes the other viable approach instead: fully parse the
    /// existing file with this project's own reader (Legacy.cs, already
    /// comprehensive), then *replay* everything it understood back through
    /// the writer's own public API (materials, layers, every component
    /// definition, every face/instance) to produce a brand-new file - not a
    /// byte-patched copy of the original, but a freshly-built one with
    /// equivalent content, to which the caller can add more geometry before
    /// saving. Direct port of openskp/edit.py.
    ///
    /// <b>Adding more geometry after the fact.</b> The returned builder can
    /// take more AddFace/AddCircle/AddInstance/etc. calls, and every
    /// material/layer the source had is already reachable via
    /// builder.MaterialsByName/builder.LayersByName (no separate lookup
    /// needed - <see cref="OpenExistingResult.Definitions"/> maps each
    /// component definition's name to its builder, for placing more
    /// instances of something the source already defined). What the
    /// returned builder can no longer do is register a genuinely NEW
    /// material, layer, or component definition/group - Create.cs's own
    /// file-format ordering requirement (materials/layers/definitions must
    /// all be finalized before any geometry is written) is already
    /// satisfied by the time replay finishes writing the source's own
    /// root-level geometry (which happens for any source file with
    /// root-level content - in practice, almost always), so all four of
    /// AddMaterial/AddLayer/AddComponentDefinition/AddGroup throw on the
    /// returned builder. Build anything new into a separate
    /// SkpCreate.NewFile() call instead.
    ///
    /// <b>Scope and known fidelity gaps</b> (this reads long because every
    /// gap here is a genuine, deliberately-scoped limitation, not an
    /// oversight - see each corresponding source file's own comments for
    /// why):
    ///
    /// <list type="bullet">
    /// <item>Only a legacy-format (SketchUp 2013-2020) source file is
    /// accepted - Create.cs never writes any other format, so a modern
    /// VFF (2021+) source can't be faithfully round-tripped through it.</item>
    /// <item>Per-edge hidden/soft/smooth flags are applied per-FACE, not
    /// per-edge (an "any edge in this boundary has the flag"
    /// approximation) - AddFace can only set these uniformly for every
    /// edge it newly declares in one call, the same limitation any user
    /// of that API has.</item>
    /// <item>A positioned texture is replayed via 3 sample-point
    /// correspondences fitted to an affine map (see AddFace's own
    /// frontUv/backUv) - exact at those 3 points, but a genuinely
    /// projective (4-pin/distorted) source mapping won't interpolate
    /// identically between them. A PROJECTED (draped) texture has no
    /// equivalent at all and falls back to the default projection.</item>
    /// <item>A colorized (tinted) material variant is replayed as its
    /// plain source texture, losing the tint.</item>
    /// <item>Per-face material/layer painting: only a face's front/back
    /// MATERIAL is replayed - this project's reader doesn't expose a
    /// per-face layer assignment at all (only instances carry an explicit
    /// layer).</item>
    /// <item>Every placed thing (originally a group or a component
    /// instance alike) is replayed as a plain component instance -
    /// structurally simpler, and visually identical, but no longer shows
    /// as a "Group" in SketchUp's Outliner afterward.</item>
    /// <item>Section planes, text entities, and dimensions aren't carried
    /// over at all - the writer has no support for any of these entity
    /// types.</item>
    /// <item>A circle/arc/polyline's original CArcCurve/CCurve grouping is
    /// lost - this project's reader doesn't preserve that grouping in its
    /// public Face/Edge model, so a round-tripped circle becomes an
    /// ordinary straight-edged face.</item>
    /// <item>Definition-level and face-level custom attributes aren't
    /// reproduced - the reader's public model doesn't expose either (only
    /// an instance's own Properties are).</item>
    /// </list>
    /// </summary>
    public static class SkpEdit
    {
        /// <summary>Parse <paramref name="path"/> (a legacy-format .skp
        /// file) and rebuild it as a new SkpBuilder, replaying materials,
        /// layers, every component definition, and all root-level
        /// geometry/instances. See this file's own header comment for the
        /// exact, itemized scope and fidelity gaps.</summary>
        public static OpenExistingResult OpenExisting(string path)
        {
            byte[] head;
            using (var f = File.OpenRead(path))
            {
                var buf = new byte[0x200];
                int total = 0, n;
                while (total < buf.Length && (n = f.Read(buf, total, buf.Length - total)) > 0) total += n;
                head = total == buf.Length ? buf : buf.Take(total).ToArray();
            }
            if (!Legacy.IsLegacy(head))
            {
                throw new SkpWriteException(
                    $"'{path}' is not a legacy-format (SketchUp 2013-2020) .skp file - "
                    + "OpenSkp.Create only ever writes that format, so only a legacy-format "
                    + "source file can be rebuilt through it (see this file's own header comment "
                    + "for why an arbitrary existing file can't simply be patched)");
            }

            var model = SkpFile.Open(path);
            var warnings = new List<string>();
            var builder = SkpCreate.NewFile();

            var materialSlots = ReplayMaterials(builder, model, warnings);
            var layerSlots = new Dictionary<string, int>();
            foreach (var layer in model.Layers)
            {
                layerSlots[layer.Name] = builder.AddLayer(
                    layer.Name, (layer.ColorR, layer.ColorG, layer.ColorB), hidden: layer.Hidden);
            }

            var defBuilders = new Dictionary<long, ComponentDefinitionBuilder>();
            foreach (var defId in DefinitionOrder(model))
            {
                var defn = model.Definitions[defId];
                string context = $"definition '{(string.IsNullOrEmpty(defn.Name) ? defId.ToString() : defn.Name)}'";
                if (!DefinitionHasContent(defn, defBuilders))
                {
                    warnings.Add($"{context}: skipped (no replayable geometry)");
                    continue;
                }
                // defn.Name unconditionally, not `IsNullOrEmpty(...) ? ... :
                // defn.Name` - an explicit empty string is a real, valid
                // definition name. SketchUp Groups are internally just
                // unnamed component definitions (unlike Components, which
                // SketchUp auto-names), so an empty name is common in real
                // files - same reasoning as ReplayInstance's own name
                // handling below.
                var db = builder.AddComponentDefinition(defn.Name);
                ReplayBody(db, defn, model, materialSlots, layerSlots, warnings, context, defBuilders);
                db.Dispose();
                defBuilders[defId] = db;
            }

            ReplayBody(builder, model.Root, model, materialSlots, layerSlots, warnings, "root", defBuilders);

            var definitionsByName = new Dictionary<string, ComponentDefinitionBuilder>();
            foreach (var kv in defBuilders)
            {
                string name = model.Definitions[kv.Key].Name;
                if (!string.IsNullOrEmpty(name)) definitionsByName[name] = kv.Value;
            }
            return new OpenExistingResult(builder, warnings, definitionsByName);
        }

        private static Dictionary<long, int> ReplayMaterials(SkpBuilder builder, SkpModel model, List<string> warnings)
        {
            var byRef = new Dictionary<Material, int>(MaterialRefComparer.Instance);
            foreach (var mat in model.Materials)
            {
                int slot;
                if (mat.Texture != null && mat.Texture.Data != null && mat.Texture.Data.Length > 0)
                {
                    string suffix = ".png";
                    if (!string.IsNullOrEmpty(mat.Texture.Filename))
                    {
                        string ext = Path.GetExtension(mat.Texture.Filename);
                        if (!string.IsNullOrEmpty(ext)) suffix = ext;
                    }
                    string tmpPath = Path.Combine(Path.GetTempPath(), Guid.NewGuid() + suffix);
                    try
                    {
                        File.WriteAllBytes(tmpPath, mat.Texture.Data);
                        // appliedHeight: 1.0 - every textured face is now
                        // replayed with an explicit FrontUv/BackUv (see
                        // ReplayUv), whose pins already bake in the
                        // SOURCE's real tile size via ComputeFaceUv - the
                        // material's own stored applied height must be a
                        // no-op divisor (1.0) or the read-side UV formula
                        // divides by it a second time. This happens to
                        // match AddTextureMaterial's own default too, but
                        // is kept explicit here since it's a hard
                        // requirement of this call site specifically, not
                        // just a safe default.
                        slot = builder.AddTextureMaterial(mat.Name, tmpPath, appliedHeight: 1.0);
                    }
                    finally
                    {
                        try { File.Delete(tmpPath); } catch (IOException) { /* best-effort cleanup */ }
                    }
                    if (mat.Colorized)
                    {
                        warnings.Add($"material '{mat.Name}': colorized tint not reproduced (base texture only)");
                    }
                }
                else
                {
                    if (mat.Texture != null)
                    {
                        warnings.Add($"material '{mat.Name}': texture image data missing - replayed as solid color");
                    }
                    slot = builder.AddMaterial(mat.Name, mat.Color);
                }
                byRef[mat] = slot;
            }
            return ByRefToById(byRef, model);
        }

        // Materials/faces reference a Material instance via numeric ids
        // (Face.MaterialId -> SkpModel.MaterialsById), and several ids can
        // alias the same Material object - mirroring Python's `id(mat)`
        // dict keying, resolve through the Material reference itself
        // rather than through any one id.
        private static Dictionary<long, int> ByRefToById(Dictionary<Material, int> byRef, SkpModel model)
        {
            var result = new Dictionary<long, int>();
            foreach (var kv in model.MaterialsById)
            {
                if (byRef.TryGetValue(kv.Value, out int slot))
                {
                    result[kv.Key] = slot;
                }
            }
            return result;
        }

        private static int? MaterialSlot(long? materialId, Dictionary<long, int> slots)
        {
            if (materialId == null) return null;
            return slots.TryGetValue(materialId.Value, out int slot) ? slot : (int?)null;
        }

        /// <summary>Reference-identity equality for Material - Model.cs's
        /// Material class doesn't override Equals/GetHashCode, so the
        /// default ObjectEqualityComparer would already do the same thing
        /// via RuntimeHelpers.GetHashCode/ReferenceEquals in practice, but
        /// this makes that reliance explicit rather than incidental.</summary>
        private sealed class MaterialRefComparer : IEqualityComparer<Material>
        {
            public static readonly MaterialRefComparer Instance = new MaterialRefComparer();
            public bool Equals(Material? x, Material? y) => ReferenceEquals(x, y);
            public int GetHashCode(Material obj) => System.Runtime.CompilerServices.RuntimeHelpers.GetHashCode(obj);
        }

        /// <summary>Topological order (dependencies before dependents) so
        /// a definition nesting instances of other definitions is only
        /// replayed after those are already built - the same ordering
        /// constraint ComponentDefinitionBuilder.AddInstance documents.</summary>
        private static List<long> DefinitionOrder(SkpModel model)
        {
            var visited = new HashSet<long>();
            var temp = new HashSet<long>();
            var order = new List<long>();

            void Visit(long defId)
            {
                if (visited.Contains(defId)) return;
                if (temp.Contains(defId))
                {
                    throw new SkpWriteException($"circular component-definition reference involving definition {defId}");
                }
                temp.Add(defId);
                if (model.Definitions.TryGetValue(defId, out var defn))
                {
                    foreach (var inst in defn.Instances)
                    {
                        if (inst.RefIdx.HasValue && model.Definitions.ContainsKey(inst.RefIdx.Value))
                        {
                            Visit(inst.RefIdx.Value);
                        }
                    }
                }
                temp.Remove(defId);
                visited.Add(defId);
                order.Add(defId);
            }

            foreach (var defId in model.Definitions.Keys) Visit(defId);
            return order;
        }

        private static Dictionary<long, (long? V1, long? V2)> EdgeMap(Definition defn)
        {
            var result = new Dictionary<long, (long?, long?)>();
            foreach (var kv in defn.Edges) result[kv.Key] = (kv.Value.V1Id, kv.Value.V2Id);
            return result;
        }

        private static bool DefinitionHasContent(Definition defn, Dictionary<long, ComponentDefinitionBuilder> defBuilders)
        {
            var edges = EdgeMap(defn);
            foreach (var face in defn.Faces.Values)
            {
                if (face.Loops.Count == 0) continue;
                if (FaceGroups.ReconstructLoopVertices(face.Loops[0], edges).Count >= 3) return true;
            }
            foreach (var inst in defn.Instances)
            {
                if (inst.RefIdx.HasValue && defBuilders.ContainsKey(inst.RefIdx.Value)) return true;
            }
            return false;
        }

        /// <summary>Replay one definition's (or the root's) own faces and
        /// instances onto target - an SkpBuilder for the root, or a
        /// ComponentDefinitionBuilder for a nested definition; both expose
        /// the same AddFace/AddInstance shape via IGeometryTarget.
        /// defBuilders resolves instance references - by the time any
        /// definition is opened (topological order, see DefinitionOrder)
        /// every OTHER definition its own instances could reference is
        /// already in it.</summary>
        private static void ReplayBody(
            IGeometryTarget target, Definition defn, SkpModel model,
            Dictionary<long, int> materialSlots, Dictionary<string, int> layerSlots,
            List<string> warnings, string context, Dictionary<long, ComponentDefinitionBuilder> defBuilders)
        {
            var edges = EdgeMap(defn);
            foreach (var face in defn.Faces.Values)
            {
                ReplayFace(target, face, defn, edges, model, materialSlots, warnings, context);
            }
            foreach (var inst in defn.Instances)
            {
                ReplayInstance(target, inst, defBuilders, materialSlots, layerSlots, warnings, context);
            }
        }

        private static void ReplayFace(
            IGeometryTarget target, Face face, Definition defn, Dictionary<long, (long?, long?)> edges,
            SkpModel model, Dictionary<long, int> materialSlots, List<string> warnings, string context)
        {
            if (face.Loops.Count < 1)
            {
                warnings.Add($"{context}: face {face.Id} has no loops - skipped");
                return;
            }
            var vertIds = FaceGroups.ReconstructLoopVertices(face.Loops[0], edges);
            if (vertIds.Count < 3)
            {
                warnings.Add($"{context}: face {face.Id} has fewer than 3 usable points - skipped");
                return;
            }
            var points = vertIds.Select(v => (defn.Vertices[v].X, defn.Vertices[v].Y, defn.Vertices[v].Z)).ToList();

            var holes = new List<IReadOnlyList<(double X, double Y, double Z)>>();
            for (int i = 1; i < face.Loops.Count; i++)
            {
                var holeVertIds = FaceGroups.ReconstructLoopVertices(face.Loops[i], edges);
                if (holeVertIds.Count < 3)
                {
                    warnings.Add($"{context}: face {face.Id} has a hole with fewer than 3 usable points - skipped");
                    return;
                }
                holes.Add(holeVertIds.Select(v => (defn.Vertices[v].X, defn.Vertices[v].Y, defn.Vertices[v].Z)).ToList());
            }

            bool hiddenEdges = false, softEdges = false, smoothEdges = false;
            foreach (var (eid, _) in face.Loops[0])
            {
                if (defn.Edges.TryGetValue(eid, out var e))
                {
                    hiddenEdges |= e.Hidden;
                    softEdges |= e.Soft;
                    smoothEdges |= e.Smooth;
                }
            }

            int? material = MaterialSlot(face.MaterialId, materialSlots);
            int? backMaterial = MaterialSlot(face.BackMaterialId, materialSlots);

            var frontUv = ReplayUv(face.MaterialId, face.UvTransform, face.UvProjected, points, face.Normal, model, warnings, context, "front");
            var backUv = ReplayUv(face.BackMaterialId, face.UvTransformBack, face.UvProjectedBack, points, face.Normal, model, warnings, context, "back");

            try
            {
                target.AddFace(
                    points,
                    material: material, backMaterial: backMaterial,
                    hidden: face.Hidden, softEdges: softEdges, smoothEdges: smoothEdges, hiddenEdges: hiddenEdges,
                    frontUv: frontUv, backUv: backUv,
                    holes: holes);
            }
            catch (SkpWriteException exc)
            {
                warnings.Add($"{context}: face {face.Id} skipped ({exc.Message})");
            }
        }

        /// front_uv/back_uv need exactly 3 correspondences whose (u, v)
        /// values are NOT collinear (an affine fit is impossible
        /// otherwise) - real faces can have a "flat" vertex (three
        /// consecutive vertices genuinely collinear in 3D), which
        /// points.Take(3) alone isn't guaranteed to avoid. Search for the
        /// first non-collinear triple - an affine map preserves
        /// collinearity, so a non-collinear triple in 3D is also
        /// non-collinear in (u, v).
        private static (double X, double Y, double Z)[]? NonCollinearTriple(
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

        // Explicit FrontUv/BackUv for EVERY textured face, not just
        // already-positioned ones - ComputeFaceUv already computes the
        // correct final UV for the untouched-projection case too
        // (uvTransform is null) using the source's real tile size, so
        // this reproduces a default-projected face's true rendering
        // exactly without needing the material's own applied
        // width/height to match (which, post-replay, is intentionally
        // 1.0 - see ReplayMaterials).
        private static List<UvCorrespondence>? ReplayUv(
            long? materialId, double[]? uvTransform, bool projected,
            IReadOnlyList<(double X, double Y, double Z)> points, (double Nx, double Ny, double Nz)? normal,
            SkpModel model, List<string> warnings, string context, string side)
        {
            if (!materialId.HasValue) return null;
            Material? mat = model.MaterialsById.TryGetValue(materialId.Value, out var m) ? m : null;
            if (mat?.Texture == null) return null; // solid color - no UV to replay
            if (projected)
            {
                warnings.Add($"{context}: {side} texture is projected/draped - falls back to default projection");
                return null;
            }
            if (normal == null) return null;
            double tileW = mat.Texture.Width > 1e-9 ? mat.Texture.Width : 1.0;
            double tileH = mat.Texture.Height > 1e-9 ? mat.Texture.Height : 1.0;
            var (xr, yr) = FaceGroups.FaceUvBasis((normal.Value.Nx, normal.Value.Ny, normal.Value.Nz));
            var sample = NonCollinearTriple(points);
            if (sample == null) return null; // every vertex triple collinear - a sliver face
            var pairs = new List<UvCorrespondence>();
            foreach (var p in sample)
            {
                var (u, v) = FaceGroups.ComputeFaceUv(p, xr, yr, uvTransform, tileW, tileH);
                pairs.Add(new UvCorrespondence(p, (u, v)));
            }
            return pairs;
        }

        private static void ReplayInstance(
            IGeometryTarget target, Instance inst, Dictionary<long, ComponentDefinitionBuilder> defBuilders,
            Dictionary<long, int> materialSlots, Dictionary<string, int> layerSlots,
            List<string> warnings, string context)
        {
            ComponentDefinitionBuilder? defBuilder = inst.RefIdx.HasValue && defBuilders.TryGetValue(inst.RefIdx.Value, out var db) ? db : null;
            if (defBuilder == null)
            {
                warnings.Add($"{context}: instance '{inst.Name}' references unavailable definition - skipped");
                return;
            }
            double[]? matrix3x3 = inst.Matrix.Count >= 9 ? inst.Matrix.Take(9).ToArray() : null;
            (double, double, double) translation = inst.Matrix.Count >= 12
                ? (inst.Matrix[9], inst.Matrix[10], inst.Matrix[11])
                : (0.0, 0.0, 0.0);
            int? material = MaterialSlot(inst.MaterialId, materialSlots);
            int? layer = !string.IsNullOrEmpty(inst.Layer) && layerSlots.TryGetValue(inst.Layer, out var l) ? l : (int?)null;
            IReadOnlyDictionary<string, object>? attributes = inst.Properties != null && inst.Properties.Count > 0
                ? inst.Properties.ToDictionary(kv => kv.Key, kv => (object)kv.Value)
                : null;
            try
            {
                // name: inst.Name, not `string.IsNullOrEmpty(...) ? null :
                // ...` - an explicit empty string is a real, valid
                // instance name (SketchUp itself stores it that way when a
                // placement was never renamed, showing the definition's
                // name in the Outliner only as a UI-level fallback);
                // AddInstance's own `name ?? definition.Name` fallback
                // only triggers on null, so passing "" through must not
                // be converted to null here or that name gets silently
                // replaced with the definition's own name instead.
                target.AddInstance(
                    defBuilder, name: inst.Name,
                    translation: translation, matrix3x3: matrix3x3,
                    material: material, layer: layer, hidden: inst.Hidden,
                    attributes: attributes, attributeDictName: "dynamic_attributes");
            }
            catch (SkpWriteException exc)
            {
                warnings.Add($"{context}: instance '{inst.Name}' skipped ({exc.Message})");
            }
        }
    }

    /// <summary>Result of <see cref="SkpEdit.OpenExisting"/>: the rebuilt
    /// builder (ready for more geometry before Save/ToBytes), any
    /// fidelity-gap warnings collected during replay, and a name -&gt;
    /// builder lookup for every replayed component definition.</summary>
    public sealed class OpenExistingResult
    {
        public SkpBuilder Builder { get; }
        public IReadOnlyList<string> Warnings { get; }
        public IReadOnlyDictionary<string, ComponentDefinitionBuilder> Definitions { get; }

        public OpenExistingResult(SkpBuilder builder, IReadOnlyList<string> warnings, IReadOnlyDictionary<string, ComponentDefinitionBuilder> definitions)
        {
            Builder = builder;
            Warnings = warnings;
            Definitions = definitions;
        }

        public void Deconstruct(out SkpBuilder builder, out IReadOnlyList<string> warnings, out IReadOnlyDictionary<string, ComponentDefinitionBuilder> definitions)
        {
            builder = Builder;
            warnings = Warnings;
            definitions = Definitions;
        }
    }
}
