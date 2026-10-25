"""Packed private tables preserve ordered values and mutable loop ownership."""

import struct

import pytest

from openskp._packed_geometry import PackedEdges, PackedFaces, PackedVertices


def face(**changes):
    value = {
        "loops": [[(7, 1), (8, -1)], []],
        "normal": (-0.0, 0.0, 1.0),
        "material_id": None,
        "back_material_id": 0,
        "uv_transform": None,
        "uv_transform_back": None,
        "hidden": False,
        "layer": None,
    }
    value.update(changes)
    return value


def assert_bits(left, right):
    assert type(left) is type(right)
    if isinstance(left, dict):
        assert list(left) == list(right)
        for key in left:
            assert_bits(left[key], right[key])
    elif isinstance(left, (tuple, list)):
        assert len(left) == len(right)
        for a, b in zip(left, right):
            assert_bits(a, b)
    elif isinstance(left, float):
        assert struct.pack("<d", left) == struct.pack("<d", right)
    else:
        assert left == right


@pytest.mark.parametrize(
    "kind,values",
    [
        (PackedVertices, {9: (-0.0, float("nan"), float("inf")), 1: (1.0, 2.0, 3.0)}),
        (PackedEdges, {9: (None, 0), 1: (0, None), 7: (2**64 - 1, 2**63)}),
        (
            PackedFaces,
            {
                9: face(),
                1: face(normal=None, material_id=2**64 - 1, hidden=True, layer=0),
            },
        ),
    ],
)
def test_roundtrip_preserves_every_type_bit_and_key_order(kind, values):
    packed = kind(values)
    assert len(packed) == len(values)
    assert_bits(values, packed.materialize())


@pytest.mark.parametrize(
    "kind,values",
    [
        (PackedVertices, {1: [1.0, 2.0, 3.0], 2: (1, 2, 3)}),
        (PackedEdges, {1: (True, 2), 2: (-1, 2), 3: (2**65, 2)}),
        (
            PackedFaces,
            {
                1: face(uv_transform=(1.0,) * 9),
                2: face(material_id=-1),
                3: face(loops=[[(2**65, 1)]]),
                4: face(loops=[[(1, True)]]),
                5: face(extra="opaque"),
            },
        ),
    ],
)
def test_unsupported_values_stay_verbatim(kind, values):
    packed = kind(values)
    restored = packed.materialize()
    assert_bits(values, restored)
    assert all(restored[key] is value for key, value in values.items())


def test_face_loop_cache_preserves_public_raw_aliasing():
    packed = PackedFaces({5: face()})
    first = next(packed.items())[1]
    first["loops"][0].append((42, -1))
    restored = packed.materialize()
    assert restored[5]["loops"] is first["loops"]
    assert restored[5]["loops"][0][-1] == (42, -1)
    restored[5]["loops"].clear()
    assert next(packed.items())[1]["loops"] == []


def test_fallback_faces_do_not_shift_packed_loop_offsets():
    values = {
        1: face(),
        2: face(uv_projected=True),
        3: face(loops=[]),
        4: face(loops=[[], [(42, -1)], [(7, 1), (8, 1)]]),
    }
    assert_bits(values, PackedFaces(values).materialize())


def large_builder():
    from openskp import _core

    builder = _core._GeometryBuilder()
    for i in range(1024):
        builder.vertices[i] = (float(i), -0.0, 1.0)
        builder.edges[i] = (i, i + 1)
        builder.faces[i] = face()
    return builder


def test_builder_dict_access_is_cached_mutable_and_pickle_compatible():
    import copy
    import pickle

    from openskp._packed_geometry import _PackedTable

    builder = large_builder()
    expected = copy.deepcopy(vars(builder))
    builder._compact_geometry()
    assert all(isinstance(t, _PackedTable) for t in builder._geometry_tables())
    vertices = builder.vertices
    assert type(vertices) is dict
    assert builder.vertices is vertices
    vertices[2000] = (4.0, 5.0, 6.0)
    expected["vertices"][2000] = (4.0, 5.0, 6.0)
    assert_bits(expected, vars(builder))
    assert all(type(t) is dict for t in builder._geometry_tables())
    builder._compact_geometry()
    restored = pickle.loads(pickle.dumps(builder))
    assert_bits(expected, vars(restored))


def test_public_conversion_keeps_concrete_types_and_raw_loop_alias(
    monkeypatch, tmp_path
):
    from openskp import SkpFile, _core
    from openskp._packed_geometry import _PackedTable
    from openskp.model import Edge, Face, Vertex

    builder = large_builder()
    builder._compact_geometry()
    raw = {
        "version": "test",
        "defs_dict": {1: {"builder": builder}},
        "layer_colors": {},
        "materials": {},
    }
    monkeypatch.setattr(_core, "full_parse", lambda path: raw)
    path = tmp_path / "model.skp"
    path.touch()
    skp = SkpFile.open(path)
    definition = skp.parse().definitions[1]
    assert all(isinstance(t, _PackedTable) for t in builder._geometry_tables())
    assert type(definition.vertices) is dict
    assert type(definition.edges) is dict
    assert type(definition.faces) is dict
    assert type(definition.vertices[0]) is Vertex
    assert type(definition.edges[0]) is Edge
    assert type(definition.faces[0]) is Face
    definition.faces[0].loops[0].append((1234, -1))
    assert builder.faces[0]["loops"] is definition.faces[0].loops
    assert builder.faces[0]["loops"][0][-1] == (1234, -1)
    # Scalar assignments on public dataclasses never mutate the raw snapshot.
    definition.faces[0].material_id = 999
    assert builder.faces[0]["material_id"] is None
    assert skp._parsed is raw


def test_real_vff_pipeline_gates_packing_without_changing_fields(monkeypatch, tmp_path):
    import zipfile

    from openskp import SkpFile, _core
    from openskp._packed_geometry import _PackedTable

    def tlv(tag, payload):
        return bytes.fromhex(tag) + struct.pack("<I", len(payload)) + payload

    vertices = b"".join(
        tlv(
            "C409",
            tlv("DC05", tlv("DE05", struct.pack("<I", i)))
            + tlv("C509", struct.pack("<3d", float(i), -0.0, 1.0)),
        )
        for i in range(1024)
    )
    definition = tlv("7C15", tlv("DC05", tlv("DE05", b"\1")) + vertices)
    path = tmp_path / "large.skp"
    path.write_bytes(b"\xff\xfe\xff\x0e")
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("model.dat", tlv("F401", definition))
    eager_skp = SkpFile.open(path)
    eager = eager_skp.parse()
    assert type(eager_skp._parsed["defs_dict"][1]["builder"].vertices) is dict
    monkeypatch.setattr(_core, "_COMPACT_MODEL_BYTES", 0)
    compact_skp = SkpFile.open(path)
    compact = compact_skp.parse()
    builder = compact_skp._parsed["defs_dict"][1]["builder"]
    assert isinstance(builder._geometry_tables()[0], _PackedTable)
    assert compact == eager
    assert_bits(vars(eager_skp._parsed["defs_dict"][1]["builder"]), vars(builder))
