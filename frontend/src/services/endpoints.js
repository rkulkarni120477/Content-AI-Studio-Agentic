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
  PROMPTS:          (id)      => `/api/v1/clusters/${id}/cluster-prompts`,
};

// ─── Cluster Prompts ──────────────────────────────────────────────────────────
export const CLUSTER_PROMPTS = {
  LIST:             '/api/v1/cluster-prompts',
  UNASSIGNED:       '/api/v1/cluster-prompts/unassigned',
  CREATE:           '/api/v1/cluster-prompts',
  DELETE:           (id)      => `/api/v1/cluster-prompts/${id}`,
  AI_GENERATE:      '/api/v1/cluster-prompts/ai-generate',
};

// ─── Courses ──────────────────────────────────────────────────────────────────
export const COURSES = {
  GET:              (id)      => `/api/v1/courses/${id}`,
  UPDATE:           (id)      => `/api/v1/courses/${id}`,
  DELETE:           (id)      => `/api/v1/courses/${id}`,
  USERS:            (id)      => `/api/v1/courses/${id}/users`,
  UNASSIGN_USER:    (id, user) => `/api/v1/courses/${id}/users/${encodeURIComponent(user)}`,
  ACTIVE_CDD:       (id)      => `/api/v1/courses/${id}/active-cdd`,
};

// ─── Users ────────────────────────────────────────────────────────────────────
export const USERS = {
  LIST:             '/api/v1/users',
  GET:              (id)      => `/api/v1/users/${id}`,
  CREATE:           '/api/v1/users',
  TOGGLE_ACTIVE:    (id)      => `/api/v1/users/${id}/toggle`,
  REVIEWERS:        '/api/v1/users/reviewers',
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
  UNDERSTAND:       (id)      => `/api/v1/styles/${id}/understand`,
  REGENERATE:       (id)      => `/api/v1/styles/${id}/understand`,
};

// ─── Documents ────────────────────────────────────────────────────────────────
export const DOCUMENTS = {
  LIST:             '/api/v1/documents',
  GET:              (id)      => `/api/v1/documents/${id}`,
  CONTENT:          (id)      => `/api/v1/documents/${id}/content`,
  UPLOAD:           '/api/v1/documents/upload',
  PARSE:            '/api/v1/documents/parse',
  DELETE:           (id)      => `/api/v1/documents/${id}`,
  STYLE_DOCS:       (styleId) => `/api/v1/styles/${styleId}/documents`,
};

// ─── CDD ──────────────────────────────────────────────────────────────────────
export const CDD = {
  LIST:             () => `/api/v1/cdd`,
  LIST_ALL:         '/api/v1/cdd',
  GET:              (id)       => `/api/v1/cdd/${id}`,
  GENERATE:         '/api/v1/cdd/generate',
  VERSIONS:         (id)       => `/api/v1/cdd/${id}/versions`,
  GET_VERSION:      (id, v)    => `/api/v1/cdd/${id}/versions/${v}`,
  ACTIVATE_VERSION: (id, v)    => `/api/v1/cdd/${id}/versions/${v}/activate`,
  COMMIT_VERSION:   (id)       => `/api/v1/cdd/${id}/versions`,
  REGENERATE_ITEM:    (id)     => `/api/v1/cdd/${id}/regenerate-item`,
  REGENERATE_SECTION: (id)     => `/api/v1/cdd/${id}/regenerate-section`,
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
  ACTIVATE_VERSION: (id, v)    => `/api/v1/blueprints/${id}/versions/${v}/activate`,
  COMMIT_VERSION:   (id)       => `/api/v1/blueprints/${id}/versions`,
  REGENERATE_ITEM:    (id)     => `/api/v1/blueprints/${id}/regenerate-item`,
  REGENERATE_SECTION: (id)     => `/api/v1/blueprints/${id}/regenerate-section`,
  PIN:              (id)       => `/api/v1/blueprints/${id}/pin`,
  SET_ACTIVE:       (id)       => `/api/v1/blueprints/${id}/pin`,
  EXPORT:           (id)       => `/api/v1/blueprints/${id}/export`,
  EXPORT_LESSONS:   (id)       => `/api/v1/blueprints/${id}/export-lessons`,
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
  JOB_CANCEL:       (jobId)    => `/api/v1/jobs/${jobId}`,
};

// ─── Blocks ───────────────────────────────────────────────────────────────────
export const BLOCKS = {
  SEARCH:           '/api/v1/search',
  LIST:             (genId)    => `/api/v1/generations/${genId}/blocks`,
  LIST_COURSE:      (courseId) => `/api/v1/courses/${courseId}/blocks`,
  REORDER_COURSE:   (courseId) => `/api/v1/courses/${courseId}/blocks/reorder`,
  GET:              (id)       => `/api/v1/${id}`,
  UPDATE:           (id)       => `/api/v1/${id}`,
  AUTOSAVE:         (id)       => `/api/v1/${id}/autosave`,
  REGENERATE:       (id)       => `/api/v1/${id}/regenerate`,
  REGENERATE_ITEM:  (id)       => `/api/v1/${id}/regenerate-item`,
  VERSIONS:         (id)       => `/api/v1/${id}/versions`,
  RESTORE_VERSION:  (id, v)    => `/api/v1/${id}/versions/${v}/restore`,
  SNAPSHOT:         (id)       => `/api/v1/${id}/snapshot`,
  SCORE:            (id)       => `/api/v1/${id}/score`,
  VALIDATE:         (id)       => `/api/v1/${id}/validate`,
  VALIDATE_GEN:     (genId)    => `/api/v1/generations/${genId}/validate`,
  VALIDATE_COURSE:  (courseId) => `/api/v1/courses/${courseId}/validate`,
  EXPORT_COURSE:    (courseId) => `/api/v1/courses/${courseId}/export`,
  RATING:           (id)       => `/api/v1/${id}/rating`,
};

// ─── Workflow ─────────────────────────────────────────────────────────────────
export const WORKFLOW = {
  LIST:             '/api/v1/workflow/blocks',
  SUMMARY:          '/api/v1/workflow/summary',
  SUBMIT:           (id)       => `/api/v1/workflow/blocks/${id}/submit`,
  APPROVE:          (id)       => `/api/v1/workflow/blocks/${id}/approve`,
  REQUEST_CHANGES:  (id)       => `/api/v1/workflow/blocks/${id}/request-changes`,
  REJECT:           (id)       => `/api/v1/workflow/blocks/${id}/reject`,
  PUBLISH:          (id)       => `/api/v1/workflow/blocks/${id}/publish`,
  ARCHIVE:          (id)       => `/api/v1/workflow/blocks/${id}/archive`,
  RESET_DRAFT:      (id)       => `/api/v1/workflow/blocks/${id}/reset-draft`,
  EVENTS:           (id)       => `/api/v1/workflow/blocks/${id}/events`,
  BULK_APPROVE:     '/api/v1/workflow/bulk-approve',
  PENDING_REVIEWS:  '/api/v1/workflow/pending',
  ADMIN_BREAKDOWN:  '/api/v1/workflow/admin-breakdown',
};

// ─── Prompts ──────────────────────────────────────────────────────────────────
export const PROMPTS = {
  LIST:             '/api/v1/prompts',
  GET:              (id)       => `/api/v1/prompts/${id}`,
  CREATE:           '/api/v1/prompts',
  UPDATE:           (id)       => `/api/v1/prompts/${id}`,
  VERSIONS:         (id)       => `/api/v1/prompts/${id}/versions`,
  COMMIT_VERSION:   (id)       => `/api/v1/prompts/${id}/versions`,
  AI_GENERATE:      '/api/v1/prompts/generate',
  AI_SUGGEST:       '/api/v1/prompts/suggest',
};

// ─── Analytics ────────────────────────────────────────────────────────────────
export const ANALYTICS = {
  SUMMARY:              '/api/v1/analytics/summary',
  PROJECTS:             '/api/v1/analytics/projects',
  USAGE:                '/api/v1/analytics/usage',
  LLM_COST:             '/api/v1/analytics/llm-cost',
  FEEDBACK:             '/api/v1/analytics/feedback',
  FEEDBACK_SUMMARY:     '/api/v1/analytics/feedback/summary',
  REVIEWS:              '/api/v1/analytics/reviews',
  SYSTEM_LOGS:          '/api/v1/analytics/system-logs',
  AUDIT_TRAIL:          '/api/v1/analytics/audit-trail',
  AUDIT_TRAIL_FILTERS:  '/api/v1/analytics/audit-trail/filters',
  AUDIT_EXPORT:         '/api/v1/analytics/audit-trail/export',
  PROMPT_PERF:          '/api/v1/analytics/prompt-performance',
  QUALITY_TRENDS:       '/api/v1/analytics/quality-trends',
  GENERATION_HISTORY:   '/api/v1/analytics/generations',
  HISTORY_PROMPT_VERSIONS: '/api/v1/analytics/history/prompt-versions',
  HISTORY_DOC_UPLOADS:  '/api/v1/analytics/history/document-uploads',
  HISTORY_CDD_BP:       '/api/v1/analytics/history/cdd-blueprint-events',
};

// ─── Plagiarism ───────────────────────────────────────────────────────────────
export const PLAGIARISM = {
  SCAN:             (blockId)  => `/api/v1/${blockId}/plagiarism`,
  STATUS:           (blockId, reportId) => `/api/v1/${blockId}/plagiarism/${reportId}`,
};

// ─── Export ───────────────────────────────────────────────────────────────────
export const EXPORT = {
  GENERATION:       (genId)    => `/api/v1/generations/${genId}/export`,
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
  CLEAR_PRESETS:    '/api/v1/admin/clear-presets',
  CLEAR_ENTITY:     (entity)   => `/api/v1/admin/clear/${entity}`,
};

export const ADMIN = {
  INSTRUCTIONS:     '/api/v1/admin/instructions',
  PERMISSIONS:      '/api/v1/admin/permissions',
  PERMISSIONS_OVERVIEW: '/api/v1/admin/permissions/overview',
};
