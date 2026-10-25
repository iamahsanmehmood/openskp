"""Snapshot and compare every parsed field across parser revisions.

Run with the reference checkout on PYTHONPATH first, then the candidate::

    python examples/python/compare_parse.py snapshot model.skp reference.pickle
    python examples/python/compare_parse.py compare model.skp reference.pickle

Snapshots include both the public model and the raw builders (attributes,
curves, texture bytes, etc.). They contain private model data: keep them outside
the repository. Only load snapshots you created yourself: pickle is executable.
One definition is loaded at a time so comparison does not retain two models.
"""

import argparse
import dataclasses
import pickle
import struct

from openskp import SkpFile
from openskp._core import _TlvNode


def fields(value):
    """Expose fields without copying the entire model with asdict()."""
    if isinstance(value, _TlvNode):
        return {name: getattr(value, name) for name in
                ("offset", "tag", "size", "children", "payload")}
    if dataclasses.is_dataclass(value):
        return {f.name: getattr(value, f.name) for f in dataclasses.fields(value)}
    if hasattr(value, "__dict__"):
        return vars(value)
    return value


def compare(expected, actual, path="model"):
    """Compare types, ordered mappings, sequences and exact floating bits."""
    if type(expected) is not type(actual):
        raise AssertionError(f"{path}: {type(expected)} != {type(actual)}")
    expected, actual = fields(expected), fields(actual)
    if isinstance(expected, dict):
        compare(list(expected), list(actual), path + ".keys")
        for key in expected:
            compare(expected[key], actual[key], f"{path}[{key!r}]")
    elif isinstance(expected, (list, tuple)):
        if len(expected) != len(actual):
            raise AssertionError(f"{path}: length {len(expected)} != {len(actual)}")
        for index, (left, right) in enumerate(zip(expected, actual)):
            compare(left, right, f"{path}[{index}]")
    elif isinstance(expected, float):
        if struct.pack("!d", expected) != struct.pack("!d", actual):
            raise AssertionError(f"{path}: floating-point bits differ")
    elif expected != actual:
        raise AssertionError(f"{path}: values differ")


def sections(skp, model):
    for name, value in vars(model).items():
        if name == "definitions":
            yield "model.definitions.keys", list(value)
            for key, definition in value.items():
                yield f"model.definitions[{key!r}]", definition
        else:
            yield "model." + name, value
    for name, value in skp._parsed.items():
        if name == "defs_dict":
            yield "raw.defs_dict.keys", list(value)
            for key, definition in value.items():
                yield f"raw.defs_dict[{key!r}]", definition
        else:
            yield "raw." + name, value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("snapshot", "compare"))
    parser.add_argument("model")
    parser.add_argument("snapshot")
    args = parser.parse_args()
    skp = SkpFile.open(args.model)
    model = skp.parse()
    with open(args.snapshot, "wb" if args.mode == "snapshot" else "rb") as stream:
        for path, actual in sections(skp, model):
            if args.mode == "snapshot":
                pickle.dump((path, actual), stream, protocol=5)
            else:
                saved_path, expected = pickle.load(stream)
                assert path == saved_path, (path, saved_path)
                compare(expected, actual, path)
        if args.mode == "compare":
            assert not stream.read(1), "Unexpected trailing sections"
    print(f"{args.mode}: PASS {args.model}", flush=True)


if __name__ == "__main__":
    main()
