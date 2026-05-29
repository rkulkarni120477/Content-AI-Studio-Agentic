import { api } from '@services/apiClient';
import { CDD, COURSES } from '@services/endpoints';

export const cddService = {
  listCdds:      (courseId)       => api.get(CDD.LIST(courseId)),
  listAllCdds:   ()               => api.get(CDD.LIST_ALL),
  getCdd:        (id)             => api.get(CDD.GET(id)),
  generateCdd:   (data)           => api.post(CDD.GENERATE, data),
  getVersions:   (id)             => api.get(CDD.VERSIONS(id)),
  getVersion:    (id, v)          => api.get(CDD.GET_VERSION(id, v)),
  commitVersion: (id, data)       => api.post(CDD.COMMIT_VERSION(id), data),
  setActiveCdd:  (cddId, courseId)=> api.post(CDD.SET_ACTIVE(cddId), { course_id: courseId }),
  exportCdd:     (id, format)     => api.download(CDD.EXPORT(id), { params: { format } }),
};
