using System;
using System.IO;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>Coverage for OpenSkp.SkpEdit.OpenExisting, mirroring
    /// packages/python/tests/test_edit.py's own structure: a synthetic
    /// round-trip (build with Create.cs, reopen, compare structure) and an
    /// end-to-end run against the real-world fixture already used by
    /// LegacyTests.cs.</summary>
    public class EditTests
    {
        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        [Fact]
        public void RejectsNonLegacyOrMissingFile()
        {
            string path = Path.Combine(Path.GetTempPath(), Guid.NewGuid() + ".skp");
            File.WriteAllBytes(path, new byte[] { 1, 2, 3, 4, 5, 6, 7, 8 });
            try
            {
                Assert.Throws<SkpWriteException>(() => SkpEdit.OpenExisting(path));
            }
            finally
            {
                File.Delete(path);
            }
        }

        [Fact]
        public void SimpleFileRoundTripsThroughEdit()
        {
            var original = SkpCreate.NewFile();
            int red = original.AddMaterial("Red", (255, 0, 0, 255));
            int roof = original.AddLayer("Roof", (10, 20, 30));
            original.AddFace(
                new (double, double, double)[] { (0, 0, 0), (100, 0, 0), (100, 100, 0), (0, 100, 0) },
                material: red, layer: roof);

            string path = Path.Combine(Path.GetTempPath(), Guid.NewGuid() + ".skp");
            original.Save(path);
            try
            {
                var result = SkpEdit.OpenExisting(path);
                Assert.Empty(result.Warnings);
                Assert.True(result.Builder.MaterialsByName.ContainsKey("Red"));
                Assert.True(result.Builder.LayersByName.ContainsKey("Roof"));

                var reparsed = SkpFile.Parse(result.Builder.ToBytes());
                Assert.Single(reparsed.Root.Faces);
                var face = reparsed.Root.Faces.Values.Single();
                Assert.Equal(4, face.Loops[0].Count);
                Assert.Single(reparsed.Materials);
                Assert.Equal("Red", reparsed.Materials[0].Name);
                Assert.Contains(reparsed.Layers, l => l.Name == "Roof");
            }
            finally
            {
                File.Delete(path);
            }
        }

        [Fact]
        public void DefinitionsAndInstancesRoundTripThroughEdit()
        {
            var original = SkpCreate.NewFile();
            ComponentDefinitionBuilder chair;
            using (chair = original.AddComponentDefinition("Chair"))
            {
                chair.AddFace(new (double, double, double)[] { (0, 0, 0), (20, 0, 0), (20, 20, 0), (0, 20, 0) });
            }
            original.AddInstance(chair, translation: (100, 0, 0));
            original.AddInstance(chair, translation: (200, 0, 0));

            string path = Path.Combine(Path.GetTempPath(), Guid.NewGuid() + ".skp");
            original.Save(path);
            try
            {
                var result = SkpEdit.OpenExisting(path);
                Assert.True(result.Definitions.ContainsKey("Chair"));

                var reparsed = SkpFile.Parse(result.Builder.ToBytes());
                Assert.Single(reparsed.Definitions);
                Assert.Equal(2, reparsed.Root.Instances.Count);

                // The returned builder can place MORE instances of an
                // already-replayed definition.
                result.Builder.AddInstance(result.Definitions["Chair"], translation: (300, 0, 0));
                var reparsed2 = SkpFile.Parse(result.Builder.ToBytes());
                Assert.Equal(3, reparsed2.Root.Instances.Count);
            }
            finally
            {
                File.Delete(path);
            }
        }

        [Fact]
        public void GenuinelyEmptyInstanceNameIsPreservedNotReplaced()
        {
            // Found via cross-language analysis (2026-08-28): AddInstance's
            // own name-defaults-to-definition-name fallback and
            // ReplayInstance's own `IsNullOrEmpty(...) ? null : inst.Name`
            // both silently replaced a genuinely empty instance name with
            // its definition's name - a real difference, not cosmetic (a
            // later rename of the definition would no longer show
            // through). No dedicated regression test for this existed for
            // .NET specifically, unlike Python/TypeScript/Dart/C++.
            var original = SkpCreate.NewFile();
            ComponentDefinitionBuilder box;
            using (box = original.AddComponentDefinition("Box"))
            {
                box.AddFace(new (double, double, double)[] { (0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0) });
            }
            original.AddInstance(box, name: "", translation: (0, 0, 0));

            string path = Path.Combine(Path.GetTempPath(), Guid.NewGuid() + ".skp");
            original.Save(path);
            try
            {
                var source = SkpFile.Parse(File.ReadAllBytes(path));
                Assert.Equal("", source.Root.Instances[0].Name);

                var result = SkpEdit.OpenExisting(path);
                var rebuilt = SkpFile.Parse(result.Builder.ToBytes());
                Assert.Equal("", rebuilt.Root.Instances[0].Name);
            }
            finally
            {
                File.Delete(path);
            }
        }

        [Fact]
        public void GenuinelyEmptyDefinitionNameIsPreservedNotReplaced()
        {
            // Found via cross-language analysis (2026-08-28), same bug
            // class as the empty instance name case above:
            // `IsNullOrEmpty(defn.Name) ? $"Definition{defId}" : defn.Name`
            // silently replaced a genuinely empty definition name with a
            // fabricated one. SketchUp Groups are internally just unnamed
            // component definitions (unlike Components, which SketchUp
            // auto-names), so an empty name is common in real files.
            var original = SkpCreate.NewFile();
            ComponentDefinitionBuilder box;
            using (box = original.AddComponentDefinition(""))
            {
                box.AddFace(new (double, double, double)[] { (0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0) });
            }
            original.AddInstance(box, translation: (0, 0, 0));

            string path = Path.Combine(Path.GetTempPath(), Guid.NewGuid() + ".skp");
            original.Save(path);
            try
            {
                var source = SkpFile.Parse(File.ReadAllBytes(path));
                Assert.Equal("", source.Definitions.Values.First().Name);

                var result = SkpEdit.OpenExisting(path);
                var rebuilt = SkpFile.Parse(result.Builder.ToBytes());
                Assert.Equal("", rebuilt.Definitions.Values.First().Name);
            }
            finally
            {
                File.Delete(path);
            }
        }

        [Fact]
        public void NewMaterialAfterOpenExistingThrows()
        {
            var original = SkpCreate.NewFile();
            original.AddFace(new (double, double, double)[] { (0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0) });
            string path = Path.Combine(Path.GetTempPath(), Guid.NewGuid() + ".skp");
            original.Save(path);
            try
            {
                var result = SkpEdit.OpenExisting(path);
                // Root-level geometry replay always finalizes the
                // material/layer/definition sections, so a genuinely new
                // one can no longer be registered - matches edit.py's own
                // documented behavior.
                Assert.Throws<SkpWriteException>(() => result.Builder.AddMaterial("New", (1, 2, 3)));
            }
            finally
            {
                File.Delete(path);
            }
        }

        [Fact]
        public void HiddenAndMultiMaterialFaceRoundTripsThroughEdit()
        {
            var original = SkpCreate.NewFile();
            int red = original.AddMaterial("Red", (255, 0, 0));
            int blue = original.AddMaterial("Blue", (0, 0, 255));
            original.AddFace(
                new (double, double, double)[] { (0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0) },
                material: red, backMaterial: blue, hidden: true);

            string path = Path.Combine(Path.GetTempPath(), Guid.NewGuid() + ".skp");
            original.Save(path);
            try
            {
                var result = SkpEdit.OpenExisting(path);
                var reparsed = SkpFile.Parse(result.Builder.ToBytes());
                var face = reparsed.Root.Faces.Values.Single();
                Assert.True(face.Hidden);
                Assert.NotNull(face.MaterialId);
                Assert.NotNull(face.BackMaterialId);
            }
            finally
            {
                File.Delete(path);
            }
        }

        [Fact]
        public void ReplaysNestedAndRootConstructionGuides()
        {
            var original = SkpCreate.NewFile();
            ComponentDefinitionBuilder nested;
            using (nested = original.AddComponentDefinition("GuideOnly"))
            {
                nested.AddConstructionPoint((4, 5, 6));
                nested.AddConstructionLine((0, 0, 2), direction: (0, 1, 0));
                nested.AddConstructionLine((1, 1, 1), point2: (2, 2, 2));
            }
            original.AddInstance(nested);
            original.AddConstructionPoint((7, 8, 9));
            original.AddConstructionLine((0, 0, 0), point2: (10, 0, 0));

            string path = Path.Combine(Path.GetTempPath(), Guid.NewGuid() + ".skp");
            original.Save(path);
            try
            {
                var result = SkpEdit.OpenExisting(path);
                Assert.Empty(result.Warnings);
                Assert.True(result.Definitions.ContainsKey("GuideOnly"));

                var rebuilt = SkpFile.Parse(result.Builder.ToBytes());
                var rootCp = Assert.Single(rebuilt.Root.ConstructionPoints);
                Assert.Equal((7.0, 8.0, 9.0), rootCp.Position);
                var rootCl = Assert.Single(rebuilt.Root.ConstructionLines);
                Assert.Equal((10.0, 0.0, 0.0), rootCl.End!.Value);

                var group = Assert.Single(rebuilt.Definitions.Values, d => d.Name == "GuideOnly");
                var defCp = Assert.Single(group.ConstructionPoints);
                Assert.Equal((4.0, 5.0, 6.0), defCp.Position);
                Assert.Equal(2, group.ConstructionLines.Count);
                Assert.Null(group.ConstructionLines[0].Start);
                Assert.Null(group.ConstructionLines[0].End);
                Assert.Equal(1.0, group.ConstructionLines[1].Start!.Value.X, 6);
                Assert.Equal(2.0, group.ConstructionLines[1].End!.Value.X, 6);
            }
            finally
            {
                File.Delete(path);
            }
        }

        [Fact]
        public void SkipsDefinitionsWhoseOnlyGuidesCannotBeReplayed()
        {
            var original = SkpCreate.NewFile();
            ComponentDefinitionBuilder half;
            using (half = original.AddComponentDefinition("Half"))
            {
                half.AddConstructionLine((0, 0, 0), point2: (12345, 0, 0));
            }
            ComponentDefinitionBuilder zero;
            using (zero = original.AddComponentDefinition("Zero"))
            {
                zero.AddConstructionLine((0, 0, 0), point2: (54321, 0, 0));
            }
            original.AddFace(new[] { (0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 10.0, 0.0) });
            original.AddInstance(half);
            original.AddInstance(zero);

            byte[] raw = original.ToBytes();
            void PatchF64(double target, double replacement)
            {
                byte[] needle = BitConverter.GetBytes(target);
                byte[] repl = BitConverter.GetBytes(replacement);
                for (int i = 0; i + 8 <= raw.Length; i++)
                {
                    bool match = true;
                    for (int j = 0; j < 8; j++)
                    {
                        if (raw[i + j] != needle[j]) { match = false; break; }
                    }
                    if (match)
                    {
                        Buffer.BlockCopy(repl, 0, raw, i, 8);
                        return;
                    }
                }
                throw new InvalidOperationException($"f64 {target} not found");
            }
            PatchF64(12345.0, 1e30);
            PatchF64(54321.0, 0.0);

            string path = Path.Combine(Path.GetTempPath(), Guid.NewGuid() + ".skp");
            File.WriteAllBytes(path, raw);
            try
            {
                var result = SkpEdit.OpenExisting(path);
                Assert.False(result.Definitions.ContainsKey("Half"));
                Assert.False(result.Definitions.ContainsKey("Zero"));
                Assert.Contains(result.Warnings, w => w.Contains("definition 'Half': skipped"));
                Assert.Contains(result.Warnings, w => w.Contains("definition 'Zero': skipped"));

                var rebuilt = SkpFile.Parse(result.Builder.ToBytes());
                Assert.Single(rebuilt.Root.Faces);
                Assert.Empty(rebuilt.Definitions);
            }
            finally
            {
                File.Delete(path);
            }
        }

        [Fact]
        public void ReplaysGuideHiddenFlagAndLayer()
        {
            var original = SkpCreate.NewFile();
            int guides = original.AddLayer("Guides");
            ComponentDefinitionBuilder nested;
            using (nested = original.AddComponentDefinition("NestedGuides"))
            {
                nested.AddConstructionPoint((2, 3, 4), layer: guides, hidden: true);
                nested.AddConstructionLine((0, 0, 0), point2: (5, 0, 0), layer: guides, hidden: false);
            }
            original.AddInstance(nested);
            original.AddConstructionPoint((1, 2, 3), hidden: true);
            original.AddConstructionLine((0, 0, 0), point2: (12, 0, 0), layer: guides, hidden: true);

            string path = Path.Combine(Path.GetTempPath(), Guid.NewGuid() + ".skp");
            original.Save(path);
            try
            {
                var result = SkpEdit.OpenExisting(path);
                Assert.Empty(result.Warnings);
                var rebuilt = SkpFile.Parse(result.Builder.ToBytes());

                Assert.True(rebuilt.Root.ConstructionPoints[0].Hidden);
                Assert.Equal("", rebuilt.Root.ConstructionPoints[0].Layer);
                Assert.True(rebuilt.Root.ConstructionLines[0].Hidden);
                Assert.Equal("Guides", rebuilt.Root.ConstructionLines[0].Layer);

                var defn = Assert.Single(rebuilt.Definitions.Values, d => d.Name == "NestedGuides");
                Assert.Equal("Guides", defn.ConstructionPoints[0].Layer);
                Assert.True(defn.ConstructionPoints[0].Hidden);
                Assert.Equal("Guides", defn.ConstructionLines[0].Layer);
                Assert.False(defn.ConstructionLines[0].Hidden);
            }
            finally
            {
                File.Delete(path);
            }
        }

        [Fact]
        public void RealWorldFixtureOpensAndReplaysCleanly()
        {
            var result = SkpEdit.OpenExisting(FixturePath("capilla_quiroz_v17.skp"));

            // Every material/layer the source had is reachable without a
            // separate lookup.
            Assert.Equal(16, result.Builder.MaterialsByName.Count);
            Assert.True(result.Builder.LayersByName.ContainsKey("Layer0"));
            Assert.True(result.Definitions.ContainsKey("puerta"));
            Assert.True(result.Definitions.ContainsKey("grada"));

            var bytes = result.Builder.ToBytes();
            var reparsed = SkpFile.Parse(bytes);

            Assert.Equal(2, reparsed.Definitions.Count);
            Assert.Equal(3, reparsed.Root.Instances.Count); // 2x grada, 1x puerta
            Assert.Equal(16, reparsed.Materials.Count);

            var puerta = reparsed.Definitions.Values.Single(d => d.Name == "puerta");
            Assert.Equal(24, puerta.Faces.Count);

            var grada = reparsed.Definitions.Values.Single(d => d.Name == "grada");
            Assert.Equal(11, grada.Faces.Count);
        }

        [Fact]
        public void PreservesTextureFilenamesIncludingEmptyAndNeverEmbedsTempFolder()
        {
            string tempDir = Path.GetTempPath().TrimEnd('\\', '/');
            byte[] asciiNeedle = System.Text.Encoding.UTF8.GetBytes(tempDir);
            byte[] utf16Needle = System.Text.Encoding.Unicode.GetBytes(tempDir);

            static bool ContainsSubsequence(byte[] haystack, byte[] needle)
            {
                if (needle.Length == 0 || haystack.Length < needle.Length) return false;
                for (int i = 0; i <= haystack.Length - needle.Length; i++)
                {
                    bool match = true;
                    for (int j = 0; j < needle.Length; j++)
                    {
                        if (haystack[i + j] != needle[j]) { match = false; break; }
                    }
                    if (match) return true;
                }
                return false;
            }

            byte[] pngBytes = new byte[] { 0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 1, 2, 3, 4 };
            var square = new (double, double, double)[] { (0, 0, 0), (100, 0, 0), (100, 100, 0), (0, 100, 0) };
            string pngPath = Path.Combine(Path.GetTempPath(), "openskp_667_" + Guid.NewGuid() + ".png");
            string srcPath = Path.Combine(Path.GetTempPath(), "openskp_667_src_" + Guid.NewGuid() + ".skp");
            File.WriteAllBytes(pngPath, pngBytes);
            try
            {
                var defBuilder = SkpCreate.NewFile();
                int defMat = defBuilder.AddTextureMaterial("DefaultMat", pngPath);
                defBuilder.AddFace(square, material: defMat);
                var defModel = SkpFile.Parse(defBuilder.ToBytes());
                Assert.Equal(pngPath, defModel.Materials[0].Texture!.Filename);

                var srcBuilder = SkpCreate.NewFile();
                int m1 = srcBuilder.AddTextureMaterial("Concrete", pngPath, filename: "Polished Concrete Old.jpg");
                int m2 = srcBuilder.AddTextureMaterial("Nameless", pngPath, filename: "");
                srcBuilder.AddImage(pngPath, 20.0, 10.0, filename: "Poster.png");
                srcBuilder.AddFace(square, material: m1);
                srcBuilder.AddFace(new (double, double, double)[] { (200, 0, 0), (300, 0, 0), (300, 100, 0), (200, 100, 0) }, material: m2);
                srcBuilder.Save(srcPath);

                var orig = SkpFile.Parse(File.ReadAllBytes(srcPath));
                Assert.Equal("Poster.png", orig.Materials.Single(m => m.Name == "__openskp_image_2").Texture!.Filename);
                Assert.Equal("Nameless.png", orig.Materials.Single(m => m.Name == "Nameless").Texture!.Filename);

                var edited = SkpEdit.OpenExisting(srcPath);
                byte[] savedBytes = edited.Builder.ToBytes();
                Assert.False(ContainsSubsequence(savedBytes, asciiNeedle));
                Assert.False(ContainsSubsequence(savedBytes, utf16Needle));

                var rebuilt = SkpFile.Parse(savedBytes);
                Assert.Equal(
                    orig.Materials.ToDictionary(m => m.Name, m => m.Texture!.Filename),
                    rebuilt.Materials.ToDictionary(m => m.Name, m => m.Texture!.Filename));

                var capOrig = SkpFile.Open(FixturePath("capilla_quiroz_v17.skp"));
                var capEdited = SkpEdit.OpenExisting(FixturePath("capilla_quiroz_v17.skp"));
                byte[] capBytes = capEdited.Builder.ToBytes();
                Assert.False(ContainsSubsequence(capBytes, asciiNeedle));
                Assert.False(ContainsSubsequence(capBytes, utf16Needle));
                var capRebuilt = SkpFile.Parse(capBytes);
                Assert.Equal(
                    capOrig.Materials.Where(m => m.Texture != null).ToDictionary(m => m.Name, m => m.Texture!.Filename),
                    capRebuilt.Materials.Where(m => m.Texture != null).ToDictionary(m => m.Name, m => m.Texture!.Filename));
            }
            finally
            {
                if (File.Exists(pngPath)) File.Delete(pngPath);
                if (File.Exists(srcPath)) File.Delete(srcPath);
            }
        }
    }
}
