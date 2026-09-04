/**
 * Which tenant's reviewers the reviewer dropdowns should ask for.
 *
 * NOT a security control — the /users/reviewers endpoint force-scopes every
 * tenant-bound caller to their own tenant from their token and ignores this
 * parameter; it is only honored for a true platform admin, who has no tenant
 * of their own. This keeps the request self-describing and picks the right
 * tenant for that one case.
 *
 * Unlike WorkflowPage's buildApiFilters (the block-listing filter, where an
 * admin intentionally browses across the projects they administer), this must
 * NOT be gated on isAdmin: isAdmin means `role === 'admin'`, which is true for
 * an ordinary tenant Admin (e.g. Cengage) exactly as much as for a platform
 * admin. Gating on it blanked the tenant scope for every tenant Admin, so the
 * request went out unscoped and the dropdown listed every reviewer/admin on
 * the platform (AIM, AIM1, AIM2, AIMAdmin, platformadmin, ...).
 *
 * `authProjectId` — the caller's own tenant, from their JWT — is tried before
 * selProject/selCourse because those only get populated by visiting a
 * Clusters/Courses/Workspace page first, and stay null for a tenant Admin who
 * opens Workflow directly. It is null only for a genuine platform admin, who
 * then falls through to whatever project they have selected or filtered by.
 */
export function resolveReviewerProjectId({
  filtersProjectId,
  authProjectId,
  selProjectId,
  selCourseProjectId,
} = {}) {
  return filtersProjectId ?? authProjectId ?? selProjectId ?? selCourseProjectId ?? null;
}

export default resolveReviewerProjectId;
