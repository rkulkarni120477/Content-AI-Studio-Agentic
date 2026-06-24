/**
 * Cluster Prompt — backend API dependency registry.
 *
 * Swagger/OpenAPI is the source of truth. These endpoints are now present in the
 * FastAPI Swagger spec (app/api/v1/routers/cluster_prompts.py).
 */

export const CLUSTER_PROMPT_REQUIRED_ENDPOINTS = [
  { method: 'GET',    path: '/api/v1/cluster-prompts',                              purpose: 'List all prompts (New Cluster multiselect)' },
  { method: 'GET',    path: '/api/v1/clusters/{cluster_id}/cluster-prompts',        purpose: 'List prompts for cluster (Style banner)' },
  { method: 'GET',    path: '/api/v1/cluster-prompts/unassigned',                   purpose: 'List unassigned prompts (View/Manage tab)' },
  { method: 'POST',   path: '/api/v1/cluster-prompts',                              purpose: 'Create cluster prompt' },
  { method: 'DELETE', path: '/api/v1/cluster-prompts/{prompt_id}',                  purpose: 'Soft-delete cluster prompt' },
  { method: 'POST',   path: '/api/v1/cluster-prompts/ai-generate',                  purpose: 'AI generate/refine prompt drafts' },
  { method: 'POST',   path: '/api/v1/projects/{project_id}/clusters', field: 'copy_prompt_ids', purpose: 'Copy prompts on cluster create' },
];

/** Cluster Prompt routes are live in Swagger — always true now. */
export function isClusterPromptApiAvailable() {
  return true;
}

export const CLUSTER_PROMPT_API_MESSAGE =
  'Cluster Prompt APIs are not yet exposed in Swagger. UI is ready; backend endpoints are required for save/load.';
