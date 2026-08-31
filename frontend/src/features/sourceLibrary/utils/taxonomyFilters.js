/**
 * Source Library taxonomy filter helpers.
 *
 * Filters are defined per client in DIS retrieval.source_ui.taxonomy_filters and
 * exposed to the UI as uiConfig.source_library.taxonomy_filters. CAS/DIS filter
 * server-side; DIS authorizes filter keys via the tenant Field Registry
 * (filter_options). This module maps UI keys onto the API / filter_options
 * contract without maintaining a per-field allowlist of supported taxonomy keys.
 */

/** Compatibility aliases: UI / YAML key → CAS/DIS storage query param. */
export const TAXONOMY_API_KEY_MAP = {
  module: 'module_name',
  day_number: 'day',
};

/**
 * Keys that appear in some tenant uiConfig but have no Source Library filter
 * path yet. Not an allowlist of supported fields — only known deferred keys.
 * (Empty after Phase 7 undeffer of AIM topic.)
 */
export const DEFERRED_TAXONOMY_KEYS = new Set([]);

/** Config/UI key → source_filter_options() response key (compat plurals / aliases). */
export const TAXONOMY_OPTIONS_KEY_MAP = {
  course_name: 'course_name',
  block: 'blocks',
  day: 'days',
  day_number: 'days',
  chapter: 'chapter',
  module: 'module',
  module_name: 'module',
  learning_objective: 'learning_objective',
};

/** UI "all topics / all blocks" style values — unconstrained, not literal filters. */
function isUnconstrainedFilterValue(value) {
  const normalized = String(value).trim().toLowerCase();
  return normalized === 'all' || normalized === '*' || normalized === 'any';
}

export function taxonomyFiltersFromUiConfig(uiConfig) {
  const raw = uiConfig?.source_library?.taxonomy_filters;
  if (!Array.isArray(raw)) return [];
  return raw.filter((entry) => {
    const key = String(entry?.key || '').trim();
    return key && !DEFERRED_TAXONOMY_KEYS.has(key);
  });
}

export function apiParamForTaxonomyKey(key) {
  const k = String(key || '').trim();
  return TAXONOMY_API_KEY_MAP[k] || k;
}

export function optionsKeyForTaxonomyKey(key) {
  const k = String(key || '').trim();
  return TAXONOMY_OPTIONS_KEY_MAP[k] || k;
}

/**
 * Build query params for listDocuments.
 * Maps UI filter keys (e.g. module) onto the CAS/DIS contract (module_name)
 * and drops empty / deferred / unconstrained ("all") values so pagination stays
 * server-owned. New registry filter_options fields need no allowlist edit here.
 */
export function toSourceLibraryApiFilters(filters = {}) {
  const out = {};
  for (const [key, value] of Object.entries(filters || {})) {
    if (value === undefined || value === null || value === '') continue;
    if (DEFERRED_TAXONOMY_KEYS.has(key)) continue;
    if (isUnconstrainedFilterValue(value)) continue;
    const apiKey = apiParamForTaxonomyKey(key);
    out[apiKey] = value;
  }
  return out;
}
