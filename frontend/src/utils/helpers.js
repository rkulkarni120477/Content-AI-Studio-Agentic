import { WORKFLOW_STATES, ROLES, DATE_RANGE_PRESETS } from './constants';

// ─── Formatting ───────────────────────────────────────────────────────────────
export function formatDate(dateStr, opts = {}) {
  if (!dateStr) return '—';
  return new Intl.DateTimeFormat('en-IN', {
    year: 'numeric', month: 'short', day: 'numeric', ...opts,
  }).format(new Date(dateStr));
}

export function formatDateTime(dateStr) {
  return formatDate(dateStr, { hour: '2-digit', minute: '2-digit' });
}

export function formatRelative(dateStr) {
  if (!dateStr) return '—';
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins  = Math.floor(diff / 60000);
  const hours = Math.floor(diff / 3600000);
  const days  = Math.floor(diff / 86400000);
  if (mins < 1)   return 'just now';
  if (mins < 60)  return `${mins}m ago`;
  if (hours < 24) return `${hours}h ago`;
  if (days < 30)  return `${days}d ago`;
  return formatDate(dateStr);
}

export function formatCurrency(amount, currency = 'USD') {
  if (amount == null) return '—';
  return new Intl.NumberFormat('en-US', {
    style: 'currency', currency, minimumFractionDigits: 4, maximumFractionDigits: 4,
  }).format(amount);
}

export function formatNumber(n) {
  if (n == null) return '—';
  return new Intl.NumberFormat('en-IN').format(n);
}

export function formatTokens(n) {
  if (n == null) return '—';
  if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
  if (n >= 1000)    return `${(n / 1000).toFixed(1)}K`;
  return n.toString();
}

export function formatDuration(ms) {
  if (ms == null) return '—';
  if (ms < 1000)  return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60000)}m ${Math.floor((ms % 60000) / 1000)}s`;
}

// ─── Date Range Helpers ───────────────────────────────────────────────────────
export function getDateRange(preset) {
  const now   = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());

  switch (preset) {
    case DATE_RANGE_PRESETS.LAST_7_DAYS:
      return { start: new Date(today.getTime() - 7 * 86400000), end: now };
    case DATE_RANGE_PRESETS.LAST_30_DAYS:
      return { start: new Date(today.getTime() - 30 * 86400000), end: now };
    case DATE_RANGE_PRESETS.LAST_90_DAYS:
      return { start: new Date(today.getTime() - 90 * 86400000), end: now };
    case DATE_RANGE_PRESETS.THIS_MONTH:
      return { start: new Date(now.getFullYear(), now.getMonth(), 1), end: now };
    case DATE_RANGE_PRESETS.LAST_MONTH: {
      const s = new Date(now.getFullYear(), now.getMonth() - 1, 1);
      const e = new Date(now.getFullYear(), now.getMonth(), 0, 23, 59, 59);
      return { start: s, end: e };
    }
    default:
      return { start: null, end: null };
  }
}

// ─── Workflow Helpers ─────────────────────────────────────────────────────────
export function getWorkflowStateColor(state) {
  const map = {
    [WORKFLOW_STATES.DRAFT]:             'gray',
    [WORKFLOW_STATES.IN_REVIEW]:         'warning',
    [WORKFLOW_STATES.CHANGES_REQUESTED]: 'purple',
    [WORKFLOW_STATES.APPROVED]:          'success',
    [WORKFLOW_STATES.PUBLISHED]:         'info',
    [WORKFLOW_STATES.ARCHIVED]:          'gray',
    [WORKFLOW_STATES.REJECTED]:          'danger',
  };
  return map[state] || 'gray';
}

export function canTransitionTo(currentState, targetState, role) {
  const transitions = {
    [WORKFLOW_STATES.DRAFT]: {
      [WORKFLOW_STATES.IN_REVIEW]: [ROLES.AUTHOR, ROLES.ADMIN],
    },
    [WORKFLOW_STATES.IN_REVIEW]: {
      [WORKFLOW_STATES.APPROVED]:          [ROLES.REVIEWER, ROLES.ADMIN],
      [WORKFLOW_STATES.CHANGES_REQUESTED]: [ROLES.REVIEWER, ROLES.ADMIN],
      [WORKFLOW_STATES.REJECTED]:          [ROLES.REVIEWER, ROLES.ADMIN],
    },
    [WORKFLOW_STATES.CHANGES_REQUESTED]: {
      [WORKFLOW_STATES.IN_REVIEW]: [ROLES.AUTHOR, ROLES.ADMIN],
    },
    [WORKFLOW_STATES.APPROVED]: {
      [WORKFLOW_STATES.PUBLISHED]: [ROLES.REVIEWER, ROLES.ADMIN],
      [WORKFLOW_STATES.ARCHIVED]:  [ROLES.ADMIN],
    },
    [WORKFLOW_STATES.PUBLISHED]: {
      [WORKFLOW_STATES.ARCHIVED]: [ROLES.ADMIN],
    },
  };
  return transitions[currentState]?.[targetState]?.includes(role) ?? false;
}

// ─── SLA Helpers ─────────────────────────────────────────────────────────────
export function getSlaStatus(submittedAt, slaHours = 24) {
  if (!submittedAt) return null;
  const elapsed = (Date.now() - new Date(submittedAt).getTime()) / 3600000;
  if (elapsed > slaHours)       return 'overdue';
  if (elapsed > slaHours * 0.7) return 'warning';
  return 'ok';
}

// ─── String Helpers ───────────────────────────────────────────────────────────
export function capitalize(str) {
  if (!str) return '';
  return str.charAt(0).toUpperCase() + str.slice(1);
}

export function truncate(str, len = 80) {
  if (!str || str.length <= len) return str;
  return str.slice(0, len) + '…';
}

export function slugify(str) {
  return str.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}

// ─── Array Helpers ────────────────────────────────────────────────────────────
export function groupBy(arr, key) {
  return arr.reduce((acc, item) => {
    const k = typeof key === 'function' ? key(item) : item[key];
    if (!acc[k]) acc[k] = [];
    acc[k].push(item);
    return acc;
  }, {});
}

export function uniqueBy(arr, key) {
  const seen = new Set();
  return arr.filter((item) => {
    const k = typeof key === 'function' ? key(item) : item[key];
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

// ─── Object Helpers ───────────────────────────────────────────────────────────
export function omit(obj, keys) {
  const out = { ...obj };
  keys.forEach((k) => delete out[k]);
  return out;
}

export function pick(obj, keys) {
  return keys.reduce((acc, k) => { if (k in obj) acc[k] = obj[k]; return acc; }, {});
}

// ─── Download Helper ──────────────────────────────────────────────────────────
export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a   = document.createElement('a');
  a.href    = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export function downloadText(content, filename, mimeType = 'text/plain') {
  downloadBlob(new Blob([content], { type: mimeType }), filename);
}

// ─── Error Normalization ──────────────────────────────────────────────────────
export function extractErrorMessage(error) {
  if (typeof error === 'string') return error;
  return (
    error?.response?.data?.detail ||
    error?.response?.data?.message ||
    error?.message ||
    'An unexpected error occurred.'
  );
}

// ─── Debounce ─────────────────────────────────────────────────────────────────
export function debounce(fn, delay) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

// ─── Class name builder ───────────────────────────────────────────────────────
export function cn(...classes) {
  return classes.filter(Boolean).join(' ');
}
