// Pipeline prompt admin API — /api/v1/prompts (the integer-keyed registry API,
// NOT the /api/v1/prompt-library mount that client.js rewrites onto).
//
// The console reads pipeline rows through the Library browse/detail endpoints
// (admins only; serialized with the additive `pipeline` block) and WRITES them
// here: commits go through the approval gate (admins instant-deploy, everyone
// else lands a draft), activation/default-flag changes are admin-only
// (prompt.pipeline.edit). Errors arrive in the AppError envelope
// { error: { code, message, detail } }.
import { tokenStorage } from '@utils/storage';

const API_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/$/, '');

async function pipelineFetch(path, init = {}) {
  const headers = new Headers(init.headers || {});
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const token = tokenStorage.get();
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  const res = await fetch(`${API_BASE}/api/v1/prompts${path}`, { ...init, headers });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body?.error?.message || body?.detail || `Request failed (${res.status})`);
  }
  return res.status === 204 ? null : res.json();
}

// Create a pipeline registry row (optionally with an activated v1 when both
// prompt texts are supplied). `name` is the unique registry slug.
export function createPipelinePrompt({ name, description, componentType, variant, systemPrompt, userPromptTemplate, changeReason }) {
  return pipelineFetch('', {
    method: 'POST',
    body: JSON.stringify({
      name,
      description: description || '',
      component_type: componentType || null,
      variant: variant || null,
      system_prompt: systemPrompt || null,
      user_prompt_template: userPromptTemplate || null,
      change_reason: changeReason || 'Initial commit.',
    }),
  });
}

// Update registry metadata. component_type/variant re-key generation
// resolution — admin-only server-side; empty string clears to NULL.
export function updatePipelineMeta(id, { description, componentType, variant }) {
  const body = {};
  if (description !== undefined) body.description = description;
  if (componentType !== undefined) body.component_type = componentType;
  if (variant !== undefined) body.variant = variant;
  return pipelineFetch(`/${id}`, { method: 'PUT', body: JSON.stringify(body) });
}

// Commit a new version. Admins instant-deploy; reviewers land an inactive
// draft the state endpoint later activates. `version` is the tag label ("v3").
export function commitPipelineVersion(id, { version, systemPrompt, userPromptTemplate, changeReason }) {
  return pipelineFetch(`/${id}/versions`, {
    method: 'POST',
    body: JSON.stringify({
      version,
      system_prompt: systemPrompt,
      user_prompt_template: userPromptTemplate,
      change_reason: changeReason || '',
    }),
  });
}

// draft → in_review → approved → active (rejection back to draft).
// Transitioning to 'active' deploys the version. Admin-only.
export function setPipelineVersionState(id, versionLabel, state) {
  return pipelineFetch(`/${id}/versions/${encodeURIComponent(versionLabel)}/state`, {
    method: 'POST',
    body: JSON.stringify({ state }),
  });
}

// Make this row the default for its (component_type, variant) — demotes the
// current default — or clear the flag. Admin-only.
export function setPipelineDefault(id, isDefault) {
  return pipelineFetch(`/${id}/default`, {
    method: 'PUT',
    body: JSON.stringify({ is_default: isDefault }),
  });
}
