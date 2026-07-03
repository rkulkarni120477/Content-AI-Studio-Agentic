# Prompt System Consolidation — Implementation Plan

Merging the standalone Prompt Library (`pl_*` tables) with the Content AI Studio
generation pipeline (`prompts` / `prompt_versions` / `prompt_fixings` /
`user_prompt_preferences`) into one production-grade prompt system.

**Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

This document is the single source of truth for this initiative. Each work
session should: (1) read "Current state" + the phase in progress, (2) update
checkboxes as work lands, (3) append an entry to the **Progress Log** at the
bottom before ending the session. Do not start a phase whose predecessors are
not checked off — dependencies are ordered deliberately (schema before data,
data before wiring, wiring before governance).

---

## 0. Context for a fresh session

**Two systems being merged today:**

| | Standalone Prompt Library | Pipeline prompt registry |
|---|---|---|
| Tables | `pl_prompts`, `pl_prompt_versions`, `pl_prompt_teams`, `pl_prompt_tags`, `pl_prompt_variables`, `pl_attachments`, `pl_teams`, `pl_prompt_requests`, `pl_reviews`, `pl_audit_events` | `prompts`, `prompt_versions`, `prompt_fixings`, `user_prompt_preferences` |
| Defined in | `promptops_app/pl_models.py` | `promptops_app/database.py` |
| Consumed by | `app/api/v1/routers/prompt_library.py`, `promptops_app/services/prompt_library_service.py` | `promptops_app/repositories/prompt_repository.py`, `promptops_app/prompts/prompt_loader.py`, `promptops_app/prompts/prompt_builder.py` |
| Prompt shape | single `content: Text` field | `system_prompt` + `user_prompt_template` pair |
| PK type | `String(36)` UUID | `Integer` |
| Purpose | browse/share/rate reusable prompt snippets | drive live CDD/Blueprint/Generate/Style LLM calls |

**Known gaps in the pipeline registry today (to be closed as part of this work, not separately):**
- Only `cdd_generation` and `blueprint_generation` actually call `build_prompt(..., db=db)` from live FastAPI routers (`app/api/v1/routers/cdd.py:242`, `blueprints.py:181`). `style_understanding`, `content_generation`, `quiz_generation` templates exist in `_REGISTRY` (`promptops_app/prompts/prompt_loader.py`) but the live routers call hardcoded Python constants from `promptops_app/prompt_templates.py` directly.
- `resolve_fixed_prompt()` (course→cluster→project→global scope resolution, `promptops_app/repositories/prompt_repository.py:133`) is fully implemented but only called from the legacy Streamlit UI (`promptops_app/ui/generation_controls.py:508`) — never from the FastAPI routers the React frontend hits.
- Blueprint student/teacher is one `Prompt` row gated by a `teacher_mode` variable flag, not two independently versioned assets — a template edit that forgets to guard the teacher-only block can leak teacher content into student output.
- No shared, versioned "fragment" layer — persona/tone, guardrails, output-contract text are duplicated string literals inside each hardcoded system prompt.

**Target prompt taxonomy** (drives what rows get seeded in Phase 3):

| Entity/Stage | LLM task(s) | Prompt uniqueness | Uses persona/tone fragment? |
|---|---|---|---|
| Cluster | Cluster Definition Assistant, Cross-Course Consistency Synthesizer | 2 unique prompts (optional / backlog) | No — Style doesn't exist yet |
| Course | Course Scaffolding Assistant, Cluster-Fit Classifier | 2 unique prompts (optional / backlog) | No — Style doesn't exist yet |
| Style | Style Extraction (reference docs → tone/voice profile) | 1 unique prompt | N/A — this stage produces the fragment |
| CDD | Course structural planning | 1 unique prompt | Yes |
| Blueprint | Module detailing | 2 unique prompts (student, teacher — never merged) | Yes |
| Generate | Content authoring | N unique prompts, 1 per component type (lesson / assessment item / interactive) | Yes |

---

## Phase 1 — Schema foundation (additive, zero-downtime)

Goal: land the new columns/tables without touching any existing read/write path.

- [ ] Add to `pl_prompts`: `component_type` (nullable string: `style|cdd|blueprint|generate|null`), `variant` (nullable string: e.g. `student`/`teacher`/`lesson`/`assessment`/`interactive`), `is_default` (bool, default `False`), `system_prompt` (nullable `Text`), `governance_tier` (string: `pipeline|library`, default `library`)
- [ ] Add `system_prompt` (nullable `Text`) to `pl_prompt_versions`; keep existing `content` column as the user/main body (library prompts leave `system_prompt` null)
- [ ] New table `pl_prompt_fixings` — same shape as `prompt_fixings` but `prompt_id: String(36) FK -> pl_prompts.id`
- [ ] New table `pl_user_prompt_preferences` — same shape as `user_prompt_preferences` but `prompt_id: String(36) FK -> pl_prompts.id`
- [ ] New tables `pl_fragments` (`fragment_key` PK, `active_version_id` FK) and `pl_fragment_versions` (versioned content, same append-only pattern as `pl_prompt_versions`) — see Phase 6
- [ ] Alembic migration file(s) under `migrations/versions/` — additive only, no drops, no column type changes to existing tables in this phase
- [ ] Run `alembic upgrade head` against a scratch/staging DB and confirm `init_db()` idempotency still holds (rerun startup twice, no errors)

**Acceptance:** migration applies cleanly to staging; existing `prompts`/`prompt_versions`/`prompt_fixings`/`pl_*` tables and all current endpoints continue to work unmodified (this phase is purely additive).

---

## Phase 2 — Data migration (existing pipeline prompts → `pl_prompts`)

Goal: copy today's ~6 pipeline templates and their fixings/preferences into the new columns/tables. Follow the same idempotent, dry-run-first pattern as the existing `scripts/migrate_prompt_library.py`.

- [ ] Write `scripts/migrate_pipeline_prompts_to_pl.py`:
  - [ ] For each row in `prompts`: create a `pl_prompts` row with `id=uuid4()`, `title=name`, `content=<user_prompt_template of active version>`, `system_prompt=<system_prompt of active version>`, `component_type`, `is_default=is_default`, `governance_tier="pipeline"`, `visibility` set to an internal/restricted value (not public library visibility)
  - [ ] For each `PromptVersion` row: create a corresponding `pl_prompt_versions` row (map `version` → `version_number`, `change_reason` → `note`, keep `created_by`/`created_at`)
  - [ ] For each `PromptFixing` row: create a `pl_prompt_fixings` row with retyped `prompt_id`
  - [ ] For each `UserPromptPreference` row: create a `pl_user_prompt_preferences` row with retyped `prompt_id`
  - [ ] Idempotent: skip rows whose target PK/natural-key already exists (same pattern as `_insert()` in the existing migration script)
  - [ ] `--dry-run` flag that reports counts without committing
- [ ] Run dry-run against staging, review counts and any unmatched-user warnings
- [ ] Run for real against staging, spot-check 2-3 rows manually (compare rendered prompt output old path vs new path — must be byte-identical)
- [ ] Do **not** run against production until Phase 5 is code-complete and tested (no consumer of the new rows exists yet before that)

**Acceptance:** every existing `Prompt`/`PromptVersion`/`PromptFixing`/`UserPromptPreference` row has an equivalent `pl_*` row; rendered output from old and new rows matches exactly for all 6 templates.

---

## Phase 3 — Seed the full taxonomy

Goal: every row in the taxonomy table above exists as a `pl_prompts` entry with `is_default=True`, even the ones not yet wired to a live call site.

- [ ] `style` / no variant — port `promptops_app/prompt_templates.py` style-understanding prompt content (currently only the file-based `_REGISTRY["style_understanding"]` template + any hardcoded fallback) as the seeded default
- [ ] `cdd` / no variant — already migrated in Phase 2, confirm `is_default=True`
- [ ] `blueprint` / `variant=student` — split from today's single `blueprint_generation` row: port `BLUEPRINT_SYSTEM_PROMPT` (`promptops_app/prompt_templates.py:307`)
- [ ] `blueprint` / `variant=teacher` — new independent row: port `TEACHER_BLUEPRINT_SYSTEM_PROMPT` (`promptops_app/prompt_templates.py:588`) — **not** a shared row with a mode flag
- [ ] `generate` / `variant=lesson` — port `LESSON_WITH_CONTEXT_SYSTEM`/`_USER` (`promptops_app/prompt_templates.py:975`)
- [ ] `generate` / `variant=assessment` — port the quiz/assessment prompt (`quiz_generation` registry entry)
- [ ] `generate` / `variant=interactive` — **new**, does not exist today; draft from scratch (scope: interactive activity authoring, distinct output contract from prose lesson content)
- [ ] Backlog only, not required for this initiative's completion — flag as `governance_tier=pipeline`, `is_default=True`, but leave unimplemented in routers until explicitly prioritized:
  - [ ] `cluster` / `variant=definition_assistant`
  - [ ] `cluster` / `variant=consistency_synthesizer`
  - [ ] `course` / `variant=scaffolding_assistant`
  - [ ] `course` / `variant=cluster_fit_classifier`
- [ ] Also port each template's declared variables into `pl_prompt_variables` (replacing the static `required_vars`/`optional_vars` lists in `_REGISTRY`)

**Acceptance:** `SELECT component_type, variant, is_default FROM pl_prompts WHERE governance_tier='pipeline'` returns exactly one default row per taxonomy line (Cluster/Course rows included but flagged backlog).

---

## Phase 4 — Repository & loader rewire

Goal: `promptops_app/prompts/prompt_builder.py`, `prompt_loader.py`, and `promptops_app/repositories/prompt_repository.py` read/write `PLPrompt`/`PLPromptVersion` instead of `Prompt`/`PromptVersion`. `resolve_fixed_prompt()`'s logic is preserved verbatim, only its target model changes.

- [ ] `prompt_repository.py`: repoint all queries at `PLPrompt`/`PLPromptVersion`/`PLPromptFixing`/`PLUserPromptPreference` (rename classes accordingly in `pl_models.py` if not already present)
- [ ] `prompt_loader._from_db()`: query by `(component_type, variant)` instead of `Prompt.name`; add a `resolve_fixed_prompt()` call ahead of the plain lookup so scope-fixed prompts win
- [ ] `prompt_builder.build_prompt()`: signature gains optional `project_id`/`cluster_id`/`course_id`/`variant` kwargs, threaded through to `resolve_fixed_prompt()`
- [ ] `deploy_new_version()`: update to write `pl_prompt_versions` rows; **do not** auto-activate for `governance_tier="pipeline"` rows — see Phase 7 (approval gate)
- [ ] Unit tests: `tests/unit/` — cover scope resolution priority (course > cluster > project > global > default), fallback-to-file behavior when DB lookup misses, variable-substitution parity with the old `_REGISTRY` dict

**Acceptance:** existing CDD/Blueprint generation calls produce byte-identical output through the new path vs. the old path, verified against Phase 2's spot-checked rows. All new unit tests pass.

---

## Phase 5 — Router wiring (close the "only 2 of 6 stages are DB-backed" gap)

Goal: every stage in the taxonomy that has a live endpoint resolves its prompt through the unified `build_prompt()` + `resolve_fixed_prompt()` path.

- [ ] `app/api/v1/routers/cdd.py` — update `build_prompt("cdd_generation", ...)` call to pass `project_id`/`cluster_id`/`course_id` for scope resolution (logic already calls `build_prompt`, just needs the new kwargs)
- [ ] `app/api/v1/routers/blueprints.py` — split the single call into `variant="teacher" if request_body.teacher_mode else "student"`; remove the `teacher_mode`/`student_mode` variable-flag branching inside the template itself
- [ ] `app/api/v1/routers/generations.py` — currently does not call `build_prompt` at all; wire it to resolve `variant` from `component_type` (lesson/assessment/interactive) and call `build_prompt(..., db=db)` with scope kwargs
- [ ] Style router (wherever `style_understanding` generation is triggered) — same treatment
- [ ] Keep the existing hardcoded-constant fallback (`except Exception: ...`) in every router as the last-resort safety net — do not remove it, it protects live generation if the DB path errors
- [ ] Integration tests: one per stage, asserting the DB-resolved prompt is used when a `pl_prompt_fixings` row exists at each scope level, and the correct `variant` is selected for blueprint/generate

**Acceptance:** `grep -rn "CDD_SYSTEM_PROMPT\|BLUEPRINT_SYSTEM_PROMPT\|TEACHER_BLUEPRINT_SYSTEM_PROMPT\|LESSON_WITH_CONTEXT_SYSTEM" app/api/v1/routers/` shows these constants only inside `except` fallback blocks, never as the primary path. Manually verify in a staging environment: set a course-level `pl_prompt_fixings` override and confirm generation picks it up.

---

## Phase 6 — Shared fragment library

Goal: persona/tone, guardrails, context-header, and output-contract become versioned, composable entities instead of duplicated string literals.

- [ ] Populate `pl_fragments`/`pl_fragment_versions` (from Phase 1) with: `guardrails`, `context_header`, `persona_tone` (source: active Style's `generated_summary`, not static text), `output_contract_json`, `output_contract_markdown`, `regenerate_wrapper`
- [ ] New composer function `promptops_app/prompts/fragment_composer.py`: `compose(stage_template, fragments: list[str], context: dict) -> str` — assembles final system prompt as `guardrails + context_header + persona_tone(if applicable) + stage_unique_template + output_contract`
- [ ] Migrate `PERSONA_PREFIX_TEMPLATE` and `DEFAULT_STYLE_GUIDE` (`promptops_app/prompt_templates.py:6,16`) into `pl_fragments` rows; keep the Python constants only as the file-fallback tier
- [ ] Migrate `build_style_context()`'s output to flow through the `persona_tone` fragment slot rather than being string-concatenated ad hoc per router
- [ ] Update Phase 5's router changes to call the composer instead of inline f-string concatenation (e.g. replace `extra_block = f"**ACTIVE STYLE:**\n{style_context}\n\n{extra_block}"` patterns)

**Acceptance:** editing the `guardrails` fragment once updates output across CDD/Blueprint/Generate without touching per-stage templates. Regression test confirms composed output for each stage matches Phase 5's baseline before the fragment refactor.

---

## Phase 7 — Governance, permissions, and approval workflow

Goal: pipeline-critical prompts (`governance_tier="pipeline"`) cannot be edited or activated with the same ease as library content.

- [ ] New permission `prompt.pipeline.edit` (distinct from existing `cluster_prompt.create`/`prompt_library.*`), admin/prompt-engineer role only — add to `app/core/permissions.py` / `promptops_app/auth/permission_matrix.py`
- [ ] Enforce at the API layer: any write to a `pl_prompts` row with `governance_tier="pipeline"` requires `prompt.pipeline.edit`; library-tier rows keep today's permission checks unchanged
- [ ] Add a `workflow_state` column to `pl_prompt_versions` for pipeline-tier rows (`draft → in_review → approved → active`), reusing the same state machine already used for `Block.workflow_state` — a new version does not become the active/served version until approved
- [ ] `deploy_new_version()` (Phase 4) respects this: pipeline-tier calls create a `draft` version, do not flip `is_active`; a separate approval endpoint flips it
- [ ] Library-tier rows keep instant-activate (no approval gate) — this workflow is additive only for `governance_tier="pipeline"`

**Acceptance:** a non-admin user cannot create an active version of a pipeline-tier prompt via the API (403); an admin can submit a draft, and a second admin (or the same, per whatever approval policy is chosen) must approve before it serves live traffic.

---

## Phase 8 — Audit unification

- [ ] Route all `pl_prompts`/`pl_prompt_versions` writes (both tiers) through the existing `pl_audit_events` logging already used by `prompt_library_service.py` (`compute_prompt_changes`, `redact_changes`)
- [ ] Confirm pipeline-tier edits capture `actor_username`, `actor_role`, before/after diff, IP/user-agent — parity with library-tier audit today
- [ ] Retire `PromptVersion.change_reason` as the sole audit trail for pipeline prompts (keep the column for human-readable context, but it's no longer the only record)

**Acceptance:** every prompt edit (either tier) produces a `pl_audit_events` row queryable by entity_id.

---

## Phase 9 — API surface & frontend

Goal: one shared table, two purpose-scoped API façades — do not let one consumer see the other's rows.

- [ ] `/api/v1/prompt-library/*` (existing `prompt_library.py`) — filter to `governance_tier='library'` (or no filter if intentionally showing both, per product decision — default to library-only)
- [ ] `/api/v1/prompts/*` (existing `prompts.py`, pipeline admin) — filter to `governance_tier='pipeline'`, exposes `component_type`/`variant`/scope-fixing management (new endpoints for `pl_prompt_fixings` CRUD, replacing whatever exists today)
- [ ] `frontend/src/features/prompts` — update to manage `component_type`/`variant` and the approval workflow (Phase 7) for pipeline-tier prompts
- [ ] `frontend/src/features/clusterPrompt` — confirm it continues to work unchanged against `ClusterPrompt` (separate table, out of scope for this merge) or migrate it onto the fragment system (Phase 6) if the "cluster consistency" concept is being unified too — **decision needed, see Open Questions**

**Acceptance:** library UI shows only library-tier prompts; pipeline admin UI shows only pipeline-tier prompts with scope-fixing controls and approval-queue view.

---

## Phase 10 — Deprecation & cleanup

- [ ] Keep `prompts`/`prompt_versions`/`prompt_fixings`/`user_prompt_preferences` tables read-only (no new writes) for one full release cycle after Phase 5 ships to production, as a rollback fallback
- [ ] After the verification window, drop the old tables via a follow-up Alembic migration
- [ ] Remove `promptops_app/ui/generation_controls.py`'s direct `resolve_fixed_prompt` usage only if/when the legacy Streamlit path is itself retired (separate initiative — do not couple to this one)

---

## Phase 11 — Testing & rollout

- [ ] Full regression pass: generate one CDD, one Blueprint (both variants), one lesson, one assessment in staging, diff output against pre-migration baseline
- [ ] Load-test scope resolution (`resolve_fixed_prompt`) — confirm no N+1 query regression under the new `pl_prompt_fixings` lookups
- [ ] Backup production DB immediately before running Phase 2's migration script for real
- [ ] Staged rollout: ship Phase 1-2 (schema + data, inert) → verify → ship Phase 4-5 (wiring) behind a config flag if feasible → verify in production with one internal course → full rollout
- [ ] Rollback plan: config flag flips routers back to the old `Prompt`/`PromptVersion` path; old tables still intact per Phase 10

---

## Open questions (resolve before Phase 6 / Phase 9)

- [ ] Should `ClusterPrompt` (existing table, already auto-injected into Style context) be absorbed into the new fragment system (Phase 6), or remain a separate concept? Leaning toward absorbing it as the seed/source for the `cluster_consistency_brief`-style fragment, but it's currently manually curated rather than synthesized — needs a product decision, not just an engineering one.
- [ ] Approval workflow for Phase 7 — single-approver or two-person rule for pipeline-tier prompt activation?
- [ ] Are Cluster/Course AI-assist prompts (Phase 3 backlog items) in scope for this initiative or a separate follow-up initiative?

---

## Progress Log

_Append one entry per work session. Keep entries short — link to commits/PRs rather than duplicating detail._

| Date | Session focus | Phases touched | Outcome |
|---|---|---|---|
| 2026-07-02 | Plan authored | — | Initial version of this document created; no implementation started |
