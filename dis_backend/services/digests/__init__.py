"""Digest tier for block-wide CDD / Blueprint generation.

ENUMERATE -> (MAP -> REDUCE -> VERIFY). This package owns the DIS-side half:
deterministic enumeration of a block's days + units, day-attribution of units
whose ``day_number`` is NULL, and (later slices) the per-day digest store.

See ``CDD_CONTEXT_REDESIGN_PLAN.md`` and ``CDD_PHASE1_IMPLEMENTATION.md``.
"""
