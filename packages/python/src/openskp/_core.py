"""Core parsing engine — battle-tested monolithic implementation.

This module contains the proven parsing logic extracted from the original
SketchUp reverse-engineering work. It is used internally by :class:`SkpFile`
to perform the actual binary parsing.

The public modules (parser.py, geometry.py, etc.) provide the clean API;
this module provides the working implementation.
"""

from __future__ import annotations

import io
import logging
import os
import struct
import time
import zipfile
from typing import Any, Dict
# material.xml/style.xml come from inside the .skp's ZIP container, which is
# fully attacker-controlled input - stdlib ElementTree expands internal
# general entities with no limit, making a "billion laughs" DoS payload
# trivial to embed. defusedxml.ElementTree is a drop-in replacement (same
# ParseError class, same fromstring() signature) that rejects that class of
# payload outright instead.
import defusedxml.ElementTree as ET
from defusedxml.common import DefusedXmlException

import numpy as np
from shapely.geometry import Polygon, Point, MultiPoint
import shapely.ops

from .errors import SkpParseError

# Silent by default (standard library convention: a library never installs
# its own handler or configures a level - the host application decides
# whether/where these go). To see progress: logging.getLogger("openskp")
# .setLevel(logging.DEBUG) plus a handler, e.g. logging.basicConfig().
logger = logging.getLogger("openskp")

# How often (in top-level records) to emit a DEBUG progress log during the
# main walk - coarse enough that it costs nothing on a 300k-definition file.
_PROGRESS_INTERVAL = 500


# ── TLV Parsing ──────────────────────────────────────────────────────────

def read_u32(data: bytes, offset: int) -> int:
    return struct.unpack_from('<I', data, offset)[0]

def read_f64(data: bytes, offset: int) -> float:
    return struct.unpack_from('<d', data, offset)[0]

def parse_var_int(data: bytes, offset: int, length: int) -> int:
    val = 0
    for i in range(length):
        val |= data[offset + i] << (8 * i)
    return val

# VFF (2021+) model.dat binary structure uses Type-Length-Value (TLV) records.
# Most TLV tags carry raw leaf binary/string payloads, but container tags contain
# nested sub-TLV nodes (e.g. definitions, drawing elements, component instances).
# CONTAINER_TAGS lists every tag hex ID that the TLV parser must recursively traverse.
CONTAINER_TAGS = {
    'F401', 'F701', 'D430', 'D530', 'C832',
    '7C15', '8813', '8913', '8A13', '8B13', '8C13', '8D13', '4C1D', '6419',
    'F901', '7017', '7117', 'D007', 'C409', '9411', '9511', '0F01',
    '384A', 'B80B', '9713', '2C4C', 'AC0D', 'AE0D', 'F601', 'F801',
    '983A', '993A', '8C3C', '8D3C',
    # Image-entity placement: an Image placed in the model wraps a standard
    # 6419 instance node inside 9013 -> 401F. Without these two containers,
    # that inner instance stays buried in an opaque payload and the image
    # definition looks "never placed".
    '9013', '401F',
}

# Every byte value's 2-character uppercase hex form, precomputed once.
# `data[pos:pos+2].hex().upper()` is a 2-byte slice (an allocation) plus two
# full string passes (hex() then upper()) on the *hot* path of the TLV
# parser - millions of times on a large real file. Two lookups plus a
# concat produce the identical 4-char string with no slice and no second
# pass - see openskp#244.
_HEX_UPPER = [format(i, '02X') for i in range(256)]


class _TlvNode:
    """One parsed TLV record - the same (offset, tag, size, children,
    payload) shape every caller already gets from a plain dict (and every
    caller still spells it that way: ``node['tag']``, ``node['payload']``,
    etc. - this class implements ``__getitem__``/``get`` so none of them
    need to change), but as a fixed-slot object instead of a dict.
    Constructing millions of these (one per TLV record in a large real
    file) is measurably cheaper as slot assignments than as dict-literal
    construction (no hashing, no hash table growth) - see openskp#244.

    ``payload`` is lazy: for a leaf record, the actual byte slice - a real
    copy, not a view - is deferred until something actually reads
    ``node['payload']``, and cached from then on. Most real files have far
    more leaf records than are ever inspected downstream (many tags are
    read only for their presence/offset/size, or belong to a branch of the
    tree the caller skips entirely), so this turns a guaranteed copy into
    one only when it's actually needed. A container record's payload is
    always the empty bytes ``b''`` (its content lives in ``children``
    instead), so that case is resolved eagerly with no deferred state at
    all - matching the original dict version's own `if not children else
    b''` rule exactly.
    """

    __slots__ = ('offset', 'tag', 'size', 'children', '_data', '_payload_start', '_payload_end', '_payload')

    def __init__(self, offset, tag, size, children, data, payload_start, payload_end):
        self.offset = offset
        self.tag = tag
        self.size = size
        self.children = children
        if children:
            self._payload = b''
            self._data = None
        else:
            self._payload = None
            self._data = data
            self._payload_start = payload_start
            self._payload_end = payload_end

    @property
    def payload(self):
        if self._payload is None:
            self._payload = self._data[self._payload_start:self._payload_end]
            self._data = None  # release the whole-buffer reference once resolved
        return self._payload

    def __getitem__(self, key):
        return getattr(self, key)

    def get(self, key, default=None):
        return getattr(self, key, default)


def parse_tlv_recursive(data, start, end, container_tags=None, depth=0):
    if container_tags is None:
        container_tags = CONTAINER_TAGS
    pos = start
    elements = []
    while pos <= end - 6:
        tag_hex = _HEX_UPPER[data[pos]] + _HEX_UPPER[data[pos + 1]]
        size = read_u32(data, pos+2)
        if pos + 6 + size > end:
            break
        children = []
        is_container = tag_hex in container_tags
        if is_container and size > 0:
            children = parse_tlv_recursive(data, pos+6, pos+6+size, container_tags, depth+1)
        elements.append(_TlvNode(pos, tag_hex, size, children, data, pos + 6, pos + 6 + size))
        pos += 6 + size
    return elements


def _flat_headers(data, start, end):
    """Scan [start, end) for direct-child (tag, offset, size) headers only,
    without recursing into any container - O(sibling count), not
    O(total node count). Used to locate top-level records one at a time so
    full_parse() never has to hold the whole file's TLV tree in memory at
    once (real production files can have 100k+ top-level definitions)."""
    headers = []
    pos = start
    while pos <= end - 6:
        tag_hex = _HEX_UPPER[data[pos]] + _HEX_UPPER[data[pos + 1]]
        size = read_u32(data, pos+2)
        if pos + 6 + size > end:
            break
        headers.append((tag_hex, pos, size))
        pos += 6 + size
    return headers


def iter_top_level_lazy(data, start, end, container_tags=None):
    """Yield ``(index, total, node)`` for each top-level TLV record, fully
    recursed, one at a time - transparently unwrapping a lone 'F401'
    wrapper (matching the shape parse_tlv_recursive's callers already
    expect) - without ever materializing more than one top-level subtree
    simultaneously.

    ``total`` (the top-level sibling count) comes for free from the same
    cheap header scan that drives the loop, so callers can report "N of
    total" progress with no extra pass over the file.

    Each yielded node is independent and safe to discard (drop all
    references) once the caller is done with it, before the next one is
    produced - that's what keeps peak memory bounded by the size of the
    single largest top-level record instead of the whole file.
    """
    if container_tags is None:
        container_tags = CONTAINER_TAGS

    headers = _flat_headers(data, start, end)
    if len(headers) == 1 and headers[0][0] == 'F401':
        _, f401_offset, f401_size = headers[0]
        headers = _flat_headers(data, f401_offset + 6, f401_offset + 6 + f401_size)

    total = len(headers)
    for index, (tag_hex, offset, size) in enumerate(headers):
        record_end = offset + 6 + size
        nodes = parse_tlv_recursive(data, offset, record_end, container_tags)
        if nodes:
            yield index, total, nodes[0]


# ── 3D planar triangulation ──────────────────────────────────────────────

def triangulate_face_3d(vertices_3d, loops, normal):
    if not loops or not loops[0] or len(loops[0]) < 3:
        return []
    if len(loops) == 1 and len(loops[0]) == 3:
        return [loops[0]]
    if len(loops) == 1 and len(loops[0]) == 4:
        v = loops[0]
        return [[v[0], v[1], v[2]], [v[0], v[2], v[3]]]

    normal = np.array(normal)
    norm_val = np.linalg.norm(normal)
    if norm_val > 1e-6:
        normal = normal / norm_val
    else:
        normal = np.array([0.0, 0.0, 1.0])

    if abs(normal[0]) < 0.9:
        helper = np.array([1.0, 0.0, 0.0])
    else:
        helper = np.array([0.0, 1.0, 0.0])

    u_axis = np.cross(normal, helper)
    u_axis = u_axis / np.linalg.norm(u_axis)
    v_axis = np.cross(normal, u_axis)

    all_v_ids = []
    for loop in loops:
        for v_id in loop:
            if v_id not in all_v_ids:
                all_v_ids.append(v_id)

    v_id_to_2d = {}
    for v_id in all_v_ids:
        if v_id in vertices_3d:
            p3d = np.array(vertices_3d[v_id])
            u = np.dot(p3d, u_axis)
            v = np.dot(p3d, v_axis)
            v_id_to_2d[v_id] = (u, v)
        else:
            return []

    outer_coords = [v_id_to_2d[v_id] for v_id in loops[0]]
    if outer_coords[0] != outer_coords[-1]:
        outer_coords.append(outer_coords[0])

    inner_holes = []
    for hole_loop in loops[1:]:
        hole_coords = [v_id_to_2d[v_id] for v_id in hole_loop]
        if hole_coords[0] != hole_coords[-1]:
            hole_coords.append(hole_coords[0])
        inner_holes.append(hole_coords)

    try:
        poly_2d = Polygon(outer_coords, inner_holes)
        if not poly_2d.is_valid:
            poly_2d = poly_2d.buffer(0)

        points_2d = []
        for coords in [outer_coords] + inner_holes:
            for c in coords[:-1]:
                points_2d.append(Point(c))

        if not points_2d:
            return []

        mp = MultiPoint(points_2d)
        triangles = shapely.ops.triangulate(mp)

        inside_triangles = []
        for tri in triangles:
            if poly_2d.contains(tri.centroid):
                tri_coords = list(tri.exterior.coords)[:3]
                tri_v_ids = []
                for tc in tri_coords:
                    best_v_id = None
                    min_dist = float('inf')
                    for v_id, c2d in v_id_to_2d.items():
                        dist = (tc[0] - c2d[0])**2 + (tc[1] - c2d[1])**2
                        if dist < min_dist:
                            min_dist = dist
                            best_v_id = v_id
                    if best_v_id is not None:
                        tri_v_ids.append(best_v_id)
                if len(tri_v_ids) == 3 and len(set(tri_v_ids)) == 3:
                    inside_triangles.append(tri_v_ids)

        if inside_triangles:
            return inside_triangles
    except Exception:
        logger.debug("Shapely triangulation failed for face, falling back to fan triangulation", exc_info=True)

    # Fan triangulation fallback for outer loop
    outer_loop = loops[0]
    if len(outer_loop) < 3:
        return []
    return [[outer_loop[0], outer_loop[i], outer_loop[i + 1]] for i in range(1, len(outer_loop) - 1)]


# ── Geometry builder ─────────────────────────────────────────────────────

class _GeometryBuilder:
    def __init__(self):
        self.vertices = {}
        self.edges = {}
        self.edge_flags = {}      # edge id -> display flag byte (D307)
        self.faces = {}
        self.instances = []
        self.section_planes = []
        self.texts = []
        self.dimensions = []


def find_child_tag(nodes, target):
    for n in nodes:
        if n['tag'] == target:
            return n
        res = find_child_tag(n['children'], target)
        if res:
            return res
    return None


def _tlv_items(buf):
    """Parse ``buf`` as a flat run of (u16 tag, u32 len, payload) records
    covering the whole buffer, or ``None`` when it doesn't fit — the page
    (scene) container nests plain TLV runs inside leaf payloads."""
    items = []
    off = 0
    n = len(buf)
    while off < n:
        if off + 6 > n:
            return None
        tag = struct.unpack_from('<H', buf, off)[0]
        ln = struct.unpack_from('<I', buf, off + 2)[0]
        if tag == 0 or off + 6 + ln > n:
            return None
        items.append((tag, buf[off + 6:off + 6 + ln]))
        off += 6 + ln
    return items


def _tlv_find(items, tag):
    """First payload with ``tag`` in a ``_tlv_items`` list, or ``None``."""
    for t, p in items or ():
        if t == tag:
            return p
    return None


def _scan_vertex_positions(top, id2pos):
    """Accumulate every vertex's persistent id (hex) → ``(x, y, z)`` inches.

    A vertex is a ``C409`` record: ``DC05`` holds its persistent id (the
    ``DE05`` var-int payload), ``C509`` its 3×f64 position. Dimension
    connection points reference geometry by this id (see
    :func:`_parse_dimensions`). Ids are unique file-wide, so a single flat map
    resolves a reference to the exact vertex regardless of which context holds
    it — model-root dimensions reference model-root vertices (world space).
    Called once per top-level record: full_parse streams the TLV tree and
    never holds it whole."""

    def walk(nodes):
        for el in nodes:
            if el['tag'] == 'C409':
                dc05 = find_child_tag(el['children'], 'DC05')
                c509 = find_child_tag(el['children'], 'C509')
                if dc05 and c509 and len(c509['payload']) == 24:
                    p = dc05['payload']
                    if p[:2] == b'\xde\x05':
                        idlen = read_u32(p, 2)
                        idb = p[6:6 + idlen]
                    else:
                        idb = p
                    id2pos[idb.hex()] = struct.unpack('<3d', c509['payload'])
            if el['children']:
                walk(el['children'])

    walk([top])


def _scan_instance_transforms(top, world):
    """Accumulate each instance's persistent id (hex) → its WORLD transform
    (a 13-float matrix), walking the instance tree and composing
    parent × local at every ``6419``.  Per top-level record, like
    :func:`_scan_vertex_positions` — an instance chain never crosses
    top-level records.

    A dimension connects to geometry INSIDE a placed component; its connection
    reference names the vertex AND the instance holding it (see
    :func:`_parse_dimensions`). The vertex position is definition-local, so it
    must be lifted to world by the instance's transform for the dimension to
    land where the author drew it."""

    def walk(nodes, parent):
        for el in nodes:
            if el['tag'] == '6419':
                d007 = find_child_tag(el['children'], 'D007')
                dc05 = find_child_tag(d007['children'], 'DC05') if d007 else None
                iid = None
                if dc05:
                    p = dc05['payload']
                    idb = p[6:6 + read_u32(p, 2)] if p[:2] == b'\xde\x05' else p
                    iid = idb.hex()
                m = find_child_tag(el['children'], '6619')
                mat = list(struct.unpack('<13d', m['payload'])) \
                    if m and len(m['payload']) == 104 else None
                here = multiply_matrices(parent, mat) if mat else parent
                if iid:
                    world[iid] = here
                walk(el['children'], here)
            elif el['children']:
                walk(el['children'], parent)

    walk([top], None)


def _parse_dimensions(model_dat, id2pos, inst_world):
    """Linear dimensions (SketchUp's Dimension tool).

    A dimension entity is a ``5BCC`` record (raw bytes ``cc 5b``) holding:

    * ``5BCD`` / ``5BCE`` — the two connection points. Each wraps a ``5208``
      whose ``5209`` is the connection TYPE (1 = a free explicit point in
      ``520A``, already world space; 2 = connected to geometry, ``520A`` is
      zero and ``520B → 53FC`` names the target: ``53FD`` = the vertex by
      persistent id, ``53FE`` = a length-prefixed persistent id of the
      INSTANCE holding it — the vertex position is definition-local, so it is
      lifted to world by that instance's transform).
    * ``5BCF`` — the dimension plane's x-axis; ``5BD0`` — its normal.
    * ``5BD2`` — the offset distance (inches): how far the dimension line sits
      from the measured segment, along the in-plane perpendicular.

    The measured value is auto-computed from the two points (no cached text on
    the samples seen), so callers format it themselves. Endpoints come out in
    WORLD space (inches). A connection point that cannot be resolved drops the
    whole dimension (fail-safe)."""
    dims = []
    i = 0
    n = len(model_dat)
    while True:
        j = model_dat.find(b'\xcc\x5b', i)
        if j < 0:
            break
        i = j + 1
        if j + 6 > n:
            continue
        ln = struct.unpack_from('<I', model_dat, j + 2)[0]
        if ln < 40 or j + 6 + ln > n:
            continue
        body = _tlv_items(model_dat[j + 6:j + 6 + ln])
        if body is None:
            continue
        tags = [t for t, _ in body]
        if 0x5BCD not in tags or 0x5BCE not in tags:
            continue

        def _point(block_payload):
            blk = _tlv_find(_tlv_items(block_payload) or [], 0x5208)
            if blk is None:
                return None
            sub = _tlv_items(blk) or []
            typ = _tlv_find(sub, 0x5209)
            typ = struct.unpack('<I', typ)[0] if typ and len(typ) == 4 else None
            if typ == 1:
                pos = _tlv_find(sub, 0x520A)
                return struct.unpack('<3d', pos) if pos and len(pos) == 24 \
                    else None
            # type 2: resolve the geometry reference (vertex + instance).
            ref = _tlv_find(sub, 0x520B)
            f53fc = _tlv_find(_tlv_items(ref) or [], 0x53FC) if ref else None
            fi = _tlv_items(f53fc) or [] if f53fc else []
            vid = _tlv_find(fi, 0x53FD)
            iid = _tlv_find(fi, 0x53FE)
            if not vid:
                return None
            local = id2pos.get(vid.hex())
            if local is None:
                return None
            if iid and len(iid) >= 1 and iid[0] > 0 \
                    and 1 + iid[0] <= len(iid):
                w = inst_world.get(iid[1:1 + iid[0]].hex())
                if w:
                    return transform_point(local, w)
            return local           # model-root vertex — already world

        a = _point(_tlv_find(body, 0x5BCD))
        b = _point(_tlv_find(body, 0x5BCE))
        if a is None or b is None:
            continue
        xaxis = _tlv_find(body, 0x5BCF)
        normal = _tlv_find(body, 0x5BD0)
        off = _tlv_find(body, 0x5BD2)
        dims.append({
            'a': a, 'b': b,
            'plane_x': struct.unpack('<3d', xaxis)
            if xaxis and len(xaxis) == 24 else None,
            'normal': struct.unpack('<3d', normal)
            if normal and len(normal) == 24 else None,
            'offset': struct.unpack('<d', off)[0]
            if off and len(off) == 8 else 0.0,
        })
    return dims


def _find_page_node(top):
    """Return the ``0702`` scenes node inside *top*'s subtree, or ``None``.

    Called per top-level record; retaining the (small) 0702 subtree is the
    only thing kept alive past the streaming loop."""
    def find(nodes):
        for el in nodes:
            if el['tag'] == '0702':
                return el
            r = find(el['children'])
            if r is not None:
                return r
        return None
    return find([top])


def _parse_pages(node):
    """Scenes ("pages"). The 0702 node's payload nests 6D60 > 6D61 > one
    7148 record per page:

    * 6F54 > 6F55 — page name (UTF-8)
    * 714A > 34BC — camera: 34BD eye, 34BE target, 34BF up (3×f64, inches),
      34C4 field of view (degrees), 34C2 u8 = PERSPECTIVE flag (00 =
      parallel projection — calibrated against the bundled scene
      thumbnails: parallel plans/elevations carry 00 and their 34C3
      visible height matches the thumbnail framing exactly, while
      perspective scenes carry 01 with a stale 34C3),
      34C3 f64 = visible height when parallel (inches)
    * 7150 — layers hidden in this page: (u8 length, var-int layer id) runs
    """
    if node is None:
        return []

    def sub(items, tag):
        for t, p in items or []:
            if t == tag:
                return p
        return None

    def vec3(p):
        return struct.unpack('<3d', p) if p is not None and len(p) == 24 \
            else None

    pages = []
    for t60, p60 in _tlv_items(node['payload']) or []:
        if t60 != 0x6D60:
            continue
        for t61, p61 in _tlv_items(p60) or []:
            if t61 != 0x6D61:
                continue
            for t48, p48 in _tlv_items(p61) or []:
                if t48 != 0x7148:
                    continue
                items = _tlv_items(p48)
                if items is None:
                    continue
                page = {'name': '', 'eye': None, 'target': None, 'up': None,
                        'fov': 35.0, 'parallel': False, 'ortho_height': 0.0,
                        'hidden_layer_ids': []}
                head = _tlv_items(sub(items, 0x6F54))
                name = sub(head, 0x6F55)
                if name:
                    page['name'] = name.decode('utf-8', errors='replace')
                cam_wrap = _tlv_items(sub(items, 0x714A))
                cam = _tlv_items(sub(cam_wrap, 0x34BC)) if cam_wrap else None
                if cam:
                    page['eye'] = vec3(sub(cam, 0x34BD))
                    page['target'] = vec3(sub(cam, 0x34BE))
                    page['up'] = vec3(sub(cam, 0x34BF))
                    fov = sub(cam, 0x34C4)
                    if fov is not None and len(fov) == 8:
                        page['fov'] = struct.unpack('<d', fov)[0]
                    flag = sub(cam, 0x34C2)
                    page['parallel'] = bool(flag) and flag[0] == 0
                    height = sub(cam, 0x34C3)
                    if height is not None and len(height) == 8:
                        page['ortho_height'] = struct.unpack('<d', height)[0]
                hidden = sub(items, 0x7150)
                off = 0
                while hidden and off + 1 <= len(hidden):
                    ln = hidden[off]
                    if ln == 0 or off + 1 + ln > len(hidden):
                        break
                    page['hidden_layer_ids'].append(
                        parse_var_int(hidden, off + 1, ln))
                    off += 1 + ln
                if page['eye'] is not None and page['target'] is not None:
                    pages.append(page)
    return pages

def find_all_nodes_rec(nodes, target_tag, results):
    for n in nodes:
        if n['tag'] == target_tag:
            results.append(n)
        find_all_nodes_rec(n['children'], target_tag, results)

def extract_entity_id(node):
    for child in node['children']:
        if child['tag'] == 'DE05':
            return parse_var_int(child['payload'], 0, len(child['payload']))
        if child['tag'] == 'DC05':
            payload = child['payload']
            if payload.startswith(bytes([0xDE, 0x05])):
                de05_len = read_u32(payload, 2)
                return parse_var_int(payload, 6, de05_len)
    for child in node['children']:
        res = extract_entity_id(child)
        if res is not None:
            return res
    return None


def _tlv_flat(payload):
    """Walk a raw payload as a flat TLV sequence; returns [(tag_hex, body)]."""
    pos = 0
    out = []
    while pos <= len(payload) - 6:
        tag = payload[pos:pos+2].hex().upper()
        size = read_u32(payload, pos+2)
        if pos + 6 + size > len(payload):
            break
        out.append((tag, payload[pos+6:pos+6+size]))
        pos += 6 + size
    return out


def _extract_uv_transforms(dc05_payload):
    """Per-face texture-mapping matrices from a face's DC05 entity-info blob.

    A positioned / photo-fitted texture stores its mapping per face under
    ``DC05 -> DD05 -> B136 -> B236 -> 1027 -> 1127 (front) / 1227 (back)
    -> 1327 -> 1527``: a 3x3 row-major matrix of f64 that maps
    **texture space -> face plane**; consumers invert it to get UVs
    (see the ``Face.uv_transform`` docs in model.py for the exact recipe).

    Returns:
        ``(front, back)`` — each a 9-tuple of floats, or ``None`` when the
        face carries no positioned mapping on that side.
    """
    def _find(seq, tag):
        for t, body in seq:
            if t == tag:
                return body
        return None

    dd05 = _find(_tlv_flat(dc05_payload), 'DD05')
    if dd05 is None:
        return None, None
    b136 = _find(_tlv_flat(dd05), 'B136')
    if b136 is None:
        return None, None
    b236 = _find(_tlv_flat(b136), 'B236')
    if b236 is None:
        return None, None
    t1027 = _find(_tlv_flat(b236), '1027')
    if t1027 is None:
        return None, None
    sides = _tlv_flat(t1027)
    result = []
    for side_tag in ('1127', '1227'):
        side = _find(sides, side_tag)
        mat = None
        if side is not None:
            t1327 = _find(_tlv_flat(side), '1327')
            if t1327 is not None:
                t1527 = _find(_tlv_flat(t1327), '1527')
                if t1527 is not None and len(t1527) == 72:
                    mat = struct.unpack('<9d', t1527)
        result.append(mat)
    return result[0], result[1]


def _extract_geometry_from_nodes(elements, builder):
    for el in elements:
        tag = el['tag']

        if tag == 'C409':
            v_id = extract_entity_id(el)
            c509 = find_child_tag(el['children'], 'C509')
            if v_id is not None and c509 and len(c509['payload']) >= 24:
                x = read_f64(c509['payload'], 0)
                y = read_f64(c509['payload'], 8)
                z = read_f64(c509['payload'], 16)
                builder.vertices[v_id] = (x, y, z)

        elif tag == 'B80B':
            e_id = extract_entity_id(el)
            if e_id is not None:
                v1_node = find_child_tag(el['children'], 'B90B')
                v2_node = find_child_tag(el['children'], 'BA0B')
                v1 = parse_var_int(v1_node['payload'], 0, len(v1_node['payload'])) if v1_node else None
                v2 = parse_var_int(v2_node['payload'], 0, len(v2_node['payload'])) if v2_node else None
                builder.edges[e_id] = (v1, v2)
                # D007 -> D307 = edge display flags: base 0x06, plus
                # 0x01 hidden, 0x08|0x10 soft/smooth (cross-validated
                # against the same models in the classic MFC format,
                # 26k edges: 0x06 plain, 0x07 hidden, 0x1E soft+smooth)
                d007 = next((c for c in el['children'] if c['tag'] == 'D007'), None)
                if d007:
                    d307 = next((c for c in d007['children'] if c['tag'] == 'D307'), None)
                    if d307 is not None and d307['payload']:
                        builder.edge_flags[e_id] = d307['payload'][0]

        elif tag == 'AC0D':
            f_id = extract_entity_id(el)
            if f_id is not None:
                normal = (0.0, 0.0, 1.0)
                ad0d = find_child_tag(el['children'], 'AD0D')
                if ad0d and len(ad0d['payload']) >= 24:
                    nx = read_f64(ad0d['payload'], 0)
                    ny = read_f64(ad0d['payload'], 8)
                    nz = read_f64(ad0d['payload'], 16)
                    normal = (nx, ny, nz)

                ae0d = find_child_tag(el['children'], 'AE0D')
                loops = []
                if ae0d:
                    loop_nodes = []
                    find_all_nodes_rec(ae0d['children'], '9411', loop_nodes)
                    for ln in loop_nodes:
                        co_edges = []
                        co_nodes = []
                        find_all_nodes_rec(ln['children'], 'A00F', co_nodes)
                        for cn in co_nodes:
                            payload = cn['payload']
                            edge_id = None
                            orient = None
                            sub_pos = 0
                            while sub_pos < len(payload) - 6:
                                sub_tag = payload[sub_pos:sub_pos+2]
                                sub_size = read_u32(payload, sub_pos+2)
                                if sub_pos + 6 + sub_size <= len(payload):
                                    val = parse_var_int(payload, sub_pos+6, sub_size)
                                    if sub_tag == bytes([0xA1, 0x0F]):
                                        edge_id = val
                                    elif sub_tag == bytes([0xA2, 0x0F]):
                                        orient = val
                                sub_pos += 6 + sub_size
                            if edge_id is not None and orient is not None:
                                # Normalize to the documented CoEdge
                                # contract (+1 = same direction as the
                                # edge, -1 = reversed) - the raw A20F
                                # value is SketchUp's own bit (0 = forward,
                                # 1 = reversed).
                                co_edges.append(
                                    (edge_id, 1 if orient == 0 else -1))
                        if co_edges:
                            loops.append(co_edges)
                face_mat_id = None
                uv_front = uv_back = None
                face_hidden = False
                d007 = next((c for c in el['children'] if c['tag'] == 'D007'), None)
                if d007:
                    d107 = next((c for c in d007['children'] if c['tag'] == 'D107'), None)
                    if d107:
                        face_mat_id = parse_var_int(d107['payload'], 0, len(d107['payload']))
                    dc05 = next((c for c in d007['children'] if c['tag'] == 'DC05'), None)
                    if dc05 is not None:
                        uv_front, uv_back = _extract_uv_transforms(dc05['payload'])
                    # D307 = display flags, same record edges already read
                    # (base 0x06, +0x01 hidden) - faces carry the identical
                    # tag under their own D007 container.
                    d307 = next((c for c in d007['children'] if c['tag'] == 'D307'), None)
                    if d307 is not None and d307['payload']:
                        face_hidden = bool(d307['payload'][0] & 0x01)
                # Back-side material: the AF0D child of the face node (a face
                # painted only on its back — common when the author paints the
                # visible side of a downward-facing cap — carries AF0D but no
                # D107).
                back_mat_id = None
                af0d = next((c for c in el['children'] if c['tag'] == 'AF0D'), None)
                if af0d and af0d['payload']:
                    back_mat_id = parse_var_int(af0d['payload'], 0, len(af0d['payload']))
                builder.faces[f_id] = {'loops': loops, 'normal': normal,
                                       'material_id': face_mat_id,
                                       'back_material_id': back_mat_id,
                                       'uv_transform': uv_front,
                                       'uv_transform_back': uv_back,
                                       'hidden': face_hidden}

        elif tag == '6419':
            nodes_to_search = el['children'] if el['children'] else [el]
            guid = None
            def_idx = None
            name = None
            matrix = []
            guid_node = find_child_tag(nodes_to_search, '6819')
            if guid_node and len(guid_node['payload']) == 16:
                guid = guid_node['payload'].hex().upper()
            def_idx_node = find_child_tag(nodes_to_search, '6719')
            if def_idx_node:
                def_idx = parse_var_int(def_idx_node['payload'], 0, len(def_idx_node['payload']))
            name_node = find_child_tag(nodes_to_search, '6519')
            if name_node:
                name = name_node['payload'].decode('utf-8', errors='replace')
            mat_node = find_child_tag(nodes_to_search, '6619')
            if mat_node and len(mat_node['payload']) >= 104:
                for idx in range(13):
                    matrix.append(read_f64(mat_node['payload'], idx * 8))

            # Instance-level material (SketchUp "paint the component"):
            # same D007/D107 structure faces use. Faces whose own material
            # is None inherit this — the SDK resolves that inheritance when
            # exporting, so consumers need the raw value to do the same.
            inst_mat_id = None
            inst_hidden = False
            # Unresolved layer ID (as a string) - the raw model has no
            # layer_id_to_name mapping in scope here, so resolution to a
            # name happens later in model.py, where that mapping is
            # already available. Only the instance's own explicit layer
            # override is captured this way - an instance that doesn't
            # specify one inherits its *placement's* layer, which is only
            # knowable once the scene graph is actually flattened (see
            # SkpFile.build_scene()'s InstanceNode.layer for that).
            inst_layer_id = None
            inst_properties = {}
            d007 = next((c for c in el['children'] if c['tag'] == 'D007'),
                        None)
            if d007:
                d107 = next((c for c in d007['children']
                             if c['tag'] == 'D107'), None)
                if d107:
                    inst_mat_id = parse_var_int(
                        d107['payload'], 0, len(d107['payload']))
                d207 = next((c for c in d007['children']
                             if c['tag'] == 'D207'), None)
                if d207 and d207['payload']:
                    inst_layer_id = parse_var_int(
                        d207['payload'], 0, len(d207['payload']))
                inst_properties = extract_dynamic_properties(d007)
                # D307 = display flags, same record edges/faces already
                # read (base 0x06, +0x01 hidden).
                d307 = next((c for c in d007['children']
                             if c['tag'] == 'D307'), None)
                if d307 is not None and d307['payload']:
                    inst_hidden = bool(d307['payload'][0] & 0x01)

            builder.instances.append({
                'offset': el['offset'],
                'ref_guid': guid,
                'ref_idx': def_idx,
                'name': name,
                'matrix': matrix,
                'material_id': inst_mat_id,
                'hidden': inst_hidden,
                'layer_id': inst_layer_id,
                'properties': inst_properties,
                'children': el['children']
            })

        elif el['children']:
            _extract_geometry_from_nodes(el['children'], builder)


# ── Transforms ───────────────────────────────────────────────────────────

def transform_point(p, mat):
    if not mat or len(mat) < 12:
        return p
    x, y, z = p
    tx = mat[0]*x + mat[1]*y + mat[2]*z + mat[9]
    ty = mat[3]*x + mat[4]*y + mat[5]*z + mat[10]
    tz = mat[6]*x + mat[7]*y + mat[8]*z + mat[11]
    return (tx, ty, tz)

def multiply_matrices(parent, child):
    if not parent:
        return child
    if not child:
        return parent
    p_r0 = [parent[0], parent[1], parent[2], parent[9]]
    p_r1 = [parent[3], parent[4], parent[5], parent[10]]
    p_r2 = [parent[6], parent[7], parent[8], parent[11]]
    c_c0 = [child[0], child[3], child[6], 0]
    c_c1 = [child[1], child[4], child[7], 0]
    c_c2 = [child[2], child[5], child[8], 0]
    c_c3 = [child[9], child[10], child[11], 1]
    def dot(row, col):
        return sum(r*c for r, c in zip(row, col))
    out = [0.0] * 13
    out[0] = dot(p_r0, c_c0)
    out[1] = dot(p_r0, c_c1)
    out[2] = dot(p_r0, c_c2)
    out[3] = dot(p_r1, c_c0)
    out[4] = dot(p_r1, c_c1)
    out[5] = dot(p_r1, c_c2)
    out[6] = dot(p_r2, c_c0)
    out[7] = dot(p_r2, c_c1)
    out[8] = dot(p_r2, c_c2)
    out[9] = dot(p_r0, c_c3)
    out[10] = dot(p_r1, c_c3)
    out[11] = dot(p_r2, c_c3)
    out[12] = parent[12] * child[12]
    return out


# ── Dynamic properties ───────────────────────────────────────────────────

def extract_dynamic_properties(d007):
    """Extract Dynamic Component attribute key-value pairs from a D007 container node.

    Dynamic properties are stored in a nested TLV hierarchy under the DC05 tag:
    - Container tags (DD05, B536, B136, B236, B336, B036, A438) wrap property sub-trees.
    - Tag B636 contains the attribute key name (UTF-8 string).
    - Tag AD38 contains the attribute value (UTF-8 string).
    """
    dc05 = next((c for c in d007['children'] if c['tag'] == 'DC05'), None)
    if not dc05:
        return {}
    # TLV container tags nesting the dynamic property key/value nodes
    prop_container_tags = ['DD05', 'B536', 'B136', 'B236', 'B336', 'B036', 'A438']
    prop_elements = parse_tlv_recursive(dc05['payload'], 0, len(dc05['payload']), prop_container_tags)
    properties = {}
    current_key = None
    def extract_props(nodes):
        nonlocal current_key
        for n in nodes:
            tag = n['tag']
            if tag == 'B636':
                # Property key name (UTF-8 string)
                current_key = n['payload'].decode('utf-8', errors='replace')
            elif tag == 'AD38' and current_key:
                # Property value (UTF-8 string) matching preceding key
                val = n['payload'].decode('utf-8', errors='replace')
                properties[current_key] = val
                current_key = None
            extract_props(n['children'])
    extract_props(prop_elements)
    return properties


# ── ZIP entry size validation ────────────────────────────────────────────

# A ZIP entry's declared uncompressed size (ZipInfo.file_size) is
# untrusted central-directory metadata: it can be set independently of
# what the compressed stream actually decompresses to, and even when
# genuine, DEFLATE can expand highly compressible data by three orders of
# magnitude. zipfile.ZipFile.read() decompresses (and so allocates) up to
# that declared size with no ceiling of its own. Real production
# model.dat entries are observed at ~10x compression, so both limits below
# leave generous headroom for legitimate files while rejecting the kind
# of declared-size lie or extreme ratio a genuine file would never need.
_MAX_UNCOMPRESSED_ENTRY_BYTES = 16 * 1024 ** 3  # 16 GB
_MAX_COMPRESSION_RATIO = 1000
_RATIO_CHECK_THRESHOLD_BYTES = 1024 ** 2  # 1 MB


def _validate_zip_entry_size(zf: "zipfile.ZipFile", name: str) -> None:
    """Reject a ZIP entry whose declared uncompressed size is implausible,
    before any code reads (and so decompresses/allocates for) it."""
    info = zf.getinfo(name)
    declared = info.file_size
    if declared <= 0:
        return
    if declared > _MAX_UNCOMPRESSED_ENTRY_BYTES:
        raise SkpParseError(
            f"ZIP entry '{name}' declares {declared} bytes uncompressed, "
            f"exceeding the {_MAX_UNCOMPRESSED_ENTRY_BYTES}-byte safety ceiling",
            stage="zip_extract",
        )
    if declared >= _RATIO_CHECK_THRESHOLD_BYTES:
        compressed = info.compress_size
        if compressed <= 0 or declared / compressed > _MAX_COMPRESSION_RATIO:
            raise SkpParseError(
                f"ZIP entry '{name}' declares an implausible compression ratio "
                f"({declared} bytes from {compressed} bytes compressed) - "
                "likely a decompression bomb",
                stage="zip_extract",
            )


# ── Model metadata (meta/meta.dat) ───────────────────────────────────────

# meta/meta.dat uses the same low-level TLV framing as model.dat (2-byte
# tag + 4-byte little-endian length + payload), but as one flat
# (non-recursive) record list wrapped in a single outer record. Confirmed
# against a real fixture: the outer wrapper is tag 0x6400; among its
# direct children, tag 0x6D00 carries the model's unit-system string as
# plain text ("Millimeter" in the fixture) - siblings carry the SketchUp
# version, save path, and thumbnail references, none of which any parser
# currently surfaces either.
_META_WRAPPER_TAG = b'\x64\x00'
_META_UNITS_TAG = b'\x6D\x00'


def _read_meta_units(meta_bytes: bytes):
    """Extract the model's unit-system string from a VFF file's
    meta/meta.dat contents, or ``None`` if the expected tags aren't
    found."""
    pos = 0
    while pos + 6 <= len(meta_bytes):
        tag = meta_bytes[pos:pos + 2]
        size = struct.unpack_from('<I', meta_bytes, pos + 2)[0]
        if pos + 6 + size > len(meta_bytes):
            break
        if tag == _META_WRAPPER_TAG:
            return _read_meta_units(meta_bytes[pos + 6:pos + 6 + size])
        if tag == _META_UNITS_TAG:
            return meta_bytes[pos + 6:pos + 6 + size].decode('utf-8', errors='replace')
        pos += 6 + size
    return None


# ── Texture extraction ───────────────────────────────────────────────────

def _extract_texture(zf, xml_name, mat_elem, ns):
    """Extract a material's texture from its ``<mat:texture>`` block.

    SketchUp stores the texture image next to the ``material.xml`` inside the
    ZIP (``materials/<folder>/<image>``), and the real-world tile size in the
    ``xScale`` / ``yScale`` attributes (inches).

    Args:
        zf: The open ZIP file.
        xml_name: Archive name of the ``material.xml`` being parsed.
        mat_elem: Its ``<mat:material>`` element.
        ns: Namespace map for the material schema.

    Returns:
        Dict with keys ``filename``, ``x_scale``, ``y_scale`` (inches, 0.0
        when absent) and ``data`` (raw image bytes, or ``None`` when the
        image entry is missing) — or ``None`` when the material carries no
        texture.
    """
    tex_elem = mat_elem.find('mat:texture', ns)
    if tex_elem is None:
        return None
    filename = tex_elem.get('textureFilename', '') or ''
    try:
        x_scale = float(tex_elem.get('xScale', 0) or 0)
    except ValueError:
        x_scale = 0.0
    try:
        y_scale = float(tex_elem.get('yScale', 0) or 0)
    except ValueError:
        y_scale = 0.0

    folder = xml_name.rsplit('/', 1)[0]
    data = None
    candidate = folder + '/' + filename if filename else None
    names = zf.namelist()
    if candidate and candidate in names:
        _validate_zip_entry_size(zf, candidate)
        data = zf.read(candidate)
    else:
        # Image name may differ from textureFilename (observed: e.g.
        # "Saftey" vs "Safety") — take the folder's non-XML sibling.
        for entry in names:
            if (entry.startswith(folder + '/') and entry != xml_name
                    and not entry.lower().endswith('.xml')):
                _validate_zip_entry_size(zf, entry)
                data = zf.read(entry)
                if not filename:
                    filename = entry.rsplit('/', 1)[-1]
                break
    if data is None:
        # Colourized copies ("[Name]1", type="2") keep no image of their
        # own — their <mat:image path> points into the SOURCE material's
        # folder ("materials/<other>/<image>"), sometimes prefixed "./".
        img_elem = tex_elem.find('mat:images/mat:image', ns)
        img_path = (img_elem.get('path', '') or '') if img_elem is not None else ''
        img_path = img_path.lstrip('./')
        for cand in (img_path, folder + '/' + img_path):
            if cand and cand in names:
                _validate_zip_entry_size(zf, cand)
                data = zf.read(cand)
                if not filename:
                    filename = cand.rsplit('/', 1)[-1]
                break
    return {'filename': filename, 'x_scale': x_scale, 'y_scale': y_scale,
            'data': data}


# ── Full parse pipeline ──────────────────────────────────────────────────

def full_parse(skp_path: str) -> Dict[str, Any]:
    """Run the complete SKP parsing pipeline.

    Args:
        skp_path: Path to .skp file.

    Returns:
        Dict with keys: version, definitions_dict, layer_colors,
        layer_id_to_name, materials, elements, defs_dict
    """
    t0 = time.monotonic()
    file_size = os.path.getsize(skp_path)
    logger.info("Parsing %s (%d bytes)", skp_path, file_size)

    # 1. Find ZIP offset
    with open(skp_path, 'rb') as f:
        header = f.read(512)

    if not header.startswith(b'\xFF\xFE\xFF\x0E'):
        raise SkpParseError(
            "Not a valid SketchUp file (bad header magic)", stage="header")

    # Classic (pre-2021) files are MFC CArchive streams, not VFF/ZIP —
    # route them to the legacy walker (same output shape).
    from .legacy import is_legacy, full_parse_legacy
    if is_legacy(header):
        logger.debug("Detected legacy MFC container; routing to legacy walker")
        return full_parse_legacy(skp_path)

    version = "unknown"
    second_marker = header.find(b'\xFF\xFE\xFF', 4)
    if second_marker > 0:
        ver_start = second_marker + 4
        ver_bytes = header[ver_start:]
        ver_text = ver_bytes.decode('utf-16-le', errors='ignore')
        brace_start = ver_text.find('{')
        brace_end = ver_text.find('}')
        if brace_start >= 0 and brace_end > brace_start:
            version = ver_text[brace_start:brace_end+1]

    logger.debug("Detected version %s (VFF/ZIP container)", version)

    pk_pos = header.find(b'PK\x03\x04')
    if pk_pos < 0:
        with open(skp_path, 'rb') as f:
            chunk = f.read(4096)
            pk_pos = chunk.find(b'PK\x03\x04')
    if pk_pos < 0:
        raise SkpParseError(
            "No ZIP container found", stage="zip_extract")

    # 2. Extract ZIP contents
    with open(skp_path, 'rb') as f:
        f.seek(pk_pos)
        zip_bytes = f.read()
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes), 'r')
    logger.debug("Opened ZIP container: %d entries", len(zf.namelist()))

    # Materials & layer colors
    layer_colors = {}
    # Modern (VFF) files derive layers from Layer_<name>-prefixed materials,
    # which carry no visibility flag of their own - unlike legacy MFC files,
    # there is currently no known tag exposing a VFF layer's hidden state,
    # so every VFF layer defaults to visible here.
    layer_hidden = {}
    materials = {}
    materials_by_folder = {}
    MAT_NS = {'mat': 'http://sketchup.google.com/schemas/sketchup/1.0/material'}
    for name in zf.namelist():
        if name.endswith('material.xml') and name.startswith('materials/'):
            _validate_zip_entry_size(zf, name)
            xml_data = zf.read(name)
            try:
                root = ET.fromstring(xml_data)
                mat_elem = root.find('.//mat:material', MAT_NS)
                if mat_elem is not None:
                    mat_name = mat_elem.get('name', 'unknown')
                    r = int(mat_elem.get('colorRed', 128))
                    g = int(mat_elem.get('colorGreen', 128))
                    b = int(mat_elem.get('colorBlue', 128))
                    # 'trans' only applies when useTrans="1"; otherwise the
                    # attribute is a leftover default and the material is
                    # fully opaque. Without this check most materials read
                    # as 50% transparent (or fully invisible for trans="0").
                    # The stored value is a TRANSPARENCY (0 = opaque,
                    # 1 = fully transparent) — e.g. SketchUp's library
                    # "Translucent Glass Blue" (70% opacity) stores
                    # trans="0.3" — so the exposed opacity is 1 - trans.
                    if mat_elem.get('useTrans') == '1':
                        trans = 1.0 - float(mat_elem.get('trans', 0.0))
                        trans = min(max(trans, 0.0), 1.0)
                    else:
                        trans = 1.0
                    folder_name = name.split('/')[1] if len(name.split('/')) > 1 else ''
                    # type="2" marks a colourized copy ("[Name]1"): the
                    # shared texture must be re-tinted toward the material
                    # colour before display. colorizeType 0 = hue shift,
                    # 1 = tint (greyscale then colour).
                    colorized = mat_elem.get('type') == '2'
                    try:
                        colorize_type = int(mat_elem.get('colorizeType', 0) or 0)
                    except ValueError:
                        colorize_type = 0
                    mat_obj = {'name': mat_name, 'color': {'r': r, 'g': g, 'b': b}, 'transparency': trans,
                               'colorized': colorized, 'colorize_type': colorize_type}
                    tex = _extract_texture(zf, name, mat_elem, MAT_NS)
                    if tex is not None:
                        mat_obj['texture'] = tex
                    materials[mat_name] = mat_obj
                    if folder_name:
                        materials_by_folder[folder_name] = mat_obj
                    if mat_name.startswith('Layer_'):
                        layer_colors[mat_name[6:]] = (r, g, b)
                        layer_hidden[mat_name[6:]] = False
            except Exception:
                logger.debug("Failed to parse material.xml %r", name, exc_info=True)

    # Thumbnail
    thumbnail_data = None
    if 'meta/model_thumbnail.png' in zf.namelist():
        _validate_zip_entry_size(zf, 'meta/model_thumbnail.png')
        thumbnail_data = zf.read('meta/model_thumbnail.png')

    # Units (meta/meta.dat) - VFF-only; legacy files carry no equivalent
    # container.
    units = None
    if 'meta/meta.dat' in zf.namelist():
        try:
            _validate_zip_entry_size(zf, 'meta/meta.dat')
            units = _read_meta_units(zf.read('meta/meta.dat'))
        except Exception:
            units = None
            logger.debug("Failed to read units from meta/meta.dat", exc_info=True)

    # Styles: face colors live in styles/*/style.xml as signed-int32 ARGB
    # variants — item id 4000 is the front (default) face color, 4001 the
    # back face color. Viewers need them to shade unpainted faces the way
    # SketchUp does (an author may e.g. set a green back color so unpainted
    # garden faces read as grass).
    styles = []
    for name in zf.namelist():
        if not (name.startswith('styles/') and name.endswith('style.xml')):
            continue
        try:
            _validate_zip_entry_size(zf, name)
            sroot = ET.fromstring(zf.read(name))
        except (ET.ParseError, DefusedXmlException):
            continue
        STY = '{http://sketchup.google.com/schemas/sketchup/1.0/style}'
        TYP = '{http://sketchup.google.com/schemas/1.0/types}'
        style_el = sroot.find(f'{STY}style')
        if style_el is None:
            continue
        colors = {}
        for item in style_el.findall(f'{STY}item'):
            iid = item.get('id')
            var = item.find(f'{TYP}variant')
            if iid in ('4000', '4001') and var is not None and var.text:
                try:
                    v = int(var.text) & 0xFFFFFFFF
                except ValueError:
                    continue
                colors[iid] = ((v >> 16) & 255, (v >> 8) & 255, v & 255)
        styles.append({'name': style_el.get('name', ''),
                       'front_color': colors.get('4000'),
                       'back_color': colors.get('4001')})

    logger.debug("Parsed %d materials, %d styles", len(materials), len(styles))

    _validate_zip_entry_size(zf, 'model.dat')
    model_dat = zf.read('model.dat')
    zf.close()
    logger.debug("Read model.dat: %d bytes", len(model_dat))

    # 3. Walk the TLV tree one top-level record at a time (instead of
    # building the whole file's tree at once) so peak memory is bounded by
    # the single largest definition/layer-manager/material-manager/root
    # block, not by the file's total node count. Real production files can
    # have 100k+ separate component definitions; materializing all of them
    # simultaneously is what actually exhausts memory on large files - not
    # the (comparatively modest, ~1x) cost of decompressing model.dat
    # itself. See iter_top_level_lazy() for the mechanics.

    # Layer ID -> name
    layer_id_to_name = {}
    def collect_layers(nodes):
        for el in nodes:
            if el['tag'] == '993A':
                for child in el['children']:
                    if child['tag'] == '8C3C':
                        dc05 = find_child_tag(child['children'], 'DC05')
                        name_node = find_child_tag(child['children'], '8D3C')
                        if dc05 and name_node:
                            payload = dc05['payload']
                            if payload.startswith(bytes([0xDE, 0x05])):
                                de05_len = read_u32(payload, 2)
                                l_id = parse_var_int(payload, 6, de05_len)
                            else:
                                l_id = parse_var_int(payload, 0, len(payload))
                            l_name = name_node['payload'].decode('utf-8', errors='replace')
                            layer_id_to_name[l_id] = l_name
            collect_layers(el['children'])

    # Material ID -> name
    material_id_to_name = {}
    def collect_material_ids(nodes):
        for el in nodes:
            if el['tag'] == 'C832':
                dc05 = find_child_tag(el['children'], 'DC05')
                name_node = find_child_tag(el['children'], 'CC32')
                if dc05 and name_node:
                    payload = dc05['payload']
                    if payload.startswith(bytes([0xDE, 0x05])):
                        de05_len = read_u32(payload, 2)
                        m_id = parse_var_int(payload, 6, de05_len)
                    else:
                        m_id = parse_var_int(payload, 0, len(payload))
                    m_name = name_node['payload'].decode('utf-8', errors='replace')
                    material_id_to_name[m_id] = m_name
            collect_material_ids(el['children'])

    # Component definitions
    defs_dict = {}
    def collect_defs(nodes):
        for el in nodes:
            if el['tag'] == '7C15':
                guid = None
                name = None
                faces_camera = False
                shadows_face_sun = False
                is_image = False
                for child in el['children']:
                    if child['tag'] == '7D15' and len(child['payload']) == 16:
                        guid = child['payload'].hex().upper()
                    elif child['tag'] == '7E15':
                        name = child['payload'].decode('utf-8', errors='replace')
                    elif child['tag'] == '581B':
                        # Component behavior flags: sub-TLV 5D1B == 1 marks
                        # "always faces camera" (2D people/tree cut-outs);
                        # its companion 5E1B is "shadows face sun".
                        pos = 0
                        pl = child['payload']
                        while pos <= len(pl) - 6:
                            sub_tag = pl[pos:pos+2].hex().upper()
                            sub_size = read_u32(pl, pos+2)
                            if pos + 6 + sub_size > len(pl):
                                break
                            if sub_tag == '5D1B' and sub_size >= 1:
                                faces_camera = parse_var_int(
                                    pl, pos+6, sub_size) == 1
                            elif sub_tag == '5E1B' and sub_size >= 1:
                                shadows_face_sun = parse_var_int(
                                    pl, pos+6, sub_size) == 1
                            pos += 6 + sub_size
                    elif child['tag'] == '8315' and child['payload']:
                        # Definition kind: observed 0/1 for ordinary
                        # component/group definitions, 2 for the quad
                        # definition backing an Image entity.
                        is_image = parse_var_int(
                            child['payload'], 0, len(child['payload'])) == 2
                ent_id = extract_entity_id(el)
                builder = _GeometryBuilder()
                _extract_geometry_from_nodes(el['children'], builder)
                defs_dict[ent_id] = {'guid': guid, 'name': name,
                                     'always_faces_camera': faces_camera,
                                     'shadows_face_sun': shadows_face_sun,
                                     'is_image': is_image, 'builder': builder}
            collect_defs(el['children'])

    root_builder = _GeometryBuilder()
    vertex_positions = {}
    instance_world = {}
    page_node = None

    for index, total, el in iter_top_level_lazy(model_dat, 0, len(model_dat), CONTAINER_TAGS):
        try:
            collect_layers([el])
            collect_material_ids([el])
            collect_defs([el])
            _scan_vertex_positions(el, vertex_positions)
            _scan_instance_transforms(el, instance_world)
            if page_node is None:
                page_node = _find_page_node(el)
            if el['tag'] == 'F601':
                _extract_geometry_from_nodes(el['children'], root_builder)
        except Exception as e:
            raise SkpParseError(
                f"Failed while processing top-level record: {e}",
                stage="tlv_walk", record_index=index, total_records=total,
                tag=el['tag'],
            ) from e
        # `el` (and its whole subtree) is now unreferenced and eligible for
        # garbage collection before the next top-level record is built.
        if index % _PROGRESS_INTERVAL == 0 or index == total - 1:
            logger.debug("Processed %d/%d top-level records", index + 1, total)

    logger.info(
        "Parse complete: %s (%d defs, %.2fs)",
        skp_path, len(defs_dict), time.monotonic() - t0,
    )

    if 1 not in layer_id_to_name:
        layer_id_to_name[1] = 'Layer0'
    if 'Layer0' not in layer_colors:
        layer_colors['Layer0'] = (136, 136, 136)
    if 'Layer0' not in layer_hidden:
        layer_hidden['Layer0'] = False

    defs_dict['ROOT'] = {'guid': 'ROOT', 'name': 'ROOT_MODEL', 'builder': root_builder}

    return {
        'version': version,
        'layer_colors': layer_colors,
        'layer_hidden': layer_hidden,
        'layer_id_to_name': layer_id_to_name,
        'pages': _parse_pages(page_node),
        'dimensions': _parse_dimensions(
            model_dat, vertex_positions, instance_world),
        'material_id_to_name': material_id_to_name,
        'materials': materials,
        'materials_by_folder': materials_by_folder,
        'defs_dict': defs_dict,
        'thumbnail_data': thumbnail_data,
        'styles': styles,
        'units': units,
    }

