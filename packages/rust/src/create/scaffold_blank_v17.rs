//! The blank-document scaffold every new file is spliced into, and the fixed positions inside it
//! the writer patches.
//!
//! `scaffold_blank_v17.skp` is byte-for-byte the SDK-authored empty v17 document every OpenSKP
//! writer port bundles (`packages/python/src/openskp/_scaffold/blank_v17.skp`, sha256
//! `809a1ab73a20a192ab13aaff197afb1c67d0e9352f6a353a9cd8030919f8a6c3`; also
//! `tests/fixtures/blank_v17.skp`). See the [`crate::create`] module docs for what it is and why
//! it is there.
//!
//! The Python writer computes the offsets below at runtime by walking the scaffold with the
//! generic MFC archive reader; since the scaffold is a fixed blob, the C++ port (and this one)
//! bake them in instead. They were produced against the exact bundled file with:
//!
//! ```text
//! import openskp, sys
//! b = sys.modules['openskp.create'].SkpBuilder()
//! # then print each of: b._material_insert_pos, b._base, b._layer_count_pos,
//! # b._orig_layer_count, b._layer_insert_pos, b._layer_writer_base, b._def_count_pos,
//! # b._orig_def_count, b._root_count_pos, b._orig_root_count, b._tail_pos,
//! # b._scaffold_next_slot, b._scaffold_class_slot
//! ```
//!
//! If the scaffold is ever swapped, re-run that recipe and update the file AND every constant
//! here together.

/// The bundled blank v17 document.
pub(super) static SCAFFOLD_BLANK_V17: &[u8; 5796] = include_bytes!("scaffold_blank_v17.skp");

/// Absolute offset of the material-manager insertion point (right before the "layer list
/// marker" a zero-material scaffold starts with) - Python's `_material_insert_pos`.
pub(super) const MATERIAL_INSERT_POS: usize = 3400;
/// Starting archive slot for anything spliced in after the (empty) material section - `_base`.
pub(super) const BASE: i32 = 9;
/// Absolute offset of the u32 layer-count field that precedes the layer list.
pub(super) const LAYER_COUNT_POS: usize = 3405;
pub(super) const ORIG_LAYER_COUNT: i32 = 1;
/// Absolute offset right after the scaffold's own layer records (Layer0) - where new layers go.
pub(super) const LAYER_INSERT_POS: usize = 3505;
/// Starting archive slot for a fresh layer writer, before any material shift.
pub(super) const LAYER_WRITER_BASE: i32 = 11;
/// Absolute offset of the u32 component-definition-count field.
pub(super) const DEF_COUNT_POS: usize = 3507;
pub(super) const ORIG_DEF_COUNT: i32 = 0;
/// Absolute offset of the u32 root-entity-count field.
pub(super) const ROOT_COUNT_POS: usize = 3511;
pub(super) const ORIG_ROOT_COUNT: i32 = 0;
/// Absolute offset where the document "tail" (the undecoded style/font-manager region) begins.
pub(super) const TAIL_POS: usize = 3515;
/// Starting slot for anything written after the byte-for-byte-copied layer/definition/root
/// region - `_scaffold_next_slot`.
pub(super) const SCAFFOLD_NEXT_SLOT: i32 = 11;

/// Offset of the u64 next persistent entity id. Ids are document-wide, not per archive section;
/// the scaffold has already consumed the ids below [`FIRST_NEW_PID`].
pub(super) const PID_COUNTER_POS: usize = 1987;

/// The first persistent id free for new objects in the scaffold.
pub(super) const FIRST_NEW_PID: u64 = 18;

/// Offsets, relative to the tail start, of internal slot references that must be renumbered by
/// the number of archive slots inserted before them.
pub(super) const TAIL_REF_POSITIONS: [usize; 6] = [409, 468, 477, 479, 1383, 1385];

/// Offset (relative to the layer insertion point) of the active-layer anchor - a back-reference
/// to Layer0. Moves only when materials shift Layer0's own slot.
pub(super) const ACTIVE_LAYER_ANCHOR_REL: usize = 0;

/// Classes the scaffold's own prefix already declares, so the writer only ever short-refs them.
pub(super) const CCAMERA_SLOT: i32 = 7;
pub(super) const ATTR_CONTAINER_SLOT: i32 = 3;
pub(super) const ATTRIBUTE_NAMED_SLOT: i32 = 5;

/// The scaffold ships SketchUp's arbitrary default camera; every written file is patched to the
/// standard "Iso" view instead (parallel projection, looking at the origin from the (1, -1, 1)
/// octant). Found by diffing two SDK-authored blank documents that differ only in an explicit
/// `SUCameraSetOrientation` + `SUCameraSetPerspective(false)` - copied verbatim, not decoded.
pub(super) const ISO_CAMERA_PREFIX_OFFSET: usize = 2993;
pub(super) const ISO_CAMERA_PREFIX_PATCH: [u8; 98] = [
    89, 64, 0, 0, 0, 0, 0, 0, 89, 192, 0, 0, 0, 0, 0, 0, 89, 64, 0, 0, //
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, //
    0, 0, 63, 44, 12, 112, 189, 32, 218, 191, 63, 44, 12, 112, 189, 32, 218, 63, 63, 44, //
    12, 112, 189, 32, 234, 63, 0, 0, 0, 0, 0, 0, 240, 63, 0, 0, 0, 0, 0, 64, //
    143, 64, 0, 0, 0, 0, 0, 0, 0, 62, 64, 42, 223, 39, 44, 128, 52, 87,
];

/// Iso-camera patches inside the tail, as (offset relative to the tail start, bytes).
pub(super) const ISO_CAMERA_TAIL_PATCHES: [(usize, &[u8]); 2] = [
    (
        509,
        &[
            208, 168, 105, 97, 60, 68, 45, 71, 153, 164, 102, 125, 26, 223, 168, 54,
        ],
    ),
    (
        1390,
        &[
            78, 83, 200, 68, 119, 2, 146, 70, 187, 169, 88, 39, 187, 167, 226,
        ],
    ),
];
