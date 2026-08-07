# OpenSKP C++

The C++17 OpenSKP implementation parses modern SketchUp VFF/ZIP files and
legacy SketchUp 2013–2020 MFC archives without the SketchUp SDK. It exposes
the common parsed model plus separately baked, world-space scene data and
in-memory or file-based GLB export.

## Dependencies

Building OpenSKP requires:

- CMake 3.21 or newer.
- A C++17 compiler and standard library, plus a C compiler for the bundled
  miniz sources. GCC, Clang, and MSVC are supported.
- Git and network access during the first CMake configure, unless the
  FetchContent dependencies have been provided locally.

CMake fetches these pinned dependencies:

| Dependency | Version | Used for | Required when |
| --- | --- | --- | --- |
| [miniz](https://github.com/richgel999/miniz) | 3.1.2 (`77d0dce8627735138c51770d1799a1ef48f2117d`) | Reading modern SKP ZIP containers | Always |
| [TinyGLTF](https://github.com/syoyo/tinygltf) | 2.9.7 (`488a70a3df62a4df1a736e9e56fb8836580c4888`) | Writing binary glTF 2.0 assets | Always |
| [GoogleTest](https://github.com/google/googletest) | 1.17.0 (`52eb8108c5bdec04579160ae17225d66034bd723`) | C++ test suite | `OPENSKP_BUILD_TESTS=ON` |

miniz and TinyGLTF are compiled privately into OpenSKP, and GoogleTest is used
only by the test executable. None is a transitive dependency for installed consumers.
The triangulation implementation is included in this source tree and does not
require a separate library.

Standard FetchContent source overrides and offline workflows are supported,
including `FETCHCONTENT_SOURCE_DIR_MINIZ` and
`FETCHCONTENT_SOURCE_DIR_TINYGLTF`, and
`FETCHCONTENT_SOURCE_DIR_GOOGLETEST`.

clang-format is an optional developer dependency. Version 18 is the canonical
CI version; it is needed only for the formatting targets documented below.

## Build and install

```bash
cmake -S . -B build -DOPENSKP_BUILD_TESTS=ON
cmake --build build
ctest --test-dir build --output-on-failure
cmake --install build --prefix /your/prefix
```

Consumers use the installed config package:

```cmake
find_package(OpenSkp CONFIG REQUIRED)
target_link_libraries(my_app PRIVATE OpenSkp::OpenSkp)
```

```cpp
#include <openskp/openskp.hpp>

auto file = openskp::SkpFile::open("model.skp");
auto model = file.parse();
auto scene = file.build_scene(); // independent reparse
auto bytes = openskp::to_glb(scene);
openskp::export_glb(scene, "model.glb");
```

`to_glb()` returns the complete binary asset as a `ByteBuffer`.
`export_glb()` writes exactly those bytes and accepts any output filename; it
does not create parent directories. OBJ and metadata JSON export are not yet
provided by the C++ package.

`BUILD_SHARED_LIBS` controls static/shared output (static is the CMake
default). `OPENSKP_BUILD_TESTS` defaults on only when this directory is the
top-level project, and `OPENSKP_BUILD_EXAMPLES` defaults off.
When examples are enabled, `openskp_export_glb input.skp output.glb` provides
a small command-line export example.

## Formatting

C++ sources use the Google clang-format style with a 100-column limit.
clang-format 18 is the canonical CI version. Data members in structs and
classes use separate declarations: declare one member per line, even when
adjacent members have the same type.

```bash
cmake --build build --target openskp-format
cmake --build build --target openskp-format-check
```

If CMake does not find the desired executable automatically, configure with
`-DOPENSKP_CLANG_FORMAT_EXECUTABLE=/path/to/clang-format`.
