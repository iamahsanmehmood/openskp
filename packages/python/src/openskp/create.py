"""Create new legacy-format (v17) ``.skp`` files from scratch.

This is a genuine, from-scratch binary writer for the same MFC ``CArchive``
object-stream format :mod:`openskp.legacy` reads - built by inverting that
reader's own, already-proven decoding logic (the class-ref/back-ref
protocol, entity preambles, drawbase records), then validated against real
desktop SketchUp until it produced files SketchUp actually opens correctly,
not just files OpenSKP's own reader accepts. No SketchUp SDK is called at
runtime; this module never links against or shells out to any proprietary
library. See the scaffold note below for the one place SDK-authored bytes
are involved, and how.

**Scope (deliberately limited for this first version):**

* Faces built directly from vertex coordinates, sharing vertices and edges
  automatically wherever coordinates coincide exactly. Solid-color and
  PNG-textured materials, named layers, and reusable component definitions
  with multiple instances (each with its own translation/rotation/scale)
  are all supported - see :meth:`SkpBuilder.add_material` / :meth:`SkpBuilder.
  add_texture_material` / :meth:`SkpBuilder.add_layer` / :meth:`SkpBuilder.
  add_component_definition`. There is no support yet for explicit texture
  positioning/pinning, nested definitions (a definition containing another
  definition's instances), or groups (as opposed to components).
* Coordinates are in **inches** - SketchUp's own native internal unit for
  this era of the format. Converting from another unit is the caller's
  responsibility for now.
* Editing an *existing* arbitrary ``.skp`` file is a separate, harder
  problem this module does not attempt: real SketchUp does not simply
  append to a file on save, it re-serializes the whole document, so there
  is no stable "original bytes + appended bytes" structure to target for
  an arbitrary input file the way there is for the blank scaffold below.

**The blank scaffold, and why it's there.** Every legacy ``.skp`` file
carries a header/material-manager/style-and-font-manager region this
project has not fully reverse-engineered - only enough of it is understood
to preserve it byte-for-byte and correctly renumber the handful of internal
references inside it that shift when new geometry is inserted (see
``_TAIL_REF_POSITIONS`` below). Rather than guess at synthesizing that
region from scratch, new files are built by splicing genuinely-written
geometry into a bundled minimal empty-document template
(``_scaffold/blank_v17.skp``).

That template's bytes came from Trimble's own official SketchUp SDK during
this feature's research phase (``SUModelCreate`` + a bare
``SUModelSaveToFileWithVersion`` call, nothing else) - disclosed here
plainly rather than hidden. Its content is SketchUp's own built-in
empty-document boilerplate (default style, default "Layer0", references to
system fonts like Arial/Tahoma) - the same bytes any brand-new SketchUp
document contains regardless of who created it, not anyone's creative work
or user/client data. The actual value in this module - the entity
byte-encoding, the object-graph protocol, the specific flag bytes real
SketchUp silently requires that :mod:`openskp.legacy`'s own reader
documents as "unused," the tail-reference renumbering - is 100%
independently reverse-engineered, written from scratch, and is what makes
this a genuine writer rather than a wrapper around the SDK. No SDK call
happens at import time, write time, or any other runtime path.
"""
from __future__ import annotations

import hashlib
import re
import struct
import time
import uuid
from importlib import resources
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

from . import legacy

__all__ = ["SkpWriteError", "SkpBuilder", "ComponentDefinitionBuilder", "create"]

Point3 = Tuple[float, float, float]


class SkpWriteError(Exception):
    """Raised when a ``.skp`` file cannot be constructed."""


_SCAFFOLD_FILE = "blank_v17.skp"
# Guards against silent corruption if the bundled scaffold is ever swapped
# without updating _TAIL_REF_POSITIONS below - those offsets are specific
# to this exact file's bytes, not derived generically.
_SCAFFOLD_SHA256 = "809a1ab73a20a192ab13aaff197afb1c67d0e9352f6a353a9cd8030919f8a6c3"

# Offsets (relative to the start of the document "tail" - the undecoded
# style/font-manager region that follows the root entity list) of internal
# references that must be renumbered by the same amount as the number of
# new archive slots inserted before them. Found empirically by diffing two
# real SDK-authored v17 files differing by exactly one piece of geometry
# and confirmed to hold up to a 600-new-entity insertion via the real
# SketchUp SDK as a validation oracle (never used at runtime by this
# module - see the module docstring). Specific to this exact scaffold
# file's tail content; do not reuse for a different base file without
# re-deriving them the same way.
_TAIL_REF_POSITIONS = (409, 468, 477, 479, 1383, 1385)

_CLAYER_PATTERN = re.escape(b"\xff\xff") + b".." + re.escape(struct.pack("<H", 6) + b"CLayer")

# Offset (relative to the material-manager insertion point - the position
# right before the "layer list marker" that a zero-material scaffold starts
# with) of the active-layer anchor - a back-reference to the model's first
# layer (Layer0) that lives immediately after the last existing layer
# record. It moves only when materials shift Layer0's own slot (never when
# layers are appended after it - confirmed empirically). Found by diffing
# real SDK-authored files with 0 vs N materials; confirmed to hold from N=1
# up to N=300. Six other candidate positions found the same way turned out
# to be _TAIL_REF_POSITIONS in disguise (their tail-relative offsets are
# exactly 409, 468, 477, 479, 1383, 1385) - those already get shifted
# correctly since to_bytes() sums every shift into total_tail_shift.
_ACTIVE_LAYER_ANCHOR_REL = 0  # relative to the layer insertion point, not material_insert_pos

# Offset (relative to the material-manager insertion point) of the u32
# layer-count field that precedes the layer list.
_LAYER_COUNT_REL = 5

_LAYER_SCHEMA = 3

# Absolute offset of a u16 "next available pid" counter that lives BEFORE the
# material insertion point (so only its value, not its position, needs
# correction). Increments by exactly the material COUNT (one pid consumed
# per material object; unlike the slot-reference fields above, the material
# class declaration itself doesn't consume a pid). Confirmed up to N=300.
_PID_COUNTER_POS = 1987

_MATERIAL_SCHEMA = 12
_DIB_SCHEMA = 3

# Ground-truth byte pattern (not a meaningful float) that real SketchUp
# writes for a texture's "applied height" when the caller never explicitly
# overrides the texture's scale/aspect - found by diffing an SDK-authored
# textured-material file; present verbatim rather than derived from a
# formula since its bit pattern doesn't correspond to any sensible height
# value (it decodes as ~1.29e-231 as an f64).
_TEXTURE_H_SENTINEL = bytes.fromhex("f0ffffffffffff0f")

_DEFINITION_SCHEMA = 11
_INSTANCE_SCHEMA = 6
_THUMBNAIL_SCHEMA = 1

# CCamera's class is declared inside the scaffold's own style/scene-manager
# prefix (before any of our splice points), not something this project has
# ever needed to declare fresh - ground-truth confirmed fixed at slot 7 for
# this exact bundled scaffold file. A thumbnail's camera sub-object is
# always written as a short class-ref to this slot.
_CCAMERA_SLOT = 7

# Same pattern as _CCAMERA_SLOT: CAttributeContainer's class is declared in
# the scaffold's own prefix, ground-truth confirmed fixed at slot 3.
_ATTR_CONTAINER_SLOT = 3

# The 176 bytes (everything after CCamera's 2-byte class-ref tag) real
# SketchUp writes for a definition's default thumbnail camera - copied
# verbatim rather than decoded, the same way as _TEXTURE_H_SENTINEL: this
# project has not reverse-engineered CCamera's internal fields, and a
# thumbnail's camera framing has no bearing on the geometry it depicts.
_CAMERA_TEMPLATE = bytes.fromhex(
    "00000000000000000000000000000000000000000000f03f0000000000000000"
    "00000000000000000000000000000000004000000000000000000000000000f0"
    "3f0000000000000000000000000000000000000000000000000100000000003e"
    "40000000000000f03f0000000000000000000000000000000000000000000000"
    "0000000000000000000100fffeff00000000000000000000000000000000f03f"
    "00000000000000000000000000000000"
)

# The definition record's 22-byte "base block" (immediately after its own
# preamble, before the embedded layer list) - all zero except offsets 3-4,
# matching the same 1,1 padding convention _drawbase already requires.
# This project has not reverse-engineered its meaning, only confirmed via
# ground truth that a definition with these bytes zeroed loads correctly
# (unlike drawbase's padding, which real SketchUp silently drops without).
_DEFINITION_BASE_BLOCK = bytes([0, 0, 0, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])


def _u32(v: int) -> bytes:
    return struct.pack("<I", v)


def _f64(v: float) -> bytes:
    return struct.pack("<d", v)


def _shift_ref(buf: bytearray, pos: int, shift: int) -> None:
    """Renumber the u16 archive slot-reference at ``pos`` by ``shift``,
    preserving the 0x8000 class-ref tag bit if the reference carries one."""
    u16 = struct.unpack_from("<H", buf, pos)[0]
    tag_bit = u16 & 0x8000
    slot = u16 & 0x7FFF
    struct.pack_into("<H", buf, pos, tag_bit | ((slot + shift) & 0x7FFF))


def _load_scaffold() -> bytes:
    # _scaffold is a plain data subdirectory, not an importable package (no
    # __init__.py) - anchor on the openskp package itself and navigate in.
    data = (resources.files("openskp") / "_scaffold" / _SCAFFOLD_FILE).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != _SCAFFOLD_SHA256:
        raise SkpWriteError(
            "bundled blank-document scaffold does not match the expected "
            "content (hash mismatch) - openskp.create's tail-reference "
            "offsets are specific to the original scaffold file and would "
            "silently corrupt output against a different one"
        )
    return data


class _ArchiveWriter:
    """Write-side mirror of :class:`legacy._Archive`'s slot/class-ref
    bookkeeping - emits the same MFC ``CArchive`` tag protocol
    (``0xFFFF`` new-class, ``0x8000|slot`` short class-ref, plain ``u16``
    back-ref) that :mod:`openskp.legacy` decodes, inverted for writing.
    """

    def __init__(self, next_slot: int, class_slot: Dict[str, int], next_pid: int = 1):
        self.next_slot = next_slot
        self.class_slot = dict(class_slot)
        self.next_pid = next_pid
        self.buf = bytearray()

    def _alloc(self) -> int:
        s = self.next_slot
        self.next_slot += 1
        return s

    def _alloc_pid(self) -> int:
        p = self.next_pid
        self.next_pid += 1
        return p

    def _new_of_known_class(self, class_name: str, schema: Optional[int] = None) -> int:
        if class_name not in self.class_slot:
            if schema is None:
                raise SkpWriteError(f"{class_name} not yet declared and no schema given")
            self.buf += struct.pack("<H", 0xFFFF)
            self.buf += struct.pack("<H", schema)
            self.buf += struct.pack("<H", len(class_name))
            self.buf += class_name.encode("ascii")
            self.class_slot[class_name] = self._alloc()
            return self._alloc()
        slot = self.class_slot[class_name]
        if slot <= 0x7FFF:
            self.buf += struct.pack("<H", 0x8000 | slot)
        else:
            self.buf += struct.pack("<H", 0x7FFF)
            self.buf += _u32(0x80000000 | slot)
        return self._alloc()

    def _null(self) -> None:
        self.buf += struct.pack("<H", 0)

    def _backref(self, slot: int) -> None:
        if slot <= 0x7FFF:
            self.buf += struct.pack("<H", slot)
        else:
            self.buf += struct.pack("<H", 0x7FFF)
            self.buf += _u32(slot)

    def _encode_pid(self, pid: int) -> bytes:
        mask = 0
        pid_bytes = []
        for bit in range(8):
            byte_val = (pid >> (8 * bit)) & 0xFF
            if byte_val:
                mask |= 1 << bit
                pid_bytes.append(byte_val)
        return bytes([mask]) + bytes(pid_bytes)

    def _preamble(self, pid: Optional[int] = None, real_attrs: bool = False) -> None:
        if real_attrs:
            # Ground truth: CComponentDefinition and CComponentInstance both
            # reference a real (but childless) CAttributeContainer here
            # instead of the null pointer every other entity in this
            # project uses - CAttributeContainer's own class is pre-existing
            # in the scaffold's prefix, same pattern as _CCAMERA_SLOT.
            self.buf += struct.pack("<H", 0x8000 | _ATTR_CONTAINER_SLOT)
            self._alloc()  # a class-ref always allocates a new object slot, even a bookkeeping-only one
            self.buf += bytes(3)  # the container's own nested preamble: null attrs (2) + mask=0 (1)
            self.buf += struct.pack("<H", 0)  # empty children-list terminator
        else:
            self._null()  # no CAttributeContainer
        if pid is None:
            pid = self._alloc_pid()
        self.buf += self._encode_pid(pid)

    def _drawbase(
        self, mat: int = 0, layer: int = 0,
        hidden: bool = False, soft: bool = False, smooth: bool = False,
    ) -> None:
        b = bytearray(10)
        struct.pack_into("<H", b, 0, mat)
        b[2] = 1 if hidden else 0
        # offsets 3-4: legacy.py's reader documents these as unused padding
        # (_drawbase's docstring), but real SketchUp silently drops any
        # entity whose drawbase has them zeroed - ground-truth-confirmed by
        # diffing real SDK-authored files. Must be 1, 1.
        b[3] = 1
        b[4] = 1
        b[5] = 1 if soft else 0
        b[6] = 1 if smooth else 0
        struct.pack_into("<H", b, 8, layer)
        self.buf += bytes(b)

    def _write_vertex(self, point: Point3) -> int:
        slot = self._new_of_known_class("CVertex", schema=0)
        self._preamble()
        self.buf += _f64(point[0]) + _f64(point[1]) + _f64(point[2])
        return slot

    def _write_str(self, s: str) -> None:
        encoded = s.encode("utf-16-le")
        n = len(encoded) // 2
        if n >= 0xFF:
            raise SkpWriteError("string too long to encode (255 char limit)")
        self.buf += b"\xff\xfe\xff" + struct.pack("<B", n) + encoded

    def write_material(self, name: str, rgba: Tuple[int, int, int, int]) -> int:
        """Write one solid-color ``CMaterial`` record and return its slot."""
        slot = self._new_of_known_class("CMaterial", schema=_MATERIAL_SCHEMA)
        self._preamble()
        self._write_str(name)
        self.buf += struct.pack("<H", 0)  # texflag: solid color, no texture
        self.buf += bytes(rgba)
        self._write_str("")  # texture path (empty - no texture)
        self.buf += bytes(8)  # unknown/padding - ground truth is all-zero here
        self.buf += _f64(1.0)  # opacity
        self.buf.append(0)  # use_opacity = False (alpha carries transparency instead)
        return slot

    def write_textured_material(self, name: str, image_bytes: bytes, texture_path: str, subtype: int) -> int:
        """Write one image-textured ``CMaterial`` record (embedding
        ``image_bytes`` verbatim inside a ``CDib`` sub-object) and return
        its slot. ``texture_path`` is stored as-is - ground truth shows
        real SketchUp stores the original absolute file path, but any
        string round-trips fine structurally. ``subtype`` is CDib's image
        format tag (4 for PNG - the only value this project has confirmed
        via SDK ground truth; see :meth:`SkpBuilder.add_texture_material`).
        """
        slot = self._new_of_known_class("CMaterial", schema=_MATERIAL_SCHEMA)
        self._preamble()
        self._write_str(name)
        self.buf += struct.pack("<H", 1)  # texflag: textured
        self.buf += bytes(2)  # texture-flag pad (v17+)
        self._new_of_known_class("CDib", schema=_DIB_SCHEMA)
        self.buf += struct.pack("<I", subtype)
        self.buf += struct.pack("<I", len(image_bytes))
        self.buf += image_bytes
        self.buf += _f64(1.0)  # applied width - ground truth default when unscaled
        self.buf += _TEXTURE_H_SENTINEL
        self._write_str(texture_path)
        # avg color (RGBA + pad + RGBA repeated, per legacy.py's _read_material
        # comment) - neutral opaque white rather than a real image average,
        # since this project doesn't depend on an image library to compute
        # one. Ground truth confirms real SketchUp reads texture pixels
        # directly for rendering; avg only feeds the material browser's
        # thumbnail/tint preview.
        self.buf += bytes([255, 255, 255, 255, 0, 255, 255, 255, 255])
        self._write_str("")  # second name field - empty in ground truth
        self.buf += struct.pack("<I", 1) + struct.pack("<I", 0)  # blob (colorize-related, ground truth: 1, 0)
        self.buf += _f64(1.0)  # opacity
        self.buf.append(0)  # use_opacity = False
        return slot

    def write_layer(self, name: str, with_pids: bool = True) -> int:
        """Write one ``CLayer`` record and return its slot. CLayer is
        always already declared (the scaffold's Layer0 guarantees it), so
        this never emits a new-class declaration - only a short class-ref.

        Ground truth shows each top-level layer record contains a second,
        embedded pid (inside a 5-byte block after the visible name - byte 0
        is the hidden flag, bytes 1-2 are always zero, then a mask+pidbytes
        pair matching the same encoding _preamble uses) - so each layer
        consumes 2 pids, not 1. ``with_pids=False`` (used only for the
        layer a component definition embeds internally - see
        `write_definition_header`) omits both: ground truth shows that
        copy carries neither its own preamble pid nor this second one.
        """
        slot = self._new_of_known_class("CLayer", schema=_LAYER_SCHEMA)
        self._preamble(pid=None if with_pids else 0)
        self._write_str(name)
        pid2 = self._alloc_pid() if with_pids else 0
        self.buf += bytes(3) + self._encode_pid(pid2)  # hidden=0, pad, pad, then mask+pidbytes
        self._write_str(f"Layer_{name}")
        self.buf += struct.pack("<H", 256)  # ground truth is a constant 256 here
        self.buf += bytes(4)  # rgba - layers don't carry a rendering color
        self._write_str("")  # second name field - empty in ground truth
        self.buf += bytes(8) + _f64(0.5) + bytes(5)  # 21-byte tail, opacity-like f64=0.5
        return slot

    def write_thumbnail(self) -> None:
        """Write a ``CThumbnail`` with a default camera and no image -
        ground truth shows the image itself is optional (a null CDib
        reference is valid and is what real SketchUp writes for a
        definition whose thumbnail was never explicitly rendered)."""
        self._new_of_known_class("CThumbnail", schema=_THUMBNAIL_SCHEMA)
        self._preamble(pid=0)  # structural container: ground truth carries no pid
        self.buf += struct.pack("<H", 0x8000 | _CCAMERA_SLOT)
        self._alloc()  # a class-ref always allocates a new object slot, even a bookkeeping-only one
        self.buf += _CAMERA_TEMPLATE
        self._null()  # no thumbnail image

    def write_definition_header(self) -> Tuple[int, int]:
        """Begin a ``CComponentDefinition`` record - everything up to (not
        including) its internal entity list. Returns ``(definition_slot,
        count_patch_pos)``: the caller writes the definition's geometry via
        further `write_face` calls (appended directly to ``self.buf``),
        then must patch a u32 entity count into ``self.buf`` at
        ``count_patch_pos`` and call `write_definition_tail` to close it out.
        """
        slot = self._new_of_known_class("CComponentDefinition", schema=_DEFINITION_SCHEMA)
        self._preamble(real_attrs=True)  # ground truth: a real pid and a real (empty) attr container
        self.buf += _DEFINITION_BASE_BLOCK
        self.buf += _u32(1)  # nlayers: always 1, an embedded copy of Layer0
        embedded_layer_slot = self.write_layer("Layer0", with_pids=False)
        self._backref(embedded_layer_slot)  # "decl": this definition's own active layer
        self.buf += _u32(0)  # nested-definition count - always 0, not supported
        count_patch_pos = len(self.buf)
        self.buf += _u32(0)  # placeholder entity count, patched by the caller
        return slot, count_patch_pos

    def write_definition_tail(self, name: str) -> None:
        """Close out a ``CComponentDefinition`` record: relationship count,
        GUID, name, timestamp, behavior flags, and a default thumbnail."""
        self.buf += _u32(0)  # nrel: CRelationship count - always 0, not supported
        self.buf += struct.pack("<H", 0)
        self.buf += uuid.uuid4().bytes
        self._write_str(name)
        self._write_str("")  # description - empty in ground truth
        self._write_str("")  # second name field - empty in ground truth
        self.buf += _u32(int(time.time()))
        # 43-byte gap; byte -9 carries the always-faces-camera/
        # shadows-face-sun behavior flags (legacy.py's _read_definition) -
        # both left off, matching neither being exposed by this writer yet.
        self.buf += bytes(43)
        self.write_thumbnail()

    def write_instance(
        self,
        definition_slot: int,
        name: str,
        translation: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        matrix3x3: Optional[Tuple[float, float, float, float, float, float, float, float, float]] = None,
        instance_material: int = 0,
        instance_layer: int = 0,
    ) -> int:
        """Write one ``CComponentInstance`` placing a copy of
        ``definition_slot`` (from `write_definition_header`) and return how
        many new root-entity-list slots it consumed - always 1, matching
        `write_face`'s return contract (the caller accumulates this into
        the file's total root count; an instance has no sub-entities of
        its own the way a face has edges).

        ``matrix3x3`` is a row-major 3x3 rotation/scale matrix (identity if
        omitted); ``translation`` is applied after it. Ground truth shows
        the file's transform encoding is exactly this 3x3 matrix (9 f64s) +
        translation (3 f64s) + a trailing 1.0 - the 4th row of a standard
        4x4 affine matrix, always [0, 0, 0, 1], is omitted entirely rather
        than stored.
        """
        self._new_of_known_class("CComponentInstance", schema=_INSTANCE_SCHEMA)
        self._preamble(real_attrs=True)  # ground truth: instances also carry a real (empty) attr container
        self._drawbase(mat=instance_material, layer=instance_layer)
        self._backref(definition_slot)
        if matrix3x3 is None:
            matrix3x3 = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
        for v in (*matrix3x3, *translation, 1.0):
            self.buf += _f64(v)
        self._write_str(name)
        self.buf += uuid.uuid4().bytes
        return 1

    def write_face(
        self,
        points: Sequence[Point3],
        vertex_slots: Dict[Point3, int],
        edge_registry: Dict[FrozenSet[int], Tuple[int, int]],
        face_material: int = 0,
        face_layer: int = 0,
        back_material: int = 0,
        hidden: bool = False,
        soft_edges: bool = False,
        smooth_edges: bool = False,
        hidden_edges: bool = False,
    ) -> int:
        """Write one planar face and return how many new root-entity-list
        slots it consumed (edges newly declared, plus the face itself) -
        the caller accumulates this into the file's total root count.

        ``points`` form a closed polygon in order (do not repeat the first
        point at the end). Vertices and edges are shared automatically
        across calls via ``vertex_slots``/``edge_registry`` wherever
        coordinates coincide exactly - pass the same dicts across every
        `write_face` call building one mesh. ``face_material``/
        ``back_material`` are material slots (from :meth:`write_material`)
        applied to the face's front/back side; ``face_layer`` is a layer
        slot (from :meth:`write_layer`). 0 means the default in all three
        cases. Edges always keep drawbase mat=0 and layer=0 (default) even
        when their face has a material or layer - ground truth confirms
        this for both fields.

        ``hidden`` hides the face itself. ``soft_edges``/``smooth_edges``/
        ``hidden_edges`` apply to any edge NEWLY declared by this call
        (typical for tessellated curved surfaces, where the internal edges
        between adjacent faces should shade smoothly and stay invisible) -
        an edge already shared with a previous face keeps whatever flags
        it was first declared with; these have no effect on it.
        """
        n = len(points)
        point_slots = [vertex_slots.get(p) for p in points]
        edge_slots: List[int] = []
        edge_senses: List[int] = []
        new_entities = 0

        for i in range(n):
            v1_idx, v2_idx = i, (i + 1) % n
            v1_known, v2_known = point_slots[v1_idx], point_slots[v2_idx]
            key = (
                frozenset((v1_known, v2_known))
                if v1_known is not None and v2_known is not None
                else None
            )
            if key is not None and key in edge_registry:
                edge_slot, fwd_v1 = edge_registry[key]
                edge_slots.append(edge_slot)
                edge_senses.append(0 if fwd_v1 == v1_known else 1)
                continue

            edge_slot = self._new_of_known_class("CEdge", schema=2)
            self._preamble()
            self._drawbase(hidden=hidden_edges, soft=soft_edges, smooth=smooth_edges)
            for idx in (v1_idx, v2_idx):
                if point_slots[idx] is None:
                    point_slots[idx] = self._write_vertex(points[idx])
                    vertex_slots[points[idx]] = point_slots[idx]
                else:
                    self._backref(point_slots[idx])
            self._null()  # curve = None
            edge_slots.append(edge_slot)
            edge_senses.append(0)
            new_entities += 1
            edge_registry[frozenset((point_slots[v1_idx], point_slots[v2_idx]))] = (
                edge_slot,
                point_slots[v1_idx],
            )

        self._new_of_known_class("CFace", schema=3)
        self._preamble()
        self._drawbase(mat=face_material, layer=face_layer, hidden=hidden)
        nx, ny, nz, d = _plane_from_polygon(points)
        self.buf += _f64(nx) + _f64(ny) + _f64(nz) + _f64(d)
        self.buf += _u32(1)  # nloops = 1

        loop_slot = self._new_of_known_class("CLoop", schema=1)
        self._preamble(pid=0)  # structural object: ground truth uses pid 0
        # legacy.py's reader treats these 2 bytes as opaque (_read_loop just
        # does r.raw(2)), but real SketchUp requires 01 01, not 00 00 - same
        # silent-drop failure mode as the drawbase padding above.
        self.buf += bytes([1, 1])

        for i in range(n):
            self._new_of_known_class("CEdgeUse", schema=1)
            self._preamble(pid=0)
            self._backref(edge_slots[i])
            self.buf.append(edge_senses[i])
            self._backref(loop_slot)
        self._null()  # loop terminator

        self.buf += struct.pack("<H", back_material)
        new_entities += 1  # the face itself
        return new_entities


def _plane_from_polygon(points: Sequence[Point3]) -> Tuple[float, float, float, float]:
    # Newell's method: sums a cross-product-like term over every edge
    # rather than reading the normal off just the first 3 points. That
    # first-3-points approach breaks for concave polygons whenever the
    # first vertex happens to be a reflex corner (wrong-signed normal) -
    # Newell's sum is the polygon's true area-weighted normal regardless
    # of convexity, as long as it's planar and simple (non-self-intersecting).
    n = len(points)
    nx = ny = nz = 0.0
    for i in range(n):
        x0, y0, z0 = points[i]
        x1, y1, z1 = points[(i + 1) % n]
        nx += (y0 - y1) * (z0 + z1)
        ny += (z0 - z1) * (x0 + x1)
        nz += (x0 - x1) * (y0 + y1)
    length = (nx * nx + ny * ny + nz * nz) ** 0.5
    if length < 1e-9:
        raise SkpWriteError("face points are collinear or degenerate; cannot compute a plane")
    nx, ny, nz = nx / length, ny / length, nz / length
    cx = sum(p[0] for p in points) / n
    cy = sum(p[1] for p in points) / n
    cz = sum(p[2] for p in points) / n
    d = nx * cx + ny * cy + nz * cz

    # Every point must actually lie on the fitted plane - a mesh built
    # from slightly-off-plane input would otherwise silently warp instead
    # of failing loudly. Tolerance scales with the face's own size so it
    # means the same thing for a 1-inch face and a 1000-inch one.
    span = max(max(p[i] for p in points) - min(p[i] for p in points) for i in range(3))
    tol = max(span, 1.0) * 1e-6
    for p in points:
        dist = nx * p[0] + ny * p[1] + nz * p[2] - d
        if abs(dist) > tol:
            raise SkpWriteError(
                f"face points are not coplanar (point {p} is {abs(dist):.6g} units "
                "off the fitted plane) - openskp.create only supports planar faces"
            )
    return nx, ny, nz, d


class ComponentDefinitionBuilder:
    """Accumulates one reusable component definition's geometry. Construct
    via :meth:`SkpBuilder.add_component_definition`, not directly - use it
    as a context manager, then pass it to :meth:`SkpBuilder.add_instance`
    to place copies of it.

    >>> with builder.add_component_definition("Chair") as chair:
    ...     chair.add_face([(0, 0, 0), (20, 0, 0), (20, 20, 0), (0, 20, 0)])
    >>> builder.add_instance(chair, translation=(100, 0, 0))
    """

    def __init__(self, skp: "SkpBuilder", slot: int, name: str, count_patch_pos: int):
        self._skp = skp
        self.slot = slot
        self.name = name
        self._count_patch_pos = count_patch_pos
        self._vertex_slots: Dict[Point3, int] = {}
        self._edge_registry: Dict[FrozenSet[int], Tuple[int, int]] = {}
        self._new_entity_count = 0
        self._closed = False

    def add_face(
        self,
        points: Sequence[Point3],
        material: Optional[int] = None,
        layer: Optional[int] = None,
        back_material: Optional[int] = None,
        hidden: bool = False,
        soft_edges: bool = False,
        smooth_edges: bool = False,
        hidden_edges: bool = False,
    ) -> None:
        """Add one planar face to this definition - same signature and
        behavior as :meth:`SkpBuilder.add_face`, except vertices/edges are
        shared only within this definition, never with the root model or
        other definitions."""
        if self._closed:
            raise SkpWriteError(
                f"component definition {self.name!r} has already closed "
                "(its `with` block exited) - cannot add more faces to it"
            )
        points = [(float(p[0]), float(p[1]), float(p[2])) for p in points]
        if len(points) < 3:
            raise SkpWriteError("a face needs at least 3 points")
        self._new_entity_count += self._skp._definition_writer.write_face(
            points, self._vertex_slots, self._edge_registry,
            material or 0, layer or 0, back_material or 0,
            hidden, soft_edges, smooth_edges, hidden_edges,
        )

    def __enter__(self) -> "ComponentDefinitionBuilder":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is not None:
            return None
        if self._new_entity_count == 0:
            raise SkpWriteError(f"component definition {self.name!r} has no geometry - add at least one face")
        writer = self._skp._definition_writer
        struct.pack_into("<I", writer.buf, self._count_patch_pos, self._new_entity_count)
        writer.write_definition_tail(self.name)
        self._closed = True
        self._skp._open_definition = None
        return None


class SkpBuilder:
    """Accumulates geometry and writes it into a new legacy-format (v17)
    ``.skp`` file. Construct via :func:`create`, not directly."""

    def __init__(self) -> None:
        data = _load_scaffold()
        lm = re.search(_CLAYER_PATTERN, data, re.DOTALL)
        if lm is None:
            raise SkpWriteError("scaffold is missing its CLayer class record")
        start = lm.start() - 9
        base = legacy._probe_layer_anchor_bases(data, 17, start, 0)[0]

        ar = legacy._Archive(data, 17)
        ar.readers.update(legacy._READERS)
        ar.next_slot = base
        ar.walk_base = base
        r = ar.r
        r.pos = start
        r.u32()
        r.u8()
        layer_count_pos = r.pos
        orig_layer_count = r.u32()
        for _ in range(orig_layer_count):
            ar.read_object(r, expect="CLayer")
        layer_insert_pos = r.pos
        layer_writer_base = ar.next_slot
        ar.read_object(r)  # definition-list anchor (active-layer back-ref)
        def_count_pos = r.pos
        def_count = r.u32()
        for _ in range(def_count):
            ar.read_object(r, expect="CComponentDefinition")

        root_count_pos = r.pos
        orig_root_count = struct.unpack_from("<I", data, root_count_pos)[0]
        r.u32()
        legacy._read_entity_list(ar, r, orig_root_count, "root")
        tail_pos = r.pos

        self._data = data
        self._material_insert_pos = start
        self._base = base
        self._layer_count_pos = layer_count_pos
        self._orig_layer_count = orig_layer_count
        self._layer_insert_pos = layer_insert_pos
        self._def_count_pos = def_count_pos
        self._orig_def_count = def_count
        self._root_count_pos = root_count_pos
        self._orig_root_count = orig_root_count
        self._tail_pos = tail_pos
        # The scaffold-derived starting slot for anything written AFTER the
        # (always byte-for-byte-copied) layer/definition/root-entity region -
        # i.e. where geometry's own new slots would start if zero materials
        # or layers are added. Materials splice in before the layer list and
        # layers splice in right after the existing ones, so every slot from
        # here on shifts by however many slots each section ends up
        # consuming - see add_material/add_layer.
        self._scaffold_next_slot = ar.next_slot
        self._scaffold_class_slot = ar.class_slot
        # Materials always start allocating at `base`, the same slot the
        # (possibly absent) material section would have occupied.
        self._material_writer = _ArchiveWriter(next_slot=base, class_slot={})
        self._materials_by_name: Dict[str, int] = {}
        self._material_count = 0
        # Deferred: layers splice in AFTER materials, so the layer writer's
        # starting slot depends on the final material count. Constructed
        # lazily on the first add_layer() call, once material_shift is
        # locked in (add_material enforces that ordering) - see add_layer.
        self._layer_writer_base = layer_writer_base
        self._layer_writer: Optional[_ArchiveWriter] = None
        self._layer_writer_start: Optional[int] = None
        self._layers_by_name: Dict[str, int] = {}
        self._layer_count = 0
        # Deferred the same way as the layer writer: component definitions
        # splice in after layers, before root-level geometry, so their
        # starting slot depends on the final material+layer shift.
        self._definition_writer: Optional[_ArchiveWriter] = None
        self._definition_writer_start: Optional[int] = None
        self._definition_count = 0
        self._open_definition: Optional["ComponentDefinitionBuilder"] = None
        self._geometry_writer: Optional[_ArchiveWriter] = None
        self._vertex_slots: Dict[Point3, int] = {}
        self._edge_registry: Dict[FrozenSet[int], Tuple[int, int]] = {}
        self._new_entity_count = 0
        self._face_count = 0

    def add_material(self, name: str, rgba: Sequence[int]) -> int:
        """Register a solid-color material and return a handle to pass as
        `add_face`'s ``material`` argument. ``rgba`` is ``(r, g, b)`` or
        ``(r, g, b, a)``, each 0-255; alpha defaults to 255 (opaque).

        Calling this again with a name already registered returns the same
        handle rather than creating a duplicate material.

        All materials must be added before the first `add_face` call - the
        geometry section's slot numbering is fixed once writing begins, and
        depends on the final material count. They must also come before any
        `add_layer` or `add_component_definition` call - materials are
        spliced in earlier in the file, so both of those sections' own slot
        numbering depends on the final material count too.
        """
        if self._geometry_writer is not None:
            raise SkpWriteError("add_material must be called before any add_face calls")
        if self._layer_writer is not None:
            raise SkpWriteError("add_material must be called before any add_layer calls")
        if self._definition_writer is not None:
            raise SkpWriteError("add_material must be called before any add_component_definition calls")
        if name in self._materials_by_name:
            return self._materials_by_name[name]
        if len(rgba) == 3:
            rgba = (*rgba, 255)
        if len(rgba) != 4 or not all(isinstance(c, int) and 0 <= c <= 255 for c in rgba):
            raise SkpWriteError("rgba must be 3 or 4 integers in 0-255")
        slot = self._material_writer.write_material(name, tuple(rgba))
        self._materials_by_name[name] = slot
        self._material_count += 1
        return slot

    def add_texture_material(self, name: str, image_path: str) -> int:
        """Register an image-textured material from a local PNG file and
        return a handle to pass as `add_face`'s ``material`` argument.

        Only PNG is supported for now - the only image format this project
        has confirmed the on-disk ``CDib`` encoding for via SDK ground
        truth (see :meth:`_ArchiveWriter.write_textured_material`). UV
        mapping is always the default planar projection; explicit
        positioning/pinning is not supported (ground truth shows the
        default case needs no extra per-face texture-coordinate record at
        all, which is what keeps this scoped as an addition to materials
        rather than a much larger face-attribute feature).

        Same ordering rules as `add_material` - must be called before any
        `add_layer`, `add_component_definition`, or `add_face` call.
        """
        if self._geometry_writer is not None:
            raise SkpWriteError("add_texture_material must be called before any add_face calls")
        if self._layer_writer is not None:
            raise SkpWriteError("add_texture_material must be called before any add_layer calls")
        if self._definition_writer is not None:
            raise SkpWriteError("add_texture_material must be called before any add_component_definition calls")
        if name in self._materials_by_name:
            return self._materials_by_name[name]
        if not image_path.lower().endswith(".png"):
            raise SkpWriteError("only .png textures are supported for now")
        with open(image_path, "rb") as f:
            image_bytes = f.read()
        slot = self._material_writer.write_textured_material(name, image_bytes, image_path, subtype=4)
        self._materials_by_name[name] = slot
        self._material_count += 1
        return slot

    def add_layer(self, name: str) -> int:
        """Register a layer and return a handle to pass as `add_face`'s
        ``layer`` argument.

        Calling this again with a name already registered returns the same
        handle rather than creating a duplicate layer.

        All layers must be added before the first `add_face` call, for the
        same reason as `add_material`. They must also come before any
        `add_component_definition` call - layers are spliced in earlier in
        the file, so a definition's own slot numbering depends on the
        final layer count too.
        """
        if self._geometry_writer is not None:
            raise SkpWriteError("add_layer must be called before any add_face calls")
        if self._definition_writer is not None:
            raise SkpWriteError("add_layer must be called before any add_component_definition calls")
        if name in self._layers_by_name:
            return self._layers_by_name[name]
        if self._layer_writer is None:
            material_shift = self._material_writer.next_slot - self._base
            self._layer_writer_start = self._layer_writer_base + material_shift
            # CLayer's class declaration lives inside Layer0's copied-through
            # bytes, which - like everything else after the material
            # section - shifts by material_shift. The scaffold-derived
            # class_slot dict still has its raw, unshifted value, so correct
            # every entry before handing it to a writer that might look one
            # up (write_layer's short class-ref for CLayer needs the true
            # post-shift slot, not the baseline one).
            self._layer_writer = _ArchiveWriter(
                next_slot=self._layer_writer_start, class_slot=self._material_shifted_class_slot()
            )
        slot = self._layer_writer.write_layer(name)
        self._layers_by_name[name] = slot
        self._layer_count += 1
        return slot

    def _material_shifted_class_slot(self) -> Dict[str, int]:
        material_shift = self._material_writer.next_slot - self._base
        return {n: s + material_shift for n, s in self._scaffold_class_slot.items()}

    def _layer_shift(self) -> int:
        if self._layer_writer is None:
            return 0
        return self._layer_writer.next_slot - self._layer_writer_start

    def _post_layer_class_slot(self) -> Dict[str, int]:
        """The class_slot dict a writer positioned right after the layer
        section (a definition writer, or root geometry if no definitions
        exist) should start from."""
        if self._layer_writer is not None:
            return dict(self._layer_writer.class_slot)
        return self._material_shifted_class_slot()

    def add_component_definition(self, name: str) -> "ComponentDefinitionBuilder":
        """Start a new reusable component definition. Use the returned
        object as a context manager, adding its geometry via `.add_face`
        inside the ``with`` block; once closed, pass it to `add_instance`
        to place copies of it in the model.

        >>> with builder.add_component_definition("Chair") as chair:
        ...     chair.add_face([(0, 0, 0), (20, 0, 0), (20, 20, 0), (0, 20, 0)])
        >>> builder.add_instance(chair, translation=(100, 0, 0))

        Must be called before any `add_face`/`add_instance` call on the
        builder itself - component definitions splice in after materials
        and layers, before root-level geometry, so their slot numbering
        depends on the final material and layer counts.
        """
        if self._geometry_writer is not None:
            raise SkpWriteError("add_component_definition must be called before any add_face/add_instance calls")
        if self._open_definition is not None:
            raise SkpWriteError(
                f"component definition {self._open_definition.name!r} is still open - "
                "exit its `with` block before starting another"
            )
        if self._definition_writer is None:
            self._definition_writer_start = self._scaffold_next_slot + (
                self._material_writer.next_slot - self._base
            ) + self._layer_shift()
            self._definition_writer = _ArchiveWriter(
                next_slot=self._definition_writer_start, class_slot=self._post_layer_class_slot()
            )
        slot, count_patch_pos = self._definition_writer.write_definition_header()
        self._definition_count += 1
        comp = ComponentDefinitionBuilder(self, slot, name, count_patch_pos)
        self._open_definition = comp
        return comp

    def _definition_shift(self) -> int:
        if self._definition_writer is None:
            return 0
        return self._definition_writer.next_slot - self._definition_writer_start

    def _post_definition_class_slot(self) -> Dict[str, int]:
        if self._definition_writer is not None:
            return dict(self._definition_writer.class_slot)
        return self._post_layer_class_slot()

    def add_instance(
        self,
        definition: "ComponentDefinitionBuilder",
        name: Optional[str] = None,
        translation: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        matrix3x3: Optional[Tuple[float, float, float, float, float, float, float, float, float]] = None,
        material: Optional[int] = None,
        layer: Optional[int] = None,
    ) -> None:
        """Place one instance of ``definition`` (from
        `add_component_definition`, already closed) in the model.

        ``matrix3x3`` is a row-major 3x3 rotation/scale matrix (identity if
        omitted); ``translation`` is applied after it, in inches.
        ``material``/``layer``, if given, are handles from `add_material`/
        `add_layer` applied to the instance itself (not its contents).
        """
        if definition._closed is False:
            raise SkpWriteError(
                f"component definition {definition.name!r} is still open - "
                "exit its `with` block before calling add_instance"
            )
        self._ensure_geometry_writer()
        self._new_entity_count += self._geometry_writer.write_instance(
            definition.slot, name or definition.name, translation, matrix3x3, material or 0, layer or 0
        )
        self._face_count += 1  # reuses the "at least one root entity" check in to_bytes

    def _ensure_geometry_writer(self) -> None:
        if self._geometry_writer is not None:
            return
        material_shift = self._material_writer.next_slot - self._base
        self._geometry_writer = _ArchiveWriter(
            next_slot=self._scaffold_next_slot + material_shift + self._layer_shift() + self._definition_shift(),
            class_slot=self._post_definition_class_slot(),
        )

    def add_face(
        self,
        points: Sequence[Point3],
        material: Optional[int] = None,
        layer: Optional[int] = None,
        back_material: Optional[int] = None,
        hidden: bool = False,
        soft_edges: bool = False,
        smooth_edges: bool = False,
        hidden_edges: bool = False,
    ) -> None:
        """Add one planar face, defined by 3 or more coplanar points (in
        inches) forming a closed polygon in order - do not repeat the
        first point at the end.

        Vertices and edges are automatically shared with previously-added
        faces wherever a point's ``(x, y, z)`` coordinates match exactly
        (same float values) - build a connected mesh by reusing the same
        point tuples across `add_face` calls, not by re-deriving
        numerically-close-but-not-identical coordinates.

        ``material``/``back_material``, if given, are handles returned by
        `add_material` (or `add_texture_material`) - applied to the face's
        front/back side respectively. ``layer``, if given, is a handle
        returned by `add_layer`. Leave any unset for the default.

        ``hidden`` hides the face. ``soft_edges``/``smooth_edges``/
        ``hidden_edges`` control any edge newly created by this call (not
        one already shared with a previous face) - typical for a
        tessellated curved surface, where the seams between adjacent
        facets should shade smoothly and stay invisible.
        """
        points = [(float(p[0]), float(p[1]), float(p[2])) for p in points]
        if len(points) < 3:
            raise SkpWriteError("a face needs at least 3 points")
        self._ensure_geometry_writer()
        self._new_entity_count += self._geometry_writer.write_face(
            points, self._vertex_slots, self._edge_registry,
            material or 0, layer or 0, back_material or 0,
            hidden, soft_edges, smooth_edges, hidden_edges,
        )
        self._face_count += 1

    def to_bytes(self) -> bytes:
        """Return the finished file's bytes."""
        if self._face_count == 0:
            raise SkpWriteError("no geometry added - call add_face at least once before saving")

        # Every new-class declaration and every new object allocation each
        # consume one archive slot; next_slot already reflects the running
        # total, so each shift is just the delta since its writer started.
        material_shift = self._material_writer.next_slot - self._base
        layer_shift = self._layer_shift()
        definition_shift = self._definition_shift()
        geometry_initial_slot = self._scaffold_next_slot + material_shift + layer_shift + definition_shift
        geometry_shift = self._geometry_writer.next_slot - geometry_initial_slot
        new_root_count = self._orig_root_count + self._new_entity_count

        out = bytearray()

        # The 4 bytes right before the material insertion point are a
        # reserved (always-present) mat_count field - zero/implicit in the
        # zero-material scaffold, not a gap that needs new bytes inserted.
        # Real SketchUp overwrites them in place rather than growing the
        # file by 4 extra bytes here; ground-truth-confirmed by diffing SDK-
        # authored files (an earlier version of this method double-counted
        # this field as a fresh insertion, corrupting every offset after it).
        # Each layer's record embeds 2 pids (see write_layer); materials
        # use 1 pid each (write_material).
        layer_pids = (self._layer_writer.next_pid - 1) if self._layer_writer else 0
        pid_delta = self._material_count + layer_pids

        prefix = bytearray(self._data[: self._material_insert_pos - 4])
        if pid_delta:
            u16 = struct.unpack_from("<H", prefix, _PID_COUNTER_POS)[0]
            struct.pack_into("<H", prefix, _PID_COUNTER_POS, u16 + pid_delta)
        out += prefix
        out += _u32(self._material_count)
        out += self._material_writer.buf

        # material_insert_pos -> layer_insert_pos: Layer0 (and any other
        # already-existing layers) plus the layer_count field, unmodified
        # except for that count.
        middle1 = bytearray(self._data[self._material_insert_pos : self._layer_insert_pos])
        layer_count_rel = self._layer_count_pos - self._material_insert_pos
        struct.pack_into("<I", middle1, layer_count_rel, self._orig_layer_count + self._layer_count)
        out += middle1
        if self._layer_writer is not None:
            out += self._layer_writer.buf

        # layer_insert_pos -> def_count_pos: just the active-layer anchor,
        # which needs +material_shift (never +layer_shift - Layer0 itself
        # never moves just because more layers are appended after it).
        middle2a = bytearray(self._data[self._layer_insert_pos : self._def_count_pos])
        if material_shift:
            _shift_ref(middle2a, _ACTIVE_LAYER_ANCHOR_REL, material_shift)
        out += middle2a

        out += _u32(self._orig_def_count + self._definition_count)
        if self._definition_writer is not None:
            out += self._definition_writer.buf

        # def_count_pos+4 -> root_count_pos: any already-existing
        # definitions (none, in the blank scaffold), unmodified.
        out += self._data[self._def_count_pos + 4 : self._root_count_pos]

        out += _u32(new_root_count)
        out += self._data[self._root_count_pos + 4 : self._tail_pos]
        out += self._geometry_writer.buf

        tail = bytearray(self._data[self._tail_pos :])
        total_tail_shift = material_shift + layer_shift + definition_shift + geometry_shift
        for pos in _TAIL_REF_POSITIONS:
            _shift_ref(tail, pos, total_tail_shift)
        out += tail
        return bytes(out)

    def save(self, path: str) -> None:
        """Write the finished file to ``path``."""
        with open(path, "wb") as f:
            f.write(self.to_bytes())


def create() -> SkpBuilder:
    """Start building a new legacy-format (v17) ``.skp`` file from scratch.

    >>> builder = create()
    >>> red = builder.add_material("Red", (255, 0, 0))
    >>> roof = builder.add_layer("Roof")
    >>> builder.add_face([(0, 0, 0), (100, 0, 0), (100, 100, 0), (0, 100, 0)], material=red, layer=roof)
    >>> builder.save("output.skp")

    See the :mod:`openskp.create` module docstring for the current scope
    and limitations (no textures/components yet, inches only).
    """
    return SkpBuilder()
