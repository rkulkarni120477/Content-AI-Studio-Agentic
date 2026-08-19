# Domain Context Block

> **Copy the fenced blocks only.** Everything outside a fenced block on this page
> is documentation for whoever maintains this repo. The model never sees this
> repository, so a filename or a path inside prompt text would be meaningless to
> it at generation time. Nothing in this directory is read from disk at runtime.

This is **not** a generation prompt and deliberately carries no System/User pair.
The AIM Domain/Category prompt has no output of its own — its own user-prompt block
ends with "Output requested: [DLU / Instructor Manual / Quick Check / Summative
Assessment / Storyline Storyboard]", which is to say the deliverable always belongs
to some other prompt. It is a standing context layer that every other AIM prompt in
this directory already declares itself to operate within.

So it is authored here as a block of standing data, to be pasted into the
**Extra instructions** field of the run:

- On the **Blueprint (CDD) route** it lands in `{{extra_instructions}}`, which
  the Block Blueprint prompt reads under the heading BLOCK STANDING DATA and
  is instructed to treat as given.
- On the **Generate route** it is appended to the user message under
  **Additional Instructions:**, ahead of the retrieved source documents.

It carries no `{{variables}}`: no route supplies a domain identifier, and there are
only three domains, so each is written out once rather than templated per run. The
square-bracket fills below are filled in by hand when the block is set up for a
domain, not at generation time.

---

## Domain 1 — General Studies (Group 1, Blocks 1–4)

Complete. Every fact below is stated in the source document.

```text
DOMAIN CONTEXT — apply in addition to the Master System Prompt (MSP-3.0) and the
Course Style Guide already governing this project.

You are producing content for the GENERAL STUDIES domain, Group 1, Blocks 1-4, of
the AIM Blocks V2 Curriculum Transformation project.

DOMAIN SCOPE
This domain covers Blocks 1-4 and is the foundational domain of the curriculum. It
is the first of three domains and has no prerequisite domain. Total DLUs: 80, being
4 Blocks of 20 DLUs each.

SOURCE CONTENT AVAILABLE FOR THIS DOMAIN
These are the source materials provided for Blocks 1-4, and what each one is for.
This states what has been provided for the domain; it does not assert that a given
file has been retrieved for the day or Block you are working on. Verify that at the
point of use.
  Course syllabus and calendar  Available for all Blocks. Use it to establish the
                                blueprint, verify the course sequence, and confirm
                                the schedule.
  Instructional content         Available for all Blocks. This is the primary
                                source of instructional material.
  Hangar activities             Available for all Blocks. Use them to design
                                practical tasks and workplace applications.
  Quizzes                       Available for all Blocks except one. On the Block
                                whose quiz is missing, flag MISSING_SOURCE. Do not
                                generate a replacement without ID confirmation.
  Study questions               Available for all Blocks. Use them as candidate
                                assessment items and as distractor sources. They
                                are never final assessment content without review.
  Projects                      Available for all Blocks. Use them to design
                                applied skills and performance expectations.
  Instructor guides             Coverage varies across the domain: available for
                                some Blocks, partial for others, unavailable for
                                the rest. Where a guide or a required section of
                                one is missing, flag MISSING_SOURCE. Never
                                construct instructor guidance from general
                                knowledge.
  ACS codes                     Available for all Blocks. Use them to align course
                                content and assessment items.

DOMAIN-LEVEL HANDLING RULES
1. Where source coverage is complete - syllabus, content, hangar activities, study
   questions, projects, and ACS codes all present - produce content according to
   the Master System Prompt.
2. Where quizzes or instructor guides are incomplete or unavailable, do not
   generate or assume the missing content. Flag MISSING_SOURCE and route the issue
   to the instructional designer for confirmation before producing the affected
   output.
3. Do not assume that a source gap identified in one Block also exists in another.
   Verify source availability at the point of use, for each Block and each day.

DOMAIN QUALITY GATE — confirm all four before any output in this domain goes for
ID sign-off:
  - every ACS code associated with the relevant day or Block is accounted for;
  - missing quiz or instructor guide content is flagged and has not been
    fabricated;
  - all content is traceable to the supplied syllabus, instructional content, or
    handbook, and none of it rests on general knowledge;
  - no unresolved REVIEW NEEDED flag remains.
```

---

## Domains 2 and 3 — template

The source document supplies only Domain 1. It establishes that there are three
domains and that General Studies is the first with no prerequisite; it says nothing
else about the other two. Fill the bracketed fields from the AIM curriculum
structure and have ID confirm them — do not infer them from Domain 1.

```text
DOMAIN CONTEXT — apply in addition to the Master System Prompt (MSP-3.0) and the
Course Style Guide already governing this project.

You are producing content for the [DOMAIN NAME] domain, Group [N], Blocks [first]-
[last], of the AIM Blocks V2 Curriculum Transformation project.

DOMAIN SCOPE
This domain covers Blocks [first]-[last]. It is domain [N] of three. Prerequisite
domain: [name, or "none"]. Total DLUs: [total], being [count] Blocks of [per-block]
DLUs each.

SOURCE CONTENT AVAILABLE FOR THIS DOMAIN
State, per source type, what has been provided across this domain's Blocks and what
each one is for. Where availability is partial, say which Blocks are affected. Any
source type whose availability has not been confirmed for this domain reads
"REVIEW NEEDED - availability not confirmed" and is flagged MISSING_SOURCE at the
point of use rather than assumed present.
  Course syllabus and calendar  [availability] - [purpose]
  Instructional content         [availability] - [purpose]
  Hangar activities             [availability] - [purpose]
  Quizzes                       [availability] - [purpose]
  Study questions               [availability] - [purpose]
  Projects                      [availability] - [purpose]
  Instructor guides             [availability] - [purpose]
  ACS codes                     [availability] - [purpose]

DOMAIN-LEVEL HANDLING RULES
1. Where source coverage is complete, produce content according to the Master
   System Prompt.
2. Where a source is incomplete or unavailable, do not generate or assume the
   missing content. Flag MISSING_SOURCE and route the issue to the instructional
   designer for confirmation before producing the affected output.
3. Do not assume that a source gap identified in one Block also exists in another.
   Verify source availability at the point of use, for each Block and each day.
4. [Any handling rule specific to this domain - a prerequisite-domain dependency, a
   subject-matter constraint, a certification requirement. Delete this line if the
   domain has none.]

DOMAIN QUALITY GATE — confirm all four before any output in this domain goes for
ID sign-off:
  - every ACS code associated with the relevant day or Block is accounted for;
  - missing source content is flagged and has not been fabricated;
  - all content is traceable to the supplied syllabus, instructional content, or
    handbook, and none of it rests on general knowledge;
  - no unresolved REVIEW NEEDED flag remains.
```
