"""Legacy (MFC) text labels anchored to an entity.

16 bytes after its font, a CText carries the connection block dimensions
use, [u32 type][u32 4][point3d]: type 0 or 1 when the label is anchored to
nothing (1 for a leader pointing at a free point stored inline), other
types (2, 4, 5 and 7 seen) for a label anchored to an entity. As in a
dimension, the block is followed by the anchored entity's ref and its
instance paths, null and empty for types 0 and 1. MFC writes an object in
full the first time it is referenced, so a path can hold a whole NEW
instance: the component a label is anchored to, when the label comes first
in its entity list. The reader skipped the block as bytes, so that instance
never got its store-map slots and every later reference was off by them
("class-ref to non-class slot"): the auditorium (CR4) models of the BRAS
database, whose loudspeaker labels are anchored to the loudspeakers, did
not open, and two of its coupled-room (CR1) models lost root entities (one
read 5 of its 318). The entity list's declared count holds such an
instance, but SketchUp does not always list it again with a back-reference.
"""
import struct
from pathlib import Path

import pytest

from openskp import SkpFile
from openskp.legacy import _READERS, _Archive, _R, _read_entity_list, _read_text

FIXTURES = Path(__file__).parent / 'fixtures'

TEXT, FONT_CLASS, FONT, THING = 1, 2, 3, 4      # pre-registered slots
FIRST_FREE = 5


def archive(data: bytes) -> _Archive:
    """A SketchUp 2015 archive that knows CText, a font and a stand-in
    class for the anchored instance, whose objects are 3 bytes long."""
    ar = _Archive(data, 15)
    ar.readers.update(_READERS)
    ar.readers['CThing'] = lambda ar, r: {'k': 'thing', 'b': r.raw(3)}
    ar.slots.update({TEXT: ('class', 'CText', 0),
                     FONT_CLASS: ('class', 'CSkFont', 0),
                     FONT: ('obj', 'CSkFont', {}),
                     THING: ('class', 'CThing', 0)})
    ar.class_slot.update({'CText': TEXT, 'CSkFont': FONT_CLASS,
                          'CThing': THING})
    ar.next_slot = FIRST_FREE
    return ar


def text_record(kind: int, point=(0.0, 0.0, 0.0), refs: bytes = b'',
                label=(0.0, 0.0, 0.0), text: str = 'LS1') -> bytes:
    """A CText's bytes after its class tag. *refs* is what follows the
    connection block: the anchored entity's ref and its instance paths."""
    return (b'\x00\x00'                        # no attribute container
            + bytes(8) + b'\x00\x00'           # draw block, layer 0
            + struct.pack('<H', FONT)          # the font, a back-reference
            + bytes(16)
            + struct.pack('<II3d', kind, 4, *point)
            + (refs or bytes(12))
            + struct.pack('<3d', *label)
            + struct.pack('<3d', -0.577, -0.577, -0.577)
            + b'\x02\x00\x00\x00'
            + b'\x01\x00\x00\x00\x01\x00\x03\x00\x00\x00\x01'   # arrow 3
            + b'\xff\xfe\xff' + bytes([len(text)]) + text.encode('utf-16-le')
            + bytes(5))


def new_thing() -> bytes:
    return struct.pack('<H', 0x8000 | THING) + b'OBJ'


def paths_with_new_instance() -> bytes:
    """Anchored to entity 9 inside an instance written here in full."""
    return (struct.pack('<H', 9) + b'\x00\x00' + struct.pack('<I', 1)
            + new_thing() + struct.pack('<I', 0))


@pytest.mark.parametrize('kind,point', [(2, (0.0, 0.0, 0.0)),
                                        (4, (0.0, 0.0, 0.0)),
                                        (5, (12.5, -3.0, 40.0)),
                                        (7, (1.0, 2.0, 3.0))])
def test_an_anchored_label_reads_the_instance_written_in_its_path(kind, point):
    data = text_record(kind, point, paths_with_new_instance()) + b'TAIL'
    ar = archive(data)
    r = _R(data, 0)
    out = _read_text(ar, r)
    assert out['text'] == 'LS1'
    assert out['connect'] == 9
    assert out['paths'] == (None, [FIRST_FREE], [])
    assert ar.slots[FIRST_FREE] == ('obj', 'CThing', {'k': 'thing', 'b': b'OBJ'})
    assert out['p'] is None                  # not a free point
    assert data[r.pos:] == b'TAIL'


def test_a_free_label_keeps_its_point_and_label_position():
    data = text_record(1, (1.0, 2.0, 3.0), label=(4.0, 5.0, 6.0)) + b'TAIL'
    r = _R(data, 0)
    out = _read_text(archive(data), r)
    assert out['p'] == (1.0, 2.0, 3.0)
    assert out['lp'] == (4.0, 5.0, 6.0)
    assert out['paths'] is None
    assert data[r.pos:] == b'TAIL'


def definition_tail() -> bytes:
    """No relationships, then a GUID and the definition's name."""
    return (bytes(4) + bytes(2) + bytes(16)
            + b'\xff\xfe\xff\x02' + 'LS'.encode('utf-16-le'))


def test_an_instance_written_in_a_label_and_never_listed_again_is_kept():
    data = (struct.pack('<H', 0x8000 | TEXT)
            + text_record(2, refs=paths_with_new_instance())
            + definition_tail())
    ar = archive(data)
    r = _R(data, 0)
    ents = _read_entity_list(ar, r, 2, 'def')
    assert [(slot, name) for slot, name, _ in ents] == [
        (FIRST_FREE, 'CText'), (FIRST_FREE + 1, 'CThing')]
    assert data[r.pos:] == definition_tail()


def test_an_instance_written_in_a_label_and_listed_again_is_listed_once():
    data = (struct.pack('<H', 0x8000 | TEXT)
            + text_record(2, refs=paths_with_new_instance())
            + struct.pack('<H', FIRST_FREE + 1)        # its back-reference
            + definition_tail())
    ar = archive(data)
    r = _R(data, 0)
    ents = _read_entity_list(ar, r, 2, 'def')
    assert [(slot, name) for slot, name, _ in ents] == [
        (FIRST_FREE, 'CText'), (FIRST_FREE + 1, 'CThing')]
    assert data[r.pos:] == definition_tail()


def test_only_the_outermost_instance_of_a_path_is_one_of_the_list_s_items():
    """A path runs from the outermost instance in; the inner ones live in
    other definitions."""
    refs = (struct.pack('<H', 9) + b'\x00\x00' + struct.pack('<I', 2)
            + new_thing() + new_thing() + struct.pack('<I', 0))
    data = (struct.pack('<H', 0x8000 | TEXT) + text_record(2, refs=refs)
            + definition_tail())
    ar = archive(data)
    r = _R(data, 0)
    ents = _read_entity_list(ar, r, 2, 'def')
    assert [(slot, name) for slot, name, _ in ents] == [
        (FIRST_FREE, 'CText'), (FIRST_FREE + 1, 'CThing')]
    assert ar.slots[FIRST_FREE + 2][1] == 'CThing'   # read, not listed
    assert data[r.pos:] == definition_tail()


def test_vertex_anchor_resolves_only_without_extra_or_instance_paths():
    direct_refs = struct.pack('<H', 9) + b'\x00\x00' + bytes(8)
    direct = text_record(2, (0.0, 0.0, 0.0), refs=direct_refs) + b'TAIL'
    r1 = _R(direct, 0)
    out1 = _read_text(archive(direct), r1)
    assert out1['vertex'] == 9
    assert direct[r1.pos:] == b'TAIL'

    extra_refs = struct.pack('<HH', 9, 8) + bytes(8)
    with_extra = text_record(2, (0.0, 0.0, 0.0), refs=extra_refs) + b'TAIL'
    r2 = _R(with_extra, 0)
    out2 = _read_text(archive(with_extra), r2)
    assert out2['paths'] == (8, [], [])
    assert out2['vertex'] is None
    assert with_extra[r2.pos:] == b'TAIL'


def test_text_delimiter_and_unanchored_vertex_scan_respect_record_bounds():
    # An inline CSkFont ending in delimiter-like bytes followed immediately by
    # FF FE FF inside screen fractions must not match across font_end.
    inline_font = (struct.pack('<H', 0x8000 | FONT_CLASS)
                   + b'\x01\x00\x00\x00\x01\x00\x03\x00\x00\x00\x01')
    early_marker = b'\xff\xfe\xff\x00' + bytes(12)
    rec1 = (b'\x00\x00' + bytes(8) + b'\x00\x00' + inline_font + early_marker
            + struct.pack('<II3d', 1, 4, 1.0, 2.0, 3.0) + bytes(12)
            + struct.pack('<3d', 4.0, 5.0, 6.0)
            + struct.pack('<3d', -0.577, -0.577, -0.577) + b'\x02\x00\x00\x00'
            + b'\x01\x00\x00\x00\x01\x00\x03\x00\x00\x00\x01'
            + b'\xff\xfe\xff\x03' + 'LS1'.encode('utf-16-le') + bytes(5) + b'TAIL')
    ar1 = archive(rec1)
    ar1.readers['CSkFont'] = lambda _ar, rr: rr.raw(11)
    r1 = _R(rec1, 0)
    out1 = _read_text(ar1, r1)
    assert out1['text'] == 'LS1'
    assert out1['p'] == (1.0, 2.0, 3.0)
    assert rec1[r1.pos:] == b'TAIL'

    # Unanchored ctype == 2 abutting label_point (q + 32 == idx - 63) must not
    # read label_point's low u16 as a CVertex slot.
    crafted_label_x = struct.unpack('<d', b'\x09\x00\x00\x00\x00\x00\x10\x40')[0]
    rec2 = (b'\x00\x00' + bytes(8) + b'\x00\x00' + struct.pack('<H', FONT)
            + bytes(6) + struct.pack('<II3d', 2, 4, 0.0, 0.0, 0.0)
            + struct.pack('<3d', crafted_label_x, 5.0, 6.0)
            + struct.pack('<3d', -0.577, -0.577, -0.577) + b'\x02\x00\x00\x00'
            + b'\x01\x00\x00\x00\x01\x00\x03\x00\x00\x00\x01'
            + b'\xff\xfe\xff\x03' + 'LS1'.encode('utf-16-le') + bytes(5) + b'TAIL')
    r2 = _R(rec2, 0)
    out2 = _read_text(archive(rec2), r2)
    assert out2['vertex'] is None
    assert out2['lp'] == (crafted_label_x, 5.0, 6.0)
    assert rec2[r2.pos:] == b'TAIL'


def test_classic_leader_and_screen_text_fixtures_match_sdk():
    model = SkpFile.open(FIXTURES / 'classic_text_anchors_2017.skp').parse()
    texts = model.root.texts
    assert [t.text for t in texts] == [
        'Free callout', 'Vertex callout', 'View callout', 'Screen note']

    free, vertex, view, screen = texts
    assert (free.point, free.label_point) == ((1.0, 2.0, 3.0), (5.0, 7.0, 9.0))
    assert (free.screen_position, free.leader_type, free.arrow_type) == ((0.0, 0.0), 2, 3)
    assert (free.display_leader, free.hidden, free.layer) == (True, False, '')

    assert (vertex.point, vertex.label_point) == ((20.0, 30.0, 0.0), (10.0, 12.0, 14.0))
    assert (vertex.screen_position, vertex.leader_type, vertex.arrow_type) == ((0.5, 0.5), 2, 4)
    assert (vertex.display_leader, vertex.hidden, vertex.layer) == (True, False, '')

    assert (view.point, view.label_point) == ((3.0, 4.0, 5.0), (6.0, 7.0, 8.0))
    assert (view.screen_position, view.leader_type, view.arrow_type) == ((0.5, 0.5), 1, 1)
    assert (view.display_leader, view.hidden, view.layer) == (True, False, '')

    assert (screen.point, screen.label_point) == ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    assert screen.screen_position == pytest.approx((0.35, 0.65))
    assert (screen.leader_type, screen.arrow_type, screen.display_leader) == (0, 0, True)
    assert (screen.hidden, screen.layer) == (False, '')

    v20 = SkpFile.open(FIXTURES / 'sdk_saved_leaderless_text_edges_2020.skp').parse()
    title, callout = v20.root.texts
    assert (title.text, title.point, title.label_point) == (
        'Screen title', (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    assert (title.screen_position, title.leader_type, title.arrow_type, title.display_leader) == (
        (0.0, 0.0), 0, 3, False)
    assert (callout.text, callout.point, callout.label_point) == (
        'Callout', (5.0, 5.0, 0.0), (7.0, 7.0, 4.0))
    assert (callout.screen_position, callout.leader_type, callout.arrow_type, callout.display_leader) == (
        (0.0, 0.0), 2, 3, True)


def test_face_anchored_texts_match_sdk_in_classic_and_vff():
    for fixture_name in ('classic_face_text_anchors_2017.skp',
                         'vff_face_text_anchors_2025.skp'):
        model = SkpFile.open(FIXTURES / fixture_name).parse()
        by_text = {t.text: t for t in model.root.texts}
        assert list(by_text) == [
            'Root untextured', 'Root textured', 'On component', 'On group']
        assert by_text['Root untextured'].point == pytest.approx((9.0, 10.0, 2.0), abs=1e-12)
        assert by_text['Root untextured'].label_point == pytest.approx((3.0, 4.0, 5.0), abs=1e-12)
        assert by_text['Root textured'].point == pytest.approx((64.5, 3.6, 0.0), abs=1e-12)
        assert by_text['Root textured'].label_point == pytest.approx((4.0, 5.0, 6.0), abs=1e-12)
        assert by_text['On component'].point == pytest.approx((90.0, 56.0, 12.0), abs=1e-12)
        assert by_text['On component'].label_point == pytest.approx((5.0, 6.0, 7.0), abs=1e-12)
        assert by_text['On group'].point == pytest.approx((56.0, 32.0, 11.0), abs=1e-12)
        assert by_text['On group'].label_point == pytest.approx((6.0, 7.0, 8.0), abs=1e-12)

        own_defs = [d for d in model.definitions.values() if d.name == 'OwnTextPanel']
        assert len(own_defs) == 1
        assert [t.text for t in own_defs[0].texts] == ['In component']
        assert own_defs[0].texts[0].point == pytest.approx((3.0, 5.0, 1.0), abs=1e-12)
        assert own_defs[0].texts[0].label_point == pytest.approx((2.0, 3.0, 4.0), abs=1e-12)

    capilla = SkpFile.open(FIXTURES / 'capilla_quiroz_v17.skp').parse()
    area_texts = [t for t in capilla.root.texts if t.text == '4.06 m²']
    assert len(area_texts) == 1
    assert area_texts[0].point == pytest.approx(
        (19.346940925615975, 154.92910008345638, 112.92862002631385), abs=1e-12)
