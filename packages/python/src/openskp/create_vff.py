"""Create native VFF (SketchUp 2021+) ``.skp`` files.

This is the native writer for the VFF container - the ZIP plus record-tree
format SketchUp has written since the 2021 release.  By default, the empty VFF
document (69-byte file header, ``meta/meta.dat``, ``material.xml``, ``style.xml``,
OpenSKP's own minimal placeholder PNG, ``model.dat``'s 27 top-level TLV
sections, and the deterministic ZIP archive) is constructed in code field by
field by :func:`openskp._blank_document_vff.build_blank_vff` — no bundled
``.skp`` file, rendered SketchUp thumbnail, or byte blob is shipped or read at
run time.  Callers may also pass an existing VFF file or byte buffer via
``template=`` to extend an existing document: the writer parses ``model.dat``'s
record tree by tag with no hard-coded byte offsets, allocates persistent ids
above the next-id counter, adds any needed archive entries, updates
``meta/meta.dat`` with a fresh GUID and save time, and recomputes every ZIP CRC
and the header integrity word.

This version covers usable faces built from vertex coordinates (with holes and
the same edge flags as ``create.py``), solid-colour and textured materials,
positioned UVs, tags, reusable component definitions and groups (including
nested groups and component instances), curves, arcs, polylines, attribute
dictionaries, texts, linear dimensions, construction lines/points, section
planes, scenes, and the model start camera.  Coordinates are in inches.  The
factory is exported as ``openskp.create_vff`` (with ``openskp.VffBuilder`` /
``openskp.VffComponentDefinitionBuilder``).

The shared methods deliberately follow :mod:`openskp.create`: same names, the
same parameters in the same order, the same defaults and the same
``SkpWriteError`` for the same bad input, so a model-building function can use
either builder.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

from ._blank_document_vff import BlankDocumentVff, build_blank_vff
from ._vff_writer.archive import (
    integrity_word,
    read_zip,
    stored_integrity_word,
    verify_integrity,
)
from ._vff_writer.template import EntityEditor, Template
from .create import (
    _DEFAULT_OFFSET,
    DocumentOptions,
    Point3,
    SkpBuilder,
    SkpWriteError,
    _default_up,
    _finite_point3,
    _resolve_construction_line_params,
    _archive_uuid,
    _circle_basis,
    _is_coplanar,
    _plane_from_polygon,
    _resolve_attribute_dicts,
    _resolve_matrix3x3,
)

__all__ = [
    "VffBuilder",
    "VffComponentDefinitionBuilder",
    "CurveHandle",
    "ComponentDefinitionBuilder",
    "create",
    "create_vff",
    "compute_integrity_word",
    "read_stored_integrity_word",
    "check_integrity",
    "DEFAULT_TEMPLATE",
]

class _BlankTemplateSource:
    """In-memory handle for the code-built blank VFF document (no file on disk)."""

    def read_bytes(self) -> bytes:
        return build_blank_vff().data

    def __bytes__(self) -> bytes:
        return build_blank_vff().data


DEFAULT_TEMPLATE = _BlankTemplateSource()

#: A template is a path, raw bytes/bytearray, or a BlankDocumentVff.
TemplateSource = Union[str, "Path", bytes, bytearray, BlankDocumentVff, _BlankTemplateSource]

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
    """Average RGB of a texture, via Pillow when installed, else mid-grey.

    The fallback is deliberate so the writer works without Pillow, but the
    resulting archive's average colour (and therefore material colour) then
    differs from a Pillow-equipped run; pass ``average_color=`` for a stable
    result across environments.
    """
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


@dataclass
class CurveHandle:
    """A curve written by :meth:`VffBuilder.add_circle_curve`."""

    points: List[Tuple[float, float, float]]
    curve_id: int
    edge_ids: List[int] = field(default_factory=list)


def _normalized(vector: Sequence[float]) -> Tuple[float, float, float]:
    length = math.sqrt(sum(float(c) * float(c) for c in vector))
    if not math.isfinite(length) or length < 1e-12:
        raise SkpWriteError("a curve normal must not be the zero vector")
    return tuple(float(c) / length for c in vector)  # type: ignore[return-value]


def _circle_points(centre, num_segments, radius, u, w):
    points = []
    for index in range(num_segments):
        angle = 2.0 * math.pi * index / num_segments
        c, s = math.cos(angle), math.sin(angle)
        points.append(
            (
                centre[0] + radius * (c * u[0] + s * w[0]),
                centre[1] + radius * (c * u[1] + s * w[1]),
                centre[2] + radius * (c * u[2] + s * w[2]),
            )
        )
    return points


def _arc_points(centre, num_segments, radius, u, w, start_angle, end_angle):
    points = []
    for index in range(num_segments + 1):
        angle = start_angle + (end_angle - start_angle) * index / num_segments
        c, s = math.cos(angle), math.sin(angle)
        points.append(
            (
                centre[0] + radius * (c * u[0] + s * w[0]),
                centre[1] + radius * (c * u[1] + s * w[1]),
                centre[2] + radius * (c * u[2] + s * w[2]),
            )
        )
    return points


def _curve_points(
    center: Sequence[float],
    normal: Sequence[float],
    radius: float,
    num_segments: int,
    start_angle: Optional[float] = None,
    end_angle: Optional[float] = None,
):
    if not 3 <= num_segments <= 255:
        raise SkpWriteError(
            f"num_segments must be between 3 and 255, got {num_segments}"
        )
    centre = tuple(float(c) for c in center)
    if not all(math.isfinite(c) for c in centre):
        raise SkpWriteError("a curve centre must contain only finite numbers")
    radius = float(radius)
    if not math.isfinite(radius):
        raise SkpWriteError("a curve radius must be finite")
    unit_normal = _normalized(normal)
    u, w = _circle_basis(unit_normal)
    if start_angle is None or end_angle is None:
        points = _circle_points(centre, num_segments, radius, u, w)
    else:
        start_angle = float(start_angle)
        end_angle = float(end_angle)
        if not math.isfinite(start_angle) or not math.isfinite(end_angle):
            raise SkpWriteError("arc angles must contain only finite numbers")
        points = _arc_points(
            centre,
            num_segments,
            radius,
            u,
            w,
            start_angle,
            end_angle,
        )
    if not all(math.isfinite(value) for point in points for value in point):
        raise SkpWriteError(
            "curve points overflowed to a non-finite coordinate; "
            "use a smaller centre, radius or angle"
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


def _validate_section_names(name: str, symbol: str) -> None:
    for value, label in ((name, "name"), (symbol, "symbol")):
        if not isinstance(value, str):
            raise SkpWriteError(f"add_section_plane: {label} must be a str")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise SkpWriteError(
                f"add_section_plane: {label} contains an unpaired UTF-16 surrogate"
            ) from None


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
        front_uv: Optional[Sequence[Tuple[Sequence[float], Tuple[float, float]]]] = None,
        back_uv: Optional[Sequence[Tuple[Sequence[float], Tuple[float, float]]]] = None,
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
            front_uv,
            back_uv,
            _resolve_attribute_dicts(
                attributes, attribute_dict_name, attribute_dicts
            ),
            auto_triangulate,
            holes,
        )

    def add_circle(
        self,
        center: Point3,
        normal: Tuple[float, float, float],
        radius: float,
        num_segments: int = 24,
        material: Optional[int] = None,
        layer: Optional[int] = None,
        back_material: Optional[int] = None,
        hidden: bool = False,
        front_uv: Optional[Sequence[Tuple[Sequence[float], Tuple[float, float]]]] = None,
        back_uv: Optional[Sequence[Tuple[Sequence[float], Tuple[float, float]]]] = None,
        attributes: Optional[Dict[str, object]] = None,
        attribute_dict_name: str = "attributes",
        attribute_dicts: Sequence[Tuple[str, Dict[str, object]]] = (),
        *,
        holes: Sequence[Sequence[Point3]] = (),
    ) -> None:
        """Add one circular face inside this definition."""
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
        center: Point3,
        normal: Tuple[float, float, float],
        radius: float,
        start_angle: float,
        end_angle: float,
        num_segments: int = 24,
        hidden_edges: bool = False,
        soft_edges: bool = False,
        smooth_edges: bool = False,
    ) -> None:
        """Add one open arc inside this definition."""
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

    def add_text(
        self,
        text: str,
        point: Point3,
        leader: Point3 = (15.0, 15.0, 15.0),
        *,
        layer: Optional[int] = None,
        hidden: bool = False,
        leader_type: int = 2,
        arrow_type: int = 3,
        screen_position: Optional[Tuple[float, float]] = None,
        display_leader: bool = True,
    ) -> None:
        """Add a text entity inside this definition."""
        if self._closed:
            raise SkpWriteError("this definition is already closed")
        self._builder._validate_layer(layer)
        # _prepare_text is a pure helper; it never touches the builder.
        prepared = SkpBuilder._prepare_text(
            None, text, point, leader=leader, leader_type=leader_type,
            arrow_type=arrow_type, screen_position=screen_position,
            display_leader=display_leader,
        )
        _, p, lb, screen, eff_leader, arrow, display = prepared
        self._editor.add_text(
            text, p, lb, leader_type=eff_leader, arrow_type=arrow,
            screen_position=screen if screen_position is not None else None,
            display_leader=display,
            layer=layer, hidden=hidden,
        )

    def add_dimension(
        self,
        p1: Point3,
        p2: Point3,
        offset: Union[float, Point3] = _DEFAULT_OFFSET,
        *,
        offset_vector: Optional[Point3] = None,
        layer: Optional[int] = None,
        hidden: bool = False,
    ) -> None:
        """Add a free linear dimension inside this definition."""
        if self._closed:
            raise SkpWriteError("this definition is already closed")
        self._builder._validate_layer(layer)
        # _prepare_dimension is a pure helper; it never touches the builder.
        prepared = SkpBuilder._prepare_dimension(
            None, p1, p2, offset=offset, offset_vector=offset_vector
        )
        a, b, normal, xaxis, scalar, _has_vector = prepared
        self._editor.add_dimension(
            a, b, normal, xaxis, scalar, layer=layer, hidden=hidden
        )

    def add_construction_line(
        self,
        point: Point3,
        point2: Optional[Point3] = None,
        direction: Optional[Point3] = None,
        layer: Optional[int] = None,
        hidden: bool = False,
    ) -> None:
        if self._closed:
            raise SkpWriteError("this definition is already closed")
        self._builder._validate_layer(layer)
        p, dirv, start, end = _resolve_construction_line_params(
            point, point2, direction
        )
        self._editor.add_guide_line(
            p, dirv, start, end, layer=layer, hidden=hidden
        )

    def add_construction_point(
        self,
        position: Point3,
        layer: Optional[int] = None,
        hidden: bool = False,
    ) -> None:
        if self._closed:
            raise SkpWriteError("this definition is already closed")
        self._builder._validate_layer(layer)
        p = _finite_point3(position, "add_construction_point")
        try:
            self._editor.add_guide_point(p, layer=layer, hidden=hidden)
        except ValueError as exc:
            raise SkpWriteError(str(exc)) from exc

    def add_section_plane(
        self,
        point: Point3,
        normal: Point3,
        *,
        name: str = "",
        symbol: str = "",
        hidden: bool = False,
    ) -> None:
        """Add a section plane inside this definition (VFF-only extra)."""
        if self._closed:
            raise SkpWriteError("this definition is already closed")
        p = _finite_point3(point, "add_section_plane")
        n = _finite_point3(normal, "add_section_plane")
        if n == (0.0, 0.0, 0.0):
            raise SkpWriteError("add_section_plane: normal must be nonzero")
        _validate_section_names(name, symbol)
        self._editor.add_section_plane(
            p, n, name=name, symbol=symbol, hidden=hidden
        )

    def add_polyline(
        self,
        points: Sequence[Point3],
        closed: bool = False,
        hidden_edges: bool = False,
        soft_edges: bool = False,
        smooth_edges: bool = False,
    ) -> None:
        """Add one freeform polyline curve inside this definition."""
        if self._closed:
            raise SkpWriteError("this definition is already closed")
        self._builder._add_polyline_to(
            self._editor, points, closed, hidden_edges, soft_edges, smooth_edges
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
        source: Union[bytes, BlankDocumentVff]
        if template is None or isinstance(template, _BlankTemplateSource):
            source = build_blank_vff()
        elif isinstance(template, BlankDocumentVff):
            source = template
        elif isinstance(template, (bytes, bytearray)):
            source = bytes(template)
        else:
            try:
                source = Path(template).read_bytes()
            except OSError as exc:
                raise SkpWriteError(f"cannot read VFF template {template!r}: {exc}") from exc
        try:
            self._template = Template(source, options)
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
        texture is drawn. Without ``average_color`` the average is computed
        with Pillow when available and falls back to mid-grey otherwise, so
        pass it explicitly when the bytes must not depend on the environment.
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

    def _add_circle_to(
        self,
        editor: EntityEditor,
        center: Point3,
        normal: Tuple[float, float, float],
        radius: float,
        num_segments: int,
        material: Optional[int],
        layer: Optional[int],
        back_material: Optional[int],
        hidden: bool,
        front_uv: Optional[Sequence[Tuple[Sequence[float], Tuple[float, float]]]],
        back_uv: Optional[Sequence[Tuple[Sequence[float], Tuple[float, float]]]],
        attributes: Optional[Dict[str, object]],
        attribute_dict_name: str,
        attribute_dicts: Sequence[Tuple[str, Dict[str, object]]],
        holes: Sequence[Sequence[Point3]],
    ) -> None:
        self._validate_material(material)
        self._validate_material(back_material)
        self._validate_layer(layer)
        resolved_attributes = _resolve_attribute_dicts(
            attributes, attribute_dict_name, attribute_dicts
        )
        centre, unit_normal, _u, _w, points = _curve_points(
            center, normal, radius, num_segments
        )
        rings = [
            _points(getattr(hole, "points", hole), "hole") for hole in holes
        ]
        for ring in rings:
            if len(ring) < 3:
                raise SkpWriteError("a hole needs at least 3 points")
        nx, ny, nz, plane_d = _plane_from_polygon(points)
        span = max(
            max(point[i] for point in points) - min(point[i] for point in points)
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
        editor.validate_face_attributes(
            resolved_attributes,
            front_uv,
            back_uv,
            material,
            back_material,
            unit_normal,
        )
        curve = self._write_circle_curve(
            editor, center, normal, radius, num_segments
        )
        self._add_face_to(
            editor,
            curve.points,
            material,
            layer,
            back_material,
            hidden,
            False,
            False,
            False,
            front_uv,
            back_uv,
            resolved_attributes,
            False,
            holes,
        )

    def _write_circle_curve(
        self,
        editor: EntityEditor,
        center: Point3,
        normal: Tuple[float, float, float],
        radius: float,
        num_segments: int,
        flags: int = 0x06,
    ) -> CurveHandle:
        centre, unit_normal, u, w, points = _curve_points(
            center, normal, radius, num_segments
        )
        frame = _arc_frame(
            centre, unit_normal, float(radius), u, w, 0.0, 2.0 * math.pi
        )
        try:
            curve_id, edges = editor.add_curve_edges(
                points, closed=True, arc_frame=frame, flags=flags
            )
        except ValueError as exc:
            raise SkpWriteError(str(exc)) from exc
        return CurveHandle(points, curve_id, edges)

    def _add_arc_to(
        self,
        editor: EntityEditor,
        center: Point3,
        normal: Tuple[float, float, float],
        radius: float,
        start_angle: float,
        end_angle: float,
        num_segments: int,
        hidden_edges: bool,
        soft_edges: bool,
        smooth_edges: bool,
    ) -> None:
        if float(start_angle) == float(end_angle):
            raise SkpWriteError(
                "start_angle and end_angle must differ - use add_circle for a full circle"
            )
        centre, unit_normal, u, w, points = _curve_points(
            center,
            normal,
            radius,
            num_segments,
            float(start_angle),
            float(end_angle),
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
        try:
            editor.add_curve_edges(
                points,
                arc_frame=frame,
                flags=_edge_flags(hidden_edges, soft_edges, smooth_edges),
            )
        except ValueError as exc:
            raise SkpWriteError(str(exc)) from exc

    def _add_polyline_to(
        self,
        editor: EntityEditor,
        points: Sequence[Point3],
        closed: bool,
        hidden_edges: bool,
        soft_edges: bool,
        smooth_edges: bool,
    ) -> None:
        vertices = _points(points, "polyline")
        if len(vertices) < 2:
            raise SkpWriteError("a polyline needs at least 2 points")
        try:
            editor.add_curve_edges(
                vertices,
                closed=closed,
                flags=_edge_flags(hidden_edges, soft_edges, smooth_edges),
            )
        except ValueError as exc:
            raise SkpWriteError(str(exc)) from exc

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
        front_uv: Optional[Sequence[Tuple[Sequence[float], Tuple[float, float]]]],
        back_uv: Optional[Sequence[Tuple[Sequence[float], Tuple[float, float]]]],
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
        rings = [
            _points(getattr(hole, "points", hole), "hole") for hole in holes
        ]
        for ring in rings:
            if len(ring) < 3:
                raise SkpWriteError("a hole needs at least 3 points")
        flags = _edge_flags(hidden_edges, soft_edges, smooth_edges)
        if auto_triangulate and (front_uv is not None or back_uv is not None):
            raise SkpWriteError(
                "auto_triangulate cannot be combined with front_uv/back_uv positioning"
            )
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
                    front_uv=front_uv,
                    back_uv=back_uv,
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
                    front_uv=front_uv,
                    back_uv=back_uv,
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
        front_uv: Optional[Sequence[Tuple[Sequence[float], Tuple[float, float]]]] = None,
        back_uv: Optional[Sequence[Tuple[Sequence[float], Tuple[float, float]]]] = None,
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
            front_uv,
            back_uv,
            _resolve_attribute_dicts(
                attributes, attribute_dict_name, attribute_dicts
            ),
            auto_triangulate,
            holes,
        )

    # -- definitions ------------------------------------------------------

    def add_circle_curve(
        self,
        center: Point3,
        normal: Tuple[float, float, float],
        radius: float,
        num_segments: int = 24,
        hidden_edges: bool = False,
        soft_edges: bool = False,
        smooth_edges: bool = False,
    ) -> CurveHandle:
        """Add only a circle's curve and edges (VFF-only extra)."""
        return self._write_circle_curve(
            self._root,
            center,
            normal,
            radius,
            num_segments,
            _edge_flags(hidden_edges, soft_edges, smooth_edges),
        )

    def add_circle(
        self,
        center: Point3,
        normal: Tuple[float, float, float],
        radius: float,
        num_segments: int = 24,
        material: Optional[int] = None,
        layer: Optional[int] = None,
        back_material: Optional[int] = None,
        hidden: bool = False,
        front_uv: Optional[Sequence[Tuple[Sequence[float], Tuple[float, float]]]] = None,
        back_uv: Optional[Sequence[Tuple[Sequence[float], Tuple[float, float]]]] = None,
        attributes: Optional[Dict[str, object]] = None,
        attribute_dict_name: str = "attributes",
        attribute_dicts: Sequence[Tuple[str, Dict[str, object]]] = (),
        *,
        holes: Sequence[Sequence[Point3]] = (),
    ) -> None:
        """Add one circular face to the model root."""
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
        center: Point3,
        normal: Tuple[float, float, float],
        radius: float,
        start_angle: float,
        end_angle: float,
        num_segments: int = 24,
        hidden_edges: bool = False,
        soft_edges: bool = False,
        smooth_edges: bool = False,
    ) -> None:
        """Add one open arc to the model root."""
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
        points: Sequence[Point3],
        closed: bool = False,
        hidden_edges: bool = False,
        soft_edges: bool = False,
        smooth_edges: bool = False,
    ) -> None:
        """Add one freeform polyline curve to the model root."""
        self._add_polyline_to(
            self._root, points, closed, hidden_edges, soft_edges, smooth_edges
        )

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

    def add_text(
        self,
        text: str,
        point: Point3,
        leader: Point3 = (15.0, 15.0, 15.0),
        *,
        layer: Optional[int] = None,
        hidden: bool = False,
        leader_type: int = 2,
        arrow_type: int = 3,
        screen_position: Optional[Tuple[float, float]] = None,
        display_leader: bool = True,
    ) -> None:
        """Add a leader or screen text to the model root."""
        self._validate_layer(layer)
        # _prepare_text is a pure helper; it never touches the builder.
        prepared = SkpBuilder._prepare_text(
            None, text, point, leader=leader, leader_type=leader_type,
            arrow_type=arrow_type, screen_position=screen_position,
            display_leader=display_leader,
        )
        _, p, lb, screen, eff_leader, arrow, display = prepared
        self._root.add_text(
            text, p, lb, leader_type=eff_leader, arrow_type=arrow,
            screen_position=screen if screen_position is not None else None,
            display_leader=display,
            layer=layer, hidden=hidden,
        )

    def add_dimension(
        self,
        p1: Point3,
        p2: Point3,
        offset: Union[float, Point3] = _DEFAULT_OFFSET,
        *,
        offset_vector: Optional[Point3] = None,
        layer: Optional[int] = None,
        hidden: bool = False,
    ) -> None:
        """Add a free linear dimension to the model root."""
        self._validate_layer(layer)
        # _prepare_dimension is a pure helper; it never touches the builder.
        prepared = SkpBuilder._prepare_dimension(
            None, p1, p2, offset=offset, offset_vector=offset_vector
        )
        a, b, normal, xaxis, scalar, _has_vector = prepared
        self._root.add_dimension(
            a, b, normal, xaxis, scalar, layer=layer, hidden=hidden
        )

    def add_construction_line(
        self,
        point: Point3,
        point2: Optional[Point3] = None,
        direction: Optional[Point3] = None,
        layer: Optional[int] = None,
        hidden: bool = False,
    ) -> None:
        """Add a bounded or unbounded construction line to the model root."""
        self._validate_layer(layer)
        p, dirv, start, end = _resolve_construction_line_params(
            point, point2, direction
        )
        self._root.add_guide_line(
            p, dirv, start, end, layer=layer, hidden=hidden
        )

    def add_construction_point(
        self,
        position: Point3,
        layer: Optional[int] = None,
        hidden: bool = False,
    ) -> None:
        """Add a construction point to the model root."""
        self._validate_layer(layer)
        p = _finite_point3(position, "add_construction_point")
        try:
            self._root.add_guide_point(p, layer=layer, hidden=hidden)
        except ValueError as exc:
            raise SkpWriteError(str(exc)) from exc

    def add_section_plane(
        self,
        point: Point3,
        normal: Point3,
        *,
        name: str = "",
        symbol: str = "",
        hidden: bool = False,
        active: bool = False,
    ) -> None:
        """Add a section plane; VFF extras are keyword-only."""
        p = _finite_point3(point, "add_section_plane")
        n = _finite_point3(normal, "add_section_plane")
        if n == (0.0, 0.0, 0.0):
            raise SkpWriteError("add_section_plane: normal must be nonzero")
        _validate_section_names(name, symbol)
        self._root.add_section_plane(
            p, n, name=name, symbol=symbol, hidden=hidden, active=active
        )

    def _validate_camera(
        self,
        eye: Point3,
        target: Point3,
        up: Optional[Point3],
        perspective: bool,
        fov: float,
        ortho_height: float,
    ) -> Tuple[Point3, Point3, Point3, bool, float, float]:
        e = (float(eye[0]), float(eye[1]), float(eye[2]))
        t = (float(target[0]), float(target[1]), float(target[2]))
        numbers = e + t + (fov, ortho_height) + (
            tuple(float(v) for v in up) if up is not None else ()
        )
        if not all(math.isfinite(float(v)) for v in numbers):
            raise SkpWriteError("scene camera values must be finite numbers")
        d = [t[i] - e[i] for i in range(3)]
        if not all(math.isfinite(value) for value in d):
            raise SkpWriteError("scene camera values must be finite numbers")
        if not any(d):
            raise SkpWriteError("scene camera eye and target coincide")
        corrected = _default_up(e, t) if up is None else tuple(float(v) for v in up)
        cross = (
            d[1] * corrected[2] - d[2] * corrected[1],
            d[2] * corrected[0] - d[0] * corrected[2],
            d[0] * corrected[1] - d[1] * corrected[0],
        )
        if math.hypot(*cross) <= 1e-9 * math.hypot(*d) * max(
            math.hypot(*corrected), 1e-300
        ):
            raise SkpWriteError(
                "scene up vector is zero or parallel to the view direction"
            )
        if not 0 < float(fov) < 180:
            raise SkpWriteError(
                "scene field of view must be between 0 and 180 degrees"
            )
        if float(ortho_height) <= 0:
            raise SkpWriteError("scene ortho_height must be positive")
        return e, t, corrected, bool(perspective), float(fov), float(ortho_height)

    def add_scene(
        self,
        name: str,
        eye: Point3,
        target: Point3,
        up: Optional[Point3] = None,
        *,
        perspective: bool = True,
        fov: float = 35.0,
        ortho_height: float = 200.0,
        hidden_layers: Sequence[int] = (),
    ) -> None:
        """Add a scene (camera plus hidden tags) without changing the model camera."""
        if not name:
            raise SkpWriteError("a scene needs a name")
        try:
            units = len(name.encode("utf-16-le")) // 2
        except UnicodeEncodeError as exc:
            raise SkpWriteError(f"scene name is not valid text: {exc}") from None
        if units >= 0xFF:
            raise SkpWriteError("scene name too long to encode (255 char limit)")
        if any(s["name"] == name for s in self._template.scenes):
            raise SkpWriteError(f"a scene named {name!r} already exists")
        hidden = tuple(dict.fromkeys(hidden_layers))
        known = set(self.layers_by_name.values())
        unknown = [h for h in hidden if h not in known]
        if unknown:
            raise SkpWriteError(
                f"hidden_layers has handles add_layer never returned: {unknown}"
            )
        e, t, u, perspective, fov, ortho_height = self._validate_camera(
            eye, target, up, perspective, fov, ortho_height
        )
        self._template.add_scene(
            name, e, t, u, perspective=perspective, fov=fov,
            ortho_height=ortho_height, hidden_layers=hidden,
        )

    def set_camera(
        self,
        eye: Point3,
        target: Point3,
        up: Optional[Point3] = None,
        *,
        perspective: bool = True,
        fov: float = 35.0,
        ortho_height: float = 200.0,
    ) -> None:
        """Set the model's start camera (VFF-only)."""
        e, t, u, perspective, fov, ortho_height = self._validate_camera(
            eye, target, up, perspective, fov, ortho_height
        )
        self._template.set_camera(
            e, t, u, perspective=perspective, fov=fov,
            ortho_height=ortho_height,
        )

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
