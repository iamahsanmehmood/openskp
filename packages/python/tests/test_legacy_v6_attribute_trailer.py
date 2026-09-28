"""Regression test for openskp#284's V6 failure signature: a SketchUp 6
file's ``CAttributeNamed`` record has no trailing field, unlike v7+.

Fixture: legacy_v6_synthetic.skp - a small synthetic model (4 component
definitions: InnerFrame, Frame, Panel, Post; InnerFrame is also placed as a
nested group inside Panel carrying an attribute dictionary; 10 root-level
instances) built with ``openskp.create()`` and saved via a real SketchUp
version-downgrade export to v6. No real project content; built specifically
for this test.

Before the fix, ``_read_attr_named`` unconditionally read a trailing u32
after an attribute dictionary's key/value entries. That trailer is a v7+
addition - a real SketchUp 6 file ends the record at the empty-key
terminator with nothing after it. Reading 4 phantom bytes silently ate into
the next sibling's own tag, which didn't raise there - it surfaced many
reads later, deep in the object graph, as an unrelated "back-ref to
unwalked slot N" error (this is the *same* misleading-error-site pattern
openskp#284 flagged for its V7/V8/2013 cluster, now confirmed to be a
separate cause specific to V6: CAttributeNamed's class is always learned
from the unwalked pre-model region - see ``_new_of_class`` - so its schema
is never observed and can't gate this; the file's own version number is
the only signal available, same as the instance GUID gate in
``_read_instance``).

The fix gates the trailing u32 on ``ar.ver >= 7``, confirmed correct
against this real V6 fixture and cross-checked for no regression on the
existing v7/2014 fixtures (see test_legacy_pre2014_instance_guid.py).
"""
from __future__ import annotations

import pathlib

from openskp import SkpFile
from openskp.legacy import is_legacy

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
V6 = FIXTURES / "legacy_v6_synthetic.skp"


class TestLegacyV6AttributeTrailer:
    def test_v6_fixture_is_detected_as_legacy(self):
        assert is_legacy(V6.read_bytes()) is True

    def test_v6_parses_without_the_unwalked_slot_error(self):
        # Before the fix, this raised SkpParseError: "back-ref to unwalked
        # slot 256" - the phantom trailer read after InnerFrame's "fbd-info"
        # attribute dictionary ate into the group instance's own drawbase
        # and definition-ref fields.
        skp = SkpFile.open(str(V6))
        model = skp.parse()
        assert model.version == "{6.0.1}"

    def test_v6_places_every_root_instance(self):
        skp = SkpFile.open(str(V6))
        model = skp.parse()
        names = [inst.name for inst in model.root.instances]
        assert names == ["", "P-1", "P-2", "P-3", "P-4", "P-5", "P-6",
                          "P-7", "P-8", "P-9"]

    def test_v6_every_definition_present(self):
        skp = SkpFile.open(str(V6))
        model = skp.parse()
        assert {d.name for d in model.definitions.values()} == \
            {"InnerFrame", "Frame", "Panel", "Post"}

    def test_v6_nested_group_attribute_dictionary_survives(self):
        # The nested CGroup (InnerFrame, inside Panel) is exactly the kind
        # of record the phantom trailer corrupted - assert its own
        # attribute dictionary round-trips correctly, not just that
        # parsing didn't crash.
        skp = SkpFile.open(str(V6))
        model = skp.parse()
        panel = next(d for d in model.definitions.values() if d.name == "Panel")
        assert len(panel.instances) == 1
        inner = panel.instances[0]
        assert inner.name == "InnerFrame"
        assert inner.attribute_dictionaries == {
            "fbd-info": {"role": "bracing", "count": 3},
        }
