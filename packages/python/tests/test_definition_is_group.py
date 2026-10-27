"""``Definition.is_group``: whether SketchUp made a definition for a group or a component (#446).

Fixtures: ``nesting_<version>.skp`` - the group "Outer" holds an instance of the
component "Shelf", which holds the group "Inner", a face at each level. Built
and saved through the installed SketchUp library as SketchUp 6, 2013, 2017,
2020 and 2021 (the last is the VFF container, the rest the classic one). The
library reports two group definitions and one component definition for each
(``SUModelGetNumGroupDefinitions`` / ``SUModelGetNumComponentDefinitions``).
No private content.
"""
from __future__ import annotations

import pathlib

import pytest

from openskp import SkpFile

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
NAMES = ["nesting_v6.skp", "nesting_2013.skp", "nesting_2017.skp", "nesting_2020.skp", "nesting_2021.skp"]


def _definition(model, instance):
    return model.definitions[instance.ref_idx]


@pytest.mark.parametrize("name", NAMES)
def test_groups_and_the_component_are_told_apart(name):
    model = SkpFile.open(str(FIXTURES / name)).parse()
    definitions = list(model.definitions.values())
    assert sum(d.is_group for d in definitions) == 2
    assert sum(not d.is_group for d in definitions) == 1


@pytest.mark.parametrize("name", NAMES)
def test_the_flag_follows_the_nesting(name):
    model = SkpFile.open(str(FIXTURES / name)).parse()
    (outer,) = model.root.instances
    assert outer.name == "Outer"
    assert _definition(model, outer).is_group is True

    (shelf,) = _definition(model, outer).instances
    assert _definition(model, shelf).name == "Shelf"
    assert _definition(model, shelf).is_group is False

    (inner,) = _definition(model, shelf).instances
    assert inner.name == "Inner"
    assert _definition(model, inner).is_group is True


@pytest.mark.parametrize("name", NAMES)
def test_a_definition_placed_once_is_not_taken_for_a_group(name):
    # "Shelf" is placed exactly once, which is what the old guess ("placed once
    # means group") got wrong
    model = SkpFile.open(str(FIXTURES / name)).parse()
    placements = {}
    for definition in list(model.definitions.values()) + [model.root]:
        for instance in definition.instances:
            placements[instance.ref_idx] = placements.get(instance.ref_idx, 0) + 1
    shelf = next(i for i, d in model.definitions.items() if d.name == "Shelf")
    assert placements[shelf] == 1
    assert model.definitions[shelf].is_group is False


def test_an_image_is_not_a_group():
    # the image kind and the group kind share one field in a 2021+ file
    from openskp import SkpFile as File

    for path in sorted(FIXTURES.glob("*.skp")):
        try:
            model = File.open(str(path)).parse()
        except Exception:
            continue
        for definition in model.definitions.values():
            assert not (definition.is_image and definition.is_group), path.name


def test_a_group_written_by_the_builder_is_a_group(tmp_path):
    # SketchUp 2025's own library reports one group definition and one component definition for a
    # file like this; the flag is the definition's own, not inferred from how it is placed
    from openskp import create

    builder = create()
    with builder.add_group("Table", translation=(50, 0, 0)) as table:
        table.add_face([(0, 0, 0), (30, 0, 0), (30, 30, 0), (0, 30, 0)])
    with builder.add_component_definition("Chair") as chair:
        chair.add_face([(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)])
    builder.add_instance(chair)
    path = tmp_path / "written.skp"
    builder.save(str(path))
    model = SkpFile.open(str(path)).parse()
    by_name = {d.name: d.is_group for d in model.definitions.values()}
    assert by_name == {"Table": True, "Chair": False}
