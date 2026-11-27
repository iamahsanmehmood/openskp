using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Xunit;

namespace OpenSkp.Tests
{
    public class BlankDocumentTests
    {
        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        private static string FindOpenSkpSourceDir()
        {
            foreach (var start in new[] { AppContext.BaseDirectory, Directory.GetCurrentDirectory() })
            {
                var dir = new DirectoryInfo(start);
                while (dir != null)
                {
                    var candidate = Path.Combine(dir.FullName, "OpenSkp", "OpenSkp.csproj");
                    if (File.Exists(candidate))
                    {
                        return Path.Combine(dir.FullName, "OpenSkp");
                    }
                    var sibling = Path.Combine(dir.FullName, "packages", "dotnet", "OpenSkp", "OpenSkp.csproj");
                    if (File.Exists(sibling))
                    {
                        return Path.Combine(dir.FullName, "packages", "dotnet", "OpenSkp");
                    }
                    dir = dir.Parent;
                }
            }
            throw new DirectoryNotFoundException("Could not locate packages/dotnet/OpenSkp source directory.");
        }

        [Fact]
        public void BuildBlankV17MatchesFixtureByteForByte()
        {
            var doc = BlankDocumentBuilder.BuildBlankV17();
            byte[] expected = File.ReadAllBytes(FixturePath("blank_v17.skp"));

            Assert.Equal(5796, doc.Data.Length);
            using var sha = SHA256.Create();
            string digest = BitConverter.ToString(sha.ComputeHash(doc.Data)).Replace("-", "").ToLowerInvariant();
            Assert.Equal(BlankDocumentBuilder.ScaffoldSha256, digest);
            Assert.Equal(expected, doc.Data);

            Assert.Equal(52, doc.ModelGuidOffset);
            Assert.Equal(1987, doc.PidCounterOffset);
            Assert.Equal(18, doc.NextPid);
            Assert.Equal(2008, doc.NoScaleMaskOffset);
            Assert.Equal(3515, doc.TailPos);
            Assert.Equal(11, doc.ScaffoldNextSlot);
            Assert.Equal(58, BlankDocumentBuilder.VersionMapV17.Length);
        }

        [Fact]
        public void BuiltBlankDocumentReadsBackInClassicReader()
        {
            var doc = BlankDocumentBuilder.BuildBlankV17();

            // 1. Verify header and CVersionMap table directly from the built stream.
            int pos = 0;
            string ReadUtf16()
            {
                Assert.Equal(0xFF, doc.Data[pos]);
                Assert.Equal(0xFE, doc.Data[pos + 1]);
                Assert.Equal(0xFF, doc.Data[pos + 2]);
                int charLen = doc.Data[pos + 3];
                pos += 4;
                string s = Encoding.Unicode.GetString(doc.Data, pos, charLen * 2);
                pos += charLen * 2;
                return s;
            }

            Assert.Equal("SketchUp Model", ReadUtf16());
            Assert.Equal("{17.0.1}", ReadUtf16());
            pos += 16; // model GUID
            Assert.Equal("", ReadUtf16());
            pos += 4; // save timestamp
            Assert.Equal(0xFFFF, BitConverter.ToUInt16(doc.Data, pos));
            Assert.Equal(0, BitConverter.ToUInt16(doc.Data, pos + 2));
            ushort vmapNameLen = BitConverter.ToUInt16(doc.Data, pos + 4);
            pos += 6;
            Assert.Equal("CVersionMap", Encoding.ASCII.GetString(doc.Data, pos, vmapNameLen));
            pos += vmapNameLen;

            var parsedVmap = new List<(string Name, uint Schema)>();
            while (true)
            {
                string cls = ReadUtf16();
                if (cls == "End-Of-Version-Map")
                {
                    break;
                }
                uint schema = BitConverter.ToUInt32(doc.Data, pos);
                pos += 4;
                parsedVmap.Add((cls, schema));
            }
            Assert.Equal(BlankDocumentBuilder.VersionMapV17, parsedVmap.ToArray());

            // 2. Parse with the classic reader and verify model state.
            var model = SkpFile.Parse(doc.Data);
            Assert.Equal("{17.0.1}", model.Version);
            Assert.Single(model.Layers);
            Assert.Equal("Layer0", model.Layers[0].Name);
            Assert.False(model.Layers[0].Hidden);
            Assert.Equal((255, 84, 84), (model.Layers[0].ColorR, model.Layers[0].ColorG, model.Layers[0].ColorB));
            Assert.Empty(model.Materials);
            Assert.Empty(model.Definitions);
            Assert.Empty(model.Root.Faces);
            Assert.Empty(model.Root.Edges);
            Assert.Empty(model.Root.Vertices);
            Assert.Empty(model.Root.Instances);
            Assert.Empty(model.Pages);
            Assert.Equal(0, model.UnreadRootEntities);
            Assert.Equal((225, 225, 200), model.FaceFrontColor);
            Assert.Equal((128, 128, 200), model.FaceBackColor);
            Assert.False(model.ColorByTag);
            Assert.Empty(model.AttributeDictionaries);
        }

        [Fact]
        public void SourceTreeAndAssemblyContainNoTemplateFileOrBytes()
        {
            // No embedded resources in OpenSkp.dll.
            Assert.Empty(typeof(SkpCreate).Assembly.GetManifestResourceNames());

            byte[] templateBytes = BlankDocumentBuilder.BuildBlankV17().Data;
            string templateHex = BitConverter.ToString(templateBytes).Replace("-", "").ToLowerInvariant();
            string templateBase64 = Convert.ToBase64String(templateBytes);

            string srcDir = FindOpenSkpSourceDir();
            var files = Directory.EnumerateFiles(srcDir, "*", SearchOption.AllDirectories)
                .Where(p =>
                {
                    string rel = Path.GetRelativePath(srcDir, p);
                    string[] parts = rel.Split(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
                    return !parts.Contains("bin", StringComparer.OrdinalIgnoreCase) &&
                           !parts.Contains("obj", StringComparer.OrdinalIgnoreCase);
                })
                .ToList();

            Assert.NotEmpty(files);
            foreach (string file in files)
            {
                Assert.NotEqual(".skp", Path.GetExtension(file).ToLowerInvariant());

                byte[] raw = File.ReadAllBytes(file);
                Assert.False(ContainsSubsequence(raw, templateBytes), $"Raw template bytes found in {file}");

                string text = Encoding.UTF8.GetString(raw);
                var compact = new StringBuilder(text.Length);
                foreach (char c in text)
                {
                    if ((c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z') ||
                        (c >= 'a' && c <= 'z') || c == '+' || c == '/' || c == '=')
                    {
                        compact.Append(c);
                    }
                }
                string compactStr = compact.ToString();
                Assert.DoesNotContain(templateBase64, compactStr, StringComparison.Ordinal);
                Assert.DoesNotContain(templateHex, compactStr.ToLowerInvariant(), StringComparison.Ordinal);

                byte[] hexTokens = ExtractHexByteTokens(text);
                Assert.False(ContainsSubsequence(hexTokens, templateBytes), $"Hex byte array of template found in {file}");
            }
        }

        private static bool ContainsSubsequence(byte[] haystack, byte[] needle)
        {
            if (needle.Length == 0 || haystack.Length < needle.Length)
            {
                return false;
            }
            for (int i = 0; i <= haystack.Length - needle.Length; i++)
            {
                if (haystack[i] != needle[0])
                {
                    continue;
                }
                bool match = true;
                for (int j = 1; j < needle.Length; j++)
                {
                    if (haystack[i + j] != needle[j])
                    {
                        match = false;
                        break;
                    }
                }
                if (match)
                {
                    return true;
                }
            }
            return false;
        }

        private static byte[] ExtractHexByteTokens(string text)
        {
            var bytes = new List<byte>();
            for (int i = 0; i + 3 < text.Length; i++)
            {
                if (text[i] == '0' && (text[i + 1] == 'x' || text[i + 1] == 'X'))
                {
                    int hi = HexVal(text[i + 2]);
                    int lo = HexVal(text[i + 3]);
                    bool nextIsHex = (i + 4 < text.Length) && HexVal(text[i + 4]) >= 0;
                    if (hi >= 0 && lo >= 0 && !nextIsHex)
                    {
                        bytes.Add((byte)((hi << 4) | lo));
                        i += 3;
                    }
                }
            }
            return bytes.ToArray();
        }

        private static int HexVal(char c)
        {
            if (c >= '0' && c <= '9') return c - '0';
            if (c >= 'a' && c <= 'f') return c - 'a' + 10;
            if (c >= 'A' && c <= 'F') return c - 'A' + 10;
            return -1;
        }
    }
}
