import { describe, it, expect } from 'vitest';
import { createSlice } from '@reduxjs/toolkit';
import { createBlockJobThunks, attachBlockJobReducers, initialBlockJobState } from '../blockJob';
import { isTerminalJobStatus, JOB_STATUSES } from '@utils/constants';

// A tiny slice wired exactly like the real CDD/Blueprint slices, so we exercise
// attachBlockJobReducers' real transitions (the layer where the polling-lifecycle
// bugs previously lived).
function makeSlice() {
  const thunks = createBlockJobThunks({
    prefix: 'test', deliverable: 'cdd',
    enqueue: async () => ({ job_id: 'j1', status: 'queued' }),
    getJobStatus: async () => ({ status: 'running' }),
    completedMessage: 'done', failedMessage: 'failed',
  });
  const slice = createSlice({
    name: 'test',
    initialState: { isGenerating: false, error: null, ...initialBlockJobState },
    reducers: { resetBlockJob(s) { s.blockJob = null; } },
    extraReducers: (b) => attachBlockJobReducers(b, thunks),
  });
  return { thunks, reducer: slice.reducer, reset: slice.actions.resetBlockJob };
}

describe('isTerminalJobStatus', () => {
  it('treats completed/failed/cancelled as terminal and active states as not', () => {
    expect(isTerminalJobStatus(JOB_STATUSES.COMPLETED)).toBe(true);
    expect(isTerminalJobStatus(JOB_STATUSES.FAILED)).toBe(true);
    expect(isTerminalJobStatus(JOB_STATUSES.CANCELLED)).toBe(true);   // the wedge bug
    expect(isTerminalJobStatus(JOB_STATUSES.RUNNING)).toBe(false);
    expect(isTerminalJobStatus(JOB_STATUSES.QUEUED)).toBe(false);
  });
});

describe('attachBlockJobReducers', () => {
  it('enqueue pending sets busy + queued job', () => {
    const { thunks, reducer } = makeSlice();
    const s = reducer(undefined, { type: thunks.generateThunk.pending.type });
    expect(s.isGenerating).toBe(true);
    expect(s.blockJob.status).toBe(JOB_STATUSES.QUEUED);
  });

  it('CANCELLED terminal clears isGenerating (no infinite wedge)', () => {
    const { thunks, reducer } = makeSlice();
    let s = reducer(undefined, { type: thunks.generateThunk.pending.type });
    s = reducer(s, { type: thunks.pollThunk.fulfilled.type, payload: { status: 'cancelled' } });
    expect(s.isGenerating).toBe(false);
    expect(s.blockJob.status).toBe('cancelled');
  });

  it('transient poll error preserves state and keeps the loop alive', () => {
    const { thunks, reducer } = makeSlice();
    let s = reducer(undefined, { type: thunks.generateThunk.pending.type });
    const before = s.blockJob;
    s = reducer(s, { type: thunks.pollThunk.fulfilled.type, payload: { status: 'running', transientError: true } });
    expect(s.isGenerating).toBe(true);          // still working
    expect(s.blockJob).toEqual(before);          // unchanged
  });

  it('running poll updates progress but stays busy', () => {
    const { thunks, reducer } = makeSlice();
    let s = reducer(undefined, { type: thunks.generateThunk.pending.type });
    s = reducer(s, { type: thunks.pollThunk.fulfilled.type, payload: { status: 'running', progress: 40, current_step: 'MAP' } });
    expect(s.isGenerating).toBe(true);
    expect(s.blockJob).toMatchObject({ status: 'running', progress: 40, currentStep: 'MAP' });
  });

  it('enqueue rejected (e.g. missing job_id) clears busy + job', () => {
    const { thunks, reducer } = makeSlice();
    let s = reducer(undefined, { type: thunks.generateThunk.pending.type });
    s = reducer(s, { type: thunks.generateThunk.rejected.type, payload: 'no job handle' });
    expect(s.isGenerating).toBe(false);
    expect(s.blockJob).toBe(null);
    expect(s.error).toBe('no job handle');
  });

  it('resetBlockJob clears stale cross-course status', () => {
    const { thunks, reducer, reset } = makeSlice();
    let s = reducer(undefined, { type: thunks.pollThunk.fulfilled.type, payload: { status: 'completed' } });
    s = reducer(s, reset());
    expect(s.blockJob).toBe(null);
  });
});
