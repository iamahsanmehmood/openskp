using System;
using System.IO;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>
    /// A leader text with any arrow type, and a font with a multi-byte pid, in a classic file
    /// (openskp#442).
    ///
    /// Fixture legacy_text_arrows_2017.skp: three leader texts written by openskp.create with arrow
    /// types 1 (dot), 2 (closed arrow) and 4 (open arrow) instead of the writer's 3, and a text font
    /// whose pid is two bytes, as in the real project's SketchUp 2017 to 2020 saves (a pid above 255 takes two bytes). SketchUp 2025 opens it and reports the same three texts with arrow types 1, 2 and 4.
    /// Before #442 this reader stopped on the first text. No private content.
    /// </summary>
    public class LegacyTextArrowsTests
    {
        [Fact]
        public void EveryTextIsRead()
        {
            var model = SkpFile.Open(Path.Combine(AppContext.BaseDirectory, "fixtures", "legacy_text_arrows_2017.skp"));
            Assert.Equal(new[] { "Dot", "Closed arrow", "Open arrow" }, model.Root.Texts.Select(t => t.Text));
        }
    }
}
