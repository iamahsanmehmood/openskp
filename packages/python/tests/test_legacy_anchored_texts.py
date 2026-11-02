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

import pytest

from openskp.legacy import _READERS, _Archive, _R, _read_entity_list, _read_text

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
