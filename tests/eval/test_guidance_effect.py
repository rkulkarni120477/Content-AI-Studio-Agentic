"""Does prompt-derived guidance actually do anything — and only what it may?

The rest of the suite proves guidance is THREADED: it reaches
``build_digests_sync``'s MAP call, it lands in the REDUCE narrative and patterns
prompts, it changes the per-day cache key, and it is recorded in provenance.
What none of that establishes is the pair of properties an admin editing a prompt
is actually relying on:

* **Effect** — different guidance produces a different document. Without this,
  "prompt-aware" is an assumption; a guidance layer that were silently dropped
  somewhere downstream would pass every threading test above.
* **Confinement** — guidance CANNOT change a code-computed fact. Worksheets 2 and
  3 are built deterministically in Python (dis_backend worksheets.py: "Pure,
  read-only, no LLM"), and several day-table cells are derived from the ACS
  registry and the day's real file list rather than from any model reply. Those
  must be byte-identical across two runs that differ only in guidance, because
  the whole reason the guidance wrapper says "must never add a field not listed
  above" is that an admin's wording must not be able to move a traceable fact.

Run end-to-end through ``generate_cdd_via_digests`` rather than against
``BlockWideGenerator`` directly, so the assertions cover the real render path the
stored document comes from.
"""
import json
import types

import pytest

from tests.eval.test_block_wide_coverage import DIGESTS, ENUMERATE_SUMMARY

GUIDANCE_A = "Name the specific AC number in every objective."
GUIDANCE_B = "Foreground the corrosion-inspection stakes in every objective."


def _fake_llm_echoing_guidance():
    """A REDUCE stand-in whose output DEPENDS on the guidance it was given.

    Echoing it into the narrative is what makes the effect assertion meaningful: a
    fake that ignored the guidance would make two runs identical and the test would
    pass while proving nothing.
    """
    def generate_with_metadata(model_choice, system, user, usage_ctx=None, max_tokens=None):
        echo = "A" if GUIDANCE_A in user else ("B" if GUIDANCE_B in user else "none")
        if '"content_arc_summary"' in user:
            text = json.dumps({"content_arc_summary": f"arc[{echo}]",
                               "production_readiness": "READY."})
        else:
            marker = "DAYS TO FILL IN:\n"
            payload = json.loads(user[user.index(marker) + len(marker):])
            text = json.dumps({
                str(d["day_number"]): {"narrative": f"cell[{echo}]",
                                       "how_it_is_applied": f"applied[{echo}]"}
                for d in payload
            })
        return types.SimpleNamespace(text=text, model=model_choice, status="ok",
                                     prompt_tokens=1, completion_tokens=1, error_type=None)
    return generate_with_metadata


@pytest.fixture
def run_block(monkeypatch):
    """Generate the same block twice, varying only the guidance."""
    import promptops_app.services.llm_service as llm_service
    from app.core import dis_client as dc
    from promptops_app.services import block_wide_service

    monkeypatch.setattr(llm_service, "generate_with_metadata", _fake_llm_echoing_guidance())
    bundle = {"block": "Block 2", "client_id": "aim",
              "enumerate": ENUMERATE_SUMMARY, "digests": DIGESTS}
    monkeypatch.setattr(dc.dis_client, "build_digests_sync",
                        lambda block, current_user=None, client_id="", map_guidance="":
                            {"built": 4, "cached": 0, "failed": 1, "map_calls": 1})
    monkeypatch.setattr(dc.dis_client, "get_digests_bundle_sync",
                        lambda block, current_user=None, client_id="": bundle)

    def _run(guidance):
        req = types.SimpleNamespace(block="Block 2", quality_tier="standard",
                                    course_title="T", course_id=1, project_id=1,
                                    document_title=None)
        return block_wide_service.generate_cdd_via_digests(
            db=None, request_body=req, current_user=types.SimpleNamespace(username="t"),
            dis_client_id="aim", map_guidance=guidance,
        )
    return _run


def _section(markdown: str, heading_fragment: str) -> str:
    """The body of the '## …<fragment>…' section, or "" when absent."""
    lines = markdown.splitlines()
    out, capturing = [], False
    for line in lines:
        if line.startswith("## "):
            if capturing:
                break
            capturing = heading_fragment in line
            continue
        if capturing:
            out.append(line)
    return "\n".join(out).strip()


def _day_rows(markdown: str) -> list[list[str]]:
    body = _section(markdown, "DAY-BY-DAY MAP")
    return [[c.strip() for c in line.strip().strip("|").split("|")]
            for line in body.splitlines()
            if line.startswith("| Day ") and not line.startswith("| Day |")]


def _column(markdown: str, header_name: str) -> list[str]:
    from promptops_app.services.block_wide_service import _DAY_TABLE_HEADER
    idx = _DAY_TABLE_HEADER.index(header_name)
    return [row[idx] for row in _day_rows(markdown)]


# --------------------------------------------------------------------------- #
# Effect
# --------------------------------------------------------------------------- #
def test_different_guidance_produces_a_different_document(run_block):
    a = run_block(GUIDANCE_A)["raw_output"]
    b = run_block(GUIDANCE_B)["raw_output"]
    assert a != b, "guidance that changes nothing is guidance that is being ignored"


def test_the_pipeline_itself_injects_no_guidance_text_into_the_document(monkeypatch):
    """Complement to the test above, with the model's contribution held constant.

    Whether a real model repeats its guidance in a reply is the model's business, and
    a stub cannot establish it either way. What the PIPELINE owns is that it does not
    paste the guidance into the rendered document — so with a model that ignores
    guidance entirely, two runs must come out byte-identical and free of it.
    """
    import promptops_app.services.llm_service as llm_service
    from app.core import dis_client as dc
    from promptops_app.services import block_wide_service

    def fixed(model_choice, system, user, usage_ctx=None, max_tokens=None):
        if '"content_arc_summary"' in user:
            text = json.dumps({"content_arc_summary": "arc", "production_readiness": "READY."})
        else:
            marker = "DAYS TO FILL IN:\n"
            payload = json.loads(user[user.index(marker) + len(marker):])
            text = json.dumps({str(d["day_number"]): {"narrative": "n", "how_it_is_applied": "a"}
                               for d in payload})
        return types.SimpleNamespace(text=text, model=model_choice, status="ok",
                                     prompt_tokens=1, completion_tokens=1, error_type=None)

    monkeypatch.setattr(llm_service, "generate_with_metadata", fixed)
    bundle = {"block": "Block 2", "client_id": "aim",
              "enumerate": ENUMERATE_SUMMARY, "digests": DIGESTS}
    monkeypatch.setattr(dc.dis_client, "build_digests_sync",
                        lambda block, current_user=None, client_id="", map_guidance="":
                            {"built": 4, "cached": 0, "failed": 1, "map_calls": 1})
    monkeypatch.setattr(dc.dis_client, "get_digests_bundle_sync",
                        lambda block, current_user=None, client_id="": bundle)

    def _run(guidance):
        req = types.SimpleNamespace(block="Block 2", quality_tier="standard", course_title="T",
                                    course_id=1, project_id=1, document_title=None)
        return block_wide_service.generate_cdd_via_digests(
            db=None, request_body=req, current_user=types.SimpleNamespace(username="t"),
            dis_client_id="aim", map_guidance=guidance,
        )["raw_output"]

    a, b = _run(GUIDANCE_A), _run(GUIDANCE_B)
    assert a == b, "the pipeline must not vary the document by itself"
    assert GUIDANCE_A not in a and GUIDANCE_B not in b


def test_guidance_reaches_the_cells_it_is_meant_to_influence(run_block):
    a = run_block(GUIDANCE_A)["raw_output"]
    b = run_block(GUIDANCE_B)["raw_output"]
    assert _column(a, "Notes") != _column(b, "Notes")
    assert _column(a, "How It Is Applied") != _column(b, "How It Is Applied")
    assert _section(a, "PATTERNS & DESIGN NOTES") != _section(b, "PATTERNS & DESIGN NOTES")


def test_running_without_guidance_still_produces_the_document(run_block):
    gen = run_block("")
    assert gen is not None and "WORKSHEET 4: DAY-BY-DAY MAP" in gen["raw_output"]
    assert gen["prompt_provenance"]["map_guidance_applied"] is False


# --------------------------------------------------------------------------- #
# Confinement — the half nothing else in the suite pins
# --------------------------------------------------------------------------- #
def test_guidance_cannot_change_the_code_computed_worksheets(run_block):
    """Worksheets 2 and 3 are assembled in Python from the enumerate bundle, with no
    model reply involved. Two runs differing only in guidance must render them
    identically — otherwise an admin's wording could move a traceable fact."""
    a = run_block(GUIDANCE_A)["raw_output"]
    b = run_block(GUIDANCE_B)["raw_output"]
    assert _section(a, "SOURCE FILE INVENTORY") == _section(b, "SOURCE FILE INVENTORY")
    assert _section(a, "ACS CODE REGISTRY") == _section(b, "ACS CODE REGISTRY")


@pytest.mark.parametrize("column", [
    "Day",                       # enumerate order
    "ACS",                       # the day's own declared codes
    "Hangar Activity",           # real file list, prefixed ahead of any model note
    "Projects Today",
    "Assessment Today",
    "Source Files",
    "Targets for Quick Check",   # derived from the ACS registry, never invented
    "Summative Exam Item Cluster",
])
def test_guidance_cannot_change_a_deterministic_day_column(run_block, column):
    a = run_block(GUIDANCE_A)["raw_output"]
    b = run_block(GUIDANCE_B)["raw_output"]
    assert _column(a, column) == _column(b, column), (
        f"{column} is code-derived — guidance must not be able to move it"
    )


def test_guidance_cannot_change_coverage_accounting(run_block):
    """Coverage is what tells a reviewer the document is trustworthy. If guidance
    could move it, a wording change could mask a gap."""
    a, b = run_block(GUIDANCE_A), run_block(GUIDANCE_B)
    assert a["coverage"] == b["coverage"]
    assert _section(a["raw_output"], "COVERAGE & REVIEW") == _section(b["raw_output"], "COVERAGE & REVIEW")


def test_the_guidance_actually_sent_is_recorded_distinguishably(run_block):
    """Provenance must let a reviewer tell the two runs apart after the fact —
    otherwise "why did this block change?" is unanswerable."""
    a = run_block(GUIDANCE_A)["prompt_provenance"]
    b = run_block(GUIDANCE_B)["prompt_provenance"]
    assert a["map_guidance"] == GUIDANCE_A and b["map_guidance"] == GUIDANCE_B
    assert a["map_guidance_sent_fingerprint"] != b["map_guidance_sent_fingerprint"]
