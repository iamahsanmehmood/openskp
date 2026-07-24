using System;
using System.Collections.Generic;
using System.IO.Compression;
using System.Linq;
using System.Text;

namespace OpenSkp
{
    /// <summary>Orchestrates the full parsing pipeline for both container
    /// eras, producing a shape-identical RawParsed regardless of which path
    /// ran. Mirrors Python's _core.full_parse() / legacy.full_parse_legacy().</summary>
    internal static class Core
    {
        internal sealed class RawParsed
        {
            public string Version = "unknown";
            public Dictionary<string, (int R, int G, int B)> LayerColors = new Dictionary<string, (int, int, int)>();
            public Dictionary<long, string> LayerIdToName = new Dictionary<long, string>();
            public Dictionary<long, string> MaterialIdToName = new Dictionary<long, string>();
            public Dictionary<string, Geometry.RawMaterial> Materials = new Dictionary<string, Geometry.RawMaterial>();
            public Dictionary<string, Geometry.RawMaterial> MaterialsByFolder = new Dictionary<string, Geometry.RawMaterial>();
            public List<Geometry.RawStyle> Styles = new List<Geometry.RawStyle>();
            public Dictionary<long, Geometry.RawDefinition> DefsDict = new Dictionary<long, Geometry.RawDefinition>();
            public Geometry.RawDefinition Root = new Geometry.RawDefinition { Guid = "ROOT", Name = "ROOT_MODEL" };
        }

        public static RawParsed FullParse(byte[] data, SkpParseOptions? options = null)
        {
            var sw = System.Diagnostics.Stopwatch.StartNew();
            Observability.Log(options, SkpLogLevel.Information, $"Parsing buffer ({data.Length} bytes)");

            int headerLen = Math.Min(512, data.Length);
            var header = new byte[headerLen];
            Array.Copy(data, header, headerLen);

            if (!Vff.HasValidHeader(header))
            {
                throw new SkpParseException("Not a valid SketchUp file (bad header magic)", stage: "header");
            }

            if (Legacy.IsLegacy(data))
            {
                Observability.Log(options, SkpLogLevel.Debug, "Detected legacy MFC container; routing to legacy walker");
                return Legacy.FullParseLegacy(data, options);
            }

            string version = Vff.ExtractVersion(header);
            Observability.Log(options, SkpLogLevel.Debug, $"Detected version {version} (VFF/ZIP container)");

            int pkPos = Vff.FindZipOffset(data);
            if (pkPos < 0)
            {
                throw new SkpParseException("No ZIP container found", stage: "zip_extract");
            }

            using var zip = Vff.OpenZip(data, pkPos);

            var layerColors = new Dictionary<string, (int, int, int)>();
            var materials = new Dictionary<string, Geometry.RawMaterial>();
            var materialsByFolder = new Dictionary<string, Geometry.RawMaterial>();

            foreach (var entry in zip.Entries)
            {
                string name = entry.FullName;
                if (name.EndsWith("material.xml", StringComparison.Ordinal) && name.StartsWith("materials/", StringComparison.Ordinal))
                {
                    byte[] xmlData;
                    using (var s = entry.Open())
                    using (var ms = new System.IO.MemoryStream())
                    {
                        s.CopyTo(ms);
                        xmlData = ms.ToArray();
                    }
                    Geometry.RawMaterial? mat;
                    try
                    {
                        mat = Geometry.ParseMaterialXml(zip, name, xmlData);
                    }
                    catch
                    {
                        mat = null;
                    }
                    if (mat != null)
                    {
                        var parts = name.Split('/');
                        string folderName = parts.Length > 1 ? parts[1] : "";
                        materials[mat.Name] = mat;
                        if (folderName.Length > 0)
                        {
                            materialsByFolder[folderName] = mat;
                        }
                        if (mat.Name.StartsWith("Layer_", StringComparison.Ordinal))
                        {
                            layerColors[mat.Name.Substring(6)] = (mat.R, mat.G, mat.B);
                        }
                    }
                }
            }

            var styles = new List<Geometry.RawStyle>();
            foreach (var entry in zip.Entries)
            {
                string name = entry.FullName;
                if (!(name.StartsWith("styles/", StringComparison.Ordinal) && name.EndsWith("style.xml", StringComparison.Ordinal)))
                {
                    continue;
                }
                byte[] xmlData;
                using (var s = entry.Open())
                using (var ms = new System.IO.MemoryStream())
                {
                    s.CopyTo(ms);
                    xmlData = ms.ToArray();
                }
                var style = Geometry.ParseStyleXml(xmlData);
                if (style != null)
                {
                    styles.Add(style);
                }
            }

            Observability.Log(options, SkpLogLevel.Debug, $"Parsed {materials.Count} materials, {styles.Count} styles");

            var modelDatEntry = zip.GetEntry("model.dat");
            if (modelDatEntry == null)
            {
                throw new SkpParseException("model.dat not found in ZIP container", stage: "zip_extract");
            }
            // model.dat routinely decompresses to several GB on real
            // production files (SketchUp's binary format has been observed
            // compressing at ~10x) - well past .NET's ~2.1GB single-array
            // limit. Read it into a ChunkedBuffer (multiple bounded
            // segments) instead of one MemoryStream, using the ZIP entry's
            // own recorded uncompressed length so each segment is allocated
            // once at its exact final size.
            ChunkedBuffer modelDat;
            using (var s = modelDatEntry.Open())
            {
                modelDat = ChunkedBuffer.FromStream(s, modelDatEntry.Length);
            }
            Observability.Log(options, SkpLogLevel.Debug, $"Read model.dat: {modelDat.Length} bytes");

            // Walk the TLV tree one top-level record at a time (instead of
            // building the whole file's tree at once) so peak memory is
            // bounded by the single largest definition/layer-manager/
            // material-manager/root block, not by the file's total node
            // count. Real production files can have 100k+ separate
            // component definitions; materializing all of them
            // simultaneously is what actually exhausts memory on large
            // files - not the (comparatively modest, ~1x) cost of
            // decompressing model.dat itself.
            var layerIdToName = new Dictionary<long, string>();
            var materialIdToName = new Dictionary<long, string>();
            var defsDictRaw = new Dictionary<long, Geometry.RawDefinition>();
            var rootBuilder = new GeometryBuilder();

            foreach (var rec in Tlv.IterTopLevelLazy(modelDat, 0, modelDat.Length, Tlv.ContainerTags))
            {
                var el = rec.Node;
                try
                {
                    var single = new List<TlvNode> { el };
                    Geometry.CollectLayers(single, layerIdToName);
                    Geometry.CollectMaterialIds(single, materialIdToName);
                    Geometry.CollectDefs(single, defsDictRaw);
                    if (el.Tag == "F601")
                    {
                        Geometry.ExtractGeometryFromNodes(el.Children, rootBuilder);
                    }
                }
                catch (Exception e) when (!(e is SkpParseException))
                {
                    throw new SkpParseException(
                        $"Failed while processing top-level record: {e.Message}",
                        stage: "tlv_walk", recordIndex: rec.Index, totalRecords: rec.Total, tag: el.Tag,
                        innerException: e);
                }
                // `el` (and its whole subtree) is now unreferenced and
                // eligible for garbage collection before the next top-level
                // record is built.
                if (rec.Index % ParseTuning.ProgressInterval == 0 || rec.Index == rec.Total - 1)
                {
                    Observability.Progress(options, "tlv_walk", rec.Index + 1, rec.Total);
                    Observability.Log(options, SkpLogLevel.Debug, $"Processed {rec.Index + 1}/{rec.Total} top-level records");
                }
            }

            Observability.Log(
                options, SkpLogLevel.Information,
                $"Parse complete: {defsDictRaw.Count} defs ({sw.Elapsed.TotalSeconds:F2}s)");

            if (!layerIdToName.ContainsKey(1))
            {
                layerIdToName[1] = "Layer0";
            }
            if (!layerColors.ContainsKey("Layer0"))
            {
                layerColors["Layer0"] = (136, 136, 136);
            }

            return new RawParsed
            {
                Version = version,
                LayerColors = layerColors,
                LayerIdToName = layerIdToName,
                MaterialIdToName = materialIdToName,
                Materials = materials,
                MaterialsByFolder = materialsByFolder,
                Styles = styles,
                DefsDict = defsDictRaw,
                Root = new Geometry.RawDefinition { Guid = "ROOT", Name = "ROOT_MODEL", Builder = rootBuilder },
            };
        }
    }
}
