using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using Xunit;

namespace OpenSkp.Tests
{
    public class IfcConformanceAndCurvesTests
    {
        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        private static string EntityLine(string text, string token) =>
            text.Split(new[] { "\r\n", "\n" }, StringSplitOptions.None)
                .First(l => l.Contains(token));

        private static List<string> SplitAttributes(string line)
        {
            int first = line.IndexOf('(');
            int last = line.LastIndexOf(')');
            string body = line.Substring(first + 1, last - first - 1);
            var list = new List<string>();
            int depth = 0;
            var cur = new System.Text.StringBuilder();
            foreach (char ch in body)
            {
                if (ch == '(') depth++;
                else if (ch == ')') depth--;
                if (ch == ',' && depth == 0)
                {
                    list.Add(cur.ToString());
                    cur.Clear();
                }
                else
                {
                    cur.Append(ch);
                }
            }
            list.Add(cur.ToString());
            return list;
        }

        private static void AssertIdsAreDense(string text)
        {
            var ids = Regex.Matches(text, @"^#(\d+)=", RegexOptions.Multiline)
                .Cast<Match>()
                .Select(m => int.Parse(m.Groups[1].Value))
                .ToList();
            Assert.NotEmpty(ids);
            Assert.Equal(ids.Count, ids.Distinct().Count());
            Assert.Equal(ids.Count, ids.Max());
        }

        private static Scene MakeMeshScene(
            string name,
            float[] positions,
            uint[] indices,
            string layer = "Layer0",
            Dictionary<string, bool>? layerHidden = null,
            Dictionary<string, string>? props = null) =>
            new Scene
            {
                SceneHierarchy = new InstanceNode { Name = "Root", DefinitionName = "RootDef" },
                MeshIndex = new Dictionary<string, MeshMetadata>
                {
                    [name] = new MeshMetadata
                    {
                        Name = name,
                        Layer = layer,
                        Properties = props ?? new Dictionary<string, string>(),
                    }
                },
                GlbPrimitives = new List<GlbPrimitive>
                {
                    new GlbPrimitive
                    {
                        GeomName = name,
                        MaterialIndex = 0,
                        Positions = positions,
                        Normals = new float[positions.Length],
                        Uvs = new float[(positions.Length / 3) * 2],
                        Indices = indices,
                    }
                },
                LayerHidden = layerHidden ?? new Dictionary<string, bool>(),
            };

        [Fact]
        public void StepTextEscaping_And_CompressedGuidLeadingBits()
        {
            string guid = IfcExport.GenerateIfcGuid();
            Assert.Equal(22, guid.Length);
            Assert.Contains(guid[0], new[] { '0', '1', '2', '3' });

            string text = IfcExport.ToIfc(MakeMeshScene(
                "剪力墙 JLQ-1",
                new float[] { 0, 0, 0, 1, 0, 0, 0, 1, 0 },
                new uint[] { 0, 1, 2 },
                layer: "隐藏层",
                layerHidden: new Dictionary<string, bool> { ["隐藏层"] = true },
                props: new Dictionary<string, string> { ["名称"] = "承重墙", ["emoji"] = "a\U0001F600墙" }));

            Assert.DoesNotContain("剪力墙", text);
            Assert.Contains("\\X2\\526A529B5899\\X0\\ JLQ-1", text);
            Assert.Contains("\\X2\\540D79F0\\X0\\", text);
            Assert.Contains("\\X2\\627F91CD5899\\X0\\", text);
            Assert.Contains("a\\X4\\0001F60000005899\\X0\\", text);
            Assert.Contains(",.F.,.F.,.F.,())", EntityLine(text, "IFCPRESENTATIONLAYERWITHSTYLE("));
        }

        [Fact]
        public void EnumSpellings_And_PerEntityAttributeCounts()
        {
            string doorText = IfcExport.ToIfc(MakeMeshScene(
                "Front Door",
                new float[] { 0, 0, 0, 1, 0, 0, 0, 1, 0 },
                new uint[] { 0, 1, 2 }));

            Assert.DoesNotContain(".TRUE.", doorText);
            Assert.DoesNotContain(".FALSE.", doorText);
            Assert.DoesNotContain(".READWRITE.", doorText);
            Assert.DoesNotContain(".STERADIANUNIT.", doorText);
            Assert.Contains(".NOCHANGE.", doorText);
            Assert.Contains(".SOLIDANGLEUNIT.", doorText);
            Assert.Contains(".STERADIAN.", doorText);
            Assert.Equal(13, SplitAttributes(EntityLine(doorText, "IFCDOOR(")).Count);

            string wallText = IfcExport.ToIfc(MakeMeshScene(
                "Outer Wall",
                new float[] { 0, 0, 0, 1, 0, 0, 0, 1, 0 },
                new uint[] { 0, 1, 2 }));
            Assert.Equal(9, SplitAttributes(EntityLine(wallText, "IFCWALL(")).Count);

            string door2x3 = IfcExport.ToIfc(
                MakeMeshScene("Front Door", new float[] { 0, 0, 0, 1, 0, 0, 0, 1, 0 }, new uint[] { 0, 1, 2 }),
                IfcExport.MetresToMm,
                "IFC2X3");
            Assert.Equal(10, SplitAttributes(EntityLine(door2x3, "IFCDOOR(")).Count);
        }

        [Fact]
        public void ClosedFlag_ComputedFromWeldedManifoldTopology()
        {
            string ClosedFlag(string txt) =>
                SplitAttributes(EntityLine(txt, "IFCTRIANGULATEDFACESET("))[2];

            Assert.Equal(
                ".F.",
                ClosedFlag(IfcExport.ToIfc(MakeMeshScene(
                    "Open",
                    new float[] { 0, 0, 0, 1, 0, 0, 0, 1, 0 },
                    new uint[] { 0, 1, 2 }))));

            float[] perFaceTetra =
            {
                0, 0, 0, 1, 0, 0, 0, 1, 0,
                0, 0, 0, 1, 0, 0, 0, 0, 1,
                0, 0, 0, 0, 1, 0, 0, 0, 1,
                1, 0, 0, 0, 1, 0, 0, 0, 1,
            };
            Assert.Equal(
                ".T.",
                ClosedFlag(IfcExport.ToIfc(MakeMeshScene(
                    "PerFaceTetra",
                    perFaceTetra,
                    Enumerable.Range(0, 12).Select(i => (uint)i).ToArray()))));
        }

        [Fact]
        public void CurveSets_ExportAsIfcAnnotationAndInlineIfcArcIndex()
        {
            var polyScene = new Scene
            {
                SceneHierarchy = new InstanceNode { Name = "Root" },
                GlbPrimitives = new List<GlbPrimitive>(),
                CurveSets = new List<CurveSetMetadata>
                {
                    new CurveSetMetadata
                    {
                        Name = "ClosedOutline",
                        Layer = "Framing",
                        Path = "ROOT",
                        PointsM = new List<(double X, double Y, double Z)> { (0, 0, 0), (1, 0, 0), (0, 0, 1) },
                        Closed = true,
                        AttributeDictionaries = new Dictionary<string, Dictionary<string, string>>
                        {
                            ["frame-meta"] = new Dictionary<string, string> { ["mark"] = "B1" }
                        }
                    }
                }
            };
            string polyText = IfcExport.ToIfc(polyScene);
            AssertIdsAreDense(polyText);
            Assert.Contains("IFCGEOMETRICREPRESENTATIONSUBCONTEXT('Annotation','Model'", polyText);
            Assert.Contains("IFCANNOTATION(", polyText);
            Assert.Contains("'ClosedOutline'", polyText);
            Assert.Contains("'Pset_frame-meta'", polyText);
            Assert.Equal(4, Regex.Matches(SplitAttributes(EntityLine(polyText, "IFCPOLYLINE("))[0], @"#\d+").Count);

            var arcScene = new Scene
            {
                SceneHierarchy = new InstanceNode { Name = "Root" },
                GlbPrimitives = new List<GlbPrimitive>(),
                CurveSets = new List<CurveSetMetadata>
                {
                    new CurveSetMetadata
                    {
                        Name = "ArcRun",
                        Layer = "Layer0",
                        Path = "ROOT",
                        PointsM = new List<(double X, double Y, double Z)> { (1, 0, 0), (0, 0, -1) },
                        Closed = false,
                        Arc = new CurveArc
                        {
                            Points = new List<(double X, double Y, double Z)>
                            {
                                (1, 0, 0),
                                (Math.Sqrt(0.5), 0, -Math.Sqrt(0.5)),
                                (0, 0, -1),
                            },
                            Segments = new List<(int Start, int Mid, int End)> { (0, 1, 2) },
                        }
                    }
                }
            };
            string arcText = IfcExport.ToIfc(arcScene);
            AssertIdsAreDense(arcText);
            Assert.Contains("IFCINDEXEDPOLYCURVE(", arcText);
            Assert.Contains("IFCARCINDEX((1,2,3))", arcText);
            Assert.DoesNotMatch(@"^#\d+=IFCARCINDEX\(", arcText);

            string capillaIfc = IfcExport.ToIfc(SkpFile.BuildScene(FixturePath("capilla_quiroz_v17.skp")));
            AssertIdsAreDense(capillaIfc);
            Assert.Equal(20, Regex.Matches(capillaIfc, @"=IFCANNOTATION\(").Count);

            string arcsIfc = IfcExport.ToIfc(SkpFile.BuildScene(FixturePath("arcs_2021.skp")));
            AssertIdsAreDense(arcsIfc);
            Assert.Single(Regex.Matches(arcsIfc, @"=IFCINDEXEDPOLYCURVE\("));
            Assert.Contains("IFCARCINDEX((1,2,3))", arcsIfc);
        }

        [Fact]
        public void ControlCharactersEscaped_And_NonFiniteValuesRejected()
        {
            Assert.Equal(
                "line1\\X2\\000D000A\\X0\\line2\\X2\\00090000001F007F\\X0\\",
                IfcExport.StepText("line1\r\nline2\t\0\u001F\u007F"));

            string text = IfcExport.ToIfc(MakeMeshScene(
                "Wall\nOne\u007F",
                new float[] { 0, 0, 0, 1, 0, 0, 0, 1, 0 },
                new uint[] { 0, 1, 2 },
                props: new Dictionary<string, string> { ["note\tkey"] = "a\nb\u007F" }));
            Assert.Contains("Wall\\X2\\000A\\X0\\One\\X2\\007F\\X0\\", text);
            Assert.Contains("note\\X2\\0009\\X0\\key", text);
            Assert.Contains("a\\X2\\000A\\X0\\b\\X2\\007F\\X0\\", text);

            var lines = text.Split(new[] { "\r\n" }, StringSplitOptions.None);
            int dataStart = Array.IndexOf(lines, "DATA;") + 1;
            int dataEnd = Array.IndexOf(lines, "ENDSEC;", dataStart);
            for (int i = dataStart; i < dataEnd; i++)
            {
                Assert.StartsWith("#", lines[i]);
                Assert.EndsWith(");", lines[i]);
            }

            var good = MakeMeshScene("Wall", new float[] { 0, 0, 0, 1, 0, 0, 0, 1, 0 }, new uint[] { 0, 1, 2 });
            Assert.Throws<ArgumentException>(() => IfcExport.ToIfc(good, double.NaN));
            Assert.Throws<ArgumentException>(() => IfcExport.ToIfc(good, double.PositiveInfinity));

            var nanMesh = MakeMeshScene("Wall", new float[] { float.NaN, 0, 0, 1, 0, 0, 0, 1, 0 }, new uint[] { 0, 1, 2 });
            Assert.Throws<ArgumentException>(() => IfcExport.ToIfc(nanMesh));

            var infCurve = new Scene
            {
                SceneHierarchy = new InstanceNode { Name = "Root" },
                GlbPrimitives = new List<GlbPrimitive>(),
                CurveSets = new List<CurveSetMetadata>
                {
                    new CurveSetMetadata
                    {
                        Name = "C",
                        PointsM = new List<(double X, double Y, double Z)> { (0, 0, 0), (1e308, 0, 0) }
                    }
                }
            };
            Assert.Throws<ArgumentException>(() => IfcExport.ToIfc(infCurve, 1000.0));

            string tmpRoot = Path.Combine(Path.GetTempPath(), "openskp_ifc_nan_" + Guid.NewGuid().ToString("N"));
            string outPath = Path.Combine(tmpRoot, "nested", "bad.ifc");
            try
            {
                Assert.Throws<ArgumentException>(() => IfcExport.ExportIfc(nanMesh, outPath));
                Assert.False(File.Exists(outPath));
                Assert.False(Directory.Exists(Path.GetDirectoryName(outPath)!));
            }
            finally
            {
                if (Directory.Exists(tmpRoot)) Directory.Delete(tmpRoot, true);
            }
        }
    }
}
