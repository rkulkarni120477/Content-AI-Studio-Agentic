"""Safe extraction of an uploaded IMSCC (.zip) package.

Guards against the classic archive attacks before anything is written to disk:
  * path traversal  — entries with ``..`` or absolute/drive paths are rejected.
  * zip bombs       — per-entry and total uncompressed-size caps + a compression
                      ratio ceiling.
  * entry-count DoS — a cap on the number of members.

On success the archive is unpacked into ``workdir`` and the location of
``imsmanifest.xml`` is returned. A missing/corrupt archive or missing manifest
is a *fatal* import error (clean message), distinct from per-item warnings that
the parser collects later. See reverse_cas.md §S1.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath

# Caps. IMSCC packages can legitimately be large (PRD allows ~2 GB), but the
# validate path streams to a temp dir, so we cap total uncompressed bytes and the
# compression ratio to stop zip bombs. Tune via callers if needed.
MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024      # 2 GB total
MAX_SINGLE_FILE_BYTES = 512 * 1024 * 1024            # 512 MB per entry
MAX_ENTRY_COUNT = 50_000
MAX_COMPRESSION_RATIO = 200                            # uncompressed/compressed per entry

MANIFEST_NAME = "imsmanifest.xml"


class PackageValidationError(Exception):
    """Fatal problem with the uploaded package (corrupt zip, no manifest, unsafe)."""


@dataclass
class ExtractResult:
    root: str            # directory that directly contains imsmanifest.xml
    manifest_path: str   # absolute path to imsmanifest.xml
    file_count: int
    total_bytes: int


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
    import os

    top = os.path.join(root, MANIFEST_NAME)
    if os.path.isfile(top):
        return top
    for dirpath, _dirs, files in os.walk(root):
        if MANIFEST_NAME in files:
            return os.path.join(dirpath, MANIFEST_NAME)
    return None


def extract_package(data: bytes, workdir: str) -> ExtractResult:
    """Safely unzip ``data`` into ``workdir`` and locate the manifest.

    Parameters
    ----------
    data : raw bytes of the uploaded .imscc/.zip file.
    workdir : an existing, caller-owned temp directory to extract into.

    Raises
    ------
    PackageValidationError : corrupt archive, unsafe member, cap exceeded, or no
        manifest found.
    """
    try:
        zf = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise PackageValidationError("Uploaded file is not a valid zip archive.") from exc

    with zf:
        file_count, total_bytes = _validate_archive(zf)
        # Members are validated as safe above, so extractall is now safe to use.
        zf.extractall(workdir)

    manifest_path = _find_manifest(workdir)
    if manifest_path is None:
        raise PackageValidationError(
            "No imsmanifest.xml found — this does not look like an IMSCC package."
        )

    import os

    return ExtractResult(
        root=os.path.dirname(manifest_path),
        manifest_path=manifest_path,
        file_count=file_count,
        total_bytes=total_bytes,
    )
