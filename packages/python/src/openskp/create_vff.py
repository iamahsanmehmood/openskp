"""Create native VFF (SketchUp 2021+) ``.skp`` files from a SketchUp template.

This is the native writer for the VFF container - the ZIP plus record-tree
format SketchUp has written since the 2021 release.  Unlike
:mod:`openskp.create`, which splices a classic 2017 file together around a
bundled 2017 scaffold, this module parses the template's ``model.dat`` record
tree and extends it by tag: no byte offset into the template is hard-coded, so
any SketchUp-written VFF document with the standard section set can be the
template.  New persistent ids are allocated above the template's own next-id
counter, the needed archive entries are added, ``meta/meta.dat`` gets a fresh
GUID and save time, every ZIP CRC is recomputed and the header integrity word
is recomputed from the final archive.

This first version covers the core: usable faces built from vertex
coordinates (with holes and the same edge flags as ``create.py``), solid-colour
materials, tags, reusable component definitions and groups (including groups
and component instances nested inside definitions), with placements given as a
3x3 matrix plus a translation.  Coordinates are in inches.  The factory is
exported as ``openskp.create_vff`` (with ``openskp.VffBuilder`` /
``openskp.VffComponentDefinitionBuilder``).

The shared methods deliberately follow :mod:`openskp.create`: same names, the
same parameters in the same order, the same defaults and the same
``SkpWriteError`` for the same bad input, so a model-building function can use
either builder.  Methods and parameters that belong to later VFF writer
stages are not present yet.

The bundled template ``_scaffold/blank_vff.skp`` is a genuine empty document
saved by SketchUp 2025 (version ``{25.0.571}``, model revision 17): no
geometry, one default tag (``Layer0``), no materials and no definitions.  It
is the VFF counterpart of ``create.py``'s ``blank_v17.skp``: SketchUp's own
empty-document boilerplate, nothing authored by this project.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

from ._vff_writer.archive import (
    integrity_word,
    read_zip,
    stored_integrity_word,
    verify_integrity,
)
from ._vff_writer.template import EntityEditor, Template
from .create import (
    Point3,
    SkpWriteError,
    _is_coplanar,
)

__all__ = [
    "VffBuilder",
    "create",
    "create_vff",
    "compute_integrity_word",
    "read_stored_integrity_word",
    "check_integrity",
    "DEFAULT_TEMPLATE",
]

DEFAULT_TEMPLATE = Path(__file__).resolve().parent / "_scaffold" / "blank_vff.skp"

#: A template is a path, the file's bytes, or a bytearray.
TemplateSource = Union[str, "Path", bytes, bytearray]

Matrix3x3 = Tuple[float, float, float, float, float, float, float, float, float]
Rotation = Tuple[Tuple[float, float, float], float]


# ── Public integrity helpers ─────────────────────────────────────────────


def compute_integrity_word(data: bytes) -> int:
    """The integrity word the archive inside *data* implies."""
    try:
        offset = data.find(b"PK\x03\x04")
        if offset < 0:
            raise ValueError("not a VFF file: no embedded ZIP archive")
        return integrity_word(read_zip(data, offset))
    except ValueError as exc:
        raise SkpWriteError(str(exc)) from exc


def read_stored_integrity_word(data: bytes) -> int:
    """The integrity word stored in *data*'s 13-byte archive header."""
    try:
        return stored_integrity_word(data)
    except ValueError as exc:
        raise SkpWriteError(str(exc)) from exc


def check_integrity(data: bytes) -> bool:
    """Whether *data*'s stored header word matches its archive."""
    try:
        return verify_integrity(data)
    except ValueError as exc:
        raise SkpWriteError(str(exc)) from exc


# ── Small helpers ────────────────────────────────────────────────────────




def _edge_flags(hidden: bool, soft: bool, smooth: bool) -> int:
    return 0x06 | (0x01 if hidden else 0) | (0x08 if soft else 0) | (0x10 if smooth else 0)




def _points(points: Sequence[Sequence[float]], label: str) -> List[Point3]:
    out: List[Point3] = []
    for point in points:
        if len(point) != 3:
            raise SkpWriteError(f"{label} points must be (x, y, z) triples")
        out.append((float(point[0]), float(point[1]), float(point[2])))
    return out


# ── Component definitions / groups ──────────────────────────────────────






# ── The builder ─────────────────────────────────────────────────────────


class VffBuilder:
    """Template-based VFF writer.  See the module docstring for the scope."""

    def __init__(
        self,
        template: Optional[TemplateSource] = None,
        *,
        options: Optional[dict] = None,
    ) -> None:
        if template is None:
            data = DEFAULT_TEMPLATE.read_bytes()
        elif isinstance(template, (bytes, bytearray)):
            data = bytes(template)
        else:
            try:
                data = Path(template).read_bytes()
            except OSError as exc:
                raise SkpWriteError(f"cannot read VFF template {template!r}: {exc}") from exc
        try:
            self._template = Template(data, options)
        except ValueError as exc:
            raise SkpWriteError(f"cannot use VFF template: {exc}") from exc
        try:
            self._root = EntityEditor(
                self._template, container=self._template.root_container
            )
        except ValueError as exc:
            raise SkpWriteError(f"cannot use VFF template: {exc}") from exc
        self.materials_by_name: Dict[str, int] = dict(self._template.materials)
        self.layers_by_name: Dict[str, int] = dict(self._template.layers)

    # -- appearance -------------------------------------------------------



    # -- validation -------------------------------------------------------

    def _validate_material(self, material: Optional[int]) -> None:
        if material and material not in self._template.material_ids:
            raise SkpWriteError(f"unknown material handle: {material!r}")

    def _validate_layer(self, layer: Optional[int]) -> None:
        if layer and layer not in self._template.layer_ids:
            raise SkpWriteError(f"unknown layer handle: {layer!r}")


    # -- geometry ---------------------------------------------------------

    def _add_face_to(
        self,
        editor: EntityEditor,
        points: Sequence[Point3],
        material: Optional[int],
        layer: Optional[int],
        back_material: Optional[int],
        hidden: bool,
        soft_edges: bool,
        smooth_edges: bool,
        hidden_edges: bool,
        auto_triangulate: bool,
        holes: Sequence[Sequence[Point3]],
    ) -> None:
        self._validate_material(material)
        self._validate_material(back_material)
        self._validate_layer(layer)
        outer = _points(points, "face")
        if len(outer) < 3:
            raise SkpWriteError("a face needs at least 3 points")
        rings = [_points(hole, "hole") for hole in holes]
        for ring in rings:
            if len(ring) < 3:
                raise SkpWriteError("a hole needs at least 3 points")
        flags = _edge_flags(hidden_edges, soft_edges, smooth_edges)
        coplanar = _is_coplanar(outer)
        if not coplanar and not auto_triangulate:
            raise SkpWriteError(
                "face points are not coplanar; pass auto_triangulate=True to split them"
            )
        if rings or not auto_triangulate or len(outer) == 3 or coplanar:
            try:
                editor.add_face(
                    outer,
                    material=material,
                    layer=layer,
                    back_material=back_material,
                    hidden=hidden,
                    holes=rings,
                    edge_flags=flags,
                )
            except ValueError as exc:
                raise SkpWriteError(str(exc)) from exc
            return
        for index in range(1, len(outer) - 1):
            triangle = [outer[0], outer[index], outer[index + 1]]
            try:
                editor.add_face(
                    triangle,
                    material=material,
                    layer=layer,
                    back_material=back_material,
                    hidden=hidden,
                    edge_flags=flags,
                )
            except ValueError as exc:
                raise SkpWriteError(str(exc)) from exc

    def add_face(
        self,
        points: Sequence[Point3],
        material: Optional[int] = None,
        layer: Optional[int] = None,
        back_material: Optional[int] = None,
        hidden: bool = False,
        soft_edges: bool = False,
        smooth_edges: bool = False,
        hidden_edges: bool = False,
        auto_triangulate: bool = False,
        holes: Sequence[Sequence[Point3]] = (),
    ) -> None:
        """Add one planar face to the model root (inches, closed polygon)."""
        self._add_face_to(
            self._root,
            points,
            material,
            layer,
            back_material,
            hidden,
            soft_edges,
            smooth_edges,
            hidden_edges,
            auto_triangulate,
            holes,
        )

    # -- definitions ------------------------------------------------------




    # -- document settings ------------------------------------------------



    # -- output -----------------------------------------------------------

    def to_bytes(self) -> bytes:
        self._root.finalize()
        try:
            return self._template.build()
        except ValueError as exc:
            raise SkpWriteError(str(exc)) from exc

    def save(self, path: str) -> None:
        """Write the finished file to ``path``."""
        Path(path).write_bytes(self.to_bytes())


def create(
    template: Optional[TemplateSource] = None,
    *,
    options: Optional[dict] = None,
) -> VffBuilder:
    """Start building a new VFF file (the ``create.py`` entry-point name)."""
    return VffBuilder(template, options=options)


def create_vff(
    template: Optional[TemplateSource] = None,
    *,
    options: Optional[dict] = None,
) -> VffBuilder:
    """Open a new VFF model built on *template* (the bundled blank by default)."""
    return create(template, options=options)
