// Prompt Library permission helpers — adapted from the standalone app to the host
// role model (admin | reviewer | author). "Manager" access (create/edit/delete
// prompts, manage requests, read all reviews, manage teams) = admin or reviewer.
// Audit is admin-only. These gate the UI; the backend enforces the same via RBAC.

export const ROLES = {
  ADMIN: 'admin',
  REVIEWER: 'reviewer',
  AUTHOR: 'author',
};

function role(user) {
  return user?.role || '';
}

function isManager(user) {
  return role(user) === ROLES.ADMIN || role(user) === ROLES.REVIEWER;
}

export function isAdmin(r) {
  return r === ROLES.ADMIN;
}

// ── Library / prompts ──────────────────────────────────────────────────────────
export function canReadLibrary(user) {
  return Boolean(user);
}
export function canManagePrompts(user) {
  return isManager(user);
}
export function canDeletePrompts(user) {
  return isManager(user);
}

// ── Requests / reviews ─────────────────────────────────────────────────────────
export function canReadAllRequests(user) {
  return isManager(user);
}
export function canManageRequests(user) {
  return isManager(user);
}
export function canReadAllReviews(user) {
  return isManager(user);
}

// ── Teams ──────────────────────────────────────────────────────────────────────
export function canReadTeams(user) {
  return isManager(user);
}
export function canManageTeams(user) {
  return isManager(user);
}

// ── Audit ──────────────────────────────────────────────────────────────────────
export function canReadAudit(user) {
  return role(user) === ROLES.ADMIN;
}
export function canExportAudit(user) {
  return role(user) === ROLES.ADMIN;
}

// ── Display helpers ────────────────────────────────────────────────────────────
export function roleLabel(r) {
  if (r === ROLES.ADMIN) return 'Admin';
  if (r === ROLES.REVIEWER) return 'Lead';
  if (r === ROLES.AUTHOR) return 'ID';
  return 'User';
}

export function roleBadgeClass(r) {
  if (r === ROLES.ADMIN) return 'role-admin';
  if (r === ROLES.REVIEWER) return 'role-manager';
  return 'role-user';
}
