"""The package ships a ``py.typed`` marker (openskp#572).

Without it mypy and pyright treat an installed openskp as untyped and ignore
its type hints. ``pyproject.toml`` lists the marker in ``package-data`` so it
lands in the wheel next to ``__init__.py``.
"""
from __future__ import annotations

import pathlib

import openskp


def test_package_has_py_typed_marker():
    assert (pathlib.Path(openskp.__file__).parent / "py.typed").is_file()
