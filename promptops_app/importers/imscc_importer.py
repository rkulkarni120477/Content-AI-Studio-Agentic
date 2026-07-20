"""IMSCC import orchestrator.

The single entry point the API/job layer calls. Session 1 implements
``validate_package`` (extract + structural parse, no DB writes); the full
build/reverse-generate pipeline is added in later sessions. See reverse_cas.md.
"""

from __future__ import annotations

import tempfile

from promptops_app.importers import canvas_parser
from promptops_app.importers.internal_model import ICourse
from promptops_app.importers.package_extractor import extract_package


def validate_package(data: bytes) -> ICourse:
    """Extract + structurally parse an IMSCC package. No database writes.

    Returns an :class:`ICourse` carrying modules, ordered item stubs, staged
    resource list, and any per-item warnings. Raises
    :class:`~promptops_app.importers.package_extractor.PackageValidationError`
    on a fatal package problem (corrupt zip / no manifest).
    """
    with tempfile.TemporaryDirectory(prefix="imscc_validate_") as workdir:
        extract = extract_package(data, workdir)
        return canvas_parser.parse_structure(extract)


def parse_package(data: bytes) -> ICourse:
    """Extract + fully parse an IMSCC package (structure **and** content).

    Unlike :func:`validate_package`, this loads every page body as markdown and
    every quiz as normalised questions — the complete :class:`ICourse` the
    reconstruction job (Session 3) turns into CourseModule/Generation/Block rows.
    Still performs **no** database writes. Raises
    :class:`~promptops_app.importers.package_extractor.PackageValidationError`
    on a fatal package problem.

    Note: the extracted files live in a temp dir that is deleted on return, so
    the caller must consume ``ICourse`` content before this function exits.
    """
    with tempfile.TemporaryDirectory(prefix="imscc_import_") as workdir:
        extract = extract_package(data, workdir)
        return canvas_parser.parse_course(extract)
