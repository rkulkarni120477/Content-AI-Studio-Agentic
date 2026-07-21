"""Tests for CendocXML package detection, parse, and non-regression precedence."""

from __future__ import annotations

import pytest

from promptops_app.importers.imscc_importer import parse_package, validate_package
from promptops_app.importers.package_extractor import (
    FORMAT_CENDOC,
    FORMAT_IMSCC,
    PackageValidationError,
    detect_package_format,
    extract_package,
)
from tests.importers.fixtures import (
    build_ambiguous_imscc_and_cendoc,
    build_sample_cendoc,
    build_sample_imscc,
)


def test_detect_cendoc_zip():
    assert detect_package_format(build_sample_cendoc()) == FORMAT_CENDOC


def test_detect_imscc_unchanged():
    assert detect_package_format(build_sample_imscc()) == FORMAT_IMSCC


def test_imscc_wins_when_both_present():
    data = build_ambiguous_imscc_and_cendoc()
    assert detect_package_format(data) == FORMAT_IMSCC


def test_extract_cendoc(tmp_path):
    result = extract_package(build_sample_cendoc(), str(tmp_path))
    assert result.format == FORMAT_CENDOC
    assert result.xml_path and result.xml_path.endswith(".xml")
    assert result.images_dir is not None
    assert result.manifest_path == ""


def test_extract_imscc_still_sets_manifest(tmp_path):
    result = extract_package(build_sample_imscc(), str(tmp_path))
    assert result.format == FORMAT_IMSCC
    assert result.manifest_path.endswith("imsmanifest.xml")
    assert result.xml_path is None


def test_extract_ambiguous_is_imscc(tmp_path):
    result = extract_package(build_ambiguous_imscc_and_cendoc(), str(tmp_path))
    assert result.format == FORMAT_IMSCC
    assert result.manifest_path.endswith("imsmanifest.xml")


def test_validate_cendoc_structure():
    course = validate_package(build_sample_cendoc())
    assert course.package_format == FORMAT_CENDOC
    assert course.title == "Sample Cendoc Book"
    counts = course.structure_counts()
    assert counts["modules"] >= 1  # chapter + appendices
    assert counts["pages"] >= 2
    assert counts["quizzes"] >= 1
    assert any("Front matter" in w for w in course.warnings)


def test_parse_cendoc_bodies_and_images():
    course = parse_package(build_sample_cendoc())
    assert course.package_format == FORMAT_CENDOC
    # First chapter module with two lessons
    chapter = course.modules[0]
    assert chapter.title == "An Overview"
    assert len([i for i in chapter.items if getattr(i, "kind", "") == "page"]) == 2
    lesson = chapter.items[0]
    assert "Marketing" in (lesson.markdown or "") or "value" in (lesson.markdown or "")
    assert "data:image" in (lesson.markdown or "") or "book_images/" in (lesson.markdown or "")
    assert "A Nested Topic" in (lesson.markdown or "")
    assert lesson.raw_body_html


def test_parse_cendoc_missing_image_warns():
    course = parse_package(build_sample_cendoc(missing_image=True))
    assert any("not found" in w.lower() or "fig1.png" in w for w in course.warnings)


def test_imscc_validate_still_works():
    course = validate_package(build_sample_imscc())
    assert course.package_format == FORMAT_IMSCC
    assert course.title == "Sample Course"
    assert course.structure_counts()["modules"] == 2


def test_rejects_unsupported_zip(tmp_path):
    from tests.importers.fixtures import build_zip_without_manifest

    with pytest.raises(PackageValidationError):
        extract_package(build_zip_without_manifest(), str(tmp_path))
