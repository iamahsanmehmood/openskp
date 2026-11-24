import pathlib
import struct
import pytest

from openskp import SkpFile, _core, create
from openskp.create import SkpWriteError

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _tlv(tag: int, payload: bytes) -> bytes:
    return struct.pack("<HI", tag, len(payload)) + payload


def _vec3(x: float, y: float, z: float) -> bytes:
    return struct.pack("<3d", x, y, z)


def test_compose_instance_world_nests_parent_times_local():
    panel = _core._GeometryBuilder()
    cabinet = _core._GeometryBuilder()
    cabinet.instances.append({
        "persistent_id": "aa",
        "ref_idx": 2,
        "matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1, 1.0, 2.0, 3.0, 1.0],
    })
    root = _core._GeometryBuilder()
    root.instances.append({
        "persistent_id": "bb",
        "ref_idx": 1,
        "matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1, 10.0, 20.0, 30.0, 1.0],
    })
    defs = {1: {"builder": cabinet}, 2: {"builder": panel}}
    world = {}
    _core._compose_instance_world(root, defs, world)
    assert _core.transform_point((0.0, 0.0, 0.0), world["bb"]) == pytest.approx((10.0, 20.0, 30.0))
    assert _core.transform_point((0.0, 0.0, 0.0), world["aa"]) == pytest.approx((11.0, 22.0, 33.0))


def test_dimension_connected_point_uses_composed_nested_world():
    vid = bytes([0xAA, 0xBB, 0x01])
    iid = bytes([0xCC, 0xDD, 0x02])
    id_len_prefixed = bytes([len(iid)]) + iid
    ref_tlv = _tlv(0x53FC, _tlv(0x53FD, vid) + _tlv(0x53FE, id_len_prefixed))
    conn_a = _tlv(0x5BCD, _tlv(0x5208, _tlv(0x5209, struct.pack("<I", 2)) + _tlv(0x520B, ref_tlv)))
    conn_b = _tlv(0x5BCE, _tlv(0x5208, _tlv(0x5209, struct.pack("<I", 1)) + _tlv(0x520A, _vec3(0.0, 0.0, 0.0))))
    blob = _tlv(0x5BCC, conn_a + conn_b + _tlv(0x5BD2, struct.pack("<d", 0.0)))

    leaf = _core._GeometryBuilder()
    parent_def = _core._GeometryBuilder()
    parent_def.instances.append({
        "persistent_id": iid.hex(),
        "ref_idx": 2,
        "matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1, 1.0, 2.0, 3.0, 1.0],
    })
    root = _core._GeometryBuilder()
    root.instances.append({
        "persistent_id": "parent",
        "ref_idx": 1,
        "matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1, 10.0, 20.0, 30.0, 1.0],
    })
    defs = {1: {"builder": parent_def}, 2: {"builder": leaf}}
    world = {}
    _core._compose_instance_world(root, defs, world)
    dims = _core._parse_dimensions(blob, {vid.hex(): (0.0, 0.0, 0.0)}, world)
    assert len(dims) == 1
    assert dims[0]["a"] == pytest.approx((11.0, 22.0, 33.0))


def test_dimension_connected_point_uses_innermost_instance_in_path():
    vid = bytes([0xAA, 0xBB, 0x01])
    outer_id = bytes([0x11, 0x01])
    inner_id = bytes([0x22, 0x02])
    path = bytes([len(outer_id)]) + outer_id + bytes([len(inner_id)]) + inner_id
    ref_tlv = _tlv(0x53FC, _tlv(0x53FD, vid) + _tlv(0x53FE, path))
    conn_a = _tlv(0x5BCD, _tlv(0x5208, _tlv(0x5209, struct.pack("<I", 2)) + _tlv(0x520B, ref_tlv)))
    conn_b = _tlv(0x5BCE, _tlv(0x5208, _tlv(0x5209, struct.pack("<I", 1)) + _tlv(0x520A, _vec3(0.0, 0.0, 0.0))))
    blob = _tlv(0x5BCC, conn_a + conn_b + _tlv(0x5BD2, struct.pack("<d", 0.0)))

    leaf = _core._GeometryBuilder()
    parent_def = _core._GeometryBuilder()
    parent_def.instances.append({
        "persistent_id": inner_id.hex(),
        "ref_idx": 2,
        "matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1, 100.0, 0.0, 0.0, 1.0],
    })
    root = _core._GeometryBuilder()
    root.instances.append({
        "persistent_id": outer_id.hex(),
        "ref_idx": 1,
        "matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1, 10.0, 0.0, 0.0, 1.0],
    })
    defs = {1: {"builder": parent_def}, 2: {"builder": leaf}}
    world = {}
    _core._compose_instance_world(root, defs, world)
    dims = _core._parse_dimensions(blob, {vid.hex(): (0.0, 0.0, 0.0)}, world)
    assert len(dims) == 1
    assert dims[0]["a"] == pytest.approx((110.0, 0.0, 0.0))


@pytest.mark.parametrize("name", ["annotation_layers_legacy.skp", "annotation_layers_vff.skp"])
def test_sdk_annotation_tags_retain_owner_and_visibility(name):
    model = SkpFile.open(FIXTURES / name).parse()

    def verify(owner, nested: bool):
        def tagged(entities, tag: str, count: int, hidden_count: int):
            assert len(entities) == count
            actual_hidden = 0
            for ent in entities:
                assert ent.layer == tag
                actual_hidden += int(bool(ent.hidden))
            assert actual_hidden == hidden_count

        tagged(owner.dimensions, "尺寸标注", 2, 1)
        assert len(owner.construction_points) == 2
        assert len(owner.construction_lines) == 2
        tagged(owner.texts, "引注文字", 1 if nested else 3, 0 if nested else 1)
        for txt in owner.texts:
            assert txt.display_leader is (txt.text != "Dot")
            if nested:
                assert txt.leader_type == 0
                assert txt.screen_position == pytest.approx((0.23, 0.67))
            else:
                assert txt.point is not None and txt.label_point is not None
                for axis in range(3):
                    expected = 10.0 if axis == 2 else 0.0
                    assert txt.label_point[axis] - txt.point[axis] == pytest.approx(expected)

    verify(model.root, False)
    annotated = 0
    for defn in model.definitions.values():
        if not defn.texts:
            continue
        verify(defn, True)
        annotated += 1
    assert annotated == 1
    if name == "annotation_layers_vff.skp":
        assert len(model.dimensions) == 4
        for dim in model.dimensions:
            assert dim.layer == "尺寸标注"


def test_drawing_properties_come_from_dimension_not_its_connections():
    def free_point(x: float, y: float, z: float) -> bytes:
        return _tlv(
            0x5208,
            _tlv(0x5209, struct.pack("<I", 1))
            + _tlv(0x520A, _vec3(x, y, z))
            + _tlv(0x07D0, _tlv(0x07D2, bytes([99]))),
        )

    body = (
        _tlv(0x59D8, _tlv(0x07D0, _tlv(0x07D2, bytes([12])) + _tlv(0x07D3, bytes([7]))))
        + _tlv(0x5BCD, free_point(0.0, 0.0, 0.0))
        + _tlv(0x5BCE, free_point(10.0, 0.0, 0.0))
        + _tlv(0x5BD2, struct.pack("<d", 3.0))
    )
    dims = _core._parse_dimensions(_tlv(0x5BCC, body), {}, {})
    assert len(dims) == 1
    assert dims[0]["layer"] == "12"
    assert dims[0]["hidden"] is True


def test_annotation_drawing_properties_round_trip(tmp_path):
    builder = create()
    layer = builder.add_layer("Annotations")
    with builder.add_component_definition("TextOnly") as nested:
        nested.add_text(
            "Screen label",
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
            layer=layer,
            hidden=True,
            screen_position=(0.23, 0.67),
        )
    builder.add_instance(nested)
    builder.add_text("Root label", (2.0, 3.0, 4.0), (0.0, 0.0, 5.0), layer=layer, hidden=True)
    builder.add_text("Default tag label", (1.0, 1.0, 1.0), (0.0, 0.0, 2.0))
    builder.add_dimension(
        (0.0, 0.0, 0.0), (10.0, 0.0, 0.0), offset_vector=(0.0, 3.0, 0.0), layer=layer, hidden=True
    )
    builder.add_dimension((0.0, 5.0, 0.0), (10.0, 5.0, 0.0), offset_vector=(0.0, 3.0, 0.0))

    out = tmp_path / "annot_props.skp"
    builder.save(str(out))
    model = SkpFile.open(str(out)).parse()

    assert len(model.root.dimensions) == 2
    assert model.root.dimensions[0].layer == "Annotations"
    assert model.root.dimensions[0].hidden is True
    assert model.root.dimensions[1].layer == ""
    assert model.root.dimensions[1].hidden is False

    assert len(model.root.texts) == 2
    text = model.root.texts[0]
    assert text.layer == "Annotations"
    assert text.hidden is True
    assert text.point == pytest.approx((2.0, 3.0, 4.0))
    assert text.label_point == pytest.approx((2.0, 3.0, 9.0))
    assert model.root.texts[1].layer == ""
    assert model.root.texts[1].hidden is False

    defn = next(d for d in model.definitions.values() if d.name == "TextOnly")
    assert len(defn.texts) == 1
    screen = defn.texts[0]
    assert screen.layer == "Annotations"
    assert screen.hidden is True
    assert screen.leader_type == 0
    assert screen.screen_position == pytest.approx((0.23, 0.67))


def test_leaderless_model_text_retains_both_points_and_visibility(tmp_path):
    builder = create()

    def add_all(sink):
        for ltype in (0, 1, 2):
            for display in (False, True):
                sink.add_text(
                    "Model label",
                    (5.0, 6.0, 7.0),
                    (2.0, 3.0, 4.0),
                    leader_type=ltype,
                    display_leader=display,
                )
        sink.add_text(
            "Screen label",
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
            screen_position=(0.23, 0.67),
        )

    with builder.add_component_definition("Leader visibility") as nested:
        add_all(nested)
    builder.add_instance(nested)
    add_all(builder)

    out = tmp_path / "leader_vis.skp"
    builder.save(str(out))
    model = SkpFile.open(str(out)).parse()

    def verify(owner):
        assert len(owner.texts) == 7
        for i in range(6):
            txt = owner.texts[i]
            assert txt.point == pytest.approx((5.0, 6.0, 7.0))
            assert txt.label_point == pytest.approx((7.0, 9.0, 11.0))
            assert txt.leader_type != 0
            assert txt.display_leader is (i in (3, 5))
        assert owner.texts[6].leader_type == 0
        assert owner.texts[6].screen_position == pytest.approx((0.23, 0.67))

    verify(model.root)
    assert len(model.definitions) == 1
    verify(next(iter(model.definitions.values())))


def test_invalid_annotation_options_leave_archive_usable(tmp_path):
    builder = create()
    with pytest.raises(SkpWriteError):
        builder.add_dimension((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), offset_vector=(0.0, 3.0, 0.0), layer=999)
    with pytest.raises(SkpWriteError):
        builder.add_text("Bad layer", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), layer=999)
    with pytest.raises(SkpWriteError):
        builder.add_text("Bad position", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), screen_position=(-1.0, 0.5))
    builder.add_text("Valid", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0))

    out = tmp_path / "usable.skp"
    builder.save(str(out))
    model = SkpFile.open(str(out)).parse()
    assert model.root.dimensions == []
    assert len(model.root.texts) == 1
    assert model.root.texts[0].text == "Valid"
