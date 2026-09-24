"""CAS-18 / CAS-136: Export Audit Log failed with 422 because the frontend's
Export button sent page_size=5000, validated against AuditTrailQuery's
page_size<=200 -- a field export never reads (it always writes every matching
row, capped at 100k, see export_audit_trail). AuditTrailFilters (no
page/page_size) is what /export now takes; only the paginated list view keeps
the le=200 cap, since only it actually uses page_size.
"""

from __future__ import annotations


def test_export_ignores_an_oversized_page_size_instead_of_422ing(client, auth_headers):
    """The exact regression: page_size=5000 on /export used to 422. It's not
    a real param there (nothing in the handler reads it), so the fix is for
    the endpoint to just not declare it -- not to raise the cap."""
    resp = client.get(
        "/api/v1/analytics/audit-trail/export",
        params={"page_size": 5000},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")


def test_list_view_still_enforces_its_real_page_size_cap(client, auth_headers):
    """The cap is real for the paginated list -- it actually limits() by
    page_size -- so this endpoint must keep rejecting an oversized value.
    Guards against loosening the wrong side of the split."""
    resp = client.get(
        "/api/v1/analytics/audit-trail",
        params={"page_size": 5000},
        headers=auth_headers,
    )
    assert resp.status_code == 422
