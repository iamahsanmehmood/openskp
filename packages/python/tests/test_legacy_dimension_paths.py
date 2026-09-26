"""Legacy (MFC) linear dimensions anchored inside groups.

Each connection ref of a CDimensionLinear is followed by an entity ref and
two lists of entity refs — the instance paths of the anchored entity. On
loose geometry they are a null ref and two empty lists (the zeros a fixed
42/82-byte layout used to skip); anchored inside nested groups they carry a
ref per group, and a fixed-size read slid off the record, silently cutting
the root entity list short (a SketchUp 2018 house lost 70 of its 72 root
entities). The first time an instance is referenced MFC writes it in full,
so a path can also hold a whole NEW object.
"""
import struct

from openskp import legacy
from openskp.legacy import _R, _connection_paths


def big(slot: int) -> bytes:
    """A 6-byte big reference (0x7FFF escape) to an existing object."""
    return struct.pack('<HI', 0x7FFF, slot)


def test_loose_geometry_paths_are_empty_and_ten_bytes():
    data = b'\x00\x00' + struct.pack('<I', 0) + struct.pack('<I', 0) + b'TAIL'
    r = _R(data, 0)
    extra, p1, p2 = _connection_paths(None, r)
    assert (extra, p1, p2) == (None, [], [])
    assert r.pos == 10                          # the old fixed layout's zeros


def test_paths_inside_nested_groups_are_read_ref_by_ref():
    data = (big(0x0F69CA) + struct.pack('<I', 2) + big(0x166157)
            + big(0x165BBC) + struct.pack('<I', 2) + big(0x166157)
            + big(0x165BBD) + b'TAIL')
    r = _R(data, 0)
    extra, p1, p2 = _connection_paths(None, r)
    assert extra == 0x0F69CA
    assert p1 == [0x166157, 0x165BBC]
    assert p2 == [0x166157, 0x165BBD]
    assert data[r.pos:] == b'TAIL'


def test_a_path_can_hold_a_whole_new_object():
    """MFC serializes an object in full the first time it is referenced."""
    class Ar:
        def read_object(self, r):
            assert r.peek(6) == struct.pack('<HI', 0x7FFF, 0x80009F67)
            r.pos += 6 + 3                      # the object's own bytes
            return 4242, 'CGroup', {}
    data = (b'\x00\x00' + struct.pack('<I', 1)
            + struct.pack('<HI', 0x7FFF, 0x80009F67) + b'OBJ'
            + struct.pack('<I', 0) + b'TAIL')
    r = _R(data, 0)
    extra, p1, p2 = _connection_paths(Ar(), r)
    assert (extra, p1, p2) == (None, [4242], [])
    assert data[r.pos:] == b'TAIL'
