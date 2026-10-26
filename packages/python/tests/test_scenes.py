"""Scenes (CViewPage) in legacy (v17) files: SkpBuilder.add_scene and the
reader's full scene records (legacy._read_page_list).

The repository's one real file with a scene is gondola_v20.skp (SketchUp
2020). The rest of the ground truth - SketchUp's own 3D Warehouse model
with eight scenes in its SketchUp 2017 format, and SketchUp 2015 saves -
isn't redistributable. So the writer is checked by round trip and on the
specific bytes SketchUp requires, and the reader on the 2020 fixture and
on synthetic schema-12 records. Files written this way were opened in
SketchUp Free (web), where the scenes switch camera and tags as expected.
"""
from __future__ import annotations

import importlib
import math
import struct
from pathlib import Path

import pytest

from openskp import SkpFile, legacy
from openskp.create import SkpWriteError, create

create_module = importlib.import_module("openskp.create")
FIXTURES = Path(__file__).parent / "fixtures"

SQUARE = [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (100.0, 100.0, 0.0), (0.0, 100.0, 0.0)]
PAGE_DECL = b"\xff\xff\x0d\x00\x09\x00CViewPage"
STYLE_DECL = b"\xff\xff\x02\x00\x09\x00CSkpStyle"


def _two_layer_model():
    builder = create()
    wood = builder.add_material("Wood", (200, 160, 120))
    roof = builder.add_layer("Roof")
    plan = builder.add_layer("Plan", hidden=True)
    with builder.add_group("Roof", layer=roof) as g:
        g.add_face(SQUARE, material=wood)
    with builder.add_group("Plan", layer=plan) as g:
        g.add_face([(x + 300.0, y, z) for x, y, z in SQUARE], material=wood)
    return builder, roof, plan


def _parse(tmp_path, data):
    path = tmp_path / "scenes.skp"
    path.write_bytes(data)
    return SkpFile.open(str(path)).parse()


def test_scenes_round_trip(tmp_path):
    builder, roof, plan = _two_layer_model()
    builder.add_scene("Roof", (-200.0, -400.0, 150.0), (50.0, 50.0, 0.0), hidden_layers=[plan])
    builder.add_scene("Plan", (350.0, 50.0, 1000.0), (350.0, 50.0, 0.0),
                      perspective=False, ortho_height=150.0, hidden_layers=[roof])
    builder.add_scene("Both", (0.0, -500.0, 300.0), (150.0, 50.0, 0.0), fov=50.0)
    model = _parse(tmp_path, builder.to_bytes())

    assert [p.name for p in model.pages] == ["Roof", "Plan", "Both"]
    roof_scene, plan_scene, both = model.pages
    assert roof_scene.eye == (-200.0, -400.0, 150.0)
    assert roof_scene.target == (50.0, 50.0, 0.0)
    assert roof_scene.parallel is False and roof_scene.fov == 35.0
    assert roof_scene.hidden_layers == ["Plan"]
    assert plan_scene.parallel is True and plan_scene.ortho_height == 150.0
    assert plan_scene.up == (0.0, 1.0, 0.0)          # looking straight down
    assert plan_scene.hidden_layers == ["Roof"]
    assert both.fov == 50.0 and both.hidden_layers == []
    # default up: world Z made orthogonal to the view direction
    d = [t - e for t, e in zip(roof_scene.target, roof_scene.eye)]
    assert abs(sum(a * b for a, b in zip(d, roof_scene.up))) < 1e-9
    assert roof_scene.up[2] > 0 and math.isclose(math.hypot(*roof_scene.up), 1.0)

    # the geometry is still all there
    assert {d.name for d in model.definitions.values()} >= {"Roof", "Plan"}


def test_scene_list_layout(tmp_path):
    """The bytes SketchUp requires, as it writes them itself: one CViewPage
    declaration (schema 13), the scene count right before it, every scene
    capturing everything (0x7F), and the style moved into the first scene
    with the style manager referring back to it."""
    builder, roof, plan = _two_layer_model()
    builder.add_scene("A", (-200.0, -400.0, 150.0), (50.0, 50.0, 0.0), hidden_layers=[plan])
    builder.add_scene("B", (0.0, -500.0, 300.0), (150.0, 50.0, 0.0))
    data = builder.to_bytes()

    assert data.count(PAGE_DECL) == 1
    decl = data.index(PAGE_DECL)
    assert struct.unpack_from("<I", data, decl - 4)[0] == 2
    assert data.count(STYLE_DECL) == 1
    style = data.index(STYLE_DECL)
    assert decl < style < data.index("B".encode("utf-16-le"), decl + 20)

    # scene A: class decl, no attributes, a pid, name, description, flags
    r = legacy._R(data, decl + len(PAGE_DECL))
    assert r.u16() == 0
    mask = r.u8()
    pid_a = int.from_bytes(r.raw(bin(mask).count("1")), "little")
    assert r.utf16() == "A" and r.utf16() == ""
    assert r.u32() == 0x7F
    assert r.u16() == 0x8007                      # CCamera class-ref

    # the pid counter covers the scenes' pids, which come last
    counter = struct.unpack_from("<I", data, create_module._PID_COUNTER_POS)[0]
    assert pid_a == counter - 1

    # scene B refers back to the style scene A declared: camera (2 + 176),
    # no thumbnail (2), a byte, the rendering options, then the back-ref
    b_name = data.index(legacy._STR_MARKER + b"\x01" + "B".encode("utf-16-le"), style)
    r = legacy._R(data, b_name)
    r.utf16(), r.utf16(), r.u32()
    r.raw(2 + 176 + 2 + 1 + create_module._RENDERING_OPTIONS_LEN)
    style_slot = r.u16()
    # the style manager, where the scaffold declared its style inline: a
    # one-entry list holding a back-ref to that same style, the active
    # style back-ref, then the class-ref of its "current style" copy
    manager = struct.pack("<IHHH", 1, style_slot, style_slot, 0x8000 | (style_slot - 1))
    assert data.count(manager) == 1 and data.index(manager) > b_name


def test_no_scenes_writes_no_scene_list():
    builder, _, _ = _two_layer_model()
    data = builder.to_bytes()
    assert b"CViewPage" not in data[2000:]          # only the version map names it
    assert data.count(STYLE_DECL) == 1


def test_scene_slots_past_the_short_ref_range(tmp_path):
    """Enough geometry that the scenes' objects land beyond slot 0x7FFF,
    so every reference they make needs the 6-byte escape form."""
    builder, roof, plan = _two_layer_model()
    for k in range(2400):
        builder.add_face([(x + 150.0 * (k % 60), y + 150.0 * (k // 60), z - 50.0) for x, y, z in SQUARE])
    builder.add_scene("A", (-200.0, -400.0, 150.0), (50.0, 50.0, 0.0), hidden_layers=[plan])
    builder.add_scene("B", (0.0, -500.0, 300.0), (150.0, 50.0, 0.0), hidden_layers=[roof])
    data = builder.to_bytes()
    assert builder._geometry_writer.next_slot > 0x7FFF
    model = _parse(tmp_path, data)
    assert [(p.name, p.hidden_layers) for p in model.pages] == [("A", ["Plan"]), ("B", ["Roof"])]


@pytest.mark.parametrize("kwargs, message", [
    (dict(name=""), "needs a name"),
    (dict(name="x" * 255), "too long"),
    (dict(name="\U0001F3E0" * 128), "too long"),       # 256 UTF-16 units
    (dict(name="\ud800"), "not valid text"),
    (dict(eye=(1.0, 2.0, 3.0), target=(1.0, 2.0, 3.0)), "coincide"),
    (dict(eye=(1.0, 2.0, 3.0), target=(1.0, 2.0, 3.0), up=(0.0, 0.0, 1.0)), "coincide"),
    (dict(eye=(float("nan"), 0.0, 0.0)), "finite"),
    (dict(up=(0.0, 0.0, 0.0)), "parallel"),
    (dict(up=(0.0, 100.0, -50.0)), "parallel"),
    (dict(fov=0.0), "field of view"),
    (dict(ortho_height=0.0), "ortho_height"),
    (dict(hidden_layers=[9999]), "never returned"),
])
def test_add_scene_rejects_bad_input(kwargs, message):
    builder, _, _ = _two_layer_model()
    args = dict(name="S", eye=(0.0, -100.0, 50.0), target=(0.0, 0.0, 0.0))
    args.update(kwargs)
    with pytest.raises(SkpWriteError, match=message):
        builder.add_scene(**args)


def test_add_scene_rejects_a_duplicate_name():
    builder, _, _ = _two_layer_model()
    builder.add_scene("S", (0.0, -100.0, 50.0), (0.0, 0.0, 0.0))
    with pytest.raises(SkpWriteError, match="already exists"):
        builder.add_scene("S", (0.0, -200.0, 50.0), (0.0, 0.0, 0.0))


def test_hidden_layers_any_iterable_once_each(tmp_path):
    builder, roof, plan = _two_layer_model()
    builder.add_scene("S", (0.0, -100.0, 50.0), (0.0, 0.0, 0.0),
                      hidden_layers=(h for h in [plan, roof, plan]))
    assert _parse(tmp_path, builder.to_bytes()).pages[0].hidden_layers == ["Plan", "Roof"]


# ── reader: a real SketchUp 2020 scene ───────────────────────────────────

def test_reads_the_scene_of_a_real_sketchup_2020_file():
    """gondola_v20.skp: one scene, saved by SketchUp 2020 with flags 0x1FF
    and the 2020-length (213-byte) rendering options."""
    model = SkpFile.open(str(FIXTURES / "gondola_v20.skp")).parse()
    assert len(model.pages) == 1
    scene = model.pages[0]
    assert scene.name == "Cena1"
    assert scene.eye == pytest.approx((2093.78994893, 412.42528040, 135.40419736))
    assert scene.target == pytest.approx((1802.70152746, 121.33685893, -10.14001338))
    assert scene.up == pytest.approx((-0.23570226, -0.23570226, 0.94280904))
    assert scene.parallel is True and scene.ortho_height == pytest.approx(180.650233)
    assert scene.hidden_layers == []


# ── reader: schema 12 (SketchUp 2015 and older) ──────────────────────────

def _schema12_record(name, flags, camera_eye, hidden_ids, first):
    """A SketchUp 2015-shaped scene: no pid, no schema-13 extra bytes,
    sections only for the flags that are set."""
    out = (b"\xff\xff\x0c\x00\x09\x00CViewPage" if first else struct.pack("<H", 0x8000 | 40))
    out += b"\x00\x00"                                     # no attributes
    out += legacy._STR_MARKER + bytes([len(name)]) + name.encode("utf-16-le")
    out += legacy._STR_MARKER + b"\x00"
    out += struct.pack("<I", flags)
    if flags & 0x01:
        out += struct.pack("<H", 0x8007) + create_module._page_camera(
            camera_eye, (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), True, 40.0, 100.0)
    else:
        out += b"\x00\x00"
    out += b"\x00\x00"                                     # no thumbnail
    if flags & 0x20:
        out += struct.pack("<I", len(hidden_ids)) + b"".join(struct.pack("<H", i) for i in hidden_ids)
    out += b"\x01" + struct.pack("<dd", -1.0, -1.0) + b"\x00\x00\x01\x01\x01"
    out += struct.pack("<I", 0)                            # empty thumbnail
    return out


def test_read_page_list_schema12_camera_and_layers():
    data = (bytes(8) + struct.pack("<I", 2)
            + _schema12_record("Front", 0x21, (10.0, -20.0, 5.0), [5, 6], True)
            + _schema12_record("Plain", 0x00, None, [], False)
            + b"\x00\x00")
    ar = legacy._Archive(data, 17)
    ar.r.pos = 0
    ar.slots[5] = ("obj", "CLayer", {"name": "Roof"})
    ar.slots[6] = ("obj", "CLayer", {"name": "Plan"})

    pages = legacy._read_page_list(data, ar)

    assert [p["name"] for p in pages] == ["Front", "Plain"]
    assert pages[0]["eye"] == (10.0, -20.0, 5.0)
    assert pages[0]["fov"] == 40.0 and pages[0]["parallel"] is False
    assert pages[0]["hidden_layer_ids"] == [5, 6]
    assert "eye" not in pages[1] and pages[1]["hidden_layer_ids"] == []


def test_read_page_list_gives_up_on_a_record_it_cannot_read():
    data = (bytes(8) + struct.pack("<I", 1)
            + _schema12_record("Front", 0x21, (10.0, -20.0, 5.0), [5], True)[:-10])
    ar = legacy._Archive(data, 17)
    ar.r.pos = 0
    assert legacy._read_page_list(data, ar) is None


def test_read_page_list_without_scenes_is_empty():
    builder, _, _ = _two_layer_model()
    data = builder.to_bytes()
    ar = legacy._Archive(data, 17)
    ar.r.pos = 0
    assert legacy._read_page_list(data, ar) == []


def test_read_page_list_gives_up_on_unknown_capture_flags():
    data = (bytes(8) + struct.pack("<I", 1)
            + _schema12_record("Front", 0x221, (10.0, -20.0, 5.0), [5], True) + b"\x00\x00")
    ar = legacy._Archive(data, 17)
    ar.r.pos = 0
    assert legacy._read_page_list(data, ar) is None


def test_read_page_list_leaves_an_unknown_schema_to_the_scan():
    record = _schema12_record("Front", 0x21, (10.0, -20.0, 5.0), [5], True)
    data = bytes(8) + struct.pack("<I", 1) + record.replace(b"\x0c\x00\x09\x00", b"\x0e\x00\x09\x00", 1)
    ar = legacy._Archive(data, 17)
    ar.r.pos = 0
    assert legacy._read_page_list(data, ar) is None
