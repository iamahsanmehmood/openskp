"""Create native VFF (SketchUp 2021+) ``.skp`` files from a SketchUp template.

This module writes the VFF container SketchUp has used since 2021 by
extending a SketchUp-written template's ``model.dat`` record tree instead of
patching fixed byte offsets.  It starts with the core: the container, the
template loader and the empty-document template.  Geometry, materials and
other entities are added by the rest of this writer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from ._vff_writer.archive import (
    integrity_word,
    read_zip,
    stored_integrity_word,
    verify_integrity,
)
from ._vff_writer.template import Template
from .create import SkpWriteError

__all__ = [
    "VffBuilder",
    "create",
    "create_vff",
    "compute_integrity_word",
    "read_stored_integrity_word",
    "check_integrity",
    "DEFAULT_TEMPLATE",
]

DEFAULT_TEMPLATE = Path(__file__).resolve().parent / "_scaffold" / "blank_vff.skp"

TemplateSource = Union[str, "Path", bytes, bytearray]


def compute_integrity_word(data: bytes) -> int:
    """The integrity word the archive inside *data* implies."""
    try:
        offset = data.find(b"PK\x03\x04")
        if offset < 0:
            raise ValueError("not a VFF file: no embedded ZIP archive")
        return integrity_word(read_zip(data, offset))
    except ValueError as exc:
        raise SkpWriteError(str(exc)) from exc


def read_stored_integrity_word(data: bytes) -> int:
    """The integrity word stored in *data*'s 13-byte archive header."""
    try:
        return stored_integrity_word(data)
    except ValueError as exc:
        raise SkpWriteError(str(exc)) from exc


def check_integrity(data: bytes) -> bool:
    """Whether *data*'s stored header word matches its archive."""
    try:
        return verify_integrity(data)
    except ValueError as exc:
        raise SkpWriteError(str(exc)) from exc


class VffBuilder:
    """Template-based VFF writer.  See the module docstring for the scope."""

    def __init__(
        self,
        template: Optional[TemplateSource] = None,
        *,
        options: Optional[dict] = None,
    ) -> None:
        if template is None:
            data = DEFAULT_TEMPLATE.read_bytes()
        elif isinstance(template, (bytes, bytearray)):
            data = bytes(template)
        else:
            try:
                data = Path(template).read_bytes()
            except OSError as exc:
                raise SkpWriteError(f"cannot read VFF template {template!r}: {exc}") from exc
        try:
            self._template = Template(data, options)
        except ValueError as exc:
            raise SkpWriteError(f"cannot use VFF template: {exc}") from exc

    def to_bytes(self) -> bytes:
        try:
            return self._template.build()
        except ValueError as exc:
            raise SkpWriteError(str(exc)) from exc

    def save(self, path: str) -> None:
        """Write the finished file to ``path``."""
        Path(path).write_bytes(self.to_bytes())


def create(
    template: Optional[TemplateSource] = None,
    *,
    options: Optional[dict] = None,
) -> VffBuilder:
    """Start building a new VFF file (the ``create.py`` entry-point name)."""
    return VffBuilder(template, options=options)


def create_vff(
    template: Optional[TemplateSource] = None,
    *,
    options: Optional[dict] = None,
) -> VffBuilder:
    """Open a new VFF model built on *template* (the bundled blank by default)."""
    return create(template, options=options)
