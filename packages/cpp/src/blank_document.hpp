#pragma once

// Construct an empty classic-format (SketchUp 2017, v17) `.skp` document in code.
//
// Every record of the empty MFC `CArchive` document is written field by field through named
// builder methods and named constants — no bundled binary file, hex blob, or compressed template
// is read or embedded. As each section is emitted, the builder tracks the 1-based MFC store-map
// slots and byte offsets that `create.cpp` splices into or patches when writing geometry,
// materials, layers, component definitions, scenes, and document options.

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include <openskp/create.hpp>

namespace openskp {
namespace detail {

inline constexpr const char* kScaffoldSha256 =
    "809a1ab73a20a192ab13aaff197afb1c67d0e9352f6a353a9cd8030919f8a6c3";

// MFC CArchive UTF-16LE string prefix marker.
inline constexpr std::array<std::uint8_t, 3> kUtf16StringMarker = {0xFF, 0xFE, 0xFF};

// CAttributeNamed value-type tags used in OptionsManager providers and ModelProperties.
inline constexpr std::uint8_t kBlankAttrInt32 = 0x04;
inline constexpr std::uint8_t kBlankAttrDouble = 0x06;
inline constexpr std::uint8_t kBlankAttrBool = 0x07;

// CSkpStyle typed-property value tags.
inline constexpr std::uint32_t kStyleBool = 1;
inline constexpr std::uint32_t kStyleUint32 = 4;
inline constexpr std::uint32_t kStyleFloat32 = 6;
inline constexpr std::uint32_t kStyleFloat64 = 7;

// File-header defaults matching the SketchUp 2017 blank document.
inline constexpr const char* kFileMagicText = "SketchUp Model";
inline constexpr const char* kVersionStringV17 = "{17.0.1}";
inline constexpr const char* kDefaultModelGuid = "3093d39a-aa69-4e29-8d53-15cbfe3d94b0";
inline constexpr std::uint32_t kDefaultSaveTimestampUnix = 1786620315u;  // 2026-08-13T11:25:15Z
inline constexpr std::uint32_t kUtf16LeCodepage = 1200u;  // Windows UTF-16LE code page (0x04B0)
inline constexpr std::uint64_t kInitialNextPersistentId = 18u;

// Persistent entity IDs assigned in the empty document.
inline constexpr std::uint64_t kSavedStylePid = 1u;
inline constexpr std::uint64_t kDimStyleFontPid = 2u;
inline constexpr std::uint64_t kTextStyleFontPid = 3u;
inline constexpr std::uint64_t kLayer0Pid = 4u;
inline constexpr std::uint64_t kLayer0MaterialPid = 5u;

// Saved and working CSkpStyle GUIDs in the empty document.
inline constexpr const char* kDefaultSavedStyleGuid = "94fee66b-2dce-4b79-ada7-4be56f9266a2";
inline constexpr const char* kDefaultWorkingStyleGuid = "667aac7a-d15a-409d-9d21-24ffa8045e61";

// Default shadow / geo-location settings ("Boulder (CO)", "USA", summer solstice).
inline constexpr std::uint32_t kDefaultShadowTimeUnix = 1782048600u;  // 2026-06-21T13:30:00Z
inline constexpr const char* kDefaultCity = "Boulder (CO)";
inline constexpr const char* kDefaultCountry = "USA";
inline constexpr double kDefaultLongitude = -105.283;
inline constexpr double kDefaultLatitude = 40.017;
inline constexpr double kDefaultTzOffset = -7.0;
inline constexpr std::uint32_t kDefaultShadowLight = 80u;
inline constexpr std::uint32_t kDefaultShadowDark = 20u;

// SketchUp 2017 CVersionMap schema table (alphabetical class name -> schema version).
inline constexpr std::array<std::pair<const char*, std::uint32_t>, 58> kVersionMapV17 = {{
    {"CArcCurve", 3},
    {"CAttribute", 0},
    {"CAttributeContainer", 0},
    {"CAttributeNamed", 1},
    {"CBackgroundImage", 10},
    {"CCamera", 5},
    {"CComponent", 11},
    {"CComponentBehavior", 5},
    {"CComponentDefinition", 10},
    {"CComponentInstance", 5},
    {"CConstructionGeometry", 0},
    {"CConstructionLine", 1},
    {"CConstructionPoint", 0},
    {"CCurve", 4},
    {"CDefinitionList", 0},
    {"CDib", 3},
    {"CDimension", 1},
    {"CDimensionLinear", 6},
    {"CDimensionRadial", 2},
    {"CDimensionStyle", 4},
    {"CDrawingElement", 9},
    {"CEdge", 2},
    {"CEdgeUse", 1},
    {"CEntity", 5},
    {"CFace", 3},
    {"CFaceTextureCoords", 4},
    {"CFontManager", 0},
    {"CGroup", 1},
    {"CImage", 1},
    {"CLayer", 2},
    {"CLayerManager", 4},
    {"CLoop", 1},
    {"CMaterial", 12},
    {"CMaterialManager", 4},
    {"CPageList", 1},
    {"CPolyline3d", 0},
    {"CRelationship", 0},
    {"CRelationshipMap", 0},
    {"CRenderingOptions", 36},
    {"CSchemaFile", 1},
    {"CSchemaFilterFile", 0},
    {"CSchemaZipFile", 1},
    {"CSectionPlane", 2},
    {"CShadowInfo", 7},
    {"CSkFont", 1},
    {"CSketchCS", 0},
    {"CSketchUpModel", 26},
    {"CSketchUpPage", 1},
    {"CSkpStyle", 1},
    {"CSkpStyleManager", 2},
    {"CText", 9},
    {"CTextStyle", 5},
    {"CTexture", 6},
    {"CThumbnail", 1},
    {"CVertex", 0},
    {"CViewPage", 12},
    {"CWatermark", 1},
    {"CWatermarkManager", 2},
}};

inline constexpr const char* kVersionMapSentinel = "End-Of-Version-Map";

inline constexpr std::uint32_t pack_abgr(std::uint8_t r, std::uint8_t g, std::uint8_t b,
                                         std::uint8_t a = 255) {
  return static_cast<std::uint32_t>(r) | (static_cast<std::uint32_t>(g) << 8) |
         (static_cast<std::uint32_t>(b) << 16) | (static_cast<std::uint32_t>(a) << 24);
}

inline std::uint8_t hex_nibble_val(char c) {
  if (c >= '0' && c <= '9') return static_cast<std::uint8_t>(c - '0');
  if (c >= 'a' && c <= 'f') return static_cast<std::uint8_t>(c - 'a' + 10);
  if (c >= 'A' && c <= 'F') return static_cast<std::uint8_t>(c - 'A' + 10);
  throw std::invalid_argument("invalid hex digit in canonical UUID");
}

inline std::array<std::uint8_t, 16> uuid_to_bytes_le(const std::string& canonical_guid) {
  std::array<std::uint8_t, 16> raw{};
  std::size_t idx = 0;
  for (std::size_t i = 0; i < canonical_guid.size();) {
    if (canonical_guid[i] == '-') {
      ++i;
      continue;
    }
    raw[idx++] = static_cast<std::uint8_t>((hex_nibble_val(canonical_guid[i]) << 4) |
                                           hex_nibble_val(canonical_guid[i + 1]));
    i += 2;
  }
  constexpr std::array<std::size_t, 16> kOrder = {3, 2, 1,  0,  5,  4,  7,  6,
                                                  8, 9, 10, 11, 12, 13, 14, 15};
  std::array<std::uint8_t, 16> out{};
  for (std::size_t i = 0; i < 16; ++i) {
    out[i] = raw[kOrder[i]];
  }
  return out;
}

inline ByteBuffer encode_utf16_ascii_or_utf8(const std::string& text) {
  std::vector<std::uint16_t> units;
  units.reserve(text.size());
  for (std::size_t i = 0; i < text.size();) {
    const auto c0 = static_cast<unsigned char>(text[i]);
    std::uint32_t cp = 0;
    int len = 1;
    if (c0 < 0x80) {
      cp = c0;
    } else if ((c0 & 0xE0) == 0xC0) {
      cp = c0 & 0x1F;
      len = 2;
    } else if ((c0 & 0xF0) == 0xE0) {
      cp = c0 & 0x0F;
      len = 3;
    } else {
      cp = c0 & 0x07;
      len = 4;
    }
    for (int k = 1; k < len; ++k) {
      cp = (cp << 6) | (static_cast<unsigned char>(text[i + static_cast<std::size_t>(k)]) & 0x3F);
    }
    i += static_cast<std::size_t>(len);
    if (cp < 0x10000) {
      units.push_back(static_cast<std::uint16_t>(cp));
    } else {
      cp -= 0x10000;
      units.push_back(static_cast<std::uint16_t>(0xD800 | (cp >> 10)));
      units.push_back(static_cast<std::uint16_t>(0xDC00 | (cp & 0x3FF)));
    }
  }
  const std::size_t n = units.size();
  ByteBuffer out;
  out.reserve(10 + n * 2);
  out.insert(out.end(), kUtf16StringMarker.begin(), kUtf16StringMarker.end());
  if (n < 0xFF) {
    out.push_back(static_cast<std::uint8_t>(n));
  } else if (n < 0xFFFF) {
    out.push_back(0xFF);
    out.push_back(static_cast<std::uint8_t>(n & 0xFF));
    out.push_back(static_cast<std::uint8_t>((n >> 8) & 0xFF));
  } else {
    out.push_back(0xFF);
    out.push_back(0xFF);
    out.push_back(0xFF);
    for (int b = 0; b < 4; ++b) {
      out.push_back(static_cast<std::uint8_t>((n >> (8 * b)) & 0xFF));
    }
  }
  for (std::uint16_t cu : units) {
    out.push_back(static_cast<std::uint8_t>(cu & 0xFF));
    out.push_back(static_cast<std::uint8_t>((cu >> 8) & 0xFF));
  }
  return out;
}

struct CameraBodyParams {
  Point3 eye = {0.0, 0.0, 500.0};
  Point3 target = {0.0, 0.0, 0.0};
  Point3 up = {0.0, 1.0, 0.0};
  double clip_near = 1.0;
  double clip_far = 1000.0;
  bool perspective = true;
  double fov = 30.0;
  double ortho_height = 133.0;
  Point3 window_params = {0.0, 0.0, 0.0};
  double aspect_ratio = 0.0;
  bool fov_is_height = true;
  std::uint8_t camera_flag = 0;
  std::string description;
  double image_width = 0.0;
  bool is_2d = false;
  double scale_2d = 1.0;
  std::array<double, 2> center_2d = {0.0, 0.0};
};

// Serialize the body of a `CCamera` (schema 5) object after its MFC tag.
// With an empty `description` string this body is 176 bytes.
inline ByteBuffer pack_camera_body(const CameraBodyParams& p) {
  ByteBuffer out;
  out.reserve(176 + p.description.size() * 2);
  auto put_f64 = [&](double v) {
    std::uint64_t bits = 0;
    std::memcpy(&bits, &v, sizeof(bits));
    for (int i = 0; i < 8; ++i) {
      out.push_back(static_cast<std::uint8_t>((bits >> (8 * i)) & 0xFF));
    }
  };
  for (double c : p.eye) put_f64(c);
  for (double c : p.target) put_f64(c);
  for (double c : p.up) put_f64(c);
  put_f64(p.clip_near);
  put_f64(p.clip_far);
  out.push_back(p.perspective ? 1 : 0);
  put_f64(p.fov);
  put_f64(p.ortho_height);
  for (double c : p.window_params) put_f64(c);
  put_f64(p.aspect_ratio);
  out.push_back(p.fov_is_height ? 1 : 0);
  out.push_back(p.camera_flag);
  const ByteBuffer desc = encode_utf16_ascii_or_utf8(p.description);
  out.insert(out.end(), desc.begin(), desc.end());
  put_f64(p.image_width);
  out.push_back(p.is_2d ? 1 : 0);
  put_f64(p.scale_2d);
  for (double c : p.center_2d) put_f64(c);
  return out;
}

struct StylePropertyDef {
  std::uint32_t key_id;
  std::optional<std::uint32_t> kind;
  double value;
};

inline constexpr std::array<StylePropertyDef, 56> kDefaultStyleProperties = {{
    {1000, kStyleUint32, 1.0},
    {1001, kStyleUint32, 0.0},
    {1002, kStyleUint32, 1.0},
    {1004, kStyleBool, 0.0},
    {1005, kStyleUint32, 8.0},
    {1006, kStyleBool, 1.0},
    {1007, kStyleUint32, 3.0},
    {1008, kStyleBool, 0.0},
    {1009, kStyleUint32, 4.0},
    {1010, kStyleBool, 0.0},
    {1011, kStyleUint32, 5.0},
    {1012, kStyleBool, 0.0},
    {1014, kStyleUint32, static_cast<double>(pack_abgr(0, 0, 0, 255))},
    {1015, kStyleBool, 0.0},
    {1016, kStyleBool, 1.0},
    {2001, kStyleUint32, 2.0},
    {2002, kStyleUint32, static_cast<double>(pack_abgr(225, 225, 200, 255))},
    {2003, kStyleUint32, static_cast<double>(pack_abgr(128, 128, 200, 255))},
    {2004, kStyleBool, 0.0},
    {2005, kStyleBool, 1.0},
    {2006, kStyleUint32, 0.0},
    {2007, kStyleBool, 1.0},
    {2008, kStyleFloat64, 0.65},
    {4000, kStyleUint32, static_cast<double>(pack_abgr(255, 255, 255, 255))},
    {4001, kStyleUint32, static_cast<double>(pack_abgr(140, 165, 255, 255))},
    {4002, kStyleBool, 0.0},
    {4003, kStyleUint32, static_cast<double>(pack_abgr(63, 147, 63, 255))},
    {4004, kStyleBool, 0.0},
    {4005, kStyleUint32, 50.0},
    {4006, kStyleBool, 1.0},
    {4007, kStyleUint32, static_cast<double>(pack_abgr(0, 0, 0, 0))},
    {5000, kStyleBool, 1.0},
    {5001, std::nullopt, 0.0},
    {7000, kStyleUint32, static_cast<double>(pack_abgr(255, 255, 0, 255))},
    {7001, kStyleUint32, static_cast<double>(pack_abgr(255, 0, 0, 255))},
    {7002, kStyleUint32, static_cast<double>(pack_abgr(128, 128, 128, 255))},
    {7003, kStyleUint32, static_cast<double>(pack_abgr(102, 110, 141, 255))},
    {7004, kStyleUint32, static_cast<double>(pack_abgr(192, 192, 192, 255))},
    {7005, kStyleUint32, static_cast<double>(pack_abgr(255, 0, 0, 255))},
    {7008, kStyleBool, 1.0},
    {7010, kStyleBool, 0.0},
    {7011, kStyleBool, 0.0},
    {7012, kStyleBool, 0.0},
    {7013, kStyleUint32, 3.0},
    {7014, kStyleUint32, 4.0},
    {7015, kStyleBool, 0.0},
    {7016, kStyleUint32, static_cast<double>(pack_abgr(0, 0, 0, 255))},
    {7017, kStyleBool, 0.0},
    {7018, kStyleBool, 0.0},
    {8000, kStyleBool, 1.0},
    {8001, kStyleFloat64, 1.0},
    {8002, kStyleBool, 1.0},
    {8003, kStyleFloat64, 0.8},
    {8100, kStyleBool, 0.0},
    {8102, kStyleFloat32, 10.0},
    {8103, kStyleFloat32, 0.0},
}};

struct RenderingOptionField {
  int id;
  std::size_t offset;
  bool is_bool;
};

struct BlankDocument {
  ByteBuffer data;
  std::size_t model_guid_offset = 0;
  std::size_t pid_counter_offset = 0;
  std::uint64_t next_pid = 0;
  std::size_t no_scale_mask_offset = 0;
  std::map<std::string, std::size_t> unit_option_offsets;
  int attr_container_slot = 0;
  int attribute_named_slot = 0;
  int ccamera_slot = 0;
  std::size_t camera_body_offset = 0;
  std::size_t camera_body_len = 0;
  std::size_t rendering_options_offset = 0;
  std::size_t rendering_options_len = 0;
  std::vector<RenderingOptionField> rendering_option_fields;
  std::size_t material_count_pos = 0;
  std::size_t material_insert_pos = 0;
  int material_base_slot = 0;
  std::size_t layer_count_pos = 0;
  int orig_layer_count = 0;
  std::size_t layer0_pid_offset = 0;
  std::size_t layer0_material_pid_offset = 0;
  std::size_t layer_insert_pos = 0;
  int layer_writer_base_slot = 0;
  std::size_t active_layer_ref_pos = 0;
  std::size_t def_count_pos = 0;
  int orig_def_count = 0;
  std::size_t root_count_pos = 0;
  int orig_root_count = 0;
  std::size_t tail_pos = 0;
  int scaffold_next_slot = 0;
  std::map<std::string, int> scaffold_class_slot;
  std::size_t shadow_info_tail_rel = 0;
  std::size_t shadow_info_len = 0;
  std::size_t page_list_tail_rel = 0;
  std::size_t arial_font_pid_tail_rel = 0;
  std::size_t tahoma_font_pid_tail_rel = 0;
  std::vector<std::size_t> font_tail_refs;
  std::size_t style_object_tail_rel = 0;
  std::size_t saved_style_pid_tail_rel = 0;
  std::array<std::size_t, 2> style_tail_refs = {0, 0};
  std::vector<std::size_t> tail_ref_positions;
  std::size_t saved_style_guid_tail_rel = 0;
  std::size_t working_style_guid_tail_rel = 0;
  std::size_t saved_style_items_tail_rel = 0;
  std::size_t working_style_items_tail_rel = 0;
  std::size_t style_item_count = 0;
};

class BlankArchiveBuilder {
 public:
  ByteBuffer buf;
  int next_slot = 1;
  std::map<std::string, int> class_slot;

  BlankArchiveBuilder() { buf.reserve(6144); }

  void write_file_header(BlankDocument& doc) {
    str(kFileMagicText);
    str(kVersionStringV17);
    doc.model_guid_offset = buf.size();
    uuid(kDefaultModelGuid);
    str("");  // empty release/locale note string
    u32(kDefaultSaveTimestampUnix);
  }

  void write_version_map() {
    new_object("CVersionMap", 0);
    for (const auto& [cls_name, schema_ver] : kVersionMapV17) {
      str(cls_name);
      u32(schema_ver);
    }
    str(kVersionMapSentinel);
  }

  void write_model_preamble(BlankDocument& doc) {
    u32(0);  // model header word 0
    u32(1);  // model header format flag (1 in v17+)
    u32(kUtf16LeCodepage);
    u32(0);  // preview thumbnail status (0 = none)
    doc.pid_counter_offset = buf.size();
    doc.next_pid = kInitialNextPersistentId;
    u64(kInitialNextPersistentId);
    null_ref();  // null model preview CDib pointer
    u32(1);      // post-thumbnail model flag
    zeros(7);    // reserved pre-behavior model header bytes (all 0)
    doc.no_scale_mask_offset = buf.size();
    u32(0);   // root CComponentBehavior.no_scale_mask = 0
    str("");  // model description (SUModelGetDescription)
    u32(3);   // options-manager format version
  }

  void write_options_providers(BlankDocument& doc) {
    u32(4);  // four named SUOptionsProvider tables follow

    // 1. NamedOptions (empty)
    str("NamedOptions");
    str("");

    // 2. PageOptions
    str("PageOptions");
    write_attr_bool("ShowTransition", true);
    write_attr_double("TransitionTime", 1.5);
    str("");

    // 3. SlideshowOptions
    str("SlideshowOptions");
    write_attr_bool("LoopSlideshow", true);
    write_attr_double("SlideTime", 1.0);
    str("");

    // 4. UnitsOptions
    str("UnitsOptions");
    doc.unit_option_offsets["LengthPrecision"] = write_attr_int32("LengthPrecision", 4);
    doc.unit_option_offsets["LengthFormat"] = write_attr_int32("LengthFormat", 1);
    doc.unit_option_offsets["LengthUnit"] = write_attr_int32("LengthUnit", 0);
    write_attr_bool("LengthSnapEnabled", true);
    write_attr_double("LengthSnapLength", 0.0625);
    write_attr_int32("AnglePrecision", 1);
    write_attr_bool("AngleSnapEnabled", true);
    write_attr_double("SnapAngle", 15.0);
    write_attr_bool("SuppressUnitsDisplay", false);
    write_attr_bool("ForceInchDisplay", false);
    doc.unit_option_offsets["AreaUnit"] = write_attr_int32("AreaUnit", 0);
    doc.unit_option_offsets["VolumeUnit"] = write_attr_int32("VolumeUnit", 0);
    write_attr_int32("AreaPrecision", 2);
    write_attr_int32("VolumePrecision", 2);
    str("");
  }

  void write_model_properties(BlankDocument& doc) {
    new_object("CAttributeContainer", 0);
    doc.attr_container_slot = class_slot.at("CAttributeContainer");
    entity_preamble(0);

    new_object("CAttributeNamed", 1);
    doc.attribute_named_slot = class_slot.at("CAttributeNamed");
    entity_preamble(0);
    u32(0);  // CAttribute flags
    str("ModelProperties");
    write_attr_bool("IsClassified", false);
    write_attr_bool("IsDynamic", false);
    write_attr_bool("IsLive", false);
    str("");  // end of CAttributeNamed entries

    null_ref();  // end of CAttributeContainer children
    zeros(6);    // three null u16 fields before model CCamera
  }

  void write_model_camera(BlankDocument& doc) {
    new_object("CCamera", 5);
    doc.ccamera_slot = class_slot.at("CCamera");
    doc.camera_body_offset = buf.size();
    const ByteBuffer body = pack_camera_body(CameraBodyParams{});
    doc.camera_body_len = body.size();
    buf.insert(buf.end(), body.begin(), body.end());
  }

  void write_rendering_options(BlankDocument& doc) {
    entity_preamble(0);  // CRenderingOptions CEntity preamble
    const std::size_t ro_start = buf.size();
    std::vector<RenderingOptionField> fields;
    fields.reserve(41);

    auto ro_u8 = [&](std::uint8_t val, std::optional<int> style_item = std::nullopt) {
      if (style_item) fields.push_back({*style_item, buf.size(), true});
      u8(val);
    };
    auto ro_u32 = [&](std::uint32_t val, std::optional<int> style_item = std::nullopt) {
      if (style_item) fields.push_back({*style_item, buf.size(), false});
      u32(val);
    };
    auto ro_rgba = [&](std::uint8_t r, std::uint8_t g, std::uint8_t b, std::uint8_t a = 255,
                       std::optional<int> style_item = std::nullopt) {
      if (style_item) fields.push_back({*style_item, buf.size(), false});
      rgba(r, g, b, a);
    };

    ro_u32(2, 2001);                    // RenderMode: ShadedWithTextures
    ro_u8(0, 2004);                     // ModelTransparency (X-ray)
    ro_u8(1, 2005);                     // MaterialTransparency
    ro_u8(0, 1012);                     // JitterEdges
    ro_u32(1, 1000);                    // EdgeDisplayMode: show edges
    ro_rgba(0, 0, 0, 0, 4000);          // BackgroundColor slot
    ro_rgba(0, 0, 0, 0, 1014);          // ForegroundColor slot
    ro_rgba(255, 255, 0, 255, 7000);    // HighlightColor
    ro_rgba(128, 128, 128, 255, 7002);  // ConstructionColor
    ro_u8(0);                           // DisplayShadows
    ro_u8(0);                           // DisplayInstanceAxes
    ro_u8(0, 7011);                     // DisplayColorByLayer (Color by Tag)
    ro_u8(1, 2007);                     // Texture
    ro_u32(1, 1002);                    // EdgeColorMode: all same
    ro_u8(0, 1004);                     // ExtendLines
    ro_u32(8, 1005);                    // LineExtension: 8 px
    ro_u8(1, 1006);                     // DrawSilhouettes (Profiles)
    ro_u32(3, 1007);                    // SilhouetteWidth: 3 px
    ro_u8(0, 1008);                     // DrawDepthQue
    ro_u32(4, 1009);                    // DepthQueWidth: 4 px
    ro_u8(0, 1010);                     // DrawLineEnds (Endpoints)
    ro_u32(5, 1011);                    // LineEndWidth: 5 px
    ro_u8(0);                           // DrawProfilesOnly
    ro_u8(0, 7010);                     // DrawHidden
    u32(0);                             // reserved rendering-options word (0)
    ro_rgba(225, 225, 200, 255, 2002);  // FaceFrontColor
    ro_rgba(128, 128, 200, 255, 2003);  // FaceBackColor
    f64(0.75);                          // InactiveFade
    f64(0.5);                           // InstanceFade
    ro_u8(0);                           // InactiveHidden
    ro_u8(0);                           // InstanceHidden
    ro_u8(0);                           // DisplayFog
    ro_rgba(0, 0, 0, 0);                // FogColor
    ro_u8(1);                           // FogUseBkColor
    f64(-1.0);                          // FogStartDist (-1 = automatic)
    f64(-1.0);                          // FogEndDist (-1 = automatic)
    u32(1);                             // fog / stroke mode constant (1)
    ro_u32(0, 1001);                    // EdgeType: standard strokes
    ro_u8(1, 7008);                     // DisplaySketchAxes
    ro_u8(1);                           // DisplayText
    ro_u8(1);                           // DisplayDims
    ro_u8(0, 7012);                     // HideConstructionGeometry
    ro_rgba(140, 165, 255, 255, 4001);  // SkyColor
    ro_rgba(0, 0, 0, 0, 4007);          // HorizonColor
    ro_rgba(63, 147, 63, 255, 4003);    // GroundColor
    ro_u8(0, 4002);                     // DrawHorizon
    ro_u8(0, 4004);                     // DrawGround
    ro_u8(1, 4006);                     // DrawUnderground
    ro_u32(50, 4005);                   // GroundTransparency: 50
    ro_rgba(102, 110, 141, 255, 7003);  // SectionActiveColor
    ro_rgba(192, 192, 192, 255, 7004);  // SectionInactiveColor
    ro_rgba(255, 0, 0, 255, 7005);      // SectionDefaultCutColor
    ro_u32(4, 7014);                    // SectionCutWidth: 4
    ro_u32(3);                          // SectionDisplayMode: planes (1) | cuts (2)
    ro_u32(0, 2006);                    // TransparencySort: faster (0)
    ro_u8(1);                           // edge halo flag (style item 1016)
    f64(0.34906585039886595);           // 20 deg in radians (crease / halo angle)
    ro_u8(1);                           // ShowViewName: true
    ro_rgba(255, 0, 0, 255, 7001);      // LockedColor
    ro_u8(1, 5000);                     // DisplayWatermarks
    f64(0.65);                          // transparency factor (style item 2008)
    ro_u8(0, 1015);                     // DrawBackEdges
    ro_u8(1);                           // Match Photo background image (item 8000)
    f64(1.0);                           // Match Photo background opacity (item 8001)
    ro_u8(1);                           // Match Photo foreground photo (item 8002)
    f64(0.8);                           // Match Photo foreground opacity (item 8003)

    doc.rendering_options_offset = ro_start;
    doc.rendering_options_len = buf.size() - ro_start;
    std::sort(
        fields.begin(), fields.end(),
        [](const RenderingOptionField& a, const RenderingOptionField& b) { return a.id < b.id; });
    doc.rendering_option_fields = std::move(fields);
  }

  void write_component_and_material_manager(BlankDocument& doc) {
    u32(0);              // post-rendering-options word 0
    u32(1);              // post-rendering-options word 1
    entity_preamble(0);  // CSketchUpModel CEntity preamble (null model attributes)
    zeros(3);            // CComponent base header pad (offsets 0..2)
    u8(1);               // CComponent base flag 0
    u8(1);               // CComponent base flag 1
    entity_preamble(0);  // CMaterialManager CEntity preamble
    zeros(5);            // CMaterialManager header pad
    doc.material_count_pos = buf.size();
    u32(0);  // material_count = 0
    doc.material_insert_pos = buf.size();
    doc.material_base_slot = next_slot;
  }

  void write_layer_manager(BlankDocument& doc) {
    null_ref();          // active material ref (null = default unpainted)
    entity_preamble(0);  // CLayerManager CEntity preamble
    doc.layer_count_pos = buf.size();
    doc.orig_layer_count = 1;
    u32(static_cast<std::uint32_t>(doc.orig_layer_count));

    // Default untagged layer "Layer0" (schema 3, layer pid=4, material pid=5)
    const int layer0_slot = new_object("CLayer", 3);
    doc.scaffold_class_slot["CLayer"] = class_slot.at("CLayer");
    doc.layer0_pid_offset = entity_preamble(kLayer0Pid);
    str("Layer0");
    u8(0);   // hidden = False
    u16(0);  // layer flags = 0
    doc.layer0_material_pid_offset = buf.size();
    encode_pid(kLayer0MaterialPid);
    str("Layer_Layer0");
    u16(0x0100);             // embedded layer-colour material flag (256)
    rgba(255, 84, 84, 255);  // default Layer0 tag colour
    str("");                 // empty texture path
    u32(0);                  // colorize mode = 0
    u32(0);                  // colorize flags = 0
    f64(0.5);                // default layer material alpha/transparency
    u8(0);                   // use_opacity = False
    u32(0);                  // layer trailer word = 0

    doc.layer_insert_pos = buf.size();
    doc.layer_writer_base_slot = next_slot;
    doc.active_layer_ref_pos = buf.size();
    backref(layer0_slot);  // active layer back-ref -> Layer0
  }

  void write_definition_and_root_lists(BlankDocument& doc) {
    doc.def_count_pos = buf.size();
    doc.orig_def_count = 0;
    u32(static_cast<std::uint32_t>(doc.orig_def_count));

    doc.root_count_pos = buf.size();
    doc.orig_root_count = 0;
    u32(static_cast<std::uint32_t>(doc.orig_root_count));

    doc.tail_pos = buf.size();
    doc.scaffold_next_slot = next_slot;
  }

  void write_shadow_info(BlankDocument& doc) {
    u32(0);              // root CRelationship list count = 0
    u16(0);              // root component closing word = 0
    entity_preamble(0);  // CShadowInfo CEntity preamble

    const std::size_t shadow_start = buf.size();
    u32(kDefaultShadowTimeUnix);
    u8(0);  // DaylightSavings = False
    str(kDefaultCity);
    str(kDefaultCountry);
    f64(kDefaultLongitude);
    f64(kDefaultLatitude);
    f64(kDefaultTzOffset);
    f64(0.0);  // NorthAngle (degrees / sin = 0.0)
    f64(1.0);  // North direction Y = cos(0) = 1.0
    f64(0.0);  // North direction Z = 0.0
    u8(0);     // DisplayNorth = False
    u8(1);     // DisplayOnAllFaces = True
    u8(1);     // DisplayOnGroundPlane = True
    u8(0);     // EdgesCastShadows = False
    u32(kDefaultShadowLight);
    u32(kDefaultShadowDark);
    doc.shadow_info_tail_rel = shadow_start - doc.tail_pos;
    doc.shadow_info_len = buf.size() - shadow_start;

    doc.page_list_tail_rel = buf.size() - doc.tail_pos;
    u8(0);  // UseSunForAllShading = False
  }

  void write_page_list() {
    entity_preamble(0);  // CPageList CEntity preamble
    u32(0);              // page_count = 0
    null_ref();          // selected_page_ref = null
  }

  void write_model_axes() {
    zeros(3);            // page-list closing bytes (0)
    entity_preamble(0);  // CSketchCS CEntity preamble
    u8(1);               // axis flag 0
    u8(1);               // axis flag 1
    zeros(5);            // axis header pad
    f64(0.0);            // origin.x
    f64(0.0);            // origin.y
    f64(0.0);            // origin.z
    f64(1.0);            // x_axis.x
    f64(0.0);            // x_axis.y
    f64(0.0);            // x_axis.z
    f64(0.0);            // y_axis.x
    f64(1.0);            // y_axis.y
    f64(0.0);            // y_axis.z
    f64(0.0);            // z_axis.x
    f64(0.0);            // z_axis.y
    f64(1.0);            // z_axis.z
    zeros(68);           // empty post-axes model tables before CDimensionStyle
  }

  int write_dimension_style(BlankDocument& doc) {
    entity_preamble(0);  // CDimensionStyle CEntity preamble
    const auto [arial_slot, arial_pid_off] = write_skfont("Arial", 12, kDimStyleFontPid);
    doc.arial_font_pid_tail_rel = arial_pid_off - doc.tail_pos;
    u8(0);                  // has_3d = False
    u8(1);                  // dimension text visibility / placement flag
    u32(5);                 // extension_line_offset = 5
    u32(10);                // extension_line_overshoot = 10
    u32(1);                 // line_weight = 1
    u32(3);                 // arrow_type = 3 (closed arrow)
    u32(10);                // arrow_size = 10
    u8(0);                  // highlight_non_associative = False
    rgba(255, 0, 0, 255);   // highlight_non_associative_color = red
    u8(1);                  // show_radial_prefix = True
    u8(0);                  // hide_out_of_plane = False
    f64(0.6);               // hide_out_of_plane_value = 0.6
    u8(0);                  // hide_small = False
    f64(10.0);              // hide_small_value = 10.0
    rgba(64, 64, 64, 255);  // dimension colour = dark grey
    u8(0);                  // use_text_color = False
    zeros(3);               // text alignment / flags = 0
    u8(1);                  // show_units = True
    zeros(3);               // dimension-style trailer pad
    return arial_slot;
  }

  void write_text_style_and_font_manager(BlankDocument& doc, int arial_slot) {
    entity_preamble(0);  // CTextStyle CEntity preamble

    // Inline CSkFont("Tahoma"): its short class-ref (0x8000|11) shifts when slots grow.
    doc.font_tail_refs.push_back(buf.size() - doc.tail_pos);
    const auto [tahoma_slot, tahoma_pid_off] = write_skfont("Tahoma", 12, kTextStyleFontPid);
    doc.tahoma_font_pid_tail_rel = tahoma_pid_off - doc.tail_pos;
    u32(2);              // leader_type = 2 (pushpin)
    u32(1);              // display_leader = 1
    u8(0);               // text-style flag = 0
    u32(3);              // arrow_type = 3 (closed arrow)
    u8(1);               // leader visibility flag = 1
    rgba(0, 0, 0, 255);  // text colour = black
    rgba(0, 0, 0, 255);  // leader colour = black
    doc.font_tail_refs.push_back(buf.size() - doc.tail_pos);
    backref(tahoma_slot);

    // CFontManager: [preamble][count = 2][Arial ref][Tahoma ref][closing u16 = 0]
    entity_preamble(0);
    u32(2);
    doc.font_tail_refs.push_back(buf.size() - doc.tail_pos);
    backref(arial_slot);
    doc.font_tail_refs.push_back(buf.size() - doc.tail_pos);
    backref(tahoma_slot);
    u16(0);
  }

  void write_style_manager(BlankDocument& doc) {
    entity_preamble(0);  // CSkpStyleManager CEntity preamble
    u32(1);              // saved style count = 1

    doc.style_object_tail_rel = buf.size() - doc.tail_pos;
    const auto [saved_slot, saved_pid_off, saved_guid_off, saved_items_off] =
        write_skp_style("Style", "", kDefaultSavedStyleGuid, kSavedStylePid);
    doc.saved_style_pid_tail_rel = saved_pid_off - doc.tail_pos;

    const std::size_t selected_ref_tail_rel = buf.size() - doc.tail_pos;
    backref(saved_slot);  // selected saved style back-ref

    const std::size_t working_ref_tail_rel = buf.size() - doc.tail_pos;
    const auto [working_slot, working_pid_off, working_guid_off, working_items_off] =
        write_skp_style("Style", "", kDefaultWorkingStyleGuid, 0);
    (void)working_slot;
    (void)working_pid_off;

    u8(0);  // active_style_changed = False
    doc.style_tail_refs = {selected_ref_tail_rel, working_ref_tail_rel};
    doc.saved_style_guid_tail_rel = saved_guid_off - doc.tail_pos;
    doc.working_style_guid_tail_rel = working_guid_off - doc.tail_pos;
    doc.saved_style_items_tail_rel = saved_items_off - doc.tail_pos;
    doc.working_style_items_tail_rel = working_items_off - doc.tail_pos;
    doc.style_item_count = kDefaultStyleProperties.size();
  }

  void write_watermark_manager_and_trailer() {
    entity_preamble(0);  // CWatermarkManager CEntity preamble
    u32(0);              // watermark_count = 0
    u32(0);              // watermark flags / selection = 0
    u8(1);               // display_watermarks = True
    u32(0);              // closing model trailer = 0
  }

 private:
  void u8(std::uint8_t value) { buf.push_back(value); }

  void u16(std::uint16_t value) {
    buf.push_back(static_cast<std::uint8_t>(value & 0xFF));
    buf.push_back(static_cast<std::uint8_t>((value >> 8) & 0xFF));
  }

  void u32(std::uint32_t value) {
    for (int i = 0; i < 4; ++i) {
      buf.push_back(static_cast<std::uint8_t>((value >> (8 * i)) & 0xFF));
    }
  }

  void i32(std::int32_t value) { u32(static_cast<std::uint32_t>(value)); }

  void u64(std::uint64_t value) {
    for (int i = 0; i < 8; ++i) {
      buf.push_back(static_cast<std::uint8_t>((value >> (8 * i)) & 0xFF));
    }
  }

  void f32(float value) {
    std::uint32_t bits = 0;
    std::memcpy(&bits, &value, sizeof(bits));
    u32(bits);
  }

  void f64(double value) {
    std::uint64_t bits = 0;
    std::memcpy(&bits, &value, sizeof(bits));
    u64(bits);
  }

  void rgba(std::uint8_t r, std::uint8_t g, std::uint8_t b, std::uint8_t a = 255) {
    buf.push_back(r);
    buf.push_back(g);
    buf.push_back(b);
    buf.push_back(a);
  }

  void uuid(const std::string& canonical_guid) {
    const auto bytes = uuid_to_bytes_le(canonical_guid);
    buf.insert(buf.end(), bytes.begin(), bytes.end());
  }

  void str(const std::string& text) {
    const ByteBuffer enc = encode_utf16_ascii_or_utf8(text);
    buf.insert(buf.end(), enc.begin(), enc.end());
  }

  void zeros(std::size_t count) { buf.insert(buf.end(), count, 0); }

  int alloc_slot() { return next_slot++; }

  void null_ref() { u16(0); }

  void backref(int slot) { u16(static_cast<std::uint16_t>(slot)); }

  int new_object(const std::string& class_name, std::uint16_t schema) {
    auto it = class_slot.find(class_name);
    if (it == class_slot.end()) {
      u16(0xFFFF);
      u16(schema);
      u16(static_cast<std::uint16_t>(class_name.size()));
      buf.insert(buf.end(), class_name.begin(), class_name.end());
      class_slot[class_name] = alloc_slot();
      return alloc_slot();
    }
    u16(static_cast<std::uint16_t>(0x8000u | static_cast<std::uint16_t>(it->second)));
    return alloc_slot();
  }

  void encode_pid(std::uint64_t pid) {
    std::uint8_t mask = 0;
    std::array<std::uint8_t, 8> payload{};
    std::size_t plen = 0;
    for (int bit = 0; bit < 8; ++bit) {
      const auto byte_val = static_cast<std::uint8_t>((pid >> (8 * bit)) & 0xFF);
      if (byte_val != 0) {
        mask = static_cast<std::uint8_t>(mask | (1u << bit));
        payload[plen++] = byte_val;
      }
    }
    u8(mask);
    buf.insert(buf.end(), payload.begin(), payload.begin() + static_cast<std::ptrdiff_t>(plen));
  }

  std::size_t entity_preamble(std::uint64_t pid = 0) {
    null_ref();
    const std::size_t pid_off = buf.size();
    encode_pid(pid);
    return pid_off;
  }

  std::size_t write_attr_bool(const std::string& key, bool value) {
    str(key);
    u8(kBlankAttrBool);
    const std::size_t off = buf.size();
    u8(value ? 1 : 0);
    return off;
  }

  std::size_t write_attr_int32(const std::string& key, std::int32_t value) {
    str(key);
    u8(kBlankAttrInt32);
    const std::size_t off = buf.size();
    i32(value);
    return off;
  }

  std::size_t write_attr_double(const std::string& key, double value) {
    str(key);
    u8(kBlankAttrDouble);
    const std::size_t off = buf.size();
    f64(value);
    return off;
  }

  std::pair<int, std::size_t> write_skfont(const std::string& face_name, std::uint32_t point_size,
                                           std::uint64_t pid) {
    const int slot = new_object("CSkFont", 1);
    const std::size_t pid_off = entity_preamble(pid);
    str(face_name);
    u16(0);  // bold/italic flags = 0
    u32(point_size);
    u8(0);     // use_world_size = False
    f64(1.0);  // world_size = 1.0 inch
    return {slot, pid_off};
  }

  std::size_t write_style_properties() {
    u32(static_cast<std::uint32_t>(kDefaultStyleProperties.size()));
    const std::size_t items_start = buf.size();
    for (const auto& prop : kDefaultStyleProperties) {
      u32(prop.key_id);
      if (!prop.kind) {
        u32(0);
        continue;
      }
      u32(1);
      u32(*prop.kind);
      if (*prop.kind == kStyleBool) {
        u8(prop.value != 0.0 ? 1 : 0);
      } else if (*prop.kind == kStyleUint32) {
        u32(static_cast<std::uint32_t>(static_cast<std::int64_t>(prop.value)));
      } else if (*prop.kind == kStyleFloat32) {
        f32(static_cast<float>(prop.value));
      } else if (*prop.kind == kStyleFloat64) {
        f64(prop.value);
      } else {
        throw std::logic_error("unsupported style property kind");
      }
    }
    return items_start;
  }

  struct SkpStyleOffsets {
    int slot;
    std::size_t pid_offset;
    std::size_t guid_offset;
    std::size_t items_offset;
  };

  SkpStyleOffsets write_skp_style(const std::string& name, const std::string& description,
                                  const std::string& guid, std::uint64_t pid) {
    const int slot = new_object("CSkpStyle", 2);
    const std::size_t pid_off = entity_preamble(pid);
    const std::size_t guid_off = buf.size();
    uuid(guid);
    str("");  // source .style path
    u32(3);   // style format version = 3
    str(name);
    str(description);
    const std::size_t items_off = write_style_properties();
    return {slot, pid_off, guid_off, items_off};
  }
};

inline BlankDocument build_blank_v17() {
  BlankDocument doc;
  BlankArchiveBuilder b;
  b.write_file_header(doc);
  b.write_version_map();
  b.write_model_preamble(doc);
  b.write_options_providers(doc);
  b.write_model_properties(doc);
  b.write_model_camera(doc);
  b.write_rendering_options(doc);
  b.write_component_and_material_manager(doc);
  b.write_layer_manager(doc);
  b.write_definition_and_root_lists(doc);
  b.write_shadow_info(doc);
  b.write_page_list();
  b.write_model_axes();
  const int arial_slot = b.write_dimension_style(doc);
  b.write_text_style_and_font_manager(doc, arial_slot);
  b.write_style_manager(doc);
  b.write_watermark_manager_and_trailer();

  doc.tail_ref_positions = doc.font_tail_refs;
  doc.tail_ref_positions.insert(doc.tail_ref_positions.end(), doc.style_tail_refs.begin(),
                                doc.style_tail_refs.end());
  doc.data = std::move(b.buf);
  return doc;
}

}  // namespace detail
}  // namespace openskp
