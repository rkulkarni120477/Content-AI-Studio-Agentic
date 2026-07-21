import { api } from '@services/apiClient';
import { STYLES, DOCUMENTS } from '@services/endpoints';

export const styleService = {
  listStyles: ({ projectId, courseId, page = 1, pageSize = 100 } = {}) =>
    api.get(STYLES.LIST, {
      params: {
        page,
        page_size: pageSize,
        ...(projectId != null ? { project_id: projectId } : {}),
        ...(courseId != null ? { course_id: courseId } : {}),
      },
    }),
  getStyle:         (id)         => api.get(STYLES.GET(id)),
  createStyle:      (data)       => api.post(STYLES.CREATE, data),
  updateStyle:      (id, data)   => api.put(STYLES.UPDATE(id), data),
  deleteStyle:      (id)         => api.delete(STYLES.DELETE(id)),
  activateStyle:    (id, body = {}) => api.post(STYLES.ACTIVATE(id), body),
  deactivateStyle:  (id)         => api.post(STYLES.DEACTIVATE(id)),
  uploadStyleDocs:  (id, formData) => api.upload(DOCUMENTS.STYLE_DOCS(id), formData),
  regenerateStyle:  (id, body = {}) => api.post(STYLES.UNDERSTAND(id), body),
  /** Fetch full document library (paginated) for style file picker. */
  listAllDocuments: async () => {
    const pageSize = 100;
    let page = 1;
    let items = [];
    let total = 0;
    do {
      const res = await api.get(DOCUMENTS.LIST, { params: { page, page_size: pageSize } });
      const batch = res.items || [];
      items = items.concat(batch);
      total = res.total ?? items.length;
      page += 1;
    } while (items.length < total && page <= 50);
    return items;
  },
  getDocumentContent: (id)        => api.get(DOCUMENTS.CONTENT(id)),
  uploadDocuments:  (formData)   => api.upload(DOCUMENTS.UPLOAD, formData),
  /** Upload a single file to the global document library (Streamlit style create flow). */
  uploadLibraryFile: async (file, sourceType = 'style_reference') => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('source_type', sourceType);
    return api.upload(DOCUMENTS.UPLOAD, formData);
  },
  deleteDocument:   (id)         => api.delete(DOCUMENTS.DELETE(id)),
};
