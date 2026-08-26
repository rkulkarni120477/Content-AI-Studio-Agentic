// @vitest-environment jsdom
//
// Round-1 PR review finding 2: GET /users had the identical cross-tenant leak
// as the reviewer dropdowns, reachable through this exact picker. The backend
// now force-scopes regardless of what's sent, but this pins that the picker
// actually SENDS a project_id at all — omitting it (as the pre-fix code did)
// still worked for a tenant Admin (server-side scoping saves them) but left a
// platform admin with no tenant of their own seeing an empty picker.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, waitFor } from '@testing-library/react';

const listUsers = vi.fn();
const listProjectUsers = vi.fn();
const listCourseUsers = vi.fn();

vi.mock('@features/dashboard/services/dashboardService', () => ({
  dashboardService: {
    listUsers: (...a) => listUsers(...a),
    listProjectUsers: (...a) => listProjectUsers(...a),
    listCourseUsers: (...a) => listCourseUsers(...a),
  },
}));

const { default: ManageUsersModal } = await import('../ManageUsersModal');

afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe('ManageUsersModal — scopes the user picker to the right tenant', () => {
  it('scope "course": sends the course´s own project id, not the course id', async () => {
    listUsers.mockResolvedValue({ items: [] });
    listCourseUsers.mockResolvedValue([]);

    render(
      <ManageUsersModal
        open
        onClose={() => {}}
        scope="course"
        entityId={501}   // the course id
        entityName="Some Course"
        projectId={23}   // the course's project
      />,
    );

    await waitFor(() => expect(listUsers).toHaveBeenCalledWith(23));
  });

  it('scope "project": sends the project id itself (entityId)', async () => {
    listUsers.mockResolvedValue({ items: [] });
    listProjectUsers.mockResolvedValue([]);

    render(
      <ManageUsersModal
        open
        onClose={() => {}}
        scope="project"
        entityId={23}
        entityName="Some Project"
      />,
    );

    await waitFor(() => expect(listUsers).toHaveBeenCalledWith(23));
  });
});
