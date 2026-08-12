using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;

namespace OpenSkp
{
    /// <summary>
    /// Export utilities for serializing OpenSKP scenes to AutoCAD R2000 ASCII DXF format.
    /// </summary>
    public static class DxfExport
    {
        /// <summary>
        /// 1 metre = 39.37007874015748 inches (SketchUp native unit).
        /// </summary>
        public const double MetresToInches = 39.37007874015748;

        private static string SanitizeLayerName(string name)
        {
            if (string.IsNullOrWhiteSpace(name)) return "0";
            char[] illegal = new[] { '<', '>', '/', '\\', '"', '~', ':', ';', '?', '*', '=', '`', '|' };
            string clean = new string(name.Select(c => illegal.Contains(c) ? '_' : c).ToArray()).Trim();
            return string.IsNullOrEmpty(clean) ? "0" : clean;
        }

        private static int RgbToAci(int r, int g, int b)
        {
            var standardAci = new (int R, int G, int B, int Aci)[]
            {
                (255, 0, 0, 1),
                (255, 255, 0, 2),
                (0, 255, 0, 3),
                (0, 255, 255, 4),
                (0, 0, 255, 5),
                (255, 0, 255, 6),
                (255, 255, 255, 7),
                (128, 128, 128, 8),
                (192, 192, 192, 9)
            };

            int bestAci = 7;
            double minDist = double.MaxValue;
            foreach (var (sr, sg, sb, aci) in standardAci)
            {
                double dist = (r - sr) * (r - sr) + (g - sg) * (g - sg) + (b - sb) * (b - sb);
                if (dist < minDist)
                {
                    minDist = dist;
                    bestAci = aci;
                }
            }
            return bestAci;
        }

        private static (int R, int G, int B) GetPrimRgb(Scene scene, GlbPrimitive prim)
        {
            int r = 200, g = 200, b = 200;
            if (prim.MaterialIndex >= 0 && scene.GltfMaterials != null && prim.MaterialIndex < scene.GltfMaterials.Count)
            {
                var mat = scene.GltfMaterials[prim.MaterialIndex] as Dictionary<string, object>;
                if (mat != null && mat.TryGetValue("pbrMetallicRoughness", out var pbrObj) && pbrObj is Dictionary<string, object> pbr)
                {
                    if (pbr.TryGetValue("baseColorFactor", out var colorVecObj) && colorVecObj is List<object> colorVec && colorVec.Count >= 3)
                    {
                        r = (int)Math.Round(Convert.ToDouble(colorVec[0], CultureInfo.InvariantCulture) * 255.0);
                        g = (int)Math.Round(Convert.ToDouble(colorVec[1], CultureInfo.InvariantCulture) * 255.0);
                        b = (int)Math.Round(Convert.ToDouble(colorVec[2], CultureInfo.InvariantCulture) * 255.0);
                    }
                }
            }
            return (Math.Max(0, Math.Min(255, r)), Math.Max(0, Math.Min(255, g)), Math.Max(0, Math.Min(255, b)));
        }

        /// <summary>
        /// Serializes a baked <see cref="Scene"/> to AutoCAD R2000 (AC1015) 3D ASCII DXF format.
        /// </summary>
        public static string ToDxf(Scene scene, double scale = MetresToInches, string mode = "polyface")
        {
            if (scene == null || scene.GlbPrimitives == null)
            {
                throw new ArgumentNullException(nameof(scene), "ToDxf requires a valid Scene instance");
            }

            var layerColors = new Dictionary<string, (int R, int G, int B)>();
            foreach (var prim in scene.GlbPrimitives)
            {
                string lName = SanitizeLayerName(prim.GeomName);
                if (!layerColors.ContainsKey(lName))
                {
                    layerColors[lName] = GetPrimRgb(scene, prim);
                }
            }

            if (layerColors.Count == 0)
            {
                layerColors["0"] = (200, 200, 200);
            }

            var sortedLayers = layerColors.Keys.OrderBy(k => k, StringComparer.Ordinal).ToList();

            int handleId = 0x100;
            string NextHandle()
            {
                string h = handleId.ToString("X", CultureInfo.InvariantCulture);
                handleId++;
                return h;
            }

            var layerHandles = new Dictionary<string, string>();
            foreach (var lName in sortedLayers)
            {
                layerHandles[lName] = NextHandle();
            }

            var lines = new List<string>
            {
                "  0", "SECTION", "  2", "HEADER",
                "  9", "$ACADVER", "  1", "AC1015",
                "  9", "$ACADMAINTVER", " 70", "6",
                "  9", "$DWGCODEPAGE", "  3", "ANSI_1252",
                "  9", "$INSBASE", " 10", "0.0", " 20", "0.0", " 30", "0.0",
                "  9", "$EXTMIN", " 10", "1e+20", " 20", "1e+20", " 30", "1e+20",
                "  9", "$EXTMAX", " 10", "-1e+20", " 20", "-1e+20", " 30", "-1e+20",
                "  9", "$LIMMIN", " 10", "0.0", " 20", "0.0",
                "  9", "$LIMMAX", " 10", "420.0", " 20", "297.0",
                "  9", "$ORTHOMODE", " 70", "0",
                "  9", "$REGENMODE", " 70", "1",
                "  9", "$FILLMODE", " 70", "1",
                "  9", "$QTEXTMODE", " 70", "0",
                "  9", "$MIRRTEXT", " 70", "1",
                "  9", "$LTSCALE", " 40", "1.0",
                "  9", "$ATTMODE", " 70", "1",
                "  9", "$TEXTSIZE", " 40", "2.5",
                "  9", "$TRACEWID", " 40", "1.0",
                "  9", "$TEXTSTYLE", "  7", "Standard",
                "  9", "$CLAYER", "  8", "0",
                "  9", "$CELTYPE", "  6", "ByLayer",
                "  9", "$CECOLOR", " 62", "256",
                "  9", "$CELTSCALE", " 40", "1.0",
                "  9", "$DISPSILH", " 70", "0",
                "  9", "$HANDSEED", "  5", "__HANDSEED__",
                "  9", "$INSUNITS", " 70", "1",
                "  0", "ENDSEC",
                "  0", "SECTION", "  2", "CLASSES",
                "  0", "CLASS", "  1", "ACDBDICTIONARYWDFLT", "  2", "AcDbDictionaryWithDefault", "  3", "ObjectDBX Classes", " 90", "0", " 91", "0", "280", "0", "281", "0",
                "  0", "ENDSEC",
                "  0", "SECTION", "  2", "TABLES",
                "  0", "TABLE", "  2", "VPORT", "  5", "1F", "100", "AcDbSymbolTable", " 70", "0", "  0", "ENDTAB",
                "  0", "TABLE", "  2", "LTYPE", "  5", "20", "100", "AcDbSymbolTable", " 70", "1",
                "  0", "LTYPE", "  5", "21", "100", "AcDbSymbolTableRecord", "100", "AcDbLinetypeTableRecord", "  2", "BYBLOCK", " 70", "0", "  3", "", " 72", "65", " 73", "0", " 40", "0.0",
                "  0", "LTYPE", "  5", "22", "100", "AcDbSymbolTableRecord", "100", "AcDbLinetypeTableRecord", "  2", "BYLAYER", " 70", "0", "  3", "", " 72", "65", " 73", "0", " 40", "0.0",
                "  0", "LTYPE", "  5", "23", "100", "AcDbSymbolTableRecord", "100", "AcDbLinetypeTableRecord", "  2", "CONTINUOUS", " 70", "0", "  3", "Solid line", " 72", "65", " 73", "0", " 40", "0.0",
                "  0", "ENDTAB",
                "  0", "TABLE", "  2", "LAYER", "  5", "4", "100", "AcDbSymbolTable", " 70", (sortedLayers.Count + 1).ToString(CultureInfo.InvariantCulture),
                "  0", "LAYER", "  5", "27", "330", "4", "100", "AcDbSymbolTableRecord", "100", "AcDbLayerTableRecord", "  2", "0", " 70", "0", " 62", "7", "  6", "Continuous",
                "  0", "LAYER", "  5", "28", "330", "4", "100", "AcDbSymbolTableRecord", "100", "AcDbLayerTableRecord", "  2", "Defpoints", " 70", "0", " 62", "7", "  6", "Continuous"
            };

            foreach (var lName in sortedLayers)
            {
                var (lr, lg, lb) = layerColors[lName];
                int aci = RgbToAci(lr, lg, lb);
                int trueColor = (lr << 16) | (lg << 8) | lb;
                lines.AddRange(new[]
                {
                    "  0", "LAYER", "  5", layerHandles[lName], "330", "4", "100", "AcDbSymbolTableRecord", "100", "AcDbLayerTableRecord",
                    "  2", lName, " 70", "0", " 62", aci.ToString(CultureInfo.InvariantCulture), "420", trueColor.ToString(CultureInfo.InvariantCulture), "  6", "Continuous"
                });
            }

            lines.AddRange(new[]
            {
                "  0", "ENDTAB",
                "  0", "TABLE", "  2", "STYLE", "  5", "25", "100", "AcDbSymbolTable", " 70", "0", "  0", "ENDTAB",
                "  0", "TABLE", "  2", "VIEW", "  5", "26", "100", "AcDbSymbolTable", " 70", "0", "  0", "ENDTAB",
                "  0", "TABLE", "  2", "UCS", "  5", "27", "100", "AcDbSymbolTable", " 70", "0", "  0", "ENDTAB",
                "  0", "TABLE", "  2", "APPID", "  5", "28", "100", "AcDbSymbolTable", " 70", "1",
                "  0", "APPID", "  5", "29", "100", "AcDbSymbolTableRecord", "100", "AcDbRegAppTableRecord", "  2", "ACAD", " 70", "0",
                "  0", "ENDTAB",
                "  0", "TABLE", "  2", "DIMSTYLE", "  5", "2A", "100", "AcDbSymbolTable", " 70", "0", "  0", "ENDTAB",
                "  0", "TABLE", "  2", "BLOCK_RECORD", "  5", "2B", "100", "AcDbSymbolTable", " 70", "2",
                "  0", "BLOCK_RECORD", "  5", "17", "330", "2B", "100", "AcDbSymbolTableRecord", "100", "AcDbBlockTableRecord", "  2", "*Model_Space",
                "  0", "BLOCK_RECORD", "  5", "1B", "330", "2B", "100", "AcDbSymbolTableRecord", "100", "AcDbBlockTableRecord", "  2", "*Paper_Space",
                "  0", "ENDTAB", "  0", "ENDSEC",
                "  0", "SECTION", "  2", "BLOCKS",
                "  0", "BLOCK", "  5", "18", "330", "17", "100", "AcDbEntity", "  8", "0", "100", "AcDbBlockBegin", "  2", "*Model_Space", " 70", "0", " 10", "0.0", " 20", "0.0", " 30", "0.0", "  3", "*Model_Space", "  1", "",
                "  0", "ENDBLK", "  5", "19", "330", "17", "100", "AcDbEntity", "  8", "0", "100", "AcDbBlockEnd",
                "  0", "BLOCK", "  5", "1C", "330", "1B", "100", "AcDbEntity", "  8", "0", "100", "AcDbBlockBegin", "  2", "*Paper_Space", " 70", "0", " 10", "0.0", " 20", "0.0", " 30", "0.0", "  3", "*Paper_Space", "  1", "",
                "  0", "ENDBLK", "  5", "1D", "330", "1B", "100", "AcDbBlockEnd",
                "  0", "ENDSEC",
                "  0", "SECTION", "  2", "ENTITIES"
            });

            foreach (var prim in scene.GlbPrimitives)
            {
                string lName = SanitizeLayerName(prim.GeomName);
                int triCount = prim.Indices.Length / 3;
                if (triCount == 0) continue;

                var (pr, pg, pb) = GetPrimRgb(scene, prim);
                int aci = RgbToAci(pr, pg, pb);

                if (string.Equals(mode, "polyface", StringComparison.OrdinalIgnoreCase))
                {
                    int vCount = prim.Positions.Length / 3;
                    lines.AddRange(new[]
                    {
                        "  0", "POLYLINE", "  5", NextHandle(), "330", "17", "100", "AcDbEntity", "  8", lName,
                        " 62", aci.ToString(CultureInfo.InvariantCulture), "100", "AcDbPolyFaceMesh", " 66", "1",
                        " 10", "0.0", " 20", "0.0", " 30", "0.0",
                        " 70", "64", " 71", vCount.ToString(CultureInfo.InvariantCulture), " 72", triCount.ToString(CultureInfo.InvariantCulture)
                    });

                    for (int i = 0; i < vCount; i++)
                    {
                        string vx = (prim.Positions[i * 3] * scale).ToString("F6", CultureInfo.InvariantCulture);
                        string vy = (prim.Positions[i * 3 + 1] * scale).ToString("F6", CultureInfo.InvariantCulture);
                        string vz = (prim.Positions[i * 3 + 2] * scale).ToString("F6", CultureInfo.InvariantCulture);
                        lines.AddRange(new[]
                        {
                            "  0", "VERTEX", "  5", NextHandle(), "330", "17", "100", "AcDbEntity", "  8", lName,
                            "100", "AcDbVertex", "100", "AcDbPolyFaceMeshVertex",
                            " 10", vx, " 20", vy, " 30", vz, " 70", "192"
                        });
                    }

                    for (int i = 0; i < triCount; i++)
                    {
                        uint idx0 = prim.Indices[i * 3] + 1;
                        uint idx1 = prim.Indices[i * 3 + 1] + 1;
                        uint idx2 = prim.Indices[i * 3 + 2] + 1;
                        lines.AddRange(new[]
                        {
                            "  0", "VERTEX", "  5", NextHandle(), "330", "17", "100", "AcDbEntity", "  8", lName,
                            "100", "AcDbVertex", "100", "AcDbFaceRecord", " 70", "128",
                            " 71", idx0.ToString(CultureInfo.InvariantCulture), " 72", idx1.ToString(CultureInfo.InvariantCulture), " 73", idx2.ToString(CultureInfo.InvariantCulture), " 74", "0"
                        });
                    }

                    lines.AddRange(new[]
                    {
                        "  0", "SEQEND", "  5", NextHandle(), "330", "17", "100", "AcDbEntity", "  8", lName
                    });
                }
                else
                {
                    for (int i = 0; i < triCount; i++)
                    {
                        int i0 = (int)prim.Indices[i * 3];
                        int i1 = (int)prim.Indices[i * 3 + 1];
                        int i2 = (int)prim.Indices[i * 3 + 2];

                        string v0x = (prim.Positions[i0 * 3] * scale).ToString("F6", CultureInfo.InvariantCulture);
                        string v0y = (prim.Positions[i0 * 3 + 1] * scale).ToString("F6", CultureInfo.InvariantCulture);
                        string v0z = (prim.Positions[i0 * 3 + 2] * scale).ToString("F6", CultureInfo.InvariantCulture);

                        string v1x = (prim.Positions[i1 * 3] * scale).ToString("F6", CultureInfo.InvariantCulture);
                        string v1y = (prim.Positions[i1 * 3 + 1] * scale).ToString("F6", CultureInfo.InvariantCulture);
                        string v1z = (prim.Positions[i1 * 3 + 2] * scale).ToString("F6", CultureInfo.InvariantCulture);

                        string v2x = (prim.Positions[i2 * 3] * scale).ToString("F6", CultureInfo.InvariantCulture);
                        string v2y = (prim.Positions[i2 * 3 + 1] * scale).ToString("F6", CultureInfo.InvariantCulture);
                        string v2z = (prim.Positions[i2 * 3 + 2] * scale).ToString("F6", CultureInfo.InvariantCulture);

                        lines.AddRange(new[]
                        {
                            "  0", "3DFACE", "  5", NextHandle(), "330", "17", "100", "AcDbEntity", "  8", lName,
                            " 62", aci.ToString(CultureInfo.InvariantCulture), "100", "AcDbFace",
                            " 10", v0x, " 20", v0y, " 30", v0z,
                            " 11", v1x, " 21", v1y, " 31", v1z,
                            " 12", v2x, " 22", v2y, " 32", v2z,
                            " 13", v2x, " 23", v2y, " 33", v2z
                        });
                    }
                }
            }

            lines.AddRange(new[]
            {
                "  0", "ENDSEC",
                "  0", "SECTION", "  2", "OBJECTS",
                "  0", "DICTIONARY", "  5", "A", "330", "0", "100", "AcDbDictionary", "281", "1",
                "  3", "ACAD_COLOR", "350", "B",
                "  3", "ACAD_GROUP", "350", "C",
                "  3", "ACAD_LAYOUT", "350", "D",
                "  3", "ACAD_MATERIAL", "350", "E",
                "  3", "ACAD_MLEADERSTYLE", "350", "F",
                "  3", "ACAD_MLINESTYLE", "350", "10",
                "  3", "ACAD_PLOTSETTINGS", "350", "11",
                "  3", "ACAD_PLOTSTYLENAME", "350", "12",
                "  3", "ACAD_SCALELIST", "350", "14",
                "  3", "ACAD_TABLESTYLE", "350", "15",
                "  3", "ACAD_VISUALSTYLE", "350", "16",
                "  0", "DICTIONARY", "  5", "B", "330", "A", "100", "AcDbDictionary", "281", "1",
                "  0", "DICTIONARY", "  5", "C", "330", "A", "100", "AcDbDictionary", "281", "1",
                "  0", "DICTIONARY", "  5", "D", "330", "A", "100", "AcDbDictionary", "281", "1", "  3", "Model", "350", "1A", "  3", "Layout1", "350", "1E",
                "  0", "DICTIONARY", "  5", "E", "330", "A", "100", "AcDbDictionary", "281", "1",
                "  0", "DICTIONARY", "  5", "F", "330", "A", "100", "AcDbDictionary", "281", "1",
                "  0", "DICTIONARY", "  5", "10", "330", "A", "100", "AcDbDictionary", "281", "1",
                "  0", "DICTIONARY", "  5", "11", "330", "A", "100", "AcDbDictionary", "281", "1",
                "  0", "ACDBDICTIONARYWDFLT", "  5", "12", "330", "A", "100", "AcDbDictionary", "281", "1", "  3", "Normal", "350", "13", "100", "AcDbDictionaryWithDefault", "340", "13",
                "  0", "ACDBPLACEHOLDER", "  5", "13", "330", "12",
                "  0", "DICTIONARY", "  5", "14", "330", "A", "100", "AcDbDictionary", "281", "1",
                "  0", "DICTIONARY", "  5", "15", "330", "A", "100", "AcDbDictionary", "281", "1",
                "  0", "DICTIONARY", "  5", "16", "330", "A", "100", "AcDbDictionary", "281", "1",
                "  0", "LAYOUT", "  5", "1A", "330", "D", "100", "AcDbPlotSettings", "  1", "", "  4", "A3", "  6", "", " 40", "7.5", " 41", "20.0", " 42", "7.5", " 43", "20.0", " 44", "420.0", " 45", "297.0", " 46", "0.0", " 47", "0.0", " 48", "0.0", " 49", "0.0", "140", "0.0", "141", "0.0", "142", "1.0", "143", "1.0", " 70", "1024", " 72", "1", " 73", "0", " 74", "5", "  7", "", " 75", "16", " 76", "0", " 77", "2", " 78", "300", "147", "1.0", "148", "0.0", "149", "0.0", "100", "AcDbLayout", "  1", "Model", " 70", "1", " 71", "0", " 10", "0.0", " 20", "0.0", " 11", "420.0", " 21", "297.0", " 12", "0.0", " 22", "0.0", " 32", "0.0", " 14", "1e+20", " 24", "1e+20", " 34", "1e+20", " 15", "-1e+20", " 25", "-1e+20", " 35", "-1e+20", "146", "0.0", " 13", "0.0", " 23", "0.0", " 33", "0.0", " 16", "1.0", " 26", "0.0", " 36", "0.0", " 17", "0.0", " 27", "1.0", " 76", "1", "330", "17",
                "  0", "LAYOUT", "  5", "1E", "330", "D", "100", "AcDbPlotSettings", "  1", "", "  4", "A3", "  6", "", " 40", "7.5", " 41", "20.0", " 42", "7.5", " 43", "20.0", " 44", "420.0", " 45", "297.0", " 46", "0.0", " 47", "0.0", " 48", "0.0", " 49", "0.0", "140", "0.0", "141", "0.0", "142", "1.0", "143", "1.0", " 70", "0", " 72", "1", " 73", "0", " 74", "5", "  7", "", " 75", "16", " 76", "0", " 77", "2", " 78", "300", "147", "1.0", "148", "0.0", "149", "0.0", "100", "AcDbLayout", "  1", "Layout1", " 70", "1", " 71", "1", " 10", "0.0", " 20", "0.0", " 11", "420.0", " 21", "297.0", " 12", "0.0", " 22", "0.0", " 32", "0.0", " 14", "1e+20", " 24", "1e+20", " 34", "1e+20", " 15", "-1e+20", " 25", "-1e+20", " 35", "-1e+20", "146", "0.0", " 13", "0.0", " 23", "0.0", " 33", "0.0", " 16", "1.0", " 26", "0.0", " 36", "0.0", " 17", "0.0", " 27", "1.0", " 76", "1", "330", "1B",
                "  0", "ENDSEC",
                "  0", "EOF"
            });

            // Enforce Windows CRLF (\r\n) line endings!
            string text = string.Join("\r\n", lines) + "\r\n";
            return text.Replace("__HANDSEED__", (handleId + 0x10).ToString("X", CultureInfo.InvariantCulture));
        }

        /// <summary>
        /// Exports a baked <see cref="Scene"/> directly to an AutoCAD R2000 3D DXF file using UTF-8 without BOM.
        /// </summary>
        public static void ExportDxf(Scene scene, string outputPath, double scale = MetresToInches, string mode = "polyface")
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

            string text = ToDxf(scene, scale, mode);
            // Write UTF-8 without BOM to avoid byte order mark corruption in CAD readers
            File.WriteAllText(outputPath, text, new UTF8Encoding(false));
        }
    }
}
