//! A minimal, dependency-free FlatBuffers builder and reader - just enough
//! of the format for the ThatOpen Fragments schema (`fragments::schema`):
//! tables, vtables, scalars, inline structs, vectors (of scalars, structs,
//! tables and strings) and strings.
//!
//! [`Builder`] follows the C++ `flatbuffers::FlatBufferBuilder` (v25)
//! algorithm: the buffer grows back to front, padding/alignment follow its
//! `Align`/`PreAlign` rules, vtables are deduplicated against every vtable
//! written so far, and default-valued scalars are not stored. Random call
//! sequences were checked byte for byte against the official `flatbuffers`
//! crate's builder (a port of the same algorithm).
//!
//! The read side ([`Table`], [`Vector`], [`Follow`]) bounds-checks every
//! access and reports malformed input as [`InvalidFlatbuffer`] instead of
//! reading out of range.

use std::fmt;
use std::marker::PhantomData;

/// Size of a vtable entry (`voffset_t`).
const SIZE_VOFFSET: usize = 2;
/// Size of an offset (`uoffset_t` / `soffset_t`).
const SIZE_UOFFSET: usize = 4;
/// Length of a buffer's file identifier.
pub const FILE_IDENTIFIER_LENGTH: usize = 4;
/// The largest buffer the format can address (2 GiB - 1).
const MAX_BUFFER_SIZE: usize = (1 << 31) - 1;

/// `((~buf_size) + 1) & (scalar_size - 1)`: bytes of padding that bring
/// `buf_size` up to a multiple of `scalar_size` (a power of two).
fn padding_bytes(buf_size: usize, scalar_size: usize) -> usize {
    (!buf_size).wrapping_add(1) & scalar_size.wrapping_sub(1)
}

// ---------------------------------------------------------------------------
// Scalars and structs
// ---------------------------------------------------------------------------

/// A little-endian scalar that can be stored in a FlatBuffer.
pub trait Scalar: Copy + PartialEq {
    /// Size in bytes (also the scalar's alignment).
    const SIZE: usize;
    /// Writes the little-endian bytes into `out` (`out.len() == SIZE`).
    fn write_le(self, out: &mut [u8]);
    /// Reads from `bytes` (`bytes.len() == SIZE`).
    fn read_le(bytes: &[u8]) -> Self;
}

macro_rules! impl_scalar {
    ($($t:ty),*) => {$(
        impl Scalar for $t {
            const SIZE: usize = std::mem::size_of::<$t>();
            fn write_le(self, out: &mut [u8]) {
                out.copy_from_slice(&self.to_le_bytes());
            }
            fn read_le(bytes: &[u8]) -> Self {
                let mut a = [0u8; std::mem::size_of::<$t>()];
                a.copy_from_slice(bytes);
                <$t>::from_le_bytes(a)
            }
        }
    )*};
}
impl_scalar!(u8, i8, u16, i16, u32, i32, u64, i64, f32, f64);

impl Scalar for bool {
    const SIZE: usize = 1;
    fn write_le(self, out: &mut [u8]) {
        out[0] = u8::from(self);
    }
    fn read_le(bytes: &[u8]) -> Self {
        bytes[0] != 0
    }
}

/// A fixed-layout FlatBuffers `struct` (stored inline, little-endian, with
/// any padding bytes zeroed).
pub trait FbStruct: Sized {
    /// Size in bytes, including trailing padding.
    const SIZE: usize;
    /// Alignment in bytes (the largest scalar member's size).
    const ALIGN: usize;
    /// Writes the struct's bytes into `out` (`out.len() == SIZE`, zeroed).
    fn write(&self, out: &mut [u8]);
    /// Reads the struct from `bytes` (`bytes.len() == SIZE`).
    fn read(bytes: &[u8]) -> Self;
}

// ---------------------------------------------------------------------------
// Builder
// ---------------------------------------------------------------------------

/// The position of a finished object (string, vector or table) inside a
/// [`Builder`], counted from the END of the buffer - FlatBuffers' own
/// `Offset<T>`. `Offset::NULL` stands for "not set": table fields given a
/// null offset are not stored.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default)]
pub struct Offset(pub u32);

impl Offset {
    /// The "absent" offset.
    pub const NULL: Offset = Offset(0);

    /// True for [`Offset::NULL`].
    pub fn is_null(self) -> bool {
        self.0 == 0
    }
}

/// One stored table field: where it lives (counted from the buffer's end)
/// and which vtable slot points at it.
#[derive(Debug, Clone, Copy)]
struct FieldLoc {
    off: u32,
    id: u16,
}

/// Back-to-front FlatBuffer builder - a port of the C++
/// `flatbuffers::FlatBufferBuilder` (`force_defaults = false`,
/// `dedup_vtables = true`).
#[derive(Debug)]
pub struct Builder {
    /// Data lives in `buf[head..]`; it grows towards index 0.
    buf: Vec<u8>,
    head: usize,
    min_align: usize,
    /// Every distinct vtable written so far, in creation order, as offsets
    /// from the buffer's end (the C++ builder's scratch vtable list).
    vtables: Vec<u32>,
    field_locs: Vec<FieldLoc>,
    max_voffset: u16,
    nested: bool,
    finished: bool,
}

impl Default for Builder {
    fn default() -> Self {
        Self::with_capacity(1024)
    }
}

impl Builder {
    /// A builder whose buffer starts with room for `capacity` bytes.
    pub fn with_capacity(capacity: usize) -> Self {
        let capacity = capacity.max(16);
        Self {
            buf: vec![0; capacity],
            head: capacity,
            min_align: 1,
            vtables: Vec::new(),
            field_locs: Vec::new(),
            max_voffset: 0,
            nested: false,
            finished: false,
        }
    }

    /// Bytes written so far.
    pub fn size(&self) -> usize {
        self.buf.len() - self.head
    }

    fn size_u32(&self) -> u32 {
        u32::try_from(self.size()).expect("FlatBuffer exceeds 2 GiB")
    }

    fn ensure_space(&mut self, want: usize) {
        if self.head >= want {
            return;
        }
        let used = self.size();
        let needed = used.checked_add(want).expect("FlatBuffer size overflow");
        assert!(
            needed <= MAX_BUFFER_SIZE,
            "cannot grow a FlatBuffer beyond 2 GiB"
        );
        let mut new_len = self.buf.len().max(16);
        while new_len < needed {
            new_len *= 2;
        }
        let mut grown = vec![0u8; new_len];
        grown[new_len - used..].copy_from_slice(&self.buf[self.head..]);
        self.buf = grown;
        self.head = new_len - used;
    }

    /// Reserves `n` zeroed bytes at the front and returns their index.
    fn make_space(&mut self, n: usize) -> usize {
        self.ensure_space(n);
        self.head -= n;
        self.buf[self.head..self.head + n].fill(0);
        self.head
    }

    fn fill(&mut self, n: usize) {
        if n > 0 {
            self.make_space(n);
        }
    }

    fn track_min_align(&mut self, alignment: usize) {
        self.min_align = self.min_align.max(alignment);
    }

    /// C++ `Align`: pads so the current size is a multiple of `elem_size`.
    fn align(&mut self, elem_size: usize) {
        self.track_min_align(elem_size);
        self.fill(padding_bytes(self.size(), elem_size));
    }

    /// C++ `PreAlign`: pads so that after `len` more bytes the size is a
    /// multiple of `alignment`. A no-op for `len == 0`.
    fn pre_align(&mut self, len: usize, alignment: usize) {
        if len == 0 {
            return;
        }
        self.track_min_align(alignment);
        self.fill(padding_bytes(self.size() + len, alignment));
    }

    fn push_bytes(&mut self, bytes: &[u8]) {
        let at = self.make_space(bytes.len());
        self.buf[at..at + bytes.len()].copy_from_slice(bytes);
    }

    /// C++ `PushElement<T>`: aligns, then writes one scalar. Returns the new
    /// size (the scalar's offset from the buffer end).
    fn push_scalar<T: Scalar>(&mut self, v: T) -> u32 {
        self.align(T::SIZE);
        let at = self.make_space(T::SIZE);
        v.write_le(&mut self.buf[at..at + T::SIZE]);
        self.size_u32()
    }

    /// C++ `ReferTo`: the uoffset that, written at the next aligned slot,
    /// points at `off`.
    fn refer_to(&mut self, off: Offset) -> u32 {
        self.align(SIZE_UOFFSET);
        let size = self.size_u32();
        debug_assert!(
            off.0 != 0 && off.0 <= size,
            "offset must refer to something already in the buffer"
        );
        size - off.0 + SIZE_UOFFSET as u32
    }

    fn push_offset(&mut self, off: Offset) -> u32 {
        let rel = self.refer_to(off);
        self.push_scalar(rel)
    }

    fn assert_not_nested(&self) {
        assert!(
            !self.nested,
            "FlatBuffers: object serialization must not be nested"
        );
        assert!(
            !self.finished,
            "FlatBuffers: the buffer is already finished"
        );
    }

    // ---- strings and vectors ----

    /// Writes a zero-terminated, length-prefixed string.
    pub fn create_string(&mut self, s: &str) -> Offset {
        self.create_byte_string(s.as_bytes())
    }

    /// [`Builder::create_string`] for raw bytes.
    pub fn create_byte_string(&mut self, bytes: &[u8]) -> Offset {
        self.assert_not_nested();
        self.pre_align(bytes.len() + 1, SIZE_UOFFSET);
        self.fill(1);
        self.push_bytes(bytes);
        Offset(self.push_scalar(bytes.len() as u32))
    }

    /// C++ `StartVector(len, elemsize, alignment)`.
    fn start_vector(&mut self, byte_len: usize, alignment: usize) {
        self.assert_not_nested();
        self.nested = true;
        self.pre_align(byte_len, SIZE_UOFFSET);
        self.pre_align(byte_len, alignment);
    }

    /// C++ `EndVector`: writes the element count.
    fn end_vector(&mut self, len: usize) -> Offset {
        assert!(self.nested, "FlatBuffers: end_vector without start_vector");
        self.nested = false;
        Offset(self.push_scalar(u32::try_from(len).expect("vector too long")))
    }

    /// A vector of scalars.
    pub fn create_vector<T: Scalar>(&mut self, items: &[T]) -> Offset {
        let byte_len = items.len() * T::SIZE;
        self.start_vector(byte_len, T::SIZE);
        if !items.is_empty() {
            let at = self.make_space(byte_len);
            for (item, out) in items
                .iter()
                .zip(self.buf[at..at + byte_len].chunks_exact_mut(T::SIZE))
            {
                item.write_le(out);
            }
        }
        self.end_vector(items.len())
    }

    /// A vector of inline structs.
    pub fn create_vector_of_structs<S: FbStruct>(&mut self, items: &[S]) -> Offset {
        let byte_len = items.len() * S::SIZE;
        self.start_vector(byte_len, S::ALIGN);
        if !items.is_empty() {
            let at = self.make_space(byte_len);
            for (item, out) in items
                .iter()
                .zip(self.buf[at..at + byte_len].chunks_exact_mut(S::SIZE))
            {
                item.write(out);
            }
        }
        self.end_vector(items.len())
    }

    /// A vector of offsets (tables, strings or vectors already written).
    pub fn create_vector_of_offsets(&mut self, items: &[Offset]) -> Offset {
        self.start_vector(items.len() * SIZE_UOFFSET, SIZE_UOFFSET);
        for &item in items.iter().rev() {
            self.push_offset(item);
        }
        self.end_vector(items.len())
    }

    /// C++ `CreateVectorOfStrings`: every string in order, then the vector.
    pub fn create_vector_of_strings<S: AsRef<str>>(&mut self, items: &[S]) -> Offset {
        let offsets: Vec<Offset> = items
            .iter()
            .map(|s| self.create_string(s.as_ref()))
            .collect();
        self.create_vector_of_offsets(&offsets)
    }

    // ---- tables ----

    /// Starts a table; returns the token [`Builder::end_table`] needs.
    pub fn start_table(&mut self) -> u32 {
        self.assert_not_nested();
        self.nested = true;
        self.size_u32()
    }

    fn track_field(&mut self, field: u16, off: u32) {
        self.field_locs.push(FieldLoc { off, id: field });
        self.max_voffset = self.max_voffset.max(field);
    }

    /// Adds a scalar field unless it equals the schema default.
    pub fn add_scalar<T: Scalar>(&mut self, field: u16, value: T, default: T) {
        if value == default {
            return;
        }
        self.add_scalar_always(field, value);
    }

    /// Adds a scalar field unconditionally (optional scalars, `= null`).
    pub fn add_scalar_always<T: Scalar>(&mut self, field: u16, value: T) {
        assert!(self.nested, "FlatBuffers: add_scalar outside a table");
        let off = self.push_scalar(value);
        self.track_field(field, off);
    }

    /// Adds an offset field; a null offset is not stored.
    pub fn add_offset(&mut self, field: u16, off: Offset) {
        if off.is_null() {
            return;
        }
        assert!(self.nested, "FlatBuffers: add_offset outside a table");
        let rel = self.refer_to(off);
        let at = self.push_scalar(rel);
        self.track_field(field, at);
    }

    /// Adds an inline struct field.
    pub fn add_struct<S: FbStruct>(&mut self, field: u16, value: &S) {
        assert!(self.nested, "FlatBuffers: add_struct outside a table");
        self.align(S::ALIGN);
        let at = self.make_space(S::SIZE);
        value.write(&mut self.buf[at..at + S::SIZE]);
        let off = self.size_u32();
        self.track_field(field, off);
    }

    /// Finishes a table: writes its vtable (or reuses an identical one
    /// already in the buffer) and returns the table's offset.
    pub fn end_table(&mut self, start: u32) -> Offset {
        assert!(self.nested, "FlatBuffers: end_table without start_table");
        let vtable_offset_loc = self.push_scalar(0i32);
        let vt_size = (self.max_voffset as usize + SIZE_VOFFSET).max(2 * SIZE_VOFFSET);
        let at = self.make_space(vt_size);
        let table_object_size = vtable_offset_loc - start;
        assert!(
            table_object_size < 0x10000,
            "FlatBuffers: table too large for a vtable"
        );
        (vt_size as u16).write_le(&mut self.buf[at..at + 2]);
        (table_object_size as u16).write_le(&mut self.buf[at + 2..at + 4]);
        for fl in &self.field_locs {
            let pos = (vtable_offset_loc - fl.off) as u16;
            let slot = at + fl.id as usize;
            debug_assert!(
                u16::read_le(&self.buf[slot..slot + 2]) == 0,
                "field set twice"
            );
            pos.write_le(&mut self.buf[slot..slot + 2]);
        }
        self.field_locs.clear();
        self.max_voffset = 0;

        let mut vt_use = self.size_u32();
        let len = self.buf.len();
        let new_vt = &self.buf[at..at + vt_size];
        for &old in &self.vtables {
            let old_at = len - old as usize;
            let old_size = u16::read_le(&self.buf[old_at..old_at + 2]) as usize;
            if old_size == vt_size && self.buf[old_at..old_at + old_size] == *new_vt {
                vt_use = old;
                break;
            }
        }
        if vt_use == self.size_u32() {
            self.vtables.push(vt_use);
        } else {
            // A duplicate: drop the vtable just written.
            self.head += vt_size;
        }
        let table_at = len - vtable_offset_loc as usize;
        (vt_use as i32 - vtable_offset_loc as i32).write_le(&mut self.buf[table_at..table_at + 4]);
        self.nested = false;
        Offset(vtable_offset_loc)
    }

    /// Finishes the buffer with `root` as its root table and an optional
    /// 4-byte file identifier.
    pub fn finish(&mut self, root: Offset, file_identifier: Option<&[u8; FILE_IDENTIFIER_LENGTH]>) {
        self.assert_not_nested();
        self.vtables.clear();
        let prefix = SIZE_UOFFSET
            + if file_identifier.is_some() {
                FILE_IDENTIFIER_LENGTH
            } else {
                0
            };
        self.pre_align(prefix, self.min_align);
        if let Some(id) = file_identifier {
            self.push_bytes(id);
        }
        self.push_offset(root);
        self.finished = true;
    }

    /// The finished buffer.
    ///
    /// # Panics
    /// When [`Builder::finish`] has not been called yet.
    pub fn finished_data(&self) -> &[u8] {
        assert!(self.finished, "FlatBuffers: the buffer is not finished");
        &self.buf[self.head..]
    }

    /// Consumes the builder, returning the finished buffer.
    ///
    /// # Panics
    /// When [`Builder::finish`] has not been called yet.
    pub fn into_finished_data(mut self) -> Vec<u8> {
        assert!(self.finished, "FlatBuffers: the buffer is not finished");
        self.buf.drain(..self.head);
        self.buf
    }
}

// ---------------------------------------------------------------------------
// Reader
// ---------------------------------------------------------------------------

/// A FlatBuffer that cannot be read: an offset, length or vtable points
/// outside the buffer, or a vector index is out of range.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InvalidFlatbuffer {
    message: String,
}

impl InvalidFlatbuffer {
    /// Creates an error with the given description.
    pub fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }

    /// The error's description.
    pub fn message(&self) -> &str {
        &self.message
    }
}

impl fmt::Display for InvalidFlatbuffer {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "invalid FlatBuffer: {}", self.message)
    }
}

impl std::error::Error for InvalidFlatbuffer {}

/// Result of a read-side access.
pub type ReadResult<T> = Result<T, InvalidFlatbuffer>;

/// `buf[pos..pos + len]`, or an error when that is out of range.
fn slice_at(buf: &[u8], pos: usize, len: usize) -> ReadResult<&[u8]> {
    pos.checked_add(len)
        .and_then(|end| buf.get(pos..end))
        .ok_or_else(|| {
            InvalidFlatbuffer::new(format!(
                "{len} bytes at {pos} lie outside the {}-byte buffer",
                buf.len()
            ))
        })
}

/// Reads one little-endian scalar at `pos`.
pub fn read_scalar<T: Scalar>(buf: &[u8], pos: usize) -> ReadResult<T> {
    Ok(T::read_le(slice_at(buf, pos, T::SIZE)?))
}

/// Follows the uoffset stored at `pos` to the position it points at.
fn deref_uoffset(buf: &[u8], pos: usize) -> ReadResult<usize> {
    let rel = read_scalar::<u32>(buf, pos)? as usize;
    let target = pos
        .checked_add(rel)
        .ok_or_else(|| InvalidFlatbuffer::new("offset overflow"))?;
    if target >= buf.len() {
        return Err(InvalidFlatbuffer::new(format!(
            "offset at {pos} points past the buffer end"
        )));
    }
    Ok(target)
}

/// A value that can be read from a buffer position - either inline (a
/// scalar or struct) or through a uoffset (a table, string or vector).
pub trait Follow<'a>: Sized {
    /// Bytes this value occupies inline (in a table field or vector slot).
    const INLINE_SIZE: usize;
    /// Reads the value whose inline part starts at `pos`.
    fn follow(buf: &'a [u8], pos: usize) -> ReadResult<Self>;
}

macro_rules! impl_follow_scalar {
    ($($t:ty),*) => {$(
        impl<'a> Follow<'a> for $t {
            const INLINE_SIZE: usize = <$t as Scalar>::SIZE;
            fn follow(buf: &'a [u8], pos: usize) -> ReadResult<Self> {
                read_scalar::<$t>(buf, pos)
            }
        }
    )*};
}
impl_follow_scalar!(u8, i8, u16, i16, u32, i32, u64, i64, f32, f64, bool);

/// Wraps an [`FbStruct`] so it can be read inline through [`Follow`].
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Inline<S>(pub S);

impl<'a, S: FbStruct> Follow<'a> for Inline<S> {
    const INLINE_SIZE: usize = S::SIZE;
    fn follow(buf: &'a [u8], pos: usize) -> ReadResult<Self> {
        Ok(Inline(S::read(slice_at(buf, pos, S::SIZE)?)))
    }
}

/// A FlatBuffers string: its bytes, without the terminating zero. The
/// format calls for UTF-8 but nothing enforces it, so the raw bytes are
/// kept.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Str<'a>(pub &'a [u8]);

impl<'a> Str<'a> {
    /// The string's raw bytes.
    pub fn as_bytes(&self) -> &'a [u8] {
        self.0
    }

    /// The string as UTF-8, replacing invalid sequences with U+FFFD.
    pub fn to_string_lossy(&self) -> String {
        String::from_utf8_lossy(self.0).into_owned()
    }
}

impl<'a> Follow<'a> for Str<'a> {
    const INLINE_SIZE: usize = SIZE_UOFFSET;
    fn follow(buf: &'a [u8], pos: usize) -> ReadResult<Self> {
        let at = deref_uoffset(buf, pos)?;
        let len = read_scalar::<u32>(buf, at)? as usize;
        Ok(Str(slice_at(buf, at + SIZE_UOFFSET, len)?))
    }
}

/// A vector of `T`s.
#[derive(Debug)]
pub struct Vector<'a, T> {
    buf: &'a [u8],
    /// Position of element 0.
    start: usize,
    len: usize,
    _marker: PhantomData<T>,
}

impl<T> Clone for Vector<'_, T> {
    fn clone(&self) -> Self {
        *self
    }
}

impl<T> Copy for Vector<'_, T> {}

impl<'a, T: Follow<'a>> Vector<'a, T> {
    /// Number of elements.
    pub fn len(&self) -> usize {
        self.len
    }

    /// True when the vector has no elements.
    pub fn is_empty(&self) -> bool {
        self.len == 0
    }

    /// Element `i`; an error when `i` is out of range or the element is
    /// malformed.
    pub fn get(&self, i: usize) -> ReadResult<T> {
        if i >= self.len {
            return Err(InvalidFlatbuffer::new(format!(
                "vector index {i} out of range (length {})",
                self.len
            )));
        }
        T::follow(self.buf, self.start + i * T::INLINE_SIZE)
    }

    /// Every element in order.
    pub fn iter(&self) -> impl Iterator<Item = ReadResult<T>> + use<'a, T> {
        let v = *self;
        (0..self.len).map(move |i| v.get(i))
    }
}

impl<'a, T: Follow<'a>> Follow<'a> for Vector<'a, T> {
    const INLINE_SIZE: usize = SIZE_UOFFSET;
    fn follow(buf: &'a [u8], pos: usize) -> ReadResult<Self> {
        let at = deref_uoffset(buf, pos)?;
        let len = read_scalar::<u32>(buf, at)? as usize;
        let start = at + SIZE_UOFFSET;
        let byte_len = len
            .checked_mul(T::INLINE_SIZE)
            .ok_or_else(|| InvalidFlatbuffer::new("vector length overflow"))?;
        slice_at(buf, start, byte_len)?;
        Ok(Vector {
            buf,
            start,
            len,
            _marker: PhantomData,
        })
    }
}

/// A table: its position plus the vtable describing which fields it holds.
#[derive(Debug, Clone, Copy)]
pub struct Table<'a> {
    buf: &'a [u8],
    pos: usize,
    vtable: usize,
    vtable_len: usize,
}

impl<'a> Table<'a> {
    /// The table at `pos`, validating its vtable.
    pub fn at(buf: &'a [u8], pos: usize) -> ReadResult<Self> {
        let soffset = i64::from(read_scalar::<i32>(buf, pos)?);
        let vtable = i64::try_from(pos).unwrap_or(i64::MAX) - soffset;
        let vtable = usize::try_from(vtable)
            .ok()
            .filter(|&v| v < buf.len())
            .ok_or_else(|| {
                InvalidFlatbuffer::new(format!("table at {pos} has its vtable outside the buffer"))
            })?;
        let vtable_len = read_scalar::<u16>(buf, vtable)? as usize;
        if vtable_len < 2 * SIZE_VOFFSET || !vtable_len.is_multiple_of(2) {
            return Err(InvalidFlatbuffer::new(format!(
                "table at {pos} has a malformed vtable"
            )));
        }
        slice_at(buf, vtable, vtable_len)?;
        Ok(Table {
            buf,
            pos,
            vtable,
            vtable_len,
        })
    }

    /// The root table of a finished buffer.
    pub fn root(buf: &'a [u8]) -> ReadResult<Self> {
        let pos = deref_uoffset(buf, 0)?;
        Table::at(buf, pos)
    }

    /// The table's position in the buffer.
    pub fn pos(&self) -> usize {
        self.pos
    }

    /// Where field `field` (a vtable slot offset: 4, 6, 8, ...) is stored,
    /// or `None` when the table does not carry it.
    pub fn field_pos(&self, field: u16) -> ReadResult<Option<usize>> {
        let slot = field as usize;
        if slot + SIZE_VOFFSET > self.vtable_len {
            return Ok(None);
        }
        let rel = read_scalar::<u16>(self.buf, self.vtable + slot)? as usize;
        Ok(if rel == 0 { None } else { Some(self.pos + rel) })
    }

    /// Field `field`, or `None` when absent.
    pub fn get<T: Follow<'a>>(&self, field: u16) -> ReadResult<Option<T>> {
        match self.field_pos(field)? {
            Some(at) => T::follow(self.buf, at).map(Some),
            None => Ok(None),
        }
    }

    /// Scalar field `field`, or `default` when absent.
    pub fn get_or<T: Follow<'a>>(&self, field: u16, default: T) -> ReadResult<T> {
        Ok(self.get(field)?.unwrap_or(default))
    }
}

impl<'a> Follow<'a> for Table<'a> {
    const INLINE_SIZE: usize = SIZE_UOFFSET;
    fn follow(buf: &'a [u8], pos: usize) -> ReadResult<Self> {
        let at = deref_uoffset(buf, pos)?;
        Table::at(buf, at)
    }
}

/// The 4-byte file identifier of a finished buffer, when it is long
/// enough to carry one.
pub fn file_identifier(buf: &[u8]) -> Option<&[u8]> {
    buf.get(SIZE_UOFFSET..SIZE_UOFFSET + FILE_IDENTIFIER_LENGTH)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[derive(Debug, Clone, Copy, PartialEq)]
    struct Vec3 {
        x: f32,
        y: f32,
        z: f32,
    }

    impl FbStruct for Vec3 {
        const SIZE: usize = 12;
        const ALIGN: usize = 4;
        fn write(&self, out: &mut [u8]) {
            self.x.write_le(&mut out[0..4]);
            self.y.write_le(&mut out[4..8]);
            self.z.write_le(&mut out[8..12]);
        }
        fn read(b: &[u8]) -> Self {
            Vec3 {
                x: f32::read_le(&b[0..4]),
                y: f32::read_le(&b[4..8]),
                z: f32::read_le(&b[8..12]),
            }
        }
    }

    #[test]
    fn padding_matches_the_reference_formula() {
        assert_eq!(padding_bytes(0, 4), 0);
        assert_eq!(padding_bytes(1, 4), 3);
        assert_eq!(padding_bytes(6, 8), 2);
        assert_eq!(padding_bytes(8, 8), 0);
    }

    #[test]
    fn string_and_table_round_trip_with_an_identifier() {
        let mut b = Builder::with_capacity(16);
        let s = b.create_string("hi");
        assert_eq!(s, Offset(8));
        let t = b.start_table();
        b.add_offset(4, s);
        let root = b.end_table(t);
        b.finish(root, Some(b"TEST"));
        let data = b.finished_data();
        assert_eq!(&data[4..8], b"TEST");
        let table = Table::root(data).unwrap();
        assert_eq!(table.get::<Str>(4).unwrap().unwrap().as_bytes(), b"hi");
        assert!(table.get::<Str>(6).unwrap().is_none());
        assert_eq!(data.len() % 4, 0);
    }

    #[test]
    fn round_trips_every_supported_kind() {
        let mut b = Builder::with_capacity(16); // forces several grows
        let name = b.create_string("hello, world");
        let empty = b.create_string("");
        let shorts = b.create_vector::<u16>(&[1, 2, 3]);
        let points = b.create_vector_of_structs(&[
            Vec3 {
                x: 1.0,
                y: 2.0,
                z: 3.0,
            },
            Vec3 {
                x: 4.0,
                y: 5.0,
                z: 6.0,
            },
        ]);
        let strings = b.create_vector_of_strings(&["a", "bc", "def"]);

        let mut children = Vec::new();
        for i in 1..=3u32 {
            // u32 + u16 + soffset + 8-byte vtable keeps every table
            // 4-aligned, so all three share one vtable.
            let t = b.start_table();
            b.add_scalar(4, i, 0);
            b.add_scalar(6, i as u16, 0);
            children.push(b.end_table(t));
        }
        let children_vec = b.create_vector_of_offsets(&children);

        let t = b.start_table();
        b.add_struct(
            14,
            &Vec3 {
                x: 7.0,
                y: 8.0,
                z: 9.0,
            },
        );
        b.add_offset(4, name);
        b.add_offset(6, shorts);
        b.add_offset(8, points);
        b.add_offset(10, strings);
        b.add_offset(12, children_vec);
        b.add_offset(18, empty);
        b.add_scalar::<i8>(16, 1, 0);
        b.add_scalar::<u32>(20, 0, 0); // default: not stored
        b.add_scalar_always::<u32>(22, 0); // optional: stored even at zero
        let root = b.end_table(t);
        b.finish(root, None);
        let data = b.into_finished_data();

        let table = Table::root(&data).unwrap();
        assert_eq!(
            table.get::<Str>(4).unwrap().unwrap().to_string_lossy(),
            "hello, world"
        );
        assert_eq!(table.get::<Str>(18).unwrap().unwrap().as_bytes(), b"");
        let shorts: Vector<u16> = table.get(6).unwrap().unwrap();
        assert_eq!(
            shorts.iter().collect::<ReadResult<Vec<_>>>().unwrap(),
            vec![1, 2, 3]
        );
        let points: Vector<Inline<Vec3>> = table.get(8).unwrap().unwrap();
        assert_eq!(
            points.get(1).unwrap().0,
            Vec3 {
                x: 4.0,
                y: 5.0,
                z: 6.0
            }
        );
        assert!(points.get(2).is_err());
        let strings: Vector<Str> = table.get(10).unwrap().unwrap();
        let strings: Vec<String> = strings
            .iter()
            .map(|s| s.unwrap().to_string_lossy())
            .collect();
        assert_eq!(strings, ["a", "bc", "def"]);
        let kids: Vector<Table> = table.get(12).unwrap().unwrap();
        for i in 0..3 {
            assert_eq!(
                kids.get(i).unwrap().get_or::<u32>(4, 0).unwrap(),
                i as u32 + 1
            );
            assert_eq!(
                kids.get(i).unwrap().get_or::<u16>(6, 0).unwrap(),
                i as u16 + 1
            );
        }
        assert_eq!(
            table.get::<Inline<Vec3>>(14).unwrap().unwrap().0,
            Vec3 {
                x: 7.0,
                y: 8.0,
                z: 9.0
            }
        );
        assert_eq!(table.get_or::<i8>(16, 0).unwrap(), 1);
        assert_eq!(table.field_pos(20).unwrap(), None);
        assert_eq!(table.get::<u32>(22).unwrap(), Some(0));
        assert_eq!(
            b_vtable_count(&data, &kids),
            1,
            "identical vtables are deduplicated"
        );
    }

    fn b_vtable_count(data: &[u8], kids: &Vector<Table>) -> usize {
        let mut seen = std::collections::BTreeSet::new();
        for k in kids.iter() {
            let k = k.unwrap();
            let soff = read_scalar::<i32>(data, k.pos()).unwrap();
            seen.insert(k.pos() as i64 - i64::from(soff));
        }
        seen.len()
    }

    #[test]
    fn rejects_out_of_range_offsets() {
        assert!(Table::root(&[]).is_err());
        assert!(Table::root(&[200, 0, 0, 0, 0, 0, 0, 0]).is_err());
        // Root points at a table whose vtable lies before the buffer start.
        assert!(Table::root(&[4, 0, 0, 0, 100, 0, 0, 0]).is_err());
    }
}
