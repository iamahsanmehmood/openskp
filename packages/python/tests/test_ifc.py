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

    def test_export_file(self):
        scene = create_mock_scene()
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = pathlib.Path(tmp_dir) / "test.ifc"
            export(scene, out_path)
            assert out_path.exists()
            content = out_path.read_text(encoding="utf-8")
            assert "ISO-10303-21;" in content
            assert "IFCWALL" in content
