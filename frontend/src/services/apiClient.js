import axios from 'axios';
import { tokenStorage } from '@utils/storage';
import { extractErrorMessage } from '@utils/helpers';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
const TIMEOUT_MS   = Number(import.meta.env.VITE_API_TIMEOUT_MS) || 120_000;

// ─── Axios Instance ───────────────────────────────────────────────────────────
const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: TIMEOUT_MS,
  headers: { 'Content-Type': 'application/json' },
});

// ─── Request Interceptor — Attach JWT ─────────────────────────────────────────
apiClient.interceptors.request.use(
  (config) => {
    const token = tokenStorage.get();
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error),
);

// ─── Response Interceptor — Global Error Normalization ────────────────────────
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error?.response?.status;

    // Session expired — dispatch logout event so auth slice can react
    if (status === 401) {
      tokenStorage.remove();
      window.dispatchEvent(new CustomEvent('auth:logout'));
    }

    // Rate limited
    if (status === 429) {
      return Promise.reject(normalizeError(error, 'Too many requests. Please wait and try again.'));
    }

    // Network error (no response)
    if (!error.response) {
      return Promise.reject(normalizeError(error, 'Network error. Check your connection.'));
    }

    return Promise.reject(normalizeError(error));
  },
);

// ─── Normalized API Error Shape ───────────────────────────────────────────────
function normalizeError(error, overrideMessage) {
  const status  = error?.response?.status;
  const message = overrideMessage || extractErrorMessage(error);
  const data    = error?.response?.data;

  const normalized = new Error(message);
  normalized.status        = status;
  normalized.originalError = error;
  normalized.data          = data;
  normalized.isApiError    = true;
  return normalized;
}

// ─── Convenience Wrappers ─────────────────────────────────────────────────────
export const api = {
  get:    (url, config)         => apiClient.get(url, config).then((r) => r.data),
  post:   (url, data, config)   => apiClient.post(url, data, config).then((r) => r.data),
  put:    (url, data, config)   => apiClient.put(url, data, config).then((r) => r.data),
  patch:  (url, data, config)   => apiClient.patch(url, data, config).then((r) => r.data),
  delete: (url, config)         => apiClient.delete(url, config).then((r) => r.data),

  // For file uploads / form-data
  upload: (url, formData, onProgress) =>
    apiClient.post(url, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (e) => {
        if (onProgress && e.total) {
          onProgress(Math.round((e.loaded / e.total) * 100));
        }
      },
    }).then((r) => r.data),

  // For binary downloads (DOCX, PDF)
  download: (url, config) =>
    apiClient.get(url, { responseType: 'blob', ...config }).then((r) => r),
};

export default apiClient;
