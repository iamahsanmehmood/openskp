#!/usr/bin/env python3
"""Regenerate every *.skp file in this directory from the C++ writer, in one command.

    python packages/rust/tests/fixtures/create_reference/regenerate.py
    cd packages/rust && cargo test --release --test create_reference_test

The files are what the reference C++ writer (packages/cpp/src/create.cpp, as it is in this
checkout) produces for the scenarios in oracle_driver.cpp. tests/create_reference_test.rs replays
the same scenarios through the Rust port and asserts byte equality, so after a C++ writer change
you run this, then the test, and port whatever the test shows differs.

What it does:
  1. Copies create.cpp to a temporary directory and makes it reproducible, the same way
     `SkpBuilder::use_deterministic_ids(1700000000)` does on the Rust side: GUID n (a global
     counter `oracle_guid_counter`, reset per scenario by the driver) is bytes (n*16 + i) & 0xFF
     with the v4 version/variant bits applied, and `std::time(nullptr)` becomes 1700000000.
     Each patch must match exactly once, so if create.cpp changes shape the script stops instead
     of writing non-reproducible files.
  2. Adds an `openskp/export.hpp` defining an empty OPENSKP_EXPORT (CMake generates the real one).
  3. Builds the patched create.cpp with oracle_driver.cpp. On Windows it uses MSVC (found with
     vswhere); elsewhere `c++`. `--cxx` picks a compiler explicitly (g++, clang++ or cl).
  4. Runs the driver from packages/rust (texture paths are relative and embedded verbatim in the
     files) and writes the files here. Any scenario that fails makes the script fail.

Only the standard library is used. Pass `--out DIR` to write somewhere else and compare.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CRATE = HERE.parents[2]  # packages/rust
REPO = CRATE.parents[1]
CPP = REPO / "packages" / "cpp"
TIMESTAMP = 1700000000

# (description, pattern, replacement): each pattern must match exactly once in create.cpp.
PATCHES = [
    (
        "define the GUID counter the driver resets per scenario",
        r'(#include "scaffold_blank_v17\.hpp"\n)',
        r"\1\nstd::uint32_t oracle_guid_counter = 0;\n",
    ),
    (
        "make random_uuid_bytes() deterministic",
        r"(std::array<std::uint8_t, 16> random_uuid_bytes\(\) \{\n)"
        r"  static thread_local std::mt19937_64 rng\{std::random_device\{\}\(\)\};\n"
        r"  std::uniform_int_distribution<int> dist\(0, 255\);\n"
        r"  std::array<std::uint8_t, 16> b\{\};\n"
        r"  for \(auto& x : b\) x = static_cast<std::uint8_t>\(dist\(rng\)\);\n",
        r"\1"
        r"  const std::uint32_t n = ::oracle_guid_counter++;\n"
        r"  std::array<std::uint8_t, 16> b{};\n"
        r"  for (std::uint32_t i = 0; i < 16; ++i) b[i] = static_cast<std::uint8_t>((n * 16 + i) & 0xFF);\n",
    ),
    (
        "fix the definition timestamp",
        r"std::time\(nullptr\)",
        str(TIMESTAMP),
    ),
]


def patch_create_cpp(source: str) -> str:
    for what, pattern, replacement in PATCHES:
        source, count = re.subn(pattern, replacement, source)
        if count != 1:
            sys.exit(
                f"regenerate.py: cannot {what}: the pattern matched {count} times in "
                f"packages/cpp/src/create.cpp (expected 1). The C++ writer changed shape; "
                f"update PATCHES in this script."
            )
    return source


def find_vcvars() -> Path | None:
    roots = [os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")]
    for root in filter(None, roots):
        vswhere = Path(root) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
        if vswhere.is_file():
            out = subprocess.run(
                [str(vswhere), "-latest", "-products", "*", "-property", "installationPath"],
                capture_output=True,
                text=True,
            ).stdout.strip()
            if out:
                vcvars = Path(out) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
                if vcvars.is_file():
                    return vcvars
    return None


def build(work: Path, create_cpp: Path, cxx: str | None) -> Path:
    driver = HERE / "oracle_driver.cpp"
    gen_include = work / "include"
    includes = [gen_include, CPP / "include", CPP / "src"]
    if cxx is None:
        cxx = "cl" if sys.platform == "win32" else "c++"

    if Path(cxx).stem.lower() == "cl":
        exe = work / "oracle_driver.exe"
        flags = ["/nologo", "/std:c++17", "/EHsc", "/O2", "/utf-8"]
        flags += [f"/I{p}" for p in includes]
        cmd = [cxx, *flags, str(create_cpp), str(driver), f"/Fe{exe}"]  # objects go to cwd
        if shutil.which(cxx) is None:
            vcvars = find_vcvars()
            if vcvars is None:
                sys.exit("regenerate.py: no MSVC found (cl is not on PATH and vswhere found no "
                         "Visual Studio with C++ tools). Use --cxx to pick another compiler.")
            line = subprocess.list2cmdline(cmd)
            # A string, not a list: cmd.exe must see the quotes as written (/s strips the outer
            # pair only).
            result = subprocess.run(f'cmd /d /s /c "call "{vcvars}" >nul && {line}"', cwd=work)
        else:
            result = subprocess.run(cmd, cwd=work)
    else:
        exe = work / ("oracle_driver.exe" if sys.platform == "win32" else "oracle_driver")
        cmd = [cxx, "-std=c++17", "-O2", *[f"-I{p}" for p in includes],
               str(create_cpp), str(driver), "-o", str(exe)]
        result = subprocess.run(cmd, cwd=work)

    if result.returncode != 0 or not exe.is_file():
        sys.exit("regenerate.py: building the oracle driver failed")
    return exe


def digests(folder: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.glob("*.skp")}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cxx", help="C++ compiler: cl, g++, clang++, ... (default: cl on Windows, "
                                  "c++ elsewhere)")
    ap.add_argument("--out", type=Path, default=HERE,
                    help="where to write the .skp files (default: this directory)")
    args = ap.parse_args()

    if not (CPP / "src" / "create.cpp").is_file():
        sys.exit(f"regenerate.py: {CPP / 'src' / 'create.cpp'} not found")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    before = digests(out)

    with tempfile.TemporaryDirectory(prefix="openskp-oracle-") as tmp:
        work = Path(tmp)
        (work / "include" / "openskp").mkdir(parents=True)
        (work / "include" / "openskp" / "export.hpp").write_text(
            "#pragma once\n#define OPENSKP_EXPORT\n", encoding="utf-8")
        create_cpp = work / "create.cpp"
        source = (CPP / "src" / "create.cpp").read_text(encoding="utf-8")
        create_cpp.write_text(patch_create_cpp(source), encoding="utf-8", newline="\n")

        exe = build(work, create_cpp, args.cxx)
        run = subprocess.run([str(exe), str(out)], cwd=CRATE, capture_output=True, text=True)

    sys.stdout.write(run.stdout)
    sys.stderr.write(run.stderr)
    failed = [line for line in run.stdout.splitlines() if " ERROR " in line]
    if run.returncode != 0 or failed:
        sys.exit(f"regenerate.py: the driver failed ({len(failed)} scenario(s) reported ERROR)")

    after = digests(out)
    changed = sorted(n for n in after if n in before and before[n] != after[n])
    new = sorted(set(after) - set(before))
    stale = sorted(set(before) - set(after))
    print(f"\n{len(after)} files written to {out}: {len(changed)} changed, {len(new)} new")
    for label, names in (("Changed", changed), ("New", new)):
        if names:
            print(f"{label}: {', '.join(names)}")
    if stale:
        print(f"Not produced by the driver any more (delete them if intended): {', '.join(stale)}")


if __name__ == "__main__":
    main()
