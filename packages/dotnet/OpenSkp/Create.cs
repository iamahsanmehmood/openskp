using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;

namespace OpenSkp
{
    /// <summary>
    /// Create new legacy-format (v17) ``.skp`` files from scratch.
    ///
    /// This is a genuine, from-scratch binary writer for the same MFC
    /// <c>CArchive</c> object-stream format <see cref="Legacy"/> reads - built
    /// by inverting that reader's own, already-proven decoding logic (the
    /// class-ref/back-ref protocol, entity preambles, drawbase records), then
    /// validated against real desktop SketchUp until it produced files
    /// SketchUp actually opens correctly, not just files OpenSKP's own reader
    /// accepts. No SketchUp SDK is called at runtime; this file never links
    /// against or shells out to any proprietary library. See the scaffold
    /// note below for the one place SDK-authored bytes are involved, and how.
    ///
    /// Ported from openskp/create.py - see that module's own (extensively
    /// commented) docstring and inline comments for the full reasoning
    /// behind every non-obvious byte value here; comments in this file
    /// summarize the same ground-truth findings rather than re-deriving
    /// them independently.
    ///
    /// <b>Scope (deliberately limited for this first version, matching the
    /// Python writer):</b> faces built directly from vertex coordinates,
    /// sharing vertices/edges automatically wherever coordinates coincide
    /// exactly. Solid-color and PNG/JPEG-textured materials, named layers,
    /// reusable component definitions with multiple positioned instances,
    /// and groups are all supported. A definition can nest instances (or
    /// group instances) of another, already-closed definition. A face's
    /// texture can be explicitly positioned (front/back independently) via
    /// <see cref="ComponentDefinitionBuilder.AddFace"/>'s <c>frontUv</c>/
    /// <c>backUv</c> parameters. Component definitions, instances, and faces
    /// can carry custom key/value metadata (string/int/double values) via
    /// their <c>attributes</c> parameters - the same mechanism SketchUp's
    /// own "dynamic component" attributes use; not yet supported on groups
    /// (ground truth shows a group's own attribute pointer is always null).
    /// Circular faces and partial (open) arcs - real, editable-by-radius
    /// SketchUp arc/circle entities - are supported via <c>AddCircle</c>/
    /// <c>AddArc</c>, as are freeform polyline curves (<c>CCurve</c>) via
    /// <c>AddPolyline</c>. An instance/group placement's rotation can be
    /// given directly as an (axis, angle) pair instead of a hand-derived
    /// 3x3 matrix. <c>AddFace</c>'s <c>autoTriangulate</c> fan-splits a
    /// non-coplanar polygon into real, always-planar triangular faces
    /// instead of throwing - the same thing real SketchUp's own UI does
    /// silently for a not-quite-flat quad.
    ///
    /// Coordinates are in <b>inches</b> - SketchUp's own native internal unit
    /// for this era of the format. Converting from another unit is the
    /// caller's responsibility for now.
    ///
    /// Every file opens to the standard "Iso" view (parallel projection,
    /// looking at the origin from the (1, -1, 1) octant) rather than the
    /// blank scaffold's own arbitrary default camera. Not configurable yet.
    ///
    /// <b>The blank scaffold, and why it's there.</b> Every legacy .skp file
    /// carries a header/material-manager/style-and-font-manager region this
    /// project has not fully reverse-engineered - only enough of it is
    /// understood to preserve it byte-for-byte and correctly renumber the
    /// handful of internal references inside it that shift when new geometry
    /// is inserted (see <see cref="TailRefPositions"/> below). Rather than
    /// guess at synthesizing that region from scratch, new files are built by
    /// splicing genuinely-written geometry into a bundled minimal
    /// empty-document template (the embedded <c>_scaffold/blank_v17.skp</c>
    /// resource, identical to the Python package's own copy).
    ///
    /// That template's bytes came from Trimble's own official SketchUp SDK
    /// during this feature's research phase (<c>SUModelCreate</c> + a bare
    /// <c>SUModelSaveToFileWithVersion</c> call, nothing else) - disclosed
    /// here plainly rather than hidden. Its content is SketchUp's own
    /// built-in empty-document boilerplate (default style, default
    /// "Layer0", references to system fonts like Arial/Tahoma) - the same
    /// bytes any brand-new SketchUp document contains regardless of who
    /// created it, not anyone's creative work or user/client data. The
    /// actual value in this file - the entity byte-encoding, the
    /// object-graph protocol, the specific flag bytes real SketchUp
    /// silently requires that Legacy.cs's own reader documents as "unused,"
    /// the tail-reference renumbering - is 100% independently
    /// reverse-engineered, written from scratch, and is what makes this a
    /// genuine writer rather than a wrapper around the SDK. No SDK call
    /// happens at load time, write time, or any other runtime path.
    /// </summary>
    public sealed class SkpWriteException : Exception
    {
        public SkpWriteException(string message) : base(message) { }
    }

    /// <summary>3D point convenience alias - a plain (X, Y, Z) tuple in
    /// inches, matching the rest of this codebase's own tuple convention
    /// (see Scene.cs).</summary>
    public static class SkpCreate
    {
        /// <summary>Start building a new legacy-format (v17) .skp file from
        /// scratch.
        ///
        /// <code>
        /// var builder = SkpCreate.NewFile();
        /// int red = builder.AddMaterial("Red", (255, 0, 0, 255));
        /// int roof = builder.AddLayer("Roof");
        /// builder.AddFace(new (double,double,double)[] {
        ///     (0,0,0), (100,0,0), (100,100,0), (0,100,0)
        /// }, material: red, layer: roof);
        /// builder.Save("output.skp");
        /// </code>
        ///
        /// See this file's own header comment for the current scope and
        /// limitations (no inline-declared nested groups; inches only).
        /// </summary>
        public static SkpBuilder NewFile() => new SkpBuilder();
    }

    // ── ground-truth constants (see the module header comment above and
    // create.py's own inline comments for the full derivation of each) ──

    internal static class CreateConstants
    {
        internal const string ScaffoldResourceName = "OpenSkp._scaffold.blank_v17.skp";

        // Guards against silent corruption if the bundled scaffold is ever
        // swapped without updating TailRefPositions below - those offsets
        // are specific to this exact file's bytes, not derived generically.
        internal const string ScaffoldSha256 = "809a1ab73a20a192ab13aaff197afb1c67d0e9352f6a353a9cd8030919f8a6c3";

        // Offsets (relative to the start of the document "tail" - the
        // undecoded style/font-manager region that follows the root entity
        // list) of internal references that must be renumbered by the same
        // amount as the number of new archive slots inserted before them.
        // Found empirically by diffing two real SDK-authored v17 files
        // differing by exactly one piece of geometry and confirmed to hold
        // up to a 600-new-entity insertion via the real SketchUp SDK as a
        // validation oracle. Specific to this exact scaffold file's tail
        // content.
        internal static readonly int[] TailRefPositions = { 409, 468, 477, 479, 1383, 1385 };

        // The blank scaffold ships with SketchUp's own arbitrary default
        // camera; every file this writer produces instead always patches it
        // to the standard "Iso" view (eye along the (1, -1, 1) octant
        // looking at the origin, up = Z, parallel/orthographic projection)
        // so it opens already framed the conventional way. Found by diffing
        // two SDK-authored blank documents that differ only in an explicit
        // camera-orientation + parallel-projection call before saving -
        // these are the exact bytes real SketchUp itself wrote for that
        // camera, copied verbatim rather than decoded. The prefix offset is
        // absolute (within the always-unshifted scaffold prefix); the tail
        // patches are relative to the document "tail" like
        // TailRefPositions, since this camera setting also touches two
        // small fields further into that region.
        internal const int IsoCameraPrefixOffset = 2993;

        internal static readonly byte[] IsoCameraPrefixPatch = FromHex(
            "594000000000000059c000000000000059400000000000000000000000000000"
            + "000000000000000000003f2c0c70bd20dabf3f2c0c70bd20da3f3f2c0c70bd20"
            + "ea3f000000000000f03f0000000000408f40000000000000003e402adf272c80"
            + "3457");

        internal static readonly (int Pos, byte[] Patch)[] IsoCameraTailPatches =
        {
            (509, FromHex("d0a869613c442d4799a4667d1adfa836")),
            (1390, FromHex("4e53c84477029246bba95827bba7e2")),
        };

        // Offset (relative to the material-manager insertion point - the
        // position right before the "layer list marker" that a
        // zero-material scaffold starts with) of the active-layer anchor -
        // a back-reference to the model's first layer (Layer0) that lives
        // immediately after the last existing layer record. It moves only
        // when materials shift Layer0's own slot (never when layers are
        // appended after it - confirmed empirically).
        internal const int ActiveLayerAnchorRel = 0;

        internal const int LayerSchema = 3;

        // Absolute offset of a u16 "next available pid" counter that lives
        // BEFORE the material insertion point (so only its value, not its
        // position, needs correction). Increments by exactly the material
        // COUNT (one pid consumed per material object; the material class
        // declaration itself doesn't consume a pid). Confirmed up to N=300.
        internal const int PidCounterPos = 1987;

        internal const int MaterialSchema = 12;
        internal const int DibSchema = 3;

        // Ground-truth byte pattern (not a meaningful float) that real
        // SketchUp writes for a texture's "applied height" when the caller
        // never explicitly overrides the texture's scale/aspect - found by
        // diffing an SDK-authored textured-material file; present verbatim
        // rather than derived from a formula since its bit pattern doesn't
        // correspond to any sensible height value (it decodes as ~1.29e-231
        // as an f64).
        internal static readonly byte[] TextureHSentinel = FromHex("f0ffffffffffff0f");

        internal const int DefinitionSchema = 11;
        internal const int InstanceSchema = 6;
        internal const int GroupSchema = 1;
        // UNVERIFIED - unlike every other schema constant here, not
        // calibrated against a real SketchUp-authored file: no sample
        // containing a CImage entity (File > Import > Image) was available
        // (ported from the Python writer, same caveat there - see
        // create.py's own comment). Legacy.cs's image reader never
        // branches on schema the way instance/group reading does, so this
        // project's own reader round-trips correctly regardless of the
        // exact value - this only affects whether real SketchUp accepts
        // the file. Chosen to match InstanceSchema for the same reason as
        // Python's _IMAGE_SCHEMA: CImage's read path always expects the
        // trailing GUID unconditionally, the same "always present" shape
        // CComponentInstance has.
        internal const int ImageSchema = 6;
        internal const int ThumbnailSchema = 1;

        // CCamera's class is declared inside the scaffold's own
        // style/scene-manager prefix (before any of our splice points) -
        // ground-truth confirmed fixed at slot 7 for this exact bundled
        // scaffold file. A thumbnail's camera sub-object is always written
        // as a short class-ref to this slot.
        internal const int CCameraSlot = 7;

        // Same pattern as CCameraSlot: CAttributeContainer's class is
        // declared in the scaffold's own prefix, ground-truth confirmed
        // fixed at slot 3.
        internal const int AttrContainerSlot = 3;

        // Same pattern again: CAttributeNamed (one named key/value
        // dictionary within an attribute container) is also pre-declared in
        // the scaffold's own prefix, ground-truth confirmed fixed at slot 5.
        internal const int AttributeNamedSlot = 5;

        // CAttributeNamed's own value-type tags, ground-truth-and-reader
        // confirmed - only the 3 most commonly useful ones for custom
        // metadata are exposed by this writer for now.
        internal const byte AttrTypeInt32 = 0x04;
        internal const byte AttrTypeDouble = 0x06;
        internal const byte AttrTypeString = 0x0A;

        // The 176 bytes (everything after CCamera's 2-byte class-ref tag)
        // real SketchUp writes for a definition's default thumbnail camera -
        // copied verbatim rather than decoded, the same way as
        // TextureHSentinel: this project has not reverse-engineered
        // CCamera's internal fields, and a thumbnail's camera framing has
        // no bearing on the geometry it depicts.
        internal static readonly byte[] CameraTemplate = FromHex(
            "00000000000000000000000000000000000000000000f03f0000000000000000"
            + "00000000000000000000000000000000004000000000000000000000000000f0"
            + "3f0000000000000000000000000000000000000000000000000100000000003e"
            + "40000000000000f03f0000000000000000000000000000000000000000000000"
            + "0000000000000000000100fffeff00000000000000000000000000000000f03f"
            + "00000000000000000000000000000000");

        // The definition record's 22-byte "base block" (immediately after
        // its own preamble, before the embedded layer list) - all zero
        // except offsets 3-4, matching the same 1,1 padding convention
        // drawbase already requires. This project has not reverse-engineered
        // its meaning, only confirmed via ground truth that a definition
        // with these bytes zeroed loads correctly.
        internal static readonly byte[] DefinitionBaseBlock =
            { 0, 0, 0, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 };

        internal const int FtcSchema = 4;
        internal const int ArcCurveSchema = 3;
        internal const int CCurveSchema = 4;

        // A face with no explicit texture positioning stores no
        // CFaceTextureCoords at all, so this identity is only ever used to
        // fill the *other* side's slot when just one of front/back is
        // explicitly positioned - real SketchUp still writes a full 24-f64
        // block either way, just with the unpositioned side's matrix left
        // as identity, ground-truth confirmed by positioning only one side
        // and reading the other back as (1,0,0, 0,1,0, 0,0,1).
        internal static readonly double[] IdentityUvMatrix = { 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0 };

        internal static byte[] FromHex(string hex)
        {
            var bytes = new byte[hex.Length / 2];
            for (int i = 0; i < bytes.Length; i++)
            {
                bytes[i] = Convert.ToByte(hex.Substring(i * 2, 2), 16);
            }
            return bytes;
        }
    }

    /// <summary>Small math/geometry helpers shared by <see cref="ArchiveWriter"/>
    /// and the builder classes - direct ports of create.py's module-level
    /// free functions.</summary>
    internal static class CreateMath
    {
        internal static double Det3(double[][] m)
        {
            return m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
                 - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
                 + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
        }

        /// <summary>Solve the 3x3 linear system a @ x = b via Cramer's rule.</summary>
        internal static (double, double, double) Solve3x3(double[][] a, double[] b)
        {
            double d = Det3(a);
            if (Math.Abs(d) < 1e-9)
            {
                throw new SkpWriteException(
                    "the 3 texture-positioning points map to collinear (u, v) coordinates - "
                    + "cannot determine a texture mapping from them");
            }
            var cols = new double[3];
            for (int col = 0; col < 3; col++)
            {
                var ai = new double[3][];
                for (int r = 0; r < 3; r++)
                {
                    ai[r] = (double[])a[r].Clone();
                    ai[r][col] = b[r];
                }
                cols[col] = Det3(ai) / d;
            }
            return (cols[0], cols[1], cols[2]);
        }

        internal static (double X, double Y, double Z) Cross((double X, double Y, double Z) a, (double X, double Y, double Z) b) =>
            (a.Y * b.Z - a.Z * b.Y, a.Z * b.X - a.X * b.Z, a.X * b.Y - a.Y * b.X);

        internal static (double X, double Y, double Z) Normalize3((double X, double Y, double Z) v)
        {
            double length = Math.Sqrt(v.X * v.X + v.Y * v.Y + v.Z * v.Z);
            if (length < 1e-9)
            {
                throw new SkpWriteException("cannot determine a texture-positioning basis: the face's first edge is degenerate");
            }
            return (v.X / length, v.Y / length, v.Z / length);
        }

        /// <summary>The row-major 3x3 rotation matrix for rotating by
        /// <paramref name="angle"/> radians (right-hand rule) around
        /// <paramref name="axis"/> (need not be a unit vector) - Rodrigues'
        /// rotation formula. Same row-major convention AddInstance's own
        /// matrix3x3 parameter uses, so this is a drop-in way to get a
        /// rotation without hand-deriving the matrix.</summary>
        internal static double[] RotationMatrix3x3((double X, double Y, double Z) axis, double angle)
        {
            double length = Math.Sqrt(axis.X * axis.X + axis.Y * axis.Y + axis.Z * axis.Z);
            if (length < 1e-9)
            {
                throw new SkpWriteException("rotation axis must not be the zero vector");
            }
            double x = axis.X / length, y = axis.Y / length, z = axis.Z / length;
            double c = Math.Cos(angle), s = Math.Sin(angle), t = 1.0 - c;
            return new[]
            {
                t * x * x + c,     t * x * y - s * z, t * x * z + s * y,
                t * x * y + s * z, t * y * y + c,     t * y * z - s * x,
                t * x * z - s * y, t * y * z + s * x, t * z * z + c,
            };
        }

        /// <summary>Shared by every AddInstance/AddGroup/AddGroupInstance
        /// call - matrix3x3 and rotation are alternate ways to specify the
        /// same underlying transform field, not two separate ones, so at
        /// most one (or neither, for identity) may be given.</summary>
        internal static double[]? ResolveMatrix3x3(double[]? matrix3x3, ((double X, double Y, double Z) Axis, double AngleRadians)? rotation)
        {
            if (matrix3x3 != null && rotation != null)
            {
                throw new SkpWriteException("pass at most one of matrix3x3/rotation - rotation is just a convenience for matrix3x3");
            }
            if (rotation != null)
            {
                return RotationMatrix3x3(rotation.Value.Axis, rotation.Value.AngleRadians);
            }
            return matrix3x3;
        }

        /// <summary>The in-plane 2D basis (U, W) real SketchUp uses to
        /// parameterize a face's texture mapping, for a face of ANY
        /// orientation (not just axis-aligned) - ground truth shows it's
        /// simply the face's own first edge direction (points[1] -
        /// points[0], normalized) as U, and the plane normal crossed with
        /// that as W - both unit vectors.</summary>
        internal static ((double X, double Y, double Z) U, (double X, double Y, double Z) W) FaceUvBasis(
            IReadOnlyList<(double X, double Y, double Z)> points, (double X, double Y, double Z) normal)
        {
            var u = Normalize3((points[1].X - points[0].X, points[1].Y - points[0].Y, points[1].Z - points[0].Z));
            var w = Normalize3(Cross(normal, u));
            return (u, w);
        }

        /// <summary>An arbitrary orthonormal in-plane basis (U, W) for a
        /// circle/arc's plane, given only its normal - unlike
        /// <see cref="FaceUvBasis"/> there's no "first edge" to derive U
        /// from here, so pick whichever of world +Z/+X is less parallel to
        /// normal as a seed and Gram-Schmidt it against normal to get U,
        /// then W = normal x U.</summary>
        internal static ((double X, double Y, double Z) U, (double X, double Y, double Z) W) CircleBasis((double X, double Y, double Z) normal)
        {
            (double X, double Y, double Z) seed = Math.Abs(normal.Z) < 0.9 ? (0.0, 0.0, 1.0) : (1.0, 0.0, 0.0);
            double dot = seed.X * normal.X + seed.Y * normal.Y + seed.Z * normal.Z;
            var uRaw = (seed.X - dot * normal.X, seed.Y - dot * normal.Y, seed.Z - dot * normal.Z);
            var u = Normalize3(uRaw);
            var w = Normalize3(Cross(normal, u));
            return (u, w);
        }

        /// <summary>The numSegments polygon vertices approximating a full
        /// circle in center/radius/normal's plane, walking
        /// counter-clockwise around normal (right-hand rule) starting at
        /// center + radius*u.</summary>
        internal static List<(double X, double Y, double Z)> CirclePoints(
            (double X, double Y, double Z) center, double radius, int numSegments,
            (double X, double Y, double Z) u, (double X, double Y, double Z) w)
        {
            var pts = new List<(double, double, double)>(numSegments);
            for (int i = 0; i < numSegments; i++)
            {
                double angle = 2.0 * Math.PI * i / numSegments;
                double c = Math.Cos(angle), s = Math.Sin(angle);
                pts.Add((
                    center.X + radius * (c * u.X + s * w.X),
                    center.Y + radius * (c * u.Y + s * w.Y),
                    center.Z + radius * (c * u.Z + s * w.Z)));
            }
            return pts;
        }

        /// <summary>The numSegments + 1 points (both endpoints included)
        /// tracing a PARTIAL arc from startAngle to endAngle.</summary>
        internal static List<(double X, double Y, double Z)> ArcPoints(
            (double X, double Y, double Z) center, double radius, int numSegments,
            (double X, double Y, double Z) u, (double X, double Y, double Z) w,
            double startAngle, double endAngle)
        {
            var pts = new List<(double, double, double)>(numSegments + 1);
            for (int i = 0; i <= numSegments; i++)
            {
                double angle = startAngle + (endAngle - startAngle) * i / numSegments;
                double c = Math.Cos(angle), s = Math.Sin(angle);
                pts.Add((
                    center.X + radius * (c * u.X + s * w.X),
                    center.Y + radius * (c * u.Y + s * w.Y),
                    center.Z + radius * (c * u.Z + s * w.Z)));
            }
            return pts;
        }

        /// <summary>Fit the 3x3 UV-to-world affine matrix ground truth
        /// shows real SketchUp stores for a positioned texture, from
        /// exactly 3 (world point, (u, v)) correspondences.</summary>
        internal static double[] SolveUvMatrix(
            IReadOnlyList<((double X, double Y, double Z) Point, (double U, double V) Uv)> pairs,
            ((double X, double Y, double Z) U, (double X, double Y, double Z) W) basis)
        {
            if (pairs.Count != 3)
            {
                throw new SkpWriteException("texture positioning needs exactly 3 (point, uv) pairs");
            }
            var (uAxis, wAxis) = basis;
            var a = new double[3][];
            var bx = new double[3];
            var by = new double[3];
            for (int i = 0; i < 3; i++)
            {
                var (pt, uv) = pairs[i];
                a[i] = new[] { uv.U, uv.V, 1.0 };
                bx[i] = pt.X * uAxis.X + pt.Y * uAxis.Y + pt.Z * uAxis.Z;
                by[i] = pt.X * wAxis.X + pt.Y * wAxis.Y + pt.Z * wAxis.Z;
            }
            var (a0, c0, e0) = Solve3x3(a, bx);
            var (b0, d0, f0) = Solve3x3(a, by);
            return new[] { a0, b0, 0.0, c0, d0, 0.0, e0, f0, 1.0 };
        }

        internal static double[] UvMatrixForFace(
            IReadOnlyList<(double X, double Y, double Z)> points,
            IReadOnlyList<((double X, double Y, double Z) Point, (double U, double V) Uv)> pairs,
            (double X, double Y, double Z) normal)
        {
            return SolveUvMatrix(pairs, FaceUvBasis(points, normal));
        }

        private static double MaxSpan(IReadOnlyList<(double X, double Y, double Z)> points)
        {
            double minX = double.PositiveInfinity, maxX = double.NegativeInfinity;
            double minY = double.PositiveInfinity, maxY = double.NegativeInfinity;
            double minZ = double.PositiveInfinity, maxZ = double.NegativeInfinity;
            foreach (var p in points)
            {
                if (p.X < minX) minX = p.X;
                if (p.X > maxX) maxX = p.X;
                if (p.Y < minY) minY = p.Y;
                if (p.Y > maxY) maxY = p.Y;
                if (p.Z < minZ) minZ = p.Z;
                if (p.Z > maxZ) maxZ = p.Z;
            }
            return Math.Max(maxX - minX, Math.Max(maxY - minY, maxZ - minZ));
        }

        /// <summary>Newell's method: sums a cross-product-like term over
        /// every edge rather than reading the normal off just the first 3
        /// points. That first-3-points approach breaks for concave
        /// polygons whenever the first vertex happens to be a reflex
        /// corner (wrong-signed normal) - Newell's sum is the polygon's
        /// true area-weighted normal regardless of convexity, as long as
        /// it's planar and simple (non-self-intersecting). Every point is
        /// then checked to actually lie on the fitted plane (tolerance
        /// scaled to the face's own size) - a mesh built from
        /// slightly-off-plane input would otherwise silently warp instead
        /// of failing loudly.</summary>
        internal static (double Nx, double Ny, double Nz, double D) PlaneFromPolygon(IReadOnlyList<(double X, double Y, double Z)> points)
        {
            int n = points.Count;
            double nx = 0, ny = 0, nz = 0;
            for (int i = 0; i < n; i++)
            {
                var p0 = points[i];
                var p1 = points[(i + 1) % n];
                nx += (p0.Y - p1.Y) * (p0.Z + p1.Z);
                ny += (p0.Z - p1.Z) * (p0.X + p1.X);
                nz += (p0.X - p1.X) * (p0.Y + p1.Y);
            }
            double length = Math.Sqrt(nx * nx + ny * ny + nz * nz);
            if (length < 1e-9)
            {
                throw new SkpWriteException("face points are collinear or degenerate; cannot compute a plane");
            }
            nx /= length; ny /= length; nz /= length;
            double cx = 0, cy = 0, cz = 0;
            foreach (var p in points) { cx += p.X; cy += p.Y; cz += p.Z; }
            cx /= n; cy /= n; cz /= n;
            double d = nx * cx + ny * cy + nz * cz;

            double span = MaxSpan(points);
            double tol = Math.Max(span, 1.0) * 1e-6;
            foreach (var p in points)
            {
                double dist = nx * p.X + ny * p.Y + nz * p.Z - d;
                if (Math.Abs(dist) > tol)
                {
                    throw new SkpWriteException(
                        $"face points are not coplanar (point ({p.X}, {p.Y}, {p.Z}) is {Math.Abs(dist):G6} units "
                        + "off the fitted plane) - openskp only supports planar faces");
                }
            }
            return (nx, ny, nz, d);
        }

        /// <summary>Same fit/tolerance <see cref="PlaneFromPolygon"/> uses,
        /// but returns a bool for "not coplanar" instead of throwing - used
        /// by AddFace's autoTriangulate to decide whether a
        /// fan-triangulation fallback is even needed. Still throws for a
        /// collinear/degenerate input (no triangulation fixes that).</summary>
        internal static bool IsCoplanar(IReadOnlyList<(double X, double Y, double Z)> points)
        {
            int n = points.Count;
            double nx = 0, ny = 0, nz = 0;
            for (int i = 0; i < n; i++)
            {
                var p0 = points[i];
                var p1 = points[(i + 1) % n];
                nx += (p0.Y - p1.Y) * (p0.Z + p1.Z);
                ny += (p0.Z - p1.Z) * (p0.X + p1.X);
                nz += (p0.X - p1.X) * (p0.Y + p1.Y);
            }
            double length = Math.Sqrt(nx * nx + ny * ny + nz * nz);
            if (length < 1e-9)
            {
                throw new SkpWriteException("face points are collinear or degenerate; cannot compute a plane");
            }
            nx /= length; ny /= length; nz /= length;
            double cx = 0, cy = 0, cz = 0;
            foreach (var p in points) { cx += p.X; cy += p.Y; cz += p.Z; }
            cx /= n; cy /= n; cz /= n;
            double d = nx * cx + ny * cy + nz * cz;
            double span = MaxSpan(points);
            double tol = Math.Max(span, 1.0) * 1e-6;
            foreach (var p in points)
            {
                if (Math.Abs(nx * p.X + ny * p.Y + nz * p.Z - d) > tol) return false;
            }
            return true;
        }

        internal static int DetectImageSubtype(byte[] imageBytes)
        {
            if (imageBytes.Length >= 8
                && imageBytes[0] == 0x89 && imageBytes[1] == 0x50 && imageBytes[2] == 0x4E && imageBytes[3] == 0x47
                && imageBytes[4] == 0x0D && imageBytes[5] == 0x0A && imageBytes[6] == 0x1A && imageBytes[7] == 0x0A)
            {
                return 4;
            }
            if (imageBytes.Length >= 3 && imageBytes[0] == 0xFF && imageBytes[1] == 0xD8 && imageBytes[2] == 0xFF)
            {
                return 1;
            }
            throw new SkpWriteException(
                "unrecognized image format - only PNG and JPEG textures are supported for now "
                + "(detected from the file's own magic bytes, not its extension)");
        }

        /// <summary>A fresh 16-byte v4 UUID, in RFC 4122 wire (big-endian)
        /// byte order - matching Python's uuid.uuid4().bytes / real
        /// SketchUp's own GUIDs, even though the exact byte order has no
        /// effect on round-trip correctness (both this project's own
        /// reader and real SketchUp store these 16 bytes as opaque data,
        /// never parsing them structurally) - .NET's Guid.ToByteArray()
        /// stores its first three fields little-endian, so they're
        /// reversed here to match.</summary>
        internal static byte[] NewGuidBytes()
        {
            var b = Guid.NewGuid().ToByteArray();
            Array.Reverse(b, 0, 4);
            Array.Reverse(b, 4, 2);
            Array.Reverse(b, 6, 2);
            return b;
        }
    }

    /// <summary>The args <see cref="ArchiveWriter.WriteArcCurve"/> needs,
    /// bundled so <see cref="ArchiveWriter.WriteFace"/>/<see cref="ArchiveWriter.WriteArc"/>
    /// can pass them through to the first newly-declared edge's own inline
    /// curve declaration - mirrors create.py's curve_params tuple.</summary>
    internal readonly struct ArcCurveParams
    {
        public readonly (double X, double Y, double Z) Center;
        public readonly (double X, double Y, double Z) Normal;
        public readonly (double X, double Y, double Z) XAxis;
        public readonly double StartAngle;
        public readonly double EndAngle;
        public readonly double Radius;
        public readonly int NumSegments;

        public ArcCurveParams(
            (double X, double Y, double Z) center, (double X, double Y, double Z) normal, (double X, double Y, double Z) xAxis,
            double startAngle, double endAngle, double radius, int numSegments)
        {
            Center = center;
            Normal = normal;
            XAxis = xAxis;
            StartAngle = startAngle;
            EndAngle = endAngle;
            Radius = radius;
            NumSegments = numSegments;
        }
    }

    /// <summary>One (dict_name, entries) pair of custom key/value metadata -
    /// entries values may be string, int, or double, matching create.py's
    /// own (str/int/float) support.</summary>
    public readonly struct AttributeDict
    {
        public readonly string DictName;
        public readonly IReadOnlyDictionary<string, object> Entries;

        public AttributeDict(string dictName, IReadOnlyDictionary<string, object> entries)
        {
            DictName = dictName;
            Entries = entries;
        }
    }

    /// <summary>One (world point, (u, v)) texture-positioning correspondence -
    /// see <see cref="ComponentDefinitionBuilder.AddFace"/>'s frontUv/backUv
    /// parameters.</summary>
    public readonly struct UvCorrespondence
    {
        public readonly (double X, double Y, double Z) Point;
        public readonly (double U, double V) Uv;

        public UvCorrespondence((double X, double Y, double Z) point, (double U, double V) uv)
        {
            Point = point;
            Uv = uv;
        }
    }

    /// <summary>Write-side mirror of Legacy.cs's Archive slot/class-ref
    /// bookkeeping - emits the same MFC CArchive tag protocol (0xFFFF
    /// new-class, 0x8000|slot short class-ref, plain u16 back-ref) that
    /// Legacy.cs decodes, inverted for writing. Direct port of create.py's
    /// _ArchiveWriter.</summary>
    internal sealed class ArchiveWriter
    {
        internal int NextSlot;
        internal readonly Dictionary<string, int> ClassSlot;
        internal long NextPid;
        internal readonly List<byte> Buf = new List<byte>();

        internal ArchiveWriter(int nextSlot, Dictionary<string, int> classSlot, long nextPid = 1)
        {
            NextSlot = nextSlot;
            ClassSlot = new Dictionary<string, int>(classSlot);
            NextPid = nextPid;
        }

        private int Alloc()
        {
            int s = NextSlot;
            NextSlot += 1;
            return s;
        }

        private long AllocPid()
        {
            long p = NextPid;
            NextPid += 1;
            return p;
        }

        // ── low-level byte emission (explicit little-endian, matching
        // Tlv.cs's own read-side convention) ──

        internal void AddU16(ushort v)
        {
            Buf.Add((byte)(v & 0xFF));
            Buf.Add((byte)((v >> 8) & 0xFF));
        }

        internal void AddU32(uint v)
        {
            Buf.Add((byte)(v & 0xFF));
            Buf.Add((byte)((v >> 8) & 0xFF));
            Buf.Add((byte)((v >> 16) & 0xFF));
            Buf.Add((byte)((v >> 24) & 0xFF));
        }

        internal void AddI32(int v) => AddU32(unchecked((uint)v));

        internal void AddF64(double v)
        {
            long bits = BitConverter.DoubleToInt64Bits(v);
            for (int i = 0; i < 8; i++)
            {
                Buf.Add((byte)((bits >> (8 * i)) & 0xFF));
            }
        }

        internal void AddRaw(byte[] bytes) => Buf.AddRange(bytes);

        internal void AddZeros(int n)
        {
            for (int i = 0; i < n; i++) Buf.Add(0);
        }

        /// <summary>Patch a u32 already written into <see cref="Buf"/> at
        /// <paramref name="pos"/> - used for the definition entity-count
        /// field, patched only once the definition's body has finished
        /// writing (see ComponentDefinitionBuilder.Dispose).</summary>
        internal void PatchU32(int pos, uint value)
        {
            Buf[pos] = (byte)(value & 0xFF);
            Buf[pos + 1] = (byte)((value >> 8) & 0xFF);
            Buf[pos + 2] = (byte)((value >> 16) & 0xFF);
            Buf[pos + 3] = (byte)((value >> 24) & 0xFF);
        }

        internal int NewOfKnownClass(string className, int? schema = null)
        {
            if (!ClassSlot.TryGetValue(className, out int existingSlot))
            {
                if (schema == null)
                {
                    throw new SkpWriteException($"{className} not yet declared and no schema given");
                }
                AddU16(0xFFFF);
                AddU16((ushort)schema.Value);
                var nameBytes = Encoding.ASCII.GetBytes(className);
                AddU16((ushort)nameBytes.Length);
                AddRaw(nameBytes);
                ClassSlot[className] = Alloc();
                return Alloc();
            }
            // slot == 0x7FFF is deliberately excluded from the short form
            // even though it numerically fits in 15 bits: 0x8000 | 0x7FFF
            // == 0xFFFF, which Archive.ReadObject (Legacy.cs) checks for
            // "new class declaration" BEFORE it ever checks the class-ref
            // high bit - a class landing at exactly that slot would be
            // silently misinterpreted as the start of a bogus class
            // record, desyncing every read after it. The escape form has
            // no such collision.
            int slot = existingSlot;
            if (slot < 0x7FFF)
            {
                AddU16((ushort)(0x8000 | slot));
            }
            else
            {
                AddU16(0x7FFF);
                AddU32(0x80000000u | (uint)slot);
            }
            return Alloc();
        }

        internal void Null_() => AddU16(0);

        internal void Backref(int slot)
        {
            // Same exclusion as NewOfKnownClass, for the plain (no
            // class-ref bit) case: a bare slot value of 0x7FFF is
            // indistinguishable from the big-tag escape marker itself -
            // ReadObject checks tag == 0x7FFF before it ever falls through
            // to "plain object back-ref", so it would consume the next 4
            // bytes as a bogus slot number instead.
            if (slot < 0x7FFF)
            {
                AddU16((ushort)slot);
            }
            else
            {
                AddU16(0x7FFF);
                AddU32((uint)slot);
            }
        }

        private static byte[] EncodePid(long pid)
        {
            int mask = 0;
            var pidBytes = new List<byte>();
            for (int bit = 0; bit < 8; bit++)
            {
                byte byteVal = (byte)((pid >> (8 * bit)) & 0xFF);
                if (byteVal != 0)
                {
                    mask |= 1 << bit;
                    pidBytes.Add(byteVal);
                }
            }
            var result = new byte[1 + pidBytes.Count];
            result[0] = (byte)mask;
            for (int i = 0; i < pidBytes.Count; i++) result[i + 1] = pidBytes[i];
            return result;
        }

        internal void Preamble(long? pid = null, bool realAttrs = false)
        {
            if (realAttrs)
            {
                // Ground truth: CComponentDefinition and CComponentInstance
                // both reference a real (but childless) CAttributeContainer
                // here instead of the null pointer every other entity in
                // this project uses.
                AddU16((ushort)(0x8000 | CreateConstants.AttrContainerSlot));
                Alloc(); // a class-ref always allocates a new object slot, even a bookkeeping-only one
                AddZeros(3); // the container's own nested preamble: null attrs (2) + mask=0 (1)
                AddU16(0); // empty children-list terminator
            }
            else
            {
                Null_(); // no CAttributeContainer
            }
            long p = pid ?? AllocPid();
            AddRaw(EncodePid(p));
        }

        /// <summary>Like Preamble(realAttrs: true), but the attribute
        /// container's children list holds real content instead of closing
        /// immediately: an optional CFaceTextureCoords (frontMatrix/
        /// backMatrix - faces with explicit texture positioning only)
        /// followed by zero or more named CAttributeNamed dictionaries.</summary>
        internal void PreambleWithRealAttrs(
            double[]? frontMatrix = null,
            double[]? backMatrix = null,
            IReadOnlyList<AttributeDict>? attributeDicts = null,
            long? pid = null)
        {
            AddU16((ushort)(0x8000 | CreateConstants.AttrContainerSlot));
            Alloc();
            AddZeros(3);
            if (frontMatrix != null || backMatrix != null)
            {
                WriteFaceTextureCoords(frontMatrix, backMatrix);
            }
            if (attributeDicts != null)
            {
                foreach (var ad in attributeDicts)
                {
                    WriteAttributeDict(ad.DictName, ad.Entries);
                }
            }
            Null_(); // children-list terminator
            long p = pid ?? AllocPid();
            AddRaw(EncodePid(p));
        }

        internal static void ValidateAttributeEntries(IReadOnlyDictionary<string, object> entries)
        {
            foreach (var kv in entries)
            {
                var value = kv.Value;
                if (value is string) continue;
                if (value is int) continue;
                if (value is double || value is float) continue;
                throw new SkpWriteException(
                    $"attribute '{kv.Key}': unsupported value type {value?.GetType().Name ?? "null"} "
                    + "(only string, int, and double are supported for now)");
            }
        }

        /// <summary>Write one CAttributeNamed record - a named dictionary
        /// of custom key/value metadata attached to an entity's real
        /// attribute container (the same mechanism SketchUp's own "dynamic
        /// component" attributes use). Unlike every other class this
        /// project declares, CAttributeNamed is already pre-declared in
        /// the scaffold's own prefix, so this always writes a short
        /// class-ref to AttributeNamedSlot, never a fresh 0xFFFF
        /// declaration.</summary>
        internal void WriteAttributeDict(string dictName, IReadOnlyDictionary<string, object> entries)
        {
            AddU16((ushort)(0x8000 | CreateConstants.AttributeNamedSlot));
            Alloc();
            AddZeros(3); // this dict's own preamble: null attrs (2) + mask=0 (1), pid=0
            AddU32(0); // ground truth: read and discarded by the reader too
            ValidateAttributeEntries(entries);
            WriteStr(dictName);
            foreach (var kv in entries)
            {
                WriteStr(kv.Key);
                if (kv.Value is string s)
                {
                    Buf.Add(CreateConstants.AttrTypeString);
                    WriteStr(s);
                }
                else if (kv.Value is int iv)
                {
                    Buf.Add(CreateConstants.AttrTypeInt32);
                    AddI32(iv);
                }
                else
                {
                    double d = Convert.ToDouble(kv.Value);
                    Buf.Add(CreateConstants.AttrTypeDouble);
                    AddF64(d);
                }
            }
            WriteStr(""); // empty-key terminator
            AddU32(0); // ground truth: read and discarded by the reader too
        }

        /// <summary>Write one CFaceTextureCoords record - the explicit
        /// front/back texture-positioning data a face's attribute
        /// container holds when either side has been explicitly
        /// positioned. frontMatrix/backMatrix are the 9-value row-major
        /// UV-to-world affine matrices from CreateMath.UvMatrixForFace, or
        /// null for a side that isn't explicitly positioned (written as
        /// identity).</summary>
        internal void WriteFaceTextureCoords(double[]? frontMatrix, double[]? backMatrix)
        {
            NewOfKnownClass("CFaceTextureCoords", CreateConstants.FtcSchema);
            Preamble(pid: 0);
            AddU32(0); // ground truth: read and discarded by the reader too
            var ks = new double[24];
            var front = frontMatrix ?? CreateConstants.IdentityUvMatrix;
            var back = backMatrix ?? CreateConstants.IdentityUvMatrix;
            Array.Copy(front, 0, ks, 0, 9);
            Array.Copy(back, 0, ks, 12, 9);
            foreach (var v in ks) AddF64(v);
            AddU32(0); // front pin count - this writer always emits a solved matrix, never raw pins
            AddU32(0); // back pin count
            AddU32(frontMatrix != null ? 1u : 0u); // fflags bit 0: front painted/positioned
            AddU32(backMatrix != null ? 1u : 0u); // bflags bit 0: back painted/positioned
        }

        internal void Drawbase(int mat = 0, int layer = 0, bool hidden = false, bool soft = false, bool smooth = false)
        {
            var b = new byte[10];
            b[0] = (byte)(mat & 0xFF);
            b[1] = (byte)((mat >> 8) & 0xFF);
            b[2] = (byte)(hidden ? 1 : 0);
            // offsets 3-4: Legacy.cs's reader documents these as unused
            // padding, but real SketchUp silently drops any entity whose
            // drawbase has them zeroed - ground-truth-confirmed by diffing
            // real SDK-authored files. Must be 1, 1.
            b[3] = 1;
            b[4] = 1;
            b[5] = (byte)(soft ? 1 : 0);
            b[6] = (byte)(smooth ? 1 : 0);
            b[8] = (byte)(layer & 0xFF);
            b[9] = (byte)((layer >> 8) & 0xFF);
            AddRaw(b);
        }

        internal int WriteVertex((double X, double Y, double Z) point)
        {
            int slot = NewOfKnownClass("CVertex", 0);
            Preamble();
            AddF64(point.X);
            AddF64(point.Y);
            AddF64(point.Z);
            return slot;
        }

        /// <summary>Write one CArcCurve record and return its slot - the
        /// shared geometric-parameter object a circle/arc's straight CEdge
        /// segments each carry a backref to. xAxis is the arc's own fixed
        /// 0-angle reference direction (a unit vector times radius, in the
        /// plane perpendicular to normal) - startAngle/endAngle (radians)
        /// are offsets from it, not the direction to the start point
        /// itself. Two of the 14 stored values (ground truth offsets 11
        /// and 13) were 0 in every sample tested and are written as 0
        /// here too; their meaning hasn't been reverse-engineered.</summary>
        internal int WriteArcCurve(
            (double X, double Y, double Z) center, (double X, double Y, double Z) normal, (double X, double Y, double Z) xAxis,
            double startAngle, double endAngle, double radius, int numSegments)
        {
            if (numSegments < 0 || numSegments > 0xFF)
            {
                throw new SkpWriteException($"num_segments must be between 0 and 255, got {numSegments}");
            }
            int slot = NewOfKnownClass("CArcCurve", CreateConstants.ArcCurveSchema);
            Preamble();
            Buf.Add(0);
            Buf.Add((byte)numSegments);
            AddZeros(3);
            foreach (var v in new[]
            {
                center.X, center.Y, center.Z, normal.X, normal.Y, normal.Z, xAxis.X, xAxis.Y, xAxis.Z,
                startAngle, endAngle, 0.0, radius, 0.0,
            })
            {
                AddF64(v);
            }
            return slot;
        }

        /// <summary>Write one CCurve record and return its slot - a
        /// freeform polyline curve grouping (as opposed to CArcCurve's arc
        /// geometry): a labeled set of already-straight CEdge segments,
        /// with no geometric data of its own beyond how many edges share
        /// it. Ground truth: the record is just a 1-byte field - always 1
        /// in every sample tested (open or closed) - followed by numEdges
        /// as a u32.</summary>
        internal int WriteCurve(int numEdges)
        {
            int slot = NewOfKnownClass("CCurve", CreateConstants.CCurveSchema);
            Preamble();
            Buf.Add(1);
            AddU32((uint)numEdges);
            return slot;
        }

        internal void WriteStr(string s)
        {
            var encoded = Encoding.Unicode.GetBytes(s); // UTF-16LE on every .NET platform
            int n = encoded.Length / 2;
            if (n >= 0xFF)
            {
                throw new SkpWriteException("string too long to encode (255 char limit)");
            }
            Buf.Add(0xFF);
            Buf.Add(0xFE);
            Buf.Add(0xFF);
            Buf.Add((byte)n);
            AddRaw(encoded);
        }

        internal int WriteMaterial(string name, (byte R, byte G, byte B, byte A) rgba)
        {
            int slot = NewOfKnownClass("CMaterial", CreateConstants.MaterialSchema);
            Preamble();
            WriteStr(name);
            AddU16(0); // texflag: solid color, no texture
            Buf.Add(rgba.R); Buf.Add(rgba.G); Buf.Add(rgba.B); Buf.Add(rgba.A);
            WriteStr(""); // texture path (empty - no texture)
            AddZeros(8); // unknown/padding - ground truth is all-zero here
            AddF64(1.0); // opacity
            Buf.Add(0); // use_opacity = False (alpha carries transparency instead)
            return slot;
        }

        /// <summary>Write one image-textured CMaterial record (embedding
        /// imageBytes verbatim inside a CDib sub-object) and return its
        /// slot. texturePath is stored as-is. subtype is CDib's image
        /// format tag (4 for PNG, 1 for JPEG).
        ///
        /// appliedHeight, if given, is written in place of
        /// CreateConstants.TextureHSentinel (applied width stays a fixed
        /// 1.0 either way). Needed because the reader's own
        /// ground-truth-derived UV formula divides a face's final UV by
        /// the material's applied width/height EVEN for a positioned
        /// (frontUv) mapping, not just the default projection - the
        /// sentinel decodes to ~1.29e-231, and dividing by it blows up to
        /// an astronomical value, which real SketchUp visibly renders as a
        /// corrupted, vertically-smeared texture (confirmed against real
        /// SketchUp 2026-08-27 via the Python writer - see create.py's own
        /// note on this). A caller positioning this material via
        /// frontUv/backUv should pass a real appliedHeight (AddImage uses
        /// 1.0, matching its own pins' 0..1 range) so that division is a
        /// no-op instead of a corruption.</summary>
        internal int WriteTexturedMaterial(string name, byte[] imageBytes, string texturePath, int subtype, double? appliedHeight = null)
        {
            int slot = NewOfKnownClass("CMaterial", CreateConstants.MaterialSchema);
            Preamble();
            WriteStr(name);
            AddU16(1); // texflag: textured
            AddZeros(2); // texture-flag pad (v17+)
            NewOfKnownClass("CDib", CreateConstants.DibSchema);
            AddU32((uint)subtype);
            AddU32((uint)imageBytes.Length);
            AddRaw(imageBytes);
            if (subtype == 1)
            {
                // JPEG only: one extra u32 real SketchUp always writes here -
                // ground-truth confirmed constant 90 regardless of the
                // source JPEG's own actual encoded quality.
                AddU32(90);
            }
            AddF64(1.0); // applied width - ground truth default when unscaled
            if (appliedHeight.HasValue)
            {
                AddF64(appliedHeight.Value);
            }
            else
            {
                AddRaw(CreateConstants.TextureHSentinel);
            }
            WriteStr(texturePath);
            // avg color (RGBA + pad + RGBA repeated) - neutral near-opaque
            // white rather than a real image average, since this project
            // doesn't depend on an image library to compute one. Alpha is
            // 254, not a fully-opaque 255: the reader treats alpha=255 here
            // as one of its two "this material is colorized" signals - a
            // plain texture's placeholder must not trip that, or every
            // plain texture this writer creates reads back as falsely
            // colorized.
            AddRaw(new byte[] { 255, 255, 255, 254, 0, 255, 255, 255, 254 });
            WriteStr(""); // second name field - empty in ground truth
            AddU32(1); AddU32(0); // blob (colorize-related, ground truth: 1, 0)
            AddF64(1.0); // opacity
            Buf.Add(0); // use_opacity = False
            return slot;
        }

        /// <summary>Write one CLayer record and return its slot. CLayer is
        /// always already declared (the scaffold's Layer0 guarantees it),
        /// so this never emits a new-class declaration - only a short
        /// class-ref. Ground truth shows each top-level layer record
        /// contains a second, embedded pid (inside a 5-byte block after
        /// the visible name) - so each layer consumes 2 pids, not 1.
        /// withPids=false (used only for the layer a component definition
        /// embeds internally) omits both.</summary>
        internal int WriteLayer(string name, bool withPids = true, bool hidden = false, (byte R, byte G, byte B, byte A)? rgba = null)
        {
            int slot = NewOfKnownClass("CLayer", CreateConstants.LayerSchema);
            Preamble(pid: withPids ? (long?)null : 0);
            WriteStr(name);
            long pid2 = withPids ? AllocPid() : 0;
            // byte 0 is the hidden flag, bytes 1-2 are always zero (ground truth)
            Buf.Add((byte)(hidden ? 1 : 0));
            Buf.Add(0);
            Buf.Add(0);
            AddRaw(EncodePid(pid2));
            WriteStr($"Layer_{name}");
            AddU16(256); // ground truth is a constant 256 here
            if (rgba.HasValue)
            {
                var c = rgba.Value;
                Buf.Add(c.R); Buf.Add(c.G); Buf.Add(c.B); Buf.Add(c.A);
            }
            else
            {
                AddZeros(4);
            }
            WriteStr(""); // second name field - empty in ground truth
            AddZeros(8);
            AddF64(0.5); // 21-byte tail, opacity-like f64=0.5
            AddZeros(5);
            return slot;
        }

        /// <summary>Write a CThumbnail with a default camera and no image -
        /// ground truth shows the image itself is optional.</summary>
        internal void WriteThumbnail()
        {
            NewOfKnownClass("CThumbnail", CreateConstants.ThumbnailSchema);
            Preamble(pid: 0); // structural container: ground truth carries no pid
            AddU16((ushort)(0x8000 | CreateConstants.CCameraSlot));
            Alloc();
            AddRaw(CreateConstants.CameraTemplate);
            Null_(); // no thumbnail image
        }

        /// <summary>Begin a CComponentDefinition record - everything up to
        /// (not including) its internal entity list. Returns
        /// (definitionSlot, countPatchPos): the caller writes the
        /// definition's geometry via further WriteFace calls, then must
        /// patch a u32 entity count at countPatchPos and call
        /// WriteDefinitionTail to close it out.</summary>
        internal (int Slot, int CountPatchPos) WriteDefinitionHeader(IReadOnlyList<AttributeDict>? attributeDicts = null)
        {
            int slot = NewOfKnownClass("CComponentDefinition", CreateConstants.DefinitionSchema);
            if (attributeDicts != null && attributeDicts.Count > 0)
            {
                PreambleWithRealAttrs(attributeDicts: attributeDicts);
            }
            else
            {
                Preamble(realAttrs: true); // ground truth: a real pid and a real (empty) attr container
            }
            AddRaw(CreateConstants.DefinitionBaseBlock);
            AddU32(1); // nlayers: always 1, an embedded copy of Layer0
            int embeddedLayerSlot = WriteLayer("Layer0", withPids: false);
            Backref(embeddedLayerSlot); // "decl": this definition's own active layer
            // A separate field from nested instances (which live in the
            // entity list just below, like any other entity) - ground
            // truth shows this counts CComponentDefinition classes declared
            // inline within this definition's own header, a distinct and
            // rarer construct this project has not needed.
            AddU32(0);
            int countPatchPos = Buf.Count;
            AddU32(0); // placeholder entity count, patched by the caller
            return (slot, countPatchPos);
        }

        /// <summary>Close out a CComponentDefinition record: relationship
        /// count, GUID, name, timestamp, behavior flags, and a default
        /// thumbnail.</summary>
        internal void WriteDefinitionTail(string name)
        {
            AddU32(0); // nrel: CRelationship count - always 0, not supported
            AddU16(0);
            AddRaw(CreateMath.NewGuidBytes());
            WriteStr(name);
            WriteStr(""); // description - empty in ground truth
            WriteStr(""); // second name field - empty in ground truth
            AddU32((uint)DateTimeOffset.UtcNow.ToUnixTimeSeconds());
            // 43-byte gap; byte -9 carries the always-faces-camera/
            // shadows-face-sun behavior flags, both left off, matching
            // neither being exposed by this writer yet.
            AddZeros(43);
            WriteThumbnail();
        }

        private void WriteInstanceLike(
            string className, int schema, bool realAttrs,
            int definitionSlot, string name,
            (double X, double Y, double Z) translation,
            double[]? matrix3x3,
            int mat, int layer,
            IReadOnlyList<AttributeDict>? attributeDicts,
            bool hidden)
        {
            NewOfKnownClass(className, schema);
            if (realAttrs && attributeDicts != null && attributeDicts.Count > 0)
            {
                PreambleWithRealAttrs(attributeDicts: attributeDicts);
            }
            else
            {
                Preamble(realAttrs: realAttrs);
            }
            Drawbase(mat: mat, layer: layer, hidden: hidden);
            Backref(definitionSlot);
            var m = matrix3x3 ?? new double[] { 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0 };
            foreach (var v in m) AddF64(v);
            AddF64(translation.X);
            AddF64(translation.Y);
            AddF64(translation.Z);
            AddF64(1.0);
            WriteStr(name);
            AddRaw(CreateMath.NewGuidBytes());
        }

        /// <summary>Write one CComponentInstance placing a copy of
        /// definitionSlot and return how many new root-entity-list slots
        /// it consumed - always 1. matrix3x3 is a row-major 3x3
        /// rotation/scale matrix (identity if null); translation is
        /// applied after it. Ground truth shows the file's transform
        /// encoding is exactly this 3x3 matrix (9 f64s) + translation
        /// (3 f64s) + a trailing 1.0 - the 4th row of a standard 4x4
        /// affine matrix, always [0, 0, 0, 1], is omitted entirely.</summary>
        internal int WriteInstance(
            int definitionSlot, string name,
            (double X, double Y, double Z) translation = default,
            double[]? matrix3x3 = null,
            int instanceMaterial = 0, int instanceLayer = 0,
            IReadOnlyList<AttributeDict>? attributeDicts = null,
            bool hidden = false)
        {
            // ground truth: instances also carry a real (empty) attr
            // container, unlike CGroup
            WriteInstanceLike(
                "CComponentInstance", CreateConstants.InstanceSchema, true,
                definitionSlot, name, translation, matrix3x3, instanceMaterial, instanceLayer,
                attributeDicts, hidden);
            return 1;
        }

        /// <summary>Write one CGroup placing a copy of definitionSlot and
        /// return how many new root-entity-list slots it consumed - always
        /// 1, same contract as WriteInstance. A group is structurally
        /// almost identical to a component instance - the two real
        /// differences are its class name/schema (CGroup, schema 1) and
        /// that it uses a plain null attribute pointer rather than the
        /// real (empty) CAttributeContainer instances need.</summary>
        internal int WriteGroup(
            int definitionSlot, string name,
            (double X, double Y, double Z) translation = default,
            double[]? matrix3x3 = null,
            int groupMaterial = 0, int groupLayer = 0,
            bool hidden = false)
        {
            WriteInstanceLike(
                "CGroup", CreateConstants.GroupSchema, false,
                definitionSlot, name, translation, matrix3x3, groupMaterial, groupLayer,
                null, hidden);
            return 1;
        }

        /// <summary>Write one CImage placing definitionSlot (the quad +
        /// texture material AddImage built for it) - return contract
        /// matches WriteInstance/WriteGroup (always 1).
        ///
        /// Legacy.cs's image reader treats CImage as "instance-shaped":
        /// preamble, drawbase, a definition back-ref, a 3x4 placement, a
        /// constant 1.0, a source-path string, and a 16-byte GUID -
        /// field-for-field identical in count and order to WriteInstance's
        /// own matrix3x3(9)+translation(3)+1.0(1)=13 f64s, name string,
        /// GUID. The source-path string is always empty - ground truth
        /// shows real SketchUp writes it empty too. No material argument -
        /// an Image entity isn't painted a material the way a face or
        /// instance can be; its appearance comes entirely from the
        /// definition's own textured face.</summary>
        internal int WriteImage(
            int definitionSlot,
            (double X, double Y, double Z) translation = default,
            double[]? matrix3x3 = null,
            int imageLayer = 0,
            bool hidden = false)
        {
            WriteInstanceLike(
                "CImage", CreateConstants.ImageSchema, false,
                definitionSlot, "", translation, matrix3x3, 0, imageLayer,
                null, hidden);
            return 1;
        }

        private static (int, int) EdgeKeyOf(int a, int b) => a < b ? (a, b) : (b, a);

        /// <summary>Write a chain of straight CEdge records connecting
        /// points in order, sharing vertices/edges via vertexSlots/
        /// edgeRegistry exactly like WriteFace (which uses this for its
        /// own, always-closed polygon boundary) - closed=true also
        /// connects the last point back to the first; closed=false stops
        /// after the last pair. At most one of curveParams/
        /// polylineNumEdges should be given - both describe the SAME
        /// first-use-inline-declaration pattern (the shared curve object
        /// is declared inline as the FIRST newly-declared edge's own
        /// "curve" field, and every other edge newly declared by this call
        /// backrefs that same slot instead of writing a null curve).</summary>
        internal (List<int> EdgeSlots, List<int> EdgeSenses, int NewEntities) WriteEdgeChain(
            IReadOnlyList<(double X, double Y, double Z)> points,
            Dictionary<(double, double, double), int> vertexSlots,
            Dictionary<(int, int), (int EdgeSlot, int FwdV1)> edgeRegistry,
            bool closed,
            bool hiddenEdges = false, bool softEdges = false, bool smoothEdges = false,
            ArcCurveParams? curveParams = null,
            int? polylineNumEdges = null)
        {
            int n = points.Count;
            int pairCount = closed ? n : n - 1;
            var pointSlots = new int?[n];
            for (int i = 0; i < n; i++)
            {
                pointSlots[i] = vertexSlots.TryGetValue(points[i], out int s) ? s : (int?)null;
            }

            var edgeSlots = new List<int>();
            var edgeSenses = new List<int>();
            int newEntities = 0;
            int? curveSlot = null;

            for (int i = 0; i < pairCount; i++)
            {
                int v1Idx = i, v2Idx = (i + 1) % n;
                int? v1Known = pointSlots[v1Idx], v2Known = pointSlots[v2Idx];
                (int, int)? key = (v1Known.HasValue && v2Known.HasValue) ? EdgeKeyOf(v1Known.Value, v2Known.Value) : ((int, int)?)null;
                if (key.HasValue && edgeRegistry.TryGetValue(key.Value, out var existing))
                {
                    edgeSlots.Add(existing.EdgeSlot);
                    edgeSenses.Add(existing.FwdV1 == v1Known ? 0 : 1);
                    continue;
                }

                int edgeSlot = NewOfKnownClass("CEdge", 2);
                Preamble();
                Drawbase(hidden: hiddenEdges, soft: softEdges, smooth: smoothEdges);
                foreach (int idx in new[] { v1Idx, v2Idx })
                {
                    if (pointSlots[idx] == null)
                    {
                        int vs = WriteVertex(points[idx]);
                        pointSlots[idx] = vs;
                        vertexSlots[points[idx]] = vs;
                    }
                    else
                    {
                        Backref(pointSlots[idx]!.Value);
                    }
                }
                if (curveSlot != null)
                {
                    Backref(curveSlot.Value);
                }
                else if (curveParams != null)
                {
                    var cp = curveParams.Value;
                    curveSlot = WriteArcCurve(cp.Center, cp.Normal, cp.XAxis, cp.StartAngle, cp.EndAngle, cp.Radius, cp.NumSegments);
                }
                else if (polylineNumEdges.HasValue)
                {
                    curveSlot = WriteCurve(polylineNumEdges.Value);
                }
                else
                {
                    Null_(); // curve = null
                }
                edgeSlots.Add(edgeSlot);
                edgeSenses.Add(0);
                newEntities++;
                edgeRegistry[EdgeKeyOf(pointSlots[v1Idx]!.Value, pointSlots[v2Idx]!.Value)] = (edgeSlot, pointSlots[v1Idx]!.Value);
            }

            return (edgeSlots, edgeSenses, newEntities);
        }

        /// <summary>Write a partial (open) arc as a chain of straight
        /// CEdge records - no face, unlike WriteFace's always-closed
        /// polygon boundary. Returns how many new root-entity-list slots
        /// were consumed.</summary>
        internal int WriteArc(
            IReadOnlyList<(double X, double Y, double Z)> points,
            Dictionary<(double, double, double), int> vertexSlots,
            Dictionary<(int, int), (int, int)> edgeRegistry,
            ArcCurveParams curveParams,
            bool hiddenEdges = false, bool softEdges = false, bool smoothEdges = false)
        {
            var (_, _, newEntities) = WriteEdgeChain(points, vertexSlots, edgeRegistry, false, hiddenEdges, softEdges, smoothEdges, curveParams, null);
            return newEntities;
        }

        /// <summary>Write a freeform polyline curve - a chain of straight
        /// CEdge records connecting points in order, all sharing one
        /// CCurve grouping, no face. closed=true additionally connects the
        /// last point back to the first.</summary>
        internal int WritePolyline(
            IReadOnlyList<(double X, double Y, double Z)> points,
            Dictionary<(double, double, double), int> vertexSlots,
            Dictionary<(int, int), (int, int)> edgeRegistry,
            bool closed = false, bool hiddenEdges = false, bool softEdges = false, bool smoothEdges = false)
        {
            int n = points.Count;
            int pairCount = closed ? n : n - 1;
            var (_, _, newEntities) = WriteEdgeChain(points, vertexSlots, edgeRegistry, closed, hiddenEdges, softEdges, smoothEdges, null, pairCount);
            return newEntities;
        }

        /// <summary>Write one planar face and return how many new
        /// root-entity-list slots it consumed (edges newly declared, plus
        /// the face itself). points form a closed polygon in order (do
        /// not repeat the first point at the end). Vertices and edges are
        /// shared automatically across calls via vertexSlots/edgeRegistry
        /// wherever coordinates coincide exactly. holes, if given, is a
        /// sequence of point lists - each an independent closed polygon
        /// cut out of the face; ground truth shows a hole is just another
        /// CLoop in the face's own nloops list, with its own independent
        /// edges - the ONLY difference from the outer boundary loop is its
        /// first flag byte (0 instead of 1).</summary>
        internal int WriteFace(
            IReadOnlyList<(double X, double Y, double Z)> points,
            Dictionary<(double, double, double), int> vertexSlots,
            Dictionary<(int, int), (int, int)> edgeRegistry,
            int faceMaterial = 0, int faceLayer = 0, int backMaterial = 0,
            bool hidden = false, bool softEdges = false, bool smoothEdges = false, bool hiddenEdges = false,
            IReadOnlyList<UvCorrespondence>? frontUv = null,
            IReadOnlyList<UvCorrespondence>? backUv = null,
            IReadOnlyList<AttributeDict>? attributeDicts = null,
            ArcCurveParams? curveParams = null,
            IReadOnlyList<IReadOnlyList<(double X, double Y, double Z)>>? holes = null)
        {
            holes ??= Array.Empty<IReadOnlyList<(double, double, double)>>();
            // Validate everything that CAN fail (a degenerate UV
            // correspondence, an unsupported attribute value) before
            // writing a single byte or touching vertexSlots/edgeRegistry
            // below - WriteEdgeChain mutates both this writer's own buffer
            // AND those caller-owned, shared-across-calls dicts as it
            // goes, with no rollback if something later in this method
            // throws; a caller that catches the exception and tries to
            // keep building (e.g. skipping one bad face while replaying
            // many, see Edit.cs) would otherwise be left with orphaned,
            // uncounted edges silently corrupting the rest of the file.
            var (nx, ny, nz, d) = CreateMath.PlaneFromPolygon(points);
            double[]? frontMatrix = frontUv != null ? CreateMath.UvMatrixForFace(points, ToPairs(frontUv), (nx, ny, nz)) : null;
            double[]? backMatrix = backUv != null ? CreateMath.UvMatrixForFace(points, ToPairs(backUv), (nx, ny, nz)) : null;
            if (attributeDicts != null)
            {
                foreach (var ad in attributeDicts) ValidateAttributeEntries(ad.Entries);
            }
            if (holes.Count > 0)
            {
                double span = 0;
                foreach (var axis in new Func<(double X, double Y, double Z), double>[] { p => p.X, p => p.Y, p => p.Z })
                {
                    double min = double.PositiveInfinity, max = double.NegativeInfinity;
                    foreach (var p in points) { double v = axis(p); if (v < min) min = v; if (v > max) max = v; }
                    span = Math.Max(span, max - min);
                }
                double tol = Math.Max(span, 1.0) * 1e-6;
                foreach (var hole in holes)
                {
                    if (hole.Count < 3)
                    {
                        throw new SkpWriteException("a hole needs at least 3 points");
                    }
                    foreach (var p in hole)
                    {
                        double dist = nx * p.X + ny * p.Y + nz * p.Z - d;
                        if (Math.Abs(dist) > tol)
                        {
                            throw new SkpWriteException(
                                $"hole point ({p.X}, {p.Y}, {p.Z}) is {Math.Abs(dist):G6} units off the face's own "
                                + "plane - a hole must lie on the same plane as the outer boundary");
                        }
                    }
                }
            }

            var (edgeSlots, edgeSenses, newEntitiesStart) = WriteEdgeChain(points, vertexSlots, edgeRegistry, true, hiddenEdges, softEdges, smoothEdges, curveParams);
            int newEntities = newEntitiesStart;
            var holeLoops = new List<(List<int> EdgeSlots, List<int> EdgeSenses)>();
            foreach (var hole in holes)
            {
                var (hEdgeSlots, hEdgeSenses, hNew) = WriteEdgeChain(hole, vertexSlots, edgeRegistry, true, hiddenEdges, softEdges, smoothEdges, null);
                holeLoops.Add((hEdgeSlots, hEdgeSenses));
                newEntities += hNew;
            }

            NewOfKnownClass("CFace", 3);
            if (frontUv != null || backUv != null || (attributeDicts != null && attributeDicts.Count > 0))
            {
                PreambleWithRealAttrs(frontMatrix, backMatrix, attributeDicts);
            }
            else
            {
                Preamble();
            }
            Drawbase(mat: faceMaterial, layer: faceLayer, hidden: hidden);
            AddF64(nx); AddF64(ny); AddF64(nz); AddF64(d);
            AddU32((uint)(1 + holes.Count)); // nloops

            int loopSlot = NewOfKnownClass("CLoop", 1);
            Preamble(pid: 0); // structural object: ground truth uses pid 0
            // Legacy.cs's reader treats these 2 bytes as opaque, but real
            // SketchUp requires 01 01, not 00 00 - same silent-drop failure
            // mode as the drawbase padding above.
            Buf.Add(1); Buf.Add(1);
            for (int i = 0; i < edgeSlots.Count; i++)
            {
                NewOfKnownClass("CEdgeUse", 1);
                Preamble(pid: 0);
                Backref(edgeSlots[i]);
                Buf.Add((byte)edgeSenses[i]);
                Backref(loopSlot);
            }
            Null_(); // loop terminator

            foreach (var (hEdgeSlots, hEdgeSenses) in holeLoops)
            {
                int hLoopSlot = NewOfKnownClass("CLoop", 1);
                Preamble(pid: 0);
                Buf.Add(0); Buf.Add(1); // ground truth: 0 marks a hole loop, not the boundary
                for (int i = 0; i < hEdgeSlots.Count; i++)
                {
                    NewOfKnownClass("CEdgeUse", 1);
                    Preamble(pid: 0);
                    Backref(hEdgeSlots[i]);
                    Buf.Add((byte)hEdgeSenses[i]);
                    Backref(hLoopSlot);
                }
                Null_();
            }

            AddU16((ushort)backMaterial);
            newEntities += 1; // the face itself
            return newEntities;
        }

        private static IReadOnlyList<((double, double, double), (double, double))> ToPairs(IReadOnlyList<UvCorrespondence> uv)
        {
            var list = new List<((double, double, double), (double, double))>(uv.Count);
            foreach (var c in uv) list.Add((c.Point, c.Uv));
            return list;
        }
    }

    /// <summary>The AddFace/AddInstance shape SkpBuilder and
    /// ComponentDefinitionBuilder both expose identically - lets Edit.cs's
    /// replay logic write one generic _ReplayBody-style helper that works
    /// against either the root builder or a nested definition builder,
    /// mirroring how edit.py's own _replay_body accepts either a Python
    /// SkpBuilder or ComponentDefinitionBuilder as its untyped `target`.</summary>
    internal interface IGeometryTarget
    {
        void AddFace(
            IReadOnlyList<(double X, double Y, double Z)> points,
            int? material = null, int? layer = null, int? backMaterial = null,
            bool hidden = false, bool softEdges = false, bool smoothEdges = false, bool hiddenEdges = false,
            IReadOnlyList<UvCorrespondence>? frontUv = null, IReadOnlyList<UvCorrespondence>? backUv = null,
            IReadOnlyDictionary<string, object>? attributes = null, string attributeDictName = "attributes",
            bool autoTriangulate = false,
            IReadOnlyList<IReadOnlyList<(double X, double Y, double Z)>>? holes = null);

        void AddInstance(
            ComponentDefinitionBuilder definition, string? name = null,
            (double X, double Y, double Z) translation = default,
            double[]? matrix3x3 = null,
            ((double X, double Y, double Z) Axis, double AngleRadians)? rotation = null,
            int? material = null, int? layer = null,
            IReadOnlyDictionary<string, object>? attributes = null, string attributeDictName = "attributes",
            bool hidden = false);
    }

    /// <summary>Shared by SkpBuilder.AddFace and
    /// ComponentDefinitionBuilder.AddFace - writes points as one face
    /// normally, unless autoTriangulate is set AND the points aren't
    /// coplanar, in which case it fan-triangulates from points[0] and
    /// writes one real, always-planar triangular face per fan wedge
    /// instead of throwing. This mirrors real SketchUp's own UI behavior:
    /// a 4-point face you draw that isn't quite flat is silently split
    /// into 2 triangles rather than rejected. Not attempted for a
    /// genuinely degenerate (collinear) input, or when holes is given.
    /// Not compatible with frontUv/backUv: positioning a texture from one
    /// 3-point correspondence doesn't generalize to a fan of independently
    /// drawn triangles.</summary>
    internal static class FaceWriting
    {
        internal static int WriteFaceOrTriangulate(
            ArchiveWriter writer,
            IReadOnlyList<(double X, double Y, double Z)> points,
            Dictionary<(double, double, double), int> vertexSlots,
            Dictionary<(int, int), (int, int)> edgeRegistry,
            int material, int layer, int backMaterial,
            bool hidden, bool softEdges, bool smoothEdges, bool hiddenEdges,
            IReadOnlyList<UvCorrespondence>? frontUv, IReadOnlyList<UvCorrespondence>? backUv,
            IReadOnlyList<AttributeDict>? attributeDicts,
            bool autoTriangulate,
            IReadOnlyList<IReadOnlyList<(double X, double Y, double Z)>>? holes = null)
        {
            bool hasHoles = holes != null && holes.Count > 0;
            if (hasHoles || !autoTriangulate || points.Count == 3 || CreateMath.IsCoplanar(points))
            {
                return writer.WriteFace(
                    points, vertexSlots, edgeRegistry,
                    material, layer, backMaterial,
                    hidden, softEdges, smoothEdges, hiddenEdges,
                    frontUv, backUv, attributeDicts,
                    holes: holes);
            }
            if (frontUv != null || backUv != null)
            {
                throw new SkpWriteException("auto_triangulate cannot be combined with front_uv/back_uv positioning");
            }
            int total = 0;
            for (int i = 1; i < points.Count - 1; i++)
            {
                total += writer.WriteFace(
                    new[] { points[0], points[i], points[i + 1] }, vertexSlots, edgeRegistry,
                    material, layer, backMaterial,
                    hidden, softEdges, smoothEdges, hiddenEdges,
                    null, null, attributeDicts);
            }
            return total;
        }
    }

    /// <summary>Accumulates one component/group definition's geometry.
    /// Construct via <see cref="SkpBuilder.AddComponentDefinition"/> or
    /// <see cref="SkpBuilder.AddGroup"/>, not directly - use it with a
    /// <c>using</c> block. A component definition needs a separate
    /// <see cref="SkpBuilder.AddInstance"/> call per placement; a group
    /// places itself automatically when Dispose runs.
    ///
    /// <code>
    /// using (var chair = builder.AddComponentDefinition("Chair"))
    /// {
    ///     chair.AddFace(new (double,double,double)[] { (0,0,0), (20,0,0), (20,20,0), (0,20,0) });
    /// }
    /// builder.AddInstance(chair, translation: (100, 0, 0));
    /// </code>
    ///
    /// Note: unlike Python's context manager, Dispose() always attempts to
    /// finalize this definition (patch the entity count, write the tail,
    /// flush a pending group placement) even if the body of the using
    /// block itself threw - a narrow, documented divergence from
    /// create.py's own __exit__, which deliberately does nothing when an
    /// exception is already propagating (there is no fully portable,
    /// reliable way to detect "Dispose is running during exception
    /// unwinding" from inside Dispose itself in .NET). In the rare case
    /// where the block's own exception happened before any geometry was
    /// added, Dispose() will throw a new "no geometry" SkpWriteException
    /// that may replace the original exception in the propagation - if
    /// this matters for your use case, catch and handle within the using
    /// block instead of letting an exception escape it.
    /// </summary>
    public sealed class ComponentDefinitionBuilder : IDisposable, IGeometryTarget
    {
        internal readonly SkpBuilder Skp;
        internal readonly int Slot;
        public string Name { get; }
        private readonly int _countPatchPos;
        private readonly Dictionary<(double, double, double), int> _vertexSlots = new Dictionary<(double, double, double), int>();
        private readonly Dictionary<(int, int), (int, int)> _edgeRegistry = new Dictionary<(int, int), (int, int)>();
        private int _newEntityCount;
        private bool _closed;
        internal bool Closed => _closed;

        // Set only by SkpBuilder.AddGroup - a group places itself
        // immediately on close, unlike a plain component definition, which
        // needs an explicit later AddInstance call.
        private readonly GroupPlacement? _groupPlacement;

        internal ComponentDefinitionBuilder(SkpBuilder skp, int slot, string name, int countPatchPos, GroupPlacement? groupPlacement)
        {
            Skp = skp;
            Slot = slot;
            Name = name;
            _countPatchPos = countPatchPos;
            _groupPlacement = groupPlacement;
        }

        private void CheckWritable(string action)
        {
            if (_closed)
            {
                throw new SkpWriteException(
                    $"component definition '{Name}' has already closed (its using block exited) - cannot add more {action} to it");
            }
        }

        /// <summary>Add one planar face to this definition - same
        /// behavior as <see cref="SkpBuilder.AddFace"/>, except
        /// vertices/edges are shared only within this definition, never
        /// with the root model or other definitions.</summary>
        public void AddFace(
            IReadOnlyList<(double X, double Y, double Z)> points,
            int? material = null, int? layer = null, int? backMaterial = null,
            bool hidden = false, bool softEdges = false, bool smoothEdges = false, bool hiddenEdges = false,
            IReadOnlyList<UvCorrespondence>? frontUv = null, IReadOnlyList<UvCorrespondence>? backUv = null,
            IReadOnlyDictionary<string, object>? attributes = null, string attributeDictName = "attributes",
            bool autoTriangulate = false,
            IReadOnlyList<IReadOnlyList<(double X, double Y, double Z)>>? holes = null)
        {
            CheckWritable("faces");
            if (points.Count < 3)
            {
                throw new SkpWriteException("a face needs at least 3 points");
            }
            var attributeDicts = BuildAttributeDicts(attributes, attributeDictName);
            _newEntityCount += FaceWriting.WriteFaceOrTriangulate(
                Skp.DefinitionWriter!, points, _vertexSlots, _edgeRegistry,
                material ?? 0, layer ?? 0, backMaterial ?? 0,
                hidden, softEdges, smoothEdges, hiddenEdges,
                frontUv, backUv, attributeDicts, autoTriangulate,
                holes: holes);
        }

        /// <summary>Add one circular face to this definition - same
        /// behavior as <see cref="SkpBuilder.AddCircle"/>, except
        /// vertices/edges are shared only within this definition.</summary>
        public void AddCircle(
            (double X, double Y, double Z) center, (double X, double Y, double Z) normal, double radius,
            int numSegments = 24,
            int? material = null, int? layer = null, int? backMaterial = null,
            bool hidden = false,
            IReadOnlyList<UvCorrespondence>? frontUv = null, IReadOnlyList<UvCorrespondence>? backUv = null,
            IReadOnlyDictionary<string, object>? attributes = null, string attributeDictName = "attributes")
        {
            CheckWritable("faces");
            if (numSegments < 3 || numSegments > 255)
            {
                throw new SkpWriteException($"num_segments must be between 3 and 255, got {numSegments}");
            }
            normal = CreateMath.Normalize3(normal);
            var writer = Skp.DefinitionWriter!;
            var (u, w) = CreateMath.CircleBasis(normal);
            var xAxis = (radius * u.X, radius * u.Y, radius * u.Z);
            var curveParams = new ArcCurveParams(center, normal, xAxis, 0.0, 2.0 * Math.PI, radius, numSegments);
            var points = CreateMath.CirclePoints(center, radius, numSegments, u, w);
            var attributeDicts = BuildAttributeDicts(attributes, attributeDictName);
            _newEntityCount += writer.WriteFace(
                points, _vertexSlots, _edgeRegistry,
                material ?? 0, layer ?? 0, backMaterial ?? 0,
                hidden, false, false, false,
                frontUv, backUv, attributeDicts,
                curveParams: curveParams);
        }

        /// <summary>Add one partial (open) arc to this definition - same
        /// behavior as <see cref="SkpBuilder.AddArc"/>, except
        /// vertices/edges are shared only within this definition.</summary>
        public void AddArc(
            (double X, double Y, double Z) center, (double X, double Y, double Z) normal, double radius,
            double startAngle, double endAngle,
            int numSegments = 24,
            bool hiddenEdges = false, bool softEdges = false, bool smoothEdges = false)
        {
            CheckWritable("arcs");
            if (numSegments < 3 || numSegments > 255)
            {
                throw new SkpWriteException($"num_segments must be between 3 and 255, got {numSegments}");
            }
            if (endAngle == startAngle)
            {
                throw new SkpWriteException("start_angle and end_angle must differ - use AddCircle for a full circle");
            }
            normal = CreateMath.Normalize3(normal);
            var writer = Skp.DefinitionWriter!;
            var (u, w) = CreateMath.CircleBasis(normal);
            var xAxis = (radius * u.X, radius * u.Y, radius * u.Z);
            var curveParams = new ArcCurveParams(center, normal, xAxis, startAngle, endAngle, radius, numSegments);
            var points = CreateMath.ArcPoints(center, radius, numSegments, u, w, startAngle, endAngle);
            _newEntityCount += writer.WriteArc(points, _vertexSlots, _edgeRegistry, curveParams, hiddenEdges, softEdges, smoothEdges);
        }

        /// <summary>Add one freeform polyline curve to this definition -
        /// same behavior as <see cref="SkpBuilder.AddPolyline"/>, except
        /// vertices/edges are shared only within this definition.</summary>
        public void AddPolyline(
            IReadOnlyList<(double X, double Y, double Z)> points,
            bool closed = false, bool hiddenEdges = false, bool softEdges = false, bool smoothEdges = false)
        {
            CheckWritable("polylines");
            if (points.Count < 2)
            {
                throw new SkpWriteException("a polyline needs at least 2 points");
            }
            _newEntityCount += Skp.DefinitionWriter!.WritePolyline(points, _vertexSlots, _edgeRegistry, closed, hiddenEdges, softEdges, smoothEdges);
        }

        /// <summary>Place one instance of another, already-closed
        /// component definition inside this one - the same nesting real
        /// SketchUp supports. definition must come from this same
        /// builder, and must already be closed - only one definition can
        /// be open on a given builder at once, and that one is always
        /// `this` while its own using block is active, so any OTHER
        /// definition from this builder reachable here was necessarily
        /// closed before `this` was even opened - that ordering is also
        /// what rules out cycles.</summary>
        public void AddInstance(
            ComponentDefinitionBuilder definition, string? name = null,
            (double X, double Y, double Z) translation = default,
            double[]? matrix3x3 = null,
            ((double X, double Y, double Z) Axis, double AngleRadians)? rotation = null,
            int? material = null, int? layer = null,
            IReadOnlyDictionary<string, object>? attributes = null, string attributeDictName = "attributes",
            bool hidden = false)
        {
            CheckWritable("instances");
            if (!ReferenceEquals(definition.Skp, Skp))
            {
                throw new SkpWriteException(
                    $"component definition '{definition.Name}' belongs to a different builder (a different NewFile() call) - its slot number is meaningless here");
            }
            if (ReferenceEquals(definition, this))
            {
                throw new SkpWriteException($"component definition '{Name}' cannot nest an instance of itself");
            }
            var resolved = CreateMath.ResolveMatrix3x3(matrix3x3, rotation);
            var attributeDicts = BuildAttributeDicts(attributes, attributeDictName);
            _newEntityCount += Skp.DefinitionWriter!.WriteInstance(
                definition.Slot, name ?? definition.Name, translation, resolved, material ?? 0, layer ?? 0,
                attributeDicts, hidden);
        }

        /// <summary>Place another, already-closed component definition
        /// inside this one as a *group* (CGroup) rather than a component
        /// instance - otherwise identical to <see cref="AddInstance"/>,
        /// including the same already-closed/same-builder/no-self-
        /// reference requirements. Unlike the self-placing
        /// <see cref="SkpBuilder.AddGroup"/> at the root level, a nested
        /// group can't be declared inline: this format has no way to
        /// embed one definition's declaration inside another's - so build
        /// the group's geometry with a normal AddComponentDefinition
        /// first, then place it here.</summary>
        public void AddGroupInstance(
            ComponentDefinitionBuilder definition, string? name = null,
            (double X, double Y, double Z) translation = default,
            double[]? matrix3x3 = null,
            ((double X, double Y, double Z) Axis, double AngleRadians)? rotation = null,
            int? material = null, int? layer = null,
            bool hidden = false)
        {
            CheckWritable("groups");
            if (!ReferenceEquals(definition.Skp, Skp))
            {
                throw new SkpWriteException(
                    $"component definition '{definition.Name}' belongs to a different builder (a different NewFile() call) - its slot number is meaningless here");
            }
            if (ReferenceEquals(definition, this))
            {
                throw new SkpWriteException($"component definition '{Name}' cannot nest a group instance of itself");
            }
            var resolved = CreateMath.ResolveMatrix3x3(matrix3x3, rotation);
            _newEntityCount += Skp.DefinitionWriter!.WriteGroup(definition.Slot, name ?? definition.Name, translation, resolved, material ?? 0, layer ?? 0, hidden);
        }

        private static List<AttributeDict>? BuildAttributeDicts(IReadOnlyDictionary<string, object>? attributes, string dictName)
        {
            if (attributes == null || attributes.Count == 0) return null;
            return new List<AttributeDict> { new AttributeDict(dictName, attributes) };
        }

        public void Dispose()
        {
            if (_closed) return;
            if (_newEntityCount == 0)
            {
                throw new SkpWriteException($"component definition '{Name}' has no geometry - add at least one face");
            }
            var writer = Skp.DefinitionWriter!;
            writer.PatchU32(_countPatchPos, (uint)_newEntityCount);
            writer.WriteDefinitionTail(Name);
            _closed = true;
            Skp.ClearOpenDefinition(this);
            if (_groupPlacement.HasValue)
            {
                // Deferred rather than written here: writing immediately
                // would call EnsureGeometryWriter() and lock in root-level
                // slot numbering right away, which would wrongly reject
                // any further AddGroup/AddComponentDefinition call after
                // this one - group placements are flushed together the
                // first time anything actually needs the geometry writer.
                Skp.EnqueuePendingGroup(this, _groupPlacement.Value);
            }
        }
    }

    /// <summary>(translation, matrix3x3, material, layer, hidden) for a
    /// group's deferred self-placement - mirrors create.py's
    /// group_placement tuple.</summary>
    internal readonly struct GroupPlacement
    {
        public readonly (double X, double Y, double Z) Translation;
        public readonly double[]? Matrix3x3;
        public readonly int Material;
        public readonly int Layer;
        public readonly bool Hidden;

        public GroupPlacement((double X, double Y, double Z) translation, double[]? matrix3x3, int material, int layer, bool hidden)
        {
            Translation = translation;
            Matrix3x3 = matrix3x3;
            Material = material;
            Layer = layer;
            Hidden = hidden;
        }
    }

    /// <summary>Accumulates geometry and writes it into a new legacy-format
    /// (v17) .skp file. Construct via <see cref="SkpCreate.NewFile"/>, not
    /// directly. Direct port of create.py's SkpBuilder - see this file's
    /// own header comment (and create.py's module docstring) for the full
    /// splicing strategy this constructor and <see cref="ToBytes"/>
    /// implement.</summary>
    public sealed class SkpBuilder : IGeometryTarget
    {
        private readonly byte[] _data;
        private readonly int _materialInsertPos;
        private readonly int _base;
        private readonly int _layerCountPos;
        private readonly int _origLayerCount;
        private readonly int _layerInsertPos;
        private readonly int _defCountPos;
        private readonly int _origDefCount;
        private readonly int _rootCountPos;
        private readonly int _origRootCount;
        private readonly int _tailPos;

        // The scaffold-derived starting slot for anything written AFTER the
        // (always byte-for-byte-copied) layer/definition/root-entity
        // region - i.e. where geometry's own new slots would start if zero
        // materials or layers are added. Materials splice in before the
        // layer list and layers splice in right after the existing ones,
        // so every slot from here on shifts by however many slots each
        // section ends up consuming.
        private readonly int _scaffoldNextSlot;
        private readonly Dictionary<string, int> _scaffoldClassSlot;

        // Materials always start allocating at `_base`, the same slot the
        // (possibly absent) material section would have occupied.
        private readonly ArchiveWriter _materialWriter;

        /// <summary>Every material registered so far, by name - populated
        /// by AddMaterial/AddTextureMaterial as a side effect (they
        /// already de-dupe by name through this same dictionary), not
        /// something a caller needs to maintain separately.</summary>
        public Dictionary<string, int> MaterialsByName { get; } = new Dictionary<string, int>();
        private int _materialCount;

        // Deferred: layers splice in AFTER materials, so the layer
        // writer's starting slot depends on the final material count.
        // Constructed lazily on the first AddLayer() call.
        private readonly int _layerWriterBase;
        private ArchiveWriter? _layerWriter;
        private int _layerWriterStart;

        /// <summary>Every layer registered so far, by name - same pattern
        /// as MaterialsByName, populated automatically by AddLayer.</summary>
        public Dictionary<string, int> LayersByName { get; } = new Dictionary<string, int>();
        private int _layerCount;

        // Deferred the same way as the layer writer: component
        // definitions splice in after layers, before root-level geometry,
        // so their starting slot depends on the final material+layer
        // shift.
        internal ArchiveWriter? DefinitionWriter;
        private int _definitionWriterStart;
        private int _definitionCount;
        private ComponentDefinitionBuilder? _openDefinition;
        private readonly List<(ComponentDefinitionBuilder Comp, GroupPlacement Placement)> _pendingGroups =
            new List<(ComponentDefinitionBuilder, GroupPlacement)>();

        private ArchiveWriter? _geometryWriter;
        private readonly Dictionary<(double, double, double), int> _vertexSlots = new Dictionary<(double, double, double), int>();
        private readonly Dictionary<(int, int), (int, int)> _edgeRegistry = new Dictionary<(int, int), (int, int)>();
        private int _newEntityCount;
        private int _faceCount;

        private static readonly int?[] ClayerPattern = BuildClayerPattern();

        private static int?[] BuildClayerPattern()
        {
            var prefix = new int?[] { 0xFF, 0xFF, null, null, 0x06, 0x00 };
            var name = Encoding.ASCII.GetBytes("CLayer").Select(b => (int?)b);
            return prefix.Concat(name).ToArray();
        }

        private static byte[] LoadScaffold()
        {
            var asm = Assembly.GetExecutingAssembly();
            using var stream = asm.GetManifestResourceStream(CreateConstants.ScaffoldResourceName);
            if (stream == null)
            {
                throw new SkpWriteException(
                    $"bundled blank-document scaffold resource '{CreateConstants.ScaffoldResourceName}' is missing from the assembly");
            }
            byte[] data;
            using (var ms = new System.IO.MemoryStream())
            {
                stream.CopyTo(ms);
                data = ms.ToArray();
            }
            string digest;
            using (var sha = SHA256.Create())
            {
                digest = string.Concat(sha.ComputeHash(data).Select(b => b.ToString("x2")));
            }
            if (digest != CreateConstants.ScaffoldSha256)
            {
                throw new SkpWriteException(
                    "bundled blank-document scaffold does not match the expected content (hash mismatch) - "
                    + "Create.cs's tail-reference offsets are specific to the original scaffold file and would "
                    + "silently corrupt output against a different one");
            }
            return data;
        }

        internal SkpBuilder()
        {
            byte[] data = LoadScaffold();
            int matchStart = LegacyBytes.FindPattern(data, ClayerPattern);
            if (matchStart < 0)
            {
                throw new SkpWriteException("scaffold is missing its CLayer class record");
            }
            int start = matchStart - 9;
            int @base = Legacy.ProbeLayerAnchorBases(data, 17, start, 0)[0];

            var ar = new Archive(data, 17);
            foreach (var kv in LegacyReaders.Readers) ar.Readers[kv.Key] = kv.Value;
            ar.NextSlot = @base;
            ar.WalkBase = @base;
            var r = ar.R;
            r.Pos = start;
            r.U32();
            r.U8();
            int layerCountPos = r.Pos;
            int origLayerCount = (int)r.U32();
            for (int i = 0; i < origLayerCount; i++) ar.ReadObject(r, "CLayer");
            int layerInsertPos = r.Pos;
            int layerWriterBase = ar.NextSlot;
            ar.ReadObject(r); // definition-list anchor (active-layer back-ref)
            int defCountPos = r.Pos;
            int defCount = (int)r.U32();
            for (int i = 0; i < defCount; i++) ar.ReadObject(r, "CComponentDefinition");

            int rootCountPos = r.Pos;
            int origRootCount = (int)Tlv.ReadU32(data, rootCountPos);
            r.U32();
            LegacyReaders.ReadEntityList(ar, r, origRootCount, "root");
            int tailPos = r.Pos;

            _data = data;
            _materialInsertPos = start;
            _base = @base;
            _layerCountPos = layerCountPos;
            _origLayerCount = origLayerCount;
            _layerInsertPos = layerInsertPos;
            _defCountPos = defCountPos;
            _origDefCount = defCount;
            _rootCountPos = rootCountPos;
            _origRootCount = origRootCount;
            _tailPos = tailPos;
            _scaffoldNextSlot = ar.NextSlot;
            _scaffoldClassSlot = new Dictionary<string, int>(ar.ClassSlot);
            _materialWriter = new ArchiveWriter(@base, new Dictionary<string, int>());
            _layerWriterBase = layerWriterBase;
        }

        /// <summary>Register a solid-color material and return a handle to
        /// pass as AddFace's material argument. rgba is (r, g, b, a),
        /// each 0-255.
        ///
        /// Calling this again with a name already registered returns the
        /// same handle rather than creating a duplicate material.
        ///
        /// All materials must be added before the first AddFace call - the
        /// geometry section's slot numbering is fixed once writing begins,
        /// and depends on the final material count. They must also come
        /// before any AddLayer or AddComponentDefinition call - materials
        /// are spliced in earlier in the file, so both of those sections'
        /// own slot numbering depends on the final material count too.</summary>
        public int AddMaterial(string name, (int R, int G, int B, int A) rgba)
        {
            if (_geometryWriter != null)
            {
                throw new SkpWriteException("AddMaterial must be called before any AddFace calls");
            }
            if (_layerWriter != null)
            {
                throw new SkpWriteException("AddMaterial must be called before any AddLayer calls");
            }
            if (DefinitionWriter != null)
            {
                throw new SkpWriteException("AddMaterial must be called before any AddComponentDefinition calls");
            }
            if (MaterialsByName.TryGetValue(name, out int existing)) return existing;
            ValidateByteRange(rgba.R, nameof(rgba.R));
            ValidateByteRange(rgba.G, nameof(rgba.G));
            ValidateByteRange(rgba.B, nameof(rgba.B));
            ValidateByteRange(rgba.A, nameof(rgba.A));
            int slot = _materialWriter.WriteMaterial(name, ((byte)rgba.R, (byte)rgba.G, (byte)rgba.B, (byte)rgba.A));
            MaterialsByName[name] = slot;
            _materialCount += 1;
            return slot;
        }

        /// <summary>Convenience overload defaulting alpha to 255 (opaque).</summary>
        public int AddMaterial(string name, (int R, int G, int B) rgb) => AddMaterial(name, (rgb.R, rgb.G, rgb.B, 255));

        private static void ValidateByteRange(int v, string label)
        {
            if (v < 0 || v > 255)
            {
                throw new SkpWriteException("rgba must be 4 integers in 0-255");
            }
        }

        /// <summary>Register an image-textured material from a local PNG
        /// or JPEG file and return a handle to pass as AddFace's material
        /// argument. The format is detected from the file's own magic
        /// bytes, not its extension - PNG and JPEG are the only two this
        /// project has confirmed the on-disk CDib subtype tag for via SDK
        /// ground truth. Same ordering rules as AddMaterial.
        ///
        /// If this material will ever be used with AddFace's frontUv/
        /// backUv pinning, pass appliedHeight: 1.0 (matching those pins'
        /// own 0..1 range) - the read-side UV formula divides by this
        /// field even for a positioned mapping, and the default (an
        /// internal sentinel, real SketchUp's own byte pattern for "never
        /// explicitly scaled") is astronomically small, which corrupts ANY
        /// face using this material, not just default-projected ones
        /// (confirmed against real SketchUp 2026-08-27 - see
        /// WriteTexturedMaterial's own note). Left at the default for the
        /// plain default-planar-projection case, matching this method's
        /// original, narrower scope.</summary>
        public int AddTextureMaterial(string name, string imagePath, double? appliedHeight = null)
        {
            if (_geometryWriter != null)
            {
                throw new SkpWriteException("AddTextureMaterial must be called before any AddFace calls");
            }
            if (_layerWriter != null)
            {
                throw new SkpWriteException("AddTextureMaterial must be called before any AddLayer calls");
            }
            if (DefinitionWriter != null)
            {
                throw new SkpWriteException("AddTextureMaterial must be called before any AddComponentDefinition calls");
            }
            if (MaterialsByName.TryGetValue(name, out int existing)) return existing;
            byte[] imageBytes = System.IO.File.ReadAllBytes(imagePath);
            int subtype = CreateMath.DetectImageSubtype(imageBytes);
            int slot = _materialWriter.WriteTexturedMaterial(name, imageBytes, imagePath, subtype, appliedHeight);
            MaterialsByName[name] = slot;
            _materialCount += 1;
            return slot;
        }

        /// <summary>Register a layer and return a handle to pass as
        /// AddFace's layer argument.
        ///
        /// Calling this again with a name already registered returns the
        /// same handle rather than creating a duplicate layer (color/
        /// hidden are ignored on a repeat call - only the first
        /// registration sets them).
        ///
        /// All layers must be added before the first AddFace call, for
        /// the same reason as AddMaterial. They must also come before any
        /// AddComponentDefinition call.</summary>
        public int AddLayer(string name, (int R, int G, int B, int A)? color = null, bool hidden = false)
        {
            if (_geometryWriter != null)
            {
                throw new SkpWriteException("AddLayer must be called before any AddFace calls");
            }
            if (DefinitionWriter != null)
            {
                throw new SkpWriteException("AddLayer must be called before any AddComponentDefinition calls");
            }
            if (LayersByName.TryGetValue(name, out int existing)) return existing;
            (byte, byte, byte, byte)? rgba = null;
            if (color.HasValue)
            {
                var c = color.Value;
                ValidateByteRange(c.R, "r"); ValidateByteRange(c.G, "g"); ValidateByteRange(c.B, "b"); ValidateByteRange(c.A, "a");
                rgba = ((byte)c.R, (byte)c.G, (byte)c.B, (byte)c.A);
            }
            if (_layerWriter == null)
            {
                int materialShift = _materialWriter.NextSlot - _base;
                _layerWriterStart = _layerWriterBase + materialShift;
                // CLayer's class declaration lives inside Layer0's
                // copied-through bytes, which - like everything else after
                // the material section - shifts by materialShift. The
                // scaffold-derived class_slot dict still has its raw,
                // unshifted value, so correct every entry before handing
                // it to a writer that might look one up.
                _layerWriter = new ArchiveWriter(_layerWriterStart, MaterialShiftedClassSlot());
            }
            int slot = _layerWriter.WriteLayer(name, hidden: hidden, rgba: rgba);
            LayersByName[name] = slot;
            _layerCount += 1;
            return slot;
        }

        /// <summary>Convenience overload defaulting alpha to 255 (opaque).</summary>
        public int AddLayer(string name, (int R, int G, int B) color, bool hidden = false) =>
            AddLayer(name, (color.R, color.G, color.B, 255), hidden);

        private Dictionary<string, int> MaterialShiftedClassSlot()
        {
            int materialShift = _materialWriter.NextSlot - _base;
            var result = new Dictionary<string, int>();
            foreach (var kv in _scaffoldClassSlot) result[kv.Key] = kv.Value + materialShift;
            return result;
        }

        private int LayerShift() => _layerWriter == null ? 0 : _layerWriter.NextSlot - _layerWriterStart;

        /// <summary>The class_slot dictionary a writer positioned right
        /// after the layer section (a definition writer, or root geometry
        /// if no definitions exist) should start from.</summary>
        private Dictionary<string, int> PostLayerClassSlot() =>
            _layerWriter != null ? new Dictionary<string, int>(_layerWriter.ClassSlot) : MaterialShiftedClassSlot();

        private ComponentDefinitionBuilder StartDefinition(string name, string caller, GroupPlacement? groupPlacement, IReadOnlyList<AttributeDict>? attributeDicts)
        {
            if (_geometryWriter != null)
            {
                throw new SkpWriteException($"{caller} must be called before any AddFace/AddInstance calls");
            }
            if (_openDefinition != null)
            {
                throw new SkpWriteException(
                    $"component definition '{_openDefinition.Name}' is still open - exit its using block before starting another");
            }
            if (DefinitionWriter == null)
            {
                _definitionWriterStart = _scaffoldNextSlot + (_materialWriter.NextSlot - _base) + LayerShift();
                DefinitionWriter = new ArchiveWriter(_definitionWriterStart, PostLayerClassSlot());
            }
            var (slot, countPatchPos) = DefinitionWriter.WriteDefinitionHeader(attributeDicts);
            _definitionCount += 1;
            var comp = new ComponentDefinitionBuilder(this, slot, name, countPatchPos, groupPlacement);
            _openDefinition = comp;
            return comp;
        }

        /// <summary>Start a new reusable component definition. Use the
        /// returned object with a using block, adding its geometry via
        /// .AddFace inside it; once closed (Dispose), pass it to
        /// AddInstance to place copies of it in the model.
        ///
        /// <code>
        /// using (var chair = builder.AddComponentDefinition("Chair"))
        /// {
        ///     chair.AddFace(new (double,double,double)[] { (0,0,0), (20,0,0), (20,20,0), (0,20,0) });
        /// }
        /// builder.AddInstance(chair, translation: (100, 0, 0));
        /// </code>
        ///
        /// Must be called before any AddFace/AddInstance call on the
        /// builder itself - component definitions splice in after
        /// materials and layers, before root-level geometry.
        ///
        /// attributes, if given, is custom key/value metadata (values may
        /// be string, int, or double) attached to the definition itself,
        /// under a dictionary named attributeDictName.</summary>
        public ComponentDefinitionBuilder AddComponentDefinition(
            string name, IReadOnlyDictionary<string, object>? attributes = null, string attributeDictName = "attributes")
        {
            var attributeDicts = attributes != null && attributes.Count > 0
                ? new List<AttributeDict> { new AttributeDict(attributeDictName, attributes) }
                : null;
            return StartDefinition(name, "AddComponentDefinition", null, attributeDicts);
        }

        /// <summary>Start a new group. Use the returned object with a
        /// using block, adding its geometry via .AddFace inside it - the
        /// group is placed at translation/matrix3x3 automatically when
        /// Dispose runs, unlike AddComponentDefinition there is no
        /// separate placement call.
        ///
        /// <code>
        /// using (var table = builder.AddGroup("Table", translation: (50, 0, 0)))
        /// {
        ///     table.AddFace(new (double,double,double)[] { (0,0,0), (30,0,0), (30,30,0), (0,30,0) });
        /// }
        /// </code>
        ///
        /// Same ordering rule as AddComponentDefinition. rotation, if
        /// given, is an (axis, angleRadians) pair - an alternative to
        /// hand-deriving matrix3x3 for the common case of a pure rotation;
        /// pass at most one of the two.</summary>
        public ComponentDefinitionBuilder AddGroup(
            string? name = null,
            (double X, double Y, double Z) translation = default,
            double[]? matrix3x3 = null,
            ((double X, double Y, double Z) Axis, double AngleRadians)? rotation = null,
            int? material = null, int? layer = null,
            bool hidden = false)
        {
            var resolved = CreateMath.ResolveMatrix3x3(matrix3x3, rotation);
            var placement = new GroupPlacement(translation, resolved, material ?? 0, layer ?? 0, hidden);
            return StartDefinition(name ?? "Group", "AddGroup", placement, null);
        }

        private int DefinitionShift() => DefinitionWriter == null ? 0 : DefinitionWriter.NextSlot - _definitionWriterStart;

        private Dictionary<string, int> PostDefinitionClassSlot() =>
            DefinitionWriter != null ? new Dictionary<string, int>(DefinitionWriter.ClassSlot) : PostLayerClassSlot();

        /// <summary>Place one instance of definition (from
        /// AddComponentDefinition, already closed) in the model.
        ///
        /// matrix3x3 is a row-major 3x3 rotation/scale matrix (identity if
        /// null); translation is applied after it, in inches.
        /// material/layer, if given, are handles from AddMaterial/AddLayer
        /// applied to the instance itself (not its contents).
        ///
        /// rotation, if given, is an (axis, angleRadians) pair - an
        /// alternative to matrix3x3 for the common case of a pure
        /// rotation, so the caller doesn't have to hand-derive a rotation
        /// matrix (Rodrigues' formula) themselves; pass at most one of the
        /// two.
        ///
        /// attributes, if given, is custom key/value metadata attached to
        /// this instance specifically, under a dictionary named
        /// attributeDictName.
        ///
        /// hidden hides this specific placement (SketchUp's "Hide" on the
        /// instance) - its contents still exist in the file, just not
        /// shown by default.</summary>
        public void AddInstance(
            ComponentDefinitionBuilder definition, string? name = null,
            (double X, double Y, double Z) translation = default,
            double[]? matrix3x3 = null,
            ((double X, double Y, double Z) Axis, double AngleRadians)? rotation = null,
            int? material = null, int? layer = null,
            IReadOnlyDictionary<string, object>? attributes = null, string attributeDictName = "attributes",
            bool hidden = false)
        {
            if (!ReferenceEquals(definition.Skp, this))
            {
                throw new SkpWriteException(
                    $"component definition '{definition.Name}' belongs to a different builder (a different NewFile() call) - its slot number is meaningless here");
            }
            if (!definition.Closed)
            {
                throw new SkpWriteException(
                    $"component definition '{definition.Name}' is still open - exit its using block before calling AddInstance");
            }
            var resolved = CreateMath.ResolveMatrix3x3(matrix3x3, rotation);
            EnsureGeometryWriter();
            var attributeDicts = attributes != null && attributes.Count > 0
                ? new List<AttributeDict> { new AttributeDict(attributeDictName, attributes) }
                : null;
            _newEntityCount += _geometryWriter!.WriteInstance(
                definition.Slot, name ?? definition.Name, translation, resolved, material ?? 0, layer ?? 0,
                attributeDicts, hidden);
            _faceCount += 1; // reuses the "at least one root entity" check in ToBytes
        }

        /// <summary>Place a SketchUp Image entity (File > Import > Image) -
        /// a picture placed as its own object, distinct from painting a
        /// texture material onto an ordinary face (an Image gets its own
        /// Outliner classification and explode behavior a plain textured
        /// face doesn't).
        ///
        /// width/height size the image's quad in inches; the image covers
        /// it edge to edge, undistorted regardless of the source file's
        /// own pixel aspect ratio (get the ratio right yourself if that
        /// matters - this does not auto-derive it). translation/
        /// matrix3x3/rotation/hidden place it exactly like AddInstance -
        /// the quad starts in the XY plane; rotate it to stand upright
        /// (e.g. on a wall) the same way you would any other placement.
        /// layer, if given, is a handle from AddLayer.
        ///
        /// <code>
        /// builder.AddImage("photo.jpg", 48, 36,
        ///     translation: (0, 0, 40),
        ///     rotation: ((1, 0, 0), Math.PI / 2));
        /// </code>
        ///
        /// Must be called before any AddLayer/AddComponentDefinition/
        /// AddGroup/AddFace/AddInstance call - like AddTextureMaterial
        /// (which this calls internally to register the image itself), it
        /// needs a material, and this writer's file format requires every
        /// material to be registered before any geometry section begins.
        ///
        /// The image's quad and UV mapping are pinned explicitly (AddFace's
        /// frontUv), not left to the default per-material tile-size
        /// projection - AddTextureMaterial is called with appliedHeight:
        /// 1.0 for exactly this reason: the read-side UV formula divides
        /// by the material's applied height even for a pinned mapping, and
        /// the library default there (a ground-truth sentinel, not a real
        /// number) is astronomically small - confirmed via real SketchUp
        /// screenshots (2026-08-27, Python writer) to render as a
        /// corrupted, vertically-smeared texture when left in place. 1.0
        /// makes that division a no-op against this method's own 0..1
        /// pins.
        ///
        /// Unlike every other entity this writer produces, CImage's exact
        /// binary schema version (see CreateConstants.ImageSchema) is a
        /// best-effort guess, not calibrated against a real
        /// SketchUp-authored Image entity - none was available. This
        /// project's own reader round-trips the result correctly, but real
        /// SketchUp's acceptance of the file is unverified beyond the
        /// Python port's own real-SketchUp test (placement/orientation/
        /// texture all confirmed correct there after the appliedHeight fix
        /// - see CHECKLIST.md).</summary>
        public void AddImage(
            string imagePath, double width, double height,
            (double X, double Y, double Z) translation = default,
            double[]? matrix3x3 = null,
            ((double X, double Y, double Z) Axis, double AngleRadians)? rotation = null,
            int? layer = null,
            bool hidden = false)
        {
            int mat = AddTextureMaterial($"__openskp_image_{_materialCount}", imagePath, appliedHeight: 1.0);
            ComponentDefinitionBuilder imageDef;
            using (imageDef = AddComponentDefinition($"Image{_definitionCount}"))
            {
                // Standard (0,0)-at-bottom-left, V increasing upward - no
                // vertical flip. Every other UV-related fact in this file
                // is calibrated against real SketchUp output; this one
                // specific sense is NOT (no ground truth available - see
                // this method's own warning above) and could come out
                // upside down in real SketchUp if its texture sampling
                // flips V the other way.
                imageDef.AddFace(
                    new (double, double, double)[] { (0, 0, 0), (width, 0, 0), (width, height, 0), (0, height, 0) },
                    material: mat,
                    frontUv: new[]
                    {
                        new UvCorrespondence((0, 0, 0), (0.0, 0.0)),
                        new UvCorrespondence((width, 0, 0), (1.0, 0.0)),
                        new UvCorrespondence((0, height, 0), (0.0, 1.0)),
                    });
            }
            var resolved = CreateMath.ResolveMatrix3x3(matrix3x3, rotation);
            EnsureGeometryWriter();
            _newEntityCount += _geometryWriter!.WriteImage(imageDef.Slot, translation, resolved, layer ?? 0, hidden);
            _faceCount += 1; // reuses the "at least one root entity" check in ToBytes
        }

        private void EnsureGeometryWriter()
        {
            if (_geometryWriter != null) return;
            if (_openDefinition != null)
            {
                // Calling this while a definition/group is still open
                // would lock in the geometry writer's starting slot
                // before that definition (and anything added to it
                // afterward) finishes growing DefinitionWriter - the
                // locked-in slot would then be too low, corrupting every
                // back-reference root-level geometry makes.
                throw new SkpWriteException(
                    $"component definition '{_openDefinition.Name}' is still open - exit its using block before adding root-level geometry");
            }
            int materialShift = _materialWriter.NextSlot - _base;
            _geometryWriter = new ArchiveWriter(
                _scaffoldNextSlot + materialShift + LayerShift() + DefinitionShift(),
                PostDefinitionClassSlot());
            // Flush any groups that closed earlier, in the order they
            // were created - deferred until now so closing one group
            // doesn't lock in root-level slot numbering before a later
            // AddGroup/AddComponentDefinition call has had a chance to
            // run.
            foreach (var (comp, placement) in _pendingGroups)
            {
                _newEntityCount += _geometryWriter.WriteGroup(
                    comp.Slot, comp.Name, placement.Translation, placement.Matrix3x3, placement.Material, placement.Layer, placement.Hidden);
                _faceCount += 1;
            }
            _pendingGroups.Clear();
        }

        internal void ClearOpenDefinition(ComponentDefinitionBuilder comp)
        {
            if (ReferenceEquals(_openDefinition, comp)) _openDefinition = null;
        }

        internal void EnqueuePendingGroup(ComponentDefinitionBuilder comp, GroupPlacement placement)
        {
            _pendingGroups.Add((comp, placement));
        }

        /// <summary>Add one planar face, defined by 3 or more coplanar
        /// points (in inches) forming a closed polygon in order - do not
        /// repeat the first point at the end.
        ///
        /// Vertices and edges are automatically shared with previously-
        /// added faces wherever a point's (x, y, z) coordinates match
        /// exactly (same double values) - build a connected mesh by
        /// reusing the same point tuples across AddFace calls, not by
        /// re-deriving numerically-close-but-not-identical coordinates.
        ///
        /// material/backMaterial, if given, are handles returned by
        /// AddMaterial (or AddTextureMaterial) - applied to the face's
        /// front/back side respectively. layer, if given, is a handle
        /// returned by AddLayer.
        ///
        /// hidden hides the face. softEdges/smoothEdges/hiddenEdges
        /// control any edge newly created by this call (not one already
        /// shared with a previous face).
        ///
        /// frontUv/backUv, if given, explicitly position that side's
        /// texture instead of the default planar projection: exactly 3
        /// (point, (u, v)) correspondences.
        ///
        /// By default, non-coplanar points throws SkpWriteException - this
        /// writer only stores true planar faces. autoTriangulate=true
        /// instead mirrors real SketchUp's own behavior when you draw a
        /// not-quite-flat polygon: it's silently fan-triangulated from
        /// points[0] into several always-planar triangular faces (2 for a
        /// quad) rather than rejected. Not compatible with frontUv/backUv.
        ///
        /// holes, if given, is a sequence of point lists - each an
        /// independent closed polygon (winding direction doesn't matter)
        /// cut out of the face, e.g. a window opening in a wall. Every
        /// hole's points must lie on the same plane as points itself. Not
        /// combined with autoTriangulate.</summary>
        public void AddFace(
            IReadOnlyList<(double X, double Y, double Z)> points,
            int? material = null, int? layer = null, int? backMaterial = null,
            bool hidden = false, bool softEdges = false, bool smoothEdges = false, bool hiddenEdges = false,
            IReadOnlyList<UvCorrespondence>? frontUv = null, IReadOnlyList<UvCorrespondence>? backUv = null,
            IReadOnlyDictionary<string, object>? attributes = null, string attributeDictName = "attributes",
            bool autoTriangulate = false,
            IReadOnlyList<IReadOnlyList<(double X, double Y, double Z)>>? holes = null)
        {
            if (points.Count < 3)
            {
                throw new SkpWriteException("a face needs at least 3 points");
            }
            EnsureGeometryWriter();
            var attributeDicts = attributes != null && attributes.Count > 0
                ? new List<AttributeDict> { new AttributeDict(attributeDictName, attributes) }
                : null;
            _newEntityCount += FaceWriting.WriteFaceOrTriangulate(
                _geometryWriter!, points, _vertexSlots, _edgeRegistry,
                material ?? 0, layer ?? 0, backMaterial ?? 0,
                hidden, softEdges, smoothEdges, hiddenEdges,
                frontUv, backUv, attributeDicts, autoTriangulate,
                holes: holes);
            _faceCount += 1;
        }

        /// <summary>Add one circular face - a true SketchUp circle
        /// (editable by radius, re-tessellatable, selectable as a single
        /// "Curve" entity), not numSegments disconnected straight edges
        /// that merely happen to trace that shape.
        ///
        /// center/radius are in inches; normal is the circle's plane
        /// normal (need not be a unit vector - it's normalized
        /// automatically), also the resulting face's front-side normal.
        /// numSegments (3-255) controls tessellation, matching SketchUp's
        /// own circle tool default of 24.</summary>
        public void AddCircle(
            (double X, double Y, double Z) center, (double X, double Y, double Z) normal, double radius,
            int numSegments = 24,
            int? material = null, int? layer = null, int? backMaterial = null,
            bool hidden = false,
            IReadOnlyList<UvCorrespondence>? frontUv = null, IReadOnlyList<UvCorrespondence>? backUv = null,
            IReadOnlyDictionary<string, object>? attributes = null, string attributeDictName = "attributes")
        {
            if (numSegments < 3 || numSegments > 255)
            {
                throw new SkpWriteException($"num_segments must be between 3 and 255, got {numSegments}");
            }
            normal = CreateMath.Normalize3(normal);
            EnsureGeometryWriter();
            var (u, w) = CreateMath.CircleBasis(normal);
            var xAxis = (radius * u.X, radius * u.Y, radius * u.Z);
            var curveParams = new ArcCurveParams(center, normal, xAxis, 0.0, 2.0 * Math.PI, radius, numSegments);
            var points = CreateMath.CirclePoints(center, radius, numSegments, u, w);
            var attributeDicts = attributes != null && attributes.Count > 0
                ? new List<AttributeDict> { new AttributeDict(attributeDictName, attributes) }
                : null;
            _newEntityCount += _geometryWriter!.WriteFace(
                points, _vertexSlots, _edgeRegistry,
                material ?? 0, layer ?? 0, backMaterial ?? 0,
                hidden, false, false, false,
                frontUv, backUv, attributeDicts,
                curveParams: curveParams);
            _faceCount += 1;
        }

        /// <summary>Add one partial (open) arc - a genuine SketchUp arc
        /// entity (editable by radius/angle, re-tessellatable), not
        /// disconnected straight edges that merely trace that shape.
        /// Unlike AddCircle, this creates edges only, no face.
        ///
        /// startAngle/endAngle (radians) measure the sweep from an
        /// arbitrary but fixed 0-angle reference direction in the circle's
        /// plane (perpendicular to normal, chosen automatically the same
        /// way for every arc/circle built by this same normal). Sweeps in
        /// either direction and sweeps beyond a full turn are both valid.</summary>
        public void AddArc(
            (double X, double Y, double Z) center, (double X, double Y, double Z) normal, double radius,
            double startAngle, double endAngle,
            int numSegments = 24,
            bool hiddenEdges = false, bool softEdges = false, bool smoothEdges = false)
        {
            if (numSegments < 3 || numSegments > 255)
            {
                throw new SkpWriteException($"num_segments must be between 3 and 255, got {numSegments}");
            }
            if (endAngle == startAngle)
            {
                throw new SkpWriteException("start_angle and end_angle must differ - use AddCircle for a full circle");
            }
            normal = CreateMath.Normalize3(normal);
            EnsureGeometryWriter();
            var (u, w) = CreateMath.CircleBasis(normal);
            var xAxis = (radius * u.X, radius * u.Y, radius * u.Z);
            var curveParams = new ArcCurveParams(center, normal, xAxis, startAngle, endAngle, radius, numSegments);
            var points = CreateMath.ArcPoints(center, radius, numSegments, u, w, startAngle, endAngle);
            _newEntityCount += _geometryWriter!.WriteArc(points, _vertexSlots, _edgeRegistry, curveParams, hiddenEdges, softEdges, smoothEdges);
            _faceCount += 1; // reuses the "at least one root entity" check in ToBytes
        }

        /// <summary>Add one freeform polyline curve - a chain of straight
        /// edges (points in order, at least 2) grouped into one genuine
        /// SketchUp "Curve" entity, not disconnected individual edges that
        /// merely happen to connect end-to-end. No face, unlike AddFace.
        ///
        /// closed, if true, also connects the last point back to the
        /// first.</summary>
        public void AddPolyline(
            IReadOnlyList<(double X, double Y, double Z)> points,
            bool closed = false, bool hiddenEdges = false, bool softEdges = false, bool smoothEdges = false)
        {
            if (points.Count < 2)
            {
                throw new SkpWriteException("a polyline needs at least 2 points");
            }
            EnsureGeometryWriter();
            _newEntityCount += _geometryWriter!.WritePolyline(points, _vertexSlots, _edgeRegistry, closed, hiddenEdges, softEdges, smoothEdges);
            _faceCount += 1; // reuses the "at least one root entity" check in ToBytes
        }

        /// <summary>Return the finished file's bytes.</summary>
        public byte[] ToBytes()
        {
            if (_pendingGroups.Count > 0)
            {
                // A file with only groups (no AddFace/AddInstance call)
                // would otherwise never flush them - EnsureGeometryWriter
                // is a no-op once already created, so this is safe to call
                // unconditionally alongside every other call site.
                EnsureGeometryWriter();
            }
            if (_faceCount == 0)
            {
                throw new SkpWriteException("no geometry added - call AddFace at least once before saving");
            }

            // Every new-class declaration and every new object allocation
            // each consume one archive slot; NextSlot already reflects the
            // running total, so each shift is just the delta since its
            // writer started.
            int materialShift = _materialWriter.NextSlot - _base;
            int layerShift = LayerShift();
            int definitionShift = DefinitionShift();
            int geometryInitialSlot = _scaffoldNextSlot + materialShift + layerShift + definitionShift;
            int geometryShift = _geometryWriter!.NextSlot - geometryInitialSlot;
            int newRootCount = _origRootCount + _newEntityCount;

            var outBuf = new List<byte>(_data.Length + _materialWriter.Buf.Count + (DefinitionWriter?.Buf.Count ?? 0) + _geometryWriter.Buf.Count + 256);

            // The 4 bytes right before the material insertion point are a
            // reserved (always-present) mat_count field - zero/implicit in
            // the zero-material scaffold, not a gap that needs new bytes
            // inserted. Real SketchUp overwrites them in place rather than
            // growing the file by 4 extra bytes here.
            // Each layer's record embeds 2 pids (see WriteLayer);
            // materials use 1 pid each (WriteMaterial).
            long layerPids = _layerWriter != null ? _layerWriter.NextPid - 1 : 0;
            long pidDelta = _materialCount + layerPids;

            var prefix = new byte[_materialInsertPos - 4];
            Array.Copy(_data, 0, prefix, 0, prefix.Length);
            if (pidDelta != 0)
            {
                ushort u16 = Tlv.ReadU16(prefix, CreateConstants.PidCounterPos);
                int newVal = u16 + (int)pidDelta;
                prefix[CreateConstants.PidCounterPos] = (byte)(newVal & 0xFF);
                prefix[CreateConstants.PidCounterPos + 1] = (byte)((newVal >> 8) & 0xFF);
            }
            Array.Copy(
                CreateConstants.IsoCameraPrefixPatch, 0, prefix, CreateConstants.IsoCameraPrefixOffset,
                CreateConstants.IsoCameraPrefixPatch.Length);
            outBuf.AddRange(prefix);
            AppendU32(outBuf, (uint)_materialCount);
            outBuf.AddRange(_materialWriter.Buf);

            // materialInsertPos -> layerInsertPos: Layer0 (and any other
            // already-existing layers) plus the layer_count field,
            // unmodified except for that count.
            var middle1 = new byte[_layerInsertPos - _materialInsertPos];
            Array.Copy(_data, _materialInsertPos, middle1, 0, middle1.Length);
            int layerCountRel = _layerCountPos - _materialInsertPos;
            uint newLayerCount = (uint)(_origLayerCount + _layerCount);
            middle1[layerCountRel] = (byte)(newLayerCount & 0xFF);
            middle1[layerCountRel + 1] = (byte)((newLayerCount >> 8) & 0xFF);
            middle1[layerCountRel + 2] = (byte)((newLayerCount >> 16) & 0xFF);
            middle1[layerCountRel + 3] = (byte)((newLayerCount >> 24) & 0xFF);
            outBuf.AddRange(middle1);
            if (_layerWriter != null) outBuf.AddRange(_layerWriter.Buf);

            // layerInsertPos -> defCountPos: just the active-layer anchor,
            // which needs +materialShift (never +layerShift - Layer0
            // itself never moves just because more layers are appended
            // after it).
            var middle2A = new List<byte>(_defCountPos - _layerInsertPos);
            for (int i = _layerInsertPos; i < _defCountPos; i++) middle2A.Add(_data[i]);
            if (materialShift != 0)
            {
                ShiftRef(middle2A, CreateConstants.ActiveLayerAnchorRel, materialShift);
            }
            outBuf.AddRange(middle2A);

            AppendU32(outBuf, (uint)(_origDefCount + _definitionCount));
            if (DefinitionWriter != null) outBuf.AddRange(DefinitionWriter.Buf);

            // defCountPos+4 -> rootCountPos: any already-existing
            // definitions (none, in the blank scaffold), unmodified.
            for (int i = _defCountPos + 4; i < _rootCountPos; i++) outBuf.Add(_data[i]);

            AppendU32(outBuf, (uint)newRootCount);
            for (int i = _rootCountPos + 4; i < _tailPos; i++) outBuf.Add(_data[i]);
            outBuf.AddRange(_geometryWriter.Buf);

            var tail = new List<byte>(_data.Length - _tailPos);
            for (int i = _tailPos; i < _data.Length; i++) tail.Add(_data[i]);
            int totalTailShift = materialShift + layerShift + definitionShift + geometryShift;
            // TailRefPositions and IsoCameraTailPatches's positions both
            // index into this same tail buffer. A ref-shift that widens to
            // the 6-byte escape form grows the buffer at that point,
            // pushing every later position forward - so every action is
            // applied in ascending original-offset order, tracking that
            // growth, rather than at its original hardcoded offset.
            var actions = new List<(int Pos, bool IsRef, byte[]? Patch)>();
            foreach (var pos in CreateConstants.TailRefPositions) actions.Add((pos, true, null));
            foreach (var (pos, patch) in CreateConstants.IsoCameraTailPatches) actions.Add((pos, false, patch));
            actions.Sort((a, b) => a.Pos.CompareTo(b.Pos));
            int growth = 0;
            foreach (var (pos, isRef, patch) in actions)
            {
                int here = pos + growth;
                if (isRef)
                {
                    growth += ShiftRef(tail, here, totalTailShift);
                }
                else
                {
                    for (int i = 0; i < patch!.Length; i++) tail[here + i] = patch[i];
                }
            }
            outBuf.AddRange(tail);
            return outBuf.ToArray();
        }

        private static void AppendU32(List<byte> buf, uint v)
        {
            buf.Add((byte)(v & 0xFF));
            buf.Add((byte)((v >> 8) & 0xFF));
            buf.Add((byte)((v >> 16) & 0xFF));
            buf.Add((byte)((v >> 24) & 0xFF));
        }

        /// <summary>Renumber the u16 archive slot-reference at pos by
        /// shift, preserving the 0x8000 class-ref tag bit if the
        /// reference carries one. Widens to the 6-byte escape form (same
        /// encoding ArchiveWriter.NewOfKnownClass/Backref use, and the
        /// same &lt; 0x7FFF boundary) if the shifted slot would land at or
        /// past 0x7FFF - the scaffold's own references always start small
        /// enough to fit in 2 bytes on their own (it's a blank document),
        /// but a large enough shift can push one past that boundary;
        /// masking it back into 15 bits instead of widening would
        /// silently renumber it to the wrong slot entirely, corrupting
        /// whatever it points to. Returns the number of bytes the field
        /// grew by (0 or 4).</summary>
        private static int ShiftRef(List<byte> buf, int pos, int shift)
        {
            ushort u16 = (ushort)(buf[pos] | (buf[pos + 1] << 8));
            int tagBit = u16 & 0x8000;
            int slot = u16 & 0x7FFF;
            int newSlot = slot + shift;
            if (newSlot < 0x7FFF)
            {
                ushort newVal = (ushort)(tagBit | newSlot);
                buf[pos] = (byte)(newVal & 0xFF);
                buf[pos + 1] = (byte)((newVal >> 8) & 0xFF);
                return 0;
            }
            uint val = tagBit != 0 ? (0x80000000u | (uint)newSlot) : (uint)newSlot;
            var replacement = new byte[]
            {
                0xFF, 0xFF,
                (byte)(val & 0xFF), (byte)((val >> 8) & 0xFF), (byte)((val >> 16) & 0xFF), (byte)((val >> 24) & 0xFF),
            };
            buf.RemoveRange(pos, 2);
            buf.InsertRange(pos, replacement);
            return 4;
        }

        /// <summary>Write the finished file to path.</summary>
        public void Save(string path)
        {
            System.IO.File.WriteAllBytes(path, ToBytes());
        }
    }
}
