You are extracting a compact JSON digest of ONE course day for a Block
Blueprint / Course Design Document. Extract ONLY these fields. Do not invent
a source type that is absent. Be concise. If a field genuinely doesn't
apply (e.g. no good interactive/job-aid opportunity, or no source page can
be identified), use the "no"/false/"N/A" values shown — do not force one
that isn't warranted by the sources.

List fields (misconceptions, salient_excerpts) must be a genuine JSON array
— use [] (empty) when the day has nothing to document (e.g. an assessment or
pure-review day has no new misconceptions to list). NEVER put a placeholder
string like "no", "N/A", or "none" INSIDE the array as if it were a real
item — that renders as a literal, wrong-looking item downstream instead of
the clean "none documented" an empty array produces.

Respond with ONLY the JSON object below — no preamble, no explanation,
no markdown fence, nothing before or after it.

{{schema}}

concept_type is one of, or a combination of, the following — pick whichever
single label fits best, or combine two with " + " and append " (Mixed)" when
a day genuinely blends two of them (e.g. "Conceptual + Skill (Mixed)" for a
day that introduces new understanding AND opens hands-on practice of it the
same day):
  - Conceptual: first-encounter understanding — explaining what something IS,
    why it matters, how pieces relate, or HOW a tool/technique/process is used
    or its operating principles. This still applies even when the SOURCES
    describe usage/procedure in prose — being TAUGHT ABOUT how something is
    done is Conceptual, not Procedural/Skill, unless the day ALSO has the
    learner actually doing it (a bench task, project activity, lab/hangar
    exercise). No hands-on practice yet.
  - Procedural: a sequence of steps/actions the LEARNER THEMSELVES follows to
    do a task THIS day (not merely reading a description of the steps).
  - Skill: hands-on practice/application BY THE LEARNER of something already
    introduced — requires an actual bench task, project, or exercise in the
    SOURCES this day, not just explanatory description of what skilled use
    looks like.
  - Factual: discrete facts, definitions, or terminology to recall, with no
    process or unifying framework tying them together.
  - Metacognitive: reflection on one's own learning/strategy — review,
    self-assessment, or planning how to approach material, not new subject
    content itself (e.g. a review day with no graded assessment).
  - Summative Assessment: the day's PURPOSE is administering a graded
    final/cumulative/block-ending exam — not new instruction and not
    reflection/review. Use this, not Metacognitive, when the SOURCES show an
    actual graded test happening this day.
  - Review-Assessment: the day both revisits earlier material AND carries a
    graded check on it (a quiz, review questions collected for marks). Use this
    rather than Metacognitive when a graded element is present, and rather than
    Summative Assessment when the assessment covers a section rather than the
    whole block.
  - Project-Application / Review: the day's PURPOSE is the learner applying
    earlier material in a project or activity, rather than new instruction —
    typically a project work day, often paired with review of what it draws on.
    Use this in preference to Skill when the SOURCES show a named project as the
    day's main event rather than practice embedded in a lesson.
concept_type_explanation is one sentence grounding that choice in what the
SOURCES below actually contain — never a generic restatement of the label. If
you pick Procedural or Skill, name the specific hands-on task/project/exercise
from the SOURCES that justifies it, not just the topic being discussed.

concept_scope lists the specific sub-topics, tools, materials, or processes this
day actually covers, taken from that day's own SOURCES. Write it as a compact
comma-separated phrase list (typically 3-8 phrases), not sentences — the FORM is
"<sub-topic>, <sub-topic>, <tool or material>, <process>". Draw every phrase from
this day's SOURCES; do not supply subject matter from your own knowledge of the
field. It must NOT be a restatement of the day's topic or lesson title — the scope
names what *within* that topic was taught. Use "" only when the sources for this
day carry no identifiable subject content at all.

interactive_candidate and job_aid_candidate DEFAULT TO false. They are
recommendations that cost real production money, so each one has to be earned by
evidence in THIS day's SOURCES. If you cannot point to the specific thing named
below, answer false — a thin "maybe" is worse than a clear no, because a reviewer
cannot tell a speculative yes from a grounded one.

interactive_candidate is true only when this day's SOURCES contain a concrete
artefact with checkable structure that a learner would manipulate — parts to
label on a named diagram, terms to match to definitions, a sequence to order, or
values to read off a named table/chart. It is false when the day is carried by
explanation or discussion alone, however important the topic, and false on days
whose purpose is review, project work, or assessment: those days exercise
material that was already made interactive earlier, and duplicating it adds cost
without adding coverage.

job_aid_candidate is true only when this day's SOURCES contain a REFERENCE
artefact a technician would consult repeatedly at the bench AFTER the lesson —
e.g. a symbol/line-type legend, a tolerance or torque table, a conversion chart,
a fixed step checklist. It is false when the day's value is understanding rather
than lookup, and false when the only candidate content is a summary of the
lesson itself: a recap is not a job aid. A day rich in facts is not sufficient —
name the artefact, or answer false.

interactive_scope is a short phrase naming what the interactive would cover, in
the form "<verb-ing> <the specific thing from THIS day's sources>" (e.g.
"labeling the parts of <a component named in the sources>"); "" when
interactive_candidate is false. Name only things present in the SOURCES below —
do not carry over subject matter from another day or from general knowledge.
job_aid_source_reference names the specific source file or handbook
chapter/page (from the SOURCES below) that grounds the job aid content;
"N/A" when job_aid_candidate is false or no such source is identifiable —
never invent a citation that isn't in the SOURCES.
interactive_type/job_aid_type/interactive_content/job_aid_description are
"" when their *_candidate is false.
{{guidance_block}}DAY {{day_number}}: {{topic}}
LESSON: {{lesson_title}}
SOURCES:
{{sources}}
