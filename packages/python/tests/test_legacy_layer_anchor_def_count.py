"""Regression: a definition count whose low u16 equals a CLayer slot.

After the last CLayer the reader sees ``<anchor u16><def_count u32>`` and
then the first CComponentDefinition. ``_skip_clayer_parent_ref`` consumed
the anchor as a v18 "parent" back-ref whenever both u16s at that position
pointed at CLayer objects - which also happens when the definition count
(e.g. 11, 12 or 13) coincides with a tag's slot. The reader then landed
two bytes early and failed with ``class-ref to non-class slot N (CLayer)``.

Files written by :func:`openskp.create` with one or two custom tags hit
exactly that for some part counts in the 10-14 range below (11-12 with
one tag, 11-13 with two); the neighbouring counts did not. Each case is
written and read back by the library itself.
"""
from __future__ import annotations

import pytest

from openskp import SkpFile, create


def _build(n_tags: int, n_defs: int) -> bytes:
    builder = create()
    mat = builder.add_material("M", (200, 100, 50, 255))
    layers = [builder.add_layer(f"T{i}") for i in range(n_tags)]
    defs = []
    for i in range(n_defs):
        with builder.add_component_definition(f"P{i}") as comp:
            comp.add_face([(0, 0, 0), (1 + i, 0, 0), (1 + i, 1, 0), (0, 1, 0)],
                          material=mat)
        defs.append(comp)
    with builder.add_component_definition("Container") as container:
        for i, comp in enumerate(defs):
            container.add_instance(comp, name=f"i{i}", translation=(0, 2 * i, 0),
                                   layer=layers[0])
    builder.add_instance(container, name="root", layer=layers[0])
    return builder.to_bytes()


@pytest.mark.parametrize("n_defs", [10, 11, 12, 13, 14])
@pytest.mark.parametrize("n_tags", [1, 2])
def test_definition_count_matching_a_tag_slot_round_trips(tmp_path, n_tags, n_defs):
    path = tmp_path / "model.skp"
    path.write_bytes(_build(n_tags, n_defs))

    model = SkpFile.open(path).parse()

    assert [layer.name for layer in model.layers] == (
        ["Layer0"] + [f"T{i}" for i in range(n_tags)])
    # n_defs parts plus the container that instances them
    assert len(model.definitions) == n_defs + 1
