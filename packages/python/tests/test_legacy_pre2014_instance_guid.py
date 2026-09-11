"""Regression tests for openskp#284: pre-2014 CComponentInstance/CGroup
records don't carry the trailing 16-byte GUID that 2014+ files do.

Fixtures: legacy_v7_synthetic.skp / legacy_v2014_synthetic.skp - a small,
synthetic model (3 component definitions, a group nested inside one of them
carrying an attribute dictionary, 10 root-level instances) saved from the
same OpenSKP-generated source file, once via a SketchUp version-downgrade
export to v7 and once left at v14 (SketchUp 2014). No real project content;
built specifically for this test.

Before the fix, `_read_instance` decided whether a CComponentInstance/
CGroup record carries a trailing GUID from the class's own reported
``schema`` number (schema >= 5 => GUID present). That doesn't hold: a v7
file's CComponentInstance reports schema 6 - well above that threshold -
yet still has no GUID. Forcing the 16-byte read anyway silently consumes
bytes belonging to the START of the next sibling entity's own tag, which
doesn't raise - it just produces garbage from that point on. Two distinct,
previously-separate-looking symptoms turned out to share this one cause:

- Deep inside a nested CComponentDefinition, the corrupted bytes eventually
  decode as a bogus class-ref, raising "class-ref to non-class slot N
  (CAttributeNamed)" (the exact failure openskp#284 was filed for).
- At the ROOT of the file, `_read_entity_list_inner` catches exactly that
  kind of error and silently treats it as "the declared count over-shot
  the real end of the list" - so instead of raising, the corruption just
  truncated a 10-instance root scene down to 1, with no error at all. That
  is the more dangerous of the two: a parse that "succeeds" while quietly
  dropping 90% of the model.

The real fix gates the GUID read on the file's own version number
(``ar.ver >= 14``) instead of the class schema, which is confirmed correct
on the actual SketchUp v3/v4/v6/v7/v8/2013/2014-2025 spread this bug was
found on (see legacy.py's `_read_instance`).
"""
from __future__ import annotations

import pathlib

from openskp import SkpFile
from openskp.legacy import is_legacy

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
V7 = FIXTURES / "legacy_v7_synthetic.skp"
V2014 = FIXTURES / "legacy_v2014_synthetic.skp"


class TestPre2014InstanceGuidDetection:
    def test_v7_fixture_is_detected_as_legacy(self):
        assert is_legacy(V7.read_bytes()) is True

    def test_v2014_fixture_is_detected_as_legacy(self):
        assert is_legacy(V2014.read_bytes()) is True


class TestPre2014InstanceGuidParse:
    def test_v7_parses_without_the_attribute_named_slot_collision(self):
        # Before the fix, this raised SkpParseError: "class-ref to
        # non-class slot N (CAttributeNamed)" - the InnerFrame group (a
        # CGroup nested inside the Panel definition) forced a GUID read
        # that ate into the definition's own next entity.
        skp = SkpFile.open(str(V7))
        model = skp.parse()
        assert model.version == "{7.0.1}"

    def test_v7_places_every_root_instance(self):
        # Before the fix, the root entity list silently truncated from 10
        # instances to 1: `_read_entity_list_inner`'s own root-list safety
        # net (which exists to tolerate a declared count that over-shoots
        # the real end of the list) caught the corruption from the second
        # instance's mis-read GUID and quietly stopped there instead of
        # raising - a parse that "succeeds" while dropping 90% of the
        # scene, which is worse than a loud failure.
        skp = SkpFile.open(str(V7))
        model = skp.parse()
        names = [inst.name for inst in model.root.instances]
        assert names == ["", "P-1", "P-2", "P-3", "P-4", "P-5", "P-6",
                          "P-7", "P-8", "P-9"]

    def test_v7_and_v2014_parse_to_the_same_structure(self):
        # Same source model, two different container versions - the
        # parsed result should be identical regardless of which era's
        # binary layout it came from.
        v7 = SkpFile.open(str(V7)).parse()
        v14 = SkpFile.open(str(V2014)).parse()

        assert len(v7.definitions) == len(v14.definitions) == 3
        assert len(v7.layers) == len(v14.layers) == 2

        assert [i.name for i in v7.root.instances] == \
            [i.name for i in v14.root.instances]

        v7_defs = {d.name: d for d in v7.definitions.values()}
        v14_defs = {d.name: d for d in v14.definitions.values()}
        assert set(v7_defs) == set(v14_defs) == {"Frame", "Panel", "Post"}
        for name, v7_def in v7_defs.items():
            v14_def = v14_defs[name]
            assert len(v7_def.faces) == len(v14_def.faces)
            assert [i.name for i in v7_def.instances] == \
                [i.name for i in v14_def.instances]

    def test_v7_nested_group_attribute_dictionary_survives(self):
        # The nested CGroup (InnerFrame, inside Panel) is exactly the kind
        # of record the original bug's GUID misread corrupted - assert its
        # own attribute dictionary round-trips correctly, not just that
        # parsing didn't crash.
        skp = SkpFile.open(str(V7))
        model = skp.parse()
        panel = next(d for d in model.definitions.values() if d.name == "Panel")
        assert len(panel.instances) == 1
        inner = panel.instances[0]
        assert inner.name == "InnerFrame"
        assert inner.attribute_dictionaries == {
            "test-info": {"role": "brace", "count": 3, "length": 12.5},
        }

    def test_v2014_instance_guids_are_still_read(self):
        # The fix must not regress the other side of the version gate:
        # 2014+ files genuinely do carry the trailing GUID, and skipping
        # it there would misalign the rest of the file just as badly.
        skp = SkpFile.open(str(V2014))
        model = skp.parse()
        assert len(model.root.instances) == 10
