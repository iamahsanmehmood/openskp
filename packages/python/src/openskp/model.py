"""Data model for parsed SketchUp files.

This module defines the core dataclasses that represent every entity
extracted from an SKP file — vertices, edges, faces, layers, materials,
component definitions, instances, and the top-level model container.

It also provides the :class:`SkpFile` entry-point that orchestrates
parsing from a ``.skp`` file path.

Typical usage::

    from openskp import SkpFile

    skp = SkpFile.open("building.skp")
    model = skp.parse()
    print(model.version, len(model.definitions))
"""

from __future__ import annotations

import contextlib
import gc
import math
import pathlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

from .errors import SkpParseError

if TYPE_CHECKING:
    from . import instanced_scene, scene


def _convert_pages(parsed: Dict[str, Any]) -> List["Page"]:
    """Convert raw parsed scene records into :class:`Page` objects."""
    layer_id_to_name = parsed.get("layer_id_to_name", {})
    return [
        Page(
            name=pg.get("name", ""),
            eye=pg.get("eye"),
            target=pg.get("target"),
            up=pg.get("up"),
            fov=pg.get("fov", 35.0),
            parallel=pg.get("parallel", False),
            ortho_height=pg.get("ortho_height", 0.0),
            hidden_layers=[
                layer_id_to_name[i]
                for i in pg.get("hidden_layer_ids", [])
                if i in layer_id_to_name
            ],
            hidden_entity_ids=list(pg.get("hidden_entity_ids", [])),
            capture_flags=pg.get("capture_flags"),
        )
        for pg in (parsed.get("pages") or [])
    ]


# ── TLV node (raw parse tree) ─────────────────────────────────────────────


@dataclass
class TlvNode:
    """A single Tag-Length-Value node in the binary parse tree.

    Attributes:
        offset: Byte offset of this node in the original buffer.
        tag: Two-byte tag encoded as upper-case hex (e.g. ``"FF01"``).
        size: Payload length in bytes.
        children: Nested child nodes (empty for leaf nodes).
        payload: Raw payload bytes (empty when *children* is populated).
    """

    offset: int
    tag: str
    size: int
    children: List["TlvNode"] = field(default_factory=list)
    payload: bytes = b""


# ── Geometry primitives ───────────────────────────────────────────────────


@dataclass
class Vertex:
    """A 3-D vertex.

    Attributes:
        id: Unique vertex identifier within its definition.
        x: X coordinate (inches in SketchUp's internal unit).
        y: Y coordinate.
        z: Z coordinate.
    """

    id: int
    x: float
    y: float
    z: float


@dataclass
class Edge:
    """A directed edge connecting two vertices.

    Attributes:
        id: Unique edge identifier within its definition.
        v1_id: Start-vertex ID.
        v2_id: End-vertex ID.
        soft: Soft edge (merges the faces it borders into one surface).
        smooth: Smooth edge (normals interpolate across it).
        hidden: Edge hidden by the user.
        layer: This edge's own layer id, or ``None`` when the file
            carries no explicit override (SketchUp's own display then
            falls back to whatever layer the containing instance/
            placement is on - resolve via :attr:`SkpModel.layers`).
            Only meaningful for a *loose* edge (one no face uses) - a
            face-bounded edge's layer is cosmetic, since the face itself
            carries the layer that actually matters for rendering. Added
            because a definition made entirely of loose edges (a
            structural-framing/light-gauge-steel member drawn as a
            construction line, not a solid) has no other layer signal
            anywhere on it.
        curve_id: SketchUp's own ``Edge#curve`` grouping - the id of the
            Curve object this edge belongs to, or ``None`` when it
            belongs to none (``edge.curve == nil`` in the source file).
            Edges sharing the same curve_id were drawn as one continuous
            multi-segment line/arc by the author; see
            :func:`loose_edge_runs` to walk them back into ordered runs.
    """

    id: int
    v1_id: int
    v2_id: int
    soft: bool = False
    smooth: bool = False
    hidden: bool = False
    layer: Optional[int] = None
    curve_id: Optional[int] = None
    persistent_id: int = 0


@dataclass
class Face:
    """A planar polygon face defined by edge loops.

    Attributes:
        id: Unique face identifier within its definition.
        loops: Ordered list of loops.  Each loop is a list of
            ``(edge_id, orientation)`` tuples where *orientation* is
            ``1`` for forward or ``-1`` for reversed.
        normal: Optional outward-facing normal vector ``(nx, ny, nz)``.
        material_id: Material of the face's FRONT side, or ``None``.
        back_material_id: Material of the face's BACK side, or ``None``.
            A face painted only on its back (front unpainted) is common when
            the author painted the visible side of a downward-facing cap;
            renderers should show this material on the back side, as
            SketchUp does.
        uv_transform: Per-face texture mapping for a *positioned* /
            photo-fitted texture (SketchUp's pins), or ``None`` when the
            texture is untouched (default projection applies).  A 9-tuple:
            a 3×3 **row-major** matrix mapping texture space → face plane.
            To compute the UV of a point ``p`` (inches):

            1. Plane basis from the face normal ``n``:
               ``xr = normalize(Z × n)``, ``yr = n × xr`` (for a vertical
               ``n`` - ``|Z × n| < 1e-3``, SketchUp's own tolerance -
               ``(X, Y)`` looking up and ``(−X, Y)`` looking down).
            2. ``uvq = [p·xr, p·yr, 1] @ inv(M)``  (row-vector convention).
            3. ``u = uvq[0]/uvq[2] / tile_w``, ``v = uvq[1]/uvq[2] / tile_h``
               with the material texture's tile size in inches.

            When the texture is untouched (``None``), the default is
            ``u = (p·xr)/tile_w``, ``v = (p·yr)/tile_h``.  Distorted
            (4-pin) mappings are projective: ``uvq[2]`` ≠ 1.
        uv_transform_back: Same for the face's back side, or ``None``.
        uv_projected: The texture is PROJECTED (e.g. the Add Location
            terrain drape): its UVs run in the projection plane's frame,
            not the face frame.
        uv_projected_back: Same for the face's back side.
        hidden: Whether the face is hidden (SketchUp's "Hide" on this
            specific face, not a layer/tag visibility toggle).
        layer: This face's own layer id, or ``None`` when the file
            carries no explicit override (falls back to whatever layer
            the containing instance/placement is on - resolve via
            :attr:`SkpModel.layers`). Previously only reachable through
            :func:`SkpFile.build_scene`/``build_instanced_scene``'s
            internal, private builder state - a consumer working
            directly with :func:`SkpFile.parse`'s typed model had no way
            to read a face's own layer at all.
    """

    id: int
    loops: List[List[Tuple[int, int]]] = field(default_factory=list)
    normal: Optional[Tuple[float, float, float]] = None
    material_id: Optional[int] = None
    back_material_id: Optional[int] = None
    uv_transform: Optional[Tuple[float, ...]] = None
    uv_transform_back: Optional[Tuple[float, ...]] = None
    uv_projected: bool = False
    uv_projected_back: bool = False
    hidden: bool = False
    layer: Optional[int] = None
    persistent_id: int = 0


# ── Layers & Materials ────────────────────────────────────────────────────


@dataclass
class Layer:
    """A SketchUp layer (tag).

    Attributes:
        name: Human-readable layer name.
        color_r: Red channel (0–255).
        color_g: Green channel (0–255).
        color_b: Blue channel (0–255).
        hidden: Whether the layer's visibility is switched off (SketchUp's
            Tags panel eye icon). Read from the layer manager's own
            visibility byte in ``model.dat`` (tag ``8E3C``, one of the
            layer record's own child nodes) for both legacy and modern
            (VFF) files alike — confirmed byte-for-byte against a real
            production file's own Tags panel. A layer with no visibility
            byte at all (e.g. one whose only trace is a
            ``Layer_<name>``-prefixed material, with no corresponding
            layer-manager record) defaults to ``False`` (visible).
    """

    name: str
    color_r: int = 200
    color_g: int = 200
    color_b: int = 200
    hidden: bool = False


@dataclass
class Style:
    """A rendering style bundled in the file (SketchUp's Styles browser).

    Attributes:
        name: Style name.
        front_color: Default front face color ``(r, g, b)`` 0-255, or
            ``None``. Unpainted faces show it.
        back_color: Back face color ``(r, g, b)`` 0-255, or ``None``.
            Unpainted faces seen from behind show it — an author may e.g.
            pick a green back color so unpainted garden faces read as grass.
    """

    name: str = ""
    front_color: Optional[Tuple[int, int, int]] = None
    back_color: Optional[Tuple[int, int, int]] = None


@dataclass
class Texture:
    """A material's texture image, extracted from the SKP container.

    SketchUp stores the image next to the material's XML inside the embedded
    ZIP, and the real-world tile size (how many inches one repetition of the
    image covers) in the material definition.

    Attributes:
        filename: Image file name as referenced by the material.
        width: Tile width in **inches** (SketchUp's internal unit); ``0.0``
            when the file does not specify it.
        height: Tile height in inches; ``0.0`` when unspecified.
        data: Raw image bytes (PNG/JPG as stored), or ``None`` when the
            image entry was missing from the container.
    """

    filename: str = ""
    width: float = 0.0
    height: float = 0.0
    data: Optional[bytes] = None

    def save(self, filepath: str | pathlib.Path) -> pathlib.Path:
        """Write the image bytes to *filepath* and return the path.

        Raises:
            ValueError: If the texture carries no image data.
        """
        if self.data is None:
            raise ValueError(f"Texture {self.filename!r} has no image data")
        p = pathlib.Path(filepath)
        p.write_bytes(self.data)
        return p


@dataclass
class Material:
    """A surface material.

    Attributes:
        name: Material name.
        color: RGBA colour tuple ``(r, g, b, a)`` with each value in 0–255.
        transparency: Opacity factor where ``0.0`` is fully transparent and
            ``1.0`` is fully opaque.
        id: Numeric material ID from the TLV stream — the value that
            :attr:`Face.material_id` references, so callers can resolve a
            face's material.  ``None`` when the file assigns the material no
            ID (e.g. it is never referenced by geometry).  When several TLV
            IDs alias the same material, this holds the first one seen; every
            ID still resolves through :attr:`SkpModel.materials_by_id`.
        texture: The material's :class:`Texture`, or ``None`` for a plain
            colour material.
        colorized: ``True`` for a colourized copy of a textured material
            (SketchUp's ``[Name]1`` materials, ``type="2"`` in the XML).
            The texture image is shared with the source material as stored;
            viewers must re-tint it toward :attr:`color` for faithful
            display.
        colorize_type: How to re-tint when :attr:`colorized` — ``0`` shifts
            every pixel's hue/lightness/saturation by the delta between the
            image average and :attr:`color`; ``1`` tints (greyscales the
            image, then applies the colour's hue/saturation).
    """

    name: str
    color: Tuple[int, int, int, int] = (200, 200, 200, 255)
    transparency: float = 1.0
    id: Optional[int] = None
    texture: Optional[Texture] = None
    colorized: bool = False
    colorize_type: int = 0


# ── Component hierarchy ───────────────────────────────────────────────────


@dataclass
class Instance:
    """A placed instance (component or group) in the scene graph.

    Attributes:
        name: Display name of the instance.
        ref_idx: Index into :attr:`SkpModel.definitions` for the
            referenced component definition.
        guid: Globally-unique identifier string.
        matrix: Transformation matrix stored as a flat **13-element**
            list, not a 4x4 in disguise: indices 0-8 are the 3x3
            rotation/scale part in row-major order, 9-11 are the
            translation, and 12 is a scale scalar - ``1/w`` of
            SketchUp's homogeneous term, 1.0 for nearly every instance
            (an instance can be scaled through it instead of through the
            3x3; the translation is then in the scaled units). To
            transform a point ``(x, y, z)``::

                s  = m[12]
                tx = s * (m[0]*x + m[1]*y + m[2]*z + m[9])
                ty = s * (m[3]*x + m[4]*y + m[5]*z + m[10])
                tz = s * (m[6]*x + m[7]*y + m[8]*z + m[11])

            Verified directly against :func:`openskp._core.transform_point`
            and :func:`openskp._core.multiply_matrices`, which are what
            every internal code path actually uses (they fold ``s`` into
            the other twelve values, so a composed matrix always has
            ``m[12] == 1``).
        layer: This instance's own explicit layer override, or ``""``
            when it has none. An instance without an explicit override
            inherits its *placement's* layer, which can only be resolved
            once the scene graph is flattened - see
            :meth:`SkpFile.build_scene`'s ``InstanceNode.layer`` for that
            resolved value.
        properties: Arbitrary key/value dynamic attributes attached
            directly to this instance (SketchUp's Dynamic Components) -
            a backward-compatible view of
            ``attribute_dictionaries.get("dynamic_attributes", {})``.
        attribute_dictionaries: Every custom attribute dictionary
            attached to this instance, keyed by the dictionary's own
            declared name - not just the one SketchUp's own Dynamic
            Components extension uses. A real instance routinely carries
            several at once (a SketchUp extension's own dictionary
            alongside SketchUp's, or several of the extension's own).
            Unlike :attr:`properties`, values here keep their real type
            (``str``/``int``/``float``/``None``/a 3-tuple for a Point3d
            or Vector3d/a possibly-nested ``list``) rather than being
            stringified - an extension is free to store a value as a
            genuine array or as its own string-encoded convention (both
            are observed in real files), and this preserves whichever
            one it chose.
        material_id: Numeric material ID painted onto the instance itself
            (SketchUp's "paint the component"), or ``None``.  Faces inside
            the placed definition whose own :attr:`Face.material_id` is
            ``None`` inherit this material — consumers must resolve that
            inheritance themselves, like the official SDK does on export.
        hidden: Whether the instance itself is hidden (SketchUp's "Hide"
            on this specific component/group placement, not a layer/tag
            visibility toggle).
    """

    name: str = ""
    ref_idx: int = -1
    guid: str = ""
    matrix: List[float] = field(default_factory=lambda: [
        1, 0, 0, 0,
        0, 1, 0, 0,
        0, 0, 1, 0,
        0, 0, 0, 1,
    ])
    layer: str = ""
    properties: Dict[str, str] = field(default_factory=dict)
    attribute_dictionaries: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    material_id: Optional[int] = None
    hidden: bool = False
    id: Optional[int] = None


@dataclass
class SectionPlane:
    plane: List[float] = field(default_factory=lambda: [0.0, 0.0, 1.0, 0.0])
    name: str = ""
    label: str = ""
    hidden: bool = False


@dataclass
class TextEntity:
    """A leader-text or screen-text label (SketchUp's Text tool).

    Attributes:
        text: The displayed string.
        hidden: Whether the label is hidden.
        point: Anchor point ``(x, y, z)`` in owner-local inches (world space
            for model-root texts), or ``None`` when unresolved.
        label_point: Where the label text floats in owner-local inches, or
            ``None`` when unresolved.
        layer: Explicit tag name, or ``""`` for the default tag (Layer0).
        screen_position: Normalized screen fractions ``(x, y)`` (used when
            ``leader_type == 0``), or ``None``.
        leader_type: SketchUp leader type (``0`` screen, ``1`` view, ``2`` pushpin).
        arrow_type: Leader arrow type (``0``..``4``, default ``3``).
        display_leader: Independent leader visibility switch.
    """

    text: str = ""
    hidden: bool = False
    point: Optional[Tuple[float, float, float]] = None
    label_point: Optional[Tuple[float, float, float]] = None
    layer: str = ""
    screen_position: Optional[Tuple[float, float]] = None
    leader_type: int = 2
    arrow_type: int = 3
    display_leader: bool = True


@dataclass
class Dimension:
    """A linear dimension (SketchUp's Dimension tool).

    Both VFF (2021+) and legacy (pre-2021) readers expose owner-local geometry
    and drawing properties on ``root.dimensions`` and ``Definition.dimensions``;
    :attr:`SkpModel.dimensions` also retains the world-space VFF compatibility list.

    Attributes:
        text: The displayed text. Empty when the dimension shows its
            auto-computed measured value (the caller formats ``|b − a|``).
        hidden: Whether the dimension is hidden.
        a: First measured point ``(x, y, z)`` in inches, or ``None`` when
            unresolved.
        b: Second measured point.
        offset: Offset distance (inches) — how far the dimension line sits
            from the ``a``–``b`` segment, along the in-plane perpendicular.
        plane_x: Historical name: for a VFF dimension this is tag ``5BCF``, which
            is the plane NORMAL (SketchUp's ``SUDimensionLinearGetNormal``
            equals it); for a legacy dimension this is the plane's X axis, or ``None``.
        normal: Historical name: for a VFF dimension this is tag ``5BD0``, the
            plane's X axis; for a legacy dimension this is the plane normal, or ``None``.
        offset_vector: Explicit offset vector (inches) for legacy or VFF dimensions
            constrained to a plane axis, preserving the side of the line
            independently of endpoint ordering, or ``None``.
        layer: Explicit tag name, or ``""`` for the default tag (Layer0).
    """

    text: str = ""
    hidden: bool = False
    a: Optional[Tuple[float, float, float]] = None
    b: Optional[Tuple[float, float, float]] = None
    offset: float = 0.0
    plane_x: Optional[Tuple[float, float, float]] = None
    normal: Optional[Tuple[float, float, float]] = None
    offset_vector: Optional[Tuple[float, float, float]] = None
    layer: str = ""


@dataclass
class Page:
    """A saved scene (SketchUp's "Scenes" tabs; "pages" in the SDK).

    Attributes:
        name: Scene name as shown on its tab.
        eye: Camera position ``(x, y, z)`` in inches, or ``None``.
        target: Point the camera looks at, in inches.
        up: Camera up vector.
        fov: Field of view in degrees (SketchUp default 35).
        parallel: ``True`` when the scene uses parallel (orthographic)
            projection; ``fov`` still holds the stored perspective angle.
        ortho_height: Visible height in inches when ``parallel``.
        hidden_layers: Names of the layers this scene hides.
        hidden_entity_ids: Persistent ids of the entities this scene hides
            (see ``Instance.id``, ``Edge.persistent_id`` and
            ``Face.persistent_id``).
        capture_flags: Which properties the scene saves (the boxes ticked
            under "Properties to save" in SketchUp's Scenes panel), as the
            u32 the file stores, or ``None`` when the reader found none
            (or, in the classic fallback scan, a word with bits above
            ``0x1FF``, which are not these flags).
            The ``use_*`` properties below read its bits. It tells an empty
            ``hidden_entity_ids`` that means "this scene hides nothing" from
            one that means "this scene leaves hidden objects alone".
    """

    name: str = ""
    eye: Optional[Tuple[float, float, float]] = None
    target: Optional[Tuple[float, float, float]] = None
    up: Optional[Tuple[float, float, float]] = None
    fov: float = 35.0
    parallel: bool = False
    ortho_height: float = 0.0
    hidden_layers: List[str] = field(default_factory=list)
    hidden_entity_ids: List[int] = field(default_factory=list)
    capture_flags: Optional[int] = None

    def _flag(self, bit: int) -> Optional[bool]:
        if self.capture_flags is None:
            return None
        return bool(self.capture_flags & bit)

    @property
    def use_camera(self) -> Optional[bool]:
        """The scene saves its camera ("Camera Location"), ``0x001``."""
        return self._flag(PAGE_USE_CAMERA)

    @property
    def use_rendering_options(self) -> Optional[bool]:
        """The scene saves its style ("Style and Fog"), ``0x002``."""
        return self._flag(PAGE_USE_RENDERING_OPTIONS)

    @property
    def use_shadow_info(self) -> Optional[bool]:
        """The scene saves its shadows ("Shadow Settings"), ``0x004``."""
        return self._flag(PAGE_USE_SHADOW_INFO)

    @property
    def use_axes(self) -> Optional[bool]:
        """The scene saves the axes ("Axes Location"), ``0x008``."""
        return self._flag(PAGE_USE_AXES)

    @property
    def use_hidden_layers(self) -> Optional[bool]:
        """The scene saves which layers it hides ("Visible Tags"),
        ``0x020``."""
        return self._flag(PAGE_USE_HIDDEN_LAYERS)

    @property
    def use_section_planes(self) -> Optional[bool]:
        """The scene saves the active section ("Active Section Planes"),
        ``0x040``."""
        return self._flag(PAGE_USE_SECTION_PLANES)

    def _hidden(self, bit: int) -> Optional[bool]:
        # Since SketchUp 2020.1 a scene saves hidden geometry and hidden
        # objects separately (0x080, 0x100). A file with neither bit has
        # only the older combined one (0x010), which then covers both.
        flags = self.capture_flags
        if flags is None:
            return None
        if flags & (PAGE_USE_HIDDEN_GEOMETRY | PAGE_USE_HIDDEN_OBJECTS):
            return bool(flags & bit)
        return bool(flags & PAGE_USE_HIDDEN)

    @property
    def use_hidden_geometry(self) -> Optional[bool]:
        """The scene saves which edges and faces it hides ("Hidden
        Geometry"), ``0x080``."""
        return self._hidden(PAGE_USE_HIDDEN_GEOMETRY)

    @property
    def use_hidden_objects(self) -> Optional[bool]:
        """The scene saves which groups and components it hides ("Hidden
        Objects"), ``0x100``. When ``True``, an empty
        ``hidden_entity_ids`` means every object is shown."""
        return self._hidden(PAGE_USE_HIDDEN_OBJECTS)


# Bits of ``Page.capture_flags``, named after SketchUp's public Ruby API
# (``Sketchup::Page#use_camera?`` and so on). Checked on the fixtures in
# tests/test_scene_capture_flags.py and one flag at a time on a model
# written with SketchUp's library (#660, #661): hidden geometry alone is
# 0x090, hidden objects alone 0x110, the older combined 0x010 set with
# either. Over 592 files (#660), every value is known and agrees with the
# camera.
PAGE_USE_CAMERA = 0x001
PAGE_USE_RENDERING_OPTIONS = 0x002
PAGE_USE_SHADOW_INFO = 0x004
PAGE_USE_AXES = 0x008
PAGE_USE_HIDDEN = 0x010          # before SketchUp 2020.1: geometry + objects
PAGE_USE_HIDDEN_LAYERS = 0x020
PAGE_USE_SECTION_PLANES = 0x040
PAGE_USE_HIDDEN_GEOMETRY = 0x080
PAGE_USE_HIDDEN_OBJECTS = 0x100


@dataclass
class ConstructionLine:
    """A construction/guide line (SketchUp's Construction Line tool).

    Stored internally (and here, unchanged) as a point + normalized
    direction + two signed distance parameters along that direction
    marking where the visible segment starts/ends - the same shape
    :class:`Sketchup::ConstructionLine`'s own ``start``/``end``/
    ``direction`` properties expose. A parameter magnitude of ``1e30``
    means unbounded in that direction (SketchUp draws this as an infinite
    guide line through ``point``) - ``start``/``end`` come back ``None``
    in that case, matching the real API returning ``nil``.

    Attributes:
        point: A point on the line, in inches (world space) - matches the
            bounded case's own ``start``, or the anchor point given for an
            infinite line.
        direction: The line's normalized direction vector.
        start: The bounded segment's start point, or ``None`` if unbounded
            in this direction.
        end: The bounded segment's end point, or ``None`` if unbounded.
    """

    point: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    direction: Tuple[float, float, float] = (1.0, 0.0, 0.0)
    start: Optional[Tuple[float, float, float]] = None
    end: Optional[Tuple[float, float, float]] = None
    layer: str = ""
    hidden: bool = False


@dataclass
class ArcCurve:
    """The analytic arc behind a curve: SketchUp's own ``ArcCurve``.

    A curve that SketchUp drew as an arc or a circle is a run of straight
    edges (:attr:`Edge.curve_id`) *and* this exact description. The arc is
    the set of points ``center + cos(t) * x_axis + sin(t) * y_axis`` for ``t``
    from :attr:`start_angle` to :attr:`end_angle`. For a circle
    ``x_axis`` and ``y_axis`` are perpendicular and both of length
    :attr:`radius`; an arc that has been scaled non-uniformly keeps its arc
    identity and stores an affine image of a circle, where they are not (then
    :attr:`radius` is the length of ``x_axis`` only). Arcs and circles that
    bound a face are arcs too. Lengths are in the model's units (inches), in
    the definition's local space, like :attr:`Vertex.x`.

    Attributes:
        center: ``(x, y, z)`` of the arc's centre.
        normal: Unit normal of the arc's plane.
        x_axis: Vector from the centre to the arc's start (length = radius).
        y_axis: ``normal x x_axis``.
        start_angle: Start parameter in radians.
        end_angle: End parameter in radians. A full circle has
            ``end_angle == start_angle`` in a classic (pre-2021) file and
            ``start_angle + 2 pi`` in a 2021+ file; :attr:`is_full_circle`
            covers both.
    """

    center: Tuple[float, float, float]
    normal: Tuple[float, float, float]
    x_axis: Tuple[float, float, float]
    y_axis: Tuple[float, float, float]
    start_angle: float
    end_angle: float

    @property
    def radius(self) -> float:
        return math.sqrt(sum(c * c for c in self.x_axis))

    @property
    def is_full_circle(self) -> bool:
        sweep = (self.end_angle - self.start_angle) % (2.0 * math.pi)
        return min(sweep, 2.0 * math.pi - sweep) < 1e-9


@dataclass
class ConstructionPoint:
    """A construction/guide point (SketchUp's Construction Point tool).

    Attributes:
        position: The point's position, in inches (world space).
        layer: Name of the layer (tag) the guide is assigned to, or ``""``
            for the default layer (Layer0).
        hidden: Whether the guide itself is hidden.
    """

    position: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    layer: str = ""
    hidden: bool = False


@dataclass
class Definition:
    """A component definition containing reusable geometry.

    Attributes:
        id: Internal definition index.
        guid: Globally-unique identifier.
        name: Human-readable component name.
        vertices: Mapping of vertex ID → :class:`Vertex`.
        edges: Mapping of edge ID → :class:`Edge`.
        faces: Mapping of face ID → :class:`Face`.
        instances: Child instances placed inside this definition.
        construction_lines: Construction/guide lines - see
            :class:`ConstructionLine`.
        construction_points: Construction/guide points - see
            :class:`ConstructionPoint`.
        arcs: The analytic arc of every curve that is one, keyed by curve id
            - the :attr:`Edge.curve_id` of the edges that form it. A curve
            that is not an arc (a freehand or polygon curve) has no entry.
        attribute_dictionaries: The definition's OWN attribute
            dictionaries, keyed by dictionary name, values in their real
            types (2021+ files; not read from classic files yet). SketchUp's
            Dynamic Components keep a component's attributes, formulas and
            option definitions here (``"dynamic_attributes"``), while each
            placement only stores its current values on the instance.
        always_faces_camera: SketchUp's "always face camera" component
            behavior (2D people / tree cut-outs that rotate to face the
            viewer). Consumers typically render such instances as
            billboards.
        is_image: ``True`` when this definition backs an *Image entity* (a
            picture placed in the model as an object): a single textured
            quad, placed through an image-specific wrapper node. Useful for
            consumers that give images special treatment (e.g. billboard
            cut-outs).
        is_group: ``True`` when SketchUp made this definition for a *group*
            rather than a component (what its own API reports as
            ``SUComponentType_Group``). Groups and component instances are
            both :class:`Instance` placements of a :class:`Definition`;
            this is the only thing that tells them apart. It comes from the
            file itself - the definition kind in a 2021+ file, the group
            record that places it in an older one - not from how often the
            definition is placed.
    """

    id: int = 0
    guid: str = ""
    name: str = ""
    vertices: Dict[int, Vertex] = field(default_factory=dict)
    edges: Dict[int, Edge] = field(default_factory=dict)
    faces: Dict[int, Face] = field(default_factory=dict)
    instances: List[Instance] = field(default_factory=list)
    section_planes: List[SectionPlane] = field(default_factory=list)
    texts: List[TextEntity] = field(default_factory=list)
    dimensions: List[Dimension] = field(default_factory=list)
    construction_lines: List[ConstructionLine] = field(default_factory=list)
    construction_points: List[ConstructionPoint] = field(default_factory=list)
    arcs: Dict[int, ArcCurve] = field(default_factory=dict)
    always_faces_camera: bool = False
    shadows_face_sun: bool = False
    is_image: bool = False
    is_group: bool = False
    attribute_dictionaries: Dict[str, Dict[str, Any]] = field(default_factory=dict)


def loose_edge_runs(definition: "Definition") -> List[Tuple[List[int], List[int], bool]]:
    """Groups a definition's LOOSE edges (edges no face uses - SketchUp's
    own way of storing drawing/construction geometry: a light-gauge-steel
    or structural-framing member is routinely drawn this way, not as a
    solid) into ordered polyline runs.

    Grouping comes from the file's own ``Edge#curve`` (:attr:`Edge.curve_id`),
    not a vertex-adjacency guess - the same rule :func:`SkpFile.build_scene`'s
    ``curve_sets`` and :func:`SkpFile.build_instanced_scene`'s
    ``curve_resources`` both use internally, exposed here directly for a
    consumer working with :func:`SkpFile.parse`'s typed model instead of
    either scene-baking API (both of those return triangulated/instanced
    *mesh* data - this returns the raw edge/vertex ids a B-rep-based
    consumer, e.g. a CAD kernel that wants real wire edges rather than a
    render mesh, can build its own geometry from).

    Returns:
        A list of ``(edge_ids, vertex_ids, closed)`` triples per run -
        vertex ids, not coordinates, so the caller resolves them via
        :attr:`Definition.vertices` and applies its own unit/transform
        conventions. An edge with no ``curve_id`` (``edge.curve == nil``
        in the source file) is its own two-point run - joining it to
        neighbouring edges by adjacency alone would invent a curve the
        author never drew.
    """
    from ._curves import _order_curve

    used_edge_ids = set()
    for face in definition.faces.values():
        for loop in face.loops:
            for edge_id, _sign in loop:
                used_edge_ids.add(edge_id)

    edges_by_id = {e.id: (e.v1_id, e.v2_id) for e in definition.edges.values()}
    loose_ids = [eid for eid in edges_by_id if eid not in used_edge_ids]

    groups: Dict[Any, List[int]] = {}
    runs: List[Tuple[List[int], List[int], bool]] = []
    for eid in loose_ids:
        curve_id = definition.edges[eid].curve_id
        if curve_id:
            groups.setdefault(curve_id, []).append(eid)
        else:
            v1, v2 = edges_by_id[eid]
            runs.append(([eid], [v1, v2], False))
    for eids in groups.values():
        runs.extend(_order_curve(edges_by_id, eids))
    return runs


# ── Top-level model ──────────────────────────────────────────────────────


@dataclass
class SkpModel:
    """Complete parsed representation of a SketchUp file.

    Attributes:
        version: SketchUp file-format version number.
        definitions: Mapping of definition index → :class:`Definition`,
            for named component/group definitions only. The implicit
            top-level model is a separate field — see :attr:`root`.
        root: The implicit top-level model definition: its ``instances``
            are the entities placed directly in the model (not inside
            any component/group), and its ``vertices``/``edges``/``faces``
            are geometry drawn directly at the top level. Corresponds to
            TypeScript/.NET/Dart/C++'s ``root``/``Root``.
        layers: List of :class:`Layer` objects found in the file.
        pages: List of :class:`Page` objects — the file's saved scenes
            (from VFF files and from classic pre-2021 files).
        dimensions: Model-level linear dimensions with world-space
            endpoints (VFF files). Legacy files surface text-only
            dimensions per definition instead (``Definition.dimensions``).
        materials: List of :class:`Material` objects found in the file.
        materials_by_id: Mapping of TLV material ID → :class:`Material`,
            the join table for :attr:`Face.material_id`.  Several IDs may
            alias the same :class:`Material` object.
        styles: List of :class:`Style` objects found in the file.
        face_front_color: The colour SketchUp shows on the front side of
            a face with no paint on that side, ``(r, g, b)`` 0-255: the
            model's current rendering options, which is what SketchUp
            displays even when the style was changed and not saved (so it
            can differ from every entry in :attr:`styles`). Read from 2021+
            files and from classic files of SketchUp 4 to 2020; ``None``
            when the file does not give it (SketchUp 3).
        face_back_color: The same for the back side of a face.
        attribute_dictionaries: The model entity's own attribute
            dictionaries (2021+ files; not read from classic files yet). A
            component saved as its own file keeps its Dynamic Components
            attributes here.
        units: The model's unit-system string (e.g. ``"Millimeter"``),
            read from ``meta/meta.dat`` in modern (VFF) files.  ``None``
            for legacy (pre-2021 MFC) files, which carry no equivalent
            container, or when the tag isn't found.
        unread_root_entities: How many top-level entities of a classic
            (pre-2021) file the reader couldn't read. It keeps what it
            read before the first root record it can't parse and drops
            that record and everything after it, so a nonzero count means
            the model lost geometry, components, groups, text or
            dimensions. Phantom entries the root count includes but the
            file never writes are not counted. Always ``0`` for VFF files.

    Note:
        The resolved, world-space scene graph (with baked instance
        transforms and a mesh index for fast export) is a separate,
        heavier computation — see :meth:`SkpFile.build_scene` and the
        :class:`~openskp.scene.Scene` class it returns, rather than a
        field on this class. ``parse()`` stays light on purpose.
    """

    version: str = "unknown"
    definitions: Dict[int, Definition] = field(default_factory=dict)
    root: Definition = field(default_factory=Definition)
    layers: List[Layer] = field(default_factory=list)
    pages: List[Page] = field(default_factory=list)
    dimensions: List[Dimension] = field(default_factory=list)
    materials: List[Material] = field(default_factory=list)
    materials_by_id: Dict[int, Material] = field(default_factory=dict)
    styles: List[Style] = field(default_factory=list)
    face_front_color: Optional[Tuple[int, int, int]] = None
    face_back_color: Optional[Tuple[int, int, int]] = None
    units: Optional[str] = None
    unread_root_entities: int = 0
    attribute_dictionaries: Dict[str, Dict[str, Any]] = field(default_factory=dict)


# ── SkpFile entry-point ──────────────────────────────────────────────────


@contextlib.contextmanager
def _paused_cyclic_gc():
    """Pause automatic cyclic garbage collection for a large allocation burst.

    Parsing builds millions of small vertex, edge, loop and face objects that
    the model keeps alive, so every automatic generation scan walks a growing
    object graph and frees nothing. Reference counting is unaffected; only the
    automatic scans are deferred, and the caller's collector state is restored
    on every exit, including exceptions and KeyboardInterrupt.

    Space_AsBuilt_Track.skp (442 MB, 3.6 M faces) parse: 95.9 s -> 61.8 s on
    CPython 3.13, max RSS 8.24 -> 8.51 GB.
    """
    enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if enabled:
            gc.enable()


class SkpFile:
    """High-level entry point for opening and parsing ``.skp`` files.

    Example::

        skp = SkpFile.open("model.skp")
        model = skp.parse()
        for layer in model.layers:
            print(layer.name)

    Attributes:
        path: Resolved path to the ``.skp`` file.
    """

    def __init__(self, path: pathlib.Path) -> None:
        self.path: pathlib.Path = path
        self._parsed: Optional[Dict[str, Any]] = None

    # ── Construction ──────────────────────────────────────────────────

    @classmethod
    def open(cls, filepath: str | pathlib.Path) -> "SkpFile":
        """Open a SketchUp file for parsing.

        Args:
            filepath: Path to a ``.skp`` file on disk.

        Returns:
            A new :class:`SkpFile` ready for :meth:`parse`.

        Raises:
            FileNotFoundError: If *filepath* does not exist.
            ValueError: If *filepath* does not have a ``.skp`` extension.
        """
        p = pathlib.Path(filepath).resolve()
        if not p.exists():
            raise FileNotFoundError(f"File not found: {p}")
        if p.suffix.lower() != ".skp":
            raise ValueError(f"Expected a .skp file, got: {p.suffix}")
        return cls(p)

    # ── Parsing pipeline ─────────────────────────────────────────────

    def parse(self) -> SkpModel:
        """Run the full parsing pipeline and return a populated model.

        The automatic cyclic garbage collector is paused for the duration of
        the parse (see :func:`_paused_cyclic_gc`) and restored afterwards.
        """
        with _paused_cyclic_gc():
            return self._parse_pipeline()

    def _parse_pipeline(self) -> SkpModel:
        """The parsing pipeline proper, run by :meth:`parse`.

        The pipeline:

        1. Read the file bytes and validate / extract the VFF container.
        2. Parse TLV nodes from ``model.dat``.
        3. Extract geometry (vertices, edges, faces) into definitions.
        4. Parse material XMLs and layer colours.
        5. Resolve metadata — dynamic properties, layer IDs, hierarchy.

        Returns:
            A fully-populated :class:`SkpModel`.

        Raises:
            ValueError: If the file is not a valid SketchUp container.
        """
        from . import _core

        # Use the proven core engine
        parsed = _core.full_parse(str(self.path))

        model = SkpModel()
        model.version = parsed.get("version", "unknown")
        model.units = parsed.get("units")
        model.unread_root_entities = parsed.get("unread_root_entities", 0)
        model.attribute_dictionaries = parsed.get("model_attribute_dictionaries") or {}

        layer_id_to_name = parsed.get("layer_id_to_name", {})

        def _resolve_annotation_layer(raw_layer: Any, raw_layer_id: Any = None) -> str:
            if raw_layer_id is not None and raw_layer_id != 0:
                return layer_id_to_name.get(raw_layer_id, str(raw_layer_id))
            if not raw_layer:
                return ""
            if isinstance(raw_layer, int):
                return layer_id_to_name.get(raw_layer, str(raw_layer)) if raw_layer != 0 else ""
            try:
                lid = int(raw_layer)
            except (ValueError, TypeError):
                return str(raw_layer)
            if lid == 0:
                return ""
            return layer_id_to_name.get(lid, str(raw_layer))

        # Convert defs_dict to Definition dataclasses
        for def_id, d in parsed["defs_dict"].items():
            builder = d["builder"]
            defn = Definition(
                id=def_id if isinstance(def_id, int) else 0,
                guid=d.get("guid", ""),
                name=d.get("name", "") or "",
                always_faces_camera=d.get("always_faces_camera", False),
                shadows_face_sun=d.get("shadows_face_sun", False),
                is_image=d.get("is_image", False),
                is_group=d.get("is_group", False),
                attribute_dictionaries=d.get("attribute_dictionaries") or {},
            )
            # Completed VFF builders can retain dense private tables. Iterate
            # those directly; their public accessors still return cached dicts.
            is_vff = isinstance(builder, _core._GeometryBuilder)
            if is_vff:
                vertices, edges, faces = builder._geometry_tables()
            else:
                vertices, edges, faces = builder.vertices, builder.edges, builder.faces
            # Populate vertices
            for v_id, (x, y, z) in vertices.items():
                defn.vertices[v_id] = Vertex(id=v_id, x=x, y=y, z=z)
            # Populate edges
            flags_map = getattr(builder, "edge_flags", {})
            edge_layers_map = getattr(builder, "edge_layers", None) or {}
            edge_curves_map = getattr(builder, "edge_curves", None) or {}
            edge_pids_map = getattr(builder, "edge_pids", None) or {}
            for e_id, (v1, v2) in edges.items():
                flags = flags_map.get(e_id, 0)
                defn.edges[e_id] = Edge(id=e_id, v1_id=v1 or 0, v2_id=v2 or 0,
                                        soft=bool(flags & 0x08),
                                        smooth=bool(flags & 0x10),
                                        hidden=bool(flags & 0x01),
                                        layer=edge_layers_map.get(e_id),
                                        curve_id=edge_curves_map.get(e_id),
                                        persistent_id=e_id if is_vff else edge_pids_map.get(e_id, 0))
            # Populate faces
            for f_id, f_data in faces.items():
                defn.faces[f_id] = Face(
                    id=f_id,
                    loops=f_data.get("loops", []),
                    normal=f_data.get("normal"),
                    material_id=f_data.get("material_id"),
                    back_material_id=f_data.get("back_material_id"),
                    layer=f_data.get("layer"),
                    uv_transform=f_data.get("uv_transform"),
                    uv_transform_back=f_data.get("uv_transform_back"),
                    uv_projected=f_data.get("uv_projected", False),
                    uv_projected_back=f_data.get("uv_projected_back", False),
                    hidden=f_data.get("hidden", False),
                    persistent_id=f_id if is_vff else f_data.get("persistent_id", 0),
                )
            # Populate instances
            for inst in builder.instances:
                layer_id = inst.get("layer_id")
                defn.instances.append(Instance(
                    name=inst.get("name", "") or "",
                    ref_idx=inst.get("ref_idx"),
                    guid=inst.get("ref_guid", ""),
                    matrix=inst.get("matrix", []),
                    material_id=inst.get("material_id"),
                    hidden=inst.get("hidden", False),
                    layer=layer_id_to_name.get(layer_id, "") if layer_id is not None else "",
                    properties=inst.get("properties", {}),
                    attribute_dictionaries=inst.get("attribute_dictionaries", {}),
                    id=inst.get("id"),
                ))
            # Populate section planes
            for sp in getattr(builder, "section_planes", []):
                defn.section_planes.append(SectionPlane(
                    plane=sp.get("plane", [0.0, 0.0, 1.0, 0.0]),
                    name=sp.get("name", ""),
                    label=sp.get("label", ""),
                    hidden=sp.get("hidden", False),
                ))
            # Populate texts
            for txt in getattr(builder, "texts", []):
                defn.texts.append(TextEntity(
                    text=txt.get("text", ""),
                    hidden=bool(txt.get("hidden", False)),
                    layer=_resolve_annotation_layer(txt.get("layer"), txt.get("layer_id")),
                    point=tuple(txt["p"]) if txt.get("p") is not None else None,
                    label_point=tuple(txt["lp"]) if txt.get("lp") is not None else None,
                    screen_position=(
                        tuple(txt["screen_position"])
                        if txt.get("screen_position") is not None else None
                    ),
                    leader_type=txt.get("leader_type", 2),
                    arrow_type=txt.get("arrow_type", 3),
                    display_leader=txt.get("display_leader", True),
                ))
            # Populate dimensions
            for dim in getattr(builder, "dimensions", []):
                defn.dimensions.append(Dimension(
                    text=dim.get("text", ""),
                    hidden=bool(dim.get("hidden", False)),
                    a=tuple(dim["a"]) if dim.get("a") is not None else None,
                    b=tuple(dim["b"]) if dim.get("b") is not None else None,
                    offset=dim.get("offset", 0.0),
                    plane_x=tuple(dim["plane_x"]) if dim.get("plane_x") is not None else None,
                    normal=tuple(dim["normal"]) if dim.get("normal") is not None else None,
                    offset_vector=(
                        tuple(dim["offset_vector"])
                        if dim.get("offset_vector") is not None else None
                    ),
                    layer=_resolve_annotation_layer(dim.get("layer"), dim.get("layer_id")),
                ))
            # Populate construction lines/points
            for cl in getattr(builder, "construction_lines", []):
                cl_layer_id = cl.get("layer_id")
                defn.construction_lines.append(ConstructionLine(
                    point=cl.get("point", (0.0, 0.0, 0.0)),
                    direction=cl.get("direction", (1.0, 0.0, 0.0)),
                    start=cl.get("start"),
                    end=cl.get("end"),
                    layer=layer_id_to_name.get(cl_layer_id, "") if cl_layer_id is not None else "",
                    hidden=cl.get("hidden", False),
                ))
            for curve_id, frame in (getattr(builder, "arc_curves", None) or {}).items():
                defn.arcs[curve_id] = ArcCurve(
                    center=tuple(frame["center"]), normal=tuple(frame["normal"]),
                    x_axis=tuple(frame["x_axis"]), y_axis=tuple(frame["y_axis"]),
                    start_angle=frame["start_angle"], end_angle=frame["end_angle"])
            for cp in getattr(builder, "construction_points", []):
                cp_layer_id = cp.get("layer_id")
                defn.construction_points.append(ConstructionPoint(
                    position=cp.get("position", (0.0, 0.0, 0.0)),
                    layer=layer_id_to_name.get(cp_layer_id, "") if cp_layer_id is not None else "",
                    hidden=cp.get("hidden", False),
                ))
            if def_id == "ROOT":
                model.root = defn
            else:
                model.definitions[def_id] = defn

        # Convert layers
        layer_hidden = parsed.get("layer_hidden", {})
        for name, (r, g, b) in parsed["layer_colors"].items():
            model.layers.append(
                Layer(name=name, color_r=r, color_g=g, color_b=b, hidden=layer_hidden.get(name, False))
            )

        # Convert pages (saved scenes) — hidden layer ids resolve to names;
        # unknown ids (stale refs) are dropped.
        model.pages = _convert_pages(parsed)

        # Convert model-level linear dimensions (VFF; world space).
        for dm in parsed.get("dimensions") or []:
            model.dimensions.append(Dimension(
                a=tuple(dm["a"]), b=tuple(dm["b"]),
                offset=dm.get("offset", 0.0),
                plane_x=dm.get("plane_x"), normal=dm.get("normal"),
                offset_vector=dm.get("offset_vector"),
                text=dm.get("text", ""),
                hidden=bool(dm.get("hidden", False)),
                layer=_resolve_annotation_layer(dm.get("layer"), dm.get("layer_id")),
            ))

        # Convert materials
        mat_for_data: Dict[int, Material] = {}   # id(raw dict) -> Material
        for mat_data in parsed["materials"].values():
            c = mat_data.get("color", {})
            tex_data = mat_data.get("texture")
            texture = None
            if tex_data is not None:
                texture = Texture(
                    filename=tex_data.get("filename", ""),
                    width=tex_data.get("x_scale", 0.0),
                    height=tex_data.get("y_scale", 0.0),
                    data=tex_data.get("data"),
                )
            mat = Material(
                name=mat_data.get("name", ""),
                color=(c.get("r", 128), c.get("g", 128), c.get("b", 128), c.get("a", 255)),
                transparency=mat_data.get("transparency", 0.5),
                texture=texture,
                colorized=mat_data.get("colorized", False),
                colorize_type=mat_data.get("colorize_type", 0),
            )
            model.materials.append(mat)
            mat_for_data[id(mat_data)] = mat

        # Join the TLV material IDs (what Face.material_id references) onto
        # the parsed materials, so callers can resolve face -> material.
        # Same name-then-folder resolution the internal exporter uses.
        materials_by_folder = parsed.get("materials_by_folder", {})
        for m_id, m_name in parsed.get("material_id_to_name", {}).items():
            mat_data = (parsed["materials"].get(m_name)
                        or materials_by_folder.get(m_name))
            mat = mat_for_data.get(id(mat_data)) if mat_data is not None else None
            if mat is None:
                continue
            if mat.id is None:
                mat.id = m_id
            model.materials_by_id[m_id] = mat

        # Convert styles
        for st in parsed.get("styles", []):
            model.styles.append(Style(
                name=st.get("name", ""),
                front_color=st.get("front_color"),
                back_color=st.get("back_color"),
            ))

        face_colors = parsed.get("face_colors")
        if face_colors:
            model.face_front_color, model.face_back_color = face_colors

        # Store raw parsed data for export use
        self._parsed = parsed

        return model

    def build_scene(
        self,
        name_override_keys: Sequence[str] = ("name", "label", "code"),
        include_curve_sets: bool = True,
        original_textures: bool = False,
        unpainted_faces: str = "style",
    ) -> "scene.Scene":
        """Bake every instance actually placed in the model into
        world-space, triangulated mesh data - SketchUp's component/group
        nesting fully resolved and flattened, ready for a GLB export or any
        other renderer.

        This is a separate, opt-in step from :meth:`parse`: it re-runs the
        underlying parse independently rather than reusing a prior
        :meth:`parse` call's data, so calling only :meth:`parse` never pays
        for the heavier instancing + triangulation work here. For a file
        that reuses a handful of definitions across many thousands of
        instances, the baked output can be far larger than the file's raw
        geometry - that's the reason this isn't part of :meth:`parse`.

        Args:
            name_override_keys: Attribute-dictionary key names (checked in
                order, first match wins) that identify an instance's real,
                plugin-assigned name. This is deliberately generic, not
                tied to any one SketchUp extension: it's tried across
                *every* non-boilerplate attribute dictionary an instance
                carries, whichever plugin (FrameBuilder, TechSteel, or any
                other tool that attaches its own dictionary) wrote it. The
                default ``("name", "label", "code")`` covers the common
                convention - pass your own tuple if your plugin instead
                uses a key like ``"mark"`` or ``"partNumber"``.
            include_curve_sets: Bake loose edges (edges no face uses) into
                :attr:`Scene.curve_sets` as polyline runs. Pass ``False``
                to skip them - a pure cost lever (+5.7% of the IFC on a
                model that has both solids and curves), not a correctness
                one. On a curve-only model it is not a lever at all: the
                curve sets are the entire content.
            original_textures: Keep a texture that is not PNG or JPEG
                (SketchUp stores TIFF, TGA and GIF textures as they were
                imported) in its own format, with its real
                :attr:`SceneTexture.mime_type` (``image/tiff``, ...),
                instead of leaving it out with a warning. Off by default:
                a consumer that assumes every texture is PNG or JPEG (a
                glTF writer of its own) would break on the others. The
                GLB exporters need no change: they decode through Pillow.
            unpainted_faces: Colour of a face side with no paint on it or on
                anything that contains it. ``"style"`` (the default) is what
                SketchUp shows: the model's front face colour on the front
                and its back face colour on the back
                (:attr:`SkpModel.face_front_color` / ``face_back_color``).
                Where the two differ, as they do in SketchUp's own styles,
                such a face is emitted as two single-sided sets and the back
                one is marked ``unpainted_back``. ``"tag"`` gives both sides
                the colour of the face's tag, in one double-sided set, as
                openskp did up to 1.6.0. A file whose face colours can't be
                read (SketchUp 3) always uses the tag colour.

        Returns:
            A populated :class:`openskp.scene.Scene`.
        """
        from . import _core
        from . import scene as _scene

        if unpainted_faces not in ("style", "tag"):
            raise ValueError(f'unpainted_faces must be "style" or "tag", not {unpainted_faces!r}')
        parsed = _core.full_parse(str(self.path))
        try:
            return _scene.build_scene(
                parsed,
                name_override_keys=name_override_keys,
                include_curve_sets=include_curve_sets,
                original_textures=original_textures,
                unpainted_faces=unpainted_faces,
            )
        except (SkpParseError, RecursionError, MemoryError):
            raise
        except Exception as e:
            raise SkpParseError(
                f"Failed while building scene: {e}", stage="build_scene"
            ) from e

    def build_instanced_scene(
        self,
        name_override_keys: Sequence[str] = ("name", "label", "code"),
        original_textures: bool = False,
        unpainted_faces: str = "style",
        include_curve_sets: bool = True,
    ) -> "instanced_scene.InstancedScene":
        """Build the placed scene graph with SketchUp's component/group
        INSTANCING PRESERVED, instead of baked into world-space vertex data.

        Use this instead of :meth:`build_scene` when the model reuses
        components: :meth:`build_scene` bakes each placement into its own
        world-space vertex buffers, so its output grows with `definition
        geometry x placement count`, while this grows with `unique geometry
        + instance transforms`. A component placed 1,000 times costs one
        copy of its geometry here.

        Same separate, opt-in re-parse as :meth:`build_scene` - see that
        method's docstring.

        Args:
            name_override_keys: See :meth:`build_scene` - same meaning,
                same default, same generic (not tied to any one plugin's
                own dictionary name) lookup.
            original_textures: Keep a texture that is not PNG or JPEG in
                its own format, with its real MIME type; see
                :meth:`build_scene`.
            unpainted_faces: ``"style"`` (the default) or ``"tag"``; see
                :meth:`build_scene`.

        Returns:
            A populated :class:`openskp.instanced_scene.InstancedScene`.
        """
        from . import _core
        from . import instanced_scene

        if unpainted_faces not in ("style", "tag"):
            raise ValueError(f'unpainted_faces must be "style" or "tag", not {unpainted_faces!r}')
        parsed = _core.full_parse(str(self.path))
        try:
            return instanced_scene.build_instanced_scene(
                parsed,
                name_override_keys=name_override_keys,
                original_textures=original_textures,
                unpainted_faces=unpainted_faces,
                include_curve_sets=include_curve_sets,
            )
        except (SkpParseError, RecursionError, MemoryError):
            raise
        except Exception as e:
            raise SkpParseError(
                f"Failed while building scene: {e}", stage="build_scene"
            ) from e

