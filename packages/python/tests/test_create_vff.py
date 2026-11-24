"""Round-trip tests for the template-based VFF writer (:mod:`openskp.create_vff`).

The spike model is the one described in the stage brief: a 10-inch box (six
faces sharing eight vertices and twelve edges), one solid-colour material on
four of its faces, one tag on every face and edge, and a component definition
holding its own box, placed twice (one plain translation, one translated and
rotated 45 degrees about Z).  Each test re-parses the written bytes with
openskp's public parser and checks the structure SketchUp itself reports for
the acceptance copy in ``research/vff-writer/``.
"""

from __future__ import annotations

import math
import os
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

from openskp import SkpFile
from openskp._face_groups import compute_face_uv, face_uv_basis
from openskp._vff_writer.archive import (
    _crc_register,
    _history_constant,
    read_zip,
)
from openskp._vff_writer.tlv import parse_records
from openskp.create import Length, Point3d, Timestamp, Vector3d
from openskp.create_vff import (
    DEFAULT_TEMPLATE,
    VffBuilder,
    check_integrity,
    compute_integrity_word,
    read_stored_integrity_word,
)

FIXTURES = Path(__file__).parent / "fixtures"
NESTING_TEMPLATE = FIXTURES / "nesting_2021.skp"
WRITER_FIXTURES = FIXTURES / "vff_writer"
TEX_PNG = (WRITER_FIXTURES / "tex.png").read_bytes()
TEX_JPEG = (WRITER_FIXTURES / "tex.jpg").read_bytes()

#: Box corners, inches - shared by every face.
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

#: Outward-wound quads: bottom, top, front, right, back, left.
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
    data = builder.to_bytes()
    return Spike(data, material, tag, definition.id)


def parse_bytes(tmp_path: Path, data: bytes):
    path = tmp_path / "spike.skp"
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
    assert sorted((v.x, v.y, v.z) for v in definition.vertices.values()) == sorted(
        BOX_POINTS
    )
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


def test_spike_on_the_bundled_2025_template(tmp_path):
    spike = build_spike()
    model = parse_bytes(tmp_path, spike.data)
    root = model.root

    # The file is a complete VFF container: CRCs, header integrity word,
    # revision 17 and model.dat last.
    assert check_integrity(spike.data)
    assert read_stored_integrity_word(spike.data) == compute_integrity_word(spike.data)
    header = spike.data[: spike.data.find(b"PK\x03\x04")]
    revision_offset = header.find(b"VFF\x08\x00\x01\x00") + 7
    assert struct.unpack_from("<H", spike.data, revision_offset)[0] == 17
    names = [entry.name for entry in read_zip(spike.data, spike.data.find(b"PK\x03\x04"))]
    assert names[-1] == "model.dat"
    assert names[2] == "meta/meta.dat"
    assert "materials/SpikeRed/material.xml" in names
    assert "materials/Layer_SpikeTag/material.xml" in names
    # meta.dat gets a fresh model GUID on every write (SketchUp does the same).
    assert _meta_guid(spike.data) != _meta_guid(DEFAULT_TEMPLATE.read_bytes())

    # Geometry.
    _assert_box_geometry(root)
    assert len(root.faces) == 6
    assert len(root.edges) == 12

    # Material: one solid colour, painted on exactly four faces.
    (material,) = model.materials
    assert material.name == "SpikeRed"
    assert material.color[:3] == RED
    assert material.id == spike.material_id
    material_ids = sorted(
        face.material_id for face in root.faces.values() if face.material_id is not None
    )
    assert material_ids == [spike.material_id] * 4
    assert sum(1 for face in root.faces.values() if face.material_id is None) == 2

    # Tag: every root face and edge carries it, and the tag reads back.
    tags = {layer.name: layer for layer in model.layers}
    assert (tags["SpikeTag"].color_r, tags["SpikeTag"].color_g, tags["SpikeTag"].color_b) == TAG_COLOR
    assert {face.layer for face in root.faces.values()} == {spike.layer_id}
    assert {edge.layer for edge in root.edges.values()} == {spike.layer_id}

    # Component definition: its own box, painted on two faces.
    assert len(model.definitions) == 1
    definition = model.definitions[spike.definition_id]
    assert definition.name == "SpikeBox"
    _assert_box_geometry(definition)
    painted = [face.material_id for face in definition.faces.values()]
    assert painted.count(spike.material_id) == 2

    # Two placed instances, one translated, one translated and rotated.
    assert len(root.instances) == 2
    for instance, expected in zip(root.instances, _expected_instances()):
        assert instance.ref_idx == spike.definition_id
        assert instance.layer == "SpikeTag"
        assert len(instance.matrix) == 13
        for actual, wanted in zip(instance.matrix, expected):
            assert actual == pytest.approx(wanted, abs=1e-12)


def test_spike_on_an_existing_2021_model_template(tmp_path):
    # nesting_2021.skp is an unrelated SketchUp-saved model: its root already
    # holds a placed group, and it has its own definitions, layers and
    # materials.  The same writer call has to extend it, which is what proves
    # there are no offsets specific to the bundled template.
    spike = build_spike(NESTING_TEMPLATE)
    assert check_integrity(spike.data)
    model = parse_bytes(tmp_path, spike.data)
    root = model.root

    # The spike geometry is still exactly one box plus two instances; the
    # template's own root entity (one group instance) is the extra instance.
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

    # The template's own definitions survive next to the new one.
    names = {definition.name for definition in model.definitions.values()}
    assert {"Group", "Shelf", "Group2", "SpikeBox"} <= names
    spike_box = model.definitions[spike.definition_id]
    assert spike_box.name == "SpikeBox"
    _assert_box_geometry(spike_box)

    # Material and tag were appended to the template's own managers.
    assert any(
        material.name == "SpikeRed" and material.color[:3] == RED
        for material in model.materials
    )
    tags = {layer.name: layer for layer in model.layers}
    assert (tags["SpikeTag"].color_r, tags["SpikeTag"].color_g, tags["SpikeTag"].color_b) == TAG_COLOR
    assert {face.layer for face in root.faces.values()} == {spike.layer_id}


@pytest.mark.parametrize(
    "name",
    [
        "SU_File.skp",
        "Untitled.skp",
        str(DEFAULT_TEMPLATE.name),
    ],
)
def test_integrity_word_matches_sketchup_saved_files(name):
    # The formula was derived from SketchUp's own files, so it must reproduce
    # the word already stored in unmodified SketchUp saves.  SU_File.skp and
    # Untitled.skp are genuine VFF (2025) saves shipped with the suite; the
    # bundled blank template is the same kind of file.
    path = DEFAULT_TEMPLATE if name == DEFAULT_TEMPLATE.name else FIXTURES / name
    data = path.read_bytes()
    assert read_stored_integrity_word(data) == compute_integrity_word(data)


def test_integrity_word_is_recomputed_for_written_files(tmp_path):
    spike = build_spike()
    assert check_integrity(spike.data)
    # A stored word that does not match the archive must be detected; that is
    # what the recomputation above is for (SketchUp warns on a wrong word).
    header = spike.data.find(b"VFF\x08\x00\x01\x00")
    tampered = bytearray(spike.data)
    tampered[header + 9] ^= 0xFF
    assert not check_integrity(bytes(tampered))


def test_skpwright_verify_when_available(tmp_path, monkeypatch):
    binary = os.environ.get("SKPWRIGHT_BIN")
    if not binary:
        pytest.skip("SKPWRIGHT_BIN is not set; skpwright is an optional verifier")
    spike = build_spike()
    path = tmp_path / "spike.skp"
    path.write_bytes(spike.data)
    result = subprocess.run(
        [binary, "verify", str(path)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr

# ── Stage 2: textured materials, groups and nested placements ───────────


def build_textured(template=None) -> tuple:
    """Two textured faces (one default, one explicitly positioned) + a JPEG."""
    builder = VffBuilder(template)
    png = builder.add_texture_material("TexPNG", TEX_PNG, 10.0, 5.0, image_name="tex.png")
    jpeg = builder.add_texture_material("TexJPEG", TEX_JPEG, 8.0, 4.0, image_name="tex.jpg")
    builder.add_face(
        [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)],
        material=png,
    )
    pins = [
        ((20.0, 0.0, 0.0), (0.0, 0.0)),
        ((30.0, 0.0, 0.0), (1.0, 0.0)),
        ((20.0, 10.0, 0.0), (0.0, 1.0)),
    ]
    builder.add_face(
        [(20.0, 0.0, 0.0), (30.0, 0.0, 0.0), (30.0, 10.0, 0.0), (20.0, 10.0, 0.0)],
        material=png,
        front_uv=pins,
    )
    builder.add_face(
        [(40.0, 0.0, 0.0), (50.0, 0.0, 0.0), (50.0, 10.0, 0.0), (40.0, 10.0, 0.0)],
        material=jpeg,
    )
    return builder.to_bytes(), png, jpeg, pins


def build_groups(template=None) -> tuple:
    """A painted/tagged root group and a component with a nested group."""
    builder = VffBuilder(template)
    red = builder.add_material("GroupRed", (200, 20, 20))
    tag = builder.add_layer("GroupTag", (200, 120, 40))
    inner_tag = builder.add_layer("InnerTag", (20, 140, 220))
    with builder.add_group("RootBox", material=red, layer=tag) as group:
        group.add_face(
            [(0.0, 20.0, 0.0), (10.0, 20.0, 0.0), (10.0, 30.0, 0.0), (0.0, 30.0, 0.0)]
        )
    with builder.add_component_definition("Outer") as outer:
        with outer.add_group("InnerBox", material=red, layer=inner_tag) as inner:
            inner.add_face(
                [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)]
            )
    builder.add_instance(outer, translation=(0.0, 40.0, 0.0), layer=tag)
    builder.add_instance(
        outer,
        translation=(40.0, 40.0, 0.0),
        rotation=((0.0, 0.0, 1.0), math.pi / 6.0),
        layer=tag,
    )
    return builder.to_bytes(), red, tag, inner_tag


def build_stage2_acceptance(template=None) -> bytes:
    """The combined stage-2 model used for SketchUp acceptance."""
    builder = VffBuilder(template)
    png = builder.add_texture_material("TexPNG", TEX_PNG, 10.0, 5.0, image_name="tex.png")
    jpeg = builder.add_texture_material("TexJPEG", TEX_JPEG, 8.0, 4.0, image_name="tex.jpg")
    red = builder.add_material("GroupRed", (200, 20, 20))
    tag = builder.add_layer("GroupTag", (200, 120, 40))
    inner_tag = builder.add_layer("InnerTag", (20, 140, 220))
    builder.add_face(
        [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)],
        material=png,
    )
    pins = [
        ((20.0, 0.0, 0.0), (0.0, 0.0)),
        ((30.0, 0.0, 0.0), (0.5, 0.8660254)),
        ((20.0, 10.0, 0.0), (-0.4330127, 0.25)),
    ]
    builder.add_face(
        [(20.0, 0.0, 0.0), (30.0, 0.0, 0.0), (30.0, 10.0, 0.0), (20.0, 10.0, 0.0)],
        material=png,
        front_uv=pins,
    )
    builder.add_face(
        [(40.0, 0.0, 0.0), (50.0, 0.0, 0.0), (50.0, 10.0, 0.0), (40.0, 10.0, 0.0)],
        material=jpeg,
    )
    with builder.add_group("RootBox", material=red, layer=tag) as group:
        for quad in (
            [(0.0, 20.0, 0.0), (10.0, 20.0, 0.0), (10.0, 30.0, 0.0), (0.0, 30.0, 0.0)],
            [(0.0, 20.0, 10.0), (10.0, 20.0, 10.0), (10.0, 30.0, 10.0), (0.0, 30.0, 10.0)],
        ):
            group.add_face(quad)
    with builder.add_component_definition("Outer") as outer:
        with outer.add_group("InnerBox", material=red, layer=inner_tag) as inner:
            for quad in (
                [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)],
                [(0.0, 0.0, 10.0), (10.0, 0.0, 10.0), (10.0, 10.0, 10.0), (0.0, 10.0, 10.0)],
            ):
                inner.add_face(quad)
    builder.add_instance(outer, translation=(0.0, 40.0, 0.0), layer=tag)
    builder.add_instance(
        outer,
        translation=(40.0, 40.0, 0.0),
        rotation=((0.0, 0.0, 1.0), math.pi / 6.0),
        layer=tag,
    )
    return builder.to_bytes()


def _solve_history_constant(data: bytes):
    """Recover V(ceil(n/2)) from a genuine file's stored integrity word.

    The word is CRC-32(meta[:-4] + table), so the unknown slot value can be
    solved with 32 CRC basis vectors; this reads the constant straight out of
    SketchUp's own file rather than from the recurrence.
    """
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


def test_textured_materials_and_uvs(tmp_path):
    data, png, jpeg, pins = build_textured()
    assert check_integrity(data)
    model = parse_bytes(tmp_path, data)
    root = model.root

    materials = {material.name: material for material in model.materials}
    png_material = materials["TexPNG"]
    jpeg_material = materials["TexJPEG"]
    assert png_material.id == png
    assert jpeg_material.id == jpeg
    assert png_material.texture.data == TEX_PNG
    assert jpeg_material.texture.data == TEX_JPEG
    assert (png_material.texture.width, png_material.texture.height) == (10.0, 5.0)
    assert (jpeg_material.texture.width, jpeg_material.texture.height) == (8.0, 4.0)

    # The image entries are stored next to the material XML, uncompressed
    # (flag 0x0000) exactly as SketchUp writes them.
    entries = {
        entry.name: entry
        for entry in read_zip(data, data.find(b"PK\x03\x04"))
    }
    assert entries["materials/TexPNG/tex.png"].data == TEX_PNG
    assert entries["materials/TexPNG/tex.png"].flag == 0x0000
    assert entries["materials/TexJPEG/tex.jpg"].flag == 0x0000
    assert entries["materials/TexPNG/material.xml"].flag == 0x0006

    faces = sorted(root.faces.values(), key=lambda face: face.id)
    assert [face.material_id for face in faces] == [png, png, jpeg]
    # Default placement stores no matrix; positioned placement does.
    assert faces[0].uv_transform is None
    assert faces[2].uv_transform is None
    expected = (1.0, 0.0, 0.0, 0.0, 2.0, 0.0, 20.0, 0.0, 1.0)
    assert len(faces[1].uv_transform) == 9
    for actual, wanted in zip(faces[1].uv_transform, expected):
        assert actual == pytest.approx(wanted, abs=1e-9)

    # The stored matrix really maps the three pins back to the requested UVs.
    face = faces[1]
    xr, yr = face_uv_basis(face.normal)
    for point, uv in pins:
        assert compute_face_uv(
            point, xr, yr, face.uv_transform, 10.0, 5.0
        ) == pytest.approx(uv, abs=1e-9)


@pytest.mark.parametrize("template_name", ["bundled", "nesting"])
def test_groups_and_nested_placements(tmp_path, template_name):
    template = None if template_name == "bundled" else NESTING_TEMPLATE
    data, red, tag, inner_tag = build_groups(template)
    assert check_integrity(data)
    model = parse_bytes(tmp_path, data)
    root = model.root

    # Root group: group definition (kind 1) with its own face, painted and
    # tagged at the placement.
    group_instance = next(
        instance for instance in root.instances if instance.name == "RootBox"
    )
    group_definition = model.definitions[group_instance.ref_idx]
    assert group_definition.is_group
    assert group_definition.name == "RootBox"
    assert len(group_definition.faces) == 1
    assert group_instance.layer == "GroupTag"
    assert group_instance.material_id == red
    assert tuple(group_instance.matrix[9:12]) == (0.0, 0.0, 0.0)

    # Component "Outer" owns a nested group; both of its placements are in
    # the root with the requested transforms.
    outer_definition = next(
        definition
        for definition in model.definitions.values()
        if definition.name == "Outer"
    )
    assert not outer_definition.is_group
    (nested,) = outer_definition.instances
    assert nested.name == "InnerBox"
    nested_definition = model.definitions[nested.ref_idx]
    assert nested_definition.is_group
    assert nested.layer == "InnerTag"
    assert nested.material_id == red
    assert len(nested_definition.faces) == 1

    outer_instances = [
        instance
        for instance in root.instances
        if instance.ref_idx == outer_definition.id
    ]
    assert len(outer_instances) == 2
    assert {instance.layer for instance in outer_instances} == {"GroupTag"}
    plain, rotated = sorted(outer_instances, key=lambda i: i.matrix[9])
    assert plain.matrix[9:12] == [0.0, 40.0, 0.0]
    c = math.cos(math.pi / 6.0)
    s = math.sin(math.pi / 6.0)
    assert rotated.matrix[:9] == pytest.approx(
        [c, -s, 0.0, s, c, 0.0, 0.0, 0.0, 1.0], abs=1e-12
    )
    assert rotated.matrix[9:12] == [40.0, 40.0, 0.0]


def test_combined_stage2_model_round_trip(tmp_path):
    data = build_stage2_acceptance()
    model = parse_bytes(tmp_path, data)
    root = model.root
    assert check_integrity(data)

    textures = {
        material.name: material.texture
        for material in model.materials
        if material.texture is not None
    }
    assert set(textures) == {"TexPNG", "TexJPEG"}
    assert textures["TexPNG"].data == TEX_PNG
    assert textures["TexJPEG"].data == TEX_JPEG
    assert len(root.faces) == 3
    assert sum(1 for face in root.faces.values() if face.uv_transform) == 1
    assert {instance.name for instance in root.instances} == {"RootBox", ""}
    group = next(instance for instance in root.instances if instance.name == "RootBox")
    assert model.definitions[group.ref_idx].is_group
    assert sum(1 for d in model.definitions.values() if d.is_group) == 2


@pytest.mark.parametrize(
    "path",
    sorted((FIXTURES / "vff_writer").glob("genuine_count_*.skp")),
    ids=lambda path: path.name,
)
def test_history_constant_recovered_from_genuine_saves(path):
    # The per-entry-count constant is solved out of genuine SketchUp saves
    # with 7, 9, 11, 13 and 15 archive entries (k = 4..8) and compared to the
    # value the recurrence produces.
    data = path.read_bytes()
    assert check_integrity(data)
    k, solved = _solve_history_constant(data)
    assert solved == _history_constant(k)


def test_history_constant_covers_a_range_of_entry_counts():
    covered = set()
    for path in (FIXTURES / "vff_writer").glob("genuine_count_*.skp"):
        k, _solved = _solve_history_constant(path.read_bytes())
        covered.add(k)
    assert {4, 5, 6, 7, 8} <= covered


def test_skpwright_verify_stage2_when_available(tmp_path, monkeypatch):
    binary = os.environ.get("SKPWRIGHT_BIN")
    if not binary:
        pytest.skip("SKPWRIGHT_BIN is not set; skpwright is an optional verifier")
    path = tmp_path / "stage2.skp"
    path.write_bytes(build_stage2_acceptance())
    result = subprocess.run(
        [binary, "verify", str(path)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# ── Stage 3: texture path, curves and attribute dictionaries ────────────────


def build_curves(template=None) -> bytes:
    """An open arc, a circle with a circular hole, a polyline and curves in a
    component."""
    builder = VffBuilder(template)
    builder.add_arc((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 10.0, 0.0, math.pi / 2.0, 6)
    hole = builder.add_circle_curve((30.0, 0.0, 0.0), (0.0, 0.0, 1.0), 6.0, 8)
    builder.add_circle(
        (30.0, 0.0, 0.0), (0.0, 0.0, 1.0), 12.0, 12, holes=[hole]
    )
    builder.add_polyline(
        [(0.0, 20.0, 0.0), (10.0, 30.0, 0.0), (20.0, 20.0, 0.0), (30.0, 30.0, 0.0)]
    )
    with builder.add_component_definition("CurveComp") as definition:
        definition.add_circle((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 8.0, 8)
        definition.add_arc(
            (20.0, 0.0, 0.0), (0.0, 0.0, 1.0), 8.0, 0.0, math.pi, 6
        )
        definition.add_polyline([(0.0, 20.0, 0.0), (8.0, 24.0, 0.0), (16.0, 20.0, 0.0)])
    builder.add_instance(definition, translation=(0.0, 40.0, 0.0))
    return builder.to_bytes()


def attribute_values() -> dict:
    return {
        "string": "hello",
        "int": 42,
        "float": 1.5,
        "bool_true": True,
        "bool_false": False,
        "point": Point3d(1.0, 2.0, 3.0),
        "vector": Vector3d(4.0, 5.0, 6.0),
        "length": Length(60.0),
        "timestamp": Timestamp(1700000000),
        "array": [1, "two", 3.5, True],
        "nested": [1, [2, [3, 4]]],
        "nothing": None,
    }


def build_attributes(template=None) -> bytes:
    builder = VffBuilder(template)
    builder.add_face(
        [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)],
        attributes=attribute_values(),
        attribute_dict_name="stage3",
    )
    with builder.add_component_definition(
        "AttrDef", attributes={"definition": "attrs"}
    ) as definition:
        definition.add_face(
            [(20.0, 0.0, 0.0), (30.0, 0.0, 0.0), (30.0, 10.0, 0.0), (20.0, 10.0, 0.0)]
        )
    builder.add_instance(
        definition,
        translation=(0.0, 20.0, 0.0),
        attributes={"instance": "one"},
    )
    with builder.add_group(
        "AttrGroup", attributes={"group": 1}
    ) as group:
        group.add_face(
            [(0.0, 30.0, 0.0), (10.0, 30.0, 0.0), (10.0, 40.0, 0.0), (0.0, 40.0, 0.0)]
        )
    builder.set_model_attributes(
        {"model": "root", "when": Timestamp(1700000001)}
    )
    return builder.to_bytes()


def _face_dictionaries(data: bytes):
    """Every face's decoded dictionaries, through openskp's VFF tree reader."""
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


def _curve_edges(definition):
    grouped = {}
    for edge in definition.edges.values():
        if edge.curve_id is not None:
            grouped.setdefault(edge.curve_id, []).append(edge.id)
    return grouped


def test_texture_filename_is_only_the_basename(tmp_path):
    builder = VffBuilder()
    material = builder.add_texture_material(
        "PathTex", WRITER_FIXTURES / "tex.png", 4.0, 4.0
    )
    builder.add_face(
        [(0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4.0, 4.0, 0.0), (0.0, 4.0, 0.0)],
        material=material,
    )
    data = builder.to_bytes()
    xml = next(
        entry.data
        for entry in read_zip(data, data.find(b"PK\x03\x04"))
        if entry.name == "materials/PathTex/material.xml"
    ).decode("utf-8")
    assert 'textureFilename="tex.png"' in xml
    assert str(WRITER_FIXTURES) not in xml

    model = parse_bytes(tmp_path, data)
    parsed = next(x for x in model.materials if x.name == "PathTex")
    assert parsed.texture.filename == "tex.png"
    assert parsed.texture.data == TEX_PNG


@pytest.mark.parametrize("template_name", ["bundled", "nesting"])
def test_arcs_circles_polylines_and_holes(tmp_path, template_name):
    template = None if template_name == "bundled" else NESTING_TEMPLATE
    data = build_curves(template)
    assert check_integrity(data)
    model = parse_bytes(tmp_path, data)
    root = model.root

    # A circle face with a circular hole: outer ring 12 edges, inner 8.
    (holed,) = [face for face in root.faces.values() if len(face.loops) == 2]
    assert sorted(len(loop) for loop in holed.loops) == [8, 12]

    # Every arc/circle carries its analytic frame and member edge chain.
    arcs = {arc.radius: arc for arc in root.arcs.values()}
    assert sorted(arcs) == [6.0, 10.0, 12.0]
    open_arc = arcs[10.0]
    assert tuple(round(v, 6) for v in open_arc.center) == (0.0, 0.0, 0.0)
    assert tuple(round(v, 6) for v in open_arc.normal) == (0.0, 0.0, 1.0)
    assert open_arc.start_angle == pytest.approx(0.0)
    assert open_arc.end_angle == pytest.approx(math.pi / 2.0)
    for radius in (6.0, 12.0):
        assert arcs[radius].start_angle == pytest.approx(0.0)
        assert arcs[radius].end_angle == pytest.approx(0.0)

    grouped = _curve_edges(root)
    assert sorted(len(edges) for edges in grouped.values()) == [3, 6, 8, 12]
    arc_members = {curve_id for curve_id in grouped if curve_id in root.arcs}
    assert set(root.arcs) == arc_members
    # The 3-edge chain is the polyline: a plain curve, not an ArcCurve.
    plain = [edges for curve_id, edges in grouped.items() if curve_id not in root.arcs]
    assert [len(edges) for edges in plain] == [3]

    # Curves inside the component definition, placed once in the root.
    definition = next(
        d for d in model.definitions.values() if d.name == "CurveComp"
    )
    assert len(definition.faces) == 1
    assert sorted(arc.radius for arc in definition.arcs.values()) == [8.0, 8.0]
    def_grouped = _curve_edges(definition)
    assert sorted(len(edges) for edges in def_grouped.values()) == [2, 6, 8]
    (instance,) = [i for i in root.instances if i.ref_idx == definition.id]
    assert tuple(instance.matrix[9:12]) == (0.0, 40.0, 0.0)


def test_attribute_dictionaries_round_trip(tmp_path):
    data = build_attributes()
    assert check_integrity(data)
    model = parse_bytes(tmp_path, data)

    # Model dictionaries (the template's own plus the new one).
    model_attributes = model.attribute_dictionaries["attributes"]
    assert model_attributes["model"] == "root"
    assert model_attributes["when"] == 1700000001

    # Definition and instance dictionaries.
    definition = next(
        d for d in model.definitions.values() if d.name == "AttrDef"
    )
    assert definition.attribute_dictionaries["attributes"] == {
        "definition": "attrs"
    }
    instance = next(
        i for i in model.root.instances if i.ref_idx == definition.id
    )
    assert instance.attribute_dictionaries["attributes"] == {"instance": "one"}
    group = next(i for i in model.root.instances if i.name == "AttrGroup")
    assert group.attribute_dictionaries["attributes"] == {"group": 1}

    # Face dictionaries, read through openskp's own VFF tree decoder.
    dictionaries = _face_dictionaries(data)
    stage3 = next(d["stage3"] for d in dictionaries if "stage3" in d)
    assert stage3["string"] == "hello"
    assert stage3["int"] == 42
    assert stage3["float"] == 1.5
    assert stage3["point"] == (1.0, 2.0, 3.0)
    assert stage3["vector"] == (4.0, 5.0, 6.0)
    assert stage3["length"] == 60.0
    assert stage3["array"] == [1, "two", 3.5, True]
    assert stage3["nested"] == [1, [2, [3, 4]]]
    assert stage3["nothing"] is None
    assert stage3["bool_true"] is True
    assert stage3["bool_false"] is False
    assert stage3["timestamp"] == 1700000000


def build_stage3_acceptance(template=None) -> bytes:
    """The combined stage-3 acceptance model."""
    builder = VffBuilder(template)
    builder.add_arc((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 10.0, 0.0, math.pi / 2.0, 6)
    hole = builder.add_circle_curve((30.0, 0.0, 0.0), (0.0, 0.0, 1.0), 6.0, 8)
    builder.add_circle(
        (30.0, 0.0, 0.0), (0.0, 0.0, 1.0), 12.0, 12, holes=[hole],
        attributes={"face": "circle"},
    )
    builder.add_polyline(
        [(0.0, 20.0, 0.0), (10.0, 30.0, 0.0), (20.0, 20.0, 0.0), (30.0, 30.0, 0.0)]
    )
    texture = builder.add_texture_material(
        "StageTex", TEX_PNG, 10.0, 5.0, image_name="tex.png"
    )
    builder.add_face(
        [(-40.0, 0.0, 0.0), (-30.0, 0.0, 0.0), (-30.0, 10.0, 0.0), (-40.0, 10.0, 0.0)],
        material=texture,
    )
    builder.add_face(
        [(-40.0, 20.0, 0.0), (-30.0, 20.0, 0.0), (-30.0, 30.0, 0.0), (-40.0, 30.0, 0.0)],
        attributes=attribute_values(),
        attribute_dict_name="stage3",
    )
    with builder.add_component_definition(
        "StageCurveComp", attributes={"definition": "attrs"}
    ) as definition:
        definition.add_circle((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 8.0, 8)
        definition.add_arc(
            (20.0, 0.0, 0.0), (0.0, 0.0, 1.0), 8.0, 0.0, math.pi, 6
        )
        definition.add_polyline([(0.0, 20.0, 0.0), (8.0, 24.0, 0.0), (16.0, 20.0, 0.0)])
    builder.add_instance(
        definition, translation=(0.0, 40.0, 0.0), attributes={"instance": "one"}
    )
    builder.add_instance(
        definition,
        translation=(40.0, 40.0, 0.0),
        rotation=((0.0, 0.0, 1.0), math.pi / 6.0),
        attributes={"instance": "two"},
    )
    red = builder.add_material("GroupRed", (200, 20, 20))
    tag = builder.add_layer("GroupTag", (200, 120, 40))
    with builder.add_group(
        "AttrGroup", material=red, layer=tag, attributes={"group": 1}
    ) as group:
        group.add_face(
            [(0.0, 40.0, 0.0), (10.0, 40.0, 0.0), (10.0, 50.0, 0.0), (0.0, 50.0, 0.0)]
        )
    builder.set_model_attributes({"model": "root", "when": Timestamp(1700000001)})
    return builder.to_bytes()


def test_combined_stage3_model_round_trip(tmp_path):
    data = build_stage3_acceptance()
    model = parse_bytes(tmp_path, data)
    root = model.root
    assert check_integrity(data)
    assert len(root.arcs) == 3
    assert any(len(face.loops) == 2 for face in root.faces.values())
    assert len(_curve_edges(root)) >= 4
    textures = {
        material.name: material.texture
        for material in model.materials
        if material.texture is not None
    }
    assert textures["StageTex"].filename == "tex.png"
    assert textures["StageTex"].data == TEX_PNG


def test_skpwright_verify_stage3_when_available(tmp_path):
    binary = os.environ.get("SKPWRIGHT_BIN")
    if not binary:
        pytest.skip("SKPWRIGHT_BIN is not set; skpwright is an optional verifier")
    path = tmp_path / "stage3.skp"
    path.write_bytes(build_stage3_acceptance())
    result = subprocess.run(
        [binary, "verify", str(path)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_genuine_sketchup_bool_and_time_attributes(tmp_path):
    """The reader decodes AA38/AC38 from a genuine SketchUp 2025 save."""
    data = (WRITER_FIXTURES / "genuine_attributes.skp").read_bytes()
    path = tmp_path / "genuine_attributes.skp"
    path.write_bytes(data)
    model = SkpFile.open(str(path)).parse()

    model_attrs = model.attribute_dictionaries["stage3"]
    assert model_attrs["bool_true"] is True
    assert model_attrs["bool_false"] is False
    assert model_attrs["timestamp"] == 1700000000
    assert model_attrs["nothing"] is None
    assert model_attrs["string"] == "hello"
    assert model_attrs["int"] == 42
    assert model_attrs["float"] == 1.5

    definition = next(
        d for d in model.definitions.values() if d.name == "AttrComp"
    )
    definition_attrs = definition.attribute_dictionaries["stage3"]
    assert definition_attrs["bool_true"] is True
    assert definition_attrs["bool_false"] is False
    assert definition_attrs["timestamp"] == 1700000000

    # The instance and group placements each carry their own stage3 dict.
    for instance in model.root.instances:
        if "stage3" in instance.attribute_dictionaries:
            attrs = instance.attribute_dictionaries["stage3"]
            assert attrs["bool_true"] is True
            assert attrs["bool_false"] is False
            assert attrs["timestamp"] == 1700000000

    face_dicts = _face_dictionaries(data)
    stage3_faces = [d["stage3"] for d in face_dicts if "stage3" in d]
    assert stage3_faces
    for attrs in stage3_faces:
        assert attrs["bool_true"] is True
        assert attrs["bool_false"] is False
        assert attrs["timestamp"] == 1700000000
        assert attrs["nothing"] is None

    # The bool string form matches the classic reader's int-based rendering.
    from openskp._core import _stringify_vff_attr_value
    from openskp.legacy import _stringify_attr_value

    assert _stringify_vff_attr_value(True) == "1"
    assert _stringify_vff_attr_value(False) == "0"
    assert _stringify_vff_attr_value(True) == _stringify_attr_value(1)
    assert _stringify_vff_attr_value(False) == _stringify_attr_value(0)


# ── Stage 4: text, dimensions, guides and section planes ────────────────────

BOX8 = [
    (0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0),
    (0.0, 0.0, 10.0), (10.0, 0.0, 10.0), (10.0, 10.0, 10.0), (0.0, 10.0, 10.0),
]
BOX_QUADS = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]


def build_stage4_acceptance(template=None) -> bytes:
    builder = VffBuilder(template)
    for quad in BOX_QUADS:
        builder.add_face([BOX8[i] for i in quad])
    builder.add_text("Corner label", (0.0, 0.0, 0.0), (10.0, 10.0, 10.0))
    builder.add_text("Face label", (5.0, 5.0, 10.0), (8.0, -4.0, 6.0))
    builder.add_screen_text("Screen label", (15.0, 0.0, 10.0), (30.0, 20.0, 0.0))
    builder.add_dimension((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), offset=8.0, anchor=True)
    builder.add_guide_line((0.0, 0.0, 0.0), (20.0, 10.0, 5.0))
    builder.add_guide_line((0.0, 0.0, 10.0), direction=(0.0, 1.0, 1.0))
    builder.add_guide_point((5.0, 5.0, 5.0))
    section_a = builder.add_section_plane(
        (0.0, 0.0, 5.0), (0.0, 0.0, 1.0),
        name="Section A", symbol="A", active=True,
    )
    builder.add_section_plane(
        (5.0, 0.0, 0.0), (1.0, 0.0, 0.0),
        name="Section B", symbol="B",
    )
    with builder.add_component_definition("Stage4Comp") as definition:
        for quad in BOX_QUADS:
            definition.add_face([BOX8[i] for i in quad])
        definition.add_dimension(
            (0.0, 0.0, 0.0), (10.0, 0.0, 0.0), offset=8.0, anchor=True
        )
    builder.add_instance(definition, translation=(0.0, 30.0, 0.0))
    builder.add_instance(
        definition,
        translation=(30.0, 30.0, 0.0),
        rotation=((0.0, 0.0, 1.0), math.pi / 6.0),
    )
    return builder.to_bytes(), section_a


def test_genuine_sketchup_annotations_are_read():
    path = WRITER_FIXTURES / "genuine_stage4.skp"
    model = SkpFile.open(str(path)).parse()
    root = model.root

    assert [t.text for t in root.texts] == [
        "Corner label", "Face label", "Screen label",
    ]
    assert tuple(round(v, 6) for v in root.texts[0].point) == (10.0, 0.0, 0.0)
    assert tuple(round(v, 6) for v in root.texts[0].label_point) == (20.0, 10.0, 10.0)
    assert tuple(round(v, 6) for v in root.texts[2].point) == (15.0, 15.0, 0.0)

    # One component dimension, but the definition is placed twice.
    assert len(model.dimensions) == 2
    for dimension in model.dimensions:
        assert tuple(round(v, 6) for v in dimension.a) == (10.0, 0.0, 0.0)
        assert tuple(round(v, 6) for v in dimension.b) == (0.0, 0.0, 0.0)
        assert dimension.offset == pytest.approx(-8.0)

    assert len(root.construction_lines) == 2
    bounded, unbounded = root.construction_lines
    assert bounded.start is not None and bounded.end is not None
    assert unbounded.start is None and unbounded.end is None
    assert len(root.construction_points) == 1
    assert tuple(round(v, 6) for v in root.construction_points[0].position) == (
        5.0, 5.0, 5.0,
    )

    sections = {section.name: section for section in root.section_planes}
    assert sections["Section A"].label == "A"
    assert sections["Section A"].plane == [0.0, 0.0, 1.0, -5.0]
    assert sections["Section B"].label == "B"
    assert sections["Section B"].plane == [1.0, 0.0, 0.0, -5.0]

    component = next(
        d for d in model.definitions.values() if d.name == "Stage4Comp"
    )
    assert len(component.dimensions) == 1


def test_writer_annotations_round_trip(tmp_path):
    data, section_a = build_stage4_acceptance()
    assert check_integrity(data)
    path = tmp_path / "stage4.skp"
    path.write_bytes(data)
    model = SkpFile.open(str(path)).parse()
    root = model.root

    assert [t.text for t in root.texts] == [
        "Corner label", "Face label", "Screen label",
    ]
    assert tuple(round(v, 6) for v in root.texts[1].point) == (5.0, 5.0, 10.0)
    assert tuple(round(v, 6) for v in root.texts[1].label_point) == (13.0, 1.0, 16.0)

    assert len(root.dimensions) == 1
    # Root anchored dimension + the component definition's own dimension.
    assert len(model.dimensions) == 2
    root_dimension = model.dimensions[0]
    assert tuple(round(v, 6) for v in root_dimension.a) == (0.0, 0.0, 0.0)
    assert tuple(round(v, 6) for v in root_dimension.b) == (10.0, 0.0, 0.0)

    assert len(root.construction_lines) == 2
    bounded, unbounded = root.construction_lines
    assert tuple(round(v, 6) for v in bounded.start) == (0.0, 0.0, 0.0)
    assert tuple(round(v, 6) for v in bounded.end) == (20.0, 10.0, 5.0)
    assert unbounded.start is None and unbounded.end is None
    assert tuple(round(v, 6) for v in unbounded.direction) == (
        0.0, round(2 ** -0.5, 6), round(2 ** -0.5, 6),
    )
    assert tuple(round(v, 6) for v in root.construction_points[0].position) == (
        5.0, 5.0, 5.0,
    )

    sections = {section.name: section for section in root.section_planes}
    assert sections["Section A"].plane == [0.0, 0.0, 1.0, -5.0]
    assert sections["Section A"].label == "A"
    assert sections["Section B"].plane == [1.0, 0.0, 0.0, -5.0]
    assert sections["Section B"].label == "B"

    # The active section id is recorded in the root entity container.
    from openskp._core import full_parse

    parsed = full_parse(str(path))
    assert parsed["defs_dict"]["ROOT"]["builder"].active_section == section_a

    component = next(
        d for d in model.definitions.values() if d.name == "Stage4Comp"
    )
    assert len(component.dimensions) == 1
    assert len(component.faces) == 6


def test_skpwright_verify_stage4_when_available(tmp_path):
    binary = os.environ.get("SKPWRIGHT_BIN")
    if not binary:
        pytest.skip("SKPWRIGHT_BIN is not set; skpwright is an optional verifier")
    data, _section = build_stage4_acceptance()
    path = tmp_path / "stage4.skp"
    path.write_bytes(data)
    result = subprocess.run(
        [binary, "verify", str(path)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# ── Stage 5: scenes and the model camera ────────────────────────────────────


def build_stage5_acceptance(template=None) -> tuple:
    builder = VffBuilder(template)
    tag_a = builder.add_layer("TagA", (200, 0, 0))
    tag_b = builder.add_layer("TagB", (0, 0, 200))

    def box(offset, tag):
        points = [
            (offset[0] + x, offset[1] + y, offset[2] + z)
            for x, y, z in BOX8
        ]
        for quad in BOX_QUADS:
            builder.add_face([points[i] for i in quad], layer=tag)

    box((0.0, 0.0, 0.0), tag_a)
    box((20.0, 0.0, 0.0), tag_b)
    builder.add_scene(
        "Perspective", (40.0, -40.0, 30.0), (0.0, 0.0, 0.0),
        perspective=True, fov=45.0,
    )
    builder.add_scene(
        "Parallel", (0.0, -60.0, 0.0), (0.0, 0.0, 0.0),
        perspective=False, ortho_height=80.0,
    )
    builder.add_scene(
        "Hide B", (40.0, -40.0, 30.0), (0.0, 0.0, 0.0),
        perspective=True, fov=45.0, hidden_layers=[tag_b],
    )
    builder.set_camera(
        (100.0, -100.0, 100.0), (0.0, 0.0, 0.0),
        perspective=False, ortho_height=200.0,
    )
    return builder.to_bytes(), tag_a, tag_b


def test_genuine_sketchup_scenes_are_read():
    path = WRITER_FIXTURES / "genuine_stage5.skp"
    model = SkpFile.open(str(path)).parse()
    pages = {page.name: page for page in model.pages}
    assert set(pages) == {"Perspective", "Parallel", "Hide B"}

    perspective = pages["Perspective"]
    assert tuple(round(v, 6) for v in perspective.eye) == (40.0, -40.0, 30.0)
    assert tuple(round(v, 6) for v in perspective.target) == (0.0, 0.0, 0.0)
    assert perspective.parallel is False
    assert perspective.fov == pytest.approx(45.0)
    assert perspective.hidden_layers == []

    parallel = pages["Parallel"]
    assert tuple(round(v, 6) for v in parallel.eye) == (0.0, -60.0, 0.0)
    assert tuple(round(v, 6) for v in parallel.up) == (0.0, 0.0, 1.0)
    assert parallel.parallel is True
    assert parallel.ortho_height == pytest.approx(80.0)

    assert pages["Hide B"].hidden_layers == ["TagB"]


def test_writer_scenes_round_trip(tmp_path):
    data, _tag_a, _tag_b = build_stage5_acceptance()
    assert check_integrity(data)
    path = tmp_path / "stage5.skp"
    path.write_bytes(data)
    model = SkpFile.open(str(path)).parse()
    page_list = model.pages
    assert [page.name for page in page_list] == ["Perspective", "Parallel", "Hide B"]

    perspective, parallel, hidden = page_list
    assert tuple(round(v, 6) for v in perspective.eye) == (40.0, -40.0, 30.0)
    assert tuple(round(v, 6) for v in perspective.target) == (0.0, 0.0, 0.0)
    assert perspective.parallel is False
    assert perspective.fov == pytest.approx(45.0)
    assert perspective.hidden_layers == []

    assert tuple(round(v, 6) for v in parallel.eye) == (0.0, -60.0, 0.0)
    assert parallel.parallel is True
    assert parallel.fov == pytest.approx(35.0)
    assert parallel.ortho_height == pytest.approx(80.0)

    assert hidden.hidden_layers == ["TagB"]
    assert hidden.parallel is False
    assert hidden.fov == pytest.approx(45.0)

    layers = {layer.name: layer for layer in model.layers}
    assert {"TagA", "TagB"} <= set(layers)


def test_skpwright_verify_stage5_when_available(tmp_path):
    binary = os.environ.get("SKPWRIGHT_BIN")
    if not binary:
        pytest.skip("SKPWRIGHT_BIN is not set; skpwright is an optional verifier")
    data, _tag_a, _tag_b = build_stage5_acceptance()
    path = tmp_path / "stage5.skp"
    path.write_bytes(data)
    result = subprocess.run(
        [binary, "verify", str(path)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
