using System;
using System.Collections.Generic;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>
    /// A face's stored plane satisfies n.p + d = 0 for the face's own vertices.
    ///
    /// SketchUp stores the plane that way (all 481 faces of two real files checked: gondola_v20.skp and
    /// capilla_quiroz_v17.skp). The writer used to store d as +n.centroid, i.e. with the wrong sign: openskp's own
    /// reader only uses the normal, so a round trip never noticed, and the SDK's SUFaceGetPlane recomputes the
    /// plane, so it does not either - the raw record has to be read, as here (C++ fixed the same in #477).
    /// </summary>
    public class CreateFacePlaneTests
    {
        private static readonly byte[] Marker = { 0xFF, 0xFF, 3, 0, 5, 0, (byte)'C', (byte)'F', (byte)'a', (byte)'c', (byte)'e' };

        private static readonly (double, double, double)[][] PolygonList =
        {
            new[] { (0.0, 0.0, 3.0), (2.0, 0.0, 3.0), (2.0, 2.0, 3.0), (0.0, 2.0, 3.0) },
            new[] { (0.0, 2.0, 3.0), (2.0, 2.0, 3.0), (2.0, 0.0, 3.0), (0.0, 0.0, 3.0) },
            new[] { (-7.0, 0.0, 0.0), (-7.0, 2.0, 0.0), (-7.0, 2.0, 2.0), (-7.0, 0.0, 2.0) },
            new[] { (10.0, 20.0, 35.0), (12.0, 20.0, 37.0), (12.0, 22.0, 39.0), (10.0, 22.0, 37.0) },
        };

        public static IEnumerable<object[]> Cases() =>
            Enumerable.Range(0, PolygonList.Length).SelectMany(i => new[] { new object[] { i, false }, new object[] { i, true } });

        private static int IndexOf(byte[] haystack, byte[] needle)
        {
            for (int i = 0; i + needle.Length <= haystack.Length; i++)
            {
                bool match = true;
                for (int j = 0; j < needle.Length && match; j++) match = haystack[i + j] == needle[j];
                if (match) return i;
            }
            return -1;
        }

        private static double[] StoredPlane(byte[] data)
        {
            int pos = IndexOf(data, Marker) + Marker.Length;
            Assert.Equal(0, BitConverter.ToUInt16(data, pos)); // null attribute ref
            pos += 2;
            byte mask = data[pos++];
            for (int bit = 0; bit < 8; bit++) if ((mask & (1 << bit)) != 0) pos++; // the persistent-id bytes the mask announces
            pos += 10; // material and drawing flags
            return Enumerable.Range(0, 4).Select(i => BitConverter.ToDouble(data, pos + 8 * i)).ToArray();
        }

        [Theory]
        [MemberData(nameof(Cases))]
        public void FacePlaneAgreesWithVertices(int polygonIndex, bool nested)
        {
            var polygon = PolygonList[polygonIndex];
            var builder = SkpCreate.NewFile();
            if (nested)
            {
                var def = builder.AddComponentDefinition("offset");
                def.AddFace(polygon);
                def.Dispose();
                builder.AddInstance(def);
            }
            else
            {
                builder.AddFace(polygon);
            }
            var p = StoredPlane(builder.ToBytes());
            foreach (var (x, y, z) in polygon)
                Assert.Equal(0.0, p[0] * x + p[1] * y + p[2] * z + p[3], 9);
        }
    }
}
