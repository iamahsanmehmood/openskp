"""Legacy (classic MFC) SketchUp ``.skp`` parser — SketchUp 2013–2020 era.

Pre-2021 ``.skp`` files are not VFF/ZIP containers: after the same UTF-16
header records, the body is **one uncompressed MFC ``CArchive`` object
stream** with a single global 1-based store map.  This module walks that
stream and adapts the result to the same dict shape that
:func:`openskp._core.full_parse` returns for VFF files, so
:class:`openskp.SkpFile` handles both eras transparently.

The decoding below was established by clean-room reverse engineering of
real SketchUp 2016/2017/2018 files, cross-validated against the same
models re-saved as VFF by SketchUp (exact face/edge counts, total area
and bounding box parity).  Where the walk logic matches the public 2017
format notes it follows them; several details were found to differ in
real files and follow the bytes instead:

* ``CEdge``: the two vertex pointers come **before** the curve pointer.
* ``CLoop``: two flag bytes follow the preamble.
* ``CEdgeUse``: has a standard entity preamble.
* ``CFace``: the back-material u16 comes right after the loops, and is
  followed by one redundant edge back-ref word per edge that was
  first-serialized inside this face's loops (push/pulled faces).
* ``CMaterial``: solid tail is ``opacity:f64 + use_opacity:u8`` — the
  trailing byte gates the opacity exactly like VFF's ``useTrans``.
* v2016 (``CEntity`` schema 3) has **no persistent-id mask** in entity
  preambles; 2017+ (schema 5) stores mask + pid bytes.
* The definition-list header's object pointer references the **active**
  layer (usually, but not always, the default layer).

Version-specific byte counts (v16 vs v17/v18) are keyed off the header
version string.
"""

from __future__ import annotations

import re
import struct
from typing import Any, Dict, Optional


class LegacyParseError(ValueError):
    """A structural error while walking the legacy archive."""


_STR_MARKER = b'\xff\xfe\xff'


class _R:
    """Byte cursor."""

    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos

    def u8(self) -> int:
        v = self.data[self.pos]
        self.pos += 1
        return v

    def u16(self) -> int:
        v = struct.unpack_from('<H', self.data, self.pos)[0]
        self.pos += 2
        return v

    def u32(self) -> int:
        v = struct.unpack_from('<I', self.data, self.pos)[0]
        self.pos += 4
        return v

    def f64(self) -> float:
        v = struct.unpack_from('<d', self.data, self.pos)[0]
        self.pos += 8
        return v

    def f64s(self, n: int):
        v = struct.unpack_from('<%dd' % n, self.data, self.pos)
        self.pos += 8 * n
        return v

    def raw(self, n: int) -> bytes:
        v = self.data[self.pos:self.pos + n]
        self.pos += n
        return v

    def peek(self, n: int) -> bytes:
        return self.data[self.pos:self.pos + n]

    def peek_u16(self) -> int:
        return struct.unpack_from('<H', self.data, self.pos)[0]

    def utf16(self) -> str:
        if self.peek(3) != _STR_MARKER:
            raise LegacyParseError(f"expected a string record {self.ctx()}")
        self.pos += 3
        n = self.u8()
        if n == 0xFF:
            n = self.u16()
            if n == 0xFFFF:
                n = self.u32()
        return self.raw(2 * n).decode('utf-16-le', errors='replace')

    def ctx(self, back: int = 16, fwd: int = 32) -> str:
        p = self.pos
        return (f"@{p:#x}: …{self.data[max(0, p - back):p].hex(' ')} | "
                f"{self.data[p:p + fwd].hex(' ')}…")


class _Archive:
    """MFC CArchive store-map bookkeeping and object-graph walk."""

    def __init__(self, data: bytes, ver: int):
        self.data = data
        self.ver = ver                # SketchUp major version (16/17/18…)
        self.has_pid = ver >= 17      # CEntity schema 5 stores pid masks
        self.r = _R(data)
        self.slots: Dict[int, tuple] = {}    # slot -> ('class'|'obj', name, value)
        self.class_slot: Dict[str, int] = {}
        self.next_slot = 0
        self.walk_base = 0            # slots below this are unwalked pre-model
        self.readers: Dict[str, Any] = {}
        self.current_loop: Optional[int] = None
        self.in_entity_list = False
        self._as_item = False

    def alloc(self, entry) -> int:
        s = self.next_slot
        self.slots[s] = entry
        self.next_slot += 1
        return s

    def read_object(self, r: _R, expect: Optional[str] = None):
        tag = r.u16()
        if tag == 0:
            return None, None, None
        if tag == 0x7FFF:                      # big-tag escape
            big = r.u32()
            if big & 0x80000000:
                return self._new_of_class(r, big & 0x7FFFFFFF, expect)
            return self._backref(big, r)
        if tag == 0xFFFF:                      # new class
            schema = r.u16()
            namelen = r.u16()
            if namelen > 40:
                raise LegacyParseError(f"implausible class name length {r.ctx()}")
            name = r.raw(namelen).decode('ascii')
            self.alloc(('class', name, schema))
            self.class_slot[name] = self.next_slot - 1
            return self._new_obj(r, name)
        if tag & 0x8000:                       # class ref -> new object
            return self._new_of_class(r, tag & 0x7FFF, expect)
        return self._backref(tag, r)           # object back-ref

    def _new_of_class(self, r, cslot, expect):
        ent = self.slots.get(cslot)
        if ent is None:
            if expect is None:
                raise LegacyParseError(f"class-ref to unknown slot {cslot} {r.ctx()}")
            # a class defined in the unwalked pre-model region: learn it
            # from context
            self.slots[cslot] = ('class', expect, None)
            self.class_slot[expect] = cslot
            ent = self.slots[cslot]
        if ent[0] != 'class':
            raise LegacyParseError(
                f"class-ref to non-class slot {cslot} ({ent[1]}) {r.ctx()}")
        return self._new_obj(r, ent[1])

    def _new_obj(self, r, name):
        self._as_item = self.in_entity_list
        self.in_entity_list = False
        slot = self.alloc(('obj', name, None))
        reader = self.readers.get(name)
        if reader is None:
            raise LegacyParseError(f"no reader for class {name} {r.ctx()}")
        value = reader(self, r)
        self.slots[slot] = ('obj', name, value)
        return slot, name, value

    def _backref(self, slot, r):
        ent = self.slots.get(slot)
        if ent is None:
            if slot < self.walk_base:
                # opaque reference into the unwalked pre-model region
                # (style-region entities, image dibs, …)
                return slot, 'premodel', None
            raise LegacyParseError(f"back-ref to unwalked slot {slot} {r.ctx()}")
        if ent[0] == 'class':
            raise LegacyParseError(f"back-ref to class slot {slot} {r.ctx()}")
        return slot, ent[1], ent[2]


# ── shared record blocks ─────────────────────────────────────────────────

def _preamble(ar, r):
    """Entity preamble: attribute pointer (+ pid mask/bytes on 2017+)."""
    slot, name, attrs = ar.read_object(r, expect='CAttributeContainer')
    pid = 0
    if ar.has_pid:
        mask = r.u8()
        for bit in range(8):
            if mask & (1 << bit):
                pid |= r.u8() << (8 * bit)
    return {'attrs': attrs, 'pid': pid}


def _drawbase(ar, r):
    b = r.raw(10)
    return {'mat': struct.unpack_from('<H', b, 0)[0],
            'hidden': b[2], 'soft': b[5], 'smooth': b[6],
            'layer': struct.unpack_from('<H', b, 8)[0]}


# ── entity readers ───────────────────────────────────────────────────────

def _read_vertex(ar, r):
    _preamble(ar, r)
    return {'k': 'vertex', 'xyz': r.f64s(3)}


def _read_edge(ar, r):
    _preamble(ar, r)
    db = _drawbase(ar, r)
    s1, _, _ = ar.read_object(r, expect='CVertex')
    s2, _, _ = ar.read_object(r, expect='CVertex')
    cs, cn, _ = ar.read_object(r)
    if cn not in (None, 'CCurve', 'CArcCurve'):
        raise LegacyParseError(f"edge curve pointer resolved to {cn} {r.ctx()}")
    return {'k': 'edge', 'db': db, 'curve': cs, 'v1': s1, 'v2': s2}


def _read_curve(ar, r):
    _preamble(ar, r)
    r.u8()
    n = r.u32()
    return {'k': 'curve', 'n': n}


def _read_arccurve(ar, r):
    _preamble(ar, r)
    r.raw(5)
    r.f64s(14)                       # arc frame (center, axes, radius, sweep)
    return {'k': 'arccurve'}


def _read_edgeuse(ar, r):
    _preamble(ar, r)
    es, _, _ = ar.read_object(r, expect='CEdge')
    sense = r.u8()
    ps, pn, _ = ar.read_object(r)    # parent-loop back-ref: alignment oracle
    if ps != ar.current_loop:
        raise LegacyParseError(
            f"edge-use parent slot {ps} != current loop {ar.current_loop} {r.ctx()}")
    return {'k': 'edgeuse', 'edge': es, 'sense': sense}


def _read_loop(ar, r):
    my_slot = ar.next_slot - 1
    prev = ar.current_loop
    ar.current_loop = my_slot
    _preamble(ar, r)
    r.raw(2)                         # 2 flag bytes
    uses = []
    while True:
        if r.peek_u16() == 0:
            r.pos += 2
            break
        _, _, v = ar.read_object(r, expect='CEdgeUse')
        uses.append(v)
    ar.current_loop = prev
    return {'k': 'loop', 'uses': uses}


def _read_face(ar, r):
    pre = _preamble(ar, r)
    db = _drawbase(ar, r)
    plane = r.f64s(4)
    nloops = r.u32()
    if nloops > 10000:
        raise LegacyParseError(f"implausible loop count {nloops} {r.ctx()}")
    loops = []
    for _ in range(nloops):
        _, _, v = ar.read_object(r, expect='CLoop')
        loops.append(v)
    # NOTE: edges first inlined inside this face's loops appear right after
    # the back-material word as redundant back-ref LIST ITEMS (they carry
    # the edges' entity-list entries) — the list loop consumes them.
    back_mat = r.u16()
    return {'k': 'face', 'db': db, 'plane': plane, 'loops': loops,
            'back_mat': back_mat, 'attrs': pre['attrs']}


def _read_attr_container(ar, r):
    _preamble(ar, r)
    children = []
    while True:
        if r.peek_u16() == 0:
            r.pos += 2
            break
        _, n, v = ar.read_object(r, expect='CAttributeNamed')
        children.append((n, v))
    return {'k': 'attrs', 'children': children}


def _read_attr_named(ar, r):
    _preamble(ar, r)
    r.raw(4)
    dictname = r.utf16()

    def read_typed(t):
        if t == 0x00:
            return None
        if t == 0x04:
            return struct.unpack('<i', r.raw(4))[0]
        if t == 0x06:
            return r.f64()
        if t == 0x07:
            return r.u8()
        if t == 0x09:
            return r.u32()           # time_t
        if t == 0x0A:
            return r.utf16()
        if t == 0x0B:
            n = r.u32()
            if n > 100000:
                raise LegacyParseError(f"implausible attr array count {r.ctx()}")
            return [read_typed(r.u8()) for _ in range(n)]
        if t == 0x12:
            return r.f64s(3)         # 3D vector
        raise LegacyParseError(f"unknown attribute value type {t:#x} {r.ctx()}")

    entries = {}
    while True:
        key = r.utf16()
        if key == '':
            break
        entries[key] = read_typed(r.u8())
    r.u32()
    return {'k': 'dict', 'name': dictname, 'entries': entries}


def _read_layer(ar, r):
    _preamble(ar, r)
    name = r.utf16()
    mid = b''
    while len(mid) < 8 and r.peek(3) != _STR_MARKER:
        mid += r.raw(1)              # flags: 3 bytes on v16, 4 on v17+
    r.utf16()                        # internal name ("Layer_<name>")
    r.u16()
    rgba = r.raw(4)
    r.utf16()
    r.raw(21)
    return {'k': 'layer', 'name': name, 'hidden': mid[0] if mid else 0,
            'rgba': tuple(rgba)}


def _read_material(ar, r):
    _preamble(ar, r)
    name = r.utf16()
    texflag = r.u16()
    out: Dict[str, Any] = {'k': 'material', 'name': name}
    if texflag == 0:
        rgba = r.raw(4)
        r.utf16()                    # texture path (empty)
        r.raw(8)
        opacity = r.f64()
        use_op = r.u8()
        out.update(rgba=tuple(rgba), opacity=opacity, use_opacity=use_op)
    else:
        r.raw(2 if ar.ver >= 17 else 1)     # texture flag pad
        s, n, dib = ar.read_object(r, expect='CDib')
        if not (isinstance(dib, dict) and dib.get('k') == 'dib'):
            raise LegacyParseError(f"texture object is not a dib {r.ctx()}")
        # optional u32 between the dib and the 2 x f64 applied size
        marker = r.data.find(_STR_MARKER, r.pos, r.pos + 28)
        if marker - r.pos == 20:
            r.u32()
        elif marker - r.pos != 16:
            raise LegacyParseError(f"texture size block misaligned {r.ctx()}")
        w = r.f64()
        h = r.f64()
        fname = r.utf16()
        avg = r.raw(9)               # RGBA + 00 + RGBA (colour stored twice)
        r.utf16()
        blob = r.raw(8)              # u32 + u32 colorized flag
        opacity = r.f64()
        use_op = r.u8()
        # A colourized (re-tinted) texture stores the ORIGINAL image plus
        # the tint as the average colour; flagged by the second blob u32
        # or by alpha 0xFF on the stored colour.
        colorized = bool(blob[4]) or avg[3] == 0xFF
        out.update(rgba=tuple(avg[:4]), opacity=opacity, use_opacity=use_op,
                   tex_dib=s, tex_w=w, tex_h=h, tex_file=fname,
                   colorized=colorized)
    return out


def _read_dib(ar, r):
    subtype = r.u32()
    length = r.u32()
    if length > len(r.data):
        raise LegacyParseError(f"implausible dib length {length} {r.ctx()}")
    data = r.raw(length)
    return {'k': 'dib', 'subtype': subtype, 'data': data}


def _read_ftc(ar, r):
    """CFaceTextureCoords: texture-mapping matrices + pins. The two trailing
    u32s are per-side flags: bit 0 = side painted/positioned, bit 1 =
    texture PROJECTED (e.g. the Add Location terrain drape — its UVs run in
    the projection plane, not the face frame)."""
    _preamble(ar, r)
    r.u32()
    ks = r.f64s(24)
    front_pins = [r.f64s(4) for _ in range(r.u32())]
    back_pins = [r.f64s(4) for _ in range(r.u32())]
    fflags = r.u32()
    bflags = r.u32()
    return {'k': 'ftc', 'front': ks[0:9], 'back': ks[12:21],
            'front_pins': front_pins, 'back_pins': back_pins,
            'front_projected': bool(fflags & 2),
            'back_projected': bool(bflags & 2)}


def _read_camera(ar, r):
    r.raw(137)
    r.u16()
    r.utf16()
    r.raw(33)
    return {'k': 'camera'}


def _read_thumbnail(ar, r):
    _preamble(ar, r)
    ar.read_object(r, expect='CCamera')
    _, _, dib = ar.read_object(r, expect='CDib')
    return {'k': 'thumbnail', 'dib': dib}


def _read_relationship(ar, r):
    _preamble(ar, r)
    # two object pointers (small maps: two u16 back-refs — which read like
    # the "u32" of the public notes; big maps escalate them to big-tags)
    ar.read_object(r)
    ar.read_object(r)
    return {'k': 'relationship'}


def _read_constructionline(ar, r):
    _preamble(ar, r)
    _drawbase(ar, r)
    r.f64s(3)
    r.f64s(3)
    r.f64s(2)
    r.raw(7 if ar.ver >= 17 else 4)
    return {'k': 'cline'}


def _read_constructionpoint(ar, r):
    _preamble(ar, r)
    db = _drawbase(ar, r)
    pos = r.f64s(3)
    r.f64s(3)
    r.u8()
    return {'k': 'cpoint', 'db': db, 'pos': pos}


def _read_sectionplane(ar, r):
    _preamble(ar, r)
    _drawbase(ar, r)
    # optional object pointer before the plane; a real plane starts with a
    # unit-normal component (|x| <= 1) — a tag word does not decode as one
    first = struct.unpack_from('<d', r.data, r.pos)[0]
    if not abs(first) <= 1.0001:
        ar.read_object(r)
    r.f64s(4)
    if r.peek(3) == _STR_MARKER:     # v18: name + short label
        r.utf16()
        r.utf16()
    return {'k': 'sectionplane'}


def _read_skfont(ar, r):
    ar.read_object(r, expect='CAttributeContainer')
    if ar.has_pid:
        r.u8()
    r.utf16()
    r.raw(15)
    return {'k': 'font'}


def _read_dimlinear(ar, r):
    _preamble(ar, r)
    db = _drawbase(ar, r)
    text = r.utf16()
    ar.read_object(r, expect='CSkFont')
    r.raw(165)
    return {'k': 'dimension', 'db': db, 'text': text}


def _read_text(ar, r):
    _preamble(ar, r)
    _drawbase(ar, r)
    ar.read_object(r, expect='CSkFont')
    # variable-length variant middle, delimited by an 11-byte block
    # `01 00 00 00 ?? 00 03 00 00 00 01` right before the text string
    p = r.pos
    while True:
        idx = r.data.find(_STR_MARKER, p, r.pos + 512)
        if idx < 0:
            raise LegacyParseError(f"text delimiter not found {r.ctx()}")
        blk = r.data[idx - 11:idx]
        if (blk[:4] == b'\x01\x00\x00\x00'
                and blk[6:10] == b'\x03\x00\x00\x00' and blk[10] == 1):
            break
        p = idx + 3
    r.raw(idx - r.pos)
    text = r.utf16()
    r.raw(5)
    return {'k': 'text', 'text': text}


def _read_entity_list(ar, r, count, owner):
    ents = []
    while len(ents) < count:
        p = r.pos
        prev_flag = ar.in_entity_list
        ar.in_entity_list = True
        try:
            s, n, v = ar.read_object(r)
        except LegacyParseError:
            if owner != 'root':
                raise
            # over-declared root counts run into the document tail — stop
            r.pos = p
            break
        finally:
            ar.in_entity_list = prev_flag
        ents.append((s, n, v))
    return ents


def _read_definition(ar, r):
    _preamble(ar, r)
    r.raw(22 if ar.ver >= 17 else 20)         # undecoded base block
    nlayers = r.u32()
    if nlayers > 10000:
        raise LegacyParseError(f"implausible def layer count {r.ctx()}")
    for _ in range(nlayers):
        ar.read_object(r, expect='CLayer')
    decl = r.u16()
    if decl == 0x7FFF:
        decl = r.u32()
    r.u32()
    count = r.u32()
    if count > 5_000_000:
        raise LegacyParseError(f"implausible def entity count {r.ctx()}")
    ents = _read_entity_list(ar, r, count, 'def')
    nrel = r.u32()
    if nrel > 100000:
        raise LegacyParseError(f"definition list misaligned {r.ctx()}")
    for _ in range(nrel):
        ar.read_object(r, expect='CRelationship')
    r.u16()
    guid = r.raw(16)
    name = r.utf16()
    r.utf16()
    r.utf16()
    r.u32()                                   # timestamp
    # undecoded block (~39-47 bytes), then the CThumbnail object
    tpos = None
    for off in range(0, 96):
        p = r.pos + off
        if (r.data[p:p + 2] == b'\xff\xff' and r.data[p + 4:p + 6] == b'\x0a\x00'
                and r.data[p + 6:p + 16] == b'CThumbnail'):
            tpos = p
            break
        if 'CThumbnail' in ar.class_slot:
            if struct.unpack_from('<H', r.data, p)[0] == \
                    0x8000 | ar.class_slot['CThumbnail']:
                tpos = p
                break
    if tpos is None:
        raise LegacyParseError(f"definition tail: thumbnail not found {r.ctx()}")
    gap = r.raw(tpos - r.pos)
    # component-behavior flags sit 9 bytes before the thumbnail:
    # bit 0 = always-faces-camera, bit 1 = shadows-face-sun
    behavior = gap[-9] if len(gap) >= 9 else 0
    ar.read_object(r, expect='CThumbnail')
    return {'k': 'definition', 'name': name, 'guid': guid.hex().upper(),
            'ents': ents, 'faces_camera': bool(behavior & 1)}


def _read_instance(ar, r):
    _preamble(ar, r)
    db = _drawbase(ar, r)
    ds, dn, _ = ar.read_object(r, expect='CComponentDefinition')
    if dn != 'CComponentDefinition':
        raise LegacyParseError(f"instance definition ref is {dn} {r.ctx()}")
    xf = r.f64s(13)
    name = r.utf16()
    guid = r.raw(16)
    return {'k': 'instance', 'db': db, 'def': ds, 'xf': xf,
            'name': name, 'guid': guid.hex().upper()}


_READERS = {
    'CVertex': _read_vertex, 'CEdge': _read_edge, 'CCurve': _read_curve,
    'CArcCurve': _read_arccurve, 'CEdgeUse': _read_edgeuse,
    'CLoop': _read_loop, 'CFace': _read_face, 'CLayer': _read_layer,
    'CMaterial': _read_material, 'CDib': _read_dib,
    'CAttributeContainer': _read_attr_container,
    'CAttributeNamed': _read_attr_named, 'CCamera': _read_camera,
    'CThumbnail': _read_thumbnail, 'CRelationship': _read_relationship,
    'CComponentDefinition': _read_definition,
    'CComponentInstance': _read_instance, 'CGroup': _read_instance,
    'CFaceTextureCoords': _read_ftc,
    'CConstructionLine': _read_constructionline,
    'CConstructionPoint': _read_constructionpoint,
    'CSectionPlane': _read_sectionplane, 'CSkFont': _read_skfont,
    'CDimensionLinear': _read_dimlinear, 'CText': _read_text,
}


# ── walk driver ──────────────────────────────────────────────────────────

def is_legacy(data: bytes) -> bool:
    """True when *data* is a classic (pre-2021) MFC-container ``.skp``."""
    if not data.startswith(b'\xff\xfe\xff\x0e'):
        return False
    if b'PK\x03\x04' in data[:0x100]:
        return False
    return b'CVersionMap' in data[:0x200]


def _walk(data: bytes):
    ver_m = re.search(rb'\{(\d+)\.', data[:0x60].replace(b'\x00', b''))
    if not ver_m:
        raise LegacyParseError("no version string in header")
    ver = int(ver_m.group(1))
    ar = _Archive(data, ver)
    ar.readers.update(_READERS)
    r = ar.r

    # anchor: the material manager (u32 count right before the first
    # CMaterial new-class record)
    m = re.search(re.escape(b'\xff\xff') + b'..'
                  + re.escape(struct.pack('<H', 9) + b'CMaterial'),
                  data, re.DOTALL)
    if not m:
        raise LegacyParseError("no CMaterial class record found")
    mat_hdr = m.start()
    mat_count = struct.unpack_from('<I', data, mat_hdr - 4)[0]
    if mat_count > 100000:
        raise LegacyParseError("implausible material count")

    # bootstrap the absolute slot base: parse material 1 with a throwaway
    # archive; material 2's class-ref tag names CMaterial's true slot
    if mat_count < 2:
        raise LegacyParseError(
            "single-material bootstrap not implemented for this file")
    boot = _Archive(data, ver)
    boot.readers.update(_READERS)
    boot.next_slot = 1 << 20
    boot.walk_base = 1 << 20
    boot.r.pos = mat_hdr
    boot.read_object(boot.r, expect='CMaterial')
    tag = boot.r.peek_u16()
    if tag == 0xFFFF or not (tag & 0x8000):
        raise LegacyParseError("cannot bootstrap the slot base")
    ar.next_slot = tag & 0x7FFF
    ar.walk_base = ar.next_slot

    # material manager
    r.pos = mat_hdr
    materials = []
    for _ in range(mat_count):
        s, _, v = ar.read_object(r, expect='CMaterial')
        materials.append((s, v))

    # layer list marker: v16 <u32 X><u32 count>, v17+ <u32 X><u8 0><u32 count>
    r.u32()
    if ver >= 17:
        r.u8()
    layer_count = r.u32()
    if layer_count > 100000:
        raise LegacyParseError("implausible layer count")
    layers = []
    for _ in range(layer_count):
        s, _, v = ar.read_object(r, expect='CLayer')
        layers.append((s, v))

    # definition list: object pointer to the ACTIVE layer, then count
    _, dn, _ = ar.read_object(r)
    if dn != 'CLayer':
        raise LegacyParseError(f"definition-list anchor is {dn}, not a layer")
    def_count = r.u32()
    if def_count > 1_000_000:
        raise LegacyParseError("implausible definition count")
    for _ in range(def_count):
        ar.read_object(r, expect='CComponentDefinition')

    # trailing definitions, back-to-back
    def_cls = ar.class_slot.get('CComponentDefinition')
    while True:
        tag = r.peek_u16()
        is_def = def_cls is not None and tag == (0x8000 | def_cls)
        if not is_def and tag == 0xFFFF \
                and r.peek(26)[6:26] == b'CComponentDefinition':
            is_def = True
        if not is_def:
            break
        ar.read_object(r)

    # root entity list
    root_count = r.u32()
    if root_count > 5_000_000:
        raise LegacyParseError("implausible root entity count")
    root = _read_entity_list(ar, r, root_count, 'root')

    return ar, root, layers, materials


# ── adapter to the full_parse dict shape ────────────────────────────────

class _Builder:
    """Mirror of ``_core._GeometryBuilder`` (kept dependency-free)."""

    def __init__(self):
        self.vertices = {}
        self.edges = {}
        self.edge_flags = {}      # edge id -> display flag byte (VFF D307 bits)
        self.faces = {}
        self.instances = []


def _fill_builder(builder, ents, slots):
    for s, n, v in ents:
        if not isinstance(v, dict):
            continue
        k = v.get('k')
        if k == 'edge':
            _add_edge(builder, s, v, slots)
        elif k == 'face':
            loops = []
            for lp in v['loops']:
                loop = []
                for u in lp['uses']:
                    es = u['edge']
                    ent = slots.get(es)
                    if ent is None or ent[2] is None:
                        continue
                    _add_edge(builder, es, ent[2], slots)
                    loop.append((es, 1 if u['sense'] else 0))
                loops.append(loop)
            face = {'loops': loops, 'normal': tuple(v['plane'][:3]),
                    'material_id': v['db']['mat'] or None,
                    'back_material_id': v['back_mat'] or None}
            attrs = v.get('attrs')
            if isinstance(attrs, dict):
                for cn, cv in attrs.get('children', []):
                    if isinstance(cv, dict) and cv.get('k') == 'ftc':
                        face['uv_transform'] = list(cv['front'])
                        face['uv_transform_back'] = list(cv['back'])
                        face['uv_projected'] = cv.get('front_projected', False)
                        face['uv_projected_back'] = cv.get('back_projected', False)
            builder.faces[s] = face
        elif k == 'instance':
            builder.instances.append({
                'name': v['name'], 'ref_idx': v['def'],
                'ref_guid': '', 'matrix': list(v['xf']),
                'material_id': v['db']['mat'] or None,
                'layer_id': v['db']['layer'] or None,
                'children': []})


def _add_edge(builder, slot, e, slots):
    if slot in builder.edges:
        return
    v1, v2 = e['v1'], e['v2']
    for vs in (v1, v2):
        ent = slots.get(vs)
        if ent is not None and ent[2] is not None and vs not in builder.vertices:
            builder.vertices[vs] = tuple(ent[2]['xyz'])
    builder.edges[slot] = (v1, v2)
    db = e.get('db') or {}
    flags = ((0x08 if db.get('soft') else 0)
             | (0x10 if db.get('smooth') else 0)
             | (0x01 if db.get('hidden') else 0))
    if flags:
        builder.edge_flags[slot] = flags


def full_parse_legacy(skp_path: str) -> Dict[str, Any]:
    """Parse a classic MFC ``.skp`` into the ``full_parse`` dict shape."""
    with open(skp_path, 'rb') as f:
        data = f.read()

    version = 'unknown'
    second = data.find(_STR_MARKER, 4)
    if second > 0:
        text = data[second + 4:second + 100].decode('utf-16-le', errors='ignore')
        if '{' in text and '}' in text:
            version = text[text.find('{'):text.find('}') + 1]

    try:
        ar, root, layers, materials = _walk(data)
    except (LegacyParseError, struct.error, IndexError, UnicodeDecodeError) as e:
        raise ValueError(f"legacy .skp parse failed: {e}") from e

    slots = ar.slots

    # materials — keyed by name like the VFF path
    mats: Dict[str, Any] = {}
    material_id_to_name: Dict[int, str] = {}
    for s, v in materials:
        rgba = v.get('rgba', (128, 128, 128, 255))
        # the stored f64 is a TRANSPARENCY (0 = opaque), gated by the
        # trailing use-flag byte; expose opacity like the VFF path
        if v.get('use_opacity'):
            trans = min(max(1.0 - v['opacity'], 0.0), 1.0)
        else:
            trans = 1.0
        colorized = v.get('colorized', False)
        mat_obj: Dict[str, Any] = {
            'name': v['name'],
            'color': {'r': rgba[0], 'g': rgba[1], 'b': rgba[2]},
            'transparency': trans,
            # colourize type is not decoded in the legacy record; tint is
            # the correct rendering for the grey base textures observed
            'colorized': colorized, 'colorize_type': 1 if colorized else 0,
        }
        if 'tex_dib' in v:
            dib = slots.get(v['tex_dib'])
            tex_data = dib[2]['data'] if dib and dib[2] else None
            ext = '.png' if (tex_data or b'')[:4] == b'\x89PNG' else '.jpg'
            fname = v.get('tex_file') or (v['name'] + ext)
            mat_obj['texture'] = {'filename': fname,
                                  'x_scale': v['tex_w'], 'y_scale': v['tex_h'],
                                  'data': tex_data}
        mats[v['name']] = mat_obj
        material_id_to_name[s] = v['name']

    # layers
    layer_colors = {}
    layer_id_to_name = {}
    for s, v in layers:
        rgba = v.get('rgba', (136, 136, 136, 255))
        layer_colors[v['name']] = (rgba[0], rgba[1], rgba[2])
        layer_id_to_name[s] = v['name']
    if 'Layer0' not in layer_colors:
        layer_colors['Layer0'] = (136, 136, 136)

    # definitions
    defs_dict: Dict[Any, Any] = {}
    for s, ent in slots.items():
        if ent[0] == 'obj' and ent[1] == 'CComponentDefinition' and ent[2]:
            d = ent[2]
            b = _Builder()
            _fill_builder(b, d['ents'], slots)
            defs_dict[s] = {'guid': d['guid'], 'name': d['name'],
                            'is_image': False,
                            'always_faces_camera': d.get('faces_camera', False),
                            'builder': b}

    root_builder = _Builder()
    _fill_builder(root_builder, root, slots)
    defs_dict['ROOT'] = {'guid': 'ROOT', 'name': 'ROOT_MODEL',
                         'builder': root_builder}

    return {
        'version': version,
        'layer_colors': layer_colors,
        'layer_id_to_name': layer_id_to_name,
        'material_id_to_name': material_id_to_name,
        'materials': mats,
        'materials_by_folder': {},
        'defs_dict': defs_dict,
        'elements': [],
        'thumbnail_data': None,
        'styles': [],
    }
