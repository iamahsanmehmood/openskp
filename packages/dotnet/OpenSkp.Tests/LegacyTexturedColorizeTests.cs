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
    }
}
