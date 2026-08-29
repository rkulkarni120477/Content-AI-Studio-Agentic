/**
 * Source Library taxonomy filter helpers.
 *
 * Filters are defined per client in DIS retrieval.source_ui.taxonomy_filters and
 * exposed to the UI as uiConfig.source_library.taxonomy_filters. CAS/DIS already
 * filter server-side; this module only maps UI config keys to the existing API
 * contract and filter_options keys.
 */

/** Cengage YAML uses `module`; CAS/DIS list endpoints expect `module_name`. */
export const TAXONOMY_API_KEY_MAP = {
  module: 'module_name',
};

/**
 * Keys with an end-to-end Source Library filter path today.
 * AIM `topic` is configured in YAML but has no index/API path yet — deferred.
 */
export const SERVER_SUPPORTED_TAXONOMY_KEYS = new Set([
  'course_name',
  'block',
  'day',
  'chapter',
  'module',
  'module_name',
  'learning_objective',
]);

/** Config/UI key → source_filter_options() response key. */
export const TAXONOMY_OPTIONS_KEY_MAP = {
  course_name: 'course_name',
  block: 'blocks',
  day: 'days',
  chapter: 'chapter',
  module: 'module',
  module_name: 'module',
  learning_objective: 'learning_objective',
};

export function taxonomyFiltersFromUiConfig(uiConfig) {
  const raw = uiConfig?.source_library?.taxonomy_filters;
  if (!Array.isArray(raw)) return [];
  return raw.filter((entry) => {
    const key = String(entry?.key || '').trim();
    return key && SERVER_SUPPORTED_TAXONOMY_KEYS.has(key);
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
 * and drops empty / unsupported values so pagination stays server-owned.
 */
export function toSourceLibraryApiFilters(filters = {}) {
  const out = {};
  for (const [key, value] of Object.entries(filters || {})) {
    if (value === undefined || value === null || value === '') continue;
    if (key === 'topic') continue;
    const apiKey = apiParamForTaxonomyKey(key);
    out[apiKey] = value;
  }
  return out;
}
