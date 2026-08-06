# OpenSKP C++

The C++17 OpenSKP implementation parses modern SketchUp VFF/ZIP files and
legacy SketchUp 2013–2020 MFC archives without the SketchUp SDK. It exposes
the common parsed model plus separately baked, world-space, GLB-ready scene
data. Serializers are intentionally outside this initial port.

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
| [GoogleTest](https://github.com/google/googletest) | 1.17.0 (`52eb8108c5bdec04579160ae17225d66034bd723`) | C++ test suite | `OPENSKP_BUILD_TESTS=ON` |

miniz is compiled privately into OpenSKP, and GoogleTest is used only by the
test executable. Neither is a transitive dependency for installed consumers.
The triangulation implementation is included in this source tree and does not
require a separate library.

Standard FetchContent source overrides and offline workflows are supported,
including `FETCHCONTENT_SOURCE_DIR_MINIZ` and
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
```

`BUILD_SHARED_LIBS` controls static/shared output (static is the CMake
default). `OPENSKP_BUILD_TESTS` defaults on only when this directory is the
top-level project, and `OPENSKP_BUILD_EXAMPLES` defaults off.

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
