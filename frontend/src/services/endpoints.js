// ─── Auth ─────────────────────────────────────────────────────────────────────
export const AUTH = {
  LOGIN:            '/api/v1/auth/login',
  LOGOUT:           '/api/v1/auth/logout',
  ME:               '/api/v1/auth/me',
  REFRESH:          '/api/v1/auth/refresh',
};

// ─── Projects ─────────────────────────────────────────────────────────────────
export const PROJECTS = {
  LIST:             '/api/v1/projects',
  GET:              (id)      => `/api/v1/projects/${id}`,
  CREATE:           '/api/v1/projects',
  UPDATE:           (id)      => `/api/v1/projects/${id}`,
  DELETE:           (id)      => `/api/v1/projects/${id}`,
  COURSES:          (id)      => `/api/v1/projects/${id}/courses`,
  CLUSTERS:         (id)      => `/api/v1/projects/${id}/clusters`,
  USERS:            (id)      => `/api/v1/projects/${id}/users`,
  UNASSIGN_USER:    (id, user) => `/api/v1/projects/${id}/users/${encodeURIComponent(user)}`,
};

// ─── Clusters ─────────────────────────────────────────────────────────────────
export const CLUSTERS = {
  GET:              (id)      => `/api/v1/clusters/${id}`,
  CREATE:           (projectId) => `/api/v1/projects/${projectId}/clusters`,
  UPDATE:           (id)      => `/api/v1/clusters/${id}`,
  DELETE:           (id)      => `/api/v1/clusters/${id}`,
  COURSES:          (id)      => `/api/v1/clusters/${id}/courses`,
};

// ─── Courses ──────────────────────────────────────────────────────────────────
export const COURSES = {
  GET:              (id)      => `/api/v1/courses/${id}`,
  CREATE:           '/api/v1/courses',
  UPDATE:           (id)      => `/api/v1/courses/${id}`,
  DELETE:           (id)      => `/api/v1/courses/${id}`,
  SET_ACTIVE_CDD:   (id)      => `/api/v1/courses/${id}/active-cdd`,
  SET_ACTIVE_BP:    (id)      => `/api/v1/courses/${id}/active-blueprint`,
  USERS:            (id)      => `/api/v1/courses/${id}/users`,
  UNASSIGN_USER:    (id, user) => `/api/v1/courses/${id}/users/${encodeURIComponent(user)}`,
  VALIDATE:         (id)      => `/api/v1/courses/${id}/validate`,
};

// ─── Users ────────────────────────────────────────────────────────────────────
export const USERS = {
  LIST:             '/api/v1/users',
  GET:              (id)      => `/api/v1/users/${id}`,
  CREATE:           '/api/v1/users',
  UPDATE:           (id)      => `/api/v1/users/${id}`,
  TOGGLE_ACTIVE:    (id)      => `/api/v1/users/${id}/toggle-active`,
};

// ─── Styles ───────────────────────────────────────────────────────────────────
export const STYLES = {
  LIST:             '/api/v1/styles',
  GET:              (id)      => `/api/v1/styles/${id}`,
  CREATE:           '/api/v1/styles',
  UPDATE:           (id)      => `/api/v1/styles/${id}`,
  DELETE:           (id)      => `/api/v1/styles/${id}`,
  ACTIVATE:         (id)      => `/api/v1/styles/${id}/activate`,
  DEACTIVATE:       (id)      => `/api/v1/styles/${id}/deactivate`,
  ADD_DOCUMENTS:    (id)      => `/api/v1/styles/${id}/documents`,
  VERSIONS:         (id)      => `/api/v1/styles/${id}/versions`,
  REGENERATE:       (id)      => `/api/v1/styles/${id}/regenerate`,
};

// ─── Documents ────────────────────────────────────────────────────────────────
export const DOCUMENTS = {
  LIST:             '/api/v1/documents',
  GET:              (id)      => `/api/v1/documents/${id}`,
  CONTENT:          (id)      => `/api/v1/documents/${id}/content`,
  UPLOAD:           '/api/v1/documents/upload',
  DELETE:           (id)      => `/api/v1/documents/${id}`,
  STYLE_DOCS:       (styleId) => `/api/v1/styles/${styleId}/documents`,
};

// ─── CDD ──────────────────────────────────────────────────────────────────────
export const CDD = {
  LIST:             () => `/api/v1/cdd`,
  LIST_ALL:         '/api/v1/cdd',
  GET:              (id)       => `/api/v1/cdd/${id}`,
  CREATE:           '/api/v1/cdd',
  GENERATE:         '/api/v1/cdd/generate',
  VERSIONS:         (id)       => `/api/v1/cdd/${id}/versions`,
  GET_VERSION:      (id, v)    => `/api/v1/cdd/${id}/versions/${v}`,
  COMMIT_VERSION:   (id)       => `/api/v1/cdd/${id}/versions`,
  PIN:              (id)       => `/api/v1/cdd/${id}/pin`,
  SET_ACTIVE:       (id)       => `/api/v1/cdd/${id}/pin`,
  EXPORT:           (id)       => `/api/v1/cdd/${id}/export`,
};

// ─── Blueprint ────────────────────────────────────────────────────────────────
export const BLUEPRINT = {
  LIST:             '/api/v1/blueprints',
  GET:              (id)       => `/api/v1/blueprints/${id}`,
  GENERATE:         '/api/v1/blueprints/generate',
  VERSIONS:         (id)       => `/api/v1/blueprints/${id}/versions`,
  GET_VERSION:      (id, v)    => `/api/v1/blueprints/${id}/versions/${v}`,
  COMMIT_VERSION:   (id)       => `/api/v1/blueprints/${id}/versions`,
  PIN:              (id)       => `/api/v1/blueprints/${id}/pin`,
  SET_ACTIVE:       (id)       => `/api/v1/blueprints/${id}/pin`,
  EXPORT:           (id)       => `/api/v1/blueprints/${id}/export`,
  PARSE_COMPONENTS: (id)       => `/api/v1/blueprints/${id}/components`,
  COMPLETION:       (id)       => `/api/v1/blueprints/${id}/completion-status`,
};

// ─── Generate ─────────────────────────────────────────────────────────────────
export const GENERATE = {
  LAUNCH:           '/api/v1/generations/launch',
  LIST:             '/api/v1/generations',
  GET:              (id)       => `/api/v1/generations/${id}`,
  MODULE_COMPLETION: (bpId)    => `/api/v1/blueprints/${bpId}/completion-status`,
  COURSE_COMPLETION: (courseId) => `/api/v1/generations/course/${courseId}/completion-status`,
  JOB_STATUS:       (jobId)    => `/api/v1/jobs/${jobId}`,
  JOB_CANCEL:       (jobId)    => `/api/v1/jobs/${jobId}/cancel`,
};

// ─── Blocks ───────────────────────────────────────────────────────────────────
export const BLOCKS = {
  LIST:             (genId)    => `/api/v1/generations/${genId}/blocks`,
  LIST_SCOPED:      '/api/v1/blocks',
  GET:              (id)       => `/api/v1/blocks/${id}`,
  UPDATE:           (id)       => `/api/v1/blocks/${id}`,
  VERSIONS:         (id)       => `/api/v1/blocks/${id}/versions`,
  RESTORE_VERSION:  (id, v)    => `/api/v1/blocks/${id}/versions/${v}/restore`,
  EXPORT:           (id)       => `/api/v1/blocks/${id}/export`,
};

// ─── Workflow ─────────────────────────────────────────────────────────────────
export const WORKFLOW = {
  LIST:             '/api/v1/workflow/blocks',
  SUBMIT:           (id)       => `/api/v1/blocks/${id}/submit`,
  APPROVE:          (id)       => `/api/v1/blocks/${id}/approve`,
  REQUEST_CHANGES:  (id)       => `/api/v1/blocks/${id}/request-changes`,
  REJECT:           (id)       => `/api/v1/blocks/${id}/reject`,
  PUBLISH:          (id)       => `/api/v1/blocks/${id}/publish`,
  ARCHIVE:          (id)       => `/api/v1/blocks/${id}/archive`,
  BULK_APPROVE:     '/api/v1/workflow/bulk-approve',
  SLA_STATUS:       (id)       => `/api/v1/blocks/${id}/sla`,
  PENDING_REVIEWS:  '/api/v1/workflow/pending',
};

// ─── Reviews ──────────────────────────────────────────────────────────────────
export const REVIEWS = {
  CREATE:           (blockId)  => `/api/v1/blocks/${blockId}/reviews`,
  LIST:             (blockId)  => `/api/v1/blocks/${blockId}/reviews`,
};

// ─── Prompts ──────────────────────────────────────────────────────────────────
export const PROMPTS = {
  LIST:             '/api/v1/prompts',
  GET:              (id)       => `/api/v1/prompts/${id}`,
  CREATE:           '/api/v1/prompts',
  UPDATE:           (id)       => `/api/v1/prompts/${id}`,
  VERSIONS:         (id)       => `/api/v1/prompts/${id}/versions`,
  COMMIT_VERSION:   (id)       => `/api/v1/prompts/${id}/versions`,
  SET_ACTIVE:       (id, v)    => `/api/v1/prompts/${id}/versions/${v}/deploy`,
  AI_GENERATE:      '/api/v1/prompts/generate',
};

// ─── Analytics ────────────────────────────────────────────────────────────────
export const ANALYTICS = {
  SUMMARY:          '/api/v1/analytics/summary',
  PROJECTS:         '/api/v1/analytics/projects',
  USAGE:            '/api/v1/analytics/usage',
  USAGE_BY_MODEL:   '/api/v1/analytics/usage/by-model',
  USAGE_BY_PROJECT: '/api/v1/analytics/usage/by-project',
  USAGE_BY_USER:    '/api/v1/analytics/usage/by-user',
  USAGE_MONTHLY:    '/api/v1/analytics/usage/monthly',
  FEEDBACK:         '/api/v1/analytics/feedback',
  REVIEWS:          '/api/v1/analytics/reviews',
  AUDIT_TRAIL:      '/api/v1/analytics/audit',
  AUDIT_EXPORT:     '/api/v1/analytics/audit/export',
  PROMPT_PERF:      '/api/v1/analytics/prompt-performance',
  QUALITY_TRENDS:   '/api/v1/analytics/quality-trends',
  GENERATION_HISTORY: '/api/v1/analytics/generations',
  COST:             '/api/v1/analytics/cost',
};

// ─── Plagiarism ───────────────────────────────────────────────────────────────
export const PLAGIARISM = {
  SCAN:             (blockId)  => `/api/v1/blocks/${blockId}/plagiarism-scan`,
  STATUS:           (blockId)  => `/api/v1/blocks/${blockId}/plagiarism-report`,
};

// ─── Export ───────────────────────────────────────────────────────────────────
export const EXPORT = {
  BLOCK:            (blockId)  => `/api/v1/blocks/${blockId}/export`,
  GENERATION:       (genId)    => `/api/v1/generations/${genId}/export`,
  COURSE:           (courseId) => `/api/v1/courses/${courseId}/export`,
  PROMPT_DOWNLOAD:  '/api/v1/export/prompt',
};

// ─── Central Repository ───────────────────────────────────────────────────────
export const CENTRAL = {
  // Central Repository is served from the Admin router in FastAPI
  LIST:             '/api/v1/admin/central',
  CREATE:           '/api/v1/admin/central',
  ARCHIVE:          (id)       => `/api/v1/admin/central/${id}/archive`,
};

// ─── Models ───────────────────────────────────────────────────────────────────
export const MODELS = {
  LIST:             '/api/v1/admin/model-catalog',
};

// ─── Workspace (JWT-embedded config) ─────────────────────────────────────────
export const WORKSPACE = {
  GET:              '/api/v1/workspace',
  UPDATE:           '/api/v1/workspace',
  UPDATE_CONFIG:    '/api/v1/workspace/config',
};

// ─── Database Admin ───────────────────────────────────────────────────────────
export const DB_ADMIN = {
  CLEAR_ENTITY:     (entity)   => `/api/v1/admin/clear/${entity}`,
};

export const ADMIN = {
  INSTRUCTIONS:     '/api/v1/admin/instructions',
};
