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

* Root-level geometry only - faces built directly from vertex coordinates,
  sharing vertices and edges automatically wherever coordinates coincide
  exactly. There is no support yet for materials, layers other than the
  default, or component/group definitions (nested component internals
  contain a byte region this project has not reverse-engineered yet - see
  :mod:`openskp.legacy`'s module docstring).
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
from importlib import resources
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

from . import legacy

__all__ = ["SkpWriteError", "SkpBuilder", "create"]

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


def _u32(v: int) -> bytes:
    return struct.pack("<I", v)


def _f64(v: float) -> bytes:
    return struct.pack("<d", v)


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

    def _preamble(self, pid: Optional[int] = None) -> None:
        self._null()  # no CAttributeContainer
        if pid is None:
            pid = self._alloc_pid()
        mask = 0
        pid_bytes = []
        for bit in range(8):
            byte_val = (pid >> (8 * bit)) & 0xFF
            if byte_val:
                mask |= 1 << bit
                pid_bytes.append(byte_val)
        self.buf.append(mask)
        self.buf += bytes(pid_bytes)

    def _drawbase(self, mat: int = 0, layer: int = 0) -> None:
        b = bytearray(10)
        struct.pack_into("<H", b, 0, mat)
        # offsets 3-4: legacy.py's reader documents these as unused padding
        # (_drawbase's docstring), but real SketchUp silently drops any
        # entity whose drawbase has them zeroed - ground-truth-confirmed by
        # diffing real SDK-authored files. Must be 1, 1.
        b[3] = 1
        b[4] = 1
        struct.pack_into("<H", b, 8, layer)
        self.buf += bytes(b)

    def _write_vertex(self, point: Point3) -> int:
        slot = self._new_of_known_class("CVertex", schema=0)
        self._preamble()
        self.buf += _f64(point[0]) + _f64(point[1]) + _f64(point[2])
        return slot

    def write_face(
        self,
        points: Sequence[Point3],
        vertex_slots: Dict[Point3, int],
        edge_registry: Dict[FrozenSet[int], Tuple[int, int]],
    ) -> int:
        """Write one planar face and return how many new root-entity-list
        slots it consumed (edges newly declared, plus the face itself) -
        the caller accumulates this into the file's total root count.

        ``points`` form a closed polygon in order (do not repeat the first
        point at the end). Vertices and edges are shared automatically
        across calls via ``vertex_slots``/``edge_registry`` wherever
        coordinates coincide exactly - pass the same dicts across every
        `write_face` call building one mesh.
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
            self._drawbase()
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
        self._drawbase()
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

        self.buf += struct.pack("<H", 0)  # back_mat = default
        new_entities += 1  # the face itself
        return new_entities


def _plane_from_polygon(points: Sequence[Point3]) -> Tuple[float, float, float, float]:
    p0, p1, p2 = points[0], points[1], points[2]
    ax, ay, az = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
    bx, by, bz = p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2]
    nx = ay * bz - az * by
    ny = az * bx - ax * bz
    nz = ax * by - ay * bx
    length = (nx * nx + ny * ny + nz * nz) ** 0.5
    if length < 1e-12:
        raise SkpWriteError("face points are collinear or coincident; cannot compute a plane")
    nx, ny, nz = nx / length, ny / length, nz / length
    d = nx * p0[0] + ny * p0[1] + nz * p0[2]
    return nx, ny, nz, d


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
        layer_count = r.u32()
        for _ in range(layer_count):
            ar.read_object(r, expect="CLayer")
        ar.read_object(r)  # definition-list anchor (active-layer back-ref)
        def_count = r.u32()
        for _ in range(def_count):
            ar.read_object(r, expect="CComponentDefinition")

        root_count_pos = r.pos
        orig_root_count = struct.unpack_from("<I", data, root_count_pos)[0]
        r.u32()
        legacy._read_entity_list(ar, r, orig_root_count, "root")
        tail_pos = r.pos

        self._data = data
        self._root_count_pos = root_count_pos
        self._orig_root_count = orig_root_count
        self._tail_pos = tail_pos
        self._initial_next_slot = ar.next_slot
        self._writer = _ArchiveWriter(next_slot=ar.next_slot, class_slot=ar.class_slot)
        self._vertex_slots: Dict[Point3, int] = {}
        self._edge_registry: Dict[FrozenSet[int], Tuple[int, int]] = {}
        self._new_entity_count = 0
        self._face_count = 0

    def add_face(self, points: Sequence[Point3]) -> None:
        """Add one planar face, defined by 3 or more coplanar points (in
        inches) forming a closed polygon in order - do not repeat the
        first point at the end.

        Vertices and edges are automatically shared with previously-added
        faces wherever a point's ``(x, y, z)`` coordinates match exactly
        (same float values) - build a connected mesh by reusing the same
        point tuples across `add_face` calls, not by re-deriving
        numerically-close-but-not-identical coordinates.
        """
        points = [(float(p[0]), float(p[1]), float(p[2])) for p in points]
        if len(points) < 3:
            raise SkpWriteError("a face needs at least 3 points")
        self._new_entity_count += self._writer.write_face(
            points, self._vertex_slots, self._edge_registry
        )
        self._face_count += 1

    def to_bytes(self) -> bytes:
        """Return the finished file's bytes."""
        if self._face_count == 0:
            raise SkpWriteError("no geometry added - call add_face at least once before saving")

        new_root_count = self._orig_root_count + self._new_entity_count
        # Every new-class declaration and every new object allocation each
        # consume one archive slot; next_slot already reflects the running
        # total, so the shift is just the delta since construction. This is
        # the same number the tail's internal cross-references need to be
        # renumbered by (see _TAIL_REF_POSITIONS).
        shift = self._writer.next_slot - self._initial_next_slot

        out = bytearray()
        out += self._data[: self._root_count_pos]
        out += _u32(new_root_count)
        out += self._data[self._root_count_pos + 4 : self._tail_pos]
        out += self._writer.buf
        tail = bytearray(self._data[self._tail_pos :])
        for pos in _TAIL_REF_POSITIONS:
            u16 = struct.unpack_from("<H", tail, pos)[0]
            tag_bit = u16 & 0x8000
            slot = u16 & 0x7FFF
            struct.pack_into("<H", tail, pos, tag_bit | ((slot + shift) & 0x7FFF))
        out += tail
        return bytes(out)

    def save(self, path: str) -> None:
        """Write the finished file to ``path``."""
        with open(path, "wb") as f:
            f.write(self.to_bytes())


def create() -> SkpBuilder:
    """Start building a new legacy-format (v17) ``.skp`` file from scratch.

    >>> builder = create()
    >>> builder.add_face([(0, 0, 0), (100, 0, 0), (100, 100, 0), (0, 100, 0)])
    >>> builder.save("output.skp")

    See the :mod:`openskp.create` module docstring for the current scope
    and limitations (no materials/layers/components yet, inches only).
    """
    return SkpBuilder()
