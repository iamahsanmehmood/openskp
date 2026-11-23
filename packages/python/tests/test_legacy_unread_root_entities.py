"""Classic root reader partial-read reporting (``SkpModel.unread_root_entities``).

The classic root reader keeps what it read before the first record it can't
parse and drops the rest of the root. ``SkpModel.unread_root_entities`` says
how many entities that dropped and ``openskp.legacy`` logs a warning when it
is nonzero, so a partial read is never silent.
"""

from __future__ import annotations

import logging
import pathlib

import pytest

from openskp import SkpFile, create

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    "name",
    [
        "Untitled.skp",
        "capilla_quiroz_v17.skp",
        "gondola_v20.skp",
        "legacy_text_arrows_2017.skp",
        "nesting_2017.skp",
        "SU_File.skp",
    ],
)
def test_complete_files_report_no_unread_root_entities(caplog, name: str) -> None:
    path = FIXTURES / name
    if not path.exists():
        pytest.skip(f"fixture {name} not present")
    with caplog.at_level(logging.WARNING, logger="openskp.legacy"):
        model = SkpFile.open(path).parse()
    assert model.unread_root_entities == 0
    assert caplog.records == []


def test_unreadable_text_drops_rest_of_root_and_warns(tmp_path, caplog) -> None:
    builder = create()
    builder.add_face([(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)])
    builder.add_text("First", (0, 0, 0))
    builder.add_text("Broken", (5, 0, 0))
    builder.add_face([(20, 0, 0), (30, 0, 0), (30, 10, 0), (20, 10, 0)])
    raw = bytearray(builder.to_bytes())

    complete_path = tmp_path / "complete.skp"
    complete_path.write_bytes(raw)
    complete_skp = SkpFile.open(complete_path)
    complete = complete_skp.parse()
    assert len(complete.root.texts) == 2
    assert complete.unread_root_entities == 0
    assert complete_skp.build_scene().unread_root_entities == 0
    assert complete_skp.build_instanced_scene().unread_root_entities == 0

    # Break the second text's string marker so the reader can't find its text.
    marker = b"\x01\xff\xfe\xff\x06B\x00"
    at = raw.find(marker)
    assert at >= 0
    raw[at : at + 4] = b"\x00\x00\x00\x00"

    broken_path = tmp_path / "broken.skp"
    broken_path.write_bytes(raw)
    broken_skp = SkpFile.open(broken_path)
    with caplog.at_level(logging.WARNING, logger="openskp.legacy"):
        model = broken_skp.parse()

    assert len(model.root.texts) == 1
    assert model.root.texts[0].text == "First"
    assert model.unread_root_entities > 0
    assert broken_skp.build_scene().unread_root_entities == model.unread_root_entities
    assert broken_skp.build_instanced_scene().unread_root_entities == model.unread_root_entities
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "stopped early" in m and f"{model.unread_root_entities} unread" in m
        for m in warnings
    )
