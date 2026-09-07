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

    def test_export_file(self):
        scene = create_mock_scene()
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = pathlib.Path(tmp_dir) / "test.ifc"
            export(scene, out_path)
            assert out_path.exists()
            content = out_path.read_text(encoding="utf-8")
            assert "ISO-10303-21;" in content
            assert "IFCWALL" in content
