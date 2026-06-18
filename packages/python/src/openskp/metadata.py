"""Metadata extraction — dynamic properties, layer IDs, and scene hierarchy.

This module is responsible for the higher-level semantic pass over the TLV
node tree: building the scene-graph hierarchy of :class:`~openskp.model.Instance`
objects, resolving layer references, and extracting user-defined dynamic
attributes.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .model import Definition, Instance, TlvNode
from .parser import read_u32


# ── Tag constants ─────────────────────────────────────────────────────────

_TAG_INSTANCE: str = "FF05"
_TAG_GUID: str = "0100"
_TAG_NAME: str = "0200"
_TAG_MATRIX: str = "0800"
_TAG_LAYER_REF: str = "0900"
_TAG_DYN_PROP: str = "FF0E"
_TAG_DYN_KEY: str = "0200"
_TAG_DYN_VAL: str = "0300"
_TAG_DEF_REF: str = "0A00"

from .parser import read_f64


# ── Dynamic properties ───────────────────────────────────────────────────


def extract_dynamic_properties(
    node: TlvNode,
) -> Dict[str, str]:
    """Extract key/value dynamic attribute pairs from a node.

    Dynamic attributes are stored as child nodes of a ``FF0E`` container,
    with tag ``0200`` for the key and ``0300`` for the value.

    Args:
        node: A TLV node (typically an instance or definition container).

    Returns:
        A dict of property names to their string values.
    """
    props: Dict[str, str] = {}

    for child in node.children:
        if child.tag == _TAG_DYN_PROP and child.children:
            key: Optional[str] = None
            val: Optional[str] = None
            for sub in child.children:
                if sub.tag == _TAG_DYN_KEY and sub.payload:
                    key = sub.payload.decode("utf-8", errors="replace").strip("\x00")
                elif sub.tag == _TAG_DYN_VAL and sub.payload:
                    val = sub.payload.decode("utf-8", errors="replace").strip("\x00")
            if key is not None:
                props[key] = val or ""

    return props


# ── Matrix extraction ────────────────────────────────────────────────────


def _extract_matrix(node: TlvNode) -> List[float]:
    """Extract a 4×4 column-major transform matrix from a matrix tag.

    The payload is expected to contain 12 ``float64`` values (the
    rotation/scale + translation components).  The fourth row is implied
    as ``[0, 0, 0, 1]``.

    Args:
        node: TLV node with tag ``0800``.

    Returns:
        A 16-element column-major list.  Returns identity if the payload
        is too short.
    """
    identity: List[float] = [
        1, 0, 0, 0,
        0, 1, 0, 0,
        0, 0, 1, 0,
        0, 0, 0, 1,
    ]

    payload = node.payload
    if len(payload) < 96:  # 12 * 8 bytes
        return identity

    values: List[float] = []
    for i in range(12):
        values.append(read_f64(payload, i * 8))

    # Column-major: columns 0-2 are rotation/scale, column 3 is translation
    # values layout: [m00 m10 m20  m01 m11 m21  m02 m12 m22  tx ty tz]
    return [
        values[0], values[1], values[2], 0.0,
        values[3], values[4], values[5], 0.0,
        values[6], values[7], values[8], 0.0,
        values[9], values[10], values[11], 1.0,
    ]


# ── Instance extraction ─────────────────────────────────────────────────


def _build_instance(
    node: TlvNode,
    definitions: Dict[int, Definition],
) -> Instance:
    """Parse a single instance from a TLV node subtree.

    Args:
        node: TLV node with tag ``FF05``.
        definitions: Available definitions for cross-referencing.

    Returns:
        A populated :class:`Instance`.
    """
    inst = Instance()

    for child in node.children:
        if child.tag == _TAG_GUID and child.payload:
            try:
                inst.guid = child.payload.decode("utf-8", errors="replace").strip("\x00")
            except Exception:
                pass

        elif child.tag == _TAG_NAME and child.payload:
            try:
                inst.name = child.payload.decode("utf-8", errors="replace").strip("\x00")
            except Exception:
                pass

        elif child.tag == _TAG_MATRIX:
            inst.matrix = _extract_matrix(child)

        elif child.tag == _TAG_LAYER_REF and child.payload:
            try:
                inst.layer = child.payload.decode("utf-8", errors="replace").strip("\x00")
            except Exception:
                pass

        elif child.tag == _TAG_DEF_REF and len(child.payload) >= 4:
            inst.ref_idx = read_u32(child.payload, 0)

        elif child.tag == _TAG_INSTANCE:
            # Nested instance
            child_inst = _build_instance(child, definitions)
            inst.children.append(child_inst)

    # Extract dynamic properties from the node itself
    inst.properties = extract_dynamic_properties(node)

    return inst


# ── Layer resolution ──────────────────────────────────────────────────────


def resolve_layer_id(
    node: TlvNode,
    layer_names: Dict[int, str],
) -> str:
    """Resolve a layer-reference payload to a human-readable name.

    Args:
        node: TLV node carrying a layer reference in its payload.
        layer_names: Mapping of layer index → name.

    Returns:
        Layer name, or ``"Layer0"`` as default.
    """
    if not node.payload or len(node.payload) < 4:
        return "Layer0"

    layer_idx = read_u32(node.payload, 0)
    return layer_names.get(layer_idx, "Layer0")


# ── Public API ────────────────────────────────────────────────────────────


def extract_metadata(
    nodes: List[TlvNode],
    definitions: Dict[int, Definition],
) -> List[Instance]:
    """Walk the TLV tree and build the scene hierarchy of instances.

    This is the semantic pass that connects component instances to their
    definitions, resolves layer references, and collects dynamic
    properties.

    Args:
        nodes: Top-level TLV nodes from the parsed ``model.dat``.
        definitions: Already-extracted geometry definitions.

    Returns:
        A list of top-level :class:`Instance` objects forming the
        scene graph.
    """
    hierarchy: List[Instance] = []

    for node in nodes:
        if node.tag == _TAG_INSTANCE and node.children:
            inst = _build_instance(node, definitions)
            hierarchy.append(inst)
        elif node.children:
            # Recurse into non-instance containers
            hierarchy.extend(extract_metadata(node.children, definitions))

    return hierarchy
