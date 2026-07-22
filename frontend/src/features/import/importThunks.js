import { createAsyncThunk } from '@reduxjs/toolkit';
import toast from 'react-hot-toast';
import { importService } from './services/importService';
import { extractErrorMessage } from '@utils/helpers';
import { JOB_STATUSES } from '@utils/constants';

// Reuse the same poll cadence the generate flow uses.
const POLL_INTERVAL_MS = Number(import.meta.env.VITE_JOB_POLL_INTERVAL_MS) || 2000;

const ACTIVE_STATUSES = ['pending', 'queued', 'running'];

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
  async (jobId, { dispatch, rejectWithValue }) => {
    try {
      const status = await importService.getJobStatus(jobId);
      if (ACTIVE_STATUSES.includes(status.status)) {
        setTimeout(() => dispatch(pollImportJobThunk(jobId)), POLL_INTERVAL_MS);
        return status;
      }
      if (status.status === JOB_STATUSES.COMPLETED) {
        toast.success('Course imported!');
      } else if (status.status === JOB_STATUSES.FAILED) {
        toast.error(status.error_message || 'Import failed.');
      }
      return status;
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);
