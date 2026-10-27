"""Core parsing engine — battle-tested monolithic implementation.

This module contains the proven parsing logic extracted from the original
SketchUp reverse-engineering work. It is used internally by :class:`SkpFile`
to perform the actual binary parsing.

The public modules (parser.py, geometry.py, etc.) provide the clean API;
this module provides the working implementation.
"""

from __future__ import annotations

import logging
import os
import struct
import time
import zipfile
from typing import Any, Dict, List, Optional
# material.xml/style.xml come from inside the .skp's ZIP container, which is
# fully attacker-controlled input - stdlib ElementTree expands internal
# general entities with no limit, making a "billion laughs" DoS payload
# trivial to embed. defusedxml.ElementTree is a drop-in replacement (same
# ParseError class, same fromstring() signature) that rejects that class of
# payload outright instead.
import defusedxml.ElementTree as ET
from defusedxml.common import DefusedXmlException

import numpy as np

from .errors import SkpParseError
from ._vff_records import decode_record as _decode_geometry_record
from ._packed_geometry import PackedVertices, PackedEdges, PackedFaces, _PackedTable

try:
    import mapbox_earcut
except ImportError:
    # A declared dependency, but a compiled one: hosts that embed their own
    # Python (FreeCAD's bundled interpreter, for one) often can't install
    # it, and a hard top-level import made `import openskp` itself fail
    # there - even for callers that never triangulate a face. Only
    # triangulate_face_3d (the scene/mesh path) needs it; parsing, the
    # loop/edge data, the writer and editor all work without. That path
    # degrades to fan triangulation and says so once (see
    # _warn_earcut_missing).
    mapbox_earcut = None

# Silent by default (standard library convention: a library never installs
# its own handler or configures a level - the host application decides
# whether/where these go). To see progress: logging.getLogger("openskp")
# .setLevel(logging.DEBUG) plus a handler, e.g. logging.basicConfig().
logger = logging.getLogger("openskp")

# How often (in top-level records) to emit a DEBUG progress log during the
# main walk - coarse enough that it costs nothing on a 300k-definition file.
_PROGRESS_INTERVAL = 500

# Packing small models costs time without a material reduction in peak memory.
_COMPACT_MODEL_BYTES = 256 * 1024 * 1024


# ── TLV Parsing ──────────────────────────────────────────────────────────

def read_u32(data: bytes, offset: int) -> int:
    return struct.unpack_from('<I', data, offset)[0]

def read_f64(data: bytes, offset: int) -> float:
    return struct.unpack_from('<d', data, offset)[0]

def parse_var_int(data: bytes, offset: int, length: int) -> int:
    if offset >= 0 and length >= 0 and offset + length <= len(data):
        return int.from_bytes(data[offset:offset + length], 'little')
    # Out-of-range read: keep the byte-at-a-time loop so it raises exactly
    # as it always has.
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
    # Construction/guide lines and points: list 9113 → entity 6942, list
    # 9213 → entity 6C42. 6842 is the drawbase wrapper those entities share.
    # 6A42 / 6D42 / 6E42 / 6F42 stay leaves.
    '9113', '9213', '6942', '6C42', '6842',
}

# Every byte value's 2-character uppercase hex form, precomputed once.
# `data[pos:pos+2].hex().upper()` is a 2-byte slice (an allocation) plus two
# full string passes (hex() then upper()) on the *hot* path of the TLV
# parser - millions of times on a large real file. Two lookups plus a
# concat produce the identical 4-char string with no slice and no second
# pass - see openskp#244.
_HEX_UPPER = [format(i, '02X') for i in range(256)]

# One C call reads a TLV record header - the 2-byte tag as a little-endian u16
# plus the u32 length - instead of two byte-index lookups, a string concat and
# a separate ``read_u32`` -> ``struct.unpack_from`` round trip per record.
_unpack_header = struct.Struct('<HI').unpack_from

# u16 tag value -> its 4-character uppercase hex tag, i.e. the same string
# ``_HEX_UPPER[b0] + _HEX_UPPER[b1]`` builds for the two raw tag bytes. Built on
# first use (about 5 ms, 3 MB) and shared: every node carrying a given tag then
# points at one string object instead of allocating its own.
_TAG_STRINGS: Optional[List[str]] = None


def _tag_strings() -> List[str]:
    """The shared table, built in full and only then published, so a thread
    that races the first call sees either nothing or the complete table."""
    global _TAG_STRINGS
    table = _TAG_STRINGS
    if table is None:
        table = [_HEX_UPPER[v & 0xFF] + _HEX_UPPER[v >> 8]
                 for v in range(0x10000)]
        _TAG_STRINGS = table
    return table


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


def _detach_tlv_payloads(nodes):
    """Resolve the small subtrees retained by instances, releasing model.dat.

    An unread leaf otherwise keeps the entire decompressed model alive even
    after full_parse returns. Preserve all node fields, including opaque tags;
    only the private lazy payload cache changes.
    """
    for node in nodes:
        _ = node.payload
        if node.children:
            _detach_tlv_payloads(node.children)


def parse_tlv_recursive(data, start, end, container_tags=None, depth=0):
    if container_tags is None:
        container_tags = CONTAINER_TAGS
    pos = start
    elements = []
    append = elements.append
    tag_strings = _TAG_STRINGS or _tag_strings()
    unpack_header = _unpack_header
    last_header = end - 6
    while pos <= last_header:
        tag_value, size = unpack_header(data, pos)
        tag_hex = tag_strings[tag_value]
        payload_start = pos + 6
        payload_end = payload_start + size
        if payload_end > end:
            break
        if size > 0 and tag_hex in container_tags:
            children = parse_tlv_recursive(data, payload_start, payload_end, container_tags, depth+1)
        else:
            children = []
        append(_TlvNode(pos, tag_hex, size, children, data, payload_start, payload_end))
        pos = payload_end
    return elements


class _GeometryRecordNode(_TlvNode):
    """Internal-only decoded geometry; never used in retained instance trees."""

    __slots__ = ('_children', '_container_tags', 'record')

    def __init__(self, offset, tag, size, record, data, container_tags):
        self.offset = offset
        self.tag = tag
        self.size = size
        self._children = None
        self._data = data
        self._payload_start = offset + 6
        self._payload_end = offset + 6 + size
        self._payload = b''
        self._container_tags = container_tags
        self.record = record

    @property
    def children(self):
        # Generic searches still see the original tree. For example, a
        # definition missing its own id may resolve one inside its geometry.
        if self._children is None:
            self._children = parse_tlv_recursive(
                self._data, self._payload_start, self._payload_end,
                self._container_tags)
        return self._children


def _parse_tlv_geometry(data, start, end, container_tags):
    """Build the structural tree, decoding exact common geometry in place.

    The public TLV parser is unchanged. Unknown layouts take its usual node
    path, and instance subtrees always use it because raw builders expose
    their complete children (including any geometry and opaque records).
    """
    elements = []
    tag_strings = _TAG_STRINGS or _tag_strings()
    pos = start
    while pos <= end - 6:
        tag_value, size = _unpack_header(data, pos)
        tag = tag_strings[tag_value]
        payload_start = pos + 6
        payload_end = payload_start + size
        if payload_end > end:
            break
        if tag in ('C409', 'B80B', 'AC0D'):
            record = _decode_geometry_record(data, payload_start, size, tag)
            if record is not None:
                elements.append(_GeometryRecordNode(
                    pos, tag, size, record, data, container_tags))
                pos = payload_end
                continue
        if size > 0 and tag in container_tags:
            parser = parse_tlv_recursive if tag == '6419' else _parse_tlv_geometry
            children = parser(data, payload_start, payload_end, container_tags)
        else:
            children = []
        elements.append(_TlvNode(pos, tag, size, children, data,
                                 payload_start, payload_end))
        pos = payload_end
    return elements


def _flat_headers(data, start, end):
    """Scan [start, end) for direct-child (tag, offset, size) headers only,
    without recursing into any container - O(sibling count), not
    O(total node count). Used to locate top-level records one at a time so
    full_parse() never has to hold the whole file's TLV tree in memory at
    once (real production files can have 100k+ top-level definitions)."""
    headers = []
    pos = start
    tag_strings = _TAG_STRINGS or _tag_strings()
    while pos <= end - 6:
        tag_value, size = _unpack_header(data, pos)
        if pos + 6 + size > end:
            break
        headers.append((tag_strings[tag_value], pos, size))
        pos += 6 + size
    return headers


_DEFINITION_LIST_WRAPPER_CHAIN = ('7017', '7117')
_DEFINITION_TAG = '7C15'


def _unwrap_definitions_container(data, offset, size):
    """``F901`` wraps the file's whole component/group-definition list
    behind two more single-child containers (``7017``, then ``7117``)
    before reaching the real, independent list of one ``7C15`` per
    definition (openskp#264: on a real 359MB production file, one such
    list held 95,363 definitions and 95% of the entire decompressed
    model - see CHECKLIST.md for the full investigation). Returns the
    flat ``(tag, offset, size)`` headers of each ``7C15`` - a cheap,
    header-only scan at every level, no tree building - or ``None`` if
    the expected wrapper shape isn't found, so a caller can fall back to
    treating ``F901`` as an ordinary single record rather than risk
    silently mis-parsing a differently-shaped file."""
    level = _flat_headers(data, offset + 6, offset + 6 + size)
    for expected_tag in _DEFINITION_LIST_WRAPPER_CHAIN:
        container = next((h for h in level if h[0] == expected_tag), None)
        if container is None:
            return None
        _, c_offset, c_size = container
        level = _flat_headers(data, c_offset + 6, c_offset + 6 + c_size)
    defs = [h for h in level if h[0] == _DEFINITION_TAG]
    return defs if defs else None


def iter_top_level_lazy(data, start, end, container_tags=None, *, _geometry=False):
    """Yield ``(index, total, node)`` for each top-level TLV record, fully
    recursed, one at a time - transparently unwrapping a lone 'F401'
    wrapper (matching the shape parse_tlv_recursive's callers already
    expect), and an 'F901' component/group-definition-list wrapper (see
    :func:`_unwrap_definitions_container`) - without ever materializing
    more than one top-level subtree, or one definition's subtree,
    simultaneously.

    The 'F901' unwrap matters because its own subtree is otherwise
    treated as ONE top-level record like any other: without it, a file
    whose definitions are all nested under one 'F901' (rather than
    spread across many top-level records) would need its ENTIRE
    definition list - every vertex/edge/face/loop of every definition -
    built as one monolithic Python object graph before any of it could
    be processed and released, defeating the streaming this function
    exists to provide (openskp#264).

    ``total`` (the top-level sibling count) comes for free from the same
    cheap header scan that drives the loop, so callers can report "N of
    total" progress with no extra pass over the file.

    Each yielded node is independent and safe to discard (drop all
    references) once the caller is done with it, before the next one is
    produced - that's what keeps peak memory bounded by the size of the
    single largest top-level record (or single largest definition, once
    the 'F901' unwrap applies) instead of the whole file.
    """
    if container_tags is None:
        container_tags = CONTAINER_TAGS

    headers = _flat_headers(data, start, end)
    if len(headers) == 1 and headers[0][0] == 'F401':
        _, f401_offset, f401_size = headers[0]
        headers = _flat_headers(data, f401_offset + 6, f401_offset + 6 + f401_size)

    expanded = []
    for tag_hex, offset, size in headers:
        if tag_hex == 'F901':
            def_headers = _unwrap_definitions_container(data, offset, size)
            if def_headers is not None:
                expanded.extend(def_headers)
                continue
        expanded.append((tag_hex, offset, size))
    headers = expanded

    total = len(headers)
    for index, (tag_hex, offset, size) in enumerate(headers):
        record_end = offset + 6 + size
        parser = _parse_tlv_geometry if _geometry else parse_tlv_recursive
        nodes = parser(data, offset, record_end, container_tags)
        if nodes:
            yield index, total, nodes[0]
        # Assignment evaluates the next parser call before replacing `nodes`.
        # Release this tree now, rather than overlapping two large definitions.
        del nodes


# ── 3D planar triangulation ──────────────────────────────────────────────

def _is_convex_2d(coords) -> bool:
    """Whether a simple (non-self-intersecting) polygon's own vertices, in
    order, turn the same direction at every corner - the standard
    cross-product-sign test. `coords` excludes the closing repeat of the
    first point. A degenerate (collinear-but-not-reversing) corner is
    allowed through (its cross product is ~0, doesn't flip the running
    sign) since a triangle fan handles a straight edge correctly either
    way; only an actual direction reversal (concavity) disqualifies the
    fast path below."""
    n = len(coords)
    if n < 3:
        return False
    sign = 0
    for i in range(n):
        ax, ay = coords[i]
        bx, by = coords[(i + 1) % n]
        cx, cy = coords[(i + 2) % n]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if abs(cross) > 1e-9:
            s = 1 if cross > 0 else -1
            if sign == 0:
                sign = s
            elif s != sign:
                return False
    return sign != 0


def _merge_overlapping_hole_loops(loops, v_id_to_2d, vertices_3d, normal, u_axis, v_axis):
    """Detect holes that overlap each other with real shared area (not
    just a shared boundary point/edge, which is a normal, valid pattern)
    and replace them with the boundary of their union.

    A face's holes are supposed to be simple and mutually disjoint -
    that's what earcut's ring-based API assumes. A real file can still
    carry two hole loops that genuinely overlap (observed on a real
    fixture: two ~0.69"-radius circles only 0.33" apart, evidently meant
    as one slotted/oval cutout but recorded as two separate full circles).
    Feeding overlapping rings to earcut is undefined - both this
    project's own triangulator and independently-implemented ports have
    each picked SOME triangle count for such input, but never the same
    one, since there's no single well-defined "correct" triangulation of
    self-overlapping boundary input. Computing the union first restores a
    normal, well-defined hole shape before triangulating, and is a no-op
    (same object identity returned) for the overwhelming common case of
    genuinely disjoint holes, so it costs nothing there.

    Mutates ``vertices_3d`` and ``v_id_to_2d`` IN PLACE to add the new
    (synthetic, intersection-derived) boundary points when a merge
    happens - ``vertices_3d`` in particular is the caller's own
    ``builder.vertices``, which `_add_face_side` reads from directly to
    resolve every triangle's vertex positions when building the actual
    output mesh; a local copy here would silently vanish once this
    function returns, leaving every triangle that touches the merged
    hole boundary with an unresolvable vertex (found and fixed during
    testing: it doesn't raise, `_add_face_side` just silently drops any
    vertex ID not present in ``builder.vertices``, which drops nearly
    every triangle of a hole-heavy face since almost all of them touch
    the hole boundary).

    Returns ``loops`` - the SAME list, unchanged, when no overlap is
    found (or fewer than 2 holes exist), or a new list with the
    overlapping holes replaced by one merged loop when it is.
    """
    if len(loops) < 3:
        return loops

    from shapely.geometry import Polygon as ShapelyPolygon
    from shapely.ops import unary_union

    hole_polys = []
    for loop in loops[1:]:
        coords = [v_id_to_2d[v_id] for v_id in loop]
        try:
            poly = ShapelyPolygon(coords)
            if not poly.is_valid:
                poly = poly.buffer(0)
        except Exception:
            return loops
        hole_polys.append(poly)

    overlap_found = False
    for i in range(len(hole_polys)):
        for j in range(i + 1, len(hole_polys)):
            area_i = hole_polys[i].area
            area_j = hole_polys[j].area
            if area_i <= 0 or area_j <= 0:
                continue
            shared = hole_polys[i].intersection(hole_polys[j]).area
            # a shared boundary edge/point contributes ~0 area; require a
            # real fraction of the smaller hole's own area before treating
            # this as a genuine overlap, not floating-point boundary noise
            if shared > 1e-9 and shared / min(area_i, area_j) > 1e-6:
                overlap_found = True
                break
        if overlap_found:
            break

    if not overlap_found:
        return loops

    merged = unary_union(hole_polys)
    merged_polys = list(merged.geoms) if hasattr(merged, "geoms") else [merged]

    # a 3D point on the face's plane, for reconstructing new (synthetic,
    # intersection-derived) boundary points back from 2D - any real vertex
    # works, since the whole face is coplanar
    origin_3d = np.array(vertices_3d[loops[0][0]])
    plane_offset = np.dot(origin_3d, normal)

    # IDs guaranteed not to collide with anything already in vertices_3d -
    # real TLV entity IDs, and any synthetic ID a PREVIOUS call added for a
    # different face earlier in the same definition's build (vertices_3d
    # is the same dict, shared and growing across every face of one
    # definition, not fresh per call).
    next_synthetic_id = min(vertices_3d.keys(), default=0) - 1
    new_hole_loops = []
    for poly in merged_polys:
        ext = list(poly.exterior.coords)
        if len(ext) > 1 and ext[0] == ext[-1]:
            ext = ext[:-1]
        loop_ids = []
        for u, v in ext:
            p3d = u * u_axis + v * v_axis + plane_offset * normal
            vid = next_synthetic_id
            next_synthetic_id -= 1
            vertices_3d[vid] = tuple(p3d)
            v_id_to_2d[vid] = (u, v)
            loop_ids.append(vid)
        if len(loop_ids) >= 3:
            new_hole_loops.append(loop_ids)

    return [loops[0]] + new_hole_loops


_earcut_warned = False


def _warn_earcut_missing() -> None:
    """Say, once, that concave and holed faces are being fan-triangulated
    because mapbox_earcut isn't importable - a fan is only correct for a
    convex outline, so the mesh path's output quietly degrades otherwise."""
    global _earcut_warned
    if not _earcut_warned:
        _earcut_warned = True
        logger.warning(
            "mapbox_earcut is not installed: concave and holed faces fall back to fan "
            "triangulation, which is only correct for convex outlines. "
            "Install it (pip install mapbox_earcut) for correct meshes."
        )


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

    if len(loops) > 2:
        loops = _merge_overlapping_hole_loops(
            loops, v_id_to_2d, vertices_3d, normal, u_axis, v_axis
        )

    outer_coords = [v_id_to_2d[v_id] for v_id in loops[0]]
    if outer_coords[0] != outer_coords[-1]:
        outer_coords.append(outer_coords[0])

    # A convex outer loop with no holes needs none of Shapely's machinery
    # at all: a triangle fan from its first vertex is a complete, correct
    # triangulation of any convex polygon - not an approximation of the
    # Delaunay result, a DIFFERENT valid triangulation of the exact same
    # boundary (same watertight surface, just a different diagonal choice
    # than Delaunay might pick - both are equally "correct"). Skips the
    # Polygon/MultiPoint construction, the O(triangles) is-inside filter,
    # and the triangulation call itself - the dominant cost of scene
    # building on real files (openskp#264: one production face stalled 16+
    # minutes in this function before the O(V^2)->O(V) fix below even
    # applies, since that fix only speeds up Shapely's OWN triangulation,
    # it doesn't avoid calling it). Falls through to the general Shapely
    # path unchanged for anything concave or with holes - fidelity is
    # identical either way, this only changes how the *simple* majority of
    # real-world faces (extruded profiles, panels, washers, bolt heads)
    # get triangulated, faster.
    if len(loops) == 1 and _is_convex_2d(outer_coords[:-1]):
        loop = loops[0]
        return [[loop[0], loop[i], loop[i + 1]] for i in range(1, len(loop) - 1)]

    try:
        # earcut (ear-clipping) triangulates a polygon - concave, with
        # holes, or both - directly from its own boundary, unlike the
        # Delaunay-then-filter approach this replaced (build a Polygon,
        # triangulate its convex hull, then test every resulting triangle's
        # centroid against the real boundary to throw away the ones that
        # fall in a hole or outside a concave notch). ear-clipping only
        # ever produces triangles that are already part of the polygon's
        # interior, by construction - there is no filter step, and no
        # "which real vertex does this Delaunay-invented coordinate
        # correspond to" reverse lookup either: earcut returns indices
        # directly into the exact point array it was given, so feeding it
        # points in v_id order means its output indices already ARE
        # positions into that same order - a direct list index, not a
        # coordinate match. Verified: same triangulated AREA as the
        # previous Shapely path across every fixture this project has
        # (they're different valid triangulations of the same boundary,
        # not identical diagonal choices - like the convex fast path
        # above, a concave/holed polygon has no single "correct"
        # triangulation, only a correct BOUNDARY, which this preserves).
        flat_v_ids = []
        ring_ends = []
        for loop in loops:
            ring = loop[:-1] if len(loop) >= 2 and loop[0] == loop[-1] else loop
            flat_v_ids.extend(ring)
            ring_ends.append(len(flat_v_ids))

        if len(flat_v_ids) < 3:
            return []

        if mapbox_earcut is None:
            _warn_earcut_missing()
            raise ImportError("mapbox_earcut is not installed")

        flat_coords = np.array([v_id_to_2d[v_id] for v_id in flat_v_ids], dtype=np.float64)
        tri_indices = mapbox_earcut.triangulate_float64(flat_coords, np.array(ring_ends, dtype=np.uint32))

        earcut_triangles = [
            [flat_v_ids[tri_indices[i]], flat_v_ids[tri_indices[i + 1]], flat_v_ids[tri_indices[i + 2]]]
            for i in range(0, len(tri_indices) - 2, 3)
        ]
        if earcut_triangles:
            return earcut_triangles
    except ImportError:
        pass  # mapbox_earcut missing; already warned once - fan fallback below
    except Exception:
        logger.debug("earcut triangulation failed for face, falling back to fan triangulation", exc_info=True)

    # Fan triangulation fallback for outer loop
    outer_loop = loops[0]
    if len(outer_loop) < 3:
        return []
    return [[outer_loop[0], outer_loop[i], outer_loop[i + 1]] for i in range(1, len(outer_loop) - 1)]


# ── Geometry builder ─────────────────────────────────────────────────────

class _GeometryBuilder:
    def __getattribute__(self, name):
        value = object.__getattribute__(self, name)
        if name == '__dict__':
            # Introspection and snapshots retain the original raw dict shape.
            for key in ('vertices', 'edges', 'faces'):
                table = value.get(key)
                if isinstance(table, _PackedTable):
                    value[key] = table.materialize()
        elif isinstance(value, _PackedTable):
            value = value.materialize()
            object.__getattribute__(self, '__dict__')[name] = value
        return value

    def __getstate__(self):
        return self.__dict__

    def _geometry_tables(self):
        """Sequential private readers; public attribute access stays a dict."""
        values = object.__getattribute__(self, '__dict__')
        return values['vertices'], values['edges'], values['faces']

    def _compact_geometry(self):
        """Compact completed large tables before building the public model."""
        values = object.__getattribute__(self, '__dict__')
        for name, storage in (('vertices', PackedVertices), ('edges', PackedEdges),
                              ('faces', PackedFaces)):
            table = values[name]
            if type(table) is dict and len(table) >= 1024:
                values[name] = storage(table)

    def __init__(self):
        self.vertices = {}
        self.edges = {}
        self.edge_flags = {}      # edge id -> display flag byte (D307)
        # edge id -> raw layer id (D007/D207).
        # Same record faces now read; edges carry it too and nothing read it,
        # so a curve-only model - which has NO faces at all, hence no other
        # source of layer information - exported every element untyped.
        # Kept as the raw id so scene.py resolves it through
        # layer_id_to_name, exactly like face['layer'].
        self.edge_layers = {}
        # edge id -> parent Curve's entity id
        # (BB0B), i.e. SketchUp's own ``Edge#curve``.
        #
        # Measured, not guessed. An edge record carries exactly three entity
        # references - B90B, BA0B, BB0B. B90B/BA0B hit the vertex table on
        # 100% of edges across four models (1776/1776, 90649/90649,
        # 34324/34324, 46168/46168) -> Edge#start / Edge#end. BB0B never
        # hits the vertex table nor the edge table on any of them, and its
        # value range starts immediately past the highest edge id
        # (magnetar: edges 23..3574, BB0B 3579..4022) -> the ids SketchUp
        # handed out to the 444 Curve objects right after their edges.
        #
        # Absent means the edge is NOT in a curve (SketchUp's
        # ``edge.curve == nil``): HyparHut has it on 0/90649 edges,
        # SourceCity_Facade on 36/34324 (10 curves). Present means the
        # grouping is authoritative - no chaining heuristic needed.
        self.edge_curves = {}
        # curve id -> the analytic frame of a Curve that is one arc
        # (center, normal, x_axis, y_axis, start_angle, end_angle), the same
        # shape the classic reader keeps. Only curves that are arcs have an
        # entry; a polyline curve has none.
        self.arc_curves = {}
        self.faces = {}
        self.instances = []
        self.section_planes = []
        self.texts = []
        self.dimensions = []
        self.construction_lines = []
        self.construction_points = []


def _find_tag(nodes, target):
    """``find_child_tag`` for ``_TlvNode`` trees: same pre-order search, but
    plain attribute reads instead of a ``__getitem__`` call per access."""
    for n in nodes:
        if n.tag == target:
            return n
        children = n.children
        if children:
            res = _find_tag(children, target)
            if res is not None:
                return res
    return None


def find_child_tag(nodes, target):
    for n in nodes:
        if n.__class__ is _TlvNode:
            return _find_tag(nodes, target)
        break
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
    view = memoryview(model_dat)
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
        # A coincidental marker in geometry can advertise hundreds of MB.
        # Validate candidates through views before copying any payloads.
        body = _tlv_items(view[j + 6:j + 6 + ln])
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
                head = _tlv_items(sub(items, 0x6F54) or b'')
                name = sub(head, 0x6F55)
                if name:
                    page['name'] = name.decode('utf-8', errors='replace')
                cam_wrap = _tlv_items(sub(items, 0x714A) or b'')
                cam = _tlv_items(sub(cam_wrap, 0x34BC) or b'') if cam_wrap else None
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
                # A scene that doesn't save its camera ("Camera Location"
                # unticked) is still a scene: it keeps its name and hidden
                # layers, with eye/target/up left None.
                pages.append(page)
    return pages

def _find_all(nodes, target_tag, results):
    """``find_all_nodes_rec`` for ``_TlvNode`` trees (attribute access)."""
    for n in nodes:
        if n.tag == target_tag:
            results.append(n)
        children = n.children
        if children:
            _find_all(children, target_tag, results)


def find_all_nodes_rec(nodes, target_tag, results):
    for n in nodes:
        if n.__class__ is _TlvNode:
            _find_all(nodes, target_tag, results)
            return
        break
    for n in nodes:
        if n['tag'] == target_tag:
            results.append(n)
        find_all_nodes_rec(n['children'], target_tag, results)

def _entity_id(node):
    """``extract_entity_id`` for ``_TlvNode`` trees (attribute access)."""
    children = node.children
    for child in children:
        tag = child.tag
        if tag == 'DE05':
            payload = child.payload
            return parse_var_int(payload, 0, len(payload))
        if tag == 'DC05':
            payload = child.payload
            if payload.startswith(bytes([0xDE, 0x05])):
                de05_len = read_u32(payload, 2)
                return parse_var_int(payload, 6, de05_len)
    for child in children:
        res = _entity_id(child)
        if res is not None:
            return res
    return None


def extract_entity_id(node):
    if node.__class__ is _TlvNode:
        return _entity_id(node)
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


def _node_from_dict(d):
    """Build a ``_TlvNode`` from a plain ``{'tag', 'children', 'payload', ...}``
    dict - the shape hand-built nodes (tests, tools) use."""
    kids = [_node_from_dict(c) for c in d.get('children') or ()]
    node = _TlvNode(d.get('offset', 0), d['tag'], d.get('size', 0), kids,
                    None, 0, 0)
    if 'payload' in d:
        node._payload = d['payload']
    elif not kids:
        node._payload = b''
    return node


def _extract_geometry_from_nodes(elements, builder):
    """Fill *builder* from a list of TLV nodes. Accepts ``_TlvNode`` trees
    (the parser's own, read by attribute) or equivalent plain dicts."""
    if not isinstance(elements, (list, tuple)):
        elements = list(elements)
    if elements and elements[0].__class__ is not _TlvNode:
        elements = [_node_from_dict(e) for e in elements]
    _extract_geometry_nodes(elements, builder)


# CoEdge sub-record tags inside an A00F payload: A10F = edge id, A20F = orientation.
_COEDGE_EDGE_TAG = bytes([0xA1, 0x0F])
_COEDGE_ORIENT_TAG = bytes([0xA2, 0x0F])


def _direct_child(children, tag):
    """First direct child with ``tag``, or ``None`` (no recursion)."""
    for c in children:
        if c.tag == tag:
            return c
    return None


def _extract_geometry_nodes(elements, builder):
    vertices, edges, faces = builder.vertices, builder.edges, builder.faces
    edge_flags = builder.edge_flags
    for el in elements:
        tag = el.tag

        if isinstance(el, _GeometryRecordNode):
            if tag == 'C409':
                v_id, position, _ = el.record
                vertices[v_id] = position
            elif tag == 'B80B':
                e_id, flags, v1, v2, curve_id = el.record
                edges[e_id] = (v1, v2)
                edge_flags[e_id] = flags
                if curve_id is not None:
                    builder.edge_curves[e_id] = curve_id
            else:
                f_id, normal, loop, hidden, material_id, back_material_id = el.record
                # A subtree can be extracted into more than one builder.
                # Each gets its own mutable face dictionary and loop lists.
                faces[f_id] = {
                    'loops': [list(loop)], 'normal': normal,
                    'material_id': material_id, 'back_material_id': back_material_id,
                    'uv_transform': None, 'uv_transform_back': None,
                    'hidden': hidden, 'layer': None}
            continue

        if tag == 'C409':
            v_id = _entity_id(el)
            c509 = _find_tag(el.children, 'C509')
            if v_id is not None and c509 and len(c509.payload) >= 24:
                x = read_f64(c509.payload, 0)
                y = read_f64(c509.payload, 8)
                z = read_f64(c509.payload, 16)
                vertices[v_id] = (x, y, z)

        elif tag == 'B80B':
            e_id = _entity_id(el)
            if e_id is not None:
                v1_node = _find_tag(el.children, 'B90B')
                v2_node = _find_tag(el.children, 'BA0B')
                v1 = parse_var_int(v1_node.payload, 0, len(v1_node.payload)) if v1_node else None
                v2 = parse_var_int(v2_node.payload, 0, len(v2_node.payload)) if v2_node else None
                edges[e_id] = (v1, v2)
                # D007 -> D307 = edge display flags: base 0x06, plus
                # 0x01 hidden, 0x08|0x10 soft/smooth (cross-validated
                # against the same models in the classic MFC format,
                # 26k edges: 0x06 plain, 0x07 hidden, 0x1E soft+smooth)
                d007 = _direct_child(el.children, 'D007')
                if d007:
                    d307 = _direct_child(d007.children, 'D307')
                    if d307 is not None and d307.payload:
                        edge_flags[e_id] = d307.payload[0]
                    # D207 = layer id - the same
                    # tag the face branch and the instance branch read. See
                    # _GeometryBuilder.edge_layers.
                    d207 = _direct_child(d007.children, 'D207')
                    if d207 and d207.payload:
                        builder.edge_layers[e_id] = parse_var_int(
                            d207.payload, 0, len(d207.payload))
                # BB0B = the parent Curve's entity
                # id (Edge#curve). See _GeometryBuilder.edge_curves.
                bb0b = _find_tag(el.children, 'BB0B')
                if bb0b is not None and bb0b.payload:
                    curve_id = parse_var_int(bb0b.payload, 0, len(bb0b.payload))
                    if curve_id:
                        builder.edge_curves[e_id] = curve_id

        elif tag == 'AC0D':
            f_id = _entity_id(el)
            if f_id is not None:
                normal = (0.0, 0.0, 1.0)
                ad0d = _find_tag(el.children, 'AD0D')
                if ad0d and len(ad0d.payload) >= 24:
                    nx = read_f64(ad0d.payload, 0)
                    ny = read_f64(ad0d.payload, 8)
                    nz = read_f64(ad0d.payload, 16)
                    normal = (nx, ny, nz)

                ae0d = _find_tag(el.children, 'AE0D')
                loops = []
                if ae0d:
                    loop_nodes = []
                    _find_all(ae0d.children, '9411', loop_nodes)
                    for ln in loop_nodes:
                        co_edges = []
                        co_nodes = []
                        _find_all(ln.children, 'A00F', co_nodes)
                        for cn in co_nodes:
                            payload = cn.payload
                            payload_len = len(payload)
                            edge_id = None
                            orient = None
                            sub_pos = 0
                            while sub_pos < payload_len - 6:
                                sub_tag = payload[sub_pos:sub_pos+2]
                                sub_size = read_u32(payload, sub_pos+2)
                                if sub_pos + 6 + sub_size <= payload_len:
                                    if sub_tag == _COEDGE_EDGE_TAG:
                                        edge_id = parse_var_int(payload, sub_pos+6, sub_size)
                                    elif sub_tag == _COEDGE_ORIENT_TAG:
                                        orient = parse_var_int(payload, sub_pos+6, sub_size)
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
                face_layer_id = None
                d007 = _direct_child(el.children, 'D007')
                if d007:
                    d107 = _direct_child(d007.children, 'D107')
                    if d107:
                        face_mat_id = parse_var_int(d107.payload, 0, len(d107.payload))
                    # D207 is the face's LAYER id -
                    # the instance branch below has always read it as such
                    # (see `inst_layer_id`), but the face branch never did,
                    # so every face silently fell back to the layer it
                    # inherited from its placement. That starved
                    # export/ifc.py's classifier of the one signal these
                    # files actually carry: on magnetar_Facade_Drawing.skp the
                    # 444 curtain-wall panel outlines sit on the AIA-coded
                    # layers A-GLAZ-CWMG (mullions) and A-GLAZ-CURT (glass),
                    # and both were invisible downstream. Stored as the raw
                    # id to match the legacy walker's `face['layer']`, which
                    # scene.py resolves through layer_id_to_name.
                    d207 = _direct_child(d007.children, 'D207')
                    if d207 and d207.payload:
                        face_layer_id = parse_var_int(
                            d207.payload, 0, len(d207.payload))
                    dc05 = _direct_child(d007.children, 'DC05')
                    if dc05 is not None:
                        uv_front, uv_back = _extract_uv_transforms(dc05.payload)
                    # D307 = display flags, same record edges already read
                    # (base 0x06, +0x01 hidden) - faces carry the identical
                    # tag under their own D007 container.
                    d307 = _direct_child(d007.children, 'D307')
                    if d307 is not None and d307.payload:
                        face_hidden = bool(d307.payload[0] & 0x01)
                # Back-side material: the AF0D child of the face node (a face
                # painted only on its back — common when the author paints the
                # visible side of a downward-facing cap — carries AF0D but no
                # D107).
                back_mat_id = None
                af0d = _direct_child(el.children, 'AF0D')
                if af0d and af0d.payload:
                    back_mat_id = parse_var_int(af0d.payload, 0, len(af0d.payload))
                faces[f_id] = {'loops': loops, 'normal': normal,
                                       'material_id': face_mat_id,
                                       'back_material_id': back_mat_id,
                                       'uv_transform': uv_front,
                                       'uv_transform_back': uv_back,
                                       'hidden': face_hidden,
                                       # See the
                                       # D207 comment above - matches the
                                       # legacy walker's face['layer'].
                                       'layer': face_layer_id}

        elif tag == '6419':
            nodes_to_search = el.children if el.children else [el]
            guid = None
            def_idx = None
            name = None
            matrix = []
            guid_node = _find_tag(nodes_to_search, '6819')
            if guid_node and len(guid_node.payload) == 16:
                guid = guid_node.payload.hex().upper()
            def_idx_node = _find_tag(nodes_to_search, '6719')
            if def_idx_node:
                def_idx = parse_var_int(def_idx_node.payload, 0, len(def_idx_node.payload))
            name_node = _find_tag(nodes_to_search, '6519')
            if name_node:
                name = name_node.payload.decode('utf-8', errors='replace')
            mat_node = _find_tag(nodes_to_search, '6619')
            if mat_node and len(mat_node.payload) >= 104:
                for idx in range(13):
                    matrix.append(read_f64(mat_node.payload, idx * 8))

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
            inst_attribute_dicts = {}
            d007 = _direct_child(el.children, 'D007')
            if d007:
                d107 = _direct_child(d007.children, 'D107')
                if d107:
                    inst_mat_id = parse_var_int(
                        d107.payload, 0, len(d107.payload))
                d207 = _direct_child(d007.children, 'D207')
                if d207 and d207.payload:
                    inst_layer_id = parse_var_int(
                        d207.payload, 0, len(d207.payload))
                inst_attribute_dicts = extract_attribute_dictionaries(d007)
                # D307 = display flags, same record edges/faces already
                # read (base 0x06, +0x01 hidden).
                d307 = _direct_child(d007.children, 'D307')
                if d307 is not None and d307.payload:
                    inst_hidden = bool(d307.payload[0] & 0x01)

            dynamic = inst_attribute_dicts.get('dynamic_attributes', {})
            _detach_tlv_payloads(el.children)
            builder.instances.append({
                'offset': el.offset,
                'ref_guid': guid,
                'ref_idx': def_idx,
                'name': name,
                'matrix': matrix,
                'material_id': inst_mat_id,
                'hidden': inst_hidden,
                'layer_id': inst_layer_id,
                # Backward-compatible view: stringified dynamic_attributes
                # dict only. attribute_dictionaries carries every
                # dictionary by name, real (non-stringified) types.
                'properties': {k: _stringify_vff_attr_value(v) for k, v in dynamic.items()},
                'attribute_dictionaries': inst_attribute_dicts,
                'children': el.children
            })

        elif tag == '6942':
            # VFF CConstructionLine (list 9113). Same 8-double body as
            # classic CConstructionLine: point + unit direction + start/end
            # params; |param| >= 1e20 is unbounded (SketchUp's own sentinel
            # is ±1e30).
            body = _find_tag(el.children, '6A42')
            if body and len(body.payload) >= 64:
                p = body.payload
                point = (read_f64(p, 0), read_f64(p, 8), read_f64(p, 16))
                direction = (read_f64(p, 24), read_f64(p, 32), read_f64(p, 40))
                start_param = read_f64(p, 48)
                end_param = read_f64(p, 56)
                huge = 1e20
                start = None
                end = None
                if abs(start_param) < huge:
                    start = (
                        point[0] + direction[0] * start_param,
                        point[1] + direction[1] * start_param,
                        point[2] + direction[2] * start_param,
                    )
                if abs(end_param) < huge:
                    end = (
                        point[0] + direction[0] * end_param,
                        point[1] + direction[1] * end_param,
                        point[2] + direction[2] * end_param,
                    )
                builder.construction_lines.append({
                    'point': point,
                    'direction': direction,
                    'start': start,
                    'end': end,
                })

        elif tag == '6C42':
            # VFF CConstructionPoint (list 9213): 6D42 position.
            pos = _find_tag(el.children, '6D42')
            if pos and len(pos.payload) >= 24:
                p = pos.payload
                builder.construction_points.append({
                    'position': (read_f64(p, 0), read_f64(p, 8), read_f64(p, 16)),
                })

        elif tag == '2C4C':
            # A Curve (384A: its entity id and edge count) and, when the curve
            # is an arc, 2D4C: 16 doubles - center, normal, the plane
            # constant, x axis, y axis, radius, start angle, end angle.
            # Checked against SketchUp 2025's own SUArcCurve getters on a
            # loose arc, a circle and an arc, the latter two bounding a face.
            curve = _direct_child(el.children, '384A')
            arc = _direct_child(el.children, '2D4C')
            if curve is not None and arc is not None and len(arc.payload) == 128:
                curve_id = _entity_id(curve)
                if curve_id is not None:
                    v = struct.unpack('<16d', arc.payload)
                    builder.arc_curves[curve_id] = {
                        'center': v[0:3], 'normal': v[3:6],
                        'x_axis': v[7:10], 'y_axis': v[10:13],
                        'start_angle': v[14], 'end_angle': v[15]}

        elif el.children:
            _extract_geometry_nodes(el.children, builder)


# ── Transforms ───────────────────────────────────────────────────────────

def _fold_scale(mat):
    """Return *mat* with its scale term (index 12) folded into the rotation and
    translation, so index 12 is 1.

    SketchUp can keep a uniform scale in the transformation's homogeneous
    term: the 3x3 stays as it is, the translation is in the scaled units, and
    the 16th value ``w`` is the divisor (``Transformation#to_a``'s w = 100
    means 1/100). The 13-value matrix stores ``1/w`` at index 12, so a point
    transforms as ``s * (R p + t)`` with ``s = mat[12]``. Folding gives the
    equivalent ``(s R) p + s t`` with ``s = 1``, which every consumer that only
    reads indices 0-11 handles correctly (openskp#444). A matrix whose scale is
    already 1 - everything SketchUp writes except this case - is returned
    unchanged."""
    if len(mat) < 13 or mat[12] == 1.0:
        return mat
    s = mat[12]
    return [v * s for v in mat[:12]] + [1.0]


def transform_point(p, mat):
    if not mat or len(mat) < 12:
        return p
    mat = _fold_scale(mat)
    x, y, z = p
    tx = mat[0]*x + mat[1]*y + mat[2]*z + mat[9]
    ty = mat[3]*x + mat[4]*y + mat[5]*z + mat[10]
    tz = mat[6]*x + mat[7]*y + mat[8]*z + mat[11]
    return (tx, ty, tz)

def multiply_matrices(parent, child):
    if not parent:
        return _fold_scale(child) if child else child
    if not child:
        return _fold_scale(parent)
    parent = _fold_scale(parent)
    child = _fold_scale(child)
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
    out[12] = 1.0               # both operands were folded (index 12 is 1)
    return out


# ── Dynamic properties ───────────────────────────────────────────────────

# TLV container tags nesting the dynamic property key/value nodes. A438
# wraps one attribute value (its own type tag lives inside it, or no
# children at all for null); AE38 wraps an array's own elements (each
# itself an A438-wrapped value, concatenated with no count prefix - the
# array's own declared byte size is what bounds how many there are). Both
# must be descended into for real content to appear instead of an opaque
# leaf payload - confirmed byte-for-byte against real FrameBuilder-
# authored production files (openskp#254/#255), cross-checked against
# SketchUp's own Ruby API reading the identical file live.
_PROP_CONTAINER_TAGS = ['DD05', 'B536', 'B136', 'B236', 'B336', 'B036', 'A438', 'AE38']

# Attribute value type tags found inside an A438 wrapper - the VFF-format
# counterpart of legacy._read_attr_named's own type table, verified the
# same way (real bytes, not guessed): AD38 string, AF38 Length, A938
# plain double - SketchUp genuinely uses two different tags for the same
# 8-byte float64 encoding depending on whether the value is a Length or a
# plain Float - A738 int32, B438 Point3d, B538 Vector3d (each 3 x f64,
# 24 bytes flat, no per-component tags). No native bool/time_t tag has
# been observed in real data yet -
# FrameBuilder itself stores booleans as the literal strings "true"/
# "false" - so those two of legacy's 9 types are not yet decoded here;
# left safely unrecognized (see _decode_vff_attr_value) rather than
# guessed at.
_VFF_ATTR_DOUBLE_TAGS = ('AF38', 'A938')


def _decode_vff_attr_value(a438_node):
    """Decode one A438-wrapped attribute value node into a native Python
    value. A438 with no children is null; otherwise it has exactly one
    child holding the value's own type tag. An unrecognized type tag (or
    a payload of the wrong size for its tag) is left undecoded (returns
    None) rather than guessed at - matching this project's standing rule
    to never assume an unverified binary layout."""
    children = a438_node['children']
    if not children:
        return None
    child = children[0]
    tag = child['tag']
    payload = child['payload']
    if tag == 'AD38':
        return payload.decode('utf-8', errors='replace')
    if tag in _VFF_ATTR_DOUBLE_TAGS and len(payload) == 8:
        return struct.unpack('<d', payload)[0]
    if tag == 'A738' and len(payload) == 4:
        return struct.unpack('<i', payload)[0]
    if tag in ('B438', 'B538') and len(payload) == 24:
        return struct.unpack('<3d', payload)
    if tag == 'AE38':
        return [_decode_vff_attr_value(elem) for elem in child['children']]
    return None


def extract_attribute_dictionaries(d007):
    """Extract EVERY attribute dictionary attached to a D007 container
    node, keyed by the dictionary's own declared name (tag B436) rather
    than merged into one flat dict (openskp#254), decoding every value
    type the format carries rather than only strings (openskp#255).

    Dynamic properties are stored in a nested TLV hierarchy under the
    DC05 tag. Each dictionary is a B436 (name) node immediately followed
    by a sibling entries container (typically B536) holding that
    dictionary's own B636 (key name) / A438 (type-tagged value) pairs.
    Returns {} when the entity carries no DC05 subtree at all.
    """
    dc05 = next((c for c in d007['children'] if c['tag'] == 'DC05'), None)
    if not dc05:
        return {}
    prop_elements = parse_tlv_recursive(dc05['payload'], 0, len(dc05['payload']), _PROP_CONTAINER_TAGS)

    dictionaries: Dict[str, Dict[str, Any]] = {}

    def extract_entries(nodes, entries):
        current_key = None
        for n in nodes:
            tag = n['tag']
            if tag == 'B636':
                current_key = n['payload'].decode('utf-8', errors='replace')
            elif tag == 'A438' and current_key is not None:
                entries[current_key] = _decode_vff_attr_value(n)
                current_key = None
            else:
                extract_entries(n['children'], entries)

    def walk(nodes):
        for i, n in enumerate(nodes):
            if n['tag'] == 'B436':
                name = n['payload'].decode('utf-8', errors='replace')
                entries: Dict[str, Any] = {}
                if i + 1 < len(nodes):
                    extract_entries(nodes[i + 1]['children'], entries)
                dictionaries[name] = entries
            else:
                walk(n['children'])

    walk(prop_elements)
    return dictionaries


def _stringify_vff_attr_value(value):
    """Render an already-typed VFF attribute value as a string, matching
    extract_dynamic_properties' pre-existing Dict[str, str] contract -
    mirrors legacy._stringify_attr_value exactly."""
    if value is None:
        return ''
    if isinstance(value, (list, tuple)):
        return ','.join(_stringify_vff_attr_value(v) for v in value)
    return str(value)


def extract_dynamic_properties(d007):
    """Extract Dynamic Component attribute key-value pairs from a D007
    container node - a backward-compatible view of just the one
    dictionary SketchUp's own Dynamic Components extension uses
    (``dynamic_attributes``), values stringified. See
    extract_attribute_dictionaries for every dictionary by name, with
    real (non-stringified) types."""
    dynamic = extract_attribute_dictionaries(d007).get('dynamic_attributes', {})
    return {k: _stringify_vff_attr_value(v) for k, v in dynamic.items()}


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

    # ZipFile accounts for a prepended SKP header when locating entries.
    # Reading the file directly avoids retaining a second, file-sized ZIP
    # buffer throughout parsing. Close it on malformed members as well.
    with zipfile.ZipFile(skp_path, 'r') as zf:
        logger.debug("Opened ZIP container: %d entries", len(zf.namelist()))

        # Materials & layer colors
        layer_colors = {}
        # Modern (VFF) files derive layer COLOR from Layer_<name>-prefixed
        # materials, which carry no visibility flag of their own - real
        # visibility comes from the model.dat layer manager's own 8E3C byte,
        # read in collect_layers() below. Seeded to False (visible) here so a
        # layer that never went through collect_layers (e.g. a layer with no
        # painted material at all) still gets a sane default.
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

        # Styles: face colors live in styles/*/style.xml as signed-int32 ABGR
        # variants — item id 2002 is the front (default) face color, 2003 the
        # back face color (the 4000-series items are background/sky/ground).
        # Viewers need them to shade unpainted faces the way SketchUp does (an author may e.g. set a green back color so unpainted
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
                if iid in ('2002', '2003') and var is not None and var.text:
                    try:
                        v = int(var.text) & 0xFFFFFFFF
                    except ValueError:
                        continue
                    colors[iid] = (v & 255, (v >> 8) & 255, (v >> 16) & 255)
            styles.append({'name': style_el.get('name', ''),
                           'front_color': colors.get('2002'),
                           'back_color': colors.get('2003')})

        logger.debug("Parsed %d materials, %d styles", len(materials), len(styles))

        _validate_zip_entry_size(zf, 'model.dat')
        model_dat = zf.read('model.dat')
    logger.debug("Read model.dat: %d bytes", len(model_dat))

    compact_geometry = len(model_dat) >= _COMPACT_MODEL_BYTES

    # 3. Walk the TLV tree one top-level record at a time (instead of
    # building the whole file's tree at once) so peak memory is bounded by
    # the single largest definition/layer-manager/material-manager/root
    # block, not by the file's total node count. Real production files can
    # have 100k+ separate component definitions; materializing all of them
    # simultaneously is what actually exhausts memory on large files - not
    # the (comparatively modest, ~1x) cost of decompressing model.dat
    # itself. See iter_top_level_lazy() for the mechanics.

    layer_id_to_name = {}
    material_id_to_name = {}
    defs_dict = {}
    root_builder = _GeometryBuilder()
    vertex_positions = {}
    instance_world = {}
    page_node = None

    def entity_key(payload):
        # DC05 payload -> persistent id: either a DE05 var-int wrapper or the
        # bare var-int itself.
        if payload.startswith(bytes([0xDE, 0x05])):
            de05_len = read_u32(payload, 2)
            return parse_var_int(payload, 6, de05_len)
        return parse_var_int(payload, 0, len(payload))

    def walk(nodes, parent):
        """One pre-order pass over a top-level record's subtree, doing the
        work that used to take six separate full traversals: layers
        (993A), materials (C832), component definitions (7C15), vertex
        positions (C409), instance world transforms (6419, carrying the
        composed ``parent`` matrix) and the first scenes node (0702).
        Each output is independent of the others, so folding the passes
        together changes no result - only how often the tree is walked."""
        nonlocal page_node
        for el in nodes:
            tag = el.tag
            children = () if isinstance(el, _GeometryRecordNode) else el.children
            here = parent

            if tag == '993A':
                # Layer ID -> name
                for child in children:
                    if child.tag == '8C3C':
                        kids = child.children
                        dc05 = _find_tag(kids, 'DC05')
                        name_node = _find_tag(kids, '8D3C')
                        if dc05 and name_node:
                            l_id = entity_key(dc05.payload)
                            l_name = name_node.payload.decode('utf-8', errors='replace')
                            layer_id_to_name[l_id] = l_name
                            # 8E3C: a single byte, 1 = hidden / 0 = visible -
                            # confirmed byte-for-byte against a real
                            # production file's own Tags panel (openskp
                            # FrameSmart pipeline report, 2026-09-08): every
                            # layer showing a hollow (hidden) eye icon had
                            # 8E3C=01, every visible one had 8E3C=00. This is
                            # the VFF-format counterpart of the legacy
                            # format's already-known layer-hidden flag.
                            hidden_node = _find_tag(kids, '8E3C')
                            if hidden_node and hidden_node.payload:
                                layer_hidden[l_name] = hidden_node.payload[0] == 1

            elif tag == 'C832':
                # Material ID -> name
                dc05 = _find_tag(children, 'DC05')
                name_node = _find_tag(children, 'CC32')
                if dc05 and name_node:
                    m_id = entity_key(dc05.payload)
                    material_id_to_name[m_id] = name_node.payload.decode('utf-8', errors='replace')

            elif tag == '7C15':
                # Component definitions
                guid = None
                name = None
                faces_camera = False
                shadows_face_sun = False
                is_image = False
                is_group = False
                for child in children:
                    ctag = child.tag
                    if ctag == '7D15' and len(child.payload) == 16:
                        guid = child.payload.hex().upper()
                    elif ctag == '7E15':
                        name = child.payload.decode('utf-8', errors='replace')
                    elif ctag == '581B':
                        # Component behavior flags: sub-TLV 5D1B == 1 marks
                        # "always faces camera" (2D people/tree cut-outs);
                        # its companion 5E1B is "shadows face sun".
                        pos = 0
                        pl = child.payload
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
                    elif ctag == '8315' and child.payload:
                        # Definition kind: 0 for a component, 1 for a
                        # group (SketchUp's own API reports the same
                        # split), 2 for the quad definition backing an
                        # Image entity.
                        kind = parse_var_int(child.payload, 0, len(child.payload))
                        is_image = kind == 2
                        is_group = kind == 1
                ent_id = _entity_id(el)
                builder = _GeometryBuilder()
                _extract_geometry_nodes(children, builder)
                if compact_geometry:
                    builder._compact_geometry()
                defs_dict[ent_id] = {'guid': guid, 'name': name,
                                     'always_faces_camera': faces_camera,
                                     'shadows_face_sun': shadows_face_sun,
                                     'is_image': is_image, 'is_group': is_group,
                                     'builder': builder}

            elif tag == 'C409':
                # Vertex persistent id (hex) -> (x, y, z) inches, for
                # dimensions: C409's DC05 holds the id, C509 the 3 x f64
                # position. Ids are unique file-wide, so one flat map
                # resolves a connection point whichever context holds it.
                if isinstance(el, _GeometryRecordNode):
                    _, position, key = el.record
                    vertex_positions[key] = position
                else:
                    dc05 = _find_tag(children, 'DC05')
                    c509 = _find_tag(children, 'C509')
                    if dc05 and c509 and len(c509.payload) == 24:
                        p = dc05.payload
                        if p[:2] == b'\xde\x05':
                            idlen = read_u32(p, 2)
                            idb = p[6:6 + idlen]
                        else:
                            idb = p
                        vertex_positions[idb.hex()] = struct.unpack('<3d', c509.payload)

            elif tag == '6419':
                # Instance persistent id (hex) -> WORLD transform (13-float
                # matrix), composing parent x local at every 6419. A
                # dimension's connection point is definition-local, so
                # _parse_dimensions lifts it to world with this. An instance
                # chain never crosses top-level records.
                d007 = _find_tag(children, 'D007')
                dc05 = _find_tag(d007.children, 'DC05') if d007 else None
                iid = None
                if dc05:
                    p = dc05.payload
                    idb = p[6:6 + read_u32(p, 2)] if p[:2] == b'\xde\x05' else p
                    iid = idb.hex()
                m = _find_tag(children, '6619')
                mat = list(struct.unpack('<13d', m.payload)) \
                    if m and len(m.payload) == 104 else None
                here = multiply_matrices(parent, mat) if mat else parent
                if iid:
                    instance_world[iid] = here

            elif tag == '0702':
                if page_node is None:
                    page_node = el

            if children:
                walk(children, here)

    for index, total, el in iter_top_level_lazy(
            model_dat, 0, len(model_dat), CONTAINER_TAGS, _geometry=True):
        try:
            walk([el], None)
            if el.tag == 'F601':
                _extract_geometry_nodes(el.children, root_builder)
        except Exception as e:
            raise SkpParseError(
                f"Failed while processing top-level record: {e}",
                stage="tlv_walk", record_index=index, total_records=total,
                tag=el['tag'],
            ) from e
        if index % _PROGRESS_INTERVAL == 0 or index == total - 1:
            logger.debug("Processed %d/%d top-level records", index + 1, total)
        # Drop the consumer's reference as well as the generator's. Otherwise
        # the for-loop retains this tree until the next one has been decoded.
        del el

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

    if compact_geometry:
        root_builder._compact_geometry()
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

