"""Tests for openskp.export.fragments - the direct
InstancedScene -> ThatOpen Fragments (.frag) exporter.

Real-loader verification (the strongest evidence, not automatable in CI):
output from this module has been confirmed, outside this test suite, to
load and render correctly in the actual unmodified @thatopen/fragments
npm package - including genuine instance deduplication (one shared Shell
referenced by two placements) and a real rotation - both programmatically
(SingleThreadedFragmentsModel) and visually in a browser. These tests
instead verify the module's own output using the SAME vendored FlatBuffers
bindings it writes with (round-tripping through our own reader), plus the
scale/mirror rejection behavior that has no equivalent in the real
importer to compare against.
"""
import json
import math
import pathlib
import zlib
from array import array

import pytest

from openskp.export import fragments
from openskp.instanced_scene import InstancedMeshResource, InstancedNode, InstancedScene, LocalPrimitive
from openskp._fragments_fb.Model import Model

IDENTITY = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)


def _rotate_z_matrix(radians, translation=(0.0, 0.0, 0.0)):
    c, s = math.cos(radians), math.sin(radians)
    return (
        c, s, 0.0, 0.0,
        -s, c, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        translation[0], translation[1], translation[2], 1.0,
    )


def _box_primitive(material_index=0):
    """A single 1x1x1 box (8 verts, 12 triangles - matches how
    build_local_face_groups would triangulate a real 6-quad box)."""
    positions = array("f", [
        0, 0, 0,  1, 0, 0,  1, 1, 0,  0, 1, 0,
        0, 0, 1,  1, 0, 1,  1, 1, 1,  0, 1, 1,
    ])
    normals = array("f", [0.0] * 24)
    uvs = array("f", [0.0] * 16)
    indices = array("I", [
        0, 1, 2, 0, 2, 3,  # bottom
        4, 6, 5, 4, 7, 6,  # top
        0, 4, 5, 0, 5, 1,  # side
        1, 5, 6, 1, 6, 2,  # side
        2, 6, 7, 2, 7, 3,  # side
        3, 7, 4, 3, 4, 0,  # side
    ])
    return LocalPrimitive(positions=positions, normals=normals, uvs=uvs, indices=indices, material_index=material_index)


def _make_two_instance_scene(second_matrix=None):
    """One shared box definition, placed twice: once at the origin,
    once via `second_matrix` (defaults to a 45-degree rotation + a 5m
    translation along X)."""
    if second_matrix is None:
        second_matrix = _rotate_z_matrix(math.radians(45), (5.0, 0.0, 0.0))

    resource = InstancedMeshResource(
        id="mesh_0", definition_id=1, definition_name="Box",
        variant_key="1|255,255,255", primitives=[_box_primitive()],
    )
    node_a = InstancedNode(name="Box_A", layer="Framing", matrix=IDENTITY, mesh_resource_id="mesh_0")
    node_b = InstancedNode(name="Box_B", layer="Framing", matrix=second_matrix, mesh_resource_id="mesh_0")
    root = InstancedNode(name="ROOT", matrix=IDENTITY, children=[node_a, node_b])

    return InstancedScene(
        bounds=None,
        scene_hierarchy=root,
        mesh_resources=[resource],
        gltf_materials=[{"pbrMetallicRoughness": {"baseColorFactor": [1.0, 1.0, 1.0, 1.0]}}],
        textures=[],
    )


class TestToFragments:
    def test_produces_a_loadable_model_with_correct_item_count(self):
        scene = _make_two_instance_scene()
        data = fragments.to_fragments(scene, raw=True)

        model = Model.GetRootAsModel(bytearray(data), 0)
        assert model.LocalIdsLength() == 2
        assert model.CategoriesLength() == 2
        assert model.Categories(0) == b"Framing"
        assert model.Categories(1) == b"Framing"

    def test_deduplicates_shared_geometry_across_instances(self):
        """The entire point of Fragments over IFC here: two placements of
        the SAME definition must share one Shell/Material/Representation,
        not get two independent copies."""
        scene = _make_two_instance_scene()
        data = fragments.to_fragments(scene, raw=True)

        model = Model.GetRootAsModel(bytearray(data), 0)
        meshes = model.Meshes()
        assert meshes.ShellsLength() == 1
        assert meshes.MaterialsLength() == 1
        assert meshes.RepresentationsLength() == 1
        # ...but two placements, each its own sample.
        assert meshes.SamplesLength() == 2

    def test_global_transforms_reflect_each_instance_placement(self):
        scene = _make_two_instance_scene()
        data = fragments.to_fragments(scene, raw=True)

        model = Model.GetRootAsModel(bytearray(data), 0)
        meshes = model.Meshes()
        assert meshes.GlobalTransformsLength() == 2

        from openskp._fragments_fb.DoubleVector import DoubleVector
        from openskp._fragments_fb.FloatVector import FloatVector

        t0 = meshes.GlobalTransforms(0)
        p0 = t0.Position(DoubleVector())
        assert (p0.X(), p0.Y(), p0.Z()) == (0.0, 0.0, 0.0)

        t1 = meshes.GlobalTransforms(1)
        p1 = t1.Position(DoubleVector())
        assert p1.X() == pytest.approx(5.0)

        x1 = t1.XDirection(FloatVector())
        assert x1.X() == pytest.approx(math.cos(math.radians(45)), abs=1e-5)
        assert x1.Y() == pytest.approx(math.sin(math.radians(45)), abs=1e-5)


class TestToFragmentsGuids:
    """Model.guids/guids_items were previously written as empty vectors
    unconditionally - geometry still rendered fine (nothing about drawing a
    mesh needs a GUID), but any consumer that zips model.getGuids() against
    model.getLocalIds() index-for-index (the real IfcImporter's own output
    shape, and what a viewer's own id-bridge typically does to resolve
    "what did I click on") silently got an empty map, breaking every
    GUID-keyed interaction: selection lookups, context menus, hide-by-id."""

    def test_guids_and_local_ids_are_the_same_length_and_order(self):
        scene = _make_two_instance_scene()
        data = fragments.to_fragments(scene, raw=True)
        model = Model.GetRootAsModel(bytearray(data), 0)

        n = model.LocalIdsLength()
        assert n == 2
        assert model.GuidsLength() == n
        assert model.GuidsItemsLength() == n
        # guids_items[i] names which local_id guids[i] belongs to - a real
        # consumer's simpler zip (getGuids() against getLocalIds(),
        # index-for-index) only works when this parity actually holds.
        for i in range(n):
            assert model.GuidsItems(i) == model.LocalIds(i)

    def test_items_without_a_source_guid_get_a_unique_synthetic_one(self):
        # _make_two_instance_scene's nodes don't set InstancedNode.guid -
        # the common case for legacy (pre-2021) SKP files, which don't
        # currently expose a per-instance GUID at parse time.
        scene = _make_two_instance_scene()
        data = fragments.to_fragments(scene, raw=True)
        model = Model.GetRootAsModel(bytearray(data), 0)

        guids = [model.Guids(i).decode() for i in range(model.GuidsLength())]
        assert all(g for g in guids)  # never empty
        assert len(set(guids)) == len(guids)  # never duplicated

    def test_a_real_source_guid_is_preserved_exactly(self):
        resource = InstancedMeshResource(
            id="mesh_0", definition_id=1, definition_name="Box",
            variant_key="1|255,255,255", primitives=[_box_primitive()],
        )
        real_guid = "F160C36229782F47A9857FC88DD1F2CB"
        node = InstancedNode(name="Box", layer="Framing", matrix=IDENTITY, mesh_resource_id="mesh_0", guid=real_guid)
        root = InstancedNode(name="ROOT", matrix=IDENTITY, children=[node])
        scene = InstancedScene(
            bounds=None, scene_hierarchy=root, mesh_resources=[resource],
            gltf_materials=[{"pbrMetallicRoughness": {"baseColorFactor": [1.0, 1.0, 1.0, 1.0]}}],
            textures=[],
        )
        data = fragments.to_fragments(scene, raw=True)
        model = Model.GetRootAsModel(bytearray(data), 0)

        assert model.GuidsLength() == 1
        assert model.Guids(0).decode() == real_guid

    def test_a_duplicated_source_guid_does_not_collide(self):
        """openskp#290: SketchUp's own native Copy/Move+Copy/Array tools
        carry an instance's attribute dictionaries - and whatever GUID a
        framing plugin wrote into one - to every copy verbatim, so a real
        file can have several DIFFERENT physical instances all sharing the
        exact same non-empty InstancedNode.guid. The first instance to
        claim a real GUID keeps it; every later instance sharing that same
        value must fall back to a synthetic one instead of silently
        colliding - a collision breaks any GUID-keyed lookup exactly like
        a missing GUID would (see test_items_without_a_source_guid_get_a_
        unique_synthetic_one above), just with real, non-obviously-wrong-
        looking values instead of an empty string."""
        resource = InstancedMeshResource(
            id="mesh_0", definition_id=1, definition_name="Truss",
            variant_key="1|255,255,255", primitives=[_box_primitive()],
        )
        duplicated_guid = "F160C36229782F47A9857FC88DD1F2CB"
        nodes = [
            InstancedNode(
                name=f"Truss{i}", layer="Framing", matrix=IDENTITY,
                mesh_resource_id="mesh_0", guid=duplicated_guid,
            )
            for i in range(3)
        ]
        root = InstancedNode(name="ROOT", matrix=IDENTITY, children=nodes)
        scene = InstancedScene(
            bounds=None, scene_hierarchy=root, mesh_resources=[resource],
            gltf_materials=[{"pbrMetallicRoughness": {"baseColorFactor": [1.0, 1.0, 1.0, 1.0]}}],
            textures=[],
        )
        data = fragments.to_fragments(scene, raw=True)
        model = Model.GetRootAsModel(bytearray(data), 0)

        assert model.GuidsLength() == 3
        guids = [model.Guids(i).decode() for i in range(3)]
        assert len(set(guids)) == 3  # never duplicated, even though the source was
        assert duplicated_guid in guids  # the first claimant keeps the real value
        assert guids.count(duplicated_guid) == 1  # but only the first one

    def test_shell_geometry_matches_the_source_primitive(self):
        scene = _make_two_instance_scene()
        data = fragments.to_fragments(scene, raw=True)

        model = Model.GetRootAsModel(bytearray(data), 0)
        shell = model.Meshes().Shells(0)
        assert shell.PointsLength() == 8
        assert shell.ProfilesLength() == 12  # 12 triangles

    def test_raw_true_returns_uncompressed_flatbuffers(self):
        scene = _make_two_instance_scene()
        data = fragments.to_fragments(scene, raw=True)
        # A valid FlatBuffers Model buffer with the schema's own file
        # identifier, readable with no decompression step.
        assert Model.ModelBufferHasIdentifier(bytearray(data), 0)

    def test_raw_false_returns_zlib_deflated_bytes_by_default(self):
        scene = _make_two_instance_scene()
        compressed = fragments.to_fragments(scene, raw=False)
        raw = fragments.to_fragments(scene, raw=True)

        assert zlib.decompress(compressed) == raw
        # The real Fragments loader auto-detects raw vs. deflated via
        # exactly this check (a valid zlib/RFC1950 header) - confirmed by
        # reading @thatopen/fragments' own isRawBuffer().
        assert (compressed[0] & 0x0F) == 8
        assert (compressed[0] << 8 | compressed[1]) % 31 == 0

    def test_export_writes_a_file(self, tmp_path: pathlib.Path):
        scene = _make_two_instance_scene()
        out = tmp_path / "model.frag"
        fragments.export(scene, out)
        assert out.exists()
        assert zlib.decompress(out.read_bytes())  # doesn't raise

    def test_multiple_definitions_each_get_their_own_shell(self):
        res_a = InstancedMeshResource(id="mesh_0", definition_id=1, definition_name="A",
                                       variant_key="1|x", primitives=[_box_primitive()])
        res_b = InstancedMeshResource(id="mesh_1", definition_id=2, definition_name="B",
                                       variant_key="2|x", primitives=[_box_primitive()])
        node_a = InstancedNode(name="A_inst", layer="Layer0", matrix=IDENTITY, mesh_resource_id="mesh_0")
        node_b = InstancedNode(name="B_inst", layer="Layer0", matrix=IDENTITY, mesh_resource_id="mesh_1")
        root = InstancedNode(name="ROOT", matrix=IDENTITY, children=[node_a, node_b])
        scene = InstancedScene(
            bounds=None, scene_hierarchy=root, mesh_resources=[res_a, res_b],
            gltf_materials=[{"pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1]}}],
            textures=[],
        )

        data = fragments.to_fragments(scene, raw=True)
        model = Model.GetRootAsModel(bytearray(data), 0)
        assert model.Meshes().ShellsLength() == 2

    def test_big_shell_threshold_switches_index_width(self):
        """A definition with more than 65,535 points must use the wide
        (BigShell) uint-index encoding instead of the default ushort one."""
        from openskp._fragments_fb.ShellType import ShellType

        n_points = 65537
        positions = array("f", [0.0, 0.0, 0.0] * n_points)
        normals = array("f", [0.0] * (n_points * 3))
        uvs = array("f", [0.0] * (n_points * 2))
        indices = array("I", [0, 1, 2] * ((n_points // 3)))
        big_prim = LocalPrimitive(positions=positions, normals=normals, uvs=uvs, indices=indices, material_index=0)

        resource = InstancedMeshResource(id="mesh_0", definition_id=1, definition_name="Huge",
                                          variant_key="1|x", primitives=[big_prim])
        node = InstancedNode(name="inst", layer="Layer0", matrix=IDENTITY, mesh_resource_id="mesh_0")
        root = InstancedNode(name="ROOT", matrix=IDENTITY, children=[node])
        scene = InstancedScene(
            bounds=None, scene_hierarchy=root, mesh_resources=[resource],
            gltf_materials=[{"pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1]}}],
            textures=[],
        )

        data = fragments.to_fragments(scene, raw=True)
        model = Model.GetRootAsModel(bytearray(data), 0)
        shell = model.Meshes().Shells(0)
        assert shell.Type() == ShellType.BIG
        assert shell.BigProfilesLength() > 0
        assert shell.ProfilesLength() == 0


class TestMetadataAndAttributes:
    """Two things Fragments has no dedicated schema field for, both
    recovered through the schema's own general-purpose escape hatches:
    per-layer visibility (via `metadata`'s free-form JSON, since nothing
    in Model/Meshes/SpatialStructure/Attribute represents visibility at
    all - confirmed by reading the real package, where setVisible/
    getVisible are live runtime calls, not persisted state) and each
    item's real display name (via `attributes`' `["Name", value, "STRING"]`
    convention, the exact one the real IfcImporter itself uses)."""

    def test_metadata_carries_the_source_files_layer_hidden_state(self):
        scene = _make_two_instance_scene()
        scene.layer_hidden = {"Framing": False, "Cladding": True}
        data = fragments.to_fragments(scene, raw=True)

        model = Model.GetRootAsModel(bytearray(data), 0)
        metadata = json.loads(model.Metadata())
        assert metadata["layer_hidden"] == {"Framing": False, "Cladding": True}

    def test_metadata_present_even_with_no_hidden_layers(self):
        scene = _make_two_instance_scene()
        data = fragments.to_fragments(scene, raw=True)

        model = Model.GetRootAsModel(bytearray(data), 0)
        metadata = json.loads(model.Metadata())
        assert metadata["layer_hidden"] == {}

    def test_attributes_carry_each_items_real_name(self):
        scene = _make_two_instance_scene()  # nodes named "Box_A" / "Box_B"
        data = fragments.to_fragments(scene, raw=True)

        model = Model.GetRootAsModel(bytearray(data), 0)
        assert model.AttributesLength() == 2

        attr_a = model.Attributes(0)
        assert attr_a.DataLength() == 1
        assert json.loads(attr_a.Data(0)) == ["Name", "Box_A", "STRING"]

        attr_b = model.Attributes(1)
        assert json.loads(attr_b.Data(0)) == ["Name", "Box_B", "STRING"]

    def test_unnamed_item_gets_an_empty_attribute_not_a_missing_one(self):
        resource = InstancedMeshResource(id="mesh_0", definition_id=1, definition_name="Box",
                                          variant_key="1|x", primitives=[_box_primitive()])
        node = InstancedNode(name="", layer="Layer0", matrix=IDENTITY, mesh_resource_id="mesh_0")
        root = InstancedNode(name="ROOT", matrix=IDENTITY, children=[node])
        scene = InstancedScene(
            bounds=None, scene_hierarchy=root, mesh_resources=[resource],
            gltf_materials=[{"pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1]}}],
            textures=[],
        )

        data = fragments.to_fragments(scene, raw=True)
        model = Model.GetRootAsModel(bytearray(data), 0)
        assert model.AttributesLength() == 1
        assert model.Attributes(0).DataLength() == 0


def _diag_matrix(sx, sy, sz, translation=(0.0, 0.0, 0.0)):
    return (
        sx, 0.0, 0.0, 0.0,
        0.0, sy, 0.0, 0.0,
        0.0, 0.0, sz, 0.0,
        translation[0], translation[1], translation[2], 1.0,
    )


def _one_instance_scene(matrix, name="Instance"):
    resource = InstancedMeshResource(id="mesh_0", definition_id=1, definition_name="Box",
                                      variant_key="1|x", primitives=[_box_primitive()])
    node = InstancedNode(name=name, layer="Layer0", matrix=matrix, mesh_resource_id="mesh_0")
    root = InstancedNode(name="ROOT", matrix=IDENTITY, children=[node])
    return InstancedScene(
        bounds=None, scene_hierarchy=root, mesh_resources=[resource],
        gltf_materials=[{"pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1]}}],
        textures=[],
    )


class TestToFragmentsScaleAndMirror:
    """Fragments' Transform struct has no scale field at all (confirmed
    against the real importer's own serialization code: it bakes scale
    into geometry before writing). This module does the same - scaled and
    mirrored instances must produce CORRECT baked geometry, not an error
    and not silently-wrong output."""

    def test_uniform_scale_bakes_into_geometry_not_transform(self):
        from openskp._fragments_fb.FloatVector import FloatVector

        scaled = _diag_matrix(2.0, 2.0, 2.0)
        scene = _one_instance_scene(scaled)
        data = fragments.to_fragments(scene, raw=True)

        model = Model.GetRootAsModel(bytearray(data), 0)
        meshes = model.Meshes()
        # The Transform stays a pure rotation (unit-length directions) -
        # the scale must NOT leak into it.
        x_dir = meshes.GlobalTransforms(0).XDirection(FloatVector())
        assert math.hypot(x_dir.X(), x_dir.Y(), x_dir.Z()) == pytest.approx(1.0)

        shell = meshes.Shells(0)
        pt = shell.Points(1)  # local point (1, 0, 0) in _box_primitive()
        assert (pt.X(), pt.Y(), pt.Z()) == pytest.approx((2.0, 0.0, 0.0))

    def test_non_uniform_scale_bakes_per_axis(self):
        scaled = _diag_matrix(2.0, 1.0, 0.5)
        scene = _one_instance_scene(scaled)
        data = fragments.to_fragments(scene, raw=True)

        model = Model.GetRootAsModel(bytearray(data), 0)
        shell = model.Meshes().Shells(0)
        pt = shell.Points(6)  # local point (1, 1, 1) in _box_primitive()
        assert (pt.X(), pt.Y(), pt.Z()) == pytest.approx((2.0, 1.0, 0.5))

    def test_mirrored_instance_flips_geometry_and_reverses_winding(self):
        mirrored = _diag_matrix(-1.0, 1.0, 1.0)
        scene = _one_instance_scene(mirrored)
        data = fragments.to_fragments(scene, raw=True)

        model = Model.GetRootAsModel(bytearray(data), 0)
        shell = model.Meshes().Shells(0)

        pt = shell.Points(1)  # local (1, 0, 0) -> mirrored X
        assert (pt.X(), pt.Y(), pt.Z()) == pytest.approx((-1.0, 0.0, 0.0))

        # Winding reversed relative to the un-mirrored case: (0,1,2) -> (0,2,1).
        profile = shell.Profiles(0)
        assert [profile.Indices(i) for i in range(3)] == [0, 2, 1]

    def test_plain_rotation_and_translation_still_dedupes_normally(self):
        rotated = _rotate_z_matrix(math.radians(30), (1.0, 2.0, 3.0))
        scene = _one_instance_scene(rotated)
        data = fragments.to_fragments(scene, raw=True)
        model = Model.GetRootAsModel(bytearray(data), 0)
        assert model.Meshes().ShellsLength() == 1

    def test_instances_sharing_the_same_scale_still_dedupe(self):
        """Two DIFFERENT placements, same non-unit scale - must still
        share one baked Shell, matching the real importer's own
        scale-hash caching behavior."""
        resource = InstancedMeshResource(id="mesh_0", definition_id=1, definition_name="Box",
                                          variant_key="1|x", primitives=[_box_primitive()])
        node_a = InstancedNode(name="A", layer="Layer0",
                                matrix=_diag_matrix(2.0, 2.0, 2.0, (0.0, 0.0, 0.0)),
                                mesh_resource_id="mesh_0")
        node_b = InstancedNode(name="B", layer="Layer0",
                                matrix=_diag_matrix(2.0, 2.0, 2.0, (10.0, 0.0, 0.0)),
                                mesh_resource_id="mesh_0")
        root = InstancedNode(name="ROOT", matrix=IDENTITY, children=[node_a, node_b])
        scene = InstancedScene(
            bounds=None, scene_hierarchy=root, mesh_resources=[resource],
            gltf_materials=[{"pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1]}}],
            textures=[],
        )

        data = fragments.to_fragments(scene, raw=True)
        model = Model.GetRootAsModel(bytearray(data), 0)
        assert model.Meshes().ShellsLength() == 1
        assert model.Meshes().SamplesLength() == 2

    def test_instances_with_different_scales_get_separate_shells(self):
        resource = InstancedMeshResource(id="mesh_0", definition_id=1, definition_name="Box",
                                          variant_key="1|x", primitives=[_box_primitive()])
        node_a = InstancedNode(name="A", layer="Layer0", matrix=_diag_matrix(1.0, 1.0, 1.0),
                                mesh_resource_id="mesh_0")
        node_b = InstancedNode(name="B", layer="Layer0", matrix=_diag_matrix(3.0, 3.0, 3.0),
                                mesh_resource_id="mesh_0")
        root = InstancedNode(name="ROOT", matrix=IDENTITY, children=[node_a, node_b])
        scene = InstancedScene(
            bounds=None, scene_hierarchy=root, mesh_resources=[resource],
            gltf_materials=[{"pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1]}}],
            textures=[],
        )

        data = fragments.to_fragments(scene, raw=True)
        model = Model.GetRootAsModel(bytearray(data), 0)
        assert model.Meshes().ShellsLength() == 2


class TestSpatialStructure:
    """The exported spatial_structure must reproduce the source scene's
    real component nesting - a container with no geometry of its own
    still gets a local_id (and therefore a Name/GUID other tools can look
    up) when it has a real name, since that's the only place a wrapper
    like a SketchUp group named "W-2" can attach its own identity; a
    truly generic/unnamed container (name_is_generated=True) gets no
    local_id, same as before this distinction existed. A leaf with
    geometry gets the local_id matching its own sample's item index; a
    node with BOTH its own geometry AND children (a component that has
    faces of its own plus a nested sub-component) gets both."""

    def _nested_scene(self):
        resource = InstancedMeshResource(id="mesh_0", definition_id=1, definition_name="Box",
                                          variant_key="1|x", primitives=[_box_primitive()])
        leaf_a = InstancedNode(name="LeafA", layer="Framing", matrix=IDENTITY, mesh_resource_id="mesh_0")
        leaf_b = InstancedNode(name="LeafB", layer="Framing", matrix=IDENTITY, mesh_resource_id="mesh_0")
        # Named container: no geometry of its own, just wraps LeafB - but
        # has a real name, so it should still become a trackable item.
        inner_group = InstancedNode(name="InnerGroup", layer="Layer0", matrix=IDENTITY,
                                     mesh_resource_id=None, children=[leaf_b])
        # Mixed node: has its OWN geometry AND a nested child.
        mixed = InstancedNode(name="Mixed", layer="Framing", matrix=IDENTITY,
                               mesh_resource_id="mesh_0", children=[inner_group])
        leaf_c = InstancedNode(name="LeafC", layer="Framing", matrix=IDENTITY, mesh_resource_id="mesh_0")
        # Generic container: no geometry, no real name - nobody in
        # SketchUp named this group, so it should stay a plain nesting
        # level with no local_id of its own, exactly like before.
        generic_group = InstancedNode(name="Component_9", name_is_generated=True, layer="Layer0",
                                       matrix=IDENTITY, mesh_resource_id=None, children=[leaf_c])
        root = InstancedNode(name="ROOT", layer="Layer0", matrix=IDENTITY,
                              children=[leaf_a, mixed, generic_group])
        return InstancedScene(
            bounds=None, scene_hierarchy=root, mesh_resources=[resource],
            gltf_materials=[{"pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1]}}],
            textures=[],
        )

    def test_named_container_gets_a_local_id_generic_one_does_not(self):
        scene = self._nested_scene()
        data = fragments.to_fragments(scene, raw=True)
        model = Model.GetRootAsModel(bytearray(data), 0)

        root_node = model.SpatialStructure()
        # root -> [LeafA, Mixed, GenericGroup]
        assert root_node.ChildrenLength() == 3
        assert root_node.LocalId() is None  # ROOT is never itself an item
        mixed_node = root_node.Children(1)
        assert mixed_node.LocalId() is not None  # Mixed has its own geometry
        assert mixed_node.ChildrenLength() == 1

        inner_group_node = mixed_node.Children(0)
        assert inner_group_node.LocalId() is not None  # named container - now a trackable item
        assert inner_group_node.ChildrenLength() == 1

        leaf_b_node = inner_group_node.Children(0)
        assert leaf_b_node.LocalId() is not None

        generic_group_node = root_node.Children(2)
        assert generic_group_node.LocalId() is None  # no real name - stays a plain nesting level
        assert generic_group_node.ChildrenLength() == 1

    def test_leaf_local_ids_match_their_own_sample_item_index(self):
        scene = self._nested_scene()
        data = fragments.to_fragments(scene, raw=True)
        model = Model.GetRootAsModel(bytearray(data), 0)

        root_node = model.SpatialStructure()
        leaf_a_node = root_node.Children(0)
        mixed_node = root_node.Children(1)
        inner_group_node = mixed_node.Children(0)
        leaf_b_node = inner_group_node.Children(0)
        leaf_c_node = root_node.Children(2).Children(0)

        # Pre-order walk assigns item indices as each tracked node (mesh
        # leaf OR named container) is first visited: LeafA, Mixed,
        # InnerGroup (named, no mesh), LeafB, LeafC - GenericGroup itself
        # is skipped (no real name), so LeafC's index follows straight
        # from LeafB's rather than leaving a gap for it.
        assert leaf_a_node.LocalId() == 0
        assert mixed_node.LocalId() == 1
        assert inner_group_node.LocalId() == 2
        assert leaf_b_node.LocalId() == 3
        assert leaf_c_node.LocalId() == 4
        assert model.LocalIdsLength() == 5

    def test_category_reflects_each_nodes_own_layer(self):
        scene = self._nested_scene()
        data = fragments.to_fragments(scene, raw=True)
        model = Model.GetRootAsModel(bytearray(data), 0)

        root_node = model.SpatialStructure()
        assert root_node.Category() == b"Layer0"
        mixed_node = root_node.Children(1)
        assert mixed_node.Category() == b"Framing"
        inner_group_node = mixed_node.Children(0)
        assert inner_group_node.Category() == b"Layer0"


class TestToFragmentsRealScene:
    """End-to-end against a REAL .skp file, built with OpenSKP's own
    writer and read back through the real parser - not just synthetic
    InstancedScene objects."""

    def test_real_written_skp_round_trips_through_the_real_pipeline(self, tmp_path: pathlib.Path) -> None:
        from openskp.create import create
        from openskp.model import SkpFile

        builder = create()
        layer = builder.add_layer("Framing")
        with builder.add_component_definition("Stud") as stud:
            w, d, h = 2.0, 4.0, 12.0
            stud.add_face([(0, 0, 0), (w, 0, 0), (w, d, 0), (0, d, 0)])
            stud.add_face([(0, 0, h), (0, d, h), (w, d, h), (w, 0, h)])
            stud.add_face([(0, 0, 0), (0, d, 0), (0, d, h), (0, 0, h)])
            stud.add_face([(w, 0, 0), (w, 0, h), (w, d, h), (w, d, 0)])
            stud.add_face([(0, 0, 0), (0, 0, h), (w, 0, h), (w, 0, 0)])
            stud.add_face([(0, d, 0), (w, d, 0), (w, d, h), (0, d, h)])
        builder.add_instance(stud, name="Stud_A", translation=(0.0, 0.0, 0.0), layer=layer)
        builder.add_instance(
            stud, name="Stud_B", translation=(120.0, 0.0, 0.0),
            rotation=((0, 0, 1), math.radians(45)), layer=layer,
        )

        skp_path = tmp_path / "studs.skp"
        skp_path.write_bytes(builder.to_bytes())

        scene = SkpFile.open(str(skp_path)).build_instanced_scene()
        assert len(scene.mesh_resources) == 1  # deduplicated, as expected

        data = fragments.to_fragments(scene, raw=True)
        model = Model.GetRootAsModel(bytearray(data), 0)
        assert model.LocalIdsLength() == 2
        assert model.Meshes().ShellsLength() == 1  # still just one shared shape
        assert model.Meshes().SamplesLength() == 2

    def test_real_nested_component_preserves_hierarchy(self, tmp_path: pathlib.Path) -> None:
        """A component that has its OWN geometry AND contains a nested
        sub-component instance - built with the real writer, read back
        through the real parser - must come out with real nesting in
        spatial_structure, not flattened under one root."""
        from openskp.create import create
        from openskp.model import SkpFile

        def _box(target, w, d, h):
            target.add_face([(0, 0, 0), (w, 0, 0), (w, d, 0), (0, d, 0)])
            target.add_face([(0, 0, h), (0, d, h), (w, d, h), (w, 0, h)])
            target.add_face([(0, 0, 0), (0, d, 0), (0, d, h), (0, 0, h)])
            target.add_face([(w, 0, 0), (w, 0, h), (w, d, h), (w, d, 0)])
            target.add_face([(0, 0, 0), (0, 0, h), (w, 0, h), (w, 0, 0)])
            target.add_face([(0, d, 0), (w, d, 0), (w, d, h), (0, d, h)])

        builder = create()
        with builder.add_component_definition("Bracket") as bracket:
            _box(bracket, 1.0, 1.0, 1.0)
        with builder.add_component_definition("Wall") as wall:
            _box(wall, 10.0, 1.0, 8.0)
            wall.add_instance(bracket, name="BracketInstance", translation=(2.0, 0.0, 2.0))
        builder.add_instance(wall, name="WallInstance")

        skp_path = tmp_path / "nested.skp"
        skp_path.write_bytes(builder.to_bytes())

        scene = SkpFile.open(str(skp_path)).build_instanced_scene()
        data = fragments.to_fragments(scene, raw=True)
        model = Model.GetRootAsModel(bytearray(data), 0)

        # ROOT (no geometry) -> WallInstance (own geometry, local_id 0) ->
        # BracketInstance (own geometry, local_id 1), nested as its child -
        # not flattened as two siblings under ROOT.
        root_node = model.SpatialStructure()
        assert root_node.LocalId() is None
        assert root_node.ChildrenLength() == 1

        wall_node = root_node.Children(0)
        assert wall_node.LocalId() == 0
        assert wall_node.ChildrenLength() == 1

        bracket_node = wall_node.Children(0)
        assert bracket_node.LocalId() == 1
        assert bracket_node.ChildrenLength() == 0
