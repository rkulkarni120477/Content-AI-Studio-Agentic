"""Parse an extracted Canvas IMSCC package into the internal course model.

Session 1 (this file) does the **structural** pass: it reads
``imsmanifest.xml`` (resource table + organization TOC) and
``course_settings/module_meta.xml`` (authoritative module + item order) and
produces an :class:`ICourse` of modules and ordered item *stubs* (title + href +
provenance id + kind). Page bodies and quiz questions are filled in Session 2.

The importer is the inverse of ``promptops_app/exporters/imscc_exporter.py`` —
see that module for the exact XML shapes this parser consumes.
"""

from __future__ import annotations

import hashlib
import os
import re
import xml.etree.ElementTree as ET
from html import unescape

from promptops_app.importers.html_to_markdown import page_html_to_markdown
from promptops_app.importers.internal_model import (
    ASSIGNMENT_KIND,
    DISCUSSION_KIND,
    ICourse,
    IModule,
    IPage,
    IAssessment,
    IResource,
    PAGE_KIND,
    QUIZ_KIND,
)
from promptops_app.importers.package_extractor import ExtractResult
from promptops_app.importers.qti_xml_parser import parse_qti_xml, questions_to_markdown

# Canvas module_meta content_type → normalised kind.
_CONTENT_TYPE_KIND = {
    "WikiPage": PAGE_KIND,
    "Quizzes::Quiz": QUIZ_KIND,
    "Assignment": ASSIGNMENT_KIND,
    "DiscussionTopic": DISCUSSION_KIND,
}

# content_types we recognise but cannot faithfully reconstruct in v1.
_UNSUPPORTED_CONTENT_TYPES = {
    "ContextExternalTool",
    "ExternalTool",
    "ExternalUrl",
    "Attachment",
}

_LEARNING_APP_RESOURCE = "learning-application-resource"
_ASSESSMENT_TYPE_HINT = "imsqti"
_MODULE_META_REL = os.path.join("course_settings", "module_meta.xml")


def _local(tag: str) -> str:
    """Strip an XML namespace, returning the bare local name."""
    return tag.rsplit("}", 1)[-1]


def _find_local(elem: ET.Element, name: str) -> ET.Element | None:
    for child in elem:
        if _local(child.tag) == name:
            return child
    return None


def _iter_local(elem: ET.Element, name: str):
    for child in elem:
        if _local(child.tag) == name:
            yield child


def _text(elem: ET.Element | None) -> str:
    return (elem.text or "").strip() if elem is not None else ""


# ── manifest ────────────────────────────────────────────────────────────────

def _parse_manifest_resources(manifest_root: ET.Element) -> dict[str, dict[str, str]]:
    """Return {resource_id: {"type": ..., "href": ...}}, skipping Canvas markers.

    Real Canvas exports differ from the CAS self-export in two ways handled here:
      * quizzes / discussions / some assignments have NO ``href`` attribute on the
        ``<resource>`` — the path lives on a ``<file href>`` child;
      * assignments reuse the ``learning-application-resource`` type (same as the
        ``canvas_export.txt`` / ``module_meta`` marker), so we can't skip on type
        alone — we skip only resources whose content lives under ``course_settings/``.
    """
    resources: dict[str, dict[str, str]] = {}
    res_container = _find_local(manifest_root, "resources")
    if res_container is None:
        return resources
    for res in _iter_local(res_container, "resource"):
        rid = res.get("identifier", "")
        if not rid:
            continue
        rtype = res.get("type", "")
        href = res.get("href", "")
        if not href:
            file_el = _find_local(res, "file")
            if file_el is not None:
                href = file_el.get("href", "")
        # Skip only the Canvas metadata marker (its files live in course_settings/).
        if not href or href.replace("\\", "/").startswith("course_settings/"):
            continue
        resources[rid] = {"type": rtype, "href": href}
    return resources


def _manifest_course_title(manifest_root: ET.Element, fallback: str) -> str:
    """Dig the course title out of metadata/lom/general/title.

    CAS self-export nests the title in ``<langstring>``; real Canvas (lomimscc)
    uses ``<string>``. Accept either, plus the title element's own text.
    """
    metadata = _find_local(manifest_root, "metadata")
    if metadata is not None:
        for lom in _iter_local(metadata, "lom"):
            general = _find_local(lom, "general")
            if general is None:
                continue
            title = _find_local(general, "title")
            if title is None:
                continue
            for child_name in ("langstring", "string"):
                node = _find_local(title, child_name)
                if _text(node):
                    return _text(node)
            if _text(title):
                return _text(title)
    return fallback


def _course_settings_title(root: str) -> str:
    """Fallback: read <title> from course_settings/course_settings.xml (real Canvas)."""
    path = os.path.join(root, "course_settings", "course_settings.xml")
    if not os.path.isfile(path):
        return ""
    try:
        node = ET.parse(path).getroot()
    except ET.ParseError:
        return ""
    return _text(_find_local(node, "title"))


def _kind_for(content_type: str, resource_type: str) -> str:
    """Resolve item kind from Canvas content_type, falling back to resource type."""
    if content_type in _CONTENT_TYPE_KIND:
        return _CONTENT_TYPE_KIND[content_type]
    if _ASSESSMENT_TYPE_HINT in (resource_type or ""):
        return QUIZ_KIND
    return PAGE_KIND


def _make_item(kind: str, title: str, href: str, provenance_id: str):
    if kind in (QUIZ_KIND, ASSIGNMENT_KIND, DISCUSSION_KIND):
        return IAssessment(title=title, href=href, provenance_id=provenance_id, kind=kind)
    return IPage(title=title, href=href, provenance_id=provenance_id, kind=PAGE_KIND)


# ── module_meta (authoritative order) ─────────────────────────────────────────

def _parse_from_module_meta(
    meta_root: ET.Element,
    resources: dict[str, dict[str, str]],
    warnings: list[str],
) -> list[IModule]:
    modules: list[IModule] = []
    module_elems = sorted(
        _iter_local(meta_root, "module"),
        key=lambda m: _int(_text(_find_local(m, "position")), default=0),
    )
    for mod in module_elems:
        title = _text(_find_local(mod, "title")) or "Untitled Module"
        position = _int(_text(_find_local(mod, "position")), default=len(modules) + 1)
        prov = mod.get("identifier", "") or f"module_{position}"
        module = IModule(title=title, position=position, provenance_id=prov)

        items_container = _find_local(mod, "items")
        item_elems = list(_iter_local(items_container, "item")) if items_container is not None else []
        item_elems.sort(key=lambda it: _int(_text(_find_local(it, "position")), default=0))

        for it in item_elems:
            content_type = _text(_find_local(it, "content_type"))
            item_title = _text(_find_local(it, "title")) or "Untitled"
            ref = _text(_find_local(it, "identifierref"))
            prov_id = it.get("identifier", "") or ref

            if content_type in _UNSUPPORTED_CONTENT_TYPES:
                warnings.append(
                    f"Skipped unsupported item '{item_title}' ({content_type}) — "
                    f"flagged for manual review."
                )
                continue

            resource = resources.get(ref)
            if resource is None:
                warnings.append(
                    f"Item '{item_title}' references a missing resource ({ref!r}); skipped."
                )
                continue

            kind = _kind_for(content_type, resource.get("type", ""))
            module.items.append(_make_item(kind, item_title, resource.get("href", ""), prov_id))

        modules.append(module)
    return modules


# ── manifest organizations (fallback when module_meta is absent) ──────────────

def _parse_from_organizations(
    manifest_root: ET.Element,
    resources: dict[str, dict[str, str]],
    warnings: list[str],
) -> list[IModule]:
    orgs = _find_local(manifest_root, "organizations")
    organization = _find_local(orgs, "organization") if orgs is not None else None
    if organization is None:
        return []

    # organization > item(root) > item(module) > item(leaf, identifierref)
    root_item = _find_local(organization, "item")
    module_items = list(_iter_local(root_item, "item")) if root_item is not None else []

    modules: list[IModule] = []
    for pos, mod in enumerate(module_items, start=1):
        title = _text(_find_local(mod, "title")) or "Untitled Module"
        module = IModule(title=title, position=pos, provenance_id=mod.get("identifier", f"module_{pos}"))
        for leaf in _iter_local(mod, "item"):
            ref = leaf.get("identifierref", "")
            item_title = _text(_find_local(leaf, "title")) or "Untitled"
            resource = resources.get(ref)
            if resource is None:
                warnings.append(
                    f"Item '{item_title}' references a missing resource ({ref!r}); skipped."
                )
                continue
            kind = _kind_for("", resource.get("type", ""))
            module.items.append(
                _make_item(kind, item_title, resource.get("href", ""), leaf.get("identifier", "") or ref)
            )
        modules.append(module)
    return modules


# ── resources on disk (web_resources/ + other staged assets) ──────────────────

def _collect_resources(root: str) -> list[IResource]:
    resources: list[IResource] = []
    for dirpath, _dirs, files in os.walk(root):
        # Only count genuine asset dirs, not the structural XML/HTML we parse.
        if os.path.basename(dirpath) != "web_resources":
            continue
        for name in files:
            resources.append(
                IResource(filename=name, path=os.path.join(dirpath, name))
            )
    return resources


def _int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ── orphan (unassigned) wiki pages ────────────────────────────────────────────
# Canvas Pages can exist without being placed in any Module. module_meta only
# lists items that ARE in modules, so those pages would be dropped. We recover
# the ones that carry real content (skipping exact duplicates of module pages)
# into a trailing synthetic module so no authored content is silently lost.
# CAS self-exports put every page in a module → no orphans → this is a no-op.

_ORPHAN_MODULE_TITLE = "Additional Pages (not in a module)"
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_BODY_RE = re.compile(r"<body[^>]*>(.*?)</body>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_POS_SUFFIX_RE = re.compile(r"-\d+$")


def _slug_to_title(href: str) -> str:
    name = os.path.splitext(os.path.basename(href))[0]
    name = _POS_SUFFIX_RE.sub("", name).replace("-", " ").replace("_", " ").strip()
    return name.title() if name else "Untitled Page"


def _page_title_and_hash(root: str, href: str) -> tuple[str, str | None]:
    """Return (html <title>, normalized-body hash) for a wiki page, or ('', None)."""
    path = _resolve_href(root, href)
    if not os.path.isfile(path):
        return "", None
    try:
        html = _read_text(path)
    except OSError:
        return "", None
    title_m = _TITLE_RE.search(html)
    title = unescape(title_m.group(1)).strip() if title_m else ""
    body_m = _BODY_RE.search(html)
    text = body_m.group(1) if body_m else html
    norm = " ".join(_TAG_RE.sub(" ", text).split())
    return title, hashlib.md5(norm.encode("utf-8")).hexdigest()


def _append_orphan_pages(root: str, resources: dict, modules: list[IModule], warnings: list[str]) -> None:
    """Append content-bearing wiki pages that no module references (deduped)."""
    used_hrefs = {
        it.href.replace("\\", "/")
        for m in modules for it in m.items if getattr(it, "href", "")
    }
    candidates = [
        (rid, href)
        for rid, meta in resources.items()
        for href in [(meta.get("href") or "").replace("\\", "/")]
        if meta.get("type") == "webcontent"
        and href.startswith("wiki_content/")
        and href not in used_hrefs
    ]
    if not candidates:
        return

    # Dedup against pages already in modules, and among orphans, by body text.
    seen: set[str] = set()
    for m in modules:
        for it in m.items:
            if isinstance(it, IPage) and it.href:
                _t, h = _page_title_and_hash(root, it.href)
                if h:
                    seen.add(h)

    orphans: list[tuple[str, str, str]] = []
    for rid, href in sorted(candidates, key=lambda x: x[1]):
        title, h = _page_title_and_hash(root, href)
        if h and h in seen:
            continue  # exact duplicate of a page already imported — skip
        if h:
            seen.add(h)
        orphans.append((rid, href, title or _slug_to_title(href)))
    if not orphans:
        return

    module = IModule(title=_ORPHAN_MODULE_TITLE, position=len(modules) + 1, provenance_id="__unassigned__")
    for rid, href, title in orphans:
        module.items.append(IPage(title=title, href=href, provenance_id=rid, kind=PAGE_KIND))
    modules.append(module)
    warnings.append(
        f"{len(orphans)} page(s) were not attached to any module in the source; "
        f"imported under '{_ORPHAN_MODULE_TITLE}'."
    )


# ── public entry point ────────────────────────────────────────────────────────

def parse_structure(extract: ExtractResult) -> ICourse:
    """Build an :class:`ICourse` (structure only) from an extracted package.

    Prefers ``module_meta.xml`` for order/typing (authoritative in Canvas
    cartridges); falls back to the manifest organization tree when it is absent.
    Per-item problems become warnings — this never raises for a single bad item.
    """
    warnings: list[str] = []

    manifest_tree = ET.parse(extract.manifest_path)
    manifest_root = manifest_tree.getroot()
    resources = _parse_manifest_resources(manifest_root)

    title = (
        _manifest_course_title(manifest_root, "")
        or _course_settings_title(extract.root)
        or os.path.basename(extract.root)
        or "Imported Course"
    )

    module_meta_path = os.path.join(extract.root, _MODULE_META_REL)
    if os.path.isfile(module_meta_path):
        meta_root = ET.parse(module_meta_path).getroot()
        modules = _parse_from_module_meta(meta_root, resources, warnings)
    else:
        warnings.append(
            "module_meta.xml not found — reconstructing order from the manifest TOC."
        )
        modules = _parse_from_organizations(manifest_root, resources, warnings)

    # Recover authored pages that aren't attached to any module (Canvas allows this).
    _append_orphan_pages(extract.root, resources, modules, warnings)

    course = ICourse(
        title=title,
        modules=modules,
        resources=_collect_resources(extract.root),
        warnings=warnings,
    )
    return course


# ── Session 2: content bodies (pages → markdown, quizzes → questions) ─────────

def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
        return handle.read()


def _resolve_href(root: str, href: str) -> str:
    """Resolve an href (possibly ``../`` / forward-slashed) under the extract root."""
    return os.path.normpath(os.path.join(root, *href.replace("\\", "/").split("/")))


def _load_page(root: str, page: IPage, warnings: list[str]) -> None:
    path = _resolve_href(root, page.href)
    if not page.href or not os.path.isfile(path):
        warnings.append(f"Page file missing for '{page.title}' ({page.href!r}); body left empty.")
        return
    html = _read_text(path)
    _title, markdown, raw_body_html = page_html_to_markdown(html)
    page.html = html
    page.markdown = markdown
    page.raw_body_html = raw_body_html


def _load_assessment(root: str, assessment: IAssessment, warnings: list[str]) -> None:
    if assessment.kind != QUIZ_KIND:
        # Assignments / discussions are authored as wiki pages, not QTI XML.
        _load_prose_item(root, assessment, warnings)
        return

    questions = _parse_quiz_questions(root, assessment.href, assessment.title, warnings)
    assessment.questions = questions
    assessment.body_markdown = questions_to_markdown(assessment.title, questions)
    if not questions:
        warnings.append(f"Quiz '{assessment.title}' contained no parseable questions.")


def _parse_quiz_questions(root: str, href: str, title: str, warnings: list[str]) -> list:
    """Parse quiz questions, trying the resource QTI then the Canvas non-CC bank.

    CAS self-export: ``assessment/block_N.xml`` (inline items).
    Real Canvas: the resource points at ``g<id>/assessment_qti.xml`` which is often
    an empty shell when questions come from a bank — the real questions live in
    ``non_cc_assessments/<resource_id>.xml.qti`` (an ``<objectbank>``). Fall back
    to that when the primary yields nothing.
    """
    primary = _resolve_href(root, href) if href else ""
    questions = _try_parse_qti(primary, title, warnings)
    if questions:
        return questions
    return _try_parse_qti(_non_cc_qti_path(root, href), title, warnings)


def _try_parse_qti(path: str, title: str, warnings: list[str]) -> list:
    if not path or not os.path.isfile(path):
        return []
    try:
        return parse_qti_xml(_read_text(path))
    except ET.ParseError as exc:
        warnings.append(f"Quiz '{title}' has malformed QTI XML ({exc}); skipped.")
        return []


def _non_cc_qti_path(root: str, href: str) -> str:
    """Derive the Canvas ``non_cc_assessments/<resource_id>.xml.qti`` path from a href."""
    if not href:
        return ""
    parts = href.replace("\\", "/").split("/")
    res_id = parts[0] if len(parts) > 1 else os.path.splitext(parts[-1])[0]
    if not res_id:
        return ""
    return os.path.join(root, "non_cc_assessments", f"{res_id}.xml.qti")


def _load_prose_item(root: str, assessment: IAssessment, warnings: list[str]) -> None:
    """Assignment/discussion prose is stored like a wiki page — load as markdown."""
    path = _resolve_href(root, assessment.href)
    if not assessment.href or not os.path.isfile(path):
        warnings.append(f"Content file missing for '{assessment.title}' ({assessment.href!r}); body left empty.")
        return
    _title, markdown, _raw = page_html_to_markdown(_read_text(path))
    assessment.body_markdown = markdown


def _load_bodies(extract: ExtractResult, course: ICourse) -> None:
    """Fill page markdown + quiz questions. One bad item degrades to a warning."""
    for _module, item in course.iter_items():
        try:
            if isinstance(item, IPage):
                _load_page(extract.root, item, course.warnings)
            elif isinstance(item, IAssessment):
                _load_assessment(extract.root, item, course.warnings)
        except Exception as exc:  # never hard-fail the whole import on one item
            course.warnings.append(f"Failed to load '{getattr(item, 'title', '?')}': {exc}")


def parse_course(extract: ExtractResult) -> ICourse:
    """Full parse: structure (S1) + page/quiz bodies (S2) → complete :class:`ICourse`.

    ``parse_structure`` remains the lightweight structure-only pass used by
    ``/imports/validate``; this adds content and is what the reconstruction job
    (Session 3) consumes.
    """
    course = parse_structure(extract)
    _load_bodies(extract, course)
    return course
