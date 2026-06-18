"""Material and layer parsing from SketchUp data.

SketchUp stores material definitions in XML files embedded inside the
ZIP container, while layer (tag) information is encoded inline in the
TLV stream.  This module handles both sources.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

from .model import Layer, Material, TlvNode
from .parser import read_u32


# ── Tag constants ─────────────────────────────────────────────────────────

_TAG_LAYER_CONTAINER: str = "FF0A"
_TAG_LAYER_NAME: str = "0200"
_TAG_LAYER_COLOR: str = "0700"


# ── Material XML parsing ─────────────────────────────────────────────────


def _parse_color_string(color_str: str) -> Tuple[int, int, int, int]:
    """Parse a colour string (hex or named) into RGBA components.

    Supports formats like ``"#RRGGBB"``, ``"#RRGGBBAA"``, or
    comma-separated ``"r,g,b"`` / ``"r,g,b,a"`` strings.

    Args:
        color_str: Raw colour string from XML.

    Returns:
        ``(r, g, b, a)`` with each channel in 0–255.
    """
    color_str = color_str.strip()

    if color_str.startswith("#"):
        hex_val = color_str.lstrip("#")
        if len(hex_val) == 6:
            r = int(hex_val[0:2], 16)
            g = int(hex_val[2:4], 16)
            b = int(hex_val[4:6], 16)
            return (r, g, b, 255)
        elif len(hex_val) == 8:
            r = int(hex_val[0:2], 16)
            g = int(hex_val[2:4], 16)
            b = int(hex_val[4:6], 16)
            a = int(hex_val[6:8], 16)
            return (r, g, b, a)

    if "," in color_str:
        parts = [int(p.strip()) for p in color_str.split(",")]
        if len(parts) == 3:
            return (parts[0], parts[1], parts[2], 255)
        elif len(parts) >= 4:
            return (parts[0], parts[1], parts[2], parts[3])

    return (200, 200, 200, 255)


def _parse_material_xml(xml_bytes: bytes) -> Optional[Material]:
    """Parse a single material XML file into a :class:`Material`.

    Args:
        xml_bytes: Raw XML content.

    Returns:
        A :class:`Material`, or ``None`` if parsing fails.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None

    name: str = root.get("name", root.get("Name", "Unnamed"))

    color: Tuple[int, int, int, int] = (200, 200, 200, 255)
    transparency: float = 1.0

    # Try to find a <color> or <Color> element
    for tag_name in ("color", "Color", "colour", "Colour"):
        color_el = root.find(tag_name)
        if color_el is not None:
            text = color_el.text or color_el.get("value", "")
            if text:
                color = _parse_color_string(text)
            else:
                # Colour stored as individual attributes
                r = int(color_el.get("red", color_el.get("r", "200")))
                g = int(color_el.get("green", color_el.get("g", "200")))
                b = int(color_el.get("blue", color_el.get("b", "200")))
                a = int(color_el.get("alpha", color_el.get("a", "255")))
                color = (r, g, b, a)
            break

    # Transparency / opacity
    for tag_name in ("alpha", "Alpha", "transparency", "Transparency", "opacity"):
        alpha_el = root.find(tag_name)
        if alpha_el is not None:
            try:
                val = float(alpha_el.text or alpha_el.get("value", "1.0"))
                # Normalise: values > 1 are assumed 0–255 scale
                transparency = val if val <= 1.0 else val / 255.0
            except ValueError:
                pass
            break

    return Material(name=name, color=color, transparency=transparency)


def parse_materials(material_files: Dict[str, bytes]) -> List[Material]:
    """Parse all material XML files from the ZIP container.

    Non-XML entries (textures, images) are silently skipped.

    Args:
        material_files: Mapping of filename → raw bytes extracted from the
            embedded ZIP.

    Returns:
        List of :class:`Material` objects successfully parsed.
    """
    materials: List[Material] = []

    for filename, content in material_files.items():
        if not filename.lower().endswith(".xml"):
            continue
        mat = _parse_material_xml(content)
        if mat is not None:
            materials.append(mat)

    return materials


# ── Layer parsing from TLV nodes ─────────────────────────────────────────


def _extract_layer_from_children(children: List[TlvNode]) -> Optional[Layer]:
    """Build a :class:`Layer` from the children of a layer container node.

    Args:
        children: Child TLV nodes of a layer container.

    Returns:
        A :class:`Layer`, or ``None`` if no name is found.
    """
    name: Optional[str] = None
    r, g, b = 200, 200, 200

    for child in children:
        if child.tag == _TAG_LAYER_NAME and child.payload:
            try:
                name = child.payload.decode("utf-8", errors="replace").strip("\x00")
            except Exception:
                pass

        elif child.tag == _TAG_LAYER_COLOR and len(child.payload) >= 3:
            r = child.payload[0]
            g = child.payload[1]
            b = child.payload[2]

    if name is None:
        return None
    return Layer(name=name, color_r=r, color_g=g, color_b=b)


def parse_layers(nodes: List[TlvNode]) -> List[Layer]:
    """Recursively walk TLV nodes and extract all layers.

    Args:
        nodes: Top-level TLV nodes from the parsed ``model.dat``.

    Returns:
        List of :class:`Layer` objects found in the tree.
    """
    layers: List[Layer] = []

    for node in nodes:
        if node.tag == _TAG_LAYER_CONTAINER and node.children:
            layer = _extract_layer_from_children(node.children)
            if layer is not None:
                layers.append(layer)

        if node.children:
            layers.extend(parse_layers(node.children))

    return layers
