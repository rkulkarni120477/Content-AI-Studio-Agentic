"""Safe extraction of an uploaded course package (IMSCC or CendocXML).

Guards against the classic archive attacks before anything is written to disk:
  * path traversal  — entries with ``..`` or absolute/drive paths are rejected.
  * zip bombs       — per-entry and total uncompressed-size caps + a compression
                      ratio ceiling.
  * entry-count DoS — a cap on the number of members.

IMSCC-first: if ``imsmanifest.xml`` is present the package is always treated as
IMSCC (even when a Cendoc XML is also present). See reverse_cas.md §S1.
"""

from __future__ import annotations

import os
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath

# Caps. Not a product-facing size limit — real course packages should never get
# close to these. This exists only to stop a zip bomb (a small, valid archive that
# claims to decompress to an absurd size) from hanging or OOM-killing the process;
# entry count and compression ratio catch that pattern directly, the byte ceilings
# below are a generous backstop in case a package is huge but honest.
MAX_UNCOMPRESSED_BYTES = 20 * 1024 * 1024 * 1024     # 20 GB total
MAX_SINGLE_FILE_BYTES = 4 * 1024 * 1024 * 1024       # 4 GB per entry
MAX_ENTRY_COUNT = 50_000
MAX_COMPRESSION_RATIO = 200                            # uncompressed/compressed per entry

MANIFEST_NAME = "imsmanifest.xml"
FORMAT_IMSCC = "imscc"
FORMAT_CENDOC = "cendoc"
CENDOC_NS_HINT = "xml.cengage-learning.com/cendoc-core"
BOOK_IMAGES_DIR = "book_images"


class PackageValidationError(Exception):
    """Fatal problem with the uploaded package (corrupt zip, no manifest, unsafe)."""


@dataclass
class ExtractResult:
    root: str            # IMSCC: dir with imsmanifest.xml; Cendoc: extract root
    manifest_path: str   # IMSCC: path to imsmanifest.xml; Cendoc: "" (unused)
    file_count: int
    total_bytes: int
    # Additive optional fields — defaults preserve existing IMSCC callers.
    format: str = FORMAT_IMSCC
    xml_path: str | None = None
    images_dir: str | None = None


def _is_unsafe_member(name: str) -> bool:
    """True if an archive member name would escape the extraction root."""
    if not name or name.endswith("/"):
        return False  # directory entries are harmless
    # Normalise separators; reject absolute paths, drive letters and parent refs.
    posix = name.replace("\\", "/")
    if posix.startswith("/") or (len(posix) >= 2 and posix[1] == ":"):
        return True
    parts = PurePosixPath(posix).parts
    return ".." in parts


def _validate_archive(zf: zipfile.ZipFile) -> tuple[int, int]:
    """Pre-flight the archive without extracting. Returns (file_count, total_bytes)."""
    infos = zf.infolist()
    if len(infos) > MAX_ENTRY_COUNT:
        raise PackageValidationError(
            f"Package has too many entries ({len(infos)} > {MAX_ENTRY_COUNT})."
        )

    total = 0
    for info in infos:
        if _is_unsafe_member(info.filename):
            raise PackageValidationError(
                f"Unsafe path in package: {info.filename!r} (path traversal blocked)."
            )
        if info.file_size > MAX_SINGLE_FILE_BYTES:
            raise PackageValidationError(
                f"Entry {info.filename!r} is too large ({info.file_size} bytes)."
            )
        # Ratio guard (skip tiny/stored entries where compress_size is 0).
        if info.compress_size > 0 and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
            raise PackageValidationError(
                f"Entry {info.filename!r} exceeds the allowed compression ratio "
                f"(possible zip bomb)."
            )
        total += info.file_size

    if total > MAX_UNCOMPRESSED_BYTES:
        raise PackageValidationError(
            f"Package is too large uncompressed ({total} > {MAX_UNCOMPRESSED_BYTES} bytes)."
        )
    return len(infos), total


def _find_manifest(root: str) -> str | None:
    """Locate imsmanifest.xml — top-level first, then anywhere in the tree."""
    top = os.path.join(root, MANIFEST_NAME)
    if os.path.isfile(top):
        return top
    for dirpath, _dirs, files in os.walk(root):
        if MANIFEST_NAME in files:
            return os.path.join(dirpath, MANIFEST_NAME)
    return None


def _looks_like_cendoc_xml(path: str) -> bool:
    """True if ``path`` is a CendocXML document (cl:doc / cendoc-core namespace)."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(4096).decode("utf-8", errors="ignore")
    except OSError:
        return False
    lower = head.lower()
    if CENDOC_NS_HINT in lower:
        return True
    return "<cl:doc" in lower or "<doc " in lower and "cendoc" in lower


def _find_cendoc_xml(root: str) -> str | None:
    """Locate a CendocXML file — prefer top-level ``cendoc-*.xml``, then any match."""
    top_candidates: list[str] = []
    deep_candidates: list[str] = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.lower().endswith(".xml"):
                continue
            path = os.path.join(dirpath, name)
            if not _looks_like_cendoc_xml(path):
                continue
            if os.path.dirname(path) == root:
                top_candidates.append(path)
            else:
                deep_candidates.append(path)
    if top_candidates:
        # Prefer filenames that start with cendoc-
        preferred = [p for p in top_candidates if os.path.basename(p).lower().startswith("cendoc")]
        return sorted(preferred or top_candidates)[0]
    if deep_candidates:
        return sorted(deep_candidates)[0]
    return None


def _find_book_images(root: str) -> str | None:
    """Return path to ``book_images`` directory if present."""
    top = os.path.join(root, BOOK_IMAGES_DIR)
    if os.path.isdir(top):
        return top
    for dirpath, dirs, _files in os.walk(root):
        if BOOK_IMAGES_DIR in dirs:
            return os.path.join(dirpath, BOOK_IMAGES_DIR)
    return None


def _is_zip_bytes(data: bytes) -> bool:
    return len(data) >= 2 and data[:2] == b"PK"


def _is_raw_cendoc_xml(data: bytes) -> bool:
    head = data[:4096].decode("utf-8", errors="ignore").lower()
    return CENDOC_NS_HINT in head or "<cl:doc" in head


def detect_package_format(data: bytes) -> str:
    """Sniff package format without extracting. IMSCC wins if manifest is present.

    Raises
    ------
    PackageValidationError : empty, unreadable, or unsupported package.
    """
    if not data:
        raise PackageValidationError("Uploaded package is empty.")

    if _is_raw_cendoc_xml(data) and not _is_zip_bytes(data):
        return FORMAT_CENDOC

    if not _is_zip_bytes(data):
        raise PackageValidationError(
            "Uploaded file is not a valid zip archive or CendocXML document."
        )

    try:
        zf = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise PackageValidationError("Uploaded file is not a valid zip archive.") from exc

    with zf:
        names = [n.replace("\\", "/") for n in zf.namelist()]
        # IMSCC-first precedence.
        if any(n.rstrip("/").endswith(MANIFEST_NAME) for n in names):
            return FORMAT_IMSCC
        # Peek XML members for Cendoc signature.
        for name in names:
            if not name.lower().endswith(".xml") or name.endswith("/"):
                continue
            try:
                head = zf.read(name)[:4096].decode("utf-8", errors="ignore").lower()
            except KeyError:
                continue
            if CENDOC_NS_HINT in head or "<cl:doc" in head:
                return FORMAT_CENDOC

    raise PackageValidationError(
        "No imsmanifest.xml or CendocXML document found — "
        "supported packages are Canvas IMSCC and Cengage CendocXML."
    )


def extract_package(data: bytes, workdir: str) -> ExtractResult:
    """Safely unpack ``data`` into ``workdir`` and detect package format.

    Parameters
    ----------
    data : raw bytes of the uploaded .imscc/.zip file, or a bare CendocXML document.
    workdir : an existing, caller-owned temp directory to extract into.

    Raises
    ------
    PackageValidationError : corrupt archive, unsafe member, cap exceeded, or no
        supported package format found.
    """
    # Bare CendocXML (not a zip) — write into workdir and treat as cendoc.
    if _is_raw_cendoc_xml(data) and not _is_zip_bytes(data):
        xml_name = "cendoc.xml"
        xml_path = os.path.join(workdir, xml_name)
        with open(xml_path, "wb") as handle:
            handle.write(data)
        return ExtractResult(
            root=workdir,
            manifest_path="",
            file_count=1,
            total_bytes=len(data),
            format=FORMAT_CENDOC,
            xml_path=xml_path,
            images_dir=None,
        )

    try:
        zf = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise PackageValidationError("Uploaded file is not a valid zip archive.") from exc

    with zf:
        file_count, total_bytes = _validate_archive(zf)
        # Members are validated as safe above, so extractall is now safe to use.
        zf.extractall(workdir)

    # IMSCC-first: never fall through to Cendoc when a manifest exists.
    manifest_path = _find_manifest(workdir)
    if manifest_path is not None:
        return ExtractResult(
            root=os.path.dirname(manifest_path),
            manifest_path=manifest_path,
            file_count=file_count,
            total_bytes=total_bytes,
            format=FORMAT_IMSCC,
            xml_path=None,
            images_dir=None,
        )

    xml_path = _find_cendoc_xml(workdir)
    if xml_path is not None:
        return ExtractResult(
            root=workdir,
            manifest_path="",
            file_count=file_count,
            total_bytes=total_bytes,
            format=FORMAT_CENDOC,
            xml_path=xml_path,
            images_dir=_find_book_images(workdir),
        )

    raise PackageValidationError(
        "No imsmanifest.xml or CendocXML document found — "
        "supported packages are Canvas IMSCC and Cengage CendocXML."
    )
