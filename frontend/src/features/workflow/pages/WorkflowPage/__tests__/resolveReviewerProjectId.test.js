// @vitest-environment jsdom
// dashboardThunks -> authSlice reads localStorage at import time.
//
// Regression: "Reviewer filter displays users from other tenants in Cengage
// Workflow". A tenant Admin (role === 'admin', same as isAdmin for a
// platform admin) opening Workflow directly — never having visited a
// Clusters/Courses/Workspace page first — has selProject/selCourse still
// null. An earlier version of the fix gated the reviewer-scope project_id on
// `!isAdmin`, which blanked the tenant scope for every tenant Admin and fell
// back to every reviewer/admin on the whole platform (AIM, AIM1, AIMAdmin,
// platformadmin, ...). authProjectId — the caller's own tenant from their
// JWT — must be used instead, independent of role.
import { describe, expect, it } from 'vitest';
import { resolveReviewerProjectId } from '../WorkflowPage';

describe('resolveReviewerProjectId', () => {
  it('uses the tenant Admin\'s own JWT project_id, landing on Workflow with no navigation history', () => {
    const projectId = resolveReviewerProjectId({
      filtersProjectId: null,
      authProjectId: 42, // Cengage
      selProjectId: undefined, // never visited Clusters/Courses/Workspace
      selCourseProjectId: undefined,
    });
    expect(projectId).toBe(42);
  });

  it('is unaffected by isAdmin — the caller only ever passes authProjectId, not a role flag', () => {
    // (documents the shape of the bug: there is no isAdmin parameter at all,
    // so a tenant Admin and a non-admin reviewer resolve identically)
    const forAdmin = resolveReviewerProjectId({ authProjectId: 42 });
    const forReviewer = resolveReviewerProjectId({ authProjectId: 42 });
    expect(forAdmin).toBe(forReviewer);
    expect(forAdmin).toBe(42);
  });

  it('an explicit admin project filter wins over the caller\'s own tenant', () => {
    const projectId = resolveReviewerProjectId({
      filtersProjectId: 7,
      authProjectId: 42,
      selProjectId: 42,
      selCourseProjectId: 42,
    });
    expect(projectId).toBe(7);
  });

  it('falls back to selProject/selCourse only for a genuine platform admin (no tenant)', () => {
    const projectId = resolveReviewerProjectId({
      filtersProjectId: null,
      authProjectId: null,
      selProjectId: 5,
      selCourseProjectId: undefined,
    });
    expect(projectId).toBe(5);
  });

  it('returns null (unscoped) only when nothing at all is available', () => {
    expect(resolveReviewerProjectId({})).toBeNull();
  });
});
