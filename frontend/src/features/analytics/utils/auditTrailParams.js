/**
 * Filter params shared by GET /api/v1/analytics/audit-trail and its /export.
 *
 * | API param      | Type    | Description                          |
 * |----------------|---------|--------------------------------------|
 * | entity_type    | string  | Filter by entity (cdd, blueprint…)   |
 * | actor          | string  | Filter by username                   |
 * | action         | string  | Filter by action key                 |
 * | project_id     | integer | Filter by project ID                 |
 * | date_from      | string  | Start date YYYY-MM-DD                |
 * | date_to        | string  | End date YYYY-MM-DD                  |
 *
 * No page/page_size here on purpose — export always writes every matching
 * row and never reads them, so they don't belong in a params builder used
 * for export requests. See app/schemas/analytics.py's AuditTrailFilters.
 */
export function buildAuditTrailFilterParams(filters = {}) {
  const params = {};

  if (filters.actor) params.actor = filters.actor;
  if (filters.action) params.action = filters.action;
  if (filters.entityType) params.entity_type = filters.entityType;
  if (filters.projectId) params.project_id = Number(filters.projectId);
  if (filters.dateFrom) params.date_from = filters.dateFrom;
  if (filters.dateTo) params.date_to = filters.dateTo;

  return params;
}

/**
 * Query parameters for GET /api/v1/analytics/audit-trail (paginated list).
 * Adds page/page_size (max 200) on top of buildAuditTrailFilterParams —
 * use that directly for the /export request instead, which has no pagination.
 */
export function buildAuditTrailParams({ page = 1, pageSize = 25, filters = {} } = {}) {
  return {
    page: Number(page) || 1,
    page_size: Number(pageSize) || 25,
    ...buildAuditTrailFilterParams(filters),
  };
}
