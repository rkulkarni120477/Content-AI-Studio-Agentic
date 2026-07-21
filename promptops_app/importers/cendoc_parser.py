"""Parse an extracted CendocXML package into the shared :class:`ICourse` model.

Mapping (v1):
  * ``cl:chapter`` → ``IModule``
  * titled ``cl:sect1`` (skip opener ``number=nonumber``) → ``IPage`` lesson
  * nested sect2/3/4 → markdown headings inside the parent lesson
  * ``cl:quiz`` short-answer → ``IAssessment`` quiz
  * front-matter skipped (warning); parts ignored as modules
  * appendices / back-matter prose → trailing ``Appendices`` module

Does **not** touch Canvas IMSCC parsers. Image embedding is Cendoc-only via
``cendoc_to_markdown.ImageResolver``.
"""

from __future__ import annotations

import os
from xml.etree import ElementTree as ET

from promptops_app.importers.cendoc_to_markdown import (
    ImageResolver,
    local,
    q,
    quiz_to_markdown,
    section_to_html,
    section_to_markdown,
    text_of,
    title_of,
)
from promptops_app.importers.internal_model import (
    IAssessment,
    ICourse,
    IModule,
    IPage,
    IResource,
    PAGE_KIND,
    QUIZ_KIND,
)
from promptops_app.importers.package_extractor import ExtractResult


def _parse_xml(path: str) -> ET.Element:
    # Register default prefix so findall with Clark names works either way.
    ET.register_namespace("cl", "http://xml.cengage-learning.com/cendoc-core")
    tree = ET.parse(path)
    return tree.getroot()


def _doc_title(root: ET.Element) -> str:
    meta = root.find(q("doc-meta"))
    if meta is not None:
        title_el = meta.find(q("title"))
        if title_el is not None and text_of(title_el):
            return text_of(title_el)
    # Fallback: any top-level title.
    title_el = root.find(f".//{q('title')}")
    return text_of(title_el) or "Imported Cendoc Course"


def _identifier(el: ET.Element) -> str:
    return el.get("identifier") or ""


def _is_opener_sect(el: ET.Element) -> bool:
    """Chapter cover / learning-outcomes opener — not a standalone lesson."""
    if local(el.tag) == "opener":
        return True
    if local(el.tag) == "sect1" and (el.get("number") or "").lower() == "nonumber":
        return True
    return False


def _learning_outcomes_md(chapter: ET.Element) -> str:
    """Pull Learning Outcomes sidebar from chapter opener if present."""
    opener = chapter.find(q("opener"))
    if opener is None:
        return ""
    for sidebar in opener.findall(f".//{q('sidebar')}"):
        label = ""
        meta = sidebar.find(q("complex-meta"))
        if meta is not None:
            label = text_of(meta.find(q("label")))
        if "learning outcome" in label.lower():
            return section_to_markdown(sidebar, ImageResolver(None), heading_level=0, include_own_title=False)
    return ""


def _sect1_lessons(
    chapter: ET.Element,
    images: ImageResolver,
    *,
    load_bodies: bool,
) -> list[IPage]:
    pages: list[IPage] = []
    for sect in chapter.findall(q("sect1")):
        if _is_opener_sect(sect):
            continue
        title = title_of(sect) or "Untitled section"
        page = IPage(
            title=title,
            href="",
            provenance_id=_identifier(sect),
            kind=PAGE_KIND,
        )
        if load_bodies:
            # Nested sect2+ rendered inside this lesson; do not duplicate sect1 title
            # as H1 — editor already uses page.title.
            md = section_to_markdown(sect, images, heading_level=0, include_own_title=False)
            html = section_to_html(sect, images, heading_level=0, include_own_title=False)
            # Promote nested sect2 titles to ## etc. already done by section_to_*.
            page.markdown = md
            page.raw_body_html = html
            page.html = html
        pages.append(page)
    return pages


def _chapter_module(
    chapter: ET.Element,
    position: int,
    images: ImageResolver,
    *,
    load_bodies: bool,
    warnings: list[str],
) -> IModule:
    title = title_of(chapter) or f"Chapter {position}"
    label_el = chapter.find(f"./{q('complex-meta')}/{q('label')}")
    label = text_of(label_el)
    if label and not title.lower().startswith("chapter"):
        # Keep chapter title as-is; label is informational.
        pass

    module = IModule(
        title=title,
        position=position,
        provenance_id=_identifier(chapter),
        items=[],
    )

    outcomes = _learning_outcomes_md(chapter) if load_bodies else ""
    pages = _sect1_lessons(chapter, images, load_bodies=load_bodies)

    if outcomes and pages and load_bodies:
        pages[0].markdown = (
            f"> **Learning Outcomes**\n>\n> {outcomes.replace(chr(10), chr(10) + '> ')}\n\n"
            + (pages[0].markdown or "")
        ).strip()
        pages[0].raw_body_html = (
            f'<aside class="cendoc-sidebar"><strong>Learning Outcomes</strong>'
            f"<p>{outcomes}</p></aside>\n" + (pages[0].raw_body_html or "")
        )
        pages[0].html = pages[0].raw_body_html

    if not pages:
        # Chapter with only opener — still create one lesson from opener body.
        opener = chapter.find(q("opener"))
        page = IPage(
            title=title,
            href="",
            provenance_id=_identifier(opener or chapter),
            kind=PAGE_KIND,
        )
        if load_bodies and opener is not None:
            page.markdown = section_to_markdown(opener, images, heading_level=0, include_own_title=False)
            page.raw_body_html = section_to_html(opener, images, heading_level=0, include_own_title=False)
            page.html = page.raw_body_html
        elif not load_bodies:
            pass
        else:
            warnings.append(f"Chapter '{title}' has no titled sections; created empty lesson.")
        pages = [page]

    module.items = pages
    return module


def _appendix_modules(
    root: ET.Element,
    images: ImageResolver,
    *,
    start_position: int,
    load_bodies: bool,
) -> tuple[list[IModule], list[IAssessment]]:
    """Build appendices module + any quiz assessments from back-matter."""
    back = root.find(q("back-matter"))
    if back is None:
        return [], []

    modules: list[IModule] = []
    quizzes: list[IAssessment] = []
    items: list = []

    # Appendices and simple-sections under back-matter as lessons.
    for appendix in back.findall(q("appendix")):
        for sect in list(appendix):
            name = local(sect.tag)
            if name in ("simple-section", "sect1"):
                title = title_of(sect) or "Appendix section"
                page = IPage(
                    title=title,
                    href="",
                    provenance_id=_identifier(sect),
                    kind=PAGE_KIND,
                )
                if load_bodies:
                    page.markdown = section_to_markdown(sect, images, heading_level=0, include_own_title=False)
                    page.raw_body_html = section_to_html(sect, images, heading_level=0, include_own_title=False)
                    page.html = page.raw_body_html
                items.append(page)
            elif name == "quiz":
                assessment = IAssessment(
                    title=title_of(sect) or "Chapter Exercises",
                    href="",
                    provenance_id=_identifier(sect),
                    kind=QUIZ_KIND,
                )
                if load_bodies:
                    assessment.body_markdown = quiz_to_markdown(sect)
                quizzes.append(assessment)

    # Quizzes nested deeper (e.g. under simple-section).
    for quiz_el in back.findall(f".//{q('quiz')}"):
        qid = _identifier(quiz_el)
        if any(a.provenance_id == qid for a in quizzes):
            continue
        assessment = IAssessment(
            title="Chapter Exercises",
            href="",
            provenance_id=qid,
            kind=QUIZ_KIND,
        )
        if load_bodies:
            assessment.body_markdown = quiz_to_markdown(quiz_el)
        quizzes.append(assessment)

    if items or quizzes:
        module = IModule(
            title="Appendices",
            position=start_position,
            provenance_id=_identifier(back) or "cendoc-appendices",
            items=list(items) + list(quizzes),
        )
        modules.append(module)

    return modules, quizzes


def _build_course(extract: ExtractResult, *, load_bodies: bool) -> ICourse:
    if not extract.xml_path or not os.path.isfile(extract.xml_path):
        raise ValueError("Cendoc extract is missing xml_path.")

    root = _parse_xml(extract.xml_path)
    warnings: list[str] = []

    if root.find(q("front-matter")) is not None:
        warnings.append("Front matter was skipped during Cendoc import.")

    images = ImageResolver(extract.images_dir)
    if load_bodies and extract.images_dir is None:
        warnings.append("No book_images/ folder found — figures will be caption-only.")

    modules: list[IModule] = []
    position = 1
    body = root.find(q("body-matter"))
    chapters: list[ET.Element] = []
    if body is not None:
        # Chapters may be nested under parts.
        chapters = body.findall(f".//{q('chapter')}")
    else:
        chapters = root.findall(f".//{q('chapter')}")

    for chapter in chapters:
        modules.append(
            _chapter_module(
                chapter,
                position,
                images,
                load_bodies=load_bodies,
                warnings=warnings,
            )
        )
        position += 1

    appendix_modules, _ = _appendix_modules(
        root, images, start_position=position, load_bodies=load_bodies
    )
    modules.extend(appendix_modules)

    warnings.extend(images.warnings)

    resources = [
        IResource(filename=name, path=path, provenance_id=name)
        for name, path in images.used
    ]

    return ICourse(
        title=_doc_title(root),
        modules=modules,
        resources=resources,
        warnings=warnings,
    )


def parse_structure(extract: ExtractResult) -> ICourse:
    """Structural parse only — module/page stubs, no bodies."""
    return _build_course(extract, load_bodies=False)


def parse_course(extract: ExtractResult) -> ICourse:
    """Full parse — markdown/HTML bodies + quizzes + image embedding."""
    return _build_course(extract, load_bodies=True)
