# Prompt System Consolidation — Implementation Plan

**Direction.** The native pipeline tables (`prompts`, `prompt_versions`, `prompt_fixings`,
`user_prompt_preferences`, `audit_logs` — all in `promptops_app/database.py`) are the canonical base.
They are enhanced to absorb every Prompt Library capability, all Prompt Library API/service code is
re-pointed at them, and the `pl_*` tables (`pl_prompts`, `pl_prompt_versions`, `pl_prompt_teams`,
`pl_prompt_tags`, `pl_prompt_variables`, `pl_attachments`, `pl_teams`, `pl_prompt_requests`,
`pl_reviews`, `pl_audit_events`) are then removed entirely. (Rationale in "Table-base decision" below.)

**Goal.** Beyond the table merge, the **Prompt Library becomes the single management + workflow
console for every Content AI Studio prompt** — both the freeform "library" prompts and the "pipeline"
prompts that drive Style / CDD / Blueprint / Generate. One `prompts` table holds both (discriminated
by `prompt_kind`); the Library UI manages both; but generation only ever resolves pipeline rows, and
pipeline writes are admin-gated + approval-gated. This subsumes the two other prompt editors that exist
today (an orphaned React admin page and the live Streamlit registry page).

**Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

This document is the single source of truth for this initiative. Each work session should: (1) read
"Current state" + the phase in progress, (2) update checkboxes as work lands, (3) append an entry to
the **Progress Log** at the bottom before ending the session. Do not start a phase whose predecessors
are not checked off.

---

## Table-base decision — native CAS `prompts` vs `pl_*`

Decided by asking not "which schema is richer at management" but "**which side is expensive and
dangerous to move, and which is cheap and safe to port onto it?**"

On pure management-feature richness, `pl_*` is the better-designed schema — it was purpose-built as a
prompt library (tags, teams, reviews/ratings, change-requests, attachments, visibility, soft-delete,
follow-up hierarchy, integer `version_number`, structured-JSON audit). But management features are
**additive and cheap to replicate** (add columns + child tables — exactly what Phase 1 does). The
native tables' advantages are **not** cheap to replicate onto `pl_*`.

| Dimension | Native CAS `prompts` | `pl_*` | Winner |
|---|---|---|---|
| Management feature richness (tags/teams/reviews/requests/attachments/visibility/hierarchy) | Thin | Rich | **`pl_*`** — but cheap to port to CAS |
| Generation integration (SYSTEM+USER pair, `component_type`, `is_default`, scope-fixing, `is_active`) | Built-in, **live** | None | **CAS**, decisively |
| Live data / criticality | Holds seeded defaults + admin pipeline prompts; **the table generation actually reads** | Empty (same-day commit `42e076e`, no prod data) | **CAS** — more to protect → keep as base, migrate the *empty* side |
| Key model | Integer PKs; `PromptFixing`/`UserPromptPreference` already Integer-FK to `prompts.id` | UUID (mixed: variables Integer, teams slug) | **CAS** — routing onto `pl_*` forces retyping the live fixing/preference FKs |
| DB lineage | create_all + ALTER (no Alembic) | create_all only (no Alembic) | Tie — both need Alembic going forward |
| Code to retire | Battle-tested, load-bearing generation path | New service/router/frontend, one commit, no deprecation window | **CAS as survivor** — `pl_*` code is cheap/safe to repoint |
| Effort + risk to unified target | Additive columns + repoint new PL code; **generation untouched** | Add system/user split + retype FKs + migrate live data + **rewire all 4 generation stages + fallback** + re-test | **CAS**, by a wide margin |

**Decision:** keep the native CAS tables as the canonical base; port `pl_*`'s richer management
feature-set onto them additively (Phase 1); then retire `pl_*` (Phase 6). Six of seven dimensions
favor CAS as the base; `pl_*` wins only on management-feature richness in isolation — an advantage
fully capturable by cheap additive porting, whereas CAS's advantages (live, integer-keyed,
generation-wired, data-bearing) are not cheaply capturable by `pl_*`. The rule that settles it: **keep
the hard-to-move side as the base, port the easy-to-move side onto it.**

---

## Safety, reversibility & pre-flight (read before any DB change)

**Honest framing.** No migration can be *guaranteed* not to break something — "100% with zero risk" is
not achievable. What this plan engineers instead is the achievable version: **breakage made very
unlikely, and if it happens, recovery fast, rehearsed, and zero-data-loss.** That rests on four pillars —
additive-only changes, a rehearsal environment, a real test safety net, and a verified backup/restore
path. Three of the four do not exist yet and are prerequisites (**Phase 0S**).

**Why the changes are inherently low-risk to reverse.** Everything through Phase 5 is *additive* and
*invisible to old code*: new nullable columns and new tables are neither read nor written by existing
code, so it keeps working unchanged even if new code is reverted. The only *destructive* step is Phase 6
(`DROP pl_*`), deliberately isolated behind a backup + full sign-off. The behavior changes (Phase 8
name-key resolution, Generate wiring) sit behind the existing DB→file→inline fallback plus a feature
flag, so they degrade safely and toggle off without a redeploy.

**Per-phase undo story:**

| Phase | What changes | How to undo | Reversible? |
|---|---|---|---|
| 1-3 (schema additive) | New columns/tables, backfills, constraints | Alembic `downgrade` (DROP the additions); old code already ignores them | ✅ Fully |
| 4-5 (service/router cutover) | Code repointed to native tables; `pl_*` still intact | `git revert` the cutover commit(s) | ✅ Fully — no data lost |
| 6 (drop `pl_*`) | Destructive | Restore from the pre-Phase-6 dump / RDS snapshot | ⚠️ Manual only — the one-way door |
| 7 (frontend) | UI only | `git revert` | ✅ Fully |
| 8 (name-key fix, Generate wiring, approval gate) | Live generation behavior | Feature-flag off → falls back to file/inline templates; `git revert` code | ✅ Flag + revert |
| 9 (fragments) | Additive | `git revert` + DROP fragment tables | ✅ Fully |

**Ultimate backstop for every phase:** production is AWS RDS (automated snapshots + point-in-time
recovery). **Take a manual RDS snapshot immediately before each schema phase** — nothing in the repo
automates DB backups (the deploy pipeline backs up only app files + `.env`). Snapshot + PITR is the
guaranteed recovery path even if a migration corrupts a table.

**Two blockers that must be fixed before Alembic is used at all (Phase 0S):**
1. **Alembic autogenerate is mis-wired.** `migrations/env.py` sets `target_metadata` from `app.core.database`'s (empty) `Base`, while all models live on a *different* `Base` in `promptops_app/database.py`. Running `--autogenerate` today would emit `DROP TABLE` for every existing table. Repoint `target_metadata` to `promptops_app.database.Base.metadata` first.
2. **`create_all()` races Alembic.** `init_db()` runs `create_all()` on every startup (`app/main.py:57`) and the deploy already runs `alembic upgrade head` before boot. For new tables the two desync `alembic_version` from reality (a container booting before `upgrade` creates the table via `create_all`, then a later `CREATE TABLE` migration fails "already exists"). Once real revisions exist, disable/prod-gate `create_all()` + the raw `ALTER`/`CREATE INDEX` blocks in `init_db()` so Alembic is the single source of truth.

**Pre-flight data checks (before adding constraints in Phase 3):**
- `UniqueConstraint(prompt_id, version_number)`: the seeded `lesson_generator` has two versions under one `prompt_id`. A naïve `DEFAULT 1` backfill (the repo's existing ALTER pattern) collides → constraint creation FAILS. **Backfill sequential numbers** (`row_number() OVER (PARTITION BY prompt_id ORDER BY id)`) first.
- Partial unique index on `(component_type, variant) WHERE is_default`: seeded defaults are one-per-component (safe), but `is_default` has no DB guard and app usage may have created duplicates. Run `SELECT component_type, count(*) FROM prompts WHERE is_default GROUP BY 1 HAVING count(*)>1` on prod and resolve first.
- Wrap each migration in a **single transaction** (Postgres transactional DDL) so a mid-way failure rolls back cleanly. Exceptions that can't run in a txn (`CREATE INDEX CONCURRENTLY`, volatile-default `ADD COLUMN`) are flagged per-migration.
- A UNIQUE constraint/index takes an `ACCESS EXCLUSIVE` lock that queues behind the app's connection pool (~30 conns/worker). Migrate in a quiet window / with the app quiesced, or use `CREATE UNIQUE INDEX CONCURRENTLY` then attach.

---

## 0. Current state (verified against code)

### The `pl_*` (Prompt Library) side
- The entire Prompt Library feature — `promptops_app/pl_models.py`, `app/api/v1/routers/prompt_library.py`, `promptops_app/services/prompt_library_service.py`, and the frontend at `frontend/src/features/promptLibrary/` — landed in a single commit (`42e076e`, "merging PL into CAS") on 2026-07-03. It is the newest thing in the repo: no multi-release deprecation window to honor, and very likely no real user data to preserve (verify explicitly in Phase 2).
- `pl_*` tables have **zero Alembic history**; they exist only because `Base.metadata.create_all()` runs at startup (`app/main.py:56-57` → `database.py:925`). Deleting the Python model classes will **not** drop the physical tables in any DB the app has already run against — an explicit `DROP TABLE` migration is required (Phase 6).
- IDs: `pl_prompts.id` (and version/attachment/request/review/audit ids) are `String(36)` UUID, but `PLPromptVariable.id` is already `Integer` (`pl_models.py:146`) and `PLTeam.id` is a caller-supplied `String(20)` slug with no default (`pl_models.py:189`). The router mints UUIDs at 9 sites (`prompt_library.py:211,234,303,346,361,385,438,493,551`), all **redundant** with each model's `default=_new_uuid` (`pl_models.py:47-48`). The router has **no Pydantic response models** (returns raw dicts via `payload: dict = Body(...)`), so there is no typed UUID contract to break — FastAPI will serialize an int fine.
- `PLPrompt.team_id` (a scalar FK) coexists with the `pl_prompt_teams` M:N junction and is actively kept in sync as "first team" by `set_prompt_teams` — a genuine redundancy the native design drops in favor of the clean M:N.
- Attachments live under `PL_ATTACHMENTS_DIR` (default `<repo>/pl_attachments`, `prompt_library.py:41-44`).

### The native pipeline side
- `PromptVersion` already has `system_prompt` + `user_prompt_template` — the exact pair library content needs (library rows leave `system_prompt` NULL, store the body in `user_prompt_template`). `PromptFixing`/`UserPromptPreference` are already `Integer`-keyed against `prompts.id` — **no FK retyping needed**.
- `Prompt.name` is `unique=True` but **nullable** (`database.py:194`, no `nullable=False`). Postgres treats multiple `NULL`s as distinct under a unique index (the codebase already relies on this — `PromptFixing` docstring, `database.py:232-235`). So library rows can leave `name` NULL and pipeline rows populate it, with no schema change and no partial-index trick.
- A native `audit_logs` table already exists (`AuditLog`, `database.py:475-504`) and is structurally close to `pl_audit_events`; reuse it additively.
- No soft-delete convention exists anywhere else (`deleted_at` appears only in `pl_models.py`). Adopting `deleted_at` on `prompts` is a new, deliberate convention scoped to this table.
- No "teams"/sharing-group concept exists elsewhere (access control is role-based + per-resource assignment). `teams` is a genuinely new primitive, ported as a real new table.
- **Out of scope, do not touch:** `UserPromptHistory` (`database.py:753-778`, saved free-text generation instructions) and `ClusterPrompt`/`cluster_prompts.py` (cluster-scoped snippets auto-injected into Style context) — different features with adjacent names.

### Pipeline prompt-resolution state (the generation path)
- **Three stages already read the DB registry.** CDD (`cdd.py:242`) and Blueprint (`blueprints.py:181`) call `build_prompt(..., db=db)`; Style reads the DB via `load_template("style_understanding", db=db)` (`style_service.py:53-54`), building its user prompt in code. **Generate is the only fully hard-coded stage** — `run_generation_job` (`generation_jobs.py`) never calls `build_prompt`/`load_template`, composing from constants (`PERSONA_PREFIX_TEMPLATE`, `LESSON_WITH_CONTEXT_SYSTEM/USER`).
- The fallback is **2-tier inside the loader** (`prompt_loader.load_template`: DB → `.md` file); the inline-constant tier lives in each caller's own `try/except` (`cdd.py:243`, `blueprints.py:183`, `style_service.py:56`).
- `resolve_fixed_prompt()` (scope-locking, `prompt_repository.py:133`) is only called from the Streamlit UI (`ui/generation_controls.py:508`), never from the FastAPI routers.
- Blueprint student/teacher is **one row gated by a `teacher_mode`/`student_mode` flag** (`blueprints.py:176-177`), not two independent assets.
- `build_prompt` runs `strict=False` (`prompt_builder.py:109`) and no caller passes `strict=True`, so `_REGISTRY` `required_vars` are **not enforced** — a missing variable is left as literal `{placeholder}` text (`prompt_builder.py:90`).

### Existing pipeline-management surface (what the console must consolidate)
- **A separate pipeline-prompt admin API** at `/api/v1/prompts` (`app/api/v1/routers/prompts.py`), with its own Pydantic schemas (`app/schemas/prompt.py`, versions are `version: str`) and permission gates (`prompts.view/create/manage`). It supports create (manual / from-template / AI-generate), list/search, read-with-active-version, delete, list versions, commit-new-version (`deploy_new_version`), activate/roll-back a version. It is **integer-keyed** and used **live** by the in-workflow Generate pickers (`InlinePromptControls.jsx`, `PromptLibraryPanel.jsx`). Distinct from `/api/v1/prompt-library`; must not be broken.
- **Two other prompt-editing UIs besides the Prompt Library:** (a) a fully-built React admin page `frontend/src/features/prompts/PromptsPage.jsx` (+ slice/thunks/service) targeting `/api/v1/prompts`, but **orphaned — not mounted in `routes.jsx`**, so unreachable dead code; (b) the **Streamlit** `promptops_app/pages/prompts.py` "Prompt Registry" page — the **only currently-reachable UI for editing pipeline prompts.**
- **⚠️ Latent correctness bug — seeded DB defaults are dormant.** `seed_data()` (`database.py:1652-1681`) creates default rows named `default_style_prompt` / `default_cdd_prompt` / `default_blueprint_prompt` / `default_generate_prompt` (keyed by `component_type` + `is_default=True`), but `_from_db` (`prompt_loader.py:182`) looks up by **exact `Prompt.name`** = the stem (`style_understanding` / `cdd_generation` / `blueprint_generation` / `content_generation`). These key sets don't intersect, so the seeded rows never satisfy a live lookup — generation resolves to the `.md`/inline tiers, **not** the DB. Editing a prompt through any management UI only affects generation if the row's `name` equals the stem the loader queries. **The console is meaningless until this is fixed** (Phase 8 prerequisite). This also means `CAS_PromptInject.md`'s "admin edits are used everywhere afterward" is only true for a stem-named row (noted there).
- **`is_default` is exposed on read schemas but writable through no endpoint**, and there is **no API to set/unset `PromptFixing` scope locks** (repo functions exist, called only from Streamlit). The console needs both as endpoints (Phase 8) and controls (Phase 7).

### Target prompt taxonomy

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

> The pipeline and the Prompt Library modelled a "prompt" differently, and after the merge one
> `prompts` table must serve both flows without either corrupting the other. Every schema choice in
> Phase 1 and every wiring choice in Phases 4–9 follows from the six decisions below. Read this before
> touching a migration.

### The two flows

| Dimension | **Pipeline flow** (Style / CDD / Blueprint / Generate) | **Library flow** (`pl_*`) |
|---|---|---|
| What a "prompt" *is* | A reusable **SYSTEM + USER pair** — `system_prompt` (role/rules) + `user_prompt_template` (form data filled in) | A single freeform **body** (`pl_prompts.content`) a user wrote to reuse/share |
| Identity | Small admin set; `name` unique + `component_type` (+ `variant`) | Freeform `title`, many-per-user, `category` + tags |
| Substitution syntax | Python `str.format` → `{course_name}` (`prompt_builder.py:83-90`) | `{{var}}` double-brace regex (`render_prompt_content`, `prompt_library_service.py:67-88`) |
| Resolution | 3-tier: DB `build_prompt`/`load_template` → `.md` file → inline constant (in caller `except`) | Direct row read by id; no fallback tiers |
| Versioning | Append-only, `deploy_new_version()` retires old + activates new, one `is_active` | `version_number` bump, instant-publish, one version row per save |
| Editing on a surface | Inline **override** (`*_override`) used once, never saved (`cdd.py:213-215`); persistent edits only via the admin page | **Every save is a version** — no ephemeral override concept |
| Governance | Admin/prompt-engineer only; no approval gate today | `visibility` (draft/team/global) + teams + reviews + change-requests |
| Injected context | Cluster prompts + active Style layer + CDD/Blueprint context stacked in by code (`build_style_context`) | None — library prompts are standalone text |

The merge does **not** blend these into one behavior. It puts both **kinds** of row in one table and
keeps their behaviors separate, selected by a discriminator. The six decisions define where they share
machinery and where they stay apart.

### Decision 1 — one table, two `prompt_kind`s; separation at the resolution + write-policy layers, not the UI

`prompts.prompt_kind ∈ {'pipeline','library'}` (Phase 1) is the master switch, driving taxonomy, which
columns are meaningful, which code path handles the row, and the permission tier. Because the Prompt
Library is the single management console for both kinds, the management UI must *surface* pipeline
rows, not hide them. Separation is therefore drawn at three layers:

- **Generation-resolution (hard separation):** the live path (`build_prompt`/`load_template`, `run_generation_job`) only ever resolves `prompt_kind='pipeline'` rows, by `component_type` + `is_default` (+ scope) — never a library row. A library prompt can never be injected into a generation call. This is the invariant that protects generation.
- **Write-policy (hard separation):** writes to any pipeline row require `prompt.pipeline.edit` and go through the approval gate (Decision 3); library rows stay self-service instant-publish. Enforced in the service/router regardless of which UI issued the write.
- **Browse/manage (unified, permission-scoped):** the console lists/searches across both kinds with `prompt_kind` as a filter/tab facet. **Non-admin authors never see pipeline prompts at all** — not in the default list, not via `kind=pipeline`, not via `kind=all`. The API strips pipeline rows from any non-admin response (server-side, Phase 4), and the pipeline tab does not render for them (Phase 7b). Surfacing pipeline rows to admins never changes what generation resolves.

### Decision 2 — library body → `user_prompt_template`, `system_prompt` stays NULL

- A library row stores its freeform body in **`user_prompt_template`** and leaves **`system_prompt` NULL**. `PromptVersion` already carries this pair, so no content column is added.
- The SYSTEM/USER split does not apply to library rows; a NULL `system_prompt` is the explicit signal "single-body (library) prompt," not missing data.
- **The two substitution syntaxes must never cross.** Pipeline uses `str.format` (`{name}`); library uses `{{var}}`. A library body run through `build_prompt` would have its `{{var}}` mangled; a pipeline template run through `render_prompt_content` would miss its `{name}` vars. The `prompt_kind` guard keeps them apart — library rows only ever render via `render_prompt_content`, pipeline rows only ever via `build_prompt`. Asserted in Phase 4 tests.
- `render_prompt` (`prompt_library.py:179`) reads the body from `user_prompt_template` (was `content`) after the cutover — a one-line source change, same `{{var}}` engine.

### Decision 3 — versioning: two write-paths, one `prompt_versions` table

Both kinds append rows to `prompt_versions`; they differ only in how a version becomes active:
- **Library:** instant-publish — write a new version, bump `version_number`, mark active immediately.
- **Pipeline:** gated — activation goes through `workflow_state` (`draft → in_review → approved → active`, Phase 8 approval gate). `deploy_new_version()`'s retire-old-activate-new stays the mechanism; the gate decides *when* activation is allowed.
- Shared invariants (Phase 1): `UniqueConstraint(prompt_id, version_number)` (missing today) and `version_number` backfilled from `created_at` order. `change_reason` doubles as the library "version note".

The pipeline's inline override (`*_override`, used-once-not-saved) is a generation-surface feature,
not a version and not a library concept — it stays exactly as-is and produces no version row.

### Decision 4 — governance: shared storage, split policy

- **Library rows** keep `visibility` (draft/team/global), team links (`prompt_team_links`), reviews (`prompt_reviews`), requests (`prompt_requests`) — instant-publish, self-service.
- **Pipeline rows** ignore visibility/teams/reviews; gated by `prompt.pipeline.edit` + the `workflow_state` approval gate. A non-admin cannot activate a pipeline version.
- **Audit is unified:** both kinds write to native `audit_logs` (`AuditLog`), extended additively with `actor_role`/`user_agent`/`changes`. The dict-based `compute_prompt_changes`/`redact_changes` helpers are model-agnostic and reused verbatim. `pl_audit_events` is dropped.

### Decision 5 — pipeline completion rides the same table

Once both kinds live in `prompts`, the long-standing pipeline gaps are just more `prompt_kind='pipeline'`
rows on the standard resolution path (all in Phase 8):
- **Generate** wired onto `build_prompt(..., db=db)` (`content_generation`/`quiz_generation`/`variant=interactive`) — the only hard-coded stage left.
- **Blueprint teacher/student** becomes two independent rows (`variant=student|teacher`), each separately versioned.
- **`resolve_fixed_prompt()`** wired into the FastAPI routers so course→cluster→project→global scope-locking works live.
- **`prompt_variables`** gives pipeline templates a DB-backed variable declaration; enabling `strict` makes it enforced instead of emitting literal `{placeholder}` text.

### Decision 6 — the Prompt Library IS the single prompt-management console

- **UI consolidation:** the live Streamlit `pages/prompts.py` and the orphaned React `features/prompts/PromptsPage.jsx` are both retired in favor of the Library console. The orphaned React page is deleted; the Streamlit page is decommissioned once the console reaches pipeline-editing parity (create / commit-version / activate / set-default) and is signed off. Until then Streamlit stays as the fallback editor — the "don't break anything" hinge.
- **API strategy — keep both routers now, converge later.** `/api/v1/prompts` (integer-keyed, permission-gated, used live by the Generate pickers) stays the pipeline admin + resolution API; `/api/v1/prompt-library` stays the library API (integer-keyed after Phase 5). Both are thin views over the one `prompts` table with kind-appropriate policy; the console frontend reads/writes both. Merging the routers is **not** done in this initiative (larger, riskier, no payoff, both needed live during transition) but **is a committed long-term follow-up** once the console is stable — the two-router state is transitional, not permanent.
- **Pipeline-aware editing in the console (Phase 7b):** (a) a dual editor (SYSTEM + USER vs the single library body); (b) `component_type` / `variant` / `is_default` controls; (c) a `workflow_state` control + status badges, reusing the `AdminRequestDetailPage` status-select pattern; (d) kind-aware variable syntax (`{single}` for pipeline vs `{{double}}` for library — the `extractVarNames`/`fillPromptContent`/preview utils are `{{double}}`-hardwired today and must branch on kind); (e) admin-only gating via a new `canManagePipelinePrompts` helper.
- **New management capabilities (Phase 8 backend):** endpoints to set `is_default` per `component_type`, and to set/unset `PromptFixing` scope locks — neither exposed today.
- **Name-key alignment (Phase 8 prerequisite):** pipeline resolution moves to `component_type` + `is_default` (+ scope), fixing the dormant-seed bug — without it the console edits rows generation ignores.

### The reconciled lifecycle

```
              ┌──────────────  Prompt Library console (one UI, kind facet)  ──────────────┐
              │   pipeline tab (admin, gated+approval)      library tab (self-service)      │
              └───────────────┬───────────────────────────────────────┬────────────────────┘
                              ▼                                         ▼
                         ┌───────────────────────  prompts (one table)  ───────────────────────┐
                         │  prompt_kind='pipeline'                 prompt_kind='library'         │
                         │  name, component_type, variant          title, category, visibility   │
                         │  system_prompt + user_prompt_template   user_prompt_template only      │
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

**Acceptance:** Phase 1's schema provides a concrete home for every row in the "two flows" table, and
each of the six decisions maps to specific Phase 1 columns / Phase 4-5 code / Phase 7 UI / Phase 8
wiring — no behavior from either flow is silently dropped or blended, and the Library can act as the
single console without ever letting a library row leak into a generation call.

---

## Phase 0S — Safety hardening (PREREQUISITE — before Phase 1)

Nothing that touches the DB or the generation code starts until these are in place. This is the
difference between "hope it works" and "we can prove it and undo it." Rationale + details in the
"Safety, reversibility & pre-flight" section above.

- [ ] **Rehearsal environment.** Stand up a disposable Postgres (local container or a staging RDS) seeded from a prod snapshot, so every migration is rehearsed on prod-shaped data before touching prod. (Today `docker-compose.yml` references a local Postgres but doesn't define one; the app talks to RDS via `.env` — there is no throwaway DB.)
- [ ] **Fix Alembic wiring** (blocker #1): repoint `migrations/env.py` `target_metadata` to `promptops_app.database.Base.metadata`; confirm `alembic revision --autogenerate` produces a sane diff (not "drop everything") against the rehearsal DB.
- [ ] **Reconcile `create_all` vs Alembic** (blocker #2): make Alembic own the schema — capture current schema as a baseline "initial" revision, then disable/prod-gate `create_all()` + the raw `ALTER`/`CREATE INDEX` blocks in `init_db()`. Verify a clean `alembic upgrade head` on an empty DB reproduces today's schema exactly.
- [ ] **Characterization tests (the safety net).** Generation and the prompt library have ZERO automated coverage today — a table repoint or resolution change passes CI green. Using the existing (unused) `mock_llm` fixture, add golden-output tests through `build_prompt`/`load_template`, the CDD/Blueprint/Style resolution paths, `run_generation_job`, and the prompt_library router CRUD/version/render endpoints. These capture *today's* behavior so any Phase 4/5/8 regression is caught automatically. Wire them into the CI gate.
- [ ] **Backup/restore drill.** Execute the RDS snapshot + PITR restore path once end-to-end on the rehearsal DB, so recovery is a known, timed procedure. Add a `pg_dump` step to the migration runbook (not just the recommendation in Phase 2/6).
- [ ] **Migration round-trip.** For every migration written in Phase 3, run `upgrade → downgrade → upgrade` on the rehearsal DB and confirm the schema and a golden generation both survive intact.

**Acceptance:** a rehearsal DB exists on prod-shaped data; Alembic autogenerate is sane and `create_all` no longer races it; characterization tests capture current generation + PL behavior and run in CI; the snapshot/restore path has been executed once successfully. Only then does Phase 1 begin.

---

## Phase 1 — Target schema design (finalize before writing migrations)

### `prompts` — new columns

| Column | Type | Notes |
|---|---|---|
| `prompt_kind` | `String(20) NOT NULL DEFAULT 'library'` | `'pipeline'` \| `'library'`. Single field driving taxonomy and permission tier. |
| `title` | `String(300) NULLABLE` | Library display title. Required at the service layer when `prompt_kind='library'`, not at DB level. |
| `category` | `String(100) NULLABLE` | Freeform library category — distinct from `component_type` (pipeline-only). |
| `visibility` | `String(20) NOT NULL DEFAULT 'draft'` | `'global'` \| `'team'` \| `'draft'`. |
| `variant` | `String(50) NULLABLE` | Pipeline-only: `student`/`teacher` (blueprint), `lesson`/`assessment`/`interactive` (generate). NULL for library rows and for CDD/Style. |
| `parent_id` | `Integer NULLABLE, FK -> prompts.id ON DELETE CASCADE`, indexed | Self-referential "follow-up prompt" hierarchy (library). |
| `last_used_at` | `DateTime NULLABLE` | |
| `deleted_at` | `DateTime NULLABLE`, indexed | Soft delete — new convention scoped to this table. |

`name` is already nullable, so there is no nullability migration. Its `unique=True` is kept — Postgres
allows multiple NULLs under a standard unique index. Rule: library rows leave `name` NULL, pipeline
rows populate it.

Add a **partial unique index** `ON prompts(component_type, variant) WHERE is_default = true AND
prompt_kind = 'pipeline'` — guarantees exactly one default prompt per pipeline stage/variant (today
only enforced by convention).

### `prompt_versions` — new columns

| Column | Type | Notes |
|---|---|---|
| `version_number` | `Integer NULLABLE` initially, backfilled then `NOT NULL` | Numeric ordering key, replacing free-text `version`-string parsing. `version` (e.g. `"v3"`) stays as the display label for the pipeline admin UI (`app/schemas/prompt.py` expects `version: str`). |
| `workflow_state` | `String(20) NOT NULL DEFAULT 'active'` | `draft` \| `in_review` \| `approved` \| `active`. Enforced only for pipeline rows (Phase 8 gate); library rows stay `active`. The `DEFAULT 'active'` is correct for the Phase 3 backfill and library rows; **new pipeline versions are inserted at `'draft'` explicitly** by the approval-gate service (Phase 8), not left to the column default. |

Add `UniqueConstraint(prompt_id, version_number)` — missing today, a real pre-existing gap. `change_reason`
is reused as-is for the library "version note".

### New tables (all Integer PK, no tenant column, snake_case names — no `pl_` prefix)

| Table | Columns | Replaces |
|---|---|---|
| `prompt_tags` | `prompt_id FK CASCADE, tag String(100)` — composite PK | `pl_prompt_tags` |
| `prompt_variables` | `id PK, prompt_id FK CASCADE, name String(100) NOT NULL, label String(200), hint Text, sort_order Integer default 0` | `pl_prompt_variables`. Also gives pipeline templates a DB-backed variable declaration, replacing the static `_REGISTRY` dict in `prompt_loader.py`. |
| `prompt_attachments` | `id PK, prompt_id FK CASCADE, original_name String(300), stored_name String(500), size_bytes BigInteger, uploaded_by String(100), uploaded_at DateTime` | `pl_attachments`. Storage dir → `PROMPT_ATTACHMENTS_DIR` (default `./prompt_attachments`). |
| `teams` | `id PK (Integer autoincrement), name String(100) UNIQUE NOT NULL, created_by String(100), created_at DateTime` | `pl_teams`. `pl_teams.id` was a `String(20)` slug; native `id` is Integer, so the migration maps slug → int and rewrites `prompt_team_links.team_id`. The slug is preserved in `name`, so nothing depends on the id shape. |
| `prompt_team_links` | `prompt_id FK CASCADE, team_id FK CASCADE` — composite PK | `pl_prompt_teams`. Deliberately drops the redundant scalar `team_id` FK that existed on `pl_prompts` alongside the M:N table. |
| `prompt_reviews` | `id PK, prompt_id FK CASCADE, username String(100) NOT NULL, rating SmallInteger NOT NULL, feedback Text, created_at, updated_at`, `UniqueConstraint(prompt_id, username)` | `pl_reviews`. A table named `reviews` already exists for content-block review — `prompt_reviews` stays distinct. |
| `prompt_requests` | `id PK, title String(300) NOT NULL, description Text, type String(20) NOT NULL DEFAULT 'new', prompt_id FK SET NULL NULLABLE, requested_by String(100) NOT NULL, status String(20) NOT NULL DEFAULT 'open' indexed, admin_notes Text, created_at, updated_at` | `pl_prompt_requests` |

### `audit_logs` — additive columns only

| Column | Type | Notes |
|---|---|---|
| `actor_role` | `String(64) NULLABLE` | |
| `user_agent` | `String(512) NULLABLE` | |
| `changes` | `JSON NULLABLE` | Structured diff from the existing `compute_prompt_changes`/`redact_changes` utilities (dict-based, reusable as-is). Existing `metadata_json` left untouched to avoid risk to other audit consumers. |

`prompt_fixings` and `user_prompt_preferences`: no changes — already Integer-keyed against `prompts.id`.

**Acceptance:** schema design reviewed against every capability in the feature inventory (CRUD,
versioning, tags, team visibility, variables, attachments, reviews, requests, follow-ups, audit,
search/filter) — each has a concrete home before any migration is written.

---

## Phase 2 — Data check & migration script

- [ ] Before writing any migration, check staging/dev databases for actual rows in `pl_*` tables. Expect near-zero (same-day commit), but verify — do not assume.
- [ ] If real rows exist: write `scripts/migrate_pl_to_native_prompts.py` (idempotent, `--dry-run` first) to carry them into the native tables — building three id-remap maps (UUID `pl_prompts.id` → new int; `pl_teams.id` slug → new int; each UUID child-row id → new int), then rewriting every FK (`parent_id`, `prompt_team_links.team_id`, version/tag/variable/attachment/review/request `prompt_id`). Preserve `created_at`/`created_by`, set `prompt_kind='library'`, map `content` → `user_prompt_template` with `system_prompt` NULL (Decision 2), resolve self-referential `parent_id` roots-then-children.
- [ ] If no real rows exist (expected): skip the carry-over script, record the verification query + result here, proceed to Phase 3.
- [ ] Regardless: take a full SQL dump of all 9 `pl_*` tables before Phase 6 drops them.

**Acceptance:** either (a) a verified-empty finding is recorded here, or (b) the carry-over script ran with dry-run output reviewed.

---

## Phase 3 — Alembic migration

- [ ] Single migration (or small ordered set) implementing all of Phase 1's additive changes: new columns on `prompts`/`prompt_versions`/`audit_logs`, the new tables, the partial unique index, and `UniqueConstraint(prompt_id, version_number)`.
- [ ] Backfill `version_number` for existing `prompt_versions` (sequential per `prompt_id`, ordered by `created_at`), then set `NOT NULL`.
- [ ] Backfill `prompt_kind = 'pipeline'` for all existing `prompts` rows (all pipeline templates today).
- [ ] **Pre-flight before constraints** (see Safety section): confirm the sequential `version_number` backfill leaves no duplicate before adding `UniqueConstraint(prompt_id, version_number)` (the seeded `lesson_generator` has 2 versions — a `DEFAULT 1` backfill would collide); run the `is_default` duplicate check on prod and resolve before creating the partial unique index.
- [ ] Wrap the migration in a **single transaction** (transactional DDL) so a mid-way failure rolls back cleanly; for the unique index use `CREATE UNIQUE INDEX CONCURRENTLY` or run during a quiet window / with the app quiesced to avoid the `ACCESS EXCLUSIVE` lock stalling behind the connection pool.
- [ ] Do **not** touch `pl_*` tables here — that's Phase 6.
- [ ] Apply to the Phase 0S rehearsal DB first; run the `upgrade → downgrade → upgrade` round-trip; confirm existing CDD/Blueprint generation still works unmodified and the characterization tests pass.

**Acceptance:** migration applies cleanly; all existing endpoints (pipeline admin, CDD/Blueprint generation, Prompt Library on its old `pl_*` backing) work exactly as before — nothing observable changes yet.

---

## Phase 4 — Service layer rewrite

Goal: `promptops_app/services/prompt_library_service.py` operates against the native models instead of
`PLPrompt`/etc., with **zero change to its public function signatures** so the router doesn't need a
parallel rewrite in the same step.

- [ ] Repoint every query/import from `pl_models` to the native model (`Prompt` filtered to `prompt_kind='library'`, `PromptVersion`, `PromptTag`, `PromptVariable`, `PromptAttachment`, `Team`, `PromptTeamLink`, `PromptRequest`, `PromptReview`, `AuditLog`).
- [ ] Remove the manual `str(uuid.uuid4())` ID assignment (the 9 id-generation call sites moving here from the router) — let the DB autoincrement `Prompt.id`/child ids.
- [ ] `build_prompt_snapshot()` / `compute_prompt_changes()` / `redact_changes()` — no change needed (dict-based, model-agnostic).
- [ ] Every write path writes an `AuditLog` row via the existing audit helper, populating the new `actor_role`/`changes` columns.
- [ ] List/search default to `prompt_kind='library'` and accept a `kind` filter (`library` | `pipeline` | all). **Server-side authorization: a caller without `prompt.pipeline.edit` never receives any pipeline row** — the service strips them regardless of requested `kind` (no 403 that would leak existence). The generation-resolution path is untouched and still only ever resolves pipeline rows.
- [ ] Enforce the write-policy split: writes to a pipeline row require `prompt.pipeline.edit` and honor the approval gate (Decision 3/6); library writes stay instant-publish. Select the `{single}` vs `{{double}}` render path by `prompt_kind` so the substitution engines never cross (Decision 2).
- [ ] Unit tests (`tests/unit/`): one per rewritten function asserting identical output shape to the pre-rewrite version; plus a test that a `kind='pipeline'` request never returns library rows into a generation resolver and vice-versa.

**Acceptance:** all service-layer unit tests pass against the native tables with output identical in shape to the old `pl_*`-backed implementation (values differ only in `id` type — int vs UUID string).

---

## Phase 5 — Router rewrite

- [ ] `app/api/v1/routers/prompt_library.py`: change every path param from `str` to `int` (≈15 endpoints).
- [ ] Remove any remaining direct `pl_models` imports from the router (should be none if Phase 4 encapsulated data access; if the router bypasses the service anywhere, fix that here).
- [ ] Attachment upload/download paths: switch storage directory to `PROMPT_ATTACHMENTS_DIR`.
- [ ] Manual smoke test of every endpoint against the native tables (list/search/create/update/delete/duplicate; create version; tags; visibility/team filtering; render; attachments; reviews; requests; audit list + CSV export; teams CRUD).

**Acceptance:** every Prompt Library endpoint works end-to-end against the native tables; `grep -rn "pl_models\|PLPrompt\|PLTeam\|PLReview\|PLAttachment" app/ promptops_app/` returns nothing outside `pl_models.py` and the obsolete migration script.

---

## Phase 6 — Drop `pl_*` tables

- [ ] Confirm Phase 2's dump exists and is retained somewhere durable — the safety net given the tables were never Alembic-managed.
- [ ] Alembic migration: explicit `DROP TABLE IF EXISTS pl_prompts, pl_prompt_versions, pl_prompt_teams, pl_prompt_tags, pl_prompt_variables, pl_attachments, pl_teams, pl_prompt_requests, pl_reviews, pl_audit_events CASCADE` — required because these were `create_all`-only and will otherwise persist as orphans.
- [ ] Delete `promptops_app/pl_models.py`.
- [ ] Delete or archive `scripts/migrate_prompt_library.py` — it imports `pl_models` and becomes dead code that fails on import once `pl_models.py` is gone.
- [ ] Delete the old `PL_ATTACHMENTS_DIR` directory (after confirming Phase 2/4 handled any carry-over).
- [ ] Repo-wide grep for `pl_` / `PLPrompt` / `PLTeam` etc. — zero remaining references outside this plan and git history.

**Acceptance:** fresh `alembic upgrade head` on an empty DB produces only the native, enhanced schema — no `pl_*` tables anywhere; `Base.metadata.tables` lists no `pl_` name after `pl_models.py` is deleted.

---

## Phase 7 — Prompt Library console: frontend

Depends on Phase 8's backend endpoints (name-key fix, `is_default`/scope-lock endpoints, `workflow_state`
transitions) — see the rollout order in Phase 10. Split so nothing breaks: **7a** is pure compatibility
(ship right after Phase 5); **7b** is the new console capability (after Phase 8's endpoints).

### Phase 7a — ID-compatibility pass
- [ ] Audit `frontend/src/features/promptLibrary/` (api clients, pages, components) for any UUID-format assumption. Investigation found none — ids are opaque strings end-to-end — but re-verify against the shipped cutover.
- [ ] Update any route definitions in `routes.jsx` enforcing UUID shape (none found today).
- [ ] Manual walkthrough: create/edit/delete a prompt, tags, teams, review, request, attachment, audit log — all render correctly against integer IDs.

### Phase 7b — Make the Library the console for pipeline prompts (Decision 6)
- [ ] **Kind-aware prompt utilities** — add a `{single}`-brace variant of `extractVarNames`/`fillPromptContent` and `PromptDetailPage`'s preview regex, selected by `prompt_kind`, so pipeline prompts get correct variable detection/preview/fill while library prompts keep `{{double}}` unchanged. Highest-risk shared change — cover with tests before wiring pages.
- [ ] **`PromptFormPage` dual editor** — a second `system_prompt` textarea shown only for pipeline rows (single body maps to `user_prompt_template`); `component_type` / `variant` / `is_default` controls; a `workflow_state` control (reuse the `AdminRequestDetailPage` status-select pattern). Gate all pipeline-editing controls behind a new `canManagePipelinePrompts` helper (admin-only).
- [ ] **`PromptDetailPage`** — render the SYSTEM + USER pair for pipeline rows, the `{single}` preview path, a `workflow_state` badge + transition buttons, and a version-history viewer (not present today).
- [ ] **List surfaces** — add a `kind` (library | pipeline) facet/tab rendered only for admins; non-admins see no pipeline tab and (per Phase 4) no pipeline rows. Show a `component_type`/`variant`/`is_default`/`workflow_state` badge for pipeline rows. Wire `kind` through `buildFilterParams`/`fetchMeta`. UI hiding is defense-in-depth over the server-side strip.
- [ ] **API client** — extend `api/prompts.js` payloads and `fetchPrompt` mapping for `prompt_kind`/`system_prompt`/`component_type`/`variant`/`is_default`/`workflow_state`; add client methods for the new Phase 8 endpoints. The console reads both `/api/v1/prompts` and `/api/v1/prompt-library`.
- [ ] **Retire the duplicate editors** — delete the orphaned `features/prompts/PromptsPage.jsx` (+ slice/thunks/service) once the console covers create/version/deploy; keep the in-workflow Generate pickers. Decommission Streamlit `pages/prompts.py` only after 7b parity sign-off (Phase 10 gate).

**Acceptance:** (7a) full walkthrough of the library-kind UI against the new backend, no console errors, no broken ID behavior. (7b) an admin can, entirely within the console, create/edit a pipeline prompt with a system+user pair, set it default for its `component_type`, move it through the approval states, and see a subsequent live CDD/Blueprint/Style/Generate call use the edited DB row; a non-admin cannot see or invoke any pipeline-editing control; no `{single}`/`{{double}}` regression on library prompts.

---

## Phase 8 — Pipeline registry completion + management-console backend

Several items here are prerequisites for the Phase 7b console — do the endpoint/loader work before
wiring the console UI.

- [ ] **⚠️ PREREQUISITE — fix the dormant-seed name-key mismatch.** Change pipeline resolution to select the active version of the `is_default=True` row for the requested `component_type` (via `get_default_prompt`), with scope override via `resolve_fixed_prompt`, treating the stem `name` only as a legacy secondary key. Verify: edit the default CDD row in the DB → a live CDD generation uses the edited text. Without this the console edits rows generation ignores.
- [ ] **New management endpoints on `/api/v1/prompts`** (guard all with `prompt.pipeline.edit`): set/clear `is_default` for a `(component_type, variant)`; set/unset a `PromptFixing` scope lock; `workflow_state` transition (draft→in_review→approved→active).
- [ ] Wire `resolve_fixed_prompt()` into the live FastAPI routers (`cdd.py`, `blueprints.py`, the not-yet-wired generations/style paths) instead of only Streamlit.
- [ ] Split Blueprint's `teacher_mode` flag into two independent rows (`variant=student` / `variant=teacher`), each separately versioned.
- [ ] Wire **Generate** (`content_generation`, `quiz_generation`, new `variant=interactive`) onto the DB-backed path — the only fully hard-coded stage. Keep `PERSONA_PREFIX_TEMPLATE`/`LESSON_WITH_CONTEXT_*` as the file/inline fallback tier only.
- [ ] Enforce registry variable declarations: pass `strict=True` (or an equivalent validation pass) so declared required vars raise instead of emitting literal `{placeholder}` text.
- [ ] Seed one `is_default=True` row per taxonomy line (Cluster/Course rows seeded but flagged backlog, not wired to a live endpoint).
- [ ] Approval gate: pipeline-tier version activation goes through `workflow_state`; library-tier rows keep instant-publish.
- [ ] New permission `prompt.pipeline.edit` (admin/prompt-engineer only), distinct from the Library's existing permissions, gating writes to any pipeline row.

**Acceptance:** `grep` confirms hardcoded prompt constants appear only in `except` fallback blocks; **editing the default row for a `component_type` demonstrably changes live generation output** (the name-key fix); a course-level `prompt_fixings` override takes effect in a live generation call; `is_default` and scope locks are settable via API; a non-admin cannot activate a pipeline-tier version via the API.

---

## Phase 9 — Shared fragment library

- [ ] `prompt_fragments` / `prompt_fragment_versions` (native-named) for `guardrails`, `context_header`, `persona_tone`, `output_contract_json`, `output_contract_markdown`, `regenerate_wrapper`.
- [ ] Composer assembling `guardrails + context_header + persona_tone (if applicable) + stage_unique_template + output_contract`.
- [ ] Migrate `PERSONA_PREFIX_TEMPLATE`/`DEFAULT_STYLE_GUIDE` (`prompt_templates.py:6,16`) into fragment rows; file constants remain the fallback tier only.

**Acceptance:** editing the `guardrails` fragment once updates output across CDD/Blueprint/Generate without touching per-stage templates.

---

## Phase 10 — Testing & rollout

- [ ] Full regression: generate one CDD, one Blueprint (both variants), one lesson, one assessment in staging; diff against baseline. Include a before/after proving the name-key fix (edit the default CDD prompt via the console → next CDD generation reflects it).
- [ ] Full Library regression per 7a + 7b walkthroughs, plus automated coverage for the rewritten service functions (Phase 4) and the kind-aware `{single}`/`{{double}}` utils.
- [ ] Confirm the Phase 2/6 backup of `pl_*` data is retained (recommend ≥2 weeks post-cutover).
- [ ] **Staged rollout** (each arrow is a verify-in-staging gate; take an RDS snapshot before every schema phase; never start a stage until the prior is signed off):
  0. **Phase 0S — safety hardening (rehearsal DB, Alembic fix, characterization tests, backup drill)** →
  1. Phase 1-3 — schema additive + inert →
  2. Phase 4-5 — service/router cutover to native tables →
  3. Phase 7a — frontend ID-compat pass →
  4. Phase 6 — drop `pl_*` (only after 7a signed off) →
  5. Phase 8 — name-key fix **first**, then new endpoints, then approval gate / scope wiring / Generate wiring →
  6. Phase 7b — console pipeline build-out →
  7. Decommission Streamlit `pages/prompts.py` (only after 7b parity sign-off) →
  8. Phase 9 — shared fragments (last; purely additive).
- [ ] Rollback plans:
  - **Any schema phase:** the pre-phase RDS snapshot + PITR is the guaranteed restore path (no in-repo DB backup automation exists — take the snapshot manually).
  - **Phases 1-3:** additive only — old code ignores the new columns/tables; Alembic `downgrade` removes them.
  - **Phases 4-5:** `pl_*` + old service/router still intact — `git revert` the cutover commit(s), zero data lost.
  - **Phase 6 onward:** the one-way door — no automated rollback; recovery is the pre-Phase-6 dump / RDS snapshot. Do not run Phase 6 until 7a is signed off.
  - **Phase 8 name-key fix:** feature-flagged and guarded behind the DB→file→inline fallback, so a bad default-row resolution degrades to the shipped `.md`/constants rather than failing generation; keep Streamlit live as the manual escape hatch until 7b is signed off.

---

## Locked decisions

- **Table base:** native CAS `prompts` tables are canonical; port `pl_*` features onto them; retire `pl_*`. (See "Table-base decision".)
- **Console API strategy:** keep two routers for this initiative; converge into one prompt API as a committed long-term follow-up after the console is stable.
- **Non-admin visibility:** non-admins never see pipeline prompts — stripped server-side (Phase 4) + pipeline tab not rendered (Phase 7b).

## Open questions

- [ ] Phase 2: is there any real dev/staging data in `pl_*` today, or is it verified empty?
- [ ] Approval workflow (Phase 8): single-approver or two-person rule for pipeline-tier activation?
- [ ] Streamlit decommission timing: retire at 7b sign-off, or keep as a break-glass admin tool longer?
- [ ] Should `teams` be a general-purpose sharing primitive for future non-prompt features, or kept prompt-scoped?
- [ ] Should `ClusterPrompt` (auto-injected into Style context) eventually converge with `prompt_fragments` (Phase 9), or stay distinct?
- [ ] Are Cluster/Course AI-assist prompts (Phase 8 backlog) in scope for this initiative or a separate follow-up?
- [ ] `variant` for Style/CDD: confirm no near-term need before the partial-unique-index `(component_type, variant)` shape is locked (NULL `variant` must behave correctly in that index).

---

## Revision History

- **2026-07-03** — Database-safety pass. Audited deploy/backup posture, migration mechanics, and test coverage. Found two blockers (Alembic `target_metadata` mis-wired at the wrong `Base` → autogenerate would drop all tables; `create_all()` races Alembic on new tables) and three gaps (no rehearsal DB, zero test coverage on generation + prompt library, no automated DB backup), plus constraint data-landmines (seeded `lesson_generator` breaks a naïve `version_number` backfill; `is_default` has no DB guard). Added the "Safety, reversibility & pre-flight" section (honest framing, per-phase undo table, RDS snapshot/PITR backstop, pre-flight data checks) and **Phase 0S — Safety hardening prerequisite**; folded landmines into Phase 3 and the RDS reality + Phase 0S into Phase 10 rollout/rollback. No implementation started.
- **2026-07-03** — Finalized and consolidated. Verified all "Current state" facts against code (four read-only audits: pipeline + library data models, pipeline management surface, library-UI fitness). Established the unified prompt model (Phase 0.5, six decisions), the Library-as-console vision, and the fair table-base analysis. Locked decisions: native tables as base; keep two routers (converge long-term); non-admins never see pipeline prompts. Discovered and scheduled the dormant-seed name-key correctness bug (Phase 8 prerequisite) and the two redundant prompt editors (orphaned React page + Streamlit). Rewrote the document into this clean single source of truth. No implementation started.
- **2026-07-03** — Direction reversed. Original plan merged native tables *into* `pl_*`; new direction keeps native tables as base and retires `pl_*`. Reason: `pl_*` is newly introduced (same-day commit `42e076e`), empty, with no Alembic history — the lower-cost, lower-risk side to retire; native tables already have the closer-fitting schema and are what live generation depends on.
- **2026-07-02** — Initial version created (original merge direction).

## Progress Log

_Append one entry per work session. Keep entries short — link to commits/PRs rather than duplicating detail._

| Date | Session focus | Phases touched | Outcome |
|---|---|---|---|
| 2026-07-02 | Plan authored | — | Initial version (original merge direction) |
| 2026-07-03 | Design finalized | 0, 0.5, 1–10 | Reversed direction to native-tables-as-base; ran four code audits; built the unified prompt model + Library-as-console design + fair table-base analysis; locked three decisions; scheduled the name-key fix and editor consolidation; consolidated the whole document into a clean SSOT. No implementation started |
| 2026-07-03 | Database-safety pass | Safety section, 0S, 3, 10 | Audited deploy/backup/migration/test posture; found 2 blockers (Alembic mis-wired, create_all races Alembic) + 3 gaps (no rehearsal DB, zero generation/PL test coverage, no DB backup automation) + constraint data-landmines; added Safety/reversibility section, Phase 0S prerequisite, pre-flight checks in Phase 3, RDS-snapshot rollback in Phase 10. No implementation started |
