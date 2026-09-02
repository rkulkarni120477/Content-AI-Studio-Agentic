# AIM Blocks V2 prompts

The AIM prompt set, transcribed from `Promts _AIM.docx` (and the DLU Outline from
`Outline_DLU 1.docx`) and adapted to the generation routes in this repo. One file
per component.

## What is here

| File | Component | Route | Status |
|---|---|---|---|
| `AIM_BLOCK_BLUEPRINT_PROMPT.md` | `cdd` — "Blueprint" in the UI | `POST /cdd/generate` and the block-wide digest pipeline | **Live** as DB prompt id 82, v3 |
| `AIM_DLU_OUTLINE_PROMPT.md` | `blueprint` — one Day, selected on the Blueprint page | `POST /blueprints/generate`, legacy single-call path | **Live** as DB prompt id 91, v1 |
| `AIM_TODAYS_MISSION_PROMPT.md` | `generate` | `content_generation` | **Live** as DB prompt id 89, v1 |
| `AIM_LEARN_IT_PROMPT.md` | `generate` | `content_generation` | **Live** as DB prompt id 88, v1 |
| `AIM_DAY_REFLECTION_PROMPT.md` | `generate` | `content_generation` | **Live** as DB prompt id 86, v1 |
| `AIM_UP_NEXT_IN_CLASS_PROMPT.md` | `generate` | `content_generation` | **Live** as DB prompt id 90, v1 |
| `AIM_INSTRUCTOR_MANUAL_PROMPT.md` | `generate` | `content_generation` | **Live** as DB prompt id 87, v1 |
| `AIM_DLU_PRODUCTION_SPEC_PROMPT.md` | `generate` — the whole day, one reply | `content_generation`, `dlu_day` component | **Live** as DB prompt id 97, v1 |
| `AIM_DOMAIN_CONTEXT_BLOCK.md` | — | pasted into Extra instructions | Context layer, not a prompt |
| `AIM_STYLE_GUIDE.md` | — | Style record + Extra instructions | Content artifact, not a prompt |

**Every id in the Status column is a `cas-prod-db` id.** Dev and prod are separate
RDS instances — `cas_dev_db` on `content-ai-studio-dev-rds`, `cas-prod-db` on
`content-ai-studio` — and none of these rows has been loaded into dev, whose
endpoint is reachable only from inside the VPC. So a dev environment resolves the
seeded defaults for every component here, not these prompts. Verified 2026-09-02
by reading both endpoints: all seven AIM rows exist in prod, and each one's stored
system and user text is byte-identical to this directory's fences, except ids 82
and 86, which differ from their files by a single trailing character (similarity
1.000 — a paste artifact, not drift).

The five DLU/manual prompts match the DLU parts the frontend already recognises
(`frontend/src/utils/dluBlueprint.js`): Today's Mission, Learn It, Quick Check, Up
Next, Day Reflection. **Quick Check has no prompt** — the source documents define
it as a downstream output, as a Blueprint targeting field, and as an item-outline
plan inside the DLU Outline, but none of them writes a prompt that produces the
items. Nor does any write one for Summative Assessment.

`AIM_DLU_PRODUCTION_SPEC_PROMPT.md` is the sixth `generate` prompt and the only
one that writes the **whole day in one reply** — every component, every Section,
every screen, with per-screen metadata. It is not an alternative phrasing of the
five: it is the artifact the `dlu_day` component ("Full DLU — Day N: topic",
`blueprint_parser.py:495-506`) actually asks for, and the five stay useful for
regenerating one part of a day that already exists. See **The whole-DLU
production specification** below for what it was transcribed from and what
changed.

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

**`{{course_name}}` renders empty on the `generate` route.** It is declared in
`build_context_variables`' signature, so it is always *supplied* and never trips
the strict-render guard — but the `generate` call site never passes it
(`generation_jobs.py:377-390` passes twelve names, and `course_name` is not one),
and neither does the router that builds the job (`generations.py:160-190`). So it
defaults to `""` and the five day-level prompts' "in {{course_name}}" renders as
"in ". Verified by rendering their fences against the route's real dict. Nothing
breaks, but the Block is silently not named. `AIM_DLU_PRODUCTION_SPEC_PROMPT.md`
therefore declares only variables the call site actually passes — `{{topic}}`,
`{{context_injection}}`, `{{style_guidelines}}`, `{{target_audience}}`,
`{{learning_objectives}}` — and reads the Block identifier out of the supplied
Blueprint the way the DLU Outline does. Worth fixing at the call site for the
other five rather than working around it in five prompts.

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
through three code-injected channels, and every day-level prompt here is written to
read them:

- **`{{context_injection}}`** — the CDD and Blueprint summaries for the pinned
  documents, built by `build_context_injection`.
- **Appended source context** — the selected and linked documents, each wrapped as
  `[START SOURCE: filename] … [END SOURCE: filename]`, appended to the user message
  *after* rendering. For a `dlu_day` component the day's plan is prepended to this
  block as `--- DLU DAY BLUEPRINT ---`.
- **`extra_instructions`** — delivered as `**Additional Instructions:**` at the end
  of the user message. On the `generate` route this field carries the user's own
  direction **and** whichever DIS pack was retrieved, headed `COURSE GENERATION
  CONTEXT FROM DIS SOURCE LIBRARY`, in either the blob shape or the day-scoped
  shape. Adaptation 11 under **The whole-DLU production specification** has the
  three shapes and their exact renderings; the five section prompts predate that
  finding and describe only the `[START SOURCE:]` one.

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

## The whole-DLU production specification

`AIM_DLU_PRODUCTION_SPEC_PROMPT.md` is transcribed from the two Aug 31, 2026
drafts in `drafts/` — a 68-section system prompt and a 23-section user prompt for
the AIM 16-Block DLU generator. Together they describe one generation that
produces a whole day: the document title, the DLU title, the SME Review
Checklist, DLU Information, Requires Human Expertise, Today's Mission, the Learn
It Introduction, every approved Section and screen, the applicable special
treatments and provisional briefs, the Quick Check placeholder, Up Next in Class,
Day Reflection, per-screen metadata, image metadata, direct/support ACS
classification, source traceability and estimated duration — plus a stop-and-validate
sequence of thirteen checks run against the finished draft.

It sits one step below `AIM_DLU_OUTLINE_PROMPT.md` in the same chain: the Block
Blueprint plans the block one row per day, the DLU Outline turns one row into a
day-level design, and this prompt turns that design into the manuscript. That is
why it reads the outline as *approved architecture* and changes it only as a last
resort, with every change recorded under Unresolved Questions.

Ten changes from the drafts, each forced by how this repo generates or by
something the drafts leave unactionable. Each is listed so it can be reversed if
the reasoning stops holding.

1. **The 23 pasted input sections became four supplied channels.** The user
   prompt draft is a paste-and-attach form: `Block number: [ ]`,
   `Calendar file: [attach]`, `ACS source file: [attach]`, `### Source 1 / File:
   [attach]`, and so on through twenty-three headings. Nothing on this route
   attaches a file. The prompt names the four channels that do exist — the
   `--- DLU DAY BLUEPRINT ---` block, `{{context_injection}}`, the appended
   `[START SOURCE: filename]` blocks, and the style/audience fields — and says
   which facts live in each. The draft's per-input instructions survive as
   instructions about what to *find* in those channels, which is why the user
   fence still enumerates the calendar, the authoritative ACS source, the FAA
   pages, the other approved sources, both mappings, the project and hangar
   documentation, the style guide, the checklist and the image inventory
   one by one. The draft's own rule — identify a named-but-missing source rather
   than substituting general knowledge — carries the whole burden the `[attach]`
   markers used to carry.

2. **Word output became markdown, and the Word rendering rules were dropped.**
   The route stores the reply as the deliverable, so §53–66 of the draft — point
   sizes, font, heading colour, table borders, page setup, the `X + 2Y` spacing
   arithmetic — has nothing to act on: a model returning markdown cannot set a
   typeface or a margin, and text of that kind in a fence is either inert or gets
   printed into the document as though it were content. Only the *hierarchy*
   those sections encode is actionable, and it is in the prompt's `OUTPUT SHAPE`
   as literal markdown levels: `#` for the two-line document title, `##` for the
   DLU title, `###` for an A-head in FULL CAPS (title case marks an
   administrative heading at the same level), `####` for a Learn It Section,
   `#####` for `Screen: [Title]`. Screens stay unnumbered, screen type stays in
   the metadata rather than in a heading, and the reusable template's example
   Section names stay out of a real DLU. Everything else in those sections
   belongs to whoever builds the Word or Canvas artifact downstream, and is not
   carried here: **this directory holds prompts, and a prompt is the only thing
   in it that reaches production.** An earlier draft of this work kept the
   rendering rules in a companion file. That was wrong on its own terms — nothing
   in this repo consumes them, they cannot affect a generation, and the file made
   a document-formatting appendix look like part of the prompt set. The drafts
   remain the record for anyone building that artifact, and they stay outside the
   repo.

3. **The artifact is renamed, and the banned word is never spelled.**
   `generation_jobs.py:483` runs `re.sub(r'(?i)\bstoryboard\b', '', out)` over
   every reply, so a document titled after the drafts would be delivered with a
   hole in its title line. The artifact is a *production specification*
   throughout, matching `AIM_STYLE_GUIDE.md`'s Part A. The prompt states the rule
   by describing the word rather than printing it, in both fences, so the prompt
   text itself cannot be the thing the model echoes. "Canvas" and "Storyline"
   stay — both are live build treatments and neither is scrubbed.

4. **Citations were reconciled with the code-injected instruction.**
   `generation_jobs.py:344-347` appends "cite it as [Source: filename]. At the end
   of EACH block, include a 'Sources Used' list" to the system prompt, which
   collides with the draft's "Do not clutter learner-facing copy with citations
   after individual sentences." The prompt resolves it explicitly: traceability
   lives in the `Content Source` and `Image Source` metadata fields and in a
   final `### Sources Used` section, and learner-facing prose stays clean. Both
   requirements are then satisfied and the model is not left choosing between two
   instructions it was handed in the same message.

5. **Images are specified, not inserted.** The draft's `Image(s)` field says
   "Insert the actual selected image when available", and its process has the
   model inspect figures, captions and labels directly. Source material reaches
   this route as extracted text — no image is ever passed in and no image can be
   returned. So the prompt has the model write a *locator* (document, volume,
   page, figure number, original figure title) into `Image(s)`, complete the
   remaining image metadata, flag `REQUIRES_ID_JUDGMENT` where the supplied text
   describes a figure only by caption or reference, and record a needed-but-
   unavailable image under Still Needs to Be Done. Every other image rule —
   functional use only, narrowest useful figure, crop and callout
   recommendations, alt text, the image–text relationship — is unaffected and
   kept verbatim in substance.

6. **Requires Human Expertise kept its two buckets; the flag names come from this
   directory.** The drafts route everything to Unresolved Questions or Still
   Needs to Be Done and never name a flag. The other prompts here share one
   closed vocabulary (`MISSING_SOURCE`, `REVIEW NEEDED`, `REQUIRES_ID_JUDGMENT`,
   `CONFLICTING_SOURCES`, `SOURCE_VERSION_CONFLICT`). Both are preserved: the
   buckets are the output structure, and each line inside them carries a flag
   from the vocabulary plus the screen or field it applies to. Without that, a
   day generated by this prompt and a day regenerated part-by-part with the five
   section prompts would report the same problem in two different languages.

7. **A missing authoritative source is a flag, not a fallback.** The draft
   insists every ACS check runs against the authoritative ACS source and never
   against the calendar, the Blueprint, the outline or model memory — but it
   assumes that source is attached. Here it may simply not be among the source
   blocks. The prompt records the assigned codes as given, marks the validation
   as not performed, and flags `MISSING_SOURCE`, rather than quietly validating
   from a downstream artifact. The same rule shape applies to the Master Mechanic
   Moment and previous-media mappings, where "no mapping document supplied" is
   distinguished from "mapped: none" — the draft's `N/A` means the second, so
   reading absence as `N/A` would launder a missing input into a decision.

8. **Neighbouring days are read where they exist.** The draft's alignment check
   inspects preceding and following days. On this route they arrive only if the
   block-level context carries the day-by-day map — which it usually does, since
   `extract_cdd_summary` falls back to the CDD's full content for a worksheet
   document. The prompt says to read them out of the map where present and not to
   assume them otherwise.

9. **The A-head/B-head split survives as a caps test.** The draft separates
   28 pt FULL CAPS component headings from 20 pt black administrative subheads.
   Markdown has no point sizes, so both land on `###` and are told apart by case:
   an A-head is FULL CAPS and comes from a closed set of five, everything else at
   that level is administrative and title case. That keeps the mapping in the
   design spec deterministic in both directions. Screen numbers stay banned, the
   eleven screen types stay a closed list, and the reusable template's example
   Section names ("Regular Screens", "Irregular Screens") are explicitly
   forbidden in a real DLU, as the draft requires.

10. **The provisional list is preserved as a rule, not as a footnote.** The
    draft's §68 names ten unsettled matters. The prompt carries them as a
    `PROVISIONAL RULES` block with the instruction not to harden any of them into
    policy — including the item the draft calls "CAS-specific output-marker or
    technical implementation requirements", which is this application. Every
    section the draft marks provisional (interactive, job aid, Master Mechanic
    Moment, Explore/Previous Media, Key Term, Safety Reminder) says so in its own
    text too, for the same reason the governing rules are restated in every file:
    unconditional beats DRY when the alternative is a rule that quietly vanishes.

11. **Retrieved source material arrives in three shapes, and one of them arrives
    in the wrong field.** The draft assumes attachments. This route assembles
    source material three ways: whole documents wrapped as
    `[START SOURCE: filename]` and appended after the user message
    (`generation_jobs.py:236-272`); a DIS blob pack whose units carry
    `Source: <filename>` / `Title:` / text / optional `Visual Summary:`
    (`context_retrieval.py:1305-1314`); and, when the request pins a block and a
    day and the digest pipeline is on for the client, a day-scoped bundle
    rendering `DAY SOURCE UNITS` and `RELATED HANDBOOK PAGES` as
    `• [unit_type] Title` bullets with **no filename at all**
    (`dis_day_context.py:42-84`). Both DIS shapes are appended to
    `extra_instructions` by `generations.py:128-165`, which
    `generation_jobs.py:443-444` then delivers under `**Additional
    Instructions:**` — so on this route retrieved *source material* is handed to
    the model in the field that otherwise carries *direction*. The prompt
    describes all three shapes, says a heading of that name inside Additional
    Instructions is source material rather than direction, identifies a source by
    filename where one is given and by unit title where none is (the same rule
    the DLU Outline prompt uses, and the only rule satisfiable under the
    day-scoped shape), treats a `(restricted — title only)` unit as no evidence,
    and reads a `Visual Summary` as evidence about a figure — which is the one
    channel that says anything at all about what a source page depicts, and so
    the only support the image-specification rules have. It also carries the
    code's own "do not expose internal DIS metadata" instruction forward as a
    concrete list of what not to print.

12. **Two rules were added that the drafts do not contain.** Both are in the
    fences because the pipeline forces them, and both are listed here so they can
    be reversed if the pipeline changes. First, **every list must be flat**: the
    2-or-more-space collapse at `generation_jobs.py:484` re-levels a nested bullet
    as a sibling of its own parent, so the prompt forbids sub-items and points at
    a table or a lead-in sentence instead. Second, **an absent day plan is a
    declared degradation**: because the `dlu_day` normalisation lives only in the
    frontend, the plan block can be missing with no error, so the prompt builds
    from the Blueprint row, records every screen title, Section boundary and SME
    scenario it had to originate as its own proposal under Unresolved Questions,
    and flags the missing outline rather than presenting an invented architecture
    as approved. Both are in **Known gaps** with the code that forces them.

Nothing else was dropped. The 45-minute hard maximum and the whole time budget,
the two global instructional principles and their test questions, the pacing
arithmetic (150 words ≈ 1 minute, image +1, table +1, no unbroken prose block over
~100 words), the five interactive templates, the eleven screen types, the fifteen
screen-metadata fields, the SME checklist's fifteen coverage points, the
thirteen final validation checks, the per-component duration targets and
the style rules are all in the fences.

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
section default. Explicit selection is immune; a default is not. The whole-DLU
component carries the same hazard: for a DLU course the dropdown is built by
`_list_dlu_day_components` (`blueprints.py:1701-1745`), which labels it
`Day 11: <topic>` exactly as above; `parse_blueprint_components` is the fallback
for a DLU blueprint with no course, and labels it `Full DLU — Day 11: <topic>`
(`blueprint_parser.py:495-501`). Either way the same substring test reads the same
topic.

## Known gaps

- **Nothing here has been generated with yet.** Every check so far is static:
  variables render, the format parses, the Blueprint reconciles 32/32. For the DLU
  Outline: its six declared variables render against the blueprint route's real
  dict, its output shape parses into Overview + the five parts through the actual
  `dluBlueprint.js` (and survives a splice round trip), and it trips neither
  `reject_if_unsatisfiable` nor `context_was_dropped`. It **is** in the DB now —
  prompt id 91, v1, registered the way prompt 82 is (`prompt_kind` `pipeline`,
  `is_default` False, `project_id` NULL, no variant, and no `PromptVariable`
  declarations, so what gets enforced is `blueprint_generation`'s registry pair,
  `cdd_context` + `selected_module`), differing only in `component_type`, which is
  `blueprint`. Verified after loading: it appears in
  `list_prompts_by_component(db, "blueprint")`, `build_prompt(..., prompt_id=91)`
  resolves and renders with no placeholder left behind, and the stored text is
  byte-identical to this file's fences. Being `is_default` False it changes no
  generation until someone picks it in the Prompt Template dropdown.
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
- **The `generate` route has no truncation guard either**, and the whole-DLU
  production specification is the largest artifact anything here asks a model to
  produce in one reply. `generation_jobs.py:465` checks only
  `_llm_result.is_error`; the response's `stop_reason` is captured
  (`llm_client.py:47-65` collects exactly the `length` / `max_tokens` values that
  mean "cut off") and then never read on this path, so a document truncated at the
  output cap is stored, CE-validated and shown as a completed generation. Nothing
  in a prompt can detect this. Until the guard is ported, generate this artifact
  on a high-output-cap model and check that the reply ends with `### Sources Used`.
- **CE validation rewrites the reply after the prompt's own style pass.**
  `generation_jobs.py:496-504` runs `run_ce_validation`, which re-prompts a model
  to auto-fix the output against the `CE_Checklist` style record or, failing that,
  the active style's Writing Rules. So the stored document is not byte-identical
  to what this prompt produced, and a checklist rule that contradicts the draft's
  style rules will win silently. Worth reading the pinned `CE_Checklist` before
  blaming the prompt for a style regression. The same stage is also where the
  `storyboard` scrub and a double-space collapse (`generation_jobs.py:483-485`)
  have already run.
- **Not yet generated with.** Every check on the production-specification prompt
  is static: both fences render against the route's real variable dict with no
  placeholder left behind and no undeclared variable; the five declared variables
  are all supplied at the call site; the file is ASCII with exactly two `text`
  fences; `legacy_blockers` returns empty on the rendered pair; and the document
  title survives the `storyboard` regex intact. **It is in prod but has never run
  a generation.** Loaded 2026-09-02 as prompt id 97, v1, registered the way prompt
  91 is: `component_type` `generate`, `prompt_kind` `pipeline`, `is_default` False,
  `project_id` NULL, no variant, `visibility` `draft`, one v1 version with
  `workflow_state` `active` and `is_active` True, and no `PromptVariable`
  declarations — so what gets enforced is `content_generation`'s registry pair,
  `topic` + `learning_objectives` (`prompt_loader.py:137-147`), both of which this
  file declares and the route supplies. Verified after loading: it appears in
  `list_prompts_by_component(db, "generate")`, `build_prompt_resolved(...,
  prompt_id=97)` resolves and renders with no placeholder left behind, the stored
  text is byte-identical to this file's fences, and `default_generate_prompt`
  (id 6) is still the only `generate` default. Being `is_default` False it changes
  no generation until someone picks it in the Prompt Template dropdown.

  **The `component_type` must be `generate`, not `content_generation`** — the
  latter is the registry *stem* name, which `_STEM_COMPONENT` maps to the component
  `generate` (`prompt_loader.py:61-66`), and the Generate page's dropdown keeps a
  row only when `!p.component_type || p.component_type === 'generate'`
  (`InlinePromptControls.jsx:120`), so a row filed under the stem name is invisible
  in the picker. That is the one mistake to avoid when loading any of these into
  another environment.
- **Nothing here is loaded into dev.** Dev and prod are separate RDS instances and
  the dev endpoint resolves only inside the VPC, so it times out from a developer
  machine. Every id in the Status table is a prod id; a dev environment falls back
  to the seeded component defaults. Loading these into dev has to run from inside
  the VPC.
- **The output cap is 16,384 tokens and this artifact is the one that will hit
  it.** `DEFAULT_MAX_OUTPUT_TOKENS = 16384` (`llm_client.py:43`) is hardcoded, not
  env-driven, and the generate path never passes a `max_tokens` override, so it
  cannot be raised per generation. A whole DLU costs roughly 6,000–8,000 tokens of
  learner-facing prose (30–45 minutes at ~150 words per minute) plus ~3,400 for
  fifteen screens of fifteen metadata fields plus the checklist, DLU Information,
  the briefs, the TOC and Sources Used: about 11,000–13,000 tokens for a typical
  day, and over the cap for a rich one carrying both briefs, a Master Mechanic
  Moment, an Explore asset and many images. With no `stop_reason` check on this
  path (above), that lands as a silently truncated document — which is exactly
  what this project's no-silent-truncation rule exists to prevent. Raising the
  constant, or passing a model-aware cap on this path, is the fix; splitting the
  artifact is not, since the drafts require one document.
- **CE validation judges the first 8,000 characters and rewrites all of them.**
  `ce_validation_service.py:144` validates `content[:8000]` — on a whole DLU that
  is roughly the title, checklist, DLU Information and Requires Human Expertise,
  i.e. the administrative front matter, so a style checklist is applied to almost
  none of the learner-facing prose. If it then reports any issue,
  `ce_validation_service.py:170-176` re-prompts with the **full** content under a
  generic "fix the content" system prompt that knows none of the DLU rules, and
  the reply replaces the document — subject to the same 16,384-token cap, so the
  rewrite can truncate a document that was complete. `_fetch_ce_checklist` also
  falls back to an unscoped registry search for any active document whose name
  contains `CE_Checklist` (`ce_validation_service.py:44-50`), so the rewrite can
  be driven by a checklist belonging to another project. For this artifact the
  stage is a net risk: check what `CE_Checklist` resolves to before running it.
- **Nested lists do not survive delivery.** `generation_jobs.py:484` collapses
  every run of two or more spaces to one, so a 2-space and a 4-space list indent
  both become 1 space and every sub-item is re-levelled as a sibling of its
  parent. Verified by running the three substitutions over a representative
  document: markdown tables survive intact (padding collapses harmlessly, the
  alignment row keeps its dashes), flat `- [ ]` checklists survive, nesting does
  not. The prompt therefore requires every list to be flat and says why in one
  line, and the `OUTPUT SHAPE` uses only tables and flat lists.
- **The `dlu_day` component contract is enforced in the frontend only.** The
  day-plan prepend is gated on `selected_component["value"] == "dlu_day"`
  (`generation_jobs.py:286`), an exact compare. `_list_dlu_day_components` emits
  `dlu_day::<blueprint_id>` (`blueprints.py:1737`) and nothing in the backend
  parses that suffix; `GeneratePage.jsx:264-273` is what normalises it back to
  `dlu_day` and moves the id into `blueprint_id`. Through the UI this is correct
  and the day plan arrives. An API client that posts the component value it was
  handed by `GET /blueprints/{id}/components` gets no day plan, no error, and a
  DLU written without its approved outline. The prompt now detects that case and
  degrades explicitly — it builds from the Blueprint row, records every screen
  title and scenario it originated as its own proposal, and flags the missing
  outline — but the backend should accept the `::<id>` form rather than rely on
  one caller to normalise it.
- **No Quick Check or Summative Assessment prompt**, per the note above.
- **Domains 2 and 3 are a template**, not content. The source document describes
  only General Studies.
