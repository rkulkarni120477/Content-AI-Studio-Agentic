import { api } from '@services/apiClient';
import { BLUEPRINT } from '@services/endpoints';

export const blueprintService = {
  listBlueprints: async ({ courseId, projectId } = {}) => {
    const res = await api.get(BLUEPRINT.LIST, {
      params: {
        course_id: courseId,
        project_id: projectId,
        page: 1,
        page_size: 100,
      },
    });
    return res.items || [];
  },

  /** All blueprints — matches Streamlit generate page override dropdown. */
  listAllBlueprints: async () => {
    const pageSize = 100;
    let page = 1;
    let items = [];
    let total = 0;
    do {
      const res = await api.get(BLUEPRINT.LIST, {
        params: { page, page_size: pageSize },
      });
      const batch = res.items || [];
      items = items.concat(batch);
      total = res.total ?? items.length;
      page += 1;
    } while (items.length < total && page <= 20);
    return items;
  },

  getBlueprint: (id) => api.get(BLUEPRINT.GET(id)),

  generateBlueprint: async (data) => {
    const body = {
      course_id: data.course_id,
      project_id: data.project_id,
      cdd_id: data.cdd_id ?? null,
      selected_module: data.selected_module,
      extra_instructions: data.extra_instructions || '',
      style_id: data.style_id ?? null,
      model_choice: data.model_choice || 'GPT-5.4',
      teacher_mode: Boolean(data.teacher_mode),
      system_prompt_override: data.system_prompt_override || undefined,
      user_prompt_override: data.user_prompt_override || undefined,
    };
    const created = await api.post(BLUEPRINT.GENERATE, body);
    if (created?.blueprint_id) {
      return api.get(BLUEPRINT.GET(created.blueprint_id));
    }
    return created;
  },

  getVersions: (id) => api.get(BLUEPRINT.VERSIONS(id)),
  getVersion: (id, v) => api.get(BLUEPRINT.GET_VERSION(id, v)),

  activateVersion: (id, version) => api.post(BLUEPRINT.ACTIVATE_VERSION(id, version)),
  commitVersion: (id, data) => api.post(BLUEPRINT.COMMIT_VERSION(id), {
    version_tag: data.version_tag || data.tag || 'v-next',
    full_content: data.full_content,
    sections: data.sections || {},
    change_reason: data.change_reason || data.reason || '',
  }),

  pinBlueprint: async (bpId, courseId) => {
    await api.post(BLUEPRINT.PIN(bpId), { course_id: courseId });
    return api.get(BLUEPRINT.GET(bpId));
  },

  exportBlueprint: (id, format) => api.download(BLUEPRINT.EXPORT(id), { params: { format } }),
  getComponents: async (id) => {
    const res = await api.get(BLUEPRINT.PARSE_COMPONENTS(id));
    return res.components || res || [];
  },
};
