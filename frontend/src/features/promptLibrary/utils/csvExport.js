function escapeCsvField(value) {
  const s = String(value ?? '');
  if (/[",\r\n]/.test(s)) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

const EXPORT_COLUMNS = [
  'id', 'parent_id', 'parent_title', 'title', 'prompt', 'description', 'category',
  'visibility', 'teams', 'tags', 'variable_names', 'created_by', 'created_at',
  'updated_at', 'last_used_at', 'review_count', 'review_avg', 'version_count',
];

function promptBody(p) {
  return (p.content ?? '').trim();
}

export function promptsToCsv(prompts) {
  const rows = prompts.map((p) => [
    p.id,
    p.parent_id ?? '',
    p.parent?.title ?? '',
    p.title,
    promptBody(p),
    p.description,
    p.category,
    p.visibility,
    (p.teams || []).join('; '),
    (p.tags || []).join('; '),
    (p.variables || []).map((v) => v.name).join('; '),
    p.created_by,
    p.created_at,
    p.updated_at,
    p.last_used_at ?? '',
    p._review_stats?.count ?? 0,
    p._review_stats?.avg ?? 0,
    p._version_count ?? p.versions?.length ?? 1,
  ]);
  const lines = [
    EXPORT_COLUMNS.join(','),
    ...rows.map((row) => row.map(escapeCsvField).join(',')),
  ];
  return lines.join('\r\n');
}

export function downloadCsv(filename, csv) {
  const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export function exportPromptsCsv(prompts) {
  const date = new Date().toISOString().slice(0, 10);
  downloadCsv(`prompts-export-${date}.csv`, promptsToCsv(prompts));
}
