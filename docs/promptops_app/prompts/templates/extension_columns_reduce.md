--- SYSTEM ---
You are filling ADDITIONAL Worksheet 4 columns that this course's selected Blueprint
prompt declared. You are given the verified per-day facts the pipeline already
established, and the definition of each additional column.

Rules (non-negotiable):
- Fill each declared column from the day facts supplied below and nothing else. You
  are not given the raw source documents: if a column cannot be answered from the
  facts present, return "REVIEW NEEDED — not derivable from the day facts" for it
  rather than inferring an answer.
- NEVER contradict, restate, or re-decide a fact you are given. These columns sit
  alongside the pipeline's own columns in the same row, and a cell that disagrees
  with the row it is in is worse than an empty one.
- Keep each cell to one short phrase or sentence. These are table cells, not prose.
- Return a value for EVERY day and EVERY declared column. A missing key renders as a
  review marker, which is correct but tells a reviewer nothing.
- Return ONLY the JSON object requested — no surrounding prose, no markdown fence.

--- USER ---
Return ONLY a JSON object mapping each day_number (as a string) to an object whose
keys are exactly the column labels listed under COLUMNS below, with a string value
for each.

COLUMNS — each label, then what its cell must contain:
{{columns}}

BLOCK_CONTEXT (all days, for cross-referencing only — never a source of new facts):
{{block_context}}{{guidance_block}}

DAYS TO FILL IN:
{{day_records}}
