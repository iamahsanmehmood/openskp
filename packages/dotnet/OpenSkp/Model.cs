using System;
using System.Collections.Generic;

namespace OpenSkp
{
    /// <summary>A 3D vertex in definition-local space (inches, SketchUp Z-up).</summary>
    public sealed class Vertex
    {
        /// <summary>Definition-scoped vertex identifier referenced by <see cref="Edge.V1Id"/> and <see cref="Edge.V2Id"/>.</summary>
        public long Id { get; set; }
        /// <summary>X coordinate in inches.</summary>
        public double X { get; set; }
        /// <summary>Y coordinate in inches.</summary>
        public double Y { get; set; }
        /// <summary>Z coordinate in inches.</summary>
        public double Z { get; set; }
    }

    /// <summary>A straight line segment connecting two vertices in a <see cref="Definition"/>.</summary>
    public sealed class Edge
    {
        /// <summary>Definition-scoped edge identifier referenced by <see cref="Face.Loops"/>.</summary>
        public long Id { get; set; }
        /// <summary>Start vertex ID in <see cref="Definition.Vertices"/>.</summary>
        public long V1Id { get; set; }
        /// <summary>End vertex ID in <see cref="Definition.Vertices"/>.</summary>
        public long V2Id { get; set; }
        /// <summary>SketchUp's Soft flag (suppresses wireframe display between adjacent faces).</summary>
        public bool Soft { get; set; }
        /// <summary>SketchUp's Smooth flag (interpolates shading normals across the edge).</summary>
        public bool Smooth { get; set; }
        /// <summary>Whether the edge itself is explicitly hidden.</summary>
        public bool Hidden { get; set; }
        /// <summary>SketchUp's own <c>Edge#curve</c> grouping: the id of the Curve this edge belongs
        /// to, or null when it belongs to none. Edges sharing a CurveId were drawn as one continuous
        /// multi-segment line or arc; if the curve is an arc, <see cref="Definition.Arcs"/> has it
        /// under the same id.</summary>
        public long? CurveId { get; set; }
        /// <summary>Document-wide persistent entity ID (used by <see cref="Page.HiddenEntityIds"/>).</summary>
        public long PersistentId { get; set; }
        /// <summary>Name of the layer (tag) this edge is assigned to, or "" when the edge carries no
        /// explicit layer reference.</summary>
        public string Layer { get; set; } = "";
    }

    /// <summary>The analytic arc behind a curve: SketchUp's own <c>ArcCurve</c>. A curve SketchUp drew
    /// as an arc or a circle is a run of straight edges (<see cref="Edge.CurveId"/>) and also this
    /// exact description. The arc is the set of points <c>Center + cos(t) * XAxis + sin(t) * YAxis</c>
    /// for t from <see cref="StartAngle"/> to <see cref="EndAngle"/>. For a circle XAxis and YAxis
    /// are perpendicular and both of length <see cref="Radius"/>; an arc scaled non-uniformly keeps
    /// its arc identity and stores an affine image of a circle, where they are not (then Radius is
    /// the length of XAxis only). Arcs and circles that bound a face are arcs too. Lengths are in
    /// the model's units (inches), in the definition's local space, like <see cref="Vertex.X"/>.</summary>
    public sealed class ArcCurve
    {
        /// <summary>Arc centre in definition-local inches.</summary>
        public (double X, double Y, double Z) Center { get; set; }
        /// <summary>Unit normal of the arc's plane.</summary>
        public (double X, double Y, double Z) Normal { get; set; }
        /// <summary>Vector from the centre to the arc's start (length = radius).</summary>
        public (double X, double Y, double Z) XAxis { get; set; }
        /// <summary><c>Normal x XAxis</c>.</summary>
        public (double X, double Y, double Z) YAxis { get; set; }
        /// <summary>Start parameter in radians.</summary>
        public double StartAngle { get; set; }
        /// <summary>End parameter in radians. A full circle has EndAngle == StartAngle in a classic
        /// (pre-2021) file and StartAngle + 2 pi in a 2021+ file; <see cref="IsFullCircle"/> covers
        /// both.</summary>
        public double EndAngle { get; set; }
        /// <summary>Length of <see cref="XAxis"/> in inches.</summary>
        public double Radius => Math.Sqrt(XAxis.X * XAxis.X + XAxis.Y * XAxis.Y + XAxis.Z * XAxis.Z);
        /// <summary><c>true</c> when the arc sweeps a complete 360-degree circle.</summary>
        public bool IsFullCircle
        {
            get
            {
                double sweep = (EndAngle - StartAngle) % (2.0 * Math.PI);
                if (sweep < 0) sweep += 2.0 * Math.PI;
                return Math.Min(sweep, 2.0 * Math.PI - sweep) < 1e-9;
            }
        }
    }

    /// <summary>A planar polygonal face bounded by an outer edge loop and zero or more inner hole loops.</summary>
    public sealed class Face
    {
        /// <summary>Definition-scoped face identifier.</summary>
        public long Id { get; set; }

        /// <summary>Ordered list of loops; each loop is a list of
        /// (edgeId, orientation) pairs where orientation is 1 for forward or
        /// -1 for reversed.</summary>
        public List<List<(long EdgeId, long Orientation)>> Loops { get; set; } = new List<List<(long, long)>>();

        /// <summary>Face plane normal in definition-local space, or <c>null</c> when omitted by the record.</summary>
        public (double Nx, double Ny, double Nz)? Normal { get; set; }
        /// <summary>Front-side material ID into <see cref="SkpModel.MaterialsById"/>, or <c>null</c> when unpainted.</summary>
        public long? MaterialId { get; set; }
        /// <summary>Back-side material ID into <see cref="SkpModel.MaterialsById"/>, or <c>null</c> when unpainted.</summary>
        public long? BackMaterialId { get; set; }

        /// <summary>Per-face texture mapping for a positioned/photo-fitted
        /// texture (SketchUp's pins), or null when the default projection
        /// applies. A 9-element row-major 3x3 matrix mapping texture space to
        /// the face plane.</summary>
        public double[]? UvTransform { get; set; }
        /// <summary>Back-side positioned texture transform (9-element row-major 3x3), or <c>null</c>.</summary>
        public double[]? UvTransformBack { get; set; }

        /// <summary>The texture is PROJECTED (e.g. the Add Location terrain
        /// drape): its UVs run in the projection plane's frame, not the face
        /// frame.</summary>
        public bool UvProjected { get; set; }

        /// <summary>Same for the face's back side.</summary>
        public bool UvProjectedBack { get; set; }

        /// <summary>Whether the face is hidden (SketchUp's "Hide" on this
        /// specific face, not a layer/tag visibility toggle).</summary>
        public bool Hidden { get; set; }
        /// <summary>Document-wide persistent entity ID (used by <see cref="Page.HiddenEntityIds"/>).</summary>
        public long PersistentId { get; set; }

        /// <summary>Name of the layer (tag) this face is assigned to, or "" when the face carries no
        /// explicit layer reference (default layer).</summary>
        public string Layer { get; set; } = "";
    }

    /// <summary>A SketchUp layer (tag), carrying a display colour and default visibility.</summary>
    public sealed class Layer
    {
        /// <summary>Layer (tag) name, such as <c>"Layer0"</c>.</summary>
        public string Name { get; set; } = "";
        /// <summary>Red channel (0-255) of the layer's display colour.</summary>
        public int ColorR { get; set; } = 200;
        /// <summary>Green channel (0-255) of the layer's display colour.</summary>
        public int ColorG { get; set; } = 200;
        /// <summary>Blue channel (0-255) of the layer's display colour.</summary>
        public int ColorB { get; set; } = 200;

        /// <summary>Whether the layer's visibility is switched off.</summary>
        public bool Hidden { get; set; }

        /// <summary>Name of the tag's line style (such as <c>"Dash"</c> or <c>"Dot"</c>), or <c>""</c> when the tag has no line style.</summary>
        public string LineStyle { get; set; } = "";
    }

    /// <summary>A SketchUp visual style bundled inside the model.</summary>
    public sealed class Style
    {
        /// <summary>Style name.</summary>
        public string Name { get; set; } = "";
        /// <summary>Default front-face RGB colour (0-255) stored on this style, or <c>null</c>.</summary>
        public (int R, int G, int B)? FrontColor { get; set; }
        /// <summary>Default back-face RGB colour (0-255) stored on this style, or <c>null</c>.</summary>
        public (int R, int G, int B)? BackColor { get; set; }
    }

    /// <summary>An image texture embedded in a SketchUp <see cref="Material"/>.</summary>
    public sealed class Texture
    {
        /// <summary>Authoring texture filename or relative path recorded in the material.</summary>
        public string Filename { get; set; } = "";
        /// <summary>Tile width in inches on unpositioned faces.</summary>
        public double Width { get; set; }
        /// <summary>Tile height in inches on unpositioned faces.</summary>
        public double Height { get; set; }
        /// <summary>Raw encoded image bytes (typically PNG or JPEG), or <c>null</c> if absent.</summary>
        public byte[]? Data { get; set; }

        /// <summary>Writes <see cref="Data"/> to <paramref name="filepath"/> and returns its <see cref="System.IO.FileInfo"/>.</summary>
        public System.IO.FileInfo Save(string filepath)
        {
            if (Data == null)
            {
                throw new System.InvalidOperationException($"Texture '{Filename}' has no image data");
            }
            System.IO.File.WriteAllBytes(filepath, Data);
            return new System.IO.FileInfo(filepath);
        }
    }

    /// <summary>A SketchUp surface material (solid colour or textured, with optional colour tinting and opacity).</summary>
    public sealed class Material
    {
        /// <summary>Material name.</summary>
        public string Name { get; set; } = "";
        /// <summary>sRGB colour <c>(R, G, B, A)</c> with channels in 0-255.</summary>
        public (int R, int G, int B, int A) Color { get; set; } = (200, 200, 200, 255);
        /// <summary>Surface opacity in <c>[0.0, 1.0]</c>, where <c>1.0</c> is completely opaque.</summary>
        public double Transparency { get; set; } = 1.0;

        /// <summary>Numeric material ID from the TLV stream - the value that
        /// Face.MaterialId references. Null when the file assigns the
        /// material no ID.</summary>
        public long? Id { get; set; }

        /// <summary>Embedded texture image and tile dimensions, or <c>null</c> for a solid-colour material.</summary>
        public Texture? Texture { get; set; }
        /// <summary>Whether SketchUp colour-tints <see cref="Texture"/> with <see cref="Color"/>.</summary>
        public bool Colorized { get; set; }
        /// <summary>Colour-tinting mode (<c>0</c> = shift, <c>1</c> = tint) when <see cref="Colorized"/> is <c>true</c>.</summary>
        public int ColorizeType { get; set; }
    }

    /// <summary>A placement of a <see cref="Definition"/> (component instance or group) inside a parent definition or model root.</summary>
    public sealed class Instance
    {
        /// <summary>Entity ID (persistent ID on classic placements), or <c>null</c>.</summary>
        public long? Id { get; set; }
        /// <summary>Instance name authored in SketchUp's Entity Info, or <c>""</c> when unnamed.</summary>
        public string Name { get; set; } = "";
        /// <summary>Numeric ID of the referenced <see cref="Definition"/> in <see cref="SkpModel.Definitions"/>.</summary>
        public long? RefIdx { get; set; }
        /// <summary>Instance GUID (VFF 2021+ files) or definition GUID reference, or <c>""</c>.</summary>
        public string Guid { get; set; } = "";

        /// <summary>4x4 transform stored as a flat 16-element list in
        /// column-major order (empty when the entity carried none).</summary>
        public List<double> Matrix { get; set; } = new List<double>();

        /// <summary>This instance's own explicit layer override, or ""
        /// when it has none. An instance without an explicit override
        /// inherits its *placement's* layer, which can only be resolved
        /// once the scene graph is flattened - see
        /// SkpFile.BuildScene()'s InstanceNode.Layer for that resolved
        /// value.</summary>
        public string Layer { get; set; } = "";

        /// <summary>Arbitrary key/value dynamic attributes attached
        /// directly to this instance (SketchUp's Dynamic Components).</summary>
        public Dictionary<string, string> Properties { get; set; } = new Dictionary<string, string>();
        /// <summary>Material ID painted onto this instance in <see cref="SkpModel.MaterialsById"/>, or <c>null</c>.</summary>
        public long? MaterialId { get; set; }

        /// <summary>Whether the instance itself is hidden (SketchUp's "Hide"
        /// on this specific component/group placement, not a layer/tag
        /// visibility toggle).</summary>
        public bool Hidden { get; set; }
    }

    /// <summary>A SketchUp section plane (<c>ax + by + cz + d = 0</c> in owner-local inches).</summary>
    public sealed class SectionPlane
    {
        /// <summary>Four-element plane equation <c>[a, b, c, d]</c> with unit normal <c>(a, b, c)</c> and signed distance <c>d</c> in inches.</summary>
        public double[] Plane { get; set; } = new double[] { 0.0, 0.0, 1.0, 0.0 };
        /// <summary>Section plane name.</summary>
        public string Name { get; set; } = "";
        /// <summary>Short section symbol shown on the section plane's corner markers.</summary>
        public string Label { get; set; } = "";
        /// <summary>Whether the section plane entity is hidden.</summary>
        public bool Hidden { get; set; }
    }

    /// <summary>A leader or screen text annotation in a <see cref="Definition"/> or model root.</summary>
    public sealed class TextEntity
    {
        /// <summary>Displayed text string.</summary>
        public string Text { get; set; } = "";
        /// <summary>Whether the text entity is hidden.</summary>
        public bool Hidden { get; set; }
        /// <summary>Explicit tag name, or empty string for the default tag (Layer0).</summary>
        public string Layer { get; set; } = "";
        /// <summary>Anchor position in owner-local inches; null when unresolved or screen-pinned.</summary>
        public (double X, double Y, double Z)? Point { get; set; }
        /// <summary>Label tip position in owner-local inches, or <c>null</c> for screen-pinned text.</summary>
        public (double X, double Y, double Z)? LabelPoint { get; set; }
        /// <summary>Normalized screen fractions (used when LeaderType is zero).</summary>
        public (double X, double Y)? ScreenPosition { get; set; }
        /// <summary>SketchUp leader mode (<c>0</c> = none/screen, <c>1</c> = view-based, <c>2</c> = pushpin).</summary>
        public int LeaderType { get; set; } = 2;
        /// <summary>SketchUp leader arrow style (<c>0</c> = none, <c>1</c> = slash, <c>2</c> = dot, <c>3</c> = closed, <c>4</c> = open).</summary>
        public int ArrowType { get; set; } = 3;
        /// <summary>Whether the leader line is drawn.</summary>
        public bool DisplayLeader { get; set; } = true;
    }

    /// <summary>A linear dimension (SketchUp's Dimension tool).
    ///
    /// Both VFF (2021+) and legacy (pre-2021) readers expose owner-local geometry
    /// and drawing properties on Root.Dimensions and Definition.Dimensions;
    /// <see cref="SkpModel.Dimensions"/> also retains the world-space VFF
    /// compatibility list.</summary>
    public sealed class Dimension
    {
        /// <summary>The displayed text. Empty when the dimension shows its
        /// auto-computed measured value (the caller formats |B - A|).</summary>
        public string Text { get; set; } = "";
        /// <summary>Whether the dimension entity is hidden.</summary>
        public bool Hidden { get; set; }

        /// <summary>First measured point (x, y, z) in inches, or null when unresolved.</summary>
        public (double X, double Y, double Z)? A { get; set; }

        /// <summary>Second measured point.</summary>
        public (double X, double Y, double Z)? B { get; set; }

        /// <summary>Offset distance (inches) - how far the dimension line
        /// sits from the A-B segment, along the in-plane perpendicular.</summary>
        public double Offset { get; set; }

        /// <summary>Historical name: for a VFF dimension this is tag 5BCF, which is the plane NORMAL (SketchUp's SUDimensionLinearGetNormal equals it); for a legacy dimension this is the plane's X axis, or null.</summary>
        public (double X, double Y, double Z)? PlaneX { get; set; }

        /// <summary>Historical name: for a VFF dimension this is tag 5BD0, the plane's X axis; for a legacy dimension this is the plane normal, or null.</summary>
        public (double X, double Y, double Z)? Normal { get; set; }

        /// <summary>Explicit offset vector (inches) for legacy or VFF dimensions constrained to a plane axis, preserving the side of the line independently of endpoint ordering, or null.</summary>
        public (double X, double Y, double Z)? OffsetVector { get; set; }

        /// <summary>Explicit tag name, or empty string for the default tag (Layer0).</summary>
        public string Layer { get; set; } = "";
    }

    /// <summary>A construction/guide line (SketchUp's Construction Line
    /// tool). Supported in both legacy and VFF (2021+) models.
    ///
    /// Stored internally (and here, unchanged) as a point + normalized
    /// direction + two signed distance parameters along that direction
    /// marking where the visible segment starts/ends - the same shape
    /// Sketchup::ConstructionLine's own start/end/direction properties
    /// expose. A parameter magnitude of 1e30 means unbounded in that
    /// direction (SketchUp draws this as an infinite guide line through
    /// Point) - Start/End come back null in that case, matching the real
    /// API returning nil.</summary>
    public sealed class ConstructionLine
    {
        /// <summary>A point on the line, in inches (world space) - matches
        /// the bounded case's own Start, or the anchor point given for an
        /// infinite line.</summary>
        public (double X, double Y, double Z) Point { get; set; }

        /// <summary>The line's normalized direction vector.</summary>
        public (double X, double Y, double Z) Direction { get; set; } = (1.0, 0.0, 0.0);

        /// <summary>The bounded segment's start point, or null if
        /// unbounded in this direction.</summary>
        public (double X, double Y, double Z)? Start { get; set; }

        /// <summary>The bounded segment's end point, or null if
        /// unbounded.</summary>
        public (double X, double Y, double Z)? End { get; set; }

        /// <summary>Name of the layer (tag) the guide is assigned to, or ""
        /// for the default layer (Layer0).</summary>
        public string Layer { get; set; } = "";

        /// <summary>Whether the guide itself is hidden.</summary>
        public bool Hidden { get; set; }

        internal long? LayerId;
    }

    /// <summary>A construction/guide point (SketchUp's Construction Point
    /// tool). Read from both legacy (pre-2021) and VFF (2021+) files.</summary>
    public sealed class ConstructionPoint
    {
        /// <summary>The point's position, in inches (world space).</summary>
        public (double X, double Y, double Z) Position { get; set; }

        /// <summary>Name of the layer (tag) the guide is assigned to, or ""
        /// for the default layer (Layer0).</summary>
        public string Layer { get; set; } = "";

        /// <summary>Whether the guide itself is hidden.</summary>
        public bool Hidden { get; set; }

        internal long? LayerId;
    }

    /// <summary>A saved scene (SketchUp's "Scenes" tabs; "pages" in the SDK).</summary>
    public sealed class Page : IEquatable<Page>
    {
        /// <summary>Scene name as shown on its tab.</summary>
        public string Name { get; set; } = "";

        /// <summary>Camera position (x, y, z) in inches, or null.</summary>
        public (double X, double Y, double Z)? Eye { get; set; }

        /// <summary>Point the camera looks at, in inches.</summary>
        public (double X, double Y, double Z)? Target { get; set; }

        /// <summary>Camera up vector.</summary>
        public (double X, double Y, double Z)? Up { get; set; }

        /// <summary>Field of view in degrees (SketchUp default 35).</summary>
        public double Fov { get; set; } = 35.0;

        /// <summary>True when the scene uses parallel (orthographic)
        /// projection; Fov still holds the stored perspective angle.</summary>
        public bool Parallel { get; set; }

        /// <summary>Visible height in inches when Parallel.</summary>
        public double OrthoHeight { get; set; }

        /// <summary>Names of the layers this scene hides.</summary>
        public List<string> HiddenLayers { get; set; } = new List<string>();

        /// <summary>Bit flag (<c>0x001</c>): scene saves camera location.</summary>
        public const uint PageUseCamera = 0x001;
        /// <summary>Bit flag (<c>0x002</c>): scene saves rendering options (style and fog).</summary>
        public const uint PageUseRenderingOptions = 0x002;
        /// <summary>Bit flag (<c>0x004</c>): scene saves shadow settings.</summary>
        public const uint PageUseShadowInfo = 0x004;
        /// <summary>Bit flag (<c>0x008</c>): scene saves axes location.</summary>
        public const uint PageUseAxes = 0x008;
        /// <summary>Legacy bit flag (<c>0x010</c>): before SketchUp 2020.1, scene saves both hidden geometry and hidden objects.</summary>
        public const uint PageUseHidden = 0x010;
        /// <summary>Bit flag (<c>0x020</c>): scene saves visible/hidden tags (layers).</summary>
        public const uint PageUseHiddenLayers = 0x020;
        /// <summary>Bit flag (<c>0x040</c>): scene saves active section planes.</summary>
        public const uint PageUseSectionPlanes = 0x040;
        /// <summary>Bit flag (<c>0x080</c>): scene saves hidden edges and faces (SketchUp 2020.1+).</summary>
        public const uint PageUseHiddenGeometry = 0x080;
        /// <summary>Bit flag (<c>0x100</c>): scene saves hidden groups and components (SketchUp 2020.1+).</summary>
        public const uint PageUseHiddenObjects = 0x100;

        /// <summary>Persistent ids of the entities this scene hides, in file order.</summary>
        public List<long> HiddenEntityIds { get; set; } = new List<long>();

        /// <summary>Which properties the scene saves ("Properties to save" in SketchUp's Scenes
        /// panel), as the u32 the file stores, or null when unknown.</summary>
        public uint? CaptureFlags { get; set; }

        private bool? Flag(uint bit) => CaptureFlags is uint f ? (f & bit) != 0 : (bool?)null;

        /// <summary>The scene saves its camera ("Camera Location"), 0x001.</summary>
        public bool? UseCamera => Flag(PageUseCamera);

        /// <summary>The scene saves its style ("Style and Fog"), 0x002.</summary>
        public bool? UseRenderingOptions => Flag(PageUseRenderingOptions);

        /// <summary>The scene saves its shadows ("Shadow Settings"), 0x004.</summary>
        public bool? UseShadowInfo => Flag(PageUseShadowInfo);

        /// <summary>The scene saves the axes ("Axes Location"), 0x008.</summary>
        public bool? UseAxes => Flag(PageUseAxes);

        /// <summary>The scene saves which layers it hides ("Visible Tags"), 0x020.</summary>
        public bool? UseHiddenLayers => Flag(PageUseHiddenLayers);

        /// <summary>The scene saves the active section ("Active Section Planes"), 0x040.</summary>
        public bool? UseSectionPlanes => Flag(PageUseSectionPlanes);

        private bool? HiddenFlag(uint bit)
        {
            if (CaptureFlags is not uint flags) return null;
            if ((flags & (PageUseHiddenGeometry | PageUseHiddenObjects)) != 0)
            {
                return (flags & bit) != 0;
            }
            return (flags & PageUseHidden) != 0;
        }

        /// <summary>The scene saves which edges and faces it hides ("Hidden Geometry"), 0x080.</summary>
        public bool? UseHiddenGeometry => HiddenFlag(PageUseHiddenGeometry);

        /// <summary>The scene saves which groups and components it hides ("Hidden Objects"), 0x100.</summary>
        public bool? UseHiddenObjects => HiddenFlag(PageUseHiddenObjects);

        /// <inheritdoc />
        public bool Equals(Page? other)
        {
            if (ReferenceEquals(this, other)) return true;
            if (other is null) return false;
            return Name == other.Name
                && Nullable.Equals(Eye, other.Eye)
                && Nullable.Equals(Target, other.Target)
                && Nullable.Equals(Up, other.Up)
                && Fov.Equals(other.Fov)
                && Parallel == other.Parallel
                && OrthoHeight.Equals(other.OrthoHeight)
                && CaptureFlags == other.CaptureFlags
                && System.Linq.Enumerable.SequenceEqual(HiddenLayers, other.HiddenLayers)
                && System.Linq.Enumerable.SequenceEqual(HiddenEntityIds, other.HiddenEntityIds);
        }

        /// <inheritdoc />
        public override bool Equals(object? obj) => Equals(obj as Page);

        /// <inheritdoc />
        public override int GetHashCode()
        {
            unchecked
            {
                int hash = Name.GetHashCode();
                hash = (hash * 397) ^ Eye.GetHashCode();
                hash = (hash * 397) ^ Target.GetHashCode();
                hash = (hash * 397) ^ Up.GetHashCode();
                hash = (hash * 397) ^ Fov.GetHashCode();
                hash = (hash * 397) ^ Parallel.GetHashCode();
                hash = (hash * 397) ^ OrthoHeight.GetHashCode();
                hash = (hash * 397) ^ CaptureFlags.GetHashCode();
                foreach (var layer in HiddenLayers)
                {
                    hash = (hash * 397) ^ (layer?.GetHashCode() ?? 0);
                }
                foreach (var id in HiddenEntityIds)
                {
                    hash = (hash * 397) ^ id.GetHashCode();
                }
                return hash;
            }
        }
    }

    /// <summary>A component or group geometry definition (or the model's <see cref="SkpModel.Root"/> container).</summary>
    public sealed class Definition
    {
        /// <summary>Numeric definition entity ID in <see cref="SkpModel.Definitions"/>.</summary>
        public long Id { get; set; }
        /// <summary>Canonical 32-hex-digit component definition GUID, or <c>"ROOT"</c> on <see cref="SkpModel.Root"/>.</summary>
        public string Guid { get; set; } = "";
        /// <summary>Component or group definition name.</summary>
        public string Name { get; set; } = "";
        /// <summary>Vertices belonging to this definition, keyed by <see cref="Vertex.Id"/>.</summary>
        public Dictionary<long, Vertex> Vertices { get; set; } = new Dictionary<long, Vertex>();
        /// <summary>Edges belonging to this definition, keyed by <see cref="Edge.Id"/>.</summary>
        public Dictionary<long, Edge> Edges { get; set; } = new Dictionary<long, Edge>();
        /// <summary>Faces belonging to this definition, keyed by <see cref="Face.Id"/>.</summary>
        public Dictionary<long, Face> Faces { get; set; } = new Dictionary<long, Face>();
        /// <summary>Component and group instances placed directly inside this definition.</summary>
        public List<Instance> Instances { get; set; } = new List<Instance>();
        /// <summary>Section planes owned by this definition.</summary>
        public List<SectionPlane> SectionPlanes { get; set; } = new List<SectionPlane>();
        /// <summary>Text annotations owned by this definition.</summary>
        public List<TextEntity> Texts { get; set; } = new List<TextEntity>();
        /// <summary>Linear dimensions owned by this definition.</summary>
        public List<Dimension> Dimensions { get; set; } = new List<Dimension>();
        /// <summary>Construction/guide lines owned by this definition.</summary>
        public List<ConstructionLine> ConstructionLines { get; set; } = new List<ConstructionLine>();
        /// <summary>Construction/guide points owned by this definition.</summary>
        public List<ConstructionPoint> ConstructionPoints { get; set; } = new List<ConstructionPoint>();
        /// <summary>The analytic arc of every curve that is one, keyed by curve id - the
        /// <see cref="Edge.CurveId"/> of the edges that form it. A curve that is not an arc (a
        /// freehand or polygon curve) has no entry.</summary>
        public Dictionary<long, ArcCurve> Arcs { get; set; } = new Dictionary<long, ArcCurve>();
        /// <summary>SketchUp's "Always face camera" billboard behaviour flag.</summary>
        public bool AlwaysFacesCamera { get; set; }
        /// <summary>SketchUp's "Shadows face sun" billboard flag when <see cref="AlwaysFacesCamera"/> is set.</summary>
        public bool ShadowsFaceSun { get; set; }
        /// <summary><c>true</c> when this definition backs a SketchUp Image entity.</summary>
        public bool IsImage { get; set; }
        /// <summary>True when SketchUp made this definition for a group rather than a
        /// component (what its own API reports as <c>SUComponentType_Group</c>). Groups and
        /// component instances are both <see cref="Instance"/> placements of a
        /// <see cref="Definition"/>; this is the only thing that tells them apart. It comes
        /// from the file itself - the definition kind in a 2021+ file, a flag in the
        /// definition record in an older one - not from how often the definition is
        /// placed.</summary>
        public bool IsGroup { get; set; }
        /// <summary>Native definition attribute dictionaries from VFF and classic pre-2021 files, preserving typed values.</summary>
        public Dictionary<string, Dictionary<string, object?>> AttributeDictionaries { get; set; } =
            new Dictionary<string, Dictionary<string, object?>>();

        /// <summary>Groups this definition's LOOSE edges (edges no face uses)
        /// into ordered polyline runs, using the file's own <c>Edge#curve</c>
        /// (<see cref="Edge.CurveId"/>) grouping.</summary>
        public List<LooseEdgeRun> LooseEdgeRuns() => SkpModel.LooseEdgeRuns(this);
    }

    /// <summary>Complete parsed representation of a SketchUp file, mirroring
    /// the shape of Python's public SkpFile.parse() result.</summary>
    public sealed class SkpModel
    {
        /// <summary>SketchUp file format version string (for example <c>"{21.0.1}"</c> or <c>"{17.0.1}"</c>).</summary>
        public string Version { get; set; } = "unknown";

        /// <summary>The model's unit-system string (e.g. "Millimeter"),
        /// read from meta/meta.dat in modern (VFF) files. Null for legacy
        /// (pre-2021 MFC) files, which carry no equivalent container, or
        /// when the tag isn't found.</summary>
        public string? Units { get; set; }

        /// <summary>Component/group definitions keyed by their numeric TLV
        /// entity ID. The implicit root definition (Python's "ROOT" dict
        /// entry, which has no numeric ID) is exposed separately via
        /// <see cref="Root"/> instead of living in this dictionary.</summary>
        public Dictionary<long, Definition> Definitions { get; set; } = new Dictionary<long, Definition>();

        /// <summary>The implicit top-level model definition: its Instances
        /// are the entities placed directly in the model (not inside any
        /// component/group). Corresponds to Python's defs_dict['ROOT'].</summary>
        public Definition Root { get; set; } = new Definition { Name = "ROOT_MODEL" };

        /// <summary>Model layers (tags) in file order.</summary>
        public List<Layer> Layers { get; set; } = new List<Layer>();

        /// <summary>The file's saved scenes, from VFF files and from classic pre-2021 files.</summary>
        public List<Page> Pages { get; set; } = new List<Page>();

        /// <summary>Model-level linear dimensions with world-space endpoints
        /// (VFF files). Legacy files surface text-only dimensions per
        /// definition instead (<see cref="Definition.Dimensions"/>).</summary>
        public List<Dimension> Dimensions { get; set; } = new List<Dimension>();

        /// <summary>Model surface materials.</summary>
        public List<Material> Materials { get; set; } = new List<Material>();

        /// <summary>Join table for Face.MaterialId / Instance.MaterialId:
        /// TLV material ID -> Material. Several IDs may alias the same
        /// Material instance.</summary>
        public Dictionary<long, Material> MaterialsById { get; set; } = new Dictionary<long, Material>();

        /// <summary>Bundled visual styles read from modern VFF files.</summary>
        public List<Style> Styles { get; set; } = new List<Style>();

        /// <summary>The colour SketchUp shows on the front side of a face with
        /// no paint on that side, <c>(R, G, B)</c> 0-255: the model's current
        /// rendering options, which is what SketchUp displays even when the
        /// style was changed and not saved (so it can differ from every entry
        /// in <see cref="Styles"/>). Read from 2021+ files and from classic
        /// files of SketchUp 4 to 2020; <c>null</c> when the file does not give
        /// it (SketchUp 3).</summary>
        public (int R, int G, int B)? FaceFrontColor { get; set; }

        /// <summary>The same for the back side of a face.</summary>
        public (int R, int G, int B)? FaceBackColor { get; set; }

        /// <summary>Whether SketchUp's "Color by Tag" (<c>DisplayColorByLayer</c>)
        /// style setting is enabled in the model's active rendering options.
        /// Read from 2021+ files and from classic files of SketchUp 4 to 2020;
        /// <c>false</c> when off or when the file does not give it (SketchUp 3).</summary>
        public bool ColorByTag { get; set; }

        /// <summary>Every attribute dictionary attached to the model itself
        /// (Ruby's <c>Sketchup::Model#attribute_dictionaries</c>), keyed by
        /// the dictionary's own declared name, preserving native value types.
        /// Populated for both modern (VFF) and classic (pre-2021 MFC) files,
        /// and shared on <c>Root.AttributeDictionaries</c>.</summary>
        public Dictionary<string, Dictionary<string, object?>> AttributeDictionaries { get; set; } =
            new Dictionary<string, Dictionary<string, object?>>();

        /// <summary>How many top-level entities of a classic (pre-2021) file
        /// the reader couldn't read. It keeps what it read before the first
        /// root record it can't parse and drops that record and everything
        /// after it, so a nonzero count means the model lost geometry,
        /// components, groups, text or dimensions. Phantom entries the root
        /// count includes but the file never writes are not counted. Always 0
        /// for VFF files.</summary>
        public int UnreadRootEntities { get; set; }

        /// <summary>Groups <paramref name="definition"/>'s LOOSE edges (edges
        /// no face uses) into ordered polyline runs, using the file's own
        /// <c>Edge#curve</c> (<see cref="Edge.CurveId"/>) grouping, in the same
        /// order as <see cref="InstancedScene.CurveResources"/>.</summary>
        public static List<LooseEdgeRun> LooseEdgeRuns(Definition definition)
        {
            if (definition == null) throw new ArgumentNullException(nameof(definition));
            if (definition.Edges.Count == 0) return new List<LooseEdgeRun>();

            var used = new HashSet<long>();
            foreach (var face in definition.Faces.Values)
            {
                foreach (var loop in face.Loops)
                {
                    foreach (var co in loop)
                    {
                        used.Add(co.EdgeId);
                    }
                }
            }

            var edgesById = new Dictionary<long, (long V1, long V2)>();
            var groupOrder = new List<long>();
            var groups = new Dictionary<long, List<long>>();
            var runs = new List<LooseEdgeRun>();

            foreach (var kv in definition.Edges)
            {
                var e = kv.Value;
                if (used.Contains(e.Id) || e.V1Id == e.V2Id)
                {
                    continue;
                }
                if (e.CurveId is long cid && cid != 0)
                {
                    edgesById[e.Id] = (e.V1Id, e.V2Id);
                    if (!groups.TryGetValue(cid, out var grp))
                    {
                        grp = new List<long>();
                        groups[cid] = grp;
                        groupOrder.Add(cid);
                    }
                    grp.Add(e.Id);
                }
                else
                {
                    runs.Add(new LooseEdgeRun
                    {
                        EdgeIds = new List<long> { e.Id },
                        VertexIds = new List<long> { e.V1Id, e.V2Id },
                        Closed = false,
                    });
                }
            }

            foreach (var cid in groupOrder)
            {
                runs.AddRange(Curves.OrderCurveEdges(edgesById, groups[cid]));
            }

            return runs;
        }
    }
}
