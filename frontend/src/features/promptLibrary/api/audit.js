import { apiFetch, apiUrl, apiDownload } from './client';

function toQuery(filters) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== '') {
      params.set(key, String(value));
    }
  });
  const q = params.toString();
  return q ? `?${q}` : '';
}

export async function fetchAuditEvents(filters = {}) {
  const res = await apiFetch(`/api/audit${toQuery(filters)}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || err.detail || 'Failed to load audit log');
  }
  return res.json();
}

export function auditExportUrl(filters = {}) {
  return apiUrl(`/api/audit/export${toQuery(filters)}`);
}

// Bearer-auth-safe CSV export.
export async function exportAuditCsv(filters = {}) {
  await apiDownload(`/api/audit/export${toQuery(filters)}`, 'audit-log.csv');
}
