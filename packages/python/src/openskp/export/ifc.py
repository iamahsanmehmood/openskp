"""IFC4 (BIM) 3D exporter for OpenSKP scenes.

Serializes a baked :class:`~openskp.scene.Scene` into ISO-10303-21 STEP ASCII format
conforming to the IFC4 schema (FILE_SCHEMA(('IFC4'))).

Uses native ``IfcTriangulatedFaceSet`` geometry representation, reusing the
baked scene's triangulated face indices as-is and its vertex positions after
converting them back from glTF's Y-up convention to IFC's (SketchUp's own)
Z-up convention.
"""

from __future__ import annotations

import datetime
import pathlib
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from ..scene import Scene

# 1 metre = 39.37007874015748 inches (SketchUp native unit) - kept for
# callers that want inch-scaled coordinates explicitly, but NOT the
# default scale below: the file always declares its length unit as
# millimetres (see IFCSIUNIT below), so the default scale has to produce
# millimetre-scaled values or every coordinate reads back ~25.4x too
# small in any IFC consumer that respects the unit declaration.
METRES_TO_INCHES = 39.37007874015748
METRES_TO_MM = 1000.0

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


def _classify_by_keyword(name: str) -> Union[Tuple[str, str], None]:
    """Match a single name string against the keyword vocabulary, or None."""
    name_lower = name.lower()
    if "wall" in name_lower:
        return "IFCWALL", "IfcWall"
    if "door" in name_lower:
        return "IFCDOOR", "IfcDoor"
    if "window" in name_lower:
        return "IFCWINDOW", "IfcWindow"
    if "slab" in name_lower or "floor" in name_lower:
        return "IFCSLAB", "IfcSlab"
    if "column" in name_lower or "pillar" in name_lower:
        return "IFCCOLUMN", "IfcColumn"
    if "beam" in name_lower or "joist" in name_lower:
        return "IFCBEAM", "IfcBeam"
    if "roof" in name_lower:
        return "IFCROOF", "IfcRoof"
    return None


def classify_element(
    geom_name: str,
    layer_name: str = "",
    path_name: str = "",
    classify_using_full_path: bool = False,
) -> Tuple[str, str]:
    """Map a geometry/component name to an IFC4 entity type and constructor.

    Tries the component's own name first - if a modeler bothered to name a
    part "Wall_A", that's the most specific signal available. Most
    real-world files never get that far (SketchUp's own default names like
    "Component#109415" carry no semantic info), so this falls back to
    ``layer_name`` next: many SketchUp-for-BIM workflows organize by
    tag/layer ("Walls", "Doors") even when individual components are never
    renamed.

    If neither matches and ``classify_using_full_path`` is set, falls back
    further to keyword-matching the component's full ancestor hierarchy
    path (e.g. "ROOT / Wall Frame / Stud 12") - a broader, noisier signal
    than the component's own name/layer, since it can match on an
    *ancestor's* name rather than the part itself. Off by default: this
    exporter used to match keywords against exactly this kind of
    concatenated internal string unconditionally (a bug - it also
    corrupted the element's displayed Name), and turning that broad
    matching back on is now an explicit opt-in rather than the only
    behavior available. Only if nothing at all matches does this fall
    back to a generic, untyped element.

    Returns:
        Tuple of (STEP_ENTITY_TYPE, IFC_CLASS_NAME)
    """
    result = _classify_by_keyword(geom_name)
    if result is not None:
        return result
    if layer_name:
        result = _classify_by_keyword(layer_name)
        if result is not None:
            return result
    if classify_using_full_path and path_name:
        result = _classify_by_keyword(path_name)
        if result is not None:
            return result
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
    scale: float = METRES_TO_MM,
    schema: str = "IFC4",
    classifier: Optional[Callable[[str, str], Tuple[str, str]]] = None,
    classify_using_full_path: bool = False,
) -> str:
    """Serialize a baked Scene into ISO-10303-21 STEP ASCII IFC4 format.

    Args:

        scene: The baked scene returned by :meth:`SkpFile.build_scene`.
        scale: Coordinate scale factor (default: METRES_TO_MM - matches the millimetre length unit this exporter always declares).
        schema: IFC schema version (default: "IFC4").
        classifier: Optional override for :func:`classify_element`, called
            as ``classifier(geom_name, layer_name)`` and expected to return
            the same ``(STEP_ENTITY_TYPE, IFC_CLASS_NAME)`` tuple - use this
            to supply your own naming convention or metadata-driven typing
            instead of the built-in keyword/layer heuristic. Ignored (never
            called) when ``classify_using_full_path`` is set, since that
            flag only affects the built-in classifier.
        classify_using_full_path: When the built-in classifier (i.e.
            ``classifier`` is not given) can't type an element from its own
            name or layer, also try keyword-matching its full ancestor
            hierarchy path (e.g. a part named "Stud 12" under a "Wall
            Frame" component would match on "Wall Frame"). Off by default -
            see :func:`classify_element` for why this is opt-in rather than
            always-on.

    Returns:
        Formatted ASCII IFC text string.
    """
    if not isinstance(scene, Scene):
        raise TypeError("to_ifc requires a valid Scene instance")

    if classifier is not None:
        def classify(name: str, layer: str, path: str) -> Tuple[str, str]:
            return classifier(name, layer)
    else:
        def classify(name: str, layer: str, path: str) -> Tuple[str, str]:
            return classify_element(name, layer, path, classify_using_full_path)

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
    lines.append(
        f"#{person_org_id}=IFCPERSONANDORGANIZATION(#{person_id},#{org_id},$);"
    )

    app_id = next_id()
    lines.append(
        f"#{app_id}=IFCAPPLICATION(#{org_id},'0.3.1','OpenSKP Exporter','OpenSKP');"
    )

    owner_hist_id = next_id()
    lines.append(
        f"#{owner_hist_id}=IFCOWNERHISTORY(#{person_org_id},#{app_id},$,.READWRITE.,$,$,$,{timestamp_epoch});"
    )

    def write_pset(product_id: int, pset_name: str, props: Dict[str, Any]) -> None:
        """Write one IFCPROPERTYSET from a flat string-keyed dict and attach
        it to ``product_id`` via IFCRELDEFINESBYPROPERTIES."""
        prop_val_ids: List[int] = []
        for p_key, p_val in props.items():
            clean_k = sanitize_name(str(p_key))
            clean_v = sanitize_name(str(p_val))
            prop_id = next_id()
            lines.append(
                f"#{prop_id}=IFCPROPERTYSINGLEVALUE('{clean_k}',$,IFCTEXT('{clean_v}'),$);"
            )
            prop_val_ids.append(prop_id)

        if not prop_val_ids:
            return

        pset_guid = generate_ifc_guid()
        pset_id = next_id()
        prop_refs = ",".join(f"#{pid}" for pid in prop_val_ids)
        lines.append(
            f"#{pset_id}=IFCPROPERTYSET('{pset_guid}',#{owner_hist_id},'{sanitize_name(pset_name)}',$,({prop_refs}));"
        )

        rel_prop_guid = generate_ifc_guid()
        rel_prop_id = next_id()
        lines.append(
            f"#{rel_prop_id}=IFCRELDEFINESBYPROPERTIES('{rel_prop_guid}',#{owner_hist_id},$,$,(#{product_id}),#{pset_id});"
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
    lines.append(
        f"#{bldg_placement_id}=IFCLOCALPLACEMENT(#{site_placement_id},#{axis_placement_id});"
    )

    bldg_id = next_id()
    bldg_guid = generate_ifc_guid()
    lines.append(
        f"#{bldg_id}=IFCBUILDING('{bldg_guid}',#{owner_hist_id},'Building',$,$,#{bldg_placement_id},$,$,.ELEMENT.,$,$,$);"
    )

    storey_placement_id = next_id()
    lines.append(
        f"#{storey_placement_id}=IFCLOCALPLACEMENT(#{bldg_placement_id},#{axis_placement_id});"
    )

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
    layer_items: Dict[str, List[int]] = {}
    mat_style_cache: Dict[Tuple[float, float, float, float], int] = {}

    # Assembly grouping: a named organizational wrapper with no geometry of
    # its own (e.g. FrameBuilder's "W-2" wall group, whose only children are
    # its studs/plates/cladding) becomes a real IFCELEMENTASSEMBLY, with its
    # member elements related to it via IFCRELAGGREGATES - mirroring the
    # named-wrapper local-id behavior already shipped for Fragments export
    # (export/fragments.py's _collect_leaves), so an IFC viewer's model tree
    # groups parts under "W-2" the same way FrameSmart's own tree already
    # does, instead of showing every part as a flat, ungrouped sibling.
    # Keyed by InstanceNode.path, which is built with the exact same
    # full-path string as MeshMetadata.path (see scene.py's instantiate()),
    # so a primitive's owning node can be found by a direct dict lookup.
    assembly_ids_by_path: Dict[str, int] = {}
    # One list per assembly of EVERY object it directly decomposes into -
    # both leaf member elements and nested child assemblies together, so
    # each assembly is the RelatingObject of exactly one IFCRELAGGREGATES
    # (some IFC consumers only look at the first such relation for a given
    # object, so splitting members and child assemblies across two
    # relations here would silently hide one set of them in those tools).
    assembly_related_objects: Dict[int, List[int]] = {}
    top_level_assembly_ids: List[int] = []
    nearest_assembly_by_path: Dict[str, Optional[int]] = {}

    def walk_assemblies(node, parent_assembly_id: Optional[int], is_root: bool = False) -> None:
        is_assembly = not is_root and not node.name_is_generated and bool(node.children)
        own_assembly_id = parent_assembly_id
        if is_assembly:
            placement_id = next_id()
            lines.append(
                f"#{placement_id}=IFCLOCALPLACEMENT(#{storey_placement_id},#{axis_placement_id});"
            )
            combined = f"{node.name} {node.layer}".lower()
            predefined = "TRUSS" if "truss" in combined else "NOTDEFINED"
            assembly_id = next_id()
            lines.append(
                f"#{assembly_id}=IFCELEMENTASSEMBLY('{generate_ifc_guid()}',#{owner_hist_id},"
                f"'{sanitize_name(node.name)}',$,$,#{placement_id},$,$,$,.{predefined}.);"
            )
            if node.properties:
                write_pset(assembly_id, "Pset_CustomProperties", node.properties)
            for dict_name, entries in (node.attribute_dictionaries or {}).items():
                if entries:
                    write_pset(assembly_id, f"Pset_{dict_name}", entries)
            if parent_assembly_id is not None:
                assembly_related_objects.setdefault(parent_assembly_id, []).append(assembly_id)
            else:
                top_level_assembly_ids.append(assembly_id)
            assembly_ids_by_path[node.path] = assembly_id
            own_assembly_id = assembly_id

        nearest_assembly_by_path[node.path] = own_assembly_id
        for child in node.children:
            walk_assemblies(child, own_assembly_id)

    walk_assemblies(scene.scene_hierarchy, None, is_root=True)

    for prim in scene.glb_primitives:
        tri_count = len(prim.indices) // 3
        v_count = len(prim.positions) // 3
        if tri_count == 0 or v_count == 0:
            continue

        meta = scene.mesh_index.get(prim.geom_name)
        # prim.geom_name is an internal lookup key (mesh index + hierarchy
        # path + layer, e.g. "mesh_3115_ROOT__Component_6205261_Layer0") -
        # never the element's real name. meta.name is the actual SketchUp
        # instance name (or SketchUp's own "Component_<id>" default when
        # nobody renamed it) - use that for both classification and the
        # element's IFC Name, falling back to the internal key only if a
        # primitive somehow has no mesh_index entry.
        display_name = sanitize_name(meta.name) if meta and meta.name else sanitize_name(prim.geom_name)
        layer_name = "Layer0"
        if meta and getattr(meta, "layer", None):
            layer_name = sanitize_name(meta.layer)

        path_name = meta.path if meta and getattr(meta, "path", None) else ""
        step_type, ifc_class = classify(display_name, layer_name, path_name)

        # 1. Coordinate Point List 3D
        #
        # scene.glb_primitives positions are baked in glTF's Y-up
        # convention (see Scene/scene.py: glTF.y = SketchUp Z (height),
        # glTF.z = -SketchUp Y (depth)) - correct for GLB export, but IFC
        # (like SketchUp itself) is Z-up, so it has to be converted back
        # rather than passed through raw, or the exported building comes
        # out rotated ~90 degrees and mirrored.
        pt_coords: List[str] = []
        for i in range(v_count):
            vx = round(prim.positions[i * 3] * scale, 6)
            vy = round(-prim.positions[i * 3 + 2] * scale, 6)
            vz = round(prim.positions[i * 3 + 1] * scale, 6)
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

        layer_items.setdefault(layer_name, []).append(face_set_id)

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
                f"#{style_id}=IFCSURFACESTYLE('{display_name}_Material',.BOTH.,(#{rendering_id}));"
            )

            style_assign_id = next_id()
            lines.append(
                f"#{style_assign_id}=IFCPRESENTATIONSTYLEASSIGNMENT((#{style_id}));"
            )
            mat_style_cache[rgba_key] = style_assign_id
        else:
            style_assign_id = mat_style_cache[rgba_key]

        styled_item_id = next_id()
        lines.append(
            f"#{styled_item_id}=IFCSTYLEDITEM(#{face_set_id},(#{style_assign_id}),$);"
        )

        # 4. Shape Representation & Product Definition Shape
        shape_rep_id = next_id()
        lines.append(
            f"#{shape_rep_id}=IFCSHAPEREPRESENTATION(#{geom_ctx_id},'Body','Tessellation',(#{face_set_id}));"
        )

        prod_shape_id = next_id()
        lines.append(
            f"#{prod_shape_id}=IFCPRODUCTDEFINITIONSHAPE($,$,(#{shape_rep_id}));"
        )

        prod_placement_id = next_id()
        lines.append(
            f"#{prod_placement_id}=IFCLOCALPLACEMENT(#{storey_placement_id},#{axis_placement_id});"
        )

        prod_guid = generate_ifc_guid()
        product_id = next_id()
        if step_type == "IFCBUILDINGELEMENTPROXY":
            lines.append(
                f"#{product_id}={step_type}('{prod_guid}',#{owner_hist_id},'{display_name}',$,$,#{prod_placement_id},#{prod_shape_id},$,.NOTDEFINED.);"
            )
        else:
            lines.append(
                f"#{product_id}={step_type}('{prod_guid}',#{owner_hist_id},'{display_name}',$,$,#{prod_placement_id},#{prod_shape_id},$,$);"
            )

        assembly_id = nearest_assembly_by_path.get(path_name) if path_name else None
        if assembly_id is not None:
            assembly_related_objects.setdefault(assembly_id, []).append(product_id)
        else:
            product_ids.append(product_id)

        # 5. Property Sets (if scene metadata contains dynamic properties)
        if meta and hasattr(meta, "properties") and isinstance(meta.properties, dict) and meta.properties:
            write_pset(product_id, "Pset_CustomProperties", meta.properties)

        # Any OTHER attribute dictionaries the instance carries (third-party
        # BIM/steel-detailing plugins, etc. - see
        # openskp.scene.InstanceNode.attribute_dictionaries) - each becomes
        # its own named property set instead of being merged into
        # Pset_CustomProperties, since these come from a distinct source and
        # commonly share key names with each other (e.g. multiple plugins
        # using "name").
        if meta and getattr(meta, "attribute_dictionaries", None):
            for dict_name, entries in meta.attribute_dictionaries.items():
                if entries:
                    write_pset(product_id, f"Pset_{dict_name}", entries)

    # 6. Presentation Layer Assignments (preserve layers and their on/off
    # state). IfcPresentationLayerWithStyle - not the plain
    # IfcPresentationLayerAssignment neither SketchUp's own IFC exporter
    # nor the IFC-manager SketchUp extension use - is the only IFC4 entity
    # that can carry a layer's visibility at all (LayerOn); every other
    # attribute here besides Name and LayerOn is left unset/false since
    # this project doesn't track them (freeze/block/layer-level styles).
    for l_name, item_ids in sorted(layer_items.items()):
        if item_ids:
            item_refs = ",".join(f"#{iid}" for iid in item_ids)
            layer_on = ".F." if scene.layer_hidden.get(l_name) else ".T."
            layer_assign_id = next_id()
            lines.append(
                f"#{layer_assign_id}=IFCPRESENTATIONLAYERWITHSTYLE("
                f"'{l_name}',$,({item_refs}),$,{layer_on},.F.,.F.,());"
            )

    # 7. Assembly Aggregation, then Containment Relation in Spatial
    # Hierarchy. Per IFC4 semantics an element belongs to exactly one
    # containment context: an assembly's members are related to it via
    # IFCRELAGGREGATES (not spatially contained themselves), a nested
    # assembly is aggregated into its parent assembly the same way, and
    # only the outermost assemblies - alongside any genuinely ungrouped
    # elements - are ever related to the storey via
    # IFCRELCONTAINEDINSPATIALSTRUCTURE.
    for assembly_id, related_ids in assembly_related_objects.items():
        related_refs = ",".join(f"#{rid}" for rid in related_ids)
        lines.append(
            f"#{next_id()}=IFCRELAGGREGATES('{generate_ifc_guid()}',#{owner_hist_id},$,$,#{assembly_id},({related_refs}));"
        )

    contained_ids = product_ids + top_level_assembly_ids
    if contained_ids:
        prod_refs = ",".join(f"#{pid}" for pid in contained_ids)
        contain_rel_id = next_id()
        lines.append(
            f"#{contain_rel_id}=IFCRELCONTAINEDINSPATIALSTRUCTURE('{generate_ifc_guid()}',#{owner_hist_id},$,$,({prod_refs}),#{storey_id});"
        )

    lines.extend(["ENDSEC;", "END-ISO-10303-21;"])
    return "\r\n".join(lines) + "\r\n"


def export(
    scene: Scene,
    output_path: Union[str, pathlib.Path],
    scale: float = METRES_TO_MM,
    schema: str = "IFC4",
    classifier: Optional[Callable[[str, str], Tuple[str, str]]] = None,
    classify_using_full_path: bool = False,
) -> None:
    """Export a baked scene to an ISO-10303-21 STEP ASCII IFC4 file.

    Args:
        scene: The baked scene returned by :meth:`SkpFile.build_scene`.
        output_path: Destination path (.ifc).
        scale: Coordinate scale factor (default: METRES_TO_MM - matches the millimetre length unit this exporter always declares).
        schema: IFC schema version (default: "IFC4").
        classifier: Optional override for :func:`classify_element` - see
            :func:`to_ifc` for the calling convention.
        classify_using_full_path: See :func:`to_ifc`.
    """
    path = pathlib.Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = to_ifc(
        scene, scale=scale, schema=schema, classifier=classifier,
        classify_using_full_path=classify_using_full_path,
    )
    path.write_bytes(text.encode("utf-8"))
