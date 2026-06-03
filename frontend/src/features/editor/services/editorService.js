import { api } from '@services/apiClient';
import { BLOCKS, EXPORT, PLAGIARISM, WORKFLOW, COURSES } from '@services/endpoints';

export const editorService = {
  listBlocks: async (courseId) => {
    const res = await api.get(BLOCKS.LIST_SCOPED, { params: { course_id: courseId, page_size: 500 } });
    return res.items || res || [];
  },
  getBlock:            (id)                => api.get(BLOCKS.GET(id)),
  updateBlock:         (id, data)          => api.put(BLOCKS.UPDATE(id), data),
  getBlockVersions:    (id)                => api.get(BLOCKS.VERSIONS(id)),
  restoreVersion:      (id, v)             => api.post(BLOCKS.RESTORE_VERSION(id, v)),
  exportBlock:         (id, format)        => api.download(BLOCKS.EXPORT(id), { params: { format } }),
  exportCourse:        (courseId, format)  => api.download(EXPORT.COURSE(courseId), { params: { format } }),
  triggerPlagiarism:   (blockId)           => api.post(PLAGIARISM.SCAN(blockId)),
  getPlagiarismStatus: (blockId)           => api.get(PLAGIARISM.STATUS(blockId)),
  // Workflow actions: action = 'submit' | 'approve' | 'request_changes' | 'publish' | 'archive' | 'reject'
  submitWorkflowAction: (blockId, action, data) => {
    const endpoints = {
      submit:           WORKFLOW.SUBMIT(blockId),
      approve:          WORKFLOW.APPROVE(blockId),
      request_changes:  WORKFLOW.REQUEST_CHANGES(blockId),
      reject:           WORKFLOW.REJECT(blockId),
      publish:          WORKFLOW.PUBLISH(blockId),
      archive:          WORKFLOW.ARCHIVE(blockId),
    };
    return api.post(endpoints[action], data || {});
  },
  validateCourse: (courseId) => api.post(COURSES.VALIDATE(courseId)),
};
