// Prompt Library API client — ported from the standalone app's api/client.ts.
//
// The standalone endpoints (/api/prompts, /api/requests, ...) are rewritten to the
// host mount point (/api/v1/prompt-library/...) and calls carry the host's Bearer
// JWT, so every per-resource api file works unchanged. Auth/login helpers are
// dropped — the host owns authentication.
import { tokenStorage } from '@utils/storage';

const API_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/$/, '');
const PL_PREFIX = '/api/v1/prompt-library';

export function apiUrl(path) {
  if (path.startsWith('http')) return path;
  let p = path.startsWith('/') ? path : `/${path}`;
  // Standalone routes all begin with /api/ — remap them onto the mounted prefix.
  p = p.replace(/^\/api\//, `${PL_PREFIX}/`);
  return `${API_BASE}${p}`;
}

export async function apiFetch(path, init = {}) {
  const headers = new Headers(init.headers || {});
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const token = tokenStorage.get();
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  return fetch(apiUrl(path), { ...init, headers });
}

// FastAPI raises {"detail": "..."} for plain errors and {"detail": {...}} when a
// handler carries structured data (the delete guard's PROMPT_IN_USE payload);
// the host's AppError envelope is {"error": {code, message, detail}}. One reader
// for all three, so a server-side reason always reaches the user instead of
// being flattened to a generic fallback or "[object Object]".
export function errorMessage(data, fallback = 'Request failed') {
  for (const v of [data?.detail, data?.error]) {
    if (typeof v === 'string' && v.trim()) return v;
    if (v && typeof v === 'object' && typeof v.message === 'string' && v.message.trim()) {
      return v.message;
    }
  }
  return fallback;
}

// Authenticated file download. Direct <a href> GETs cannot carry the Bearer token,
// so we fetch the response as a blob and trigger a browser download.
export async function apiDownload(path, filename) {
  const res = await apiFetch(path);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || err.error || 'Download failed');
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  if (filename) link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
