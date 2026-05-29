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
  addDocuments:     (id, docIds) => api.post(STYLES.ADD_DOCUMENTS(id), { document_ids: docIds }),
  getVersions:      (id)         => api.get(STYLES.VERSIONS(id)),
  regenerateStyle:  (id)         => api.post(STYLES.REGENERATE(id)),
  listDocuments:    ()           => api.get(DOCUMENTS.LIST),
  uploadDocuments:  (formData)   => api.upload(DOCUMENTS.UPLOAD, formData),
  deleteDocument:   (id)         => api.delete(DOCUMENTS.DELETE(id)),
};
