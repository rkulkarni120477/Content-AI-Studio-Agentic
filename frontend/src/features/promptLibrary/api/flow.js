// Flow-view data access — the workflow hierarchy (projects/courses, host API)
// plus the pipeline registry's fixings endpoints (/api/v1/prompts). Neither
// lives under the /api/v1/prompt-library mount, so this module does its own
// fetch rather than client.js's remapping apiFetch.
import { tokenStorage } from '@utils/storage';

const API_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/$/, '');

async function hostFetch(path, init = {}) {
  const headers = new Headers(init.headers || {});
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const token = tokenStorage.get();
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body?.error?.message || body?.detail || `Request failed (${res.status})`);
  }
  return res.status === 204 ? null : res.json();
}

// ── Workflow hierarchy (host app) ──────────────────────────────────────────────
export async function listProjects() {
  const data = await hostFetch('/api/v1/projects?page_size=100');
  return data.items || data || [];
}

export async function listProjectCourses(projectId) {
  const data = await hostFetch(`/api/v1/projects/${projectId}/courses?page_size=200`);
  return data.items || data || [];
}

// ── Course-grouped view (Phase 11) ─────────────────────────────────────────────
// One batch call: every course with the prompt each pipeline slot resolves to
// (scope locks → component defaults → file fallback), server-computed with the
// same semantics as /fixings/resolve.
export async function fetchPromptsByCourse(projectId) {
  const qs = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
  const data = await hostFetch(`/api/v1/prompts/by-course${qs}`);
  return data.courses || [];
}

// ── Pipeline registry (fixings + per-component prompt pool) ───────────────────
export async function listComponentPrompts(component) {
  const data = await hostFetch(`/api/v1/prompts?component=${encodeURIComponent(component)}&page_size=100`);
  return data.items || [];
}

// Every live prompt, all components — the request form's "Prompt to update"
// picker. The prompt-library list endpoint hides pipeline rows from
// non-managers (they browse via the Courses tab instead), but a requester may
// reference any prompt; this host registry list only needs prompts.view.
export async function listAllPrompts() {
  const items = [];
  let page = 1;
  for (;;) {
    const data = await hostFetch(`/api/v1/prompts?page=${page}&page_size=100`);
    const batch = data.items || [];
    items.push(...batch);
    if (!batch.length || items.length >= (data.total ?? items.length) || page >= 10) break;
    page += 1;
  }
  return items;
}

// All scope locks referencing a prompt — the detail page's "used by" facets.
// scope_name carries the bound course/cluster/project name (null for global).
export async function listPromptFixings(promptId) {
  return hostFetch(`/api/v1/prompts/fixings?prompt_id=${encodeURIComponent(promptId)}`);
}

// Reuse is by reference (a PromptFixing row), never a copy. Admins bind any
// pipeline prompt; other roles only prompts whose active version is approved —
// both enforced server-side, errors surfaced to the caller.
export async function bindFixing({ component, scopeLevel, projectId, clusterId, courseId, promptId }) {
  return hostFetch('/api/v1/prompts/fixings', {
    method: 'PUT',
    body: JSON.stringify({
      component,
      scope_level: scopeLevel,
      project_id: projectId ?? null,
      cluster_id: clusterId ?? null,
      course_id: courseId ?? null,
      prompt_id: promptId,
    }),
  });
}

export async function unbindFixing({ component, scopeLevel, projectId, clusterId, courseId }) {
  const qs = new URLSearchParams({ component, scope_level: scopeLevel });
  if (projectId) qs.set('project_id', projectId);
  if (clusterId) qs.set('cluster_id', clusterId);
  if (courseId) qs.set('course_id', courseId);
  return hostFetch(`/api/v1/prompts/fixings?${qs}`, { method: 'DELETE' });
}
