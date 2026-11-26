# Security Policy

## Supported Versions

OpenSKP ships one version number across all five published language packages
(Python, TypeScript, .NET, Dart, C++), with the unpublished Rust crate
(`packages/rust`) and Dart `openskp_fragments` (`packages/dart_fragments`)
tracked on `main`. Security fixes are made against the latest release (`1.7.x`
today, and `1.8.x` once released) and `main`; older release lines are not
backported.

| Version | Supported                            |
| ------- | ------------------------------------ |
| 1.8.x   | :white_check_mark: *(once released)* |
| 1.7.x   | :white_check_mark:                   |
| < 1.7   | :x:                                  |

## Reporting a Vulnerability

**Please do not open a public issue for a security vulnerability.**

Use GitHub's private reporting instead: go to the
[Security tab](https://github.com/iamahsanmehmood/openskp/security) and
click **"Report a vulnerability."** This opens a private advisory visible
only to you and the maintainers, so the issue isn't exposed before a fix
ships.

What's useful in a report: which language package and version, a minimal
`.skp` file or code snippet that reproduces the issue, and what you'd
expect to happen instead.

## Scope

OpenSKP parses and writes SketchUp's binary `.skp` format from
untrusted input. Reports of particular interest:

- Memory-safety issues in the C++ port (out-of-bounds reads/writes, buffer
  overflows) triggered by a malformed or malicious `.skp` file.
- Denial-of-service via a crafted file (unbounded allocation, decompression
  bombs in the ZIP-based VFF container, infinite loops).
- Path traversal or arbitrary file write via a crafted file's embedded
  paths (textures, ZIP entries).
- Any input that causes a parser to execute unintended code.

Issues that require an already-malicious actor with write access to the
`.skp` file being parsed are still in scope — the library's job is to
handle arbitrary, untrusted files safely, not just well-formed ones.

Current parser and CI safeguards include a 64-level container/object and
attribute nesting cap in both the 2021+ VFF/TLV readers and the classic MFC
`CArchive` readers across all six ports, ZIP/DEFLATE decompression-bomb limits,
a header-checked 178,956,970-pixel cap before decoding embedded textures for GLB
colorization, C++ AddressSanitizer and UndefinedBehaviorSanitizer runs in CI
(`.github/workflows/ci-cpp.yml`), and automated fixture-mutation fuzzing across
all six readers under per-file time and memory limits
(`.github/workflows/ci-fuzz.yml`). Any input that bypasses these checks —
causing a crash, hang, unbounded allocation, or unwrapped internal exception —
is in scope.

## Response

We'll acknowledge a report as soon as we can and follow up with next
steps once we've had a chance to look into it.
