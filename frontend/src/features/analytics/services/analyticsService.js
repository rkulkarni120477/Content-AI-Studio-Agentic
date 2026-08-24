import { api } from '@services/apiClient';
import { ANALYTICS, GENERATE, PROJECTS } from '@services/endpoints';

/** Normalize list endpoints — API returns a JSON array; guard wrapped shapes. */
function asList(data) {
  if (Array.isArray(data)) return data;
  if (data?.items && Array.isArray(data.items)) return data.items;
  return [];
}

export const analyticsService = {
  getSummary:           (p)  => api.get(ANALYTICS.SUMMARY, { params: p }),
  getProjectAnalytics:  ()   => api.get(ANALYTICS.PROJECTS),
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
  getLlmCost:           (p)  => api.get(ANALYTICS.LLM_COST, { params: p }),
  getUsage:             (p)  => api.get(ANALYTICS.USAGE, { params: p }),
  getAuditTrailFilters: () => api.get(ANALYTICS.AUDIT_TRAIL_FILTERS),
  getAuditTrail:        (p)  => api.get(ANALYTICS.AUDIT_TRAIL, { params: p }),
  getGenerationTrace:   (id) => api.get(GENERATE.TRACE(id)),
  getProjectCourses:    (projectId) =>
    api.get(PROJECTS.COURSES(projectId), { params: { page_size: 200 } }).then(asList),
  exportAudit:          (p)  => api.download(ANALYTICS.AUDIT_EXPORT, { params: p }),
};
