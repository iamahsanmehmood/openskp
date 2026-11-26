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

import math
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
    _plane_from_polygon,
    _resolve_attribute_dicts,
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
    except Exception as exc:
        raise SkpWriteError(str(exc)) from exc


def read_stored_integrity_word(data: bytes) -> int:
    """The integrity word stored in *data*'s 13-byte archive header."""
    try:
        return stored_integrity_word(data)
    except Exception as exc:
        raise SkpWriteError(str(exc)) from exc


def check_integrity(data: bytes) -> bool:
    """Whether *data*'s stored header word matches its archive."""
    try:
        return verify_integrity(data)
    except Exception as exc:
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
            data = picture.convert("RGB").tobytes()
        count = len(data) // 3
        if not count:
            return (128, 128, 128)
        return (
            sum(data[0::3]) // count,
            sum(data[1::3]) // count,
            sum(data[2::3]) // count,
        )
    except Exception:
        return (128, 128, 128)


def _texture_entry_name(name: str) -> str:
    """Archive basename SketchUp can represent for a texture image."""
    name = name.replace("/", "_").replace("\\", "_").replace('"', "_")
    if name in (".", ".."):
        raise SkpWriteError("a texture image name cannot be '.' or '..'")
    for char in name:
        codepoint = ord(char)
        if codepoint < 0x20 or 0xD800 <= codepoint <= 0xDFFF:
            raise SkpWriteError(
                f"texture file name contains a character a ZIP entry cannot hold: {char!r}"
            )
    return name or "texture"


def _edge_flags(hidden: bool, soft: bool, smooth: bool) -> int:
    return 0x06 | (0x01 if hidden else 0) | (0x08 if soft else 0) | (0x10 if smooth else 0)


def _matrix13(
    translation: Sequence[float],
    matrix3x3: Optional[Sequence[float]],
    rotation: Optional[Rotation],
) -> List[float]:
    """A 13-value placement matrix: 3x3 row-major, translation, scale 1."""
    if rotation is not None:
        axis, angle = rotation
        if not all(math.isfinite(float(value)) for value in axis) or not math.isfinite(
            float(angle)
        ):
            raise SkpWriteError("rotation axis and angle must contain only finite numbers")
    matrix = _resolve_matrix3x3(matrix3x3, rotation)
    if matrix is None:
        matrix = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    try:
        finite_matrix = all(math.isfinite(float(value)) for value in matrix)
    except (TypeError, ValueError) as exc:
        raise SkpWriteError("matrix3x3 must be nine finite numbers") from exc
    if not finite_matrix:
        raise SkpWriteError("matrix3x3 must contain only finite numbers")
    tx, ty, tz = (float(c) for c in translation)
    if not all(math.isfinite(value) for value in (tx, ty, tz)):
        raise SkpWriteError("translation must contain only finite numbers")
    return [float(c) for c in matrix] + [tx, ty, tz, 1.0]


def _points(points: Sequence[Sequence[float]], label: str) -> List[Point3]:
    out: List[Point3] = []
    for point in points:
        if len(point) != 3:
            raise SkpWriteError(f"{label} points must be (x, y, z) triples")
        coordinates = (float(point[0]), float(point[1]), float(point[2]))
        if not all(math.isfinite(value) for value in coordinates):
            raise SkpWriteError(
                f"{label} point coordinate must be finite, got {coordinates!r}"
            )
        out.append(coordinates)
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
        parent: Optional["ComponentDefinitionBuilder"] = None,
        attribute_dicts: Optional[Sequence[Tuple[str, Dict[str, object]]]] = None,
    ) -> None:
        self._builder = builder
        self.name = name
        self.kind = kind
        self._owner = owner
        self._parent = parent
        self._children: set["ComponentDefinitionBuilder"] = set()
        builder._open_definitions.add(self)
        if parent is not None:
            parent._children.add(self)
        self._placement = placement or {}
        self._container_id = builder._template.alloc_id()
        self._editor = EntityEditor(
            builder._template,
            container_id=self._container_id,
            attributes=attribute_dicts,
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
        attributes: Optional[Dict[str, object]] = None,
        attribute_dict_name: str = "attributes",
        attribute_dicts: Sequence[Tuple[str, Dict[str, object]]] = (),
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
            _resolve_attribute_dicts(
                attributes, attribute_dict_name, attribute_dicts
            ),
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
        attributes: Optional[Dict[str, object]] = None,
        attribute_dict_name: str = "attributes",
        attribute_dicts: Sequence[Tuple[str, Dict[str, object]]] = (),
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
            attributes=_resolve_attribute_dicts(
                attributes, attribute_dict_name, attribute_dicts
            ),
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
        attributes: Optional[Dict[str, object]] = None,
        attribute_dict_name: str = "attributes",
        attribute_dicts: Sequence[Tuple[str, Dict[str, object]]] = (),
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
            attributes=_resolve_attribute_dicts(
                attributes, attribute_dict_name, attribute_dicts
            ),
        )

    def add_group(
        self,
        name: Optional[str] = None,
        translation: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        matrix3x3: Optional[Matrix3x3] = None,
        rotation: Optional[Rotation] = None,
        material: Optional[int] = None,
        layer: Optional[int] = None,
        attributes: Optional[Dict[str, object]] = None,
        attribute_dict_name: str = "attributes",
        attribute_dicts: Sequence[Tuple[str, Dict[str, object]]] = (),
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
            parent=self,
            placement={
                "translation": translation,
                "matrix3x3": matrix3x3,
                "rotation": rotation,
                "material": material,
                "layer": layer,
                "hidden": hidden,
                "attributes": _resolve_attribute_dicts(
                    attributes, attribute_dict_name, attribute_dicts
                ),
            },
        )

    # -- lifecycle --------------------------------------------------------

    def close(self) -> "ComponentDefinitionBuilder":
        if not self._closed:
            if self._children:
                child = next(iter(self._children))
                raise SkpWriteError(
                    f"component definition {self.name!r} has an open group or "
                    f"definition {child.name!r} - close it before closing "
                    f"{self.name!r}"
                )
            if self._parent is not None and self._parent._closed:
                raise SkpWriteError(
                    f"component definition {self._parent.name!r} is already closed - "
                    f"cannot close {self.name!r} after it"
                )
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
            self._builder._open_definitions.discard(self)
            if self._parent is not None:
                self._parent._children.discard(self)
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
                    attributes=placement.get("attributes"),
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
        parent: Optional["ComponentDefinitionBuilder"] = None,
    ) -> None:
        definition_name = builder._unique_definition_name(name or "Group")
        super().__init__(
            builder,
            definition_name,
            kind=1,
            owner=owner,
            placement=placement,
            parent=parent,
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
            self._root = EntityEditor(
                self._template, container=self._template.root_container
            )
        except Exception as exc:
            raise SkpWriteError(f"cannot use VFF template: {exc}") from exc
        self.materials_by_name: Dict[str, int] = dict(self._template.materials)
        self.layers_by_name: Dict[str, int] = dict(self._template.layers)
        self._open_definitions: set[ComponentDefinitionBuilder] = set()
        self._definition_names = {
            definition["name"].lower()
            for definition in self._template.definitions
            if definition.get("name")
        }

    def _unique_definition_name(self, name: str) -> str:
        """SketchUp's rename-on-collision rule: ``Same``, ``Same#1``, ...

        Comparison is case-insensitive, like SketchUp's own definition
        library: ``Same``, ``Same#1``, ``Same#2``, ``Same#3`` then ``same``
        becomes ``same#4``.
        """
        if name.lower() not in self._definition_names:
            self._definition_names.add(name.lower())
            return name
        counter = 1
        while f"{name}#{counter}".lower() in self._definition_names:
            counter += 1
        unique = f"{name}#{counter}"
        self._definition_names.add(unique.lower())
        return unique

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

    def add_texture_material(
        self,
        name: str,
        image_path: str,
        applied_width: Optional[float] = None,
        applied_height: Optional[float] = None,
        opacity: Optional[float] = None,
        filename: Optional[str] = None,
        *,
        image_name: Optional[str] = None,
        average_color: Optional[Sequence[int]] = None,
    ) -> int:
        """Register a PNG/JPEG-textured material and return a handle.

        ``image_path`` may be a path or, as a VFF-only extra, the image bytes.
        ``image_name`` overrides the archive entry name for bytes/paths, and
        ``average_color`` overrides the average RGB SketchUp shows before the
        texture is drawn. Without it the average is computed with Pillow when
        available and falls back to mid-grey otherwise, so pass it explicitly
        when the bytes must not depend on the environment.
        """
        if name in self.materials_by_name:
            return self.materials_by_name[name]
        if isinstance(image_path, (bytes, bytearray)):
            image_bytes = bytes(image_path)
            extension = _image_extension(image_bytes)
            source_name = image_name or f"texture.{extension}"
            stored_filename = filename if filename is not None else source_name
        else:
            image_bytes = Path(image_path).read_bytes()
            extension = _image_extension(image_bytes)
            source_name = image_name or Path(image_path).name or f"texture.{extension}"
            stored_filename = image_path if filename is None else filename
        entry_name = _texture_entry_name(source_name)
        width = 1.0 if applied_width is None else float(applied_width)
        height = 1.0 if applied_height is None else float(applied_height)
        if not math.isfinite(width) or not math.isfinite(height):
            raise SkpWriteError("a texture's applied size must be finite")
        if width <= 0 or height <= 0:
            raise SkpWriteError("a texture's applied size must be positive")
        alpha = 1.0 if opacity is None else float(opacity)
        if not math.isfinite(alpha):
            raise SkpWriteError("texture opacity must be finite")
        if average_color is not None:
            red, green, blue, _alpha = _validated_rgba(average_color)
            average = (red, green, blue)
        else:
            average = _average_color(image_bytes)
        try:
            identifier = self._template.add_texture_material(
                name,
                image_bytes,
                entry_name,
                width,
                height,
                alpha,
                average,
                stored_filename,
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
        attributes: Sequence[Tuple[str, Dict[str, object]]],
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
        if rings or not auto_triangulate:
            # Validates the boundary as planar and gives the same message as
            # create.py for an off-plane hole or a non-planar boundary.
            nx, ny, nz, plane_d = _plane_from_polygon(outer)
            span = max(
                max(point[i] for point in outer) - min(point[i] for point in outer)
                for i in range(3)
            )
            tolerance = max(span, 1.0) * 1e-6
            for ring in rings:
                for point in ring:
                    distance = nx * point[0] + ny * point[1] + nz * point[2] - plane_d
                    if abs(distance) > tolerance:
                        raise SkpWriteError(
                            f"hole point {point} is {abs(distance):.6g} units off the "
                            "face's own plane - a hole must lie on the same plane as "
                            "the outer boundary"
                        )
        coplanar = _is_coplanar(outer)
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
                    attributes=attributes,
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
                    attributes=attributes,
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
        attributes: Optional[Dict[str, object]] = None,
        attribute_dict_name: str = "attributes",
        attribute_dicts: Sequence[Tuple[str, Dict[str, object]]] = (),
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
            _resolve_attribute_dicts(
                attributes, attribute_dict_name, attribute_dicts
            ),
            auto_triangulate,
            holes,
        )

    # -- definitions ------------------------------------------------------

    def add_component_definition(
        self,
        name: str,
        attributes: Optional[Dict[str, object]] = None,
        attribute_dict_name: str = "attributes",
        attribute_dicts: Sequence[Tuple[str, Dict[str, object]]] = (),
        always_faces_camera: bool = False,
        shadows_face_sun: bool = False,
        guid: Optional[str] = None,
    ) -> ComponentDefinitionBuilder:
        """Start a component definition; add geometry inside a ``with`` block."""
        guid_bytes = _archive_uuid(guid)
        return ComponentDefinitionBuilder(
            self,
            self._unique_definition_name(name),
            always_faces_camera=always_faces_camera,
            shadows_face_sun=shadows_face_sun,
            guid=guid_bytes,
            attribute_dicts=_resolve_attribute_dicts(
                attributes, attribute_dict_name, attribute_dicts
            ),
        )

    def add_group(
        self,
        name: Optional[str] = None,
        translation: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        matrix3x3: Optional[Matrix3x3] = None,
        rotation: Optional[Rotation] = None,
        material: Optional[int] = None,
        layer: Optional[int] = None,
        attributes: Optional[Dict[str, object]] = None,
        attribute_dict_name: str = "attributes",
        attribute_dicts: Sequence[Tuple[str, Dict[str, object]]] = (),
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
                "attributes": _resolve_attribute_dicts(
                    attributes, attribute_dict_name, attribute_dicts
                ),
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
        attributes: Optional[Dict[str, object]] = None,
        attribute_dict_name: str = "attributes",
        attribute_dicts: Sequence[Tuple[str, Dict[str, object]]] = (),
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
            attributes=_resolve_attribute_dicts(
                attributes, attribute_dict_name, attribute_dicts
            ),
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

    def set_model_attributes(
        self,
        attributes: Optional[Dict[str, object]] = None,
        attribute_dict_name: str = "attributes",
        attribute_dicts: Sequence[Tuple[str, Dict[str, object]]] = (),
    ) -> None:
        """Attach custom dictionaries to the model itself (VFF-only extra).

        Dictionaries go into the model's existing attribute block.  A name
        that already exists (including one from the template) is replaced
        with the new dictionary; other dictionaries are preserved, so calls
        add to the same block rather than replacing it.
        """
        self._root.add_model_attributes(
            _resolve_attribute_dicts(attributes, attribute_dict_name, attribute_dicts)
        )

    # -- output -----------------------------------------------------------

    def to_bytes(self) -> bytes:
        open_builder = next(iter(self._open_definitions), None)
        if open_builder is not None:
            raise SkpWriteError(
                f"component definition {open_builder.name!r} is still open - exit its "
                "`with` block before saving the file"
            )
        self._root.finalize()
        try:
            return self._template.build()
        except Exception as exc:
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
