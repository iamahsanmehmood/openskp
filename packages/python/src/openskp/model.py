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

import pathlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from . import scene


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
    """

    id: int
    v1_id: int
    v2_id: int
    soft: bool = False
    smooth: bool = False
    hidden: bool = False


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
               ``n``: ``xr = X``, ``yr = ±Y`` by the sign of ``n``·Z).
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


# ── Layers & Materials ────────────────────────────────────────────────────


@dataclass
class Layer:
    """A SketchUp layer (tag).

    Attributes:
        name: Human-readable layer name.
        color_r: Red channel (0–255).
        color_g: Green channel (0–255).
        color_b: Blue channel (0–255).
        hidden: Whether the layer's visibility is switched off. Only
            populated for legacy (pre-2021 MFC) files, where the byte is
            read directly from the layer record — modern (VFF) files
            derive layers from ``Layer_<name>``-prefixed materials, which
            carry no visibility data, so this is always ``False`` there.
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
        matrix: 4×4 transformation matrix stored as a flat 16-element list
            in **column-major** order.
        layer: This instance's own explicit layer override, or ``""``
            when it has none. An instance without an explicit override
            inherits its *placement's* layer, which can only be resolved
            once the scene graph is flattened - see
            :meth:`SkpFile.build_scene`'s ``InstanceNode.layer`` for that
            resolved value.
        properties: Arbitrary key/value dynamic attributes attached
            directly to this instance (SketchUp's Dynamic Components).
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
    material_id: Optional[int] = None
    hidden: bool = False


@dataclass
class SectionPlane:
    plane: List[float] = field(default_factory=lambda: [0.0, 0.0, 1.0, 0.0])
    name: str = ""
    label: str = ""
    hidden: bool = False


@dataclass
class TextEntity:
    """A leader-text label (SketchUp's Text tool).

    Attributes:
        text: The displayed string.
        hidden: Whether the label is hidden.
        point: Anchor point ``(x, y, z)`` in inches (world space for
            model-root texts), or ``None`` when the record anchors to
            geometry instead of a free point (not resolved yet).
    """

    text: str = ""
    hidden: bool = False
    point: Optional[Tuple[float, float, float]] = None


@dataclass
class Dimension:
    """A linear dimension (SketchUp's Dimension tool).

    The legacy (pre-2021) reader recovers only ``text``/``hidden``. The VFF
    reader (2021+) recovers the full geometry — see :attr:`SkpModel.dimensions`
    for the model-level, world-space list.

    Attributes:
        text: The displayed text. Empty when the dimension shows its
            auto-computed measured value (the caller formats ``|b − a|``).
        hidden: Whether the dimension is hidden.
        a: First measured point ``(x, y, z)`` in inches (world space), or
            ``None`` when only the text was recovered.
        b: Second measured point.
        offset: Offset distance (inches) — how far the dimension line sits
            from the ``a``–``b`` segment, along the in-plane perpendicular.
        plane_x: The dimension plane's x-axis, or ``None``.
        normal: The dimension plane's normal, or ``None``.
    """

    text: str = ""
    hidden: bool = False
    a: Optional[Tuple[float, float, float]] = None
    b: Optional[Tuple[float, float, float]] = None
    offset: float = 0.0
    plane_x: Optional[Tuple[float, float, float]] = None
    normal: Optional[Tuple[float, float, float]] = None


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
    """

    name: str = ""
    eye: Optional[Tuple[float, float, float]] = None
    target: Optional[Tuple[float, float, float]] = None
    up: Optional[Tuple[float, float, float]] = None
    fov: float = 35.0
    parallel: bool = False
    ortho_height: float = 0.0
    hidden_layers: List[str] = field(default_factory=list)


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
        always_faces_camera: SketchUp's "always face camera" component
            behavior (2D people / tree cut-outs that rotate to face the
            viewer). Consumers typically render such instances as
            billboards.
        is_image: ``True`` when this definition backs an *Image entity* (a
            picture placed in the model as an object): a single textured
            quad, placed through an image-specific wrapper node. Useful for
            consumers that give images special treatment (e.g. billboard
            cut-outs).
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
    always_faces_camera: bool = False
    shadows_face_sun: bool = False
    is_image: bool = False


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
            (VFF files; classic pre-2021 files import with none).
        dimensions: Model-level linear dimensions with world-space
            endpoints (VFF files). Legacy files surface text-only
            dimensions per definition instead (``Definition.dimensions``).
        materials: List of :class:`Material` objects found in the file.
        materials_by_id: Mapping of TLV material ID → :class:`Material`,
            the join table for :attr:`Face.material_id`.  Several IDs may
            alias the same :class:`Material` object.
        styles: List of :class:`Style` objects found in the file.
        units: The model's unit-system string (e.g. ``"Millimeter"``),
            read from ``meta/meta.dat`` in modern (VFF) files.  ``None``
            for legacy (pre-2021 MFC) files, which carry no equivalent
            container, or when the tag isn't found.

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
    units: Optional[str] = None


# ── SkpFile entry-point ──────────────────────────────────────────────────


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

        layer_id_to_name = parsed.get("layer_id_to_name", {})

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
            )
            # Populate vertices
            for v_id, (x, y, z) in builder.vertices.items():
                defn.vertices[v_id] = Vertex(id=v_id, x=x, y=y, z=z)
            # Populate edges
            flags_map = getattr(builder, "edge_flags", {})
            for e_id, (v1, v2) in builder.edges.items():
                flags = flags_map.get(e_id, 0)
                defn.edges[e_id] = Edge(id=e_id, v1_id=v1 or 0, v2_id=v2 or 0,
                                        soft=bool(flags & 0x08),
                                        smooth=bool(flags & 0x10),
                                        hidden=bool(flags & 0x01))
            # Populate faces
            for f_id, f_data in builder.faces.items():
                defn.faces[f_id] = Face(
                    id=f_id,
                    loops=f_data.get("loops", []),
                    normal=f_data.get("normal"),
                    material_id=f_data.get("material_id"),
                    back_material_id=f_data.get("back_material_id"),
                    uv_transform=f_data.get("uv_transform"),
                    uv_transform_back=f_data.get("uv_transform_back"),
                    uv_projected=f_data.get("uv_projected", False),
                    uv_projected_back=f_data.get("uv_projected_back", False),
                    hidden=f_data.get("hidden", False),
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
                    hidden=txt.get("hidden", False),
                    point=tuple(txt["p"]) if txt.get("p") else None,
                ))
            # Populate dimensions
            for dim in getattr(builder, "dimensions", []):
                defn.dimensions.append(Dimension(
                    text=dim.get("text", ""),
                    hidden=dim.get("hidden", False),
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
        for pg in parsed.get("pages") or []:
            model.pages.append(Page(
                name=pg.get("name", ""),
                eye=pg.get("eye"),
                target=pg.get("target"),
                up=pg.get("up"),
                fov=pg.get("fov", 35.0),
                parallel=pg.get("parallel", False),
                ortho_height=pg.get("ortho_height", 0.0),
                hidden_layers=[layer_id_to_name[i]
                               for i in pg.get("hidden_layer_ids", [])
                               if i in layer_id_to_name],
            ))

        # Convert model-level linear dimensions (VFF; world space).
        for dm in parsed.get("dimensions") or []:
            model.dimensions.append(Dimension(
                a=tuple(dm["a"]), b=tuple(dm["b"]),
                offset=dm.get("offset", 0.0),
                plane_x=dm.get("plane_x"), normal=dm.get("normal"),
                text=dm.get("text", ""),
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

        # Store raw parsed data for export use
        self._parsed = parsed

        return model

    def build_scene(self) -> "scene.Scene":
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

        Returns:
            A populated :class:`openskp.scene.Scene`.
        """
        from . import _core
        from . import scene as _scene

        parsed = _core.full_parse(str(self.path))
        return _scene.build_scene(parsed)

