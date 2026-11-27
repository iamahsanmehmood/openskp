/// Construct an empty classic-format (SketchUp 2017, v17) `.skp` document in code.
///
/// Every record of the empty MFC `CArchive` document is written field by field
/// through named builder methods and named constants — no bundled binary file,
/// hex blob, or compressed template is read or embedded. As each section is
/// emitted, the builder tracks the 1-based MFC store-map slots and byte offsets
/// that `create.dart` splices into or patches when writing geometry, materials,
/// layers, component definitions, scenes, and document options.
library;

import 'dart:convert';
import 'dart:typed_data';

typedef BlankPoint3 = (double, double, double);

const String scaffoldSha256 =
    '809a1ab73a20a192ab13aaff197afb1c67d0e9352f6a353a9cd8030919f8a6c3';

// MFC CArchive UTF-16LE string prefix marker.
const List<int> _utf16StringMarker = [0xFF, 0xFE, 0xFF];

// CAttributeNamed value-type tags used in OptionsManager providers and ModelProperties.
const int _attrInt32 = 0x04;
const int _attrDouble = 0x06;
const int _attrBool = 0x07;

// CSkpStyle typed-property value tags.
const int _styleBool = 1;
const int _styleUint32 = 4;
const int _styleFloat32 = 6;
const int _styleFloat64 = 7;

// File-header defaults matching the SketchUp 2017 blank document.
const String _fileMagicText = 'SketchUp Model';
const String _versionStringV17 = '{17.0.1}';
const String _defaultModelGuid = '3093d39a-aa69-4e29-8d53-15cbfe3d94b0';
const int _defaultSaveTimestampUnix = 1786620315; // 2026-08-13T11:25:15Z (time_t)
const int _utf16LeCodepage = 1200; // Windows UTF-16LE code page (0x04B0)
const int _initialNextPersistentId = 18;

// Persistent entity IDs assigned in the empty document.
const int _savedStylePid = 1;
const int _dimStyleFontPid = 2;
const int _textStyleFontPid = 3;
const int _layer0Pid = 4;
const int _layer0MaterialPid = 5;

// Saved and working CSkpStyle GUIDs in the empty document.
const String _defaultSavedStyleGuid = '94fee66b-2dce-4b79-ada7-4be56f9266a2';
const String _defaultWorkingStyleGuid = '667aac7a-d15a-409d-9d21-24ffa8045e61';

// Default shadow / geo-location settings ("Boulder (CO)", "USA", summer solstice).
const int _defaultShadowTimeUnix = 1782048600; // 2026-06-21T13:30:00Z
const String _defaultCity = 'Boulder (CO)';
const String _defaultCountry = 'USA';
const double _defaultLongitude = -105.283;
const double _defaultLatitude = 40.017;
const double _defaultTzOffset = -7.0;
const int _defaultShadowLight = 80;
const int _defaultShadowDark = 20;

/// SketchUp 2017 `CVersionMap` schema table (alphabetical class name -> schema version).
const List<(String, int)> versionMapV17 = [
  ('CArcCurve', 3),
  ('CAttribute', 0),
  ('CAttributeContainer', 0),
  ('CAttributeNamed', 1),
  ('CBackgroundImage', 10),
  ('CCamera', 5),
  ('CComponent', 11),
  ('CComponentBehavior', 5),
  ('CComponentDefinition', 10),
  ('CComponentInstance', 5),
  ('CConstructionGeometry', 0),
  ('CConstructionLine', 1),
  ('CConstructionPoint', 0),
  ('CCurve', 4),
  ('CDefinitionList', 0),
  ('CDib', 3),
  ('CDimension', 1),
  ('CDimensionLinear', 6),
  ('CDimensionRadial', 2),
  ('CDimensionStyle', 4),
  ('CDrawingElement', 9),
  ('CEdge', 2),
  ('CEdgeUse', 1),
  ('CEntity', 5),
  ('CFace', 3),
  ('CFaceTextureCoords', 4),
  ('CFontManager', 0),
  ('CGroup', 1),
  ('CImage', 1),
  ('CLayer', 2),
  ('CLayerManager', 4),
  ('CLoop', 1),
  ('CMaterial', 12),
  ('CMaterialManager', 4),
  ('CPageList', 1),
  ('CPolyline3d', 0),
  ('CRelationship', 0),
  ('CRelationshipMap', 0),
  ('CRenderingOptions', 36),
  ('CSchemaFile', 1),
  ('CSchemaFilterFile', 0),
  ('CSchemaZipFile', 1),
  ('CSectionPlane', 2),
  ('CShadowInfo', 7),
  ('CSkFont', 1),
  ('CSketchCS', 0),
  ('CSketchUpModel', 26),
  ('CSketchUpPage', 1),
  ('CSkpStyle', 1),
  ('CSkpStyleManager', 2),
  ('CText', 9),
  ('CTextStyle', 5),
  ('CTexture', 6),
  ('CThumbnail', 1),
  ('CVertex', 0),
  ('CViewPage', 12),
  ('CWatermark', 1),
  ('CWatermarkManager', 2),
];

const String _versionMapSentinel = 'End-Of-Version-Map';

int _packAbgr(int r, int g, int b, [int a = 255]) =>
    ((r & 0xFF) | ((g & 0xFF) << 8) | ((b & 0xFF) << 16) | ((a & 0xFF) << 24)) & 0xFFFFFFFF;

Uint8List uuidToBytesLe(String canonicalGuid) {
  final hex = canonicalGuid.replaceAll('-', '');
  final raw = Uint8List(16);
  for (var i = 0; i < 16; i++) {
    raw[i] = int.parse(hex.substring(2 * i, 2 * i + 2), radix: 16);
  }
  const order = [3, 2, 1, 0, 5, 4, 7, 6, 8, 9, 10, 11, 12, 13, 14, 15];
  final out = Uint8List(16);
  for (var i = 0; i < 16; i++) {
    out[i] = raw[order[i]];
  }
  return out;
}

Uint8List _encodeUtf16Str(String text) {
  final units = text.codeUnits;
  final n = units.length;
  final headerLen = n >= 0xFFFF ? 10 : (n >= 0xFF ? 6 : 4);
  final out = Uint8List(headerLen + n * 2);
  out[0] = _utf16StringMarker[0];
  out[1] = _utf16StringMarker[1];
  out[2] = _utf16StringMarker[2];
  var pos = 3;
  if (n < 0xFF) {
    out[pos++] = n;
  } else if (n < 0xFFFF) {
    out[pos++] = 0xFF;
    out[pos++] = n & 0xFF;
    out[pos++] = (n >> 8) & 0xFF;
  } else {
    out[pos++] = 0xFF;
    out[pos++] = 0xFF;
    out[pos++] = 0xFF;
    out[pos++] = n & 0xFF;
    out[pos++] = (n >> 8) & 0xFF;
    out[pos++] = (n >> 16) & 0xFF;
    out[pos++] = (n >> 24) & 0xFF;
  }
  for (var i = 0; i < n; i++) {
    final cu = units[i];
    out[pos++] = cu & 0xFF;
    out[pos++] = (cu >> 8) & 0xFF;
  }
  return out;
}

/// Serialize the body of a `CCamera` (schema 5) object after its MFC tag.
/// With an empty `description` string this body is 176 bytes.
Uint8List packCameraBody({
  required BlankPoint3 eye,
  required BlankPoint3 target,
  required BlankPoint3 up,
  double clipNear = 1.0,
  double clipFar = 1000.0,
  bool perspective = true,
  double fov = 30.0,
  double orthoHeight = 133.0,
  BlankPoint3 windowParams = (0.0, 0.0, 0.0),
  double aspectRatio = 0.0,
  bool fovIsHeight = true,
  int cameraFlag = 0,
  String description = '',
  double imageWidth = 0.0,
  bool is2d = false,
  double scale2d = 1.0,
  (double, double) center2d = (0.0, 0.0),
}) {
  final descBytes = _encodeUtf16Str(description);
  final out = Uint8List(139 + descBytes.length + 33);
  final bd = ByteData.sublistView(out);
  var p = 0;
  void putF64(double v) {
    bd.setFloat64(p, v, Endian.little);
    p += 8;
  }

  putF64(eye.$1);
  putF64(eye.$2);
  putF64(eye.$3);
  putF64(target.$1);
  putF64(target.$2);
  putF64(target.$3);
  putF64(up.$1);
  putF64(up.$2);
  putF64(up.$3);
  putF64(clipNear);
  putF64(clipFar);
  out[p++] = perspective ? 1 : 0;
  putF64(fov);
  putF64(orthoHeight);
  putF64(windowParams.$1);
  putF64(windowParams.$2);
  putF64(windowParams.$3);
  putF64(aspectRatio);
  out[p++] = fovIsHeight ? 1 : 0;
  out[p++] = cameraFlag & 0xFF;
  out.setRange(p, p + descBytes.length, descBytes);
  p += descBytes.length;
  putF64(imageWidth);
  out[p++] = is2d ? 1 : 0;
  putF64(scale2d);
  putF64(center2d.$1);
  putF64(center2d.$2);
  return out;
}

// Default 56-entry CSkpStyle property table: (keyId, valueKind, value).
final List<(int, int?, double)> _defaultStyleProperties = [
  (1000, _styleUint32, 1),                                      // EdgeDisplayMode: show edges
  (1001, _styleUint32, 0),                                      // EdgeType: standard strokes
  (1002, _styleUint32, 1),                                      // EdgeColorMode: all same
  (1004, _styleBool, 0),                                        // ExtendLines: false
  (1005, _styleUint32, 8),                                      // LineExtension: 8 px
  (1006, _styleBool, 1),                                        // DrawSilhouettes (Profiles): true
  (1007, _styleUint32, 3),                                      // SilhouetteWidth: 3 px
  (1008, _styleBool, 0),                                        // DrawDepthQue: false
  (1009, _styleUint32, 4),                                      // DepthQueWidth: 4 px
  (1010, _styleBool, 0),                                        // DrawLineEnds (Endpoints): false
  (1011, _styleUint32, 5),                                      // LineEndWidth: 5 px
  (1012, _styleBool, 0),                                        // JitterEdges: false
  (1014, _styleUint32, _packAbgr(0, 0, 0, 255).toDouble()),     // ForegroundColor: black
  (1015, _styleBool, 0),                                        // DrawBackEdges: false
  (1016, _styleBool, 1),                                        // Edge halo / stroke flag: true
  (2001, _styleUint32, 2),                                      // RenderMode: ShadedWithTextures
  (2002, _styleUint32, _packAbgr(225, 225, 200, 255).toDouble()), // FaceFrontColor
  (2003, _styleUint32, _packAbgr(128, 128, 200, 255).toDouble()), // FaceBackColor
  (2004, _styleBool, 0),                                        // ModelTransparency (X-ray): false
  (2005, _styleBool, 1),                                        // MaterialTransparency: true
  (2006, _styleUint32, 0),                                      // TransparencySort: faster (0)
  (2007, _styleBool, 1),                                        // Texture: true
  (2008, _styleFloat64, 0.65),                                  // Transparency factor: 0.65
  (4000, _styleUint32, _packAbgr(255, 255, 255, 255).toDouble()), // BackgroundColor: white
  (4001, _styleUint32, _packAbgr(140, 165, 255, 255).toDouble()), // SkyColor
  (4002, _styleBool, 0),                                        // DrawHorizon (Sky): false
  (4003, _styleUint32, _packAbgr(63, 147, 63, 255).toDouble()), // GroundColor
  (4004, _styleBool, 0),                                        // DrawGround: false
  (4005, _styleUint32, 50),                                     // GroundTransparency: 50
  (4006, _styleBool, 1),                                        // DrawUnderground: true
  (4007, _styleUint32, _packAbgr(0, 0, 0, 0).toDouble()),       // HorizonColor: transparent black
  (5000, _styleBool, 1),                                        // DisplayWatermarks: true
  (5001, null, 0),                                              // Watermarks list: empty (count = 0)
  (7000, _styleUint32, _packAbgr(255, 255, 0, 255).toDouble()), // HighlightColor: yellow
  (7001, _styleUint32, _packAbgr(255, 0, 0, 255).toDouble()),   // LockedColor: red
  (7002, _styleUint32, _packAbgr(128, 128, 128, 255).toDouble()), // ConstructionColor: grey
  (7003, _styleUint32, _packAbgr(102, 110, 141, 255).toDouble()), // SectionActiveColor
  (7004, _styleUint32, _packAbgr(192, 192, 192, 255).toDouble()), // SectionInactiveColor
  (7005, _styleUint32, _packAbgr(255, 0, 0, 255).toDouble()),   // SectionDefaultCutColor
  (7008, _styleBool, 1),                                        // DisplaySketchAxes: true
  (7010, _styleBool, 0),                                        // DrawHidden: false
  (7011, _styleBool, 0),                                        // DisplayColorByLayer: false
  (7012, _styleBool, 0),                                        // HideConstructionGeometry: false
  (7013, _styleUint32, 3),                                      // SectionDisplayMode: planes (1) | cuts (2)
  (7014, _styleUint32, 4),                                      // SectionCutWidth: 4
  (7015, _styleBool, 0),                                        // SectionCutFilled: false
  (7016, _styleUint32, _packAbgr(0, 0, 0, 255).toDouble()),     // SectionDefaultFillColor: black
  (7017, _styleBool, 0),                                        // DrawHiddenGeometry: false
  (7018, _styleBool, 0),                                        // DrawHiddenObjects: false
  (8000, _styleBool, 1),                                        // Match Photo background image: true
  (8001, _styleFloat64, 1.0),                                   // Match Photo background opacity: 1.0
  (8002, _styleBool, 1),                                        // Match Photo foreground photo: true
  (8003, _styleFloat64, 0.8),                                   // Match Photo foreground opacity: 0.8
  (8100, _styleBool, 0),                                        // AmbientOcclusion: false
  (8102, _styleFloat32, 10.0),                                  // AmbientOcclusionDistance: 10.0
  (8103, _styleFloat32, 0.0),                                   // AmbientOcclusionIntensity: 0.0
];

/// Serialized empty v17 `.skp` document together with every named splice
/// position, field offset, length, and MFC store-map slot `create.dart` needs.
class BlankDocument {
  final Uint8List data;
  final int modelGuidOffset;
  final int pidCounterOffset;
  final int nextPid;
  final int noScaleMaskOffset;
  final Map<String, int> unitOptionOffsets;
  final int attrContainerSlot;
  final int attributeNamedSlot;
  final int ccameraSlot;
  final int cameraBodyOffset;
  final int cameraBodyLen;
  final int renderingOptionsOffset;
  final int renderingOptionsLen;
  final List<(int, int, bool)> renderingOptionFields;
  final int materialCountPos;
  final int materialInsertPos;
  final int materialBaseSlot;
  final int layerCountPos;
  final int origLayerCount;
  final int layerInsertPos;
  final int layerWriterBaseSlot;
  final int activeLayerRefPos;
  final int defCountPos;
  final int origDefCount;
  final int rootCountPos;
  final int origRootCount;
  final int tailPos;
  final int scaffoldNextSlot;
  final Map<String, int> scaffoldClassSlot;
  final int shadowInfoTailRel;
  final int shadowInfoLen;
  final int pageListTailRel;
  final List<int> fontTailRefs;
  final int styleObjectTailRel;
  final (int, int) styleTailRefs;
  final List<int> tailRefPositions;
  final int savedStyleGuidTailRel;
  final int workingStyleGuidTailRel;
  final int savedStyleItemsTailRel;
  final int workingStyleItemsTailRel;
  final int styleItemCount;

  const BlankDocument({
    required this.data,
    required this.modelGuidOffset,
    required this.pidCounterOffset,
    required this.nextPid,
    required this.noScaleMaskOffset,
    required this.unitOptionOffsets,
    required this.attrContainerSlot,
    required this.attributeNamedSlot,
    required this.ccameraSlot,
    required this.cameraBodyOffset,
    required this.cameraBodyLen,
    required this.renderingOptionsOffset,
    required this.renderingOptionsLen,
    required this.renderingOptionFields,
    required this.materialCountPos,
    required this.materialInsertPos,
    required this.materialBaseSlot,
    required this.layerCountPos,
    required this.origLayerCount,
    required this.layerInsertPos,
    required this.layerWriterBaseSlot,
    required this.activeLayerRefPos,
    required this.defCountPos,
    required this.origDefCount,
    required this.rootCountPos,
    required this.origRootCount,
    required this.tailPos,
    required this.scaffoldNextSlot,
    required this.scaffoldClassSlot,
    required this.shadowInfoTailRel,
    required this.shadowInfoLen,
    required this.pageListTailRel,
    required this.fontTailRefs,
    required this.styleObjectTailRel,
    required this.styleTailRefs,
    required this.tailRefPositions,
    required this.savedStyleGuidTailRel,
    required this.workingStyleGuidTailRel,
    required this.savedStyleItemsTailRel,
    required this.workingStyleItemsTailRel,
    required this.styleItemCount,
  });
}

class _BlankArchiveBuilder {
  final BytesBuilder _buf = BytesBuilder(copy: false);
  final ByteData _scratch = ByteData(8);
  int nextSlot = 1;
  final Map<String, int> classSlot = {};

  int get length => _buf.length;

  Uint8List takeBytes() => _buf.toBytes();

  void _u8(int value) {
    _buf.addByte(value & 0xFF);
  }

  void _u16(int value) {
    _buf.addByte(value & 0xFF);
    _buf.addByte((value >> 8) & 0xFF);
  }

  void _u32(int value) {
    _buf.addByte(value & 0xFF);
    _buf.addByte((value >> 8) & 0xFF);
    _buf.addByte((value >> 16) & 0xFF);
    _buf.addByte((value >> 24) & 0xFF);
  }

  void _i32(int value) {
    _u32(value & 0xFFFFFFFF);
  }

  void _u64(int value) {
    _u32(value & 0xFFFFFFFF);
    _u32((value ~/ 0x100000000) & 0xFFFFFFFF);
  }

  void _f32(double value) {
    _scratch.setFloat32(0, value, Endian.little);
    _buf.add(Uint8List.fromList([
      _scratch.getUint8(0),
      _scratch.getUint8(1),
      _scratch.getUint8(2),
      _scratch.getUint8(3),
    ]));
  }

  void _f64(double value) {
    _scratch.setFloat64(0, value, Endian.little);
    _buf.add(Uint8List.fromList([
      _scratch.getUint8(0),
      _scratch.getUint8(1),
      _scratch.getUint8(2),
      _scratch.getUint8(3),
      _scratch.getUint8(4),
      _scratch.getUint8(5),
      _scratch.getUint8(6),
      _scratch.getUint8(7),
    ]));
  }

  void _rgba(int r, int g, int b, [int a = 255]) {
    _buf.add(Uint8List.fromList([r & 0xFF, g & 0xFF, b & 0xFF, a & 0xFF]));
  }

  void _uuid(String canonicalGuid) {
    _buf.add(uuidToBytesLe(canonicalGuid));
  }

  void _str(String text) {
    _buf.add(_encodeUtf16Str(text));
  }

  void _zeros(int count) {
    _buf.add(Uint8List(count));
  }

  int _allocSlot() {
    final slot = nextSlot;
    nextSlot += 1;
    return slot;
  }

  void _nullRef() => _u16(0);

  void _backref(int slot) => _u16(slot);

  int _newObject(String className, int schema) {
    final existing = classSlot[className];
    if (existing == null) {
      _u16(0xFFFF);
      _u16(schema);
      final nameBytes = ascii.encode(className);
      _u16(nameBytes.length);
      _buf.add(nameBytes);
      classSlot[className] = _allocSlot();
      return _allocSlot();
    }
    _u16(0x8000 | existing);
    return _allocSlot();
  }

  void _encodePid(int pid) {
    var mask = 0;
    final payload = <int>[];
    var rem = pid;
    for (var bit = 0; bit < 8; bit++) {
      final byteVal = rem & 0xFF;
      rem = rem ~/ 256;
      if (byteVal != 0) {
        mask |= 1 << bit;
        payload.add(byteVal);
      }
    }
    _u8(mask);
    if (payload.isNotEmpty) {
      _buf.add(Uint8List.fromList(payload));
    }
  }

  void _entityPreamble([int pid = 0]) {
    _nullRef();
    _encodePid(pid);
  }

  int _writeAttrBool(String key, bool value) {
    _str(key);
    _u8(_attrBool);
    final valOffset = length;
    _u8(value ? 1 : 0);
    return valOffset;
  }

  int _writeAttrInt32(String key, int value) {
    _str(key);
    _u8(_attrInt32);
    final valOffset = length;
    _i32(value);
    return valOffset;
  }

  int _writeAttrDouble(String key, double value) {
    _str(key);
    _u8(_attrDouble);
    final valOffset = length;
    _f64(value);
    return valOffset;
  }

  // ── document sections ────────────────────────────────────────────────────

  int writeFileHeader() {
    _str(_fileMagicText);
    _str(_versionStringV17);
    final modelGuidOffset = length;
    _uuid(_defaultModelGuid);
    _str(''); // empty release/locale note string
    _u32(_defaultSaveTimestampUnix);
    return modelGuidOffset;
  }

  void writeVersionMap() {
    _newObject('CVersionMap', 0);
    for (final (clsName, schemaVer) in versionMapV17) {
      _str(clsName);
      _u32(schemaVer);
    }
    _str(_versionMapSentinel);
  }

  (int, int) writeModelPreamble() {
    _u32(0); // model header word 0
    _u32(1); // model header format flag (1 in v17+)
    _u32(_utf16LeCodepage);
    _u32(0); // preview thumbnail status (0 = none)
    final pidCounterOffset = length;
    _u64(_initialNextPersistentId);
    _nullRef(); // null model preview CDib pointer
    _u32(1); // post-thumbnail model flag
    _zeros(7); // reserved pre-behavior model header bytes (all 0)
    final noScaleMaskOffset = length;
    _u32(0); // root CComponentBehavior.no_scale_mask = 0
    _str(''); // model description (SUModelGetDescription)
    _u32(3); // options-manager format version
    return (pidCounterOffset, noScaleMaskOffset);
  }

  Map<String, int> writeOptionsProviders() {
    _u32(4); // four named SUOptionsProvider tables follow

    // 1. NamedOptions (empty)
    _str('NamedOptions');
    _str('');

    // 2. PageOptions
    _str('PageOptions');
    _writeAttrBool('ShowTransition', true);
    _writeAttrDouble('TransitionTime', 1.5);
    _str('');

    // 3. SlideshowOptions
    _str('SlideshowOptions');
    _writeAttrBool('LoopSlideshow', true);
    _writeAttrDouble('SlideTime', 1.0);
    _str('');

    // 4. UnitsOptions
    _str('UnitsOptions');
    final unitOffsets = <String, int>{};
    unitOffsets['LengthPrecision'] = _writeAttrInt32('LengthPrecision', 4);
    unitOffsets['LengthFormat'] = _writeAttrInt32('LengthFormat', 1); // 1 = Architectural
    unitOffsets['LengthUnit'] = _writeAttrInt32('LengthUnit', 0); // 0 = Inches
    _writeAttrBool('LengthSnapEnabled', true);
    _writeAttrDouble('LengthSnapLength', 0.0625); // 1/16 inch
    _writeAttrInt32('AnglePrecision', 1);
    _writeAttrBool('AngleSnapEnabled', true);
    _writeAttrDouble('SnapAngle', 15.0);
    _writeAttrBool('SuppressUnitsDisplay', false);
    _writeAttrBool('ForceInchDisplay', false);
    unitOffsets['AreaUnit'] = _writeAttrInt32('AreaUnit', 0);
    unitOffsets['VolumeUnit'] = _writeAttrInt32('VolumeUnit', 0);
    _writeAttrInt32('AreaPrecision', 2);
    _writeAttrInt32('VolumePrecision', 2);
    _str('');
    return unitOffsets;
  }

  void writeModelProperties() {
    // CAttributeContainer holding one CAttributeNamed("ModelProperties")
    _newObject('CAttributeContainer', 0);
    _entityPreamble(0);

    _newObject('CAttributeNamed', 1);
    _entityPreamble(0);
    _u32(0); // CAttribute flags
    _str('ModelProperties');
    _writeAttrBool('IsClassified', false);
    _writeAttrBool('IsDynamic', false);
    _writeAttrBool('IsLive', false);
    _str(''); // end of CAttributeNamed entries

    _nullRef(); // end of CAttributeContainer children
    _zeros(6); // three null u16 fields before model CCamera
  }

  (int, int) writeModelCamera() {
    _newObject('CCamera', 5);
    final bodyOffset = length;
    final body = packCameraBody(
      eye: (0.0, 0.0, 500.0),
      target: (0.0, 0.0, 0.0),
      up: (0.0, 1.0, 0.0),
      clipNear: 1.0,
      clipFar: 1000.0,
      perspective: true,
      fov: 30.0,
      orthoHeight: 133.0,
    );
    _buf.add(body);
    return (bodyOffset, body.length);
  }

  (int, int, List<(int, int, bool)>) writeRenderingOptions() {
    _entityPreamble(0); // CRenderingOptions CEntity preamble
    final roStart = length;
    final fields = <(int, int, bool)>[];

    void roU8(int val, [int? styleItem]) {
      if (styleItem != null) fields.add((styleItem, length, true));
      _u8(val);
    }

    void roU32(int val, [int? styleItem]) {
      if (styleItem != null) fields.add((styleItem, length, false));
      _u32(val);
    }

    void roRgba(int r, int g, int b, int a, [int? styleItem]) {
      if (styleItem != null) fields.add((styleItem, length, false));
      _rgba(r, g, b, a);
    }

    roU32(2, 2001);                         // RenderMode: ShadedWithTextures
    roU8(0, 2004);                          // ModelTransparency (X-ray)
    roU8(1, 2005);                          // MaterialTransparency
    roU8(0, 1012);                          // JitterEdges
    roU32(1, 1000);                         // EdgeDisplayMode: show edges
    roRgba(0, 0, 0, 0, 4000);               // BackgroundColor slot (unset until style sync)
    roRgba(0, 0, 0, 0, 1014);               // ForegroundColor slot (unset until style sync)
    roRgba(255, 255, 0, 255, 7000);         // HighlightColor
    roRgba(128, 128, 128, 255, 7002);       // ConstructionColor
    roU8(0);                                // DisplayShadows
    roU8(0);                                // DisplayInstanceAxes
    roU8(0, 7011);                          // DisplayColorByLayer (Color by Tag)
    roU8(1, 2007);                          // Texture
    roU32(1, 1002);                         // EdgeColorMode: all same
    roU8(0, 1004);                          // ExtendLines
    roU32(8, 1005);                         // LineExtension: 8 px
    roU8(1, 1006);                          // DrawSilhouettes (Profiles)
    roU32(3, 1007);                         // SilhouetteWidth: 3 px
    roU8(0, 1008);                          // DrawDepthQue
    roU32(4, 1009);                         // DepthQueWidth: 4 px
    roU8(0, 1010);                          // DrawLineEnds (Endpoints)
    roU32(5, 1011);                         // LineEndWidth: 5 px
    roU8(0);                                // DrawProfilesOnly
    roU8(0, 7010);                          // DrawHidden
    _u32(0);                                // reserved rendering-options word (0)
    roRgba(225, 225, 200, 255, 2002);       // FaceFrontColor
    roRgba(128, 128, 200, 255, 2003);       // FaceBackColor
    _f64(0.75);                             // InactiveFade
    _f64(0.5);                              // InstanceFade
    roU8(0);                                // InactiveHidden
    roU8(0);                                // InstanceHidden
    roU8(0);                                // DisplayFog
    roRgba(0, 0, 0, 0);                     // FogColor
    roU8(1);                                // FogUseBkColor
    _f64(-1.0);                             // FogStartDist (-1 = automatic)
    _f64(-1.0);                             // FogEndDist (-1 = automatic)
    _u32(1);                                // fog / stroke mode constant (1)
    roU32(0, 1001);                         // EdgeType: standard strokes
    roU8(1, 7008);                          // DisplaySketchAxes
    roU8(1);                                // DisplayText
    roU8(1);                                // DisplayDims
    roU8(0, 7012);                          // HideConstructionGeometry
    roRgba(140, 165, 255, 255, 4001);       // SkyColor
    roRgba(0, 0, 0, 0, 4007);               // HorizonColor
    roRgba(63, 147, 63, 255, 4003);         // GroundColor
    roU8(0, 4002);                          // DrawHorizon
    roU8(0, 4004);                          // DrawGround
    roU8(1, 4006);                          // DrawUnderground
    roU32(50, 4005);                        // GroundTransparency: 50
    roRgba(102, 110, 141, 255, 7003);       // SectionActiveColor
    roRgba(192, 192, 192, 255, 7004);       // SectionInactiveColor
    roRgba(255, 0, 0, 255, 7005);           // SectionDefaultCutColor
    roU32(4, 7014);                         // SectionCutWidth: 4
    roU32(3);                               // SectionDisplayMode: planes (1) | cuts (2)
    roU32(0, 2006);                         // TransparencySort: faster (0)
    roU8(1);                                // edge halo flag (style item 1016)
    _f64(0.34906585039886595);              // 20 deg in radians (crease / halo angle)
    roU8(1);                                // ShowViewName: true
    roRgba(255, 0, 0, 255, 7001);           // LockedColor
    roU8(1, 5000);                          // DisplayWatermarks
    _f64(0.65);                             // transparency factor (style item 2008)
    roU8(0, 1015);                          // DrawBackEdges
    roU8(1);                                // Match Photo background image (item 8000)
    _f64(1.0);                              // Match Photo background opacity (item 8001)
    roU8(1);                                // Match Photo foreground photo (item 8002)
    _f64(0.8);                              // Match Photo foreground opacity (item 8003)

    final roLen = length - roStart;
    fields.sort((a, b) => a.$1.compareTo(b.$1));
    return (roStart, roLen, fields);
  }

  (int, int, int) writeComponentAndMaterialManager() {
    _u32(0); // post-rendering-options word 0
    _u32(1); // post-rendering-options word 1
    _entityPreamble(0); // CSketchUpModel CEntity preamble (null model attributes)
    _zeros(3); // CComponent base header pad (offsets 0..2)
    _u8(1); // CComponent base flag 0
    _u8(1); // CComponent base flag 1
    _entityPreamble(0); // CMaterialManager CEntity preamble
    _zeros(5); // CMaterialManager header pad
    final materialCountPos = length;
    _u32(0); // material_count = 0
    final materialInsertPos = length;
    final materialBaseSlot = nextSlot;
    return (materialCountPos, materialInsertPos, materialBaseSlot);
  }

  (int, int, int, int, int, Map<String, int>) writeLayerManager() {
    _nullRef(); // active material ref (null = default unpainted)
    _entityPreamble(0); // CLayerManager CEntity preamble
    final layerCountPos = length;
    const origLayerCount = 1;
    _u32(origLayerCount);

    // Default untagged layer "Layer0" (schema 3, layer pid=4, material pid=5)
    final layer0Slot = _newObject('CLayer', 3);
    final middleClassSlot = <String, int>{'CLayer': classSlot['CLayer']!};
    _entityPreamble(_layer0Pid);
    _str('Layer0');
    _u8(0); // hidden = False
    _u16(0); // layer flags = 0
    _encodePid(_layer0MaterialPid);
    _str('Layer_Layer0');
    _u16(0x0100); // embedded layer-colour material flag (256)
    _rgba(255, 84, 84, 255); // default Layer0 tag colour
    _str(''); // empty texture path
    _u32(0); // colorize mode = 0
    _u32(0); // colorize flags = 0
    _f64(0.5); // default layer material alpha/transparency
    _u8(0); // use_opacity = False
    _u32(0); // layer trailer word = 0

    final layerInsertPos = length;
    final layerWriterBaseSlot = nextSlot;
    final activeLayerRefPos = length;
    _backref(layer0Slot); // active layer back-ref -> Layer0
    return (
      layerCountPos,
      origLayerCount,
      layerInsertPos,
      layerWriterBaseSlot,
      activeLayerRefPos,
      middleClassSlot,
    );
  }

  (int, int, int, int, int, int) writeDefinitionAndRootLists() {
    final defCountPos = length;
    const origDefCount = 0;
    _u32(origDefCount);

    final rootCountPos = length;
    const origRootCount = 0;
    _u32(origRootCount);

    final tailPos = length;
    final scaffoldNextSlot = nextSlot;
    return (defCountPos, origDefCount, rootCountPos, origRootCount, tailPos, scaffoldNextSlot);
  }

  (int, int, int) writeShadowInfo(int tailPos) {
    _u32(0); // root CRelationship list count = 0
    _u16(0); // root component closing word = 0
    _entityPreamble(0); // CShadowInfo CEntity preamble

    final shadowStart = length;
    _u32(_defaultShadowTimeUnix);
    _u8(0); // DaylightSavings = False
    _str(_defaultCity);
    _str(_defaultCountry);
    _f64(_defaultLongitude);
    _f64(_defaultLatitude);
    _f64(_defaultTzOffset);
    _f64(0.0); // NorthAngle (degrees / sin = 0.0)
    _f64(1.0); // North direction Y = cos(0) = 1.0
    _f64(0.0); // North direction Z = 0.0
    _u8(0); // DisplayNorth = False
    _u8(1); // DisplayOnAllFaces = True
    _u8(1); // DisplayOnGroundPlane = True
    _u8(0); // EdgesCastShadows = False
    _u32(_defaultShadowLight);
    _u32(_defaultShadowDark);
    final shadowLen = length - shadowStart;

    final pageListTailRel = length - tailPos;
    _u8(0); // UseSunForAllShading = False
    return (shadowStart - tailPos, shadowLen, pageListTailRel);
  }

  void writePageList() {
    _entityPreamble(0); // CPageList CEntity preamble
    _u32(0); // page_count = 0
    _nullRef(); // selected_page_ref = null
  }

  void writeModelAxes() {
    _zeros(3); // page-list closing bytes (0)
    _entityPreamble(0); // CSketchCS CEntity preamble
    _u8(1); // axis flag 0
    _u8(1); // axis flag 1
    _zeros(5); // axis header pad
    _f64(0.0); // origin.x
    _f64(0.0); // origin.y
    _f64(0.0); // origin.z
    _f64(1.0); // x_axis.x
    _f64(0.0); // x_axis.y
    _f64(0.0); // x_axis.z
    _f64(0.0); // y_axis.x
    _f64(1.0); // y_axis.y
    _f64(0.0); // y_axis.z
    _f64(0.0); // z_axis.x
    _f64(0.0); // z_axis.y
    _f64(1.0); // z_axis.z
    _zeros(68); // empty post-axes model tables before CDimensionStyle
  }

  int _writeSkFont(String faceName, int pointSize, int pid) {
    final slot = _newObject('CSkFont', 1);
    _entityPreamble(pid);
    _str(faceName);
    _u16(0); // bold/italic flags = 0
    _u32(pointSize);
    _u8(0); // use_world_size = False
    _f64(1.0); // world_size = 1.0 inch
    return slot;
  }

  int writeDimensionStyle() {
    _entityPreamble(0); // CDimensionStyle CEntity preamble
    final arialSlot = _writeSkFont('Arial', 12, _dimStyleFontPid);
    _u8(0); // has_3d = False
    _u8(1); // dimension text visibility / placement flag
    _u32(5); // extension_line_offset = 5
    _u32(10); // extension_line_overshoot = 10
    _u32(1); // line_weight = 1
    _u32(3); // arrow_type = 3 (closed arrow)
    _u32(10); // arrow_size = 10
    _u8(0); // highlight_non_associative = False
    _rgba(255, 0, 0, 255); // highlight_non_associative_color = red
    _u8(1); // show_radial_prefix = True
    _u8(0); // hide_out_of_plane = False
    _f64(0.6); // hide_out_of_plane_value = 0.6
    _u8(0); // hide_small = False
    _f64(10.0); // hide_small_value = 10.0
    _rgba(64, 64, 64, 255); // dimension colour = dark grey
    _u8(0); // use_text_color = False
    _zeros(3); // text alignment / flags = 0
    _u8(1); // show_units = True
    _zeros(3); // dimension-style trailer pad
    return arialSlot;
  }

  List<int> writeTextStyleAndFontManager(int tailPos, int arialSlot) {
    final fontRefs = <int>[];
    _entityPreamble(0); // CTextStyle CEntity preamble

    // Inline CSkFont("Tahoma"): its short class-ref (0x8000|11) shifts when slots grow.
    fontRefs.add(length - tailPos);
    final tahomaSlot = _writeSkFont('Tahoma', 12, _textStyleFontPid);
    _u32(2); // leader_type = 2 (pushpin)
    _u32(1); // display_leader = 1
    _u8(0); // text-style flag = 0
    _u32(3); // arrow_type = 3 (closed arrow)
    _u8(1); // leader visibility flag = 1
    _rgba(0, 0, 0, 255); // text colour = black
    _rgba(0, 0, 0, 255); // leader colour = black
    fontRefs.add(length - tailPos);
    _backref(tahomaSlot);

    // CFontManager: [preamble][count = 2][Arial ref][Tahoma ref][closing u16 = 0]
    _entityPreamble(0);
    _u32(2);
    fontRefs.add(length - tailPos);
    _backref(arialSlot);
    fontRefs.add(length - tailPos);
    _backref(tahomaSlot);
    _u16(0);
    return fontRefs;
  }

  int _writeStyleProperties(List<(int, int?, double)> properties) {
    _u32(properties.length);
    final itemsStart = length;
    for (final (keyId, kind, value) in properties) {
      _u32(keyId);
      if (kind == null) {
        _u32(0);
        continue;
      }
      _u32(1);
      _u32(kind);
      if (kind == _styleBool) {
        _u8(value != 0.0 ? 1 : 0);
      } else if (kind == _styleUint32) {
        _u32(value.truncate() & 0xFFFFFFFF);
      } else if (kind == _styleFloat32) {
        _f32(value);
      } else if (kind == _styleFloat64) {
        _f64(value);
      } else {
        throw StateError('unsupported style property kind $kind');
      }
    }
    return itemsStart;
  }

  (int, int, int) _writeSkpStyle({
    required String name,
    required String description,
    required String guid,
    required int pid,
  }) {
    final slot = _newObject('CSkpStyle', 2);
    _entityPreamble(pid);
    final guidOffset = length;
    _uuid(guid);
    _str(''); // source .style path
    _u32(3); // style format version = 3
    _str(name);
    _str(description);
    final itemsOffset = _writeStyleProperties(_defaultStyleProperties);
    return (slot, guidOffset, itemsOffset);
  }

  (int, (int, int), int, int, int, int, int) writeStyleManager(int tailPos) {
    _entityPreamble(0); // CSkpStyleManager CEntity preamble
    _u32(1); // saved style count = 1

    final styleObjectTailRel = length - tailPos;
    final (savedSlot, savedGuidOff, savedItemsOff) = _writeSkpStyle(
      name: 'Style',
      description: '',
      guid: _defaultSavedStyleGuid,
      pid: _savedStylePid,
    );

    final selectedRefTailRel = length - tailPos;
    _backref(savedSlot); // selected saved style back-ref

    final workingRefTailRel = length - tailPos;
    final (_, workingGuidOff, workingItemsOff) = _writeSkpStyle(
      name: 'Style',
      description: '',
      guid: _defaultWorkingStyleGuid,
      pid: 0,
    );

    _u8(0); // active_style_changed = False
    return (
      styleObjectTailRel,
      (selectedRefTailRel, workingRefTailRel),
      savedGuidOff - tailPos,
      workingGuidOff - tailPos,
      savedItemsOff - tailPos,
      workingItemsOff - tailPos,
      _defaultStyleProperties.length,
    );
  }

  void writeWatermarkManagerAndTrailer() {
    _entityPreamble(0); // CWatermarkManager CEntity preamble
    _u32(0); // watermark_count = 0
    _u32(0); // watermark flags / selection = 0
    _u8(1); // display_watermarks = True
    _u32(0); // closing model trailer = 0
  }
}

/// Build the empty classic SketchUp 2017 (v17) document and return its
/// serialized bytes alongside every splice offset and slot index.
BlankDocument buildBlankV17() {
  final b = _BlankArchiveBuilder();
  final modelGuidOffset = b.writeFileHeader();
  b.writeVersionMap();
  final (pidCounterOffset, noScaleMaskOffset) = b.writeModelPreamble();
  final unitOptionOffsets = b.writeOptionsProviders();
  b.writeModelProperties();
  final (cameraBodyOffset, cameraBodyLen) = b.writeModelCamera();
  final (roOffset, roLen, roFields) = b.writeRenderingOptions();
  final (matCountPos, matInsertPos, matBaseSlot) = b.writeComponentAndMaterialManager();
  final (
    layerCountPos,
    origLayerCount,
    layerInsertPos,
    layerWriterBaseSlot,
    activeLayerRefPos,
    scaffoldClassSlot,
  ) = b.writeLayerManager();
  final (
    defCountPos,
    origDefCount,
    rootCountPos,
    origRootCount,
    tailPos,
    scaffoldNextSlot,
  ) = b.writeDefinitionAndRootLists();
  final (shadowTailRel, shadowLen, pageListTailRel) = b.writeShadowInfo(tailPos);
  b.writePageList();
  b.writeModelAxes();
  final arialSlot = b.writeDimensionStyle();
  final fontTailRefs = b.writeTextStyleAndFontManager(tailPos, arialSlot);
  final (
    styleObjectTailRel,
    styleTailRefs,
    savedGuidTailRel,
    workingGuidTailRel,
    savedItemsTailRel,
    workingItemsTailRel,
    styleItemCount,
  ) = b.writeStyleManager(tailPos);
  b.writeWatermarkManagerAndTrailer();

  return BlankDocument(
    data: b.takeBytes(),
    modelGuidOffset: modelGuidOffset,
    pidCounterOffset: pidCounterOffset,
    nextPid: _initialNextPersistentId,
    noScaleMaskOffset: noScaleMaskOffset,
    unitOptionOffsets: Map.unmodifiable(unitOptionOffsets),
    attrContainerSlot: b.classSlot['CAttributeContainer']!,
    attributeNamedSlot: b.classSlot['CAttributeNamed']!,
    ccameraSlot: b.classSlot['CCamera']!,
    cameraBodyOffset: cameraBodyOffset,
    cameraBodyLen: cameraBodyLen,
    renderingOptionsOffset: roOffset,
    renderingOptionsLen: roLen,
    renderingOptionFields: List.unmodifiable(roFields),
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
    scaffoldClassSlot: Map.unmodifiable(scaffoldClassSlot),
    shadowInfoTailRel: shadowTailRel,
    shadowInfoLen: shadowLen,
    pageListTailRel: pageListTailRel,
    fontTailRefs: List.unmodifiable(fontTailRefs),
    styleObjectTailRel: styleObjectTailRel,
    styleTailRefs: styleTailRefs,
    tailRefPositions: List.unmodifiable([
      ...fontTailRefs,
      styleTailRefs.$1,
      styleTailRefs.$2,
    ]),
    savedStyleGuidTailRel: savedGuidTailRel,
    workingStyleGuidTailRel: workingGuidTailRel,
    savedStyleItemsTailRel: savedItemsTailRel,
    workingStyleItemsTailRel: workingItemsTailRel,
    styleItemCount: styleItemCount,
  );
}
