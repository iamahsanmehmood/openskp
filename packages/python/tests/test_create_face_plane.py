"""A face's stored plane satisfies n.p + d = 0 for the face's own vertices.

SketchUp stores the plane that way (all 481 faces of two real files checked: gondola_v20.skp and
capilla_quiroz_v17.skp). The writer used to store d as +n.centroid, i.e. with the wrong sign: openskp's own
reader only uses the normal, so a round trip never noticed, and the SDK's ``SUFaceGetPlane`` recomputes the
plane, so it does not either - the raw record has to be read, as here (C++ fixed the same in #477).
"""
from __future__ import annotations

import struct

import pytest

from openskp.create import create

MARKER = bytes([0xFF, 0xFF, 3, 0, 5, 0]) + b"CFace"
POLYGONS = [
    [(0, 0, 3), (2, 0, 3), (2, 2, 3), (0, 2, 3)],
    [(0, 2, 3), (2, 2, 3), (2, 0, 3), (0, 0, 3)],
    [(-7, 0, 0), (-7, 2, 0), (-7, 2, 2), (-7, 0, 2)],
    [(10, 20, 35), (12, 20, 37), (12, 22, 39), (10, 22, 37)],
]


def stored_plane(data: bytes):
    pos = data.index(MARKER) + len(MARKER)
    assert struct.unpack_from("<H", data, pos)[0] == 0  # null attribute ref
    pos += 2
    mask = data[pos]
    pos += 1 + bin(mask).count("1")  # the persistent-id bytes the mask announces
    pos += 10  # material and drawing flags
    return struct.unpack_from("<4d", data, pos)


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("polygon", POLYGONS)
def test_face_plane_agrees_with_vertices(polygon, nested):
    builder = create()
    if nested:
        with builder.add_component_definition("offset") as comp:
            comp.add_face(polygon)
        builder.add_instance(comp)
    else:
        builder.add_face(polygon)
    a, b, c, d = stored_plane(builder.to_bytes())
    for x, y, z in polygon:
        assert a * x + b * y + c * z + d == pytest.approx(0, abs=1e-9)
