"""Tests for the safe IMSCC package extractor (Session 1)."""

from __future__ import annotations

import pytest

from promptops_app.importers.package_extractor import (
    PackageValidationError,
    extract_package,
)
from tests.importers.fixtures import (
    build_sample_imscc,
    build_zip_with_traversal,
    build_zip_without_manifest,
)


def test_extracts_and_locates_manifest(tmp_path):
    result = extract_package(build_sample_imscc(), str(tmp_path))
    assert result.manifest_path.endswith("imsmanifest.xml")
    assert result.file_count > 0
    assert result.total_bytes > 0


def test_rejects_non_zip(tmp_path):
    with pytest.raises(PackageValidationError):
        extract_package(b"this is not a zip file", str(tmp_path))


def test_rejects_missing_manifest(tmp_path):
    with pytest.raises(PackageValidationError):
        extract_package(build_zip_without_manifest(), str(tmp_path))


def test_rejects_path_traversal(tmp_path):
    with pytest.raises(PackageValidationError):
        extract_package(build_zip_with_traversal(), str(tmp_path))
