// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import generateReducer from '../generateSlice';
import { logoutThunk } from '@features/auth/authThunks';
import { forceLogout } from '@features/auth/authSlice';

// CAS-146: logout is a plain client-side Redux action, no page reload -- this
// slice used to survive it untouched, so a leftover activeJobId/latestBlocks
// from a previous tenant's session stayed in memory and got rendered (and
// re-polled) under whatever tenant logged in next in the same tab.

const DIRTY_STATE = {
  activeJobId: 'job-from-tenant-a',
  jobCourseId: 42,
  jobStatus: 'completed',
  jobProgress: ['Generating...', 'Saving...'],
  jobProgressPct: 100,
  jobErrorDetail: null,
  latestBlocks: [{ id: 1, content: 'tenant a content' }],
  latestGenerationId: 999,
  isGenerating: false,
  error: null,
};

describe('generateSlice — reset on logout', () => {
  it('clears everything on a normal logout', () => {
    const next = generateReducer(DIRTY_STATE, logoutThunk.fulfilled());

    expect(next.activeJobId).toBeNull();
    expect(next.jobCourseId).toBeNull();
    expect(next.latestBlocks).toEqual([]);
    expect(next.latestGenerationId).toBeNull();
  });

  it('clears everything on a forced logout (session-expiry path)', () => {
    const next = generateReducer(DIRTY_STATE, forceLogout());

    expect(next.activeJobId).toBeNull();
    expect(next.latestBlocks).toEqual([]);
    expect(next.latestGenerationId).toBeNull();
  });
});
