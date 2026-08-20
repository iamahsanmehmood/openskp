using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>Optional, local-only validation using the real Trimble
    /// SketchUp SDK (SketchUpAPI.dll) as an oracle - never a runtime
    /// dependency of Create.cs itself, purely an offline confidence check
    /// mirroring packages/python/tests/test_create.py's own
    /// TestRealSketchUpOracle class (same skip-if-DLL-absent discipline,
    /// same environment variable override).
    ///
    /// Every test here calls <see cref="SkipIfAbsent"/> first and returns
    /// early when the DLL isn't found - there is no first-class "skipped"
    /// status without an extra test-framework dependency this project
    /// doesn't otherwise need, so an environment without the SDK reports
    /// these as trivially passed rather than skipped; the important
    /// property (CI machines without the DLL never fail here) still
    /// holds.</summary>
    public sealed class RealSketchUpOracleTests : IDisposable
    {
        private static readonly string DllPath = Environment.GetEnvironmentVariable("OPENSKP_TEST_SKETCHUP_SDK_DLL")
            ?? @"C:\Program Files\SketchUp\SketchUp 2025\SketchUp\SketchUpAPI.dll";

        private static bool DllPresent => File.Exists(DllPath);

        private readonly SketchUpSdk? _sdk;

        public RealSketchUpOracleTests()
        {
            if (!DllPresent)
            {
                _sdk = null;
                return;
            }
            try
            {
                _sdk = new SketchUpSdk(DllPath);
            }
            catch (InvalidOperationException)
            {
                // SUInitialize() itself can fail even with the DLL file
                // present - observed on this project's own dev machine
                // with the desktop SketchUp 2025 install's bundled
                // SketchUpAPI.dll (SUInitialize() -> 1, reproduced
                // identically via plain ctypes from Python, so not a bug
                // in this P/Invoke wrapper - Trimble's standalone
                // redistributable SDK, a separate download from the full
                // desktop app, is the officially supported artifact for
                // this kind of out-of-process use). Skip rather than fail
                // the whole suite in that case, the same discipline as the
                // DLL-absent path.
                _sdk = null;
            }
        }

        public void Dispose() => _sdk?.Dispose();

        private bool SkipIfAbsent() => _sdk == null;

        private static string TempSkpPath() => Path.Combine(Path.GetTempPath(), Guid.NewGuid() + ".skp");

        private static (double, double, double)[] Square() => new (double, double, double)[]
        {
            (0, 0, 0), (100, 0, 0), (100, 100, 0), (0, 100, 0),
        };

        [Fact]
        public void SingleFaceLoadsWithCorrectFaceCount()
        {
            if (SkipIfAbsent()) return;
            var builder = SkpCreate.NewFile();
            builder.AddFace(Square());
            string path = TempSkpPath();
            builder.Save(path);
            try
            {
                using var model = _sdk!.OpenModel(path);
                var entities = _sdk.GetEntities(model);
                Assert.Equal(1u, _sdk.GetNumFaces(entities));
            }
            finally
            {
                File.Delete(path);
            }
        }

        [Fact]
        public void MaterialColorsRoundTripThroughRealSketchUp()
        {
            if (SkipIfAbsent()) return;
            var builder = SkpCreate.NewFile();
            int red = builder.AddMaterial("Red", (255, 0, 0));
            int blue = builder.AddMaterial("Blue", (0, 0, 255));
            builder.AddFace(Square(), material: red);
            builder.AddFace(
                new (double, double, double)[] { (100, 0, 0), (200, 0, 0), (200, 100, 0), (100, 100, 0) },
                material: blue);
            string path = TempSkpPath();
            builder.Save(path);
            try
            {
                using var model = _sdk!.OpenModel(path);
                var entities = _sdk.GetEntities(model);
                var faces = _sdk.GetFaces(entities, 2);
                var colors = faces.Select(f => _sdk.GetFrontMaterialColor(f)).ToHashSet();
                Assert.Contains((255, 0, 0), colors);
                Assert.Contains((0, 0, 255), colors);
            }
            finally
            {
                File.Delete(path);
            }
        }

        [Fact]
        public void BackMaterialRoundTripsThroughRealSketchUp()
        {
            if (SkipIfAbsent()) return;
            var builder = SkpCreate.NewFile();
            int red = builder.AddMaterial("Red", (255, 0, 0));
            int green = builder.AddMaterial("Green", (0, 255, 0));
            builder.AddFace(Square(), material: red, backMaterial: green);
            string path = TempSkpPath();
            builder.Save(path);
            try
            {
                using var model = _sdk!.OpenModel(path);
                var entities = _sdk.GetEntities(model);
                var face = _sdk.GetFaces(entities, 1).Single();
                Assert.Equal((255, 0, 0), _sdk.GetFrontMaterialColor(face));
                Assert.Equal((0, 255, 0), _sdk.GetBackMaterialColor(face));
            }
            finally
            {
                File.Delete(path);
            }
        }

        [Fact]
        public void MaterialsAndLayersRoundTripThroughRealSketchUp()
        {
            if (SkipIfAbsent()) return;
            var builder = SkpCreate.NewFile();
            int red = builder.AddMaterial("Red", (255, 0, 0));
            int blue = builder.AddMaterial("Blue", (0, 0, 255));
            int roof = builder.AddLayer("Roof");
            int walls = builder.AddLayer("Walls");
            builder.AddFace(Square(), material: red, layer: roof);
            builder.AddFace(
                new (double, double, double)[] { (100, 0, 0), (200, 0, 0), (200, 100, 0), (100, 100, 0) },
                material: blue, layer: walls);
            string path = TempSkpPath();
            builder.Save(path);
            try
            {
                using var model = _sdk!.OpenModel(path);
                var entities = _sdk.GetEntities(model);
                var faces = _sdk.GetFaces(entities, 2);
                var names = faces.Select(f => _sdk.GetLayerName(_sdk.GetLayer(f))).ToHashSet();
                Assert.Contains("Roof", names);
                Assert.Contains("Walls", names);
            }
            finally
            {
                File.Delete(path);
            }
        }

        [Fact]
        public void PngTextureMaterialRoundTripsThroughRealSketchUp()
        {
            if (SkipIfAbsent()) return;
            string pngPath = Path.Combine(Path.GetTempPath(), Guid.NewGuid() + ".png");
            File.WriteAllBytes(pngPath, TinyPng8x8());
            var builder = SkpCreate.NewFile();
            int tex = builder.AddTextureMaterial("Checker", pngPath);
            builder.AddFace(Square(), material: tex);
            string path = TempSkpPath();
            builder.Save(path);
            try
            {
                using var model = _sdk!.OpenModel(path);
                var entities = _sdk.GetEntities(model);
                var face = _sdk.GetFaces(entities, 1).Single();
                var mat = _sdk.GetFrontMaterial(face);
                var (w, h) = _sdk.GetTextureDimensions(_sdk.GetMaterialTexture(mat));
                Assert.Equal((8u, 8u), (w, h));
            }
            finally
            {
                File.Delete(path);
                File.Delete(pngPath);
            }
        }

        [Fact]
        public void HiddenSoftSmoothFlagsRoundTripThroughRealSketchUp()
        {
            if (SkipIfAbsent()) return;
            var builder = SkpCreate.NewFile();
            builder.AddFace(Square(), hidden: true, softEdges: true, smoothEdges: true, hiddenEdges: true);
            string path = TempSkpPath();
            builder.Save(path);
            try
            {
                using var model = _sdk!.OpenModel(path);
                var entities = _sdk.GetEntities(model);
                var face = _sdk.GetFaces(entities, 1).Single();
                Assert.True(_sdk.GetHidden(face));
                var edges = _sdk.GetFaceEdges(face, 4);
                Assert.Equal(4, edges.Count);
                foreach (var edge in edges)
                {
                    Assert.True(_sdk.GetHidden(edge));
                    Assert.True(_sdk.GetEdgeSoft(edge));
                    Assert.True(_sdk.GetEdgeSmooth(edge));
                }
            }
            finally
            {
                File.Delete(path);
            }
        }

        [Fact]
        public void ComponentInstancesRoundTripThroughRealSketchUp()
        {
            if (SkipIfAbsent()) return;
            var builder = SkpCreate.NewFile();
            ComponentDefinitionBuilder chair;
            using (chair = builder.AddComponentDefinition("Chair"))
            {
                chair.AddFace(Square());
            }
            for (int i = 0; i < 5; i++)
            {
                builder.AddInstance(chair, name: $"Chair{i}", translation: (i * 40.0, 0.0, 0.0));
            }
            string path = TempSkpPath();
            builder.Save(path);
            try
            {
                using var model = _sdk!.OpenModel(path);
                var entities = _sdk.GetEntities(model);
                Assert.Equal(5u, _sdk.GetNumInstances(entities));
                var insts = _sdk.GetInstances(entities, 5);
                var translations = insts.Select(i => _sdk.GetInstanceTransform(i)[12]).OrderBy(x => x).ToArray();
                Assert.Equal(new[] { 0.0, 40.0, 80.0, 120.0, 160.0 }, translations);
            }
            finally
            {
                File.Delete(path);
            }
        }

        [Fact]
        public void GroupRoundTripsThroughRealSketchUp()
        {
            if (SkipIfAbsent()) return;
            var builder = SkpCreate.NewFile();
            using (var table = builder.AddGroup("Table", translation: (50.0, 0.0, 0.0)))
            {
                table.AddFace(Square());
            }
            string path = TempSkpPath();
            builder.Save(path);
            try
            {
                using var model = _sdk!.OpenModel(path);
                var entities = _sdk.GetEntities(model);
                Assert.Equal((UIntPtr)1, _sdk.GetNumGroups(entities));
                var group = _sdk.GetGroups(entities, 1).Single();
                var xf = _sdk.GetGroupTransform(group);
                Assert.Equal((50.0, 0.0, 0.0), (xf[12], xf[13], xf[14]));
            }
            finally
            {
                File.Delete(path);
            }
        }

        [Fact]
        public void CircleRecognizedAsTrueCurveByRealSketchUp()
        {
            // The key claim AddCircle makes beyond "N straight edges that
            // happen to trace a circle": every edge's own curve pointer
            // (SUEdgeGetCurve) resolves to the exact SAME real curve
            // object, typed as a genuine arc curve with the right edge
            // count - proof real SketchUp treats this as one editable arc
            // entity, not disconnected geometry that merely looks
            // circular.
            if (SkipIfAbsent()) return;
            var builder = SkpCreate.NewFile();
            builder.AddCircle((50.0, 50.0, 0.0), (0.0, 0.0, 1.0), 40.0, numSegments: 8);
            string path = TempSkpPath();
            builder.Save(path);
            try
            {
                using var model = _sdk!.OpenModel(path);
                var entities = _sdk.GetEntities(model);
                Assert.Equal(1u, _sdk.GetNumFaces(entities));
                var edges = _sdk.GetEntityEdges(entities, 8);
                Assert.Equal(8, edges.Count);
                var curvePtrs = edges.Select(e => _sdk.GetEdgeCurve(e)).ToHashSet();
                Assert.True(curvePtrs.Count == 1, "every edge must share the exact same curve");
                var curve = curvePtrs.Single();
                Assert.Equal(1, _sdk.GetCurveType(curve)); // SUCurveType_ArcCurve
                Assert.Equal((UIntPtr)8, _sdk.GetCurveNumEdges(curve));
            }
            finally
            {
                File.Delete(path);
            }
        }

        [Fact]
        public void ModelCrossingTheSlotBoundaryOpensCleanlyInRealSketchUp()
        {
            // Before the Backref/NewOfKnownClass/ShiftRef fix (ported
            // faithfully from openskp.create's own real, previously-shipped
            // bug - see CreateSlotBoundaryTests.cs), a model whose total
            // archive-slot count crossed 0x7FFF (32767) was rejected
            // outright by the real SDK (SUModelCreateFromFile returning a
            // non-zero error, matching the "Unexpected file format" a user
            // sees in the SketchUp GUI). This is the single strongest
            // available validation of that fix: not just "our own reader
            // parses it back", but "the actual SketchUp engine accepts it".
            if (SkipIfAbsent()) return;
            const int n = 5000;
            var builder = SkpCreate.NewFile();
            for (int i = 0; i < n; i++)
            {
                double x = i * 10.0;
                builder.AddFace(new (double, double, double)[]
                {
                    (x, 0.0, 0.0), (x + 1.0, 0.0, 0.0), (x, 1.0, 0.0),
                });
            }
            string path = TempSkpPath();
            builder.Save(path);
            try
            {
                using var model = _sdk!.OpenModel(path);
                var entities = _sdk.GetEntities(model);
                Assert.Equal((uint)n, _sdk.GetNumFaces(entities));
            }
            finally
            {
                File.Delete(path);
            }
        }

        // 8x8 solid-color PNG (matches the fixture size Python's own oracle
        // suite uses for its texture-dimension assertions).
        private static byte[] TinyPng8x8()
        {
            // Minimal valid 8x8 RGB PNG, uncompressed (zlib stored blocks).
            // Built once, inline, rather than shelling out to an image
            // library this project doesn't otherwise depend on.
            using var ms = new MemoryStream();
            using (var writer = new BinaryWriter(ms))
            {
                writer.Write(new byte[] { 0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A });
                WriteChunk(writer, "IHDR", BuildIhdr(8, 8));
                byte[] raw = BuildRawScanlines(8, 8, (60, 180, 75));
                byte[] idat = ZlibStore(raw);
                WriteChunk(writer, "IDAT", idat);
                WriteChunk(writer, "IEND", Array.Empty<byte>());
            }
            return ms.ToArray();
        }

        private static byte[] BuildIhdr(int w, int h)
        {
            var b = new byte[13];
            WriteBE32(b, 0, (uint)w);
            WriteBE32(b, 4, (uint)h);
            b[8] = 8; // bit depth
            b[9] = 2; // color type: truecolor (RGB)
            b[10] = 0; b[11] = 0; b[12] = 0;
            return b;
        }

        private static byte[] BuildRawScanlines(int w, int h, (byte R, byte G, byte B) rgb)
        {
            var raw = new byte[h * (1 + w * 3)];
            int p = 0;
            for (int y = 0; y < h; y++)
            {
                raw[p++] = 0; // filter type: none
                for (int x = 0; x < w; x++)
                {
                    raw[p++] = rgb.R; raw[p++] = rgb.G; raw[p++] = rgb.B;
                }
            }
            return raw;
        }

        // Uncompressed ("stored") zlib stream - valid per RFC 1950/1951
        // without needing a deflate implementation.
        private static byte[] ZlibStore(byte[] data)
        {
            using var ms = new MemoryStream();
            ms.WriteByte(0x78); ms.WriteByte(0x01); // zlib header (no compression)
            int pos = 0;
            while (pos < data.Length || pos == 0)
            {
                int chunk = Math.Min(65535, data.Length - pos);
                bool last = pos + chunk >= data.Length;
                ms.WriteByte((byte)(last ? 1 : 0));
                ms.WriteByte((byte)(chunk & 0xFF));
                ms.WriteByte((byte)((chunk >> 8) & 0xFF));
                ms.WriteByte((byte)(~chunk & 0xFF));
                ms.WriteByte((byte)((~chunk >> 8) & 0xFF));
                ms.Write(data, pos, chunk);
                pos += chunk;
                if (data.Length == 0) break;
            }
            uint adler = Adler32(data);
            var adlerBytes = new byte[4];
            WriteBE32(adlerBytes, 0, adler);
            ms.Write(adlerBytes, 0, 4);
            return ms.ToArray();
        }

        private static uint Adler32(byte[] data)
        {
            uint a = 1, b = 0;
            const uint mod = 65521;
            foreach (var by in data)
            {
                a = (a + by) % mod;
                b = (b + a) % mod;
            }
            return (b << 16) | a;
        }

        private static void WriteBE32(byte[] buf, int offset, uint v)
        {
            buf[offset] = (byte)((v >> 24) & 0xFF);
            buf[offset + 1] = (byte)((v >> 16) & 0xFF);
            buf[offset + 2] = (byte)((v >> 8) & 0xFF);
            buf[offset + 3] = (byte)(v & 0xFF);
        }

        private static uint Crc32(byte[] data)
        {
            uint[] table = Crc32Table.Value;
            uint crc = 0xFFFFFFFF;
            foreach (var b in data)
            {
                crc = table[(crc ^ b) & 0xFF] ^ (crc >> 8);
            }
            return crc ^ 0xFFFFFFFF;
        }

        private static readonly Lazy<uint[]> Crc32Table = new Lazy<uint[]>(() =>
        {
            var table = new uint[256];
            for (uint n = 0; n < 256; n++)
            {
                uint c = n;
                for (int k = 0; k < 8; k++)
                {
                    c = (c & 1) != 0 ? 0xEDB88320 ^ (c >> 1) : c >> 1;
                }
                table[n] = c;
            }
            return table;
        });

        private static void WriteChunk(BinaryWriter writer, string type, byte[] payload)
        {
            var lenBytes = new byte[4];
            WriteBE32(lenBytes, 0, (uint)payload.Length);
            writer.Write(lenBytes);
            var typeBytes = Encoding.ASCII.GetBytes(type);
            writer.Write(typeBytes);
            writer.Write(payload);
            var crcInput = typeBytes.Concat(payload).ToArray();
            var crcBytes = new byte[4];
            WriteBE32(crcBytes, 0, Crc32(crcInput));
            writer.Write(crcBytes);
        }
    }

    /// <summary>Thin P/Invoke-by-handle wrapper around the subset of the
    /// public SketchUp C SDK (SketchUpAPI.dll) these oracle tests need.
    /// Loaded via NativeLibrary.Load at a runtime-provided path (rather
    /// than a compile-time DllImport) since the DLL's location is only
    /// known at test time - mirrors Python's ctypes.CDLL(path) usage in
    /// test_create.py's own oracle suite. cdecl calling convention,
    /// matching that same ctypes.CDLL (not WinDLL/stdcall) choice.</summary>
    internal sealed class SketchUpSdk : IDisposable
    {
        private readonly IntPtr _handle;

        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUInitializeDelegate();
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUTerminateDelegate();
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUModelCreateFromFileDelegate(out IntPtr model, [MarshalAs(UnmanagedType.LPStr)] string path);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUModelReleaseDelegate(ref IntPtr model);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUModelGetEntitiesDelegate(IntPtr model, out IntPtr entities);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUEntitiesGetNumFacesDelegate(IntPtr entities, out UIntPtr count);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUEntitiesGetFacesDelegate(IntPtr entities, UIntPtr len, [Out] IntPtr[] faces, out UIntPtr count);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUFaceGetFrontMaterialDelegate(IntPtr face, out IntPtr material);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUFaceGetBackMaterialDelegate(IntPtr face, out IntPtr material);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUMaterialGetColorDelegate(IntPtr material, out SUColor color);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUMaterialGetTextureDelegate(IntPtr material, out IntPtr texture);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUTextureGetDimensionsDelegate(IntPtr texture, out UIntPtr width, out UIntPtr height, out double sScale, out double tScale);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUDrawingElementGetLayerDelegate(IntPtr element, out IntPtr layer);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUDrawingElementGetHiddenDelegate(IntPtr element, out byte hidden);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SULayerGetNameDelegate(IntPtr layer, IntPtr name);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUStringCreateDelegate(out IntPtr str);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUStringReleaseDelegate(ref IntPtr str);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUStringGetUTF8LengthDelegate(IntPtr str, out UIntPtr length);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUStringGetUTF8Delegate(IntPtr str, UIntPtr len, [Out] byte[] utf8, out UIntPtr count);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUFaceGetEdgesDelegate(IntPtr face, UIntPtr len, [Out] IntPtr[] edges, out UIntPtr count);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUEdgeGetSoftDelegate(IntPtr edge, out byte soft);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUEdgeGetSmoothDelegate(IntPtr edge, out byte smooth);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUEdgeGetCurveDelegate(IntPtr edge, out IntPtr curve);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUCurveGetTypeDelegate(IntPtr curve, out int type);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUCurveGetNumEdgesDelegate(IntPtr curve, out UIntPtr count);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUEntitiesGetNumEdgesDelegate(IntPtr entities, byte stray, out UIntPtr count);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUEntitiesGetEdgesDelegate(IntPtr entities, byte stray, UIntPtr len, [Out] IntPtr[] edges, out UIntPtr count);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUEntitiesGetNumInstancesDelegate(IntPtr entities, out UIntPtr count);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUEntitiesGetInstancesDelegate(IntPtr entities, UIntPtr len, [Out] IntPtr[] instances, out UIntPtr count);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUComponentInstanceGetTransformDelegate(IntPtr instance, [Out] double[] transform);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUEntitiesGetNumGroupsDelegate(IntPtr entities, out UIntPtr count);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUEntitiesGetGroupsDelegate(IntPtr entities, UIntPtr len, [Out] IntPtr[] groups, out UIntPtr count);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SUGroupGetTransformDelegate(IntPtr group, [Out] double[] transform);

        [StructLayout(LayoutKind.Sequential)]
        private struct SUColor
        {
            public byte Red, Green, Blue, Alpha;
        }

        private readonly SUModelCreateFromFileDelegate _modelCreateFromFile;
        private readonly SUModelReleaseDelegate _modelRelease;
        private readonly SUModelGetEntitiesDelegate _modelGetEntities;
        private readonly SUEntitiesGetNumFacesDelegate _entitiesGetNumFaces;
        private readonly SUEntitiesGetFacesDelegate _entitiesGetFaces;
        private readonly SUFaceGetFrontMaterialDelegate _faceGetFrontMaterial;
        private readonly SUFaceGetBackMaterialDelegate _faceGetBackMaterial;
        private readonly SUMaterialGetColorDelegate _materialGetColor;
        private readonly SUMaterialGetTextureDelegate _materialGetTexture;
        private readonly SUTextureGetDimensionsDelegate _textureGetDimensions;
        private readonly SUDrawingElementGetLayerDelegate _drawingElementGetLayer;
        private readonly SUDrawingElementGetHiddenDelegate _drawingElementGetHidden;
        private readonly SULayerGetNameDelegate _layerGetName;
        private readonly SUStringCreateDelegate _stringCreate;
        private readonly SUStringReleaseDelegate _stringRelease;
        private readonly SUStringGetUTF8LengthDelegate _stringGetUtf8Length;
        private readonly SUStringGetUTF8Delegate _stringGetUtf8;
        private readonly SUFaceGetEdgesDelegate _faceGetEdges;
        private readonly SUEdgeGetSoftDelegate _edgeGetSoft;
        private readonly SUEdgeGetSmoothDelegate _edgeGetSmooth;
        private readonly SUEdgeGetCurveDelegate _edgeGetCurve;
        private readonly SUCurveGetTypeDelegate _curveGetType;
        private readonly SUCurveGetNumEdgesDelegate _curveGetNumEdges;
        private readonly SUEntitiesGetNumEdgesDelegate _entitiesGetNumEdges;
        private readonly SUEntitiesGetEdgesDelegate _entitiesGetEdges;
        private readonly SUEntitiesGetNumInstancesDelegate _entitiesGetNumInstances;
        private readonly SUEntitiesGetInstancesDelegate _entitiesGetInstances;
        private readonly SUComponentInstanceGetTransformDelegate _componentInstanceGetTransform;
        private readonly SUEntitiesGetNumGroupsDelegate _entitiesGetNumGroups;
        private readonly SUEntitiesGetGroupsDelegate _entitiesGetGroups;
        private readonly SUGroupGetTransformDelegate _groupGetTransform;

        public SketchUpSdk(string dllPath)
        {
            _handle = NativeLibrary.Load(dllPath);

            var init = Load<SUInitializeDelegate>("SUInitialize");
            _terminate = Load<SUTerminateDelegate>("SUTerminate");
            _modelCreateFromFile = Load<SUModelCreateFromFileDelegate>("SUModelCreateFromFile");
            _modelRelease = Load<SUModelReleaseDelegate>("SUModelRelease");
            _modelGetEntities = Load<SUModelGetEntitiesDelegate>("SUModelGetEntities");
            _entitiesGetNumFaces = Load<SUEntitiesGetNumFacesDelegate>("SUEntitiesGetNumFaces");
            _entitiesGetFaces = Load<SUEntitiesGetFacesDelegate>("SUEntitiesGetFaces");
            _faceGetFrontMaterial = Load<SUFaceGetFrontMaterialDelegate>("SUFaceGetFrontMaterial");
            _faceGetBackMaterial = Load<SUFaceGetBackMaterialDelegate>("SUFaceGetBackMaterial");
            _materialGetColor = Load<SUMaterialGetColorDelegate>("SUMaterialGetColor");
            _materialGetTexture = Load<SUMaterialGetTextureDelegate>("SUMaterialGetTexture");
            _textureGetDimensions = Load<SUTextureGetDimensionsDelegate>("SUTextureGetDimensions");
            _drawingElementGetLayer = Load<SUDrawingElementGetLayerDelegate>("SUDrawingElementGetLayer");
            _drawingElementGetHidden = Load<SUDrawingElementGetHiddenDelegate>("SUDrawingElementGetHidden");
            _layerGetName = Load<SULayerGetNameDelegate>("SULayerGetName");
            _stringCreate = Load<SUStringCreateDelegate>("SUStringCreate");
            _stringRelease = Load<SUStringReleaseDelegate>("SUStringRelease");
            _stringGetUtf8Length = Load<SUStringGetUTF8LengthDelegate>("SUStringGetUTF8Length");
            _stringGetUtf8 = Load<SUStringGetUTF8Delegate>("SUStringGetUTF8");
            _faceGetEdges = Load<SUFaceGetEdgesDelegate>("SUFaceGetEdges");
            _edgeGetSoft = Load<SUEdgeGetSoftDelegate>("SUEdgeGetSoft");
            _edgeGetSmooth = Load<SUEdgeGetSmoothDelegate>("SUEdgeGetSmooth");
            _edgeGetCurve = Load<SUEdgeGetCurveDelegate>("SUEdgeGetCurve");
            _curveGetType = Load<SUCurveGetTypeDelegate>("SUCurveGetType");
            _curveGetNumEdges = Load<SUCurveGetNumEdgesDelegate>("SUCurveGetNumEdges");
            _entitiesGetNumEdges = Load<SUEntitiesGetNumEdgesDelegate>("SUEntitiesGetNumEdges");
            _entitiesGetEdges = Load<SUEntitiesGetEdgesDelegate>("SUEntitiesGetEdges");
            _entitiesGetNumInstances = Load<SUEntitiesGetNumInstancesDelegate>("SUEntitiesGetNumInstances");
            _entitiesGetInstances = Load<SUEntitiesGetInstancesDelegate>("SUEntitiesGetInstances");
            _componentInstanceGetTransform = Load<SUComponentInstanceGetTransformDelegate>("SUComponentInstanceGetTransform");
            _entitiesGetNumGroups = Load<SUEntitiesGetNumGroupsDelegate>("SUEntitiesGetNumGroups");
            _entitiesGetGroups = Load<SUEntitiesGetGroupsDelegate>("SUEntitiesGetGroups");
            _groupGetTransform = Load<SUGroupGetTransformDelegate>("SUGroupGetTransform");

            int err = init();
            if (err != 0)
            {
                NativeLibrary.Free(_handle);
                throw new InvalidOperationException($"SUInitialize failed with error {err}");
            }
        }

        private readonly SUTerminateDelegate _terminate;

        private T Load<T>(string name) where T : Delegate
        {
            IntPtr fn = NativeLibrary.GetExport(_handle, name);
            return Marshal.GetDelegateForFunctionPointer<T>(fn);
        }

        public sealed class OracleModel : IDisposable
        {
            private readonly SketchUpSdk _sdk;
            internal IntPtr Handle;
            internal OracleModel(SketchUpSdk sdk, IntPtr handle) { _sdk = sdk; Handle = handle; }
            public void Dispose()
            {
                if (Handle != IntPtr.Zero)
                {
                    _sdk._modelRelease(ref Handle);
                    Handle = IntPtr.Zero;
                }
            }
        }

        public OracleModel OpenModel(string path)
        {
            int err = _modelCreateFromFile(out var model, path);
            if (err != 0)
            {
                throw new InvalidOperationException($"SketchUp SDK rejected the file (error {err}): {path}");
            }
            return new OracleModel(this, model);
        }

        public IntPtr GetEntities(OracleModel model)
        {
            Check(_modelGetEntities(model.Handle, out var entities));
            return entities;
        }

        public uint GetNumFaces(IntPtr entities)
        {
            Check(_entitiesGetNumFaces(entities, out var count));
            return (uint)count;
        }

        public List<IntPtr> GetFaces(IntPtr entities, int count)
        {
            var buf = new IntPtr[count];
            Check(_entitiesGetFaces(entities, (UIntPtr)count, buf, out var got));
            return buf.Take((int)(uint)got).ToList();
        }

        public IntPtr GetFrontMaterial(IntPtr face)
        {
            Check(_faceGetFrontMaterial(face, out var mat));
            return mat;
        }

        public (int R, int G, int B) GetFrontMaterialColor(IntPtr face)
        {
            var mat = GetFrontMaterial(face);
            Check(_materialGetColor(mat, out var color));
            return (color.Red, color.Green, color.Blue);
        }

        public (int R, int G, int B) GetBackMaterialColor(IntPtr face)
        {
            Check(_faceGetBackMaterial(face, out var mat));
            Check(_materialGetColor(mat, out var color));
            return (color.Red, color.Green, color.Blue);
        }

        public IntPtr GetMaterialTexture(IntPtr material)
        {
            Check(_materialGetTexture(material, out var texture));
            return texture;
        }

        public (uint W, uint H) GetTextureDimensions(IntPtr texture)
        {
            Check(_textureGetDimensions(texture, out var w, out var h, out _, out _));
            return ((uint)w, (uint)h);
        }

        public IntPtr GetLayer(IntPtr element)
        {
            Check(_drawingElementGetLayer(element, out var layer));
            return layer;
        }

        public bool GetHidden(IntPtr element)
        {
            Check(_drawingElementGetHidden(element, out var hidden));
            return hidden != 0;
        }

        public string GetLayerName(IntPtr layer)
        {
            Check(_stringCreate(out var sref));
            try
            {
                Check(_layerGetName(layer, sref));
                Check(_stringGetUtf8Length(sref, out var length));
                var buf = new byte[(int)(uint)length + 1];
                Check(_stringGetUtf8(sref, (UIntPtr)buf.Length, buf, out var outLen));
                return Encoding.UTF8.GetString(buf, 0, (int)(uint)outLen);
            }
            finally
            {
                _stringRelease(ref sref);
            }
        }

        public List<IntPtr> GetFaceEdges(IntPtr face, int count)
        {
            var buf = new IntPtr[count];
            Check(_faceGetEdges(face, (UIntPtr)count, buf, out var got));
            return buf.Take((int)(uint)got).ToList();
        }

        public bool GetEdgeSoft(IntPtr edge)
        {
            Check(_edgeGetSoft(edge, out var v));
            return v != 0;
        }

        public bool GetEdgeSmooth(IntPtr edge)
        {
            Check(_edgeGetSmooth(edge, out var v));
            return v != 0;
        }

        public IntPtr GetEdgeCurve(IntPtr edge)
        {
            Check(_edgeGetCurve(edge, out var curve));
            return curve;
        }

        public int GetCurveType(IntPtr curve)
        {
            Check(_curveGetType(curve, out var t));
            return t;
        }

        public UIntPtr GetCurveNumEdges(IntPtr curve)
        {
            Check(_curveGetNumEdges(curve, out var n));
            return n;
        }

        public List<IntPtr> GetEntityEdges(IntPtr entities, int count)
        {
            var buf = new IntPtr[count];
            Check(_entitiesGetEdges(entities, 0, (UIntPtr)count, buf, out var got));
            return buf.Take((int)(uint)got).ToList();
        }

        public uint GetNumInstances(IntPtr entities)
        {
            Check(_entitiesGetNumInstances(entities, out var count));
            return (uint)count;
        }

        public List<IntPtr> GetInstances(IntPtr entities, int count)
        {
            var buf = new IntPtr[count];
            Check(_entitiesGetInstances(entities, (UIntPtr)count, buf, out var got));
            return buf.Take((int)(uint)got).ToList();
        }

        public double[] GetInstanceTransform(IntPtr instance)
        {
            var xf = new double[16];
            Check(_componentInstanceGetTransform(instance, xf));
            return xf;
        }

        public UIntPtr GetNumGroups(IntPtr entities)
        {
            Check(_entitiesGetNumGroups(entities, out var count));
            return count;
        }

        public List<IntPtr> GetGroups(IntPtr entities, int count)
        {
            var buf = new IntPtr[count];
            Check(_entitiesGetGroups(entities, (UIntPtr)count, buf, out var got));
            return buf.Take((int)(uint)got).ToList();
        }

        public double[] GetGroupTransform(IntPtr group)
        {
            var xf = new double[16];
            Check(_groupGetTransform(group, xf));
            return xf;
        }

        private static void Check(int err)
        {
            if (err != 0) throw new InvalidOperationException($"SketchUp SDK call failed with error {err}");
        }

        public void Dispose()
        {
            _terminate();
            if (_handle != IntPtr.Zero) NativeLibrary.Free(_handle);
        }
    }
}
