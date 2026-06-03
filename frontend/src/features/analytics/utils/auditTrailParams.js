/**
 * Query parameters for GET /api/v1/analytics/audit-trail
 *
 * | API param      | Type    | Description                          |
 * |----------------|---------|--------------------------------------|
 * | entity_type    | string  | Filter by entity (cdd, blueprint…)   |
 * | actor          | string  | Filter by username                   |
 * | action         | string  | Filter by action key                 |
 * | project_id     | integer | Filter by project ID                 |
 * | date_from      | string  | Start date YYYY-MM-DD                |
 * | date_to        | string  | End date YYYY-MM-DD                  |
 * | page           | integer | Page number (default 1)              |
 * | page_size      | integer | Rows per page (default 25, max 200)  |
 */
export function buildAuditTrailParams({ page = 1, pageSize = 25, filters = {} } = {}) {
  const params = {
    page: Number(page) || 1,
    page_size: Number(pageSize) || 25,
  };

  if (filters.actor) params.actor = filters.actor;
  if (filters.action) params.action = filters.action;
  if (filters.entityType) params.entity_type = filters.entityType;
  if (filters.projectId) params.project_id = Number(filters.projectId);
  if (filters.dateFrom) params.date_from = filters.dateFrom;
  if (filters.dateTo) params.date_to = filters.dateTo;

  return params;
}
