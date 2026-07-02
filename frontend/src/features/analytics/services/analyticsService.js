import { api } from '@services/apiClient';
import { ANALYTICS, USERS } from '@services/endpoints';

/** Normalize list endpoints — API returns a JSON array; guard wrapped shapes. */
function asList(data) {
  if (Array.isArray(data)) return data;
  if (data?.items && Array.isArray(data.items)) return data.items;
  return [];
}

export const analyticsService = {
  getSummary:           (p)  => api.get(ANALYTICS.SUMMARY, { params: p }),
  getProjectAnalytics:  ()   => api.get(ANALYTICS.PROJECTS),
  getPromptPerf:        (p)  => api.get(ANALYTICS.PROMPT_PERF, { params: p }),
  getQualityTrends:     (p)  => api.get(ANALYTICS.QUALITY_TRENDS, { params: p }),
  getGenerationHistory: (p)  => api.get(ANALYTICS.GENERATION_HISTORY, { params: p }),
  getPromptVersionHistory: (p) =>
    api.get(ANALYTICS.HISTORY_PROMPT_VERSIONS, { params: p }).then(asList),
  getDocumentUploadHistory: (p) =>
    api.get(ANALYTICS.HISTORY_DOC_UPLOADS, { params: p }).then(asList),
  getCddBlueprintHistory: (p) =>
    api.get(ANALYTICS.HISTORY_CDD_BP, { params: p }).then(asList),
  getFeedbackSummary:   ()   => api.get(ANALYTICS.FEEDBACK_SUMMARY),
  getFeedback:          (p)  => api.get(ANALYTICS.FEEDBACK, { params: p }),
  getReviews:           (p)  => api.get(ANALYTICS.REVIEWS, { params: p }),
  getSystemLogs:        (p)  => api.get(ANALYTICS.SYSTEM_LOGS, { params: p }),
  getLlmCost:           (p)  => api.get(ANALYTICS.LLM_COST, { params: p }),
  getUsage:             (p)  => api.get(ANALYTICS.USAGE, { params: p }),
  getAuditTrailFilters: () => api.get(ANALYTICS.AUDIT_TRAIL_FILTERS),
  getAuditTrail:        (p)  => api.get(ANALYTICS.AUDIT_TRAIL, { params: p }),
  exportAudit:          (p)  => api.download(ANALYTICS.AUDIT_EXPORT, { params: p }),
  listUsers:            ()   => api.get(USERS.LIST),
  createUser:           (d)  => api.post(USERS.CREATE, d),
  toggleUserActive:     (id, isActive) => api.put(USERS.TOGGLE_ACTIVE(id), { is_active: isActive }),
};
