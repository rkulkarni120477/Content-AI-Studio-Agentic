# Editing the block-wide prompts

Who this is for: anyone who wants to change **how** the block-wide CDD / Block
Blueprint pipeline writes, without a code change.

The short version: the pipeline's *structure* is code-owned and not editable by
prompt. Its *judgment* — how a cell is decided, what counts as sufficient evidence,
tone, emphasis, controlled vocabulary — lives in four editable templates. Editing
them is live, validated, and CI-guarded. Nothing here needs a deploy of new code.

---

## What you can and cannot change

| | Where it lives | Editable by prompt? |
|---|---|---|
| Which worksheets exist, and their columns | `promptops_app/services/block_wide_service.py` (`_DAY_TABLE_HEADER`, `_SOURCE_INVENTORY_HEADER`, `_ACS_REGISTRY_HEADER`) | **No** — code |
| Which per-day fields are extracted | `MAP_SCHEMA` in `dis_backend/services/digests/mapper.py` | **No** — code |
| Worksheets 1–3 contents | `dis_backend/services/digests/worksheets.py` (pure Python, no LLM) | **No** — code |
| ACS registry, orphan check, quick-check targets, hangar file lists | same | **No** — code, deliberately |
| **How each field is judged and worded** | the four templates below | **Yes** |

That split is not an oversight. `tests/eval/test_guidance_effect.py` pins it: two
runs differing only in prompt guidance must render Worksheets 2 and 3, the coverage
block, and every code-derived day column *byte-identically*. An admin's wording must
never be able to move a traceable fact.

---

## The four files

All under `promptops_app/prompts/templates/` except the first.

| File | Governs | Cost of an edit |
|---|---|---|
| `dis_backend/services/digests/templates/digest_map.md` | the per-day extraction rubric and the `concept_type` taxonomy | **Rebuilds every day of every block.** Its content hash feeds the per-day digest cache key, so an edit invalidates exactly the digests it changes — correct, but it means paying for a full re-extraction (~20 MAP calls per block). |
| `cdd_reduce.md` | CDD day-narrative wording | free — REDUCE runs fresh every generation |
| `blueprint_reduce_worksheet.md` | Blueprint day-narrative wording | free |
| `patterns_notes_reduce.md` | Worksheet 5 (Content Arc, Production Readiness, …) | free |

Edits take effect on the **next generation**: `prompt_loader` reads the file on
every resolve (no cache), and `docker-compose.yml` bind-mounts the repo into the app
containers. No restart. *Confirm your production deployment has that bind mount —
`Dockerfile` uses `COPY . .`, so without it an edit becomes a redeploy.*

---

## The one rule you must not break

Each template is simultaneously prose **and a machine contract**: the code parses
the model's JSON reply by exact key name. So keep asking for every key the template
already asks for, and keep its `{{placeholders}}`.

You do not have to remember which keys. If an edit drops one:

* **at runtime** — `reduce_prompts.resolve_reduce_prompt` validates the contract and
  falls back to the built-in prompt **for the affected layer only**, logging
  `reduce_prompt_*`. Your edit is ignored, loudly; nothing generates garbage.
* **in CI** — `tests/unit/test_reduce_prompts.py::test_shipped_templates_satisfy_their_contracts`
  runs over these files and fails before the change ships.
* **for `digest_map.md`** — `prompt_template.resolve` checks it mentions every
  `MAP_REPLY_KEYS` entry and otherwise falls back to a compact built-in rubric,
  logging the reason. Digest *quality* degrades, correctness does not.

The system prompt half is free prose and has no contract beyond being non-empty, so
tone/emphasis edits there are the safest kind.

---

## How to check your edit did something

1. Generate the block. In the stored artifact's `prompt_provenance`, read
   `reduce_prompts` — it records, per layer, whether the text came from `db`, `file`
   or `builtin`, plus a content hash. `file` with a changed hash means your edit ran.
   `builtin` means it was rejected — check the logs.
2. `map_guidance_sent_chars` / `..._fingerprint` identify exactly what MAP received.
3. Run `pytest tests/eval/test_guidance_effect.py` — it proves guidance still has an
   effect and still cannot touch the code-computed facts.

---

## Two things that are *not* prompt-editable, and what to do instead

**A different column set.** Add or rename a Worksheet 4 column and the prompt will
be ignored: the renderer emits its own header row. You will see this reported —
generation records a `PROMPT RECONCILIATION` section and the prompt picker shows the
divergence when you select the template (see `promptops_app/services/prompt_capability.py`).
Changing the column set means changing `MAP_SCHEMA`, `_DAY_TABLE_HEADER` and the
renderer — a code change, and a `PROMPT_VERSION_BASE` bump.

**AKTR high-miss data.** `Targets for Quick Check` reads `NO AKTR DATA` because the
miss-rate table is not ingested anywhere in the system. No prompt edit can supply
it; it needs an ingestion path.

---

## Selecting a whole different prompt

The "Prompt Template" dropdown selects the prompt that drives *single-call*
generation and, on the block-wide path, is distilled into judgment guidance only. On
selection the UI now shows how that template compares with what the pipeline emits —
requested-but-unemitted columns, variables this platform cannot supply, and whether
generation would be refused outright. A red panel means the template asks for
something impossible here (a code interpreter, an `.xlsx` file, a reply that is only
a download link) and generation will refuse rather than store an empty document.
