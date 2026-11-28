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

import collections
import io
import math
import random
import re
import struct
import xml.etree.ElementTree as ET
import zipfile
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
from openskp.create import Length, Point3d, SkpWriteError, Timestamp, Vector3d
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



def test_damaged_templates_raise_skpwriteerror():
    blank = DEFAULT_TEMPLATE.read_bytes()
    rng = random.Random(1234)
    for index in range(400):
        data = bytearray(blank)
        if index % 4 == 0:
            data = data[: rng.randrange(1, len(data))]
        else:
            for _ in range(rng.randint(1, 20)):
                data[rng.randrange(len(data))] = rng.randrange(256)
        try:
            create_vff(template=bytes(data)).to_bytes()
        except SkpWriteError:
            pass

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



def test_holes_must_lie_on_the_face_plane():
    square = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)]
    raised_hole = [(2, 2, 1), (4, 2, 1), (4, 4, 1), (2, 4, 1)]
    with pytest.raises(SkpWriteError):
        create_vff().add_face(square, holes=[raised_hole])

    bent = [(0, 0, 0), (10, 0, 0), (10, 10, 3), (0, 10, 0)]
    flat_hole = [(2, 2, 0), (4, 2, 0), (4, 4, 0), (2, 4, 0)]
    with pytest.raises(SkpWriteError):
        create_vff().add_face(bent, auto_triangulate=True, holes=[flat_hole])

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



def _sketchup_folder_map(data: bytes):
    """Map (kind, name) -> archive folder from a SketchUp-saved VFF file."""
    archive = zipfile.ZipFile(io.BytesIO(data))
    found = {}
    for info in archive.infolist():
        if not info.filename.startswith("materials/") or not info.filename.endswith("/material.xml"):
            continue
        folder = info.filename.split("/")[1]
        # SketchUp stores UTF-8 entry names without the ZIP UTF-8 flag, so
        # Python decodes them as cp437; put the original UTF-8 bytes back.
        folder = folder.encode("cp437").decode("utf-8", errors="replace")
        root = ET.fromstring(archive.read(info))
        namespace = "{http://sketchup.google.com/schemas/sketchup/1.0/material}"
        name = root.find(f".//{namespace}material").attrib["name"]
        if name.startswith("Layer_"):
            found[("tag", name[len("Layer_"):])] = folder
        else:
            found[("material", name)] = folder
    return found


def test_material_and_tag_folders_match_sketchup(tmp_path):
    """SketchUp 2025's own folder mapping for special names and collisions.

    ``material_folder_names_2025.skp`` (29 KB) was saved by SketchUp 2025
    from the bundled empty template, after creating these material and tag
    names through the Ruby API and applying each to its own face. The writer
    below is given the same names in the same order, so its archive folders
    must equal the ones SketchUp chose, including the ``a:b`` drive-prefix
    fallback, ``_N`` collisions, the 200-character truncation and the
    ``Layer_Foo`` material/Foo-tag collision.
    """
    material_names = [
        '1/2" plywood', "a/b", "a_b", "a:b", "a*b", "a?b",
        "x" * 200, "Ünïcødé 木材", ".", "Layer_Foo",
    ]
    tag_names = [
        '1/2" tag', "a/b", "a_b", "a:b", "a*b", "a?b",
        "x" * 200, "Ünïcødé 木材", ".", "Foo",
    ]
    expected = _sketchup_folder_map(
        (WRITER_FIXTURES / "material_folder_names_2025.skp").read_bytes()
    )
    builder = create_vff()
    for index, name in enumerate(material_names):
        material = builder.add_material(name, (100, 150, 200))
        builder.add_face(
            [(index * 30, 0, 0), (index * 30 + 10, 0, 0),
             (index * 30 + 10, 10, 0), (index * 30, 10, 0)],
            material=material,
        )
    for index, name in enumerate(tag_names):
        tag = builder.add_layer(name)
        builder.add_face(
            [(index * 30, 200, 0), (index * 30 + 10, 200, 0),
             (index * 30 + 10, 210, 0), (index * 30, 210, 0)],
            layer=tag,
        )
    actual = _sketchup_folder_map(builder.to_bytes())
    assert actual == expected

    model = parse_bytes(tmp_path, builder.to_bytes())
    assert '1/2" plywood' in {material.name for material in model.materials}
    assert '1/2" tag' in {layer.name for layer in model.layers}


def test_material_folder_special_character_table():
    """The folder rule for characters absent from the SketchUp fixture."""
    names = ["p\\q", "r<s>", "t|u", "v?w", "A" * 90, "B" * 120, "C" * 200]
    expected = {
        "p\\q": "p_q",
        "r<s>": "r_s_",
        "t|u": "t_u",
        "v?w": "v_w",
        "A" * 90: "A" * 60,
        "B" * 120: "B" * 53,
        "C" * 200: "C" * 58,
    }
    builder = create_vff()
    for name in names:
        builder.add_material(name, (10, 20, 30))
    actual = {
        name: folder
        for (_kind, name), folder in _sketchup_folder_map(builder.to_bytes()).items()
        if name in expected
    }
    assert actual == expected



def test_template_materials_are_keyed_by_their_material_xml_name(tmp_path):
    square = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)]

    def shifted(dx):
        return [(x + dx, y, z) for x, y, z in square]

    builder = create_vff()
    builder.add_face(shifted(0), material=builder.add_material("a/b", (200, 0, 0)))
    builder.add_face(shifted(20), material=builder.add_material('1/2" ply', (0, 200, 0)))
    template = builder.to_bytes()

    reopened = create_vff(template=template)
    assert "a/b" in reopened.materials_by_name
    assert '1/2" ply' in reopened.materials_by_name
    assert "a_b" not in reopened.materials_by_name
    assert "1_2_ ply" not in reopened.materials_by_name
    a_over_b = reopened.materials_by_name["a/b"]
    assert reopened.add_material("a/b", (200, 0, 0)) == a_over_b
    new_material = reopened.add_material("a_b", (0, 0, 200))
    assert new_material != a_over_b
    reopened.add_face(shifted(40), material=new_material)

    names = collections.Counter(material.name for material in parse_bytes(tmp_path, reopened.to_bytes()).materials)
    for name in ("a/b", '1/2" ply', "a_b"):
        assert names[name] == 1, names

    fixture = create_vff(
        template=(WRITER_FIXTURES / "material_folder_names_2025.skp").read_bytes()
    )
    assert '1/2" plywood' in fixture.materials_by_name
    assert "1_2_ plywood" not in fixture.materials_by_name

def test_case_variant_material_and_tag_names_follow_sketchup(tmp_path):
    builder = create_vff()
    first = builder.add_material("Wood", (10, 20, 30))
    second = builder.add_material("wood", (40, 50, 60))
    assert first != second
    tag = builder.add_layer("Wood", (1, 2, 3))
    assert builder.add_layer("wood", (4, 5, 6)) == tag

    model = parse_bytes(tmp_path, builder.to_bytes())
    assert [material.name for material in model.materials] == ["Wood", "wood1"]
    assert [layer.name for layer in model.layers].count("Wood") == 1
    folders = {
        name: folder
        for (kind, name), folder in _sketchup_folder_map(builder.to_bytes()).items()
        if kind == "material" and name in {"Wood", "wood1"}
    }
    assert folders == {"Wood": "Wood", "wood1": "wood1"}

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




def test_non_finite_coordinates_are_rejected():
    square = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)]
    with pytest.raises(SkpWriteError) as error:
        create_vff().add_face([(0, 0, 0), (math.nan, 0, 0), (1, 1, 0)])
    assert "coplanar" not in str(error.value).lower()

    builder = create_vff()
    with builder.add_component_definition("D") as definition:
        definition.add_face(square)
    with pytest.raises(SkpWriteError):
        builder.add_instance(definition, translation=(math.inf, 0, 0))
    with pytest.raises(SkpWriteError):
        builder.add_instance(
            definition,
            matrix3x3=((math.nan, 0, 0), (0, 1, 0), (0, 0, 1)),
        )
    with pytest.raises(SkpWriteError):
        builder.add_instance(definition, rotation=((0, 0, math.inf), 0.5))

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


def test_duplicate_definition_names_are_renamed(tmp_path):
    square = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)]
    builder = create_vff()
    for _ in range(3):
        with builder.add_component_definition("Same") as definition:
            definition.add_face(square)
    names = {item.name for item in parse_bytes(tmp_path, builder.to_bytes()).definitions.values()}
    assert {"Same", "Same#1", "Same#2"} <= names

    builder = create_vff()
    with builder.add_group("Group") as group:
        group.add_face(square)
    with builder.add_group("Group") as group:
        group.add_face(square)
    names = {item.name for item in parse_bytes(tmp_path, builder.to_bytes()).definitions.values()}
    assert {"Group", "Group#1"} <= names

    builder = create_vff(NESTING_TEMPLATE)
    with builder.add_component_definition("Group") as definition:
        definition.add_face(square)
    names = {item.name for item in parse_bytes(tmp_path, builder.to_bytes()).definitions.values()}
    assert "Group#1" in names


def test_definition_names_compare_without_case(tmp_path):
    square = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)]
    builder = create_vff()
    for _ in range(4):
        with builder.add_component_definition("Same") as definition:
            definition.add_face(square)
    with builder.add_component_definition("same") as definition:
        definition.add_face(square)
    names = {item.name for item in parse_bytes(tmp_path, builder.to_bytes()).definitions.values()}
    assert {"Same", "Same#1", "Same#2", "Same#3", "same#4"} <= names
    assert len({name.lower() for name in names}) == len(names)

    builder = create_vff()
    for name in ("Same", "same"):
        with builder.add_component_definition(name) as definition:
            definition.add_face(square)
    names = [item.name for item in parse_bytes(tmp_path, builder.to_bytes()).definitions.values()]
    assert len({name.lower() for name in names}) == len(names)

    builder = create_vff()
    for name in ("Same", "same"):
        with builder.add_group(name) as group:
            group.add_face(square)
    names = [item.name for item in parse_bytes(tmp_path, builder.to_bytes()).definitions.values()]
    assert names == ["Same", "same#1"], names

    builder = create_vff(template=NESTING_TEMPLATE)
    with builder.add_component_definition("group") as definition:
        definition.add_face(square)
    names = {item.name for item in parse_bytes(tmp_path, builder.to_bytes()).definitions.values()}
    assert "group#1" in names

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



def test_writing_keeps_the_templates_model_revision(tmp_path):
    def revision(data: bytes) -> int:
        offset = data.find(b"VFF\x08\x00\x01\x00")
        return struct.unpack_from("<H", data, offset + 7)[0]

    source = NESTING_TEMPLATE.read_bytes()
    assert revision(create_vff(template=source).to_bytes()) == revision(source)

    older = bytearray(DEFAULT_TEMPLATE.read_bytes())
    offset = older.find(b"VFF\x08\x00\x01\x00")
    struct.pack_into("<H", older, offset + 7, 16)
    assert revision(create_vff(template=bytes(older)).to_bytes()) == 16

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


def _face_dictionaries(data: bytes):
    from openskp._core import (
        CONTAINER_TAGS,
        extract_attribute_dictionaries,
        parse_tlv_recursive,
    )
    from openskp.vff import extract_skp_contents

    model_data = extract_skp_contents(data)["model_data"]
    roots = parse_tlv_recursive(model_data, 0, len(model_data), CONTAINER_TAGS)
    found = []

    def walk(node):
        if node.tag == "AC0D":
            drawing = next(
                (child for child in node.children if child.tag == "D007"), None
            )
            if drawing is not None:
                found.append(extract_attribute_dictionaries(drawing))
            return
        for child in node.children:
            walk(child)

    for root in roots:
        walk(root)
    return found


ATTRIBUTE_VALUES = {
    "string": "hello",
    "int": 42,
    "float": 1.5,
    "bool": True,
    "nothing": None,
    "length": Length(2.0),
    "point": Point3d(1, 2, 3),
    "vector": Vector3d(0, 0, 1),
    "time": Timestamp(1700000000),
    "array": [1, [2, "x"], False, None],
}
ATTRIBUTE_VALUES_EXPECTED = {
    "string": "hello",
    "int": 42,
    "float": 1.5,
    "bool": True,
    "nothing": None,
    "length": 2.0,
    "point": (1.0, 2.0, 3.0),
    "vector": (0.0, 0.0, 1.0),
    "time": 1700000000,
    "array": [1, [2, "x"], False, None],
}
SQUARE = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)]


def _plain(value):
    return list(value) if isinstance(value, tuple) else value


def test_face_attribute_dictionary_encodes_every_value_type():
    builder = create_vff()
    builder.add_face(SQUARE, attributes=ATTRIBUTE_VALUES)
    dictionaries = _face_dictionaries(builder.to_bytes())
    (attributes,) = [entry["attributes"] for entry in dictionaries if "attributes" in entry]
    for key, expected in ATTRIBUTE_VALUES_EXPECTED.items():
        assert _plain(attributes[key]) == _plain(expected), (key, attributes[key], expected)


def test_face_writes_multiple_attribute_dictionaries():
    builder = create_vff()
    builder.add_face(
        SQUARE,
        attribute_dicts=[("first", {"a": 1}), ("second", {"b": "two"})],
    )
    dictionaries = _face_dictionaries(builder.to_bytes())
    assert {"first": {"a": 1}, "second": {"b": "two"}} in dictionaries


def test_attribute_dictionaries_on_definitions_instances_groups_and_model(tmp_path):
    builder = create_vff()
    with builder.add_component_definition("D", attributes={"def": 1}) as definition:
        definition.add_face(SQUARE, attributes={"face": 2})
    builder.add_instance(definition, attributes={"instance": 3})
    with builder.add_group("G", attributes={"group": 4}) as group:
        group.add_face([(0, 0, 20), (10, 0, 20), (10, 10, 20), (0, 10, 20)])
    builder.set_model_attributes({"model": 5})
    model = parse_bytes(tmp_path, builder.to_bytes())

    definition = next(d for d in model.definitions.values() if d.name == "D")
    assert definition.attribute_dictionaries == {"attributes": {"def": 1}}
    assert [i.attribute_dictionaries for i in model.root.instances] == [
        {"attributes": {"instance": 3}},
        {"attributes": {"group": 4}},
    ]
    assert model.attribute_dictionaries["attributes"] == {"model": 5}


def test_bad_attribute_values_raise_skpwriteerror():
    with pytest.raises(SkpWriteError):
        create_vff().add_face(SQUARE, attributes={"bad": object()})
    with pytest.raises(SkpWriteError):
        create_vff().add_face(
            SQUARE,
            attributes={"a": 1},
            attribute_dicts=[("d", {"b": 2})],
        )
    with pytest.raises(SkpWriteError):
        create_vff().add_face(SQUARE, attributes={"time": Timestamp(-1)})
    with pytest.raises(SkpWriteError):
        create_vff().add_face(SQUARE, attributes={"int": 1 << 31})
    with pytest.raises(SkpWriteError):
        create_vff().add_face(SQUARE, attributes={"float": math.nan})


def test_texture_materials_round_trip(tmp_path):
    png = (WRITER_FIXTURES / "tex.png").read_bytes()
    jpeg = (WRITER_FIXTURES / "tex.jpg").read_bytes()
    builder = create_vff()
    png_handle = builder.add_texture_material(
        "TexPNG", str(WRITER_FIXTURES / "tex.png"), 10.0, 5.0
    )
    builder.add_texture_material(
        "TexJPEG", jpeg, 8.0, 4.0, image_name="tex.jpg"
    )
    builder.add_face(SQUARE, material=png_handle)
    model = parse_bytes(tmp_path, builder.to_bytes())
    textures = {material.name: material.texture for material in model.materials}
    assert textures["TexPNG"].data == png
    assert textures["TexPNG"].width == 10.0
    assert textures["TexPNG"].height == 5.0
    assert textures["TexJPEG"].data == jpeg
    assert textures["TexJPEG"].filename == "tex.jpg"
    assert textures["TexJPEG"].width == 8.0


def test_texture_filename_override_and_special_names(tmp_path):
    builder = create_vff()
    handle = builder.add_texture_material(
        "Tex", (WRITER_FIXTURES / "tex.png").read_bytes(),
        filename="C:/Textures/tex.png", image_name="té#xt ure.png",
    )
    builder.add_face(SQUARE, material=handle)
    model = parse_bytes(tmp_path, builder.to_bytes())
    texture = model.materials[0].texture
    assert texture.filename == "C:/Textures/tex.png"
    entries = [entry.name for entry in read_zip(builder.to_bytes(), builder.to_bytes().find(b"PK\x03\x04"))]
    assert any(entry.endswith("/té#xt ure.png") for entry in entries), entries


def test_texture_read_errors_pass_through_like_create_py(tmp_path):
    from openskp.create import create as create_classic

    with pytest.raises(FileNotFoundError):
        create_vff().add_texture_material("Tex", "/nonexistent/texture.png")
    directory = tmp_path / "texture-dir"
    directory.mkdir()
    # Windows raises PermissionError when read_bytes hits a directory.
    with pytest.raises((IsADirectoryError, PermissionError)) as vff_error:
        create_vff().add_texture_material("Tex", str(directory))
    with pytest.raises((IsADirectoryError, PermissionError)) as classic_error:
        create_classic().add_texture_material("Tex", str(directory))
    assert type(vff_error.value) is type(classic_error.value)
    assert not isinstance(vff_error.value, SkpWriteError)
    with pytest.raises(SkpWriteError):
        create_vff().add_texture_material("Tex", b"not an image")
    with pytest.raises(SkpWriteError):
        create_vff().add_texture_material(
            "Tex", (WRITER_FIXTURES / "tex.png").read_bytes(), float("nan")
        )
    with pytest.raises(SkpWriteError):
        create_vff().add_texture_material(
            "Tex", (WRITER_FIXTURES / "tex.png").read_bytes(), image_name="bad\x01.png"
        )
    with pytest.raises(SkpWriteError):
        create_vff().add_texture_material(
            "Tex", (WRITER_FIXTURES / "tex.png").read_bytes(), image_name="bad\n.png"
        )
    for image_name in (".", ".."):
        with pytest.raises(SkpWriteError):
            create_vff().add_texture_material(
                "Tex", (WRITER_FIXTURES / "tex.png").read_bytes(),
                image_name=image_name,
            )


def test_face_uvs_round_trip_through_sketchup_matrix(tmp_path):
    from openskp._face_groups import compute_face_uv, face_uv_basis

    pins = [
        ((20, 0, 0), (0.0, 0.0)),
        ((30, 0, 0), (1.0, 0.0)),
        ((20, 10, 0), (0.0, 1.0)),
    ]
    builder = create_vff()
    texture = builder.add_texture_material(
        "Tex", str(WRITER_FIXTURES / "tex.png"), 10.0, 5.0
    )
    builder.add_face(
        [(20, 0, 0), (30, 0, 0), (30, 10, 0), (20, 10, 0)],
        material=texture,
        front_uv=pins,
    )
    model = parse_bytes(tmp_path, builder.to_bytes())
    (face,) = model.root.faces.values()
    assert face.uv_transform is not None
    xr, yr = face_uv_basis(face.normal)
    for point, uv in pins:
        assert compute_face_uv(
            point, xr, yr, face.uv_transform, 10.0, 5.0
        ) == pytest.approx(uv, abs=1e-9)


def test_face_uv_requires_exactly_three_pins():
    from openskp.create import create as create_classic

    pins = [
        ((0, 0, 0), (0.0, 0.0)),
        ((10, 0, 0), (1.0, 0.0)),
        ((0, 10, 0), (0.0, 1.0)),
        ((10, 10, 0), (1.0, 1.0)),
    ]
    with pytest.raises(SkpWriteError) as classic:
        create_classic().add_face(SQUARE, front_uv=pins)
    with pytest.raises(SkpWriteError) as vff:
        create_vff().add_face(SQUARE, front_uv=pins)
    assert str(vff.value) == str(classic.value)
    assert "texture positioning needs exactly 3" in str(vff.value)


def test_bad_face_uvs_raise_skpwriteerror():
    builder = create_vff()
    texture = builder.add_texture_material(
        "Tex", str(WRITER_FIXTURES / "tex.png")
    )
    with pytest.raises(SkpWriteError):
        builder.add_face(
            SQUARE,
            material=texture,
            front_uv=[
                ((0, 0, 0), (0.0, 0.0)),
                ((10, 0, 0), (float("nan"), 0.0)),
                ((0, 10, 0), (0.0, 1.0)),
            ],
        )
    with pytest.raises(SkpWriteError):
        builder.add_face(
            SQUARE,
            material=texture,
            front_uv=[
                ((0, 0, 0), (0.0, 0.0)),
                ((10, 0, 0), (1.0, 0.0)),
                ((0, 10, 0), (0.0, 1.0)),
            ],
            auto_triangulate=True,
        )


def test_polylines_at_root_and_in_definitions(tmp_path):
    builder = create_vff()
    builder.add_polyline([(0, 0, 0), (10, 0, 0), (10, 10, 0)])
    builder.add_polyline(
        [(0, 20, 0), (10, 20, 0), (10, 30, 0)], closed=True
    )
    with builder.add_component_definition("CurveDef") as definition:
        definition.add_polyline([(0, 0, 0), (0, 10, 0), (10, 10, 0)])
    model = parse_bytes(tmp_path, builder.to_bytes())
    assert len(model.root.edges) == 5
    definition_model = next(
        d for d in model.definitions.values() if d.name == "CurveDef"
    )
    assert len(definition_model.edges) == 2


def test_bad_polylines_raise_skpwriteerror():
    with pytest.raises(SkpWriteError):
        create_vff().add_polyline([(0, 0, 0)])
    with pytest.raises(SkpWriteError):
        create_vff().add_polyline([(0, 0, 0), (float("inf"), 0, 0)])
    with pytest.raises(SkpWriteError):
        create_vff().add_polyline([(0, 0, 0), (0, 0, 0)])


def test_circles_and_arcs_at_root_and_in_definitions(tmp_path):
    builder = create_vff()
    builder.add_circle((0, 0, 0), (0, 0, 1), 10, 12)
    builder.add_arc((30, 0, 0), (0, 0, 1), 10, 0.0, math.pi / 2, 8)
    with builder.add_component_definition("Curves") as definition:
        definition.add_circle((0, 0, 0), (1, 0, 0), 5, 6)
        definition.add_arc((20, 0, 0), (1, 0, 0), 5, 0.5, 1.5, 4)
    model = parse_bytes(tmp_path, builder.to_bytes())

    root_arcs = sorted(
        model.root.arcs.values(), key=lambda arc: arc.center[0]
    )
    assert len(root_arcs) == 2
    circle, arc = root_arcs
    assert circle.center == (0.0, 0.0, 0.0)
    assert circle.normal == (0.0, 0.0, 1.0)
    assert circle.start_angle == 0.0
    assert circle.end_angle == pytest.approx(2.0 * math.pi)
    assert arc.center == (30.0, 0.0, 0.0)
    assert arc.start_angle == 0.0
    assert arc.end_angle == pytest.approx(math.pi / 2)
    assert len(model.root.faces) == 1
    assert len(model.root.edges) == 20

    definition_model = next(
        d for d in model.definitions.values() if d.name == "Curves"
    )
    assert len(definition_model.arcs) == 2
    assert len(definition_model.faces) == 1
    assert len(definition_model.edges) == 10


def test_circle_curve_handle_can_be_a_face_hole(tmp_path):
    builder = create_vff()
    hole = builder.add_circle_curve((5, 5, 0), (0, 0, 1), 2, 8)
    builder.add_face(
        [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)], holes=[hole]
    )
    model = parse_bytes(tmp_path, builder.to_bytes())
    (face,) = model.root.faces.values()
    assert len(face.loops) == 2


def test_bad_curves_raise_skpwriteerror():
    for segments in (2, 256):
        with pytest.raises(
            SkpWriteError, match=rf"num_segments must be between 3 and 255, got {segments}"
        ):
            create_vff().add_circle((0, 0, 0), (0, 0, 1), 1, segments)
        with pytest.raises(
            SkpWriteError, match=rf"num_segments must be between 3 and 255, got {segments}"
        ):
            create_vff().add_arc((0, 0, 0), (0, 0, 1), 1, 0.0, 1.0, segments)
    with pytest.raises(SkpWriteError, match="zero vector"):
        create_vff().add_circle((0, 0, 0), (0, 0, 0), 1)
    with pytest.raises(SkpWriteError, match="finite"):
        create_vff().add_circle((0, 0, 0), (0, 0, 1), float("inf"))
    with pytest.raises(SkpWriteError, match="must differ"):
        create_vff().add_arc((0, 0, 0), (0, 0, 1), 1, 0.5, 0.5)
    with pytest.raises(SkpWriteError, match="finite"):
        create_vff().add_arc((0, 0, 0), (0, 0, 1), 1, 0.0, float("inf"))
    with pytest.raises(SkpWriteError, match="non-finite"):
        create_vff().add_arc((1e308, 0, 0), (0, 0, 1), 1e308, 0.0, 1.0)


def _face_attribute_set_order(data: bytes):
    from openskp._core import CONTAINER_TAGS, parse_tlv_recursive
    from openskp.vff import extract_skp_contents

    tags = set(CONTAINER_TAGS) | {
        "D007", "DC05", "DD05", "B136", "B236",
    }
    model_data = extract_skp_contents(data)["model_data"]
    roots = parse_tlv_recursive(model_data, 0, len(model_data), tags)
    orders = []

    def walk(node):
        if node.tag == "AC0D":
            for child in node.children:
                if child.tag != "D007":
                    continue
                for entity in child.children:
                    if entity.tag != "DC05":
                        continue
                    for block in entity.children:
                        if block.tag != "DD05":
                            continue
                        for container in block.children:
                            for record_set in container.children:
                                if record_set.tag == "B236":
                                    orders.append([c.tag for c in record_set.children])
        for child in node.children:
            walk(child)

    for root in roots:
        walk(root)
    return orders


def test_face_with_uv_and_attributes_keeps_both(tmp_path):
    pins = [
        ((0, 0, 0), (0.0, 0.0)),
        ((10, 0, 0), (1.0, 0.0)),
        ((0, 10, 0), (0.0, 1.0)),
    ]
    builder = create_vff()
    texture = builder.add_texture_material(
        "Tex", str(WRITER_FIXTURES / "tex.png"), 10.0, 10.0
    )
    builder.add_face(
        SQUARE,
        material=texture,
        front_uv=pins,
        attribute_dicts=[("attributes", {"d": 1}), ("second", {"label": "ok"})],
    )
    data = builder.to_bytes()
    assert _face_attribute_set_order(data) == [["B336", "B336", "1027"]]
    dictionaries = [
        entry for entry in _face_dictionaries(data) if "attributes" in entry
    ]
    assert dictionaries == [{"attributes": {"d": 1}, "second": {"label": "ok"}}]


def test_set_model_attributes_keeps_template_and_earlier_dicts(tmp_path):
    base = parse_bytes(tmp_path, create_vff().to_bytes())
    builder = create_vff()
    builder.set_model_attributes({"a": 1}, "D1")
    builder.set_model_attributes({"b": 2}, "D2")
    builder.set_model_attributes({"a": 3}, "D1")
    model = parse_bytes(tmp_path, builder.to_bytes())
    dictionaries = model.attribute_dictionaries
    assert set(base.attribute_dictionaries) <= set(dictionaries)
    assert dictionaries["D1"] == {"a": 3}
    assert dictionaries["D2"] == {"b": 2}
    assert len(_model_attribute_blocks(builder.to_bytes())) == 1


def _model_attribute_blocks(data: bytes):
    from openskp._vff_writer.template import MODEL_CONTAINERS
    from openskp._vff_writer.tlv import parse_records
    from openskp.vff import extract_skp_contents

    model_data = extract_skp_contents(data)["model_data"]
    records = parse_records(
        model_data,
        set(MODEL_CONTAINERS) | {0x07D0, 0x05DC, 0x05DD},
    )

    def find(node, tag):
        if node.tag == tag:
            return node
        for child in node.children or []:
            found = find(child, tag)
            if found is not None:
                return found
        return None

    root = find(records[0], 0x1388)
    drawing = next(c for c in root.children if c.tag == 0x07D0)
    entity = next(c for c in drawing.children if c.tag == 0x05DC)
    return [c for c in entity.children if c.tag == 0x05DD]


def _all_attribute_blocks(data: bytes):
    from openskp._vff_writer.template import MODEL_CONTAINERS
    from openskp._vff_writer.tlv import parse_records
    from openskp.vff import extract_skp_contents

    model_data = extract_skp_contents(data)["model_data"]
    records = parse_records(
        model_data,
        set(MODEL_CONTAINERS)
        | {
            0x07D0, 0x05DC, 0x05DD, 0x36B1, 0x36B2, 0x36B3,
            0x2710, 0x2711, 0x2712, 0x2713,
        },
    )
    count = 0

    def has_dictionary(node):
        if node.tag == 0x36B3:
            return True
        return any(has_dictionary(child) for child in node.children or [])

    def walk(node):
        nonlocal count
        if node.tag == 0x05DD and not has_dictionary(node):
            count += 1
        for child in node.children or []:
            walk(child)

    for record_ in records:
        walk(record_)
    return count


def test_model_without_attributes_has_no_empty_blocks(tmp_path):
    builder = create_vff()
    builder.add_face(SQUARE)
    with builder.add_component_definition("D") as definition:
        definition.add_face(SQUARE)
    builder.add_instance(definition, translation=(20, 0, 0))
    with builder.add_group(translation=(0, 20, 0)) as group:
        group.add_face(SQUARE)
    data = builder.to_bytes()
    assert _all_attribute_blocks(data) == 0
    parse_bytes(tmp_path, data)


def test_failed_geometry_calls_leave_no_geometry(tmp_path):
    bad_nan_pins = [
        ((0, 0, 0), (0.0, 0.0)),
        ((10, 0, 0), (float("nan"), 0.0)),
        ((0, 10, 0), (0.0, 1.0)),
    ]
    cases = [
        lambda b: b.add_face(SQUARE, attributes={"x": object()}),
        lambda b: b.add_face(SQUARE, attributes={"x": 2 ** 40}),
        lambda b: b.add_circle(
            (0, 0, 0), (0, 0, 1), 5,
            holes=[[(1, 1, 1), (2, 1, 1), (2, 2, 1)]],
        ),
        lambda b: b.add_circle(
            (0, 0, 0), (0, 0, 1), 5, attributes={"x": object()}
        ),
        lambda b: b.add_face(
            SQUARE, material=b.add_texture_material("Tex", str(WRITER_FIXTURES / "tex.png")),
            front_uv=bad_nan_pins,
        ),
        lambda b: b.add_circle(
            (0, 0, 0), (0, 0, 1), 5,
            material=b.add_texture_material("Tex2", str(WRITER_FIXTURES / "tex.png")),
            front_uv=bad_nan_pins,
        ),
    ]
    triangle = [(20, 0, 0), (30, 0, 0), (20, 10, 0)]
    for bad in cases:
        builder = create_vff()
        with pytest.raises(SkpWriteError):
            bad(builder)
        builder.add_face(triangle)
        model = parse_bytes(tmp_path, builder.to_bytes())
        assert len(model.root.faces) == 1
        assert len(model.root.edges) == 3
        assert len(model.root.vertices) == 3


def _curve_records(data: bytes):
    from openskp._vff_writer.template import MODEL_CONTAINERS
    from openskp._vff_writer.tlv import parse_records
    from openskp.vff import extract_skp_contents

    model_data = extract_skp_contents(data)["model_data"]
    records = parse_records(
        model_data,
        set(MODEL_CONTAINERS)
        | {0x0DAC, 0x0BB8, 0x4A38, 0x4C2C, 0x07D0, 0x05DC},
    )
    curves = []

    def walk(node):
        if node.tag == 0x4A38:
            curves.append(node)
        for child in node.children or []:
            walk(child)

    for record_ in records:
        walk(record_)
    return curves


def test_curves_leave_existing_edges_out(tmp_path):
    square = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)]
    builder = create_vff()
    builder.add_face(square)
    builder.add_polyline([(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)])
    model = parse_bytes(tmp_path, builder.to_bytes())
    assert len(model.root.edges) == 4
    assert _curve_records(builder.to_bytes()) == []

    builder = create_vff()
    builder.add_circle((0, 0, 0), (0, 0, 1), 5, 12)
    builder.add_circle((0, 0, 0), (0, 0, 1), 5, 12)
    data = builder.to_bytes()
    model = parse_bytes(tmp_path, data)
    assert len(model.root.edges) == 12
    assert len(_curve_records(data)) == 1

    builder = create_vff()
    builder.add_polyline([(0, 0, 0), (1, 0, 0), (0, 0, 0), (1, 0, 0)])
    data = builder.to_bytes()
    assert len(parse_bytes(tmp_path, data).root.edges) == 1
    assert _curve_records(data) == []


def test_curve_over_a_partly_existing_chain_keeps_new_runs(tmp_path):
    square = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)]
    builder = create_vff()
    builder.add_face(square)
    builder.add_polyline(
        [(0, 0, 0), (10, 0, 0), (20, 0, 0), (30, 0, 0)]
    )
    data = builder.to_bytes()
    assert len(parse_bytes(tmp_path, data).root.edges) == 6
    curves = _curve_records(data)
    assert len(curves) == 1
    count = next(c for c in curves[0].children if c.tag == 0x4A39)
    assert count.payload == (2).to_bytes(4, "little")


def test_face_uvs_on_solid_or_no_material_are_accepted(tmp_path):
    from openskp.create import create as create_classic
    from openskp._face_groups import compute_face_uv, face_uv_basis

    pins = [
        ((0, 0, 0), (0.0, 0.0)),
        ((10, 0, 0), (1.0, 0.0)),
        ((0, 10, 0), (0.0, 1.0)),
    ]

    def build(factory, material):
        builder = factory()
        if material == "solid":
            material = builder.add_material("Red", (255, 0, 0))
        else:
            material = None
        builder.add_face(SQUARE, material=material, front_uv=pins)
        return parse_bytes(tmp_path, builder.to_bytes())

    for material in ("solid", "none"):
        classic = build(create_classic, material)
        vff = build(create_vff, material)
        (classic_face,) = classic.root.faces.values()
        (vff_face,) = vff.root.faces.values()
        assert vff_face.uv_transform is not None
        assert vff_face.uv_transform == pytest.approx(
            classic_face.uv_transform, abs=1e-9
        )
        xr, yr = face_uv_basis(vff_face.normal)
        for point, uv in pins:
            assert compute_face_uv(
                point, xr, yr, vff_face.uv_transform, 1.0, 1.0
            ) == pytest.approx(uv, abs=1e-9)


def test_back_uv_without_back_material_does_not_use_front_texture_size(tmp_path):
    from openskp.create import create as create_classic

    front_pins = [
        ((0, 0, 0), (0.0, 0.0)),
        ((10, 0, 0), (1.0, 0.0)),
        ((0, 10, 0), (0.0, 1.0)),
    ]
    back_pins = [
        ((0, 0, 0), (0.0, 0.0)),
        ((5, 0, 0), (1.0, 0.0)),
        ((0, 5, 0), (0.0, 1.0)),
    ]

    def build(factory):
        builder = factory()
        texture = builder.add_texture_material(
            "Tex", str(WRITER_FIXTURES / "tex.png"), 10.0, 10.0
        )
        builder.add_face(
            SQUARE,
            material=texture,
            front_uv=front_pins,
            back_uv=back_pins,
        )
        return parse_bytes(tmp_path, builder.to_bytes())

    classic = build(create_classic)
    vff = build(create_vff)
    (classic_face,) = classic.root.faces.values()
    (vff_face,) = vff.root.faces.values()
    assert vff_face.uv_transform_back is not None
    assert vff_face.uv_transform_back == pytest.approx(
        classic_face.uv_transform_back, abs=1e-9
    )


def test_annotations_round_trip_at_root_and_in_definition(tmp_path):
    builder = create_vff()
    tag = builder.add_layer("Annotations", (10, 20, 30))
    builder.add_text(
        "Corner label", (0, 0, 0), (1, 2, 3), layer=tag, hidden=True,
        screen_position=(0.25, 0.75), display_leader=False,
    )
    builder.add_dimension(
        (0, 0, 0), (10, 0, 0), offset_vector=(0, 5, 0), layer=tag, hidden=True
    )
    builder.add_construction_line((0, 0, 0), point2=(4, 0, 0), layer=tag, hidden=True)
    builder.add_construction_line((0, 0, 0), direction=(0, 0, 1), layer=tag)
    builder.add_construction_point((1, 2, 3), layer=tag, hidden=True)
    builder.add_section_plane(
        (0, 0, 5), (0, 0, 1), name="Section A", symbol="A", hidden=True
    )
    with builder.add_component_definition("Annotated") as definition:
        definition.add_text("In component", (0, 0, 0), layer=tag)
        definition.add_dimension((0, 0, 0), (5, 0, 0), layer=tag)
        definition.add_construction_line((0, 0, 0), point2=(1, 1, 1), layer=tag)
        definition.add_construction_point((2, 2, 2), layer=tag)
        definition.add_section_plane(
            (0, 0, 1), (1, 0, 0), name="Inner", symbol="I"
        )
    model = parse_bytes(tmp_path, builder.to_bytes())
    root = model.root
    assert [text.text for text in root.texts] == ["Corner label"]
    text = root.texts[0]
    assert text.layer == "Annotations" and text.hidden is True
    assert text.screen_position == pytest.approx((0.25, 0.75))
    assert text.display_leader is False
    assert len(root.dimensions) == 1
    assert root.dimensions[0].layer == "Annotations"
    assert len(root.construction_lines) == 2
    assert len(root.construction_points) == 1
    section = root.section_planes[0]
    assert (section.name, section.label, section.hidden) == ("Section A", "A", True)
    assert section.plane == pytest.approx([0.0, 0.0, 1.0, -5.0])

    definition_model = next(
        item for item in model.definitions.values() if item.name == "Annotated"
    )
    assert [text.text for text in definition_model.texts] == ["In component"]
    assert definition_model.texts[0].point == pytest.approx((0.0, 0.0, 0.0))
    assert definition_model.texts[0].label_point == pytest.approx((15.0, 15.0, 15.0))
    assert len(definition_model.dimensions) == 1
    dimension = definition_model.dimensions[0]
    assert dimension.a == pytest.approx((0.0, 0.0, 0.0))
    assert dimension.b == pytest.approx((5.0, 0.0, 0.0))
    assert dimension.offset == pytest.approx(10.0)
    assert len(definition_model.construction_lines) == 1
    line = definition_model.construction_lines[0]
    assert line.start == pytest.approx((0.0, 0.0, 0.0))
    assert line.end == pytest.approx((1.0, 1.0, 1.0))
    assert line.direction == pytest.approx((1 / 3 ** 0.5,) * 3)
    assert len(definition_model.construction_points) == 1
    assert definition_model.construction_points[0].position == pytest.approx(
        (2.0, 2.0, 2.0)
    )
    assert definition_model.section_planes[0].name == "Inner"
    assert definition_model.section_planes[0].plane == pytest.approx(
        [1.0, 0.0, 0.0, 0.0]
    )


def test_scenes_and_camera_round_trip(tmp_path):
    builder = create_vff()
    visible = builder.add_layer("Visible")
    hidden = builder.add_layer("Hidden Tag", (1, 2, 3))
    builder.add_scene(
        "Perspective", (40, -40, 30), (0, 0, 0),
        perspective=True, fov=45.0,
    )
    builder.add_scene(
        "Parallel", (0, -60, 0), (0, 0, 0),
        up=(0, 0, 1), perspective=False, ortho_height=80.0,
    )
    builder.add_scene(
        "Hide B", (10, -10, 10), (0, 0, 0), hidden_layers=(hidden,)
    )
    builder.set_camera((50, -50, 50), (0, 0, 0), perspective=True, fov=30.0)
    data = builder.to_bytes()
    model = parse_bytes(tmp_path, data)
    pages = model.pages
    assert [page.name for page in pages] == ["Perspective", "Parallel", "Hide B"]
    assert pages[0].parallel is False and pages[0].fov == pytest.approx(45.0)
    assert pages[1].parallel is True and pages[1].ortho_height == pytest.approx(80.0)
    assert pages[2].hidden_layers == ["Hidden Tag"]

    # The model's start camera is the separate set_camera record.
    from openskp._vff_writer.template import T_CAMERA, T_CAMERA_MODEL, T_CAMERA_EYE
    from openskp._vff_writer.tlv import parse_records
    from openskp.vff import extract_skp_contents

    model_data = extract_skp_contents(data)["model_data"]
    document = parse_records(model_data, {0x01F4, T_CAMERA_MODEL, T_CAMERA})[0]
    camera_section = next(c for c in document.children if c.tag == T_CAMERA_MODEL)
    camera = next(c for c in camera_section.children if c.tag == T_CAMERA)
    eye = next(c for c in camera.children if c.tag == T_CAMERA_EYE)
    assert struct.unpack("<3d", eye.payload) == pytest.approx((50.0, -50.0, 50.0))
    assert visible != hidden


def test_failed_annotation_and_scene_calls_leave_nothing_behind(tmp_path):
    cases = [
        lambda b: b.add_text("x", (0, 0, 0), leader_type=9),
        lambda b: b.add_dimension((0, 0, 0), (0, 0, 0)),
        lambda b: b.add_construction_line((0, 0, 0)),
        lambda b: b.add_construction_point((float("nan"), 0, 0)),
        lambda b: b.add_section_plane((0, 0, 0), (0, 0, 0)),
        lambda b: b.add_scene("S", (1, 0, 0), (1, 0, 0)),
    ]
    triangle = [(20, 0, 0), (30, 0, 0), (20, 10, 0)]
    for bad in cases:
        builder = create_vff()
        with pytest.raises(SkpWriteError):
            bad(builder)
        builder.add_face(triangle)
        model = parse_bytes(tmp_path, builder.to_bytes())
        assert len(model.root.faces) == 1
        assert len(model.root.edges) == 3
        assert model.root.texts == []
        assert model.root.dimensions == []
        assert model.root.construction_lines == []
        assert model.root.construction_points == []
        assert model.root.section_planes == []
        assert model.pages == []






def test_special_text_and_scene_layer_names_round_trip(tmp_path):
    builder = create_vff()
    special = 'Quoted "label" ü 木材\t\n'
    tag = builder.add_layer('Tag "quoted" / ü 木材')
    builder.add_text(special, (0, 0, 0), layer=tag)
    builder.add_scene("Special scene", (10, -10, 10), (0, 0, 0), hidden_layers=(tag,))
    model = parse_bytes(tmp_path, builder.to_bytes())
    assert model.root.texts[0].text == special
    assert model.root.texts[0].layer == 'Tag "quoted" / ü 木材'
    assert model.pages[0].hidden_layers == ['Tag "quoted" / ü 木材']


def test_set_camera_twice_keeps_the_last_camera(tmp_path):
    builder = create_vff()
    builder.set_camera((10, -10, 10), (0, 0, 0))
    builder.set_camera((20, -20, 20), (0, 0, 0), perspective=False, ortho_height=50.0)
    data = builder.to_bytes()
    from openskp._vff_writer.template import (
        T_CAMERA, T_CAMERA_EYE, T_CAMERA_MODEL, T_CAMERA_TARGET
    )
    from openskp._vff_writer.tlv import parse_records
    from openskp.vff import extract_skp_contents

    model_data = extract_skp_contents(data)["model_data"]
    document = parse_records(model_data, {0x01F4, T_CAMERA_MODEL, T_CAMERA})[0]
    section = next(c for c in document.children if c.tag == T_CAMERA_MODEL)
    camera = next(c for c in section.children if c.tag == T_CAMERA)
    eye = next(c for c in camera.children if c.tag == T_CAMERA_EYE)
    target = next(c for c in camera.children if c.tag == T_CAMERA_TARGET)
    assert struct.unpack("<3d", eye.payload) == pytest.approx((20.0, -20.0, 20.0))
    assert struct.unpack("<3d", target.payload) == pytest.approx((0.0, 0.0, 0.0))


def test_annotations_round_trip_inside_a_group(tmp_path):
    builder = create_vff()
    with builder.add_group("Annotated Group") as group:
        group.add_text("Group text", (1, 2, 3), (4, 5, 6))
        group.add_dimension((0, 0, 0), (4, 0, 0))
        group.add_construction_line((0, 0, 0), point2=(2, 2, 2))
        group.add_construction_point((3, 3, 3))
        group.add_section_plane((0, 0, 2), (0, 0, 1), name="Group section")
    model = parse_bytes(tmp_path, builder.to_bytes())
    group_model = next(
        item for item in model.definitions.values() if item.name == "Annotated Group"
    )
    assert group_model.texts[0].text == "Group text"
    assert group_model.texts[0].point == pytest.approx((1.0, 2.0, 3.0))
    assert group_model.dimensions[0].b == pytest.approx((4.0, 0.0, 0.0))
    assert group_model.construction_lines[0].end == pytest.approx((2.0, 2.0, 2.0))
    assert group_model.construction_points[0].position == pytest.approx((3.0, 3.0, 3.0))
    assert group_model.section_planes[0].name == "Group section"


def test_second_section_plane_can_be_marked_active(tmp_path):
    builder = create_vff()
    builder.add_section_plane((0, 0, 1), (0, 0, 1), name="First")
    builder.add_section_plane((0, 0, 2), (0, 0, 1), name="Second", active=True)
    data = builder.to_bytes()
    from openskp._vff_writer.template import (
        MODEL_CONTAINERS, T_ACTIVE_SECTION, T_ENTITY_BASE, T_ENTITY_ID,
        T_SECTION, T_SECTION_PLANES, T_DRAWING
    )
    from openskp._vff_writer.tlv import parse_records, read_varint
    from openskp.vff import extract_skp_contents

    model_data = extract_skp_contents(data)["model_data"]
    document = parse_records(
        model_data,
        set(MODEL_CONTAINERS) | {T_ENTITY_BASE, T_DRAWING, T_SECTION, T_ACTIVE_SECTION},
    )[0]
    def find(node, tag):
        if node.tag == tag:
            return node
        for child in node.children or []:
            found = find(child, tag)
            if found is not None:
                return found
        return None

    root = find(document, 0x1388)
    section_pool = next(c for c in root.children if c.tag == T_SECTION_PLANES)
    section_ids = []
    for section in section_pool.children or []:
        base = find(section, T_ENTITY_BASE)
        section_ids.append(
            read_varint(next(c for c in base.children if c.tag == T_ENTITY_ID).payload)
        )
    active = next(c for c in root.children if c.tag == T_ACTIVE_SECTION)
    assert read_varint(active.payload) == section_ids[1]
    model = parse_bytes(tmp_path, data)
    assert [s.name for s in model.root.section_planes] == ["First", "Second"]


def _model_camera_bytes(data: bytes) -> bytes:
    from openskp._vff_writer.template import T_CAMERA_MODEL
    from openskp._vff_writer.tlv import parse_records
    from openskp.vff import extract_skp_contents

    model_data = extract_skp_contents(data)["model_data"]
    document = parse_records(model_data, {0x01F4, T_CAMERA_MODEL})[0]
    section = next(c for c in document.children if c.tag == T_CAMERA_MODEL)
    return section.encode()


def test_add_scene_does_not_change_the_model_camera(tmp_path):
    plain = create_vff()
    with_scene = create_vff()
    with_scene.add_scene("S", (10, -10, 10), (0, 0, 0))
    assert _model_camera_bytes(plain.to_bytes()) == _model_camera_bytes(
        with_scene.to_bytes()
    )

    set_camera = create_vff()
    set_camera.set_camera((20, -20, 20), (0, 0, 0), perspective=False, ortho_height=50)
    camera_before = _model_camera_bytes(set_camera.to_bytes())
    set_camera.add_scene("S", (10, -10, 10), (0, 0, 0))
    assert camera_before == _model_camera_bytes(set_camera.to_bytes())


def test_huge_finite_construction_lines_keep_a_unit_direction(tmp_path):
    builder = create_vff()
    builder.add_construction_line((0, 0, 0), direction=(1e200, 0, 0))
    builder.add_construction_line((0, 0, 0), point2=(1e200, 0, 0))
    model = parse_bytes(tmp_path, builder.to_bytes())
    for line in model.root.construction_lines:
        assert line.direction == pytest.approx((1.0, 0.0, 0.0))


def test_section_plane_normalises_huge_and_tiny_normals(tmp_path):
    for normal in ((1e200, 0, 0), (1e-200, 0, 0)):
        builder = create_vff()
        builder.add_section_plane((3, 4, 5), normal)
        model = parse_bytes(tmp_path, builder.to_bytes())
        plane = model.root.section_planes[0].plane
        assert plane[:3] == pytest.approx([1.0, 0.0, 0.0])
        assert math.isfinite(plane[3])
    with pytest.raises(SkpWriteError, match="offset must be finite"):
        create_vff().add_section_plane((1.7e308, 1.7e308, 1.7e308), (1, 1, 1))


def test_section_plane_name_validation_and_atomicity():
    cases = [
        lambda b: b.add_section_plane((0, 0, 0), (0, 0, 1), name=3),
        lambda b: b.add_section_plane((0, 0, 0), (0, 0, 1), symbol=3),
        lambda b: b.add_section_plane((0, 0, 0), (0, 0, 1), name="\ud800"),
        lambda b: b.add_section_plane((0, 0, 0), (0, 0, 1), symbol="\ud800"),
    ]
    for bad in cases:
        builder = create_vff()
        with pytest.raises(SkpWriteError):
            bad(builder)
        assert builder._root._pools.get(0x1393) is None


def _first_record_child_tags(data: bytes, tag: int):
    from openskp._vff_writer.template import MODEL_CONTAINERS
    from openskp._vff_writer.tlv import parse_records
    from openskp.vff import extract_skp_contents

    model_data = extract_skp_contents(data)["model_data"]
    records = parse_records(
        model_data,
        set(MODEL_CONTAINERS)
        | {0x55F0, 0x55F1, 0x55F4, 0x55F5, 0x5208, 0x5BCC, 0x59D8,
           0x5BCD, 0x5BCE, 0x4269, 0x4268, 0x426C, 0x445C, 0x7148,
           0x6F54, 0x34BC},
    )
    found = []

    def walk(node):
        if node.tag == tag and not found:
            found.append([child.tag for child in node.children or []])
        for child in node.children or []:
            walk(child)

    for record_ in records:
        walk(record_)
    return found[0]


def test_written_annotation_records_match_sketchup_2025_child_layout():
    """The genuine fixtures are SketchUp 2025 Ruby-generated saves."""
    annotations_fixture = (WRITER_FIXTURES / "genuine_annotations_2025.skp").read_bytes()
    scenes_fixture = (WRITER_FIXTURES / "genuine_scenes_2025.skp").read_bytes()
    builder = create_vff()
    builder.add_text("Text", (0, 0, 0))
    builder.add_dimension((0, 0, 0), (10, 0, 0))
    builder.add_construction_line((0, 0, 0), point2=(1, 1, 1))
    builder.add_construction_point((1, 2, 3))
    builder.add_section_plane((0, 0, 0), (0, 0, 1), name="S", symbol="S")
    builder.add_scene("Scene", (10, -10, 10), (0, 0, 0))
    written = builder.to_bytes()
    for tag in (0x55F0, 0x5BCC, 0x4269, 0x426C, 0x445C):
        assert _first_record_child_tags(written, tag) == _first_record_child_tags(
            annotations_fixture, tag
        ), hex(tag)
    assert _first_record_child_tags(written, 0x7148) == _first_record_child_tags(
        scenes_fixture, 0x7148
    )


def test_huge_finite_camera_values_are_rejected_or_written_finite(tmp_path):
    for add in (
        lambda b: b.set_camera((1e200, 0, 0), (0, 0, 0)),
        lambda b: b.add_scene("S", (1e200, 0, 0), (0, 0, 0)),
    ):
        builder = create_vff()
        try:
            add(builder)
        except SkpWriteError as error:
            assert "scene camera values must be finite numbers" in str(error)
            assert builder._template.camera is None
            assert builder._template.scenes == []
            data = builder.to_bytes()
            assert _model_camera_bytes(data) == _model_camera_bytes(
                create_vff().to_bytes()
            )
        else:
            data = builder.to_bytes()
            if builder._template.camera is not None:
                camera = builder._template.camera
                direction = [
                    camera["target"][i] - camera["eye"][i] for i in range(3)
                ]
                assert all(math.isfinite(value) for value in direction)
                assert math.hypot(*direction) == pytest.approx(1e200)
            for scene in builder._template.scenes:
                direction = [scene["target"][i] - scene["eye"][i] for i in range(3)]
                assert all(math.isfinite(value) for value in direction)


def test_binary_format_section7_tags_are_in_file_byte_order():
    root = Path(__file__).resolve().parents[3]
    source = (
        (root / "packages/python/src/openskp/_blank_document_vff.py").read_text(
            encoding="utf-8"
        )
        + (root / "packages/python/src/openskp/_vff_writer/template.py").read_text(
            encoding="utf-8"
        )
        + (root / "packages/python/src/openskp/_vff_writer/uv.py").read_text(
            encoding="utf-8"
        )
    )
    allowed = {
        int(match.group(1), 16).to_bytes(2, "little").hex().upper()
        for match in re.finditer(
            r"^T_[A-Z0-9_]+\s*=\s*0x([0-9A-Fa-f]+)", source, re.MULTILINE
        )
    }
    allowed.update(
        value.to_bytes(2, "little").hex().upper()
        for value in range(0x7B0F, 0x7B17)
    )
    section7 = (
        (root / "docs/BINARY_FORMAT.md")
        .read_text(encoding="utf-8")
        .split("## 7.", 1)[1]
    )
    for tag in set(re.findall(r"`([0-9A-F]{4})`", section7)):
        assert tag in allowed, tag
