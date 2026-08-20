import { api } from '@services/apiClient';
import { PROMPTS } from '@services/endpoints';

export const promptsService = {
  listPrompts: async (params = {}) => {
    const res = await api.get(PROMPTS.LIST, {
      params: { page: 1, page_size: 100, ...params },
    });
    return res.items || [];
  },
  getPromptDetail: (id) => api.get(PROMPTS.GET(id)),
  commitPrompt: async (data) => {
    return api.post(PROMPTS.CREATE, {
      name: data.name,
      description: data.description || '',
      tags: data.tags || data.component_type || '',
      component_type: data.component_type,
      system_prompt: data.system_prompt,
      user_prompt_template: data.user_prompt_template,
      change_reason: data.change_reason || 'Initial commit.',
      project_id: data.project_id,
    });
  },
  updatePrompt: (id, d) => api.put(PROMPTS.UPDATE(id), d),
  getVersions: (id) => api.get(PROMPTS.VERSIONS(id)),
  commitVersion: (id, d) => api.post(PROMPTS.COMMIT_VERSION(id), d),
  aiSuggest: (data) => api.post(PROMPTS.AI_SUGGEST, data),
  aiGenerate: (data) => api.post(PROMPTS.AI_GENERATE, data),
};
