using System;
using System.Collections.Generic;
using System.IO.Compression;
using System.Linq;
using System.Text;
using System.Xml.Linq;

namespace OpenSkp
{
    internal sealed class GeometryBuilderFace
    {
        public List<List<(long EdgeId, long Orientation)>> Loops = new List<List<(long, long)>>();
        public (double X, double Y, double Z) Normal = (0.0, 0.0, 1.0);
        public long? MaterialId;
        public long? BackMaterialId;
        public double[]? UvTransform;
        public double[]? UvTransformBack;
    }

    internal sealed class GeometryBuilderInstance
    {
        public long Offset;
        public string? RefGuid;
        public long? RefIdx;
        public string? Name;
        public List<double> Matrix = new List<double>();
        public long? MaterialId;
        public List<TlvNode> Children = new List<TlvNode>();
    }

    /// <summary>Accumulates the raw geometry extracted for one component
    /// definition (or the implicit ROOT definition). Mirrors Python's
    /// _GeometryBuilder in _core.py.</summary>
    internal sealed class GeometryBuilder
    {
        public Dictionary<long, (double X, double Y, double Z)> Vertices = new Dictionary<long, (double, double, double)>();
        public Dictionary<long, (long? V1, long? V2)> Edges = new Dictionary<long, (long?, long?)>();
        public Dictionary<long, int> EdgeFlags = new Dictionary<long, int>();
        public Dictionary<long, GeometryBuilderFace> Faces = new Dictionary<long, GeometryBuilderFace>();
        public List<GeometryBuilderInstance> Instances = new List<GeometryBuilderInstance>();
    }

    internal static class Geometry
    {
        public static TlvNode? FindChildTag(List<TlvNode> nodes, string target)
        {
            foreach (var n in nodes)
            {
                if (n.Tag == target) return n;
                var res = FindChildTag(n.Children, target);
                if (res != null) return res;
            }
            return null;
        }

        public static void FindAllNodesRec(List<TlvNode> nodes, string targetTag, List<TlvNode> results)
        {
            foreach (var n in nodes)
            {
                if (n.Tag == targetTag) results.Add(n);
                FindAllNodesRec(n.Children, targetTag, results);
            }
        }

        /// <summary>Mirrors Python's extract_entity_id exactly: only DE05 and
        /// a DE05-prefixed DC05 resolve an ID here (unlike collect_layers /
        /// collect_material_ids, which also fall back to a bare var-int read
        /// when the DC05 payload lacks the DE05 marker).</summary>
        public static long? ExtractEntityId(TlvNode node)
        {
            foreach (var child in node.Children)
            {
                if (child.Tag == "DE05")
                {
                    return Tlv.ParseVarInt(child.Payload, 0, child.Payload.Length);
                }
                if (child.Tag == "DC05")
                {
                    var payload = child.Payload;
                    if (payload.Length >= 2 && payload[0] == 0xDE && payload[1] == 0x05)
                    {
                        uint de05Len = Tlv.ReadU32(payload, 2);
                        return Tlv.ParseVarInt(payload, 6, (int)de05Len);
                    }
                }
            }
            foreach (var child in node.Children)
            {
                var res = ExtractEntityId(child);
                if (res != null) return res;
            }
            return null;
        }

        /// <summary>Entity-ID resolution used by collect_layers /
        /// collect_material_ids: falls back to a raw var-int read of the
        /// whole DC05 payload when it doesn't start with the DE05 marker.</summary>
        private static long ParseIdFromDc05(byte[] payload)
        {
            if (payload.Length >= 2 && payload[0] == 0xDE && payload[1] == 0x05)
            {
                uint de05Len = Tlv.ReadU32(payload, 2);
                return Tlv.ParseVarInt(payload, 6, (int)de05Len);
            }
            return Tlv.ParseVarInt(payload, 0, payload.Length);
        }

        public static (double[]? Front, double[]? Back) ExtractUvTransforms(byte[] dc05Payload)
        {
            var dd05 = Tlv.FindFlat(Tlv.ParseFlat(dc05Payload), "DD05");
            if (dd05 == null) return (null, null);
            var b136 = Tlv.FindFlat(Tlv.ParseFlat(dd05), "B136");
            if (b136 == null) return (null, null);
            var b236 = Tlv.FindFlat(Tlv.ParseFlat(b136), "B236");
            if (b236 == null) return (null, null);
            var t1027 = Tlv.FindFlat(Tlv.ParseFlat(b236), "1027");
            if (t1027 == null) return (null, null);

            var sides = Tlv.ParseFlat(t1027);
            double[]? front = ExtractUvSide(sides, "1127");
            double[]? back = ExtractUvSide(sides, "1227");
            return (front, back);
        }

        private static double[]? ExtractUvSide(List<(string Tag, byte[] Body)> sides, string sideTag)
        {
            var side = Tlv.FindFlat(sides, sideTag);
            if (side == null) return null;
            var t1327 = Tlv.FindFlat(Tlv.ParseFlat(side), "1327");
            if (t1327 == null) return null;
            var t1527 = Tlv.FindFlat(Tlv.ParseFlat(t1327), "1527");
            if (t1527 == null || t1527.Length != 72) return null;
            var mat = new double[9];
            for (int i = 0; i < 9; i++) mat[i] = Tlv.ReadF64(t1527, i * 8);
            return mat;
        }

        public static void ExtractGeometryFromNodes(List<TlvNode> elements, GeometryBuilder builder)
        {
            foreach (var el in elements)
            {
                string tag = el.Tag;

                if (tag == "C409")
                {
                    var vId = ExtractEntityId(el);
                    var c509 = FindChildTag(el.Children, "C509");
                    if (vId != null && c509 != null && c509.Payload.Length >= 24)
                    {
                        double x = Tlv.ReadF64(c509.Payload, 0);
                        double y = Tlv.ReadF64(c509.Payload, 8);
                        double z = Tlv.ReadF64(c509.Payload, 16);
                        builder.Vertices[vId.Value] = (x, y, z);
                    }
                }
                else if (tag == "B80B")
                {
                    var eId = ExtractEntityId(el);
                    if (eId != null)
                    {
                        var v1Node = FindChildTag(el.Children, "B90B");
                        var v2Node = FindChildTag(el.Children, "BA0B");
                        long? v1 = v1Node != null ? Tlv.ParseVarInt(v1Node.Payload, 0, v1Node.Payload.Length) : (long?)null;
                        long? v2 = v2Node != null ? Tlv.ParseVarInt(v2Node.Payload, 0, v2Node.Payload.Length) : (long?)null;
                        builder.Edges[eId.Value] = (v1, v2);

                        var d007 = el.Children.FirstOrDefault(c => c.Tag == "D007");
                        if (d007 != null)
                        {
                            var d307 = d007.Children.FirstOrDefault(c => c.Tag == "D307");
                            if (d307 != null && d307.Payload.Length > 0)
                            {
                                builder.EdgeFlags[eId.Value] = d307.Payload[0];
                            }
                        }
                    }
                }
                else if (tag == "AC0D")
                {
                    var fId = ExtractEntityId(el);
                    if (fId != null)
                    {
                        var normal = (0.0, 0.0, 1.0);
                        var ad0d = FindChildTag(el.Children, "AD0D");
                        if (ad0d != null && ad0d.Payload.Length >= 24)
                        {
                            normal = (Tlv.ReadF64(ad0d.Payload, 0), Tlv.ReadF64(ad0d.Payload, 8), Tlv.ReadF64(ad0d.Payload, 16));
                        }

                        var ae0d = FindChildTag(el.Children, "AE0D");
                        var loops = new List<List<(long, long)>>();
                        if (ae0d != null)
                        {
                            var loopNodes = new List<TlvNode>();
                            FindAllNodesRec(ae0d.Children, "9411", loopNodes);
                            foreach (var ln in loopNodes)
                            {
                                var coEdges = new List<(long, long)>();
                                var coNodes = new List<TlvNode>();
                                FindAllNodesRec(ln.Children, "A00F", coNodes);
                                foreach (var cn in coNodes)
                                {
                                    var payload = cn.Payload;
                                    long? edgeId = null;
                                    long? orient = null;
                                    int subPos = 0;
                                    while (subPos < payload.Length - 6)
                                    {
                                        byte b0 = payload[subPos];
                                        byte b1 = payload[subPos + 1];
                                        uint subSize = Tlv.ReadU32(payload, subPos + 2);
                                        if (subPos + 6 + subSize <= payload.Length)
                                        {
                                            long val = Tlv.ParseVarInt(payload, subPos + 6, (int)subSize);
                                            if (b0 == 0xA1 && b1 == 0x0F) edgeId = val;
                                            else if (b0 == 0xA2 && b1 == 0x0F) orient = val;
                                        }
                                        subPos += 6 + (int)subSize;
                                    }
                                    if (edgeId != null && orient != null)
                                    {
                                        coEdges.Add((edgeId.Value, orient.Value));
                                    }
                                }
                                if (coEdges.Count > 0) loops.Add(coEdges);
                            }
                        }

                        long? faceMatId = null;
                        double[]? uvFront = null;
                        double[]? uvBack = null;
                        var d007 = el.Children.FirstOrDefault(c => c.Tag == "D007");
                        if (d007 != null)
                        {
                            var d107 = d007.Children.FirstOrDefault(c => c.Tag == "D107");
                            if (d107 != null)
                            {
                                faceMatId = Tlv.ParseVarInt(d107.Payload, 0, d107.Payload.Length);
                            }
                            var dc05 = d007.Children.FirstOrDefault(c => c.Tag == "DC05");
                            if (dc05 != null)
                            {
                                (uvFront, uvBack) = ExtractUvTransforms(dc05.Payload);
                            }
                        }

                        long? backMatId = null;
                        var af0d = el.Children.FirstOrDefault(c => c.Tag == "AF0D");
                        if (af0d != null && af0d.Payload.Length > 0)
                        {
                            backMatId = Tlv.ParseVarInt(af0d.Payload, 0, af0d.Payload.Length);
                        }

                        builder.Faces[fId.Value] = new GeometryBuilderFace
                        {
                            Loops = loops,
                            Normal = normal,
                            MaterialId = faceMatId,
                            BackMaterialId = backMatId,
                            UvTransform = uvFront,
                            UvTransformBack = uvBack,
                        };
                    }
                }
                else if (tag == "6419")
                {
                    var nodesToSearch = el.Children.Count > 0 ? el.Children : new List<TlvNode> { el };
                    string? guid = null;
                    long? defIdx = null;
                    string? name = null;
                    var matrix = new List<double>();

                    var guidNode = FindChildTag(nodesToSearch, "6819");
                    if (guidNode != null && guidNode.Payload.Length == 16)
                    {
                        guid = Tlv.ToHexUpper(guidNode.Payload);
                    }
                    var defIdxNode = FindChildTag(nodesToSearch, "6719");
                    if (defIdxNode != null)
                    {
                        defIdx = Tlv.ParseVarInt(defIdxNode.Payload, 0, defIdxNode.Payload.Length);
                    }
                    var nameNode = FindChildTag(nodesToSearch, "6519");
                    if (nameNode != null)
                    {
                        name = Encoding.UTF8.GetString(nameNode.Payload);
                    }
                    var matNode = FindChildTag(nodesToSearch, "6619");
                    if (matNode != null && matNode.Payload.Length >= 104)
                    {
                        for (int idx = 0; idx < 13; idx++)
                        {
                            matrix.Add(Tlv.ReadF64(matNode.Payload, idx * 8));
                        }
                    }

                    long? instMatId = null;
                    var instD007 = el.Children.FirstOrDefault(c => c.Tag == "D007");
                    if (instD007 != null)
                    {
                        var d107 = instD007.Children.FirstOrDefault(c => c.Tag == "D107");
                        if (d107 != null)
                        {
                            instMatId = Tlv.ParseVarInt(d107.Payload, 0, d107.Payload.Length);
                        }
                    }

                    builder.Instances.Add(new GeometryBuilderInstance
                    {
                        Offset = el.Offset,
                        RefGuid = guid,
                        RefIdx = defIdx,
                        Name = name,
                        Matrix = matrix,
                        MaterialId = instMatId,
                        Children = el.Children,
                    });
                }
                else if (el.Children.Count > 0)
                {
                    ExtractGeometryFromNodes(el.Children, builder);
                }
            }
        }

        // ── Layer / material ID lookups (used by Core.FullParse) ──────────

        public static void CollectLayers(List<TlvNode> nodes, Dictionary<long, string> layerIdToName)
        {
            foreach (var el in nodes)
            {
                if (el.Tag == "993A")
                {
                    foreach (var child in el.Children)
                    {
                        if (child.Tag == "8C3C")
                        {
                            var dc05 = FindChildTag(child.Children, "DC05");
                            var nameNode = FindChildTag(child.Children, "8D3C");
                            if (dc05 != null && nameNode != null)
                            {
                                long lId = ParseIdFromDc05(dc05.Payload);
                                string lName = Encoding.UTF8.GetString(nameNode.Payload);
                                layerIdToName[lId] = lName;
                            }
                        }
                    }
                }
                CollectLayers(el.Children, layerIdToName);
            }
        }

        public static void CollectMaterialIds(List<TlvNode> nodes, Dictionary<long, string> materialIdToName)
        {
            foreach (var el in nodes)
            {
                if (el.Tag == "C832")
                {
                    var dc05 = FindChildTag(el.Children, "DC05");
                    var nameNode = FindChildTag(el.Children, "CC32");
                    if (dc05 != null && nameNode != null)
                    {
                        long mId = ParseIdFromDc05(dc05.Payload);
                        string mName = Encoding.UTF8.GetString(nameNode.Payload);
                        materialIdToName[mId] = mName;
                    }
                }
                CollectMaterialIds(el.Children, materialIdToName);
            }
        }

        internal sealed class RawDefinition
        {
            public string? Guid;
            public string? Name;
            public bool AlwaysFacesCamera;
            public bool IsImage;
            public GeometryBuilder Builder = new GeometryBuilder();
        }

        public static void CollectDefs(List<TlvNode> nodes, Dictionary<long, RawDefinition> defsDict)
        {
            foreach (var el in nodes)
            {
                if (el.Tag == "7C15")
                {
                    string? guid = null;
                    string? name = null;
                    bool facesCamera = false;
                    bool isImage = false;
                    foreach (var child in el.Children)
                    {
                        if (child.Tag == "7D15" && child.Payload.Length == 16)
                        {
                            guid = Tlv.ToHexUpper(child.Payload);
                        }
                        else if (child.Tag == "7E15")
                        {
                            name = Encoding.UTF8.GetString(child.Payload);
                        }
                        else if (child.Tag == "581B")
                        {
                            int pos = 0;
                            var pl = child.Payload;
                            while (pos <= pl.Length - 6)
                            {
                                string subTag = pl[pos].ToString("X2") + pl[pos + 1].ToString("X2");
                                uint subSize = Tlv.ReadU32(pl, pos + 2);
                                if (pos + 6 + subSize > pl.Length) break;
                                if (subTag == "5D1B" && subSize >= 1)
                                {
                                    facesCamera = Tlv.ParseVarInt(pl, pos + 6, (int)subSize) == 1;
                                }
                                pos += 6 + (int)subSize;
                            }
                        }
                        else if (child.Tag == "8315" && child.Payload.Length > 0)
                        {
                            isImage = Tlv.ParseVarInt(child.Payload, 0, child.Payload.Length) == 2;
                        }
                    }
                    long? entId = ExtractEntityId(el);
                    var builder = new GeometryBuilder();
                    ExtractGeometryFromNodes(el.Children, builder);
                    if (entId != null)
                    {
                        defsDict[entId.Value] = new RawDefinition
                        {
                            Guid = guid,
                            Name = name,
                            AlwaysFacesCamera = facesCamera,
                            IsImage = isImage,
                            Builder = builder,
                        };
                    }
                }
                CollectDefs(el.Children, defsDict);
            }
        }

        // ── material.xml / style.xml parsing ──────────────────────────────

        internal sealed class RawTexture
        {
            public string Filename = "";
            public double XScale;
            public double YScale;
            public byte[]? Data;
        }

        internal sealed class RawMaterial
        {
            public string Name = "";
            public int R = 128, G = 128, B = 128;
            public double Transparency = 1.0;
            public bool Colorized;
            public int ColorizeType;
            public RawTexture? Texture;
        }

        internal sealed class RawStyle
        {
            public string Name = "";
            public (int R, int G, int B)? FrontColor;
            public (int R, int G, int B)? BackColor;
        }

        private static readonly XNamespace MatNs = "http://sketchup.google.com/schemas/sketchup/1.0/material";
        private static readonly XNamespace StyleNs = "http://sketchup.google.com/schemas/sketchup/1.0/style";
        private static readonly XNamespace TypesNs = "http://sketchup.google.com/schemas/1.0/types";

        /// <summary>Parse one materials/&lt;folder&gt;/material.xml entry.
        /// xmlName is the archive path (e.g. "materials/Wood/material.xml"),
        /// used to resolve sibling texture image files.</summary>
        public static RawMaterial? ParseMaterialXml(ZipArchive zip, string xmlName, byte[] xmlData)
        {
            XElement root;
            try
            {
                root = XDocument.Parse(Encoding.UTF8.GetString(xmlData)).Root!;
            }
            catch
            {
                return null;
            }

            var matElem = root.Descendants(MatNs + "material").FirstOrDefault();
            if (matElem == null) return null;

            string matName = (string?)matElem.Attribute("name") ?? "unknown";
            int r = (int?)matElem.Attribute("colorRed") ?? 128;
            int g = (int?)matElem.Attribute("colorGreen") ?? 128;
            int b = (int?)matElem.Attribute("colorBlue") ?? 128;

            double trans;
            if ((string?)matElem.Attribute("useTrans") == "1")
            {
                double raw = ParseDoubleOr((string?)matElem.Attribute("trans"), 0.0);
                trans = Math.Min(Math.Max(1.0 - raw, 0.0), 1.0);
            }
            else
            {
                trans = 1.0;
            }

            bool colorized = (string?)matElem.Attribute("type") == "2";
            int colorizeType = (int?)matElem.Attribute("colorizeType") ?? 0;

            var mat = new RawMaterial
            {
                Name = matName,
                R = r,
                G = g,
                B = b,
                Transparency = trans,
                Colorized = colorized,
                ColorizeType = colorizeType,
            };

            mat.Texture = ExtractTexture(zip, xmlName, matElem);
            return mat;
        }

        private static double ParseDoubleOr(string? s, double fallback)
        {
            if (string.IsNullOrEmpty(s)) return fallback;
            return double.TryParse(s, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var v) ? v : fallback;
        }

        private static RawTexture? ExtractTexture(ZipArchive zip, string xmlName, XElement matElem)
        {
            var texElem = matElem.Element(MatNs + "texture");
            if (texElem == null) return null;

            string filename = (string?)texElem.Attribute("textureFilename") ?? "";
            double xScale = ParseDoubleOr((string?)texElem.Attribute("xScale"), 0.0);
            double yScale = ParseDoubleOr((string?)texElem.Attribute("yScale"), 0.0);

            int lastSlash = xmlName.LastIndexOf('/');
            string folder = lastSlash >= 0 ? xmlName.Substring(0, lastSlash) : "";

            byte[]? data = null;
            var entryNames = zip.Entries.Select(e => e.FullName).ToList();

            string? candidate = filename.Length > 0 ? folder + "/" + filename : null;
            if (candidate != null && entryNames.Contains(candidate))
            {
                data = ReadEntry(zip, candidate);
            }
            else
            {
                foreach (var entry in entryNames)
                {
                    if (entry.StartsWith(folder + "/", StringComparison.Ordinal)
                        && entry != xmlName
                        && !entry.ToLowerInvariant().EndsWith(".xml"))
                    {
                        data = ReadEntry(zip, entry);
                        if (filename.Length == 0)
                        {
                            int s = entry.LastIndexOf('/');
                            filename = s >= 0 ? entry.Substring(s + 1) : entry;
                        }
                        break;
                    }
                }
            }

            if (data == null)
            {
                var imgElem = texElem.Element(MatNs + "images")?.Element(MatNs + "image");
                string imgPath = (string?)imgElem?.Attribute("path") ?? "";
                imgPath = LStripChars(imgPath, "./");
                foreach (var cand in new[] { imgPath, folder + "/" + imgPath })
                {
                    if (cand.Length > 0 && entryNames.Contains(cand))
                    {
                        data = ReadEntry(zip, cand);
                        if (filename.Length == 0)
                        {
                            int s = cand.LastIndexOf('/');
                            filename = s >= 0 ? cand.Substring(s + 1) : cand;
                        }
                        break;
                    }
                }
            }

            return new RawTexture { Filename = filename, XScale = xScale, YScale = yScale, Data = data };
        }

        /// <summary>Matches Python's str.lstrip(chars): repeatedly strips any
        /// leading character found in `chars`, NOT the literal prefix "./".</summary>
        private static string LStripChars(string s, string chars)
        {
            int i = 0;
            while (i < s.Length && chars.IndexOf(s[i]) >= 0) i++;
            return s.Substring(i);
        }

        private static byte[] ReadEntry(ZipArchive zip, string name)
        {
            var entry = zip.GetEntry(name)!;
            using var stream = entry.Open();
            using var ms = new System.IO.MemoryStream();
            stream.CopyTo(ms);
            return ms.ToArray();
        }

        public static RawStyle? ParseStyleXml(byte[] xmlData)
        {
            XElement root;
            try
            {
                root = XDocument.Parse(Encoding.UTF8.GetString(xmlData)).Root!;
            }
            catch
            {
                return null;
            }

            var styleEl = root.Element(StyleNs + "style");
            if (styleEl == null) return null;

            var colors = new Dictionary<string, (int, int, int)>();
            foreach (var item in styleEl.Elements(StyleNs + "item"))
            {
                string? iid = (string?)item.Attribute("id");
                var variant = item.Element(TypesNs + "variant");
                if ((iid == "4000" || iid == "4001") && variant != null && !string.IsNullOrEmpty(variant.Value))
                {
                    if (long.TryParse(variant.Value, out long signed))
                    {
                        uint v = unchecked((uint)signed);
                        colors[iid] = ((int)((v >> 16) & 255), (int)((v >> 8) & 255), (int)(v & 255));
                    }
                }
            }

            return new RawStyle
            {
                Name = (string?)styleEl.Attribute("name") ?? "",
                FrontColor = colors.TryGetValue("4000", out var fc) ? fc : ((int, int, int)?)null,
                BackColor = colors.TryGetValue("4001", out var bc) ? bc : ((int, int, int)?)null,
            };
        }
    }
}
