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

        /// <summary>
        /// 1 metre = 1000 millimetres (matches the millimetre length unit declared by the IFC exporter).
        /// </summary>
        public const double MetresToMm = 1000.0;

        private const string IfcBase64 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$";

        /// <summary>
        /// Generates a standard 22-character IFC base64 compressed GUID (RFC 4122 v4 128-bit UUID).
        /// </summary>
        public static string GenerateIfcGuid()
        {
            byte[] b = Guid.NewGuid().ToByteArray();
            // Guid.ToByteArray uses mixed-endian; set RFC 4122 v4 bits in big-endian byte layout:
            byte[] be = new byte[16]
            {
                b[3], b[2], b[1], b[0],
                b[5], b[4],
                (byte)((b[7] & 0x0F) | 0x40), b[6],
                (byte)((b[8] & 0x3F) | 0x80), b[9],
                b[10], b[11], b[12], b[13], b[14], b[15],
            };
            ulong hi = 0;
            ulong lo = 0;
            for (int i = 0; i < 8; i++) hi = (hi << 8) | be[i];
            for (int i = 8; i < 16; i++) lo = (lo << 8) | be[i];

            char[] chars = new char[22];
            for (int i = 21; i >= 0; i--)
            {
                chars[i] = IfcBase64[(int)(lo & 63UL)];
                lo = (lo >> 6) | ((hi & 63UL) << 58);
                hi >>= 6;
            }
            return new string(chars);
        }

        internal static string StepText(string text)
        {
            if (string.IsNullOrEmpty(text)) return "";
            var sb = new StringBuilder(text.Length);
            var run = new List<int>();
            bool wide = false;

            void Flush()
            {
                if (run.Count == 0) return;
                sb.Append(wide ? "\\X4\\" : "\\X2\\");
                string fmt = wide ? "X8" : "X4";
                foreach (int cp in run)
                {
                    sb.Append(cp.ToString(fmt, CultureInfo.InvariantCulture));
                }
                sb.Append("\\X0\\");
                run.Clear();
            }

            for (int i = 0; i < text.Length; i++)
            {
                char ch = text[i];
                int cp;
                if (char.IsHighSurrogate(ch) && i + 1 < text.Length && char.IsLowSurrogate(text[i + 1]))
                {
                    cp = char.ConvertToUtf32(ch, text[i + 1]);
                    i++;
                }
                else if (char.IsSurrogate(ch))
                {
                    cp = 0xFFFD;
                }
                else
                {
                    cp = ch;
                }

                if (cp >= 0x20 && cp <= 0x7E)
                {
                    Flush();
                    if (cp == '\'') sb.Append("''");
                    else if (cp == '\\') sb.Append("\\\\");
                    else sb.Append((char)cp);
                }
                else
                {
                    run.Add(cp);
                    if (cp > 0xFFFF) wide = true;
                }
            }
            Flush();
            return sb.ToString();
        }

        private static string TrimAsciiWs(string? value) =>
            string.IsNullOrEmpty(value) ? "" : value!.Trim(' ', '\t', '\r', '\n');

        private static string SanitizeName(string? name)
        {
            string clean = TrimAsciiWs(name);
            if (clean.Length == 0) return "Unnamed";
            return StepText(clean);
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
        /// <paramref name="layerName"/>, then (when
        /// <paramref name="classifyUsingFullPath"/> is <c>true</c>) to
        /// <paramref name="pathName"/>, and finally to a generic, untyped element.
        /// </summary>
        public static (string StepType, string ClassName) ClassifyElement(
            string geomName,
            string layerName = "",
            string pathName = "",
            bool classifyUsingFullPath = false)
        {
            var byName = ClassifyByKeyword(geomName);
            if (byName.HasValue) return byName.Value;
            if (!string.IsNullOrEmpty(layerName))
            {
                var byLayer = ClassifyByKeyword(layerName);
                if (byLayer.HasValue) return byLayer.Value;
            }
            if (classifyUsingFullPath && !string.IsNullOrEmpty(pathName))
            {
                var byPath = ClassifyByKeyword(pathName);
                if (byPath.HasValue) return byPath.Value;
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
                    if (pbr.TryGetValue("baseColorFactor", out var colorVecObj) && colorVecObj is System.Collections.IList colorVec && colorVec.Count >= 3)
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

        private static bool IsFinite(double v) => !double.IsNaN(v) && !double.IsInfinity(v);

        private static long WeldKey(float v)
        {
            double r = Curves.Round6((double)v);
            if (r == 0.0) r = 0.0;
            return BitConverter.DoubleToInt64Bits(r);
        }

        private static bool MeshIsClosed(GlbPrimitive prim)
        {
            if (prim.Positions == null || prim.Indices == null) return false;
            int vCount = prim.Positions.Length / 3;
            int triCount = prim.Indices.Length / 3;
            if (vCount == 0 || triCount == 0) return false;

            var weld = new Dictionary<(long, long, long), int>();
            int[] remap = new int[vCount];
            for (int i = 0; i < vCount; i++)
            {
                var key = (
                    WeldKey(prim.Positions[i * 3]),
                    WeldKey(prim.Positions[i * 3 + 1]),
                    WeldKey(prim.Positions[i * 3 + 2]));
                if (!weld.TryGetValue(key, out int id))
                {
                    id = weld.Count;
                    weld[key] = id;
                }
                remap[i] = id;
            }

            var edgeUse = new Dictionary<(int, int), int>();
            for (int i = 0; i < triCount; i++)
            {
                uint i0 = prim.Indices[i * 3];
                uint i1 = prim.Indices[i * 3 + 1];
                uint i2 = prim.Indices[i * 3 + 2];
                if (i0 >= (uint)vCount || i1 >= (uint)vCount || i2 >= (uint)vCount) return false;
                int a = remap[i0];
                int b = remap[i1];
                int c = remap[i2];
                (int U, int V)[] edges = { (a, b), (b, c), (c, a) };
                for (int k = 0; k < 3; k++)
                {
                    int u = edges[k].U;
                    int v = edges[k].V;
                    var ek = u < v ? (u, v) : (v, u);
                    edgeUse[ek] = (edgeUse.TryGetValue(ek, out int cur) ? cur : 0) + 1;
                }
            }

            if (edgeUse.Count == 0) return false;
            foreach (var n in edgeUse.Values)
            {
                if (n != 2) return false;
            }
            return true;
        }

        private static string IfcCoord(double x, double y, double z, double scale)
        {
            if (!IsFinite(x) || !IsFinite(y) || !IsFinite(z))
            {
                throw new ArgumentException("IFC coordinates must be finite");
            }
            double sx = x * scale;
            double sy = -z * scale;
            double sz = y * scale;
            if (!IsFinite(sx) || !IsFinite(sy) || !IsFinite(sz))
            {
                throw new ArgumentException("IFC coordinates must be finite");
            }
            string vx = sx.ToString("F6", CultureInfo.InvariantCulture);
            string vy = sy.ToString("F6", CultureInfo.InvariantCulture);
            string vz = sz.ToString("F6", CultureInfo.InvariantCulture);
            return $"({vx},{vy},{vz})";
        }

        private static string FmtReal6(double val)
        {
            if (!IsFinite(val))
            {
                throw new ArgumentException("IFC coordinates must be finite");
            }
            return val.ToString("F6", CultureInfo.InvariantCulture);
        }

        private readonly struct SolvedArcSeg
        {
            public readonly (double X, double Y, double Z) Center;
            public readonly double Radius;
            public readonly (double X, double Y, double Z) AxisZ;
            public readonly (double X, double Y, double Z) RefX;
            public readonly double Sweep;

            public SolvedArcSeg(
                (double X, double Y, double Z) center,
                double radius,
                (double X, double Y, double Z) axisZ,
                (double X, double Y, double Z) refX,
                double sweep)
            {
                Center = center;
                Radius = radius;
                AxisZ = axisZ;
                RefX = refX;
                Sweep = sweep;
            }
        }

        private static SolvedArcSeg? SolveIfc2x3ArcSegment(
            (double X, double Y, double Z) p0,
            (double X, double Y, double Z) pm,
            (double X, double Y, double Z) p1)
        {
            double ux = pm.X - p0.X, uy = pm.Y - p0.Y, uz = pm.Z - p0.Z;
            double vx = p1.X - p0.X, vy = p1.Y - p0.Y, vz = p1.Z - p0.Z;
            double wx = uy * vz - uz * vy;
            double wy = uz * vx - ux * vz;
            double wz = ux * vy - uy * vx;
            double u2 = ux * ux + uy * uy + uz * uz;
            double v2 = vx * vx + vy * vy + vz * vz;
            double w2 = wx * wx + wy * wy + wz * wz;
            if (u2 <= 0.0 || v2 <= 0.0 || w2 <= 1e-24 * Math.Max(1.0, u2 * v2)) return null;

            double vxwX = vy * wz - vz * wy;
            double vxwY = vz * wx - vx * wz;
            double vxwZ = vx * wy - vy * wx;
            double wxuX = wy * uz - wz * uy;
            double wxuY = wz * ux - wx * uz;
            double wxuZ = wx * uy - wy * ux;

            double inv2w2 = 0.5 / w2;
            double ox = (u2 * vxwX + v2 * wxuX) * inv2w2;
            double oy = (u2 * vxwY + v2 * wxuY) * inv2w2;
            double oz = (u2 * vxwZ + v2 * wxuZ) * inv2w2;
            double radius = Math.Sqrt(ox * ox + oy * oy + oz * oz);
            if (!IsFinite(radius) || radius <= 0.0) return null;

            double cx = p0.X + ox, cy = p0.Y + oy, cz = p0.Z + oz;
            double invR = 1.0 / radius;
            double xx = -ox * invR, xy = -oy * invR, xz = -oz * invR;
            double invW = 1.0 / Math.Sqrt(w2);
            double zx = wx * invW, zy = wy * invW, zz = wz * invW;
            double yx = zy * xz - zz * xy;
            double yy = zz * xx - zx * xz;
            double yz = zx * xy - zy * xx;

            double d1x = (p1.X - cx) * invR, d1y = (p1.Y - cy) * invR, d1z = (p1.Z - cz) * invR;
            double sweep = Math.Atan2(d1x * yx + d1y * yy + d1z * yz, d1x * xx + d1y * xy + d1z * xz);
            if (sweep <= 0.0) sweep += 2.0 * Math.PI;
            if (!IsFinite(sweep) || sweep <= 0.0 || sweep >= 2.0 * Math.PI) return null;

            return new SolvedArcSeg((cx, cy, cz), radius, (zx, zy, zz), (xx, xy, xz), sweep);
        }

        private static bool NeedsIfc2x3NotDefinedAttr8(string stepType) =>
            stepType == "IFCROOF"
            || stepType == "IFCFOOTING"
            || stepType == "IFCPILE"
            || stepType == "IFCRAMP"
            || stepType == "IFCSTAIR";

        /// <summary>
        /// Serializes a baked <see cref="Scene"/> to ISO-10303-21 STEP ASCII IFC4 format.
        /// </summary>
        /// <param name="scene">Baked world-space scene returned by <see cref="SkpFile.BuildScene(byte[], SkpParseOptions?)"/>.</param>
        /// <param name="scale">Coordinate multiplier from scene metres to the declared millimetre IFC unit (defaults to <see cref="MetresToMm"/>).</param>
        /// <param name="schema">STEP header schema identifier (for example <c>"IFC4"</c>).</param>
        /// <param name="classifier">Optional override for <see cref="ClassifyElement"/> -
        /// use this to supply your own naming convention or metadata-driven typing
        /// instead of the built-in keyword/layer heuristic.</param>
        /// <param name="classifyUsingFullPath">When <c>true</c> and <paramref name="classifier"/> is null, falls back to matching keywords across the full instance hierarchy path.</param>
        public static string ToIfc(
            Scene scene,
            double scale = MetresToMm,
            string schema = "IFC4",
            Func<string, string, (string StepType, string ClassName)>? classifier = null,
            bool classifyUsingFullPath = false)
        {
            if (scene == null || scene.GlbPrimitives == null)
            {
                throw new ArgumentNullException(nameof(scene), "ToIfc requires a valid Scene instance");
            }
            if (!IsFinite(scale))
            {
                throw new ArgumentException("IFC scale must be finite", nameof(scale));
            }

            Func<string, string, string, (string StepType, string ClassName)> classify = classifier != null
                ? (name, layer, _) => classifier(name, layer)
                : (name, layer, path) => ClassifyElement(name, layer, path, classifyUsingFullPath);

            string schemaStr = string.IsNullOrWhiteSpace(schema) ? "IFC4" : schema.ToUpperInvariant();
            bool isIfc2x3 = schemaStr.StartsWith("IFC2X3", StringComparison.Ordinal);
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
            lines.Add($"#{ownerHistId}=IFCOWNERHISTORY(#{personOrgId},#{appId},$,.NOCHANGE.,$,$,$,{timestampEpoch});");

            int lengthUnitId = NextId();
            lines.Add($"#{lengthUnitId}=IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.);");

            int angleUnitId = NextId();
            lines.Add($"#{angleUnitId}=IFCSIUNIT(*,.PLANEANGLEUNIT.,$,.RADIAN.);");

            int solidUnitId = NextId();
            lines.Add($"#{solidUnitId}=IFCSIUNIT(*,.SOLIDANGLEUNIT.,$,.STERADIAN.);");

            int unitAssignId = NextId();
            lines.Add($"#{unitAssignId}=IFCUNITASSIGNMENT((#{lengthUnitId},#{angleUnitId},#{solidUnitId}));");

            int ptZeroId = NextId();
            lines.Add($"#{ptZeroId}=IFCCARTESIANPOINT((0.0,0.0,0.0));");

            int axisPlacementId = NextId();
            lines.Add($"#{axisPlacementId}=IFCAXIS2PLACEMENT3D(#{ptZeroId},$,$);");

            int geomCtxId = NextId();
            lines.Add($"#{geomCtxId}=IFCGEOMETRICREPRESENTATIONCONTEXT($,'Model',3,1.0E-5,#{axisPlacementId},$);");

            int annCtxId = NextId();
            lines.Add($"#{annCtxId}=IFCGEOMETRICREPRESENTATIONSUBCONTEXT('Annotation','Model',*,*,*,*,#{geomCtxId},$,.MODEL_VIEW.,$);");

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
            var layerItems = new Dictionary<string, (string RawName, List<int> ItemIds)>(StringComparer.Ordinal);
            var matStyleCache = new Dictionary<string, int>(StringComparer.Ordinal);

            void AddLayerItem(string? rawLayer, int itemId)
            {
                bool hasLayer = TrimAsciiWs(rawLayer).Length > 0;
                string raw = hasLayer ? rawLayer! : "Layer0";
                string key = hasLayer ? SanitizeName(rawLayer) : "Layer0";
                if (!layerItems.TryGetValue(key, out var entry))
                {
                    entry = (raw, new List<int>());
                    layerItems[key] = entry;
                }
                entry.ItemIds.Add(itemId);
            }

            void WritePset(int productId, string psetName, Dictionary<string, string>? props)
            {
                if (props == null || props.Count == 0) return;

                var propValIds = new List<int>();
                foreach (var kvp in props)
                {
                    string cleanK = SanitizeName(kvp.Key);
                    string cleanV = SanitizeName(kvp.Value);
                    int propId = NextId();
                    lines.Add($"#{propId}=IFCPROPERTYSINGLEVALUE('{cleanK}',$,IFCTEXT('{cleanV}'),$);");
                    propValIds.Add(propId);
                }

                int psetId = NextId();
                string propRefs = string.Join(",", propValIds.ConvertAll(pid => $"#{pid}"));
                lines.Add($"#{psetId}=IFCPROPERTYSET('{GenerateIfcGuid()}',#{ownerHistId},'{SanitizeName(psetName)}',$,({propRefs}));");
                lines.Add($"#{NextId()}=IFCRELDEFINESBYPROPERTIES('{GenerateIfcGuid()}',#{ownerHistId},$,$,(#{productId}),#{psetId});");
            }

            if (scene.CurveSets != null)
            {
                foreach (var cs in scene.CurveSets)
                {
                    if (cs.PointsM == null || cs.PointsM.Count < 2) continue;
                    foreach (var p in cs.PointsM)
                    {
                        if (!IsFinite(p.X) || !IsFinite(p.Y) || !IsFinite(p.Z))
                        {
                            throw new ArgumentException("IFC coordinates must be finite");
                        }
                    }

                    int curveId = 0;
                    if (cs.Arc != null && cs.Arc.Points.Count >= 3 && cs.Arc.Segments.Count >= 1)
                    {
                        if (!isIfc2x3)
                        {
                            var segs = new List<string>(cs.Arc.Segments.Count);
                            foreach (var s in cs.Arc.Segments)
                            {
                                segs.Add($"IFCARCINDEX(({s.Start + 1},{s.Mid + 1},{s.End + 1}))");
                            }
                            var coords = new List<string>(cs.Arc.Points.Count);
                            foreach (var p in cs.Arc.Points)
                            {
                                coords.Add(IfcCoord(p.X, p.Y, p.Z, scale));
                            }
                            int ptListId = NextId();
                            lines.Add($"#{ptListId}=IFCCARTESIANPOINTLIST3D(({string.Join(",", coords)}));");
                            curveId = NextId();
                            lines.Add($"#{curveId}=IFCINDEXEDPOLYCURVE(#{ptListId},({string.Join(",", segs)}),.F.);");
                        }
                        else
                        {
                            var arcPtsZup = new List<(double X, double Y, double Z)>(cs.Arc.Points.Count);
                            foreach (var p in cs.Arc.Points)
                            {
                                if (!IsFinite(p.X) || !IsFinite(p.Y) || !IsFinite(p.Z))
                                {
                                    throw new ArgumentException("IFC coordinates must be finite");
                                }
                                double sx = p.X * scale, sy = -p.Z * scale, sz = p.Y * scale;
                                if (!IsFinite(sx) || !IsFinite(sy) || !IsFinite(sz))
                                {
                                    throw new ArgumentException("IFC coordinates must be finite");
                                }
                                arcPtsZup.Add((sx, sy, sz));
                            }

                            var solvedSegs = new List<SolvedArcSeg>(cs.Arc.Segments.Count);
                            bool validArc = true;
                            foreach (var s in cs.Arc.Segments)
                            {
                                if (s.Start < 0 || s.Start >= arcPtsZup.Count
                                    || s.Mid < 0 || s.Mid >= arcPtsZup.Count
                                    || s.End < 0 || s.End >= arcPtsZup.Count)
                                {
                                    validArc = false;
                                    break;
                                }
                                var solved = SolveIfc2x3ArcSegment(arcPtsZup[s.Start], arcPtsZup[s.Mid], arcPtsZup[s.End]);
                                if (!solved.HasValue)
                                {
                                    validArc = false;
                                    break;
                                }
                                solvedSegs.Add(solved.Value);
                            }

                            int EmitCircle(SolvedArcSeg seg)
                            {
                                int cId = NextId();
                                lines.Add($"#{cId}=IFCCARTESIANPOINT(({FmtReal6(seg.Center.X)},{FmtReal6(seg.Center.Y)},{FmtReal6(seg.Center.Z)}));");
                                int zId = NextId();
                                lines.Add($"#{zId}=IFCDIRECTION(({FmtReal6(seg.AxisZ.X)},{FmtReal6(seg.AxisZ.Y)},{FmtReal6(seg.AxisZ.Z)}));");
                                int xId = NextId();
                                lines.Add($"#{xId}=IFCDIRECTION(({FmtReal6(seg.RefX.X)},{FmtReal6(seg.RefX.Y)},{FmtReal6(seg.RefX.Z)}));");
                                int plId = NextId();
                                lines.Add($"#{plId}=IFCAXIS2PLACEMENT3D(#{cId},#{zId},#{xId});");
                                int circId = NextId();
                                lines.Add($"#{circId}=IFCCIRCLE(#{plId},{FmtReal6(seg.Radius)});");
                                return circId;
                            }

                            if (validArc && solvedSegs.Count == 1)
                            {
                                int circId = EmitCircle(solvedSegs[0]);
                                curveId = NextId();
                                lines.Add($"#{curveId}=IFCTRIMMEDCURVE(#{circId},(IFCPARAMETERVALUE(0.0)),(IFCPARAMETERVALUE({FmtReal6(solvedSegs[0].Sweep)})),.T.,.PARAMETER.);");
                            }
                            else if (validArc
                                && solvedSegs.Count == 2
                                && cs.Arc.Segments[0] == (0, 1, 2)
                                && cs.Arc.Segments[1] == (2, 3, 0)
                                && Math.Abs(solvedSegs[0].Radius - solvedSegs[1].Radius) <= 1e-3 * Math.Max(1.0, solvedSegs[0].Radius))
                            {
                                double dcx = solvedSegs[0].Center.X - solvedSegs[1].Center.X;
                                double dcy = solvedSegs[0].Center.Y - solvedSegs[1].Center.Y;
                                double dcz = solvedSegs[0].Center.Z - solvedSegs[1].Center.Z;
                                if (Math.Sqrt(dcx * dcx + dcy * dcy + dcz * dcz) <= 1e-3 * Math.Max(1.0, solvedSegs[0].Radius))
                                {
                                    curveId = EmitCircle(solvedSegs[0]);
                                }
                            }
                            if (validArc && curveId == 0 && solvedSegs.Count >= 2)
                            {
                                var compSegIds = new List<int>(solvedSegs.Count);
                                foreach (var seg in solvedSegs)
                                {
                                    int circId = EmitCircle(seg);
                                    int trimId = NextId();
                                    lines.Add($"#{trimId}=IFCTRIMMEDCURVE(#{circId},(IFCPARAMETERVALUE(0.0)),(IFCPARAMETERVALUE({FmtReal6(seg.Sweep)})),.T.,.PARAMETER.);");
                                    int csegId = NextId();
                                    lines.Add($"#{csegId}=IFCCOMPOSITECURVESEGMENT(.CONTINUOUS.,.T.,#{trimId});");
                                    compSegIds.Add(csegId);
                                }
                                curveId = NextId();
                                lines.Add($"#{curveId}=IFCCOMPOSITECURVE(({string.Join(",", compSegIds.ConvertAll(sid => $"#{sid}"))}),.F.);");
                            }
                        }
                    }
                    if (curveId == 0)
                    {
                        var coords = new List<string>(cs.PointsM.Count + (cs.Closed ? 1 : 0));
                        foreach (var p in cs.PointsM)
                        {
                            coords.Add(IfcCoord(p.X, p.Y, p.Z, scale));
                        }
                        if (cs.Closed)
                        {
                            coords.Add(coords[0]);
                        }
                        var ptIds = new List<int>(coords.Count);
                        foreach (var c in coords)
                        {
                            int ptId = NextId();
                            lines.Add($"#{ptId}=IFCCARTESIANPOINT({c});");
                            ptIds.Add(ptId);
                        }
                        curveId = NextId();
                        lines.Add($"#{curveId}=IFCPOLYLINE(({string.Join(",", ptIds.ConvertAll(pid => $"#{pid}"))}));");
                    }

                    int curveSetId = NextId();
                    lines.Add($"#{curveSetId}=IFCGEOMETRICCURVESET((#{curveId}));");

                    AddLayerItem(cs.Layer, curveId);

                    string annName = SanitizeName(string.IsNullOrEmpty(cs.Name) ? "Curve" : cs.Name);
                    int annRepId = NextId();
                    lines.Add($"#{annRepId}=IFCSHAPEREPRESENTATION(#{annCtxId},'Annotation','GeometricCurveSet',(#{curveSetId}));");
                    int annShapeId = NextId();
                    lines.Add($"#{annShapeId}=IFCPRODUCTDEFINITIONSHAPE($,$,(#{annRepId}));");
                    int annPlacementId = NextId();
                    lines.Add($"#{annPlacementId}=IFCLOCALPLACEMENT(#{storeyPlacementId},#{axisPlacementId});");
                    int annId = NextId();
                    lines.Add($"#{annId}=IFCANNOTATION('{GenerateIfcGuid()}',#{ownerHistId},'{annName}',$,$,#{annPlacementId},#{annShapeId});");

                    if (cs.AttributeDictionaries != null)
                    {
                        foreach (var dictKvp in cs.AttributeDictionaries)
                        {
                            WritePset(annId, $"Pset_{dictKvp.Key}", dictKvp.Value);
                        }
                    }
                    productIds.Add(annId);
                }
            }

            foreach (var prim in scene.SurfacePrimitives)
            {
                int triCount = prim.Indices.Length / 3;
                int vCount = prim.Positions.Length / 3;
                if (triCount == 0 || vCount == 0) continue;

                MeshMetadata? meta = null;
                if (scene.MeshIndex != null)
                {
                    scene.MeshIndex.TryGetValue(prim.GeomName, out meta);
                }

                string rawNameSrc = meta != null && !string.IsNullOrEmpty(meta.Name) ? meta.Name : prim.GeomName;
                string trimmedName = TrimAsciiWs(rawNameSrc);
                string rawName = trimmedName.Length == 0 ? "Unnamed" : trimmedName;
                string displayName = SanitizeName(rawName);
                string rawLayer = meta != null && !string.IsNullOrEmpty(meta.Layer) ? meta.Layer : "";
                string trimmedLayer = TrimAsciiWs(rawLayer);
                string classifyLayer = trimmedLayer.Length == 0 ? "Layer0" : trimmedLayer;
                string pathName = meta != null && !string.IsNullOrEmpty(meta.Path) ? meta.Path : "";

                var (stepType, _) = classify(rawName, classifyLayer, pathName);

                var ptCoords = new List<string>(vCount);
                for (int i = 0; i < vCount; i++)
                {
                    ptCoords.Add(IfcCoord(
                        prim.Positions[i * 3],
                        prim.Positions[i * 3 + 1],
                        prim.Positions[i * 3 + 2],
                        scale));
                }

                int faceSetId;
                string bodyRepType;
                if (!isIfc2x3)
                {
                    int ptListId = NextId();
                    lines.Add($"#{ptListId}=IFCCARTESIANPOINTLIST3D(({string.Join(",", ptCoords)}));");

                    var faceIndices = new List<string>(triCount);
                    for (int i = 0; i < triCount; i++)
                    {
                        uint idx0 = prim.Indices[i * 3] + 1;
                        uint idx1 = prim.Indices[i * 3 + 1] + 1;
                        uint idx2 = prim.Indices[i * 3 + 2] + 1;
                        faceIndices.Add($"({idx0.ToString(CultureInfo.InvariantCulture)},{idx1.ToString(CultureInfo.InvariantCulture)},{idx2.ToString(CultureInfo.InvariantCulture)})");
                    }

                    string closed = MeshIsClosed(prim) ? ".T." : ".F.";
                    faceSetId = NextId();
                    lines.Add($"#{faceSetId}=IFCTRIANGULATEDFACESET(#{ptListId},$,{closed},({string.Join(",", faceIndices)}),$);");
                    bodyRepType = "Tessellation";
                }
                else
                {
                    var coordToUid = new Dictionary<string, int>(vCount, StringComparer.Ordinal);
                    var uniqueCoords = new List<string>(vCount);
                    var vToUid = new int[vCount];
                    for (int i = 0; i < vCount; i++)
                    {
                        string cStr = ptCoords[i];
                        if (!coordToUid.TryGetValue(cStr, out int uid))
                        {
                            uid = uniqueCoords.Count;
                            coordToUid[cStr] = uid;
                            uniqueCoords.Add(cStr);
                        }
                        vToUid[i] = uid;
                    }

                    var validTris = new List<(int U0, int U1, int U2)>(triCount);
                    bool outOfRange = false;
                    for (int i = 0; i < triCount; i++)
                    {
                        uint i0 = prim.Indices[i * 3];
                        uint i1 = prim.Indices[i * 3 + 1];
                        uint i2 = prim.Indices[i * 3 + 2];
                        if (i0 >= (uint)vCount || i1 >= (uint)vCount || i2 >= (uint)vCount)
                        {
                            outOfRange = true;
                            continue;
                        }
                        int u0 = vToUid[i0], u1 = vToUid[i1], u2 = vToUid[i2];
                        if (u0 == u1 || u1 == u2 || u2 == u0) continue;
                        validTris.Add((u0, u1, u2));
                    }
                    if (validTris.Count == 0) continue;

                    var usedPtId = new int[uniqueCoords.Count];
                    foreach (var (u0, u1, u2) in validTris)
                    {
                        int[] triUids = { u0, u1, u2 };
                        for (int k = 0; k < 3; k++)
                        {
                            int uid = triUids[k];
                            if (usedPtId[uid] == 0)
                            {
                                int ptId = NextId();
                                lines.Add($"#{ptId}=IFCCARTESIANPOINT({uniqueCoords[uid]});");
                                usedPtId[uid] = ptId;
                            }
                        }
                    }

                    var faceIds = new List<int>(validTris.Count);
                    var halfEdges = new Dictionary<ulong, int>(validTris.Count * 3);
                    foreach (var (u0, u1, u2) in validTris)
                    {
                        (int A, int B)[] hes = { (u0, u1), (u1, u2), (u2, u0) };
                        for (int k = 0; k < 3; k++)
                        {
                            ulong hk = ((ulong)(uint)hes[k].A << 32) | (uint)hes[k].B;
                            halfEdges[hk] = (halfEdges.TryGetValue(hk, out int cur) ? cur : 0) + 1;
                        }
                        int loopId = NextId();
                        lines.Add($"#{loopId}=IFCPOLYLOOP((#{usedPtId[u0]},#{usedPtId[u1]},#{usedPtId[u2]}));");
                        int boundId = NextId();
                        lines.Add($"#{boundId}=IFCFACEOUTERBOUND(#{loopId},.T.);");
                        int faceId = NextId();
                        lines.Add($"#{faceId}=IFCFACE((#{boundId}));");
                        faceIds.Add(faceId);
                    }

                    bool isClosedSolid = !outOfRange && validTris.Count >= 4;
                    if (isClosedSolid)
                    {
                        foreach (var kv in halfEdges)
                        {
                            uint ha = (uint)(kv.Key >> 32);
                            uint hb = (uint)kv.Key;
                            ulong rev = ((ulong)hb << 32) | ha;
                            if (kv.Value != 1 || !halfEdges.TryGetValue(rev, out int rcnt) || rcnt != 1)
                            {
                                isClosedSolid = false;
                                break;
                            }
                        }
                    }

                    string faceRefs = string.Join(",", faceIds.ConvertAll(fid => $"#{fid}"));
                    if (isClosedSolid)
                    {
                        int shellId = NextId();
                        lines.Add($"#{shellId}=IFCCLOSEDSHELL(({faceRefs}));");
                        faceSetId = NextId();
                        lines.Add($"#{faceSetId}=IFCFACETEDBREP(#{shellId});");
                        bodyRepType = "Brep";
                    }
                    else
                    {
                        int cfsId = NextId();
                        lines.Add($"#{cfsId}=IFCCONNECTEDFACESET(({faceRefs}));");
                        faceSetId = NextId();
                        lines.Add($"#{faceSetId}=IFCFACEBASEDSURFACEMODEL((#{cfsId}));");
                        bodyRepType = "SurfaceModel";
                    }
                }

                AddLayerItem(rawLayer, faceSetId);

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
                    lines.Add($"#{styleId}=IFCSURFACESTYLE('{displayName}_Material',.BOTH.,(#{renderingId}));");

                    styleAssignId = NextId();
                    lines.Add($"#{styleAssignId}=IFCPRESENTATIONSTYLEASSIGNMENT((#{styleId}));");
                    matStyleCache[rgbaKey] = styleAssignId;
                }

                int styledItemId = NextId();
                lines.Add($"#{styledItemId}=IFCSTYLEDITEM(#{faceSetId},(#{styleAssignId}),$);");

                int shapeRepId = NextId();
                lines.Add($"#{shapeRepId}=IFCSHAPEREPRESENTATION(#{geomCtxId},'Body','{bodyRepType}',(#{faceSetId}));");

                int prodShapeId = NextId();
                lines.Add($"#{prodShapeId}=IFCPRODUCTDEFINITIONSHAPE($,$,(#{shapeRepId}));");

                int prodPlacementId = NextId();
                lines.Add($"#{prodPlacementId}=IFCLOCALPLACEMENT(#{storeyPlacementId},#{axisPlacementId});");

                int productId = NextId();
                var attrs = new List<string>
                {
                    $"'{GenerateIfcGuid()}'",
                    $"#{ownerHistId}",
                    $"'{displayName}'",
                    "$",
                    "$",
                    $"#{prodPlacementId}",
                    $"#{prodShapeId}",
                    "$",
                };
                int? declared = IfcAttrCounts.Get(schemaStr, stepType);
                int total = Math.Max(attrs.Count, declared.HasValue && declared.Value > 0 ? declared.Value : 9);
                while (attrs.Count < total)
                {
                    attrs.Add("$");
                }
                if (isIfc2x3)
                {
                    if (stepType == "IFCBUILDINGELEMENTPROXY" && total == 9)
                    {
                        attrs[8] = ".ELEMENT.";
                    }
                    else if (NeedsIfc2x3NotDefinedAttr8(stepType) && total >= 9)
                    {
                        attrs[8] = ".NOTDEFINED.";
                    }
                }
                else if (stepType == "IFCBUILDINGELEMENTPROXY" && total == 9)
                {
                    attrs[8] = ".NOTDEFINED.";
                }
                lines.Add($"#{productId}={stepType}({string.Join(",", attrs)});");
                productIds.Add(productId);

                if (meta != null)
                {
                    WritePset(productId, "Pset_CustomProperties", meta.Properties);
                    if (meta.AttributeDictionaries != null)
                    {
                        foreach (var dictKvp in meta.AttributeDictionaries)
                        {
                            WritePset(productId, $"Pset_{dictKvp.Key}", dictKvp.Value);
                        }
                    }
                }
            }

            var sortedLayerNames = new List<string>(layerItems.Keys);
            sortedLayerNames.Sort(StringComparer.Ordinal);
            foreach (var lName in sortedLayerNames)
            {
                var entry = layerItems[lName];
                if (entry.ItemIds.Count > 0)
                {
                    string itemRefs = string.Join(",", entry.ItemIds.ConvertAll(iid => $"#{iid}"));
                    bool hidden = scene.LayerHidden != null
                        && scene.LayerHidden.TryGetValue(entry.RawName, out bool h)
                        && h;
                    string layerOn = hidden ? ".F." : ".T.";
                    lines.Add($"#{NextId()}=IFCPRESENTATIONLAYERWITHSTYLE('{lName}',$,({itemRefs}),$,{layerOn},.F.,.F.,());");
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
        public static void ExportIfc(
            Scene scene,
            string outputPath,
            double scale = MetresToMm,
            string schema = "IFC4",
            Func<string, string, (string StepType, string ClassName)>? classifier = null,
            bool classifyUsingFullPath = false)
        {
            if (scene == null)
            {
                throw new ArgumentNullException(nameof(scene));
            }
            if (string.IsNullOrWhiteSpace(outputPath))
            {
                throw new ArgumentException("outputPath cannot be empty", nameof(outputPath));
            }

            string text = ToIfc(scene, scale, schema, classifier, classifyUsingFullPath);
            string? dir = Path.GetDirectoryName(outputPath);
            if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
            {
                Directory.CreateDirectory(dir);
            }
            File.WriteAllText(outputPath, text, new UTF8Encoding(false));
        }
    }
}
