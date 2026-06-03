import { api } from '@services/apiClient';
import { STYLES, DOCUMENTS } from '@services/endpoints';

export const styleService = {
  listStyles:       ()           => api.get(STYLES.LIST),
  getStyle:         (id)         => api.get(STYLES.GET(id)),
  createStyle:      (data)       => api.post(STYLES.CREATE, data),
  updateStyle:      (id, data)   => api.put(STYLES.UPDATE(id), data),
  deleteStyle:      (id)         => api.delete(STYLES.DELETE(id)),
  activateStyle:    (id)         => api.post(STYLES.ACTIVATE(id)),
  deactivateStyle:  (id)         => api.post(STYLES.DEACTIVATE(id)),
  uploadStyleDocs:  (id, formData) => api.upload(DOCUMENTS.STYLE_DOCS(id), formData),
  getVersions:      (id)         => api.get(STYLES.VERSIONS(id)),
  regenerateStyle:  (id)         => api.post(STYLES.REGENERATE(id)),
  listDocuments:    async (courseId, projectId) => {
    const res = await api.get(DOCUMENTS.LIST, {
      params: {
        course_id: courseId,
        project_id: projectId,
        page: 1,
        page_size: 100,
      },
    });
    return res.items || [];
  },
  getDocumentContent: (id)        => api.get(DOCUMENTS.CONTENT(id)),
  uploadDocuments:  (formData)   => api.upload(DOCUMENTS.UPLOAD, formData),
  deleteDocument:   (id)         => api.delete(DOCUMENTS.DELETE(id)),
};
