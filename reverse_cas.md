# Reverse CAS — IMSCC Course Import Implementation Plan

> **What this is:** A session-by-session build plan for merging the **Reverse Pipeline** (Canvas IMSCC course import) into Content-AI-Studio, grounded in the *actual* codebase (not the idealized doc structure). Source specs: `PRD-CAS-Reverse-Pipeline.md` and `Technical-Requirements-Reverse-Pipeline-v2.md`.
>
> **Prime directive (never violate):** The *scratch* course path (New Course → Style → CDD → Blueprint → Generate → Editor → Export) must behave **exactly as today**. The importer is **additive and decoupled** — it produces the *same DB rows* the scratch pipeline produces, just filled from parsed IMSCC content instead of from an LLM. Downstream (Editor/CDD/Blueprint/Style/Workflow/Analytics) stays **origin-agnostic**.

---

## 0 · How to use this doc across sessions (READ FIRST every session)

This plan is chunked into **self-contained sessions** so we never lose context or hit a session limit. Each session has: a goal, the exact files to touch, precise steps, exit criteria, a verification step, and a rollback note.

### Start-of-session ritual (paste this to Claude each new session)
```
Read reverse_cas.md. We are resuming the Reverse CAS import feature.
Go to "Session <N>" and continue from the first unchecked item in the
Session Progress Log. Re-read the "Ground-truth codebase facts" section
before writing any code. Do not touch the scratch path.
```

### End-of-session ritual
1. Tick the completed items in the **Session Progress Log** (bottom of this doc).
2. Add a one-line note in the log: what landed, what's pending, any surprise.
3. Leave the repo green (`New Course` still works; app boots).

> **⚠️ DO NOT COMMIT.** Claude must **never** run `git commit`, `git push`, `git add`, or any staging/committing command during this project — not per-session and not at the end. The user (Shubham) commits **manually** after reviewing all changes once every session is complete. At most, summarize what changed and (only if asked) run read-only `git status` / `git diff`. All work stays in the working tree until the user commits it themselves.

### Golden rules for every session
- **Never commit.** No `git add` / `git commit` / `git push` at any point — the user commits manually after reviewing the whole feature. All changes stay in the working tree.
- **Match the existing CAS look & conventions.** The import UI must look and feel like the rest of the app — reuse the existing React components, SCSS-module styling, Redux-Toolkit slice/thunk pattern, and the shared api client. The backend must use the existing FastAPI router/schema/dependency conventions. **No new UI kit, no new HTTP client, no new state-management style.** (Details in §1 "UI & API consistency contract".)
- **Feature flag is king.** Everything hides behind `IMPORT_COURSES_ENABLED` (default **false**). When off, the app is byte-for-byte today's behavior.
- **New files only**, except the small **additive touchpoints** explicitly listed per session.
- **Never** add `if source_type == "imscc"` branches into generation/editor/CDD/blueprint/style/workflow/analytics code.
- **Never** change an existing column, endpoint contract, or scratch prompt template.
- **Reuse existing functions exactly as the scratch path calls them.** If you need different behavior, write a *new* function — never add a parameter to a hot-path function.

---

## 1 · Ground-truth codebase facts (verified — don't re-discover)

These were confirmed by reading the code. Trust them, but re-verify a line number before editing (the repo moves).

### Backend layout
- `app/` = FastAPI API layer (routers, schemas, core config/db/deps).
- `promptops_app/` = domain layer (SQLAlchemy models, repositories, services, jobs, exporters, parsers, prompts).
- Two entry surfaces share the same job infra: legacy Streamlit (`promptops_app/pages/`) and FastAPI (`app/api/v1/`). **We build only on the FastAPI surface.**

### Models (`promptops_app/database.py`) — key facts
- **No `source_type` / `import_id` columns exist** on `courses` (or anywhere). Session 0 adds them.
- FK discipline is **inconsistent**: `Course.active_cdd_id/active_blueprint_id/active_style_id`, `CourseModule.course_id`, `Generation.project_id/course_id`, `GenerationJob.result_entity_id`, CDD/Blueprint `project_id/course_id` are **plain `Integer`, no DB-level FK**. Import code must set these ids **manually**; no ORM cascade for them.
- `Course` (`courses`, ~L1102): `id, project_id (NOT NULL, FK), cluster_id (FK, nullable), name, description, created_by, created_at, is_active, active_style_id, active_cdd_id, active_blueprint_id, config_model_choice, config_expert_domain, config_target_audience, config_audience_category`.
- `CourseModule` (`course_modules`, ~L557): `id, course_id (NOT NULL, indexed), title, position (default 0), created_at, updated_at`.
- `Generation` (`generations`, ~L537): `id, prompt_name (NN), prompt_version (NN), block_type (NN), topic (NN), output_text (NN), cdd_id, cdd_version, blueprint_id, blueprint_version, project_id, course_id, created_by, created_at`.
- `Block` (`blocks`, ~L573): `id, generation_id (FK), block_type, block_label, content, sources (JSON string), position, eval_score, eval_report, ai_review, workflow_state (default "draft"), version_num, content_html, content_html_at, module_id (FK course_modules, nullable), created_at, updated_at`, plus many workflow columns. **`content_html` exists** — use it as the HTML fidelity fallback.
- `GenerationJob` (`generation_jobs`, ~L790): **`id` is `String(64)` (caller-supplied)**, `job_type` (default "generation"), `status` (default "queued"), `progress` (0-100), `current_step`, `input_payload_json`, `result_json`, `result_entity_id`, `error_message`, `project_id`, `course_id`, `created_by`, timestamps. **Property aliases:** `request_json`↔`input_payload_json`, `stage`↔`current_step`, `generation_id`↔`result_entity_id`.
- `CourseDesignDocument` (~L919) + `CDDVersion` (~L937, fields: `cdd_id, version, version_number, full_content, sections (JSON), generation_params, change_reason, is_active, created_by, created_at`).
- `ModuleBlueprint` (~L956) + `BlueprintVersion` (~L976, same shape as CDDVersion but `blueprint_id`).
- `Style` (`styles`, ~L135: note `id` int PK **and** `style_id` unique string slug) + `StyleVersion` (~L162) + `StyleDocument` (~L185 join table). `Course.active_style_id` → `styles.id` (the int).

### Jobs (`promptops_app/jobs/`)
- **Dispatch is a direct function reference, not a registry.** `job_runner.submit(fn, job_id)` forwards to a module-level `ThreadPoolExecutor` (`job_runner.py:24-41`). `job_type` is only a DB label.
- Create-job pattern (FastAPI): `job_repository.create_job(db, ..., request_params=<dict>, job_type=..., project_id, course_id)` → returns 32-char hex id → then `job_runner.submit(fn, job_id)`. See `app/api/v1/routers/generations.py:168-176`.
- `run_generation_job(job_id)` (`generation_jobs.py`) opens its **own** `SessionLocal()`, reads `params = json.loads(job.request_json)`, drives stages via `set_running(db, job, progress, label)` and finishes with `set_completed(db, job, entity_id)` / `set_failed(...)` (`job_status.py:40-84`).
- Block-persist reference loop: `generation_jobs.py:458-493` (create `Generation`, commit, then loop `Block(...)` rows, commit).
- Status constants (`job_status.py`): `queued/running/completed/failed/cancelled`; `TERMINAL`, `ACTIVE` sets.
- **Our importer:** new `promptops_app/jobs/import_jobs.py` with `run_import_job(job_id)`; it shares only the generic runner + `job_status` helpers. It must **not** call `run_generation_job`.

### Exporter (to invert) — `promptops_app/exporters/`
IMSCC zip layout produced by `imscc_exporter.build_imscc` (the importer is its inverse):
- `imsmanifest.xml` — org tree TOC: `organization > item(root) > item(module) > item(leaf)`. Leaf `identifierref` → `<resource identifier=… href=…>`. Resource `type`: `webcontent` (pages) vs `imsqti_xmlv1p2/imscc_xmlv1p1/assessment` (quizzes). Skip the two `course_settings` `learning-application-resource` resources.
- `course_settings/module_meta.xml` — **authoritative module + item order** (`<position>` 1-based). Cross-ref items → manifest resources via `identifierref`. `content_type` = `WikiPage` | `Quizzes::Quiz`.
- `wiki_content/block_{idx}.html` — one page per content block. Body wrapped as `<style>…locked CSS…</style>` + `<div class="cas-lesson">…</div>`. **Importer must:** read `<title>`, strip the leading `<style>` block, unwrap `.cas-lesson`, then convert HTML→markdown.
- `assessment/block_{idx}.xml` — QTI 1.2 quiz.
- `web_resources/…` — media placeholders (SVG stubs today).

**Gaps that are NET-NEW code (do not exist yet):**
1. **HTML → markdown** converter. `markdown_html.py` only has `markdown_to_html`. Add a reverse util (recommend `markdownify`; add to `requirements.txt`). Always also keep original HTML on `Block.content_html` as fidelity fallback.
2. **QTI-XML → questions** reader. `qti_parser.parse_assessment_questions(content)` parses **source markdown/HTML**, NOT exported QTI XML. Write a new QTI-XML parser (read `<item ident title>`, `itemmetadata/qtimetadata question_type`, `presentation/material/mattext`, `response_lid/render_choice/response_label`, `resprocessing/respcondition/conditionvar/varequal`, `itemfeedback`). Emit content in the **same normalized markdown shape** the exporter/`qti_parser` expects, so round-trip and Editor rendering "just work".

Reusable as-is: `qti_parser.is_assessment_block(block_type, label)`, `file_parser.parse_uploaded_file(uploaded_file) -> (filename, content, error)`, `canvas_html_layout._BODY_RE` / `_extract_body` (reference for body extraction).

### Frontend (`frontend/src/`)
- Create-course is an **inline expandable form** in `components/layout/SelectionLayout/SelectionSidebar.jsx` (course variant ~L404-427), submitting via `onCreateCourse`.
- Handler: `features/dashboard/pages/CoursesPage/CoursesPage.jsx` → `handleCreate(data)` (~L78-89) → `dashboardService.createCourse(pid, {...data, cluster_id})` → then `dispatch(fetchCoursesThunk(cid))`.
- Service: `features/dashboard/services/dashboardService.js:20` → `api.post(PROJECTS.COURSES(projectId), data)`. **No create-course thunk** (direct call).
- Endpoints: `services/endpoints.js` (grouped consts; `PROJECTS.COURSES = (id)=>'/api/v1/projects/${id}/courses'`, `COURSES` group L57-64). **No `IMPORTS` group yet.**
- Routes: `app/routes.jsx`. Selection: `/projects/:projectId/clusters/:clusterId/courses` → `CoursesPage`. Workspace: `/workspace/:courseId/{sources,style,cdd,blueprint,generate,editor,workflow,export,analytics}`. Route strings in `utils/constants.js` `ROUTES`.
- **Interception point:** wrap `CoursesPage.handleCreate` with a New-vs-Import modal. "New" → unchanged `createCourse`. "Import" → route to the wizard.

### UI & API consistency contract (import feature must look/behave like existing CAS)
The reverse-pipeline UI and endpoints are **new features built the CAS way**, not a bolt-on. Reuse the established patterns:

**Frontend (React):**
- **Structure:** one feature folder `features/import/` mirroring existing features (`features/dashboard`, `features/editor`, …): `pages/<Page>/<Page>.jsx` + co-located `<Page>.module.scss`, `importSlice.js`, `importThunks.js`, `services/importService.js`.
- **Styling:** **SCSS modules** (`*.module.scss`) only, consuming shared tokens from `styles/variables.scss` + `styles/mixins.scss`. No inline style systems, no Tailwind, no CSS-in-JS. Match spacing/typography/color of existing pages.
- **Shared components:** reuse from `components/common/` and `components/layout/` (buttons, modals, cards, spinners, toasts). The **CreateCourseModal** and **ImportWizardPage** must follow the existing modal pattern seen in `features/dashboard/components/EditEntityModal/` + `ManageUsersModal/` (`.jsx` + `.module.scss`). Progress UI should reuse existing job/progress components if present (`components/generation`, `ui/job_progress` equivalents) rather than a new widget.
- **State:** Redux Toolkit slice + thunks, exactly like `dashboardSlice.js`/`dashboardThunks.js`. Use `createAsyncThunk` for `validate/start/pollJob`.
- **HTTP:** call through the shared `services/apiClient.js` (`api.get/post`) with paths from `services/endpoints.js` — add an `IMPORTS` group there; never hand-roll `fetch`/`axios`.
- **Routing/feedback:** register routes in `app/routes.jsx` and use the same toast/error-handling conventions (`toast.success/error`) as `CoursesPage.handleCreate`.

**Backend (FastAPI):**
- New router `app/api/v1/routers/imports.py` using the same `APIRouter` + `Depends(require_permission(...))` + `Depends(get_db)` + tenant-context conventions as `courses.py`/`generations.py`; register in `app/api/v1/router.py` the same way (follow the module-adding recipe in that file's header).
- Request/response DTOs as Pydantic models in `app/schemas/import_.py`, mirroring `schemas/course.py` (e.g. `CourseCreateRequest`, `CourseRead`) — typed, `response_model=` on every route.
- Reuse the existing job-status endpoint (`GET /jobs/{jobId}`) for progress; don't invent a parallel progress channel.
- Errors via the app's existing exception patterns (`app/core/exceptions.py`), not ad-hoc `HTTPException` strings where a shared error exists.

### Backend create-course (scratch — DO NOT TOUCH)
- `app/api/v1/routers/courses.py:71-97` → `POST /projects/{project_id}/courses`, `require_permission("course.create")`, schema `CourseCreateRequest {name, cluster_id?}` (`app/schemas/course.py`). Router mounted **no prefix** in `app/api/v1/router.py:71`.

### Config / migrations
- Config: `app/core/config.py` → `AppSettings(BaseSettings)`, singleton `settings` (L267). Feature-flag section ~L187-196 (e.g. `sync_quality_checks`). Add flags here as `Field(default=False, alias="ENV_VAR")`.
- Alembic: `migrations/versions/` (latest `…0008_course_modules.py`). New migration = **ADD COLUMN (nullable) + CREATE TABLE only**; fully reversible in `downgrade()`.

---

## 2 · Target file inventory (what we will create)

New backend package `promptops_app/importers/`:
| File | Responsibility |
|---|---|
| `internal_model.py` | Dataclasses: `ICourse, IModule, IPage, IAssessment, IResource` (+ stable provenance ids). |
| `package_extractor.py` | Safe unzip (zip-bomb + path-traversal guards, size cap), locate `imsmanifest.xml`, stage assets to temp. |
| `canvas_parser.py` | Parse manifest + `module_meta.xml` + wiki pages + QTI XML → `ICourse`. |
| `qti_xml_parser.py` | **NEW** QTI-1.2-XML → normalized questions (gap #2 above). |
| `html_to_markdown.py` | **NEW** page-body HTML→markdown (gap #1; strip locked CSS, unwrap `.cas-lesson`). |
| `editor_builder.py` | `ICourse` → `CourseModule`/`Generation`/`Block` rows (+ provenance). Mirrors `generation_jobs.py:458-493`. |
| `reverse_blueprint.py` | Per-module content → `reverse_blueprint` prompt → `BlueprintVersion` (active) → pin `active_blueprint_id`. |
| `reverse_cdd.py` | Blueprints + course content → `reverse_cdd` prompt → `CDDVersion` (active) → pin `active_cdd_id`. |
| `style_analyzer.py` | Sample lessons → `style_analysis` prompt → `Style`/`StyleVersion` → pin `active_style_id`. |
| `provenance.py` | CRUD for `import_provenance` (Canvas-item ↔ CAS-entity map). |
| `imscc_importer.py` | Orchestrator: validate → extract → parse → build → reverse-gen. |

New job / router / schemas:
| File | Responsibility |
|---|---|
| `promptops_app/jobs/import_jobs.py` | `run_import_job(job_id)`: load job, run stages, update progress, set `result_entity_id=course_id`, per-item retry-then-flag. |
| `app/api/v1/routers/imports.py` | Endpoints (Section: API). Feature-flag guarded. |
| `app/schemas/import_.py` | Request/response DTOs. |
| `promptops_app/prompts/templates/reverse_cdd.*` / `reverse_blueprint.*` / `style_analysis.*` | New prompt templates (read-only additions; never replace scratch prompts). |

New frontend feature `frontend/src/features/import/`:
| File | Responsibility |
|---|---|
| `components/CreateCourseModal/` (in `features/dashboard`) | New-vs-Import choice modal. |
| `pages/ImportWizardPage/` | Upload & validate → progress → done. |
| `importSlice.js` / `importThunks.js` | Local state + `validatePackageThunk`, `startImportThunk`, `pollImportJobThunk`. |
| `services/importService.js` | Calls new endpoints; reuses api client. |

Additive edits (small, listed per session): `app/core/config.py`, `app/api/v1/router.py`, `requirements.txt`, `frontend/src/services/endpoints.js`, `frontend/src/app/routes.jsx`, `frontend/src/features/dashboard/pages/CoursesPage/CoursesPage.jsx`.

New API surface (all under feature flag):
| Method + path | Purpose |
|---|---|
| `POST /imports/validate` | Multipart IMSCC → validate only (counts + warnings), no DB writes. |
| `POST /projects/{projectId}/imports` | Create course shell + `course_imports` row + enqueue `GenerationJob(job_type="import")`. Returns `{course_id, import_id, job_id}`. |
| `GET /imports/{importId}` | Import record + latest status/warnings. |
| `GET /jobs/{jobId}` | **Existing** — reuse for progress polling. |
| `POST /imports/{importId}/retry` | Re-run reverse-gen only (Editor already built). |
| `POST /imports/{importId}/cancel` | Cancel running job (reuse job cancel). |

---

## 3 · Phases → Sessions map

The tech doc's 5 phases (0–4) are split into **8 sessions** sized to avoid context limits. Each session is independently shippable and leaves the app green.

| Session | Phase | Deliverable | Depends on |
|---|---|---|---|
| **S0** | 0 · Scaffolding | Migration (columns + tables), feature flag, empty imports router, `importers/` package skeleton, FE modal (Import disabled). | — |
| **S1** | 1 · Ingest A | `internal_model`, `package_extractor`, manifest + `module_meta` parsing, `/imports/validate` endpoint + unit tests. | S0 |
| **S2** | 1 · Ingest B | `html_to_markdown`, `qti_xml_parser`, wiki-page + assessment + resource parsing → full `ICourse`. | S1 |
| **S3** | 2 · Reconstruct | `editor_builder` + `provenance`, `import_jobs` stages 1–4, `POST /projects/{id}/imports`, wire job. | S2 |
| **S4** | 2 · Reconstruct FE | Import wizard (upload → validate → progress → open workspace), enable modal "Import". | S3 |
| **S5** | 3 · Reverse-gen A | `reverse_blueprint` + `reverse_cdd` + prompts; pin actives; import_jobs stages 5–6. | S3 |
| **S6** | 3 · Reverse-gen B | `style_analyzer` + prompt; pin `active_style_id`; stage 7; retry endpoint. | S5 |
| **S7** | 4 · Round-trip + polish | Provenance-aware export (optional read), analytics badge, warning surfacing, E2E round-trip test. | S3–S6 |

> **Value milestone:** after **S4** an imported IMSCC opens fully in the Editor and is editable (Goal B structure). S5–S6 add the AI artifacts. S7 closes the round trip.

---

## 4 · Sessions in detail

### Session S0 — Scaffolding (safe, no behavior change)
**Goal:** Land the skeleton and the flag so every later session is purely additive. Goal A green with flag on **and** off.

**Backend steps**
1. **Migration** — new file in `migrations/versions/` (revision after `…0008`). In `upgrade()`:
   - `add_column('courses', Column('source_type', String(20), nullable=True))` (values: `None`/"scratch" | "imscc"; existing rows read as scratch).
   - `add_column('courses', Column('import_id', Integer, nullable=True))`.
   - `create_table('course_imports', ...)` → `id, course_id, project_id, uploaded_by, package_name, package_size, status, structure_counts_json (Text), warnings_json (Text), provenance_ready (Boolean default False), created_at, completed_at`.
   - `create_table('import_provenance', ...)` → `id, import_id, canvas_identifier, canvas_type (page|quiz|module), cas_entity_type (block|module), cas_entity_id, created_at`.
   - `downgrade()` drops both tables + both columns. **Only ADD/CREATE — nothing altered/dropped from existing tables.**
2. Add ORM models `CourseImport`, `ImportProvenance` to `database.py`; add nullable `source_type`, `import_id` to `Course` model. (Leave all existing columns untouched.)
3. **Feature flag** in `app/core/config.py` feature-flags section: `import_courses_enabled: bool = Field(default=False, alias="IMPORT_COURSES_ENABLED")`. Add `IMPORT_COURSES_ENABLED=false` to `.env.example`.
4. Create `promptops_app/importers/__init__.py` (empty package).
5. Create `app/api/v1/routers/imports.py` with an `APIRouter` that mounts **only when `settings.import_courses_enabled`** (or always-mounted but every route returns 404/403 when flag off). Register in `app/api/v1/router.py` next to courses (guarded by the flag). Add one `GET /imports/health` returning `{enabled: bool}` to prove wiring.
6. Add `app/schemas/import_.py` with placeholder DTOs.

**Frontend steps**
7. Add `CreateCourseModal` under `features/dashboard/components/`: two options — **New Course** (calls today's `createCourse` path unchanged) and **Import Course** (**disabled**, tooltip "coming soon").
8. Wire `CoursesPage.handleCreate` so the create action opens the modal; "New Course" reproduces the exact current call + `fetchCoursesThunk`. Gate the modal's Import option behind a FE flag mirroring the backend (read from a config/env or a `/imports/health` check).

**Exit criteria**
- `alembic upgrade head` then `alembic downgrade -1` both succeed (reversible).
- App boots; `New Course` works exactly as before; creating from scratch is unchanged.
- With flag **off**: Import option hidden/disabled; imports router not reachable.
- `GET /api/v1/imports/health` returns `{enabled:true}` only when flag on.

**Verify:** run the app, create a scratch course end-to-end (Style→CDD→Blueprint→Generate→Editor→Export) — confirm identical to today. Toggle flag; confirm no other change.

**Rollback:** `alembic downgrade -1`; delete new files; revert the two touched files.

---

### Session S1 — Ingest A: extract + structural parse + validate
**Goal:** Turn a real IMSCC into structural counts and a partial `ICourse` (modules + item list), exposed via `/imports/validate`.

**Steps**
1. `importers/internal_model.py`: dataclasses `ICourse{title, modules[], resources[], warnings[]}`, `IModule{title, position, items[], provenance_id}`, `IPage{title, html, markdown?, provenance_id}`, `IAssessment{title, questions[], provenance_id}`, `IResource{filename, path, provenance_id}`. Provenance id = stable Canvas `identifier` from manifest.
2. `importers/package_extractor.py`: `extract(package_path, workdir) -> ExtractResult`. Guards: reject > size cap (config), zip-bomb ratio guard, path-traversal (`..`/absolute) rejection, isolated temp dir. Locate `imsmanifest.xml` (fatal if absent).
3. `importers/canvas_parser.py` (part 1): parse `imsmanifest.xml` (org tree) + `course_settings/module_meta.xml` (authoritative order). Build modules + ordered item stubs (type page/quiz via resource `type` + `content_type`). Skip `learning-application-resource`. Collect warnings for unsupported items (e.g. external LTI). Do **not** parse page bodies yet (S2).
4. `POST /imports/validate` in `imports.py`: multipart upload → stream to temp → extract → structural parse → return `{structure_counts:{modules,pages,quizzes,assignments,discussions,resources}, warnings[]}`. **No DB writes.** Permission: `course.create`. Clean up temp always.
5. Unit tests in `tests/` (add `tests/importers/`) against a **real Canvas IMSCC** sample (obtain/export one; also generate one via the existing `imscc_exporter` for a controlled round-trip fixture). Assert counts + ordering.

**Exit criteria:** `/imports/validate` returns correct counts + warnings on a real Canvas export **and** on a CAS-exported IMSCC (self round-trip). Fatal cases (corrupt zip, no manifest) return a clean readable error.

**Verify:** curl/HTTP the endpoint with a sample package; check counts match the source course.

**Rollback:** delete new files; endpoint is flag-gated so no user impact.

---

### Session S2 — Ingest B: page bodies + QTI + resources → full ICourse
**Goal:** Complete `canvas_parser` so `ICourse` carries real content (markdown pages + normalized questions + staged resources).

**Steps**
1. `importers/html_to_markdown.py`: `page_html_to_markdown(html) -> (title, markdown, raw_body_html)`. Extract `<title>`; extract `<body>`; strip leading `<style>…</style>` (locked CSS); unwrap `<div class="cas-lesson">`; convert to markdown (`markdownify`). Return raw body HTML too (for `Block.content_html` fallback). Add `markdownify` to `requirements.txt`.
2. `importers/qti_xml_parser.py`: `parse_qti_xml(xml_str) -> list[question]`. Read items/metadata/choices/correct/feedback (elements listed in Ground-truth §Exporter gap #2). **Output in the same normalized markdown shape** `qti_parser.parse_assessment_questions` produces, so downstream rendering/round-trip match. Cross-check by feeding the result of an exporter-produced QTI back and diffing.
3. `canvas_parser` (part 2): for each item, load `wiki_content/*.html` → `html_to_markdown`; load `assessment/*.xml` → `qti_xml_parser`; stage `web_resources/` + embedded docs (via `file_parser.parse_uploaded_file`). Populate full `ICourse`. Per-item try/except → append warning, continue (never hard-fail on one item).
4. Extend tests: full parse of the round-trip fixture reconstructs the same page text + question set (content fidelity ≥ 90% target).

**Exit criteria:** Full `ICourse` built from a real IMSCC with pages as markdown, quizzes as normalized questions, resources staged; malformed single items degrade to warnings.

**Verify:** parse the fixture, dump `ICourse` to JSON, eyeball a page's markdown and a quiz's questions vs original.

**Rollback:** delete new files; `requirements.txt` revert.

---

### Session S3 — Reconstruct: editor_builder + provenance + import job (stages 1–4)
**Goal:** An imported IMSCC becomes a real CAS course whose Editor is fully populated and editable. **This is the core of Goal B.**

**Steps**
1. `importers/provenance.py`: create/read `import_provenance` rows (Canvas identifier ↔ CAS entity). Repo-style, append/read only.
2. `importers/editor_builder.py`: `build(db, ICourse, course_id, project_id, user) -> BuildResult`. Mirror `generation_jobs.py:458-493`:
   - One `CourseModule(course_id, title, position)` per `IModule`.
   - Per page: `Generation(block_type="lesson", topic=title, output_text=markdown, prompt_name/version="import", project_id, course_id, created_by)` + `Block(generation_id, block_type="lesson", block_label, content=markdown, content_html=raw_body_html, module_id, position, workflow_state="draft")`.
   - Per assessment: `Block(block_type="quiz"/"assignment", content=<normalized questions markdown>, module_id, ...)`.
   - Set all manual ids explicitly (no FK cascade). Write provenance rows per item/module.
   - Commit in batches like the reference loop.
3. `promptops_app/jobs/import_jobs.py`: `run_import_job(job_id)` — own `SessionLocal()`; read payload via `job.request_json` alias; stages with `set_running(db, job, pct, label)`: **Extract → Parse (n/m) → Reconstruct Editor**; on success set `result_entity_id=course_id`, `courses.source_type="imscc"`, `course_imports.status`. Per-item retry once → warning → skip. Fatal (corrupt/no manifest) → `set_failed`. **Does not call `run_generation_job`.**
4. `POST /projects/{projectId}/imports` in `imports.py`: create Course shell (reusing the same construction as scratch create, or call the course-create path) + `course_imports` row → `job_repository.create_job(db, ..., request_params={import_id, course_id, options}, job_type="import", project_id, course_id)` → `job_runner.submit(import_jobs.run_import_job, job_id)` → return `{course_id, import_id, job_id}`.
5. `GET /imports/{importId}` returns the record + latest status/warnings.
6. Tests: end-to-end job on the fixture creates modules/blocks with correct order/grouping; blocks are `draft` and editable.

**Exit criteria:** Imported course opens in Editor with correct modules/pages/quizzes, correctly ordered; blocks editable; Workflow (Draft→…→Published), autosave, version history, per-block regenerate all work with **zero** editor changes.

**Verify:** run the import job on a fixture; open `/workspace/{courseId}/editor` in the app; edit a block, move it through workflow, regenerate one block.

**Rollback:** flag-gated; delete new files. Imported courses are normal rows (deletable via existing course delete).

---

### Session S4 — Reconstruct FE: import wizard
**Goal:** User-facing upload → progress → open workspace. Enable the modal's **Import** option.

**Steps**
1. `services/endpoints.js`: add `IMPORTS` group (`VALIDATE`, `CREATE(projectId)`, `GET(importId)`, `RETRY(importId)`, `CANCEL(importId)`) + reuse `JOBS.GET`.
2. `features/import/services/importService.js`: `validatePackage(file)`, `startImport(projectId, body)`, `getImport(importId)`, `pollJob(jobId)`.
3. `features/import/importSlice.js` + `importThunks.js`: `validatePackageThunk`, `startImportThunk`, `pollImportJobThunk` (polls existing `GET /jobs/{jobId}`).
4. `features/import/pages/ImportWizardPage/`: steps Upload & validate (show counts + warnings) → Start Import → live progress (render `current_step`/`progress`) → on complete navigate to `/workspace/{courseId}/editor`.
5. `app/routes.jsx`: add `/projects/:projectId/import` → `ImportWizardPage` (pre-course). No workspace route changes.
6. Enable **Import Course** in `CreateCourseModal` (behind flag) → routes to wizard.

**Exit criteria:** From Create Course → Import → upload IMSCC → watch stages → land in a populated, editable Editor. Flag off = option hidden.

**Verify:** full manual run in the browser with a real IMSCC.

**Rollback:** flag-gated; revert the modal enablement + route.

---

### Session S5 — Reverse-gen A: Blueprint + CDD
**Goal:** Auto-populate Blueprint and CDD tabs from imported content, editable + regenerable.

**Steps**
1. Prompts: add `prompts/templates/reverse_blueprint.*` and `reverse_cdd.*` (new, read-only additions; never touch scratch prompts). Register via existing prompt loader pattern.
2. `importers/reverse_blueprint.py`: per `CourseModule`, summarize reconstructed blocks → `llm_service` with reverse_blueprint prompt → parse via existing `blueprint_parser` into the **same sections shape** the forward blueprint uses → `blueprint_repository.create_version(..., is_active=True)` → set `courses.active_blueprint_id`.
3. `importers/reverse_cdd.py`: aggregate module blueprints + course metadata → reverse_cdd prompt → `cdd_parser` → `cdd_repository.create_version(..., is_active=True)` → set `courses.active_cdd_id`.
4. `import_jobs`: add **stage 5 Blueprint → stage 6 CDD** (ordering: content → module structure → course design). Must run only **after** reconstruction fully succeeds; failure here leaves Editor usable and is retryable.
5. Tests: tabs populated with correct shape; regenerate via existing endpoints works.

**Exit criteria:** Blueprint + CDD tabs auto-populated, individually editable and regenerable via existing controls; no auto-cascade between artifacts (v1 conservative).

**Verify:** open Blueprint & CDD tabs on an imported course; edit and regenerate each.

**Rollback:** flag-gated; reverse-gen is independent of reconstruction.

---

### Session S6 — Reverse-gen B: Style + retry
**Goal:** Detect and pin a reusable Style; add reverse-gen retry.

**Steps**
1. Prompt: `prompts/templates/style_analysis.*` (new).
2. `importers/style_analyzer.py`: sample N reconstructed lessons → style_analysis prompt → `style_service` create Style/StyleVersion → set `courses.active_style_id`.
3. `import_jobs`: add **stage 7 Style (optional)** → Finalize (persist provenance map, set `provenance_ready=True`, `course_imports.status="completed"`).
4. `POST /imports/{importId}/retry`: re-run reverse-gen stages only (idempotent; does **not** rebuild blocks). `POST /imports/{importId}/cancel`: reuse job cancel.
5. Tests: Style tab populated + reusable; retry re-runs only reverse-gen.

**Exit criteria:** Style tab populated/editable/reusable; retry + cancel work.

**Verify:** import a course; confirm Style pinned; hit retry and confirm blocks unchanged, artifacts regenerated.

**Rollback:** flag-gated.

---

### Session S7 — Round-trip + polish
**Goal:** High-fidelity export and finishing touches.

**Steps**
1. **Provenance-aware export (optional read):** enhance the existing course export so that *when* `import_provenance` exists, the exporter reuses saved Canvas identifiers for higher-fidelity round-trip. Implement as an **optional read** that defaults to today's behavior if absent — **no change to the export contract**.
2. **Analytics badge:** surface `courses.source_type` as a display-only badge/filter. **Never** gate logic on it.
3. **Warning surfacing:** show `course_imports.warnings_json` in the workspace (low-confidence items flagged for review).
4. **E2E round-trip test:** import a Canvas IMSCC → edit → export IMSCC → re-import; assert ≥95% structure fidelity, ≥90% content fidelity.
5. Optional: "N sections may be stale" hint from a simple `updated_at` comparison (display only; no auto-cascade in v1).

**Exit criteria:** Export→re-import meets fidelity targets; Analytics counts imported courses; warnings visible. Acceptance criteria (both Goals) fully met.

**Verify:** full round-trip in the app; run characterization tests for the scratch path to confirm zero regression.

**Rollback:** provenance export is an optional read (safe); badge/warnings are display-only.

---

## 5 · Isolation guardrails checklist (apply to every PR)
- [ ] No `if source_type == "imscc"` branch in generation/editor/CDD/blueprint/style/workflow/analytics code.
- [ ] No existing column altered/dropped; migration is ADD/CREATE only + reversible.
- [ ] No existing endpoint contract changed; no scratch prompt edited.
- [ ] Importer reuses shared functions **exactly** as scratch does (same args/side effects); new behavior = new function.
- [ ] Reverse-gen never calls `run_generation_job`.
- [ ] Whole feature gated by `IMPORT_COURSES_ENABLED` (default false).
- [ ] With flag off, app behavior is identical to today (except the create-course modal, whose "New Course" calls the identical endpoint).
- [ ] Import code confined to `importers/`, `jobs/import_jobs.py`, `routers/imports.py`, `schemas/import_.py`, `features/import/`, new prompt templates.

## 6 · Risks & mitigations (carry-over from tech doc)
| Risk | Mitigation |
|---|---|
| Malicious/oversized packages | Zip-bomb + path-traversal guards, size cap, isolated temp, tenant-scoped (S1). |
| Imperfect HTML→markdown fidelity | Keep original HTML on `Block.content_html`; provenance enables faithful re-export (S2/S7). |
| No QTI-XML reader exists | Net-new `qti_xml_parser` emitting the normalized shape; round-trip diff test (S2). |
| Reverse-gen LLM variance | Reconstruction independent of reverse-gen; reverse-gen retryable; structure is the hard target, quality soft (S5/S6). |
| Accidental coupling | This checklist + feature flag + confined packages (every session). |
| Large courses blocking a worker | Background job + progress + per-item retry; thread pool today, Celery-ready signature (S3). |

## 7 · Out of scope (v1)
SCORM/Moodle/Brightspace import; automatic bidirectional artifact sync; AI modernization suggestions; standards mapping; bulk/batch migration; automated quality scoring. (All are Phase-2+ enabled by the pluggable parser + provenance map.)

---

## 8 · Session Progress Log (update at end of each session)

> Legend: `[ ]` not started · `[~]` in progress · `[x]` done

- [x] **S0 · Scaffolding** — migration (source_type, import_id, course_imports, import_provenance), flag, empty router+health, importers pkg, FE modal (Import disabled).
  - Notes (done 2026-07-18): Landed migration `000100000009` (additive+reversible); `Course.source_type/import_id` + `CourseImport`/`ImportProvenance` models; `import_courses_enabled` flag (`IMPORT_COURSES_ENABLED`, default false) + `.env.example`; `promptops_app/importers/` pkg; `app/schemas/import_.py` (`ImportHealthResponse`); `app/api/v1/routers/imports.py` (`GET /imports/health`) **conditionally mounted** in `router.py` only when flag on; FE `CreateCourseModal` (New-vs-Import fork, Import disabled) wired via new `onRequestCreateCourse` prop on `SelectionSidebar` (course variant only) + rendered in `CoursesPage`.
  - Verified here: `py_compile` all backend files; models import + new-table/column DDL on sqlite; changeset is 6 additive edits + new files only (no scratch logic touched). Split the sidebar course-create block so the inline form is preserved verbatim as the `!onRequestCreateCourse` branch.
  - **PENDING manual verification (needs project venv / node_modules — this shell lacks alembic/httpx/node_modules):**
    1. `alembic upgrade head` then `alembic downgrade -1` (confirm reversible), then `alembic upgrade head` again.
    2. `cd frontend && npm install && npm run lint && npm run build` (confirm FE compiles).
    3. Start API; with `IMPORT_COURSES_ENABLED=false` confirm `/api/v1/imports/health` is 404 and New Course works unchanged; set flag true, confirm health returns `{enabled:true}`.
    4. In UI: click Create Course → fork modal appears; "New Course" creates a course exactly as before; "Import Course" is disabled ("Coming soon").
- [x] **S1 · Ingest A** — internal_model, package_extractor, manifest+module_meta parse, /imports/validate + tests.
  - Notes (done 2026-07-18): `importers/internal_model.py` (ICourse/IModule/IPage/IAssessment/IResource + `structure_counts()`); `importers/package_extractor.py` (safe unzip — path-traversal / zip-bomb / entry-count / size caps, locates manifest, `PackageValidationError`); `importers/canvas_parser.py` (`parse_structure` — prefers module_meta.xml by `<position>`, falls back to manifest organizations; maps content_type→kind; per-item warnings for unsupported LTI + missing resources; collects `web_resources/` assets); `importers/imscc_importer.py` (`validate_package` orchestrator, temp-dir, no DB writes); schemas `StructureCounts` + `ImportValidateResponse`; `POST /imports/validate` (multipart, `course.create` perm, maps fatal errors → 422). Tests under `tests/importers/` (fixtures + extractor + parser + endpoint).
  - Verified here: `py_compile` all files; **standalone run of parser+extractor = 14/14 checks PASS** (counts, module/item order by position, kinds, provenance ids, LTI+missing warnings, module_meta fallback, and all 3 extractor guards). Only new files added + the two S0 import-only files extended; zero scratch files touched beyond S0.
  - **PENDING manual verification (needs venv — this shell lacks pytest/markdown/httpx):** `pytest tests/importers -q` (unit + endpoint). Also: get a **real Canvas IMSCC** export and hit `POST /api/v1/imports/validate` (flag on) to confirm counts on real-world data (fixtures are synthetic + a CAS self-export round-trip is added in S2).
- [x] **S2 · Ingest B** — html_to_markdown, qti_xml_parser, full ICourse (pages+quizzes+resources) + tests.
  - Notes (done 2026-07-18): `importers/html_to_markdown.py` (`page_html_to_markdown(html)->(title, markdown, raw_body_html)`: extract `<title>`, extract `<body>`, strip `<style>`/`<script>`, balanced-unwrap `.cas-lesson`, convert via **markdownify** with a stdlib **regex fallback** when the dep is absent — module stays importable/testable without it). `importers/qti_xml_parser.py` (**net-new** QTI-1.2-XML reader, namespace-agnostic like canvas_parser: reads item ident/title, `question_type` metadata, presentation stem, `response_label` choices, `varequal` correct, `itemfeedback`; `questions_to_markdown` re-emits the **exact normalised shape** `qti_parser.parse_assessment_questions` round-trips — `### Question N` / `**Stem:**` / `**Answer Options:**` A) B)… / `**Correct Answer:** <LETTER>` / `**Explanation:**`; correct choice mapped ident→positional letter so real-Canvas numeric idents still round-trip). `canvas_parser.parse_course(extract)` = structure pass (S1, untouched) + new `_load_bodies` pass (per-item try/except → warning, never hard-fails; pages→markdown+raw_body_html, quizzes→questions+body_markdown, assignments/discussions→prose markdown). `imscc_importer.parse_package(data)` = full-parse orchestrator (temp dir, no DB writes) alongside the unchanged `validate_package`. `markdownify>=0.11` added to requirements.txt. Tests: `test_html_to_markdown.py`, `test_qti_xml_parser.py` (incl. exporter→parse→markdown→forward-parser round-trip), `test_full_parse.py` (+ missing-file-degrades-to-warning). Fixtures extended (`CAS_WIKI_PAGE`, `sample_qti_xml()` built via forward exporter, `build_content_imscc()`).
  - Verified here: `py_compile` all files; **standalone run = 28/28 checks PASS** (title/CSS-strip/unwrap/fragment fidelity via the regex fallback, QTI parse of all 3 question types, full round-trip through the forward `qti_parser`, full `parse_package` of a content package with pages+quiz, structure preserved by the body pass). Only new files + additive edits to `canvas_parser.py`/`imscc_importer.py`/`requirements.txt`/`fixtures.py`; `parse_structure` and the S1 validate path untouched; zero scratch files touched.
  - Known limits (acceptable v1): forward `qti_parser._OPTION_LINE` only matches A–D, so quizzes with >4 options don't fully round-trip (pre-existing forward-parser limit, not introduced here); `IResource.path` from `parse_package` points inside a temp dir deleted on return (fine for validate/parse — S3's job owns a persistent workdir for staging). embedded-doc text extraction via `file_parser` deferred (not consumed until reconstruction; resources already staged as filename+path).
  - **PENDING manual verification (needs venv — this shell lacks pytest/markdownify):** `pip install markdownify` then `pytest tests/importers -q` (confirms the real markdownify path, not just the fallback). Also parse a **real Canvas IMSCC** export via `parse_package` and eyeball one page's markdown + one quiz's questions vs the source.
- [x] **S3 · Reconstruct** — provenance, editor_builder, import_jobs stages 1–4, POST /projects/{id}/imports, GET /imports/{id}.
  - Notes (done 2026-07-18): `importers/provenance.py` (append/read CRUD for `import_provenance`; `record()` + `list_for_import()`; canvas_type page|quiz|module, cas_entity_type block|module). `importers/editor_builder.py` `build(db, ICourse, *, course_id, project_id, import_id, user_name, progress_cb)` → mirrors `generation_jobs.py:458-493`: **one `CourseModule` per IModule** (position preserved) + **one `Generation` per module** (`prompt_name/version="import"`, output_text=aggregated item markdown) + **one `Block` per item** (`workflow_state="draft"`, `module_id` set, `position` within module, pages→`content`=markdown & `content_html`=raw body HTML, quizzes→`content`=normalised questions markdown). **Design decision:** grouped Generation *per module* (not per page) because the editor reaches blocks *through* `generation_repository.list_course_generations` (filters `Generation.course_id`, LIMIT 200) — per-module keeps the generation count tiny while still matching scratch's one-generation-many-blocks shape and giving every block a valid `generation_id`. Per-item persist with **one retry** (flush→provenance→commit; rollback+retry+warning on failure) so a bad item degrades to a warning; module-level failures also caught. `jobs/import_jobs.py` `run_import_job(job_id)` — own `SessionLocal`, JSON payload via `job.request_json`, stages **Extract(10)→Parse(30)→Reconstruct(55→90, per-module progress)→Finalize(95)**; on success sets `courses.source_type="imscc"` + `courses.import_id`, `course_imports.status="completed"` + structure_counts + warnings, and `set_completed(result_entity_id=course_id)`; fatal→`set_failed` + `course_imports.status="failed"`; **does NOT call `run_generation_job`**; deletes the staged temp package in `finally`. Endpoints in `routers/imports.py`: **`POST /projects/{projectId}/imports`** (new `project_router`, mounted no-prefix like courses; multipart file+`name`+`cluster_id`; creates Course shell w/ `source_type="imscc"` + `course_imports` row, stages package to a temp file via `mkstemp`, `job_repository.create_job(job_type="import")` → `job_runner.submit(run_import_job)`, returns `{course_id, import_id, job_id}`) and **`GET /imports/{importId}`** (returns `course_imports` record + parsed counts/warnings). Schemas `ImportStartResponse` + `ImportRecordResponse`. `router.py` mounts both `imports_router`(prefix `/imports`) and `imports_project_router`(no prefix) **inside the same `if settings.import_courses_enabled` block** — flag-off surface unchanged.
  - Verified here: `py_compile` all files; router import smoke shows exactly `['/health','/validate','/{import_id}']` (+ `/projects/{project_id}/imports`); **standalone end-to-end run on in-memory SQLite = 30/30 PASS** — `editor_builder.build` (2 modules, 3 blocks, 1 gen/module, draft state, module_id+position order, page content_html fidelity, quiz normalised md, provenance rows mapping Canvas ids→real block/module ids) **and** full `run_import_job` (job completed, `result_entity_id=course_id`, progress 100, `source_type=imscc`, `import_id` pinned, `course_imports.status=completed` + structure_counts stored, staged temp package cleaned up). Only new files + additive edits to `router.py`/`imports.py`/`import_.py`; **zero scratch files touched**; no `if source_type==` branch anywhere; reverse-gen not called.
  - **PENDING manual verification (needs venv — this shell lacks pytest/markdownify/fastapi TestClient):** `pytest tests/importers -q` (incl. new `test_editor_builder.py` with the monkeypatched-SessionLocal job test). Then with `IMPORT_COURSES_ENABLED=true` + a running API: `POST /api/v1/projects/{pid}/imports` (multipart file+name) → poll `GET /api/v1/jobs/{jobId}` to completion → open `/workspace/{courseId}/editor` and confirm modules/pages/quizzes appear in order, blocks are editable, and Workflow/autosave/regenerate work unchanged. Confirm flag-off = endpoints 404.
  - Notes for S4 (FE): start response is `{course_id, import_id, job_id}`; wizard polls existing `GET /jobs/{jobId}` for progress (stages Extracting/Parsing/Reconstructing/Finalizing), then navigates to `/workspace/{course_id}/editor`. `GET /imports/{importId}` surfaces counts+warnings for the review screen. **Known follow-up:** `IResource.path` from `parse_package` points into a temp dir deleted on job return — fine for S3 (blocks are text; resources not yet copied), but S7 provenance-aware export / any media staging will need the job to own a persistent workdir.
- [x] **S4 · Reconstruct FE** — endpoints, importService, slice/thunks, ImportWizardPage, route, enable modal Import.
  - Notes (done 2026-07-18): `services/endpoints.js` new **`IMPORTS`** group (`HEALTH`, `VALIDATE`, `CREATE(projectId)`, `GET(importId)`, `RETRY`/`CANCEL` — last two land in S6; progress reuses `GENERATE.JOB_STATUS`). New feature `features/import/`: `services/importService.js` (validate/start via `api.upload` FormData, getImport, health, getJobStatus — all through the shared api client, no hand-rolled fetch); `importThunks.js` (`validatePackageThunk`, `startImportThunk` → auto-dispatches poll, `pollImportJobThunk` recursive `setTimeout` mirroring the generate flow, reuses `JOB_STATUSES` + `VITE_JOB_POLL_INTERVAL_MS`); `importSlice.js` (step `upload→progress→done|error`, validate/start/poll lifecycle, `resetImport`, `selectImport`). `pages/ImportWizardPage/` (`.jsx` + `.module.scss` SCSS-module w/ shared tokens): Upload+validate (FileUpload `.imscc,.zip` → auto-validate → counts grid + collapsible warnings + prefilled course-name Input) → Start → live progress bar (`job.current_step`/`job.progress`) → done auto-navigates to `ROUTES.EDITOR(courseId)` after 900ms (+ manual button); error state offers Retry/Back. Store: registered `import` reducer + added `meta.arg.file` to `serializableCheck.ignoredActionPaths` (thunks carry the raw File). Route `/projects/:projectId/import` → `ImportWizardPage` (pre-course, in ProtectedRoute group; cluster passed via `?cluster_id=`); `ROUTES.IMPORT(projectId)` added. `CoursesPage`: probes `importService.health()` on mount (404 when flag off → disabled) → passes `importEnabled` + `onImport={() => navigate(IMPORT(pid)+'?cluster_id='+cid)}` to the existing `CreateCourseModal` (S0 modal already had the disabled Import fork + `onImport` prop). WorkspaceLayout self-bootstraps the course from the `:courseId` URL param, so the wizard→editor hand-off needs no pre-seeded Redux state.
  - Verified here: **`node --check` passes** on all touched plain-JS (importSlice/importThunks/importService/endpoints/store/constants); every `@`-alias target confirmed to exist (FileUpload/Loader/Button/Input/@app/hooks); `Loader` accepts `size`; jobs `JobStatusResponse` exposes exactly `status`/`progress`/`current_step`/`error_message` (the fields the slice + progress UI bind to); wizard poll active-set (`pending|queued|running`) + terminal (`completed`/`failed`) match `job_status.py`. SCSS tokens all resolve (swapped the one missing `$color-warning-dark`→`$color-warning`). Additive-only: 1 new feature folder + edits to `endpoints.js`/`store.js`/`constants.js`/`routes.jsx`/`CoursesPage.jsx`; scratch create path untouched (New Course still calls the identical `handleCreate`).
  - **PENDING manual verification (this shell has NO `frontend/node_modules`):** `cd frontend && npm install && npm run lint && npm run build` (confirm JSX compiles — I could only `node --check` the plain JS, not transpile the two `.jsx` files). Then E2E with `IMPORT_COURSES_ENABLED=true` + running API: Courses → Create Course → **Import Course** (now enabled) → upload a real Canvas IMSCC → see counts+warnings → Start → watch stages (Extracting/Parsing/Reconstructing/Finalizing) → land in a populated, editable Editor. Confirm flag **off** = Import option shows "Coming soon" (health 404 → disabled) and the wizard route (if hit directly) just fails the API calls gracefully.
  - **Value milestone reached:** after S4 an imported IMSCC opens fully in the Editor and is editable (Goal B). S5–S6 add the AI artifacts (Blueprint/CDD/Style); S7 closes the round trip.
- [x] **S5 · Reverse-gen A** — reverse_blueprint + reverse_cdd + prompts, stages 5–6, pin actives.
  - Notes (done 2026-07-18): New file-tier prompts `prompts/templates/reverse_blueprint.md` + `reverse_cdd.md` (reverse-instructional-design: reconstruct the design spec *from* imported content; both emit the **exact strict OUTPUT FORMAT** the forward `cdd_generation.md`/`blueprint_generation.md` use so the shared parsers just work). Registered in `prompt_loader._REGISTRY` (additive keys only; **not** in `_STEM_COMPONENT`, so they resolve straight from the file tier and the scratch prompts are never touched). `importers/reverse_common.py` (`collect_course_modules(db, course_id)` reads the *reconstructed* CourseModule+Block rows ordered by position; `render_module_content`/`render_course_structure` with char caps; `DEFAULT_IMPORT_MODEL="GPT-5.4"`). `importers/reverse_blueprint.py` `build_blueprints(...)` — per module: `build_prompt("reverse_blueprint", db=…)` → `llm_service.generate_with_metadata` → `cdd_parser.parse_sections_from_text` → `ModuleBlueprint(cdd_id=None,…)` + `blueprint_repository.create_blueprint_version(is_active)` → pins `courses.active_blueprint_id` to the **first** module's bp; per-module try/except → warning. `importers/reverse_cdd.py` `build_cdd(...)` — aggregate outline → `reverse_cdd` prompt → `parse_sections_from_text` **merged with non-underscore `parse_cdd_flat` blocks** (mirrors forward exactly) → `CourseDesignDocument` + `cdd_repository.create_cdd_version(is_active)` → `set_active_cdd`; whole thing non-fatal (returns warnings). Reuses forward prompt-builder/llm_service/parser/repository **exactly**; adds no branch, never calls `run_generation_job`. **Isolation checks:** uses `result.status == "error"` (NOT `.is_error`) to stay compatible with the `mock_llm` MagicMock. `jobs/import_jobs.py`: reprogressed stages (Extract10/Parse30/Reconstruct50→78/**Blueprint80**/**CDD90**/Finalize96) + new `_reverse_generate()` helper — runs **only after reconstruction succeeds**, blueprints→CDD (reverse order), then backfills `blueprint.cdd_id = new cdd.id` (FK sanity for the blueprint-regenerate path); the **entire** reverse-gen block is wrapped so any failure → warning on `course_imports.warnings_json`, **never fails the import** (Editor + blocks stay usable, retryable).
  - Verified here: `py_compile` all files; **standalone S5 run = 20/20 PASS** (direct `build_blueprints`: 2 bps w/ module_numbers [1,2] + active versions + parsed sections + `active_blueprint_id` pinned to first; direct `build_cdd`: cdd + active version + flat-blocks-merged sections + `active_cdd_id` pinned; full `run_import_job` w/ mocked LLM: job completed, both actives pinned, 2 bps linked to the 1 cdd, cdd created). **S3 standalone re-run = 30/30 still PASS** with reverse-gen appended (LLM mocked). pytest `test_reverse_gen.py` added (happy path for bp+cdd + a non-fatal-on-LLM-error test) and `test_editor_builder.py` job test updated to use `mock_llm` + assert bp/cdd populated. Additive-only: new files + additive edits to `import_jobs.py`/`prompt_loader._REGISTRY`; zero scratch prompt/pipeline files modified.
  - Design decisions: (1) **grouped nothing new** — blueprints are one `ModuleBlueprint` per module (like forward), CDD is one doc; (2) `active_blueprint_id` pins module 1's bp (a course has many module bps but one course-level active — matches forward semantics); (3) reverse order blueprint→CDD per the plan, so `blueprint.cdd_id` starts NULL and is backfilled once the CDD row exists; (4) model = `course.config_model_choice or "GPT-5.4"`; (5) `usage_ctx` omitted (None) — reverse-gen cost logging deferred, non-critical.
  - **PENDING manual verification (needs venv w/ real LLM creds):** `pytest tests/importers -q`. Then a live import (flag on) and open the **Blueprint** + **CDD** tabs on the imported course — confirm they're populated in the forward shape, individually **editable** and **regenerable** via the existing controls, and that a mid-import LLM failure still lands a usable Editor (warnings surfaced). No auto-cascade between artifacts (v1 conservative — editing the CDD does not rewrite blueprints).
- [x] **S6 · Reverse-gen B** — style_analyzer + prompt, stage 7 + finalize, retry/cancel endpoints.
  - Notes (done 2026-07-18): New file-tier prompt `prompts/templates/style_analysis.md` (reverse **style detection** — infers the implicit style from sampled finished lessons, emits the **exact 4-section format** `style_understanding.md` uses: WHAT THIS IS / WHAT I LEARNED / HOW I WILL WORK / WHAT I WILL NOT DO) + registered in `_REGISTRY` (additive; not in `_STEM_COMPONENT`). `importers/style_analyzer.py` `build_style(...)` — samples ≤4 lesson blocks (≤2000 chars each) → `build_prompt("style_analysis")` → `llm_service.generate_with_metadata` → `database.create_style(docs=[], custom="")` + `style_repository.create_style_version(is_active)` (syncs `style.generated_summary`) → `database.set_active_style(scope="course", course_id=…)`; non-fatal (warnings, no raise). Style is inferred from lessons (no reference docs), so it does NOT reuse forward `generate_style_understanding` (that reads `style_documents`) — it reuses `create_style`/`create_style_version`/`set_active_style` exactly instead. `jobs/import_jobs.py`: added **stage 7 Style** into `_reverse_generate` (now Blueprint78/CDD86/**Style92**), which is parametrized by a `stages` tuple so the retry job reuses it; `_finalize` now also sets `course_imports.provenance_ready=True` (provenance map was written in S3). New **`run_reverse_gen_job(job_id)`** — reverse-gen-only entry point (Blueprint/CDD/Style + re-pin actives) that **never touches reconstructed blocks**; uses `editor_builder.BuildResult()` as its warnings accumulator. Endpoints in `routers/imports.py`: **`POST /imports/{importId}/retry`** (202; creates `GenerationJob(job_type="import_reverse")` → `job_runner.submit(run_reverse_gen_job)` → returns `{course_id, import_id, job_id}`) and **`POST /imports/{importId}/cancel`** (finds the import's latest ACTIVE job by course_id + job_type in [import, import_reverse] → `set_cancelled`; returns `MessageResponse`). FE `importService.js` gained `retryImport`/`cancelImport` (endpoints already existed from S4).
  - Verified here: `py_compile` all files; router smoke = `/health,/validate,/{id},/{id}/cancel,/{id}/retry` (+ project create); **standalone S6 run = 16/16 PASS** (direct `build_style`: Style + active StyleVersion + generated_summary synced + `active_style_id` pinned; full `run_import_job`: stage-7 Style created & pinned + `provenance_ready=True`; **retry via `run_reverse_gen_job`: block ids IDENTICAL before/after (3 blocks untouched), blueprints regenerated, all three actives still pinned, package cleaned**). **S5 20/20 + S3 30/30 re-run still PASS** (stage 7 added, LLM mocked). pytest `test_style_and_retry.py` added. Additive-only; zero scratch prompt/pipeline/style files modified; no `if source_type==` branch; reverse-gen never calls `run_generation_job`.
  - Design decisions: (1) retry is **regenerate semantics** — it creates fresh Blueprint/CDD/Style parents each run and re-pins the newest active (old ones deactivated, not deleted); the reconstructed blocks are guaranteed untouched (verified by identical block ids). This is not row-count-idempotent but is safe + matches forward "regenerate". (2) cancel is best-effort (marks the job cancelled; the running thread isn't cooperatively interrupted — same limitation as the forward generate flow). (3) The shared `DELETE /jobs/{jobId}` also cancels an import job; the dedicated `/cancel` is provided per the plan and for the wizard's convenience.
  - **PENDING manual verification (needs venv w/ real LLM creds):** `pytest tests/importers -q`. Live: import a course (flag on) → open the **Style** tab, confirm it's populated + reusable on other courses; hit **Retry** and confirm blocks are unchanged while Blueprint/CDD/Style are regenerated; hit **Cancel** on a running import and confirm the job stops (best-effort).
- [x] **S7 · Round-trip + polish** — provenance-aware export (optional read), analytics badge, warnings UI, E2E round-trip + scratch regression tests.
  - Notes (done 2026-07-18): **Provenance-aware export (optional read):** `build_imscc(..., item_ids=None)` — new **optional** param parallel to blocks; a non-empty entry is used as that block's manifest **item identifier** (instead of a fresh uuid), so a re-import maps back to the same Canvas items; `None`/empty ⇒ byte-identical to today (export contract unchanged). Threaded additively through `ExportRequest.block_item_ids` → `_build_imscc`. Export endpoint (`blocks.py`) loads them via new `provenance.block_identifier_map(db, import_id)` in a `_import_item_ids()` helper — **gated on `course.import_id` existence, NOT `source_type`** (respects the isolation checklist's "no source_type branch in export"), and only for `format=="imscc"`; absent provenance ⇒ `None` ⇒ fresh ids. **Analytics badge:** `source_type` (+ `import_id`) added to `CourseRead`/`CourseListItem` (display-only, `from_attributes` picks them up); `StreamlitCard` gained an optional `badge` prop; `CoursesPage` shows an **"Imported"** chip when `source_type==="imscc"`. **Warnings surfacing:** wizard **done** step now fetches `GET /imports/{importId}` (`fetchImportRecordThunk` + `record` slice field) and, when `warnings` exist, shows them in an open `<details>` and **suppresses the auto-navigate** (user reviews, then clicks "Open in Editor"); clean imports still auto-open. **E2E round-trip test:** import→export→re-import proves fidelity.
  - Verified here: `py_compile` all backend; `node --check` FE plain-JS; **standalone S7 = 12/12 PASS** — full round-trip on the content fixture: **structure fidelity 100%** (module/item counts, order, kinds, titles preserved), **prose content fidelity 100%** (the only export/import delta is a redundant in-body `# title` heading, which is preserved separately as the item label — measured with headings normalized out symmetrically), **quiz round-trips through QTI** (3 questions in = 3 out), **Canvas item ids preserved** via provenance-aware export, and **default export unchanged** (mints fresh ids, same structure). **Regression: S3 30/30 · S5 20/20 · S6 16/16 all still PASS.** pytest `test_roundtrip.py` (3 tests) + `test_block_identifier_map_backs_provenance_export` added. **Isolation audit clean:** grep confirms zero `source_type ==` branches in backend logic (only unrelated `resource_type` checks + the display-only FE badge), and `run_generation_job` is never called from importers/import_jobs (docstrings only).
  - Deferred from plan (documented, low-value/optional): the "N sections may be stale" `updated_at` hint (plan S7 step 5, explicitly optional). Warnings are surfaced in the wizard (transient, at hand-off) rather than as a persistent workspace banner — a persistent banner is a clean future add (course now exposes `import_id` + `source_type`, and `GET /imports/{id}` returns warnings).
  - **PENDING manual verification (needs venv / node_modules / real LLM):** `pytest tests/importers -q` (all sessions) + `pytest tests/characterization -q` (scratch-path regression). `cd frontend && npm install && npm run lint && npm run build`. Live E2E round-trip (flag on): import a **real Canvas IMSCC** → edit a block → export as IMSCC → re-import → confirm ≥95% structure / ≥90% content fidelity and that provenance-aware export keeps item identity; confirm the **"Imported" badge** on the course card, the **warnings** panel on a flagged import, and that **scratch New Course → Style→CDD→Blueprint→Generate→Editor→Export is byte-for-byte unchanged** (flag on and off).

### Acceptance status (both Goals)
- **Goal A (scratch untouched):** every change is additive + flag-gated (`IMPORT_COURSES_ENABLED`, default false); no scratch prompt/column/endpoint/contract modified; New-Course path calls the identical handler. ✅ (final manual regression pass pending in venv)
- **Goal B (import → editable course):** IMSCC → populated, editable Editor (S3/S4) + auto Blueprint/CDD/Style (S5/S6) + high-fidelity round-trip export (S7). ✅ (final manual/live pass pending)
- **All 8 sessions (S0–S7) implemented.** Remaining work is the human-in-the-loop verification checklist above (needs the project venv, node_modules, and LLM creds this shell lacks) before commit.

### Decisions / open questions
- Obtain a **real Canvas IMSCC** sample for S1 fixtures (in addition to the CAS-exported self round-trip). Where from? _(TBD)_
- Confirm `markdownify` is acceptable as a new dependency (vs `html2text`). _(default: markdownify)_
- Confirm tenant-scoping/RBAC expectations for import endpoints match `course.create`. _(default: yes)_
