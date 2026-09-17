"""The .doc extractor depends on a binary the image must actually carry.

`extract_docx` handles the OOXML container only; a pre-2007 binary .doc raises
there and falls through to `_extract_legacy_doc`, which shells out to `antiword`.
When the binary is absent that helper logs and returns EMPTY TEXT — no exception,
no failed job — so the pipeline stores a document with nothing in it and reports
success.

That is what happened: `antiword` was never added to dis_backend/Dockerfile, and
110 of AIM's 121 .doc files (final cumulative exams, landing-gear and ice & rain
project keys) ingested empty and sat that way for a month while every job said
"completed". Verified 2026-08-27 that antiword recovers 12/12 of a random sample.

These tests fail if the dependency is dropped from the image again, and pin that
health reports it rather than leaving it to be discovered from output quality.
"""
from __future__ import annotations

import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"


def _apt_packages(text: str) -> set[str]:
    """Package names from the image's apt-get install block.

    Line continuations are joined FIRST. Matching them inside the pattern is the
    obvious approach and it silently returns an empty package set (the trailing
    backslash is consumed before the continuation can match), which would make
    this whole module pass while asserting nothing.
    """
    joined = text.replace("\\\n", " ")
    m = re.search(r"apt-get install -y(.*)", joined)
    assert m, "no apt-get install block in the Dockerfile"
    body = m.group(1).split("&&")[0]
    return {tok for tok in body.split() if tok and not tok.startswith("-")}


def test_image_installs_antiword():
    pkgs = _apt_packages(DOCKERFILE.read_text())
    assert "antiword" in pkgs, (
        "antiword is missing from dis_backend/Dockerfile. Legacy .doc uploads will "
        "extract to empty text and be indexed as successful — silently, with no "
        "failed job and no error surfaced to the user."
    )
    assert "tesseract-ocr" in pkgs, (
        "tesseract-ocr is missing from dis_backend/Dockerfile. Image-only DOCX "
        "exam figures will extract to empty text when the OOXML walker finds no "
        "body paragraphs."
    )


def test_legacy_doc_path_still_depends_on_that_binary():
    """If this stops being true, the Dockerfile assertion above is protecting
    nothing and should be revisited rather than left as decoration."""
    src = (DOCKERFILE.parent / "services/pipeline/extractors.py").read_text()
    assert "antiword" in src
    assert 'extractors.get(ext' in src and '"doc":' in src


def test_health_reports_a_missing_extractor_binary_as_degraded(monkeypatch):
    import main

    monkeypatch.setattr("shutil.which", lambda name: None)
    deps = main._extractor_dependencies()
    assert deps["antiword"]["present"] is False
    assert deps["antiword"]["used_for"]
    assert deps["tesseract"]["present"] is False


def test_health_reports_the_binary_when_present(monkeypatch):
    import main

    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    assert main._extractor_dependencies()["antiword"]["present"] is True
