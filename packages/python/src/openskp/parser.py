"""TLV binary parser for SketchUp's internal data format.

This module provides low-level functions for reading the Tag-Length-Value (TLV)
encoded binary data found inside SketchUp files. The parser recursively
descends into container tags to build a tree of ``TlvNode`` objects.

Typical usage::

    from openskp.parser import parse_tlv_recursive
    from openskp.model import TlvNode

    nodes = parse_tlv_recursive(raw_bytes, 0, len(raw_bytes))
"""

from __future__ import annotations

import struct
from typing import List, Optional, Set

from .model import TlvNode

# ---------------------------------------------------------------------------
# Known container tags that may hold nested TLV children.
# This set is used by ``parse_tlv_recursive`` to decide whether to descend
# into a tag's payload.
# ---------------------------------------------------------------------------
CONTAINER_TAGS: Set[str] = {
    '7C15', '8813', '8913', '8A13', '8B13', '8D13', '4C1D', '6419',
    'F901', '7017', '7117', 'D007', 'C409', '9411', '9511', '0F01',
    '384A', 'B80B', '9713', '2C4C', 'AC0D', 'AE0D', 'F601', 'F801',
    '983A', '993A', '8C3C', '8D3C',
}


# ── Primitive readers ──────────────────────────────────────────────────────


def read_u32(data: bytes, offset: int) -> int:
    """Read an unsigned 32-bit little-endian integer.

    Args:
        data: Raw byte buffer.
        offset: Byte offset into *data*.

    Returns:
        The decoded ``uint32`` value.

    Raises:
        struct.error: If there are fewer than 4 bytes at *offset*.
    """
    return struct.unpack_from('<I', data, offset)[0]


def read_f64(data: bytes, offset: int) -> float:
    """Read a 64-bit little-endian IEEE-754 double.

    Args:
        data: Raw byte buffer.
        offset: Byte offset into *data*.

    Returns:
        The decoded ``float64`` value.

    Raises:
        struct.error: If there are fewer than 8 bytes at *offset*.
    """
    return struct.unpack_from('<d', data, offset)[0]


def parse_var_int(data: bytes, offset: int, length: int) -> int:
    """Decode a variable-length little-endian unsigned integer.

    Args:
        data: Raw byte buffer.
        offset: Byte offset into *data*.
        length: Number of bytes to consume (1–8).

    Returns:
        The decoded integer value.
    """
    val: int = 0
    for i in range(length):
        val |= data[offset + i] << (8 * i)
    return val


# ── Recursive TLV parser ──────────────────────────────────────────────────


def parse_tlv_recursive(
    data: bytes,
    start: int,
    end: int,
    container_tags: Optional[Set[str]] = None,
    depth: int = 0,
) -> List[TlvNode]:
    """Recursively parse a TLV-encoded byte range into a tree of nodes.

    Each TLV element consists of:

    * **Tag** — 2 bytes (interpreted as upper-case hex, e.g. ``"FF01"``).
    * **Length** — 4-byte ``uint32`` little-endian payload length.
    * **Value** — *length* bytes of payload data.

    If a tag is listed in *container_tags* the parser descends into its
    payload to extract child nodes.

    Args:
        data: Full binary buffer.
        start: Byte offset where parsing begins (inclusive).
        end: Byte offset where parsing stops (exclusive).
        container_tags: Set of hex tag strings that may contain nested TLV
            data.  Defaults to :data:`CONTAINER_TAGS`.
        depth: Current recursion depth (used internally).

    Returns:
        A list of :class:`TlvNode` objects found in the range
        ``[start, end)``.
    """
    if container_tags is None:
        container_tags = CONTAINER_TAGS

    pos: int = start
    elements: List[TlvNode] = []

    while pos < end - 6:
        tag_bytes = data[pos:pos + 2]
        size = read_u32(data, pos + 2)

        if pos + 6 + size > end:
            break

        tag_hex: str = tag_bytes.hex().upper()
        children: List[TlvNode] = []
        is_container: bool = tag_hex in container_tags

        if is_container and size > 0:
            children = parse_tlv_recursive(
                data, pos + 6, pos + 6 + size, container_tags, depth + 1
            )

        payload: bytes = data[pos + 6: pos + 6 + size] if not children else b''

        elements.append(TlvNode(
            offset=pos,
            tag=tag_hex,
            size=size,
            children=children,
            payload=payload,
        ))

        pos += 6 + size

    return elements
