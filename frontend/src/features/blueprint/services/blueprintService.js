import { api } from '@services/apiClient';
import { BLUEPRINT } from '@services/endpoints';

export const blueprintService = {
  listBlueprints:      (courseId)              => api.get(BLUEPRINT.LIST(courseId)),
  getBlueprint:        (id)                    => api.get(BLUEPRINT.GET(id)),
  generateBlueprint:   (data)                  => api.post(BLUEPRINT.GENERATE, data),
  getVersions:         (id)                    => api.get(BLUEPRINT.VERSIONS(id)),
  getVersion:          (id, v)                 => api.get(BLUEPRINT.GET_VERSION(id, v)),
  commitVersion:       (id, data)              => api.post(BLUEPRINT.COMMIT_VERSION(id), data),
  setActiveBlueprint:  (bpId, courseId)        => api.post(BLUEPRINT.SET_ACTIVE(bpId), { course_id: courseId }),
  exportBlueprint:     (id, format)            => api.download(BLUEPRINT.EXPORT(id), { params: { format } }),
  getComponents:       (id)                    => api.get(BLUEPRINT.PARSE_COMPONENTS(id)),
};
