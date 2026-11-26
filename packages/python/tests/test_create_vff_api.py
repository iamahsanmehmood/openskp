"""API-parity tests: the same model-building code through both writers.

These check that :mod:`openskp.create_vff` follows :mod:`openskp.create`'s
public names, argument order, defaults and ``SkpWriteError`` cases, and that
one model-building function can target either factory and produce the same
faces, materials, tags, definitions, instances and groups.
"""

from __future__ import annotations

import inspect
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

    skip = {"front_uv", "back_uv"}
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
        "to_bytes", "save", "add_material", "add_layer", "add_face",
        "add_component_definition", "add_group", "add_instance",
        "set_guid", "set_document_options",
    ):
        compare(getattr(classic, name), getattr(vff, name), name)
    classic_definition = classic.add_component_definition("D")
    vff_definition = vff.add_component_definition("D")
    for name in ("add_face", "add_instance", "add_group_instance"):
        compare(
            getattr(classic_definition, name),
            getattr(vff_definition, name),
            "definition." + name,
        )


def test_the_same_model_code_writes_both_formats(tmp_path):
    def build(builder):
        red = builder.add_material("Red", (220, 30, 40))
        tag = builder.add_layer("Tag", (0, 153, 135))
        # create.py requires every definition/group to be declared before the
        # first root face; both writers accept this same sequence.
        with builder.add_component_definition("Box") as definition:
            for quad in BOX_FACES:
                definition.add_face([BOX_POINTS[i] for i in quad], layer=tag)
        with builder.add_group("Group", translation=(0.0, 20.0, 0.0), layer=tag) as group:
            group.add_face([BOX_POINTS[i] for i in BOX_FACES[0]])
        for index, quad in enumerate(BOX_FACES):
            builder.add_face(
                [BOX_POINTS[i] for i in quad],
                material=red if index < 4 else None,
                layer=tag,
            )
        builder.add_instance(definition, translation=(20.0, 0.0, 0.0), layer=tag)
        return builder.to_bytes()

    classic_model = parse_bytes(tmp_path, build(create()))
    vff_model = parse_bytes(tmp_path, build(create_vff()))

    def summary(model):
        return {
            "root_faces": len(model.root.faces),
            "root_edges": len(model.root.edges),
            "materials": sorted((m.name, tuple(m.color[:3])) for m in model.materials),
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
