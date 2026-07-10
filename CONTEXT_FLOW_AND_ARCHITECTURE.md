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


## ###################################################################

 Approaches (simplest → biggest)

  Quick wins (config only, do today):
  - Raise the caps. Move max_context_chars from 30k → ~120k chars and max_source_chars from 6k → ~20k, CDD/BP summaries from
  3k → ~8k. These are env-driven (PROMPTOPS_MAX_CONTEXT_CHARS, etc.), so no code change — just .env. This alone fixes most
  current loss.
  - Log when truncation happens. Right now clipping is silent. Emit a warning + store on the Generation row "context
  truncated: dropped N chars / which docs." So you know when you lost something instead of guessing.

  Medium (small code changes):
  - Budget per-source instead of one tail-chop. Instead of concatenate-then-cut, divide the budget across docs (e.g. give
  each doc a fair share, or protect CDD source docs + summaries first, then fill remaining with uploads). Prevents the "last
  file disappears" problem as you add more uploads.
  - Smart clipping, not text[:limit]. Clip on paragraph/heading boundaries and optionally keep the head and tail of a doc,
  since conclusions/constraints often live at the end.

  Bigger (real retrieval — the right long-term fix for "many files"):
  - Add embedding-based retrieval. When you'll have lots of files in style/CDD/blueprint, don't send whole files — chunk
  them, embed once, and at generation time pull only the chunks relevant to this lesson's topic + learning objectives. This
  is how you keep quality high while cutting tokens, not just raising limits. This is the scalable answer to your "more
  uploaded files later" plan.
  - Token-based budgeting, not char-based. Count real tokens (tiktoken/model tokenizer) and pack context to a target token
  budget per model — precise cost control instead of the rough 4-chars-per-token guess.

  My recommendation
  Do it in two phases:
  1. Now: raise the caps via .env + add truncation logging. Near-zero risk, fixes today's silent loss immediately.
  2. Next: as file volume grows, add embedding retrieval so you send relevant chunks instead of whole-file dumps — that's
  what simultaneously improves quality and optimizes tokens.

---

## ################################################################### ##

# 12. Embedding-Based Retrieval + Token Budgeting — Detailed Plan

> **What this section is:** the "Bigger" approach spelled out step-by-step,
> mapped onto the **exact files and flow this project already has**. Written in
> plain language. No code here — just *what* to build, *where* it plugs in, and
> *why*.

## 12.1 The core idea in one picture

**Today:**
```
upload file → store whole text in documents.content
generate    → dump whole file into prompt → chop at 30k chars (blind tail-cut)
```

**With retrieval:**
```
upload file → store whole text  AND  split into chunks + embed each chunk (once)
generate    → build a "search query" from this lesson's topic + objectives
            → find the most relevant chunks → pack them to a token budget → prompt
```

The one-time work (chunk + embed) happens at **upload**. The smart selection
happens at **generation**. You stop sending whole files; you send only the
paragraphs that matter for *this specific lesson*.

## 12.2 What "embedding" means (the simple version)

An **embedding** is just a list of numbers (a vector, e.g. 1536 numbers) that
represents the *meaning* of a piece of text. Two texts about the same idea
produce vectors that are numerically close. So "find relevant text" becomes
"find the vectors closest to my query's vector" — plain math (cosine
similarity), **no LLM call needed for the search itself**.

You call an embedding API **once per chunk at upload**, store the numbers, and
reuse them forever. Embeddings are cheap (~1/100th the cost of generation).

## 12.3 What we already have (Step 0)

- **`DocumentChunk` table already exists** (`database.py:876`) with
  `document_id`, `chunk_index`, `content`, `token_estimate`, `embedding_json`.
  It is currently **empty scaffolding — nothing reads or writes it yet.** The
  hardest schema decision is already made.
- `Document.content` holds the full raw text (`database.py:121`). ✅
- A document repository (`repositories/document_repository.py`) is the single
  DB access point for documents. ✅
- Style docs, CDD source docs, and blueprint docs **all live in the same
  `documents` table** — so one chunking path covers every upload type you plan
  to add. ✅

## 12.4 Step-by-step, mapped to this project

### Step 1 — Chunk documents at upload time
Split `Document.content` into overlapping pieces:
- **Chunk size:** ~500–800 tokens each.
- **Overlap:** ~50–100 tokens between neighbours, so a sentence split across a
  boundary isn't lost.
- Split on paragraph/heading boundaries where possible (don't cut mid-sentence).

Write one `DocumentChunk` row per piece: `document_id`, `chunk_index`,
`content`, `token_estimate`.

**Where:** new helper `services/chunking.py`, called from the upload path (the
document repository create path / the upload router).

### Step 2 — Embed each chunk (once)
Right after chunking, send each chunk's text to an embedding model and store the
returned vector as JSON in `embedding_json`.
- **Model:** OpenAI `text-embedding-3-small` (1536 dims, cheap) is the pragmatic
  default since OpenAI is already wired. Bedrock Titan/Cohere embeddings are the
  AWS alternative.
- Do this in the **Celery worker**, not the request thread — a big PDF is dozens
  of API calls. Make it a background task like generation jobs.
- Track state (e.g. `embed_status: pending → embedded`) so you know it's ready.

**Where:** new `services/embeddings.py` (thin wrapper: text in → vector out,
with retry/fallback mirroring `core/llm_client.py`), plus a Celery task
`jobs/embedding_jobs.py`.

### Step 3 — Build the retrieval "query" at generation time
The query should describe what this lesson is about. All of it already exists in
`build_context_injection`:
- the lesson **topic**,
- the **learning objectives**,
- the **key concepts** (from CDD/Blueprint).

Concatenate those into one query string and embed it (one embedding call per
generation — negligible cost).

**Where:** `jobs/generation_jobs.py`, Stage 1 (`~line 199`), before `context` is
built.

### Step 4 — Retrieve the relevant chunks
Compare the query vector against the `embedding_json` of the candidate
documents' chunks (the same set gathered today at
`generation_jobs.py:206–237` — CDD source docs, selected library docs, style
docs). Compute cosine similarity, sort, keep the top matches.

Two ways to do the math:
- **Simple start (JSON):** load candidate chunks, compute cosine similarity in
  Python with numpy. Fine for hundreds/low-thousands of chunks. Zero infra
  change.
- **Scalable (pgvector):** you're on Postgres — install the `pgvector`
  extension, store embeddings in a real `vector` column, and let the DB do
  `ORDER BY embedding <=> query` fast. The `embedding_json` comment in the model
  literally anticipates this.

**Recommendation:** build with JSON + numpy first (proves the flow, no infra
risk), switch to pgvector once it works and volumes grow. The retrieval
function's interface stays the same.

**Where:** new `services/retrieval.py` →
`retrieve_relevant_chunks(db, query, candidate_doc_ids, token_budget)`.

### Step 5 — Token-based budgeting (pack, don't chop)
Instead of "chop the combined string at 30,000 chars," spend a **token budget**
deliberately:
```
context_budget = model_context_window
               − prompt_overhead (persona + style + CDD/BP injection + instructions)
               − max_output_tokens (16,384 today)
               − safety_margin
```
Then fill that budget with the highest-ranked chunks until full, **in priority
order** (CDD source chunks first, then library, then supplementary), and stop.
Each `DocumentChunk` already carries `token_estimate`, so packing is just adding
those up. Use a real tokenizer (`tiktoken` for OpenAI) instead of the
4-chars≈1-token guess. Keep a model → context-window map (hang it on the model
catalog in `core/llm_client.py`).

**Where:** `services/retrieval.py` for packing + `core/tokens.py` for counting.
This **replaces** `trim_generation_context` (`content_utils.py:52`) as the thing
that bounds context.

### Step 6 — Swap it into the generation flow
In `generation_jobs.py` Stage 1 the change is surgical:
- **Before:** loop over docs → `make_source_context(whole file)` → concatenate →
  `trim_generation_context` (blind chop).
- **After:** collect candidate document IDs →
  `retrieve_relevant_chunks(query, ids, budget)` → wrap the returned chunks with
  `make_source_context` (**keep the `[START SOURCE: filename]` markers** so the
  citation splitter at `content_utils.py:144` still works) → done.

Everything downstream (citations, block splitting, versioning) stays identical
because the output is still the same `[START SOURCE: ...]` format. **Retrieval
changes *what text* goes in, not the *shape* of the prompt.**

### Step 7 — Keep chunks fresh
- When a document is **edited/re-uploaded**, delete its old chunks and
  re-chunk + re-embed (`embed_status = stale` → re-run the task).
- **Backfill:** a one-time script that chunks + embeds all *existing* documents
  so old courses benefit too.

## 12.5 Where each new piece lives (file map)

| Piece | New/changed file | Notes |
|---|---|---|
| Chunking | `services/chunking.py` (new) | called on upload |
| Embedding wrapper | `services/embeddings.py` (new) | mirror `llm_client.py` retry/fallback |
| Embedding job | `jobs/embedding_jobs.py` (new) | Celery, runs at upload |
| Retrieval + packing | `services/retrieval.py` (new) | cosine + token budget |
| Token counting | `core/tokens.py` (new) | tiktoken; model→window map |
| Wire into upload | `repositories/document_repository.py` | trigger chunk+embed task |
| Wire into generation | `jobs/generation_jobs.py` Stage 1 | replace dump-all + `trim_generation_context` |
| Storage | `DocumentChunk` (exists) | later: pgvector column |

No frontend change needed — this is all backend. The UX is identical; the
prompts just get smarter.

## 12.6 Practical decisions (recommendations)

- **Embedding model:** `text-embedding-3-small` — cheap, good enough, OpenAI
  already wired.
- **Storage:** start `embedding_json` + numpy; graduate to **pgvector** when a
  course exceeds a few thousand chunks.
- **Chunk size:** ~600 tokens, ~80 overlap.
- **Top-K:** don't hardcode "top 10" — fill by **token budget** (Step 5), so a
  big-context model uses more chunks and a small one fewer, automatically.
- **Always-include vs retrieved:** keep CDD/Blueprint **summaries always fully
  included** (they're your constraints, not retrievable trivia). Only the
  *source/RAG material* goes through retrieval.

## 12.7 Rollout plan (low risk)

1. **Phase A:** add chunking + embedding at upload + backfill script. Generation
   is unchanged — you're only populating `DocumentChunk`. Fully safe.
2. **Phase B:** add retrieval behind a flag (e.g. `PROMPTOPS_USE_RETRIEVAL`).
   Off = current dump-all; On = retrieval. Compare on real lessons.
3. **Phase C:** flip the flag on by default; keep the old path as fallback for
   docs that aren't embedded yet.

## 12.8 Gotchas

- **Keep the `[Source: filename]` markers** on retrieved chunks or citation
  extraction and per-block source tagging break.
- **Cost is one-time per chunk**, but re-uploads re-embed — dedup by content
  hash if users re-upload the same file.
- **Retrieval quality depends on the query** — use topic + LOs + key concepts
  (Step 3); a query of just "Lesson 3" retrieves poorly.
- **Don't retrieve away your constraints** — CDD/Blueprint summaries stay
  always-in; only bulky source files go through retrieval.