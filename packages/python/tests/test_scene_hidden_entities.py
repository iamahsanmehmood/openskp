from pathlib import Path

import pytest

from openskp import SkpFile
from openskp.model import Definition, Page, SkpModel

CPP_FIXTURES = Path(__file__).resolve().parents[2] / "cpp" / "tests" / "fixtures"

FILES = (
    "scene_hidden_entities_2017.skp",
    "scene_hidden_entities_2019.skp",
    "scene_hidden_entities_2020.skp",
    "scene_hidden_entities_2024.skp",
)


def _hidden(page: Page) -> set[int]:
    return set(page.hidden_entity_ids)


def _placement_ids(model: SkpModel) -> dict[str, int]:
    ids: dict[str, int] = {}

    def add(defn: Definition) -> None:
        for inst in defn.instances:
            if inst.id is not None:
                ids[inst.name] = inst.id

    add(model.root)
    for defn in model.definitions.values():
        add(defn)
    return ids


@pytest.mark.parametrize("name", FILES)
def test_each_scene_lists_what_it_hides(name: str) -> None:
    model = SkpFile.open(CPP_FIXTURES / name).parse()
    assert len(model.pages) == 6
    assert model.pages[0].name == "All"
    assert model.pages[0].hidden_entity_ids == []
    assert model.pages[1].name == "No B, no crate"
    assert _hidden(model.pages[1]) == {71, 73}
    assert model.pages[2].name == "No A, no crate"
    assert _hidden(model.pages[2]) == {70, 73}
    assert model.pages[4].name == "Edge"
    assert _hidden(model.pages[4]) == {17}
    assert model.pages[5].name == "Not saved"
    assert model.pages[5].hidden_entity_ids == []


@pytest.mark.parametrize("name", FILES)
def test_ids_resolve_through_placements(name: str) -> None:
    model = SkpFile.open(CPP_FIXTURES / name).parse()
    assert len(model.pages) == 6
    ids = _placement_ids(model)
    assert "Part A" in ids
    assert "Part B" in ids
    assert "Crate" in ids
    assert _hidden(model.pages[1]) == {ids["Part B"], ids["Crate"]}
    assert _hidden(model.pages[2]) == {ids["Part A"], ids["Crate"]}


@pytest.mark.parametrize("name", FILES)
def test_hidden_edge_is_box_edge(name: str) -> None:
    model = SkpFile.open(CPP_FIXTURES / name).parse()
    assert len(model.pages) == 6
    root = model.root
    assert any(e.persistent_id == 17 for e in root.edges.values())
    assert any(f.persistent_id > 0 for f in root.faces.values())


@pytest.mark.parametrize(
    "name",
    ("scene_hidden_entities_2020.skp", "scene_hidden_entities_2024.skp"),
)
def test_2020_or_newer_keeps_nested_placement(name: str) -> None:
    model = SkpFile.open(CPP_FIXTURES / name).parse()
    assert len(model.pages) == 6
    assert model.pages[3].name == "No C"
    assert _hidden(model.pages[3]) == {100}
    assert _placement_ids(model)["Part C"] == 100


def test_2017_keeps_none_like_sketchup() -> None:
    model = SkpFile.open(CPP_FIXTURES / "scene_hidden_entities_2017.skp").parse()
    assert len(model.pages) == 6
    assert model.pages[3].hidden_entity_ids == []


def test_gondola_scene_lists_hidden_edges() -> None:
    model = SkpFile.open(CPP_FIXTURES / "gondola_v20.skp").parse()
    assert len(model.pages) == 1
    assert len(model.pages[0].hidden_entity_ids) == 12
