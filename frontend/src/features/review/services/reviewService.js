import { api } from '@services/apiClient';
import { REVIEW_CHECKLISTS, REVIEWS, GENERATE } from '@services/endpoints';

/**
 * CE Agent Review — checklist management API (Step 1).
 * The review run itself is added in later steps.
 */
export const reviewService = {
  /** The active checklist for a project (or the caller's tenant), or null. */
  getActiveChecklist: (projectId = null) =>
    api.get(REVIEW_CHECKLISTS.ACTIVE, { params: projectId ? { project_id: projectId } : {} }),

  /** List checklists (active first), optionally scoped to a project. */
  listChecklists: (includeArchived = false, projectId = null) =>
    api.get(REVIEW_CHECKLISTS.LIST, {
      params: { include_archived: includeArchived, ...(projectId ? { project_id: projectId } : {}) },
    }),

  /** One checklist with its rules. */
  getChecklist: (id) => api.get(REVIEW_CHECKLISTS.GET(id)),

  /**
   * Upload a checklist file. Splitting runs as a background job — returns a job
   * handle { job_id, status, status_url }; poll getJobStatus until terminal.
   * @param {File} file
   * @param {{ name?: string, modelChoice?: string }} [opts]
   * @param {Function} [onProgress]
   */
  uploadChecklist: (file, { name, modelChoice, projectId } = {}, onProgress) => {
    const formData = new FormData();
    formData.append('file', file);
    if (name && name.trim()) formData.append('name', name.trim());
    if (modelChoice) formData.append('model_choice', modelChoice);
    if (projectId) formData.append('project_id', String(projectId));   // needed for platform admins
    return api.upload(REVIEW_CHECKLISTS.UPLOAD, formData, onProgress);
  },

  /** Edit one rule (any subset of fields). */
  updateItem: (checklistId, itemId, patch) =>
    api.patch(REVIEW_CHECKLISTS.UPDATE_ITEM(checklistId, itemId), patch),

  /** Delete one rule (hard delete). */
  deleteItem: (checklistId, itemId) => api.delete(REVIEW_CHECKLISTS.UPDATE_ITEM(checklistId, itemId)),

  /** Delete several rules (hard delete). */
  bulkDeleteItems: (checklistId, itemIds) =>
    api.post(REVIEW_CHECKLISTS.BULK_DELETE_ITEMS(checklistId), { item_ids: itemIds }),

  /** Delete the whole checklist (hard delete — rules + source file). */
  deleteChecklist: (checklistId) => api.delete(REVIEW_CHECKLISTS.GET(checklistId)),

  // ── Review runs ─────────────────────────────────────────────────────────────

  /** Start (or reuse) a review of a generation. Returns
   *  { review_id, run_status, reused, job_id?, status_url? }. */
  startReview: (generationId, modelChoice) =>
    api.post(REVIEWS.START, {
      generation_id: generationId,
      ...(modelChoice ? { model_choice: modelChoice } : {}),
    }),

  /** Get one review run's status/results. */
  getReview: (reviewId) => api.get(REVIEWS.GET(reviewId)),

  /** A generation's review history (newest first). */
  listReviews: (generationId, limit = 20) =>
    api.get(REVIEWS.LIST, { params: { generation_id: generationId, limit } }),

  /** Non-pass checklist results for a review. */
  getChecklistResults: (reviewId) => api.get(REVIEWS.CHECKLIST_RESULTS(reviewId)),

  /** Detected issues for a review (open first, most severe first). */
  getFindings: (reviewId) => api.get(REVIEWS.FINDINGS(reviewId)),

  /** Apply one finding's fix. Returns the updated finding. */
  applyFinding: (reviewId, findingId) => api.post(REVIEWS.APPLY_FINDING(reviewId, findingId)),

  /** Apply selected findings, or all eligible when findingIds is null. Returns a summary. */
  applyFindings: (reviewId, findingIds = null) =>
    api.post(REVIEWS.APPLY_FINDINGS(reviewId), { finding_ids: findingIds }),

  /** Dismiss a finding. Returns the updated finding. */
  dismissFinding: (reviewId, findingId, reason) =>
    api.post(REVIEWS.DISMISS_FINDING(reviewId, findingId), reason ? { reason } : {}),

  /** Poll a background job's status (short timeout — one indexed DB read). */
  getJobStatus: (jobId) => api.get(GENERATE.JOB_STATUS(jobId), { timeout: 15_000 }),
};
