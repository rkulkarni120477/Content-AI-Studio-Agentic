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
  CONFIG:           (id)      => `/api/v1/courses/${id}/config`,
  UPDATE_CONFIG:    (id)      => `/api/v1/courses/${id}/config`,
  SET_ACTIVE_CDD:   (id)      => `/api/v1/courses/${id}/active-cdd`,
  SET_ACTIVE_BP:    (id)      => `/api/v1/courses/${id}/active-blueprint`,
  USERS:            (id)      => `/api/v1/courses/${id}/users`,
  UNASSIGN_USER:    (id, user) => `/api/v1/courses/${id}/users/${encodeURIComponent(user)}`,
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
  UPLOAD:           '/api/v1/documents/upload',
  DELETE:           (id)      => `/api/v1/documents/${id}`,
  STYLE_DOCS:       (styleId) => `/api/v1/styles/${styleId}/documents`,
};

// ─── CDD ──────────────────────────────────────────────────────────────────────
export const CDD = {
  LIST:             (courseId) => `/api/v1/courses/${courseId}/cdds`,
  LIST_ALL:         '/api/v1/cdds',
  GET:              (id)       => `/api/v1/cdds/${id}`,
  CREATE:           '/api/v1/cdds',
  GENERATE:         '/api/v1/cdds/generate',
  VERSIONS:         (id)       => `/api/v1/cdds/${id}/versions`,
  GET_VERSION:      (id, v)    => `/api/v1/cdds/${id}/versions/${v}`,
  COMMIT_VERSION:   (id)       => `/api/v1/cdds/${id}/versions`,
  SET_ACTIVE:       (id)       => `/api/v1/cdds/${id}/set-active`,
  EXPORT:           (id)       => `/api/v1/cdds/${id}/export`,
};

// ─── Blueprint ────────────────────────────────────────────────────────────────
export const BLUEPRINT = {
  LIST:             (courseId) => `/api/v1/courses/${courseId}/blueprints`,
  GET:              (id)       => `/api/v1/blueprints/${id}`,
  GENERATE:         '/api/v1/blueprints/generate',
  VERSIONS:         (id)       => `/api/v1/blueprints/${id}/versions`,
  GET_VERSION:      (id, v)    => `/api/v1/blueprints/${id}/versions/${v}`,
  COMMIT_VERSION:   (id)       => `/api/v1/blueprints/${id}/versions`,
  SET_ACTIVE:       (id)       => `/api/v1/blueprints/${id}/set-active`,
  EXPORT:           (id)       => `/api/v1/blueprints/${id}/export`,
  PARSE_COMPONENTS: (id)       => `/api/v1/blueprints/${id}/components`,
};

// ─── Generate ─────────────────────────────────────────────────────────────────
export const GENERATE = {
  RUN:              '/api/v1/generate',
  QUEUE:            '/api/v1/generate/queue',
  STATUS:           (jobId)    => `/api/v1/jobs/${jobId}/status`,
  CANCEL:           (jobId)    => `/api/v1/jobs/${jobId}/cancel`,
  GENERATIONS_LIST: (courseId) => `/api/v1/courses/${courseId}/generations`,
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
  GET:              (name)     => `/api/v1/prompts/${name}`,
  CREATE:           '/api/v1/prompts',
  UPDATE:           (name)     => `/api/v1/prompts/${name}`,
  VERSIONS:         (name)     => `/api/v1/prompts/${name}/versions`,
  COMMIT_VERSION:   (name)     => `/api/v1/prompts/${name}/versions`,
  SET_ACTIVE:       (name, v)  => `/api/v1/prompts/${name}/versions/${v}/activate`,
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
  PROMPT_PERF:      '/api/v1/analytics/prompts/performance',
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
  LIST:             '/api/v1/central',
  GET:              (id)       => `/api/v1/central/${id}`,
  CREATE:           '/api/v1/central',
  IMPORT:           '/api/v1/central/import',
  DELETE:           (id)       => `/api/v1/central/${id}`,
};

// ─── Models ───────────────────────────────────────────────────────────────────
export const MODELS = {
  LIST:             '/api/v1/models',
};

// ─── Database Admin ───────────────────────────────────────────────────────────
export const DB_ADMIN = {
  CLEAR_ENTITY:     (entity)   => `/api/v1/admin/clear/${entity}`,
};
