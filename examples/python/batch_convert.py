#!/usr/bin/env python3
"""Batch-convert a folder of .skp files to .glb.

Usage:
    python batch_convert.py INPUT_DIR OUTPUT_DIR [--recursive]

Each .skp in INPUT_DIR is converted to a .glb in OUTPUT_DIR (subfolders are
mirrored with --recursive). One line is printed per file with its name, the
time taken and its face count. A file that fails is reported and skipped, so
one bad file does not stop the run. The exit code is 1 if any file failed.
"""

import argparse
from pathlib import Path
import sys
import time

from openskp import SkpFile, SkpParseError
from openskp.export import glb


def convert_one(source, target):
    """Convert one file and return its face count. Raises on failure."""
    skp = SkpFile.open(str(source))
    model = skp.parse()
    target.parent.mkdir(parents=True, exist_ok=True)
    glb.export(skp, str(target))
    return len(model.root.faces) + sum(len(d.faces) for d in model.definitions.values())


def batch_convert(input_dir, output_dir, recursive=False):
    """Convert every .skp under input_dir. Returns (succeeded, failed)."""
    pattern = "**/*" if recursive else "*"
    sources = sorted(
        p for p in input_dir.glob(pattern) if p.is_file() and p.suffix.lower() == ".skp"
    )
    succeeded = failed = 0
    for source in sources:
        relative = source.relative_to(input_dir)
        target = output_dir / relative.with_suffix(".glb")
        started = time.perf_counter()
        try:
            faces = convert_one(source, target)
        except (SkpParseError, OSError, ValueError) as error:
            failed += 1
            print(f"FAILED  {relative}: {error}", file=sys.stderr)
            continue
        succeeded += 1
        elapsed = time.perf_counter() - started
        print(f"{relative}  {elapsed:.2f}s  {faces} faces")
    return succeeded, failed


def main(argv=None):
    parser = argparse.ArgumentParser(description="Convert a folder of .skp files to .glb")
    parser.add_argument("input_dir", type=Path, help="folder containing .skp files")
    parser.add_argument("output_dir", type=Path, help="folder to write .glb files to")
    parser.add_argument(
        "-r", "--recursive", action="store_true", help="also convert files in subfolders"
    )
    args = parser.parse_args(argv)

    if not args.input_dir.is_dir():
        parser.error(f"not a folder: {args.input_dir}")

    succeeded, failed = batch_convert(args.input_dir, args.output_dir, args.recursive)
    print(f"\nDone: {succeeded} converted, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
