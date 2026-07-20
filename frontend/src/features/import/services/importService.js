import { api } from '@services/apiClient';
import { IMPORTS, GENERATE } from '@services/endpoints';

/**
 * Reverse-pipeline (Canvas IMSCC import) API calls.
 *
 * Uses the shared api client exactly like dashboardService — no hand-rolled
 * fetch/axios. Progress polling reuses the existing jobs endpoint rather than a
 * parallel channel. See reverse_cas.md §S4.
 */
export const importService = {
  /** Capability probe — 404 (route not mounted) means the flag is off. */
  health: () => api.get(IMPORTS.HEALTH),

  /** Pre-flight: structure counts + warnings, no DB writes. */
  validatePackage: (file) => {
    const form = new FormData();
    form.append('file', file);
    return api.upload(IMPORTS.VALIDATE, form);
  },

  /** Create the course shell + enqueue the import job. Returns {course_id, import_id, job_id}. */
  startImport: (projectId, { name, clusterId, file }) => {
    const form = new FormData();
    form.append('file', file);
    form.append('name', name);
    if (clusterId != null && clusterId !== '') form.append('cluster_id', String(clusterId));
    return api.upload(IMPORTS.CREATE(projectId), form);
  },

  /** Import record + latest status/warnings. */
  getImport: (importId) => api.get(IMPORTS.GET(importId)),

  /** Re-run reverse-gen (Blueprint/CDD/Style) only. Returns {course_id, import_id, job_id}. */
  retryImport: (importId) => api.post(IMPORTS.RETRY(importId)),

  /** Cancel the import's active job (best-effort). */
  cancelImport: (importId) => api.post(IMPORTS.CANCEL(importId)),

  /** Shared job-status endpoint — same one the generate flow polls. */
  getJobStatus: (jobId) => api.get(GENERATE.JOB_STATUS(jobId)),
};
