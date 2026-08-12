"""IFC4 (BIM) 3D exporter for OpenSKP scenes.

Serializes a baked :class:`~openskp.scene.Scene` into ISO-10303-21 STEP ASCII format
conforming to the IFC4 schema (FILE_SCHEMA(('IFC4'))).

Uses native ``IfcTriangulatedFaceSet`` geometry representation for direct 1:1
mapping of triangulated vertex positions and face indices from baked scene primitives.
"""

from __future__ import annotations

import datetime
import math
import pathlib
import uuid
from typing import Any, Dict, List, Optional, Tuple, Union

from ..scene import INCHES_TO_MM, Scene

# 1 metre = 39.37007874015748 inches (SketchUp native unit)
METRES_TO_INCHES = 39.37007874015748

_IFC_BASE64 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$"


def generate_ifc_guid() -> str:
    """Generate a standard 22-character IFC base64 compressed GUID."""
    u = uuid.uuid4().int
    chars = []
    for _ in range(22):
        chars.append(_IFC_BASE64[u % 64])
        u //= 64
    return "".join(reversed(chars))


def sanitize_name(name: str) -> str:
    """Sanitize string for STEP text escaping."""
    if not name:
        return "Unnamed"
    clean = name.replace("'", "''").replace("\\", "\\\\").strip()
    return clean if clean else "Unnamed"


def classify_element(geom_name: str) -> Tuple[str, str]:
    """Map a geometry/component name to an IFC4 entity type and constructor.

    Returns:
        Tuple of (STEP_ENTITY_TYPE, IFC_CLASS_NAME)
    """
    l = geom_name.lower()
    if "wall" in l:
        return "IFCWALL", "IfcWall"
    if "door" in l:
        return "IFCDOOR", "IfcDoor"
    if "window" in l:
        return "IFCWINDOW", "IfcWindow"
    if "slab" in l or "floor" in l:
        return "IFCSLAB", "IfcSlab"
    if "column" in l or "pillar" in l:
        return "IFCCOLUMN", "IfcColumn"
    if "beam" in l or "joist" in l:
        return "IFCBEAM", "IfcBeam"
    if "roof" in l:
        return "IFCROOF", "IfcRoof"
    return "IFCBUILDINGELEMENTPROXY", "IfcBuildingElementProxy"


def _get_prim_rgb(scene: Scene, prim_mat_idx: int) -> Tuple[float, float, float, float]:
    """Extract (R, G, B, Alpha) normalized [0.0, 1.0] from gltf_materials."""
    r, g, b, a = 0.8, 0.8, 0.8, 1.0
    if 0 <= prim_mat_idx < len(scene.gltf_materials):
        mat = scene.gltf_materials[prim_mat_idx]
        if isinstance(mat, dict):
            pbr = mat.get("pbrMetallicRoughness", {})
            if isinstance(pbr, dict) and "baseColorFactor" in pbr:
                color_vec = pbr["baseColorFactor"]
                if isinstance(color_vec, (list, tuple)) and len(color_vec) >= 3:
                    r = max(0.0, min(1.0, float(color_vec[0])))
                    g = max(0.0, min(1.0, float(color_vec[1])))
                    b = max(0.0, min(1.0, float(color_vec[2])))
                    if len(color_vec) >= 4:
                        a = max(0.0, min(1.0, float(color_vec[3])))
    return r, g, b, a


def to_ifc(
    scene: Scene,
    scale: float = METRES_TO_INCHES,
    schema: str = "IFC4",
) -> str:
    """Serialize a baked Scene into ISO-10303-21 STEP ASCII IFC4 format.

    Args:

        scene: The baked scene returned by :meth:`SkpFile.build_scene`.
        scale: Coordinate scale factor (default: METRES_TO_INCHES).
        schema: IFC schema version (default: "IFC4").

    Returns:
        Formatted ASCII IFC text string.
    """
    if not isinstance(scene, Scene):
        raise TypeError("to_ifc requires a valid Scene instance")

    schema_str = schema.upper() if schema else "IFC4"
    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    timestamp_epoch = int(datetime.datetime.now(datetime.timezone.utc).timestamp())

    lines: List[str] = [
        "ISO-10303-21;",
        "HEADER;",
        "FILE_DESCRIPTION(('ViewDefinition [CoordinationView]'),'2;1');",
        f"FILE_NAME('model.ifc','{now_iso}',('OpenSKP Author'),('OpenSKP Organization'),'OpenSKP IFC Exporter','OpenSKP','');",
        f"FILE_SCHEMA(('{schema_str}'));",
        "ENDSEC;",
        "DATA;",
    ]

    entity_id = 1

    def next_id() -> int:
        nonlocal entity_id
        current = entity_id
        entity_id += 1
        return current

    # Boilerplate Owner History & Units
    person_id = next_id()
    lines.append(f"#{person_id}=IFCPERSON($,$,'OpenSKP User',$,$,$,$,$);")

    org_id = next_id()
    lines.append(f"#{org_id}=IFCORGANIZATION($,'OpenSKP',$,$,$);")

    person_org_id = next_id()
    lines.append(f"#{person_org_id}=IFCPERSONANDORGANIZATION(#{person_id},#{org_id},$);")

    app_id = next_id()
    lines.append(f"#{app_id}=IFCAPPLICATION(#{org_id},'0.3.0','OpenSKP Exporter','OpenSKP');")

    owner_hist_id = next_id()
    lines.append(
        f"#{owner_hist_id}=IFCOWNERHISTORY(#{person_org_id},#{app_id},$,.READWRITE.,$,$,$,{timestamp_epoch});"
    )

    length_unit_id = next_id()
    lines.append(f"#{length_unit_id}=IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.);")

    angle_unit_id = next_id()
    lines.append(f"#{angle_unit_id}=IFCSIUNIT(*,.PLANEANGLEUNIT.,$,.RADIAN.);")

    solid_unit_id = next_id()
    lines.append(f"#{solid_unit_id}=IFCSIUNIT(*,.STERADIANUNIT.,$,.STERADIAN.);")

    unit_assign_id = next_id()
    lines.append(
        f"#{unit_assign_id}=IFCUNITASSIGNMENT((#{length_unit_id},#{angle_unit_id},#{solid_unit_id}));"
    )

    # Geometry Context & Placement
    pt_zero_id = next_id()
    lines.append(f"#{pt_zero_id}=IFCCARTESIANPOINT((0.0,0.0,0.0));")

    axis_placement_id = next_id()
    lines.append(f"#{axis_placement_id}=IFCAXIS2PLACEMENT3D(#{pt_zero_id},$,$);")

    geom_ctx_id = next_id()
    lines.append(
        f"#{geom_ctx_id}=IFCGEOMETRICREPRESENTATIONCONTEXT($,'Model',3,1.0E-5,#{axis_placement_id},$);"
    )

    # Spatial Hierarchy: Project -> Site -> Building -> BuildingStorey
    proj_id = next_id()
    proj_guid = generate_ifc_guid()
    lines.append(
        f"#{proj_id}=IFCPROJECT('{proj_guid}',#{owner_hist_id},'OpenSKP Project',$,$,$,$,(#{geom_ctx_id}),#{unit_assign_id});"
    )

    site_placement_id = next_id()
    lines.append(f"#{site_placement_id}=IFCLOCALPLACEMENT($,#{axis_placement_id});")

    site_id = next_id()
    site_guid = generate_ifc_guid()
    lines.append(
        f"#{site_id}=IFCSITE('{site_guid}',#{owner_hist_id},'Site',$,$,#{site_placement_id},$,$,.ELEMENT.,$,$,$,$,$);"
    )

    bldg_placement_id = next_id()
    lines.append(f"#{bldg_placement_id}=IFCLOCALPLACEMENT(#{site_placement_id},#{axis_placement_id});")

    bldg_id = next_id()
    bldg_guid = generate_ifc_guid()
    lines.append(
        f"#{bldg_id}=IFCBUILDING('{bldg_guid}',#{owner_hist_id},'Building',$,$,#{bldg_placement_id},$,$,.ELEMENT.,$,$,$);"
    )

    storey_placement_id = next_id()
    lines.append(f"#{storey_placement_id}=IFCLOCALPLACEMENT(#{bldg_placement_id},#{axis_placement_id});")

    storey_id = next_id()
    storey_guid = generate_ifc_guid()
    lines.append(
        f"#{storey_id}=IFCBUILDINGSTOREY('{storey_guid}',#{owner_hist_id},'Level 0',$,$,#{storey_placement_id},$,$,.ELEMENT.,0.0);"
    )

    # Aggregates Relations
    rel_site_id = next_id()
    lines.append(
        f"#{rel_site_id}=IFCRELAGGREGATES('{generate_ifc_guid()}',#{owner_hist_id},$,$,#{proj_id},(#{site_id}));"
    )

    rel_bldg_id = next_id()
    lines.append(
        f"#{rel_bldg_id}=IFCRELAGGREGATES('{generate_ifc_guid()}',#{owner_hist_id},$,$,#{site_id},(#{bldg_id}));"
    )

    rel_storey_id = next_id()
    lines.append(
        f"#{rel_storey_id}=IFCRELAGGREGATES('{generate_ifc_guid()}',#{owner_hist_id},$,$,#{bldg_id},(#{storey_id}));"
    )

    product_ids: List[int] = []
    mat_style_cache: Dict[Tuple[float, float, float, float], int] = {}

    for prim in scene.glb_primitives:
        tri_count = len(prim.indices) // 3
        v_count = len(prim.positions) // 3
        if tri_count == 0 or v_count == 0:
            continue

        geom_name = sanitize_name(prim.geom_name)
        step_type, ifc_class = classify_element(geom_name)

        # 1. Coordinate Point List 3D
        pt_coords: List[str] = []
        for i in range(v_count):
            vx = round(prim.positions[i * 3] * scale, 6)
            vy = round(prim.positions[i * 3 + 1] * scale, 6)
            vz = round(prim.positions[i * 3 + 2] * scale, 6)
            pt_coords.append(f"({vx},{vy},{vz})")

        pt_list_id = next_id()
        lines.append(f"#{pt_list_id}=IFCCARTESIANPOINTLIST3D(({','.join(pt_coords)}));")

        # 2. Triangulated Face Set (1-based indices)
        face_indices: List[str] = []
        for i in range(tri_count):
            idx0 = prim.indices[i * 3] + 1
            idx1 = prim.indices[i * 3 + 1] + 1
            idx2 = prim.indices[i * 3 + 2] + 1
            face_indices.append(f"({idx0},{idx1},{idx2})")

        face_set_id = next_id()
        lines.append(
            f"#{face_set_id}=IFCTRIANGULATEDFACESET(#{pt_list_id},$,.TRUE.,({','.join(face_indices)}),$);"
        )

        # 3. Surface Style / Material Color if present
        r, g, b, a = _get_prim_rgb(scene, prim.material_index)
        rgba_key = (r, g, b, a)
        if rgba_key not in mat_style_cache:
            col_id = next_id()
            lines.append(f"#{col_id}=IFCCOLOURRGB($,{r:.4f},{g:.4f},{b:.4f});")

            transparency = round(1.0 - a, 4)
            rendering_id = next_id()
            lines.append(
                f"#{rendering_id}=IFCSURFACESTYLERENDERING(#{col_id},{transparency:.4f},$,$,$,$,$,$,.FLAT.);"
            )

            style_id = next_id()
            lines.append(
                f"#{style_id}=IFCSURFACESTYLE('{geom_name}_Material',.BOTH.,(#{rendering_id}));"
            )

            style_assign_id = next_id()
            lines.append(f"#{style_assign_id}=IFCPRESENTATIONSTYLEASSIGNMENT((#{style_id}));")
            mat_style_cache[rgba_key] = style_assign_id
        else:
            style_assign_id = mat_style_cache[rgba_key]

        styled_item_id = next_id()
        lines.append(f"#{styled_item_id}=IFCSTYLEDITEM(#{face_set_id},(#{style_assign_id}),$);")

        # 4. Shape Representation & Product Definition Shape
        shape_rep_id = next_id()
        lines.append(
            f"#{shape_rep_id}=IFCSHAPEREPRESENTATION(#{geom_ctx_id},'Body','Tessellation',(#{face_set_id}));"
        )

        prod_shape_id = next_id()
        lines.append(f"#{prod_shape_id}=IFCPRODUCTDEFINITIONSHAPE($,$,(#{shape_rep_id}));")

        prod_placement_id = next_id()
        lines.append(f"#{prod_placement_id}=IFCLOCALPLACEMENT(#{storey_placement_id},#{axis_placement_id});")

        prod_guid = generate_ifc_guid()
        product_id = next_id()
        if step_type == "IFCBUILDINGELEMENTPROXY":
            lines.append(
                f"#{product_id}={step_type}('{prod_guid}',#{owner_hist_id},'{geom_name}',$,$,#{prod_placement_id},#{prod_shape_id},$,.NOTDEFINED.);"
            )
        else:
            lines.append(
                f"#{product_id}={step_type}('{prod_guid}',#{owner_hist_id},'{geom_name}',$,$,#{prod_placement_id},#{prod_shape_id},$,$);"
            )

        product_ids.append(product_id)

        # 5. Property Sets (if scene metadata contains dynamic properties)
        meta = scene.mesh_index.get(prim.geom_name)
        if meta and hasattr(meta, "properties") and isinstance(meta.properties, dict) and meta.properties:
            prop_val_ids: List[int] = []
            for p_key, p_val in meta.properties.items():
                clean_k = sanitize_name(str(p_key))
                clean_v = sanitize_name(str(p_val))
                prop_id = next_id()
                lines.append(
                    f"#{prop_id}=IFCPROPERTYSINGLEVALUE('{clean_k}',$,IFCTEXT('{clean_v}'),$);"
                )
                prop_val_ids.append(prop_id)

            if prop_val_ids:
                pset_guid = generate_ifc_guid()
                pset_id = next_id()
                prop_refs = ",".join(f"#{pid}" for pid in prop_val_ids)
                lines.append(
                    f"#{pset_id}=IFCPROPERTYSET('{pset_guid}',#{owner_hist_id},'Pset_CustomProperties',$,({prop_refs}));"
                )

                rel_prop_guid = generate_ifc_guid()
                rel_prop_id = next_id()
                lines.append(
                    f"#{rel_prop_id}=IFCRELDEFINESBYPROPERTIES('{rel_prop_guid}',#{owner_hist_id},$,$,(#{product_id}),#{pset_id});"
                )

    # 6. Containment Relation in Spatial Hierarchy
    if product_ids:
        prod_refs = ",".join(f"#{pid}" for pid in product_ids)
        contain_rel_id = next_id()
        lines.append(
            f"#{contain_rel_id}=IFCRELCONTAINEDINSPATIALSTRUCTURE('{generate_ifc_guid()}',#{owner_hist_id},$,$,({prod_refs}),#{storey_id});"
        )

    lines.extend(["ENDSEC;", "END-ISO-10303-21;"])
    return "\r\n".join(lines) + "\r\n"


def export(
    scene: Scene,
    output_path: Union[str, pathlib.Path],
    scale: float = METRES_TO_INCHES,
    schema: str = "IFC4",
) -> None:
    """Export a baked scene to an ISO-10303-21 STEP ASCII IFC4 file.

    Args:
        scene: The baked scene returned by :meth:`SkpFile.build_scene`.
        output_path: Destination path (.ifc).
        scale: Coordinate scale factor (default: METRES_TO_INCHES).
        schema: IFC schema version (default: "IFC4").
    """
    path = pathlib.Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = to_ifc(scene, scale=scale, schema=schema)
    path.write_text(text, encoding="utf-8", errors="replace")
