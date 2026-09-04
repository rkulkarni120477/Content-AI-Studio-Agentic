import { api } from '@services/apiClient';
import { SOURCE_LIBRARY } from '@services/endpoints';

export const sourceLibraryApi = {
  uiConfig: (params = {}) => api.get(SOURCE_LIBRARY.UI_CONFIG, { params }),
  profile: () => api.get(SOURCE_LIBRARY.ME),
  listDocuments: (params = {}) => api.get(SOURCE_LIBRARY.DOCUMENTS, { params }),
  getStructure: (jobId, params = {}) => api.get(SOURCE_LIBRARY.STRUCTURE(jobId), { params }),
  getOverview: (jobId, params = {}) => api.get(SOURCE_LIBRARY.OVERVIEW(jobId), { params }),
  getPages: (jobId, params = {}) => api.get(SOURCE_LIBRARY.PAGES(jobId), { params }),
  getUnits: (jobId, params = {}) => api.get(SOURCE_LIBRARY.UNITS(jobId), { params }),
  getUnitDetail: (jobId, unitId, params = {}) => api.get(SOURCE_LIBRARY.UNIT_DETAIL(jobId, unitId), { params }),
  searchSource: (jobId, params = {}) => api.get(SOURCE_LIBRARY.SEARCH(jobId), { params }),
  deleteDocument: (jobId, params = {}) => api.delete(SOURCE_LIBRARY.DELETE_DOCUMENT(jobId), { params }),
  uploadDocument: (formData, onProgress) => api.upload(SOURCE_LIBRARY.UPLOAD, formData, onProgress),
  /** What the server will accept. Fetched, never hardcoded — see get_upload_policy. */
  getUploadPolicy: () => api.get(SOURCE_LIBRARY.UPLOAD_POLICY),
  scanFolder: (payload, params = {}) => api.post(SOURCE_LIBRARY.FOLDER_SCAN, payload, { params }),
  retrieve: (purpose, payload) => api.post(SOURCE_LIBRARY.RETRIEVE(purpose), payload),
  getAccessConfig: () => api.get(SOURCE_LIBRARY.ACCESS_CONFIG),
  updateAccessConfig: (payload) => api.put(SOURCE_LIBRARY.ACCESS_CONFIG, payload),
  listGenerated: (params = {}) => api.get(SOURCE_LIBRARY.GENERATED, { params }),
  getGenerated: (id, params = {}) => api.get(SOURCE_LIBRARY.GENERATED_GET(id), { params }),
  activateGenerated: (id, params = {}) => api.post(SOURCE_LIBRARY.GENERATED_ACTIVATE(id), {}, { params }),
};

export default sourceLibraryApi;
