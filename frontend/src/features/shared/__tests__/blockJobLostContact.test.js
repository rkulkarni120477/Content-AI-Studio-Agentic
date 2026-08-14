/**
 * A build the browser stopped watching is not a build that failed.
 *
 * Measured incident, 2026-08-14: six block-wide builds completed server-side
 * (CDDs 162-167, taking 113-204s each) while the UI reported "❌ generation
 * failed — see the error above" and, above it, "Something went wrong / Network
 * error. Check your connection." The user retried four times, paying for a full
 * 20-day digest rebuild each time, for a document that had already generated.
 *
 * Two separate defects produced that:
 *
 *  1. Poll requests inherited the app-wide 120s axios timeout. A poll is one
 *     indexed DB read; when it stalled it occupied two minutes, so the
 *     four-consecutive-error allowance meant to ride out blips could span eight
 *     minutes of frozen UI. Axios aborts a timeout with NO response object, and
 *     the interceptor renders that as "Network error. Check your connection." —
 *     a message the codebase already documents elsewhere as misleading.
 *
 *  2. On exhausting retries the reducer marked the job FAILED and set a
 *     page-level error, which is a claim about the SERVER that the client is in
 *     no position to make. The job was running the whole time.
 *
 * These tests pin the honest behaviour: retry longer on short timeouts, and when
 * we do stop, say we stopped watching — not that the work failed.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createSlice } from '@reduxjs/toolkit';
import { configureStore } from '@reduxjs/toolkit';
import {
  createBlockJobThunks, attachBlockJobReducers, initialBlockJobState,
  MAX_POLL_ERRORS, POLL_REQUEST_TIMEOUT_MS, POLL_REQUEST_CONFIG,
} from '../blockJob';
import { JOB_STATUSES } from '@utils/constants';

vi.mock('react-hot-toast', () => ({
  default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}));

function makeStore({ getJobStatus }) {
  const thunks = createBlockJobThunks({
    prefix: 'test', deliverable: 'cdd',
    enqueue: async () => ({ job_id: 'j1', status: 'queued' }),
    getJobStatus,
    completedMessage: 'done', failedMessage: 'failed',
  });
  const slice = createSlice({
    name: 'test',
    initialState: { isGenerating: false, error: null, ...initialBlockJobState },
    reducers: {},
    extraReducers: (b) => attachBlockJobReducers(b, thunks),
  });
  const store = configureStore({ reducer: slice.reducer });
  return { thunks, store };
}

/** The exact shape axios produces on a timeout: an Error with no `response`. */
function timeoutError() {
  const e = new Error('timeout of 15000ms exceeded');
  e.code = 'ECONNABORTED';
  return e;                       // note: no `.response` — this is the whole trap
}

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); });

describe('poll request timeout', () => {
  it('is far shorter than the app-wide API timeout', () => {
    // The bug was inheriting 120_000. A poll that has not answered in 15s is not
    // going to; retrying on the next tick beats occupying the chain for 2 minutes.
    expect(POLL_REQUEST_TIMEOUT_MS).toBeLessThanOrEqual(30_000);
    expect(POLL_REQUEST_CONFIG.timeout).toBe(POLL_REQUEST_TIMEOUT_MS);
  });

  it('tolerates enough consecutive failures to outlast a slow build', () => {
    // 20 x 2s ~= 2 minutes of grace. The old value of 4 gave up while the server
    // was still working — on builds measured at 113-204s.
    expect(MAX_POLL_ERRORS).toBeGreaterThanOrEqual(10);
  });
});

describe('losing contact with a running build', () => {
  it('does not report the job as failed', async () => {
    const { thunks, store } = makeStore({
      getJobStatus: vi.fn().mockRejectedValue(timeoutError()),
    });
    store.dispatch({ type: thunks.generateThunk.pending.type });

    // Drain the whole retry chain.
    for (let i = 0; i < MAX_POLL_ERRORS + 2; i += 1) {
      await store.dispatch(thunks.pollThunk({ jobId: 'j1', courseId: 1, errorCount: MAX_POLL_ERRORS - 1 }));
      await vi.advanceTimersByTimeAsync(0);
    }

    const s = store.getState();
    expect(s.blockJob.status).not.toBe(JOB_STATUSES.FAILED);
    expect(s.blockJob.lostContact).toBe(true);
    expect(s.isGenerating).toBe(false);
  });

  it('does not raise a page-level error, which would hide the list behind ErrorState', async () => {
    // The user saw "Something went wrong" covering the whole page. That is the
    // ErrorState fed by slice.error — and only the poll had stopped.
    const { thunks, store } = makeStore({
      getJobStatus: vi.fn().mockRejectedValue(timeoutError()),
    });
    store.dispatch({ type: thunks.generateThunk.pending.type });
    await store.dispatch(thunks.pollThunk({ jobId: 'j1', courseId: 1, errorCount: MAX_POLL_ERRORS - 1 }));
    await vi.advanceTimersByTimeAsync(0);

    expect(store.getState().error).toBeNull();
  });

  it('still reports a genuine server-side failure as a failure', async () => {
    // The fix must not swallow real failures — that would be the same dishonesty
    // pointing the other way.
    const { thunks, store } = makeStore({
      getJobStatus: vi.fn().mockResolvedValue({
        status: JOB_STATUSES.FAILED, error_message: 'DIS unavailable',
      }),
    });
    store.dispatch({ type: thunks.generateThunk.pending.type });
    await store.dispatch(thunks.pollThunk({ jobId: 'j1', courseId: 1 }));

    const s = store.getState();
    expect(s.blockJob.status).toBe(JOB_STATUSES.FAILED);
    expect(s.blockJob.lostContact).toBeFalsy();
    expect(s.isGenerating).toBe(false);
  });

  it('keeps polling through a blip rather than giving up on the first one', async () => {
    const getJobStatus = vi.fn()
      .mockRejectedValueOnce(timeoutError())
      .mockResolvedValue({ status: JOB_STATUSES.RUNNING });
    const { thunks, store } = makeStore({ getJobStatus });
    store.dispatch({ type: thunks.generateThunk.pending.type });

    await store.dispatch(thunks.pollThunk({ jobId: 'j1', courseId: 1 }));
    const s = store.getState();
    expect(s.blockJob.status).not.toBe(JOB_STATUSES.FAILED);
    expect(s.blockJob.lostContact).toBeFalsy();
  });

  it('clears the disconnect once a poll succeeds again', async () => {
    // Reattaching (resumeThunk on mount, or a recovered poll) must not leave a
    // stale "lost contact" banner over a build that is reporting again.
    const { thunks, store } = makeStore({
      getJobStatus: vi.fn().mockResolvedValue({ status: JOB_STATUSES.RUNNING }),
    });
    store.dispatch({ type: thunks.generateThunk.pending.type });
    store.dispatch({
      type: thunks.pollThunk.rejected.type,
      payload: { lostContact: true, message: 'x' },
    });
    expect(store.getState().blockJob.lostContact).toBe(true);

    await store.dispatch(thunks.pollThunk({ jobId: 'j1', courseId: 1 }));
    expect(store.getState().blockJob.lostContact).toBeFalsy();
  });
});
