import { createAsyncThunk } from '@reduxjs/toolkit';
import { generateService } from './services/generateService';
import { extractErrorMessage } from '@utils/helpers';
import { JOB_STATUSES } from '@utils/constants';
import toast from 'react-hot-toast';

const POLL_INTERVAL_MS = Number(import.meta.env.VITE_JOB_POLL_INTERVAL_MS) || 3000;

export const runGenerationThunk = createAsyncThunk(
  'generate/run',
  async (payload, { rejectWithValue }) => {
    try {
      const result = await generateService.run(payload);
      toast.success(`${result.blocks?.length || 0} block(s) generated.`);
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const queueGenerationThunk = createAsyncThunk(
  'generate/queue',
  async (payload, { rejectWithValue }) => {
    try { return await generateService.queue(payload); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const pollJobThunk = createAsyncThunk(
  'generate/pollJob',
  async (jobId, { dispatch, rejectWithValue }) => {
    try {
      const status = await generateService.getJobStatus(jobId);
      // If still running, schedule another poll
      if (status.status === JOB_STATUSES.PENDING || status.status === JOB_STATUSES.RUNNING) {
        setTimeout(() => dispatch(pollJobThunk(jobId)), POLL_INTERVAL_MS);
      } else if (status.status === JOB_STATUSES.COMPLETED) {
        toast.success('Generation completed!');
      } else if (status.status === JOB_STATUSES.FAILED) {
        toast.error('Generation failed. Check the job log.');
      }
      return status;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);
