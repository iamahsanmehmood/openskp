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
    }

    public sealed class Layer
    {
        public string Name { get; set; } = "";
        public int ColorR { get; set; } = 200;
        public int ColorG { get; set; } = 200;
        public int ColorB { get; set; } = 200;
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
        public string Name { get; set; } = "";
        public long? RefIdx { get; set; }
        public string Guid { get; set; } = "";

        /// <summary>4x4 transform stored as a flat 16-element list in
        /// column-major order (empty when the entity carried none).</summary>
        public List<double> Matrix { get; set; } = new List<double>();

        public string Layer { get; set; } = "";
        public Dictionary<string, string> Properties { get; set; } = new Dictionary<string, string>();
        public List<Instance> Children { get; set; } = new List<Instance>();
        public long? MaterialId { get; set; }
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
        public bool AlwaysFacesCamera { get; set; }
        public bool IsImage { get; set; }
    }

    /// <summary>Complete parsed representation of a SketchUp file, mirroring
    /// the shape of Python's public SkpFile.parse() result.</summary>
    public sealed class SkpModel
    {
        public string Version { get; set; } = "unknown";

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
        public List<Material> Materials { get; set; } = new List<Material>();

        /// <summary>Join table for Face.MaterialId / Instance.MaterialId:
        /// TLV material ID -> Material. Several IDs may alias the same
        /// Material instance.</summary>
        public Dictionary<long, Material> MaterialsById { get; set; } = new Dictionary<long, Material>();

        public List<Style> Styles { get; set; } = new List<Style>();
    }
}
