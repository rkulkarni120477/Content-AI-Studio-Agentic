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
| Live data / criticality | Holds seeded defaults + admin pipeline prompts (8 prompts / 7 versions); **the table generation actually reads** | **NOT empty** (verified 2026-07-05): 20 admin-authored library prompts + 6 teams + 46 tags + 32 variables + 2 requests = 126 rows | **CAS** as base — but the `pl_*` side must now be **migrated, not discarded** |
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
- The entire Prompt Library feature — `promptops_app/pl_models.py`, `app/api/v1/routers/prompt_library.py`, `promptops_app/services/prompt_library_service.py`, and the frontend at `frontend/src/features/promptLibrary/` — landed in a single commit (`42e076e`, "merging PL into CAS") on 2026-07-03. It is the newest thing in the repo: no multi-release deprecation window to honor. **However, it is NOT empty (verified against prod RDS, 2026-07-05):** 20 admin-authored library prompts (a curated Cengage CTE authoring playbook created 2026-06-02 — freeform, first-person, `{{double}}`-brace vars; categories Course/Storyboard/Blueprint Development, Follow-Up, Tagging), 6 teams (P1–P6), 46 tags, 32 declared variables, 2 requests — **126 rows across `pl_*`**. This real content **MUST be migrated, not dropped** (Phase 2 carry-over is mandatory, not skippable).
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

- [x] **Rehearsal environment.** Done 2026-07-05: `scripts/rehearsal_db.sh` (dump / up / restore / reset / verify / url / down) manages a disposable `postgres:17` container (`cas-rehearsal-db`, port 55432) seeded from a full prod `pg_dump`. Row parity verified: 47 tables, `prompts`=8, `prompt_versions`=7, `pl_*` total=126. `backups/` is git-ignored (holds prod data).
- [x] **Fix Alembic wiring** (blocker #1): done — `migrations/env.py` now targets `promptops_app.database.Base.metadata` and explicitly imports `pl_models` (else autogenerate would propose dropping the 10 `pl_*` tables prematurely; remove that import in Phase 6). Also added the missing `migrations/script.py.mako` and fixed `alembic.ini`'s post-write hook (`ruff.executable`). Autogenerate against the rehearsal DB produces a sane drift diff — NOT drop-everything (see discovery note below on `tenants`).
- [x] **Reconcile `create_all` vs Alembic** (blocker #2): done — baseline revision `000100000001` executes a schema-only prod dump (`migrations/versions/baseline_schema.sql`) on an empty DB and **self-stamps** (no-op) when the schema already exists, so the deploy pipeline's `alembic upgrade head` is safe against prod in any order. Verified: `upgrade head` on an empty DB reproduces the prod schema **byte-identically** (normalized pg_dump diff empty); self-stamp on the rehearsal clone left all data intact. `init_db()`'s `create_all` + raw `ALTER`/`CREATE INDEX` are now gated behind `DB_AUTO_DDL` (default OFF); the idempotent DML backfills still run (Postgres only). App boots green against the rehearsal DB with DDL gated.
- [x] **Characterization tests (the safety net).** Done — 67 tests in `tests/characterization/` covering: `prompt_builder` render/validate semantics; `prompt_loader` 2-tier resolution incl. **the dormant-seed bug captured as a test** (`TestDormantSeedBug` — flip it when Phase 8 fixes resolution); CDD/Blueprint/Style router-level resolution with a recording LLM mock (file tier vs stem-named DB row vs `*_override`, override-produces-no-version); `run_generation_job` (hard-coded persona+constants, `prompt_name=""` written, storyboard scrub, extra-instructions append); Prompt Library API contract (CRUD/version-bump/soft-delete/render/`{{var}}`/teams/RBAC/audit shapes — IDs asserted opaque so the Phase 5 int-ID switch doesn't break them). Wired into CI as a dedicated step. **Note:** the pre-existing suite was entirely un-runnable (Postgres-only pool kwargs in both `create_engine` sites crashed under SQLite; conftest created tables on the wrong (empty) `Base`; in-memory SQLite lacked `StaticPool`; `User(password=)` vs `password_hash`; stale SHA-256 hash assertions; missing NOT NULLs in fixtures) — all fixed; full suite now **147 passed**.
- [x] **Backup/restore drill.** Logical path executed end-to-end and timed (dump ≈1 s, restore ≈2 s on the 16 MB DB; full drop→recreate→restore cycle via `rehearsal_db.sh reset`). `MIGRATION_RUNBOOK.md` created with the pg_dump step, RDS-snapshot console procedure, and per-phase gates. ⚠️ Remaining: the RDS **console snapshot-restore** has not been executed (no AWS CLI/console access from this box) — schedule it before Phase 6 (tracked in the runbook).
- [x] **Migration round-trip.** Executed 2026-07-05 for the Phase 3 revision `000100000002` on a fresh prod clone: `upgrade → downgrade → upgrade` clean, zero residuals after downgrade, data intact, full suite green at every step. The same drill applies to every future revision (procedure in `MIGRATION_RUNBOOK.md`).

**Acceptance:** a rehearsal DB exists on prod-shaped data; Alembic autogenerate is sane and `create_all` no longer races it; characterization tests capture current generation + PL behavior and run in CI; the snapshot/restore path has been executed once successfully. Only then does Phase 1 begin.

**Phase 0S discoveries (2026-07-05):**
- **Orphaned multi-tenancy schema in prod.** The live DB contains a `tenants` table plus `tenant_id` columns (+ `idx_*_tenant_id` indexes) on 9 tables (`users`, `projects`, `prompts`, `documents`, `audit_logs`, `generation_jobs`, `llm_usage_logs`, `styles`, `central_repositories`), and `users.project_id` / `users.is_platform_admin` — none of it present in any model. Remnants of an abandoned experiment; live in prod, invisible to code. Kept as-is in the Alembic baseline (faithful capture); autogenerate will keep flagging it as drift until an explicit cleanup migration removes it (a candidate follow-up, NOT bundled into this initiative). Added to Open questions.
- **Also drift, captured in baseline, not urgent:** the ~45 raw-SQL indexes from `init_db()` are not declared on the models; `server_default` mismatches (`blocks.position`, `blocks.version_num`, `prompts.is_default`, `workflow_events.comment`); `courses.cluster_id` was added by raw ALTER *without* its FK constraint. Aligning models with the DB (or vice versa) is follow-up hygiene; until then, review autogenerate output and strip these known-drift ops from new revisions.
- **Plan correction — variable syntax.** The registry/pipeline templates (`.md` files + DB rows rendered by `prompt_builder.render`) use **`{{double}}`-brace** substitution — the SAME syntax family as the Prompt Library's `render_prompt_content` (regexes differ only in identifier rules). The `{single}` + `.format()` syntax exists **only** in the hard-coded constants: `prompt_templates.py` (`PERSONA_PREFIX_TEMPLATE`, `LESSON_WITH_CONTEXT_*`, seeded `default_*` prompt bodies) and each router's inline-fallback `except` blocks. Decision 2's "two syntaxes must never cross" therefore applies to the *legacy-constant/seeded-content* boundary, not builder-vs-library engines; Phase 7b's "kind-aware `{single}`/`{{double}}` frontend utils" matter for pipeline rows whose bodies still carry `{single}` text (e.g. the seeded defaults converted from constants) and for Phase 8's Generate wiring, where the `{single}` constants must be converted to `{{double}}` templates when they become DB rows.

---

## Phase 1 — Target schema design (finalize before writing migrations)

### `prompts` — new columns

| Column | Type | Notes |
|---|---|---|
| `prompt_kind` | `String(20) NOT NULL DEFAULT 'pipeline'` | `'pipeline'` \| `'library'`. Single field driving taxonomy and permission tier. Default changed from `'library'` at Phase 3 implementation: between the Phase 3 schema deploy and the Phase 4 cutover the only live writers are pipeline admin paths that don't set the column — a `'library'` default would mislabel rows created in that window. The Phase 4+ service sets the kind explicitly on every create, so the column default only ever matters for legacy writers, which are all pipeline. |
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
only enforced by convention). **Must be declared `NULLS NOT DISTINCT`** (Phase 1 verification): all
four current defaults have `variant` NULL, and standard unique-index semantics treat NULLs as
distinct — without the modifier two defaults with a NULL variant would both insert and the guarantee
is void. Prod RDS is PostgreSQL 17.9 (verified from the prod-sourced dump header), so the PG 15+
syntax is available.

### `prompt_versions` — new columns

| Column | Type | Notes |
|---|---|---|
| `version_number` | `Integer NULLABLE` initially, backfilled; **`NOT NULL` deferred to the Phase 4 migration** | Numeric ordering key, replacing free-text `version`-string parsing. `version` (e.g. `"v3"`) stays as the display label for the pipeline admin UI (`app/schemas/prompt.py` expects `version: str`). NOT NULL cannot land in Phase 3: the live write paths (`deploy_new_version`, seeds) don't populate the column until Phase 4 — enforcing it early would crash pipeline version creation in the deploy gap. The `UniqueConstraint(prompt_id, version_number)` still lands in Phase 3 (Postgres treats NULLs as distinct in unique constraints, so interim NULL rows don't collide). |
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
| `summary` | `Text NULLABLE` | Human-readable one-liner the PL audit UI displays. Added in Phase 1 verification — `pl_audit_events` carries it and no native column can hold it (`metadata_json` is reserved for existing consumers). |

**Field mapping `pl_audit_events` → `audit_logs`** (Phase 1 verification; `pl_audit_events` has 0
rows in prod, so this shapes only Phase 4's write path, not a data migration): `event_type`
(`"prompt.create"`, the specific dotted string) → **`action`** (`String(120)` fits); the PL API's
short `action` field (`"create"`) is **derived** as the suffix after the last `.` — not stored
twice; `actor_username` → `user_id` (native is `NOT NULL`; PL writes always have an authenticated
actor, so no conflict); `entity_type`/`entity_id`/`ip_address`/`created_at` map 1:1;
`summary`/`changes`/`actor_role`/`user_agent` land in the additive columns above.

`prompt_fixings` and `user_prompt_preferences`: no changes — already Integer-keyed against `prompts.id`.

**Acceptance:** schema design reviewed against every capability in the feature inventory (CRUD,
versioning, tags, team visibility, variables, attachments, reviews, requests, follow-ups, audit,
search/filter) — each has a concrete home before any migration is written.

### Phase 1 verification (2026-07-05) — design signed off against models + prod data

Every `pl_*` column was mapped against the real models (`pl_models.py`, `database.py`) and the
prod-parity rehearsal DB. Spec amendments made above: `NULLS NOT DISTINCT` on the partial unique
index; `audit_logs.summary` + the audit field mapping; `(created_at, id)` backfill ordering.
Remaining findings — all confirmed, folded into later phases:

- **Column homes confirmed.** `pl_prompts.description` → existing `prompts.description` (no new
  column needed); `pl_prompts.created_by` → existing `prompts.owner` (Phase 4 serializes `owner`
  as the API's `created_by`; Phase 2 writes it); every other `pl_*` column lands per the tables
  above. Size fits verified (max title 44/300, tag 20/100, var name 19/100, category 22/100).
- **Library "current content" = the active version's `user_prompt_template`** — native `prompts`
  deliberately has no content column. Data verified safe today: every one of the 20 `pl_prompts`
  has exactly 1 version and `content` matches its head snapshot (no PL "silent edits"
  outstanding). The Phase 2 script must **re-assert this at run time** and, on divergence, write a
  reconciliation version from the row body (`change_reason='migration: unversioned edit'`) so no
  text is lost.
- **Scalar `pl_prompts.team_id` verified unused (0 rows)** and `pl_prompt_teams` empty — dropping
  the scalar in the target schema loses nothing. All 20 prompts are `visibility='draft'`, none
  soft-deleted, `parent_id` unused (0 follow-up chains — the "Follow Up Prompts" *category* is how
  they're grouped today).
- **Teams: `id == name` for all six rows** (`P1`…`P6`) — the slug→int remap loses nothing since
  the slug is literally the name.
- **No table-name conflicts:** all 7 new names (`prompt_tags`, `prompt_variables`,
  `prompt_attachments`, `teams`, `prompt_team_links`, `prompt_reviews`, `prompt_requests`) are
  absent from the 47-table prod schema.
- **`is_default` pre-flight passes today:** exactly one default per `component_type`
  (style/cdd/blueprint/generate) — re-run before Phase 3 creates the index (data can change).
- **Two versionless native prompts exist:** id 7 (`titles`, `component_type` NULL) and id 8
  (`Lesson gen`, generate) have zero `prompt_versions` rows and NULL `active_version`. The
  `version_number` backfill is unaffected (nothing to backfill); Phase 4's service and the Phase 7
  console must tolerate pipeline rows with no versions and a NULL `component_type`.
- **Legacy `prompts.tags` comma-string is live on 6 rows** (pipeline admin metadata). Decision:
  the new `prompt_tags` table is canonical for **library** rows only; the legacy string column
  stays untouched for pipeline rows through the cutover (Phase 4 reads tags per kind). Converging
  pipeline tags into `prompt_tags` is Phase 8 hygiene, not Phase 2/3.
- **Library version display label:** native `prompt_versions.version` (`String`, e.g. `"v3"`) is
  NOT NULL-in-practice for the pipeline admin UI; Phase 2 populates it as `'v' || version_number`
  for migrated library versions, and Phase 4 does the same on new library saves.

**Acceptance met** — each feature-inventory capability has a verified concrete home; migrations
(Phase 3) and the carry-over script (Phase 2) are unblocked.

---

## Phase 2 — Data check & migration script

- [x] **Data check done (2026-07-05).** Read-only row count against prod RDS (`atlas-db…/promptops_db`): `pl_prompts`=20, `pl_prompt_versions`=20, `pl_prompt_tags`=46, `pl_prompt_variables`=32, `pl_teams`=6, `pl_prompt_requests`=2; `pl_attachments`/`pl_prompt_teams`/`pl_reviews`/`pl_audit_events`=0. **Total 126 rows.** The 20 prompts are a curated, admin-authored Cengage CTE authoring playbook (freeform, first-person, `{{double}}` vars; 9 declare variables). Sanity: native `prompts`=8, `prompt_versions`=7 (untouched). **Conclusion: the carry-over migration is MANDATORY** — the earlier "expected empty, skip it" assumption is falsified.
- [x] **Carry-over script written (2026-07-05).** `scripts/migrate_pl_to_native_prompts.py` — idempotent, `--dry-run`/`--apply` modes, both run the full migration + parity verification in one transaction (dry-run rolls back; apply aborts + rolls back on any parity failure). Id-remap maps built for prompt UUIDs / team slugs / request UUIDs; every FK rewritten; `created_at`/`created_by` preserved (tz-aware → naive UTC to match native columns); `prompt_kind='library'`, `name` NULL, `content` → head version's `user_prompt_template` with `system_prompt` NULL; version label `'v'||version_number`; `is_active` on head, `workflow_state='active'`; parent_id topo-ordered (roots-then-children; unused in prod). **Run-time re-assertion implemented:** head-snapshot divergence appends a reconciliation version (`change_reason='migration: unversioned edit'`); verified 0 divergences on prod data. **Idempotency:** each migrated entity records an `audit_logs` mapping row (`action='migration.pl_carryover'`, `changes={pl_id, native_id}`); re-runs skip via the map, with a natural-key adoption fallback (title+category+created_at / team name / request title+requester+created_at — category is in the key because prod has two prompts sharing title+created_at). Safety rails: refuses non-localhost targets without `--allow-non-local`; non-local apply additionally requires a `pl_tables_pre_migration_*.sql` dump in `backups/`; refuses if the target isn't at Alembic revision ≥ `000100000002`.
- [x] **Dry-run reviewed row-for-row + applied to the REHEARSAL DB (2026-07-05).** On a fresh prod clone + Phase 3 migration: dry-run output verified (20 prompts w/ expected titles/categories/owners, 6 teams P1–P6 → int ids, 46 tags, 32 vars, 2 requests carried verbatim incl. `type='update'`/`status='rejected'`); apply committed; re-apply migrated **zero** rows (skip-by-mapping); adoption fallback exercised (deleted the 28 mapping rows → re-apply adopted all 28, created nothing). Independent SQL checks: 126-row parity; pipeline rows untouched (`prompts`=8/`prompt_versions`=7); active-version content byte-equal to `pl_prompts.content` for all 20; `{{double}}` braces preserved (9 variable-bearing prompts); no lib row with `name` set; no lib version with `system_prompt`; exactly 1 active version per prompt. Full suite **147 passed** after. Caught + fixed in testing: request-mapping entity-key mismatch that duplicated the 2 requests on re-run. **Prod apply NOT done — separate gated step** (RDS snapshot + fresh `pl_*` dump + quiet window per `MIGRATION_RUNBOOK.md`).
- [~] Full SQL dump of all 10 `pl_*` tables: pre-migration artifact exists (`backups/pl_tables_pre_migration_20260705T063359Z.sql`, from prod, git-ignored; also enforced by the script for non-local applies — take a **fresh** one immediately before the prod apply). The second dump **before Phase 6** drops `pl_*` is still pending (Phase 6 gate).

**Acceptance:** the carry-over script has run (dry-run reviewed, then applied) and all 126 `pl_*` rows are accounted for in the native tables — no prompt, team, tag, or variable lost. **Met on rehearsal (2026-07-05); prod apply outstanding** — it rides the same runbook window as the Phase 3 prod apply (`alembic upgrade head`, then this script).

---

## Phase 3 — Alembic migration

- [x] Single migration implementing all of Phase 1's additive changes: revision `000100000002` (`migrations/versions/20260705_0900_000100000002_prompt_consolidation_additive.py`) — new columns on `prompts`/`prompt_versions`/`audit_logs`, the 7 new tables, the partial unique index (`NULLS NOT DISTINCT`, verified to reject a second NULL-variant default), and `UniqueConstraint(prompt_id, version_number)`. SQLAlchemy models updated in lockstep (`promptops_app/database.py`: new columns + `PromptTag`/`PromptVariable`/`PromptAttachment`/`Team`/`PromptTeamLink`/`PromptReview`/`PromptRequest`). The partial index is deliberately NOT declared on the model (SQLite test DBs can't express it) — strip it from future autogenerate diffs.
- [x] Backfill `version_number` for existing `prompt_versions` (sequential per `prompt_id`, ordered by **`(created_at, id)`** — Phase 1 verification found prompt 1's two versions share an identical `created_at`, so `created_at` alone is ambiguous; `id` order matches the `v1`/`v2` labels). Verified on rehearsal: `1:v1→1, 1:v2→2, 2:v2→1, 3–6:v1→1`. **`NOT NULL` deferred to the Phase 4 migration** (live write paths don't populate the column until Phase 4 — see the Phase 1 table note).
- [x] Backfill `prompt_kind = 'pipeline'` for all existing `prompts` rows — done via the column's `server_default='pipeline'` (default flipped from `'library'`, see Phase 1 table note); verified all 8 rows are `'pipeline'`.
- [x] **Pre-flight before constraints**: `_preflight()` embedded in the migration aborts (transactional rollback) on `is_default` duplicates per component; the `row_number()` backfill is duplicate-free by construction, so the unique constraint cannot collide. Rehearsal run passed pre-flight.
- [x] Single transaction: Alembic transactional DDL confirmed in the rehearsal run ("Will assume transactional DDL"). Plain `CREATE UNIQUE INDEX` (not CONCURRENTLY — the table has 8 rows; the lock is momentary), still schedule the prod apply in a quiet window per the runbook.
- [x] No `pl_*` table touched — verified `pl_prompts`=20 intact after upgrade.
- [x] Applied to the Phase 0S rehearsal DB; `upgrade → downgrade → upgrade` round-trip clean (downgrade left zero residual columns/tables, all data intact); autogenerate drift check shows only the pre-recorded known drift; app boots against the migrated DB; full suite **147 passed** incl. characterization — nothing observable changed.

**Status: rehearsed and green (2026-07-05). NOT yet applied to prod** — prod apply happens via the
deploy pipeline (or manual `alembic upgrade head`) per `MIGRATION_RUNBOOK.md`: RDS snapshot +
logical dump first, quiet window.

**Acceptance:** migration applies cleanly; all existing endpoints (pipeline admin, CDD/Blueprint generation, Prompt Library on its old `pl_*` backing) work exactly as before — nothing observable changes yet.

---

## Phase 4 — Service layer rewrite

Goal: `promptops_app/services/prompt_library_service.py` operates against the native models instead of
`PLPrompt`/etc., with **zero change to its public function signatures** so the router doesn't need a
parallel rewrite in the same step.

- [x] Repoint every query/import from `pl_models` to the native models — done (2026-07-05). Key mechanics: library "current content" = active `PromptVersion.user_prompt_template` via `get_prompt_content`/`set_prompt_content` (create_version → instant-publish bump w/ `'v'||n` label + `active_version` sync; no flag → in-place overwrite of the active snapshot, preserving the old "silent edit" semantics where the version list does not grow); content search now an `EXISTS` over the active version; `owner`↔`created_by` mapping; pipeline rows read tags from the legacy comma-string, library rows from `prompt_tags`. Library-feature relationships (`parent`/`children`/`tag_rows`/`variables`/`attachments`/`team_links`) added to the native `Prompt` model (code-only). Fixed in testing: `set_prompt_content` must query versions from the DB, not the possibly-stale relationship (two same-session bumps collided on the unique constraint).
- [x] Manual UUID assignment removed — DB autoincrement everywhere (`uuid4` survives only in attachment stored filenames).
- [x] `build_prompt_snapshot()` / `compute_prompt_changes()` / `redact_changes()` — reused as-is.
- [x] Write paths audit to the unified `audit_logs`: PL `event_type` (dotted) → `AuditLog.action`, short action derived from the suffix on read (per the Phase 1 field mapping); `actor_username`→`user_id`, `summary`/`changes`/`actor_role`/`user_agent` in the additive columns. The PL audit UI is scoped to the PL action families (`prompt.` / `request.` / `attachment.` / `team.` prefixes — native CAS writers use snake_case verbs, so no collision; the `migration.pl_carryover` rows stay out of the UI too).
- [x] `browse_prompts_query(db, role, user_team, kind)` — defaults to library; `kind=pipeline|all` honored only for `PIPELINE_MANAGER_ROLES` (admin; formalized as `prompt.pipeline.edit` in Phase 8), everyone else silently stripped to library (no 403 leak). `can_access_prompt` gives non-admins the same 404 for a pipeline id as for a nonexistent one. Generation resolution untouched.
- [x] Write-policy split enforced structurally: every PL write/render endpoint resolves its target through the library-only base query, so pipeline rows are unreachable for writes via this API (they stay on `/api/v1/prompts`; the `workflow_state` approval gate itself is Phase 8). The `{{double}}` engine renders library rows only — pipeline rows 404 on `/render` (Decision 2).
- [x] Unit tests: `tests/unit/test_prompt_library_service_native.py` — serializer shape parity (prompt/review/request/team/audit dicts), silent-edit vs create_version semantics, `system_prompt` stays NULL, `{{double}}` render semantics, and the kind-separation acceptance tests (library queries never return pipeline rows; kind stripped for non-admins; a library row titled `cdd_generation` is invisible to `_from_db` and `get_default_prompt` while a real pipeline row resolves).

**Acceptance: met (2026-07-05).** Suite 162 passed — all 67 pre-cutover characterization tests pass against the native backing unchanged except the two team-id-shape assertions Phase 5 deliberately changes. Service smoke against the rehearsal DB's real migrated data: all 20 library prompts list/serialize with content == head version, author view strips drafts+pipeline, admin `kind=pipeline` sees the 8 pipeline rows, `{{var}}` render substitutes, content search hits.

---

## Phase 5 — Router rewrite

- [x] Path params `str` → `int` on all endpoints (`pid`/`aid`/`rid`/`tid`) — done with Phase 4 (2026-07-05; the router did its own data access, so 4+5 landed as one cutover, as the rollout stage 2 anticipated).
- [x] Router rewritten onto native models + service; zero `pl_models` imports remain in `app/`/`promptops_app/` outside `pl_models.py` itself and the `init_db()` registration import that Phase 6 removes (the Phase 2 carry-over script legitimately reads `pl_*` as its source).
- [x] Attachment storage: `PROMPT_ATTACHMENTS_DIR` (default `<repo>/prompt_attachments`), with legacy `PL_ATTACHMENTS_DIR` honored as fallback until Phase 6.
- [x] Endpoint coverage: characterization HTTP suite extended with reviews round-trip (incl. upsert-per-user), requests round-trip (author-scoped list, manager status update), attachment upload/download/delete + extension rejection; existing tests cover list/search/pagination/create/update/delete/duplicate/version/render/meta/RBAC/audit. **Deliberate contract changes recorded in the tests:** team ids are autoincrement ints (caller-supplied slug ignored, slug lives in `name`), team-uniqueness 409 moved from slug id to case-insensitive name.

**Acceptance: met (2026-07-05)** — suite 162 passed; grep clean (see above); service smoke green against rehearsal data. Frontend re-verification of the id-shape assumptions is Phase 7a as planned.

---

## Phase 6 — Drop `pl_*` tables

- [ ] **Confirm Phase 2's carry-over migration completed AND its dump exists** and is retained somewhere durable — do **NOT** drop `pl_*` until the 20 prompts + 6 teams + 46 tags + 32 variables are verified present in the native tables (row-parity query). The dump is the safety net given the tables were never Alembic-managed.
- [ ] Alembic migration: explicit `DROP TABLE IF EXISTS pl_prompts, pl_prompt_versions, pl_prompt_teams, pl_prompt_tags, pl_prompt_variables, pl_attachments, pl_teams, pl_prompt_requests, pl_reviews, pl_audit_events CASCADE` — required because these were `create_all`-only and will otherwise persist as orphans.
- [ ] **Delete `promptops_app/pl_models.py` AND remove its import in `promptops_app/database.py:922`** (`import promptops_app.pl_models  # noqa: F401`, inside `init_db()`, added solely to register `pl_*` classes on `Base.metadata` before `create_all()`). **This import is easy to miss** — it's not in any of the 3 already-obvious `pl_models` consumers (the router, the service, the migration script); it's a one-line deferred import buried inside `init_db()`. If it's left behind after `pl_models.py` is deleted, `init_db()` raises `ModuleNotFoundError` on the **next app startup** — since `init_db()` runs in `app/main.py`'s lifespan hook, this would break the entire application, not just the Prompt Library. Verify with `python -c "from promptops_app.database import init_db; init_db()"` against the Phase 0S rehearsal DB immediately after deleting `pl_models.py`, before this phase is considered done.
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
- [x] Audit done against the shipped cutover (2026-07-05). No UUID regex/format assumptions, ids flow opaquely into URLs — but the int switch surfaced **two strict-comparison bugs** (string route param vs now-integer id), both fixed: `AdminRequestDetailPage.jsx` `list.find(x => x.id === id)` always missed → the admin request detail page redirected away (real breakage); `PromptFormPage.jsx` `rp.id !== id` stopped excluding the current prompt from the parent dropdown (cosmetic; the backend still rejects self-parenting). Noted, no change needed: `api/teams.js` `createTeam(id, name)` still sends a legacy slug `id` the backend now ignores — it has no UI caller today; drop the param if a team-creation UI is ever built. `RequestNewPage`'s `promptId` query-param string is coerced server-side.
- [x] `routes.jsx` — confirmed no UUID shape enforcement (`prompts/:id` is untyped).
- [ ] Manual walkthrough: create/edit/delete a prompt, tags, teams, review, request, attachment, audit log — all render correctly against integer IDs. (Needs a running frontend+backend; frontend `npm run build` passes with the fixes. This walkthrough is the 7a sign-off that gates Phase 6.)

### Phase 7b — Make the Library the console for pipeline prompts (Decision 6)
- [x] **Kind-aware prompt utilities — OBSOLETED by the brace conversion, replaced with a legacy-brace warning** (2026-07-05). This item assumed pipeline rows carry `{single}` bodies; after the seed-source fix + `scripts/convert_seeded_prompt_braces.py`, **every generation-resolvable row — pipeline and library — is `{{double}}`** (verified by scanning all 27 prompt-version pairs on prod-parity rehearsal data; the only `{single}` leftovers are `lesson_generator` and `Gen Ai generator`, neither resolvable by any stem/component key). Dual-syntax utils would serve two dormant demo rows, so `extractVarNames`/`fillPromptContent`/preview stay `{{double}}`-only for both kinds. What shipped instead: `findLegacyVarNames` in `utils/prompt.js` + a warning under the `PromptFormPage` content editor when a body contains `{single}` identifier tokens — protects the real residual risk (an admin pasting legacy `.format` constants, e.g. seeding the blueprint teacher/student variants via console) instead of silently detecting zero variables. Note: the frontend had NO test runner — **resolved 2026-07-05 (commit `31378c1`)**: vitest added (dev-only; `npm test` / `test:watch`), 39 unit tests over the pure PL utils (`prompt` incl. the adjacent-`{x}{y}` regression, `permissions` incl. the admin-only pipeline gate, `nav` role visibility, `csvExport` RFC 4180), and a CI step (Node 22, `npm ci` + `vitest run`) appended to `ci.yml`.
- [x] **`PromptFormPage` dual editor** — done 2026-07-05 (commit `d928614`). Pipeline mode: registry-name field (immutable on edit), component/variant controls (select + datalist; locked on default rows — clear the flag first), SYSTEM textarea + USER template, change note; saving commits the next `vN` via `POST /api/v1/prompts/{id}/versions` (admin instant-deploy / reviewer draft per the Phase 8 gate); meta-only edits go through `PUT /{id}` without a version commit; admin-only kind selector on create (library | pipeline). All gated by new `canManagePipelinePrompts` (admin-only, mirrors `prompt.pipeline.edit`). Backend additions this required: `variant` on `PromptCreateRequest`/`PromptRead`; `component_type`/`variant` re-keying on `PUT /{id}` (`prompt.pipeline.edit` only, 422 on default rows, empty-string clears). Console reads the Library API (admins get an additive `pipeline` block in `prompt_to_dict` + version extras; library dict shapes pinned unchanged), writes the registry API via new `api/pipeline.js`.
- [x] **`PromptDetailPage`** — done 2026-07-05 (commit `d928614`): SYSTEM + USER pair, component/variant/default/state badges, version-history viewer with per-version `workflow_state` transition buttons (draft→in_review→approved→active + reject-to-draft, mirroring the backend table), make/clear-default action; library-only affordances (reviews/attachments/follow-ups/request-update) hidden for pipeline rows. (The planned `{single}` preview path was obsoleted by the brace conversion — see the utils item above.)
- [x] **List surfaces** — done 2026-07-05 (commit `70d8e8b`): Library | Pipeline toggle rendered only for `canManagePipelinePrompts` admins (pipeline tab passes `kind=pipeline` through `buildFilterParams`; server-side strip remains the boundary); `VisibilityBadge` is kind-aware, so cards and table rows show component/variant + Default + workflow-state badges for pipeline rows; Duplicate/Delete hidden on pipeline rows (the Library API rejects those writes anyway). `fetchMeta` untouched — categories are library-only and pipeline tags still surface via the legacy comma-string (`_prompt_tags_list`), so the tags filter works on both tabs; storage convergence stays with the deferred tags-hygiene item.
- [x] **Flow-organized console view** — done 2026-07-05 (commit `1b8f0e3`): `PipelineFlowPage` at `/prompt-library/flow` (nav item for all library readers). Project → Course pickers (cluster derived from the course row); per phase (style/cdd/blueprint/generate/quiz) shows the effective resolution — scope lock (holder + Unbind) → component default → file template, plus variant defaults — and a bind row (component-matching prompt pool from `GET /api/v1/prompts?component=`, scope select enabled by context, `PUT /fixings`). Reuse is by reference (`PromptFixing`); phase-appropriateness + the authors-only-approved rule stay server-enforced (`PromptListItem` carries no `workflow_state`, so the UI surfaces the 403/422 messages rather than pre-filtering). The migrated playbook prompts appear per phase via a category-keyword match (collapsible reference panel). Bind→resolve→unbind round-trip verified against the rehearsal-backed API. Note: the locked decision says flow becomes the tab's *primary* view — it ships as a sibling nav item first; promoting it to the default landing view is a UX call for the walkthrough.
- [ ] **API client** — extend `api/prompts.js` payloads and `fetchPrompt` mapping for `prompt_kind`/`system_prompt`/`component_type`/`variant`/`is_default`/`workflow_state`; add client methods for the new Phase 8 endpoints. The console reads both `/api/v1/prompts` and `/api/v1/prompt-library`.
- [x] **Retire the duplicate editor page — but NOT its shared engine.** Done 2026-07-05 (commit `70d8e8b`): deleted `features/prompts/pages/PromptsPage/` (verified unreachable — zero importers outside itself, never mounted in `routes.jsx`); console now covers create/version/deploy so the precondition held. `promptsSlice.js`/`promptsThunks.js`/`services/promptsService.js` kept — re-verified live dependencies at deletion time: `InlinePromptControls.jsx`, `PromptDetailsModal.jsx`, `PromptLibraryPanel.jsx`, `app/store.js`. Folding that shared logic into the console's API client stays a separate follow-up. Decommission Streamlit `pages/prompts.py` only after 7b parity sign-off (Phase 10 gate).

**Acceptance:** (7a) full walkthrough of the library-kind UI against the new backend, no console errors, no broken ID behavior. (7b) an admin can, entirely within the console, create/edit a pipeline prompt with a system+user pair, set it default for its `component_type`, move it through the approval states, and see a subsequent live CDD/Blueprint/Style/Generate call use the edited DB row; a non-admin cannot see or invoke any pipeline-editing control; no `{single}`/`{{double}}` regression on library prompts.

---

## Phase 8 — Pipeline registry completion + management-console backend

Several items here are prerequisites for the Phase 7b console — do the endpoint/loader work before
wiring the console UI.

- [x] **⚠️ PREREQUISITE — fix the dormant-seed name-key mismatch.** Done 2026-07-05, behind the `PROMPT_RESOLVE_BY_COMPONENT` feature flag (env var, ships OFF — flag-off is byte-identical to the old stem-name behavior, the promised toggle-off rollback). Flag-on DB-tier order: scope fixing (`resolve_fixed_prompt`, course→cluster→project→global) → component default (`get_default_prompt`, now hard-filtered to `prompt_kind='pipeline'`) → legacy stem `name` → file tier. `load_template`/`build_prompt` accept optional `project_id`/`cluster_id`/`course_id` for the scope tier (routers pass them in the later scope-wiring item). Stem→component map: `style_understanding→style`, `cdd_generation→cdd`, `blueprint_generation→blueprint`, `content_generation→generate` (`quiz_generation`/`validation` unmapped until Generate wiring). Verified: acceptance test edits the default CDD row's active version → next `load_template` returns the edit; smoke on rehearsal (prod-shaped data) — flag off: all 4 stems file-tier as today; flag on: all 4 resolve the seeded default rows. `TestDormantSeedBug` re-pinned to flag-off + new `TestComponentKeyedResolution` (8 tests: default resolution, edit-propagation acceptance, default-beats-stem, stem-as-secondary-key, course-scope + global fixings, mislabeled-library-row blindness, unmapped stems). **⚠️ Enablement precondition (data, per env) — tooling shipped 2026-07-05 (`6c3cd62`):** the 4 seeded default rows' bodies were pure `{single}`-brace (15 placeholders) — they must be `{{double}}` before flipping the flag anywhere, else generation renders literal `{course_name}` text. Now dischargeable: `seed_data()` seeds already-converted bodies (fresh DBs need nothing); `scripts/convert_seeded_prompt_braces.py` (idempotent, `--dry-run`/`--apply`, localhost guard, aborts on unexpected tokens) converts already-seeded DBs and includes the `extra_instructions_block`→`extra_instructions` rename the routers require. **Applied to rehearsal** (15 placeholders converted, re-apply converts 0; flag-on smoke: all 4 stems resolve `source=db` and render with zero residual/unrendered placeholders). **Prod run = one script invocation in the enablement window** (RDS snapshot first, per runbook). Legacy constants untouched (they remain the inline-fallback tier).
- [x] **New management endpoints on `/api/v1/prompts`** — done 2026-07-05. `PUT /{id}/default` (demotes the current default for the same `(component_type, variant)` — NULL-variant safe; rejects library rows and rows with no `component_type`); `POST /{id}/versions/{version}/state` (`draft→in_review→approved→active` + rejection back to `draft`; unknown state 422, illegal transition 409; `→active` deploys: deactivates siblings, demotes the previously-active version's state to `approved`, syncs `Prompt.active_version`); `PUT/DELETE /fixings` + `GET /fixings/resolve` (declared before `/{prompt_id}` so the literal path isn't shadowed; scope_level validated and only the matching scope id kept, preventing unreachable upsert tuples). Default/state gated `prompt.pipeline.edit`; `workflow_state` added to the version read schemas. 16 integration tests (`tests/integration/test_pipeline_prompt_admin.py`).
- [x] **Reuse / inject endpoint + role split.** Done 2026-07-05 as the `PUT /fixings` policy: admins (`prompt.pipeline.edit`) bind any pipeline prompt; any other role with `prompts.view` may bind only a prompt whose **active version** is in `approved`/`active` state (403 otherwise), at any scope incl. global. Phase-appropriateness (`prompt.component_type == fixing.component`) is enforced for **everyone** (422). Library rows can never be bound (422). Unbind allowed to the same roles (an author could equivalently rebind, so unset is not an escalation). Reuse is by reference (`PromptFixing`), per the locked decision.
- [x] Wire `resolve_fixed_prompt()` into the live FastAPI routers — done 2026-07-05. `cdd.py` and `blueprints.py` pass `project_id`/`cluster_id`/`course_id` (from the resolved Course row) into `build_prompt`; the loader's scope tier (flag-on only) resolves the most specific fixing. Style needs no wiring: `Style` rows carry no project/cluster scope, so only global fixings apply — and the loader already checks global scope with no ids. Generate's wiring rides the Generate item below. Router-level tests: seeded default reaches a live CDD call (the flip of `test_seeded_default_row_is_ignored`), and a course-scope lock reaches live CDD and Blueprint generations.
- [x] **Variant-aware default resolution** — done 2026-07-05 (commit `8c8397b`). `get_default_prompt`/`resolve_fixed_prompt`/`load_template`/`build_prompt` take a `variant` param: exact `(component_type, variant)` match wins, the NULL-variant row is the fallback (only when a variant was requested), **never sideways** across variants — in both the default tier and the fixing tier (an incompatible narrow fixing falls through to the next scope instead of hijacking the request; `resolve_fixed_prompt` filters by the bound prompt's variant + pipeline kind when `acceptable_variants` is passed, legacy callers unaffected). `require_variant=True` restricts resolution to an exact-variant DB row with **no** NULL-variant/stem/file fallback at all, for callers whose fallback tiers are a *different* variant's template (Generate interactive). Flag-off stays byte-identical (variant is only honored by the component-keyed DB tier).
- [x] Split Blueprint's `teacher_mode` flag into two independent rows (`variant=student` / `variant=teacher`), each separately versioned — done 2026-07-05: `blueprints.py` passes `variant="teacher"|"student"` into `build_prompt`; each mode resolves its own row once authored via the console; the NULL-variant seeded default keeps serving both modes until then (router-level tests for both the split and the fallback). Note: `regenerate-section` still uses the mode-specific constants directly (`get_blueprint_prompts`) — it was never on the DB path; candidate for Phase 9 fragments.
- [x] Wire **Generate** onto the DB-backed path — complete 2026-07-05 (flag-gated like the rest): in `run_generation_job`, lesson components resolve `content_generation` and quiz/assessment components resolve `quiz_generation` via `build_prompt` (scope lock → component default → stem row → `.md` file), with persona prefix, style injection, citation instruction and source context still code-injected around the resolved template exactly as before. Any resolution failure (or flag off) falls through to the untouched legacy constants — `PERSONA_PREFIX_TEMPLATE`/`LESSON_WITH_CONTEXT_*` are now the fallback tier for this path. `quiz_generation` maps to a new component key `'quiz'` (own default slot; an assessment can never resolve the lesson default; no seeded row yet — DB control starts when an admin creates a `(quiz, is_default)` row, file tier until then). The resolved template name/version is now recorded on `Generation.prompt_name/prompt_version` and in the LLM usage log (flag-off keeps the pinned `""`). **`variant=interactive` done 2026-07-05** (commit `8c8397b`): all non-lesson/non-quiz components fill the taxonomy's interactive slot and resolve **only** an exact `(generate, variant='interactive')` row (`require_variant` — the NULL-variant default/stem/file are all lesson-shaped and must never hijack an interactive component; the miss is quiet and expected); until an admin authors that row, the bespoke `build_component_generation_prompt` path keeps full control.
- [x] Enforce registry variable declarations — done 2026-07-05 (commit `8c8397b`), flag-gated. Deliberately NOT `render(strict=True)` (which fails on every `{{placeholder}}` incl. deliberately-optional ones, and whose raise would be swallowed by the callers' constant-fallback `except` — silently ignoring the DB row): `build_prompt` validates only **declared** required variables — `prompt_variables` rows for a DB-resolved prompt (superseding the static `_REGISTRY` per-row; none exist for pipeline rows yet, so enforcement arms as admins declare vars via the console), `_REGISTRY.required_vars` for the file tier — and raises `PromptVariableError`. A violation is a template misconfiguration and **surfaces instead of falling back**: CDD/Blueprint map it to a new `PromptConfigurationError` (`PROMPT_MISCONFIGURED`, 500); `run_generation_job` fails the job with the message; all other resolution failures keep the legacy fallback tiers. Undeclared placeholders stay lenient (left literal). Style not covered (doesn't route through `build_prompt`; its one registry var is code-supplied).
- [x] `prompt_variables` declare/edit surface — done 2026-07-05 (commit `bf93029`); the arming surface for the item above. `PUT /api/v1/prompts/{id}/variables` (`prompt.pipeline.edit`, pipeline rows only, replace-all `{name, label, hint}`): names schema-validated as identifiers and required to appear as `{{placeholders}}` in the **active version** (a declaration the template never uses would only make every generation call fail); duplicates + versionless rows 422. Console: the pipeline edit form lists detected `{{placeholders}}` from the system+user text with a declare-checkbox + label/hint each (stale declarations flagged); saved **after** the version commit so validation runs against the newly deployed text; the detail page retitles the section "Declared variables" with an enforcement note. Reads ride the existing PL detail `variables` list — no new read endpoint. Verified live on rehearsal (declare → typo 422 → console detail shows it → cleared). 8 integration tests; suite 242.
- [ ] Seed one `is_default=True` row per taxonomy line (Cluster/Course rows seeded but flagged backlog, not wired to a live endpoint). **Proposal (2026-07-05, awaiting content sign-off — seeds are content decisions):**
  - **(blueprint, teacher) + (blueprint, student):** seed from the existing mode-specific constants (`TEACHER_BLUEPRINT_*` / `BLUEPRINT_*` in `blueprint_parser.py`'s `get_blueprint_prompts`), converted `{single}`→`{{double}}` at seed time. Zero behavior change flag-on (same text, now independently versionable per mode).
  - **(quiz, NULL):** seed from `templates/quiz_generation.md` (already `{{double}}`). Zero behavior change (that file is the tier serving quiz today); gives the console a quiz default slot to edit.
  - **(generate, interactive): do NOT seed.** The bespoke builder routes ~10 label-specific prompts (reflection/journal/worksheet/knowledge-check/…); flattening them into one generic seeded template would degrade every interactive component the moment it exists. DB control should start only when an admin deliberately authors the row (or Phase 9 fragments absorb the family).
  - **Cluster/Course (4 backlog prompts): do NOT seed yet** — no endpoint resolves those components; dormant rows would just be console noise. Seed together with their endpoints (separate follow-up per Open questions).
  - All new seeds must be `{{double}}`-brace from the start (the 4 existing seeded defaults still need their recorded `{single}`→`{{double}}` conversion before flag enablement).
- [x] Approval gate — done 2026-07-05. `POST /{id}/versions` role-splits: `prompt.pipeline.edit` holders keep the historical instant-deploy (via `deploy_new_version`, which now also demotes the retired version's state `active`→`approved`); everyone else (reviewers, via `prompts.manage`) commits through new `commit_draft_version` — `workflow_state='draft'`, `is_active=False`, deployed version untouched — and activation goes through the state-transition endpoint (admin-gated). `POST /{id}/versions/{v}/deploy` tightened `prompts.manage`→`prompt.pipeline.edit` (safe: its only frontend caller is the unmounted `PromptsPage.jsx`; Streamlit calls the repo directly and is unchanged). Duplicate version tags now 409. Both write paths + all 5 v1-creation sites (router ×3, seeds ×2) populate `version_number` — the `NOT NULL` promotion deferred since Phase 3 is now unblocked (all writers set it; schedule the constraint with a later migration). Acceptance "a non-admin cannot activate a pipeline-tier version via the API" covered by tests (reviewer deploy 403, reviewer state-transition 403, reviewer commit lands draft). Library instant-publish untouched (separate service path). 6 new integration tests; suite 192.
- [x] New permission `prompt.pipeline.edit` (admin-only; also added to the reviewer blocklist as belt-and-suspenders), distinct from the Library's existing permissions. Gates the default/state endpoints and unrestricted scope-binds today; Phase 4's `PIPELINE_MANAGER_ROLES` service check is its Library-side counterpart to converge on it later.
- [x] Pipeline-tags hygiene — done 2026-07-05 (commit `288b9ad`). `prompt_tags` rows are now canonical for **every** kind; the legacy `prompts.tags` comma-string stays mirrored on writes (Streamlit surfaces + rollback) and remains the read fallback for databases predating the backfill. Migration `000100000003` (data-only, idempotent `ON CONFLICT DO NOTHING`, pipeline-kind-scoped so downgrade can never touch carried-over library tags) parses the legacy strings into `prompt_tags`; rehearsed up/down/up on the rehearsal clone (46→53→46→53 rows — the expected 7 pairs from the 6 pipeline rows). Reader/writer convergence: `set_prompt_tags` (dedup; kept rows reused — a delete+insert of the same composite PK in one flush collides) is used by all four `/api/v1/prompts` write paths; the in-memory `search` scans `tag_rows`; `list_prompts_tagged` (live Generate pickers) matches canonical rows OR the legacy string; `_prompt_tags_list` prefers `tag_rows` for all kinds — the PL `tag` filter's `PromptTag` join now actually matches pipeline rows (it silently never did). Seeds create `PromptTag` rows alongside the mirror. **Prod note:** `000100000003` rides the same runbook window as Phases 3+2 (it sits above them in the alembic chain). 6 new tests; suite 234.

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
  - Phase 11 (requirements-doc alignment) is additive UI/service work outside this gate chain — any
    time after 7b sign-off; it never touches resolution or schema.
- [ ] Rollback plans:
  - **Any schema phase:** the pre-phase RDS snapshot + PITR is the guaranteed restore path (no in-repo DB backup automation exists — take the snapshot manually).
  - **Phases 1-3:** additive only — old code ignores the new columns/tables; Alembic `downgrade` removes them.
  - **Phases 4-5:** `pl_*` + old service/router still intact — `git revert` the cutover commit(s), zero data lost.
  - **Phase 6 onward:** the one-way door — no automated rollback; recovery is the pre-Phase-6 dump / RDS snapshot. Do not run Phase 6 until 7a is signed off.
  - **Phase 8 name-key fix:** feature-flagged and guarded behind the DB→file→inline fallback, so a bad default-row resolution degrades to the shipped `.md`/constants rather than failing generation; keep Streamlit live as the manual escape hatch until 7b is signed off.

---

## Phase 11 — Requirements-doc alignment (PL ↔ CAS sync spec)

> Source: `Technical Requirements- Prompt Library and Content AI Studio Sync.docx` (repo root).
> Alignment assessment run 2026-07-06: the doc's data-model core (§6 master record + dedup, §7
> master/mapping/tags tables, §5 bidirectional sync, §8 versioning) is **already delivered** by the
> unified model — one `prompts` master table + append-only `prompt_versions`, `prompt_fixings` as
> the course↔prompt many-to-many (richer than spec: course→cluster→project→global), `prompt_tags`,
> component-filtered CAS dropdowns with phase-appropriateness enforcement, and edit-once-propagates
> sync (the 7b acceptance proof). What remains is presentation-layer and metadata work, plus
> documented deviations to confirm with the doc's stakeholders.

### Build items (gaps)

- [x] **Course-grouped library view (doc §2, §8 — the headline UX).** Done 2026-07-06 (`39875e5`).
  `GET /api/v1/prompts/by-course` computes every course's effective prompt set in one pass
  (fixings + defaults loaded whole; per-course resolution replicates `resolve_fixed_prompt`
  semantics — own-ID scope match, variant-incompatible fixings fall through; slots = the six doc
  categories + dynamic slots for authored variant defaults). New **Courses** tab
  (`/prompt-library/courses`, all readers — same audience as Flow) renders collapsible course
  groups with source badges (🔒 scope locks / Default / File template / Built-in builder); a shared
  default displays under every course while remaining one master row (doc §6.2). Detail links
  admin-only. 5 integration tests; live-smoked on rehearsal (16 courses, defaults resolve,
  quiz=file, interactive=builtin).
- [x] **Course/cluster/project filters in the PL list (doc §8).** Done 2026-07-06 (`0ec8666`).
  `course_id`/`cluster_id`/`project_id` params on `apply_list_filters`: matches pipeline rows
  locked anywhere relevant to the scope (upward covering chain for a course, downward subtree for
  cluster/project, global always) plus inherited defaults; most specific id wins; unknown ids →
  empty; scope implies pipeline-kind so the non-admin library-only boundary holds (leak test).
  UI: Project → Course cascading selects on the admin Pipeline tab. 6 unit tests.
- [x] **Category taxonomy reconciliation (doc §3).** Done 2026-07-06 (`18fe1ce`). Canonical mapping
  `componentCategoryLabel()`: style→Style, cdd→CDD, blueprint→Blueprint, generate→**Lesson
  Generation**, quiz→**Assessment**, (generate, interactive)→**Component**. Wired through
  list/card/CSV, Flow phase labels, the form's component select (values stay keys), detail header
  (raw key kept alongside). Display-layer only — resolution keys unchanged.
- [x] **Scope metadata surfaced as searchable facets (doc §4).** Done 2026-07-06 (`f8418cc`).
  `GET /api/v1/prompts/fixings` (list; prompt_id/component filters; scope_name resolved
  server-side). Pipeline detail page renders read-only "Used by" chips — '★ All courses (component
  default)', '🔒 Course: X' — instead of the doc's freeform tags (drift risk). Version/status
  facets already existed as badges.
- [x] **Duplicate detection at creation (doc §10).** Done 2026-07-06 (`aebb30c`).
  `find_duplicate_prompt` (whitespace/case-normalized active-version match, same kind, soft-deleted
  excluded) + `POST /prompt-library/prompts/duplicate-check` (manage-gated). Create form probes and
  asks via confirm — warns, never blocks; probe failure never stops a save.
- [x] **`tag_type` on tags (doc §7.3) — decided NO** (2026-07-06). Freeform tags stay untyped;
  typed facts are relational and now surfaced as facets. Recorded as deviation below.
- [x] **Category mandatory (doc §10) — decided ENFORCE** (2026-07-06, `f197857`). Library create
  400s on blank category (the silent `'General'` fallback removed); update rejects an explicit
  blank, omission stays a valid partial update; form field required. Pipeline rows unaffected
  (component keys are their classification).
- [x] **Archived-prompt exclusion audit (doc §9).** Done 2026-07-06 (`74a8e17`). Audit found no
  `deleted_at` guards on selection/resolution surfaces; added dormant forward guards (no write
  path soft-deletes a pipeline row today, so zero reachable behavior change; flag-off identical):
  `get_default_prompt`, `list_prompts_by_component`, `list_prompts_tagged`, `list_all_prompts`,
  the fixing-target load (falls through to the component default), the legacy stem lookup, and
  by-course lookups. 7 pinning tests (`test_archived_exclusion.py`). Note: version-tier nuance —
  `_from_db('latest')` falls back to the newest version only when a row has NO active version
  (legacy safety net for pre-gate rows); the approval gate always keeps an active version on
  deployed rows, so draft versions cannot hijack selection.

### Review verdict on the doc's technical prescriptions (per the stakeholder instruction)

> Instruction that accompanied the doc (relayed 2026-07-06): review it, do NOT follow its
> technical requirements verbatim — change backend logic / the DB model where the *intent*
> genuinely warrants it. Verdicts:

- **§7.1 prompt master table** — REJECTED as written; intent already exceeded by
  `prompts` + append-only `prompt_versions` (the doc has no versioning table at all).
- **§7.2 course_prompt_mapping** — REJECTED as written; `prompt_fixings` is the mapping table
  with a richer scope hierarchy and none of the doc's denormalized name columns (its own §10
  asks for normalization).
- **§7.3 tags w/ tag_type** — REJECTED; typed facts are relational and surfaced as facets (11.4).
- **§6.1 semantic Prompt IDs (`PROMPT_STYLE_001`)** — REJECTED; integer PKs are the
  auto-generated unique ID (cosmetic).
- **§10 content-hash dedup at storage** — SOFTENED to the advisory create-time warning (11.5);
  structural dedup for defaults already guarantees the doc's 40%-shared-defaults case.
- **§5.2 CAS-authored prompts captured** — ACCEPTED as a real backend gap and fixed (11.9,
  `2c18df2`): CDD/Blueprint `generation_params` now persists prompt provenance — the full
  inline-override text verbatim (was: used once and discarded), or the registry template
  name+version (same convention as `Generation.prompt_name`), or `builtin_fallback`. Additive
  JSON keys, no schema change, LLM input unchanged. Deliberately NOT auto-registering author
  overrides as prompt rows — that would bypass the Phase 8 approval gate; a "promote override
  to prompt" console affordance is the sanctioned follow-up if wanted. **Deferred:** Style
  override capture needs a `StyleVersion` params column — schema addition, rides a future
  migration window.
- **§7.1 tenant_id** — deviation below stands (orphaned tenant experiment).

### Documented deviations (confirm with stakeholders, then close)

- [ ] **Tenant isolation (doc §7.1 `tenant_id`, §10):** no tenant column by design — prod's
  `tenants`/`tenant_id` schema is orphaned (Phase 0S discovery; cleanup is a separate follow-up).
  Team/global/draft visibility is the isolation primitive. Confirm multi-tenancy is genuinely dead
  or this becomes a real schema phase.
- [ ] **CAS→PL per-course prompt copies (doc §5.2):** course creation deliberately does NOT spawn
  `default_*_prompt` rows per course — the doc's own §6 forbids duplicate storage; resolution +
  fixings satisfy the intent. The course-grouped view (build item 1) delivers the §5.2 *display*,
  and 11.9 closes the capture half (inline overrides persisted with the artifact). Remaining to
  confirm: that opt-in "promote override to prompt" (vs. the doc's automatic save) is acceptable.
- [ ] **Prompt ID format (doc §6.1 `PROMPT_STYLE_001`):** integer PKs are the auto-generated unique
  ID; no semantic ID scheme. Cosmetic — confirm acceptable.

**Ordering/gates:** all build items are additive UI/service work — safe any time after the 7b
walkthrough signs off; none touch the resolution path or schema (except an optional index for the
scope filter). Do the taxonomy-reconciliation decision *before* the course-grouped view so labels
don't churn twice.

**Acceptance:** a user can expand a course and see its effective prompt set (shared defaults + its
scope locks); filter the library by course/cluster/project; category labels match CAS tab names
one-to-one; duplicate-content creation warns; archived prompts provably absent from CAS selection;
each deviation has an explicit stakeholder sign-off note.

**Status (2026-07-06):** all 8 build/decision items implemented and committed
(`18fe1ce`→`74a8e17`), plus 11.9 provenance capture from the doc-review instruction (`2c18df2`);
backend suite 273, frontend 46, build green. Remaining in this phase: the three deviation
sign-offs above (stakeholder), the opt-in-promotion confirmation (§5.2 note), the deferred
StyleVersion params column, and the user-facing acceptance pass over the new Courses tab + scope
filters (rides the 7b walkthrough — the :8001 walkthrough API needs a restart to pick up the new
endpoints).

---

## Phase 12 — Unified Library presentation (doc §3/§5.1 spirit, user-approved 2026-07-07)

**Decision (user: "Go ahead with perfection"):** unify the *presentation*, not the *model*.
The doc's world has one Prompt Library whose categories are the six CAS tabs; ours already has one
table — the remaining seam was the Library/Pipeline tab split in the console. `prompt_kind` and the
`(component_type, variant)` resolution keys stay exactly as they are (load-bearing: a freeform
category string must never steer generation, and the admin-only boundary rides on kind). What
changes is the UI and two additive API affordances:

- [x] **12.1 Unified list** — the console list page drops the Library/⚙Pipeline tab toggle; pipeline
  managers browse `kind=all` (both kinds in one list; non-admins keep sending nothing and stay
  server-stripped to library rows). Scope selects (project→course) now always visible to pipeline
  managers.
- [x] **12.2 CAS categories as first-class filter values** — the category select gains a
  "CAS Workflow" optgroup (admin-only) with the doc's six names; sent as a distinct `cas_category`
  param so a freeform library category named "CDD" can never collide. Server maps each label back
  to its resolution key in ONE place (`_CAS_CATEGORY_KEYS`): Assessment→quiz, Component→(generate,
  interactive) exact, Lesson Generation→generate minus interactive, others→component. Unknown label
  → empty; non-admin → empty (same no-leak shape as the scope filter).
- [x] **12.3 Promote-to-pipeline** — `POST /prompt-library/prompts/{id}/promote`
  (`prompt_library.manage` + pipeline-manager role, 403 otherwise). The kind flips **in place**:
  same id, same version history — still exactly one master record (doc §6). Registry name is the
  slugified title (`unique_prompt_name` suffixes past collisions, soft-deleted rows included);
  freeform category is cleared (classification is the resolution key now); legacy tags
  comma-string mirrored. Guards: valid component only, no follow-up hierarchies, non-empty
  content, double-promote 404s (row left the library write surface). **Promotion is inert for
  generation** — nothing fires until an admin makes the row a component default or binds a scope
  lock; this is the sanctioned §5.1 path ("create in library, appears in CAS") without letting a
  text field steer generation, and the opt-in answer to the §5.2 deviation question.
  Detail-page UI: "⚙ Promote to pipeline…" panel (library rows, pipeline managers only) with the
  six CAS slots and an inertness warning in the confirm.

**Explicitly rejected (same grounds as the §7.1 verdict):** dropping `prompt_kind` / letting
category strings control CAS visibility — downgrades a structured, permission-guarded
classification into a typo-prone convention.

### Phase 12b — freeform library retired from display (user directive 2026-07-07)

**Directive:** "We don't need to show the prompt library old prompts, only CAS related ones to be
surfaced." Display-level retirement only — the 20 carried-over library prompts stay in the DB
untouched (data-preservation rule; deleting them is a Phase-6-style gated decision, not taken
here), and the library API surface keeps working for anything that still references it.

- [x] **Console list = CAS pipeline prompts only.** The list requests `kind=pipeline`; category
  filter is exactly the six CAS names (freeform category options + category sort removed);
  `/meta` tags are pipeline-scoped for pipeline managers (non-admins keep their library scope —
  same no-leak fallback shape as browse).
- [x] **Nav:** the Library list entry + "＋ New Prompt" header action are pipeline-manager-only;
  non-managers hitting the list route are redirected to the Courses view. Readers' CAS surfaces
  (Flow, Courses) unchanged.
- [x] **New Prompt survives as the draft → promote path** (create library draft → detail →
  Promote to pipeline). A freshly created draft is not listed (pipeline-only list) but the create
  flow lands on its detail page where the promote panel lives.
- [x] **Flow's "Reference playbook prompts" rail removed** (found in the Flow-redundancy review,
  same directive): it keyword-matched *library* prompts per phase — the last remaining library
  surfacing in the console.
- **Not surfaced ≠ deleted:** requests/reviews/teams/audit pages and the library CRUD API remain
  functional but dormant; library rows are reachable by direct detail link. Whether to eventually
  delete/export the 20 legacy prompts is a stakeholder call (rides the existing "none obsolete?"
  open question).

### Phase 12c — Flow folded into Courses (user-approved 2026-07-07)

**Trigger:** reviewing Flow for redundancy showed its read half fully absorbed by the Phase 11/12
surfaces (Courses view, Library scope filters, "Used by" facets) — its irreplaceable remainder was
the Bind/Unbind pair. Console lands on two tabs matching the two object types: **Library** = the
prompt assets (edit/version/approve/default/promote), **Courses** = who uses them (read + lock
editor).

- [x] Each Courses slot row gains a "Change…" editor: per-slot prompt pool + scope picker
  (course/cluster/project/global, mirroring Flow) + Bind; slots showing a lock get Unbind. Same
  server rules as always (locks by reference; non-admins only approved prompts; errors verbatim).
- [x] Pool filtering mirrors the server's `_acceptable_variants` exactly (NULL slots → NULL-variant
  prompts; interactive slot exact; authored-variant slots → variant or base), so anything bound
  from a slot's editor is guaranteed to display in that slot and drive that slot's generation —
  stricter than Flow, which offered the whole component pool.
- [x] Flow retired: page deleted, nav entry + `plFlow` removed, `/prompt-library/flow` redirects to
  the Courses view (bookmarks safe), unused `resolveFixing` client helper dropped. Backend fixings
  endpoints unchanged.
- The 2026-07-03 locked decision "Console shape: primary view is flow-organized" is superseded by
  this shape (Library + Courses); the underlying principle (organize by the workflow hierarchy)
  survives in the Courses view.

### Phase 12d — "Request a Change" from the CAS tabs (user-approved 2026-07-07)

**Trigger:** capability check "can we consume/request prompts from the CAS flows?" — consume yes
(every tab's prompt panel lists the registry; note the panel dropdown is preview-only: generation
re-resolves lock chain → default unless the user explicitly overrides), request **no** (the PL
Requests workflow had no entry point from CAS).

- [x] `InlinePromptControls` (CDD/Blueprint/Generate tabs) and `PromptLibraryPanel` (Style tab)
  gain a "📬 Request a Change" action deep-linking to `/prompt-library/requests/new` with
  `component`, `promptId` (the selected registry prompt), and `context` (project/course names,
  where the panel has them) prefilled.
- [x] `RequestNewPage` reads the new params: title defaults to "<CAS category> prompt change",
  description seeded with the originating tab + context; everything stays user-editable.
- **Known UX trap (flagged, not fixed):** the CAS panel's prompt dropdown looks like a selector
  but is a preview — picking a non-default prompt does NOT change what generation uses (bind it
  in Courses, or explicitly override). Candidate follow-up: a hint in the panel.

### Phase 12e — coverage-audit gap closers (user-approved 2026-07-07)

From the final doc-coverage audit: the two buildable gaps (§5.2 loop-closer + the §8/§9
letter-gaps), all opt-in and additive.

- [x] **"Promote override to prompt"** — `POST /api/v1/prompts/from-generation`
  (`prompt.pipeline.edit`): reads the override captured in a CDD/Blueprint version's
  `generation_params` (11.9 provenance) and creates a registry prompt from it (slugged unique
  name, component = source type, inert until defaulted/locked; non-override versions 422).
  Frontend: `PromoteOverrideButton` appears above the content view on the CDD + Blueprint pages
  ONLY when the displayed version was override-generated; success navigates to the new prompt.
- [x] **Workflow-status filter (doc §8)** — list param `state` filters on the ACTIVE version's
  `workflow_state`; console gets an "Any Status / Draft / In review / Approved / Active" select.
- [x] **Archived opt-in + restore (doc §9 "unless explicitly enabled")** — list/search param
  `include_archived=1` (pipeline managers only; ignored for everyone else — no-leak), rows carry
  an additive `archived` flag; admins can OPEN archived rows by id (contract change pinned in the
  soft-delete characterization) and `POST /prompts/{pid}/restore` un-archives (404 for callers who
  couldn't see the row). Console: "🗄 Archived" checkbox, badges, ♻ Restore on card/row/detail.

**Tests:** service-level CAS-mapping suite (6 labels, generate split, case-insensitivity,
non-admin no-leak) + promotion-helper suite (slug, suffixing, soft-deleted names count) + API
suites for unified list/cas_category and the full promote contract (in-place flip, inertness,
write-surface exit, collision suffix, variant slot, 400s, reviewer 403). Backend 291, frontend 48
(CAS constants pinned against `componentCategoryLabel` round-trip), build green. Live-smoked on
rehearsal: 20 library + 8 pipeline under `kind=all`; CDD→`default_cdd_prompt`; promote round-trip
on a throwaway row (created → promoted → filtered under Blueprint → library PUT 404s → soft-deleted
clean).

---

## Locked decisions

- **Table base:** native CAS `prompts` tables are canonical; port `pl_*` features onto them; retire `pl_*`. (See "Table-base decision".)
- **Console API strategy:** keep two routers for this initiative; converge into one prompt API as a committed long-term follow-up after the console is stable.
- **Non-admin visibility:** non-admins never see pipeline prompts — stripped server-side (Phase 4) + pipeline tab not rendered (Phase 7b).
- **Data preservation:** `pl_*` holds 20 real admin-authored library prompts (verified 2026-07-05) — they are **migrated, not deleted**; Phase 2 carry-over is mandatory.
- **Reuse model:** reference/bind via `PromptFixing` (edit-once-propagates), **not** copy-on-inject. (Revisit only if per-course prompt reproducibility becomes a hard requirement.)
- **Roles:** admins create + approve + set-default; authors may inject already-approved, phase-appropriate prompts.
- **Binding scope:** full hierarchy global → project → cluster → course (already supported by `PromptFixing`).
- **Console shape:** ~~the Prompt Library tab's primary view is flow-organized~~ **superseded by Phase 12c (2026-07-07):** two tabs — Library (prompt assets) + Courses (hierarchy-organized resolution view with the lock editor).

## Open questions

- [x] ~~Phase 2: is there any real dev/staging data in `pl_*` today, or is it verified empty?~~ **Resolved 2026-07-05: NOT empty — 126 rows, 20 real admin-authored prompts. Carry-over migration is now mandatory (see Phase 2).**
- [ ] Confirm with stakeholders that none of the 20 migrated library prompts are obsolete/superseded before Phase 6 drops `pl_*` (default: keep all).
- [ ] Approval workflow (Phase 8): single-approver or two-person rule for pipeline-tier activation?
- [ ] Streamlit decommission timing: retire at 7b sign-off, or keep as a break-glass admin tool longer?
- [ ] Should `teams` be a general-purpose sharing primitive for future non-prompt features, or kept prompt-scoped?
- [ ] Should `ClusterPrompt` (auto-injected into Style context) eventually converge with `prompt_fragments` (Phase 9), or stay distinct?
- [ ] Are Cluster/Course AI-assist prompts (Phase 8 backlog) in scope for this initiative or a separate follow-up?
- [ ] `variant` for Style/CDD: confirm no near-term need before the partial-unique-index `(component_type, variant)` shape is locked (NULL `variant` must behave correctly in that index).
- [ ] Orphaned `tenants`/`tenant_id` schema (Phase 0S discovery): confirm the multi-tenancy experiment is dead, then schedule a cleanup migration (drop `tenants` + 9 `tenant_id` columns + their indexes + `users.project_id`/`users.is_platform_admin`) as a separate follow-up — NOT part of this initiative.
- [ ] Execute one RDS console snapshot-restore end-to-end before Phase 6 (the logical pg_dump/restore path is drilled; the console path is documented in `MIGRATION_RUNBOOK.md` but unexecuted — needs AWS console access).

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
| 2026-07-07 | **Request-queue loop closed + prompt-page copy pass** (walkthrough feedback: "make the request flow production grade"; "review all messages for clarity") | 12d follow-up + console polish (frontend-only) | Copy pass (commits `e7ad36b`, `b7eaa4b`): 15 fixes across 9 files — standalone-app leftovers ("Paste it into ChatGPT"), two references to the retired Flow view (now "Courses tab"), the CAS panel's contradictory "available across all generation components" hint, jargon → plain language ("soft-deleted", "errors loudly", "instant-deploy", "promotion is inert"), Courses-tab intro rewritten. Request flow: backend already returned `prompt_id`+`admin_notes` on every row, so closed frontend-only — admin request detail now shows the **linked prompt** (title link + archived badge + "✏️ Edit the prompt"), type=new requests get "＋ Draft this prompt" deep-linking to the New Prompt form with the request context prefilled into description (`?description=` support added, create-mode only), rejecting **requires an admin note**, a tip states the requester sees status+notes, admin queue rows link "🔗 prompt #N", and "My requests" now shows the **💬 Admin response** box (was invisible to requesters before). Frontend **53 passed**, build green. Known limits (unchanged scope): no push notification to the requester; single notes field, not a thread. |
| 2026-07-07 | **Studio-wide collapsible sidebars** (user follow-up to the chrome integration: "make the sidebar collapsible" → "do it studio-wide") | Host UI polish (not a plan phase; logged for completeness) | Commit `325b513`. Deliberately Studio-wide, not Prompts-only, to preserve the just-won consistency. New shared pieces: `useSidebarCollapsed` hook (one localStorage key `cas.sidebarCollapsed`, so the preference follows the user across layouts), `SidebarToggle` chevron («/»), `AppBrand compact` (logo-only). All three sidebars (Selection, Workspace, Prompts) collapse to the pre-existing `$sidebar-collapsed` 64px icon rail: user pill → role-colored initial dot with tooltip, nav/back/sign-out → icon-only with `title` tooltips (accessible names fall back to title), Workspace context/state pills + GettingStarted/TargetModel panels hidden, Selection create-forms become a ➕ that expands the sidebar and opens the form in one click. Prompts nav items gained icons (📖🎓📬⭐📜) shown in both states. Width animates (`$transition-base`). Frontend **53 passed**, build green. |
| 2026-07-07 | **Prompts section integrated into Studio chrome** (user: UI "looks like two different apps under one domain" — make it a single Content AI Studio) | 12b (frontend-only, presentation) | The "two apps" feel came from the PL keeping its standalone shell (own fixed top header with a "PromptLibrary" wordmark, own nav row, own footer, own palette). Rebuilt `PromptLibraryLayout` on the host sidebar pattern: same dark-indigo sticky sidebar as the dashboard (`AppBrand` block, "Signed in as" pill with role badge, "← Projects" back button, Sign Out), with the intra-feature nav (Library/Courses/Requests/Reviews/Audit) as sidebar links under a "📚 Prompts" section label and "＋ New Prompt" as a sidebar action. Design tokens aligned to the host palette (`--primary` #4f6ef7→#4f46e5, border/text/bg to the host grays, focus rings re-tinted) and the custom props moved off `:root` into `.pl-root` (no more global leak). Dead standalone-shell CSS removed (~250 lines: header/footer/logo/mobile-menu/help-pages; verified unreferenced by grep before deletion). Section renamed "Prompt Library"→"Prompts" everywhere user-visible: dashboard sidebar entry, CAS workspace panel titles (`PromptLibraryPanel`, `InlinePromptControls`), `PromoteOverrideButton` tooltip. Routes/paths (`/prompt-library`) and all page logic untouched; PL ToastProvider kept (renders inside `.pl-root`). Frontend **53 passed**, build green. |
| 2026-07-07 | **Console polish from walkthrough feedback** (duplicate-looking clusters + Visibility column question) | 12b (frontend-only refinements) | Two follow-ups, commits `206d054` + `1b51530`. (1) Duplicate cluster names are BY DESIGN (no unique constraint; names unique per project only; startup migration seeds a "General" cluster per project) — the dropdowns now disambiguate cross-project name collisions as "General — \<project\>" via shared `utils/clusters.js` (sorted, "(No cluster)" last, 5 pinning tests). Flagged for stakeholders: same-project duplicate names are also uncreatable-guarded nowhere (host-app scope, untouched). (2) The list's "Visibility" column was a pre-12b leftover (visibility is a library concept; the list is pipeline-only): renamed to **Status**, badge trimmed to ★ Default + capitalized workflow state — the ⚙ component/variant chip was fully redundant with the Category column / card footer (both show the CAS stage label incl. variant). Default vs deployed-state kept distinct (a non-default row can be active via locks — load-bearing). (3) **New Prompt is pipeline-only** (`efb817a`, user: "we have nothing to do with the old prompt library now"): the Prompt kind selector removed — create always builds a CAS pipeline prompt (with a static tip on inertness + approval gate); direct-URL hits by non-pipeline-managers redirect to Courses (matching the list page). Library CREATE path retired end-to-end: create-mode parent picker, "＋ Add follow-up" detail button, `plPromptNewChild`, and the unused `createPrompt`/`fetchRootPrompts` client calls removed. Library EDIT branch kept intact so the 20 legacy rows stay editable by id. Frontend **53 passed**, build green. |
| 2026-07-07 | **Console filters moved to cluster basis** (user: "Filters should be cluster basis instead of the Project basis") | 12b/12c (frontend-only refinement) | Library tab: Project→Course scope selects replaced by Cluster→Course (options derived from the one `/prompts/by-course` batch call; a "(No cluster)" bucket keeps unclustered courses reachable — display bucket only, it narrows once a course is picked, so no invalid `cluster_id` is ever sent). Courses tab: the Project dropdown became a Cluster dropdown (same "(No cluster)" bucket); group headers now lead with the cluster name. No backend change needed — `cluster_id` was already supported by the Phase 11 scope filter (upward chain + subtree + defaults, no-leak pinned). The Scope picker inside the slot bind editor deliberately keeps Course/Cluster/Project/Global — those are lock scopes, not filters. Frontend **48 passed**, build green. Live smoke on :8001: cluster_id=7 and 6 each → the 4 inherited defaults (no cluster locks exist yet — correct). |
| 2026-07-07 | **Verification pass over the Phase 12–12e stretch** (close-out) | 12–12e | Adversarial re-review of all 11 session commits + cross-cutting invariants. **Confirmed untouched:** every generation-critical backend file (loader/builder/repository/cdd/blueprints routers) is byte-identical to the verified Phase 11 state — today's work is console/API-additive only. **One real defect found & fixed:** `POST /prompts/{pid}/restore` let reviewers (library managers, not pipeline managers) restore archived rows they cannot see anywhere — visibility rule now matches reads ("nobody may restore what they cannot see"), pinned by a reviewer-role test. **One sharp edge closed:** the promote-to-pipeline confirm now warns when the prompt declares {{variables}} (strict enforcement arms on promotion — a stage that doesn't supply them fails loudly). Verified clean: no-leak boundary on every new param (cas_category/scope/include_archived/meta tags — all author-tested), unused-import sweep of the 24 touched frontend files, ruff byte-identical to baseline, no leftover Flow references. Suites: backend **303**, frontend **48**, build green. Live sweep on restarted :8001 (pid 145419): 8 pipeline rows, CDD+active filter exact, archived probe visible only via opt-in, by-course all 16 courses. |
| 2026-07-07 | **Phase 12e — coverage-audit gap closers** (doc coverage audit → "go ahead" on both buildable gaps) | 12e (new subsection) | §5.2 loop closed: `POST /prompts/from-generation` promotes a captured CDD/Blueprint override (11.9 provenance) into a registry prompt (opt-in, inert, unique slug, 422 on non-override versions, admin-gated) with a conditional `PromoteOverrideButton` on both CAS pages. §8 status filter (`state` on the active version's workflow_state). §9 "unless explicitly enabled": `include_archived=1` (admin-only, no-leak), archived rows openable by admins (soft-delete characterization re-pinned), `POST /restore`, console checkbox+badges+♻. Suites: backend **302** (+11), frontend **48**, build green, ruff baseline-identical after fixing the 1 introduced finding. :8001 restarted (old stale instance found holding the port and killed); live smoke: state=active → 6 rows, archived probe visible→restored→re-archived. |
| 2026-07-07 | **Phase 12d — request-from-CAS entry point** (capability check → "go ahead") | 12d (new subsection) | Found: consume=yes via each CAS tab's prompt panel (with the preview-only dropdown caveat now documented), request=no path from CAS. Added "📬 Request a Change" to `InlinePromptControls` + `PromptLibraryPanel`, deep-linking to the PL request form with component/promptId/context prefilled; `RequestNewPage` seeds title/description from those params. Frontend **48 passed**, build green; no backend changes. |
| 2026-07-07 | **Phase 12c — Flow folded into Courses** (user asked whether Flow/Library overlap; recommendation approved with "Go ahead with perfection") | 12c (new subsection) | Courses slot rows gained the Bind/Unbind lock editor (scope picker incl. global/project; pool filtered to the slot's `_acceptable_variants` — stricter than Flow's whole-component pool). Flow page deleted, nav entry + `plFlow` removed, `/flow` redirects to Courses. Also removed earlier in the review: Flow's library-prompt reference rail (`e35e7ae`, last library surfacing). Locked "console shape" decision annotated as superseded. Frontend **48 passed** (Flow nav test retired), build green, no backend changes. Live smoke: bind cdd course-lock on course 8 → by-course shows `course_lock` → unbind → back to `default`. |
| 2026-07-07 | **Phase 12b — freeform library retired from display** (user: "only CAS related ones to be surfaced") | 12b (new subsection) | Console list now `kind=pipeline` only; category filter = the six CAS names; `/meta` tags pipeline-scoped for pipeline managers (library fallback for everyone else, no-leak); Library nav entry + New Prompt action pipeline-manager-only; non-managers redirected to Courses. The 20 legacy library prompts stay in the DB untouched (display retirement, not deletion — their fate is a stakeholder call). New Prompt kept as the draft→promote path. Suites: backend **293**, frontend **49**, build green, ruff baseline-identical. Live smoke on :8001 (restarted, pid 140403): admin meta tags = pipeline tags only, CDD CAS filter → prompt 4. |
| 2026-07-07 | **Phase 12 — unified Library presentation** (eighth implementation session; user question "should we unify Library and Pipeline?" → recommendation "unify presentation, not model" → user: "Go ahead with perfection") | 12 (new) | All three items built, tested, live-smoked. Backend: `cas_category` filter param (label→resolution-key map in one place, no-leak for non-admins) + `POST /prompts/{id}/promote` (in-place kind flip, unique slug registry name, inert until defaulted/locked, pipeline-manager-gated) + helpers `slugify_prompt_name`/`unique_prompt_name`. Frontend: list page tab toggle removed (admins browse `kind=all`), category select gains admin-only "CAS Workflow" optgroup (six doc names), detail page gains the promote panel with inertness warning. 16 new backend tests + 2 frontend; suites backend **291**, frontend **48**, build green; ruff findings byte-identical to baseline (zero introduced). Rehearsal smoke: 20+8 under kind=all, CDD filter → prompt 4, full promote round-trip on a throwaway row then soft-deleted. :8001 restarted on this code (pid 138980) — ready for the user's walkthrough. Model-merge (dropping `prompt_kind`) explicitly rejected in the plan. |
| 2026-07-06 | **Full verification pass over the Phase 11 stretch** (seventh implementation session, close-out) | 11 | Commit `a9bf0b1`. Adversarial re-review of all 9 session commits. **One real defect found & fixed:** the by-course view applied no variant filter on NULL-variant slots — a fixing bound to a variant-keyed prompt (e.g. generate/interactive) displayed as the effective Lesson Generation lock while live generation (`_acceptable_variants=(None,)`) would skip it; `resolve_slot` now mirrors the loader exactly (NULL→NULL-only, interactive→exact-only, others exact-then-NULL, never sideways) + pinning test. Also closed the untested pipeline branch of duplicate-check (incl. unknown-kind coercion — never 500s) and fixed the 3 ruff findings this stretch introduced (import order, 2 unused test locals; remaining ruff/eslint noise is pre-existing repo-wide — `npm run lint` is broken by the unfinished ESLint-9 flat-config migration, flagged as a separate hygiene item). Verified clean: no category-bypass write paths (request flow creates no prompts; duplicate copies source category), no other CAS override surfaces, provenance set on all 4 CDD/BP prompt paths, permission gates match their pre-existing counterparts. Suites: backend **275**, frontend **46**, build green. Live smoke on rehearsal (throwaway :8002; the user's :8001 untouched): by-course for all 16 courses incl. a real course lock (course 7 style) rendering `course_lock`+scope name; full-body case-mangled dup-check self-match; blank-category 400; course-8 scope filter → exactly the 4 inherited defaults; counts 20/8. **:8001 still runs pre-Phase-11 code — restart before the acceptance pass over the new surfaces.** |
| 2026-07-06 | **Phase 11 executed — all 8 build/decision items** (seventh implementation session, cont.) | 11 | Six commits (`18fe1ce`, `39875e5`, `0ec8666`, `f8418cc`, `aebb30c`, `f197857`, `74a8e17`). (1) **Taxonomy**: `componentCategoryLabel()` maps keys→doc categories (generate→Lesson Generation, quiz→Assessment, generate+interactive→Component); wired through list/card/CSV/Flow/form/detail; keys unchanged. (2) **Course-grouped view**: `GET /prompts/by-course` (one-pass resolution replicating `resolve_fixed_prompt`; six doc-category slots + dynamic variant-default slots) + **Courses** tab with collapsible groups & source badges; live-smoked on rehearsal (16 courses). (3) **Scope filters**: `course_id`/`cluster_id`/`project_id` on browse (upward chain + downward subtree + defaults; non-admin no-leak pinned) + Project→Course selects on the Pipeline tab. (4) **Facets**: `GET /prompts/fixings` w/ scope names; detail-page read-only "Used by" chips (deliberately NOT freeform tags). (5) **Dup warning**: normalized-content probe + confirm on create (warns, never blocks). (6) **tag_type: declined** (typed facts are relational — deviation recorded). (7) **Category mandatory: enforced** (400 on blank create/explicit-blank update; 'General' fallback removed). (8) **Archived exclusion**: dormant `deleted_at` guards on every selection surface + resolution tier (zero reachable behavior change, flag-off identical) + 7 pinning tests. Suites: backend **270 passed** (+27 this stretch), frontend **46 passed**, build green. Remaining Phase 11: 3 deviation sign-offs (tenant, per-course copies, ID format) + acceptance pass over the new surfaces (needs :8001 restart to serve the new endpoints). |
| 2026-07-06 | **Walkthrough support + requirements-doc alignment** (seventh implementation session) | 7b (walkthrough fix), 11 (new) | (1) Walkthrough feedback fix (commit `29ddf0a`): Pipeline-tab Category column was empty by design (pipeline rows carry no freeform `category` — their key is `component_type`+`variant`); added `pipelineStageLabel()` deriving the display label from the real key and wired it into the list row, card footer, and CSV export — no schema change, label can never drift from what generation resolves. 4 new vitest cases (43 total green), build green. (2) Assessed the implementation against `Technical Requirements- Prompt Library and Content AI Studio Sync.docx`: data-model core (master record + dedup, mapping table, versioning, bidirectional sync, category-controlled CAS dropdowns) already delivered — often stronger than spec (`prompt_fixings` full scope hierarchy, enforced phase-appropriateness, append-only versions); gaps are presentation/metadata: course-grouped view, scope filters, taxonomy naming (Assessment/Component/Lesson Generation vs generate+variants), scope facets, duplicate-content warning, plus three deviations to confirm (no tenant column, no per-course prompt copies, integer IDs). Added **Phase 11 — Requirements-doc alignment** capturing all of it (8 build/decision items, 3 deviation sign-offs, ordering note: taxonomy decision before the grouped view; all additive, after 7b sign-off). |
| 2026-07-02 | Plan authored | — | Initial version (original merge direction) |
| 2026-07-03 | Design finalized | 0, 0.5, 1–10 | Reversed direction to native-tables-as-base; ran four code audits; built the unified prompt model + Library-as-console design + fair table-base analysis; locked three decisions; scheduled the name-key fix and editor consolidation; consolidated the whole document into a clean SSOT. No implementation started |
| 2026-07-03 | Database-safety pass | Safety section, 0S, 3, 10 | Audited deploy/backup/migration/test posture; found 2 blockers (Alembic mis-wired, create_all races Alembic) + 3 gaps (no rehearsal DB, zero generation/PL test coverage, no DB backup automation) + constraint data-landmines; added Safety/reversibility section, Phase 0S prerequisite, pre-flight checks in Phase 3, RDS-snapshot rollback in Phase 10. No implementation started |
| 2026-07-03 | Frontend-safety correction; chose this file over `PROMPT_CONSOLIDATION_PLAN.md` | 7b | Independently verified this document's key safety claims against code (Alembic `target_metadata` mis-wiring, `alembic upgrade head` in `.github/workflows/dev-fastapi-deploy.yml:103`, dormant-seed name-key bug, `lesson_generator` 2-version landmine, orphaned `PromptsPage.jsx`) — all confirmed accurate, so this file supersedes `PROMPT_CONSOLIDATION_PLAN.md` as the tracked document. Found and fixed one defect: Phase 7b's "delete PromptsPage.jsx + slice/thunks/service" would have broken live components (`InlinePromptControls.jsx`, `PromptDetailsModal.jsx`, `PromptLibraryPanel.jsx`) that import the same slice/thunks/service — corrected to delete only the unreachable page. No implementation started |
| 2026-07-03 | End-to-end break/delete audit | 6 | Full phase-by-phase safety review requested by user. Swept for raw SQL against `prompts`/`pl_*` (none — ORM-only, so `DROP TABLE` in Phase 6 is clean), all `pl_models` importers, and stray `PL_ATTACHMENTS_DIR` references. Found one real gap: `promptops_app/database.py:922` does `import promptops_app.pl_models` inside `init_db()` (to register `pl_*` on `Base.metadata` for `create_all()`) — not covered by the existing Phase 6 checklist, and if missed, deleting `pl_models.py` breaks app startup entirely (`ModuleNotFoundError` in the lifespan hook, not just Prompt Library). Added explicit removal + verification step to Phase 6. No other hidden dependencies found; confirmed nothing else in the plan is scheduled for deletion beyond `pl_*` tables/dir, `pl_models.py`, the obsolete migration script, and the orphaned `PromptsPage.jsx`. No implementation started |
| 2026-07-05 | Tracker consolidation | — | Retired the duplicate `PROMPT_CONSOLIDATION_PLAN 1 1.md` (an untracked, Windows-downloaded copy) in favour of a single git-tracked source of truth. Confirmed that copy was a strict superset of the old tracked file, carrying two fixes it lacked (Phase 7b: keep `promptsSlice`/`promptsThunks`/`promptsService` — live deps of `InlinePromptControls`/`PromptDetailsModal`/`PromptLibraryPanel`; Phase 6: also remove the `pl_models` import at `database.py:922` or startup crashes). Promoted that content into `PROMPT_CONSOLIDATION_PLAN.md` and deleted the stray `.md` + `:Zone.Identifier` files. Also confirmed all Prompt Library management features are retained (repointed tables, redesigned only where a redundancy/type-mismatch made a straight copy wrong). No implementation started |
| 2026-07-05 | `pl_*` data check + direction confirm | 2, 6, 7b, 8, Locked decisions, Open questions | Ran a read-only row count on prod RDS: `pl_*` is **NOT empty** — 126 rows incl. 20 curated admin-authored library prompts (Cengage CTE authoring playbook, 2026-06-02), 6 teams, 46 tags, 32 variables, 2 requests (native `prompts`=8 / `prompt_versions`=7, untouched). This **falsifies** the "expected empty, skip carry-over" assumption. Decision (user-delegated): **KEEP + migrate** — the existing two-kind direction is confirmed; the "delete all library data" re-scope was based on a false premise. Corrected: table-base row, current-state `pl_*` note, Phase 2 (data-check marked done, carry-over now mandatory), Phase 6 (drop only after migration verified), Open questions (data question resolved; added obsolete-prompt sign-off). Added the flow-organized console view + approval-gated reference-reuse to Phase 7b/8 and five Locked decisions (data preservation, reuse=reference, roles, scope, console shape). **Execution deferred to a separate session — no implementation started.** |
| 2026-07-05 | **Phase 0S executed** (first implementation session) | 0S, Open questions | All four 0S blockers cleared. (1) Rehearsal DB: `scripts/rehearsal_db.sh` + `cas-rehearsal-db` container (postgres:17 @55432) restored from a fresh prod dump — parity exact (47 tables / 8 / 7 / 126); prod `pl_*` also dumped to plain SQL (the Phase 2 pre-migration artifact, `backups/`, git-ignored). (2) Alembic: `env.py` repointed to the real `Base` (+ explicit `pl_models` import), missing `script.py.mako` added, ruff hook key fixed; autogenerate now sane. (3) Baseline revision `000100000001`: schema-only prod dump replayed on empty DBs, **self-stamps** on provisioned DBs (deploy-order-safe); empty-DB `upgrade head` reproduces prod schema byte-identically; `init_db()` DDL gated behind `DB_AUTO_DDL` (default off — Alembic owns schema), DML backfills kept (Postgres-only); app boots green against rehearsal. (4) Characterization tests: 67 new tests (builder/loader incl. dormant-seed-bug capture, CDD/BP/Style router resolution, `run_generation_job` hard-coded path, PL API contract) + CI step; had to first resurrect the entire test harness (pool kwargs crash under SQLite ×2 engines, wrong `Base` in conftest, missing `StaticPool`, `password=` kwarg, stale hash tests, fixture NOT NULLs) — suite now **147 passed** (was: could not even collect). (5) Backup drill: logical dump/restore executed + timed (~2 s); `MIGRATION_RUNBOOK.md` written; RDS console snapshot-restore still to be exercised before Phase 6 (Open questions). Discoveries recorded: orphaned `tenants`/`tenant_id` schema in prod (cleanup = separate follow-up); model↔DB drift (raw indexes, server_defaults, missing `courses.cluster_id` FK); plan correction — pipeline registry templates use `{{double}}` (same family as PL), `{single}`+`.format` lives only in hard-coded constants/fallbacks. **Phase 0S acceptance met except the console-side snapshot-restore exercise; Phase 1 (schema design) is unblocked.** |
| 2026-07-05 | **Phase 8 — name-key fix, management endpoints, approval gate, scope + Generate wiring** (fourth implementation session) | 8 | Five commits (`8964f19`…). (1) **Name-key fix** behind `PROMPT_RESOLVE_BY_COMPONENT` (ships OFF; off = byte-identical legacy): DB tier resolves scope fixing → component default (pipeline-kind-guarded) → legacy stem name → file; smoke on rehearsal green both ways; ⚠️ enablement precondition recorded — the 4 seeded default bodies are pure `{single}`-brace, convert before flipping. (2) **Management endpoints**: `PUT /{id}/default`, `POST /{id}/versions/{v}/state` (draft→in_review→approved→active; activation deploys + demotes), `PUT/DELETE /fixings` + `GET /fixings/resolve`; new admin-only `prompt.pipeline.edit` permission (+ reviewer blocklist); reuse-by-reference role split — authors may bind only approved, component-matching prompts. (3) **Approval gate**: non-admin version commits land as inactive drafts (`commit_draft_version`); deploy endpoint tightened to `prompt.pipeline.edit`; duplicate tags 409; every version writer now populates `version_number` (NOT NULL promotion unblocked). (4) **Scope wiring**: CDD/Blueprint routers pass project/cluster/course ids; course-scope lock proven to reach live CDD + Blueprint LLM calls (router-level tests). (5) **Generate wiring** (lesson + quiz): `run_generation_job` resolves `content_generation`/`quiz_generation` via the registry, constants demoted to fallback tier, template name/version recorded on `Generation`. Suite **198 passed** (was 162). Remaining Phase 8: Blueprint student/teacher variant split + `variant=interactive` (need variant-aware default resolution), strict variable enforcement, taxonomy seeds, pipeline-tags hygiene. |
| 2026-07-05 | **Full verification pass over the sixth session** (sixth implementation session, close-out) | 8, 7b, CI | Commit `b14ca65`. Re-reviewed every diff (tags convergence `288b9ad`, variables surface `bf93029`, vitest `31378c1`) + re-ran everything: backend **243 passed**, frontend 39 passed, build green; seeds smoke-tested on fresh SQLite incl. double-seed idempotency (tag_rows correct, no dupes); flag-off vs flag-on resolution verified live on rehearsal with a real session — flag=0 all five stems resolve `source=file` (byte-identical legacy), flag=1 cdd/blueprint/content/style resolve `source=db`, quiz stays file (its seed awaits sign-off — correct); live role matrix on `:8001` — author+reviewer `kind=pipeline` silently stripped (author sees 0: all 20 carried-over library prompts are `draft` visibility), variables endpoint 403 for both, pipeline detail 404 for author / 200+pipeline-block for admin; rehearsal at head `000100000003`. **Two defects found & fixed:** (1) a single tag >100 chars passed the 500-char field limit and would `DataError`→500 on PostgreSQL (`prompt_tags.tag` is String(100); SQLite tests can't catch it) — registry create/update schemas now 422 per-tag (PL router untouched: faithful legacy port, same column length pre-exists); (2) `ci.yml`'s four unquoted `sqlite:///:memory:` values fail strict YAML parsers on the trailing colon (pre-existing, PyYAML repros) — quoted, no semantic change. |
| 2026-07-05 | **Frontend test runner (vitest)** (sixth implementation session, cont.) | 7b (hygiene) | Commit `31378c1`. vitest 4 added as the frontend's first test runner (dev-dependency; `npm test`/`test:watch`); 39 unit tests across the pure PL utils — `prompt.js` (extractVarNames, findLegacyVarNames incl. the adjacent-`{x}{y}` consumed-neighbor regression + malformed-double neighbor cases, fillPromptContent, toLabel, starsDisplay, visibilityLabel incl. legacy standalone-app labels), `permissions.js` (manager gates; the admin-only pipeline gate a reviewer must NOT pass), `nav.js` (role-visibility of MAIN_NAV incl. Flow-for-all-readers, header-action path suppression), `csvExport.js` (promptsToCsv shape + RFC 4180 escaping). CI: Node-22 step (`npm ci` + `vitest run`) appended to `ci.yml` after the backend coverage step. All 39 green in ~200 ms; build unaffected. |
| 2026-07-05 | **`prompt_variables` declare/edit surface** (sixth implementation session, cont.) | 8 (follow-up) | Commit `bf93029`. Strict enforcement (`8c8397b`) shipped dormant — no pipeline row had declarations and there was no way to create them. Now: `PUT /api/v1/prompts/{id}/variables` (admin `prompt.pipeline.edit`, pipeline-only, replace-all) with the key guard that every declared name must be a `{{placeholder}}` in the active version — the failure mode this prevents is an admin typo-arming enforcement and 500ing every generation for that component. Console (admin edit form): detected placeholders as declare-checkboxes + label/hint, stale declarations flagged, save ordered after the version commit so validation sees the new deployed text; detail page marks the section "Declared variables (strict enforcement)". Live rehearsal round-trip verified incl. the 422 typo path. Suite **242 passed** (+8); build green; `:8001` restarted on this code. |
| 2026-07-05 | **Pipeline-tags hygiene — tags converged into `prompt_tags`** (sixth implementation session) | 8 (deferred item) | Commit `288b9ad`. `prompt_tags` rows are canonical for every kind; the legacy `prompts.tags` comma-string stays write-mirrored (Streamlit + rollback) and is the pipeline read fallback pre-backfill. New data-only migration `000100000003` (idempotent, pipeline-kind-scoped so its downgrade can never delete carried-over library tags) — rehearsed up/down/up on rehearsal: 46→53→46→53 rows, exactly the 7 pairs from the 6 legacy-tagged pipeline rows. Convergence: `set_prompt_tags` (dedup; kept rows reused — same-composite-PK delete+insert in one flush collides) feeds all 4 registry write paths; registry `search` scans `tag_rows`; `list_prompts_tagged` (live Generate pickers) matches rows OR legacy string; `_prompt_tags_list` prefers rows for all kinds — the PL `tag` filter's `PromptTag` join now genuinely matches pipeline rows (it silently never did); seeds write both stores. Prod: `000100000003` rides the Phases 3+2 runbook window (sits above them in the chain). Suite **234 passed** (+6). Walkthrough API restarted on `:8001` with this code. |
| 2026-07-05 | **7b flow view — build-out complete** (fifth implementation session, cont.) | 7b | Commit `1b8f0e3`. `PipelineFlowPage` (`/prompt-library/flow`, nav for all readers): Project→Course pickers; per-phase effective resolution (lock → default → file, + variant defaults); bind-by-reference row (component pool + context-gated scope select → `PUT /fixings`); Unbind; per-phase reference panel of migrated playbook prompts (category keyword match). Server keeps enforcing phase-appropriateness + authors-only-approved (list schema has no workflow_state — errors surfaced, not pre-filtered). Bind→resolve→unbind verified live against the rehearsal API. **All Phase 7b build items are now done** — remaining: the 7b acceptance walkthrough (console-edit → live generation proof, non-admin negative checks), the deferred tags-hygiene/prompt-variables-editor follow-ups, and the UX call on making Flow the tab's primary view. |
| 2026-07-05 | **7b list surfaces + walkthrough env** (fifth implementation session, cont.) | 7b | Commit `70d8e8b`. Admin-only Library \| Pipeline tab on the list page (`kind=pipeline` via `buildFilterParams`; server strip remains the boundary); kind-aware `VisibilityBadge` gives cards + table rows component/variant/Default/state badges; Duplicate/Delete hidden for pipeline rows; orphaned `PromptsPage/` deleted (slice/thunks/service kept — live Generate-screen deps re-verified). Also stood up the **7a/7b walkthrough environment** after the user hit "Could not load prompts" on the dev container: root-caused it to the container pointing at prod RDS *without* the Phase 3 schema (`UndefinedColumn: prompts.category` — expected until the gated prod apply; do NOT alembic the container), and ran the host-venv API on `0.0.0.0:8001` against rehearsal with the flag ON (127.0.0.1-only bind fails the WSL2→Windows localhost relay — that was the login "Network error"). Verified live: 20 library prompts + 8 pipeline rows w/ pipeline blocks over HTTP; frontend repointed via `VITE_API_BASE_URL=http://localhost:8001`. Remaining 7b: flow-organized console view, then the acceptance walkthrough. |
| 2026-07-05 | **7b console pipeline editing** (fifth implementation session, cont.) | 7b, 8 (small API additions) | Commit `d928614`. The console can now author/manage pipeline prompts end-to-end: **detail page** renders the SYSTEM+USER pair, component/variant/default/state badges, a version-history viewer with per-version workflow transitions (mirroring the backend's `_STATE_TRANSITIONS`), and make/clear-default; **form page** gets the dual editor (registry name immutable on edit; component/variant controls locked on default rows; change note; next-`vN` commit through the approval gate — admin instant-deploy, reviewer draft; meta-only edits skip the commit) + an admin-only kind selector on create. Architecture: console **reads** the Library API (new additive `pipeline` block in `prompt_to_dict`, pipeline-only version extras; library shapes pinned unchanged) and **writes** the registry API (`api/pipeline.js`; `canManagePipelinePrompts` = admin, mirrors `prompt.pipeline.edit`). Backend gaps closed: `variant` on create/read schemas; `PUT /{id}` re-keys component/variant (admin-only 403, default-row 422, empty-string clears). Suite **228 passed** (+5); build green. Remaining 7b: list surfaces (kind tab + badges + tags hygiene), flow-organized console view, retire `PromptsPage.jsx`, then the 7b acceptance walkthrough (needs a running stack + flag-on env). |
| 2026-07-05 | **Brace conversion + 7b start** (fifth implementation session, cont.) | 8 (enablement), 7b | Two commits (`6c3cd62`, this one). (1) **`{single}`→`{{double}}` conversion** — the flag-enablement data precondition is now dischargeable: shared converter (`prompts/brace_conversion.py`, identifier-tokens-only, idempotent, `extra_instructions_block`→`extra_instructions` rename because `blueprints.py` never supplies the legacy name); `seed_data()` seeds converted bodies (fresh DBs need nothing); `scripts/convert_seeded_prompt_braces.py` for existing DBs (dry-run/apply/localhost-guard/abort-on-unexpected-tokens). Rehearsed: 15 placeholders converted, re-apply 0, flag-on smoke renders all 4 stems from DB with zero residual placeholders. Suite **223 passed**. (2) **7b item 1 resolved by simplification**: with every resolvable row now `{{double}}`, kind-aware dual-syntax utils are obsolete (scan proved only 2 dormant demo rows still carry `{single}`); shipped `findLegacyVarNames` + a PromptFormPage warning against pasting legacy `.format` text instead. Frontend has no test runner (node-script + build verification; runner = 7b hygiene). Next 7b: dual editor + pipeline controls in PromptFormPage/PromptDetailPage, list surfaces + tags hygiene, flow-organized view. |
| 2026-07-05 | **Phase 8 — variant-aware resolution, Blueprint split, Generate interactive, strict variables** (fifth implementation session) | 8 | One commit (`8c8397b`). (1) **Variant-aware resolution**: `variant` param through `get_default_prompt`/`resolve_fixed_prompt`/`load_template`/`build_prompt` — exact `(component_type, variant)` wins, NULL-variant fallback only when a variant was requested, never sideways; fixing tier filters by the bound prompt's variant and falls through to broader scopes on incompatibility; `require_variant` = exact-variant-row-or-nothing. (2) **Blueprint split**: router passes `variant=teacher\|student`; independently-versioned rows once authored; NULL-variant seeded default serves both modes meanwhile (proven at router level). (3) **Generate interactive**: non-lesson/non-quiz components resolve only `(generate, variant='interactive')` — the lesson-shaped NULL default/stem/file can never hijack them; bespoke builder stays in control until an admin authors the row. (4) **Strict variables**: `build_prompt` enforces *declared* vars only (`prompt_variables` per-row, registry for file tier) via `PromptVariableError`; CDD/Blueprint surface it as `PROMPT_MISCONFIGURED` 500, generation jobs fail visibly — misconfigurations can no longer silently fall back to constants; undeclared placeholders stay lenient; all of it flag-gated (flag-off byte-identical). Suite **217 passed** (+19); rehearsal smoke green both flag states. **Taxonomy-seed proposal written into Phase 8 (awaiting content sign-off): seed blueprint teacher/student from the mode constants + quiz from its .md ({{double}} at seed time); do NOT seed interactive (would flatten ~10 bespoke prompts) or Cluster/Course (no endpoint)**. Tags hygiene deferred to ride 7b (noted in Phase 8). Remaining Phase 8: taxonomy seeds (after sign-off). |
| 2026-07-05 | **Phase 7a — frontend ID-compat audit + fixes** (third implementation session, cont.) | 7a | Swept `frontend/src/features/promptLibrary/` against the shipped int-id cutover. No UUID-format assumptions; ids opaque in URLs; `routes.jsx` untyped. Found + fixed two strict string-vs-int comparisons: `AdminRequestDetailPage` (page always redirected away — real breakage) and `PromptFormPage` parent-dropdown self-exclusion (cosmetic). Noted `createTeam(id, ...)`'s vestigial slug arg (no UI caller; server ignores it). `npm run build` green. Remaining for 7a sign-off (gates Phase 6): the manual browser walkthrough. |
| 2026-07-05 | **Phases 4+5 — service/router cutover to native tables** (third implementation session, cont.) | 4, 5 | Rewrote `promptops_app/services/prompt_library_service.py` and `app/api/v1/routers/prompt_library.py` onto the native models (signatures preserved; router+service cut over together since the router did its own data access). Mechanics: content = active version's `user_prompt_template` (`get_prompt_content`/`set_prompt_content`; silent edit = in-place snapshot overwrite, create_version = instant-publish bump w/ `'v'||n` + `active_version` sync); library relationships added to native `Prompt`; unified `audit_logs` (dotted `event_type`→`action`, short action derived, PL-prefix scoping); int ids everywhere; `PROMPT_ATTACHMENTS_DIR`; `kind=library|pipeline|all` browse with silent server-side strip for non-admins; PL writes structurally library-only (pipeline rows unreachable). Deliberate contract changes: team ids autoincrement int (slug→`name`), team 409 on name not slug — characterization tests updated + extended (reviews/requests/attachments round-trips). New `tests/unit/test_prompt_library_service_native.py` (shape parity + kind-separation acceptance incl. resolver-blindness to library rows). One bug caught: `set_prompt_content` originally read the stale `versions` relationship — same-session double-bump collided with the Phase 3 unique constraint; now DB-queried + `expire`. Suite **162 passed**; read-only service smoke against rehearsal's migrated data green (20 library rows, admin pipeline browse 8, render, content search). Next: Phase 7a frontend ID-compat pass, then Phase 6 drop gate. |
| 2026-07-05 | **Phase 2 carry-over script written + rehearsed** (third implementation session) | 2 | Committed the outstanding Phase 0S/1/3 work first (`0f4616d`, 24 files). Wrote `scripts/migrate_pl_to_native_prompts.py` (idempotent; `--dry-run`/`--apply`; in-transaction parity verification that aborts the apply on any failure; audit_logs-based pl_id→native_id mapping + natural-key adoption fallback; run-time head-snapshot re-assertion w/ reconciliation versions; refuses non-local targets without `--allow-non-local` + a `pl_*` dump in `backups/`; requires Alembic ≥ `000100000002`). Rehearsed on a fresh prod clone (reset → `alembic upgrade head` → dry-run → apply → re-apply): **126-row parity** (20 prompts / 20 versions / 46 tags / 32 vars / 6 teams / 2 requests), re-apply migrates zero rows, adoption fallback exercised (wiped the 28 mapping rows → all adopted, nothing duplicated), pipeline rows untouched (8/7), `{{double}}` vars byte-preserved, exactly-one-active-version invariant holds. One bug caught in rehearsal and fixed: request mapping recorded under a mismatched entity key → duplicated the 2 requests on re-run. Full suite **147 passed**. Discovery: prod has two library prompts sharing title+created_at ('Pathway Specific Dev Prompt', Blueprint vs Storyboard) — adoption natural key includes category because of it. **Prod apply NOT done** — gated (runbook: RDS snapshot + fresh dump + quiet window; same window as the Phase 3 prod apply). Next: Phase 4 service-layer rewrite. |
| 2026-07-05 | **Phase 1 signed off + Phase 3 rehearsed** (second implementation session) | 1, 3, 0S round-trip | **Phase 1:** every `pl_*` column mapped against the real models + prod-parity data; spec amendments: partial unique index must be `NULLS NOT DISTINCT` (all 4 defaults have NULL `variant`; prod verified PostgreSQL 17.9), `audit_logs.summary` added + full `pl_audit_events`→`audit_logs` field mapping declared (`event_type`→`action`, short action derived), backfill ordering `(created_at, id)` (prompt 1's versions share a timestamp), `prompt_kind` default flipped to `'pipeline'` (deploy-gap safety), `version_number` NOT NULL deferred to Phase 4 (live writers don't set it yet). Verified findings: `description`→existing column, `created_by`→`owner`; scalar `pl_prompts.team_id` unused (0 rows); teams `id==name` for all 6; all 20 pl prompts `draft`, 1 version each, zero silent-edit divergence (Phase 2 script must re-assert at run time); 2 versionless native prompts (ids 7, 8 — service/console must tolerate); legacy `prompts.tags` comma-string live on 6 rows (stays for pipeline kind; `prompt_tags` canonical for library); no table-name conflicts. **Phase 3:** revision `000100000002` (additive: 8+2+4 columns, 7 tables, partial unique index w/ live-fire duplicate-rejection test, unique constraint, backfills, embedded pre-flight) + lockstep model updates (7 new model classes). Rehearsed on fresh prod clone: upgrade/downgrade/upgrade round-trip clean (zero residuals, data intact — also closes the 0S round-trip checkbox), autogenerate drift = known-drift only, app boots, suite **147 passed**. **NOT applied to prod** — that goes through the runbook (snapshot + dump + quiet window). Next: Phase 2 carry-over script (`migrate_pl_to_native_prompts.py`), which now has a live target schema on rehearsal. |
