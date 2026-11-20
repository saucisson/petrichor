"""Protected archive ingestion: extract PNML candidates from zip/tgz/tar archives.

Zip-slip protection (spec §21.3, ADR-0008): absolute paths, drive letters and
``..`` segments are refused, and the refused members are listed in the typed
error. Shared by the catalog fetch (P69) and the server-side ingestion of
uploaded archives / URLs (P70).
"""

import gzip
import hashlib
import io
import tarfile
import xml.etree.ElementTree as ET
import zipfile
from typing import Literal

from pydantic import BaseModel, Field

CONTENT_TYPE_ZIP = "application/zip"
CONTENT_TYPE_GZIP = "application/gzip"
CONTENT_TYPE_TAR = "application/x-tar"
ARCHIVE_CONTENT_TYPES = frozenset({CONTENT_TYPE_ZIP, CONTENT_TYPE_GZIP, CONTENT_TYPE_TAR})
"""The archive media types accepted for ingestion (P70, spec §21.3)."""

PNML_SUFFIXES = (".pnml", ".xml")
"""File suffixes recognized as PNML documents inside an archive."""

SYMMETRIC_NET_TYPE_MARKER = "symmetric"
DRIVE_LETTER_MIN_LENGTH = 2
"""Minimum length of a Windows drive-letter segment (``C:``)."""
"""Marker of the Symmetric net type in the PNML ``net`` element (pnml_import)."""


class ArchiveError(Exception):
    """Base class of the typed archive-ingestion errors."""


class UnsafeArchiveError(ArchiveError):
    """The archive contains refused members (absolute paths or ``..``)."""

    def __init__(self, *, refused: list[str]) -> None:
        """Carry the refused member names in the message."""
        self.refused = refused
        super().__init__(f"Unsafe archive members refused: {', '.join(refused)}")


class NoPnmlError(ArchiveError):
    """The archive contains no PNML document."""


class CorruptArchiveError(ArchiveError):
    """The archive bytes cannot be decoded as their declared format."""


class MalformedPnmlError(ArchiveError):
    """A PNML member is not UTF-8 or not well-formed XML."""


class PnmlCandidate(BaseModel):
    """One PNML document extracted from an archive, with its provenance data."""

    name: str
    """The archive member path of the PNML document."""
    size_bytes: int = Field(ge=0)
    sha256: str
    """``sha256:`` + 64 hex digits of the PNML content."""
    content: str
    """The PNML text (UTF-8)."""
    formalism: Literal["pt", "symmetric"]
    """The formalism detected from the PNML net type."""


def detect_formalism(*, text: str) -> Literal["pt", "symmetric"]:
    """Detect the formalism from the first ``net`` element's type attribute."""
    root = ET.fromstring(text)  # noqa: S314 - PNML is trusted input, same as pnml_import
    net = next(
        (element for element in root.iter() if element.tag.split("}")[-1] == "net"),
        None,
    )
    net_type = (net.get("type") if net is not None else None) or ""
    return "symmetric" if SYMMETRIC_NET_TYPE_MARKER in net_type.lower() else "pt"


def _safe_member_name(*, name: str) -> str | None:
    """Return the normalized member name, or None when it must be refused."""
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("~"):
        return None
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        return None
    if any(len(part) >= DRIVE_LETTER_MIN_LENGTH and part[1] == ":" for part in parts):
        return None
    return "/".join(parts)


def _candidate_from_member(*, name: str, data: bytes) -> PnmlCandidate:
    """Build the candidate of one PNML member (UTF-8 decode + formalism)."""
    try:
        text = data.decode("utf-8")
        formalism = detect_formalism(text=text)
    except (UnicodeDecodeError, ET.ParseError) as error:
        raise MalformedPnmlError(f"Membre PNML illisible {name!r} : {error}") from error
    return PnmlCandidate(
        name=name,
        size_bytes=len(data),
        sha256=f"sha256:{hashlib.sha256(data).hexdigest()}",
        content=text,
        formalism=formalism,
    )


def _collect(*, members: list[tuple[str, bytes]]) -> list[PnmlCandidate]:
    """Filter the safe PNML members and build the candidates."""
    refused: list[str] = []
    candidates: list[PnmlCandidate] = []
    for raw_name, data in members:
        safe = _safe_member_name(name=raw_name)
        if safe is None:
            refused.append(raw_name)
            continue
        if safe.lower().endswith(PNML_SUFFIXES):
            candidates.append(_candidate_from_member(name=safe, data=data))
    if refused:
        raise UnsafeArchiveError(refused=refused)
    return candidates


def _zip_members(*, data: bytes) -> list[tuple[str, bytes]]:
    """The (name, bytes) members of a zip archive; CorruptArchiveError when unreadable."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise CorruptArchiveError(f"Archive zip corrompue : {error}") from error
    with archive:
        return [
            (info.filename, archive.read(info)) for info in archive.infolist() if not info.is_dir()
        ]


def _tar_members(*, data: bytes) -> list[tuple[str, bytes]]:
    """The (name, bytes) file members of a tar/tgz archive."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        members = []
        for member in archive.getmembers():
            if not member.isfile():
                continue
            extracted = archive.extractfile(member)
            if extracted is not None:
                members.append((member.name, extracted.read()))
        return members


def _gzip_pnml_candidate(*, data: bytes) -> list[PnmlCandidate]:
    """A bare gzip stream may hold one gzipped PNML document."""
    try:
        decompressed = gzip.decompress(data)
    except OSError as error:
        raise CorruptArchiveError(f"Archive gzip corrompue : {error}") from error
    try:
        text = decompressed.decode("utf-8")
    except UnicodeDecodeError as error:
        raise MalformedPnmlError(f"Document gzip non UTF-8 : {error}") from error
    if text.lstrip().startswith("<"):
        return [_candidate_from_member(name="model.pnml", data=decompressed)]
    return []


def extract_pnml_candidates(*, data: bytes, content_type: str) -> list[PnmlCandidate]:
    """Extract the PNML candidates of an archive (zip, tgz/tar.gz, tar).

    Raises UnsafeArchiveError on zip-slip members, CorruptArchiveError on
    undecodable archive bytes, MalformedPnmlError on invalid PNML members and
    NoPnmlError when the archive holds no PNML document.
    """
    if content_type == CONTENT_TYPE_ZIP:
        candidates = _collect(members=_zip_members(data=data))
    elif content_type in (CONTENT_TYPE_GZIP, CONTENT_TYPE_TAR):
        try:
            candidates = _collect(members=_tar_members(data=data))
        except tarfile.TarError:
            # A bare gzip stream (not a tar): maybe a gzipped PNML document.
            candidates = _gzip_pnml_candidate(data=data)
    else:
        raise ArchiveError(f"Unsupported archive content type: {content_type!r}")
    if not candidates:
        raise NoPnmlError("The archive contains no PNML document.")
    return candidates
