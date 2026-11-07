//! Hand-written bindings for the parts of ThatOpen's Fragments FlatBuffers
//! schema (`index.fbs`, vendored in the C++ package under
//! `include/openskp/_fragments_fb/`) that the exporter and reader use - the
//! counterpart of the flatc-generated `index_generated.h`.
//!
//! Struct layouts, vtable slots, and the order in which the `create_*`
//! functions write their vectors and add their fields follow flatc's C++
//! output (`CreateX` / `CreateXDirect`), so the same calls lay out the
//! same bytes.

use crate::flatbuffers::{
    Builder, FbStruct, Follow, Inline, Offset, ReadResult, Scalar, Str, Table, Vector,
};

/// The file identifier every Fragments buffer carries (`file_identifier
/// "0001"`).
pub const MODEL_IDENTIFIER: &[u8; 4] = b"0001";

macro_rules! byte_enum {
    ($(#[$doc:meta])* $name:ident { $($(#[$vdoc:meta])* $variant:ident = $value:expr),* $(,)? }) => {
        $(#[$doc])*
        #[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default)]
        pub struct $name(pub i8);

        impl $name {
            $($(#[$vdoc])* pub const $variant: Self = Self($value);)*
        }
    };
}

byte_enum!(
    /// `enum RenderedFaces:byte`.
    RenderedFaces {
        /// One rendered face.
        ONE = 0,
        /// Two rendered faces.
        TWO = 1,
    }
);

byte_enum!(
    /// `enum Stroke:byte`.
    Stroke {
        /// Default line type.
        DEFAULT = 0,
    }
);

byte_enum!(
    /// `enum RepresentationClass:byte`.
    RepresentationClass {
        /// No representation class.
        NONE = 0,
        /// Shell (brep).
        SHELL = 1,
        /// Circle extrusion (reinforcement bars, pipes).
        CIRCLE_EXTRUSION = 2,
    }
);

byte_enum!(
    /// `enum ShellType:byte`.
    ShellType {
        /// Fewer than 65536 points (`ushort` indices).
        NONE = 0,
        /// More points (`uint` indices).
        BIG = 1,
    }
);

// ---------------------------------------------------------------------------
// Structs
// ---------------------------------------------------------------------------

fn put<T: Scalar>(out: &mut [u8], at: usize, v: T) {
    v.write_le(&mut out[at..at + T::SIZE]);
}

fn get<T: Scalar>(b: &[u8], at: usize) -> T {
    T::read_le(&b[at..at + T::SIZE])
}

/// `struct FloatVector` (12 bytes, align 4).
#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct FloatVector {
    pub x: f32,
    pub y: f32,
    pub z: f32,
}

impl FloatVector {
    pub const fn new(x: f32, y: f32, z: f32) -> Self {
        Self { x, y, z }
    }
}

impl FbStruct for FloatVector {
    const SIZE: usize = 12;
    const ALIGN: usize = 4;
    fn write(&self, out: &mut [u8]) {
        put(out, 0, self.x);
        put(out, 4, self.y);
        put(out, 8, self.z);
    }
    fn read(b: &[u8]) -> Self {
        Self {
            x: get(b, 0),
            y: get(b, 4),
            z: get(b, 8),
        }
    }
}

/// `struct DoubleVector` (24 bytes, align 8).
#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct DoubleVector {
    pub x: f64,
    pub y: f64,
    pub z: f64,
}

impl DoubleVector {
    pub const fn new(x: f64, y: f64, z: f64) -> Self {
        Self { x, y, z }
    }
}

impl FbStruct for DoubleVector {
    const SIZE: usize = 24;
    const ALIGN: usize = 8;
    fn write(&self, out: &mut [u8]) {
        put(out, 0, self.x);
        put(out, 8, self.y);
        put(out, 16, self.z);
    }
    fn read(b: &[u8]) -> Self {
        Self {
            x: get(b, 0),
            y: get(b, 8),
            z: get(b, 16),
        }
    }
}

/// `struct Transform` (48 bytes, align 8): a position plus the X and Y axis
/// directions (Z is implied, `x cross y`).
#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct Transform {
    pub position: DoubleVector,
    pub x_direction: FloatVector,
    pub y_direction: FloatVector,
}

impl Transform {
    /// The identity transform: origin, X = (1,0,0), Y = (0,1,0).
    pub const IDENTITY: Transform = Transform {
        position: DoubleVector::new(0.0, 0.0, 0.0),
        x_direction: FloatVector::new(1.0, 0.0, 0.0),
        y_direction: FloatVector::new(0.0, 1.0, 0.0),
    };
}

impl FbStruct for Transform {
    const SIZE: usize = 48;
    const ALIGN: usize = 8;
    fn write(&self, out: &mut [u8]) {
        self.position.write(&mut out[0..24]);
        self.x_direction.write(&mut out[24..36]);
        self.y_direction.write(&mut out[36..48]);
    }
    fn read(b: &[u8]) -> Self {
        Self {
            position: DoubleVector::read(&b[0..24]),
            x_direction: FloatVector::read(&b[24..36]),
            y_direction: FloatVector::read(&b[36..48]),
        }
    }
}

/// `struct Material` (6 bytes, align 1): a flat RGBA color.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Material {
    pub r: u8,
    pub g: u8,
    pub b: u8,
    pub a: u8,
    pub rendered_faces: RenderedFaces,
    pub stroke: Stroke,
}

impl FbStruct for Material {
    const SIZE: usize = 6;
    const ALIGN: usize = 1;
    fn write(&self, out: &mut [u8]) {
        out[0] = self.r;
        out[1] = self.g;
        out[2] = self.b;
        out[3] = self.a;
        put(out, 4, self.rendered_faces.0);
        put(out, 5, self.stroke.0);
    }
    fn read(b: &[u8]) -> Self {
        Self {
            r: b[0],
            g: b[1],
            b: b[2],
            a: b[3],
            rendered_faces: RenderedFaces(get(b, 4)),
            stroke: Stroke(get(b, 5)),
        }
    }
}

/// `struct BoundingBox` (24 bytes, align 4).
#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct BoundingBox {
    pub min: FloatVector,
    pub max: FloatVector,
}

impl FbStruct for BoundingBox {
    const SIZE: usize = 24;
    const ALIGN: usize = 4;
    fn write(&self, out: &mut [u8]) {
        self.min.write(&mut out[0..12]);
        self.max.write(&mut out[12..24]);
    }
    fn read(b: &[u8]) -> Self {
        Self {
            min: FloatVector::read(&b[0..12]),
            max: FloatVector::read(&b[12..24]),
        }
    }
}

/// `struct Representation` (32 bytes, align 4; 3 trailing padding bytes).
#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct Representation {
    /// Index into the array of `representation_class` (`Meshes::shells`
    /// for a SHELL) - not this representation's own position.
    pub id: u32,
    pub bbox: BoundingBox,
    pub representation_class: RepresentationClass,
}

impl FbStruct for Representation {
    const SIZE: usize = 32;
    const ALIGN: usize = 4;
    fn write(&self, out: &mut [u8]) {
        put(out, 0, self.id);
        self.bbox.write(&mut out[4..28]);
        put(out, 28, self.representation_class.0);
        out[29..32].fill(0);
    }
    fn read(b: &[u8]) -> Self {
        Self {
            id: get(b, 0),
            bbox: BoundingBox::read(&b[4..28]),
            representation_class: RepresentationClass(get(b, 28)),
        }
    }
}

/// `struct Sample` (16 bytes, align 4): one placed geometry.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Sample {
    pub item: u32,
    pub material: u32,
    pub representation: u32,
    pub local_transform: u32,
}

impl FbStruct for Sample {
    const SIZE: usize = 16;
    const ALIGN: usize = 4;
    fn write(&self, out: &mut [u8]) {
        put(out, 0, self.item);
        put(out, 4, self.material);
        put(out, 8, self.representation);
        put(out, 12, self.local_transform);
    }
    fn read(b: &[u8]) -> Self {
        Self {
            item: get(b, 0),
            material: get(b, 4),
            representation: get(b, 8),
            local_transform: get(b, 12),
        }
    }
}

// ---------------------------------------------------------------------------
// Tables: writers (flatc's CreateX / CreateXDirect)
// ---------------------------------------------------------------------------

/// `CreateShellProfileDirect`: the `ushort` index vector, then the table.
pub fn create_shell_profile_direct(fbb: &mut Builder, indices: &[u16]) -> Offset {
    let indices = fbb.create_vector(indices);
    let start = fbb.start_table();
    fbb.add_offset(ShellProfile::VT_INDICES, indices);
    fbb.end_table(start)
}

/// `CreateBigShellProfileDirect`: the `uint` index vector, then the table.
pub fn create_big_shell_profile_direct(fbb: &mut Builder, indices: &[u32]) -> Offset {
    let indices = fbb.create_vector(indices);
    let start = fbb.start_table();
    fbb.add_offset(BigShellProfile::VT_INDICES, indices);
    fbb.end_table(start)
}

/// Arguments of [`create_shell_direct`] (every vector is written, even
/// when empty - all of them are `required`).
#[derive(Debug, Clone, Copy)]
pub struct ShellArgs<'x> {
    pub profiles: &'x [Offset],
    pub holes: &'x [Offset],
    pub points: &'x [FloatVector],
    pub big_profiles: &'x [Offset],
    pub big_holes: &'x [Offset],
    pub shell_type: ShellType,
    pub profiles_face_ids: &'x [u16],
}

/// `CreateShellDirect`.
pub fn create_shell_direct(fbb: &mut Builder, args: &ShellArgs<'_>) -> Offset {
    let profiles = fbb.create_vector_of_offsets(args.profiles);
    let holes = fbb.create_vector_of_offsets(args.holes);
    let points = fbb.create_vector_of_structs(args.points);
    let big_profiles = fbb.create_vector_of_offsets(args.big_profiles);
    let big_holes = fbb.create_vector_of_offsets(args.big_holes);
    let face_ids = fbb.create_vector(args.profiles_face_ids);
    let start = fbb.start_table();
    fbb.add_offset(Shell::VT_PROFILES_FACE_IDS, face_ids);
    fbb.add_offset(Shell::VT_BIG_HOLES, big_holes);
    fbb.add_offset(Shell::VT_BIG_PROFILES, big_profiles);
    fbb.add_offset(Shell::VT_POINTS, points);
    fbb.add_offset(Shell::VT_HOLES, holes);
    fbb.add_offset(Shell::VT_PROFILES, profiles);
    fbb.add_scalar(Shell::VT_TYPE, args.shell_type.0, 0);
    fbb.end_table(start)
}

/// Arguments of [`create_meshes`]; unset offsets are [`Offset::NULL`].
#[derive(Debug, Clone, Copy, Default)]
pub struct MeshesArgs {
    pub coordinates: Option<Transform>,
    pub meshes_items: Offset,
    pub samples: Offset,
    pub representations: Offset,
    pub materials: Offset,
    pub circle_extrusions: Offset,
    pub shells: Offset,
    pub local_transforms: Offset,
    pub global_transforms: Offset,
    pub material_ids: Offset,
    pub representation_ids: Offset,
    pub sample_ids: Offset,
    pub local_transform_ids: Offset,
    pub global_transform_ids: Offset,
}

/// `CreateMeshes`.
pub fn create_meshes(fbb: &mut Builder, args: &MeshesArgs) -> Offset {
    let start = fbb.start_table();
    fbb.add_offset(Meshes::VT_GLOBAL_TRANSFORM_IDS, args.global_transform_ids);
    fbb.add_offset(Meshes::VT_LOCAL_TRANSFORM_IDS, args.local_transform_ids);
    fbb.add_offset(Meshes::VT_SAMPLE_IDS, args.sample_ids);
    fbb.add_offset(Meshes::VT_REPRESENTATION_IDS, args.representation_ids);
    fbb.add_offset(Meshes::VT_MATERIAL_IDS, args.material_ids);
    fbb.add_offset(Meshes::VT_GLOBAL_TRANSFORMS, args.global_transforms);
    fbb.add_offset(Meshes::VT_LOCAL_TRANSFORMS, args.local_transforms);
    fbb.add_offset(Meshes::VT_SHELLS, args.shells);
    fbb.add_offset(Meshes::VT_CIRCLE_EXTRUSIONS, args.circle_extrusions);
    fbb.add_offset(Meshes::VT_MATERIALS, args.materials);
    fbb.add_offset(Meshes::VT_REPRESENTATIONS, args.representations);
    fbb.add_offset(Meshes::VT_SAMPLES, args.samples);
    fbb.add_offset(Meshes::VT_MESHES_ITEMS, args.meshes_items);
    if let Some(coordinates) = &args.coordinates {
        fbb.add_struct(Meshes::VT_COORDINATES, coordinates);
    }
    fbb.end_table(start)
}

/// `CreateAttributeDirect`: the string-offset vector, then the table.
pub fn create_attribute_direct(fbb: &mut Builder, data: &[Offset]) -> Offset {
    let data = fbb.create_vector_of_offsets(data);
    let start = fbb.start_table();
    fbb.add_offset(Attribute::VT_DATA, data);
    fbb.end_table(start)
}

/// `CreateSpatialStructure` (`local_id: uint = null`, so a present id is
/// stored even when it is 0).
pub fn create_spatial_structure(
    fbb: &mut Builder,
    local_id: Option<u32>,
    category: Offset,
    children: Offset,
) -> Offset {
    let start = fbb.start_table();
    fbb.add_offset(SpatialStructure::VT_CHILDREN, children);
    fbb.add_offset(SpatialStructure::VT_CATEGORY, category);
    if let Some(id) = local_id {
        fbb.add_scalar_always(SpatialStructure::VT_LOCAL_ID, id);
    }
    fbb.end_table(start)
}

/// Arguments of [`create_model`]; unset offsets are [`Offset::NULL`].
#[derive(Debug, Clone, Copy, Default)]
pub struct ModelArgs {
    pub metadata: Offset,
    pub guids: Offset,
    pub guids_items: Offset,
    pub max_local_id: u32,
    pub local_ids: Offset,
    pub categories: Offset,
    pub meshes: Offset,
    pub attributes: Offset,
    pub relations: Offset,
    pub relations_items: Offset,
    pub guid: Offset,
    pub spatial_structure: Offset,
    pub unique_attributes: Offset,
    pub relation_names: Offset,
    pub indexes: Offset,
}

/// `CreateModel`.
pub fn create_model(fbb: &mut Builder, args: &ModelArgs) -> Offset {
    let start = fbb.start_table();
    fbb.add_offset(Model::VT_INDEXES, args.indexes);
    fbb.add_offset(Model::VT_RELATION_NAMES, args.relation_names);
    fbb.add_offset(Model::VT_UNIQUE_ATTRIBUTES, args.unique_attributes);
    fbb.add_offset(Model::VT_SPATIAL_STRUCTURE, args.spatial_structure);
    fbb.add_offset(Model::VT_GUID, args.guid);
    fbb.add_offset(Model::VT_RELATIONS_ITEMS, args.relations_items);
    fbb.add_offset(Model::VT_RELATIONS, args.relations);
    fbb.add_offset(Model::VT_ATTRIBUTES, args.attributes);
    fbb.add_offset(Model::VT_MESHES, args.meshes);
    fbb.add_offset(Model::VT_CATEGORIES, args.categories);
    fbb.add_offset(Model::VT_LOCAL_IDS, args.local_ids);
    fbb.add_scalar(Model::VT_MAX_LOCAL_ID, args.max_local_id, 0);
    fbb.add_offset(Model::VT_GUIDS_ITEMS, args.guids_items);
    fbb.add_offset(Model::VT_GUIDS, args.guids);
    fbb.add_offset(Model::VT_METADATA, args.metadata);
    fbb.end_table(start)
}

/// `FinishModelBuffer`: finishes with the `"0001"` identifier.
pub fn finish_model_buffer(fbb: &mut Builder, root: Offset) {
    fbb.finish(root, Some(MODEL_IDENTIFIER));
}

// ---------------------------------------------------------------------------
// Tables: readers
// ---------------------------------------------------------------------------

macro_rules! table_reader {
    ($(#[$doc:meta])* $name:ident { $($vt:ident = $slot:expr),* $(,)? }) => {
        $(#[$doc])*
        #[derive(Debug, Clone, Copy)]
        pub struct $name<'a>(pub Table<'a>);

        impl $name<'_> {
            $(pub const $vt: u16 = $slot;)*
        }

        impl<'a> Follow<'a> for $name<'a> {
            const INLINE_SIZE: usize = <Table<'a> as Follow<'a>>::INLINE_SIZE;
            fn follow(buf: &'a [u8], pos: usize) -> ReadResult<Self> {
                Table::follow(buf, pos).map($name)
            }
        }
    };
}

table_reader!(
    /// `table ShellProfile`.
    ShellProfile { VT_INDICES = 4 }
);

impl<'a> ShellProfile<'a> {
    pub fn indices(&self) -> ReadResult<Option<Vector<'a, u16>>> {
        self.0.get(Self::VT_INDICES)
    }
}

table_reader!(
    /// `table BigShellProfile`.
    BigShellProfile { VT_INDICES = 4 }
);

impl<'a> BigShellProfile<'a> {
    pub fn indices(&self) -> ReadResult<Option<Vector<'a, u32>>> {
        self.0.get(Self::VT_INDICES)
    }
}

table_reader!(
    /// `table Shell`.
    Shell {
        VT_PROFILES = 4,
        VT_HOLES = 6,
        VT_POINTS = 8,
        VT_BIG_PROFILES = 10,
        VT_BIG_HOLES = 12,
        VT_TYPE = 14,
        VT_PROFILES_FACE_IDS = 16,
    }
);

impl<'a> Shell<'a> {
    pub fn profiles(&self) -> ReadResult<Option<Vector<'a, ShellProfile<'a>>>> {
        self.0.get(Self::VT_PROFILES)
    }
    pub fn points(&self) -> ReadResult<Option<Vector<'a, Inline<FloatVector>>>> {
        self.0.get(Self::VT_POINTS)
    }
    pub fn big_profiles(&self) -> ReadResult<Option<Vector<'a, BigShellProfile<'a>>>> {
        self.0.get(Self::VT_BIG_PROFILES)
    }
    pub fn shell_type(&self) -> ReadResult<ShellType> {
        self.0.get_or(Self::VT_TYPE, 0i8).map(ShellType)
    }
    pub fn profiles_face_ids(&self) -> ReadResult<Option<Vector<'a, u16>>> {
        self.0.get(Self::VT_PROFILES_FACE_IDS)
    }
}

table_reader!(
    /// `table Meshes`.
    Meshes {
        VT_COORDINATES = 4,
        VT_MESHES_ITEMS = 6,
        VT_SAMPLES = 8,
        VT_REPRESENTATIONS = 10,
        VT_MATERIALS = 12,
        VT_CIRCLE_EXTRUSIONS = 14,
        VT_SHELLS = 16,
        VT_LOCAL_TRANSFORMS = 18,
        VT_GLOBAL_TRANSFORMS = 20,
        VT_MATERIAL_IDS = 22,
        VT_REPRESENTATION_IDS = 24,
        VT_SAMPLE_IDS = 26,
        VT_LOCAL_TRANSFORM_IDS = 28,
        VT_GLOBAL_TRANSFORM_IDS = 30,
    }
);

impl<'a> Meshes<'a> {
    pub fn coordinates(&self) -> ReadResult<Option<Transform>> {
        Ok(self
            .0
            .get::<Inline<Transform>>(Self::VT_COORDINATES)?
            .map(|t| t.0))
    }
    pub fn meshes_items(&self) -> ReadResult<Option<Vector<'a, u32>>> {
        self.0.get(Self::VT_MESHES_ITEMS)
    }
    pub fn samples(&self) -> ReadResult<Option<Vector<'a, Inline<Sample>>>> {
        self.0.get(Self::VT_SAMPLES)
    }
    pub fn representations(&self) -> ReadResult<Option<Vector<'a, Inline<Representation>>>> {
        self.0.get(Self::VT_REPRESENTATIONS)
    }
    pub fn materials(&self) -> ReadResult<Option<Vector<'a, Inline<Material>>>> {
        self.0.get(Self::VT_MATERIALS)
    }
    pub fn shells(&self) -> ReadResult<Option<Vector<'a, Shell<'a>>>> {
        self.0.get(Self::VT_SHELLS)
    }
    pub fn local_transforms(&self) -> ReadResult<Option<Vector<'a, Inline<Transform>>>> {
        self.0.get(Self::VT_LOCAL_TRANSFORMS)
    }
    pub fn global_transforms(&self) -> ReadResult<Option<Vector<'a, Inline<Transform>>>> {
        self.0.get(Self::VT_GLOBAL_TRANSFORMS)
    }
}

table_reader!(
    /// `table Attribute`: an item's data as JSON-encoded strings.
    Attribute { VT_DATA = 4 }
);

impl<'a> Attribute<'a> {
    pub fn data(&self) -> ReadResult<Option<Vector<'a, Str<'a>>>> {
        self.0.get(Self::VT_DATA)
    }
}

table_reader!(
    /// `table SpatialStructure`.
    SpatialStructure { VT_LOCAL_ID = 4, VT_CATEGORY = 6, VT_CHILDREN = 8 }
);

impl<'a> SpatialStructure<'a> {
    pub fn local_id(&self) -> ReadResult<Option<u32>> {
        self.0.get(Self::VT_LOCAL_ID)
    }
    pub fn category(&self) -> ReadResult<Option<Str<'a>>> {
        self.0.get(Self::VT_CATEGORY)
    }
    pub fn children(&self) -> ReadResult<Option<Vector<'a, SpatialStructure<'a>>>> {
        self.0.get(Self::VT_CHILDREN)
    }
}

table_reader!(
    /// `table Model`, the root of every Fragments buffer.
    Model {
        VT_METADATA = 4,
        VT_GUIDS = 6,
        VT_GUIDS_ITEMS = 8,
        VT_MAX_LOCAL_ID = 10,
        VT_LOCAL_IDS = 12,
        VT_CATEGORIES = 14,
        VT_MESHES = 16,
        VT_ATTRIBUTES = 18,
        VT_RELATIONS = 20,
        VT_RELATIONS_ITEMS = 22,
        VT_GUID = 24,
        VT_SPATIAL_STRUCTURE = 26,
        VT_UNIQUE_ATTRIBUTES = 28,
        VT_RELATION_NAMES = 30,
        VT_INDEXES = 32,
    }
);

impl<'a> Model<'a> {
    pub fn metadata(&self) -> ReadResult<Option<Str<'a>>> {
        self.0.get(Self::VT_METADATA)
    }
    pub fn guids(&self) -> ReadResult<Option<Vector<'a, Str<'a>>>> {
        self.0.get(Self::VT_GUIDS)
    }
    pub fn guids_items(&self) -> ReadResult<Option<Vector<'a, u32>>> {
        self.0.get(Self::VT_GUIDS_ITEMS)
    }
    pub fn max_local_id(&self) -> ReadResult<u32> {
        self.0.get_or(Self::VT_MAX_LOCAL_ID, 0)
    }
    pub fn local_ids(&self) -> ReadResult<Option<Vector<'a, u32>>> {
        self.0.get(Self::VT_LOCAL_IDS)
    }
    pub fn categories(&self) -> ReadResult<Option<Vector<'a, Str<'a>>>> {
        self.0.get(Self::VT_CATEGORIES)
    }
    pub fn meshes(&self) -> ReadResult<Option<Meshes<'a>>> {
        self.0.get(Self::VT_MESHES)
    }
    pub fn attributes(&self) -> ReadResult<Option<Vector<'a, Attribute<'a>>>> {
        self.0.get(Self::VT_ATTRIBUTES)
    }
    pub fn guid(&self) -> ReadResult<Option<Str<'a>>> {
        self.0.get(Self::VT_GUID)
    }
    pub fn spatial_structure(&self) -> ReadResult<Option<SpatialStructure<'a>>> {
        self.0.get(Self::VT_SPATIAL_STRUCTURE)
    }
}

/// `GetModel`: the root [`Model`] of a raw (uncompressed) Fragments
/// buffer. Like the C++ accessor, the file identifier is not checked.
pub fn root_as_model(buf: &[u8]) -> ReadResult<Model<'_>> {
    Table::root(buf).map(Model)
}
