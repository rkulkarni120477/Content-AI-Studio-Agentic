import { api } from '@services/apiClient';
import { WORKFLOW } from '@services/endpoints';

export const workflowService = {
  listBlocks:     (filters)          => api.get(WORKFLOW.LIST, { params: filters }),
  getPending:     ()                 => api.get(WORKFLOW.PENDING_REVIEWS),
  approve:        (id, data)         => api.post(WORKFLOW.APPROVE(id), data || {}),
  requestChanges: (id, data)         => api.post(WORKFLOW.REQUEST_CHANGES(id), data || {}),
  publish:        (id)               => api.post(WORKFLOW.PUBLISH(id)),
  archive:        (id)               => api.post(WORKFLOW.ARCHIVE(id)),
  bulkApprove:    (ids)              => api.post(WORKFLOW.BULK_APPROVE, { block_ids: ids }),
  getSlaStatus:   (id)               => api.get(WORKFLOW.SLA_STATUS(id)),
};
