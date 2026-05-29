import { api } from '@services/apiClient';
import { PROMPTS } from '@services/endpoints';

export const promptsService = {
  listPrompts:   ()        => api.get(PROMPTS.LIST),
  getPrompt:     (name)    => api.get(PROMPTS.GET(name)),
  commitPrompt:  (data)    => api.post(PROMPTS.CREATE, data),
  updatePrompt:  (name, d) => api.put(PROMPTS.UPDATE(name), d),
  getVersions:   (name)    => api.get(PROMPTS.VERSIONS(name)),
  commitVersion: (name, d) => api.post(PROMPTS.COMMIT_VERSION(name), d),
  setActive:     (name, v) => api.post(PROMPTS.SET_ACTIVE(name, v)),
  aiGenerate:    (data)    => api.post(PROMPTS.AI_GENERATE, data),
};
