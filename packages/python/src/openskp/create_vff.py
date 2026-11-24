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
    DocumentOptions,
    Point3,
    SkpWriteError,
    _archive_uuid,
    _is_coplanar,
    _resolve_matrix3x3,
)

__all__ = [
    "VffBuilder",
    "VffComponentDefinitionBuilder",
    "ComponentDefinitionBuilder",
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


def _validated_rgba(
    rgba: Sequence[int], opacity: Optional[float] = None
) -> Tuple[int, int, int, int]:
    if len(rgba) == 3:
        rgba = (*rgba, 255)
    if len(rgba) != 4 or not all(
        isinstance(channel, int) and 0 <= channel <= 255 for channel in rgba
    ):
        raise SkpWriteError("rgba must be 3 or 4 integers in 0-255")
    return int(rgba[0]), int(rgba[1]), int(rgba[2]), int(rgba[3])


def _edge_flags(hidden: bool, soft: bool, smooth: bool) -> int:
    return 0x06 | (0x01 if hidden else 0) | (0x08 if soft else 0) | (0x10 if smooth else 0)


def _matrix13(
    translation: Sequence[float],
    matrix3x3: Optional[Sequence[float]],
    rotation: Optional[Rotation],
) -> List[float]:
    """A 13-value placement matrix: 3x3 row-major, translation, scale 1."""
    matrix = _resolve_matrix3x3(matrix3x3, rotation)
    if matrix is None:
        matrix = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    tx, ty, tz = (float(c) for c in translation)
    return [float(c) for c in matrix] + [tx, ty, tz, 1.0]


def _points(points: Sequence[Sequence[float]], label: str) -> List[Point3]:
    out: List[Point3] = []
    for point in points:
        if len(point) != 3:
            raise SkpWriteError(f"{label} points must be (x, y, z) triples")
        out.append((float(point[0]), float(point[1]), float(point[2])))
    return out


# ── Component definitions / groups ──────────────────────────────────────


class ComponentDefinitionBuilder:
    """A component definition under construction (use as a context manager).

    A definition can hold its own faces and nested placements: component
    instances (:meth:`add_instance`) and groups (:meth:`add_group`).  Groups
    are definitions with kind 1 whose placement is wrapped in the file's
    group record; the builder places them at the point they are closed.
    """

    def __init__(
        self,
        builder: "VffBuilder",
        name: str,
        *,
        kind: int = 0,
        owner: Optional[EntityEditor] = None,
        placement: Optional[dict] = None,
        always_faces_camera: bool = False,
        shadows_face_sun: bool = False,
        guid: Optional[bytes] = None,
    ) -> None:
        self._builder = builder
        self.name = name
        self.kind = kind
        self._owner = owner
        self._placement = placement or {}
        self._container_id = builder._template.alloc_id()
        self._editor = EntityEditor(
            builder._template, container_id=self._container_id
        )
        self._closed = False
        self._instance_id: Optional[int] = None
        self._always_faces_camera = always_faces_camera
        self._shadows_face_sun = shadows_face_sun
        self._guid = guid

    # -- geometry ---------------------------------------------------------

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
        if self._closed:
            raise SkpWriteError("this definition is already closed")
        self._builder._add_face_to(
            self._editor,
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

    # -- placements -------------------------------------------------------

    def add_instance(
        self,
        definition: "ComponentDefinitionBuilder",
        name: Optional[str] = None,
        translation: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        matrix3x3: Optional[Matrix3x3] = None,
        rotation: Optional[Rotation] = None,
        material: Optional[int] = None,
        layer: Optional[int] = None,
        hidden: bool = False,
    ) -> None:
        if self._closed:
            raise SkpWriteError("this definition is already closed")
        self._builder._check_placement(
            definition, translation, matrix3x3, rotation, material, layer
        )
        self._editor.add_instance(
            definition.id,
            _matrix13(translation, matrix3x3, rotation),
            definition_bounds=definition.bounds,
            name=name if name is not None else definition.name,
            material=material,
            layer=layer,
            hidden=hidden,
        )

    def add_group_instance(
        self,
        definition: "ComponentDefinitionBuilder",
        name: Optional[str] = None,
        translation: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        matrix3x3: Optional[Matrix3x3] = None,
        rotation: Optional[Rotation] = None,
        material: Optional[int] = None,
        layer: Optional[int] = None,
        hidden: bool = False,
    ) -> None:
        if self._closed:
            raise SkpWriteError("this definition is already closed")
        self._builder._check_placement(
            definition, translation, matrix3x3, rotation, material, layer
        )
        self._editor.add_group(
            definition.id,
            _matrix13(translation, matrix3x3, rotation),
            definition_bounds=definition.bounds,
            name=name if name is not None else definition.name,
            material=material,
            layer=layer,
            hidden=hidden,
        )

    def add_group(
        self,
        name: Optional[str] = None,
        translation: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        matrix3x3: Optional[Matrix3x3] = None,
        rotation: Optional[Rotation] = None,
        material: Optional[int] = None,
        layer: Optional[int] = None,
        hidden: bool = False,
    ) -> "GroupBuilder":
        """Start a group placed inside this definition (VFF-only extra)."""
        if self._closed:
            raise SkpWriteError("this definition is already closed")
        self._builder._validate_material(material)
        self._builder._validate_layer(layer)
        return GroupBuilder(
            self._builder,
            name,
            owner=self._editor,
            placement={
                "translation": translation,
                "matrix3x3": matrix3x3,
                "rotation": rotation,
                "material": material,
                "layer": layer,
                "hidden": hidden,
            },
        )

    # -- lifecycle --------------------------------------------------------

    def close(self) -> "ComponentDefinitionBuilder":
        if not self._closed:
            self._editor.finalize()
            self._builder._template.add_definition(
                self._editor.container,
                self.name,
                self._container_id,
                kind=self.kind,
                guid=self._guid,
                always_faces_camera=self._always_faces_camera,
                shadows_face_sun=self._shadows_face_sun,
            )
            self._closed = True
            if self._placement:
                placement = self._placement
                self._instance_id = self._owner.add_group(
                    self._container_id,
                    _matrix13(
                        placement["translation"],
                        placement["matrix3x3"],
                        placement["rotation"],
                    ),
                    definition_bounds=self.bounds,
                    name=placement.get("name", ""),
                    material=placement["material"],
                    layer=placement["layer"],
                    hidden=placement["hidden"],
                )
        return self

    @property
    def id(self) -> int:
        if not self._closed:
            raise SkpWriteError("definition is still open")
        return self._container_id

    @property
    def instance_id(self) -> Optional[int]:
        return self._instance_id

    @property
    def is_group(self) -> bool:
        return self.kind == 1

    @property
    def bounds(self) -> Optional[Sequence[float]]:
        return self._editor.bounds

    def __enter__(self) -> "ComponentDefinitionBuilder":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is None:
            self.close()


class GroupBuilder(ComponentDefinitionBuilder):
    """A group: a definition (kind 1) plus the placement created at close."""

    def __init__(
        self,
        builder: "VffBuilder",
        name: Optional[str],
        *,
        owner: EntityEditor,
        placement: dict,
    ) -> None:
        definition_name = name or "Group"
        super().__init__(
            builder,
            definition_name,
            kind=1,
            owner=owner,
            placement=placement,
        )
        self._placement["name"] = definition_name


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

    def add_material(
        self,
        name: str,
        rgba: Sequence[int],
        opacity: Optional[float] = None,
    ) -> int:
        """Register a solid-colour material and return a handle for ``add_face``."""
        if name in self.materials_by_name:
            return self.materials_by_name[name]
        red, green, blue, _alpha = _validated_rgba(rgba, opacity)
        try:
            identifier = self._template.add_material(
                name,
                (red, green, blue),
                opacity=1.0 if opacity is None else float(opacity),
            )
        except ValueError as exc:
            raise SkpWriteError(str(exc)) from exc
        self.materials_by_name[name] = identifier
        return identifier

    def add_layer(
        self,
        name: str,
        color: Optional[Sequence[int]] = None,
        hidden: bool = False,
    ) -> int:
        """Register a tag and return a handle for ``add_face``/``add_instance``."""
        if name in self.layers_by_name:
            return self.layers_by_name[name]
        rgb = None
        if color is not None:
            rgba = _validated_rgba(color)
            rgb = rgba[:3]
        try:
            identifier = self._template.add_layer(name, rgb, hidden=bool(hidden))
        except ValueError as exc:
            raise SkpWriteError(str(exc)) from exc
        self.layers_by_name[name] = identifier
        return identifier

    # -- validation -------------------------------------------------------

    def _validate_material(self, material: Optional[int]) -> None:
        if material and material not in self._template.material_ids:
            raise SkpWriteError(f"unknown material handle: {material!r}")

    def _validate_layer(self, layer: Optional[int]) -> None:
        if layer and layer not in self._template.layer_ids:
            raise SkpWriteError(f"unknown layer handle: {layer!r}")

    def _check_placement(
        self,
        definition: "ComponentDefinitionBuilder",
        translation: Sequence[float],
        matrix3x3: Optional[Matrix3x3],
        rotation: Optional[Rotation],
        material: Optional[int],
        layer: Optional[int],
    ) -> None:
        if not isinstance(definition, ComponentDefinitionBuilder):
            raise SkpWriteError("this needs a component definition builder")
        if definition._builder is not self:
            raise SkpWriteError("the definition belongs to a different builder")
        if not definition._closed:
            raise SkpWriteError("close the definition before placing instances")
        self._validate_material(material)
        self._validate_layer(layer)
        _matrix13(translation, matrix3x3, rotation)

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

    def add_component_definition(
        self,
        name: str,
        always_faces_camera: bool = False,
        shadows_face_sun: bool = False,
        guid: Optional[str] = None,
    ) -> ComponentDefinitionBuilder:
        """Start a component definition; add geometry inside a ``with`` block."""
        guid_bytes = _archive_uuid(guid)
        return ComponentDefinitionBuilder(
            self,
            name,
            always_faces_camera=always_faces_camera,
            shadows_face_sun=shadows_face_sun,
            guid=guid_bytes,
        )

    def add_group(
        self,
        name: Optional[str] = None,
        translation: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        matrix3x3: Optional[Matrix3x3] = None,
        rotation: Optional[Rotation] = None,
        material: Optional[int] = None,
        layer: Optional[int] = None,
        hidden: bool = False,
    ) -> GroupBuilder:
        """Start a root-level group (definition kind 1, placed on close)."""
        self._validate_material(material)
        self._validate_layer(layer)
        _matrix13(translation, matrix3x3, rotation)
        return GroupBuilder(
            self,
            name,
            owner=self._root,
            placement={
                "translation": translation,
                "matrix3x3": matrix3x3,
                "rotation": rotation,
                "material": material,
                "layer": layer,
                "hidden": hidden,
            },
        )

    def add_instance(
        self,
        definition: ComponentDefinitionBuilder,
        name: Optional[str] = None,
        translation: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        matrix3x3: Optional[Matrix3x3] = None,
        rotation: Optional[Rotation] = None,
        material: Optional[int] = None,
        layer: Optional[int] = None,
        hidden: bool = False,
    ) -> None:
        """Place one instance of a closed definition in the model root."""
        self._check_placement(
            definition, translation, matrix3x3, rotation, material, layer
        )
        self._root.add_instance(
            definition.id,
            _matrix13(translation, matrix3x3, rotation),
            definition_bounds=definition.bounds,
            name=name if name is not None else definition.name,
            material=material,
            layer=layer,
            hidden=hidden,
        )

    # -- document settings ------------------------------------------------

    def set_guid(self, guid: str) -> None:
        """Set the file/model UUID (canonical 32 hex digits or 8-4-4-4-12)."""
        self._template.options["model_guid"] = _archive_uuid(guid)

    def set_document_options(
        self,
        options: Optional[DocumentOptions] = None,
        *,
        length_unit: int = 0,
        length_precision: int = 3,
        style_items: Optional[Dict[int, float]] = None,
    ) -> None:
        """Accept the default document settings; other values are not encoded yet."""
        if options is not None:
            length_unit = options.length_unit
            length_precision = options.length_precision
            style_items = options.style_items
        if length_unit != 0 or length_precision != 3 or style_items:
            raise SkpWriteError(
                "VFF document options are not supported by the VFF writer yet"
            )

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


# Public alias so callers can name the VFF definition builder without
# colliding with the classic :class:`openskp.create.ComponentDefinitionBuilder`.
VffComponentDefinitionBuilder = ComponentDefinitionBuilder


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
