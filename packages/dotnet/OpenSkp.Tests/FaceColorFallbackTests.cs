using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>
    /// What an unpainted face ends up wearing.
    ///
    /// SketchUp resolves it in three steps: the face's own material, then the material painted
    /// onto the group or component that places it, then the active style's front face color.
    /// Both scene builders skipped the middle step for textures - an inherited material was
    /// reduced to its average RGB, so the image and its tile size were lost - and used the tag
    /// (layer) color for the last one, which is what SketchUp shows in "Color by Tag" mode
    /// rather than what the modeller sees.
    /// </summary>
    public class FaceColorFallbackTests
    {
        private static string FixturePath(string name) =>
            Path.Combine(AppContext.BaseDirectory, "fixtures", name);

        private static readonly (double X, double Y, double Z)[] _quad =
        {
            (0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0),
        };

        private static byte[] TinyPng() => Convert.FromBase64String(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=");

        private static SkpParseOptions StyleColor() => new SkpParseOptions { UseStyleFaceColor = true };

        private sealed record Pbr(double[] BaseColorFactor, int? TextureIndex);

        private static List<Pbr> Materials(Scene scene) => Materials(scene.GltfMaterials);

        private static List<Pbr> Materials(InstancedScene scene) => Materials(scene.GltfMaterials);

        private static List<Pbr> Materials(List<object> gltfMaterials) =>
            gltfMaterials.Select(entry =>
            {
                using var doc = JsonDocument.Parse(JsonSerializer.Serialize(entry));
                var pbr = doc.RootElement.GetProperty("pbrMetallicRoughness");
                var factor = pbr.GetProperty("baseColorFactor").EnumerateArray().Select(v => v.GetDouble()).ToArray();
                int? tex = pbr.TryGetProperty("baseColorTexture", out var t)
                    ? t.GetProperty("index").GetInt32()
                    : null;
                return new Pbr(factor, tex);
            }).ToList();

        private static (int R, int G, int B) Rgb(Pbr material) => (
            (int)Math.Round(material.BaseColorFactor[0] * 255),
            (int)Math.Round(material.BaseColorFactor[1] * 255),
            (int)Math.Round(material.BaseColorFactor[2] * 255));

        /// <summary>Every color the placed geometry actually wears, deduplicated.</summary>
        private static HashSet<(int R, int G, int B)> PlacedColors(byte[] skp, SkpParseOptions options)
        {
            var scene = SkpFile.BuildScene(skp, options);
            var materials = Materials(scene);
            return scene.GlbPrimitives
                .Where(primitive => primitive.Indices.Length > 0)
                .Select(primitive => Rgb(materials[primitive.MaterialIndex]))
                .ToHashSet();
        }

        private static HashSet<(int R, int G, int B)> PlacedColors(string fixturePath, SkpParseOptions options) =>
            PlacedColors(File.ReadAllBytes(fixturePath), options);

        // -- the style's front face color, instead of the tag color ---------------------------

        [Fact]
        public void Default_PaintsUnpaintedFacesWithTheirTagColor()
        {
            // Untitled.skp is a timber frame: every member is unpainted and sorted onto a tag,
            // so the whole model comes out in the tag palette - blue plates, orange king studs.
            var colors = PlacedColors(FixturePath("Untitled.skp"), new SkpParseOptions());

            Assert.Contains((90, 138, 168), colors);
            Assert.Contains((255, 165, 0), colors);
            Assert.DoesNotContain((255, 255, 255), colors);
        }

        [Fact]
        public void UseStyleFaceColor_PaintsUnpaintedFacesWithTheStylesFrontColor()
        {
            var colors = PlacedColors(FixturePath("Untitled.skp"), StyleColor());

            // The style declares front (255,255,255), and nothing in this model is painted.
            Assert.Equal(new HashSet<(int, int, int)> { (255, 255, 255) }, colors);
        }

        [Fact]
        public void UseStyleFaceColor_LeavesRealPaintAlone()
        {
            // gondola_v20 is genuinely painted in places. Those colors have to survive, or the
            // option would be repainting the model rather than correcting its fallback.
            var painted = (153, 38, 0);
            var tagRed = (255, 84, 84);

            var off = PlacedColors(FixturePath("gondola_v20.skp"), new SkpParseOptions());
            var on = PlacedColors(FixturePath("gondola_v20.skp"), StyleColor());

            Assert.Contains(painted, off);
            Assert.Contains(painted, on);
            Assert.Contains(tagRed, off);
            Assert.DoesNotContain(tagRed, on);
        }

        [Fact]
        public void UseStyleFaceColor_FallsBackToWhiteWhenTheFileDeclaresNoStyle()
        {
            // Legacy (pre-2021) files carry no style container. White is SketchUp's own
            // default front face color, and still beats a tag color.
            Assert.Empty(SkpFile.Parse(File.ReadAllBytes(FixturePath("gondola_v20.skp"))).Styles);
            Assert.Contains((255, 255, 255), PlacedColors(FixturePath("gondola_v20.skp"), StyleColor()));
        }

        [Fact]
        public void BothSceneApisAgreeOnTheFallbackColor()
        {
            foreach (var fixture in new[] { "Untitled.skp", "SU_File.skp", "gondola_v20.skp" })
            {
                var bytes = File.ReadAllBytes(FixturePath(fixture));
                var baked = PlacedColors(bytes, StyleColor());

                var instanced = SkpFile.BuildInstancedScene(bytes, StyleColor());
                var instancedMaterials = Materials(instanced);
                var instancedColors = instanced.MeshResources
                    .SelectMany(resource => resource.Primitives)
                    .Where(primitive => primitive.Indices.Length > 0)
                    .Select(primitive => Rgb(instancedMaterials[primitive.MaterialIndex]))
                    .ToHashSet();

                Assert.Equal(baked, instancedColors);
            }
        }

        // -- an inherited material keeps its texture ------------------------------------------

        /// <summary>A component whose faces carry no material, placed once by an instance
        /// painted with <paramref name="material"/>.</summary>
        private static byte[] PaintedInstance(Func<SkpBuilder, int> material)
        {
            var builder = SkpCreate.NewFile();
            var handle = material(builder);
            ComponentDefinitionBuilder panel;
            using (panel = builder.AddComponentDefinition("panel"))
            {
                panel.AddFace(_quad);
            }

            builder.AddInstance(panel, "placed", material: handle);
            return builder.ToBytes();
        }

        [Fact]
        public void AnUnpaintedFaceTakesTheColorPaintedOnItsInstance()
        {
            var skp = PaintedInstance(builder => builder.AddMaterial("Teal", (0, 128, 128)));

            Assert.Equal(new HashSet<(int, int, int)> { (0, 128, 128) }, PlacedColors(skp, new SkpParseOptions()));
        }

        [Fact]
        public void AnUnpaintedFaceTakesTheTexturePaintedOnItsInstance()
        {
            var png = Path.Combine(Path.GetTempPath(), $"openskp_inherit_{Guid.NewGuid():N}.png");
            File.WriteAllBytes(png, TinyPng());
            try
            {
                var skp = PaintedInstance(builder => builder.AddTextureMaterial("Brick", png));
                var scene = SkpFile.BuildScene(skp, new SkpParseOptions());

                var placed = scene.GlbPrimitives.Where(primitive => primitive.Indices.Length > 0).ToList();
                Assert.NotEmpty(placed);
                Assert.All(placed, primitive =>
                    Assert.NotNull(Materials(scene)[primitive.MaterialIndex].TextureIndex));
                Assert.Single(scene.Textures);
            }
            finally
            {
                File.Delete(png);
            }
        }

        [Fact]
        public void TwoInstancesPaintedWithDifferentTexturesDoNotShareAMeshResource()
        {
            // The instanced builder caches a definition's mesh by its placement context. The
            // inherited material has to be part of that key: these two share a definition and
            // an average color, and differ only by image.
            var first = Path.Combine(Path.GetTempPath(), $"openskp_a_{Guid.NewGuid():N}.png");
            var second = Path.Combine(Path.GetTempPath(), $"openskp_b_{Guid.NewGuid():N}.png");
            File.WriteAllBytes(first, TinyPng());
            File.WriteAllBytes(second, TinyPng());
            try
            {
                var builder = SkpCreate.NewFile();
                var matA = builder.AddTextureMaterial("A", first);
                var matB = builder.AddTextureMaterial("B", second);
                ComponentDefinitionBuilder panel;
                using (panel = builder.AddComponentDefinition("panel"))
                {
                    panel.AddFace(_quad);
                }

                builder.AddInstance(panel, "a", material: matA);
                builder.AddInstance(panel, "b", translation: (0.0, 0.0, 20.0), material: matB);

                var scene = SkpFile.BuildInstancedScene(builder.ToBytes(), new SkpParseOptions());

                var placed = new List<string>();
                void Walk(InstancedNode node)
                {
                    if (node.MeshResourceId is string id) placed.Add(id);
                    foreach (var child in node.Children) Walk(child);
                }

                Walk(scene.SceneHierarchy);
                Assert.Equal(2, placed.Distinct().Count());
            }
            finally
            {
                File.Delete(first);
                File.Delete(second);
            }
        }
    }
}
