using System;
using System.IO;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>
    /// Real-file regression tests for the modern (2021+) VFF/ZIP reader,
    /// mirroring the values already cross-validated against Python in the
    /// TypeScript port's integration.test.ts.
    /// </summary>
    public class IntegrationTests
    {
        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        [Fact]
        public void ParsesUntitledSkpCorrectly()
        {
            var model = SkpFile.Open(FixturePath("Untitled.skp"));

            Assert.Equal("{25.0.575}", model.Version);
            Assert.Equal("Millimeter", model.Units);

            Assert.Equal(14, model.Layers.Count);
            var expectedLayers = new[]
            {
                "Layer0", "BottomPlate", "TopPlate", "Stud", "Nog", "KingStud",
                "HeaderJackStud", "HeaderPlate1", "HeaderPlate2", "SillPlate1",
                "VerticalHeaderStud", "generic_frame", "dimension", "Hat Sections",
            };
            var parsedLayers = model.Layers.Select(l => l.Name).ToList();
            foreach (var name in expectedLayers)
            {
                Assert.Contains(name, parsedLayers);
            }

            var layer0 = model.Layers.FirstOrDefault(l => l.Name == "Layer0");
            Assert.NotNull(layer0);

            // VFF files carry no known layer-visibility tag - always false here.
            Assert.All(model.Layers, l => Assert.False(l.Hidden));

            Assert.Equal(15, model.Materials.Count);
            var expectedMaterials = new[]
            {
                "*", "Layer_Layer0", "Layer_BottomPlate", "Layer_TopPlate", "Layer_Stud",
                "Layer_Nog", "Layer_KingStud", "Layer_HeaderJackStud", "Layer_HeaderPlate1",
                "Layer_HeaderPlate2", "Layer_SillPlate1", "Layer_VerticalHeaderStud",
                "Layer_generic_frame", "Layer_dimension", "Layer_Hat Sections",
            };
            var parsedMaterials = model.Materials.Select(m => m.Name).ToList();
            foreach (var name in expectedMaterials)
            {
                Assert.Contains(name, parsedMaterials);
            }

            var matLayer0 = model.Materials.FirstOrDefault(m => m.Name == "Layer_Layer0");
            Assert.NotNull(matLayer0);
            // Real data: none of this fixture's materials have useTrans="1"
            // set, so all correctly read fully opaque.
            Assert.Equal(1.0, matLayer0!.Transparency);
            Assert.Null(matLayer0.Id);
            Assert.Null(matLayer0.Texture);
            Assert.False(matLayer0.Colorized);
            Assert.Equal(0, matLayer0.ColorizeType);

            Assert.Equal(46, model.Definitions.Count);

            Assert.True(model.Definitions.TryGetValue(66, out var def66));
            Assert.Equal("Group200#2", def66!.Name);
            Assert.Equal(32, def66.Guid.Length);

            Assert.Equal(136, def66.Vertices.Count);
            Assert.Equal(158, def66.Edges.Count);
            Assert.Equal(26, def66.Faces.Count);

            var firstFace = def66.Faces.Values.First();
            Assert.NotEmpty(firstFace.Loops);
            Assert.NotEmpty(firstFace.Loops[0]);
            Assert.Null(firstFace.BackMaterialId);
            Assert.Null(firstFace.UvTransform);
            Assert.Null(firstFace.UvTransformBack);
            Assert.NotNull(firstFace.Normal);
            // Real data: every face in this fixture is visible - D307's
            // flag byte reads the plain baseline (0x06) throughout.
            Assert.All(def66.Faces.Values, f => Assert.False(f.Hidden));

            var firstEdge = def66.Edges.Values.First();
            Assert.False(firstEdge.Soft);
            Assert.False(firstEdge.Smooth);
            Assert.False(firstEdge.Hidden);

            // Instance Layer/Properties (item 17): previously always ""
            // / {} - declared but never assigned. Now genuinely
            // populated from each instance's own D207 (layer
            // override)/DC05 (dynamic properties) TLV children, matching
            // C++'s existing behavior. Cross-checked directly against
            // Python's independent parse of this same fixture.
            var battens = model.Root.Instances.Where(i => i.Name == "BattenHatSection_1").ToList();
            Assert.NotEmpty(battens);
            Assert.Equal("Hat Sections", battens[0].Layer);
            var w1 = model.Root.Instances.Where(i => i.Name == "W1").ToList();
            Assert.NotEmpty(w1);
            Assert.Equal("SteelFramer::Engine::PanelGenerator", w1[0].Properties["generator"]);
            Assert.Equal("362S200-43", w1[0].Properties["profile"]);

            Assert.False(def66.IsImage);
            Assert.False(def66.AlwaysFacesCamera);
            Assert.False(def66.ShadowsFaceSun);

            // materialsById join: TLV material ID 26180 resolves to the
            // default "*" material, and the resolved object is the SAME
            // instance held in model.Materials (the join shares identity).
            Assert.True(model.MaterialsById.TryGetValue(26180, out var joined));
            Assert.Equal("*", joined!.Name);
            Assert.Same(model.Materials.First(m => m.Name == "*"), joined);
            Assert.Equal(26180, joined.Id);

            // Real style data: this fixture bundles two style.xml files (the
            // second is SketchUp's "_1" duplicate-naming convention), both
            // named "[Construction Documentation Style]" with the same
            // front/back colors.
            Assert.Equal(2, model.Styles.Count);
            Assert.Equal("[Construction Documentation Style]", model.Styles[0].Name);
            Assert.Equal((255, 255, 255), model.Styles[0].FrontColor);
            Assert.Equal((208, 209, 189), model.Styles[0].BackColor);

            // BuildScene/MeshIndex - a separate, opt-in step from Parse(), so
            // it never costs a plain Parse() call anything. TS/Dart/C++ all
            // have this real-fixture coverage already; .NET's stopped at
            // parsing.
            var scene = SkpFile.BuildScene(FixturePath("Untitled.skp"));
            Assert.NotNull(scene.SceneHierarchy);
            Assert.Equal("ROOT", scene.SceneHierarchy.Name);
            Assert.Equal("ROOT_MODEL", scene.SceneHierarchy.DefinitionName);
            Assert.True(scene.SceneHierarchy.Children.Count > 0);

            Assert.Equal(43, scene.MeshIndex.Count);
            var firstMesh = scene.MeshIndex.Values.First();
            Assert.NotNull(firstMesh.Name);
            Assert.NotNull(firstMesh.Layer);
        }

        [Fact]
        public void ParsesSuFileSkpCorrectly()
        {
            var model = SkpFile.Open(FixturePath("SU_File.skp"));

            Assert.Equal("{25.0.575}", model.Version);

            Assert.Single(model.Layers);
            Assert.Equal("Layer0", model.Layers[0].Name);

            Assert.Single(model.Materials);
            Assert.Equal("Layer_Layer0", model.Materials[0].Name);

            // Only ROOT holds geometry in this fixture, so the numeric
            // Definitions map (which excludes ROOT) is empty.
            Assert.Empty(model.Definitions);
            Assert.Equal("ROOT_MODEL", model.Root.Name);

            var scene = SkpFile.BuildScene(FixturePath("SU_File.skp"));
            Assert.NotNull(scene.SceneHierarchy);
            Assert.Equal("ROOT", scene.SceneHierarchy.Name);
            Assert.Equal("ROOT_MODEL", scene.SceneHierarchy.DefinitionName);

            Assert.Single(scene.MeshIndex);
            Assert.Equal("ROOT_MODEL", scene.MeshIndex.Values.First().DefinitionName);
        }

        [Fact]
        public void NullBufferThrowsArgumentNullException()
        {
            byte[] nullBuffer = null!;
            Assert.Throws<ArgumentNullException>(() => SkpFile.Parse(nullBuffer));
            Assert.Throws<ArgumentNullException>(() => SkpFile.BuildScene(nullBuffer));
        }
    }
}
