"""The VFF container: the embedded ZIP archive and its header integrity word.

A SketchUp file starts with two UTF-16 header string records, then 13 bytes
whose last four are an integrity word, then the ZIP archive.  The 2025 writer
emits every entry as a local header with a 20-byte ZIP64 extended-information
extra field (two zero u64s), DEFLATE for ``.dat``/``.xml`` at zlib level 1
(ZIP flag ``0x0006``) and stored DEFLATE at level 0 for images (flag
``0x0000``), no data descriptor, a DOS date of ``0x0020`` and a UTF-8 name.
The central directory repeats the fields without the extra field; XML entries
carried with flags ``0x0006`` have internal attributes 1.

The integrity word follows the SketchUp 2025/2026 rule: for the ``n`` archive
entries in archive order, take each entry's CRC-32, subtract 42 from the
odd-indexed ones, replace the value in the slot of ``meta/meta.dat`` with a
constant derived from ``ceil(n/2)`` and the last four bytes of ``meta.dat``,
lay the values out evens-first, and CRC-32 the ``meta.dat`` bytes before its
last four followed by that table.  The rule is verified against unmodified
SketchUp-saved files in ``tests/test_create_vff.py``.

The description of this rule was taken from skpwright's
``docs/SKP_FORMAT.md`` §16.15 (https://github.com/hew3d/skpwright, by Kurt
Granroth, GPL-3.0-only); the implementation below is openskp's own and was
verified against genuine SketchUp 2025 saves.  skpwright's docs also pointed
to the archive ZIP layout and ``meta/meta.dat`` fields, which were confirmed
from those saves.  No skpwright code is used or included; the skpwright
binary is only an optional external verifier (``SKPWRIGHT_BIN``).
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from typing import List, Sequence

__all__ = [
    "ZIP_LOCAL",
    "ZipEntry",
    "read_zip",
    "build_zip",
    "integrity_word",
    "stored_integrity_word",
    "verify_integrity",
    "build_skp",
    "decompress_entry",
]

ZIP_LOCAL = b"PK\x03\x04"
ZIP_CENTRAL = b"PK\x01\x02"
ZIP_END = b"PK\x05\x06"

_LOCAL = struct.Struct("<IHHHHHIIIHH")
_CENTRAL = struct.Struct("<IHHHHHHIIIHHHHHII")
_END = struct.Struct("<IHHHHIIH")

#: Local header version needed (4.5, ZIP64) as every SketchUp 2025 entry says.
_VERSION_NEEDED = 45
#: Central directory version needed as SketchUp writes it.
_CENTRAL_VERSION_NEEDED = 20
#: DOS date 1980-01-01, what SketchUp writes (time 0).
_DOS_DATE = 0x0020
#: The 20-byte ZIP64 extra: tag 0x0001, length 16, two zero u64s.
_ZIP64_EXTRA = b"\x01\x00\x10\x00" + b"\x00" * 16


@dataclass
class ZipEntry:
    """One archive member: name, uncompressed bytes, and its ZIP flags."""

    name: str
    data: bytes
    flag: int = 0x0006
    #: 1 for an ``.xml`` entry carried with flags 0x0006, else 0.
    internal_attr: int = field(default=0)

    def __post_init__(self) -> None:
        if self.internal_attr == 0 and self.flag != 0 and self.name.endswith(".xml"):
            self.internal_attr = 1

    @property
    def level(self) -> int:
        """zlib level matching the flag word SketchUp uses."""
        if self.flag == 0x0002:
            return 9
        if self.flag == 0x0000:
            return 0
        return 1


def _inflate(data: bytes) -> bytes:
    return zlib.decompress(data, -15)


def decompress_entry(raw: bytes, method: int, crc: int) -> bytes:
    if method == 0:
        return raw
    if method == 8:
        return _inflate(raw)
    raise ValueError(f"unsupported ZIP compression method {method}")


def read_zip(data: bytes, offset: int) -> List[ZipEntry]:
    """Read the local-header chain of the archive starting at *offset*."""
    entries: List[ZipEntry] = []
    pos = offset
    while data[pos : pos + 4] == ZIP_LOCAL:
        if pos + _LOCAL.size > len(data):
            raise ValueError("truncated ZIP local header")
        (
            signature,
            version,
            flag,
            method,
            mod_time,
            mod_date,
            crc,
            compressed_size,
            uncompressed_size,
            name_length,
            extra_length,
        ) = _LOCAL.unpack_from(data, pos)
        name_start = pos + _LOCAL.size
        name = data[name_start : name_start + name_length].decode("utf-8")
        body_start = name_start + name_length + extra_length
        body_end = body_start + compressed_size
        if body_end > len(data):
            raise ValueError(f"truncated ZIP entry {name!r}")
        body = decompress_entry(data[body_start:body_end], method, crc)
        if zlib.crc32(body) & 0xFFFFFFFF != crc:
            raise ValueError(f"CRC mismatch in ZIP entry {name!r}")
        entries.append(
            ZipEntry(
                name=name,
                data=body,
                flag=flag,
                internal_attr=1 if (flag != 0 and name.endswith(".xml")) else 0,
            )
        )
        pos = body_end
    if not entries:
        raise ValueError("no ZIP archive found")
    return entries


def _deflate(data: bytes, level: int) -> bytes:
    compressor = zlib.compressobj(level, zlib.DEFLATED, -15)
    return compressor.compress(data) + compressor.flush()


def build_zip(entries: Sequence[ZipEntry]) -> bytes:
    """Encode *entries* in the layout SketchUp 2025 writes."""
    out = bytearray()
    central = bytearray()
    for entry in entries:
        name = entry.name.encode("utf-8")
        compressed = _deflate(entry.data, entry.level)
        crc = zlib.crc32(entry.data) & 0xFFFFFFFF
        offset = len(out)
        out += _LOCAL.pack(
            int.from_bytes(ZIP_LOCAL, "little"),
            _VERSION_NEEDED,
            entry.flag,
            8,
            0,
            _DOS_DATE,
            crc,
            len(compressed),
            len(entry.data),
            len(name),
            len(_ZIP64_EXTRA),
        )
        out += name
        out += _ZIP64_EXTRA
        out += compressed
        central += _CENTRAL.pack(
            int.from_bytes(ZIP_CENTRAL, "little"),
            0,
            _CENTRAL_VERSION_NEEDED,
            entry.flag,
            8,
            0,
            _DOS_DATE,
            crc,
            len(compressed),
            len(entry.data),
            len(name),
            0,
            0,
            0,
            entry.internal_attr,
            0,
            offset,
        )
        central += name
    end_offset = len(out)
    out += central
    out += _END.pack(
        int.from_bytes(ZIP_END, "little"), 0, 0, len(entries), len(entries),
        len(central), end_offset, 0,
    )
    return bytes(out)


# ── Integrity word ───────────────────────────────────────────────────────

_POLYNOMIAL = 0xEDB88320
_CRC_TABLE: List[int] = []


def _crc_table() -> List[int]:
    """The reflected CRC-32 lookup table (polynomial 0xEDB88320)."""
    if not _CRC_TABLE:
        for index in range(256):
            value = index
            for _ in range(8):
                value = (value >> 1) ^ _POLYNOMIAL if value & 1 else value >> 1
            _CRC_TABLE.append(value)
    return _CRC_TABLE


def _crc_register(data: bytes, start: int = 0) -> int:
    """Reflected CRC-32 register after *data*: no pre- or post-inversion."""
    table = _crc_table()
    register = start & 0xFFFFFFFF
    for byte in data:
        register = (register >> 8) ^ table[(register ^ byte) & 0xFF]
    return register


def _rewind_register_bit(register: int, bit: int) -> int:
    """Invert one reflected CRC-32 bit step.

    Forward, a bit ``b`` turns register ``r`` into
    ``r' = (r >> 1) ^ (poly if (r ^ b) & 1 else 0)``.  Because the
    polynomial's top bit is set, ``r' >> 31`` is exactly the flag that says
    whether the polynomial was xored, so the previous register is
    ``((r' ^ poly*flag) << 1) | (flag ^ b)``.
    """
    flag = register >> 31
    shifted = (register ^ (_POLYNOMIAL if flag else 0)) << 1
    return (shifted & 0xFFFFFFFF) | (flag ^ bit)


def _rewind_register_byte(register: int, byte: int) -> int:
    # The forward byte step consumes bits least-significant first, so the
    # inverse has to put them back most-significant first.
    for shift in range(7, -1, -1):
        register = _rewind_register_bit(register, (byte >> shift) & 1)
    return register


def _history_constant(k: int) -> int:
    """``V(k)`` for ``k = ceil(entries / 2)``.

    Derived from the step as described in SketchUp's format notes: start at
    ``V(1) = 0xFFFFFFFF``, ``V(2) = 0``, and each later value is the
    complement of the previous one run *backwards* through four zero bytes of
    the reflected CRC-32 register.  Instead of tabulating that inverse, the
    register is rewound one bit at a time with ``_rewind_register_bit``; eight
    inverse bits undo one byte and four bytes undo one step.  The resulting
    constants are checked against values solved out of the stored words of
    genuine SketchUp saves in the test suite.
    """
    if k < 1:
        raise ValueError("k must be positive")
    if k == 1:
        return 0xFFFFFFFF
    value = 0
    remaining = k - 2
    while remaining > 0:
        value = _rewind_register_byte(
            _rewind_register_byte(
                _rewind_register_byte(
                    _rewind_register_byte(value ^ 0xFFFFFFFF, 0), 0
                ),
                0,
            ),
            0,
        )
        remaining -= 1
    return value


def _meta_slot_value(meta: bytes, k: int) -> int:
    # The last four bytes of meta.dat are not covered by the CRC; they reach
    # the table through this XOR instead.
    return _history_constant(k) ^ _crc_register(meta[-4:])


def integrity_word(entries: Sequence[ZipEntry]) -> int:
    """The u32 integrity word for *entries*, in archive order.

    Implemented from the description in skpwright's ``SKP_FORMAT.md``
    §16.15 and verified against genuine SketchUp 2025 saves; no skpwright
    code is used.
    """
    meta = next((e.data for e in entries if e.name == "meta/meta.dat"), None)
    if meta is None:
        raise ValueError("archive has no meta/meta.dat")
    meta_index = next(
        i for i, e in enumerate(entries) if e.name == "meta/meta.dat"
    )
    n = len(entries)
    crcs = [zlib.crc32(e.data) & 0xFFFFFFFF for e in entries]
    words = [
        crc if index % 2 == 0 else (crc - 42) & 0xFFFFFFFF
        for index, crc in enumerate(crcs)
    ]
    words[meta_index] = _meta_slot_value(meta, (n + 1) // 2)
    table = b"".join(struct.pack("<I", word) for word in words[0::2] + words[1::2])
    return zlib.crc32(meta[:-4] + table) & 0xFFFFFFFF


def _header_word_offset(data: bytes) -> int:
    start = data.find(b"VFF\x08\x00\x01\x00")
    if start < 0:
        raise ValueError("not a VFF container (no 13-byte header)")
    return start + 9


def stored_integrity_word(data: bytes) -> int:
    """The integrity word currently stored in the file header."""
    offset = _header_word_offset(data)
    return struct.unpack_from("<I", data, offset)[0]


def header_offset(data: bytes) -> int:
    """Offset of the 56 46 46 ... header (just before the ZIP archive)."""
    return _header_word_offset(data) - 9


def verify_integrity(data: bytes) -> bool:
    """Whether the stored header word matches the archive it describes."""
    offset = data.find(ZIP_LOCAL)
    if offset < 0:
        raise ValueError("no embedded ZIP archive")
    entries = read_zip(data, offset)
    return stored_integrity_word(data) == integrity_word(entries)


def build_skp(header: bytes, entries: Sequence[ZipEntry]) -> bytes:
    """Assemble a complete VFF file: *header* (through the revision) + ZIP.

    The four integrity bytes of ``header`` are replaced with the word for the
    final archive; everything else in the header (the two string records and
    the revision byte) is kept as the template wrote it.
    """
    if not entries or entries[-1].name != "model.dat":
        raise ValueError("model.dat must be the last archive entry")
    word = integrity_word(entries)
    offset = _header_word_offset(header)
    patched = bytearray(header)
    struct.pack_into("<I", patched, offset, word)
    return bytes(patched) + build_zip(entries)


# Convenience for tests: re-export the record helpers used by the template
# reader without making template.py part of the archive module's API.
def entries_of(data: bytes) -> List[ZipEntry]:
    offset = data.find(ZIP_LOCAL)
    if offset < 0:
        raise ValueError("no embedded ZIP archive")
    return read_zip(data, offset)