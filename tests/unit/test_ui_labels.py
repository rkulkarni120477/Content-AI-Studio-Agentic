"""Per-tenant UI label overrides — storage contract for `projects.ui_labels`.

These labels are display-only, so the guarantee that matters is that a bad or
legacy column value degrades to "use the defaults" instead of raising and
breaking a page render.
"""

import pytest

from app.schemas.project import ProjectRead
from app.schemas.tenant import TenantRead, TenantUpdateRequest
from app.schemas.ui_labels import (
    MAX_LABEL_LENGTH,
    dump_ui_labels,
    parse_ui_labels,
    sanitize_ui_labels,
)


class TestParseUiLabels:
    def test_parses_json_string_from_column(self):
        assert parse_ui_labels('{"style": "Design Guide"}') == {"style": "Design Guide"}

    def test_accepts_already_decoded_dict(self):
        assert parse_ui_labels({"title": "Block"}) == {"title": "Block"}

    @pytest.mark.parametrize("value", [None, "", "not json{{", "[]", '"a string"', "42"])
    def test_unparseable_values_degrade_to_empty(self, value):
        # Must never raise: a malformed row would otherwise break every page.
        assert parse_ui_labels(value) == {}

    def test_drops_unknown_keys(self):
        assert parse_ui_labels('{"style": "Guide", "bogus": "x"}') == {"style": "Guide"}


class TestSanitizeUiLabels:
    def test_trims_whitespace(self):
        assert sanitize_ui_labels({"style": "  Design Guide  "}) == {"style": "Design Guide"}

    def test_drops_blank_and_whitespace_only(self):
        assert sanitize_ui_labels({"style": "", "cdd": "   "}) == {}

    def test_drops_non_string_values(self):
        assert sanitize_ui_labels({"style": 42, "cdd": None, "title": ["x"]}) == {}

    def test_caps_length(self):
        out = sanitize_ui_labels({"style": "B" * (MAX_LABEL_LENGTH + 20)})
        assert len(out["style"]) == MAX_LABEL_LENGTH

    def test_keeps_all_four_known_keys(self):
        labels = {"title": "Block", "style": "Guide", "cdd": "Doc", "blueprint": "Plan"}
        assert sanitize_ui_labels(labels) == labels


class TestDumpUiLabels:
    def test_empty_stores_null_not_empty_object(self):
        # NULL keeps "no overrides" a single representation in the DB.
        assert dump_ui_labels({}) is None
        assert dump_ui_labels(None) is None
        assert dump_ui_labels({"style": "   "}) is None

    def test_round_trips_through_parse(self):
        stored = dump_ui_labels({"title": "Block", "style": "Design Guide"})
        assert parse_ui_labels(stored) == {"title": "Block", "style": "Design Guide"}

    def test_preserves_non_ascii(self):
        stored = dump_ui_labels({"style": "Guía"})
        assert parse_ui_labels(stored) == {"style": "Guía"}


class TestSchemaExposure:
    def test_tenant_read_decodes_column_string(self):
        t = TenantRead(id=1, name="Org", ui_labels='{"cdd": "Course Doc"}')
        assert t.ui_labels == {"cdd": "Course Doc"}

    def test_tenant_read_defaults_to_empty_when_null(self):
        assert TenantRead(id=1, name="Org", ui_labels=None).ui_labels == {}

    def test_tenant_read_omitting_field_is_empty(self):
        assert TenantRead(id=1, name="Org").ui_labels == {}

    def test_project_read_carries_labels_to_every_member(self):
        # This is the delivery path for non-platform-admin users: the frontend
        # already fetches the project on workspace load.
        p = ProjectRead(id=1, name="Org", ui_labels='{"style": "Design Guide"}')
        assert p.ui_labels == {"style": "Design Guide"}

    def test_project_read_survives_garbage_column(self):
        assert ProjectRead(id=1, name="Org", ui_labels="}}broken{{").ui_labels == {}


class TestUpdateRequest:
    def test_accepts_partial_labels(self):
        assert TenantUpdateRequest(ui_labels={"title": "Block"}).ui_labels == {"title": "Block"}

    def test_absent_field_is_none_meaning_no_change(self):
        assert TenantUpdateRequest(name="Org").ui_labels is None

    def test_empty_dict_is_distinct_from_none_and_clears(self):
        # {} must reach the router so it can reset the tenant to defaults.
        assert TenantUpdateRequest(ui_labels={}).ui_labels == {}

    def test_rejects_overlong_label(self):
        with pytest.raises(Exception):
            TenantUpdateRequest(ui_labels={"title": "B" * (MAX_LABEL_LENGTH + 1)})
