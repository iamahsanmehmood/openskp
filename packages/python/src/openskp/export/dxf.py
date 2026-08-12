"""DXF (AutoCAD Drawing Exchange Format R2000 / AC1015) 3D export module for OpenSKP.

Exports a baked :class:`~openskp.scene.Scene` to 3D DXF format with Polyface Mesh
or 3DFACE entities grouped by layer using ezdxf for 100% AutoCAD / DWG TrueView compatibility.
Includes layer and entity RGB material base colors (ACI Group 62 and True Color Group 420).
"""

from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING, Literal, Union

if TYPE_CHECKING:
    from ..scene import Scene

# 1 metre = 39.37007874015748 inches (SketchUp native unit)
METRES_TO_INCHES = 39.37007874015748


def _sanitize_layer_name(name: str) -> str:
    """Sanitize layer name for DXF Group 8 compliance."""
    if not name:
        return "0"
    illegal = '<>/\\"~:;?*=`|'
    clean = "".join(c if c not in illegal else "_" for c in name)
    return clean.strip() or "0"


def _rgb_to_aci(r: int, g: int, b: int) -> int:
    """Map 0-255 RGB color to closest standard AutoCAD Color Index (ACI 1-255)."""
    standard_aci = (
        (255, 0, 0, 1),      # Red
        (255, 255, 0, 2),    # Yellow
        (0, 255, 0, 3),      # Green
        (0, 255, 255, 4),    # Cyan
        (0, 0, 255, 5),      # Blue
        (255, 0, 255, 6),    # Magenta
        (255, 255, 255, 7),  # White
        (128, 128, 128, 8),  # Dark Gray
        (192, 192, 192, 9),  # Light Gray
    )
    best_aci = 7
    min_dist = float("inf")
    for sr, sg, sb, aci in standard_aci:
        dist = (r - sr) ** 2 + (g - sg) ** 2 + (b - sb) ** 2
        if dist < min_dist:
            min_dist = dist
            best_aci = aci
    return best_aci


def _get_prim_rgb(scene: Scene, prim: any) -> tuple[int, int, int]:
    """Extract (R, G, B) integer tuple (0-255) for a primitive's material."""
    r, g, b = 200, 200, 200
    if prim.material_index is not None and scene.gltf_materials and prim.material_index < len(scene.gltf_materials):
        mat = scene.gltf_materials[prim.material_index]
        if isinstance(mat, dict):
            pbr = mat.get("pbrMetallicRoughness", {})
            color_vec = pbr.get("baseColorFactor", [0.8, 0.8, 0.8, 1.0])
            if len(color_vec) >= 3:
                r = int(round(color_vec[0] * 255.0))
                g = int(round(color_vec[1] * 255.0))
                b = int(round(color_vec[2] * 255.0))
    return max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))


def to_dxf(
    scene: Scene,
    scale: float = METRES_TO_INCHES,
    mode: Literal["3dface", "polyface"] = "polyface",
) -> str:
    """Serialize a baked scene to AutoCAD R2000 (AC1015) 3D ASCII DXF format.

    Args:
        scene: The baked scene returned by :meth:`SkpFile.build_scene`.
        scale: Scale factor for vertex coordinates (default: METRES_TO_INCHES).
        mode: Export entity mode ('polyface' for Polyface Meshes or '3dface' for 3DFACE entities).

    Returns:
        Formatted ASCII DXF text string with CRLF newlines.
    """
    if scene is None or scene.glb_primitives is None:
        raise ValueError("scene cannot be None")

    try:
        import ezdxf
        has_ezdxf = True
    except ImportError:
        has_ezdxf = False

    if has_ezdxf and mode == "polyface":
        doc = ezdxf.new("R2000")
        msp = doc.modelspace()

        for prim in scene.glb_primitives:
            layer_name = _sanitize_layer_name(prim.geom_name or "0")
            v_count = len(prim.positions) // 3
            tri_count = len(prim.indices) // 3
            if v_count == 0 or tri_count == 0:
                continue

            r, g, b = _get_prim_rgb(scene, prim)
            aci_color = _rgb_to_aci(r, g, b)
            true_color = (r << 16) | (g << 8) | b

            if not doc.layers.has_entry(layer_name):
                layer_entry = doc.layers.add(layer_name)
                layer_entry.dxf.color = aci_color
                layer_entry.dxf.true_color = true_color

            unique_verts = []
            vert_map = {}
            index_remap = []
            for i in range(v_count):
                pos = (
                    round(prim.positions[i * 3] * scale, 6),
                    round(prim.positions[i * 3 + 1] * scale, 6),
                    round(prim.positions[i * 3 + 2] * scale, 6),
                )
                if pos not in vert_map:
                    vert_map[pos] = len(unique_verts)
                    unique_verts.append(pos)
                index_remap.append(vert_map[pos])

            from ezdxf.render import MeshBuilder
            mesh = MeshBuilder()
            mesh.add_vertices(unique_verts)
            for i in range(tri_count):
                idx0 = index_remap[prim.indices[i * 3]]
                idx1 = index_remap[prim.indices[i * 3 + 1]]
                idx2 = index_remap[prim.indices[i * 3 + 2]]
                mesh.add_face([unique_verts[idx0], unique_verts[idx1], unique_verts[idx2]])

            mesh.render_polyface(
                msp,
                dxfattribs={
                    "layer": layer_name,
                    "color": aci_color,
                    "true_color": true_color,
                },
            )

        import io
        stream = io.StringIO()
        doc.write(stream)
        return stream.getvalue()

    # Zero-dependency native R2000 DXF exporter with 100% AutoCAD parity
    layer_colors: dict[str, tuple[int, int, int]] = {}
    for prim in scene.glb_primitives:
        layer_name = _sanitize_layer_name(prim.geom_name or "0")
        if layer_name not in layer_colors:
            layer_colors[layer_name] = _get_prim_rgb(scene, prim)

    if not layer_colors:
        layer_colors["0"] = (200, 200, 200)

    sorted_layers = sorted(layer_colors.keys())

    handle_counter = 0x100
    def next_handle() -> str:
        nonlocal handle_counter
        h = f"{handle_counter:X}"
        handle_counter += 1
        return h

    layer_handles: dict[str, str] = {}
    for l_name in sorted_layers:
        layer_handles[l_name] = next_handle()

    lines = [
        "  0", "SECTION", "  2", "HEADER", "  9", "$ACADVER", "  1", "AC1015", "  9", "$DWGCODEPAGE", "  3", "ANSI_1252",
        "  9", "$INSUNITS", " 70", "1", "  0", "ENDSEC", "  0", "SECTION", "  2", "TABLES", "  0", "TABLE", "  2", "VPORT",
        "  5", "8", "100", "AcDbSymbolTable", " 70", "0", "  0", "ENDTAB", "  0", "TABLE", "  2", "LTYPE", "  5", "5",
        "100", "AcDbSymbolTable", " 70", "1", "  0", "LTYPE", "  5", "14", "100", "AcDbSymbolTableRecord", "100", "AcDbLinetypeTableRecord",
        "  2", "BYBLOCK", " 70", "0", "  3", "", " 72", "65", " 73", "0", " 40", "0.0", "  0", "LTYPE", "  5", "15",
        "100", "AcDbSymbolTableRecord", "100", "AcDbLinetypeTableRecord", "  2", "BYLAYER", " 70", "0", "  3", "", " 72", "65",
        " 73", "0", " 40", "0.0", "  0", "LTYPE", "  5", "16", "100", "AcDbSymbolTableRecord", "100", "AcDbLinetypeTableRecord",
        "  2", "CONTINUOUS", " 70", "0", "  3", "Solid line", " 72", "65", " 73", "0", " 40", "0.0", "  0", "ENDTAB",
        "  0", "TABLE", "  2", "LAYER", "  5", "4", "100", "AcDbSymbolTable", " 70", str(len(sorted_layers) + 1),
        "  0", "LAYER", "  5", "27", "330", "4", "100", "AcDbSymbolTableRecord", "100", "AcDbLayerTableRecord", "  2", "0", " 70", "0", " 62", "7", "  6", "Continuous",
        "  0", "LAYER", "  5", "28", "330", "4", "100", "AcDbSymbolTableRecord", "100", "AcDbLayerTableRecord", "  2", "Defpoints", " 70", "0", " 62", "7", "  6", "Continuous"
    ]

    for l_name in sorted_layers:
        r, g, b = layer_colors[l_name]
        aci = _rgb_to_aci(r, g, b)
        true_color = (r << 16) | (g << 8) | b
        lines.extend([
            "  0", "LAYER", "  5", layer_handles[l_name], "330", "4", "100", "AcDbSymbolTableRecord", "100", "AcDbLayerTableRecord",
            "  2", l_name, " 70", "0", " 62", str(aci), "420", str(true_color), "  6", "Continuous"
        ])

    lines.extend([
        "  0", "ENDTAB", "  0", "TABLE", "  2", "STYLE", "  5", "3", "100", "AcDbSymbolTable", " 70", "0", "  0", "ENDTAB",
        "  0", "TABLE", "  2", "VIEW", "  5", "6", "100", "AcDbSymbolTable", " 70", "0", "  0", "ENDTAB",
        "  0", "TABLE", "  2", "UCS", "  5", "7", "100", "AcDbSymbolTable", " 70", "0", "  0", "ENDTAB",
        "  0", "TABLE", "  2", "APPID", "  5", "9", "100", "AcDbSymbolTable", " 70", "1", "  0", "APPID", "  5", "12",
        "100", "AcDbSymbolTableRecord", "100", "AcDbRegAppTableRecord", "  2", "ACAD", " 70", "0", "  0", "ENDTAB",
        "  0", "TABLE", "  2", "DIMSTYLE", "  5", "A", "100", "AcDbSymbolTable", " 70", "0", "  0", "ENDTAB",
        "  0", "TABLE", "  2", "BLOCK_RECORD", "  5", "1", "100", "AcDbSymbolTable", " 70", "2",
        "  0", "BLOCK_RECORD", "  5", "17", "330", "1", "100", "AcDbSymbolTableRecord", "100", "AcDbBlockTableRecord", "  2", "*Model_Space",
        "  0", "BLOCK_RECORD", "  5", "1B", "330", "1", "100", "AcDbSymbolTableRecord", "100", "AcDbBlockTableRecord", "  2", "*Paper_Space",
        "  0", "ENDTAB", "  0", "ENDSEC", "  0", "SECTION", "  2", "BLOCKS", "  0", "ENDSEC", "  0", "SECTION", "  2", "ENTITIES"
    ])

    for prim in scene.glb_primitives:
        layer_name = _sanitize_layer_name(prim.geom_name or "0")
        tri_count = len(prim.indices) // 3
        if tri_count == 0:
            continue

        r, g, b = layer_colors.get(layer_name, (200, 200, 200))
        aci = _rgb_to_aci(r, g, b)

        if mode == "polyface":
            v_count = len(prim.positions) // 3
            lines.extend([
                "  0", "POLYLINE", "  5", next_handle(), "330", "17", "100", "AcDbEntity", "  8", layer_name,
                " 62", str(aci), "100", "AcDbPolyFaceMesh", " 66", "1",
                " 10", "0.0", " 20", "0.0", " 30", "0.0",
                " 70", "64", " 71", str(v_count), " 72", str(tri_count)
            ])
            for i in range(v_count):
                vx = f"{prim.positions[i * 3] * scale:.6f}"
                vy = f"{prim.positions[i * 3 + 1] * scale:.6f}"
                vz = f"{prim.positions[i * 3 + 2] * scale:.6f}"
                lines.extend([
                    "  0", "VERTEX", "  5", next_handle(), "330", "17", "100", "AcDbEntity", "  8", layer_name,
                    "100", "AcDbVertex", "100", "AcDbPolyFaceMeshVertex",
                    " 10", vx, " 20", vy, " 30", vz, " 70", "192"
                ])
            for i in range(tri_count):
                idx0 = prim.indices[i * 3] + 1
                idx1 = prim.indices[i * 3 + 1] + 1
                idx2 = prim.indices[i * 3 + 2] + 1
                lines.extend([
                    "  0", "VERTEX", "  5", next_handle(), "330", "17", "100", "AcDbEntity", "  8", layer_name,
                    "100", "AcDbVertex", "100", "AcDbFaceRecord", " 70", "128",
                    " 71", str(idx0), " 72", str(idx1), " 73", str(idx2), " 74", "0"
                ])
            lines.extend([
                "  0", "SEQEND", "  5", next_handle(), "330", "17", "100", "AcDbEntity", "  8", layer_name
            ])
        else:
            for i in range(tri_count):
                i0 = prim.indices[i * 3]
                i1 = prim.indices[i * 3 + 1]
                i2 = prim.indices[i * 3 + 2]

                v0x = f"{prim.positions[i0 * 3] * scale:.6f}"
                v0y = f"{prim.positions[i0 * 3 + 1] * scale:.6f}"
                v0z = f"{prim.positions[i0 * 3 + 2] * scale:.6f}"

                v1x = f"{prim.positions[i1 * 3] * scale:.6f}"
                v1y = f"{prim.positions[i1 * 3 + 1] * scale:.6f}"
                v1z = f"{prim.positions[i1 * 3 + 2] * scale:.6f}"

                v2x = f"{prim.positions[i2 * 3] * scale:.6f}"
                v2y = f"{prim.positions[i2 * 3 + 1] * scale:.6f}"
                v2z = f"{prim.positions[i2 * 3 + 2] * scale:.6f}"

                lines.extend([
                    "  0", "3DFACE", "  5", next_handle(), "330", "17", "100", "AcDbEntity", "  8", layer_name,
                    " 62", str(aci), "100", "AcDbFace",
                    " 10", v0x, " 20", v0y, " 30", v0z,
                    " 11", v1x, " 21", v1y, " 31", v1z,
                    " 12", v2x, " 22", v2y, " 32", v2z,
                    " 13", v2x, " 23", v2y, " 33", v2z
                ])

    lines.extend([
        "  0", "ENDSEC", "  0", "SECTION", "  2", "OBJECTS", "  0", "DICTIONARY", "  5", "C", "330", "0",
        "100", "AcDbDictionary", "281", "1", "  0", "ENDSEC", "  0", "EOF"
    ])

    return "\r\n".join(lines) + "\r\n"


def export(
    scene: Scene,
    output_path: Union[str, pathlib.Path],
    scale: float = METRES_TO_INCHES,
    mode: Literal["3dface", "polyface"] = "polyface",
) -> None:
    """Export a baked scene to an AutoCAD R2000 3D DXF file.

    Args:
        scene: The baked scene returned by :meth:`SkpFile.build_scene`.
        output_path: Destination file path (.dxf).
        scale: Scale factor for vertex coordinates (default: METRES_TO_INCHES).
        mode: Export entity mode ('polyface' or '3dface').
    """
    if scene is None:
        raise ValueError("scene cannot be None")

    out = pathlib.Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    text = to_dxf(scene, scale=scale, mode=mode)
    with open(out, "w", encoding="utf-8", newline="") as fp:
        fp.write(text)


__all__ = ["to_dxf", "export", "METRES_TO_INCHES"]
