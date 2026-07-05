"""
Characterization: promptops_app/prompts/prompt_builder.py

Captures the {{double}}-brace substitution engine's exact semantics.
NOTE: registry/pipeline templates use {{var}} — the SAME syntax family as the
Prompt Library's render_prompt_content. Only the legacy hard-coded Generate
constants (prompt_templates.py) use Python {single} + .format().
"""

from __future__ import annotations

import pytest

from promptops_app.prompts.prompt_builder import (
    build_context_variables,
    build_prompt,
    extract_variables,
    render,
    render_safe,
    validate,
)


class TestExtractVariables:
    def test_double_brace_only(self):
        # {single} braces are NOT variables to this engine.
        assert extract_variables("a {{x}} b {y} c {{z}}") == ["x", "z"]

    def test_appearance_order_and_dedupe(self):
        assert extract_variables("{{b}} {{a}} {{b}}") == ["b", "a"]

    def test_identifier_rule(self):
        # Must start with letter/underscore; digits-first is not a variable.
        assert extract_variables("{{1abc}} {{_ok}} {{ok2}}") == ["_ok", "ok2"]


class TestRender:
    def test_substitutes_and_coerces_to_str(self):
        assert render("n={{n}}", {"n": 42}) == "n=42"

    def test_strict_raises_on_missing(self):
        with pytest.raises(ValueError) as exc:
            render("{{present}} {{missing}}", {"present": "x"})
        assert "missing" in str(exc.value)

    def test_non_strict_leaves_placeholder_in_place(self):
        # This is the "missing variable becomes literal {{placeholder}} text"
        # behavior the plan flags — captured deliberately.
        out = render("{{present}} {{missing}}", {"present": "x"}, strict=False)
        assert out == "x {{missing}}"

    def test_none_value_leaves_placeholder_even_in_strict(self):
        # None counts as "provided" for validation but the placeholder stays.
        out = render("{{a}}", {"a": None})
        assert out == "{{a}}"

    def test_render_safe_is_non_strict(self):
        assert render_safe("{{gone}}", {}) == "{{gone}}"


class TestValidate:
    def test_missing_and_present(self):
        ok, missing = validate("{{a}} {{b}}", {"a": 1})
        assert ok is False and missing == ["b"]

    def test_none_is_provided(self):
        ok, missing = validate("{{a}}", {"a": None})
        assert ok is True and missing == []


class TestBuildPrompt:
    def test_default_is_non_strict(self):
        # No caller passes strict=True today — required_vars are NOT enforced.
        system, user, name, version = build_prompt("cdd_generation", {})
        assert name == "cdd_generation"
        assert version == "v1"          # registry version for the file tier
        assert "{{course_name}}" in user  # unresolved placeholder left as-is

    def test_renders_supplied_variables(self):
        _, user, _, _ = build_prompt(
            "cdd_generation", {"course_name": "CTE Nursing"}
        )
        assert "CTE Nursing" in user
        assert "{{course_name}}" not in user

    def test_unknown_template_raises_filenotfound(self):
        with pytest.raises(FileNotFoundError):
            build_prompt("no_such_template", {})


class TestDeclaredVariableEnforcement:
    """Phase 8 strict-variable enforcement: with PROMPT_RESOLVE_BY_COMPONENT
    on, build_prompt raises PromptVariableError when a template's DECLARED
    required variables (prompt_variables rows for DB templates, registry
    required_vars for file templates) are not supplied. Deliberately narrower
    than render(strict=True): undeclared {{placeholders}} stay lenient."""

    @pytest.fixture(autouse=True)
    def _flag_on(self, monkeypatch):
        monkeypatch.setenv("PROMPT_RESOLVE_BY_COMPONENT", "1")

    def _declare(self, db, prompt, *names):
        from promptops_app.database import PromptVariable

        for i, n in enumerate(names):
            db.add(PromptVariable(prompt_id=prompt.id, name=n, sort_order=i))
        db.commit()

    def test_file_tier_registry_required_vars_enforced(self):
        from promptops_app.prompts.prompt_builder import PromptVariableError

        with pytest.raises(PromptVariableError) as exc:
            build_prompt("cdd_generation", {})
        assert set(exc.value.missing) == {
            "course_name", "target_audience", "expert_domain",
        }

    def test_flag_off_stays_lenient(self, monkeypatch):
        monkeypatch.delenv("PROMPT_RESOLVE_BY_COMPONENT", raising=False)
        _, user, _, _ = build_prompt("cdd_generation", {})
        assert "{{course_name}}" in user  # legacy leave-in-place behavior

    def test_db_declarations_supersede_registry_and_are_enforced(self, db):
        from promptops_app.prompts.prompt_builder import PromptVariableError

        from .conftest import make_db_prompt

        row = make_db_prompt(
            db, "cdd_generation",
            system="s", user="{{special_var}} and {{undeclared}}",
        )
        self._declare(db, row, "special_var")

        # The registry's cdd vars are NOT required for this row — only the
        # declared one is.
        with pytest.raises(PromptVariableError) as exc:
            build_prompt("cdd_generation", {"course_name": "x"}, db=db)
        assert exc.value.missing == ["special_var"]
        assert exc.value.template == "cdd_generation"

        # Supplying the declared var renders; the undeclared placeholder is
        # still left in place (no strict-all regression).
        _, user, _, _ = build_prompt(
            "cdd_generation", {"special_var": "OK"}, db=db,
        )
        assert user == "OK and {{undeclared}}"

    def test_none_counts_as_supplied(self, db):
        from .conftest import make_db_prompt

        row = make_db_prompt(db, "cdd_generation", system="s", user="{{v}}")
        self._declare(db, row, "v")
        _, user, _, _ = build_prompt("cdd_generation", {"v": None}, db=db)
        assert user == "{{v}}"  # None → provided-but-empty, left in place


class TestBuildContextVariables:
    def test_teacher_student_inference(self):
        v = build_context_variables(teacher_mode=True)
        assert v["teacher_mode"] == "Yes" and v["student_mode"] == "No"
        v = build_context_variables(teacher_mode=False)
        assert v["teacher_mode"] == "No" and v["student_mode"] == "Yes"

    def test_extra_kwargs_merge_verbatim(self):
        v = build_context_variables(topic="Safety", selected_module="M1")
        assert v["topic"] == "Safety" and v["selected_module"] == "M1"

    def test_defaults(self):
        v = build_context_variables()
        assert v["output_format"] == "lesson"
        assert v["course_name"] == ""
