"""Create native VFF (SketchUp 2021+) ``.skp`` files from a SketchUp template.

This is the first, deliberately small writer for the VFF container - the ZIP
plus record-tree format SketchUp has written since the 2021 release, and the
format the native desktop application still writes in 2025.  Unlike
:mod:`openskp.create`, which splices a classic 2017 file together around a
bundled 2017 scaffold, this module **parses the template's ``model.dat``
record tree** and extends it by tag: no byte offset into the template is
hard-coded, so any SketchUp-written VFF document with the standard section
set (a tabula-rasa save, or a real model) can be the template.  New
persistent ids are allocated above the template's own next-id counter, the
needed archive entries are added, ``meta/meta.dat`` gets a fresh GUID and
save time, every ZIP CRC is recomputed and the header integrity word is
recomputed from the final archive.

Scope: faces built from vertex coordinates (shared vertices and edges dedupe
exactly like ``create.py``), solid-colour and PNG/JPEG-textured materials (with
SketchUp's default placement or explicit three-pin texture positioning), tags,
reusable component definitions and groups (including groups and component
instances nested inside definitions), and placements with a 3x3 matrix plus a
translation.  Coordinates are in inches.  The module is intentionally *not*
re-exported from ``openskp/__init__.py`` yet.

The empty template ``_scaffold/blank_vff.skp``
------------------------------------------------
``blank_vff.skp`` is a **genuine empty document saved by SketchUp 2025**, not
an SDK conversion and not a file this writer produced:

* SketchUp version: ``25.0.571`` (reported by ``Sketchup.version``), the
  version string in the file header is ``{25.0.571}`` and the model revision
  is 17.
* File SHA-256:
  ``795c5cb831a0474d633cf7db2022177678347e767e115bf5a4ee361167eb306c``.
* Saved through the application's Ruby API on the Windows acceptance VM.
  The exact Ruby used (``Sketchup.active_model``, clear, purge, ``save``):

  .. code-block:: ruby

     m=Sketchup.active_model; m.entities.clear!
     m.definitions.purge_unused; m.materials.purge_unused
     m.layers.purge_unused; m.save("C:/Temp/vff/blank.skp");
     [Sketchup.version, m.entities.length, m.definitions.length,
      m.materials.length, m.layers.map(&:name).inspect].join(" | ")

  (evaluated in one call; it returned ``25.0.571 | 0 | 0 | 0 | ["Layer0"]``).
  The result has no geometry, one default tag (``Layer0``), no materials and
  no definitions.  It is the same class of content as ``create.py``'s
  ``blank_v17.skp``: SketchUp's own empty-document boilerplate (default
  style, default tag, the built-in rendering/shadow/camera/axes settings),
  nothing authored by this project.

The record layouts written here were derived from SketchUp itself - the
minimal pairs and the acceptance model saved by SketchUp 2025 in
``research/vff-writer/oracle/`` - and cross-checked against the reader in
:mod:`openskp._core`.  skpwright's documentation was used only as a pointer
to facts that were then confirmed against those real files; no skpwright code
was copied or translated, and openskp never calls the SketchUp SDK or
skpwright at runtime (skpwright is only an external verifier when
``SKPWRIGHT_BIN`` is set).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple, Union

from ._vff_writer.archive import (
    integrity_word,
    read_zip,
    stored_integrity_word,
    verify_integrity,
)
from ._vff_writer.template import EntityEditor, Template
from .create import (
    SkpWriteError,
    _arc_points,
    _circle_basis,
    _circle_points,
    _resolve_attribute_dicts,
)

__all__ = [
    "VffBuilder",
    "ComponentDefinitionBuilder",
    "create",
    "create_vff",
    "compute_integrity_word",
    "read_stored_integrity_word",
    "check_integrity",
    "DEFAULT_TEMPLATE",
]

DEFAULT_TEMPLATE = Path(__file__).resolve().parent / "_scaffold" / "blank_vff.skp"

TemplateSource = Union[str, "Path", bytes, bytearray]


def compute_integrity_word(data: bytes) -> int:
    """The integrity word the archive inside *data* implies."""
    offset = data.find(b"PK\x03\x04")
    if offset < 0:
        raise ValueError("not a VFF file: no embedded ZIP archive")
    return integrity_word(read_zip(data, offset))


def read_stored_integrity_word(data: bytes) -> int:
    """The integrity word stored in *data*'s 13-byte header."""
    return stored_integrity_word(data)


def check_integrity(data: bytes) -> bool:
    """Whether *data*'s stored header word matches its archive."""
    return verify_integrity(data)


def _validated_rgba(
    rgba: Sequence[int], opacity: Optional[float] = None
) -> Tuple[int, int, int, int]:
    values = tuple(rgba)
    if len(values) != 3 and len(values) != 4:
        raise SkpWriteError("rgba must be 3 or 4 integers in 0-255")
    if not all(isinstance(c, int) and 0 <= c <= 255 for c in values):
        raise SkpWriteError("rgba must be 3 or 4 integers in 0-255")
    if len(values) == 3:
        values = (*values, 255)
    if opacity is not None:
        if not 0.0 <= float(opacity) <= 1.0:
            raise SkpWriteError("opacity must be between 0 and 1")
        values = (values[0], values[1], values[2], int(round(float(opacity) * 255)))
    return values  # type: ignore[return-value]


def _rotation_matrix(axis: Sequence[float], angle: float) -> List[float]:
    length = math.sqrt(sum(c * c for c in axis))
    if length == 0:
        raise SkpWriteError("rotation axis must be non-zero")
    x, y, z = (c / length for c in axis)
    cosine = math.cos(angle)
    sine = math.sin(angle)
    one_c = 1.0 - cosine
    return [
        one_c * x * x + cosine,
        one_c * x * y - sine * z,
        one_c * x * z + sine * y,
        one_c * x * y + sine * z,
        one_c * y * y + cosine,
        one_c * y * z - sine * x,
        one_c * x * z - sine * y,
        one_c * y * z + sine * x,
        one_c * z * z + cosine,
    ]


def _matrix13(
    translation: Sequence[float],
    matrix3x3: Optional[Sequence[float]],
    rotation: Optional[Tuple[Sequence[float], float]],
) -> List[float]:
    if matrix3x3 is not None and rotation is not None:
        raise SkpWriteError("pass either matrix3x3 or rotation, not both")
    if len(translation) != 3:
        raise SkpWriteError("translation must be three coordinates")
    if matrix3x3 is not None:
        if len(matrix3x3) != 9:
            raise SkpWriteError("matrix3x3 must have nine values (row-major)")
        matrix = [float(v) for v in matrix3x3]
    elif rotation is not None:
        matrix = _rotation_matrix(rotation[0], float(rotation[1]))
    else:
        matrix = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    return matrix + [float(c) for c in translation] + [1.0]


@dataclass
class CurveHandle:
    """A curve written by :meth:`VffBuilder.add_circle_curve`.

    ``points`` is the closed ring in creation order; pass the handle as a
    hole to ``add_face(holes=[...])`` to reuse its edges and curve membership.
    """

    points: List[Tuple[float, float, float]]
    curve_id: int
    edge_ids: List[int] = field(default_factory=list)


def _normalized(vector: Sequence[float]) -> Tuple[float, float, float]:
    length = math.sqrt(sum(float(c) * float(c) for c in vector))
    if length < 1e-12:
        raise SkpWriteError("a curve normal must not be the zero vector")
    return tuple(float(c) / length for c in vector)  # type: ignore[return-value]


def _curve_points(
    center: Sequence[float],
    normal: Sequence[float],
    radius: float,
    num_segments: int,
    start_angle: Optional[float] = None,
    end_angle: Optional[float] = None,
):
    if not 3 <= num_segments <= 255:
        raise SkpWriteError("num_segments must be between 3 and 255")
    centre = tuple(float(c) for c in center)
    unit_normal = _normalized(normal)
    u, w = _circle_basis(unit_normal)
    if start_angle is None or end_angle is None:
        points = _circle_points(centre, unit_normal, float(radius), num_segments, u, w)
    else:
        points = _arc_points(
            centre, unit_normal, float(radius), num_segments, u, w,
            float(start_angle), float(end_angle),
        )
    return centre, unit_normal, u, w, points


def _arc_frame(
    center: Sequence[float],
    normal: Sequence[float],
    radius: float,
    u: Sequence[float],
    w: Sequence[float],
    start_angle: float,
    end_angle: float,
) -> List[float]:
    offset = -sum(float(normal[i]) * float(center[i]) for i in range(3))
    return [
        *[float(c) for c in center],
        *[float(c) for c in normal],
        offset,
        *(radius * float(c) for c in u),
        *(radius * float(c) for c in w),
        float(radius),
        float(start_angle),
        float(end_angle),
    ]


def _edge_flags(hidden: bool, soft: bool, smooth: bool) -> int:
    return 0x06 | (0x01 if hidden else 0) | (0x08 if soft else 0) | (0x10 if smooth else 0)


def _attribute_dicts(attributes, name, dicts):
    return _resolve_attribute_dicts(attributes, name, dicts)


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
        owner: Optional["EntityEditor"] = None,
        placement: Optional[dict] = None,
        attribute_dicts: Optional[Sequence[Tuple[str, dict]]] = None,
    ) -> None:
        self._builder = builder
        self.name = name
        self.kind = kind
        self._owner = owner
        self._placement = placement or {}
        self._container_id = builder._template.alloc_id()
        self._editor = EntityEditor(
            builder._template,
            container_id=self._container_id,
            attributes=attribute_dicts,
        )
        self._closed = False
        self._instance_id: Optional[int] = None

    # -- geometry ---------------------------------------------------------

    def add_face(
        self,
        points: Sequence[Sequence[float]],
        material: Optional[int] = None,
        layer: Optional[int] = None,
        back_material: Optional[int] = None,
        hidden: bool = False,
        holes: Sequence[Sequence[Sequence[float]]] = (),
        front_uv: Optional[
            Sequence[Tuple[Sequence[float], Tuple[float, float]]]
        ] = None,
        back_uv: Optional[
            Sequence[Tuple[Sequence[float], Tuple[float, float]]]
        ] = None,
        attributes: Optional[dict] = None,
        attribute_dict_name: str = "attributes",
        attribute_dicts: Sequence[Tuple[str, dict]] = (),
    ) -> None:
        self._builder._validate_material(material)
        self._builder._validate_material(back_material)
        self._builder._validate_layer(layer)
        if self._closed:
            raise SkpWriteError("this definition is already closed")
        resolved = _attribute_dicts(attributes, attribute_dict_name, attribute_dicts)
        self._editor.add_face(
            points,
            material=material,
            layer=layer,
            back_material=back_material,
            hidden=hidden,
            holes=holes,
            front_uv=front_uv,
            back_uv=back_uv,
            attribute_dicts=resolved,
        )

    # -- annotations ------------------------------------------------------

    def add_text(
        self,
        text: str,
        point: Sequence[float],
        leader: Sequence[float] = (15.0, 15.0, 15.0),
        layer: Optional[int] = None,
    ) -> None:
        self._builder._validate_layer(layer)
        if self._closed:
            raise SkpWriteError("this definition is already closed")
        self._editor.add_text(text, point, leader, screen=False, layer=layer)

    def add_screen_text(
        self,
        text: str,
        point: Sequence[float],
        vector: Sequence[float] = (30.0, 20.0, 0.0),
        layer: Optional[int] = None,
    ) -> None:
        self._builder._validate_layer(layer)
        if self._closed:
            raise SkpWriteError("this definition is already closed")
        self._editor.add_text(text, point, vector, screen=True, layer=layer)

    def add_dimension(
        self,
        start: Sequence[float],
        end: Sequence[float],
        offset: float = 10.0,
        offset_vector: Optional[Sequence[float]] = None,
        anchor: bool = False,
    ) -> None:
        if self._closed:
            raise SkpWriteError("this definition is already closed")
        self._editor.add_dimension(start, end, offset, offset_vector, anchor=anchor)

    def add_guide_line(
        self,
        point: Sequence[float],
        point2: Optional[Sequence[float]] = None,
        direction: Optional[Sequence[float]] = None,
    ) -> None:
        if self._closed:
            raise SkpWriteError("this definition is already closed")
        self._editor.add_guide_line(point, point2, direction)

    def add_guide_point(self, position: Sequence[float]) -> None:
        if self._closed:
            raise SkpWriteError("this definition is already closed")
        self._editor.add_guide_point(position)

    def add_section_plane(
        self,
        point: Sequence[float],
        normal: Sequence[float],
        name: Optional[str] = None,
        symbol: Optional[str] = None,
        active: bool = False,
        hidden: bool = False,
    ) -> int:
        if self._closed:
            raise SkpWriteError("this definition is already closed")
        return self._editor.add_section_plane(
            point,
            normal,
            name=name or "",
            symbol=symbol or "",
            active=active,
            hidden=hidden,
        )

    # -- curves -----------------------------------------------------------

    def add_circle(
        self,
        center: Sequence[float],
        normal: Sequence[float],
        radius: float,
        num_segments: int = 24,
        material: Optional[int] = None,
        layer: Optional[int] = None,
        back_material: Optional[int] = None,
        hidden: bool = False,
        front_uv: Optional[
            Sequence[Tuple[Sequence[float], Tuple[float, float]]]
        ] = None,
        back_uv: Optional[
            Sequence[Tuple[Sequence[float], Tuple[float, float]]]
        ] = None,
        attributes: Optional[dict] = None,
        attribute_dict_name: str = "attributes",
        attribute_dicts: Sequence[Tuple[str, dict]] = (),
        holes: Sequence = (),
    ) -> None:
        if self._closed:
            raise SkpWriteError("this definition is already closed")
        self._builder._add_circle_to(
            self._editor,
            center,
            normal,
            radius,
            num_segments,
            material,
            layer,
            back_material,
            hidden,
            front_uv,
            back_uv,
            attributes,
            attribute_dict_name,
            attribute_dicts,
            holes,
        )

    def add_arc(
        self,
        center: Sequence[float],
        normal: Sequence[float],
        radius: float,
        start_angle: float,
        end_angle: float,
        num_segments: int = 24,
        hidden_edges: bool = False,
        soft_edges: bool = False,
        smooth_edges: bool = False,
    ) -> None:
        if self._closed:
            raise SkpWriteError("this definition is already closed")
        self._builder._add_arc_to(
            self._editor,
            center,
            normal,
            radius,
            start_angle,
            end_angle,
            num_segments,
            hidden_edges,
            soft_edges,
            smooth_edges,
        )

    def add_polyline(
        self,
        points: Sequence[Sequence[float]],
        closed: bool = False,
        hidden_edges: bool = False,
        soft_edges: bool = False,
        smooth_edges: bool = False,
    ) -> None:
        if self._closed:
            raise SkpWriteError("this definition is already closed")
        self._builder._add_polyline_to(
            self._editor,
            points,
            closed,
            hidden_edges,
            soft_edges,
            smooth_edges,
        )

    def add_instance(
        self,
        definition: "ComponentDefinitionBuilder",
        name: Optional[str] = None,
        translation: Sequence[float] = (0.0, 0.0, 0.0),
        matrix3x3: Optional[Sequence[float]] = None,
        rotation: Optional[Tuple[Sequence[float], float]] = None,
        material: Optional[int] = None,
        layer: Optional[int] = None,
        hidden: bool = False,
        attributes: Optional[dict] = None,
        attribute_dict_name: str = "attributes",
        attribute_dicts: Sequence[Tuple[str, dict]] = (),
    ) -> None:
        """Place a component instance *inside this definition*."""
        self._builder._check_placement(
            definition, name, translation, matrix3x3, rotation, material, layer
        )
        if self._closed:
            raise SkpWriteError("this definition is already closed")
        self._editor.add_instance(
            definition.id,
            _matrix13(translation, matrix3x3, rotation),
            definition_bounds=definition.bounds,
            name=name or "",
            material=material,
            layer=layer,
            hidden=hidden,
            attribute_dicts=_attribute_dicts(
                attributes, attribute_dict_name, attribute_dicts
            ),
        )

    def add_group(
        self,
        name: Optional[str] = None,
        translation: Sequence[float] = (0.0, 0.0, 0.0),
        matrix3x3: Optional[Sequence[float]] = None,
        rotation: Optional[Tuple[Sequence[float], float]] = None,
        material: Optional[int] = None,
        layer: Optional[int] = None,
        hidden: bool = False,
        attributes: Optional[dict] = None,
        attribute_dict_name: str = "attributes",
        attribute_dicts: Sequence[Tuple[str, dict]] = (),
    ) -> "GroupBuilder":
        """Start a group placed inside this definition (use as a context manager)."""
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
                "attribute_dicts": _attribute_dicts(
                    attributes, attribute_dict_name, attribute_dicts
                ),
            },
        )

    # -- lifecycle --------------------------------------------------------

    def close(self) -> "ComponentDefinitionBuilder":
        if not self._closed:
            container = self._editor.finalize()
            self._template_id = self._builder._template.add_definition(
                container, self.name, self._container_id, kind=self.kind
            )
            self._closed = True
            if self._placement:
                self._instance_id = self._owner.add_group(
                    self._template_id,
                    _matrix13(
                        self._placement["translation"],
                        self._placement["matrix3x3"],
                        self._placement["rotation"],
                    ),
                    definition_bounds=self.bounds,
                    name=self._instance_name,
                    material=self._placement["material"],
                    layer=self._placement["layer"],
                    hidden=self._placement["hidden"],
                    attribute_dicts=self._placement.get("attribute_dicts"),
                )
        return self

    @property
    def id(self) -> int:
        if not self._closed:
            raise SkpWriteError("definition is still open")
        return self._template_id

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
        owner: "EntityEditor",
        placement: dict,
    ) -> None:
        instance_name = name or ""
        definition_name = builder._unique_definition_name(name or "Group")
        super().__init__(
            builder,
            definition_name,
            kind=1,
            owner=owner,
            placement=placement,
        )
        self._instance_name = instance_name


class VffBuilder:
    """Template-based VFF writer.  See the module docstring for the scope."""

    def __init__(self, template: Optional[TemplateSource] = None) -> None:
        if template is None:
            data = DEFAULT_TEMPLATE.read_bytes()
        elif isinstance(template, (bytes, bytearray)):
            data = bytes(template)
        else:
            data = Path(template).read_bytes()
        self._template = Template(data)
        self._root = EntityEditor(self._template, container=self._template.root_container)
        self._materials: dict[str, int] = {}
        self._layers: dict[str, int] = {}
        self._definition_names: set[str] = set()

    # -- appearance -------------------------------------------------------

    def add_material(
        self,
        name: str,
        rgba: Sequence[int],
        opacity: Optional[float] = None,
    ) -> int:
        """Register a solid-colour material and return a handle for ``add_face``."""
        if name in self._materials:
            return self._materials[name]
        values = _validated_rgba(rgba, opacity)
        identifier = self._template.add_material(
            name, values[:3], opacity=values[3] / 255.0
        )
        self._materials[name] = identifier
        return identifier

    def add_texture_material(
        self,
        name: str,
        image: Union[str, "Path", bytes, bytearray],
        applied_width: Optional[float] = None,
        applied_height: Optional[float] = None,
        opacity: Optional[float] = None,
        image_name: Optional[str] = None,
    ) -> int:
        """Register a PNG/JPEG-textured material and return a handle.

        *image* is a path or the file's bytes; *applied_width* and
        *applied_height* are the tile size in inches (both default to 1.0,
        like ``create.py``).  For bytes, *image_name* names the archive entry
        (defaults to ``texture.png``/``texture.jpg`` from the magic bytes).
        Faces painted with the handle get SketchUp's default placement unless
        ``add_face``'s ``front_uv``/``back_uv`` pins are given.
        """
        if name in self._materials:
            return self._materials[name]
        if isinstance(image, (bytes, bytearray)):
            image_bytes = bytes(image)
            extension = _image_extension(image_bytes)
            entry_name = image_name or f"texture.{extension}"
            texture_filename = image_name or entry_name
        else:
            path = Path(image)
            image_bytes = path.read_bytes()
            _image_extension(image_bytes)
            entry_name = image_name or path.name
            # Only the basename is stored: the archive image is addressed
            # relative to the material folder, so an absolute caller path
            # would leak into the document without adding information.
            texture_filename = entry_name
        width = 1.0 if applied_width is None else float(applied_width)
        height = 1.0 if applied_height is None else float(applied_height)
        if width <= 0 or height <= 0:
            raise SkpWriteError("a texture's applied size must be positive")
        alpha = 1.0
        if opacity is not None:
            if not 0.0 <= float(opacity) <= 1.0:
                raise SkpWriteError("opacity must be between 0 and 1")
            alpha = float(opacity)
        identifier = self._template.add_texture_material(
            name,
            image_bytes,
            entry_name,
            width,
            height,
            alpha,
            _average_color(image_bytes),
            texture_filename,
        )
        self._materials[name] = identifier
        return identifier

    def add_layer(
        self,
        name: str,
        color: Optional[Sequence[int]] = None,
        hidden: bool = False,
    ) -> int:
        """Register a tag and return a handle for ``add_face``/``add_instance``."""
        if name in self._layers:
            return self._layers[name]
        values = _validated_rgba(color) if color is not None else None
        identifier = self._template.add_layer(
            name,
            None if values is None else values[:3],
            hidden=bool(hidden),
        )
        self._layers[name] = identifier
        return identifier

    # -- handles ----------------------------------------------------------

    def _validate_material(self, material: Optional[int]) -> None:
        if material is not None and material not in self._template.material_ids:
            raise SkpWriteError(f"unknown material handle: {material!r}")

    def _validate_layer(self, layer: Optional[int]) -> None:
        if layer is not None and layer not in self._template.layer_ids:
            raise SkpWriteError(f"unknown layer handle: {layer!r}")

    def _unique_definition_name(self, base: str) -> str:
        name = base
        counter = 1
        while name in self._definition_names:
            name = f"{base}#{counter}"
            counter += 1
        self._definition_names.add(name)
        return name

    def _check_definition(self, definition: ComponentDefinitionBuilder) -> None:
        if not isinstance(definition, ComponentDefinitionBuilder):
            raise SkpWriteError("this needs a component definition builder")
        if definition._builder is not self:
            raise SkpWriteError("the definition belongs to a different builder")
        if not definition._closed:
            raise SkpWriteError("close the definition before placing instances")

    def _check_placement(
        self,
        definition: ComponentDefinitionBuilder,
        name: Optional[str],
        translation: Sequence[float],
        matrix3x3: Optional[Sequence[float]],
        rotation: Optional[Tuple[Sequence[float], float]],
        material: Optional[int],
        layer: Optional[int],
    ) -> None:
        self._check_definition(definition)
        self._validate_material(material)
        self._validate_layer(layer)
        _matrix13(translation, matrix3x3, rotation)

    # -- geometry ---------------------------------------------------------

    def add_face(
        self,
        points: Sequence[Sequence[float]],
        material: Optional[int] = None,
        layer: Optional[int] = None,
        back_material: Optional[int] = None,
        hidden: bool = False,
        holes: Sequence[Sequence[Sequence[float]]] = (),
        front_uv: Optional[
            Sequence[Tuple[Sequence[float], Tuple[float, float]]]
        ] = None,
        back_uv: Optional[
            Sequence[Tuple[Sequence[float], Tuple[float, float]]]
        ] = None,
        attributes: Optional[dict] = None,
        attribute_dict_name: str = "attributes",
        attribute_dicts: Sequence[Tuple[str, dict]] = (),
    ) -> None:
        """Add one planar face to the model root (inches, closed polygon)."""
        self._validate_material(material)
        self._validate_material(back_material)
        self._validate_layer(layer)
        self._root.add_face(
            points,
            material=material,
            layer=layer,
            back_material=back_material,
            hidden=hidden,
            holes=holes,
            front_uv=front_uv,
            back_uv=back_uv,
            attribute_dicts=_attribute_dicts(
                attributes, attribute_dict_name, attribute_dicts
            ),
        )

    # -- annotations ------------------------------------------------------

    def add_text(
        self,
        text: str,
        point: Sequence[float],
        leader: Sequence[float] = (15.0, 15.0, 15.0),
        layer: Optional[int] = None,
    ) -> None:
        """Add a leader text anchored at *point*, label at point + leader."""
        self._validate_layer(layer)
        self._root.add_text(text, point, leader, screen=False, layer=layer)

    def add_screen_text(
        self,
        text: str,
        point: Sequence[float],
        vector: Sequence[float] = (30.0, 20.0, 0.0),
        layer: Optional[int] = None,
    ) -> None:
        """Add a screen text (no displayed leader) at *point*."""
        self._validate_layer(layer)
        self._root.add_text(text, point, vector, screen=True, layer=layer)

    def add_dimension(
        self,
        start: Sequence[float],
        end: Sequence[float],
        offset: float = 10.0,
        offset_vector: Optional[Sequence[float]] = None,
        anchor: bool = False,
    ) -> None:
        """Add a linear dimension; ``anchor=True`` attaches to existing vertices."""
        self._root.add_dimension(start, end, offset, offset_vector, anchor=anchor)

    def add_guide_line(
        self,
        point: Sequence[float],
        point2: Optional[Sequence[float]] = None,
        direction: Optional[Sequence[float]] = None,
    ) -> None:
        """Add a bounded (point2) or unbounded (direction) guide line."""
        self._root.add_guide_line(point, point2, direction)

    def add_guide_point(self, position: Sequence[float]) -> None:
        self._root.add_guide_point(position)

    def add_section_plane(
        self,
        point: Sequence[float],
        normal: Sequence[float],
        name: Optional[str] = None,
        symbol: Optional[str] = None,
        active: bool = False,
        hidden: bool = False,
    ) -> int:
        """Add a section plane; ``active=True`` also makes it the active one."""
        return self._root.add_section_plane(
            point,
            normal,
            name=name or "",
            symbol=symbol or "",
            active=active,
            hidden=hidden,
        )

    def set_active_section_plane(self, identifier: Optional[int]) -> None:
        self._root.active_section = identifier

    # -- curves -----------------------------------------------------------

    def _add_circle_to(
        self,
        editor,
        center,
        normal,
        radius,
        num_segments,
        material,
        layer,
        back_material,
        hidden,
        front_uv,
        back_uv,
        attributes,
        attribute_dict_name,
        attribute_dicts,
        holes,
    ) -> None:
        self._validate_material(material)
        self._validate_material(back_material)
        self._validate_layer(layer)
        curve = self._write_circle_curve(editor, center, normal, radius, num_segments)
        editor.add_face(
            curve.points,
            material=material,
            layer=layer,
            back_material=back_material,
            hidden=hidden,
            holes=holes,
            front_uv=front_uv,
            back_uv=back_uv,
            attribute_dicts=_attribute_dicts(
                attributes, attribute_dict_name, attribute_dicts
            ),
        )

    def _write_circle_curve(
        self,
        editor,
        center,
        normal,
        radius,
        num_segments,
        hidden_edges=False,
        soft_edges=False,
        smooth_edges=False,
    ) -> CurveHandle:
        centre, unit_normal, u, w, points = _curve_points(
            center, normal, radius, num_segments
        )
        frame = _arc_frame(
            centre, unit_normal, float(radius), u, w, 0.0, 0.0
        )
        curve_id, edges = editor.add_curve_edges(
            points,
            closed=True,
            arc_frame=frame,
            flags=_edge_flags(hidden_edges, soft_edges, smooth_edges),
        )
        return CurveHandle(points, curve_id, edges)

    def _add_arc_to(
        self,
        editor,
        center,
        normal,
        radius,
        start_angle,
        end_angle,
        num_segments,
        hidden_edges,
        soft_edges,
        smooth_edges,
    ) -> None:
        if float(start_angle) == float(end_angle):
            raise SkpWriteError("start_angle and end_angle must differ - use add_circle")
        centre, unit_normal, u, w, points = _curve_points(
            center, normal, radius, num_segments, start_angle, end_angle
        )
        frame = _arc_frame(
            centre,
            unit_normal,
            float(radius),
            u,
            w,
            float(start_angle),
            float(end_angle),
        )
        editor.add_curve_edges(
            points,
            arc_frame=frame,
            flags=_edge_flags(hidden_edges, soft_edges, smooth_edges),
        )

    def _add_polyline_to(
        self,
        editor,
        points,
        closed,
        hidden_edges,
        soft_edges,
        smooth_edges,
    ) -> None:
        coordinates = [tuple(float(c) for c in point) for point in points]
        if len(coordinates) < 2:
            raise SkpWriteError("a polyline needs at least 2 points")
        editor.add_curve_edges(
            coordinates,
            closed=bool(closed),
            flags=_edge_flags(hidden_edges, soft_edges, smooth_edges),
        )

    def add_circle_curve(
        self,
        center: Sequence[float],
        normal: Sequence[float],
        radius: float,
        num_segments: int = 24,
        hidden_edges: bool = False,
        soft_edges: bool = False,
        smooth_edges: bool = False,
    ) -> CurveHandle:
        """Add only a circle's curve+edges; pass the handle as a face hole."""
        return self._write_circle_curve(
            self._root,
            center,
            normal,
            radius,
            num_segments,
            hidden_edges,
            soft_edges,
            smooth_edges,
        )

    def add_circle(
        self,
        center: Sequence[float],
        normal: Sequence[float],
        radius: float,
        num_segments: int = 24,
        material: Optional[int] = None,
        layer: Optional[int] = None,
        back_material: Optional[int] = None,
        hidden: bool = False,
        front_uv: Optional[
            Sequence[Tuple[Sequence[float], Tuple[float, float]]]
        ] = None,
        back_uv: Optional[
            Sequence[Tuple[Sequence[float], Tuple[float, float]]]
        ] = None,
        attributes: Optional[dict] = None,
        attribute_dict_name: str = "attributes",
        attribute_dicts: Sequence[Tuple[str, dict]] = (),
        holes: Sequence = (),
    ) -> None:
        """Add one circular face (a real ArcCurve, like ``create.py``)."""
        self._add_circle_to(
            self._root,
            center,
            normal,
            radius,
            num_segments,
            material,
            layer,
            back_material,
            hidden,
            front_uv,
            back_uv,
            attributes,
            attribute_dict_name,
            attribute_dicts,
            holes,
        )

    def add_arc(
        self,
        center: Sequence[float],
        normal: Sequence[float],
        radius: float,
        start_angle: float,
        end_angle: float,
        num_segments: int = 24,
        hidden_edges: bool = False,
        soft_edges: bool = False,
        smooth_edges: bool = False,
    ) -> None:
        """Add one open arc (a real ArcCurve, edges only)."""
        self._add_arc_to(
            self._root,
            center,
            normal,
            radius,
            start_angle,
            end_angle,
            num_segments,
            hidden_edges,
            soft_edges,
            smooth_edges,
        )

    def add_polyline(
        self,
        points: Sequence[Sequence[float]],
        closed: bool = False,
        hidden_edges: bool = False,
        soft_edges: bool = False,
        smooth_edges: bool = False,
    ) -> None:
        """Add one freeform polyline curve (a plain Curve, edges only)."""
        self._add_polyline_to(
            self._root,
            points,
            closed,
            hidden_edges,
            soft_edges,
            smooth_edges,
        )

    def add_component_definition(
        self,
        name: str,
        attributes: Optional[dict] = None,
        attribute_dict_name: str = "attributes",
        attribute_dicts: Sequence[Tuple[str, dict]] = (),
    ) -> ComponentDefinitionBuilder:
        """Start a component definition; add geometry inside a ``with`` block."""
        return ComponentDefinitionBuilder(
            self,
            self._unique_definition_name(name),
            attribute_dicts=_attribute_dicts(
                attributes, attribute_dict_name, attribute_dicts
            ),
        )

    def add_group(
        self,
        name: Optional[str] = None,
        translation: Sequence[float] = (0.0, 0.0, 0.0),
        matrix3x3: Optional[Sequence[float]] = None,
        rotation: Optional[Tuple[Sequence[float], float]] = None,
        material: Optional[int] = None,
        layer: Optional[int] = None,
        hidden: bool = False,
        attributes: Optional[dict] = None,
        attribute_dict_name: str = "attributes",
        attribute_dicts: Sequence[Tuple[str, dict]] = (),
    ) -> GroupBuilder:
        """Start a root-level group (definition kind 1, placed on close)."""
        self._validate_material(material)
        self._validate_layer(layer)
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
                "attribute_dicts": _attribute_dicts(
                    attributes, attribute_dict_name, attribute_dicts
                ),
            },
        )

    def add_instance(
        self,
        definition: ComponentDefinitionBuilder,
        name: Optional[str] = None,
        translation: Sequence[float] = (0.0, 0.0, 0.0),
        matrix3x3: Optional[Sequence[float]] = None,
        rotation: Optional[Tuple[Sequence[float], float]] = None,
        material: Optional[int] = None,
        layer: Optional[int] = None,
        hidden: bool = False,
        attributes: Optional[dict] = None,
        attribute_dict_name: str = "attributes",
        attribute_dicts: Sequence[Tuple[str, dict]] = (),
    ) -> None:
        """Place one instance of a closed definition in the model root."""
        self._check_placement(
            definition, name, translation, matrix3x3, rotation, material, layer
        )
        self._root.add_instance(
            definition.id,
            _matrix13(translation, matrix3x3, rotation),
            definition_bounds=definition.bounds,
            name=name or "",
            material=material,
            layer=layer,
            hidden=hidden,
            attribute_dicts=_attribute_dicts(
                attributes, attribute_dict_name, attribute_dicts
            ),
        )

    def add_scene(
        self,
        name: str,
        eye: Sequence[float],
        target: Sequence[float],
        up: Optional[Sequence[float]] = None,
        *,
        perspective: bool = True,
        fov: float = 35.0,
        ortho_height: float = 200.0,
        hidden_layers: Sequence[int] = (),
    ) -> None:
        """Add a scene (page): named camera plus the tags it hides."""
        for identifier in hidden_layers:
            self._validate_layer(identifier)
        self._template.add_scene(
            name,
            eye,
            target,
            up,
            perspective=perspective,
            fov=fov,
            ortho_height=ortho_height,
            hidden_layers=hidden_layers,
        )

    def set_camera(
        self,
        eye: Sequence[float],
        target: Sequence[float],
        up: Optional[Sequence[float]] = None,
        *,
        perspective: bool = True,
        fov: float = 35.0,
        ortho_height: float = 200.0,
    ) -> None:
        """Set the model camera (the view the file opens with)."""
        self._template.set_camera(
            eye,
            target,
            up,
            perspective=perspective,
            fov=fov,
            ortho_height=ortho_height,
        )

    def set_model_attributes(
        self,
        attributes: Optional[dict] = None,
        attribute_dict_name: str = "attributes",
        attribute_dicts: Sequence[Tuple[str, dict]] = (),
    ) -> None:
        """Attach custom dictionaries to the model itself."""
        self._root.add_model_attributes(
            _attribute_dicts(attributes, attribute_dict_name, attribute_dicts)
        )

    # -- output -----------------------------------------------------------

    def to_bytes(self) -> bytes:
        self._root.finalize()
        return self._template.build()

    def save(self, path: Union[str, "Path"]) -> None:
        Path(path).write_bytes(self.to_bytes())


def _image_extension(image: bytes) -> str:
    if image[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if image[:3] == b"\xff\xd8\xff":
        return "jpg"
    raise SkpWriteError(
        "unrecognized image format - only PNG and JPEG textures are supported "
        "(detected from the file's own magic bytes, not any extension)"
    )


def _average_color(image: bytes) -> Tuple[int, int, int]:
    """Average RGB of a texture, via Pillow when installed, else mid-grey."""
    try:
        import io

        from PIL import Image
    except Exception:
        return (128, 128, 128)
    try:
        with Image.open(io.BytesIO(image)) as picture:
            picture = picture.convert("RGB")
            picture.thumbnail((32, 32))
            raw = picture.tobytes()
            count = len(raw) // 3
            if not count:
                return (128, 128, 128)
            return (
                sum(raw[0::3]) // count,
                sum(raw[1::3]) // count,
                sum(raw[2::3]) // count,
            )
    except Exception:
        return (128, 128, 128)


def create(template: Optional[TemplateSource] = None) -> VffBuilder:
    """Start building a new VFF file (the ``create.py`` entry-point name).

    >>> builder = create()
    >>> red = builder.add_material("Red", (255, 0, 0))
    >>> tag = builder.add_layer("Roof")
    >>> builder.add_face([(0, 0, 0), (100, 0, 0), (100, 100, 0), (0, 100, 0)],
    ...                  material=red, layer=tag)
    >>> builder.save("output.skp")
    """
    return VffBuilder(template)


def create_vff(template: Optional[TemplateSource] = None) -> VffBuilder:
    """Open a new VFF model built on *template* (the bundled 2025 blank by default)."""
    return VffBuilder(template)