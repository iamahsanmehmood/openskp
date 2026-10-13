using System;
using System.Collections.Generic;
using System.Linq;
using OpenSkp;
using Xunit;

namespace OpenSkp.Tests
{
    public class ObjMaterialNamesTests
    {
        [Fact]
        public void ObjAndMtlNamesStayUniqueAfterSanitizing()
        {
            var scenarios = new (string?[] Raw, string[] Expected)[]
            {
                (new[] { "Wood/Oak", "Wood_Oak", "Wood_Oak_2" },
                    new[] { "Wood_Oak", "Wood_Oak_2_1", "Wood_Oak_2" }),
                (new[] { "Oak", "Pine" }, new[] { "Oak", "Pine" }),
                (new[] { "A", "A", "A" }, new[] { "A", "A_2", "A_3" }),
                (new string?[] { "Material_1", null }, new[] { "Material_1", "Material_1_2" }),
                (new[] { " ", "   " }, new[] { "default_material", "default_material_2" }),
                (new[] { "!!!", "???" }, new[] { "___", "____2" }),
                (new[] { "Oak", "oak" }, new[] { "Oak", "oak" })
            };

            foreach (var (raw, expected) in scenarios)
            {
                var scene = new Scene
                {
                    GltfMaterials = raw.Select(name => (object)(name == null
                        ? new Dictionary<string, object>()
                        : new Dictionary<string, object> { ["name"] = name })).ToList(),
                    GlbPrimitives = raw.Select((name, i) => new GlbPrimitive
                    {
                        GeomName = $"part{i}",
                        MaterialIndex = i,
                        Positions = new float[] { 0, 0, 0, 1, 0, 0, 0, 1, 0 },
                        Indices = new uint[] { 0, 1, 2 }
                    }).ToList()
                };

                // AppendLine emits CRLF on Windows; normalize assertions, not the exporter.
                string[] references(string input, string prefix) =>
                    input.Split('\n').Select(line => line.TrimEnd('\r'))
                        .Where(line => line.StartsWith(prefix, StringComparison.Ordinal))
                        .Select(line => line.Substring(prefix.Length)).ToArray();

                string[] mtlNames = references(ObjExport.ToMtl(scene), "newmtl ");
                string[] objNames = references(ObjExport.ToObj(scene), "usemtl ");
                Assert.Equal(expected, mtlNames);
                Assert.Equal(expected, objNames);
                Assert.Equal(mtlNames.Length, new HashSet<string>(mtlNames, StringComparer.Ordinal).Count);
            }
        }
    }
}
