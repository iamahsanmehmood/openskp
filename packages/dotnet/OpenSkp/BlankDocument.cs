using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;

namespace OpenSkp
{
    /// <summary>
    /// Serialized empty v17 <c>.skp</c> document together with every named splice
    /// position, field offset, length, and MFC store-map slot <c>Create.cs</c> needs.
    /// </summary>
    internal sealed class BlankDocument
    {
        internal byte[] Data { get; }
        internal int ModelGuidOffset { get; }
        internal int PidCounterOffset { get; }
        internal int NextPid { get; }
        internal int NoScaleMaskOffset { get; }
        internal IReadOnlyDictionary<string, int> UnitOptionOffsets { get; }
        internal int AttrContainerSlot { get; }
        internal int AttributeNamedSlot { get; }
        internal int CCameraSlot { get; }
        internal int CameraBodyOffset { get; }
        internal int CameraBodyLen { get; }
        internal int RenderingOptionsOffset { get; }
        internal int RenderingOptionsLen { get; }
        internal (int Id, int Offset, bool IsBool)[] RenderingOptionFields { get; }
        internal int MaterialCountPos { get; }
        internal int MaterialInsertPos { get; }
        internal int MaterialBaseSlot { get; }
        internal int LayerCountPos { get; }
        internal int OrigLayerCount { get; }
        internal int LayerInsertPos { get; }
        internal int LayerWriterBaseSlot { get; }
        internal int ActiveLayerRefPos { get; }
        internal int DefCountPos { get; }
        internal int OrigDefCount { get; }
        internal int RootCountPos { get; }
        internal int OrigRootCount { get; }
        internal int TailPos { get; }
        internal int ScaffoldNextSlot { get; }
        internal IReadOnlyDictionary<string, int> ScaffoldClassSlot { get; }
        internal int ShadowInfoTailRel { get; }
        internal int ShadowInfoLen { get; }
        internal int PageListTailRel { get; }
        internal int[] FontTailRefs { get; }
        internal int StyleObjectTailRel { get; }
        internal (int SelectedRef, int WorkingRef) StyleTailRefs { get; }
        internal int[] TailRefPositions { get; }
        internal int SavedStyleGuidTailRel { get; }
        internal int WorkingStyleGuidTailRel { get; }
        internal int SavedStyleItemsTailRel { get; }
        internal int WorkingStyleItemsTailRel { get; }
        internal int StyleItemCount { get; }

        internal BlankDocument(
            byte[] data,
            int modelGuidOffset,
            int pidCounterOffset,
            int nextPid,
            int noScaleMaskOffset,
            IReadOnlyDictionary<string, int> unitOptionOffsets,
            int attrContainerSlot,
            int attributeNamedSlot,
            int ccameraSlot,
            int cameraBodyOffset,
            int cameraBodyLen,
            int renderingOptionsOffset,
            int renderingOptionsLen,
            (int Id, int Offset, bool IsBool)[] renderingOptionFields,
            int materialCountPos,
            int materialInsertPos,
            int materialBaseSlot,
            int layerCountPos,
            int origLayerCount,
            int layerInsertPos,
            int layerWriterBaseSlot,
            int activeLayerRefPos,
            int defCountPos,
            int origDefCount,
            int rootCountPos,
            int origRootCount,
            int tailPos,
            int scaffoldNextSlot,
            IReadOnlyDictionary<string, int> scaffoldClassSlot,
            int shadowInfoTailRel,
            int shadowInfoLen,
            int pageListTailRel,
            int[] fontTailRefs,
            int styleObjectTailRel,
            (int SelectedRef, int WorkingRef) styleTailRefs,
            int[] tailRefPositions,
            int savedStyleGuidTailRel,
            int workingStyleGuidTailRel,
            int savedStyleItemsTailRel,
            int workingStyleItemsTailRel,
            int styleItemCount)
        {
            Data = data;
            ModelGuidOffset = modelGuidOffset;
            PidCounterOffset = pidCounterOffset;
            NextPid = nextPid;
            NoScaleMaskOffset = noScaleMaskOffset;
            UnitOptionOffsets = unitOptionOffsets;
            AttrContainerSlot = attrContainerSlot;
            AttributeNamedSlot = attributeNamedSlot;
            CCameraSlot = ccameraSlot;
            CameraBodyOffset = cameraBodyOffset;
            CameraBodyLen = cameraBodyLen;
            RenderingOptionsOffset = renderingOptionsOffset;
            RenderingOptionsLen = renderingOptionsLen;
            RenderingOptionFields = renderingOptionFields;
            MaterialCountPos = materialCountPos;
            MaterialInsertPos = materialInsertPos;
            MaterialBaseSlot = materialBaseSlot;
            LayerCountPos = layerCountPos;
            OrigLayerCount = origLayerCount;
            LayerInsertPos = layerInsertPos;
            LayerWriterBaseSlot = layerWriterBaseSlot;
            ActiveLayerRefPos = activeLayerRefPos;
            DefCountPos = defCountPos;
            OrigDefCount = origDefCount;
            RootCountPos = rootCountPos;
            OrigRootCount = origRootCount;
            TailPos = tailPos;
            ScaffoldNextSlot = scaffoldNextSlot;
            ScaffoldClassSlot = scaffoldClassSlot;
            ShadowInfoTailRel = shadowInfoTailRel;
            ShadowInfoLen = shadowInfoLen;
            PageListTailRel = pageListTailRel;
            FontTailRefs = fontTailRefs;
            StyleObjectTailRel = styleObjectTailRel;
            StyleTailRefs = styleTailRefs;
            TailRefPositions = tailRefPositions;
            SavedStyleGuidTailRel = savedStyleGuidTailRel;
            WorkingStyleGuidTailRel = workingStyleGuidTailRel;
            SavedStyleItemsTailRel = savedStyleItemsTailRel;
            WorkingStyleItemsTailRel = workingStyleItemsTailRel;
            StyleItemCount = styleItemCount;
        }
    }

    internal static class BlankDocumentBuilder
    {
        internal const string ScaffoldSha256 = "809a1ab73a20a192ab13aaff197afb1c67d0e9352f6a353a9cd8030919f8a6c3";

        private static readonly byte[] Utf16StringMarker = { 0xFF, 0xFE, 0xFF };

        // CAttributeNamed value-type tags used in OptionsManager providers and ModelProperties.
        private const byte AttrInt32 = 0x04;
        private const byte AttrDouble = 0x06;
        private const byte AttrBool = 0x07;

        // CSkpStyle typed-property value tags.
        private const uint StyleBool = 1;
        private const uint StyleUInt32 = 4;
        private const uint StyleFloat32 = 6;
        private const uint StyleFloat64 = 7;

        // File-header defaults matching the SketchUp 2017 blank document.
        private const string FileMagicText = "SketchUp Model";
        private const string VersionStringV17 = "{17.0.1}";
        private const string DefaultModelGuid = "3093d39a-aa69-4e29-8d53-15cbfe3d94b0";
        private const uint DefaultSaveTimestampUnix = 1786620315u; // 2026-08-13T11:25:15Z
        private const uint Utf16LeCodepage = 1200u; // Windows UTF-16LE code page (0x04B0)
        private const int InitialNextPersistentId = 18;

        // Persistent entity IDs assigned in the empty document.
        private const int SavedStylePid = 1;
        private const int DimStyleFontPid = 2;
        private const int TextStyleFontPid = 3;
        private const int Layer0Pid = 4;
        private const int Layer0MaterialPid = 5;

        // Saved and working CSkpStyle GUIDs in the empty document.
        private const string DefaultSavedStyleGuid = "94fee66b-2dce-4b79-ada7-4be56f9266a2";
        private const string DefaultWorkingStyleGuid = "667aac7a-d15a-409d-9d21-24ffa8045e61";

        // Default shadow / geo-location settings ("Boulder (CO)", "USA", summer solstice).
        private const uint DefaultShadowTimeUnix = 1782048600u; // 2026-06-21T13:30:00Z
        private const string DefaultCity = "Boulder (CO)";
        private const string DefaultCountry = "USA";
        private const double DefaultLongitude = -105.283;
        private const double DefaultLatitude = 40.017;
        private const double DefaultTzOffset = -7.0;
        private const uint DefaultShadowLight = 80u;
        private const uint DefaultShadowDark = 20u;

        // SketchUp 2017 CVersionMap schema table (alphabetical class name -> schema version).
        internal static readonly (string ClassName, uint Schema)[] VersionMapV17 =
        {
            ("CArcCurve", 3),
            ("CAttribute", 0),
            ("CAttributeContainer", 0),
            ("CAttributeNamed", 1),
            ("CBackgroundImage", 10),
            ("CCamera", 5),
            ("CComponent", 11),
            ("CComponentBehavior", 5),
            ("CComponentDefinition", 10),
            ("CComponentInstance", 5),
            ("CConstructionGeometry", 0),
            ("CConstructionLine", 1),
            ("CConstructionPoint", 0),
            ("CCurve", 4),
            ("CDefinitionList", 0),
            ("CDib", 3),
            ("CDimension", 1),
            ("CDimensionLinear", 6),
            ("CDimensionRadial", 2),
            ("CDimensionStyle", 4),
            ("CDrawingElement", 9),
            ("CEdge", 2),
            ("CEdgeUse", 1),
            ("CEntity", 5),
            ("CFace", 3),
            ("CFaceTextureCoords", 4),
            ("CFontManager", 0),
            ("CGroup", 1),
            ("CImage", 1),
            ("CLayer", 2),
            ("CLayerManager", 4),
            ("CLoop", 1),
            ("CMaterial", 12),
            ("CMaterialManager", 4),
            ("CPageList", 1),
            ("CPolyline3d", 0),
            ("CRelationship", 0),
            ("CRelationshipMap", 0),
            ("CRenderingOptions", 36),
            ("CSchemaFile", 1),
            ("CSchemaFilterFile", 0),
            ("CSchemaZipFile", 1),
            ("CSectionPlane", 2),
            ("CShadowInfo", 7),
            ("CSkFont", 1),
            ("CSketchCS", 0),
            ("CSketchUpModel", 26),
            ("CSketchUpPage", 1),
            ("CSkpStyle", 1),
            ("CSkpStyleManager", 2),
            ("CText", 9),
            ("CTextStyle", 5),
            ("CTexture", 6),
            ("CThumbnail", 1),
            ("CVertex", 0),
            ("CViewPage", 12),
            ("CWatermark", 1),
            ("CWatermarkManager", 2),
        };

        private const string VersionMapSentinel = "End-Of-Version-Map";

        private static uint PackAbgr(byte r, byte g, byte b, byte a = 255) =>
            (uint)r | ((uint)g << 8) | ((uint)b << 16) | ((uint)a << 24);

        internal static byte[] UuidToBytesLe(string canonicalGuid)
        {
            string hex = canonicalGuid.Replace("-", string.Empty);
            var raw = new byte[16];
            for (int i = 0; i < 16; i++)
            {
                raw[i] = Convert.ToByte(hex.Substring(2 * i, 2), 16);
            }
            int[] order = { 3, 2, 1, 0, 5, 4, 7, 6, 8, 9, 10, 11, 12, 13, 14, 15 };
            var result = new byte[16];
            for (int i = 0; i < 16; i++)
            {
                result[i] = raw[order[i]];
            }
            return result;
        }

        private static byte[] EncodeUtf16Str(string text)
        {
            byte[] raw = Encoding.Unicode.GetBytes(text);
            int codeUnits = raw.Length / 2;
            var outBuf = new List<byte>(10 + raw.Length);
            outBuf.AddRange(Utf16StringMarker);
            if (codeUnits < 0xFF)
            {
                outBuf.Add((byte)codeUnits);
            }
            else if (codeUnits < 0xFFFF)
            {
                outBuf.Add(0xFF);
                outBuf.Add((byte)(codeUnits & 0xFF));
                outBuf.Add((byte)((codeUnits >> 8) & 0xFF));
            }
            else
            {
                outBuf.Add(0xFF);
                outBuf.Add(0xFF);
                outBuf.Add(0xFF);
                uint cu = (uint)codeUnits;
                outBuf.Add((byte)(cu & 0xFF));
                outBuf.Add((byte)((cu >> 8) & 0xFF));
                outBuf.Add((byte)((cu >> 16) & 0xFF));
                outBuf.Add((byte)((cu >> 24) & 0xFF));
            }
            outBuf.AddRange(raw);
            return outBuf.ToArray();
        }

        private static void AppendF64(List<byte> buf, double v)
        {
            long bits = BitConverter.DoubleToInt64Bits(v);
            for (int i = 0; i < 8; i++)
            {
                buf.Add((byte)((bits >> (8 * i)) & 0xFF));
            }
        }

        internal static byte[] PackCameraBody(
            (double X, double Y, double Z) eye,
            (double X, double Y, double Z) target,
            (double X, double Y, double Z) up,
            double clipNear = 1.0,
            double clipFar = 1000.0,
            bool perspective = true,
            double fov = 30.0,
            double orthoHeight = 133.0,
            (double X, double Y, double Z)? windowParams = null,
            double aspectRatio = 0.0,
            bool fovIsHeight = true,
            byte cameraFlag = 0,
            string description = "",
            double imageWidth = 0.0,
            bool is2d = false,
            double scale2d = 1.0,
            (double X, double Y)? center2d = null)
        {
            var wp = windowParams ?? (0.0, 0.0, 0.0);
            var c2 = center2d ?? (0.0, 0.0);
            var outBuf = new List<byte>(176 + description.Length * 2);
            AppendF64(outBuf, eye.X);
            AppendF64(outBuf, eye.Y);
            AppendF64(outBuf, eye.Z);
            AppendF64(outBuf, target.X);
            AppendF64(outBuf, target.Y);
            AppendF64(outBuf, target.Z);
            AppendF64(outBuf, up.X);
            AppendF64(outBuf, up.Y);
            AppendF64(outBuf, up.Z);
            AppendF64(outBuf, clipNear);
            AppendF64(outBuf, clipFar);
            outBuf.Add(perspective ? (byte)1 : (byte)0);
            AppendF64(outBuf, fov);
            AppendF64(outBuf, orthoHeight);
            AppendF64(outBuf, wp.X);
            AppendF64(outBuf, wp.Y);
            AppendF64(outBuf, wp.Z);
            AppendF64(outBuf, aspectRatio);
            outBuf.Add(fovIsHeight ? (byte)1 : (byte)0);
            outBuf.Add(cameraFlag);
            outBuf.AddRange(EncodeUtf16Str(description));
            AppendF64(outBuf, imageWidth);
            outBuf.Add(is2d ? (byte)1 : (byte)0);
            AppendF64(outBuf, scale2d);
            AppendF64(outBuf, c2.X);
            AppendF64(outBuf, c2.Y);
            return outBuf.ToArray();
        }

        // Default 56-entry CSkpStyle property table: (KeyId, Kind, Value).
        private static readonly (uint KeyId, uint? Kind, double Value)[] DefaultStyleProperties =
        {
            (1000, StyleUInt32, 1),                              // EdgeDisplayMode: show edges
            (1001, StyleUInt32, 0),                              // EdgeType: standard strokes
            (1002, StyleUInt32, 1),                              // EdgeColorMode: all same
            (1004, StyleBool, 0),                                // ExtendLines: false
            (1005, StyleUInt32, 8),                              // LineExtension: 8 px
            (1006, StyleBool, 1),                                // DrawSilhouettes (Profiles): true
            (1007, StyleUInt32, 3),                              // SilhouetteWidth: 3 px
            (1008, StyleBool, 0),                                // DrawDepthQue: false
            (1009, StyleUInt32, 4),                              // DepthQueWidth: 4 px
            (1010, StyleBool, 0),                                // DrawLineEnds (Endpoints): false
            (1011, StyleUInt32, 5),                              // LineEndWidth: 5 px
            (1012, StyleBool, 0),                                // JitterEdges: false
            (1014, StyleUInt32, PackAbgr(0, 0, 0, 255)),         // ForegroundColor: black
            (1015, StyleBool, 0),                                // DrawBackEdges: false
            (1016, StyleBool, 1),                                // Edge halo / stroke flag: true
            (2001, StyleUInt32, 2),                              // RenderMode: ShadedWithTextures
            (2002, StyleUInt32, PackAbgr(225, 225, 200, 255)),   // FaceFrontColor
            (2003, StyleUInt32, PackAbgr(128, 128, 200, 255)),   // FaceBackColor
            (2004, StyleBool, 0),                                // ModelTransparency (X-ray): false
            (2005, StyleBool, 1),                                // MaterialTransparency: true
            (2006, StyleUInt32, 0),                              // TransparencySort: faster (0)
            (2007, StyleBool, 1),                                // Texture: true
            (2008, StyleFloat64, 0.65),                          // Transparency factor: 0.65
            (4000, StyleUInt32, PackAbgr(255, 255, 255, 255)),   // BackgroundColor: white
            (4001, StyleUInt32, PackAbgr(140, 165, 255, 255)),   // SkyColor
            (4002, StyleBool, 0),                                // DrawHorizon (Sky): false
            (4003, StyleUInt32, PackAbgr(63, 147, 63, 255)),     // GroundColor
            (4004, StyleBool, 0),                                // DrawGround: false
            (4005, StyleUInt32, 50),                             // GroundTransparency: 50
            (4006, StyleBool, 1),                                // DrawUnderground: true
            (4007, StyleUInt32, PackAbgr(0, 0, 0, 0)),           // HorizonColor: transparent black
            (5000, StyleBool, 1),                                // DisplayWatermarks: true
            (5001, null, 0),                                     // Watermarks list: empty (count = 0)
            (7000, StyleUInt32, PackAbgr(255, 255, 0, 255)),     // HighlightColor: yellow
            (7001, StyleUInt32, PackAbgr(255, 0, 0, 255)),       // LockedColor: red
            (7002, StyleUInt32, PackAbgr(128, 128, 128, 255)),   // ConstructionColor: grey
            (7003, StyleUInt32, PackAbgr(102, 110, 141, 255)),   // SectionActiveColor
            (7004, StyleUInt32, PackAbgr(192, 192, 192, 255)),   // SectionInactiveColor
            (7005, StyleUInt32, PackAbgr(255, 0, 0, 255)),       // SectionDefaultCutColor
            (7008, StyleBool, 1),                                // DisplaySketchAxes: true
            (7010, StyleBool, 0),                                // DrawHidden: false
            (7011, StyleBool, 0),                                // DisplayColorByLayer: false
            (7012, StyleBool, 0),                                // HideConstructionGeometry: false
            (7013, StyleUInt32, 3),                              // SectionDisplayMode: planes (1) | cuts (2)
            (7014, StyleUInt32, 4),                              // SectionCutWidth: 4
            (7015, StyleBool, 0),                                // SectionCutFilled: false
            (7016, StyleUInt32, PackAbgr(0, 0, 0, 255)),         // SectionDefaultFillColor: black
            (7017, StyleBool, 0),                                // DrawHiddenGeometry: false
            (7018, StyleBool, 0),                                // DrawHiddenObjects: false
            (8000, StyleBool, 1),                                // Match Photo background image: true
            (8001, StyleFloat64, 1.0),                           // Match Photo background opacity: 1.0
            (8002, StyleBool, 1),                                // Match Photo foreground photo: true
            (8003, StyleFloat64, 0.8),                           // Match Photo foreground opacity: 0.8
            (8100, StyleBool, 0),                                // AmbientOcclusion: false
            (8102, StyleFloat32, 10.0),                          // AmbientOcclusionDistance: 10.0
            (8103, StyleFloat32, 0.0),                           // AmbientOcclusionIntensity: 0.0
        };

        private sealed class BlankArchiveBuilder
        {
            internal readonly List<byte> Buf = new List<byte>(6144);
            internal int NextSlot = 1;
            internal readonly Dictionary<string, int> ClassSlot = new Dictionary<string, int>();

            private void U8(byte value) => Buf.Add(value);

            private void U16(ushort value)
            {
                Buf.Add((byte)(value & 0xFF));
                Buf.Add((byte)((value >> 8) & 0xFF));
            }

            private void U32(uint value)
            {
                Buf.Add((byte)(value & 0xFF));
                Buf.Add((byte)((value >> 8) & 0xFF));
                Buf.Add((byte)((value >> 16) & 0xFF));
                Buf.Add((byte)((value >> 24) & 0xFF));
            }

            private void I32(int value) => U32(unchecked((uint)value));

            private void U64(ulong value)
            {
                for (int i = 0; i < 8; i++)
                {
                    Buf.Add((byte)((value >> (8 * i)) & 0xFF));
                }
            }

            private void F32(float value)
            {
                byte[] bytes = BitConverter.GetBytes(value);
                if (!BitConverter.IsLittleEndian)
                {
                    Array.Reverse(bytes);
                }
                Buf.AddRange(bytes);
            }

            private void F64(double value) => AppendF64(Buf, value);

            private void Rgba(byte r, byte g, byte b, byte a = 255)
            {
                Buf.Add(r);
                Buf.Add(g);
                Buf.Add(b);
                Buf.Add(a);
            }

            private void Uuid(string canonicalGuid) => Buf.AddRange(UuidToBytesLe(canonicalGuid));

            private void Str(string text) => Buf.AddRange(EncodeUtf16Str(text));

            private void Zeros(int count)
            {
                for (int i = 0; i < count; i++)
                {
                    Buf.Add(0);
                }
            }

            private int AllocSlot()
            {
                int slot = NextSlot;
                NextSlot += 1;
                return slot;
            }

            private void NullRef() => U16(0);

            private void Backref(int slot) => U16((ushort)slot);

            private int NewObject(string className, ushort schema)
            {
                if (!ClassSlot.TryGetValue(className, out int cslot))
                {
                    U16(0xFFFF);
                    U16(schema);
                    byte[] ascii = Encoding.ASCII.GetBytes(className);
                    U16((ushort)ascii.Length);
                    Buf.AddRange(ascii);
                    ClassSlot[className] = AllocSlot();
                    return AllocSlot();
                }
                U16((ushort)(0x8000 | cslot));
                return AllocSlot();
            }

            private void EncodePid(long pid)
            {
                byte mask = 0;
                var payload = new List<byte>(8);
                for (int bit = 0; bit < 8; bit++)
                {
                    byte byteVal = (byte)((pid >> (8 * bit)) & 0xFF);
                    if (byteVal != 0)
                    {
                        mask |= (byte)(1 << bit);
                        payload.Add(byteVal);
                    }
                }
                U8(mask);
                Buf.AddRange(payload);
            }

            private void EntityPreamble(long pid = 0)
            {
                NullRef();
                EncodePid(pid);
            }

            private int WriteAttrBool(string key, bool value)
            {
                Str(key);
                U8(AttrBool);
                int valOffset = Buf.Count;
                U8(value ? (byte)1 : (byte)0);
                return valOffset;
            }

            private int WriteAttrInt32(string key, int value)
            {
                Str(key);
                U8(AttrInt32);
                int valOffset = Buf.Count;
                I32(value);
                return valOffset;
            }

            private int WriteAttrDouble(string key, double value)
            {
                Str(key);
                U8(AttrDouble);
                int valOffset = Buf.Count;
                F64(value);
                return valOffset;
            }

            // ── document sections ────────────────────────────────────────────

            internal int WriteFileHeader()
            {
                Str(FileMagicText);
                Str(VersionStringV17);
                int modelGuidOffset = Buf.Count;
                Uuid(DefaultModelGuid);
                Str(string.Empty); // empty release/locale note string
                U32(DefaultSaveTimestampUnix);
                return modelGuidOffset;
            }

            internal void WriteVersionMap()
            {
                NewObject("CVersionMap", 0);
                foreach (var (clsName, schemaVer) in VersionMapV17)
                {
                    Str(clsName);
                    U32(schemaVer);
                }
                Str(VersionMapSentinel);
            }

            internal (int PidCounterOffset, int NoScaleMaskOffset) WriteModelPreamble()
            {
                U32(0); // model header word 0
                U32(1); // model header format flag (1 in v17+)
                U32(Utf16LeCodepage);
                U32(0); // preview thumbnail status (0 = none)
                int pidCounterOffset = Buf.Count;
                U64(InitialNextPersistentId);
                NullRef(); // null model preview CDib pointer
                U32(1); // post-thumbnail model flag
                Zeros(7); // reserved pre-behavior model header bytes (all 0)
                int noScaleMaskOffset = Buf.Count;
                U32(0); // root CComponentBehavior.no_scale_mask = 0
                Str(string.Empty); // model description (SUModelGetDescription)
                U32(3); // options-manager format version
                return (pidCounterOffset, noScaleMaskOffset);
            }

            internal Dictionary<string, int> WriteOptionsProviders()
            {
                U32(4); // four named SUOptionsProvider tables follow

                // 1. NamedOptions (empty)
                Str("NamedOptions");
                Str(string.Empty);

                // 2. PageOptions
                Str("PageOptions");
                WriteAttrBool("ShowTransition", true);
                WriteAttrDouble("TransitionTime", 1.5);
                Str(string.Empty);

                // 3. SlideshowOptions
                Str("SlideshowOptions");
                WriteAttrBool("LoopSlideshow", true);
                WriteAttrDouble("SlideTime", 1.0);
                Str(string.Empty);

                // 4. UnitsOptions
                Str("UnitsOptions");
                var unitOffsets = new Dictionary<string, int>
                {
                    ["LengthPrecision"] = WriteAttrInt32("LengthPrecision", 4),
                    ["LengthFormat"] = WriteAttrInt32("LengthFormat", 1), // 1 = Architectural
                    ["LengthUnit"] = WriteAttrInt32("LengthUnit", 0),     // 0 = Inches
                };
                WriteAttrBool("LengthSnapEnabled", true);
                WriteAttrDouble("LengthSnapLength", 0.0625); // 1/16 inch
                WriteAttrInt32("AnglePrecision", 1);
                WriteAttrBool("AngleSnapEnabled", true);
                WriteAttrDouble("SnapAngle", 15.0);
                WriteAttrBool("SuppressUnitsDisplay", false);
                WriteAttrBool("ForceInchDisplay", false);
                unitOffsets["AreaUnit"] = WriteAttrInt32("AreaUnit", 0);
                unitOffsets["VolumeUnit"] = WriteAttrInt32("VolumeUnit", 0);
                WriteAttrInt32("AreaPrecision", 2);
                WriteAttrInt32("VolumePrecision", 2);
                Str(string.Empty);
                return unitOffsets;
            }

            internal void WriteModelProperties()
            {
                // CAttributeContainer holding one CAttributeNamed("ModelProperties")
                NewObject("CAttributeContainer", 0);
                EntityPreamble(0);

                NewObject("CAttributeNamed", 1);
                EntityPreamble(0);
                U32(0); // CAttribute flags
                Str("ModelProperties");
                WriteAttrBool("IsClassified", false);
                WriteAttrBool("IsDynamic", false);
                WriteAttrBool("IsLive", false);
                Str(string.Empty); // end of CAttributeNamed entries

                NullRef(); // end of CAttributeContainer children
                Zeros(6);  // three null u16 fields before model CCamera
            }

            internal (int BodyOffset, int BodyLen) WriteModelCamera()
            {
                NewObject("CCamera", 5);
                int bodyOffset = Buf.Count;
                byte[] body = PackCameraBody(
                    eye: (0.0, 0.0, 500.0),
                    target: (0.0, 0.0, 0.0),
                    up: (0.0, 1.0, 0.0),
                    clipNear: 1.0,
                    clipFar: 1000.0,
                    perspective: true,
                    fov: 30.0,
                    orthoHeight: 133.0);
                Buf.AddRange(body);
                return (bodyOffset, body.Length);
            }

            internal (int Offset, int Len, (int Id, int Offset, bool IsBool)[] Fields) WriteRenderingOptions()
            {
                EntityPreamble(0); // CRenderingOptions CEntity preamble
                int roStart = Buf.Count;
                var fields = new List<(int Id, int Offset, bool IsBool)>(41);

                void RoU8(byte val, int? styleItem = null)
                {
                    if (styleItem.HasValue)
                    {
                        fields.Add((styleItem.Value, Buf.Count, true));
                    }
                    U8(val);
                }

                void RoU32(uint val, int? styleItem = null)
                {
                    if (styleItem.HasValue)
                    {
                        fields.Add((styleItem.Value, Buf.Count, false));
                    }
                    U32(val);
                }

                void RoRgba(byte r, byte g, byte b, byte a = 255, int? styleItem = null)
                {
                    if (styleItem.HasValue)
                    {
                        fields.Add((styleItem.Value, Buf.Count, false));
                    }
                    Rgba(r, g, b, a);
                }

                RoU32(2, 2001);                         // RenderMode: ShadedWithTextures
                RoU8(0, 2004);                          // ModelTransparency (X-ray)
                RoU8(1, 2005);                          // MaterialTransparency
                RoU8(0, 1012);                          // JitterEdges
                RoU32(1, 1000);                         // EdgeDisplayMode: show edges
                RoRgba(0, 0, 0, 0, 4000);               // BackgroundColor slot (unset until style sync)
                RoRgba(0, 0, 0, 0, 1014);               // ForegroundColor slot (unset until style sync)
                RoRgba(255, 255, 0, 255, 7000);         // HighlightColor
                RoRgba(128, 128, 128, 255, 7002);       // ConstructionColor
                RoU8(0);                                // DisplayShadows
                RoU8(0);                                // DisplayInstanceAxes
                RoU8(0, 7011);                          // DisplayColorByLayer (Color by Tag)
                RoU8(1, 2007);                          // Texture
                RoU32(1, 1002);                         // EdgeColorMode: all same
                RoU8(0, 1004);                          // ExtendLines
                RoU32(8, 1005);                         // LineExtension: 8 px
                RoU8(1, 1006);                          // DrawSilhouettes (Profiles)
                RoU32(3, 1007);                         // SilhouetteWidth: 3 px
                RoU8(0, 1008);                          // DrawDepthQue
                RoU32(4, 1009);                         // DepthQueWidth: 4 px
                RoU8(0, 1010);                          // DrawLineEnds (Endpoints)
                RoU32(5, 1011);                         // LineEndWidth: 5 px
                RoU8(0);                                // DrawProfilesOnly
                RoU8(0, 7010);                          // DrawHidden
                U32(0);                                 // reserved rendering-options word (0)
                RoRgba(225, 225, 200, 255, 2002);       // FaceFrontColor
                RoRgba(128, 128, 200, 255, 2003);       // FaceBackColor
                F64(0.75);                              // InactiveFade
                F64(0.5);                               // InstanceFade
                RoU8(0);                                // InactiveHidden
                RoU8(0);                                // InstanceHidden
                RoU8(0);                                // DisplayFog
                RoRgba(0, 0, 0, 0);                     // FogColor
                RoU8(1);                                // FogUseBkColor
                F64(-1.0);                              // FogStartDist (-1 = automatic)
                F64(-1.0);                              // FogEndDist (-1 = automatic)
                U32(1);                                 // fog / stroke mode constant (1)
                RoU32(0, 1001);                         // EdgeType: standard strokes
                RoU8(1, 7008);                          // DisplaySketchAxes
                RoU8(1);                                // DisplayText
                RoU8(1);                                // DisplayDims
                RoU8(0, 7012);                          // HideConstructionGeometry
                RoRgba(140, 165, 255, 255, 4001);       // SkyColor
                RoRgba(0, 0, 0, 0, 4007);               // HorizonColor
                RoRgba(63, 147, 63, 255, 4003);         // GroundColor
                RoU8(0, 4002);                          // DrawHorizon
                RoU8(0, 4004);                          // DrawGround
                RoU8(1, 4006);                          // DrawUnderground
                RoU32(50, 4005);                        // GroundTransparency: 50
                RoRgba(102, 110, 141, 255, 7003);       // SectionActiveColor
                RoRgba(192, 192, 192, 255, 7004);       // SectionInactiveColor
                RoRgba(255, 0, 0, 255, 7005);           // SectionDefaultCutColor
                RoU32(4, 7014);                         // SectionCutWidth: 4
                RoU32(3);                               // SectionDisplayMode: planes (1) | cuts (2)
                RoU32(0, 2006);                         // TransparencySort: faster (0)
                RoU8(1);                                // edge halo flag (style item 1016)
                F64(0.34906585039886595);               // 20 deg in radians (crease / halo angle)
                RoU8(1);                                // ShowViewName: true
                RoRgba(255, 0, 0, 255, 7001);           // LockedColor
                RoU8(1, 5000);                          // DisplayWatermarks
                F64(0.65);                              // transparency factor (style item 2008)
                RoU8(0, 1015);                          // DrawBackEdges
                RoU8(1);                                // Match Photo background image (item 8000)
                F64(1.0);                               // Match Photo background opacity (item 8001)
                RoU8(1);                                // Match Photo foreground photo (item 8002)
                F64(0.8);                               // Match Photo foreground opacity (item 8003)

                int roLen = Buf.Count - roStart;
                fields.Sort((a, b) => a.Id.CompareTo(b.Id));
                return (roStart, roLen, fields.ToArray());
            }

            internal (int MaterialCountPos, int MaterialInsertPos, int MaterialBaseSlot) WriteComponentAndMaterialManager()
            {
                U32(0); // post-rendering-options word 0
                U32(1); // post-rendering-options word 1
                EntityPreamble(0); // CSketchUpModel CEntity preamble (null model attributes)
                Zeros(3); // CComponent base header pad (offsets 0..2)
                U8(1); // CComponent base flag 0
                U8(1); // CComponent base flag 1
                EntityPreamble(0); // CMaterialManager CEntity preamble
                Zeros(5); // CMaterialManager header pad
                int materialCountPos = Buf.Count;
                U32(0); // material_count = 0
                int materialInsertPos = Buf.Count;
                int materialBaseSlot = NextSlot;
                return (materialCountPos, materialInsertPos, materialBaseSlot);
            }

            internal (
                int LayerCountPos,
                int OrigLayerCount,
                int LayerInsertPos,
                int LayerWriterBaseSlot,
                int ActiveLayerRefPos,
                Dictionary<string, int> MiddleClassSlot) WriteLayerManager()
            {
                NullRef(); // active material ref (null = default unpainted)
                EntityPreamble(0); // CLayerManager CEntity preamble
                int layerCountPos = Buf.Count;
                const int origLayerCount = 1;
                U32(origLayerCount);

                // Default untagged layer "Layer0" (schema 3, layer pid=4, material pid=5)
                int layer0Slot = NewObject("CLayer", 3);
                var middleClassSlot = new Dictionary<string, int> { ["CLayer"] = ClassSlot["CLayer"] };
                EntityPreamble(Layer0Pid);
                Str("Layer0");
                U8(0); // hidden = False
                U16(0); // layer flags = 0
                EncodePid(Layer0MaterialPid);
                Str("Layer_Layer0");
                U16(0x0100); // embedded layer-colour material flag (256)
                Rgba(255, 84, 84, 255); // default Layer0 tag colour
                Str(string.Empty); // empty texture path
                U32(0); // colorize mode = 0
                U32(0); // colorize flags = 0
                F64(0.5); // default layer material alpha/transparency
                U8(0); // use_opacity = False
                U32(0); // layer trailer word = 0

                int layerInsertPos = Buf.Count;
                int layerWriterBaseSlot = NextSlot;
                int activeLayerRefPos = Buf.Count;
                Backref(layer0Slot); // active layer back-ref -> Layer0
                return (
                    layerCountPos,
                    origLayerCount,
                    layerInsertPos,
                    layerWriterBaseSlot,
                    activeLayerRefPos,
                    middleClassSlot);
            }

            internal (
                int DefCountPos,
                int OrigDefCount,
                int RootCountPos,
                int OrigRootCount,
                int TailPos,
                int ScaffoldNextSlot) WriteDefinitionAndRootLists()
            {
                int defCountPos = Buf.Count;
                const int origDefCount = 0;
                U32(origDefCount);

                int rootCountPos = Buf.Count;
                const int origRootCount = 0;
                U32(origRootCount);

                int tailPos = Buf.Count;
                int scaffoldNextSlot = NextSlot;
                return (defCountPos, origDefCount, rootCountPos, origRootCount, tailPos, scaffoldNextSlot);
            }

            internal (int ShadowTailRel, int ShadowLen, int PageListTailRel) WriteShadowInfo(int tailPos)
            {
                U32(0); // root CRelationship list count = 0
                U16(0); // root component closing word = 0
                EntityPreamble(0); // CShadowInfo CEntity preamble

                int shadowStart = Buf.Count;
                U32(DefaultShadowTimeUnix);
                U8(0); // DaylightSavings = False
                Str(DefaultCity);
                Str(DefaultCountry);
                F64(DefaultLongitude);
                F64(DefaultLatitude);
                F64(DefaultTzOffset);
                F64(0.0); // NorthAngle (degrees / sin = 0.0)
                F64(1.0); // North direction Y = cos(0) = 1.0
                F64(0.0); // North direction Z = 0.0
                U8(0); // DisplayNorth = False
                U8(1); // DisplayOnAllFaces = True
                U8(1); // DisplayOnGroundPlane = True
                U8(0); // EdgesCastShadows = False
                U32(DefaultShadowLight);
                U32(DefaultShadowDark);
                int shadowLen = Buf.Count - shadowStart;

                int pageListTailRel = Buf.Count - tailPos;
                U8(0); // UseSunForAllShading = False
                return (shadowStart - tailPos, shadowLen, pageListTailRel);
            }

            internal void WritePageList()
            {
                EntityPreamble(0); // CPageList CEntity preamble
                U32(0); // page_count = 0
                NullRef(); // selected_page_ref = null
            }

            internal void WriteModelAxes()
            {
                Zeros(3); // page-list closing bytes (0)
                EntityPreamble(0); // CSketchCS CEntity preamble
                U8(1); // axis flag 0
                U8(1); // axis flag 1
                Zeros(5); // axis header pad
                F64(0.0); // origin.x
                F64(0.0); // origin.y
                F64(0.0); // origin.z
                F64(1.0); // x_axis.x
                F64(0.0); // x_axis.y
                F64(0.0); // x_axis.z
                F64(0.0); // y_axis.x
                F64(1.0); // y_axis.y
                F64(0.0); // y_axis.z
                F64(0.0); // z_axis.x
                F64(0.0); // z_axis.y
                F64(1.0); // z_axis.z
                Zeros(68); // empty post-axes model tables before CDimensionStyle
            }

            private int WriteSkFont(string faceName, uint pointSize, long pid)
            {
                int slot = NewObject("CSkFont", 1);
                EntityPreamble(pid);
                Str(faceName);
                U16(0); // bold/italic flags = 0
                U32(pointSize);
                U8(0); // use_world_size = False
                F64(1.0); // world_size = 1.0 inch
                return slot;
            }

            internal int WriteDimensionStyle()
            {
                EntityPreamble(0); // CDimensionStyle CEntity preamble
                int arialSlot = WriteSkFont("Arial", 12, DimStyleFontPid);
                U8(0); // has_3d = False
                U8(1); // dimension text visibility / placement flag
                U32(5); // extension_line_offset = 5
                U32(10); // extension_line_overshoot = 10
                U32(1); // line_weight = 1
                U32(3); // arrow_type = 3 (closed arrow)
                U32(10); // arrow_size = 10
                U8(0); // highlight_non_associative = False
                Rgba(255, 0, 0, 255); // highlight_non_associative_color = red
                U8(1); // show_radial_prefix = True
                U8(0); // hide_out_of_plane = False
                F64(0.6); // hide_out_of_plane_value = 0.6
                U8(0); // hide_small = False
                F64(10.0); // hide_small_value = 10.0
                Rgba(64, 64, 64, 255); // dimension colour = dark grey
                U8(0); // use_text_color = False
                Zeros(3); // text alignment / flags = 0
                U8(1); // show_units = True
                Zeros(3); // dimension-style trailer pad
                return arialSlot;
            }

            internal int[] WriteTextStyleAndFontManager(int tailPos, int arialSlot)
            {
                var fontRefs = new List<int>(4);
                EntityPreamble(0); // CTextStyle CEntity preamble

                // Inline CSkFont("Tahoma"): its short class-ref (0x8000|11) shifts when slots grow.
                fontRefs.Add(Buf.Count - tailPos);
                int tahomaSlot = WriteSkFont("Tahoma", 12, TextStyleFontPid);
                U32(2); // leader_type = 2 (pushpin)
                U32(1); // display_leader = 1
                U8(0); // text-style flag = 0
                U32(3); // arrow_type = 3 (closed arrow)
                U8(1); // leader visibility flag = 1
                Rgba(0, 0, 0, 255); // text colour = black
                Rgba(0, 0, 0, 255); // leader colour = black
                fontRefs.Add(Buf.Count - tailPos);
                Backref(tahomaSlot);

                // CFontManager: [preamble][count = 2][Arial ref][Tahoma ref][closing u16 = 0]
                EntityPreamble(0);
                U32(2);
                fontRefs.Add(Buf.Count - tailPos);
                Backref(arialSlot);
                fontRefs.Add(Buf.Count - tailPos);
                Backref(tahomaSlot);
                U16(0);
                return fontRefs.ToArray();
            }

            private int WriteStyleProperties((uint KeyId, uint? Kind, double Value)[] properties)
            {
                U32((uint)properties.Length);
                int itemsStart = Buf.Count;
                foreach (var (keyId, kind, value) in properties)
                {
                    U32(keyId);
                    if (!kind.HasValue)
                    {
                        U32(0);
                        continue;
                    }
                    U32(1);
                    U32(kind.Value);
                    if (kind.Value == StyleBool)
                    {
                        U8(value != 0.0 ? (byte)1 : (byte)0);
                    }
                    else if (kind.Value == StyleUInt32)
                    {
                        U32(unchecked((uint)(long)value));
                    }
                    else if (kind.Value == StyleFloat32)
                    {
                        F32((float)value);
                    }
                    else if (kind.Value == StyleFloat64)
                    {
                        F64(value);
                    }
                    else
                    {
                        throw new InvalidOperationException($"unsupported style property kind {kind.Value}");
                    }
                }
                return itemsStart;
            }

            private (int Slot, int GuidOffset, int ItemsOffset) WriteSkpStyle(
                string name,
                string description,
                string guid,
                long pid)
            {
                int slot = NewObject("CSkpStyle", 2);
                EntityPreamble(pid);
                int guidOffset = Buf.Count;
                Uuid(guid);
                Str(string.Empty); // source .style path
                U32(3); // style format version = 3
                Str(name);
                Str(description);
                int itemsOffset = WriteStyleProperties(DefaultStyleProperties);
                return (slot, guidOffset, itemsOffset);
            }

            internal (
                int StyleObjectTailRel,
                (int SelectedRef, int WorkingRef) StyleTailRefs,
                int SavedGuidTailRel,
                int WorkingGuidTailRel,
                int SavedItemsTailRel,
                int WorkingItemsTailRel,
                int StyleItemCount) WriteStyleManager(int tailPos)
            {
                EntityPreamble(0); // CSkpStyleManager CEntity preamble
                U32(1); // saved style count = 1

                int styleObjectTailRel = Buf.Count - tailPos;
                var (savedSlot, savedGuidOff, savedItemsOff) = WriteSkpStyle(
                    "Style",
                    string.Empty,
                    DefaultSavedStyleGuid,
                    SavedStylePid);

                int selectedRefTailRel = Buf.Count - tailPos;
                Backref(savedSlot); // selected saved style back-ref

                int workingRefTailRel = Buf.Count - tailPos;
                var (_, workingGuidOff, workingItemsOff) = WriteSkpStyle(
                    "Style",
                    string.Empty,
                    DefaultWorkingStyleGuid,
                    0);

                U8(0); // active_style_changed = False
                return (
                    styleObjectTailRel,
                    (selectedRefTailRel, workingRefTailRel),
                    savedGuidOff - tailPos,
                    workingGuidOff - tailPos,
                    savedItemsOff - tailPos,
                    workingItemsOff - tailPos,
                    DefaultStyleProperties.Length);
            }

            internal void WriteWatermarkManagerAndTrailer()
            {
                EntityPreamble(0); // CWatermarkManager CEntity preamble
                U32(0); // watermark_count = 0
                U32(0); // watermark flags / selection = 0
                U8(1); // display_watermarks = True
                U32(0); // closing model trailer = 0
            }
        }

        internal static BlankDocument BuildBlankV17()
        {
            var b = new BlankArchiveBuilder();
            int modelGuidOffset = b.WriteFileHeader();
            b.WriteVersionMap();
            var (pidCounterOffset, noScaleMaskOffset) = b.WriteModelPreamble();
            var unitOptionOffsets = b.WriteOptionsProviders();
            b.WriteModelProperties();
            var (cameraBodyOffset, cameraBodyLen) = b.WriteModelCamera();
            var (roOffset, roLen, roFields) = b.WriteRenderingOptions();
            var (matCountPos, matInsertPos, matBaseSlot) = b.WriteComponentAndMaterialManager();
            var (
                layerCountPos,
                origLayerCount,
                layerInsertPos,
                layerWriterBaseSlot,
                activeLayerRefPos,
                scaffoldClassSlot) = b.WriteLayerManager();
            var (
                defCountPos,
                origDefCount,
                rootCountPos,
                origRootCount,
                tailPos,
                scaffoldNextSlot) = b.WriteDefinitionAndRootLists();
            var (shadowTailRel, shadowLen, pageListTailRel) = b.WriteShadowInfo(tailPos);
            b.WritePageList();
            b.WriteModelAxes();
            int arialSlot = b.WriteDimensionStyle();
            int[] fontTailRefs = b.WriteTextStyleAndFontManager(tailPos, arialSlot);
            var (
                styleObjectTailRel,
                styleTailRefs,
                savedGuidTailRel,
                workingGuidTailRel,
                savedItemsTailRel,
                workingItemsTailRel,
                styleItemCount) = b.WriteStyleManager(tailPos);
            b.WriteWatermarkManagerAndTrailer();

            int[] tailRefPositions = fontTailRefs
                .Concat(new[] { styleTailRefs.SelectedRef, styleTailRefs.WorkingRef })
                .ToArray();

            return new BlankDocument(
                data: b.Buf.ToArray(),
                modelGuidOffset: modelGuidOffset,
                pidCounterOffset: pidCounterOffset,
                nextPid: InitialNextPersistentId,
                noScaleMaskOffset: noScaleMaskOffset,
                unitOptionOffsets: unitOptionOffsets,
                attrContainerSlot: b.ClassSlot["CAttributeContainer"],
                attributeNamedSlot: b.ClassSlot["CAttributeNamed"],
                ccameraSlot: b.ClassSlot["CCamera"],
                cameraBodyOffset: cameraBodyOffset,
                cameraBodyLen: cameraBodyLen,
                renderingOptionsOffset: roOffset,
                renderingOptionsLen: roLen,
                renderingOptionFields: roFields,
                materialCountPos: matCountPos,
                materialInsertPos: matInsertPos,
                materialBaseSlot: matBaseSlot,
                layerCountPos: layerCountPos,
                origLayerCount: origLayerCount,
                layerInsertPos: layerInsertPos,
                layerWriterBaseSlot: layerWriterBaseSlot,
                activeLayerRefPos: activeLayerRefPos,
                defCountPos: defCountPos,
                origDefCount: origDefCount,
                rootCountPos: rootCountPos,
                origRootCount: origRootCount,
                tailPos: tailPos,
                scaffoldNextSlot: scaffoldNextSlot,
                scaffoldClassSlot: scaffoldClassSlot,
                shadowInfoTailRel: shadowTailRel,
                shadowInfoLen: shadowLen,
                pageListTailRel: pageListTailRel,
                fontTailRefs: fontTailRefs,
                styleObjectTailRel: styleObjectTailRel,
                styleTailRefs: styleTailRefs,
                tailRefPositions: tailRefPositions,
                savedStyleGuidTailRel: savedGuidTailRel,
                workingStyleGuidTailRel: workingGuidTailRel,
                savedStyleItemsTailRel: savedItemsTailRel,
                workingStyleItemsTailRel: workingItemsTailRel,
                styleItemCount: styleItemCount);
        }
    }
}
