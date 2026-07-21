"""Normalised internal course model for the IMSCC importer.

The parser (canvas_parser) produces an :class:`ICourse`; the editor_builder
(Session 3) consumes it to write CourseModule/Generation/Block rows. Keeping a
format-agnostic model in the middle is what makes the parser pluggable for
future formats (SCORM/Moodle) without touching reconstruction. See reverse_cas.md.

Provenance ids are the stable Canvas ``identifier`` values pulled from the
manifest / module_meta, so the round-trip map (import_provenance) can be built
later without re-deriving anything.

Session 1 fills the *structure* (modules + ordered item stubs + counts). Bodies
(page markdown, quiz questions) are filled in Session 2 — the ``html`` /
``markdown`` / ``questions`` fields start empty by design.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Canvas module_meta content_type → our normalised item kind.
PAGE_KIND = "page"
QUIZ_KIND = "quiz"
ASSIGNMENT_KIND = "assignment"
DISCUSSION_KIND = "discussion"


@dataclass
class IResource:
    """A staged, non-page/non-assessment asset (embedded file or web resource)."""
    filename: str
    path: str = ""              # path within the extracted workspace (empty until staged)
    provenance_id: str = ""


@dataclass
class IPage:
    """A Canvas wiki page → becomes a lesson Block."""
    title: str
    href: str
    provenance_id: str
    kind: str = PAGE_KIND
    html: str = ""             # raw file HTML (filled S2)
    raw_body_html: str = ""    # unwrapped <body> inner HTML, for Block.content_html (filled S2)
    markdown: str = ""         # converted body (filled S2)


@dataclass
class IAssessment:
    """A Canvas quiz/assignment/discussion → becomes a quiz/assignment Block."""
    title: str
    href: str
    provenance_id: str
    kind: str = QUIZ_KIND      # quiz | assignment | discussion
    questions: list = field(default_factory=list)   # normalised questions (filled S2)
    body_markdown: str = ""    # for assignment/discussion prose (filled S2)


@dataclass
class IModule:
    """An ordered Canvas module → becomes a CourseModule."""
    title: str
    position: int
    provenance_id: str
    items: list = field(default_factory=list)   # list[IPage | IAssessment], in order


@dataclass
class ICourse:
    """The whole reconstructed course, format-agnostic."""
    title: str
    modules: list[IModule] = field(default_factory=list)
    resources: list[IResource] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def iter_items(self):
        """Yield every (module, item) pair in reading order."""
        for module in self.modules:
            for item in module.items:
                yield module, item

    def structure_counts(self) -> dict[str, int]:
        """Pre-flight counts surfaced by /imports/validate and the wizard."""
        counts = {
            "modules": len(self.modules),
            "pages": 0,
            "quizzes": 0,
            "assignments": 0,
            "discussions": 0,
            "resources": len(self.resources),
        }
        kind_to_key = {
            PAGE_KIND: "pages",
            QUIZ_KIND: "quizzes",
            ASSIGNMENT_KIND: "assignments",
            DISCUSSION_KIND: "discussions",
        }
        for _module, item in self.iter_items():
            key = kind_to_key.get(item.kind)
            if key:
                counts[key] += 1
        return counts
