using System;
using System.Collections.Generic;
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
        public void AGroupIsWrittenAsAGroupAndAComponentAsAComponent()
        {
            // SketchUp 2025's own library reports one group definition and one component
            // definition for a file like this (openskp#446)
            var builder = SkpCreate.NewFile();
            using (var table = builder.AddGroup("Table"))
            {
                table.AddFace(Square());
            }
            var chair = builder.AddComponentDefinition("Chair");
            chair.AddFace(Square());
            chair.Dispose();
            builder.AddInstance(chair);
            var model = SkpFile.Parse(builder.ToBytes());
            Assert.Equal(1, model.Definitions.Values.Count(d => d.IsGroup));
            Assert.False(model.Definitions.Values.Single(d => d.Name == "Chair").IsGroup);
        }

        [Fact]
        public void AGroupPlacedInsideAComponentIsAGroup()
        {
            // SketchUp 2025's own library reports the placed definition as a group definition and
            // the enclosing one as a component (openskp#467)
            var builder = SkpCreate.NewFile();
            var engine = builder.AddComponentDefinition("Engine");
            engine.AddFace(Square());
            engine.Dispose();
            var car = builder.AddComponentDefinition("Car");
            car.AddFace(Square());
            car.AddGroupInstance(engine, translation: (50, 0, 10));
            car.Dispose();
            builder.AddInstance(car);
            var model = SkpFile.Parse(builder.ToBytes());
            Assert.True(model.Definitions.Values.Single(d => d.Name == "Engine").IsGroup);
            Assert.False(model.Definitions.Values.Single(d => d.Name == "Car").IsGroup);
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
        public void MultipleAttributeDictsOnInstancePreserveOrderAndRoundTrip()
        {
            var builder = SkpCreate.NewFile();
            ComponentDefinitionBuilder stud;
            using (stud = builder.AddComponentDefinition("Stud"))
            {
                stud.AddFace(Square());
            }
            builder.AddInstance(
                stud,
                attributeDicts: new[]
                {
                    new AttributeDict("z_first", new System.Collections.Generic.Dictionary<string, object> { ["part"] = "Stud", ["depth"] = 94.92 }),
                    new AttributeDict("dynamic_attributes", new System.Collections.Generic.Dictionary<string, object> { ["count"] = 4 }),
                    new AttributeDict("a_third", new System.Collections.Generic.Dictionary<string, object> { ["guide"] = 1 }),
                });

            var bytes = builder.ToBytes();
            var walked = Legacy.Walk(bytes);
            var instRec = Assert.IsType<InstanceRec>(walked.Root[0].Value);
            Assert.NotNull(instRec.Attrs);
            var dictNames = instRec.Attrs!.Children.Select(c => ((DictRec)c.Value!).Name).ToArray();
            Assert.Equal(new[] { "z_first", "dynamic_attributes", "a_third" }, dictNames);
            Assert.Equal("Stud", ((DictRec)instRec.Attrs.Children[0].Value!).Entries["part"]);
            Assert.Equal(1, ((DictRec)instRec.Attrs.Children[2].Value!).Entries["guide"]);

            var model = SkpFile.Parse(bytes);
            var instance = Assert.Single(model.Root.Instances);
            Assert.Equal("4", instance.Properties["count"]);
        }

        [Fact]
        public void MultipleAttributeDictsOnGroupPreserveOrderAndCreateRealContainer()
        {
            var builder = SkpCreate.NewFile();
            using (var grp = builder.AddGroup(
                "W-1",
                attributeDicts: new[]
                {
                    new AttributeDict("fbd-einfo", new System.Collections.Generic.Dictionary<string, object> { ["depth"] = 15.5 }),
                    new AttributeDict("dynamic_attributes", new System.Collections.Generic.Dictionary<string, object> { ["sku"] = "GRP-1" }),
                    new AttributeDict("fbd-profile", new System.Collections.Generic.Dictionary<string, object> { ["guide"] = 1 }),
                }))
            {
                grp.AddFace(Square());
            }

            var bytes = builder.ToBytes();
            var walked = Legacy.Walk(bytes);
            var instRec = Assert.IsType<InstanceRec>(walked.Root[0].Value);
            Assert.NotNull(instRec.Attrs);
            var dictNames = instRec.Attrs!.Children.Select(c => ((DictRec)c.Value!).Name).ToArray();
            Assert.Equal(new[] { "fbd-einfo", "dynamic_attributes", "fbd-profile" }, dictNames);

            var model = SkpFile.Parse(bytes);
            Assert.Equal("GRP-1", Assert.Single(model.Root.Instances).Properties["sku"]);
        }

        [Fact]
        public void MultipleAttributeDictsWorkOnFacesCirclesDefinitionsAndNestedPlacements()
        {
            static bool ContainsUtf16(byte[] haystack, string needle)
            {
                byte[] sub = System.Text.Encoding.Unicode.GetBytes(needle);
                for (int i = 0; i + sub.Length <= haystack.Length; i++)
                {
                    bool match = true;
                    for (int j = 0; j < sub.Length; j++)
                    {
                        if (haystack[i + j] != sub[j]) { match = false; break; }
                    }
                    if (match) return true;
                }
                return false;
            }

            var builder = SkpCreate.NewFile();
            ComponentDefinitionBuilder part;
            using (part = builder.AddComponentDefinition(
                "Part",
                attributeDicts: new[]
                {
                    new AttributeDict("def_meta_a", new System.Collections.Generic.Dictionary<string, object> { ["k"] = "v1" }),
                    new AttributeDict("def_meta_b", new System.Collections.Generic.Dictionary<string, object> { ["k"] = "v2" }),
                }))
            {
                part.AddFace(Square(size: 10), attributeDicts: new[]
                {
                    new AttributeDict("def_face_a", new System.Collections.Generic.Dictionary<string, object> { ["x"] = 1 }),
                    new AttributeDict("def_face_b", new System.Collections.Generic.Dictionary<string, object> { ["y"] = 2 }),
                });
                part.AddCircle((0, 0, 10), (0, 0, 1), 5, attributeDicts: new[]
                {
                    new AttributeDict("def_circ_a", new System.Collections.Generic.Dictionary<string, object> { ["r"] = 5 }),
                    new AttributeDict("def_circ_b", new System.Collections.Generic.Dictionary<string, object> { ["seg"] = 24 }),
                });
            }

            ComponentDefinitionBuilder assembly;
            using (assembly = builder.AddComponentDefinition("Assembly"))
            {
                assembly.AddInstance(part, attributeDicts: new[]
                {
                    new AttributeDict("nest_inst_a", new System.Collections.Generic.Dictionary<string, object> { ["a"] = 1 }),
                    new AttributeDict("nest_inst_b", new System.Collections.Generic.Dictionary<string, object> { ["b"] = 2 }),
                });
                assembly.AddGroupInstance(part, name: "NestedGroup", attributeDicts: new[]
                {
                    new AttributeDict("nest_grp_a", new System.Collections.Generic.Dictionary<string, object> { ["g"] = 1 }),
                    new AttributeDict("nest_grp_b", new System.Collections.Generic.Dictionary<string, object> { ["h"] = 2 }),
                });
            }

            builder.AddInstance(assembly);
            builder.AddFace(Square(), attributeDicts: new[]
            {
                new AttributeDict("root_face_a", new System.Collections.Generic.Dictionary<string, object> { ["x"] = 1 }),
                new AttributeDict("root_face_b", new System.Collections.Generic.Dictionary<string, object> { ["y"] = 2 }),
            });
            builder.AddCircle((50, 50, 0), (0, 0, 1), 10, attributeDicts: new[]
            {
                new AttributeDict("root_circ_a", new System.Collections.Generic.Dictionary<string, object> { ["c"] = 1 }),
                new AttributeDict("root_circ_b", new System.Collections.Generic.Dictionary<string, object> { ["d"] = 2 }),
            });

            var bytes = builder.ToBytes();
            foreach (var marker in new[]
            {
                "def_meta_a", "def_meta_b", "def_face_a", "def_face_b",
                "def_circ_a", "def_circ_b", "nest_inst_a", "nest_inst_b",
                "nest_grp_a", "nest_grp_b", "root_face_a", "root_face_b",
                "root_circ_a", "root_circ_b",
            })
            {
                Assert.True(ContainsUtf16(bytes, marker), $"expected marker {marker}");
            }

            var model = SkpFile.Parse(bytes);
            Assert.Equal(2, model.Root.Faces.Count);
            Assert.Single(model.Root.Instances);
        }

        [Fact]
        public void PassingBothAttributesAndAttributeDictsThrows()
        {
            var single = new System.Collections.Generic.Dictionary<string, object> { ["a"] = 1 };
            var multi = new[] { new AttributeDict("b", new System.Collections.Generic.Dictionary<string, object> { ["c"] = 2 }) };

            var builder = SkpCreate.NewFile();
            ComponentDefinitionBuilder chair;
            using (chair = builder.AddComponentDefinition("Chair"))
            {
                chair.AddFace(Square());
            }

            var ex1 = Assert.Throws<SkpWriteException>(() => builder.AddInstance(chair, attributes: single, attributeDicts: multi));
            Assert.Contains("not both", ex1.Message);

            var ex2 = Assert.Throws<SkpWriteException>(() => builder.AddGroup("W-1", attributes: single, attributeDicts: multi));
            Assert.Contains("not both", ex2.Message);

            var ex3 = Assert.Throws<SkpWriteException>(() => builder.AddFace(Square(), attributes: single, attributeDicts: multi));
            Assert.Contains("not both", ex3.Message);

            var ex4 = Assert.Throws<SkpWriteException>(() => builder.AddCircle((0, 0, 0), (0, 0, 1), 10, attributes: single, attributeDicts: multi));
            Assert.Contains("not both", ex4.Message);

            var ex5 = Assert.Throws<SkpWriteException>(() => builder.AddComponentDefinition("Bad", attributes: single, attributeDicts: multi));
            Assert.Contains("not both", ex5.Message);

            var builder2 = SkpCreate.NewFile();
            ComponentDefinitionBuilder part;
            using (part = builder2.AddComponentDefinition("Part"))
            {
                part.AddFace(Square());
            }
            using (var outer = builder2.AddComponentDefinition("Outer"))
            {
                outer.AddFace(Square());
                var ex6 = Assert.Throws<SkpWriteException>(() => outer.AddInstance(part, attributes: single, attributeDicts: multi));
                Assert.Contains("not both", ex6.Message);
                var ex7 = Assert.Throws<SkpWriteException>(() => outer.AddGroupInstance(part, attributes: single, attributeDicts: multi));
                Assert.Contains("not both", ex7.Message);
            }
        }

        [Fact]
        public void EmptyAttributeDictsBehavesLikeNoAttributes()
        {
            var builder = SkpCreate.NewFile();
            ComponentDefinitionBuilder chair;
            using (chair = builder.AddComponentDefinition("Chair"))
            {
                chair.AddFace(Square());
            }
            using (var grp = builder.AddGroup("PlainGroup", attributeDicts: Array.Empty<AttributeDict>()))
            {
                grp.AddFace(Square());
            }
            builder.AddInstance(chair, attributeDicts: Array.Empty<AttributeDict>());

            var walked = Legacy.Walk(builder.ToBytes());
            var grpRec = Assert.IsType<InstanceRec>(walked.Root[0].Value);
            var instRec = Assert.IsType<InstanceRec>(walked.Root[1].Value);
            Assert.Null(grpRec.Attrs);
            Assert.NotNull(instRec.Attrs);
            Assert.Empty(instRec.Attrs!.Children);
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
            // Guards against BlankDocumentBuilder.BuildBlankV17() drifting from
            // the expected classic v17 empty document. NewFile() validates the
            // SHA-256 digest on construction; this test also checks byte-for-byte
            // agreement with the reader fixture.
            var doc = BlankDocumentBuilder.BuildBlankV17();
            var fixture = File.ReadAllBytes(Path.Combine(AppContext.BaseDirectory, "fixtures", "blank_v17.skp"));
            Assert.Equal(fixture, doc.Data);
            var builder = SkpCreate.NewFile();
            builder.AddFace(Square());
            Assert.True(builder.ToBytes().Length > 0);
        }

        private static uint PidCounter(byte[] data) => BitConverter.ToUInt32(data, CreateConstants.PidCounterPos);

        // Every section's writer used to hand out persistent ids from 1 again, so
        // a definition and a root instance could carry the same pid, and the header
        // counter only grew by the material and layer count. SketchUp loads such a
        // file and renumbers the duplicates, after which SketchUp for Web cannot
        // save it (openskp#492). Ids now run in one sequence and the counter is the
        // last id handed out - the same numbers the Python writer produces.
        [Fact]
        public void PersistentIdsRunInOneSequenceAcrossSections()
        {
            var builder = SkpCreate.NewFile();
            int red = builder.AddMaterial("Rojo", (255, 0, 0, 255));
            int layer = builder.AddLayer("Estructura");
            var a = builder.AddComponentDefinition("A");
            a.AddFace(Square(), material: red, layer: layer);
            a.AddFace(Square(x0: 300));
            a.Dispose();
            var b = builder.AddComponentDefinition("B");
            b.AddFace(Square(z: 50));
            b.Dispose();
            builder.AddInstance(a);
            builder.AddInstance(b, translation: (0, 0, 100));
            var data = builder.ToBytes();

            Assert.Equal(18L, builder._pidStart);
            Assert.True(builder._pidStart < builder.NextPid());
            Assert.Equal(builder.NextPid(), (long)PidCounter(data));
            Assert.Equal(52u, PidCounter(data));
        }

        [Fact]
        public void GroupsWithoutMaterialsOrLayersStillRaiseTheHeaderCounter()
        {
            // The scaffold's counter is 18; a file of two groups allocates ids
            // 18..39 (nextPid == 40).
            var builder = SkpCreate.NewFile();
            using (var one = builder.AddGroup("one")) one.AddFace(Square());
            using (var two = builder.AddGroup("two")) two.AddFace(Square(x0: 60));
            var data = builder.ToBytes();

            Assert.True(PidCounter(data) > 18u);
            Assert.Equal(builder.NextPid(), (long)PidCounter(data));
            Assert.Equal(40u, PidCounter(data));
        }

        [Fact]
        public void AttributeStringsCrossMfcLengthBoundaries()
        {
            foreach (int length in new[] { 254, 255, 256, 65533, 65534, 65535, 65536 })
            {
                var builder = SkpCreate.NewFile();
                ComponentDefinitionBuilder def;
                using (def = builder.AddComponentDefinition("TextCarrier"))
                {
                    def.AddFace(new[] { (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0) });
                }
                string ascii = new string('x', length);
                string unicode = new string('x', length - 2) + "\U0001F600";
                builder.AddInstance(
                    def,
                    attributes: new System.Collections.Generic.Dictionary<string, object>
                    {
                        ["ascii"] = ascii,
                        ["unicode"] = unicode,
                    },
                    attributeDictName: "dynamic_attributes");

                var model = SkpFile.Parse(builder.ToBytes());
                var inst = Assert.Single(model.Root.Instances);
                Assert.Equal(ascii, inst.Properties["ascii"]);
                Assert.Equal(unicode, inst.Properties["unicode"]);
            }
        }

        [Fact]
        public void MalformedUtf16AttributeStringsUseReplacementCharacters()
        {
            var builder = SkpCreate.NewFile();
            ComponentDefinitionBuilder def;
            using (def = builder.AddComponentDefinition("TextCarrier"))
            {
                def.AddFace(new[] { (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0) });
            }
            builder.AddInstance(
                def,
                attributes: new System.Collections.Generic.Dictionary<string, object>
                {
                    ["damaged"] = "WXYZQ",
                    ["intact"] = "still readable",
                },
                attributeDictName: "dynamic_attributes");

            var bytes = builder.ToBytes();
            byte[] original = { (byte)'W', 0, (byte)'X', 0, (byte)'Y', 0, (byte)'Z', 0, (byte)'Q', 0 };
            byte[] malformed = { 0x00, 0xDC, 0x00, 0xD8, (byte)'Y', 0, 0x3D, 0xD8, 0x00, 0xDE };
            int start = -1;
            for (int i = 0; i + original.Length <= bytes.Length; i++)
            {
                bool match = true;
                for (int j = 0; j < original.Length; j++)
                {
                    if (bytes[i + j] != original[j]) { match = false; break; }
                }
                if (match) { start = i; break; }
            }
            Assert.True(start >= 0);
            System.Array.Copy(malformed, 0, bytes, start, malformed.Length);

            var parsed = SkpFile.Parse(bytes);
            var inst = Assert.Single(parsed.Root.Instances);
            Assert.Equal("\uFFFD\uFFFDY\U0001F600", inst.Properties["damaged"]);
            Assert.Equal("still readable", inst.Properties["intact"]);

            // A trailing high surrogate cannot read into the next archive field.
            bytes[start + 9] = 0xD8;
            var trailing = SkpFile.Parse(bytes);
            var trailingInst = Assert.Single(trailing.Root.Instances);
            Assert.Equal("\uFFFD\uFFFDY\uFFFD\uFFFD", trailingInst.Properties["damaged"]);
        }

        [Fact]
        public void OpaqueTexturedMaterialUsesAlpha255AndStartsPidAt18()
        {
            string pngPath = Path.Combine(Path.GetTempPath(), Guid.NewGuid() + ".png");
            File.WriteAllBytes(pngPath, TinyPng());
            try
            {
                var builder = SkpCreate.NewFile();
                Assert.Equal(18L, builder._pidStart);
                int brick = builder.AddTextureMaterial("Brick", pngPath);
                builder.AddFace(Square(), material: brick);
                var model = SkpFile.Parse(builder.ToBytes());
                var mat = Assert.Single(model.Materials);
                Assert.Equal((255, 255, 255, 255), mat.Color);
                Assert.False(mat.Colorized);
            }
            finally
            {
                File.Delete(pngPath);
            }
        }

        [Fact]
        public void DocumentOptionsPatchUnitsRenderingAndStylesWithoutScaling()
        {
            static uint NativeOption(byte[] raw, string name)
            {
                var key = new List<byte> { 0xff, 0xfe, 0xff, (byte)name.Length };
                foreach (char c in name) { key.Add((byte)c); key.Add(0); }
                int found = -1;
                for (int i = 0; i <= raw.Length - key.Count; i++)
                {
                    bool ok = true;
                    for (int j = 0; j < key.Count; j++) { if (raw[i + j] != key[j]) { ok = false; break; } }
                    if (ok) { found = i; break; }
                }
                Assert.True(found >= 0);
                int pos = found + key.Count;
                Assert.Equal((byte)4, raw[pos]);
                return BitConverter.ToUInt32(raw, pos + 1);
            }

            byte[] styleSig = { 0xa0, 0x0f, 0, 0, 1, 0, 0, 0, 4, 0, 0, 0, 0x12, 0x34, 0x56, 0xff };
            for (int unit = 0; unit <= 5; unit++)
            {
                var builder = SkpCreate.NewFile();
                builder.SetDocumentOptions(new DocumentOptions
                {
                    LengthUnit = unit,
                    LengthPrecision = 2,
                    StyleItems = new Dictionary<int, double>
                    {
                        [4000] = 0xff563412u,
                        [1006] = 0,
                        [1007] = 5,
                    },
                });
                builder.AddFace(new (double, double, double)[] { (0, 0, 0), (2, 0, 0), (0, 3, 0) });
                var raw = builder.ToBytes();
                Assert.Equal((uint)unit, NativeOption(raw, "LengthUnit"));
                Assert.Equal(0u, NativeOption(raw, "LengthFormat"));
                Assert.Equal(2u, NativeOption(raw, "LengthPrecision"));
                Assert.Equal((uint)unit, NativeOption(raw, "AreaUnit"));
                Assert.Equal((uint)unit, NativeOption(raw, "VolumeUnit"));
                Assert.Equal(0xff563412u, BitConverter.ToUInt32(raw, 3177));
                Assert.Equal((byte)0, raw[3206]);
                Assert.Equal(5u, BitConverter.ToUInt32(raw, 3207));

                int matches = 0;
                for (int i = 0; i <= raw.Length - styleSig.Length; i++)
                {
                    bool ok = true;
                    for (int j = 0; j < styleSig.Length; j++) { if (raw[i + j] != styleSig[j]) { ok = false; break; } }
                    if (ok) matches++;
                }
                Assert.Equal(2, matches);
                Assert.Equal((ulong)builder.NextPid(), BitConverter.ToUInt64(raw, 1987));

                var model = SkpFile.Parse(raw);
                Assert.Contains(model.Root.Vertices.Values, v => v.X == 2.0 && v.Y == 0.0 && v.Z == 0.0);
            }
        }

        [Fact]
        public void RejectsInvalidDocumentOptionsAndSkipsDegenerateAutoTriangulateWedge()
        {
            var invalid = SkpCreate.NewFile();
            Assert.Throws<SkpWriteException>(() => invalid.SetDocumentOptions(new DocumentOptions { LengthUnit = 6 }));
            Assert.Throws<SkpWriteException>(() => invalid.SetDocumentOptions(new DocumentOptions { LengthPrecision = 7 }));
            Assert.Throws<SkpWriteException>(() => invalid.SetDocumentOptions(new DocumentOptions
            {
                StyleItems = new Dictionary<int, double> { [1000] = double.NaN },
            }));

            var polygon = new (double, double, double)[] { (0, 0, 0), (1, 0, 0), (1, 1, 0), (2, 2, 0), (0, 1, 1) };
            foreach (bool nested in new[] { false, true })
            {
                var builder = SkpCreate.NewFile();
                if (nested)
                {
                    ComponentDefinitionBuilder def;
                    using (def = builder.AddComponentDefinition("fan"))
                    {
                        def.AddFace(polygon, autoTriangulate: true);
                    }
                    builder.AddInstance(def);
                }
                else
                {
                    builder.AddFace(polygon, autoTriangulate: true);
                }
                builder.AddFace(new (double, double, double)[] { (10, 0, 0), (11, 0, 0), (10, 1, 0) });
                var model = SkpFile.Parse(builder.ToBytes());
                int totalFaces = model.Root.Faces.Count + model.Definitions.Values.Sum(d => d.Faces.Count);
                Assert.Equal(3, totalFaces);
            }
        }

        [Fact]
        public void FaceWithUvAndAttributesWritesDictionariesBeforeTextureCoords()
        {
            string pngPath = Path.Combine(Path.GetTempPath(), Guid.NewGuid() + ".png");
            File.WriteAllBytes(pngPath, TinyPng());
            try
            {
                var builder = SkpCreate.NewFile();
                int brick = builder.AddTextureMaterial("Brick", pngPath, filename: "tex.png");
                builder.AddFace(
                    Square(),
                    material: brick,
                    frontUv: new[]
                    {
                        new UvCorrespondence((0, 0, 0), (0.0, 0.0)),
                        new UvCorrespondence((50, 0, 0), (1.0, 0.0)),
                        new UvCorrespondence((0, 50, 0), (0.0, 1.0)),
                    },
                    attributeDicts: new[]
                    {
                        new AttributeDict("attributes", new Dictionary<string, object> { ["d"] = 1 }),
                        new AttributeDict("second", new Dictionary<string, object> { ["k"] = "v" }),
                    });
                byte[] raw = builder.ToBytes();
                byte[] dictBytes = System.Text.Encoding.Unicode.GetBytes("second");
                byte[] ftcBytes = System.Text.Encoding.ASCII.GetBytes("CFaceTextureCoords");
                int dictPos = IndexOfBytes(raw, dictBytes);
                int ftcPos = IndexOfBytes(raw, ftcBytes);
                Assert.True(dictPos >= 0 && ftcPos >= 0);
                Assert.True(dictPos < ftcPos, "CAttributeNamed dictionaries must precede CFaceTextureCoords");
                var model = SkpFile.Parse(raw);
                Assert.NotNull(model.Root.Faces.Values.Single().UvTransform);
            }
            finally
            {
                File.Delete(pngPath);
            }
        }

        [Fact]
        public void CurvesOverExistingAndRetracingEdgesOmitSharedEdges()
        {
            var sq = new (double, double, double)[] { (0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0) };

            var b1 = SkpCreate.NewFile();
            b1.AddFace(sq);
            b1.AddPolyline(sq, closed: true);
            byte[] raw1 = b1.ToBytes();
            Assert.Equal(-1, IndexOfBytes(raw1, System.Text.Encoding.ASCII.GetBytes("CCurve")));
            Assert.Equal(4, SkpFile.Parse(raw1).Root.Edges.Count);

            var b2 = SkpCreate.NewFile();
            b2.AddFace(sq);
            b2.AddPolyline(new (double, double, double)[]
            {
                (20, -10, 0), (20, 0, 0), (10, 0, 0), (10, 10, 0), (20, 10, 0), (20, 20, 0),
            });
            b2.AddPolyline(new (double, double, double)[] { (0, 30, 0), (10, 30, 0), (0, 30, 0), (10, 30, 0) });
            var m2 = SkpFile.Parse(b2.ToBytes());
            Assert.Equal(9, m2.Root.Edges.Count);
            var curveIds = m2.Root.Edges.Values.Where(e => e.CurveId.HasValue).Select(e => e.CurveId!.Value).ToList();
            Assert.Equal(4, curveIds.Count);
            Assert.Equal(2, curveIds.Distinct().Count());

            var b3 = SkpCreate.NewFile();
            b3.AddCircle((0, 0, 0), (0, 0, 1), 10, numSegments: 8);
            b3.AddCircle((0, 0, 0), (0, 0, 1), 10, numSegments: 8);
            var m3 = SkpFile.Parse(b3.ToBytes());
            Assert.Equal(8, m3.Root.Edges.Count);
            Assert.Single(m3.Root.Arcs);
        }

        [Fact]
        public void ScalesUvPinsByAppliedTextureSizeAcrossFacesCirclesAndEdit()
        {
            string pngPath = Path.Combine(Path.GetTempPath(), "openskp_uv_size_" + Guid.NewGuid() + ".png");
            string skpPath = Path.Combine(Path.GetTempPath(), "openskp_uv_size_" + Guid.NewGuid() + ".skp");
            File.WriteAllBytes(pngPath, TinyPng());
            try
            {
                var quad = new (double, double, double)[] { (50, 0, 0), (50, 40, 0), (0, 40, 0), (0, 0, 0) };
                var downQuad = new (double, double, double)[] { (0, 0, 10), (0, 40, 10), (50, 40, 10), (50, 0, 10) };
                var fPins = new[]
                {
                    new UvCorrespondence((0, 0, 0), (0.0, 0.0)),
                    new UvCorrespondence((50, 0, 0), (2.5, 0.0)),
                    new UvCorrespondence((0, 40, 0), (0.0, 1.0)),
                };
                var downPins = new[]
                {
                    new UvCorrespondence((0, 0, 10), (0.0, 0.0)),
                    new UvCorrespondence((50, 0, 10), (2.5, 0.0)),
                    new UvCorrespondence((0, 40, 10), (0.0, 1.0)),
                };
                var bPins = new[]
                {
                    new UvCorrespondence((0, 0, 0), (0.25, 0.5)),
                    new UvCorrespondence((50, 0, 0), (2.75, 0.5)),
                    new UvCorrespondence((0, 40, 0), (0.25, 2.5)),
                };
                var cfPins = new[]
                {
                    new UvCorrespondence((100, 20, 0), (0.0, 0.0)),
                    new UvCorrespondence((140, 20, 0), (2.0, 0.0)),
                    new UvCorrespondence((120, 40, 0), (0.0, 1.0)),
                };
                var cbPins = new[]
                {
                    new UvCorrespondence((100, 20, 0), (0.5, 0.25)),
                    new UvCorrespondence((140, 20, 0), (2.5, 0.25)),
                    new UvCorrespondence((120, 40, 0), (0.5, 1.25)),
                };

                var b = SkpCreate.NewFile();
                int frontTex = b.AddTextureMaterial("FrontTex", pngPath, appliedHeight: 20.0, appliedWidth: 10.0, filename: "tex.png");
                int backTex = b.AddTextureMaterial("BackTex", pngPath, appliedHeight: 8.0, appliedWidth: 4.0, filename: "tex.png");
                ComponentDefinitionBuilder panel;
                using (panel = b.AddComponentDefinition("Panel"))
                {
                    panel.AddFace(quad, material: frontTex, backMaterial: backTex, frontUv: fPins, backUv: bPins);
                    panel.AddCircle((120, 20, 0), (0, 0, 1), 20.0, numSegments: 8, material: frontTex, backMaterial: backTex, frontUv: cfPins, backUv: cbPins);
                }
                b.AddInstance(panel, translation: (0, 100, 0));
                b.AddFace(quad, material: frontTex, backMaterial: backTex, frontUv: fPins, backUv: bPins);
                b.AddCircle((120, 20, 0), (0, 0, 1), 20.0, numSegments: 8, material: frontTex, backMaterial: backTex, frontUv: cfPins, backUv: cbPins);
                b.AddFace(downQuad, material: frontTex, frontUv: downPins);
                b.Save(skpPath);

                static void CheckModel(byte[] raw)
                {
                    var m = SkpFile.Parse(raw);
                    var fm = m.Materials.Single(x => x.Name == "FrontTex");
                    var bm = m.Materials.Single(x => x.Name == "BackTex");
                    Assert.Equal((10.0, 20.0), (fm.Texture!.Width, fm.Texture.Height));
                    Assert.Equal((4.0, 8.0), (bm.Texture!.Width, bm.Texture.Height));

                    double[] expectedFaceFront = { 2.0, 0.0, 0.0, 0.0, 2.0, 0.0, 0.0, 0.0, 1.0 };
                    double[] expectedFaceBack = { 5.0, 0.0, 0.0, 0.0, 2.5, 0.0, -5.0, -10.0, 1.0 };
                    double[] expectedCircleFront = { 2.0, 0.0, 0.0, 1.0, 1.0, 0.0, 100.0, 20.0, 1.0 };
                    double[] expectedCircleBack = { 5.0, 0.0, 0.0, 2.5, 2.5, 0.0, 85.0, 15.0, 1.0 };
                    double[] expectedDownFront = { -2.0, 0.0, 0.0, 0.0, 2.0, 0.0, 0.0, 0.0, 1.0 };

                    var defFaces = m.Definitions.Values.Single().Faces.Values.ToList();
                    var rootFaces = m.Root.Faces.Values.ToList();
                    foreach (var faces in new[] { defFaces, rootFaces })
                    {
                        for (int i = 0; i < 9; i++)
                        {
                            Assert.Equal(expectedFaceFront[i], faces[0].UvTransform![i], 3);
                            Assert.Equal(expectedFaceBack[i], faces[0].UvTransformBack![i], 3);
                            Assert.Equal(expectedCircleFront[i], faces[1].UvTransform![i], 3);
                            Assert.Equal(expectedCircleBack[i], faces[1].UvTransformBack![i], 3);
                        }
                    }
                    for (int i = 0; i < 9; i++)
                    {
                        Assert.Equal(expectedDownFront[i], rootFaces[2].UvTransform![i], 3);
                    }
                }

                CheckModel(File.ReadAllBytes(skpPath));
                var edited = SkpEdit.OpenExisting(skpPath);
                CheckModel(edited.Builder.ToBytes());
            }
            finally
            {
                if (File.Exists(pngPath)) File.Delete(pngPath);
                if (File.Exists(skpPath)) File.Delete(skpPath);
            }
        }

        private static int IndexOfBytes(byte[] haystack, byte[] needle)
        {
            for (int i = 0; i <= haystack.Length - needle.Length; i++)
            {
                bool match = true;
                for (int j = 0; j < needle.Length; j++)
                {
                    if (haystack[i + j] != needle[j]) { match = false; break; }
                }
                if (match) return i;
            }
            return -1;
        }

        // 1x1 transparent PNG.
        private static byte[] TinyPng() => Convert.FromBase64String(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=");
    }
}
