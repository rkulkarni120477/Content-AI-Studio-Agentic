import { createAsyncThunk } from '@reduxjs/toolkit';
import { generateService } from './services/generateService';
import { extractErrorMessage } from '@utils/helpers';
import { JOB_STATUSES } from '@utils/constants';
import toast from 'react-hot-toast';

const POLL_INTERVAL_MS = Number(import.meta.env.VITE_JOB_POLL_INTERVAL_MS) || 2000;

export const launchGenerationThunk = createAsyncThunk(
  'generate/launch',
  async (payload, { rejectWithValue }) => {
    try {
      if (!payload?.project_id) {
        return rejectWithValue('Select a project before launching generation.');
      }
      const accepted = await generateService.launch(payload);
      return accepted;
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const pollJobThunk = createAsyncThunk(
  'generate/pollJob',
  async (jobId, { dispatch, rejectWithValue }) => {
    try {
      const status = await generateService.getJobStatus(jobId);
      if (status.current_step) {
        dispatch({ type: 'generate/addJobStage', payload: status.current_step });
      }
      const active = ['pending', 'queued', 'running'].includes(status.status);
      if (active) {
        setTimeout(() => dispatch(pollJobThunk(jobId)), POLL_INTERVAL_MS);
      } else if (status.status === JOB_STATUSES.COMPLETED) {
        toast.success('Generation completed!');
        if (status.generation_id) {
          const gen = await generateService.getGeneration(status.generation_id);
          return { ...status, blocks: gen.blocks || [] };
        }
      } else if (status.status === JOB_STATUSES.FAILED) {
        toast.error(status.error_message || 'Generation failed.');
      }
      return status;
    } catch (e) {
      return rejectWithValue(extractErrorMessage(e));
    }
  },
);

export const cancelJobThunk = createAsyncThunk(
  'generate/cancelJob',
  async (jobId, { rejectWithValue }) => {
    try {
      const result = await generateService.cancelJob(jobId);
      toast.success('Generation job cancelled.');
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

/** @deprecated use launchGenerationThunk */
export const runGenerationThunk = launchGenerationThunk;
export const queueGenerationThunk = launchGenerationThunk;
