import { api } from '@services/apiClient';
import { WORKFLOW } from '@services/endpoints';

export const workflowService = {
  listBlocks: async (filters = {}) => {
    const params = { page_size: 500, page: 1, ...filters };
    const res = await api.get(WORKFLOW.LIST, { params });
    return res.items || res || [];
  },
  getSummary:       (filters) => api.get(WORKFLOW.SUMMARY, { params: filters }),
  submit:           (id, data) => api.post(WORKFLOW.SUBMIT(id), data || {}),
  getPending:       () => api.get(WORKFLOW.PENDING_REVIEWS),
  approve:          (id, data) => api.post(WORKFLOW.APPROVE(id), data || {}),
  requestChanges:   (id, data) => api.post(WORKFLOW.REQUEST_CHANGES(id), data || {}),
  reject:           (id, data) => api.post(WORKFLOW.REJECT(id), data || {}),
  publish:          (id) => api.post(WORKFLOW.PUBLISH(id)),
  archive:          (id) => api.post(WORKFLOW.ARCHIVE(id)),
  resetDraft:       (id) => api.post(WORKFLOW.RESET_DRAFT(id)),
  bulkApprove:      (ids) => api.post(WORKFLOW.BULK_APPROVE, { block_ids: ids }),
  bulkSubmit:       (ids, reviewerUsername) => api.post(WORKFLOW.BULK_SUBMIT, {
    block_ids: ids, reviewer_username: reviewerUsername,
  }),
  bulkPublish:      (ids) => api.post(WORKFLOW.BULK_PUBLISH, { block_ids: ids }),
  getEvents:        (id) => api.get(WORKFLOW.EVENTS(id)),
  getAdminBreakdown: () => api.get(WORKFLOW.ADMIN_BREAKDOWN),
};
