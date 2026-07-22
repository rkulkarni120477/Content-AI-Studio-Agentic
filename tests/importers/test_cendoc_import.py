"""Tests for CendocXML package detection, parse, and non-regression precedence."""

from __future__ import annotations

import re

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
    assert counts["modules"] >= 2  # front matter + chapter (+ appendices)
    assert counts["pages"] >= 2
    assert counts["quizzes"] >= 1
    assert any(m.title == "Front Matter" for m in course.modules)


def test_parse_cendoc_bodies_and_images():
    course = parse_package(build_sample_cendoc())
    assert course.package_format == FORMAT_CENDOC
    front = next(m for m in course.modules if m.title == "Front Matter")
    assert any("Dedicated to testers" in (p.markdown or "") for p in front.items)

    # First chapter module with two lessons
    chapter = next(m for m in course.modules if m.title == "An Overview")
    assert len([i for i in chapter.items if getattr(i, "kind", "") == "page"]) == 2
    lesson = chapter.items[0]
    md = lesson.markdown or ""
    assert "Marketing" in md or "value" in md
    assert "data:image" in md or "book_images/" in md
    assert "A Nested Topic" in md
    assert "Chapter 2" in md  # xref pre-text + ordinal
    assert "Source: Smith, Marketing 101." in md
    assert "Define the term marketing" in md
    assert "exchange" in md and "(p. 2)" in md
    assert "Adapt or perish" in md
    assert "Sample Matrix" in md or "Table" in md
    assert "Price" in md and "Revenue" in md
    assert lesson.raw_body_html

    lesson2 = next(i for i in chapter.items if getattr(i, "kind", "") == "page" and "Why Study" in (i.title or ""))
    md2 = lesson2.markdown or ""
    assert "The Insurance Industry" in md2
    assert "companies create the products" in md2
    assert "Bullet alpha" in md2
    # Unordered must stay bullets, not numbered (regression: 'ordered' in 'unordered')
    assert re.search(r"(?m)^- Bullet alpha", md2)


def test_list_md_keeps_all_item_paras():
    from xml.etree import ElementTree as ET

    from promptops_app.importers.cendoc_to_markdown import CENDOC_NS, _list_html, _list_md

    xml = f"""<?xml version="1.0"?>
    <cl:list xmlns:cl="{CENDOC_NS}" list-style="Ordered" numeration="arabic">
      <cl:item>
        <cl:para><cl:style styles="bold">The Automotive Industry</cl:style></cl:para>
        <cl:para>While the vehicle itself is often built according to market orientation.</cl:para>
      </cl:item>
    </cl:list>
    """
    el = ET.fromstring(xml)
    md = _list_md(el)
    assert "The Automotive Industry" in md
    assert "market orientation" in md
    html = _list_html(el)
    assert "The Automotive Industry" in html
    assert "market orientation" in html
    assert html.count("<p>") >= 2


def test_unordered_list_not_treated_as_ordered():
    from xml.etree import ElementTree as ET

    from promptops_app.importers.cendoc_to_markdown import CENDOC_NS, _list_md

    xml = f"""<?xml version="1.0"?>
    <cl:list xmlns:cl="{CENDOC_NS}" list-style="Unordered">
      <cl:item><cl:para>Only bullet</cl:para></cl:item>
    </cl:list>
    """
    md = _list_md(ET.fromstring(xml))
    assert md.startswith("- ")
    assert not md.startswith("1.")


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
