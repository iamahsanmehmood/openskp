"""File provenance report for SketchUp ``.skp`` files (#731).

Extracts what a ``.skp`` file (2021+ VFF or classic MFC SketchUp 3–2020)
records about where it came from — each value paired with the exact binary
record, TLV tag path, or ZIP entry it was read from and the entity it belongs
to — and derives user-folder names, drive letters, network shares, and implied
operating systems from the stored file paths.

The report records only what is stored in the file and never guesses authorship
or history beyond what the bytes state.
"""

from __future__ import annotations

import datetime
import math
import os
import pathlib
import re
import struct
import uuid
import zipfile
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import unquote

import defusedxml.ElementTree as ET
from defusedxml.common import DefusedXmlException

from ._core import (
    _PROP_CONTAINER_TAGS,
    _extract_raw_material_xml_name,
    _find_tag,
    _validate_zip_entry_size,
    extract_attribute_dictionaries,
    iter_top_level_lazy,
    parse_tlv_recursive,
    parse_var_int,
    read_u32,
)
from .errors import SkpParseError
from .legacy import (
    LegacyParseError,
    _Archive,
    _PAGE_DECL_RE,
    _PageClasses,
    _R,
    _READERS,
    _STR_MARKER,
    _STYLE_VALUE_SIZES,
    _extract_legacy_attribute_dictionaries,
    _read_page_record,
    _read_tag,
    _scan_pages,
    _skip_pid,
    _walk,
    is_legacy,
)

__all__ = [
    "ProvenanceSource",
    "ProvenanceItem",
    "DerivedPathSummary",
    "ProvenanceReport",
    "provenance_report",
]

# IFC / SketchUp 22-character Base64 alphabet used by SUComponentDefinitionGetGuid.
_IFC_GUID_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$"

# Attribute dictionaries written by SketchUp's own C++ core rather than third-party
# plugins or extensions. GeoReference, GSU_ContributorsInfo, and 3DWarehouse have
# dedicated provenance kinds; ModelProperties is SketchUp's internal model-info
# record (reported via model_name / model_description).
_SKETCHUP_BUILTIN_DICTS = frozenset(
    {
        "ModelProperties",
        "GeoReference",
        "GSU_ContributorsInfo",
        "3DWarehouse",
        "temp",
        "TempShadowInfo",
        "SU_DefinitionSet",
        "SU_InstanceSet",
        "AppliedSchemaTypes",
    }
)

_PATH_KINDS = frozenset(
    {
        "model_path",
        "texture_path",
        "definition_path",
        "image_path",
        "style_path",
    }
)

# Container tags walked eagerly in VFF model.dat for provenance. Heavy geometry
# pools (8913 vertices, 8A13 edges, 8B13 faces, 9113/9213 guides, 9613/9713
# curves) are deliberately left as leaf slices and entered only when needed
# (is_image face material lookup, or rare B436 dictionaries on faces/edges).
_VFF_PROV_CONTAINERS = frozenset(
    {
        "F401",
        "F701",
        "D430",
        "D530",
        "C832",
        "F801",
        "983A",
        "993A",
        "8C3C",
        "F901",
        "7017",
        "7117",
        "7C15",
        "F601",
        "8813",
        "8C13",
        "8D13",
        "4C1D",
        "9013",
        "401F",
        "6419",
        "D007",
        "9313",
        "5C44",
        "9813",
        "F055",
        "9913",
        "CC5B",
        "D859",
        "0402",
        "9065",
        "0602",
        "7869",
        "7969",
        "7B69",
        "6C6B",
        "0702",
        "606D",
        "616D",
        "4871",
        "546F",
    }
)

_ASSET_EXTENSIONS = frozenset(
    {
        "png",
        "jpg",
        "jpeg",
        "gif",
        "bmp",
        "tif",
        "tiff",
        "webp",
        "svg",
        "hdr",
        "exr",
        "dds",
        "tga",
        "skp",
        "skb",
        "style",
        "layout",
        "dxf",
        "dwg",
        "ifc",
        "glb",
        "gltf",
        "obj",
        "mtl",
        "stl",
        "ply",
        "fbx",
        "3ds",
        "dae",
        "pdf",
        "zip",
    }
)

_URL_RE = re.compile(
    r"(?:https?://[^\s<>\"')\]}]+|(?<![A-Za-z0-9@:/\\])www\.[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?:/[^\s<>\"')\]}]*)?)",
    re.IGNORECASE,
)
_EMAIL_LOCAL_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._%+-"
)
_EMAIL_DOMAIN_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.-"
)

_WIN_USERS_RE = re.compile(r"^[A-Za-z]:[\\/]Users[\\/]([^\\/]+)(?:[\\/]|$)", re.IGNORECASE)
_WIN_DOCSET_RE = re.compile(
    r"^[A-Za-z]:[\\/]Documents and Settings[\\/]([^\\/]+)(?:[\\/]|$)", re.IGNORECASE
)
_MAC_USERS_RE = re.compile(r"^/Users/([^/]+)(?:/|$)")
_LINUX_HOME_RE = re.compile(r"^/home/([^/]+)(?:/|$)")
_DRIVE_RE = re.compile(r"^([A-Za-z]):(?:[\\/]|$)")
_UNC_BACKSLASH_RE = re.compile(r"^\\\\([^\\/\s]+)\\([^\\/\s]+)")
_UNC_SLASH_RE = re.compile(r"^//([^/\s]+)/([^/\s]+)")


def _utc_iso(ts: int) -> str:
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _win16_to_uuid_str(raw16: bytes) -> str:
    return str(uuid.UUID(bytes_le=raw16))


def _win16_to_ifc_guid(raw16: bytes) -> str:
    big16 = uuid.UUID(bytes_le=raw16).bytes
    num = int.from_bytes(big16, "big")
    return "".join(
        _IFC_GUID_CHARS[(num >> (i * 6)) & (0x3 if i == 21 else 0x3F)]
        for i in range(21, -1, -1)
    )


def _hex32_to_ifc_guid(hex32: str) -> str:
    big16 = bytes.fromhex(hex32)
    num = int.from_bytes(big16, "big")
    return "".join(
        _IFC_GUID_CHARS[(num >> (i * 6)) & (0x3 if i == 21 else 0x3F)]
        for i in range(21, -1, -1)
    )


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


@dataclass(frozen=True)
class ProvenanceSource:
    """Where a provenance item was read from (or derived from)."""

    record: str
    entity_type: str
    entity_name: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {
            "record": self.record,
            "entity_type": self.entity_type,
            "entity_name": self.entity_name,
        }

    def format_short(self) -> str:
        if self.entity_name:
            return f"{self.record} | {self.entity_type} {self.entity_name!r}"
        return f"{self.record} | {self.entity_type}"


@dataclass(frozen=True)
class ProvenanceItem:
    """One stored (``derived=False``) or path-derived (``derived=True``) fact."""

    kind: str
    value: Any
    source: ProvenanceSource
    derived: bool = False

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "kind": self.kind,
            "value": _json_safe(self.value),
            "source": self.source.to_dict(),
            "derived": self.derived,
        }
        if self.kind in ("save_time", "definition_load_time") and isinstance(
            self.value, int
        ):
            out["utc"] = _utc_iso(self.value)
        return out


@dataclass(frozen=True)
class DerivedPathSummary:
    """Facts derived strictly from the file's stored path fields."""

    user_folders: Tuple[str, ...]
    distinct_user_folder_count: int
    drive_letters: Tuple[str, ...]
    distinct_drive_letter_count: int
    network_shares: Tuple[str, ...]
    distinct_network_share_count: int
    operating_systems: Tuple[str, ...]
    distinct_operating_system_count: int
    items: Tuple[ProvenanceItem, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "derived": True,
            "user_folders": list(self.user_folders),
            "distinct_user_folder_count": self.distinct_user_folder_count,
            "drive_letters": list(self.drive_letters),
            "distinct_drive_letter_count": self.distinct_drive_letter_count,
            "network_shares": list(self.network_shares),
            "distinct_network_share_count": self.distinct_network_share_count,
            "operating_systems": list(self.operating_systems),
            "distinct_operating_system_count": self.distinct_operating_system_count,
            "items": [item.to_dict() for item in self.items],
        }


@dataclass(frozen=True)
class ProvenanceReport:
    """Complete provenance report for a ``.skp`` file."""

    file: str
    format: str
    sketchup_version: str
    file_guid: str
    model_name: str
    model_description: str
    items: Tuple[ProvenanceItem, ...]
    derived: DerivedPathSummary
    unread_root_entities: int = 0

    def items_of_kind(self, kind: str) -> Tuple[ProvenanceItem, ...]:
        return tuple(item for item in self.items if item.kind == kind)

    @property
    def definition_guids(self) -> Tuple[ProvenanceItem, ...]:
        return self.items_of_kind("definition_guid")

    @property
    def save_times(self) -> Tuple[ProvenanceItem, ...]:
        return tuple(
            item
            for item in self.items
            if item.kind in ("save_time", "definition_load_time")
        )

    @property
    def save_clients(self) -> Tuple[ProvenanceItem, ...]:
        return self.items_of_kind("save_client")

    @property
    def contributor_records(self) -> Tuple[ProvenanceItem, ...]:
        return self.items_of_kind("contributor_record")

    @property
    def paths(self) -> Tuple[ProvenanceItem, ...]:
        return tuple(item for item in self.items if item.kind in _PATH_KINDS)

    @property
    def texture_paths(self) -> Tuple[ProvenanceItem, ...]:
        return self.items_of_kind("texture_path")

    @property
    def definition_paths(self) -> Tuple[ProvenanceItem, ...]:
        return self.items_of_kind("definition_path")

    @property
    def image_paths(self) -> Tuple[ProvenanceItem, ...]:
        return self.items_of_kind("image_path")

    @property
    def model_paths(self) -> Tuple[ProvenanceItem, ...]:
        return self.items_of_kind("model_path")

    @property
    def style_paths(self) -> Tuple[ProvenanceItem, ...]:
        return self.items_of_kind("style_path")

    @property
    def geolocation(self) -> Tuple[ProvenanceItem, ...]:
        return self.items_of_kind("geolocation")

    @property
    def warehouse_dictionaries(self) -> Tuple[ProvenanceItem, ...]:
        return self.items_of_kind("warehouse_dictionary")

    @property
    def plugin_dictionaries(self) -> Tuple[ProvenanceItem, ...]:
        return self.items_of_kind("plugin_dictionary")

    @property
    def email_addresses(self) -> Tuple[ProvenanceItem, ...]:
        return self.items_of_kind("email_address")

    @property
    def web_addresses(self) -> Tuple[ProvenanceItem, ...]:
        return self.items_of_kind("web_address")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file": self.file,
            "format": self.format,
            "sketchup_version": self.sketchup_version,
            "file_guid": self.file_guid,
            "model_name": self.model_name,
            "model_description": self.model_description,
            "unread_root_entities": self.unread_root_entities,
            "items": [item.to_dict() for item in self.items],
            "derived": self.derived.to_dict(),
        }

    def format_text(self) -> str:
        lines: List[str] = []
        fmt_items = self.items_of_kind("format")
        ver_items = self.items_of_kind("sketchup_version")
        guid_items = self.items_of_kind("file_guid")
        lines.append(f"File:             {self.file}")
        if fmt_items:
            lines.append(
                f"Format:           {self.format}  [{fmt_items[0].source.format_short()}]"
            )
        else:
            lines.append(f"Format:           {self.format}")
        if ver_items:
            lines.append(
                f"SketchUp version: {self.sketchup_version}  [{ver_items[0].source.format_short()}]"
            )
        else:
            lines.append(f"SketchUp version: {self.sketchup_version}")
        if guid_items:
            lines.append(
                f"File GUID:        {self.file_guid}  [{guid_items[0].source.format_short()}]"
            )
        name_items = self.items_of_kind("model_name")
        if name_items:
            for it in name_items:
                lines.append(
                    f"Model name:       {it.value!r}  [{it.source.format_short()}]"
                )
        else:
            lines.append("Model name:       (empty)")
        desc_items = self.items_of_kind("model_description")
        if desc_items:
            for it in desc_items:
                lines.append(
                    f"Model desc:       {it.value!r}  [{it.source.format_short()}]"
                )
        else:
            lines.append("Model desc:       (empty)")
        if self.unread_root_entities:
            lines.append(
                f"Unread entities:  {self.unread_root_entities} root entities were not read"
            )

        clients = self.save_clients
        if clients:
            lines.append("")
            lines.append(f"Save-history client strings ({len(clients)}):")
            for it in clients:
                lines.append(f"  - {it.value!r}  [{it.source.format_short()}]")

        times = self.save_times
        lines.append("")
        lines.append(f"Stored save/load times ({len(times)}):")
        if times:
            for it in times:
                lines.append(
                    f"  - [{it.kind}] {_utc_iso(it.value)} ({it.value})  [{it.source.format_short()}]"
                )
        else:
            lines.append("  (none)")

        def_guids = self.definition_guids
        lines.append("")
        lines.append(f"Definition GUIDs ({len(def_guids)}):")
        if def_guids:
            for it in def_guids:
                lines.append(f"  - {it.value}  [{it.source.format_short()}]")
        else:
            lines.append("  (none)")

        contribs = self.contributor_records
        lines.append("")
        lines.append(f"Contributor records (GSU_ContributorsInfo) ({len(contribs)}):")
        if contribs:
            for it in contribs:
                lines.append(f"  - {it.value}  [{it.source.format_short()}]")
        else:
            lines.append("  (none)")

        stored_paths = self.paths
        lines.append("")
        lines.append(f"Stored paths ({len(stored_paths)}):")
        if stored_paths:
            for it in stored_paths:
                lines.append(
                    f"  - [{it.kind}] {it.value}  [{it.source.format_short()}]"
                )
        else:
            lines.append("  (none)")

        d = self.derived
        lines.append("")
        lines.append("Derived from stored paths (derived):")
        uf_n = d.distinct_user_folder_count
        if uf_n == 0:
            lines.append("  0 user folders appear in stored paths.")
        elif uf_n == 1:
            lines.append("  1 user folder appears in stored paths:")
        else:
            lines.append(f"  {uf_n} different user folders appear in stored paths:")
        if uf_n > 0:
            for folder in d.user_folders:
                where = [
                    it.source.format_short()
                    for it in d.items
                    if it.kind == "derived_user_folder" and it.value == folder
                ]
                lines.append(f"    - {folder}  ({'; '.join(where)})")
        dl_n = d.distinct_drive_letter_count
        if dl_n == 0:
            lines.append("  0 drive letters appear in stored paths.")
        elif dl_n == 1:
            lines.append(
                f"  1 drive letter appears in stored paths: {d.drive_letters[0]}"
            )
        else:
            lines.append(
                f"  {dl_n} different drive letters appear in stored paths: {', '.join(d.drive_letters)}"
            )
        ns_n = d.distinct_network_share_count
        if ns_n == 0:
            lines.append("  0 network shares appear in stored paths.")
        elif ns_n == 1:
            lines.append(
                f"  1 network share appears in stored paths: {d.network_shares[0]}"
            )
        else:
            lines.append(
                f"  {ns_n} different network shares appear in stored paths: {', '.join(d.network_shares)}"
            )
        os_n = d.distinct_operating_system_count
        if os_n == 0:
            lines.append("  0 operating systems implied by stored paths.")
        elif os_n == 1:
            lines.append(
                f"  1 operating system implied by stored paths: {d.operating_systems[0]}"
            )
        else:
            lines.append(
                f"  {os_n} operating systems implied by stored paths: {', '.join(d.operating_systems)}"
            )

        geos = self.geolocation
        lines.append("")
        lines.append(f"Geo-location ({len(geos)}):")
        if geos:
            for it in geos:
                lines.append(f"  - {it.value}  [{it.source.format_short()}]")
        else:
            lines.append("  (none)")

        wh = self.warehouse_dictionaries
        lines.append("")
        lines.append(f"3D Warehouse dictionaries ({len(wh)}):")
        if wh:
            for it in wh:
                lines.append(f"  - {it.value}  [{it.source.format_short()}]")
        else:
            lines.append("  (none)")

        plugs = self.plugin_dictionaries
        lines.append("")
        lines.append(f"Plugin dictionaries ({len(plugs)}):")
        if plugs:
            for it in plugs:
                val = it.value
                lines.append(
                    f"  - {val['name']} ({val['entry_count']} entries)  [{it.source.format_short()}]"
                )
        else:
            lines.append("  (none)")

        emails = self.email_addresses
        lines.append("")
        lines.append(f"Email addresses in stored strings ({len(emails)}):")
        if emails:
            for it in emails:
                lines.append(f"  - {it.value}  [{it.source.format_short()}]")
        else:
            lines.append("  (none)")

        urls = self.web_addresses
        lines.append("")
        lines.append(f"Web addresses in stored strings ({len(urls)}):")
        if urls:
            for it in urls:
                lines.append(f"  - {it.value}  [{it.source.format_short()}]")
        else:
            lines.append("  (none)")

        return "\n".join(lines)


# ── Path derivation & string contact scanning ────────────────────────────


def _normalize_file_uri(raw_path: str) -> str:
    p = raw_path.strip()
    if p.lower().startswith("file:///"):
        rest = unquote(p[8:])
        if _DRIVE_RE.match(rest):
            return rest
        return "/" + rest
    if p.lower().startswith("file://"):
        rest = unquote(p[7:])
        if _DRIVE_RE.match(rest):
            return rest
        return "//" + rest
    return raw_path


def _derive_from_path(raw_path: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Return ``(user_folder, drive_letter, network_share, implied_os)`` for *raw_path*."""
    if not raw_path:
        return None, None, None, None
    p = _normalize_file_uri(raw_path)
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", p):
        return None, None, None, None

    user_folder: Optional[str] = None
    drive_letter: Optional[str] = None
    network_share: Optional[str] = None
    implied_os: Optional[str] = None

    m_drive = _DRIVE_RE.match(p)
    if m_drive:
        drive_letter = f"{m_drive.group(1).upper()}:"
        implied_os = "Windows"
        m_u = _WIN_USERS_RE.match(p) or _WIN_DOCSET_RE.match(p)
        if m_u:
            user_folder = m_u.group(1)
        return user_folder, drive_letter, network_share, implied_os

    m_unc = _UNC_BACKSLASH_RE.match(p) or _UNC_SLASH_RE.match(p)
    if m_unc:
        network_share = f"\\\\{m_unc.group(1)}\\{m_unc.group(2)}"
        implied_os = "Windows"
        return user_folder, drive_letter, network_share, implied_os

    if p.startswith("/"):
        m_mac = _MAC_USERS_RE.match(p)
        if m_mac:
            return m_mac.group(1), None, None, "macOS"
        m_lin = _LINUX_HOME_RE.match(p)
        if m_lin:
            return m_lin.group(1), None, None, "Linux"
        low = p.lower()
        if low.startswith(("/volumes/", "/library/", "/applications/", "/system/", "/private/")):
            return None, None, None, "macOS"
        if low.startswith(("/mnt/", "/media/", "/usr/", "/opt/", "/etc/")):
            return None, None, None, "Linux"
        return None, None, None, "POSIX"

    if "\\" in p:
        return None, None, None, "Windows"

    return None, None, None, None


def _build_derived_summary(path_items: Sequence[ProvenanceItem]) -> DerivedPathSummary:
    user_folders: List[str] = []
    drive_letters: List[str] = []
    network_shares: List[str] = []
    operating_systems: List[str] = []
    derived_items: List[ProvenanceItem] = []

    for p_item in path_items:
        if not isinstance(p_item.value, str):
            continue
        uf, dl, ns, ios = _derive_from_path(p_item.value)
        if uf is not None:
            if uf not in user_folders:
                user_folders.append(uf)
            derived_items.append(
                ProvenanceItem("derived_user_folder", uf, p_item.source, derived=True)
            )
        if dl is not None:
            if dl not in drive_letters:
                drive_letters.append(dl)
            derived_items.append(
                ProvenanceItem("derived_drive_letter", dl, p_item.source, derived=True)
            )
        if ns is not None:
            if ns not in network_shares:
                network_shares.append(ns)
            derived_items.append(
                ProvenanceItem("derived_network_share", ns, p_item.source, derived=True)
            )
        if ios is not None:
            if ios not in operating_systems:
                operating_systems.append(ios)
            derived_items.append(
                ProvenanceItem(
                    "derived_operating_system", ios, p_item.source, derived=True
                )
            )

    return DerivedPathSummary(
        user_folders=tuple(user_folders),
        distinct_user_folder_count=len(user_folders),
        drive_letters=tuple(drive_letters),
        distinct_drive_letter_count=len(drive_letters),
        network_shares=tuple(network_shares),
        distinct_network_share_count=len(network_shares),
        operating_systems=tuple(operating_systems),
        distinct_operating_system_count=len(operating_systems),
        items=tuple(derived_items),
    )


def _clean_url(raw_url: str) -> str:
    url = raw_url.rstrip(".,;:!?")
    while url.endswith(")") and url.count(")") > url.count("("):
        url = url[:-1].rstrip(".,;:!?")
    return url


def _scan_string_for_contacts(
    text: str,
    source: ProvenanceSource,
    out: List[ProvenanceItem],
    seen: set,
) -> None:
    if not text:
        return
    low = text.lower()
    has_url_hint = "http://" in low or "https://" in low or "www." in low
    has_at = "@" in text
    if not has_url_hint and not has_at:
        return

    url_spans: List[Tuple[int, int]] = []
    if has_url_hint:
        for m in _URL_RE.finditer(text):
            cleaned = _clean_url(m.group(0))
            if cleaned:
                url_spans.append((m.start(), m.start() + len(cleaned)))
                key = ("web_address", cleaned, source)
                if key not in seen:
                    seen.add(key)
                    out.append(ProvenanceItem("web_address", cleaned, source))

    if has_at:
        n = len(text)
        idx = text.find("@")
        while idx >= 0:
            l0 = idx
            while l0 > 0 and text[l0 - 1] in _EMAIL_LOCAL_CHARS:
                l0 -= 1
            r1 = idx + 1
            while r1 < n and text[r1] in _EMAIL_DOMAIN_CHARS:
                r1 += 1
            # Trim trailing dots/hyphens so sentence-ending "a@b.com." resolves cleanly
            r_trim = r1
            while r_trim > idx + 1 and text[r_trim - 1] in ".-":
                r_trim -= 1
            prev_ok = l0 == 0 or text[l0 - 1] not in ":/"
            next_ok = r_trim == n or (
                r_trim < r1 or text[r_trim] not in _EMAIL_LOCAL_CHARS
            )
            if l0 < idx and r_trim > idx + 1 and prev_ok and next_ok:
                if not any(s <= l0 < e for s, e in url_spans):
                    domain = text[idx + 1 : r_trim]
                    labels = domain.split(".")
                    if len(labels) >= 2 and all(
                        lbl and not lbl.startswith("-") and not lbl.endswith("-")
                        for lbl in labels
                    ):
                        tld = labels[-1]
                        if (
                            2 <= len(tld) <= 24
                            and tld.isalpha()
                            and tld.lower() not in _ASSET_EXTENSIONS
                        ):
                            email = text[l0:r_trim]
                            key = ("email_address", email, source)
                            if key not in seen:
                                seen.add(key)
                                out.append(ProvenanceItem("email_address", email, source))
            idx = text.find("@", max(idx + 1, r1))


def _scan_attr_value_for_contacts(
    value: Any,
    source: ProvenanceSource,
    out: List[ProvenanceItem],
    seen: set,
) -> None:
    if isinstance(value, str):
        _scan_string_for_contacts(value, source, out, seen)
    elif isinstance(value, (list, tuple)):
        for elem in value:
            _scan_attr_value_for_contacts(elem, source, out, seen)
    elif isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str):
                _scan_string_for_contacts(k, source, out, seen)
            _scan_attr_value_for_contacts(v, source, out, seen)


def _record_attribute_dictionaries(
    dicts: Dict[str, Any],
    record_fmt: str,
    entity_type: str,
    entity_name: str,
    items: List[ProvenanceItem],
    contacts: List[ProvenanceItem],
    seen_contacts: set,
) -> None:
    for dict_name, entries in dicts.items():
        if dict_name == "ModelProperties":
            continue
        rec = record_fmt.format(name=dict_name)
        src = ProvenanceSource(rec, entity_type, entity_name)
        if isinstance(entries, int):
            if dict_name not in _SKETCHUP_BUILTIN_DICTS:
                items.append(
                    ProvenanceItem(
                        "plugin_dictionary",
                        {"name": dict_name, "entry_count": entries},
                        src,
                    )
                )
            continue
        if dict_name == "GSU_ContributorsInfo":
            items.append(ProvenanceItem("contributor_record", dict(entries), src))
        elif dict_name == "GeoReference":
            items.append(
                ProvenanceItem(
                    "geolocation",
                    {"kind": "GeoReference", "entries": dict(entries)},
                    src,
                )
            )
        elif dict_name.lower() == "3dwarehouse":
            items.append(
                ProvenanceItem(
                    "warehouse_dictionary",
                    {
                        "name": dict_name,
                        "entry_count": len(entries),
                        "entries": dict(entries),
                    },
                    src,
                )
            )
        elif dict_name not in _SKETCHUP_BUILTIN_DICTS:
            items.append(
                ProvenanceItem(
                    "plugin_dictionary",
                    {"name": dict_name, "entry_count": len(entries)},
                    src,
                )
            )

        _scan_string_for_contacts(dict_name, src, contacts, seen_contacts)
        for k, v in entries.items():
            entry_src = ProvenanceSource(f"{rec}[{k!r}]", entity_type, entity_name)
            _scan_string_for_contacts(k, entry_src, contacts, seen_contacts)
            _scan_attr_value_for_contacts(v, entry_src, contacts, seen_contacts)


# ── VFF (2021+) provenance extraction ───────────────────────────────────


def _iter_flat_tlv(buf: bytes, start: int = 0, end: Optional[int] = None) -> Iterable[Tuple[str, bytes]]:
    if end is None:
        end = len(buf)
    pos = start
    while pos + 6 <= end:
        tag = buf[pos : pos + 2].hex().upper()
        sz = read_u32(buf, pos + 2)
        if pos + 6 + sz > end:
            break
        yield tag, buf[pos + 6 : pos + 6 + sz]
        pos += 6 + sz


def _entity_id_from_dc05(dc05_payload: bytes) -> Optional[int]:
    if not dc05_payload:
        return None
    if dc05_payload.startswith(b"\xde\x05") and len(dc05_payload) >= 6:
        id_len = read_u32(dc05_payload, 2)
        if 6 + id_len <= len(dc05_payload):
            return parse_var_int(dc05_payload, 6, id_len)
        return None
    return parse_var_int(dc05_payload, 0, len(dc05_payload))


_RAW_FULL_DICT_HINT_RE = re.compile(
    rb"@|https?://|www\.|GSU_ContributorsInfo|GeoReference|3DWarehouse|ModelProperties",
    re.IGNORECASE,
)


def _fast_count_b336_dicts(pl: bytes) -> Optional[Dict[str, int]]:
    """Zero-allocation key counter for contact-free, non-special VFF B336 blocks.
    Returns ``None`` if any unexpected nesting is encountered so the caller
    falls back to ``extract_attribute_dictionaries``.
    """
    pos = 0
    end = len(pl)
    if pl.startswith(b"\xde\x05") and end >= 6:
        id_len = read_u32(pl, 2)
        if 6 + id_len > end:
            return None
        pos = 6 + id_len

    counts: Dict[str, int] = {}
    stack: List[Tuple[int, int]] = [(pos, end)]
    while stack:
        cur, lim = stack.pop()
        while cur + 6 <= lim:
            t2 = pl[cur : cur + 2]
            sz = read_u32(pl, cur + 2)
            body = cur + 6
            nxt = body + sz
            if nxt > lim:
                return None
            if t2 in (b"\xdd\x05", b"\xb1\x36", b"\xb2\x36"):
                stack.append((body, nxt))
            elif t2 == b"\xb3\x36":
                bpos = body
                dname: Optional[str] = None
                seen_keys: set = set()
                has_b536 = False
                while bpos + 6 <= nxt:
                    bt = pl[bpos : bpos + 2]
                    bsz = read_u32(pl, bpos + 2)
                    bbody = bpos + 6
                    bnxt = bbody + bsz
                    if bnxt > nxt:
                        return None
                    if bt == b"\xb4\x36":
                        dname = pl[bbody:bnxt].decode("utf-8", errors="replace")
                    elif bt == b"\xb5\x36":
                        has_b536 = True
                        epos = bbody
                        pending_key: Optional[bytes] = None
                        while epos + 6 <= bnxt:
                            et = pl[epos : epos + 2]
                            esz = read_u32(pl, epos + 2)
                            ebody = epos + 6
                            enxt = ebody + esz
                            if enxt > bnxt:
                                return None
                            if et == b"\xb6\x36":
                                pending_key = pl[ebody:enxt]
                            elif et == b"\xa4\x38":
                                if pending_key is not None:
                                    seen_keys.add(pending_key)
                                    pending_key = None
                            else:
                                return None
                            epos = enxt
                    elif bt not in (b"\xb0\x36", b"\xb7\x36"):
                        return None
                    bpos = bnxt
                if dname is None or not has_b536:
                    return None
                counts[dname] = len(seen_keys)
            cur = nxt
    return counts


def _extract_d007_dicts_fast(d007: Any) -> Dict[str, Any]:
    for ch in d007.children:
        if ch.tag == "DC05":
            pl = ch.payload
            if b"\xb3\x36" not in pl or b"\xb4\x36" not in pl:
                return {}
            if not _RAW_FULL_DICT_HINT_RE.search(pl):
                fast = _fast_count_b336_dicts(pl)
                if fast is not None:
                    return fast
            return extract_attribute_dictionaries(d007)
    return {}


def _parse_b336_dicts_from_b136(payload: bytes) -> Dict[str, Dict[str, Any]]:
    nodes = parse_tlv_recursive(payload, 0, len(payload), _PROP_CONTAINER_TAGS)
    dictionaries: Dict[str, Dict[str, Any]] = {}

    def extract_entries(children, entries):
        current_key = None
        for n in children:
            if n.tag == "B636":
                current_key = n.payload.decode("utf-8", errors="replace")
            elif n.tag == "A438" and current_key is not None:
                from ._core import _decode_vff_attr_value

                entries[current_key] = _decode_vff_attr_value(n)
                current_key = None
            elif n.children:
                extract_entries(n.children, entries)

    def walk(children):
        for idx, n in enumerate(children):
            if n.tag == "B436":
                name = n.payload.decode("utf-8", errors="replace")
                entries: Dict[str, Any] = {}
                if idx + 1 < len(children):
                    extract_entries(children[idx + 1].children, entries)
                dictionaries[name] = entries
            elif n.children:
                walk(n.children)

    walk(nodes)
    return dictionaries


def _provenance_vff(skp_path: pathlib.Path, header: bytes) -> ProvenanceReport:
    version = "unknown"
    second_marker = header.find(b"\xff\xfe\xff", 4)
    if second_marker > 0:
        ver_text = header[second_marker + 4 :].decode("utf-16-le", errors="ignore")
        b0 = ver_text.find("{")
        b1 = ver_text.find("}")
        if 0 <= b0 < b1:
            version = ver_text[b0 + 1 : b1]

    pk_pos = header.find(b"PK\x03\x04")
    if pk_pos < 0:
        with skp_path.open("rb") as f:
            pk_pos = f.read(4096).find(b"PK\x03\x04")
    if pk_pos < 0:
        raise SkpParseError("No ZIP container found", stage="zip_extract")

    items: List[ProvenanceItem] = []
    contacts: List[ProvenanceItem] = []
    seen_contacts: set = set()

    hdr_src = ProvenanceSource("VFF file header", "file")
    items.append(ProvenanceItem("format", "vff", hdr_src))
    if version != "unknown":
        items.append(ProvenanceItem("sketchup_version", version, hdr_src))

    file_guid = ""
    meta_name: Optional[str] = None
    meta_desc: Optional[str] = None
    meta_path: Optional[str] = None
    meta_version: Optional[str] = None
    save_clients: List[Tuple[int, str]] = []
    save_times_meta: List[Tuple[int, int]] = []

    mat_xml_info: Dict[str, Dict[str, Any]] = {}
    style_xml_texts: List[Tuple[str, str, str]] = []

    try:
        with zipfile.ZipFile(skp_path, "r") as zf:
            names = zf.namelist()
            if "meta/meta.dat" in names:
                _validate_zip_entry_size(zf, "meta/meta.dat")
                meta_bytes = zf.read("meta/meta.dat")
                outer = list(_iter_flat_tlv(meta_bytes))
                if outer and outer[0][0] == "6400":
                    for tag, pl in _iter_flat_tlv(outer[0][1]):
                        if tag == "7500" and pl:
                            meta_version = pl.decode("utf-8", errors="replace")
                        elif tag == "6600" and len(pl) == 16:
                            file_guid = _win16_to_uuid_str(pl)
                        elif tag == "6A00":
                            meta_name = pl.decode("utf-8", errors="replace")
                        elif tag == "6B00":
                            meta_desc = pl.decode("utf-8", errors="replace")
                        elif tag == "6F00" and pl:
                            meta_path = pl.decode("utf-8", errors="replace")
                        elif tag == "7800":
                            for t1, p1 in _iter_flat_tlv(pl):
                                if t1 != "C800":
                                    continue
                                for t2, p2 in _iter_flat_tlv(p1):
                                    if t2 != "C900":
                                        continue
                                    ca_idx = 0
                                    for t3, p3 in _iter_flat_tlv(p2):
                                        if t3 != "CA00":
                                            continue
                                        for t4, p4 in _iter_flat_tlv(p3):
                                            if t4 == "CB00" and p4:
                                                save_clients.append(
                                                    (ca_idx, p4.decode("utf-8", errors="replace"))
                                                )
                                            elif t4 == "CC00" and len(p4) == 4:
                                                ts = read_u32(p4, 0)
                                                if ts > 0:
                                                    save_times_meta.append((ca_idx, ts))
                                        ca_idx += 1

            mat_ns = {"mat": "http://sketchup.google.com/schemas/sketchup/1.0/material"}
            sty_ns = "{http://sketchup.google.com/schemas/sketchup/1.0/style}"
            for entry_name in names:
                if entry_name.startswith("materials/") and entry_name.endswith("/material.xml"):
                    _validate_zip_entry_size(zf, entry_name)
                    xml_bytes = zf.read(entry_name)
                    parts = entry_name.split("/")
                    folder = parts[1] if len(parts) > 2 else ""
                    try:
                        root = ET.fromstring(xml_bytes)
                    except (ET.ParseError, DefusedXmlException):
                        continue
                    mat_el = root.find(".//mat:material", mat_ns)
                    if mat_el is None:
                        continue
                    raw_name = _extract_raw_material_xml_name(xml_bytes)
                    mat_name = raw_name if raw_name is not None else (mat_el.get("name") or "unknown")
                    tex_el = mat_el.find("mat:texture", mat_ns)
                    tex_fn = (tex_el.get("textureFilename") or "") if tex_el is not None else ""
                    mat_xml_info[folder] = {
                        "entry": entry_name,
                        "name": mat_name,
                        "texture_filename": tex_fn,
                        "has_texture": mat_el.get("hasTexture") == "1" or tex_el is not None,
                    }
                elif entry_name.startswith("styles/") and entry_name.endswith("/style.xml"):
                    _validate_zip_entry_size(zf, entry_name)
                    try:
                        sroot = ET.fromstring(zf.read(entry_name))
                    except (ET.ParseError, DefusedXmlException):
                        continue
                    sel = sroot.find(f"{sty_ns}style")
                    if sel is not None:
                        style_xml_texts.append(
                            (entry_name, sel.get("name") or "", sel.get("desc") or "")
                        )

            if "model.dat" not in names:
                raise SkpParseError("Invalid ZIP container: missing model.dat", stage="zip_extract")
            _validate_zip_entry_size(zf, "model.dat")
            model_dat = zf.read("model.dat")
    except (SkpParseError, FileNotFoundError, PermissionError, IsADirectoryError, RecursionError, MemoryError):
        raise
    except Exception as exc:
        raise SkpParseError(f"Invalid or damaged ZIP container: {exc}", stage="zip_extract") from exc

    if version == "unknown" and meta_version:
        version = meta_version
        items.append(
            ProvenanceItem(
                "sketchup_version",
                version,
                ProvenanceSource("meta/meta.dat > 6400 > 7500", "file"),
            )
        )
    elif meta_version and meta_version != version:
        items.append(
            ProvenanceItem(
                "sketchup_version",
                meta_version,
                ProvenanceSource("meta/meta.dat > 6400 > 7500", "file"),
            )
        )

    if file_guid:
        items.append(
            ProvenanceItem(
                "file_guid",
                file_guid,
                ProvenanceSource("meta/meta.dat > 6400 > 6600", "file"),
            )
        )

    for ca_idx, client in save_clients:
        src = ProvenanceSource(
            f"meta/meta.dat > 6400 > 7800 > C800 > C900 > CA00[{ca_idx}] > CB00",
            "file",
        )
        items.append(ProvenanceItem("save_client", client, src))
        _scan_string_for_contacts(client, src, contacts, seen_contacts)

    for ca_idx, ts in save_times_meta:
        items.append(
            ProvenanceItem(
                "save_time",
                ts,
                ProvenanceSource(
                    f"meta/meta.dat > 6400 > 7800 > C800 > C900 > CA00[{ca_idx}] > CC00",
                    "file",
                ),
            )
        )

    if meta_path:
        items.append(
            ProvenanceItem(
                "model_path",
                meta_path,
                ProvenanceSource("meta/meta.dat > 6400 > 6F00", "file"),
            )
        )

    model_props_0902: Dict[str, Any] = {}
    shadow_info: Optional[Dict[str, Any]] = None
    style_records: List[Tuple[str, str, str, str]] = []  # (pool_tag, name, desc, path)
    scene_records: List[Tuple[str, str, Dict[str, Dict[str, Any]]]] = []
    mat_id_to_folder: Dict[int, Tuple[str, int, Dict[str, Dict[str, Any]]]] = {}
    layer_folder_to_name: Dict[str, str] = {}
    layer_records: List[Tuple[str, Dict[str, Dict[str, Any]]]] = []
    def_records: List[Dict[str, Any]] = []
    root_dicts: Dict[str, Dict[str, Any]] = {}
    placed_images: List[Tuple[str, int]] = []  # (inst_name, def_id)

    def scan_container_entities(
        container_children,
        owner_prefix: str,
        owner_type: str,
        owner_name: str,
    ) -> Optional[int]:
        first_face_mat: Optional[int] = None
        for ch in container_children:
            ctag = ch.tag
            if ctag == "8B13" and ch.size > 0:
                start = ch._payload_start
                end = ch._payload_end
                has_dict_bytes = (
                    model_dat.find(b"\xb3\x36", start, end) >= 0
                    and model_dat.find(b"\xb4\x36", start, end) >= 0
                )
                if owner_type == "image_definition" or has_dict_bytes:
                    pos = start
                    while pos + 6 <= end:
                        ftag = model_dat[pos : pos + 2]
                        fsz = read_u32(model_dat, pos + 2)
                        f_body = pos + 6
                        f_end = f_body + fsz
                        if f_end > end:
                            break
                        if ftag == b"\xac\x0d":
                            need_mat = (
                                owner_type == "image_definition" and first_face_mat is None
                            )
                            need_dict = (
                                owner_type != "image_definition"
                                and has_dict_bytes
                                and model_dat.find(b"\xb3\x36", f_body, f_end) >= 0
                                and model_dat.find(b"\xb4\x36", f_body, f_end) >= 0
                            )
                            if need_mat or need_dict:
                                fn_children = parse_tlv_recursive(
                                    model_dat, f_body, f_end, {"D007"}
                                )
                                d007 = _find_tag(fn_children, "D007")
                                if d007 is not None:
                                    if need_mat:
                                        d107 = _find_tag(d007.children, "D107")
                                        if d107 is not None and d107.payload:
                                            first_face_mat = parse_var_int(
                                                d107.payload, 0, len(d107.payload)
                                            )
                                    if need_dict:
                                        fdicts = _extract_d007_dicts_fast(d007)
                                        if fdicts:
                                            _record_attribute_dictionaries(
                                                fdicts,
                                                f"{owner_prefix} > 8B13 > AC0D > D007 > DC05 > DD05 > B336({{name!r}})",
                                                "face",
                                                "",
                                                items,
                                                contacts,
                                                seen_contacts,
                                            )
                                if (
                                    owner_type == "image_definition"
                                    and first_face_mat is not None
                                    and not need_dict
                                ):
                                    break
                        pos = f_end
            elif ctag == "8A13" and ch.size > 0:
                start = ch._payload_start
                end = ch._payload_end
                if (
                    model_dat.find(b"\xb3\x36", start, end) >= 0
                    and model_dat.find(b"\xb4\x36", start, end) >= 0
                ):
                    pos = start
                    while pos + 6 <= end:
                        etag = model_dat[pos : pos + 2]
                        esz = read_u32(model_dat, pos + 2)
                        e_body = pos + 6
                        e_end = e_body + esz
                        if e_end > end:
                            break
                        if (
                            etag == b"\xb8\x0b"
                            and model_dat.find(b"\xb3\x36", e_body, e_end) >= 0
                            and model_dat.find(b"\xb4\x36", e_body, e_end) >= 0
                        ):
                            en_children = parse_tlv_recursive(
                                model_dat, e_body, e_end, {"D007"}
                            )
                            d007 = _find_tag(en_children, "D007")
                            if d007 is not None:
                                edicts = _extract_d007_dicts_fast(d007)
                                if edicts:
                                    _record_attribute_dictionaries(
                                        edicts,
                                        f"{owner_prefix} > 8A13 > B80B > D007 > DC05 > DD05 > B336({{name!r}})",
                                        "edge",
                                        "",
                                        items,
                                        contacts,
                                        seen_contacts,
                                    )
                        pos = e_end
            elif ctag in ("8C13", "8D13", "9013"):
                for item_node in ch.children:
                    inst_node = item_node
                    inst_etype = "instance"
                    rec_mid = "8C13 > 6419"
                    if item_node.tag == "4C1D":
                        inst_node = _find_tag(item_node.children, "6419")
                        inst_etype = "group"
                        rec_mid = "8D13 > 4C1D > 6419"
                    elif item_node.tag == "401F":
                        inst_node = _find_tag(item_node.children, "6419")
                        inst_etype = "image"
                        rec_mid = "9013 > 401F > 6419"
                    if inst_node is None or inst_node.tag != "6419":
                        continue
                    n6519 = _find_tag(inst_node.children, "6519")
                    iname = (
                        n6519.payload.decode("utf-8", errors="replace")
                        if n6519 is not None and n6519.payload
                        else ""
                    )
                    if iname:
                        _scan_string_for_contacts(
                            iname,
                            ProvenanceSource(
                                f"{owner_prefix} > {rec_mid} > 6519",
                                inst_etype,
                                iname,
                            ),
                            contacts,
                            seen_contacts,
                        )
                    if inst_etype == "image":
                        n6719 = _find_tag(inst_node.children, "6719")
                        if n6719 is not None and n6719.payload:
                            ref_def = parse_var_int(n6719.payload, 0, len(n6719.payload))
                            placed_images.append((iname, ref_def))
                    d007 = _find_tag(inst_node.children, "D007")
                    if d007 is not None:
                        idicts = _extract_d007_dicts_fast(d007)
                        if idicts:
                            _record_attribute_dictionaries(
                                idicts,
                                f"{owner_prefix} > {rec_mid} > D007 > DC05 > DD05 > B336({{name!r}})",
                                inst_etype,
                                iname,
                                items,
                                contacts,
                                seen_contacts,
                            )
            elif ctag == "9813":
                for tn in ch.children:
                    if tn.tag != "F055":
                        continue
                    f155 = _find_tag(tn.children, "F155")
                    if f155 is not None and f155.payload:
                        tstr = f155.payload.decode("utf-8", errors="replace")
                        _scan_string_for_contacts(
                            tstr,
                            ProvenanceSource(f"{owner_prefix} > 9813 > F055 > F155", "text"),
                            contacts,
                            seen_contacts,
                        )
                    d007 = _find_tag(tn.children, "D007")
                    if d007 is not None:
                        tdicts = _extract_d007_dicts_fast(d007)
                        if tdicts:
                            _record_attribute_dictionaries(
                                tdicts,
                                f"{owner_prefix} > 9813 > F055 > D007 > DC05 > DD05 > B336({{name!r}})",
                                "text",
                                "",
                                items,
                                contacts,
                                seen_contacts,
                            )
            elif ctag == "9913":
                for dn in ch.children:
                    if dn.tag != "CC5B":
                        continue
                    d859 = _find_tag(dn.children, "D859")
                    if d859 is not None:
                        d959 = _find_tag(d859.children, "D959")
                        if d959 is not None and d959.payload:
                            dstr = d959.payload.decode("utf-8", errors="replace")
                            _scan_string_for_contacts(
                                dstr,
                                ProvenanceSource(
                                    f"{owner_prefix} > 9913 > CC5B > D859 > D959",
                                    "dimension",
                                ),
                                contacts,
                                seen_contacts,
                            )
                        d007 = _find_tag(d859.children, "D007")
                        if d007 is not None:
                            ddicts = _extract_d007_dicts_fast(d007)
                            if ddicts:
                                _record_attribute_dictionaries(
                                    ddicts,
                                    f"{owner_prefix} > 9913 > CC5B > D859 > D007 > DC05 > DD05 > B336({{name!r}})",
                                    "dimension",
                                    "",
                                    items,
                                    contacts,
                                    seen_contacts,
                                )
            elif ctag == "9313":
                for sp in ch.children:
                    if sp.tag != "5C44":
                        continue
                    n5e = _find_tag(sp.children, "5E44")
                    n5f = _find_tag(sp.children, "5F44")
                    sp_name = (
                        n5e.payload.decode("utf-8", errors="replace")
                        if n5e is not None and n5e.payload
                        else ""
                    )
                    if sp_name:
                        _scan_string_for_contacts(
                            sp_name,
                            ProvenanceSource(
                                f"{owner_prefix} > 9313 > 5C44 > 5E44",
                                "section_plane",
                                sp_name,
                            ),
                            contacts,
                            seen_contacts,
                        )
                    if n5f is not None and n5f.payload:
                        _scan_string_for_contacts(
                            n5f.payload.decode("utf-8", errors="replace"),
                            ProvenanceSource(
                                f"{owner_prefix} > 9313 > 5C44 > 5F44",
                                "section_plane",
                                sp_name,
                            ),
                            contacts,
                            seen_contacts,
                        )
                    d007 = _find_tag(sp.children, "D007")
                    if d007 is not None:
                        sdicts = _extract_d007_dicts_fast(d007)
                        if sdicts:
                            _record_attribute_dictionaries(
                                sdicts,
                                f"{owner_prefix} > 9313 > 5C44 > D007 > DC05 > DD05 > B336({{name!r}})",
                                "section_plane",
                                sp_name,
                                items,
                                contacts,
                                seen_contacts,
                            )
        return first_face_mat

    for index, total, el in iter_top_level_lazy(
        model_dat, 0, len(model_dat), _VFF_PROV_CONTAINERS
    ):
        try:
            tag = el.tag
            if tag == "0902" and el.size > 0:
                dicts = _parse_b336_dicts_from_b136(el.payload)
                model_props_0902.update(dicts.get("ModelProperties", {}))
            elif tag == "0402":
                n9065 = _find_tag(el.children, "9065")
                if n9065 is not None:
                    city = ""
                    country = ""
                    lon = 0.0
                    lat = 0.0
                    tz = 0.0
                    for sub in n9065.children:
                        if sub.tag == "9365":
                            city = sub.payload.decode("utf-8", errors="replace")
                        elif sub.tag == "9465":
                            country = sub.payload.decode("utf-8", errors="replace")
                        elif sub.tag == "9565" and len(sub.payload) == 8:
                            lon = struct.unpack_from("<d", sub.payload, 0)[0]
                        elif sub.tag == "9665" and len(sub.payload) == 8:
                            lat = struct.unpack_from("<d", sub.payload, 0)[0]
                        elif sub.tag == "9765" and len(sub.payload) == 8:
                            tz = struct.unpack_from("<d", sub.payload, 0)[0]
                    shadow_info = {
                        "city": city,
                        "country": country,
                        "latitude": lat,
                        "longitude": lon,
                        "tz_offset": tz,
                    }
            elif tag == "0602":
                n7869 = _find_tag(el.children, "7869")
                if n7869 is not None:
                    for pool in n7869.children:
                        if pool.tag not in ("7969", "7B69"):
                            continue
                        for st_node in pool.children:
                            if st_node.tag != "6C6B":
                                continue
                            p_node = _find_tag(st_node.children, "6E6B")
                            n_node = _find_tag(st_node.children, "6F6B")
                            d_node = _find_tag(st_node.children, "706B")
                            st_path = (
                                p_node.payload.decode("utf-8", errors="replace")
                                if p_node is not None and p_node.payload
                                else ""
                            )
                            st_name = (
                                n_node.payload.decode("utf-8", errors="replace")
                                if n_node is not None and n_node.payload
                                else ""
                            )
                            st_desc = (
                                d_node.payload.decode("utf-8", errors="replace")
                                if d_node is not None and d_node.payload
                                else ""
                            )
                            style_records.append((pool.tag, st_name, st_desc, st_path))
            elif tag == "0702":
                n606d = _find_tag(el.children, "606D")
                n616d = _find_tag(n606d.children, "616D") if n606d is not None else None
                if n616d is not None:
                    for pg in n616d.children:
                        if pg.tag != "4871":
                            continue
                        b546f = _find_tag(pg.children, "546F")
                        n556f = _find_tag(b546f.children, "556F") if b546f is not None else None
                        n5371 = _find_tag(pg.children, "5371")
                        pg_name = (
                            n556f.payload.decode("utf-8", errors="replace")
                            if n556f is not None and n556f.payload
                            else ""
                        )
                        pg_disp = (
                            n5371.payload.decode("utf-8", errors="replace")
                            if n5371 is not None and n5371.payload
                            else ""
                        )
                        pg_dicts: Dict[str, Dict[str, Any]] = {}
                        if b546f is not None:
                            dc05 = _find_tag(b546f.children, "DC05")
                            if dc05 is not None and b"\xb4\x36" in dc05.payload:
                                pg_dicts = extract_attribute_dictionaries(
                                    {"children": [{"tag": "DC05", "payload": dc05.payload}]}
                                )
                        scene_records.append((pg_name, pg_disp, pg_dicts))
            elif tag == "F701":
                d430 = _find_tag(el.children, "D430")
                d530 = _find_tag(d430.children, "D530") if d430 is not None else None
                if d530 is not None:
                    for c832 in d530.children:
                        if c832.tag != "C832":
                            continue
                        dc05 = _find_tag(c832.children, "DC05")
                        cc32 = _find_tag(c832.children, "CC32")
                        ca32 = _find_tag(c832.children, "CA32")
                        if dc05 is not None and cc32 is not None:
                            mid = _entity_id_from_dc05(dc05.payload)
                            folder = cc32.payload.decode("utf-8", errors="replace")
                            owner_b = ca32.payload[0] if ca32 is not None and ca32.payload else 0
                            mdicts: Dict[str, Dict[str, Any]] = {}
                            if b"\xb4\x36" in dc05.payload:
                                mdicts = extract_attribute_dictionaries(
                                    {"children": [{"tag": "DC05", "payload": dc05.payload}]}
                                )
                            if mid is not None:
                                mat_id_to_folder[mid] = (folder, owner_b, mdicts)
            elif tag == "F801":
                n983a = _find_tag(el.children, "983A")
                n993a = _find_tag(n983a.children, "993A") if n983a is not None else None
                if n993a is not None:
                    for lyr in n993a.children:
                        if lyr.tag != "8C3C":
                            continue
                        n8d = _find_tag(lyr.children, "8D3C")
                        dc05 = _find_tag(lyr.children, "DC05")
                        lname = (
                            n8d.payload.decode("utf-8", errors="replace")
                            if n8d is not None
                            else ""
                        )
                        n8f = _find_tag(lyr.children, "8F3C")
                        if n8f is not None and len(n8f.payload) >= 6:
                            for st, sp in _iter_flat_tlv(n8f.payload):
                                if st == "C832":
                                    for it, ip in _iter_flat_tlv(sp):
                                        if it == "CC32":
                                            layer_folder_to_name[
                                                ip.decode("utf-8", errors="replace")
                                            ] = lname
                        ldicts: Dict[str, Dict[str, Any]] = {}
                        if dc05 is not None and b"\xb4\x36" in dc05.payload:
                            ldicts = extract_attribute_dictionaries(
                                {"children": [{"tag": "DC05", "payload": dc05.payload}]}
                            )
                        layer_records.append((lname, ldicts))
            elif tag in ("7C15", "F901"):
                def_nodes = [el] if tag == "7C15" else [
                    n
                    for n70 in el.children if n70.tag == "7017"
                    for n71 in n70.children if n71.tag == "7117"
                    for n in n71.children if n.tag == "7C15"
                ]
                for def_el in def_nodes:
                    d_guid = ""
                    d_name = ""
                    d_desc = ""
                    d_path = ""
                    d_time = 0
                    d_kind = 0
                    c8813 = None
                    for ch in def_el.children:
                        ct = ch.tag
                        if ct == "7D15" and len(ch.payload) == 16:
                            d_guid = _win16_to_ifc_guid(ch.payload)
                        elif ct == "7E15":
                            d_name = ch.payload.decode("utf-8", errors="replace")
                        elif ct == "7F15":
                            d_desc = ch.payload.decode("utf-8", errors="replace")
                        elif ct == "8015":
                            d_path = ch.payload.decode("utf-8", errors="replace")
                        elif ct == "8115" and len(ch.payload) == 4:
                            d_time = read_u32(ch.payload, 0)
                        elif ct == "8315" and ch.payload:
                            d_kind = parse_var_int(ch.payload, 0, len(ch.payload))
                        elif ct == "8813":
                            c8813 = ch
                    d_etype = (
                        "image_definition"
                        if d_kind == 2
                        else ("group_definition" if d_kind == 1 else "definition")
                    )
                    def_id = None
                    ddicts: Dict[str, Dict[str, Any]] = {}
                    face_mat_id: Optional[int] = None
                    if c8813 is not None:
                        d007 = _find_tag(c8813.children, "D007")
                        if d007 is not None:
                            dc05 = _find_tag(d007.children, "DC05")
                            if dc05 is not None:
                                def_id = _entity_id_from_dc05(dc05.payload)
                            ddicts = _extract_d007_dicts_fast(d007)
                        face_mat_id = scan_container_entities(
                            c8813.children,
                            "model.dat > F901 > 7017 > 7117 > 7C15 > 8813",
                            d_etype,
                            d_name,
                        )
                    def_records.append(
                        {
                            "id": def_id,
                            "etype": d_etype,
                            "name": d_name,
                            "desc": d_desc,
                            "path": d_path,
                            "load_time": d_time,
                            "guid": d_guid,
                            "dicts": ddicts,
                            "face_mat_id": face_mat_id,
                        }
                    )
            elif tag == "F601":
                c8813 = _find_tag(el.children, "8813")
                if c8813 is not None:
                    d007 = _find_tag(c8813.children, "D007")
                    if d007 is not None:
                        root_dicts = _extract_d007_dicts_fast(d007)
                    scan_container_entities(
                        c8813.children, "model.dat > F601 > 8813", "model", ""
                    )
        except (SkpParseError, RecursionError, MemoryError):
            raise
        except Exception as exc:
            raise SkpParseError(
                f"Failed while processing top-level record: {exc}",
                stage="tlv_walk",
                record_index=index,
                total_records=total,
                tag=el.tag,
            ) from exc
        del el

    # Model name & description
    prop_name = model_props_0902.get("Name")
    prop_desc = model_props_0902.get("Description")
    model_name = (
        meta_name
        if meta_name is not None
        else (prop_name if isinstance(prop_name, str) else "")
    )
    model_desc = (
        meta_desc
        if meta_desc is not None
        else (prop_desc if isinstance(prop_desc, str) else "")
    )

    if model_name:
        src = ProvenanceSource(
            "meta/meta.dat > 6400 > 6A00"
            if meta_name
            else "model.dat > 0902 > B136 > B236 > B336('ModelProperties')['Name']",
            "model",
        )
        items.append(ProvenanceItem("model_name", model_name, src))
        _scan_string_for_contacts(model_name, src, contacts, seen_contacts)
    if isinstance(prop_name, str) and prop_name and meta_name and prop_name != meta_name:
        src2 = ProvenanceSource(
            "model.dat > 0902 > B136 > B236 > B336('ModelProperties')['Name']",
            "model",
        )
        items.append(ProvenanceItem("model_name", prop_name, src2))
        _scan_string_for_contacts(prop_name, src2, contacts, seen_contacts)

    if model_desc:
        src = ProvenanceSource(
            "meta/meta.dat > 6400 > 6B00"
            if meta_desc
            else "model.dat > 0902 > B136 > B236 > B336('ModelProperties')['Description']",
            "model",
        )
        items.append(ProvenanceItem("model_description", model_desc, src))
        _scan_string_for_contacts(model_desc, src, contacts, seen_contacts)
    if isinstance(prop_desc, str) and prop_desc and meta_desc and prop_desc != meta_desc:
        src2 = ProvenanceSource(
            "model.dat > 0902 > B136 > B236 > B336('ModelProperties')['Description']",
            "model",
        )
        items.append(ProvenanceItem("model_description", prop_desc, src2))
        _scan_string_for_contacts(prop_desc, src2, contacts, seen_contacts)

    # Identify image-backed material folders so user texture materials and
    # imported Image entities match SUModelGetMaterials vs SUEntitiesGetImages.
    image_mat_folders: Dict[str, str] = {}
    for drec in def_records:
        if drec["etype"] == "image_definition" and drec["face_mat_id"] is not None:
            m_entry = mat_id_to_folder.get(drec["face_mat_id"])
            if m_entry is not None:
                image_mat_folders[m_entry[0]] = drec["name"]

    # Definitions: GUIDs, paths, load times, descriptions, dictionaries
    for drec in def_records:
        dname = drec["name"]
        detype = drec["etype"]
        if dname:
            _scan_string_for_contacts(
                dname,
                ProvenanceSource(
                    "model.dat > F901 > 7017 > 7117 > 7C15 > 7E15", detype, dname
                ),
                contacts,
                seen_contacts,
            )
        if drec["desc"]:
            _scan_string_for_contacts(
                drec["desc"],
                ProvenanceSource(
                    "model.dat > F901 > 7017 > 7117 > 7C15 > 7F15", detype, dname
                ),
                contacts,
                seen_contacts,
            )
        if drec["guid"]:
            items.append(
                ProvenanceItem(
                    "definition_guid",
                    drec["guid"],
                    ProvenanceSource(
                        "model.dat > F901 > 7017 > 7117 > 7C15 > 7D15", detype, dname
                    ),
                )
            )
        if drec["load_time"] > 0:
            items.append(
                ProvenanceItem(
                    "definition_load_time",
                    drec["load_time"],
                    ProvenanceSource(
                        "model.dat > F901 > 7017 > 7117 > 7C15 > 8115", detype, dname
                    ),
                )
            )
        if drec["path"]:
            p_kind = "image_path" if detype == "image_definition" else "definition_path"
            items.append(
                ProvenanceItem(
                    p_kind,
                    drec["path"],
                    ProvenanceSource(
                        "model.dat > F901 > 7017 > 7117 > 7C15 > 8015", detype, dname
                    ),
                )
            )
        if drec["dicts"]:
            _record_attribute_dictionaries(
                drec["dicts"],
                "model.dat > F901 > 7017 > 7117 > 7C15 > 8813 > D007 > DC05 > DD05 > B336({name!r})",
                detype,
                dname,
                items,
                contacts,
                seen_contacts,
            )

    # Materials & imported images from materials/<folder>/material.xml
    real_mat_folders = {f for f, _, _ in mat_id_to_folder.values()}
    for folder, minfo in mat_xml_info.items():
        mname = minfo["name"]
        entry_path = minfo["entry"]
        tex_fn = minfo["texture_filename"]
        is_layer_mat = (
            folder in layer_folder_to_name or mname.startswith("Layer_")
        ) and folder not in real_mat_folders

        if not is_layer_mat and folder not in image_mat_folders and mname:
            _scan_string_for_contacts(
                mname,
                ProvenanceSource(f"{entry_path} (mat:material/@name)", "material", mname),
                contacts,
                seen_contacts,
            )

        if tex_fn:
            if folder in image_mat_folders:
                img_def_name = image_mat_folders[folder]
                items.append(
                    ProvenanceItem(
                        "image_path",
                        tex_fn,
                        ProvenanceSource(
                            f"{entry_path} (mat:texture/@textureFilename)",
                            "image",
                            img_def_name,
                        ),
                    )
                )
            elif is_layer_mat:
                tag_name = layer_folder_to_name.get(folder, mname[6:])
                items.append(
                    ProvenanceItem(
                        "texture_path",
                        tex_fn,
                        ProvenanceSource(
                            f"{entry_path} (mat:texture/@textureFilename)",
                            "layer",
                            tag_name,
                        ),
                    )
                )
            else:
                items.append(
                    ProvenanceItem(
                        "texture_path",
                        tex_fn,
                        ProvenanceSource(
                            f"{entry_path} (mat:texture/@textureFilename)",
                            "material",
                            mname,
                        ),
                    )
                )

    for _mid, (folder, _owner_b, mdicts) in mat_id_to_folder.items():
        if mdicts:
            mname = mat_xml_info.get(folder, {}).get("name", folder)
            _record_attribute_dictionaries(
                mdicts,
                "model.dat > F701 > D430 > D530 > C832 > DC05 > DD05 > B336({name!r})",
                "material",
                mname,
                items,
                contacts,
                seen_contacts,
            )

    for lname, ldicts in layer_records:
        if lname:
            _scan_string_for_contacts(
                lname,
                ProvenanceSource(
                    "model.dat > F801 > 983A > 993A > 8C3C > 8D3C", "layer", lname
                ),
                contacts,
                seen_contacts,
            )
        if ldicts:
            _record_attribute_dictionaries(
                ldicts,
                "model.dat > F801 > 983A > 993A > 8C3C > DC05 > DD05 > B336({name!r})",
                "layer",
                lname,
                items,
                contacts,
                seen_contacts,
            )

    # Styles
    seen_style_paths: set = set()
    for pool_tag, st_name, st_desc, st_path in style_records:
        if st_name:
            _scan_string_for_contacts(
                st_name,
                ProvenanceSource(
                    f"model.dat > 0602 > 7869 > {pool_tag} > 6C6B > 6F6B",
                    "style",
                    st_name,
                ),
                contacts,
                seen_contacts,
            )
        if st_desc:
            _scan_string_for_contacts(
                st_desc,
                ProvenanceSource(
                    f"model.dat > 0602 > 7869 > {pool_tag} > 6C6B > 706B",
                    "style",
                    st_name,
                ),
                contacts,
                seen_contacts,
            )
        if st_path and st_path not in seen_style_paths:
            seen_style_paths.add(st_path)
            items.append(
                ProvenanceItem(
                    "style_path",
                    st_path,
                    ProvenanceSource(
                        f"model.dat > 0602 > 7869 > {pool_tag} > 6C6B > 6E6B",
                        "style",
                        st_name,
                    ),
                )
            )
    for entry_name, sx_name, sx_desc in style_xml_texts:
        if sx_name:
            _scan_string_for_contacts(
                sx_name,
                ProvenanceSource(f"{entry_name} (sty:style/@name)", "style", sx_name),
                contacts,
                seen_contacts,
            )
        if sx_desc:
            _scan_string_for_contacts(
                sx_desc,
                ProvenanceSource(f"{entry_name} (sty:style/@desc)", "style", sx_name),
                contacts,
                seen_contacts,
            )

    # Scenes
    for pg_name, pg_disp, pg_dicts in scene_records:
        if pg_name:
            _scan_string_for_contacts(
                pg_name,
                ProvenanceSource(
                    "model.dat > 0702 > 606D > 616D > 4871 > 546F > 556F",
                    "scene",
                    pg_name,
                ),
                contacts,
                seen_contacts,
            )
        if pg_disp and pg_disp != pg_name:
            _scan_string_for_contacts(
                pg_disp,
                ProvenanceSource(
                    "model.dat > 0702 > 606D > 616D > 4871 > 5371",
                    "scene",
                    pg_name,
                ),
                contacts,
                seen_contacts,
            )
        if pg_dicts:
            _record_attribute_dictionaries(
                pg_dicts,
                "model.dat > 0702 > 606D > 616D > 4871 > 546F > DC05 > DD05 > B336({name!r})",
                "scene",
                pg_name,
                items,
                contacts,
                seen_contacts,
            )

    # ShadowInfo + root model dictionaries (GeoReference, GSU_ContributorsInfo, 3DWarehouse, plugins)
    georef_dict = root_dicts.get("GeoReference")
    is_georef = bool(georef_dict.get("UsesGeoReferencing")) if isinstance(georef_dict, dict) else False
    if shadow_info is not None:
        sh_val = dict(shadow_info)
        sh_val["is_georeferenced"] = is_georef
        sh_src = ProvenanceSource("model.dat > 0402 > 9065", "model")
        items.append(
            ProvenanceItem(
                "geolocation",
                {"kind": "ShadowInfo", **sh_val},
                sh_src,
            )
        )
        if shadow_info["city"]:
            _scan_string_for_contacts(
                shadow_info["city"],
                ProvenanceSource("model.dat > 0402 > 9065 > 9365", "model"),
                contacts,
                seen_contacts,
            )
        if shadow_info["country"]:
            _scan_string_for_contacts(
                shadow_info["country"],
                ProvenanceSource("model.dat > 0402 > 9065 > 9465", "model"),
                contacts,
                seen_contacts,
            )

    if root_dicts:
        _record_attribute_dictionaries(
            root_dicts,
            "model.dat > F601 > 8813 > D007 > DC05 > DD05 > B336({name!r})",
            "model",
            "",
            items,
            contacts,
            seen_contacts,
        )

    items.extend(contacts)
    derived = _build_derived_summary([it for it in items if it.kind in _PATH_KINDS])

    return ProvenanceReport(
        file=str(skp_path),
        format="vff",
        sketchup_version=version,
        file_guid=file_guid,
        model_name=model_name,
        model_description=model_desc,
        items=tuple(items),
        derived=derived,
        unread_root_entities=0,
    )


# ── Classic MFC (SketchUp 3–2020) provenance extraction ──────────────────


def _read_classic_premodel_dicts_with_props(data: bytes) -> Dict[str, Dict[str, Any]]:
    ver_m = re.search(rb"\{(\d+)\.", data[:0x60].replace(b"\x00", b""))
    if not ver_m:
        return {}
    ver = int(ver_m.group(1))
    end = len(data)
    for cname in (b"CMaterial", b"CLayer", b"CComponentDefinition"):
        needle = struct.pack("<H", len(cname)) + cname
        pos_c = 0
        while True:
            idx_c = data.find(needle, pos_c, end)
            if idx_c < 0:
                break
            if idx_c >= 4 and data[idx_c - 4 : idx_c - 2] == b"\xff\xff":
                end = idx_c - 4
                break
            pos_c = idx_c + 1
    cls = data.find(b"\xff\xff", 0, end)
    while cls >= 0 and data[cls + 4 : cls + 25] != b"\x13\x00CAttributeContainer":
        cls = data.find(b"\xff\xff", cls + 1, end)
    if cls < 0:
        return {}

    pid = rb"\x00" if ver >= 17 else b""
    named_refs: Dict[int, int] = {}
    for m in re.finditer(
        rb"(?=([\x01-\xff][\x80-\xff])\x00\x00" + pid + rb"....\xff\xfe\xff)",
        data[cls:end],
        re.DOTALL,
    ):
        tag = int.from_bytes(m.group(1), "little") & 0x7FFF
        named_refs[tag] = named_refs.get(tag, 0) + 1
    named = max(named_refs, key=named_refs.get) if named_refs else 3
    base = max(named - 2, 1)

    ar = _Archive(data, ver)
    ar.readers.update(_READERS)
    ar.next_slot = base
    ar.walk_base = base
    r = ar.r
    containers = []
    r.pos = cls
    try:
        _, n, v = ar.read_object(r)
        if n == "CAttributeContainer" and isinstance(v, dict):
            containers.append(v)
    except (LegacyParseError, struct.error, IndexError, UnicodeDecodeError, ValueError):
        pass
    cslot = ar.class_slot.get("CAttributeContainer")
    if cslot is not None:
        tag_bytes = struct.pack("<H", 0x8000 | cslot)
        pos = r.pos
        while True:
            pos = data.find(tag_bytes, pos, end)
            if pos < 0:
                break
            saved = ar.next_slot
            try:
                r.pos = pos
                _, n, v = ar.read_object(r)
            except (LegacyParseError, struct.error, IndexError, UnicodeDecodeError, ValueError):
                ar.next_slot = saved
                pos += 1
                continue
            if n == "CAttributeContainer" and isinstance(v, dict):
                containers.append(v)
            pos = max(r.pos, pos + 1)

    out: Dict[str, Dict[str, Any]] = {}
    for c in containers:
        for dname, entries in _extract_legacy_attribute_dictionaries(c).items():
            out.setdefault(dname, {}).update(entries)
    return out


def _read_classic_header_description(data: bytes) -> Tuple[Optional[int], str]:
    """Read the CSketchUpModel header description string right before SUOptionsManager."""
    eovm_marker = "End-Of-Version-Map".encode("utf-16-le")
    idx = data.find(eovm_marker, 0, 8192)
    if idx < 0:
        return None, ""
    start = idx + len(eovm_marker)
    # SUOptionsManager starts with u32(3) + u32(provider_count 1..16) + UTF-16 string
    # ('NamedOptions' or 'UnitsOptions'). The description UTF-16 string sits immediately
    # before that u32(3).
    for opt_name in ("NamedOptions", "UnitsOptions"):
        opt_bytes = opt_name.encode("utf-16-le")
        opt_idx = data.find(opt_bytes, start, start + 16384)
        if opt_idx < 0:
            continue
        # Back up over UTF-16 marker (4B for <255 len) and two u32s (8B)
        hdr_pos = opt_idx - 12
        if hdr_pos <= start or data[opt_idx - 4 : opt_idx - 1] != _STR_MARKER:
            continue
        ver_word, cnt_word = struct.unpack_from("<2I", data, hdr_pos)
        if ver_word not in (1, 2, 3) or not (1 <= cnt_word <= 20):
            continue
        # Scan from start..hdr_pos for the UTF-16 string that ends exactly at hdr_pos
        pos = start
        while True:
            s_idx = data.find(_STR_MARKER, pos, hdr_pos)
            if s_idx < 0:
                break
            try:
                rr = _R(data, s_idx)
                desc = rr.utf16()
                if rr.pos == hdr_pos:
                    return s_idx, desc
            except (LegacyParseError, struct.error, IndexError):
                pass
            pos = s_idx + 1
    return None, ""


def _parse_classic_shadow_at(data: bytes, pos: int) -> Optional[Dict[str, Any]]:
    try:
        r = _R(data, pos)
        sh_time = r.u32()
        dst_b = r.u8()
        if dst_b not in (0, 1):
            return None
        city = r.utf16()
        country = r.utf16()
        if len(city) > 256 or len(country) > 256:
            return None
        lon, lat, tz = r.f64s(3)
        if not (
            math.isfinite(lon)
            and math.isfinite(lat)
            and math.isfinite(tz)
            and -180.5 <= lon <= 180.5
            and -90.5 <= lat <= 90.5
            and -25.0 <= tz <= 25.0
        ):
            return None
        north_angle, dx, dy = r.f64s(3)
        if not (
            math.isfinite(north_angle)
            and math.isfinite(dx)
            and math.isfinite(dy)
            and abs((dx * dx + dy * dy) - 1.0) < 1e-3
        ):
            return None
        return {
            "offset": pos,
            "city": city,
            "country": country,
            "latitude": lat,
            "longitude": lon,
            "tz_offset": tz,
            "shadow_time": sh_time,
        }
    except (LegacyParseError, struct.error, IndexError):
        return None


def _read_classic_shadow_info(data: bytes, ar: _Archive) -> Optional[Dict[str, Any]]:
    if not ar.root_stopped_early:
        try:
            r = _R(data, ar.r.pos)
            nrel = r.u32()
            if nrel <= 100000:
                for _ in range(nrel):
                    ar.read_object(r, expect="CRelationship")
                r.u16()
                if ar.ver >= 4:
                    ar.read_object(r, expect="CAttributeContainer")
                    if ar.has_pid:
                        _skip_pid(r)
                parsed = _parse_classic_shadow_at(data, r.pos)
                if parsed is not None:
                    return parsed
        except (LegacyParseError, struct.error, IndexError):
            pass

    # Fallback scan in the document tail when root_stopped_early is True:
    # locate two consecutive UTF-16 strings (city, country) preceded by u32+u8
    # and followed by valid (lon, lat, tz, north_angle, unit north vector).
    pos = ar.r.pos
    end = len(data) - 64
    while True:
        idx = data.find(_STR_MARKER, pos, end)
        if idx < 0:
            break
        if idx >= ar.r.pos + 5:
            cand = _parse_classic_shadow_at(data, idx - 5)
            if cand is not None:
                return cand
        pos = idx + 1
    return None


def _read_classic_styles_in_tail(
    data: bytes, start_pos: int, ver: int
) -> List[Tuple[int, str, str, str]]:
    """Scan saved CSkpStyle declarations in the classic document tail."""
    out: List[Tuple[int, str, str, str]] = []
    style_decl = re.compile(rb"\xff\xff([\x01-\x04])\x00\x09\x00CSkpStyle")
    for m in style_decl.finditer(data, start_pos):
        schema = m.group(1)[0]
        try:
            r = _R(data, m.end())
            if r.u16() != 0:
                continue
            if schema >= 2 and ver >= 17:
                _skip_pid(r)
            r.raw(16)  # style GUID
            st_path = r.utf16()
            fmt_ver = r.u32()
            if fmt_ver not in (1, 2, 3, 4):
                continue
            st_name = r.utf16()
            st_desc = r.utf16()
            prop_cnt = r.u32()
            if prop_cnt > 500:
                continue
            # Verify first property header when present
            if prop_cnt > 0:
                r.u32()
                vcnt = r.u32()
                if vcnt > 32:
                    continue
                for _ in range(vcnt):
                    sz = _STYLE_VALUE_SIZES.get(r.u32())
                    if sz is None:
                        raise LegacyParseError("unknown style property type")
                    r.raw(sz)
            out.append((m.start(), st_name, st_desc, st_path))
        except (LegacyParseError, struct.error, IndexError):
            continue
    return out


def _read_classic_scene_texts(data: bytes, ar: _Archive) -> List[Tuple[str, str]]:
    m = _PAGE_DECL_RE.search(data, ar.r.pos)
    if m is not None:
        try:
            count = struct.unpack_from("<I", data, m.start() - 4)[0]
            if 1 <= count <= 10000:
                r = _R(data, m.start())
                classes = _PageClasses()
                scenes: List[Tuple[str, str]] = []
                for _ in range(count):
                    p0 = r.pos
                    kind, slot = _read_tag(r)
                    schema = classes.object(r, kind, slot, "CViewPage")
                    if r.u16() != 0:
                        raise LegacyParseError("scene attrs")
                    if schema >= 13 and ar.ver >= 17:
                        _skip_pid(r)
                    sname = r.utf16()
                    sdesc = r.utf16()
                    # Advance r over the full record via _read_page_record
                    r.pos = p0
                    _read_page_record(r, _PageClasses() if len(scenes) == 0 else classes, ar.ver)
                    scenes.append((sname, sdesc))
                return scenes
        except (LegacyParseError, struct.error, IndexError, ValueError):
            pass
    try:
        return [(pg.get("name", ""), "") for pg in _scan_pages(data, ar)]
    except Exception:
        return []


def _provenance_classic(skp_path: pathlib.Path) -> ProvenanceReport:
    data = skp_path.read_bytes()
    if not data.startswith(b"\xff\xfe\xff\x0e"):
        raise SkpParseError("Not a valid SketchUp file (bad header magic)", stage="header")

    items: List[ProvenanceItem] = []
    contacts: List[ProvenanceItem] = []
    seen_contacts: set = set()

    r_hdr = _R(data, 0)
    try:
        magic = r_hdr.utf16()
        if magic != "SketchUp Model":
            raise SkpParseError("Not a valid SketchUp file (bad header magic)", stage="header")
        ver_pos = r_hdr.pos
        ver_raw = r_hdr.utf16()
        version = ver_raw.strip("{}") or "unknown"
        guid_pos = r_hdr.pos
        guid_bytes = r_hdr.raw(16)
        file_guid = _win16_to_uuid_str(guid_bytes)
        path_pos = r_hdr.pos
        hdr_path = r_hdr.utf16()
        ts_pos = r_hdr.pos
        hdr_ts = r_hdr.u32()
    except SkpParseError:
        raise
    except Exception as exc:
        raise SkpParseError(f"Malformed classic .skp header: {exc}", stage="header") from exc

    items.append(
        ProvenanceItem("format", "classic", ProvenanceSource("file header @0x0000", "file"))
    )
    items.append(
        ProvenanceItem(
            "sketchup_version",
            version,
            ProvenanceSource(f"file header @{ver_pos:#06x}", "file"),
        )
    )
    items.append(
        ProvenanceItem(
            "file_guid",
            file_guid,
            ProvenanceSource(f"file header @{guid_pos:#06x}", "file"),
        )
    )
    if hdr_path:
        items.append(
            ProvenanceItem(
                "model_path",
                hdr_path,
                ProvenanceSource(f"file header @{path_pos:#06x}", "file"),
            )
        )
    if hdr_ts > 0:
        items.append(
            ProvenanceItem(
                "save_time",
                hdr_ts,
                ProvenanceSource(f"file header @{ts_pos:#06x}", "file"),
            )
        )

    try:
        ar, root_ents, layers, materials = _walk(data)
    except (SkpParseError, RecursionError, MemoryError):
        raise
    except Exception as exc:
        raise SkpParseError(f"legacy .skp parse failed: {exc}", stage="legacy_walk") from exc

    premodel_dicts = _read_classic_premodel_dicts_with_props(data)
    model_props = premodel_dicts.pop("ModelProperties", {})
    hdr_desc_pos, hdr_desc = _read_classic_header_description(data)

    prop_name = model_props.get("Name")
    prop_desc = model_props.get("Description")
    model_name = prop_name if isinstance(prop_name, str) else ""
    model_desc = (
        prop_desc
        if isinstance(prop_desc, str) and prop_desc
        else (hdr_desc or (prop_desc if isinstance(prop_desc, str) else ""))
    )

    if model_name:
        src = ProvenanceSource("CAttributeNamed 'ModelProperties'['Name']", "model")
        items.append(ProvenanceItem("model_name", model_name, src))
        _scan_string_for_contacts(model_name, src, contacts, seen_contacts)

    if model_desc:
        if isinstance(prop_desc, str) and prop_desc == model_desc:
            src = ProvenanceSource(
                "CAttributeNamed 'ModelProperties'['Description']", "model"
            )
        else:
            loc = f" @{hdr_desc_pos:#06x}" if hdr_desc_pos is not None else ""
            src = ProvenanceSource(f"CSketchUpModel description{loc}", "model")
        items.append(ProvenanceItem("model_description", model_desc, src))
        _scan_string_for_contacts(model_desc, src, contacts, seen_contacts)
        if (
            hdr_desc
            and isinstance(prop_desc, str)
            and prop_desc
            and hdr_desc != prop_desc
        ):
            loc = f" @{hdr_desc_pos:#06x}" if hdr_desc_pos is not None else ""
            src2 = ProvenanceSource(f"CSketchUpModel description{loc}", "model")
            items.append(ProvenanceItem("model_description", hdr_desc, src2))
            _scan_string_for_contacts(hdr_desc, src2, contacts, seen_contacts)

    slots = ar.slots

    # Map CImage entities -> image definition slots -> backing <auto> material slots
    image_def_slots: Dict[int, List[Dict[str, Any]]] = {}
    for _s, ent in slots.items():
        if ent[0] == "obj" and ent[1] == "CImage" and isinstance(ent[2], dict):
            image_def_slots.setdefault(ent[2]["def"], []).append(ent[2])

    image_mat_slots: Dict[int, str] = {}
    for def_slot in image_def_slots:
        dent = slots.get(def_slot)
        if dent and dent[0] == "obj" and isinstance(dent[2], dict):
            dname = dent[2].get("name", "")
            for _es, _en, ev in dent[2].get("ents", ()):
                if isinstance(ev, dict) and ev.get("k") == "face":
                    fmat = (ev.get("db") or {}).get("mat")
                    if fmat:
                        image_mat_slots[fmat] = dname

    def scan_classic_entity_list(ents, owner_prefix: str) -> None:
        for _es, en, ev in ents:
            if not isinstance(ev, dict):
                continue
            k = ev.get("k")
            if k == "instance":
                iname = ev.get("name", "")
                ietype = "group" if en == "CGroup" else "instance"
                cls_name = "CGroup" if en == "CGroup" else "CComponentInstance"
                if iname:
                    _scan_string_for_contacts(
                        iname,
                        ProvenanceSource(f"{owner_prefix}{cls_name}.name", ietype, iname),
                        contacts,
                        seen_contacts,
                    )
                raw_ia = ev.get("attrs")
                idicts = _extract_legacy_attribute_dictionaries(raw_ia) if raw_ia else {}
                if idicts:
                    _record_attribute_dictionaries(
                        idicts,
                        f"{owner_prefix}{cls_name} > CAttributeNamed {{name!r}}",
                        ietype,
                        iname,
                        items,
                        contacts,
                        seen_contacts,
                    )
            elif k == "image":
                img_path = ev.get("path", "")
                def_ent = slots.get(ev.get("def"))
                img_name = (
                    def_ent[2].get("name", "")
                    if def_ent and isinstance(def_ent[2], dict)
                    else ""
                )
                if img_path:
                    items.append(
                        ProvenanceItem(
                            "image_path",
                            img_path,
                            ProvenanceSource(
                                f"{owner_prefix}CImage.path", "image", img_name
                            ),
                        )
                    )
            elif k == "face":
                raw_fa = ev.get("attrs")
                fdicts = _extract_legacy_attribute_dictionaries(raw_fa) if raw_fa else {}
                if fdicts:
                    _record_attribute_dictionaries(
                        fdicts,
                        f"{owner_prefix}CFace > CAttributeNamed {{name!r}}",
                        "face",
                        "",
                        items,
                        contacts,
                        seen_contacts,
                    )
            elif k == "text":
                tstr = ev.get("text", "")
                if tstr:
                    _scan_string_for_contacts(
                        tstr,
                        ProvenanceSource(f"{owner_prefix}CText.text", "text"),
                        contacts,
                        seen_contacts,
                    )
            elif k == "dimension":
                dstr = ev.get("text", "")
                if dstr:
                    _scan_string_for_contacts(
                        dstr,
                        ProvenanceSource(
                            f"{owner_prefix}CDimensionLinear.text", "dimension"
                        ),
                        contacts,
                        seen_contacts,
                    )
            elif k == "sectionplane":
                sp_name = ev.get("name", "")
                sp_lbl = ev.get("label", "")
                if sp_name:
                    _scan_string_for_contacts(
                        sp_name,
                        ProvenanceSource(
                            f"{owner_prefix}CSectionPlane.name",
                            "section_plane",
                            sp_name,
                        ),
                        contacts,
                        seen_contacts,
                    )
                if sp_lbl:
                    _scan_string_for_contacts(
                        sp_lbl,
                        ProvenanceSource(
                            f"{owner_prefix}CSectionPlane.label",
                            "section_plane",
                            sp_name,
                        ),
                        contacts,
                        seen_contacts,
                    )

    # Definitions
    for s, ent in slots.items():
        if ent[0] == "obj" and ent[1] == "CComponentDefinition" and isinstance(ent[2], dict):
            d = ent[2]
            dname = d.get("name", "")
            ddesc = d.get("description", "")
            dpath = d.get("path", "")
            dtime = d.get("load_time", 0)
            dguid_hex = d.get("guid", "")
            detype = (
                "image_definition"
                if s in image_def_slots
                else ("group_definition" if d.get("is_group") else "definition")
            )
            if dname:
                _scan_string_for_contacts(
                    dname,
                    ProvenanceSource("CComponentDefinition.name", detype, dname),
                    contacts,
                    seen_contacts,
                )
            if ddesc:
                _scan_string_for_contacts(
                    ddesc,
                    ProvenanceSource("CComponentDefinition.description", detype, dname),
                    contacts,
                    seen_contacts,
                )
            if len(dguid_hex) == 32:
                items.append(
                    ProvenanceItem(
                        "definition_guid",
                        _hex32_to_ifc_guid(dguid_hex),
                        ProvenanceSource("CComponentDefinition.guid", detype, dname),
                    )
                )
            if isinstance(dtime, int) and dtime > 0:
                items.append(
                    ProvenanceItem(
                        "definition_load_time",
                        dtime,
                        ProvenanceSource(
                            "CComponentDefinition.load_time", detype, dname
                        ),
                    )
                )
            if dpath:
                pkind = "image_path" if detype == "image_definition" else "definition_path"
                items.append(
                    ProvenanceItem(
                        pkind,
                        dpath,
                        ProvenanceSource("CComponentDefinition.path", detype, dname),
                    )
                )
            ddicts = _extract_legacy_attribute_dictionaries(d.get("attrs"))
            if ddicts:
                _record_attribute_dictionaries(
                    ddicts,
                    "CComponentDefinition > CAttributeNamed {name!r}",
                    detype,
                    dname,
                    items,
                    contacts,
                    seen_contacts,
                )
            scan_classic_entity_list(
                d.get("ents", ()), f"CComponentDefinition({dname!r}) > "
            )

    # Root entities
    scan_classic_entity_list(root_ents, "root > ")

    # Materials & image backing <auto> materials
    for s, mv in materials:
        mname = mv.get("name", "")
        tex_file = mv.get("tex_file", "")
        if s in image_mat_slots:
            if tex_file:
                items.append(
                    ProvenanceItem(
                        "image_path",
                        tex_file,
                        ProvenanceSource(
                            "CMaterial.tex_file (CImage)",
                            "image",
                            image_mat_slots[s],
                        ),
                    )
                )
        else:
            if mname:
                _scan_string_for_contacts(
                    mname,
                    ProvenanceSource("CMaterial.name", "material", mname),
                    contacts,
                    seen_contacts,
                )
            if tex_file:
                items.append(
                    ProvenanceItem(
                        "texture_path",
                        tex_file,
                        ProvenanceSource("CMaterial.tex_file", "material", mname),
                    )
                )
        mdicts = _extract_legacy_attribute_dictionaries(mv.get("attrs"))
        if mdicts:
            _record_attribute_dictionaries(
                mdicts,
                "CMaterial > CAttributeNamed {name!r}",
                "material",
                mname,
                items,
                contacts,
                seen_contacts,
            )

    # Layers
    for _s, lv in layers:
        lname = lv.get("name", "")
        if lname:
            _scan_string_for_contacts(
                lname,
                ProvenanceSource("CLayer.name", "layer", lname),
                contacts,
                seen_contacts,
            )
        ltex = lv.get("tex_file", "")
        if ltex:
            items.append(
                ProvenanceItem(
                    "texture_path",
                    ltex,
                    ProvenanceSource("CLayer.tex_file", "layer", lname),
                )
            )
        ldicts = _extract_legacy_attribute_dictionaries(lv.get("attrs"))
        if ldicts:
            _record_attribute_dictionaries(
                ldicts,
                "CLayer > CAttributeNamed {name!r}",
                "layer",
                lname,
                items,
                contacts,
                seen_contacts,
            )

    # Styles & scenes in document tail
    seen_style_paths: set = set()
    for st_off, st_name, st_desc, st_path in _read_classic_styles_in_tail(
        data, ar.r.pos, ar.ver
    ):
        if st_name:
            _scan_string_for_contacts(
                st_name,
                ProvenanceSource(f"CSkpStyle.name @{st_off:#06x}", "style", st_name),
                contacts,
                seen_contacts,
            )
        if st_desc:
            _scan_string_for_contacts(
                st_desc,
                ProvenanceSource(
                    f"CSkpStyle.description @{st_off:#06x}", "style", st_name
                ),
                contacts,
                seen_contacts,
            )
        if st_path and st_path not in seen_style_paths:
            seen_style_paths.add(st_path)
            items.append(
                ProvenanceItem(
                    "style_path",
                    st_path,
                    ProvenanceSource(f"CSkpStyle.path @{st_off:#06x}", "style", st_name),
                )
            )

    for sname, sdesc in _read_classic_scene_texts(data, ar):
        if sname:
            _scan_string_for_contacts(
                sname,
                ProvenanceSource("CViewPage.name", "scene", sname),
                contacts,
                seen_contacts,
            )
        if sdesc:
            _scan_string_for_contacts(
                sdesc,
                ProvenanceSource("CViewPage.description", "scene", sname),
                contacts,
                seen_contacts,
            )

    # ShadowInfo + premodel attribute dictionaries
    georef_dict = premodel_dicts.get("GeoReference")
    is_georef = bool(georef_dict.get("UsesGeoReferencing")) if isinstance(georef_dict, dict) else False
    sh_info = _read_classic_shadow_info(data, ar)
    if sh_info is not None:
        sh_off = sh_info["offset"]
        sh_src = ProvenanceSource(f"CShadowInfo @{sh_off:#06x}", "model")
        items.append(
            ProvenanceItem(
                "geolocation",
                {
                    "kind": "ShadowInfo",
                    "city": sh_info["city"],
                    "country": sh_info["country"],
                    "latitude": sh_info["latitude"],
                    "longitude": sh_info["longitude"],
                    "tz_offset": sh_info["tz_offset"],
                    "is_georeferenced": is_georef,
                },
                sh_src,
            )
        )
        if sh_info["city"]:
            _scan_string_for_contacts(
                sh_info["city"],
                ProvenanceSource(f"CShadowInfo.City @{sh_off:#06x}", "model"),
                contacts,
                seen_contacts,
            )
        if sh_info["country"]:
            _scan_string_for_contacts(
                sh_info["country"],
                ProvenanceSource(f"CShadowInfo.Country @{sh_off:#06x}", "model"),
                contacts,
                seen_contacts,
            )

    if premodel_dicts:
        _record_attribute_dictionaries(
            premodel_dicts,
            "CSketchUpModel > CAttributeNamed {name!r}",
            "model",
            "",
            items,
            contacts,
            seen_contacts,
        )

    items.extend(contacts)
    derived = _build_derived_summary([it for it in items if it.kind in _PATH_KINDS])

    return ProvenanceReport(
        file=str(skp_path),
        format="classic",
        sketchup_version=version,
        file_guid=file_guid,
        model_name=model_name,
        model_description=model_desc,
        items=tuple(items),
        derived=derived,
        unread_root_entities=ar.unread_root_entities,
    )


# ── Public entry point ───────────────────────────────────────────────────


def provenance_report(path: str | os.PathLike[str]) -> ProvenanceReport:
    """Inspect *path* and return a :class:`ProvenanceReport` of everything the
    ``.skp`` file stores about where it came from, plus path-derived summaries.
    """
    skp_path = pathlib.Path(path)
    with skp_path.open("rb") as f:
        header = f.read(512)
    if not header.startswith(b"\xff\xfe\xff\x0e"):
        raise SkpParseError(
            "Not a valid SketchUp file (bad header magic)", stage="header"
        )
    if is_legacy(header):
        return _provenance_classic(skp_path)
    return _provenance_vff(skp_path, header)
