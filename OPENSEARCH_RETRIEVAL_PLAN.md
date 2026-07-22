# Plan: OpenSearch Vector Retrieval → CAS Pipeline Injection

**Goal:** At every generation step (Style → CDD → Blueprint → Generate), automatically pull the most *relevant* chunks of client documents (AIM or Cengage) from the OpenSearch vector DB and inject them into the AI prompt — instead of relying only on S3 keyword matching.

**Date:** 2026-07-16
**Status:** Approach C implemented (Phase 1) — see "Implementation Status" at the bottom.

---

## 1. What we have today (current state)

- Every ingested document is already split into chunks, and every chunk already has an
  **embedding** (a "meaning fingerprint" — 1024 numbers from Amazon Titan) stored in
  OpenSearch (indexes `dis-content-dev-aim`, `dis-content-dev` etc.).
- **But nothing reads OpenSearch.** Live retrieval reads JSON files from S3 and scores
  chunks by keyword overlap (same words = match). Different words with the same meaning
  score zero.
- Retrieval already knows the "purpose" (style / cdd / blueprint / course_generation),
  filters restricted docs (answer keys, instructor guides), and returns one text block
  (`combined_context`) that CAS pastes into the LLM prompt.

**So the plumbing exists. The change = make retrieval search OpenSearch by meaning,
and design the right query for each pipeline step.**

---

## 2. Three possible approaches (pick one)

### Approach A — Keep S3 keyword search only (today)
- How: no change.
- Pros: zero work, predictable.
- Cons: misses relevant content whenever wording differs ("rust protection" ≠
  "corrosion control"). Whole-doc reading gets slow as library grows.

### Approach B — Pure vector search (OpenSearch kNN only)
- How: embed the query → ask OpenSearch for the closest chunks by meaning.
- Pros: best semantic relevance; fast even with thousands of docs.
- Cons: bad at *exact* tokens (course codes like "B2D4", "Quiz 6") — vectors
  don't understand IDs. Fails silently if index is incomplete.

### Approach C — Hybrid search (vector + keyword) ✅ RECOMMENDED
- How: run both — vector similarity for meaning + keyword match for exact terms —
  and blend the scores (e.g. 60% vector + 40% keyword).
- Pros: catches meaning AND exact codes/day numbers; industry standard for RAG.
- Cons: slightly more code than B.
- Safety net: if OpenSearch is down/empty → automatically fall back to today's
  S3 keyword retrieval. Nothing breaks, it only gets smarter.

---

## 3. What query to fire at each pipeline step

The quality of retrieval = the quality of the query. Each step already has natural
inputs — we turn them into the search query:

| Step | What the user is doing | Query we build (embedded + keywords) | OpenSearch filters |
|------|------------------------|--------------------------------------|--------------------|
| **1. Style** | Creating a writing-style reference | Style name + description + custom instructions (e.g. "conversational tone, aviation maintenance, student-friendly") | `client_id`, doc types: style_guide, sample_lesson, slide_deck, lesson_pdf; exclude restricted |
| **2. CDD** (course design) | Defining the course | Course name + description + audience + goals (e.g. "General Science II, Block 2, aviation maintenance students") | `client_id`, doc types: syllabus, course_outline, course_calendar, program_overview; exclude restricted |
| **3. Blueprint** (module/day map) | Structuring blocks & days | Per block/day: course + block number + day topics from the CDD (e.g. "Block 2 Day 4 aircraft drawings schedule objectives") | `client_id`, doc types: course_calendar, syllabus, block/day fields when present; exclude restricted |
| **4. Generate** (lesson content) | Writing an actual lesson | Lesson title + learning objectives + day topic from blueprint (e.g. "identify types of corrosion, cleaning methods, Day 5") | `client_id`, `block`, `day`, doc types: lesson_pdf, slide_deck, quiz, project, study_questions; exclude restricted |

**Key idea:** each step's query = the *output of the previous step*. CDD feeds
Blueprint's queries; Blueprint feeds each lesson's query. So context gets more
specific as we go down the pipeline — exactly what we want.

**Hand-picked docs override:** if the user explicitly selects documents as reference,
we fetch those documents directly (no search needed). Vector search is for
"find relevant material automatically."

---

## 4. How injection works (unchanged shape, better content)

For every step, retrieval returns the same structure CAS already consumes:

```
1. Build query text (from the step, table above)
2. Embed query with the SAME Titan model used at ingestion
3. OpenSearch hybrid query (vector + keyword) with client & security filters
4. Take top-k chunks within the token budget (~6000 tokens)
5. Return combined_context + source_units  ← same format as today
6. CAS pastes combined_context into the LLM prompt  ← no CAS changes needed
```

Because the output format is identical, **CAS, the frontend, and all prompts stay
untouched.** Only DIS's `retrieve()` internals change.

---

## 5. Security rules (must-keep)

1. Restricted docs (answer keys, instructor guides) are excluded **inside the
   OpenSearch query itself** (filter on `restricted` / `visibility`), never filtered
   after the fact.
2. Every query filters by `client_id` — AIM users can never receive Cengage chunks
   and vice versa.
3. Explicit user selections still pass through the existing visibility gate.

---

## 6. Implementation steps (phased, small)

**Phase 1 — Foundation (backend only)**
1. Add a query-embedding helper (Titan, same model/dimension as ingestion).
2. Add an OpenSearch search function in DIS: hybrid kNN + keyword, with
   client/purpose/restricted filters.
3. Wire it into `retrieve()` in `dis_backend/services/context_retrieval.py`:
   try OpenSearch first → fall back to current S3 scoring on error/empty.
4. One-time check: verify all ingested chunks actually have embeddings in the index
   (backfill script if any are missing).

**Phase 2 — Per-step query builders**
5. Style/CDD/Blueprint/Generate: build the query text from each step's inputs
   (table in section 3). Most of these inputs already exist in the request payloads
   CAS sends today.
6. Pass `block`/`day` filters from Blueprint → Generate so lessons pull day-matched
   material.

**Phase 3 — Quality & tuning**
7. Log retrieval results (which chunks, what scores) so we can inspect relevance.
8. Tune the vector/keyword blend (start 60/40) and top-k per step.
9. Optional: add a reranker later if needed (not required for v1).

---

## 7. Acceptance criteria (how we know it works)

1. Generating a lesson about "corrosion control" retrieves the corrosion chunks even
   when the query wording differs from the document wording.
2. Day-specific generation (e.g. Block 2 Day 4) retrieves that day's lesson/slide
   material, not random chunks.
3. Answer keys / instructor guides never appear in any retrieved context.
4. AIM generation never receives Cengage content, and vice versa.
5. If OpenSearch is stopped, generation still works (S3 fallback) — just less smart.
6. No change required in CAS routes, frontend, or prompt templates.

---

## 8. One-paragraph summary (for anyone)

> Our documents already have "meaning fingerprints" stored in OpenSearch — we just
> never use them. This plan makes each pipeline step (Style, CDD, Blueprint,
> Generate) build a short search query from what the user is doing, find the chunks
> whose *meaning* matches (plus exact keyword matches for things like "B2D4"), and
> feed those chunks into the AI prompt the same way we already do. Result: more
> relevant context at every step, same security rules, zero changes to the CAS app,
> and automatic fallback to today's behavior if OpenSearch is unavailable.

---

## Implementation Status (2026-07-16)

**Approach C implemented — DIS retrieval only. No CAS/frontend/prompt changes.**

### Code changes
- `dis_backend/services/indexing.py` — added read-only helpers:
  - `embed_query()` — embeds the query with the same Titan model used at ingestion.
  - `_vector_store_read_client()` / `vector_search()` — hybrid kNN + keyword query
    over the tenant OpenSearch index, filtered by `client_id`.
- `dis_backend/services/context_retrieval.py` — `retrieve()` now:
  1. Builds a security allow-set of `job_id`s from the existing S3 gate
     (`_passes_filters`) — unchanged security.
  2. Tries OpenSearch hybrid ranking (`_vector_units`), restricted to that
     allow-set. Falls back to S3 keyword ranking on hand-picked docs, empty
     query, or any OpenSearch/Bedrock error. Output shape identical
     (`combined_context` + `source_units`), plus a new
     `retrieval_summary.retrieval_method` field.

### Verified
- OpenSearch hybrid query works (returns correctly ranked chunks when fed a valid vector).
- Security allow-set gating works (restricted docs never surface; client isolation holds).
- Graceful fallback works (no query / hand-picked / OpenSearch error → S3 keyword).
- No regression: Source Library list still returns all docs; retrieve endpoint healthy.

### Blockers to fully ACTIVATE (both PRE-EXISTING, not caused by this change)
1. **Bedrock permission.** The IAM user the DIS container uses
   (`spawar@academian.com`) is **not authorized** for `bedrock:InvokeModel` on
   `amazon.titan-embed-text-v2:0`. Query embedding therefore fails and retrieval
   falls back to S3 keyword. → Grant `bedrock:InvokeModel` for the Titan model in
   `us-east-1` to that user/role. (Ingestion embeddings were created with
   different creds/role.)
2. **Eligibility filter/flag mismatch.** The `/retrieve/course-generation` and
   `/retrieve/style` handlers force `use_for_course_generation=True` /
   `purpose=style`, but AIM source records don't carry `use_for_*` flags and
   their doc types (`lesson_pdf`, `quiz`, `project`) don't match the purpose→type
   mapping in `aim.yaml` (`lesson_slide_deck`, `quiz_exam`, `project_activity`).
   Result: the allow-set is empty, so **both keyword and vector retrieval return
   0** for AIM course-generation/style today. → Align `aim.yaml`
   `source_type_mapping` with actual doc types and/or derive `use_for_*` flags
   from `purpose` in the source records.

Until #1 and #2 are addressed, retrieval behaves exactly as before (S3 keyword,
same results), so nothing is broken — the semantic path simply stays dormant.

---

## Update (2026-07-16, later) — Blockers RESOLVED, semantic retrieval LIVE

Both blockers are fixed and verified end-to-end via the live DIS API.

**Blocker #1 (Bedrock) — resolved.** New AWS credentials in `dis_backend/.env` are
authorized for `bedrock:InvokeModel`. Query embedding now succeeds.

**Blocker #2 (eligibility filters) — resolved with two additive changes:**
- `context_retrieval.py` `_iter_payloads`: derives `use_for_style/cdd/blueprint/
  course_generation` flags from each doc's inferred purpose (additive only —
  never relaxes restricted/visibility). Source-index records don't carry these
  flags, so without this the generation handlers' `use_for_*` filters rejected
  every doc.
- `config/clients/aim.yaml` `retrieval.source_type_mapping.course_generation`:
  added the real Source Library doc types (`lesson_pdf, slide_deck, quiz,
  final_exam, project, hangar_activity, study_questions`); the old list only had
  pipeline-era names (`lesson_slide_deck, quiz_exam, project_activity`) that no
  doc actually uses.

**Note on query text:** the query comes from the request's `context_input`
(flattened), NOT a top-level `query` field (that field is not in the
`DynamicContextRequest` schema). CAS already populates `context_input` per step.

**Verified live (AIM, role=user):**
- course-generation "corrosion control cleaning" -> method=opensearch_hybrid,
  returns Cleaning & Corrosion study questions + quiz (semantic).
- course-generation "aircraft drawings" -> returns the Aircraft Drawings lesson.
- blueprint / cdd -> return the syllabus (opensearch_hybrid).
- SECURITY: an answer-key-bait query returned NO answer keys/instructor guides
  (S3 allow-set gate drops restricted docs even when semantically top-ranked).
- ISOLATION: a Cengage query returns zero AIM files.
- Explicit document_ids selection still uses the S3 path (+ style-gate fix).
- Fallback: empty query / OpenSearch error -> S3 keyword (unchanged behavior).

**Follow-ups (optional):** Cengage `source_type_mapping` may need the same
real-doc-type alignment as AIM for its course-generation retrieval to populate.

---

## Update 3 (2026-07-16) — Both clients live + security hardened

Semantic retrieval now works end-to-end for BOTH AIM and Cengage in the CAS
pipeline, with answer-key protection made robust.

### Changes
- **Step 1 — harden restricted flags** (`context_retrieval.py`): answer keys /
  instructor guides are flagged `restricted` at read time by doc type + filename
  (`_is_restricted_source`), so protection no longer depends on the
  instructor-visibility gate.
- **Step 2 — allow content-team source docs** (`context_retrieval.py` gate +
  `api/routers/context.py`): the restricted gate now blocks only GENUINELY
  restricted content (`_is_hard_restricted`: restricted flag / access_level
  admin_only / internal-only / answer-key type). Instructor/content-team-authored
  SOURCE docs (Cengage manuscripts, AIM calendar) are allowed into generation.
  The course-generation handler no longer forces `visibility=student`.
- **Performance** (`services/artifacts.py`): cache the boto3 S3 client per
  ArtifactWriter instead of creating one per read (was ~1s/read). Retrieve latency
  32s -> 13s locally; most of the remainder is local-Docker -> us-east-1 S3 round
  trips (~0.5s each) that disappear in an in-region deployment (~5s expected).

### Verified live (role=user)
- AIM course-gen "corrosion control" / "aircraft drawings" -> opensearch_hybrid,
  relevant chunks.
- Cengage course-gen "marketing management" -> opensearch_hybrid, returns the
  textbook/manuscript chunks (previously 0).
- SECURITY: answer-key-bait query returns no answer keys; explicitly selecting an
  answer key as a user is blocked (returns 0).
- ISOLATION: a Cengage query returns zero AIM files.
- Fallback + hand-picked selection unchanged.

### Access-policy note
Genuinely restricted docs (answer keys, instructor guides, admin/internal-only)
never enter generation context for anyone (role=user always blocked; admins only
via explicit include_restricted). Content-team/instructor-authored SOURCE material
is now usable for generation for both clients.

---

## Update 4 (2026-07-16) — How the CAS pipeline injects the retrieved context (plain English)

This section explains, end to end, **what happens, what gets injected, and where**,
when a user runs a step in Content AI Studio.

### The journey of one generation request

1. **User acts in CAS** — clicks Understand Style / generate CDD / generate
   Blueprint / Generate a component in the browser.
2. **Browser → CAS backend only.** The browser never talks to DIS. CAS holds the
   secret service token.
3. **CAS builds a retrieval request** for that step and calls DIS at
   `POST /v1/context/retrieve/{purpose}`. The request carries:
   - `query` — free text describing what the step is about (built from the step's
     own fields; see table below),
   - `filters` — `purpose` (style/cdd/blueprint/course_generation) + optional
     document-type narrowing,
   - `retrieval` — `top_k` and `token_budget`.
   Plus client headers (`X-CAS-Client-Id`, etc.) so DIS knows the tenant.
4. **DIS `retrieve()` does the smart part:**
   - embeds the `query` with Titan,
   - runs the **hybrid OpenSearch search** (meaning + keywords), filtered to that
     client,
   - keeps only documents the **S3 security gate** allows (no answer keys/guides,
     correct client, correct purpose),
   - packs the best chunks into the token budget,
   - returns **`combined_context`** (one big readable text block) and
     **`source_units`** (the list of chunks used, for tracing).
5. **CAS injects it into the prompt.** CAS wraps `combined_context` with a short
   instruction and adds it to the LLM prompt as **extra instructions** — it does
   NOT replace CAS's own prompt. Then the LLM generates.
6. CAS stores `source_units` so you can see which sources fed the output.

### What each step sends and where the context is injected (from the code)

| Step | CAS file (call site) | `query` is built from | Injected where | Wrapper instruction |
|------|----------------------|------------------------|----------------|---------------------|
| **Style** | `app/api/v1/routers/styles.py` (`_retrieve_dis_style_context`, ~L94–112; used ~L483) | A fixed "understand instructional style, tone, structure, prohibited patterns" query + the **documents you hand-picked** (`document_ids`). top_k=20, budget≈14k | Prepended into `extra_instructions` passed to `generate_style_understanding()` / `regenerate_style_understanding()` | "Use the following processed DIS Source Library documents as the **authoritative style reference** context." |
| **CDD** | `app/api/v1/routers/cdd.py` (`_dis_context_block`, ~L75–89; used ~L234) | course_title + document_title + target_audience + expert_domain + audience_category + extra_instructions | Appended to the user prompt / extra block | "CDD … FROM DIS SOURCE LIBRARY. Use this as **supporting source context only. Follow CAS style, structure, and user instructions first.**" |
| **Blueprint** | `app/api/v1/routers/blueprints.py` (`_dis_context_block`, ~L66–80; used ~L182) | selected_module + extra_instructions + the CDD context + teacher/student | Appended to the prompt | Same "supporting source context only" wrapper |
| **Generate** | `app/api/v1/routers/generations.py` (`_dis_context_block`, ~L42–56; used ~L115) | component_label + component_value + component_type + target_audience + expert_domain + audience_category + extra_instructions. top_k=16, budget≈16k | Concatenated onto `extra_instructions` for the generation job | Same "supporting source context only" wrapper |

**Key idea:** each step feeds the next — the CDD text becomes part of the
Blueprint query, and the Blueprint/component becomes part of the Generate query —
so the retrieved context gets more specific as you go down the pipeline.

### What "combined_context" actually looks like

One text block = the top-ranked chunks joined by `---`, each formatted as readable
content (no internal metadata):

```
Source: B2D4 - Aircraft Drawings - Day 4.pdf
Title: Aircraft Drawings
<the chunk text…>
---
Source: Block 2 Study Questions ….pdf
Title: …
<the chunk text…>
```

It stays within the step's token budget (Style ≈14k, CDD/Blueprint ≈12k,
Generate ≈16k tokens).

### Where it lands in the prompt (important)

It is added as **extra / supporting instructions**, appended to the prompt — it
does **not** overwrite CAS's own prompt templates. Priority order the LLM is told
to follow: **CAS style + structure + user instructions first**, DIS source context
as support. (Style is the one exception: there the selected docs are the
*authoritative* style reference.)

### Safety that holds at every step (unchanged by injection)

- Answer keys / instructor guides / admin-internal content is **never** injected.
- One client never receives another client's content (AIM ↔ Cengage isolation).
- A normal user (`role=user`) can never pull restricted content.
- If DIS or OpenSearch is unavailable, or nothing matches, the block is simply
  empty and generation continues without it (each call is wrapped in try/except).

### Fix required to make CAS actually use semantic search

- **Added `query` to DIS `DynamicContextRequest`** (`dis_backend/api/routers/context.py`).
  CAS sends the query as a top-level `query` field, but the DIS schema didn't
  declare it, so pydantic silently **dropped it** — leaving semantic search with
  no query and falling back to unranked keyword results. With the field added, the
  query flows through and hybrid retrieval engages for real CAS calls (verified for
  both AIM and Cengage using the exact CAS payload shape).

### Known follow-up (CAS-side over-filtering)

Each CAS handler also sends a hard-coded `document_types` list (e.g. Generate:
`textbook_chapter, activity, assessment, rubric, lesson_plan, slide_deck,
student_handout, style_guide, authoring_guide`). These names **don't match the
actual stored doc types** (AIM: `lesson_pdf`, `quiz`, `project`, …; Cengage:
`pdf`), so in the real pipeline they can over-restrict Generate/CDD/Blueprint to
only the few matching types. To get full coverage, align these lists with the real
doc types **or** drop them and rely on `purpose` + semantic ranking (the security
allow-set already enforces safety). This is the same class of fix as the
`aim.yaml source_type_mapping` alignment and is a small CAS-side change.

### One-line summary

> When you run any step, CAS turns that step into a short query, DIS finds the most
> relevant, allowed source chunks by meaning (+ keywords) and returns them as one
> `combined_context` block, and CAS pastes that block into the LLM prompt as
> supporting context (authoritative for Style) — with answer keys and other clients'
> content always excluded, and a safe fallback if anything is unavailable.

---

## Update 5 (2026-07-16) — CAS document_types filters removed (follow-up done)

The hard-coded `document_types` filters in the CAS generation handlers were
removed, because their fixed type names did not match the real stored doc types
and were silently returning zero results.

**Changed:**
- `app/api/v1/routers/generations.py` — dropped `document_types` (Generate).
- `app/api/v1/routers/cdd.py` — dropped `document_types` (CDD).
- `app/api/v1/routers/blueprints.py` — dropped `document_types` (Blueprint; the
  DIS blueprint handler already narrows to calendar/syllabus).

Retrieval now relies on `purpose` + semantic ranking; the security allow-set is
unchanged. CAS runs with a mounted volume + `--reload`, so these took effect
without a rebuild.

**Verified (real CAS payload shape: top-level `query`, no `document_types`):**
- AIM course-gen -> opensearch_hybrid, 5 | AIM cdd -> opensearch_hybrid, 3
- Cengage course-gen -> opensearch_hybrid, 5 (was 0) | Cengage cdd -> 1 (was 0)
- SECURITY re-checked: AIM answer-key-bait query still leaks NOTHING.
