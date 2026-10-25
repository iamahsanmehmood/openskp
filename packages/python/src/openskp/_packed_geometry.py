"""Compact, sequential storage for completed private VFF geometry tables.

Public access still returns real dictionaries. These read-only tables exist
only between extraction and access; IDs retain insertion order and exceptional
values stay verbatim in a side table. Public model conversion can iterate them
without constructing a second complete set of geometry dictionaries.
"""

import struct
from array import array

_VERTEX = struct.Struct("<3d")
_EDGE = struct.Struct("<QQB")
_FACE = struct.Struct("<3d3QB")
_FACE_KEYS = (
    "loops",
    "normal",
    "material_id",
    "back_material_id",
    "uv_transform",
    "uv_transform_back",
    "hidden",
    "layer",
)


def _uint(value):
    return type(value) is int and 0 <= value <= 0xFFFFFFFFFFFFFFFF


def _vector(value):
    return (
        type(value) is tuple
        and len(value) == 3
        and all(type(v) is float for v in value)
    )


class _PackedTable:
    def __len__(self):
        return len(self.ids)

    def items(self):
        for index, entity_id in enumerate(self.ids):
            if index in self.fallback:
                yield entity_id, self.fallback[index]
            else:
                yield entity_id, self._value(index)

    def materialize(self):
        return dict(self.items())


class PackedVertices(_PackedTable):
    def __init__(self, values):
        self.ids = tuple(values)
        self.data = bytearray()
        self.fallback = {}
        for index, value in enumerate(values.values()):
            if _vector(value):
                self.data.extend(_VERTEX.pack(*value))
            else:
                self.fallback[index] = value
                self.data.extend(bytes(_VERTEX.size))

    def _value(self, index):
        return _VERTEX.unpack_from(self.data, index * _VERTEX.size)


class PackedEdges(_PackedTable):
    def __init__(self, values):
        self.ids = tuple(values)
        self.data = bytearray()
        self.fallback = {}
        for index, value in enumerate(values.values()):
            if (
                type(value) is tuple
                and len(value) == 2
                and all(v is None or _uint(v) for v in value)
            ):
                a, b = value
                self.data.extend(
                    _EDGE.pack(a or 0, b or 0, (a is None) | ((b is None) << 1))
                )
            else:
                self.fallback[index] = value
                self.data.extend(bytes(_EDGE.size))

    def _value(self, index):
        a, b, missing = _EDGE.unpack_from(self.data, index * _EDGE.size)
        return (None if missing & 1 else a, None if missing & 2 else b)


class PackedFaces(_PackedTable):
    def __init__(self, values):
        self.ids = tuple(values)
        self.data = bytearray()
        self.fallback = {}
        self.face_starts = array("Q", [0])
        self.loop_starts = array("Q", [0])
        self.edge_ids = array("Q")
        self.senses = array("b")
        # A typed Face and its raw dictionary share mutable loop lists.
        # Cache those lists on first read, while keeping the raw face dict lazy.
        self.loops = [None] * len(values)
        for index, value in enumerate(values.values()):
            if not self._can_pack(value):
                self.fallback[index] = value
                self.data.extend(bytes(_FACE.size))
            else:
                normal = value["normal"]
                refs = (value["material_id"], value["back_material_id"], value["layer"])
                flags = (normal is None) | (int(value["hidden"]) << 1)
                for bit, ref in enumerate(refs, 2):
                    flags |= (ref is None) << bit
                self.data.extend(
                    _FACE.pack(
                        *(normal or (0.0, 0.0, 0.0)), *(v or 0 for v in refs), flags
                    )
                )
                for loop in value["loops"]:
                    for edge_id, sense in loop:
                        self.edge_ids.append(edge_id)
                        self.senses.append(sense)
                    self.loop_starts.append(len(self.edge_ids))
            self.face_starts.append(len(self.loop_starts) - 1)

    @staticmethod
    def _can_pack(value):
        if type(value) is not dict or tuple(value) != _FACE_KEYS:
            return False
        if value["normal"] is not None and not _vector(value["normal"]):
            return False
        if value["uv_transform"] is not None or value["uv_transform_back"] is not None:
            return False
        if type(value["hidden"]) is not bool:
            return False
        if not all(
            value[k] is None or _uint(value[k])
            for k in ("material_id", "back_material_id", "layer")
        ):
            return False
        if type(value["loops"]) is not list:
            return False
        return all(
            type(loop) is list
            and all(
                type(pair) is tuple
                and len(pair) == 2
                and _uint(pair[0])
                and type(pair[1]) is int
                and pair[1] in (-1, 1)
                for pair in loop
            )
            for loop in value["loops"]
        )

    def _value(self, index):
        nx, ny, nz, front, back, layer, flags = _FACE.unpack_from(
            self.data, index * _FACE.size
        )
        loops = self.loops[index]
        if loops is None:
            loops = []
            for loop in range(self.face_starts[index], self.face_starts[index + 1]):
                loops.append(
                    [
                        (self.edge_ids[i], self.senses[i])
                        for i in range(
                            self.loop_starts[loop], self.loop_starts[loop + 1]
                        )
                    ]
                )
            self.loops[index] = loops
        return {
            "loops": loops,
            "normal": None if flags & 1 else (nx, ny, nz),
            "material_id": None if flags & 4 else front,
            "back_material_id": None if flags & 8 else back,
            "uv_transform": None,
            "uv_transform_back": None,
            "hidden": bool(flags & 2),
            "layer": None if flags & 16 else layer,
        }
