import { api } from '@services/apiClient';
import { CDD, COURSES } from '@services/endpoints';

function mapGeneratePayload(data) {
  return {
    course_id: data.course_id,
    project_id: data.project_id,
    course_title: data.course_title,
    document_title: data.document_title || undefined,
    estimated_duration_hours: data.estimated_duration_hours ?? data.duration_hours ?? 8,
    extra_instructions: data.extra_instructions || '',
    style_id: data.style_id ?? null,
    model_choice: data.model_choice,
    target_audience: data.target_audience || '',
    expert_domain: data.expert_domain || '',
    audience_category: data.audience_category || 'Professional/Corporate',
  };
}

export const cddService = {
  listCdds: async (courseId, params = {}) => {
    const query = {
      page: 1,
      page_size: 100,
      ...params,
    };
    if (courseId) query.course_id = courseId;
    const res = await api.get(CDD.LIST(), { params: query });
    return res?.items ?? (Array.isArray(res) ? res : []);
  },
  listAllCdds: async (params = {}) => {
    const res = await api.get(CDD.LIST_ALL, {
      params: { page: 1, page_size: 100, ...params },
    });
    return res.items || [];
  },
  /** Load CDD by course_design_documents.id (not course id). */
  getCdd:        (cddId)          => api.get(CDD.GET(cddId)),
  /** Load pinned CDD for a course — pass course id from /workspace/{courseId}/... */
  getActiveCddForCourse: (courseId) => api.get(COURSES.ACTIVE_CDD(courseId)),
  generateCdd:   async (data)     => {
    const created = await api.post(CDD.GENERATE, mapGeneratePayload(data));
    if (created?.cdd_id) {
      return api.get(CDD.GET(created.cdd_id));
    }
    return created;
  },
  getVersions:   (id)             => api.get(CDD.VERSIONS(id)),
  getVersion:    (id, v)          => api.get(CDD.GET_VERSION(id, v)),
  commitVersion: (id, data)       => api.post(CDD.COMMIT_VERSION(id), {
    version_tag: data.version_tag || data.tag || 'v-next',
    full_content: data.full_content,
    sections: data.sections || {},
    change_reason: data.change_reason || data.reason || '',
  }),
  setActiveCdd:  async (cddId, courseId) => {
    await api.post(CDD.PIN(cddId), { course_id: courseId });
    return api.get(CDD.GET(cddId));
  },
  exportCdd:     (id, format)     => api.download(CDD.EXPORT(id), { params: { format } }),
};
