"""Canvas HTML Lesson Generator.

Converts a block's markdown content into a production-quality, responsive,
Canvas-LMS-compatible standalone HTML lesson using the LLM. The rendition is
generated when a block is published and stored on ``Block.content_html`` so the
IMSCC exporter can package the LMS-ready HTML instead of converting markdown at
export time.

The public entry point ``generate_canvas_html`` never raises — it returns the
HTML string on success or ``None`` on any failure, so publishing a block is
never blocked by an LLM error.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Optional

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from promptops_app.services.usage_service import UsageLogContext


CANVAS_HTML_SYSTEM_PROMPT = """You are an expert Frontend Engineer, UX Designer, Instructional Designer, and LMS Content Developer.

Your task is to convert the provided Markdown lesson into a production-quality, responsive HTML lesson suitable for Canvas LMS and other modern Learning Management Systems.

PRIMARY GOAL
Generate HTML that is:
- Professional
- Modern
- Minimal
- Responsive
- Accessible (WCAG AA)
- Canvas LMS compatible
- Mobile friendly
- Easy to maintain
- Component-based

DESIGN PRINCIPLES
- Card-based layout
- White background with soft gray surfaces
- Blue accent theme (#2563EB)
- Rounded corners and subtle shadows
- Modern typography (Inter, Segoe UI fallback)
- Responsive design

LESSON STRUCTURE
1. Lesson Header
2. Learning Objectives
3. Introduction
4. Topic Cards
5. Visual Placeholder
6. Interactive Placeholder
7. Knowledge Check
8. Summary
9. Footer

VISUAL PLACEHOLDERS
Replace all visual/media notes with learner-friendly placeholders:
- Visual Placeholder
- Video Placeholder
- Animation Placeholder

INTERACTIVE PLACEHOLDERS
Replace technical interaction specifications with reusable activity cards:
- Multiple Choice
- Scenario
- Drag and Drop
- Reflection

ACCESSIBILITY
- Semantic HTML5
- Proper heading hierarchy
- Keyboard accessible
- WCAG AA compliant
- Responsive

CODE STANDARDS
- HTML5
- Internal CSS
- Minimal vanilla JavaScript
- No frameworks
- Canvas-compatible

CONTENT RULES
Preserve instructional content.
Remove AI metadata, source references, blueprint references, CDD references, and prompt artifacts.
Convert instructional notes into learner-friendly placeholders.

OUTPUT
Generate a premium, production-ready standalone HTML lesson suitable for packaging into a Canvas IMSCC course.
Return ONLY the raw HTML document beginning with <!DOCTYPE html>. Do not wrap it in Markdown code fences and do not add any commentary before or after the HTML."""


_FENCE_RE = re.compile(r"^\s*```(?:html)?\s*\n(.*?)\n?```\s*$", re.DOTALL | re.IGNORECASE)


def _strip_code_fence(text: str) -> str:
    """Remove a surrounding ```html ... ``` fence if the model added one."""
    match = _FENCE_RE.match(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _looks_like_html(text: str) -> bool:
    lowered = text.lower()
    return "<html" in lowered or "<!doctype html" in lowered or "<body" in lowered


def _build_user_prompt(label: str, block_type: str, content: str) -> str:
    header_bits = []
    if label:
        header_bits.append(f"Lesson title: {label}")
    if block_type:
        header_bits.append(f"Content type: {block_type}")
    header = "\n".join(header_bits)
    return (
        f"{header}\n\n"
        "Convert the following Markdown lesson into a single standalone HTML document "
        "following all rules in the system prompt.\n\n"
        "--- BEGIN MARKDOWN LESSON ---\n"
        f"{content}\n"
        "--- END MARKDOWN LESSON ---"
    )


def generate_canvas_html(
    label: str,
    content: str,
    block_type: str = "",
    model_choice: Optional[str] = None,
    usage_ctx: Optional["UsageLogContext"] = None,
) -> Optional[str]:
    """Generate a Canvas-ready standalone HTML lesson from markdown content.

    Returns the HTML string on success, or ``None`` on empty input or any LLM
    failure. Never raises.
    """
    text = (content or "").strip()
    if not text:
        return None

    try:
        from promptops_app.core.models import DEFAULT_MODEL_NAME
        from promptops_app.services.llm_service import generate_with_metadata

        chosen_model = model_choice or DEFAULT_MODEL_NAME
        user_prompt = _build_user_prompt(label or "", block_type or "", text)

        result = generate_with_metadata(
            chosen_model,
            CANVAS_HTML_SYSTEM_PROMPT,
            user_prompt,
            usage_ctx=usage_ctx,
        )

        if result.is_error or not (result.text or "").strip():
            _log.warning(
                "canvas_html_generation_failed label=%r type=%r error=%s",
                label, block_type, result.error_type,
            )
            return None

        html = _strip_code_fence(result.text)
        if not _looks_like_html(html):
            _log.warning(
                "canvas_html_generation_non_html label=%r type=%r", label, block_type,
            )
            return None
        return html
    except Exception:  # pragma: no cover - defensive; publish must not break
        _log.exception("canvas_html_generation_exception label=%r", label)
        return None
