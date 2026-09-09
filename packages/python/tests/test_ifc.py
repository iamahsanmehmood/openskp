from array import array
import tempfile
import pathlib

from openskp.scene import GlbPrimitive, MeshMetadata, Scene, InstanceNode
from openskp.export.ifc import to_ifc, export, classify_element, generate_ifc_guid


def create_mock_scene() -> Scene:
    prim1 = GlbPrimitive(
        positions=array("f", [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0]),
        normals=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
        uvs=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
        indices=array("I", [0, 1, 2]),
        material_index=0,
        geom_name="Outer Wall",
    )
    prim2 = GlbPrimitive(
        positions=array("f", [2.0, 0.0, 0.0, 3.0, 0.0, 0.0, 2.0, 1.0, 0.0]),
        normals=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
        uvs=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
        indices=array("I", [0, 1, 2]),
        material_index=1,
        geom_name="Front Door",
    )
    materials = [
        {"pbrMetallicRoughness": {"baseColorFactor": [0.8, 0.2, 0.2, 1.0]}},
        {"pbrMetallicRoughness": {"baseColorFactor": [0.2, 0.8, 0.2, 0.9]}},
    ]
    mesh_index = {
        "Outer Wall": MeshMetadata(
            name="Outer Wall", properties={"Thickness": "200mm", "LoadBearing": "True"}
        ),
        "Front Door": MeshMetadata(name="Front Door", properties={"Material": "Wood"}),
    }
    return Scene(
        scene_hierarchy=InstanceNode(name="Root"),
        mesh_index=mesh_index,
        glb_primitives=[prim1, prim2],
        gltf_materials=materials,
    )


class TestIfcExporter:
    def test_generate_ifc_guid(self):
        guid = generate_ifc_guid()
        assert isinstance(guid, str)
        assert len(guid) == 22

    def test_classify_element(self):
        assert classify_element("Main Wall")[0] == "IFCWALL"
        assert classify_element("Front Door")[0] == "IFCDOOR"
        assert classify_element("Office Window")[0] == "IFCWINDOW"
        assert classify_element("Concrete Slab")[0] == "IFCSLAB"
        assert classify_element("Pillar Column")[0] == "IFCCOLUMN"
        assert classify_element("Steel Beam")[0] == "IFCBEAM"
        assert classify_element("Roof Tile")[0] == "IFCROOF"
        assert classify_element("Random Object")[0] == "IFCBUILDINGELEMENTPROXY"

    def test_classify_element_falls_back_to_layer_name(self):
        # A SketchUp default component name carries no keyword, but a
        # BIM-style layer/tag name often does - this is the real-world
        # case openskp#238 reported (components never renamed, but
        # organized onto layers like "Walls").
        assert classify_element("Component#109415", "Walls")[0] == "IFCWALL"
        assert classify_element("Group#3", "Doors")[0] == "IFCDOOR"

    def test_classify_element_prefers_component_name_over_layer(self):
        # The component's own name is a more specific signal than the
        # layer it happens to sit on, so it must win when both match.
        assert classify_element("Interior Door", "Walls")[0] == "IFCDOOR"

    def test_classify_element_generic_when_neither_matches(self):
        assert (
            classify_element("Component#109415", "Layer0")[0]
            == "IFCBUILDINGELEMENTPROXY"
        )
        assert classify_element("Component#109415")[0] == "IFCBUILDINGELEMENTPROXY"

    def test_classify_element_full_path_is_opt_in(self):
        """Neither the part's own name nor its layer say "wall" - only an
        ancestor in its hierarchy path does. That must stay untyped by
        default, and only match when classify_using_full_path is set -
        this is the broader, noisier fallback callers can opt into after
        openskp#272 stopped matching keywords against internal path
        strings unconditionally."""
        path = "ROOT / Wall Frame / Stud 12"
        assert classify_element("Stud 12", "Layer0", path)[0] == "IFCBUILDINGELEMENTPROXY"
        assert (
            classify_element("Stud 12", "Layer0", path, classify_using_full_path=True)[0]
            == "IFCWALL"
        )

    def test_classify_element_full_path_still_prefers_name_and_layer(self):
        """The path fallback only kicks in once name and layer both miss -
        it must never override a real, specific match."""
        path = "ROOT / Wall Frame / Front Door"
        result = classify_element("Front Door", "Layer0", path, classify_using_full_path=True)
        assert result[0] == "IFCDOOR"

    def test_to_ifc_uses_layer_name_fallback_for_unnamed_components(self):
        prim = GlbPrimitive(
            positions=array("f", [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0]),
            normals=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            uvs=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            indices=array("I", [0, 1, 2]),
            material_index=0,
            geom_name="Component#109415",
        )
        scene = Scene(
            scene_hierarchy=InstanceNode(name="Root"),
            mesh_index={
                "Component#109415": MeshMetadata(name="Component#109415", layer="Walls")
            },
            glb_primitives=[prim],
            gltf_materials=[{}],
        )
        ifc_text = to_ifc(scene)
        assert "IFCWALL(" in ifc_text
        assert "IFCBUILDINGELEMENTPROXY" not in ifc_text

    def test_to_ifc_classify_using_full_path_opt_in(self):
        prim = GlbPrimitive(
            positions=array("f", [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0]),
            normals=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            uvs=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            indices=array("I", [0, 1, 2]),
            material_index=0,
            geom_name="mesh_0_ROOT__Wall_Frame__Stud_12_Layer0",
        )
        scene = Scene(
            scene_hierarchy=InstanceNode(name="Root"),
            mesh_index={
                "mesh_0_ROOT__Wall_Frame__Stud_12_Layer0": MeshMetadata(
                    name="Stud 12", layer="Layer0", path="ROOT / Wall Frame / Stud 12",
                )
            },
            glb_primitives=[prim],
            gltf_materials=[{}],
        )

        default_text = to_ifc(scene)
        assert "IFCBUILDINGELEMENTPROXY" in default_text
        assert "IFCWALL(" not in default_text

        opted_in_text = to_ifc(scene, classify_using_full_path=True)
        assert "IFCWALL(" in opted_in_text
        # the real, clean name is still what's shown - opting into the
        # broader classification fallback doesn't reintroduce the old
        # mangled-name-as-Name bug.
        assert "'Stud 12'" in opted_in_text

    def test_to_ifc_accepts_a_custom_classifier(self):
        scene = create_mock_scene()

        def always_column(geom_name, layer_name):
            return "IFCCOLUMN", "IfcColumn"

        ifc_text = to_ifc(scene, classifier=always_column)
        assert "IFCWALL(" not in ifc_text
        assert "IFCDOOR(" not in ifc_text
        assert ifc_text.count("IFCCOLUMN(") == 2

    def test_to_ifc_structure(self):
        scene = create_mock_scene()
        ifc_text = to_ifc(scene)

        assert "ISO-10303-21;" in ifc_text
        assert "HEADER;" in ifc_text
        assert "FILE_SCHEMA(('IFC4'));" in ifc_text
        assert "IFCPROJECT" in ifc_text
        assert "IFCSITE" in ifc_text
        assert "IFCBUILDING" in ifc_text
        assert "IFCBUILDINGSTOREY" in ifc_text
        assert "IFCWALL" in ifc_text
        assert "IFCDOOR" in ifc_text
        assert "IFCTRIANGULATEDFACESET" in ifc_text
        assert "IFCCARTESIANPOINTLIST3D" in ifc_text
        assert "IFCPROPERTYSET" in ifc_text
        assert "IFCPROPERTYSINGLEVALUE" in ifc_text
        assert "IFCRELCONTAINEDINSPATIALSTRUCTURE" in ifc_text
        assert "ENDSEC;" in ifc_text

    def test_to_ifc_declares_millimetres_and_scales_to_match(self):
        """The exporter always declares millimetres - the default scale has
        to actually produce millimetre-scaled values, or every coordinate
        reads back ~25.4x too small in any IFC consumer that respects the
        unit declaration (this exact bug, caught 2026-09-07 comparing
        against a real SketchUp IFC export of the same file)."""
        prim = GlbPrimitive(
            positions=array("f", [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]),
            normals=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            uvs=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            indices=array("I", [0, 1, 2]),
            material_index=0,
            geom_name="Test Triangle",
        )
        scene = Scene(
            scene_hierarchy=InstanceNode(name="Root"),
            mesh_index={"Test Triangle": MeshMetadata(name="Test Triangle", properties={})},
            glb_primitives=[prim],
            gltf_materials=[{"pbrMetallicRoughness": {"baseColorFactor": [0.5, 0.5, 0.5, 1.0]}}],
        )
        ifc_text = to_ifc(scene)

        assert "IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.)" in ifc_text
        # vertex 2 is glTF (1.0, 0.0, 0.0) = 1 metre along X, which maps
        # straight through to IFC X - millimetres means it must come out
        # as 1000.0, not ~39.37 (metres-to-inches, the old default).
        assert "(1000.0,-0.0,0.0)" in ifc_text

    def test_to_ifc_converts_gltf_y_up_to_ifc_z_up(self):
        """scene.glb_primitives positions are baked in glTF's Y-up
        convention (glTF.y = SketchUp Z/height, glTF.z = -SketchUp
        Y/depth) for GLB export - IFC (like SketchUp itself) is Z-up, so
        to_ifc must convert back rather than pass positions through raw,
        or the exported building comes out rotated ~90 degrees and
        mirrored (this exact bug, caught 2026-09-07 comparing against a
        real SketchUp IFC export of the same file)."""
        prim = GlbPrimitive(
            positions=array("f", [0.0, 0.0, 0.0, 2.0, 3.0, 5.0, 0.0, 1.0, 0.0]),
            normals=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            uvs=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            indices=array("I", [0, 1, 2]),
            material_index=0,
            geom_name="Test Triangle",
        )
        scene = Scene(
            scene_hierarchy=InstanceNode(name="Root"),
            mesh_index={"Test Triangle": MeshMetadata(name="Test Triangle", properties={})},
            glb_primitives=[prim],
            gltf_materials=[{"pbrMetallicRoughness": {"baseColorFactor": [0.5, 0.5, 0.5, 1.0]}}],
        )
        ifc_text = to_ifc(scene, scale=1.0)

        # glTF (2.0, 3.0, 5.0) is SketchUp (x=2.0, y=-5.0, z=3.0) - IFC/
        # SketchUp Z-up means that vertex must appear as (2.0,-5.0,3.0),
        # not the raw glTF-order (2.0,3.0,5.0).
        assert "(2.0,-5.0,3.0)" in ifc_text
        assert "(2.0,3.0,5.0)" not in ifc_text

    def test_to_ifc_uses_real_instance_name_not_internal_key(self):
        """prim.geom_name is an internal lookup key (mesh index + hierarchy
        path + layer, e.g. "mesh_3_ROOT__W1_Layer0") - never a name a user
        should see. The IFC element's Name must come from
        MeshMetadata.name (the actual SketchUp instance name) instead
        (this exact bug, caught 2026-09-07 comparing against real IFC
        exports of the same file)."""
        prim = GlbPrimitive(
            positions=array("f", [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0]),
            normals=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            uvs=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            indices=array("I", [0, 1, 2]),
            material_index=0,
            geom_name="mesh_3_ROOT__W1_Layer0",
        )
        scene = Scene(
            scene_hierarchy=InstanceNode(name="Root"),
            mesh_index={"mesh_3_ROOT__W1_Layer0": MeshMetadata(name="W1", layer="Layer0")},
            glb_primitives=[prim],
            gltf_materials=[{"pbrMetallicRoughness": {"baseColorFactor": [0.5, 0.5, 0.5, 1.0]}}],
        )
        ifc_text = to_ifc(scene)

        assert "'W1'" in ifc_text
        assert "mesh_3_ROOT" not in ifc_text

    def test_to_ifc_layer_on_reflects_scene_layer_hidden(self):
        """Only IfcPresentationLayerWithStyle (not the plain
        IfcPresentationLayerAssignment this exporter used to write)
        carries a layer's visibility - LayerOn must match the source
        file's own hidden/visible state per layer, not just default to
        visible for everything."""
        scene = create_mock_scene()
        scene.mesh_index["Outer Wall"].layer = "Hidden Layer"
        scene.mesh_index["Front Door"].layer = "Visible Layer"
        scene.layer_hidden = {"Hidden Layer": True, "Visible Layer": False}

        ifc_text = to_ifc(scene)

        assert "IFCPRESENTATIONLAYERWITHSTYLE" in ifc_text
        assert "IFCPRESENTATIONLAYERASSIGNMENT(" not in ifc_text
        assert "'Hidden Layer',$,(#" in ifc_text
        hidden_line = next(line for line in ifc_text.splitlines() if "'Hidden Layer'" in line)
        visible_line = next(line for line in ifc_text.splitlines() if "'Visible Layer'" in line)
        assert ",.F.,.F.,.F.,())" in hidden_line
        assert ",.T.,.F.,.F.,())" in visible_line

    def test_to_ifc_writes_a_property_set_per_extra_attribute_dictionary(self):
        """A third-party plugin's attribute dictionary (e.g. the
        steel-detailing "fbd-einfo" seen on the Keith Street file) must
        reach the exported IFC as its own named property set - separate
        from Pset_CustomProperties (which stays SketchUp's own Dynamic
        Components data), since a second source of properties commonly
        reuses key names like "name"."""
        prim = GlbPrimitive(
            positions=array("f", [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0]),
            normals=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            uvs=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            indices=array("I", [0, 1, 2]),
            material_index=0,
            geom_name="mesh_0_ROOT__Profile25_Layer0",
        )
        scene = Scene(
            scene_hierarchy=InstanceNode(name="Root"),
            mesh_index={
                "mesh_0_ROOT__Profile25_Layer0": MeshMetadata(
                    name="Profile25",
                    properties={"width": "10.0"},
                    attribute_dictionaries={"fbd-einfo": {"code": "aPf", "angle": "90"}},
                )
            },
            glb_primitives=[prim],
            gltf_materials=[{"pbrMetallicRoughness": {"baseColorFactor": [0.5, 0.5, 0.5, 1.0]}}],
        )
        ifc_text = to_ifc(scene)

        assert "'Pset_CustomProperties'" in ifc_text
        assert "'Pset_fbd-einfo'" in ifc_text
        assert "'width'" in ifc_text
        assert "'code'" in ifc_text and "'aPf'" in ifc_text
        assert "'angle'" in ifc_text and "'90'" in ifc_text

    def test_to_ifc_skips_empty_attribute_dictionaries(self):
        prim = GlbPrimitive(
            positions=array("f", [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0]),
            normals=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            uvs=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            indices=array("I", [0, 1, 2]),
            material_index=0,
            geom_name="Test Triangle",
        )
        scene = Scene(
            scene_hierarchy=InstanceNode(name="Root"),
            mesh_index={
                "Test Triangle": MeshMetadata(
                    name="Test Triangle", properties={}, attribute_dictionaries={"empty_dict": {}}
                )
            },
            glb_primitives=[prim],
            gltf_materials=[{"pbrMetallicRoughness": {"baseColorFactor": [0.5, 0.5, 0.5, 1.0]}}],
        )
        ifc_text = to_ifc(scene)

        assert "Pset_empty_dict" not in ifc_text

    def test_to_ifc_wraps_named_wrapper_in_element_assembly(self):
        """A pure organizational group with real, per-instance identity
        (FrameBuilder's "W-2" wall, whose own children are its studs/
        plates/cladding) has no geometry of its own - export/fragments.py
        already gives it its own trackable identity for Fragments export
        (openskp#286); IFC needs the equivalent: a real IFCELEMENTASSEMBLY
        with its members related via IFCRELAGGREGATES, not a flat sibling
        list under the storey."""
        stud = GlbPrimitive(
            positions=array("f", [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0]),
            normals=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            uvs=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            indices=array("I", [0, 1, 2]),
            material_index=0,
            geom_name="mesh_0_ROOT__W-2__Stud_1_Layer0",
        )
        plate = GlbPrimitive(
            positions=array("f", [2.0, 0.0, 0.0, 3.0, 0.0, 0.0, 2.0, 1.0, 0.0]),
            normals=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            uvs=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            indices=array("I", [0, 1, 2]),
            material_index=0,
            geom_name="mesh_1_ROOT__W-2__Plate_1_Layer0",
        )
        scene_hierarchy = InstanceNode(
            name="ROOT",
            path="ROOT",
            children=[
                InstanceNode(
                    name="W-2",
                    path="ROOT / W-2",
                    properties={"MarkID": "W-2"},
                    children=[
                        InstanceNode(name="Stud 1", path="ROOT / W-2 / Stud 1"),
                        InstanceNode(name="Plate 1", path="ROOT / W-2 / Plate 1"),
                    ],
                ),
            ],
        )
        scene = Scene(
            scene_hierarchy=scene_hierarchy,
            mesh_index={
                "mesh_0_ROOT__W-2__Stud_1_Layer0": MeshMetadata(
                    name="Stud 1", path="ROOT / W-2 / Stud 1",
                ),
                "mesh_1_ROOT__W-2__Plate_1_Layer0": MeshMetadata(
                    name="Plate 1", path="ROOT / W-2 / Plate 1",
                ),
            },
            glb_primitives=[stud, plate],
            gltf_materials=[{"pbrMetallicRoughness": {"baseColorFactor": [0.5, 0.5, 0.5, 1.0]}}],
        )
        ifc_text = to_ifc(scene)

        assert ifc_text.count("IFCELEMENTASSEMBLY(") == 1
        assembly_line = next(
            line for line in ifc_text.splitlines() if "IFCELEMENTASSEMBLY(" in line
        )
        assert "'W-2'" in assembly_line
        assembly_id = assembly_line.split("=")[0].lstrip("#")

        # Member elements go through IFCRELAGGREGATES to the assembly...
        rel_agg_line = next(
            line for line in ifc_text.splitlines()
            if line.startswith("#") and "IFCRELAGGREGATES(" in line and f",#{assembly_id},(" in line
        )
        stud_id = next(
            line for line in ifc_text.splitlines() if "'Stud 1'" in line and "IFCBUILDINGELEMENTPROXY" in line
        ).split("=")[0].lstrip("#")
        plate_id = next(
            line for line in ifc_text.splitlines() if "'Plate 1'" in line and "IFCBUILDINGELEMENTPROXY" in line
        ).split("=")[0].lstrip("#")
        assert f"#{stud_id}" in rel_agg_line
        assert f"#{plate_id}" in rel_agg_line

        # ...and only the assembly itself (never its individual members)
        # is related to the storey via IFCRELCONTAINEDINSPATIALSTRUCTURE.
        contain_line = next(
            line for line in ifc_text.splitlines() if "IFCRELCONTAINEDINSPATIALSTRUCTURE(" in line
        )
        assert f"#{assembly_id}" in contain_line
        assert f"#{stud_id}" not in contain_line
        assert f"#{plate_id}" not in contain_line

        # The wrapper's own properties (FrameBuilder's own Mark ID, here)
        # attach to the assembly element itself, not to its members.
        assert "'MarkID'" in ifc_text
        assert "'W-2'" in ifc_text  # both the element Name and the pset value

    def test_to_ifc_generic_wrapper_stays_flat(self):
        """A generated/placeholder group name (SketchUp's own auto "Group#1"
        style, or simply no children) must NOT become an IFCELEMENTASSEMBLY -
        only a real, named organizational wrapper should."""
        prim = GlbPrimitive(
            positions=array("f", [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0]),
            normals=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            uvs=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            indices=array("I", [0, 1, 2]),
            material_index=0,
            geom_name="mesh_0_ROOT__Group#1__Stud_1_Layer0",
        )
        scene_hierarchy = InstanceNode(
            name="ROOT",
            path="ROOT",
            children=[
                InstanceNode(
                    name="Component_2",
                    name_is_generated=True,
                    path="ROOT / Component_2",
                    children=[
                        InstanceNode(name="Stud 1", path="ROOT / Component_2 / Stud 1"),
                    ],
                ),
            ],
        )
        scene = Scene(
            scene_hierarchy=scene_hierarchy,
            mesh_index={
                "mesh_0_ROOT__Group#1__Stud_1_Layer0": MeshMetadata(
                    name="Stud 1", path="ROOT / Component_2 / Stud 1",
                ),
            },
            glb_primitives=[prim],
            gltf_materials=[{"pbrMetallicRoughness": {"baseColorFactor": [0.5, 0.5, 0.5, 1.0]}}],
        )
        ifc_text = to_ifc(scene)

        assert "IFCELEMENTASSEMBLY(" not in ifc_text
        # IFCRELAGGREGATES still appears 3 times for the boilerplate
        # Project -> Site -> Building -> Storey hierarchy - just not for
        # this generated-name wrapper.
        assert ifc_text.count("IFCRELAGGREGATES(") == 3
        contain_line = next(
            line for line in ifc_text.splitlines() if "IFCRELCONTAINEDINSPATIALSTRUCTURE(" in line
        )
        assert "'Stud 1'" not in contain_line  # sanity: Name isn't in this line at all
        stud_id = next(
            line for line in ifc_text.splitlines() if "'Stud 1'" in line
        ).split("=")[0].lstrip("#")
        assert f"#{stud_id}" in contain_line

    def test_to_ifc_nests_assemblies_via_rel_aggregates(self):
        """A named wrapper nested inside another named wrapper (e.g. a
        "door1" sub-assembly inside wall "W-2") must be related to its
        *parent assembly* via IFCRELAGGREGATES, not directly to the
        storey - only the outermost assembly is ever spatially contained."""
        hinge = GlbPrimitive(
            positions=array("f", [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0]),
            normals=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            uvs=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            indices=array("I", [0, 1, 2]),
            material_index=0,
            geom_name="mesh_0_ROOT__W-2__door1__Hinge_Layer0",
        )
        scene_hierarchy = InstanceNode(
            name="ROOT",
            path="ROOT",
            children=[
                InstanceNode(
                    name="W-2",
                    path="ROOT / W-2",
                    children=[
                        InstanceNode(
                            name="door1",
                            path="ROOT / W-2 / door1",
                            children=[
                                InstanceNode(name="Hinge", path="ROOT / W-2 / door1 / Hinge"),
                            ],
                        ),
                    ],
                ),
            ],
        )
        scene = Scene(
            scene_hierarchy=scene_hierarchy,
            mesh_index={
                "mesh_0_ROOT__W-2__door1__Hinge_Layer0": MeshMetadata(
                    name="Hinge", path="ROOT / W-2 / door1 / Hinge",
                ),
            },
            glb_primitives=[hinge],
            gltf_materials=[{"pbrMetallicRoughness": {"baseColorFactor": [0.5, 0.5, 0.5, 1.0]}}],
        )
        ifc_text = to_ifc(scene)

        assert ifc_text.count("IFCELEMENTASSEMBLY(") == 2
        w2_line = next(
            line for line in ifc_text.splitlines()
            if "IFCELEMENTASSEMBLY(" in line and "'W-2'" in line
        )
        door_line = next(
            line for line in ifc_text.splitlines()
            if "IFCELEMENTASSEMBLY(" in line and "'door1'" in line
        )
        w2_id = w2_line.split("=")[0].lstrip("#")
        door_id = door_line.split("=")[0].lstrip("#")

        # door1 is aggregated INTO W-2...
        rel_line = next(
            line for line in ifc_text.splitlines()
            if "IFCRELAGGREGATES(" in line and f",#{w2_id},(" in line
        )
        assert f"#{door_id}" in rel_line

        # ...and only W-2 (not door1) is contained in the storey.
        contain_line = next(
            line for line in ifc_text.splitlines() if "IFCRELCONTAINEDINSPATIALSTRUCTURE(" in line
        )
        assert f"#{w2_id}" in contain_line
        assert f"#{door_id}" not in contain_line

    def test_to_ifc_assembly_predefined_type_matches_truss_keyword(self):
        prim = GlbPrimitive(
            positions=array("f", [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0]),
            normals=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            uvs=array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),
            indices=array("I", [0, 1, 2]),
            material_index=0,
            geom_name="mesh_0_ROOT__RT-2__Profile_Layer0",
        )
        scene_hierarchy = InstanceNode(
            name="ROOT",
            path="ROOT",
            children=[
                InstanceNode(
                    name="RT-2 truss",
                    path="ROOT / RT-2 truss",
                    children=[
                        InstanceNode(name="Profile", path="ROOT / RT-2 truss / Profile"),
                    ],
                ),
            ],
        )
        scene = Scene(
            scene_hierarchy=scene_hierarchy,
            mesh_index={
                "mesh_0_ROOT__RT-2__Profile_Layer0": MeshMetadata(
                    name="Profile", path="ROOT / RT-2 truss / Profile",
                ),
            },
            glb_primitives=[prim],
            gltf_materials=[{"pbrMetallicRoughness": {"baseColorFactor": [0.5, 0.5, 0.5, 1.0]}}],
        )
        ifc_text = to_ifc(scene)
        assembly_line = next(
            line for line in ifc_text.splitlines() if "IFCELEMENTASSEMBLY(" in line
        )
        assert assembly_line.rstrip(";").endswith(".TRUSS.)")

    def test_export_file(self):
        scene = create_mock_scene()
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = pathlib.Path(tmp_dir) / "test.ifc"
            export(scene, out_path)
            assert out_path.exists()
            content = out_path.read_text(encoding="utf-8")
            assert "ISO-10303-21;" in content
            assert "IFCWALL" in content
