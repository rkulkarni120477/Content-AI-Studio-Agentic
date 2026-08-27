"""An unchunked document must not become the entire context pack.

_pack_units admits the top-ranked unit even when it alone exceeds the caller's
token budget, so a query is never answered with nothing merely because its best
answer runs a little long. That concession had no ceiling, and production holds
documents it was never meant to cover: five reference textbooks reached the
Source Library as ONE unit each — 8083-31B.pdf is a single unit of 3,560,663
characters, about 890,000 tokens — because they were classified as a generic type
instead of ebook_reference and so were never chunked.

Any of those ranking first would have been packed whole into a 6,000-token
budget, displacing every other source and overrunning the model's own window.

These pin the boundary from both sides: a long-but-real passage still gets
through, an unchunked document does not, and the response says which of the two
happened — the remedy differs, and only one of them is the caller's to fix.
"""
from __future__ import annotations

import pytest

from config.settings import get_tenant_config
from services.context_retrieval import (
    ContextRetrievalService,
    _FIRST_UNIT_BUDGET_MULTIPLE,
    estimate_tokens,
)

BUDGET = 6000


@pytest.fixture
def service():
    return ContextRetrievalService(get_tenant_config("aim"))


def unit(uid: str, tokens: int, name: str = "doc.pdf"):
    """A unit whose formatted size is approximately `tokens`.

    estimate_tokens counts words * 1.35, so the word count is derived from the
    target rather than guessed — a unit built by character count would drift the
    moment that estimator changes and these tests would stop testing the
    boundary they name.
    """
    words = max(1, int(tokens / 1.35))
    return {
        "content_unit_id": uid,
        "source_file_name": name,
        "title": "",
        "text": " ".join(["word"] * words),
    }


def pack(service, units, budget=BUDGET, top_k=8):
    dropped: list = []
    selected, used, _combined = service._pack_units(
        units, top_k=top_k, token_budget=budget,
        include_visual_summary=False, dropped=dropped)
    return selected, used, dropped


def test_a_long_first_unit_is_still_admitted(service):
    """The original concession survives: over budget is not by itself a refusal."""
    over = unit("u1", BUDGET * 2)
    assert estimate_tokens(over["text"]) > BUDGET
    selected, _used, dropped = pack(service, [over])
    assert [u["content_unit_id"] for u in selected] == ["u1"]
    assert dropped == []


def test_an_unchunked_document_is_refused_not_packed(service):
    """The 8083-31B shape: one unit far past any budget."""
    blob = unit("whole-book", BUDGET * _FIRST_UNIT_BUDGET_MULTIPLE * 10, "8083-31B.pdf")
    selected, used, dropped = pack(service, [blob])
    assert selected == []
    assert used == 0
    assert [d["content_unit_id"] for d in dropped] == ["whole-book"]
    assert dropped[0]["reason"] == "unit_exceeds_budget_ceiling"


def test_refusing_the_blob_does_not_cost_the_units_behind_it(service):
    """The real hazard was displacement, not just the oversized unit itself.

    Packed whole, the blob consumed the budget and every genuine passage after it
    was dropped. Refused, they are returned — which is the entire point.
    """
    blob = unit("whole-book", BUDGET * 100, "8083-31B.pdf")
    good = [unit(f"g{i}", 500, "Block 9 Calendar.xlsx") for i in range(4)]
    selected, _used, dropped = pack(service, [blob] + good)
    assert [u["content_unit_id"] for u in selected] == ["g0", "g1", "g2", "g3"]
    assert [d["content_unit_id"] for d in dropped] == ["whole-book"]


def test_the_ceiling_applies_only_to_the_first_unit(service):
    """A later unit is refused by the budget, and says so differently.

    Same outcome, different cause: the budget ran out. Conflating the two would
    have a caller raise token_budget to chase a document no budget can return.
    """
    units = [unit("u1", 5000), unit("u2", 5000)]
    selected, _used, dropped = pack(service, units)
    assert [u["content_unit_id"] for u in selected] == ["u1"]
    assert [d["reason"] for d in dropped] == ["budget_exhausted"]


def test_the_response_names_the_document_that_needs_rechunking(service):
    """retrieval_summary must distinguish the two, not just count them."""
    blob = unit("whole-book", BUDGET * 100, "8083-31B.pdf")
    later = unit("u2", 5000, "Block 9 Calendar.xlsx")
    _selected, _used, dropped = pack(service, [blob, later])
    oversized = sorted({d["source_file_name"] for d in dropped
                        if d.get("reason") == "unit_exceeds_budget_ceiling"})
    assert oversized == ["8083-31B.pdf"]
    # Negative control: the calendar was never dropped for being oversized, so if
    # the reason were absent or constant this assertion would fail.
    assert "Block 9 Calendar.xlsx" not in oversized


def test_the_boundary_itself(service):
    """Exactly at the ceiling is admitted; past it is refused."""
    at = unit("at", BUDGET * _FIRST_UNIT_BUDGET_MULTIPLE - 200)
    assert estimate_tokens(at["text"]) <= BUDGET * _FIRST_UNIT_BUDGET_MULTIPLE
    selected, _u, dropped = pack(service, [at])
    assert [u["content_unit_id"] for u in selected] == ["at"] and dropped == []

    past = unit("past", BUDGET * _FIRST_UNIT_BUDGET_MULTIPLE + 2000)
    assert estimate_tokens(past["text"]) > BUDGET * _FIRST_UNIT_BUDGET_MULTIPLE
    selected, _u, dropped = pack(service, [past])
    assert selected == [] and [d["reason"] for d in dropped] == ["unit_exceeds_budget_ceiling"]
