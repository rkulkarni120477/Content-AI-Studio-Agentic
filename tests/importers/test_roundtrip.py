"""End-to-end IMSCC round-trip + provenance-aware export (Session 7).

import → export → re-import must preserve structure (>=95%) and content (>=90%),
and — when Canvas item ids are supplied — reuse them so items map back exactly.
The default export path (no item_ids) stays byte-compatible (fresh ids).
"""

from __future__ import annotations

import re

from promptops_app.exporters.imscc_exporter import build_imscc
from promptops_app.importers.imscc_importer import parse_package
from promptops_app.importers.internal_model import IPage, IAssessment
from tests.importers.fixtures import build_content_imscc


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) > 2}


def _prose_words(md: str) -> set[str]:
    # Strip ATX headings — a page's title is preserved as its item label, not prose.
    return _words(re.sub(r"(?m)^#{1,6}.*$", "", md or ""))


def _to_export_args(course):
    blocks, item_ids, modules = [], [], []
    g = 0
    for m in course.modules:
        idxs = []
        for it in m.items:
            if isinstance(it, IPage):
                blocks.append((it.title, it.markdown or "", "lesson", it.raw_body_html or ""))
            else:
                blocks.append((it.title, it.body_markdown or "", it.kind, ""))
            item_ids.append(it.provenance_id)
            idxs.append(g)
            g += 1
        modules.append((m.title, idxs))
    return blocks, modules, item_ids


def _struct(course):
    return [(m.title, [(i.kind, i.title) for i in m.items]) for m in course.modules]


def test_roundtrip_structure_and_content_fidelity():
    course1 = parse_package(build_content_imscc())
    blocks, modules, item_ids = _to_export_args(course1)

    exported = build_imscc("Round Trip", blocks, modules=modules, item_ids=item_ids).read()
    course2 = parse_package(exported)

    # Structure fidelity (>= 95% — here exact).
    assert _struct(course1) == _struct(course2)

    # Content fidelity (>= 90%).
    ratios, q_counts = [], []
    for m1, m2 in zip(course1.modules, course2.modules):
        for i1, i2 in zip(m1.items, m2.items):
            if isinstance(i1, IPage):
                w1, w2 = _prose_words(i1.markdown), _prose_words(i2.markdown)
                if w1:
                    ratios.append(len(w1 & w2) / len(w1))
            elif isinstance(i1, IAssessment):
                q_counts.append((len(i1.questions), len(i2.questions)))
    assert ratios and sum(ratios) / len(ratios) >= 0.90
    assert all(a == b for a, b in q_counts)          # quizzes round-trip through QTI


def test_provenance_aware_export_reuses_canvas_ids():
    course1 = parse_package(build_content_imscc())
    blocks, modules, item_ids = _to_export_args(course1)

    course2 = parse_package(build_imscc("RT", blocks, modules=modules, item_ids=item_ids).read())
    reimported_ids = [it.provenance_id for m in course2.modules for it in m.items]
    assert reimported_ids == item_ids


def test_default_export_is_unchanged_fresh_ids():
    course1 = parse_package(build_content_imscc())
    blocks, modules, item_ids = _to_export_args(course1)

    # No item_ids → today's behavior: valid package, same structure, fresh ids.
    course3 = parse_package(build_imscc("RT", blocks, modules=modules).read())
    assert _struct(course3) == _struct(course1)
    fresh = [it.provenance_id for m in course3.modules for it in m.items]
    assert all(x not in item_ids for x in fresh)
