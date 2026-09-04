import { api } from '@services/apiClient';
import { FEEDBACK, GENERATE } from '@services/endpoints';

export const feedbackService = {
  /** List active feedback items for a course. */
  listFeedback: async (courseId) => {
    const res = await api.get(FEEDBACK.LIST, {
      params: courseId ? { course_id: courseId } : {},
    });
    return res?.items ?? [];
  },

  /**
   * Upload a document and analyse it with AI.
   * @param {File} file
   * @param {number} courseId
   * @param {number|null|undefined} blueprintId — module scope; null/omit = entire course
   * @param {Function} [onProgress]
   */
  analyze: (file, courseId, blueprintId, onProgress) => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('course_id', courseId);
    if (blueprintId != null && blueprintId !== '') {
      formData.append('blueprint_id', String(blueprintId));
    }
    return api.upload(FEEDBACK.ANALYZE, formData, onProgress);
  },

  /** Generate AI recommendations for one or more feedback items.
   *  Optional `guidance` (steering text) and `modelChoice` (model override)
   *  apply to this run. Each item is a sequential LLM call, so allow a longer
   *  timeout than the default. Returns { items, recommended, failed }. */
  recommend: (itemIds, { guidance, modelChoice } = {}) => {
    const body = { item_ids: itemIds };
    if (guidance && guidance.trim()) body.guidance = guidance.trim();
    if (modelChoice) body.model_choice = modelChoice;
    return api.post(FEEDBACK.RECOMMEND, body, { timeout: 300_000 });
  },

  /** Remap an item to a module (null = entire course). */
  updateItem: (itemId, { blueprintId }) =>
    api.patch(FEEDBACK.UPDATE_ITEM(itemId), {
      blueprint_id: blueprintId == null || blueprintId === '' ? null : Number(blueprintId),
    }),

  /** Queue applying selected items to regenerate a module's blocks.
   *  Returns a job handle { job_id, status, status_url }; the caller polls
   *  getJobStatus until terminal, then reads applyResult for the summary.
   *  Regeneration is one LLM call per block and runs in a background worker,
   *  so this enqueue call itself returns immediately. */
  apply: ({ itemIds, blueprintId }) =>
    api.post(FEEDBACK.APPLY, {
      item_ids: itemIds,
      blueprint_id: blueprintId == null || blueprintId === '' ? null : Number(blueprintId),
    }),

  /** Poll a background job's status. Short per-request timeout: a status read is
   *  one indexed DB read, so a stalled poll should retry next tick, not occupy
   *  the app-wide two-minute default. */
  getJobStatus: (jobId) => api.get(GENERATE.JOB_STATUS(jobId), { timeout: 15_000 }),

  /** Read the regenerate summary of a completed apply-feedback job. */
  applyResult: (jobId) => api.get(FEEDBACK.APPLY_RESULT(jobId)),

  deleteItem: (itemId) => api.delete(FEEDBACK.DELETE_ITEM(itemId)),

  bulkDelete: (ids) => api.post(FEEDBACK.BULK_DELETE, { ids }),
};
