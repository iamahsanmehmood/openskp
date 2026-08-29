using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;

namespace OpenSkp
{
    /// <summary>
    /// Export utilities for serializing OpenSKP scenes to ISO-10303-21 STEP ASCII IFC4 format.
    /// </summary>
    public static class IfcExport
    {
        /// <summary>
        /// 1 metre = 39.37007874015748 inches (SketchUp native unit).
        /// </summary>
        public const double MetresToInches = 39.37007874015748;

        private const string IfcBase64 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$";

        /// <summary>
        /// Generates a standard 22-character IFC base64 compressed GUID.
        /// </summary>
        public static string GenerateIfcGuid()
        {
            var rand = new Random();
            var sb = new StringBuilder(22);
            for (int i = 0; i < 22; i++)
            {
                sb.Append(IfcBase64[rand.Next(64)]);
            }
            return sb.ToString();
        }

        private static string SanitizeName(string name)
        {
            if (string.IsNullOrWhiteSpace(name)) return "Unnamed";
            string clean = name.Replace("'", "''").Replace("\\", "\\\\").Trim();
            return string.IsNullOrEmpty(clean) ? "Unnamed" : clean;
        }

        private static (string StepType, string ClassName)? ClassifyByKeyword(string name)
        {
            if (string.IsNullOrEmpty(name)) return null;
            string l = name.ToLowerInvariant();
            if (l.Contains("wall")) return ("IFCWALL", "IfcWall");
            if (l.Contains("door")) return ("IFCDOOR", "IfcDoor");
            if (l.Contains("window")) return ("IFCWINDOW", "IfcWindow");
            if (l.Contains("slab") || l.Contains("floor")) return ("IFCSLAB", "IfcSlab");
            if (l.Contains("column") || l.Contains("pillar")) return ("IFCCOLUMN", "IfcColumn");
            if (l.Contains("beam") || l.Contains("joist")) return ("IFCBEAM", "IfcBeam");
            if (l.Contains("roof")) return ("IFCROOF", "IfcRoof");
            return null;
        }

        /// <summary>
        /// Classifies geometry/component name into an IFC entity type.
        /// Tries <paramref name="geomName"/> first, then falls back to
        /// <paramref name="layerName"/> (many SketchUp-for-BIM workflows
        /// organize by tag/layer - "Walls", "Doors" - even when individual
        /// components are never renamed away from SketchUp's own defaults
        /// like "Component#109415"), then falls back to a generic, untyped
        /// element if neither matches.
        /// </summary>
        public static (string StepType, string ClassName) ClassifyElement(string geomName, string layerName = "")
        {
            var byName = ClassifyByKeyword(geomName);
            if (byName.HasValue) return byName.Value;
            if (!string.IsNullOrEmpty(layerName))
            {
                var byLayer = ClassifyByKeyword(layerName);
                if (byLayer.HasValue) return byLayer.Value;
            }
            return ("IFCBUILDINGELEMENTPROXY", "IfcBuildingElementProxy");
        }

        private static (double R, double G, double B, double A) GetPrimRgb(Scene scene, int primMatIdx)
        {
            double r = 0.8, g = 0.8, b = 0.8, a = 1.0;
            if (scene.GltfMaterials != null && primMatIdx >= 0 && primMatIdx < scene.GltfMaterials.Count)
            {
                var mat = scene.GltfMaterials[primMatIdx] as Dictionary<string, object>;
                if (mat != null && mat.TryGetValue("pbrMetallicRoughness", out var pbrObj) && pbrObj is Dictionary<string, object> pbr)
                {
                    if (pbr.TryGetValue("baseColorFactor", out var colorVecObj) && colorVecObj is List<object> colorVec && colorVec.Count >= 3)
                    {
                        r = Math.Max(0.0, Math.Min(1.0, Convert.ToDouble(colorVec[0], CultureInfo.InvariantCulture)));
                        g = Math.Max(0.0, Math.Min(1.0, Convert.ToDouble(colorVec[1], CultureInfo.InvariantCulture)));
                        b = Math.Max(0.0, Math.Min(1.0, Convert.ToDouble(colorVec[2], CultureInfo.InvariantCulture)));
                        if (colorVec.Count >= 4)
                        {
                            a = Math.Max(0.0, Math.Min(1.0, Convert.ToDouble(colorVec[3], CultureInfo.InvariantCulture)));
                        }
                    }
                }
            }
            return (r, g, b, a);
        }

        /// <summary>
        /// Serializes a baked <see cref="Scene"/> to ISO-10303-21 STEP ASCII IFC4 format.
        /// </summary>
        /// <param name="classifier">Optional override for <see cref="ClassifyElement"/> -
        /// use this to supply your own naming convention or metadata-driven typing
        /// instead of the built-in keyword/layer heuristic.</param>
        public static string ToIfc(Scene scene, double scale = MetresToInches, string schema = "IFC4", Func<string, string, (string StepType, string ClassName)>? classifier = null)
        {
            if (scene == null || scene.GlbPrimitives == null)
            {
                throw new ArgumentNullException(nameof(scene), "ToIfc requires a valid Scene instance");
            }

            var classify = classifier ?? ClassifyElement;

            string schemaStr = string.IsNullOrWhiteSpace(schema) ? "IFC4" : schema.ToUpperInvariant();
            string nowIso = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ss", CultureInfo.InvariantCulture);
            long timestampEpoch = DateTimeOffset.UtcNow.ToUnixTimeSeconds();

            var lines = new List<string>
            {
                "ISO-10303-21;",
                "HEADER;",
                "FILE_DESCRIPTION(('ViewDefinition [CoordinationView]'),'2;1');",
                $"FILE_NAME('model.ifc','{nowIso}',('OpenSKP Author'),('OpenSKP Organization'),'OpenSKP IFC Exporter','OpenSKP','');",
                $"FILE_SCHEMA(('{schemaStr}'));",
                "ENDSEC;",
                "DATA;"
            };

            int entityId = 1;
            int NextId() => entityId++;

            int personId = NextId();
            lines.Add($"#{personId}=IFCPERSON($,$,'OpenSKP User',$,$,$,$,$);");

            int orgId = NextId();
            lines.Add($"#{orgId}=IFCORGANIZATION($,'OpenSKP',$,$,$);");

            int personOrgId = NextId();
            lines.Add($"#{personOrgId}=IFCPERSONANDORGANIZATION(#{personId},#{orgId},$);");

            int appId = NextId();
            lines.Add($"#{appId}=IFCAPPLICATION(#{orgId},'0.3.1','OpenSKP Exporter','OpenSKP');");

            int ownerHistId = NextId();
            lines.Add($"#{ownerHistId}=IFCOWNERHISTORY(#{personOrgId},#{appId},$,.READWRITE.,$,$,$,{timestampEpoch});");

            int lengthUnitId = NextId();
            lines.Add($"#{lengthUnitId}=IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.);");

            int angleUnitId = NextId();
            lines.Add($"#{angleUnitId}=IFCSIUNIT(*,.PLANEANGLEUNIT.,$,.RADIAN.);");

            int solidUnitId = NextId();
            lines.Add($"#{solidUnitId}=IFCSIUNIT(*,.STERADIANUNIT.,$,.STERADIAN.);");

            int unitAssignId = NextId();
            lines.Add($"#{unitAssignId}=IFCUNITASSIGNMENT((#{lengthUnitId},#{angleUnitId},#{solidUnitId}));");

            int ptZeroId = NextId();
            lines.Add($"#{ptZeroId}=IFCCARTESIANPOINT((0.0,0.0,0.0));");

            int axisPlacementId = NextId();
            lines.Add($"#{axisPlacementId}=IFCAXIS2PLACEMENT3D(#{ptZeroId},$,$);");

            int geomCtxId = NextId();
            lines.Add($"#{geomCtxId}=IFCGEOMETRICREPRESENTATIONCONTEXT($,'Model',3,1.0E-5,#{axisPlacementId},$);");

            int projId = NextId();
            lines.Add($"#{projId}=IFCPROJECT('{GenerateIfcGuid()}',#{ownerHistId},'OpenSKP Project',$,$,$,$,(#{geomCtxId}),#{unitAssignId});");

            int sitePlacementId = NextId();
            lines.Add($"#{sitePlacementId}=IFCLOCALPLACEMENT($,#{axisPlacementId});");

            int siteId = NextId();
            lines.Add($"#{siteId}=IFCSITE('{GenerateIfcGuid()}',#{ownerHistId},'Site',$,$,#{sitePlacementId},$,$,.ELEMENT.,$,$,$,$,$);");

            int bldgPlacementId = NextId();
            lines.Add($"#{bldgPlacementId}=IFCLOCALPLACEMENT(#{sitePlacementId},#{axisPlacementId});");

            int bldgId = NextId();
            lines.Add($"#{bldgId}=IFCBUILDING('{GenerateIfcGuid()}',#{ownerHistId},'Building',$,$,#{bldgPlacementId},$,$,.ELEMENT.,$,$,$);");

            int storeyPlacementId = NextId();
            lines.Add($"#{storeyPlacementId}=IFCLOCALPLACEMENT(#{bldgPlacementId},#{axisPlacementId});");

            int storeyId = NextId();
            lines.Add($"#{storeyId}=IFCBUILDINGSTOREY('{GenerateIfcGuid()}',#{ownerHistId},'Level 0',$,$,#{storeyPlacementId},$,$,.ELEMENT.,0.0);");

            lines.Add($"#{NextId()}=IFCRELAGGREGATES('{GenerateIfcGuid()}',#{ownerHistId},$,$,#{projId},(#{siteId}));");
            lines.Add($"#{NextId()}=IFCRELAGGREGATES('{GenerateIfcGuid()}',#{ownerHistId},$,$,#{siteId},(#{bldgId}));");
            lines.Add($"#{NextId()}=IFCRELAGGREGATES('{GenerateIfcGuid()}',#{ownerHistId},$,$,#{bldgId},(#{storeyId}));");

            var productIds = new List<int>();
            var layerItems = new Dictionary<string, List<int>>();
            var matStyleCache = new Dictionary<string, int>();

            foreach (var prim in scene.GlbPrimitives)
            {
                int triCount = prim.Indices.Length / 3;
                int vCount = prim.Positions.Length / 3;
                if (triCount == 0 || vCount == 0) continue;

                string geomName = SanitizeName(prim.GeomName);
                string layerName = "Layer0";
                MeshMetadata? meta = null;
                if (scene.MeshIndex != null && scene.MeshIndex.TryGetValue(prim.GeomName, out meta) && meta != null)
                {
                    if (!string.IsNullOrEmpty(meta.Layer))
                    {
                        layerName = SanitizeName(meta.Layer);
                    }
                }

                var (stepType, _) = classify(geomName, layerName);

                var ptCoords = new List<string>();
                for (int i = 0; i < vCount; i++)
                {
                    string vx = (prim.Positions[i * 3] * scale).ToString("F6", CultureInfo.InvariantCulture);
                    string vy = (prim.Positions[i * 3 + 1] * scale).ToString("F6", CultureInfo.InvariantCulture);
                    string vz = (prim.Positions[i * 3 + 2] * scale).ToString("F6", CultureInfo.InvariantCulture);
                    ptCoords.Add($"({vx},{vy},{vz})");
                }

                int ptListId = NextId();
                lines.Add($"#{ptListId}=IFCCARTESIANPOINTLIST3D(({string.Join(",", ptCoords)}));");

                var faceIndices = new List<string>();
                for (int i = 0; i < triCount; i++)
                {
                    uint idx0 = prim.Indices[i * 3] + 1;
                    uint idx1 = prim.Indices[i * 3 + 1] + 1;
                    uint idx2 = prim.Indices[i * 3 + 2] + 1;
                    faceIndices.Add($"({idx0.ToString(CultureInfo.InvariantCulture)},{idx1.ToString(CultureInfo.InvariantCulture)},{idx2.ToString(CultureInfo.InvariantCulture)})");
                }

                int faceSetId = NextId();
                lines.Add($"#{faceSetId}=IFCTRIANGULATEDFACESET(#{ptListId},$,.TRUE.,({string.Join(",", faceIndices)}),$);");

                if (!layerItems.ContainsKey(layerName))
                {
                    layerItems[layerName] = new List<int>();
                }
                layerItems[layerName].Add(faceSetId);

                var (r, g, b, a) = GetPrimRgb(scene, prim.MaterialIndex);
                string rgbaKey = $"{r.ToString("F4", CultureInfo.InvariantCulture)},{g.ToString("F4", CultureInfo.InvariantCulture)},{b.ToString("F4", CultureInfo.InvariantCulture)},{a.ToString("F4", CultureInfo.InvariantCulture)}";
                int styleAssignId;

                if (!matStyleCache.TryGetValue(rgbaKey, out styleAssignId))
                {
                    int colId = NextId();
                    lines.Add($"#{colId}=IFCCOLOURRGB($,{r.ToString("F4", CultureInfo.InvariantCulture)},{g.ToString("F4", CultureInfo.InvariantCulture)},{b.ToString("F4", CultureInfo.InvariantCulture)});");

                    string transparency = (1.0 - a).ToString("F4", CultureInfo.InvariantCulture);
                    int renderingId = NextId();
                    lines.Add($"#{renderingId}=IFCSURFACESTYLERENDERING(#{colId},{transparency},$,$,$,$,$,$,.FLAT.);");

                    int styleId = NextId();
                    lines.Add($"#{styleId}=IFCSURFACESTYLE('{geomName}_Material',.BOTH.,(#{renderingId}));");

                    styleAssignId = NextId();
                    lines.Add($"#{styleAssignId}=IFCPRESENTATIONSTYLEASSIGNMENT((#{styleId}));");
                    matStyleCache[rgbaKey] = styleAssignId;
                }

                int styledItemId = NextId();
                lines.Add($"#{styledItemId}=IFCSTYLEDITEM(#{faceSetId},(#{styleAssignId}),$);");

                int shapeRepId = NextId();
                lines.Add($"#{shapeRepId}=IFCSHAPEREPRESENTATION(#{geomCtxId},'Body','Tessellation',(#{faceSetId}));");

                int prodShapeId = NextId();
                lines.Add($"#{prodShapeId}=IFCPRODUCTDEFINITIONSHAPE($,$,(#{shapeRepId}));");

                int prodPlacementId = NextId();
                lines.Add($"#{prodPlacementId}=IFCLOCALPLACEMENT(#{storeyPlacementId},#{axisPlacementId});");

                int productId = NextId();
                string prodGuid = GenerateIfcGuid();
                if (stepType == "IFCBUILDINGELEMENTPROXY")
                {
                    lines.Add($"#{productId}={stepType}('{prodGuid}',#{ownerHistId},'{geomName}',$,$,#{prodPlacementId},#{prodShapeId},$,.NOTDEFINED.);");
                }
                else
                {
                    lines.Add($"#{productId}={stepType}('{prodGuid}',#{ownerHistId},'{geomName}',$,$,#{prodPlacementId},#{prodShapeId},$,$);");
                }
                productIds.Add(productId);

                if (meta != null && meta.Properties != null && meta.Properties.Count > 0)
                {
                    var propValIds = new List<int>();
                    foreach (var kvp in meta.Properties)
                    {
                        string cleanK = SanitizeName(kvp.Key);
                        string cleanV = SanitizeName(kvp.Value);
                        int propId = NextId();
                        lines.Add($"#{propId}=IFCPROPERTYSINGLEVALUE('{cleanK}',$,IFCTEXT('{cleanV}'),$);");
                        propValIds.Add(propId);
                    }

                    if (propValIds.Count > 0)
                    {
                        int psetId = NextId();
                        string propRefs = string.Join(",", propValIds.ConvertAll(pid => $"#{pid}"));
                        lines.Add($"#{psetId}=IFCPROPERTYSET('{GenerateIfcGuid()}',#{ownerHistId},'Pset_CustomProperties',$,({propRefs}));");

                        lines.Add($"#{NextId()}=IFCRELDEFINESBYPROPERTIES('{GenerateIfcGuid()}',#{ownerHistId},$,$,(#{productId}),#{psetId});");
                    }
                }
            }

            var sortedLayerNames = new List<string>(layerItems.Keys);
            sortedLayerNames.Sort(StringComparer.Ordinal);
            foreach (var lName in sortedLayerNames)
            {
                var itemIds = layerItems[lName];
                if (itemIds.Count > 0)
                {
                    string itemRefs = string.Join(",", itemIds.ConvertAll(iid => $"#{iid}"));
                    lines.Add($"#{NextId()}=IFCPRESENTATIONLAYERASSIGNMENT('{lName}',$,({itemRefs}),$);");
                }
            }

            if (productIds.Count > 0)
            {
                string prodRefs = string.Join(",", productIds.ConvertAll(pid => $"#{pid}"));
                lines.Add($"#{NextId()}=IFCRELCONTAINEDINSPATIALSTRUCTURE('{GenerateIfcGuid()}',#{ownerHistId},$,$,({prodRefs}),#{storeyId});");
            }

            lines.Add("ENDSEC;");
            lines.Add("END-ISO-10303-21;");
            return string.Join("\r\n", lines) + "\r\n";
        }

        /// <summary>
        /// Exports a baked <see cref="Scene"/> directly to an ISO-10303-21 STEP ASCII IFC4 file using UTF-8 without BOM.
        /// </summary>
        public static void ExportIfc(Scene scene, string outputPath, double scale = MetresToInches, string schema = "IFC4", Func<string, string, (string StepType, string ClassName)>? classifier = null)
        {
            if (scene == null)
            {
                throw new ArgumentNullException(nameof(scene));
            }
            if (string.IsNullOrWhiteSpace(outputPath))
            {
                throw new ArgumentException("outputPath cannot be empty", nameof(outputPath));
            }

            string dir = Path.GetDirectoryName(outputPath);
            if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
            {
                Directory.CreateDirectory(dir);
            }

            string text = ToIfc(scene, scale, schema, classifier);
            File.WriteAllText(outputPath, text, new UTF8Encoding(false));
        }
    }
}
