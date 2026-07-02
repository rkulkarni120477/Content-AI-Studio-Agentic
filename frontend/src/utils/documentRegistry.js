/** Document source types — matches Streamlit style.py document registry selectbox. */
export const DOCUMENT_SOURCE_TYPE_OPTIONS = [
  { value: 'guidelines', label: 'guidelines' },
  { value: 'checklist', label: 'checklist' },
  { value: 'chapter', label: 'chapter' },
  { value: 'specification', label: 'specification' },
  { value: 'outline', label: 'outline' },
  { value: 'assessment', label: 'assessment' },
  { value: 'rubric', label: 'rubric' },
  { value: 'reference', label: 'reference' },
  { value: 'style_reference', label: 'style_reference' },
  { value: 'general', label: 'general' },
];

export const DEFAULT_DOCUMENT_SOURCE_TYPE = 'guidelines';

/**
 * Derive registry KPIs from active list + analytics endpoints (no hardcoded counts).
 * Total comes from GET /api/v1/analytics/summary; distinct tags from upload history
 * (includes archived rows when total library size is within the history limit).
 */
export function computeDocumentRegistryStats(activeDocuments, summary, uploadHistory = []) {
  const activeCount = activeDocuments?.length ?? 0;
  const totalCount = summary?.documents ?? activeCount;
  const archivedCount = Math.max(0, totalCount - activeCount);

  const tagSet = new Set();
  for (const row of uploadHistory || []) {
    tagSet.add((row.tag || row.source_type || 'general').toLowerCase());
  }
  for (const doc of activeDocuments || []) {
    tagSet.add((doc.source_type || 'reference').toLowerCase());
  }

  return {
    total: totalCount,
    active: activeCount,
    archived: archivedCount,
    types: tagSet.size,
    distinctTags: Array.from(tagSet).sort(),
  };
}

export function normalizeDocumentsPayload(payload) {
  if (Array.isArray(payload)) {
    return { documents: payload, stats: null };
  }
  return {
    documents: payload?.documents ?? [],
    stats: payload?.stats ?? null,
  };
}
