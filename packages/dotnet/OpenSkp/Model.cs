using System;
using System.Collections.Generic;

namespace OpenSkp
{
    public sealed class Vertex
    {
        public long Id { get; set; }
        public double X { get; set; }
        public double Y { get; set; }
        public double Z { get; set; }
    }

    public sealed class Edge
    {
        public long Id { get; set; }
        public long V1Id { get; set; }
        public long V2Id { get; set; }
        public bool Soft { get; set; }
        public bool Smooth { get; set; }
        public bool Hidden { get; set; }
        /// <summary>SketchUp's own <c>Edge#curve</c> grouping: the id of the Curve this edge belongs
        /// to, or null when it belongs to none. Edges sharing a CurveId were drawn as one continuous
        /// multi-segment line or arc; if the curve is an arc, <see cref="Definition.Arcs"/> has it
        /// under the same id.</summary>
        public long? CurveId { get; set; }
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
        public double Radius => Math.Sqrt(XAxis.X * XAxis.X + XAxis.Y * XAxis.Y + XAxis.Z * XAxis.Z);
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

    public sealed class Face
    {
        public long Id { get; set; }

        /// <summary>Ordered list of loops; each loop is a list of
        /// (edgeId, orientation) pairs where orientation is 1 for forward or
        /// -1 for reversed.</summary>
        public List<List<(long EdgeId, long Orientation)>> Loops { get; set; } = new List<List<(long, long)>>();

        public (double Nx, double Ny, double Nz)? Normal { get; set; }
        public long? MaterialId { get; set; }
        public long? BackMaterialId { get; set; }

        /// <summary>Per-face texture mapping for a positioned/photo-fitted
        /// texture (SketchUp's pins), or null when the default projection
        /// applies. A 9-element row-major 3x3 matrix mapping texture space to
        /// the face plane.</summary>
        public double[]? UvTransform { get; set; }
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
        public long PersistentId { get; set; }

        /// <summary>Name of the layer (tag) this face is assigned to, or "" when the face carries no
        /// explicit layer reference (default layer).</summary>
        public string Layer { get; set; } = "";
    }

    public sealed class Layer
    {
        public string Name { get; set; } = "";
        public int ColorR { get; set; } = 200;
        public int ColorG { get; set; } = 200;
        public int ColorB { get; set; } = 200;

        /// <summary>Whether the layer's visibility is switched off. Only
        /// populated for legacy (pre-2021 MFC) files, where the byte is read
        /// directly from the layer record - modern (VFF) files derive layers
        /// from Layer_&lt;name&gt;-prefixed materials, which carry no
        /// visibility data, so this is always false there.</summary>
        public bool Hidden { get; set; }
    }

    public sealed class Style
    {
        public string Name { get; set; } = "";
        public (int R, int G, int B)? FrontColor { get; set; }
        public (int R, int G, int B)? BackColor { get; set; }
    }

    public sealed class Texture
    {
        public string Filename { get; set; } = "";
        public double Width { get; set; }
        public double Height { get; set; }
        public byte[]? Data { get; set; }

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

    public sealed class Material
    {
        public string Name { get; set; } = "";
        public (int R, int G, int B, int A) Color { get; set; } = (200, 200, 200, 255);
        public double Transparency { get; set; } = 1.0;

        /// <summary>Numeric material ID from the TLV stream - the value that
        /// Face.MaterialId references. Null when the file assigns the
        /// material no ID.</summary>
        public long? Id { get; set; }

        public Texture? Texture { get; set; }
        public bool Colorized { get; set; }
        public int ColorizeType { get; set; }
    }

    public sealed class Instance
    {
        public long? Id { get; set; }
        public string Name { get; set; } = "";
        public long? RefIdx { get; set; }
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
        public long? MaterialId { get; set; }

        /// <summary>Whether the instance itself is hidden (SketchUp's "Hide"
        /// on this specific component/group placement, not a layer/tag
        /// visibility toggle).</summary>
        public bool Hidden { get; set; }
    }

    public sealed class SectionPlane
    {
        public double[] Plane { get; set; } = new double[] { 0.0, 0.0, 1.0, 0.0 };
        public string Name { get; set; } = "";
        public string Label { get; set; } = "";
        public bool Hidden { get; set; }
    }

    public sealed class TextEntity
    {
        public string Text { get; set; } = "";
        public bool Hidden { get; set; }
        /// <summary>Explicit tag name, or empty string for the default tag (Layer0).</summary>
        public string Layer { get; set; } = "";
        /// <summary>Anchor and label positions in owner-local inches; null when unresolved.</summary>
        public (double X, double Y, double Z)? Point { get; set; }
        public (double X, double Y, double Z)? LabelPoint { get; set; }
        /// <summary>Normalized screen fractions (used when LeaderType is zero).</summary>
        public (double X, double Y)? ScreenPosition { get; set; }
        public int LeaderType { get; set; } = 2;
        public int ArrowType { get; set; } = 3;
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

        public const uint PageUseCamera = 0x001;
        public const uint PageUseRenderingOptions = 0x002;
        public const uint PageUseShadowInfo = 0x004;
        public const uint PageUseAxes = 0x008;
        public const uint PageUseHidden = 0x010; // before SketchUp 2020.1: geometry + objects
        public const uint PageUseHiddenLayers = 0x020;
        public const uint PageUseSectionPlanes = 0x040;
        public const uint PageUseHiddenGeometry = 0x080;
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

        public override bool Equals(object? obj) => Equals(obj as Page);

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

    public sealed class Definition
    {
        public long Id { get; set; }
        public string Guid { get; set; } = "";
        public string Name { get; set; } = "";
        public Dictionary<long, Vertex> Vertices { get; set; } = new Dictionary<long, Vertex>();
        public Dictionary<long, Edge> Edges { get; set; } = new Dictionary<long, Edge>();
        public Dictionary<long, Face> Faces { get; set; } = new Dictionary<long, Face>();
        public List<Instance> Instances { get; set; } = new List<Instance>();
        public List<SectionPlane> SectionPlanes { get; set; } = new List<SectionPlane>();
        public List<TextEntity> Texts { get; set; } = new List<TextEntity>();
        public List<Dimension> Dimensions { get; set; } = new List<Dimension>();
        public List<ConstructionLine> ConstructionLines { get; set; } = new List<ConstructionLine>();
        public List<ConstructionPoint> ConstructionPoints { get; set; } = new List<ConstructionPoint>();
        /// <summary>The analytic arc of every curve that is one, keyed by curve id - the
        /// <see cref="Edge.CurveId"/> of the edges that form it. A curve that is not an arc (a
        /// freehand or polygon curve) has no entry.</summary>
        public Dictionary<long, ArcCurve> Arcs { get; set; } = new Dictionary<long, ArcCurve>();
        public bool AlwaysFacesCamera { get; set; }
        public bool ShadowsFaceSun { get; set; }
        public bool IsImage { get; set; }
        /// <summary>True when SketchUp made this definition for a group rather than a
        /// component (what its own API reports as <c>SUComponentType_Group</c>). Groups and
        /// component instances are both <see cref="Instance"/> placements of a
        /// <see cref="Definition"/>; this is the only thing that tells them apart. It comes
        /// from the file itself - the definition kind in a 2021+ file, a flag in the
        /// definition record in an older one - not from how often the definition is
        /// placed.</summary>
        public bool IsGroup { get; set; }
        /// <summary>Native definition attribute dictionaries from VFF files, preserving typed values.</summary>
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

        public List<Layer> Layers { get; set; } = new List<Layer>();

        /// <summary>The file's saved scenes, from VFF files and from classic pre-2021 files.</summary>
        public List<Page> Pages { get; set; } = new List<Page>();

        /// <summary>Model-level linear dimensions with world-space endpoints
        /// (VFF files). Legacy files surface text-only dimensions per
        /// definition instead (<see cref="Definition.Dimensions"/>).</summary>
        public List<Dimension> Dimensions { get; set; } = new List<Dimension>();

        public List<Material> Materials { get; set; } = new List<Material>();

        /// <summary>Join table for Face.MaterialId / Instance.MaterialId:
        /// TLV material ID -> Material. Several IDs may alias the same
        /// Material instance.</summary>
        public Dictionary<long, Material> MaterialsById { get; set; } = new Dictionary<long, Material>();

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
