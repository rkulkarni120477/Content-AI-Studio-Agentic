# Prompt System Consolidation — Implementation Plan

**Direction (as of 2026-07-03): REVERSED from the original plan.** We are **not**
merging the pipeline's native prompt tables into `pl_*`. Instead: the native
tables (`prompts`, `prompt_versions`, `prompt_fixings`, `user_prompt_preferences`,
`audit_logs` — all in `promptops_app/database.py`) are enhanced to absorb every
Prompt Library capability, all Prompt Library API/service code is re-pointed at
them, and the `pl_*` tables (`pl_prompts`, `pl_prompt_versions`, `pl_prompt_teams`,
`pl_prompt_tags`, `pl_prompt_variables`, `pl_attachments`, `pl_teams`,
`pl_prompt_requests`, `pl_reviews`, `pl_audit_events`) are then removed entirely.
See "Revision History" at the bottom for what changed and why.

**Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

This document is the single source of truth for this initiative. Each work
session should: (1) read "Current state" + the phase in progress, (2) update
checkboxes as work lands, (3) append an entry to the **Progress Log** at the
bottom before ending the session. Do not start a phase whose predecessors are
not checked off.

---

## 0. Current state (verified facts, not assumptions)

- **The entire Prompt Library feature — `promptops_app/pl_models.py`, `app/api/v1/routers/prompt_library.py`, `promptops_app/services/prompt_library_service.py`, and the full frontend at `frontend/src/features/promptLibrary/` — landed in a single commit (`42e076e`, "merging PL into CAS") on 2026-07-03.** It is the newest thing in the repo. This materially lowers migration risk: there is no multi-release deprecation window to honor, and very likely no real user data to preserve (verify this explicitly in Phase 2 rather than assuming).
- **`pl_*` tables have zero Alembic history.** They exist only because `Base.metadata.create_all()` runs at startup (`app/main.py:56-57` → `promptops_app/database.py:925`). This means **deleting the Python model classes will not drop the physical tables** in any dev/staging DB where the app has already run — an explicit `DROP TABLE` migration is required in Phase 6, not optional cleanup.
- **The native pipeline tables already support most of what's needed structurally:** `PromptVersion` already has `system_prompt` + `user_prompt_template` — the exact pair library content needs (library rows just leave `system_prompt` NULL and store the prompt body in `user_prompt_template`). `PromptFixing`/`UserPromptPreference` are already `Integer`-keyed against `prompts.id`, same as `Prompt.id` — **no FK retyping is needed in this direction** (this was the main cost in the old, reversed plan).
- **Two real conflicts to resolve, not just port:**
  1. `Prompt.name` is `unique=True`. Pipeline template names are a small, admin-controlled set — fine. Library prompts are freeform, many-per-user, not globally unique. **Correction (verified 2026-07-03): `name` is ALREADY nullable** — `Column(String, unique=True)` at `promptops_app/database.py:194` has no `nullable=False`. So there is **no nullability migration to write**; the only rule is a service-layer one: leave `name` NULL for `prompt_kind='library'` rows, populate it only for `'pipeline'` rows. Postgres unique indexes treat multiple `NULL`s as distinct (the codebase already relies on this exact behavior — see `PromptFixing`'s docstring at `promptops_app/database.py:232-235`), so uniqueness among non-null pipeline names is preserved automatically with zero extra constraint machinery. A new `title` column (nullable, non-unique) carries library display titles.
  2. `pl_prompts.id` is `String(36)` UUID, generated in the router (`str(uuid.uuid4())` at `app/api/v1/routers/prompt_library.py:211,234,303,346,361,385,438,493,551` — 9 id-generation sites total across all PL entities; only **2** of them mint `pl_prompts.id` itself — lines 211 create, 346 duplicate — the rest mint version/review/request/attachment ids). `Prompt.id` is `Integer` autoincrement. Switching means removing every manual UUID assignment and letting the DB assign IDs, and changing path params from `pid: str` to `pid: int` across ~15 endpoints. **This router-side generation is already redundant** — every `pl_*` model declares `default=_new_uuid` (`pl_models.py:47-48`), so the DB would assign the PK anyway; the cutover mostly *deletes* code rather than rewriting it. **Not every PL id is a UUID:** `PLPromptVariable.id` is **already `Integer` autoincrement** (`pl_models.py:146`) and `PLTeam.id` is a caller-supplied **`String(20)` slug with no default** (`pl_models.py:189`) — so the team remapping is slug→int (or keep-slug), not UUID→int; see the `teams` row in Phase 1 and the migration note in Phase 2. **Low frontend risk**: `prompt_library.py` has no dedicated Pydantic response models (it returns raw `dict`s via `payload: dict = Body(...)` and service-layer snapshot builders), so there's no strict `id: str`/UUID-format contract to break — FastAPI will happily serialize an int. Frontend audit (Phase 7) confirmed IDs are opaque strings end-to-end (no regex/`Number()`/`parseInt` on any id, route segments unconstrained), so the switch is safe there too.
- **No pre-existing "teams"/sharing-group concept elsewhere in the app.** Access control elsewhere is role-based (`promptops_app/auth/permission_matrix.py`, `permissions.py`) plus per-resource row assignment (`ProjectUserAssignment`, `CourseUserAssignment` — user↔project/course, not user↔named-group). `pl_teams` is a genuinely new primitive, not a duplicate of something existing — it must be ported as a real new table, not mapped onto existing RBAC.
- **A native `audit_logs` table already exists** (`AuditLog`, `promptops_app/database.py:475-504`: `user_id`, `action`, `entity_type`, `entity_id`, `project_id`, `course_id`, `metadata_json`, `ip_address`, `created_at`) and is structurally very close to `pl_audit_events`. Reuse it (additively) instead of creating a parallel prompt-specific audit table.
- **No soft-delete convention exists anywhere else in this app** (`deleted_at` appears nowhere in `database.py` outside `pl_models.py`). Adopting `deleted_at` on `prompts` is a new, deliberate convention scoped to this table — not a violation of an existing pattern, since none exists to violate. Documented here so it isn't second-guessed later as an inconsistency.
- **Unrelated, do not touch:** `UserPromptHistory` (`database.py:753-778`, saved free-text generation instructions) and `ClusterPrompt`/`cluster_prompts.py` (cluster-scoped snippets auto-injected into Style context) are different features that happen to have adjacent names. Out of scope for this initiative except where Phase 8's Open Questions note a possible future convergence with `ClusterPrompt`.
- **Also still open from the original architecture discussion** (independent of the PL/CAS table question, still required per the taxonomy below). **Corrected against code 2026-07-03:**
  - **Three stages already read the DB registry, not two.** CDD (`app/api/v1/routers/cdd.py:242`) and Blueprint (`blueprints.py:181`) call `build_prompt("...", variables, db=db)`; **Style also reads the DB** via `load_template("style_understanding", db=db)` (`promptops_app/services/style_service.py:53-54`) — it just builds its *user* prompt in code rather than from a template, so it uses the raw loader instead of `build_prompt`. **Generate is the only fully hard-coded stage** — `run_generation_job` (`promptops_app/jobs/generation_jobs.py`) never imports `build_prompt`/`load_template` and composes from constants (`PERSONA_PREFIX_TEMPLATE`, `LESSON_WITH_CONTEXT_SYSTEM/USER`). So Phase 8's "wire onto the registry" work is narrower than originally scoped: Style is already wired; only **Generate** needs the DB path added.
  - The 3-tier fallback is **2-tier inside the loader** (`prompt_loader.load_template`: DB → file); the inline-constant "3rd tier" actually lives in **each caller's own `try/except`** (`cdd.py:243`, `blueprints.py:183`, `style_service.py:56`), not in the loader. Phase 9's acceptance ("constants only in `except` blocks") is therefore already partially true and just needs Generate brought into the same shape.
  - `resolve_fixed_prompt()` is implemented (`promptops_app/repositories/prompt_repository.py:133`) but only called from the legacy Streamlit UI (`promptops_app/ui/generation_controls.py:508`), never from the FastAPI routers the React frontend hits.
  - Blueprint student/teacher is one row gated by a `teacher_mode`/`student_mode` flag (`blueprints.py:176-177`) instead of two independent assets.
  - **`build_prompt` runs with `strict=False`** (`prompt_builder.py:109`) and no caller passes `strict=True`, so `_REGISTRY`'s `required_vars` are **not enforced at runtime** — a missing variable is left as literal `{placeholder}` text in the prompt (`prompt_builder.py:90`) rather than raising. Worth tightening as part of Phase 8 so registry-declared variables are actually validated.
  - These are fixed in **Phase 8** (not Phase 5 — earlier draft mislabelled the phase), using the same native tables this whole plan now targets.

**Target prompt taxonomy** (unchanged from the original plan — still the goal, now seeded into native tables only):

| Entity/Stage | LLM task(s) | Prompt uniqueness | Uses persona/tone fragment? |
|---|---|---|---|
| Cluster | Cluster Definition Assistant, Cross-Course Consistency Synthesizer | 2 unique prompts (optional / backlog) | No — Style doesn't exist yet |
| Course | Course Scaffolding Assistant, Cluster-Fit Classifier | 2 unique prompts (optional / backlog) | No — Style doesn't exist yet |
| Style | Style Extraction (reference docs → tone/voice profile) | 1 unique prompt | N/A — this stage produces the fragment |
| CDD | Course structural planning | 1 unique prompt | Yes |
| Blueprint | Module detailing | 2 unique prompts (student, teacher — never merged) | Yes |
| Generate | Content authoring | N unique prompts, 1 per component type (lesson / assessment item / interactive) | Yes |

---

## Phase 0.5 — Unified prompt model (the reconciled design)

> This section is the "careful design" the whole initiative turns on: **the pipeline and the Prompt Library modelled a "prompt" differently, and after the merge one `prompts` table must serve both flows without either corrupting the other.** Every schema choice in Phase 1 and every wiring choice in Phases 4–9 follows from the five decisions below. Read this before touching a migration.

### The two flows, as they exist today

| Dimension | **Pipeline flow** (Style / CDD / Blueprint / Generate) | **Library flow** (`pl_*`) |
|---|---|---|
| What a "prompt" *is* | A reusable **SYSTEM + USER pair** — `system_prompt` (role/rules) + `user_prompt_template` (form data filled in) | A single freeform **body** (`pl_prompts.content`) a user wrote to reuse/share |
| Identity | Small admin set; `name` unique + `component_type` (+ future `variant`) | Freeform `title`, many-per-user, `category` + tags |
| Substitution syntax | Python `str.format` → `{course_name}` (`prompt_builder.py:83-90`) | `{{var}}` double-brace, regex-replaced in `render_prompt_content` (`prompt_library_service.py:67-88`) |
| Resolution | 3-tier: DB `build_prompt`/`load_template` → `.md` file → inline constant (in caller `except`) | Direct row read by id; no fallback tiers |
| Versioning | Append-only, `deploy_new_version()` retires old + activates new, one `is_active` | `version_number` bump, instant-publish, `PLPromptVersion` per save |
| Editing on a surface | Inline **override** (`system_prompt_override`/`user_prompt_override`) used once, **never saved** (`cdd.py:213-215`); persistent edits only via the admin Prompts page | **Every save is a version** — no ephemeral override concept |
| Governance | Admin/prompt-engineer only; today no approval gate | `visibility` (draft/team/global) + teams + reviews + change-requests |
| Injected context | Cluster prompts + active Style layer + CDD/Blueprint context stacked in by code (`build_style_context`) | None — library prompts are standalone text |

The merge does **not** blend these into one behavior. It puts both **kinds** of row in one table and keeps their behaviors separate, selected by a discriminator. The five decisions below define exactly where they share machinery and where they stay apart.

### Decision 1 — one table, two `prompt_kind`s, behavior selected by the discriminator

`prompts.prompt_kind ∈ {'pipeline','library'}` (Phase 1) is the master switch. It drives, in one field: taxonomy (pipeline stages vs library assets), which columns are meaningful, which code path handles the row, and the permission tier. **Rule enforced everywhere:** every library query filters `prompt_kind='library'` (Phase 4) and every pipeline lookup filters `prompt_kind='pipeline'` (implicit today because pipeline looks up by `name`/`component_type`, which library rows leave NULL). The two populations never leak into each other's lists, searches, or resolution paths.

### Decision 2 — the load-bearing mapping: **library body → `user_prompt_template`, `system_prompt` stays NULL**

This is the single most consequential decision, so its consequences are spelled out:

- A library row stores its freeform body in **`user_prompt_template`** and leaves **`system_prompt` NULL**. `PromptVersion` already carries exactly this pair, so no content column is added.
- **The SYSTEM/USER split simply does not apply to library rows.** There is no "role vs request" concept in the library; the whole prompt is the body. A NULL `system_prompt` is the explicit signal "this is a single-body (library) prompt," not missing data.
- **The two substitution syntaxes must never cross.** Pipeline rendering uses `str.format` (`{name}`); library rendering uses `{{var}}`. A library body fed through `build_prompt` would have its `{{var}}` mangled by `.format`, and a pipeline template fed through `render_prompt_content` would keep its `{name}` untouched and miss its real vars. **The `prompt_kind` guard is what keeps them apart:** library rows are only ever rendered by `render_prompt_content`; pipeline rows only ever by `build_prompt`. This invariant must be asserted in Phase 4 tests, not left implicit.
- `render_prompt` (`prompt_library.py:179`) therefore keeps reading the body from `user_prompt_template` (was `content`) after the cutover — a one-line source change, same `{{var}}` engine.

### Decision 3 — versioning: two write-paths, one `prompt_versions` table

Both kinds append rows to `prompt_versions`; they differ only in *how a version becomes active*:

- **Library**: instant-publish. On save, write a new `PromptVersion`, bump `version_number` (Phase 1's new numeric column, replacing brittle string parsing), mark it active immediately — today's behavior, unchanged.
- **Pipeline**: gated. Version activation goes through `workflow_state` (`draft → in_review → approved → active`, Phase 1 column) via the Phase 8 approval gate. `deploy_new_version()`'s retire-old-activate-new stays the mechanism; the gate just decides *when* activation is allowed.
- Shared invariants added in Phase 1: `UniqueConstraint(prompt_id, version_number)` (missing today) and `version_number` backfilled from `created_at` order. `change_reason` doubles as the library "version note" — no new column.

The pipeline's **inline override** (`*_override`, used-once-not-saved) is a generation-surface feature, **not** a version and **not** a library concept — it stays exactly as-is and produces no `prompt_versions` row. Nothing in the merge touches it.

### Decision 4 — governance: shared storage, split policy

- **Library rows** keep `visibility` (draft/team/global), team links (`prompt_team_links`), reviews (`prompt_reviews`), and requests (`prompt_requests`) — all ported 1:1 into native tables (Phase 1). Instant-publish, self-service.
- **Pipeline rows** ignore `visibility`/teams/reviews and are instead gated by the new `prompt.pipeline.edit` permission (Phase 8, admin/prompt-engineer only) plus the `workflow_state` approval gate. A non-admin cannot activate a pipeline version.
- **Audit is unified**: both kinds write to the existing native `audit_logs` (`AuditLog`), extended additively with `actor_role`/`user_agent`/`changes` (Phase 1). The dict-based `compute_prompt_changes`/`redact_changes` helpers are model-agnostic and reused verbatim (Phase 4). The parallel `pl_audit_events` table is dropped.

### Decision 5 — pipeline completion rides the same table (no separate effort)

Once both kinds live in `prompts`, the long-standing pipeline gaps (Phase 8) are just more `prompt_kind='pipeline'` rows and the standard resolution path:

- **Generate** gets wired onto `build_prompt(..., db=db)` with rows `content_generation`/`quiz_generation`/`variant=interactive` — closing the "only Generate is hard-coded" gap (Style/CDD/Blueprint already read the DB).
- **Blueprint teacher/student** becomes two independent rows (`component_type=blueprint, variant=student|teacher`), each separately versioned — replacing the single-row `teacher_mode` flag.
- **`resolve_fixed_prompt()`** is wired into the FastAPI routers (not just Streamlit), so course→cluster→project→global scope-locking works in the live app.
- **`prompt_variables`** (Phase 1) finally gives pipeline templates a DB-backed variable declaration, and enabling `strict=True` (or an equivalent validation pass) makes those declarations actually enforced instead of silently emitting literal `{placeholder}` text.

### The reconciled lifecycle, end to end

```
                         ┌───────────────────────  prompts (one table)  ───────────────────────┐
                         │  prompt_kind='pipeline'                 prompt_kind='library'         │
   admin Prompts page ──▶│  name, component_type, variant          title, category, visibility   │◀── Prompt Library UI
   (gated + approval)    │  system_prompt + user_prompt_template   user_prompt_template only      │   (self-service, instant)
                         │            │                                      │                    │
                         └────────────┼──────────────────────────────────────┼───────────────────┘
                                      │                                       │
                     build_prompt() / load_template()            render_prompt_content()
                     `.format`  {name}   · 3-tier fallback        `{{var}}` regex · direct read
                                      │                                       │
        Style/CDD/Blueprint/Generate  │                                       │  browse / share / review / request
        + Style layer + cluster +     ▼                                       ▼
        CDD/BP context injected   LLM call                              (text handed to user)

   versions  ──▶  prompt_versions (shared): version_number, workflow_state
                   pipeline → draft→in_review→approved→active (gated)
                   library  → active immediately (instant-publish)
   audit     ──▶  audit_logs (shared, additive: actor_role/user_agent/changes)
```

**Acceptance for this section:** the schema in Phase 1 provides a concrete home for every row in the "two flows" table above, and each of the five decisions maps to specific Phase 1 columns / Phase 4-5 code paths / Phase 8 wiring — no behavior from either flow is silently dropped or blended.

---

## Phase 1 — Target schema design (finalize before writing migrations)

### `prompts` — new columns

| Column | Type | Notes |
|---|---|---|
| `prompt_kind` | `String(20) NOT NULL DEFAULT 'library'` | `'pipeline'` \| `'library'`. Single field driving both taxonomy and permission tier (no separate `governance_tier` — avoids a redundant column carrying the same signal). |
| `title` | `String(300) NULLABLE` | Library display title. Required at the Pydantic/service layer when `prompt_kind='library'`, not enforced at DB level (consistent with other optional-at-DB / required-at-schema patterns already in this codebase, e.g. `prompt_fixings` composite lookups). |
| `category` | `String(100) NULLABLE` | Freeform library category — distinct from `component_type`, which is pipeline-only. |
| `visibility` | `String(20) NOT NULL DEFAULT 'draft'` | `'global'` \| `'team'` \| `'draft'`. |
| `variant` | `String(50) NULLABLE` | Pipeline-only: `student`/`teacher` (blueprint), `lesson`/`assessment`/`interactive` (generate). Null for library rows and for CDD/Style (no variant). |
| `parent_id` | `Integer NULLABLE, FK -> prompts.id ON DELETE CASCADE`, indexed | Self-referential "follow-up prompt" hierarchy (library feature). |
| `last_used_at` | `DateTime NULLABLE` | |
| `deleted_at` | `DateTime NULLABLE`, indexed | Soft delete — new convention, scoped to this table (see Phase 0 note). |

`name` **is already `NULLABLE`** (`database.py:194` — `unique=True` with no `nullable=False`), so there is **no column-nullability change to migrate**. Its existing `unique=True` is kept as-is and continues to work correctly because Postgres allows multiple `NULL`s under a standard unique index — no partial-index trick needed. The only rule is service-layer: library rows leave `name` NULL, pipeline rows populate it.

Add a **partial unique index**: `ON prompts(component_type, variant) WHERE is_default = true AND prompt_kind = 'pipeline'` — guarantees exactly one default prompt per pipeline stage/variant, which today is only enforced by convention.

### `prompt_versions` — new columns

| Column | Type | Notes |
|---|---|---|
| `version_number` | `Integer NULLABLE` initially, backfilled then set `NOT NULL` | Numeric ordering key, replacing reliance on parsing the free-text `version` string. `version` (e.g. `"v3"`) stays as the existing display label for backward compatibility with the pipeline admin UI (`app/schemas/prompt.py`'s `PromptVersionRead`/`PromptVersionListItem`, which already expect `version: str`). |
| `workflow_state` | `String(20) NOT NULL DEFAULT 'active'` | `draft` \| `in_review` \| `approved` \| `active`. Only enforced for `prompt_kind='pipeline'` rows (Phase 5's approval gate); library rows stay at `active` immediately, same as today's instant-publish behavior. |

Add `UniqueConstraint(prompt_id, version_number)` — this constraint is missing today (a real, pre-existing gap, not just a port) and should be added regardless of this initiative's other goals.

`change_reason` is reused as-is for the library's "version note" concept — no new column needed.

### New tables (all `Integer` PK, no tenant column, naming matches existing snake_case conventions — no `pl_` prefix)

| Table | Columns | Replaces |
|---|---|---|
| `prompt_tags` | `prompt_id FK CASCADE, tag String(100)` — composite PK | `pl_prompt_tags` |
| `prompt_variables` | `id PK, prompt_id FK CASCADE, name String(100) NOT NULL, label String(200), hint Text, sort_order Integer default 0` | `pl_prompt_variables`. Also finally gives pipeline templates a DB-backed variable declaration, replacing the static `_REGISTRY["required_vars"/"optional_vars"]` dict in `prompt_loader.py` — closes a drift risk noted in the original architecture review. |
| `prompt_attachments` | `id PK, prompt_id FK CASCADE, original_name String(300), stored_name String(500), size_bytes BigInteger, uploaded_by String(100), uploaded_at DateTime` | `pl_attachments`. Storage dir renamed from `pl_attachments/` to `prompt_attachments/` (config var `PROMPT_ATTACHMENTS_DIR`, default `./prompt_attachments`). |
| `teams` | `id PK (Integer autoincrement), name String(100) UNIQUE NOT NULL, created_by String(100), created_at DateTime` | `pl_teams`. Generic name since nothing else in the app currently claims "teams" — scoped to prompt-sharing for now; reusable later if another feature needs named sharing groups. **Note:** `pl_teams.id` was a caller-supplied `String(20)` slug (`pl_models.py:189`), not a UUID; the native `id` becomes Integer autoincrement, so the migration (Phase 2) maps old slug → new int and rewrites `prompt_team_links.team_id` accordingly. If any external caller/UI references teams by slug, keep the slug in `name` (it already is) so nothing depends on the id shape. |
| `prompt_team_links` | `prompt_id FK CASCADE, team_id FK CASCADE` — composite PK | `pl_prompt_teams`. **Deliberately drops** the redundant single `team_id` FK that existed directly on `pl_prompts` alongside the M:N table — that was an inconsistency in the original design (a prompt could have one "primary" team AND multiple linked teams simultaneously with no defined precedence). The native version keeps only the clean M:N. |
| `prompt_reviews` | `id PK, prompt_id FK CASCADE, username String(100) NOT NULL, rating SmallInteger NOT NULL, feedback Text, created_at, updated_at`, `UniqueConstraint(prompt_id, username)` | `pl_reviews`. Note: a table named `reviews` already exists in `database.py` for a **different** purpose (content-block review workflow) — `prompt_reviews` must stay a distinct table, not reuse or rename into that one. |
| `prompt_requests` | `id PK, title String(300) NOT NULL, description Text, type String(20) NOT NULL DEFAULT 'new', prompt_id FK SET NULL NULLABLE, requested_by String(100) NOT NULL, status String(20) NOT NULL DEFAULT 'open' indexed, admin_notes Text, created_at, updated_at` | `pl_prompt_requests` |

### `audit_logs` — additive columns only

| Column | Type | Notes |
|---|---|---|
| `actor_role` | `String(64) NULLABLE` | |
| `user_agent` | `String(512) NULLABLE` | |
| `changes` | `JSON NULLABLE` | Structured diff, populated by the existing `compute_prompt_changes`/`redact_changes` utilities in `prompt_library_service.py` (framework-agnostic, operate on plain dicts — reusable as-is). `metadata_json` (existing `Text` column, used by other `AuditLog` consumers today) is left untouched to avoid any risk to unrelated audit entries. |

`prompt_fixings` and `user_prompt_preferences`: **no changes required.** Already `Integer`-keyed against `prompts.id`.

**Acceptance:** schema design reviewed against every capability listed in Phase 0's feature inventory (CRUD, versioning, tags, team visibility, variables, attachments, reviews, requests, follow-ups, audit, search/filter) — each has a concrete home in the table above before any migration is written.

---

## Phase 2 — Data check & migration script

- [ ] **Before writing any migration, check staging/dev databases for actual rows in `pl_*` tables.** Given the same-day-commit finding in Phase 0, expect near-zero, but verify — do not assume.
- [ ] If any real rows exist: write `scripts/migrate_pl_to_native_prompts.py` (idempotent, `--dry-run` first, same pattern as the now-obsolete `scripts/migrate_prompt_library.py`) to carry them into the new native tables/columns — building three id-remap maps: UUID `pl_prompts.id` → new int (`prompts.id`), `pl_teams.id` slug `String(20)` → new int (`teams.id`), and each UUID child-row id → new int; then rewriting every FK (`parent_id`, `prompt_team_links.team_id`, version/tag/variable/attachment/review/request `prompt_id`) through those maps. Preserve `created_at`/`created_by` on every row, set `prompt_kind='library'`, map `content` → `user_prompt_template` with `system_prompt` NULL (Decision 2), and resolve self-referential `parent_id` in two passes (roots first, then children) as the existing script already does.
- [ ] If no real rows exist (expected outcome): skip the carry-over script entirely, document that decision here with the verification query used and its result, and proceed straight to Phase 3.
- [ ] Regardless of outcome: **take a full SQL dump of all 9 `pl_*` tables before Phase 6 drops them** (see Phase 6) — due diligence, independent of whether Phase 2 found data worth formally migrating.

**Acceptance:** either (a) a verified-empty finding is recorded in this document, or (b) the carry-over script has run successfully with dry-run output reviewed.

---

## Phase 3 — Alembic migration

- [ ] Single migration (or a small ordered set) implementing all of Phase 1's additive changes: new columns on `prompts`/`prompt_versions`/`audit_logs`, new tables (`prompt_tags`, `prompt_variables`, `prompt_attachments`, `teams`, `prompt_team_links`, `prompt_reviews`, `prompt_requests`), the partial unique index, and the `UniqueConstraint(prompt_id, version_number)` on `prompt_versions`
- [ ] Backfill `version_number` for all existing `prompt_versions` rows (sequential per `prompt_id`, ordered by `created_at`), then set the column `NOT NULL`
- [ ] Backfill `prompt_kind = 'pipeline'` for all existing `prompts` rows (they're all pipeline templates today)
- [ ] Do **not** touch `pl_*` tables in this migration — that's Phase 6, after the service/router cutover is verified
- [ ] Apply to a scratch/staging DB; confirm `init_db()` idempotency (rerun app startup twice, no errors); confirm existing CDD/Blueprint generation still works unmodified (this phase is purely additive to tables those flows already use)

**Acceptance:** migration applies cleanly; all existing endpoints (pipeline admin, CDD/Blueprint generation, Prompt Library on its old `pl_*` backing) continue to work exactly as before — this phase changes nothing observable yet.

---

## Phase 4 — Service layer rewrite

Goal: `promptops_app/services/prompt_library_service.py` operates against the new native models instead of `PLPrompt`/`PLPromptVersion`/etc., with **zero change to its public function signatures** so the router doesn't need a parallel rewrite in the same step (keeps this phase reviewable in isolation).

- [ ] Repoint every query/import from `promptops_app.pl_models` (`PLPrompt`, `PLPromptVersion`, `PLPromptTag`, `PLPromptVariable`, `PLAttachment`, `PLTeam`, `PLPromptTeam`, `PLPromptRequest`, `PLReview`, `PLAuditEvent`) to the corresponding native model (`Prompt` filtered to `prompt_kind='library'`, `PromptVersion`, `PromptTag`, `PromptVariable`, `PromptAttachment`, `Team`, `PromptTeamLink`, `PromptRequest`, `PromptReview`, `AuditLog`)
- [ ] Remove all manual `str(uuid.uuid4())` ID assignment (the 8+ call sites currently in the router — will move here once the router is thin, see Phase 5) — let the DB autoincrement `Prompt.id`/child-table `id`s
- [ ] `build_prompt_snapshot()` / `compute_prompt_changes()` / `redact_changes()` — no change needed, already dict-based and model-agnostic
- [ ] Every write path (`create_prompt`, `create_version`, `duplicate_prompt`, `submit_review`, `create_request`, `upload_attachment`, `create_team`) writes an `AuditLog` row via the existing audit helper, populating the new `actor_role`/`changes` columns
- [ ] All queries scoped to `prompt_kind = 'library'` by default so pipeline-tier rows never leak into library list/search results
- [ ] Unit tests: `tests/unit/` — one per rewritten function, asserting identical output shape to the pre-rewrite version (use Phase 2's dry-run output or fresh fixtures as the comparison baseline)

**Acceptance:** all service-layer unit tests pass against the new tables with output identical in shape to the old `pl_*`-backed implementation (values will legitimately differ only in `id` type — int vs UUID string).

---

## Phase 5 — Router rewrite

- [ ] `app/api/v1/routers/prompt_library.py`: change every path param from `pid: str`/`rid: str`/`tid: str`/`aid: str` to `int` (≈15 endpoints — see the full list captured in Phase 0)
- [ ] Remove any remaining direct `pl_models` imports from the router (should be none left if Phase 4 fully encapsulated data access in the service layer — if the router bypasses the service anywhere, fix that as part of this phase, don't just swap the import)
- [ ] Attachment upload/download paths: switch storage directory to `PROMPT_ATTACHMENTS_DIR`
- [ ] Manual smoke test of every endpoint listed in Phase 0 against the new backing tables (list/search/create/update/delete/duplicate prompts; create version; tags; visibility/team filtering; variable substitution render; attachments upload/download/delete; reviews; requests; audit list + CSV export; teams CRUD)

**Acceptance:** every Prompt Library endpoint works end-to-end against the native tables with no `pl_*` reads/writes remaining anywhere in the call path (`grep -rn "pl_models\|PLPrompt\|PLTeam\|PLReview\|PLAttachment" app/ promptops_app/` returns nothing outside `pl_models.py` itself and the now-obsolete migration script).

---

## Phase 6 — Drop `pl_*` tables

- [ ] Confirm Phase 2's dump exists and is retained somewhere durable (not just local disk) — this is the safety net given the tables were never Alembic-managed
- [ ] Alembic migration: explicit `DROP TABLE IF EXISTS pl_prompts, pl_prompt_versions, pl_prompt_teams, pl_prompt_tags, pl_prompt_variables, pl_attachments, pl_teams, pl_prompt_requests, pl_reviews, pl_audit_events CASCADE` — required specifically because these were `create_all`-only and will otherwise persist as orphan tables in every environment the app has run in
- [ ] Delete `promptops_app/pl_models.py`
- [ ] Delete or clearly archive `scripts/migrate_prompt_library.py` — it imports `pl_models` (`from promptops_app import pl_models`) to migrate an external standalone Postgres DB into `pl_*`; once `pl_models.py` is gone this script is dead code that will fail on import if anyone runs it. Do not leave it in a working state pointing at a dropped schema.
- [ ] Delete the old `PL_ATTACHMENTS_DIR`-based attachment files directory (after confirming nothing in `prompt_attachments/` still needs them — should already be handled by Phase 2/4 if a data carry-over happened)
- [ ] Repo-wide grep for `pl_` / `PLPrompt` / `PLTeam` / etc. to confirm zero remaining references outside this plan document and git history

**Acceptance:** fresh `alembic upgrade head` on an empty DB produces only the native, enhanced schema — no `pl_*` tables exist anywhere; `init_db()` no longer creates them (confirm by checking `Base.metadata.tables` no longer lists any `pl_` name after `pl_models.py` is deleted).

---

## Phase 7 — Frontend compatibility pass

- [ ] Audit `frontend/src/features/promptLibrary/` (api clients: `client.js`, `prompts.js`, `teams.js`, `requests.js`, `reviews.js`, `audit.js`; pages: `PromptListPage`, `PromptDetailPage`, `PromptFormPage`, `RequestsPage`, `RequestNewPage`, `admin/AdminAuditLogPage`; components: `TeamMultiSelect`, `PromptCard`, `PromptListTable`) for any UUID-format assumption — regex validators on ID strings, route param patterns, key-based list rendering that assumes string IDs. Expected to be minimal/none given the backend never exposed a typed UUID contract, but must be verified, not assumed.
- [ ] Update any route definitions (`frontend/src/app/routes.jsx`) using string-typed dynamic segments if they enforce UUID shape
- [ ] Manual UI walkthrough in a browser: create/edit/delete a prompt, add tags, create a team and share a prompt to it, submit a review, submit a request, upload/download an attachment, view the audit log — confirm all render correctly against integer IDs

**Acceptance:** full manual walkthrough of the Prompt Library UI against the new backend with no console errors and no broken ID-dependent behavior.

---

## Phase 8 — Pipeline registry completion (carried over, still required)

Independent of the table-consolidation direction, these gaps from the original architecture review still need closing, now exclusively on the native tables this plan already targets:

- [ ] Wire `resolve_fixed_prompt()` into the live FastAPI routers (`cdd.py`, `blueprints.py`, and the not-yet-wired `generations.py`/style router) instead of only the legacy Streamlit UI path
- [ ] Split Blueprint's `teacher_mode` flag into two independent `prompts` rows (`component_type=blueprint, variant=student` / `variant=teacher`), each separately versioned
- [ ] Wire **Generate** (`content_generation`, `quiz_generation`, new `variant=interactive`) onto the DB-backed path — it is the **only** stage still fully hard-coded (`generation_jobs.py` never calls `build_prompt`/`load_template`). Style already reads the DB via `load_template("style_understanding", db=db)` (`style_service.py:53-54`) and CDD/Blueprint via `build_prompt`; leave those as-is. When wiring Generate, keep the existing `PERSONA_PREFIX_TEMPLATE`/`LESSON_WITH_CONTEXT_*` constants as the file/inline fallback tier only.
- [ ] Enforce registry variable declarations: pass `strict=True` (or add an equivalent validation pass) to `build_prompt` so `_REGISTRY`/`prompt_variables`-declared required vars actually raise instead of emitting literal `{placeholder}` text (`prompt_builder.py:90,109`) — a latent gap today, independent of the merge
- [ ] Seed one `is_default=True` row per taxonomy line from the table in Phase 0 (Cluster/Course rows seeded but flagged backlog, not wired to a live endpoint yet — same as originally planned)
- [ ] Approval gate: pipeline-tier (`prompt_kind='pipeline'`) version activation goes through `workflow_state` (`draft → in_review → approved → active`) rather than instant-activate; library-tier rows keep today's instant-publish behavior
- [ ] New permission `prompt.pipeline.edit` (admin/prompt-engineer only), distinct from the Prompt Library's existing permissions, gating writes to any `prompt_kind='pipeline'` row

**Acceptance:** same as the original plan's Phase 5/7 acceptance criteria — `grep` confirms hardcoded prompt constants only appear in `except` fallback blocks; a course-level `prompt_fixings` override is verified to take effect in a live generation call; a non-admin cannot activate a pipeline-tier prompt version via the API.

---

## Phase 9 — Shared fragment library (carried over)

- [ ] `prompt_fragments` / `prompt_fragment_versions` (native-named, not `pl_`-prefixed) for `guardrails`, `context_header`, `persona_tone`, `output_contract_json`, `output_contract_markdown`, `regenerate_wrapper`
- [ ] Composer function assembling `guardrails + context_header + persona_tone (if applicable) + stage_unique_template + output_contract`
- [ ] Migrate `PERSONA_PREFIX_TEMPLATE`/`DEFAULT_STYLE_GUIDE` (`promptops_app/prompt_templates.py:6,16`) into fragment rows; file constants remain as the fallback tier only

**Acceptance:** editing the `guardrails` fragment once updates output across CDD/Blueprint/Generate without touching per-stage templates.

---

## Phase 10 — Testing & rollout

- [ ] Full regression: generate one CDD, one Blueprint (both variants), one lesson, one assessment in staging; diff output against pre-change baseline
- [ ] Full Prompt Library regression per Phase 7's manual walkthrough, plus automated coverage for the rewritten service functions (Phase 4)
- [ ] Confirm the Phase 2/6 backup of `pl_*` data is retained per whatever retention policy the team sets (recommend minimum 2 weeks post-cutover, even though the expected finding is zero real data)
- [ ] Staged rollout: Phase 1-3 (schema, additive, inert) → verify → Phase 4-5 (service/router cutover) → verify in staging → Phase 6 (drop `pl_*`) → verify → Phase 8-9 (pipeline completion + fragments)
- [ ] Rollback plan for Phase 4-5: since Phase 6 hasn't run yet at that point, `pl_*` tables and the old service/router code are still intact in git history — revert the cutover commit(s) if a serious issue surfaces before Phase 6 runs. **Once Phase 6 has run, there is no automated rollback** — recovery would mean restoring from the Phase 2/6 SQL dump. Do not run Phase 6 until Phase 7's manual walkthrough is fully signed off.

---

## Open questions

- [ ] Confirmed answer needed from Phase 2: is there any real dev/staging data in `pl_*` tables today that must be preserved, or is it verified empty?
- [ ] Should `teams` (new, prompt-scoped for now) be designed as a general-purpose sharing primitive for future non-prompt features, or kept intentionally narrow? Affects whether it lives in a generically-named location vs. something more prompt-specific.
- [ ] Approval workflow for Phase 8 — single-approver or two-person rule for pipeline-tier prompt activation?
- [ ] Should `ClusterPrompt` (separate existing table, auto-injected into Style context) eventually converge with the new `prompt_fragments` concept (Phase 9), or remain a distinct feature? Still unresolved from the original plan.
- [ ] Are Cluster/Course AI-assist prompts (Phase 8 backlog items) in scope for this initiative or a separate follow-up?

---

## Revision History

- **2026-07-03** — Code-grounded review + unified-design pass. Verified every "Current state" claim against the actual code (two parallel read-only audits of the pipeline and library sides). Corrections landed: (a) `Prompt.name` is *already* nullable — no nullability migration needed; (b) **Style already reads the DB** (`load_template`), so three stages are DB-backed and only **Generate** is hard-coded — narrows Phase 8; (c) the inline-constant fallback tier lives in each caller's `try/except`, not the loader (loader is DB→file only); (d) `build_prompt` runs `strict=False` so registry `required_vars` aren't enforced — added to Phase 8; (e) PL id types clarified — `PLPromptVariable.id` already Integer, `PLTeam.id` a `String(20)` slug (not UUID), router UUID generation redundant with model defaults; (f) frontend confirmed to treat ids as opaque strings. Added **Phase 0.5 — Unified prompt model**, spelling out the five decisions that reconcile the pipeline SYSTEM/USER-pair flow with the library single-body flow in one `prompts` table (the `prompt_kind` discriminator, the body→`user_prompt_template` mapping and its substitution-syntax hazard, dual versioning write-paths, split governance, and pipeline completion on the same table). No implementation started.
- **2026-07-03** — Direction reversed. Original plan merged native pipeline tables *into* `pl_*` (Prompt Library becomes canonical). New direction: native tables absorb Prompt Library functionality, `pl_*` is removed. Reason: `pl_*` was newly introduced (same-day commit `42e076e`) with no production data and no Alembic history, making it the lower-cost side to retire; the native tables already had the closer-fitting schema (system/user prompt pair, matching FK types for `prompt_fixings`/`user_prompt_preferences`) and are the tables the live generation pipeline depends on.
- **2026-07-02** — Initial version of this document created (original merge direction).

## Progress Log

_Append one entry per work session. Keep entries short — link to commits/PRs rather than duplicating detail._

| Date | Session focus | Phases touched | Outcome |
|---|---|---|---|
| 2026-07-02 | Plan authored (original direction) | — | Initial version created; no implementation started |
| 2026-07-03 | Plan reversed per direction change | — | Rewrote plan to consolidate onto native `prompts` tables instead of `pl_*`; verified facts about commit history, table lineage, and PK/uniqueness conflicts before rewriting; no implementation started yet |
| 2026-07-03 | Code-grounded review + unified design | 0, 0.5, 1, 2, 8 | Ran two read-only code audits (pipeline + library sides); corrected 6 factual claims in "Current state"/Phase 1/Phase 8 (name already nullable, Style already DB-backed, fallback-tier location, strict=False, PL id types, frontend id handling); added Phase 0.5 unified-prompt-model design (5 decisions reconciling the two flows). No implementation started |
