import { describe, it, expect, vi } from 'vitest';
import { configureStore, createSlice } from '@reduxjs/toolkit';
import { createBlockJobThunks, attachBlockJobReducers, initialBlockJobState } from '../blockJob';

/**
 * Day-level progress during a block build ("day 7 of 20").
 *
 * The job row only moves at stage boundaries, so a cold build shows one label
 * ("Building day digests...") for minutes and is indistinguishable from a wedged one.
 *
 * The property under test is subordination: progress is a nicety, and it must never
 * delay or break the poll the UI relies on to notice completion.
 */
function makeStore({ progress, progressError, status = 'running' } = {}) {
  const getProgress = progressError
    ? vi.fn(async () => { throw new Error('dis down'); })
    : vi.fn(async () => ({ progress }));
  const thunks = createBlockJobThunks({
    prefix: 'p', deliverable: 'cdd',
    enqueue: async () => ({ job_id: 'j1', status: 'queued' }),
    getJobStatus: async (jobId) => ({ status, job_id: jobId, progress: 20 }),
    getProgress,
    completedMessage: 'done', failedMessage: 'failed',
  });
  const slice = createSlice({
    name: 'p',
    initialState: { isGenerating: false, error: null, ...initialBlockJobState },
    reducers: {},
    extraReducers: (b) => attachBlockJobReducers(b, thunks),
  });
  return { thunks, store: configureStore({ reducer: slice.reducer }), getProgress };
}

const DAYS = { total: 20, done: 7, built: 6, failed: 1, cached: 0, remaining: 13 };

describe('block job day progress', () => {
  it('surfaces the day counts onto the job state', async () => {
    const { thunks, store } = makeStore({ progress: DAYS });
    await store.dispatch(thunks.pollThunk({ jobId: 'j1', courseId: 1 }));
    expect(store.getState().blockJob.days).toEqual(DAYS);
  });

  it('keeps polling normally when progress is unavailable', async () => {
    // A DIS outage must not stop the poll noticing that the job finished.
    const { thunks, store } = makeStore({ progressError: true });
    const res = await store.dispatch(thunks.pollThunk({ jobId: 'j1', courseId: 1 }));
    expect(res.payload.status).toBe('running');
    expect(store.getState().blockJob.days).toBeNull();
  });

  it('keeps the last known counts when one fetch fails mid-build', async () => {
    // blockJob is rebuilt every poll, so a single failed progress fetch would blank
    // the counter and make the build look like it restarted.
    const { thunks, store } = makeStore({ progress: DAYS });
    await store.dispatch(thunks.pollThunk({ jobId: 'j1', courseId: 1 }));

    const { thunks: t2 } = makeStore({ progressError: true });
    // Reuse the same store: simulate the next tick failing.
    await store.dispatch(t2.pollThunk({ jobId: 'j1', courseId: 1 }));
    expect(store.getState().blockJob.days).toEqual(DAYS);
  });

  it('ignores a progress payload with no total', async () => {
    // total 0 would render as "day 0 of 0" — worse than showing nothing.
    const { thunks, store } = makeStore({ progress: { total: 0, done: 0 } });
    await store.dispatch(thunks.pollThunk({ jobId: 'j1', courseId: 1 }));
    expect(store.getState().blockJob.days).toBeNull();
  });

  it('does not ask for progress once the job is terminal', async () => {
    // The build is over; a further request is pure waste on every completed job.
    const { thunks, store, getProgress } = makeStore({ progress: DAYS, status: 'completed' });
    await store.dispatch(thunks.pollThunk({ jobId: 'j1', courseId: 1 }));
    expect(getProgress).not.toHaveBeenCalled();
  });

  it('is inert for a caller that has not wired progress up', async () => {
    const thunks = createBlockJobThunks({
      prefix: 'q', deliverable: 'cdd',
      enqueue: async () => ({ job_id: 'j', status: 'queued' }),
      getJobStatus: async () => ({ status: 'running', job_id: 'j' }),
    });
    const slice = createSlice({
      name: 'q',
      initialState: { isGenerating: false, error: null, ...initialBlockJobState },
      reducers: {},
      extraReducers: (b) => attachBlockJobReducers(b, thunks),
    });
    const store = configureStore({ reducer: slice.reducer });
    await store.dispatch(thunks.pollThunk({ jobId: 'j', courseId: 1 }));
    expect(store.getState().blockJob.days).toBeNull();
  });
});
