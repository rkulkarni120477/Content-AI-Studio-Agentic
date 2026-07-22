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

  deleteItem: (itemId) => api.delete(FEEDBACK.DELETE_ITEM(itemId)),

  bulkDelete: (ids) => api.post(FEEDBACK.BULK_DELETE, { ids }),
};
