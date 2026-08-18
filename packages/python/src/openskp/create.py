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
  PNG/JPEG-textured materials, named layers, reusable component
  definitions with multiple positioned instances, and groups are all
  supported - see :meth:`SkpBuilder.add_material` / :meth:`SkpBuilder.
  add_texture_material` / :meth:`SkpBuilder.add_layer` / :meth:`SkpBuilder.
  add_component_definition` / :meth:`SkpBuilder.add_group`. A definition
  can also nest instances (or group instances) of another, already-built
  definition inside its own body (an assembly containing its own
  sub-parts) via :meth:`ComponentDefinitionBuilder.add_instance` /
  :meth:`ComponentDefinitionBuilder.add_group_instance` - nested groups
  can't be declared inline the way :meth:`SkpBuilder.add_group` is at the
  root level (this format has no way to embed one definition's
  declaration inside another's), so build the group's geometry with a
  normal `add_component_definition` first, then place it as a group.
  A face's texture can be explicitly positioned (scaled/rotated/sheared/
  offset, independently per side) instead of the default planar
  projection, on a face of any orientation - see `write_face`'s
  ``front_uv``/``back_uv`` parameters. Component definitions, instances,
  and faces can also carry custom key/value metadata (``str``/``int``/
  ``float`` values) - the same mechanism SketchUp's own "dynamic
  component" attributes use - via each of their ``attributes`` parameters;
  not yet supported on groups (ground truth shows a group's own
  attribute pointer is always null, unlike a component instance's).
  Circular faces and partial (open) arcs - real, editable-by-radius
  SketchUp arc/circle entities, not disconnected straight edges that
  merely trace that shape - are supported via :meth:`SkpBuilder.
  add_circle` / :meth:`SkpBuilder.add_arc`, as are freeform polyline
  curves (``CCurve`` - a labeled grouping of straight edges, distinct
  from a true arc's own geometric frame) via :meth:`SkpBuilder.
  add_polyline`; all three have :class:`ComponentDefinitionBuilder`
  equivalents.
* Coordinates are in **inches** - SketchUp's own native internal unit for
  this era of the format. Converting from another unit is the caller's
  responsibility for now.
* Every file opens to the standard "Iso" view (parallel projection,
  looking at the origin from the (1, -1, 1) octant) rather than the
  blank scaffold's own arbitrary default camera - see
  ``_ISO_CAMERA_PREFIX_PATCH`` below. Not configurable yet.
* This module itself only ever builds a brand-new file from its own
  blank scaffold - it has no notion of an existing input file at all
  (real SketchUp does not simply append to a file on save, it
  re-serializes the whole document, so there is no stable "original
  bytes + appended bytes" structure to target the way there is for the
  blank scaffold below). :mod:`openskp.edit` builds on top of this
  module and :mod:`openskp.legacy` to load an *existing* legacy file by
  fully parsing it and replaying its content back through this module's
  own API - see that module's docstring for the exact scope and gaps.

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
import math
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

# The blank scaffold ships with SketchUp's own arbitrary default camera;
# every file this writer produces instead always patches it to the
# standard "Iso" view (eye along the (1, -1, 1) octant looking at the
# origin, up = Z, parallel/orthographic projection - matching Camera >
# Standard Views > Iso) so it opens already framed the conventional way,
# rather than whatever angle a brand-new blank document happens to
# default to. Found the same way as every other ground-truth constant
# here: diffing two SDK-authored blank documents that differ only in an
# explicit SUCameraSetOrientation + SUCameraSetPerspective(False) call
# before saving - these are the exact bytes real SketchUp itself wrote
# for that camera, copied verbatim rather than decoded (like
# _CAMERA_TEMPLATE, this project has not reverse-engineered CCamera's
# own internal field layout, only confirmed these specific byte ranges
# are what changes for this camera setting). The prefix offset is
# absolute (within the always-unshifted scaffold prefix, well before
# _material_insert_pos); the tail patches are relative to the document
# "tail" like _TAIL_REF_POSITIONS, since this camera setting also
# touches two small fields further into that region.
_ISO_CAMERA_PREFIX_OFFSET = 2993
_ISO_CAMERA_PREFIX_PATCH = bytes.fromhex(
    "594000000000000059c000000000000059400000000000000000000000000000"
    "000000000000000000003f2c0c70bd20dabf3f2c0c70bd20da3f3f2c0c70bd20"
    "ea3f000000000000f03f0000000000408f40000000000000003e402adf272c80"
    "3457"
)
_ISO_CAMERA_TAIL_PATCHES = (
    (509, bytes.fromhex("d0a869613c442d4799a4667d1adfa836")),
    (1390, bytes.fromhex("4e53c84477029246bba95827bba7e2")),
)

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
_GROUP_SCHEMA = 1
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

# Same pattern again: CAttributeNamed (one named key/value dictionary
# within an attribute container) is also pre-declared in the scaffold's
# own prefix, ground-truth confirmed fixed at slot 5 - found by attaching
# a real attribute dictionary to a face via the SDK's own
# SUEntityGetAttributeDictionary/SUAttributeDictionarySetValue and
# reading back where its class-ref pointed.
_ATTRIBUTE_NAMED_SLOT = 5

# CAttributeNamed's own value-type tags, ground-truth-and-reader-confirmed
# (legacy.py's _read_attr_named documents the full set this format
# supports; only the 3 most commonly useful ones for custom metadata are
# exposed by this writer for now - see write_attribute_dict).
_ATTR_TYPE_INT32 = 0x04
_ATTR_TYPE_DOUBLE = 0x06
_ATTR_TYPE_STRING = 0x0A

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

_FTC_SCHEMA = 4

_ARCCURVE_SCHEMA = 3

_CCURVE_SCHEMA = 4

# A face with no explicit texture positioning stores no CFaceTextureCoords
# at all, so this identity is only ever used to fill the *other* side's slot
# when just one of front/back is explicitly positioned - real SketchUp still
# writes a full 24-f64 block either way, just with the unpositioned side's
# matrix left as identity, ground-truth confirmed by positioning only one
# side and reading the other back as (1,0,0, 0,1,0, 0,0,1).
_IDENTITY_UV_MATRIX = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


def _det3(m: Sequence[Sequence[float]]) -> float:
    return (
        m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
    )


def _solve3x3(a: Sequence[Sequence[float]], b: Sequence[float]) -> Tuple[float, float, float]:
    """Solve the 3x3 linear system ``a @ x = b`` via Cramer's rule."""
    d = _det3(a)
    if abs(d) < 1e-9:
        raise SkpWriteError(
            "the 3 texture-positioning points map to collinear (u, v) coordinates - "
            "cannot determine a texture mapping from them"
        )
    cols = []
    for col in range(3):
        ai = [list(row) for row in a]
        for r in range(3):
            ai[r][col] = b[r]
        cols.append(_det3(ai) / d)
    return cols[0], cols[1], cols[2]


def _cross(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> Tuple[float, float, float]:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _normalize3(v: Tuple[float, float, float]) -> Tuple[float, float, float]:
    length = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) ** 0.5
    if length < 1e-9:
        raise SkpWriteError("cannot determine a texture-positioning basis: the face's first edge is degenerate")
    return (v[0] / length, v[1] / length, v[2] / length)


def _face_uv_basis(
    points: Sequence[Point3], normal: Tuple[float, float, float]
) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    """The in-plane 2D basis (U, W) real SketchUp uses to parameterize a
    face's texture mapping, for a face of ANY orientation (not just
    axis-aligned) - ground truth (an SDK-authored file's own computed
    matrix, cross-checked against this formula using an asymmetric
    correspondence specifically chosen to rule out simpler axis-dropping
    projections) shows it's simply the face's own first edge direction
    (``points[1] - points[0]``, normalized) as U, and the plane normal
    crossed with that as W - both unit vectors. This exactly explains why
    the axis-aligned case (this feature's first version) worked with
    fixed (x, y)/(x, z)/(y, z) axis pairs: for a face whose first edge
    happens to run along a world axis, this formula reduces to exactly
    that pair.
    """
    u = _normalize3((
        points[1][0] - points[0][0], points[1][1] - points[0][1], points[1][2] - points[0][2],
    ))
    w = _normalize3(_cross(normal, u))
    return u, w


def _circle_basis(
    normal: Tuple[float, float, float],
) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    """An arbitrary orthonormal in-plane basis (U, W) for a circle/arc's
    plane, given only its normal - unlike :func:`_face_uv_basis` there's
    no "first edge" to derive U from here, so pick whichever of world
    +Z/+X is less parallel to ``normal`` as a seed and Gram-Schmidt it
    against ``normal`` to get U, then W = normal x U. This choice of seed
    only affects where angle 0 points around the circle, not its shape.
    """
    seed = (0.0, 0.0, 1.0) if abs(normal[2]) < 0.9 else (1.0, 0.0, 0.0)
    dot = seed[0] * normal[0] + seed[1] * normal[1] + seed[2] * normal[2]
    u_raw = (seed[0] - dot * normal[0], seed[1] - dot * normal[1], seed[2] - dot * normal[2])
    u = _normalize3(u_raw)
    w = _normalize3(_cross(normal, u))
    return u, w


def _circle_points(
    center: Point3,
    normal: Tuple[float, float, float],
    radius: float,
    num_segments: int,
    u: Tuple[float, float, float],
    w: Tuple[float, float, float],
) -> List[Point3]:
    """The ``num_segments`` polygon vertices approximating a full circle
    in ``center``/``radius``/``normal``'s plane, walking counter-clockwise
    around ``normal`` (right-hand rule) starting at ``center + radius*u``
    - so the resulting face's own computed normal (Newell's method on
    these points, see :func:`_plane_from_polygon`) comes out parallel to
    ``normal``, not anti-parallel to it.
    """
    pts: List[Point3] = []
    for i in range(num_segments):
        angle = 2.0 * math.pi * i / num_segments
        c, s = math.cos(angle), math.sin(angle)
        pts.append((
            center[0] + radius * (c * u[0] + s * w[0]),
            center[1] + radius * (c * u[1] + s * w[1]),
            center[2] + radius * (c * u[2] + s * w[2]),
        ))
    return pts


def _arc_points(
    center: Point3,
    normal: Tuple[float, float, float],
    radius: float,
    num_segments: int,
    u: Tuple[float, float, float],
    w: Tuple[float, float, float],
    start_angle: float,
    end_angle: float,
) -> List[Point3]:
    """The ``num_segments + 1`` points (both endpoints included) tracing a
    PARTIAL arc from ``start_angle`` to ``end_angle`` (radians, measured
    from ``u`` toward ``w`` - the same convention :func:`_circle_points`
    and :meth:`_ArchiveWriter.write_arc_curve` use, so this is a strict
    generalization: ``_circle_points(...)`` is equivalent to
    ``_arc_points(..., 0.0, 2*pi)[:-1]``, the closing point dropped since
    a full circle's own last edge back to the start is implicit).
    """
    pts: List[Point3] = []
    for i in range(num_segments + 1):
        angle = start_angle + (end_angle - start_angle) * i / num_segments
        c, s = math.cos(angle), math.sin(angle)
        pts.append((
            center[0] + radius * (c * u[0] + s * w[0]),
            center[1] + radius * (c * u[1] + s * w[1]),
            center[2] + radius * (c * u[2] + s * w[2]),
        ))
    return pts


def _solve_uv_matrix(
    pairs: Sequence[Tuple[Point3, Tuple[float, float]]],
    basis: Tuple[Tuple[float, float, float], Tuple[float, float, float]],
) -> Tuple[float, ...]:
    """Fit the 3x3 UV-to-world affine matrix ground truth shows real
    SketchUp stores for a positioned texture, from exactly 3 (world point,
    (u, v)) correspondences - the minimum that fully determines an affine
    map (scale, rotation, shear, translation; no perspective/keystone term,
    matching the third column ground truth always shows as (0, 0, 1)).

    ``basis`` is the face's own (U, W) in-plane unit vectors (see
    `_face_uv_basis`) - each correspondence's world point is projected
    onto them via a plain dot product (ground truth confirms this uses
    the point's raw coordinates with no origin subtraction - confirmed by
    positioning a face far from the world origin, where an "obviously
    sensible" points[0]-relative hypothesis predicted the wrong
    translation terms) before fitting.

    Ground truth (see ``_read_ftc`` in legacy.py, which this inverts) shows
    the stored matrix satisfies ``(u, v, 1) @ M == (world_x, world_y, 1)``
    in row-vector convention - i.e. it maps a UV coordinate to the world
    point it should land on, which is the natural direction for *defining*
    a mapping (a caller says where each UV coordinate goes).
    """
    if len(pairs) != 3:
        raise SkpWriteError("texture positioning needs exactly 3 (point, uv) pairs")
    u_axis, w_axis = basis
    a = [[uv[0], uv[1], 1.0] for _, uv in pairs]
    bx = [pt[0] * u_axis[0] + pt[1] * u_axis[1] + pt[2] * u_axis[2] for pt, _ in pairs]
    by = [pt[0] * w_axis[0] + pt[1] * w_axis[1] + pt[2] * w_axis[2] for pt, _ in pairs]
    col_x = _solve3x3(a, bx)
    col_y = _solve3x3(a, by)
    a0, c0, e0 = col_x
    b0, d0, f0 = col_y
    return (a0, b0, 0.0, c0, d0, 0.0, e0, f0, 1.0)


def _uv_matrix_for_face(
    points: Sequence[Point3],
    pairs: Sequence[Tuple[Point3, Tuple[float, float]]],
    normal: Tuple[float, float, float],
) -> Tuple[float, ...]:
    """`_solve_uv_matrix` using the face's own `_face_uv_basis`."""
    return _solve_uv_matrix(pairs, _face_uv_basis(points, normal))


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


def _detect_image_subtype(image_bytes: bytes) -> int:
    """CDib's format tag for the two image formats this project has
    confirmed via SDK ground truth (diffing an SDK-authored textured
    material file for each) - PNG and JPEG, both real SketchUp encodes as
    the source file's bytes verbatim, distinguished only by this tag."""
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return 4
    if image_bytes[:3] == b"\xff\xd8\xff":
        return 1
    raise SkpWriteError(
        "unrecognized image format - only PNG and JPEG textures are supported for now "
        "(detected from the file's own magic bytes, not its extension)"
    )


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

    def _preamble_with_real_attrs(
        self,
        front_matrix: Optional[Tuple[float, ...]] = None,
        back_matrix: Optional[Tuple[float, ...]] = None,
        attribute_dicts: Sequence[Tuple[str, Dict[str, object]]] = (),
        pid: Optional[int] = None,
    ) -> None:
        """Like `_preamble(real_attrs=True)`, but the attribute container's
        children list holds real content instead of closing immediately:
        an optional `CFaceTextureCoords` (``front_matrix``/``back_matrix``
        - faces with explicit texture positioning only; a face with
        neither side positioned writes none, even if it has
        ``attribute_dicts``) followed by zero or more named
        `CAttributeNamed` dictionaries (``attribute_dicts``, custom
        key/value metadata - the same mechanism SketchUp's own "dynamic
        component" attributes use)."""
        self.buf += struct.pack("<H", 0x8000 | _ATTR_CONTAINER_SLOT)
        self._alloc()
        self.buf += bytes(3)  # the container's own nested preamble: null attrs (2) + mask=0 (1)
        if front_matrix is not None or back_matrix is not None:
            self.write_face_texture_coords(front_matrix, back_matrix)
        for dict_name, entries in attribute_dicts:
            self.write_attribute_dict(dict_name, entries)
        self._null()  # children-list terminator
        if pid is None:
            pid = self._alloc_pid()
        self.buf += self._encode_pid(pid)

    def _validate_attribute_entries(self, entries: Dict[str, object]) -> None:
        """Raise ``SkpWriteError`` for the first unsupported key/value in
        ``entries``, without writing anything - shares
        `write_attribute_dict`'s own exact validation rules so a caller
        can check every attribute dict a multi-part write will need
        BEFORE that write starts mutating ``self.buf`` (and any shared
        vertex/edge-sharing dicts a caller passes in), see `write_face`'s
        own upfront-validation comment for why that ordering matters.
        """
        for key, value in entries.items():
            if isinstance(value, str):
                continue
            if isinstance(value, bool):
                raise SkpWriteError(
                    f"attribute {key!r}: bool is not a supported value type - "
                    "use an int (0/1) if you need a boolean-like flag"
                )
            if isinstance(value, int):
                if not (-(2**31) <= value < 2**31):
                    raise SkpWriteError(f"attribute {key!r}: int value {value} out of signed 32-bit range")
                continue
            if isinstance(value, float):
                continue
            raise SkpWriteError(
                f"attribute {key!r}: unsupported value type {type(value).__name__} "
                "(only str, int, and float are supported for now)"
            )

    def write_attribute_dict(self, dict_name: str, entries: Dict[str, object]) -> None:
        """Write one ``CAttributeNamed`` record - a named dictionary of
        custom key/value metadata attached to an entity's real attribute
        container (the same mechanism SketchUp's own "dynamic component"
        attributes use). Inverts ``legacy._read_attr_named`` field-for-
        field; see that function's docstring for the full set of value
        types this format supports - only ``str``, ``int`` (32-bit
        signed), and ``float`` are exposed by this writer for now.

        Unlike every other class this project declares, ``CAttributeNamed``
        is already pre-declared in the scaffold's own prefix (ground
        truth: found by attaching a real attribute dictionary to a face
        via the SDK's own attribute-dictionary API and reading back where
        its class-ref pointed) - so this always writes a short class-ref
        to ``_ATTRIBUTE_NAMED_SLOT``, never a fresh ``0xFFFF`` declaration.
        """
        self.buf += struct.pack("<H", 0x8000 | _ATTRIBUTE_NAMED_SLOT)
        self._alloc()
        self.buf += bytes(3)  # this dict's own preamble: null attrs (2) + mask=0 (1), pid=0
        self.buf += _u32(0)  # ground truth: read and discarded by legacy.py's reader too
        self._validate_attribute_entries(entries)
        self._write_str(dict_name)
        for key, value in entries.items():
            self._write_str(key)
            if isinstance(value, str):
                self.buf.append(_ATTR_TYPE_STRING)
                self._write_str(value)
            elif isinstance(value, int):
                self.buf.append(_ATTR_TYPE_INT32)
                self.buf += struct.pack("<i", value)
            else:
                self.buf.append(_ATTR_TYPE_DOUBLE)
                self.buf += _f64(value)
        self._write_str("")  # empty-key terminator
        self.buf += _u32(0)  # ground truth: read and discarded by legacy.py's reader too

    def write_face_texture_coords(
        self,
        front_matrix: Optional[Tuple[float, ...]],
        back_matrix: Optional[Tuple[float, ...]],
    ) -> None:
        """Write one ``CFaceTextureCoords`` record - the explicit
        front/back texture-positioning data a face's attribute container
        holds when either side has been explicitly positioned (as opposed
        to the default planar projection, which needs no such record at
        all). Inverts ``legacy._read_ftc`` field-for-field; see that
        function's docstring for what each field means.

        ``front_matrix``/``back_matrix`` are the 9-value row-major
        UV-to-world affine matrices from `_uv_matrix_for_face`, or
        ``None`` for a side that isn't explicitly positioned (written as
        identity, matching ground truth for the untouched side).
        """
        self._new_of_known_class("CFaceTextureCoords", schema=_FTC_SCHEMA)
        self._preamble(pid=0)
        self.buf += _u32(0)  # ground truth: read and discarded by legacy.py's reader too
        ks = [0.0] * 24
        ks[0:9] = front_matrix if front_matrix is not None else _IDENTITY_UV_MATRIX
        ks[12:21] = back_matrix if back_matrix is not None else _IDENTITY_UV_MATRIX
        for v in ks:
            self.buf += _f64(v)
        self.buf += _u32(0)  # front pin count - this writer always emits a solved matrix, never raw pins
        self.buf += _u32(0)  # back pin count
        self.buf += _u32(1 if front_matrix is not None else 0)  # fflags bit 0: front painted/positioned
        self.buf += _u32(1 if back_matrix is not None else 0)  # bflags bit 0: back painted/positioned

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

    def write_arc_curve(
        self,
        center: Point3,
        normal: Tuple[float, float, float],
        xaxis: Tuple[float, float, float],
        start_angle: float,
        end_angle: float,
        radius: float,
        num_segments: int,
    ) -> int:
        """Write one ``CArcCurve`` record and return its slot - the shared
        geometric-parameter object a circle/arc's straight ``CEdge``
        segments each carry a backref to (via `write_face`'s
        ``curve_params``, which calls this inline as the first newly
        declared edge's own "curve" field - ground truth shows that's
        where a real SDK-authored file declares it, not as a standalone
        entity before the edges), so real SketchUp recognizes the result
        as a true circle/arc (editable by radius, re-tessellatable)
        rather than N disconnected straight edges that merely happen to
        form that shape.

        ``xaxis`` is the arc's own fixed 0-angle reference direction
        (a unit vector times ``radius``, in the plane perpendicular to
        ``normal``) - ``start_angle``/``end_angle`` (radians) are offsets
        from it, not the direction to the start point itself. Ground
        truth: found by creating full circles and partial arcs via the
        SDK's own ``SUGeometryInputAddArcCurve`` and reading back the
        resulting bytes - a full circle has ``start_angle=0``,
        ``end_angle=2*pi``. Two of the 14 stored values (ground truth
        offsets 11 and 13, interleaved with the fields above) were 0 in
        every sample tested and are written as 0 here too; their meaning
        hasn't been reverse-engineered.
        """
        if not (0 <= num_segments <= 0xFF):
            raise SkpWriteError(f"num_segments must be between 0 and 255, got {num_segments}")
        slot = self._new_of_known_class("CArcCurve", schema=_ARCCURVE_SCHEMA)
        self._preamble()
        self.buf += bytes([0, num_segments]) + bytes(3)
        for v in (*center, *normal, *xaxis, start_angle, end_angle, 0.0, radius, 0.0):
            self.buf += _f64(v)
        return slot

    def write_curve(self, num_edges: int) -> int:
        """Write one ``CCurve`` record and return its slot - a freeform
        polyline curve grouping (as opposed to ``CArcCurve``'s arc
        geometry): a labeled set of already-straight ``CEdge`` segments,
        with no geometric data of its own beyond how many edges share it.

        Ground truth (SDK-authored open and closed polylines, of several
        different edge counts, read back and byte-decoded): the record
        is just a 1-byte field - always ``1`` in every sample tested
        (open or closed), written as a constant here since its meaning
        (beyond a "this is a curve" type tag) hasn't been reverse-
        engineered - followed by ``num_edges`` as a u32, matching
        :mod:`openskp.legacy`'s own ``_read_curve`` shape exactly
        (``r.u8()`` then ``r.u32()``).
        """
        slot = self._new_of_known_class("CCurve", schema=_CCURVE_SCHEMA)
        self._preamble()
        self.buf += bytes([1]) + _u32(num_edges)
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
        format tag (4 for PNG, 1 for JPEG - see :func:`_detect_image_subtype`).
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
        if subtype == 1:
            # JPEG only: one extra u32 real SketchUp always writes here -
            # ground-truth confirmed constant 90 regardless of the source
            # JPEG's own actual encoded quality (tested at two different
            # qualities, same value both times), so not something this
            # project computes from the image; PNG has no such field.
            self.buf += _u32(90)
        self.buf += _f64(1.0)  # applied width - ground truth default when unscaled
        self.buf += _TEXTURE_H_SENTINEL
        self._write_str(texture_path)
        # avg color (RGBA + pad + RGBA repeated, per legacy.py's _read_material
        # comment) - neutral near-opaque white rather than a real image
        # average, since this project doesn't depend on an image library to
        # compute one. Ground truth confirms real SketchUp reads texture
        # pixels directly for rendering; avg only feeds the material
        # browser's thumbnail/tint preview. Alpha is 254, not a fully-opaque
        # 255: legacy.py's own reader treats alpha=255 here as one of its
        # two "this material is colorized" signals (`avg[3] == 0xFF`,
        # alongside the blob flag below) - a real SketchUp-authored
        # colorized material's stored average apparently always has full
        # alpha, but a PLAIN one's placeholder must not, or every plain
        # texture this writer creates reads back as falsely colorized.
        self.buf += bytes([255, 255, 255, 254, 0, 255, 255, 255, 254])
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

    def write_definition_header(
        self, attribute_dicts: Sequence[Tuple[str, Dict[str, object]]] = (),
    ) -> Tuple[int, int]:
        """Begin a ``CComponentDefinition`` record - everything up to (not
        including) its internal entity list. Returns ``(definition_slot,
        count_patch_pos)``: the caller writes the definition's geometry via
        further `write_face` calls (appended directly to ``self.buf``),
        then must patch a u32 entity count into ``self.buf`` at
        ``count_patch_pos`` and call `write_definition_tail` to close it out.

        ``attribute_dicts``, if given, is a sequence of ``(dict_name,
        entries)`` pairs - custom key/value metadata attached to this
        definition (the same mechanism SketchUp's own "dynamic component"
        attributes use).
        """
        slot = self._new_of_known_class("CComponentDefinition", schema=_DEFINITION_SCHEMA)
        if attribute_dicts:
            self._preamble_with_real_attrs(attribute_dicts=attribute_dicts)
        else:
            self._preamble(real_attrs=True)  # ground truth: a real pid and a real (empty) attr container
        self.buf += _DEFINITION_BASE_BLOCK
        self.buf += _u32(1)  # nlayers: always 1, an embedded copy of Layer0
        embedded_layer_slot = self.write_layer("Layer0", with_pids=False)
        self._backref(embedded_layer_slot)  # "decl": this definition's own active layer
        # A separate field from nested instances (which live in the entity
        # list just below, like any other entity) - ground truth shows this
        # counts CComponentDefinition classes declared inline within this
        # definition's own header, a distinct and rarer construct this
        # project has not needed: every definition this writer produces is
        # declared at the top level, so this stays 0 even when its entity
        # list below places instances of other top-level definitions.
        self.buf += _u32(0)
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

    def _write_instance_like(
        self,
        class_name: str,
        schema: int,
        real_attrs: bool,
        definition_slot: int,
        name: str,
        translation: Tuple[float, float, float],
        matrix3x3: Optional[Tuple[float, float, float, float, float, float, float, float, float]],
        mat: int,
        layer: int,
        attribute_dicts: Sequence[Tuple[str, Dict[str, object]]] = (),
    ) -> None:
        self._new_of_known_class(class_name, schema=schema)
        if real_attrs and attribute_dicts:
            self._preamble_with_real_attrs(attribute_dicts=attribute_dicts)
        else:
            self._preamble(real_attrs=real_attrs)
        self._drawbase(mat=mat, layer=layer)
        self._backref(definition_slot)
        if matrix3x3 is None:
            matrix3x3 = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
        for v in (*matrix3x3, *translation, 1.0):
            self.buf += _f64(v)
        self._write_str(name)
        self.buf += uuid.uuid4().bytes

    def write_instance(
        self,
        definition_slot: int,
        name: str,
        translation: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        matrix3x3: Optional[Tuple[float, float, float, float, float, float, float, float, float]] = None,
        instance_material: int = 0,
        instance_layer: int = 0,
        attribute_dicts: Sequence[Tuple[str, Dict[str, object]]] = (),
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

        ``attribute_dicts``, if given, is a sequence of ``(dict_name,
        entries)`` pairs - custom key/value metadata attached to this
        instance (the same mechanism SketchUp's own "dynamic component"
        attributes use). Not available on `write_group` - ground truth
        shows a group's attribute pointer is always null, unlike a
        component instance's real (if often empty) container.
        """
        # ground truth: instances also carry a real (empty) attr container, unlike CGroup
        self._write_instance_like(
            "CComponentInstance", _INSTANCE_SCHEMA, True,
            definition_slot, name, translation, matrix3x3, instance_material, instance_layer,
            attribute_dicts,
        )
        return 1

    def write_group(
        self,
        definition_slot: int,
        name: str,
        translation: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        matrix3x3: Optional[Tuple[float, float, float, float, float, float, float, float, float]] = None,
        group_material: int = 0,
        group_layer: int = 0,
    ) -> int:
        """Write one ``CGroup`` placing a copy of ``definition_slot`` and
        return how many new root-entity-list slots it consumed - always 1,
        same contract as `write_instance`.

        A group is structurally almost identical to a component instance
        (same preamble/drawbase/def-backref/transform/name/guid shape,
        confirmed via SDK ground truth) - the two real differences are its
        class name/schema (CGroup, schema 1) and that - unlike
        CComponentInstance - it uses a plain null attribute pointer rather
        than the real (empty) CAttributeContainer instances need.
        """
        self._write_instance_like(
            "CGroup", _GROUP_SCHEMA, False,
            definition_slot, name, translation, matrix3x3, group_material, group_layer,
        )
        return 1

    def _write_edge_chain(
        self,
        points: Sequence[Point3],
        vertex_slots: Dict[Point3, int],
        edge_registry: Dict[FrozenSet[int], Tuple[int, int]],
        closed: bool,
        hidden_edges: bool = False,
        soft_edges: bool = False,
        smooth_edges: bool = False,
        curve_params: Optional[
            Tuple[Point3, Tuple[float, float, float], Tuple[float, float, float], float, float, float, int]
        ] = None,
        polyline_num_edges: Optional[int] = None,
    ) -> Tuple[List[int], List[int], int]:
        """Write a chain of straight ``CEdge`` records connecting ``points``
        in order, sharing vertices/edges via ``vertex_slots``/
        ``edge_registry`` exactly like `write_face` (which uses this for
        its own, always-``closed`` polygon boundary) - ``closed=True``
        also connects the last point back to the first (an ``n``-edge
        loop); ``closed=False`` stops after the last pair (an ``n-1``-edge
        open chain, for a partial arc or polyline curve with no face).

        Returns ``(edge_slots, edge_senses, new_entities)`` - the last is
        how many new root-entity-list slots were consumed (edges newly
        declared; the caller adds any of its own, e.g. a face record).

        At most one of ``curve_params``/``polyline_num_edges`` should be
        given - both describe the SAME first-use-inline-declaration
        pattern (ground truth shows the shared curve object is declared
        inline as the FIRST newly-declared edge's own "curve" field, and
        every other edge newly declared by this call backrefs that same
        slot instead of writing a null curve), just for two different
        curve record types: ``curve_params`` is a ``(center, normal,
        xaxis, start_angle, end_angle, radius, num_segments)`` tuple for
        :meth:`write_arc_curve` (a circle/arc); ``polyline_num_edges`` is
        the edge count :meth:`write_curve` needs (a freeform polyline).
        """
        n = len(points)
        pair_count = n if closed else n - 1
        point_slots = [vertex_slots.get(p) for p in points]
        edge_slots: List[int] = []
        edge_senses: List[int] = []
        new_entities = 0
        curve_slot: Optional[int] = None

        for i in range(pair_count):
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
            if curve_slot is not None:
                self._backref(curve_slot)
            elif curve_params is not None:
                curve_slot = self.write_arc_curve(*curve_params)
            elif polyline_num_edges is not None:
                curve_slot = self.write_curve(polyline_num_edges)
            else:
                self._null()  # curve = None
            edge_slots.append(edge_slot)
            edge_senses.append(0)
            new_entities += 1
            edge_registry[frozenset((point_slots[v1_idx], point_slots[v2_idx]))] = (
                edge_slot,
                point_slots[v1_idx],
            )

        return edge_slots, edge_senses, new_entities

    def write_arc(
        self,
        points: Sequence[Point3],
        vertex_slots: Dict[Point3, int],
        edge_registry: Dict[FrozenSet[int], Tuple[int, int]],
        curve_params: Tuple[Point3, Tuple[float, float, float], Tuple[float, float, float], float, float, float, int],
        hidden_edges: bool = False,
        soft_edges: bool = False,
        smooth_edges: bool = False,
    ) -> int:
        """Write a partial (open) arc as a chain of straight ``CEdge``
        records - no face, unlike `write_face`'s always-closed polygon
        boundary. ``points`` are the ``num_segments + 1`` points along the
        arc in order (see :func:`_arc_points`); ``curve_params`` are the
        same args :meth:`write_arc_curve` needs, shared by every edge here
        exactly like `write_face`'s ``curve_params``. Returns how many new
        root-entity-list slots were consumed (edges newly declared).
        """
        _, _, new_entities = self._write_edge_chain(
            points, vertex_slots, edge_registry, False,
            hidden_edges, soft_edges, smooth_edges, curve_params,
        )
        return new_entities

    def write_polyline(
        self,
        points: Sequence[Point3],
        vertex_slots: Dict[Point3, int],
        edge_registry: Dict[FrozenSet[int], Tuple[int, int]],
        closed: bool = False,
        hidden_edges: bool = False,
        soft_edges: bool = False,
        smooth_edges: bool = False,
    ) -> int:
        """Write a freeform polyline curve - a chain of straight ``CEdge``
        records connecting ``points`` in order, all sharing one ``CCurve``
        grouping (see :meth:`write_curve`), no face. ``closed=True``
        additionally connects the last point back to the first. Distinct
        from `write_arc`: there's no geometric arc frame here, just a
        labeled set of already-straight edges - the same grouping real
        SketchUp's own Freehand/multi-segment-line tools produce.
        Returns how many new root-entity-list slots were consumed (edges
        newly declared; the ``CCurve`` itself is declared inline as the
        first one's own "curve" field, so it doesn't consume a separate
        slot - the same pattern `write_arc`/`write_face` use for
        ``CArcCurve``).
        """
        n = len(points)
        pair_count = n if closed else n - 1
        _, _, new_entities = self._write_edge_chain(
            points, vertex_slots, edge_registry, closed,
            hidden_edges, soft_edges, smooth_edges,
            polyline_num_edges=pair_count,
        )
        return new_entities

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
        front_uv: Optional[Sequence[Tuple[Point3, Tuple[float, float]]]] = None,
        back_uv: Optional[Sequence[Tuple[Point3, Tuple[float, float]]]] = None,
        attribute_dicts: Sequence[Tuple[str, Dict[str, object]]] = (),
        curve_params: Optional[
            Tuple[Point3, Tuple[float, float, float], Tuple[float, float, float], float, float, float, int]
        ] = None,
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

        ``front_uv``/``back_uv``, if given, explicitly position that
        side's texture instead of the default planar projection - exactly
        3 ``(point, (u, v))`` pairs (a world point on the face's plane
        paired with the texture coordinate it should land on), which fully
        determines an affine mapping (scale/rotation/shear/translation, no
        perspective). Works on a face of any orientation, not just
        axis-aligned ones. See :meth:`SkpBuilder.add_face` for a worked
        example.

        ``attribute_dicts``, if given, is a sequence of ``(dict_name,
        entries)`` pairs - custom key/value metadata attached to this
        face (``entries`` values may be ``str``, ``int``, or ``float``).

        ``curve_params``, if given, is a ``(center, normal, xaxis,
        start_angle, end_angle, radius, num_segments)`` tuple - the args
        :meth:`write_arc_curve` needs. Ground truth (an SDK-authored
        circle's own byte layout) shows the shared ``CArcCurve`` is
        declared inline as the FIRST newly-declared edge's own "curve"
        field (the same first-use-inline-declaration pattern
        :meth:`_write_vertex` already follows for vertices) - every
        OTHER edge newly declared by this call then backrefs that same
        slot instead of writing a null curve. An edge already shared
        with a previous face is left alone either way.
        """
        # Validate everything that CAN fail (a degenerate UV correspondence,
        # an unsupported attribute value) before writing a single byte or
        # touching vertex_slots/edge_registry below - _write_edge_chain
        # mutates both this writer's own buffer AND those caller-owned,
        # shared-across-calls dicts as it goes, with no rollback if
        # something later in this method raises; a caller that catches the
        # exception and tries to keep building (e.g. skipping one bad face
        # while replaying many) would otherwise be left with orphaned,
        # uncounted edges silently corrupting the rest of the file - a real
        # bug found via exactly that usage pattern (see openskp.edit).
        nx, ny, nz, d = _plane_from_polygon(points)
        front_matrix = _uv_matrix_for_face(points, front_uv, (nx, ny, nz)) if front_uv is not None else None
        back_matrix = _uv_matrix_for_face(points, back_uv, (nx, ny, nz)) if back_uv is not None else None
        for _, entries in attribute_dicts:
            self._validate_attribute_entries(entries)

        edge_slots, edge_senses, new_entities = self._write_edge_chain(
            points, vertex_slots, edge_registry, True,
            hidden_edges, soft_edges, smooth_edges, curve_params,
        )

        self._new_of_known_class("CFace", schema=3)
        if front_uv is not None or back_uv is not None or attribute_dicts:
            self._preamble_with_real_attrs(front_matrix, back_matrix, attribute_dicts)
        else:
            self._preamble()
        self._drawbase(mat=face_material, layer=face_layer, hidden=hidden)
        self.buf += _f64(nx) + _f64(ny) + _f64(nz) + _f64(d)
        self.buf += _u32(1)  # nloops = 1

        loop_slot = self._new_of_known_class("CLoop", schema=1)
        self._preamble(pid=0)  # structural object: ground truth uses pid 0
        # legacy.py's reader treats these 2 bytes as opaque (_read_loop just
        # does r.raw(2)), but real SketchUp requires 01 01, not 00 00 - same
        # silent-drop failure mode as the drawbase padding above.
        self.buf += bytes([1, 1])

        for i in range(len(edge_slots)):
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
    """Accumulates one component/group definition's geometry. Construct via
    :meth:`SkpBuilder.add_component_definition` or :meth:`SkpBuilder.
    add_group`, not directly - use it as a context manager. A component
    definition needs a separate :meth:`SkpBuilder.add_instance` call per
    placement; a group places itself automatically when its ``with`` block
    exits.

    >>> with builder.add_component_definition("Chair") as chair:
    ...     chair.add_face([(0, 0, 0), (20, 0, 0), (20, 20, 0), (0, 20, 0)])
    >>> builder.add_instance(chair, translation=(100, 0, 0))
    """

    def __init__(
        self, skp: "SkpBuilder", slot: int, name: str, count_patch_pos: int,
        group_placement: Optional[Tuple[Tuple[float, float, float], Optional[Tuple[float, ...]], int, int]] = None,
    ):
        self._skp = skp
        self.slot = slot
        self.name = name
        self._count_patch_pos = count_patch_pos
        self._vertex_slots: Dict[Point3, int] = {}
        self._edge_registry: Dict[FrozenSet[int], Tuple[int, int]] = {}
        self._new_entity_count = 0
        self._closed = False
        # set only by SkpBuilder.add_group - a group places itself
        # immediately on close, unlike a plain component definition, which
        # needs an explicit later add_instance call.
        self._group_placement = group_placement

    def _check_writable(self, action: str) -> None:
        if self._closed:
            raise SkpWriteError(
                f"component definition {self.name!r} has already closed "
                f"(its `with` block exited) - cannot add more {action} to it"
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
        front_uv: Optional[Sequence[Tuple[Point3, Tuple[float, float]]]] = None,
        back_uv: Optional[Sequence[Tuple[Point3, Tuple[float, float]]]] = None,
        attributes: Optional[Dict[str, object]] = None,
        attribute_dict_name: str = "attributes",
    ) -> None:
        """Add one planar face to this definition - same signature and
        behavior as :meth:`SkpBuilder.add_face`, except vertices/edges are
        shared only within this definition, never with the root model or
        other definitions."""
        self._check_writable("faces")
        points = [(float(p[0]), float(p[1]), float(p[2])) for p in points]
        if len(points) < 3:
            raise SkpWriteError("a face needs at least 3 points")
        attribute_dicts = [(attribute_dict_name, attributes)] if attributes else []
        self._new_entity_count += self._skp._definition_writer.write_face(
            points, self._vertex_slots, self._edge_registry,
            material or 0, layer or 0, back_material or 0,
            hidden, soft_edges, smooth_edges, hidden_edges,
            front_uv, back_uv, attribute_dicts,
        )

    def add_circle(
        self,
        center: Point3,
        normal: Tuple[float, float, float],
        radius: float,
        num_segments: int = 24,
        material: Optional[int] = None,
        layer: Optional[int] = None,
        back_material: Optional[int] = None,
        hidden: bool = False,
        front_uv: Optional[Sequence[Tuple[Point3, Tuple[float, float]]]] = None,
        back_uv: Optional[Sequence[Tuple[Point3, Tuple[float, float]]]] = None,
        attributes: Optional[Dict[str, object]] = None,
        attribute_dict_name: str = "attributes",
    ) -> None:
        """Add one circular face to this definition - same signature and
        behavior as :meth:`SkpBuilder.add_circle`, except vertices/edges
        are shared only within this definition."""
        self._check_writable("faces")
        if not (3 <= num_segments <= 255):
            raise SkpWriteError(f"num_segments must be between 3 and 255, got {num_segments}")
        center = (float(center[0]), float(center[1]), float(center[2]))
        normal = _normalize3((float(normal[0]), float(normal[1]), float(normal[2])))
        radius = float(radius)
        writer = self._skp._definition_writer
        u, w = _circle_basis(normal)
        xaxis = (radius * u[0], radius * u[1], radius * u[2])
        curve_params = (center, normal, xaxis, 0.0, 2.0 * math.pi, radius, num_segments)
        points = _circle_points(center, normal, radius, num_segments, u, w)
        attribute_dicts = [(attribute_dict_name, attributes)] if attributes else []
        self._new_entity_count += writer.write_face(
            points, self._vertex_slots, self._edge_registry,
            material or 0, layer or 0, back_material or 0,
            hidden, False, False, False,
            front_uv, back_uv, attribute_dicts,
            curve_params=curve_params,
        )

    def add_arc(
        self,
        center: Point3,
        normal: Tuple[float, float, float],
        radius: float,
        start_angle: float,
        end_angle: float,
        num_segments: int = 24,
        hidden_edges: bool = False,
        soft_edges: bool = False,
        smooth_edges: bool = False,
    ) -> None:
        """Add one partial (open) arc to this definition - same signature
        and behavior as :meth:`SkpBuilder.add_arc`, except vertices/edges
        are shared only within this definition."""
        self._check_writable("arcs")
        if not (3 <= num_segments <= 255):
            raise SkpWriteError(f"num_segments must be between 3 and 255, got {num_segments}")
        if end_angle == start_angle:
            raise SkpWriteError("start_angle and end_angle must differ - use add_circle for a full circle")
        center = (float(center[0]), float(center[1]), float(center[2]))
        normal = _normalize3((float(normal[0]), float(normal[1]), float(normal[2])))
        radius = float(radius)
        writer = self._skp._definition_writer
        u, w = _circle_basis(normal)
        xaxis = (radius * u[0], radius * u[1], radius * u[2])
        curve_params = (center, normal, xaxis, float(start_angle), float(end_angle), radius, num_segments)
        points = _arc_points(center, normal, radius, num_segments, u, w, float(start_angle), float(end_angle))
        self._new_entity_count += writer.write_arc(
            points, self._vertex_slots, self._edge_registry, curve_params,
            hidden_edges, soft_edges, smooth_edges,
        )

    def add_polyline(
        self,
        points: Sequence[Point3],
        closed: bool = False,
        hidden_edges: bool = False,
        soft_edges: bool = False,
        smooth_edges: bool = False,
    ) -> None:
        """Add one freeform polyline curve to this definition - same
        signature and behavior as :meth:`SkpBuilder.add_polyline`, except
        vertices/edges are shared only within this definition."""
        self._check_writable("polylines")
        points = [(float(p[0]), float(p[1]), float(p[2])) for p in points]
        if len(points) < 2:
            raise SkpWriteError("a polyline needs at least 2 points")
        self._new_entity_count += self._skp._definition_writer.write_polyline(
            points, self._vertex_slots, self._edge_registry,
            closed, hidden_edges, soft_edges, smooth_edges,
        )

    def add_instance(
        self,
        definition: "ComponentDefinitionBuilder",
        name: Optional[str] = None,
        translation: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        matrix3x3: Optional[Tuple[float, float, float, float, float, float, float, float, float]] = None,
        material: Optional[int] = None,
        layer: Optional[int] = None,
        attributes: Optional[Dict[str, object]] = None,
        attribute_dict_name: str = "attributes",
    ) -> None:
        """Place one instance of another, already-closed component
        definition inside this one - the same nesting real SketchUp
        supports (an assembly definition containing instances of its own
        sub-part definitions), same signature and behavior as
        :meth:`SkpBuilder.add_instance` otherwise.

        >>> with builder.add_component_definition("Wheel") as wheel:
        ...     wheel.add_face([(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)])
        >>> with builder.add_component_definition("Car") as car:
        ...     car.add_instance(wheel, translation=(0, 0, 0))
        ...     car.add_instance(wheel, translation=(100, 0, 0))

        ``definition`` must come from this same builder - a definition
        from a different `create()` call has a slot number that means
        nothing in this document. It is always already closed by the time
        it's valid to pass here: only one definition can be open on a
        given builder at once (see `add_component_definition`), and that
        one is always ``self`` while its own `with` block is active - so
        any *other* definition from this builder reachable here was
        necessarily closed before ``self`` was even opened. That
        ordering is also what rules out cycles: a definition can only
        ever nest others fully built strictly before it existed, never
        itself or anything still in progress.
        """
        self._check_writable("instances")
        if definition._skp is not self._skp:
            raise SkpWriteError(
                f"component definition {definition.name!r} belongs to a different "
                "builder (a different create() call) - its slot number is meaningless here"
            )
        if definition is self:
            raise SkpWriteError(f"component definition {self.name!r} cannot nest an instance of itself")
        attribute_dicts = [(attribute_dict_name, attributes)] if attributes else []
        self._new_entity_count += self._skp._definition_writer.write_instance(
            definition.slot, name or definition.name, translation, matrix3x3, material or 0, layer or 0,
            attribute_dicts,
        )

    def add_group_instance(
        self,
        definition: "ComponentDefinitionBuilder",
        name: Optional[str] = None,
        translation: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        matrix3x3: Optional[Tuple[float, float, float, float, float, float, float, float, float]] = None,
        material: Optional[int] = None,
        layer: Optional[int] = None,
    ) -> None:
        """Place another, already-closed component definition inside this
        one as a *group* (``CGroup``) rather than a component instance -
        otherwise identical to `add_instance`, including the same
        already-closed/same-builder/no-self-reference requirements.

        Unlike the self-placing :meth:`SkpBuilder.add_group` at the root
        level, a nested group can't be declared inline: this format has
        no way to embed one definition's declaration inside another's -
        every definition is a flat, top-level record, and a definition
        can only reference others already fully closed strictly before
        it was opened, the same constraint `add_instance` relies on for
        cycle-safety. So build the group's geometry with a normal
        `add_component_definition` first, then place it here:

        >>> with builder.add_component_definition("Engine") as engine:
        ...     engine.add_face([(0, 0, 0), (30, 0, 0), (30, 30, 0), (0, 30, 0)])
        >>> with builder.add_component_definition("Car") as car:
        ...     car.add_face([(0, 0, 0), (150, 0, 0), (150, 60, 0), (0, 60, 0)])
        ...     car.add_group_instance(engine, translation=(50, 0, 10))
        """
        self._check_writable("groups")
        if definition._skp is not self._skp:
            raise SkpWriteError(
                f"component definition {definition.name!r} belongs to a different "
                "builder (a different create() call) - its slot number is meaningless here"
            )
        if definition is self:
            raise SkpWriteError(f"component definition {self.name!r} cannot nest a group instance of itself")
        self._new_entity_count += self._skp._definition_writer.write_group(
            definition.slot, name or definition.name, translation, matrix3x3, material or 0, layer or 0
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
        if self._group_placement is not None:
            # Deferred rather than written here: writing immediately would
            # call _ensure_geometry_writer() and lock in root-level slot
            # numbering right away, which would wrongly reject any
            # further add_group/add_component_definition call after this
            # one - group placements are flushed together the first time
            # anything actually needs the geometry writer (see
            # _ensure_geometry_writer and to_bytes).
            self._skp._pending_groups.append((self, self._group_placement))
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
        self._pending_groups: List[Tuple["ComponentDefinitionBuilder", tuple]] = []
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
        """Register an image-textured material from a local PNG or JPEG
        file and return a handle to pass as `add_face`'s ``material``
        argument.

        The format is detected from the file's own magic bytes, not its
        extension - PNG and JPEG are the only two this project has
        confirmed the on-disk ``CDib`` subtype tag for via SDK ground
        truth (4 and 1 respectively; see :meth:`_ArchiveWriter.
        write_textured_material`). UV mapping is always the default planar
        projection; explicit positioning/pinning is not supported (ground
        truth shows the default case needs no extra per-face
        texture-coordinate record at all, which is what keeps this scoped
        as an addition to materials rather than a much larger
        face-attribute feature).

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
        with open(image_path, "rb") as f:
            image_bytes = f.read()
        subtype = _detect_image_subtype(image_bytes)
        slot = self._material_writer.write_textured_material(name, image_bytes, image_path, subtype=subtype)
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

    def _start_definition(
        self, name: str, caller: str,
        group_placement: Optional[Tuple[Tuple[float, float, float], Optional[Tuple[float, ...]], int, int]] = None,
        attribute_dicts: Sequence[Tuple[str, Dict[str, object]]] = (),
    ) -> "ComponentDefinitionBuilder":
        if self._geometry_writer is not None:
            raise SkpWriteError(f"{caller} must be called before any add_face/add_instance calls")
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
        slot, count_patch_pos = self._definition_writer.write_definition_header(attribute_dicts)
        self._definition_count += 1
        comp = ComponentDefinitionBuilder(self, slot, name, count_patch_pos, group_placement)
        self._open_definition = comp
        return comp

    def add_component_definition(
        self, name: str,
        attributes: Optional[Dict[str, object]] = None,
        attribute_dict_name: str = "attributes",
    ) -> "ComponentDefinitionBuilder":
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

        ``attributes``, if given, is custom key/value metadata (values
        may be ``str``, ``int``, or ``float``) attached to the definition
        itself, under a dictionary named ``attribute_dict_name`` - the
        same mechanism SketchUp's own "dynamic component" attributes use.
        """
        attribute_dicts = [(attribute_dict_name, attributes)] if attributes else []
        return self._start_definition(name, "add_component_definition", attribute_dicts=attribute_dicts)

    def add_group(
        self,
        name: Optional[str] = None,
        translation: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        matrix3x3: Optional[Tuple[float, float, float, float, float, float, float, float, float]] = None,
        material: Optional[int] = None,
        layer: Optional[int] = None,
    ) -> "ComponentDefinitionBuilder":
        """Start a new group. Use the returned object as a context manager,
        adding its geometry via `.add_face` inside the ``with`` block - the
        group is placed at ``translation``/``matrix3x3`` automatically when
        the block exits, unlike `add_component_definition` there is no
        separate placement call, matching how groups are normally used
        (defined and placed once, not reused across multiple positions).

        >>> with builder.add_group("Table", translation=(50, 0, 0)) as table:
        ...     table.add_face([(0, 0, 0), (30, 0, 0), (30, 30, 0), (0, 30, 0)])

        Same ordering rule as `add_component_definition` - must be called
        before any `add_face`/`add_instance`/`add_group` call already in
        progress on the builder itself.
        """
        return self._start_definition(
            name or "Group", "add_group", group_placement=(translation, matrix3x3, material or 0, layer or 0)
        )

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
        attributes: Optional[Dict[str, object]] = None,
        attribute_dict_name: str = "attributes",
    ) -> None:
        """Place one instance of ``definition`` (from
        `add_component_definition`, already closed) in the model.

        ``matrix3x3`` is a row-major 3x3 rotation/scale matrix (identity if
        omitted); ``translation`` is applied after it, in inches.
        ``material``/``layer``, if given, are handles from `add_material`/
        `add_layer` applied to the instance itself (not its contents).

        ``attributes``, if given, is custom key/value metadata (values
        may be ``str``, ``int``, or ``float``) attached to this instance
        specifically (as opposed to its definition), under a dictionary
        named ``attribute_dict_name`` - the same mechanism SketchUp's own
        "dynamic component" attributes use for per-instance overrides.
        """
        if definition._skp is not self:
            raise SkpWriteError(
                f"component definition {definition.name!r} belongs to a different "
                "builder (a different create() call) - its slot number is meaningless here"
            )
        if not definition._closed:
            raise SkpWriteError(
                f"component definition {definition.name!r} is still open - "
                "exit its `with` block before calling add_instance"
            )
        self._ensure_geometry_writer()
        attribute_dicts = [(attribute_dict_name, attributes)] if attributes else []
        self._new_entity_count += self._geometry_writer.write_instance(
            definition.slot, name or definition.name, translation, matrix3x3, material or 0, layer or 0,
            attribute_dicts,
        )
        self._face_count += 1  # reuses the "at least one root entity" check in to_bytes

    def _ensure_geometry_writer(self) -> None:
        if self._geometry_writer is not None:
            return
        if self._open_definition is not None:
            # Calling this while a definition/group is still open would
            # lock in the geometry writer's starting slot before that
            # definition (and anything added to it afterward) finishes
            # growing _definition_writer - the locked-in slot would then be
            # too low, corrupting every back-reference root-level geometry
            # makes. A real, previously-unguarded gap: nothing stopped
            # calling add_face/add_instance on the root builder from
            # inside an open `with add_component_definition(...)` block,
            # which silently produced a file whose root entities didn't
            # parse (found while testing group nesting - not related to
            # it otherwise).
            raise SkpWriteError(
                f"component definition {self._open_definition.name!r} is still open - "
                "exit its `with` block before adding root-level geometry"
            )
        material_shift = self._material_writer.next_slot - self._base
        self._geometry_writer = _ArchiveWriter(
            next_slot=self._scaffold_next_slot + material_shift + self._layer_shift() + self._definition_shift(),
            class_slot=self._post_definition_class_slot(),
        )
        # Flush any groups that closed earlier, in the order they were
        # created - deferred until now so closing one group doesn't lock in
        # root-level slot numbering before a later add_group/
        # add_component_definition call has had a chance to run.
        for comp, (translation, matrix3x3, mat, layer) in self._pending_groups:
            self._new_entity_count += self._geometry_writer.write_group(
                comp.slot, comp.name, translation, matrix3x3, mat, layer
            )
            self._face_count += 1
        self._pending_groups = []

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
        front_uv: Optional[Sequence[Tuple[Point3, Tuple[float, float]]]] = None,
        back_uv: Optional[Sequence[Tuple[Point3, Tuple[float, float]]]] = None,
        attributes: Optional[Dict[str, object]] = None,
        attribute_dict_name: str = "attributes",
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

        ``front_uv``/``back_uv``, if given, explicitly position that
        side's texture instead of the default planar projection: exactly
        3 ``(point, (u, v))`` pairs, each a world point on the face paired
        with the texture coordinate that should land there. Works on a
        face of any orientation, not just axis-aligned ones.

        >>> brick = builder.add_texture_material("Brick", "brick.png")
        >>> builder.add_face(
        ...     [(0, 0, 0), (100, 0, 0), (100, 100, 0), (0, 100, 0)],
        ...     material=brick,
        ...     front_uv=[
        ...         ((0, 0, 0), (0.0, 0.0)),
        ...         ((50, 0, 0), (1.0, 0.0)),
        ...         ((0, 50, 0), (0.0, 1.0)),
        ...     ],
        ... )

        ``attributes``, if given, is custom key/value metadata (values
        may be ``str``, ``int``, or ``float``) attached to this face,
        under a dictionary named ``attribute_dict_name``.
        """
        points = [(float(p[0]), float(p[1]), float(p[2])) for p in points]
        if len(points) < 3:
            raise SkpWriteError("a face needs at least 3 points")
        self._ensure_geometry_writer()
        attribute_dicts = [(attribute_dict_name, attributes)] if attributes else []
        self._new_entity_count += self._geometry_writer.write_face(
            points, self._vertex_slots, self._edge_registry,
            material or 0, layer or 0, back_material or 0,
            hidden, soft_edges, smooth_edges, hidden_edges,
            front_uv, back_uv, attribute_dicts,
        )
        self._face_count += 1

    def add_circle(
        self,
        center: Point3,
        normal: Tuple[float, float, float],
        radius: float,
        num_segments: int = 24,
        material: Optional[int] = None,
        layer: Optional[int] = None,
        back_material: Optional[int] = None,
        hidden: bool = False,
        front_uv: Optional[Sequence[Tuple[Point3, Tuple[float, float]]]] = None,
        back_uv: Optional[Sequence[Tuple[Point3, Tuple[float, float]]]] = None,
        attributes: Optional[Dict[str, object]] = None,
        attribute_dict_name: str = "attributes",
    ) -> None:
        """Add one circular face - a true SketchUp circle (editable by
        radius, re-tessellatable, selectable as a single "Curve" entity),
        not ``num_segments`` disconnected straight edges that merely
        happen to trace that shape.

        ``center``/``radius`` are in inches; ``normal`` is the circle's
        plane normal (need not be a unit vector - it's normalized
        automatically), also the resulting face's front-side normal.
        ``num_segments`` (3-255) controls tessellation, matching
        SketchUp's own circle tool default of 24.

        ``material``/``back_material``/``layer``/``hidden``/`front_uv`/
        ``back_uv``/``attributes``/``attribute_dict_name`` are the same
        as :meth:`add_face`.

        >>> builder.add_circle((50, 50, 0), (0, 0, 1), radius=40)
        """
        if not (3 <= num_segments <= 255):
            raise SkpWriteError(f"num_segments must be between 3 and 255, got {num_segments}")
        center = (float(center[0]), float(center[1]), float(center[2]))
        normal = _normalize3((float(normal[0]), float(normal[1]), float(normal[2])))
        radius = float(radius)
        self._ensure_geometry_writer()
        u, w = _circle_basis(normal)
        xaxis = (radius * u[0], radius * u[1], radius * u[2])
        curve_params = (center, normal, xaxis, 0.0, 2.0 * math.pi, radius, num_segments)
        points = _circle_points(center, normal, radius, num_segments, u, w)
        attribute_dicts = [(attribute_dict_name, attributes)] if attributes else []
        self._new_entity_count += self._geometry_writer.write_face(
            points, self._vertex_slots, self._edge_registry,
            material or 0, layer or 0, back_material or 0,
            hidden, False, False, False,
            front_uv, back_uv, attribute_dicts,
            curve_params=curve_params,
        )
        self._face_count += 1

    def add_arc(
        self,
        center: Point3,
        normal: Tuple[float, float, float],
        radius: float,
        start_angle: float,
        end_angle: float,
        num_segments: int = 24,
        hidden_edges: bool = False,
        soft_edges: bool = False,
        smooth_edges: bool = False,
    ) -> None:
        """Add one partial (open) arc - a genuine SketchUp arc entity
        (editable by radius/angle, re-tessellatable), not disconnected
        straight edges that merely trace that shape. Unlike `add_circle`,
        this creates edges only, no face.

        ``center``/``radius`` are in inches; ``normal`` is the arc's
        plane normal (need not be a unit vector). ``start_angle``/
        ``end_angle`` (radians) measure the sweep from an arbitrary but
        fixed 0-angle reference direction in that plane (perpendicular to
        ``normal``, chosen automatically the same way for every arc/circle
        built by this same normal) - there's no vertex/edge to derive a
        caller-visible "angle 0" from the way a face has its own first
        edge, so the reference direction itself isn't exposed. Sweeps in
        either direction (``end_angle`` less than or greater than
        ``start_angle``) and sweeps beyond a full turn are both valid.
        ``num_segments`` (3-255) controls tessellation. ``hidden_edges``/
        ``soft_edges``/``smooth_edges`` apply to every edge (all newly
        declared, since an arc's own points are essentially never shared
        with prior geometry).

        >>> import math
        >>> builder.add_arc((50, 50, 0), (0, 0, 1), radius=40, start_angle=0, end_angle=math.pi / 2)
        """
        if not (3 <= num_segments <= 255):
            raise SkpWriteError(f"num_segments must be between 3 and 255, got {num_segments}")
        if end_angle == start_angle:
            raise SkpWriteError("start_angle and end_angle must differ - use add_circle for a full circle")
        center = (float(center[0]), float(center[1]), float(center[2]))
        normal = _normalize3((float(normal[0]), float(normal[1]), float(normal[2])))
        radius = float(radius)
        self._ensure_geometry_writer()
        u, w = _circle_basis(normal)
        xaxis = (radius * u[0], radius * u[1], radius * u[2])
        curve_params = (center, normal, xaxis, float(start_angle), float(end_angle), radius, num_segments)
        points = _arc_points(center, normal, radius, num_segments, u, w, float(start_angle), float(end_angle))
        self._new_entity_count += self._geometry_writer.write_arc(
            points, self._vertex_slots, self._edge_registry, curve_params,
            hidden_edges, soft_edges, smooth_edges,
        )
        self._face_count += 1  # reuses the "at least one root entity" check in to_bytes

    def add_polyline(
        self,
        points: Sequence[Point3],
        closed: bool = False,
        hidden_edges: bool = False,
        soft_edges: bool = False,
        smooth_edges: bool = False,
    ) -> None:
        """Add one freeform polyline curve - a chain of straight edges
        (``points`` in order, at least 2) grouped into one genuine
        SketchUp "Curve" entity (selectable/editable as a whole, the same
        grouping real SketchUp's own Freehand/multi-segment-line tools
        produce), not disconnected individual edges that merely happen to
        connect end-to-end. No face, unlike `add_face`. Distinct from
        `add_arc`: there's no arc geometry here, just a labeled set of
        already-straight edges - use this for an arbitrary polyline shape
        that isn't a circular arc.

        ``closed``, if true, also connects the last point back to the
        first. ``hidden_edges``/``soft_edges``/``smooth_edges`` apply to
        every edge (all newly declared, since a polyline's own points are
        essentially never shared with prior geometry).

        >>> builder.add_polyline([(0, 0, 0), (10, 10, 0), (20, 0, 0), (30, 10, 0)])
        """
        points = [(float(p[0]), float(p[1]), float(p[2])) for p in points]
        if len(points) < 2:
            raise SkpWriteError("a polyline needs at least 2 points")
        self._ensure_geometry_writer()
        self._new_entity_count += self._geometry_writer.write_polyline(
            points, self._vertex_slots, self._edge_registry,
            closed, hidden_edges, soft_edges, smooth_edges,
        )
        self._face_count += 1  # reuses the "at least one root entity" check in to_bytes

    def to_bytes(self) -> bytes:
        """Return the finished file's bytes."""
        if self._pending_groups:
            # A file with only groups (no add_face/add_instance call) would
            # otherwise never flush them - _ensure_geometry_writer is a
            # no-op once already created, so this is safe to call
            # unconditionally alongside every other call site.
            self._ensure_geometry_writer()
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
        prefix[_ISO_CAMERA_PREFIX_OFFSET : _ISO_CAMERA_PREFIX_OFFSET + len(_ISO_CAMERA_PREFIX_PATCH)] = (
            _ISO_CAMERA_PREFIX_PATCH
        )
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
        for pos, patch in _ISO_CAMERA_TAIL_PATCHES:
            tail[pos : pos + len(patch)] = patch
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
    and limitations (no inline-declared nested groups; inches only).
    """
    return SkpBuilder()
