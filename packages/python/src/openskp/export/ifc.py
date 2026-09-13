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
import inspect
import json
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


def _step_text(text: str) -> str:
    """Escape a string for a STEP (ISO 10303-21) string literal.

    Non-ASCII used to go out as raw UTF-8, which is not legal STEP - the
    default character set is ISO 8859-1 - and conforming
    readers disagree about what to do with it. ifcopenshell 0.8.5 *silently
    drops every non-ASCII character*: a name like '剪力墙JLQ-1' reaches the
    downstream tool as 'JLQ-1'. Measured, not guessed - the bytes were on
    disk (4x UTF-8 剪) and gone after ifcopenshell.open().

    The two escape forms below are copied from what ifcopenshell's own writer
    emits, which is the authoritative reference (probed, not read off a spec):
        '测试项目'          -> '\\X2\\6D4B8BD5987976EE\\X0\\'
        'emoji\\U0001F600墙' -> 'emoji\\X4\\0001F60000005899\\X0\\'

    Rule: maximal runs of non-ASCII are wrapped in \\X2\\ (UTF-16BE, 4 hex per
    char), or \\X4\\ (8 hex per char) when the run contains a non-BMP char;
    ASCII falls outside the runs; ' -> '' and \\ -> \\\\.
    """
    out: list = []
    run: list = []
    wide = False

    def flush() -> None:
        if not run:
            return
        fmt = "%08X" if wide else "%04X"
        out.append("\\X%d\\" % (4 if wide else 2))
        out.extend(fmt % ord(c) for c in run)
        out.append("\\X0\\")
        del run[:]

    for ch in text:
        o = ord(ch)
        if o < 128:
            flush()
            if ch == "'":
                out.append("''")
            elif ch == "\\":
                out.append("\\\\")
            else:
                out.append(ch)
        else:
            run.append(ch)
            if o > 0xFFFF:
                wide = True
    flush()
    return "".join(out)


def sanitize_name(name: str) -> str:
    """Sanitize string for STEP text escaping."""
    if not name:
        return "Unnamed"
    clean = name.strip()
    if not clean:
        return "Unnamed"
    return _step_text(clean)


_ATTR_COUNT_CACHE: Dict[str, Dict[str, int]] = {}


def _attr_counts(schema: str) -> Dict[str, int]:
    """{STEP 名: 属性总个数}，如 {"IFCDOOR": 13, "IFCWALL": 9}。

    The table is needed because IFC entity types do NOT all have the same
    number of attributes, while the product line was hardcoded at 9.
    Values come from ifcopenshell's declaration.all_attributes(), cross-checked
    against the official .skc ifcXML schema (651/653 IFC2X3 entities agree; the
    2 exceptions are explained in gen_ifc_attr_counts.py). See that script.
    """
    key = (schema or "IFC4").upper()
    if key not in _ATTR_COUNT_CACHE:
        table: Dict[str, int] = {}
        try:
            path = pathlib.Path(__file__).with_name("ifc_attr_counts.json")
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            src = data.get(key) or data.get("IFC4") or {}
            table = {k.upper(): v for k, v in src.items()}
        except (OSError, ValueError):
            table = {}          # 缺表就退回老行为（9 个参数），不要炸掉导出
        _ATTR_COUNT_CACHE[key] = table
    return _ATTR_COUNT_CACHE[key]


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
            as ``classifier(geom_name, layer_name)`` - or
            ``classifier(geom_name, layer_name, path_name)`` if the callable
            accepts a third parameter, in which case it also receives the
            owning instance's full hierarchy path (the same string used as
            the key in ``Scene.mesh_index`` / ``InstanceNode.path``) -
            and expected to return the same
            ``(STEP_ENTITY_TYPE, IFC_CLASS_NAME)`` tuple - use this
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
        # A caller-supplied classifier used to be
        # called as classifier(name, layer) only - `path` was already
        # resolved a few lines below (meta.path) and then thrown away. That
        # path is the exact key into Scene.mesh_index / the InstanceNode
        # tree, so dropping it made it impossible to write a classifier that
        # consults the instance's definition name, node layer, or attribute
        # dictionaries. Forward it when the callback accepts it.
        #
        # Arity is inspected rather than probed with try/except TypeError:
        # a TypeError raised *inside* the classifier would otherwise be
        # swallowed as "wrong arity" and re-run with wrong arguments.
        try:
            _arity = len(inspect.signature(classifier).parameters)
        except (TypeError, ValueError):
            _arity = 2

        def classify(name: str, layer: str, path: str) -> Tuple[str, str]:
            if _arity >= 3:
                return classifier(name, layer, path)
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

    # `.READWRITE.` is not an IFC4 enumeration
    # literal. IfcChangeActionEnum in IFC4 is exactly (NOCHANGE, MODIFIED,
    # ADDED, DELETED, NOTDEFINED) - READWRITE only ever existed in the IFC2x3
    # version of the enum. Every file this exporter produced carried the
    # invalid literal, and ifcopenshell's validate() flags it under
    # express_rules=True:
    #   "An enumeration literal 'READWRITE' is not valid for type
    #    'IfcChangeActionEnum'"
    # The attribute is OPTIONAL, so `$` would also be legal; NOCHANGE is used
    # because it is what an untouched, just-created entity actually is.
    owner_hist_id = next_id()
    lines.append(
        f"#{owner_hist_id}=IFCOWNERHISTORY(#{person_org_id},#{app_id},$,.NOCHANGE.,$,$,$,{timestamp_epoch});"
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
    # .STERADIANUNIT. is not a value of IfcUnitEnum
    # (the legal set is .LENGTHUNIT. .MASSUNIT. ... .PLANEANGLEUNIT.
    #  .SOLIDANGLEUNIT. .AREAUNIT. .VOLUMEUNIT.). ifcopenshell validate
    # flagged it as "Attribute not optional / IfcCorrectUnitAssignment".
    # .STERADIAN. is a valid IfcSIUnitName, so only the type slot was wrong.
    lines.append(f"#{solid_unit_id}=IFCSIUNIT(*,.SOLIDANGLEUNIT.,$,.STERADIAN.);")

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

    # An Annotation sub-context for the loose-edge
    # curves. Until now they rode on the Model context above, which is what a
    # *body* belongs to; IFC puts line work in a sub-context instead, so a
    # consumer can tell a 3D solid from an annotation without guessing from
    # the representation identifier.
    #
    # Ten attributes, in schema order. The four derived ones - dimension,
    # precision, world coordinate system, true north - are written as `*`
    # because IFC4 declares them DERIVE FROM ParentContext, and writing real
    # values there is both redundant and, per the derivation constraint,
    # wrong. Verified against ifcopenshell's own serializer output rather
    # than from memory. Parent is the Model context (not a 2D Plan context):
    # these runs are 3D polylines and a sub-context cannot change
    # CoordinateSpaceDimension, so a 2D parent would make the points
    # semantically invalid.
    ann_ctx_id = next_id()
    lines.append(
        f"#{ann_ctx_id}=IFCGEOMETRICREPRESENTATIONSUBCONTEXT('Annotation',"
        f"'Model',*,*,*,*,#{geom_ctx_id},$,.MODEL_VIEW.,$);"
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

    # Loose-edge curve sets -> IfcAnnotation.
    #
    # Runs of *loose* edges - edges no face uses. This is the
    # only geometry a drawing-style model has: author a facade elevation in
    # SketchUp and the file contains 1776 edges and 0 faces, so the mesh loop
    # below contributes nothing and the export is a spatial skeleton with no
    # elements at all.
    #
    # IfcAnnotation + IfcGeometricCurveSet is IFC4's shape for exactly this:
    # zero-thickness line work. An IfcAnnotation is a real IfcProduct, so it
    # anchors through IfcRelContainedInSpatialStructure and shows up in a
    # viewer's model tree like any other element - but it carries no volume.
    # Deliberately NOT extruded into a solid: these are 2D profiles, and
    # inventing a thickness to force a "real" element would be fabricating
    # data the file does not contain (Revit imports them as lines, correctly).
    for cs in getattr(scene, "curve_sets", None) or []:
        pts = cs.points_m
        if len(pts) < 2:
            continue

        # Same glTF-Y-up -> IFC-Z-up conversion the mesh branch below uses
        # (there: y = -z, z = y; here the same swap on the curve's points).
        def _ifc_coord(p: Tuple[float, float, float]) -> str:
            return (f"({round(p[0] * scale, 6)},{round(-p[2] * scale, 6)},"
                    f"{round(p[1] * scale, 6)})")

        arc = getattr(cs, "arc", None)
        if arc and len(arc.get("points") or ()) >= 3:
            # A run that provably IS one whole
            # circular arc is emitted as an IfcIndexedPolyCurve with
            # IfcArcIndex segments, not as a polyline of its tessellation.
            #
            # This is the only place in the export where the analytic curve
            # survives: everything else is chords. It matters downstream -
            # a Revit/Bonsai endpoint receives an arc it can dimension,
            # snap to and re-fillet, where a 13-point polyline is just 13
            # points. The arc is not approximated here; the three points of
            # an IfcArcIndex are exact (they are the frame's own
            # center + cos(t)*x_axis + sin(t)*y_axis at the two ends and the
            # middle), verified against SketchUp's own COLLADA export of
            # arc.skp to 4e-8 inch.
            #
            # IFC4 declarations, read off ifcopenshell 0.8.5 rather than
            # from a secondhand table:
            #   IfcIndexedPolyCurve(Points, Segments, SelfIntersect)
            #   IfcCartesianPointList3D(CoordList) - Points is typed
            #     IfcCartesianPointList, the abstract supertype, which is
            #     exactly why no placement is needed: a standalone point
            #     list is legal here and nowhere else.
            #   IfcArcIndex = LIST [3:3] OF IfcPositiveInteger (start,
            #     a point ON the arc, end), IfcLineIndex = LIST [2:?].
            # CoordList is 1-based.
            #
            # IfcArcIndex is a DEFINED TYPE, not an entity, so it cannot
            # take a line of its own - it is a typed parameter written
            # inline inside Segments:
            #   (IFCARCINDEX((1,2,3)),IFCARCINDEX((3,4,1)))
            # Writing `#24=IFCARCINDEX((1,2,3));` is not merely unidiomatic,
            # it is a syntax error, and ifcopenshell 0.8.5 reacts by
            # truncating the file at that line WITHOUT a word: a 33-entity
            # export parsed as 23 entities, with the IfcAnnotation, the
            # layer assignment and the containment relationship all past the
            # cut. Hence no ids for the segments.
            segs = ",".join(
                "IFCARCINDEX((%s))" % ",".join(str(i + 1) for i in seg)
                for seg in arc["segments"]
            )
            pt_list_id = next_id()
            lines.append(
                f"#{pt_list_id}=IFCCARTESIANPOINTLIST3D("
                f"({','.join(_ifc_coord(p) for p in arc['points'])}));"
            )
            curve_id = next_id()
            lines.append(
                f"#{curve_id}=IFCINDEXEDPOLYCURVE(#{pt_list_id},"
                f"({segs}),.F.);"
            )
        else:
            pt_refs: List[str] = []
            coord_strs: List[str] = [_ifc_coord(p) for p in pts]
            # IfcPolyline has no implicit closing segment - a closed run
            # must repeat its first point or the last gap renders open.
            if cs.closed:
                coord_strs.append(coord_strs[0])
            for c in coord_strs:
                pt_id = next_id()
                lines.append(f"#{pt_id}=IFCCARTESIANPOINT({c});")
                pt_refs.append(f"#{pt_id}")

            curve_id = next_id()
            lines.append(
                f"#{curve_id}=IFCPOLYLINE(({','.join(pt_refs)}));"
            )

        curve_set_id = next_id()
        lines.append(f"#{curve_set_id}=IFCGEOMETRICCURVESET((#{curve_id}));")

        # The layer assignment takes representation items, so the curve
        # goes in - that is what carries the loose-edge layer (A-GLAZ-CWMG /
        # A-GLAZ-CURT on a real facade drawing) across to the IFC.
        layer_items.setdefault(cs.layer or "Layer0", []).append(curve_id)

        ann_name = sanitize_name(cs.name or "Curve")
        ann_rep_id = next_id()
        lines.append(
            f"#{ann_rep_id}=IFCSHAPEREPRESENTATION(#{ann_ctx_id},"
            f"'Annotation','GeometricCurveSet',(#{curve_set_id}));"
        )

        ann_shape_id = next_id()
        lines.append(
            f"#{ann_shape_id}=IFCPRODUCTDEFINITIONSHAPE($,$,(#{ann_rep_id}));"
        )

        ann_placement_id = next_id()
        lines.append(
            f"#{ann_placement_id}=IFCLOCALPLACEMENT(#{storey_placement_id},#{axis_placement_id});"
        )

        ann_id = next_id()
        lines.append(
            f"#{ann_id}=IFCANNOTATION('{generate_ifc_guid()}',#{owner_hist_id},"
            f"'{ann_name}',$,$,#{ann_placement_id},#{ann_shape_id});"
        )
        # Same property-set treatment the mesh branch gives its primitives:
        # each attribute dictionary becomes its own named Pset. This is how
        # ifc_classify's Pset_AI_Classification reaches a curve-only model
        # (there are no meshes to hang it on, so without this the inference
        # basis would silently not ship for such files).
        for dict_name, entries in (getattr(cs, "attribute_dictionaries", None) or {}).items():
            if entries:
                write_pset(ann_id, f"Pset_{dict_name}", entries)

        product_ids.append(ann_id)

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

        # `Closed` was a hardcoded `.TRUE.`.
        #
        # Two separate bugs in one token. (a) The literal: `Closed` is an
        # IfcBoolean, whose STEP spelling in IFC4 is `.T.`/`.F.` - `.TRUE.`
        # is the IFC2x spelling, and ifcopenshell's validate() flags it:
        #   "An enumeration literal 'TRUE' is not expected at attribute
        #    index '2'"
        # (b) The *claim*: `.TRUE.` says every mesh this exporter ever wrote
        # is a closed manifold. The exporter never checked. Now it is:
        # a triangle mesh is closed iff every undirected edge is shared by
        # exactly two triangles - no boundary edge, no edge used 3+ times.
        #
        # The weld-by-position step is not decoration. Reading closure off
        # the index buffer directly reports False for *every* mesh this
        # pipeline produces, because the vertices it emits are per-face, not
        # shared - measured on mx_Primitives: 576 verts / 276 tris, 576
        # boundary edges, i.e. no edge shared at all. That is a fact about
        # this exporter's output layout, not about the geometry: weld by
        # rounded position first and the same two models come out 1/1 and
        # 16/16 closed. Rounding to 6 dp matches the coordinates written to
        # IFCCARTESIANPOINTLIST3D above, so the two agree on what "same
        # point" means.
        _weld: Dict[Tuple[float, float, float], int] = {}
        _remap = [0] * v_count
        for i in range(v_count):
            _key = (round(prim.positions[i * 3], 6),
                    round(prim.positions[i * 3 + 1], 6),
                    round(prim.positions[i * 3 + 2], 6))
            _remap[i] = _weld.setdefault(_key, len(_weld))
        _edge_use: Dict[Tuple[int, int], int] = {}
        for i in range(tri_count):
            _a = _remap[prim.indices[i * 3]]
            _b = _remap[prim.indices[i * 3 + 1]]
            _c = _remap[prim.indices[i * 3 + 2]]
            for _u, _v in ((_a, _b), (_b, _c), (_c, _a)):
                _k = (_u, _v) if _u < _v else (_v, _u)
                _edge_use[_k] = _edge_use.get(_k, 0) + 1
        _closed = bool(_edge_use) and all(n == 2 for n in _edge_use.values())

        face_set_id = next_id()
        lines.append(
            f"#{face_set_id}=IFCTRIANGULATEDFACESET(#{pt_list_id},$,"
            f"{'.T.' if _closed else '.F.'},({','.join(face_indices)}),$);"
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
        # This used to write a hardcoded 9-argument
        # line for every entity type. 9 happens to be right for IfcWall, but
        # IFC4 IfcDoor/IfcWindow take 13 (OverallHeight, OverallWidth,
        # PredefinedType, OperationType, UserDefinedOperationType), so those
        # came out malformed - ifcopenshell reported
        #   "Index 9 is out of range for variant of size 9"
        # plus four "Invalid attribute value" per door/window. Pad to the
        # type's real attribute count instead (see ifc_attr_counts.json, whose
        # values come from ifcopenshell and were cross-checked against the
        # official .skc ifcXML schema).
        attrs = [
            f"'{prod_guid}'",           # GlobalId
            f"#{owner_hist_id}",        # OwnerHistory
            f"'{display_name}'",        # Name
            "$",                        # Description
            "$",                        # ObjectType
            f"#{prod_placement_id}",    # ObjectPlacement
            f"#{prod_shape_id}",        # Representation
            "$",                        # Tag
        ]
        total = _attr_counts(schema_str).get(step_type, 0) or 9
        if total < len(attrs):
            total = len(attrs)          # 表里没有这个类型就别写坏，至少不比原来差
        while len(attrs) < total:
            attrs.append("$")
        # IfcBuildingElementProxy 的第 9 个属性正是 PredefinedType，写
        # .NOTDEFINED. 比 $ 更明确（IFC4 里该属性可选，两者都合法）
        if step_type == "IFCBUILDINGELEMENTPROXY" and total == 9:
            attrs[8] = ".NOTDEFINED."
        lines.append(f"#{product_id}={step_type}({','.join(attrs)});")

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
            # l_name went out raw. Layer names are
            # exactly where non-ASCII lives on a Chinese model (剪力墙, 框架柱,
            # ...), so this was the widest hole for the STEP escaping bug.
            lines.append(
                f"#{layer_assign_id}=IFCPRESENTATIONLAYERWITHSTYLE("
                f"'{sanitize_name(l_name)}',$,({item_refs}),$,{layer_on},"
                f".F.,.F.,());"
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
