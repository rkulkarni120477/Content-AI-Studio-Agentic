# =============================================================================
# PromptOps shared utilities extracted from the legacy Streamlit monolith
# =============================================================================
# This single-file application provides a complete eLearning content generation
# platform powered by OpenAI. It includes:
#   - User authentication with role-based access (admin/author/reviewer)
#   - A centralized Prompt Registry with version control
#   - RAG-enabled course generation from uploaded documents (PDF/DOCX/TXT)
#   - Block-level content editing with live preview
#   - Formal review system with quality scoring
#   - Multi-format export (Markdown, JSON, HTML, DOCX)
#   - Full observability via system event logging
#   - Prompt A/B testing and analytics dashboards
# =============================================================================

# --- Standard Library Imports ---
import os
from dotenv import load_dotenv
load_dotenv()
import sys
from pathlib import Path

# Project root is two levels above this package: <repo>/promptops_app/core/shared.py
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
import subprocess
import json
import difflib
from datetime import datetime, timezone
from typing import Any, List, Optional
from io import BytesIO
import base64
import json_repair
from concurrent.futures import ThreadPoolExecutor
# --- Third-Party Imports ---
import streamlit as st               # Web UI framework
import jwt                            # JSON Web Token for auth
import requests                       # HTTP client for API calls
import pandas as pd                   # Excel/CSV data handling
import boto3                          # AWS Bedrock Client
from botocore.config import Config    # AWS Boto Client Settings
from docx import Document as DocxDocument  # Word document export
from pypdf import PdfReader                # PDF text extraction

# --- Prompt & Template Import ---
from promptops_app.prompt_templates import (
    DEFAULT_STYLE_GUIDE,
    PROMPT_TEMPLATES,
    EVAL_PROMPT,
    SCORING_PROMPT,
    META_PROMPT,
    REVIEW_PROMPT,
    PERSONA_PREFIX_TEMPLATE,
    PLAGIARISM_PROMPT,
    LOGIN_DEFAULT_PROMPT_SYSTEM,
    LOGIN_DEFAULT_PROMPT_USER,
    REGISTRY_FALLBACK_SYSTEM,
    REGISTRY_FALLBACK_USER,
    IMPROVISE_DEFAULT_REQUEST,
    IMPROVISE_BLOCK_PROMPT_TEMPLATE,
    # CDD & Blueprint
    CDD_SYSTEM_PROMPT,
    CDD_USER_PROMPT_TEMPLATE,
    CDD_SECTION_REGENERATE_PROMPT,
    BLUEPRINT_SYSTEM_PROMPT,
    BLUEPRINT_USER_PROMPT_TEMPLATE,
    BLUEPRINT_SECTION_REGENERATE_PROMPT,
    # Teacher Blueprint
    TEACHER_BLUEPRINT_SYSTEM_PROMPT,
    TEACHER_BLUEPRINT_USER_PROMPT_TEMPLATE,
    TEACHER_BLUEPRINT_SECTION_REGENERATE_PROMPT,
    CONTEXT_INJECTION_TEMPLATE,
    LESSON_WITH_CONTEXT_SYSTEM,
    LESSON_WITH_CONTEXT_USER,
)

# --- Database Layer Import ---
from promptops_app.database import (
    Settings, settings,
    engine, SessionLocal, Base,
    User, Document, Style, StyleDocument, Prompt, PromptVersion,
    Generation, Block, BlockComment, BlockVersion, WorkflowEvent,
    ABTestRun, Review, SystemLog, FeedbackSignal,
    CourseDesignDocument, CDDVersion, ModuleBlueprint, BlueprintVersion,
    Project, Course, ProjectUserAssignment, CourseUserAssignment,
    init_db, init_db_with_seed, seed_data,
    hash_password, verify_password,
    log_event,
    _is_lead_for_project, _is_lead_for_course, can_modify_style,
    get_active_cdd_version, get_active_blueprint_version,
    create_style, add_files_to_style, get_styles,
    get_active_style, set_active_style, deactivate_style,
    _build_unified_style_docs, build_style_context,
    resolve_document_references,
    search_blocks, clone_block,
    TRANSITIONS, apply_transition_local,
    _get_user_projects,
)

# UI helpers moved to promptops_app/ui/components.py (Phase 1 refactoring)
from promptops_app.ui.components import (
    status_badge, fill_template, inject_premium_style, _req, _logo_b64,
    _section_badge, _info_card, _load_css,
)

# Constants — core/constants.py (Phase 3 refactoring)
from promptops_app.core.constants import WorkflowState, UserRole, DocumentStatus, FeedbackScope, ChangeSource
# NOTE: Parser re-exports are placed AFTER local definitions further in this file
# so they correctly override the legacy code. See end of file for:
# parsers/cdd_parser.py re-exports and parsers/blueprint_parser.py re-exports.

# Evaluation functions moved to services/evaluation_service.py (Phase 3 refactoring)
from promptops_app.services.evaluation_service import (
    evaluate_text, compare_outputs, lightweight_evaluate_text,
    get_initial_quality_metadata, score_content_quality,
    generate_prompt_template_with_llm, llm_evaluate_block,
    get_plagi_model, check_plagiarism_content, evaluate_readability,
)

# LLM client implementations moved to promptops_app/core/llm_client.py (Phase 1 refactoring)
from promptops_app.core.llm_client import (
    _get_openai_session, call_openai, _get_bedrock_client, call_bedrock,
    call_llm, safe_json_loads,
)

# =============================================================================
# Content Block Splitter
# =============================================================================
import re

def split_into_blocks(output_text: str):
    """Split raw LLM output into individual content blocks based on ## headings and extract source citations."""
    lines = output_text.splitlines()
    blocks = []
    label = "Body"
    order_val = 1
    current = []
    
    def extract_sources(text):
        # Find all [Source: filename.ext] patterns
        matches = re.findall(r"\[Source:\s*(.*?)\]", text)
        return list(set([m.strip() for m in matches if m.strip()]))

    for line in lines:
        if line.startswith("## "):
            if current:
                blk_text = "\n".join(current).strip()
                blocks.append((label, order_val, blk_text, extract_sources(blk_text)))
                order_val = order_val + 1
            label = line.replace("## ", "", 1).strip()
            current = [line]
        else:
            current.append(line)
            
    if current:
        blk_text = "\n".join(current).strip()
        blocks.append((label, order_val, blk_text, extract_sources(blk_text)))
        
    return blocks or [("Body", 1, output_text, extract_sources(output_text))]

def generate_for_version(db, prompt_name: str, version_name: str, topic: str, inputs: dict):
    prompt = db.query(Prompt).filter(Prompt.name == prompt_name).first()
    version = db.query(PromptVersion).filter(PromptVersion.prompt_id == prompt.id, PromptVersion.version == version_name).first()
    rendered = fill_template(version.user_prompt_template, {"topic": topic, "block_type": "lesson", **inputs})
    return call_openai(version.system_prompt, rendered)

# File parsing moved to parsers/file_parser.py (Phase 3 refactoring)
from promptops_app.parsers.file_parser import _parse_uploaded_file, parse_uploaded_file


# Config constants moved to core/config.py (Phase 3 refactoring)
from promptops_app.core.config import (
    _env_bool, _clip_text, clip_text,
    PROMPTOPS_MAX_SOURCE_CHARS, PROMPTOPS_MAX_CONTEXT_CHARS,
    PROMPTOPS_EDITOR_PAGE_SIZE, PROMPTOPS_WORKFLOW_PAGE_SIZE,
    PROMPTOPS_SYNC_QUALITY_CHECKS, PROMPTOPS_SYNC_PLAGIARISM_CHECKS,
    PROMPTOPS_API_TIMEOUT_SECONDS,
)


def make_source_context(filename: str, content: str, max_chars: int = None) -> str:
    """Create a bounded source block for prompt injection."""
    max_chars = max_chars or PROMPTOPS_MAX_SOURCE_CHARS
    safe_content = _clip_text(content or "", max_chars)
    return f"\n\n[START SOURCE: {filename}]\n{safe_content}\n[END SOURCE: {filename}]"


def trim_generation_context(context: str) -> str:
    """Apply a final global cap to supplementary generation context."""
    return _clip_text(context or "", PROMPTOPS_MAX_CONTEXT_CHARS)


# lightweight_evaluate_text, get_initial_quality_metadata → services/evaluation_service.py


@st.cache_data(ttl=60, show_spinner=False)
def get_cached_prompt_names() -> list[str]:
    """Cache prompt dropdown data to avoid repeated DB scans on each Streamlit rerun."""
    with SessionLocal() as _db:
        return [p.name for p in _db.query(Prompt).order_by(Prompt.name.asc()).all()]


@st.cache_data(ttl=60, show_spinner=False)
def get_cached_active_document_names() -> list[str]:
    """Cache active document filenames for source-material dropdowns."""
    with SessionLocal() as _db:
        return [d.filename for d in _db.query(Document.filename).filter(Document.status == "active").order_by(Document.filename.asc()).all()]

# Export functions moved to services/export_service.py (Phase 3 refactoring)
from promptops_app.services.export_service import export_html, export_docx

# Notification helpers moved to ui/notifications.py (Phase 3 refactoring)
from promptops_app.ui.notifications import notify, notify_deferred, notify_check


# RBAC logic moved to promptops_app/auth/permissions.py (Phase 1 refactoring)
from promptops_app.auth.permissions import (
    ROLE_DISPLAY, ROLE_DISPLAY_OPTIONS, ROLE_DISPLAY_TO_DB,
    _PERMISSIONS, _LEAD_BLOCKED, role_label, rbac_check, rbac_gate,
)


# =============================================================================
# Global Context Layer — assembled once per session for injection
# =============================================================================

def build_global_context(db) -> dict:
    """
    Assemble the combined Global Context Layer:
      - Active Style (understanding / raw docs) — scoped to current project/course
      - Target Role + Experience Level
    Returns a dict consumed by CDD / Blueprint / Generate / Editor pipelines.
    """
    _proj_id    = st.session_state.get("selected_project_id")
    _crs_id     = st.session_state.get("selected_course_id")
    active_style    = get_active_style(db, project_id=_proj_id, course_id=_crs_id)
    style_context   = build_style_context(db, active_style) if active_style else ""
    target_role     = st.session_state.get("target_role", "")
    exp_level       = st.session_state.get("experience_level", "")
    target_audience = st.session_state.get("target_audience", "")
    expert_domain   = st.session_state.get("expert_domain", "")

    audience_block = ""
    if target_role or exp_level:
        audience_block = (
            f"TARGET AUDIENCE PROFILE:\n"
            f"- Role: {target_role or 'Not specified'}\n"
            f"- Experience Level: {exp_level or 'Not specified'}\n"
            f"- Domain: {expert_domain or 'Not specified'}\n"
            f"- Audience: {target_audience or 'Not specified'}\n\n"
            f"Calibrate content depth, tone, complexity, and examples to match "
            f"a {exp_level or 'general'} {target_role or 'learner'} in {expert_domain or 'this domain'}."
        )

    return {
        "active_style":      active_style,
        "style_context":     style_context,
        "target_role":       target_role,
        "experience_level":  exp_level,
        "target_audience":   target_audience,
        "expert_domain":     expert_domain,
        "audience_block":    audience_block,
        "style_name":        active_style.name if active_style else "None",
    }


# =============================================================================
# CDD & Blueprint Helper Functions
# =============================================================================

def get_blueprint_prompts(mode: str) -> tuple:
    """Return (system_prompt, user_prompt_template, section_regen_prompt) for the given mode."""
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


def parse_sections_from_text(text: str) -> dict:
    """
    Parse LLM output into a dict of {section_title: section_content} using ## headings.
    - No 'Preamble' bucket: content before the first ## heading is discarded.
    - Empty sections are skipped.
    - 'Purpose' label is globally renamed to 'Goal' per spec.
    """
    sections = {}
    current_title = None   # None = pre-amble before first ##, discard
    current_lines = []

    for line in text.splitlines():
        if line.startswith("## "):
            if current_title is not None:
                body = "\n".join(current_lines).strip()
                if body:
                    sections[current_title] = body
            raw_title = line.replace("## ", "", 1).strip()
            current_title = raw_title
            current_lines = []
        else:
            if current_title is not None:
                current_lines.append(line)

    if current_title is not None:
        body = "\n".join(current_lines).strip()
        if body:
            sections[current_title] = body

    # Rename "Purpose:" → "Goal:" inside content, and section titles
    cleaned = {}
    for title, content in sections.items():
        new_title = title.replace("Purpose", "Goal") if title.strip() in ("Purpose", "Module Purpose") else title
        new_content = content.replace("Purpose:", "Goal:")
        cleaned[new_title] = new_content
    return cleaned


def parse_cdd_flat(raw_text: str) -> dict:
    """
    Parse the new flat CDD output schema (not ##-delimited) into logical blocks:
      - Course Details
      - Course Structure         ← modules + lessons
      - Course Level Assessment  ← summative
      - _raw                     ← always stored (full text for downloads / backend)
      - _validation              ← stored but hidden from UI (Step 6 Validation block)

    The new schema uses plain-text headings like 'Course Details:', 'Course Structure',
    'Course Level Assessment' rather than ## markdown headers.
    """
    import re as _re

    result = {
        "_raw":                  raw_text,
        "Course Details":        "",
        "Course Structure":      "",
        "Course Level Assessment": "",
        "_validation":           "",
    }

    # Normalise line endings
    text = raw_text.strip()

    # ── Split by known top-level section markers ───────────────────────────────
    # Markers we recognise (case-insensitive, flexible spacing):
    _MARKERS = [
        ("Course Details",          "Course Details"),
        ("Course Structure",        "Course Structure"),
        ("Course Level Assessment", "Course Level Assessment"),
        ("Validation",              "_validation"),
    ]

    # Build a regex that finds each marker so we can slice around it
    _marker_pattern = _re.compile(
        r'(?:^|\n)\s*(?:##\s*)?'
        r'(Course Details|Course Structure|Course Level Assessment|Validation)'
        r'\s*:?\s*\n',
        _re.IGNORECASE
    )

    positions = []
    for m in _marker_pattern.finditer(text):
        label_found = m.group(1).strip()
        # Map to canonical key
        for display, key in _MARKERS:
            if label_found.lower() == display.lower():
                positions.append((m.start(), m.end(), key))
                break

    if not positions:
        # Fallback: treat entire output as Course Structure (old schema or unknown)
        result["Course Structure"] = text
        return result

    # Slice sections between markers
    for i, (start, end, key) in enumerate(positions):
        next_start = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        content = text[end:next_start].strip()
        # Replace "Purpose:" with "Goal:" inside content
        content = content.replace("Purpose:", "Goal:")
        result[key] = content

    return result


# ── UI-only fields filter lists (hidden from UI, preserved in DB) ─────────────
_CDD_UI_HIDDEN_PATTERNS = [
    r"(?i)progression\s+logic",
    r"(?i)step\s+\d+",
]

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
    import re as _re
    tl = title.strip().lower()
    for key in _BP_UI_HIDDEN_SECTION_KEYS:
        if key in tl:
            return True
    # Hide "Step N" labels
    if _re.match(r'^step\s+\d+', tl):
        return True
    # Hide any "blueprint complete/ready/validated — module N" variants
    if _re.search(r'blueprint\s+(complete|ready|validated|done)', tl):
        return True
    # Hide "blueprint output" wrapper sections
    if _re.search(r'blueprint\s+output', tl):
        return True
    return False


# ── Patterns to strip from rendered UI content (appear in LLM output) ─────────
_UI_STRIP_PATTERNS = [
    # CDD validation complete lines
    r'(?im)^[•\-\*]?\s*\*{0,2}validation\s+complete\.?\*{0,2}\s*$',
    r'(?im)^[•\-\*]?\s*✅\s*validation\s+complete\.?\s*$',
    r'(?im)^\s*\*{0,2}✅\s*validation\s+complete\*{0,2}.*$',
    r'(?im)^.*\bvalidation\s+complete\b.*$',
    # Blueprint complete / ready lines
    r'(?im)^[•\-\*]?\s*\*{0,2}blueprint\s+(is\s+)?(complete|ready|validated)\.?\*{0,2}\s*$',
    r'(?im)^[•\-\*]?\s*✅\s*blueprint\s+(is\s+)?(complete|ready|validated)\.?\s*$',
    r'(?im)^.*\bblueprint\s+(is\s+)?(complete|ready|validated)\b.*$',
    r'(?im)^.*blueprint\s+is\s+ready\s+for\s+(lesson|content)\s+generation.*$',
    # Step 5/6 validation headers inside content
    r'(?im)^#+\s*step\s+[56]\s*[:\-—]?\s*(blueprint\s+)?validation.*$',
    r'(?im)^#+\s*(blueprint\s+)?validation\s*(check|complete|summary|confirmed)?.*$',
]


def _strip_ui_hidden_text(text: str) -> str:
    """
    Strip backend-only phrases (Validation Complete, Blueprint Complete, etc.)
    from a content string before UI rendering or download output.
    """
    import re as _re
    result = text
    for pattern in _UI_STRIP_PATTERNS:
        result = _re.sub(pattern, '', result)
    result = _re.sub(r'\n{3,}', '\n\n', result)
    return result.strip()



def render_cdd_content(full_content: str, cdd_id: int, ver_label: str, sel_cdd_ver,
                        sel_cdd, db, user_name: str, model_choice: str):
    """
    Schema-driven CDD UI renderer.
    Shows: Course Details · Course Structure · Course Level Assessment
    Hides: Progression Logic · Validation · Step labels
    Stores everything in backend unchanged.
    Includes per-item ⟳ regeneration + section-level 🔄 Regenerate Section.
    """
    import re as _re

    if not full_content:
        st.info("No content yet.")
        return

    parsed = parse_cdd_flat(full_content)

    # Ordered UI-visible blocks
    UI_BLOCKS = [
        ("Course Details",          "📋 Course Details"),
        ("Course Structure",        "🗂️ Course Structure & Module Assessments"),
        ("Course Level Assessment", "🏆 Course Level Assessment"),
    ]

    for block_key, block_label in UI_BLOCKS:
        content = parsed.get(block_key, "").strip()
        if not content:
            continue

        # Strip any "Progression Logic" lines from UI rendering of Course Structure
        if block_key == "Course Structure":
            content_lines = content.splitlines()
            clean_lines = []
            skip = False
            for line in content_lines:
                if _re.search(r'(?i)progression\s+logic', line):
                    skip = True
                    continue
                if skip and (_re.match(r'^\s*(#{1,4}|\*{2}|\-)', line) or line.strip() == ""):
                    if line.strip() != "":
                        skip = False
                if not skip:
                    clean_lines.append(line)
            content = "\n".join(clean_lines).strip()

        # Strip backend-only phrases (Validation Complete, etc.) from all blocks
        content = _strip_ui_hidden_text(content)

        _pending_key = f"cdd_flat_pending_{cdd_id}_{ver_label}_{block_key}"
        _widget_key  = f"cdd_flat_{cdd_id}_{ver_label}_{block_key}"
        _expand = _pending_key in st.session_state
        if _expand:
            st.session_state[_widget_key] = st.session_state.pop(_pending_key)

        with st.expander(block_label, expanded=_expand):
            # Render the content as formatted markdown (read-only view)
            st.markdown(content)

            st.divider()
            _edited = st.text_area(
                "✏️ Edit this block",
                value=content,
                height=200,
                key=_widget_key
            )
            _changed = _edited.strip() != content.strip()
            if _changed:
                st.markdown(
                    "<div style='background:#fff7ed;border:1px solid #fed7aa;"
                    "border-radius:8px;padding:8px 12px;font-size:0.82rem;"
                    "color:#92400e;margin:4px 0;'>✏️ Edited. Add a reason before saving.</div>",
                    unsafe_allow_html=True
                )
            _reason = st.text_input(
                "Edit reason / Regen instruction",
                placeholder="e.g. Updated module duration, add a Bloom's verb",
                key=f"cdd_flat_reason_{cdd_id}_{block_key}_{ver_label}"
            )
            _scope_lbl = st.radio(
                "Scope",
                ["⚡ Apply Once", "🧠 Use as Learning"],
                horizontal=True,
                key=f"cdd_flat_scope_{cdd_id}_{block_key}_{ver_label}"
            )
            _scope = "one_time" if "Once" in _scope_lbl else "learning"
            _can_save = bool(_reason.strip())
            if _changed and not _can_save:
                st.caption("⚠️ Enter a reason above to enable saving.")

            _sc1, _sc2 = st.columns(2)
            if _sc1.button(
                "💾 Save Edit",
                key=f"cdd_flat_save_{cdd_id}_{block_key}_{ver_label}",
                disabled=not _can_save,
                use_container_width=True
            ):
                parsed[block_key] = _edited
                _new_full = _rebuild_cdd_full_content(parsed)
                sel_cdd_ver.full_content = _new_full
                _old_sections = safe_json_loads(sel_cdd_ver.sections) if sel_cdd_ver.sections else {}
                _old_sections[block_key] = _edited
                sel_cdd_ver.sections = json.dumps(_old_sections)
                db.commit()
                if _changed:
                    db.add(FeedbackSignal(
                        block_id=0, generation_id=None,
                        prompt_name="cdd_generation",
                        block_type=f"CDD:{block_key}",
                        signal_source="edit", feedback_scope=_scope,
                        original_content=content, final_content=_edited,
                        edit_reason=_reason,
                        topic=sel_cdd.course_title if sel_cdd else block_key,
                        author=user_name,
                    ))
                    db.commit()
                st.toast(f"✅ '{block_label}' saved.")
                st.session_state[_pending_key] = _edited
                st.rerun()

            # ── Per-item ⟳ regeneration + section-level 🔄 Regenerate Section ─
            _cdd_items = parse_items_from_section(content)
            _cdd_learn = db.query(FeedbackSignal).filter(
                FeedbackSignal.feedback_scope == "learning",
                FeedbackSignal.prompt_name == "cdd_generation"
            ).order_by(FeedbackSignal.created_at.desc()).limit(5).all()
            _cdd_lrn_sigs = "\n".join(
                f"- {s.edit_reason or s.user_instruction}"
                for s in _cdd_learn if (s.edit_reason or s.user_instruction)
            )
            _cdd_lrn = f"\n\nLEARNED PREFERENCES:\n{_cdd_lrn_sigs}" if _cdd_lrn_sigs else ""

            if _cdd_items:
                st.markdown(
                    "<div style='font-size:0.72rem;font-weight:700;text-transform:uppercase;"
                    "letter-spacing:.07em;color:#4f46e5;margin:8px 0 3px;'>"
                    "🔄 Regenerate a single item — click ⟳ next to any item</div>",
                    unsafe_allow_html=True
                )
                for _ci, _citem in enumerate(_cdd_items):
                    _cc1, _cc2 = st.columns([0.85, 0.15])
                    _short_text = _citem["text"][:72] + ("..." if len(_citem["text"]) > 72 else "")
                    _cc1.markdown(
                        f"<div style='font-size:0.8rem;padding:3px 0;color:#374151;'>"
                        f"<code style='background:#eef2ff;color:#4338ca;padding:1px 4px;"
                        f"border-radius:3px;font-size:0.7rem;'>{_ci+1}</code> "
                        f"{_short_text}</div>",
                        unsafe_allow_html=True
                    )
                    if _cc2.button(
                        "⟳",
                        key=f"cdd_ir_{cdd_id}_{block_key}_{ver_label}_{_ci}",
                        help=f"Regenerate only item {_ci+1}. Others stay unchanged.",
                        use_container_width=True
                    ):
                        with st.spinner(f"Regenerating item {_ci+1} only..."):
                            _new_item_txt = regen_single_item(
                                section_title=block_key,
                                section_content=content,
                                item_index=_ci,
                                item_text=_citem["text"],
                                custom_instruction=_reason,
                                model_choice=model_choice,
                                learning_signals=_cdd_lrn,
                            )
                            _patched = patch_item_in_section(content, _ci, _new_item_txt)
                            parsed[block_key] = _patched
                            sel_cdd_ver.full_content = _rebuild_cdd_full_content(parsed)
                            _old_secs = safe_json_loads(sel_cdd_ver.sections) if sel_cdd_ver.sections else {}
                            _old_secs[block_key] = _patched
                            sel_cdd_ver.sections = json.dumps(_old_secs)
                            db.commit()
                            db.add(FeedbackSignal(
                                block_id=0, generation_id=None,
                                prompt_name="cdd_generation",
                                block_type=f"CDD:{block_key}",
                                signal_source="regenerate", feedback_scope="one_time",
                                original_content=_citem["text"], final_content=_new_item_txt,
                                user_instruction=_reason or "(item regenerated)",
                                topic=sel_cdd.course_title if sel_cdd else block_key,
                                author=user_name,
                            ))
                            db.commit()
                        st.toast(f"✅ Item {_ci+1} regenerated. Others unchanged.")
                        st.session_state[_pending_key] = _patched
                        st.rerun()

            # Section-level regenerate button
            if _sc2.button(
                "🔄 Regenerate Section",
                key=f"cdd_flat_regen_{cdd_id}_{block_key}_{ver_label}",
                use_container_width=True
            ):
                with st.spinner(f"Regenerating '{block_label}'..."):
                    regen_p = CDD_SECTION_REGENERATE_PROMPT.format(
                        section_title=block_key,
                        course_title=sel_cdd.course_title if sel_cdd else block_key,
                        custom_instruction=_reason or "Improve and expand this section."
                    )
                    _nc = call_llm(model_choice, CDD_SYSTEM_PROMPT, regen_p)
                if not _nc.startswith("ERROR"):
                    parsed[block_key] = _nc
                    sel_cdd_ver.full_content = _rebuild_cdd_full_content(parsed)
                    _old_secs2 = safe_json_loads(sel_cdd_ver.sections) if sel_cdd_ver.sections else {}
                    _old_secs2[block_key] = _nc
                    sel_cdd_ver.sections = json.dumps(_old_secs2)
                    db.commit()
                    st.toast(f"✅ '{block_label}' regenerated.")
                    st.session_state[_pending_key] = _nc
                    st.rerun()
                else:
                    st.error(_nc)


def _rebuild_cdd_full_content(parsed: dict) -> str:
    """Reconstruct full CDD text from parsed blocks (preserves hidden fields)."""
    parts = []
    for key in ["Course Details", "Course Structure", "Course Level Assessment", "_validation"]:
        val = parsed.get(key, "").strip()
        if val:
            if key == "_validation":
                parts.append(f"## Validation\n{val}")
            else:
                parts.append(f"## {key}\n{val}")
    return "\n\n".join(parts)


def render_blueprint_content(full_content: str, bp_id: int, ver_label: str, sel_bp_ver,
                              sel_bp, db, user_name: str, model_choice: str,
                              blueprint_mode: str = "student"):
    """
    Schema-driven Blueprint UI renderer.
    Shows only schema-defined blocks. Hides Narrative/Modularity, Step labels, validation.
    Renames 'Lesson Structure' → 'Lesson Structure (Topics)'.
    Includes per-item ⟳ regeneration + section-level 🔄 Regenerate Section.
    blueprint_mode: "student" (default) or "teacher" — controls which prompts are used for regen.
    """
    import re as _re

    if not full_content:
        st.info("No Blueprint content yet.")
        return

    # Parse via ## headings (Blueprint uses ## headers)
    raw_sections = parse_sections_from_text(full_content)

    # Filter: hide backend-only sections
    ui_sections = {k: v for k, v in raw_sections.items()
                   if not _is_bp_section_hidden(k)}

    if not ui_sections:
        st.markdown(full_content)
        return

    for sec_title, sec_content in ui_sections.items():
        if not sec_content.strip():
            continue

        # Strip backend-only phrases before rendering
        sec_content = _strip_ui_hidden_text(sec_content)
        if not sec_content.strip():
            continue

        # Rename display labels per spec
        _display = sec_title
        # Strip "Step N:" prefix so blocks show clean titles e.g. "Learning Objectives"
        _display = _re.sub(r'(?i)^step\s+\d+\s*[:\-–—.]\s*', '', _display).strip()
        _display = _re.sub(r'(?i)lesson\s+structure\s*\(planks?\)', 'Lesson Structure (Topics)', _display)
        if _re.search(r'(?i)^lesson structure$', _display.strip()):
            _display = 'Lesson Structure (Topics)'
        _display = _display.replace("Purpose", "Goal")

        _pending_key = f"bp_flat_pending_{bp_id}_{ver_label}_{sec_title}"
        _widget_key  = f"bp_flat_{bp_id}_{ver_label}_{sec_title}"
        _expand = _pending_key in st.session_state
        if _expand:
            st.session_state[_widget_key] = st.session_state.pop(_pending_key)

        with st.expander(f"📋 {_display}", expanded=_expand):
            st.markdown(sec_content)

            st.divider()
            _edited = st.text_area(
                "✏️ Edit this section",
                value=sec_content,
                height=200,
                key=_widget_key
            )
            _changed = _edited.strip() != sec_content.strip()
            if _changed:
                st.markdown(
                    "<div style='background:#fff7ed;border:1px solid #fed7aa;"
                    "border-radius:8px;padding:8px 12px;font-size:0.82rem;"
                    "color:#92400e;margin:4px 0;'>✏️ Edited. Add a reason before saving.</div>",
                    unsafe_allow_html=True
                )
            _reason = st.text_input(
                "Edit reason / Regen instruction",
                placeholder="e.g. Clarified lesson sequence, align to CDD LO 2",
                key=f"bp_flat_reason_{bp_id}_{sec_title}_{ver_label}"
            )
            _scope_lbl = st.radio(
                "Scope",
                ["⚡ Apply Once", "🧠 Use as Learning"],
                horizontal=True,
                key=f"bp_flat_scope_{bp_id}_{sec_title}_{ver_label}"
            )
            _scope = "one_time" if "Once" in _scope_lbl else "learning"
            _can_save = bool(_reason.strip())
            if _changed and not _can_save:
                st.caption("⚠️ Enter a reason above to enable saving.")

            _bc1, _bc2 = st.columns(2)
            if _bc1.button(
                "💾 Save Edit",
                key=f"bp_flat_save_{bp_id}_{sec_title}_{ver_label}",
                disabled=not _can_save,
                use_container_width=True
            ):
                raw_sections[sec_title] = _edited
                _new_full = "\n\n".join(
                    f"## {t}\n{c}" for t, c in raw_sections.items() if c.strip()
                )
                sel_bp_ver.full_content = _new_full
                sel_bp_ver.sections = json.dumps(raw_sections)
                db.commit()
                if _changed:
                    db.add(FeedbackSignal(
                        block_id=0, generation_id=None,
                        prompt_name="blueprint_generation",
                        block_type=f"Blueprint:{sec_title}",
                        signal_source="edit", feedback_scope=_scope,
                        original_content=sec_content, final_content=_edited,
                        edit_reason=_reason,
                        topic=sel_bp.module_title if sel_bp else sec_title,
                        author=user_name,
                    ))
                    db.commit()
                st.toast(f"✅ '{_display}' saved.")
                st.session_state[_pending_key] = _edited
                st.rerun()

            # Section-level 🔄 Regenerate Section
            if _bc2.button(
                "🔄 Regenerate Section",
                key=f"bp_flat_regen_{bp_id}_{sec_title}_{ver_label}",
                use_container_width=True
            ):
                _bp_cdd_sum = ""
                if sel_bp.cdd_id:
                    _bp_cdd_v = get_active_cdd_version(db, sel_bp.cdd_id)
                    if _bp_cdd_v:
                        _bp_cdd_sum = extract_cdd_summary(_bp_cdd_v, max_chars=1500)
                with st.spinner(f"Regenerating '{_display}'..."):
                    _regen_sys, _, _regen_tmpl = get_blueprint_prompts(blueprint_mode)
                    regen_p = _regen_tmpl.format(
                        section_title=sec_title,
                        module_title=sel_bp.module_title,
                        course_title=sel_bp.title,
                        cdd_summary=_bp_cdd_sum or "No CDD linked.",
                        custom_instruction=_reason or "Improve this section."
                    )
                    _nbp = call_llm(model_choice, _regen_sys, regen_p)
                if not _nbp.startswith("ERROR"):
                    raw_sections[sec_title] = _nbp
                    _new_full2 = "\n\n".join(
                        f"## {t}\n{c}" for t, c in raw_sections.items() if c.strip()
                    )
                    sel_bp_ver.full_content = _new_full2
                    sel_bp_ver.sections = json.dumps(raw_sections)
                    db.commit()
                    st.toast(f"✅ '{_display}' regenerated.")
                    st.session_state[_pending_key] = _nbp
                    st.rerun()
                else:
                    st.error(_nbp)

            # ── Per-item ⟳ regeneration ────────────────────────────────────────
            _bp_items = parse_items_from_section(sec_content)
            _bp_learn_q = db.query(FeedbackSignal).filter(
                FeedbackSignal.feedback_scope == "learning",
                FeedbackSignal.prompt_name == "blueprint_generation"
            ).order_by(FeedbackSignal.created_at.desc()).limit(5).all()
            _bp_lrn_sigs = "\n".join(
                f"- {s.edit_reason or s.user_instruction}"
                for s in _bp_learn_q if (s.edit_reason or s.user_instruction)
            )
            _bp_lrn = f"\n\nLEARNED PREFERENCES:\n{_bp_lrn_sigs}" if _bp_lrn_sigs else ""

            if _bp_items:
                st.markdown(
                    "<div style='font-size:0.72rem;font-weight:700;text-transform:uppercase;"
                    "letter-spacing:.07em;color:#4f46e5;margin:8px 0 3px;'>"
                    "🔄 Regenerate a single item — click ⟳ next to any item</div>",
                    unsafe_allow_html=True
                )
                for _bi, _bitem in enumerate(_bp_items):
                    _bbc1, _bbc2 = st.columns([0.85, 0.15])
                    _bshort = _bitem["text"][:72] + ("..." if len(_bitem["text"]) > 72 else "")
                    _bbc1.markdown(
                        f"<div style='font-size:0.8rem;padding:3px 0;color:#374151;'>"
                        f"<code style='background:#eef2ff;color:#4338ca;padding:1px 4px;"
                        f"border-radius:3px;font-size:0.7rem;'>{_bi+1}</code> "
                        f"{_bshort}</div>",
                        unsafe_allow_html=True
                    )
                    if _bbc2.button(
                        "⟳",
                        key=f"bp_ir_{bp_id}_{sec_title}_{ver_label}_{_bi}",
                        help=f"Regenerate only item {_bi+1}. Others stay unchanged.",
                        use_container_width=True
                    ):
                        _bp_cdd_sum_ir = ""
                        if sel_bp.cdd_id:
                            _bp_cdd_v_ir = get_active_cdd_version(db, sel_bp.cdd_id)
                            if _bp_cdd_v_ir:
                                _bp_cdd_sum_ir = extract_cdd_summary(_bp_cdd_v_ir, max_chars=800)
                        with st.spinner(f"Regenerating item {_bi+1} only..."):
                            _ctx = (f"Module: {sel_bp.module_title}. "
                                    f"CDD context: {_bp_cdd_sum_ir[:300] if _bp_cdd_sum_ir else 'N/A'}.")
                            _new_bp_item = regen_single_item(
                                section_title=sec_title,
                                section_content=sec_content,
                                item_index=_bi,
                                item_text=_bitem["text"],
                                custom_instruction=(_reason + " " + _ctx).strip(),
                                model_choice=model_choice,
                                learning_signals=_bp_lrn,
                            )
                            _patched_bp = patch_item_in_section(sec_content, _bi, _new_bp_item)
                            raw_sections[sec_title] = _patched_bp
                            _new_full_ir = "\n\n".join(
                                f"## {t}\n{c}" for t, c in raw_sections.items() if c.strip()
                            )
                            sel_bp_ver.full_content = _new_full_ir
                            sel_bp_ver.sections = json.dumps(raw_sections)
                            db.commit()
                            db.add(FeedbackSignal(
                                block_id=0, generation_id=None,
                                prompt_name="blueprint_generation",
                                block_type=f"Blueprint:{sec_title}",
                                signal_source="regenerate", feedback_scope="one_time",
                                original_content=_bitem["text"], final_content=_new_bp_item,
                                user_instruction=_reason or "(item regenerated)",
                                topic=sel_bp.module_title if sel_bp else sec_title,
                                author=user_name,
                            ))
                            db.commit()
                        st.toast(f"✅ Item {_bi+1} regenerated. Others unchanged.")
                        st.session_state[_pending_key] = _patched_bp
                        st.rerun()


# extract_cdd_summary, extract_blueprint_summary → parsers/cdd_parser.py (re-exported below)


def build_context_injection(db, cdd_id: Optional[int], blueprint_id: Optional[int],
                             target_audience: str = "") -> tuple:
    """
    Build the context injection string for lesson generation.
    Returns (injection_text, cdd_version_label, blueprint_version_label)
    """
    cdd_version_label = "None"
    blueprint_version_label = "None"
    cdd_obj = None
    bp_obj = None

    cdd_ver = get_active_cdd_version(db, cdd_id) if cdd_id else None
    bp_ver = get_active_blueprint_version(db, blueprint_id) if blueprint_id else None

    if cdd_id:
        cdd_obj = db.query(CourseDesignDocument).filter(CourseDesignDocument.id == cdd_id).first()
    if blueprint_id:
        bp_obj = db.query(ModuleBlueprint).filter(ModuleBlueprint.id == blueprint_id).first()

    if cdd_ver:
        cdd_version_label = f"{cdd_obj.title if cdd_obj else 'CDD'} ({cdd_ver.version})"
    if bp_ver:
        blueprint_version_label = f"{bp_obj.title if bp_obj else 'Blueprint'} ({bp_ver.version})"

    # Extract structured fields from sections
    cdd_sections = safe_json_loads(cdd_ver.sections) if cdd_ver and cdd_ver.sections else {}
    bp_sections = safe_json_loads(bp_ver.sections) if bp_ver and bp_ver.sections else {}

    def _find_section(sections: dict, keys: list) -> str:
        for k in keys:
            for sk, sv in sections.items():
                if k.lower() in sk.lower():
                    return sv
        return ""

    learning_objectives = _find_section(bp_sections, ["Learning Objectives"]) or \
                          _find_section(cdd_sections, ["Learning Objectives"])
    tone_guidelines = _find_section(cdd_sections, ["Tone & Style", "Tone", "Style"])
    key_concepts = _find_section(bp_sections, ["Key Concepts"]) or \
                   _find_section(cdd_sections, ["Key Concepts", "Terminology"])
    quality_standards = _find_section(cdd_sections, ["Quality Standards", "Constraints"])

    cdd_summary = extract_cdd_summary(cdd_ver) if cdd_ver else "No CDD linked."
    blueprint_summary = extract_blueprint_summary(bp_ver) if bp_ver else "No Blueprint linked."

    injection = CONTEXT_INJECTION_TEMPLATE.format(
        cdd_title=cdd_obj.title if cdd_obj else "N/A",
        cdd_version=cdd_ver.version if cdd_ver else "N/A",
        cdd_summary=cdd_summary,
        blueprint_title=bp_obj.title if bp_obj else "N/A",
        blueprint_version=bp_ver.version if bp_ver else "N/A",
        blueprint_summary=blueprint_summary,
        learning_objectives=learning_objectives or "Refer to CDD/Blueprint documents.",
        tone_guidelines=tone_guidelines or "Professional, clear, and engaging.",
        key_concepts=key_concepts or "Refer to Blueprint key concepts section.",
        target_audience=target_audience or "As defined in CDD.",
        quality_standards=quality_standards or "High quality, structured, well-cited content."
    )
    return injection, cdd_version_label, blueprint_version_label

# score_content_quality, llm_evaluate_block, check_plagiarism_content, etc.
# → services/evaluation_service.py (re-exported at top of this file)

# Style understanding moved to services/style_service.py (Phase 3 refactoring)
from promptops_app.services.style_service import (
    generate_style_understanding, regenerate_style_understanding,
)


# =============================================================================
# Blueprint Component Parser
# Parses a BlueprintVersion into a structured list of generatable components.
# Used by the Generate tab to drive the dynamic Content Type dropdown.
# =============================================================================

# Fallback options used when no Blueprint is linked (maintains backward compat)
DEFAULT_CONTENT_TYPES = [
    {"label": "Full Course",    "value": "full_course",    "type": "course"},
    {"label": "Lesson",         "value": "lesson",         "type": "lesson"},
    {"label": "Quiz",           "value": "quiz",           "type": "assessment"},
    {"label": "Course Outline", "value": "course_outline", "type": "outline"},
    {"label": "Case Study",     "value": "case_study",     "type": "component"},
]

# Known sub-component keywords to scan for in Blueprint text
_KNOWN_COMPONENTS = [
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


# =============================================================================
# ── GRANULAR ITEM-LEVEL REGENERATION ENGINE ──────────────────────────────────
# =============================================================================
# Purpose:  Allow regenerating a single numbered/bulleted item inside any
#           section without touching the surrounding content.
#
# Three public helpers consumed by the UI:
#   parse_items_from_section(text)       → [{index,type,prefix,text,...}, ...]
#   patch_item_in_section(text,idx,new)  → full section text with only idx replaced
#   regen_single_item(...)               → new item text (no prefix)
# =============================================================================

import re as _re_engine   # alias to avoid shadowing the outer `re`

_NUM_RE  = _re_engine.compile(r"^(\s*)(\d+)([.):])\s+(.+)$")
_BULL_RE = _re_engine.compile(r"^(\s*)([-*•])\s+(.+)$")
_HEAD_RE = _re_engine.compile(r"^(#{1,4})\s+(.+)$")


def parse_items_from_section(text: str) -> list:
    """
    Parse section text into individual editable items.

    Returns a list of dicts:
      {index, type, prefix, text, raw_line, line_index, indent}

    Handles in priority order:
      1. Numbered lists   e.g.  1. … / 1) … / 1: …
      2. Bulleted lists   e.g.  - … / * … / • …
      3. ### Sub-headings
      4. Paragraph fallback (blank-line delimited)
    """
    items  = []
    lines  = text.split("\n")

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

    # Paragraph fallback — no structured items found
    if not items:
        paras = [p.strip() for p in _re_engine.split(r"\n{2,}", text) if p.strip()]
        items = [
            {"index": i, "type": "paragraph", "prefix": "", "text": p,
             "raw_line": p, "line_index": i, "indent": ""}
            for i, p in enumerate(paras)
        ]

    return items


def patch_item_in_section(original_text: str, item_index: int, new_item_text: str) -> str:
    """
    Replace ONLY the item at item_index with new_item_text.
    All other lines are preserved byte-for-byte.
    Returns the reconstructed section text.
    """
    items = parse_items_from_section(original_text)
    if not items or item_index < 0 or item_index >= len(items):
        return original_text

    target = items[item_index]
    new_item_text = new_item_text.strip()

    if target["type"] == "paragraph":
        paras = [p.strip() for p in _re_engine.split(r"\n{2,}", original_text)]
        if item_index < len(paras):
            paras[item_index] = new_item_text
        return "\n\n".join(paras)

    # Line-based replacement
    lines = original_text.split("\n")
    li    = target["line_index"]
    if li >= len(lines):
        return original_text

    indent = target.get("indent", "")
    t_type = target["type"]
    prefix = target["prefix"]

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


def regen_single_item(
    section_title: str,
    section_content: str,
    item_index: int,
    item_text: str,
    custom_instruction: str,
    model_choice: str = "GPT-5.4",
    learning_signals: str = "",
) -> str:
    """
    Regenerate one item. Returns the new item text only (no prefix).
    Falls back to the original item_text on LLM error.
    """
    items = parse_items_from_section(section_content)
    context_others = "\n".join(
        f"  {it['prefix']} {it['text']}"
        for i, it in enumerate(items)
        if i != item_index and it["type"] != "paragraph"
    )

    user_p = (
        f"Section: \"{section_title}\"\n\n"
        f"All OTHER items in this section (DO NOT change these):\n"
        f"{context_others if context_others else '  (none)'}\n\n"
        f"Item to regenerate (position {item_index + 1}):\n"
        f"  \"{item_text}\"\n\n"
        f"Regeneration instruction: "
        f"{custom_instruction or 'Improve this item — make it clearer, more specific, and better aligned to the section.'}"
        f"{learning_signals}\n\n"
        f"Return ONLY the new text for this single item. "
        f"No numbering, no bullet, no explanation."
    )

    result = call_llm(model_choice, _ITEM_REGEN_SYSTEM, user_p)
    if result.startswith("ERROR"):
        return item_text  # safe fallback

    # Strip any accidental prefix the LLM added
    result = _re_engine.sub(r"^\d+[.):]\s*", "", result.strip())
    result = _re_engine.sub(r"^[-*•]\s*", "", result.strip())
    return result.strip()


def parse_blueprint_components(bp_version) -> list:
    """
    Parse a BlueprintVersion ORM object into a filtered list of generatable
    dropdown components for the Generate tab.

    Returns ONLY:
      1. Lessons  — all lessons in Blueprint order (e.g. "1.1 What Is a Robot?")
      2. Module Assessment — the single module-level assessment for this Blueprint

    Everything else is excluded to keep the Content Type dropdown clean
    and scoped to the selected module only.
    """
    if not bp_version:
        return []

    # Keywords that identify a Module Assessment section
    _MODULE_ASSESSMENT_KEYS = {
        "module assessment", "module-level assessment",
        "knowledge check", "module check"
    }

    try:
        sections  = safe_json_loads(bp_version.sections) if bp_version.sections else {}
        full_text = bp_version.full_content or ""
        components = []
        seen_vals  = set()

        # ── Step 1: Extract Lessons in Blueprint order ──────────────────────
        # Search the full blueprint text so no lesson is missed regardless
        # of which section the LLM placed them in.
        search_text = full_text or " ".join(sections.values())

        # Require the word "Lesson" — this prevents numbered sub-items inside
        # lesson bodies (e.g. "1. Understand basics", "2. Apply concepts")
        # from stealing lesson numbers and causing real lessons to be skipped.
        # Matches:  "### Lesson 1.1: Title"
        #           "**Lesson 1.2 — Title**"
        #           "Lesson 3: Title"
        _lesson_heading_pat = re.compile(
            r'(?:^|\n)'
            r'\s*(?:#{1,4}\s*|\*{1,2})?'       # optional ## heading or **bold
            r'[Ll]esson\s+'                      # REQUIRED "Lesson" word
            r'(\d+(?:\.\d+)?)'                  # number: 1 or 1.1
            r'[\s:\-—.]*'
            r'([^\n#*]{2,80})',                  # title text (2-80 chars)
            re.MULTILINE
        )

        # Only skip titles that are clearly metadata lines, not lesson names
        _LESSON_SKIP_WORDS = (
            "objective", "duration", "strategy", "interaction",
            "bloom", "skill focus", "alignment", "design intent",
            "lesson type", "activity type",
        )

        seen_lesson_nums = set()
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
                        "lesson_number":     num_str,
                        "lesson_title":      raw_title,
                        "blueprint_section": "Lesson Plan",
                    },
                })

        # ── Step 2: Module Assessment (one entry, from section keys) ─────────
        # Use the actual section title from the blueprint as the dropdown label.
        for sec_key in sections:
            sk = sec_key.lower().strip()
            if any(mk in sk for mk in _MODULE_ASSESSMENT_KEYS):
                val = "module_assessment"
                if val not in seen_vals:
                    seen_vals.add(val)
                    # Show the real section name (e.g. "Module Assessment")
                    # not a hard-coded string
                    components.append({
                        "label":    sec_key.strip(),
                        "value":    val,
                        "type":     "assessment",
                        "metadata": {"blueprint_section": sec_key},
                    })
                break  # only one module assessment per Blueprint

        # Fallback: if no assessment found via section keys, scan full text
        if "module_assessment" not in seen_vals:
            _assess_pat = re.compile(
                r'(?:^|\n)\s*(?:#{1,4}\s*)?'
                r'((?:Module\s+)?Assessment[^\n]{0,60})',
                re.MULTILINE | re.IGNORECASE
            )
            _am = _assess_pat.search(search_text)
            if _am:
                _assess_label = _am.group(1).strip().strip("*:").strip()
                if _assess_label and "module_assessment" not in seen_vals:
                    seen_vals.add("module_assessment")
                    components.append({
                        "label":    _assess_label,
                        "value":    "module_assessment",
                        "type":     "assessment",
                        "metadata": {"blueprint_section": _assess_label},
                    })

        return components

    except Exception:
        return []



def build_component_generation_prompt(
    component: dict,
    bp_version,
    cdd_version,
    target_audience: str = "",
    expert_domain: str = "",
) -> tuple:
    """
    Build (system_prompt, user_prompt) for a specific Blueprint component.

    For 'lesson' type components, the caller should use the existing
    LESSON_WITH_CONTEXT_SYSTEM / LESSON_WITH_CONTEXT_USER flow.
    This function handles all other component types.

    Returns:
        (system_prompt str, user_prompt str)
    """
    comp_type  = component.get("type", "component")
    comp_label = component.get("label", "Content")
    comp_value = component.get("value", "")          # snake_case identifier
    comp_meta  = component.get("metadata", {})

    # Build context block
    bp_secs  = safe_json_loads(bp_version.sections)  if bp_version  and bp_version.sections  else {}
    cdd_secs = safe_json_loads(cdd_version.sections) if cdd_version and cdd_version.sections else {}

    def _find(secs, keys, max_c=400):
        for k in keys:
            for sk, sv in secs.items():
                if k.lower() in sk.lower():
                    return str(sv)[:max_c]
        return ""

    bp_los   = _find(bp_secs,  ["Learning Objectives"])
    bp_plan  = _find(bp_secs,  ["Lesson Plan"], 600)
    bp_keys  = _find(bp_secs,  ["Key Concepts"])
    cdd_los  = _find(cdd_secs, ["Learning Objectives"])
    cdd_tone = _find(cdd_secs, ["Tone & Style", "Tone"])
    cdd_qual = _find(cdd_secs, ["Quality Standards"])

    ctx_block = f"""BLUEPRINT CONTEXT (Module: {getattr(bp_version, 'blueprint_id', 'N/A') if bp_version else 'N/A'}):
Module LOs: {bp_los or 'See Blueprint'}
Key Concepts: {bp_keys or 'See Blueprint'}
Lesson Plan excerpt: {bp_plan[:400] if bp_plan else 'See Blueprint'}

CDD CONTEXT:
Course LOs: {cdd_los or 'See CDD'}
Tone & Style: {cdd_tone or 'Professional, engaging, accessible'}
Quality Standards: {cdd_qual or 'High quality, structured content'}

TARGET AUDIENCE: {target_audience or 'As defined in CDD'}
EXPERT DOMAIN: {expert_domain or 'General'}"""

    # ── Component-type routing ────────────────────────────────────────────
    if comp_type == "assessment" or "assessment" in comp_label.lower() or "quiz" in comp_label.lower():
        system_p = (
            f"You are an expert Instructional Designer creating a lesson or module quiz. Map objective coverage before writing a single question — questions written before the alignment map is complete will drift.\n"
            f"Use '## ' for all main section headings."
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
            f"You are an expert Instructional Designer creating a lesson reflection or journal prompt. Determine the reflection type for the strategy before writing any questions.\n"
            f"Use '## ' for all main section headings."
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
- For Strategy 7 MS courses: keep all three questions at Understand/Apply level — do not ask learners to evaluate or judge whether a career is right for them

## Step 3  Format and Stakes Calibration
- Expected writing: 3–5 sentences per question — not a paragraph essay
- Explicitly mark as low-stakes in the student-facing intro: 'There are no right or wrong answers here'
- Avoid: 'Write about your feelings about…' — too vague and developmentally inappropriate for CTE
- Prefer specific and grounded: 'Describe one moment from today's lesson that surprised you. What made it surprising?'
- For Strategy 5 / 8: reflection is appropriate after a compliance scenario; frame it as professional reasoning practice, not personal expression"""

    elif any(kw in comp_label.lower() for kw in ("explorer", "spotlight", "career", "connection")):
        system_p = (
            f"You are a curriculum developer creating engaging, real-world connection components for eLearning.\n"
            f"Use '## ' for all main section headings."
        )
        user_p = f"""Create a {comp_label} component for this module.

{ctx_block}

Structure with these sections:
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
            f"You are an expert Instructional Designer creating a lesson reflection or journal prompt. Determine the reflection type for the strategy before writing any questions.\n"
            f"Use '## ' for all main section headings."
        )
        user_p = f"""Create a {comp_label} for this module.

{ctx_block}

Work through each step in order before writing any prompts.

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
- For Strategy 7 MS courses: keep all three questions at Understand/Apply level — do not ask learners to evaluate or judge whether a career is right for them

## Step 3  Format and Stakes Calibration
- Expected writing: 3–5 sentences per question — not a paragraph essay
- Explicitly mark as low-stakes in the student-facing intro: 'There are no right or wrong answers here'
- Avoid: 'Write about your feelings about…' — too vague and developmentally inappropriate for CTE
- Prefer specific and grounded: 'Describe one moment from today's lesson that surprised you. What made it surprising?'
- For Strategy 5 / 8: reflection is appropriate after a compliance scenario; frame it as professional reasoning practice, not personal expression"""

    elif "knowledge check" in comp_label.lower():
        system_p = (
            f"You are an expert Instructional Designer writing embedded knowledge check interactions. Complete all three steps in order — do not write the question before the concept distillation is done.\n"
            f"Use '## ' for all main section headings."
        )
        user_p = f"""Create a {comp_label} (formative check) for this module.

{ctx_block}

Work through each step in order before writing any questions.

## Step 1  Concept Distillation
- State the single most important thing the learner should be able to do with this concept after this plank — one sentence
- Check: is this testable in an auto-gradable format? If not, it belongs in a reflection prompt, not a knowledge check — redesign accordingly

## Step 2  Check Design by Strategy
Select the check type based on strategy:
- Strategy 4 (Skill+Practice): sequencing / process ordering / correct-step identification
- Strategy 5 (Standard+Procedure): compliance scenario — 'What should happen next according to the regulation or protocol?'
- Strategy 1 (Investigation): evidence evaluation — 'Which finding best supports the claim?'
- Strategy 2 (Role/Context): role-based judgment — 'In this workplace situation, what does the professional do first?'
- Strategy 3 (Decision/Sim): decision point — 'Given this constraint, which option is most appropriate and why?'
- Strategy 8 (Cert Prep): exam-style MCQ — single best answer, timed if platform supports it

For each knowledge check produce:
- Stem: 1–2 sentences maximum — concise enough for a mid-lesson checkpoint
- 2–4 answer options (true/false is acceptable for foundational concepts in early lessons)
- Correct answer + 1-sentence feedback explaining the reasoning behind it
- Incorrect feedback: one line per wrong option — name the specific misunderstanding, not just 'incorrect'
- Bridge forward: end feedback with one sentence connecting to the next plank ('Now that you understand X, let's look at how it applies in…')

## Step 3  Placement and Pacing Rules
- One check per concept — do not bundle two concepts into a single question
- Place the check immediately after the concept is introduced — not at the end of a long content block
- Do not place two checks back-to-back without content in between
- Target 3–4 checks per 8–12 plank lesson, distributed across the arc

★ For Career Exploration (Strategy 7) MS courses: replace knowledge checks with interest prompts — e.g., 'Which part of this career surprised you most?' or 'What question does this make you want to ask?' Right/wrong framing is inappropriate for exploratory content."""

    elif comp_value in ("assessments", "assessment_plan") or comp_label.lower() in ("assessments", "assessment plan"):
        # Module-level comprehensive Assessments section (new)
        system_p = (
            f"You are an expert Instructional Designer creating a lesson or module quiz. Map objective coverage before writing a single question — questions written before the alignment map is complete will drift.\n"
            f"Use '## ' for all main section headings."
        )
        user_p = f"""Create the full Assessments package for this module.

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

    elif comp_value == "teacher_resources" or "teacher resource" in comp_label.lower():
        # Teacher Resources — works for both module-level and course-level
        _scope = "course" if "course" in comp_label.lower() else "module"
        system_p = (
            f"You are a senior instructional designer creating instructor support materials.\n"
            f"Use '## ' for all main section headings."
        )
        if _scope == "course":
            user_p = f"""Create Course-Level Teacher Resources.

{ctx_block}

## Course Facilitator Guide
Complete facilitation roadmap: weekly schedule, pacing guide, milestone checkpoints.

## Instructor Onboarding Checklist
Everything a new instructor must know/do before teaching this course.
Technical setup, prerequisite reading, key platform features.

## Assessment Answer Keys & Rubrics Master
Consolidated answer keys for all module and course-level assessments.

## Student Progress Monitoring Framework
How to track learner engagement, flag at-risk students, interpret data.
Intervention triggers and suggested responses.

## Course-Level FAQ
Top 15–20 questions learners ask, with suggested responses.

## Continuous Improvement Log
Template for instructors to document issues, successes, and suggested course revisions after each cohort."""
        else:
            user_p = f"""Create Module-Level Teacher Resources.

{ctx_block}

## Instructor Overview
Module purpose, key teaching moments, prerequisite knowledge required.

## Facilitation Guide
Step-by-step delivery notes for each lesson segment.
Discussion prompts, facilitation tips, and timing recommendations.

## Differentiation Strategies
Extensions for advanced learners. Scaffolds for struggling learners. Accommodations for diverse needs.

## Common Misconceptions & Interventions
Top 3–5 misconceptions learners have about this module's content.
Specific instructional interventions to address each.

## Discussion Questions
5–8 high-quality discussion questions with key talking points.

## Answer Keys
Complete answer keys for all module assessments included in this module."""

    elif comp_value == "worksheet" or "worksheet" in comp_label.lower():
        system_p = (
            f"You are an expert Instructional Designer creating a lesson worksheet. Determine the correct worksheet type for the strategy before designing any tasks.\n"
            f"Use '## ' for all main section headings."
        )
        user_p = f"""Create a learner Worksheet for this module.

{ctx_block}

Work through each step in order before designing any tasks.

## Step 1  Alignment and Type Selection
- State the learning objective — every task on the worksheet must trace back to it
- Confirm Bloom's level: the worksheet should operate at Apply or above, not Remember
- Select the correct worksheet type for the strategy:
  - Strategy 4 (Skill+Practice): procedural step-sequencing or checklist
  - Strategy 6 (Project/Build): design brief, planning template, or critique rubric
  - Strategy 3 (Decision/Sim): decision journal — document the choice, the reasoning, and the predicted consequence
  - Strategy 7 (Career Exploration): interest inventory or low-stakes career comparison — no right/wrong answers
  - Strategy 1 (Investigation): evidence log or claim-evidence-reasoning (CER) frame
  - Strategy 5 / 8 (Procedure/Cert): compliance checklist or timed practice item set
  - Strategy 2 (Role/Context): role-based task brief — learner completes a professional task as if in the role

## Step 2  Task Sections
- Section A — Guided practice: learner completes a partially built structure (scaffolded support)
- Section B — Independent practice: same concept applied without the scaffold
- Section C — Application task: learner uses the concept in a new, realistic context from the career field
- Total expected completion time: 10–15 minutes — trim ruthlessly if it runs over

## Step 3  Answer Key
- Provide complete answers for all sections, including acceptable variations for open-response items
- For rubric-scored items: define what 'meets expectations' looks like in 1–2 observable, measurable criteria"""

    elif comp_value == "journal_prompts" or "journal" in comp_label.lower():
        system_p = (
            f"You are an expert Instructional Designer creating a lesson reflection or journal prompt. Determine the reflection type for the strategy before writing any questions.\n"
            f"Use '## ' for all main section headings."
        )
        user_p = f"""Create a Journal Prompts package for this module.

{ctx_block}

Work through each step in order before writing any prompts.

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
- For Strategy 7 MS courses: keep all three questions at Understand/Apply level — do not ask learners to evaluate or judge whether a career is right for them

## Step 3  Format and Stakes Calibration
- Expected writing: 3–5 sentences per question — not a paragraph essay
- Explicitly mark as low-stakes in the student-facing intro: 'There are no right or wrong answers here'
- Avoid: 'Write about your feelings about…' — too vague and developmentally inappropriate for CTE
- Prefer specific and grounded: 'Describe one moment from today's lesson that surprised you. What made it surprising?'
- For Strategy 5 / 8: reflection is appropriate after a compliance scenario; frame it as professional reasoning practice, not personal expression"""

    elif comp_value == "project_work" or "project" in comp_label.lower():
        system_p = (
            f"You are a curriculum designer creating course-level capstone project materials.\n"
            f"Use '## ' for all main section headings."
        )
        user_p = f"""Create a Course-Level Project Work assignment.

{ctx_block}

## Project Overview
Purpose, scope, and alignment to course learning objectives. Why this project matters.

## Project Brief
Clear description of what learners must create/produce/demonstrate.
Deliverables list with format specifications.

## Step-by-Step Project Guide
Phase 1: Research & Planning (estimated time)
Phase 2: Development / Creation (estimated time)
Phase 3: Review & Refinement (estimated time)
Phase 4: Submission & Presentation (estimated time)

## Assessment Rubric
Criteria, performance levels (Exemplary / Proficient / Developing / Beginning), and point allocations.
State minimum passing criteria clearly.

## Bloom's Alignment Table
| Deliverable | Course LO | Bloom's Level |

## Submission Requirements
Format, file types, naming conventions, submission instructions."""

    elif comp_value == "summative_assessments" or "summative" in comp_label.lower():
        system_p = (
            f"You are an assessment designer creating a course-level summative assessment.\n"
            f"Use '## ' for all main section headings."
        )
        user_p = f"""Create a Course-Level Summative Assessment.

{ctx_block}

## Assessment Overview
Purpose, scope, total marks, time allocation, and passing criteria.

## Section A — Knowledge Check (Multiple Choice)
15–20 questions covering key factual and conceptual knowledge across all modules.
Include complete answer key with rationale for each correct answer.
Show Bloom's level per question.

## Section B — Applied Understanding (Short Answer)
5–8 questions requiring learners to Apply, Analyse, or Explain.
Include model answers and marking guidance.

## Section C — Capstone Scenario (Extended Response)
1–2 complex scenario-based questions requiring Synthesis and Evaluation.
Include detailed scoring rubric.

## LO Coverage Matrix
| Course LO | Assessed By (Section + Question #) | Bloom's Level |

## Adaptive Retake Guidelines
Conditions for retake, remediation pathways, maximum attempts."""

    elif comp_value == "learning_activities" or "learning activit" in comp_label.lower():
        system_p = (
            f"You are a senior instructional designer creating module learning activities.\n"
            f"Use '## ' for all main section headings."
        )
        user_p = f"""Create a detailed Learning Activities plan for this module.

{ctx_block}

## Activity Overview
Purpose of each activity type and how they support the module LOs.

## Problem-Based Scenario
A real-world scenario relevant to {expert_domain or "the subject domain"}.
Include: scenario description, task instructions, guiding questions, facilitator notes.

## Case Study
A detailed case relevant to learners in {expert_domain or "this field"}.
Include: background, challenge, discussion questions, debrief guide.

## Group Discussion
2–3 structured discussion questions with facilitation tips.
Include: expected outcomes, time allocation, assessment rubric.

## Interactive / Role-Play Activity
Description of the simulated activity.
Include: setup instructions, roles, success criteria, debrief questions.

## Facilitator Notes
Sequencing guidance, timing per activity, differentiation suggestions."""

    else:
        # Generic fallback for any unrecognized component type
        system_p = (
            f"You are a senior instructional designer creating {comp_label} for an eLearning course.\n"
            f"Use '## ' for all main section headings."
        )
        user_p = f"""Create a high-quality {comp_label} for this module.

{ctx_block}

Use clear '## ' headings to structure the content.
Ensure alignment with the Blueprint LOs and CDD quality standards.
Include practical examples and content appropriate for {target_audience or 'the target audience'}."""

    return system_p, user_p

# -----------------------------------------------------------------------------
# Streamlit UI
# -----------------------------------------------------------------------------
st.set_page_config(page_title="Content AI Studio", layout="wide", page_icon="📘", initial_sidebar_state="expanded")
if "theme" not in st.session_state: st.session_state.theme = "light"
inject_premium_style(st.session_state.theme)

if "user" not in st.session_state: st.session_state.user = None

# -----------------------------------------------------------------------------
# Style Engine & PromptOps Constants
# -----------------------------------------------------------------------------
def evaluate_readability(text: str) -> dict:
    words = text.split()
    avg_word_len = sum(len(w) for w in words) / len(words) if words else 0.0
    sentences = text.count(".") + text.count("!") + text.count("?")
    complexity = "Low" if avg_word_len < 5 else "Medium" if avg_word_len < 7 else "High"
    return {"avg_word_length": f"{avg_word_len:.2f}", "complexity": complexity, "sentence_count": sentences}

# =============================================================================
# Login Page — Sidebar login with branding in main area
# =============================================================================
# =============================================================================
# ── BLUEPRINT-DRIVEN FLOW HELPERS ────────────────────────────────────────────
# =============================================================================

def extract_module_count_from_cdd(cdd_version) -> int:
    """
    Parse a CDD version and return how many modules the course defines.
    Looks at the 'Course Structure & Modules' section for numbered module entries.
    Falls back to scanning all section text for 'Module N' patterns.
    Returns 1 as a safe default.
    """
    if not cdd_version:
        return 1
    try:
        sections = safe_json_loads(cdd_version.sections) if cdd_version.sections else {}
        # Try the dedicated course structure section first
        for key, val in sections.items():
            if any(k in key.lower() for k in ["course structure", "module", "modules"]):
                nums = re.findall(r"(?:Module|module|MODULE)\s+(\d+)", val)
                if nums:
                    return max(int(n) for n in nums)
        # Fallback: scan full content
        full = cdd_version.full_content or ""
        nums = re.findall(r"(?:Module|MODULE)\s+(\d+)", full)
        if nums:
            return max(int(n) for n in nums)
    except Exception:
        pass
    return 1


def get_module_completion_status(db, blueprint_id: int) -> dict:
    """
    For a given blueprint, compute how many lessons/components have been
    generated (Blocks in non-Draft state or any state) vs the total
    expected from the blueprint's Lesson Plan section.

    Returns:
        {
          "total_lessons": int,
          "generated_lessons": int,
          "completed": bool,   # True when generated_lessons >= total_lessons
          "module_components_generated": bool,
          "lesson_labels": [str],   # e.g. ["Lesson 1", "Lesson 2"]
        }
    """
    result = {
        "total_lessons": 0,
        "generated_lessons": 0,
        "completed": False,
        "module_components_generated": False,
        "lesson_labels": [],
    }
    try:
        bp_ver = get_active_blueprint_version(db, blueprint_id)
        if not bp_ver:
            return result

        # Count lessons defined in blueprint
        components = parse_blueprint_components(bp_ver)
        lesson_comps = [c for c in components if c["type"] == "lesson"]
        result["total_lessons"]  = len(lesson_comps)
        result["lesson_labels"]  = [c["label"] for c in lesson_comps]

        # Count generations that link to this blueprint
        gens = db.query(Generation).filter(Generation.blueprint_id == blueprint_id).all()
        # A lesson is "generated" if there's at least one generation for it
        gen_topics = set(g.topic.lower() for g in gens)
        generated = 0
        for lc in lesson_comps:
            label_lower = lc["label"].lower()
            if any(label_lower in gt or gt in label_lower for gt in gen_topics):
                generated += 1
        result["generated_lessons"] = generated
        result["completed"] = generated >= result["total_lessons"] and result["total_lessons"] > 0

        # Check if module-level components (assessments/worksheet/etc.) have been generated
        module_comp_types = {"assessments", "assessment_plan", "teacher_resources",
                             "worksheet", "journal_prompts"}
        module_gens = [g for g in gens
                       if any(mc in (g.block_type or "").lower() for mc in module_comp_types)]
        result["module_components_generated"] = bool(module_gens)
    except Exception:
        pass
    return result


def get_all_modules_completion(db, cdd_id: int) -> dict:
    """
    For a CDD, check completion across all its blueprints.
    Returns {
      "total_modules": int,
      "completed_modules": int,
      "all_modules_done": bool,
      "all_module_components_done": bool,
      "per_module": [{blueprint_id, module_number, title, status}]
    }
    """
    blueprints = db.query(ModuleBlueprint).filter(
        ModuleBlueprint.cdd_id == cdd_id
    ).order_by(ModuleBlueprint.module_number).all()

    if not blueprints:
        return {
            "total_modules": 0, "completed_modules": 0,
            "all_modules_done": False, "all_module_components_done": False,
            "per_module": [],
        }

    bp_ids = [bp.id for bp in blueprints]

    # Bulk-fetch all BlueprintVersions for these blueprints in one query,
    # then pick the active version per blueprint (avoids 2 queries per blueprint).
    all_bp_versions = db.query(BlueprintVersion).filter(
        BlueprintVersion.blueprint_id.in_(bp_ids)
    ).all()
    versions_by_bp = {}
    for v in all_bp_versions:
        versions_by_bp.setdefault(v.blueprint_id, []).append(v)
    active_ver_by_bp = {}
    for bp in blueprints:
        candidates = versions_by_bp.get(bp.id, [])
        active_ver_by_bp[bp.id] = next(
            (v for v in candidates if v.version == bp.active_version), None
        )

    # Bulk-fetch all Generations for these blueprints in one query.
    all_gens = db.query(Generation).filter(
        Generation.blueprint_id.in_(bp_ids)
    ).all()
    gens_by_bp = {}
    for g in all_gens:
        gens_by_bp.setdefault(g.blueprint_id, []).append(g)

    module_comp_types = {"assessments", "assessment_plan", "teacher_resources",
                         "worksheet", "journal_prompts"}
    per_module = []
    completed  = 0
    mc_done    = 0

    for bp in blueprints:
        result = {
            "total_lessons": 0, "generated_lessons": 0, "completed": False,
            "module_components_generated": False, "lesson_labels": [],
        }
        try:
            bp_ver = active_ver_by_bp.get(bp.id)
            if bp_ver:
                components  = parse_blueprint_components(bp_ver)
                lesson_comps = [c for c in components if c["type"] == "lesson"]
                result["total_lessons"] = len(lesson_comps)
                result["lesson_labels"] = [c["label"] for c in lesson_comps]

                gens       = gens_by_bp.get(bp.id, [])
                gen_topics = {g.topic.lower() for g in gens}
                generated  = 0
                for lc in lesson_comps:
                    label_lower = lc["label"].lower()
                    if any(label_lower in gt or gt in label_lower for gt in gen_topics):
                        generated += 1
                result["generated_lessons"] = generated
                result["completed"] = generated >= result["total_lessons"] and result["total_lessons"] > 0

                module_gens = [g for g in gens
                               if any(mc in (g.block_type or "").lower() for mc in module_comp_types)]
                result["module_components_generated"] = bool(module_gens)
        except Exception:
            pass

        per_module.append({
            "blueprint_id":  bp.id,
            "module_number": bp.module_number,
            "title":         bp.title,
            "status":        result,
        })
        if result["completed"]:
            completed += 1
        if result["module_components_generated"]:
            mc_done += 1

    total = len(blueprints)
    return {
        "total_modules":              total,
        "completed_modules":          completed,
        "all_modules_done":           completed >= total and total > 0,
        "all_module_components_done": mc_done >= total and total > 0,
        "per_module":                 per_module,
    }


def is_component_type_module_level(comp_value: str) -> bool:
    """Return True for module-level components (end-of-module, not lesson-level).
    Module Assessment requires lessons to be generated first."""
    MODULE_LEVEL = {"assessments", "assessment_plan", "module_assessment", "teacher_resources",
                    "worksheet", "journal_prompts"}
    return comp_value in MODULE_LEVEL


def is_component_type_course_level(comp_value: str) -> bool:
    """Return True for course-level components (cross-module, end-of-course)."""
    COURSE_LEVEL = {"project_work", "summative_assessments", "learning_activities"}
    return comp_value in COURSE_LEVEL


def render_completion_gate(status: dict, component_label: str, override: bool = False) -> bool:
    """
    Render a completion status banner. Returns True if generation is ALLOWED.

    - If all lessons are complete: allowed unconditionally.
    - If no lessons exist at all and it's an assessment: warns but allows.
    - If lessons are partially done:
        - Non-assessment module components: hard block (return False).
        - Assessment components with override=True (user confirmed via checkbox): allowed with
          an informational banner so users know they're proceeding with incomplete lessons.
        - Assessment components without override: hard block (return False).
    """
    total    = status.get("total_lessons", 0)
    done     = status.get("generated_lessons", 0)
    complete = status.get("completed", False)
    is_assessment = "assessment" in component_label.lower()

    if complete:
        st.success(
            f"✅ All {total} lesson(s) completed for this module. "
            f"**{component_label}** generation is unlocked."
        )
        return True
    elif is_assessment and done == 0 and total == 0:
        # No lessons exist at all — warn but allow Module Assessment
        st.warning(
            f"⚠️ **Module Assessment can be generated, but it may be out of context "
            f"since Lessons are not created yet.** "
            f"For best results, generate lessons first."
        )
        return True   # Allow with warning per spec
    elif is_assessment and override:
        # User explicitly confirmed via the override checkbox — proceed with informational notice
        _pct = int(done / total * 100) if total > 0 else 0
        st.info(
            f"ℹ️ **Proceeding with {component_label} generation.** "
            f"Note: only {done}/{total} lesson(s) have been generated for this module. "
            f"The assessment will be based on the available lessons."
        )
        return True
    else:
        _pct = int(done / total * 100) if total > 0 else 0
        st.markdown(
            f"<div style='background:#fef2f2;border:1.5px solid #fca5a5;"
            f"border-radius:9px;padding:12px 16px;'>"
            f"<div style='font-size:0.9rem;font-weight:700;color:#b91c1c;margin-bottom:4px;'>"
            f"🚫 Module lessons are incomplete</div>"
            f"<div style='font-size:0.84rem;color:#7f1d1d;'>"
            f"{'Not all lessons have been generated yet' if is_assessment else 'Complete all lessons before generating module-level components'}.<br>"
            f"<strong>Completion Status: {done} / {total} lessons generated</strong></div>"
            f"<div style='margin-top:8px;background:#fee2e2;border-radius:6px;"
            f"height:10px;overflow:hidden;'>"
            f"<div style='background:#ef4444;height:100%;width:{_pct}%;'></div></div>"
            f"</div>",
            unsafe_allow_html=True
        )
        return False


def render_course_completion_gate(course_status: dict, component_label: str) -> bool:
    """
    Render a course-level completion gate. Returns True if allowed.
    """
    total    = course_status.get("total_modules", 0)
    done     = course_status.get("completed_modules", 0)
    mc_done  = course_status.get("all_module_components_done", False)
    allowed  = course_status.get("all_modules_done", False) and mc_done

    if allowed:
        st.success(
            f"✅ All {total} modules complete with module-level components. "
            f"**{component_label}** generation is unlocked."
        )
        return True
    else:
        missing_msg = []
        if not course_status.get("all_modules_done", False):
            missing_msg.append(f"Lessons: {done}/{total} modules fully generated")
        if not mc_done:
            missing_msg.append("Module-level components not yet generated for all modules")
        st.markdown(
            f"<div style='background:#fef2f2;border:1.5px solid #fca5a5;"
            f"border-radius:9px;padding:12px 16px;'>"
            f"<div style='font-size:0.9rem;font-weight:700;color:#b91c1c;margin-bottom:4px;'>"
            f"🚫 Course content is incomplete</div>"
            f"<div style='font-size:0.84rem;color:#7f1d1d;'>"
            f"Complete all modules before generating course-level components.<br>"
            f"{'<br>'.join(f'• {m}' for m in missing_msg)}</div>"
            f"</div>",
            unsafe_allow_html=True
        )
        return False


def login_page():
    """Display the login screen with sidebar sign-in form and main area branding."""
    inject_premium_style()
    db = SessionLocal()

    # Seed default users on first run
    if not db.query(User).first():
        seed_data(db)
    if not db.query(Prompt).filter(Prompt.name == "lesson_generator").first():
        p = Prompt(name="lesson_generator", description="Generates eLearning lessons in a unified voice.", owner="admin", active_version="v1", tags="elearning,lessons")
        db.add(p); db.commit(); db.refresh(p)
        db.add(PromptVersion(prompt_id=p.id, version="v1", system_prompt=LOGIN_DEFAULT_PROMPT_SYSTEM, user_prompt_template=LOGIN_DEFAULT_PROMPT_USER, is_active=True))
    db.commit()

    # --- Sidebar: Simple Login Form ---
    _logo = _logo_b64()
    st.sidebar.markdown(
        "<div style='text-align:center;padding:1rem 0 0.5rem 0;'>"
        "<div style='position:relative;display:inline-block;margin-bottom:2px;'>"
        f"<img src='data:image/png;base64,{_logo}' style='position:absolute;right:calc(100% + 5px);top:50%;transform:translateY(-50%);height:32px;width:auto;'>"
        "<span style='font-size:1.4rem;font-weight:800;color:#ffffff;letter-spacing:-0.02em;'>Content AI Studio</span>"
        "</div>"
        "<div style='font-size:0.65rem;font-weight:600;letter-spacing:0.2em;text-transform:uppercase;"
        "color:#a5b4fc;margin-top:2px;'>Enterprise AI Platform</div></div>",
        unsafe_allow_html=True
    )
    st.sidebar.divider()
    st.sidebar.markdown("<p style='font-size:0.8rem;color:#a5b4fc;font-weight:600;letter-spacing:0.05em;text-transform:uppercase;margin-bottom:2px;'>Sign In</p>", unsafe_allow_html=True)
    username_input = st.sidebar.text_input("Username", placeholder="Enter username", key="login_username")
    password_input = st.sidebar.text_input("Password", type="password", placeholder="Enter password", key="login_password")
    if st.sidebar.button("Sign In", type="primary", use_container_width=True):
        user = db.query(User).filter(User.username == username_input).first()
        if user and (user.is_active is None or user.is_active) and verify_password(password_input, user.password_hash):
            st.session_state.user = {"username": user.username, "role": user.role}
            log_event(db, "login", user.username, f"User '{user.username}' logged in (role: {user.role})")
            notify_deferred("login", f"Welcome back, {user.username}! Signed in as {role_label(user.role)}.")
            st.rerun()
        elif user and not (user.is_active is None or user.is_active):
            st.sidebar.error("Your account has been deactivated. Contact an Admin.")
        else:
            st.sidebar.error("Invalid credentials.")

    # --- Main Area: Branding & Feature Overview ---
    _logo = _logo_b64()
    st.markdown(
        "<div style='text-align:center;padding:0 0 1.5rem 0;'>"
        "<div style='display:inline-block;background:#eef2ff;color:#4338ca;"
        "font-size:0.72rem;font-weight:700;letter-spacing:0.18em;text-transform:uppercase;"
        "padding:4px 14px;border-radius:20px;margin-bottom:1rem;'>Enterprise AI Platform</div>"
        "<div style='margin-bottom:0.5rem;'>"
        "<div style='position:relative;display:inline-block;'>"
        f"<img src='data:image/png;base64,{_logo}' style='position:absolute;right:calc(100% + 8px);top:50%;transform:translateY(-50%);height:52px;width:auto;'>"
        "<span style='font-size:2.8rem;font-weight:800;letter-spacing:-0.03em;color:#111827;'>Content AI Studio</span>"
        "</div></div>"
        "<p style='font-size:1.05rem;color:#6b7280;max-width:600px;margin:auto;line-height:1.75;'>"
        "The centralised platform for AI-powered eLearning content creation. "
        "Manage prompts as code, enforce brand consistency, and generate production-ready courses at scale."
        "</p></div>",
        unsafe_allow_html=True
    )

    st.markdown("<hr style='border:none;border-top:1px solid #e4e7ef;margin:1.5rem 0;'>",
                unsafe_allow_html=True)

    def _feat_card(icon, title, desc):
        return (f"<div style='background:#ffffff;border:1px solid #e4e7ef;border-radius:10px;"
                f"padding:1rem 1.1rem;height:100%;'>"
                f"<div style='font-size:1.5rem;margin-bottom:0.4rem;'>{icon}</div>"
                f"<div style='font-weight:700;color:#111827;font-size:0.9rem;margin-bottom:4px;'>{title}</div>"
                f"<div style='color:#6b7280;font-size:0.82rem;line-height:1.55;'>{desc}</div></div>")

    feat1, feat2, feat3 = st.columns(3)
    feat1.markdown(_feat_card("📚","Prompt Library","Version-controlled prompt assets with team collaboration and performance tracking."), unsafe_allow_html=True)
    feat2.markdown(_feat_card("⚙️","AI Course Generation","Generate lessons, assessments, and all components from a single Blueprint."), unsafe_allow_html=True)
    feat3.markdown(_feat_card("✏️","Block-Level Editing","Edit, review, and regenerate individual content blocks without redoing the course."), unsafe_allow_html=True)
    st.markdown("<div style='margin-top:0.75rem;'></div>", unsafe_allow_html=True)
    feat4, feat5, feat6 = st.columns(3)
    feat4.markdown(_feat_card("📊","Analytics","Track prompt quality, review scores, and system events in real-time dashboards."), unsafe_allow_html=True)
    feat5.markdown(_feat_card("📄","Multi-Format Export","Export to Markdown, JSON, HTML, or Word DOCX — ready for any LMS."), unsafe_allow_html=True)
    feat6.markdown(_feat_card("🔍","Full Observability","Every action is logged — generations, reviews, exports — for complete audit trails."), unsafe_allow_html=True)
    db.close()


# =============================================================================
# Project & Course Selection Pages
# =============================================================================

def _sidebar_brand(user_name: str, user_role: str):
    """Render consistent brand + user pill in sidebar (reused across pages)."""
    _logo = _logo_b64()
    st.sidebar.markdown(
        "<div style='text-align:center;padding:1rem 0 0.5rem 0;'>"
        "<div style='position:relative;display:inline-block;margin-bottom:2px;'>"
        f"<img src='data:image/png;base64,{_logo}' style='position:absolute;right:calc(100% + 5px);top:50%;transform:translateY(-50%);height:32px;width:auto;'>"
        "<span style='font-size:1.4rem;font-weight:800;color:#ffffff;letter-spacing:-0.02em;'>Content AI Studio</span>"
        "</div>"
        "<div style='font-size:0.65rem;font-weight:600;letter-spacing:0.2em;text-transform:uppercase;"
        "color:#a5b4fc;margin-top:2px;'>Enterprise AI Platform</div></div>",
        unsafe_allow_html=True
    )
    _role_display_label = role_label(user_role)
    _role_color_map = {"admin": "#7c3aed", "reviewer": "#0f766e", "author": "#4338ca"}
    role_color = _role_color_map.get(user_role, "#4338ca")
    st.sidebar.markdown(
        f"<div style='background:rgba(255,255,255,0.13);border:1px solid rgba(165,180,252,0.25);border-radius:8px;padding:8px 12px;margin:6px 0;'>"
        f"<div style='font-size:0.72rem;color:#c7d2fe;text-transform:uppercase;letter-spacing:.06em;font-weight:600;margin-bottom:2px;'>Signed in as</div>"
        f"<div style='color:#ffffff;font-weight:700;font-size:0.9rem;'>{user_name}</div>"
        f"<div style='display:inline-block;background:{role_color};color:#ffffff;font-size:0.68rem;"
        f"font-weight:700;padding:2px 8px;border-radius:4px;text-transform:uppercase;"
        f"letter-spacing:.05em;margin-top:4px;'>{_role_display_label}</div>"
        f"</div>",
        unsafe_allow_html=True
    )
    st.sidebar.divider()


def project_dashboard_page():
    """Project selection page — shown after login, before course selection."""
    inject_premium_style()
    user_role = st.session_state.user["role"]
    user_name = st.session_state.user["username"]

    _sidebar_brand(user_name, user_role)

    db = SessionLocal()

    # ── Admin: create project panel ───────────────────────────────────────────
    if user_role == "admin":
        with st.sidebar.expander("➕ New Project", expanded=False):
            with st.form("create_project_form"):
                proj_name   = st.text_input("Project Name *")
                proj_client = st.text_input("Client Name")
                proj_desc   = st.text_area("Description", height=60)
                if st.form_submit_button("Create Project", use_container_width=True):
                    if proj_name.strip():
                        db.add(Project(name=proj_name.strip(), description=proj_desc,
                                       client_name=proj_client, created_by=user_name))
                        db.commit()
                        st.toast(f"✅ Project '{proj_name}' created!")
                        st.rerun()
                    else:
                        st.error("Project name is required.")

    if st.sidebar.button("🚪 Sign Out", use_container_width=True):
        st.session_state.user = None
        st.rerun()

    # ── Main area ─────────────────────────────────────────────────────────────
    st.markdown(
        "<div style='padding:2rem 0 1rem 0;'>"
        "<h1 style='font-size:2rem;font-weight:800;color:#111827;margin-bottom:0.25rem;'>Project Dashboard</h1>"
        "<p style='color:#374151;font-size:0.95rem;'>Select a project to continue.</p></div>",
        unsafe_allow_html=True
    )

    projects = _get_user_projects(db, user_name, user_role)

    if not projects:
        if user_role == "admin":
            st.info("No projects yet. Create your first project using the sidebar panel.")
        else:
            st.info("No projects assigned to you. Contact an Admin to be added to a project.")
        db.close()
        return

    cols_per_row = 3
    for i in range(0, len(projects), cols_per_row):
        row_cols = st.columns(cols_per_row, gap="medium")
        for j, proj in enumerate(projects[i : i + cols_per_row]):
            course_count = db.query(Course).filter(Course.project_id == proj.id).count()
            with row_cols[j]:
                client_line = f"<div style='font-size:0.75rem;color:#4f46e5;font-weight:600;margin-bottom:4px;'>Client: {proj.client_name}</div>" if proj.client_name else ""
                desc_line   = f"<div style='font-size:0.82rem;color:#1f2937;margin-bottom:8px;line-height:1.5;'>{(proj.description or '')[:90]}{'…' if proj.description and len(proj.description) > 90 else ''}</div>" if proj.description else ""
                st.markdown(
                    f"<div style='background:#ffffff;border:1.5px solid #d1d5db;border-radius:12px;"
                    f"padding:1.2rem;margin-bottom:0.5rem;box-shadow:0 1px 4px rgba(0,0,0,0.07);'>"
                    f"<div style='font-weight:700;font-size:1.05rem;color:#111827;margin-bottom:4px;'>{proj.name}</div>"
                    f"{client_line}{desc_line}"
                    f"<div style='font-size:0.72rem;color:#374151;font-weight:500;'>{course_count} course{'s' if course_count != 1 else ''}</div>"
                    f"</div>",
                    unsafe_allow_html=True
                )
                if st.button("Open →", key=f"proj_open_{proj.id}", use_container_width=True):
                    st.session_state.selected_project_id   = proj.id
                    st.session_state.selected_project_name = proj.name
                    st.session_state.pop("selected_course_id", None)
                    st.session_state.pop("selected_course_name", None)
                    st.rerun()

                # Admin and Lead (within their assigned scope): Edit & Delete controls
                _lead_on_proj = _is_lead_for_project(db, user_name, proj.id)
                if user_role == "admin" or _lead_on_proj:
                    _edit_key = f"proj_edit_{proj.id}"
                    _del_key  = f"proj_del_confirm_{proj.id}"

                    _a1, _a2 = st.columns(2)
                    if _a1.button("✏️ Edit", key=f"proj_edit_btn_{proj.id}", use_container_width=True):
                        st.session_state[_edit_key] = not st.session_state.get(_edit_key, False)
                        st.session_state.pop(_del_key, None)
                        st.rerun()
                    # Delete: admin only (Lead cannot delete projects)
                    if user_role == "admin":
                        if _a2.button("🗑️ Delete", key=f"proj_del_btn_{proj.id}", use_container_width=True):
                            st.session_state[_del_key] = not st.session_state.get(_del_key, False)
                            st.session_state.pop(_edit_key, None)
                            st.rerun()

                    # Inline edit form
                    if st.session_state.get(_edit_key):
                        with st.form(f"edit_proj_form_{proj.id}"):
                            new_name   = st.text_input("Project Name", value=proj.name)
                            new_client = st.text_input("Client Name", value=proj.client_name or "")
                            new_desc   = st.text_area("Description", value=proj.description or "", height=70)
                            _s1, _s2 = st.columns(2)
                            if _s1.form_submit_button("💾 Save", use_container_width=True):
                                if new_name.strip():
                                    proj.name        = new_name.strip()
                                    proj.client_name = new_client.strip() or None
                                    proj.description = new_desc.strip() or None
                                    db.commit()
                                    log_event(db, "project_updated", user_name,
                                              f"Project '{proj.name}' metadata updated")
                                    st.session_state.pop(_edit_key, None)
                                    st.toast("✅ Project updated!")
                                    st.rerun()
                                else:
                                    st.error("Project name cannot be empty.")
                            if _s2.form_submit_button("Cancel", use_container_width=True):
                                st.session_state.pop(_edit_key, None)
                                st.rerun()

                    # Two-step delete confirm
                    if st.session_state.get(_del_key):
                        st.warning(f"Delete **{proj.name}**? This will archive the project and all its courses.")
                        _d1, _d2 = st.columns(2)
                        if _d1.button("Confirm Delete", key=f"proj_del_ok_{proj.id}",
                                      type="primary", use_container_width=True):
                            proj.is_active = False
                            db.commit()
                            log_event(db, "project_deleted", user_name,
                                      f"Project '{proj.name}' archived")
                            st.session_state.pop(_del_key, None)
                            st.toast(f"🗑️ Project '{proj.name}' deleted.")
                            st.rerun()
                        if _d2.button("Cancel", key=f"proj_del_cancel_{proj.id}",
                                      use_container_width=True):
                            st.session_state.pop(_del_key, None)
                            st.rerun()

                    # User assignment expander (admin and Lead within their scope)
                    _can_manage_users = (user_role == "admin") or _is_lead_for_project(db, user_name, proj.id)
                    all_non_admin = db.query(User).filter(User.role != "admin", User.is_active == True).all()
                    if all_non_admin and _can_manage_users:
                        with st.expander("👥 Manage Users"):
                            existing = {a.username for a in db.query(ProjectUserAssignment).filter(
                                ProjectUserAssignment.project_id == proj.id).all()}
                            for usr in all_non_admin:
                                is_assigned = usr.username in existing
                                _usr_label = f"{usr.username} ({role_label(usr.role)})"
                                new_val = st.checkbox(
                                    _usr_label, value=is_assigned,
                                    key=f"asgn_{proj.id}_{usr.username}"
                                )
                                if new_val and not is_assigned:
                                    db.add(ProjectUserAssignment(project_id=proj.id, username=usr.username))
                                    db.commit()
                                    st.toast(f"✅ {usr.username} added to project.")
                                elif not new_val and is_assigned:
                                    db.query(ProjectUserAssignment).filter(
                                        ProjectUserAssignment.project_id == proj.id,
                                        ProjectUserAssignment.username == usr.username
                                    ).delete()
                                    db.commit()
                                    st.toast(f"🔒 {usr.username} removed from project.")

    db.close()


def course_selection_page():
    """Course selection page — shown after project selection, before workspace."""
    inject_premium_style()
    user_role = st.session_state.user["role"]
    user_name = st.session_state.user["username"]
    proj_id   = st.session_state.selected_project_id
    proj_name = st.session_state.selected_project_name

    _sidebar_brand(user_name, user_role)

    db = SessionLocal()

    st.sidebar.markdown(
        f"<div style='background:rgba(255,255,255,0.12);border:1px solid rgba(165,180,252,0.22);border-radius:8px;padding:8px 12px;"
        f"margin:4px 0;font-size:0.78rem;color:#c7d2fe;font-weight:500;'>"
        f"📁 Project: <strong style='color:#ffffff;font-weight:700;'>{proj_name}</strong></div>",
        unsafe_allow_html=True
    )
    if st.sidebar.button("← Back to Projects", use_container_width=True):
        st.session_state.pop("selected_project_id", None)
        st.session_state.pop("selected_project_name", None)
        st.rerun()

    # Admin and Lead can add/manage courses within assigned project
    _lead_here = _is_lead_for_project(db, user_name, proj_id)
    _can_manage_courses = (user_role == "admin") or _lead_here
    if _can_manage_courses:
        with st.sidebar.expander("➕ New Course", expanded=False):
            with st.form("create_course_form"):
                c_name = st.text_input("Course Name *")
                c_desc = st.text_area("Description", height=60)
                if st.form_submit_button("Create Course", use_container_width=True):
                    if c_name.strip():
                        db.add(Course(project_id=proj_id, name=c_name.strip(),
                                      description=c_desc, created_by=user_name))
                        db.commit()
                        st.toast(f"✅ Course '{c_name}' created!")
                        st.rerun()
                    else:
                        st.error("Course name is required.")

    if st.sidebar.button("🚪 Sign Out", use_container_width=True):
        st.session_state.user = None
        st.rerun()

    # ── Main area ─────────────────────────────────────────────────────────────
    st.markdown(
        f"<div style='padding:2rem 0 1rem 0;'>"
        f"<div style='font-size:0.78rem;color:#4b5563;font-weight:700;text-transform:uppercase;"
        f"letter-spacing:.06em;margin-bottom:4px;'>Project: {proj_name}</div>"
        f"<h1 style='font-size:2rem;font-weight:800;color:#111827;margin-bottom:0.25rem;'>Select Course</h1>"
        f"<p style='color:#374151;font-size:0.95rem;'>Choose a course to enter the workspace.</p></div>",
        unsafe_allow_html=True
    )

    courses = db.query(Course).filter(
        Course.project_id == proj_id, Course.is_active == True
    ).order_by(Course.created_at.desc()).all()

    if not courses:
        if user_role == "admin":
            st.info("No courses in this project yet. Create one using the sidebar panel.")
        else:
            st.info("No courses available. Ask an Admin or Lead to create courses for this project.")
        db.close()
        return

    cols_per_row = 3
    for i in range(0, len(courses), cols_per_row):
        row_cols = st.columns(cols_per_row, gap="medium")
        for j, course in enumerate(courses[i : i + cols_per_row]):
            with row_cols[j]:
                desc_line = f"<div style='font-size:0.82rem;color:#1f2937;margin-bottom:8px;line-height:1.5;'>{(course.description or '')[:90]}{'…' if course.description and len(course.description) > 90 else ''}</div>" if course.description else ""
                st.markdown(
                    f"<div style='background:#ffffff;border:1.5px solid #d1d5db;border-radius:12px;"
                    f"padding:1.2rem;margin-bottom:0.5rem;box-shadow:0 1px 4px rgba(0,0,0,0.07);'>"
                    f"<div style='font-weight:700;font-size:1.05rem;color:#111827;margin-bottom:4px;'>{course.name}</div>"
                    f"{desc_line}</div>",
                    unsafe_allow_html=True
                )
                if st.button("Enter Workspace →", key=f"course_open_{course.id}", use_container_width=True):
                    # Clear any stale course-scoped session state from previous course
                    _prev_crs = st.session_state.get("selected_course_id")
                    if _prev_crs and _prev_crs != course.id:
                        st.session_state.pop("active_cdd_id", None)
                        st.session_state.pop("active_blueprint_id", None)
                    st.session_state.selected_course_id   = course.id
                    st.session_state.selected_course_name = course.name
                    # Admin/Lead → Style tab; ID → CDD (ID does not manage Style)
                    st.session_state.nav_page = "CDD" if user_role == "author" else "Style"
                    st.rerun()

                # Admin and Lead can edit courses
                _lead_this_course = _is_lead_for_course(db, user_name, course.id)
                _can_edit_course  = (user_role == "admin") or _lead_this_course
                if _can_edit_course:
                    _edit_key = f"course_edit_{course.id}"
                    _del_key  = f"course_del_confirm_{course.id}"
                    _umu_key  = f"course_manage_users_{course.id}"

                    _a1, _a2, _a3 = st.columns(3)
                    if _a1.button("✏️ Edit", key=f"course_edit_btn_{course.id}", use_container_width=True):
                        st.session_state[_edit_key] = not st.session_state.get(_edit_key, False)
                        st.session_state.pop(_del_key, None); st.session_state.pop(_umu_key, None)
                        st.rerun()
                    if _a2.button("👥 Users", key=f"course_users_btn_{course.id}", use_container_width=True):
                        st.session_state[_umu_key] = not st.session_state.get(_umu_key, False)
                        st.session_state.pop(_edit_key, None); st.session_state.pop(_del_key, None)
                        st.rerun()
                    if user_role == "admin":
                        if _a3.button("🗑️ Delete", key=f"course_del_btn_{course.id}", use_container_width=True):
                            st.session_state[_del_key] = not st.session_state.get(_del_key, False)
                            st.session_state.pop(_edit_key, None); st.session_state.pop(_umu_key, None)
                            st.rerun()

                    # Inline edit form
                    if st.session_state.get(_edit_key):
                        with st.form(f"edit_course_form_{course.id}"):
                            new_name = st.text_input("Course Name", value=course.name)
                            new_desc = st.text_area("Description", value=course.description or "", height=70)
                            _s1, _s2 = st.columns(2)
                            if _s1.form_submit_button("💾 Save", use_container_width=True):
                                if new_name.strip():
                                    course.name        = new_name.strip()
                                    course.description = new_desc.strip() or None
                                    db.commit()
                                    log_event(db, "course_updated", user_name,
                                              f"Course '{course.name}' metadata updated")
                                    st.session_state.pop(_edit_key, None)
                                    st.toast("✅ Course updated!")
                                    st.rerun()
                                else:
                                    st.error("Course name cannot be empty.")
                            if _s2.form_submit_button("Cancel", use_container_width=True):
                                st.session_state.pop(_edit_key, None)
                                st.rerun()

                    # Course-level Manage Users panel
                    if st.session_state.get(_umu_key):
                        with st.expander(f"👥 Manage Users — {course.name}", expanded=True):
                            all_non_admin_crs = db.query(User).filter(
                                User.role != "admin", User.is_active == True
                            ).all()
                            _proj_assigned = {a.username for a in db.query(ProjectUserAssignment).filter(
                                ProjectUserAssignment.project_id == proj_id).all()}
                            _crs_assigned = {a.username for a in db.query(CourseUserAssignment).filter(
                                CourseUserAssignment.course_id == course.id).all()}
                            if not all_non_admin_crs:
                                st.info("No non-admin users in the system.")
                            else:
                                st.caption("Users assigned to the project are shown. Check to grant course-level access.")
                                for _cu in all_non_admin_crs:
                                    _in_proj = _cu.username in _proj_assigned
                                    _in_crs  = _cu.username in _crs_assigned
                                    _crs_chk = st.checkbox(
                                        f"{_cu.username} ({role_label(_cu.role)})"
                                        + (" ✓ project" if _in_proj else ""),
                                        value=_in_crs,
                                        key=f"crs_asgn_{course.id}_{_cu.username}"
                                    )
                                    if _crs_chk and not _in_crs:
                                        db.add(CourseUserAssignment(course_id=course.id, username=_cu.username))
                                        db.commit()
                                        st.toast(f"✅ {_cu.username} added to course.")
                                        st.rerun()
                                    elif not _crs_chk and _in_crs:
                                        db.query(CourseUserAssignment).filter(
                                            CourseUserAssignment.course_id == course.id,
                                            CourseUserAssignment.username == _cu.username
                                        ).delete()
                                        db.commit()
                                        st.toast(f"🔒 {_cu.username} removed from course.")
                                        st.rerun()

                    # Two-step delete confirm (admin only)
                    if st.session_state.get(_del_key):
                        st.warning(f"Delete **{course.name}**? All content inside this course will be archived.")
                        _d1, _d2 = st.columns(2)
                        if _d1.button("Confirm Delete", key=f"course_del_ok_{course.id}",
                                      type="primary", use_container_width=True):
                            course.is_active = False
                            db.commit()
                            log_event(db, "course_deleted", user_name,
                                      f"Course '{course.name}' archived")
                            st.session_state.pop(_del_key, None)
                            st.toast(f"🗑️ Course '{course.name}' deleted.")
                            st.rerun()
                        if _d2.button("Cancel", key=f"course_del_cancel_{course.id}",
                                      use_container_width=True):
                            st.session_state.pop(_del_key, None)
                            st.rerun()

    db.close()


# =============================================================================
# Phase 3 parser override imports — placed after local definitions so they
# take precedence. Phase 4 will remove the legacy local code above.
# =============================================================================

from promptops_app.parsers.cdd_parser import (
    parse_sections_from_text, parse_cdd_flat,
    _strip_ui_hidden_text, _rebuild_cdd_full_content,
    extract_cdd_summary, extract_blueprint_summary,
    _CDD_UI_HIDDEN_PATTERNS, _UI_STRIP_PATTERNS,
)

from promptops_app.parsers.blueprint_parser import (
    _is_bp_section_hidden, _BP_UI_HIDDEN_SECTION_KEYS,
    get_blueprint_prompts,
    parse_items_from_section, patch_item_in_section, regen_single_item,
    parse_blueprint_components, build_component_generation_prompt,
    is_component_type_module_level, is_component_type_course_level,
    _COMPONENT_TYPES_LIST,
)


