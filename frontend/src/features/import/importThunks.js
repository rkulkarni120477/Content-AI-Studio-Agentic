import { createAsyncThunk } from '@reduxjs/toolkit';
import toast from 'react-hot-toast';
import { importService } from './services/importService';
import { extractErrorMessage } from '@utils/helpers';
import { JOB_STATUSES } from '@utils/constants';
import { labelsFromState } from '@config/tenantLabels';

// Reuse the same poll cadence the generate flow uses.
const POLL_INTERVAL_MS = Number(import.meta.env.VITE_JOB_POLL_INTERVAL_MS) || 2000;

const ACTIVE_STATUSES = ['pending', 'queued', 'running'];

// The import runs server-side; a poll that can't reach the API says nothing
// about the job. Keep retrying so a blip (or a blocked request) doesn't render
// a "failed" screen for an import that is actually completing.
const MAX_POLL_FAILURES = 5;

export const validatePackageThunk = createAsyncThunk(
  'import/validatePackage',
  async ({ file }, { rejectWithValue }) => {
    try {
      return await importService.validatePackage(file);
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const startImportThunk = createAsyncThunk(
  'import/startImport',
  async ({ projectId, clusterId, name, file }, { dispatch, rejectWithValue }) => {
    try {
      const res = await importService.startImport(projectId, { name, clusterId, file });
      // Kick off polling immediately with the returned job id.
      if (res?.job_id) dispatch(pollImportJobThunk(res.job_id));
      return res;
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const fetchImportRecordThunk = createAsyncThunk(
  'import/fetchRecord',
  async (importId, { rejectWithValue }) => {
    try {
      return await importService.getImport(importId);
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const pollImportJobThunk = createAsyncThunk(
  'import/pollJob',
  async (arg, { dispatch, getState, rejectWithValue }) => {
    const { jobId, failures = 0 } = typeof arg === 'object' && arg !== null ? arg : { jobId: arg };
    try {
      const status = await importService.getJobStatus(jobId);
      if (ACTIVE_STATUSES.includes(status.status)) {
        setTimeout(() => dispatch(pollImportJobThunk({ jobId })), POLL_INTERVAL_MS);
        return status;
      }
      if (status.status === JOB_STATUSES.COMPLETED) {
        toast.success(`${labelsFromState(getState).title} imported!`);
      } else if (status.status === JOB_STATUSES.FAILED) {
        toast.error(status.error_message || 'Import failed.');
      }
      return status;
    } catch (e) {
      // Unreachable API != failed import. Retry, and if we never get through,
      // say what's actually true: the job is still running on the server.
      const next = failures + 1;
      if (next < MAX_POLL_FAILURES) {
        setTimeout(() => dispatch(pollImportJobThunk({ jobId, failures: next })), POLL_INTERVAL_MS);
        return null;   // reducer ignores a null payload — keep last known progress
      }
      const L = labelsFromState(getState);
      return rejectWithValue(
        `Lost contact with the import (${extractErrorMessage(e)}). It is still running on the `
        + `server — check your ${L.titles} list in a moment before starting it again.`,
      );
    }
  },
);
