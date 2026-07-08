# Content AI Studio — Context Flow & Architecture (Plain-English Guide)

> **What this document is:** A simple, complete walk-through of *how "context" moves
> through this project* — where it comes from, how it is shaped, how it is passed
> from stage to stage, and how it is kept under control. It also explains the
> overall architecture that carries this context, both theoretically (the *idea*)
> and practically (the *code*).

---

## 1. The Big Picture in One Paragraph

Content AI Studio is an **eLearning content factory**. A user picks a
Project → Course, uploads reference material, and then walks a course through a
**3-stage pipeline**: **CDD (Course Design Document) → Module Blueprint → Lesson
Generation**. The key idea is that **each stage produces "context" that the next
stage is forced to obey.** The CDD sets the course-wide rules, the Blueprint
refines them per module, and Generation writes the actual student-facing lessons
*while being fed everything decided upstream*. On top of that pipeline sits a
**Global Context Layer** (who the audience is + the writing Style) that is
injected into every stage, and a **Feedback/Learning loop** that remembers the
user's edits and folds them back into future generations.

So "context" here is not one thing — it is **several layers stacked together**
right before every call to the LLM.

---

## 2. The Layers of Context (Theory)

Think of the final prompt sent to the AI as a **sandwich**. Each layer is
assembled separately and then stacked:

| Layer | What it carries | Where it lives | Scope |
|-------|-----------------|----------------|-------|
| **1. Workspace/Session context** | Who is the user, which Project / Course / Cluster, which model | `PageContext` (backend), Redux state (frontend) | Per session |
| **2. Global Context Layer** | Target audience profile + active Style guide | `build_global_context()` | Per project/course |
| **3. Pipeline inheritance context** | CDD summary + Blueprint summary + inherited Learning Objectives, Tone, Key Concepts, Quality Standards | `build_context_injection()` + `CONTEXT_INJECTION_TEMPLATE` | Per course, flows downstream |
| **4. Source / RAG context** | Uploaded files, library docs, `` `backtick` `` references, CDD-linked source docs | `make_source_context()` | Per generation |
| **5. Feedback / Learning context** | Past user edits marked "Use as Learning" | `FeedbackSignal` rows → "LEARNED PREFERENCES" | Per prompt type, accumulates |
| **6. Instruction context** | Persona prefix, citation rules, extra instructions typed at run time | `PERSONA_PREFIX_TEMPLATE`, code-injected strings | Per generation |

The magic is that all six are **concatenated into two strings** — a `system_p`
(system prompt) and a `user_p` (user prompt) — at the moment of generation.
Everything before that is just *gathering and shaping* these layers.

---

## 3. The Pipeline & How Context Flows Downstream

```
   ┌─────────────┐      ┌──────────────────┐      ┌──────────────────┐
   │   STAGE 1   │      │     STAGE 2      │      │     STAGE 3      │
   │     CDD     │ ───▶ │  Module Blueprint │ ───▶ │ Lesson Generation │
   │ (course-wide│      │ (per-module rules,│      │ (actual student   │
   │  rules)     │      │  refines the CDD) │      │  content)         │
   └─────────────┘      └──────────────────┘      └──────────────────┘
         │                      │                          │
         │  CDD summary         │  Blueprint summary       │  produces Blocks
         │  (LOs, tone,         │  (LOs, key concepts)     │  (## sections)
         ▼  key concepts) ──────┴──────────────────────────▶  fed as CONTEXT
                          "inherited constraints"           INJECTION
```

### How it actually works in code
- Each artifact (CDD, Blueprint) is stored **with versions** (`CDDVersion`,
  `BlueprintVersion`). Only the **active version** is used as context.
- When generating a lesson, `build_context_injection(db, cdd_id, blueprint_id, ...)`:
  1. Loads the **active** CDD version and **active** Blueprint version.
  2. Extracts structured fields from their saved `sections` JSON —
     `Learning Objectives`, `Tone & Style`, `Key Concepts`, `Quality Standards`.
  3. Produces short **summaries** (`extract_cdd_summary`, `extract_blueprint_summary`).
  4. Fills the `CONTEXT_INJECTION_TEMPLATE` — a big "DO NOT IGNORE THIS SECTION"
     block that becomes part of the lesson prompt.
- The Blueprint stage does the same thing one level up: when you regenerate a
  Blueprint section, it pulls a **CDD summary** so the Blueprint stays aligned
  to the course design.

**Key theoretical point:** context flows *strictly downward*. Upstream decisions
(CDD) constrain everything below. This is what keeps a 20-lesson course
internally consistent instead of 20 disconnected AI outputs.

---

## 4. The Global Context Layer (audience + style)

Independent of the pipeline, two things are injected into *every* stage:

1. **Target Audience Profile** — role, experience level, domain, audience
   category. Assembled into an `audience_block` telling the AI to *"calibrate
   depth, tone, complexity, and examples to a {level} {role} in {domain}."*
2. **Active Style** — a reusable writing-style definition (`Style` +
   `StyleDocument`), scoped to the current project/course. `build_style_context()`
   turns it into an `--- ACTIVE INSTRUCTIONAL STYLE ---` block that the AI *must*
   comply with.

Both are gathered by `build_global_context(db)` (legacy Streamlit path) or
resolved per-request in the FastAPI path, and prepended to the prompt as the
`User_prefix`.

---

## 5. Source / RAG Context (grounding the AI in real material)

Before generation, the worker builds a `context` string from up to four sources,
in priority order (see `generation_jobs.py`, Stage 1):

1. **Supplementary files** uploaded at run time (already parsed to text).
2. **Library documents** the user selected (`ctx_docs`).
3. **Backtick references** — `` `filename.pdf` `` mentioned inside the topic.
4. **CDD-linked source documents** — the original material the CDD was built
   from; **prepended** so it takes top priority.

Each source is wrapped with `make_source_context()`:
```
[START SOURCE: filename]
...content...
[END SOURCE: filename]
```
The AI is instructed to cite these as `[Source: filename]`, and the output
splitter later extracts those citations back out per block.

---

## 6. The Feedback / Learning Loop (lightweight memory)

This is the project's "gets smarter over time" mechanism:

- Whenever a user **edits** or **regenerates** a CDD/Blueprint section, a
  `FeedbackSignal` row is written with the original text, final text, and a
  **reason**.
- Each signal has a **scope**:
  - **⚡ Apply Once (`one_time`)** — just this edit, not remembered.
  - **🧠 Use as Learning (`learning`)** — remembered and reused.
- On the next regeneration, the code queries the most recent `learning`-scoped
  signals for that prompt type and injects them as a **"LEARNED PREFERENCES"**
  block into the prompt. So the AI is nudged toward the user's proven preferences.

This is a **cheap, database-backed alternative to fine-tuning** — the "memory"
lives in `FeedbackSignal` rows and is re-injected as context, not baked into a model.

---

## 7. How the Final Prompt Is Assembled (the exact stack)

Inside `run_generation_job()` the prompt is built in layers (Stage 2):

```
system_p = User_prefix                 # persona + audience + active style
         + <template system prompt>     # from DB registry OR hard-coded constant
         + citation_instruction         # "cite sources as [Source: ...]"

user_p   = <template user prompt>       # includes CONTEXT_INJECTION (CDD+BP)
         + context                      # RAG source material
         + extra_instructions           # anything typed at launch time
```

The template itself is resolved through a **fallback ladder** (`_db_backed_prompt`):
1. **Scope-locked** prompt (project/course-specific override)
2. **Component default** (e.g. a quiz-specific prompt)
3. **Stem row** in the DB registry
4. **`.md` file** in `prompts/templates/`
5. **Hard-coded constant** (`LESSON_WITH_CONTEXT_SYSTEM`, etc.) as the last resort

> Note from the project's own `project_execution.md`: *"Generate is the one fully
> hard-coded stage"* — meaning the Generate stage most often falls through to the
> hard-coded constants unless an admin has authored registry prompts.

---

## 8. Context *Management* — Keeping It Under Control

Gathering context is easy; **managing** it is the hard part. This project does it
in five ways:

### a) Bounding / clipping (avoid blowing the token budget)
Config-driven caps in `core/config.py`:
- `MAX_SOURCE_CHARS = 6000` — per-document cap.
- `MAX_CONTEXT_CHARS = 30000` — global cap on all supplementary context.
- `clip_text()` trims with a visible `...[truncated]` marker.

### b) Versioning & provenance (traceability)
Every generation records **which CDD version and which Blueprint version** it used
(`cdd_version`, `blueprint_version` on the `Generation` row). You can always answer
*"what context produced this lesson?"*

### c) Scoping (the right context for the right place)
Styles, prompts, and CDDs are scoped by `project_id` / `course_id` / `cluster_id`.
The `PageContext` dataclass carries this scope so nothing leaks across courses.

### d) Thread / process isolation (safe async generation)
Generation is **asynchronous**. The UI creates a `GenerationJob`, serializes *all*
context into `request_json`, and hands the job to a **Celery worker**. The worker
**never** touches session state — it rebuilds everything from the DB and the job
payload. This is why context must be explicitly serialized, not shared in memory.
The client then **polls** `GET /api/v1/jobs/{job_id}` for progress. Stages are
tracked explicitly: `Context → Prompt → LLM → CE Validation → Split → Save`.

### e) Post-generation shaping
- **CE validation** cleans the output against the active style.
- **Block splitting** breaks the lesson at `## ` headings into editable `Block`
  rows, each with its extracted `[Source: ...]` citations, quality score, and an
  async plagiarism scan (Copyleaks).

---

## 9. The Architecture That Carries the Context

This repo is **mid-migration** and runs a **dual architecture**:

```
┌────────────────────────────────────────────────────────────────────┐
│  FRONTEND  (frontend/ — React + Vite + Redux Toolkit)              │
│  • Feature-sliced: features/{cdd,blueprint,generate,editor,...}    │
│  • Holds workspace context in Redux (selectedProject/Course)       │
│  • Sends context in API request bodies; polls jobs for status      │
└───────────────────────────────┬────────────────────────────────────┘
                                 │  HTTP (JSON), JWT auth
┌───────────────────────────────▼────────────────────────────────────┐
│  API LAYER  (app/ — FastAPI)  ← the NEW, thin layer                │
│  • Routers: cdd, blueprints, generations, styles, jobs, ...        │
│  • Validates (Pydantic schemas), checks permissions (RBAC)         │
│  • Delegates all real work down to promptops_app services/repos    │
└───────────────────────────────┬────────────────────────────────────┘
                                 │  in-process calls
┌───────────────────────────────▼────────────────────────────────────┐
│  DOMAIN LAYER  (promptops_app/ — the LEGACY monolith, decomposed)  │
│  • services/    → business logic (llm, style, evaluation, export)  │
│  • repositories/→ all DB access (SQLAlchemy)                       │
│  • parsers/     → CDD/Blueprint/file parsing                       │
│  • prompts/     → registry, builder, .md templates, fragments      │
│  • jobs/        → generation_jobs, plagiarism_jobs (Celery tasks)  │
│  • core/        → context.py, config.py, llm_client.py, shared.py  │
│  • pages/       → OLD Streamlit UI (being deleted)                 │
└───────────────────────────────┬────────────────────────────────────┘
          ┌──────────────────────┼───────────────────────┐
          ▼                      ▼                        ▼
     PostgreSQL              Redis + Celery          LLM providers
   (all state, versions,   (async job queue,        (OpenAI +
    feedback signals)       polling backend)          AWS Bedrock/Claude)
```

**Reading it top-to-bottom:**
- **React frontend** owns the *workspace context* (Project/Course selection) and
  sends it down with each request.
- **FastAPI (`app/`)** is a **thin, modern shell** — it authenticates, validates,
  and forwards. It contains almost no business logic; routers `import` from
  `promptops_app` and call services/repositories.
- **`promptops_app`** is the **real brain** — the former Streamlit monolith now
  split into `services / repositories / parsers / prompts / jobs / core`. This is
  where every context layer is built.
- **Postgres** stores all durable context (versions, feedback, styles, prompts).
- **Redis + Celery** run generation asynchronously and back the job-polling model.
- **LLM providers** are abstracted behind `call_llm()` / `generate_with_metadata()`
  with **retry + fallback** (primary model → fallback model) and a model catalog
  that maps display names (e.g. "GPT-5.4", "Sonnet 4.5") to real API IDs.

> **Legacy note:** `promptops_app/core/shared.py` still `import st` in places and
> has Streamlit UI renderers. Those are the *old* path being retired. The FastAPI
> generation flow (`app/.../generations.py` → `jobs/generation_jobs.py`) is the
> current, thread-safe one and **never** touches Streamlit.

---

## 10. End-to-End: One Lesson, Start to Finish

1. **User** selects Project → Course in the React app (workspace context set in Redux).
2. User configures audience + picks an active **Style** → *Global Context Layer*.
3. User authors/generates a **CDD**, then a **Blueprint** → *pipeline context* saved & versioned.
4. User clicks **Generate** → frontend calls `POST /generations/launch`.
5. FastAPI validates, checks a **completion gate** (all lessons done before an
   assessment?), creates a `GenerationJob`, and submits it to Celery. Returns a `job_id`.
6. **Celery worker** (`run_generation_job`) rebuilds context in stages:
   - **Context:** gather source/RAG material (clipped to caps).
   - **Prompt:** stack persona + style + CDD/Blueprint injection + citation rules +
     learned preferences + extra instructions → `system_p`, `user_p`.
   - **LLM:** call the model (with retry/fallback), log token usage & cost.
   - **CE Validation:** clean output against the style.
   - **Split:** break into `Block` rows, extract citations, score quality.
   - **Save:** persist `Generation` + `Block`s, tagged with the exact CDD/BP versions used.
7. Frontend **polls** the job, then loads the result in the **Editor**, where
   per-block edits/regenerations feed the **Feedback/Learning loop** back into step 6
   for next time.

---

## 11. One-Line Summary

> **Context in Content AI Studio = (Workspace scope) + (Global audience & style)
> + (Downstream-inherited CDD/Blueprint constraints) + (RAG source docs) +
> (Learned preferences) + (Run-time instructions)** — all gathered, bounded,
> versioned, serialized into a background job, stacked into two prompt strings,
> and sent to the LLM behind a retry/fallback layer.
