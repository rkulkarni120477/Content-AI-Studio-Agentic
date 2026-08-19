# AIM Style Guide

> **Copy the fenced blocks only.** Everything outside a fenced block on this page
> is documentation for whoever maintains this repo. The model never sees this
> repository, so a filename or a path inside prompt text would be meaningless to
> it at generation time. Nothing in this directory is read from disk at runtime.

This is **not** a generation prompt and deliberately carries no System/User pair.
The app's `style` component prompt (`style_understanding` / `style_analysis`)
*derives* a style from reference documents; the AIM "Style Creation" section is not
that. It is the style itself — the standing guidance every other AIM prompt in this
directory declares itself to operate within ("the Course Style Guide already
governing this project — apply them; do not restate them").

The source section packages three different things under one heading, on three
different lifetimes, so it is split here into two artifacts:

| Part | What it is | Where it goes | Lifetime |
|---|---|---|---|
| **A — Project style guide** | Audience, reading level, citation standard, source hierarchy, subject strategy, standing rules, prohibitions | A **Style** record, pinned to the AIM project. Reaches generation as `{{style_guidelines}}` and as the "ACTIVE INSTRUCTIONAL STYLE" block. | Project-wide, every Block |
| **B — Block standing data** | Missing Master Mechanic Moments, knowledge-test miss rates, the glossary, day mappings | The **Extra instructions** field, alongside the domain block. Reaches the Blueprint route as `{{extra_instructions}}`, which the Block Blueprint prompt reads under BLOCK STANDING DATA. | One per Block |

**Why the split matters.** In the source document, Block 2's missing S-codes, its
AKTR miss-rate table, its day-number mappings and its glossary sit inside the
project-wide style guide. Pinned as a Style, those Block 2 facts would be injected
into every generation for Blocks 1, 3 and 4 — and every AIM prompt in this
directory forbids carrying a fact from one Block to another. Part B keeps them
where they belong, and the Blueprint prompt was already written to consume exactly
this input: *"its glossary, its SME review notes, its performance data … are given
in BLOCK STANDING DATA."*

**One wording change from the source.** The audience list says "Storyline
storyboards: developer-facing". That artifact is called a *production
specification* throughout the Learn It prompt, because the generation pipeline
strips the word "storyboard" from delivered output. Part A uses the same name.

---

## Part A — Project style guide

Paste into the Style record's guidelines. Applies to every Block and every domain.

```text
AIM BLOCKS V2 — COURSE STYLE GUIDE

Apply this style guide to every content-development session across the AIM Blocks
V2 Curriculum Transformation project, in addition to the Master System Prompt
(MSP-3.0) and any active domain-level context.

OVERARCHING GOAL
Produce client-ready, FAA-aligned instructional content for the AIM Aviation
Maintenance Technician (AMT) program: accurate, instructionally sound, and ready
for SME review rather than a draft needing substantial rewrite. Every output should
lead to validation and targeted correction on review, never to reconstruction.

TARGET AUDIENCE
AMT students are adult learners in a vocational training environment. Many are
career-changers or recent high school graduates with varied academic backgrounds.
Content reads as clear, direct technical communication - not simplified, not
academic. Write the way a skilled AMT explains something to a colleague who is new
to the task: precise, practical, no unnecessary complexity.

Secondary audiences, by output type:
  Instructor manuals        experienced aviation professionals facilitating, not
                            learning. Tone shifts to direct, operational, and
                            facilitation-oriented.
  Assessment items          neutral, unambiguous, standard item-construction
                            register. No trick questions.
  Production specifications developer-facing. Clear, complete, sequential, no
                            ambiguity.

READING LEVEL
Target a middle-school reading level, Flesch-Kincaid grade 8-9.
  - Sentences averaging 12-18 words.
  - One main idea per sentence.
  - Long procedures broken into numbered steps.
  - Bullets for lists of three or more items.
  - Paragraphs of two to four sentences.
  - Active voice preferred.
  - Direct verbs: identify, compare, inspect, select, calculate, verify, apply.
  - Retain required FAA and ACS terminology exactly. Never simplify or substitute
    regulatory language to hit the reading-level target.
  - Define technical terms and abbreviations at first use, as in "Aviation
    Maintenance Technician (AMT)".
  - Use the same term consistently throughout. Do not vary vocabulary for style.
  - US English throughout; no British spelling substitutions.

CITATION AND SOURCE STANDARD
APA 7th edition for all source citation. For FAA handbook citations, follow this
pattern within the APA structure:
  (FAA-H-8083-[volume number], p. [page number])
  Example: (FAA-H-8083-31B, p. 4-12)
Every factual claim must be traceable to a specific page of a supplied FAA handbook
or approved source. Do not draw on outside or general knowledge. Confidence is not
a citation. Being vague when a specific fact IS present in the source is as serious
as fabrication: use the literal value, code, filename, or percentage from the
source.

MATERIALS TO BE FOLLOWED
  Primary authority   the FAA-H-8083 handbook series.
  Secondary           the Airman Certification Standards (ACS).
  Supporting          course calendars, block syllabi, instructor slide decks,
                      teacher guides, existing quiz and exam items, hangar activity
                      documentation, student handouts, and exam questions - as
                      supplied per session.
Where sources conflict, the FAA-H-8083 handbook is always the authority. Flag the
conflict; never resolve it silently.

SUBJECT MATTER STRATEGY
Content reflects how the subject matter is actually used in aviation maintenance
practice, not treated as an isolated academic topic. Where a subject area has a
natural technician workflow - interpreting information, selecting resources,
performing the task, protecting the result - structure the instruction around that
workflow rather than a flat topic list.

General sequencing principle, across all subjects:
  Identify -> Describe -> Compare -> Interpret -> Select -> Apply -> Inspect ->
  Judge
Move learners from basic recognition toward interpretation, application, and
judgment, not recall alone.

Subjects requiring applied rather than theoretical treatment - mathematics,
physics, calculations: ground every formula or principle in an actual aviation
maintenance task, show a worked example before independent practice, and use the
exact units and formulas found in the source material.

Visual-technical subjects - drawings, schematics, materials, corrosion, defects:
pair every image with a task, whether to compare, trace, identify, select,
sequence, or judge. Never present a visual for passive viewing.

Misconception-based instruction: where source material identifies a common error or
unsafe practice, surface it explicitly rather than only teaching the correct
procedure.

SME-INFORMED SUBJECT STRATEGY - STANDING RULE
Wherever SME review notes are supplied for a Block, treat them as binding
instructional guidance, not optional colour, and apply all of the following
automatically.
  High-attention concepts   Give extra instructional depth and practice repetition
                            to concepts the SME flags as high-difficulty or
                            high-stakes for certification.
  Known misconceptions      Where the SME names a specific misconception, address
                            it directly in the Learn It content and in Quick Check
                            distractor design. Do not leave it to be inferred.
  Certification alignment   For subjects the SME identifies as rote or
                            knowledge-test-heavy, build Quick Checks and quizzes
                            around authentic FAA knowledge-test question patterns
                            and phrasing, not generic recall questions.
  Prior-block overlap       Where the SME flags overlap with an earlier Block,
                            treat the later occurrence as reinforcement and deeper
                            application rather than a first introduction, and allow
                            the additional depth the SME indicates.
  Physics and safety        Where a concept has an underlying physical principle
                            tied to safety, teach the underlying reason, not the
                            procedure alone - without fear-based framing unless the
                            SME specifically endorses it.
  External resources        Where the SME names a specific existing resource, flag
                            it for ID to evaluate for integration rather than
                            ignoring it or independently sourcing a replacement.
  Pilot selection           Where the SME recommends specific DLUs for pilot
                            testing based on hands-on and theory integration, treat
                            that as the default starting point for pilot scope.

MASTER MECHANIC MOMENTS - STANDING RULE
A Skill-type ACS code (S-suffix) cannot be fully delivered through DLU content
alone. It requires a Master Mechanic Moment or an equivalent physical demonstration
and practice component, and that must be stated explicitly rather than silently
assumed to be covered by Learn It.

For every Skill-type ACS code in a day's calendar entry:
1. Confirm whether a Master Mechanic Moment exists for that code in the supplied
   source material.
2. Where one does, reference it in the output and describe how it delivers the
   skill - demonstration, guided practice, or independent assessment.
3. Where none does, do not claim the DLU delivers the Skill code in full. State
   explicitly: "This output delivers the conceptual/procedural foundation for [ACS
   code]; physical skill demonstration is carried by [hangar activity /
   instructor-led session / Master Mechanic Moment - TBD]." Flag
   REQUIRES_ID_JUDGMENT where no clear downstream delivery point is identified in
   the source material.

PERFORMANCE DATA - STANDING RULE
Where knowledge-test miss-rate data is supplied for the Block:
  - A code with a miss rate above 30% signals that prior instruction has not been
    landing. Its DLU must go deeper than a standard slide-deck treatment and must
    not be treated as a simple recall topic. Flag it HIGH-MISS with its percentage
    and target it at APPLY or ANALYZE level in Quick Check design, regardless of
    its otherwise-assigned concept type.
  - A code between 20% and 30% is meaningfully elevated relative to a
    well-performing code. Treat it as APPLY-minimum rather than RECALL, even where
    the Blueprint's default Quick Check priority would suggest RECALL.
  - For every high-miss code, build distractors against the specific misconception
    pattern the miss rate reveals, where that pattern is known, rather than generic
    wrong answers. The goal is to close the actual gap, not to test recognition.
Where no performance data is supplied for the Block, say so explicitly. Never infer
difficulty from the topic alone.

GLOSSARY - STANDING RULE
Maintain and apply the Block's glossary across all output. Cross-verify every
definition against the FAA-H-8083 handbook series and ACS terminology before final
use. The glossary grows per Block as new terms are introduced. Add new terms;
never overwrite or alter an existing entry when adding one.

WHAT NOT TO DO
  - No motivational language not grounded in source material.
  - No generic safety disclaimer not found in the handbook.
  - No transitional summary added purely for stylistic flow.
  - No invented learning objective. Mark every derived objective "DERIVED -
    requires ID and SME confirmation before use."
  - No filled gap where source material is missing. Flag it per the review flag
    vocabulary instead.
  - No fear-based safety framing, including PPE scare tactics, unless the SME
    specifically endorses it for that subject.
  - No claim of full coverage of a Skill-type ACS code without an identified Master
    Mechanic Moment or an explicit partial-delivery statement.
```

---

## Part B — Block standing data

One per Block, pasted into **Extra instructions**. Block 2 is filled in from the
source document; the headings below are the template for every other Block.

```text
BLOCK STANDING DATA — BLOCK 2: AIRCRAFT DRAWINGS, MATERIALS AND PROCESSES,
CLEANING AND CORROSION CONTROL

SKILL CODES WITH NO MASTER MECHANIC MOMENT ON FILE
Confirmed missing per SME review. Treat each as open until resolved, and do not
treat any of them as fully addressed by a DLU's Learn It or Quick Check content.
Each must show either an integrated Master Mechanic Moment or an explicit
partial-delivery statement.
  AM.I.E.S6  Aircraft Materials, Hardware, and Processes - make precision
             measurements with an instrument that has a Vernier scale.
  AM.I.G.S1  Cleaning and Corrosion Control - perform a portion of an aircraft
             corrosion inspection.
  AM.I.G.S3  Cleaning and Corrosion Control - apply corrosion prevention and
             coating materials.

KNOWLEDGE-TEST PERFORMANCE DATA (AKTR MISSED-CODE DATA)
ACS codes ranked by student miss rate on the FAA knowledge test for this Block.
Apply the performance-data standing rule in the style guide to every one of them.
| Rank | Miss Rate | ACS Code | Task Description |
|---|---|---|---|
| 1 | 79.6% | AM.I.B.K1 | Drawings, blueprints, sketches, charts, graphs, and system schematics, including commonly used lines, symbols, and terminology |
| 2 | 54.7% | AM.I.E.K6 | Precision measurement tools, principles, and procedures |
| 3 | 35.6% | AM.I.G.K2 | Corrosion theory and causation |
| 4 | 34.6% | AM.I.G.K3 | Types and effects of corrosion |
| 5 | 29.4% | AM.I.E.K4 | Hardware commonly used in aircraft (bolts, nuts, screws, pins, washers, turnlock fasteners, cables, cable fittings, rigid line couplings) |
| 6 | 27.6% | AM.I.G.K7 | Corrosion removal and treatment procedures |
| 7 | 27.6% | AM.I.E.K2 | Heat treatment and metalworking processes |
| 8 | 21.0% | AM.I.E.K12 | Characteristics of acceptable welds |
| 9 | 20.3% | AM.I.B.K2 | Repair or alteration of an aircraft system or component(s) using drawings, blueprints, or system schematics to determine whether it conforms to type design |
| 10 | 20.0% | AM.I.B.K3 | Inspection of an aircraft system or component(s) using drawings, blueprints, or system schematics |

Codes 1 to 4 exceed the 30% threshold and are APPLY/ANALYZE priority in Quick Check
design without exception. Codes 5 to 10 are APPLY-minimum.

HIGH-MISS CODES BY DAY
  AM.I.B.K1              Days 1, 2, 3. The highest-miss code in the Block; Learn It
                         on these days must go beyond slide-deck depth, with
                         additional worked examples in reading drawings, symbols,
                         and schematics.
  AM.I.E.K6              Day 6, Layout and Measuring Tools. The 54.7% miss rate
                         independently corroborates the SME's flag that this day is
                         dense and may need extended time, and makes it the
                         second-highest priority in the Block.
  AM.I.G.K2, AM.I.G.K3   Day 11, corrosion theory, types, and causation.
  AM.I.E.K4              Days 9 and 10, Aircraft Hardware.
  AM.I.G.K7              Day 12, Corrosion Removal and Treatment.
  AM.I.E.K2              Day 8, Heat Treatment and Metalworking.
  AM.I.E.K12             Day 10, welding - acceptable weld characteristics.
  AM.I.B.K2, AM.I.B.K3   Days 2 and 3, drawing-based repair and inspection
                         determination.

INSTRUCTIONAL-REPAIR PRIORITY
AM.I.B.K1 alone carries the single largest miss rate in the Block, 79.6%, and spans
three consecutive days. Days 1 to 3 therefore carry the highest instructional-repair
priority in this Block. ID should consider whether the current Day 1-3 sequencing
and depth are sufficient, or whether additional worked practice or interactives are
warranted beyond the standard DLU template.

BLOCK GLOSSARY
Every definition is cross-verified against the FAA-H-8083 handbook series and ACS
terminology. Add new terms as they are introduced; never overwrite an entry.
  Applicability          The determination of whether a given drawing, part, or
                         procedure is valid for a specific aircraft model, series,
                         and serial-number range.
  Bill of Material (BOM) A structured list on a drawing identifying every part,
                         quantity, and specification needed to complete the
                         depicted assembly.
  Clamping Force         The compressive force a fastener exerts on the materials
                         it joins, produced by properly applied torque.
  Conversion Coating     A chemical treatment that changes a metal's surface into a
                         corrosion-resistant compound, such as chemical film or
                         Alodine on aluminium.
  Corrosion              The deterioration of a metal caused by chemical or
                         electrochemical reaction with its environment.
  Effectivity            The specific aircraft, model, or serial-number range to
                         which a drawing, part, or revision applies.
  Exploded View          A drawing style showing the components of an assembly
                         separated to reveal their relative position and order of
                         assembly.
  Galvanic Corrosion     Corrosion that occurs when two dissimilar metals are in
                         contact in the presence of an electrolyte, causing one
                         metal to corrode preferentially.
  Nonferrous Metal       A metal that contains no significant iron content, such as
                         aluminium, titanium, or magnesium.
  Personal Protective Equipment (PPE)
                         Equipment worn to minimise exposure to workplace hazards,
                         including eye, hand, respiratory, and hearing protection.
  Preventive Maintenance Scheduled maintenance action performed to prevent failure
                         or deterioration rather than to correct an existing
                         defect.
  Revision Control       The system of tracking and documenting changes made to a
                         drawing over time, to ensure only the current, approved
                         version is used.
  Safety Data Sheet (SDS)
                         A standardised document providing hazard, handling, and
                         disposal information for a chemical product.
  Safety Wiring          A method of securing fasteners with wire to prevent them
                         loosening due to vibration.
  Serial-Number Range    The specific span of aircraft serial numbers to which a
                         given drawing, part, or ACS task applies.
  Title Block            The section of a drawing containing identifying
                         information: drawing number, revision, scale, date, and
                         approval signatures.
  Torque                 A rotational force applied to a fastener, measured to
                         achieve a specified clamping force without over-stressing
                         the fastener or the joined material.
  Vernier Scale          A secondary graduated scale on a measuring instrument such
                         as callipers, allowing readings more precise than the
                         primary scale alone.

INSTRUCTIONAL MODEL
[The Block's own instructional model and its named stages. The Blueprint prompt
records each day's stage in the Instructional Model Stage column and will read
"REVIEW NEEDED - no source available" for every day where this is absent.]

SME REVIEW NOTES
[Paste the SME review notes for this Block, or state "none provided". The style
guide's SME standing rule treats whatever appears here as binding.]

SOURCE AVAILABILITY CONFIRMED FOR THIS BLOCK
[Per source type: available, partially available, or not supplied. Anything not
stated here is flagged MISSING_SOURCE at the point of use, never assumed present.]
```
