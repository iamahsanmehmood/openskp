"""VFF 2021+ annotation/section-plane/scene reading tests.

Fixtures are genuine SketchUp 2025 saves used by the writer prototype and
kept here so the reader is checked without SketchUp.
"""

from pathlib import Path

import pytest

from openskp import SkpFile

WRITER_FIXTURES = Path(__file__).parent / "fixtures" / "vff_writer"


def test_reads_annotations_and_named_section_planes_from_a_sketchup_2025_file():
    model = SkpFile.open(str(WRITER_FIXTURES / "genuine_annotations_2025.skp")).parse()
    root = model.root
    assert [text.text for text in root.texts] == [
        "Corner label", "Face label", "Screen label",
    ]
    sections = {section.name: section for section in root.section_planes}
    assert sections["Section A"].label == "A"
    assert sections["Section A"].plane == pytest.approx([0.0, 0.0, 1.0, -5.0])
    assert sections["Section B"].label == "B"
    assert len(root.construction_lines) == 2
    assert len(root.construction_points) == 1


def test_reads_scene_cameras_and_hidden_tags_from_a_sketchup_2025_file():
    model = SkpFile.open(str(WRITER_FIXTURES / "genuine_scenes_2025.skp")).parse()
    pages = {page.name: page for page in model.pages}
    assert set(pages) == {"Perspective", "Parallel", "Hide B"}
    assert pages["Perspective"].fov == pytest.approx(45.0)
    assert pages["Parallel"].parallel is True
    assert pages["Parallel"].ortho_height == pytest.approx(80.0)
    assert pages["Hide B"].hidden_layers == ["TagB"]
