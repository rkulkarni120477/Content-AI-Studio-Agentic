// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import jobsReducer from '../jobsSlice';
import { logoutThunk } from '@features/auth/authThunks';
import { forceLogout } from '@features/auth/authSlice';

// CAS-146: the job bell / tracker slice used to survive a logout too, so it
// could keep showing another tenant's jobs (and the dismissed-ids list from
// their session) after switching tenants in the same tab.

const DIRTY_STATE = {
  jobsById: { 'job-1': { jobId: 'job-1', status: 'completed', courseId: 42 } },
  dismissedIds: ['job-0'],
  pollCourseId: 42,
  isResuming: true,
  lastError: 'boom',
};

describe('jobsSlice — reset on logout', () => {
  beforeEach(() => {
    sessionStorage.setItem('cas_dismissed_jobs', JSON.stringify(['job-0']));
  });
  afterEach(() => {
    sessionStorage.clear();
  });

  it('clears everything, including the sessionStorage dismiss list, on logout', () => {
    const next = jobsReducer(DIRTY_STATE, logoutThunk.fulfilled());

    expect(next.jobsById).toEqual({});
    expect(next.dismissedIds).toEqual([]);
    expect(next.pollCourseId).toBeNull();
    expect(sessionStorage.getItem('cas_dismissed_jobs')).toBeNull();
  });

  it('clears everything on a forced logout too', () => {
    const next = jobsReducer(DIRTY_STATE, forceLogout());

    expect(next.jobsById).toEqual({});
    expect(next.dismissedIds).toEqual([]);
  });
});
