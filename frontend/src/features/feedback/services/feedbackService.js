import { api } from '@services/apiClient';
import { FEEDBACK } from '@services/endpoints';

export const feedbackService = {
  /** List active feedback items for a course. */
  listFeedback: async (courseId) => {
    const res = await api.get(FEEDBACK.LIST, {
      params: courseId ? { course_id: courseId } : {},
    });
    return res?.items ?? [];
  },

  /** Upload a document and analyse it with AI. Returns { document, items }. */
  analyze: (file, courseId, onProgress) => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('course_id', courseId);
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

  deleteItem: (itemId) => api.delete(FEEDBACK.DELETE_ITEM(itemId)),

  bulkDelete: (ids) => api.post(FEEDBACK.BULK_DELETE, { ids }),
};
