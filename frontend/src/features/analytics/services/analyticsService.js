import { api } from '@services/apiClient';
import { ANALYTICS, USERS } from '@services/endpoints';

export const analyticsService = {
  getSummary:      (p)  => api.get(ANALYTICS.SUMMARY, { params: p }),
  getUsage:        (p)  => api.get(ANALYTICS.USAGE, { params: p }),
  getUsageByModel: (p)  => api.get(ANALYTICS.USAGE_BY_MODEL, { params: p }),
  getUsageByProject:(p) => api.get(ANALYTICS.USAGE_BY_PROJECT, { params: p }),
  getUsageByUser:  (p)  => api.get(ANALYTICS.USAGE_BY_USER, { params: p }),
  getMonthlyUsage: (p)  => api.get(ANALYTICS.USAGE_MONTHLY, { params: p }),
  getFeedback:     (p)  => api.get(ANALYTICS.FEEDBACK, { params: p }),
  getReviews:      (p)  => api.get(ANALYTICS.REVIEWS, { params: p }),
  getAuditTrail:   (p)  => api.get(ANALYTICS.AUDIT_TRAIL, { params: p }),
  exportAudit:     (p)  => api.download(ANALYTICS.AUDIT_EXPORT, { params: p }),
  getPromptPerf:   (p)  => api.get(ANALYTICS.PROMPT_PERF, { params: p }),
  getCost:         (p)  => api.get(ANALYTICS.COST, { params: p }),
  listUsers:       ()   => api.get(USERS.LIST),
  createUser:      (d)  => api.post(USERS.CREATE, d),
  toggleUserActive:(id) => api.post(USERS.TOGGLE_ACTIVE(id)),
};
