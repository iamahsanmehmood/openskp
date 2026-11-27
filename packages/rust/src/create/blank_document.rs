//! Construct an empty classic-format (SketchUp 2017, v17) `.skp` document in code.
//!
//! Every record of the empty MFC `CArchive` document is written field by field through named
//! builder methods and named constants — no bundled binary file, hex blob, or compressed template
//! is read or embedded. As each section is emitted, the builder tracks the 1-based MFC store-map
//! slots and byte offsets that `create` splices into or patches when writing geometry, materials,
//! layers, component definitions, scenes, and document options.

use std::collections::HashMap;

pub type BlankPoint3 = [f64; 3];

pub const SCAFFOLD_SHA256: &str =
    "809a1ab73a20a192ab13aaff197afb1c67d0e9352f6a353a9cd8030919f8a6c3";

// MFC CArchive UTF-16LE string prefix marker.
const UTF16_STRING_MARKER: [u8; 3] = [0xFF, 0xFE, 0xFF];

// CAttributeNamed value-type tags used in OptionsManager providers and ModelProperties.
const ATTR_INT32: u8 = 0x04;
const ATTR_DOUBLE: u8 = 0x06;
const ATTR_BOOL: u8 = 0x07;

// CSkpStyle typed-property value tags.
const STYLE_BOOL: u32 = 1;
const STYLE_UINT32: u32 = 4;
const STYLE_FLOAT32: u32 = 6;
const STYLE_FLOAT64: u32 = 7;

// File-header defaults matching the SketchUp 2017 blank document.
const FILE_MAGIC_TEXT: &str = "SketchUp Model";
const VERSION_STRING_V17: &str = "{17.0.1}";
const DEFAULT_MODEL_GUID: &str = "3093d39a-aa69-4e29-8d53-15cbfe3d94b0";
const DEFAULT_SAVE_TIMESTAMP_UNIX: u32 = 1_786_620_315; // 2026-08-13T11:25:15Z (time_t)
const UTF16LE_CODEPAGE: u32 = 1200; // Windows UTF-16LE code page (0x04B0)
const INITIAL_NEXT_PERSISTENT_ID: u64 = 18;

// Persistent entity IDs assigned in the empty document.
const SAVED_STYLE_PID: u64 = 1;
const DIM_STYLE_FONT_PID: u64 = 2;
const TEXT_STYLE_FONT_PID: u64 = 3;
const LAYER0_PID: u64 = 4;
const LAYER0_MATERIAL_PID: u64 = 5;

// Saved and working CSkpStyle GUIDs in the empty document.
const DEFAULT_SAVED_STYLE_GUID: &str = "94fee66b-2dce-4b79-ada7-4be56f9266a2";
const DEFAULT_WORKING_STYLE_GUID: &str = "667aac7a-d15a-409d-9d21-24ffa8045e61";

// Default shadow / geo-location settings ("Boulder (CO)", "USA", summer solstice).
const DEFAULT_SHADOW_TIME_UNIX: u32 = 1_782_048_600; // 2026-06-21T13:30:00Z
const DEFAULT_CITY: &str = "Boulder (CO)";
const DEFAULT_COUNTRY: &str = "USA";
const DEFAULT_LONGITUDE: f64 = -105.283;
const DEFAULT_LATITUDE: f64 = 40.017;
const DEFAULT_TZ_OFFSET: f64 = -7.0;
const DEFAULT_SHADOW_LIGHT: u32 = 80;
const DEFAULT_SHADOW_DARK: u32 = 20;

/// SketchUp 2017 `CVersionMap` schema table (alphabetical class name -> schema version).
pub const VERSION_MAP_V17: [(&str, u32); 58] = [
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
];

const VERSION_MAP_SENTINEL: &str = "End-Of-Version-Map";

const fn pack_abgr(r: u8, g: u8, b: u8, a: u8) -> u32 {
    (r as u32) | ((g as u32) << 8) | ((b as u32) << 16) | ((a as u32) << 24)
}

fn hex_nibble(b: u8) -> u8 {
    match b {
        b'0'..=b'9' => b - b'0',
        b'a'..=b'f' => b - b'a' + 10,
        b'A'..=b'F' => b - b'A' + 10,
        _ => panic!("invalid hex digit in canonical UUID"),
    }
}

pub(crate) fn uuid_to_bytes_le(canonical_guid: &str) -> [u8; 16] {
    let mut raw = [0u8; 16];
    let mut idx = 0usize;
    let bytes = canonical_guid.as_bytes();
    let mut i = 0usize;
    while i < bytes.len() {
        if bytes[i] == b'-' {
            i += 1;
            continue;
        }
        raw[idx] = (hex_nibble(bytes[i]) << 4) | hex_nibble(bytes[i + 1]);
        idx += 1;
        i += 2;
    }
    assert_eq!(idx, 16);
    const ORDER: [usize; 16] = [3, 2, 1, 0, 5, 4, 7, 6, 8, 9, 10, 11, 12, 13, 14, 15];
    let mut out = [0u8; 16];
    for (dst, &src) in out.iter_mut().zip(ORDER.iter()) {
        *dst = raw[src];
    }
    out
}

fn encode_utf16_str(text: &str) -> Vec<u8> {
    let units: Vec<u16> = text.encode_utf16().collect();
    let n = units.len();
    let mut out = Vec::with_capacity(10 + n * 2);
    out.extend_from_slice(&UTF16_STRING_MARKER);
    if n < 0xFF {
        out.push(n as u8);
    } else if n < 0xFFFF {
        out.push(0xFF);
        out.extend_from_slice(&(n as u16).to_le_bytes());
    } else {
        out.extend_from_slice(&[0xFF, 0xFF, 0xFF]);
        out.extend_from_slice(&(n as u32).to_le_bytes());
    }
    for cu in units {
        out.extend_from_slice(&cu.to_le_bytes());
    }
    out
}

/// Parameters for [`pack_camera_body`].
#[derive(Clone, Debug)]
pub struct CameraBodyParams<'a> {
    pub eye: BlankPoint3,
    pub target: BlankPoint3,
    pub up: BlankPoint3,
    pub clip_near: f64,
    pub clip_far: f64,
    pub perspective: bool,
    pub fov: f64,
    pub ortho_height: f64,
    pub window_params: BlankPoint3,
    pub aspect_ratio: f64,
    pub fov_is_height: bool,
    pub camera_flag: u8,
    pub description: &'a str,
    pub image_width: f64,
    pub is_2d: bool,
    pub scale_2d: f64,
    pub center_2d: [f64; 2],
}

impl Default for CameraBodyParams<'_> {
    fn default() -> Self {
        Self {
            eye: [0.0, 0.0, 500.0],
            target: [0.0, 0.0, 0.0],
            up: [0.0, 1.0, 0.0],
            clip_near: 1.0,
            clip_far: 1000.0,
            perspective: true,
            fov: 30.0,
            ortho_height: 133.0,
            window_params: [0.0, 0.0, 0.0],
            aspect_ratio: 0.0,
            fov_is_height: true,
            camera_flag: 0,
            description: "",
            image_width: 0.0,
            is_2d: false,
            scale_2d: 1.0,
            center_2d: [0.0, 0.0],
        }
    }
}

/// Serialize the body of a `CCamera` (schema 5) object after its MFC tag.
/// With an empty `description` string this body is 176 bytes.
pub fn pack_camera_body(params: &CameraBodyParams<'_>) -> Vec<u8> {
    let mut out = Vec::with_capacity(176 + params.description.len() * 2);
    for &c in &params.eye {
        out.extend_from_slice(&c.to_le_bytes());
    }
    for &c in &params.target {
        out.extend_from_slice(&c.to_le_bytes());
    }
    for &c in &params.up {
        out.extend_from_slice(&c.to_le_bytes());
    }
    out.extend_from_slice(&params.clip_near.to_le_bytes());
    out.extend_from_slice(&params.clip_far.to_le_bytes());
    out.push(u8::from(params.perspective));
    out.extend_from_slice(&params.fov.to_le_bytes());
    out.extend_from_slice(&params.ortho_height.to_le_bytes());
    for &c in &params.window_params {
        out.extend_from_slice(&c.to_le_bytes());
    }
    out.extend_from_slice(&params.aspect_ratio.to_le_bytes());
    out.push(u8::from(params.fov_is_height));
    out.push(params.camera_flag);
    out.extend_from_slice(&encode_utf16_str(params.description));
    out.extend_from_slice(&params.image_width.to_le_bytes());
    out.push(u8::from(params.is_2d));
    out.extend_from_slice(&params.scale_2d.to_le_bytes());
    for &c in &params.center_2d {
        out.extend_from_slice(&c.to_le_bytes());
    }
    out
}

// Default 56-entry CSkpStyle property table: (key_id, value_kind, value).
const DEFAULT_STYLE_PROPERTIES: [(u32, Option<u32>, f64); 56] = [
    (1000, Some(STYLE_UINT32), 1.0), // EdgeDisplayMode: show edges
    (1001, Some(STYLE_UINT32), 0.0), // EdgeType: standard strokes
    (1002, Some(STYLE_UINT32), 1.0), // EdgeColorMode: all same
    (1004, Some(STYLE_BOOL), 0.0),   // ExtendLines: false
    (1005, Some(STYLE_UINT32), 8.0), // LineExtension: 8 px
    (1006, Some(STYLE_BOOL), 1.0),   // DrawSilhouettes (Profiles): true
    (1007, Some(STYLE_UINT32), 3.0), // SilhouetteWidth: 3 px
    (1008, Some(STYLE_BOOL), 0.0),   // DrawDepthQue: false
    (1009, Some(STYLE_UINT32), 4.0), // DepthQueWidth: 4 px
    (1010, Some(STYLE_BOOL), 0.0),   // DrawLineEnds (Endpoints): false
    (1011, Some(STYLE_UINT32), 5.0), // LineEndWidth: 5 px
    (1012, Some(STYLE_BOOL), 0.0),   // JitterEdges: false
    (1014, Some(STYLE_UINT32), pack_abgr(0, 0, 0, 255) as f64), // ForegroundColor: black
    (1015, Some(STYLE_BOOL), 0.0),   // DrawBackEdges: false
    (1016, Some(STYLE_BOOL), 1.0),   // Edge halo / stroke flag: true
    (2001, Some(STYLE_UINT32), 2.0), // RenderMode: ShadedWithTextures
    (
        2002,
        Some(STYLE_UINT32),
        pack_abgr(225, 225, 200, 255) as f64,
    ), // FaceFrontColor
    (
        2003,
        Some(STYLE_UINT32),
        pack_abgr(128, 128, 200, 255) as f64,
    ), // FaceBackColor
    (2004, Some(STYLE_BOOL), 0.0),   // ModelTransparency (X-ray): false
    (2005, Some(STYLE_BOOL), 1.0),   // MaterialTransparency: true
    (2006, Some(STYLE_UINT32), 0.0), // TransparencySort: faster (0)
    (2007, Some(STYLE_BOOL), 1.0),   // Texture: true
    (2008, Some(STYLE_FLOAT64), 0.65), // Transparency factor: 0.65
    (
        4000,
        Some(STYLE_UINT32),
        pack_abgr(255, 255, 255, 255) as f64,
    ), // BackgroundColor: white
    (
        4001,
        Some(STYLE_UINT32),
        pack_abgr(140, 165, 255, 255) as f64,
    ), // SkyColor
    (4002, Some(STYLE_BOOL), 0.0),   // DrawHorizon (Sky): false
    (4003, Some(STYLE_UINT32), pack_abgr(63, 147, 63, 255) as f64), // GroundColor
    (4004, Some(STYLE_BOOL), 0.0),   // DrawGround: false
    (4005, Some(STYLE_UINT32), 50.0), // GroundTransparency: 50
    (4006, Some(STYLE_BOOL), 1.0),   // DrawUnderground: true
    (4007, Some(STYLE_UINT32), pack_abgr(0, 0, 0, 0) as f64), // HorizonColor: transparent black
    (5000, Some(STYLE_BOOL), 1.0),   // DisplayWatermarks: true
    (5001, None, 0.0),               // Watermarks list: empty
    (7000, Some(STYLE_UINT32), pack_abgr(255, 255, 0, 255) as f64), // HighlightColor: yellow
    (7001, Some(STYLE_UINT32), pack_abgr(255, 0, 0, 255) as f64), // LockedColor: red
    (
        7002,
        Some(STYLE_UINT32),
        pack_abgr(128, 128, 128, 255) as f64,
    ), // ConstructionColor: grey
    (
        7003,
        Some(STYLE_UINT32),
        pack_abgr(102, 110, 141, 255) as f64,
    ), // SectionActiveColor
    (
        7004,
        Some(STYLE_UINT32),
        pack_abgr(192, 192, 192, 255) as f64,
    ), // SectionInactiveColor
    (7005, Some(STYLE_UINT32), pack_abgr(255, 0, 0, 255) as f64), // SectionDefaultCutColor
    (7008, Some(STYLE_BOOL), 1.0),   // DisplaySketchAxes: true
    (7010, Some(STYLE_BOOL), 0.0),   // DrawHidden: false
    (7011, Some(STYLE_BOOL), 0.0),   // DisplayColorByLayer: false
    (7012, Some(STYLE_BOOL), 0.0),   // HideConstructionGeometry: false
    (7013, Some(STYLE_UINT32), 3.0), // SectionDisplayMode
    (7014, Some(STYLE_UINT32), 4.0), // SectionCutWidth: 4
    (7015, Some(STYLE_BOOL), 0.0),   // SectionCutFilled: false
    (7016, Some(STYLE_UINT32), pack_abgr(0, 0, 0, 255) as f64), // SectionDefaultFillColor
    (7017, Some(STYLE_BOOL), 0.0),   // DrawHiddenGeometry: false
    (7018, Some(STYLE_BOOL), 0.0),   // DrawHiddenObjects: false
    (8000, Some(STYLE_BOOL), 1.0),   // Match Photo background image
    (8001, Some(STYLE_FLOAT64), 1.0), // Match Photo background opacity
    (8002, Some(STYLE_BOOL), 1.0),   // Match Photo foreground photo
    (8003, Some(STYLE_FLOAT64), 0.8), // Match Photo foreground opacity
    (8100, Some(STYLE_BOOL), 0.0),   // AmbientOcclusion: false
    (8102, Some(STYLE_FLOAT32), 10.0), // AmbientOcclusionDistance: 10.0
    (8103, Some(STYLE_FLOAT32), 0.0), // AmbientOcclusionIntensity: 0.0
];

/// Serialized empty v17 `.skp` document together with every named splice position, field offset,
/// length, and MFC store-map slot `create` needs.
#[derive(Clone, Debug)]
pub struct BlankDocument {
    pub data: Vec<u8>,
    pub model_guid_offset: usize,
    pub pid_counter_offset: usize,
    pub next_pid: u64,
    pub no_scale_mask_offset: usize,
    pub unit_option_offsets: HashMap<&'static str, usize>,
    pub attr_container_slot: i32,
    pub attribute_named_slot: i32,
    pub ccamera_slot: i32,
    pub camera_body_offset: usize,
    pub camera_body_len: usize,
    pub rendering_options_offset: usize,
    pub rendering_options_len: usize,
    pub rendering_option_fields: Vec<(i32, usize, bool)>,
    pub material_count_pos: usize,
    pub material_insert_pos: usize,
    pub material_base_slot: i32,
    pub layer_count_pos: usize,
    pub orig_layer_count: usize,
    pub layer_insert_pos: usize,
    pub layer_writer_base_slot: i32,
    pub active_layer_ref_pos: usize,
    pub def_count_pos: usize,
    pub orig_def_count: usize,
    pub root_count_pos: usize,
    pub orig_root_count: usize,
    pub tail_pos: usize,
    pub scaffold_next_slot: i32,
    pub scaffold_class_slot: HashMap<&'static str, i32>,
    pub shadow_info_tail_rel: usize,
    pub shadow_info_len: usize,
    pub page_list_tail_rel: usize,
    pub font_tail_refs: Vec<usize>,
    pub style_object_tail_rel: usize,
    pub style_tail_refs: [usize; 2],
    pub tail_ref_positions: Vec<usize>,
    pub saved_style_guid_tail_rel: usize,
    pub working_style_guid_tail_rel: usize,
    pub saved_style_items_tail_rel: usize,
    pub working_style_items_tail_rel: usize,
    pub style_item_count: usize,
}

enum AttrVal {
    Bool(bool),
    Int32(i32),
    Double(f64),
}

struct BlankArchiveBuilder {
    buf: Vec<u8>,
    next_slot: i32,
    class_slot: HashMap<&'static str, i32>,
}

impl BlankArchiveBuilder {
    fn new() -> Self {
        Self {
            buf: Vec::with_capacity(6144),
            next_slot: 1,
            class_slot: HashMap::new(),
        }
    }

    fn u8(&mut self, value: u8) {
        self.buf.push(value);
    }

    fn u16(&mut self, value: u16) {
        self.buf.extend_from_slice(&value.to_le_bytes());
    }

    fn u32(&mut self, value: u32) {
        self.buf.extend_from_slice(&value.to_le_bytes());
    }

    fn i32(&mut self, value: i32) {
        self.buf.extend_from_slice(&value.to_le_bytes());
    }

    fn u64(&mut self, value: u64) {
        self.buf.extend_from_slice(&value.to_le_bytes());
    }

    fn f32(&mut self, value: f32) {
        self.buf.extend_from_slice(&value.to_le_bytes());
    }

    fn f64(&mut self, value: f64) {
        self.buf.extend_from_slice(&value.to_le_bytes());
    }

    fn rgba(&mut self, r: u8, g: u8, b: u8, a: u8) {
        self.buf.extend_from_slice(&[r, g, b, a]);
    }

    fn uuid(&mut self, canonical_guid: &str) {
        self.buf
            .extend_from_slice(&uuid_to_bytes_le(canonical_guid));
    }

    fn str(&mut self, text: &str) {
        self.buf.extend_from_slice(&encode_utf16_str(text));
    }

    fn zeros(&mut self, count: usize) {
        self.buf.resize(self.buf.len() + count, 0);
    }

    fn alloc_slot(&mut self) -> i32 {
        let slot = self.next_slot;
        self.next_slot += 1;
        slot
    }

    fn null_ref(&mut self) {
        self.u16(0);
    }

    fn backref(&mut self, slot: i32) {
        self.u16(slot as u16);
    }

    fn new_object(&mut self, class_name: &'static str, schema: u16) -> i32 {
        if let Some(&cslot) = self.class_slot.get(class_name) {
            self.u16(0x8000 | (cslot as u16));
            return self.alloc_slot();
        }
        self.u16(0xFFFF);
        self.u16(schema);
        self.u16(class_name.len() as u16);
        self.buf.extend_from_slice(class_name.as_bytes());
        let cslot = self.alloc_slot();
        self.class_slot.insert(class_name, cslot);
        self.alloc_slot()
    }

    fn encode_pid(&mut self, pid: u64) {
        let bytes = pid.to_le_bytes();
        let mut mask = 0u8;
        let mut payload = [0u8; 8];
        let mut plen = 0usize;
        for (bit, &b) in bytes.iter().enumerate() {
            if b != 0 {
                mask |= 1 << bit;
                payload[plen] = b;
                plen += 1;
            }
        }
        self.u8(mask);
        self.buf.extend_from_slice(&payload[..plen]);
    }

    fn entity_preamble(&mut self, pid: u64) {
        self.null_ref();
        self.encode_pid(pid);
    }

    fn write_attr_entry(&mut self, key: &str, value: AttrVal) -> usize {
        self.str(key);
        match value {
            AttrVal::Bool(v) => {
                self.u8(ATTR_BOOL);
                let off = self.buf.len();
                self.u8(u8::from(v));
                off
            }
            AttrVal::Int32(v) => {
                self.u8(ATTR_INT32);
                let off = self.buf.len();
                self.i32(v);
                off
            }
            AttrVal::Double(v) => {
                self.u8(ATTR_DOUBLE);
                let off = self.buf.len();
                self.f64(v);
                off
            }
        }
    }

    // ── document sections ────────────────────────────────────────────────────

    fn write_file_header(&mut self) -> usize {
        self.str(FILE_MAGIC_TEXT);
        self.str(VERSION_STRING_V17);
        let model_guid_offset = self.buf.len();
        self.uuid(DEFAULT_MODEL_GUID);
        self.str(""); // empty release/locale note string
        self.u32(DEFAULT_SAVE_TIMESTAMP_UNIX);
        model_guid_offset
    }

    fn write_version_map(&mut self) {
        self.new_object("CVersionMap", 0);
        for (cls_name, schema_ver) in VERSION_MAP_V17 {
            self.str(cls_name);
            self.u32(schema_ver);
        }
        self.str(VERSION_MAP_SENTINEL);
    }

    fn write_model_preamble(&mut self) -> (usize, usize) {
        self.u32(0); // model header word 0
        self.u32(1); // model header format flag (1 in v17+)
        self.u32(UTF16LE_CODEPAGE);
        self.u32(0); // preview thumbnail status (0 = none)
        let pid_counter_offset = self.buf.len();
        self.u64(INITIAL_NEXT_PERSISTENT_ID);
        self.null_ref(); // null model preview CDib pointer
        self.u32(1); // post-thumbnail model flag
        self.zeros(7); // reserved pre-behavior model header bytes (all 0)
        let no_scale_mask_offset = self.buf.len();
        self.u32(0); // root CComponentBehavior.no_scale_mask = 0
        self.str(""); // model description (SUModelGetDescription)
        self.u32(3); // options-manager format version
        (pid_counter_offset, no_scale_mask_offset)
    }

    fn write_options_providers(&mut self) -> HashMap<&'static str, usize> {
        self.u32(4); // four named SUOptionsProvider tables follow

        // 1. NamedOptions (empty)
        self.str("NamedOptions");
        self.str("");

        // 2. PageOptions
        self.str("PageOptions");
        self.write_attr_entry("ShowTransition", AttrVal::Bool(true));
        self.write_attr_entry("TransitionTime", AttrVal::Double(1.5));
        self.str("");

        // 3. SlideshowOptions
        self.str("SlideshowOptions");
        self.write_attr_entry("LoopSlideshow", AttrVal::Bool(true));
        self.write_attr_entry("SlideTime", AttrVal::Double(1.0));
        self.str("");

        // 4. UnitsOptions
        self.str("UnitsOptions");
        let mut unit_offsets = HashMap::new();
        unit_offsets.insert(
            "LengthPrecision",
            self.write_attr_entry("LengthPrecision", AttrVal::Int32(4)),
        );
        unit_offsets.insert(
            "LengthFormat",
            self.write_attr_entry("LengthFormat", AttrVal::Int32(1)),
        );
        unit_offsets.insert(
            "LengthUnit",
            self.write_attr_entry("LengthUnit", AttrVal::Int32(0)),
        );
        self.write_attr_entry("LengthSnapEnabled", AttrVal::Bool(true));
        self.write_attr_entry("LengthSnapLength", AttrVal::Double(0.0625));
        self.write_attr_entry("AnglePrecision", AttrVal::Int32(1));
        self.write_attr_entry("AngleSnapEnabled", AttrVal::Bool(true));
        self.write_attr_entry("SnapAngle", AttrVal::Double(15.0));
        self.write_attr_entry("SuppressUnitsDisplay", AttrVal::Bool(false));
        self.write_attr_entry("ForceInchDisplay", AttrVal::Bool(false));
        unit_offsets.insert(
            "AreaUnit",
            self.write_attr_entry("AreaUnit", AttrVal::Int32(0)),
        );
        unit_offsets.insert(
            "VolumeUnit",
            self.write_attr_entry("VolumeUnit", AttrVal::Int32(0)),
        );
        self.write_attr_entry("AreaPrecision", AttrVal::Int32(2));
        self.write_attr_entry("VolumePrecision", AttrVal::Int32(2));
        self.str("");
        unit_offsets
    }

    fn write_model_properties(&mut self) {
        self.new_object("CAttributeContainer", 0);
        self.entity_preamble(0);

        self.new_object("CAttributeNamed", 1);
        self.entity_preamble(0);
        self.u32(0); // CAttribute flags
        self.str("ModelProperties");
        self.write_attr_entry("IsClassified", AttrVal::Bool(false));
        self.write_attr_entry("IsDynamic", AttrVal::Bool(false));
        self.write_attr_entry("IsLive", AttrVal::Bool(false));
        self.str(""); // end of CAttributeNamed entries

        self.null_ref(); // end of CAttributeContainer children
        self.zeros(6); // three null u16 fields before model CCamera
    }

    fn write_model_camera(&mut self) -> (usize, usize) {
        self.new_object("CCamera", 5);
        let body_offset = self.buf.len();
        let body = pack_camera_body(&CameraBodyParams::default());
        let body_len = body.len();
        self.buf.extend_from_slice(&body);
        (body_offset, body_len)
    }

    fn write_rendering_options(&mut self) -> (usize, usize, Vec<(i32, usize, bool)>) {
        self.entity_preamble(0); // CRenderingOptions CEntity preamble
        let ro_start = self.buf.len();
        let mut fields: Vec<(i32, usize, bool)> = Vec::with_capacity(41);

        macro_rules! ro_u8 {
            ($val:expr) => {
                self.u8($val);
            };
            ($val:expr, $id:expr) => {{
                fields.push(($id, self.buf.len(), true));
                self.u8($val);
            }};
        }
        macro_rules! ro_u32 {
            ($val:expr) => {
                self.u32($val);
            };
            ($val:expr, $id:expr) => {{
                fields.push(($id, self.buf.len(), false));
                self.u32($val);
            }};
        }
        macro_rules! ro_rgba {
            ($r:expr, $g:expr, $b:expr, $a:expr) => {
                self.rgba($r, $g, $b, $a);
            };
            ($r:expr, $g:expr, $b:expr, $a:expr, $id:expr) => {{
                fields.push(($id, self.buf.len(), false));
                self.rgba($r, $g, $b, $a);
            }};
        }

        ro_u32!(2, 2001); // RenderMode: ShadedWithTextures
        ro_u8!(0, 2004); // ModelTransparency (X-ray)
        ro_u8!(1, 2005); // MaterialTransparency
        ro_u8!(0, 1012); // JitterEdges
        ro_u32!(1, 1000); // EdgeDisplayMode: show edges
        ro_rgba!(0, 0, 0, 0, 4000); // BackgroundColor slot
        ro_rgba!(0, 0, 0, 0, 1014); // ForegroundColor slot
        ro_rgba!(255, 255, 0, 255, 7000); // HighlightColor
        ro_rgba!(128, 128, 128, 255, 7002); // ConstructionColor
        ro_u8!(0); // DisplayShadows
        ro_u8!(0); // DisplayInstanceAxes
        ro_u8!(0, 7011); // DisplayColorByLayer (Color by Tag)
        ro_u8!(1, 2007); // Texture
        ro_u32!(1, 1002); // EdgeColorMode: all same
        ro_u8!(0, 1004); // ExtendLines
        ro_u32!(8, 1005); // LineExtension: 8 px
        ro_u8!(1, 1006); // DrawSilhouettes (Profiles)
        ro_u32!(3, 1007); // SilhouetteWidth: 3 px
        ro_u8!(0, 1008); // DrawDepthQue
        ro_u32!(4, 1009); // DepthQueWidth: 4 px
        ro_u8!(0, 1010); // DrawLineEnds (Endpoints)
        ro_u32!(5, 1011); // LineEndWidth: 5 px
        ro_u8!(0); // DrawProfilesOnly
        ro_u8!(0, 7010); // DrawHidden
        self.u32(0); // reserved rendering-options word (0)
        ro_rgba!(225, 225, 200, 255, 2002); // FaceFrontColor
        ro_rgba!(128, 128, 200, 255, 2003); // FaceBackColor
        self.f64(0.75); // InactiveFade
        self.f64(0.5); // InstanceFade
        ro_u8!(0); // InactiveHidden
        ro_u8!(0); // InstanceHidden
        ro_u8!(0); // DisplayFog
        ro_rgba!(0, 0, 0, 0); // FogColor
        ro_u8!(1); // FogUseBkColor
        self.f64(-1.0); // FogStartDist (-1 = automatic)
        self.f64(-1.0); // FogEndDist (-1 = automatic)
        self.u32(1); // fog / stroke mode constant (1)
        ro_u32!(0, 1001); // EdgeType: standard strokes
        ro_u8!(1, 7008); // DisplaySketchAxes
        ro_u8!(1); // DisplayText
        ro_u8!(1); // DisplayDims
        ro_u8!(0, 7012); // HideConstructionGeometry
        ro_rgba!(140, 165, 255, 255, 4001); // SkyColor
        ro_rgba!(0, 0, 0, 0, 4007); // HorizonColor
        ro_rgba!(63, 147, 63, 255, 4003); // GroundColor
        ro_u8!(0, 4002); // DrawHorizon
        ro_u8!(0, 4004); // DrawGround
        ro_u8!(1, 4006); // DrawUnderground
        ro_u32!(50, 4005); // GroundTransparency: 50
        ro_rgba!(102, 110, 141, 255, 7003); // SectionActiveColor
        ro_rgba!(192, 192, 192, 255, 7004); // SectionInactiveColor
        ro_rgba!(255, 0, 0, 255, 7005); // SectionDefaultCutColor
        ro_u32!(4, 7014); // SectionCutWidth: 4
        ro_u32!(3); // SectionDisplayMode: planes (1) | cuts (2)
        ro_u32!(0, 2006); // TransparencySort: faster (0)
        ro_u8!(1); // edge halo flag (style item 1016)
        self.f64(0.34906585039886595); // 20 deg in radians (crease / halo angle)
        ro_u8!(1); // ShowViewName: true
        ro_rgba!(255, 0, 0, 255, 7001); // LockedColor
        ro_u8!(1, 5000); // DisplayWatermarks
        self.f64(0.65); // transparency factor (style item 2008)
        ro_u8!(0, 1015); // DrawBackEdges
        ro_u8!(1); // Match Photo background image (item 8000)
        self.f64(1.0); // Match Photo background opacity (item 8001)
        ro_u8!(1); // Match Photo foreground photo (item 8002)
        self.f64(0.8); // Match Photo foreground opacity (item 8003)

        let ro_len = self.buf.len() - ro_start;
        fields.sort_by_key(|&(id, _, _)| id);
        (ro_start, ro_len, fields)
    }

    fn write_component_and_material_manager(&mut self) -> (usize, usize, i32) {
        self.u32(0); // post-rendering-options word 0
        self.u32(1); // post-rendering-options word 1
        self.entity_preamble(0); // CSketchUpModel CEntity preamble (null model attributes)
        self.zeros(3); // CComponent base header pad (offsets 0..2)
        self.u8(1); // CComponent base flag 0
        self.u8(1); // CComponent base flag 1
        self.entity_preamble(0); // CMaterialManager CEntity preamble
        self.zeros(5); // CMaterialManager header pad
        let material_count_pos = self.buf.len();
        self.u32(0); // material_count = 0
        let material_insert_pos = self.buf.len();
        let material_base_slot = self.next_slot;
        (material_count_pos, material_insert_pos, material_base_slot)
    }

    fn write_layer_manager(
        &mut self,
    ) -> (usize, usize, usize, i32, usize, HashMap<&'static str, i32>) {
        self.null_ref(); // active material ref (null = default unpainted)
        self.entity_preamble(0); // CLayerManager CEntity preamble
        let layer_count_pos = self.buf.len();
        let orig_layer_count = 1usize;
        self.u32(orig_layer_count as u32);

        // Default untagged layer "Layer0" (schema 3, layer pid=4, material pid=5)
        let layer0_slot = self.new_object("CLayer", 3);
        let middle_class_slot = HashMap::from([("CLayer", self.class_slot["CLayer"])]);
        self.entity_preamble(LAYER0_PID);
        self.str("Layer0");
        self.u8(0); // hidden = False
        self.u16(0); // layer flags = 0
        self.encode_pid(LAYER0_MATERIAL_PID);
        self.str("Layer_Layer0");
        self.u16(0x0100); // embedded layer-colour material flag (256)
        self.rgba(255, 84, 84, 255); // default Layer0 tag colour
        self.str(""); // empty texture path
        self.u32(0); // colorize mode = 0
        self.u32(0); // colorize flags = 0
        self.f64(0.5); // default layer material alpha/transparency
        self.u8(0); // use_opacity = False
        self.u32(0); // layer trailer word = 0

        let layer_insert_pos = self.buf.len();
        let layer_writer_base_slot = self.next_slot;
        let active_layer_ref_pos = self.buf.len();
        self.backref(layer0_slot); // active layer back-ref -> Layer0
        (
            layer_count_pos,
            orig_layer_count,
            layer_insert_pos,
            layer_writer_base_slot,
            active_layer_ref_pos,
            middle_class_slot,
        )
    }

    fn write_definition_and_root_lists(&mut self) -> (usize, usize, usize, usize, usize, i32) {
        let def_count_pos = self.buf.len();
        let orig_def_count = 0usize;
        self.u32(orig_def_count as u32);

        let root_count_pos = self.buf.len();
        let orig_root_count = 0usize;
        self.u32(orig_root_count as u32);

        let tail_pos = self.buf.len();
        let scaffold_next_slot = self.next_slot;
        (
            def_count_pos,
            orig_def_count,
            root_count_pos,
            orig_root_count,
            tail_pos,
            scaffold_next_slot,
        )
    }

    fn write_shadow_info(&mut self, tail_pos: usize) -> (usize, usize, usize) {
        self.u32(0); // root CRelationship list count = 0
        self.u16(0); // root component closing word = 0
        self.entity_preamble(0); // CShadowInfo CEntity preamble

        let shadow_start = self.buf.len();
        self.u32(DEFAULT_SHADOW_TIME_UNIX);
        self.u8(0); // DaylightSavings = False
        self.str(DEFAULT_CITY);
        self.str(DEFAULT_COUNTRY);
        self.f64(DEFAULT_LONGITUDE);
        self.f64(DEFAULT_LATITUDE);
        self.f64(DEFAULT_TZ_OFFSET);
        self.f64(0.0); // NorthAngle (degrees / sin = 0.0)
        self.f64(1.0); // North direction Y = cos(0) = 1.0
        self.f64(0.0); // North direction Z = 0.0
        self.u8(0); // DisplayNorth = False
        self.u8(1); // DisplayOnAllFaces = True
        self.u8(1); // DisplayOnGroundPlane = True
        self.u8(0); // EdgesCastShadows = False
        self.u32(DEFAULT_SHADOW_LIGHT);
        self.u32(DEFAULT_SHADOW_DARK);
        let shadow_len = self.buf.len() - shadow_start;

        let page_list_tail_rel = self.buf.len() - tail_pos;
        self.u8(0); // UseSunForAllShading = False
        (shadow_start - tail_pos, shadow_len, page_list_tail_rel)
    }

    fn write_page_list(&mut self) {
        self.entity_preamble(0); // CPageList CEntity preamble
        self.u32(0); // page_count = 0
        self.null_ref(); // selected_page_ref = null
    }

    fn write_model_axes(&mut self) {
        self.zeros(3); // page-list closing bytes (0)
        self.entity_preamble(0); // CSketchCS CEntity preamble
        self.u8(1); // axis flag 0
        self.u8(1); // axis flag 1
        self.zeros(5); // axis header pad
        self.f64(0.0); // origin.x
        self.f64(0.0); // origin.y
        self.f64(0.0); // origin.z
        self.f64(1.0); // x_axis.x
        self.f64(0.0); // x_axis.y
        self.f64(0.0); // x_axis.z
        self.f64(0.0); // y_axis.x
        self.f64(1.0); // y_axis.y
        self.f64(0.0); // y_axis.z
        self.f64(0.0); // z_axis.x
        self.f64(0.0); // z_axis.y
        self.f64(1.0); // z_axis.z
        self.zeros(68); // empty post-axes model tables before CDimensionStyle
    }

    fn write_skfont(&mut self, face_name: &str, point_size: u32, pid: u64) -> i32 {
        let slot = self.new_object("CSkFont", 1);
        self.entity_preamble(pid);
        self.str(face_name);
        self.u16(0); // bold/italic flags = 0
        self.u32(point_size);
        self.u8(0); // use_world_size = False
        self.f64(1.0); // world_size = 1.0 inch
        slot
    }

    fn write_dimension_style(&mut self) -> i32 {
        self.entity_preamble(0); // CDimensionStyle CEntity preamble
        let arial_slot = self.write_skfont("Arial", 12, DIM_STYLE_FONT_PID);
        self.u8(0); // has_3d = False
        self.u8(1); // dimension text visibility / placement flag
        self.u32(5); // extension_line_offset = 5
        self.u32(10); // extension_line_overshoot = 10
        self.u32(1); // line_weight = 1
        self.u32(3); // arrow_type = 3 (closed arrow)
        self.u32(10); // arrow_size = 10
        self.u8(0); // highlight_non_associative = False
        self.rgba(255, 0, 0, 255); // highlight_non_associative_color = red
        self.u8(1); // show_radial_prefix = True
        self.u8(0); // hide_out_of_plane = False
        self.f64(0.6); // hide_out_of_plane_value = 0.6
        self.u8(0); // hide_small = False
        self.f64(10.0); // hide_small_value = 10.0
        self.rgba(64, 64, 64, 255); // dimension colour = dark grey
        self.u8(0); // use_text_color = False
        self.zeros(3); // text alignment / flags = 0
        self.u8(1); // show_units = True
        self.zeros(3); // dimension-style trailer pad
        arial_slot
    }

    fn write_text_style_and_font_manager(
        &mut self,
        tail_pos: usize,
        arial_slot: i32,
    ) -> Vec<usize> {
        let mut font_refs = Vec::with_capacity(4);
        self.entity_preamble(0); // CTextStyle CEntity preamble

        // Inline CSkFont("Tahoma"): its short class-ref (0x8000|11) shifts when slots grow.
        font_refs.push(self.buf.len() - tail_pos);
        let tahoma_slot = self.write_skfont("Tahoma", 12, TEXT_STYLE_FONT_PID);
        self.u32(2); // leader_type = 2 (pushpin)
        self.u32(1); // display_leader = 1
        self.u8(0); // text-style flag = 0
        self.u32(3); // arrow_type = 3 (closed arrow)
        self.u8(1); // leader visibility flag = 1
        self.rgba(0, 0, 0, 255); // text colour = black
        self.rgba(0, 0, 0, 255); // leader colour = black
        font_refs.push(self.buf.len() - tail_pos);
        self.backref(tahoma_slot);

        // CFontManager: [preamble][count = 2][Arial ref][Tahoma ref][closing u16 = 0]
        self.entity_preamble(0);
        self.u32(2);
        font_refs.push(self.buf.len() - tail_pos);
        self.backref(arial_slot);
        font_refs.push(self.buf.len() - tail_pos);
        self.backref(tahoma_slot);
        self.u16(0);
        font_refs
    }

    fn write_style_properties(&mut self, properties: &[(u32, Option<u32>, f64)]) -> usize {
        self.u32(properties.len() as u32);
        let items_start = self.buf.len();
        for &(key_id, kind, value) in properties {
            self.u32(key_id);
            let Some(kind) = kind else {
                self.u32(0);
                continue;
            };
            self.u32(1);
            self.u32(kind);
            match kind {
                STYLE_BOOL => self.u8(u8::from(value != 0.0)),
                STYLE_UINT32 => self.u32(value as i64 as u32),
                STYLE_FLOAT32 => self.f32(value as f32),
                STYLE_FLOAT64 => self.f64(value),
                _ => panic!("unsupported style property kind {kind}"),
            }
        }
        items_start
    }

    fn write_skp_style(
        &mut self,
        name: &str,
        description: &str,
        guid: &str,
        pid: u64,
    ) -> (i32, usize, usize) {
        let slot = self.new_object("CSkpStyle", 2);
        self.entity_preamble(pid);
        let guid_offset = self.buf.len();
        self.uuid(guid);
        self.str(""); // source .style path
        self.u32(3); // style format version = 3
        self.str(name);
        self.str(description);
        let items_offset = self.write_style_properties(&DEFAULT_STYLE_PROPERTIES);
        (slot, guid_offset, items_offset)
    }

    fn write_style_manager(
        &mut self,
        tail_pos: usize,
    ) -> (usize, [usize; 2], usize, usize, usize, usize, usize) {
        self.entity_preamble(0); // CSkpStyleManager CEntity preamble
        self.u32(1); // saved style count = 1

        let style_object_tail_rel = self.buf.len() - tail_pos;
        let (saved_slot, saved_guid_off, saved_items_off) =
            self.write_skp_style("Style", "", DEFAULT_SAVED_STYLE_GUID, SAVED_STYLE_PID);

        let selected_ref_tail_rel = self.buf.len() - tail_pos;
        self.backref(saved_slot); // selected saved style back-ref

        let working_ref_tail_rel = self.buf.len() - tail_pos;
        let (_, working_guid_off, working_items_off) =
            self.write_skp_style("Style", "", DEFAULT_WORKING_STYLE_GUID, 0);

        self.u8(0); // active_style_changed = False
        (
            style_object_tail_rel,
            [selected_ref_tail_rel, working_ref_tail_rel],
            saved_guid_off - tail_pos,
            working_guid_off - tail_pos,
            saved_items_off - tail_pos,
            working_items_off - tail_pos,
            DEFAULT_STYLE_PROPERTIES.len(),
        )
    }

    fn write_watermark_manager_and_trailer(&mut self) {
        self.entity_preamble(0); // CWatermarkManager CEntity preamble
        self.u32(0); // watermark_count = 0
        self.u32(0); // watermark flags / selection = 0
        self.u8(1); // display_watermarks = True
        self.u32(0); // closing model trailer = 0
    }
}

/// Build the empty classic SketchUp 2017 (v17) document and return its serialized bytes alongside
/// every splice offset and slot index.
pub fn build_blank_v17() -> BlankDocument {
    let mut b = BlankArchiveBuilder::new();
    let model_guid_offset = b.write_file_header();
    b.write_version_map();
    let (pid_counter_offset, no_scale_mask_offset) = b.write_model_preamble();
    let unit_option_offsets = b.write_options_providers();
    b.write_model_properties();
    let (camera_body_offset, camera_body_len) = b.write_model_camera();
    let (ro_offset, ro_len, ro_fields) = b.write_rendering_options();
    let (mat_count_pos, mat_insert_pos, mat_base_slot) = b.write_component_and_material_manager();
    let (
        layer_count_pos,
        orig_layer_count,
        layer_insert_pos,
        layer_writer_base_slot,
        active_layer_ref_pos,
        scaffold_class_slot,
    ) = b.write_layer_manager();
    let (
        def_count_pos,
        orig_def_count,
        root_count_pos,
        orig_root_count,
        tail_pos,
        scaffold_next_slot,
    ) = b.write_definition_and_root_lists();
    let (shadow_tail_rel, shadow_len, page_list_tail_rel) = b.write_shadow_info(tail_pos);
    b.write_page_list();
    b.write_model_axes();
    let arial_slot = b.write_dimension_style();
    let font_tail_refs = b.write_text_style_and_font_manager(tail_pos, arial_slot);
    let (
        style_object_tail_rel,
        style_tail_refs,
        saved_guid_tail_rel,
        working_guid_tail_rel,
        saved_items_tail_rel,
        working_items_tail_rel,
        style_item_count,
    ) = b.write_style_manager(tail_pos);
    b.write_watermark_manager_and_trailer();

    let mut tail_ref_positions = font_tail_refs.clone();
    tail_ref_positions.extend_from_slice(&style_tail_refs);

    BlankDocument {
        data: b.buf,
        model_guid_offset,
        pid_counter_offset,
        next_pid: INITIAL_NEXT_PERSISTENT_ID,
        no_scale_mask_offset,
        unit_option_offsets,
        attr_container_slot: b.class_slot["CAttributeContainer"],
        attribute_named_slot: b.class_slot["CAttributeNamed"],
        ccamera_slot: b.class_slot["CCamera"],
        camera_body_offset,
        camera_body_len,
        rendering_options_offset: ro_offset,
        rendering_options_len: ro_len,
        rendering_option_fields: ro_fields,
        material_count_pos: mat_count_pos,
        material_insert_pos: mat_insert_pos,
        material_base_slot: mat_base_slot,
        layer_count_pos,
        orig_layer_count,
        layer_insert_pos,
        layer_writer_base_slot,
        active_layer_ref_pos,
        def_count_pos,
        orig_def_count,
        root_count_pos,
        orig_root_count,
        tail_pos,
        scaffold_next_slot,
        scaffold_class_slot,
        shadow_info_tail_rel: shadow_tail_rel,
        shadow_info_len: shadow_len,
        page_list_tail_rel,
        font_tail_refs,
        style_object_tail_rel,
        style_tail_refs,
        tail_ref_positions,
        saved_style_guid_tail_rel: saved_guid_tail_rel,
        working_style_guid_tail_rel: working_guid_tail_rel,
        saved_style_items_tail_rel: saved_items_tail_rel,
        working_style_items_tail_rel: working_items_tail_rel,
        style_item_count,
    }
}
