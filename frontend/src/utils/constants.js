// ─── Roles ───────────────────────────────────────────────────────────────────
export const ROLES = {
  ADMIN:    'admin',
  AUTHOR:   'author',   // also called "ID" (Instructional Designer)
  REVIEWER: 'reviewer', // also called "Lead"
};

export const ROLE_LABELS = {
  [ROLES.ADMIN]:    'Admin',
  [ROLES.AUTHOR]:   'ID',
  [ROLES.REVIEWER]: 'Lead',
};

// ─── Workflow States ──────────────────────────────────────────────────────────
export const WORKFLOW_STATES = {
  DRAFT:               'draft',
  IN_REVIEW:           'in_review',
  CHANGES_REQUESTED:   'changes_requested',
  APPROVED:            'approved',
  PUBLISHED:           'published',
  ARCHIVED:            'archived',
  REJECTED:            'rejected',
};

export const WORKFLOW_STATE_LABELS = {
  [WORKFLOW_STATES.DRAFT]:             'Draft',
  [WORKFLOW_STATES.IN_REVIEW]:         'In Review',
  [WORKFLOW_STATES.CHANGES_REQUESTED]: 'Changes Requested',
  [WORKFLOW_STATES.APPROVED]:          'Approved',
  [WORKFLOW_STATES.PUBLISHED]:         'Published',
  [WORKFLOW_STATES.ARCHIVED]:          'Archived',
  [WORKFLOW_STATES.REJECTED]:          'Rejected',
};

export const WORKFLOW_STATE_ICONS = {
  [WORKFLOW_STATES.DRAFT]:             '📝',
  [WORKFLOW_STATES.IN_REVIEW]:         '🔍',
  [WORKFLOW_STATES.CHANGES_REQUESTED]: '🔁',
  [WORKFLOW_STATES.APPROVED]:          '✅',
  [WORKFLOW_STATES.PUBLISHED]:         '🚀',
  [WORKFLOW_STATES.ARCHIVED]:          '🗄️',
  [WORKFLOW_STATES.REJECTED]:          '❌',
};

/** Kanban column top-border colours (Streamlit workflow.py). */
export const WORKFLOW_KANBAN_COLORS = {
  [WORKFLOW_STATES.DRAFT]:             '#94a3b8',
  [WORKFLOW_STATES.IN_REVIEW]:         '#f59e0b',
  [WORKFLOW_STATES.CHANGES_REQUESTED]: '#f97316',
  [WORKFLOW_STATES.APPROVED]:          '#10b981',
  [WORKFLOW_STATES.PUBLISHED]:         '#6366f1',
  [WORKFLOW_STATES.ARCHIVED]:          '#6b7280',
};

// ─── Generation Modes ────────────────────────────────────────────────────────
export const GENERATION_MODES = {
  STUDENT: 'student',
  TEACHER: 'teacher',
};

// ─── Block Types ─────────────────────────────────────────────────────────────
export const BLOCK_TYPES = {
  LESSON:      'lesson',
  ASSESSMENT:  'assessment',
  ACTIVITY:    'activity',
  CAPSTONE:    'capstone',
  SUMMARY:     'summary',
};

// ─── Entity Types (for usage logs) ───────────────────────────────────────────
export const ENTITY_TYPES = {
  CDD:       'cdd',
  BLUEPRINT: 'blueprint',
  GENERATE:  'generate',
  STYLE:     'style',
  PROMPT:    'prompt',
};

// ─── Job Statuses ────────────────────────────────────────────────────────────
export const JOB_STATUSES = {
  PENDING:   'pending',
  RUNNING:   'running',
  COMPLETED: 'completed',
  FAILED:    'failed',
};

// ─── Plagiarism Statuses ─────────────────────────────────────────────────────
export const PLAGIARISM_STATUSES = {
  PENDING:   'pending',
  COMPLETED: 'completed',
  ERROR:     'error',
};

// ─── Export Formats ───────────────────────────────────────────────────────────
export const EXPORT_TEMPLATES = {
  default:       'Default',
  storyboard:    'Storyboard',
  teacher_guide: 'Teacher Guide',
  quiz_bank:     'Quiz Bank',
  client:        'Client Export',
};

export const WORKFLOW_EXPORTABLE = ['approved', 'published'];

export const EXPORT_FORMATS = {
  MARKDOWN: 'markdown',
  DOCX:     'docx',
  PDF:      'pdf',
  HTML:     'html',
  JSON:     'json',
};

// ─── Prompt Component Types ──────────────────────────────────────────────────
export const PROMPT_COMPONENT_TYPES = {
  STYLE:     'style',
  CDD:       'cdd',
  BLUEPRINT: 'blueprint',
  GENERATE:  'generate',
};

// ─── Document Types ───────────────────────────────────────────────────────────
export const DOCUMENT_TYPES = {
  PDF:  'pdf',
  DOCX: 'docx',
  TXT:  'txt',
};

// ─── LLM Providers ───────────────────────────────────────────────────────────
export const LLM_PROVIDERS = {
  OPENAI:  'openai',
  BEDROCK: 'bedrock',
};

// ─── Pagination ───────────────────────────────────────────────────────────────
export const DEFAULT_PAGE_SIZE = 20;
export const PAGE_SIZE_OPTIONS = [10, 20, 50, 100];

// ─── SLA ─────────────────────────────────────────────────────────────────────
export const SLA_HOURS = 24;

// ─── Navigation Routes ────────────────────────────────────────────────────────
export const ROUTES = {
  LOGIN:        '/login',
  DASHBOARD:    '/dashboard',
  PROJECT_CLUSTERS: (projectId) => `/projects/${projectId}/clusters`,
  CLUSTER_COURSES:  (projectId, clusterId) => `/projects/${projectId}/clusters/${clusterId}/courses`,
  WORKSPACE:    (courseId) => `/workspace/${courseId}`,
  STYLE:        (courseId) => `/workspace/${courseId}/style`,
  CDD:          (courseId) => `/workspace/${courseId}/cdd`,
  BLUEPRINT:    (courseId) => `/workspace/${courseId}/blueprint`,
  GENERATE:     (courseId) => `/workspace/${courseId}/generate`,
  EDITOR:       (courseId) => `/workspace/${courseId}/editor`,
  WORKFLOW:     (courseId) => `/workspace/${courseId}/workflow`,
  ANALYTICS:    (courseId) => `/workspace/${courseId}/analytics`,
  CENTRAL:      '/central',
};

// ─── Local Storage Keys ───────────────────────────────────────────────────────
export const STORAGE_KEYS = {
  TOKEN:           'content_ai_jwt',
  THEME:           'content_ai_theme',
  SIDEBAR_OPEN:    'content_ai_sidebar',
  LAST_PROJECT_ID: 'content_ai_last_project',
  LAST_COURSE_ID:  'content_ai_last_course',
};

// ─── Date Range Presets ───────────────────────────────────────────────────────
export const DATE_RANGE_PRESETS = {
  LAST_7_DAYS:   'last_7_days',
  LAST_30_DAYS:  'last_30_days',
  LAST_90_DAYS:  'last_90_days',
  THIS_MONTH:    'this_month',
  LAST_MONTH:    'last_month',
  ALL_TIME:      'all_time',
};

export const DATE_RANGE_LABELS = {
  [DATE_RANGE_PRESETS.LAST_7_DAYS]:  'Last 7 Days',
  [DATE_RANGE_PRESETS.LAST_30_DAYS]: 'Last 30 Days',
  [DATE_RANGE_PRESETS.LAST_90_DAYS]: 'Last 90 Days',
  [DATE_RANGE_PRESETS.THIS_MONTH]:   'This Month',
  [DATE_RANGE_PRESETS.LAST_MONTH]:   'Last Month',
  [DATE_RANGE_PRESETS.ALL_TIME]:     'All Time',
};

// ─── Rating ───────────────────────────────────────────────────────────────────
export const MIN_RATING = 1;
export const MAX_RATING = 5;

// ─── Feedback Scopes ──────────────────────────────────────────────────────────
export const FEEDBACK_SCOPES = {
  LEARNING:  'learning',
  ONE_TIME:  'one_time',
};

export const FEEDBACK_SCOPE_OPTIONS = [
  { id: FEEDBACK_SCOPES.ONE_TIME, label: '⚡ Apply Once' },
  { id: FEEDBACK_SCOPES.LEARNING, label: '🧠 Use as Learning' },
];

export const FEEDBACK_SCOPE_HINTS = {
  [FEEDBACK_SCOPES.ONE_TIME]: {
    icon: '⚡', fg: '#f59e0b', bg: '#fffbeb',
    title: 'Apply Once',
    tip: 'Used for this block only. Not stored as a learning signal.',
  },
  [FEEDBACK_SCOPES.LEARNING]: {
    icon: '🧠', fg: '#6366f1', bg: '#eef2ff',
    title: 'Use as Learning',
    tip: 'Stored as a reusable signal to improve future generations.',
  },
};

// ─── Signal Sources ───────────────────────────────────────────────────────────
export const SIGNAL_SOURCES = {
  REGENERATE: 'regenerate',
  EDIT:       'edit',
};
