"""API-parity tests: the same model-building code through both writers.

These check that :mod:`openskp.create_vff` follows :mod:`openskp.create`'s
public names, argument order, defaults and ``SkpWriteError`` cases, and that
one model-building function can target either factory and produce the same
faces, materials, tags, definitions, instances and groups.
"""

from __future__ import annotations

import inspect
import math
from pathlib import Path

import pytest

from openskp import SkpFile
from openskp.create import SkpWriteError, create
from openskp.create_vff import create as create_vff

BOX_POINTS = [
    (0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0),
    (0.0, 0.0, 10.0), (10.0, 0.0, 10.0), (10.0, 10.0, 10.0), (0.0, 10.0, 10.0),
]
BOX_FACES = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]


def parse_bytes(tmp_path: Path, data: bytes):
    path = tmp_path / "written.skp"
    path.write_bytes(data)
    return SkpFile.open(str(path)).parse()


# ── API parity and same model code ─────────────────────────────────────


def test_shared_method_signatures_match_create():
    from openskp.create import create as classic_create

    skip = set()
    classic = classic_create()
    vff = create_vff()

    def compare(left, right, label):
        left_params = [p for p in inspect.signature(left).parameters.values() if p.name != "self"]
        right_params = [p for p in inspect.signature(right).parameters.values() if p.name != "self"]
        want = [p.name for p in left_params if p.name not in skip]
        got = [p.name for p in right_params if p.kind != p.KEYWORD_ONLY or p.name in want]
        assert got == want, (label, want, got)
        defaults = {p.name: p.default for p in left_params}
        for param in right_params:
            if param.name in defaults:
                assert repr(param.default) == repr(defaults[param.name]), (label, param.name)

    for name in (
        "to_bytes", "save", "add_material", "add_texture_material", "add_layer",
        "add_face", "add_component_definition", "add_group", "add_instance",
        "add_circle", "add_arc", "add_polyline", "add_text", "add_dimension",
        "set_guid", "set_document_options",
    ):
        compare(getattr(classic, name), getattr(vff, name), name)
    classic_definition = classic.add_component_definition("D")
    vff_definition = vff.add_component_definition("D")
    for name in (
        "add_face", "add_instance", "add_group_instance",
        "add_circle", "add_arc", "add_polyline", "add_text", "add_dimension",
    ):
        compare(
            getattr(classic_definition, name),
            getattr(vff_definition, name),
            "definition." + name,
        )


def test_the_same_model_code_writes_both_formats(tmp_path):
    def build(builder):
        red = builder.add_material("Red", (220, 30, 40))
        texture = builder.add_texture_material(
            "Texture", str(Path(__file__).parent / "fixtures" / "vff_writer" / "tex.png"),
            10.0, 5.0,
        )
        tag = builder.add_layer("Tag", (0, 153, 135))
        # create.py requires every definition/group to be declared before the
        # first root face; both writers accept this same sequence.
        with builder.add_component_definition(
            "Box", attributes={"kind": "component", "n": 3}
        ) as definition:
            for quad in BOX_FACES:
                definition.add_face([BOX_POINTS[i] for i in quad], layer=tag)
            definition.add_circle((5, 5, 20), (0, 0, 1), 2, 8)
            definition.add_arc((5, 5, 30), (0, 0, 1), 2, 0.0, 1.0, 6)
            definition.add_polyline([(0, 0, 40), (5, 0, 40), (5, 5, 40)])
        with builder.add_group(
            "Group", translation=(0.0, 20.0, 0.0), layer=tag,
            attributes={"kind": "group"},
        ) as group:
            group.add_face([BOX_POINTS[i] for i in BOX_FACES[0]])
        for index, quad in enumerate(BOX_FACES):
            builder.add_face(
                [BOX_POINTS[i] for i in quad],
                material=texture if index == 0 else (red if index < 4 else None),
                layer=tag,
                front_uv=(
                    [(0, 0, 0), (0.0, 0.0)],
                    [(10, 0, 0), (1.0, 0.0)],
                    [(0, 10, 0), (0.0, 1.0)],
                ) if index == 0 else None,
                attributes={"face": index},
            )
        builder.add_circle((40, 0, 0), (0, 0, 1), 5, 12)
        builder.add_arc((60, 0, 0), (0, 0, 1), 5, 0.0, math.pi / 2, 8)
        builder.add_polyline([(70, 0, 0), (75, 0, 0), (75, 5, 0)])
        builder.add_instance(
            definition, translation=(20.0, 0.0, 0.0), layer=tag,
            attributes={"kind": "instance"},
        )
        return builder.to_bytes()

    classic_model = parse_bytes(tmp_path, build(create()))
    vff_model = parse_bytes(tmp_path, build(create_vff()))

    def summary(model):
        return {
            "root_faces": len(model.root.faces),
            "root_edges": len(model.root.edges),
            "materials": sorted(
                (m.name, tuple(m.color[:3]))
                for m in model.materials
                if m.texture is None
            ),
            "layers": sorted(
                (layer.name, (layer.color_r, layer.color_g, layer.color_b))
                for layer in model.layers
                if layer.name != "Layer0"
            ),
            "definitions": sorted(
                (d.name, d.is_group, len(d.faces), len(d.instances))
                for d in model.definitions.values()
            ),
            "root_instances": sorted(
                (i.name, model.definitions[i.ref_idx].name, i.layer) for i in model.root.instances
            ),
            "face_vertex_counts": sorted(len(f.loops[0]) for f in model.root.faces.values()),
            "arcs": sorted(
                (round(a.center[0], 6), round(a.center[1], 6), round(a.start_angle, 6), round(a.end_angle, 6))
                for a in model.root.arcs.values()
            ),
            "textures": sorted(
                (m.name, m.texture.filename, round(m.texture.width, 6), round(m.texture.height, 6))
                for m in model.materials if m.texture is not None
            ),
            "definition_attributes": sorted(
                (d.name, sorted(d.attribute_dictionaries.items()))
                for d in model.definitions.values() if d.attribute_dictionaries
            ),
            "instance_attributes": sorted(
                (i.name, tuple(sorted(i.attribute_dictionaries.items())))
                for i in model.root.instances if i.attribute_dictionaries
            ),
        }

    assert summary(classic_model) == summary(vff_model)




def test_set_guid_and_document_options():
    builder = create_vff()
    builder.set_guid("0123456789ABCDEF0123456789ABCDEF")
    builder.set_document_options()
    builder.set_document_options(length_unit=0, length_precision=3)
    with pytest.raises(SkpWriteError):
        builder.set_guid("not-a-guid")
    with pytest.raises(SkpWriteError):
        builder.set_document_options(length_unit=1)


def test_same_text_and_dimension_code_writes_both_formats(tmp_path):
    def build(builder):
        tag = builder.add_layer("Tag")
        builder.add_text("label", (0, 0, 0), (1, 2, 3), layer=tag, hidden=True)
        builder.add_dimension((0, 0, 0), (10, 0, 0), offset_vector=(0, 5, 0), layer=tag)
        return parse_bytes(tmp_path, builder.to_bytes())

    classic = build(create())
    vff = build(create_vff())

    def annotations(model):
        root = model.root
        return {
            "texts": [(t.text, t.layer, t.hidden, t.point, t.label_point) for t in root.texts],
            "dimensions": [(d.a, d.b, round(d.offset, 6), d.layer) for d in root.dimensions],
        }

    assert annotations(classic) == annotations(vff)


def test_text_and_dimension_errors_match_create(tmp_path):
    cases = [
        ("bad text type", lambda b: b.add_text(42, (0, 0, 0))),
        ("bad leader type", lambda b: b.add_text("x", (0, 0, 0), leader_type=9)),
        ("bad screen position", lambda b: b.add_text("x", (0, 0, 0), screen_position=(2, 0))),
        ("dimension both offsets", lambda b: b.add_dimension((0, 0, 0), (1, 0, 0), 1.0, offset_vector=(0, 1, 0))),
        ("dimension same points", lambda b: b.add_dimension((0, 0, 0), (0, 0, 0))),
    ]
    for label, call in cases:
        classic_error = None
        vff_error = None
        try:
            call(create())
        except Exception as exc:
            classic_error = exc
        try:
            call(create_vff())
        except Exception as exc:
            vff_error = exc
        assert classic_error is not None and vff_error is not None, (label, classic_error, vff_error)
        assert type(vff_error) is type(classic_error), (label, vff_error, classic_error)
        assert str(vff_error) == str(classic_error), (label, str(vff_error), str(classic_error))
