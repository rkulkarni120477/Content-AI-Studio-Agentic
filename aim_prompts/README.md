# AIM Blocks V2 prompts

The AIM prompt set, transcribed from `Promts _AIM.docx` and adapted to the
generation routes in this repo. One file per component.

## What is here

| File | Component | Route | Status |
|---|---|---|---|
| `AIM_BLOCK_BLUEPRINT_PROMPT.md` | `cdd` — "Blueprint" in the UI | `POST /cdd/generate` and the block-wide digest pipeline | **Live** as DB prompt id 82, v2 |
| `AIM_TODAYS_MISSION_PROMPT.md` | `generate` | `content_generation` | Not yet in the DB |
| `AIM_LEARN_IT_PROMPT.md` | `generate` | `content_generation` | Not yet in the DB |
| `AIM_DAY_REFLECTION_PROMPT.md` | `generate` | `content_generation` | Not yet in the DB |
| `AIM_UP_NEXT_IN_CLASS_PROMPT.md` | `generate` | `content_generation` | Not yet in the DB |
| `AIM_INSTRUCTOR_MANUAL_PROMPT.md` | `generate` | `content_generation` | Not yet in the DB |
| `AIM_DOMAIN_CONTEXT_BLOCK.md` | — | pasted into Extra instructions | Context layer, not a prompt |
| `AIM_STYLE_GUIDE.md` | — | Style record + Extra instructions | Content artifact, not a prompt |

The five DLU/manual prompts match the DLU parts the frontend already recognises
(`frontend/src/utils/dluBlueprint.js`): Today's Mission, Learn It, Quick Check, Up
Next, Day Reflection. **Quick Check has no prompt** — the source document defines
it as a downstream output and as a Blueprint targeting field, but never writes a
prompt for it. Nor does it write one for Summative Assessment.

## File format

Each generation prompt is exactly two fenced ` ```text ` blocks, under `# System
Prompt` and `# User Prompt`. That layout is load-bearing: the tests in
`tests/unit/test_prompt_capability.py` split on the fences to reconcile the
Blueprint prompt against `_DAY_TABLE_HEADER`, and the same split is how each file
gets loaded into a `PromptVersion`.

The two governing files break that layout deliberately, and each says why in its
own opening lines. They produce no output of their own, so a System/User pair would
be a prompt that generates nothing.

**Only fenced-block content ever reaches a model.** The prose around the fences in
every file here — this README included — is documentation for whoever maintains the
repo. Nothing in this directory is read from disk at runtime: a prompt is loaded by
copying its fenced blocks into a `PromptVersion`, and a governing block by copying
its fenced content into the Style record or the Extra instructions field. So no
prompt text may name a file or a path, because the model has no repository to
resolve it against. Inside the fences, the sections refer to each other the way the
curriculum does — "Learn It", "Up Next in Class", "Block Blueprint", "Quick Check" —
and never by filename.

### There is no Master System Prompt at runtime

Every prompt here descends from AIM's own stack, in which a **Master System Prompt
(MSP-3.0)** and per-domain Domain prompts sit above the component prompts and carry
the shared rules: the source hierarchy, the content-conduct rules, the
technical-terminology requirement, and the ACS coverage requirement. You can read
MSP-3.0's text in this repo at `BLOCK2_BLUEPRINT_PROMPT_V4.md:14`, where an earlier
draft inlined it.

**Nothing in this application injects MSP-3.0 or a Domain prompt.** The `cdd` route
builds its system prompt from the template alone (`cdd.py:592-617`); the `generate`
route prepends only a flag-gated `persona_tone` fragment and a citation instruction
(`generation_jobs.py:313,405`). Of the three governors AIM's headers name, exactly
one has a live channel: the pinned Style record, which reaches **both** routes as
`{{style_guidelines}}` (`cdd.py:564,581` and `generation_jobs.py:307,328`).

So the MSP's role is filled here by two things, and a third file would be neither:

1. **`AIM_STYLE_GUIDE.md` Part A is the de facto MSP.** Pinned as the Style record,
   it is the one place project-wide rules can live and actually arrive. It already
   carries the source hierarchy and the conduct and terminology rules.
2. **Each prompt restates those rules in its own fences,** so nothing depends on
   that pin being set. The duplication is deliberate: unconditional beats DRY when
   the alternative is a rule that silently vanishes if a record is not pinned.

Consequently **no fenced block in this directory names MSP-3.0, a Domain prompt, or
a Course Style Guide.** Every prompt opens with a `GOVERNING RULES` paragraph that
says the operative rules are in this prompt or in the supplied style guidelines,
and forbids inferring a rule from any document the model was not given. Naming the
lineage inside a fence was rejected on purpose: the fence is read by the model, not
by a reviewer, so a document name there is either ignorable or misleading — it tells
the model governing text exists somewhere it cannot look. Lineage is documentation,
so it lives here, outside the fences.

**Do not reintroduce a document name into any fenced block, and do not add an MSP
markdown file** — a seventh file would have no injection path, which is the same
mistake in a new costume. The two governing files were corrected the same way: their
own fenced blocks are pasted into the Style record and Extra instructions, so they
reach a model too, and they no longer name MSP-3.0 either.

The same caveat applies to `AIM_DOMAIN_CONTEXT_BLOCK.md`: its Domain 1 block reaches
a generation only if someone pastes it into the Style record or Extra instructions.

## Variables

`prompt_builder.render` is strict — a declared `{{var}}` that the route does not
supply raises rather than rendering empty. The routes supply different dicts, so a
variable valid in one file is a hard failure in another.

**`cdd` route** (`app/api/v1/routers/cdd.py`, the `variables` dict): `course_title`,
`course_name`, `target_audience`, `expert_domain`, `audience_level`,
`estimated_duration`, `extra_instructions_block`, `extra_instructions`,
`style_guidelines`, `grade_level`, `block`.

**`generate` route** (`promptops_app/jobs/generation_jobs.py`, the
`build_context_variables` call): `course_name`, `grade_level`,
`learning_objectives`, `style_guidelines`, `cdd_context`, `blueprint_context`,
`document_summary`, `teacher_mode`, `student_mode`, `output_format`, `topic`,
`lesson_topic`, `lesson_title`, `lesson_objective`, `content_type`,
`context_injection`, `target_audience`, `component_label`, `component_type`.

Note what is **not** there: `block` and `extra_instructions` exist on the `cdd`
route only. The five day-level prompts use `{{course_name}}` where the Blueprint
uses `{{block}}` — on this project a course *is* a Block.

**`{{learning_objectives}}` is a pointer, not an objective.** The generate route
sets it to the literal string `"As defined in the Blueprint for '<topic>'"`, or
`"Generate content for '<topic>'"` when nothing is pinned — never the day's
objective text. A prompt that labels it "derived day objective" is lying to the
model. The five day-level prompts label it as generation scope and read the real
objective out of the day plan.

**`{{topic}}` is the component label.** `generations.py:169` sets
`"topic": request_body.component_label`, so on a DLU day it renders as
`Day 11: Corrosion Theory` — the day number arrives with it. That is also why the
label-derived quiz-stem caveat below matters: `topic` and the stem selector read the
same string.

**Two flags are derived, not supplied.** `Assessment Adjacent` is not a Blueprint
column at all — it is derived from whether an assessment falls on this day or the
next. `Learn-While-Doing` *is* a column, but the Blueprint emits it as a reasoning
clause with explicitly "no leading Yes or No", so a prompt cannot branch on it being
`TRUE` without first saying how to read the clause as a boolean. Today's Mission and
Up Next in Class both do.

Verify before loading a prompt into the DB:

```python
from promptops_app.prompts.prompt_builder import render, extract_variables
# split the file on ```text, then render each block against the route's real dict
```

## What the model actually receives

Neither route hands the prompt a labelled field per fact. The day's facts arrive
through two code-injected channels, and every day-level prompt here is written to
read them:

- **`{{context_injection}}`** — the CDD and Blueprint summaries for the pinned
  documents, built by `build_context_injection`.
- **Appended source context** — the retrieved documents, each wrapped as
  `[START SOURCE: filename] … [END SOURCE: filename]`, appended to the user message
  *after* rendering. For a `dlu_day` component the day's plan is prepended to this
  block as `--- DLU DAY BLUEPRINT ---`.

So the source document's "Block number: [fill] / Day number: [fill] / SOURCE 1 —
[PASTE]" pattern, which assumes a human pastes eighteen fields and six documents,
is replaced throughout by an instruction to read those facts out of the day plan
and the source blocks, and to flag anything absent rather than infer it. The
prompts match on meaning rather than on an exact field label, because the day plan
and the Blueprint row name the same facts differently.

## Adaptations from the source document

Every change from `Promts _AIM.docx` was forced by how this repo generates, or by
an inconsistency in the source. Each is listed so it can be reversed if the
reasoning stops holding.

1. **Word documents and JSON became markdown.** Five of the seven sections specify
   "Deliver as a Word document … never JSON as the deliverable", one specifies a
   JSON schema. The pipeline stores the model's reply as the deliverable and parses
   markdown out of it, so every prompt now declares a markdown `OUTPUT SHAPE`
   carrying the same named sections the source asked for.

2. **The word "storyboard" is banned from output.** `generation_jobs.py` runs
   `re.sub(r'(?i)\bstoryboard\b', '', out)` on every reply. The source's "LEARN IT
   STORYBOARD" header would be delivered as "LEARN IT ". The Learn It artifact is
   called a *production specification* throughout, and both Learn It and the
   Instructor Manual instruct the model not to emit the word. Those two prompts state
   the rule only — the mechanism above is recorded here rather than in prompt text,
   because a model cannot act on a regex it cannot see.

3. **Learn It's concept-type list collided with the Blueprint's.** Learn It switches
   its structural weighting on a ten-value list (Terminology-Heavy, Component
   Identification, System Relationship…) that shares no vocabulary with the closed
   Concept Type list the Blueprint prompt emits, while telling the model to take
   the concept type *from the Blueprint*. Those ten are now a second axis — a
   **content shape**, a production judgment about how the material is organised —
   and the Blueprint's Concept Type is recorded as given. Neither replaces the
   other.

4. **The style guide was split.** `AIM_STYLE_GUIDE.md` explains this at length:
   Block 2's missing S-codes, its AKTR miss-rate table, its day mappings and its
   glossary were packaged inside a project-wide style guide. Pinned as a Style they
   would inject Block 2 facts into every generation for Blocks 1, 3 and 4 — which
   every prompt here forbids. Part A is the project style; Part B is Block standing
   data.

5. **Hardcoded scope was parameterised.** The Blueprint prompt named "Block 2 —
   Aircraft Drawings…" in its own text; it now takes `{{block}}`. The Domain prompt
   hardcodes General Studies; no route supplies a domain identifier, so it stays a
   hand-filled block with a template for the other two domains.

6. **Review flag vocabularies were closed and reconciled.** The Blueprint, Today's
   Mission, Day Reflection, Up Next and the Instructor Manual share one vocabulary.
   Learn It keeps its own eight labels, which the source declares "EXACT LABELS
   ONLY", plus `REQUIRES_ID_JUDGMENT` — which the source *uses* twice, in the
   interaction decision and in Pass 3, without listing it.

7. **Standing provenance flags were preserved.** Day Reflection and Up Next in Class
   have no confirmed AIM design document; both source sections say so and require
   `REQUIRES_ID_JUDGMENT` on every output. Both prompts carry that as a standing
   flag, and both state that it is not licence to soften the rules.

## Editing

`EDITING_BLOCK_WIDE_PROMPTS.md` in the repo root covers the Blueprint prompt: the
day-table contract, the one-line rule for `ADDITIONAL DAY COLUMNS` declarations,
and what the reconciliation panel in the prompt picker does and does not report.
That panel only assesses components that produce a day table, so it stays silent
for the five `generate` prompts.

## Selecting these prompts

An explicitly selected prompt wins outright: `prompt_id` is resolved by ID, filtered
only on `prompt_kind == "pipeline"` and not-deleted, with no `component_type` or
`variant` check. So picking one of these in the prompt picker always uses it.

One caveat if any of them is ever made the **component default** instead. The
generate route chooses its stem from the component label:

```python
_is_quiz = (_comp_type == "assessment"
            or "assessment" in _comp_label.lower()
            or "quiz" in _comp_label.lower())
```

DLU day components are labelled `Day 11: <topic>`, so a day whose *topic* contains
"assessment" or "quiz" — a review or exam day — resolves `quiz_generation` rather
than `content_generation`, and would pick up the quiz default instead of the DLU
section default. Explicit selection is immune; a default is not.

## Known gaps

- **Nothing here has been generated with yet.** Every check so far is static:
  variables render, the format parses, the Blueprint reconciles 32/32.
- **AKTR data is read from the source library, not from a prompt.** Ingest the
  block's AKTR missed-code rollup as a `knowledge_test_report` and
  `Targets for Quick Check` plus the high-miss fields fill with the real figures,
  computed in code (`dis_backend/services/digests/worksheets.build_acs_registry`).
  Codes the rollup does not list still read `NO AKTR DATA`, per code. Where no such
  document is ingested for a block, every field reads `NO AKTR DATA` as before —
  `AIM_STYLE_GUIDE.md` Part B carries Block 2's table by hand, and every prompt is
  written to say so explicitly rather than infer difficulty. No prompt change can
  close this: it is an ingestion question.
- **No Quick Check or Summative Assessment prompt**, per the note above.
- **Domains 2 and 3 are a template**, not content. The source document describes
  only General Studies.
