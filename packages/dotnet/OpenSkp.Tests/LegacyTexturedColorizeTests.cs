using System;
using System.IO;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>
    /// Textured and colorized materials in a classic file read as SketchUp reads them.
    ///
    /// Fixture legacy_textured_colorize_2017.skp: four materials with the same 2x1 texture, made and saved as a
    /// SketchUp 2017 file by SketchUp's own library: plain (type 1, textured), tint and shift (type 2, colorized,
    /// colorize mode tint 1 and shift 0) and tint_half (a tint at opacity 0.5). Before, every classic textured
    /// material read as colorized and the mode was always tint (96 of 117 textured materials of the differential
    /// test files disagreed with SketchUp's own SUMaterialGetType). No private content.
    /// </summary>
    public class LegacyTexturedColorizeTests
    {
        [Fact]
        public void ReadsTheTextureTypeTheColorizeModeAndThePaintColour()
        {
            var model = SkpFile.Open(Path.Combine(AppContext.BaseDirectory, "fixtures", "legacy_textured_colorize_2017.skp"));
            var got = model.Materials.ToDictionary(
                m => m.Name,
                m => (m.Color.R, m.Color.G, m.Color.B, m.Colorized, m.ColorizeType, Math.Round(m.Transparency, 2)));
            Assert.Equal(4, got.Count);
            Assert.Equal((120, 40, 120, false, 0, 1.0), got["plain"]);
            Assert.Equal((30, 160, 60, true, 1, 1.0), got["tint"]);
            Assert.Equal((200, 90, 20, true, 0, 1.0), got["shift"]);
            Assert.Equal((10, 20, 230, true, 1, 0.5), got["tint_half"]);
        }

        // blender-openskp#9: a SketchUp 2017 textured material stores two colours side by side and the first
        // one's alpha byte can be 0 (as in the box the report attached). Taking the colour and its alpha from
        // the first made every textured material fully transparent in the Blender add-on. Here the first
        // colour of "plain" (120, 40, 120) gets alpha 0, as in that file.
        [Fact]
        public void ColourAlphaIsTheMaterialColoursNotTheImageAverages()
        {
            var data = File.ReadAllBytes(Path.Combine(AppContext.BaseDirectory, "fixtures", "legacy_textured_colorize_2017.skp"));
            var pair = new byte[] { 0x78, 0x28, 0x78, 0xFF, 0x00, 0x78, 0x28, 0x78, 0xFF }; // image average, 00, material colour
            int at = IndexOf(data, pair, 0);
            Assert.True(at > 0);
            Assert.Equal(-1, IndexOf(data, pair, at + 1));
            data[at + 3] = 0;
            var plain = SkpFile.Parse(data).Materials.Single(m => m.Name == "plain");
            Assert.Equal((120, 40, 120, 255), plain.Color);
        }

        private static int IndexOf(byte[] haystack, byte[] needle, int from)
        {
            for (int i = from; i + needle.Length <= haystack.Length; i++)
            {
                bool match = true;
                for (int j = 0; j < needle.Length && match; j++) match = haystack[i + j] == needle[j];
                if (match) return i;
            }
            return -1;
        }
    }
}
