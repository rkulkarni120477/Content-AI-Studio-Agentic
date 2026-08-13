import { describe, it, expect, vi } from 'vitest';
import { configureStore, createSlice } from '@reduxjs/toolkit';
import { createBlockJobThunks, attachBlockJobReducers, initialBlockJobState } from '../blockJob';
import { JOB_STATUSES } from '@utils/constants';

/**
 * Reattaching to a build already running server-side.
 *
 * The poll chain lives only in browser memory, so a refresh — or a closed laptop, or
 * the transient network error a 20-minute build reliably provokes — orphaned the UI
 * while the job kept running. The user then either watched a dead spinner or
 * re-submitted and paid for a second concurrent build (observed 2026-08-13 on a
 * 22-minute Block 2 build).
 */
function makeStore({ active, getActiveJob, getJobStatus } = {}) {
  const thunks = createBlockJobThunks({
    prefix: 'test',
    deliverable: 'cdd',
    enqueue: async () => ({ job_id: 'j1', status: 'queued' }),
    // Mirrors the real status endpoint: it echoes the polled id and always reports
    // progress/current_step. A stub returning a fixed id, or omitting progress, would
    // hide the poller clobbering the job it was just handed.
    getJobStatus: getJobStatus || (async (jobId) => ({
      status: 'running', job_id: jobId, progress: 20,
      current_step: 'Building day digests...',
    })),
    getActiveJob: getActiveJob || (async () => ({ job: active ?? null })),
    completedMessage: 'done',
    failedMessage: 'failed',
  });
  const slice = createSlice({
    name: 'test',
    initialState: { isGenerating: false, error: null, ...initialBlockJobState },
    reducers: { resetBlockJob(s) { s.blockJob = null; } },
    extraReducers: (b) => attachBlockJobReducers(b, thunks),
  });
  const store = configureStore({ reducer: slice.reducer });
  return { thunks, store, reset: slice.actions.resetBlockJob };
}

const RUNNING_JOB = {
  job_id: 'resumed-1',
  status: JOB_STATUSES.RUNNING,
  progress: 20,
  current_step: 'Building day digests...',
  created_at: '2026-08-13T06:25:22',
};

describe('resumeThunk', () => {
  it('adopts a running job so the page shows the build instead of an idle form', async () => {
    const { thunks, store } = makeStore({ active: RUNNING_JOB });
    await store.dispatch(thunks.resumeThunk({ courseId: 48 }));

    const s = store.getState();
    expect(s.isGenerating).toBe(true);
    expect(s.blockJob.jobId).toBe('resumed-1');
    expect(s.blockJob.progress).toBe(20);
    expect(s.blockJob.currentStep).toBe('Building day digests...');
  });

  it('records when the build started, the only signal a long build is alive', async () => {
    // A cold block build sits on one step for minutes, so without elapsed time
    // "working" and "wedged" look identical.
    const { thunks, store } = makeStore({ active: RUNNING_JOB });
    await store.dispatch(thunks.resumeThunk({ courseId: 48 }));
    expect(store.getState().blockJob.startedAt).toBe('2026-08-13T06:25:22');
    expect(store.getState().blockJob.resumed).toBe(true);
  });

  it('starts polling the adopted job', async () => {
    const getJobStatus = vi.fn(async () => ({ status: 'running', job_id: 'resumed-1' }));
    const { thunks, store } = makeStore({ active: RUNNING_JOB, getJobStatus });
    await store.dispatch(thunks.resumeThunk({ courseId: 48 }));
    expect(getJobStatus).toHaveBeenCalledWith('resumed-1');
  });

  it('changes nothing when no build is running', async () => {
    // This is the overwhelmingly common case — it runs on every page load and must
    // not disturb whatever the user is looking at.
    const { thunks, store } = makeStore({ active: null });
    const before = store.getState();
    await store.dispatch(thunks.resumeThunk({ courseId: 48 }));
    expect(store.getState()).toEqual(before);
    expect(store.getState().isGenerating).toBe(false);
    expect(store.getState().blockJob).toBeNull();
  });

  it('never breaks a page load when the lookup fails', async () => {
    const { thunks, store } = makeStore({
      getActiveJob: async () => { throw new Error('boom'); },
    });
    const res = await store.dispatch(thunks.resumeThunk({ courseId: 48 }));
    expect(res.payload ?? null).toBeNull();
    expect(store.getState().isGenerating).toBe(false);
    expect(store.getState().error).toBeNull();  // no error the user did not ask for
  });

  it('does not call the server without a course', async () => {
    const getActiveJob = vi.fn(async () => ({ job: null }));
    const { thunks, store } = makeStore({ getActiveJob });
    await store.dispatch(thunks.resumeThunk({ courseId: undefined }));
    expect(getActiveJob).not.toHaveBeenCalled();
  });

  it('is a no-op for a caller that has not wired up the lookup', async () => {
    // getActiveJob is optional so the factory stays backward compatible.
    const thunks = createBlockJobThunks({
      prefix: 't2', deliverable: 'cdd',
      enqueue: async () => ({ job_id: 'x', status: 'queued' }),
      getJobStatus: async () => ({ status: 'running' }),
    });
    const slice = createSlice({
      name: 't2',
      initialState: { isGenerating: false, error: null, ...initialBlockJobState },
      reducers: {},
      extraReducers: (b) => attachBlockJobReducers(b, thunks),
    });
    const store = configureStore({ reducer: slice.reducer });
    await store.dispatch(thunks.resumeThunk({ courseId: 48 }));
    expect(store.getState().isGenerating).toBe(false);
  });

  it('ignores a response that carries no job handle', async () => {
    // Nothing to poll ⇒ adopting it would spin forever with no way to clear.
    const { thunks, store } = makeStore({ active: { status: 'running' } });
    await store.dispatch(thunks.resumeThunk({ courseId: 48 }));
    expect(store.getState().isGenerating).toBe(false);
    expect(store.getState().blockJob).toBeNull();
  });
});

describe('resumeThunk duplicate-chain guard', () => {
  it('does not start a second poll chain for a job already being polled', async () => {
    // Chains are setTimeout→dispatch loops on the store and are never cancelled, so
    // they outlive unmount. Navigating away and back would otherwise add one poller
    // per visit, and on completion each would fire its own toast and list refetch.
    const getActiveJob = vi.fn(async () => ({ job: RUNNING_JOB }));
    const thunks = createBlockJobThunks({
      prefix: 'g', deliverable: 'cdd',
      enqueue: async () => ({ job_id: 'j', status: 'queued' }),
      getJobStatus: async (jobId) => ({ status: 'running', job_id: jobId, progress: 20 }),
      getActiveJob,
      selectBlockJob: (state) => state.blockJob,
      completedMessage: 'done', failedMessage: 'failed',
    });
    const slice = createSlice({
      name: 'g',
      initialState: { isGenerating: false, error: null, ...initialBlockJobState },
      reducers: {},
      extraReducers: (b) => attachBlockJobReducers(b, thunks),
    });
    const store = configureStore({ reducer: slice.reducer });

    await store.dispatch(thunks.resumeThunk({ courseId: 48 }));
    expect(getActiveJob).toHaveBeenCalledTimes(1);

    // A remount while the first chain is live must be a no-op.
    await store.dispatch(thunks.resumeThunk({ courseId: 48 }));
    expect(getActiveJob).toHaveBeenCalledTimes(1);
  });

  it('still adopts after the banner is reset', async () => {
    // resetBlockJob nulls blockJob (it fires when projectId resolves), so the guard
    // must not permanently block a legitimate re-adoption.
    const getActiveJob = vi.fn(async () => ({ job: RUNNING_JOB }));
    const thunks = createBlockJobThunks({
      prefix: 'h', deliverable: 'cdd',
      enqueue: async () => ({ job_id: 'j', status: 'queued' }),
      getJobStatus: async (jobId) => ({ status: 'running', job_id: jobId, progress: 20 }),
      getActiveJob,
      selectBlockJob: (state) => state.blockJob,
      completedMessage: 'done', failedMessage: 'failed',
    });
    const slice = createSlice({
      name: 'h',
      initialState: { isGenerating: false, error: null, ...initialBlockJobState },
      reducers: { resetBlockJob(s) { s.blockJob = null; } },
      extraReducers: (b) => attachBlockJobReducers(b, thunks),
    });
    const store = configureStore({ reducer: slice.reducer });

    await store.dispatch(thunks.resumeThunk({ courseId: 48 }));
    store.dispatch(slice.actions.resetBlockJob());
    await store.dispatch(thunks.resumeThunk({ courseId: 48 }));
    expect(getActiveJob).toHaveBeenCalledTimes(2);
    expect(store.getState().blockJob.jobId).toBe('resumed-1');
  });

  it('reports which block the adopted build is for', async () => {
    const { thunks, store } = makeStore({ active: { ...RUNNING_JOB, block: 'Block 2' } });
    await store.dispatch(thunks.resumeThunk({ courseId: 48 }));
    expect(store.getState().blockJob.block).toBe('Block 2');
  });
});
