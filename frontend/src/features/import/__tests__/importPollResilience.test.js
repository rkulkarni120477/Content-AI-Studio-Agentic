import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { configureStore } from '@reduxjs/toolkit';

vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }));
vi.mock('../services/importService', () => ({
  importService: { getJobStatus: vi.fn() },
}));

import { importService } from '../services/importService';
import importReducer from '../importSlice';
import { pollImportJobThunk } from '../importThunks';

const makeStore = () => configureStore({ reducer: { import: importReducer } });

describe('import job polling resilience', () => {
  beforeEach(() => { vi.useFakeTimers(); importService.getJobStatus.mockReset(); });
  afterEach(() => { vi.useRealTimers(); });

  it('does not fail the wizard when a single poll is blocked', async () => {
    const store = makeStore();
    // Seed real progress, then block the next poll (what DevTools "Block request URL" does).
    importService.getJobStatus.mockResolvedValueOnce({ status: 'running', progress: 50 });
    await store.dispatch(pollImportJobThunk('job-1'));
    expect(store.getState().import.job.progress).toBe(50);

    importService.getJobStatus.mockRejectedValueOnce(new Error('Network Error'));
    await store.dispatch(pollImportJobThunk({ jobId: 'job-1' }));

    const s = store.getState().import;
    expect(s.step).not.toBe('error');   // the import is still running server-side
    expect(s.job.progress).toBe(50);    // last known progress preserved
  });

  it('reports a still-running import once retries are exhausted', async () => {
    const store = makeStore();
    importService.getJobStatus.mockRejectedValue(new Error('Network Error'));
    await store.dispatch(pollImportJobThunk({ jobId: 'job-1', failures: 4 }));

    const s = store.getState().import;
    expect(s.step).toBe('error');
    expect(s.error).toMatch(/still running on the server/i);
  });

  it('still fails the wizard when the server says the job failed', async () => {
    const store = makeStore();
    importService.getJobStatus.mockResolvedValueOnce({
      status: 'failed', error_message: 'Invalid IMSCC package',
    });
    await store.dispatch(pollImportJobThunk('job-1'));

    const s = store.getState().import;
    expect(s.step).toBe('error');
    expect(s.error).toBe('Invalid IMSCC package');
  });
});
