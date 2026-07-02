import { api } from '@services/apiClient';
import { CLUSTERS, CLUSTER_PROMPTS } from '@services/endpoints';

export const clusterPromptService = {
  list:           ()                 => api.get(CLUSTER_PROMPTS.LIST),
  listUnassigned: ()                 => api.get(CLUSTER_PROMPTS.UNASSIGNED),
  listForCluster: (clusterId)        => api.get(CLUSTERS.PROMPTS(clusterId)),
  create:         (data)             => api.post(CLUSTER_PROMPTS.CREATE, data),
  remove:         (id)               => api.delete(CLUSTER_PROMPTS.DELETE(id)),
  aiGenerate:     (data)             => api.post(CLUSTER_PROMPTS.AI_GENERATE, data),
};
