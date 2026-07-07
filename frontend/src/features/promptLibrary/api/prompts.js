import { apiFetch, apiUrl, apiDownload } from './client';

export async function fetchPrompts(params) {
  const qs = new URLSearchParams(params).toString();
  const res = await apiFetch(`/api/prompts${qs ? `?${qs}` : ''}`);
  if (res.status === 401) throw new Error('unauthorized');
  return res.json();
}

export async function searchPrompts(params) {
  const qs = new URLSearchParams(params).toString();
  const res = await apiFetch(`/api/prompts/search?${qs}`);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'Search failed');
  return data;
}

export async function fetchMeta() {
  const res = await apiFetch('/api/meta');
  return res.json();
}

export async function fetchCategories() {
  const res = await apiFetch('/api/categories');
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'Failed to load categories');
  return data.categories || [];
}

export async function fetchTags(category) {
  const qs = category ? `?category=${encodeURIComponent(category)}` : '';
  const res = await apiFetch(`/api/tags${qs}`);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'Failed to load tags');
  return data.tags || [];
}

export async function fetchPrompt(id) {
  const res = await apiFetch(`/api/prompts/${id}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error('Failed to load prompt');
  return res.json();
}

export async function fetchRootPrompts() {
  return fetchPrompts({ roots_only: '1' });
}

export async function fetchChildPrompts(parentId) {
  return fetchPrompts({ parent_id: parentId, roots_only: '0' });
}

export async function createPrompt(body) {
  const res = await apiFetch('/api/prompts', { method: 'POST', body: JSON.stringify(body) });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || data.detail || 'Save failed');
  return data;
}

// Advisory dedup probe (never blocks): returns {id,title,kind,category} of a
// live same-kind prompt with identical normalized content, or null. A failed
// probe must not stop a save — callers treat errors as "no duplicate".
export async function checkDuplicate(content, kind = 'library', excludeId = null) {
  try {
    const res = await apiFetch('/api/prompts/duplicate-check', {
      method: 'POST',
      body: JSON.stringify({ content, kind, exclude_id: excludeId }),
    });
    if (!res.ok) return null;
    const data = await res.json();
    return data.duplicate_of || null;
  } catch {
    return null;
  }
}

// Promote a library prompt to an admin-managed pipeline prompt (Phase 12).
// The kind flips in place (same id, same version history) and stays inert
// for generation until the prompt is made a default or scope-locked.
export async function promotePrompt(id, body) {
  const res = await apiFetch(`/api/prompts/${id}/promote`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || data.detail || 'Promote failed');
  return data;
}

export async function updatePrompt(id, body) {
  const res = await apiFetch(`/api/prompts/${id}`, { method: 'PUT', body: JSON.stringify(body) });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || data.detail || 'Update failed');
  return data;
}

export async function deletePrompt(id) {
  const res = await apiFetch(`/api/prompts/${id}`, { method: 'DELETE' });
  if (!res.ok) throw new Error('Delete failed');
}

export async function duplicatePrompt(id) {
  const res = await apiFetch(`/api/prompts/${id}/duplicate`, { method: 'POST' });
  const data = await res.json();
  if (!res.ok) throw new Error('Duplicate failed');
  return data;
}

// Un-archive a soft-deleted prompt (doc §9 "unless explicitly enabled").
export async function restorePrompt(id) {
  const res = await apiFetch(`/api/prompts/${id}/restore`, { method: 'POST' });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || data.detail || 'Restore failed');
  return data;
}

export async function markPromptUsed(id) {
  await apiFetch(`/api/prompts/${id}/use`, { method: 'POST' });
}

export async function fetchReviews(promptId) {
  const res = await apiFetch(`/api/prompts/${promptId}/reviews`);
  return res.json();
}

export async function submitReview(promptId, rating, feedback) {
  const res = await apiFetch(`/api/prompts/${promptId}/reviews`, {
    method: 'POST',
    body: JSON.stringify({ rating, feedback }),
  });
  if (!res.ok) throw new Error('Failed to submit review');
}

export async function uploadAttachment(promptId, file) {
  const fd = new FormData();
  fd.append('file', file);
  const res = await apiFetch(`/api/prompts/${promptId}/attachments`, { method: 'POST', body: fd });
  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    throw new Error(e.error || e.detail || 'Upload failed');
  }
}

export async function deleteAttachment(promptId, attachmentId) {
  const res = await apiFetch(`/api/prompts/${promptId}/attachments/${attachmentId}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw new Error('Delete failed');
}

export function attachmentDownloadUrl(promptId, attachmentId) {
  return apiUrl(`/api/prompts/${promptId}/attachments/${attachmentId}/download`);
}

// Bearer-auth-safe download (replaces direct <a href> which cannot send the token).
export async function downloadAttachment(promptId, attachmentId, filename) {
  await apiDownload(`/api/prompts/${promptId}/attachments/${attachmentId}/download`, filename);
}
