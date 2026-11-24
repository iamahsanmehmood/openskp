"""STL must contain one oriented surface for each painted SketchUp face."""
from __future__ import annotations

import io

import numpy as np
import pytest
import trimesh

from openskp import SkpFile
from openskp.create import create
from openskp.export.ply import to_ply_ascii
from openskp.export.stl import to_stl_ascii, to_stl_binary


BOX = [
    [(0, 0, 0), (0, 10, 0), (10, 10, 0), (10, 0, 0)],
    [(0, 0, 10), (10, 0, 10), (10, 10, 10), (0, 10, 10)],
    [(0, 0, 0), (10, 0, 0), (10, 0, 10), (0, 0, 10)],
    [(10, 0, 0), (10, 10, 0), (10, 10, 10), (10, 0, 10)],
    [(10, 10, 0), (0, 10, 0), (0, 10, 10), (10, 10, 10)],
    [(0, 10, 0), (0, 0, 0), (0, 0, 10), (0, 10, 10)],
]


def make_box(tmp_path, paint):
    builder = create()
    red = builder.add_material("Red", (255, 0, 0))
    blue = builder.add_material("Blue", (0, 0, 255))
    for i, face in enumerate(BOX):
        front = red if paint in ("front", "both", "mixed") else None
        back = blue if paint in ("back", "both", "mixed") else None
        # The same material occurs on both a front and a back. Grouping
        # those sides together would make it impossible to drop only backs.
        if paint == "mixed" and i % 2:
            front, back = back, front
        builder.add_face(face, material=front, back_material=back)
    path = tmp_path / "box.skp"
    builder.save(path)
    return SkpFile.open(path).build_scene()


@pytest.mark.parametrize("paint", ["unpainted", "front", "back", "both", "mixed"])
@pytest.mark.parametrize("binary", [False, True], ids=["ascii", "binary"])
def test_stl_is_a_closed_oriented_box(tmp_path, paint, binary):
    scene = make_box(tmp_path, paint)
    data = to_stl_binary(scene) if binary else to_stl_ascii(scene).encode()
    mesh = trimesh.load(io.BytesIO(data), file_type="stl", process=True)
    _, edge_counts = np.unique(mesh.edges_unique_inverse, return_counts=True)
    assert len(mesh.faces) == 12
    assert np.all(edge_counts == 2)
    assert mesh.volume == pytest.approx(0.254 ** 3, rel=1e-5)


def test_color_export_keeps_both_painted_sides(tmp_path):
    scene = make_box(tmp_path, "both")
    assert sum(len(p.indices) // 3 for p in scene.glb_primitives) == 24
    ply = to_ply_ascii(scene)
    assert "element face 24" in ply
    assert "255 0 0 255" in ply
    assert "0 0 255 255" in ply
