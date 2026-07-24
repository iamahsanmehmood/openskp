"""Structured parse errors.

:class:`SkpParseError` carries *where* a parse failed - which stage, which
top-level record (and how many total), which TLV tag, which byte offset,
which definition - so a stuck or failed model in a production pipeline can
be traced back to an exact location instead of a bare stack trace pointing
at a dict lookup deep in ``_core.py``.

Raised by wrapping (``raise SkpParseError(...) from exc``), so the original
exception and its traceback are always preserved as ``__cause__`` -
inspecting the failure never loses information, it only adds context.
"""

from __future__ import annotations

from typing import Optional


class SkpParseError(Exception):
    """A parse failure with structured location context.

    Attributes:
        stage: Which pipeline stage was running (e.g. ``"header"``,
            ``"zip_extract"``, ``"materials"``, ``"tlv_walk"``,
            ``"build_scene"``).
        record_index: Index of the top-level record being processed when
            the failure happened (0-based), or ``None`` outside the
            per-record walk.
        total_records: Total top-level record count for the file, paired
            with ``record_index`` for a "N of M" position.
        tag: The TLV tag hex string of the record being processed, if
            known.
        offset: Byte offset into ``model.dat`` (or the legacy archive
            stream) of the record being processed, if known.
        definition_id: The component definition id being built when the
            failure happened, if applicable.
    """

    def __init__(
        self,
        message: str,
        *,
        stage: Optional[str] = None,
        record_index: Optional[int] = None,
        total_records: Optional[int] = None,
        tag: Optional[str] = None,
        offset: Optional[int] = None,
        definition_id: object = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.record_index = record_index
        self.total_records = total_records
        self.tag = tag
        self.offset = offset
        self.definition_id = definition_id

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.stage is not None:
            parts.append(f"stage={self.stage}")
        if self.record_index is not None and self.total_records is not None:
            parts.append(f"record={self.record_index}/{self.total_records}")
        if self.tag is not None:
            parts.append(f"tag={self.tag}")
        if self.offset is not None:
            parts.append(f"offset=0x{self.offset:X}")
        if self.definition_id is not None:
            parts.append(f"definition_id={self.definition_id}")
        return " | ".join(parts)
