using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using Xunit;
using OpenSkp;

namespace OpenSkp.Tests
{
    /// <summary>Tests for Codegen.ToCSharpCode - generates C# source that
    /// rebuilds a parsed model via the writer API.
    ///
    /// Found via diffing a real, large file (jeff.skp: 2713 definitions,
    /// 113643 faces) against its own regenerated output (via the
    /// TypeScript port this mirrors, toTypeScriptCode): an early prototype
    /// dropped instance-level paint (95% of that file's instances) and
    /// instance names entirely, and never emitted textured materials at
    /// all.
    ///
    /// Unlike Python/TypeScript, C# can't exec() a source string directly
    /// - the real-fixture tests below actually COMPILE and RUN the
    /// generated code (via `dotnet run` in a throwaway project referencing
    /// this build's own OpenSkp.dll), the same way a real caller running
    /// this code would, rather than only checking the generated text looks
    /// right.</summary>
    public class CodegenTests
    {
        private static readonly string FixturesDir = Path.Combine(AppContext.BaseDirectory, "fixtures");

        [Fact]
        public void ReproducesSolidMaterialsInstancePaintAndNames()
        {
            var builder = SkpCreate.NewFile();
            int red = builder.AddMaterial("Red", (255, 0, 0));
            var box = builder.AddComponentDefinition("Box");
            using (box)
            {
                box.AddFace(new (double, double, double)[] { (0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0) }, material: red);
            }
            builder.AddInstance(box, translation: (0, 0, 0), material: red, name: "PaintedBox");
            builder.AddInstance(box, translation: (50, 0, 0), name: "PlainBox");

            var original = SkpFile.Parse(builder.ToBytes());
            string code = Codegen.ToCSharpCode(original);

            byte[] regenBytes = CompileAndRun(code);
            var regen = SkpFile.Parse(regenBytes);

            Assert.Equal(original.Materials.Select(m => m.Name), regen.Materials.Select(m => m.Name));
            Assert.Equal(2, regen.Root.Instances.Count);
            var byName = regen.Root.Instances.ToDictionary(i => i.Name);
            Assert.NotNull(byName["PaintedBox"].MaterialId);
            Assert.Null(byName["PlainBox"].MaterialId);
        }

        [Fact]
        public void ReproducesAGenuinelyEmptyDefinitionName()
        {
            // Found via cross-language analysis (2026-08-28), same bug
            // class as the empty instance name case above:
            // `!IsNullOrEmpty(defn.Name) ? defn.Name : $"Def{defId}"`
            // silently replaced a genuinely empty definition name with a
            // fabricated one. SketchUp Groups are internally just unnamed
            // component definitions (unlike Components, which SketchUp
            // auto-names), so an empty name is common in real files.
            var builder = SkpCreate.NewFile();
            var box = builder.AddComponentDefinition("");
            using (box)
            {
                box.AddFace(new (double, double, double)[] { (0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0) });
            }
            builder.AddInstance(box, translation: (0, 0, 0));

            var original = SkpFile.Parse(builder.ToBytes());
            Assert.Equal("", original.Definitions.Values.First().Name);

            string code = Codegen.ToCSharpCode(original);
            byte[] regenBytes = CompileAndRun(code);
            var regen = SkpFile.Parse(regenBytes);

            Assert.Equal("", regen.Definitions.Values.First().Name);
        }

        public static System.Collections.Generic.IEnumerable<object[]> RealFixtures()
        {
            // single_material_v17.skp is deliberately excluded: it
            // declares one material used by zero faces anywhere - a real
            // file the reader parses fine, but not one ToBytes() can ever
            // re-save (this writer requires at least one face),
            // independent of anything Codegen does.
            yield return new object[] { "SU_File.skp" };
            yield return new object[] { "Untitled.skp" };
            yield return new object[] { "capilla_quiroz_v17.skp" };
            yield return new object[] { "gondola_v20.skp" };
        }

        [Theory]
        [MemberData(nameof(RealFixtures))]
        public void ReproducesMaterialsLayersInstancePaintAndNames(string fixtureName)
        {
            var original = SkpFile.Parse(File.ReadAllBytes(Path.Combine(FixturesDir, fixtureName)));
            string code = Codegen.ToCSharpCode(original);

            byte[] regenBytes = CompileAndRun(code);
            var regen = SkpFile.Parse(regenBytes);

            Assert.Equal(
                original.Materials.Select(m => m.Name).OrderBy(x => x),
                regen.Materials.Select(m => m.Name).OrderBy(x => x));
            Assert.Equal(
                original.Layers.Select(l => l.Name).OrderBy(x => x),
                regen.Layers.Select(l => l.Name).OrderBy(x => x));

            static (string Name, bool Painted)[] InstKeys(SkpModel m) =>
                m.Root.Instances.Select(i => (i.Name, i.MaterialId.HasValue)).OrderBy(t => t.Name).ThenBy(t => t.Item2).ToArray();
            Assert.Equal(InstKeys(original), InstKeys(regen));
        }

        /// <summary>Writes `code` (a Codegen.ToCSharpCode result) plus a
        /// tiny Program.cs into a throwaway project referencing this
        /// build's own OpenSkp.dll, `dotnet run`s it, and returns the
        /// bytes GeneratedModel.Build() produced by reading them back from
        /// the file it's told to write them to (stdout is reserved for the
        /// build/run tool's own diagnostics, not a good channel for
        /// arbitrary binary data).</summary>
        private static byte[] CompileAndRun(string code)
        {
            string tempDir = Path.Combine(Path.GetTempPath(), "openskp_codegen_test_" + Guid.NewGuid());
            Directory.CreateDirectory(tempDir);
            try
            {
                string dllPath = typeof(SkpModel).Assembly.Location;
                File.WriteAllText(Path.Combine(tempDir, "Generated.cs"), code);
                File.WriteAllText(Path.Combine(tempDir, "harness.csproj"), $@"<Project Sdk=""Microsoft.NET.Sdk"">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net9.0</TargetFramework>
    <Nullable>enable</Nullable>
  </PropertyGroup>
  <ItemGroup>
    <Reference Include=""OpenSkp"">
      <HintPath>{dllPath}</HintPath>
    </Reference>
  </ItemGroup>
</Project>
");
                string outPath = Path.Combine(tempDir, "out.skp");
                File.WriteAllText(Path.Combine(tempDir, "Program.cs"), $@"
using System.IO;
File.WriteAllBytes(@""{outPath}"", GeneratedModel.Build());
");
                var psi = new ProcessStartInfo("dotnet", "run --no-restore")
                {
                    WorkingDirectory = tempDir,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    UseShellExecute = false,
                };
                // Restore separately (with output captured) so a restore
                // failure surfaces its own log instead of being buried
                // under `run`'s.
                RunOrThrow(new ProcessStartInfo("dotnet", "restore") { WorkingDirectory = tempDir, RedirectStandardOutput = true, RedirectStandardError = true, UseShellExecute = false });
                RunOrThrow(psi);

                Assert.True(File.Exists(outPath), "generated code did not produce an output file");
                return File.ReadAllBytes(outPath);
            }
            finally
            {
                try { Directory.Delete(tempDir, recursive: true); } catch (IOException) { /* best-effort cleanup */ }
            }
        }

        private static void RunOrThrow(ProcessStartInfo psi)
        {
            using var proc = Process.Start(psi)!;
            string stdout = proc.StandardOutput.ReadToEnd();
            string stderr = proc.StandardError.ReadToEnd();
            proc.WaitForExit();
            if (proc.ExitCode != 0)
            {
                throw new InvalidOperationException(
                    $"'{psi.FileName} {psi.Arguments}' exited {proc.ExitCode}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}");
            }
        }
    }
}
