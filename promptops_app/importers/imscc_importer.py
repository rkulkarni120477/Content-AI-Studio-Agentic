"""Course package import orchestrator (IMSCC + CendocXML).

Thin format dispatch at the entry point. When the package is IMSCC, the existing
``canvas_parser`` path runs unchanged. CendocXML uses ``cendoc_parser``.
"""

from __future__ import annotations

import tempfile

from promptops_app.importers import canvas_parser, cendoc_parser
from promptops_app.importers.internal_model import ICourse
from promptops_app.importers.package_extractor import (
    FORMAT_CENDOC,
    FORMAT_IMSCC,
    extract_package,
)


def validate_package(data: bytes) -> ICourse:
    """Extract + structurally parse a course package. No database writes.

    Returns an :class:`ICourse` carrying modules, ordered item stubs, staged
    resource list, and any per-item warnings. Raises
    :class:`~promptops_app.importers.package_extractor.PackageValidationError`
    on a fatal package problem (corrupt zip / unsupported format).
    """
    with tempfile.TemporaryDirectory(prefix="import_validate_") as workdir:
        extract = extract_package(data, workdir)
        if extract.format == FORMAT_CENDOC:
            course = cendoc_parser.parse_structure(extract)
        else:
            course = canvas_parser.parse_structure(extract)
        course.package_format = extract.format or FORMAT_IMSCC
        return course


def parse_package(data: bytes) -> ICourse:
    """Extract + fully parse a course package (structure **and** content).

    Unlike :func:`validate_package`, this loads every page body as markdown and
    every quiz / assessment body — the complete :class:`ICourse` the
    reconstruction job turns into CourseModule/Generation/Block rows.
    Still performs **no** database writes.

    Note: the extracted files live in a temp dir that is deleted on return, so
    the caller must consume ``ICourse`` content before this function exits.
    Cendoc image data-URIs are embedded into markdown/HTML before return.
    """
    with tempfile.TemporaryDirectory(prefix="import_pkg_") as workdir:
        extract = extract_package(data, workdir)
        if extract.format == FORMAT_CENDOC:
            course = cendoc_parser.parse_course(extract)
        else:
            course = canvas_parser.parse_course(extract)
        course.package_format = extract.format or FORMAT_IMSCC
        return course
