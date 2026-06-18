using System;
using System.IO;
using System.Collections.Generic;

namespace OpenSkp
{
    public class SkpModel
    {
        public string Version { get; set; } = "unknown";
        public List<Layer> Layers { get; set; } = new List<Layer>();
        public List<Material> Materials { get; set; } = new List<Material>();
    }

    public class Layer
    {
        public string Name { get; set; } = "";
        public Color Color { get; set; } = new Color();
    }

    public class Color
    {
        public byte R { get; set; } = 200;
        public byte G { get; set; } = 200;
        public byte B { get; set; } = 200;
    }

    public class Material
    {
        public string Name { get; set; } = "";
        public Color Color { get; set; } = new Color();
        public double Transparency { get; set; } = 1.0;
    }

    public static class SkpFile
    {
        public static SkpModel Parse(byte[] buffer)
        {
            // C# implementation coming soon
            throw new NotImplementedException("C#/.NET implementation is under development.");
        }

        public static SkpModel Open(string filePath)
        {
            var bytes = File.ReadAllBytes(filePath);
            return Parse(bytes);
        }
    }
}
