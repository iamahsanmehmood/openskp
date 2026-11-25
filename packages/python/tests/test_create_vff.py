"""Round-trip tests for the core VFF (2021+) writer (:mod:`openskp.create_vff`).

The spike model is a 10-inch box (six faces sharing eight vertices and twelve
edges), one solid-colour material on four faces, one tag on every face and
edge, and one component definition holding its own box, placed twice (one
plain translation, one translated and rotated 45 degrees about Z).  Every
writer test re-parses the produced bytes with openskp's public reader; the
fixtures it reuses are genuine SketchUp saves:

* ``blank_vff.skp`` - the bundled empty SketchUp 2025 document (one default
  tag, no geometry) used as the template.
* ``nesting_2021.skp`` - an unrelated SketchUp-saved 2021 model whose root
  already holds a group and whose file already has definitions, tags and
  materials; extending it proves there are no template-specific offsets.
* ``SU_File.skp`` / ``Untitled.skp`` - unmodified SketchUp 2025 saves shipped
  with the suite, used to check the integrity word against files SketchUp
  itself wrote.
* ``genuine_count_*.skp`` - SketchUp saves with 7, 9, 11, 13 and 15 archive
  entries, used to check the archive history constant.
* ``written_spike.skp`` - the stage-1 spike this writer produced, opened and
  accepted by SketchUp 2025 (kept as a parse/round-trip fixture).
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

import pytest

from openskp import SkpFile
from openskp._vff_writer.archive import (
    _crc_register,
    _history_constant,
    read_zip,
)
from openskp._vff_writer.tlv import parse_records
from openskp.create import SkpWriteError
from openskp.create_vff import (
    DEFAULT_TEMPLATE,
    VffBuilder,
    check_integrity,
    compute_integrity_word,
    create as create_vff,
    read_stored_integrity_word,
)

FIXTURES = Path(__file__).parent / "fixtures"
NESTING_TEMPLATE = FIXTURES / "nesting_2021.skp"
WRITER_FIXTURES = FIXTURES / "vff_writer"

BOX_POINTS = [
    (0.0, 0.0, 0.0),
    (10.0, 0.0, 0.0),
    (10.0, 10.0, 0.0),
    (0.0, 10.0, 0.0),
    (0.0, 0.0, 10.0),
    (10.0, 0.0, 10.0),
    (10.0, 10.0, 10.0),
    (0.0, 10.0, 10.0),
]

BOX_FACES = [
    (0, 3, 2, 1),
    (4, 5, 6, 7),
    (0, 1, 5, 4),
    (1, 2, 6, 5),
    (2, 3, 7, 6),
    (3, 0, 4, 7),
]

BOX_NORMALS = {
    (0.0, 0.0, -1.0),
    (0.0, 0.0, 1.0),
    (0.0, -1.0, 0.0),
    (1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (-1.0, 0.0, 0.0),
}

RED = (220, 30, 40)
TAG_COLOR = (0, 153, 135)


class Spike:
    """The spike model as written, plus the handles the assertions compare to."""

    def __init__(self, data, material_id, layer_id, definition_id):
        self.data = data
        self.material_id = material_id
        self.layer_id = layer_id
        self.definition_id = definition_id


def build_spike(template=None) -> Spike:
    builder = VffBuilder(template)
    material = builder.add_material("SpikeRed", RED)
    tag = builder.add_layer("SpikeTag", TAG_COLOR)
    for index, quad in enumerate(BOX_FACES):
        builder.add_face(
            [BOX_POINTS[i] for i in quad],
            material=material if index < 4 else None,
            layer=tag,
        )
    with builder.add_component_definition("SpikeBox") as definition:
        for index, quad in enumerate(BOX_FACES):
            definition.add_face(
                [BOX_POINTS[i] for i in quad],
                material=material if index < 2 else None,
                layer=tag,
            )
    builder.add_instance(definition, translation=(20.0, 0.0, 0.0), layer=tag)
    builder.add_instance(
        definition,
        translation=(0.0, 30.0, 0.0),
        rotation=((0.0, 0.0, 1.0), math.pi / 4.0),
        layer=tag,
    )
    return Spike(builder.to_bytes(), material, tag, definition.id)


def parse_bytes(tmp_path: Path, data: bytes):
    path = tmp_path / "written.skp"
    path.write_bytes(data)
    return SkpFile.open(str(path)).parse()


def _meta_guid(data: bytes) -> bytes:
    entries = read_zip(data, data.find(b"PK\x03\x04"))
    meta = next(entry.data for entry in entries if entry.name == "meta/meta.dat")
    root = parse_records(meta, {0x0064})[0]
    return root.child(0x0066).payload


def _normals(faces):
    return {tuple(round(v, 6) for v in face.normal) for face in faces.values()}


def _assert_box_geometry(definition):
    assert sorted((v.x, v.y, v.z) for v in definition.vertices.values()) == sorted(BOX_POINTS)
    assert len(definition.edges) == 12
    assert _normals(definition.faces) == BOX_NORMALS
    for face in definition.faces.values():
        assert len(face.loops) == 1
        assert len(face.loops[0]) == 4


def _expected_instances():
    c = math.cos(math.pi / 4.0)
    s = math.sin(math.pi / 4.0)
    return [
        [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 20.0, 0.0, 0.0, 1.0],
        [c, -s, 0.0, s, c, 0.0, 0.0, 0.0, 1.0, 0.0, 30.0, 0.0, 1.0],
    ]


def _solve_history_constant(data: bytes):
    """Recover the unknown meta slot from a genuine file's stored word."""
    offset = data.find(b"PK\x03\x04")
    entries = read_zip(data, offset)
    meta = next(entry.data for entry in entries if entry.name == "meta/meta.dat")
    meta_index = next(
        i for i, entry in enumerate(entries) if entry.name == "meta/meta.dat"
    )
    count = len(entries)
    halves = (count + 1) // 2
    crcs = [zlib.crc32(entry.data) & 0xFFFFFFFF for entry in entries]
    words = [
        crc if index % 2 == 0 else (crc - 42) & 0xFFFFFFFF
        for index, crc in enumerate(crcs)
    ]

    def word_for(slot: int) -> int:
        candidate = list(words)
        candidate[meta_index] = slot
        table = b"".join(
            struct.pack("<I", value)
            for value in candidate[0::2] + candidate[1::2]
        )
        return zlib.crc32(meta[:-4] + table) & 0xFFFFFFFF

    base = word_for(0)
    target = base ^ read_stored_integrity_word(data)
    deltas = [word_for(1 << i) ^ base for i in range(32)]
    rows = [
        [
            sum(((deltas[i] >> row) & 1) << i for i in range(32)),
            (target >> row) & 1,
        ]
        for row in range(32)
    ]
    pivots = []
    current = 0
    for column in range(31, -1, -1):
        selected = next(
            (i for i in range(current, len(rows)) if (rows[i][0] >> column) & 1),
            None,
        )
        if selected is None:
            continue
        rows[current], rows[selected] = rows[selected], rows[current]
        for index in range(len(rows)):
            if index != current and ((rows[index][0] >> column) & 1):
                rows[index][0] ^= rows[current][0]
                rows[index][1] ^= rows[current][1]
        pivots.append((column, current))
        current += 1
    for mask, rhs in rows[current:]:
        assert mask == 0 and rhs == 0
    slot = sum(1 << column for column, row in pivots if rows[row][1])
    return halves, slot ^ _crc_register(meta[-4:])


def test_integrity_word_matches_sketchup_saved_files():
    for path in (FIXTURES / "SU_File.skp", FIXTURES / "Untitled.skp"):
        data = path.read_bytes()
        assert read_stored_integrity_word(data) == compute_integrity_word(data)
        assert check_integrity(data)


@pytest.mark.parametrize(
    "path",
    sorted(WRITER_FIXTURES.glob("genuine_count_*.skp")),
    ids=lambda path: path.name,
)
def test_history_constant_recovered_from_genuine_saves(path):
    data = path.read_bytes()
    assert check_integrity(data)
    k, solved = _solve_history_constant(data)
    assert solved == _history_constant(k)


def test_history_constant_covers_a_range_of_entry_counts():
    covered = set()
    for path in WRITER_FIXTURES.glob("genuine_count_*.skp"):
        k, _solved = _solve_history_constant(path.read_bytes())
        covered.add(k)
    assert {4, 5, 6, 7, 8} <= covered


def test_a_new_builder_round_trips_the_template(tmp_path):
    builder = create_vff()
    data = builder.to_bytes()
    assert check_integrity(data)
    header = data[: data.find(b"PK\x03\x04")]
    revision_offset = header.find(b"VFF\x08\x00\x01\x00") + 7
    assert struct.unpack_from("<H", data, revision_offset)[0] == 17
    names = [entry.name for entry in read_zip(data, data.find(b"PK\x03\x04"))]
    assert names[-1] == "model.dat"
    assert names[2] == "meta/meta.dat"
    # meta.dat gets a fresh model GUID on every write, as SketchUp does.
    assert _meta_guid(data) != _meta_guid(DEFAULT_TEMPLATE.read_bytes())
    model = parse_bytes(tmp_path, data)
    assert model.version == "{25.0.571}"
    assert not model.root.faces
    assert not model.definitions


def test_faces_support_holes_and_shared_edges(tmp_path):
    builder = create_vff()
    builder.add_face(
        [(0, 0, 0), (20, 0, 0), (20, 20, 0), (0, 20, 0)],
        holes=[[(5, 5, 0), (15, 5, 0), (15, 15, 0), (5, 15, 0)]],
    )
    model = parse_bytes(tmp_path, builder.to_bytes())
    (face,) = model.root.faces.values()
    assert len(face.loops) == 2


def test_faces_honour_edge_flags(tmp_path):
    builder = create_vff()
    builder.add_face(
        [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)],
        soft_edges=True,
        smooth_edges=True,
    )
    model = parse_bytes(tmp_path, builder.to_bytes())
    edges = list(model.root.edges.values())
    assert len(edges) == 4
    assert all(edge.soft and edge.smooth for edge in edges)


def test_auto_triangulate_splits_a_non_planar_face(tmp_path):
    builder = create_vff()
    builder.add_face(
        [(0, 0, 0), (10, 0, 0), (10, 10, 1), (0, 10, 0)],
        auto_triangulate=True,
    )
    model = parse_bytes(tmp_path, builder.to_bytes())
    assert len(model.root.faces) == 2
    assert all(len(face.loops[0]) == 3 for face in model.root.faces.values())


def test_face_errors_are_skpwriteerrors():
    with pytest.raises(SkpWriteError):
        create_vff().add_face([(0, 0, 0), (1, 0, 0)])
    with pytest.raises(SkpWriteError):
        create_vff().add_face([(0, 0, 0), (1, 0, 0), (2, 0, 0)])
    with pytest.raises(SkpWriteError):
        create_vff().add_face([(0, 0, 0), (10, 0, 0), (10, 10, 1), (0, 10, 0)])
    with pytest.raises(SkpWriteError):
        create_vff().add_face(
            [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)],
            material=123456,
        )
    with pytest.raises(SkpWriteError):
        create_vff().add_face(
            [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)],
            layer=123456,
        )


def test_materials_and_tags_are_shared_handles(tmp_path):
    builder = create_vff()
    red = builder.add_material("Red", (255, 0, 0))
    assert builder.add_material("Red", (0, 255, 0)) == red
    tag = builder.add_layer("Roof", (10, 20, 30), hidden=True)
    assert builder.add_layer("Roof") == tag
    assert builder.materials_by_name == {"Red": red}
    assert builder.layers_by_name["Roof"] == tag
    builder.add_face(
        [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)],
        material=red,
        layer=tag,
    )
    model = parse_bytes(tmp_path, builder.to_bytes())
    assert [material.name for material in model.materials] == ["Red"]
    tag_read = next(layer for layer in model.layers if layer.name == "Roof")
    assert (tag_read.color_r, tag_read.color_g, tag_read.color_b) == (10, 20, 30)
    assert tag_read.hidden



def test_material_and_tag_names_with_quotes_round_trip(tmp_path):
    builder = create_vff()
    material = builder.add_material('1/2" plywood', (200, 150, 100))
    tag = builder.add_layer('1/2" tag')
    builder.add_face(
        [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)],
        material=material,
        layer=tag,
    )
    model = parse_bytes(tmp_path, builder.to_bytes())
    assert [item.name for item in model.materials] == ['1/2" plywood']
    assert '1/2" tag' in {layer.name for layer in model.layers}
    # The archive uses a sanitised folder, never a path inside another folder.
    names = [entry.name for entry in read_zip(builder.to_bytes(), builder.to_bytes().find(b"PK\x03\x04"))]
    assert any(name.startswith("materials/1_2_ plywood/") for name in names), names


def test_material_and_tag_names_reject_xml_control_characters():
    with pytest.raises(SkpWriteError):
        create_vff().add_material("bad\x01", (1, 2, 3))
    with pytest.raises(SkpWriteError):
        create_vff().add_layer("bad\x02")

def test_material_errors_are_skpwriteerrors():
    with pytest.raises(SkpWriteError):
        create_vff().add_material("bad", (300, 0, 0))
    with pytest.raises(SkpWriteError):
        create_vff().add_material("bad", (1, 2), opacity=0.5)
    with pytest.raises(SkpWriteError):
        create_vff().add_layer("bad", (1, 2, 300))


def test_definition_flags_and_explicit_guid(tmp_path):
    guid = "01234567-89AB-CDEF-0123-456789ABCDEF"
    builder = create_vff()
    with builder.add_component_definition(
        "Cam", always_faces_camera=True, shadows_face_sun=True, guid=guid
    ) as definition:
        definition.add_face([(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)])
    builder.add_instance(definition)
    model = parse_bytes(tmp_path, builder.to_bytes())
    cam = next(d for d in model.definitions.values() if d.name == "Cam")
    assert cam.guid == guid.replace("-", "").upper()
    assert cam.always_faces_camera
    assert cam.shadows_face_sun



def test_unclosed_definitions_and_groups_raise():
    square = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)]

    builder = create_vff()
    definition = builder.add_component_definition("Open")
    definition.add_face(square)
    with pytest.raises(SkpWriteError):
        builder.to_bytes()

    builder = create_vff()
    group = builder.add_group("OpenGroup")
    group.add_face(square)
    with pytest.raises(SkpWriteError):
        builder.to_bytes()

    builder = create_vff()
    definition = builder.add_component_definition("Outer")
    group = definition.add_group("Inner")
    group.add_face(square)
    with pytest.raises(SkpWriteError):
        definition.close()

def test_definition_lifecycle_errors():
    builder = create_vff()
    definition = builder.add_component_definition("D")
    with pytest.raises(SkpWriteError):
        builder.add_instance(definition)
    definition.close()
    with pytest.raises(SkpWriteError):
        definition.add_face([(0, 0, 0), (1, 0, 0), (1, 1, 0)])
    with pytest.raises(SkpWriteError):
        builder.add_instance(other := create_vff().add_component_definition("Other"))
    other.close()
    with pytest.raises(SkpWriteError):
        builder.add_instance(other)


def test_sketchup_accepted_spike_parses(tmp_path):
    """The stage-1 file SketchUp 2025 opened and re-saved stays readable."""
    data = (WRITER_FIXTURES / "written_spike.skp").read_bytes()
    assert check_integrity(data)
    model = parse_bytes(tmp_path, data)
    assert len(model.root.faces) == 6
    assert len(model.root.edges) == 12
    _assert_box_geometry(model.root)
    (definition,) = model.definitions.values()
    assert definition.name == "SpikeBox"
    assert len(definition.faces) == 6
    assert len(model.root.instances) == 2
    assert all(instance.ref_idx == definition.id for instance in model.root.instances)
    assert {instance.layer for instance in model.root.instances} == {"SpikeTag"}
    assert [(m.name, tuple(m.color[:3])) for m in model.materials] == [("SpikeRed", RED)]


def test_spike_on_the_bundled_2025_template(tmp_path):
    spike = build_spike()
    model = parse_bytes(tmp_path, spike.data)
    root = model.root

    assert check_integrity(spike.data)
    assert read_stored_integrity_word(spike.data) == compute_integrity_word(spike.data)

    _assert_box_geometry(root)
    assert len(root.faces) == 6
    assert len(root.edges) == 12

    (material,) = model.materials
    assert material.name == "SpikeRed"
    assert material.color[:3] == RED
    assert material.id == spike.material_id
    material_ids = sorted(
        face.material_id for face in root.faces.values() if face.material_id is not None
    )
    assert material_ids == [spike.material_id] * 4
    assert sum(1 for face in root.faces.values() if face.material_id is None) == 2

    tags = {layer.name: layer for layer in model.layers}
    assert (tags["SpikeTag"].color_r, tags["SpikeTag"].color_g, tags["SpikeTag"].color_b) == TAG_COLOR
    assert {face.layer for face in root.faces.values()} == {spike.layer_id}
    assert {edge.layer for edge in root.edges.values()} == {spike.layer_id}

    assert len(model.definitions) == 1
    definition = model.definitions[spike.definition_id]
    assert definition.name == "SpikeBox"
    _assert_box_geometry(definition)
    painted = [face.material_id for face in definition.faces.values()]
    assert painted.count(spike.material_id) == 2

    assert len(root.instances) == 2
    for instance, expected in zip(root.instances, _expected_instances()):
        assert instance.ref_idx == spike.definition_id
        assert instance.layer == "SpikeTag"
        assert len(instance.matrix) == 13
        for actual, wanted in zip(instance.matrix, expected):
            assert actual == pytest.approx(wanted, abs=1e-12)


def test_spike_on_an_existing_2021_model_template(tmp_path):
    spike = build_spike(NESTING_TEMPLATE)
    assert check_integrity(spike.data)
    model = parse_bytes(tmp_path, spike.data)
    root = model.root
    assert len(root.faces) == 6
    assert len(root.edges) == 12
    _assert_box_geometry(root)
    assert len(root.instances) == 3
    ours = [i for i in root.instances if i.ref_idx == spike.definition_id]
    assert len(ours) == 2
    for instance, expected in zip(ours, _expected_instances()):
        assert instance.layer == "SpikeTag"
        for actual, wanted in zip(instance.matrix, expected):
            assert actual == pytest.approx(wanted, abs=1e-12)
    names = {definition.name for definition in model.definitions.values()}
    assert {"Group", "Shelf", "Group2", "SpikeBox"} <= names
    spike_box = model.definitions[spike.definition_id]
    assert spike_box.name == "SpikeBox"
    _assert_box_geometry(spike_box)
    assert any(
        material.name == "SpikeRed" and material.color[:3] == RED
        for material in model.materials
    )
    tags = {layer.name: layer for layer in model.layers}
    assert (tags["SpikeTag"].color_r, tags["SpikeTag"].color_g, tags["SpikeTag"].color_b) == TAG_COLOR
    assert {face.layer for face in root.faces.values()} == {spike.layer_id}


def test_groups_and_nested_placements(tmp_path):
    builder = create_vff()
    red = builder.add_material("GroupRed", (200, 20, 20))
    tag = builder.add_layer("GroupTag", (200, 120, 40))
    with builder.add_group("RootBox", material=red, layer=tag) as group:
        group.add_face([(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)])
    with builder.add_component_definition("Outer") as outer:
        with outer.add_group("InnerBox", material=red, layer=tag) as inner:
            inner.add_face([(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)])
    builder.add_instance(outer, translation=(0.0, 40.0, 0.0), layer=tag)
    builder.add_instance(
        outer,
        translation=(40.0, 40.0, 0.0),
        rotation=((0.0, 0.0, 1.0), math.pi / 6.0),
        layer=tag,
    )
    model = parse_bytes(tmp_path, builder.to_bytes())
    root = model.root
    group_instance = next(i for i in root.instances if i.name == "RootBox")
    group_definition = model.definitions[group_instance.ref_idx]
    assert group_definition.is_group
    assert group_definition.name == "RootBox"
    assert len(group_definition.faces) == 1
    assert group_instance.layer == "GroupTag"
    assert group_instance.material_id == red

    outer_definition = next(d for d in model.definitions.values() if d.name == "Outer")
    assert not outer_definition.is_group
    (nested,) = outer_definition.instances
    assert nested.name == "InnerBox"
    nested_definition = model.definitions[nested.ref_idx]
    assert nested_definition.is_group
    assert nested.layer == "GroupTag"
    assert nested.material_id == red
    outer_instances = [i for i in root.instances if i.ref_idx == outer_definition.id]
    assert len(outer_instances) == 2
    plain, rotated = sorted(outer_instances, key=lambda i: i.matrix[9])
    assert plain.matrix[9:12] == [0.0, 40.0, 0.0]
    c = math.cos(math.pi / 6.0)
    s = math.sin(math.pi / 6.0)
    assert rotated.matrix[:9] == pytest.approx([c, -s, 0.0, s, c, 0.0, 0.0, 0.0, 1.0], abs=1e-12)
    assert rotated.matrix[9:12] == [40.0, 40.0, 0.0]


