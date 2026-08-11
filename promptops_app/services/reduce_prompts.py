"""Resolution + contract validation for the block-wide REDUCE prompts.

Why this layer exists
---------------------
The REDUCE calls are structured-extraction contracts: ``BlockWideGenerator``
parses the model's JSON reply and reads specific keys by exact name, and the
worksheet renderer reads the resulting fields by exact name after that. So the
prompts are *simultaneously* two things:

* an instructional-design artifact an admin should be able to edit (tone,
  emphasis, what each cell should say, how to judge a borderline case), and
* a machine contract that must keep asking for an exact set of JSON keys.

Making the whole prompt DB-editable without a guard would let a well-meaning
edit drop a required key and produce a structurally-valid-but-empty deliverable
with **no error** — the failure mode ``prompt_guidance``'s docstring warns about.
Hardcoding it instead (the previous state) made the instructional half
unreachable to the people who own it.

This module resolves the editable prompt through the normal DB → file → built-in
tiers and then *verifies the contract survived the edit*, falling back to the
built-in for any layer that fails. An admin gets full editorial control; a bad
edit degrades loudly to the built-in instead of silently to garbage.

Resolution is per-layer, not all-or-nothing: an admin who edits only the SYSTEM
half (the domain/style layer) keeps the built-in USER contract, and vice versa.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Dict

log = logging.getLogger(__name__)


def content_hash(text: str) -> str:
    """Short content fingerprint of a resolved prompt layer.

    Mirrors the MAP side's ``current_prompt_version()`` (dis_backend digests/mapper),
    which versions its template by content hash for the same reason: a
    ``template_version`` label is only trustworthy for the DB tier, where versions are
    append-only and immutable. A ``file``-tier template can be edited in place without
    the registry version moving, and a ``builtin`` moves with the code — so a version
    label alone lets an audit row claim a prompt identity whose text has since changed.
    Recording the hash makes the logged prompt identity VERIFIABLE rather than merely
    asserted, without storing the prompt text itself.
    """
    return hashlib.sha256((text or "").encode()).hexdigest()[:12]


@dataclass(frozen=True)
class ReducePrompt:
    """A resolved REDUCE prompt pair plus provenance for the audit trail."""

    system: str
    user_template: str
    #: "db" | "file" | "builtin", tracked per layer so provenance shows exactly
    #: which half an admin actually overrode.
    system_source: str = "builtin"
    user_source: str = "builtin"
    template_name: str = ""
    template_version: str = ""
    #: Contract violations found in a resolved template, for logging/provenance.
    rejected: Dict[str, str] = field(default_factory=dict)

    def to_provenance(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "template_name": self.template_name,
            "template_version": self.template_version,
            "system_source": self.system_source,
            "user_source": self.user_source,
            # Per-layer, because the two halves resolve independently: an admin can
            # override SYSTEM from the DB while USER stays built-in, and each half
            # needs its own verifiable identity.
            "system_sha": content_hash(self.system),
            "user_template_sha": content_hash(self.user_template),
        }
        if self.rejected:
            out["rejected"] = dict(self.rejected)
        return out


@dataclass(frozen=True)
class PromptContract:
    """What a resolved template must satisfy to be usable.

    ``required_placeholders`` are the ``{{vars}}`` the caller substitutes real
    payload into — a template missing one would silently omit the day records or
    the block context and ask the model to invent them.

    ``required_json_keys`` are the reply keys the parser reads by name. They must
    be *mentioned* in the prompt text; that is a necessary condition (a prompt
    that never names a key will not reliably get it) and is checkable without
    running the model.
    """

    required_placeholders: tuple[str, ...] = ()
    required_json_keys: tuple[str, ...] = ()

    def check(self, text: str, *, check_placeholders: bool = True) -> list[str]:
        """Return human-readable problems with *text* (empty ⇒ contract holds)."""
        problems: list[str] = []
        if check_placeholders:
            missing = [p for p in self.required_placeholders if "{{%s}}" % p not in text]
            if missing:
                problems.append("missing placeholders: " + ", ".join(missing))
        absent = [k for k in self.required_json_keys if k not in text]
        if absent:
            problems.append("does not mention required JSON keys: " + ", ".join(absent))
        return problems


def resolve_reduce_prompt(
    name: str,
    *,
    builtin_system: str,
    builtin_user: str,
    contract: PromptContract,
    db: Any = None,
    project_id: Any = None,
    cluster_id: Any = None,
    course_id: Any = None,
    prompt_id: Any = None,
) -> ReducePrompt:
    """Resolve *name* through DB → file → built-in, validating the contract.

    Passing ``db`` is what enables the DB tier at all: ``load_template`` only
    consults it when a session is supplied, so a call without ``db`` can reach
    the file tier at best (this was the pre-existing bug — the reduce prompts
    advertised runtime admin-editability they could not deliver).

    Never raises: any resolution or validation failure logs and yields the
    built-in for the affected layer.
    """
    builtin = ReducePrompt(
        system=builtin_system,
        user_template=builtin_user,
        template_name=name,
        template_version="builtin",
    )

    try:
        from promptops_app.prompts.prompt_loader import load_template
        tpl = load_template(
            name, db=db, project_id=project_id, cluster_id=cluster_id,
            course_id=course_id, prompt_id=prompt_id,
        )
    except Exception as exc:
        log.info("reduce_prompt_load_failed name=%s error=%s — using built-in", name, exc)
        return builtin

    source = getattr(tpl, "source", "file") or "file"
    version = str(getattr(tpl, "version", "") or "")
    rejected: Dict[str, str] = {}

    # ── SYSTEM layer: free prose, no contract beyond being non-empty ──────────
    raw_system = str(getattr(tpl, "system_template", "") or "").strip()
    if raw_system:
        system, system_source = raw_system, source
    else:
        system, system_source = builtin_system, "builtin"

    # ── USER layer: carries the machine contract, so it is validated ──────────
    raw_user = str(getattr(tpl, "user_template", "") or "").strip()
    if not raw_user:
        user, user_source = builtin_user, "builtin"
    else:
        problems = contract.check(raw_user)
        if problems:
            # Loud, actionable, and non-fatal — the deliverable still generates
            # correctly off the built-in while the admin fixes their template.
            log.warning(
                "reduce_prompt_contract_violation name=%s source=%s version=%s: %s — "
                "falling back to the built-in USER prompt for this call",
                name, source, version, "; ".join(problems),
            )
            rejected["user_template"] = "; ".join(problems)
            user, user_source = builtin_user, "builtin"
        else:
            user, user_source = raw_user, source

    resolved = ReducePrompt(
        system=system,
        user_template=user,
        system_source=system_source,
        user_source=user_source,
        template_name=name,
        template_version=version or "builtin",
        rejected=rejected,
    )
    log.info("reduce_prompt_resolved name=%s version=%s system=%s user=%s",
             name, resolved.template_version, resolved.system_source, resolved.user_source)
    return resolved


def render_user_prompt(prompt: ReducePrompt, variables: Dict[str, Any],
                       contract: PromptContract, builtin_user: str) -> str:
    """Render ``prompt.user_template`` with *variables*, verifying the contract
    survived rendering.

    Rendering is non-strict so an admin's extra ``{{placeholder}}`` is left in
    place verbatim rather than raising mid-generation. The post-render check
    catches the case a pre-render check cannot: a template whose contract keys
    were themselves supplied by a variable that resolved empty.
    """
    from promptops_app.prompts.prompt_builder import render_safe

    try:
        text = render_safe(prompt.user_template, variables)
    except Exception as exc:
        log.warning("reduce_prompt_render_failed name=%s error=%s — using built-in",
                    prompt.template_name, exc)
        text = render_safe(builtin_user, variables)

    # Placeholders are already substituted here, so only the JSON-key half of
    # the contract is meaningful post-render.
    problems = contract.check(text, check_placeholders=False)
    if problems:
        log.warning("reduce_prompt_postrender_violation name=%s: %s — using built-in",
                    prompt.template_name, "; ".join(problems))
        text = render_safe(builtin_user, variables)
    return text


# --------------------------------------------------------------------------- #
# Contracts for the three block-wide REDUCE calls
# --------------------------------------------------------------------------- #
#: Narrative fill (_fill_narratives) — one row per day, six generated cells.
NARRATIVE_CONTRACT = PromptContract(
    required_placeholders=("block_facts", "block_context", "guidance_block", "day_records"),
    required_json_keys=(
        "narrative", "how_it_is_applied", "learn_while_doing_reason",
        "hangar_activity_note", "objective_block_framing", "cross_day_misconception_note",
    ),
)

#: Patterns & Design Notes synthesis (_patterns_notes) — two prose fields.
PATTERNS_CONTRACT = PromptContract(
    required_placeholders=("facts", "guidance_block"),
    required_json_keys=("content_arc_summary", "production_readiness"),
)
