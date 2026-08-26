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
            /// <summary>The model's unit-system string (e.g. "Millimeter"),
            /// read from meta/meta.dat. Null for legacy files or when the
            /// tag isn't found.</summary>
            public string? Units = null;
            public Dictionary<string, (int R, int G, int B)> LayerColors = new Dictionary<string, (int, int, int)>();
            // Modern (VFF) files derive layers from Layer_<name>-prefixed
            // materials, which carry no visibility flag of their own -
            // unlike legacy MFC files, there is currently no known tag
            // exposing a VFF layer's hidden state, so every VFF layer
            // defaults to visible.
            public Dictionary<string, bool> LayerHidden = new Dictionary<string, bool>();
            public Dictionary<long, string> LayerIdToName = new Dictionary<long, string>();
            public List<RawPage> Pages = new List<RawPage>();
            public List<RawDimension> Dimensions = new List<RawDimension>();
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
            var layerHidden = new Dictionary<string, bool>();
            var materials = new Dictionary<string, Geometry.RawMaterial>();
            var materialsByFolder = new Dictionary<string, Geometry.RawMaterial>();

            foreach (var entry in zip.Entries)
            {
                string name = entry.FullName;
                if (name.EndsWith("material.xml", StringComparison.Ordinal) && name.StartsWith("materials/", StringComparison.Ordinal))
                {
                    Vff.ValidateEntrySize(entry);
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
                        mat = Geometry.ParseMaterialXml(zip, name, xmlData, options);
                    }
                    catch (Exception e)
                    {
                        mat = null;
                        Observability.Log(options, SkpLogLevel.Debug, $"Failed to parse material.xml {name}: {e.Message}");
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
                            layerHidden[mat.Name.Substring(6)] = false;
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
                Vff.ValidateEntrySize(entry);
                byte[] xmlData;
                using (var s = entry.Open())
                using (var ms = new System.IO.MemoryStream())
                {
                    s.CopyTo(ms);
                    xmlData = ms.ToArray();
                }
                var style = Geometry.ParseStyleXml(xmlData, name, options);
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
            Vff.ValidateEntrySize(modelDatEntry);
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
            var vertexPositions = new Dictionary<string, (double X, double Y, double Z)>();
            var instanceWorld = new Dictionary<string, List<double>?>();
            TlvNode? pageNode = null;

            foreach (var rec in Tlv.IterTopLevelLazy(modelDat, 0, modelDat.Length, Tlv.ContainerTags))
            {
                var el = rec.Node;
                try
                {
                    var single = new List<TlvNode> { el };
                    Geometry.CollectLayers(single, layerIdToName);
                    Geometry.CollectMaterialIds(single, materialIdToName);
                    Geometry.CollectDefs(single, defsDictRaw);
                    PagesDimensions.ScanVertexPositions(el, vertexPositions);
                    PagesDimensions.ScanInstanceTransforms(el, instanceWorld);
                    if (pageNode == null)
                    {
                        pageNode = PagesDimensions.FindPageNode(el);
                    }
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

            // Units (meta/meta.dat) - VFF-only; legacy files carry no
            // equivalent container.
            string? units = null;
            var metaDatEntry = zip.GetEntry("meta/meta.dat");
            if (metaDatEntry != null)
            {
                try
                {
                    Vff.ValidateEntrySize(metaDatEntry);
                    byte[] metaData;
                    using (var s = metaDatEntry.Open())
                    using (var ms = new System.IO.MemoryStream())
                    {
                        s.CopyTo(ms);
                        metaData = ms.ToArray();
                    }
                    units = Vff.ReadMetaUnits(metaData);
                }
                catch (Exception e)
                {
                    units = null;
                    Observability.Log(options, SkpLogLevel.Debug, $"Failed to read units from meta/meta.dat: {e.Message}");
                }
            }

            if (!layerIdToName.ContainsKey(1))
            {
                layerIdToName[1] = "Layer0";
            }
            if (!layerColors.ContainsKey("Layer0"))
            {
                layerColors["Layer0"] = (136, 136, 136);
            }
            if (!layerHidden.ContainsKey("Layer0"))
            {
                layerHidden["Layer0"] = false;
            }

            return new RawParsed
            {
                Version = version,
                Units = units,
                LayerColors = layerColors,
                LayerHidden = layerHidden,
                LayerIdToName = layerIdToName,
                Pages = PagesDimensions.ParsePages(pageNode),
                Dimensions = PagesDimensions.ParseDimensions(modelDat, vertexPositions, instanceWorld),
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
