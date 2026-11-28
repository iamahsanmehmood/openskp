"""Command-line interface for OpenSKP (``openskp info`` and ``openskp convert``)."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections.abc import Sequence

from . import SkpFile, SkpParseError, __version__, provenance_report

SUPPORTED_FORMATS: tuple[str, ...] = (
    ".glb",
    ".obj",
    ".stl",
    ".ply",
    ".dxf",
    ".ifc",
    ".json",
)


def _cmd_info(args: argparse.Namespace) -> int:
    """Inspect a ``.skp`` file and print summary or provenance information."""
    src = pathlib.Path(args.file)
    if getattr(args, "provenance", False):
        try:
            report = provenance_report(src)
        except (FileNotFoundError, PermissionError, ValueError, SkpParseError, OSError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        if getattr(args, "json", False):
            print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        else:
            print(report.format_text())
        return 0

    try:
        model = SkpFile.open(src).parse()
    except (FileNotFoundError, PermissionError, ValueError, SkpParseError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    all_defs = (model.root, *model.definitions.values())
    definitions_count = len(model.definitions)
    instances_count = sum(len(d.instances) for d in all_defs)
    faces_count = sum(len(d.faces) for d in all_defs)
    tags_count = len(model.layers)
    materials_count = len(model.materials)

    tag_names = [layer.name for layer in model.layers]
    material_names = [mat.name for mat in model.materials]

    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "file": str(src),
                    "version": model.version.strip("{}"),
                    "units": model.units or "unknown",
                    "definitions": definitions_count,
                    "instances": instances_count,
                    "faces": faces_count,
                    "tags": tags_count,
                    "materials": materials_count,
                    "tag_list": tag_names,
                    "material_list": material_names,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    rows = [
        ("File:", str(src)),
        ("Version:", model.version.strip("{}")),
        ("Units:", model.units or "unknown"),
        ("Definitions:", str(definitions_count)),
        ("Instances:", str(instances_count)),
        ("Faces:", str(faces_count)),
        ("Tags:", str(tags_count)),
        ("Materials:", str(materials_count)),
        ("Tag list:", ", ".join(tag_names) if tag_names else "(none)"),
        ("Material list:", ", ".join(material_names) if material_names else "(none)"),
    ]
    for label, value in rows:
        print(f"{label:<15} {value}")
    return 0


def _cmd_convert(args: argparse.Namespace) -> int:
    """Convert a ``.skp`` file to one of the supported output formats."""
    src = pathlib.Path(args.file)
    dst = pathlib.Path(args.output)
    ext = dst.suffix.lower()

    if not ext or ext not in SUPPORTED_FORMATS:
        supported = ", ".join(SUPPORTED_FORMATS)
        if not ext:
            print(
                f"Error: Output path '{dst}' has no file extension. "
                f"Supported formats: {supported}",
                file=sys.stderr,
            )
        else:
            print(
                f"Error: Unsupported output format '{ext}'. "
                f"Supported formats: {supported}",
                file=sys.stderr,
            )
        return 1

    try:
        if ext == ".glb":
            from .export import glb

            skp = SkpFile.open(src)
            skp.parse()
            glb.export(skp, str(dst))
        elif ext == ".json":
            from .export import json_export

            skp = SkpFile.open(src)
            model = skp.parse()
            scene = skp.build_scene()
            json_export.export(model, dst, scene=scene)
        else:
            from .export import dxf, ifc, obj, ply, stl

            scene = SkpFile.open(src).build_scene()
            if ext == ".obj":
                obj.export(scene, dst)
            elif ext == ".stl":
                stl.export(scene, dst)
            elif ext == ".ply":
                ply.export(scene, dst)
            elif ext == ".dxf":
                dxf.export(scene, dst)
            elif ext == ".ifc":
                ifc.export(scene, dst)
    except (FileNotFoundError, PermissionError, ValueError, SkpParseError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Converted {src} -> {dst}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Create and configure the top-level ``openskp`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="openskp",
        description="Inspect and convert SketchUp (.skp) files.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"openskp {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    info_parser = subparsers.add_parser(
        "info",
        help="Show metadata, entity counts, tags, and materials for a .skp file",
    )
    info_parser.add_argument("file", help="Path to the input .skp file")
    info_parser.add_argument(
        "--provenance",
        action="store_true",
        help="Report stored provenance records and derived path summary",
    )
    info_parser.add_argument(
        "--json",
        action="store_true",
        help="Format output as JSON",
    )
    info_parser.set_defaults(func=_cmd_info)

    convert_parser = subparsers.add_parser(
        "convert",
        help=(
            "Convert a .skp file to a target format "
            f"({', '.join(SUPPORTED_FORMATS)})"
        ),
    )
    convert_parser.add_argument("file", help="Path to the input .skp file")
    convert_parser.add_argument(
        "output",
        help=(
            "Path to the output file (format inferred from extension: "
            f"{', '.join(SUPPORTED_FORMATS)})"
        ),
    )
    convert_parser.set_defaults(func=_cmd_convert)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the OpenSKP command-line interface."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    args_list = list(sys.argv[1:] if argv is None else argv)
    if (
        len(args_list) == 1
        and not args_list[0].startswith("-")
        and args_list[0] not in ("info", "convert")
    ):
        args_list = ["info", args_list[0]]

    parser = build_parser()
    args = parser.parse_args(args_list)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
