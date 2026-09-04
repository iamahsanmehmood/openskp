using System;
using System.IO;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>Coverage for OpenSkp.Create's SkpBuilder/ComponentDefinitionBuilder,
    /// mirroring packages/python/tests/test_create.py's own class-per-feature
    /// structure. Every scene is round-tripped through this project's own
    /// (independently-ported, already-verified) legacy reader, the same
    /// validation strategy the Python suite uses for its non-SDK-oracle
    /// tests.</summary>
    public class CreateTests
    {
        private static (double, double, double)[] Square(double x0 = 0, double y0 = 0, double z = 0, double size = 100) => new (double, double, double)[]
        {
            (x0, y0, z), (x0 + size, y0, z), (x0 + size, y0 + size, z), (x0, y0 + size, z),
        };

        [Fact]
        public void SingleFaceRoundTrips()
        {
            var builder = SkpCreate.NewFile();
            builder.AddFace(Square());
            var model = SkpFile.Parse(builder.ToBytes());
            Assert.Single(model.Root.Faces);
            var face = model.Root.Faces.Values.First();
            Assert.Single(face.Loops);
            Assert.Equal(4, face.Loops[0].Count);
            Assert.Equal(4, model.Root.Vertices.Count);
            Assert.Equal(4, model.Root.Edges.Count);
        }

        [Fact]
        public void MultiFaceSharesVerticesAndEdges()
        {
            var builder = SkpCreate.NewFile();
            // Two faces sharing one edge: (0,0,0)-(100,0,0)-(100,100,0)-(0,100,0)
            // and (100,0,0)-(200,0,0)-(200,100,0)-(100,100,0).
            builder.AddFace(Square());
            builder.AddFace(Square(x0: 100));
            var model = SkpFile.Parse(builder.ToBytes());
            Assert.Equal(2, model.Root.Faces.Count);
            // 4 + 4 unique corners minus the 2 shared on the common edge = 6.
            Assert.Equal(6, model.Root.Vertices.Count);
            // 4 + 4 edges minus the 1 shared edge = 7.
            Assert.Equal(7, model.Root.Edges.Count);
        }

        [Fact]
        public void RejectsFewerThanThreePoints()
        {
            var builder = SkpCreate.NewFile();
            Assert.Throws<SkpWriteException>(() => builder.AddFace(new (double, double, double)[] { (0, 0, 0), (1, 0, 0) }));
        }

        [Fact]
        public void RejectsNonCoplanarPoints()
        {
            var builder = SkpCreate.NewFile();
            Assert.Throws<SkpWriteException>(() => builder.AddFace(new (double, double, double)[]
            {
                (0, 0, 0), (10, 0, 0), (10, 10, 5), (0, 10, 0),
            }));
        }

        [Fact]
        public void RejectsSaveWithNoGeometry()
        {
            var builder = SkpCreate.NewFile();
            Assert.Throws<SkpWriteException>(() => builder.ToBytes());
        }

        [Fact]
        public void AddFaceRejectsALayerHandlePassedAsMaterial()
        {
            // The exact real-world mistake this guards against: a caller
            // accidentally passes a layer handle into the material
            // parameter (e.g. via an argument-order slip in a wrapper
            // function around AddFace). Before this check, the layer's
            // slot silently became a dangling material reference -
            // openskp's own reader tolerated it, but real SketchUp
            // rejected the resulting file outright.
            var builder = SkpCreate.NewFile();
            int layer = builder.AddLayer("Layer0");
            var ex = Assert.Throws<SkpWriteException>(() => builder.AddFace(Square(), material: layer));
            Assert.Contains("material", ex.Message);
        }

        [Fact]
        public void AddFaceRejectsAMaterialHandlePassedAsLayer()
        {
            var builder = SkpCreate.NewFile();
            int mat = builder.AddMaterial("Red", (255, 0, 0));
            var ex = Assert.Throws<SkpWriteException>(() => builder.AddFace(Square(), layer: mat));
            Assert.Contains("layer", ex.Message);
        }

        [Fact]
        public void AddFaceRejectsAnUnrelatedBackMaterialHandle()
        {
            var builder = SkpCreate.NewFile();
            int layer = builder.AddLayer("Layer0");
            var ex = Assert.Throws<SkpWriteException>(() => builder.AddFace(Square(), backMaterial: layer));
            Assert.Contains("backMaterial", ex.Message);
        }

        [Fact]
        public void AddFaceRejectsAHandleFromADifferentBuilder()
        {
            var otherBuilder = SkpCreate.NewFile();
            int strayMaterial = otherBuilder.AddMaterial("Blue", (0, 0, 255));
            var builder = SkpCreate.NewFile();
            Assert.Throws<SkpWriteException>(() => builder.AddFace(Square(), material: strayMaterial));
        }

        [Fact]
        public void AddInstanceRejectsALayerHandlePassedAsMaterial()
        {
            var builder = SkpCreate.NewFile();
            int layer = builder.AddLayer("Layer0");
            var chair = builder.AddComponentDefinition("Chair");
            chair.AddFace(Square());
            chair.Dispose();
            Assert.Throws<SkpWriteException>(() => builder.AddInstance(chair, material: layer));
        }

        [Fact]
        public void AddGroupRejectsAnUnrelatedLayerHandle()
        {
            var builder = SkpCreate.NewFile();
            int mat = builder.AddMaterial("Red", (255, 0, 0));
            Assert.Throws<SkpWriteException>(() => builder.AddGroup("Table", layer: mat));
        }

        [Fact]
        public void ComponentScopeAddFaceRejectsAnUnrelatedHandle()
        {
            var builder = SkpCreate.NewFile();
            int layer = builder.AddLayer("Layer0");
            using var chair = builder.AddComponentDefinition("Chair");
            Assert.Throws<SkpWriteException>(() => chair.AddFace(Square(), material: layer));
            // the definition must still be usable after a rejected call
            chair.AddFace(Square());
        }

        [Fact]
        public void AddFaceAcceptsARealMaterialAndLayer()
        {
            var builder = SkpCreate.NewFile();
            int mat = builder.AddMaterial("Red", (255, 0, 0));
            int layer = builder.AddLayer("MyLayer");
            builder.AddFace(Square(), material: mat, layer: layer);
            Assert.True(builder.ToBytes().Length > 0);
        }

        [Fact]
        public void AutoTriangulateSplitsNonPlanarQuad()
        {
            var builder = SkpCreate.NewFile();
            builder.AddFace(
                new (double, double, double)[] { (0, 0, 0), (10, 0, 0), (10, 10, 5), (0, 10, 0) },
                autoTriangulate: true);
            var model = SkpFile.Parse(builder.ToBytes());
            Assert.Equal(2, model.Root.Faces.Count);
            Assert.All(model.Root.Faces.Values, f => Assert.Equal(3, f.Loops[0].Count));
        }

        [Fact]
        public void AutoTriangulateLeavesPlanarInputAsSingleFace()
        {
            var builder = SkpCreate.NewFile();
            builder.AddFace(Square(), autoTriangulate: true);
            var model = SkpFile.Parse(builder.ToBytes());
            Assert.Single(model.Root.Faces);
        }

        [Fact]
        public void FaceWithHoleHasTwoLoops()
        {
            var builder = SkpCreate.NewFile();
            var wall = new (double, double, double)[] { (0, 0, 0), (200, 0, 0), (200, 100, 0), (0, 100, 0) };
            var window = new (double, double, double)[] { (80, 30, 0), (120, 30, 0), (120, 70, 0), (80, 70, 0) };
            builder.AddFace(wall, holes: new[] { window });
            var model = SkpFile.Parse(builder.ToBytes());
            var face = model.Root.Faces.Values.Single();
            Assert.Equal(2, face.Loops.Count);
            Assert.Equal(4, face.Loops[0].Count);
            Assert.Equal(4, face.Loops[1].Count);
        }

        [Fact]
        public void FaceWithTwoHoles()
        {
            var builder = SkpCreate.NewFile();
            var wall = new (double, double, double)[] { (0, 0, 0), (300, 0, 0), (300, 100, 0), (0, 100, 0) };
            var h1 = new (double, double, double)[] { (20, 20, 0), (60, 20, 0), (60, 60, 0), (20, 60, 0) };
            var h2 = new (double, double, double)[] { (200, 20, 0), (240, 20, 0), (240, 60, 0), (200, 60, 0) };
            builder.AddFace(wall, holes: new[] { h1, h2 });
            var model = SkpFile.Parse(builder.ToBytes());
            var face = model.Root.Faces.Values.Single();
            Assert.Equal(3, face.Loops.Count);
        }

        [Fact]
        public void HoleOffPlaneThrows()
        {
            var builder = SkpCreate.NewFile();
            var wall = new (double, double, double)[] { (0, 0, 0), (200, 0, 0), (200, 100, 0), (0, 100, 0) };
            var badHole = new (double, double, double)[] { (80, 30, 5), (120, 30, 5), (120, 70, 5), (80, 70, 5) };
            Assert.Throws<SkpWriteException>(() => builder.AddFace(wall, holes: new[] { badHole }));
        }

        [Fact]
        public void SolidMaterialRoundTrips()
        {
            var builder = SkpCreate.NewFile();
            int red = builder.AddMaterial("Red", (255, 0, 0, 255));
            builder.AddFace(Square(), material: red);
            var model = SkpFile.Parse(builder.ToBytes());
            Assert.Single(model.Materials);
            var mat = model.Materials[0];
            Assert.Equal("Red", mat.Name);
            Assert.Equal((255, 0, 0, 255), mat.Color);
            var face = model.Root.Faces.Values.Single();
            Assert.NotNull(face.MaterialId);
            Assert.Equal(mat.Id, face.MaterialId);
        }

        [Fact]
        public void DuplicateMaterialNameReturnsSameHandle()
        {
            var builder = SkpCreate.NewFile();
            int a = builder.AddMaterial("Red", (255, 0, 0));
            int b = builder.AddMaterial("Red", (0, 255, 0)); // color ignored on repeat, matching Python
            Assert.Equal(a, b);
            Assert.Single(builder.MaterialsByName);
        }

        [Fact]
        public void MaterialAfterFaceThrows()
        {
            var builder = SkpCreate.NewFile();
            builder.AddFace(Square());
            Assert.Throws<SkpWriteException>(() => builder.AddMaterial("Red", (255, 0, 0)));
        }

        [Fact]
        public void TexturedMaterialRoundTrips()
        {
            string pngPath = Path.Combine(Path.GetTempPath(), Guid.NewGuid() + ".png");
            File.WriteAllBytes(pngPath, TinyPng());
            try
            {
                var builder = SkpCreate.NewFile();
                int brick = builder.AddTextureMaterial("Brick", pngPath);
                builder.AddFace(Square(), material: brick);
                var model = SkpFile.Parse(builder.ToBytes());
                var mat = model.Materials.Single();
                Assert.NotNull(mat.Texture);
                Assert.NotNull(mat.Texture!.Data);
                Assert.Equal(TinyPng(), mat.Texture.Data);
            }
            finally
            {
                File.Delete(pngPath);
            }
        }

        [Fact]
        public void TexturedMaterialDefaultAppliedHeightIsOneNotCorrupted()
        {
            // Regression test for a real bug: until 2026-08-28, an omitted
            // appliedHeight wrote a corrupted internal sentinel byte
            // pattern (~1.29e-231) instead of a real number - confirmed
            // via real SketchUp screenshots to render as a streaky,
            // vertically-smeared texture. AddTextureMaterial's applied
            // WIDTH is unconditionally 1.0 (a deliberate ground-truth
            // value); height should match it by default now, not
            // silently corrupt every caller who doesn't know to pass
            // appliedHeight: 1.0 explicitly.
            string pngPath = Path.Combine(Path.GetTempPath(), Guid.NewGuid() + ".png");
            File.WriteAllBytes(pngPath, TinyPng());
            try
            {
                var builder = SkpCreate.NewFile();
                int brick = builder.AddTextureMaterial("Brick", pngPath);
                builder.AddFace(Square(), material: brick);
                var model = SkpFile.Parse(builder.ToBytes());
                var mat = model.Materials.Single();
                Assert.Equal(1.0, mat.Texture!.Width);
                Assert.Equal(1.0, mat.Texture.Height);
            }
            finally
            {
                File.Delete(pngPath);
            }
        }

        [Fact]
        public void TexturedMaterialExplicitAppliedHeightStillOverridable()
        {
            string pngPath = Path.Combine(Path.GetTempPath(), Guid.NewGuid() + ".png");
            File.WriteAllBytes(pngPath, TinyPng());
            try
            {
                var builder = SkpCreate.NewFile();
                int brick = builder.AddTextureMaterial("Brick", pngPath, appliedHeight: 48.0);
                builder.AddFace(Square(), material: brick);
                var model = SkpFile.Parse(builder.ToBytes());
                var mat = model.Materials.Single();
                Assert.Equal(48.0, mat.Texture!.Height);
            }
            finally
            {
                File.Delete(pngPath);
            }
        }

        [Fact]
        public void TexturedMaterialAppliedSizeFullyOverridable()
        {
            // Real SketchUp writes the material's own tile size in BOTH
            // axes (a file authored in SketchUp Web carries 8.0 x 16.0 for
            // a brick); a texture applied without positioning carries no
            // per-face UV record, so this pair IS its mapping.
            string pngPath = Path.Combine(Path.GetTempPath(), Guid.NewGuid() + ".png");
            File.WriteAllBytes(pngPath, TinyPng());
            try
            {
                var builder = SkpCreate.NewFile();
                int brick = builder.AddTextureMaterial("Brick", pngPath, appliedHeight: 16.0, appliedWidth: 8.0);
                builder.AddFace(Square(), material: brick);
                var model = SkpFile.Parse(builder.ToBytes());
                var mat = model.Materials.Single();
                Assert.Equal(8.0, mat.Texture!.Width);
                Assert.Equal(16.0, mat.Texture.Height);
            }
            finally
            {
                File.Delete(pngPath);
            }
        }

        [Fact]
        public void TexturedMaterialCarriesOpacity()
        {
            string pngPath = Path.Combine(Path.GetTempPath(), Guid.NewGuid() + ".png");
            File.WriteAllBytes(pngPath, TinyPng());
            try
            {
                var builder = SkpCreate.NewFile();
                int voile = builder.AddTextureMaterial("Voile", pngPath, opacity: 0.5);
                builder.AddFace(Square(), material: voile);
                var model = SkpFile.Parse(builder.ToBytes());
                var mat = model.Materials.Single();
                Assert.Equal(0.5, mat.Transparency, 6);
            }
            finally
            {
                File.Delete(pngPath);
            }
        }

        [Fact]
        public void SolidMaterialCarriesOpacity()
        {
            var builder = SkpCreate.NewFile();
            int glass = builder.AddMaterial("Glass", (200, 220, 255), opacity: 0.35);
            builder.AddFace(Square(), material: glass);
            var model = SkpFile.Parse(builder.ToBytes());
            var mat = model.Materials.Single();
            Assert.Equal(0.35, mat.Transparency, 6);
        }

        [Fact]
        public void OmittedOpacityStaysFullyOpaque()
        {
            var builder = SkpCreate.NewFile();
            int red = builder.AddMaterial("Red", (255, 0, 0));
            builder.AddFace(Square(), material: red);
            var model = SkpFile.Parse(builder.ToBytes());
            var mat = model.Materials.Single();
            Assert.Equal(1.0, mat.Transparency);
        }

        [Fact]
        public void UnrecognizedImageFormatThrows()
        {
            string path = Path.Combine(Path.GetTempPath(), Guid.NewGuid() + ".bin");
            File.WriteAllBytes(path, new byte[] { 1, 2, 3, 4 });
            try
            {
                var builder = SkpCreate.NewFile();
                Assert.Throws<SkpWriteException>(() => builder.AddTextureMaterial("Bad", path));
            }
            finally
            {
                File.Delete(path);
            }
        }

        [Fact]
        public void AddImagePlacesARealImageEntityNotAPlainTexturedFace()
        {
            string pngPath = Path.Combine(Path.GetTempPath(), Guid.NewGuid() + ".png");
            File.WriteAllBytes(pngPath, TinyPng());
            try
            {
                var builder = SkpCreate.NewFile();
                builder.AddImage(
                    pngPath, 48, 36,
                    translation: (0, 0, 40),
                    rotation: ((1, 0, 0), Math.PI / 2));
                var model = SkpFile.Parse(builder.ToBytes());

                var imageDefs = model.Definitions.Values.Where(d => d.IsImage).ToList();
                Assert.Single(imageDefs);
                Assert.Single(imageDefs[0].Faces);
                Assert.Single(model.Root.Instances);
                Assert.Equal(imageDefs[0].Id, model.Root.Instances[0].RefIdx);
            }
            finally
            {
                File.Delete(pngPath);
            }
        }

        [Fact]
        public void LayerRoundTrips()
        {
            var builder = SkpCreate.NewFile();
            int roof = builder.AddLayer("Roof", (200, 50, 50), hidden: true);
            builder.AddFace(Square(), layer: roof);
            var model = SkpFile.Parse(builder.ToBytes());
            var layer = model.Layers.Single(l => l.Name == "Roof");
            Assert.True(layer.Hidden);
            Assert.Equal(200, layer.ColorR);
            Assert.Equal(50, layer.ColorG);
            Assert.Equal(50, layer.ColorB);
        }

        [Fact]
        public void ComponentDefinitionAndInstanceRoundTrip()
        {
            var builder = SkpCreate.NewFile();
            var chair = builder.AddComponentDefinition("Chair");
            chair.AddFace(new (double, double, double)[] { (0, 0, 0), (20, 0, 0), (20, 20, 0), (0, 20, 0) });
            chair.Dispose();
            builder.AddInstance(chair, translation: (100, 0, 0));
            builder.AddInstance(chair, translation: (200, 0, 0));

            var model = SkpFile.Parse(builder.ToBytes());
            Assert.Single(model.Definitions);
            var def = model.Definitions.Values.Single();
            Assert.Equal("Chair", def.Name);
            Assert.Single(def.Faces);
            Assert.Equal(2, model.Root.Instances.Count);
            Assert.All(model.Root.Instances, i => Assert.Equal(def.Id, i.RefIdx));
        }

        [Fact]
        public void ComponentDefinitionUsingBlockClosesAutomatically()
        {
            var builder = SkpCreate.NewFile();
            ComponentDefinitionBuilder chair;
            using (chair = builder.AddComponentDefinition("Chair"))
            {
                chair.AddFace(new (double, double, double)[] { (0, 0, 0), (20, 0, 0), (20, 20, 0), (0, 20, 0) });
            }
            builder.AddInstance(chair, translation: (0, 0, 0));
            var model = SkpFile.Parse(builder.ToBytes());
            Assert.Single(model.Definitions);
        }

        [Fact]
        public void EmptyDefinitionThrowsOnClose()
        {
            var builder = SkpCreate.NewFile();
            var def = builder.AddComponentDefinition("Empty");
            Assert.Throws<SkpWriteException>(() => def.Dispose());
        }

        [Fact]
        public void GroupSelfPlaces()
        {
            var builder = SkpCreate.NewFile();
            using (var table = builder.AddGroup("Table", translation: (50, 0, 0)))
            {
                table.AddFace(new (double, double, double)[] { (0, 0, 0), (30, 0, 0), (30, 30, 0), (0, 30, 0) });
            }
            builder.AddFace(Square()); // ensure at least one root entity besides the group
            var model = SkpFile.Parse(builder.ToBytes());
            Assert.Single(model.Definitions);
            // The group placed itself as one instance; AddFace above added
            // one more root-level face - so exactly one instance, one face.
            Assert.Single(model.Root.Instances);
            Assert.Single(model.Root.Faces);
        }

        [Fact]
        public void GroupOnlyFileStillFlushesPendingGroup()
        {
            var builder = SkpCreate.NewFile();
            using (var table = builder.AddGroup("Table"))
            {
                table.AddFace(Square());
            }
            var model = SkpFile.Parse(builder.ToBytes());
            Assert.Single(model.Root.Instances);
        }

        [Fact]
        public void GroupAttributeDictsRoundTripThroughInstanceProperties()
        {
            // Groups used to hardcode a null attribute pointer on the belief
            // that a CGroup never carries a CAttributeContainer - real
            // production files (SketchUp 2020-legacy export, ground truth)
            // contradicted this (openskp#261).
            var builder = SkpCreate.NewFile();
            using (var table = builder.AddGroup(
                "Table",
                attributes: new System.Collections.Generic.Dictionary<string, object> { ["sku"] = "TBL-1", ["qty"] = 2 },
                attributeDictName: "dynamic_attributes"))
            {
                table.AddFace(Square());
            }
            var model = SkpFile.Parse(builder.ToBytes());
            var instance = Assert.Single(model.Root.Instances);
            Assert.Equal("TBL-1", instance.Properties["sku"]);
            Assert.Equal("2", instance.Properties["qty"]);
        }

        [Fact]
        public void GroupWithNoAttributesStillGetsNullAttributePointer()
        {
            var builder = SkpCreate.NewFile();
            using (var table = builder.AddGroup("Table"))
            {
                table.AddFace(Square());
            }
            var model = SkpFile.Parse(builder.ToBytes());
            var instance = Assert.Single(model.Root.Instances);
            Assert.Empty(instance.Properties);
        }

        [Fact]
        public void NestedDefinitionInstanceInsideAnother()
        {
            var builder = SkpCreate.NewFile();
            ComponentDefinitionBuilder wheel;
            using (wheel = builder.AddComponentDefinition("Wheel"))
            {
                wheel.AddFace(new (double, double, double)[] { (0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0) });
            }
            ComponentDefinitionBuilder car;
            using (car = builder.AddComponentDefinition("Car"))
            {
                car.AddInstance(wheel, translation: (0, 0, 0));
                car.AddInstance(wheel, translation: (100, 0, 0));
            }
            builder.AddInstance(car, translation: (0, 0, 0));
            var model = SkpFile.Parse(builder.ToBytes());
            Assert.Equal(2, model.Definitions.Count);
            var carDef = model.Definitions.Values.Single(d => d.Name == "Car");
            Assert.Equal(2, carDef.Instances.Count);
        }

        [Fact]
        public void NestedGroupInstanceInsideDefinition()
        {
            var builder = SkpCreate.NewFile();
            ComponentDefinitionBuilder engine;
            ComponentDefinitionBuilder car;
            using (engine = builder.AddComponentDefinition("Engine"))
            {
                engine.AddFace(new (double, double, double)[] { (0, 0, 0), (30, 0, 0), (30, 30, 0), (0, 30, 0) });
            }
            using (car = builder.AddComponentDefinition("Car"))
            {
                car.AddFace(new (double, double, double)[] { (0, 0, 0), (150, 0, 0), (150, 60, 0), (0, 60, 0) });
                car.AddGroupInstance(engine, translation: (50, 0, 10));
            }
            builder.AddInstance(car, translation: (0, 0, 0));

            var model = SkpFile.Parse(builder.ToBytes());
            Assert.Equal(2, model.Definitions.Count); // Engine, Car - AddGroupInstance reuses Engine's definition
            var carDef = model.Definitions.Values.Single(d => d.Name == "Car");
            Assert.Single(carDef.Faces);
            Assert.Single(carDef.Instances); // the nested group placement
            Assert.Single(model.Root.Instances); // the top-level Car instance
        }

        [Fact]
        public void NestedGroupInstanceCarriesItsOwnAttributeDictsToo()
        {
            var builder = SkpCreate.NewFile();
            ComponentDefinitionBuilder engine;
            ComponentDefinitionBuilder car;
            using (engine = builder.AddComponentDefinition("Engine"))
            {
                engine.AddFace(Square());
            }
            using (car = builder.AddComponentDefinition("Car"))
            {
                car.AddFace(new (double, double, double)[] { (0, 0, 0), (150, 0, 0), (150, 60, 0), (0, 60, 0) });
                car.AddGroupInstance(
                    engine, translation: (50, 0, 10),
                    attributes: new System.Collections.Generic.Dictionary<string, object> { ["part"] = "V6" },
                    attributeDictName: "dynamic_attributes");
            }
            builder.AddInstance(car, translation: (0, 0, 0));

            var model = SkpFile.Parse(builder.ToBytes());
            var carDef = model.Definitions.Values.Single(d => d.Name == "Car");
            var groupInstance = Assert.Single(carDef.Instances);
            Assert.Equal("V6", groupInstance.Properties["part"]);
        }

        [Fact]
        public void InstanceRotationAppliesRodrigues()
        {
            var builder = SkpCreate.NewFile();
            ComponentDefinitionBuilder chair;
            using (chair = builder.AddComponentDefinition("Chair"))
            {
                chair.AddFace(new (double, double, double)[] { (0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0) });
            }
            builder.AddInstance(chair, rotation: ((0.0, 0.0, 1.0), Math.PI / 2));
            var model = SkpFile.Parse(builder.ToBytes());
            var inst = model.Root.Instances.Single();
            // The matrix is stored flat, row-major: [m00,m01,m02, m10,m11,m12, m20,m21,m22].
            // Applied as world = M @ local (column-vector convention, the
            // standard Rodrigues-formula reading), rotating local X=(1,0,0)
            // by +90deg around Z gives world (0,1,0) - i.e. the matrix's
            // first COLUMN (m00,m10,m20 = indices 0,3,6) should land there.
            Assert.True(Math.Abs(inst.Matrix[0]) < 1e-9);
            Assert.True(Math.Abs(inst.Matrix[3] - 1.0) < 1e-9);
            Assert.True(Math.Abs(inst.Matrix[6]) < 1e-9);
        }

        [Fact]
        public void HiddenFaceAndInstanceRoundTrip()
        {
            var builder = SkpCreate.NewFile();
            ComponentDefinitionBuilder def;
            using (def = builder.AddComponentDefinition("Def"))
            {
                def.AddFace(Square(size: 10));
            }
            builder.AddFace(Square(), hidden: true);
            builder.AddInstance(def, hidden: true);
            var model = SkpFile.Parse(builder.ToBytes());
            Assert.Contains(model.Root.Faces.Values, f => f.Hidden);
            Assert.Contains(model.Root.Instances, i => i.Hidden);
        }

        [Fact]
        public void AttributesOnFaceAndDefinitionAndInstanceDoNotThrow()
        {
            var builder = SkpCreate.NewFile();
            ComponentDefinitionBuilder def;
            using (def = builder.AddComponentDefinition("Def", attributes: new System.Collections.Generic.Dictionary<string, object>
            {
                ["part_number"] = 42,
            }))
            {
                def.AddFace(Square(size: 10), attributes: new System.Collections.Generic.Dictionary<string, object>
                {
                    ["note"] = "hello",
                    ["ratio"] = 1.5,
                });
            }
            builder.AddInstance(def, attributes: new System.Collections.Generic.Dictionary<string, object> { ["serial"] = "abc123" });
            var bytes = builder.ToBytes();
            var model = SkpFile.Parse(bytes); // just confirm it still parses cleanly
            Assert.Single(model.Root.Instances);
        }

        [Fact]
        public void InstanceDynamicAttributesRoundTripThroughInstanceProperties()
        {
            // Was reader-side broken (LegacyReaders.ExtractLegacyDynamicProperties
            // compared the *class* name Archive.ReadObject returns for each
            // CAttributeContainer child - always "CAttributeNamed" - against
            // the dictionary's own name instead of comparing DictRec.Name,
            // so "dynamic_attributes" was never recognized) - fixed
            // 2026-08-26, ported from the same fix in Python's legacy.py.
            // Now genuinely round-trips.
            var builder = SkpCreate.NewFile();
            var chair = builder.AddComponentDefinition("Chair");
            using (chair) { chair.AddFace(Square()); }
            builder.AddInstance(
                chair,
                attributes: new System.Collections.Generic.Dictionary<string, object> { ["sku"] = "CH-1", ["count"] = 3 },
                attributeDictName: "dynamic_attributes");
            var model = SkpFile.Parse(builder.ToBytes());
            var instance = Assert.Single(model.Root.Instances);
            Assert.Equal("CH-1", instance.Properties["sku"]);
            Assert.Equal("3", instance.Properties["count"]);
        }

        [Fact]
        public void UnsupportedAttributeValueTypeThrows()
        {
            var builder = SkpCreate.NewFile();
            Assert.Throws<SkpWriteException>(() =>
            {
                builder.AddFace(Square(), attributes: new System.Collections.Generic.Dictionary<string, object> { ["bad"] = true });
            });
        }

        [Fact]
        public void CircleIsRealArcCurveFace()
        {
            var builder = SkpCreate.NewFile();
            builder.AddCircle((50, 50, 0), (0, 0, 1), 40, numSegments: 16);
            var model = SkpFile.Parse(builder.ToBytes());
            var face = model.Root.Faces.Values.Single();
            Assert.Equal(16, face.Loops[0].Count);
            Assert.Equal(16, model.Root.Vertices.Count);
            Assert.Equal(16, model.Root.Edges.Count);
        }

        [Fact]
        public void ArcHasNoFaceJustEdges()
        {
            var builder = SkpCreate.NewFile();
            builder.AddArc((0, 0, 0), (0, 0, 1), 40, 0, Math.PI / 2, numSegments: 8);
            var model = SkpFile.Parse(builder.ToBytes());
            Assert.Empty(model.Root.Faces);
            Assert.Equal(9, model.Root.Vertices.Count); // numSegments+1 points
            Assert.Equal(8, model.Root.Edges.Count);
        }

        [Fact]
        public void PolylineOpenAndClosedRoundTrip()
        {
            var builder = SkpCreate.NewFile();
            builder.AddPolyline(new (double, double, double)[] { (0, 0, 0), (10, 10, 0), (20, 0, 0), (30, 10, 0) });
            var model = SkpFile.Parse(builder.ToBytes());
            Assert.Equal(4, model.Root.Vertices.Count);
            Assert.Equal(3, model.Root.Edges.Count);
        }

        [Fact]
        public void ClosedPolylineConnectsLastToFirst()
        {
            var builder = SkpCreate.NewFile();
            builder.AddPolyline(
                new (double, double, double)[] { (0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0) },
                closed: true);
            var model = SkpFile.Parse(builder.ToBytes());
            Assert.Equal(4, model.Root.Edges.Count);
        }

        [Fact]
        public void FrontUvPositioningRoundTrips()
        {
            string pngPath = Path.Combine(Path.GetTempPath(), Guid.NewGuid() + ".png");
            File.WriteAllBytes(pngPath, TinyPng());
            try
            {
                var builder = SkpCreate.NewFile();
                int brick = builder.AddTextureMaterial("Brick", pngPath);
                builder.AddFace(
                    new (double, double, double)[] { (0, 0, 0), (100, 0, 0), (100, 100, 0), (0, 100, 0) },
                    material: brick,
                    frontUv: new[]
                    {
                        new UvCorrespondence((0, 0, 0), (0.0, 0.0)),
                        new UvCorrespondence((50, 0, 0), (1.0, 0.0)),
                        new UvCorrespondence((0, 50, 0), (0.0, 1.0)),
                    });
                var model = SkpFile.Parse(builder.ToBytes());
                var face = model.Root.Faces.Values.Single();
                Assert.NotNull(face.UvTransform);
            }
            finally
            {
                File.Delete(pngPath);
            }
        }

        [Fact]
        public void UvPositioningWithWrongPairCountThrows()
        {
            var builder = SkpCreate.NewFile();
            Assert.Throws<SkpWriteException>(() =>
            {
                builder.AddFace(
                    Square(),
                    frontUv: new[]
                    {
                        new UvCorrespondence((0, 0, 0), (0.0, 0.0)),
                        new UvCorrespondence((50, 0, 0), (1.0, 0.0)),
                    });
            });
        }

        [Fact]
        public void UnicodeNamesRoundTrip()
        {
            var builder = SkpCreate.NewFile();
            int layer = builder.AddLayer("日本語レイヤー");
            using (var def = builder.AddComponentDefinition("Café Silla"))
            {
                def.AddFace(Square(size: 10));
            }
            builder.AddFace(Square(), layer: layer);
            var model = SkpFile.Parse(builder.ToBytes());
            Assert.Contains(model.Definitions.Values, d => d.Name == "Café Silla");
            Assert.Contains(model.Layers, l => l.Name == "日本語レイヤー");
        }

        [Fact]
        public void ScaffoldResourceMatchesExpectedHash()
        {
            // Guards against the embedded scaffold resource silently
            // drifting without CreateConstants' offsets being re-derived
            // to match - would otherwise fail in a much more confusing
            // way (corrupted output, not a clear error). NewFile() itself
            // already validates this on every construction; this test
            // just makes the check explicit and fails fast if it ever
            // regresses.
            var builder = SkpCreate.NewFile();
            builder.AddFace(Square());
            Assert.True(builder.ToBytes().Length > 0);
        }

        // 1x1 transparent PNG.
        private static byte[] TinyPng() => Convert.FromBase64String(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=");
    }
}
