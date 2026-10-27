using System;
using System.IO;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>
    /// A scene that doesn't save its camera (openskp#447).
    ///
    /// SketchUp lets a scene leave "Camera Location" unticked: such a scene
    /// saves no camera and still carries its name and the layers it hides. The
    /// 2021+ reader used to drop it.
    ///
    /// Fixture: scene_without_camera_2021.skp - one layer ("Furniture"), one
    /// face and three scenes built through SketchUp's own library: "With
    /// camera" (eye 100, -200, 80 looking at 5, 5, 0), "No camera"
    /// (SUSceneSetUseCamera off, hides Furniture) and "Plain" (no camera,
    /// hides nothing). SketchUp reports the same three scenes. No private
    /// content.
    /// </summary>
    public class SceneWithoutCameraTests
    {
        private static Page[] LoadPages() => SkpFile.Open(
            Path.Combine(AppContext.BaseDirectory, "fixtures", "scene_without_camera_2021.skp"))
            .Pages.ToArray();

        [Fact]
        public void EverySceneIsRead()
        {
            Assert.Equal(new[] { "With camera", "No camera", "Plain" }, LoadPages().Select(p => p.Name));
        }

        [Fact]
        public void ASavedCameraIsStillRead()
        {
            var pages = LoadPages();
            Assert.Equal((100.0, -200.0, 80.0), pages[0].Eye);
            Assert.Equal((5.0, 5.0, 0.0), pages[0].Target);
        }

        [Fact]
        public void ASceneWithoutACameraKeepsItsHiddenLayers()
        {
            var pages = LoadPages();
            Assert.Null(pages[1].Eye);
            Assert.Null(pages[1].Target);
            Assert.Null(pages[1].Up);
            Assert.Equal(new[] { "Furniture" }, pages[1].HiddenLayers);
            Assert.Null(pages[2].Eye);
            Assert.Empty(pages[2].HiddenLayers);
        }
    }
}
