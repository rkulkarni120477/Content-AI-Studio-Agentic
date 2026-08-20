# AIM Blocks V2 prompts

The AIM prompt set, transcribed from `Promts _AIM.docx` (and the DLU Outline from
`Outline_DLU 1.docx`) and adapted to the generation routes in this repo. One file
per component.

## What is here

| File | Component | Route | Status |
|---|---|---|---|
| `AIM_BLOCK_BLUEPRINT_PROMPT.md` | `cdd` — "Blueprint" in the UI | `POST /cdd/generate` and the block-wide digest pipeline | **Live** as DB prompt id 82, v2 |
| `AIM_DLU_OUTLINE_PROMPT.md` | `blueprint` — one Day, selected on the Blueprint page | `POST /blueprints/generate`, legacy single-call path | Not yet in the DB |
| `AIM_TODAYS_MISSION_PROMPT.md` | `generate` | `content_generation` | Not yet in the DB |
| `AIM_LEARN_IT_PROMPT.md` | `generate` | `content_generation` | Not yet in the DB |
| `AIM_DAY_REFLECTION_PROMPT.md` | `generate` | `content_generation` | Not yet in the DB |
| `AIM_UP_NEXT_IN_CLASS_PROMPT.md` | `generate` | `content_generation` | Not yet in the DB |
| `AIM_INSTRUCTOR_MANUAL_PROMPT.md` | `generate` | `content_generation` | Not yet in the DB |
| `AIM_DOMAIN_CONTEXT_BLOCK.md` | — | pasted into Extra instructions | Context layer, not a prompt |
| `AIM_STYLE_GUIDE.md` | — | Style record + Extra instructions | Content artifact, not a prompt |

The five DLU/manual prompts match the DLU parts the frontend already recognises
(`frontend/src/utils/dluBlueprint.js`): Today's Mission, Learn It, Quick Check, Up
Next, Day Reflection. **Quick Check has no prompt** — the source documents define
it as a downstream output, as a Blueprint targeting field, and as an item-outline
plan inside the DLU Outline, but none of them writes a prompt that produces the
items. Nor does any write one for Summative Assessment.

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

**`blueprint` route** (`app/api/v1/routers/blueprints.py:328`, the `variables`
dict): `cdd_context`, `selected_module`, `extra_instructions`, `teacher_mode`,
`student_mode`, `style_guidelines`, `block`. That is the whole dict — the seven
names the DLU Outline is written against, and the only ones it may declare.

**`generate` route** (`promptops_app/jobs/generation_jobs.py`, the
`build_context_variables` call): `course_name`, `grade_level`,
`learning_objectives`, `style_guidelines`, `cdd_context`, `blueprint_context`,
`document_summary`, `teacher_mode`, `student_mode`, `output_format`, `topic`,
`lesson_topic`, `lesson_title`, `lesson_objective`, `content_type`,
`context_injection`, `target_audience`, `component_label`, `component_type`.

Note what is **not** there: `block` and `extra_instructions` exist on the `cdd` and
`blueprint` routes only. The five day-level prompts use `{{course_name}}` where the
Blueprint uses `{{block}}` — on this project a course *is* a Block.

**The DLU Outline does not declare `{{block}}`,** though its route supplies the
name. The Blueprint page never sends `block` (`BlueprintPage.jsx:312-331` sends
`selected_module` and `day_number`), so the route resolves it to `""` and the
variable would render as an empty string — a prompt asserting a block it was never
told. The outline reads the block identifier out of the supplied Blueprint instead,
and flags it when absent.

**`{{cdd_context}}` carries the whole Block Blueprint, not a summary.** For a DLU
CDD, `extract_cdd_summary` finds none of its six priority section names (the
document is worksheets) and falls back to the full content uncapped
(`cdd_parser.py:338-362`, `_cap(text, 0)` is a no-op). And `extract_module_section`
returns `""` for a Day selection — it requires a literal "Module N"
(`cdd_parser.py:380-382`) — so nothing narrows it to the day either. That is why
the outline prompt tells the model to *find its own day's row* in the day-by-day
map rather than expecting a pre-extracted one.

**`{{extra_instructions}}` carries the day statement and the retrieved sources.**
`buildExtraInstructionsBlock` puts "**This is Day N (topic) of the block.**" at its
head (`blueprintModules.js:251-259`), and the route appends whichever grounding
block succeeded — `BLUEPRINT DAY CONTEXT` when day-scoped digest grounding is
available, `BLUEPRINT CONTEXT` from the blob query otherwise — plus the active style
as a prefix. Retrieved units arrive in **two different shapes** on this route, and
the prompt describes both because which one shows up is a client flag, not a
choice: the blob-query pack formats each unit as `Source: <filename>` / `Title:` /
text separated by a rule (`context_retrieval.py:1144-1153`), while the day-scoped
bundle renders `DAY SOURCE UNITS` / `RELATED HANDBOOK PAGES` as
`• [unit_type] Title` bullets with the text beneath and **no filename at all**
(`dis_day_context.py:_render_day_context`). Neither is the
`[START SOURCE: filename]` wrapper the `generate` route appends. That is also why
the outline's source map says "filename where one is given, unit title where none
is" — requiring a filename would be unsatisfiable under the day-scoped shape. The
same render marks a restricted unit `(restricted — title only)`, so the prompt
treats a title-only unit as no evidence, alongside an `EXISTS BUT UNVERIFIED`
inventory status.

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

## The DLU Outline sits between the Blueprint and the five sections

`AIM_DLU_OUTLINE_PROMPT.md` is the missing middle of the chain the other files
already describe. The Block Blueprint plans the whole block, one row per day; the
five day-level prompts write one section of one day. The outline is what turns one
Blueprint row into the day-level design the five then draft from — and on this
platform it is not a separate artifact type. **A DLU Outline *is* a Blueprint** for
a single Day, generated on the Blueprint page with a Day selected in the dropdown
instead of a Module.

That has four consequences the prompt is written around, all of them checkable in
code rather than matters of taste:

1. **It runs on the legacy single-call blueprint path, not the digest pipeline.**
   `run_block_wide_sync` engages only when `block` is supplied *and* the digest
   pipeline is on for the client, and its `day_number` guard keeps a day-scoped
   request out of the block-wide path entirely (`blueprints.py:198-206`). A Day
   request therefore always falls through to the single call, which stores the
   model's reply **as** the document. So the outline is written to be the whole
   deliverable in one reply, and it trips neither `reject_if_unsatisfiable` nor
   `context_was_dropped` (`prompt_capability.py`) — verified against the rendered
   pair. Note that `day_number` may still fetch day-scoped *grounding* from the
   digest store when the pipeline is on for the client
   (`resolve_day_context_block`); that arrives inside `{{extra_instructions}}` as
   `BLUEPRINT DAY CONTEXT` and replaces the blob query rather than adding to it.
   The prompt describes both labels for that reason and depends on neither.

2. **Its output shape is what `dluBlueprint.js` parses.** The Blueprint page finds
   per-part Save/Regenerate controls only through `detectDluBlueprint` +
   `parseDluBlueprintSections`, which key on `### DLU Outline` and on lines that are
   *entirely* a bold canonical part name (`HEADER_LINE_RE` + `DLU_TITLE_RES`). So
   the prompt's `OUTPUT SHAPE` emits exactly that marker and exactly those five
   names, forbids any other bold-only line, and keeps the review day's part named
   "Learn It (Review Content)" so it still matches `/^learn\s*it\b/i`. The source
   document's `## 1. Today's Mission` headings would match none of it and the
   controls would silently not appear.

3. **Everything that is not one of the five parts goes ABOVE them.** The parser
   assigns every line after the last part header to that part, so a trailing
   appendix becomes part of Day Reflection — and regenerating Day Reflection would
   then delete the ACS table and the Open Items. The ACS table, Master Mechanic
   Moment status, interactive/job-aid notes, source map, open items and sign-off
   lines therefore precede `### DLU Outline`, which reverses the source document's
   order (it ends with Open Items). Checked by running the real parser over a
   sample: six sections, Overview holding the whole appendix, and a Quick Check
   splice round trip leaving the other five untouched.

4. **Its first day number is scraped as *the* day number.** `_parse_dlu_day`
   (`blueprint_parser.py:360-374`) runs `re.search(r"\bday\s*(?:number)?\s*[:\-]?\s*(\d+)")`
   over the whole document to label the Generate page's Content Type entry. A bold
   label line is invisible to it — the `**` after the colon blocks the match, which
   is why today's real DLU blueprints yield no day at all — so the first thing it
   *can* match wins, and on a draft whose header said `**Day Number:** 11` that was
   "Day 13" out of the Learn-While-Doing clause. Verified by running the parser: it
   returned day 13. The prompt therefore requires a plain
   `# DLU Outline - Day <N>: <topic>` title line above everything else, and states
   that its numeral must be the first day number in the document. With it the
   parser returns 11. (The primary path, `_list_dlu_day_components`, reads the
   blueprint *record title* instead and is unaffected either way.)

**The outline becomes the day plan the five day-level prompts read.** For a
`dlu_day` component, `generation_jobs.py:282-292` prepends this artifact's
`full_content` verbatim as `--- DLU DAY BLUEPRINT ---`. That is why its header
carries every field those prompts name — day number, topic label, concept type,
concept scope, derived day objective, ACS codes, projects and hangar activity,
assessment status, Learn-While-Doing, application connection — as bold label lines
rather than leaving them implied in prose, and why it emits a `Day Type Flag` from
the closed Content-Delivery / Project-Application / Review-Assessment set those
prompts branch on, alongside the source document's own Teaching / Project / Review
Day naming that the existing artifacts use.

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


### From `Outline_DLU 1.docx` specifically

8. **The eighteen pasted input fields became four supplied channels.** The source's
   `INPUTS FOR THIS REQUEST` block ends with `[PASTE COMPLETE DAY ROW HERE]`,
   `[PASTE WORKSHEET 2 / INPUT BUNDLE DETAILS HERE]`, prior rows for a review day,
   and Master Mechanic Moment verification — four hand-pastes by a human. All four
   arrive here through the route: the day row inside `{{cdd_context}}` — the whole
   Blueprint, so prior rows for a Review Day, the ACS code registry's task
   descriptions and Master Mechanic Moment checks, and the source file inventory's
   availability statuses are all already present — and the input bundle and any
   further MMM evidence inside the retrieved source material. The prompt names the
   four channels, and the three parts of the Blueprint, where each fact lives.

9. **A part is never omitted, only emptied.** The source omits Learn It on a
   project day and Quick Check where no new K-codes are active. Omitting a part
   would renumber the rest and break the parser's part list — and the day-level
   prompts read the plan expecting all five. So each day-type rule now prescribes
   the exact single line that part carries instead ("No new K-codes — see prior
   day's Quick Check coverage."), which is also what the source wanted printed.

10. **"Storyline Candidate" reads as "Interactive Candidate".** The Blueprint's day
    table emits `Interactive Candidate` / `Interactive Type` (`block_wide_service`'s
    `_DAY_TABLE_HEADER`, and `prompt_capability._ALIASES` maps the Storyline naming
    onto it). The outline matches on meaning across both namings and records the
    row's Yes/No as given — it may not upgrade a No. **"Canvas" and "Storyline" stay**
    as the content-treatment vocabulary the docx uses, because both are live here:
    Learn It emits `## CANVAS PAGE LAYOUT`, and the Blueprint's day table has a
    `Storyline Source Asset Status` column. Only the word "storyboard" is banned from
    the output, for the reason recorded in adaptation 2 — this artifact is prepended
    verbatim into the day-level generations, whose replies are scrubbed of that word,
    so a plan that seeds it produces a section with a hole in it. An early draft of
    this file had genericised Canvas to "page" and Storyline to "interactive"; that
    lost the distinction between static LMS content and an authored interactive,
    which is the exact call the outline exists to make.

11. **The A–K "future lessons" section list is folded in, not added as sections.**
    The docx opens with an eleven-row table (Lesson Blueprint Snapshot … Job Aid
    Opportunity) and then writes a prompt whose `REQUIRED OUTPUT FORMAT` already
    covers most of it. The mapping used: A → the day header; B → the ACS table plus
    the high-miss treatment; C and D → Learn It's scope and sequence and the derived
    day objective; E → the lesson-section lines; F → Master Mechanic Moment status;
    G → Quick Check and the interactive notes; H → Quick Check plus the review quiz
    named in the row; I → Up Next in Class; J → the source map; K → the job-aid
    notes. No section was invented for a row the prompt's own format already
    carried.

12. **The day-type mapping was reconciled with two closed vocabularies.** The docx
    keys Teaching Day off "Conceptual, Procedural, Calculation, Safety, or Mixed"
    and Review Day off Review-Assessment alone. Neither Calculation nor Safety is a
    value the Blueprint can emit, and two labels it *can* emit — Summative
    Assessment and the compound Project-Application / Review — get no day type from
    the docx at all. Both now resolve to Review Day, which is where
    `AIM_TODAYS_MISSION_PROMPT.md` already puts them, so the two prompts cannot
    disagree about the same day. The prompt states that every label in the closed
    set selects exactly one day type, because a label with no rule is the one case
    where the model must invent something.

13. **Cognitive-level and difficulty judgments are data-gated.** The source requires
    APPLY or ANALYZE treatment for codes in "AKTR high-miss data" without saying
    where that data comes from. Here it arrives only as an ingested missed-code
    rollup or `knowledge_test_report`; absent one, the prompt writes `NO AKTR DATA`
    and takes the level from the row's own targeting, and is explicitly forbidden
    from inferring difficulty. Same rule, same wording, as the other files —
    see **Known gaps**.

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
  variables render, the format parses, the Blueprint reconciles 32/32. For the DLU
  Outline: its six declared variables render against the blueprint route's real
  dict, its output shape parses into Overview + the five parts through the actual
  `dluBlueprint.js` (and survives a splice round trip), and it trips neither
  `reject_if_unsatisfiable` nor `context_was_dropped`. **It is not in the DB** —
  until its fenced blocks are loaded into a `PromptVersion` with `component_type`
  `blueprint`, selecting it on the Blueprint page is not possible.
- **AKTR data is read from the source library, not from a prompt.** Ingest the
  block's AKTR missed-code rollup as a `knowledge_test_report` and
  `Targets for Quick Check` plus the high-miss fields fill with the real figures,
  computed in code (`dis_backend/services/digests/worksheets.build_acs_registry`).
  Codes the rollup does not list still read `NO AKTR DATA`, per code. Where no such
  document is ingested for a block, every field reads `NO AKTR DATA` as before —
  `AIM_STYLE_GUIDE.md` Part B carries Block 2's table by hand, and every prompt is
  written to say so explicitly rather than infer difficulty. No prompt change can
  close this: it is an ingestion question.
- **The blueprint route has no truncation guard.** `cdd.py:195` refuses a reply
  that hit the model's output cap (`_reject_if_truncated`); `blueprints.py` has no
  equivalent, so a DLU Outline cut short by the cap is stored as the document with
  no error shown. Nothing in the prompt can detect that, which is one reason its
  output shape carries the appendix as label lines rather than prose. Worth porting
  the guard to that route.
- **No Quick Check or Summative Assessment prompt**, per the note above.
- **Domains 2 and 3 are a template**, not content. The source document describes
  only General Studies.
