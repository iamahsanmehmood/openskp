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
        builder.add_dimension((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), offset_vector=(0.0, 3.0, 0.0))
    with pytest.raises(SkpWriteError):
        builder.add_text("Bad layer", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), layer=999)
    with pytest.raises(SkpWriteError):
        builder.add_text("Bad position", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), screen_position=(-1.0, 0.5))
    with pytest.raises(SkpWriteError):
        builder.add_text("Short screen", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), screen_position=(0.5,))
    with pytest.raises(SkpWriteError):
        builder.add_text("Long screen", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), screen_position=(0.5, 0.5, 0.5))
    with pytest.raises(SkpWriteError):
        builder.add_text(None, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0))
    with pytest.raises(SkpWriteError):
        builder.add_text("\ud800", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0))
    with pytest.raises(SkpWriteError):
        builder.add_text("Float leader", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), leader_type=1.5)
    with pytest.raises(SkpWriteError):
        builder.add_text("Float arrow", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), arrow_type=1.5)
    with pytest.raises(SkpWriteError):
        builder.add_text("Bool leader", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), leader_type=True)
    with pytest.raises(SkpWriteError):
        builder.add_text("NaN point", (float("nan"), 0.0, 0.0), (1.0, 0.0, 0.0))
    with pytest.raises(SkpWriteError):
        builder.add_text("Inf leader", (0.0, 0.0, 0.0), (float("inf"), 0.0, 0.0))

    # Rejected annotation calls must not lock the geometry writer or prevent adding tags/defs.
    after_layer = builder.add_layer("AfterReject")
    with builder.add_component_definition("DefAfterReject") as nested:
        with pytest.raises(SkpWriteError):
            nested.add_text("\ud800", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0))
        nested.add_text("Nested valid", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), layer=after_layer)
    builder.add_instance(nested)
    builder.add_text("Valid", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), layer=after_layer)

    out = tmp_path / "usable.skp"
    builder.save(str(out))
    model = SkpFile.open(str(out)).parse()
    assert model.root.dimensions == []
    assert len(model.root.texts) == 1
    assert model.root.texts[0].text == "Valid"
    assert model.root.texts[0].layer == "AfterReject"
    defn = next(iter(model.definitions.values()))
    assert len(defn.texts) == 1
    assert defn.texts[0].text == "Nested valid"


def test_vff_doubling_chain_composes_only_referenced_path_and_caps_visits():
    model = SkpFile.open(FIXTURES / "vff_doubling_chain.skp").parse()
    assert len(model.definitions) == 22
    assert len(model.root.instances) == 2

    levels = 22
    defs = {0: {"builder": _core._GeometryBuilder()}}
    path_bytes = bytearray()
    for i in range(1, levels):
        b = _core._GeometryBuilder()
        for j, tx in enumerate((0.0, 10.0)):
            pid_raw = bytes([i, j + 1])
            b.instances.append({
                "persistent_id": pid_raw.hex(),
                "ref_idx": i - 1,
                "matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1, tx, 0.0, 0.0, 1.0],
            })
        defs[i] = {"builder": b}

    def make_root():
        rb = _core._GeometryBuilder()
        for j, tx in enumerate((0.0, 10.0)):
            pid_raw = bytes([levels, j + 1])
            rb.instances.append({
                "persistent_id": pid_raw.hex(),
                "ref_idx": levels - 1,
                "matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1, tx, 0.0, 0.0, 1.0],
            })
        return rb

    for i in range(levels, 0, -1):
        pid_raw = bytes([i, 2])
        path_bytes.append(len(pid_raw))
        path_bytes.extend(pid_raw)

    vid = bytes([0xAA, 0xBB, 0x01])
    vid_b = bytes([0xAA, 0xBB, 0x02])
    id2pos = {
        vid.hex(): (4.0, 0.0, 0.0),
        vid_b.hex(): (5.0, 0.0, 0.0),
    }

    def dim_blob(raw_53fe: bytes) -> bytes:
        ref_tlv = _tlv(0x53FC, _tlv(0x53FD, vid) + _tlv(0x53FE, raw_53fe))
        conn_a = _tlv(0x5BCD, _tlv(0x5208, _tlv(0x5209, struct.pack("<I", 2)) + _tlv(0x520B, ref_tlv)))
        conn_b = _tlv(0x5BCE, _tlv(0x5208, _tlv(0x5209, struct.pack("<I", 1)) + _tlv(0x520A, _vec3(0.0, 0.0, 0.0))))
        return _tlv(0x5BCC, conn_a + conn_b + _tlv(0x5BD2, struct.pack("<d", 0.0)))

    # Empty 53FE visits zero placements on the 2^22 doubling chain.
    rb0 = make_root()
    assert _core._assign_vff_annotations(dim_blob(b""), id2pos, rb0, defs, max_visits=5) == 0
    assert rb0.dimensions[0]["a"] == pytest.approx((4.0, 0.0, 0.0))

    # Full 22-step path resolves in 22 visits (well under max_visits=64 << 2^22).
    rb_ok = make_root()
    world_ok = []
    visits_ok = _core._assign_vff_annotations(
        dim_blob(bytes(path_bytes)), id2pos, rb_ok, defs, world_ok, max_visits=64
    )
    assert visits_ok == levels
    assert rb_ok.dimensions[0]["a"] == pytest.approx((224.0, 0.0, 0.0))
    assert world_ok[0]["a"] == pytest.approx((224.0, 0.0, 0.0))

    # Tighter visit cap stops mid-path at max_visits=10.
    rb_cap = make_root()
    assert _core._assign_vff_annotations(
        dim_blob(bytes(path_bytes)), id2pos, rb_cap, defs, max_visits=10
    ) == 10
    assert rb_cap.dimensions[0]["a"] == pytest.approx((4.0, 0.0, 0.0))

    # Unresolvable 53FE skips the 2^22 chain completely via the PID holder index (0 visits).
    rb_miss = make_root()
    bogus = bytes([4, 0xDE, 0xAD, 0xBE, 0xEF])
    assert _core._assign_vff_annotations(dim_blob(bogus), id2pos, rb_miss, defs, max_visits=25) == 0

    # (1a) A definition placed in root AFTER the 2^22 doubling chain resolves its world
    # dimension without walking the chain.
    def make_after_chain_graph():
        g_defs = {}
        for k in defs:
            nb = _core._GeometryBuilder()
            nb.instances.extend(defs[k]["builder"].instances)
            g_defs[k] = {"builder": nb}
        sub_leaf = _core._GeometryBuilder()
        g_defs[101] = {"builder": sub_leaf}
        after_b = _core._GeometryBuilder()
        after_b.instances.append({
            "persistent_id": bytes([0x65, 0x01]).hex(),
            "ref_idx": 101,
            "matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1, 3.0, 0.0, 0.0, 1.0],
        })
        g_defs[100] = {"builder": after_b}
        rb = make_root()
        rb.instances.append({
            "persistent_id": bytes([0x64, 0x01]).hex(),
            "ref_idx": 100,
            "matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1, 5.0, 0.0, 0.0, 1.0],
        })
        return rb, g_defs

    vid_zero = bytes([0xAA, 0xBB, 0x00])
    after_id2pos = {
        vid_zero.hex(): (0.0, 0.0, 0.0),
        vid_b.hex(): (5.0, 0.0, 0.0),
    }
    sub_path = bytes([2, 0x65, 0x01])
    ref_a = _tlv(0x53FC, _tlv(0x53FD, vid_zero) + _tlv(0x53FE, sub_path))
    ref_b = _tlv(0x53FC, _tlv(0x53FD, vid_b) + _tlv(0x53FE, sub_path))
    after_dim = _tlv(
        0x5BCC,
        _tlv(0x5BCD, _tlv(0x5208, _tlv(0x5209, struct.pack("<I", 2)) + _tlv(0x520B, ref_a)))
        + _tlv(0x5BCE, _tlv(0x5208, _tlv(0x5209, struct.pack("<I", 2)) + _tlv(0x520B, ref_b)))
        + _tlv(0x5BD2, struct.pack("<d", 0.0)),
    )
    def_100_span = _tlv(
        0x01F9,
        _tlv(0x1770, _tlv(0x1771, _tlv(0x157C, _tlv(0x05DC, _tlv(0x05DE, bytes([100]))) + after_dim))),
    )
    rb_after, defs_after = make_after_chain_graph()
    world_after = []
    visits_after = _core._assign_vff_annotations(
        def_100_span, after_id2pos, rb_after, defs_after, world_after, max_visits=16
    )
    assert visits_after <= 10
    assert defs_after[100]["builder"].dimensions[0]["a"] == pytest.approx((3.0, 0.0, 0.0))
    assert defs_after[100]["builder"].dimensions[0]["b"] == pytest.approx((8.0, 0.0, 0.0))
    assert world_after[0]["a"] == pytest.approx((8.0, 0.0, 0.0))
    assert world_after[0]["b"] == pytest.approx((13.0, 0.0, 0.0))

    # (1b) Repeated dangling 53FE paths starting mid-chain whose tail PID does not exist
    # cache the miss and never walk the 2^22 chain.
    dangling_mid = bytes([2, 10, 2, 4, 0xDE, 0xAD, 0xBE, 0xEF])
    rb_repeat = make_root()
    visits_repeat = _core._assign_vff_annotations(
        dim_blob(dangling_mid) * 20, id2pos, rb_repeat, defs, max_visits=16
    )
    assert visits_repeat <= 5

    # (1c) 60 distinct dangling paths cannot exhaust the per-query visit budget and starve
    # a subsequent valid dimension.
    dangling_blobs = b"".join(
        dim_blob(bytes([2, 10, 2, 2, 0xEE, k])) for k in range(60)
    )
    rb_starve, defs_starve = make_after_chain_graph()
    world_starve = []
    _core._assign_vff_annotations(
        dangling_blobs + def_100_span,
        {**id2pos, **after_id2pos},
        rb_starve,
        defs_starve,
        world_starve,
        max_visits=16,
    )
    assert world_starve[-1]["a"] == pytest.approx((8.0, 0.0, 0.0))
    assert world_starve[-1]["b"] == pytest.approx((13.0, 0.0, 0.0))
