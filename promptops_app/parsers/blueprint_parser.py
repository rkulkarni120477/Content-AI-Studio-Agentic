"""Blueprint parser — section parsing, item-level regeneration, and prompt building.

Contains NO Streamlit calls and NO direct database queries (db is passed in
where needed). Safe for FastAPI or CLI use.

Extracted from core/shared.py (Phase 3 refactoring).
"""

import logging
import re
import re as _re_engine  # alias used by the item-parsing engine
from typing import Optional

from promptops_app.prompt_templates import (
    BLUEPRINT_SYSTEM_PROMPT,
    BLUEPRINT_USER_PROMPT_TEMPLATE,
    BLUEPRINT_SECTION_REGENERATE_PROMPT,
    TEACHER_BLUEPRINT_SYSTEM_PROMPT,
    TEACHER_BLUEPRINT_USER_PROMPT_TEMPLATE,
    TEACHER_BLUEPRINT_SECTION_REGENERATE_PROMPT,
)
from promptops_app.core.llm_client import safe_json_loads
from promptops_app.parsers.markdown_emphasis import repair_emphasis
from promptops_app.services.llm_service import generate_text as call_llm
from promptops_app.services.llm_service import generate_with_metadata
from promptops_app.services.usage_service import UsageLogContext

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Blueprint section visibility
# ---------------------------------------------------------------------------

_BP_UI_HIDDEN_SECTION_KEYS = {
    "step 1 module identification",
    "step 5 blueprint validation",
    "blueprint validation",
    "narrative and modularity",
    "narrative",
    "modularity",
    "blueprint complete",
    "blueprint ready",
    "blueprint is complete",
    "blueprint is ready",
}


def _is_bp_section_hidden(title: str) -> bool:
    """Return True if a Blueprint section key should be hidden from the UI."""
    tl = title.strip().lower()
    for key in _BP_UI_HIDDEN_SECTION_KEYS:
        if key in tl:
            return True
    if re.match(r"^step\s+[15]\b", tl):
        return True
    if re.search(r"blueprint\s+(complete|ready|validated|done)", tl):
        return True
    if re.search(r"blueprint\s+output", tl):
        return True
    return False


# ---------------------------------------------------------------------------
# Prompt selection
# ---------------------------------------------------------------------------

def get_blueprint_prompts(mode: str) -> tuple:
    """Return (system_prompt, user_template, section_regen_prompt) for mode."""
    if mode == "teacher":
        return (
            TEACHER_BLUEPRINT_SYSTEM_PROMPT,
            TEACHER_BLUEPRINT_USER_PROMPT_TEMPLATE,
            TEACHER_BLUEPRINT_SECTION_REGENERATE_PROMPT,
        )
    return (
        BLUEPRINT_SYSTEM_PROMPT,
        BLUEPRINT_USER_PROMPT_TEMPLATE,
        BLUEPRINT_SECTION_REGENERATE_PROMPT,
    )


# ---------------------------------------------------------------------------
# Granular item-level parsing & patching
# ---------------------------------------------------------------------------

_NUM_RE  = _re_engine.compile(r"^(\s*)(\d+)([.):])\s+(.+)$")
_BULL_RE = _re_engine.compile(r"^(\s*)([-*•])\s+(.+)$")
_HEAD_RE = _re_engine.compile(r"^(#{1,4})\s+(.+)$")


def parse_items_from_section(text: str) -> list:
    """Parse section text into individually editable items.

    Returns a list of dicts:
      {index, type, prefix, text, raw_line, line_index, indent}

    Priority order:
      1. Numbered lists  e.g. 1. … / 1) … / 1: …
      2. Bulleted lists  e.g. - … / * … / • …
      3. ### Sub-headings
      4. Paragraph fallback (blank-line delimited)
    """
    items: list = []
    lines = text.split("\n")

    for li, line in enumerate(lines):
        nm = _NUM_RE.match(line)
        bm = _BULL_RE.match(line)
        hm = _HEAD_RE.match(line)

        if nm:
            items.append({
                "index":      len(items),
                "type":       "numbered",
                "prefix":     f"{nm.group(2)}{nm.group(3)}",
                "number":     int(nm.group(2)),
                "text":       nm.group(4).strip(),
                "raw_line":   line,
                "line_index": li,
                "indent":     nm.group(1),
            })
        elif bm:
            items.append({
                "index":      len(items),
                "type":       "bullet",
                "prefix":     bm.group(2),
                "text":       bm.group(3).strip(),
                "raw_line":   line,
                "line_index": li,
                "indent":     bm.group(1),
            })
        elif hm:
            items.append({
                "index":      len(items),
                "type":       "heading",
                "prefix":     hm.group(1),
                "text":       hm.group(2).strip(),
                "raw_line":   line,
                "line_index": li,
                "indent":     "",
            })

    if not items:
        paras = [p.strip() for p in _re_engine.split(r"\n{2,}", text) if p.strip()]
        items = [
            {
                "index": i, "type": "paragraph", "prefix": "", "text": p,
                "raw_line": p, "line_index": i, "indent": "",
            }
            for i, p in enumerate(paras)
        ]

    return items


def patch_item_in_section(original_text: str, item_index: int, new_item_text: str) -> str:
    """Replace ONLY the item at item_index with new_item_text.

    All other lines are preserved byte-for-byte.
    Returns the reconstructed section text.
    """
    items = parse_items_from_section(original_text)
    if not items or item_index < 0 or item_index >= len(items):
        return original_text

    target = items[item_index]
    new_item_text = new_item_text.strip()

    if target["type"] == "paragraph":
        paras = [p.strip() for p in _re_engine.split(r"\n{2,}", original_text) if p.strip()]
        if item_index < len(paras):
            paras[item_index] = new_item_text
        return "\n\n".join(paras)

    lines = original_text.split("\n")
    li = target["line_index"]
    if li >= len(lines):
        return original_text

    indent  = target.get("indent", "")
    t_type  = target["type"]
    prefix  = target["prefix"]

    if t_type == "numbered":
        new_line = f"{indent}{prefix} {new_item_text}"
    elif t_type == "bullet":
        new_line = f"{indent}{prefix} {new_item_text}"
    elif t_type == "heading":
        new_line = f"{prefix} {new_item_text}"
    else:
        new_line = new_item_text

    lines[li] = new_line
    return "\n".join(lines)


_ITEM_REGEN_SYSTEM = (
    "You are a senior instructional designer. "
    "Regenerate ONLY the single item specified. "
    "Return ONLY the replacement text — no numbering, no bullet, no prefix. "
    "One sentence or phrase per item. Do NOT include any other items."
)


#: Strips a list bullet the model re-added, WITHOUT eating markdown emphasis.
#:
#: The previous pattern was ``^[-*•]\\s*``, and because ``\\s*`` matches zero
#: characters it read the first ``*`` of ``**Bold:** value`` as a bullet and
#: removed it. patch_item_in_section then prepended the real bullet, so a
#: single-item regeneration turned
#:     - **Supplemental References:** Essential Texts: …
#: into
#:     - *Supplemental References:** Essential Texts: …
#: deterministically, on every run. Worksheet 1 of an AIM CDD is nothing but
#: ``- **Field:** value`` lines, so every per-item regeneration there corrupted
#: the field label, and the same applied to italics (``*emphasis*`` → ``emphasis*``).
#: Observed live on CDD 169 item 12.
#:
#: ``-`` and ``•`` keep the old zero-width behaviour, since neither carries any
#: meaning in markdown beyond "bullet". ``*`` requires real whitespace after it,
#: which is what distinguishes a bullet from an emphasis delimiter.
_LEADING_BULLET_RE = _re_engine.compile(r"^(?:[-•]\s*|\*\s+)")


def _strip_leading_bullet(text: str) -> str:
    return _LEADING_BULLET_RE.sub("", text, count=1)


def _item_regen_user_prompt(
    section_title: str, section_content: str, item_index: int, item_text: str,
    custom_instruction: str, *, learning_signals: str = "", context: str = "",
) -> str:
    """The item-regeneration user prompt. Shared by both variants below so the
    text-only and with-result paths cannot drift apart."""
    # When the "item" IS the whole section — which is what a markdown table
    # parses to, since parse_items_from_section recognises lists and headings
    # but not table rows — appending it again as "Section context" doubles the
    # prompt for no added information. The check is cheap and the saving is
    # ~19k tokens on a CDD day table.
    section_context = "" if section_content.strip() == item_text.strip() else section_content
    return (
        f"Section: {section_title}\n\n"
        f"Current item (index {item_index}): {item_text}\n\n"
        f"Instruction: {custom_instruction or 'Improve this item.'}"
        + (f"\n\n{context}" if context else "")
        + (f"\n\nLEARNED PREFERENCES:\n{learning_signals}" if learning_signals else "")
        + (f"\n\nSection context:\n{section_context}" if section_context else "")
    )


def _postprocess_item(raw: str, section_title: str, item_index: int) -> str:
    """Strip a leading bullet and repair unbalanced emphasis. Shared by both variants.

    A second line of defence, on a different failure than the strip. The model can
    return a label whose delimiters do not pair up, and a malformed line committed
    here is sticky: every later save preserves untouched lines byte for byte, so it
    survives until somebody edits that exact line by hand. Repair the one
    unambiguous shape, and say so when the damage is a guess rather than a fix.
    """
    result = _strip_leading_bullet((raw or "").strip())
    result, emphasis = repair_emphasis(result.strip())
    if emphasis:
        _log.warning(
            "regen_single_item: unbalanced markdown emphasis in the model reply "
            "for section=%r item=%s — %s",
            section_title, item_index, "; ".join(f.describe() for f in emphasis),
        )
    return result.strip()


def regen_single_item(
    section_title: str,
    section_content: str,
    item_index: int,
    item_text: str,
    custom_instruction: str,
    model_choice: str = "GPT-5.4",
    learning_signals: str = "",
    usage_ctx: Optional["UsageLogContext"] = None,
    context: str = "",
) -> str:
    """Regenerate a single item — text only. See regen_single_item_with_result.

    Kept as-is for the callers that only want the string (cdd.py, regen_jobs.py,
    core/shared.py). A caller that splices the reply back over stored content
    should use ``regen_single_item_with_result`` instead: a bare string cannot say
    whether the reply hit the output ceiling, and a truncated item reads as a
    finished one right up to the point it is committed.

    No ``max_tokens`` parameter, deliberately. This variant goes through
    generate_text, which has no way to pass one, so accepting the argument would
    have meant silently ignoring it — a caller asking for a 64000-token ceiling
    would have got 16384 with no error and no warning. A parameter that cannot be
    honoured is worse than one that does not exist; ask for the ceiling on the
    variant that can request it.
    """
    user_p = _item_regen_user_prompt(
        section_title, section_content, item_index, item_text, custom_instruction,
        learning_signals=learning_signals, context=context,
    )
    # Still generate_text, deliberately. cdd.py, regen_jobs.py and core/shared.py
    # all reach this wrapper, and none of them has been analysed for the
    # refuse-on-truncation behaviour the sibling adds. Keeping this call exactly
    # as it was means their behaviour is unchanged and their tests keep stubbing
    # the name they always stubbed; the prompt itself is shared, so the two
    # cannot drift apart.
    return _postprocess_item(call_llm(model_choice, _ITEM_REGEN_SYSTEM, user_p, usage_ctx),
                             section_title, item_index)


def regen_single_item_with_result(
    section_title: str,
    section_content: str,
    item_index: int,
    item_text: str,
    custom_instruction: str,
    model_choice: str = "GPT-5.4",
    learning_signals: str = "",
    usage_ctx: Optional["UsageLogContext"] = None,
    context: str = "",
    max_tokens: Optional[int] = None,
) -> tuple:
    """Regenerate a single item inside a section using the LLM.

    Returns ``(text, LLMResult)``. The result is what lets a caller see whether
    the reply hit the model's output ceiling — ``generate_text``, which this used
    to call, collapses that to a bare string, so an item cut off mid-sentence was
    patched into the document and committed as though it were complete.

    ``max_tokens`` requests the model's real ceiling. Omitting it inherited
    DEFAULT_MAX_OUTPUT_TOKENS (16384) while the blueprint router's pre-flight
    check sized the item against ``output_budget`` (64000 on three of the five
    catalog models) — so an item between those two figures passed the guard and
    then truncated anyway, which is the exact outcome the guard exists to
    prevent.

    Pass usage_ctx (project/course/user) so this call's cost is attributed in
    llm_usage_logs — this is a real, live LLM-calling path (cdd.py/blueprints.py/
    blocks.py all route their per-item regenerate endpoint through here), not a
    dead one; without it the call still logs, just as unattributed.

    ``context`` (optional) is grounding assembled by the caller — for CDDs, the
    block overview and the day/ACS rows the instruction refers to (see
    app.services.cdd_regen_context). "" reproduces this function's exact
    previous behaviour, which is what the blueprint and block callers still get.
    """
    user_p = _item_regen_user_prompt(
        section_title, section_content, item_index, item_text, custom_instruction,
        learning_signals=learning_signals, context=context,
    )
    llm_result = generate_with_metadata(
        model_choice, _ITEM_REGEN_SYSTEM, user_p, usage_ctx, max_tokens=max_tokens,
    )
    # Same shape generate_text produced, so a caller checking
    # startswith("ERROR") keeps working unchanged.
    raw = f"ERROR: {llm_result.text}" if llm_result.is_error else llm_result.text
    return _postprocess_item(raw, section_title, item_index), llm_result


# ---------------------------------------------------------------------------
# Blueprint component extraction
# ---------------------------------------------------------------------------

# Full ordered list of generatable component types
_COMPONENT_TYPES_LIST = [
    ("Module Assessment",    "module_assessment",    "assessment"),
    ("Explorer Spotlight",   "explorer_spotlight",   "component"),
    ("Career Connection",    "career_connection",    "component"),
    ("Reflection",           "reflection",           "component"),
    ("Journal Prompt",       "journal_prompt",       "component"),
    ("Knowledge Check",      "knowledge_check",      "assessment"),
    ("Discussion Prompt",    "discussion_prompt",    "component"),
    ("Case Study",           "case_study",           "component"),
    ("Lab Activity",         "lab_activity",         "component"),
    ("Practice Exercise",    "practice_exercise",    "component"),
    ("Summative Assessment", "summative_assessment", "assessment"),
    ("Formative Assessment", "formative_assessment", "assessment"),
    ("Teacher Resources",    "teacher_resources",    "component"),
    ("Worksheet",            "worksheet",            "component"),
]


def is_component_type_module_level(comp_value: str) -> bool:
    """Return True for module-level components (Module Assessment requires lessons first)."""
    return comp_value in {"module_assessment", "assessments", "assessment_plan"}


def is_component_type_course_level(comp_value: str) -> bool:
    """Return True for course-level components (cross-module, end-of-course)."""
    return comp_value in {"project_work", "summative_assessments", "learning_activities"}


# ---------------------------------------------------------------------------
# DLU (Daily Learning Unit) blueprints
#
# Day-based ("Block") curricula produce a per-day DLU blueprint instead of a
# module→lessons blueprint. Its content is organised as five DLU sections
# (Today's Mission, Learn It, Quick Check, Up Next in Class, Day Reflection)
# under a "DLU OUTLINE" header — it has no "Lesson N" or "Module Assessment"
# headings, so the standard parser finds nothing. Detection is additive and
# content-based: a standard blueprint never matches, so it keeps the exact
# module/lesson behaviour below.
# ---------------------------------------------------------------------------

_DLU_SECTION_MARKERS = (
    "today's mission",
    "learn it",
    "quick check",
    "up next in class",
    "day reflection",
)


def is_dlu_blueprint(bp_version) -> bool:
    """Return True when a blueprint is a day-based DLU outline.

    Signal (either):
      - an explicit ``DLU OUTLINE`` header, OR
      - at least two of the five canonical DLU section markers.
    A standard module/lesson blueprint has none of these.
    """
    if not bp_version:
        return False
    text = (getattr(bp_version, "full_content", "") or "").lower()
    if not text:
        return False
    if "dlu outline" in text:
        return True
    hits = sum(1 for marker in _DLU_SECTION_MARKERS if marker in text)
    return hits >= 2


def _parse_dlu_day(bp_version) -> tuple:
    """Best-effort (day_number, topic) from a DLU blueprint's content.

    Returns (day_number:str|"", topic:str|""). Never raises.
    """
    text = getattr(bp_version, "full_content", "") or ""
    day = ""
    topic = ""
    m = re.search(r"\bday\s*(?:number)?\s*[:\-]?\s*(\d+)", text, re.IGNORECASE)
    if m:
        day = m.group(1)
    tm = re.search(r"(?im)^\s*\*{0,2}topic\*{0,2}\s*[:\-]\s*(.+)$", text)
    if tm:
        topic = tm.group(1).strip().strip("*").strip()
        topic = re.split(r"\s{2,}|\|", topic)[0].strip()[:80]
    return day, topic


def parse_day_and_title(blueprint_title: str, module_number=None) -> tuple:
    """Derive ``(day_number:int|None, topic:str)`` from a DLU blueprint's title.

    DLU day blueprints created via the Blueprint "Select Day" dropdown are
    titled ``"Day N: <topic> Blueprint"``. Older DLU blueprints predate that
    dropdown and are titled ``"Module N Blueprint"``; for those we fall back to
    ``module_number`` for the day and leave the topic blank. Pure string
    parsing — no DB access — so it stays in this module.
    """
    title = (blueprint_title or "").strip()
    m = re.match(r"(?i)^\s*day\s+(\d+)\s*[:\-–—.]?\s*(.*)$", title)
    if m:
        day = int(m.group(1))
        topic = m.group(2).strip()
        topic = re.sub(r"(?i)\s*blueprint\s*$", "", topic).strip()
        topic = topic.strip(":-–— ").strip()
        return day, topic
    # No "Day N" prefix (older DLU blueprint) — fall back to the module number.
    try:
        day = int(module_number) if module_number is not None else None
    except (TypeError, ValueError):
        day = None
    return day, ""


def parse_blueprint_components(bp_version) -> list:
    """Parse a BlueprintVersion ORM object into generatable dropdown components.

    Standard (module) blueprints return ONLY:
      1. Lessons — all lessons in blueprint order (e.g. 'Lesson 1.1 What Is a Robot?')
      2. Module Assessment — the single module-level assessment for this blueprint

    DLU (day-based) blueprints instead return a single "Full DLU" component for
    the day, so the Generate page's Content Type dropdown is populated. The user
    then generates each DLU section by picking the matching section prompt.

    Everything else is excluded to keep the Content Type dropdown scoped.
    """
    if not bp_version:
        return []

    # DLU blueprints have no Lesson/Module-Assessment headings — expose the day
    # itself as one generatable unit. Standard blueprints skip this entirely.
    if is_dlu_blueprint(bp_version):
        day, topic = _parse_dlu_day(bp_version)
        if day and topic:
            label = f"Full DLU — Day {day}: {topic}"
        elif day:
            label = f"Full DLU — Day {day}"
        else:
            label = "Full DLU (this day)"
        return [{
            "label":    label,
            "value":    "dlu_day",
            "type":     "lesson",
            "metadata": {"day_number": day, "topic": topic, "structure": "dlu"},
        }]

    _MODULE_ASSESSMENT_KEYS = {
        "module assessment", "module-level assessment",
        "knowledge check", "module check",
    }

    try:
        sections   = safe_json_loads(bp_version.sections) if bp_version.sections else {}
        full_text  = bp_version.full_content or ""
        components: list = []
        seen_vals: set   = set()

        search_text = full_text or " ".join(sections.values())

        _lesson_heading_pat = re.compile(
            r"(?:^|\n)"
            r"\s*(?:#{1,4}\s*|\*{1,2})?"
            r"[Ll]esson\s+"
            r"(\d+(?:\.\d+)?)"
            r"[\s:\-—.]*"
            r"([^\n#*]{2,80})",
            re.MULTILINE,
        )

        _LESSON_SKIP_WORDS = (
            "objective", "duration", "strategy", "interaction",
            "bloom", "skill focus", "alignment", "design intent",
            "lesson type", "activity type",
        )

        seen_lesson_nums: set = set()
        for m in _lesson_heading_pat.finditer(search_text):
            num_str   = m.group(1).strip()
            raw_title = m.group(2).strip().strip("*:—").strip()
            if len(raw_title) < 2:
                continue
            lower_t = raw_title.lower()
            if any(skip in lower_t for skip in _LESSON_SKIP_WORDS):
                continue
            if num_str in seen_lesson_nums:
                continue
            seen_lesson_nums.add(num_str)
            label = f"Lesson {num_str}: {raw_title}"
            val   = f"lesson_{num_str.replace('.', '_')}"
            if val not in seen_vals:
                seen_vals.add(val)
                components.append({
                    "label":    label,
                    "value":    val,
                    "type":     "lesson",
                    "metadata": {
                        "lesson_number": num_str,
                        "title":         raw_title,
                    },
                })

        # Step 2: Extract Module Assessment
        for sec_title, sec_content in sections.items():
            tl = sec_title.strip().lower()
            if any(k in tl for k in _MODULE_ASSESSMENT_KEYS):
                val = "module_assessment"
                if val not in seen_vals:
                    seen_vals.add(val)
                    components.append({
                        "label":    "Module Assessment",
                        "value":    val,
                        "type":     "assessment",
                        "metadata": {"section_title": sec_title},
                    })
                break

        # Fallback: check full text for 'Module Assessment' heading
        if "module_assessment" not in seen_vals:
            if re.search(r"(?im)^#+\s*module\s+assessment", search_text):
                components.append({
                    "label":    "Module Assessment",
                    "value":    "module_assessment",
                    "type":     "assessment",
                    "metadata": {},
                })

        return components

    except Exception:
        return []


# ---------------------------------------------------------------------------
# Component generation prompt builder
# ---------------------------------------------------------------------------

def build_component_generation_prompt(
    component: dict,
    bp_version,
    cdd_version,
    target_audience: str = "",
    expert_domain: str = "",
) -> tuple:
    """Build (system_prompt, user_prompt) for a specific Blueprint component.

    For 'lesson' type, callers should use the LESSON_WITH_CONTEXT flow instead.
    This function handles all other component types.

    Returns:
        (system_prompt: str, user_prompt: str)
    """
    comp_type  = component.get("type", "component")
    comp_label = component.get("label", "Content")
    comp_value = component.get("value", "")
    comp_meta  = component.get("metadata", {})

    bp_secs  = safe_json_loads(bp_version.sections)  if bp_version  and bp_version.sections  else {}
    cdd_secs = safe_json_loads(cdd_version.sections) if cdd_version and cdd_version.sections else {}

    from promptops_app.core.content_utils import find_labeled_snippet

    bp_full  = bp_version.full_content  if bp_version  else ""
    cdd_full = cdd_version.full_content if cdd_version else ""

    bp_los   = find_labeled_snippet(bp_secs,  ["Learning Objectives", "Learning Objective"], bp_full)
    bp_plan  = find_labeled_snippet(bp_secs,  ["Lesson Plan", "Lesson-by-Lesson", "Lesson Blueprint"], bp_full)
    bp_keys  = find_labeled_snippet(bp_secs,  ["Key Concepts"], bp_full)
    cdd_los  = find_labeled_snippet(cdd_secs, ["Learning Objectives", "Learning Objective"], cdd_full)
    cdd_tone = find_labeled_snippet(cdd_secs, ["Tone & Style", "Tone"], cdd_full)
    cdd_qual = find_labeled_snippet(cdd_secs, ["Quality Standards"], cdd_full)

    ctx_block = (
        f"BLUEPRINT CONTEXT (Module: "
        f"{getattr(bp_version, 'blueprint_id', 'N/A') if bp_version else 'N/A'}):\n"
        f"Module LOs: {bp_los or 'Not specified in the Blueprint.'}\n"
        f"Key Concepts: {bp_keys or 'Not specified in the Blueprint.'}\n"
        f"Lesson Plan excerpt: {bp_plan or 'Not specified in the Blueprint.'}\n\n"
        f"CDD CONTEXT:\n"
        f"Course LOs: {cdd_los or 'Not specified in the CDD.'}\n"
        f"Tone & Style: {cdd_tone or 'Professional, engaging, accessible'}\n"
        f"Quality Standards: {cdd_qual or 'High quality, structured content'}\n\n"
        f"TARGET AUDIENCE: {target_audience or 'As defined in CDD'}\n"
        f"EXPERT DOMAIN: {expert_domain or 'General'}"
    )

    # ── Component-type routing ─────────────────────────────────────────────
    if comp_type == "assessment" or "assessment" in comp_label.lower() or "quiz" in comp_label.lower():
        system_p = (
            "You are an expert Instructional Designer creating a lesson or module quiz. "
            "Map objective coverage before writing a single question — questions written "
            "before the alignment map is complete will drift.\n"
            "Use '## ' for all main section headings."
        )
        user_p = f"""Create a {comp_label} for this module.

{ctx_block}

Work through each step in order before writing any questions.

## Step 1  Objective-to-Question Map
- List each learning objective and next to it state: the Bloom's level and the question format that matches it
  - Remember / Understand → factual MCQ or matching
  - Apply → scenario-based MCQ or process sequencing
  - Analyze → case-based multi-select or decision scenario
- Each objective needs at least 1 question; high-stakes objectives (Strategies 5 and 8) need 2–3
- Flag if any objective is untestable in auto-gradable format — redesign the objective or replace the question with a reflection prompt

## Step 2  Question Writing
For each question produce:
- Stem: clear, concise, scenario-based where possible — avoid opening with 'Which of the following…'
- 4 answer options for MCQ: all plausible, no joke distractors, no 'all / none of the above'
- Correct answer with explanation — state why this option is correct, not just that it is
- Distractor rationale for each wrong option: name the common misconception being tested
- Strategy 8 (Cert Prep) only: tag each question with its certification domain (e.g., FAA Part 107: Airspace Classification)

## Step 3  Quiz-Level Checks
- Coverage: is every listed objective tested?
- Balance: no more than 30% of questions at Remember / Understand level for HS and above
- Representation check: are scenario characters and contexts inclusive and representative of diverse learners?

★ If this course is Career Exploration (Strategy 7): replace formal quiz with a self-assessment. Formal right/wrong grading is not appropriate for purely exploratory content."""

    elif "reflection" in comp_label.lower():
        system_p = (
            "You are an expert Instructional Designer creating a lesson reflection or journal prompt. "
            "Determine the reflection type for the strategy before writing any questions.\n"
            "Use '## ' for all main section headings."
        )
        user_p = f"""Create a {comp_label} component for this module.

{ctx_block}

Work through each step in order before writing any questions.

## Step 1  Purpose Calibration by Strategy
The purpose of reflection changes fundamentally by strategy — do not write prompts before completing this step:
- Strategy 7 (Career Exploration): 'What did you notice about yourself in this lesson?' — curiosity and identity focus; no wrong answers; keep language warm and accessible
- Strategy 2 (Role/Context): 'How would you handle this situation differently now?' — professional judgment and growth
- Strategy 1 (Investigation): 'What evidence changed your thinking?' — metacognitive and analytical
- Strategy 3 (Decision/Sim): 'Looking back at your decision, what would you weigh differently?' — consequence analysis
- Strategy 4 (Skill+Practice): 'Which part of the process felt uncertain? What would help you improve?' — skill self-assessment
- Strategy 6 (Project/Build): 'What would you change in your design and why?' — iterative design thinking
- Strategy 5 / 8: 'What part of the rule or procedure surprised you? How will you remember it?' — compliance internalization

## Step 2  Prompt Construction
- Write exactly 3 guiding questions — not one large open prompt
- Question 1: ground the learner in what happened — factual and low-stakes ('What did you notice… / What stood out…')
- Question 2: ask for a connection or interpretation ('How does this connect to… / What does this make you think about…')
- Question 3: forward-looking ('What do you want to find out next? / How might you use this…')

## Step 3  Format and Stakes Calibration
- Expected writing: 3–5 sentences per question — not a paragraph essay
- Explicitly mark as low-stakes in the student-facing intro: 'There are no right or wrong answers here'"""

    elif any(kw in comp_label.lower() for kw in ("explorer", "spotlight", "career", "connection")):
        system_p = (
            "You are a curriculum developer creating engaging, real-world connection components for eLearning.\n"
            "Use '## ' for all main section headings."
        )
        user_p = f"""Create a {comp_label} component for this module.

{ctx_block}

## Overview
What this component is and why it matters for learners in {expert_domain or 'this domain'}.

## Content
Engaging scenario, profile, or spotlight relevant to {expert_domain or 'the field'}.
Use diverse personas and real-world settings.

## Discussion or Quick Activity
One brief prompt or activity tied directly to the content above.

## Career / Real-World Connection
Explicit link to career paths, job roles, or professional practice in {expert_domain or 'this field'}."""

    elif "journal" in comp_label.lower():
        system_p = (
            "You are an expert Instructional Designer creating a lesson reflection or journal prompt. "
            "Determine the reflection type for the strategy before writing any questions.\n"
            "Use '## ' for all main section headings."
        )
        user_p = f"""Create a {comp_label} for this module.

{ctx_block}

Work through each step in order before writing any prompts.

## Step 1  Purpose Calibration by Strategy
- Strategy 7: curiosity and identity focus; no wrong answers
- Strategy 2: professional judgment and growth
- Strategy 1: metacognitive and analytical
- Strategy 3: consequence analysis
- Strategy 4: skill self-assessment
- Strategy 6: iterative design thinking
- Strategy 5 / 8: compliance internalization

## Step 2  Prompt Construction
- Write exactly 3 guiding questions — not one large open prompt
- Q1: ground the learner in what happened
- Q2: ask for a connection or interpretation
- Q3: forward-looking

## Step 3  Format and Stakes Calibration
- Expected writing: 3–5 sentences per question
- Explicitly mark as low-stakes: 'There are no right or wrong answers here'"""

    elif "knowledge check" in comp_label.lower():
        system_p = (
            "You are an expert Instructional Designer writing embedded knowledge check interactions. "
            "Complete all three steps in order — do not write the question before the concept distillation is done.\n"
            "Use '## ' for all main section headings."
        )
        user_p = f"""Create a {comp_label} (formative check) for this module.

{ctx_block}

Work through each step in order before writing any questions.

## Step 1  Concept Distillation
- State the single most important thing the learner should be able to do after this plank
- Check: is this testable in an auto-gradable format?

## Step 2  Check Design by Strategy
- Strategy 4: sequencing / process ordering / correct-step identification
- Strategy 5: compliance scenario
- Strategy 1: evidence evaluation
- Strategy 2: role-based judgment
- Strategy 3: decision point
- Strategy 8: exam-style MCQ

For each knowledge check produce:
- Stem (1–2 sentences) + 2–4 answer options
- Correct answer + 1-sentence feedback
- Bridge forward to the next plank

## Step 3  Placement and Pacing Rules
- One check per concept; place immediately after introduction
- Target 3–4 checks per 8–12 plank lesson"""

    elif comp_value in ("assessments", "assessment_plan") or comp_label.lower() in ("assessments", "assessment plan"):
        system_p = (
            "You are an expert Instructional Designer creating a lesson or module quiz. "
            "Map objective coverage before writing a single question.\n"
            "Use '## ' for all main section headings."
        )
        user_p = f"""Create the full Assessments package for this module.

{ctx_block}

## Step 1  Objective-to-Question Map
Map each learning objective to Bloom's level and question format.

## Step 2  Question Writing
For each question: stem, 4 options, correct answer with explanation, distractor rationale.

## Step 3  Quiz-Level Checks
Coverage, balance (≤30% Remember/Understand for HS+), representation check."""

    elif comp_value == "teacher_resources" or "teacher resource" in comp_label.lower():
        _scope = "course" if "course" in comp_label.lower() else "module"
        system_p = (
            "You are a senior instructional designer creating instructor support materials.\n"
            "Use '## ' for all main section headings."
        )
        if _scope == "course":
            user_p = f"""Create Course-Level Teacher Resources.

{ctx_block}

## Course Facilitator Guide
Complete facilitation roadmap: weekly schedule, pacing guide, milestone checkpoints.

## Instructor Onboarding Checklist
Everything a new instructor must know/do before teaching this course.

## Assessment Answer Keys & Rubrics Master
Consolidated answer keys for all module and course-level assessments.

## Student Progress Monitoring Framework
How to track learner engagement, flag at-risk students, interpret data.

## Course-Level FAQ
Top 15–20 questions learners ask, with suggested responses.

## Continuous Improvement Log
Template for instructors to document issues and suggested course revisions."""
        else:
            user_p = f"""Create Module-Level Teacher Resources.

{ctx_block}

## Instructor Overview
Module purpose, key teaching moments, prerequisite knowledge required.

## Facilitation Guide
Step-by-step delivery notes for each lesson segment.

## Differentiation Strategies
Extensions for advanced learners. Scaffolds for struggling learners.

## Common Misconceptions & Interventions
Top 3–5 misconceptions with specific instructional interventions.

## Discussion Questions
5–8 high-quality discussion questions with key talking points.

## Answer Keys
Complete answer keys for all module assessments."""

    elif comp_value == "worksheet" or "worksheet" in comp_label.lower():
        system_p = (
            "You are an expert Instructional Designer creating a lesson worksheet. "
            "Determine the correct worksheet type for the strategy before designing any tasks.\n"
            "Use '## ' for all main section headings."
        )
        user_p = f"""Create a learner Worksheet for this module.

{ctx_block}

## Step 1  Alignment and Type Selection
- State the learning objective — every task must trace back to it
- Confirm Bloom's level: Apply or above, not Remember
- Select correct worksheet type for the strategy

## Step 2  Task Sections
- Section A — Guided practice (scaffolded)
- Section B — Independent practice (same concept, no scaffold)
- Section C — Application task (new realistic context)
- Total expected time: 10–15 minutes

## Step 3  Answer Key
Complete answers including acceptable variations and rubric criteria."""

    elif comp_value == "project_work" or "project" in comp_label.lower():
        system_p = (
            "You are a curriculum designer creating course-level capstone project materials.\n"
            "Use '## ' for all main section headings."
        )
        user_p = f"""Create a Course-Level Project Work assignment.

{ctx_block}

## Project Overview
Purpose, scope, and alignment to course learning objectives.

## Project Brief
Clear description of deliverables with format specifications.

## Step-by-Step Project Guide
Phase 1: Research & Planning — Phase 2: Development — Phase 3: Review — Phase 4: Submission

## Assessment Rubric
Criteria, performance levels (Exemplary / Proficient / Developing / Beginning), point allocations.

## Bloom's Alignment Table
| Deliverable | Course LO | Bloom's Level |

## Submission Requirements
Format, file types, naming conventions, submission instructions."""

    elif comp_value == "summative_assessments" or "summative" in comp_label.lower():
        system_p = (
            "You are an assessment designer creating a course-level summative assessment.\n"
            "Use '## ' for all main section headings."
        )
        user_p = f"""Create a Course-Level Summative Assessment.

{ctx_block}

## Assessment Overview
Purpose, scope, total marks, time allocation, and passing criteria.

## Section A — Knowledge Check (Multiple Choice)
15–20 questions covering key knowledge across all modules. Include answer key with rationale.

## Section B — Applied Understanding (Short Answer)
5–8 questions at Apply/Analyse/Explain. Include model answers and marking guidance.

## Section C — Capstone Scenario (Extended Response)
1–2 complex scenario-based questions. Include detailed scoring rubric.

## LO Coverage Matrix
| Course LO | Assessed By (Section + Question #) | Bloom's Level |

## Adaptive Retake Guidelines
Conditions for retake, remediation pathways, maximum attempts."""

    elif comp_value == "learning_activities" or "learning activit" in comp_label.lower():
        system_p = (
            "You are a senior instructional designer creating module learning activities.\n"
            "Use '## ' for all main section headings."
        )
        user_p = f"""Create a detailed Learning Activities plan for this module.

{ctx_block}

## Activity Overview
Purpose of each activity type and how they support the module LOs.

## Problem-Based Scenario
Real-world scenario relevant to {expert_domain or 'the subject domain'}.

## Case Study
Detailed case with background, challenge, discussion questions, debrief guide.

## Group Discussion
2–3 structured questions with facilitation tips and time allocation.

## Interactive / Role-Play Activity
Setup instructions, roles, success criteria, debrief questions.

## Facilitator Notes
Sequencing guidance, timing per activity, differentiation suggestions."""

    else:
        system_p = (
            f"You are a senior instructional designer creating {comp_label} for an eLearning course.\n"
            "Use '## ' for all main section headings."
        )
        user_p = f"""Create a high-quality {comp_label} for this module.

{ctx_block}

Use clear '## ' headings to structure the content.
Ensure alignment with the Blueprint LOs and CDD quality standards.
Include practical examples appropriate for {target_audience or 'the target audience'}."""

    return system_p, user_p
