# OpenSKP — C# / .NET SketchUp Binary Parser

OpenSKP is a pure class library that enables programmatic parsing of SketchUp (`.skp`) binary files without requiring Trimble SketchUp or its proprietary SDK.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/iamahsanmehmood/openskp/blob/main/LICENSE)
[![NuGet](https://img.shields.io/nuget/v/OpenSkp.svg?logo=nuget&logoColor=white)](https://www.nuget.org/packages/OpenSkp)

---

## 🌟 Vision

Provide .NET developers (desktop, cloud, and mobile) with native, fast, zero-dependency access to SketchUp 3D models. Works seamlessly across Windows, macOS, Linux, and Xamarin/MAUI platforms.

### 🌐 [Try the Live Web Viewer (Drag-and-Drop)](https://iamahsanmehmood.github.io/openskp/)

---

## 🚀 Installation

Install the package via the .NET CLI:

```bash
dotnet add package OpenSkp
```

Or via the NuGet Package Manager:

```powershell
Install-Package OpenSkp
```

---

## 💻 Quick Start

### 1. Parse an SKP File
Open and parse a SketchUp model to inspect its metadata:

```csharp
using System;
using OpenSkp;

class Program
{
    static void Main()
    {
        // Open and decode an SKP file
        SkpModel model = SkpFile.Open("house.skp");

        Console.WriteLine($"SketchUp File Version: {model.Version}");
        
        // Print Layer Names
        Console.WriteLine("Layers:");
        foreach (var layer in model.Layers)
        {
            Console.WriteLine($"- {layer.Name} (RGB: {layer.Color.R}, {layer.Color.G}, {layer.Color.B})");
        }

        // Print Materials List
        Console.WriteLine("Materials:");
        foreach (var material in model.Materials)
        {
            Console.WriteLine($"- {material.Name} (Transparency: {material.Transparency})");
        }
    }
}
```

---

## ⚙️ Target Frameworks

OpenSKP targets **.NET Standard 2.0**, ensuring full compatibility with:
- **.NET 5 / 6 / 7 / 8 / 9** (Console, Web APIs, ASP.NET Core)
- **.NET Core 2.0+**
- **.NET Framework 4.6.1+** (WPF, WinForms)
- **Mono / Xamarin / .NET MAUI** (iOS, Android)

---

## 🖥️ Monorepo Package Ecosystem

OpenSKP is designed as a unified cross-platform monorepo:
* [Python Package](https://pypi.org/project/openskp/) (`openskp`)
* [TypeScript / JS Package](https://www.npmjs.com/package/openskp) (`openskp`)
* [.NET Package](https://www.nuget.org/packages/OpenSkp) (`OpenSkp`)
* [Dart Package](https://pub.dev/packages/openskp) (`openskp`)

---

## 📄 License

This project is licensed under the **MIT License** — see the [LICENSE](https://github.com/iamahsanmehmood/openskp/blob/main/LICENSE) file for details.
