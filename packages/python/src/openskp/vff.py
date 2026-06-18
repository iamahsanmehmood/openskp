"""VFF/ZIP container handler for SketchUp files.

SketchUp ``.skp`` files are VFF containers that embed a ZIP archive at a
known offset.  The ZIP contains at minimum ``model.dat`` (the TLV binary
data) and optionally material texture/XML files.

This module validates the VFF header, locates the embedded ZIP, and
extracts the relevant entries.
"""

from __future__ import annotations

import io
import zipfile
from typing import Any, Dict


# ── VFF magic bytes ───────────────────────────────────────────────────────

_VFF_MAGIC: bytes = b"\xFF\xFE\xFF\x0E"
"""First four bytes expected in a valid SketchUp VFF container."""

_ZIP_LOCAL_HEADER: bytes = b"PK\x03\x04"
"""ZIP local-file header signature used for scanning."""


# ── Internal helpers ─────────────────────────────────────────────────────


def _find_zip_offset(data: bytes) -> int:
    """Locate the start of the embedded ZIP archive inside *data*.

    The function scans for a ``PK\\x03\\x04`` ZIP local-file header.

    Args:
        data: Raw file bytes.

    Returns:
        Byte offset of the first ZIP header.

    Raises:
        ValueError: If no ZIP signature is found.
    """
    offset = data.find(_ZIP_LOCAL_HEADER)
    if offset < 0:
        raise ValueError("No embedded ZIP archive found in the file")
    return offset


def _read_version(data: bytes) -> str:
    """Extract the SketchUp version string from the VFF header.

    The version is a UTF-16LE encoded string between FF FE FF markers
    in the file header, enclosed in braces (e.g. ``"{25.0.575}"``).

    Args:
        data: Raw file bytes.

    Returns:
        Version string (e.g. ``"{25.0.575}"``), or ``"unknown"`` if
        not found.
    """
    if len(data) < 16:
        return "unknown"

    # Find second FF FE FF marker after the initial one at offset 0
    second_marker = data.find(b'\xFF\xFE\xFF', 4)
    if second_marker > 0:
        ver_start = second_marker + 4
        ver_bytes = data[ver_start:ver_start + 200]
        ver_text = ver_bytes.decode('utf-16-le', errors='ignore')
        brace_start = ver_text.find('{')
        brace_end = ver_text.find('}')
        if brace_start >= 0 and brace_end > brace_start:
            return ver_text[brace_start:brace_end + 1]

    return "unknown"


# ── Public API ────────────────────────────────────────────────────────────


def validate_header(data: bytes) -> bool:
    """Check whether *data* begins with the VFF magic bytes.

    Args:
        data: Raw file bytes (at least 4 bytes).

    Returns:
        ``True`` if the header matches a SketchUp VFF container.
    """
    return data[:4] == _VFF_MAGIC


def extract_skp_contents(data: bytes) -> Dict[str, Any]:
    """Extract ``model.dat`` and material files from a ``.skp`` byte buffer.

    Args:
        data: Complete ``.skp`` file contents.

    Returns:
        A dict with the following keys:

        * ``"version"`` — integer file-format version.
        * ``"model_data"`` — raw bytes of the ``model.dat`` entry.
        * ``"material_files"`` — mapping of filename → bytes for every
          material-related entry found in the ZIP.

    Raises:
        ValueError: If *data* is not a recognised SketchUp file or does
            not contain a ``model.dat`` entry.
    """
    # Allow both VFF-wrapped and bare ZIP (some exporters omit the header)
    if not validate_header(data):
        if _ZIP_LOCAL_HEADER not in data[:64]:
            raise ValueError("Not a valid SketchUp (.skp) file")

    version = _read_version(data)
    zip_offset = _find_zip_offset(data)

    zip_bytes = data[zip_offset:]
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))

    model_data: bytes = b""
    material_files: Dict[str, bytes] = {}

    for entry in zf.namelist():
        lower = entry.lower()
        if lower == "model.dat" or lower.endswith("/model.dat"):
            model_data = zf.read(entry)
        elif (
            lower.endswith(".xml")
            or lower.endswith(".png")
            or lower.endswith(".jpg")
            or lower.endswith(".jpeg")
            or "material" in lower
        ):
            material_files[entry] = zf.read(entry)

    zf.close()

    if not model_data:
        raise ValueError(
            "ZIP archive found but does not contain a model.dat entry"
        )

    return {
        "version": version,
        "model_data": model_data,
        "material_files": material_files,
    }
